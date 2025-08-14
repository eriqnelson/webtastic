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
      • LISTENER_DEBUG=1  → print raw packets as they arrive.
    """
    iface = getattr(radio, "iface", radio)
    debug = _is_on("LISTENER_DEBUG")

    def _handle(packet, interface=None, **kw):
        if debug:
            print(f"[LISTENER] RAW: {packet}")
        txt = _payload_text(packet)
        if isinstance(txt, str):
            js = None
            try:
                js = json.loads(txt)
                print(f"[LISTENER] JSON: {js}")
            except Exception:
                # Not JSON; ignore to preserve old behavior
                if debug:
                    print(f"[LISTENER] TEXT (non-JSON): {txt[:120]}")
            _deliver(callback, js, packet)
        else:
            # No text payload; nothing to deliver for legacy callback
            if debug:
                port = (packet.get("decoded") or {}).get("portnum")
                print(f"[LISTENER] Non-text port={port}")

    # Attach direct interface callback (covers cases where pubsub isn't used)
    try:
        def _iface_on_receive(packet, interface):
            _handle(packet, interface=interface)
        iface.onReceive = _iface_on_receive
        if debug:
            print("[LISTENER] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[LISTENER] WARN: Could not attach iface.onReceive: {e}")

    # Subscribe to pubsub topics
    pub.subscribe(lambda packet=None, interface=None, **kw: _handle(packet, interface=interface), "meshtastic.receive")
    pub.subscribe(lambda packet=None, interface=None, **kw: _handle(packet, interface=interface), "meshtastic.receive.text")
    pub.subscribe(lambda packet=None, interface=None, **kw: _handle(packet, interface=interface), "meshtastic.receive.data")
    if debug:
        print("[LISTENER] Subscribed to meshtastic.receive, .text, .data")