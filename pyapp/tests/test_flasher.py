"""CAN bootloader flash against the simulator's bootloader state machine."""

import os

from openinverter_web import flasher as F


def flash(rig, size):
    data = os.urandom(size)
    rig.storage.write_file("stm32.bin", data)
    code, resp = rig.flasher.start(rig.storage.file_path("stm32.bin"))
    assert code == 200 and resp["message"] == "started"
    rig.wait(lambda: rig.flasher.state in (F.DONE, F.ERROR), timeout=30, msg="flash finish")
    assert rig.flasher.state == F.DONE, rig.flasher.message
    return data


def test_flash_happy_path(rig):
    data = flash(rig, 2500)
    assert rig.flasher.pages == 3
    assert len(rig.sim.flashed) == 3072  # 3 pages, 0xFF padded
    assert rig.sim.flashed[:2500] == data
    assert rig.sim.flashed[2500:] == b"\xff" * 572
    st = rig.flasher.status()
    assert st["state"] == F.DONE and st["message"] == "Update complete"


def test_flash_survives_one_crc_error(rig):
    rig.sim.fail_crc_once = True
    data = flash(rig, 1000)
    assert rig.sim.flashed[:1000] == data


def test_flash_refuses_bad_files(rig):
    code, resp = rig.flasher.start(rig.storage.files_dir / "nope.bin")
    assert code == 500 and "Failed to open" in resp["message"]
    rig.storage.write_file("big.bin", b"\x00" * (255 * 1024 + 1))
    code, resp = rig.flasher.start(rig.storage.file_path("big.bin"))
    assert code == 500 and "too large" in resp["message"]
