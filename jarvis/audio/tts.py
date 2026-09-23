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

    def __init__(self, rate: int, volume: float, voice_id: str = "", output_device: str = ""):
        self._rate = rate
        self._volume = volume
        self._voice_id = voice_id
        # Substring match (case-insensitive) against a SAPI5 audio output
        # device name, e.g. "Realtek" or "BenQ". Empty = leave it on
        # whatever Windows' system-wide default output is. Pinning this
        # explicitly matters because the system default can silently be a
        # Bluetooth headset that isn't actually turned on/connected --
        # Windows keeps it "default" even while disconnected, so TTS speech
        # just vanishes into a device nothing is listening to. SAPI5 lets an
        # application pin its own output independently of that Windows-wide
        # default (there's no supported API to change the Windows default
        # itself -- see jarvis/tools/system.py's get_default_output_device).
        self._output_device = output_device
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
        if self._output_device:
            self._pin_output_device(engine, self._output_device)
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

    def _pin_output_device(self, engine, name_substring: str) -> None:
        """Sets SAPI5's per-voice AudioOutput property so this engine's
        speech always goes to a specific device, regardless of what Windows
        currently considers the default. Must run on the worker thread
        (same COM-apartment rule as everything else on `engine`)."""
        try:
            tts = engine.proxy._driver._tts  # raw ISpeechVoice COM object pyttsx3 wraps
            outputs = tts.GetAudioOutputs()
            wanted = name_substring.strip().lower()
            for i in range(outputs.Count):
                token = outputs.Item(i)
                desc = token.GetDescription()
                if wanted in desc.lower():
                    tts.AudioOutput = token
                    logger.info("TTS output pinned to %r (matched %r)", desc, name_substring)
                    return
            available = [outputs.Item(i).GetDescription() for i in range(outputs.Count)]
            logger.warning(
                "Configured TTS output device %r not found; leaving as system default. Available: %s",
                name_substring, available,
            )
        except Exception:  # noqa: BLE001 - a bad/stale device name must not break TTS entirely
            logger.exception("Could not pin TTS output device %r; leaving as system default", name_substring)

    @staticmethod
    def list_output_devices() -> list[str]:
        """Enumerates SAPI5 audio output device names, for the Settings
        window's device picker. Synchronous and self-contained (creates its
        own short-lived pyttsx3 engine) since it's only ever called from a
        Settings UI action, not the hot speech path."""
        import pyttsx3

        engine = pyttsx3.init()
        try:
            tts = engine.proxy._driver._tts
            outputs = tts.GetAudioOutputs()
            return [outputs.Item(i).GetDescription() for i in range(outputs.Count)]
        finally:
            try:
                engine.stop()
            except Exception:
                pass

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
