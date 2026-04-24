# =============================================================================
# VoiceIQ Insight Service – container image
#
# Two-stage build:
#   1. `builder` installs build deps and compiles wheels.
#   2. `runtime` is a slim image that only carries the wheels + app code.
#
# Defaults assume the SQLite-backed Insight Service. Mount a host volume at
# /app/data to persist the SQLite file (./data/insights.db) across runs.
#
# Build:
#   docker build -t voiceiq-insights:latest .
#
# Run:
#   docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" voiceiq-insights:latest
#
# Then visit http://localhost:8000/docs
# =============================================================================

ARG PYTHON_VERSION=3.10

# --------------------------------------------------------------------------- #
# Stage 1 — build wheels                                                       #
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install only what we need to compile wheels. We deliberately keep this
# minimal — heavy ML deps (torch, whisper, pyannote) are NOT required to
# run the Insight Service itself and would bloat the image substantially.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

# Build a wheelhouse for the *Insight Service* runtime only. The full
# pipeline depends on torch/whisper/pyannote, which should be added in a
# separate image when the audio pipeline is needed.
RUN pip wheel --wheel-dir=/build/wheels \
      "fastapi==0.115.6" \
      "uvicorn[standard]==0.32.1" \
      "python-multipart==0.0.17" \
      "pydantic==2.12.4" \
      "pydantic-settings>=2.6.0" \
      "httpx==0.28.1" \
      "SQLAlchemy>=2.0.30" \
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

# Run as a non-root user.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-index --find-links=/tmp/wheels \
      fastapi uvicorn python-multipart pydantic pydantic-settings \
      httpx SQLAlchemy python-dotenv \
 && rm -rf /tmp/wheels

# Copy only the source the Insight Service needs to run.
COPY app ./app
COPY .env.example ./.env.example

# Persistent volume for the SQLite database.
RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
EXPOSE 8000

# Healthcheck hits the lightweight liveness route.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.insight_main:app", "--host", "0.0.0.0", "--port", "8000"]
