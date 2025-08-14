#!/usr/bin/env python3
"""
debug_server.py — lightweight Meshtastic sniffer/echo utility (API-only, serial-only)

Purpose:
  * Attach directly to the Meshtastic interface and PubSub to print ALL inbound packets.
  * Optional echo mode: if a JSON message with {"type":"GET","path":X} is received,
    respond with a tiny single-fragment RESP so you can validate the end-to-end path.
  * Optional periodic beacon to validate TX even without RX.

Env toggles (set to 1/true/yes/on/y to enable):
  * DEBUG_ECHO          -> echo tiny RESP for incoming GETs
  * DEBUG_BEACON        -> send a RESP beacon every 5s

Examples:
  MESHTASTIC_PORT=/dev/ttyACM1 python debug_server.py
  DEBUG_ECHO=1 MESHTASTIC_PORT=/dev/ttyACM1 python debug_server.py
  DEBUG_BEACON=1 MESHTASTIC_PORT=/dev/ttyACM1 python debug_server.py
"""
import json
import os
import threading
import time
from pubsub import pub

from radio import RadioInterface

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY

# ----------------- main -----------------

def main():
    radio = RadioInterface()
    iface = getattr(radio, "iface", radio)

    # Diagnostics
    try:
        tname = type(iface).__name__
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Interface: {tname} dev={dev}")
    except Exception:
        pass

    # Echo helper
    def _send_text(payload: dict):
        data = json.dumps(payload)
        print(f"[DEBUG] TX: {data}")
        try:
            # If RadioInterface wrapper was passed around, it will have send(); otherwise use raw iface
            if hasattr(radio, 'send'):
                radio.send(data)
            else:
                # fall back to env/default index; server/client scripts set DEFAULT_CHANNEL_INDEX in .env
                ch = int(os.getenv('DEFAULT_CHANNEL_INDEX', '1'))
                iface.sendText(data, channelIndex=ch)
        except Exception as e:
            print(f"[WARN] send error: {e}")

    # Optional beacon
    if _is_on('DEBUG_BEACON'):
        def _beacon_loop():
            n = 0
            while True:
                _send_text({"type":"RESP","path":"/beacon","frag":1,"of_frag":1,"data":f"debug_beacon_{n}"})
                n += 1
                time.sleep(5)
        threading.Thread(target=_beacon_loop, daemon=True).start()
        print("[INFO] Debug beacon enabled (DEBUG_BEACON=1)")

    echo_on = _is_on('DEBUG_ECHO')
    if echo_on:
        print("[INFO] Echo mode enabled (DEBUG_ECHO=1)")

    # Unified message handler
    def handle_message(raw):
        print(f"[RX ] {raw}")
        # Try to parse JSON content from common shapes
        try:
            # raw might be a dict with decoded.text or plain text
            payload = None
            if isinstance(raw, dict):
                # meshtastic.receive packet shapes
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
        except Exception:
            payload = None

        if echo_on and isinstance(payload, dict) and payload.get('type') == 'GET':
            # Minimal single-fragment response
            resp = {
                "type": "RESP",
                "path": payload.get('path', '/unknown'),
                "frag": 1,
                "of_frag": 1,
                "data": f"echo from debug_server at {int(time.time())}"
            }
            _send_text(resp)

    # Wire both iface callback and pubsub
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
    pub.subscribe(_on_any, "meshtastic.receive")
    print("[INFO] Subscribed to meshtastic.receive")

    print(f"[INFO] MESHTASTIC_PORT={os.getenv('MESHTASTIC_PORT')} | DEFAULT_CHANNEL_INDEX={os.getenv('DEFAULT_CHANNEL_INDEX','1')}")
    print("[INFO] Debug server sniffing... Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[INFO] Stopped")


if __name__ == "__main__":
    main()
