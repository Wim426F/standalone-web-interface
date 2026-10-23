"""Simulated OpenInverter node on a python-can 'virtual' bus.

Implements the device side of everything the app speaks: expedited SDO
reads/writes with range aborts, segmented upload of the JSON parameter
database, the command/error/mapping objects, and the CAN bootloader state
machine with real CRC verification. `openinverter-web --simulate` wires one
of these to the same in-process virtual channel, so every feature is
exercisable end-to-end with zero hardware.
"""

import json
import math
import struct
import threading
import time

import can

from . import sdo as S
from .flasher import crc32_chunk, PAGE_SIZE


def default_model():
    """A small stm32-sine-like parameter set (mirrors tests/mock-esp)."""
    return {
        "fweak":     {"unit": "Hz", "value": 67.0, "isparam": True, "minimum": 0, "maximum": 400, "default": 67, "category": "Motor (sine)", "i": 0, "id": 1},
        "boost":     {"unit": "dig", "value": 1700, "isparam": True, "minimum": 0, "maximum": 37813, "default": 1700, "category": "Motor (sine)", "i": 1, "id": 2},
        "polepairs": {"unit": "", "value": 2, "isparam": True, "minimum": 1, "maximum": 16, "default": 2, "category": "Motor", "i": 2, "id": 3},
        "udcmin":    {"unit": "V", "value": 450, "isparam": True, "minimum": 0, "maximum": 1000, "default": 450, "category": "Voltage", "i": 3, "id": 4},
        "udcmax":    {"unit": "V", "value": 520, "isparam": True, "minimum": 0, "maximum": 1000, "default": 520, "category": "Voltage", "i": 4, "id": 5},
        "dirmode":   {"unit": "0=Button, 1=Switch, 2=ButtonReversed, 3=SwitchReversed, 4=DefaultForward", "value": 1, "isparam": True, "minimum": 0, "maximum": 4, "default": 1, "category": "Derate", "i": 5, "id": 6},
        "potmin":    {"unit": "dig", "value": 0, "isparam": True, "minimum": 0, "maximum": 4095, "default": 0, "category": "Throttle", "i": 6, "id": 7},
        "potmax":    {"unit": "dig", "value": 4095, "isparam": True, "minimum": 0, "maximum": 4095, "default": 4095, "category": "Throttle", "i": 7, "id": 8},
        "Set_Hour":  {"unit": "Hours", "value": 9, "isparam": True, "minimum": 0, "maximum": 23, "default": 0, "category": "Clock", "i": 8, "id": 9},
        "version":   {"unit": "4=5.27.R-sine", "value": 4, "isparam": False, "id": 20},
        "opmode":    {"unit": "0=Off, 1=Run, 2=ManualRun, 3=Boost, 4=Buck, 5=Sine, 6=AcHeat, 7=Chademo", "value": 0, "isparam": False, "id": 21},
        "status":    {"unit": "0=None, 1=UdcLow, 2=UdcHigh, 4=UdcBelowUdcSw, 8=UdcLim, 16=EmcyStop, 32=MProt, 64=PotPressed, 128=TmpHs, 256=WaitStart", "value": 0, "isparam": False, "id": 22},
        "lasterr":   {"unit": "0=NONE, 1=OVERCURRENT, 2=THROTTLE1", "value": 0, "isparam": False, "id": 23},
        "udc":       {"unit": "V", "value": 398.5, "isparam": False, "id": 24},
        "tmphs":     {"unit": "°C", "value": 31.2, "isparam": False, "id": 25},
        "speed":     {"unit": "rpm", "value": 0, "isparam": False, "id": 26},
        "pot":       {"unit": "dig", "value": 0, "isparam": False, "id": 27},
    }


ABORT_NO_OBJECT = 0x06020000
ABORT_NO_SUBINDEX = 0x06090011
ABORT_READONLY = 0x06010002


