import meshtastic
import meshtastic.serial_interface as serial_interface
from pubsub import pub
from dotenv import load_dotenv; load_dotenv()
import time
import os

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_CHANNEL_INDEX = int(os.getenv("DEFAULT_CHANNEL_INDEX", 1))

class RadioInterface:
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.iface = serial_interface.SerialInterface(self.port)
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

import os

def configure_channel(port=DEFAULT_PORT, index=DEFAULT_CHANNEL_INDEX):
    """Set up a Meshtastic channel using environment variables."""
    name = os.getenv("MINIHTTP_CHANNEL_NAME", "minihttp")
    psk = os.getenv("MINIHTTP_CHANNEL_PSK", "mistynight42")
    import subprocess
    subprocess.run([
        "meshtastic",
        "--port", port,
        "--ch-set", f"name={name}", f"psk={psk}", "--ch-index", str(index)
    ])