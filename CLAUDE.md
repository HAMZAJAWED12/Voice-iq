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
| CI | GitHub Actions workflow running insight tests on every push/PR | ✅ Done |

**Currently open:** Choose next improvement — Docker hardening, pre-commit hooks, CHANGELOG, security cleanup, or a new engine.

## Engineering standards (STRICT)

These are non-negotiable. Violations have cost time before — uphold them.

1. **Clean architecture.** Every feature is its own engine + models + tests. Never mix logic across engines. Layout: `core/<feature>_engine.py`, `models/<feature>_models.py`, `tests/test_<feature>.py`.
2. **Production safety.** Handle empty sessions, missing sentiment/emotion, null values. Use safe defaults. No crashes from upstream gaps.
3. **Pydantic everywhere.** Strict, validated types. Don't break the existing schema without explicit reason.
4. **Explainability.** Every output must include `reason` and `evidence`. No black-box decisions.
5. **Score discipline.** All scores `0.0 ≤ score ≤ 1.0`. Use the `_clamp()` helper everywhere.
6. **Timeline integrity.** Apply thresholds carefully. Don't introduce noisy markers. Avoid over-triggering.
7. **Code quality.** No redundant calculations, no duplicated logic, no unused imports. Keep functions focused.

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

## CI pipeline notes

`.github/workflows/test.yml` deliberately installs only `requirements-insight.txt` (FastAPI, Pydantic, SQLAlchemy, pytest, httpx) — not `requirements.txt`. The heavy ML deps (torch, whisper, pyannote, transformers) would push CI runtime from ~30 seconds to 8–12 minutes and burn free Actions minutes. Insight tests don't need them. If you ever add tests that require the heavy stack, create a *separate* workflow file — don't bolt it onto this one.

## Known issues / tech debt

These need attention but are not blocking new work:

1. **Exposed Hugging Face token in git history.** A `voiceiq-AI(2).zip` containing the token was previously committed and pushed. The token has been rotated. The zip has been deleted from the working tree. **It is still in git history** — needs `git filter-repo --path "voiceiq-AI.zip" --path "voiceiq-AI(2).zip" --invert-paths --force` followed by a force push to fully purge.
2. **`__pycache__/*.pyc` files tracked in git.** They were committed before `.gitignore` existed. Untrack with `git rm -r --cached app/**/__pycache__` and commit. They'll then be permanently ignored.
3. **`run_eval_dev.LOCAL.py` exists alongside `run_eval_dev.py`.** Renamed during a merge conflict. Decide whether to merge or delete.

## Working with this repo

- **OS:** Windows. PowerShell is the default shell. Paths use backslashes.
- **OneDrive:** The repo lives inside a synced OneDrive folder. This causes occasional `index.lock` collisions during git operations — pause OneDrive sync if git starts fighting itself.
- **Python:** Use the project `.venv` for local work. CI uses Python 3.11.
- **Git editor:** Set to `notepad` to avoid vim swap-file disasters: `git config --global core.editor notepad`.
- **Never** run `git add .` without checking `git status` first — it has previously staged the entire `.venv` (10,000+ files).

## Next-task candidates (pick one when ready)

- Docker: harden the existing Dockerfile, add docker-compose, document local-run flow.
- Pre-commit hooks: ruff + black + a `git secrets` style scanner to prevent token leaks.
- Security cleanup: purge `voiceiq-AI*.zip` from git history with `git filter-repo`, force-push.
- CHANGELOG: start a `CHANGELOG.md` with the Sprint 1–4 history backfilled.
- New engine: e.g., a Trust Engine, a Persona Engine, or a Conversation Quality Engine — would follow the same pattern as `escalation_engine.py` and `inconsistency_engine.py`.

## Style for AI assistants

- Be terse. The user reads diffs — don't summarize what just changed.
- Prefer plans before edits when the task is non-trivial. Use plan mode (`Shift+Tab`) for risky refactors.
- Use the `/security-review` slash command before any push that touches auth, secrets, or external API calls.
- Always read this file at the start of a new session.
