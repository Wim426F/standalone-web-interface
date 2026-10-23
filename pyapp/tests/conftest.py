"""Shared test rig: a full app stack wired to the simulator on an isolated
python-can 'virtual' channel (no hardware, no sockets beyond localhost)."""

import time
import uuid

import pytest

from openinverter_web.canbus import CanBus
from openinverter_web.device import Device
from openinverter_web.flasher import Flasher
from openinverter_web.settings import Settings
from openinverter_web.sim import SimInverter
from openinverter_web.storage import Storage
from openinverter_web.virtualvals import VirtualValues


class Rig:
    def __init__(self, tmp_path):
        channel = "test-%s" % uuid.uuid4().hex[:8]
        self.sim = SimInverter(channel=channel)
        self.storage = Storage(tmp_path / "appdata")
        self.settings = Settings(self.storage)
        self.bus = CanBus(lambda: {"interface": "virtual", "channel": channel, "bitrate": 500000})
        self.virtual = VirtualValues(self.storage)
        self.device = Device(self.bus, self.settings, self.storage, self.virtual)
        self.flasher = Flasher(self.bus, self.device.sdo)
        self.device.flasher = self.flasher
        self.bus.set_rx_hook(self.virtual.feed)
        self.bus.start()
        deadline = time.monotonic() + 5
        while not self.bus.connected() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.bus.connected(), "virtual bus did not come up"

    def stop(self):
        self.bus.stop()
        self.sim.stop()

    def wait(self, cond, timeout=5.0, msg="condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return True
            time.sleep(0.02)
        raise AssertionError("timeout waiting for " + msg)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.stop()
