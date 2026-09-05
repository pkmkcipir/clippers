"""Tests for app/timeline/model.py -- the undo/redo controller that the
Qt timeline widget will drive. Pure Python, no Qt/display needed.
Run with: pytest tests/test_timeline_model.py
"""
import pytest

from app.timeline.model import (
    TimelineProject, TimelineController, TimelineConflictError, Effect,
)


def make_controller():
    return TimelineController(TimelineProject.new_default())


def test_default_project_has_three_tracks():
    project = TimelineProject.new_default()
    assert [t.name for t in project.tracks] == ["Video 1", "Video 2", "Overlay"]
    assert project.total_duration() == 0.0


def test_add_clip_and_duration():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=10, timeline_start=0)
    assert clip.duration == 10
    assert clip.timeline_end == 10
    assert ctl.project.total_duration() == 10


def test_add_clip_conflict_raises_and_does_not_snapshot():
    ctl = make_controller()
    ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    with pytest.raises(TimelineConflictError):
        ctl.add_clip(0, "b.mp4", 0, 10, timeline_start=5)  # overlaps 0-10
    # Rejected op must not have polluted the undo stack.
    assert ctl.undo_stack.can_undo() is True  # only the first add_clip snapshotted
    assert len(ctl.undo_stack._undo) == 1


def test_add_clip_adjacent_no_conflict():
    ctl = make_controller()
    ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    clip2 = ctl.add_clip(0, "b.mp4", 0, 5, timeline_start=10)  # starts exactly where first ends
    assert clip2.timeline_start == 10


def test_move_clip_between_tracks():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    ctl.move_clip(clip.id, new_track_index=1, new_start=5)
    track0 = ctl.project.track_by_index(0)
    track1 = ctl.project.track_by_index(1)
    assert clip.id not in [c.id for c in track0.clips]
    assert clip.id in [c.id for c in track1.clips]
    assert clip.timeline_start == 5


def test_move_clip_conflict_raises():
    ctl = make_controller()
    ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    clip2 = ctl.add_clip(1, "b.mp4", 0, 10, timeline_start=0)
    with pytest.raises(TimelineConflictError):
        ctl.move_clip(clip2.id, new_track_index=0, new_start=5)  # would overlap clip1 on track 0


def test_trim_clip_head_and_tail():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=5, source_out=20, timeline_start=0)
    ctl.trim_clip(clip.id, new_source_in=8, new_timeline_start=3)  # trim head
    assert clip.source_in == 8 and clip.timeline_start == 3 and clip.duration == 12
    ctl.trim_clip(clip.id, new_source_out=18)  # trim tail
    assert clip.source_out == 18 and clip.duration == 10


def test_split_clip_never_leaves_gap_or_overlap():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=30, timeline_start=10)
    result = ctl.split_clip(clip.id, at_time=25)  # 15s into the clip
    assert result is not None
    left, right = result
    assert left.timeline_start == 10 and left.timeline_end == 25
    assert right.timeline_start == 25 and right.timeline_end == 40
    assert left.source_out == right.source_in  # contiguous in source too
    track = ctl.project.track_by_index(0)
    assert {c.id for c in track.clips} == {left.id, right.id}


def test_split_outside_clip_bounds_is_noop():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    assert ctl.split_clip(clip.id, at_time=0) is None
    assert ctl.split_clip(clip.id, at_time=10) is None
    assert ctl.split_clip(clip.id, at_time=50) is None


def test_merge_undoes_a_split():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=30, timeline_start=0)
    left, right = ctl.split_clip(clip.id, at_time=12)
    merged = ctl.merge_clips(left.id, right.id)
    assert merged is not None
    assert merged.source_in == 0 and merged.source_out == 30 and merged.timeline_start == 0
    track = ctl.project.track_by_index(0)
    assert len(track.clips) == 1


def test_merge_non_contiguous_returns_none_and_does_not_snapshot():
    ctl = make_controller()
    a = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    b = ctl.add_clip(0, "b.mp4", 0, 10, timeline_start=10)  # different source
    undo_depth_before = len(ctl.undo_stack._undo)
    assert ctl.merge_clips(a.id, b.id) is None
    assert len(ctl.undo_stack._undo) == undo_depth_before


def test_ripple_delete_shifts_following_clips_on_same_track_only():
    ctl = make_controller()
    a = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    b = ctl.add_clip(0, "b.mp4", 0, 10, timeline_start=10)
    other_track_clip = ctl.add_clip(1, "c.mp4", 0, 10, timeline_start=10)

    ctl.delete_clip(a.id, ripple=True)
    assert b.timeline_start == 0  # shifted left by a's 10s duration
    assert other_track_clip.timeline_start == 10  # untouched, different track


def test_non_ripple_delete_leaves_a_gap():
    ctl = make_controller()
    a = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    b = ctl.add_clip(0, "b.mp4", 0, 10, timeline_start=10)
    ctl.delete_clip(a.id, ripple=False)
    assert b.timeline_start == 10  # untouched


