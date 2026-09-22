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

from jarvis.audio.recorder import record_command
from jarvis.audio.stt import Transcriber
from jarvis.audio.tts import Speaker
from jarvis.audio.wake_word import WakeWordListener
from jarvis.brain import create_brain
from jarvis.brain.memory import Memory
from jarvis.config import load_config
from jarvis.server import run_server
from jarvis.tools import registry
from jarvis.tray import TrayApp
from jarvis.ui.window import create_main_window, open_settings_window, push_message, push_status

logger = logging.getLogger("jarvis.main")


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


def main() -> None:
    if os.name != "nt":
        print(
            "Warning: this build targets Windows (uses os.startfile, SAPI5 "
            "TTS, and Windows-specific window/volume control). Some tools "
            "will not work on this OS."
        )

    config = load_config()
    setup_logging(config.log_level, config.log_file)
    logger.info("Starting Jarvis...")

    memory = Memory(config.memory_file, config.history_turns, provider=config.brain_provider)
    brain = create_brain(config, memory)
    speaker = Speaker(rate=config.tts_rate, volume=config.tts_volume, voice_id=config.tts_voice_id)
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

    window = create_main_window(brain, transcriber, config, tray)
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
            open_settings_window(config)

        try:
            window.events.loaded += _prompt_for_api_key
        except Exception:
            logger.debug("Could not attach loaded handler for first-run API key prompt", exc_info=True)

    def handle_wake() -> None:
        if tray.muted.is_set():
            logger.debug("Wake word detected but microphone is muted; ignoring.")
            return

        push_status(window, listening=True, statusLine="LISTENING...")
        audio = record_command(
            sample_rate=config.sample_rate,
            silence_seconds=config.silence_seconds,
            max_seconds=config.max_record_seconds,
            silence_rms_threshold=config.silence_rms_threshold,
        )
        text = transcriber.transcribe(audio, config.sample_rate)
        if not text:
            logger.info("Heard nothing intelligible, ignoring.")
            push_status(window, listening=False, statusLine="WAKE WORD · HEY JARVIS")
            return

        push_message(window, "user", text)
        push_status(window, listening=False, statusLine="THINKING...")

        before = registry.call_count()
        try:
            reply = brain.respond(text)
        except Exception as exc:  # noqa: BLE001 - never let one bad turn kill the loop
            logger.exception("Brain error")
            reply = "Sorry, I hit an error handling that."
        after = registry.call_count()
        tools_used = registry.get_recent_calls(after - before) if after > before else []

        push_message(window, "jarvis", reply, tools_used)
        push_status(window, statusLine="WAKE WORD · HEY JARVIS")
        speaker.say(reply)

    def wake_word_thread() -> None:
        listener = WakeWordListener(config.wake_word_model, config.wake_word_threshold)
        listener.listen_forever(handle_wake)

    threading.Thread(target=wake_word_thread, daemon=True).start()
    # The tray icon runs in a background thread (not the main thread) on
    # this build because the main thread is needed for the webview event
    # loop below -- pystray tolerates a non-main thread fine on Windows,
    # which is the only OS this project targets.
    threading.Thread(target=tray.run, daemon=True).start()

    if config.server_enabled:
        threading.Thread(
            target=run_server,
            args=(brain, config.api_token, config.server_host, config.server_port),
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
