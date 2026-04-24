# 08 — Deployment

The Insight Service is intended to be deployed as a containerised FastAPI process. The repository ships a multi-stage `Dockerfile` and the necessary configuration hooks. This document covers the container path, environment overrides per environment, and the operational expectations.

---

## Dockerfile

The Dockerfile in the repository root is multi-stage:

### Stage 1 — `builder`

```dockerfile
FROM python:3.10-slim AS builder

WORKDIR /build
RUN pip install --upgrade pip
COPY requirements-insight.txt .
RUN pip wheel --wheel-dir /wheels -r requirements-insight.txt
```

`requirements-insight.txt` is the focused subset of dependencies needed by the Insight Service (FastAPI, Uvicorn, Pydantic, Pydantic-Settings, SQLAlchemy, python-multipart). The full `requirements.txt` with `torch` / `whisper` / `pyannote` is not used here — the insight container does not need them.

The builder stage produces the wheel cache.

### Stage 2 — `runtime`

```dockerfile
FROM python:3.10-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=builder /wheels /wheels
COPY requirements-insight.txt .
RUN pip install --no-index --find-links=/wheels -r requirements-insight.txt && \
    rm -rf /wheels

COPY app /app/app
COPY samples /app/samples

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()"

CMD ["uvicorn", "app.insight_main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Notable choices:

- **`app.insight_main:app` is the CMD**, not `app.main:app`. The Docker image is for the insight-only deployment. The full pipeline runs in a different image (with the audio pipeline dependencies).
- **`/app/data` is a named volume**. The default `VOICEIQ_DATABASE_URL=sqlite:///./data/insights.db` writes here. Mount a host directory or a Docker volume to persist data across container restarts.
- **The healthcheck uses `urllib`** rather than `curl` to avoid depending on a curl install in the slim base image.
- **No GPU / CUDA layers**. The image is < 200 MB and starts in under a second.

---

## Build

```bash
docker build -t voiceiq-insight:1.0.0 .
```

For multi-arch builds (Apple Silicon → Linux/amd64 deploy):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t voiceiq-insight:1.0.0 --push .
```

---

## Run

### Development

```bash
docker run --rm -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  -e VOICEIQ_ENVIRONMENT=dev \
  voiceiq-insight:1.0.0
```

The bind-mounted `./data/` directory makes the SQLite database persist on the host, so insights survive container restarts.

### Staging

```bash
docker run -d --name voiceiq-insight \
  -p 8000:8000 \
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
  -p 8000:8000 \
  -v voiceiq-data:/app/data \
  -e VOICEIQ_ENVIRONMENT=prod \
  -e VOICEIQ_LOG_LEVEL=WARNING \
  -e VOICEIQ_THRESHOLD_PROFILE=default \
  -e VOICEIQ_DATABASE_AUTO_CREATE=true \
  --restart unless-stopped \
  --health-cmd "python -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8000/healthz\").read()'" \
  voiceiq-insight:1.0.0
```

For a production deployment beyond a single host, the SQLite default should be swapped for PostgreSQL via `VOICEIQ_DATABASE_URL`, and the container should be placed behind a reverse proxy (nginx / Caddy / Traefik) that handles TLS termination.

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

The `/healthz` endpoint returns `200 OK` with `{"status": "ok"}` once the FastAPI app's `lifespan` has completed. The Dockerfile's `HEALTHCHECK` polls every 30 seconds with a 5-second timeout. Three consecutive failures mark the container as unhealthy.

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
