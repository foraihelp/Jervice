"""The desktop UI: a native window (via pywebview, backed by Windows'
built-in WebView2 runtime) showing jarvis/ui/assets/main.html, wired to
live data through a small Python<->JS bridge (js_api classes below).

This is the actual working app window -- not a mockup -- built by loading
the same HTML/CSS design directly rather than reimplementing it as native
widgets, so it matches the design pixel-for-pixel while still being wired
up to the real brain, tools, and config.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.ui")

if getattr(sys, "frozen", False):
    # Under PyInstaller, __file__ for a bundled module does not reliably
    # point to a real file on disk (source is packed into an archive), so
    # asset paths must be resolved relative to the extraction root instead.
    # build_exe.bat's --add-data places these files at "jarvis/ui/assets"
    # relative to that root, matching the path built here.
    _BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    ASSETS_DIR = _BUNDLE_ROOT / "jarvis" / "ui" / "assets"
else:
    ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_settings_window = None  # module-level singleton so "open settings" twice re-focuses instead of duplicating


class JarvisAPI:
    """Exposed to main.html as `window.pywebview.api`."""

    def __init__(self, brain, transcriber, config, tray):
        self.brain = brain
        self.transcriber = transcriber
        self.config = config
        self.tray = tray
        self.window = None  # set by create_main_window() right after the window exists

    def get_state(self) -> dict[str, Any]:
        from jarvis.tools import registry

        connections = ["This PC"]
        if self.config.server_enabled:
            connections.append(f"Remote API · enabled, port {self.config.server_port}")
        else:
            connections.append("Remote API · disabled")

        return {
            "messages": [],  # the visible transcript starts fresh each launch; the brain's own memory persists regardless (data/memory.json)
            "model": self.config.brain_model,
            "connections": connections,
            "recent_tools": registry.get_recent_calls(6),
            "muted": self.tray.muted.is_set() if self.tray else False,
        }

    def send_command(self, text: str) -> dict[str, Any]:
        from jarvis.tools import registry

        before = registry.call_count()
        reply = self.brain.respond(text)
        after = registry.call_count()
        tools = registry.get_recent_calls(after - before) if after > before else []
        return {"reply": reply, "tools": tools}

    def trigger_listen(self) -> dict[str, Any]:
        """Manually triggered by clicking the orb -- records and responds
        to one command immediately, skipping the wake word."""
        from jarvis.audio.recorder import record_command
        from jarvis.tools import registry

        audio = record_command(
            sample_rate=self.config.sample_rate,
            silence_seconds=self.config.silence_seconds,
            max_seconds=self.config.max_record_seconds,
            silence_rms_threshold=self.config.silence_rms_threshold,
        )
        text = self.transcriber.transcribe(audio, self.config.sample_rate)
        if not text:
            return {"heard": "", "reply": "", "tools": []}

        before = registry.call_count()
        reply = self.brain.respond(text)
        after = registry.call_count()
        tools = registry.get_recent_calls(after - before) if after > before else []
        return {"heard": text, "reply": reply, "tools": tools}

    def toggle_mute(self) -> bool:
        if self.tray is None:
            return False
        if self.tray.muted.is_set():
            self.tray.muted.clear()
        else:
            self.tray.muted.set()
        return self.tray.muted.is_set()

    def minimize(self) -> None:
        if self.window is not None:
            self.window.minimize()

    def hide_to_tray(self) -> None:
        if self.window is not None:
            self.window.hide()

    def open_settings(self) -> None:
        open_settings_window(self.config)

    def quit(self) -> None:
        logger.info("Quit requested from main window.")
        os._exit(0)


class SettingsAPI:
    """Exposed to settings.html as `window.pywebview.api`."""

    def __init__(self, config):
        self.config = config
        self.window = None  # set by open_settings_window() right after the window exists

    def get_settings(self) -> dict[str, Any]:
        return {
            "wake_word_threshold": self.config.wake_word_threshold,
            "tts_rate": self.config.tts_rate,
            "tts_volume": self.config.tts_volume,
            "server_enabled": self.config.server_enabled,
            "server_port": self.config.server_port,
            "api_token": self.config.api_token,
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        from jarvis.config import save_settings

        save_settings(payload)
        return {"ok": True}

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()


def create_main_window(brain, transcriber, config, tray):
    """Creates and returns the main pywebview window. Must be called
    before webview.start()."""
    import webview

    api = JarvisAPI(brain=brain, transcriber=transcriber, config=config, tray=tray)
    window = webview.create_window(
        "Jarvis",
        str(ASSETS_DIR / "main.html"),
        js_api=api,
        width=1180,
        height=760,
        frameless=True,
        easy_drag=False,
        resizable=True,
        min_size=(860, 560),
    )
    api.window = window

    def _on_closing():
        # Alt+F4 / taskbar-close hides to tray instead of quitting outright --
        # quitting is only ever intentional, via the tray menu or the
        # in-window power icon (JarvisAPI.quit).
        window.hide()
        return False

    try:
        window.events.closing += _on_closing
    except Exception:
        logger.debug("Could not attach window close handler (pywebview version mismatch?)", exc_info=True)

    return window


def open_settings_window(config) -> None:
    """Opens the settings window, or re-focuses it if already open."""
    global _settings_window

    if _settings_window is not None:
        try:
            _settings_window.restore()
            _settings_window.show()
            return
        except Exception:
            _settings_window = None  # window was destroyed; fall through and recreate

    import webview

    api = SettingsAPI(config)
    window = webview.create_window(
        "Jarvis Settings",
        str(ASSETS_DIR / "settings.html"),
        js_api=api,
        width=640,
        height=760,
        frameless=True,
        easy_drag=False,
        resizable=True,
    )
    api.window = window

    def _on_closed():
        global _settings_window
        _settings_window = None

    try:
        window.events.closed += _on_closed
    except Exception:
        logger.debug("Could not attach settings-window closed handler", exc_info=True)

    _settings_window = window


def push_message(window, role: str, text: str, tools: Optional[list[str]] = None) -> None:
    """Appends a message to the main window's transcript from any thread
    (e.g. the wake-word background loop). Safe to call even if the window
    isn't ready yet or was already closed -- failures are swallowed."""
    if window is None:
        return
    try:
        window.evaluate_js(
            f"appendMessage({json.dumps(role)}, {json.dumps(text)}, {json.dumps(tools or [])})"
        )
    except Exception:
        logger.debug("push_message failed (window not ready?)", exc_info=True)


def push_status(window, **fields: Any) -> None:
    """Updates the main window's status readouts from any thread. `fields`
    matches the shape JS's setStatus() expects, e.g. push_status(window,
    listening=True, statusLine='LISTENING...')."""
    if window is None:
        return
    try:
        window.evaluate_js(f"setStatus({json.dumps(fields)})")
    except Exception:
        logger.debug("push_status failed (window not ready?)", exc_info=True)
