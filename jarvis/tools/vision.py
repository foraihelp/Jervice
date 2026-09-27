"""Screen vision: lets Jarvis look at what's currently on screen and answer a question about
it (an error dialog, what app is open, text that's hard to read). This is a one-off call to
the currently configured AI provider with an image attached -- not a generic tool result (which
is always plain text, see jarvis/tools/registry.py), because sending an image needs a shape
neither brain's tool-result plumbing carries. Reusing jarvis.brain.fallback.failure_kind to
classify a failed call keeps this consistent with how the fallback chain explains failures.

Whatever is on screen is sent to whichever AI provider is configured in Settings (the same
place ordinary conversation already goes) -- a local Ollama server keeps it on this PC; Claude,
OpenAI, Groq and the rest send it to that provider, same as everything else said to Jarvis.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Optional

logger = logging.getLogger("jarvis.tools.vision")

_MAX_QUESTION_CHARS = 500
_MAX_SIDE_PIXELS = 1280       # keeps the request fast and reasonably cheap in tokens
_JPEG_QUALITY = 78
_TIMEOUT_SECONDS = 30
_MAX_REPLY_TOKENS = 500

_DEFAULT_QUESTION = "Describe what's on the screen, and call out anything that looks like an error, warning or dialog box."

_NOT_SUPPORTED = (
    "Either the AI model currently selected in Settings can't look at images, or this description "
    "didn't make it understand the screen. Claude and GPT-4o both support this; Settings can switch to one."
)


def _capture_jpeg_base64() -> str:
    from PIL import Image, ImageGrab

    img = ImageGrab.grab()
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((_MAX_SIDE_PIXELS, _MAX_SIDE_PIXELS), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _explain_failure(exc: Exception, model: str) -> str:
    from jarvis.brain.fallback import failure_kind

    kind = failure_kind(exc)
    if kind == "bad_key":
        return "The AI provider didn't accept my API key for this. Check it in Settings."
    if kind in ("rate_limit", "daily_limit"):
        return "The AI provider is too busy (or its quota is used up) to look at the screen right now. Try again shortly."
    if kind == "outage":
        return "I couldn't reach the AI provider to look at the screen. Check your internet connection and try again."
    if kind == "model_missing":
        return f"I couldn't reach the model '{model}' set in Settings to look at the screen."
    # Most often a plain 400: the provider accepted the request but rejected the image content,
    # which in practice means the selected model doesn't support images.
    logger.warning("Screen vision call failed for model %r: %s", model, exc)
    return f"I couldn't look at the screen. {_NOT_SUPPORTED}"


def _ask_anthropic(config, question: str, image_b64: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=config.brain_api_key, max_retries=0, timeout=_TIMEOUT_SECONDS)
    response = client.messages.create(
        model=config.brain_model,
        max_tokens=_MAX_REPLY_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": question},
            ],
        }],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _ask_openai(config, question: str, image_b64: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.brain_api_key, base_url=config.brain_base_url or None, max_retries=0, timeout=_TIMEOUT_SECONDS)
    response = client.chat.completions.create(
        model=config.brain_model,
        max_tokens=_MAX_REPLY_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ],
        }],
    )
    return (response.choices[0].message.content or "").strip()


def describe_screen(question: str = "") -> str:
    """Takes a screenshot of the current screen and answers `question` about it (or gives a
    general description if left blank), using whichever AI provider is set up in Settings."""
    from jarvis.config import load_config

    question = (question or "").strip()[:_MAX_QUESTION_CHARS] or _DEFAULT_QUESTION

    config = load_config()
    if not config.has_api_key:
        return "I don't have an AI provider set up yet to look at images -- add an API key in Settings first."

    try:
        image_b64 = _capture_jpeg_base64()
    except Exception as exc:  # noqa: BLE001 - e.g. no display attached
        logger.warning("Could not capture the screen: %s", exc)
        return f"I couldn't take a screenshot: {exc}"

    try:
        text = (_ask_anthropic if config.brain_provider == "anthropic" else _ask_openai)(config, question, image_b64)
    except Exception as exc:  # noqa: BLE001 - network/auth/unsupported-model, all turned into plain text below
        return _explain_failure(exc, config.brain_model)

    return text or f"I looked at the screen but didn't get a description back. {_NOT_SUPPORTED}"
