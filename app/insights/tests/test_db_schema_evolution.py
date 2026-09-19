"""Characterization of what `init_db()` can and cannot change.

Sprint 7 decision **D1** constrains the Fact-Check Agent to *new tables
only*. That constraint is not a style preference — it is forced by the
mechanism this repository actually has:

    Base.metadata.create_all(bind=engine)

`create_all` issues `CREATE TABLE` for tables that are **missing**. It never
issues `ALTER TABLE`. So a column added to an ORM model that maps to an
already-existing table lands on a fresh database and is **silently absent**
on one that already holds rows — and every fresh-database test in this suite
passes either way. That asymmetry is exactly why the failure would first be
seen in an environment with real data.

These tests pin both halves of the behaviour so the constraint is executable
rather than merely written down in `DOCS/FACTCHECK-AGENT-PHASE-0.md` §4.

They are characterization tests: they assert what the code *does*, including
the limitation. If someone adopts Alembic later (D1 Option B), the
`does_not_alter` test is the one that is *supposed* to fail — that failure is
the signal the constraint has been lifted, not a regression.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, text

from app.insights.config.settings import InsightSettings
from app.insights.repository.db import init_db, reset_engine

# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db_path(tmp_path: Path) -> Iterator[Path]:
    """A real SQLite file — in-memory would not survive the reconnect."""
    path = tmp_path / "legacy.db"
    yield path
    reset_engine()


def _settings(db_path: Path) -> InsightSettings:
    # as_posix() keeps the URL valid on Windows, where the raw path has
    # backslashes that urlparse mangles.
    return InsightSettings(database_url=f"sqlite:///{db_path.as_posix()}")


def _columns(db_path: Path, table: str) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _seed_legacy_table(db_path: Path) -> None:
    """Create `fact_check_results` with an intentionally narrow shape.

    Stands in for a database written by an older version of the service:
    the table exists, it holds a row, and it is missing columns the current
    ORM model declares.
    """
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    md = MetaData()
    Table(
        "fact_check_results",
        md,
        Column("id", Integer, primary_key=True),
        Column("conversation_id", String(128)),
    )
    md.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO fact_check_results (conversation_id) VALUES ('sess-legacy')"))
    engine.dispose()


# --------------------------------------------------------------------------- #
# The limitation                                                              #
# --------------------------------------------------------------------------- #


def test_init_db_does_not_alter_an_existing_table(db_path: Path) -> None:
    """The D1 constraint, stated as behaviour.

    `fact_check_results` already exists with two columns. The current ORM
    model declares many more. After `init_db()` the table is *unchanged* —
    no column is added, and no error is raised to tell anyone.
    """
    _seed_legacy_table(db_path)
    before = _columns(db_path, "fact_check_results")
    assert before == {"id", "conversation_id"}

    init_db(_settings(db_path))

    after = _columns(db_path, "fact_check_results")
    assert after == before, (
        "create_all() altered an existing table — the D1 constraint no longer "
        "holds and DOCS/FACTCHECK-AGENT-PHASE-0.md §4 needs revisiting"
    )
    # Specifically: columns the live ORM model declares are still missing.
    assert "verdict" not in after


def test_the_missing_column_is_silent_not_an_error(db_path: Path) -> None:
    """No exception, no warning — which is what makes this dangerous.

    If `init_db()` raised on a schema mismatch, the constraint would be
    self-enforcing and D1 would not have mattered. It does not.
    """
    _seed_legacy_table(db_path)
    init_db(_settings(db_path))  # must not raise
    assert "verdict" not in _columns(db_path, "fact_check_results")


def test_preexisting_rows_survive(db_path: Path) -> None:
    """create_all() is additive: it does not drop or rewrite existing data."""
    _seed_legacy_table(db_path)
    init_db(_settings(db_path))

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT conversation_id FROM fact_check_results")).scalars().all()
    finally:
        engine.dispose()
    assert rows == ["sess-legacy"]


# --------------------------------------------------------------------------- #
# The escape hatch D1 relies on                                               #
# --------------------------------------------------------------------------- #


def test_init_db_does_create_missing_tables(db_path: Path) -> None:
    """Why Option A works: a *new* table is created even on an old database.

    This is the half that makes "new tables only" a viable strategy rather
    than a dead end. The Sprint 7 agent tables land here.
    """
    _seed_legacy_table(db_path)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    try:
        assert set(inspect(engine).get_table_names()) == {"fact_check_results"}
    finally:
        engine.dispose()

    init_db(_settings(db_path))

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert len(tables) > 1, "init_db() created no new tables on a pre-existing database"


def test_auto_create_false_creates_nothing(db_path: Path) -> None:
    """The externally-migrated deployment path stays a genuine no-op."""
    settings = InsightSettings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        database_auto_create=False,
    )
    init_db(settings)
    assert not db_path.exists() or _table_count(db_path) == 0


def _table_count(db_path: Path) -> int:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    try:
        return len(inspect(engine).get_table_names())
    finally:
        engine.dispose()
