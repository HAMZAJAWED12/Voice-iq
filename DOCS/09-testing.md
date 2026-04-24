# 09 — Testing

The test suite is in `app/insights/tests/`. There are 28 tests across four files, all passing as of the current branch. This document describes what is covered, how to run it, and the patterns the suite follows.

---

## Test layout

```
app/insights/tests/
├── test_insight_api.py            ← end-to-end FastAPI integration tests (10 tests)
├── test_signal_aggregation.py     ← signal aggregation unit tests (4 tests)
├── test_escalation_signal.py      ← escalation engine unit tests (2 tests)
└── test_inconsistency_signal.py   ← inconsistency engine unit tests (~12 tests)
```

The integration file exercises the full HTTP surface. The other three are focused unit tests on individual engines.

---

## Running the suite

```bash
pytest -v
```

Or filter by file:

```bash
pytest -v app/insights/tests/test_insight_api.py
pytest -v app/insights/tests/test_inconsistency_signal.py
```

The suite has no external dependencies (no Docker, no live database, no network). Every test that needs persistence creates its own SQLite file under a `tmp_path` and tears it down automatically.

Expected runtime is well under 30 seconds on a developer laptop.

---

## Integration tests — `test_insight_api.py`

### `isolated_repo` fixture

```python
@pytest.fixture
def isolated_repo(tmp_path):
    db_path = tmp_path / "insights.db"
    db_url = f"sqlite:///{db_path}"
    settings = InsightSettings(database_url=db_url, database_auto_create=True)
    init_db(settings)
    factory = sessionmaker(bind=get_engine())
    repo = InsightRepository(session_factory=factory)
    yield repo
    reset_engine()
```

The fixture creates a fresh per-test SQLite file, initialises the schema, and yields a repository bound to that file. After the test, `reset_engine()` clears the module globals so the next test starts clean.

### `client` fixture

