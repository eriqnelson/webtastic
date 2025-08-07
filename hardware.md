# MiniHTTP Hardware and Environment Specification

## Standard Hardware Node

**Device:** [LILYGO T-Echo (ESP32-S3 + LoRa + GPS)](https://github.com/Xinyuan-LilyGO/T-Echo)  
- **LoRa module:** SX1262  
- **Processor:** ESP32-S3  
- **Power:** USB-C (5V) or battery  
- **Firmware:** [Meshtastic](https://meshtastic.org/download) latest release

## Host System (Linux Peer)

**Requirements:**
- Any Linux distribution with:
  - USB support for CDC-ACM or USB-Serial (e.g. `/dev/ttyACM0`)
  - Python 3.9+
  - pip / venv

**Role:** Acts as a MiniHTTP client or server via USB-connected T-Echo

**Communication:** JSON messages sent over Meshtastic `text` field via USB serial connection using the Meshtastic Python API.

## Baseline Software Stack

### On the ESP32 (T-Echo)
- **Firmware:** [Meshtastic firmware](https://meshtastic.org/download)
- **Channel Configuration:** Preconfigured channel name and PSK (e.g. `minihttp`)

### On the Linux Host
- **Python Environment Setup:**
  ```bash
  python3 -m venv ~/.venvs/mesh
  source ~/.venvs/mesh/bin/activate
  pip install --upgrade pip
  pip install meshtastic pyserial
  ```

- **Required Python Packages:**
  - `meshtastic`
  - `pyserial`
  - `pubsub` (installed automatically by `meshtastic`)

## Communication Flow

- **GET requests** are sent as single JSON messages over channel index 1
- **RESP messages** are split into 122-byte `data` fragments, each packaged into a fixed-size JSON envelope and sent as individual Meshtastic messages