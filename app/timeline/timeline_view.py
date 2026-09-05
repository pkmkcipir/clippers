"""QGraphicsView wrapper around TimelineScene: mouse-wheel zoom and every
keyboard shortcut a Premiere-style editor needs (Del, Shift+Del, Ctrl+Z/Y,
Ctrl+C/V, S to split, M to mark).
"""
from __future__ import annotations

from PySide6.QtWidgets import QGraphicsView
from PySide6.QtGui import QPainter
from PySide6.QtCore import Qt, Signal

from app.timeline.model import TimelineController, TimelineConflictError
from app.timeline.timeline_scene import TimelineScene


class TimelineView(QGraphicsView):
    project_changed = Signal()
    message = Signal(str)

    def __init__(self, controller: TimelineController, language: str = "id", parent=None):
        super().__init__(parent)
        self.controller = controller
        self.language = language
        self._clipboard = None

        self.timeline_scene = TimelineScene(controller, language=language)
        self.setScene(self.timeline_scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.StrongFocus)

        self.timeline_scene.message.connect(self.message.emit)
        self.timeline_scene.project_changed.connect(self.project_changed.emit)

    # ---- zoom -------------------------------------------------------------
    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else (1 / 1.15)
            self.timeline_scene.set_zoom(self.timeline_scene.pixels_per_second * factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self.timeline_scene.set_zoom(self.timeline_scene.pixels_per_second * 1.25)

    def zoom_out(self):
        self.timeline_scene.set_zoom(self.timeline_scene.pixels_per_second / 1.25)

    # ---- toolbar-equivalent actions (also reachable via shortcuts) -------
    def split_at_playhead(self):
        clip_id = self.timeline_scene.selected_clip_id
        if not clip_id:
            self.message.emit("Pilih klip dulu untuk di-split." if self.language == "id"
                               else "Select a clip first to split it.")
            return
        result = self.controller.split_clip(clip_id, self.controller.playhead)
        if result is None:
            self.message.emit("Playhead harus berada di dalam klip yang dipilih."
                               if self.language == "id" else "Playhead must be inside the selected clip.")
            return
        self.timeline_scene.rebuild()

    def delete_selected(self, ripple: bool = False):
        clip_id = self.timeline_scene.selected_clip_id
        if not clip_id:
            return
        self.controller.delete_clip(clip_id, ripple=ripple)
        self.timeline_scene.select_clip(None)
        self.timeline_scene.rebuild()

    def merge_selected_with_next(self):
        clip_id = self.timeline_scene.selected_clip_id
        if not clip_id:
            return
        track, clip = self.controller.project.find(clip_id)
        if not clip:
            return
        neighbors = sorted(track.clips, key=lambda c: c.timeline_start)
        idx = next((i for i, c in enumerate(neighbors) if c.id == clip_id), None)
        if idx is None or idx + 1 >= len(neighbors):
            self.message.emit("Tidak ada klip bersebelahan untuk digabung."
                               if self.language == "id" else "No adjacent clip to merge with.")
            return
        merged = self.controller.merge_clips(clip_id, neighbors[idx + 1].id)
        if merged is None:
            self.message.emit(
                "Hanya bisa merge klip bersebelahan dari sumber video yang sama (mis. bekas split)."
                if self.language == "id" else
                "Can only merge adjacent clips from the same source (e.g. undoing a split).")
            return
        self.timeline_scene.select_clip(merged.id)
        self.timeline_scene.rebuild()

    def add_marker_at_playhead(self, label: str = ""):
        self.controller.add_marker(self.controller.playhead, label=label)
        self.timeline_scene.rebuild()

    def copy_selected(self):
        clip_id = self.timeline_scene.selected_clip_id
        if clip_id:
            self._clipboard = self.controller.copy_clip(clip_id)

    def paste_at_playhead(self):
        if not self._clipboard:
            return
        track, _ = self.controller.project.find(self.timeline_scene.selected_clip_id) if \
            self.timeline_scene.selected_clip_id else (None, None)
        target_track = track.index if track else self._clipboard and 0
        try:
            pasted = self.controller.paste_clip(self._clipboard, target_track or 0, self.controller.playhead)
            self.timeline_scene.select_clip(pasted.id)
        except TimelineConflictError:
            self.message.emit("Tidak bisa paste di sini -- akan bertumpuk."
                               if self.language == "id" else "Can't paste here -- it would overlap.")
        self.timeline_scene.rebuild()

    def undo(self):
        if self.controller.undo():
            self.timeline_scene.select_clip(None)
            self.timeline_scene.rebuild()

    def redo(self):
        if self.controller.redo():
            self.timeline_scene.select_clip(None)
            self.timeline_scene.rebuild()

    # ---- keyboard shortcuts -----------------------------------------------
    def keyPressEvent(self, event):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        if ctrl and key == Qt.Key_Z:
            self.undo()
        elif ctrl and (key == Qt.Key_Y or (key == Qt.Key_Z and shift)):
            self.redo()
        elif ctrl and key == Qt.Key_C:
            self.copy_selected()
        elif ctrl and key == Qt.Key_V:
            self.paste_at_playhead()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected(ripple=shift)
        elif key == Qt.Key_S:
            self.split_at_playhead()
        elif key == Qt.Key_M:
            self.add_marker_at_playhead()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif key == Qt.Key_Minus:
            self.zoom_out()
        else:
            super().keyPressEvent(event)
