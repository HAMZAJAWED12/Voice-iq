Last verified: 2026-10-16, 793e02e
# System: Runtime View

This page covers how VoiceIQ runs: the two entrypoints, the Docker image, configuration, where data is stored, which outside services it calls, logging and CI. It does not cover:

- how the components fit together: see [ARCHITECTURE.md](ARCHITECTURE.md)
- what each endpoint does: see [FEATURES.md](FEATURES.md)
- every point where data leaves the host: see [DATA-FLOW.md](DATA-FLOW.md)
- security controls: see [SECURITY.md](SECURITY.md)

[08-deployment.md](08-deployment.md) and [06-persistence-and-config.md](06-persistence-and-config.md) are the historical Sprint 1–4 views. Where they disagree with this page, this page is the one checked against the code at the commit above.

---

## 1. Two entrypoints

The repository has two FastAPI applications. They share the same settings, database and router code. The difference is whether the audio pipeline is mounted.

| | `app.main:app` (full pipeline) | `app.insight_main:app` (light) |
|---|---|---|
| `POST /v1/process-audio` (audio upload, ASR, diarization, NLP, PDF) | Mounted (`app/main.py:58`) | **Not mounted** |
| `/v1/insights/*` and `/v1/fact-check` | Mounted (`app/main.py:59`) | Mounted (`app/insight_main.py:70`) |
| `POST /internal/v1/agent-brain/recommendations/generate` | Mounted (`app/main.py:63`) | Mounted (`app/insight_main.py:74`) |
| `GET /healthz`, `GET /version` (no API key needed) | `app/main.py:66-77` | `app/insight_main.py:77-88` |
| `/docs`, `/redoc`, `/openapi.json` | Served unless `VOICEIQ_ENVIRONMENT=production` (`app/main.py:41-46`) | Same rule (`app/insight_main.py:52-57`) |
| On startup | Creates missing tables, then sweeps expired job folders (`app/main.py:28-36`) | Same (`app/insight_main.py:41-47`) |
| Dependencies | `requirements.txt` (torch, Whisper, pyannote, Transformers) plus `ffmpeg` on PATH (`README.md:66-69`) | `requirements-insight.txt` only |
| `.env` loading | `load_dotenv()` is imported and called unconditionally (`app/main.py:11, :22`) | `load_dotenv()` is used only if python-dotenv is installed (`app/insight_main.py:20-36`) |

There is no container image for `app.main`. The Dockerfile header calls the full-pipeline image a deferred task (`Dockerfile:8-11`).

Sprint 7 parts that would add runtime pieces (for example `/v2` routes or a job runner) are planned, not built. No `/v2` route exists in `app/`. Live status: [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).

### Ports

| How it is started | Entrypoint | Port | Source |
|---|---|---|---|
| Docker image / `docker compose` | `app.insight_main` | 8888 (`EXPOSE`, `CMD`, healthcheck, compose `8888:8888`) | `Dockerfile:83-89`, `docker-compose.yml:24-25` |
| README light quick start | `app.insight_main` | 8888 | `README.md:59` |
| README full-pipeline quick start | `app.main` | 8000 | `README.md:69-71` |

The module docstring in `app/insight_main.py:11-15` still says port 8000, including for the Docker run. The Dockerfile actually uses 8888.

---

## 2. Docker image and compose

### Image (`Dockerfile`)

| Item | Value | Lines |
|---|---|---|
| Entrypoint | `app.insight_main:app` only | `:89` |
| Base | `python:3.11-slim`, set by build arg `PYTHON_VERSION` (default `3.11`) | `:24, :29, :54` |
| Build | Two stages. `builder` installs `build-essential` and builds wheels. `runtime` installs from those wheels with `--no-index` | `:29-49, :54-74` |
| Python packages | `requirements-insight.txt` plus `uvicorn[standard]==0.32.1` and `python-dotenv>=1.0.0` | `:45-49, :69-74` |
| User | Non-root system user and group `app`, home `/app` | `:63, :82` |
| Environment | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PIP_NO_CACHE_DIR=1`, `PIP_DISABLE_PIP_VERSION_CHECK=1`, `VOICEIQ_ENVIRONMENT=production`, `VOICEIQ_DATABASE_URL=sqlite:////app/data/insights.db` | `:56-61` |
| Contents | `requirements-insight.txt` (for the install step), `app/` and `.env.example`. `.dockerignore` keeps out `.env`, `data/`, tests, `DOCS/`, media and PDFs | `:69, :76-77`; `.dockerignore` |
| Volume | `/app/data`, owned by `app` | `:79-80` |
| Healthcheck | Python `urllib` GET of `http://127.0.0.1:8888/healthz`, expects 200. Runs every 30 s, timeout 5 s, start period 10 s, 3 retries | `:85-87` |
| Command | `uvicorn app.insight_main:app --host 0.0.0.0 --port 8888` | `:89` |
| Workers | One process. There is no `--workers` flag, and the code, Dockerfile, compose file and requirements set no gunicorn or `WEB_CONCURRENCY` | `:89` |

