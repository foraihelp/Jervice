"""The 'brain': sends the user's transcribed speech to Claude, runs Claude's
tool-use loop against the local tool registry, and returns the final text
reply to be spoken back.
"""

from __future__ import annotations

import logging
import threading

from anthropic import Anthropic

from jarvis.brain.memory import Memory
from jarvis.tools.registry import TOOL_SCHEMAS, run_tool

logger = logging.getLogger("jarvis.brain")

MAX_TOOL_ITERATIONS = 6  # safety cap against runaway tool-use loops


class Brain:
    def __init__(self, api_key: str, model: str, max_tokens: int, system_prompt: str, memory: Memory):
        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.memory = memory
        # Guards against interleaved conversation state if a voice command
        # (wake-word loop) and a remote API request (iOS app) land at the
        # same time -- this is a single-user assistant with one shared
        # conversation, so requests are serialized rather than run concurrently.
        self._lock = threading.Lock()

    def respond(self, user_text: str) -> str:
        """Sends `user_text` to Claude, executes any tool calls it requests,
        and returns the final natural-language reply. Thread-safe: calls
        from multiple clients (local voice loop + remote API) are serialized."""
        with self._lock:
            return self._respond_locked(user_text)

    def _respond_locked(self, user_text: str) -> str:
        self.memory.add_message("user", user_text)

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                tools=TOOL_SCHEMAS,
                messages=self.memory.recent_messages(),
            )

            self.memory.add_message("assistant", response.content)

            if response.stop_reason != "tool_use":
                text = "".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                self.memory.save()
                return text or "Done."

            # Execute every tool_use block in this response, collect results.
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                logger.info("Running tool %s(%s)", block.name, block.input)
                result_text = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            self.memory.add_message("user", tool_results)

        self.memory.save()
        return "Sorry, that request needed too many steps -- can you break it down?"
