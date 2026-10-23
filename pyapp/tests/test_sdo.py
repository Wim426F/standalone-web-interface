"""SDO frame codec and segmented-upload reassembly against scripted frames."""

import threading
from collections import deque

from openinverter_web import sdo as S


class Msg:
    def __init__(self, can_id, data):
        self.arbitration_id = can_id
        self.data = bytearray(data)


class FakeBus:
    def __init__(self):
        self.tx_lock = threading.RLock()
        self.sent = []
        self.responses = deque()

    def send(self, can_id, data):
        self.sent.append((can_id, bytes(data)))
        return True

    def drain_sdo(self):
        pass

    def wait_sdo(self, node, timeout):
        return self.responses.popleft() if self.responses else None


def client(bus, node=1):
    return S.SdoClient(bus, lambda: node)


def test_frame_builders():
    assert S.read_request(0x2100, 0x05) == bytes([0x40, 0x00, 0x21, 0x05, 0, 0, 0, 0])
    assert S.write_request(0x5002, 2, 1) == bytes([0x23, 0x02, 0x50, 0x02, 1, 0, 0, 0])
    # negative values ride as two's complement little-endian
    assert S.write_request(0x2100, 0, -48)[4:] == b"\xd0\xff\xff\xff"


def test_value_encoding_x32():
    assert S.encode_value(67.0) == 2144
    assert S.encode_value(-1.5) == -48
    assert S.decode_value(2144) == 67.0
    assert S.param_index(0x1234) == 0x2112
    assert S.param_sub(0x1234) == 0x34


def test_read_parses_expedited_response():
    bus = FakeBus()
    bus.responses.append(Msg(0x581, bytes([0x43, 0x00, 0x21, 0x05]) + (2144).to_bytes(4, "little")))
    assert client(bus).read(0x2100, 0x05) == 2144
    assert bus.sent[0] == (0x601, S.read_request(0x2100, 0x05))


def test_read_rejects_mismatched_echo_and_aborts():
    bus = FakeBus()
    bus.responses.append(Msg(0x581, bytes([0x43, 0x99, 0x21, 0x05, 0, 0, 0, 0])))
    assert client(bus).read(0x2100, 0x05) is None
    bus.responses.append(Msg(0x581, bytes([0x80, 0x00, 0x21, 0x05]) + (0x06020000).to_bytes(4, "little")))
    assert client(bus).read(0x2100, 0x05) is None
    assert client(bus).read(0x2100, 0x05) is None  # timeout (no response)


def test_write_result_codes():
    bus = FakeBus()
    bus.responses.append(Msg(0x581, bytes([0x60, 0x00, 0x21, 0x05, 0, 0, 0, 0])))
    assert client(bus).write(0x2100, 0x05, 123) == 0
    bus.responses.append(Msg(0x581, bytes([0x80, 0x00, 0x21, 0x05]) + S.SDO_ERR_RANGE.to_bytes(4, "little")))
    assert client(bus).write(0x2100, 0x05, 123) == S.SDO_ERR_RANGE
    assert client(bus).write(0x2100, 0x05, 123) == -1  # timeout


def _segment(toggle, data, last):
    cmd = (S.SDO_TOGGLE_BIT if toggle else 0) | ((7 - len(data)) << 1) | (1 if last else 0)
    return Msg(0x581, bytes([cmd]) + bytes(data).ljust(7, b"\x00"))


def test_segmented_reassembly():
    bus = FakeBus()
    bus.responses.append(Msg(0x581, bytes([0x41, 0x01, 0x50, 0x00]) + (10).to_bytes(4, "little")))
    bus.responses.append(_segment(False, b"{|abcde", False))
    bus.responses.append(_segment(True, b"fg}", True))
    data, complete = client(bus).read_segmented(0x5001)
    assert complete and data == b"{|abcdefg}"
    # segment requests alternate the toggle bit
    assert bus.sent[1][1][0] == 0x60
    assert bus.sent[2][1][0] == 0x70


def test_segmented_toggle_mismatch_is_incomplete():
    bus = FakeBus()
    bus.responses.append(Msg(0x581, bytes([0x41, 0x01, 0x50, 0x00]) + (20).to_bytes(4, "little")))
    bus.responses.append(_segment(False, b"1234567", False))
    bus.responses.append(_segment(False, b"89", True))  # wrong toggle
    data, complete = client(bus).read_segmented(0x5001)
    assert not complete and data == b"1234567"


def test_segmented_expedited_short_object():
    bus = FakeBus()
    # expedited, size specified, n = (cmd>>2)&3 = 3 -> 1 valid byte
    bus.responses.append(Msg(0x581, bytes([0x4F, 0x00, 0x50, 0x00, ord("x"), 0, 0, 0])))
    data, complete = client(bus).read_segmented(0x5000)
    assert complete and data == b"x"
