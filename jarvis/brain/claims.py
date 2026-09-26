"""Catches a model saying it did something without having called a tool.

A real failure from the logs: asked "can I do this?", Jarvis answered "I've opened Notepad for
you" and called no tool at all, so nothing opened. Instructions to be honest did not stop it, so
the brains check for it: a first-person "I've opened / typed / saved / closed..." in a turn where
no tool ran is cut off before it is spoken, and the model is told to call the tool or correct itself.
"""

from __future__ import annotations

import re

_CLAIM = re.compile(
    r"\b(?:i(?:['’]ve|\s+have|\s+just|\s+already|\s+successfully)?|we(?:['’]ve|\s+have)?)\s+"
    r"(?:successfully\s+|just\s+|now\s+)?"
    r"(opened|launched|started|closed|created|saved|written|wrote|typed|pasted|copied|moved|deleted|"
    r"organi[sz]ed|locked|muted|unmuted|set\s+(?:a|the|your)|sent|turned|adjusted|increased|decreased|lowered|raised)\b",
    re.IGNORECASE,
)

NOTE = (
    "(Automatic check) You said you did something, but no tool was called during this request. "
    "If that really happened earlier in this conversation, repeat your answer unchanged. Otherwise, "
    "call the right tool now, or say plainly that you did not do it and what you can do instead."
)


class UnbackedClaim(Exception):
    """A reply claims an action but no tool ran."""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


def claims_action(text: str) -> bool:
    return bool(_CLAIM.search(text))
