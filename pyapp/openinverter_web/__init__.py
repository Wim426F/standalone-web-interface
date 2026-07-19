"""OpenInverter web interface served from a PC over CAN (python-can).

The computer takes the ESP32's place: this package serves the same data/
frontend and speaks the same CANopen SDO protocol to the inverter/VCU,
through any CAN adapter python-can supports (socketcan, slcan, gs_usb,
PCAN, Kvaser, Vector, ...).
"""

__version__ = "0.1.0"
