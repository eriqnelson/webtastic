# server.py: MiniHTTP server entry point
from listener import start_listener
import json

# Helpers to work with either RadioInterface or a raw Meshtastic iface
import os

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
    # Fallback to raw iface; include a channelIndex if we can
    try:
        ch = getattr(r, "default_channel_index", None)
        if ch is None:
            ch = _default_channel_index()
        r.sendText(text, channelIndex=ch)
    except Exception:
        # Last-ditch: send without explicit channel index
        r.sendText(text)

def create_response_envelopes(path, fragments):
    """
    Given a file path and a list of fragments, returns a list of response envelopes
    conforming to the MiniHTTP spec.
    """
    total = len(fragments)
    envelopes = []

    for i, frag in enumerate(fragments):
        envelopes.append({
            "type": "RESP",
            "path": path,
            "frag": i + 1,
            "of_frag": total,
            "data": frag
        })

    return envelopes

from fragment import fragment_html_file


def handle_get_message(message):
    """
    Processes a GET message and returns a list of response envelopes.
    Handles full file or single fragment requests.
    """
    if message.get("type") != "GET":
        return []

    path = message.get("path")
    if not path:
        return []

    try:
        fragments = fragment_html_file(f"html{path}")
        frag_num = message.get("frag")
        if frag_num is not None:
            # Return only the requested fragment (frag is 1-based)
            frag_num = int(frag_num)
            if 1 <= frag_num <= len(fragments):
                return [create_response_envelopes(path, fragments)[frag_num-1]]
            else:
                print(f"Requested fragment {frag_num} out of range for {path}")
                return []
        return create_response_envelopes(path, fragments)
    except FileNotFoundError:
        print(f"File not found: {path}")
        return []



def start_server(radio):
    """
    Starts the MiniHTTP server by listening for GET messages and responding.
    """
    def handle_message(message):
        responses = handle_get_message(message)
        for resp in responses:
            _send_text(radio, json.dumps(resp))

    start_listener(_iface_of(radio), handle_message)


# Only run the server if this script is executed directly
if __name__ == "__main__":
    try:
        from radio import RadioInterface, configure_channel, DEFAULT_CHANNEL_INDEX
        import time
        # Configure the channel first (API / URL if provided), then open a single interface
        radio = configure_channel(index=DEFAULT_CHANNEL_INDEX)
        print(f"[INFO] Using channel index: {getattr(radio, 'default_channel_index', 'unknown')}")
        print("Starting MiniHTTP server...")
        start_server(radio)
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"Failed to start server: {e}")