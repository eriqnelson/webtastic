#!/usr/bin/env python3
"""
provision.py — One-shot Meshtastic provisioning for webtastic nodes (API-only, serial-only)

Goals
-----
- Apply a Complete URL deterministically (no CLI, no TCP fallback).
- Optionally enforce LoRa region & modem preset via API.
- Optionally set/update a single channel (name+psk) at a given index via API.
- Idempotent: reads current state first and only writes if different.
- Verbose, with --dry-run support.

Usage examples
--------------
# Apply a golden Complete URL from the environment
MESHTASTIC_PORT=/dev/tty.SLAB_USBtoUART \
MESHTASTIC_SETURL="https://meshtastic.org/e/#CgMS..." \
python3 provision.py --apply-url

# Apply an explicit URL (overrides env)
python3 provision.py --set-url "https://meshtastic.org/e/#CgMS..."

# Enforce region/preset only (no URL changes)
python3 provision.py --enforce-lora --region US --preset LONG_FAST

# Update a single channel at index 1
python3 provision.py --set-channel --index 1 --name webtastic \
    --psk 0x8e2a4b7c5d1e3f6a9b0c2d4e6f8a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d9e1f2a

# Dry-run to see what would change
python3 provision.py --apply-url --enforce-lora --set-channel --index 1 --name webtastic --psk 0x... --dry-run
"""

import argparse
import os
import sys
from typing import Optional

from radio import (
    get_radio_interface,
)

# We use API-only helpers via the live interface

try:
    from meshtastic.protobuf import config_pb2
except Exception:
    config_pb2 = None  # We will guard usage

# -------------------------- Logging helpers --------------------------

def log(msg: str, *, level: str = "INFO", quiet: bool = False) -> None:
    if quiet:
        return
    print(f"[{level}] {msg}")

# -------------------------- Local API helpers --------------------------

def _api_get_node(iface):
    try:
        return getattr(iface, "localNode", None)
    except Exception:
        return None

def _api_get_channel(node, index: int):
    try:
        return node.getChannelByChannelIndex(index)
    except Exception:
        return None

def _channel_name_psk(ch) -> tuple[str, str]:
    s = getattr(ch, "settings", ch)
    name = getattr(s, "name", "") or getattr(ch, "name", "")
    psk = getattr(s, "psk", "") or getattr(ch, "psk", "")
    return name, psk

def _ensure_url(node, url: str, *, dry_run=False) -> bool:
    """Return True if we changed the device URL; False if already matching."""
    same = False
    try:
        current = node.getURL(includeAll=True)
        if current and current.strip() == url.strip():
            same = True
    except Exception:
        pass
    if same:
        return False
    if dry_run:
        return True
    # Apply via API
    node.setURL(url)
    return True

def _ensure_lora(node, region: Optional[str], preset: Optional[str], *, dry_run=False) -> bool:
    if not node or not getattr(node, "localConfig", None):
        return False
    if config_pb2 is None:
        raise RuntimeError("meshtastic.protobuf.config_pb2 not available; cannot enforce LoRa settings")
    lora = node.localConfig.lora
    changed = False
    if region:
        try:
            val = config_pb2.Config.LoRaConfig.Region.Value(region)
            if lora.region != val:
                if not dry_run:
                    lora.region = val
                changed = True
        except Exception:
            raise SystemExit(f"Unknown region '{region}'. Valid values depend on firmware.")
    if preset:
        try:
            val = config_pb2.Config.LoRaConfig.ModemPreset.Value(preset)
            if lora.modem_preset != val:
                if not dry_run:
                    lora.modem_preset = val
                changed = True
        except Exception:
            raise SystemExit(f"Unknown modem preset '{preset}'.")
    if changed and not dry_run:
        node.writeConfig("lora")
    return changed

