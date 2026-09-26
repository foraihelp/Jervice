"""Restarts Jarvis (used after Settings changes the data folder, which is moved at the next start)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def restart() -> None:
    """Starts a new copy of Jarvis and exits this one. The new copy waits for this one to release the
    single-instance lock (see main.py), so the two never run side by side."""
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(sys.argv[0]).resolve())]
    subprocess.Popen(
        command,
        env={**os.environ, "JARVIS_RESTARTING": "1"},
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    os._exit(0)
