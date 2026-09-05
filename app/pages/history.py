"""History page: every project/download/clip/export event, newest first."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

from config.i18n import t
from database.db import get_session
from database.models import HistoryEntry

TYPE_LABELS = {
    "id": {"all": "Semua", "project": "Proyek", "download": "Download", "clip": "Klip", "export": "Export"},
    "en": {"all": "All", "project": "Project", "download": "Download", "clip": "Clip", "export": "Export"},
}


class HistoryPage(QWidget):
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
        title = QLabel(t("nav.history", self.language))
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)

        labels = TYPE_LABELS.get(self.language, TYPE_LABELS["en"])
        self.filter_combo = QComboBox()
        for key, label in labels.items():
            self.filter_combo.addItem(label, key)
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.filter_combo)

        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.setProperty("class", "secondary")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        self.table = QTableWidget()
        columns = ["Waktu", "Tipe", "Deskripsi"] if self.language == "id" else ["Time", "Type", "Description"]
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(columns)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.table, 1)

    def refresh(self):
        selected_type = self.filter_combo.currentData()
        with get_session() as session:
            query = session.query(HistoryEntry).order_by(HistoryEntry.created_at.desc())
            if selected_type and selected_type != "all":
                query = query.filter_by(entry_type=selected_type)
            entries = query.limit(500).all()

        labels = TYPE_LABELS.get(self.language, TYPE_LABELS["en"])
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            when = entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "-"
            self.table.setItem(row, 0, QTableWidgetItem(when))
            self.table.setItem(row, 1, QTableWidgetItem(labels.get(entry.entry_type, entry.entry_type)))
            self.table.setItem(row, 2, QTableWidgetItem(entry.description or ""))
