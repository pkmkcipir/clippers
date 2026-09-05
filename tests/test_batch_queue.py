"""Tests for services/batch_queue.py -- bounded concurrency, retry, and
real end-to-end job execution (actual ffmpeg export jobs against a
synthetic clip, not mocked). Needs a Qt event loop since BatchQueueManager
is QObject-based and jobs run on QThreads.
Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_batch_queue.py -v
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

from services.batch_queue import BatchQueueManager
from export.exporter import ExportSettings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def synthetic_clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("batch_clips")
    path = d / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=20",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", str(path),
    ], check=True)
    return str(path)


def _run_until(condition, qapp, timeout_ms=20000):
    loop = QEventLoop()

    def check():
        qapp.processEvents()
        if condition():
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(50)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()


def test_jobs_run_with_bounded_concurrency(qapp, synthetic_clip, tmp_path):
    mgr = BatchQueueManager(max_concurrent=2)
    max_seen = [0]
    mgr.job_updated.connect(
        lambda _jid: max_seen.__setitem__(0, max(max_seen[0], sum(1 for j in mgr.jobs if j.status == "running")))
    )

    settings = ExportSettings(resolution="480p", fps=20, codec="h264", hw_accel="cpu", burn_subtitles=False)
    for i in range(4):
        mgr.add_export_job(
            f"job{i}", source_path=synthetic_clip, start=0.2, end=1.5, sentences=[],
            output_path=str(tmp_path / f"out_{i}.mp4"), settings=settings, temp_dir=str(tmp_path),
        )

    mgr.start()
    _run_until(lambda: mgr.counts()["done"] + mgr.counts()["error"] == 4, qapp)

    assert mgr.counts()["done"] == 4
    assert max_seen[0] <= 2
    for job in mgr.jobs:
        assert os.path.exists(job.output_path)
        assert os.path.getsize(job.output_path) > 0


def test_failed_job_can_be_retried_successfully(qapp, synthetic_clip, tmp_path):
    mgr = BatchQueueManager(max_concurrent=1)
    settings = ExportSettings(resolution="480p", fps=20, codec="h264", hw_accel="cpu", burn_subtitles=False)

    job = mgr.add_export_job(
        "will fail", source_path=str(tmp_path / "nope.mp4"), start=0, end=1, sentences=[],
        output_path=str(tmp_path / "retry_out.mp4"), settings=settings, temp_dir=str(tmp_path),
    )
    mgr.start()
    _run_until(lambda: job.status == "error", qapp)
    assert job.status == "error"
    assert job.error

    job.payload["source_path"] = synthetic_clip
    mgr.retry_job(job.id)
    _run_until(lambda: job.status in ("done", "error"), qapp)
    assert job.status == "done"
    assert os.path.exists(job.output_path)


def test_remove_queued_job_but_not_running_job(qapp, synthetic_clip, tmp_path):
    mgr = BatchQueueManager(max_concurrent=1)
    settings = ExportSettings(resolution="480p", fps=20, codec="h264", hw_accel="cpu", burn_subtitles=False)
    job1 = mgr.add_export_job("first", source_path=synthetic_clip, start=0, end=2, sentences=[],
                                output_path=str(tmp_path / "j1.mp4"), settings=settings, temp_dir=str(tmp_path))
    job2 = mgr.add_export_job("second", source_path=synthetic_clip, start=0, end=1, sentences=[],
                                output_path=str(tmp_path / "j2.mp4"), settings=settings, temp_dir=str(tmp_path))

    mgr.start()
    _run_until(lambda: job1.status == "running", qapp)

    assert mgr.remove_job(job2.id) is True   # queued -> removable
    assert mgr.remove_job(job1.id) is False  # running -> not removable
    assert job2 not in mgr.jobs

    _run_until(lambda: job1.status == "done", qapp)
    assert job1.status == "done"


def test_counts_reflects_all_statuses(qapp, synthetic_clip, tmp_path):
    mgr = BatchQueueManager(max_concurrent=2)
    settings = ExportSettings(resolution="480p", fps=20, codec="h264", hw_accel="cpu", burn_subtitles=False)
    for i in range(2):
        mgr.add_export_job(f"c{i}", source_path=synthetic_clip, start=0, end=1, sentences=[],
                             output_path=str(tmp_path / f"count_{i}.mp4"), settings=settings, temp_dir=str(tmp_path))
    counts = mgr.counts()
    assert counts["queued"] == 2
    assert counts["running"] == 0 and counts["done"] == 0
