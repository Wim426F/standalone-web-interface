"""File storage: the PC stand-in for the ESP32's SPIFFS.

Layout under the user data directory:
    settings.json                 adapter config, node id, device name
    files/                        UI blobs uploaded via /edit (favorites.json,
                                  gauges.json, uiprefs.json, stm32.bin, ...)
    cache/paramdb_<serial>.json   parameter database, keyed by device serial

The frontend itself is served from the repo's data/ directory in a dev
checkout, or from the copy embedded in the wheel (openinverter_web/data).
"""

import os
from pathlib import Path


def default_data_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "openinverter-web"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "openinverter-web"


def frontend_dir():
    """Locate the web frontend.

    A checkout serves the repo's data/ directory, which is the copy the ESP32
    image is built from. An installed wheel has no such neighbour and uses the
    data/ embedded next to this package.
    """
    repo = Path(__file__).resolve().parents[2] / "data"
    if (repo / "index.html").is_file() and (repo.parent / "esp32-web-interface.ino").is_file():
        return repo
    pkg = Path(__file__).resolve().parent / "data"
    if (pkg / "index.html").is_file():
        return pkg
    raise SystemExit(
        "Web frontend not found: neither the packaged data/ nor a repo checkout "
        "data/ directory exists. Reinstall the package or run from the repo."
    )


class Storage:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.files_dir = self.data_dir / "files"
        self.cache_dir = self.data_dir / "cache"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def settings_path(self):
        return self.data_dir / "settings.json"

    @property
    def instance_marker_path(self):
        return self.data_dir / "running.json"

    # SPIFFS was a flat namespace; keep ours flat too so "/stm32.bin" and
    # "stm32.bin" are the same file and traversal is structurally impossible.
    @staticmethod
    def safe_name(name):
        name = (name or "").replace("\\", "/").lstrip("/")
        name = os.path.basename(name)
        if not name or name in (".", ".."):
            return None
        return name

    def file_path(self, name):
        name = self.safe_name(name)
        return (self.files_dir / name) if name else None

    def read_file(self, name):
        p = self.file_path(name)
        if p and p.is_file():
            return p.read_bytes()
        return None

    def write_file(self, name, data):
        p = self.file_path(name)
        if not p:
            return False
        p.write_bytes(data)
        return True

    def delete_file(self, name):
        p = self.file_path(name)
        if p and p.is_file():
            p.unlink()
            return True
        return False

    def list_files(self):
        out = []
        for p in sorted(self.files_dir.iterdir()):
            if p.is_file():
                out.append({"type": "file", "name": p.name, "size": p.stat().st_size})
        return out

    # ---- parameter database cache (keyed by device serial) ----

    def _paramdb_path(self, serial):
        return self.cache_dir / ("paramdb_%08x.json" % (serial & 0xFFFFFFFF))

    def load_paramdb_cache(self, serial):
        p = self._paramdb_path(serial)
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def save_paramdb_cache(self, serial, text):
        try:
            self._paramdb_path(serial).write_text(text, encoding="utf-8")
        except OSError:
            pass

    def drop_paramdb_cache(self, serial=None):
        if serial is not None:
            p = self._paramdb_path(serial)
            if p.is_file():
                p.unlink()
            return
        for p in self.cache_dir.glob("paramdb_*.json"):
            p.unlink()
