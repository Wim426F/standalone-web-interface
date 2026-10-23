"""Command-line entry point: wire everything up and serve."""

import argparse
import json
import os
import threading
import time
import urllib.request
import webbrowser

from . import __version__
from .canbus import CanBus
from .device import Device
from .flasher import Flasher
from .server import App, make_server
from .settings import Settings
from .storage import Storage, frontend_dir
from .virtualvals import VirtualValues


def _warn_if_already_running(storage):
    """A second instance racing the first for the same CAN adapter is the
    single most confusing failure mode here (looks like a broken adapter,
    is actually just 'Access is denied' on the port) — so a lightweight,
    non-blocking heads-up beats a cryptic connection error."""
    try:
        data = json.loads(storage.instance_marker_path.read_text(encoding="utf-8"))
        host, port = data.get("host"), data.get("port")
        probe_host = "127.0.0.1" if not host or host == "0.0.0.0" else host
        with urllib.request.urlopen("http://%s:%d/version" % (probe_host, port), timeout=0.5):
            pass
    except Exception:
        return  # no marker, stale marker, or nothing answering there
    print("NOTE: another openinverter-web instance looks like it's already running at "
          "http://%s:%d/ (started %s)." % (probe_host, port, data.get("started", "?")))
    print("      If that's not intentional, use that one instead of starting a second -")
    print("      two instances fighting over the same CAN adapter causes 'Access is denied'.")


def _write_instance_marker(storage, host, port):
    try:
        storage.instance_marker_path.write_text(json.dumps({
            "pid": os.getpid(), "host": host, "port": port,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }), encoding="utf-8")
    except OSError:
        pass


def _remove_instance_marker(storage):
    try:
        storage.instance_marker_path.unlink()
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="openinverter-web",
        description="OpenInverter web interface served from your PC over CAN "
                    "(python-can: socketcan, slcan, gs_usb, pcan, kvaser, vector, ...)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; use 0.0.0.0 to expose on the LAN "
                         "- there is no sign-in, so anyone on that network can control the inverter)")
    ap.add_argument("--port", type=int, default=None,
                    help="HTTP port (default 8998, falling back to a free port "
                         "if Windows reserves it; 0 picks a free port)")
    ap.add_argument("--interface", help="python-can interface, e.g. socketcan / slcan / gs_usb / pcan")
    ap.add_argument("--channel", help="adapter channel, e.g. can0 / COM5 / PCAN_USBBUS1")
    ap.add_argument("--bitrate", type=int, help="CAN bitrate in bit/s (e.g. 500000)")
    ap.add_argument("--node", type=int, help="CAN node id of the device (1-127)")
    ap.add_argument("--data-dir", help="settings/uploads directory (default: per-user app data)")
    ap.add_argument("--simulate", action="store_true",
                    help="no hardware: virtual bus with a simulated inverter")
    ap.add_argument("--no-browser", action="store_true", help="don't open the web browser")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = ap.parse_args(argv)

    storage = Storage(args.data_dir)
    _warn_if_already_running(storage)
    settings = Settings(storage)
    changed = False
    if args.interface is not None:
        settings.can_interface = args.interface
        changed = True
    if args.channel is not None:
        settings.can_channel = args.channel
        changed = True
    if args.bitrate is not None:
        settings.can_bitrate = args.bitrate
        changed = True
    if args.node is not None:
        settings.can_node_id = Settings._clamp_node(args.node)
        changed = True
    if changed and not args.simulate:
        settings.save()

    sim = None
    if args.simulate:
        from .sim import SimInverter
        sim = SimInverter(channel="oi-sim")
        settings.can_node_id = sim.node_id  # in-memory only

        def get_cfg():
            return {"interface": "virtual", "channel": "oi-sim", "bitrate": 500000}
    else:
        def get_cfg():
            return {"interface": settings.can_interface,
                    "channel": settings.can_channel,
                    "bitrate": settings.can_bitrate}

    bus = CanBus(get_cfg)
    virtual = VirtualValues(storage)
    virtual.load()
    device = Device(bus, settings, storage, virtual)
    flasher = Flasher(bus, device.sdo)
    device.flasher = flasher
    flasher.on_done = lambda: device.invalidate(drop_disk=True)  # new firmware, new DB
    bus.set_rx_hook(virtual.feed)
    bus.start()

    app = App(storage, settings, bus, device, virtual, flasher)
    try:
        httpd = make_server(app, args.host, 8998 if args.port is None else args.port)
    except OSError as e:
        if args.port is not None:
            raise SystemExit("Could not bind port %d: %s" % (args.port, e))
        # Windows reserves seemingly-free ports (WinError 10013) — fall back
        # to an OS-picked one; the frontend derives the SSE URL from
        # /settings so any port works
        print("Port 8998 unavailable (%s); picking a free port instead." % e)
        httpd = make_server(app, args.host, 0)
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = "http://%s:%d/" % (display_host, app.http_port)
    _write_instance_marker(storage, display_host, app.http_port)

    print("openinverter-web %s%s" % (__version__, " (simulator)" if args.simulate else ""))
    print("Serving %s" % url)
    print("Data dir: %s" % storage.data_dir)
    print("Frontend: %s" % frontend_dir())
    if not args.simulate and not settings.can_interface:
        print("No CAN adapter configured yet - pick one in Settings, or pass "
              "--interface/--channel/--bitrate.")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        app.running = False
        httpd.server_close()
        bus.stop()
        if sim is not None:
            sim.stop()
        _remove_instance_marker(storage)


if __name__ == "__main__":
    main()
