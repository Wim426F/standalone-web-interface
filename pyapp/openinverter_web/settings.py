"""Persistent app settings and the /settings JSON shape.

The GET shape keeps every key the ESP32 firmware sends (with stub values for
the hardware-specific ones) so the unchanged frontend keeps working, and adds
PC-only keys. `backend: "pc"` is the flag the frontend gates on; the ESP32
firmware never sends it.
"""

import json

from . import __version__

BITRATE_TO_SPEED = {125000: 0, 250000: 1, 500000: 2}
SPEED_TO_BITRATE = {0: 125000, 1: 250000, 2: 500000}

CAN_NODE_ID_MIN = 1
CAN_NODE_ID_MAX = 127


class Settings:
    def __init__(self, storage):
        self.storage = storage
        self.dev_name = ""
        self.can_node_id = 1
        self.can_interface = ""   # python-can interface name; "" = not configured
        self.can_channel = ""
        self.can_bitrate = 500000
        self.can_nodes = []       # [{nodeId, serial, name?, default?}, ...] managed by the UI
        self.load()

    def load(self):
        try:
            data = json.loads(self.storage.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.dev_name = str(data.get("dev_name", ""))[:32]
        self.can_node_id = self._clamp_node(data.get("can_node_id", 1))
        self.can_interface = str(data.get("can_interface", ""))
        self.can_channel = str(data.get("can_channel", ""))
        try:
            self.can_bitrate = int(data.get("can_bitrate", 500000))
        except (TypeError, ValueError):
            self.can_bitrate = 500000
        nodes = data.get("can_nodes", [])
        self.can_nodes = nodes if isinstance(nodes, list) else []

    def reset_device(self):
        """Factory-reset the connection settings. Layout files stay."""
        self.dev_name = ""
        self.can_node_id = 1
        self.can_interface = ""
        self.can_channel = ""
        self.can_bitrate = 500000
        self.can_nodes = []
        self.save()

    def save(self):
        data = {
            "dev_name": self.dev_name,
            "can_node_id": self.can_node_id,
            "can_interface": self.can_interface,
            "can_channel": self.can_channel,
            "can_bitrate": self.can_bitrate,
            "can_nodes": self.can_nodes,
        }
        self.storage.settings_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _clamp_node(v):
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 1
        return max(CAN_NODE_ID_MIN, min(CAN_NODE_ID_MAX, v))

    def public(self, http_port, connected, status_msg):
        """The /settings GET response: ESP-compatible keys + PC additions."""
        return {
            "dev_name": self.dev_name,
            "txrx_swapped": False,
            "uart_rx_pin": -1,
            "uart_tx_pin": -1,
            "ap_fallback": False,
            "can_mode": True,
            "can_node_id": self.can_node_id,
            "can_speed": BITRATE_TO_SPEED.get(self.can_bitrate, 2),
            "can_rx_pin": -1,
            "can_tx_pin": -1,
            "can_pwr_pin": -1,
            "can_pwr_inv": False,
            "can_en_pin": -1,
            "can_en_inv": False,
            "sd_available": False,
            "sd_enabled": False,
            "sd_card": False,
            "arch": "pc",
            "sse_port": http_port,
            "can_nodes": self.can_nodes,
            "backend": "pc",
            "backend_version": __version__,
            "can_interface": self.can_interface,
            "can_channel": self.can_channel,
            "can_bitrate": self.can_bitrate,
            "can_connected": connected,
            "can_status": status_msg,
            "data_dir": str(self.storage.data_dir),
        }
