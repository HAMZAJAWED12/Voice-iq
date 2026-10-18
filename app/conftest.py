"""Keep the test suite away from the developer's environment.

pytest loads this file before it imports any test module, so everything below
is in place before the first settings object, engine or app is built (both
entrypoints resolve their settings at import time).

- `.env` is never read. `dotenv.load_dotenv` becomes a no-op before either
  entrypoint imports it, whatever the python-dotenv version;
  `PYTHON_DOTENV_DISABLED` does the same for subprocesses on python-dotenv
  1.2 and later. pydantic-settings reads its `env_file` through
  `dotenv_values()`, which neither affects, so both settings classes stop
  naming a file.
- The database and the default job folder live in one temporary directory for
  the whole run, so the lifespan's retention sweep and any route that writes
  touch nothing in the repository.

Tests that set their own `VOICEIQ_DATABASE_URL` or pass `JobIO(base_dir=...)`
keep doing so. `app/insights/tests/test_suite_isolation.py` pins all of this.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import dotenv

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="voiceiq-tests-"))


def _no_dotenv(*_args: object, **_kwargs: object) -> bool:
    return False


dotenv.load_dotenv = _no_dotenv
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["VOICEIQ_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'insights.db').as_posix()}"

from app.agent_brain.config.settings import AgentBrainSettings, get_agent_settings  # noqa: E402
from app.insights.config.settings import InsightSettings, get_settings  # noqa: E402
from app.utils.job_io import JobIO  # noqa: E402

# A settings object cached before the patch below would have read `.env`.
if get_settings.cache_info().currsize or get_agent_settings.cache_info().currsize:
    raise RuntimeError("settings were built before the test isolation was applied")

for _settings_cls in (InsightSettings, AgentBrainSettings):
    _settings_cls.model_config["env_file"] = None

# `JobIO()` with no argument means `data/jobs` under the working directory.
JobIO.__init__.__defaults__ = (str(_TEST_ROOT / "jobs"),)


def pytest_unconfigure(config: object) -> None:
    from app.insights.repository.db import reset_engine

    reset_engine()  # Windows cannot delete a SQLite file that is still open
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
