"""Main application window: sidebar navigation + a QStackedWidget holding
every page. Also owns the "current project" concept, propagated to
whichever pages need it (Import Video, AI Clip Generator, Export).
"""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QStackedWidget, QMessageBox, QApplication
from PySide6.QtGui import QIcon

from config.i18n import t
from app.theme import get_stylesheet
from app.widgets.sidebar import Sidebar
from app.pages import (
    DashboardPage, ImportVideoPage, AIClipGeneratorPage, DownloadManagerPage,
    ExportPage, HistoryPage, SettingsPage, VideoEditorPage, BatchExportPage, SubtitleStylePage,
)


class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.current_project_id: str | None = None

        self.setWindowTitle(t("app.title", settings.language))
        self.resize(settings.window_width, settings.window_height)
        self.setStyleSheet(get_stylesheet(settings.theme))

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)

        self.sidebar = Sidebar(language=settings.language)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self._build_pages()
        self.sidebar.page_changed.connect(self._navigate)
        self._navigate("dashboard")

    def _build_pages(self):
        lang = self.settings.language
        self.pages: dict[str, QWidget] = {
            "dashboard": DashboardPage(lang),
            "import_video": ImportVideoPage(lang, self.settings),
            "ai_clip_generator": AIClipGeneratorPage(lang, self.settings),
            "video_editor": VideoEditorPage(lang, self.settings),
            "subtitle": SubtitleStylePage(lang),
            "export": ExportPage(lang, self.settings),
            "batch_export": BatchExportPage(lang, self.settings),
            "history": HistoryPage(lang),
            "download_manager": DownloadManagerPage(lang),
            "settings": SettingsPage(lang, self.settings),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # Cross-page wiring.
        self.pages["dashboard"].project_selected.connect(self._on_project_selected)
        self.pages["import_video"].video_imported.connect(self._on_video_imported)
        self.pages["settings"].theme_changed.connect(self._on_theme_changed)
        self.pages["settings"].language_changed.connect(self._on_language_changed)

    def _navigate(self, key: str):
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        if hasattr(page, "refresh"):
            page.refresh()

    def _on_project_selected(self, project_id: str):
        self.current_project_id = project_id
        for key in ("import_video", "ai_clip_generator", "export", "video_editor", "batch_export"):
            page = self.pages.get(key)
            if page and hasattr(page, "set_project"):
                page.set_project(project_id)
        self._navigate("import_video")

    def _on_video_imported(self, source_video_id: str, path: str):
        clip_gen = self.pages.get("ai_clip_generator")
        if clip_gen:
            clip_gen.reload_sources()
        self._navigate("ai_clip_generator")

    def _on_theme_changed(self, theme: str):
        self.setStyleSheet(get_stylesheet(theme))

    def _on_language_changed(self, language: str):
        # Full relabel of every page needs each page to expose a
        # retranslate() hook; sidebar already supports it live.
        self.sidebar.retranslate(language)

    def closeEvent(self, event):
        """Two things worth doing before the process tears down:
        1. Warn if Batch Export has a job actively running, since closing
           mid-job interrupts it (no resume) -- this is a real "you might
           lose work" moment, not just a cleanup nicety.
        2. Drain the Qt event loop briefly so any short-lived background
           worker very close to finishing (e.g. an effect preview render,
           see app/widgets/effect_preview.py) gets a chance to complete
           and clean up its QThread normally, rather than the interpreter
           tearing it down mid-flight -- the same class of issue documented
           in the README under "Batch Export: design notes" and
           "AI Caption Generator", just triggered by app shutdown instead
           of a fast succession of UI events."""
        batch_page = self.pages.get("batch_export")
        if batch_page and any(j.status == "running" for j in batch_page.queue.jobs):
            reply = QMessageBox.question(
                self, "AI Klipers",
                "Batch Export masih berjalan. Yakin mau keluar? Item yang sedang diproses akan terhenti."
                if self.settings.language == "id" else
                "Batch Export is still running. Quit anyway? The in-progress item will be interrupted.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return

        app = QApplication.instance()
        if app is not None:
            for _ in range(5):
                app.processEvents()
        event.accept()
