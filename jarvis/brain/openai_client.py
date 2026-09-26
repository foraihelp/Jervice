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
import math
import re
import threading
import time
from typing import Callable, Optional

import openai
from openai import OpenAI

from jarvis.brain.context import dynamic_context
from jarvis.brain import progress
from jarvis.brain.memory import Memory
from jarvis.brain.streaming import SentenceStreamer
from jarvis.tools.registry import TOOL_SCHEMAS, run_tool
from jarvis.tools.safety import begin_turn, end_turn

logger = logging.getLogger("jarvis.brain")

MAX_TOOL_ITERATIONS = 6  # safety cap against runaway tool-use loops
_STREAMING_REFUSED_STATUSES = {400, 404, 405, 415, 422, 501}
_MAX_ATTEMPTS = 3          # per model call, when the provider says "slow down"
_MAX_WAIT_SECONDS = 30     # longer than this and it is better to tell the user than to sit silent


def _retry_after_seconds(exc: "openai.RateLimitError", attempt: int) -> float:
    """How long the provider asks us to wait: its Retry-After header, else the
    "try again in 4.5s" / "1m2.5s" in its message, else a modest guess."""
    try:
        header = exc.response.headers.get("retry-after")
        if header:
            return float(header)
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"try again in (?:(\d+)m)?\s*([\d.]+)s", str(exc))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return 5.0 * attempt


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
        # No hidden SDK retries: they slept for a minute or more in silence, which looked like a
        # frozen app. _request() below retries a bounded number of times and shows why.
        self._client = OpenAI(api_key=api_key, base_url=base_url or None, max_retries=0)
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.memory = memory
        self._tools = _openai_tools()
        # See claude_client.AnthropicBrain.__init__ -- same reasoning:
        # serializes the local voice loop against remote (iOS) requests on
        # one shared conversation.
        self._lock = threading.Lock()

    def respond(self, user_text: str, on_sentence: Optional[Callable[[str], None]] = None) -> str:
        """Returns the final reply. If `on_sentence` is given, the whole
        reply is also delivered through it, one sentence at a time, while it
        is still being generated -- the caller should speak those instead of
        speaking the return value."""
        with self._lock:
            reply = self._respond_locked(user_text, on_sentence)
        end_turn(reply)
        return reply

    def _request(self, **kwargs):
        """chat.completions.create with bounded retries for rate limits (waiting as
        long as the provider asks, and saying so in the window) and for brief
        network/server hiccups."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                wait = _retry_after_seconds(exc, attempt)
                if getattr(exc, "code", None) == "insufficient_quota" or attempt >= _MAX_ATTEMPTS or wait > _MAX_WAIT_SECONDS:
                    raise
                progress.notify(f"AI PROVIDER RATE LIMIT · RETRYING IN {math.ceil(wait)}s")
                time.sleep(wait + 0.5)
            except (openai.APIConnectionError, openai.InternalServerError):
                if attempt >= 2:
                    raise
                progress.notify("AI PROVIDER UNREACHABLE · RETRYING")
                time.sleep(1.5)

    def _complete(self, messages: list[dict], streamer: Optional[SentenceStreamer]) -> tuple[str, list[dict]]:
        """One model call. Returns (text, tool_calls) where each tool call is
        {"id", "name", "arguments"}. Streams text through `streamer` when
        given; if the endpoint rejects streaming outright (some
        OpenAI-compatible servers do), quietly falls back to a plain call."""
        kwargs = dict(model=self.model, max_tokens=self.max_tokens, messages=messages, tools=self._tools)

        if streamer is not None:
            text_parts: list[str] = []
            calls: dict[int, dict] = {}
            try:
                for chunk in self._request(stream=True, **kwargs):
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text_parts.append(delta.content)
                        streamer.feed(delta.content)
                    for tc in delta.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name and not slot["name"]:
                                slot["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
            except Exception as exc:
                # Only fall back when the endpoint refused the streaming request itself.
                # Anything else (a rate limit, an outage) would fail again, and retrying
                # would just spend more of the rate limit.
                if text_parts or calls or getattr(exc, "status_code", None) not in _STREAMING_REFUSED_STATUSES:
                    raise
                logger.warning("Endpoint rejected streaming (HTTP %s); retrying without streaming.", exc.status_code)
            else:
                streamer.flush()
                tool_calls = [
                    {"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": c["arguments"]}
                    for i, c in sorted(calls.items())
                ]
                return "".join(text_parts).strip(), tool_calls

        message = self._request(**kwargs).choices[0].message
        tool_calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in message.tool_calls or []
        ]
        text = (message.content or "").strip()
        if streamer is not None and text:
            streamer.feed(text)
            streamer.flush()
        return text, tool_calls

    def _respond_locked(self, user_text: str, on_sentence: Optional[Callable[[str], None]]) -> str:
        streamer = SentenceStreamer(on_sentence) if on_sentence else None
        begin_turn(user_text)
        self.memory.add_message("user", user_text)

        for _ in range(MAX_TOOL_ITERATIONS):
            messages = [
                {"role": "system", "content": self.system_prompt + dynamic_context(self.memory)},
                *self.memory.recent_messages(),
            ]
            text, tool_calls = self._complete(messages, streamer)

            if not tool_calls:
                self.memory.add_message("assistant", text)
                self.memory.save()
                if not text:
                    text = "Done."
                    if streamer:
                        streamer.feed(text)
                        streamer.flush()
                return text

            self.memory.add_message(
                "assistant",
                content=text or None,
                tool_calls=[
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls
                ],
            )

            for tc in tool_calls:
                try:
                    tool_input = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                logger.info("Running tool %s(%s)", tc["name"], tool_input)
                result_text = run_tool(tc["name"], tool_input)
                self.memory.add_message("tool", content=result_text, tool_call_id=tc["id"])

        self.memory.save()
        message = "Sorry, that request needed too many steps -- can you break it down?"
        if streamer:
            streamer.feed(message)
            streamer.flush()
        return message
