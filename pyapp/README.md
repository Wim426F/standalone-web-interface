# openinverter-web — the web interface without the ESP32

Runs the OpenInverter web interface **directly on your PC** and talks to the
inverter/VCU (stm32-sine, stm32-foc, ZombieVerter, ...) over a USB CAN
adapter via [python-can](https://python-can.readthedocs.io/). Your computer
takes the ESP32's place: same frontend, same features, no WiFi involved.

It serves the exact same `data/` frontend as the ESP32 firmware in this repo
(single source of truth) and speaks the same CANopen SDO protocol on the bus,
so a device that works with the ESP32 module in CAN mode works with this too.

Works on **Windows and Linux**, Python 3.9+.

## Install

From a repo checkout:

```
pip install ./pyapp
```

For candleLight/CANable adapters in gs_usb mode:

```
pip install "./pyapp[gs_usb]"
```

## Run

```
openinverter-web
```

This starts the server (default `http://127.0.0.1:8998/`, auto-picking
another port if Windows has that one reserved) and opens your browser.
On first run, pick your CAN adapter under **Settings → Data Interface**
(adapter type, channel, bitrate), press **Save**, then **Scan for devices**.

Everything can also be set on the command line (persisted for next time):

```
openinverter-web --interface slcan --channel COM5 --bitrate 500000
openinverter-web --interface socketcan --channel can0
openinverter-web --interface gs_usb --bitrate 500000
```

No hardware handy? A full simulated inverter (including the CAN bootloader)
is built in:

```
openinverter-web --simulate
```

Other flags: `--host 0.0.0.0` (expose on the LAN), `--port`, `--node`,
`--data-dir`, `--no-browser`.

## Supported adapters

Anything python-can supports. The common ones:

| Adapter | `--interface` | `--channel` | Notes |
|---|---|---|---|
| SocketCAN (Linux) | `socketcan` | `can0` | `sudo ip link set can0 up type can bitrate 500000` first; bitrate is set there, not here |
| SLCAN (Lawicel, CANable in slcan mode) | `slcan` | `COM5` / `/dev/ttyACM0` | pyserial is installed automatically |
| candleLight / CANable (gs_usb mode) | `gs_usb` | *(empty)* | `pip install "openinverter-web[gs_usb]"`; on Windows install a WinUSB driver with [Zadig](https://zadig.akeo.ie/) |
| PCAN | `pcan` | `PCAN_USBBUS1` | Peak drivers required |
| Kvaser | `kvaser` | `0` | Kvaser CANlib required |
| Vector | `vector` | `0` | Vector drivers required |

## What works

Everything the ESP32 web interface does in CAN mode:

- Parameter database download (segmented SDO, cached on disk per device
  serial so restarts are instant), live spot values, parameter editing with
  save/load/defaults, error log
- Dashboard, Plot, Gauges (with the ~10 Hz SSE push stream), Data Logger
- CAN message mapping (add/list/remove/clear) and host-side **virtual spot
  values** decoded from arbitrary bus frames
- Node scan (1–127) and multi-device switching
- **STM32 firmware updates over the CAN bootloader** (Update tab — upload a
  `.bin`, or fetch stm32-sine releases from GitHub)
- Favourites, gauge layouts, presets, UI preferences — stored in a per-user
  data directory (shown in Settings) instead of SPIFFS

ESP32-only things (WiFi setup, ESP OTA, GPIO pins) hide themselves in the UI
when this backend is serving it.

## Development

```sh
cd pyapp
python -m venv .venv
.venv/bin/pip install -e .[dev]      # Linux/macOS
.venv/Scripts/pip install -e .[dev]  # Windows

.venv/bin/python -m pytest tests     # Linux/macOS
.venv/Scripts/python -m pytest tests # Windows
```

The test suite runs the whole stack — HTTP server, SDO client, flasher —
against a simulated inverter on a python-can `virtual` bus, with no
hardware needed.

Protocol references: `src/can_protocol.cpp` and `esp32-web-interface.cpp`
in the repo root, and `PROTOCOL.md`.

## Relationship to other projects

This package reproduces the CAN protocol implemented in this repo's ESP32
firmware (`src/can_protocol.cpp`, `esp32-web-interface.cpp`) and serves the
same `data/` frontend, so any device that works with the ESP32 module in CAN
mode works here too.

[openinverter-can-tool](https://github.com/davefiddes/openinverter-can-tool)
proved the same protocol from Python as a CLI; this project provides the
full web UI instead.
