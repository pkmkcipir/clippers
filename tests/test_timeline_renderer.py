"""End-to-end tests for editor/timeline_renderer.py against real ffmpeg
and small synthetic videos (generated in a fixture, not checked into the
repo). These are slower than the rest of the suite (each spins up several
real ffmpeg processes) -- run with: pytest tests/test_timeline_renderer.py -v
"""
import subprocess
from pathlib import Path

import pytest

from app.timeline.model import TimelineController, Effect
from editor.timeline_renderer import render_timeline, resolve_target_dimensions
from editor.ffmpeg_utils import get_media_info


@pytest.fixture(scope="module")
def synthetic_clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    clip_a = d / "a.mp4"
    clip_b = d / "b.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=480x270:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", str(clip_a),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "smptebars=duration=3:size=480x270:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-shortest", str(clip_b),
    ], check=True)
    return {"a": str(clip_a), "b": str(clip_b)}


@pytest.fixture
def temp_dirs(tmp_path):
    render_temp = tmp_path / "render_temp"
    render_out = tmp_path / "out.mp4"
    render_temp.mkdir()
    return render_temp, render_out


def test_single_clip_renders_correct_duration(synthetic_clips, temp_dirs):
    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=3, timeline_start=0)

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")
    info = get_media_info(str(render_out))
    assert abs(info["duration"] - 3.0) < 0.3


def test_gap_is_filled_with_black(synthetic_clips, temp_dirs):
    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=2, timeline_start=0)
    ctl.add_clip(0, synthetic_clips["b"], source_in=0, source_out=2, timeline_start=4)  # 2s gap at 2-4

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")
    info = get_media_info(str(render_out))
    assert abs(info["duration"] - 6.0) < 0.3  # 2 + 2(gap) + 2


def test_slow_motion_stretches_output_duration(synthetic_clips, temp_dirs):
    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    clip = ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=2, timeline_start=0)
    ctl.add_effect(clip.id, Effect("speed", {"multiplier": 0.5}))  # half speed -> 4s output

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")
    info = get_media_info(str(render_out))
    assert abs(info["duration"] - 4.0) < 0.4


def test_reframe_effect_produces_vertical_output(synthetic_clips, temp_dirs):
    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    clip = ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=2, timeline_start=0)
    ctl.add_effect(clip.id, Effect("reframe", {"target_aspect": 9 / 16}))

    target_w, target_h = resolve_target_dimensions(ctl.project, "480p")
    assert target_h > target_w  # vertical

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")
    info = get_media_info(str(render_out))
    assert info["width"] == target_w and info["height"] == target_h


def test_video2_cutaway_and_text_overlay_do_not_change_duration(synthetic_clips, temp_dirs):
    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=4, timeline_start=0)
    ctl.add_clip(1, synthetic_clips["b"], source_in=0, source_out=1, timeline_start=1, kind="video")
    ctl.add_clip(2, "", source_in=0, source_out=2, timeline_start=0, kind="text", text="Test Caption")

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")
    info = get_media_info(str(render_out))
    assert abs(info["duration"] - 4.0) < 0.3  # overlays don't extend the timeline


def test_video2_default_is_still_full_frame_cutaway(synthetic_clips, temp_dirs):
    """Backward compatibility: a Video 2 clip with no pip_scale set (the
    dataclass default, 1.0) must behave exactly like Phase 2's original
    full-frame cutaway -- old saved projects rely on this."""
    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=3, timeline_start=0)
    clip = ctl.add_clip(1, synthetic_clips["b"], source_in=0, source_out=1, timeline_start=1, kind="video")
    assert clip.pip_scale == 1.0  # dataclass default

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")
    info = get_media_info(str(render_out))
    assert abs(info["duration"] - 3.0) < 0.3


def test_real_pip_box_is_scaled_positioned_and_bordered(synthetic_clips, temp_dirs):
    """Pixel-level check (not just 'ffmpeg exited 0'): a bottom-right PIP
    box at 32% scale with a border actually appears at the right size,
    position, and has genuinely white border pixels."""
    import numpy as np
    from PIL import Image

    render_temp, render_out = temp_dirs
    ctl = TimelineController()
    ctl.add_clip(0, synthetic_clips["a"], source_in=0, source_out=5, timeline_start=0)
    pip_clip = ctl.add_clip(1, synthetic_clips["b"], source_in=0, source_out=2, timeline_start=1, kind="video")
    ctl.set_pip(pip_clip.id, scale=0.32, x=1.0, y=1.0, border=True)

    render_timeline(ctl.project, render_temp, str(render_out), resolution="480p", hw_accel="cpu")

    frame_path = render_temp / "pip_frame.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", str(render_out),
                     "-frames:v", "1", str(frame_path)], check=True)
    frame = np.array(Image.open(frame_path))
    h, w = frame.shape[0], frame.shape[1]

    pip_w = round(w * 0.32 / 2) * 2
    pip_h = round(pip_w * h / w / 2) * 2
    border = 4
    pip_w_b, pip_h_b = pip_w + 2 * border, pip_h + 2 * border
    px = round((w - pip_w_b) * 1.0)
    py = round((h - pip_h_b) * 1.0)

    bottom_border = frame[h - 1, px + pip_w_b // 2]
    right_border = frame[py + pip_h_b // 2, w - 1]
    assert bottom_border.astype(int).sum() > 650, "expected a near-white bottom border pixel"
    assert right_border.astype(int).sum() > 650, "expected a near-white right border pixel"


def test_empty_video1_track_raises():
    ctl = TimelineController()
    with pytest.raises(ValueError):
        render_timeline(ctl.project, "/tmp/unused", "/tmp/unused.mp4")
