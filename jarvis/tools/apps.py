"""Open and close applications on Windows.

`open_app` uses `os.startfile`, which is exactly what double-clicking a
Start Menu shortcut or typing a name into the Windows Run box does -- it
resolves app names, .exe names, and file paths via the same App Paths /
PATH resolution Windows itself uses, so it works for almost anything
installed normally (notepad, calc, chrome, spotify, a project folder, etc.)
without needing a hardcoded path table.

It opens things; it does not run commands. An earlier version fell back to
`cmd /c start`, which reported success the moment cmd itself started, even for
a bogus name or a whole PowerShell script the AI model passed in as the "app
name" -- so a task could silently do nothing while being reported as done.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

import psutil

logger = logging.getLogger("jarvis.tools.apps")

# Letters, digits, spaces, and a few characters real app names use. Anything with
# quotes, pipes, redirects, semicolons, $ and so on is a command, not a name.
_APP_NAME = re.compile(r"^[\w .+()'-]{1,80}$")
_SETTINGS_URI = re.compile(r"^ms-[a-z]+:[\w/?=.-]*$", re.IGNORECASE)
_REFUSAL = (
    "I can only open an app by its name (like 'notepad') or a file or folder path. "
    "I can't run commands or scripts, or pass options to a program."
)


def open_app(name: str) -> str:
    """Opens an application by name (e.g. "notepad", "chrome", "spotify"), or a file
    or folder by path. Returns what actually happened, including failure."""
    name = name.strip().strip('"')
    if not name:
        return "No application name given."

    is_path = False
    try:
        is_path = Path(name).expanduser().exists()
    except OSError:
        pass

    if not is_path:
        if not (_APP_NAME.match(name) or _SETTINGS_URI.match(name)):
            return _REFUSAL
        # "chrome --incognito", "cmd /c ..." : options make it a command, not a name.
        if any(token.startswith(("-", "/")) for token in name.split()[1:]):
            return _REFUSAL

    try:
        os.startfile(name)  # type: ignore[attr-defined]  (Windows-only)
        return f"Opened {name}."
    except OSError:
        pass

    # Some apps are on PATH but not registered with the shell under the bare name.
    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        try:
            os.startfile(found)  # type: ignore[attr-defined]
            return f"Opened {name}."
        except OSError as exc:
            logger.warning("Failed to open %r (%s): %s", name, found, exc)

    return f"I couldn't find an app or file called '{name}'."


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
