"""AI Clip Generator page: the heart of the app. Pick a source video, pick
a duration bucket, hit Generate, and watch ranked clip candidates stream
in from the background ClipGenerationWorker.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QFrame,
    QProgressBar, QScrollArea, QGridLayout, QMessageBox, QDialog, QLineEdit,
    QTextEdit, QApplication,
)
from PySide6.QtCore import Qt

from config.i18n import t
from database.db import get_session
from database.models import SourceVideo, Clip
from services.clip_pipeline import ClipGenerationWorker
from app.widgets.score_ring import ScoreRing
from utils.logger import get_logger

log = get_logger("ai_clip_generator_page")

STAGE_LABELS = {
    "id": {
        "audio": "Mengekstrak audio...", "transcribe": "Transkripsi ucapan...",
        "scenes": "Deteksi pergantian scene...", "faces": "Deteksi wajah & ekspresi...",
        "scoring": "Menilai & memotong klip...", "captions": "Membuat judul & caption...",
        "done": "Selesai ✓",
    },
    "en": {
        "audio": "Extracting audio...", "transcribe": "Transcribing speech...",
        "scenes": "Detecting scene changes...", "faces": "Detecting faces & expressions...",
        "scoring": "Scoring & splitting clips...", "captions": "Writing titles & captions...",
        "done": "Done ✓",
    },
}


class CaptionDialog(QDialog):
    def __init__(self, clip: dict, language: str, parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle("Caption & Metadata" if language != "id" else "Caption & Metadata")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        source = clip.get("caption_source", "")
        if source:
            badge = QLabel(f"Sumber: {'LLM' if source == 'llm' else 'Heuristik'}" if language == "id"
                            else f"Source: {'LLM' if source == 'llm' else 'Heuristic'}")
            badge.setProperty("role", "muted")
            badge.setStyleSheet("font-size: 11px;")
            layout.addWidget(badge)

        self._fields: list[tuple[str, QLineEdit | QTextEdit]] = []
        self._add_line_field(layout, "Judul" if language == "id" else "Title", clip.get("suggested_title", ""))
        self._add_line_field(layout, "Caption", clip.get("suggested_caption", ""))
        self._add_text_field(layout, "Deskripsi" if language == "id" else "Description",
                              clip.get("suggested_description", ""))
        self._add_line_field(layout, "Hashtag" if language == "id" else "Hashtags", clip.get("suggested_hashtags", ""))
        self._add_line_field(layout, "Kata Kunci SEO" if language == "id" else "SEO Keywords",
                              clip.get("suggested_keywords", ""))

        buttons = QHBoxLayout()
        copy_all_btn = QPushButton("📋 Salin Semua" if language == "id" else "📋 Copy All")
        copy_all_btn.setProperty("class", "primary")
        copy_all_btn.clicked.connect(self._copy_all)
        close_btn = QPushButton("Tutup" if language == "id" else "Close")
        close_btn.setProperty("class", "secondary")
        close_btn.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(copy_all_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _add_line_field(self, layout, label: str, value: str):
        layout.addWidget(QLabel(label))
        edit = QLineEdit(value)
        edit.setReadOnly(True)
        layout.addWidget(edit)
        self._fields.append((label, edit))

    def _add_text_field(self, layout, label: str, value: str):
        layout.addWidget(QLabel(label))
        edit = QTextEdit(value)
        edit.setReadOnly(True)
        edit.setFixedHeight(70)
        layout.addWidget(edit)
        self._fields.append((label, edit))

    def _copy_all(self):
        lines = []
        for label, widget in self._fields:
            value = widget.text() if isinstance(widget, QLineEdit) else widget.toPlainText()
            lines.append(f"{label}:\n{value}")
        QApplication.clipboard().setText("\n\n".join(lines))


class ClipCard(QFrame):
    def __init__(self, clip: dict, language: str, parent=None):
        super().__init__(parent)
        self.clip = clip
        self.language = language
        self.setProperty("class", "card")
        self.setFixedHeight(168)

        layout = QHBoxLayout(self)
        ring = ScoreRing(clip["viral_score"], size=56)
        layout.addWidget(ring)

        text_col = QVBoxLayout()
        start_m, start_s = divmod(int(clip["start_time"]), 60)
        end_m, end_s = divmod(int(clip["end_time"]), 60)
        time_lbl = QLabel(f"{start_m}:{start_s:02d} → {end_m}:{end_s:02d}  ({clip['duration']:.0f}s)")
        time_lbl.setStyleSheet("font-weight: 600;")
        text_col.addWidget(time_lbl)

        title_text = clip.get("suggested_title") or ""
        if title_text:
            title_lbl = QLabel(title_text)
            title_lbl.setWordWrap(True)
            title_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #6E56F8;")
            text_col.addWidget(title_lbl)

        transcript_lbl = QLabel(clip["transcript_text"][:120] + ("..." if len(clip["transcript_text"]) > 120 else ""))
        transcript_lbl.setWordWrap(True)
        transcript_lbl.setProperty("role", "muted")
        text_col.addWidget(transcript_lbl)

        conf_lbl = QLabel(f"{t('clipgen.confidence', language)}: {clip['confidence_score']:.0f}%")
        conf_lbl.setProperty("role", "muted")
        conf_lbl.setStyleSheet("font-size: 11px;")
        text_col.addWidget(conf_lbl)
        text_col.addStretch(1)

        if clip.get("suggested_title") or clip.get("suggested_caption"):
            caption_btn = QPushButton("📋 Caption")
            caption_btn.setProperty("class", "secondary")
            caption_btn.clicked.connect(self._show_caption_dialog)
            text_col.addWidget(caption_btn, 0)

        layout.addLayout(text_col, 1)

    def _show_caption_dialog(self):
        dialog = CaptionDialog(self.clip, self.language, self)
        dialog.exec()


class AIClipGeneratorPage(QWidget):
    def __init__(self, language: str, settings, parent=None):
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self.current_project_id: str | None = None
        self._worker: ClipGenerationWorker | None = None
        self._clip_cards: list[ClipCard] = []
        self._build_ui()

    def set_project(self, project_id: str):
        self.current_project_id = project_id
        self.reload_sources()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(t("clipgen.title", self.language))
        title.setProperty("role", "title")
        root.addWidget(title)

        controls = QHBoxLayout()
        controls.addWidget(QLabel(t("clipgen.select_source", self.language)))
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(260)
        controls.addWidget(self.source_combo)

        controls.addSpacing(16)
        controls.addWidget(QLabel(t("clipgen.duration_bucket", self.language)))
        self.duration_combo = QComboBox()
        self.duration_combo.addItems(["15s", "30s", "45s", "60s"])
        self.duration_combo.setCurrentText(f"{self.settings.default_clip_duration}s")
        controls.addWidget(self.duration_combo)
        controls.addStretch(1)

        self.generate_btn = QPushButton(t("clipgen.generate", self.language))
        self.generate_btn.setProperty("class", "primary")
        self.generate_btn.clicked.connect(self._start_generation)
        controls.addWidget(self.generate_btn)
        root.addLayout(controls)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setVisible(False)
        root.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.results_host = QWidget()
        self.results_grid = QGridLayout(self.results_host)
        self.results_grid.setSpacing(12)
        scroll.setWidget(self.results_host)
        root.addWidget(scroll, 1)

    def reload_sources(self):
        self.source_combo.clear()
        if not self.current_project_id:
            return
        with get_session() as session:
            videos = session.query(SourceVideo).filter_by(
                project_id=self.current_project_id, status="ready"
            ).all()
            for v in videos:
                self.source_combo.addItem(v.title or v.local_path or v.url, v.id)

    def _start_generation(self):
        if self.source_combo.count() == 0:
            QMessageBox.warning(self, "AI Klipers",
                                 "Import & download video dulu sebelum generate klip."
                                 if self.language == "id" else "Import & download a video before generating clips.")
            return

        source_id = self.source_combo.currentData()
        with get_session() as session:
            video = session.get(SourceVideo, source_id)
            video_path = video.local_path if video else None
        if not video_path or not Path(video_path).exists():
            QMessageBox.warning(self, "AI Klipers", "File video sumber tidak ditemukan di disk."
                                 if self.language == "id" else "Source video file not found on disk.")
            return

        duration_bucket = int(self.duration_combo.currentText().rstrip("s"))

        self.generate_btn.setEnabled(False)
        self.status_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._clear_results()

        self._worker = ClipGenerationWorker(
            source_id, video_path, duration_bucket=duration_bucket,
            whisper_model_size=self.settings.whisper_model_size,
            language=self.language if self.language in ("id", "en") else None,
            temp_dir=self.settings.temp_folder,
            caption_backend=self.settings.caption_backend,
            caption_llm_provider=self.settings.caption_llm_provider,
            caption_llm_api_key=self.settings.caption_llm_api_key,
            caption_llm_model=self.settings.caption_llm_model,
        )
        self._worker.stage_changed.connect(self._on_stage_changed)
        self._worker.progress.connect(lambda p: self.progress_bar.setValue(int(p)))
        self._worker.clip_ready.connect(self._on_clip_ready)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_stage_changed(self, stage: str):
        label = STAGE_LABELS.get(self.language, STAGE_LABELS["en"]).get(stage, stage)
        self.status_label.setText(label)
        self.progress_bar.setValue(0)

    def _on_clip_ready(self, clip: dict):
        idx = len(self._clip_cards)
        card = ClipCard(clip, self.language)
        self._clip_cards.append(card)
        self.results_grid.addWidget(card, idx // 2, idx % 2)

    def _on_finished(self, count: int):
        self.generate_btn.setEnabled(True)
        msg = f"{count} klip berhasil dibuat." if self.language == "id" else f"{count} clips generated."
        self.status_label.setText(msg)
        self.progress_bar.setVisible(False)

    def _on_failed(self, message: str):
        self.generate_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "AI Klipers", f"Gagal generate klip:\n{message}"
                              if self.language == "id" else f"Clip generation failed:\n{message}")

    def _clear_results(self):
        while self.results_grid.count():
            item = self.results_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._clip_cards = []
