"""Simple JSON-file-backed conversation memory.

This is intentionally lightweight (no DB) -- it's a list of provider-format
messages (Anthropic content-blocks, or OpenAI tool-call messages, depending
on which brain is active -- see jarvis/brain/__init__.py) plus a small
free-form "facts" dict the model can be told to update via conversation
(e.g. "remember that my wifi password is ..."). For a personal single-user
local assistant this is plenty; swap in SQLite later if the history grows
large.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.memory")


class Memory:
    def __init__(self, path: Path, history_turns: int, provider: str = ""):
        self.path = path
        self.history_turns = history_turns
        # Different providers use incompatible message shapes (Anthropic's
        # content-block lists vs. OpenAI's tool_calls/tool role messages).
        # Tagging the saved file with which provider wrote it lets us detect
        # a provider switch and start fresh instead of feeding one
        # provider's malformed history to another's API.
        self.provider = provider
        self.messages: list[dict[str, Any]] = []
        self.facts: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            stored_provider = data.get("provider", "")
            if self.provider and stored_provider and stored_provider != self.provider:
                logger.info(
                    "Memory file was written by provider '%s', now using '%s' -- "
                    "starting a fresh conversation (facts are kept).",
                    stored_provider, self.provider,
                )
            else:
                self.messages = data.get("messages", [])
            self.facts = data.get("facts", {})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load memory file %s: %s", self.path, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep only the most recent N turns on disk to bound file size.
        trimmed = self.messages[-(self.history_turns * 2):]
        data = {"provider": self.provider, "messages": trimmed, "facts": self.facts}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_message(self, role: str, content: Any = None, **extra: Any) -> None:
        msg: dict[str, Any] = {"role": role}
        if content is not None:
            msg["content"] = content
        msg.update(extra)
        self.messages.append(msg)

    def recent_messages(self) -> list[dict[str, Any]]:
        return self.messages[-(self.history_turns * 2):]
