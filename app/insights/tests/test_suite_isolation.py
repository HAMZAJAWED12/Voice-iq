"""The test suite never reads `.env` and never writes repository data.

`app/conftest.py` sets this up before any test module is imported. These
tests pin it: a developer's `.env` must not change what the suite checks, and
the lifespan's retention sweep must not touch the repository's `data/`.
Every `.env` here is a synthetic file under tmp_path.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import dotenv
import pytest
from sqlalchemy.engine import make_url

from app.agent_brain.config.settings import AgentBrainSettings
from app.insights.config.settings import InsightSettings, get_settings
from app.utils.job_io import JobIO

REPO_ROOT = Path(__file__).resolve().parents[3]


def _outside_repo(path: Path) -> bool:
    return not path.resolve().is_relative_to(REPO_ROOT)


@pytest.mark.parametrize("entrypoint", ["app.main", "app.insight_main"])
def test_the_entrypoints_never_load_a_dotenv_file(
    entrypoint: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VOICEIQ_ISOLATION_PROBE=from-file\n", encoding="utf-8")
    monkeypatch.delenv("VOICEIQ_ISOLATION_PROBE", raising=False)

    load_dotenv = importlib.import_module(entrypoint).load_dotenv

    assert load_dotenv(dotenv_path=env_file) is False
    assert dotenv.load_dotenv(dotenv_path=env_file) is False
    assert "VOICEIQ_ISOLATION_PROBE" not in os.environ


def test_insight_settings_ignore_an_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("VOICEIQ_SERVICE_NAME=from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VOICEIQ_SERVICE_NAME", raising=False)

    assert InsightSettings().service_name != "from-file"


def test_agent_brain_settings_ignore_an_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("VOICEIQ_AGENT_CALLBACK_URL=https://example.invalid/cb\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VOICEIQ_AGENT_CALLBACK_URL", raising=False)

    assert AgentBrainSettings().callback_url == ""


def test_database_is_outside_the_repository() -> None:
    url = make_url(get_settings().database_url)

    assert url.get_backend_name() == "sqlite"
    assert url.database
    assert Path(url.database).is_absolute()
    assert _outside_repo(Path(url.database))


def test_default_job_folder_is_outside_the_repository() -> None:
    base = JobIO().base_dir

    assert base.is_absolute()
    assert _outside_repo(base)
