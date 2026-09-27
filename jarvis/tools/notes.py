"""Freeform notes Jarvis can save, list, read back, edit, or delete on
request. Same persisted-list shape as tasks.py, with a title and body
instead of a single line, and an updated_at so the list shows the most
recently touched note first.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.notes")

MAX_NOTES = 100
MAX_TITLE_CHARS = 80
MAX_CONTENT_CHARS = 4000


class NoteStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._notes: list[dict] = self._load()
        # Ordering uses this counter, not the updated_at timestamp: two notes created or
        # updated back to back can land in the same tick (Windows' clock can be as coarse
        # as ~15ms), which would make wall-clock order ambiguous.
        self._next_seq = 1 + max((n.get("seq", 0) for n in self._notes), default=0)

    def _load(self) -> list[dict]:
        try:
            return list(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            logger.warning("Could not read %s; starting with no notes.", self._path, exc_info=True)
            return []

    def _save_locked(self) -> None:
        from jarvis.fsutil import atomic_write_text

        atomic_write_text(self._path, json.dumps(self._notes, indent=2, ensure_ascii=False))

    def create(self, title: str, content: str) -> dict:
        title = (title or "").strip()
        content = (content or "").strip()
        if not title:
            title = (content[:40] + "...") if len(content) > 40 else content
        if not title:
            raise ValueError("What should the note say?")
        with self._lock:
            if len(self._notes) >= MAX_NOTES:
                raise ValueError("There are too many notes saved already. Delete some first.")
            now = datetime.now().isoformat(timespec="seconds")
            note = {
                "id": uuid.uuid4().hex[:6], "title": title[:MAX_TITLE_CHARS],
                "content": content[:MAX_CONTENT_CHARS], "created_at": now, "updated_at": now,
                "seq": self._next_seq,
            }
            self._next_seq += 1
            self._notes.append(note)
            self._save_locked()
        return note

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._notes, key=lambda n: n["seq"], reverse=True)

    def _find_locked(self, query: str) -> Optional[dict]:
        q = (query or "").strip().lower()
        for n in self._notes:
            if q and (q == n["id"] or q in n["title"].lower()):
                return n
        return None

    def get(self, query: str) -> Optional[dict]:
        with self._lock:
            note = self._find_locked(query)
            return dict(note) if note is not None else None

    def update(self, query: str, content: str) -> Optional[dict]:
        with self._lock:
            note = self._find_locked(query)
            if note is not None:
                note["content"] = (content or "").strip()[:MAX_CONTENT_CHARS]
                note["updated_at"] = datetime.now().isoformat(timespec="seconds")
                note["seq"] = self._next_seq
                self._next_seq += 1
                self._save_locked()
                note = dict(note)
        return note

    def delete(self, query: str) -> Optional[dict]:
        with self._lock:
            note = self._find_locked(query)
            if note is not None:
                self._notes = [n for n in self._notes if n is not note]
                self._save_locked()
        return note


_store: Optional[NoteStore] = None


def configure(path: Path) -> NoteStore:
    global _store
    _store = NoteStore(path)
    return _store


def get_store() -> Optional[NoteStore]:
    """For the Settings UI, which wants the raw dicts/list rather than the
    tool-facing spoken-string functions below."""
    return _store


def _unavailable() -> str:
    return "Notes aren't available right now."


def create_note(title: str, content: str) -> str:
    if _store is None:
        return _unavailable()
    try:
        note = _store.create(title, content)
    except ValueError as exc:
        return str(exc)
    return f"Saved note '{note['title']}'."


def list_notes() -> str:
    if _store is None:
        return _unavailable()
    notes = _store.list()
    if not notes:
        return "You have no notes."
    return f"You have {len(notes)}: " + "; ".join(n["title"] for n in notes) + "."


def read_note(query: str) -> str:
    if _store is None:
        return _unavailable()
    note = _store.get(query)
    if note is None:
        return f"I couldn't find a note matching '{query}'."
    return f"{note['title']}: {note['content']}" if note["content"] else note["title"]


def update_note(query: str, content: str) -> str:
    if _store is None:
        return _unavailable()
    note = _store.update(query, content)
    if note is None:
        return f"I couldn't find a note matching '{query}'."
    return f"Updated note '{note['title']}'."


def delete_note(query: str) -> str:
    if _store is None:
        return _unavailable()
    note = _store.delete(query)
    if note is None:
        return f"I couldn't find a note matching '{query}'."
    return f"Deleted note '{note['title']}'."
