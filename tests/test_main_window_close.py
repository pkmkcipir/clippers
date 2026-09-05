"""Tests for app/main_window.py's closeEvent safety net: warn before
losing an actively-running Batch Export job, and otherwise close cleanly.
Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_main_window_close.py -v
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def main_window(qapp, tmp_path):
    from config.settings import Settings
    from database.db import init_db
    from app.main_window import MainWindow

    init_db(f"sqlite:///{tmp_path}/close_test.db")
    settings = Settings.load()
    settings.output_folder = str(tmp_path / "out")
    settings.temp_folder = str(tmp_path / "temp")

    window = MainWindow(settings)
    window.show()
    qapp.processEvents()
    return window


def test_closes_cleanly_with_no_active_jobs(main_window):
    event = QCloseEvent()
    main_window.closeEvent(event)
    assert event.isAccepted()


def test_warns_and_can_be_cancelled_when_batch_job_running(main_window, monkeypatch):
    from services.batch_queue import BatchJob
    batch_page = main_window.pages["batch_export"]
    batch_page.queue.jobs.append(BatchJob(kind="export_clip", label="running job", payload={}, status="running"))

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    event = QCloseEvent()
    main_window.closeEvent(event)
    assert not event.isAccepted()


def test_proceeds_when_user_confirms_despite_running_job(main_window, monkeypatch):
    from services.batch_queue import BatchJob
    batch_page = main_window.pages["batch_export"]
    batch_page.queue.jobs.append(BatchJob(kind="export_clip", label="running job", payload={}, status="running"))

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    event = QCloseEvent()
    main_window.closeEvent(event)
    assert event.isAccepted()


def test_queued_or_done_jobs_do_not_trigger_warning(main_window, monkeypatch):
    from services.batch_queue import BatchJob
    batch_page = main_window.pages["batch_export"]
    batch_page.queue.jobs.append(BatchJob(kind="export_clip", label="queued job", payload={}, status="queued"))
    batch_page.queue.jobs.append(BatchJob(kind="export_clip", label="done job", payload={}, status="done"))

    def fail_if_called(*a, **k):
        raise AssertionError("QMessageBox.question should not be called when nothing is running")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail_if_called))

    event = QCloseEvent()
    main_window.closeEvent(event)
    assert event.isAccepted()
