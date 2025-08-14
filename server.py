# server.py: MiniHTTP server entry point
import json
import traceback
import threading
import time
from pubsub import pub
from typing import Optional

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


def _api_get_node(iface):
    try:
        return getattr(iface, "localNode", None)
    except Exception:
        return None


def _api_get_channel(node, index: int):
    try:
        return node.getChannelByChannelIndex(index)
    except Exception:
        return None


def _channel_info_dict(ch, index: int) -> Optional[dict]:
    if not ch:
        return None
    s = getattr(ch, "settings", ch)
    name = getattr(s, "name", "") or getattr(ch, "name", "")
    psk = getattr(s, "psk", "") or getattr(ch, "psk", "")
    is_primary = getattr(ch, "isPrimary", False) or getattr(s, "isPrimary", False)
    return {"index": index, "name": name, "psk_len": len(psk), "primary": is_primary}


def _send_text(r, text):
    """Always send with an explicit channel index so we don't rely on wrapper defaults."""
    try:
        ch = getattr(r, "default_channel_index", None)
        if ch is None:
            ch = _default_channel_index()
    except Exception:
        ch = _default_channel_index()
    try:
        # Prefer the raw iface if available
        iface = _iface_of(r)
        print(f"[DEBUG] TX channelIndex={ch} payload={text}")
        iface.sendText(text, channelIndex=int(ch))
    except Exception as e:
        print(f"[ERROR] sendText failed on channel {ch}: {e}")
        # Last resort: try wrapper .send (may choose its own index)
        if hasattr(r, "send"):
            try:
                print("[WARN] Falling back to RadioInterface.send() without explicit channelIndex")
                r.send(text)
            except Exception as e2:
                print(f"[ERROR] Fallback RadioInterface.send failed: {e2}")


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
    print(f"[INFO] GET for path: {message.get('path')} frag={message.get('frag')}")

    path = message.get("path")
    if not path:
        return []

    try:
        fragments = fragment_html_file(f"html{path}")
        print(f"[INFO] Fragmented {path} into {len(fragments)} parts")
        frag_num = message.get("frag")
        if frag_num is not None:
            # Return only the requested fragment (frag is 1-based)
            frag_num = int(frag_num)
            if 1 <= frag_num <= len(fragments):
                print(f"[INFO] Serving single fragment {frag_num}/{len(fragments)} for {path}")
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

    # Mutable holder for our node id so early handlers can read it before discovery completes
    my_id_holder = {"id": None}

    # Diagnostics about interface/transport
    try:
        tname = type(iface).__name__
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Interface: {tname} dev={dev}")
    except Exception:
        pass

    # --- Attach RX handlers ASAP (before node wait/dumps) ---
    def handle_message(raw):
        try:
            # Log raw packet
            print(f"[DEBUG] RX(raw): {raw}")

            # Skip self-originated packets to avoid loops
            from_id = raw.get('fromId') if isinstance(raw, dict) else None
            from_id = from_id or (raw.get('from') if isinstance(raw, dict) else None)
            local_my_id = my_id_holder.get("id")
            if local_my_id and (str(from_id) == str(local_my_id)):
                print("[INFO] Skipping self-originated packet")
                return

            # Extract decoded content
            dec = raw.get('decoded') if isinstance(raw, dict) else None
            portnum = dec.get('portnum') if isinstance(dec, dict) else None
            text = dec.get('text') if isinstance(dec, dict) else None

            # Only react to text app traffic for MiniHTTP
            if portnum != 'TEXT_MESSAGE_APP':
                return

            # Try to parse JSON request
            req = None
            if isinstance(text, str):
                try:
                    req = json.loads(text)
                except Exception:
                    req = None

            # If not JSON, echo back a simple envelope so we can confirm RX path
            if not isinstance(req, dict):
                resp = {"type": "RESP", "path": "/echo", "frag": 1, "of_frag": 1, "data": text or f"port={portnum}"}
                payload = json.dumps(resp)
                print(f"[ECHO] {payload}")
                _send_text(radio, payload)
                return

            # If JSON but not a GET, echo back the message
            if req.get('type') != 'GET':
                resp = {"type": "RESP", "path": "/echo", "frag": 1, "of_frag": 1, "data": text}
                payload = json.dumps(resp)
                print(f"[ECHO] {payload}")
                _send_text(radio, payload)
                return

            # Valid MiniHTTP GET
            path = req.get('path') or '/'
            frag = req.get('frag')
            print(f"[INFO] GET for path: {path} frag={frag}")

            try:
                fragments = fragment_html_file(f"html{path}")
            except FileNotFoundError:
                err = json.dumps({"type": "RESP", "path": path, "frag": 1, "of_frag": 1, "data": f"404: {path} not found"})
                print(f"[WARN] File not found: {path}")
                _send_text(radio, err)
                return

            if frag is not None:
                try:
                    frag_i = int(frag)
                except Exception:
                    frag_i = -1
                total = len(fragments)
                if 1 <= frag_i <= total:
                    envelopes = create_response_envelopes(path, fragments)
                    one = json.dumps(envelopes[frag_i - 1])
                    print(f"[INFO] Serving single fragment {frag_i}/{total} for {path}")
                    _send_text(radio, one)
                    return
                else:
                    print(f"[WARN] Requested fragment {frag} out of range for {path}")
                    return

            envelopes = create_response_envelopes(path, fragments)
            print(f"[INFO] Sending {len(envelopes)} fragment(s) for {path}")
            for env in envelopes:
                payload = json.dumps(env)
                print(f"[DEBUG] TX: {payload}")
                _send_text(radio, payload)
        except Exception:
            print("[ERROR] Exception while handling message:\n" + traceback.format_exc())

    try:
        def _iface_on_receive(packet, interface):
            print("[IFACE]")
            print(f"[DEBUG] iface.onReceive packet: {packet}")
            handle_message(packet)
        iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback (early)")
    except Exception as e:
        print(f"[WARN] Could not attach iface.onReceive: {e}")

    try:
        def _on_pub(packet=None, interface=None, **kw):
            print("[PUBSB]")
            pkt = packet or kw.get('packet') or kw.get('text') or kw.get('data')
            if pkt is not None:
                handle_message(pkt)
        pub.subscribe(_on_pub, "meshtastic.receive")
        print("[INFO] Subscribed to meshtastic.receive (early)")
    except Exception as e:
        print(f"[WARN] PubSub subscribe failed: {e}")

    # Wait briefly for localNode to initialize (avoids Node: None on slow boots)
    node = None
    for _ in range(20):  # up to ~10s total
        try:
            node = _api_get_node(iface)
            if node and getattr(node, 'myInfo', None):
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not node:
        print("[WARN] localNode not ready after wait; continuing anyway")

    # Read-only dump of node and channels for visibility
    try:
        if node:
            try:
                my = getattr(node, 'myInfo', None)
                node_id = getattr(my, 'my_node_num', None) or getattr(my, 'my_node_id', None)
                print(f"[INFO] Node: {node_id}")
            except Exception:
                print("[INFO] Node: (no myInfo)")
            any_found = False
            for i in range(8):
                d = _channel_info_dict(_api_get_channel(node, i), i)
                if d:
                    any_found = True
                    print(f"[INFO] Channel[{d['index']}] name='{d['name']}' primary={d['primary']} psk_len={d['psk_len']}")
            if not any_found:
                print("[WARN] No channels reported by API (0..7)")
        else:
            print("[WARN] No localNode available from interface (cannot dump channels)")
    except Exception as e:
        print(f"[WARN] Could not dump channels: {e}")

    # Discover our own node id to avoid responding to ourselves
    try:
        my = getattr(node, 'myInfo', None)
        my_id_holder["id"] = getattr(my, 'my_node_num', None) or getattr(my, 'my_node_id', None)
        print(f"[INFO] My node: {my_id_holder['id']}")
    except Exception:
        print("[WARN] Could not determine local node id")

    # Optional: emit a periodic beacon to verify TX path
    if os.getenv('SERVER_DEBUG_BEACON') in {'1','true','yes','on','y'}:
        def _beacon_loop():
            n = 0
            while True:
                try:
                    payload = json.dumps({"type":"RESP","path":"/beacon","frag":1,"of_frag":1,"data":f"server_beacon_{n}"})
                    print(f"[DEBUG] TX(beacon): {payload}")
                    _send_text(radio, payload)
                    n += 1
                except Exception as e:
                    print(f"[WARN] Beacon send error: {e}")
                time.sleep(5)
        th = threading.Thread(target=_beacon_loop, daemon=True)
        th.start()
        print("[INFO] Debug beacon enabled (SERVER_DEBUG_BEACON=1)")

    print("[INFO] Server READY: listening for GETs…")


# Only run the server if this script is executed directly
if __name__ == "__main__":
    try:
        from radio import RadioInterface
        # Open a single interface (no provisioning; provisioning handled elsewhere)
        radio = RadioInterface()
        print(f"[INFO] Default channel index (env): {os.getenv('DEFAULT_CHANNEL_INDEX', '1')}")
        print("Starting MiniHTTP server...")
        start_server(radio)
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"Failed to start server: {e}")