### Compose (`docker-compose.yml`)

One service, `voiceiq-app`:

- Builds from `.` with `PYTHON_VERSION: "3.11"` and tags the image `voiceiq-insight:dev` (`:17-23`).
- Maps port `8888:8888` (`:24-25`).
- Reads `env_file: .env`, which must exist. Start it from `.env.example` (`:10-11, :26-27`).
- Mounts the named volume `voiceiq-data` at `/app/data` (`:28-29, :42-43`).
- Sets `restart: unless-stopped` and uses the same healthcheck as the image (`:30-40`).
- `docker compose down -v` also deletes the volume, and the SQLite file with it (`:8`).

### Things to know when running the container

- **`.env` overrides the image defaults.** `.env.example` sets `VOICEIQ_ENVIRONMENT=development` (`.env.example:12`). If you copy it to `.env` unchanged, compose runs the container in development mode: `/docs` is visible, and with `VOICEIQ_API_KEYS` empty, API-key checks are off with a logged warning (`app/security/api_key.py:65-85`). For anything beyond local use, set `VOICEIQ_ENVIRONMENT=production` and at least one key.
- **The database still lands in the volume.** The `.env.example` value `sqlite:///./data/insights.db` resolves to `/app/data/insights.db`, because the working directory is `/app` (`Dockerfile:65`).
- **The healthcheck does not test auth.** With `VOICEIQ_ENVIRONMENT=production` and no keys, the protected routes return 503 (`app/security/api_key.py:68-77`). `/healthz` takes no API key, so it still returns 200 and the container still reports healthy. Check that keys are set as a separate step.

---

## 3. Configuration

### How settings are loaded

- **Insight settings** (`app/insights/config/settings.py:28-41`) use the prefix `VOICEIQ_`. They are read from the process environment and from a `.env` file in the working directory. Names are case-insensitive and unknown keys are ignored.
- **Agent Brain settings** (`app/agent_brain/config/settings.py:11-14`) use the prefix `VOICEIQ_AGENT_` and the same `.env` file.
- **Pyannote token.** Two variables have no prefix and are read with `os.getenv` (`app/services/diarization_service.py:90`). `load_dotenv()` copies `.env` into the environment so that this read also sees values in `.env`.
- **Restart to apply changes.** Each settings object is built once per process and cached with `lru_cache` (`settings.py:241-249`; `agent_brain/config/settings.py:31-33`). Some values are also copied into module constants when the module is imported (marked "at import" below).
- **Invalid values stop startup.** Both entrypoints build the insight settings when they are imported (`app/main.py:25`, `app/insight_main.py:38`). An enum value outside its list (`VOICEIQ_ENVIRONMENT`, `VOICEIQ_LOG_LEVEL`, `VOICEIQ_THRESHOLD_PROFILE`) or a number outside its bounds raises a pydantic `ValidationError`, and the process does not start. Checked on 2026-10-16 with an invalid value for each of the three enums, and with `VOICEIQ_API_MAX_UPLOAD_MB=0`, `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB=0` and `VOICEIQ_JOB_RETENTION_HOURS=-1`.
- **Agent Brain values are checked late.** These settings are read only when the Agent Brain stage of `/v1/process-audio` has produced recommendations and is about to dispatch the Java callback (`app/pipeline/orchestrator.py:779, :807-811`). A bad `VOICEIQ_AGENT_*` value therefore shows up at that point, not at startup.

### Environment variables the code reads

This table lists names and defaults only. Templates for all of them are in [`.env.example`](../.env.example).

