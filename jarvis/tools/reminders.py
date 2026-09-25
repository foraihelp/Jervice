"""Timers and reminders. They are saved to disk so they survive closing Jarvis,
and a background thread speaks them when they are due. A reminder that came
due while Jarvis was closed is announced as missed shortly after the next start.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("jarvis.reminders")

MAX_ITEMS = 100
MAX_TIMER_SECONDS = 7 * 24 * 3600
_MISSED_AFTER = timedelta(seconds=90)
_POLL_SECONDS = 30
_STARTUP_DELAY_SECONDS = 6  # lets the window finish loading before missed reminders are announced


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _when_phrase(due: datetime, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    days = (due.date() - now.date()).days
    if days == 0:
        return f"today at {_clock(due)}"
    if days == 1:
        return f"tomorrow at {_clock(due)}"
    return f"on {due.strftime('%A, %d %B')} at {_clock(due)}"


class Scheduler:
    def __init__(self, path: Path, on_fire: Callable[[str], None], startup_delay: float = _STARTUP_DELAY_SECONDS):
        self._path = path
        self._on_fire = on_fire
        self._startup_delay = startup_delay
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            return list(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            logger.warning("Could not read %s; starting with no reminders.", self._path, exc_info=True)
            return []

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._items, indent=2, ensure_ascii=False), encoding="utf-8")

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="reminders").start()

    def add(self, due: datetime, message: str, kind: str, label: str = "") -> dict:
        with self._lock:
            if len(self._items) >= MAX_ITEMS:
                raise ValueError("There are too many reminders saved already.")
            item = {"id": uuid.uuid4().hex[:6], "due": due.isoformat(timespec="seconds"),
                    "message": message, "kind": kind, "label": label}
            self._items.append(item)
            self._save_locked()
        self._wake.set()
        return item

    def pending(self) -> list[dict]:
        with self._lock:
            return sorted(self._items, key=lambda i: i["due"])

    def cancel(self, query: str) -> list[dict]:
        """Removes and returns the items matching `query` (an id, or text
        contained in the message or label)."""
        q = query.strip().lower()
        with self._lock:
            hits = [i for i in self._items if q and (q == i["id"] or q in i["message"].lower() or q in i["label"].lower())]
            if hits:
                self._items = [i for i in self._items if i not in hits]
                self._save_locked()
        self._wake.set()
        return hits

    def _text(self, item: dict, missed: bool) -> str:
        due = datetime.fromisoformat(item["due"])
        if item["kind"] == "timer":
            label = item["label"]
            base = f"Your {label} timer is done." if label else "Your timer is up."
            return f"{base} It finished at {_clock(due)}." if missed else base
        if missed:
            return f"You missed a reminder from {_when_phrase(due)}: {item['message']}"
        return f"Reminder: {item['message']}"

    def _run(self) -> None:
        self._wake.wait(self._startup_delay)
        self._wake.clear()
        while True:
            now = datetime.now()
            with self._lock:
                due_items = [i for i in self._items if datetime.fromisoformat(i["due"]) <= now]
                if due_items:
                    self._items = [i for i in self._items if i not in due_items]
                    self._save_locked()
                upcoming = [datetime.fromisoformat(i["due"]) for i in self._items]
            for item in due_items:
                missed = now - datetime.fromisoformat(item["due"]) > _MISSED_AFTER
                try:
                    self._on_fire(self._text(item, missed))
                except Exception:  # noqa: BLE001 - one failed announcement must not stop the scheduler
                    logger.exception("Could not announce reminder %s", item.get("id"))
            wait = _POLL_SECONDS
            if upcoming:
                wait = max(0.2, min(_POLL_SECONDS, (min(upcoming) - datetime.now()).total_seconds()))
            self._wake.wait(wait)
            self._wake.clear()


_scheduler: Optional[Scheduler] = None


def configure(path: Path, on_fire: Callable[[str], None]) -> Scheduler:
    global _scheduler
    _scheduler = Scheduler(path, on_fire)
    _scheduler.start()
    return _scheduler


def _unavailable() -> str:
    return "Reminders aren't available right now."


def set_timer(duration_seconds: float, label: str = "") -> str:
    if _scheduler is None:
        return _unavailable()
    seconds = float(duration_seconds)
    if not 1 <= seconds <= MAX_TIMER_SECONDS:
        return "A timer must be between 1 second and 7 days. For anything later, use a reminder."
    due = datetime.now() + timedelta(seconds=seconds)
    try:
        _scheduler.add(due, label or "timer", "timer", label.strip())
    except ValueError as exc:
        return str(exc)
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")) if n]
    return f"Timer set for {' '.join(parts)}" + (f" ({label.strip()})." if label.strip() else ".")


def set_reminder(when: str, message: str) -> str:
    if _scheduler is None:
        return _unavailable()
    if not message.strip():
        return "What should the reminder say?"
    try:
        due = datetime.fromisoformat(when.strip().replace(" ", "T", 1))
    except ValueError:
        return "I couldn't read that time. Use the format YYYY-MM-DD HH:MM (24-hour, local time)."
    if due.tzinfo is not None:
        due = due.astimezone().replace(tzinfo=None)
    now = datetime.now()
    if due < now - timedelta(seconds=60):
        return f"That time has already passed (it is {now.strftime('%Y-%m-%d %H:%M')} now). Pick a future time."
    try:
        _scheduler.add(due, message.strip(), "reminder")
    except ValueError as exc:
        return str(exc)
    return f"Okay, I'll remind you {_when_phrase(due, now)}: {message.strip()}"


def list_reminders() -> str:
    if _scheduler is None:
        return _unavailable()
    items = _scheduler.pending()
    if not items:
        return "You have no timers or reminders set."
    now = datetime.now()
    lines = []
    for i in items:
        what = f"timer{' (' + i['label'] + ')' if i['label'] else ''}" if i["kind"] == "timer" else i["message"]
        lines.append(f"{what} {_when_phrase(datetime.fromisoformat(i['due']), now)}")
    return f"You have {len(items)}: " + "; ".join(lines) + "."


def cancel_reminder(query: str) -> str:
    if _scheduler is None:
        return _unavailable()
    hits = _scheduler.cancel(query)
    if not hits:
        return f"I couldn't find a timer or reminder matching '{query}'."
    return f"Cancelled {len(hits)}: " + "; ".join(h["message"] for h in hits) + "."
