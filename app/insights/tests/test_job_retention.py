"""Unit coverage for JobIO.purge_expired (S2 — artifact retention).

Tests the sweeper directly on a tmp_path-backed JobIO; no server, no ML.
Age is driven by back-dating file mtimes via os.utime.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import app.utils.job_io as jio
from app.utils.job_io import JobIO


def _make_job(io: JobIO, job_id: str, *, age_hours: float) -> Path:
    """Create a job dir with meta.json back-dated by age_hours."""
    job = io.init_job(job_id)
    io.save_json(job, "meta.json", {"job_id": job_id})
    old = time.time() - age_hours * 3600.0
    os.utime(job.root / "meta.json", (old, old))
    return job.root


def test_purges_only_dirs_older_than_ttl(tmp_path: Path) -> None:
    io = JobIO(base_dir=str(tmp_path / "jobs"))
    fresh = _make_job(io, "fresh", age_hours=1.0)
    stale = _make_job(io, "stale", age_hours=48.0)

    removed = io.purge_expired(max_age_hours=24.0)

    assert removed == 1
    assert fresh.exists()
    assert not stale.exists()


def test_ttl_zero_is_noop(tmp_path: Path) -> None:
    io = JobIO(base_dir=str(tmp_path / "jobs"))
    old = _make_job(io, "old", age_hours=1000.0)

    assert io.purge_expired(max_age_hours=0.0) == 0
    assert old.exists()


def test_negative_ttl_is_noop(tmp_path: Path) -> None:
    io = JobIO(base_dir=str(tmp_path / "jobs"))
    _make_job(io, "old", age_hours=1000.0)
    assert io.purge_expired(max_age_hours=-5.0) == 0


def test_missing_base_dir_is_safe(tmp_path: Path) -> None:
    io = JobIO(base_dir=str(tmp_path / "does_not_exist"))
    assert io.purge_expired(max_age_hours=24.0) == 0


def test_falls_back_to_dir_mtime_when_no_meta(tmp_path: Path) -> None:
    io = JobIO(base_dir=str(tmp_path / "jobs"))
    job = io.init_job("nometa")  # init_job creates the tree but no meta.json
    old = time.time() - 100 * 3600.0
    os.utime(job.root, (old, old))

    assert io.purge_expired(max_age_hours=24.0) == 1
    assert not job.root.exists()


def test_non_dir_entries_ignored(tmp_path: Path) -> None:
    base = tmp_path / "jobs"
    base.mkdir(parents=True)
    stray = base / "stray.txt"
    stray.write_text("not a job dir", encoding="utf-8")
    old = time.time() - 100 * 3600.0
    os.utime(stray, (old, old))

    io = JobIO(base_dir=str(base))
    assert io.purge_expired(max_age_hours=24.0) == 0
    assert stray.exists()  # files are left alone; only job dirs are swept


def test_removal_error_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    io = JobIO(base_dir=str(tmp_path / "jobs"))
    _make_job(io, "stale", age_hours=48.0)

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("directory locked")

    monkeypatch.setattr(jio.shutil, "rmtree", _boom)

    # An unremovable dir is logged and skipped, never raised into the caller.
    assert io.purge_expired(max_age_hours=24.0) == 0
