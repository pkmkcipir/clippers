"""Tests for database/db.py's lightweight auto-migration -- this matters
because AI Klipers is a desktop app with a persistent on-disk database;
someone's real ai_klipers.db from an earlier version must gain new
columns (like the AI Caption Generator's Clip fields) without losing
data or crashing. Run with: pytest tests/test_db_migration.py -v
"""
import sqlite3
from pathlib import Path

import pytest

from database.db import init_db, get_session
from database.models import Clip


def _create_old_schema_clips_table(db_path: Path) -> None:
    """Mimics the clips table as it existed before suggested_caption /
    suggested_description / suggested_keywords / caption_source existed."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE clips (
            id VARCHAR PRIMARY KEY, source_video_id VARCHAR NOT NULL,
            start_time FLOAT NOT NULL, end_time FLOAT NOT NULL, duration FLOAT NOT NULL,
            viral_score FLOAT, confidence_score FLOAT, transcript_text TEXT,
            suggested_title VARCHAR, suggested_hashtags VARCHAR,
            thumbnail_path VARCHAR, output_path VARCHAR, subtitle_style VARCHAR,
            status VARCHAR, created_at DATETIME
        )
    """)
    conn.execute("""
        INSERT INTO clips (id, source_video_id, start_time, end_time, duration, viral_score,
                            transcript_text, suggested_title, status)
        VALUES ('clip1', 'vid1', 0.0, 10.0, 10.0, 88.5, 'existing transcript data', 'Old Title', 'candidate')
    """)
    conn.commit()
    conn.close()


def test_migration_preserves_existing_data_and_adds_new_columns(tmp_path):
    db_path = tmp_path / "old_schema.db"
    _create_old_schema_clips_table(db_path)

    init_db(str(db_path))

    with get_session() as session:
        clip = session.query(Clip).filter_by(id="clip1").first()
        assert clip is not None
        assert clip.transcript_text == "existing transcript data"
        assert clip.suggested_title == "Old Title"
        # New columns must exist with safe defaults, not raise/None-crash.
        assert clip.suggested_caption == ""
        assert clip.suggested_description == ""
        assert clip.suggested_keywords == ""
        assert clip.caption_source == ""


def test_migrated_columns_are_writable(tmp_path):
    db_path = tmp_path / "old_schema2.db"
    _create_old_schema_clips_table(db_path)
    init_db(str(db_path))

    with get_session() as session:
        clip = session.query(Clip).filter_by(id="clip1").first()
        clip.suggested_caption = "A generated caption"
        clip.caption_source = "heuristic"
        session.commit()

    with get_session() as session:
        clip = session.query(Clip).filter_by(id="clip1").first()
        assert clip.suggested_caption == "A generated caption"
        assert clip.caption_source == "heuristic"


def test_migration_is_idempotent(tmp_path):
    """Running init_db() twice against the same (already-migrated)
    database must not error (e.g. from trying to add a column twice)."""
    db_path = tmp_path / "idempotent.db"
    _create_old_schema_clips_table(db_path)
    init_db(str(db_path))
    init_db(str(db_path))  # should be a no-op the second time, not raise

    with get_session() as session:
        assert session.query(Clip).filter_by(id="clip1").first() is not None


def test_brand_new_database_has_all_columns_from_create_all(tmp_path):
    """A database with no pre-existing tables should get everything from
    Base.metadata.create_all() directly -- the migration path should not
    be needed (and must not error) for a fresh install."""
    db_path = tmp_path / "brand_new.db"
    init_db(str(db_path))

    with get_session() as session:
        from database.models import Project
        session.add(Project(name="x"))
        session.commit()
        assert session.query(Project).count() == 1
