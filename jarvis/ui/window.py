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

    def __init__(self, brain_holder, transcriber, config, tray):
        self.brain_holder = brain_holder
        self.transcriber = transcriber
        self.config = config
        self.tray = tray
        self.window = None  # set by create_main_window() right after the window exists

    def get_state(self) -> dict[str, Any]:
        from jarvis import __version__
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
            "version": __version__,
        }

    def check_for_updates(self) -> None:
        from jarvis.updater import check_for_updates

        check_for_updates(lambda status: push_update_status(self.window, status))

    def get_update_status(self) -> dict[str, Any]:
        from jarvis.updater import get_latest_status

        return get_latest_status()

    def install_update(self) -> None:
        from jarvis.updater import install_and_restart

        install_and_restart()

    def send_command(self, text: str) -> dict[str, Any]:
        from jarvis.tools import registry

        before = registry.call_count()
        reply = self.brain_holder.brain.respond(text)
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
        reply = self.brain_holder.brain.respond(text)
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
        open_settings_window(self.config, self.brain_holder)

    def quit(self) -> None:
        logger.info("Quit requested from main window.")
        os._exit(0)


class SettingsAPI:
    """Exposed to settings.html as `window.pywebview.api`."""

    # save_settings() payload keys that require rebuilding the live brain
    # (and its memory, since Anthropic/OpenAI message formats aren't
    # cross-compatible -- see Memory's provider tag) to take effect
    # immediately instead of needing a full restart.
    _BRAIN_AFFECTING_KEYS = {"provider", "model", "base_url", "api_key"}

    def __init__(self, config, brain_holder):
        self.config = config
        self.brain_holder = brain_holder
        self.window = None  # set by open_settings_window() right after the window exists

    def get_settings(self) -> dict[str, Any]:
        return {
            "wake_word_threshold": self.config.wake_word_threshold,
            "tts_rate": self.config.tts_rate,
            "tts_volume": self.config.tts_volume,
            "server_enabled": self.config.server_enabled,
            "server_port": self.config.server_port,
            "api_token": self.config.api_token,
            "provider": self.config.brain_provider,
            "model": self.config.brain_model,
            "base_url": self.config.brain_base_url,
            # Both are sent (not just the active one) so the Settings window
            # can show the right already-saved key immediately when the user
            # switches providers, before saving.
            "anthropic_api_key": self.config.anthropic_api_key,
            "openai_api_key": self.config.openai_api_key,
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        from jarvis.config import load_config, save_settings

        save_settings(payload)
        self.config = load_config()

        applied_live = False
        if self._BRAIN_AFFECTING_KEYS & payload.keys():
            try:
                from jarvis.brain import create_brain
                from jarvis.brain.memory import Memory

                memory = Memory(self.config.memory_file, self.config.history_turns, provider=self.config.brain_provider)
                self.brain_holder.brain = create_brain(self.config, memory)
                applied_live = True
            except Exception:
                logger.exception("Could not hot-swap the brain after a Settings save; restart Jarvis to apply it.")

        return {"ok": True, "applied_live": applied_live}

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()


def create_main_window(brain_holder, transcriber, config, tray):
    """Creates and returns the main pywebview window. Must be called
    before webview.start()."""
    import webview

    api = JarvisAPI(brain_holder=brain_holder, transcriber=transcriber, config=config, tray=tray)
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


def open_settings_window(config, brain_holder) -> None:
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

    api = SettingsAPI(config, brain_holder)
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


def push_message(window, role: str, text: str, tools: Optional[list[str]] = None) -> bool:
    """Appends a message to the main window's transcript from any thread
    (e.g. the wake-word background loop). Safe to call even if the window
    isn't ready yet or was already closed -- failures are swallowed, and
    False is returned so a caller with something important to say (e.g.
    "the microphone doesn't work") can retry until the window is ready
    rather than losing the message."""
    if window is None:
        return False
    try:
        window.evaluate_js(
            f"appendMessage({json.dumps(role)}, {json.dumps(text)}, {json.dumps(tools or [])})"
        )
        return True
    except Exception:
        logger.debug("push_message failed (window not ready?)", exc_info=True)
        return False


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


def push_update_status(window, status: dict[str, Any]) -> None:
    """Pushes an update-checker status (see jarvis/updater.py) to the main
    window's update pill. Safe to call from the updater's background
    thread, same as push_message/push_status above."""
    if window is None:
        return
    try:
        window.evaluate_js(f"setUpdateStatus({json.dumps(status)})")
    except Exception:
        logger.debug("push_update_status failed (window not ready?)", exc_info=True)
