"""Export page: turn a generated Clip candidate into a finished MP4 with
the chosen resolution/fps/codec/bitrate/hardware-accelerator and burned
in subtitles, matching the Export section of the spec.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QProgressBar, QMessageBox, QFileDialog,
)
from PySide6.QtCore import QThread, Signal

from config.i18n import t
from database.db import get_session
from database.models import Clip, SourceVideo, ExportJob, HistoryEntry
from export.exporter import export_clip, ExportSettings
from subtitle.styles import list_styles
from ai.transcription import Transcript, Sentence
from utils.logger import get_logger

log = get_logger("export_page")


class ExportWorker(QThread):
    stage = Signal(str, float)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, source_path, start, end, sentences, output_path, settings, temp_dir):
        super().__init__()
        self.source_path, self.start, self.end = source_path, start, end
        self.sentences, self.output_path = sentences, output_path
        self.settings, self.temp_dir = settings, temp_dir

    def run(self):
        try:
            export_clip(
                self.source_path, self.start, self.end, self.sentences, self.output_path,
                self.settings, temp_dir=self.temp_dir,
                progress_cb=lambda stage, pct: self.stage.emit(stage, pct * 100),
            )
            self.done.emit(self.output_path)
        except Exception as exc:
            log.exception("Export failed")
            self.error.emit(str(exc))


def _sentences_for_clip(transcript_path: str | None, start: float, end: float) -> list[Sentence]:
    if not transcript_path or not Path(transcript_path).exists():
        return []
    transcript = Transcript.from_json(transcript_path)
    sliced = [s for s in transcript.sentences if s.start >= start - 0.05 and s.end <= end + 0.05]
    # Re-base timestamps to 0 so they line up with the *cut* clip, not the source.
    return [Sentence(text=s.text, start=max(s.start - start, 0), end=max(s.end - start, 0), words=[
        type(w)(text=w.text, start=max(w.start - start, 0), end=max(w.end - start, 0)) for w in s.words
    ]) for s in sliced]


class ExportPage(QWidget):
    def __init__(self, language: str, settings, parent=None):
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self.current_project_id: str | None = None
        self._worker: ExportWorker | None = None
        self._build_ui()

    def set_project(self, project_id: str):
        self.current_project_id = project_id
        self._reload_clips()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(t("export.title", self.language))
        title.setProperty("role", "title")
        root.addWidget(title)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Klip" if self.language == "id" else "Clip"))
        self.clip_combo = QComboBox()
        self.clip_combo.setMinimumWidth(320)
        row1.addWidget(self.clip_combo, 1)
        root.addLayout(row1)

        grid = QHBoxLayout()

        col1 = QVBoxLayout()
        col1.addWidget(QLabel(t("export.resolution", self.language)))
        self.res_combo = QComboBox()
        self.res_combo.addItems(["480p", "720p", "1080p", "2K", "4K"])
        self.res_combo.setCurrentText("1080p")
        col1.addWidget(self.res_combo)
        grid.addLayout(col1)

        col2 = QVBoxLayout()
        col2.addWidget(QLabel(t("export.fps", self.language)))
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "30", "60"])
        self.fps_combo.setCurrentText("30")
        col2.addWidget(self.fps_combo)
        grid.addLayout(col2)

        col3 = QVBoxLayout()
        col3.addWidget(QLabel(t("export.codec", self.language)))
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["h264", "h265", "av1"])
        col3.addWidget(self.codec_combo)
        grid.addLayout(col3)

        col4 = QVBoxLayout()
        col4.addWidget(QLabel(t("export.bitrate", self.language)))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["auto", "4M", "8M", "12M", "20M"])
        col4.addWidget(self.bitrate_combo)
        grid.addLayout(col4)

        col5 = QVBoxLayout()
        col5.addWidget(QLabel(t("export.hw_accel", self.language)))
        self.hw_combo = QComboBox()
        self.hw_combo.addItems(["auto", "nvidia", "quicksync", "amf", "cpu"])
        col5.addWidget(self.hw_combo)
        grid.addLayout(col5)

        col6 = QVBoxLayout()
        col6.addWidget(QLabel("Subtitle Style"))
        self.style_combo = QComboBox()
        for style in list_styles():
            self.style_combo.addItem(style.label, style.key)
        col6.addWidget(self.style_combo)
        grid.addLayout(col6)

        root.addLayout(grid)

        self.export_btn = QPushButton(t("export.start", self.language))
        self.export_btn.setProperty("class", "primary")
        self.export_btn.clicked.connect(self._start_export)
        root.addWidget(self.export_btn)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        root.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        root.addStretch(1)

    def _reload_clips(self):
        self.clip_combo.clear()
        if not self.current_project_id:
            return
        with get_session() as session:
            clips = (
                session.query(Clip).join(SourceVideo)
                .filter(SourceVideo.project_id == self.current_project_id)
                .order_by(Clip.viral_score.desc()).all()
            )
            for c in clips:
                label = f"{c.start_time:.0f}s-{c.end_time:.0f}s · score {c.viral_score:.0f} · {c.transcript_text[:40]}"
                self.clip_combo.addItem(label, c.id)

    def _start_export(self):
        if self.clip_combo.count() == 0:
            QMessageBox.warning(self, "AI Klipers", "Belum ada klip untuk di-export. Generate klip dulu."
                                 if self.language == "id" else "No clips to export yet. Generate clips first.")
            return

        clip_id = self.clip_combo.currentData()
        with get_session() as session:
            clip = session.get(Clip, clip_id)
            video = session.get(SourceVideo, clip.source_video_id) if clip else None
        if not clip or not video or not video.local_path:
            QMessageBox.warning(self, "AI Klipers", "Video sumber untuk klip ini tidak ditemukan."
                                 if self.language == "id" else "Source video for this clip was not found.")
            return

        output_dir = Path(self.settings.output_folder) / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"clip_{clip.id[:8]}.mp4")

        settings = ExportSettings(
            resolution=self.res_combo.currentText(), fps=int(self.fps_combo.currentText()),
            codec=self.codec_combo.currentText(), bitrate=self.bitrate_combo.currentText(),
            hw_accel=self.hw_combo.currentText(), subtitle_style=self.style_combo.currentData(),
        )
        sentences = _sentences_for_clip(video.transcript_path, clip.start_time, clip.end_time)

        with get_session() as session:
            job = ExportJob(clip_id=clip.id, resolution=settings.resolution, fps=settings.fps,
                             codec=settings.codec, bitrate=settings.bitrate, hw_accel=settings.hw_accel,
                             output_path=output_path, status="running")
            session.add(job)
            session.commit()
            self._current_job_id = job.id

        self.export_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self._worker = ExportWorker(video.local_path, clip.start_time, clip.end_time, sentences,
                                     output_path, settings, self.settings.temp_folder)
        self._worker.stage.connect(self._on_stage)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_stage(self, stage: str, pct: float):
        self.status_label.setText(stage)
        self.progress_bar.setValue(int(pct))

    def _on_done(self, output_path: str):
        with get_session() as session:
            job = session.get(ExportJob, self._current_job_id)
            if job:
                job.status = "done"
                job.progress_percent = 100.0
            clip = session.query(Clip).filter_by(id=job.clip_id).first() if job else None
            if clip:
                clip.status = "exported"
                clip.output_path = output_path
            session.add(HistoryEntry(entry_type="export", reference_id=self._current_job_id,
                                      description=f"Export selesai: {Path(output_path).name}"))
            session.commit()

        self.export_btn.setEnabled(True)
        self.status_label.setText(f"Selesai ✓ {output_path}" if self.language == "id" else f"Done ✓ {output_path}")
        QMessageBox.information(self, "AI Klipers",
                                 f"Export selesai:\n{output_path}" if self.language == "id"
                                 else f"Export complete:\n{output_path}")

    def _on_error(self, message: str):
        with get_session() as session:
            job = session.get(ExportJob, getattr(self, "_current_job_id", None))
            if job:
                job.status = "error"
                job.error_message = message
                session.commit()
        self.export_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "AI Klipers", f"Export gagal:\n{message}"
                              if self.language == "id" else f"Export failed:\n{message}")
