"""Download Manager page: a table of every DownloadTask (queued, in
progress, done, error) with a retry action. Live pause/resume controls
for an *active* download live on the Import Video page (where the worker
object actually is); this page is the queue/history view across all of
them, refreshed from the database.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt

from config.i18n import t
from database.db import get_session
from database.models import DownloadTask, SourceVideo

STATUS_LABELS = {
    "id": {"queued": "Menunggu", "downloading": "Mengunduh", "paused": "Dijeda",
           "done": "Selesai", "cancelled": "Dibatalkan", "error": "Gagal"},
    "en": {"queued": "Queued", "downloading": "Downloading", "paused": "Paused",
           "done": "Done", "cancelled": "Cancelled", "error": "Error"},
}

COLUMNS_ID = ["Video", "Status", "Progress", "Kecepatan", "ETA", "Dibuat"]
COLUMNS_EN = ["Video", "Status", "Progress", "Speed", "ETA", "Created"]


class DownloadManagerPage(QWidget):
    def __init__(self, language: str, parent=None):
        super().__init__(parent)
        self.language = language
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel(t("nav.download_manager", self.language))
        title.setProperty("role", "title")
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.setProperty("class", "secondary")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        self.table = QTableWidget()
        columns = COLUMNS_ID if self.language == "id" else COLUMNS_EN
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        root.addWidget(self.table, 1)

    def refresh(self):
        labels = STATUS_LABELS.get(self.language, STATUS_LABELS["en"])
        with get_session() as session:
            tasks = session.query(DownloadTask).order_by(DownloadTask.created_at.desc()).all()
            self.table.setRowCount(len(tasks))
            for row, task in enumerate(tasks):
                video = session.get(SourceVideo, task.source_video_id)
                name = (video.title if video and video.title else task.url)

                self.table.setItem(row, 0, QTableWidgetItem(name))
                self.table.setItem(row, 1, QTableWidgetItem(labels.get(task.status, task.status)))
                self.table.setItem(row, 2, QTableWidgetItem(f"{task.progress_percent:.0f}%"))
                self.table.setItem(row, 3, QTableWidgetItem(task.speed or "-"))
                self.table.setItem(row, 4, QTableWidgetItem(task.eta or "-"))
                created = task.created_at.strftime("%Y-%m-%d %H:%M") if task.created_at else "-"
                self.table.setItem(row, 5, QTableWidgetItem(created))
