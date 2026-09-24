"""Sentence-by-sentence speech: the brains stream the model's reply token by
token, SentenceStreamer cuts that stream into speakable sentences, and each
one goes straight to the Speaker's queue -- so Jarvis starts talking after the
first sentence is written instead of after the whole reply is finished.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("jarvis.brain")

# Latin and Devanagari/Bengali sentence-enders (the danda is Hindi/Bengali's full stop).
_TERMINATORS = ".!?।॥"


class SentenceStreamer:
    """Feed it text deltas; it calls `emit(sentence)` for each complete
    sentence. Very short sentences ("Sure.") are held and joined with the
    next one so speech doesn't come out in choppy fragments, and a full stop
    only counts as a sentence end when whitespace follows it, so "3.5" or
    "example.com" aren't split."""

    def __init__(self, emit: Callable[[str], None], min_chars: int = 25):
        self._emit = emit
        self._min_chars = min_chars
        self._buffer = ""

    def feed(self, delta: str) -> None:
        self._buffer += delta
        self._drain()

    def flush(self) -> None:
        """Emits whatever is left (the reply's last, possibly unpunctuated, sentence)."""
        self._drain()
        rest = self._buffer.strip()
        self._buffer = ""
        if rest:
            self._emit(rest)

    def _drain(self) -> None:
        buf = self._buffer
        cut = 0
        for i, ch in enumerate(buf):
            if ch == "\n":
                boundary = True
            elif ch in _TERMINATORS:
                boundary = i + 1 < len(buf) and buf[i + 1].isspace()
            else:
                continue
            if boundary and len(buf[cut : i + 1].strip()) >= self._min_chars:
                self._emit(buf[cut : i + 1].strip())
                cut = i + 1
        self._buffer = buf[cut:]


class SpeechCancelled(Exception):
    """Raised inside a brain's streaming loop once the user has pressed Stop,
    to abandon the rest of the reply (and close the connection to the model)
    instead of generating text nobody will hear."""


def respond_speaking(brain, text: str, speaker, error_reply: str) -> str:
    """Runs one brain turn, speaking the reply sentence by sentence as it is
    generated (or all at once for a brain that can't stream). Never raises: a
    brain failure is logged and `error_reply` is spoken and returned instead,
    so a bad API key or a network drop can't leave the user with silence.
    If the user presses Stop (Speaker.stop()) mid-reply, generation is
    abandoned and only the part that was already spoken is returned.
    Returns the reply text for display."""
    if speaker is None:
        try:
            return brain.respond(text)
        except Exception:  # noqa: BLE001
            logger.exception("brain.respond() failed for %r", text)
            return error_reply

    generation = speaker.generation
    delivered: list[str] = []

    def on_sentence(sentence: str) -> None:
        if speaker.generation != generation:
            raise SpeechCancelled
        delivered.append(sentence)
        speaker.say(sentence, generation=generation)

    try:
        return brain.respond(text, on_sentence=on_sentence)
    except SpeechCancelled:
        logger.info("Reply stopped by the user.")
        return " ".join(delivered + ["[stopped]"])
    except Exception:  # noqa: BLE001 - one bad turn must not kill the caller
        logger.exception("brain.respond() failed for %r", text)
        if speaker.generation == generation:
            speaker.say(error_reply)
        return error_reply
