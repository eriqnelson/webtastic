#!/usr/bin/env python3
"""
publisher.py — generic MiniHTTP/Meshtastic publisher helpers (API-only, serial-only)

Mirrors the successful send logic from server2:
  • Prefer RadioInterface.send() and fall back to iface.sendText(channelIndex=N)
  • Pull DEFAULT_CHANNEL_INDEX from env when needed
  • Convenience senders for plain text, JSON, and fragment envelopes
  • Optional debug logging via PUBLISHER_DEBUG=1

Usage examples:

    from publisher import (
        send_text,
        send_json,
        send_envelope,
        send_fragments,
    )

    # send plain text
    send_text(radio, "hello mesh")

    # send JSON
    send_json(radio, {"type": "PING"})

    # send one MiniHTTP envelope
    send_envelope(radio, path="/index.html", frag=1, of_frag=3, data="...")

    # send many fragments (list[str]) for a given path
    send_fragments(radio, "/index.html", ["chunk1", "chunk2"])  # sends envelopes 1/2, 2/2
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

# --- Compatibility helpers to make subscriber parsing easier -----------------
MINIHTTP_VERSION = 1
TRAILER = ""  # reserved for future use

# Meshtastic text frames have a practical size budget; keep JSON compact
# Use compact separators and avoid whitespace.
_def_json_kwargs = dict(separators=(",", ":"), ensure_ascii=False)

TRUTHY = {"1", "true", "yes", "on", "y"}


def _is_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUTHY


def _stamp_enabled() -> bool:
    """If enabled, prefix text with a magic header so the subscriber can
    detect/route MiniHTTP frames quickly without costly JSON probing.
    Set PUBLISHER_STAMP=1 to enable.
    """
    return _is_on("PUBLISHER_STAMP")


def _default_channel_index() -> int:
    env = (os.getenv("DEFAULT_CHANNEL_INDEX") or "1").strip()
    try:
        return int(env)
    except Exception:
        return 1


def _iface_of(radio):
    return getattr(radio, "iface", radio)


def _json_dumps(obj: Any) -> str:
    """Compact, UTF-8 JSON string suitable for Meshtastic text payloads."""
    return json.dumps(obj, **_def_json_kwargs)


def _stamp_text(text: str) -> str:
    """Optionally add a tiny header the subscriber can key off of.
    Format: "MH1 " + text
    """
    if _stamp_enabled():
        return f"MH{MINIHTTP_VERSION} " + text
    return text


def _build_env(**fields) -> dict:
    env = {
        "ver": MINIHTTP_VERSION,
        "type": fields.pop("type_", None) or fields.pop("type", None) or "RESP",
        "ts": int(__import__("time").time()),
        **fields,
    }
    # Include channel hint so the subscriber can optionally filter/log
    env.setdefault("chan", _default_channel_index())
    return env


def _send_text_core(radio, text: str) -> None:
    """Prefer RadioInterface.send(); fall back to iface.sendText(channelIndex=N)."""
    debug = _is_on("PUBLISHER_DEBUG")
    text = _stamp_text(text)

    # Try high-level send first (lets the library choose the right channel)
    try:
        if hasattr(radio, "send"):
            if debug:
                print(f"[PUB] TX (radio.send) len={len(text)} {text[:80]!r}")
            radio.send(text)
            return
    except Exception as e:
        print(f"[PUB] WARN: radio.send failed, will fall back to iface.sendText: {e}")

    # Fallback path: explicit channel
    ch = _default_channel_index()
    iface = _iface_of(radio)
    if debug:
        try:
            dev = getattr(iface, 'devPath', None) or getattr(iface, 'port', None)
        except Exception:
            dev = None
        print(f"[PUB] TX (fallback) ch={ch} dev={dev} len={len(text)} {text[:80]!r}")
    iface.sendText(text, channelIndex=int(ch))


# --- Public helpers ---------------------------------------------------------

def send_text(radio, text: str) -> None:
    """Send a plain text message (string)."""
    if not isinstance(text, str):
        text = str(text)
    _send_text_core(radio, text)


def send_json(radio, obj: Any) -> None:
    """Serialize obj as compact JSON and send as text (with optional MH1 stamp)."""
    try:
        payload = _json_dumps(obj)
    except Exception as e:
        raise ValueError(f"Object not JSON-serializable: {e}")
    _send_text_core(radio, payload)


def send_envelope(
    radio,
    *,
    path: str,
    frag: int,
    of_frag: int,
    data: str,
    type_: str = "RESP",
) -> None:
    """Send a single MiniHTTP envelope (RESP by default) with a tiny schema.

    Envelope schema (all strings/numbers):
      {"ver":1, "type":"RESP", "path":"/index.html", "frag":1, "of":3,
       "chan":<int>, "data":"...", "ts":<unix>}
    """
    if not isinstance(data, str):
        data = str(data)
    env = _build_env(path=path, frag=int(frag), of=int(of_frag), data=data, type_=type_)
    if _is_on("PUBLISHER_DEBUG"):
        print(f"[PUB] ENV TX path={path} {frag}/{of_frag} len={len(data)}")
    send_json(radio, env)


def send_fragments(radio, path: str, fragments: Iterable[str], *, type_: str = "RESP") -> int:
    """Send a sequence of string fragments as MiniHTTP envelopes.

    Returns the number of fragments sent.
    """
    frags = list(fragments)
    total = len(frags)
    for idx, chunk in enumerate(frags, start=1):
        send_envelope(radio, path=path, frag=idx, of_frag=total, data=chunk, type_=type_)
    if _is_on("PUBLISHER_DEBUG"):
        print(f"[PUB] FRAGS TX path={path} total={total}")
    return total