# VoiceIQ Insight Service — Project Context

This file is the project brief for any AI assistant working on this repo. Read it first before answering, suggesting changes, or running tools.

## What this project is

**VoiceIQ Insight Service** is a production-grade, FastAPI-based conversation analytics pipeline. It ingests audio, processes it through an ASR + diarization + NLP stack, and emits structured intelligence (insights, scores, timelines, summaries) plus a PDF report.

**Repo:** https://github.com/HAMZAJAWED12/Voice-iq

**High-level flow:**

```
Audio Upload → ASR → Diarization → Alignment → Metadata → NLP Enrichment
            → Insight Service → PDF Report → API Response
```

The Insight Service (`app/insights/`) is the current focus of active development.

## Repository layout

```
voiceiq-AI/
├── app/
│   ├── insights/              # ← primary work area
│   │   ├── core/              # engines (one file per engine)
│   │   │   ├── signal_aggregation.py
│   │   │   ├── rule_engine.py            # main orchestrator
│   │   │   ├── timeline_engine.py
│   │   │   ├── scoring_engine.py
│   │   │   ├── summary_engine.py
│   │   │   ├── escalation_engine.py
│   │   │   ├── inconsistency_engine.py
│   │   │   ├── analytics_engine.py
│   │   │   ├── normalizer.py
│   │   │   └── validator.py
│   │   ├── models/            # Pydantic models (one file per concern)
│   │   │   ├── input_models.py
│   │   │   ├── signal_models.py
│   │   │   ├── insight_models.py
│   │   │   ├── analytics_models.py
│   │   │   ├── api_models.py
│   │   │   ├── escalation_models.py
│   │   │   └── inconsistency_models.py
│   │   ├── api/               # FastAPI routes
│   │   ├── repository/        # SQLAlchemy ORM + SQLite persistence
│   │   ├── adapters/          # external integration adapters
│   │   ├── config/            # settings module
│   │   ├── service.py         # orchestration service
│   │   └── tests/             # pytest tests for the insight layer
│   ├── agent_brain/           # Sprint 6: action-recommendation layer
│   │   ├── models/            # AgentContext, Recommendation (camelCase Java contract)
│   │   ├── core/              # 5 agents + confidence/dedup/ranker/runner
│   │   ├── extraction/        # signals, assignee/date/priority extractors
│   │   ├── api/               # POST /internal/v1/agent-brain/recommendations/generate
│   │   ├── adapters/          # internal SessionInput/Insight/FactCheck -> AgentContext
│   │   ├── integrations/      # HMAC-signed Java callback client
│   │   ├── config/, service.py, tests/
│   ├── pipeline/              # upstream pipeline (ASR, NLP)
│   ├── routes/
│   ├── services/              # ASR, diarization, sentiment, etc.
│   ├── utils/
│   └── main.py                # FastAPI entrypoint
├── DOCS/                      # markdown handoff documentation
├── tests/                     # legacy / top-level tests
├── samples/                   # sample audio
├── .github/workflows/test.yml # CI pipeline (lightweight)
├── Dockerfile
├── requirements.txt           # heavy: torch, whisper, pyannote, transformers
├── requirements-insight.txt   # lightweight: FastAPI, Pydantic, SQLAlchemy, pytest
├── requirements-dev.txt       # eval/metrics deps
└── VoiceIQ-Insight-Service-Handoff.docx
```

## Sprint status

