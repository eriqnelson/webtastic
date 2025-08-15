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
        txt = dec.get("text")
        if isinstance(txt, str):
            return txt
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
    """Back-compat delivery helper.
    - If callback takes 2 args, call (parsed_json, packet)
    - Else call (parsed_json)
    Only deliver when parsed_json is not None.
    """
    if parsed_json is None:
        return
    if _is_on("LISTENER_DEBUG"):
        try:
            print(f"[LISTENER] Delivering to callback: keys={list(parsed_json.keys())}")
        except Exception:
            print("[LISTENER] Delivering to callback (non-dict payload)")
    try:
        code = getattr(callback, "__code__", None)
        if code and code.co_argcount >= 2:
            callback(parsed_json, packet)
        else:
            callback(parsed_json)
    except TypeError:
        callback(parsed_json)


def start_listener(radio, callback):
    """
    Minimal, robust Meshtastic listener modeled after server2:
      • Subscribes ONLY to "meshtastic.receive" (prevents topic arg-spec clashes)
      • Also wires iface.onReceive (covers stacks that use direct callback)
      • Parses JSON from TEXT_MESSAGE_APP (decoded.text or payload bytes)
      • Optional pass-through for non-JSON text (LISTENER_PASS_THRU=1)

    Env flags:
      LISTENER_DEBUG=1      → verbose logs (RAW, PUBSB, TEXT, etc)
      LISTENER_PASS_THRU=1  → deliver non-JSON text as {"type":"TEXT","data":...}
    """
    iface = getattr(radio, "iface", radio)
    debug = _is_on("LISTENER_DEBUG")

    def _handle(packet):
        if debug:
            print(f"[LISTENER] RAW: {packet}")
        txt = _payload_text(packet)
        if isinstance(txt, str):
            try:
                js = json.loads(txt)
                print(f"[LISTENER] JSON: {js}")
                _deliver(callback, js, packet)
                return
            except Exception:
                if debug:
                    print(f"[LISTENER] TEXT (non-JSON): {txt[:160]}")
                if _is_on("LISTENER_PASS_THRU"):
                    pseudo = {"type": "TEXT", "data": txt}
                    print(f"[LISTENER] PASS-THRU TEXT → {pseudo}")
                    _deliver(callback, pseudo, packet)
                return
        else:
            if debug:
                port = (packet.get("decoded") or {}).get("portnum")
                print(f"[LISTENER] Non-text port={port} (no delivery)")

    # Direct interface callback (like server2)
    try:
        def _iface_on_receive(packet, interface):
            if debug:
                print("[IFACE]")
            _handle(packet)
        iface.onReceive = _iface_on_receive
        if debug:
            print("[LISTENER] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[LISTENER] WARN: Could not attach iface.onReceive: {e}")

    # PubSub: subscribe only to the base topic (like server2)
    def _on_pub(packet=None, interface=None, topic=pub.AUTO_TOPIC, **kw):
        if debug:
            try:
                tn = ".".join(topic.getNameTuple()) if hasattr(topic, "getNameTuple") else str(topic)
            except Exception:
                tn = "meshtastic.receive"
            print(f"[PUBSB] topic={tn}")
        if packet is None:
            if debug:
                print("[LISTENER] WARN: _on_pub called without packet")
            return
        _handle(packet)

    pub.subscribe(_on_pub, "meshtastic.receive")
    if debug:
        print("[LISTENER] Subscribed to meshtastic.receive (server2-style)")