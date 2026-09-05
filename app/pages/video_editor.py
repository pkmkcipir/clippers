"""The Video Editor page: a real multi-track timeline (app/timeline/),
synced preview (app/widgets/video_preview.py), a per-clip effects panel,
and a background render pass (editor/timeline_renderer.py) -- this
replaces the Phase-1 placeholder page.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QComboBox, QProgressBar, QMessageBox,
    QInputDialog, QFileDialog, QFrame, QCheckBox, QSpinBox,
)
from PySide6.QtCore import Qt, QThread, Signal

from config.i18n import t
from database.db import get_session
from database.models import SourceVideo, Clip as ClipRecord, EditorTimeline, ExportJob, HistoryEntry
from app.timeline.model import TimelineController, TimelineProject, Effect, TimelineConflictError
from app.timeline.timeline_view import TimelineView
from app.widgets.video_preview import VideoPreviewPanel
from app.widgets.effect_preview import EffectPreviewPanel
from app.pages.effect_param_dialog import EffectParamDialog
from editor.effects import EFFECT_REGISTRY
from editor.timeline_renderer import render_timeline
from utils.logger import get_logger

log = get_logger("video_editor_page")

PIP_POSITIONS = {
    "top_left": (0.0, 0.0),
    "top_right": (1.0, 0.0),
    "bottom_left": (0.0, 1.0),
    "bottom_right": (1.0, 1.0),
    "center": (0.5, 0.5),
}
PIP_POSITION_LABELS = {
    "id": {"top_left": "Kiri-Atas", "top_right": "Kanan-Atas", "bottom_left": "Kiri-Bawah",
           "bottom_right": "Kanan-Bawah", "center": "Tengah"},
    "en": {"top_left": "Top-Left", "top_right": "Top-Right", "bottom_left": "Bottom-Left",
           "bottom_right": "Bottom-Right", "center": "Center"},
}


def _closest_pip_position_key(x: float, y: float) -> str:
    """Nearest-match lookup (not exact-equality) so a custom/dragged
    position still highlights the closest preset instead of nothing."""
    return min(PIP_POSITIONS, key=lambda k: (PIP_POSITIONS[k][0] - x) ** 2 + (PIP_POSITIONS[k][1] - y) ** 2)


STAGE_LABELS = {
    "id": {"rendering_clips": "Merender klip...", "concatenating": "Menggabungkan segmen...",
           "compositing_overlays": "Menggabungkan overlay...", "final_encode": "Encoding akhir...",
           "done": "Selesai ✓"},
    "en": {"rendering_clips": "Rendering clips...", "concatenating": "Concatenating segments...",
           "compositing_overlays": "Compositing overlays...", "final_encode": "Final encoding...",
           "done": "Done ✓"},
}


class RenderWorker(QThread):
    stage = Signal(str, float)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, project: TimelineProject, temp_dir: str, output_path: str, settings: dict):
        super().__init__()
        self.project, self.temp_dir, self.output_path, self.settings = project, temp_dir, output_path, settings

    def run(self):
        try:
            render_timeline(
                self.project, self.temp_dir, self.output_path,
                resolution=self.settings["resolution"], fps=self.settings.get("fps"),
                codec=self.settings["codec"], bitrate=self.settings["bitrate"],
                hw_accel=self.settings["hw_accel"],
                progress_cb=lambda s, p: self.stage.emit(s, p * 100),
            )
            self.done.emit(self.output_path)
        except Exception as exc:
            log.exception("Timeline render failed")
            self.error.emit(str(exc))


class EffectRow(QFrame):
    def __init__(self, index: int, effect: Effect, language: str, on_edit, on_remove, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        label_key = "label_id" if language == "id" else "label_en"
        label = EFFECT_REGISTRY.get(effect.kind, {}).get(label_key, effect.kind)
        layout.addWidget(QLabel(label), 1)
        edit_btn = QPushButton("✎")
        edit_btn.setFixedWidth(28)
        edit_btn.clicked.connect(lambda: on_edit(index))
        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setProperty("class", "danger")
        remove_btn.clicked.connect(lambda: on_remove(index))
        layout.addWidget(edit_btn)
        layout.addWidget(remove_btn)


class VideoEditorPage(QWidget):
    def __init__(self, language: str, settings, parent=None):
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self.current_project_id: str | None = None
        self.controller = TimelineController()
        self._render_worker: RenderWorker | None = None
        self._build_ui()
        self._refresh_effects_panel()

    # ---- project lifecycle -------------------------------------------------
    def set_project(self, project_id: str):
        if self.current_project_id == project_id:
            self._reload_clip_bin()  # still pick up newly downloaded/generated clips
            return
        self.current_project_id = project_id
        self._load_timeline_for_project()
        self._reload_clip_bin()

    def refresh(self):
        """Called by MainWindow._navigate() whenever this page becomes
        visible, so newly imported videos / AI-generated clips show up
        in the clip bin without needing a fresh project selection."""
        self._reload_clip_bin()

    def _load_timeline_for_project(self):
        with get_session() as session:
            saved = (session.query(EditorTimeline).filter_by(project_id=self.current_project_id)
                      .order_by(EditorTimeline.updated_at.desc()).first())
            if saved:
                project = TimelineProject.from_dict(json.loads(saved.data_json))
                self.controller = TimelineController(project)
            else:
                self.controller = TimelineController()
        self._rewire_timeline_view()

    def _rewire_timeline_view(self):
        # Rebuilding the TimelineView is simpler and safer than trying to
        # hot-swap the controller inside an existing scene/view.
        old = self.timeline_view
        new_view = TimelineView(self.controller, language=self.language)
        new_view.message.connect(self._show_message)
        new_view.timeline_scene.clip_selected.connect(self._on_clip_selected)
        new_view.timeline_scene.playhead_moved.connect(self.preview.seek_to)
        new_view.timeline_scene.playhead_moved.connect(self._on_playhead_moved_for_preview)
        new_view.project_changed.connect(self._refresh_effects_panel)
        layout = old.parent().layout()
        idx = layout.indexOf(old)
        layout.removeWidget(old)
        old.deleteLater()
        layout.insertWidget(idx, new_view, 1)
        self.timeline_view = new_view
        self._refresh_effects_panel()

    # ---- UI construction -----------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(t("nav.video_editor", self.language))
        title.setProperty("role", "title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        save_btn = QPushButton("💾 Simpan" if self.language == "id" else "💾 Save")
        save_btn.setProperty("class", "secondary")
        save_btn.clicked.connect(self._save_timeline)
        title_row.addWidget(save_btn)
        render_btn = QPushButton("🎬 Render / Export")
        render_btn.setProperty("class", "primary")
        render_btn.clicked.connect(self._start_render)
        self.render_btn = render_btn
        title_row.addWidget(render_btn)
        root.addLayout(title_row)

        root.addLayout(self._build_toolbar())

        self.message_label = QLabel("")
        self.message_label.setProperty("role", "muted")
        root.addWidget(self.message_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        # Clip bin (top) + preview/effects (middle) + timeline (bottom).
        self.clip_bin = QListWidget()
        self.clip_bin.setMaximumHeight(90)
        self.clip_bin.setFlow(QListWidget.LeftToRight)
        self.clip_bin.setWrapping(False)
        self.clip_bin.setFixedHeight(90)
        bin_row = QHBoxLayout()
        bin_row.addWidget(self.clip_bin, 1)
        bin_buttons = QVBoxLayout()
        add_video_btn = QPushButton("+ Video1" if self.language != "id" else "+ ke Video 1")
        add_video_btn.setProperty("class", "secondary")
        add_video_btn.clicked.connect(lambda: self._add_selected_clip_to_timeline(0))
        add_video2_btn = QPushButton("+ Video 2 (PIP)")
        add_video2_btn.setProperty("class", "secondary")
        add_video2_btn.clicked.connect(lambda: self._add_selected_clip_to_timeline(1))
        text_btn = QPushButton("+ Teks" if self.language == "id" else "+ Text")
        text_btn.setProperty("class", "secondary")
        text_btn.clicked.connect(self._add_text_overlay)
        image_btn = QPushButton("+ Logo/Gambar" if self.language == "id" else "+ Logo/Image")
        image_btn.setProperty("class", "secondary")
        image_btn.clicked.connect(self._add_image_overlay)
        for b in (add_video_btn, add_video2_btn, text_btn, image_btn):
            bin_buttons.addWidget(b)
        bin_row.addLayout(bin_buttons)
        root.addLayout(bin_row)

        splitter = QSplitter(Qt.Horizontal)
        self.preview = VideoPreviewPanel(lambda: self.controller.project)
        splitter.addWidget(self.preview)

        self.effects_panel_host = QWidget()
        self.effects_panel_layout = QVBoxLayout(self.effects_panel_host)
        self.effects_title = QLabel("Tidak ada klip dipilih" if self.language == "id" else "No clip selected")
        self.effects_title.setStyleSheet("font-weight: 600;")
        self.effects_panel_layout.addWidget(self.effects_title)

        self.effect_preview = EffectPreviewPanel(lambda: self.settings.temp_folder, language=self.language)
        self.effects_panel_layout.addWidget(self.effect_preview)

        self._pip_updating = False
        self.pip_panel = QFrame()
        self.pip_panel.setProperty("class", "card")
        pip_layout = QVBoxLayout(self.pip_panel)
        pip_layout.addWidget(QLabel("Picture-in-Picture"))

        pip_row1 = QHBoxLayout()
        pip_row1.addWidget(QLabel("Ukuran:" if self.language == "id" else "Size:"))
        self.pip_scale_spin = QSpinBox()
        self.pip_scale_spin.setRange(10, 100)
        self.pip_scale_spin.setSuffix("%")
        self.pip_scale_spin.valueChanged.connect(self._on_pip_changed)
        pip_row1.addWidget(self.pip_scale_spin)
        pip_layout.addLayout(pip_row1)

        pip_row2 = QHBoxLayout()
        pip_row2.addWidget(QLabel("Posisi:" if self.language == "id" else "Position:"))
        self.pip_position_combo = QComboBox()
        labels = PIP_POSITION_LABELS.get(self.language, PIP_POSITION_LABELS["en"])
        for key in ("top_left", "top_right", "bottom_left", "bottom_right", "center"):
            self.pip_position_combo.addItem(labels[key], key)
        self.pip_position_combo.currentIndexChanged.connect(self._on_pip_changed)
        pip_row2.addWidget(self.pip_position_combo, 1)
        pip_layout.addLayout(pip_row2)

        self.pip_border_check = QCheckBox("Border putih" if self.language == "id" else "White border")
        self.pip_border_check.toggled.connect(self._on_pip_changed)
        pip_layout.addWidget(self.pip_border_check)

        pip_hint = QLabel(
            "Set ukuran ke 100% untuk mode cutaway penuh layar (perilaku Fase 2)."
            if self.language == "id" else
            "Set size to 100% for a full-frame cutaway (Phase 2 behavior).")
        pip_hint.setProperty("role", "muted")
        pip_hint.setWordWrap(True)
        pip_hint.setStyleSheet("font-size: 10px;")
        pip_layout.addWidget(pip_hint)

        self.pip_panel.setVisible(False)
        self.effects_panel_layout.addWidget(self.pip_panel)

        add_fx_row = QHBoxLayout()
        self.fx_combo = QComboBox()
        for key, spec in EFFECT_REGISTRY.items():
            self.fx_combo.addItem(spec.get("label_id" if self.language == "id" else "label_en", key), key)
        add_fx_btn = QPushButton("+ Efek" if self.language == "id" else "+ Effect")
        add_fx_btn.setProperty("class", "secondary")
        add_fx_btn.clicked.connect(self._add_effect_to_selected)
        add_fx_row.addWidget(self.fx_combo, 1)
        add_fx_row.addWidget(add_fx_btn)
        self.effects_panel_layout.addLayout(add_fx_row)

        self.effects_list_host = QVBoxLayout()
        self.effects_panel_layout.addLayout(self.effects_list_host)
        self.effects_panel_layout.addStretch(1)
        splitter.addWidget(self.effects_panel_host)
        splitter.setSizes([500, 260])
        root.addWidget(splitter)

        # Placeholder TimelineView; real one is created in _rewire_timeline_view()
        # once we're inside a project (needs the widget hierarchy to already exist).
        timeline_row = QVBoxLayout()
        self.timeline_view = TimelineView(self.controller, language=self.language)
        self.timeline_view.message.connect(self._show_message)
        self.timeline_view.timeline_scene.clip_selected.connect(self._on_clip_selected)
        self.timeline_view.timeline_scene.playhead_moved.connect(self.preview.seek_to)
        self.timeline_view.timeline_scene.playhead_moved.connect(self._on_playhead_moved_for_preview)
        self.timeline_view.project_changed.connect(self._refresh_effects_panel)
        timeline_row.addWidget(self.timeline_view, 1)
        root.addLayout(timeline_row, 1)

        self.preview.playhead_changed.connect(self._on_preview_playhead_changed)

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        specs = [
            ("↶ Undo", self.timeline_view_undo), ("↷ Redo", self.timeline_view_redo),
            ("✂ Split (S)", lambda: self.timeline_view.split_at_playhead()),
            ("⛓ Merge", lambda: self.timeline_view.merge_selected_with_next()),
            ("🗑 Delete", lambda: self.timeline_view.delete_selected(ripple=False)),
            ("🗑 Ripple Delete", lambda: self.timeline_view.delete_selected(ripple=True)),
            ("⎘ Copy", lambda: self.timeline_view.copy_selected()),
            ("⎗ Paste", lambda: self.timeline_view.paste_at_playhead()),
            ("🚩 Marker (M)", lambda: self.timeline_view.add_marker_at_playhead()),
            ("🔍+", lambda: self.timeline_view.zoom_in()),
            ("🔍-", lambda: self.timeline_view.zoom_out()),
        ]
        for label, handler in specs:
            btn = QPushButton(label)
            btn.setProperty("class", "secondary")
            btn.clicked.connect(handler)
            row.addWidget(btn)
        row.addStretch(1)
        return row

    def timeline_view_undo(self):
        self.timeline_view.undo()

    def timeline_view_redo(self):
        self.timeline_view.redo()

    # ---- clip bin --------------------------------------------------------
    def _reload_clip_bin(self):
        self.clip_bin.clear()
        if not self.current_project_id:
            return
        with get_session() as session:
            videos = session.query(SourceVideo).filter_by(
                project_id=self.current_project_id, status="ready").all()
            for v in videos:
                if not v.local_path:
                    continue
                item = QListWidgetItem(f"🎞 {v.title or Path(v.local_path).name}\n{v.duration_sec:.0f}s (full video)")
                item.setData(Qt.UserRole, {"kind": "source_video", "path": v.local_path,
                                            "in": 0.0, "out": v.duration_sec, "label": v.title})
                self.clip_bin.addItem(item)

            clips = (session.query(ClipRecord).join(SourceVideo)
                      .filter(SourceVideo.project_id == self.current_project_id).all())
            for c in clips:
                video = session.get(SourceVideo, c.source_video_id)
                if not video or not video.local_path:
                    continue
                item = QListWidgetItem(f"✨ {c.transcript_text[:24] or 'Clip'}...\nscore {c.viral_score:.0f} · {c.duration:.0f}s")
                item.setData(Qt.UserRole, {"kind": "ai_clip", "path": video.local_path,
                                            "in": c.start_time, "out": c.end_time, "label": c.transcript_text[:30]})
                self.clip_bin.addItem(item)

    def _add_selected_clip_to_timeline(self, track_index: int):
        item = self.clip_bin.currentItem()
        if not item:
            self._show_message("Pilih klip dari clip bin dulu." if self.language == "id"
                                else "Select a clip from the clip bin first.")
            return
        data = item.data(Qt.UserRole)
        try:
            new_clip = self.controller.add_clip(
                track_index, data["path"], source_in=data["in"], source_out=data["out"],
                timeline_start=self.controller.project.total_duration(), label=data.get("label", ""),
            )
            if track_index == 1:
                # Default Video 2 clips to a real PIP box (bottom-right,
                # bordered) rather than the old full-frame cutaway; users
                # can still drag pip_scale back to 100% for a cutaway.
                self.controller.set_pip(new_clip.id, scale=0.32, x=1.0, y=1.0, border=True)
            self.timeline_view.timeline_scene.select_clip(new_clip.id)
        except TimelineConflictError:
            self._show_message("Tidak bisa menambah di posisi itu." if self.language == "id"
                                else "Can't add a clip there.")
        self.timeline_view.timeline_scene.rebuild()

    def _add_text_overlay(self):
        text, ok = QInputDialog.getText(self, "Teks Overlay" if self.language == "id" else "Text Overlay",
                                          "Isi teks:" if self.language == "id" else "Caption text:")
        if not ok or not text.strip():
            return
        try:
            clip = self.controller.add_clip(2, "", source_in=0, source_out=3,
                                              timeline_start=self.controller.playhead, kind="text", text=text.strip())
            self.timeline_view.timeline_scene.select_clip(clip.id)
        except TimelineConflictError:
            self._show_message("Tidak ada ruang di track Overlay pada posisi playhead."
                                if self.language == "id" else "No room on the Overlay track at the playhead.")
        self.timeline_view.timeline_scene.rebuild()

    def _add_image_overlay(self):
        path, _ = QFileDialog.getOpenFileName(self, "Pilih Logo/Gambar" if self.language == "id" else "Choose Logo/Image",
                                                "", "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            clip = self.controller.add_clip(2, path, source_in=0, source_out=5,
                                              timeline_start=self.controller.playhead, kind="image",
                                              label=Path(path).name)
            self.timeline_view.timeline_scene.select_clip(clip.id)
        except TimelineConflictError:
            self._show_message("Tidak ada ruang di track Overlay pada posisi playhead."
                                if self.language == "id" else "No room on the Overlay track at the playhead.")
        self.timeline_view.timeline_scene.rebuild()

    # ---- effects panel -----------------------------------------------------
    def _on_clip_selected(self, clip_id: str):
        self._refresh_effects_panel()

    def _refresh_effects_panel(self):
        while self.effects_list_host.count():
            item = self.effects_list_host.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        clip_id = self.timeline_view.timeline_scene.selected_clip_id
        track, clip = self.controller.project.find(clip_id) if clip_id else (None, None)
        if not clip:
            self.effects_title.setText("Tidak ada klip dipilih" if self.language == "id" else "No clip selected")
            self.pip_panel.setVisible(False)
            self.effect_preview.show_clip(None)
            return

        name = clip.label or clip.text or (Path(clip.source_path).name if clip.source_path else clip.kind)
        self.effects_title.setText(f"{name} ({clip.timeline_duration:.1f}s)")

        playhead = self.controller.playhead
        at_time = (playhead - clip.timeline_start
                   if clip.timeline_start <= playhead < clip.timeline_end else None)
        self.effect_preview.show_clip(clip, at_time_in_clip=at_time)

        is_pip_eligible = clip.kind == "video" and track is not None and track.index != 0
        self.pip_panel.setVisible(is_pip_eligible)
        if is_pip_eligible:
            self._pip_updating = True
            self.pip_scale_spin.setValue(int(round(clip.pip_scale * 100)))
            key = _closest_pip_position_key(clip.pip_x, clip.pip_y)
            idx = self.pip_position_combo.findData(key)
            self.pip_position_combo.setCurrentIndex(idx if idx >= 0 else 3)
            self.pip_border_check.setChecked(clip.pip_border)
            self._pip_updating = False

        for i, effect in enumerate(clip.effects):
            row = EffectRow(i, effect, self.language, self._edit_effect, self._remove_effect)
            self.effects_list_host.addWidget(row)

    def _on_pip_changed(self, *_args):
        if self._pip_updating:
            return
        clip_id = self.timeline_view.timeline_scene.selected_clip_id
        if not clip_id:
            return
        x, y = PIP_POSITIONS[self.pip_position_combo.currentData()]
        self.controller.set_pip(clip_id, scale=self.pip_scale_spin.value() / 100.0, x=x, y=y,
                                  border=self.pip_border_check.isChecked())
        self.timeline_view.timeline_scene.rebuild()

    def _add_effect_to_selected(self):
        clip_id = self.timeline_view.timeline_scene.selected_clip_id
        if not clip_id:
            self._show_message("Pilih klip dulu." if self.language == "id" else "Select a clip first.")
            return
        kind = self.fx_combo.currentData()
        dialog = EffectParamDialog(kind, None, self.language, self)
        if dialog.exec():
            self.controller.add_effect(clip_id, Effect(kind=kind, params=dialog.get_params()))
            self.timeline_view.timeline_scene.rebuild()
            self._refresh_effects_panel()

    def _edit_effect(self, index: int):
        clip_id = self.timeline_view.timeline_scene.selected_clip_id
        _, clip = self.controller.project.find(clip_id) if clip_id else (None, None)
        if not clip or not (0 <= index < len(clip.effects)):
            return
        effect = clip.effects[index]
        dialog = EffectParamDialog(effect.kind, effect.params, self.language, self)
        if dialog.exec():
            self.controller.remove_effect(clip_id, index)
            self.controller.add_effect(clip_id, Effect(kind=effect.kind, params=dialog.get_params()))
            self.timeline_view.timeline_scene.rebuild()
            self._refresh_effects_panel()

    def _remove_effect(self, index: int):
        clip_id = self.timeline_view.timeline_scene.selected_clip_id
        if not clip_id:
            return
        self.controller.remove_effect(clip_id, index)
        self.timeline_view.timeline_scene.rebuild()
        self._refresh_effects_panel()

    def _on_preview_playhead_changed(self, time_sec: float):
        self.timeline_view.timeline_scene.set_playhead(time_sec)

    def _on_playhead_moved_for_preview(self, time_sec: float):
        """Keeps the effect preview following the playhead while scrubbing
        inside the currently selected clip -- e.g. checking how a face-
        tracking reframe looks at different points, not just the midpoint.
        No-op (debounced away) if nothing is selected or the playhead is
        outside the selected clip."""
        clip_id = self.timeline_view.timeline_scene.selected_clip_id
        if not clip_id:
            return
        _, clip = self.controller.project.find(clip_id)
        if not clip or not (clip.timeline_start <= time_sec < clip.timeline_end):
            return
        self.effect_preview.show_clip(clip, at_time_in_clip=time_sec - clip.timeline_start)

    # ---- save / render -----------------------------------------------------
    def _save_timeline(self):
        if not self.current_project_id:
            return
        with get_session() as session:
            existing = session.query(EditorTimeline).filter_by(project_id=self.current_project_id).first()
            payload = json.dumps(self.controller.project.to_dict())
            if existing:
                existing.data_json = payload
            else:
                session.add(EditorTimeline(project_id=self.current_project_id,
                                             name=self.controller.project.name, data_json=payload))
            session.add(HistoryEntry(entry_type="project", reference_id=self.current_project_id,
                                       description="Timeline video editor disimpan"))
            session.commit()
        self._show_message("Timeline disimpan ✓" if self.language == "id" else "Timeline saved ✓")

    def _start_render(self):
        if not self.controller.project.track_by_index(0) or not self.controller.project.track_by_index(0).clips:
            self._show_message("Tambahkan klip ke Video 1 dulu sebelum render."
                                if self.language == "id" else "Add at least one clip to Video 1 before rendering.")
            return

        output_dir = Path(self.settings.output_folder) / "timeline_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"timeline_{self.current_project_id[:8] if self.current_project_id else 'x'}.mp4")

        render_settings = {"resolution": "1080p", "fps": None, "codec": "h264",
                            "bitrate": "auto", "hw_accel": "auto"}

        self.render_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self._render_worker = RenderWorker(self.controller.project, self.settings.temp_folder,
                                             output_path, render_settings)
        self._render_worker.stage.connect(self._on_render_stage)
        self._render_worker.done.connect(self._on_render_done)
        self._render_worker.error.connect(self._on_render_error)
        self._render_worker.start()

    def _on_render_stage(self, stage: str, pct: float):
        label = STAGE_LABELS.get(self.language, STAGE_LABELS["en"]).get(stage, stage)
        self._show_message(label)
        self.progress_bar.setValue(int(pct))

    def _on_render_done(self, output_path: str):
        self.render_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        with get_session() as session:
            session.add(HistoryEntry(entry_type="export", description=f"Render timeline: {Path(output_path).name}"))
            session.commit()
        self._show_message(f"Render selesai ✓ {output_path}" if self.language == "id" else f"Render complete ✓ {output_path}")
        QMessageBox.information(self, "AI Klipers",
                                  f"Render selesai:\n{output_path}" if self.language == "id"
                                  else f"Render complete:\n{output_path}")

    def _on_render_error(self, message: str):
        self.render_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "AI Klipers", f"Render gagal:\n{message}"
                              if self.language == "id" else f"Render failed:\n{message}")

    def _show_message(self, text: str):
        self.message_label.setText(text)
