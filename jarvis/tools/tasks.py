"""A simple to-do list: short text items Jarvis can add, list, mark done, or
delete on request. Unlike reminders/agents, nothing here runs itself later --
just a persisted list, saved the same atomic way as everything else.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.tasks")

MAX_TASKS = 200
MAX_TEXT_CHARS = 300


class TaskStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._tasks: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            return list(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            logger.warning("Could not read %s; starting with no tasks.", self._path, exc_info=True)
            return []

    def _save_locked(self) -> None:
        from jarvis.fsutil import atomic_write_text

        atomic_write_text(self._path, json.dumps(self._tasks, indent=2, ensure_ascii=False))

    def create(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("What's the task?")
        with self._lock:
            if len(self._tasks) >= MAX_TASKS:
                raise ValueError("There are too many tasks saved already. Clear the done ones first.")
            task = {
                "id": uuid.uuid4().hex[:6], "text": text[:MAX_TEXT_CHARS], "done": False,
                "created_at": datetime.now().isoformat(timespec="microseconds"),
            }
            self._tasks.append(task)
            self._save_locked()
        return task

    def list(self) -> list[dict]:
        with self._lock:
            items = list(self._tasks)
        return sorted(items, key=lambda t: (t["done"], t["created_at"]))

    def _find_locked(self, query: str) -> Optional[dict]:
        q = (query or "").strip().lower()
        for t in self._tasks:
            if q and (q == t["id"] or q in t["text"].lower()):
                return t
        return None

    def set_done(self, query: str, done: bool) -> Optional[dict]:
        with self._lock:
            task = self._find_locked(query)
            if task is not None:
                task["done"] = bool(done)
                self._save_locked()
                task = dict(task)
        return task

    def delete(self, query: str) -> Optional[dict]:
        with self._lock:
            task = self._find_locked(query)
            if task is not None:
                self._tasks = [t for t in self._tasks if t is not task]
                self._save_locked()
        return task

    def clear_done(self) -> int:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if not t["done"]]
            removed = before - len(self._tasks)
            if removed:
                self._save_locked()
        return removed


_store: Optional[TaskStore] = None


def configure(path: Path) -> TaskStore:
    global _store
    _store = TaskStore(path)
    return _store


def get_store() -> Optional[TaskStore]:
    """For the Settings UI, which wants the raw dicts/list rather than the
    tool-facing spoken-string functions below."""
    return _store


def _unavailable() -> str:
    return "Tasks aren't available right now."


def create_task(text: str) -> str:
    if _store is None:
        return _unavailable()
    try:
        task = _store.create(text)
    except ValueError as exc:
        return str(exc)
    return f"Added to your list: {task['text']}."


def list_tasks() -> str:
    if _store is None:
        return _unavailable()
    items = _store.list()
    if not items:
        return "Your task list is empty."
    open_items = [t for t in items if not t["done"]]
    done_count = len(items) - len(open_items)
    if not open_items:
        return f"Nothing open. ({done_count} done.)" if done_count else "Your task list is empty."
    text = f"You have {len(open_items)} open: " + "; ".join(t["text"] for t in open_items) + "."
    if done_count:
        text += f" ({done_count} done.)"
    return text


def complete_task(query: str) -> str:
    if _store is None:
        return _unavailable()
    task = _store.set_done(query, True)
    if task is None:
        return f"I couldn't find a task matching '{query}'."
    return f"Marked done: {task['text']}."


def reopen_task(query: str) -> str:
    if _store is None:
        return _unavailable()
    task = _store.set_done(query, False)
    if task is None:
        return f"I couldn't find a task matching '{query}'."
    return f"Reopened: {task['text']}."


def delete_task(query: str) -> str:
    if _store is None:
        return _unavailable()
    task = _store.delete(query)
    if task is None:
        return f"I couldn't find a task matching '{query}'."
    return f"Deleted: {task['text']}."
