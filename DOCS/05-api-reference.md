# 05 — API Reference

Every HTTP route under `/v1/insights/*`, plus the shared ops endpoints. All routes are defined in `app/insights/api/insight_routes.py` and share the `APIRouter(prefix="/insights", tags=["Insights"])` declaration. The `/v1` prefix is added by the entrypoint app (`app.main` or `app.insight_main`) when including the router.

## Conventions

- All response bodies use the Pydantic models documented in [`03-data-models.md`](03-data-models.md). The OpenAPI schema served at `/docs` is the source of truth at runtime; this document reproduces it for handoff.
- The repository is injected via `Depends(get_insight_repository)` on every route that touches storage. In tests this dependency is overridden via `app.dependency_overrides`.
- All routes return `application/json`. Errors use FastAPI's `HTTPException` with `detail` strings.
- All `session_id` values are case-sensitive strings.

## Status semantics

`InsightGenerateResponse.status` is one of:

- `"ok"` — pipeline ran cleanly, validation produced no warnings or errors.
- `"warning"` — pipeline ran, but `_augment_validation` flagged one or more soft issues. The bundle is still complete and persisted.
- `"error"` — pipeline raised an exception. The response carries empty bundles, a populated `validation.errors` list, and is **not** persisted to storage.

---

## `POST /v1/insights/generate`

Generate insights from a pre-validated session payload.

**Request body**: `SessionInput` (see `03-data-models.md`).

**Response**: `200 OK` with `InsightGenerateResponse`.

**Behaviour**:

1. FastAPI parses and validates the body against `SessionInput`. A malformed payload returns `422 Unprocessable Entity` with the standard FastAPI validation error shape.
2. `InsightService.generate(session_input)` runs the full pipeline.
3. The response is upserted into storage by `session_id`.

**Example request**:

```bash
curl -X POST http://localhost:8000/v1/insights/generate \
  -H "Content-Type: application/json" \
  -d @samples/request.json
```

**Example response (truncated)**:

```json
{
  "session_id": "sample-call-2026-04-22",
  "status": "ok",
  "validation": { "valid": true, "errors": [], "warnings": [] },
  "analytics": { "session_metrics": {...}, "speaker_metrics": {...}, "pauses": [...] },
  "insights": {
    "scores": {
      "dominance_score": 0.55,
      "engagement_score": 0.62,
      "conflict_score": 0.17,
      "cooperation_score": 0.71,
      "emotion_volatility_score": 0.48
    },
    "flags": [...],
    "timeline": [...],
    "escalation": { "level": "mild", "score": 0.27, "signals": [...], "windows": [], "summary": "..." },
    "inconsistency": { "level": "none", "score": 0.0, "signals": [], "windows": [], "summary": "No inconsistency detected." }
  },
  "summaries": {
    "overall_summary": "...",
    "speaker_summaries": { "AGENT": "...", "CUSTOMER": "..." },
    "notable_concerns": ["..."],
    "key_moments_summary": "At 9.2s — Customer interrupts the agent's resolution attempt; ..."
  },
  "warnings": [],
  "meta": {
    "service_version": "1.0.0",
    "threshold_profile": "default",
    "generated_at": "2026-04-22T14:03:11.402381+00:00",
    "processing_ms": 14
  }
}
```

---

## `POST /v1/insights/generate-from-raw`

Same as `generate` but accepts an unvalidated raw dict. Runs `InsightValidator` and `InsightNormalizer` before the rest of the pipeline. Useful when the upstream caller has not yet enforced the `SessionInput` shape.

**Request body**: arbitrary JSON object. The service applies its own validation and normalization.

**Response**: `200 OK` with `InsightGenerateResponse` (with `status="warning"` if validation produced soft issues).

If validation produces **errors**, the response carries `status="error"` and an empty insights bundle, but is still returned with `200 OK` so the caller can read the structured `validation.errors` list. This is consistent with the fail-soft principle: the API never returns an unhelpful 5xx for malformed input.

---

## `GET /v1/insights/{session_id}`

Fetch a previously-stored full insight response.

**Path parameter**: `session_id`.

**Response**: `200 OK` with `InsightGenerateResponse` (the exact payload that was stored at generation time).

**Errors**: `404 Not Found` if no record exists for `session_id`. Detail: `"Session '{session_id}' not found"`.

---

## `GET /v1/insights/{session_id}/summary`

