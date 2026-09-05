"""Thin wrappers around the ffmpeg/ffprobe binaries. Uses subprocess
directly (no ffmpeg-python dependency) so behaviour is easy to predict
and the PyInstaller bundle stays smaller.

AI Klipers expects ffmpeg/ffprobe to be on PATH, or bundled inside the
ffmpeg/ folder next to the executable (see get_ffmpeg_path()).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from utils.logger import get_logger

log = get_logger("ffmpeg_utils")


def get_ffmpeg_path(binary: str = "ffmpeg") -> str:
    """Prefer a copy bundled in ./ffmpeg (for the packaged .exe); fall back
    to PATH for development."""
    here = Path(__file__).resolve().parent.parent
    bundled = here / "ffmpeg" / (f"{binary}.exe" if _is_windows() else binary)
    if bundled.exists():
        return str(bundled)
    found = shutil.which(binary)
    if found:
        return found
    raise FileNotFoundError(
        f"{binary} not found on PATH or in ./ffmpeg/. Install ffmpeg or place "
        f"a static build in the ffmpeg/ folder."
    )


def _is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


def get_media_info(path: str) -> dict:
    """Run ffprobe and return duration/resolution/fps/codec info."""
    ffprobe = get_ffmpeg_path("ffprobe")
    cmd = [
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})

    fps = 0.0
    if video_stream.get("r_frame_rate"):
        num, _, den = video_stream["r_frame_rate"].partition("/")
        try:
            fps = float(num) / float(den) if den and float(den) != 0 else float(num)
        except ValueError:
            fps = 0.0

    return {
        "duration": float(data.get("format", {}).get("duration", 0.0)),
        "width": int(video_stream.get("width", 0) or 0),
        "height": int(video_stream.get("height", 0) or 0),
        "fps": round(fps, 2),
        "video_codec": video_stream.get("codec_name", ""),
        "audio_codec": audio_stream.get("codec_name", ""),
        "filesize": int(data.get("format", {}).get("size", 0) or 0),
    }


def extract_audio_waveform(path: str, sample_rate: int = 16000) -> np.ndarray:
    """Decode the audio track to mono 16-bit PCM directly into a numpy
    array via a pipe (no temp .wav file needed). Used for RMS-energy-based
    highlight detection in ai/viral_scorer.py."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    cmd = [
        ffmpeg, "-v", "error", "-i", path,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", "1", "-ar", str(sample_rate), "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    audio = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def extract_audio_file(path: str, output_path: str, sample_rate: int = 16000) -> str:
    """Extract audio to a .wav file on disk, e.g. as faster-whisper input."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    cmd = [
        ffmpeg, "-y", "-v", "error", "-i", path,
        "-ac", "1", "-ar", str(sample_rate), output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path


def cut_clip(input_path: str, start: float, end: float, output_path: str, reencode: bool = False) -> str:
    """Cut [start, end] (seconds) from input_path. Stream-copy by default
    (fast, no quality loss); re-encode when the clip will be modified
    further downstream (e.g. before burning subtitles)."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    duration = max(end - start, 0.01)
    cmd = [ffmpeg, "-y", "-v", "error", "-ss", str(start), "-i", input_path, "-t", str(duration)]
    if reencode:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
    else:
        cmd += ["-c", "copy"]
    cmd.append(output_path)
    subprocess.run(cmd, check=True)
    return output_path


