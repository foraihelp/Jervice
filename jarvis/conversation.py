"""One wake-word activation: the first command, then follow-ups.

After Jarvis answers, it keeps listening for a few seconds without needing the
wake word again, so a conversation (or a "yes" to a confirmation question) flows
naturally. The loop ends when nobody speaks in time, when the user says thanks or
"that's all", when the user presses Stop, or when the turn cap is reached. The
dependencies are passed in as callables so the loop can be tested without a
microphone, model or window.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

import numpy as np

from jarvis.audio.errors import MicrophoneError

logger = logging.getLogger("jarvis.conversation")

IDLE_STATUS = "WAKE WORD · HEY JARVIS"
MAX_TURNS = 8
_SPEECH_WAIT_SECONDS = 180

_DISMISSALS = {
    "thanks", "thank you", "thanks jarvis", "thank you jarvis", "thats all", "that is all", "thats it",
    "no thanks", "nothing", "nothing else", "never mind", "nevermind", "goodbye", "bye", "stop", "cancel",
    "okay thanks", "ok thanks", "ok thank you", "okay thank you", "no", "nope",
}


def is_dismissal(text: str) -> bool:
    """True when the user is just ending the conversation ("thanks", "that's all")."""
    cleaned = re.sub(r"[^\w\s]", "", text.lower().replace("’", "").replace("'", "")).strip()
    return cleaned in _DISMISSALS


class VoiceConversation:
    def __init__(
        self,
        *,
        record: Callable[[Optional[float]], np.ndarray],
        transcribe: Callable[[np.ndarray], str],
        respond: Callable[[str], tuple[str, list[str]]],
        speaker,
        is_muted: Callable[[], bool],
        on_status: Callable[..., None],
        on_message: Callable[..., None],
        follow_up_seconds: float,
        max_turns: int = MAX_TURNS,
        settle_seconds: float = 0.4,
    ):
        self._record = record
        self._transcribe = transcribe
        self._respond = respond
        self._speaker = speaker
        self._is_muted = is_muted
        self._status = on_status
        self._message = on_message
        self._follow_up_seconds = follow_up_seconds
        self._max_turns = max_turns
        self._settle_seconds = settle_seconds

    def run(self) -> None:
        turn = 0
        timeout: Optional[float] = None
        try:
            while True:
                if self._is_muted():
                    logger.debug("Microphone is muted; ending conversation.")
                    return
                self._status(listening=True, statusLine="LISTENING..." if turn == 0 else "LISTENING FOR FOLLOW-UP...")
                try:
                    audio = self._record(timeout)
                except MicrophoneError as exc:
                    # Uncaught, this would kill the wake-word thread: this runs inside the
                    # listener's own callback with nothing else to catch it.
                    logger.warning("Microphone error while recording a command: %s", exc)
                    self._message("jarvis", str(exc))
                    return

                text = self._transcribe(audio) if audio.size else ""
                if not text:
                    logger.info("Heard nothing%s.", "" if turn == 0 else " more; conversation over")
                    return
                if turn > 0 and is_dismissal(text):
                    logger.info("User ended the conversation: %r", text)
                    self._message("user", text)
                    return

                self._message("user", text)
                self._status(listening=False, statusLine="THINKING...")
                generation = self._speaker.generation
                reply, tools = self._respond(text)
                self._message("jarvis", reply, tools)

                turn += 1
                if self._follow_up_seconds <= 0 or turn >= self._max_turns:
                    return
                self._speaker.wait_until_idle(timeout=_SPEECH_WAIT_SECONDS)
                if self._speaker.generation != generation:
                    return  # the user pressed Stop
                time.sleep(self._settle_seconds)  # let the room (and the speakers) go quiet before listening
                timeout = self._follow_up_seconds
        finally:
            self._status(listening=False, statusLine=IDLE_STATUS)
