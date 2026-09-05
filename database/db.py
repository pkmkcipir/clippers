"""Database engine + session management (SQLite via SQLAlchemy).

Usage:
    from database.db import init_db, get_session
    init_db()
    with get_session() as session:
        session.add(Project(name="My Project"))
        session.commit()
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from database.models import Base
from utils.logger import get_logger

log = get_logger("database")

_engine = None
_SessionFactory = None


def _resolve_db_path() -> Path:
    from config.settings import DB_FILE
    return DB_FILE


def _run_lightweight_migrations(engine) -> None:
    """Adds columns that exist on the SQLAlchemy models but not yet in an
    existing on-disk table. This project doesn't use a full migration
    framework (Alembic would be overkill at this size), but a plain
    `ALTER TABLE ADD COLUMN` covers every column added here so far, since
    they're all additive and nullable/defaulted. Never touches existing
    columns or drops anything, so it's safe to run on every startup --
    e.g. this is what lets a database from before the AI Caption
    Generator existed pick up its new `Clip` columns automatically."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand new table -- create_all() already handled it
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(engine.dialect)
                default_clause = ""
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    value = column.default.arg
                    if isinstance(value, str):
                        default_clause = f" DEFAULT '{value}'"
                    elif isinstance(value, bool):
                        default_clause = f" DEFAULT {int(value)}"
                    elif isinstance(value, (int, float)):
                        default_clause = f" DEFAULT {value}"
                log.info("Migrating: adding column %s.%s", table.name, column.name)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}{default_clause}'))


def init_db(db_path: str | Path | None = None) -> None:
    """Create the SQLite engine + all tables if they don't exist yet, and
    migrate any existing tables that are missing newer columns.

    Pass db_path="sqlite:///:memory:"-style string, or a filesystem path,
    mainly used by tests. Defaults to the real app-data DB file.
    """
    global _engine, _SessionFactory

    if db_path is None:
        path = _resolve_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    elif str(db_path).startswith("sqlite:"):
        url = str(db_path)
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"

    _engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    _run_lightweight_migrations(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
    log.info("Database ready -> %s", url)


def get_engine():
    if _engine is None:
        init_db()
    return _engine


@contextmanager
def get_session():
    if _SessionFactory is None:
        init_db()
    session = _SessionFactory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
