"""Batch Export / Batch Processing page (spec: process 10/50/100 videos
or a whole folder automatically). Two ways to fill the queue:
  1. Pick several already-generated Clips from this project -> export
     them all with one shared set of export settings.
  2. Pick several imported source videos, or point at a whole folder of
     video files not yet in the project, -> run each through the full
     AI Clip Generator pipeline (download-if-needed is out of scope here;
     these must already be local files -- see clip bin note in-app).
Everything runs through services.batch_queue.BatchQueueManager, which
reuses the exact same workers the single-item pages use.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QComboBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFileDialog, QAbstractItemView,
)
from PySide6.QtCore import Qt

from config.i18n import t
from database.db import get_session
from database.models import SourceVideo, Clip as ClipRecord, HistoryEntry
from editor.ffmpeg_utils import get_media_info
from export.exporter import ExportSettings
from services.batch_queue import BatchQueueManager, BatchJob
from utils.logger import get_logger

log = get_logger("batch_export_page")

STATUS_LABELS = {
    "id": {"queued": "Menunggu", "running": "Berjalan", "done": "Selesai",
           "error": "Gagal", "cancelled": "Dibatalkan"},
    "en": {"queued": "Queued", "running": "Running", "done": "Done",
           "error": "Error", "cancelled": "Cancelled"},
}
KIND_LABELS = {
    "id": {"export_clip": "Export Klip", "generate_clips": "Generate Klip (AI)"},
    "en": {"export_clip": "Export Clip", "generate_clips": "AI Generate Clips"},
}


class BatchExportPage(QWidget):
    def __init__(self, language: str, settings, parent=None):
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self.current_project_id: str | None = None
        self.queue = BatchQueueManager(max_concurrent=2)
        self._row_by_job_id: dict[str, int] = {}
        self._build_ui()
        self._wire_queue_signals()

    def set_project(self, project_id: str):
        self.current_project_id = project_id
        self._reload_source_lists()

    def refresh(self):
        self._reload_source_lists()

    # ---- UI construction -----------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel(t("nav.batch_export", self.language))
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(
            "Proses banyak klip atau video sekaligus secara otomatis di latar belakang."
            if self.language == "id" else
            "Process many clips or videos at once, automatically, in the background.")
        subtitle.setProperty("role", "muted")
        root.addWidget(subtitle)

        source_row = QHBoxLayout()
        source_row.addWidget(self._build_export_source_box(), 1)
        source_row.addWidget(self._build_generate_source_box(), 1)
        root.addLayout(source_row)

        root.addWidget(self._build_queue_controls())
        root.addWidget(self._build_queue_table(), 1)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "muted")
        root.addWidget(self.summary_label)

    def _build_export_source_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Export Klip yang Sudah Ada" if self.language == "id" else "Export Existing Clips"))

        self.clip_list = QListWidget()
        self.clip_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.clip_list.setMaximumHeight(120)
        layout.addWidget(self.clip_list)

        settings_row = QHBoxLayout()
        self.batch_res_combo = QComboBox()
        self.batch_res_combo.addItems(["480p", "720p", "1080p", "2K", "4K"])
        self.batch_res_combo.setCurrentText("1080p")
        self.batch_style_combo = QComboBox()
        from subtitle.styles import list_styles
        for style in list_styles():
            self.batch_style_combo.addItem(style.label, style.key)
        settings_row.addWidget(self.batch_res_combo)
        settings_row.addWidget(self.batch_style_combo)
        layout.addLayout(settings_row)

        add_btn = QPushButton("+ Tambah ke Antrian" if self.language == "id" else "+ Add to Queue")
        add_btn.setProperty("class", "secondary")
        add_btn.clicked.connect(self._enqueue_selected_clips)
        layout.addWidget(add_btn)
        return box

    def _build_generate_source_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Generate Klip AI dari Video" if self.language == "id" else "AI-Generate Clips from Video"))

        self.video_list = QListWidget()
        self.video_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.video_list.setMaximumHeight(120)
        layout.addWidget(self.video_list)

        settings_row = QHBoxLayout()
        self.batch_duration_combo = QComboBox()
        self.batch_duration_combo.addItems(["15", "30", "45", "60"])
        self.batch_duration_combo.setCurrentText(str(self.settings.default_clip_duration))
        settings_row.addWidget(self.batch_duration_combo)
        layout.addLayout(settings_row)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Tambah ke Antrian" if self.language == "id" else "+ Add to Queue")
        add_btn.setProperty("class", "secondary")
        add_btn.clicked.connect(self._enqueue_selected_videos)
        folder_btn = QPushButton("📁 Import Folder Penuh" if self.language == "id" else "📁 Import Whole Folder")
        folder_btn.setProperty("class", "secondary")
        folder_btn.clicked.connect(self._import_folder_and_enqueue)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(folder_btn)
        layout.addLayout(btn_row)
        return box

    def _build_queue_controls(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)

        self.start_btn = QPushButton("▶ Mulai Antrian" if self.language == "id" else "▶ Start Queue")
        self.start_btn.setProperty("class", "primary")
        self.start_btn.clicked.connect(self._start_queue)
        self.pause_btn = QPushButton("⏸ Jeda" if self.language == "id" else "⏸ Pause")
        self.pause_btn.setProperty("class", "secondary")
        self.pause_btn.clicked.connect(self._pause_queue)
        row.addWidget(self.start_btn)
        row.addWidget(self.pause_btn)

        row.addWidget(QLabel("Maks. paralel:" if self.language == "id" else "Max concurrent:"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 8)
        self.concurrency_spin.setValue(self.queue.max_concurrent)
        self.concurrency_spin.valueChanged.connect(self._on_concurrency_changed)
        row.addWidget(self.concurrency_spin)
        row.addStretch(1)

        clear_btn = QPushButton("🗑 Bersihkan yang Selesai" if self.language == "id" else "🗑 Clear Finished")
        clear_btn.setProperty("class", "secondary")
        clear_btn.clicked.connect(self._clear_finished)
        row.addWidget(clear_btn)
        return box

    def _build_queue_table(self) -> QTableWidget:
        columns = (["Item", "Tipe", "Status", "Progress", "Aksi"] if self.language == "id"
                   else ["Item", "Kind", "Status", "Progress", "Action"])
        table = QTableWidget()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue_table = table
        return table

    # ---- populating clip/video pickers -----------------------------------
    def _reload_source_lists(self):
        self.clip_list.clear()
        self.video_list.clear()
        if not self.current_project_id:
            return
        with get_session() as session:
            clips = (session.query(ClipRecord).join(SourceVideo)
                      .filter(SourceVideo.project_id == self.current_project_id).all())
            for c in clips:
                video = session.get(SourceVideo, c.source_video_id)
                if not video or not video.local_path:
                    continue
                item = QListWidgetItem(f"{c.transcript_text[:35] or 'Clip'}...  ({c.duration:.0f}s, score {c.viral_score:.0f})")
                item.setData(Qt.UserRole, c.id)
                self.clip_list.addItem(item)

            videos = session.query(SourceVideo).filter_by(
                project_id=self.current_project_id, status="ready").all()
            for v in videos:
                if not v.local_path:
                    continue
                item = QListWidgetItem(f"{v.title or Path(v.local_path).name}  ({v.duration_sec:.0f}s)")
                item.setData(Qt.UserRole, v.id)
                self.video_list.addItem(item)

    # ---- enqueue actions -----------------------------------------------------
    def _enqueue_selected_clips(self):
        items = self.clip_list.selectedItems()
        if not items:
            self._notify("Pilih minimal satu klip." if self.language == "id" else "Select at least one clip.")
            return
        settings = ExportSettings(resolution=self.batch_res_combo.currentText(),
                                    subtitle_style=self.batch_style_combo.currentData())
        output_dir = Path(self.settings.output_folder) / "batch_exports"
        output_dir.mkdir(parents=True, exist_ok=True)

        with get_session() as session:
            for item in items:
                clip_id = item.data(Qt.UserRole)
                clip = session.get(ClipRecord, clip_id)
                video = session.get(SourceVideo, clip.source_video_id) if clip else None
                if not clip or not video or not video.local_path:
                    continue
                sentences = self._load_sentences_for_clip(video.transcript_path, clip.start_time, clip.end_time)
                output_path = str(output_dir / f"batch_{clip.id[:8]}.mp4")
                self.queue.add_export_job(
                    item.text().split("  (")[0], source_path=video.local_path,
                    start=clip.start_time, end=clip.end_time, sentences=sentences,
                    output_path=output_path, settings=settings, temp_dir=self.settings.temp_folder,
                )
        self._notify(f"{len(items)} klip ditambahkan ke antrian." if self.language == "id"
                     else f"{len(items)} clips added to the queue.")

    def _enqueue_selected_videos(self):
        items = self.video_list.selectedItems()
        if not items:
            self._notify("Pilih minimal satu video." if self.language == "id" else "Select at least one video.")
            return
        duration_bucket = int(self.batch_duration_combo.currentText())
        with get_session() as session:
            for item in items:
                video_id = item.data(Qt.UserRole)
                video = session.get(SourceVideo, video_id)
                if not video or not video.local_path:
                    continue
                self.queue.add_generate_job(
                    item.text().split("  (")[0], source_video_id=video.id, video_path=video.local_path,
                    duration_bucket=duration_bucket, whisper_model_size=self.settings.whisper_model_size,
                    language=self.language if self.language in ("id", "en") else None,
                    temp_dir=self.settings.temp_folder,
                    caption_backend=self.settings.caption_backend,
                    caption_llm_provider=self.settings.caption_llm_provider,
                    caption_llm_api_key=self.settings.caption_llm_api_key,
                    caption_llm_model=self.settings.caption_llm_model,
                )
        self._notify(f"{len(items)} video ditambahkan ke antrian." if self.language == "id"
                     else f"{len(items)} videos added to the queue.")

    def _import_folder_and_enqueue(self):
        if not self.current_project_id:
            self._notify("Pilih/buat proyek dulu dari Dashboard." if self.language == "id"
                         else "Select/create a project from the Dashboard first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Video" if self.language == "id" else "Choose Video Folder")
        if not folder:
            return

        paths = [str(p) for ext in ("mp4", "mkv", "avi", "mov") for p in Path(folder).glob(f"*.{ext}")]
        if not paths:
            self._notify("Tidak ada file video di folder itu." if self.language == "id"
                         else "No video files found in that folder.")
            return

        duration_bucket = int(self.batch_duration_combo.currentText())
        added = 0
        with get_session() as session:
            for path in paths:
                try:
                    info = get_media_info(path)
                except Exception:
                    info = {}
                video = SourceVideo(
                    project_id=self.current_project_id, source_type="local", local_path=path,
                    title=Path(path).stem, status="ready", duration_sec=info.get("duration", 0.0),
                    resolution=f"{info.get('width', 0)}x{info.get('height', 0)}", fps=info.get("fps", 0.0),
                )
                session.add(video)
                session.commit()
                session.refresh(video)
                self.queue.add_generate_job(
                    Path(path).stem, source_video_id=video.id, video_path=path,
                    duration_bucket=duration_bucket, whisper_model_size=self.settings.whisper_model_size,
                    language=self.language if self.language in ("id", "en") else None,
                    temp_dir=self.settings.temp_folder,
                    caption_backend=self.settings.caption_backend,
                    caption_llm_provider=self.settings.caption_llm_provider,
                    caption_llm_api_key=self.settings.caption_llm_api_key,
                    caption_llm_model=self.settings.caption_llm_model,
                )
                added += 1
            session.add(HistoryEntry(entry_type="download", description=f"Import folder batch: {added} video dari {folder}"))
            session.commit()

        self._reload_source_lists()
        self._notify(f"{added} video dari folder ditambahkan ke antrian." if self.language == "id"
                     else f"{added} videos from the folder added to the queue.")

    @staticmethod
    def _load_sentences_for_clip(transcript_path, start, end):
        from app.pages.export_page import _sentences_for_clip
        return _sentences_for_clip(transcript_path, start, end)

    # ---- queue control -----------------------------------------------------
    def _start_queue(self):
        if not self.queue.jobs:
            self._notify("Antrian masih kosong." if self.language == "id" else "The queue is empty.")
            return
        self.queue.start()

    def _pause_queue(self):
        self.queue.pause()

    def _on_concurrency_changed(self, value: int):
        self.queue.max_concurrent = value

    def _clear_finished(self):
        for job in list(self.queue.jobs):
            if job.status in ("done", "error", "cancelled"):
                self.queue.jobs.remove(job)
        self._rebuild_table()

    # ---- queue -> UI wiring -----------------------------------------------
    def _wire_queue_signals(self):
        self.queue.job_added.connect(lambda _jid: self._rebuild_table())
        self.queue.job_updated.connect(self._on_job_updated)
        self.queue.queue_finished.connect(
            lambda: self._notify("Semua item di antrian selesai diproses." if self.language == "id"
                                  else "Every item in the queue has finished."))

    def _rebuild_table(self):
        self.queue_table.setRowCount(0)
        self._row_by_job_id = {}
        for job in self.queue.jobs:
            self._append_row(job)
        self._update_summary()

    def _append_row(self, job: BatchJob):
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        self._row_by_job_id[job.id] = row
        self._paint_row(row, job)

    def _paint_row(self, row: int, job: BatchJob):
        labels = STATUS_LABELS.get(self.language, STATUS_LABELS["en"])
        kind_labels = KIND_LABELS.get(self.language, KIND_LABELS["en"])

        self.queue_table.setItem(row, 0, QTableWidgetItem(job.label))
        self.queue_table.setItem(row, 1, QTableWidgetItem(kind_labels.get(job.kind, job.kind)))
        status_item = QTableWidgetItem(labels.get(job.status, job.status))
        if job.status == "error":
            status_item.setToolTip(job.error)
        self.queue_table.setItem(row, 2, status_item)

        bar = QProgressBar()
        bar.setValue(int(job.progress))
        bar.setTextVisible(job.status == "running")
        self.queue_table.setCellWidget(row, 3, bar)

        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(2, 2, 2, 2)
        if job.status == "error":
            retry_btn = QPushButton("↻ Retry")
            retry_btn.setProperty("class", "secondary")
            retry_btn.clicked.connect(lambda _=False, jid=job.id: self.queue.retry_job(jid))
            action_layout.addWidget(retry_btn)
        elif job.status == "queued":
            remove_btn = QPushButton("✕")
            remove_btn.setProperty("class", "danger")
            remove_btn.clicked.connect(lambda _=False, jid=job.id: self._remove_job_row(jid))
            action_layout.addWidget(remove_btn)
        self.queue_table.setCellWidget(row, 4, action_widget)

    def _remove_job_row(self, job_id: str):
        self.queue.remove_job(job_id)
        self._rebuild_table()

    def _on_job_updated(self, job_id: str):
        row = self._row_by_job_id.get(job_id)
        job = next((j for j in self.queue.jobs if j.id == job_id), None)
        if row is None or job is None:
            return
        self._paint_row(row, job)
        self._update_summary()

    def _update_summary(self):
        counts = self.queue.counts()
        total = sum(counts.values())
        self.summary_label.setText(
            f"{counts['done']}/{total} selesai · {counts['running']} berjalan · {counts['error']} gagal"
            if self.language == "id" else
            f"{counts['done']}/{total} done · {counts['running']} running · {counts['error']} failed"
        )

    def _notify(self, text: str):
        self.summary_label.setText(text)
