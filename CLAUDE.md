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

**Currently open:** Tier 3 Wave E remainder (E3 alignment O(n²), deferred) + Agent Brain Phase 2 (NLP/model extraction; see the handoff doc §13). Wave E's E1/E1.b/E2/E4/E5 are all done.

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

### Test counts differ by context — this is expected, not a bug

| Where | Command | Count |
|---|---|---|
| Local | `pytest app/insights/tests/ app/agent_brain/tests/` | **503** |
| CI `test` job (light, 3.10 + 3.11) | same, `--ignore` the orchestrator harness | **452** |
| CI `orchestrator-harness` job (heavy) | `pytest app/insights/tests/test_orchestrator.py` | **51** |

452 + 51 = 503. The split exists because `test_orchestrator.py` imports `app.pipeline.orchestrator`, which imports the **full ML stack at module top level** (whisper, torch, transformers, librosa, soundfile — none of them lazy). It therefore cannot run on `requirements-insight.txt`, and gets its own job per the "heavy deps get their own job" rule below. Making those orchestrator imports lazy (a Phase 2 candidate) would let the harness rejoin the light job and retire the heavy one.

**Harness services must be mocked, not run real, when they touch model artifacts.** `KeywordService` looks pure-python but calls `spacy.load("en_core_web_sm")` and builds a `SentenceTransformer`. The spaCy model is a separate `python -m spacy download` artifact declared in no requirements file — present on a dev machine, absent in CI. That divergence passed locally and failed CI with `KEYWORDS_FAILED`. Rule: a service is **heavy** if it imports an ML lib **or** loads a model artifact; heavy ⇒ mocked. Never fix this class of failure by installing the model in CI — a safety net must not depend on model artifacts or a network fetch.

## CI pipeline notes

`.github/workflows/test.yml` deliberately installs only `requirements-insight.txt` (FastAPI, Pydantic, SQLAlchemy, pytest, httpx) — not `requirements.txt`. The heavy ML deps (torch, whisper, pyannote, transformers) would push CI runtime from ~30 seconds to 8–12 minutes and burn free Actions minutes. Insight tests don't need them. If you ever add tests that require the heavy stack, create a *separate* workflow file or job — don't bolt it onto this one. The `orchestrator-harness` job is exactly that exception.

**Diagnosing a CI failure without log access.** GitHub now requires sign-in to read Actions logs even on public repos, and `GET /actions/jobs/{id}/logs` returns `403 Must have admin rights`. Annotations, however, *are* readable unauthenticated. The `orchestrator-harness` job therefore pipes pytest output to a file and, on failure, emits the tail as a `::error::` annotation. Read it with:

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
4. **E3 profile finding — `alignment_service` O(n²) is below the optimization bar (for now).** Profiled `AlignmentService.align()` on a realistic 60-minute-call fixture (600 ASR segments / ~6000 words / 600 diarization turns): mean ~1.15 s. Top cumulative:
   - `_best_asr_for_window` ~1.19 s — O(M·A): each merged segment scans every ASR segment (600×600 → 360k `_overlap` calls).
   - `_align_words_to_diarization` listcomp (line ~218) ~0.72 s — O(D·W): each diarization window scans every word.

   Both are genuine quadratics, but ~1.1 s is **<5% of end-to-end pipeline wall-clock** (whisper ASR + pyannote diarization on the same audio run tens of seconds to minutes), so optimizing now fails the cost/benefit bar. **Deferred, not dropped.** When it matters (much longer/denser audio): both inputs are already time-sorted, so replace the full scans with a two-pointer sweep (`_align_words_to_diarization`) and a bisect-bounded window (`_best_asr_for_window`) → O(D+W) / O(M·log A). Reproduce with the fixture above.

## Working with this repo

