

#!/usr/bin/env python3
"""
server3 — MiniHTTP server using listener & publisher modules (API-only, serial-only)

Highlights
  • Uses RadioInterface for single-open serial
  • Uses listener.start_listener() for robust RX
  • Uses publisher.send_fragments()/send_json()/send_text() for TX
  • Safe HTML root resolution (WEBTASTIC_HTML_DIR or <repo>/html)
  • Defaults / → index.html, adds .html fallback, blocks path traversal
  • Optional DEBUG/echo/heartbeat toggles via env

Env knobs
  MESHTASTIC_PORT, DEFAULT_CHANNEL_INDEX    
  WEBTASTIC_HTML_DIR      → override HTML root
  SERVER_DEBUG_BEACON=1   → send periodic beacons
  SERVER_ECHO_ALL=1       → echo any inbound JSON to /echo
  LISTENER_DEBUG=1        → verbose RX (from listener.py)
  PUBLISHER_DEBUG=1       → verbose TX (from publisher.py)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from pubsub import pub

# Local modules
from radio import RadioInterface
from listener import start_listener
from publisher import send_text, send_json, send_fragments
from fragment import fragment_html_file

TRUTHY = {"1", "true", "yes", "on", "y"}


def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _iface_of(radio):
    return getattr(radio, "iface", radio)


# --------------------- Filesystem helpers ---------------------

def resolve_html_root() -> Path:
    base_dir = Path(__file__).resolve().parent
    env_html = os.getenv("WEBTASTIC_HTML_DIR")
    if env_html:
        html_dir = Path(env_html).expanduser().resolve()
        print(f"[INFO] HTML dir (env): {html_dir}")
    else:
        html_dir = (base_dir / "html").resolve()
        print(f"[INFO] HTML dir (default): {html_dir}")
    print(f"[INFO] Base dir: {base_dir}")
    print(f"[INFO] HTML dir exists={html_dir.exists()}")
    if not html_dir.exists():
        print("[WARN] HTML directory does not exist. Create it or set WEBTASTIC_HTML_DIR.")
    return html_dir


def normalize_request_path(req_path: str) -> str:
    raw = (req_path or "").strip()
    raw = unquote(raw)
    if raw.startswith("/html/"):
        rel = raw[len("/html/"):]
    else:
        rel = raw.lstrip("/")
    # Directory → index.html
    if rel == "" or rel.endswith("/"):
        rel = rel + "index.html"
    return rel


def secure_candidate(html_dir: Path, rel: str) -> Optional[Path]:
    candidate = (html_dir / rel).resolve()
    # Prevent path traversal outside html_dir
    if not str(candidate).startswith(str(html_dir)):
        print(f"[WARN] Rejected path traversal: {candidate}")
        return None
    # If missing and no extension, try adding .html
    if not candidate.exists() and "." not in Path(rel).name:
        alt = (html_dir / (rel + ".html")).resolve()
        if str(alt).startswith(str(html_dir)) and alt.exists():
            print(f"[INFO] FS alt match: {alt}")
            return alt
    return candidate


# --------------------- Server logic ---------------------

def main() -> int:
    radio = RadioInterface()
    iface = _iface_of(radio)

    # Diagnostics
    try:
        dev = getattr(iface, "devPath", None) or getattr(iface, "port", None)
        print(f"[INFO] Interface: {iface.__class__.__name__} dev={dev}")
    except Exception:
        pass

    html_root = resolve_html_root()

    # Beacon thread (optional)
    if _is_on("SERVER_DEBUG_BEACON"):
        def _beacon_loop():
            n = 0
            while True:
                try:
                    send_json(radio, {"type": "RESP", "path": "/beacon", "frag": 1, "of_frag": 1, "data": f"server3_beacon_{n}"})
                    print(f"[DEBUG] TX(beacon): server3_beacon_{n}")
                    n += 1
                except Exception as e:
                    print(f"[WARN] Beacon error: {e}")
                time.sleep(5)
        threading.Thread(target=_beacon_loop, daemon=True).start()
        print("[INFO] Debug beacon enabled (SERVER_DEBUG_BEACON=1)")

    # Connection established notice
    def _on_conn(interface=None, **kw):
        print("[INFO] Connection established (pubsub)")
        try:
            send_json(radio, {"type": "RESP", "path": "/hello", "frag": 1, "of_frag": 1, "data": "server3_online"})
        except Exception as e:
            print(f"[WARN] hello send failed: {e}")
    pub.subscribe(_on_conn, "meshtastic.connection.established")

    # RX handler using listener module
    def on_json(msg: dict, packet: Optional[dict] = None):
        try:
            # Optional echo-all for debugging
            if _is_on("SERVER_ECHO_ALL"):
                echo_env = {
                    "type": "RESP",
                    "path": msg.get("path", "/echo"),
                    "frag": 1,
                    "of_frag": 1,
                    "data": f"echo: {json.dumps(msg)[:200]}"
                }
                send_json(radio, echo_env)

            # We only act on GET
            if (msg or {}).get("type") != "GET":
                return

            req_path = (msg or {}).get("path") or "/"
            rel = normalize_request_path(req_path)
            cand = secure_candidate(html_root, rel)
            if cand is None:
                send_json(radio, {"type": "RESP", "path": req_path, "frag": 1, "of_frag": 1, "data": "400: invalid path"})
                return

            print(f"[INFO] FS lookup: req='{req_path}' → abs='{cand}'")
            if not cand.exists():
                # Show listing once for troubleshooting
                try:
                    listing = ", ".join(sorted(p.name for p in html_root.iterdir()))
                except Exception:
                    listing = "(unavailable)"
                print(f"[WARN] File not found: {cand}")
                print(f"[WARN] HTML dir exists={html_root.exists()} contents=[{listing}]")
                send_json(radio, {"type": "RESP", "path": req_path, "frag": 1, "of_frag": 1, "data": f"404: {req_path} not found"})
                return

            # Fragment and send
            try:
                chunks = fragment_html_file(str(cand))
            except Exception as e:
                print(f"[ERROR] Fragment read failed: {e}")
                send_json(radio, {"type": "RESP", "path": req_path, "frag": 1, "of_frag": 1, "data": f"500: read error"})
                return

            total = len(chunks)
            print(f"[INFO] GET {req_path} → {total} fragment(s)")
            send_fragments(radio, req_path, chunks)
        except Exception as e:
            print(f"[ERROR] handle msg failed: {e} msg={msg}")

    start_listener(radio, on_json)
    print("[INFO] server3 READY: listening for GETs…")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[INFO] Stopped")
        try:
            iface.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())