| Sprint | Scope | Status |
|---|---|---|
| 1 | Foundation: Signal Aggregation, Rule, Timeline, Scoring, Summary engines | ✅ Done |
| 2 | Intelligence: Escalation Engine + Inconsistency Engine | ✅ Done |
| 3 | Persistence + ops: SQLite via SQLAlchemy, Dockerfile, OpenAPI polish, integration tests | ✅ Done |
| 4 | Handoff documentation: DOCS/ markdown set + polished .docx | ✅ Done |
| 5 | Fact-check engine: claim detector, classifier, comparator, scorer, orchestrator; 5 source clients (exchangerate.host, CoinGecko, OpenWeather, Alpha Vantage, Wikipedia REST); SQLite `fact_check_results` table; `POST /v1/fact-check` + `GET /v1/fact-check/{id}`; auto-enrichment in `/v1/process-audio`; 91 new tests (118 insight tests passing) | ✅ Done |
| CI | GitHub Actions: lint (ruff + mypy hard-gate + gitleaks) + tests on Python 3.10/3.11, every push/PR | ✅ Done |
| Tier 1 | Production-readiness security: X-API-Key auth, payload-size caps, ffmpeg subprocess timeout, model-loader concurrency locks | ✅ Done |
| Tier 2 | Test-coverage closure: 6 insight-core engines to 100%; CI ruff + gitleaks + mypy + 3.10/3.11 matrix | ✅ Done |
| Tier 3 | Waves A/B/D: cleanups, schema fixes, mypy hard-gate | ✅ Done |
| Sprint 6 | Agent Brain (`app/agent_brain/`): 5 rule-based agents (Task/FollowUp/Email/Escalation/FactCheckReview), confidence refine, difflib dedup, ranker, runner w/ per-agent fault isolation, internal API, pipeline adapter, HMAC Java callback; 103 tests, 100% | ✅ Done |
| Tier S | Security hardening (8 items): rate limiting, job-artifact TTL, opt-in fact-check + `DOCS/DATA-FLOW.md`, error-detail leak, filename guard, URL encoding, callback replay/TLS, prod docs gating. See `DOCS/SECURITY-HARDENING-PLAN.md` | ✅ Done |
| Sprint 7 | **Fact-Check Agent** — open-source LLM (Qwen3) claim extraction, hybrid retrieval, evidence-grounded reasoning, citations. Phases 0–7. Phase 0 (contract lock, no code) in `DOCS/FACTCHECK-AGENT-PHASE-0.md`; D1 + D2 signed | 🟡 Phase 0 — 2/9 decisions signed |

**Currently open:** Sprint 7 Fact-Check Agent, at Phase 0 with 2 of 9
decisions signed — D3–D9 still block Phase 1
(`DOCS/FACTCHECK-AGENT-PHASE-0.md` §1). Also open: Agent Brain Phase 2
(NLP/model extraction; see the handoff doc §13). **Tier 3 Wave E is now
closed** — E1/E1.b/E2/E3/E4/E5 all done.

> **Sprint 7 hard constraints** (settled in Phase 0 — do not relitigate):
>
> 1. **New tables only** (D1). The repo has no Alembic; `init_db()` is
>    `create_all()`, which creates missing *tables* and never `ALTER`s one.
>    A column added to an existing table lands on a fresh DB and is silently
>    absent on one with prior data — every fresh-DB test passes.
> 2. **Job-based execution** (D2). `/v1/process-audio` returns before the
>    agent finishes, so `fact_check_report` is a *state envelope*
>    (`pending|complete|failed|disabled|skipped`) and
>    `GET /v2/fact-check/{runId}` is required, not optional. The PDF must
>    not ship with a silently empty fact-check section.
> 3. **v1 is frozen.** `FactCheckRequest`/`Response`, `ClaimType`, `Verdict`
>    take no new members. The agent gets its own models.
> 4. **No default in the verdict map.** `pipeline_adapter._VERDICT_TO_STATUS`
>    must lose its `.get(..., "UNVERIFIED")` fallback before v2 verdicts
>    arrive: `CONTRADICTED` would fall through to `UNVERIFIED` and silently
>    drop the review recommendation from `CRITICAL` to `HIGH`. Non-checkable
>    statements are excluded *before* mapping — otherwise every opinion in a
>    call generates a manual-review recommendation.

## Engineering standards (STRICT)

These are non-negotiable. Violations have cost time before — uphold them.

1. **Clean architecture.** Every feature is its own engine + models + tests. Never mix logic across engines. Layout: `core/<feature>_engine.py`, `models/<feature>_models.py`, `tests/test_<feature>.py`.
2. **Production safety.** Handle empty sessions, missing sentiment/emotion, null values. Use safe defaults. No crashes from upstream gaps.
3. **Pydantic everywhere.** Strict, validated types. Don't break the existing schema without explicit reason.
4. **Explainability.** Every output must include `reason` and `evidence`. No black-box decisions.
5. **Score discipline.** All scores `0.0 ≤ score ≤ 1.0`. Use the `_clamp()` helper everywhere.
6. **Timeline integrity.** Apply thresholds carefully. Don't introduce noisy markers. Avoid over-triggering.
7. **Code quality.** No redundant calculations, no duplicated logic, no unused imports. Keep functions focused.

## Orchestration + fault isolation

Fault handling lives at the **service boundary, not inside the engines.**

