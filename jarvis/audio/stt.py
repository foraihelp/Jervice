"""Speech-to-text.

By default this runs fully offline on this PC with faster-whisper
(CTranslate2-accelerated Whisper), and no audio leaves the machine. Optionally,
recognition can be sent to the AI provider's hosted Whisper instead (Groq or
OpenAI): far more accurate for Hindi and Bengali, and fast, but it uploads each
voice recording to that provider, so it is opt-in. If the cloud request fails
for any reason, the local model handles that utterance.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import wave
from typing import Any, Callable, Optional

import numpy as np

from jarvis.audio.errors import SpeechNotReady

logger = logging.getLogger("jarvis.stt")

_CLOUD_TIMEOUT_SECONDS = 20


def _wav_bytes(audio_int16: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(audio_int16.tobytes())
    return buf.getvalue()


class CloudTranscriber:
    """Transcribes through an OpenAI-compatible /audio/transcriptions endpoint."""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=_CLOUD_TIMEOUT_SECONDS, max_retries=0)

    def transcribe(self, audio_int16: np.ndarray, sample_rate: int, language: Optional[str]) -> str:
        kwargs: dict[str, Any] = {"language": language} if language else {}
        result = self._client.audio.transcriptions.create(
            model=self.model, file=("speech.wav", _wav_bytes(audio_int16, sample_rate)), **kwargs
        )
        return (result.text or "").strip()


# Download sizes of the Whisper models (MB), to show a percentage while the first one downloads.
_MODEL_SIZE_MB = {"tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145, "small": 486, "small.en": 486,
                  "medium": 1530, "medium.en": 1530, "large-v3": 3090}


def _model_repo_dir(model_size: str) -> Optional[str]:
    """Where Hugging Face keeps this model on disk, or None if that can't be worked out."""
    try:
        from faster_whisper.utils import _MODELS
        from huggingface_hub.constants import HF_HUB_CACHE

        repo = _MODELS.get(model_size)
        return os.path.join(HF_HUB_CACHE, "models--" + repo.replace("/", "--")) if repo else None
    except Exception:  # noqa: BLE001
        return None


def _folder_bytes(path: Optional[str]) -> int:
    total = 0
    if path and os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    return total


