"""Orchestrates turning a Clip database record into a finished MP4:
cut the segment from the source -> generate/burn subtitles -> encode with
the requested resolution/fps/codec/bitrate/hardware accelerator.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from editor.ffmpeg_utils import cut_clip, encode_clip
from subtitle.generator import write_ass
from ai.transcription import Sentence
from utils.logger import get_logger
from utils.system_info import recommend_hw_accel

log = get_logger("exporter")


@dataclass
class ExportSettings:
    resolution: str = "1080p"    # 480p/720p/1080p/2K/4K
    fps: int = 30                # 24/30/60
    codec: str = "h264"          # h264/h265/av1
    bitrate: str = "auto"
    hw_accel: str = "auto"       # auto/nvidia/quicksync/amf/cpu
    subtitle_style: str = "tiktok"
    burn_subtitles: bool = True


def export_clip(
    source_path: str,
    clip_start: float,
    clip_end: float,
    sentences: list[Sentence],
    output_path: str,
    settings: ExportSettings,
    temp_dir: str = ".",
    progress_cb: Callable[[str, float], None] | None = None,
) -> str:
    """sentences must already be the subset covering [clip_start, clip_end]
    (see services/clip_pipeline.py, which slices the full transcript per
    clip and re-bases timestamps to 0 before calling this)."""
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    hw_accel = recommend_hw_accel() if settings.hw_accel == "auto" else settings.hw_accel

    if progress_cb:
        progress_cb("cutting", 0.1)
    raw_cut = str(temp_dir / f"_raw_{Path(output_path).stem}.mp4")
    cut_clip(source_path, clip_start, clip_end, raw_cut, reencode=True)

    subtitle_ass = None
    if settings.burn_subtitles and sentences:
        if progress_cb:
            progress_cb("subtitle", 0.4)
        subtitle_ass = str(temp_dir / f"_sub_{Path(output_path).stem}.ass")
        write_ass(sentences, subtitle_ass, style_key=settings.subtitle_style)

    if progress_cb:
        progress_cb("encoding", 0.6)

    try:
        encode_clip(
            raw_cut, output_path,
            resolution=settings.resolution, fps=settings.fps, codec=settings.codec,
            bitrate=settings.bitrate, hw_accel=hw_accel, subtitle_ass_path=subtitle_ass,
        )
    except Exception:
        if hw_accel != "cpu":
            log.warning("Hardware encode (%s) failed, falling back to CPU", hw_accel)
            encode_clip(
                raw_cut, output_path,
                resolution=settings.resolution, fps=settings.fps, codec=settings.codec,
                bitrate=settings.bitrate, hw_accel="cpu", subtitle_ass_path=subtitle_ass,
            )
        else:
            raise

    if progress_cb:
        progress_cb("done", 1.0)

    log.info("Exported clip -> %s", output_path)
    return output_path
