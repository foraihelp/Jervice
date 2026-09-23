"""Local text-to-speech via pyttsx3 (wraps Windows SAPI5 voices).

Run `python -m jarvis.audio.tts --list-voices` to see installed voice IDs
you can set in config.yaml under tts.voice_id.
"""

from __future__ import annotations

import argparse
import logging
import queue
import threading

logger = logging.getLogger("jarvis.tts")


class Speaker:
    """Runs pyttsx3 (backed by SAPI5, a COM object) on one dedicated
    background thread that owns it for its entire life, and exposes a
    thread-safe say() that just drops text on a queue for that thread to
    pick up.

    This matters more than it looks: SAPI5's COM object is tied to
    whichever thread created it, and calling into it from a *different*
    thread without the right apartment/marshaling setup doesn't
    necessarily raise an error -- it can silently block forever waiting on
    a Windows message loop that never runs on that thread. That's exactly
    what happened before this was a queue: main.py builds the Speaker on
    the main thread, but jarvis/ui/window.py's send_command()/
    trigger_listen() run on pywebview's own bridge-call thread, and having
    those call self._engine.say()/.runAndWait() directly hung the entire
    bridge (not just speech) the first time a real reply needed to be
    spoken from either of those two paths. Routing every caller through
    this queue means the actual pyttsx3 engine is only ever touched by the
    one thread that created it, regardless of which thread calls say().
    """

    def __init__(self, rate: int, volume: float, voice_id: str = ""):
        self._rate = rate
        self._volume = volume
        self._voice_id = voice_id
        self._queue: queue.Queue[str] = queue.Queue()
        self._engine = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", self._rate)
        engine.setProperty("volume", self._volume)
        if self._voice_id:
            engine.setProperty("voice", self._voice_id)
        self._engine = engine
        self._ready.set()

        while True:
            text = self._queue.get()
            try:
                logger.info("Speaking: %r", text)
                engine.say(text)
                engine.runAndWait()
            except Exception:  # noqa: BLE001 - one bad utterance shouldn't kill the speech thread
                logger.exception("TTS engine error")
            finally:
                self._queue.task_done()

    def say(self, text: str) -> None:
        if not text.strip():
            return
        self._queue.put(text)

    def wait_until_idle(self, timeout: float | None = None) -> None:
        """Blocks until every say() call made so far has finished playing.
        Only needed by the CLI below (the app itself is fire-and-forget by
        design -- callers don't wait on speech)."""
        self._queue.join() if timeout is None else self._wait_join_with_timeout(timeout)

    def _wait_join_with_timeout(self, timeout: float) -> None:
        # queue.Queue.join() has no timeout parameter; poll unfinished_tasks
        # instead so the CLI can't hang forever if something goes wrong.
        import time

        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks > 0 and time.monotonic() < deadline:
            time.sleep(0.1)

    def list_voices(self) -> list[tuple[str, str]]:
        # Only the CLI below calls this, synchronously, right after
        # construction -- give the worker thread a moment to finish
        # pyttsx3.init() if it hasn't already.
        self._ready.wait(timeout=10)
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
        speaker.wait_until_idle(timeout=15)  # keep the CLI alive long enough to actually hear it


if __name__ == "__main__":
    _cli()
