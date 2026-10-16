"""The SQLite parent directory is created where SQLAlchemy will open the file.

`mkdir` is recorded, not run, so no directory is created anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.insights.repository import db


@pytest.fixture
def made(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    monkeypatch.chdir(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: calls.append(self))
    return calls


def test_an_absolute_path_keeps_its_root(made):
    db._ensure_sqlite_directory("sqlite:////abs_probe/sub/x.db")

    assert made == [Path("/abs_probe/sub").resolve()]


def test_a_relative_path_is_under_the_working_directory(made, tmp_path):
    db._ensure_sqlite_directory("sqlite:///./data/x.db")

    assert made == [(tmp_path / "data").resolve()]


def test_a_driver_qualified_url_is_parsed_the_same_way(made, tmp_path):
    db._ensure_sqlite_directory("sqlite+pysqlite:///./data/x.db")

    assert made == [(tmp_path / "data").resolve()]


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "postgresql://u@h/db"])
def test_no_directory_for_memory_or_other_databases(made, url):
    db._ensure_sqlite_directory(url)

    assert made == []