```python
@pytest.fixture
def client(isolated_repo):
    app.dependency_overrides[get_insight_repository] = lambda: isolated_repo
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

`app.dependency_overrides` is the canonical FastAPI mechanism for swapping a dependency in tests. The override is lambda-based so each test gets the fixture's repository instance.

### Tests covered

| Test | Asserts |
|------|---------|
| `test_healthz` | `/healthz` returns `{"status": "ok"}` with 200 |
| `test_version` | `/version` returns the configured service name and version |
| `test_generate_returns_full_response` | `POST /v1/insights/generate` with the sample payload returns `status="ok"` and a populated bundle |
| `test_get_after_generate_round_trip` | `GET /v1/insights/{session_id}` returns byte-equivalent JSON to what `generate` returned |
| `test_summary_endpoint_returns_summary_only` | `GET /v1/insights/{session_id}/summary` returns the `InsightSummaryResponse` shape |
| `test_speakers_endpoint` | `GET /v1/insights/{session_id}/speakers` returns per-speaker metrics + insights + summaries |
| `test_timeline_endpoint` | `GET /v1/insights/{session_id}/timeline` returns the marker list, sorted chronologically |
| `test_list_endpoint` | `GET /v1/insights/` returns the list of stored session ids and a count |
| `test_delete_endpoint` | `DELETE /v1/insights/{session_id}` returns `{"deleted": true}` and removes the row |
| `test_get_returns_404_for_unknown_session` | `GET` and `DELETE` return `404` with the expected detail string for a nonexistent session id |

The integration tests are the regression net for the public API. Any change to the route surface, status semantics, or response shape will fail at least one of these.

---

## Signal aggregation tests — `test_signal_aggregation.py`

Unit tests on `SignalAggregationEngine`:

| Test | Asserts |
|------|---------|
| `test_session_sentiment_majority_label` | the session sentiment label resolves to the majority across utterances |
| `test_session_emotion_dominant` | the dominant emotion is the one with the highest accumulated weight |
| `test_sentiment_trend_direction_improving` | a monotonically increasing series produces `direction == "improving"` and a positive slope |
| `test_emotion_volatility_score_zero_for_constant_emotion` | a session with constant emotion vectors yields `emotion_volatility_score == 0.0` |

These tests pin the core math against synthetic utterance lists and are the fastest place to detect a regression in the aggregation layer.

---

## Escalation tests — `test_escalation_signal.py`

| Test | Asserts |
|------|---------|
| `test_escalation_none_for_neutral_session` | a fully-neutral session produces `level == "none"` and `score == 0.0` |
| `test_escalation_severe_for_full_negative_session` | a session loaded with negative sentiment, high frustrated emotion, and dense interruptions produces `level in {"moderate", "severe"}` |

These are intentionally bracketed — they confirm the boundary cases. The mid-range behaviour is exercised through the integration test against the sample payload.

---

## Inconsistency tests — `test_inconsistency_signal.py`

The inconsistency engine has the most tests because it has the most detectors. Cases include:

| Test | Asserts |
|------|---------|
| `test_no_signals_when_consistent` | a fully-consistent session yields `level == "none"`, `score == 0.0`, no signals, no windows |
| `test_sentiment_emotion_mismatch_detected` | "positive" labels paired with anger / frustration emotions trigger the mismatch detector |
| `test_text_sentiment_contradiction_detected` | text containing "thanks" but a negative label triggers the text-sentiment contradiction signal |
| `test_abrupt_reversals_detected` | same-speaker swings of ≥ 0.5 in sentiment score across consecutive utterances trigger the reversal detector |
| `test_emotion_oscillation_detected` | rapid switching between calming and tense emotion categories triggers the oscillation detector |
| `test_masking_pattern_detected` | a speaker with positive sentiment lean but tense dominant emotion triggers the masking detector |
| `test_per_signal_caps_applied` | no single detector contributes more than its `_CAP_*` to the aggregate score |
| `test_level_mapping_thresholds` | the score-to-level boundaries (`0.15`, `0.35`, `0.60`) map correctly |
| `test_speaker_attribution_on_signals` | `InconsistencySignal.speaker` is populated when a single speaker sources the mismatch |
| `test_windows_lifted_into_timeline_via_rule_engine` | the rule engine correctly emits `inconsistency_candidate` markers for each `InconsistencyWindow` |
| `test_summary_includes_signal_types` | the assessment's `summary` string lists each fired signal type |
| `test_assessment_for_empty_session_is_safe` | passing a synthetic empty session does not raise; returns the default empty assessment |

The empty-session and per-detector-cap tests are direct expressions of the fail-soft and score-discipline principles.

---

## Test patterns

### Fixture-driven persistence

Every persistence test uses the `isolated_repo` + `client` pair. There is no shared global database, no fixture teardown beyond `reset_engine()`, and no test order dependency.

### TestClient over httpx-async

The integration tests use FastAPI's synchronous `TestClient`. This is appropriate because the service has no async I/O — SQLAlchemy's sync API is what the repository uses, and the route handlers are not declared `async`.

### Synthetic utterance builders

The unit tests construct `UtteranceInput` lists inline rather than loading sample payloads. This keeps each test self-contained and makes the asserted behaviour visible at the test site.

### Direct engine invocation

Engine unit tests call the engine's `assess` / `aggregate` / `build_timeline` classmethods directly. They do not go through `InsightRuleEngine`. This is what makes them fast and what isolates regressions to a single engine.

---

## What is not yet covered

A senior developer continuing the project should be aware of these gaps:

- **No load tests.** The service has not been benchmarked under concurrent load. A simple `wrk` or `hey` run against `POST /v1/insights/generate` is the recommended smoke test before scaling.
- **No PostgreSQL integration tests.** The repository tests run against SQLite only. Switching `VOICEIQ_DATABASE_URL` to PostgreSQL should work without code changes (the ORM uses standard SQLAlchemy types) but is not exercised in CI.
- **No threshold-profile end-to-end tests.** The rule engine accepts a `threshold_profile` parameter, but no integration test asserts that `"strict"` and `"lenient"` produce different bundles for the same payload. The unit tests on the threshold defaults are the closest coverage.
- **No timeline-engine unit-test file.** The timeline engine is exercised only through the integration tests and the rule-engine path. A focused `test_timeline_engine.py` would tighten the coverage.

These are the obvious next-sprint additions for the testing layer.

---

## CI

There is currently no CI configuration shipped in the repository. The recommended setup is GitHub Actions running:

```yaml
- run: pip install -r requirements.txt
- run: pytest -v --tb=short
```

The full install is heavy because of the audio pipeline dependencies; for an insights-only CI lane, install only `requirements-insight.txt` and run `pytest app/insights/tests/`.
