"""Batch processing queue (spec: "Batch Processing" -- 10/50/100 videos or
a whole folder, processed automatically).

Deliberately reuses the exact same QThread workers the single-item pages
use -- services.clip_pipeline.ClipGenerationWorker for AI clip generation,
and a thin ExportClipJobWorker wrapping export.exporter.export_clip for
exports -- so there is exactly one code path for "process one video",
whether it was triggered from a single page or from this queue. This
manager's only job is bounded concurrency + status tracking.

Cancellation note: a *queued* job can be removed cleanly before it
starts. A *running* job cannot be safely killed mid-ffmpeg-subprocess
here (that would need a cancellation token threaded through every ffmpeg
call and a way to terminate the child process) -- so cancel is only
offered for queued jobs; a running job always runs to completion or
failure. This is documented rather than silently half-working.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal, QThread

from services.clip_pipeline import ClipGenerationWorker
from export.exporter import export_clip
from utils.logger import get_logger

log = get_logger("batch_queue")


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class BatchJob:
    kind: str              # "generate_clips" | "export_clip"
    label: str
    payload: dict[str, Any]
    id: str = field(default_factory=_uid)
    status: str = "queued"  # queued/running/done/error/cancelled
    progress: float = 0.0
    stage: str = ""
    error: str = ""
    output_path: str | None = None


class ExportClipJobWorker(QThread):
    progress = Signal(str, float)  # stage, 0-100
    done = Signal(str)
    error = Signal(str)

    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload

    def run(self):
        p = self.payload
        try:
            export_clip(
                p["source_path"], p["start"], p["end"], p["sentences"], p["output_path"], p["settings"],
                temp_dir=p.get("temp_dir", "."),
                progress_cb=lambda stage, pct: self.progress.emit(stage, pct * 100),
            )
            self.done.emit(p["output_path"])
        except Exception as exc:
            log.exception("Batch export job failed: %s", p.get("output_path"))
            self.error.emit(str(exc))


class BatchQueueManager(QObject):
    job_added = Signal(str)     # job_id
    job_updated = Signal(str)   # job_id
    queue_finished = Signal()

    def __init__(self, max_concurrent: int = 2, parent=None):
        super().__init__(parent)
        self.max_concurrent = max(1, max_concurrent)
        self.jobs: list[BatchJob] = []
        self._workers: dict[str, QThread] = {}
        self._running = False

    # ---- adding jobs --------------------------------------------------------
    def add_export_job(self, label: str, *, source_path: str, start: float, end: float,
                        sentences: list, output_path: str, settings, temp_dir: str) -> BatchJob:
        job = BatchJob(kind="export_clip", label=label, payload={
            "source_path": source_path, "start": start, "end": end, "sentences": sentences,
            "output_path": output_path, "settings": settings, "temp_dir": temp_dir,
        })
        self.jobs.append(job)
        self.job_added.emit(job.id)
        if self._running:
            self._fill_slots()
        return job

    def add_generate_job(self, label: str, *, source_video_id: str, video_path: str,
                          duration_bucket: int, whisper_model_size: str, language: str | None,
                          temp_dir: str, caption_backend: str = "heuristic",
                          caption_llm_provider: str = "anthropic", caption_llm_api_key: str = "",
                          caption_llm_model: str = "") -> BatchJob:
        job = BatchJob(kind="generate_clips", label=label, payload={
            "source_video_id": source_video_id, "video_path": video_path,
            "duration_bucket": duration_bucket, "whisper_model_size": whisper_model_size,
            "language": language, "temp_dir": temp_dir, "caption_backend": caption_backend,
            "caption_llm_provider": caption_llm_provider, "caption_llm_api_key": caption_llm_api_key,
            "caption_llm_model": caption_llm_model,
        })
        self.jobs.append(job)
        self.job_added.emit(job.id)
        if self._running:
            self._fill_slots()
        return job

    # ---- control -----------------------------------------------------------
    def start(self):
        self._running = True
        self._fill_slots()

    def pause(self):
        """Stop starting new jobs; anything already running finishes normally."""
        self._running = False

    def retry_job(self, job_id: str):
        job = self._find(job_id)
        if job and job.status == "error":
            job.status, job.error, job.progress = "queued", "", 0.0
            self.job_updated.emit(job.id)
            if self._running:
                self._fill_slots()

    def remove_job(self, job_id: str) -> bool:
        job = self._find(job_id)
        if not job or job.status == "running":
            return False
        self.jobs.remove(job)
        return True

    def counts(self) -> dict[str, int]:
        out = {"queued": 0, "running": 0, "done": 0, "error": 0, "cancelled": 0}
        for j in self.jobs:
            out[j.status] = out.get(j.status, 0) + 1
        return out

    # ---- internals -----------------------------------------------------------
    def _find(self, job_id: str) -> BatchJob | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def _fill_slots(self):
        if not self._running:
            return
        active = sum(1 for j in self.jobs if j.status == "running")
        for job in self.jobs:
            if active >= self.max_concurrent:
                break
            if job.status == "queued":
                self._start_job(job)
                active += 1

    def _start_job(self, job: BatchJob):
        job.status = "running"
        job.progress = 0.0
        self.job_updated.emit(job.id)

        if job.kind == "export_clip":
            worker: QThread = ExportClipJobWorker(job.payload)
            worker.progress.connect(lambda stage, pct, jid=job.id: self._on_progress(jid, stage, pct))
            worker.done.connect(lambda path, jid=job.id: self._on_done(jid, path))
            worker.error.connect(lambda msg, jid=job.id: self._on_error(jid, msg))
        else:
            p = job.payload
            worker = ClipGenerationWorker(
                p["source_video_id"], p["video_path"], duration_bucket=p["duration_bucket"],
                whisper_model_size=p["whisper_model_size"], language=p["language"], temp_dir=p["temp_dir"],
                caption_backend=p.get("caption_backend", "heuristic"),
                caption_llm_provider=p.get("caption_llm_provider", "anthropic"),
                caption_llm_api_key=p.get("caption_llm_api_key", ""),
                caption_llm_model=p.get("caption_llm_model", ""),
            )
            worker.stage_changed.connect(lambda stage, jid=job.id: self._on_stage(jid, stage))
            worker.progress.connect(lambda pct, jid=job.id: self._on_progress(jid, None, pct))
            worker.finished_ok.connect(lambda count, jid=job.id: self._on_done(jid, f"{count} clips"))
            worker.failed.connect(lambda msg, jid=job.id: self._on_error(jid, msg))

        self._workers[job.id] = worker
        worker.finished.connect(lambda jid=job.id: self._on_thread_finished(jid))
        worker.start()

    def _on_thread_finished(self, job_id: str):
        # Only safe place to drop our reference to the QThread object:
        # QThread.finished is guaranteed to fire once Qt itself considers
        # the thread done. Dropping the reference inside _on_done/_on_error
        # instead (which run off the worker's own done/error signal, queued
        # from the worker thread) can race Qt's internal thread-finished
        # bookkeeping and abort the process ("QThread: Destroyed while
        # thread is still running") -- this bit the first version of this
        # file during testing.
        self._workers.pop(job_id, None)

    def _on_stage(self, job_id: str, stage: str):
        job = self._find(job_id)
        if job:
            job.stage = stage
            self.job_updated.emit(job_id)

    def _on_progress(self, job_id: str, stage: str | None, pct: float):
        job = self._find(job_id)
        if job:
            if stage is not None:
                job.stage = stage
            job.progress = pct
            self.job_updated.emit(job_id)

    def _on_done(self, job_id: str, output):
        job = self._find(job_id)
        if job:
            job.status, job.progress, job.output_path = "done", 100.0, output
            self.job_updated.emit(job_id)
        self._finish_slot(job_id)

    def _on_error(self, job_id: str, message: str):
        job = self._find(job_id)
        if job:
            job.status, job.error = "error", message
            self.job_updated.emit(job_id)
        self._finish_slot(job_id)

    def _finish_slot(self, job_id: str):
        self._fill_slots()
        if self._running and self.jobs and all(j.status in ("done", "error", "cancelled") for j in self.jobs):
            self.queue_finished.emit()
