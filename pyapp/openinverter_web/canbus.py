"""python-can bus lifecycle: one bus, one RX pump, one transaction lock.

    HTTP handler threads          SSE thread        flasher thread
          \\               |               /
           +---- tx_lock (RLock): serialized SDO transactions ----+
                              |
                          CanBus.send()
                              |
    RX pump thread: bus.recv() loop, dispatches every frame:
        0x580-0x5FF  -> sdo_q   (drained before each SDO request)
        0x7DE        -> boot_q  (drained by the flasher)
        every frame  -> rx hook (virtual spot values) + debug capture

The pump also owns reconnection: when the adapter errors or disappears it
closes the bus and retries with the current settings every couple of seconds.
"""

import queue
import threading
import time

import can

from .sdo import SDO_RESPONSE_BASE, BOOTLOADER_RESP_ID

# Interfaces that configure their own bitrate outside python-can
_NO_BITRATE_ARG = ("socketcan", "virtual")


class CanBus:
    def __init__(self, get_config):
        self.get_config = get_config  # callable -> {"interface","channel","bitrate"} or None
        self.tx_lock = threading.RLock()
        self.state = "down"           # down | up | error
        self.status_msg = "No CAN adapter configured"
        self.sdo_q = queue.Queue()
        self.boot_q = queue.Queue()
        self._bus = None
        self._bus_lock = threading.Lock()
        self._rx_hook = None
        self._capture = None
        self._capture_t0 = 0.0
        self._reconnect = threading.Event()
        self._running = False
        self._thread = None

    # ---- lifecycle ----

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._pump, name="can-rx", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._close()
        if self._thread:
            self._thread.join(timeout=2)

    def reconfigure(self):
        """Tear down and reconnect with the current get_config() values."""
        self._reconnect.set()

    def connected(self):
        return self.state == "up"

    def set_rx_hook(self, fn):
        self._rx_hook = fn

    # ---- connection handling (pump thread) ----

    def _connect(self):
        cfg = self.get_config() or {}
        interface = cfg.get("interface") or ""
        if not interface:
            self.state = "down"
            self.status_msg = "No CAN adapter configured"
            return None
        channel = cfg.get("channel") or None
        if isinstance(channel, str) and channel.isdigit():
            channel = int(channel)  # kvaser/vector/gs_usb take numeric channels
        kwargs = {"interface": interface, "channel": channel}
        if cfg.get("bitrate") and interface not in _NO_BITRATE_ARG:
            kwargs["bitrate"] = int(cfg["bitrate"])
        try:
            bus = can.Bus(**kwargs)
        except Exception as e:  # adapter missing, driver error, bad channel...
            self.state = "error"
            self.status_msg = "%s: %s" % (type(e).__name__, e)
            text = str(e).lower()
            if "permissionerror" in text or "access is denied" in text or "errno 13" in text or "resource busy" in text:
                self.status_msg += (" — this port looks like it's already open by another "
                                    "program (maybe another copy of openinverter-web, or the "
                                    "adapter's own software). Close it and reconnect.")
            return None
        self.state = "up"
        self.status_msg = ""
        return bus

    def _close(self):
        with self._bus_lock:
            bus, self._bus = self._bus, None
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass

    def _pump(self):
        while self._running:
            if self._reconnect.is_set():
                self._reconnect.clear()
                self._close()
                self.state = "down"
            if self._bus is None:
                bus = self._connect()
                if bus is None:
                    time.sleep(2 if self.state == "error" else 1)
                    continue
                with self._bus_lock:
                    self._bus = bus
                self.drain_sdo()
                self.drain_boot()
            try:
                msg = self._bus.recv(0.2)
            except Exception as e:
                self.status_msg = "%s: %s" % (type(e).__name__, e)
                self.state = "error"
                self._close()
                time.sleep(2)
                continue
            if msg is None or msg.is_error_frame:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg):
        cap = self._capture
        if cap is not None and len(cap) < 40:
            cap.append((
                int((time.monotonic() - self._capture_t0) * 1000),
                msg.arbitration_id,
                bytes(msg.data),
            ))
        hook = self._rx_hook
        if hook is not None:
            try:
                hook(msg)
            except Exception:
                pass
        can_id = msg.arbitration_id
        if SDO_RESPONSE_BASE <= can_id <= (SDO_RESPONSE_BASE | 0x7F):
            self.sdo_q.put(msg)
        elif can_id == BOOTLOADER_RESP_ID:
            self.boot_q.put(msg)

    # ---- TX / RX API ----

    def send(self, can_id, data):
        with self._bus_lock:
            bus = self._bus
        if bus is None:
            return False
        msg = can.Message(
            arbitration_id=can_id,
            data=bytes(data)[:8],
            is_extended_id=can_id > 0x7FF,
        )
        try:
            bus.send(msg, timeout=0.1)
            return True
        except Exception as e:
            self.status_msg = "%s: %s" % (type(e).__name__, e)
            self.state = "error"
            self._reconnect.set()
            return False

    @staticmethod
    def _drain(q):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def drain_sdo(self):
        self._drain(self.sdo_q)

    def drain_boot(self):
        self._drain(self.boot_q)

    def wait_sdo(self, node_id, timeout):
        """Next SDO response for this node; unrelated responses are dropped
        (the firmware's canReceiveForNode drained them the same way)."""
        expected = SDO_RESPONSE_BASE | node_id
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                msg = self.sdo_q.get(timeout=remaining)
            except queue.Empty:
                return None
            if msg.arbitration_id == expected:
                return msg

    def wait_boot(self, timeout):
        """Next bootloader (0x7DE) frame, or None."""
        try:
            return self.boot_q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    # ---- debug capture (for /can-debug) ----

    def capture_start(self):
        self._capture_t0 = time.monotonic()
        self._capture = []

    def capture_stop(self):
        frames, self._capture = self._capture, None
        return frames or []
