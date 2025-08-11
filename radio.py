from dotenv import load_dotenv; load_dotenv()
import os
import meshtastic
import meshtastic.serial_interface as serial_interface
from pubsub import pub
import time

DEFAULT_CHANNEL_INDEX = int(os.getenv("DEFAULT_CHANNEL_INDEX", 1))

def get_radio_interface():
    """
    Create a SerialInterface using connection info from .env (BLE, host, or devPath).
    Priority: BLE > HOST > DEVPATH > auto-detect
    """
    ble = os.getenv("MESHTASTIC_BLE")
    host = os.getenv("MESHTASTIC_HOST")
    devpath = os.getenv("MESHTASTIC_PORT")
    # BLE connection
    if ble:
        iface = serial_interface.SerialInterface(ble=ble)
    # TCP/host connection
    elif host:
        iface = serial_interface.SerialInterface(host=host)
    # Serial device path connection
    elif devpath:
        iface = serial_interface.SerialInterface(devPath=devpath)
    # Auto-detect
    else:
        iface = serial_interface.SerialInterface()
    return iface

class RadioInterface:
    def read_channel_config(self, index=DEFAULT_CHANNEL_INDEX):
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

    def write_channel_config(self, name, psk, index=DEFAULT_CHANNEL_INDEX, ble=None, host=None, port=None):
        """Delete and overwrite the channel at the given index with the provided name and PSK."""
        import subprocess
        cmd = ["meshtastic"]
        if ble:
            cmd += ["--ble", ble]
        if host:
            cmd += ["--host", host]
        if port:
            cmd += ["--port", port]
        # Delete channel
        del_cmd = cmd + ["--ch-index", str(index), "--ch-del"]
        subprocess.run(del_cmd, capture_output=True, text=True)
        # Set name
        cmd_name = cmd + ["--ch-set", "name", name, "--ch-index", str(index)]
        subprocess.run(cmd_name, capture_output=True, text=True)
        # Set PSK
        cmd_psk = cmd + ["--ch-set", "psk", psk, "--ch-index", str(index)]
        subprocess.run(cmd_psk, capture_output=True, text=True)
    def __init__(self):
        self.iface = get_radio_interface()
        self._subscribed = False

    def send(self, message: str, channel_index: int = DEFAULT_CHANNEL_INDEX):
        """Send a message to the specified Meshtastic channel."""
        self.iface.sendText(message, channelIndex=channel_index)

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
    """Set up a Meshtastic channel using environment variables and connection type."""
    name = os.getenv("MINIHTTP_CHANNEL_NAME", "minihttp")
    psk = os.getenv("MINIHTTP_CHANNEL_PSK", "mistynight42")
    ble = os.getenv("MESHTASTIC_BLE")
    host = os.getenv("MESHTASTIC_HOST")
    port = os.getenv("MESHTASTIC_PORT")
    radio = RadioInterface()
    # Read current config
    current = radio.read_channel_config(index=index)
    # Only write if needed
    if not current or current.get("name") != name or current.get("psk") != psk:
        radio.write_channel_config(name, psk, index=index, ble=ble, host=host, port=port)