"""Open and close applications on Windows.

`open_app` uses `os.startfile`, which is exactly what double-clicking a
Start Menu shortcut or typing a name into the Windows Run box does -- it
resolves app names, .exe names, and file paths via the same App Paths /
PATH resolution Windows itself uses, so it works for almost anything
installed normally (notepad, calc, chrome, spotify, a project folder, etc.)
without needing a hardcoded path table.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

import psutil

logger = logging.getLogger("jarvis.tools.apps")


def open_app(name: str) -> str:
    """Attempts to launch an application by name (e.g. "notepad", "chrome",
    "spotify") or by full path. Returns a human-readable result string."""
    name = name.strip()
    if not name:
        return "No application name given."

    try:
        os.startfile(name)  # type: ignore[attr-defined]  (Windows-only)
        return f"Opened {name}."
    except OSError:
        pass

    # Fall back to letting the shell resolve it (handles things like
    # "notepad.exe" vs "notepad", or apps registered only on PATH).
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", name],
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return f"Opened {name}."
    except Exception as exc:
        logger.warning("Failed to open app %r: %s", name, exc)
        return f"I couldn't find or launch '{name}'."


def close_app(name: str) -> str:
    """Terminates all running processes whose name matches `name`
    (case-insensitive, partial match on the process/executable name)."""
    name = name.strip().lower().removesuffix(".exe")
    if not name:
        return "No application name given."

    matched = []
    for proc in psutil.process_iter(["pid", "name"]):
        proc_name = (proc.info.get("name") or "").lower().removesuffix(".exe")
        if name in proc_name:
            matched.append(proc)

    if not matched:
        return f"No running process matching '{name}' found."

    for proc in matched:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.warning("Could not terminate %s: %s", proc, exc)

    gone, alive = psutil.wait_procs(matched, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return f"Closed {len(matched)} process(es) matching '{name}'."


def list_running_apps(limit: int = 30) -> str:
    """Returns a short list of currently visible/running application
    process names, deduplicated."""
    names = set()
    for proc in psutil.process_iter(["name"]):
        n = proc.info.get("name")
        if n:
            names.add(n)
    sample = sorted(names)[:limit]
    return ", ".join(sample) if sample else "No processes found."
