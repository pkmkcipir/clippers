"""Preview panel for the Video Editor: shows whichever clip is active at
the timeline's current playhead position, and can play through the
sequence (including gaps, rendered as a black pause) via QMediaPlayer.

Scope note: this previews the *base video* (Video 1, with Video 2
cutaways taking priority when both are present at a given instant) for
responsiveness -- it does not live-composite text/image overlays or
per-clip effects (those only appear in the final rendered/exported
file via editor/timeline_renderer.py). Trying to re-implement every
ffmpeg filter as a live Qt/GPU effect would be a large project on its
own; showing the plain footage is enough to line up cuts and timing.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, Signal

from app.timeline.model import TimelineProject, TimelineClip
from utils.logger import get_logger

log = get_logger("video_preview")


def find_active_clip(project: TimelineProject, time_sec: float) -> TimelineClip | None:
    """Which clip should be visible at `time_sec`, honoring the same
    'higher video track wins' priority the renderer uses. Returns None
    during a gap (nothing on any video track at that instant)."""
    for track in sorted(project.video_tracks(), key=lambda t: -t.index):
        for clip in track.clips:
            if clip.timeline_start <= time_sec < clip.timeline_end:
                return clip
    return None


class VideoPreviewPanel(QWidget):
    playhead_changed = Signal(float)  # emitted while playing, so the timeline can follow along

    def __init__(self, project_provider, parent=None):
        """project_provider: a zero-arg callable returning the current
        TimelineProject (a callable, not the project itself, since the
        page may swap/reload projects)."""
        super().__init__(parent)
        self._project_provider = project_provider
        self._current_clip_id: str | None = None
        self._is_playing = False
        self._seeking_externally = False

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_widget = QVideoWidget(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.video_widget.setMinimumHeight(220)
        root.addWidget(self.video_widget, 1)

        controls = QHBoxLayout()
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(36)
        self.play_btn.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_btn)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setProperty("role", "muted")
        controls.addWidget(self.time_label)
        controls.addStretch(1)
        root.addLayout(controls)

    # ---- external API: called by the page when the timeline playhead moves ----
    def seek_to(self, time_sec: float):
        project = self._project_provider()
        clip = find_active_clip(project, time_sec)
        self._update_time_label(time_sec, project.total_duration())

        if clip is None:
            self.player.pause()
            self._current_clip_id = None
            return

        offset_in_clip = time_sec - clip.timeline_start
        source_time_ms = int((clip.source_in + offset_in_clip) * 1000)

        if clip.id != self._current_clip_id:
            self._current_clip_id = clip.id
            self._seeking_externally = True
            self.player.setSource(QUrl.fromLocalFile(clip.source_path))
            self._pending_seek_ms = source_time_ms
            if not self._is_playing:
                self.player.pause()
        else:
            self.player.setPosition(source_time_ms)

    def toggle_play(self):
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def play(self):
        project = self._project_provider()
        if find_active_clip(project, self._current_timeline_time()) is None:
            return  # can't play from a gap or past the end; user should seek first
        self._is_playing = True
        self.play_btn.setText("⏸")
        self.player.play()

    def pause(self):
        self._is_playing = False
        self.play_btn.setText("▶")
        self.player.pause()

    def stop(self):
        self.pause()
        self.player.stop()

    # ---- internal: keep the timeline playhead following playback --------
    def _current_timeline_time(self) -> float:
        project = self._project_provider()
        _, clip = project.find(self._current_clip_id) if self._current_clip_id else (None, None)
        if not clip:
            return 0.0
        return clip.timeline_start + (self.player.position() / 1000.0 - clip.source_in)

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._seeking_externally:
            self.player.setPosition(getattr(self, "_pending_seek_ms", 0))
            self._seeking_externally = False
            if self._is_playing:
                self.player.play()

    def _on_player_position_changed(self, position_ms: int):
        if not self._is_playing or self._seeking_externally:
            return
        project = self._project_provider()
        _, clip = project.find(self._current_clip_id) if self._current_clip_id else (None, None)
        if not clip:
            return

        source_time = position_ms / 1000.0
        if source_time >= clip.source_out - 0.05:
            next_time = clip.timeline_end + 0.02
            if find_active_clip(project, next_time) is None and next_time >= project.total_duration() - 0.05:
                self.pause()
                return
            self.playhead_changed.emit(next_time)
            return

        timeline_time = clip.timeline_start + (source_time - clip.source_in)
        self._update_time_label(timeline_time, project.total_duration())
        self.playhead_changed.emit(timeline_time)

    def _update_time_label(self, current: float, total: float):
        def fmt(t):
            m, s = divmod(max(int(t), 0), 60)
            return f"{m}:{s:02d}"
        self.time_label.setText(f"{fmt(current)} / {fmt(total)}")
