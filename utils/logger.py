"""Centralized logging setup for AI Klipers.

Writes to logs/ai_klipers.log (rotating, 5 files x 2MB) and echoes to the
console. Call setup_logging() once at app startup, then use
logging.getLogger("ai_klipers.<module>") everywhere else.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return

    from config.settings import LOGS_DIR

    log_dir = log_dir or LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = RotatingFileHandler(
        log_dir / "ai_klipers.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger("ai_klipers")
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.propagate = False

    # Quiet down noisy third-party libraries a little.
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)

    _configured = True
    root.info("Logging initialized -> %s", log_dir / "ai_klipers.log")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ai_klipers.{name}")
