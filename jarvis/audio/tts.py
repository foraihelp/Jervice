"""Local text-to-speech via pyttsx3 (wraps Windows SAPI5 voices).

Run `python -m jarvis.audio.tts --list-voices` to see installed voice IDs
you can set in config.yaml under tts.voice_id.
"""

from __future__ import annotations

import argparse
import logging
import threading

logger = logging.getLogger("jarvis.tts")


class Speaker:
    def __init__(self, rate: int, volume: float, voice_id: str = ""):
        import pyttsx3

        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", rate)
        self._engine.setProperty("volume", volume)
        if voice_id:
            self._engine.setProperty("voice", voice_id)
        # pyttsx3's engine isn't safe to drive from two threads at once --
        # now that both the wake-word thread and the orb-click/typed-command
        # bridge calls can speak a reply, a lock keeps overlapping requests
        # from garbling each other instead of just queuing up normally.
        self._lock = threading.Lock()

    def say(self, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            logger.info("Speaking: %r", text)
            self._engine.say(text)
            self._engine.runAndWait()

    def list_voices(self) -> list[tuple[str, str]]:
        voices = self._engine.getProperty("voices")
        return [(v.id, v.name) for v in voices]


def _cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument("--say", type=str, default=None)
    args = parser.parse_args()

    speaker = Speaker(rate=185, volume=1.0)

    if args.list_voices:
        for voice_id, name in speaker.list_voices():
            print(f"{voice_id}\t{name}")
    if args.say:
        speaker.say(args.say)


if __name__ == "__main__":
    _cli()
