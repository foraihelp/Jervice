"""Entry point: shows the main window, wires together wake word -> record
-> transcribe -> Claude (with tools) -> speak, and runs a tray icon for
minimize/restore/quit.

Run with:  python main.py
Stop with: click the power icon in the window, or right-click the tray
icon -> Quit Jarvis (or Ctrl+C in console).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from jarvis.audio.errors import MicrophoneError
from jarvis.audio.recorder import record_command
from jarvis.audio.stt import Transcriber
from jarvis.audio.tts import Speaker
from jarvis.audio.wake_word import WakeWordListener
from jarvis.brain import BrainHolder, create_brain
from jarvis.brain.memory import Memory
from jarvis.config import load_config
from jarvis.server import run_server
from jarvis.tools import registry
from jarvis.tray import TrayApp
from jarvis.ui.window import (
    create_main_window,
    open_settings_window,
    push_message,
    push_status,
    push_update_status,
)

logger = logging.getLogger("jarvis.main")


class _PywebviewComNoiseFilter(logging.Filter):
    """Drops a specific, confirmed-harmless class of pywebview log spam:
    WebView2's COM bridge throwing when pywebview's `window.native.*`
    property walk touches something that "can only be accessed from the UI
    thread", plus a pywebview bug where failing to read
    `AccessibilityObject` recurses into a message hundreds of ".Empty"
    segments long. Neither reflects a real app problem (confirmed via the
    live jarvis.log: they arrive in bursts of 5-15 at once, always followed
    by the app continuing to work normally), but formatting/writing those
    giant stack traces -- especially the recursive one -- is real, wasted
    CPU and disk I/O landing at the same moment a user is clicking around,
    which is exactly when "laggy" gets noticed. Anything else logged by
    pywebview still comes through untouched."""

    _NOISY_SNIPPETS = ("CoreWebView2 can only be accessed", "CoreWebView2Controller members can only be accessed", "AccessibilityObject")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "pywebview" and any(s in record.getMessage() for s in self._NOISY_SNIPPETS):
            return False
        return True


def setup_logging(level: str, log_file) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logging.getLogger("pywebview").addFilter(_PywebviewComNoiseFilter())


# Arbitrary fixed GUID, not tied to anything else -- just needs to be
# unique to Jarvis so this doesn't collide with some other app's mutex.
_SINGLE_INSTANCE_MUTEX_NAME = "Jarvis-SingleInstance-8F1B7C1E-9C2E-4C1A-9B7F-3E5D6A2F1D40"


def _acquire_single_instance_lock() -> bool:
    """Returns True if this is the only running Jarvis instance (and keeps
    it that way by holding a named mutex handle for the rest of the
    process's life -- the OS releases it automatically on exit, even a
    crash, so there's no cleanup to get wrong). Returns False if another
    instance already holds it.

    This matters because two instances each run their own continuous
    wake-word listener holding the microphone open, and each try to bind
    the same local API server port -- a second instance doesn't run
    alongside the first, it actively breaks it (confirmed in the wild:
    launching a second instance while one was already running produced
    "Device unavailable"/"Invalid device" PortAudio errors in BOTH,
    despite each one's own mic handling being individually correct)."""
    if os.name != "nt":
        return True  # only Windows is supported anyway; don't block other platforms on this
    import win32api
    import win32event
    import winerror

    global _single_instance_mutex_handle
    _single_instance_mutex_handle = win32event.CreateMutex(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


_single_instance_mutex_handle = None  # kept alive for the process's lifetime; see _acquire_single_instance_lock


def main() -> None:
    if os.name != "nt":
        print(
            "Warning: this build targets Windows (uses os.startfile, SAPI5 "
            "TTS, and Windows-specific window/volume control). Some tools "
            "will not work on this OS."
        )

    if not _acquire_single_instance_lock():
        logger.warning("Another Jarvis instance is already running; exiting.")
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "Jarvis is already running. Check your system tray (it may be "
            "hidden, not closed) instead of launching it again.",
            "Jarvis",
            0x40,  # MB_ICONINFORMATION
        )
        return

    config = load_config()
    setup_logging(config.log_level, config.log_file)
    logger.info("Starting Jarvis...")

    memory = Memory(config.memory_file, config.history_turns, provider=config.brain_provider)
    # Boxed so Settings can hot-swap the active brain (provider/model/key
    # change) in place, without restarting -- see SettingsAPI.save_settings.
    brain_holder = BrainHolder(create_brain(config, memory))
    speaker = Speaker(
        rate=config.tts_rate,
        volume=config.tts_volume,
        voice_id=config.tts_voice_id,
        output_device=config.tts_output_device,
        language=config.reply_language,
    )
    transcriber = Transcriber(
        model_size=config.stt_model_size,
        device=config.stt_device,
        compute_type=config.stt_compute_type,
    )

    # window_holder lets the tray's "Show Jarvis" callback reach the window
    # object even though the window is created after the tray (TrayApp
    # needs to exist first so the window's JarvisAPI can reference
    # tray.muted) -- the closure below reads it at call time, not definition
    # time, so the late assignment further down is fine.
    window_holder: dict[str, object] = {"window": None}

    def show_window() -> None:
        window = window_holder["window"]
        if window is None:
            return
        try:
            window.restore()
        except Exception:
            pass
        window.show()

    tray = TrayApp(on_quit=lambda: os._exit(0), on_show=show_window)

    # Created here (not inside wake_word_thread() below) so trigger_listen()
    # (the orb click, in jarvis/ui/window.py) can also pause/resume it --
    # both that path and the wake-word-triggered one record a command via
    # the same microphone, and only one of them can have it open at a time.
    wake_word_listener = WakeWordListener(config.wake_word_model, config.wake_word_threshold)

    window = create_main_window(brain_holder, transcriber, config, tray, wake_word_listener, speaker)
    window_holder["window"] = window

    if not config.has_api_key:
        # First run on a fresh install/machine: no crash, no manual .env
        # editing required -- just point the user at Settings once the
        # window has actually loaded (pushing to it any earlier would be a
        # no-op, since the page's JS isn't running yet).
        def _prompt_for_api_key() -> None:
            provider_name = "Claude" if config.brain_provider == "anthropic" else "OpenAI (or compatible)"
            push_message(
                window, "jarvis",
                f"Welcome! Add your {provider_name} API key in Settings (gear "
                "icon, bottom of the left rail) to get started -- you can "
                "also switch AI providers there.",
            )
            open_settings_window(config, brain_holder, wake_word_listener=wake_word_listener, speaker=speaker)

        try:
            window.events.loaded += _prompt_for_api_key
        except Exception:
            logger.debug("Could not attach loaded handler for first-run API key prompt", exc_info=True)

    # Silent check shortly after launch, same delay as the Video Converter
    # app's electron-updater setup -- failures just leave the update pill
    # showing "Check for Updates" rather than being surfaced as errors.
    def _auto_check_for_updates() -> None:
        from jarvis.updater import check_for_updates

        check_for_updates(lambda status: push_update_status(window, status))

    threading.Timer(3.0, _auto_check_for_updates).start()

    def handle_wake() -> None:
        if tray.muted.is_set():
            logger.debug("Wake word detected but microphone is muted; ignoring.")
            return

        push_status(window, listening=True, statusLine="LISTENING...")
        try:
            audio = record_command(
                sample_rate=config.sample_rate,
                silence_seconds=config.silence_seconds,
                max_seconds=config.max_record_seconds,
                silence_rms_threshold=config.silence_rms_threshold,
            )
        except MicrophoneError as exc:
            # Not caught, this would kill the wake-word background thread
            # entirely (silently, from the user's perspective -- "Hey
            # Jarvis" would just stop doing anything, forever, with no
            # indication why) since this runs inside WakeWordListener's
            # on_wake callback with nothing else to catch it.
            logger.warning("Microphone error while recording a command: %s", exc)
            push_message(window, "jarvis", str(exc))
            push_status(window, listening=False, statusLine="WAKE WORD · HEY JARVIS")
            return
        text = transcriber.transcribe(audio, config.sample_rate)
        if not text:
            logger.info("Heard nothing intelligible, ignoring.")
            push_status(window, listening=False, statusLine="WAKE WORD · HEY JARVIS")
            return

        push_message(window, "user", text)
        push_status(window, listening=False, statusLine="THINKING...")

        before = registry.call_count()
        try:
            reply = brain_holder.brain.respond(text)
        except Exception as exc:  # noqa: BLE001 - never let one bad turn kill the loop
            logger.exception("Brain error")
            reply = "Sorry, I hit an error handling that."
        after = registry.call_count()
        tools_used = registry.get_recent_calls(after - before) if after > before else []

        push_message(window, "jarvis", reply, tools_used)
        push_status(window, statusLine="WAKE WORD · HEY JARVIS")
        speaker.say(reply)

    def wake_word_thread() -> None:
        try:
            wake_word_listener.listen_forever(handle_wake)
        except MicrophoneError as exc:
            # Opening the mic for continuous wake-word listening failed
            # right at startup -- without this, the thread just dies and
            # "Hey Jarvis" silently never works again, with nothing in the
            # UI to explain why. The window may not have finished loading
            # yet this early, so retry the push for a few seconds rather
            # than losing the message to push_message's normal
            # fail-silently behavior (fine for routine status updates, not
            # for the one message explaining why voice input is dead).
            logger.warning("Microphone error starting wake word listener: %s", exc)
            for _ in range(20):
                if push_message(window, "jarvis", str(exc)):
                    break
                time.sleep(0.5)

    threading.Thread(target=wake_word_thread, daemon=True).start()
    # The tray icon runs in a background thread (not the main thread) on
    # this build because the main thread is needed for the webview event
    # loop below -- pystray tolerates a non-main thread fine on Windows,
    # which is the only OS this project targets.
    threading.Thread(target=tray.run, daemon=True).start()

    if config.server_enabled:
        threading.Thread(
            target=run_server,
            args=(brain_holder, config.api_token, config.server_host, config.server_port),
            daemon=True,
        ).start()
        logger.info(
            "Remote API enabled on %s:%d -- see README for connecting the iOS app.",
            config.server_host, config.server_port,
        )

    logger.info("Jarvis is running. Say the wake word to give a command.")

    import webview

    webview.start()  # blocks the main thread; required for the window's event loop


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback

        traceback.print_exc()
        # When double-clicked as a built .exe (or a .py with no console
        # attached), Windows closes the console the instant the process
        # exits -- which means a crash-on-startup flashes and vanishes
        # before you can read it. Pausing here keeps the window open so
        # the error above is actually visible. This only matters for a
        # frozen/double-clicked launch; running via `python main.py` in an
        # already-open terminal doesn't need it (the terminal stays open
        # regardless), but the pause is harmless there too.
        if getattr(sys, "frozen", False) or sys.stdout.isatty() is False:
            input("\nJarvis crashed on startup (see error above). Press Enter to close this window...")
        sys.exit(1)