class SimInverter:
    def __init__(self, channel="oi-sim", node_id=1, serial=0x1CAFE042):
        self.channel = channel
        self.node_id = node_id
        self.serial = serial & 0xFFFFFFFF
        self.model = default_model()
        self.by_id = {e["id"]: n for n, e in self.model.items()}
        self.errors = []          # [(timestamp, code), ...] for the error log
        self.tx_maps = []         # [{"cobid": u32, "items": [(sub1, sub2), ...]}]
        self.rx_maps = []
        self._pending = {}        # 3-stage mapping adds, keyed by write index
        self._seg = None          # active segmented upload state
        self.boot = None          # bootloader state (device "in bootloader" when set)
        self.flashed = None       # completed firmware image (for test assertions)
        self.fail_crc_once = False
        self.running = True
        self.bus = can.Bus(interface="virtual", channel=channel)
        self._rx_thread = threading.Thread(target=self._rx_loop, name="sim-rx", daemon=True)
        self._tick_thread = threading.Thread(target=self._tick_loop, name="sim-tick", daemon=True)
        self._rx_thread.start()
        self._tick_thread.start()

    def stop(self):
        self.running = False
        try:
            self.bus.shutdown()
        except Exception:
            pass

    # ---- TX helpers ----

    def _send(self, can_id, data):
        try:
            self.bus.send(can.Message(arbitration_id=can_id, data=bytes(data)[:8],
                                      is_extended_id=can_id > 0x7FF))
        except Exception:
            pass

    def _sdo(self, payload):
        self._send(S.SDO_RESPONSE_BASE | self.node_id, payload)

    def _read_reply(self, index, sub, value):
        self._sdo(bytes([S.SDO_READ_RESPONSE, index & 0xFF, index >> 8, sub]) +
                  struct.pack("<I", value & 0xFFFFFFFF))

    def _write_reply(self, index, sub):
        self._sdo(bytes([S.SDO_WRITE_RESPONSE, index & 0xFF, index >> 8, sub, 0, 0, 0, 0]))

    def _abort(self, index, sub, code):
        self._sdo(bytes([S.SDO_ABORT, index & 0xFF, index >> 8, sub]) +
                  struct.pack("<I", code))

    # ---- RX loop ----

    def _rx_loop(self):
        while self.running:
            try:
                msg = self.bus.recv(0.2)
            except Exception:
                return
            if msg is None:
                continue
            if msg.arbitration_id == S.BOOTLOADER_CMD_ID and self.boot is not None:
                self._handle_boot(bytes(msg.data))
            elif msg.arbitration_id == (S.SDO_REQUEST_BASE | self.node_id) and self.boot is None:
                self._handle_sdo(bytes(msg.data).ljust(8, b"\x00"))

    # ---- SDO server ----

    def _handle_sdo(self, d):
        cmd, sub = d[0], d[3]
        index = d[1] | (d[2] << 8)

        if cmd == S.SDO_READ_REQUEST:
            return self._handle_read(index, sub)
        if cmd == S.SDO_WRITE_REQUEST_4B:
            return self._handle_write(index, sub, struct.unpack("<I", d[4:8])[0])
        if (cmd & 0xE0) == S.SDO_REQUEST_SEGMENT:
            return self._handle_segment(cmd)

    def _handle_read(self, index, sub):
        if index == S.INDEX_JSON:
            data = json.dumps(self.model).encode("utf-8")
            self._seg = {"data": data, "pos": 0, "toggle": False}
            # segmented initiate response, size specified
            self._sdo(bytes([S.SDO_RESPONSE_UPLOAD | S.SDO_SIZE_SPECIFIED,
                             index & 0xFF, index >> 8, sub]) + struct.pack("<I", len(data)))
            return
        if index == S.INDEX_SERIAL:
            if sub == 0:
                return self._read_reply(index, sub, self.serial)
            return self._abort(index, sub, ABORT_NO_SUBINDEX)
        if (index >> 8) == (S.INDEX_PARAM_UID >> 8):
            pid = ((index & 0xFF) << 8) | sub
            name = self.by_id.get(pid)
            if name is None:
                return self._abort(index, sub, ABORT_NO_OBJECT)
            raw = int(round(self.model[name]["value"] * 32))
            return self._read_reply(index, sub, raw)
        if index in (S.INDEX_ERROR_TIME, S.INDEX_ERRORS):
            if sub < len(self.errors):
                t, code = self.errors[sub]
                return self._read_reply(index, sub, t if index == S.INDEX_ERROR_TIME else code)
            return self._abort(index, sub, ABORT_NO_SUBINDEX)
        if S.INDEX_MAP_RD <= index < S.INDEX_MAP_RD + 0x80:
            return self._read_map(self.tx_maps, index - S.INDEX_MAP_RD, index, sub)
        if S.INDEX_MAP_RD_RX <= index < S.INDEX_MAP_RD_RX + 0x80:
            return self._read_map(self.rx_maps, index - S.INDEX_MAP_RD_RX, index, sub)
        return self._abort(index, sub, ABORT_NO_OBJECT)

    def _read_map(self, maps, mi, index, sub):
        if mi >= len(maps):
            return self._abort(index, sub, ABORT_NO_OBJECT)
        entry = maps[mi]
        if sub == 0:
            return self._read_reply(index, sub, entry["cobid"])
        item = (sub - 1) // 2
        if item >= len(entry["items"]):
            return self._abort(index, sub, ABORT_NO_SUBINDEX)
        pair = entry["items"][item]
        return self._read_reply(index, sub, pair[0] if (sub % 2) == 1 else pair[1])

    def _handle_write(self, index, sub, uval):
        if index == S.INDEX_COMMANDS:
            if sub == S.CMD_DEFAULTS:
                for e in self.model.values():
                    if e.get("isparam"):
                        e["value"] = e.get("default", e["value"])
            elif sub == S.CMD_START:
                self.model["opmode"]["value"] = 1
            elif sub == S.CMD_STOP:
                self.model["opmode"]["value"] = 0
            elif sub == S.CMD_CLEAR_MAP:
                self.tx_maps = []
                self.rx_maps = []
            elif sub == S.CMD_RESET:
                self._write_reply(index, sub)
                threading.Timer(0.4, self._enter_bootloader).start()
                return
            return self._write_reply(index, sub)

        if (index >> 8) == (S.INDEX_PARAM_UID >> 8):
            pid = ((index & 0xFF) << 8) | sub
            name = self.by_id.get(pid)
            if name is None:
                return self._abort(index, sub, ABORT_NO_OBJECT)
            entry = self.model[name]
            if not entry.get("isparam"):
                return self._abort(index, sub, ABORT_READONLY)
            sval = struct.unpack("<i", struct.pack("<I", uval))[0]
            value = sval / 32.0
            if value < entry["minimum"] or value > entry["maximum"]:
                return self._abort(index, sub, S.SDO_ERR_RANGE)
            entry["value"] = value
            return self._write_reply(index, sub)

        if index in (S.INDEX_MAP_TX, S.INDEX_MAP_RX):
            maps = self.tx_maps if index == S.INDEX_MAP_TX else self.rx_maps
            pend = self._pending.setdefault(index, {})
            if sub == 0:
                pend.clear()
                pend["cobid"] = uval
            elif sub == 1:
                pend["sub1"] = uval
            elif sub == 2 and "cobid" in pend and "sub1" in pend:
                for entry in maps:
                    if entry["cobid"] == pend["cobid"]:
                        break
                else:
                    entry = {"cobid": pend["cobid"], "items": []}
                    maps.append(entry)
                entry["items"].append((pend["sub1"], uval))
                pend.clear()
            return self._write_reply(index, sub)

        # remove a mapping item: write 0 to the read-index entry
        for base, maps in ((S.INDEX_MAP_RD, self.tx_maps), (S.INDEX_MAP_RD_RX, self.rx_maps)):
            if base <= index < base + 0x80:
                mi = index - base
                item = (sub - 1) // 2
                if mi < len(maps) and item < len(maps[mi]["items"]):
                    del maps[mi]["items"][item]
                    if not maps[mi]["items"]:
                        del maps[mi]
                    return self._write_reply(index, sub)
                return self._abort(index, sub, ABORT_NO_SUBINDEX)

        return self._abort(index, sub, ABORT_NO_OBJECT)

    def _handle_segment(self, cmd):
        seg = self._seg
        if seg is None:
            return
        expected = S.SDO_TOGGLE_BIT if seg["toggle"] else 0
        if (cmd & S.SDO_TOGGLE_BIT) != expected:
            self._seg = None
            return self._abort(0, 0, 0x05030000)  # toggle bit error
        data = seg["data"]
        pos = seg["pos"]
        n = min(7, len(data) - pos)
        last = pos + n >= len(data)
        rcmd = expected | ((7 - n) << 1) | (S.SDO_SIZE_SPECIFIED if last else 0)
        self._sdo(bytes([rcmd]) + data[pos:pos + n].ljust(7, b"\x00"))
        seg["pos"] = pos + n
        seg["toggle"] = not seg["toggle"]
        if last:
            self._seg = None

    # ---- CAN bootloader ----

    def _enter_bootloader(self):
        if not self.running:
            return
        self.boot = {"stage": "id", "pages": 0, "page_buf": bytearray(),
                     "collected": [], "awaiting_crc": False}
        # magic, version 1, two zero bytes, 4-byte device id
        self._send(S.BOOTLOADER_RESP_ID,
                   bytes([0x33, 1, 0, 0]) + struct.pack("<I", self.serial))

    def _handle_boot(self, d):
        b = self.boot
        if b["stage"] == "id":
            if d[:4] == struct.pack("<I", self.serial):
                b["stage"] = "size"
                self._send(S.BOOTLOADER_RESP_ID, b"S")
            return
        if b["stage"] == "size":
            b["pages"] = d[0]
            b["stage"] = "data"
            self._send(S.BOOTLOADER_RESP_ID, b"P")
            return
        if b["stage"] != "data":
            return
        if b["awaiting_crc"]:
            host_crc = struct.unpack("<I", d[:4])[0]
            crc = 0xFFFFFFFF
            for off in range(0, PAGE_SIZE, 8):
                crc = crc32_chunk(crc, bytes(b["page_buf"][off:off + 8]))
            if host_crc != crc or self.fail_crc_once:
                self.fail_crc_once = False
                b["page_buf"] = bytearray()
                b["awaiting_crc"] = False
                self._send(S.BOOTLOADER_RESP_ID, b"E")
                self._send(S.BOOTLOADER_RESP_ID, b"P")  # re-request the page
                return
            b["collected"].append(bytes(b["page_buf"]))
            b["page_buf"] = bytearray()
            b["awaiting_crc"] = False
            if len(b["collected"]) >= b["pages"]:
                self.flashed = b"".join(b["collected"])
                self.boot = None  # "reboot" into the application
                self._send(S.BOOTLOADER_RESP_ID, b"D")
            else:
                self._send(S.BOOTLOADER_RESP_ID, b"P")
            return
        # 8-byte data chunk; request the next with 'P', or the CRC with 'C'
        # once the page is complete
        b["page_buf"] += d[:8]
        if len(b["page_buf"]) >= PAGE_SIZE:
            b["awaiting_crc"] = True
            self._send(S.BOOTLOADER_RESP_ID, b"C")
        else:
            self._send(S.BOOTLOADER_RESP_ID, b"P")

    # ---- background dynamics ----

    def _tick_loop(self):
        t0 = time.monotonic()
        counter = 0
        while self.running:
            time.sleep(0.1)
            t = time.monotonic() - t0
            m = self.model
            m["udc"]["value"] = round(398.5 + 6 * math.sin(t / 3.0), 2)
            m["tmphs"]["value"] = round(31.2 + 2 * math.sin(t / 7.0), 2)
            if m["opmode"]["value"] == 1:
                m["speed"]["value"] = round(1500 + 500 * math.sin(t), 0)
                m["pot"]["value"] = round(2000 + 800 * math.sin(t / 2.0), 0)
            else:
                m["speed"]["value"] = 0
                m["pot"]["value"] = 0
            counter = (counter + 1) % 5
            if counter == 0:
                # demo traffic for virtual spot values: uint16 counter at bit 0,
                # int8 sine at bit 16 of frame 0x123
                c = int(t * 10) & 0xFFFF
                s8 = int(100 * math.sin(t)) & 0xFF
                self._send(0x123, bytes([c & 0xFF, c >> 8, s8, 0, 0, 0, 0, 0]))
