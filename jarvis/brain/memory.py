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

_OLD_TOOL_OUTPUT_CHARS = 300


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

    def save_facts(self) -> None:
        """Writes only the facts to disk, leaving the saved conversation as it
        was. Used by the remember/forget tools, which run in the middle of a
        turn -- saving the whole conversation at that point could store a tool
        call whose result hasn't been recorded yet."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {"provider": self.provider, "messages": []}
        data["facts"] = self.facts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def facts_prompt(self) -> str:
        if not self.facts:
            return ""
        lines = "\n".join(f"- {k}: {v}" for k, v in self.facts.items())
        return f"\n\nThings the user has asked you to remember (use them naturally, don't recite them):\n{lines}"

    def add_message(self, role: str, content: Any = None, **extra: Any) -> None:
        msg: dict[str, Any] = {"role": role}
        if content is not None:
            msg["content"] = content
        msg.update(extra)
        self.messages.append(msg)

    def recent_messages(self) -> list[dict[str, Any]]:
        """The conversation to send to the model. Tool results from earlier turns
        (web search results especially) are cut short: the model already used them
        to answer, and resending them in full on every request eats the provider's
        per-minute token allowance."""
        msgs = self.messages[-(self.history_turns * 2):]
        current = max(
            (i for i, m in enumerate(msgs) if m.get("role") == "user" and isinstance(m.get("content"), str)),
            default=0,
        )
        return [self._trim_tool_output(m) if i < current else m for i, m in enumerate(msgs)]

    @staticmethod
    def _trim(text: Any) -> Any:
        if isinstance(text, str) and len(text) > _OLD_TOOL_OUTPUT_CHARS:
            return text[:_OLD_TOOL_OUTPUT_CHARS] + " ...[trimmed]"
        return text

    @classmethod
    def _trim_tool_output(cls, msg: dict[str, Any]) -> dict[str, Any]:
        if msg.get("role") == "tool":  # OpenAI-style tool result
            return {**msg, "content": cls._trim(msg.get("content"))}
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list):  # Anthropic-style tool results
            return {**msg, "content": [
                {**b, "content": cls._trim(b.get("content"))} if isinstance(b, dict) and b.get("type") == "tool_result" else b
                for b in content
            ]}
        return msg
