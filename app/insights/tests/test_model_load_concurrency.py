"""Concurrency tests for module-level ML model caches.

Each service exposes a lazy loader that must call the underlying model
constructor *exactly once* even when N concurrent first requests race
for init. These tests stub the constructor with a `MagicMock` and
assert `call_count == 1` after 10 threads release simultaneously from
a `threading.Barrier`.

No real ML weights are loaded — every external constructor is mocked.
The whole file runs in well under a second.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

# The three services import heavy ML deps (torch, transformers, pyannote,
# soundfile) at module load. CI installs only `requirements-insight.txt`
# which intentionally excludes them — so without these guards, importing
# this test file would break the whole `app/insights/tests/` collection
# step. Locally (where the heavy deps are present) the tests run normally.
pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("soundfile")
pytest.importorskip("huggingface_hub")
pytest.importorskip("pyannote.audio")

from app.services import diarization_service, sentiment_service, summary_service  # noqa: E402


def _race(loader, n: int = 10) -> tuple[list, list[BaseException]]:
    """Run `loader()` from `n` threads released simultaneously.

    Returns `(results, errors)`. `results` collects what each thread
    received from the loader; `errors` captures any exception raised
    inside a worker (we never want a worker exception to silently fail
    the test).
    """
    barrier = threading.Barrier(n)
    results: list = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def _worker() -> None:
        try:
            barrier.wait()
            value = loader()
            with results_lock:
                results.append(value)
        except BaseException as exc:  # noqa: BLE001 - capture for assertions
            with results_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


# --------------------------------------------------------------------------- #
# summary_service._get_summarizer                                             #
# --------------------------------------------------------------------------- #


def test_summary_loader_runs_constructor_once_under_concurrency() -> None:
    summary_service._summarizer = None  # hermetic reset
    try:
        with patch(
            "app.services.summary_service.pipeline",
            return_value=MagicMock(name="summarizer"),
        ) as mock_pipeline:
            results, errors = _race(summary_service._get_summarizer, n=10)
        assert errors == []
        assert mock_pipeline.call_count == 1
        # All 10 callers must receive the same singleton.
        assert all(r is results[0] for r in results)
    finally:
        summary_service._summarizer = None


# --------------------------------------------------------------------------- #
# SentimentService._load_pipeline                                             #
# --------------------------------------------------------------------------- #


def test_sentiment_loader_runs_constructor_once_under_concurrency() -> None:
    sentiment_service.SentimentService._pipeline = None
    try:
        with (
            patch("app.services.sentiment_service.AutoTokenizer.from_pretrained"),
            patch("app.services.sentiment_service.AutoModelForSequenceClassification.from_pretrained"),
            patch(
                "app.services.sentiment_service.pipeline",
                return_value=MagicMock(name="sentiment"),
            ) as mock_pipeline,
        ):
            results, errors = _race(sentiment_service.SentimentService._load_pipeline, n=10)
        assert errors == []
        assert mock_pipeline.call_count == 1
        assert all(r is results[0] for r in results)
    finally:
        sentiment_service.SentimentService._pipeline = None


# --------------------------------------------------------------------------- #
# DiarizationService._load_pipeline                                           #
# --------------------------------------------------------------------------- #


def test_diarization_loader_runs_constructor_once_under_concurrency() -> None:
    diarization_service._DIARIZATION_PIPELINE = None
    try:
        with (
            patch.object(diarization_service, "_HAS_PYANNOTE", True),
            patch.dict("os.environ", {"PYANNOTE_AUTH_TOKEN": "test"}, clear=False),
            patch("app.services.diarization_service.login"),
            patch("app.services.diarization_service.Pipeline") as mock_pipe_cls,
        ):
            # The real chain is Pipeline.from_pretrained(...).to(DEVICE),
            # then .instantiate({...}). MagicMock handles both implicitly.
            fake = MagicMock(name="pyannote_pipeline")
            fake.to.return_value = fake
            mock_pipe_cls.from_pretrained.return_value = fake

            # Build one service outside the race so __init__-time init does
            # not pollute the call count. Then reset the cache + the mock so
            # the race triggers a true cold-init.
            svc = diarization_service.DiarizationService()
            diarization_service._DIARIZATION_PIPELINE = None
            mock_pipe_cls.from_pretrained.reset_mock()

            results, errors = _race(svc._load_pipeline, n=10)

        assert errors == []
        assert mock_pipe_cls.from_pretrained.call_count == 1
        assert all(r is results[0] for r in results)
    finally:
        diarization_service._DIARIZATION_PIPELINE = None
