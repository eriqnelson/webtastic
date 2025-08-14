import json
import os
from pubsub import pub

import threading
import time


def _to_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default

received_fragments = {}

# Normalize incoming packets/messages to a JSON dict we expect
def _coerce_to_dict(msg):
    try:
        if isinstance(msg, dict) and ("type" in msg or "path" in msg):
            return True, msg, None
        if isinstance(msg, dict):
            d = msg.get("decoded") if isinstance(msg.get("decoded"), dict) else None
            txt = None
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
        if isinstance(msg, str):
            import json as _json
            payload = _json.loads(msg)
            if isinstance(payload, dict):
                return True, payload, None
        return False, None, "unrecognized message shape"
    except Exception as e:
        return False, None, f"exception while coercing: {e}"

# Helpers to work with either RadioInterface or a raw Meshtastic iface
def _iface_of(r):
    return getattr(r, "iface", r)

def _default_channel_index():
    try:
        return int(os.getenv("DEFAULT_CHANNEL_INDEX", 1))
    except Exception:
        return 1

def _send_text(r, text):
    # Prefer the RadioInterface .send() (lets library choose the right channel)
    try:
        if hasattr(r, "send"):
            print(f"[DEBUG] Client TX (radio.send) payload={text}")
            r.send(text)
            return
    except Exception as e:
        print(f"[WARN] radio.send failed, will fall back to iface.sendText: {e}")
    # Fallback to iface with explicit channel
    try:
        ch = getattr(r, "default_channel_index", None)
        if ch is None:
            ch = _default_channel_index()
    except Exception:
        ch = _default_channel_index()
    try:
        iface = _iface_of(r)
        print(f"[DEBUG] Client TX (fallback) channelIndex={ch} payload={text}")
        iface.sendText(text, channelIndex=int(ch))
    except Exception as e:
        print(f"[ERROR] Client sendText failed on channel {ch}: {e}")

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


def start_client(radio, path, timeout=5):
    """
    Starts the MiniHTTP client, listens for fragments, reassembles the file, and re-requests missing fragments.
    """
    lock = threading.Lock()
    seen_ids = set()
    complete = threading.Event()
    connected_evt = threading.Event()
    missing_fragments = set()

    def handle_response(message):
        if message.get("type") != "RESP":
            return
        if message.get("path") != path:
            return

        total = _to_int(message.get("of_frag"), 0)
        frag_no = _to_int(message.get("frag"), 0)
        if total <= 0 or frag_no <= 0 or frag_no > total:
            print(f"[WARN] Ignoring malformed fragment: frag={frag_no} of={total}")
            return

        # De-dupe on (path, of_frag, frag, data_len)
        msg_id = (message.get("path"), total, frag_no, len(message.get("data", "")))
        with lock:
            if msg_id in seen_ids:
                print(f"[DEBUG] Duplicate fragment ignored: {frag_no}/{total}")
                return
            seen_ids.add(msg_id)

            key = (message["path"], total)
            frag_list = received_fragments.setdefault(key, [None] * total)
            frag_index = frag_no - 1
            frag_list[frag_index] = message.get("data", "")
            have = sum(1 for x in frag_list if x is not None)
            missing = [i+1 for i, v in enumerate(frag_list) if v is None]
            print(f"[FRAG] {frag_no}/{total} received | have={have}/{total} missing={missing}")

            if have == total:
                html = ''.join(frag_list)
                print(f"\nReceived complete file ({total} frags):\n\n{html}\n")

                # Ensure downloads directory exists
                os.makedirs("downloads", exist_ok=True)

                # Derive filename from path
                filename = os.path.basename(path or "index.html")
                outpath = os.path.join("downloads", filename)
                with open(outpath, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"Saved to {outpath}")
                complete.set()

    iface = _iface_of(radio)

    # Diagnostics similar to server2
    try:
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Client iface dev={dev}")
        nodes = getattr(iface, 'nodes', {}) or {}
        if nodes:
            print("[INFO] Client known nodes:")
            for node_num, info in nodes.items():
                user = (info or {}).get('user', {}) or {}
                sn = user.get('shortName') or user.get('longName') or 'Unknown'
                print(f"  {node_num}: {sn}")
    except Exception as e:
        print(f"[WARN] Client diagnostics failed: {e}")

    def _handle_raw(raw):
        print(f"[DEBUG] Client RX raw: {raw}")
        ok, payload, reason = _coerce_to_dict(raw)
        if ok and isinstance(payload, dict) and payload.get("type") == "RESP":
            print(f"[DEBUG] Client RESP: path={payload.get('path')} frag={payload.get('frag')}/{payload.get('of_frag')} len={len(payload.get('data',''))}")
        if not ok:
            print(f"[WARN] Client ignoring message: {reason}")
            return
        handle_response(payload)

    # Attach direct interface callback
    try:
        def _iface_on_receive(packet, interface):
            print(f"[IFACE] {packet}")
            _handle_raw(packet)
        iface.onReceive = _iface_on_receive
        print("[INFO] Client attached iface.onReceive callback")
    except Exception as e:
        print(f"[WARN] Client could not attach iface.onReceive: {e}")

    # Subscribe to pubsub as well
    try:
        pub.subscribe(lambda packet=None, interface=None, **kw: _handle_raw(packet), "meshtastic.receive")
        print("[INFO] Client subscribed to meshtastic.receive")
        pub.subscribe(lambda packet=None, interface=None, **kw: _handle_raw(packet), "meshtastic.receive.text")
        print("[INFO] Client also subscribed to meshtastic.receive.text")
        pub.subscribe(lambda packet=None, interface=None, **kw: _handle_raw(packet), "meshtastic.receive.data")
        print("[INFO] Client also subscribed to meshtastic.receive.data")
        # Wait for connection before first GET (mirrors server2 pattern)
        def _on_conn(interface=None, **kw):
            print("[INFO] Client connection established (pubsub)")
            connected_evt.set()
        pub.subscribe(_on_conn, "meshtastic.connection.established")
        print("[INFO] Client subscribed to meshtastic.connection.established")
    except Exception as e:
        print(f"[WARN] Client pubsub subscribe failed: {e}")

    # Initial request: wait briefly for connection
    # If already connected, the event may never fire; send after short grace.
    sent_initial = False
    # Try quick path: if the interface exposes isConnected-like state, send immediately
    try:
        iface = _iface_of(radio)
        is_up = getattr(iface, "connected", True)
        if is_up:
            send_get_request(radio, path)
            sent_initial = True
    except Exception:
        pass
    if not sent_initial:
        print("[INFO] Waiting for connection before sending GET (up to 2s)…")
        connected_evt.wait(timeout=2.0)
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
    iface = getattr(radio, 'iface', radio)
    try:
        tname = type(iface).__name__
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Client interface: {tname} dev={dev}")
    except Exception:
        pass
    print(f"[INFO] Default channel index (env): {os.getenv('DEFAULT_CHANNEL_INDEX', '1')}")
    path = input("Enter the file path to request (e.g. /test.html): ")
    print("Waiting for response... (Ctrl+C to exit)")
    try:
        start_client(radio, path)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nClient stopped.")