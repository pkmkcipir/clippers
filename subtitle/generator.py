"""Generate .srt and .ass subtitle files from a Transcript (see
ai/transcription.py). .ass is what actually gets burned into exported
clips (via editor.ffmpeg_utils.encode_clip's `ass` filter) since it's the
only format that carries the style presets and karaoke timing.
"""
from __future__ import annotations

from pathlib import Path

from ai.transcription import Sentence
from subtitle.styles import SubtitleStyle, get_style
from utils.logger import get_logger

log = get_logger("subtitle_generator")


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_timestamp(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(sentences: list[Sentence], output_path: str | Path) -> str:
    lines = []
    for i, s in enumerate(sentences, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(s.start)} --> {_srt_timestamp(s.end)}")
        lines.append(s.text.strip())
        lines.append("")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return str(output_path)


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{primary},{highlight},{outline},{back},{bold},0,0,0,100,100,0,0,{border_style},{outline_w},{shadow},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _karaoke_text(sentence: Sentence) -> str:
    """Build {\\k<centiseconds>}word tags for per-word ASS karaoke highlighting."""
    if not sentence.words:
        return sentence.text.strip()
    parts = []
    for w in sentence.words:
        dur_cs = max(int(round((w.end - w.start) * 100)), 1)
        parts.append(f"{{\\k{dur_cs}}}{w.text.strip()}")
    return " ".join(parts)


def write_ass(sentences: list[Sentence], output_path: str | Path, style_key: str = "tiktok") -> str:
    style: SubtitleStyle = get_style(style_key)

    header = _ASS_HEADER.format(
        font=style.font, font_size=style.font_size,
        primary=style.primary_color, highlight=style.highlight_color,
        outline=style.outline_color, back=style.back_color,
        bold=-1 if style.bold else 0, border_style=style.border_style,
        outline_w=style.outline_width, shadow=style.shadow,
        alignment=style.alignment, margin_v=style.margin_v,
    )

    events = []
    for s in sentences:
        text = _karaoke_text(s) if style.karaoke else s.text.strip()
        events.append(
            f"Dialogue: 0,{_ass_timestamp(s.start)},{_ass_timestamp(s.end)},Default,,0,0,0,,{text}"
        )

    Path(output_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    log.info("Wrote ASS subtitle (%s style) -> %s", style.label, output_path)
    return str(output_path)


def generate(sentences: list[Sentence], output_dir: str | Path, basename: str,
             style_key: str = "tiktok") -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = write_srt(sentences, output_dir / f"{basename}.srt")
    ass_path = write_ass(sentences, output_dir / f"{basename}.ass", style_key=style_key)
    return {"srt": srt_path, "ass": ass_path}
