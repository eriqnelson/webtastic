#!/usr/bin/env python3
"""
client2.py — Self-contained MiniHTTP client (pairs with server2/read_messages.py)

Goals
  • No dependency on publisher/subscriber helpers
  • Open the serial interface ONCE
  • Send compact JSON GET requests over TEXT_MESSAGE_APP
  • Broadcast to ^all on a specific channel index (env: DEFAULT_CHANNEL_INDEX)
  • Robust RX: parse decoded.text or decoded.payload (bytes or list[int])
  • Reassemble RESP fragments (frag/of or of_frag) and output to stdout or file
  • Wire both iface.onReceive and pubsub (meshtastic.receive), like server2

Usage examples
  MESHTASTIC_PORT=/dev/ttyACM1 python client2.py --path /index.html
  MESHTASTIC_PORT=/dev/ttyACM1 python client2.py --path /index.html --out out.html
  MESHTASTIC_PORT=/dev/ttyACM1 DEFAULT_CHANNEL_INDEX=1 python client2.py --path /index.html

Env flags
  LISTENER_DEBUG=1          # verbose RX logs
  DEFAULT_CHANNEL_INDEX=1   # 0 or 1 (or whatever slot you use)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

from pubsub import pub

from radio import RadioInterface  # same resilient resolver server2 uses

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY

def _downloads_dir() -> Path:
    # XDG first, then ~/downloads
    xdg = os.getenv("XDG_DOWNLOAD_DIR")
    if xdg:
        return Path(os.path.expanduser(xdg)).resolve()
    return (Path.home() / "downloads").resolve()

# ----------------------------- Helpers ---------------------------------------

def _default_channel_index() -> int:
    env = (os.getenv("DEFAULT_CHANNEL_INDEX") or "1").strip()
    try:
        return int(env)
    except Exception:
        return 1


def _payload_text(packet: dict) -> str | None:
    """Extract UTF-8 text from packet.decoded (text or payload bytes/list[int])."""
    if not isinstance(packet, dict):
        return None
    dec = packet.get("decoded") if isinstance(packet, dict) else None
    if not isinstance(dec, dict):
        return None
    # Preferred: decoded.text
    txt = dec.get("text")
    if isinstance(txt, str):
        return txt
    # Fallback: decoded.payload -> utf-8
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
    return None


def _send_text(radio, iface, payload: str):
    """Send a TEXT frame. Prefer radio.send(); fallback to iface.sendText with broadcast."""
    ch = _default_channel_index()
    try:
        if hasattr(radio, "send"):
            print(f"[TX  ] channel={ch} payload={payload}")
            radio.send(payload)
            return
    except Exception as e:
        print(f"[TX  ] WARN: radio.send failed; falling back to iface.sendText: {e}")
    # Explicit broadcast to ^all on chosen channel
    print(f"[TX  ] channel={ch} to=^all payload={payload}")
    iface.sendText(payload, destinationId="^all", channelIndex=int(ch))

# ----------------------------- Client logic ----------------------------------

class MiniHttpClient:
    def __init__(self, path: str, want_frag: int | None, out_path: Path | None, timeout: float):
        self.path = path
        self.want_frag = want_frag
        self.out_path = out_path
        self.timeout = max(1.0, float(timeout))
        self.radio = RadioInterface()
        self.iface = getattr(self.radio, "iface", self.radio)
        self.debug = _is_on("LISTENER_DEBUG")
        self.start_time = time.time()
        # buffers[path] = (total_of, {frag_idx: data})
        self.buffers: Dict[str, Tuple[int, Dict[int, str]]] = {}

    # ---- TX ----
    def send_get(self) -> None:
        req = {"type": "GET", "path": self.path}
        if self.want_frag is not None:
            req["frag"] = int(self.want_frag)
        payload = json.dumps(req, separators=(",", ":"))
        if self.debug:
            print(f"[TX  ] GET {payload}")
        _send_text(self.radio, self.iface, payload)

    # ---- RX ----
    def _handle_packet(self, packet: dict) -> None:
        if self.debug:
            print(f"[RAW ] {packet}")
        txt = _payload_text(packet)
        if not isinstance(txt, str):
            return
        # Try JSON decode (ignore non-JSON text)
        try:
            js = json.loads(txt)
        except Exception:
            if self.debug:
                print(f"[TEXT] {txt}")
            return
        # Expect RESP envelopes for our requested path
        if not isinstance(js, dict) or str(js.get("type", "")).upper() != "RESP":
            return
        if js.get("path") != self.path:
            return
        frag = int(js.get("frag", 1))
        total = int(js.get("of") or js.get("of_frag") or 1)
        data = str(js.get("data", ""))
        if self.want_frag is not None and frag != self.want_frag:
            return
        # store
        prev_total, frags = self.buffers.get(self.path, (total, {}))
        frags[frag] = data
        self.buffers[self.path] = (max(prev_total, total), frags)
        if self.debug:
            have = len(frags)
            print(f"[RX  ] {self.path} {frag}/{total} (have {have}/{total})")
        # flush when complete or when single-frag requested
        if self.want_frag is not None:
            self._flush(single=True)
        else:
            self._flush()

    def _flush(self, *, single: bool = False) -> None:
        total, frags = self.buffers.get(self.path, (0, {}))
        if single:
            if not frags:
                return
            frag_idx = min(frags)  # the one we got
            self._emit(frags[frag_idx])
            return
        if total and len(frags) >= total:
            chunks = [frags[i] for i in range(1, total + 1) if i in frags]
            if len(chunks) == total:
                self._emit("".join(chunks))

    def _emit(self, content: str) -> None:
        # Decide output path: explicit --out wins; otherwise ~/Downloads/<basename>
        if self.out_path:
            out_path = self.out_path
        else:
            base = Path(self.path).name or "index.html"
            out_path = _downloads_dir() / base
        # Ensure parent exists
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"[SAVE] wrote {out_path}")
        # Successful completion → exit
        os._exit(0)

    # ---- Wiring ----
    def run(self) -> None:
        dev = getattr(self.iface, 'devPath', None) or getattr(self.iface, 'port', None)
        print(f"[INFO] Using serial port: {dev}")
        # iface callback
        def _iface_on_receive(packet, interface):
            self._handle_packet(packet)
        self.iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback")
        # PubSub too (server2-style)
        def _on_pub(packet=None, interface=None, **kw):
            self._handle_packet(packet)
        pub.subscribe(_on_pub, "meshtastic.receive")
        print("[INFO] Subscribed to meshtastic.receive")
        # Send GET
        self.send_get()
        # Wait until timeout
        deadline = self.start_time + self.timeout
        try:
            while time.time() < deadline:
                time.sleep(0.1)
            print(f"[ERR ] Timeout after {self.timeout:.1f}s waiting for {self.path}")
            os._exit(2)
        except KeyboardInterrupt:
            pass

# ----------------------------- CLI ------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Self-contained MiniHTTP client for server2")
    ap.add_argument("--path", default="/index.html", help="Path to fetch (default: /index.html)")
    ap.add_argument("--frag", type=int, default=None, help="Specific fragment to fetch (1-based)")
    ap.add_argument("--out", type=Path, default=None, help="Write response body to file")
    ap.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait before failing")
    args = ap.parse_args()

    MiniHttpClient(path=args.path, want_frag=args.frag, out_path=args.out, timeout=args.timeout).run()

if __name__ == "__main__":
    main()