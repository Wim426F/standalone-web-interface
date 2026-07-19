"""CANopen SDO client for the OpenInverter object dictionary.

A direct port of src/can_protocol.{h,cpp} from the ESP32 firmware: expedited
reads/writes at 0x600+node / 0x580+node, segmented upload for the JSON
parameter database, ×32 fixed-point value encoding, and the same
drain-send-wait transaction discipline (stale frames are drained before each
request so a late response can't be mismatched to the wrong read).

Every transaction holds the bus tx_lock, serializing SDO access exactly like
the single-threaded ESP32 loop did.
"""

import struct

# CAN ID bases
SDO_REQUEST_BASE = 0x600   # + nodeId
SDO_RESPONSE_BASE = 0x580  # + nodeId
BOOTLOADER_CMD_ID = 0x7DD
BOOTLOADER_RESP_ID = 0x7DE

# SDO command bytes
SDO_READ_REQUEST = 0x40
SDO_READ_RESPONSE = 0x43
SDO_WRITE_REQUEST_4B = 0x23
SDO_WRITE_RESPONSE = 0x60
SDO_RESPONSE_UPLOAD = 0x40
SDO_REQUEST_SEGMENT = 0x60
SDO_TOGGLE_BIT = 0x10
SDO_EXPEDITED = 0x02
SDO_SIZE_SPECIFIED = 0x01
SDO_ABORT = 0x80

# OpenInverter object dictionary
INDEX_PARAMS = 0x2000      # named parameters (by param ID)
INDEX_PARAM_UID = 0x2100   # parameter by UID (live value reads/writes)
INDEX_SERIAL = 0x5000      # device serial number
INDEX_JSON = 0x5001        # JSON parameter database (segmented)
INDEX_COMMANDS = 0x5002    # device commands (sub-index selects the command)
INDEX_ERRORS = 0x5003      # error log: codes
INDEX_ERROR_TIME = 0x5004  # error log: timestamps
INDEX_MAP_TX = 0x3000      # add TX CAN mapping (write-only, 3-stage)
INDEX_MAP_RX = 0x3001      # add RX CAN mapping
INDEX_MAP_RD = 0x3100      # read TX mappings
INDEX_MAP_RD_RX = 0x3180   # read RX mappings

SDO_ERR_RANGE = 0x06090030

# Command values (sub-index into INDEX_COMMANDS)
CMD_SAVE = 0
CMD_LOAD = 1
CMD_RESET = 2
CMD_DEFAULTS = 3
CMD_START = 4
CMD_STOP = 5
CMD_CLEAR_MAP = 6


def param_index(param_id):
    return INDEX_PARAM_UID | (param_id >> 8)


def param_sub(param_id):
    return param_id & 0xFF


def encode_value(value):
    """Fixed-point ×32, as the firmware's canEncodeValue."""
    return int(value * 32)


def decode_value(raw):
    return raw / 32.0


def read_request(index, sub):
    return bytes([SDO_READ_REQUEST, index & 0xFF, (index >> 8) & 0xFF, sub & 0xFF, 0, 0, 0, 0])


def write_request(index, sub, value):
    return bytes([SDO_WRITE_REQUEST_4B, index & 0xFF, (index >> 8) & 0xFF, sub & 0xFF]) + \
        struct.pack("<I", value & 0xFFFFFFFF)


def _echo_matches(data, index, sub):
    return (data[1] | (data[2] << 8)) == index and data[3] == (sub & 0xFF)


