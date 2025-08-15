#!/usr/bin/env python3
"""
tx_rx.py — ultra-minimal TX/RX pair for Meshtastic (serial-only, API-only)

Goal:
  * Avoid pubsub topic arg-spec headaches entirely.
  * Open the serial port ONCE and keep it open.
  * Provide a dead-simple RX loop using iface.onReceive only.
  * Provide a dead-simple TX command that uses iface.sendText() directly.

Usage:
  # Receive everything (print JSON if it's JSON, else text preview)
  python tx_rx.py rx

  # Transmit a line of text (broadcast on DEFAULT_CHANNEL_INDEX, defaults to 1)
  python tx_rx.py tx --text "hello mesh"

  # Transmit the contents of a file (sent as a single message)
  python tx_rx.py tx --file html/test.html

Environment:
  MESHTASTIC_PORT         A concrete device path or glob. Examples:
                          /dev/ttyACM0, /dev/ttyUSB0, /dev/cu.usbmodem*, /dev/ttyACM*
  DEFAULT_CHANNEL_INDEX   Integer channel index (default: 1)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from typing import Optional

from meshtastic.serial_interface import SerialInterface

TRUTHY = {"1", "true", "yes", "on", "y"}


def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _resolve_port() -> str:
    """
    Resolve the serial port to open:
      1) Use MESHTASTIC_PORT if it points to an existing device.
      2) If MESHTASTIC_PORT is a glob, pick the first match.
      3) Otherwise, try a few sensible fallbacks.
    """
    env = (os.getenv("MESHTASTIC_PORT") or "").strip()
    candidates: list[str] = []

    if env:
        # If it's an exact path that exists, prefer it
        if os.path.exists(env):
            return env
        # Else treat it as a glob
        candidates.extend(sorted(glob.glob(env)))

    # Common Linux & macOS fallbacks
    candidates.extend(sorted(glob.glob("/dev/ttyACM*")))
    candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))
    candidates.extend(sorted(glob.glob("/dev/cu.usbmodem*")))
    candidates.extend(sorted(glob.glob("/dev/cu.SLAB_USBtoUART*")))

    if not candidates:
        raise FileNotFoundError(
            "No Meshtastic serial port found. "
            "Set MESHTASTIC_PORT to a valid device or glob (e.g. /dev/ttyACM*)."
        )
    return candidates[0]


def _channel() -> int:
    val = (os.getenv("DEFAULT_CHANNEL_INDEX") or "1").strip()
    try:
        return int(val)
    except Exception:
        return 1


class SimpleRadio:
    """
    Tiny convenience wrapper that owns a single SerialInterface.
    No pubsub subscriptions. No config writes. Just TX/RX.
    """

    def __init__(self, port: Optional[str] = None):
        if not port:
            port = _resolve_port()
        self.iface = SerialInterface(devPath=port)
        dev = getattr(self.iface, "devPath", None) or getattr(self.iface, "port", None)
        print(f"[INFO] Using serial port: {dev}")

    def close(self):
        try:
            self.iface.close()
        except Exception:
            pass

    # ----- TX -----
    def send_text(self, text: str, *, node_id: Optional[str] = None, channel_index: Optional[int] = None):
        """
        Send a text payload. If node_id is None, broadcasts.
        """
        if channel_index is None:
            channel_index = _channel()
        if not isinstance(text, str):
            text = str(text)
        dest = node_id or "^all"  # Explicit broadcast if no destination provided
        print(f"[TX  ] channel={channel_index} to={dest} len={len(text)}")
        self.iface.sendText(text, dest, channelIndex=channel_index)

    # ----- RX -----
    def start_rx_loop(self, *, only_channel: Optional[int] = None):
        """
        Attach a direct onReceive callback and loop forever.
        Prints JSON if payload is JSON, otherwise prints a short preview.
        """
        def _on_receive(packet, interface):
            try:
                ch = packet.get("channel")
                if only_channel is not None and ch != only_channel:
                    return

                dec = packet.get("decoded") or {}
                txt = dec.get("text")
                raw = dec.get("payload")

                # Prefer decoded.text, fallback to bytes payload
                if isinstance(txt, str):
                    payload_str = txt
                elif isinstance(raw, (bytes, bytearray)):
                    payload_str = raw.decode("utf-8", errors="replace")
                else:
                    payload_str = ""

                # Pretty print if JSON
                shown = False
                if payload_str:
                    try:
                        js = json.loads(payload_str)
                        print(f"[RX  ] JSON from={packet.get('fromId')} ch={ch}: {json.dumps(js, ensure_ascii=False)}")
                        shown = True
                    except Exception:
                        pass

                if not shown:
                    # Show a compact line with port info
                    portnum = dec.get("portnum")
                    preview = (payload_str[:180] + "…") if len(payload_str) > 180 else payload_str
                    print(f"[RX  ] port={portnum} from={packet.get('fromId')} ch={ch} id={packet.get('id')} text='{preview}'")

            except Exception as e:
                print(f"[WARN] RX handler error: {e}")

        # Wire the direct callback and run an idle loop
        self.iface.onReceive = _on_receive
        print("[INFO] RX armed (iface.onReceive). Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[INFO] Stopped RX.")
        finally:
            self.close()


def _cmd_tx(args):
    radio = SimpleRadio()
    try:
        if args.text is None and args.file is None:
            print("Nothing to send. Use --text or --file.")
            return
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                data = f.read()
        else:
            data = args.text
        radio.send_text(data, node_id=args.dest, channel_index=args.channel)
        # Give the radio a moment to flush
        time.sleep(0.5)
    finally:
        radio.close()


def _cmd_rx(args):
    radio = SimpleRadio()
    radio.start_rx_loop(only_channel=args.channel)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Simple Meshtastic TX/RX over serial (no pubsub).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tx = sub.add_parser("tx", help="send a text message (broadcast or to a node)")
    p_tx.add_argument("--text", help="text to send")
    p_tx.add_argument("--file", help="path to a file to read and send")
    p_tx.add_argument("--dest", help="nodeId to send to (e.g. !ec2dca42); default is broadcast", default="^all")
    p_tx.add_argument("--channel", type=int, help="channel index (default from DEFAULT_CHANNEL_INDEX or 1)", default=None)
    p_tx.set_defaults(func=_cmd_tx)

    p_rx = sub.add_parser("rx", help="print all received messages")
    p_rx.add_argument("--channel", type=int, help="only print this channel index", default=None)
    p_rx.set_defaults(func=_cmd_rx)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()