| Variable | Default | Read in | Notes |
|---|---|---|---|
| `VOICEIQ_SERVICE_NAME` | `VoiceIQ Insight Service` | `app/main.py:47, :75`; `app/insight_main.py:58, :86` | OpenAPI title and `/version` |
| `VOICEIQ_SERVICE_VERSION` | `1.0.0` | `app/main.py:54`; `app/insight_main.py:66` | App version and `/version` |
| `VOICEIQ_ENVIRONMENT` | `development` | `app/main.py:41`; `app/insight_main.py:52`; `app/security/api_key.py:66` | One of `development`, `staging`, `production`, `test`. `production` hides the docs routes, and with no keys set every protected request gets 503. The image sets `production` |
| `VOICEIQ_LOG_LEVEL` | `INFO` | nothing reads it | **Validated but not applied.** The root level is fixed at INFO (`app/utils/logger.py:13`) |
| `VOICEIQ_DATABASE_URL` | `sqlite:///./data/insights.db` | `app/insights/repository/db.py:69`; `db.py:200` (command-line bootstrap, via `os.environ`) | The path is relative to the working directory. The image sets `sqlite:////app/data/insights.db` |
| `VOICEIQ_DATABASE_ECHO` | `false` | `db.py:63` | `true` makes SQLAlchemy log every statement |
| `VOICEIQ_DATABASE_AUTO_CREATE` | `true` | `db.py:120` | `false` means startup creates no tables |
| `VOICEIQ_THRESHOLD_PROFILE` | `default` | nothing reads it | **Validated but not applied.** Accepts `default`, `strict`, `lenient`. `InsightService` uses its own constant `"default"` (`app/insights/service.py:31`) |
| `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB` | `2048` (≥ 1) | `app/insights/api/insight_routes.py:27`; `factcheck_routes.py:40`; `app/agent_brain/api/agent_routes.py:22` | JSON body cap. Read at import |
| `VOICEIQ_API_MAX_UPLOAD_MB` | `200` (≥ 1) | `app/routes/process_audio.py:27` | Audio upload cap. Read at import. Full pipeline only |
| `VOICEIQ_FFMPEG_TIMEOUT_SEC` | `120.0` (1–600) | `app/utils/audio_utils.py:60` | Full pipeline only |
| `VOICEIQ_JOB_RETENTION_HOURS` | `24.0` (≥ 0) | `app/main.py:35`; `app/insight_main.py:46`; `process_audio.py:33` (at import) | `0` turns off the job-folder purge |
| `VOICEIQ_RATE_LIMIT_ENABLED` | `true` | `app/security/rate_limit.py:57` | Master switch |
| `VOICEIQ_RATE_LIMIT_PROCESS_AUDIO` | `30/minute` | `rate_limit.py:79` | |
| `VOICEIQ_RATE_LIMIT_FACTCHECK` | `60/minute` | `rate_limit.py:74` | |
| `VOICEIQ_RATE_LIMIT_AGENT_BRAIN` | `60/minute` | `rate_limit.py:84` | |
| `VOICEIQ_API_KEYS` | empty | `app/security/api_key.py:65` | Comma-separated list (`settings.py:179-190`). Empty means auth is off outside production and every protected request gets 503 in production |
| `VOICEIQ_FACTCHECK_AUTO_ENRICH` | `false` | `app/utils/factcheck_gate.py:38` | A per-request `?fact_check=` value takes priority |
| `VOICEIQ_OPENWEATHER_API_KEY` | empty | `app/insights/core/factcheck/factcheck_engine.py:81` | Empty turns off the weather client |
| `VOICEIQ_ALPHAVANTAGE_API_KEY` | empty | `factcheck_engine.py:80` | Empty turns off the stock-price client |
| `VOICEIQ_FACTCHECK_HTTP_TIMEOUT_SEC` | `5.0` (0.5–30) | `factcheck_engine.py:75` | Applies to all fact-check source clients |
| `VOICEIQ_AGENT_BRAIN_AUTO_RUN` | `false` | `app/pipeline/orchestrator.py:741` | An insight setting despite the name. Adds `recommendations` to the `/v1/process-audio` response |
| `VOICEIQ_AGENT_CALLBACK_URL` | empty | `app/agent_brain/integrations/java_callback_client.py:71, :98` | The callback is off unless both URL and secret are set |
| `VOICEIQ_AGENT_CALLBACK_SECRET` | empty | `java_callback_client.py:78` | HMAC signing key |
| `VOICEIQ_AGENT_CALLBACK_TIMEOUT_SEC` | `5.0` (1–60) | `java_callback_client.py:97` | |
| `VOICEIQ_AGENT_MIN_CONFIDENCE` | `0.50` (0–1) | `app/pipeline/orchestrator.py:821` | Filters the callback only. The API response keeps every recommendation |
| `PYANNOTE_AUTH_TOKEN` | unset | `app/services/diarization_service.py:90` | No prefix. Full pipeline only |
| `HUGGINGFACE_TOKEN` | unset | `diarization_service.py:90` | Used when `PYANNOTE_AUTH_TOKEN` is unset. Without either token, diarization falls back to a single-speaker mock (`diarization_service.py:91-93, :133-146`) |

