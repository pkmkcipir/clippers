"""Pages that are honestly not fully built yet in this iteration.

Rather than hide the gap, each placeholder states plainly what backend
groundwork already exists (e.g. subtitle/styles.py has all 8 presets
implemented and burn-in tested end-to-end) versus what UI is still
needed (a live timeline to preview/adjust them). See README.md's
roadmap for the suggested build order.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea, QGridLayout,
)
from PySide6.QtCore import Qt

from config.i18n import t
from subtitle.styles import list_styles


class PlaceholderPage(QWidget):
    def __init__(self, language: str, title: str, planned_features: list[str], parent=None):
        super().__init__(parent)
        self.language = language
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title_lbl = QLabel(title)
        title_lbl.setProperty("role", "title")
        root.addWidget(title_lbl)

        card = QFrame()
        card.setProperty("class", "card")
        card_layout = QVBoxLayout(card)

        badge = QLabel(t("phase2.title", language))
        badge.setStyleSheet("color: #6E56F8; font-weight: 700; font-size: 13px;")
        card_layout.addWidget(badge)

        body = QLabel(t("phase2.body", language))
        body.setWordWrap(True)
        body.setProperty("role", "muted")
        card_layout.addWidget(body)

        if planned_features:
            heading = QLabel("Rencana fitur:" if language == "id" else "Planned features:")
            heading.setStyleSheet("font-weight: 600; margin-top: 8px;")
            card_layout.addWidget(heading)
            for feat in planned_features:
                lbl = QLabel(f"•  {feat}")
                lbl.setProperty("role", "muted")
                card_layout.addWidget(lbl)

        root.addWidget(card)
        root.addStretch(1)


class StyleSwatch(QFrame):
    def __init__(self, style, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setFixedHeight(90)
        layout = QVBoxLayout(self)
        name = QLabel(style.label)
        name.setStyleSheet("font-weight: 600;")
        font_lbl = QLabel(f"{style.font} · {style.font_size}px")
        font_lbl.setProperty("role", "muted")
        font_lbl.setStyleSheet("font-size: 11px;")
        preview = QLabel("Contoh subtitle" if style.key != "karaoke" else "Con-toh sub-ti-tle")
        preview.setStyleSheet(
            f"background-color: #14151C; color: white; font-weight: {'700' if style.bold else '400'}; "
            f"padding: 6px; border-radius: 6px;"
        )
        layout.addWidget(name)
        layout.addWidget(font_lbl)
        layout.addWidget(preview)


class SubtitleStylePage(QWidget):
    """Real gallery of the 8 implemented ASS style presets (subtitle/styles.py).
    Picking a default here is functional; a live video preview + per-word
    timeline editing is the Phase 2 piece that isn't built yet."""

    def __init__(self, language: str, parent=None):
        super().__init__(parent)
        self.language = language
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(t("nav.subtitle", language))
        title.setProperty("role", "title")
        root.addWidget(title)

        info = QLabel(
            "8 style siap dipakai saat Export (lihat kolom Subtitle Style). "
            "Preview video langsung + edit posisi/animasi per kata adalah bagian Fase 2."
            if language == "id" else
            "All 8 styles are ready to use from the Export page's Subtitle Style dropdown. "
            "A live video preview + per-word position/animation editing is the Phase 2 piece."
        )
        info.setWordWrap(True)
        info.setProperty("role", "muted")
        root.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setSpacing(12)
        for i, style in enumerate(list_styles()):
            grid.addWidget(StyleSwatch(style), i // 3, i % 3)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)
