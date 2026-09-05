"""Left sidebar navigation -- matches the menu structure from the spec:
Dashboard, Import Video, AI Clip Generator, Video Editor, Subtitle,
Export, Batch Export, History, Download Manager, Settings.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QButtonGroup, QFrame
from PySide6.QtCore import Signal, Qt

from config.i18n import t

NAV_ITEMS = [
    ("dashboard", "nav.dashboard", "🏠"),
    ("import_video", "nav.import_video", "⬇"),
    ("ai_clip_generator", "nav.ai_clip_generator", "✨"),
    ("video_editor", "nav.video_editor", "🎬"),
    ("subtitle", "nav.subtitle", "💬"),
    ("export", "nav.export", "⬆"),
    ("batch_export", "nav.batch_export", "📦"),
    ("history", "nav.history", "🕘"),
    ("download_manager", "nav.download_manager", "⇩"),
    ("settings", "nav.settings", "⚙"),
]


class Sidebar(QWidget):
    page_changed = Signal(str)

    def __init__(self, language: str = "id", parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)
        self._language = language
        self._buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(2)

        brand = QLabel(f"🎞  {t('app.title', language)}")
        brand.setObjectName("sidebarBrand")
        layout.addWidget(brand)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #2E3142; margin: 0 12px 8px 12px;")
        layout.addWidget(divider)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for key, label_key, icon in NAV_ITEMS:
            btn = QPushButton(f"  {icon}   {t(label_key, language)}")
            btn.setCheckable(True)
            btn.setProperty("class", "navItem")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, k=key: self.page_changed.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            layout.addWidget(btn)

        layout.addStretch(1)
        self._buttons["dashboard"].setChecked(True)

    def set_active(self, key: str):
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def retranslate(self, language: str):
        self._language = language
        for (key, label_key, icon) in NAV_ITEMS:
            self._buttons[key].setText(f"  {icon}   {t(label_key, language)}")
