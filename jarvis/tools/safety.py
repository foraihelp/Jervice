"""Confirmation gate for risky tools.

Closing an app or window, or locking the PC, is easy to trigger by a misheard
command. Asking the model to "always confirm first" isn't reliable, so the
gate is enforced here in code: the first call to a risky tool is *not*
executed. It returns a message telling the model to ask the user a yes/no
question. The tool only runs when it is called again with the same arguments
during a *later* user turn, in which the user's own words were an affirmative
("yes", "go ahead", "haan"...) and not a refusal. The model can't skip the
question or answer it for the user, because a new turn only starts when the
user speaks or types again.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

RISKY_TOOLS = {"close_app", "close_window", "lock_workstation"}

_PENDING_TTL_SECONDS = 120

_enabled = True
_turn = 0
_last_user_text = ""
_pending: dict[str, tuple[int, float]] = {}
_last_reply = ""
_last_reply_turn = -1

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "proceed",
    "affirmative", "correct", "haan", "han", "ji", "theek", "thik",
    "हाँ", "हां", "जी", "ठीक", "हा", "হ্যাঁ", "হ্যা", "হাঁ", "হা", "ঠিক",
}
_YES_PHRASES = ("go ahead", "do it", "go for it", "please do", "yes please")
_NO_WORDS = {
    "no", "not", "dont", "nope", "cancel", "stop", "never", "nevermind", "nahi", "nahin", "na", "mat",
    "नहीं", "नही", "मत", "रुको", "না", "নাহ", "নয়",
}


def configure(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def begin_turn(user_text: str) -> None:
    """Called by the brain at the start of every user turn."""
    global _turn, _last_user_text
    _turn += 1
    _last_user_text = user_text or ""


def end_turn(reply: str) -> None:
    """Called by the brain with the final reply of each turn. If that reply was a
    question naming the action, a "yes" on the next turn confirms it even though
    the model asked on its own instead of going through the gate first."""
    global _last_reply, _last_reply_turn
    _last_reply = reply or ""
    _last_reply_turn = _turn


def _asked_about(name: str, tool_input: dict[str, Any]) -> bool:
    """True if the assistant's reply to the previous turn was a question that
    mentions this action's target."""
    if _last_reply_turn != _turn - 1 or "?" not in _last_reply:
        return False
    reply = _last_reply.lower()
    if name == "lock_workstation":
        return "lock" in reply
    target = str(tool_input.get("name") or tool_input.get("title_substring") or "").strip().lower()
    return bool(target) and target in reply


def is_affirmative(text: str) -> bool:
    lowered = (text or "").lower().replace("'", "").replace("’", "")
    words = re.findall(r"[\wऀ-ॿঀ-৿]+", lowered)
    if any(w in _NO_WORDS for w in words):
        return False
    return any(w in _YES_WORDS for w in words) or any(p in lowered for p in _YES_PHRASES)


def _key(name: str, tool_input: dict[str, Any]) -> str:
    normalized = {k: (v.strip().lower() if isinstance(v, str) else v) for k, v in tool_input.items()}
    return name + json.dumps(normalized, sort_keys=True, default=str)


def _describe(name: str, tool_input: dict[str, Any]) -> str:
    if name == "close_app":
        return f"close {tool_input.get('name', 'that app')}"
    if name == "close_window":
        return f"close the window \"{tool_input.get('title_substring', '')}\""
    if name == "lock_workstation":
        return "lock your PC"
    return name


def check(name: str, tool_input: dict[str, Any]) -> Optional[str]:
    """Returns None if the call may proceed now, otherwise the message to give
    the model instead of running the tool."""
    if not _enabled or name not in RISKY_TOOLS:
        return None

    now = time.time()
    for k in [k for k, (_, t) in _pending.items() if now - t > _PENDING_TTL_SECONDS]:
        del _pending[k]

    key = _key(name, tool_input)
    pending = _pending.get(key)
    confirmed = is_affirmative(_last_user_text) and (
        (pending is not None and pending[0] < _turn) or _asked_about(name, tool_input)
    )
    if confirmed:
        _pending.pop(key, None)
        return None

    if pending is None or pending[0] < _turn:
        _pending[key] = (_turn, now)
    action = _describe(name, tool_input)
    return (
        f"CONFIRMATION REQUIRED: this action has NOT been done yet ({action}). Ask the user a short "
        f"yes/no question, for example \"Do you want me to {action}?\", and wait for their answer. "
        f"Do not call {name} again until the user has replied. If they say yes, call {name} again "
        "with exactly the same arguments; if they say no, do nothing."
    )
