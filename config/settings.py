"""
Application settings for AI Klipers.

Settings are persisted as JSON in the OS-appropriate app-data folder:
  - Windows: %APPDATA%/AI Klipers/settings.json
  - macOS/Linux (dev only): ~/.ai_klipers/settings.json

Kept dependency-free (stdlib only) so it works identically before and
after PyInstaller packaging.
"""
from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, asdict
from pathlib import Path


def get_app_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "AI Klipers"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AI Klipers"
    return Path.home() / ".ai_klipers"


APP_DATA_DIR = get_app_data_dir()
SETTINGS_FILE = APP_DATA_DIR / "settings.json"
DB_FILE = APP_DATA_DIR / "ai_klipers.db"
LOGS_DIR = APP_DATA_DIR / "logs"

VALID_THEMES = ("dark", "light")
VALID_LANGUAGES = ("id", "en")
VALID_CLIP_DURATIONS = (15, 30, 45, 60, 0)  # 0 = custom
VALID_HW_ACCEL = ("auto", "nvidia", "quicksync", "amf", "cpu")
VALID_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")


@dataclass
class Settings:
    theme: str = "dark"
    language: str = "id"
    output_folder: str = str(Path.home() / "AI Klipers Output")
    temp_folder: str = str(APP_DATA_DIR / "temp")
    auto_save: bool = True
    auto_backup: bool = False
    whisper_model_size: str = "base"
    use_gpu: bool = True
    default_clip_duration: int = 30
    hw_accel: str = "auto"
    caption_backend: str = "heuristic"        # "heuristic" | "llm"
    caption_llm_provider: str = "anthropic"    # "anthropic" | "openai"
    caption_llm_api_key: str = ""              # stored locally in plaintext JSON -- see Settings UI warning
    caption_llm_model: str = ""                # blank = provider's default
    window_width: int = 1360
    window_height: int = 860

    @classmethod
    def load(cls) -> "Settings":
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                defaults = asdict(cls())
                # Ignore unknown keys, fill missing ones with defaults so
                # upgrading the app never crashes on an old settings file.
                merged = {**defaults, **{k: v for k, v in raw.items() if k in defaults}}
                return cls(**merged)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        settings = cls()
        settings.save()
        return settings

    def save(self) -> None:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.output_folder).mkdir(parents=True, exist_ok=True)
        Path(self.temp_folder).mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def as_dict(self) -> dict:
        return asdict(self)
