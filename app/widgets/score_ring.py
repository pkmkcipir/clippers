"""ScoreRing: a small radial gauge that draws a 0-100 score as an arc.

This is the app's one deliberate signature element (see app/theme.py's
docstring) -- used specifically for the AI's viral_score output on clip
cards, nowhere else, so it stays meaningful rather than decorative.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtCore import Qt, QRectF


class ScoreRing(QWidget):
    def __init__(self, score: float = 0.0, label: str = "", size: int = 64, parent=None):
        super().__init__(parent)
        self._score = max(0.0, min(100.0, score))
        self._label = label
        self.setFixedSize(size, size)

    def set_score(self, score: float):
        self._score = max(0.0, min(100.0, score))
        self.update()

    def _color_for_score(self) -> QColor:
        # Muted grey-indigo at low scores, warming to coral as it climbs --
        # a "hot clip" reads as visually hot, not just numerically high.
        if self._score >= 70:
            return QColor("#FF6B5B")
        if self._score >= 40:
            return QColor("#6E56F8")
        return QColor("#4A4C5E")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        side = min(self.width(), self.height())
        pen_width = max(int(side * 0.09), 3)
        rect = QRectF(pen_width / 2, pen_width / 2, side - pen_width, side - pen_width)

        track_pen = QPen(QColor("#2E3142"), pen_width, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        score_pen = QPen(self._color_for_score(), pen_width, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(score_pen)
        span = int(-self._score / 100 * 360 * 16)
        painter.drawArc(rect, 90 * 16, span)

        painter.setPen(QColor("#F2F1F7"))
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setPointSize(max(int(side * 0.2), 8))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, str(int(round(self._score))))

        painter.end()
