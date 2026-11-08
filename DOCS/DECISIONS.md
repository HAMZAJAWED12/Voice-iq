Last verified: 2026-10-24, 9887e0b
# Decisions

This file lists the decisions already taken in this repository. A decision is
"taken" when it is recorded as signed or is implemented in code. Each entry
gives the context, the decision, its consequence, the date and the commit.
Dates are commit dates from `git log`.

Decisions that are still open are listed at the end, with links only. For the
Sprint 7 register decisions (D1–D10, D3b, D10-T and U1–U10), the status
lives in one place: the
register in [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
This file does not repeat it.

Related: how the parts fit together is in [ARCHITECTURE.md](ARCHITECTURE.md),
security controls are in [SECURITY.md](SECURITY.md), the commit and review
process is in [WORKFLOW.md](WORKFLOW.md), the change history is in
[CHANGES.md](CHANGES.md), and the AI brief is [CLAUDE.md](../CLAUDE.md).

## Index

| # | Decision | Date | Commit |
|---|---|---|---|
| 1 | Fail-soft pipeline with a fixed set of hard-fail gates (five since #36) | 2026-07-17, 2026-07-20 | `24dc644`, `a1e9ef7` |
| 2 | Lazy ML imports in the orchestrator | 2026-07-25 | `9e57cb4`, `4df98c6` |
| 3 | Two fault-isolation models, chosen by topology | 2026-06-18, 2026-06-22 | `3c2b14a`, `0970819`, `ef005dd` |
| 4 | One shared clamp helper | 2026-06-21 | `e9cbd1f`, `c58dd9a` |
| 5 | Deterministic insight engines; every output has a reason and evidence | 2026-04-25, 2026-06-18 | `94f6479`, `c83d92b` |
| 6 | One light CI lane; heavy services are mocked | 2026-04-25 to 2026-07-25 | `37ee449`, `1a9fbed`, `077aaef`, `4df98c6` |
| 7 | Pre-commit hygiene hooks run through `python -m` | 2026-10-16 | `7b25b3f` |
| 8 | X-API-Key authentication | 2026-05-20 | `bb6e61b` |
| 9 | Interactive API docs hidden in production | 2026-07-25 | `c7c34db` |
| 10 | Per-endpoint rate limiting | 2026-07-25 | `93f73b1`, `df8eec3` |
| 11 | Fact-check egress is opt-in | 2026-07-25 | `61fbf56` |
| 12 | HMAC-signed Java callback with a timestamp-bound v2 signature | 2026-06-22, 2026-07-25 | `3e362e9`, `a54944b` |
| 13 | Job-artifact retention | 2026-07-25 | `3c3adb2` |
| 14 | D1: new tables only | 2026-09-04 | `2a539d3` |
| 15 | D2: job-based execution | 2026-09-04 | `2a539d3` |
| 16 | D10: the job re-runs every downstream consumer | 2026-09-20 | `dacc926` |
| 17 | The v1 fact-check contract is frozen (PHASE-0 §2 approved 2026-10-19) | 2026-09-04, 2026-10-19 | `c6284b6` |
| 18 | Verdict map with no default | 2026-09-19 | `1cd5350` |
| 19 | Agent Brain as an opt-in pipeline stage (L5) | 2026-09-20 | `f5e4d84`, `f619f7d` |
| 20 | N4b language seam, Phase 1 | 2026-09-20 | `359cd5f`, `404de5b`, `c2fadaa` |
| 21 | M1: no outbound request URLs in logs | 2026-10-11 | `c1b35d9`, `cedfe96` |
| 22 | M2: an unusable provider reply is `SOURCE_UNAVAILABLE` for that claim only | 2026-10-11 | `9aff207` |
| 23 | M3: provider errors are named | 2026-10-16 | `dc03a44` |
| 24 | M4: claim numbers capped at 15 digits; strict-JSON check before saving | 2026-10-16 | `8e51a94` |
| 25 | M5: a price claim needs a number that stands alone; names need price context | 2026-10-16 | `1dbfac7` |
| 26 | Claim detector: no new heuristics until Phase 3 | 2026-10-16 | — (owner) |
| 27 | Size rule for milestones and security fixes | 2026-10-16 | — (owner) |
| 28 | Request checks run before the body is read | 2026-10-16 | `ff874de` |
| 29 | One pipeline run at a time per worker, off the event loop | 2026-10-16 | `2bb6593` |
| 30 | Insight routes accept only finite numbers | 2026-10-16 | `93dda14` |
| 31 | M6: the PDF prints Latin-1 and substitutes the rest | 2026-10-17 | `dc9caf3` |
| 32 | M7: keyword rules match whole words only | 2026-10-17, 2026-10-19 | `dbae452`, `ee2e15b` |
| 33 | One trust group for API keys | 2026-10-17 | — (owner) |
| 34 | Staging fails closed; compose binds to loopback | 2026-10-17 | `d7f9075` |
| 35 | Bounded wait for a pipeline slot; 503 beyond it | 2026-10-17 | `948c49d` |
| 36 | Audio longer than two hours is rejected, never truncated | 2026-10-17 | `6c17c72` |
| 37 | Rate limits on the insight routes; the session list is paged | 2026-10-17 | `e802fcd` |
| 38 | A fact-check run checks at most 25 claims and starts none after 20 seconds | 2026-10-17 | `da92ae4` |
| 39 | Stored records are deleted after 30 days | 2026-10-17 | `cbdafb4` |
| 40 | An error result never replaces a good stored record | 2026-10-17 | `f8bc3ed` |
| 41 | An insight error result fails the pipeline stage | 2026-10-17 | `c53c71d` |
| 42 | A missing stage package fails only that stage | 2026-10-17 | `0f9c4c1` |
| 43 | Pipeline sentiment reaches the insight engines as positivity | 2026-10-17 | `98730c3` |
| 44 | A warning whenever mock diarization is used | 2026-10-17 | `2721a73` |
| 45 | The log level setting is applied; threshold profiles are not implemented | 2026-10-17 | `e25a3b2` |
| 46 | A readiness probe beside the liveness probe | 2026-10-17 | `660558c` |
| 47 | Container resource limits; TLS terminates at the ingress | 2026-10-17 | `fb56d7d` |
| 48 | Currency rates come from open.er-api.com | 2026-10-17 | `45a5b22` |
| 49 | A failed topic step returns a fixed message | 2026-10-17 | `dc97a84` |
| 50 | One owner holds every Phase 0 role | 2026-10-19 | — (owner) |
| 51 | Phase 0 is complete; Phase 1 may start | 2026-10-19 | — (owner) |
| 52 | Gold-set labelling may start before the Phase 1 gate | 2026-10-19 | — (owner) |
| 53 | Diarization uses pyannote 3.1 under the weights-only loader | 2026-10-24 | `9887e0b` |
| 54 | D5 run deadline set; D6 criteria note signed | 2026-10-28 | — (owner) |
| 55 | D6 criteria note S2: launch line | 2026-10-30 | — (owner) |

---

## Pipeline and code structure

### 1. Fail-soft pipeline with a fixed set of hard-fail gates

- **Context:** `/v1/process-audio` runs 19 stage methods in sequence
  (`app/pipeline/orchestrator.py`, `run()`, lines 154–231). If one late stage
  such as the PDF failed the whole run, the caller would lose good upstream
  output such as the transcript.
- **Decision:** Only four gates may fail a run: `MISSING_INPUT_AUDIO`,
  `AUDIO_NORMALIZATION_TIMEOUT`, `AUDIO_NORMALIZATION_FAILED` and
  `AUDIO_SILENT_OR_NEAR_SILENT`. They raise the private `_HardFail`
  (`orchestrator.py:70-79`), which `run()` catches once and returns with
  `status="failed"`. Every other stage catches its own exception, records a
  `<STAGE>_FAILED` warning (diarization records
  `DIARIZATION_FAILED_FALLBACK`), writes an empty or failed artifact, and the
  run ends with `status="ok"`.
- **Update (2026-10-17):** `AUDIO_TOO_LONG` is a fifth gate (#36); the
  pin test is now `test_exactly_five_hard_fail_gates`.
- **Consequence:** `test_exactly_five_hard_fail_gates` (named
  `test_exactly_four_hard_fail_gates` until #36) and
  `test_late_stage_exception_never_flips_status`
  (`app/insights/tests/test_orchestrator.py:1032-1107`) pin this, and
  `test_full_success_stage_order` pins the stage order. A new stage is a
  `_run_<stage>` method plus a `_PipelineState` field, and it must be
  fail-soft.
- **Date:** 2026-07-17 (pinned), 2026-07-20 (gates extracted) · **Commit:** `24dc644`, `a1e9ef7`

### 2. Lazy ML imports in the orchestrator

- **Context:** `orchestrator.py` imported whisper, torch, transformers, spacy
  and librosa at module scope. Its test harness could not run on the light
  requirements and needed its own 8–12 minute CI job (`9e57cb4`).
- **Decision:** The eight ML-backed imports (ASR, diarization, sentiment,
  keywords, gender, topic, summary, `analyze_audio_quality`) happen inside the
  `_run_<stage>` method that uses them (`orchestrator.py:13-25`). Four
  services that only look heavy stay at module scope on purpose:
  `FactCheckService`, `PDFService`, `normalize_to_wav`, and `EmotionService`,
  which guards its own torch import.
- **Consequence:** `import app.pipeline.orchestrator` works on
  `requirements-insight.txt`, and the separate heavy CI job was removed
  (`4df98c6`). New heavy imports go into the stage method, never module scope.
- **Date:** 2026-07-25 · **Commit:** `9e57cb4`, `4df98c6`

### 3. Two fault-isolation models, chosen by topology

- **Context:** The insight pipeline is a linear chain where each sub-engine
  feeds the next. The Agent Brain is a fan-out of five independent agents.
- **Decision:**
  - `InsightRuleEngine.run` (`app/insights/core/rule_engine.py`) has no
    internal `try/except`, so a sub-engine fault propagates. The single fault
    boundary is `InsightService.generate` (`app/insights/service.py:64-108`),
    which turns any engine failure into a `status="error"` response and keeps
    the validation result.
  - `AgentRunner._collect` (`app/agent_brain/core/agent_runner.py:52-59`)
    catches, logs and skips a failing agent, then returns the other agents'
    recommendations.
  - The two must not be harmonised. Catching per stage in a linear chain gives
    a silently wrong bundle. Raising in the fan-out throws away four agents'
    correct output.
- **Consequence:** `test_per_agent_fault_isolation`
  (`app/agent_brain/tests/test_agent_runner.py:41`) pins the runner side. M2
  later applied the fan-out model to fact-check claims (entry 22). Engines
  still use safe defaults for gaps in upstream data. That is a separate
  concern from engine faults.
- **Date:** 2026-06-18, 2026-06-22 · **Commit:** `3c2b14a`, `0970819`, `ef005dd`

### 4. One shared clamp helper

- **Context:** Three insight engines each had their own score clamp, and the
  fact-check scorer had a fourth. The engineering standard requires every
  score to lie in `0.0–1.0`, clamped the same way.
- **Decision:** `clamp(value, low=0.0, high=1.0)` in
  `app/insights/core/_math.py` is the only clamp. `scoring_engine` keeps its
  round-to-4-places policy but delegates the clamp
  (`scoring_engine.py:104-108`).
- **Consequence:** `scoring_engine`, `signal_aggregation`,
  `inconsistency_engine`, `factcheck/scorer` and the Agent Brain's
  `confidence` module and agents all import this helper. It is tested in
  `app/insights/tests/test_math.py`.
- **Date:** 2026-06-21 · **Commit:** `e9cbd1f`, `c58dd9a`

### 5. Deterministic insight engines; every output has a reason and evidence

- **Context:** Insight output feeds reports and human decisions, so it has to
  be explainable and repeatable.
- **Decision:** The insight engines (the modules directly in
  `app/insights/core/`) are rule-based. They import only the standard
  library and internal modules, and they make no model calls
  ([01-overview.md](01-overview.md)). `InsightFlag` and
  `TimelineMarker` carry `reason` and `evidence`, and every
  `ScoreBreakdownItem` carries a `reason`
  (`app/insights/models/insight_models.py:28-63`). `TimelineMarker.reason` is
  `Field(min_length=1)`, so a marker cannot be built with an empty reason.
- **Consequence:** An engine's output depends only on its input. Marker ids
  and ordering are deterministic ([04-engines.md](04-engines.md)). A new
  engine must emit a reason and evidence for every output.
- **Date:** 2026-04-25 (engines), 2026-06-18 (non-empty reason enforced) · **Commit:** `94f6479`, `c83d92b`

## Testing and tooling

### 6. One light CI lane; heavy services are mocked

- **Context:** The heavy ML dependencies would make CI take minutes instead
  of seconds. Once, a model artifact that was present on a development machine
  but absent in CI made a test pass locally and fail in CI (`077aaef`).
- **Decision:** `.github/workflows/test.yml` installs only
  `requirements-insight.txt`. The test job runs
  `pytest app/insights/tests/ app/agent_brain/tests/` on Python 3.10 and 3.11.
  The lint job runs ruff, mypy and a gitleaks scan of the working tree. A
  service counts as **heavy** if it imports an ML library **or** loads a
  model artifact. Heavy services are mocked in tests and are never installed
  in CI. The tests that need the heavy stack skip themselves through
  `pytest.importorskip`: the 3-test module
  `app/insights/tests/test_model_load_concurrency.py` and, since `9887e0b`,
  `test_the_allowlisted_names_are_the_real_classes` in
  `app/insights/tests/test_diarization_service.py`.
- **Consequence:** There is one lane, and the local and CI test counts
  reconcile ([CLAUDE.md](../CLAUDE.md)). A test that really needs the heavy
  stack belongs in a separate workflow or job.
- **Date:** 2026-04-25 (lane), 2026-06-14 (lint and matrix), 2026-07-19 (heavy rule), 2026-07-25 (single lane) · **Commit:** `37ee449`, `1a9fbed`, `077aaef`, `4df98c6`

### 7. Pre-commit hygiene hooks run through `python -m`

- **Context:** pre-commit generates unsigned `.exe` console-script launchers
  for the remote pre-commit-hooks repository. On some development machines,
  Windows Application Control blocks them, so three hygiene hooks could not
  run (`7b25b3f`).
- **Decision:** `trailing-whitespace`, `end-of-file-fixer` and
  `check-merge-conflict` run from a `repo: local` block with
  `entry: python -m pre_commit_hooks.<module>`, `language: python` and the pin
  `pre-commit-hooks==5.0.0` (`.pre-commit-config.yaml:11-40`). Their ids,
  names, file types and stages match upstream. `ruff`, `ruff-format` and
  `gitleaks` are unchanged. The hooks were first added in `fceeeac`.
- **Consequence:** All six hooks run on every commit, with none skipped. The
  pin is bumped by hand in this file, not through `pre-commit autoupdate`.
  The commit process is in [WORKFLOW.md](WORKFLOW.md).
- **Date:** 2026-10-16 · **Commit:** `7b25b3f`

## Security and privacy

### 8. X-API-Key authentication

- **Context:** The `/v1` routes had no authentication.
- **Decision:** `verify_api_key` (`app/security/api_key.py`) is a router
  dependency on the insight, fact-check and process-audio routers, and on the
  Agent Brain route (`app/agent_brain/api/agent_routes.py:29`, added with
  that route in `d4cfbe0`). Accepted keys
  come from `VOICEIQ_API_KEYS` as a comma-separated list. If keys are
  configured, a missing header gets 401 and a wrong key gets 403. If no keys
  are configured outside production, requests are allowed with a warning. If
  no keys are configured in production, every request gets 503. Keys are
  compared in constant time. `/healthz` and `/version` stay public.
- **Update (2026-10-17):** staging now fails closed like production (#34),
  and all keys form one trust group (#33).
- **Consequence:** A production deployment without keys fails closed, and
  the failure is obvious. `app/insights/tests/test_auth.py` pins this. The
  overview of controls is in [SECURITY.md](SECURITY.md).
- **Date:** 2026-05-20 · **Commit:** `bb6e61b`

### 9. Interactive API docs hidden in production

- **Context:** `/docs`, `/redoc` and `/openapi.json` showed every route's
  schema in every environment.
- **Decision:** With `VOICEIQ_ENVIRONMENT=production`, both entrypoints turn
  all three off (`app/main.py:50-52`, `app/insight_main.py:61-63`). Other
  environments are unchanged, and `/healthz` stays public.
- **Consequence:** `app/insights/tests/test_docs_gating.py` pins this.
- **Date:** 2026-07-25 · **Commit:** `c7c34db`

### 10. Per-endpoint rate limiting

- **Context:** No endpoint was throttled. A single caller could drive
  `/v1/process-audio` (ML and disk) and `/v1/fact-check` (external API
  calls) without limit (`93f73b1`).
- **Decision:** Moving-window limits are applied as FastAPI route
  dependencies (`app/security/rate_limit.py`). slowapi's decorator was not
  used: combined with `from __future__ import annotations`, it made FastAPI
  read body models as query parameters. Limits are counted per caller. A
  caller over the limit gets 429 with `Retry-After`. The defaults are
  `VOICEIQ_RATE_LIMIT_PROCESS_AUDIO=30/minute`,
  `VOICEIQ_RATE_LIMIT_FACTCHECK=60/minute` and
  `VOICEIQ_RATE_LIMIT_AGENT_BRAIN=60/minute`, with the master switch
  `VOICEIQ_RATE_LIMIT_ENABLED=true` (`app/insights/config/settings.py:176-202`).
- **Update (2026-10-17):** the insight routes have their own read and write
  limits, and every route group keeps its own counter (#37).
- **Consequence:** `app/insights/tests/test_rate_limit.py` pins this. The
  Agent Brain limit was added when that router was mounted (`df8eec3`).
- **Date:** 2026-07-25 · **Commit:** `93f73b1`, `df8eec3`

### 11. Fact-check egress is opt-in

- **Context:** `/v1/process-audio` fact-checked every request. That sent claim
  subjects taken from the transcript (city, country, currency, asset, ticker)
  to external providers without any consent step (`61fbf56`).
- **Decision:** Fact-checking is off by default. `factcheck_requested()`
  (`app/utils/factcheck_gate.py`) uses the request's `?fact_check=` value
  first. If there is none, it uses `VOICEIQ_FACTCHECK_AUTO_ENRICH`, which
  defaults to `false`. When fact-checking is skipped, `fact_checks_v2` is a
  `{"status": "disabled", ...}` stub and no external call is made. Calling
  `POST /v1/fact-check` directly counts as consent.
- **Consequence:** Callers that relied on automatic `fact_checks_v2` now
  have to opt in. Every egress point is listed in
  [DATA-FLOW.md](DATA-FLOW.md). `app/insights/tests/test_factcheck_gate.py`
  pins this.
- **Date:** 2026-07-25 · **Commit:** `61fbf56`

### 12. HMAC-signed Java callback with a timestamp-bound v2 signature

- **Context:** The Agent Brain pushes recommendations to the Java Action
  Layer. The first signature covered only the body, so nothing in the signed
  material expired. The callback URL scheme was not checked either
  (`a54944b`).
- **Decision:** `JavaCallbackClient.send`
  (`app/agent_brain/integrations/java_callback_client.py`) does nothing unless
  both `VOICEIQ_AGENT_CALLBACK_URL` and `VOICEIQ_AGENT_CALLBACK_SECRET` are
  set. It sends `X-VoiceIQ-Signature` (v1: HMAC-SHA256 of the body,
  unchanged). It also sends `X-VoiceIQ-Signature-V2`, an HMAC-SHA256 of
  `"{timestamp}.{body}"`, together with `X-VoiceIQ-Timestamp`. It refuses a
  URL that is not https, except for local development hosts.
- **Consequence:** The change is backward compatible. Java can move to v2,
  with a freshness window, on its own schedule, and v1 can be dropped after
  that. The integration spec is [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md).
  `app/agent_brain/tests/test_java_callback.py` pins this.
- **Date:** 2026-06-22 (client), 2026-07-25 (v2 and transport check) · **Commit:** `3e362e9`, `a54944b`

### 13. Job-artifact retention

- **Context:** Every request wrote `data/jobs/<id>/` (raw audio, transcript,
  PDF, insights), and nothing deleted it. Disk use grew without limit, and
  call data stayed on disk indefinitely.
- **Decision:** `JobIO.purge_expired` (`app/utils/job_io.py:127`) deletes job
  directories older than `VOICEIQ_JOB_RETENTION_HOURS` (default 24; `0` turns
  purging off). A directory's age is the modification time of its
  `meta.json`, or of the directory itself when there is none. The sweep runs
  at startup in both entrypoints (at `3bf7506`: `app/main.py:37`, `app/insight_main.py:47`)
  and at most once an hour at request time
  (at `3bf7506`: `app/routes/process_audio.py:33-51`). If one directory fails, the sweep
  skips it and carries on.
- **Update (2026-10-17):** the sweep moved to `app/utils/retention.py`, runs
  at startup and then hourly from each app's lifespan instead of inside the
  upload request, and also deletes stored records after
  `VOICEIQ_DB_RETENTION_DAYS` (#39).
- **Consequence:** Job artifacts last only as long as the retention window.
  Until #39 the sweep covered `data/jobs/` directories only, not database rows.
  `app/insights/tests/test_job_retention.py` pins this.
- **Date:** 2026-07-25 · **Commit:** `3c3adb2`

## Fact-Check Agent contract (Sprint 7)

For the status of D1, D2 and D10, see the
[register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register). Live progress is
in [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).

### 14. D1: new tables only

- **Context:** The repo has no Alembic. `init_db()` ends with
  `Base.metadata.create_all()` (`app/insights/repository/db.py:125`), which
  creates missing tables and never alters an existing one. A column added to
  an existing table appears on a fresh database but is silently missing on a
  database that already has rows. Every fresh-database test still passes.
- **Decision:** The Fact-Check Agent adds new tables only. No column of an
  existing table (`fact_check_results`, `insight_records` or any other) is
  added, removed, renamed or retyped. The four-table shape is in PHASE-0 §4.
  Adopting Alembic remains a separate decision for later.
- **Consequence:** No phase may assume `ALTER TABLE`.
  `app/insights/tests/test_db_schema_evolution.py` (`ff84664`, 2026-09-19)
  pins the behaviour this relies on: an old table is not widened, nothing
  warns, existing rows survive, and a missing table is created. If Alembic is
  adopted, its `does_not_alter` test is expected to fail and should be
  removed along with the constraint (`ff84664`).
- **Date:** 2026-09-04 · **Commit:** `2a539d3`

### 15. D2: job-based execution

- **Context:** By the plan's estimate, an LLM fact-check of about twenty
  claims takes minutes on CPU, on top of the time ASR and diarization already
  take (PHASE-0 §D2). Building synchronous first and adding async later is
  the expensive direction.
- **Decision:** The agent runs as a job after the audio response has been
  returned. In `/v1/process-audio`, `fact_check_report` is a state envelope
  (`pending | complete | failed | disabled | skipped`), and
  `GET /v2/fact-check/{runId}` is required. A failed job never changes the
  audio-processing status. A synchronous path may be added later as an option
  (`FACTCHECK_EXECUTION_MODE=sync`), but never as the default.
- **Consequence:** Downstream consumers cannot hold verdicts on the first
  pass (entry 16). A run needs an identity that can be looked up and a final
  status, and it must age out if nobody collects it. Nothing in today's code
  implements this yet; PHASE-0 §D2 makes these obligations binding on
  Phases 2, 5 and 6. The job runner is a DB-polled worker on
  `factcheck_agent_runs` (U1, signed 2026-10-19; see the
  [register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register)).
- **Date:** 2026-09-04 · **Commit:** `2a539d3`

### 16. D10: the job re-runs every downstream consumer

- **Context:** L5 (entry 19) made the Agent Brain a pipeline stage that runs
  before the response is returned. Under D2 it therefore has no fact-check
  result (`fact_check=None`, `app/pipeline/orchestrator.py:776-782`), so
  `FactCheckReviewAgent` is dormant on that path.
- **Decision:** When the fact-check job finishes, it re-runs these consumers
  in order: (1) the Agent Brain, (2) the PDF. A new consumer is added as a new
  row in PHASE-0 §D10. The Java callback must fire **exactly once** per
  session, on the final pass only. When fact-check is enabled, the in-pipeline
  run is provisional and does not fire it.
- **Consequence:** This is a Phase 6 obligation, and today's code does not
  change. Acceptance is a test showing that exactly one callback is sent per
  session when both features are on. "At least one" does not pass. Today the
  callback fires on every Agent Brain run; that is listed as a prerequisite
  fix in the status doc. D10-T (2026-10-19) sets at-most-once delivery and
  the accepted zero-callback cases the test must allow; see PHASE-0 §D10.
- **Date:** 2026-09-20 (raised in `17606d6`) · **Commit:** `dacc926`

### 17. The v1 fact-check contract is frozen (PHASE-0 §2 approved 2026-10-19)

- **Context:** Sprint 7 adds an agent with its own verdicts. Changing the v1
  shapes would break existing consumers of `POST /v1/fact-check`,
  `GET /v1/fact-check/{id}` and `fact_checks_v2`. The rule is written in
  PHASE-0 §2.1, is listed in [CLAUDE.md](../CLAUDE.md) among the Sprint 7
  hard constraints, and is followed in code today (see Consequence).
- **Decision:** No field of the v1 models in
  `app/insights/models/factcheck_models.py` is added, removed, renamed or
  retyped, and no member is added to or removed from `ClaimType` or
  `Verdict`. The agent gets its own models
  ([PHASE-0 §2.1](FACTCHECK-AGENT-PHASE-0.md#21-v1-is-frozen)). Since
  2026-10-19 the freeze also covers `ClaimSpan`, `ConfidenceLabel`,
  `MAX_TRANSCRIPT_CHARS`, every type or field constraint reachable from the
  v1 request and response, and the `GET /v1` row key set (W2, A2
  amendments (a) and (e); see #51).
- **Consequence:** `factcheck_models.py` has not changed since `e55d46c`
  (2026-05-04). M2, M3 and M4 put new failure cases into the existing
  `SOURCE_UNAVAILABLE` verdict or drop the claim; none of them added a member.
  `app/insights/tests/test_logging_hygiene.py:403-405` checks the v1 response
  shape. PHASE-0 §2 as a whole was approved on 2026-10-19 (#51).
- **Date:** 2026-09-04 · **Commit:** `c6284b6`

### 18. Verdict map with no default

- **Context:** The v1 adapter mapped verdicts with
  `.get(verdict, "UNVERIFIED")`. A new verdict such as `CONTRADICTED` would
  silently fall through to `UNVERIFIED`. The review agent would then emit HIGH
  priority at base confidence 0.8 instead of CRITICAL at 0.9, and nothing
  would raise an error.
- **Decision:** `_VERDICT_TO_STATUS`
  (`app/agent_brain/adapters/pipeline_adapter.py:41-48`) is typed
  `dict[Verdict, FactCheckStatus]`, covers every `Verdict`, and is indexed
  directly, so an unmapped verdict raises. The v2 map must keep this property
  and have its own literal-coverage test. Never re-add a default.
- **Consequence:** `test_verdict_map_is_total` compares the keys with
  `typing.get_args(Verdict)`
  (`app/agent_brain/tests/test_pipeline_adapter.py:97-99`), so a new verdict
  without an entry fails the suite.
- **Date:** 2026-09-19 · **Commit:** `1cd5350`

## Agent Brain and language

### 19. Agent Brain as an opt-in pipeline stage (L5)

- **Context:** `PipelineAdapter.to_context` and the Java callback client had
  existed since Sprint 6, but no production code called them. The agents
  were reached only through the pull route
  `POST /internal/v1/agent-brain/recommendations/generate`, where the caller
  builds the context itself (`f5e4d84`, `f619f7d`).
- **Decision:** `_run_agent_brain` runs between the insights and PDF stages
  (`app/pipeline/orchestrator.py:734-804`). It is off unless
  `VOICEIQ_AGENT_BRAIN_AUTO_RUN=true`. When it is off, the `recommendations`
  key is left out entirely (`orchestrator.py:980-981`), but the stage is
  still timed. The payload uses the camelCase aliases, the same shape the
  route and the callback use. The callback runs on a daemon thread and the
  pipeline does not wait for it. `min_confidence` filters only the callback,
  not the response. A failure in this stage is soft (`AGENT_BRAIN_FAILED`).
- **Consequence:** `FactCheckReviewAgent` stays dormant on this path until
  the D10 re-run exists; the other four agents are live. `TestAgentBrainStage`
  (`app/insights/tests/test_orchestrator.py:791`) and
  `app/agent_brain/tests/test_pipeline_callback.py` cover this, and
  `1d8f2f6` proves `to_context` is reached from production code.
- **Date:** 2026-09-20 · **Commit:** `f5e4d84`, `f619f7d`

### 20. N4b language seam, Phase 1

- **Context:** Urdu and Arabic extraction fails mostly because the
  vocabulary is missing, not because a model is missing
  ([N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md)). The ASR
  service recorded the detected language and then threw it away, and
  `AgentContext.language` was never read (`359cd5f`).
- **Decision:** `PipelineAdapter.to_context` picks the language in this
  order: the explicit argument, then the language ASR detected, then English
  (`app/agent_brain/adapters/pipeline_adapter.py`).
  `app/agent_brain/extraction/language.py` holds two functions:
  - `resolve_language()` maps a detected code onto the contract. A code
    outside the contract falls back to English.
  - `vocabulary_for()` falls back to English both when a key is absent and
    when it is empty.

  The extractors (assignee, date-time, priority, signals) and the four
  agents that match text (task, follow-up, email draft, escalation) take
  `language` and read a table keyed by language. Each table has exactly one filled key, `"en"`, which holds the
  earlier English terms unchanged. Adding a language means filling in data.
  Engineers do not invent Urdu or Arabic terms; that is work for native
  speakers.
- **Consequence:** Behaviour did not change: the suite stayed at 731 with no
  assertion edited (`404de5b`). Filling in a language waits on decision L1 in
  [N4B-MULTILINGUAL-STRATEGY.md §7](N4B-MULTILINGUAL-STRATEGY.md#7-open-decisions).
  `app/agent_brain/tests/test_language_seam.py` covers this.
- **Date:** 2026-09-20 · **Commit:** `359cd5f`, `404de5b`, `c2fadaa`

## Prerequisite fixes (M1–M7)

Working rule, recorded in the status doc (`6c8c89b`, 2026-10-11): until Phase
0 lets a phase start, no Sprint 7 application code is written. The only work
is fixes that need no open decision, one per milestone, smallest first. The
next milestone is in
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md#next-milestone).

### 21. M1: no outbound request URLs in logs

- **Context:** httpx logs the full URL of every request at INFO, and the root
  logger runs at INFO. Source-client URLs can carry sensitive values,
  including claim subjects taken from the call. A failed Java callback also
  logged httpx's error text, which contains the callback URL.
- **Decision:** `setup_logging()` (`app/utils/logger.py:10-23`) keeps the
  `httpx` and `httpcore` loggers at WARNING whatever the root level is. A
  failed callback logs only the exception class and HTTP status
  (`app/pipeline/orchestrator.py:853-863`). The rule: a log line never
  contains an outbound request URL, a payload, or exception text that
  includes a URL.
- **Consequence:** This covers every current and future httpx client. A
  logging config applied later must not lower those loggers' level again.
  `app/insights/tests/test_logging_hygiene.py` pins this.
- **Date:** 2026-10-11 · **Commit:** `c1b35d9`, `cedfe96`

### 22. M2: an unusable provider reply is `SOURCE_UNAVAILABLE` for that claim only

- **Context:** Some third-party replies could become wrong verdicts or fail
  the whole request, and then every other claim was lost. Examples are
  non-finite numbers, JSON that is not an object, wrong nested types, and
  bodies that cannot be decoded or are nested too deeply.
- **Decision:** `BaseSourceClient._get_json` rejects these replies
  (`app/insights/core/factcheck/source_clients/base_client.py:152-234`).
  Clients read nested values through the `_as_dict` and `_finite_float`
  helpers (`base_client.py:138-150`), and copy only scalar metadata.
  `FactCheckEngine._fetch_usable`
  (`app/insights/core/factcheck/factcheck_engine.py:274-293`) isolates each
  claim the way the AgentRunner fan-out isolates agents. If a client fails or
  returns a non-finite value, that claim ends as the existing
  `SOURCE_UNAVAILABLE` with a short reason, and the run continues. Logs carry
  only the client name, the claim id and the error class.
- **Consequence:** `POST /v1/fact-check` returns 200 with strict JSON, and
  `/v1/process-audio` degrades `fact_checks_v2` one claim at a time. The v1
  models did not change.
  `app/insights/tests/test_factcheck_source_replies.py` pins this.
- **Date:** 2026-10-11 · **Commit:** `9aff207`

### 23. M3: provider errors are named

- **Context:** exchangerate.host now requires an access key on every call.
  `ForexClient` sends no key, and there is no setting for one. Every
  `CURRENCY_RATE` claim therefore came back `SOURCE_UNAVAILABLE` with a
  generic reason and no log line.
- **Decision:** A reply with `success: false` or an error object counts as a
  provider error. The client logs only the provider's error code and type,
  then raises `SourceUnavailable(reason)`, for example "provider requires an
  access key". A reply with no `rates` is reported the same way
  (`app/insights/core/factcheck/source_clients/forex_client.py:25-32, 49-59, 73-84` at `dc03a44`; the client was replaced in `45a5b22`, #48).
  `SourceUnavailable` is the one exception the client contract allows
  (`base_client.py:93`), and the engine maps it to `SOURCE_UNAVAILABLE` with
  that reason, for that claim only. The provider did not change.
- **Update (2026-10-17):** the provider was decided (#48), and currency-rate
  claims can be verified again.
- **Consequence:** Until #48, currency-rate claims could not be verified, but
  the result said why. The provider question this left open was settled by
  #48.
- **Date:** 2026-10-16 · **Commit:** `dc03a44`

### 24. M4: claim numbers capped at 15 digits; strict-JSON check before saving

- **Context:** A very long number in a transcript could turn into a
  non-finite claimed value, which strict JSON cannot render.
- **Decision:** `MAX_VALUE_DIGITS = 15`
  (`app/insights/core/factcheck/claim_detector.py:193`). Fifteen digits is
  the decimal precision a float guarantees, and real prices, rates and
  temperatures need far fewer. A claim with a longer number is dropped, and
  claim ids stay sequential. As a second layer, `/v1/process-audio` checks the
  fact-check payload with `json.dumps(payload, allow_nan=False)` before saving
  or returning it, and falls back to the failed stub if the check fails
  (`app/routes/process_audio.py:101-104`).
- **Consequence:** `POST /v1/fact-check` returns 200 with that claim
  dropped. `/v1/process-audio` cannot return 500 because of fact-check, and
  a payload that cannot be rendered is never stored. The v1 models did not
  change. `app/insights/tests/test_factcheck_source_replies.py` pins this.
  Data stored before this fix is a separate item in the status doc.
- **Date:** 2026-10-16 · **Commit:** `8e51a94`

### 25. M5: a price claim needs a number that stands alone; names need price context

- **Context:** The detector read a coin or ticker alias followed by "is" and
  a number as a price, so "Ada is 30", "my friend Sol is 25" and "Meta is 30
  people" became claims, typically reported FALSE at high confidence.
- **Decision:** Before overlaps are resolved, `_keep_match` drops a crypto,
  stock or commodity match whose number is followed, on the same line, by
  what it counts or measures or by a currency other than the US dollar
  (word lists `_NOT_PRICE_WORDS`, `_NOT_PRICE_PER_UNITS`,
  `_OTHER_CURRENCY_WORDS`). A list of unit words was chosen over a list of
  allowed words: a first version that allowed only listed words dropped real
  claims such as "Bitcoin is 95000 isn't it". A subject that is also a name
  or an everyday word (ada, sol, eth, doge, brent), or any stock ticker,
  needs price context unless it is written in capitals ("ADA", "META"). A currency rate must be quoted per 1, 10,
  100, ... units, so a year or a count before a currency is not an amount
  (`app/insights/core/factcheck/claim_detector.py:196-316, 385-467`).
- **Consequence:** Names, ages, counts and durations no longer become
  claims; "1 USD = 280 PKR", "95k" and "24 hundred" are read. Some sentences
  still produce claims or are skipped (listed in
  [FEATURES.md §4.3](FEATURES.md#43-known-functional-limits-of-the-detector)).
  The v1 models did not change. `app/insights/tests/test_claim_detector.py`
  pins this. The project owner accepted three choices on 2026-10-16: rate
  amounts of 1, 10, 100, ... only; crypto and stock prices in other
  currencies dropped; the weather pattern's linear-time rewrite.
- **Date:** 2026-10-16 · **Commit:** `1dbfac7`

### 26. Claim detector: no new heuristics until Phase 3

- **Context:** M5 needed two rework rounds after review, and every rule it
  added had edge cases of its own. The limits that remain (past prices read
  as current, "21 million", capitalised abbreviations, decimal commas) need
  sentence understanding, which Phase 3 brings.
- **Decision:** From 2026-10-16 the regex claim detector changes only to fix
  a crash, a security problem or a performance problem. The remaining limits
  wait for Phase 3.
- **Consequence:** [FEATURES.md §4.3](FEATURES.md#43-known-functional-limits-of-the-detector)
  is the documented list of detector limits until Phase 3.
- **Date:** 2026-10-16 · **Decided by:** project owner (no code change)

### 27. Size rule for milestones and security fixes

- **Context:** M5 grew well past its plan and needed two rework rounds.
- **Decision:** If a milestone or a security fix grows past roughly twice
  its planned size, or needs a second rework round after review, work stops
  and the owner is asked before it continues.
- **Consequence:** The rule is part of the milestone loop in
  [WORKFLOW.md](WORKFLOW.md#the-milestone-loop) and of the working rules in
  [CLAUDE.md](../CLAUDE.md).
- **Date:** 2026-10-16 · **Decided by:** project owner (no code change)

### 28. Request checks run before the body is read

- **Context:** FastAPI parses a request body before it resolves route
  dependencies, so the API-key and size checks, both dependencies, ran only
  after the whole body had arrived.
- **Decision:** A pure ASGI middleware (`app/security/request_guard.py`,
  installed in `app/main.py` and `app/insight_main.py`) runs the same API-key
  check, then the same Content-Length check, for POST, PUT and PATCH requests
  to the guarded route prefixes, with the same status and detail, and counts
  the body bytes that arrive. The router dependencies stay as the second
  layer and for GET and DELETE.
- **Consequence:** A refused request's body is never passed to the app. An
  unauthenticated request with an invalid body now gets the authentication
  error instead of 422, and a body-method request to an unknown path under a
  guarded prefix gets the authentication or size error instead of 404 or 405.
  Response shapes did not change. `app/insights/tests/test_request_guard.py`
  pins this. The project owner accepted the authentication-before-422 order
  on 2026-10-17.
- **Date:** 2026-10-16 · **Commit:** `ff874de`

### 29. One pipeline run at a time per worker, off the event loop

- **Context:** `POST /v1/process-audio` is an async route, but it ran the
  whole pipeline synchronously, so a worker answered nothing else during a
  run.
- **Decision:** The pipeline and its fact-check enrichment run in a worker
  thread behind a one-slot limiter per event loop
  (`app/routes/process_audio.py`). A further upload waits for the slot.
  How many runs a worker allows, and what an overloaded worker answers, were
  decided later (#35).
- **Consequence:** Health checks and other routes are served during a run;
  runs stay one at a time per worker, as before.
  `app/insights/tests/test_process_audio_concurrency.py` pins both.
- **Date:** 2026-10-16 · **Commit:** `2bb6593`

### 30. Insight routes accept only finite numbers

- **Context:** JSON parsing accepts `NaN`, `Infinity` and numbers that
  overflow to infinity. Such a value could be stored and then fail every
  response that rendered it, including the 422 meant to reject it.
- **Decision:** The `/v1/insights` router uses `FiniteJSONRoute`
  (`app/insights/api/finite_json.py`): such bodies get the standard 422
  `json_invalid` error while parsing. The insight input models refuse
  non-finite numbers for in-process callers; the raw-session normalizer
  treats them like invalid values; a non-finite Agent Brain confidence
  counts as missing.
- **Consequence:** Nothing non-finite is stored from these routes;
  `/v1/insights/generate-from-raw` refuses such tokens rather than
  correcting them. `app/insights/tests/test_non_finite_inputs.py` pins this.
  The project owner accepted that refusal on 2026-10-17.
- **Date:** 2026-10-16 · **Commit:** `93dda14`

### 31. M6: the PDF prints Latin-1 and substitutes the rest

- **Context:** The report's core Helvetica font covers Latin-1 only, and one
  character outside it failed the whole PDF.
- **Decision:** `to_latin1` (`app/services/pdf_service.py`) maps common
  punctuation to its nearest Latin-1 form, drops zero-width spaces and
  byte-order marks, and turns every other character into "?",
  in `safe_text`, which all report text passes through.
- **Consequence:** The report is produced for any text; Urdu, Arabic and
  emoji print as "?" until a Unicode font is added (required before Phase 6). The JSON response
  keeps the original text. `app/insights/tests/test_pdf_unicode.py` pins this.
  The project owner confirmed on 2026-10-17 that M6 is containment only: a
  Unicode-capable PDF font is still required before Phase 6.
- **Date:** 2026-10-17 · **Commit:** `dc9caf3`

### 32. M7: keyword rules match whole words only

- **Context:** The pipeline's flag rules (hesitation, aggression, lie risk),
  its intent rules and the Agent Brain's email-draft triggers used a plain
  substring check, so a keyword inside a longer word matched: "um" in
  "number", the greeting "hi" at the start of "his order", "ok" in "book",
  "send a" in "send all".
- **Decision:** `app/utils/text_match.py` matches a keyword only where it is
  not part of a longer word; a punctuation edge ("...", "?") matches
  anywhere. `FlagService` and `IntentService` use it for every rule.
  `find_signals(..., whole_words=True)` is used by the email-draft agent
  only. No vocabulary changed.
- **Consequence:** Fewer false flags, intents and email drafts. The other
  Agent Brain agents keep substring matching. Pinned by
  `app/insights/tests/test_whole_word_rules.py` and
  `app/agent_brain/tests/test_email_signal_whole_words.py`.
- **Update (2026-10-18, owner):** whole-word matching also dropped forms the
  substring check had caught through a shorter keyword ("send an invoice",
  "share these slides", "hates", "umm", "apologized"). The owner chose to
  list those forms now rather than accept the misses. The email triggers
  gained the article and pronoun forms, and the flag and intent lists the
  inflected and drawn-out ones; the email title drops a leading article
  only as a whole word. "Send an engineer" now gives an email suggestion
  too, an accepted trade-off because every recommendation needs approval
  (`ee2e15b`).
- **Date:** 2026-10-17 (whole-word matching), 2026-10-19 (forms listed) ·
  **Commit:** `dbae452`, `ee2e15b`

## Security and reliability decisions (2026-10-17)

Owner decisions on items from a security and reliability review (its
detailed list is tracked privately). Each code change is its own small
commit with its own tests, under the size rule (#27).

### 33. One trust group for API keys

- **Context:** Every valid API key can read, replace or delete any stored
  record (#8).
- **Decision:** The service has one trust group. Owner policy: only the Java
  backend calls it, and it stays off the public internet; a deployment must
  meet both. There is no per-session ownership for now.
- **Consequence:** Anyone who holds a key can act on every record. Revisit
  before any customer can reach the service directly.
- **Date:** 2026-10-17 · **Commit:** — (owner decision, no code)

### 34. Staging fails closed; compose binds to loopback

- **Context:** With no API keys configured, only `production` refused
  requests, and the compose file published the port on every interface.
- **Decision:** `staging` also answers 503 when no keys are configured
  (`app/security/api_key.py`); `development` and `test` still allow
  requests with a warning. `docker-compose.yml` publishes the port on
  `127.0.0.1` only.
- **Consequence:** A shared environment without keys fails closed. Serving
  other hosts needs a TLS-terminating proxy in front (#47).
  `app/insights/tests/test_deployment_defaults.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `d7f9075`

### 35. Bounded wait for a pipeline slot; 503 beyond it

- **Context:** #29 made uploads wait for the worker's pipeline slot, with no
  bound on how many could wait.
- **Decision:** Per worker, at most `VOICEIQ_PROCESS_AUDIO_MAX_RUNNING`
  runs (default 1) and `VOICEIQ_PROCESS_AUDIO_MAX_WAITING` waiting uploads
  (default 2). A further upload gets 503 with `Retry-After:
  VOICEIQ_PROCESS_AUDIO_RETRY_AFTER_SEC` (default 30), before a job folder
  is created (`app/routes/process_audio.py`).
- **Consequence:** A busy worker refuses instead of queueing without limit.
  The refusal comes only after the whole upload has been received, because
  FastAPI reads the form before the route runs, so this bounds pipeline
  runs, not upload traffic. `app/insights/tests/test_process_audio_concurrency.py`
  pins this.
- **Date:** 2026-10-17 · **Commit:** `948c49d`

### 36. Audio longer than two hours is rejected, never truncated

- **Context:** Audio of any length was decoded and processed.
- **Decision:** Audio longer than `VOICEIQ_MAX_AUDIO_SECONDS` (default 7200)
  gets 413 with the warning code `AUDIO_TOO_LONG`. ffprobe reads the
  duration before decoding; ffmpeg also stops one second past the limit,
  and a decoded file longer than the limit is deleted and rejected
  (`app/utils/audio_utils.py`). Nothing is truncated.
- **Consequence:** `AUDIO_TOO_LONG` is a fifth hard-fail gate (#1). Pinned by
  `app/insights/tests/test_audio_duration.py` and
  `test_exactly_five_hard_fail_gates`.
- **Date:** 2026-10-17 · **Commit:** `6c17c72`

### 37. Rate limits on the insight routes; the session list is paged

- **Context:** The `/v1/insights` routes had no rate limit, and the session
  list returned every id.
- **Decision:** Routes that store or delete a record count against
  `VOICEIQ_RATE_LIMIT_INSIGHT_WRITE` (default `60/minute`), routes that read
  against `VOICEIQ_RATE_LIMIT_INSIGHT_READ` (default `120/minute`), per
  caller. Every route group keeps its own counter. `GET /v1/insights/` takes
  optional `limit` (default 100, at most 500) and `offset`.
- **Consequence:** A caller with more than 100 sessions now pages through
  them. `app/insights/tests/test_insight_limits.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `e802fcd`

### 38. A fact-check run checks at most 25 claims and starts none after 20 seconds

- **Context:** A run checked every detected claim, one provider call after
  another, with no overall time limit.
- **Decision:** `FactCheckEngine` checks at most `VOICEIQ_FACTCHECK_MAX_CLAIMS`
  claims (default 25) and starts no new check after
  `VOICEIQ_FACTCHECK_RUN_DEADLINE_SEC` (default 20). The deadline is
  checked between claims, so a check already running is not cut short: it
  can make up to two attempts, each limited by
  `VOICEIQ_FACTCHECK_HTTP_TIMEOUT_SEC` (default 5) per network operation.
  The 20 seconds is not a hard bound. Claims past either limit get no
  result and are counted in `stats.skipped`.
- **Consequence:** The response shape is unchanged.
  `app/insights/tests/test_factcheck_run_limits.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `da92ae4`

### 39. Stored records are deleted after 30 days

- **Context:** Insight records and fact-check results stayed in the database
  until deleted by hand (#13).
- **Decision:** Records older than `VOICEIQ_DB_RETENTION_DAYS` (default 30;
  `0` keeps everything) are deleted: insight records by last update,
  fact-check results by creation. The sweep in `app/utils/retention.py`
  purges them with the job directories, at startup and then hourly from
  each app's lifespan.
- **Consequence:** The insight-only Docker image, which never handles
  uploads, now purges while it stays up.
  `app/insights/tests/test_record_retention.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `cbdafb4`

### 40. An error result never replaces a good stored record

- **Context:** Regenerating insights for a session and getting an error
  overwrote the stored ok or warning record.
- **Decision:** `InsightRepository.save` keeps a stored record that is not
  an error when the new one is. The caller still gets the error response.
- **Consequence:** `app/insights/tests/test_insight_keep_good_record.py` pins
  this.
- **Date:** 2026-10-17 · **Commit:** `f8bc3ed`

### 41. An insight error result fails the pipeline stage

- **Context:** `InsightService` turns an engine fault into `status="error"`
  with an empty bundle (CLAUDE.md, fault isolation). The pipeline recorded
  the stage as ok and passed the empty bundle to the Agent Brain.
- **Decision:** An error result marks the stage failed, adds
  `INSIGHTS_FAILED` and skips the Agent Brain. The run stays fail-soft.
- **Consequence:** Pinned in `app/insights/tests/test_orchestrator.py`.
- **Date:** 2026-10-17 · **Commit:** `c53c71d`

### 42. A missing stage package fails only that stage

- **Context:** Eight stages imported their service before their `try`, so a
  package that could not be imported failed the whole request.
- **Decision:** The import sits inside each stage's `try` (#2 still holds:
  the imports stay lazy).
- **Consequence:** The stage records its `*_FAILED` warning and the run
  continues. Pinned in `app/insights/tests/test_orchestrator.py`.
- **Date:** 2026-10-17 · **Commit:** `0f9c4c1`

### 43. Pipeline sentiment reaches the insight engines as positivity

- **Context:** The sentiment service reports a label and its confidence in
  that label; the insight engines read the score as positivity.
- **Decision:** The pipeline adapter maps a positive label with confidence c
  to 0.5 + c/2, a negative one to 0.5 − c/2, and neutral to 0.5.
- **Consequence:** Insight scores for processed audio change (see
  [CHANGES.md](CHANGES.md)). `/v1/insights` callers that send their own
  scores are unaffected. `app/insights/tests/test_voiceiq_adapter_sentiment.py`
  pins this.
- **Date:** 2026-10-17 · **Commit:** `98730c3`

### 44. A warning whenever mock diarization is used

- **Context:** Without a working diarization model the service falls back to
  a mock that puts the whole call under one speaker, and the response did
  not say so.
- **Decision:** Such a run carries `MOCK_DIARIZATION_USED`. The two-speaker
  default for calls is kept.
- **Consequence:** Pinned in `app/insights/tests/test_orchestrator.py`.
- **Date:** 2026-10-17 · **Commit:** `2721a73`

### 45. The log level setting is applied; threshold profiles are not implemented

- **Context:** `VOICEIQ_LOG_LEVEL` and `VOICEIQ_THRESHOLD_PROFILE` were
  validated but never read.
- **Decision:** Each app applies `VOICEIQ_LOG_LEVEL` at startup; `httpx` and
  `httpcore` stay at WARNING. Threshold profiles are marked not implemented,
  and each app logs a warning at startup when the variable is set. No
  profiles were added.
- **Consequence:** `app/insights/tests/test_logging_settings.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `e25a3b2`

### 46. A readiness probe beside the liveness probe

- **Context:** `/healthz` answers whenever the process is up, even if the
  database or ffmpeg is unavailable.
- **Decision:** `GET /readyz` returns 200 only when the database answers
  and, on the full service, ffmpeg is on PATH; otherwise 503 naming the
  failed check (`app/utils/readiness.py`). `/healthz` stays the liveness
  probe.
- **Consequence:** `app/insights/tests/test_readiness.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `660558c`

### 47. Container resource limits; TLS terminates at the ingress

- **Context:** The compose file set no resource limits, and the TLS set-up
  was described only as a recommendation.
- **Decision:** `docker-compose.yml` caps the container at 2 CPUs and 1 GB.
  TLS terminates at the ingress, a trusted reverse proxy or load balancer
  ([SECURITY.md](SECURITY.md)).
- **Consequence:** The compose file was checked as YAML and by test; Docker
  was not available to run `docker compose config`.
- **Date:** 2026-10-17 · **Commit:** `fb56d7d`

### 48. Currency rates come from open.er-api.com

- **Context:** exchangerate.host requires an access key on every call, so
  every currency-rate claim was `SOURCE_UNAVAILABLE` (#23).
- **Decision:** Option A: ExchangeRate-API's keyless open access endpoint.
  Only the base currency code is sent; each base is fetched at most hourly;
  every evidence record carries the attribution the provider requires.
- **Consequence:** Currency-rate claims can be verified again.
  [DATA-FLOW.md](DATA-FLOW.md) and
  [PHASE-0 §6.4](FACTCHECK-AGENT-PHASE-0.md) changed in the same commit.
- **Date:** 2026-10-17 · **Commit:** `45a5b22`

### 49. A failed topic step returns a fixed message

- **Context:** The topic result is part of the `/v1/process-audio` response,
  and a failed topic step put the exception text in it.
- **Decision:** The response carries a fixed message; the exception text is
  written only to the job's status file on disk.
- **Consequence:** `app/insights/tests/test_pipeline_error_text.py` pins this.
- **Date:** 2026-10-17 · **Commit:** `dc97a84`

## Phase 0 sign-off (2026-10-19)

The project owner signed these on 2026-10-19. What each register decision
chose, and the phase it gates, is in the
[register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register); this file does
not repeat it.

### 50. One owner holds every Phase 0 role

- **Context:** The Phase 0 register names separate owner roles: tech lead,
  engineering manager, product, senior sponsor, infrastructure and legal.
  The project has one maintainer.
- **Decision:** The project owner (sole maintainer) holds every one of those
  roles and signs in each. The repository records signers by role only; a
  private sign-off record outside the repository holds the names.
- **Consequence:** Where a decision needs a party outside the project, the
  owner's signature does not cover it: the Java action layer team's
  acknowledgement of PHASE-0 §3, and the API consumer's written sign-off
  before any later removal of v1 (D8).
- **Date:** 2026-10-19 · **Commit:** — (owner)

### 51. Phase 0 is complete; Phase 1 may start

- **Context:** Phase 1 could not start until the register decisions it
  depends on were signed and PHASE-0 §2, §3 and §6 were approved. Three of
  ten decisions were signed, and several decisions the work needed were not
  in the register.
- **Decision:**
  - The Phase 0 gate is read per phase (W1): each register row gates the
    phase its *Needed by* column names.
  - D3–D9 are signed, and D3b, D10-T and U1–U10 are added to the register
    with their own phase dates and signed.
  - PHASE-0 §2 (API contract), §3 (compatibility) and §6 (privacy and source
    policy) are approved with their amendments. §5 and §7 are approved with
    §3 (W7). `ClaimSpan` joins the v1 freeze list (W2), and errata against
    plan v1.1 are recorded (W3).
  - Gate record (G1): Phase 0 opened 2026-09-04; the decisions were recorded
    on 2026-10-19.
- **Consequence:** Phase 1 may start. Some values are still open, each to be
  set before the phase that uses it; they are listed in
  [PHASE-0 §8](FACTCHECK-AGENT-PHASE-0.md#8-exit-checklist) and under
  [Open decisions](#open-decisions). No code changed.
- **Date:** 2026-10-19 · **Commit:** — (owner)

### 52. Gold-set labelling may start before the Phase 1 gate

- **Context:** Building the evaluation set is Phase 1 work in plan v1.1.
  Labelling is data preparation, not code.
- **Decision:** With D3 (Option A) and D7 signed, gold-set labelling may
  start at once. The set is English and synthetic-first. Drafts are
  machine-assisted and the project owner checks every item; consistency is
  checked by a blind re-label of 40 items after one week (intra-rater
  agreement), in place of a second annotator. Only synthetic material is
  used until a written rule for real calls exists (U10).
- **Consequence:** The gold set is off the Phase 1 critical path. Verdict
  labels (D3b) wait for Phase 4.
- **Date:** 2026-10-19 · **Commit:** — (owner)

---

## Diarization model (2026-10-24)

### 53. Diarization uses pyannote 3.1 under the weights-only loader

- **Context:** The default pipeline, `pyannote/speaker-diarization` (2.x),
  no longer loaded with the pinned stack: its speaker-embedding model
  rejects an argument pyannote passes. `pyannote/speaker-diarization-3.1`
  failed PyTorch's weights-only loader. Every job therefore fell back to
  the one-speaker mock.
- **Decision:** The project owner approved the fix round on 2026-10-24.
  The default model is `pyannote/speaker-diarization-3.1`. The service
  allows the six classes its checkpoints store and keeps PyTorch's
  weights-only loader on; turning it off
  (`TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`) is not a supported setting.
  `requirements.txt` declares matplotlib, which pyannote.audio 3.3.2
  imports without declaring. A run whose pipeline never loaded carries
  `DIARIZATION_UNAVAILABLE`, and the mock reads the duration from the
  file header.
- **Consequence:** The token's account must accept the conditions of the
  gated `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`.
  The 0.65 clustering threshold set in code still overrides the 3.1
  default (about 0.70); whether to keep it is open (below).
  `app/insights/tests/test_diarization_service.py` and
  `test_mock_diarization_is_flagged` pin this.
- **Date:** 2026-10-24 · **Commit:** `9887e0b`

---

## Phase 1 bake-off inputs (2026-10-28)

### 54. D5 run deadline set; D6 criteria note signed

- **Context:** The Phase 0 sign-off left D5's provisional run deadline
  open. The signed D6 line lets Qwen3.5-4B win the Phase 1 bake-off only
  under a criteria note signed before the first run, and that note's
  latency gate uses the deadline.
- **Decision:** The project owner set the run deadline on 2026-10-28 at
  125 s, with a D10 re-run allowance of 25 s, and signed the D6 criteria
  note, [D6-CRITERIA-NOTE-S1.md](D6-CRITERIA-NOTE-S1.md), in the senior
  sponsor role. The note fixes the three candidates, the run conditions,
  the scoring, the gates, the margin (literal reading) and the decision
  rule before any result exists. The gold-set guide's Q15 note was added
  to PHASE-0 §6.
- **Consequence:** With one job at a time, one run may wait:
  (1 + 1) × (125 s + 25 s) = 300 s, D5's p95 budget. The note's latency
  gate gives the model 62.5 s per session at the 20-claim cap. Both are
  provisional and are re-checked once Phase 1 has measured latency.
- **Date:** 2026-10-28 · **Commit:** — (owner)

### 55. D6 criteria note S2: launch line

- **Context:** The pinned llama.cpp build (`b11936`) does three things by default that S1's
  frozen launch line left on. It fits layers and context automatically, although S1 says fitting
  is off. It keeps a host-RAM prompt cache that also does work for requests that send
  `cache_prompt: false`. And it makes context checkpoints for models whose memory cannot be rolled
  back, which among the candidates means Qwen3.5-4B only.
- **Decision:** The project owner signed S2, [D6-CRITERIA-NOTE-S2.md](D6-CRITERIA-NOTE-S2.md), on 2026-10-30
  in the senior sponsor role, before any bake-off run. It adds `-fit off --cache-ram 0 -ctxcp 0`
  to the launch line, and a check that the running server's command line equals that line.
  Nothing else changes.
- **Consequence:** Timed calls carry no cache or checkpoint work, and gate M's RAM figure leaves
  out the prompt cache, so all three candidates run under the same conditions. S1 stays as the
  record of the first signature.
- **Date:** 2026-10-30 · **Commit:** — (owner)

---

## Open decisions

These are not settled yet. None is in force. Details are in
[PHASE-0 §8](FACTCHECK-AGENT-PHASE-0.md#8-exit-checklist), the
[register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register) and
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md#blockers).

**Values left open at the Phase 0 sign-off (2026-10-19)**

- The runner and admission defaults that follow from D5's run deadline,
  set on 2026-10-28 ([#54](#54-d5-run-deadline-set-d6-criteria-note-signed)).
- Whether `fact_check_report` shows `disabled` or `skipped` when the agent is
  enabled but its tables are missing (U7).
- Whether `app.main` shares its `data/` volume with `app.insight_main`, and
  the `app.main` deployment recipe (U9).
- The maximum evidence age per claim type, the remaining provider and
  Wikipedia terms, and the maximum excerpt length (PHASE-0 §6.3–§6.5).
- The Java action layer team's acknowledgement of PHASE-0 §3.

**Other open owner decisions**

- Items from the security and reliability review that the owner parked,
  most of them waiting on the partner team (tracked privately).
- The diarization clustering threshold: keep the 0.65 set in code, or use
  the pyannote 3.1 default ([#53](#53-diarization-uses-pyannote-31-under-the-weights-only-loader)).
