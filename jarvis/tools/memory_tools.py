"""Tools for long-term memory: facts the user asks Jarvis to remember. They are
stored in Memory.facts, saved to disk, and shown to the model on every request
(see jarvis/brain/context.py), so it can use them without being asked.
"""

from __future__ import annotations

from typing import Callable, Optional

from jarvis.brain.memory import Memory

MAX_FACTS = 60
MAX_KEY_CHARS = 60
MAX_VALUE_CHARS = 300

_get_memory: Optional[Callable[[], Memory]] = None


def configure(get_memory: Callable[[], Memory]) -> None:
    """`get_memory` returns the *current* Memory, since switching AI provider in
    Settings replaces the brain (and its Memory object)."""
    global _get_memory
    _get_memory = get_memory


def remember(key: str, value: str) -> str:
    if _get_memory is None:
        return "Memory isn't available right now."
    key, value = key.strip(), value.strip()
    if not key or not value:
        return "I need both what to call it and what to remember."
    memory = _get_memory()
    if key not in memory.facts and len(memory.facts) >= MAX_FACTS:
        return "My memory is full. Ask me to forget something first."
    memory.facts[key[:MAX_KEY_CHARS]] = value[:MAX_VALUE_CHARS]
    memory.save_facts()
    return f"Got it, I'll remember that: {key}: {value}."


def forget(key: str) -> str:
    if _get_memory is None:
        return "Memory isn't available right now."
    memory = _get_memory()
    q = key.strip().lower()
    hits = [k for k in memory.facts if q and (q == k.lower() or q in k.lower())]
    if not hits:
        return f"I don't have anything remembered about '{key}'."
    for k in hits:
        del memory.facts[k]
    memory.save_facts()
    return f"Forgot: {', '.join(hits)}."
