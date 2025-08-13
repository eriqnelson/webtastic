from dotenv import load_dotenv; load_dotenv()
import os
# Use the correct Meshtastic interface classes for each transport
from meshtastic.serial_interface import SerialInterface
from pubsub import pub
import time
from typing import Optional

def _api_get_node(iface) -> Optional[object]:
    try:
        return getattr(iface, "localNode", None)
    except Exception:
        return None

def _api_get_channel(node, index: int):
    # Returns a channel protobuf or None
    try:
        return node.getChannelByChannelIndex(index)
    except Exception:
        return None

def _api_set_channel(node, index: int, name: Optional[str] = None, psk: Optional[str] = None):
    """Use Meshtastic API to set channel fields in-place. Accepts hex (0x..) or base64 for psk."""
    try:
        # Newer API supports kwargs; guard for older versions
        if hasattr(node, "setChannel"):
            # setChannel(index, name=None, psk=None, uplinkEnabled=None, downlinkEnabled=None, )
            node.setChannel(index=index, name=name, psk=psk)
            return True
    except Exception:
        pass
    return False

def _api_set_url(node, url: str) -> bool:
    try:
        if hasattr(node, "setURL"):
            node.setURL(url)
            return True
    except Exception:
        pass
    return False

def apply_url_config(url: str):
    """Apply a Complete URL via Meshtastic API (no CLI)."""
    tmp_iface = get_radio_interface()
    try:
        node = _api_get_node(tmp_iface)
        if not node:
            return
        _api_set_url(node, url)
    finally:
        tmp_iface.close()

def _api_find_channel_index_by_name(node, name: str, max_channels: int = 8) -> Optional[int]:
    """Scan channel indices via API and return the index whose name matches."""
    for i in range(max_channels):
        ch = _api_get_channel(node, i)
        if not ch:
            continue
        ch_name = getattr(getattr(ch, "settings", ch), "name", "") or getattr(ch, "name", "")
        if ch_name == name:
            return i
    return None

DEFAULT_CHANNEL_INDEX = int(os.getenv("DEFAULT_CHANNEL_INDEX", 1))

def get_radio_interface():
    """Create a Meshtastic SerialInterface using .env devPath if provided, else auto-detect."""
    devpath = os.getenv("MESHTASTIC_PORT")
    if devpath:
        return SerialInterface(devPath=devpath)
    return SerialInterface()

class RadioInterface:
    @staticmethod
    def read_channel_config(index=DEFAULT_CHANNEL_INDEX):
        """Read channel config via Meshtastic API (preferred), fallback to CLI parse."""
        # API path
        try:
            tmp = get_radio_interface()
            node = _api_get_node(tmp)
            if node:
                ch = _api_get_channel(node, index)
                if ch:
                    # Protobuf fields may differ across versions; try common names
                    name = getattr(getattr(ch, "settings", ch), "name", "") or getattr(ch, "name", "")
                    psk = getattr(getattr(ch, "settings", ch), "psk", "") or getattr(ch, "psk", "")
                    tmp.close()
                    return {"name": name, "psk": psk}
            tmp.close()
        except Exception:
            pass
        return None

    @staticmethod
    def write_channel_config(name, psk, index=DEFAULT_CHANNEL_INDEX, ble=None, host=None, port=None):
        """Prefer Meshtastic API to update/create channel; fallback to CLI behavior."""
        # API path
        try:
            tmp = get_radio_interface()
            node = _api_get_node(tmp)
            if node:
                # If primary, update in place only
                if index == 0:
                    _api_set_channel(node, index, name=name, psk=psk)
                    tmp.close()
                    return
                # If channel exists, update; if not, add then set PSK without index
                ch = _api_get_channel(node, index)
                if ch:
                    _api_set_channel(node, index, name=name, psk=psk)
                else:
                    # Add a new channel; many builds expose addChannel(name) on node
                    added = False
                    if hasattr(node, "addChannel"):
                        try:
                            node.addChannel(name)
                            added = True
                        except Exception:
                            added = False
                    # If addChannel unavailable, attempt setChannel to a higher free index anyway
                    if not added:
                        _api_set_channel(node, index, name=name, psk=psk)
                tmp.close()
                return
            tmp.close()
        except Exception:
            pass

    def __init__(self):
        self.iface = get_radio_interface()
        self._subscribed = False
        self.default_channel_index = DEFAULT_CHANNEL_INDEX

    def send(self, message: str, channel_index: int = None):
        """Send a message to the specified Meshtastic channel."""
        idx = self.default_channel_index if channel_index is None else channel_index
        self.iface.sendText(message, channelIndex=idx)

    def on_receive(self, callback):
        """Register a callback to handle incoming messages."""
        if not self._subscribed:
            pub.subscribe(lambda packet, iface: callback(packet), "meshtastic.receive")
            self._subscribed = True

    def run_forever(self):
        """Keep the radio interface alive to receive messages."""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self):
        """Clean up the serial connection."""
        self.iface.close()

def configure_channel(index=DEFAULT_CHANNEL_INDEX):
    """Set up a Meshtastic channel using environment variables and connection type.
    Uses the Meshtastic CLI to read/write channel config before opening a Python interface
    to avoid serial-port lock conflicts.
    """
    name = os.getenv("MINIHTTP_CHANNEL_NAME", "webtastic")
    psk = os.getenv("MINIHTTP_CHANNEL_PSK", "0x8e2a4b7c5d1e3f6a9b0c2d4e6f8a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d9e1f2a")

    # If a Complete URL is provided, apply it deterministically before opening the interface
    seturl = os.getenv("MESHTASTIC_SETURL") or os.getenv("MESHTASTIC_CONFIG_URL")
    if seturl:
        apply_url_config(seturl)
    else:
        # Read current config BEFORE opening any interface (prevents port busy issues)
        current = RadioInterface.read_channel_config(index=index)
        # Only write if needed
        if (not current) or (current.get("name") != name) or (current.get("psk") != psk):
            RadioInterface.write_channel_config(name, psk, index=index)

    # Optionally return an open interface for immediate use
    radio = RadioInterface()
    # Try to discover the desired channel index by name; fall back to provided index
    try:
        node = _api_get_node(radio.iface)
        if node:
            resolved = _api_find_channel_index_by_name(node, name)
            radio.default_channel_index = resolved if resolved is not None else index
        else:
            radio.default_channel_index = index
    except Exception:
        radio.default_channel_index = index
    return radio