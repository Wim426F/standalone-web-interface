"""The startup heads-up when another instance already holds the data dir."""

import http.server
import threading

from openinverter_web.cli import _warn_if_already_running, _write_instance_marker, _remove_instance_marker
from openinverter_web.storage import Storage


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


def test_warns_when_marker_points_at_a_live_server(tmp_path, capsys):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        storage = Storage(tmp_path / "appdata")
        _write_instance_marker(storage, "127.0.0.1", port)
        _warn_if_already_running(storage)
        assert "already running" in capsys.readouterr().out
    finally:
        httpd.shutdown()


def test_silent_when_no_marker(tmp_path, capsys):
    storage = Storage(tmp_path / "appdata")
    _warn_if_already_running(storage)
    assert capsys.readouterr().out == ""


def test_silent_when_marker_is_stale(tmp_path, capsys):
    storage = Storage(tmp_path / "appdata")
    _write_instance_marker(storage, "127.0.0.1", 1)  # nothing listens on port 1
    _warn_if_already_running(storage)
    assert capsys.readouterr().out == ""


def test_remove_marker_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "appdata")
    _remove_instance_marker(storage)  # no marker yet — must not raise
    _write_instance_marker(storage, "127.0.0.1", 1234)
    assert storage.instance_marker_path.exists()
    _remove_instance_marker(storage)
    assert not storage.instance_marker_path.exists()
