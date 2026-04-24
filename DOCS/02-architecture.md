# 02 — Architecture

## Module topology

```
app/
├── insight_main.py              ← Standalone FastAPI entrypoint (Insight Service only)
├── main.py                      ← Full-pipeline FastAPI entrypoint (audio + insights)
└── insights/
    ├── __init__.py              ← exports InsightService
    ├── service.py               ← orchestrator: validate → analytics → rules → summary
    ├── api/
    │   ├── __init__.py          ← exports `router`
    │   └── insight_routes.py    ← every /v1/insights/* HTTP route
    ├── config/
    │   ├── defaults.py          ← InsightThresholds (rule-engine knobs)
    │   └── settings.py          ← InsightSettings (env-driven runtime config)
    ├── core/
    │   ├── validator.py         ← raw-payload structural validation
    │   ├── normalizer.py        ← raw dict → SessionInput coercion
    │   ├── analytics_engine.py  ← session/speaker metrics + pause computation
    │   ├── signal_aggregation.py← sentiment / emotion / trend / volatility
    │   ├── timeline_engine.py   ← chronological marker generation
    │   ├── scoring_engine.py    ← five normalised scores + breakdown
    │   ├── summary_engine.py    ← prose summaries (overall + speaker + concerns)
    │   ├── escalation_engine.py ← escalation level + windows + signals
    │   ├── inconsistency_engine.py← inconsistency level + windows + signals
    │   └── rule_engine.py       ← orchestrator for the rules layer
    ├── models/
    │   ├── input_models.py      ← SessionInput, UtteranceInput, SentimentInput, EmotionInput
    │   ├── analytics_models.py  ← SessionMetrics, SpeakerMetrics, PauseMetric, AnalyticsBundle, ValidationIssue/Result
    │   ├── signal_models.py     ← SentimentAggregate, EmotionAggregate, SessionSentimentTrend, AggregatedSignals
    │   ├── insight_models.py    ← InsightFlag, ScoreBreakdownItem, InsightScores, TimelineMarker, SpeakerInsight, InsightBundle
    │   ├── escalation_models.py ← EscalationSignal, EscalationWindow, EscalationAssessment
    │   ├── inconsistency_models.py← InconsistencySignal, InconsistencyWindow, InconsistencyAssessment
    │   └── api_models.py        ← SummaryBundle, InsightMeta, InsightGenerateResponse, sub-resource responses, InsightStoredRecord
    ├── repository/
    │   ├── db.py                ← SQLAlchemy engine + sessionmaker + Base + lifecycle helpers
    │   ├── orm_models.py        ← InsightRecordORM (single table: insight_records)
    │   ├── insight_repository.py← CRUD wrapper for InsightStoredRecord
    │   └── __init__.py          ← exports InsightRepository, get_insight_repository (DI)
    └── tests/
        ├── test_insight_api.py            ← end-to-end FastAPI integration tests
        ├── test_signal_aggregation.py     ← signal aggregation unit tests
        ├── test_escalation_signal.py      ← escalation engine unit tests
        └── test_inconsistency_signal.py   ← inconsistency engine unit tests
```

## Dependency direction

The dependency graph is strictly downward:

```
api ─────► service ─────► core/* ─────► models/*
                          ▲
                          └─── config/*
service ─────► repository ─────► db / orm_models
```

`models/*` are leaf modules (Pydantic-only, no project imports beyond peer model files where re-export is needed). `core/*` modules import only from `models/*` and `config/*`. `service.py` orchestrates `core/*` and produces `api_models` outputs. `api/*` (the FastAPI router) injects the repository and calls the service. The repository layer talks only to SQLAlchemy and the ORM model.

There are no circular imports. The Insight Service imports nothing from the audio pipeline, which is what allows `app/insight_main.py` to mount the Insight router without pulling in `torch`, `whisper`, `pyannote`, `librosa`, or `soundfile`.

## Request lifecycle

The end-to-end path for `POST /v1/insights/generate` is:

1. **Routing.** `app.insights.api.insight_routes.generate_insights` receives the request. The body is parsed into a `SessionInput` by FastAPI's Pydantic validation. A 422 is returned if the structural validation fails (e.g. empty `utterances`).
2. **Repository injection.** The route declares `repository: InsightRepository = Depends(get_insight_repository)`. In production this resolves to the module-level `insight_repository` singleton; in tests `app.dependency_overrides` swaps in an isolated, file-backed SQLite DB per test.
3. **Service orchestration.** `InsightService.generate(session_input)` is called. The service:
   - calls `_parse_session_input` (handles already-validated `SessionInput` instances and dicts identically),
   - calls `_augment_validation` to add eight categories of structured warnings (missing speakers, missing duration, single-utterance, single-speaker, empty text, missing word_count, missing sentiment, missing emotion, irregular utterance order),
   - runs `InsightAnalyticsEngine.run(session)` to produce an `AnalyticsBundle`,
   - runs `InsightRuleEngine.run(session, analytics, threshold_profile=...)` to produce an `InsightBundle`,
   - runs `InsightSummaryEngine.run(session, analytics, insights)` to produce a `SummaryBundle`,
   - assembles an `InsightGenerateResponse` with `status="ok"` (or `"warning"` if validation flagged issues, or `"error"` if the pipeline raised), validation, analytics, insights, summaries, warnings, and an `InsightMeta` block (service version, threshold profile, ISO-8601 generated_at, processing_ms).