def generate_thumbnail(input_path: str, timestamp: float, output_path: str, width: int = 480) -> str:
    ffmpeg = get_ffmpeg_path("ffmpeg")
    cmd = [
        ffmpeg, "-y", "-v", "error", "-ss", str(timestamp), "-i", input_path,
        "-frames:v", "1", "-vf", f"scale={width}:-1", output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path


CODEC_MAP = {
    "h264": {"cpu": "libx264", "nvidia": "h264_nvenc", "quicksync": "h264_qsv", "amf": "h264_amf"},
    "h265": {"cpu": "libx265", "nvidia": "hevc_nvenc", "quicksync": "hevc_qsv", "amf": "hevc_amf"},
    "av1": {"cpu": "libaom-av1", "nvidia": "av1_nvenc", "quicksync": "av1_qsv", "amf": "av1_amf"},
}

RESOLUTION_MAP = {
    "480p": 480, "720p": 720, "1080p": 1080, "2K": 1440, "4K": 2160,
}


def encode_clip(
    input_path: str, output_path: str, *,
    resolution: str = "1080p", fps: int = 30, codec: str = "h264",
    bitrate: str = "auto", hw_accel: str = "cpu", subtitle_ass_path: str | None = None,
) -> str:
    """Final export pass: scale, set fps, burn subtitles (optional), encode
    with the chosen codec/hardware accelerator."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    target_h = RESOLUTION_MAP.get(resolution, 1080)
    encoder = CODEC_MAP.get(codec, CODEC_MAP["h264"]).get(hw_accel, CODEC_MAP["h264"]["cpu"])

    vf_parts = [f"scale=-2:{target_h}"]
    if subtitle_ass_path:
        # ffmpeg's subtitles filter needs escaped colons/backslashes on Windows paths.
        escaped = subtitle_ass_path.replace("\\", "/").replace(":", "\\:")
        vf_parts.append(f"ass='{escaped}'")

    cmd = [
        ffmpeg, "-y", "-v", "error", "-i", input_path,
        "-vf", ",".join(vf_parts), "-r", str(fps),
        "-c:v", encoder, "-c:a", "aac", "-b:a", "192k",
    ]
    if bitrate != "auto":
        cmd += ["-b:v", bitrate]
    cmd.append(output_path)

    subprocess.run(cmd, check=True)
    return output_path


def final_encode(input_path: str, output_path: str, *, fps: int = 30, codec: str = "h264",
                  bitrate: str = "auto", hw_accel: str = "cpu") -> str:
    """Like encode_clip, but does NOT scale -- used by the timeline
    renderer's last pass, where every segment has already been fit to the
    correct target_w/target_h (including vertical outputs) during
    per-clip rendering. Re-scaling here with encode_clip's fixed
    landscape-oriented RESOLUTION_MAP would silently corrupt a vertical
    (reframed) render."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    encoder = CODEC_MAP.get(codec, CODEC_MAP["h264"]).get(hw_accel, CODEC_MAP["h264"]["cpu"])
    cmd = [ffmpeg, "-y", "-v", "error", "-i", input_path, "-r", str(fps),
           "-c:v", encoder, "-c:a", "aac", "-b:a", "192k"]
    if bitrate != "auto":
        cmd += ["-b:v", bitrate]
    cmd.append(output_path)
    subprocess.run(cmd, check=True)
    return output_path


def render_filtered_frame(input_path: str, seek_time: float, video_filter_graph: str,
                           output_path: str, timeout: float = 30.0) -> str:
    """Extracts a single frame at `seek_time` from `input_path`, runs it
    through `video_filter_graph` (a `-filter_complex` string with `[0:v]`
    input / `[outv]` output, as built by editor/effects.py), and saves it
    as an image. This is the building block for on-demand effect preview
    (app/widgets/effect_preview.py) -- a still frame, refreshed when the
    selected clip or its effects change, rather than continuous live
    playback through the effect chain (see README's documented
    simplifications for why full real-time preview is out of scope)."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    cmd = [ffmpeg, "-y", "-v", "error", "-ss", str(max(seek_time, 0.0)), "-i", input_path,
           "-filter_complex", video_filter_graph, "-map", "[outv]", "-frames:v", "1", output_path]
    subprocess.run(cmd, check=True, timeout=timeout)
    return output_path


def run_filtergraph(
    input_path: str, output_path: str, video_filter_graph: str, *,
    audio_filter: str | None = None, fps: float | None = None,
    has_audio: bool = True, reencode_audio: bool = True,
) -> str:
    """Runs one `-filter_complex` graph (as built by editor/effects.py)
    against a single input, mapping its `[outv]` pad to the output video
    stream and (optionally) applying an audio filter alongside it. Used
    for per-clip effect rendering in editor/timeline_renderer.py."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    cmd = [ffmpeg, "-y", "-v", "error", "-i", input_path, "-filter_complex", video_filter_graph,
           "-map", "[outv]"]

    if has_audio:
        if audio_filter:
            cmd += ["-filter:a", audio_filter]
        cmd += ["-map", "0:a?"]
    if fps:
        cmd += ["-r", str(fps)]

    cmd += ["-c:v", "libx264", "-preset", "veryfast"]
    if has_audio:
        cmd += ["-c:a", "aac"] if reencode_audio or audio_filter else ["-c:a", "copy"]
    cmd.append(output_path)

    subprocess.run(cmd, check=True)
    return output_path


def concat_videos(input_paths: list[str], output_path: str, reencode: bool = True) -> str:
    """Concatenates already-cut/processed clips into one file. Uses the
    concat *filter* (not the faster concat demuxer) by default because
    per-clip effects can leave slightly different encodes that the
    demuxer's stream-copy mode is picky about; the filter re-decodes and
    re-encodes, which is slower but robust."""
    ffmpeg = get_ffmpeg_path("ffmpeg")
    if len(input_paths) == 1:
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", input_paths[0], "-c", "copy", output_path], check=True)
        return output_path

    if not reencode:
        list_file = Path(output_path).with_suffix(".txt")
        list_file.write_text("\n".join(f"file '{Path(p).resolve()}'" for p in input_paths), encoding="utf-8")
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                         "-i", str(list_file), "-c", "copy", output_path], check=True)
        return output_path

    inputs = []
    for p in input_paths:
        inputs += ["-i", p]
    n = len(input_paths)
    filter_parts = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    filter_complex = f"{filter_parts}concat=n={n}:v=1:a=1[outv][outa]"
    cmd = [ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", filter_complex,
           "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "veryfast",
           "-c:a", "aac", output_path]
    subprocess.run(cmd, check=True)
    return output_path
