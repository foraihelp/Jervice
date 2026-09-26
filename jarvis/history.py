"""What was said in the Jarvis window, saved so it is still there after a restart.

One JSON object per line. Kept separate from the AI's own memory (memory.json), which holds what the
model sees; this is what the user sees and wrote. The file is trimmed when it gets large.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.history")

MAX_BYTES = 5 * 1024 * 1024
KEEP_LINES_WHEN_TRIMMED = 2000
RESTORE_MESSAGES = 60

_path: Optional[Path] = None
_lock = threading.Lock()


def configure(path: Path) -> None:
    global _path
    _path = path


def add(role: str, text: str, tools: Optional[list[str]] = None) -> None:
    """Records one message. Never raises: losing a history line must not break a conversation."""
    if _path is None or not text:
        return
    entry = {"t": datetime.now().isoformat(timespec="seconds"), "role": role, "text": text}
    if tools:
        entry["tools"] = list(tools)
    try:
        with _lock:
            _path.parent.mkdir(parents=True, exist_ok=True)
            with open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if _path.stat().st_size > MAX_BYTES:
                _trim()
    except OSError:
        logger.warning("Could not save a history line", exc_info=True)


def _trim() -> None:
    lines = _path.read_text(encoding="utf-8").splitlines()[-KEEP_LINES_WHEN_TRIMMED:]
    _path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recent(n: int = RESTORE_MESSAGES) -> list[dict[str, Any]]:
    """The last `n` messages, oldest first, as {role, text, tools, time}."""
    if _path is None or not _path.exists():
        return []
    try:
        with _lock:
            lines = _path.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    messages = []
    for line in lines:
        try:
            e = json.loads(line)
            messages.append({"role": e["role"], "text": e["text"], "tools": e.get("tools", []), "time": e.get("t", "")})
        except (json.JSONDecodeError, KeyError):
            continue
    return messages


def clear() -> None:
    if _path is None:
        return
    try:
        with _lock:
            _path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not clear the history", exc_info=True)
