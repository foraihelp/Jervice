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
import wave
from typing import Any, Optional

import numpy as np

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


class Transcriber:
    def __init__(self, model_size: str, device: str, compute_type: str, language: str = "en"):
        # Imported lazily so importing this module doesn't force a model
        # load (useful for tests / tooling that only touch other modules).
        from faster_whisper import WhisperModel

        logger.info("Loading Whisper model '%s' (device=%s, compute_type=%s)...",
                     model_size, device, compute_type)
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        # "en" / "hi" / "bn", or "auto" to let Whisper guess. Guessing is unreliable
        # for short commands and especially for Bengali, so a fixed language is better.
        self.language = language
        self.engine = "local"
        self._cloud: Optional[CloudTranscriber] = None

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
