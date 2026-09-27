"""Picks the right brain implementation for config.brain_provider.

Adding another provider later: write a new module here exposing a class
with the same shape (`__init__(api_key, model, max_tokens, system_prompt,
memory, ...)` and a `.respond(text) -> str` method), then add one more
branch below.
"""

from __future__ import annotations

from typing import Any

from jarvis.brain.memory import Memory


class BrainHolder:
    """A mutable box around the active brain instance.

    Both the main window (JarvisAPI) and the remote iOS API server
    (jarvis/server.py) read `.brain` through this holder rather than
    closing over a Brain object directly, so that switching AI provider in
    Settings can swap the live brain in place -- no restart required. See
    SettingsAPI.save_settings in jarvis/ui/window.py, which rebuilds and
    reassigns `.brain` after a provider/model/key change.
    """

    def __init__(self, brain):
        self.brain = brain


def _build_brain(spec, config: Any, memory: Memory, patient: bool):
    """One brain for one provider."""
    if spec.provider == "openai":
        from jarvis.brain.openai_client import OpenAIBrain

        return OpenAIBrain(
            api_key=spec.api_key,
            model=spec.model,
            max_tokens=config.brain_max_tokens,
            system_prompt=config.effective_system_prompt,
            memory=memory,
            base_url=spec.base_url,
            patient=patient,
        )

    from jarvis.brain.claude_client import AnthropicBrain

    return AnthropicBrain(
        api_key=spec.api_key,
        model=spec.model,
        max_tokens=config.brain_max_tokens,
        system_prompt=config.effective_system_prompt,
        memory=memory,
        patient=patient,
    )


def create_brain(config: Any, memory: Memory):
    """Returns the brain for config.brain_provider ("anthropic" or "openai" -- the latter also
    covers any OpenAI-compatible endpoint via config.brain_base_url). When backup providers are
    set up in Settings, returns a FallbackBrain that tries them in turn if the main one is
    rate-limited or unreachable; without backups it is just the one brain, unchanged."""
    backups = config.fallback_specs
    primary = _build_brain(config.primary_spec, config, memory, patient=not backups)
    if not backups:
        return primary

    from jarvis.brain.fallback import FallbackBrain

    members = []
    for index, spec in enumerate(backups):
        is_last = index == len(backups) - 1
        # Only the last provider in the chain waits out a rate limit; the others give up at once.
        blank = Memory.in_memory(config.history_turns, spec.provider, [], {})
        members.append((_build_brain(spec, config, blank, patient=is_last), spec.label, spec.provider))
    return FallbackBrain(primary, members, config.history_turns)
