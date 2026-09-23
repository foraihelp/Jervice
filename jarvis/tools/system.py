"""System-level controls: volume, screenshots, locking the workstation.

Deliberately does NOT expose shutdown/restart as a tool the model can call
autonomously -- those are destructive and easy to trigger by a misheard
command. `lock_workstation` is included because it's safe (just requires
your password to undo) and useful ("Jarvis, lock my computer").
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("jarvis.tools.system")

# Brightness control only works for displays that expose WMI brightness
# methods -- in practice, laptop-internal panels (via their video driver's
# DDC/CI-equivalent ACPI interface), not most external/desktop monitors.
# Shelling out to PowerShell avoids adding a WMI-specific Python dependency
# just for this one feature.
_BRIGHTNESS_UNSUPPORTED_HINT = (
    "your display doesn't support software brightness control (common for "
    "external/desktop monitors -- only most laptop screens support this)"
)


def _run_powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "PowerShell command failed.")
    return result.stdout.strip()


def set_brightness(percent: float) -> str:
    """Sets screen brightness to `percent` (0-100), if the display supports it."""
    percent = int(max(0, min(100, round(float(percent)))))
    try:
        _run_powershell(
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1, {percent})"
        )
        return f"Brightness set to {percent}%."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to set brightness: %s", exc)
        return f"I couldn't change the brightness -- {_BRIGHTNESS_UNSUPPORTED_HINT}."


def get_brightness() -> str:
    try:
        output = _run_powershell(
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
        )
        return f"Current brightness is {output}%."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read brightness: %s", exc)
        return f"I couldn't read the brightness -- {_BRIGHTNESS_UNSUPPORTED_HINT}."


def set_volume(percent: float) -> str:
    """Sets system output volume to `percent` (0-100)."""
    percent = max(0.0, min(100.0, float(percent)))
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
        return f"Volume set to {int(percent)}%."
    except Exception as exc:
        logger.warning("Failed to set volume: %s", exc)
        return f"I couldn't change the volume: {exc}"


def get_volume() -> str:
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        level = volume.GetMasterVolumeLevelScalar()
        return f"Current volume is {round(level * 100)}%."
    except Exception as exc:
        logger.warning("Failed to read volume: %s", exc)
        return f"I couldn't read the volume: {exc}"


def take_screenshot(save_dir: str = "data/screenshots") -> str:
    from datetime import datetime

    from PIL import ImageGrab

    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_dir / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"

    img = ImageGrab.grab()
    img.save(filename)
    return f"Screenshot saved to {filename}."


def lock_workstation() -> str:
    try:
        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
        return "Locking the workstation."
    except Exception as exc:
        logger.warning("Failed to lock workstation: %s", exc)
        return f"I couldn't lock the workstation: {exc}"
