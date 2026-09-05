"""Import Video page: paste a YouTube URL (fetch info -> download with
progress/pause/resume/cancel) or import local MP4/MKV/AVI/MOV files or a
whole folder.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QFrame,
    QProgressBar, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Signal, QThread, Qt

from config.i18n import t
from database.db import get_session
from database.models import SourceVideo, DownloadTask, HistoryEntry
from downloader.youtube_downloader import YouTubeDownloader, VideoInfo, DownloadProgress
from editor.ffmpeg_utils import get_media_info
from utils.logger import get_logger

log = get_logger("import_video_page")

LOCAL_EXTENSIONS = ["*.mp4", "*.mkv", "*.avi", "*.mov"]


class InfoFetchWorker(QThread):
    done = Signal(object)   # VideoInfo
    error = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            info = YouTubeDownloader.get_info(self.url)
            self.done.emit(info)
        except Exception as exc:
            self.error.emit(str(exc))


class DownloadWorker(QThread):
    progress = Signal(object)  # DownloadProgress
    done = Signal(str)         # filepath
    error = Signal(str)

    def __init__(self, url: str, output_dir: str):
        super().__init__()
        self.url = url
        self.output_dir = output_dir
        self.downloader = YouTubeDownloader()

    def run(self):
        try:
            path = self.downloader.download(self.url, self.output_dir, on_progress=self.progress.emit)
            self.done.emit(path)
        except Exception as exc:
            self.error.emit(str(exc))

    def pause(self):
        self.downloader.pause()

    def resume(self):
        self.downloader.resume()

    def cancel(self):
        self.downloader.cancel()


class ImportVideoPage(QWidget):
    video_imported = Signal(str, str)  # source_video_id, local_path

    def __init__(self, language: str, settings, current_project_id: str | None = None, parent=None):
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self.current_project_id = current_project_id
        self._video_info: VideoInfo | None = None
        self._download_worker: DownloadWorker | None = None
        self._info_worker: InfoFetchWorker | None = None
        self._build_ui()

    def set_project(self, project_id: str):
        self.current_project_id = project_id

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(t("import.title", self.language))
        title.setProperty("role", "title")
        root.addWidget(title)

        # --- YouTube URL row ---
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(t("import.url_placeholder", self.language))
        fetch_btn = QPushButton(t("import.fetch_info", self.language))
        fetch_btn.setProperty("class", "secondary")
        fetch_btn.clicked.connect(self._fetch_info)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(fetch_btn)
        root.addLayout(url_row)

        # --- Local import row ---
        local_row = QHBoxLayout()
        file_btn = QPushButton(t("import.browse_file", self.language))
        file_btn.setProperty("class", "secondary")
        file_btn.clicked.connect(self._browse_file)
        folder_btn = QPushButton(t("import.browse_folder", self.language))
        folder_btn.setProperty("class", "secondary")
        folder_btn.clicked.connect(self._browse_folder)
        local_row.addWidget(file_btn)
        local_row.addWidget(folder_btn)
        local_row.addStretch(1)
        root.addLayout(local_row)

        # --- Metadata card ---
        self.info_card = QFrame()
        self.info_card.setProperty("class", "card")
        self.info_card.setVisible(False)
        info_layout = QVBoxLayout(self.info_card)
        self.info_title = QLabel("")
        self.info_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.info_meta = QLabel("")
        self.info_meta.setProperty("role", "muted")
        info_layout.addWidget(self.info_title)
        info_layout.addWidget(self.info_meta)

        dl_row = QHBoxLayout()
        self.download_btn = QPushButton(t("import.download", self.language))
        self.download_btn.setProperty("class", "primary")
        self.download_btn.clicked.connect(self._start_download)
        self.pause_btn = QPushButton(t("import.pause", self.language))
        self.pause_btn.setProperty("class", "secondary")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.pause_btn.setVisible(False)
        self.cancel_btn = QPushButton(t("import.cancel", self.language))
        self.cancel_btn.setProperty("class", "danger")
        self.cancel_btn.clicked.connect(self._cancel_download)
        self.cancel_btn.setVisible(False)
        dl_row.addWidget(self.download_btn)
        dl_row.addWidget(self.pause_btn)
        dl_row.addWidget(self.cancel_btn)
        dl_row.addStretch(1)
        info_layout.addLayout(dl_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        info_layout.addWidget(self.progress_bar)

        self.progress_detail = QLabel("")
        self.progress_detail.setProperty("role", "muted")
        self.progress_detail.setStyleSheet("font-size: 11px;")
        info_layout.addWidget(self.progress_detail)

        root.addWidget(self.info_card)
        root.addStretch(1)

    # ---- YouTube flow -------------------------------------------------
    def _fetch_info(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        self.url_edit.setEnabled(False)
        self._info_worker = InfoFetchWorker(url)
        self._info_worker.done.connect(self._on_info_ready)
        self._info_worker.error.connect(self._on_info_error)
        self._info_worker.start()

    def _on_info_ready(self, info: VideoInfo):
        self.url_edit.setEnabled(True)
        self._video_info = info
        self.info_title.setText(info.title or "(tanpa judul)")
        duration_min = int(info.duration_sec // 60)
        duration_sec = int(info.duration_sec % 60)
        size_mb = round(info.filesize_bytes / (1024 * 1024), 1) if info.filesize_bytes else "?"
        self.info_meta.setText(
            f"{info.channel}  ·  {duration_min}m{duration_sec:02d}s  ·  "
            f"{info.resolution or '?'}  ·  {info.fps or '?'} FPS  ·  {size_mb} MB"
        )
        self.info_card.setVisible(True)

    def _on_info_error(self, message: str):
        self.url_edit.setEnabled(True)
        QMessageBox.critical(self, "AI Klipers", f"Gagal mengambil info video:\n{message}"
                              if self.language == "id" else f"Failed to fetch video info:\n{message}")

    def _start_download(self):
        url = self.url_edit.text().strip()
        if not url or not self.current_project_id:
            QMessageBox.warning(self, "AI Klipers", "Pilih/buat proyek terlebih dahulu dari Dashboard."
                                 if self.language == "id" else "Select/create a project from the Dashboard first.")
            return

        with get_session() as session:
            source_video = SourceVideo(
                project_id=self.current_project_id, source_type="youtube", url=url,
                title=self._video_info.title if self._video_info else "",
                channel=self._video_info.channel if self._video_info else "",
                duration_sec=self._video_info.duration_sec if self._video_info else 0,
                resolution=self._video_info.resolution if self._video_info else "",
                fps=self._video_info.fps if self._video_info else 0,
                status="downloading",
            )
            session.add(source_video)
            session.commit()
            session.refresh(source_video)

            task = DownloadTask(source_video_id=source_video.id, url=url, status="downloading")
            session.add(task)
            session.commit()
            session.refresh(task)

            self._source_video_id = source_video.id
            self._download_task_id = task.id

        output_dir = str(Path(self.settings.output_folder) / "downloads")
        self._download_worker = DownloadWorker(url, output_dir)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

        self.download_btn.setVisible(False)
        self.pause_btn.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)

    def _toggle_pause(self):
        if not self._download_worker:
            return
        if self.pause_btn.text() == t("import.pause", self.language):
            self._download_worker.pause()
            self.pause_btn.setText(t("import.resume", self.language))
        else:
            self._download_worker.resume()
            self.pause_btn.setText(t("import.pause", self.language))

    def _cancel_download(self):
        if self._download_worker:
            self._download_worker.cancel()
        self._reset_download_ui()

    def _on_download_progress(self, prog: DownloadProgress):
        if prog.status == "downloading":
            self.progress_bar.setValue(int(prog.percent))
            self.progress_detail.setText(f"{prog.percent:.1f}%  ·  {prog.speed}  ·  ETA {prog.eta}")

    def _on_download_done(self, filepath: str):
        try:
            media_info = get_media_info(filepath)
        except Exception:
            media_info = {}

        with get_session() as session:
            source_video = session.get(SourceVideo, self._source_video_id)
            if source_video:
                source_video.local_path = filepath
                source_video.status = "ready"
                if media_info:
                    source_video.duration_sec = media_info.get("duration", source_video.duration_sec)
                    source_video.resolution = f"{media_info.get('width')}x{media_info.get('height')}"
                    source_video.fps = media_info.get("fps", source_video.fps)
            task = session.get(DownloadTask, self._download_task_id)
            if task:
                task.status = "done"
                task.progress_percent = 100.0
            session.add(HistoryEntry(entry_type="download", reference_id=self._source_video_id,
                                      description=f"Selesai download: {self._video_info.title if self._video_info else filepath}"))
            session.commit()

        self.progress_detail.setText("Selesai ✓" if self.language == "id" else "Done ✓")
        self.progress_bar.setValue(100)
        self.video_imported.emit(self._source_video_id, filepath)
        self._reset_download_ui(keep_progress=True)

    def _on_download_error(self, message: str):
        with get_session() as session:
            task = session.get(DownloadTask, getattr(self, "_download_task_id", None))
            if task:
                task.status = "error"
                task.error_message = message
                session.commit()
        QMessageBox.critical(self, "AI Klipers", f"Download gagal:\n{message}"
                              if self.language == "id" else f"Download failed:\n{message}")
        self._reset_download_ui()

    def _reset_download_ui(self, keep_progress: bool = False):
        self.download_btn.setVisible(True)
        self.pause_btn.setVisible(False)
        self.pause_btn.setText(t("import.pause", self.language))
        self.cancel_btn.setVisible(False)
        if not keep_progress:
            self.progress_bar.setVisible(False)
            self.progress_detail.setText("")

    # ---- Local import flow --------------------------------------------
    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("import.browse_file", self.language), "",
            "Video files (*.mp4 *.mkv *.avi *.mov)"
        )
        if path:
            self._import_local_path(path)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, t("import.browse_folder", self.language))
        if not folder:
            return
        video_paths = [
            str(p) for ext in ("mp4", "mkv", "avi", "mov")
            for p in Path(folder).glob(f"*.{ext}")
        ]
        for path in video_paths:
            self._import_local_path(path)
        if video_paths:
            QMessageBox.information(self, "AI Klipers",
                                     f"{len(video_paths)} video diimpor dari folder."
                                     if self.language == "id" else f"{len(video_paths)} videos imported from folder.")

    def _import_local_path(self, path: str):
        if not self.current_project_id:
            QMessageBox.warning(self, "AI Klipers", "Pilih/buat proyek terlebih dahulu dari Dashboard."
                                 if self.language == "id" else "Select/create a project from the Dashboard first.")
            return
        try:
            media_info = get_media_info(path)
        except Exception:
            media_info = {}

        with get_session() as session:
            source_video = SourceVideo(
                project_id=self.current_project_id, source_type="local", local_path=path,
                title=Path(path).stem, status="ready",
                duration_sec=media_info.get("duration", 0.0),
                resolution=f"{media_info.get('width', 0)}x{media_info.get('height', 0)}",
                fps=media_info.get("fps", 0.0),
            )
            session.add(source_video)
            session.add(HistoryEntry(entry_type="download", description=f"Import lokal: {Path(path).name}"))
            session.commit()
            session.refresh(source_video)

        self.video_imported.emit(source_video.id, path)