4. **Persistence.** `_store_response(repository, response)` calls `repository.save(InsightStoredRecord(...))`. Save is upsert: existing rows are updated by `session_id`.
5. **Return.** The response model is serialised by FastAPI and returned. The same payload is what later GETs by `session_id` will return verbatim — round-trip equality is asserted by the integration tests.

## Inside the rule engine

`InsightRuleEngine.run` is the single integration point for every signal-producing engine. The order matters and is fixed:

1. `SignalAggregationEngine.aggregate(session.utterances)` produces the `AggregatedSignals` (session and per-speaker sentiment/emotion, sentiment trend, emotion volatility score).
2. `_build_speaker_insights(...)` produces a `SpeakerInsight` per speaker, attaching speaker-level flags (`frequent_interruptions`, `high_overlap_participation`, `low_inquiry_behavior`, `speaker_emotional_strain`).
3. `InsightTimelineEngine.build_timeline(...)` builds the chronological marker list. **This must run before escalation**, because the escalation engine reads marker clusters off the timeline. (Note: there is an explicit comment in `rule_engine.py` flagging that the order was previously inverted and caused a `NameError` at runtime; it is now correct.)
4. `InsightEscalationEngine.assess(session, analytics, aggregated_signals, timeline)` produces an `EscalationAssessment`.
5. `InsightInconsistencyEngine.assess(session, analytics, aggregated_signals)` produces an `InconsistencyAssessment`.
6. `_build_inconsistency_markers(inconsistency)` lifts inconsistency windows into `inconsistency_candidate` `TimelineMarker` entries, which are appended to the timeline. The combined timeline is then re-sorted by `(time_sec, marker_id)`.
7. `_build_session_flags(...)` synthesises session-level flags (speaker dominance, high tension, negative session mood, declining session tone, conversation escalation, conversation inconsistency).
8. `InsightScoringEngine.compute_scores(...)` computes the five normalised scores, each with its own `ScoreBreakdownItem` list.
9. The `InsightBundle` is assembled and returned.

## Data contract between layers

Each layer has one input contract and one output contract:

| Layer | Input | Output |
|-------|-------|--------|
| `InsightValidator` | raw dict | `ValidationResult` |
| `InsightNormalizer` | raw dict | `SessionInput` (Pydantic-validated) |
| `InsightAnalyticsEngine` | `SessionInput` | `AnalyticsBundle` |
| `SignalAggregationEngine` | `List[UtteranceInput]` | `AggregatedSignals` |
| `InsightTimelineEngine` | `(SessionInput, AnalyticsBundle, AggregatedSignals, InsightThresholds)` | `List[TimelineMarker]` |
| `InsightEscalationEngine` | `(SessionInput, AnalyticsBundle, AggregatedSignals, List[TimelineMarker])` | `EscalationAssessment` |
| `InsightInconsistencyEngine` | `(SessionInput, AnalyticsBundle, AggregatedSignals)` | `InconsistencyAssessment` |
| `InsightScoringEngine` | `(AnalyticsBundle, List[TimelineMarker], Dict[str, SpeakerInsight], AggregatedSignals)` | `InsightScores` |
| `InsightSummaryEngine` | `(SessionInput, AnalyticsBundle, InsightBundle)` | `SummaryBundle` |
| `InsightRuleEngine` | `(SessionInput, AnalyticsBundle, threshold_profile?, thresholds?)` | `InsightBundle` |
| `InsightService.generate` | `SessionInput | dict` | `InsightGenerateResponse` |
| `InsightRepository.save` | `InsightStoredRecord` | `InsightStoredRecord` |

## Two FastAPI entrypoints

The codebase ships two `FastAPI` apps with different surface areas:

- `app/insight_main.py` exposes only `/v1/insights/*` plus `/healthz` and `/version`. It does **not** import the audio pipeline. This is what the bundled `Dockerfile` runs and the only entrypoint suitable for an "insights-only" deployment that does not need GPU/torch.
- `app/main.py` is the full pipeline. It includes both the audio routes (`/v1/process-audio`) via `app.routes.process_audio.router` and the Insight routes. It is the entrypoint for the development environment that has the full ML stack available.

Both entrypoints share the same `lifespan` behaviour: on startup they call `init_db(settings)` to ensure the SQLite schema exists before the first request lands. `init_db` is a no-op when `VOICEIQ_DATABASE_AUTO_CREATE=false`, which is the configuration intended for environments where migrations are managed externally.
