"""SQLAlchemy engine + session factory for the Insight Service.

Single source of truth for database wiring. Anything that needs to talk
to the DB (repositories, FastAPI dependencies, smoke scripts) goes through
the helpers exposed here so we keep one engine per process.

Defaults to a local SQLite file (`./data/insights.db`); override via the
`VOICEIQ_DATABASE_URL` environment variable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.insights.config.settings import InsightSettings, get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the Insight Service."""


# Module-level engine + session factory. Initialised lazily so importing
# this module (e.g. for tests) is cheap and side-effect free.
_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def _ensure_sqlite_directory(database_url: str) -> None:
    """For SQLite URLs, make sure the parent directory exists.

    SQLAlchemy will happily fail with an obscure "unable to open database file"
    if the parent directory is missing. This guard keeps the failure mode loud
    and obvious.
    """
    if not database_url.startswith("sqlite"):
        return
    parsed = urlparse(database_url)
    # urlparse on `sqlite:///./data/insights.db` puts the path on .path
    raw_path = parsed.path or ""
    if raw_path.startswith("/") and database_url.startswith("sqlite:///"):
        # `sqlite:///./data/insights.db` -> path == "/./data/insights.db"
        # Strip the leading slash so we treat it as a relative path.
        raw_path = raw_path.lstrip("/")
    if not raw_path or raw_path == ":memory:":
        return
    parent = Path(raw_path).resolve().parent
    parent.mkdir(parents=True, exist_ok=True)


def _build_engine(settings: InsightSettings) -> Engine:
    """Create a SQLAlchemy engine from settings, with SQLite-friendly args."""
    _ensure_sqlite_directory(settings.database_url)

    engine_kwargs: dict = {
        "echo": settings.database_echo,
        "future": True,
    }
    if settings.is_sqlite:
        # SQLite + multi-threaded FastAPI requires `check_same_thread=False`.
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(settings.database_url, **engine_kwargs)


def init_engine(settings: Optional[InsightSettings] = None) -> Engine:
    """Initialise the module-level engine and session factory.

    Idempotent: subsequent calls return the existing engine. Tests that
    need a fresh DB should call `reset_engine()` first.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    resolved = settings or get_settings()
    _engine = _build_engine(resolved)
    _SessionLocal = sessionmaker(
        bind=_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    return _engine


def get_engine() -> Engine:
    """Return the active engine, initialising on first use."""
    if _engine is None:
        init_engine()
    assert _engine is not None  # for type-checkers
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the active SessionLocal factory, initialising on first use."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def init_db(settings: Optional[InsightSettings] = None) -> None:
    """Create all tables on the configured database.

    Safe to call repeatedly; SQLAlchemy issues `CREATE TABLE IF NOT EXISTS`
    semantics under the hood. Disable by setting
    `VOICEIQ_DATABASE_AUTO_CREATE=false` and managing migrations externally.
    """
    resolved = settings or get_settings()
    engine = init_engine(resolved)

    if not resolved.database_auto_create:
        return

    # Importing here so the ORM model registers itself on Base.metadata
    # without forcing an import cycle at module load time.
    from app.insights.repository import orm_models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def reset_engine() -> None:
    """Dispose the active engine and clear cached factories.

    Intended for tests. Production code should never need this.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations.

    Commits on success, rolls back on any exception, and always closes
    the session.
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a SQLAlchemy session.

    Usage in a route:

        from fastapi import Depends
        from app.insights.repository.db import get_db

        @router.get(...)
        def handler(db: Session = Depends(get_db)):
            ...
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


__all__ = [
    "Base",
    "init_engine",
    "init_db",
    "reset_engine",
    "get_engine",
    "get_session_factory",
    "session_scope",
    "get_db",
]


# Allow `python -m app.insights.repository.db` to bootstrap the schema
# from the CLI, useful for local dev and Docker entrypoints.
if __name__ == "__main__":  # pragma: no cover
    init_db()
    db_url = os.environ.get("VOICEIQ_DATABASE_URL", get_settings().database_url)
    print(f"[insight-db] schema ensured against {db_url}")
