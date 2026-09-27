"""A chain of AI providers: the main one first, then backups.

When the main provider is rate-limited, out of quota, unreachable or rejecting the key, the same
request is answered by the next provider instead of failing. Once a provider has failed, it is
left alone for a while so the following requests don't each wait for it to fail again.

Each provider has its own conversation format (Anthropic's content blocks vs OpenAI's tool-call
messages), so a backup never touches the main provider's saved conversation. It is handed the
plain text of the conversation so far, and afterwards the main conversation is told what was
said, so nothing is lost when the main provider comes back.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from jarvis.brain import progress
from jarvis.brain.memory import Memory
from jarvis.brain.streaming import SpeechCancelled
from jarvis.tools.registry import call_count, get_recent_calls

logger = logging.getLogger("jarvis.brain")

# How long a provider is left alone after it fails, by kind of failure.
COOLDOWN_RATE_LIMIT = 60.0
COOLDOWN_DAILY_LIMIT = 30 * 60.0
COOLDOWN_BAD_KEY = 10 * 60.0
COOLDOWN_OUTAGE = 30.0

_CONNECTION_ERRORS = {"APIConnectionError", "APITimeoutError"}


def failure_kind(exc: BaseException) -> Optional[str]:
    """Why a provider call failed, if another provider could do better ("rate_limit", "daily_limit",
    "bad_key", "outage", "model_missing"); None for errors a different provider would not fix
    (a malformed request, a bug, the user pressing Stop)."""
    status = getattr(exc, "status_code", None)
    message = str(exc).lower()
    if status == 429:
        if "per day" in message or "(tpd)" in message or "daily" in message or "insufficient_quota" in message or "quota" in message:
            return "daily_limit"
        return "rate_limit"
    if status in (401, 403):
        return "bad_key"
    if status == 404:
        return "model_missing"
    if isinstance(status, int) and status >= 500:
        return "outage"
    if any(cls.__name__ in _CONNECTION_ERRORS for cls in type(exc).__mro__):
        return "outage"
    return None


_COOLDOWNS = {
    "rate_limit": COOLDOWN_RATE_LIMIT,
    "daily_limit": COOLDOWN_DAILY_LIMIT,
    "bad_key": COOLDOWN_BAD_KEY,
    "model_missing": COOLDOWN_BAD_KEY,
    "outage": COOLDOWN_OUTAGE,
}

_REASON_TEXT = {
    "rate_limit": "RATE-LIMITED",
    "daily_limit": "OUT OF QUOTA",
    "bad_key": "KEY REJECTED",
    "model_missing": "MODEL NOT FOUND",
    "outage": "UNREACHABLE",
}


def text_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation as plain alternating user/assistant text, whatever provider wrote it:
    tool calls and tool results are dropped, keeping what was actually said."""
    out: list[dict[str, Any]] = []

    def add(role: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})

    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role == "user" and isinstance(content, str):
            add("user", content)
        elif role == "assistant":
            if isinstance(content, str):
                add("assistant", content)
            elif isinstance(content, list):
                add("assistant", "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"))
    while out and out[0]["role"] != "user":
        out.pop(0)   # a conversation has to start with the user
    return out


class _Member:
    def __init__(self, brain, label: str, provider: str):
        self.brain = brain
        self.label = label
        self.provider = provider
        self.down_until = 0.0


class FallbackBrain:
    """Looks like a single brain (`respond`, `memory`, `model`) to the rest of the app."""

    def __init__(self, primary, backups: list[tuple[Any, str, str]], history_turns: int,
                 clock: Callable[[], float] = time.monotonic):
        """`backups` is a list of (brain, label, provider) in the order to try them."""
        self._primary = _Member(primary, getattr(primary, "model", "main AI"), getattr(primary.memory, "provider", ""))
        self._backups = [_Member(brain, label, provider) for brain, label, provider in backups]
        self._history_turns = history_turns
        self._clock = clock
        self._lock = threading.Lock()
        self.last_used = self._primary.label   # which provider answered the latest request

    @property
    def memory(self) -> Memory:
        return self._primary.brain.memory

    @property
    def model(self) -> str:
        return self._primary.brain.model

    def respond(self, user_text: str, on_sentence: Optional[Callable[[str], None]] = None) -> str:
        with self._lock:
            return self._respond(user_text, on_sentence)

    # ----------------------------------------------------------------------------------

    def _order(self) -> list[_Member]:
        """The providers to try, skipping any that failed recently. If they all failed recently,
        everything is tried anyway: some answer beats none."""
        members = [self._primary, *self._backups]
        now = self._clock()
        ready = [m for m in members if m.down_until <= now]
        return ready or members

    def _respond(self, user_text: str, on_sentence: Optional[Callable[[str], None]]) -> str:
        primary_memory = self._primary.brain.memory
        start = len(primary_memory.messages)
        delivered = {"any": False}

        def tracked(sentence: str) -> None:
            delivered["any"] = True
            on_sentence(sentence)

        calls_before = call_count()
        last_error: Optional[BaseException] = None

        for member in self._order():
            is_primary = member is self._primary
            request = user_text
            done = get_recent_calls(call_count() - calls_before) if call_count() > calls_before else []
            if done:
                request += (
                    "\n\n[Note from the system: while handling this request these actions already ran "
                    "successfully, so do not repeat them: " + "; ".join(done) + ". Carry on from there and "
                    "tell the user what was done.]"
                )
            if not is_primary:
                member.brain.memory = Memory.in_memory(
                    self._history_turns, member.provider, text_history(primary_memory.messages[:start]), primary_memory.facts
                )
            try:
                reply = member.brain.respond(request, on_sentence=tracked if on_sentence else None)
            except SpeechCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                kind = failure_kind(exc)
                if kind is None:
                    raise
                last_error = exc
                member.down_until = self._clock() + _COOLDOWNS[kind]
                logger.warning("AI provider %s failed (%s): %s", member.label, kind, exc)
                del primary_memory.messages[start:]     # drop the half-finished turn from the main conversation
                if delivered["any"]:
                    raise   # part of an answer is already spoken; a second provider can't continue it
                remaining = [m for m in self._order() if m is not member and m.down_until <= self._clock()]
                if remaining:
                    progress.notify(f"{member.label.upper()} {_REASON_TEXT[kind]} · SWITCHING TO {remaining[0].label.upper()}")
                continue

            member.down_until = 0.0
            self.last_used = member.label
            if not is_primary:
                logger.info("Answered by backup provider %s.", member.label)
                # The main conversation must know what was said while it was away.
                primary_memory.add_message("user", user_text)
                primary_memory.add_message("assistant", reply)
                primary_memory.save()
            return reply

        assert last_error is not None
        raise last_error
