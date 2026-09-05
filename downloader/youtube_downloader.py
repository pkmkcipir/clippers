"""YouTube import via yt-dlp: metadata lookup + threaded download with
progress / pause / resume / cancel.

Notes on pause/resume semantics (being explicit because yt-dlp itself has
no native "pause" concept):
  - cancel() stops the download outright. Calling download() again on the
    same output path will NOT resume (a fresh file is started).
  - pause() blocks the download loop between chunks without closing the
    connection/partial file; resume() releases it. This is a soft pause
    that works because yt-dlp calls the progress hook frequently during
    a download.
  - If the app is closed entirely mid-download, yt-dlp's own ".part" file
    resume support (continuedl, enabled by default) means re-downloading
    the same URL to the same path picks up roughly where it left off.

Only the input video/audio is ever downloaded -- no re-upload, scraping
of private content, or bypassing of age/region locks is implemented here.
Respect the source video's license and YouTube's Terms of Service before
redistributing anything produced from it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from utils.logger import get_logger

log = get_logger("downloader")


class DownloadCancelled(Exception):
    pass


@dataclass
class VideoInfo:
    title: str = ""
    channel: str = ""
    duration_sec: float = 0.0
    resolution: str = ""
    fps: float = 0.0
    filesize_bytes: int = 0
    thumbnail_url: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class DownloadProgress:
    status: str  # "downloading" | "paused" | "finished" | "error" | "cancelled"
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    filepath: Optional[str] = None
    error: Optional[str] = None


class YouTubeDownloader:
    """One instance manages one download's lifecycle (pause/resume/cancel).
    Create a new instance per download task."""

    def __init__(self):
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused
        self._cancelled = False
        self._thread: Optional[threading.Thread] = None

    # ---- metadata -------------------------------------------------
    @staticmethod
    def get_info(url: str) -> VideoInfo:
        import yt_dlp

        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        width, height = info.get("width"), info.get("height")
        return VideoInfo(
            title=info.get("title") or "",
            channel=info.get("uploader") or info.get("channel") or "",
            duration_sec=float(info.get("duration") or 0),
            resolution=f"{width}x{height}" if width and height else "",
            fps=float(info.get("fps") or 0),
            filesize_bytes=int(info.get("filesize") or info.get("filesize_approx") or 0),
            thumbnail_url=info.get("thumbnail") or "",
            raw=info,
        )

    # ---- download ---------------------------------------------------
    def download(
        self,
        url: str,
        output_dir: str | Path,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        quality: str = "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    ) -> str:
        """Blocking call -- run this in a QThread/worker, not the UI thread."""
        import yt_dlp

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        def hook(d: dict):
            # Soft-pause: block here (between chunks) until resumed.
            self._pause_event.wait()
            if self._cancelled:
                raise DownloadCancelled("Download cancelled by user")

            if not on_progress:
                return

            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                percent = (downloaded / total * 100) if total else 0.0
                on_progress(DownloadProgress(
                    status="downloading",
                    percent=round(percent, 1),
                    speed=_format_speed(d.get("speed")),
                    eta=_format_eta(d.get("eta")),
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                ))
            elif d["status"] == "finished":
                on_progress(DownloadProgress(
                    status="finished", percent=100.0, filepath=d.get("filename"),
                ))

        opts = {
            "format": quality,
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "continuedl": True,  # resume partial .part files across app restarts
            "noprogress": True,
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = ydl.prepare_filename(info)
            log.info("Downloaded: %s", filepath)
            return filepath
        except DownloadCancelled:
            log.info("Download cancelled: %s", url)
            if on_progress:
                on_progress(DownloadProgress(status="cancelled"))
            raise
        except Exception as exc:
            log.exception("Download failed for %s", url)
            if on_progress:
                on_progress(DownloadProgress(status="error", error=str(exc)))
            raise

    def start_async(self, url: str, output_dir: str | Path, on_progress=None, on_done=None, on_error=None):
        """Fire-and-forget convenience wrapper using a plain thread. The GUI
        layer uses services.clip_pipeline's QThread worker instead of this
        for anything that needs to touch Qt widgets from callbacks."""
        def _run():
            try:
                path = self.download(url, output_dir, on_progress=on_progress)
                if on_done:
                    on_done(path)
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    on_error(exc)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return self._thread

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def cancel(self):
        self._cancelled = True
        self._pause_event.set()  # unblock so the hook can see the cancel flag


def _format_speed(bytes_per_sec) -> str:
    if not bytes_per_sec:
        return "-"
    mb = bytes_per_sec / (1024 * 1024)
    return f"{mb:.2f} MB/s" if mb >= 1 else f"{bytes_per_sec / 1024:.0f} KB/s"


def _format_eta(seconds) -> str:
    if not seconds:
        return "-"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"