Convenience accessor returning only the prose summary view.

**Response**: `200 OK` with `InsightSummaryResponse`:

```json
{
  "session_id": "sample-call-2026-04-22",
  "overall_summary": "...",
  "notable_concerns": ["..."],
  "key_moments_summary": "..."
}
```

**Errors**: `404 Not Found` if the session is not stored.

---

## `GET /v1/insights/{session_id}/speakers`

Per-speaker view across analytics, insights, and summaries.

**Response**: `200 OK` with `InsightSpeakersResponse`:

```json
{
  "session_id": "sample-call-2026-04-22",
  "speaker_metrics": { "AGENT": {...}, "CUSTOMER": {...} },
  "speaker_insights": { "AGENT": {...}, "CUSTOMER": {...} },
  "speaker_summaries": { "AGENT": "...", "CUSTOMER": "..." }
}
```

`speaker_metrics` and `speaker_insights` are returned as raw dicts (not as Pydantic models) so callers can introspect freely. The shapes are still those of `SpeakerMetrics` and `SpeakerInsight`.

**Errors**: `404 Not Found` if the session is not stored.

---

## `GET /v1/insights/{session_id}/timeline`

Returns just the ordered timeline marker list.

**Response**: `200 OK` with `InsightTimelineResponse`:

```json
{
  "session_id": "sample-call-2026-04-22",
  "timeline": [
    {
      "marker_id": "interruption_0",
      "type": "interruption",
      "time_sec": 9.2,
      "speaker": "CUSTOMER",
      "severity": "low",
      "reason": "Customer began speaking before agent finished",
      "start_sec": 9.2,
      "end_sec": 9.2,
      "evidence": { "interrupter": "CUSTOMER", "interrupted": "AGENT", "overlap_sec": 0.0 }
    }
  ]
}
```

Timeline is sorted by `(time_sec, marker_id)`. Empty array if no markers were emitted.

**Errors**: `404 Not Found` if the session is not stored.

---

## `GET /v1/insights/`

Lists all stored session ids, ordered by most-recently-updated first.

**Response**: `200 OK` with:

```json
{
  "session_ids": ["sample-call-2026-04-22", "sample-call-2026-04-21", ...],
  "count": 17
}
```

This is a thin wrapper over `repository.list_session_ids()`. The endpoint does not paginate; if the dataset grows substantially, paging should be added at this layer.

---

## `DELETE /v1/insights/{session_id}`

Removes a stored insight record.

**Response**: `200 OK` with `{"deleted": true, "session_id": "..."}` if a record existed and was removed.

**Errors**: `404 Not Found` if no record existed. Detail: `"Session '{session_id}' not found"`.

---

## Shared ops endpoints

These are mounted directly on each entrypoint app (not on the insight router) and live in `app/main.py` and `app/insight_main.py`.

### `GET /healthz`

Liveness probe. Returns `200 OK` with:

```json
{ "status": "ok" }
```

The Dockerfile's `HEALTHCHECK` hits this endpoint.

### `GET /version`

Returns the running service identification:

```json
{
  "service": "voiceiq-insight-service",
  "version": "1.0.0",
  "environment": "dev"
}
```

`service`, `version`, and `environment` come from `InsightSettings`.

---

## OpenAPI / Swagger UI

Both entrypoints expose:

- `GET /docs` — Swagger UI
- `GET /redoc` — ReDoc UI
- `GET /openapi.json` — raw OpenAPI 3.1 schema

The `SessionInput` example in Swagger is the `sample-call-2026-04-22` payload, served via `model_config["json_schema_extra"]` on the Pydantic class. This is the same payload that lives at `samples/request.json`.

## Error contract summary

| Condition | Status | Body |
|-----------|--------|------|
| Body fails Pydantic parsing | `422` | FastAPI's standard validation error envelope |
| Pipeline raises (fail-soft) | `200` with `status="error"` | `InsightGenerateResponse` with empty bundles + populated `validation.errors` |
| `session_id` not found on GET / DELETE | `404` | `{"detail": "Session '...' not found"}` |
| Repository error during save | `500` with `detail` from `InsightRepositoryError` | The service catches and re-raises as HTTPException; logs the underlying SQLAlchemy error |

The service never returns 5xx for input issues. The only path to 5xx is a storage-layer failure that cannot be wrapped into a structured response.
