"""QGraphicsScene rendering of a TimelineProject: ruler, track lanes,
draggable/trimmable clip blocks, a scrubbable playhead, and markers.

This is the only file in app/timeline/ that imports Qt -- model.py stays
framework-free. Every mutation goes through self.controller (see
app/timeline/model.py's TimelineController), so undo/redo and conflict
validation are never duplicated here; a rejected drag/trim just calls
rebuild(), which redraws from the controller's still-unchanged state.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QGraphicsScene, QGraphicsRectItem, QGraphicsLineItem,
    QGraphicsItem, QGraphicsPolygonItem, QGraphicsSimpleTextItem,
)
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QPolygonF, QTransform
from PySide6.QtCore import Qt, QPointF, Signal

from app.timeline.model import TimelineController, TimelineClip, TimelineConflictError

TRACK_HEIGHT = 60
RULER_HEIGHT = 26
TRIM_HANDLE_PX = 8
SNAP_PIXEL_THRESHOLD = 10
MIN_CLIP_SECONDS = 0.2

KIND_COLORS = {"video": "#6E56F8", "text": "#3DDC97", "image": "#F5B759"}
TRACK_BG_COLORS = {"video": "#20222E", "overlay": "#232030"}


def _nice_tick_interval(pixels_per_second: float) -> float:
    """Pick a 'nice' ruler tick spacing (1/2/5/10/30/60s...) so labels
    don't crowd together when zoomed out or thin out to nothing when
    zoomed in."""
    target_px = 70
    raw = target_px / max(pixels_per_second, 0.01)
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
        if raw <= step:
            return step
    return 600


class ClipItem(QGraphicsRectItem):
    def __init__(self, clip: TimelineClip, scene_ref: "TimelineScene"):
        super().__init__()
        self.clip_id = clip.id
        self.scene_ref = scene_ref
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)

        self._drag_mode: str | None = None
        self._press_scene_x = 0.0
        self._orig_clip_start = 0.0
        self._orig_source_in = 0.0
        self._orig_source_out = 0.0
        self._orig_track_index = 0
        self._pending_track_index = 0
        self._pending_timeline_start = 0.0
        self._pending_source_in = 0.0
        self._pending_source_out = 0.0

        self.label = QGraphicsSimpleTextItem(self)
        self.label.setBrush(QBrush(QColor("white")))
        self.label.setFont(QFont("Segoe UI", 8))

        self.update_from_clip(clip)

    def _clip(self) -> TimelineClip | None:
        _, clip = self.scene_ref.controller.project.find(self.clip_id)
        return clip

    def update_from_clip(self, clip: TimelineClip):
        pps = self.scene_ref.pixels_per_second
        track, _ = self.scene_ref.controller.project.find(clip.id)
        w = max(clip.timeline_duration * pps, 4)
        self.setRect(0, 0, w, TRACK_HEIGHT - 6)
        self.setPos(clip.timeline_start * pps, self.scene_ref.track_y(track.index if track else 0))

        color = QColor(KIND_COLORS.get(clip.kind, "#6E56F8"))
        selected = self.clip_id == self.scene_ref.selected_clip_id
        self.setBrush(QBrush(color))
        self.setPen(QPen(QColor("#FF6B5B" if selected else "#14151C"), 2 if selected else 1))

        if clip.kind == "text":
            name = clip.text or "(teks kosong)"
        elif clip.source_path:
            name = clip.label or Path(clip.source_path).name
        else:
            name = clip.label or clip.kind
        fx_suffix = f" [{len(clip.effects)} fx]" if clip.effects else ""
        self.label.setText(f"{name} · {clip.timeline_duration:.1f}s{fx_suffix}")
        self.label.setPos(6, 4)

    def hoverMoveEvent(self, event):
        x = event.pos().x()
        w = self.rect().width()
        if x <= TRIM_HANDLE_PX or x >= w - TRIM_HANDLE_PX:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        self.scene_ref.select_clip(self.clip_id)
        clip = self._clip()
        if not clip:
            return
        x = event.pos().x()
        w = self.rect().width()
        if x <= TRIM_HANDLE_PX:
            self._drag_mode = "trim_left"
        elif x >= w - TRIM_HANDLE_PX:
            self._drag_mode = "trim_right"
        else:
            self._drag_mode = "move"

        self._press_scene_x = event.scenePos().x()
        track, _ = self.scene_ref.controller.project.find(self.clip_id)
        self._orig_track_index = track.index if track else 0
        self._orig_clip_start = clip.timeline_start
        self._orig_source_in = clip.source_in
        self._orig_source_out = clip.source_out
        self._pending_track_index = self._orig_track_index
        self._pending_timeline_start = self._orig_clip_start
        self._pending_source_in = self._orig_source_in
        self._pending_source_out = self._orig_source_out
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            return
        pps = self.scene_ref.pixels_per_second
        delta_sec = (event.scenePos().x() - self._press_scene_x) / pps

        if self._drag_mode == "move":
            new_start = max(self._orig_clip_start + delta_sec, 0.0)
            track_index = self.scene_ref.track_index_at_y(event.scenePos().y())
            if track_index is None:
                track_index = self._orig_track_index
            self._pending_track_index = track_index
            self._pending_timeline_start = new_start
            self.setPos(new_start * pps, self.scene_ref.track_y(track_index))

        elif self._drag_mode == "trim_left":
            span = self._orig_source_out - self._orig_source_in
            d = max(min(delta_sec, span - MIN_CLIP_SECONDS), -self._orig_source_in)
            self._pending_source_in = self._orig_source_in + d
            self._pending_timeline_start = self._orig_clip_start + d
            new_w = max((self._orig_source_out - self._pending_source_in) * pps, 4)
            self.setPos(self._pending_timeline_start * pps, self.pos().y())
            self.setRect(0, 0, new_w, self.rect().height())

        elif self._drag_mode == "trim_right":
            span = self._orig_source_out - self._orig_source_in
            d = max(delta_sec, -(span - MIN_CLIP_SECONDS))
            self._pending_source_out = self._orig_source_out + d
            new_w = max((self._pending_source_out - self._orig_source_in) * pps, 4)
            self.setRect(0, 0, new_w, self.rect().height())

    def mouseReleaseEvent(self, event):
        mode, self._drag_mode = self._drag_mode, None
        ctl = self.scene_ref.controller
        try:
            if mode == "move":
                snapped = self.scene_ref.apply_snap(self._pending_timeline_start, self.clip_id)
                ctl.move_clip(self.clip_id, self._pending_track_index, snapped)
            elif mode == "trim_left":
                snapped = self.scene_ref.apply_snap(self._pending_timeline_start, self.clip_id)
                shift = snapped - self._pending_timeline_start
                ctl.trim_clip(self.clip_id, new_source_in=self._pending_source_in + shift,
                              new_timeline_start=snapped)
            elif mode == "trim_right":
                end_snapped = self.scene_ref.apply_snap(
                    self._orig_clip_start + (self._pending_source_out - self._orig_source_in), self.clip_id)
                new_source_out = self._orig_source_in + (end_snapped - self._orig_clip_start)
                ctl.trim_clip(self.clip_id, new_source_out=new_source_out)
        except TimelineConflictError:
            self.scene_ref.notify(
                "Tidak bisa: akan bertumpuk dengan klip lain di track ini."
                if self.scene_ref.language == "id" else "Can't do that: it would overlap another clip on this track.")
        if mode is not None:
            self.scene_ref.rebuild()


class TimelineScene(QGraphicsScene):
    clip_selected = Signal(str)     # "" means no selection
    playhead_moved = Signal(float)
    message = Signal(str)
    project_changed = Signal()

    def __init__(self, controller: TimelineController, language: str = "id", parent=None):
        super().__init__(parent)
        self.controller = controller
        self.language = language
        self.pixels_per_second = 40.0
        self.selected_clip_id: str | None = None
        self._clip_items: dict[str, ClipItem] = {}
        self._dragging_playhead = False
        self._playhead_item: QGraphicsLineItem | None = None
        self.rebuild()

    # ---- track <-> pixel geometry --------------------------------------
    def _track_order(self):
        """Higher track index renders nearer the top (matches 'higher
        track wins/overlays' semantics used by the renderer)."""
        return sorted(self.controller.project.tracks, key=lambda t: -t.index)

    def track_y(self, track_index: int) -> float:
        for i, t in enumerate(self._track_order()):
            if t.index == track_index:
                return RULER_HEIGHT + i * TRACK_HEIGHT + 3
        return RULER_HEIGHT

    def track_index_at_y(self, y: float):
        order = self._track_order()
        i = int((y - RULER_HEIGHT) // TRACK_HEIGHT)
        if 0 <= i < len(order):
            return order[i].index
        return None

    # ---- selection / notifications -------------------------------------
    def select_clip(self, clip_id: str | None):
        self.selected_clip_id = clip_id
        for cid, item in self._clip_items.items():
            selected = cid == clip_id
            item.setPen(QPen(QColor("#FF6B5B" if selected else "#14151C"), 2 if selected else 1))
        self.clip_selected.emit(clip_id or "")

    def notify(self, text: str):
        self.message.emit(text)

    # ---- snapping -------------------------------------------------------
    def apply_snap(self, time_sec: float, exclude_clip_id: str | None) -> float:
        threshold = SNAP_PIXEL_THRESHOLD / self.pixels_per_second
        candidates = {0.0, self.controller.playhead}
        for t in self.controller.project.tracks:
            for c in t.clips:
                if c.id != exclude_clip_id:
                    candidates.add(c.timeline_start)
                    candidates.add(c.timeline_end)
        for m in self.controller.project.markers:
            candidates.add(m.time)
        best = min(candidates, key=lambda c: abs(c - time_sec))
        return round(best if abs(best - time_sec) <= threshold else time_sec, 4)

    # ---- playhead ---------------------------------------------------------
    def set_playhead(self, time_sec: float):
        self.controller.playhead = max(time_sec, 0.0)
        self._position_playhead_item()
        self.playhead_moved.emit(self.controller.playhead)

    def _position_playhead_item(self):
        if self._playhead_item:
            x = self.controller.playhead * self.pixels_per_second
            y2 = self._playhead_item.line().y2()
            self._playhead_item.setLine(x, 0, x, y2)

    def set_zoom(self, pixels_per_second: float):
        self.pixels_per_second = max(min(pixels_per_second, 400.0), 5.0)
        self.rebuild()

    # ---- mouse handling for empty-space clicks (seek) -------------------
    def mousePressEvent(self, event):
        views = self.views()
        item = self.itemAt(event.scenePos(), views[0].transform() if views else QTransform())
        if not isinstance(item, ClipItem):
            self._dragging_playhead = True
            self.set_playhead(event.scenePos().x() / self.pixels_per_second)
            self.select_clip(None)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging_playhead:
            self.set_playhead(max(event.scenePos().x(), 0) / self.pixels_per_second)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging_playhead = False
        super().mouseReleaseEvent(event)

    # ---- (re)drawing ------------------------------------------------------
    def rebuild(self):
        self.clear()
        self._clip_items = {}
        self._playhead_item = None

        project = self.controller.project
        content_end = max(project.total_duration(), 8.0) + 6.0
        n_tracks = len(project.tracks)
        total_h = RULER_HEIGHT + n_tracks * TRACK_HEIGHT
        total_w = content_end * self.pixels_per_second

        self._draw_ruler(content_end, total_h)
        self._draw_track_lanes(total_w, n_tracks)

        for track in project.tracks:
            for clip in track.clips:
                item = ClipItem(clip, self)
                self.addItem(item)
                self._clip_items[clip.id] = item

        self._draw_markers(total_h)
        self._draw_playhead(total_h)
        self.setSceneRect(0, 0, total_w, total_h)
        self.project_changed.emit()

    def _draw_ruler(self, content_end: float, total_h: float):
        bg = QGraphicsRectItem(0, 0, content_end * self.pixels_per_second, RULER_HEIGHT)
        bg.setBrush(QBrush(QColor("#1C1E29")))
        bg.setPen(QPen(Qt.NoPen))
        bg.setZValue(-2)
        self.addItem(bg)

        step = _nice_tick_interval(self.pixels_per_second)
        t = 0.0
        while t <= content_end:
            x = t * self.pixels_per_second
            tick = QGraphicsLineItem(x, RULER_HEIGHT - 8, x, RULER_HEIGHT)
            tick.setPen(QPen(QColor("#5A5C6E"), 1))
            tick.setZValue(-1)
            self.addItem(tick)

            m, s = divmod(int(round(t)), 60)
            label = QGraphicsSimpleTextItem(f"{m}:{s:02d}")
            label.setBrush(QBrush(QColor("#8B8D9E")))
            label.setFont(QFont("Segoe UI", 7))
            label.setPos(x + 3, 2)
            label.setZValue(-1)
            self.addItem(label)
            t += step

    def _draw_track_lanes(self, total_w: float, n_tracks: int):
        for i, track in enumerate(self._track_order()):
            y = RULER_HEIGHT + i * TRACK_HEIGHT
            bg = QGraphicsRectItem(0, y, total_w, TRACK_HEIGHT)
            bg.setBrush(QBrush(QColor(TRACK_BG_COLORS.get(track.kind, "#20222E"))))
            bg.setPen(QPen(QColor("#14151C"), 1))
            bg.setZValue(-2)
            self.addItem(bg)

            name = QGraphicsSimpleTextItem(track.name or f"Track {track.index}")
            name.setBrush(QBrush(QColor("#5A5C6E")))
            name.setFont(QFont("Segoe UI", 7))
            name.setPos(4, y + TRACK_HEIGHT - 14)
            name.setZValue(-1)
            self.addItem(name)

    def _draw_markers(self, total_h: float):
        for marker in self.controller.project.markers:
            x = marker.time * self.pixels_per_second
            triangle = QGraphicsPolygonItem(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 8)]))
            triangle.setBrush(QBrush(QColor("#F5B759")))
            triangle.setPen(QPen(Qt.NoPen))
            triangle.setZValue(2)
            triangle.setToolTip(marker.label)
            self.addItem(triangle)

    def _draw_playhead(self, total_h: float):
        x = self.controller.playhead * self.pixels_per_second
        self._playhead_item = QGraphicsLineItem(x, 0, x, total_h)
        self._playhead_item.setPen(QPen(QColor("#FF6B5B"), 2))
        self._playhead_item.setZValue(3)
        self.addItem(self._playhead_item)
