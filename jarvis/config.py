"""Loads config.yaml and environment variables (.env) into one place."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    # Running as a PyInstaller-built .exe. The exe's own folder (INSTALL_DIR)
    # ships a read-only template config.yaml, but is NOT assumed writable --
    # an installer (e.g. installer/jarvis.iss) may have placed it under
    # Program Files, which a normal (non-elevated) run cannot write to.
    # config.yaml/.env/data/ therefore live under the current user's
    # per-user AppData instead, which is always writable regardless of
    # where the app itself was installed. See load_config() below, which
    # seeds this location's config.yaml from the bundled template on first run.
    INSTALL_DIR = Path(sys.executable).resolve().parent
    PROJECT_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Jarvis"
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
else:
    INSTALL_DIR = Path(__file__).resolve().parent.parent
    PROJECT_ROOT = INSTALL_DIR


@dataclass
class Config:
    raw: dict[str, Any]
    anthropic_api_key: str
    openai_api_key: str
    api_token: str

    @property
    def wake_word_model(self) -> str:
        return self.raw["wake_word"]["model"]

    @property
    def wake_word_threshold(self) -> float:
        return float(self.raw["wake_word"]["threshold"])

    @property
    def silence_seconds(self) -> float:
        return float(self.raw["recording"]["silence_seconds"])

    @property
    def max_record_seconds(self) -> float:
        return float(self.raw["recording"]["max_seconds"])

    @property
    def sample_rate(self) -> int:
        return int(self.raw["recording"]["sample_rate"])

    @property
    def silence_rms_threshold(self) -> float:
        return float(self.raw["recording"].get("silence_rms_threshold", 400))

    @property
    def stt_model_size(self) -> str:
        return self.raw["stt"]["model_size"]

    @property
    def stt_device(self) -> str:
        return self.raw["stt"]["device"]

    @property
    def stt_compute_type(self) -> str:
        return self.raw["stt"]["compute_type"]

    @property
    def tts_rate(self) -> int:
        return int(self.raw["tts"]["rate"])

    @property
    def tts_volume(self) -> float:
        return float(self.raw["tts"]["volume"])

    @property
    def tts_voice_id(self) -> str:
        return self.raw["tts"].get("voice_id", "") or ""

    @property
    def tts_engine(self) -> str:
        """"auto" (Windows voices, plus Microsoft's online neural voices for
        Hindi/Bengali replies), "sapi" (Windows voices only, fully offline),
        or "edge" (online neural voices for everything)."""
        value = (self.raw["tts"].get("engine", "auto") or "auto").strip().lower()
        return value if value in ("auto", "sapi", "edge") else "auto"

    @property
    def input_device(self) -> str:
        """Substring of the microphone's name to record from; empty = system
        default. See jarvis/audio/devices.py."""
        return (self.raw["recording"].get("input_device", "") or "").strip()

    @property
    def tts_output_device(self) -> str:
        """Substring to match against a SAPI5 audio output device name (see
        jarvis/audio/tts.py's Speaker). Empty = system default."""
        return self.raw["tts"].get("output_device", "") or ""

    @property
    def brain_provider(self) -> str:
        """'anthropic' (Claude) or 'openai' (OpenAI, or any OpenAI-compatible
        endpoint -- see brain_base_url). Defaults to 'anthropic' so existing
        config.yaml files without this key keep working unchanged."""
        return (self.raw["brain"].get("provider") or "anthropic").strip().lower()

    @property
    def brain_base_url(self) -> str:
        """Only used when brain_provider == 'openai'. Blank means the real
        OpenAI API; set to another OpenAI-compatible endpoint's URL (Groq,
        Together, OpenRouter, DeepSeek, Azure OpenAI, a local Ollama/LM
        Studio server, ...) to use that instead."""
        return (self.raw["brain"].get("base_url") or "").strip()

    @property
    def brain_api_key(self) -> str:
        """The API key for whichever provider is currently selected."""
        return self.openai_api_key if self.brain_provider == "openai" else self.anthropic_api_key

    @property
    def brain_model(self) -> str:
        return self.raw["brain"]["model"]

    @property
    def brain_max_tokens(self) -> int:
        return int(self.raw["brain"]["max_tokens"])

    @property
    def system_prompt(self) -> str:
        return self.raw["brain"]["system_prompt"].strip()

    @property
    def reply_language(self) -> str:
        """"" (reply in whatever language the user spoke/typed in -- the
        default), or "english" / "hindi" / "bengali" to always reply in
        that language regardless of input language."""
        return (self.raw["brain"].get("reply_language", "") or "").strip().lower()

    @property
    def stt_language(self) -> str:
        """Language the user speaks: "en", "hi", "bn", or "auto"."""
        value = (self.raw["stt"].get("language", "en") or "en").strip().lower()
        return value if value in ("en", "hi", "bn", "auto") else "en"

    @property
    def stt_engine(self) -> str:
        """"local" (on this PC, private) or "cloud" (the AI provider's hosted Whisper)."""
        value = (self.raw["stt"].get("engine", "local") or "local").strip().lower()
        return value if value in ("local", "cloud") else "local"

    @property
    def stt_cloud_model(self) -> str:
        override = (self.raw["stt"].get("cloud_model", "") or "").strip()
        if override:
            return override
        return "whisper-large-v3-turbo" if "groq" in self.brain_base_url.lower() else "whisper-1"

    @property
    def follow_up_seconds(self) -> float:
        """After answering a wake-word command, how long Jarvis keeps listening
        for a follow-up without the wake word. 0 turns it off."""
        value = (self.raw.get("conversation") or {}).get("follow_up_seconds", 6)
        return max(0.0, min(30.0, float(value)))

    @property
    def confirm_risky(self) -> bool:
        return bool((self.raw.get("safety") or {}).get("confirm_risky", True))

    @property
    def effective_system_prompt(self) -> str:
        """system_prompt, with a language instruction appended when
        reply_language pins a specific output language. This is the
        "translation" mechanism -- the model is simply asked to answer
        directly in that language, rather than answering in English and
        running a separate translation pass, which would double the
        latency and add a second point of failure for no real benefit."""
        prompt = self.system_prompt + _CAPABILITY_NOTES
        if self.reply_language:
            prompt += (
                f"\n\nAlways reply in {self.reply_language.capitalize()}, regardless of what "
                "language the user speaks or types in. Do not mix languages or add an English "
                "translation alongside it -- reply only in that language."
            )
        return prompt

    @property
    def history_turns(self) -> int:
        return int(self.raw["memory"]["history_turns"])

    @property
    def memory_file(self) -> Path:
        return PROJECT_ROOT / self.raw["memory"]["file"]

    @property
    def server_enabled(self) -> bool:
        return bool(self.raw.get("server", {}).get("enabled", False))

    @property
    def server_host(self) -> str:
        return self.raw.get("server", {}).get("host", "0.0.0.0")

    @property
    def server_port(self) -> int:
        return int(self.raw.get("server", {}).get("port", 8731))

    @property
    def log_level(self) -> str:
        return self.raw["logging"]["level"]

    @property
    def log_file(self) -> Path:
        return PROJECT_ROOT / self.raw["logging"]["file"]

    @property
    def has_api_key(self) -> bool:
        return bool(self.brain_api_key)


# Appended in code (rather than living only in config.yaml) so it also reaches
# installs whose per-user config.yaml was copied from an older template.
_CAPABILITY_NOTES = (
    "\n\nTimers and reminders: use set_timer for durations ('in 10 minutes') and set_reminder for a "
    "specific clock time, working the time out from the current date and time given below. They are "
    "spoken aloud when due. Memory: when the user asks you to remember something about themselves, "
    "call remember; use remembered facts naturally without reciting them; call forget if asked. "
    "Safety: closing apps or windows and locking the PC first return 'CONFIRMATION REQUIRED' -- when "
    "that happens, ask the user one short yes/no question and call the tool again only after they say yes."
)

_PLACEHOLDER_API_KEY = "sk-ant-your-key-here"
_PLACEHOLDER_API_TOKEN = "change-me-to-a-long-random-string"


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def load_config() -> Config:
    env_path = _env_path()
    if not env_path.exists():
        # First run on a fresh install/machine: create an empty .env instead
        # of requiring the user to manually copy .env.example before the app
        # will even start. The API key is filled in from the Settings window
        # (see save_settings() below) once the app is running.
        env_path.write_text("", encoding="utf-8")
    load_dotenv(env_path)

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        bundled_template = INSTALL_DIR / "config.yaml"
        if bundled_template != config_path and bundled_template.exists():
            # Frozen build's first run: seed the per-user writable copy from
            # the read-only template that shipped next to the .exe.
            import shutil

            shutil.copy(bundled_template, config_path)
        else:
            raise FileNotFoundError(
                f"Missing config.yaml at {config_path}. Copy config.yaml (it "
                "ships with the project) or restore it from source."
            )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key == _PLACEHOLDER_API_KEY:
        api_key = ""
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    # No API key yet is not fatal: the app still starts, with the main
    # window prompting the user to add one in Settings (see main.py). This
    # is what lets a freshly installed .exe run on a machine that has never
    # been configured, instead of crashing before any window can appear.
    # Both providers' keys are kept in .env at once (not just the active
    # one) so switching brain.provider back and forth in Settings doesn't
    # forget whichever key isn't currently selected.

    api_token = os.environ.get("JARVIS_API_TOKEN", "").strip()
    server_enabled = bool(raw.get("server", {}).get("enabled", False))
    if server_enabled and (not api_token or api_token == _PLACEHOLDER_API_TOKEN):
        # Unlike the Claude API key, this token doesn't need to come from the
        # user -- it's just a shared secret between this PC and the iOS app.
        # Generate one automatically so a fresh install works out of the box
        # without requiring any manual .env editing.
        import secrets

        api_token = secrets.token_urlsafe(32)
        from dotenv import set_key

        set_key(str(env_path), "JARVIS_API_TOKEN", api_token)

    return Config(raw=raw, anthropic_api_key=api_key, openai_api_key=openai_api_key, api_token=api_token)


def save_settings(payload: dict[str, Any]) -> None:
    """Applies a settings payload from the settings window to config.yaml
    and .env. Uses ruamel.yaml (not PyYAML) for the config.yaml read-modify-
    write cycle specifically so the file's comments and formatting survive
    a GUI save instead of being stripped.

    Recognized payload keys (all optional): wake_word_threshold (float),
    tts_rate (int), tts_volume (float 0-1), tts_output_device (str,
    substring of a SAPI5 device name, blank = system default),
    reply_language (str, "" / "english" / "hindi" / "bengali"),
    stt_language ("en"/"hi"/"bn"/"auto"), stt_engine ("local"/"cloud"),
    follow_up_seconds (number, 0 = off), confirm_risky (bool),
    tts_engine ("auto" / "sapi" / "edge"), input_device (str, microphone
    name substring, blank = system default),
    server_enabled (bool),
    server_port (int), api_token (str, written to .env not config.yaml),
    provider ("anthropic" or "openai"), model (str), base_url (str, only
    meaningful for "openai"), api_key (str, written to .env not
    config.yaml -- under ANTHROPIC_API_KEY or OPENAI_API_KEY depending on
    `provider`, so switching providers doesn't overwrite the other one's key).
    """
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True

    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml_rt.load(f)

    if "wake_word_threshold" in payload:
        data["wake_word"]["threshold"] = float(payload["wake_word_threshold"])
    if "tts_rate" in payload:
        data["tts"]["rate"] = int(payload["tts_rate"])
    if "tts_volume" in payload:
        data["tts"]["volume"] = float(payload["tts_volume"])
    if "tts_output_device" in payload:
        data["tts"]["output_device"] = (payload["tts_output_device"] or "").strip()
    if "input_device" in payload:
        data.setdefault("recording", {})["input_device"] = (payload["input_device"] or "").strip()
    if payload.get("tts_engine") in ("auto", "sapi", "edge"):
        data["tts"]["engine"] = payload["tts_engine"]
    if payload.get("stt_language") in ("en", "hi", "bn", "auto"):
        data["stt"]["language"] = payload["stt_language"]
    if payload.get("stt_engine") in ("local", "cloud"):
        data["stt"]["engine"] = payload["stt_engine"]
    if "follow_up_seconds" in payload:
        data.setdefault("conversation", {})["follow_up_seconds"] = max(0, min(30, float(payload["follow_up_seconds"])))
    if "confirm_risky" in payload:
        data.setdefault("safety", {})["confirm_risky"] = bool(payload["confirm_risky"])
    if "reply_language" in payload:
        data.setdefault("brain", {})["reply_language"] = (payload["reply_language"] or "").strip().lower()
    if "server_enabled" in payload:
        data.setdefault("server", {})["enabled"] = bool(payload["server_enabled"])
    if "server_port" in payload:
        data.setdefault("server", {})["port"] = int(payload["server_port"])
    provider = payload.get("provider", "").strip().lower() if payload.get("provider") else ""
    if provider in ("anthropic", "openai"):
        data.setdefault("brain", {})["provider"] = provider
    if payload.get("model"):
        data.setdefault("brain", {})["model"] = payload["model"].strip()
    if "base_url" in payload:
        data.setdefault("brain", {})["base_url"] = (payload["base_url"] or "").strip()

    with open(config_path, "w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)

    api_token = payload.get("api_token", "").strip() if payload.get("api_token") else ""
    api_key = payload.get("api_key", "").strip() if payload.get("api_key") else ""
    if api_token or api_key:
        from dotenv import set_key

        env_path = _env_path()
        if not env_path.exists():
            env_path.write_text("", encoding="utf-8")
        if api_token:
            set_key(str(env_path), "JARVIS_API_TOKEN", api_token)
        if api_key:
            # Written under whichever provider is selected *after* this
            # save (falls back to the config file's current provider if the
            # payload didn't change it), so it lands in the right variable.
            active_provider = provider or (data.get("brain", {}).get("provider") or "anthropic")
            env_var = "OPENAI_API_KEY" if active_provider == "openai" else "ANTHROPIC_API_KEY"
            set_key(str(env_path), env_var, api_key)
