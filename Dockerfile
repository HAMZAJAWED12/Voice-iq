# =============================================================================
# VoiceIQ Insight Service — container image
#
# Insight-only deployment. Multi-stage build:
#   1. `builder` compiles wheels from requirements-insight.txt.
#   2. `runtime` is a slim image carrying the wheels + app source.
#
# This image runs `app.insight_main:app`, exposing the insight + fact-check
# routes plus the ops endpoints. The audio pipeline (`/v1/process-audio`)
# requires torch / whisper / pyannote and is intentionally NOT included —
# that ships as a separate full-pipeline image (deferred task).
#
# Mount a host volume at /app/data to persist the SQLite file across runs.
#
# Build:
#   docker build -t voiceiq-insight:dev .
#
# Run:
#   docker run --rm -p 8888:8888 -v "$(pwd)/data:/app/data" voiceiq-insight:dev
#
# Then visit http://localhost:8888/docs
# =============================================================================

ARG PYTHON_VERSION=3.11

# --------------------------------------------------------------------------- #
# Stage 1 — build wheels                                                       #
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# requirements-insight.txt is the single source of truth for pinned versions
# (shared with CI). uvicorn[standard] and python-dotenv are runtime-only
# extras not needed by CI, so they are appended here.
COPY requirements-insight.txt ./
RUN pip wheel --wheel-dir=/build/wheels \
      -r requirements-insight.txt \
      "uvicorn[standard]==0.32.1" \
      "python-dotenv>=1.0.0"

# --------------------------------------------------------------------------- #
# Stage 2 — runtime                                                            #
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VOICEIQ_ENVIRONMENT=production \
    VOICEIQ_DATABASE_URL=sqlite:////app/data/insights.db

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

# Install deps before copying source so code edits don't bust this layer.
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
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8888/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.insight_main:app", "--host", "0.0.0.0", "--port", "8888"]
