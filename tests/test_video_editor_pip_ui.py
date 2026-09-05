"""Regression tests for the Video Editor's Picture-in-Picture panel
(app/pages/video_editor.py). Specifically guards against a real bug found
during development: QComboBox.findData() is unreliable for tuple-valued
userData in PySide6, silently falling back to the wrong preset for every
position except whichever one happened to match the fallback constant.
The fix uses string keys instead -- these tests exercise every preset,
not just one, since that's exactly what let the bug slip through the
first version's (too-narrow) manual check.

Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_video_editor_pip_ui.py -v
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from config.settings import Settings
from database.db import init_db, get_session
from database.models import Project, SourceVideo

EXPECTED_POSITIONS = {
    "top_left": (0.0, 0.0), "top_right": (1.0, 0.0),
    "bottom_left": (0.0, 1.0), "bottom_right": (1.0, 1.0), "center": (0.5, 0.5),
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor_with_video2_clip(qapp, tmp_path):
    init_db(f"sqlite:///{tmp_path}/pip_ui_test.db")
    settings = Settings.load()
    settings.output_folder = str(tmp_path / "output")
    settings.temp_folder = str(tmp_path / "temp")

    from app.pages.video_editor import VideoEditorPage

    with get_session() as session:
        project = Project(name="PIP UI Test")
        session.add(project)
        session.commit()
        session.refresh(project)
        video = SourceVideo(project_id=project.id, source_type="local",
                             local_path="/tmp/test_assets/clip_b.mp4", title="Clip B",
                             status="ready", duration_sec=8.0)
        session.add(video)
        session.commit()

    editor = VideoEditorPage("id", settings)
    editor.resize(1000, 700)
    editor.show()  # isVisible() only reflects reality once the widget chain is actually shown
    editor.set_project(project.id)
    qapp.processEvents()

    editor.clip_bin.setCurrentRow(0)
    editor._add_selected_clip_to_timeline(1)  # -> Video 2, real PIP defaults applied
    qapp.processEvents()

    clip = editor.controller.project.track_by_index(1).clips[0]
    editor.timeline_view.timeline_scene.select_clip(clip.id)
    editor._refresh_effects_panel()
    qapp.processEvents()
    return editor, clip


def test_new_video2_clip_gets_real_pip_defaults(editor_with_video2_clip):
    _, clip = editor_with_video2_clip
    assert clip.pip_scale == pytest.approx(0.32)
    assert (clip.pip_x, clip.pip_y) == (1.0, 1.0)
    assert clip.pip_border is True


def test_pip_panel_only_visible_for_video2_clips(editor_with_video2_clip, qapp):
    editor, clip = editor_with_video2_clip
    assert editor.pip_panel.isVisible() is True

    editor.clip_bin.setCurrentRow(0)
    editor._add_selected_clip_to_timeline(0)  # Video 1 clip
    qapp.processEvents()
    v1_clip = editor.controller.project.track_by_index(0).clips[0]
    editor.timeline_view.timeline_scene.select_clip(v1_clip.id)
    editor._refresh_effects_panel()
    qapp.processEvents()
    assert editor.pip_panel.isVisible() is False


@pytest.mark.parametrize("index,key", list(enumerate(["top_left", "top_right", "bottom_left", "bottom_right", "center"])))
def test_every_position_preset_applies_correctly(editor_with_video2_clip, qapp, index, key):
    """The bug this guards against only affected *some* presets (whichever
    didn't match the hardcoded fallback), so every preset must be checked
    individually -- this is a parametrized test specifically because of
    that history."""
    editor, clip = editor_with_video2_clip
    editor.pip_position_combo.setCurrentIndex(index)
    qapp.processEvents()

    _, updated = editor.controller.project.find(clip.id)
    expected_x, expected_y = EXPECTED_POSITIONS[key]
    assert (updated.pip_x, updated.pip_y) == (expected_x, expected_y)


def test_scale_and_border_controls_apply(editor_with_video2_clip, qapp):
    editor, clip = editor_with_video2_clip
    editor.pip_scale_spin.setValue(60)
    editor.pip_border_check.setChecked(False)
    qapp.processEvents()

    _, updated = editor.controller.project.find(clip.id)
    assert updated.pip_scale == pytest.approx(0.6)
    assert updated.pip_border is False


def test_reselecting_clip_resyncs_panel_from_model(editor_with_video2_clip, qapp):
    editor, clip = editor_with_video2_clip
    editor.pip_position_combo.setCurrentIndex(2)  # bottom_left
    editor.pip_scale_spin.setValue(45)
    qapp.processEvents()

    editor.timeline_view.timeline_scene.select_clip(None)
    editor._refresh_effects_panel()
    editor.timeline_view.timeline_scene.select_clip(clip.id)
    editor._refresh_effects_panel()
    qapp.processEvents()

    assert editor.pip_position_combo.currentData() == "bottom_left"
    assert editor.pip_scale_spin.value() == 45
