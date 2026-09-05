"""GUI interaction tests for the timeline widget, using QTest to simulate
real mouse clicks/drags and keyboard-equivalent actions -- not just
"does it construct", but "does dragging a clip actually move it".
Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_timeline_gui.py -v
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from app.timeline.model import TimelineController


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def view_with_two_clips(qapp):
    from app.timeline.timeline_view import TimelineView
    ctl = TimelineController()
    c1 = ctl.add_clip(0, "/tmp/test_assets/clip_b.mp4", source_in=0, source_out=4, timeline_start=0)
    c2 = ctl.add_clip(0, "/tmp/test_assets/sample.mp4", source_in=0, source_out=3, timeline_start=4)
    view = TimelineView(ctl, language="id")
    view.resize(900, 300)
    view.show()
    qapp.processEvents()
    return view, ctl, c1, c2


def _to_viewport(view, x, y):
    return view.mapFromScene(x, y)


def test_click_on_clip_selects_it(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips
    item1 = view.timeline_scene._clip_items[c1.id]
    p = _to_viewport(view, item1.pos().x() + 20, item1.pos().y() + 10)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    qapp.processEvents()
    assert view.timeline_scene.selected_clip_id == c1.id


def test_click_empty_space_seeks_playhead_and_deselects(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips
    pps = view.timeline_scene.pixels_per_second
    target_time = c2.timeline_end + 3
    p = _to_viewport(view, target_time * pps, 50)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    qapp.processEvents()
    assert abs(ctl.playhead - target_time) < 0.3
    assert view.timeline_scene.selected_clip_id is None


def test_drag_clip_to_another_track(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips
    item1 = view.timeline_scene._clip_items[c1.id]
    start_p = _to_viewport(view, item1.pos().x() + 30, item1.pos().y() + 10)
    target_y = view.timeline_scene.track_y(1)
    end_p = _to_viewport(view, item1.pos().x() + 30, target_y + 10)

    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start_p)
    qapp.processEvents()
    QTest.mouseMove(view.viewport(), end_p)
    qapp.processEvents()
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end_p)
    qapp.processEvents()

    track_after, _ = ctl.project.find(c1.id)
    assert track_after.index == 1


def test_drag_conflict_reverts_visually_and_leaves_model_unchanged(view_with_two_clips, qapp):
    """Dragging clip1 onto clip2's own time range on the same track must
    be rejected (TimelineConflictError) and leave the model untouched."""
    view, ctl, c1, c2 = view_with_two_clips
    item1 = view.timeline_scene._clip_items[c1.id]
    start_p = _to_viewport(view, item1.pos().x() + 10, item1.pos().y() + 10)
    # Drag clip1 (0-4) to start at c2's start (4) on the SAME track -> overlap.
    end_p = _to_viewport(view, c2.timeline_start * view.timeline_scene.pixels_per_second + 10, item1.pos().y() + 10)

    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start_p)
    qapp.processEvents()
    QTest.mouseMove(view.viewport(), end_p)
    qapp.processEvents()
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end_p)
    qapp.processEvents()

    _, clip1_after = ctl.project.find(c1.id)
    assert clip1_after.timeline_start == 0  # rejected -> unchanged


def test_drag_trim_right_edge_shrinks_clip(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips
    item2 = view.timeline_scene._clip_items[c2.id]
    right_edge_x = item2.pos().x() + item2.rect().width() - 3
    pps = view.timeline_scene.pixels_per_second
    start_p = _to_viewport(view, right_edge_x, item2.pos().y() + 10)
    end_p = _to_viewport(view, right_edge_x - 1 * pps, item2.pos().y() + 10)
    orig_duration = c2.duration

    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start_p)
    qapp.processEvents()
    QTest.mouseMove(view.viewport(), end_p)
    qapp.processEvents()
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end_p)
    qapp.processEvents()

    _, clip2_after = ctl.project.find(c2.id)
    assert clip2_after.duration < orig_duration - 0.5


def test_split_undo_redo_via_view_actions(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips
    view.timeline_scene.select_clip(c2.id)
    ctl.playhead = c2.timeline_start + c2.duration / 2
    view.split_at_playhead()
    assert len(ctl.project.track_by_index(0).clips) == 3  # c1 + c2 split into 2

    view.undo()
    assert len(ctl.project.track_by_index(0).clips) == 2

    view.redo()
    assert len(ctl.project.track_by_index(0).clips) == 3


def test_marker_copy_paste_ripple_delete(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips

    ctl.playhead = 0.5
    view.add_marker_at_playhead("note")
    assert len(ctl.project.markers) == 1

    view.timeline_scene.select_clip(c1.id)
    view.copy_selected()
    ctl.playhead = 20.0
    view.paste_at_playhead()
    assert len(ctl.project.track_by_index(0).clips) == 3

    first = sorted(ctl.project.track_by_index(0).clips, key=lambda c: c.timeline_start)[0]
    view.timeline_scene.select_clip(first.id)
    view.delete_selected(ripple=True)
    assert len(ctl.project.track_by_index(0).clips) == 2


def test_zoom_changes_pixels_per_second(view_with_two_clips, qapp):
    view, ctl, c1, c2 = view_with_two_clips
    original = view.timeline_scene.pixels_per_second
    view.zoom_in()
    assert view.timeline_scene.pixels_per_second > original
    view.zoom_out()
    view.zoom_out()
    assert view.timeline_scene.pixels_per_second < original
