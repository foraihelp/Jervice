"""The 'brain' for OpenAI and any OpenAI-compatible endpoint (Groq,
Together, OpenRouter, DeepSeek, Azure OpenAI, a local Ollama/LM Studio
server, ...). Same job as claude_client.py's AnthropicBrain -- sends the
user's transcribed speech to the model, runs its function-calling loop
against the local tool registry, and returns the final text reply -- just
against the Chat Completions API shape instead of Anthropic's Messages API.
"""

from __future__ import annotations

import json
import logging
import threading

from openai import OpenAI

from jarvis.brain.memory import Memory
from jarvis.tools.registry import TOOL_SCHEMAS, run_tool

logger = logging.getLogger("jarvis.brain")

MAX_TOOL_ITERATIONS = 6  # safety cap against runaway tool-use loops


def _openai_tools() -> list[dict]:
    """Converts the shared Anthropic-shaped TOOL_SCHEMAS (name/description/
    input_schema) into OpenAI's function-calling tool format, so both
    brains dispatch through the same tool registry without duplicating
    every tool's schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_SCHEMAS
    ]


class OpenAIBrain:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        system_prompt: str,
        memory: Memory,
        base_url: str = "",
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.memory = memory
        self._tools = _openai_tools()
        # See claude_client.AnthropicBrain.__init__ -- same reasoning:
        # serializes the local voice loop against remote (iOS) requests on
        # one shared conversation.
        self._lock = threading.Lock()

    def respond(self, user_text: str) -> str:
        with self._lock:
            return self._respond_locked(user_text)

    def _respond_locked(self, user_text: str) -> str:
        self.memory.add_message("user", user_text)

        for _ in range(MAX_TOOL_ITERATIONS):
            messages = [{"role": "system", "content": self.system_prompt}, *self.memory.recent_messages()]
            response = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=messages,
                tools=self._tools,
            )
            message = response.choices[0].message

            if not message.tool_calls:
                text = (message.content or "").strip()
                self.memory.add_message("assistant", text)
                self.memory.save()
                return text or "Done."

            self.memory.add_message(
                "assistant",
                content=message.content,
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ],
            )

            for tc in message.tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                logger.info("Running tool %s(%s)", tc.function.name, tool_input)
                result_text = run_tool(tc.function.name, tool_input)
                self.memory.add_message("tool", content=result_text, tool_call_id=tc.id)

        self.memory.save()
        return "Sorry, that request needed too many steps -- can you break it down?"
