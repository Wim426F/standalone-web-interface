"""End-to-end command dispatch over the virtual bus against the simulator."""

import json

from openinverter_web.commands import execute


def test_json_full(rig):
    data = json.loads(execute(rig.device, "json"))
    assert data["can_cache"] is True
    assert data["fweak"]["value"] == 67.0
    assert data["fweak"]["isparam"] is True
    assert data["fweak"]["category"] == "Motor (sine)"
    assert "udc" in data
    # the segmented download landed in the on-disk cache, keyed by serial
    assert list(rig.storage.cache_dir.glob("paramdb_*.json"))


def test_get_and_set(rig):
    execute(rig.device, "json")
    vals = execute(rig.device, "get udc,fweak").split("\t")
    assert len(vals) == 2 and float(vals[1]) == 67.0
    assert execute(rig.device, "set fweak 70") == "Set OK"
    rig.wait(lambda: rig.sim.model["fweak"]["value"] == 70.0, msg="set applied")
    assert execute(rig.device, "set fweak 1000") == "Value out of range"
    assert execute(rig.device, "set nope 1") == "Unknown parameter"
    assert execute(rig.device, "get nope") == "0.00"


def test_start_stop_defaults(rig):
    execute(rig.device, "json")
    assert execute(rig.device, "start 2") == "started"
    rig.wait(lambda: rig.sim.model["opmode"]["value"] == 1, msg="opmode run")
    assert execute(rig.device, "stop") == "stopped"
    rig.wait(lambda: rig.sim.model["opmode"]["value"] == 0, msg="opmode off")
    execute(rig.device, "set fweak 90")
    rig.wait(lambda: rig.sim.model["fweak"]["value"] == 90.0, msg="set applied")
    assert execute(rig.device, "defaults") == "defaults restored"
    rig.wait(lambda: rig.sim.model["fweak"]["value"] == 67, msg="defaults restored")


def test_errors_walk(rig):
    execute(rig.device, "json")
    assert execute(rig.device, "errors") == "No errors logged."
    rig.sim.errors = [(1234, 1), (2345, 2)]
    assert execute(rig.device, "errors") == "[1234]: OVERCURRENT\r\n[2345]: THROTTLE1\r\n"


def test_can_mapping_roundtrip(rig):
    execute(rig.device, "json")
    assert execute(rig.device, "can tx speed 0x351 0 16 1") == "ok"
    out = json.loads(execute(rig.device, "can list"))
    assert out == [{"isrx": False, "id": 849, "paramid": 26, "position": 0,
                    "length": 16, "gain": 1.0, "offset": 0,
                    "index": 0x3100, "subindex": 1}]
    assert execute(rig.device, "can rm %d %d" % (out[0]["index"], out[0]["subindex"])) == "ok"
    assert execute(rig.device, "can list") == "[]"
    assert execute(rig.device, "can rx pot 0x200 8 8 0.5 -10") == "ok"
    out = json.loads(execute(rig.device, "can list"))
    assert out[0]["isrx"] is True
    assert out[0]["gain"] == 0.5 and out[0]["offset"] == -10 and out[0]["position"] == 8
    assert execute(rig.device, "can clear") == "cleared"
    assert execute(rig.device, "can list") == "[]"


def test_mapping_unknown_param_is_error(rig):
    execute(rig.device, "json")
    assert execute(rig.device, "can tx nope 0x123 0 8 1") == "error: unknown parameter"


def test_scan_finds_the_node(rig):
    assert rig.device.scan() == [{"nodeId": 1, "serial": 0x1CAFE042}]
