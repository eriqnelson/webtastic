import json
from listener import start_listener
import os

received_fragments = {}

# Helpers to work with either RadioInterface or a raw Meshtastic iface
def _iface_of(r):
    return getattr(r, "iface", r)

def _default_channel_index():
    try:
        return int(os.getenv("DEFAULT_CHANNEL_INDEX", 1))
    except Exception:
        return 1

def _send_text(r, text):
    # Prefer RadioInterface.send() which uses resolved default_channel_index
    if hasattr(r, "send"):
        r.send(text)
        return
    # Fallback to raw iface; include a channelIndex
    try:
        ch = getattr(r, "default_channel_index", None)
        if ch is None:
            ch = _default_channel_index()
        r.sendText(text, channelIndex=ch)
    except Exception:
        # Last-ditch: send without explicit channel index
        r.sendText(text)

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
    _send_text(radio, json.dumps(message))


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

    start_listener(_iface_of(radio), handle_response)

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
    from radio import RadioInterface, DEFAULT_CHANNEL_INDEX
    import time, os
    radio = RadioInterface()
    print(f"[INFO] Default channel index (env): {os.getenv('DEFAULT_CHANNEL_INDEX', '1')}")
    path = input("Enter the file path to request (e.g. /test.html): ")
    send_get_request(radio, path)
    print("Waiting for response... (Ctrl+C to exit)")
    try:
        start_client(radio, path)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nClient stopped.")