import json

from openinverter_web.storage import Storage
from openinverter_web.virtualvals import VirtualValues


class Msg:
    def __init__(self, can_id, data):
        self.arbitration_id = can_id
        self.data = bytearray(data)


def make(tmp_path, items):
    storage = Storage(tmp_path / "appdata")
    storage.write_file("virtualvals.json", json.dumps({"items": items}).encode())
    vv = VirtualValues(storage)
    assert vv.load() == len([i for i in items if str(i.get("name", "")).startswith("v_")])
    return vv


def test_extraction_gain_offset_and_sign(tmp_path):
    vv = make(tmp_path, [
        {"name": "v_soc", "unit": "%", "id": "0x123", "pos": 0, "len": 16, "gain": 0.1, "offset": 0},
        {"name": "v_temp", "unit": "C", "id": 291, "pos": 16, "len": 8, "signed": True, "gain": 1, "offset": -40},
    ])
    # uint16 1000 little-endian at bit 0; int8 -5 (0xFB) at bit 16
    vv.feed(Msg(0x123, bytes([0xE8, 0x03, 0xFB, 0, 0, 0, 0, 0])))
    found, soc = vv.find("v_soc")
    assert found and abs(soc - 100.0) < 1e-9
    found, temp = vv.find("v_temp")
    assert found and temp == -45.0


def test_prefix_enforced_and_unseen_is_none(tmp_path):
    vv = make(tmp_path, [
        {"name": "nope", "id": 1, "pos": 0, "len": 8},
        {"name": "v_x", "id": 2, "pos": 0, "len": 8},
    ])
    assert len(vv.items) == 1
    found, val = vv.find("v_x")
    assert found and val is None  # no frame seen yet
    assert vv.find("nope") == (False, None)


def test_wrong_id_ignored(tmp_path):
    vv = make(tmp_path, [{"name": "v_x", "id": 0x100, "pos": 0, "len": 8}])
    vv.feed(Msg(0x101, bytes([42])))
    assert vv.find("v_x") == (True, None)
    vv.feed(Msg(0x100, bytes([42])))
    assert vv.find("v_x") == (True, 42.0)
