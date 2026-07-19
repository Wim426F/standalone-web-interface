"""/cmd text dispatcher: OpenInverter terminal commands → CAN SDO.

Parity port of the firmware's canExecuteCommand. Reply strings must match
exactly — the frontend classifies failures with regexes
(/out of range|unknown param|set failed|error/i for 'set',
result.indexOf('error') === 0 for mapping commands).
"""

import re

from . import sdo as S

_NUM = re.compile(r"\s*[-+]?(?:0[xX][0-9a-fA-F]+|[0-9]*\.?[0-9]+)")


def _to_float(s):
    """Arduino String.toFloat(): parse the leading number, 0.0 on garbage."""
    m = re.match(r"\s*[-+]?[0-9]*\.?[0-9]+", s)
    return float(m.group(0)) if m else 0.0


def _to_int(s):
    m = re.match(r"\s*[-+]?[0-9]+", s)
    return int(m.group(0)) if m else 0


def _strtoul0(s):
    """strtoul(..., base 0): decimal by default, hex with 0x prefix."""
    m = _NUM.match(s)
    if not m:
        return 0
    try:
        return int(m.group(0).strip(), 0)
    except ValueError:
        return 0


def _sign8(v):
    return v - 256 if v & 0x80 else v


def execute(dev, cmd):
    cmd = cmd[:1024]

    # The firmware update owns the bus — any SDO traffic would eat
    # bootloader frames and kill the transfer
    if dev.flasher is not None and dev.flasher.busy():
        if cmd == "json":
            return '{"status":{"value":0,"isparam":false,"unit":""}}'
        return "update in progress"

    # Auto-download the parameter cache on first use
    if not dev.cache_loaded:
        dev.ensure_param_db()

    if cmd == "json":
        if dev.cache_loaded:
            return dev.build_json_with_values()
        # No cache — minimal response without the can_cache flag, so the UI
        # shows the device as disconnected
        return ('{"status":{"value":0,"isparam":false,"unit":""},'
                '"opmode":{"value":0,"isparam":false,"unit":""}}')

    if cmd.startswith("get "):
        vals = []
        for name in cmd[4:].split(","):
            name = name.strip()
            if not name:
                continue
            if len(vals) >= 128:
                break
            vals.append(dev.format_value(name))
        return "\t".join(vals)

    if cmd.startswith("set "):
        rest = cmd[4:]
        sp = rest.find(" ")
        if sp <= 0:
            return "error"
        name = rest[:sp]
        val = _to_float(rest[sp + 1:])
        pid = dev.param_id(name)
        if pid is None:
            return "Unknown parameter"
        err = dev.sdo.write(S.param_index(pid), S.param_sub(pid), S.encode_value(val))
        if err == 0:
            return "Set OK"
        if err == S.SDO_ERR_RANGE:
            return "Value out of range"
        return "Set failed"

    if cmd in ("start 2", "start"):
        dev.sdo.command(S.CMD_START)
        return "started"
    if cmd == "stop":
        dev.sdo.command(S.CMD_STOP)
        return "stopped"
    if cmd == "reset":
        dev.sdo.reset_device()
        return "reset sent"
    if cmd == "save":
        dev.sdo.command(S.CMD_SAVE)
        return "saved"
    if cmd == "load":
        dev.sdo.command(S.CMD_LOAD)
        return "loaded"
    if cmd == "defaults":
        # The ESP32 CAN dispatcher never mapped this even though the UI sends
        # it; command 3 is the device's restore-defaults
        dev.sdo.command(S.CMD_DEFAULTS)
        return "defaults restored"
    if cmd == "errors":
        return dev.read_errors()
    if cmd == "fastuart":
        return "ok"
    if cmd.startswith("can "):
        return _can_mapping(dev, cmd[4:].strip())

    return "unknown command"


