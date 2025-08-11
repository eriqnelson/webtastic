import json
from listener import start_listener
import os

received_fragments = {}

def send_get_request(radio, path, frag=None):
    """
    Sends a GET request for the given file path, optionally for a specific fragment.
    """
    message = {
        "type": "GET",
        "path": path
    }
    if frag is not None:
        message["frag"] = frag
    radio.sendText(json.dumps(message))


import threading
import time

def start_client(radio, path, timeout=5):
    """
    Starts the MiniHTTP client, listens for fragments, reassembles the file, and re-requests missing fragments.
    """
    complete = threading.Event()
    missing_fragments = set()

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
            complete.set()

    start_listener(radio, handle_response)

    # Initial request
    send_get_request(radio, path)

    # Wait for fragments, then check for missing
    start = time.time()
    key = None
    while not complete.is_set():
        time.sleep(0.5)
        # After timeout, check for missing fragments
        if key is None and received_fragments:
            key = next(iter(received_fragments))
        if key:
            frag_list = received_fragments[key]
            missing = [i+1 for i, frag in enumerate(frag_list) if frag is None]
            if missing and (time.time() - start) > timeout:
                print(f"Re-requesting missing fragments: {missing}")
                for frag in missing:
                    send_get_request(radio, path, frag=frag)
                start = time.time()  # reset timer


if __name__ == "__main__":
    from radio import RadioInterface, configure_channel
    import time
    configure_channel(index=2)  # Use PSK channel 2 or higher
    radio = RadioInterface()
    path = input("Enter the file path to request (e.g. /test.html): ")
    send_get_request(radio.iface, path)
    print("Waiting for response... (Ctrl+C to exit)")
    try:
        start_client(radio.iface, path)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nClient stopped.")