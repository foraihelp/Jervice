"""Shared audio error types."""

from __future__ import annotations

MIC_HELP_TEXT = (
    "Could not access the microphone. In Windows, go to Settings -> "
    "Privacy & security -> Microphone, and make sure both 'Microphone "
    "access' and 'Let desktop apps access your microphone' are turned On "
    "-- then restart Jarvis. (This also covers the mic being unplugged, "
    "disabled in Device Manager, or already in exclusive use by another app.)"
)


class MicrophoneError(Exception):
    """Raised when the microphone can't be opened at all (permission
    denied, no device, device busy) -- as opposed to a transcription or
    recording-logic failure. Carries a message a user can actually act on
    instead of PortAudio's native error text (e.g. "PaErrorCode -9999")."""