def _can_mapping(dev, rest):
    if rest == "clear":
        if dev.sdo.write(S.INDEX_COMMANDS, S.CMD_CLEAR_MAP, 0) == 0:
            return "cleared"
        return "error: clear failed"

    if rest == "list" or rest.startswith("list"):
        # Walk the read indices: each message holds sub0 = COB ID, then item
        # pairs (paramid/pos/len, gain/offset) until the device aborts
        parts = []
        for direction in (0, 1):
            index = S.INDEX_MAP_RD_RX if direction else S.INDEX_MAP_RD
            while True:
                cobid = dev.sdo.read_entry(index, 0)
                if cobid is None:
                    break  # no more messages
                sub = 1
                got_item = False
                while sub < 100:
                    pos_len = dev.sdo.read_entry(index, sub)
                    if pos_len is None:
                        break  # no more items
                    gain_ofs = dev.sdo.read_entry(index, sub + 1)
                    if gain_ofs is None:
                        break
                    u1 = pos_len & 0xFFFFFFFF
                    u2 = gain_ofs & 0xFFFFFFFF
                    paramid = u1 & 0xFFFF
                    pos = (u1 >> 16) & 0xFF
                    length = _sign8((u1 >> 24) & 0xFF)
                    # gain: signed 24-bit fixed point (x1000); offset: signed byte
                    g24 = u2 & 0xFFFFFF
                    if g24 & 0x800000:
                        g24 -= 1 << 24
                    gain = g24 / 1000.0
                    offset = _sign8((u2 >> 24) & 0xFF)
                    parts.append(
                        '{"isrx":%s,"id":%d,"paramid":%d,"position":%d,"length":%d,'
                        '"gain":%.3f,"offset":%d,"index":%d,"subindex":%d}'
                        % ("true" if direction else "false", cobid, paramid, pos,
                           length, gain, offset, index, sub)
                    )
                    got_item = True
                    sub += 2
                if not got_item:
                    break  # empty message — stop walking this direction
                index += 1
        return "[" + ",".join(parts) + "]"

    if rest.startswith("rm "):
        args = rest[3:]
        sp = args.find(" ")
        if sp < 0:
            return "error: usage: can rm index subindex"
        index = _to_int(args[:sp])
        sub = _to_int(args[sp + 1:]) & 0xFF
        if dev.sdo.write(index, sub, 0) == 0:
            return "ok"
        return "error: remove failed"

    # can tx/rx name canid pos bits gain [offset]
    sp1 = rest.find(" ")
    if sp1 < 0:
        return "error: usage: can tx/rx name canid pos bits gain"
    typ = rest[:sp1]
    if typ not in ("tx", "rx"):
        return "error: usage: can tx/rx name canid pos bits gain"

    args = rest[sp1 + 1:]
    sp2 = args.find(" ")
    if sp2 < 0:
        return "error: missing arguments"
    name = args[:sp2]

    args = args[sp2 + 1:]
    sp3 = args.find(" ")
    if sp3 < 0:
        return "error: missing canid"
    can_id = _strtoul0(args[:sp3])

    args = args[sp3 + 1:]
    sp4 = args.find(" ")
    if sp4 < 0:
        return "error: missing pos"
    pos = _to_int(args[:sp4])

    args = args[sp4 + 1:]
    sp5 = args.find(" ")
    bits = _to_int(args[:sp5] if sp5 >= 0 else args)
    gain = 1.0
    offset = 0
    if sp5 >= 0:
        args = args[sp5 + 1:]
        sp6 = args.find(" ")
        gain = _to_float(args[:sp6] if sp6 >= 0 else args)
        if sp6 >= 0:
            offset = _to_int(args[sp6 + 1:])

    pid = dev.param_id(name)
    if pid is None:
        return "error: unknown parameter"

    # Three-stage add: COB ID, then param/pos/len, then gain/offset
    map_index = S.INDEX_MAP_TX if typ == "tx" else S.INDEX_MAP_RX
    if dev.sdo.write(map_index, 0, can_id) != 0:
        return "error: COB ID rejected"
    if dev.sdo.write(map_index, 1,
                     (pid & 0xFFFF) | ((pos & 0xFF) << 16) | ((bits & 0xFF) << 24)) != 0:
        return "error: position/length rejected"
    if dev.sdo.write(map_index, 2,
                     (int(gain * 1000) & 0xFFFFFF) | ((offset & 0xFF) << 24)) != 0:
        return "error: gain/offset rejected"
    return "ok"
