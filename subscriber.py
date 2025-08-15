import json
import os
from pubsub import pub

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _payload_text(packet: dict) -> str | None:
    """Extract UTF-8 text from a decoded dict that may have 'text' or byte 'payload'."""
    try:
        dec = packet.get("decoded") or {}
        # Preferred: decoded.text (Meshtastic sets this for TEXT_MESSAGE_APP)
        txt = dec.get("text")
        if isinstance(txt, str):
            return txt
        # Fallback: decoded.payload (bytes) → utf-8
        raw = dec.get("payload")
        if isinstance(raw, (bytes, bytearray)):
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return None
    except Exception:
        pass
    return None


def _deliver(callback, parsed_json, packet):
    """Call the callback in a backward-compatible way.
    - If the callback accepts 2 args, send (parsed_json, packet).
    - Else, send just (parsed_json) like the old listener.
    Only deliver when parsed_json is not None to preserve old semantics.
    """
    if parsed_json is None:
        return
    if _is_on("LISTENER_DEBUG"):
        try:
            print(f"[LISTENER] Delivering to callback: keys={list(parsed_json.keys())}")
        except Exception:
            print("[LISTENER] Delivering to callback (non-dict payload)")
    try:
        # Try 2-arg style first
        if getattr(callback, "__code__", None) and callback.__code__.co_argcount >= 2:
            callback(parsed_json, packet)
        else:
            callback(parsed_json)
    except TypeError:
        # Fall back to single-arg if signature mismatch
        callback(parsed_json)


def start_listener(radio, callback):
    """
    Generic Meshtastic listener (API-only) that routes inbound packets to a callback.

    Enhancements over the original:
      • Subscribes to meshtastic.receive, .receive.text, and .receive.data
      • Also wires iface.onReceive (some stacks only hit the direct callback)
      • Safely parses JSON messages from TEXT_MESSAGE_APP, but will also parse any
        UTF‑8 payload that looks like JSON.
      • Backward-compatible callback delivery: callback(message) as before, or
        callback(message, packet) if you want raw context.

    Env toggles:
      • LISTENER_DEBUG=1      → print raw packets as they arrive.
      • LISTENER_PASS_THRU=1  → deliver non-JSON text as {"type":"TEXT","data":...}
    """
    iface = getattr(radio, "iface", radio)
    debug = _is_on("LISTENER_DEBUG")

    def _handle(packet, interface=None, **kw):
        if debug:
            print(f"[LISTENER] RAW: {packet}")
        txt = _payload_text(packet)
        if isinstance(txt, str):
            try:
                js = json.loads(txt)
                # Always show a JSON line so delivery is visible even if LISTENER_DEBUG is off
                print(f"[LISTENER] JSON: {js}")
                _deliver(callback, js, packet)
                return
            except Exception:
                # Not JSON; optionally deliver plain text if pass-through is enabled
                if debug:
                    print(f"[LISTENER] TEXT (non-JSON): {txt[:120]}")
                if _is_on('LISTENER_PASS_THRU'):
                    pseudo = {"type": "TEXT", "data": txt}
                    print(f"[LISTENER] PASS-THRU TEXT → {pseudo}")
                    _deliver(callback, pseudo, packet)
                return
        else:
            # No text payload; nothing to deliver for legacy callback
            if debug:
                port = (packet.get("decoded") or {}).get("portnum")
                print(f"[LISTENER] Non-text port={port} (no delivery)")

    # Attach direct interface callback (covers cases where pubsub isn't used)
    try:
        def _iface_on_receive(packet, interface):
            _handle(packet, interface=interface)
        iface.onReceive = _iface_on_receive
        if debug:
            print("[LISTENER] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[LISTENER] WARN: Could not attach iface.onReceive: {e}")

    # Subscribe to pubsub topics with a robust handler that tolerates varying signatures
    def _on_pub(*args, **kw):
        # pyPubSub may pass packet as kw or as the first positional arg
        packet = kw.get('packet')
        if packet is None and args:
            packet = args[0]
        interface = kw.get('interface')
        _handle(packet, interface=interface)

    pub.subscribe(_on_pub, "meshtastic.receive")
    pub.subscribe(_on_pub, "meshtastic.receive.text")
    pub.subscribe(_on_pub, "meshtastic.receive.data")
    if debug:
        print("[LISTENER] Subscribed to meshtastic.receive, .text, .data (robust handler)")