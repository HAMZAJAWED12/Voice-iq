Last verified: 2026-10-17, dbae452
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
| `POST /v1/process-audio` (audio upload, ASR, diarization, NLP, PDF) | Mounted (`app/main.py:64`) | **Not mounted** |
| `/v1/insights/*` and `/v1/fact-check` | Mounted (`app/main.py:65`) | Mounted (`app/insight_main.py:76`) |
| `POST /internal/v1/agent-brain/recommendations/generate` | Mounted (`app/main.py:69`) | Mounted (`app/insight_main.py:80`) |
| Request guard middleware (API key and declared size checked before the body is read) | Installed, including the upload cap for `/v1/process-audio` (`app/main.py:72`) | Installed, for the JSON routes only (`app/insight_main.py:83`) |
| `GET /healthz`, `GET /readyz`, `GET /version` (no API key needed) | `app/main.py:75-91`. `/readyz` checks the database and that `ffmpeg` is on PATH | `app/insight_main.py:86-102`. `/readyz` checks the database only |
| `/docs`, `/redoc`, `/openapi.json` | Served unless `VOICEIQ_ENVIRONMENT=production` (`app/main.py:47-52`) | Same rule (`app/insight_main.py:58-63`) |
| On startup (`lifespan`) | Applies the log level (`configure_logging`), creates missing tables, then runs the retention sweep once and starts the hourly one (`app/main.py:32-42`) | Same (`app/insight_main.py:45-53`) |
| Dependencies | `requirements.txt` (torch, Whisper, pyannote, Transformers) plus `ffmpeg` on PATH (`README.md:66-69`) | `requirements-insight.txt` only |
| `.env` loading | `load_dotenv()` is imported and called unconditionally (`app/main.py:11, :26`) | `load_dotenv()` is used only if python-dotenv is installed (`app/insight_main.py:20-40`) |

**Request path.** For POST, PUT and PATCH requests under `/v1/insights`, `/v1/fact-check`, `/internal/v1/agent-brain` and, on `app.main`, `/v1/process-audio`, the middleware runs the API-key check and then the Content-Length check before routing and before the body reaches the app, with the same status and detail as the router dependencies. A refusal carries `Connection: close`; a request that passes has its body bytes counted, and a body longer than its declared length gets 413 (`app/security/request_guard.py:38-101`). The router dependencies then run as before. Design and order: [ARCHITECTURE.md](ARCHITECTURE.md) §3.

