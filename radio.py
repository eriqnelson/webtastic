from dotenv import load_dotenv; load_dotenv()
import os
# Use the correct Meshtastic interface classes for each transport
from meshtastic.serial_interface import SerialInterface
from meshtastic.tcp_interface import TCPInterface
try:
    from meshtastic.ble_interface import BLEInterface
except Exception:
    BLEInterface = None
from pubsub import pub
import time
import subprocess
import json
import re

DEFAULT_CHANNEL_INDEX = int(os.getenv("DEFAULT_CHANNEL_INDEX", 1))

def _build_cli(ble=None, host=None, port=None):
    cmd = ["meshtastic"]
    if ble:
        cmd += ["--ble", ble]
    if host:
        cmd += ["--host", host]
    if port:
        cmd += ["--port", port]
    return cmd

def apply_url_config(url: str, ble=None, host=None, port=None):
    """Apply a Complete URL to the radio. This replaces channel config deterministically."""
    cmd = _build_cli(ble, host, port) + ["--seturl", url]
    subprocess.run(cmd, capture_output=True, text=True)

def get_channel_index_by_name(name: str, ble=None, host=None, port=None):
    """Return the channel index whose JSON name matches `name`, or None if not found."""
    cmd = _build_cli(ble, host, port) + ["--info"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        # Match a line like: Index 2: SECONDARY ... { "psk": "...", "name": "webtastic", ... }
        m = re.search(r"Index\s+(\d+):.*\{(.*)\}", line)
        if not m:
            continue
        idx = int(m.group(1))
        try:
            obj = json.loads("{" + m.group(2) + "}")
        except Exception:
            continue
        if obj.get("name", "") == name:
            return idx
    return None

def get_radio_interface():
    """
    Create a Meshtastic Interface using connection info from .env (BLE, host, or devPath).
    Priority: BLE > HOST > DEVPATH > auto-detect
    """
    ble = os.getenv("MESHTASTIC_BLE")
    host = os.getenv("MESHTASTIC_HOST")
    devpath = os.getenv("MESHTASTIC_PORT")

    # BLE connection (requires extras installed)
    if ble:
        if BLEInterface is None:
            raise RuntimeError("BLEInterface not available. Install meshtastic[ble] or remove MESHTASTIC_BLE from .env.")
        return BLEInterface(ble)

    # TCP/host connection
    if host:
        return TCPInterface(host)

    # Serial device path connection
    if devpath:
        return SerialInterface(devPath=devpath)

    # Auto-detect (serial)
    return SerialInterface()

class RadioInterface:
    @staticmethod
    def read_channel_config(index=DEFAULT_CHANNEL_INDEX):
        """Read the channel config for the given index using meshtastic --info and return a dict with name and psk (or None if not found)."""
        import subprocess
        cmd = ["meshtastic", "--info"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f"Index {index}:" in line:
                # Example line: Index 1: SECONDARY psk=secret { "psk": "...", "name": "...", ... }
                import re, json
                m = re.search(r'\{(.+)\}', line)
                if m:
                    try:
                        # Convert to valid JSON
                        d = json.loads('{' + m.group(1) + '}')
                        return {"name": d.get("name", ""), "psk": d.get("psk", "")}
                    except Exception:
                        pass
                return None
        return None

    @staticmethod
    def write_channel_config(name, psk, index=DEFAULT_CHANNEL_INDEX, ble=None, host=None, port=None):
        """Update channel name/psk at `index` in-place. Avoid --ch-del to prevent sparse indices.
        If the channel doesn't exist, add it and then set the PSK without --ch-index.
        """
        cmd = _build_cli(ble, host, port)
        # First, check if the index exists
        info = subprocess.run(cmd + ["--info"], capture_output=True, text=True)
        exists = any(f"Index {index}:" in ln for ln in info.stdout.splitlines())
        if index == 0 or exists:
            subprocess.run(cmd + ["--ch-set", "name", name, "--ch-index", str(index), "--channel-fetch-attempts", "5"], capture_output=True, text=True)
            subprocess.run(cmd + ["--ch-set", "psk", psk, "--ch-index", str(index), "--channel-fetch-attempts", "5"], capture_output=True, text=True)
            return
        # If it doesn't exist, add then set PSK without index (targets newly added channel)
        subprocess.run(cmd + ["--ch-add", name], capture_output=True, text=True)
        subprocess.run(cmd + ["--ch-set", "psk", psk], capture_output=True, text=True)

    def __init__(self):
        self.iface = get_radio_interface()
        self._subscribed = False
        self.default_channel_index = DEFAULT_CHANNEL_INDEX

    def send(self, message: str, channel_index: int = None):
        """Send a message to the specified Meshtastic channel."""
        idx = self.default_channel_index if channel_index is None else channel_index
        self.iface.sendText(message, channelIndex=idx)

    def on_receive(self, callback):
        """Register a callback to handle incoming messages."""
        if not self._subscribed:
            pub.subscribe(lambda packet, iface: callback(packet), "meshtastic.receive")
            self._subscribed = True

    def run_forever(self):
        """Keep the radio interface alive to receive messages."""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self):
        """Clean up the serial connection."""
        self.iface.close()

def configure_channel(index=DEFAULT_CHANNEL_INDEX):
    """Set up a Meshtastic channel using environment variables and connection type.
    Uses the Meshtastic CLI to read/write channel config before opening a Python interface
    to avoid serial-port lock conflicts.
    """
    name = os.getenv("MINIHTTP_CHANNEL_NAME", "minihttp")
    psk = os.getenv("MINIHTTP_CHANNEL_PSK", "mistynight42")
    ble = os.getenv("MESHTASTIC_BLE")
    host = os.getenv("MESHTASTIC_HOST")
    port = os.getenv("MESHTASTIC_PORT")

    # If a Complete URL is provided, apply it deterministically before opening the interface
    seturl = os.getenv("MESHTASTIC_SETURL") or os.getenv("MESHTASTIC_CONFIG_URL")
    if seturl:
        apply_url_config(seturl, ble=ble, host=host, port=port)

    # Read current config BEFORE opening any interface (prevents port busy issues)
    current = RadioInterface.read_channel_config(index=index)

    # Only write if needed
    if (not current) or (current.get("name") != name) or (current.get("psk") != psk):
        RadioInterface.write_channel_config(name, psk, index=index, ble=ble, host=host, port=port)

    # Optionally return an open interface for immediate use
    radio = RadioInterface()
    # Try to discover the desired channel index by name; fall back to provided index
    try:
        resolved = get_channel_index_by_name(name, ble=ble, host=host, port=port)
        radio.default_channel_index = resolved if resolved is not None else index
    except Exception:
        radio.default_channel_index = index
    return radio