def test_copy_paste_clip():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    copied = ctl.copy_clip(clip.id)
    pasted = ctl.paste_clip(copied, track_index=1, at_time=20)
    assert pasted.id != clip.id
    assert pasted.source_path == clip.source_path
    assert pasted.timeline_start == 20


def test_paste_conflict_raises():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    ctl.add_clip(1, "b.mp4", 0, 10, timeline_start=5)
    copied = ctl.copy_clip(clip.id)
    with pytest.raises(TimelineConflictError):
        ctl.paste_clip(copied, track_index=1, at_time=0)  # overlaps the 5-15 clip on track 1


def test_undo_redo_round_trip():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    ctl.move_clip(clip.id, new_track_index=1, new_start=5)
    assert ctl.project.track_by_index(0).clips == []
    assert ctl.undo() is True
    assert len(ctl.project.track_by_index(0).clips) == 1  # move undone
    assert ctl.undo() is True
    assert ctl.project.total_duration() == 0.0  # add_clip undone too
    assert ctl.undo() is False  # nothing left to undo

    assert ctl.redo() is True
    assert ctl.project.total_duration() == 10.0
    assert ctl.redo() is True
    assert len(ctl.project.track_by_index(1).clips) == 1
    assert ctl.redo() is False  # nothing left to redo


def test_new_action_after_undo_clears_redo_stack():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    ctl.undo()
    assert ctl.undo_stack.can_redo() is True
    ctl.add_clip(0, "b.mp4", 0, 5, timeline_start=0)
    assert ctl.undo_stack.can_redo() is False


def test_markers():
    ctl = make_controller()
    marker = ctl.add_marker(12.5, label="Hook")
    assert marker in ctl.project.markers
    ctl.delete_marker(marker.id)
    assert ctl.project.markers == []


def test_effects_add_remove():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0)
    ctl.add_effect(clip.id, Effect(kind="brightness_contrast_saturation", params={"brightness": 0.1}))
    assert len(clip.effects) == 1
    ctl.remove_effect(clip.id, 0)
    assert len(clip.effects) == 0


def test_to_dict_from_dict_round_trip_preserves_state():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", 0, 10, timeline_start=0, label="Intro")
    ctl.add_effect(clip.id, Effect(kind="vignette", params={"strength": 0.5}))
    ctl.add_marker(3.0, "note")

    data = ctl.project.to_dict()
    restored = TimelineProject.from_dict(data)

    assert restored.name == ctl.project.name
    assert restored.total_duration() == ctl.project.total_duration()
    restored_clip = restored.tracks[0].clips[0]
    assert restored_clip.label == "Intro"
    assert restored_clip.effects[0].kind == "vignette"
    assert restored.markers[0].label == "note"


def test_slow_motion_stretches_timeline_footprint():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=10, timeline_start=0)
    assert clip.duration == 10 and clip.timeline_duration == 10  # no speed effect yet

    ctl.add_effect(clip.id, Effect(kind="speed", params={"multiplier": 0.5}))  # half speed = 2x longer
    assert clip.duration == 10          # source span cut is unchanged
    assert clip.timeline_duration == 20  # but it now occupies 20s on the timeline
    assert clip.timeline_end == 20


def test_fast_motion_shrinks_timeline_footprint():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=10, timeline_start=0)
    ctl.add_effect(clip.id, Effect(kind="speed", params={"multiplier": 2.0}))  # 2x speed = half duration
    assert clip.timeline_duration == 5


def test_speed_change_is_reflected_in_conflict_checks():
    ctl = make_controller()
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=10, timeline_start=0)
    ctl.add_clip(0, "b.mp4", source_in=0, source_out=5, timeline_start=10)  # starts right after (at normal speed)

    # Slowing the first clip down now makes it run into the second clip;
    # this should surface as a real conflict if we try to re-place it.
    ctl.add_effect(clip.id, Effect(kind="speed", params={"multiplier": 0.5}))
    assert clip.timeline_end == 20  # now overlaps b.mp4 (10-15)
    with pytest.raises(TimelineConflictError):
        ctl.move_clip(clip.id, new_track_index=0, new_start=0)  # re-validate its own slot


def test_speed_ramp_integrates_to_correct_duration():
    ctl = make_controller()
    # Ramp: normal speed for the first half, then 4x slow-motion for the second half.
    clip = ctl.add_clip(0, "a.mp4", source_in=0, source_out=10, timeline_start=0)
    ctl.add_effect(clip.id, Effect(kind="speed", params={"ramp": [[0.0, 1.0], [0.5, 1.0], [0.5, 0.25], [1.0, 0.25]]}))
    # First 5s of source at 1x = 5s output; last 5s of source at 0.25x = 20s output.
    assert clip.timeline_duration == pytest.approx(25.0, abs=0.5)
