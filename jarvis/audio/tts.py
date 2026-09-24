"""Text-to-speech: Windows SAPI5 voices via pyttsx3 (offline), plus optional
Microsoft neural voices via edge-tts (online, no API key) for Hindi/Bengali.

Run `python -m jarvis.audio.tts --list-voices` to see installed voice IDs
you can set in config.yaml under tts.voice_id.
"""

from __future__ import annotations

import argparse
import logging
import queue
import re
import threading

logger = logging.getLogger("jarvis.tts")

# Maps a reply_language config value to the locale prefix pyttsx3 reports in
# a SAPI5 voice's `.languages` (e.g. "hi-IN" for a Hindi voice) -- used to
# auto-pick an installed voice that can actually pronounce that language.
# An English SAPI5 voice reading Hindi/Bengali script produces garbage, not
# a graceful approximation, so this match is required, not cosmetic.
LANGUAGE_LOCALE_PREFIXES = {"english": "en", "hindi": "hi", "bengali": "bn"}

_EDGE_VOICES = {
    "english": "en-IN-NeerjaNeural",
    "hindi": "hi-IN-SwaraNeural",
    "bengali": "bn-IN-TanishaaNeural",
}
_EDGE_SAMPLE_RATE = 24000
_EDGE_TIMEOUT_SECONDS = 20


