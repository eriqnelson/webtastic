#!/usr/bin/env python3
"""
sniff.py — Meshtastic RX sniffer (API-only, serial-only)

Usage:
  MESHTASTIC_PORT=/dev/ttyACM2 python sniff.py
  # or use a glob so it survives reboots:
  MESHTASTIC_PORT="/dev/ttyACM*" python sniff.py

Prints every inbound packet via both iface.onReceive and the
' meshtastic.receive ' pubsub topic. Also tries to pretty-print
any JSON carried in decoded.text.
"""

import json
import os
import time
from pubsub import pub

from radio import RadioInterface


def _extract_json(payload):
    """Return (ok, dict_or_None, reason) after attempting to parse JSON from a packet."""
    try:
        if isinstance(payload, dict):
            d = payload.get("decoded") if isinstance(payload.get("decoded"), dict) else None
            txt = None
            if d and isinstance(d.get("text"), str):
                txt = d["text"]
            elif isinstance(payload.get("text"), str):
                txt = payload["text"]
            if txt:
                return True, json.loads(txt), None
        elif isinstance(payload, str):
            return True, json.loads(payload), None
        return False, None, "no JSON found"
    except Exception as e:
        return False, None, f"json parse error: {e}"


def main():
    radio = RadioInterface()
    iface = getattr(radio, "iface", radio)

    # Wait briefly for localNode to initialize
    node = None
    for _ in range(20):  # up to ~10s total
        try:
            node = getattr(iface, 'localNode', None)
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
                try:
                    ch = node.getChannelByChannelIndex(i)
                except Exception:
                    ch = None
                if not ch:
                    continue
                s = getattr(ch, 'settings', ch)
                name = getattr(s, 'name', '') or getattr(ch, 'name', '')
                psk = getattr(s, 'psk', '') or getattr(ch, 'psk', '')
                is_primary = getattr(ch, 'isPrimary', False) or getattr(s, 'isPrimary', False)
                any_found = True
                print(f"[INFO] Channel[{i}] name='{name}' primary={is_primary} psk_len={len(psk)}")
            if not any_found:
                print("[WARN] No channels reported by API (0..7)")
        else:
            print("[WARN] No localNode available from interface (cannot dump channels)")
    except Exception as e:
        print(f"[WARN] Could not dump channels: {e}")

    # Show which port we actually opened
    try:
        tname = type(iface).__name__
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Sniffer interface: {tname} dev={dev}")
    except Exception:
        pass

    # Unified handler
    def handle(raw):
        print(f"[RAW ] {raw}")
        ok, js, reason = _extract_json(raw)
        if ok and isinstance(js, dict):
            print(f"[JSON] {js}")
        else:
            # keep it quiet unless you need it
            pass

    # Attach direct interface callback
    try:
        def _iface_on_receive(packet, interface):
            print(f"[IFACE] {packet}")
            handle(packet)
        iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback")
    except Exception as e:
        print(f"[WARN] Could not attach iface.onReceive: {e}")

    # Subscribe to pubsub as well
    try:
        pub.subscribe(lambda packet=None, interface=None, **kw: (print(f"[PUBSB] {packet}"), handle(packet)), "meshtastic.receive")
        print("[INFO] Subscribed to meshtastic.receive")
    except Exception as e:
        print(f"[WARN] PubSub subscribe failed: {e}")

    print(f"[INFO] MESHTASTIC_PORT={os.getenv('MESHTASTIC_PORT')} | DEFAULT_CHANNEL_INDEX={os.getenv('DEFAULT_CHANNEL_INDEX','1')}")
    print("[INFO] Sniffing... Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[INFO] Stopped")


if __name__ == "__main__":
    main()
