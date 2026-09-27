"""Daily-recurring AI agents. Like reminders.py's Scheduler, but where a
reminder just speaks a fixed message, an agent's "instruction" is run as a
full AI turn (via the same on_fire callback wired in main.py) so it can
actually use tools, then speaks and shows whatever it found -- once a day,
at a set clock time, until turned off or deleted.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("jarvis.agents")

MAX_AGENTS = 20
_POLL_SECONDS = 30
_STARTUP_DELAY_SECONDS = 6  # lets the window finish loading before a same-day catch-up run fires
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _valid_time(value: str) -> str:
    value = (value or "").strip()
    if not _TIME_RE.match(value):
        raise ValueError("Time must be 'HH:MM' in 24-hour format, e.g. '08:00' or '17:30'.")
    return value


def _clock(time_str: str) -> str:
    return datetime.strptime(time_str, "%H:%M").strftime("%I:%M %p").lstrip("0")


class AgentScheduler:
    def __init__(self, path: Path, on_fire: Callable[[dict], None], startup_delay: float = _STARTUP_DELAY_SECONDS):
        self._path = path
        self._on_fire = on_fire
        self._startup_delay = startup_delay
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._agents: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            return list(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            logger.warning("Could not read %s; starting with no agents.", self._path, exc_info=True)
            return []

    def _save_locked(self) -> None:
        from jarvis.fsutil import atomic_write_text

        atomic_write_text(self._path, json.dumps(self._agents, indent=2, ensure_ascii=False))

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="agents").start()

    def create(self, name: str, instruction: str, time_str: str) -> dict:
        name = (name or "").strip()
        instruction = (instruction or "").strip()
        if not name:
            raise ValueError("Give the agent a short name.")
        if not instruction:
            raise ValueError("What should the agent do?")
        time_str = _valid_time(time_str)
        with self._lock:
            if len(self._agents) >= MAX_AGENTS:
                raise ValueError("There are too many agents saved already.")
            agent = {
                "id": uuid.uuid4().hex[:6], "name": name, "instruction": instruction,
                "time": time_str, "enabled": True, "last_run_date": "",
            }
            self._agents.append(agent)
            self._save_locked()
        self._wake.set()
        return agent

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._agents, key=lambda a: a["time"])

    def _find_locked(self, query: str) -> Optional[dict]:
        q = (query or "").strip().lower()
        for a in self._agents:
            if q and (q == a["id"] or q in a["name"].lower()):
                return a
        return None

    def set_enabled(self, query: str, enabled: bool) -> Optional[dict]:
        with self._lock:
            agent = self._find_locked(query)
            if agent is not None:
                agent["enabled"] = bool(enabled)
                self._save_locked()
                agent = dict(agent)
        self._wake.set()
        return agent

    def delete(self, query: str) -> Optional[dict]:
        with self._lock:
            agent = self._find_locked(query)
            if agent is not None:
                self._agents = [a for a in self._agents if a is not agent]
                self._save_locked()
        return agent

    def run_now(self, query: str) -> Optional[dict]:
        """Fires the agent right away, in the background (never on the calling
        thread): the caller may itself be inside a brain turn (the AI called
        this as a tool), and firing acquires the same brain's lock to run the
        instruction -- doing that synchronously here would deadlock a turn
        against itself. Marks it as run today, so the daily scheduler doesn't
        also fire it again a few minutes later."""
        with self._lock:
            agent = self._find_locked(query)
            if agent is not None:
                agent["last_run_date"] = date.today().isoformat()
                self._save_locked()
                agent = dict(agent)
        if agent is not None:
            threading.Thread(target=self._fire, args=(agent,), daemon=True, name="agent-run-now").start()
        return agent

    def _fire(self, agent: dict) -> None:
        try:
            self._on_fire(agent)
        except Exception:  # noqa: BLE001 - one failed agent must not stop the scheduler
            logger.exception("Agent '%s' failed", agent.get("name"))

    def _run(self) -> None:
        self._wake.wait(self._startup_delay)
        self._wake.clear()
        while True:
            today = date.today().isoformat()
            now_hm = datetime.now().strftime("%H:%M")
            due: list[dict] = []
            with self._lock:
                for agent in self._agents:
                    if agent["enabled"] and agent["last_run_date"] != today and now_hm >= agent["time"]:
                        agent["last_run_date"] = today
                        due.append(dict(agent))
                if due:
                    self._save_locked()
            for agent in due:
                self._fire(agent)
            self._wake.wait(_POLL_SECONDS)
            self._wake.clear()


_scheduler: Optional[AgentScheduler] = None


def configure(path: Path, on_fire: Callable[[dict], None]) -> AgentScheduler:
    global _scheduler
    _scheduler = AgentScheduler(path, on_fire)
    _scheduler.start()
    return _scheduler


def get_scheduler() -> Optional[AgentScheduler]:
    """For the Settings UI, which wants the raw dicts/list rather than the
    tool-facing spoken-string functions below."""
    return _scheduler


def _unavailable() -> str:
    return "Agents aren't available right now."


def create_agent(name: str, instruction: str, time: str) -> str:
    if _scheduler is None:
        return _unavailable()
    try:
        agent = _scheduler.create(name, instruction, time)
    except ValueError as exc:
        return str(exc)
    return f"Agent '{agent['name']}' created -- it will run every day at {_clock(agent['time'])}."


def list_agents() -> str:
    if _scheduler is None:
        return _unavailable()
    agents = _scheduler.list()
    if not agents:
        return "You have no agents set up."
    lines = [
        f"{a['name']} ({'on' if a['enabled'] else 'off'}) daily at {_clock(a['time'])}: {a['instruction']}"
        for a in agents
    ]
    return f"You have {len(agents)}: " + "; ".join(lines) + "."


def toggle_agent(query: str, enabled: bool) -> str:
    if _scheduler is None:
        return _unavailable()
    agent = _scheduler.set_enabled(query, enabled)
    if agent is None:
        return f"I couldn't find an agent matching '{query}'."
    return f"Agent '{agent['name']}' is now {'on' if agent['enabled'] else 'off'}."


def delete_agent(query: str) -> str:
    if _scheduler is None:
        return _unavailable()
    agent = _scheduler.delete(query)
    if agent is None:
        return f"I couldn't find an agent matching '{query}'."
    return f"Deleted agent '{agent['name']}'."


def run_agent_now(query: str) -> str:
    if _scheduler is None:
        return _unavailable()
    agent = _scheduler.run_now(query)
    if agent is None:
        return f"I couldn't find an agent matching '{query}'."
    return f"Running '{agent['name']}' now -- I'll share what it finds in a moment."
