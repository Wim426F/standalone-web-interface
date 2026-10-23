"""The HTTP server: reproduces the ESP32 firmware's web contract.

Endpoint spec: esp32-web-interface.ino route registrations and
tests/mock-esp/server.mjs. Differences from the ESP32:
  - /stream (SSE) is served on the SAME port; /settings advertises it via
    sse_port (the frontend builds its EventSource URL from that value).
  - ESP-only endpoints (/wifi*, /espupdate*, /reboot) are stubs; the
    frontend hides those cards when /settings carries backend:"pc".
  - SPIFFS becomes the storage dir: uploads via /edit land in files/ and
    shadow packaged frontend files, exactly like SPIFFS uploads did.
"""

import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import __version__
from . import sdo as S
from .commands import execute
from .http_util import guess_type, parse_multipart
from .settings import SPEED_TO_BITRATE, Settings
from .storage import frontend_dir

LAYOUT_DEFAULTS = {
    "favorites.json": b'{"p":[],"s":[]}',
    "gauges.json": b'{"items":[]}',
    "plots.json": b'{"plots":[]}',
    "virtualvals.json": b'{"items":[]}',
}

SSE_NAMES_RE = re.compile(r"^[A-Za-z0-9_.,\-]+$")

WIFI_STATUS_STUB = ('{"sta_connected":false,"rssi":0,"ssid":"","ip":"",'
                    '"ap_active":false,"ap_clients":0,"ap_fallback":false}')


def detect_channels(iface):
    """Channels available for one adapter type — the 'select an adapter,
    then pick a found channel' flow. slcan and socketcan have no vendor SDK
    to query, so they get their own listing; everything else (pcan, kvaser,
    vector, gs_usb, ixxat, ...) goes through python-can's own detection."""
    if iface == "slcan":
        try:
            from serial.tools import list_ports
            return [{"channel": p.device, "description": p.description or ""}
                    for p in list_ports.comports()]
        except Exception:
            return []
    if iface == "socketcan":
        return _detect_socketcan_channels()
    try:
        import can
        out = []
        for c in can.detect_available_configs(interfaces=[iface]):
            if str(c.get("interface", "")) != iface:
                continue
            out.append({"channel": str(c.get("channel", "")),
                        "description": str(c.get("device_name", "") or "")})
        return out
    except Exception:
        return []


def _detect_socketcan_channels():
    # python-can has no socketcan autodetection; `ip link` is present on
    # every Linux box that could plausibly have a can interface
    try:
        r = subprocess.run(["ip", "-o", "link", "show", "type", "can"],
                          capture_output=True, text=True, timeout=2)
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"\d+:\s+([^:@]+)", line)
        if m:
            out.append({"channel": m.group(1), "description": ""})
    return out


