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
        # Unified receive path
        try:
            dec = packet.get('decoded') if isinstance(packet, dict) else None
            portnum = dec.get('portnum') if isinstance(dec, dict) else None
            if portnum == 'TEXT_MESSAGE_APP':
                msg = _payload_text(dec)
                from_id = packet.get('fromId') or packet.get('from')
                short = shortnames.get(str(from_id), 'Unknown')
                print(f"{short} ({from_id}): {msg}")
            else:
                # Uncomment if you want to see everything
                # print(f"[OTHER] {packet}")
                pass
        except Exception as e:
            print(f"[WARN] Parse error: {e} | packet={packet}")

    # Attach direct interface callback
    try:
        def _iface_on_receive(packet, interface):
            handle_packet(packet)
        iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[WARN] Could not attach iface.onReceive: {e}")

    # Subscribe to pubsub as well
    try:
        pub.subscribe(lambda packet=None, interface=None, **kw: handle_packet(packet), "meshtastic.receive")
        print("[INFO] Subscribed to meshtastic.receive")
    except Exception as e:
        print(f"[WARN] PubSub subscribe failed: {e}")

    print("[INFO] Listening… Ctrl+C to stop")
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
