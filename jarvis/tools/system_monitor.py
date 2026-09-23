"""Hardware/system metrics (CPU, RAM, disk, battery) via psutil -- already
a dependency (see apps.py's process listing/termination)."""

from __future__ import annotations

import logging

import psutil

logger = logging.getLogger("jarvis.tools.system_monitor")


def get_system_status() -> str:
    """Returns a short human-readable summary of CPU load, memory use,
    main-drive disk use, and battery status (if this machine has one)."""
    parts = []

    try:
        cpu = psutil.cpu_percent(interval=0.3)
        parts.append(f"CPU at {cpu:.0f}%")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read CPU usage: %s", exc)

    try:
        mem = psutil.virtual_memory()
        parts.append(f"RAM at {mem.percent:.0f}% ({_gb(mem.used)} of {_gb(mem.total)} GB used)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read memory usage: %s", exc)

    try:
        disk = psutil.disk_usage("C:\\")
        parts.append(f"main drive at {disk.percent:.0f}% full ({_gb(disk.free)} GB free)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read disk usage: %s", exc)

    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            state = "charging" if battery.power_plugged else "on battery"
            parts.append(f"battery at {battery.percent:.0f}% ({state})")
        # battery is None on desktops with no battery -- omit, not an error
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read battery status: %s", exc)

    if not parts:
        return "I couldn't read any system metrics right now."
    return "; ".join(parts) + "."


def _gb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 ** 3):.1f}"