The repository does not set or read `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` or any model cache path variable. [DATA-FLOW.md](DATA-FLOW.md) §3 explains what that means for model downloads.

---

## 4. Data stores

### SQLite database

- **Default file:** `./data/insights.db`, relative to the working directory. In the image it is `/app/data/insights.db`. The parent folder is created if it is missing (`app/insights/repository/db.py:36-55`).
- **Schema management:** there is no migrations tool. Startup calls `Base.metadata.create_all()` (`db.py:110-130`), which creates missing tables and never alters an existing one. `app/insights/tests/test_db_schema_evolution.py` pins this behaviour.
- **Retention:** no sweep touches the database. Rows stay until they are deleted. The only delete route is `DELETE /v1/insights/{session_id}` (`app/insights/api/insight_routes.py:199`), which removes one insight record; `fact_check_results` rows have no delete route.

| Table | Model | Holds |
|---|---|---|
| `insight_records` | `app/insights/repository/orm_models.py:25-33` | One row per `session_id`: status, the full insight response as JSON, timestamps |
| `fact_check_results` | `app/insights/repository/factcheck_orm_models.py:25-33` | One row per checked claim: conversation and speaker ids, the claim, the verdict and values, the evidence as JSON, timestamps |

### Job folders (full pipeline only)

- Each `/v1/process-audio` request gets its own folder, `data/jobs/<job_id>/`, relative to the working directory (`app/utils/job_io.py:36-50`). It contains `input/` (the uploaded audio), `artifacts/` (audio, ASR, diarization, alignment, NLP, insights, report and, when enabled, Agent Brain output; see the paths in `app/pipeline/orchestrator.py`), `logs/` and `meta.json`.
- **Retention:** a folder is deleted once it is older than `VOICEIQ_JOB_RETENTION_HOURS`. Age is taken from the time `meta.json` was last modified, or from the folder itself if `meta.json` is missing (`job_io.py:127-163`).
- **When the sweep runs:** at startup in both entrypoints, and at most once an hour during `/v1/process-audio` requests (`app/routes/process_audio.py:30-48`).
- **Failures:** the sweep never raises. Removal errors are ignored (`shutil.rmtree(..., ignore_errors=True)`), and a folder whose age cannot be read is logged and skipped (`job_io.py:150-160`). The request-time call is also wrapped in a logged `try/except` (`process_audio.py:45-48`).

### Model caches (full pipeline only)

The light image loads no models.

| Model | Where it is cached |
|---|---|
| Whisper `base` (`app/routes/process_audio.py:205`) | Whisper's default download folder; the code passes no `download_root` (`app/services/asr_service.py:37`). [DATA-FLOW.md](DATA-FLOW.md) §3 gives the path as `~/.cache/whisper`. Each process also keeps loaded models in memory (`asr_service.py:7`) |
| Hugging Face models: `pyannote/speaker-diarization`, `cardiffnlp/twitter-roberta-base-sentiment-latest`, `facebook/bart-large-mnli`, `sshleifer/distilbart-cnn-12-6`, `sentence-transformers/all-MiniLM-L6-v2` | The Hugging Face libraries' default cache. The repository sets no cache path (`diarization_service.py:47`, `sentiment_service.py:31`, `topic_service.py:34-36`, `summary_service.py:27-29`, `keyword_service.py:26`) |
| spaCy `en_core_web_sm` | Loaded with `spacy.load`, which does not download it (`app/services/keyword_service.py:20`). It must be installed separately as a Python package; no requirements file declares it |

---

## 5. External services

There is one line per service below. [DATA-FLOW.md](DATA-FLOW.md) has the full detail: what is sent, when, and the controls on it.

