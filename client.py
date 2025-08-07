import json
from listener import start_listener
import os

received_fragments = {}

def send_get_request(radio, path):
    """
    Sends a GET request for the given file path.
    """
    message = {
        "type": "GET",
        "path": path
    }
    radio.sendText(json.dumps(message))

def start_client(radio, path):
    """
    Starts the MiniHTTP client, listens for fragments, and reassembles the file.
    """

    def handle_response(message):
        if message.get("type") != "RESP":
            return
        if message.get("path") != path:
            return

        key = (message["path"], message["of_frag"])
        frag_list = received_fragments.setdefault(key, [None] * message["of_frag"])

        frag_index = message["frag"] - 1
        frag_list[frag_index] = message["data"]

        if all(frag_list):
            html = ''.join(frag_list)
            print(f"\nReceived complete file:\n\n{html}\n")

            # Ensure downloads directory exists
            os.makedirs("downloads", exist_ok=True)

            # Derive filename from path
            filename = os.path.basename(path)
            with open(os.path.join("downloads", filename), "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Saved to downloads/{filename}")

    start_listener(radio, handle_response)