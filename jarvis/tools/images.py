"""Picture generation. Uses pollinations.ai's free, keyless image API (image.pollinations.ai) --
no account or API key needed, unlike OpenAI/Stability/Gemini image generation. In exchange it is
best-effort: no uptime guarantee, and a small "pollinations.ai" mark is stamped in a corner of
each image. Good enough for "draw me a picture of..." without asking the user to go get a key.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from jarvis import storage
from jarvis.tools.files import _INVALID_FILENAME_CHARS, _free_name

logger = logging.getLogger("jarvis.tools.images")

_ENDPOINT = "https://image.pollinations.ai/prompt/"
_TIMEOUT_SECONDS = 60
_SIDE_PIXELS = 1024
_MAX_PROMPT_CHARS = 800

_CONTENT_TYPE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _name_from_prompt(prompt: str, ext: str) -> str:
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in prompt).split()
    stem = "-".join(words[:6]).lower() or "image"
    return _INVALID_FILENAME_CHARS.sub("", stem)[:60] + ext


def create_image(prompt: str, filename: str = "") -> str:
    """Generates a picture from a text description (free, via pollinations.ai -- no API key
    needed) and saves it in Jarvis's Images folder. Not for editing an existing photo or for
    charts/diagrams -- only for a new picture created from a description."""
    prompt = (prompt or "").strip()
    if not prompt:
        return "What should the picture show? Give me a description."
    if len(prompt) > _MAX_PROMPT_CHARS:
        return f"That description is too long ({len(prompt)} characters) -- try something shorter."

    url = _ENDPOINT + urllib.parse.quote(prompt) + "?" + urllib.parse.urlencode({
        "width": _SIDE_PIXELS, "height": _SIDE_PIXELS, "nologo": "true", "referrer": "jarvis-voice-assistant",
    })
    request = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Assistant (local personal use)"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return "The free image service is busy right now (too many requests). Please try again in a minute."
        logger.warning("Image generation HTTP error %s for %r", exc.code, prompt)
        return f"The image service turned down that request (HTTP {exc.code}). Try rewording it."
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Image generation failed for %r: %s", prompt, exc)
        return "I couldn't reach the free image service -- check your internet connection and try again."

    if content_type not in _CONTENT_TYPE_EXT or len(data) < 1000:
        logger.warning("Image generation returned something unusable for %r (%s, %d bytes)", prompt, content_type, len(data))
        return "The image service didn't return a usable picture for that description -- try rewording it."

    ext = _CONTENT_TYPE_EXT[content_type]
    name = _INVALID_FILENAME_CHARS.sub("", Path((filename or "").strip()).name).strip(" .")
    if name:
        name = name if Path(name).suffix.lower() in _CONTENT_TYPE_EXT.values() else name + ext
    else:
        name = _name_from_prompt(prompt, ext)

    dest = _free_name(storage.images_dir(), name)
    try:
        dest.write_bytes(data)
    except OSError as exc:
        return f"I generated the picture but couldn't save it: {exc.strerror or exc}"

    result = f"Saved {dest}"
    if dest.name != name:
        result += f" (the name {name} was already taken)"

    try:
        import os

        os.startfile(str(dest))  # type: ignore[attr-defined]  -- whatever the PC uses for pictures (usually Photos)
    except OSError as exc:
        return result + f", but I couldn't open it: {exc.strerror or exc}"
    return result + " and opened it."
