

#!/usr/bin/env python3
"""
client3 — MiniHTTP client using listener & publisher modules (API-only, serial-only)

Features
  • Uses RadioInterface for single-open serial
  • Uses listener.start_listener() for robust RX (pubsub + iface.onReceive)
  • Uses publisher.send_json() for GETs
  • Reassembles RESP fragments with dedupe & progress logging
  • Saves completed file to downloads/<basename>

Env knobs
  MESHTASTIC_PORT, DEFAULT_CHANNEL_INDEX
  CLIENT_DEFAULT_PATH  → default request path if user gives none (default: /index.html)
  LISTENER_DEBUG=1     → verbose RX from listener
  PUBLISHER_DEBUG=1    → verbose TX from publisher
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from pubsub import pub

from radio import RadioInterface
from subscriber import start_listener
from publisher import send_json

TRUTHY = {"1", "true", "yes", "on", "y"}


def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _iface_of(radio):
    return getattr(radio, "iface", radio)


def _normalize_path(p: str | None) -> str:
    p = (p or "").strip()
    if not p:
        p = os.getenv("CLIENT_DEFAULT_PATH", "/index.html")
    if not p.startswith("/"):
        p = "/" + p
    return p


class FragmentAssembler:
    """Collect RESP fragments keyed by (path, total)."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bykey: Dict[Tuple[str, int], List[Optional[str]]] = {}
        self._seen: set[Tuple[str, int, int, int]] = set()

    def add(self, msg: dict) -> Optional[str]:
        if msg.get("type") != "RESP":
            return None
        path = str(msg.get("path", ""))
        total = self._to_int(msg.get("of_frag"), 0)
        frag = self._to_int(msg.get("frag"), 0)
        data = msg.get("data", "")
        if not path or total <= 0 or frag <= 0 or frag > total:
            print(f"[WARN] Ignoring malformed fragment: path={path!r} frag={frag} of={total}")
            return None
        key = (path, total)
        sig = (path, total, frag, len(data))
        with self._lock:
            if sig in self._seen:
                print(f"[DEBUG] Duplicate fragment ignored: {frag}/{total} {path}")
                return None
            self._seen.add(sig)
            buf = self._bykey.setdefault(key, [None] * total)
            idx = frag - 1
            buf[idx] = data
            have = sum(1 for x in buf if x is not None)
            missing = [i + 1 for i, v in enumerate(buf) if v is None]
            print(f"[FRAG] {path} {frag}/{total} | have={have}/{total} missing={missing}")
            if have == total:
                html = ''.join(x or '' for x in buf)
                # Done with this key; free memory
                del self._bykey[key]
                return html
        return None

    @staticmethod
    def _to_int(x, default=0) -> int:
        try:
            return int(x)
        except Exception:
            return default


def main(argv: list[str]) -> int:
    radio = RadioInterface()
    iface = _iface_of(radio)
    try:
        dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        print(f"[INFO] Client3 iface: {iface.__class__.__name__} dev={dev}")
    except Exception:
        pass

    # Get request path from argv or prompt
    req = None
    if len(argv) >= 2 and argv[0] in ("--path", "-p"):
        req = argv[1]
        argv = argv[2:]
    else:
        # interactive prompt
        try:
            req = input("Enter the file path to request (e.g. /test.html): ")
        except EOFError:
            req = None
    path = _normalize_path(req)
    print(f"[INFO] Requesting path: {path}")

    assembler = FragmentAssembler()
    complete_evt = threading.Event()

    # Listener callback: accepts (message, packet)
    def on_json(msg: dict, packet: Optional[dict] = None):
        try:
            if not isinstance(msg, dict):
                return
            if msg.get("type") != "RESP":
                return
            # Match our requested path only
            if str(msg.get("path")) != path:
                return
            content = assembler.add(msg)
            if content is not None:
                # Save to downloads/<basename>
                os.makedirs("downloads", exist_ok=True)
                fname = Path(path).name or "index.html"
                out = Path("downloads") / fname
                out.write_text(content, encoding="utf-8")
                print(f"\n[OK] Received complete file → {out} ({len(content)} bytes)\n")
                complete_evt.set()
        except Exception as e:
            print(f"[WARN] on_json error: {e}")

    # Bring up listener wiring (pubsub + iface.onReceive)
    start_listener(radio, on_json)

    # Also show connection event for clarity
    def _on_conn(interface=None, **kw):
        print("[INFO] Client3 connection established (pubsub)")
    pub.subscribe(_on_conn, "meshtastic.connection.established")

    # Send GET envelope
    env = {"type": "GET", "path": path}
    print(f"[DEBUG] Client3 TX GET: {env}")
    send_json(radio, env)

    # Wait for completion (or Ctrl+C)
    try:
        while not complete_evt.wait(timeout=0.5):
            pass
    except KeyboardInterrupt:
        print("[INFO] Stopped by user")
        try:
            iface.close()
        except Exception:
            pass
        return 130

    # Close interface nicely
    try:
        iface.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))