- `InsightRuleEngine.run` composes five sub-engines (signal aggregation → timeline → escalation → inconsistency → scoring). It deliberately has **no internal `try/except`**: a sub-engine exception propagates up by design. Do not add per-sub-engine fallbacks there — it would mask real failures and produce silently-partial bundles.
- The single fault boundary is `InsightService` (`app/insights/service.py`): the `analytics → rule → summary` call chain runs inside one `try/except` that converts any engine failure into a `status="error"` response with the validation result preserved. Keep new orchestration failure handling at this layer.
- Engines still uphold standard #2 (production safety) for *upstream data gaps* — empty sessions, missing sentiment, nulls — via safe defaults. That is different from *engine-level faults*, which are the service's responsibility.

**Two fault-isolation models live in this repo — pick by topology, not preference:**

| | `InsightRuleEngine` (insight pipeline) | `AgentRunner` (agent brain) |
|---|---|---|
| Topology | **Linear** dependency chain (each stage feeds the next) | **Independent fan-out** (agents don't depend on each other) |
| On a component fault | **Raise** — propagate up | **Catch + log + skip** that one agent |
| Partial output | Invalid — a missing mid-stage corrupts everything downstream | **Valid** — the other agents' recommendations still stand |
| Fault boundary | `InsightService` try/except → `status="error"` | inside `AgentRunner` itself (per-agent try/except) |

The reason they differ: in a linear pipeline a swallowed mid-stage failure produces a *silently wrong* bundle, so failing loud is safer. In an independent fan-out, one agent crashing should not deny the user the other four agents' correct output, so isolating per-agent and returning partial results is safer. Do not "harmonise" these — applying the rule-engine rule to the runner would drop good recommendations; applying the runner rule to the pipeline would hide corruption.

## Security + authenticity rules

- Don't expose internal logic in API unnecessarily.
- Don't log sensitive transcript content.
- Don't modify upstream ASR/NLP outputs.
- Don't fabricate signals — only derive from real data.
- All outputs must be traceable and justifiable.

## Testing requirements

For every new feature:

- Unit tests for core logic.
- Edge cases: empty input, neutral conversation, extreme values.
- Integration safety — must not break the pipeline.
- Tests live under `app/insights/tests/`.

Run locally:

```bash
pytest -v app/insights/tests/
```

CI runs the same command on every push to `main` via `.github/workflows/test.yml`, against the lightweight `requirements-insight.txt` stack only — not the heavy ML deps.

### One test lane (since N2) — the counts reconcile

| Where | Command | Count |
|---|---|---|
| Local (heavy deps installed) | `pytest app/insights/tests/ app/agent_brain/tests/` | **714** |
| CI `test` job (light, 3.10 + 3.11) | same command, no `--ignore` | **711 + 1 skip** |

711 + the 3-test `test_model_load_concurrency.py` module (module-level `pytest.importorskip("torch")`, reported as a single skip) = 714. Nothing is lost in CI.

Keep these numbers current — a stale reconciliation is worse than none, because the next person cannot tell a real gap from drift.

`orchestrator.py` imports its eight ML-backed services **inside** the `_run_<stage>` methods, so importing it pulls nothing heavy and `test_orchestrator.py` runs on `requirements-insight.txt`. The separate ~8–12 minute `orchestrator-harness` job is retired. Verified in a venv built from the light requirements alone: harness 51/51 in ~8 s, full suite ~16 s.

**Keep new heavy imports out of `orchestrator.py`'s module scope** — put them in the stage method that uses them, or the light lane breaks again. Four services that only *look* heavy are deliberately module-scope: `FactCheckService` (httpx), `PDFService` (fpdf), `normalize_to_wav` (subprocess), and `EmotionService`, which guards its torch import in its own try/except.

**Harness services must be mocked, not run real, when they touch model artifacts.** `KeywordService` looks pure-python but calls `spacy.load("en_core_web_sm")` and builds a `SentenceTransformer`. The spaCy model is a separate `python -m spacy download` artifact declared in no requirements file — present on a dev machine, absent in CI. That divergence passed locally and failed CI with `KEYWORDS_FAILED`. Rule: a service is **heavy** if it imports an ML lib **or** loads a model artifact; heavy ⇒ mocked. Never fix this class of failure by installing the model in CI — a safety net must not depend on model artifacts or a network fetch.

## CI pipeline notes

`.github/workflows/test.yml` deliberately installs only `requirements-insight.txt` (FastAPI, Pydantic, SQLAlchemy, pytest, httpx) — not `requirements.txt`. The heavy ML deps (torch, whisper, pyannote, transformers) would push CI runtime from ~30 seconds to 8–12 minutes and burn free Actions minutes. Insight tests don't need them. If you ever add tests that genuinely require the heavy stack, create a *separate* workflow file or job — don't bolt it onto this one. (There is no such job today: N2 made the orchestrator's ML imports lazy, so the harness rejoined this lane and the old `orchestrator-harness` job was retired.)

**Diagnosing a CI failure without log access.** GitHub now requires sign-in to read Actions logs even on public repos, and `GET /actions/jobs/{id}/logs` returns `403 Must have admin rights`. Annotations, however, *are* readable unauthenticated. The `test` job therefore pipes pytest output to a file and, on failure, emits the tail as a `::error::` annotation. Read it with:

```bash
curl -s https://api.github.com/repos/HAMZAJAWED12/Voice-iq/actions/runs/<RUN_ID>/jobs
curl -s https://api.github.com/repos/HAMZAJAWED12/Voice-iq/check-runs/<JOB_ID>/annotations
```

Keep that step — it is the only way to see why the harness failed without repo-admin rights.

## Known issues / tech debt

These need attention but are not blocking new work:

1. **`__pycache__/*.pyc` files tracked in git.** They were committed before `.gitignore` existed. Untrack with `git rm -r --cached app/**/__pycache__` and commit. They'll then be permanently ignored.
2. **`run_eval_dev.LOCAL.py` exists alongside `run_eval_dev.py`.** Renamed during a merge conflict. Decide whether to merge or delete.
3. **Wave E punch list lives in CLAUDE.md next-task candidates.** Tier 3 Waves A/B/D consumed the per-engine `# Tier 3 candidates` comment blocks (dead params, identity maps, type hints, schema fixes, mypy gate). The remaining structural items are scoped under next-task candidates below.
4. ~~**E3 — `alignment_service` O(n²)**~~ **RESOLVED.** See the E3 entry under next-task candidates for the final numbers and the one trap worth remembering.

## Working with this repo

- **OS:** Windows. PowerShell is the default shell. Paths use backslashes.
- **OneDrive:** The repo lives inside a synced OneDrive folder. This causes occasional `index.lock` collisions during git operations — pause OneDrive sync if git starts fighting itself.
- **Python:** Use the project `.venv` for local work. CI tests on a Python 3.10 + 3.11 matrix.
- **Interpreter trap:** a bare `python`/`pytest` on PATH may resolve to a different interpreter (e.g. `D:\Downloads\python`) that is **missing the project deps** (`pydantic_settings`, etc.) — collection then dies with confusing `ModuleNotFoundError`s. Always run via the venv: `.venv\Scripts\python.exe -m pytest ...` / `.venv\Scripts\python.exe -m mypy ...`. `where python` surfaces the trap.
- **Git editor:** Set to `notepad` to avoid vim swap-file disasters: `git config --global core.editor notepad`.
- **Never** run `git add .` without checking `git status` first — it has previously staged the entire `.venv` (10,000+ files).
- **Security config knobs (Tier S).** All `VOICEIQ_`-prefixed env vars: `JOB_RETENTION_HOURS` (default 24, `0` disables the job-artifact purge), `RATE_LIMIT_ENABLED` / `RATE_LIMIT_PROCESS_AUDIO` (`30/minute`) / `RATE_LIMIT_FACTCHECK` (`60/minute`), `FACTCHECK_AUTO_ENRICH` (default **false** — `/v1/process-audio` no longer fact-checks unless asked; pass `?fact_check=true` per request). Setting `ENVIRONMENT=production` additionally hides `/docs`, `/redoc`, `/openapi.json` and makes missing `VOICEIQ_API_KEYS` fail closed with 503.

## Next-task candidates (pick one when ready)

### ✅ Tier 3 Wave E — CLOSED (structural hardening)

Nothing here is a candidate any more; kept as the record of what each item
turned out to be. Wave E was deferred from the original Tier 3 pass.

- ✅ **Consolidate `_clamp()` (E1 / E1.b).** Done — single `core/_math.py:clamp`; `scoring_engine`, `signal_aggregation`, `inconsistency_engine`, and `factcheck/scorer` all repointed.
- ✅ **MIME / magic-byte upload check (E4).** Done — `app/utils/audio_sniff.py` rejects non-audio uploads with 415; extension check kept as first gate.
- ✅ **E2 — orchestrator decomposition COMPLETE (Phase 1 + Phase 2).**
  - *Phase 1* built the behavioral safety net `app/insights/tests/test_orchestrator.py`: **51 tests, `orchestrator.py` 100% covered**, real `JobIO(base_dir=tmp_path)`, 12 side-effect points mocked `autospec=True` (7 heavy ML services + 2 audio utils + network FactCheckService + byte-producing PDFService + KeywordService), 5 cheap services real. Two crown-jewel invariants pin the fault contract: `test_exactly_four_hard_fail_gates` (only `MISSING_INPUT_AUDIO` / `AUDIO_NORMALIZATION_TIMEOUT` / `AUDIO_NORMALIZATION_FAILED` / `AUDIO_SILENT_OR_NEAR_SILENT` hard-fail) and `test_late_stage_exception_never_flips_status` (every other stage is fail-soft). Stage order locked via the insertion-ordered `timings_ms` keys.
  - *Phase 2* decomposed `run()` (~530 LOC) into a **75-line stage sequence** of `_run_<stage>` methods. Shared state is one mutable `_PipelineState` dataclass (23 fields); the four early-return gates raise a private `_HardFail` that `run()` catches once. The harness ran green 51/51 after every single extraction — refactor changed shape, not behavior. `orchestrator.py` is 100% covered; **the harness is now a regression net for future edits.**
  - Extending this pattern to new stages: add a `_run_<stage>` method + a `_PipelineState` field, call it from `run()`, and add a stage-class in the harness. Making the top-level ML imports lazy (so the harness rejoins the light CI job) is the remaining follow-up.

- ✅ **E3 — O(n²) hot paths in `alignment_service`. DONE**, in two passes.
  - *N3a* (`5c43f45`) built the characterization net first — `app/insights/tests/test_alignment_service.py`, **90 tests**. The file had zero tests of its own before that; its 78% coverage was incidental, from `test_orchestrator.py` driving it through the pipeline.
  - *N3b* (`1f79131`) bisect-bounded both scans. `_align_words_to_diarization` went 0.72 s → 0.040 s.
  - *Completion pass* fixed the half that was still quadratic. **N3b bounded `hi` but not `lo`** — `candidates = asr[:hi]` restarted at index 0 on every window, so window *i* rescanned segments 0..*i*: 600·601/2 = 180,300 `_overlap` calls, a constant-factor halving of 360k rather than the O(log A + k) the docstring claimed. The fix adds a lower bound.

  **The trap, if you ever touch this again:** the lower bound cannot bisect raw segment *ends*, because ends are **not sorted** — one long early segment finishes after several later ones. Bisecting raw starts or raw ends silently skips that long segment and picks the wrong winner. The bound must use a **running maximum of ends** (`max_ends`), which is non-decreasing by construction and therefore bisectable. `test_alignment_service.py` covers this shape; the differential fixture named `long_early_segment` exists specifically to catch it.

  **Final numbers** on the 60-minute fixture (600 ASR segments / 6000 words / 600 diarization turns):

  | | original | after N3b | after completion |
  |---|---|---|---|
  | `align()` wall-clock, mean | ~1150 ms | ~441 ms | **~42 ms** |
  | `_best_asr_for_window` cumulative | ~1.19 s | 0.778 s | **0.005 s** |
  | `_align_words_to_diarization` cumulative | ~0.72 s | 0.040 s | **0.034 s** |
  | total function calls | — | 774,085 | **57,085** |

  ~27× end-to-end vs the original. Output verified **byte-identical** (sha256 over canonical JSON, 8 fixture shapes) — use sha256, never `hash()`, which is `PYTHONHASHSEED`-randomized and will report a false difference between runs.

### Other candidates

- Docker: harden the existing Dockerfile, add docker-compose, document local-run flow.
- CHANGELOG: start a `CHANGELOG.md` with the Sprint 1–4 history backfilled.
- New engine: e.g., a Trust Engine, a Persona Engine, or a Conversation Quality Engine — would follow the same pattern as `escalation_engine.py` and `inconsistency_engine.py`.

## Style for AI assistants

- Be terse. The user reads diffs — don't summarize what just changed.
- Prefer plans before edits when the task is non-trivial. Use plan mode (`Shift+Tab`) for risky refactors.
- Use the `/security-review` slash command before any push that touches auth, secrets, or external API calls.
- Always read this file at the start of a new session.
