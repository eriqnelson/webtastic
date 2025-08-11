

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
    name = os.getenv("MINIHTTP_CHANNEL_NAME", "minihttp")
    psk = os.getenv("MINIHTTP_CHANNEL_PSK", "mistynight42")
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
    cmd += ["--ch-set", f"name={name}", f"psk={psk}", "--ch-index", str(index)]
    subprocess.run(cmd)