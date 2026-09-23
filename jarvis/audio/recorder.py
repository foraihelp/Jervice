"""Records a single spoken command after the wake word fires, stopping
automatically once the user has paused for `silence_seconds`.

Uses a simple energy-based (RMS) silence detector implemented in plain
numpy rather than a compiled VAD library (webrtcvad/webrtcvad-wheels) --
those packages frequently lack prebuilt wheels for newer Python versions
and fail to install without a C compiler. This approach has no compiled
dependencies at all, at the cost of being a bit less sophisticated than a
real VAD in very noisy rooms (see `silence_rms_threshold` in config.yaml
to tune it if needed).
"""

from __future__ import annotations

import logging

import numpy as np
import sounddevice as sd

from jarvis.audio.errors import MIC_HELP_TEXT, MicrophoneError

logger = logging.getLogger("jarvis.recorder")

FRAME_MS = 30  # size of each analysis chunk


def test_microphone() -> None:
    """Briefly opens (and immediately closes) an input stream purely to
    confirm the microphone is actually reachable -- used by Settings'
    "Test Microphone & Speaker" button so the user gets a real yes/no
    answer from the app itself instead of having to go dig through
    Windows' Privacy & security settings to guess. Raises MicrophoneError
    (with the same actionable message record_command() uses) on failure;
    returns normally on success."""
    try:
        stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1600)
        stream.start()
        stream.stop()
        stream.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Microphone test failed: %s", exc)
        raise MicrophoneError(MIC_HELP_TEXT) from exc


def record_command(
    sample_rate: int,
    silence_seconds: float,
    max_seconds: float,
    silence_rms_threshold: float = 400.0,
) -> np.ndarray:
    """Records mono int16 audio from the default microphone until the user
    stops talking (RMS-detected silence) or `max_seconds` is reached.

    A frame counts as "speech" when its RMS (root-mean-square amplitude)
    exceeds `silence_rms_threshold`. Typical int16 mic idle/room noise is
    roughly 50-300; normal speech is usually well above 1000. If Jarvis
    stops recording too early, lower the threshold in config.yaml; if it
    keeps recording through silence (picking up background noise), raise it.

    Returns the recorded audio as a 1-D numpy int16 array.
    """
    frame_samples = int(sample_rate * FRAME_MS / 1000)
    silence_frames_needed = int(silence_seconds * 1000 / FRAME_MS)
    max_frames = int(max_seconds * 1000 / FRAME_MS)

    frames: list[np.ndarray] = []
    silence_run = 0
    speech_started = False
    buffer = np.zeros((0,), dtype=np.int16)

    def callback(indata, frame_count, time_info, status):
        nonlocal buffer
        if status:
            logger.debug("Audio input status: %s", status)
        buffer = np.concatenate([buffer, indata[:, 0]])

    logger.info("Recording command...")
    try:
        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            callback=callback,
            blocksize=frame_samples,
        )
        stream.start()
    except Exception as exc:  # noqa: BLE001 - sounddevice raises PortAudioError/OSError depending on the failure
        logger.warning("Could not open microphone input stream: %s", exc)
        raise MicrophoneError(MIC_HELP_TEXT) from exc

    with stream:
        frame_count = 0
        while frame_count < max_frames:
            if len(buffer) < frame_samples:
                sd.sleep(10)
                continue

            frame, buffer = buffer[:frame_samples], buffer[frame_samples:]
            frames.append(frame)
            frame_count += 1

            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            is_speech = rms > silence_rms_threshold

            if is_speech:
                speech_started = True
                silence_run = 0
            elif speech_started:
                silence_run += 1
                if silence_run >= silence_frames_needed:
                    logger.info("Silence detected, stopping recording.")
                    break

    if not frames:
        return np.zeros((0,), dtype=np.int16)

    return np.concatenate(frames)
