"""Virtual spot values: host-side CAN RX decodes (firmware canVirtualRxHook).

Arbitrary bus frames are decoded into pseudo spot values (names prefixed
"v_", configured in the UI, stored in virtualvals.json). The RX pump feeds
every received frame through feed(); values surface through the json/get
command paths like real spot values.
"""

import json

VIRT_MAX = 16


def _to_int(v, default=0):
    if isinstance(v, str):
        try:
            return int(v, 0)  # decimal, or hex with 0x prefix
        except ValueError:
            return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class _Item:
    __slots__ = ("name", "unit", "id", "pos", "len", "sgn", "gain", "offset", "value", "valid")

    def __init__(self, name, unit, can_id, pos, length, sgn, gain, offset):
        self.name = name
        self.unit = unit
        self.id = can_id
        self.pos = pos
        self.len = length
        self.sgn = sgn
        self.gain = gain
        self.offset = offset
        self.value = 0.0
        self.valid = False


class VirtualValues:
    def __init__(self, storage):
        self.storage = storage
        self.items = []

    def load(self):
        """(Re)load from files/virtualvals.json. Returns the item count."""
        items = []
        raw = self.storage.read_file("virtualvals.json")
        if raw:
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                data = {}
            for obj in (data.get("items") or []):
                if len(items) >= VIRT_MAX:
                    break
                name = str(obj.get("name", ""))
                if not name.startswith("v_") or len(name) < 3:  # enforced prefix
                    continue
                gain = obj.get("gain")
                items.append(_Item(
                    name=name,
                    unit=str(obj.get("unit", "")),
                    can_id=_to_int(obj.get("id")),
                    pos=max(0, min(63, _to_int(obj.get("pos")))),
                    length=max(1, min(32, _to_int(obj.get("len"), 1))),
                    sgn=obj.get("signed") in (True, "true", 1),
                    gain=_to_float(gain, 1.0) if gain is not None else 1.0,
                    offset=_to_float(obj.get("offset")),
                ))
        self.items = items  # atomic swap; feed() iterates a snapshot
        return len(items)

    def feed(self, msg):
        """Called from the RX pump for every received frame."""
        items = self.items
        for v in items:
            if msg.arbitration_id != v.id:
                continue
            data = int.from_bytes(bytes(msg.data)[:8].ljust(8, b"\x00"), "little")
            mask = (1 << v.len) - 1
            raw = (data >> v.pos) & mask
            if v.sgn and raw & (1 << (v.len - 1)):
                raw -= 1 << v.len
            v.value = raw * v.gain + v.offset
            v.valid = True

    def find(self, name):
        """(True, value|None) when the name belongs to the virtual table —
        membership decides routing, not the prefix. value None = no frame yet."""
        for v in self.items:
            if v.name == name:
                return True, (v.value if v.valid else None)
        return False, None
