"""File search/open helpers, scoped to the user's profile directory by
default to keep searches fast and avoid trawling the whole filesystem."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("jarvis.tools.files")

MAX_RESULTS = 15
MAX_DIRS_WALKED = 20000  # safety cap so a huge tree can't hang the assistant


def search_files(query: str, root: str = "") -> str:
    query = query.strip().lower()
    if not query:
        return "No search term given."

    start = Path(root).expanduser() if root else Path.home()
    if not start.exists():
        return f"Search location '{start}' does not exist."

    matches: list[str] = []
    walked = 0
    for dirpath, dirnames, filenames in os.walk(start):
        walked += 1
        if walked > MAX_DIRS_WALKED:
            break
        # Skip noisy/system directories.
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in
            {"node_modules", "$recycle.bin", "windows", "appdata"}
        ]
        for fname in filenames:
            if query in fname.lower():
                matches.append(str(Path(dirpath) / fname))
                if len(matches) >= MAX_RESULTS:
                    return "\n".join(matches)

    return "\n".join(matches) if matches else f"No files matching '{query}' found under {start}."


def open_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"File '{path}' does not exist."
    try:
        os.startfile(str(p))  # type: ignore[attr-defined]  (Windows-only)
        return f"Opened {p}."
    except Exception as exc:
        logger.warning("Failed to open file %s: %s", p, exc)
        return f"I couldn't open '{path}': {exc}"


def read_text_file(path: str, max_chars: int = 4000) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"File '{path}' does not exist."
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"
        return text
    except Exception as exc:
        logger.warning("Failed to read file %s: %s", p, exc)
        return f"I couldn't read '{path}': {exc}"
