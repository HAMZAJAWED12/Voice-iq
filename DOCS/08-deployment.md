# 08 — Deployment

The Insight Service is deployed as a containerised FastAPI process. The repository ships a hardened multi-stage `Dockerfile`, a `docker-compose.yml` for single-host orchestration, and the necessary configuration hooks. This document covers the container path, environment overrides per environment, and the operational expectations.

> **Image scope:** this image is **insight-only**. `/v1/process-audio` (audio upload + ASR + diarization) is **not** included — it requires the heavy ML stack (torch, whisper, pyannote) and is intended for a separate full-pipeline image (deferred task). The endpoints that DO work in the container are listed under [Image variants](#image-variants).

---

## Dockerfile

The Dockerfile in the repository root is multi-stage and pinned to **Python 3.11** (matching CI).

### Stage 1 — `builder`

```dockerfile
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-insight.txt ./
RUN pip wheel --wheel-dir=/build/wheels \
      -r requirements-insight.txt \
      "uvicorn[standard]==0.32.1" \
      "python-dotenv>=1.0.0"
```

`requirements-insight.txt` is the **single source of truth** for pinned versions, shared with CI (FastAPI, Uvicorn, Pydantic, Pydantic-Settings, SQLAlchemy, python-multipart, httpx, pytest). The full `requirements.txt` with `torch` / `whisper` / `pyannote` is not used here — the insight container does not need them. `uvicorn[standard]` and `python-dotenv` are runtime-only extras appended in the Dockerfile because CI does not need them.

### Stage 2 — `runtime`

```dockerfile
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    VOICEIQ_ENVIRONMENT=production \
    VOICEIQ_DATABASE_URL=sqlite:////app/data/insights.db

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

COPY --from=builder /build/wheels /tmp/wheels
COPY requirements-insight.txt ./
RUN pip install --no-index --find-links=/tmp/wheels \
      -r requirements-insight.txt \
      "uvicorn[standard]==0.32.1" \
      "python-dotenv>=1.0.0" \
 && rm -rf /tmp/wheels

COPY app ./app
COPY .env.example ./.env.example

RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8888/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.insight_main:app", "--host", "0.0.0.0", "--port", "8888"]
```

Notable choices:

- **`app.insight_main:app` is the CMD**, not `app.main:app`. The image is the insight-only deployment. The full pipeline runs in a different image (deferred task).
- **Non-root user (`app`).** The container drops privileges before serving traffic.
- **Layer caching.** `requirements-insight.txt` is copied and installed before `app/` is copied, so source edits don't bust the dep layer.
- **`/app/data` is a volume**. The default `VOICEIQ_DATABASE_URL=sqlite:////app/data/insights.db` writes here. Mount a host directory or a Docker volume to persist data across container restarts.
- **The healthcheck uses `urllib`** to avoid depending on `curl` in the slim base.
- **No GPU / CUDA layers.** The image is ~200 MB and starts in under a second.

---

## Image variants

| Variant | Image | What works | What does not |
|---|---|---|---|
| Insight (this image) | `voiceiq-insight:*` | `/v1/insights/*`, `/v1/fact-check/*`, `/healthz`, `/version`, `/docs` | `/v1/process-audio` (imports torch/whisper at startup) |
| Full pipeline | not yet built | everything | n/a |

If you call `/v1/process-audio` against this image you will get an import-time failure. Use the local non-Docker workflow described in `07-developer-setup.md` until the full-pipeline image is built.

---

## Build

For local development:

```bash
docker build -t voiceiq-insight:dev .
```

For a versioned production image, build with the explicit version tag and add `:latest` as a moving alias:

```bash
docker build -t voiceiq-insight:1.0.0 .
docker tag voiceiq-insight:1.0.0 voiceiq-insight:latest
```

Always deploy from the explicit version tag (`1.0.0`), not `:latest` — `:latest` is a convenience for local pulls, not a deployment contract.

For multi-arch builds (Apple Silicon → Linux/amd64 deploy):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t voiceiq-insight:1.0.0 --push .
```

---

## Environment file

The container reads runtime configuration from environment variables. The compose flow loads them from a host-side `.env` file; raw `docker run` invocations pass them with `-e` flags.

The repository ships `.env.example` as a template. **`.env` itself is gitignored and must never be baked into the image** — the `.dockerignore` enforces this.

### Dev quick-start

```bash
cp .env.example .env
```

Empty values are fine in development. Fact-check source clients without an API key (e.g. `VOICEIQ_OPENWEATHER_API_KEY=`) return a `SOURCE_UNAVAILABLE` verdict instead of crashing.

### Production

Populate the API keys in `.env` (or, preferably, inject them through your orchestrator's secret store) **before** starting the container:

```env
VOICEIQ_OPENWEATHER_API_KEY=...
VOICEIQ_ALPHAVANTAGE_API_KEY=...
VOICEIQ_ENVIRONMENT=prod
VOICEIQ_LOG_LEVEL=WARNING
```

For Kubernetes / ECS / Nomad deployments, prefer mounting these as secrets rather than a literal `.env` on disk.

---

## Run

### Development

```bash
docker run --rm -p 8888:8888 \
  -v "$(pwd)/data:/app/data" \
  -e VOICEIQ_ENVIRONMENT=dev \
  voiceiq-insight:dev
```

The bind-mounted `./data/` directory makes the SQLite database persist on the host, so insights survive container restarts.

### Staging

```bash
docker run -d --name voiceiq-insight \
  -p 8888:8888 \
  -v voiceiq-data:/app/data \
  -e VOICEIQ_ENVIRONMENT=staging \
  -e VOICEIQ_LOG_LEVEL=INFO \
  -e VOICEIQ_THRESHOLD_PROFILE=default \
  --restart unless-stopped \
  voiceiq-insight:1.0.0
```

The named volume `voiceiq-data` is created on first run and survives container removal.

### Production (single-host)

```bash
docker run -d --name voiceiq-insight \
  -p 8888:8888 \
  -v voiceiq-data:/app/data \
  --env-file .env \
  -e VOICEIQ_ENVIRONMENT=prod \
  -e VOICEIQ_LOG_LEVEL=WARNING \
  -e VOICEIQ_THRESHOLD_PROFILE=default \
  -e VOICEIQ_DATABASE_AUTO_CREATE=true \
  --restart unless-stopped \
  voiceiq-insight:1.0.0
```

For a production deployment beyond a single host, swap the SQLite default for PostgreSQL via `VOICEIQ_DATABASE_URL`, and place the container behind a reverse proxy (nginx / Caddy / Traefik) that handles TLS termination.

---

## Running with docker-compose

The repository ships `docker-compose.yml` for the single-host workflow. It builds the image, mounts the named volume `voiceiq-data` for SQLite persistence, loads env vars from `.env`, and applies an `unless-stopped` restart policy.

```bash
cp .env.example .env          # one-time
docker compose up --build     # foreground build + run
docker compose up -d          # detached
docker compose logs -f voiceiq-app
docker compose down           # stop + remove (named volume survives)
docker compose down -v        # stop + remove + drop the SQLite volume
```

The compose file maps host port `8888` → container port `8888`. Once running:

```bash
curl http://localhost:8888/healthz
curl http://localhost:8888/version
open http://localhost:8888/docs
```

---

## Environment overrides

| Variable | Dev | Staging | Prod |
|----------|-----|---------|------|
| `VOICEIQ_ENVIRONMENT` | `dev` | `staging` | `prod` |
| `VOICEIQ_LOG_LEVEL` | `DEBUG` | `INFO` | `WARNING` |
| `VOICEIQ_DATABASE_URL` | `sqlite:///./data/insights.db` | `sqlite:///./data/insights.db` | PostgreSQL DSN |
| `VOICEIQ_DATABASE_AUTO_CREATE` | `true` | `true` | `true` (or `false` if managed by external migrations) |
| `VOICEIQ_DATABASE_ECHO` | `false` | `false` | `false` |
| `VOICEIQ_THRESHOLD_PROFILE` | `default` | `default` | `default` (set per call by the upstream caller in advanced setups) |

`VOICEIQ_DATABASE_ECHO=true` is useful when diagnosing repository issues but should never be enabled in production — it logs every SQL statement issued by SQLAlchemy.

---

## Healthcheck

The `/healthz` endpoint returns `200 OK` with `{"status": "ok"}` once the FastAPI app's `lifespan` has completed. The Dockerfile's `HEALTHCHECK` polls `http://127.0.0.1:8888/healthz` every 30 seconds with a 5-second timeout. Three consecutive failures mark the container as unhealthy.

Behind a load balancer, configure a TCP or HTTP probe against `/healthz` with similar semantics. The app is single-process and stateless modulo the SQLite file — restarts are safe at any time.

---

## Persistence considerations

The default SQLite configuration is suitable for:

- single-host deployments,
- low-write throughput (the insight service writes one row per session, and the bottleneck is typically the upstream audio pipeline),
- environments where backup means snapshotting the volume.

For multi-host or write-heavy production:

1. Provision a PostgreSQL instance.
2. Set `VOICEIQ_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/voiceiq_insights`.
3. Install `psycopg2-binary` in the image (add to `requirements-insight.txt` and rebuild).
4. Decide whether to keep `VOICEIQ_DATABASE_AUTO_CREATE=true` (the lifespan will run `CREATE TABLE IF NOT EXISTS`) or set it to `false` and manage the schema with Alembic migrations.

The ORM model is intentionally trivial — a single table with primary key, status, JSON payload, and timestamps. A first-cut Alembic migration is straightforward.

---

## Logging

The app uses Python's standard `logging` library. The log level is set from `VOICEIQ_LOG_LEVEL`. Logs go to stdout/stderr in standard FastAPI/Uvicorn format. No transcript text is logged — the explainability principle requires structured `evidence` on every flag, but raw utterance text never crosses the logging boundary.

---

## Resource expectations

A typical session (10–50 utterances) takes under 50 ms end-to-end on a modern x86 core. Memory footprint of the running app is around 80–120 MB resident depending on the SQLite cache.

The service has no batch mode or worker pool. Each request is handled synchronously by Uvicorn's threadpool. For high concurrency, scale horizontally — there is no shared in-process state beyond the SQLAlchemy connection pool and the `lru_cache` on settings.

---

## Operational checklist

Before promoting an Insight Service deployment to production, verify:

- The `/healthz` probe is configured at the load balancer.
- `VOICEIQ_LOG_LEVEL` is `INFO` or `WARNING`, not `DEBUG`.
- `VOICEIQ_DATABASE_ECHO` is `false`.
- The `/app/data` volume is mounted to durable storage (or `VOICEIQ_DATABASE_URL` points at PostgreSQL).
- `VOICEIQ_ENVIRONMENT` is set to `prod` so it surfaces correctly in `/version` and any downstream observability.
- The image was built reproducibly (tagged version, not `latest`).
- The container's healthcheck is reporting `healthy` before traffic is routed.
