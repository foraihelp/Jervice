"""A small channel for "what is Jarvis doing right now" messages from deep inside
a request (waiting out a rate limit, and so on) to the window's status line.
The brain doesn't know about the UI; main.py connects the two."""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("jarvis.brain")

_listener: Optional[Callable[[str], None]] = None


def set_listener(listener: Optional[Callable[[str], None]]) -> None:
    global _listener
    _listener = listener


def notify(message: str) -> None:
    logger.info(message)
    if _listener is not None:
        try:
            _listener(message)
        except Exception:  # noqa: BLE001 - a broken status display must never break a request
            logger.debug("Progress listener failed", exc_info=True)
