"""AI Klipers -- entry point.

Run with:  python main.py   (from this folder)
Build EXE: python build.py  (see build.py / README.md; must run on Windows)
"""
from __future__ import annotations

import sys
import logging

from PySide6.QtWidgets import QApplication

from utils.logger import setup_logging
from config.settings import Settings
from database.db import init_db


def main() -> int:
    setup_logging()
    logger = logging.getLogger("ai_klipers.main")
    logger.info("Starting AI Klipers...")

    settings = Settings.load()
    init_db()

    app = QApplication(sys.argv)
    app.setApplicationName("AI Klipers")
    app.setOrganizationName("AIKlipers")

    # Imported after QApplication exists so any Qt-dependent module-level
    # code (there isn't any today, but this keeps the ordering safe) runs
    # with an active application instance.
    from app.main_window import MainWindow

    window = MainWindow(settings)
    window.show()

    logger.info("Main window shown, entering event loop")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
