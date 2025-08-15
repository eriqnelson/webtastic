

import json
from pubsub import pub

def start_listener(radio, callback):
    """
    Listens for incoming Meshtastic messages and routes them to the callback.
    """
    def _on_receive(packet, interface):
        try:
            text = packet.get("decoded", {}).get("text")
            if text:
                message = json.loads(text)
                callback(message)
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Failed to parse message: {e}")

    pub.subscribe(_on_receive, "meshtastic.receive")