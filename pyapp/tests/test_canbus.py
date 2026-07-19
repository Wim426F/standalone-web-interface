"""CanBus connection error handling — the friendlier busy-port hint."""

import can

from openinverter_web.canbus import CanBus


def test_permission_error_gets_a_helpful_hint(monkeypatch):
    def raise_busy(**kwargs):
        raise can.CanInitializationError(
            "could not open port 'COM6': PermissionError(13, 'Access is denied.', None, 5)")
    monkeypatch.setattr(can, "Bus", raise_busy)

    bus = CanBus(lambda: {"interface": "slcan", "channel": "COM6", "bitrate": 500000})
    result = bus._connect()

    assert result is None
    assert bus.state == "error"
    assert "already open by another program" in bus.status_msg


def test_other_errors_pass_through_unhinted(monkeypatch):
    def raise_missing(**kwargs):
        raise can.CanInitializationError("device not found")
    monkeypatch.setattr(can, "Bus", raise_missing)

    bus = CanBus(lambda: {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 500000})
    bus._connect()

    assert "already open by another program" not in bus.status_msg
    assert "device not found" in bus.status_msg


def test_no_adapter_configured(monkeypatch):
    bus = CanBus(lambda: None)
    assert bus._connect() is None
    assert bus.status_msg == "No CAN adapter configured"
