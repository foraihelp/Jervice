"""Local speech-to-text via faster-whisper (CTranslate2-accelerated Whisper).

Runs fully offline on CPU (or GPU if configured). No audio is sent anywhere.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("jarvis.stt")


class Transcriber:
    def __init__(self, model_size: str, device: str, compute_type: str):
        # Imported lazily so importing this module doesn't force a model
        # load (useful for tests / tooling that only touch other modules).
        from faster_whisper import WhisperModel

        logger.info("Loading Whisper model '%s' (device=%s, compute_type=%s)...",
                     model_size, device, compute_type)
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio_int16: np.ndarray, sample_rate: int) -> str:
        """Transcribes int16 PCM mono audio at `sample_rate` and returns text."""
        if audio_int16.size == 0:
            return ""

        # faster-whisper expects float32 PCM in [-1, 1].
        audio_float32 = (audio_int16.astype(np.float32) / 32768.0)

        segments, _info = self._model.transcribe(
            audio_float32,
            language="en",
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info("Transcribed: %r", text)
        return text
