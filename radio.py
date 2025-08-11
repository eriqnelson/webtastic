

import meshtastic
import meshtastic.serial_interface as serial_interface
from pubsub import pub
from dotenv import load_dotenv; load_dotenv()
import time
import os

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
    name = os.getenv("MINIHTTP_CHANNEL_NAME", "webtastic")
    psk = os.getenv("MINIHTTP_CHANNEL_PSK", "0x8e2a4b7c5d1e3f6a9b0c2d4e6f8a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d9e1f2a")
    ble = os.getenv("MESHTASTIC_BLE")
    host = os.getenv("MESHTASTIC_HOST")
    port = os.getenv("MESHTASTIC_PORT")
    import subprocess
    cmd = ["meshtastic"]
    if ble:
        cmd += ["--ble", ble]
    elif host:
        cmd += ["--host", host]
    elif port:
        cmd += ["--port", port]
    import sys
    # Set channel name
    cmd_name = cmd + ["--ch-set", "name", name, "--ch-index", str(index)]
    print(f"[DEBUG] Running command for name: {' '.join(cmd_name)}", file=sys.stderr)
    subprocess.run(cmd_name, capture_output=True)
    # Set channel PSK
    cmd_psk = cmd + ["--ch-set", "psk", psk, "--ch-index", str(index)]
    print(f"[DEBUG] Running command for psk: {' '.join(cmd_psk)}", file=sys.stderr)
    result = subprocess.run(cmd_psk, capture_output=True, text=True)
    print(f"[DEBUG] PSK command stdout: {result.stdout}", file=sys.stderr)
    print(f"[DEBUG] PSK command stderr: {result.stderr}", file=sys.stderr)