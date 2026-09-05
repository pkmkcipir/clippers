"""Renders a TimelineProject (app/timeline/model.py) to one final MP4.

Rendering strategy, in order:
  1. "Video 1" is the backbone: every one of its clips is individually
     cut + effect-processed + fit to the target resolution, gaps (no clip
     at some point in time) are filled with a generated black/silent
     segment, and everything is concatenated in timeline order.
  2. "Video 2" clips are treated as full-frame cutaways: composited over
     the Video 1 base during their exact time window (so a Video 2 clip
     simply replaces what Video 1 shows for that window -- this is the
     documented simplification instead of true alpha/PIP compositing
     between two video tracks; see README's Video Editor section).
  3. "Overlay" clips (text or a logo/watermark image) are composited on
     top of that, text via `drawtext`, images pinned to a corner.
  4. One final encode pass applies the export resolution/codec/bitrate/hw
     accel the user picked (same editor.ffmpeg_utils.encode_clip used by
     the Phase-1 single-clip exporter).

Speed-ramped clips are rendered as N concatenated constant-speed
sub-segments (see _render_ramped_clip) rather than a single continuous
filter, since ffmpeg's audio tempo filter has no time-varying form --
keeping video and audio segment boundaries identical avoids sync drift.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from app.timeline.model import TimelineProject, TimelineClip, Track
from editor.effects import build_video_filter_graph, build_speed_audio_filter
from editor.ffmpeg_utils import (
    get_ffmpeg_path, get_media_info, cut_clip, run_filtergraph, concat_videos, final_encode,
)
from utils.logger import get_logger

log = get_logger("timeline_renderer")

ProgressCb = Callable[[str, float], None]


def _fit_tail(target_w: int, target_h: int) -> str:
    """Scale-to-cover + center-crop so every segment is pixel-identical in
    size before concatenation, regardless of its source's native aspect.
    setsar=1 is required too: ffmpeg's scale filter can leave a slightly
    off sample-aspect-ratio behind (rounding from the source's own SAR),
    which the concat filter then rejects even when width/height match."""
    return f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1"


def _render_black_segment(duration: float, temp_dir: Path, index, fps: float, target_w: int, target_h: int) -> str:
    ffmpeg = get_ffmpeg_path("ffmpeg")
    out = str(temp_dir / f"seg_{index}_black.mp4")
    subprocess.run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={target_w}x{target_h}:r={fps}:d={max(duration, 0.05):.3f}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-vf", "setsar=1",
        "-t", f"{max(duration, 0.05):.3f}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out,
    ], check=True)
    return out


def _render_clip_segment(clip: TimelineClip, temp_dir: Path, index, fps: float,
                          target_w: int, target_h: int) -> str:
    """Cuts + applies effects + fits-to-target for one 'video' kind clip.
    Speed-ramped clips are delegated to _render_ramped_clip."""
    if clip.speed_ramp:
        return _render_ramped_clip(clip, temp_dir, index, fps, target_w, target_h)

    raw_cut = str(temp_dir / f"seg_{index}_raw.mp4")
    cut_clip(clip.source_path, clip.source_in, clip.source_out, raw_cut, reencode=True)

    info = get_media_info(clip.source_path)
    speed_mult = clip.speed_multiplier
    tail_parts = [_fit_tail(target_w, target_h)]
    if speed_mult != 1.0:
        tail_parts.append(f"setpts=PTS/{speed_mult}")

    graph = build_video_filter_graph(
        clip.effects, width=info["width"], height=info["height"], clip_duration=clip.duration,
        video_path=clip.source_path, clip_source_start=clip.source_in, clip_source_end=clip.source_out,
        lut_dir=str(temp_dir / "luts"), extra_tail=",".join(tail_parts),
    )
    audio_filter = build_speed_audio_filter(speed_mult) if speed_mult != 1.0 else None

    out = str(temp_dir / f"seg_{index}.mp4")
    run_filtergraph(raw_cut, out, graph, audio_filter=audio_filter, fps=fps)
    return out


def _render_ramped_clip(clip: TimelineClip, temp_dir: Path, index, fps: float,
                         target_w: int, target_h: int) -> str:
    ramp = clip.speed_ramp
    info = get_media_info(clip.source_path)
    non_speed_effects = [e for e in clip.effects if e.kind != "speed"]
    sub_paths = []

    for i, ((f0, s0), (f1, s1)) in enumerate(zip(ramp, ramp[1:])):
        if f1 <= f0:
            continue
        seg_in = clip.source_in + f0 * clip.duration
        seg_out = clip.source_in + f1 * clip.duration
        avg_speed = max((s0 + s1) / 2, 0.05)

        raw = str(temp_dir / f"seg_{index}_r{i}_raw.mp4")
        cut_clip(clip.source_path, seg_in, seg_out, raw, reencode=True)

        tail = f"{_fit_tail(target_w, target_h)},setpts=PTS/{avg_speed}"
        graph = build_video_filter_graph(
            non_speed_effects, width=info["width"], height=info["height"], clip_duration=seg_out - seg_in,
            video_path=clip.source_path, clip_source_start=seg_in, clip_source_end=seg_out,
            lut_dir=str(temp_dir / "luts"), extra_tail=tail,
        )
        out = str(temp_dir / f"seg_{index}_r{i}.mp4")
        run_filtergraph(raw, out, graph, audio_filter=build_speed_audio_filter(avg_speed), fps=fps)
        sub_paths.append(out)

    final = str(temp_dir / f"seg_{index}.mp4")
    concat_videos(sub_paths, final)
    return final


def _flatten_primary_track(track: Track, total_duration: float, temp_dir: Path, fps: float,
                            target_w: int, target_h: int, progress_cb: ProgressCb | None) -> list[str]:
    clips = track.sorted_clips()
    plan: list[tuple[str, object]] = []
    cursor = 0.0
    for clip in clips:
        if clip.timeline_start > cursor + 0.02:
            plan.append(("gap", (cursor, clip.timeline_start)))
        plan.append(("clip", clip))
        cursor = clip.timeline_end
    if cursor < total_duration - 0.02:
        plan.append(("gap", (cursor, total_duration)))

    paths = []
    for i, (kind, payload) in enumerate(plan):
        if progress_cb:
            progress_cb("rendering_clips", 0.05 + 0.35 * (i / max(len(plan), 1)))
        if kind == "gap":
            start, end = payload
            paths.append(_render_black_segment(end - start, temp_dir, i, fps, target_w, target_h))
        else:
            paths.append(_render_clip_segment(payload, temp_dir, i, fps, target_w, target_h))
    return paths


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")


PIP_BORDER_PX = 4


def _add_pip_border(input_path: str, temp_dir: Path, tag) -> str:
    """Pads a rendered PIP clip with a solid border so it reads clearly as
    an inset window over the base footage rather than a stray rectangle."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    out = str(temp_dir / f"seg_{tag}_bordered.mp4")
    vf = f"pad=iw+{2 * PIP_BORDER_PX}:ih+{2 * PIP_BORDER_PX}:{PIP_BORDER_PX}:{PIP_BORDER_PX}:color=white"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", input_path, "-vf", vf, "-c:a", "copy", out], check=True)
    return out


def _composite_overlays(base_path: str, overlay_clips: list[TimelineClip], temp_dir: Path,
                         fps: float, target_w: int, target_h: int) -> str:
    if not overlay_clips:
        return base_path

    overlay_clips = sorted(overlay_clips, key=lambda c: c.timeline_start)
    inputs: list[tuple[str, str]] = [("video", base_path)]
    filter_lines: list[str] = []
    current = "0:v"

    for n, clip in enumerate(overlay_clips, start=1):
        t1, t2 = clip.timeline_start, clip.timeline_end

        if clip.kind == "text":
            font_size = max(int(target_h * 0.045), 18)
            box_y = int(target_h * 0.78)
            label = f"txt{n}"
            filter_lines.append(
                f"[{current}]drawtext=text='{_escape_drawtext(clip.text)}':fontcolor=white:"
                f"fontsize={font_size}:box=1:boxcolor=black@0.55:boxborderw=14:"
                f"x=(w-text_w)/2:y={box_y}:enable='between(t,{t1:.3f},{t2:.3f})'[{label}]"
            )
            current = label

        elif clip.kind == "image":
            idx = len(inputs)
            inputs.append(("image", clip.source_path))
            logo_w = int(target_w * 0.15)
            margin = int(target_h * 0.03)
            delayed, label = f"imgdelay{n}", f"img{n}"
            filter_lines.append(f"[{idx}:v]scale={logo_w}:-1,setpts=PTS+{t1:.3f}/TB[{delayed}]")
            filter_lines.append(
                f"[{current}][{delayed}]overlay=x=W-w-{margin}:y={margin}:"
                f"enable='between(t,{t1:.3f},{t2:.3f})'[{label}]"
            )
            current = label

        elif clip.kind == "video":
            idx = len(inputs)
            if clip.pip_scale >= 0.999:
                # Full-frame cutaway (original Phase 2 behaviour, unchanged
                # for any clip that doesn't opt into a smaller PIP box).
                pip_w, pip_h = target_w, target_h
                px, py = 0, 0
                rendered = _render_clip_segment(clip, temp_dir, f"ov{n}", fps, pip_w, pip_h)
            else:
                pip_w = max(int(target_w * clip.pip_scale / 2) * 2, 2)
                pip_h = max(int(round(pip_w * target_h / target_w) / 2) * 2, 2)
                rendered = _render_clip_segment(clip, temp_dir, f"ov{n}", fps, pip_w, pip_h)
                if clip.pip_border:
                    rendered = _add_pip_border(rendered, temp_dir, n)
                    pip_w += 2 * PIP_BORDER_PX
                    pip_h += 2 * PIP_BORDER_PX
                px = int(round((target_w - pip_w) * clip.pip_x))
                py = int(round((target_h - pip_h) * clip.pip_y))

            inputs.append(("video", rendered))
            delayed, label = f"viddelay{n}", f"vid{n}"
            filter_lines.append(f"[{idx}:v]setpts=PTS+{t1:.3f}/TB[{delayed}]")
            filter_lines.append(
                f"[{current}][{delayed}]overlay=x={px}:y={py}:enable='between(t,{t1:.3f},{t2:.3f})'[{label}]"
            )
            current = label

    filter_lines[-1] = filter_lines[-1].rsplit("[", 1)[0] + "[outv]"

    ffmpeg = get_ffmpeg_path("ffmpeg")
    cmd = [ffmpeg, "-y", "-v", "error"]
    for kind, path in inputs:
        if kind == "image":
            cmd += ["-loop", "1", "-i", path]
        else:
            cmd += ["-i", path]
    cmd += ["-filter_complex", ";".join(filter_lines), "-map", "[outv]", "-map", "0:a?",
            "-t", str(get_media_info(base_path)["duration"]),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
    out_path = str(temp_dir / "_composited.mp4")
    cmd.append(out_path)
    subprocess.run(cmd, check=True)
    return out_path


def resolve_target_dimensions(project: TimelineProject, resolution: str) -> tuple[int, int]:
    """Vertical output if any Video 1 clip has a reframe effect aiming at
    a vertical aspect (this app's core use case is shorts/reels); landscape
    otherwise. Height comes from the requested export resolution."""
    from editor.ffmpeg_utils import RESOLUTION_MAP
    target_h_landscape = RESOLUTION_MAP.get(resolution, 1080)

    primary = project.track_by_index(0)
    is_vertical = False
    if primary:
        for clip in primary.clips:
            for fx in clip.effects:
                if fx.kind == "reframe" and float(fx.params.get("target_aspect", 1.0)) < 1.0:
                    is_vertical = True

    if is_vertical:
        height = int(target_h_landscape * 16 / 9)
        width = int(round(height * 9 / 16 / 2)) * 2
        height = int(round(height / 2)) * 2
        return width, height

    width = int(round(target_h_landscape * 16 / 9 / 2)) * 2
    return width, target_h_landscape


def render_timeline(
    project: TimelineProject, temp_dir: str | Path, output_path: str, *,
    resolution: str = "1080p", fps: float | None = None, codec: str = "h264",
    bitrate: str = "auto", hw_accel: str = "cpu",
    progress_cb: ProgressCb | None = None,
) -> str:
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    fps = fps or project.fps

    primary = project.track_by_index(0)
    if not primary or not primary.clips:
        raise ValueError("Video 1 track is empty -- add at least one clip before rendering.")

    total = project.total_duration()
    target_w, target_h = resolve_target_dimensions(project, resolution)
    log.info("Rendering timeline: %.1fs, target %dx%d @ %sfps", total, target_w, target_h, fps)

    if progress_cb:
        progress_cb("rendering_clips", 0.02)
    segment_paths = _flatten_primary_track(primary, total, temp_dir, fps, target_w, target_h, progress_cb)

    if progress_cb:
        progress_cb("concatenating", 0.42)
    base_path = str(temp_dir / "_base_concat.mp4")
    concat_videos(segment_paths, base_path)

    overlay_clips = [c for t in project.tracks if t.index != 0 for c in t.clips]
    if progress_cb:
        progress_cb("compositing_overlays", 0.55)
    composited_path = _composite_overlays(base_path, overlay_clips, temp_dir, fps, target_w, target_h)

    if progress_cb:
        progress_cb("final_encode", 0.85)
    final_encode(composited_path, output_path, fps=int(fps), codec=codec, bitrate=bitrate, hw_accel=hw_accel)

    if progress_cb:
        progress_cb("done", 1.0)
    log.info("Timeline render complete -> %s", output_path)
    return output_path
