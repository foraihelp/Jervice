"""Local, always-on wake word detection using openWakeWord.

openWakeWord ships pretrained ONNX models (including "hey_jarvis") and runs
entirely on-device -- no audio ever leaves the machine until the wake word
fires and we start recording a command.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from jarvis.audio.errors import MIC_HELP_TEXT, MicrophoneError

logger = logging.getLogger("jarvis.wake_word")

# openWakeWord's pretrained models expect 16kHz mono audio in 80ms frames
# (1280 samples per frame).
FRAME_SAMPLES = 1280
SAMPLE_RATE = 16000


class WakeWordListener:
    """Blocks the calling thread, invoking `on_wake` each time the wake word
    is detected. Intended to be run in its own background thread.

    Also exposes pause()/resume() so something else that needs exclusive
    mic access (recording a command, whether triggered by the wake word or
    by clicking the orb) can borrow the device -- some audio drivers
    refuse to open a second InputStream on the same device while this
    listener's own stream is active, failing with PortAudioError "Device
    unavailable" rather than allowing both to coexist. listen_forever()
    already calls pause()/resume() around on_wake() automatically; a
    caller triggering a recording independently of the wake word (e.g. the
    orb click in jarvis/ui/window.py) needs to call them itself.
    """

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
        self._stream: Optional[sd.InputStream] = None
        self._callback = None
        self._lock = threading.Lock()

    def _open_stream(self) -> sd.InputStream:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            callback=self._callback,
            blocksize=FRAME_SAMPLES,
        )
        stream.start()
        return stream

    def pause(self) -> None:
        """Closes the input stream, freeing the microphone for another
        operation. Safe to call from another thread; safe to call
        multiple times (a no-op if already paused/stopped)."""
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    logger.debug("Error closing wake-word stream in pause()", exc_info=True)
                self._stream = None

    def resume(self) -> None:
        """Reopens the input stream after pause(). No-op if already open,
        already stopped for good, or if listen_forever() was never
        started (self._callback unset)."""
        with self._lock:
            if self._stream is None and not self._stop and self._callback is not None:
                try:
                    self._stream = self._open_stream()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not resume wake word listening: %s", exc)

    def stop(self) -> None:
        self._stop = True
        self.pause()

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

        self._callback = callback
        try:
            with self._lock:
                self._stream = self._open_stream()
        except Exception as exc:  # noqa: BLE001 - PortAudioError/OSError depending on the failure
            logger.warning("Could not open microphone for wake word listening: %s", exc)
            raise MicrophoneError(MIC_HELP_TEXT) from exc

        try:
            while not self._stop:
                if self._stream is None:
                    # Paused (someone else has the mic right now, e.g. the
                    # orb-click path recording a command) -- wait quietly.
                    buffer = np.zeros((0,), dtype=np.int16)
                    sd.sleep(50)
                    continue

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
                    # Free the device before on_wake() records a command --
                    # see the class docstring for why both can't stay open.
                    self.pause()
                    try:
                        on_wake()
                    finally:
                        self.resume()
        finally:
            self.pause()