def _ensure_channel(node, *, index: int, name: str, psk: str, dry_run=False) -> bool:
    """Ensure channel at index has the given name/psk. Updates in place; adds if missing."""
    changed = False
    ch = _api_get_channel(node, index)
    if ch:
        cur_name, cur_psk = _channel_name_psk(ch)
        if cur_name != name or cur_psk != psk:
            changed = True
            if not dry_run:
                # setChannel(index, name=None, psk=None, ...)
                node.setChannel(index=index, name=name, psk=psk)
    else:
        changed = True
        if not dry_run:
            # Prefer addChannel(name) if available, otherwise setChannel will create/update
            if hasattr(node, "addChannel"):
                try:
                    node.addChannel(name)
                except Exception:
                    pass
            node.setChannel(index=index, name=name, psk=psk)
    return changed

# -------------------------- Main --------------------------

def main():
    parser = argparse.ArgumentParser(description="Provision Meshtastic nodes (API-only)")
    # URL provisioning
    parser.add_argument("--apply-url", action="store_true", help="Apply Complete URL from env or --set-url")
    parser.add_argument("--set-url", type=str, help="Complete URL to apply (overrides MESHTASTIC_SETURL)")
    # LoRa enforcement
    parser.add_argument("--enforce-lora", action="store_true", help="Enforce LoRa region/preset via API")
    parser.add_argument("--region", type=str, help="LoRa region (e.g., US, EU_868)")
    parser.add_argument("--preset", type=str, help="LoRa modem preset (e.g., LONG_FAST)")
    # Channel
    parser.add_argument("--set-channel", action="store_true", help="Set/update a single channel by index")
    parser.add_argument("--index", type=int, default=1, help="Channel index (default 1)")
    parser.add_argument("--name", type=str, help="Channel name")
    parser.add_argument("--psk", type=str, help="Channel PSK (0xHEX or base64)")

    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing")
    parser.add_argument("--quiet", action="store_true", help="Reduce output")

    args = parser.parse_args()

    # Resolve inputs
    set_url = args.set_url or os.getenv("MESHTASTIC_SETURL") or os.getenv("MESHTASTIC_CONFIG_URL")
    if args.apply_url and not set_url:
        print("[ERROR] --apply-url requested but no URL provided (use --set-url or MESHTASTIC_SETURL)")
        sys.exit(2)

    if args.set_channel and (not args.name or not args.psk):
        print("[ERROR] --set-channel requires --name and --psk")
        sys.exit(2)

    # Open serial interface (env MESHTASTIC_PORT recommended to avoid TCP fallback)
    iface = get_radio_interface()
    try:
        node = _api_get_node(iface)
        if not node:
            print("[ERROR] Serial interface did not initialize (no localNode). Set MESHTASTIC_PORT to your /dev/tty.* path.")
            sys.exit(2)

        changed_any = False

        # URL
        if args.apply_url:
            did = _ensure_url(node, set_url, dry_run=args.dry_run)
            log(("Would apply URL" if args.dry_run else "Applied URL") if did else "URL already matches", quiet=args.quiet)
            changed_any = changed_any or did

        # LoRa
        if args.enforce_lora:
            region = args.region or os.getenv("MESHTASTIC_LORA_REGION")
            preset = args.preset or os.getenv("MESHTASTIC_LORA_MODEM_PRESET")
            did = _ensure_lora(node, region, preset, dry_run=args.dry_run)
            if region or preset:
                log(("Would update LoRa" if args.dry_run else "Updated LoRa") if did else "LoRa already matches", quiet=args.quiet)
            else:
                log("No region/preset provided; skipping LoRa changes", level="WARN", quiet=args.quiet)
            changed_any = changed_any or did

        # Channel
        if args.set_channel:
            did = _ensure_channel(node, index=args.index, name=args.name, psk=args.psk, dry_run=args.dry_run)
            log(("Would update channel" if args.dry_run else "Updated channel") if did else "Channel already matches", quiet=args.quiet)
            changed_any = changed_any or did

        if args.dry_run:
            log("Dry-run complete.", quiet=args.quiet)
        else:
            log("Provisioning complete." if changed_any else "No changes were necessary.", quiet=args.quiet)

    finally:
        try:
            iface.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
