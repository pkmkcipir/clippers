"""Timeline data model + undo/redo controller for the Video Editor.

Deliberately has zero PySide6 import: app/timeline/timeline_scene.py is
the only place that touches Qt, so everything here is fully unit
testable headless, and reusable later (e.g. a Phase-2 Batch Export queue
could build/render a TimelineProject without ever creating a QApplication).

Undo/redo is snapshot-based (whole-project JSON-able dict, not a
fine-grained Command per field) -- simpler to get correct than one
Command subclass per operation, and a timeline project is small enough
(tens of clips) that snapshotting is cheap.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field, asdict


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class TimelineConflictError(Exception):
    """Raised when an operation would make two clips overlap on one track."""


@dataclass
class Effect:
    kind: str
    params: dict = field(default_factory=dict)


def _interp_ramp(ramp: list[tuple[float, float]], frac: float) -> float:
    if frac <= ramp[0][0]:
        return ramp[0][1]
    if frac >= ramp[-1][0]:
        return ramp[-1][1]
    for (f0, s0), (f1, s1) in zip(ramp, ramp[1:]):
        if f0 <= frac <= f1:
            if f1 == f0:
                return s1
            t = (frac - f0) / (f1 - f0)
            return s0 + t * (s1 - s0)
    return ramp[-1][1]


def _integrate_ramp_duration(source_duration: float, ramp: list[tuple[float, float]], samples: int = 200) -> float:
    """Numerically integrate output (rendered) duration for a piecewise-
    linear speed-vs-source-time ramp, so speed-ramped clips report an
    accurate timeline footprint instead of assuming a flat multiplier."""
    if len(ramp) < 2:
        return source_duration
    total = 0.0
    step = source_duration / samples
    for i in range(samples):
        frac = (i + 0.5) / samples
        speed = _interp_ramp(ramp, frac)
        total += step / max(speed, 0.01)
    return total


@dataclass
class TimelineClip:
    source_path: str
    source_in: float
    source_out: float
    timeline_start: float
    kind: str = "video"           # "video" | "text" | "image"
    label: str = ""
    text: str = ""                 # used when kind == "text"
    effects: list[Effect] = field(default_factory=list)
    id: str = field(default_factory=_uid)

    # Picture-in-picture framing -- only meaningful for kind == "video" on
    # a non-primary video track (e.g. "Video 2"). pip_scale=1.0 (the
    # default) means "full frame", i.e. the original cutaway behaviour
    # from Phase 2 -- existing saved projects load with this default and
    # are unaffected. pip_x/pip_y are 0-1 fractions of the *available*
    # space (0=flush against that edge, 1=flush against the opposite
    # edge), so the box always stays fully on-screen at any scale.
    pip_scale: float = 1.0
    pip_x: float = 0.0
    pip_y: float = 0.0
    pip_border: bool = False

    @property
    def duration(self) -> float:
        """Raw source-time span being cut from the source file. This is
        NOT affected by speed -- see timeline_duration for the on-timeline
        footprint, which is what matters for placement/overlap checks."""
        return round(self.source_out - self.source_in, 4)

    def _speed_effect(self) -> Effect | None:
        return next((e for e in self.effects if e.kind == "speed"), None)

    @property
    def speed_ramp(self) -> list[tuple[float, float]] | None:
        fx = self._speed_effect()
        ramp = fx.params.get("ramp") if fx else None
        return [(float(f), float(m)) for f, m in ramp] if ramp else None

    @property
    def speed_multiplier(self) -> float:
        """Flat speed multiplier for the constant-speed case (slow motion /
        fast motion). Returns 1.0 whenever a ramp is used instead, since
        ramps carry their own per-instant speed."""
        fx = self._speed_effect()
        if not fx or fx.params.get("ramp"):
            return 1.0
        return float(fx.params.get("multiplier", 1.0)) or 1.0

    @property
    def timeline_duration(self) -> float:
        """How much space this clip occupies on the timeline. Equals
        `duration` unless a speed effect is applied (slow motion stretches
        it, fast motion / a ramp can shrink or stretch it unevenly)."""
        ramp = self.speed_ramp
        if ramp:
            return round(_integrate_ramp_duration(self.duration, ramp), 4)
        return round(self.duration / self.speed_multiplier, 4)

    @property
    def timeline_end(self) -> float:
        return round(self.timeline_start + self.timeline_duration, 4)

    def overlaps(self, start: float, end: float) -> bool:
        return self.timeline_start < end - 1e-6 and self.timeline_end > start + 1e-6


@dataclass
class Track:
    index: int
    kind: str                      # "video" | "overlay"
    name: str = ""
    clips: list[TimelineClip] = field(default_factory=list)
    muted: bool = False

    def sorted_clips(self) -> list[TimelineClip]:
        return sorted(self.clips, key=lambda c: c.timeline_start)

    def has_conflict(self, start: float, end: float, exclude_id: str | None = None) -> bool:
        return any(c.overlaps(start, end) for c in self.clips if c.id != exclude_id)


@dataclass
class Marker:
    time: float
    label: str = ""
    id: str = field(default_factory=_uid)


@dataclass
class TimelineProject:
    name: str = "Untitled Timeline"
    fps: float = 30.0
    tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)

    @classmethod
    def new_default(cls, fps: float = 30.0) -> "TimelineProject":
        return cls(fps=fps, tracks=[
            Track(index=0, kind="video", name="Video 1"),
            Track(index=1, kind="video", name="Video 2"),
            Track(index=2, kind="overlay", name="Overlay"),
        ])

    def total_duration(self) -> float:
        ends = [c.timeline_end for t in self.tracks for c in t.clips]
        return max(ends) if ends else 0.0

    def find(self, clip_id: str) -> tuple[Track | None, TimelineClip | None]:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return t, c
        return None, None

    def track_by_index(self, index: int) -> Track | None:
        return next((t for t in self.tracks if t.index == index), None)

    def video_tracks(self) -> list[Track]:
        return sorted([t for t in self.tracks if t.kind == "video"], key=lambda t: t.index)

    def overlay_tracks(self) -> list[Track]:
        return sorted([t for t in self.tracks if t.kind == "overlay"], key=lambda t: t.index)

    # ---- serialization (also used by undo/redo snapshots + disk saves) ----
    def to_dict(self) -> dict:
        return {
            "name": self.name, "fps": self.fps,
            "tracks": [
                {"index": t.index, "kind": t.kind, "name": t.name, "muted": t.muted,
                 "clips": [
                     {**asdict(c), "effects": [asdict(e) for e in c.effects]}
                     for c in t.clips
                 ]}
                for t in self.tracks
            ],
            "markers": [asdict(m) for m in self.markers],
        }

    def load_dict(self, data: dict) -> None:
        """Mutate self in place from a dict produced by to_dict(). Used for
        undo/redo restores and for loading a saved project from disk/DB."""
        self.name = data.get("name", self.name)
        self.fps = data.get("fps", self.fps)
        self.tracks = [
            Track(
                index=t["index"], kind=t["kind"], name=t.get("name", ""), muted=t.get("muted", False),
                clips=[
                    TimelineClip(
                        source_path=c["source_path"], source_in=c["source_in"], source_out=c["source_out"],
                        timeline_start=c["timeline_start"], kind=c.get("kind", "video"),
                        label=c.get("label", ""), text=c.get("text", ""), id=c.get("id") or _uid(),
                        effects=[Effect(kind=e["kind"], params=e.get("params", {})) for e in c.get("effects", [])],
                        pip_scale=c.get("pip_scale", 1.0), pip_x=c.get("pip_x", 0.0),
                        pip_y=c.get("pip_y", 0.0), pip_border=c.get("pip_border", False),
                    )
                    for c in t.get("clips", [])
                ],
            )
            for t in data.get("tracks", [])
        ]
        self.markers = [Marker(time=m["time"], label=m.get("label", ""), id=m.get("id") or _uid())
                         for m in data.get("markers", [])]

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineProject":
        project = cls()
        project.load_dict(data)
        return project


class UndoStack:
    def __init__(self, max_depth: int = 100):
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self.max_depth = max_depth

    def snapshot(self, project: TimelineProject) -> None:
        self._undo.append(project.to_dict())
        if len(self._undo) > self.max_depth:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, project: TimelineProject) -> bool:
        if not self._undo:
            return False
        self._redo.append(project.to_dict())
        project.load_dict(self._undo.pop())
        return True

    def redo(self, project: TimelineProject) -> bool:
        if not self._redo:
            return False
        self._undo.append(project.to_dict())
        project.load_dict(self._redo.pop())
        return True


class TimelineController:
    """High-level operations the UI (or a test) calls into. Every mutating
    method validates first and only snapshots (for undo) once it knows the
    operation is legal, so a rejected operation never pollutes the undo
    stack with a no-op entry."""

    def __init__(self, project: TimelineProject | None = None):
        self.project = project or TimelineProject.new_default()
        self.undo_stack = UndoStack()
        self.playhead = 0.0
        self.selected_clip_id: str | None = None

    # ---- clip lifecycle ---------------------------------------------
    def add_clip(self, track_index: int, source_path: str, source_in: float, source_out: float,
                 timeline_start: float, kind: str = "video", label: str = "", text: str = "") -> TimelineClip:
        track = self.project.track_by_index(track_index)
        if track is None:
            raise ValueError(f"No track at index {track_index}")
        end = timeline_start + (source_out - source_in)
        if track.has_conflict(timeline_start, end):
            raise TimelineConflictError("Clip would overlap an existing clip on this track")

        self.undo_stack.snapshot(self.project)
        clip = TimelineClip(source_path=source_path, source_in=source_in, source_out=source_out,
                             timeline_start=timeline_start, kind=kind, label=label, text=text)
        track.clips.append(clip)
        return clip

    def delete_clip(self, clip_id: str, ripple: bool = False) -> None:
        track, clip = self.project.find(clip_id)
        if not clip:
            return
        self.undo_stack.snapshot(self.project)
        track.clips.remove(clip)
        if ripple:
            shift = clip.timeline_duration
            for other in track.clips:
                if other.timeline_start >= clip.timeline_start - 1e-6:
                    other.timeline_start = round(other.timeline_start - shift, 4)

    def move_clip(self, clip_id: str, new_track_index: int, new_start: float) -> None:
        old_track, clip = self.project.find(clip_id)
        if not clip:
            return
        new_track = self.project.track_by_index(new_track_index)
        if new_track is None:
            raise ValueError(f"No track at index {new_track_index}")
        new_start = max(round(new_start, 4), 0.0)
        new_end = new_start + clip.timeline_duration
        if new_track.has_conflict(new_start, new_end, exclude_id=clip_id):
            raise TimelineConflictError("Clip would overlap an existing clip on the target track")

        self.undo_stack.snapshot(self.project)
        clip.timeline_start = new_start
        if old_track.index != new_track.index:
            old_track.clips.remove(clip)
            new_track.clips.append(clip)

    def trim_clip(self, clip_id: str, *, new_source_in: float | None = None,
                  new_source_out: float | None = None, new_timeline_start: float | None = None) -> None:
        track, clip = self.project.find(clip_id)
        if not clip:
            return
        source_in = clip.source_in if new_source_in is None else max(round(new_source_in, 4), 0.0)
        source_out = clip.source_out if new_source_out is None else max(round(new_source_out, 4), source_in + 0.04)
        timeline_start = clip.timeline_start if new_timeline_start is None else max(round(new_timeline_start, 4), 0.0)
        new_end = timeline_start + (source_out - source_in)

        if track.has_conflict(timeline_start, new_end, exclude_id=clip_id):
            raise TimelineConflictError("Trim would overlap an existing clip on this track")

        self.undo_stack.snapshot(self.project)
        clip.source_in, clip.source_out, clip.timeline_start = source_in, source_out, timeline_start

    def split_clip(self, clip_id: str, at_time: float) -> tuple[TimelineClip, TimelineClip] | None:
        track, clip = self.project.find(clip_id)
        if not clip or not (clip.timeline_start + 0.01 < at_time < clip.timeline_end - 0.01):
            return None

        self.undo_stack.snapshot(self.project)
        offset = at_time - clip.timeline_start
        split_source_time = clip.source_in + offset

        left = TimelineClip(source_path=clip.source_path, source_in=clip.source_in,
                             source_out=split_source_time, timeline_start=clip.timeline_start,
                             kind=clip.kind, label=clip.label, text=clip.text,
                             effects=copy.deepcopy(clip.effects))
        right = TimelineClip(source_path=clip.source_path, source_in=split_source_time,
                              source_out=clip.source_out, timeline_start=at_time,
                              kind=clip.kind, label=clip.label, text=clip.text,
                              effects=copy.deepcopy(clip.effects))
        track.clips.remove(clip)
        track.clips.extend([left, right])
        return left, right

    def merge_clips(self, clip_id_a: str, clip_id_b: str) -> TimelineClip | None:
        """Rejoin two adjacent clips sharing one source and contiguous in
        both timeline position and source time -- e.g. undoing a split, or
        two independently-trimmed neighbors that now butt up exactly.
        Returns None (no-op, nothing snapshotted) if they don't qualify."""
        track_a, a = self.project.find(clip_id_a)
        track_b, b = self.project.find(clip_id_b)
        if not a or not b or track_a is not track_b:
            return None
        first, second = (a, b) if a.timeline_start <= b.timeline_start else (b, a)
        contiguous_timeline = abs(first.timeline_end - second.timeline_start) < 0.02
        contiguous_source = (first.source_path == second.source_path and
                              abs(first.source_out - second.source_in) < 0.02)
        if not (contiguous_timeline and contiguous_source):
            return None

        self.undo_stack.snapshot(self.project)
        merged = TimelineClip(source_path=first.source_path, source_in=first.source_in,
                               source_out=second.source_out, timeline_start=first.timeline_start,
                               kind=first.kind, label=first.label or second.label,
                               effects=copy.deepcopy(first.effects))
        track_a.clips.remove(first)
        track_a.clips.remove(second)
        track_a.clips.append(merged)
        return merged

    # ---- markers -------------------------------------------------------
    def add_marker(self, time: float, label: str = "") -> Marker:
        self.undo_stack.snapshot(self.project)
        marker = Marker(time=round(max(time, 0.0), 4), label=label)
        self.project.markers.append(marker)
        return marker

    def delete_marker(self, marker_id: str) -> None:
        if not any(m.id == marker_id for m in self.project.markers):
            return
        self.undo_stack.snapshot(self.project)
        self.project.markers = [m for m in self.project.markers if m.id != marker_id]

    # ---- effects ---------------------------------------------------------
    def add_effect(self, clip_id: str, effect: Effect) -> None:
        _, clip = self.project.find(clip_id)
        if not clip:
            return
        self.undo_stack.snapshot(self.project)
        clip.effects.append(effect)

    def remove_effect(self, clip_id: str, index: int) -> None:
        _, clip = self.project.find(clip_id)
        if not clip or not (0 <= index < len(clip.effects)):
            return
        self.undo_stack.snapshot(self.project)
        clip.effects.pop(index)

    # ---- picture-in-picture framing (kind == "video" clips) ----------------
    def set_pip(self, clip_id: str, *, scale: float | None = None, x: float | None = None,
                y: float | None = None, border: bool | None = None) -> None:
        _, clip = self.project.find(clip_id)
        if not clip:
            return
        self.undo_stack.snapshot(self.project)
        if scale is not None:
            clip.pip_scale = round(max(0.1, min(1.0, scale)), 3)
        if x is not None:
            clip.pip_x = round(max(0.0, min(1.0, x)), 3)
        if y is not None:
            clip.pip_y = round(max(0.0, min(1.0, y)), 3)
        if border is not None:
            clip.pip_border = border

    # ---- copy / paste ----------------------------------------------------
    def copy_clip(self, clip_id: str) -> TimelineClip | None:
        _, clip = self.project.find(clip_id)
        return copy.deepcopy(clip) if clip else None

    def paste_clip(self, clip: TimelineClip, track_index: int, at_time: float) -> TimelineClip:
        track = self.project.track_by_index(track_index)
        if track is None:
            raise ValueError(f"No track at index {track_index}")
        at_time = round(max(at_time, 0.0), 4)
        end = at_time + clip.timeline_duration
        if track.has_conflict(at_time, end):
            raise TimelineConflictError("Paste would overlap an existing clip on this track")

        self.undo_stack.snapshot(self.project)
        new_clip = copy.deepcopy(clip)
        new_clip.id = _uid()
        new_clip.timeline_start = at_time
        track.clips.append(new_clip)
        return new_clip

    # ---- undo/redo ---------------------------------------------------------
    def undo(self) -> bool:
        return self.undo_stack.undo(self.project)

    def redo(self) -> bool:
        return self.undo_stack.redo(self.project)
