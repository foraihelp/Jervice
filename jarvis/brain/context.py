"""Per-request additions to the system prompt: things that change between
requests, so they can't be baked into the prompt when the brain is created."""

from __future__ import annotations

from datetime import datetime

from jarvis.brain.memory import Memory


def dynamic_context(memory: Memory) -> str:
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M")
    return f"\n\nCurrent local date and time: {now}." + memory.facts_prompt()
