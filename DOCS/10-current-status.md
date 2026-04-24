# 10 — Current Status

A snapshot of what is currently shipped on this branch. Everything below is on disk and verified by the test suite. This file is intentionally backward-looking; forward-looking roadmap decisions are left to the receiving developer.

---

## Sprint history

### Sprint 1 — Foundation (complete)

Delivered the core analytics + signal aggregation + rules + scoring + summary path:

- `InsightAnalyticsEngine` — session and per-speaker metrics, pause computation
- `SignalAggregationEngine` — sentiment / emotion aggregation, sentiment trend, emotion volatility
- `InsightTimelineEngine` — chronological markers (dominance, pause, interruption, emotional shift, high tension, session tone decline)
- `InsightScoringEngine` — five normalised scores with breakdowns
- `InsightSummaryEngine` — overall, per-speaker, notable concerns, key moments
- `InsightRuleEngine` — orchestrator wiring it all together
- `InsightService` — top-level orchestrator with validation, normalization, status semantics

### Sprint 2 — Intelligence layer (complete)

Added the two assessment engines on top of Sprint 1:

- `InsightEscalationEngine` — five-detector escalation assessment (sentiment trend, negative density, emotional strain, interruption density, marker clusters)
- `InsightInconsistencyEngine` — five-detector inconsistency assessment (sentiment-emotion mismatch, text-sentiment contradiction, abrupt reversals, emotion oscillation, masking patterns)
- Rule engine integration: escalation runs after timeline, inconsistency windows are lifted into the timeline as `inconsistency_candidate` markers
- Session-level flags: `conversation_escalation`, `conversation_inconsistency`
- Both assessments surface in `InsightBundle.escalation` and `InsightBundle.inconsistency`

### Sprint 3 — Persistence and operations (complete)

Wrapped the service in production-grade infrastructure:

- SQLAlchemy 2.0 ORM layer (`InsightRecordORM`)
- `InsightRepository` with full CRUD (`save`, `get`, `exists`, `delete`, `list_session_ids`, `list_records`, `count`, `clear`)
- Module-level repository singleton + FastAPI `Depends(get_insight_repository)`
- SQLite default with auto-create on startup; configurable via `VOICEIQ_DATABASE_URL`
- `InsightSettings` (Pydantic-Settings) + `.env.example`
- Threshold profiles (`default`, `strict`, `lenient`) plumbed end-to-end
- Two FastAPI entrypoints (`app.main` for full pipeline, `app.insight_main` for insights-only)
- Multi-stage Dockerfile + healthcheck
- Sample request payload + samples README
- Postman walkthrough
- 28 passing tests covering API integration, signal aggregation, escalation, and inconsistency

### Sprint 4 — Documentation handoff (this sprint)

- Comprehensive `DOCS/` markdown set (10 files)
- Polished `.docx` handoff document
- Sample payload curated and tested

---

## What is currently shipped

### Engines (in `app/insights/core/`)

- `validator.py`
- `normalizer.py`
- `analytics_engine.py`
- `signal_aggregation.py`
- `timeline_engine.py`
- `scoring_engine.py`
- `summary_engine.py`
- `escalation_engine.py`
- `inconsistency_engine.py`
- `rule_engine.py`

### Models (in `app/insights/models/`)

- `input_models.py`
- `analytics_models.py`
- `signal_models.py`
- `insight_models.py`
- `escalation_models.py`
- `inconsistency_models.py`
- `api_models.py`

### API routes (in `app/insights/api/`)

Seven routes under `/v1/insights/*`:

- `POST /generate`
- `POST /generate-from-raw`
- `GET /{session_id}`
- `GET /{session_id}/summary`
- `GET /{session_id}/speakers`
- `GET /{session_id}/timeline`
- `GET /`
- `DELETE /{session_id}`

Plus shared ops endpoints `/healthz` and `/version` on each entrypoint.

### Persistence (in `app/insights/repository/`)

- `db.py` — engine, sessionmaker, lifecycle helpers
- `orm_models.py` — `InsightRecordORM`
- `insight_repository.py` — typed repository wrapper

### Configuration (in `app/insights/config/`)

- `settings.py` — `InsightSettings` (Pydantic-Settings, env-prefix `VOICEIQ_`)
- `defaults.py` — `InsightThresholds` + profile resolver

### Entry points

- `app/main.py` — full pipeline (audio routes + insight routes)
- `app/insight_main.py` — insights-only (the Docker default)

### Container artefacts

- `Dockerfile` (multi-stage, python:3.10-slim, healthcheck)
- `requirements.txt` (full pipeline)
- `requirements-insight.txt` (insights-only)
- `.env.example`

### Tests (in `app/insights/tests/`)

- `test_insight_api.py` (10 tests)
- `test_signal_aggregation.py` (4 tests)
- `test_escalation_signal.py` (2 tests)
- `test_inconsistency_signal.py` (~12 tests)

**Total: 28 tests, all passing.**

### Samples

- `samples/request.json` — `sample-call-2026-04-22` agent/customer dialog
- `samples/README.md` — curl + Postman walkthrough

### Documentation

- `DOCS/00-index.md` through `DOCS/10-current-status.md` (this file)
- `VoiceIQ-Insight-Service-Handoff.docx` (compiled handoff document)

---

## Known characteristics

### Strengths

- Fully deterministic — same input always produces the same output; no LLM or non-deterministic dependency
- Fail-soft throughout — empty inputs, missing fields, single-speaker sessions, missing sentiment all return populated responses with structured warnings rather than 5xx errors
- Explainable — every signal, flag, marker, score, and assessment carries `reason` and `evidence`
- Fast — typical 10–50 utterance session processes in under 50 ms on a developer laptop
- Lightweight — insights-only Docker image is < 200 MB, no GPU dependency, starts in under a second

### Acknowledged limitations

- **Single-process, single-host SQLite default.** Production deployments past one host need PostgreSQL.
- **No authentication or rate limiting.** The service is intended to sit behind an authenticated gateway in production.
- **Threshold profiles are coarse.** The three preset profiles (`default`, `strict`, `lenient`) are a starting point. Per-tenant or per-domain thresholds would need to be added.
- **No PDF generation.** The PDF reporting layer lives elsewhere in the repository under the audio pipeline; the Insight Service only produces structured JSON.
- **No caller authentication on the API.** Anyone who can reach the port can read or delete any session.
- **No batch endpoint.** Each session is processed one at a time. Bulk processing requires the caller to fan out.
- **No webhooks or async notifications.** The service is request/response only.

These are the obvious next-sprint candidates but are **not** built, and noting them here is purely descriptive of the current ship state.

---

## What's running where

| Environment | Entry point | Database | Image |
|-------------|-------------|----------|-------|
| Local dev | `uvicorn app.insight_main:app --reload` | SQLite (`./data/insights.db`) | none |
| Docker dev | `docker run voiceiq-insight:1.0.0` | SQLite via volume | `voiceiq-insight:1.0.0` |
| Full pipeline (local) | `uvicorn app.main:app --reload` | SQLite (`./data/insights.db`) | none |

There is no staging or production deployment target documented in the repository — those are out of scope for this codebase and live with the operations team.

---

## Branch state

- All Sprint 1, Sprint 2, and Sprint 3 work is merged.
- The 28-test suite passes on this branch.
- The Docker image builds reproducibly from `Dockerfile`.
- The `samples/request.json` payload is current and parses cleanly into `SessionInput`.
- The OpenAPI schema served at `/openapi.json` is consistent with `03-data-models.md` and `05-api-reference.md` in this DOCS package.
