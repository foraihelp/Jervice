"""A tiny shared helper so Jarvis's own saved files (config.yaml, memory.json,
reminders.json, history.jsonl) can never be left half-written.

Writing straight to the real file (`path.write_text(...)`) means a process kill, a crash, or
a power loss at exactly the wrong moment leaves a truncated, corrupt file behind -- and for
config.yaml in particular, that meant Jarvis failing to start at all next time (see
jarvis.config.load_config, which now recovers from this instead of raising). Writing to a
temp file next to the target and then swapping it in (os.replace, atomic on the same drive)
means the real file is either the old complete version or the new complete version, never
something in between.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)   # atomic on the same volume -- never a half-written file
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
