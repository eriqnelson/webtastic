#!/usr/bin/env python3
"""
read_messages.py — Minimal, single-open Meshtastic reader (API-only, serial-only)

Key improvements vs the GitHub snippet you found:
  * Opens the serial interface ONCE (avoids port lock errors)
  * Subscribes to meshtastic.receive and also hooks iface.onReceive
  * Works with env-based port selection (supports wildcards)
  * Robust TEXT_MESSAGE_APP parsing (decoded.text or payload bytes)
  * Optionally prints a compact node list for name lookup

Usage:
  MESHTASTIC_PORT=/dev/ttyACM0 python read_messages.py
  # or
  MESHTASTIC_PORT="/dev/ttyACM*" python read_messages.py
"""
import json
import os
import sys
import time
import threading

VERBOSE = True  # set False to quiet non-JSON traffic

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY

from pubsub import pub

from radio import RadioInterface  # uses our resilient port resolution & wiring helpers


def _payload_text(decoded: dict) -> str | None:
    """Extract UTF-8 text from a decoded dict that may have 'text' or byte 'payload'."""
    if not isinstance(decoded, dict):
        return None
    # Preferred: decoded.text (Meshtastic sets this for TEXT_MESSAGE_APP)
    txt = decoded.get("text")
    if isinstance(txt, str):
        return txt
    # Fallback: decoded.payload (bytes) → utf-8
    raw = decoded.get("payload")
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    return None


# Helper to send a text payload (for heartbeat etc)
def _send_text(radio, iface, payload: str):
    try:
        ch_env = os.getenv('DEFAULT_CHANNEL_INDEX', '1')
        ch = int(ch_env) if ch_env.isdigit() else 1
    except Exception:
        ch = 1
    try:
        # Prefer RadioInterface wrapper if it has .send
        if hasattr(radio, 'send'):
            print(f"[TX  ] channel={ch} payload={payload}")
            radio.send(payload)
        else:
            print(f"[TX  ] channel={ch} payload={payload}")
            iface.sendText(payload, channelIndex=ch)
    except Exception as e:
        print(f"[WARN] Heartbeat send error: {e}")


def main():
    radio = RadioInterface()
    iface = getattr(radio, "iface", radio)

    # Diagnostics
    dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
    print(f"[INFO] Using serial port: {dev}")

    # Optionally print a compact node list for name lookup
    shortnames = {}
    try:
        nodes = getattr(iface, 'nodes', {}) or {}
        for node_num, info in nodes.items():
            user = (info or {}).get('user', {}) or {}
            sn = user.get('shortName') or user.get('longName') or 'Unknown'
            shortnames[str(node_num)] = sn
        if shortnames:
            print("[INFO] Known nodes:")
            for k, v in shortnames.items():
                print(f"  {k}: {v}")
    except Exception as e:
        print(f"[WARN] Could not read nodes: {e}")

    def handle_packet(packet):
        # Always show the raw dict
        print(f"[RAW ] {packet}")
        try:
            dec = packet.get('decoded') if isinstance(packet, dict) else None
            portnum = dec.get('portnum') if isinstance(dec, dict) else None
            txt = dec.get('text') if isinstance(dec, dict) else None
            if isinstance(txt, str):
                # Try to pretty-print JSON if present
                try:
                    js = json.loads(txt)
                    print(f"[JSON] {js}")
                except Exception:
                    print(f"[TEXT] {txt}")
            elif VERBOSE and isinstance(dec, dict):
                # Show non-text payloads briefly
                payload = dec.get('payload')
                bf = dec.get('bitfield')
                print(f"[INFO] port={portnum} bitfield={bf} payload_type={type(payload).__name__}")
        except Exception as e:
            print(f"[WARN] Parse error: {e} | packet={packet}")

    # Attach direct interface callback
    try:
        def _iface_on_receive(packet, interface):
            print("[IFACE]")
            handle_packet(packet)
        iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[WARN] Could not attach iface.onReceive: {e}")

    # Subscribe to pubsub as well
    try:
        def _on_pub(packet=None, interface=None, **kw):
            print("[PUBSB]")
            handle_packet(packet)
        pub.subscribe(_on_pub, "meshtastic.receive")
        print("[INFO] Subscribed to meshtastic.receive")
    except Exception as e:
        print(f"[WARN] PubSub subscribe failed: {e}")

    print("[INFO] Listening… Ctrl+C to stop")
    print(f"[INFO] MESHTASTIC_PORT={os.getenv('MESHTASTIC_PORT')} | DEFAULT_CHANNEL_INDEX={os.getenv('DEFAULT_CHANNEL_INDEX','1')} | SNIFF_HEARTBEAT={os.getenv('SNIFF_HEARTBEAT','0')}")
    # Optional heartbeat beacon: set SNIFF_HEARTBEAT=1 to enable
    if _is_on('SNIFF_HEARTBEAT'):
        def _hb_loop():
            n = 0
            while True:
                try:
                    # JSON heartbeat so other tools can parse
                    hb = json.dumps({"type": "HB", "node": shortnames.get(str(getattr(getattr(iface, 'localNode', None), 'myInfo', None)), None), "seq": n, "ts": int(time.time())})
                    _send_text(radio, iface, hb)
                    n += 1
                except Exception as e:
                    print(f"[WARN] Heartbeat loop error: {e}")
                time.sleep(5)
        threading.Thread(target=_hb_loop, daemon=True).start()
        print("[INFO] Heartbeat enabled (SNIFF_HEARTBEAT=1)")

    try:
        while True:
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        print("[INFO] Exiting…")
        try:
            iface.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