class Transcriber:
    """Speech-to-text. The local model is loaded in the background by start_loading(), so
    the window can open at once even on a first run that has to download it."""

    def __init__(self, model_size: str, device: str, compute_type: str, language: str = "en"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        # "en" / "hi" / "bn", or "auto" to let Whisper guess. Guessing is unreliable
        # for short commands and especially for Bengali, so a fixed language is better.
        self.language = language
        self.engine = "local"
        self._cloud: Optional[CloudTranscriber] = None
        self._model = None
        self._lock = threading.Lock()
        self._loading = False
        self.state = "idle"        # idle | loading | downloading | ready | failed
        self.detail = ""           # short text for the status line, e.g. "DOWNLOADING SPEECH MODEL 42%"
        self.error = ""            # why the last load failed
        self._listener: Optional[Callable[[str, str], None]] = None

    # -- loading -------------------------------------------------------------------

    def set_listener(self, listener: Optional[Callable[[str, str], None]]) -> None:
        """listener(state, detail) is called from the loading thread whenever they change."""
        self._listener = listener

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        if self._listener is not None:
            try:
                self._listener(state, detail)
            except Exception:  # noqa: BLE001 - a broken UI callback must not stop the load
                logger.debug("Speech status listener failed", exc_info=True)

    def start_loading(self) -> None:
        """Starts loading (and if needed downloading) the local model without waiting for it."""
        with self._lock:
            if self._model is not None or self._loading:
                return
            self._loading = True
        threading.Thread(target=self._load, name="whisper-load", daemon=True).start()

    def _is_cached(self) -> bool:
        try:
            from faster_whisper.utils import download_model

            download_model(self.model_size, local_files_only=True)
            return True
        except Exception:  # noqa: BLE001 - not downloaded yet
            return False

    def _watch_download(self, stop: threading.Event) -> None:
        folder = _model_repo_dir(self.model_size)
        total = _MODEL_SIZE_MB.get(self.model_size, 0) * 1024 * 1024
        while not stop.wait(0.7):
            done = _folder_bytes(folder)
            if total:
                self._set_state("downloading", f"DOWNLOADING SPEECH MODEL {min(99, int(done * 100 / total))}%")
            else:
                self._set_state("downloading", f"DOWNLOADING SPEECH MODEL {done // (1024 * 1024)} MB")

    def _load(self) -> None:
        try:
            from faster_whisper import WhisperModel

            if not self._is_cached():
                mb = _MODEL_SIZE_MB.get(self.model_size)
                logger.info("Downloading the Whisper '%s' model (first run only)...", self.model_size)
                self._set_state("downloading", "DOWNLOADING SPEECH MODEL" + (f" (~{mb} MB)" if mb else ""))
                stop = threading.Event()
                threading.Thread(target=self._watch_download, args=(stop,), daemon=True).start()
                try:
                    from faster_whisper.utils import download_model

                    download_model(self.model_size)
                finally:
                    stop.set()
            self._set_state("loading", "LOADING SPEECH MODEL...")
            logger.info("Loading Whisper model '%s' (device=%s, compute_type=%s)...",
                        self.model_size, self.device, self.compute_type)
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            self.error = ""
            logger.info("Speech model ready.")
            self._set_state("ready", "")
        except Exception as exc:  # noqa: BLE001 - offline, disk full, blocked by a firewall...
            logger.warning("Could not load the speech model: %s", exc, exc_info=True)
            self.error = str(exc)
            self._set_state("failed", "SPEECH MODEL UNAVAILABLE")
        finally:
            self._loading = False

    def is_available(self) -> bool:
        """True when a voice command can be understood right now."""
        return self._cloud is not None or self._model is not None

    def not_ready_message(self) -> str:
        """What to tell the person when voice can't be understood yet."""
        if self.state in ("downloading", "loading") or self._loading:
            return ("I'm still getting my speech recognition ready (the first start downloads it once). "
                    "You can type to me meanwhile, and voice will work in a minute.")
        return ("I can't understand speech right now because my speech model isn't available. It has to be "
                "downloaded once, so please connect to the internet and restart me. Typing works "
                "in the meantime, or you can switch speech recognition to the cloud in Settings.")

    def apply_config(self, config) -> None:
        """Takes the speech language and engine from a Config. Called at startup and
        again whenever Settings are saved, so changes apply without a restart."""
        self.language = config.stt_language
        self.engine = config.stt_engine
        self._cloud = None
        if self.engine == "cloud":
            if config.brain_provider == "openai" and config.has_api_key:
                self._cloud = CloudTranscriber(config.brain_api_key, config.brain_base_url, config.stt_cloud_model)
                logger.info("Speech recognition: cloud (%s), language=%s", config.stt_cloud_model, self.language)
            else:
                logger.warning(
                    "Cloud speech recognition needs an OpenAI-compatible AI provider (such as Groq) "
                    "with an API key; using this PC instead."
                )
        if self._cloud is None:
            logger.info("Speech recognition: on this PC, language=%s", self.language)
            if self.state == "failed":
                self.start_loading()  # a retry, e.g. now that the person is back online

    def transcribe(self, audio_int16: np.ndarray, sample_rate: int) -> str:
        """Transcribes int16 PCM mono audio at `sample_rate` and returns text."""
        if audio_int16.size == 0:
            return ""

        language = None if self.language in ("", "auto") else self.language

        if self._cloud is not None:
            try:
                text = self._cloud.transcribe(audio_int16, sample_rate, language)
                logger.info("Transcribed (cloud): %r", text)
                return text
            except Exception as exc:  # noqa: BLE001 - offline, rate-limited, unsupported endpoint...
                logger.warning("Cloud transcription failed (%s); using this PC for this one.", exc)

        if self._model is None:
            message = self.not_ready_message()  # worded for the state it is in now, before any retry starts
            self.start_loading()                # retries after a failure; no-op while it is already loading
            raise SpeechNotReady(message)

        # faster-whisper expects float32 PCM in [-1, 1].
        audio_float32 = (audio_int16.astype(np.float32) / 32768.0)

        segments, info = self._model.transcribe(
            audio_float32,
            language=language,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info("Transcribed: %r%s", text, f" (detected {info.language})" if language is None else "")
        return text
