"""Dashboard page: quick stats + recent projects + create-new-project."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea,
    QGridLayout, QDialog, QLineEdit, QTextEdit, QFormLayout, QMessageBox,
)
from PySide6.QtCore import Signal, Qt

from config.i18n import t
from database.db import get_session
from database.models import Project, SourceVideo, Clip, ExportJob, HistoryEntry


class NewProjectDialog(QDialog):
    def __init__(self, language: str, parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle(t("dashboard.new_project", language))
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Podcast Eps 12" if language == "id" else "Podcast Ep 12")
        self.desc_edit = QTextEdit()
        self.desc_edit.setFixedHeight(70)
        form.addRow("Nama proyek" if language == "id" else "Project name", self.name_edit)
        form.addRow("Deskripsi" if language == "id" else "Description", self.desc_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton("Batal" if language == "id" else "Cancel")
        cancel_btn.setProperty("class", "secondary")
        cancel_btn.clicked.connect(self.reject)
        create_btn = QPushButton("Buat" if language == "id" else "Create")
        create_btn.setProperty("class", "primary")
        create_btn.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(create_btn)
        layout.addLayout(buttons)

    def get_values(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.desc_edit.toPlainText().strip()


class ProjectCard(QFrame):
    clicked = Signal(str)

    def __init__(self, project: Project, video_count: int, clip_count: int, parent=None):
        super().__init__(parent)
        self.project_id = project.id
        self.setProperty("class", "card")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(110)

        layout = QVBoxLayout(self)
        name = QLabel(project.name)
        name.setStyleSheet("font-size: 15px; font-weight: 600;")
        desc = QLabel((project.description or "-")[:80])
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        meta = QLabel(f"{video_count} video · {clip_count} klip")
        meta.setProperty("role", "muted")
        meta.setStyleSheet("font-size: 11px;")

        layout.addWidget(name)
        layout.addWidget(desc)
        layout.addStretch(1)
        layout.addWidget(meta)

    def mousePressEvent(self, event):
        self.clicked.emit(self.project_id)
        super().mousePressEvent(event)


class StatCard(QFrame):
    def __init__(self, value: str, label: str, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setFixedHeight(80)
        layout = QVBoxLayout(self)
        value_lbl = QLabel(value)
        value_lbl.setStyleSheet("font-size: 24px; font-weight: 700;")
        label_lbl = QLabel(label)
        label_lbl.setProperty("role", "muted")
        layout.addWidget(value_lbl)
        layout.addWidget(label_lbl)


class DashboardPage(QWidget):
    project_selected = Signal(str)  # navigates to Import Video with this project

    def __init__(self, language: str = "id", parent=None):
        super().__init__(parent)
        self.language = language
        self.setObjectName("pageRoot")
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel(t("dashboard.title", self.language))
        title.setProperty("role", "title")
        new_btn = QPushButton(t("dashboard.new_project", self.language))
        new_btn.setProperty("class", "primary")
        new_btn.clicked.connect(self._create_project)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(new_btn)
        root.addLayout(header)

        self.stats_row = QHBoxLayout()
        root.addLayout(self.stats_row)

        recent_label = QLabel(t("dashboard.recent_projects", self.language))
        recent_label.setStyleSheet("font-size: 15px; font-weight: 600; margin-top: 8px;")
        root.addWidget(recent_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(12)
        scroll.setWidget(self.grid_host)
        root.addWidget(scroll, 1)

        self.empty_label = QLabel(t("dashboard.no_projects", self.language))
        self.empty_label.setProperty("role", "muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_label)
        self.empty_label.hide()

    def _create_project(self):
        dialog = NewProjectDialog(self.language, self)
        if dialog.exec() == QDialog.Accepted:
            name, desc = dialog.get_values()
            if not name:
                QMessageBox.warning(self, "AI Klipers", "Nama proyek tidak boleh kosong."
                                     if self.language == "id" else "Project name is required.")
                return
            with get_session() as session:
                project = Project(name=name, description=desc)
                session.add(project)
                session.add(HistoryEntry(entry_type="project", description=f"Membuat proyek '{name}'"))
                session.commit()
                session.refresh(project)
            self.refresh()
            self.project_selected.emit(project.id)

    def refresh(self):
        # Clear existing grid widgets.
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        with get_session() as session:
            projects = session.query(Project).order_by(Project.updated_at.desc()).all()
            total_clips = session.query(Clip).count()
            total_exports = session.query(ExportJob).count()

            self.stats_row.addWidget(StatCard(str(len(projects)), t("dashboard.stat.projects", self.language)))
            self.stats_row.addWidget(StatCard(str(total_clips), t("dashboard.stat.clips", self.language)))
            self.stats_row.addWidget(StatCard(str(total_exports), t("dashboard.stat.exports", self.language)))
            self.stats_row.addStretch(1)

            self.empty_label.setVisible(len(projects) == 0)

            for i, project in enumerate(projects):
                video_count = session.query(SourceVideo).filter_by(project_id=project.id).count()
                clip_count = (
                    session.query(Clip).join(SourceVideo)
                    .filter(SourceVideo.project_id == project.id).count()
                )
                card = ProjectCard(project, video_count, clip_count)
                card.clicked.connect(self.project_selected.emit)
                self.grid.addWidget(card, i // 3, i % 3)