class App:
    """Shared state wired together by the CLI."""

    def __init__(self, storage, settings, bus, device, virtual, flasher):
        self.storage = storage
        self.settings = settings
        self.bus = bus
        self.device = device
        self.virtual = virtual
        self.flasher = flasher
        self.webroot = frontend_dir()
        self.http_port = 0
        self.running = True
        self.sse_gen = 0
        self._sse_lock = threading.Lock()

    def sse_next(self):
        """New stream generation — the newest /stream client wins, older
        handler loops observe the bump and exit (firmware: single client)."""
        with self._sse_lock:
            self.sse_gen += 1
            return self.sse_gen


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "openinverter-web/" + __version__
    timeout = 120

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass

    # ---- response helpers ----

    def _send(self, code, body, ctype="text/plain", encoding=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            # Revalidate on every load — a cached page pointing at a stopped
            # server silently renders the ESP32 fallback UI
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.close_connection = True

    def _text(self, body, code=200):
        self._send(code, body, "text/plain")

    def _tjson(self, body, code=200):
        # the firmware answers most JSON as text/json; browsers don't care
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body)
        self._send(code, body, "text/json")

    def _ajson(self, body, code=200):
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body)
        self._send(code, body, "application/json")

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    # ---- method entry points ----

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PUT(self):
        self._route("PUT")

    # ---- routing ----

    def _route(self, method):
        url = urlparse(self.path)
        path = url.path
        q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        app = self.app

        if path == "/cmd":
            if "cmd" not in q:
                return self._text("BAD ARGS", 500)
            return self._tjson(execute(app.device, q["cmd"][:1024]))

        if path == "/stream":
            return self._handle_stream(q)

        if path == "/list":
            return self._ajson(app.storage.list_files())

        if path == "/edit":
            return self._handle_edit(method, q)

        name = path.lstrip("/")
        if name in LAYOUT_DEFAULTS and method == "GET":
            stored = app.storage.read_file(name)
            return self._ajson(stored if stored is not None else LAYOUT_DEFAULTS[name])

        if path == "/settings":
            q = self._merged_form(q)
            if q:
                return self._apply_settings(q)
            return self._tjson(app.settings.public(
                app.http_port, app.bus.connected(), app.bus.status_msg))

        if path == "/wifi":
            return self._send(200, "<h2>WiFi</h2><p>Not applicable "
                              "&mdash; this interface runs on your PC.</p>", "text/html")
        if path == "/wifi-status":
            return self._tjson(WIFI_STATUS_STUB)

        if path == "/version":
            return self._text("v%s-pc" % __version__)
        if path == "/otainfo":
            # empty repo keeps the frontend's web-UI update check dormant
            return self._ajson({"version": __version__, "repo": "", "target": "pc"})

        if path in ("/fwupdate", "/can-fwupdate"):
            return self._handle_fwupdate(q)
        if path == "/fwupdate-status":
            return self._tjson(app.flasher.status())

        if path == "/reboot":
            return self._text("Not applicable on PC")

        if path == "/reset-inverter":
            if app.flasher.busy():
                return self._text("CAN firmware update in progress", 409)
            app.device.sdo.reset_device()
            return self._text("Inverter reset sent")

        if path == "/baud":
            return self._text("off")

        if path == "/can-send":
            return self._handle_can_send(q)

        if path == "/can-scan":
            if app.flasher.busy():
                return self._tjson('{"error":"CAN firmware update in progress"}', 409)
            return self._tjson(app.device.scan())

        if path == "/set-can-node":
            if app.flasher.busy():
                return self._text("CAN firmware update in progress", 409)
            if "id" in q:
                app.settings.can_node_id = Settings._clamp_node(q["id"])
                app.device.invalidate()  # reload the DB for the new device
                app.settings.save()
            return self._tjson({"nodeId": app.settings.can_node_id})

        if path == "/virtual-reload":
            return self._ajson({"count": app.virtual.load()})

        if path == "/can-debug":
            return self._handle_can_debug(q)

        if path == "/can-detect":
            return self._handle_can_detect(q)

        if method == "GET":
            return self._serve_static(path)
        self._text("FileNotFound", 404)

    # ---- endpoint bodies ----

    def _handle_edit(self, method, q):
        app = self.app
        if method == "POST":
            parts = parse_multipart(self._body(), self.headers.get("Content-Type"))
            for part in parts:
                if part["filename"]:
                    app.storage.write_file(part["filename"], part["data"])
            return self._text("")
        if method == "DELETE":
            app.storage.delete_file(q.get("f", ""))
            return self._text("")
        if method == "PUT":
            name = q.get("f") or q.get("path") or ""
            if app.storage.write_file(name, b""):
                return self._text("")
            return self._text("BAD ARGS", 500)
        # GET: the legacy SPIFFS editor page, when the frontend ships one
        return self._serve_static("/edit.htm")

    def _merged_form(self, q):
        """Query string plus an application/x-www-form-urlencoded body.

        The firmware's hasArg() sees both. The reset control posts its flag
        in the body; the interface Save still uses the query string.
        """
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/x-www-form-urlencoded":
            return q
        raw = self._body()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        for key, vals in parse_qs(text, keep_blank_values=True).items():
            q.setdefault(key, vals[0] if vals else "")
        return q

    def _apply_settings(self, q):
        app = self.app
        s = app.settings
        # Keyed by which args are present, like the firmware's handleSettings
        if "txrx_swap" in q or "ap_fallback" in q or "sd_enabled" in q:
            return self._tjson('{"result":"ok"}')  # ESP-only toggles: accepted, ignored
        if "reset_settings" in q:
            s.reset_device()
            if not app.flasher.busy():
                app.bus.reconfigure()
                app.device.invalidate()
            return self._tjson('{"result":"ok","rebooting":false}')
        if "dev_name" in q:
            s.dev_name = q["dev_name"][:32]
            s.save()
            return self._tjson('{"result":"ok"}')

        if app.flasher.busy():
            return self._tjson('{"error":"CAN firmware update in progress"}', 409)
        if "can_node_id" in q:
            s.can_node_id = Settings._clamp_node(q["can_node_id"])
        if "can_interface" in q:
            s.can_interface = q["can_interface"].strip()
        if "can_channel" in q:
            s.can_channel = q["can_channel"].strip()
        if "can_bitrate" in q:
            try:
                s.can_bitrate = int(q["can_bitrate"])
            except ValueError:
                pass
        elif "can_speed" in q:
            try:
                s.can_bitrate = SPEED_TO_BITRATE.get(int(q["can_speed"]), 500000)
            except ValueError:
                pass
        if "can_nodes" in q:
            try:
                nodes = json.loads(q["can_nodes"])
                if isinstance(nodes, list):
                    s.can_nodes = nodes
            except ValueError:
                pass
        s.save()
        app.bus.reconfigure()
        app.device.invalidate()  # node/adapter may have changed
        return self._tjson('{"result":"ok"}')

    def _handle_fwupdate(self, q):
        app = self.app
        if "file" not in q:
            return self._text("BAD ARGS", 500)
        path = app.storage.file_path(q["file"])
        if path is None or not path.is_file():
            return self._tjson({"message": "Failed to open file"}, 500)
        code, resp = app.flasher.start(path)
        return self._tjson(resp, code)

    def _handle_can_send(self, q):
        app = self.app
        if app.flasher.busy():
            return self._tjson('{"error":"CAN firmware update in progress"}', 409)
        if "canId" not in q:
            return self._tjson('{"error":"Missing canId"}', 400)
        try:
            can_id = int(q["canId"], 16)
        except ValueError:
            can_id = 0
        data = bytearray()
        for tok in re.split(r"[,\s]+", q.get("data", "")):
            if not tok or len(data) >= 8:
                continue
            try:
                data.append(int(tok, 16) & 0xFF)
            except ValueError:
                data.append(0)
        ok = app.bus.send(can_id, bytes(data))
        return self._tjson({"status": "sent" if ok else "failed",
                            "canId": can_id, "len": len(data)})

    def _handle_can_debug(self, q):
        app = self.app
        if app.flasher.busy():
            return self._tjson('{"error":"CAN firmware update in progress"}', 409)
        if "reset" in q:
            # Reset the device and capture bus traffic for 8s (diagnoses
            # whether a CAN bootloader announces itself after reset)
            app.bus.capture_start()
            app.device.sdo.reset_device()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 8.0:
                cap = app.bus._capture
                if cap is not None and len(cap) >= 40:
                    break
                time.sleep(0.1)
            frames = app.bus.capture_stop()
            items = [{"t": t, "id": "0x%x" % i, "data": "".join("%02X " % b for b in d)}
                     for t, i, d in frames]
            return self._tjson({"frames": items, "count": len(items)})
        # One segmented download attempt with diagnostics
        t0 = time.monotonic()
        data, complete = app.device.sdo.read_segmented(S.INDEX_JSON, 0, seg_timeout=0.1)
        elapsed = int((time.monotonic() - t0) * 1000)
        head = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:24])
        return self._tjson({
            "nodeId": app.settings.can_node_id,
            "bytes": len(data),
            "complete": complete,
            "ms": elapsed,
            "head": head,
        })

    def _handle_can_detect(self, q):
        """?interface=X: channels available for that one interface (the
        normal flow — pick an adapter type, then pick a found channel).
        No interface: broad scan across everything, kept for diagnostics."""
        iface = (q.get("interface") or "").strip()
        if iface:
            return self._ajson({"channels": detect_channels(iface)})

        configs = []
        try:
            import can
            for c in can.detect_available_configs():
                found_iface = str(c.get("interface", ""))
                # 'serial' duplicates the COM-port list below (offered as
                # slcan there); virtual/udp are not real adapters
                if found_iface in ("virtual", "udp_multicast", "serial"):
                    continue
                configs.append({"interface": found_iface,
                                "channel": str(c.get("channel", ""))})
        except Exception:
            pass
        ports = []
        try:
            from serial.tools import list_ports
            for p in list_ports.comports():
                ports.append({"device": p.device, "description": p.description or ""})
        except Exception:
            pass
        return self._ajson({"configs": configs, "serial_ports": ports})

    def _handle_stream(self, q):
        app = self.app
        names = q.get("names", "")
        if not names or len(names) > 120 or not SSE_NAMES_RE.match(names):
            return self._text("FileNotFound", 404)
        try:
            ms = int(q.get("ms") or 100)
        except ValueError:
            ms = 100
        if ms < 50:
            ms = 100
        if ms > 5000:
            ms = 5000
        gen = app.sse_next()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            interval = ms / 1000.0
            while app.running and app.sse_gen == gen:
                t0 = time.monotonic()
                if not app.flasher.busy():  # never touch the bus mid-flash
                    out = execute(app.device, "get " + names)
                    if out and out != "update in progress":
                        line = out.replace("\r", " ").replace("\n", " ")
                        self.wfile.write(b"data: " + line.encode("utf-8") + b"\n\n")
                        self.wfile.flush()
                dt = interval - (time.monotonic() - t0)
                if dt > 0:
                    time.sleep(dt)
        except OSError:
            pass  # client gone
        finally:
            self.close_connection = True

    def _serve_static(self, path):
        app = self.app
        name = path.lstrip("/") or "index.html"
        # Uploaded blobs (uiprefs.json, presets.json, stm32.bin, ...) shadow
        # packaged files, exactly like SPIFFS uploads did on the ESP
        if "/" not in name:
            stored = app.storage.read_file(name)
            if stored is not None:
                return self._send(200, stored, guess_type(name))
        parts = [p for p in name.split("/") if p]
        if any(p == ".." for p in parts):
            return self._text("Bad path", 400)
        p = app.webroot.joinpath(*parts)
        if p.is_file():
            return self._send(200, p.read_bytes(), guess_type(p.name))
        gz = p.with_name(p.name + ".gz")
        if gz.is_file():  # gz-only assets, served like the ESP does
            return self._send(200, gz.read_bytes(), guess_type(p.name), encoding="gzip")
        self._text("FileNotFound", 404)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(app, host, port):
    httpd = WebServer((host, port), Handler)
    httpd.app = app
    app.http_port = httpd.server_address[1]
    return httpd
