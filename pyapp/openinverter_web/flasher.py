"""STM32 firmware update over the OpenInverter CAN bootloader.

Port of the firmware's canFwUpdateTask. The bootloader drives the transfer
over 0x7DE; the host answers on 0x7DD:

    0x33 magic after reset -> reflect device ID to enter update mode
    'S' -> send page count (1 byte)
    'P' -> send next 8 bytes of page data
    'C' -> send page CRC32 (STM32 hardware CRC); reply 'P' = ok, 'E' = CRC
           error (resend page), 'D' = done

The bootloader tolerates no gaps between frames, so the whole transfer runs
in one dedicated thread that holds the bus tx_lock from the reset SDO until
the end — every other handler checks flasher.busy() and refuses first, so
nothing ever blocks on that lock for the duration of a flash.

State values match the firmware so /fwupdate-status keeps its contract:
0 idle, 1 waiting for bootloader, 2 flashing, 3 done, 4 error.
"""

import threading
import time
from pathlib import Path

from . import sdo as S

PAGE_SIZE = 1024
IDLE, WAITBOOT, FLASHING, DONE, ERROR = 0, 1, 2, 3, 4


def crc32_word(crc, data):
    """One word of the STM32 hardware CRC (polynomial 0x04C11DB7)."""
    crc = (crc ^ data) & 0xFFFFFFFF
    for _ in range(32):
        if crc & 0x80000000:
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
        else:
            crc = (crc << 1) & 0xFFFFFFFF
    return crc


def crc32_chunk(crc, chunk8):
    """Fold one 8-byte chunk (two little-endian words) into the CRC."""
    crc = crc32_word(crc, int.from_bytes(chunk8[0:4], "little"))
    return crc32_word(crc, int.from_bytes(chunk8[4:8], "little"))


class Flasher:
    def __init__(self, bus, sdo_client):
        self.bus = bus
        self.sdo = sdo_client
        self.state = IDLE
        self.page = 0
        self.pages = 0
        self.message = ""
        self.on_done = None  # called after a successful flash (cache invalidation)
        self._thread = None

    def busy(self):
        return self.state in (WAITBOOT, FLASHING)

    def status(self):
        # Message only once the thread stopped writing it (firmware did the
        # same to avoid racing the string)
        msg = self.message if self.state in (DONE, ERROR) else ""
        return {
            "state": self.state,
            "page": self.page,
            "pages": self.pages,
            "message": msg.replace('"', "'"),
        }

    def start(self, path):
        """Kick off a flash. Returns (http_status, response_dict)."""
        if self.busy():
            return 409, {"message": "Update already in progress"}
        try:
            data = Path(path).read_bytes()
        except OSError:
            return 500, {"message": "Failed to open file"}
        if len(data) == 0:
            return 500, {"message": "Firmware file is empty"}
        if len(data) > 255 * 1024:  # the bootloader's page count is one byte
            return 500, {"message": "Firmware too large (max 255KB)"}
        self.pages = (len(data) + PAGE_SIZE - 1) // PAGE_SIZE
        self.page = 0
        self.message = ""
        self.state = WAITBOOT
        self._thread = threading.Thread(
            target=self._run, args=(data,), daemon=True, name="can-flash")
        self._thread.start()
        return 200, {"pages": self.pages, "message": "started"}

    def _fail(self, why):
        self.message = why
        self.state = ERROR

    def _send_chunk(self, data, pos, crc):
        chunk = bytes(data[pos:pos + 8]).ljust(8, b"\xff")
        crc = crc32_chunk(crc, chunk)
        # Retry transmit failures — a silently dropped chunk deadlocks the
        # transfer (bootloader waits for data, we wait for the next request)
        for _ in range(5):
            if self.bus.send(S.BOOTLOADER_CMD_ID, chunk):
                return crc, True
            time.sleep(0.005)
        return crc, False

    def _wait_boot(self, timeout):
        f = self.bus.wait_boot(timeout)
        if f is None:
            return None
        return bytes(f.data).ljust(8, b"\x00")

    def _run(self, data):
        with self.bus.tx_lock:  # own the bus for the whole flash
            self.bus.drain_boot()
            self.bus.drain_sdo()
            self.sdo.reset_device()

            # Catch the bootloader magic. The long window also covers manual
            # recovery: power-cycling a device with a dead application.
            magic = None
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                f = self._wait_boot(min(0.5, max(0.0, deadline - time.monotonic())))
                if f is not None and f[0] == 0x33:
                    magic = f
                    break
            if magic is None:
                return self._fail("No bootloader magic (reset or power-cycle the device)")

            # Reflect the device ID back to enter update mode
            dev_id = magic[4:8]
            old_bootloader = magic[1] < 1
            self.bus.send(S.BOOTLOADER_CMD_ID, dev_id)
            if old_bootloader:
                time.sleep(0.1)  # bootloader with timing quirk

            f = self._wait_boot(5.0)
            if f is None or f[0] != ord("S"):
                return self._fail("Bootloader did not request size")
            self.bus.send(S.BOOTLOADER_CMD_ID, bytes([self.pages & 0xFF]))

            f = self._wait_boot(15.0)
            if f is None or f[0] != ord("P"):
                return self._fail("Bootloader did not request data")

            self.state = FLASHING

            # Transfer all pages back-to-back — the pending 'P' has been
            # consumed, so each page starts by sending its first chunk
            for page in range(self.pages):
                self.page = page
                page_done = False

                for _attempt in range(3):
                    if page_done:
                        break
                    crc = 0xFFFFFFFF
                    pos = page * PAGE_SIZE
                    chunks = 0
                    crc_sent = False
                    fail = ""

                    crc, ok = self._send_chunk(data, pos, crc)
                    if not ok:
                        fail = "chunk TX failed"
                    pos += 8
                    chunks += 1

                    while not fail and not page_done:
                        f = self._wait_boot(10.0)
                        if f is None:
                            fail = ("no verdict after CRC" if crc_sent
                                    else "no request after chunk %d" % chunks)
                            break
                        c = f[0]
                        if not crc_sent and c == ord("P"):      # next chunk request
                            crc, ok = self._send_chunk(data, pos, crc)
                            if not ok:
                                fail = "chunk TX failed"
                                break
                            pos += 8
                            chunks += 1
                        elif not crc_sent and c == ord("C"):    # CRC request
                            if not self.bus.send(S.BOOTLOADER_CMD_ID, crc.to_bytes(4, "little")):
                                fail = "CRC TX failed"
                                break
                            crc_sent = True
                        elif crc_sent and c in (ord("P"), ord("D")):
                            page_done = True  # accepted ('P' doubles as next page's first request)
                        elif crc_sent and c == ord("E"):        # CRC error — resend the page
                            f = self._wait_boot(10.0)
                            if f is None or f[0] != ord("P"):
                                fail = "lost sync after CRC error"
                            break  # next attempt resends the page

                    if fail:
                        return self._fail("Page %d: %s" % (page, fail))

                if not page_done:
                    return self._fail("Page %d failed after retries" % page)

            self.page = self.pages
            self.message = "Update complete"
            self.state = DONE
        if self.on_done:
            try:
                self.on_done()
            except Exception:
                pass
