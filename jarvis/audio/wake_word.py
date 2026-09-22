"""Local, always-on wake word detection using openWakeWord.

openWakeWord ships pretrained ONNX models (including "hey_jarvis") and runs
entirely on-device -- no audio ever leaves the machine until the wake word
fires and we start recording a command.
"""

from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger("jarvis.wake_word")

# openWakeWord's pretrained models expect 16kHz mono audio in 80ms frames
# (1280 samples per frame).
FRAME_SAMPLES = 1280
SAMPLE_RATE = 16000


class WakeWordListener:
    """Blocks the calling thread, invoking `on_wake` each time the wake word
    is detected. Intended to be run in its own background thread."""

    def __init__(self, model_name: str, threshold: float):
        # Imported lazily so the rest of the app can be imported/tested
        # without openwakeword (and its model download) being present.
        from openwakeword.model import Model

        self.threshold = threshold
        self.model_name = model_name
        try:
            self._model = Model(wakeword_models=[model_name])
        except Exception as exc:  # pragma: no cover - depends on local models
            raise RuntimeError(
                f"Failed to load openWakeWord model '{model_name}'. Run "
                "`python -m openwakeword.utils --download` once after "
                "installing requirements to fetch pretrained models, then "
                "retry. Original error: {exc}"
            ) from exc

        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def listen_forever(self, on_wake: Callable[[], None]) -> None:
        self._stop = False
        logger.info("Wake word listener started (model=%s, threshold=%.2f)",
                     self.model_name, self.threshold)

        buffer = np.zeros((0,), dtype=np.int16)

        def callback(indata, frames, time_info, status):
            nonlocal buffer
            if status:
                logger.debug("Audio input status: %s", status)
            buffer = np.concatenate([buffer, indata[:, 0]])

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            callback=callback,
            blocksize=FRAME_SAMPLES,
        ):
            while not self._stop:
                if len(buffer) < FRAME_SAMPLES:
                    sd.sleep(20)
                    continue

                frame, buffer = buffer[:FRAME_SAMPLES], buffer[FRAME_SAMPLES:]
                predictions = self._model.predict(frame)

                score = predictions.get(self.model_name, 0.0)
                if score >= self.threshold:
                    logger.info("Wake word detected (score=%.2f)", score)
                    # Reset the model's internal buffers so the same
                    # utterance doesn't immediately re-trigger.
                    self._model.reset()
                    on_wake()
