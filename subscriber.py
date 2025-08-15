import json
import os
from pubsub import pub

TRUTHY = {"1", "true", "yes", "on", "y"}


def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _default_channel_index() -> int:
    env = (os.getenv("DEFAULT_CHANNEL_INDEX") or "1").strip()
    try:
        return int(env)
    except Exception:
        return 1


def _chan_filter_on() -> bool:
    # When true, JSON envelopes with a 'chan' field must match DEFAULT_CHANNEL_INDEX
    return _is_on("LISTENER_FILTER_CHANNEL")


def _payload_text(packet: dict) -> str | None:
    """Extract UTF-8 text from a decoded dict that may have 'text' or byte 'payload'.
    Also tolerate top-level 'text' kw that some pubsub senders provide.
    """
    try:
        # If someone passed text at top-level (splatted kwargs), honor it
        top_txt = packet.get("text")
        if isinstance(top_txt, str):
            return top_txt

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
        if isinstance(raw, list) and all(isinstance(b, int) for b in raw):
            try:
                return bytes(raw).decode("utf-8", errors="replace")
            except Exception:
                return None
    except Exception:
        pass
    return None


def _synthesize_packet_from_kwargs(**kw) -> dict:
    """Build a packet-like dict when pubsub calls us without `packet=`.
    We accept common fields as separate kwargs and normalize them.
    """
    pkt = {}
    # Known top-level fields that might arrive splatted
    for k in (
        "from", "to", "id", "channel", "fromId", "toId", "rxTime",
        "hopLimit", "priority", "raw", "rxSnr", "rxRssi", "hopStart",
    ):
        if k in kw:
            pkt[k] = kw[k]

    # decoded bits may be provided as a kw already
    dec = kw.get("decoded") or {}
    if not isinstance(dec, dict):
        dec = {}

    # Some senders splat a `text` kw separately
    if "text" in kw and isinstance(kw["text"], str):
        dec = dict(dec)
        dec["text"] = kw["text"]

    if dec:
        pkt["decoded"] = dec

    return pkt


def _deliver(callback, payload, packet):
    """Back-compat delivery helper.
    - If callback takes 2 args, call (payload, packet)
    - Else call (payload)
    ALWAYS deliver when we have a payload (JSON dict OR TEXT pseudo-dict).
    """
    if payload is None:
        return
    debug = _is_on("LISTENER_DEBUG")
    if debug:
        try:
            kind = payload.get("type") if isinstance(payload, dict) else type(payload).__name__
            print(f"[LISTENER] Delivering kind={kind} to callback")
        except Exception:
            print("[LISTENER] Delivering to callback")
    try:
        code = getattr(callback, "__code__", None)
        if code and code.co_argcount >= 2:
            callback(payload, packet)
        else:
            callback(payload)
    except TypeError:
        callback(payload)


def start_listener(radio, callback):
    """
    Minimal, robust Meshtastic listener modeled after server2:
      • Subscribes ONLY to "meshtastic.receive" (prevents topic arg-spec clashes)
      • Also wires iface.onReceive (covers stacks that use direct callback)
      • Parses JSON from TEXT_MESSAGE_APP (decoded.text or payload bytes)
      • **By default now** also delivers non-JSON text as {"type":"TEXT","data":...}
        (set LISTENER_TEXT_OFF=1 to disable pass-through)

    Env flags:
      LISTENER_DEBUG=1   → verbose logs (RAW, PUBSB, TEXT, etc)
      LISTENER_TEXT_OFF=1→ disable TEXT pass-through delivery
    """
    iface = getattr(radio, "iface", radio)
    debug = _is_on("LISTENER_DEBUG")
    passthru = not _is_on("LISTENER_TEXT_OFF")

    def _handle(packet: dict):
        if debug:
            print(f"[LISTENER] RAW: {packet}")
        # Fire an unconditional ANY event so we react to *every* packet
        try:
            dec = packet.get("decoded") or {}
            port = dec.get("portnum")
            any_evt = {"type": "ANY", "port": port}
            chan = packet.get("channel") or packet.get("channelIndex")
            if chan is not None:
                any_evt["chan"] = chan
            src = packet.get("fromId") or packet.get("from")
            if src is not None:
                any_evt["from"] = src
            _deliver(callback, any_evt, packet)
            if debug:
                print("[LISTENER] ANY event delivered (pre-parse)")
        except Exception as _e:
            if debug:
                print(f"[LISTENER] WARN: ANY event delivery failed: {_e}")

        txt = _payload_text(packet)
        if not isinstance(txt, str):
            if debug:
                port = (packet.get("decoded") or {}).get("portnum")
                print(f"[LISTENER] Non-text port={port} (no delivery)")
            return

        # Recognize optional publisher stamp: "MH1 " prefix
        if txt.startswith("MH1 "):
            body = txt[4:]
        else:
            body = txt

        # Try to parse JSON
        try:
            js = json.loads(body)
            if debug:
                print(f"[LISTENER] JSON: {js}")
            # Optional channel filter if publisher provided 'chan'
            if _chan_filter_on():
                chan = js.get("chan")
                if chan is not None and str(chan) != str(_default_channel_index()):
                    if debug:
                        print(f"[LISTENER] Drop: chan {chan} != wanted {_default_channel_index()}")
                    return
            _deliver(callback, js, packet)
            return
        except Exception:
            # Not JSON — treat as plain text
            if debug:
                preview = txt[:160]
                print(f"[LISTENER] TEXT: {preview}")
            if passthru:
                pseudo = {"type": "TEXT", "data": txt}
                _deliver(callback, pseudo, packet)
            return

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
        # Some stacks call with packet kwarg; others splat fields.
        if packet is None:
            if kw:
                packet = _synthesize_packet_from_kwargs(**kw)
                if debug:
                    print("[LISTENER] Synthesized packet from kwargs")
            else:
                if debug:
                    print("[LISTENER] WARN: _on_pub called without packet/kwargs; ignoring")
                return
        _handle(packet)

    pub.subscribe(_on_pub, "meshtastic.receive")
    if debug:
        print("[LISTENER] Subscribed to meshtastic.receive (server2-style)")