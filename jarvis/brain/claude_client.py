"""The 'brain': sends the user's transcribed speech to Claude, runs Claude's
tool-use loop against the local tool registry, and returns the final text
reply to be spoken back.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from anthropic import Anthropic

from jarvis.brain import claims
from jarvis.brain.context import dynamic_context
from jarvis.brain.memory import Memory
from jarvis.brain.streaming import SentenceStreamer
from jarvis.tools.registry import TOOL_SCHEMAS, call_count, run_tool
from jarvis.tools.safety import begin_turn, end_turn

logger = logging.getLogger("jarvis.brain")

MAX_TOOL_ITERATIONS = 6  # safety cap against runaway tool-use loops


class AnthropicBrain:
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

    def respond(self, user_text: str, on_sentence: Optional[Callable[[str], None]] = None) -> str:
        """Sends `user_text` to Claude, executes any tool calls it requests,
        and returns the final natural-language reply. Thread-safe: calls
        from multiple clients (local voice loop + remote API) are serialized.

        If `on_sentence` is given, the whole reply is also delivered through
        it, one sentence at a time, while it is still being generated -- the
        caller should speak those instead of speaking the return value."""
        with self._lock:
            reply = self._respond_locked(user_text, on_sentence)
        end_turn(reply)
        return reply

    def _create(self, streamer: Optional[SentenceStreamer]):
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt + dynamic_context(self.memory),
            tools=TOOL_SCHEMAS,
            messages=self.memory.recent_messages(),
        )
        if streamer is None:
            return self._client.messages.create(**kwargs)
        with self._client.messages.stream(**kwargs) as stream:
            for delta in stream.text_stream:
                streamer.feed(delta)
            message = stream.get_final_message()
        # Flushed after every turn, so a short lead-in before a tool call
        # ("Let me check that.") is spoken while the tool runs.
        streamer.flush()
        return message

    def _respond_locked(self, user_text: str, on_sentence: Optional[Callable[[str], None]]) -> str:
        start_calls = call_count()
        checked = {"done": False}   # the claim check runs once per request

        def guarded(sentence: str) -> None:
            if not checked["done"] and call_count() == start_calls and claims.claims_action(sentence):
                raise claims.UnbackedClaim(sentence)
            on_sentence(sentence)

        streamer = SentenceStreamer(guarded) if on_sentence else None
        begin_turn(user_text)
        self.memory.add_message("user", user_text)

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                response = self._create(streamer)
                if streamer is None and response.stop_reason != "tool_use" and not checked["done"] and call_count() == start_calls:
                    said = "".join(b.text for b in response.content if b.type == "text")
                    if claims.claims_action(said):
                        raise claims.UnbackedClaim(said)
            except claims.UnbackedClaim as claim:
                logger.warning("Reply claimed an action but no tool ran: %r", claim.sentence)
                checked["done"] = True
                self.memory.add_message("assistant", [{"type": "text", "text": claim.sentence}])
                self.memory.add_message("user", claims.NOTE)
                streamer = SentenceStreamer(on_sentence) if on_sentence else None
                continue

            # Plain dicts, not the SDK's block objects: memory is saved as
            # JSON, which can't serialize those, and dicts are accepted
            # back by the API unchanged.
            self.memory.add_message(
                "assistant", [block.model_dump(mode="json", exclude_none=True) for block in response.content]
            )

            if response.stop_reason != "tool_use":
                text = "".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                self.memory.save()
                if not text:
                    text = "Done."
                    if streamer:
                        streamer.feed(text)
                        streamer.flush()
                return text

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
        message = "Sorry, that request needed too many steps -- can you break it down?"
        if streamer:
            streamer.feed(message)
            streamer.flush()
        return message