| Service | Called from | Summary |
|---|---|---|
| Model hosts (Whisper's public download host, Hugging Face) | `app.main` only | Weights are fetched when a model is first loaded and is not already cached. No call content is sent. Cache and network behaviour: [DATA-FLOW.md](DATA-FLOW.md) §3 |
| Fact-check providers (exchangerate.host, CoinGecko, OpenWeather, Alpha Vantage, Wikipedia REST) | Both entrypoints | Called by `POST /v1/fact-check`, and by `/v1/process-audio` only when fact-checking is requested (`?fact_check=true`, or `VOICEIQ_FACTCHECK_AUTO_ENRICH=true` when the query value is omitted; `app/utils/factcheck_gate.py:36-38`). Only short claim subjects are sent. OpenWeather and Alpha Vantage need keys. The currency provider now needs an access key, and the service has no setting for one, so currency claims end as `SOURCE_UNAVAILABLE` (`dc03a44`) |
| Java Action Layer callback | `app.main` only (the Agent Brain stage of the pipeline) | Sent only when `VOICEIQ_AGENT_BRAIN_AUTO_RUN=true`, both URL and secret are set, and at least one recommendation meets `VOICEIQ_AGENT_MIN_CONFIDENCE` (`app/pipeline/orchestrator.py:811-824`). It is an HMAC-signed POST, sent outside the request path. The contract is in [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md) |

---

## 6. Logging

| Behaviour | Where | Since |
|---|---|---|
| The root logger is set to INFO with the format `%(asctime)s - %(levelname)s - %(message)s`, and the application logger is named `voiceiq` | `app/utils/logger.py:12-21` | — |
| Setup runs on the first import of `app.utils.logger`. `app.main` also calls it explicitly. `app.insight_main` reaches it through its imports, for example `app/utils/job_io.py:11` | `logger.py:18`; `app/main.py:23` | — |
| The `httpx` and `httpcore` loggers are pinned to WARNING whatever the root level is, so outbound request URLs are not logged | `logger.py:4-15` | `c1b35d9` |
| A failed Java callback logs only the exception class and HTTP status, never the exception text or the payload | `app/pipeline/orchestrator.py:836-846` | `cedfe96` |

- `VOICEIQ_LOG_LEVEL` changes none of the behaviour above.
- `VOICEIQ_DATABASE_ECHO=true` adds SQLAlchemy statement logging.
- In the container, `PYTHONUNBUFFERED=1` makes logs appear without delay. Read them with `docker compose logs -f voiceiq-app`.
- `app/insights/tests/test_logging_hygiene.py` pins the logging rules. It checks that the HTTP loggers stay at WARNING, including after importing the Docker entrypoint (`:169`, `:229`), and that the callback URL never appears in a log record (`:276`).

---

## 7. CI

CI is defined in `.github/workflows/test.yml` and runs on pushes to `main` and on pull requests to `main` (`:3-7`).

| Job | Python | What it runs |
|---|---|---|
| `lint` ("Lint, types, secrets") | 3.11 | Installs `requirements-insight.txt`, ruff 0.7.4 and mypy 1.13.0. Then runs `ruff check .`, `mypy --ignore-missing-imports --check-untyped-defs app/insights/ app/agent_brain/`, and gitleaks 8.21.2 as `gitleaks dir . --no-banner --redact` (`:10-47`) |
| `test` ("Run Insight Service unit tests") | 3.10 and 3.11 (matrix) | Installs `requirements-insight.txt` only, then runs `pytest -v app/insights/tests/ app/agent_brain/tests/` with `PYTHONPATH=.`. On failure, the end of the output is posted as an `::error::` annotation (`:49-93`) |

- **Expected test result:** 1078 passed and 1 skipped on the light stack, measured 2026-10-16. The skipped item is `app/insights/tests/test_model_load_concurrency.py`, a 3-test module that needs the ML libraries ([CLAUDE.md](../CLAUDE.md), "One test lane").
- **Not in CI:** a Docker build, the heavy ML stack, and the legacy top-level `tests/` folder.
- **Tool settings:** ruff and mypy are configured in `pyproject.toml`: line length 120, target `py310`, and the pydantic mypy plugin.
- **Python versions in use:** Docker 3.11, CI 3.10 and 3.11, README "Python 3.10+" (`README.md:66`).
- **Before a commit:** pre-commit hooks run locally: ruff, ruff-format, three hygiene hooks run through `python -m` (`7b25b3f`), and gitleaks (`.pre-commit-config.yaml`). How to use them is in [WORKFLOW.md](WORKFLOW.md).
