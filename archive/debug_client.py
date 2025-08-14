

#!/usr/bin/env python3
"""
debug_client.py — Meshtastic debug client/sniffer (API-only, serial-only)

Features:
  * Attaches to the Meshtastic interface and PubSub to print ALL inbound packets.
  * Can send ad-hoc GET requests from the command line.
  * Optional periodic GET beacon to validate TX regularly.

Env toggles (set to 1/true/yes/on/y):
  * DEBUG_GET_BEACON   -> send a GET every 5s for PATH (default /test.html)

Usage examples:
  MESHTASTIC_PORT=/dev/ttyACM0 python debug_client.py --path /test.html
  DEBUG_GET_BEACON=1 MESHTASTIC_PORT=/dev/ttyACM0 python debug_client.py --path /test.html
"""
import argparse
import json
import os
import threading
import time
from pubsub import pub

from radio import RadioInterface

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _send_text(radio, iface, payload: dict):
    data = json.dumps(payload)
    print(f"[DEBUG] TX: {data}")
    try:
        if hasattr(radio, 'send'):
            # RadioInterface handles channelIndex defaulting internally
            radio.send(data)
        else:
            ch = int(os.getenv('DEFAULT_CHANNEL_INDEX', '1'))
            iface.sendText(data, channelIndex=ch)
    except Exception as e:
        print(f"[WARN] send error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Meshtastic debug client/sniffer")
    parser.add_argument('--path', default='/test.html', help='Path to request with GET (default /test.html)')
    parser.add_argument('--once', action='store_true', help='Send a single GET immediately on start')
    args = parser.parse_args()

    radio = RadioInterface()
    iface = getattr(radio, 'iface', radio)

    # Diagnostics
    try:
        tname = type(iface).__name__
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Interface: {tname} dev={dev}")
    except Exception:
        pass

    # Sniff: unified inbound handler
    def handle_message(raw):
        print(f"[RX ] {raw}")
        try:
            # Try to unwrap JSON from common shapes
            payload = None
            if isinstance(raw, dict):
                d = raw.get('decoded') if isinstance(raw.get('decoded'), dict) else None
                txt = None
                if d and isinstance(d.get('text'), str):
                    txt = d['text']
                elif isinstance(raw.get('text'), str):
                    txt = raw['text']
                if txt:
                    payload = json.loads(txt)
                elif 'type' in raw and 'path' in raw:
                    payload = raw
            elif isinstance(raw, str):
                payload = json.loads(raw)

            if isinstance(payload, dict):
                # Pretty-print RESP summary
                if payload.get('type') == 'RESP':
                    print(f"[RESP] path={payload.get('path')} frag={payload.get('frag')}/{payload.get('of_frag')} data_len={len(payload.get('data',''))}")
        except Exception:
            pass

    # Attach both iface callback and pubsub
    try:
        def _iface_on_receive(packet, interface):
            print(f"[IFACE] {packet}")
            handle_message(packet)
        iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[WARN] Could not attach iface.onReceive: {e}")

    def _on_any(packet=None, interface=None, **kwargs):
        print(f"[PUBSB] {packet}")
        handle_message(packet)
    pub.subscribe(_on_any, 'meshtastic.receive')
    print("[INFO] Subscribed to meshtastic.receive")

    # Optional: send one GET at startup
    if args.once:
        _send_text(radio, iface, {"type": "GET", "path": args.path})

    # Optional: periodic GET beacon
    if _is_on('DEBUG_GET_BEACON'):
        def _get_loop():
            while True:
                _send_text(radio, iface, {"type": "GET", "path": args.path})
                time.sleep(5)
        threading.Thread(target=_get_loop, daemon=True).start()
        print(f"[INFO] GET beacon enabled (DEBUG_GET_BEACON=1) path={args.path}")

    print(f"[INFO] MESHTASTIC_PORT={os.getenv('MESHTASTIC_PORT')} | DEFAULT_CHANNEL_INDEX={os.getenv('DEFAULT_CHANNEL_INDEX','1')}")
    print("[INFO] Debug client sniffing... Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[INFO] Stopped")


if __name__ == '__main__':
    main()