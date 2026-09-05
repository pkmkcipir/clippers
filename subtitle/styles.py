"""Subtitle style presets, expressed as ASS (Advanced SubStation Alpha)
style parameters. ASS is used (rather than plain SRT) because it supports
per-word karaoke highlighting, outlines, shadows and precise positioning
that these short-form caption looks need.

Colors are &HAABBGGRR (ASS order is alpha-blue-green-red, not RGB).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SubtitleStyle:
    key: str
    label: str
    font: str = "Arial Black"
    font_size: int = 20
    primary_color: str = "&H00FFFFFF"     # normal text fill
    highlight_color: str = "&H0000D7FF"   # active/karaoke word fill (orange-ish)
    outline_color: str = "&H00000000"
    back_color: str = "&H80000000"        # box background (used if border_style=3)
    outline_width: float = 3.0
    shadow: float = 1.0
    bold: bool = True
    alignment: int = 2          # ASS numpad alignment; 2 = bottom-center
    margin_v: int = 80          # distance from the bottom edge, px
    border_style: int = 1       # 1 = outline+shadow, 3 = opaque box
    karaoke: bool = False       # word-by-word highlight as it's spoken


STYLE_PRESETS: dict[str, SubtitleStyle] = {
    "tiktok": SubtitleStyle(
        key="tiktok", label="TikTok", font="Arial Black", font_size=22,
        primary_color="&H00FFFFFF", outline_color="&H00000000", outline_width=3.5,
        bold=True, alignment=2, margin_v=90, karaoke=False,
    ),
    "capcut": SubtitleStyle(
        key="capcut", label="CapCut", font="Arial Black", font_size=20,
        primary_color="&H00FFFFFF", highlight_color="&H0000D7FF",
        outline_color="&H00000000", outline_width=2.5, bold=True,
        alignment=2, margin_v=100, karaoke=True,
    ),
    "mrbeast": SubtitleStyle(
        key="mrbeast", label="MrBeast", font="Arial Black", font_size=26,
        primary_color="&H0000FFFF", outline_color="&H00000000", outline_width=4.0,
        bold=True, alignment=2, margin_v=110, karaoke=False,
    ),
    "youtube_shorts": SubtitleStyle(
        key="youtube_shorts", label="YouTube Shorts", font="Roboto", font_size=18,
        primary_color="&H00FFFFFF", outline_color="&H00000000", outline_width=2.0,
        bold=False, alignment=2, margin_v=70, karaoke=False,
    ),
    "instagram_reels": SubtitleStyle(
        key="instagram_reels", label="Instagram Reels", font="Helvetica", font_size=18,
        primary_color="&H00FFFFFF", back_color="&HA0000000", outline_width=0.0,
        border_style=3, bold=False, alignment=2, margin_v=70, karaoke=False,
    ),
    "neon": SubtitleStyle(
        key="neon", label="Neon", font="Arial Black", font_size=22,
        primary_color="&H00FF00FF", outline_color="&H00FFFF00", outline_width=2.5,
        bold=True, alignment=2, margin_v=90, karaoke=False,
    ),
    "shadow": SubtitleStyle(
        key="shadow", label="Shadow", font="Arial", font_size=20,
        primary_color="&H00FFFFFF", outline_color="&H00000000", outline_width=1.5,
        shadow=3.0, bold=False, alignment=2, margin_v=80, karaoke=False,
    ),
    "karaoke": SubtitleStyle(
        key="karaoke", label="Karaoke", font="Arial Black", font_size=22,
        primary_color="&H00FFFFFF", highlight_color="&H0000D7FF",
        outline_color="&H00000000", outline_width=3.0, bold=True,
        alignment=2, margin_v=90, karaoke=True,
    ),
}


def get_style(key: str) -> SubtitleStyle:
    return STYLE_PRESETS.get(key, STYLE_PRESETS["tiktok"])


def list_styles() -> list[SubtitleStyle]:
    return list(STYLE_PRESETS.values())