class SdoClient:
    """Serialized SDO transactions against the currently selected node."""

    def __init__(self, bus, node_id_fn):
        self.bus = bus
        self.node = node_id_fn  # callable: the active node id (mutable via /set-can-node)

    def _txn(self, payload, timeout):
        with self.bus.tx_lock:
            self.bus.drain_sdo()
            if not self.bus.send(SDO_REQUEST_BASE | self.node(), payload):
                return None
            return self.bus.wait_sdo(self.node(), timeout)

    def read(self, index, sub, timeout=0.05):
        """Expedited read. Returns the raw signed int32, or None on failure
        (timeout, abort, or a response for a different object)."""
        resp = self._txn(read_request(index, sub), timeout)
        if resp is None or resp.data[0] != SDO_READ_RESPONSE:
            return None
        if not _echo_matches(resp.data, index, sub):
            return None
        return struct.unpack("<i", bytes(resp.data[4:8]))[0]

    # Alias used when walking mapping/error tables, where an abort is the
    # normal "end of table" signal (firmware canSdoReadEntry, 50 ms timeout).
    def read_entry(self, index, sub, timeout=0.05):
        return self.read(index, sub, timeout)

    def write(self, index, sub, value, timeout=0.2):
        """Checked write. Returns 0 on success, the SDO abort code on
        rejection, -1 on communication failure (firmware canSdoWriteChecked)."""
        resp = self._txn(write_request(index, sub, value), timeout)
        if resp is None:
            return -1
        if resp.data[0] == SDO_WRITE_RESPONSE:
            return 0
        if resp.data[0] == SDO_ABORT:
            code = struct.unpack("<i", bytes(resp.data[4:8]))[0]
            return code if code else -1
        return -1

    def write_nowait(self, index, sub, value):
        """Fire-and-forget write (firmware canSdoWrite/canSdoCommand). The
        confirmation frame lands in the SDO queue and is drained by the next
        transaction."""
        with self.bus.tx_lock:
            return self.bus.send(SDO_REQUEST_BASE | self.node(), write_request(index, sub, value))

    def command(self, cmd):
        return self.write_nowait(INDEX_COMMANDS, cmd, 0)

    def reset_device(self):
        return self.write_nowait(INDEX_COMMANDS, CMD_RESET, 1)

    def read_segmented(self, index, sub=0, seg_timeout=0.1, max_len=256 * 1024):
        """CANopen segmented upload (firmware canSdoReadSegmented).

        Returns (data, complete): complete is True only when the device's
        last-segment flag was received — a truncated transfer must be retried
        whole, since blind re-requests can silently skip data.
        """
        with self.bus.tx_lock:
            self.bus.drain_sdo()
            node = self.node()
            if not self.bus.send(SDO_REQUEST_BASE | node, read_request(index, sub)):
                return b"", False
            resp = self.bus.wait_sdo(node, seg_timeout)
            if resp is None:
                return b"", False
            cmd = resp.data[0]
            if cmd == SDO_ABORT or (cmd & 0xE0) != SDO_RESPONSE_UPLOAD:
                return b"", False

            # Expedited: the object fit in the initiate response's 4 data bytes
            if cmd & SDO_EXPEDITED:
                n = 4 - ((cmd >> 2) & 0x3) if (cmd & SDO_SIZE_SPECIFIED) else 4
                return bytes(resp.data[4:4 + n]), True

            out = bytearray()
            toggle = False
            while len(out) < max_len:
                expected = SDO_TOGGLE_BIT if toggle else 0
                req = bytes([SDO_REQUEST_SEGMENT | expected]) + b"\x00" * 7
                if not self.bus.send(SDO_REQUEST_BASE | node, req):
                    return bytes(out), False
                resp = self.bus.wait_sdo(node, seg_timeout)
                if resp is None:
                    return bytes(out), False
                cmd = resp.data[0]
                if cmd & SDO_ABORT:
                    return bytes(out), False
                if (cmd & SDO_TOGGLE_BIT) != expected:
                    return bytes(out), False
                n = 7 - ((cmd >> 1) & 0x7)
                out += bytes(resp.data[1:1 + n])
                if cmd & SDO_SIZE_SPECIFIED:  # last segment
                    return bytes(out), True
                toggle = not toggle
            return bytes(out), False  # buffer-full equivalent
