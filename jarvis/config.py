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
    # Running as a PyInstaller-built .exe: config.yaml/.env/data/ live next
    # to the .exe itself (NOT inside the bundled temp/_internal folder),
    # so they can be edited without rebuilding.
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict[str, Any]
    anthropic_api_key: str
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
    def brain_model(self) -> str:
        return self.raw["brain"]["model"]

    @property
    def brain_max_tokens(self) -> int:
        return int(self.raw["brain"]["max_tokens"])

    @property
    def system_prompt(self) -> str:
        return self.raw["brain"]["system_prompt"].strip()

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


def load_config() -> Config:
    load_dotenv(PROJECT_ROOT / ".env")

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Missing config.yaml at {config_path}. Copy config.yaml (it ships "
            "with the project) or restore it from source."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or api_key == "sk-ant-your-key-here":
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and put "
            "your real key in it. Get one at https://console.anthropic.com/"
        )

    api_token = os.environ.get("JARVIS_API_TOKEN", "").strip()
    server_enabled = bool(raw.get("server", {}).get("enabled", False))
    if server_enabled and (not api_token or api_token == "change-me-to-a-long-random-string"):
        raise RuntimeError(
            "server.enabled is true in config.yaml but JARVIS_API_TOKEN is not "
            "set (or still has its placeholder value) in .env. Generate one "
            "with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return Config(raw=raw, anthropic_api_key=api_key, api_token=api_token)


def save_settings(payload: dict[str, Any]) -> None:
    """Applies a settings payload from the settings window to config.yaml
    and .env. Uses ruamel.yaml (not PyYAML) for the config.yaml read-modify-
    write cycle specifically so the file's comments and formatting survive
    a GUI save instead of being stripped.

    Recognized payload keys (all optional): wake_word_threshold (float),
    tts_rate (int), tts_volume (float 0-1), server_enabled (bool),
    server_port (int), api_token (str, written to .env not config.yaml).
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
    if "server_enabled" in payload:
        data.setdefault("server", {})["enabled"] = bool(payload["server_enabled"])
    if "server_port" in payload:
        data.setdefault("server", {})["port"] = int(payload["server_port"])

    with open(config_path, "w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)

    api_token = payload.get("api_token", "").strip() if payload.get("api_token") else ""
    if api_token:
        from dotenv import set_key

        env_path = PROJECT_ROOT / ".env"
        if not env_path.exists():
            env_path.write_text("", encoding="utf-8")
        set_key(str(env_path), "JARVIS_API_TOKEN", api_token)
