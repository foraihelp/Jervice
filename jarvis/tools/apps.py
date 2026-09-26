"""Open and close applications on Windows.

`open_app` finds an app the way the Windows Start menu does: an executable or
App Paths name (`os.startfile`, like the Run box), then a Start Menu shortcut
(apps installed as plug-in bundles, like Boris FX Mocha Pro, are only reachable
this way), then anything on PATH, then Microsoft Store apps. It opens things; it
does not run commands.

It also reports what really happened. An earlier version fell back to
`cmd /c start`, which reported success the moment cmd itself started, even for
a bogus name or a whole PowerShell script the AI model passed in as the "app
name" -- so a task could silently do nothing while being reported as done. Now a
missing app, a broken shortcut, and an app that never appeared are all said out
loud.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

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

_START_MENU_DIRS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]
# Shortcuts that sit next to an app but aren't the app.
_NOT_THE_APP = ("uninstall", "readme", "release notes", "user guide", "userguide", "manual", "help",
                "documentation", "license", "website", "support", "changelog", "what s new")
_LAUNCH_CONFIRM_SECONDS = 8
_STORE_APPS_TTL = 600
_store_apps_cache: tuple[float, list[dict]] = (0.0, [])


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _rank(candidate: str, query: str, words: list[str]) -> Optional[int]:
    """0 = same name, 1 = starts with it, 2 = contains all its words, None = no match."""
    if candidate == query:
        return 0
    if candidate.startswith(query):
        return 1
    if words and all(w in candidate for w in words):
        return 2
    return None


def _find_shortcut(name: str) -> Optional[Path]:
    """The Start Menu shortcut that best matches `name`, the way the Windows Start
    menu would find it."""
    query = _norm(name)
    if not query:
        return None
    words = query.split()
    asked_for_extras = any(term in query for term in _NOT_THE_APP)
    best: Optional[tuple[tuple[int, int], Path]] = None
    for folder in _START_MENU_DIRS:
        if not folder.is_dir():
            continue
        for lnk in folder.rglob("*.lnk"):
            stem = _norm(lnk.stem)
            if not asked_for_extras and any(term in stem for term in _NOT_THE_APP):
                continue
            rank = _rank(stem, query, words)
            if rank is None:
                continue
            key = (rank, len(stem))
            if best is None or key < best[0]:
                best = (key, lnk)
    return best[1] if best else None


def _shortcut_target(lnk: Path) -> Optional[str]:
    """Where a shortcut points, or None if that can't be read."""
    quoted = str(lnk).replace("'", "''")  # PowerShell single-quoted string: only ' needs escaping
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(New-Object -ComObject WScript.Shell).CreateShortcut('{quoted}').TargetPath"],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout.strip() or None
    except Exception:  # noqa: BLE001
        logger.debug("Could not read shortcut %s", lnk, exc_info=True)
        return None


def _find_store_app(name: str) -> Optional[dict]:
    """A Microsoft Store / packaged app from the Start menu's app list (Calculator,
    Photos, ...). These have no .exe or .lnk to open."""
    global _store_apps_cache
    stamp, apps = _store_apps_cache
    if time.time() - stamp > _STORE_APPS_TTL:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            data = json.loads(result.stdout or "[]")
            apps = data if isinstance(data, list) else [data]
        except Exception:  # noqa: BLE001
            logger.debug("Get-StartApps failed", exc_info=True)
            apps = []
        _store_apps_cache = (time.time(), apps)

    query = _norm(name)
    words = query.split()
    best: Optional[tuple[tuple[int, int], dict]] = None
    for app in apps:
        app_name = _norm(str(app.get("Name", "")))
        rank = _rank(app_name, query, words)
        if rank is None:
            continue
        key = (rank, len(app_name))
        if best is None or key < best[0]:
            best = (key, app)
    return best[1] if best else None


def _running(exe: str) -> bool:
    wanted = os.path.normcase(exe)
    for proc in psutil.process_iter(["exe"]):
        try:
            if os.path.normcase(proc.info.get("exe") or "") == wanted:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _appeared(exe: str) -> bool:
    """Waits a few seconds for a process running `exe` to exist."""
    deadline = time.time() + _LAUNCH_CONFIRM_SECONDS
    while time.time() < deadline:
        if _running(exe):
            return True
        time.sleep(0.5)
    return _running(exe)


def open_app(name: str) -> str:
    """Opens an application by name (e.g. "notepad", "chrome", "Mocha Pro"), or a
    file or folder by path. Returns what actually happened, including failure."""
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

    # Not an executable or path Windows knows by that name: look it up the way the
    # Start menu does.
    shortcut = _find_shortcut(name)
    if shortcut is not None:
        target = _shortcut_target(shortcut)
        if target and not os.path.exists(target):
            return f"I found '{shortcut.stem}' in the Start menu, but the program it points to is missing: {target}"
        exe = target if target and target.lower().endswith(".exe") else None
        was_running = bool(exe and _running(exe))
        try:
            os.startfile(str(shortcut))  # type: ignore[attr-defined]
        except OSError as exc:
            logger.warning("Failed to open shortcut %s: %s", shortcut, exc)
            return f"I found '{shortcut.stem}' but couldn't start it: {exc.strerror or exc}"
        if exe is None or was_running or _appeared(exe):
            return f"Opened {shortcut.stem}."
        return (f"I started {shortcut.stem}, but I couldn't confirm that it opened. "
                "It may still be loading, or it may have failed to start.")

    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        try:
            os.startfile(found)  # type: ignore[attr-defined]
            return f"Opened {name}."
        except OSError as exc:
            logger.warning("Failed to open %r (%s): %s", name, found, exc)

    store = _find_store_app(name)
    if store is not None and store.get("AppID"):
        try:
            os.startfile("shell:AppsFolder\\" + str(store["AppID"]))  # type: ignore[attr-defined]
            return f"Opened {store['Name']}."
        except OSError as exc:
            logger.warning("Failed to open store app %r: %s", store, exc)

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