def clean_for_speech(text: str) -> str:
    """Strips markdown and other visual-only formatting so it isn't read
    aloud ("asterisk asterisk Weather"). The on-screen transcript keeps the
    original text; only what gets spoken is cleaned."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•+]\s+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|\*|~~)", "", t)
    t = t.replace("|", ", ")
    t = t.replace(" ", " ").replace(" ", " ").replace("​", "")
    # A line break with no punctuation before it becomes a sentence pause,
    # so list items and headings don't run together into one long sentence.
    t = re.sub(r"(?<![.!?:;,\s।])[ \t]*\n+\s*", ". ", t)
    t = re.sub(r"\s*\n+\s*", " ", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def detect_script_language(text: str) -> str | None:
    """"hindi" / "bengali" if the text is (mostly) written in Devanagari /
    Bengali script, else None -- lets the speaker route non-Latin replies to
    a voice that can pronounce them regardless of the Settings language."""
    devanagari = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    bengali = sum(1 for c in text if "ঀ" <= c <= "৿")
    if max(devanagari, bengali) < 3:
        return None
    return "hindi" if devanagari >= bengali else "bengali"


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

    `engine_mode` picks who speaks: "sapi" (Windows voices only), "edge"
    (Microsoft neural voices for everything, needs internet), or "auto"
    (default: SAPI, except Hindi/Bengali-script replies go to edge-tts since
    Windows doesn't ship voices for those). If edge-tts fails (offline),
    it falls back to a matching installed SAPI voice.
    """

    def __init__(
        self,
        rate: int,
        volume: float,
        voice_id: str = "",
        output_device: str = "",
        language: str = "",
        engine_mode: str = "auto",
    ):
        self._rate = rate
        self._volume = volume
        self._voice_id = voice_id
        # "english" / "hindi" / "bengali", or "" for whatever voice_id/the
        # system default already is. When set (and voice_id isn't also
        # explicitly set), overrides voice_id with an installed voice
        # matching this language -- see _auto_select_voice_for_language.
        self._language = (language or "").strip().lower()
        mode = (engine_mode or "auto").strip().lower()
        self._mode = mode if mode in ("auto", "sapi", "edge") else "auto"
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
        self._base_voice = None
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
        elif self._language and (self._mode == "sapi" or self._language == "english"):
            self._auto_select_voice_for_language(engine, self._language)
        if self._output_device:
            self._pin_output_device(engine, self._output_device)
        self._base_voice = engine.getProperty("voice")
        self._engine = engine
        self._ready.set()

        while True:
            text, extra = self._coalesce(self._queue.get())
            try:
                spoken = clean_for_speech(text)
                if spoken:
                    logger.info("Speaking: %r", spoken)
                    self._speak(engine, spoken)
            except Exception:  # noqa: BLE001 - one bad utterance shouldn't kill the speech thread
                logger.exception("TTS engine error")
            finally:
                for _ in range(1 + extra):
                    self._queue.task_done()

    def _coalesce(self, first: str) -> tuple[str, int]:
        """Streamed replies arrive as one sentence at a time. Local Windows
        voices start instantly, so those are spoken one by one. An online
        voice costs a network round trip per utterance, so sentences that
        piled up while the previous one was playing are joined and sent as
        one request -- one short pause instead of one between every
        sentence. Returns (text, number of extra queue items consumed)."""
        lang = detect_script_language(first)
        if not (self._mode == "edge" or (self._mode == "auto" and lang is not None)):
            return first, 0
        parts, extra = [first], 0
        while True:
            with self._queue.mutex:
                nxt = self._queue.queue[0] if self._queue.queue else None
            if nxt is None or detect_script_language(nxt) != lang:
                break
            parts.append(self._queue.get_nowait())
            extra += 1
        return " ".join(parts), extra

    def _speak(self, engine, text: str) -> None:
        lang = detect_script_language(text)
        if self._mode == "edge" or (self._mode == "auto" and lang is not None):
            try:
                self._speak_edge(text, lang)
                return
            except Exception as exc:  # noqa: BLE001 - offline / service hiccup: fall back to a local voice
                logger.warning("edge-tts failed (%s); falling back to a local Windows voice.", exc)
        self._speak_sapi(engine, text, lang)

    def _speak_sapi(self, engine, text: str, lang: str | None) -> None:
        if lang:
            voice_id = self._match_voice(engine.getProperty("voices"), lang, LANGUAGE_LOCALE_PREFIXES[lang])
            if not voice_id:
                logger.warning("No installed Windows voice can speak %s and the online voice is unavailable; skipping speech.", lang)
                return
            engine.setProperty("voice", voice_id)
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            if lang and self._base_voice:
                engine.setProperty("voice", self._base_voice)

    def _speak_edge(self, text: str, lang: str | None) -> None:
        import asyncio
        import io

        import av
        import edge_tts
        import numpy as np
        import sounddevice as sd

        from jarvis.audio.devices import resolve_device

        voice = _EDGE_VOICES[lang or "english"]
        percent = max(-50, min(100, round((self._rate / 200 - 1) * 100)))

        async def fetch() -> bytes:
            data = bytearray()
            async for chunk in edge_tts.Communicate(text, voice, rate=f"{percent:+d}%").stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
            return bytes(data)

        mp3 = asyncio.run(asyncio.wait_for(fetch(), timeout=_EDGE_TIMEOUT_SECONDS))
        if not mp3:
            raise RuntimeError("edge-tts returned no audio")

        resampler = av.AudioResampler(format="s16", layout="mono", rate=_EDGE_SAMPLE_RATE)
        pieces = []
        with av.open(io.BytesIO(mp3)) as container:
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    pieces.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            pieces.append(out.to_ndarray().reshape(-1))
        if not pieces:
            raise RuntimeError("edge-tts audio could not be decoded")

        audio = np.concatenate(pieces).astype(np.float32) / 32768.0 * float(self._volume)
        logger.info("Speaking via edge-tts voice %s", voice)
        sd.play(audio, samplerate=_EDGE_SAMPLE_RATE, device=resolve_device("output", self._output_device))
        sd.wait()

    def _auto_select_voice_for_language(self, engine, language: str) -> None:
        """Picks an installed SAPI5 voice that can actually speak
        `language` (matched by locale prefix, e.g. "hi" for Hindi; falls
        back to a name substring match since not every driver populates
        `.languages` reliably) and sets it on `engine`. If nothing matches,
        logs a clear warning and leaves the current (likely English) voice
        in place rather than silently mispronouncing the reply -- Windows
        doesn't ship Hindi/Bengali voices by default, so this is a real,
        expected case, not just a defensive fallback."""
        prefix = LANGUAGE_LOCALE_PREFIXES.get(language)
        voice_id = self._match_voice(engine.getProperty("voices"), language, prefix)
        if voice_id:
            engine.setProperty("voice", voice_id)
            logger.info("TTS voice auto-selected for language %r: %s", language, voice_id)
        else:
            logger.warning(
                "No installed voice found for language %r -- speech will use the current "
                "default voice, which likely can't pronounce it correctly. Install a matching "
                "voice via Windows Settings -> Time & Language -> Speech.",
                language,
            )

    @staticmethod
    def _match_voice(voices, language: str, prefix: str | None) -> str | None:
        for v in voices:
            langs = [str(l).lower() for l in (getattr(v, "languages", None) or [])]
            if prefix and any(l == prefix or l.startswith(prefix + "-") for l in langs):
                return v.id
        for v in voices:
            if language in (v.name or "").lower():
                return v.id
        return None

    @staticmethod
    def has_voice_for_language(language: str) -> bool:
        """Whether an installed SAPI5 voice matches `language` ("english" /
        "hindi" / "bengali") -- used by the Settings window to warn the
        user up front instead of them finding out by getting silence or
        garbled speech after saving."""
        import pyttsx3

        language = (language or "").strip().lower()
        if not language:
            return True
        engine = pyttsx3.init()
        try:
            prefix = LANGUAGE_LOCALE_PREFIXES.get(language)
            return Speaker._match_voice(engine.getProperty("voices"), language, prefix) is not None
        finally:
            try:
                engine.stop()
            except Exception:
                pass

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
