"""SQLAlchemy models backing every persisted entity in AI Klipers."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    source_videos = relationship(
        "SourceVideo", back_populates="project", cascade="all, delete-orphan"
    )


class SourceVideo(Base):
    __tablename__ = "source_videos"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)

    source_type = Column(String, default="youtube")  # "youtube" | "local"
    url = Column(String, nullable=True)
    local_path = Column(String, nullable=True)

    title = Column(String, default="")
    channel = Column(String, default="")
    duration_sec = Column(Float, default=0.0)
    resolution = Column(String, default="")
    fps = Column(Float, default=0.0)
    filesize_bytes = Column(Integer, default=0)
    thumbnail_url = Column(String, default="")

    transcript_path = Column(String, nullable=True)  # JSON transcript on disk
    status = Column(String, default="pending")  # pending/downloading/ready/failed
    created_at = Column(DateTime, default=_now)

    project = relationship("Project", back_populates="source_videos")
    clips = relationship("Clip", back_populates="source_video", cascade="all, delete-orphan")
    download_tasks = relationship(
        "DownloadTask", back_populates="source_video", cascade="all, delete-orphan"
    )


class Clip(Base):
    __tablename__ = "clips"

    id = Column(String, primary_key=True, default=_uuid)
    source_video_id = Column(String, ForeignKey("source_videos.id"), nullable=False)

    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)

    viral_score = Column(Float, default=0.0)      # 0-100
    confidence_score = Column(Float, default=0.0)  # 0-100
    transcript_text = Column(Text, default="")
    suggested_title = Column(String, default="")
    suggested_hashtags = Column(String, default="")
    suggested_caption = Column(String, default="")
    suggested_description = Column(Text, default="")
    suggested_keywords = Column(String, default="")
    caption_source = Column(String, default="")  # "heuristic" | "llm" | "" (not generated yet)

    thumbnail_path = Column(String, nullable=True)
    output_path = Column(String, nullable=True)
    subtitle_style = Column(String, default="tiktok")
    status = Column(String, default="candidate")  # candidate/exported/discarded
    created_at = Column(DateTime, default=_now)

    source_video = relationship("SourceVideo", back_populates="clips")
    export_jobs = relationship("ExportJob", back_populates="clip", cascade="all, delete-orphan")


class DownloadTask(Base):
    __tablename__ = "download_tasks"

    id = Column(String, primary_key=True, default=_uuid)
    source_video_id = Column(String, ForeignKey("source_videos.id"), nullable=False)

    url = Column(String, nullable=False)
    status = Column(String, default="queued")  # queued/downloading/paused/done/cancelled/error
    progress_percent = Column(Float, default=0.0)
    speed = Column(String, default="")
    eta = Column(String, default="")
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    source_video = relationship("SourceVideo", back_populates="download_tasks")


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id = Column(String, primary_key=True, default=_uuid)
    clip_id = Column(String, ForeignKey("clips.id"), nullable=False)

    resolution = Column(String, default="1080p")
    fps = Column(Integer, default=30)
    codec = Column(String, default="h264")
    bitrate = Column(String, default="auto")
    hw_accel = Column(String, default="auto")

    output_path = Column(String, nullable=True)
    status = Column(String, default="queued")  # queued/running/done/error
    progress_percent = Column(Float, default=0.0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

    clip = relationship("Clip", back_populates="export_jobs")


class EditorTimeline(Base):
    __tablename__ = "editor_timelines"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    name = Column(String, default="Untitled Timeline")
    data_json = Column(Text, nullable=False)  # TimelineProject.to_dict(), JSON-encoded
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class HistoryEntry(Base):
    __tablename__ = "history_entries"

    id = Column(String, primary_key=True, default=_uuid)
    entry_type = Column(String, nullable=False)  # project/download/clip/export
    reference_id = Column(String, nullable=True)
    description = Column(String, default="")
    created_at = Column(DateTime, default=_now)
