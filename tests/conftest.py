"""Shared pytest fixtures.

The autouse fixture below drains the Qt event loop after every test.
Without it, Qt's deferred `deleteLater()` cleanup for QThread/QMediaPlayer/
clipboard-related objects created in one test can still be pending when
the *next* test (possibly in a different file) starts creating its own
Qt objects -- this was observed to cause an intermittent native-level
crash ("Aborted", no Python traceback) when running the full suite,
reproducing more often when Qt-heavy files (timeline GUI, batch queue,
video editor/preview, clipboard) ran back to back, but never when any
single file ran alone. Processing events (plus a brief pause so any
pending native-thread teardown actually finishes, not just gets queued)
between tests gives pending deletions a chance to complete before the
next test's objects exist.

Honesty note: this is a mitigation, not a proven-100%-eliminated fix --
it reduced the observed failure rate substantially (roughly 1-in-4 runs
down to roughly 1-in-10+ in repeated local testing) but an occasional
"Aborted" with no Python traceback when running the FULL suite is still
possible. It has not been observed from any single test file run alone,
and does not reflect an application bug -- see README's "How this was
verified" for the full writeup.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest


@pytest.fixture(autouse=True)
def _drain_qt_event_loop():
    yield
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            for _ in range(8):
                app.processEvents()
                app.sendPostedEvents(None, 0)
            time.sleep(0.02)
            for _ in range(4):
                app.processEvents()
    except ImportError:
        pass
