"""Audio device lookup by name, via PortAudio (sounddevice).

Devices are matched by case-insensitive name substring rather than a numeric
index because PortAudio indexes shift whenever a device (e.g. a Bluetooth
headset) connects or disconnects, while names stay stable. Only the default
host API's devices are listed (normally MME on Windows) so each physical
device appears once instead of once per host API.
"""

from __future__ import annotations

import logging
from typing import Optional

import sounddevice as sd

logger = logging.getLogger("jarvis.devices")

_VIRTUAL_NAMES = ("sound mapper", "primary sound")


def _candidates(kind: str) -> list[tuple[int, str]]:
    channels_key = "max_input_channels" if kind == "input" else "max_output_channels"
    devices = sd.query_devices()
    try:
        indexes = sd.query_hostapis(sd.default.hostapi)["devices"]
    except Exception:
        indexes = range(len(devices))
    result = []
    for i in indexes:
        d = devices[i]
        name = str(d["name"])
        if d[channels_key] > 0 and not any(v in name.lower() for v in _VIRTUAL_NAMES):
            result.append((i, name))
    return result


def list_devices(kind: str) -> list[str]:
    """Unique device names for `kind` ("input" or "output")."""
    seen: list[str] = []
    for _, name in _candidates(kind):
        if name not in seen:
            seen.append(name)
    return seen


def resolve_device(kind: str, wanted: str) -> Optional[int]:
    """PortAudio index of the first `kind` device matching `wanted`, or None
    (meaning "use the system default") if `wanted` is blank or nothing
    matches. Names are matched in both directions because MME truncates long
    device names, so a full name saved from elsewhere may be longer than what
    PortAudio reports."""
    wanted = (wanted or "").strip().lower()
    if not wanted:
        return None
    for index, name in _candidates(kind):
        low = name.lower()
        if wanted in low or (len(low) >= 8 and low in wanted):
            return index
    logger.warning("No %s device matching %r found; using the system default.", kind, wanted)
    return None
