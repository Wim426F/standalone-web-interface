"""Device model: parameter database, live values, errors, node scan.

Ports the ESP32 firmware's CAN-mode logic: canDownloadParamCache,
canBuildJsonWithValues, canReadParamValue, canGetParamId, canLookupEnum and
the errors/scan walks. One addition over the firmware: the downloaded
parameter database is cached on disk keyed by device serial, so restarts
skip the (slow) segmented download; live values are always read fresh.
"""

import json
import struct
import threading
import time

from . import sdo as S


def lookup_enum(unit_str, value):
    """Translate an enum value to its name using a unit string like
    "0=None, 1=UdcLow" (firmware canLookupEnum)."""
    key = "%d=" % value
    pos = -1
    if unit_str.startswith(key):
        pos = len(key)
    else:
        i = unit_str.find("," + key)
        if i >= 0:
            pos = i + 1 + len(key)
        else:
            i = unit_str.find(", " + key)
            if i >= 0:
                pos = i + 2 + len(key)
    if pos < 0:
        return str(value)
    end = unit_str.find(",", pos)
    if end < 0:
        end = len(unit_str)
    name = unit_str[pos:end].strip()
    return name if name else str(value)


class Device:
    def __init__(self, bus, settings, storage, virtualvals):
        self.bus = bus
        self.settings = settings
        self.storage = storage
        self.virtual = virtualvals
        self.sdo = S.SdoClient(bus, lambda: settings.can_node_id)
        self.params = {}       # parsed param DB: name -> entry dict
        self.param_text = ""
        self.cache_loaded = False
        self.flasher = None    # wired up by the app
        self._db_lock = threading.Lock()

    # ---- parameter database ----

    def invalidate(self, drop_disk=False):
        """Drop the in-memory DB (node switch / settings change). With
        drop_disk also forget the on-disk copies, forcing a fresh download."""
        self.cache_loaded = False
        self.params = {}
        self.param_text = ""
        if drop_disk:
            self.storage.drop_paramdb_cache()

    def read_serial(self):
        raw = self.sdo.read(S.INDEX_SERIAL, 0, timeout=0.1)
        return None if raw is None else raw & 0xFFFFFFFF

    def ensure_param_db(self):
        if self.cache_loaded:
            return True
        with self._db_lock:
            if self.cache_loaded:
                return True
            if not self.bus.connected():
                return False
            serial = self.read_serial()
            if serial is not None:
                text = self.storage.load_paramdb_cache(serial)
                if text and self._adopt(text):
                    return True
            # A single lost segment truncates the transfer, so retry the whole
            # download and only accept transfers that finished with the
            # device's last-segment flag (firmware canDownloadParamCache).
            for _attempt in range(3):
                data, complete = self.sdo.read_segmented(S.INDEX_JSON, 0, seg_timeout=0.1)
                text = data.split(b"\x00")[0].decode("utf-8", "replace").strip()
                if complete and len(text) > 64 and text.startswith("{") and text.endswith("}"):
                    if self._adopt(text):
                        if serial is not None:
                            self.storage.save_paramdb_cache(serial, text)
                        return True
                time.sleep(0.05)
            return False

    def _adopt(self, text):
        try:
            parsed = json.loads(text)
        except ValueError:
            return False
        if not isinstance(parsed, dict):
            return False
        self.params = parsed
        self.param_text = text
        self.cache_loaded = True
        return True

    def param_id(self, name):
        entry = self.params.get(name)
        if not isinstance(entry, dict):
            return None
        try:
            return int(entry["id"])
        except (KeyError, TypeError, ValueError):
            return None

    def unit_of(self, name):
        entry = self.params.get(name)
        return str(entry.get("unit", "")) if isinstance(entry, dict) else ""

    # ---- live values ----

    def read_value(self, param_id):
        raw = self.sdo.read(S.param_index(param_id), S.param_sub(param_id), timeout=0.05)
        return None if raw is None else S.decode_value(raw)

    def format_value(self, name):
        """Value string for 'get'/SSE: virtuals first, else an SDO read;
        failures render as "0.00" like the firmware."""
        is_virtual, val = self.virtual.find(name)
        if not is_virtual:
            pid = self.param_id(name)
            val = self.read_value(pid) if pid is not None else None
        return "0.00" if val is None else "%.2f" % val

    def build_json_with_values(self):
        """The 'json' response: cached metadata with live values spliced in
        (firmware canBuildJsonWithValues)."""
        out = {}
        failed = success = 0
        skip = False  # set when the device looks offline — avoids hundreds of timeouts
        for name, entry in self.params.items():
            if not isinstance(entry, dict):
                out[name] = entry
                continue
            e = dict(entry)
            try:
                pid = int(e.get("id", 0))
            except (TypeError, ValueError):
                pid = 0
            if pid > 0 and not skip:
                val = self.read_value(pid)
                if val is not None:
                    success += 1
                    e["value"] = float("%.2f" % val)
                else:
                    failed += 1
                    if failed >= 8 and success == 0:
                        skip = True
            out[name] = e
        for item in self.virtual.items:
            out[item.name] = {
                "unit": item.unit,
                "isparam": False,
                "virtual": True,
                "value": float("%.2f" % item.value) if item.valid else 0,
            }
        # Only claim a live connection when reads mostly succeeded — otherwise
        # the UI would show stale cached values as if they were current
        if failed < 5:
            out["can_cache"] = True
        return json.dumps(out, separators=(",", ":"))

    # ---- errors ----

    def read_errors(self):
        """Walk the error log: timestamps at 0x5004, codes at 0x5003."""
        unit = self.unit_of("lasterr")
        lines = []
        for i in range(100):
            err_time = self.sdo.read_entry(S.INDEX_ERROR_TIME, i)
            if err_time is None or err_time == 0:
                break
            err_num = self.sdo.read_entry(S.INDEX_ERRORS, i)
            if err_num is None:
                break
            lines.append("[%d]: %s\r\n" % (err_time, lookup_enum(unit, err_num & 0xFFFFFFFF)))
        return "".join(lines) if lines else "No errors logged."

    # ---- node scan ----

    def scan(self):
        """Probe node IDs 1-127 by reading the serial number (0x5000)."""
        found = []
        with self.bus.tx_lock:
            for nid in range(1, 128):
                self.bus.drain_sdo()
                if not self.bus.send(S.SDO_REQUEST_BASE | nid, S.read_request(S.INDEX_SERIAL, 0)):
                    break  # bus gone — no point probing the remaining nodes
                resp = self.bus.wait_sdo(nid, 0.04)
                if resp is not None and resp.data[0] == S.SDO_READ_RESPONSE:
                    serial = struct.unpack("<i", bytes(resp.data[4:8]))[0]
                    found.append({"nodeId": nid, "serial": serial})
                time.sleep(0.002)
        return found