**`/v1/process-audio` concurrency.** The pipeline and its fact-check enrichment run in a worker thread. Each worker process (one event loop each) runs at most `VOICEIQ_PROCESS_AUDIO_MAX_RUNNING` pipelines at once (default 1) and lets `VOICEIQ_PROCESS_AUDIO_MAX_WAITING` more uploads wait for a slot (default 2). A further upload gets 503 "Audio processing is at capacity. Retry later." with `Retry-After: VOICEIQ_PROCESS_AUDIO_RETRY_AFTER_SEC` (default 30), checked after the API key and the filename and before the upload is saved. Other requests, `/healthz` included, are served during a run (`app/routes/process_audio.py:34-57, :153-165, :234-236`; `948c49d`; [DECISIONS.md #35](DECISIONS.md)). The limits are per worker process, so a deployment with several workers or replicas multiplies them.

There is no container image for `app.main`. The Dockerfile header calls the full-pipeline image a deferred task (`Dockerfile:8-11`).

Sprint 7 parts that would add runtime pieces (for example `/v2` routes or a job runner) are planned, not built. No `/v2` route exists in `app/`. Live status: [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).

### Ports

| How it is started | Entrypoint | Port | Source |
|---|---|---|---|
| Docker image / `docker compose` | `app.insight_main` | 8888 (`EXPOSE`, `CMD`, healthcheck; compose publishes `127.0.0.1:8888:8888`, loopback only) | `Dockerfile:83-89`, `docker-compose.yml:28-29` |
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
- Publishes port 8888 on the loopback interface only, `127.0.0.1:8888:8888`, so only the same machine can reach it. To serve other hosts, put a TLS-terminating reverse proxy or load balancer in front (`:24-29`; `d7f9075`, `fb56d7d`).
- Reads `env_file: .env`, which must exist. Start it from `.env.example` (`:10-11, :30-31`).
- Mounts the named volume `voiceiq-data` at `/app/data` (`:32-33, :53-54`).
- Sets `restart: unless-stopped` (`:34`).
- Limits the container to 2 CPUs and 1 GB of memory (`deploy.resources.limits`, `:35-41`; `fb56d7d`). This was not checked with `docker compose config`.
- Uses the same `/healthz` healthcheck as the image (`:42-51`). `/readyz` is there for an orchestrator that wants a readiness probe; neither file calls it.
- `docker compose down -v` also deletes the volume, and the SQLite file with it (`:8`).

### Things to know when running the container

- **`.env` overrides the image defaults.** `.env.example` sets `VOICEIQ_ENVIRONMENT=development` (`.env.example:12`). If you copy it to `.env` unchanged, compose runs the container in development mode: `/docs` is visible, and with `VOICEIQ_API_KEYS` empty, API-key checks are off with a logged warning (`app/security/api_key.py:69-81, :113-120`). For anything beyond local use, set `VOICEIQ_ENVIRONMENT=production` and at least one key.
- **The database still lands in the volume.** The `.env.example` value `sqlite:///./data/insights.db` resolves to `/app/data/insights.db`, because the working directory is `/app` (`Dockerfile:65`).
- **The healthcheck does not test auth.** With `VOICEIQ_ENVIRONMENT` set to `production` or `staging` and no keys, the protected routes return 503 (`app/security/api_key.py:36, :70-80`). `/healthz` and `/readyz` take no API key, so they still return 200 and the container still reports healthy. Check that keys are set as a separate step.

---

## 3. Configuration

### How settings are loaded

- **Insight settings** (`app/insights/config/settings.py:28-41`) use the prefix `VOICEIQ_`. They are read from the process environment and from a `.env` file in the working directory. Names are case-insensitive and unknown keys are ignored.
- **Agent Brain settings** (`app/agent_brain/config/settings.py:11-14`) use the prefix `VOICEIQ_AGENT_` and the same `.env` file.
- **Pyannote token.** Two variables have no prefix and are read with `os.getenv` (`app/services/diarization_service.py:90`). `load_dotenv()` copies `.env` into the environment so that this read also sees values in `.env`.
- **Restart to apply changes.** Each settings object is built once per process and cached with `lru_cache` (`settings.py:313-321`; `agent_brain/config/settings.py:31-33`). Some values are also copied into module constants when the module is imported (marked "at import" below).
- **Invalid values stop startup.** Both entrypoints build the insight settings when they are imported (`app/main.py:29`, `app/insight_main.py:42`). An enum value outside its list (`VOICEIQ_ENVIRONMENT`, `VOICEIQ_LOG_LEVEL`, `VOICEIQ_THRESHOLD_PROFILE`) or a number outside its bounds raises a pydantic `ValidationError`, and the process does not start. Checked on 2026-10-16 with an invalid value for each of the three enums, and with `VOICEIQ_API_MAX_UPLOAD_MB=0`, `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB=0` and `VOICEIQ_JOB_RETENTION_HOURS=-1`. The settings added since then declare bounds the same way (`settings.py:113-138, :164-173, :288-305`).
- **Startup logging.** Each app's lifespan calls `configure_logging(settings)`, which sets the root log level from `VOICEIQ_LOG_LEVEL` and logs a warning when `VOICEIQ_THRESHOLD_PROFILE` is set, whatever its value (`app/utils/logger.py:31-42`; `e25a3b2`).
- **Agent Brain values are checked late.** These settings are read only when the Agent Brain stage of `/v1/process-audio` has produced recommendations and is about to dispatch the Java callback (`app/pipeline/orchestrator.py:796, :824-828`). A bad `VOICEIQ_AGENT_*` value therefore shows up at that point, not at startup.

### Environment variables the code reads

This table lists names and defaults only. The template is [`.env.example`](../.env.example).

| Variable | Default | Read in | Notes |
|---|---|---|---|
| `VOICEIQ_SERVICE_NAME` | `VoiceIQ Insight Service` | `app/main.py:53, :89`; `app/insight_main.py:64, :100` | OpenAPI title and `/version` |
| `VOICEIQ_SERVICE_VERSION` | `1.0.0` | `app/main.py:60`; `app/insight_main.py:72` | App version and `/version` |
| `VOICEIQ_ENVIRONMENT` | `development` | `app/main.py:47`; `app/insight_main.py:58`; `app/security/api_key.py:71` | One of `development`, `staging`, `production`, `test`. `production` hides the docs routes. With no keys set, `production` and `staging` give every protected request 503 (`api_key.py:36`). The image sets `production` |
| `VOICEIQ_LOG_LEVEL` | `INFO` | `app/utils/logger.py:36`, called from each app's lifespan (`app/main.py:34`, `app/insight_main.py:47`) | Root log level, applied at startup. The `httpx` and `httpcore` loggers stay at WARNING whatever it is (`e25a3b2`) |
| `VOICEIQ_DATABASE_URL` | `sqlite:///./data/insights.db` | `app/insights/repository/db.py:55, :64`; `db.py:195` (command-line bootstrap, via `os.environ`) | The path is relative to the working directory. The image sets `sqlite:////app/data/insights.db` |
| `VOICEIQ_DATABASE_ECHO` | `false` | `db.py:58` | `true` makes SQLAlchemy log every statement |
| `VOICEIQ_DATABASE_AUTO_CREATE` | `true` | `db.py:115` | `false` means startup creates no tables |
| `VOICEIQ_THRESHOLD_PROFILE` | `default` | `app/utils/logger.py:37-42`, only to warn | **Not implemented.** Accepts `default`, `strict`, `lenient`, but the rule engines always use their default thresholds; `InsightService` uses its own constant `"default"` (`app/insights/service.py:32`). Each app logs a warning at startup when the variable is set, whatever its value (`e25a3b2`) |
| `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB` | `2048` (≥ 1) | `app/insights/api/insight_routes.py:29`; `factcheck_routes.py:40`; `app/agent_brain/api/agent_routes.py:22`; `app/security/request_guard.py:40` | JSON body cap, for the router dependencies and the request guard. Read at import |
| `VOICEIQ_API_MAX_UPLOAD_MB` | `200` (≥ 1) | `app/routes/process_audio.py:30` | Audio upload cap, also passed to the request guard (`app/main.py:72`). Read at import. Full pipeline only |
| `VOICEIQ_PROCESS_AUDIO_MAX_RUNNING` | `1` (≥ 1) | `app/routes/process_audio.py:55, :155` | Pipeline runs one worker process executes at once. Read when a worker's first upload arrives. Full pipeline only (`948c49d`) |
| `VOICEIQ_PROCESS_AUDIO_MAX_WAITING` | `2` (≥ 0) | `process_audio.py:155` | Uploads that may wait for a slot per worker process; beyond running + waiting, the route answers 503. Full pipeline only |
| `VOICEIQ_PROCESS_AUDIO_RETRY_AFTER_SEC` | `30` (≥ 1) | `process_audio.py:159` | `Retry-After` sent with that 503. Full pipeline only |
| `VOICEIQ_MAX_AUDIO_SECONDS` | `7200.0` (≥ 1) | `app/utils/audio_utils.py:108` | Longest audio accepted, two hours by default. Longer audio gets 413 with `AUDIO_TOO_LONG` and is never truncated. Full pipeline only (`6c17c72`) |
| `VOICEIQ_FFMPEG_TIMEOUT_SEC` | `120.0` (1–600) | `app/utils/audio_utils.py:107` | Full pipeline only. Applies to ffprobe and ffmpeg. ffmpeg runs with `-nostdin`, and a partial output file is deleted on a timeout or error (`audio_utils.py:46, :117, :139, :147`) |
| `VOICEIQ_JOB_RETENTION_HOURS` | `24.0` (≥ 0) | `app/utils/retention.py:43` | Read by the retention sweep, which each app's lifespan runs at startup and then hourly. `0` turns off the job-folder purge |
| `VOICEIQ_DB_RETENTION_DAYS` | `30` (≥ 0) | `app/utils/retention.py:47` | Insight records last updated, and fact-check results created, more than this many days ago are deleted by the same sweep. `0` keeps every record (`cbdafb4`) |
| `VOICEIQ_RATE_LIMIT_ENABLED` | `true` | `app/security/rate_limit.py:64` | Master switch. A request is counted under its API key only when keys are configured and the header holds one of them; otherwise under the client IP (`rate_limit.py:45-54`). Each route group has its own counter |
| `VOICEIQ_RATE_LIMIT_PROCESS_AUDIO` | `30/minute` | `rate_limit.py:86` | |
| `VOICEIQ_RATE_LIMIT_FACTCHECK` | `60/minute` | `rate_limit.py:81` | `POST /v1/fact-check` |
| `VOICEIQ_RATE_LIMIT_AGENT_BRAIN` | `60/minute` | `rate_limit.py:91` | |
| `VOICEIQ_RATE_LIMIT_INSIGHT_WRITE` | `60/minute` | `rate_limit.py:96` | The insight routes that store or delete: `POST /generate`, `POST /generate-from-raw`, `DELETE /{session_id}` (`e802fcd`) |
| `VOICEIQ_RATE_LIMIT_INSIGHT_READ` | `120/minute` | `rate_limit.py:101` | The insight routes that read: `GET /{session_id}` and its `/summary`, `/speakers`, `/timeline`, and the list `GET /v1/insights/` |
| `VOICEIQ_API_KEYS` | empty | `app/security/api_key.py:69`; `app/security/rate_limit.py:52` | Comma-separated list (`settings.py:233-244`). Empty means auth is off in `development` and `test`, and every protected request gets 503 in `production` and `staging` |
| `VOICEIQ_FACTCHECK_AUTO_ENRICH` | `false` | `app/utils/factcheck_gate.py:38` | A per-request `?fact_check=` value takes priority |
| `VOICEIQ_OPENWEATHER_API_KEY` | empty | `app/insights/core/factcheck/factcheck_engine.py:88` | Empty turns off the weather client |
| `VOICEIQ_ALPHAVANTAGE_API_KEY` | empty | `factcheck_engine.py:87` | Empty turns off the stock-price client |
| `VOICEIQ_FACTCHECK_HTTP_TIMEOUT_SEC` | `5.0` (0.5–30) | `factcheck_engine.py:82` | Applies to all fact-check source clients |
| `VOICEIQ_FACTCHECK_MAX_CLAIMS` | `25` (1–1000) | `app/insights/api/factcheck_routes.py:68` | Most claims one fact-check run checks, in transcript order; the rest count as skipped (`da92ae4`) |
| `VOICEIQ_FACTCHECK_RUN_DEADLINE_SEC` | `20.0` (above 0, at most 300) | `factcheck_routes.py:69` | No new check starts after this many seconds of a run; a call already running finishes within its own timeout |
| `VOICEIQ_AGENT_BRAIN_AUTO_RUN` | `false` | `app/pipeline/orchestrator.py:758` | An insight setting despite the name. Adds `recommendations` to the `/v1/process-audio` response |
| `VOICEIQ_AGENT_CALLBACK_URL` | empty | `app/agent_brain/integrations/java_callback_client.py:71, :98` | The callback is off unless both URL and secret are set |
| `VOICEIQ_AGENT_CALLBACK_SECRET` | empty | `java_callback_client.py:78` | HMAC signing key |
| `VOICEIQ_AGENT_CALLBACK_TIMEOUT_SEC` | `5.0` (1–60) | `java_callback_client.py:97` | |
| `VOICEIQ_AGENT_MIN_CONFIDENCE` | `0.50` (0–1) | `app/pipeline/orchestrator.py:838` | Filters the callback only. The API response keeps every recommendation |
| `PYANNOTE_AUTH_TOKEN` | unset | `app/services/diarization_service.py:90` | No prefix. Full pipeline only |
| `HUGGINGFACE_TOKEN` | unset | `diarization_service.py:90` | Used when `PYANNOTE_AUTH_TOKEN` is unset. Without either token, diarization falls back to a single-speaker mock (`diarization_service.py:91-93, :133-146`) |

The repository does not set or read `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` or any model cache path variable. [DATA-FLOW.md](DATA-FLOW.md) §3 explains what that means for model downloads.

---

## 4. Data stores

### SQLite database

- **Default file:** `./data/insights.db`, relative to the working directory. In the image it is `/app/data/insights.db`. The parent folder of the file SQLAlchemy opens is created if it is missing; the path is taken from `make_url(...).database`, so relative, absolute and `sqlite+pysqlite` URLs all work (`app/insights/repository/db.py:35-50`, `718d763`).
- **Schema management:** there is no migrations tool. Startup calls `Base.metadata.create_all()` (`db.py:105-125`), which creates missing tables and never alters an existing one. `app/insights/tests/test_db_schema_evolution.py` pins this behaviour.
- **Retention:** records are kept for `VOICEIQ_DB_RETENTION_DAYS`, 30 days by default. The retention sweep deletes insight records last updated, and fact-check results created, before that cutoff (`purge_older_than` in `app/insights/repository/insight_repository.py:154-165` and `factcheck_repository.py:194-205`; `purge_expired_records` in `app/utils/retention.py:28-36`; `cbdafb4`). `0` keeps every record. The sweep runs at startup and then hourly, with the job-folder purge below. The only delete route is `DELETE /v1/insights/{session_id}` (`app/insights/api/insight_routes.py:217`), which removes one insight record; `fact_check_results` rows have no delete route.

| Table | Model | Holds |
|---|---|---|
| `insight_records` | `app/insights/repository/orm_models.py:25-33` | One row per `session_id`: status, the full insight response as JSON, timestamps |
| `fact_check_results` | `app/insights/repository/factcheck_orm_models.py:25-33` | One row per checked claim: conversation and speaker ids, the claim, the verdict and values, the evidence as JSON, timestamps |

### Job folders (full pipeline only)

- Each `/v1/process-audio` request gets its own folder, `data/jobs/<job_id>/`, relative to the working directory (`app/utils/job_io.py:36-50`). It contains `input/` (the uploaded audio), `artifacts/` (audio, ASR, diarization, alignment, NLP, insights, report and, when enabled, Agent Brain output; see the paths in `app/pipeline/orchestrator.py`), `logs/` and `meta.json`.
- **Retention:** a folder is deleted once it is older than `VOICEIQ_JOB_RETENTION_HOURS`. Age is taken from the time `meta.json` was last modified, or from the folder itself if `meta.json` is missing (`job_io.py:127-168`).
- **When the sweep runs:** each app's lifespan runs `run_retention()`: one sweep at startup, then one every hour in a background task while the app is up. Each sweep runs in a worker thread and purges job folders and stored records together (`app/utils/retention.py:39-68`; `app/main.py:41`, `app/insight_main.py:52`; `cbdafb4`). This replaced the hourly sweep that used to run inside `/v1/process-audio`, so the Docker image, which serves no audio route, now purges while it stays up.
- **Failures:** the sweep never raises. Each purge is wrapped in a logged `try/except` (`retention.py:39-49`). Removal uses `shutil.rmtree(..., ignore_errors=True)`; a folder still there afterwards is logged and not counted in the returned total, and a folder whose age cannot be read is logged and skipped (`job_io.py:151-165`).

### Model caches (full pipeline only)

The light image loads no models.

| Model | Where it is cached |
|---|---|
| Whisper `base` (`app/routes/process_audio.py:249`) | Whisper's default download folder; the code passes no `download_root` (`app/services/asr_service.py:37`). [DATA-FLOW.md](DATA-FLOW.md) §3 gives the path as `~/.cache/whisper`. Each process also keeps loaded models in memory (`asr_service.py:7`) |
| Hugging Face models: `pyannote/speaker-diarization`, `cardiffnlp/twitter-roberta-base-sentiment-latest`, `facebook/bart-large-mnli`, `sshleifer/distilbart-cnn-12-6`, `sentence-transformers/all-MiniLM-L6-v2` | The Hugging Face libraries' default cache. The repository sets no cache path (`diarization_service.py:47`, `sentiment_service.py:31`, `topic_service.py:34-36`, `summary_service.py:27-29`, `keyword_service.py:26`) |
| spaCy `en_core_web_sm` | Loaded with `spacy.load`, which does not download it (`app/services/keyword_service.py:20`). It must be installed separately as a Python package; no requirements file declares it |

---

## 5. External services

There is one line per service below. [DATA-FLOW.md](DATA-FLOW.md) has the full detail: what is sent, when, and the controls on it.

| Service | Called from | Summary |
|---|---|---|
| Model hosts (Whisper's public download host, Hugging Face) | `app.main` only | Weights are fetched when a model is first loaded and is not already cached. No call content is sent. Cache and network behaviour: [DATA-FLOW.md](DATA-FLOW.md) §3 |
| Fact-check providers (open.er-api.com, CoinGecko, OpenWeather, Alpha Vantage, Wikipedia REST) | Both entrypoints | Called by `POST /v1/fact-check`, and by `/v1/process-audio` only when fact-checking is requested (`?fact_check=true`, or `VOICEIQ_FACTCHECK_AUTO_ENRICH=true` when the query value is omitted; `app/utils/factcheck_gate.py:36-38`). Only short claim subjects are sent; at most 25 claims and 20 seconds per run by default. OpenWeather and Alpha Vantage need keys. The currency provider, open.er-api.com (ExchangeRate-API open access), needs no key and receives only the base currency code; each base is fetched at most once an hour per client, so currency claims can be verified (`source_clients/forex_client.py`; `45a5b22`) |
| Java Action Layer callback | `app.main` only (the Agent Brain stage of the pipeline) | Sent only when `VOICEIQ_AGENT_BRAIN_AUTO_RUN=true`, both URL and secret are set, and at least one recommendation meets `VOICEIQ_AGENT_MIN_CONFIDENCE` (`app/pipeline/orchestrator.py:828-841`). It is an HMAC-signed POST, sent outside the request path. The contract is in [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md) |

---

## 6. Logging

| Behaviour | Where | Since |
|---|---|---|
| On import, the root logger is set to INFO with the format `%(asctime)s - %(levelname)s - %(message)s`, and the application logger is named `voiceiq` | `app/utils/logger.py:18-28` | — |
| Setup runs on the first import of `app.utils.logger`. `app.main` also calls it explicitly. `app.insight_main` imports `configure_logging` from it | `logger.py:26`; `app/main.py:27`; `app/insight_main.py:36` | — |
| At startup each app's lifespan calls `configure_logging(settings)`, which sets the root level to `VOICEIQ_LOG_LEVEL` and logs a warning when `VOICEIQ_THRESHOLD_PROFILE` is set | `logger.py:31-42`; `app/main.py:34`; `app/insight_main.py:47` | `e25a3b2` |
| The `httpx` and `httpcore` loggers are pinned to WARNING whatever the root level is, so outbound request URLs are not logged | `logger.py:10-23` | `c1b35d9` |
| The currency client logs only the provider's error type | `app/insights/core/factcheck/source_clients/forex_client.py:98-104` | `45a5b22` |
| A failed readiness check logs the check name, and for the database only the exception class; the `/readyz` response carries no error text | `app/utils/readiness.py:21-35` | `660558c` |
| A failed Java callback logs only the exception class and HTTP status, never the exception text or the payload | `app/pipeline/orchestrator.py:853-863` | `cedfe96` |
| A failed insight run, a failed fact-check enrichment in `/v1/process-audio` and a failed fact-check save log only the exception class, never its text | `app/insights/service.py:89`; `app/routes/process_audio.py:109-113, :118-122` | `0011dd7` |

- `VOICEIQ_LOG_LEVEL` changes only the root level, from startup on. The HTTP loggers stay at WARNING.
- `VOICEIQ_DATABASE_ECHO=true` adds SQLAlchemy statement logging.
- In the container, `PYTHONUNBUFFERED=1` makes logs appear without delay. Read them with `docker compose logs -f voiceiq-app`.
- `app/insights/tests/test_logging_hygiene.py` pins the logging rules. It checks that the HTTP loggers stay at WARNING, including after importing the Docker entrypoint (`:169`, `:229`), and that the callback URL never appears in a log record (`:276`).

---

## 7. CI

CI is defined in `.github/workflows/test.yml` and runs on pushes to `main` and on pull requests to `main` (`:3-7`).

| Job | Python | What it runs |
|---|---|---|
| `lint` ("Lint, types, secrets") | 3.11 | Installs `requirements-insight.txt`, ruff 0.7.4 and mypy 1.13.0. Then runs `ruff check .` and `mypy --ignore-missing-imports --check-untyped-defs app/insights/ app/agent_brain/`. It downloads the gitleaks 8.21.2 archive and the release's checksums file, checks the archive with `sha256sum --check` and installs it only on a match, in a step that runs with `set -euo pipefail` (`:37-48`, `c661bad`). It then runs `gitleaks dir . --no-banner --redact` (`:53-54`). Job: `:10-54` |
| `test` ("Run Insight Service unit tests") | 3.10 and 3.11 (matrix) | Installs `requirements-insight.txt` only, then runs `pytest -v app/insights/tests/ app/agent_brain/tests/` with `PYTHONPATH=.`. On failure, the end of the output is posted as an `::error::` annotation (`:56-100`) |

- **Expected test result:** 1730 passed and 1 skipped on the light stack, measured 2026-10-17 at `dbae452`. The skipped item is `app/insights/tests/test_model_load_concurrency.py`, a 3-test module that needs the ML libraries ([CLAUDE.md](../CLAUDE.md), "One test lane").
- **Not in CI:** a Docker build, the heavy ML stack, and the legacy top-level `tests/` folder.
- **Tool settings:** ruff and mypy are configured in `pyproject.toml`: line length 120, target `py310`, and the pydantic mypy plugin.
- **Python versions in use:** Docker 3.11, CI 3.10 and 3.11, README "Python 3.10+" (`README.md:66`).
- **Before a commit:** pre-commit hooks run locally: ruff, ruff-format, three hygiene hooks run through `python -m` (`7b25b3f`), and gitleaks (`.pre-commit-config.yaml`). How to use them is in [WORKFLOW.md](WORKFLOW.md).
