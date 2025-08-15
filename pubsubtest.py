

#!/usr/bin/env python3
"""
pubsubtest.py — exercise the new publisher & listener modules

Run modes:
  • Listen only (default):
        python pubsubtest.py
  • Send one JSON message (and keep listening):
        python pubsubtest.py --send --path /echo --data "hello from pubsubtest" \
                             --type RESP --frag 1 --of-frag 1
  • Periodic send (every N seconds) while listening:
        python pubsubtest.py --send --interval 5 --path /ping --data ping

Env:
  MESHTASTIC_PORT, DEFAULT_CHANNEL_INDEX as usual
  LISTENER_DEBUG=1, PUBLISHER_DEBUG=1 for verbose logs
  PUBLISHER_STAMP=1 to prefix frames with "MH1 " for easy subscriber detection
  LISTENER_FILTER_CHANNEL=1 to drop envelopes whose chan != DEFAULT_CHANNEL_INDEX
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

from functools import partial

from pubsub import pub

# Local modules
from radio import RadioInterface
from subscriber import start_listener
from publisher import send_text, send_json, send_envelope

TRUTHY = {"1", "true", "yes", "on", "y"}


def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _iface_of(radio):
    return getattr(radio, "iface", radio)


def _print_iface_diag(iface):
    try:
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Test iface: {iface.__class__.__name__} dev={dev}")
        nodes = getattr(iface, 'nodes', {}) or {}
        if nodes:
            print("[INFO] Known nodes:")
            for node_num, info in nodes.items():
                user = (info or {}).get('user', {}) or {}
                sn = user.get('shortName') or user.get('longName') or 'Unknown'
                print(f"  {node_num}: {sn}")
    except Exception as e:
        print(f"[WARN] Diagnostics failed: {e}")


def _on_message(message: dict, packet: Optional[dict] = None):
    """Listener callback for parsed JSON messages."""
    try:
        print(f"[RX  JSON] {message}")
        if packet and _is_on('LISTENER_DEBUG'):
            print(f"[RX  RAW ] {packet}")
    except Exception as e:
        print(f"[WARN] on_message error: {e}")


def _on_conn(interface=None, **kw):
    print("[INFO] Connection established (pubsub)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Publisher/Listener test harness")
    ap.add_argument("--send", action="store_true", help="Send a message on start")
    ap.add_argument("--interval", type=int, default=0, help="If >0, send every N seconds")
    ap.add_argument("--type", dest="type_", default="RESP", help="Envelope type (RESP/PING/etc)")
    ap.add_argument("--path", default="/echo", help="Envelope path (e.g. /echo, /test.html)")
    ap.add_argument("--frag", type=int, default=1, help="Fragment number (1-based)")
    ap.add_argument("--of-frag", type=int, default=1, help="Total fragments")
    ap.add_argument("--data", default="pubsubtest", help="Payload data string")
    ap.add_argument("--probe", action="store_true", help="Send a small JSON echo probe to verify RX path")
    ap.add_argument("--tap", action="store_true", help="Also tap pubsub directly (meshtastic.receive) for raw verification")
    ap.add_argument("--tap-all", action="store_true", help="Tap ALL pubsub topics for deep debugging")
    args = ap.parse_args(argv)

    # Bring up radio & iface
    radio = RadioInterface()
    iface = _iface_of(radio)
    _print_iface_diag(iface)
    print("[INFO] Wiring subscriber (start_listener)…")

    # Wire listener and connection notice
    start_listener(radio, _on_message)
    print("[INFO] Listener armed (subscriber.start_listener)")

    if args.tap:
        def _tap(packet=None, interface=None, topic=pub.AUTO_TOPIC, **kw):
            print(f"[TAP ] topic={topic.getName()} from={getattr(packet, 'fromId', None) or (packet or {}).get('fromId')} decoded={(packet or {}).get('decoded', {})}")
        pub.subscribe(_tap, "meshtastic.receive")
        print("[INFO] Tap enabled on meshtastic.receive")

    if args.tap_all:
        def _tap_all(topic=pub.AUTO_TOPIC, **kw):
            print(f"[TAP*] topic={topic.getName()} data_keys={list(kw.keys())}")
        pub.subscribe(_tap_all, pub.ALL_TOPICS)
        print("[INFO] Tap enabled on ALL_TOPICS (noisy)")

    pub.subscribe(_on_conn, "meshtastic.connection.established")

    # Optionally send probe
    if args.probe and not args.send:
        print("[TX PROBE] envelope /echo 1/1 'probe_from_pubsubtest'")
        # Use send_envelope so we exercise the new compact schema + optional MH1 stamp
        send_envelope(radio, path="/echo", frag=1, of_frag=1, data="probe_from_pubsubtest", type_="RESP")

    # Optionally send once on start
    if args.send:
        env = {
            "type": args.type_,
            "path": args.path,
            "frag": int(args.frag),
            "of_frag": int(args.of_frag),
            "data": args.data,
        }
        print(f"[TX  NOW] {env}")
        # Use envelope helper so we test that path
        send_envelope(radio, path=args.path, frag=args.frag, of_frag=args.of_frag, data=args.data, type_=args.type_)

    # Periodic send loop (0 = disabled)
    t0 = time.time()
    try:
        n = 0
        while True:
            time.sleep(1)
            if args.interval > 0 and (time.time() - t0) >= args.interval:
                n += 1
                t0 = time.time()
                env = {
                    "type": args.type_,
                    "path": args.path,
                    "frag": int(args.frag),
                    "of_frag": int(args.of_frag),
                    "data": f"{args.data}#{n}",
                }
                print(f"[TX  {n:03d}] {env}")
                send_envelope(radio, path=args.path, frag=args.frag, of_frag=args.of_frag, data=env["data"], type_=args.type_)
    except KeyboardInterrupt:
        print("[INFO] Stopped")
        try:
            iface.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))