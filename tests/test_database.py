"""Tests for database/models.py + db.py. Run with: pytest tests/test_database.py"""
import pytest

from database.db import init_db, get_session
from database.models import Project, SourceVideo, Clip, DownloadTask, ExportJob, HistoryEntry


@pytest.fixture(autouse=True)
def fresh_memory_db():
    """Each test gets its own isolated in-memory SQLite database."""
    init_db("sqlite:///:memory:")
    yield


def test_create_project():
    with get_session() as session:
        project = Project(name="Test Project", description="desc")
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        assert session.query(Project).count() == 1


def test_project_source_video_clip_chain():
    with get_session() as session:
        project = Project(name="Podcast")
        session.add(project)
        session.commit()
        session.refresh(project)

        video = SourceVideo(project_id=project.id, source_type="youtube",
                             url="https://youtube.com/watch?v=x", title="Ep 1", duration_sec=600)
        session.add(video)
        session.commit()
        session.refresh(video)

        clip = Clip(source_video_id=video.id, start_time=10, end_time=40, duration=30,
                     viral_score=91.2, confidence_score=80.0)
        session.add(clip)
        session.commit()

        assert session.query(SourceVideo).filter_by(project_id=project.id).count() == 1
        stored_clip = session.query(Clip).filter_by(source_video_id=video.id).first()
        assert stored_clip.viral_score == pytest.approx(91.2)


def test_cascade_delete_project_removes_children():
    with get_session() as session:
        project = Project(name="ToDelete")
        session.add(project)
        session.commit()
        session.refresh(project)

        video = SourceVideo(project_id=project.id, source_type="local", local_path="/tmp/x.mp4")
        session.add(video)
        session.commit()
        session.refresh(video)

        clip = Clip(source_video_id=video.id, start_time=0, end_time=10, duration=10)
        session.add(clip)
        session.commit()

        session.delete(project)
        session.commit()

        assert session.query(SourceVideo).count() == 0
        assert session.query(Clip).count() == 0


def test_download_task_and_export_job():
    with get_session() as session:
        project = Project(name="P")
        session.add(project); session.commit(); session.refresh(project)
        video = SourceVideo(project_id=project.id, source_type="youtube", url="https://x")
        session.add(video); session.commit(); session.refresh(video)

        task = DownloadTask(source_video_id=video.id, url="https://x", status="downloading", progress_percent=42.0)
        session.add(task)

        clip = Clip(source_video_id=video.id, start_time=0, end_time=15, duration=15)
        session.add(clip); session.commit(); session.refresh(clip)

        job = ExportJob(clip_id=clip.id, resolution="1080p", status="queued")
        session.add(job)
        session.add(HistoryEntry(entry_type="export", reference_id=job.id, description="queued export"))
        session.commit()

        assert session.query(DownloadTask).filter_by(status="downloading").count() == 1
        assert session.query(ExportJob).filter_by(resolution="1080p").count() == 1
        assert session.query(HistoryEntry).count() == 1
