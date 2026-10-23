"""HTTP contract tests: the frontend's expectations against the live server."""

import http.client
import json
import socket
import threading

import pytest

from openinverter_web.server import App, make_server


@pytest.fixture
def web(rig):
    app = App(rig.storage, rig.settings, rig.bus, rig.device, rig.virtual, rig.flasher)
    httpd = make_server(app, "127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield app.http_port
    app.running = False
    httpd.shutdown()
    httpd.server_close()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def test_settings_shape(web):
    _, body = get(web, "/settings")
    data = json.loads(body)
    assert data["backend"] == "pc"
    assert data["can_mode"] is True          # routes the UI to the CAN paths
    assert data["sse_port"] == web           # SSE on our own port
    assert data["arch"] == "pc"
    assert data["can_connected"] is True
    assert data["uart_rx_pin"] == -1         # ESP-only keys present as stubs


def test_static_frontend_serves(web):
    status, body = get(web, "/")
    assert status == 200 and b"app.js" in body
    status, _ = get(web, "/app.js")
    assert status == 200
    status, _ = get(web, "/definitely-missing.js")
    assert status == 404


def test_cmd_json_and_bad_args(web):
    status, body = get(web, "/cmd?cmd=json")
    data = json.loads(body)
    assert status == 200 and "fweak" in data and data.get("can_cache") is True
    status, body = get(web, "/cmd")
    assert status == 500 and body == b"BAD ARGS"


def test_layout_default_and_upload_roundtrip(web):
    _, body = get(web, "/favorites.json")
    assert json.loads(body) == {"p": [], "s": []}

    boundary = "----oiwebtest"
    payload = ('--%s\r\nContent-Disposition: form-data; name="updatefile"; '
               'filename="uiprefs.json"\r\nContent-Type: application/json\r\n\r\n'
               '{"theme":"dark"}\r\n--%s--\r\n') % (boundary, boundary)
    conn = http.client.HTTPConnection("127.0.0.1", web, timeout=10)
    conn.request("POST", "/edit", payload.encode(),
                 {"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    conn.getresponse().read()
    conn.close()

    _, body = get(web, "/list")
    assert any(f["name"] == "uiprefs.json" for f in json.loads(body))
    _, body = get(web, "/uiprefs.json")
    assert json.loads(body) == {"theme": "dark"}

    conn = http.client.HTTPConnection("127.0.0.1", web, timeout=10)
    conn.request("DELETE", "/edit?f=uiprefs.json")
    conn.getresponse().read()
    conn.close()
    status, _ = get(web, "/uiprefs.json")
    assert status == 404


def test_sse_stream(web):
    s = socket.create_connection(("127.0.0.1", web), timeout=10)
    s.sendall(b"GET /stream?names=udc,tmphs&ms=50 HTTP/1.1\r\nHost: t\r\n\r\n")
    buf = b""
    while b"data: " not in buf:
        chunk = s.recv(4096)
        assert chunk, "connection closed before SSE data arrived"
        buf += chunk
    s.close()
    assert b"text/event-stream" in buf
    line = buf.split(b"data: ")[1].split(b"\n")[0]
    parts = line.strip().split(b"\t")
    assert len(parts) == 2 and float(parts[0]) > 300  # udc ~398V


def test_sse_rejects_bad_names(web):
    status, _ = get(web, "/stream?names=udc;rm&ms=100")
    assert status == 404


def test_can_scan_and_set_node(web):
    _, body = get(web, "/can-scan")
    assert json.loads(body)[0]["nodeId"] == 1
    _, body = get(web, "/set-can-node?id=5")
    assert json.loads(body) == {"nodeId": 5}
    _, body = get(web, "/set-can-node?id=1")
    assert json.loads(body) == {"nodeId": 1}


def test_fwupdate_http_flow(web, rig):
    rig.storage.write_file("stm32.bin", b"\x42" * 900)
    status, body = get(web, "/fwupdate?step=-1&file=%2Fstm32.bin")
    assert status == 200 and json.loads(body)["message"] == "started"
    rig.wait(lambda: json.loads(get(web, "/fwupdate-status")[1])["state"] in (3, 4),
             timeout=30, msg="flash finish")
    st = json.loads(get(web, "/fwupdate-status")[1])
    assert st["state"] == 3 and st["message"] == "Update complete"
    assert rig.sim.flashed[:900] == b"\x42" * 900


def test_can_detect_shape(web):
    status, body = get(web, "/can-detect")
    data = json.loads(body)
    assert status == 200
    assert isinstance(data["configs"], list)
    assert isinstance(data["serial_ports"], list)
    for c in data["configs"]:
        assert "interface" in c and "channel" in c
        assert c["interface"] not in ("virtual", "udp_multicast")
    for p in data["serial_ports"]:
        assert "device" in p and "description" in p


def test_can_detect_by_interface(web):
    status, body = get(web, "/can-detect?interface=slcan")
    data = json.loads(body)
    assert status == 200 and isinstance(data["channels"], list)
    for c in data["channels"]:
        assert "channel" in c and "description" in c
    # an interface with no vendor SDK installed degrades to an empty list,
    # never an error — the frontend shows "no channels found" for this
    status, body = get(web, "/can-detect?interface=this_adapter_does_not_exist")
    assert status == 200 and json.loads(body)["channels"] == []


def post(port, path, body, ctype="application/x-www-form-urlencoded"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body.encode() if isinstance(body, str) else body,
                 {"Content-Type": ctype})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def test_reset_settings_from_body(web):
    status, _ = get(web, "/settings?dev_name=Landy&can_interface=virtual"
                         "&can_channel=sim&can_bitrate=250000&can_node_id=7")
    assert status == 200
    status, body = post(web, "/settings", "reset_settings=1")
    assert status == 200 and json.loads(body)["rebooting"] is False
    _, body = get(web, "/settings")
    data = json.loads(body)
    assert data["dev_name"] == ""
    assert data["can_interface"] == ""
    assert data["can_channel"] == ""
    assert data["can_bitrate"] == 500000
    assert data["can_node_id"] == 1
    assert data["sd_available"] is False


def test_auth_is_absent(web):
    # No /auth/* : the frontend treats that as sign-in off (everyone is admin)
    status, _ = get(web, "/auth/state")
    assert status == 404


def test_redesign_assets(web):
    for path in ("/style.css", "/gridstack-all.js", "/gridstack.min.css",
                 "/inverter.js", "/fonts/intertight.woff2", "/fonts/jbmono.woff2"):
        status, body = get(web, path)
        assert status == 200 and len(body) > 50, path


def test_stub_endpoints(web):
    _, body = get(web, "/version")
    assert body.startswith(b"v") and b"-pc" in body
    _, body = get(web, "/otainfo")
    info = json.loads(body)
    assert info["target"] == "pc" and info["repo"] == ""
    _, body = get(web, "/wifi-status")
    assert json.loads(body)["sta_connected"] is False
    _, body = get(web, "/virtual-reload")
    assert json.loads(body) == {"count": 0}
