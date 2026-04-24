# 07 — Developer Setup

How to clone, install, run, and exercise the Insight Service locally. Targets a developer who has not seen this repository before.

---

## Prerequisites

- Python 3.10 or 3.11 (the Dockerfile uses 3.10-slim).
- `pip` ≥ 23.0 (for PEP 668 / `--break-system-packages` semantics on system Pythons).
- Optional: Docker 24+ for the containerised path.
- Optional: Postman or any HTTP client for hitting the API interactively.

The Insight Service does not require GPU, CUDA, `torch`, `whisper`, `pyannote`, `librosa`, or `soundfile`. The full pipeline (`app.main`) does require those, but everything in `app/insights/*` is pure-Python plus FastAPI + SQLAlchemy + Pydantic.

---

## Clone and create a virtual environment

```bash
git clone <repo-url> voiceiq-AI
cd voiceiq-AI

python -m venv .venv
source .venv/bin/activate              # on Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
```

---

## Install dependencies

The repository uses a single `requirements.txt`. The Insight-only subset is small; the full pipeline pulls heavier ML libraries.

For the **Insight Service only** (matches the Docker image):

```bash
pip install fastapi==0.115.6 uvicorn==0.32.1 pydantic==2.12.4 \
            "pydantic-settings>=2.5.0" "SQLAlchemy>=2.0.30" python-multipart
```

For the **full repository** (audio pipeline + insights):

```bash
pip install -r requirements.txt
```

The full install can take 10+ minutes on the first run because of `torch`, `whisper`, and `pyannote.audio`. If you only need the Insight Service, the focused install above is sufficient.

---

## Configuration

The service reads `.env` at the repository root. Copy the example:

```bash
cp .env.example .env
```

Then edit anything you need. The defaults work out of the box for local development:

```
VOICEIQ_ENVIRONMENT=dev
VOICEIQ_DATABASE_URL=sqlite:///./data/insights.db
VOICEIQ_DATABASE_AUTO_CREATE=true
VOICEIQ_THRESHOLD_PROFILE=default
```

The `./data/` directory is created automatically when the SQLite engine first initialises.

---

## Running locally

There are two FastAPI apps. Pick one based on what you need:

### Insight Service only (no audio pipeline imports)

```bash
uvicorn app.insight_main:app --reload --host 0.0.0.0 --port 8000
```

Use this for fast iteration on the rules layer. Cold start is under a second because no ML libraries are imported.

### Full pipeline

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Use this when you need `/v1/process-audio` alongside the insight routes. Cold start is several seconds because `torch` and `whisper` load eagerly.

Both apps boot the SQLite schema on startup via the `lifespan` hook. The first request will land on a fully-initialised database.

---

## Verifying the service is up

```bash
curl http://localhost:8000/healthz
# → {"status": "ok"}

curl http://localhost:8000/version
# → {"service": "voiceiq-insight-service", "version": "1.0.0", "environment": "dev"}
```

If both succeed, the server is healthy.

---

## Hitting the API

### Swagger UI

Open `http://localhost:8000/docs` in a browser. The `SessionInput` request body in the `POST /v1/insights/generate` route ships with a working example (`sample-call-2026-04-22`). Click "Try it out" → "Execute" to send it.

### From the command line

The repository ships a sample payload at `samples/request.json`:

```bash
curl -X POST http://localhost:8000/v1/insights/generate \
  -H "Content-Type: application/json" \
  -d @samples/request.json | jq
```

Then read it back:

```bash
curl http://localhost:8000/v1/insights/sample-call-2026-04-22 | jq
curl http://localhost:8000/v1/insights/sample-call-2026-04-22/summary | jq
curl http://localhost:8000/v1/insights/sample-call-2026-04-22/timeline | jq
curl http://localhost:8000/v1/insights/sample-call-2026-04-22/speakers | jq
curl http://localhost:8000/v1/insights/ | jq
```

### From Postman

Import the OpenAPI schema from `http://localhost:8000/openapi.json` directly into Postman. Postman will generate one request per route, pre-populated with the example body where one is defined. The `samples/README.md` file documents this flow with screenshots and step-by-step instructions for non-CLI users.

---

## Running the test suite

```bash
pytest -v
```

The test suite is fully self-contained: each test that needs the database creates its own SQLite file under a `tmp_path` fixture and overrides `get_insight_repository` accordingly. There is no global test database to manage and no cleanup is required between runs.

The full suite runs in well under a minute on a developer laptop. Coverage is documented in [`09-testing.md`](09-testing.md).

---

## Project layout pointer

For a senior developer joining the project, the natural reading order is:

1. `app/insights/api/insight_routes.py` — see what the public surface is.
2. `app/insights/service.py` — see the orchestration.
3. `app/insights/core/rule_engine.py` — see the rules orchestration order.
4. Then drill into individual `core/*_engine.py` files as needed.
5. `app/insights/tests/test_insight_api.py` — confirm what behaviours are pinned by tests.

Avoid starting in the model files — they are leaf nodes and do not show how the system flows.

---

## IDE setup notes

The codebase relies on Pydantic v2 generic types and SQLAlchemy 2.0 typed mapping syntax. In VS Code, install the official Python extension and let Pylance handle type inference. PyCharm 2024.1+ has working support for both libraries' typing annotations out of the box.

There is no project-specific lint configuration. Black + isort with default settings works fine.

---

## Common first-run issues

- **`sqlite3.OperationalError: unable to open database file`** — the `data/` directory was not created. Either ensure the working directory is the repo root when starting the server, or set `VOICEIQ_DATABASE_URL` to an absolute path.
- **`pydantic_core._pydantic_core.ValidationError: 1 validation error for SessionInput`** — the request body is malformed. Check the FastAPI 422 response body — the `loc` field points at the offending key.
- **`ImportError: cannot import name 'init_db'`** — you are running an older branch. Pull the latest; `init_db` was added in the persistence sprint.
