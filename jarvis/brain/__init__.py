"""Picks the right brain implementation for config.brain_provider.

Adding another provider later: write a new module here exposing a class
with the same shape (`__init__(api_key, model, max_tokens, system_prompt,
memory, ...)` and a `.respond(text) -> str` method), then add one more
branch below.
"""

from __future__ import annotations

from typing import Any

from jarvis.brain.memory import Memory


def create_brain(config: Any, memory: Memory):
    """Returns an AnthropicBrain or OpenAIBrain instance, matching
    config.brain_provider ("anthropic" or "openai" -- the latter also
    covers any OpenAI-compatible endpoint via config.brain_base_url)."""
    if config.brain_provider == "openai":
        from jarvis.brain.openai_client import OpenAIBrain

        return OpenAIBrain(
            api_key=config.brain_api_key,
            model=config.brain_model,
            max_tokens=config.brain_max_tokens,
            system_prompt=config.system_prompt,
            memory=memory,
            base_url=config.brain_base_url,
        )

    from jarvis.brain.claude_client import AnthropicBrain

    return AnthropicBrain(
        api_key=config.brain_api_key,
        model=config.brain_model,
        max_tokens=config.brain_max_tokens,
        system_prompt=config.system_prompt,
        memory=memory,
    )