- **OS:** Windows. PowerShell is the default shell. Paths use backslashes.
- **OneDrive:** The repo lives inside a synced OneDrive folder. This causes occasional `index.lock` collisions during git operations — pause OneDrive sync if git starts fighting itself.
- **Python:** Use the project `.venv` for local work. CI tests on a Python 3.10 + 3.11 matrix.
- **Interpreter trap:** a bare `python`/`pytest` on PATH may resolve to a different interpreter (e.g. `D:\Downloads\python`) that is **missing the project deps** (`pydantic_settings`, etc.) — collection then dies with confusing `ModuleNotFoundError`s. Always run via the venv: `.venv\Scripts\python.exe -m pytest ...` / `.venv\Scripts\python.exe -m mypy ...`. `where python` surfaces the trap.
- **Git editor:** Set to `notepad` to avoid vim swap-file disasters: `git config --global core.editor notepad`.
- **Never** run `git add .` without checking `git status` first — it has previously staged the entire `.venv` (10,000+ files).

## Next-task candidates (pick one when ready)

### Tier 3 Wave E (structural hardening — deferred from the Tier 3 pass)

- ✅ **Consolidate `_clamp()` (E1 / E1.b).** Done — single `core/_math.py:clamp`; `scoring_engine`, `signal_aggregation`, `inconsistency_engine`, and `factcheck/scorer` all repointed.
- ✅ **MIME / magic-byte upload check (E4).** Done — `app/utils/audio_sniff.py` rejects non-audio uploads with 415; extension check kept as first gate.
- ✅ **E2 — orchestrator decomposition COMPLETE (Phase 1 + Phase 2).**
  - *Phase 1* built the behavioral safety net `app/insights/tests/test_orchestrator.py`: **51 tests, `orchestrator.py` 100% covered**, real `JobIO(base_dir=tmp_path)`, 12 side-effect points mocked `autospec=True` (7 heavy ML services + 2 audio utils + network FactCheckService + byte-producing PDFService + KeywordService), 5 cheap services real. Two crown-jewel invariants pin the fault contract: `test_exactly_four_hard_fail_gates` (only `MISSING_INPUT_AUDIO` / `AUDIO_NORMALIZATION_TIMEOUT` / `AUDIO_NORMALIZATION_FAILED` / `AUDIO_SILENT_OR_NEAR_SILENT` hard-fail) and `test_late_stage_exception_never_flips_status` (every other stage is fail-soft). Stage order locked via the insertion-ordered `timings_ms` keys.
  - *Phase 2* decomposed `run()` (~530 LOC) into a **75-line stage sequence** of `_run_<stage>` methods. Shared state is one mutable `_PipelineState` dataclass (23 fields); the four early-return gates raise a private `_HardFail` that `run()` catches once. The harness ran green 51/51 after every single extraction — refactor changed shape, not behavior. `orchestrator.py` is 100% covered; **the harness is now a regression net for future edits.**
  - Extending this pattern to new stages: add a `_run_<stage>` method + a `_PipelineState` field, call it from `run()`, and add a stage-class in the harness. Making the top-level ML imports lazy (so the harness rejoins the light CI job) is the remaining follow-up.

- **E3 — O(n²) hot paths (profiled, deferred).** `alignment_service` has genuine quadratics but they sit below the optimization bar at current scale — see "Known issues / tech debt" #4 for the profile data, fixture, and the two-pointer/bisect recipe to apply when audio grows.

### Other candidates

- Docker: harden the existing Dockerfile, add docker-compose, document local-run flow.
- CHANGELOG: start a `CHANGELOG.md` with the Sprint 1–4 history backfilled.
- New engine: e.g., a Trust Engine, a Persona Engine, or a Conversation Quality Engine — would follow the same pattern as `escalation_engine.py` and `inconsistency_engine.py`.

## Style for AI assistants

- Be terse. The user reads diffs — don't summarize what just changed.
- Prefer plans before edits when the task is non-trivial. Use plan mode (`Shift+Tab`) for risky refactors.
- Use the `/security-review` slash command before any push that touches auth, secrets, or external API calls.
- Always read this file at the start of a new session.
