"""GitHub-Releases-based update checker for the packaged .exe build.

Same idea as the sibling Video Converter app's electron-updater flow (a
version badge plus an update pill in the header) -- reimplemented for a
Python/PyInstaller app instead of Electron, since there's no npm-style
auto-updater available here. This hits the GitHub Releases API directly
and, when a newer release is found, downloads its .exe asset and runs it
(matching installer/jarvis.iss's fixed AppId, so it upgrades in place)
instead of electron-updater's differential binary patching.

Only meaningful for a frozen build -- like Video Converter's
`app.isPackaged` guard, `python main.py` always reports 'unsupported'.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from jarvis import __version__ as CURRENT_VERSION

logger = logging.getLogger("jarvis.updater")

GITHUB_REPO = "foraihelp/Jervice"
_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_USER_AGENT = "Jarvis-Updater"
_REQUEST_TIMEOUT = 10

# Mirrors Video Converter's `latestStatus` module variable + getStatus IPC
# handler, so the UI can ask "what's the current status?" on load instead
# of only reacting to push events.
_latest_status: dict[str, Any] = {"state": "idle"}
_downloaded_installer_path: Optional[Path] = None


def get_latest_status() -> dict[str, Any]:
    return _latest_status


def _parse_version(v: str) -> tuple[int, ...]:
    """"v1.2.3" -> (1, 2, 3). Non-numeric releases sort as (0,) (oldest)."""
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def _fetch_latest_release() -> dict[str, Any]:
    req = urllib.request.Request(
        _RELEASES_API,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_updates(on_status: Callable[[dict[str, Any]], None]) -> None:
    """Runs the whole check (and, if a newer release exists, the download)
    in a daemon thread, calling on_status(status) as it progresses --
    checking -> available -> downloading -> downloaded, or not-available /
    error. Safe to call repeatedly (e.g. from a "Check for Updates" click)."""
    global _latest_status

    if not getattr(sys, "frozen", False):
        _latest_status = {"state": "unsupported"}
        on_status(_latest_status)
        return

    def _set(status: dict[str, Any]) -> None:
        global _latest_status
        _latest_status = status
        on_status(status)

    def _run() -> None:
        _set({"state": "checking"})
        try:
            release = _fetch_latest_release()
        except Exception as exc:  # noqa: BLE001 - never let a network hiccup crash the app
            logger.warning("Update check failed: %s", exc)
            _set({"state": "error", "message": str(exc)})
            return

        remote_version = release.get("tag_name", "")
        if not remote_version or not is_newer(remote_version, CURRENT_VERSION):
            _set({"state": "not-available"})
            return

        asset = next(
            (a for a in release.get("assets", []) if a.get("name", "").lower().endswith(".exe")),
            None,
        )
        if asset is None:
            _set({"state": "error", "message": "Latest release has no .exe installer attached."})
            return

        _set({"state": "available", "version": remote_version})
        _download(asset["browser_download_url"], remote_version, _set)

    threading.Thread(target=_run, daemon=True).start()


def _download(url: str, version: str, set_status: Callable[[dict[str, Any]], None]) -> None:
    global _downloaded_installer_path
    try:
        dest = Path(tempfile.gettempdir()) / f"JarvisSetup-{version}.exe"
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0) or None
            written = 0
            last_reported = -1.0
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if total:
                    # Push at most once per whole percentage point -- a
                    # ~110MB installer in 256KB chunks is ~440 chunks, and
                    # pushing a JS eval for every single one floods the
                    # pywebview bridge with no benefit to what's visible.
                    percent = round(written / total * 100, 1)
                    if percent - last_reported >= 1.0:
                        set_status({"state": "downloading", "percent": percent})
                        last_reported = percent
        _downloaded_installer_path = dest
        set_status({"state": "downloaded", "version": version})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Update download failed: %s", exc)
        set_status({"state": "error", "message": str(exc)})


def install_and_restart() -> None:
    """Launches the downloaded installer silently (it upgrades in place,
    reusing the previous install's location/scope via jarvis.iss's fixed
    AppId) and quits this process immediately so the installer isn't
    blocked by file locks on the running Jarvis.exe. Mirrors
    electron-updater's quitAndInstall()."""
    if _downloaded_installer_path is None or not _downloaded_installer_path.exists():
        logger.warning("install_and_restart() called with no downloaded installer available.")
        return
    subprocess.Popen(
        [str(_downloaded_installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        close_fds=True,
    )
    os._exit(0)
