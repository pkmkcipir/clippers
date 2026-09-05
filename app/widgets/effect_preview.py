"""On-demand effect preview: a still frame of the selected clip with its
effects applied, regenerated in the background whenever the selection,
its effects, or the playhead (while scrubbing inside that clip) change.

Deliberately NOT continuous live video -- re-implementing every ffmpeg
filter (including the split/blend ones like glow and blur_background) as
a real-time Qt/GPU shader pipeline would be its own multi-week project.
A debounced still frame is a fraction of the effort and answers the
actual question someone has while adjusting an effect: "what does this
look like now?" -- see README's documented simplifications.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from app.timeline.model import TimelineClip
from editor.ffmpeg_utils import get_media_info, render_filtered_frame
from editor.effects import build_video_filter_graph
from utils.logger import get_logger

log = get_logger("effect_preview")

DEBOUNCE_MS = 400
PREVIEW_HEIGHT = 160


class _FrameRenderWorker(QThread):
    done = Signal(str, int)   # output path, request token
    error = Signal(str, int)

    def __init__(self, clip: TimelineClip, at_source_time: float, temp_path: str, token: int):
        super().__init__()
        self.clip = clip
        self.at_source_time = at_source_time
        self.temp_path = temp_path
        self.token = token

    def run(self):
        try:
            info = get_media_info(self.clip.source_path)
            graph = build_video_filter_graph(
                self.clip.effects, width=info["width"], height=info["height"],
                clip_duration=self.clip.duration, video_path=self.clip.source_path,
                clip_source_start=self.clip.source_in, clip_source_end=self.clip.source_out,
                lut_dir=str(Path(self.temp_path).parent / "luts"),
            )
            render_filtered_frame(self.clip.source_path, self.at_source_time, graph, self.temp_path)
            self.done.emit(self.temp_path, self.token)
        except Exception as exc:
            log.warning("Effect preview render failed: %s", exc)
            self.error.emit(str(exc), self.token)


class EffectPreviewPanel(QWidget):
    def __init__(self, temp_dir_provider, language: str = "id", parent=None):
        """temp_dir_provider: zero-arg callable returning the current temp
        folder (mirrors VideoPreviewPanel's project_provider pattern)."""
        super().__init__(parent)
        self._temp_dir_provider = temp_dir_provider
        self.language = language
        self._active_workers: dict[int, _FrameRenderWorker] = {}
        self._pending_clip: TimelineClip | None = None
        self._pending_time: float = 0.0
        self._request_token = 0
        self._latest_applied_token = -1

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._start_render)

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        title = QLabel("Preview Efek" if self.language == "id" else "Effect Preview")
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setFixedWidth(28)
        self.refresh_btn.setToolTip("Refresh preview" if self.language != "id" else "Perbarui preview")
        self.refresh_btn.clicked.connect(self._force_refresh)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(PREVIEW_HEIGHT)
        self.image_label.setStyleSheet(
            "background-color: #14151C; border-radius: 8px; color: #5A5C6E; font-size: 11px;")
        self.image_label.setText("Pilih klip video untuk melihat preview efek"
                                  if self.language == "id" else "Select a video clip to preview its effects")
        root.addWidget(self.image_label)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setStyleSheet("font-size: 10px;")
        root.addWidget(self.status_label)

    # ---- public API ---------------------------------------------------------
    def show_clip(self, clip: TimelineClip | None, at_time_in_clip: float | None = None):
        """Call whenever the selected clip changes, its effects change, or
        the playhead moves within it. `at_time_in_clip` is seconds from the
        clip's own timeline_start; defaults to the clip's midpoint."""
        if clip is None or clip.kind != "video" or not clip.source_path:
            self._debounce_timer.stop()
            self._pending_clip = None
            self.image_label.setText(
                "Pilih klip video untuk melihat preview efek" if self.language == "id"
                else "Select a video clip to preview its effects")
            self.image_label.setPixmap(QPixmap())
            self.status_label.setText("")
            return

        offset = clip.duration / 2 if at_time_in_clip is None else max(0.0, min(at_time_in_clip, clip.duration))
        self._pending_clip = clip
        self._pending_time = clip.source_in + offset
        self.status_label.setText("Menunggu perubahan berhenti..." if self.language == "id" else "Waiting for changes to settle...")
        self._debounce_timer.start(DEBOUNCE_MS)

    def _force_refresh(self):
        if self._pending_clip is not None:
            self._debounce_timer.stop()
            self._start_render()

    # ---- internals -----------------------------------------------------------
    def _start_render(self):
        clip = self._pending_clip
        if clip is None:
            return
        if not clip.effects:
            # Nothing to preview differently from the raw frame -- skip the
            # ffmpeg round-trip entirely and just say so.
            self.status_label.setText(
                "Belum ada efek pada klip ini." if self.language == "id" else "This clip has no effects yet.")
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("(tanpa efek)" if self.language == "id" else "(no effects)")
            return

        self._request_token += 1
        token = self._request_token
        temp_dir = Path(self._temp_dir_provider()) / "effect_previews"
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(temp_dir / f"preview_{token}.png")

        self.status_label.setText("Merender preview..." if self.language == "id" else "Rendering preview...")
        worker = _FrameRenderWorker(clip, self._pending_time, out_path, token)
        worker.done.connect(self._on_render_done)
        worker.error.connect(self._on_render_error)
        worker.finished.connect(lambda tok=token: self._active_workers.pop(tok, None))
        self._active_workers[token] = worker
        worker.start()

    def _on_render_done(self, path: str, token: int):
        if token < self._latest_applied_token:
            return  # a newer request already finished and applied; ignore this stale one
        self._latest_applied_token = token
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.image_label.setPixmap(pixmap.scaledToHeight(PREVIEW_HEIGHT, Qt.SmoothTransformation))
        self.status_label.setText("Preview terbaru ✓" if self.language == "id" else "Preview up to date ✓")

    def _on_render_error(self, message: str, token: int):
        if token < self._latest_applied_token:
            return
        self.status_label.setText(
            "Gagal merender preview." if self.language == "id" else "Failed to render preview.")
