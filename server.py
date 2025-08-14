# server.py: MiniHTTP server entry point
import json
import traceback
from pubsub import pub
def _coerce_to_dict(msg):
    """Try to turn various message shapes into a dict with keys we expect.
    Accepts raw JSON string, Meshtastic decoded packet dicts, or already-JSON dicts.
    Returns a tuple (ok: bool, payload: dict|None, reason: str|None).
    """
    try:
        # Case 1: already a dict with 'type'
        if isinstance(msg, dict) and ("type" in msg or "path" in msg):
            return True, msg, None
        # Case 2: Meshtastic packet with decoded text
        if isinstance(msg, dict):
            # Common shapes: {'decoded': {'text': '{...}'}} or {'text': '{...}'}
            txt = None
            d = msg.get("decoded") if isinstance(msg.get("decoded"), dict) else None
            if d and isinstance(d.get("text"), str):
                txt = d["text"]
            elif isinstance(msg.get("text"), str):
                txt = msg["text"]
            if txt:
                import json as _json
                try:
                    payload = _json.loads(txt)
                    if isinstance(payload, dict):
                        return True, payload, None
                except Exception:
                    return False, None, "decoded.text not JSON"
        # Case 3: raw JSON string
        if isinstance(msg, str):
            import json as _json
            payload = _json.loads(msg)
            if isinstance(payload, dict):
                return True, payload, None
        return False, None, "unrecognized message shape"
    except Exception as e:
        return False, None, f"exception while coercing: {e}"

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
    ok, payload, reason = _coerce_to_dict(message)
    if not ok:
        print(f"[WARN] Ignoring message (cannot parse): {reason} | {type(message)} => {message}")
        return []

    message = payload

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
    iface = _iface_of(radio)

    def handle_message(raw):
        try:
            print(f"[DEBUG] RX: {raw}")
            responses = handle_get_message(raw)
            if not responses:
                return
            for resp in responses:
                payload = json.dumps(resp)
                print(f"[DEBUG] TX: {payload}")
                _send_text(radio, payload)
        except Exception:
            print("[ERROR] Exception while handling message:\n" + traceback.format_exc())

    # Direct pubsub subscriptions (some environments deliver messages only via pubsub)
    # NOTE: The 'meshtastic.receive' root topic defines payload name 'packet';
    # all subtopics must include 'packet' in their handler signature per pypubsub rules.
    def _on_any(packet=None, interface=None, **kwargs):
        print(f"[DEBUG] pubsub receive packet: {packet}")
        handle_message(packet)

    def _on_conn_established(**kwargs):
        print("[INFO] Meshtastic connection established")

    def _on_conn_lost(**kwargs):
        print("[WARN] Meshtastic connection lost")

    # Subscribe to the structured packet stream and connection lifecycle events
    pub.subscribe(_on_any, "meshtastic.receive")
    pub.subscribe(_on_conn_established, "meshtastic.connection.established")
    pub.subscribe(_on_conn_lost, "meshtastic.connection.lost")

    print("[INFO] Subscribed to meshtastic.receive and connection topics")



# Only run the server if this script is executed directly
if __name__ == "__main__":
    try:
        from radio import RadioInterface
        import time
        # Open a single interface (no provisioning; provisioning handled elsewhere)
        radio = RadioInterface()
        print(f"[INFO] Default channel index (env): {os.getenv('DEFAULT_CHANNEL_INDEX', '1')}")
        print("Starting MiniHTTP server...")
        start_server(radio)
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"Failed to start server: {e}")