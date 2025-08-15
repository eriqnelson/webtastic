

#!/usr/bin/env python3
"""
client2.py — MiniHTTP client for server2

Features
  • Opens the serial interface ONCE
  • Sends compact MiniHTTP GET envelopes (via publisher helpers)
  • Broadcasts to ^all on the chosen channel (handled by publisher fallback)
  • Robust RX: handles decoded.text and decoded.payload (bytes or list[int])
  • Understands optional "MH1 " stamp
  • Reassembles fragments (frag/of) and writes result to stdout or a file
  • PubSub + iface.onReceive wiring (like server2)

Usage examples
  MESHTASTIC_PORT=/dev/ttyACM1 python client2.py --path /index.html
  MESHTASTIC_PORT=/dev/ttyACM1 python client2.py --path /index.html --out out.html
  MESHTASTIC_PORT=/dev/ttyACM1 DEFAULT_CHANNEL_INDEX=1 python client2.py --path /index.html

Env flags
  PUBLISHER_STAMP=1         # prefix TX with "MH1 "
  PUBLISHER_DEBUG=1         # verbose TX logs
  LISTENER_DEBUG=1          # verbose RX logs
  DEFAULT_CHANNEL_INDEX=1   # fallback slot (0 or 1)
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

from radio import RadioInterface
from publisher import send_json  # uses our compact JSON + optional MH1 + ^all fallback

TRUTHY = {"1", "true", "yes", "on", "y"}

def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY

# ----------------------------- RX helpers ------------------------------------

def _payload_text(packet: dict) -> str | None:
    dec = packet.get("decoded") if isinstance(packet, dict) else None
    if not isinstance(dec, dict):
        return None
    # Common case
    txt = dec.get("text")
    if isinstance(txt, str):
        return txt
    # payload as bytes
    raw = dec.get("payload")
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    # payload as list[int]
    if isinstance(raw, list) and all(isinstance(b, int) for b in raw):
        try:
            return bytes(raw).decode("utf-8", errors="replace")
        except Exception:
            return None
    return None

# ----------------------------- Client logic ----------------------------------

class MiniHttpClient:
    def __init__(self, path: str, want_frag: int | None, out_path: Path | None, timeout: float):
        self.path = path
        self.want_frag = want_frag
        self.out_path = out_path
        self.timeout = timeout
        self.radio = RadioInterface()
        self.iface = getattr(self.radio, "iface", self.radio)
        self.debug = _is_on("LISTENER_DEBUG")
        self.start_time = time.time()
        # buffers[(path)] = (total_of, {frag_idx: data})
        self.buffers: Dict[str, Tuple[int, Dict[int, str]]] = {}

    # ---- TX ----
    def send_get(self) -> None:
        req = {"type": "GET", "path": self.path}
        if self.want_frag is not None:
            req["frag"] = int(self.want_frag)
        if self.debug:
            print(f"[TX  ] GET {req}")
        send_json(self.radio, req)

    # ---- RX ----
    def _handle_packet(self, packet: dict) -> None:
        if self.debug:
            print(f"[RAW ] {packet}")
        txt = _payload_text(packet)
        if not isinstance(txt, str):
            return
        # Strip optional MH1 stamp
        if txt.startswith("MH1 "):
            txt = txt[4:]
        try:
            js = json.loads(txt)
        except Exception:
            if self.debug:
                print(f"[TEXT] {txt}")
            return
        # Expect RESP envelopes
        if not isinstance(js, dict) or str(js.get("type","")) != "RESP":
            return
        if js.get("path") != self.path:
            # unrelated response
            return
        frag = int(js.get("frag", 1))
        total = int(js.get("of") or js.get("of_frag") or 1)
        data = str(js.get("data", ""))
        if self.want_frag is not None and frag != self.want_frag:
            # not our requested fragment
            return
        # store
        total0, frags = self.buffers.get(self.path, (total, {}))
        frags[frag] = data
        self.buffers[self.path] = (max(total0, total), frags)
        have = len(frags)
        if self.debug:
            print(f"[RX  ] {self.path} {frag}/{total} (have {have}/{total})")
        # If single-frag wanted, flush immediately
        if self.want_frag is not None:
            self._flush_if_complete(single=True)
        else:
            self._flush_if_complete()

    def _flush_if_complete(self, *, single: bool = False) -> None:
        total, frags = self.buffers.get(self.path, (0, {}))
        if single:
            # user asked for a single fragment
            frag_idx = next(iter(frags)) if frags else None
            if frag_idx is None:
                return
            data = frags[frag_idx]
            self._emit(data)
            # keep buffer intact in case they want more later
            return
        if total and len(frags) >= total:
            # assemble
            chunks = [frags[i] for i in range(1, total + 1) if i in frags]
            if len(chunks) == total:
                html = "".join(chunks)
                self._emit(html)

    def _emit(self, content: str) -> None:
        if self.out_path:
            self.out_path.write_text(content, encoding="utf-8")
            print(f"[SAVE] wrote {self.out_path}")
        else:
            # write to stdout
            sys.stdout.write(content)
            sys.stdout.flush()
        # successful completion → exit
        os._exit(0)

    # ---- Wiring ----
    def run(self) -> None:
        # Diagnostics
        dev = getattr(self.iface, 'devPath', None) or getattr(self.iface, 'port', None)
        print(f"[INFO] Using serial port: {dev}")
        # Attach iface callback
        def _iface_on_receive(packet, interface):
            self._handle_packet(packet)
        self.iface.onReceive = _iface_on_receive
        print("[INFO] Attached iface.onReceive callback")
        # PubSub too
        def _on_pub(packet=None, interface=None, **kw):
            self._handle_packet(packet)
        pub.subscribe(_on_pub, "meshtastic.receive")
        print("[INFO] Subscribed to meshtastic.receive")
        # Send request
        self.send_get()
        # Busy-wait until timeout
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
    ap = argparse.ArgumentParser(description="MiniHTTP client for server2")
    ap.add_argument("--path", default="/index.html", help="Path to fetch (default: /index.html)")
    ap.add_argument("--frag", type=int, default=None, help="Specific fragment to fetch (1-based)")
    ap.add_argument("--out", type=Path, default=None, help="Write response body to file")
    ap.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait before failing")
    args = ap.parse_args()

    client = MiniHttpClient(path=args.path, want_frag=args.frag, out_path=args.out, timeout=args.timeout)
    client.run()

if __name__ == "__main__":
    main()