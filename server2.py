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
import traceback

VERBOSE = True  # set False to quiet non-JSON traffic

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY

from pubsub import pub

from radio import RadioInterface  # uses our resilient port resolution & wiring helpers

from fragment import fragment_html_file


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

    # Discover our own node id to avoid responding to ourselves
    my_node = None
    my_id = None
    try:
        my_node = getattr(iface, 'localNode', None)
        my_info = getattr(my_node, 'myInfo', None)
        my_id = getattr(my_info, 'my_node_num', None) or getattr(my_info, 'my_node_id', None)
        print(f"[INFO] My node: {my_id}")
    except Exception:
        print("[WARN] Could not determine local node id")

    def handle_packet(packet):
        # Always show the raw dict
        print(f"[RAW ] {packet}")
        try:
            # Basic fields
            from_id = packet.get('fromId') or packet.get('from')
            if my_id and (str(from_id) == str(my_id)):
                # Don't respond to ourselves
                print("[INFO] Skipping self-originated packet")
                return

            dec = packet.get('decoded') if isinstance(packet, dict) else None
            portnum = dec.get('portnum') if isinstance(dec, dict) else None
            txt = dec.get('text') if isinstance(dec, dict) else None

            # Try to parse JSON if present
            req = None
            if isinstance(txt, str):
                try:
                    req = json.loads(txt)
                    print(f"[JSON] {req}")
                except Exception:
                    # Not JSON → fall through to echo below
                    print(f"[TEXT] {txt}")

            # If it's a proper MiniHTTP GET, serve fragments
            is_get = isinstance(req, dict) and str(req.get('type', '')).upper() == 'GET'
            print(f"[DEBUG] is_get={is_get} portnum={portnum}")
            if is_get:
                try:
                    path = req.get('path') or '/'
                    frag = req.get('frag')

                    # Normalize filesystem path: allow "/foo.html" or "foo.html" or "/html/foo.html"
                    if path.startswith('/html/'):
                        fs_path = path.lstrip('/')  # "html/foo.html"
                    elif path.startswith('/'):
                        fs_path = f"html{path}"  # "html/foo.html"
                    else:
                        fs_path = os.path.join('html', path)  # "html/foo.html"

                    try:
                        frags = fragment_html_file(fs_path)
                    except FileNotFoundError:
                        err = json.dumps({
                            "type": "RESP",
                            "path": path,
                            "frag": 1,
                            "of_frag": 1,
                            "data": f"404: {path} not found"
                        })
                        print(f"[WARN] File not found: {fs_path}")
                        _send_text(radio, iface, err)
                        return

                    total = len(frags)

                    # If a single fragment is requested
                    if frag is not None:
                        try:
                            i = int(frag)
                        except Exception:
                            i = -1
                        if 1 <= i <= total:
                            env = {
                                "type": "RESP",
                                "path": path,
                                "frag": i,
                                "of_frag": total,
                                "data": frags[i - 1],
                            }
                            payload = json.dumps(env)
                            print(f"[TX  ] {path} frag {i}/{total}")
                            _send_text(radio, iface, payload)
                            return
                        else:
                            print(f"[WARN] Requested out-of-range frag {frag} for {path}")
                            return

                    # Otherwise send all fragments in order
                    for idx, chunk in enumerate(frags, start=1):
                        env = {
                            "type": "RESP",
                            "path": path,
                            "frag": idx,
                            "of_frag": total,
                            "data": chunk,
                        }
                        payload = json.dumps(env)
                        print(f"[TX  ] {path} {idx}/{total}")
                        _send_text(radio, iface, payload)
                    return
                except Exception:
                    print("[ERROR] GET handling failed:\n" + traceback.format_exc())
                    # fall through to default echo

            # Default behavior: echo what we got (like the reader)
            if isinstance(txt, str):
                data_preview = (txt[:200] + '…') if len(txt) > 200 else txt
            else:
                data_preview = f"port={portnum} id={packet.get('id')}"
            resp = {
                "type": "RESP",
                "path": "/echo",
                "frag": 1,
                "of_frag": 1,
                "data": f"echo: {data_preview}"
            }
            payload = json.dumps(resp)
            print(f"[ECHO] {payload}")
            _send_text(radio, iface, payload)

            if VERBOSE and isinstance(dec, dict) and not isinstance(txt, str):
                # Show non-text payloads briefly
                payload_raw = dec.get('payload')
                bf = dec.get('bitfield')
                print(f"[INFO] port={portnum} bitfield={bf} payload_type={type(payload_raw).__name__}")
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
