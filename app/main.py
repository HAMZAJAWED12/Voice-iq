"""FastAPI entrypoint for the VoiceIQ service.

Wires the audio-processing routes and the Insight Service routes, plus
two ops endpoints (`/healthz`, `/version`). Database schema for the
Insight Service is ensured on startup so the first request never has to
race a `CREATE TABLE`.
"""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.agent_brain.api.agent_routes import router as agent_brain_router
from app.insights.api import router as insight_router
from app.insights.config.settings import get_settings
from app.insights.repository.db import init_db
from app.routes.process_audio import MAX_UPLOAD_BYTES
from app.routes.process_audio import router as process_router
from app.security.request_guard import RequestGuardMiddleware, guarded_limits
from app.utils.logger import setup_logging
from app.utils.retention import run_retention

load_dotenv()
setup_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the Insight Service schema exists before serving traffic.
    # init_db() respects VOICEIQ_DATABASE_AUTO_CREATE=false for environments
    # where migrations are managed externally.
    init_db(settings)
    # Sweep expired job artifacts and stored records now and then hourly
    # (bounds disk + PII TTL).
    async with run_retention():
        yield


# Interactive docs expose the full request/response schema of every route.
# Useful in dev, needless attack-surface detail in production.
_docs_enabled = settings.environment != "production"

app = FastAPI(
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
    title=settings.service_name,
    description=(
        "VoiceIQ pipeline: Audio → ASR → Diarization → Alignment → "
        "Metadata → NLP Enrichment → Insight Service. The `/v1/insights/*` "
        "routes expose conversational intelligence (sentiment, escalation, "
        "inconsistency, timeline, summaries) on top of pipeline output."
    ),
    version=settings.service_version,
    lifespan=lifespan,
)

app.include_router(process_router, prefix="/v1")
app.include_router(insight_router, prefix="/v1")
# Agent Brain carries its own /internal/v1/agent-brain prefix. Internal-only:
# the Java Action Layer calls it with an X-API-Key; it is not part of the
# public /v1 surface.
app.include_router(agent_brain_router)
# Checks the API key and declared size of guarded POSTs before the body is
# read, and counts the bytes that arrive (app/security/request_guard.py).
app.add_middleware(RequestGuardMiddleware, limits=guarded_limits(settings, upload_max_bytes=MAX_UPLOAD_BYTES))


@app.get("/healthz", tags=["Ops"], summary="Liveness probe")
def healthz():
    return {"status": "ok"}


@app.get("/version", tags=["Ops"], summary="Service version + environment")
def version():
    return {
        "version": app.version,
        "service": settings.service_name,
        "environment": settings.environment,
    }
