"""Tests for app/widgets/effect_preview.py (on-demand effect preview) and
its integration into the Video Editor page. Includes a regression test
for a real QThread lifetime bug found during development: overwriting
self._worker on every render request could orphan a still-running
previous render, causing an intermittent "QThread: Destroyed while
thread is still running" crash under rapid/overlapping requests.
Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_effect_preview.py -v
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

from app.timeline.model import TimelineClip, Effect

SOURCE = "/tmp/test_assets/clip_b.mp4"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run_until(condition, qapp, timeout_ms=15000):
    loop = QEventLoop()

    def check():
        qapp.processEvents()
        if condition():
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(30)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()


@pytest.fixture
def panel(qapp, tmp_path):
    from app.widgets.effect_preview import EffectPreviewPanel
    p = EffectPreviewPanel(lambda: str(tmp_path), language="id")
    p.resize(320, 220)
    p.show()
    return p


def test_no_clip_shows_placeholder_and_does_not_render(panel, qapp):
    panel.show_clip(None)
    qapp.processEvents()
    assert panel.image_label.pixmap().isNull()
    assert len(panel._active_workers) == 0


def test_clip_without_effects_skips_ffmpeg_entirely(panel, qapp):
    clip = TimelineClip(source_path=SOURCE, source_in=0, source_out=6, timeline_start=0)
    panel.show_clip(clip)
    _run_until(lambda: panel._debounce_timer.isActive() is False, qapp, timeout_ms=2000)
    assert len(panel._active_workers) == 0  # never started a worker -- no effects to preview


def test_clip_with_effect_renders_a_real_frame(panel, qapp):
    clip = TimelineClip(source_path=SOURCE, source_in=0, source_out=6, timeline_start=0)
    clip.effects.append(Effect("vignette", {"strength": 0.6}))
    panel.show_clip(clip)

    _run_until(lambda: not panel.image_label.pixmap().isNull(), qapp)
    assert not panel.image_label.pixmap().isNull()
    assert len(panel._active_workers) == 0  # cleaned up after finishing


def test_stale_result_does_not_override_newer_one(panel, qapp):
    """If an older request somehow finishes after a newer one (shouldn't
    normally happen with debounce, but the token guard exists precisely
    for when it does), the newer result must win."""
    clip = TimelineClip(source_path=SOURCE, source_in=0, source_out=6, timeline_start=0)
    clip.effects.append(Effect("vignette", {"strength": 0.5}))

    panel._latest_applied_token = 5
    panel._on_render_done("/nonexistent/stale.png", token=2)  # older token -> must be ignored
    assert panel._latest_applied_token == 5  # unchanged


def test_rapid_overlapping_requests_do_not_crash(panel, qapp):
    """Regression test for the QThread lifetime bug: firing many
    overlapping preview requests (bypassing debounce via _force_refresh)
    must not crash, and every worker must eventually clean itself up."""
    clip = TimelineClip(source_path=SOURCE, source_in=0, source_out=6, timeline_start=0)
    clip.effects.append(Effect("color", {"brightness": 0.1, "contrast": 1.2, "saturation": 1.3}))

    for i in range(6):
        panel.show_clip(clip, at_time_in_clip=(i % 3) * 1.0)
        panel._force_refresh()
        qapp.processEvents()

    _run_until(lambda: len(panel._active_workers) == 0, qapp, timeout_ms=20000)
    assert len(panel._active_workers) == 0
    assert not panel.image_label.pixmap().isNull()


def test_non_video_clip_shows_placeholder(panel, qapp):
    text_clip = TimelineClip(source_path="", source_in=0, source_out=3, timeline_start=0,
                              kind="text", text="hello")
    panel.show_clip(text_clip)
    qapp.processEvents()
    assert panel.image_label.pixmap().isNull()


class TestVideoEditorIntegration:
    @pytest.fixture
    def editor(self, qapp, tmp_path):
        from config.settings import Settings
        from database.db import init_db, get_session
        from database.models import Project, SourceVideo
        from app.pages.video_editor import VideoEditorPage

        init_db(f"sqlite:///{tmp_path}/effect_preview_editor.db")
        settings = Settings.load()
        settings.output_folder = str(tmp_path / "out")
        settings.temp_folder = str(tmp_path / "temp")

        with get_session() as session:
            project = Project(name="Effect Preview Integration")
            session.add(project)
            session.commit()
            session.refresh(project)
            video = SourceVideo(project_id=project.id, source_type="local", local_path=SOURCE,
                                 title="Clip B", status="ready", duration_sec=8.0)
            session.add(video)
            session.commit()

        page = VideoEditorPage("id", settings)
        page.resize(1200, 800)
        page.show()
        page.set_project(project.id)
        qapp.processEvents()
        return page

    def test_selecting_clip_and_adding_effect_updates_preview(self, editor, qapp):
        editor.clip_bin.setCurrentRow(0)
        editor._add_selected_clip_to_timeline(0)
        qapp.processEvents()
        clip = editor.controller.project.track_by_index(0).clips[0]
        editor.timeline_view.timeline_scene.select_clip(clip.id)
        editor._refresh_effects_panel()
        qapp.processEvents()

        editor.controller.add_effect(clip.id, Effect("color", {"brightness": 0.2, "contrast": 1.3, "saturation": 1.4}))
        editor.timeline_view.timeline_scene.rebuild()
        qapp.processEvents()

        _run_until(lambda: not editor.effect_preview.image_label.pixmap().isNull(), qapp)
        assert not editor.effect_preview.image_label.pixmap().isNull()

    def test_scrubbing_within_selected_clip_updates_preview_time(self, editor, qapp):
        editor.clip_bin.setCurrentRow(0)
        editor._add_selected_clip_to_timeline(0)
        qapp.processEvents()
        clip = editor.controller.project.track_by_index(0).clips[0]
        editor.timeline_view.timeline_scene.select_clip(clip.id)
        editor._refresh_effects_panel()

        editor._on_playhead_moved_for_preview(2.5)
        qapp.processEvents()
        assert abs(editor.effect_preview._pending_time - 2.5) < 0.01

    def test_scrubbing_outside_selected_clip_is_ignored(self, editor, qapp):
        editor.clip_bin.setCurrentRow(0)
        editor._add_selected_clip_to_timeline(0)
        qapp.processEvents()
        clip = editor.controller.project.track_by_index(0).clips[0]
        editor.timeline_view.timeline_scene.select_clip(clip.id)
        editor._refresh_effects_panel()
        before = editor.effect_preview._pending_time

        editor._on_playhead_moved_for_preview(999.0)  # far outside the clip
        qapp.processEvents()
        assert editor.effect_preview._pending_time == before
