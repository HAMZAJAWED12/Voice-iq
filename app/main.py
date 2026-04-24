"""FastAPI entrypoint for the VoiceIQ service.

Wires the audio-processing routes and the Insight Service routes, plus
two ops endpoints (`/healthz`, `/version`). Database schema for the
Insight Service is ensured on startup so the first request never has to
race a `CREATE TABLE`.
"""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.insights.api import router as insight_router
from app.insights.config.settings import get_settings
from app.insights.repository.db import init_db
from app.routes.process_audio import router as process_router
from app.utils.logger import setup_logging

load_dotenv()
setup_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the Insight Service schema exists before serving traffic.
    # init_db() respects VOICEIQ_DATABASE_AUTO_CREATE=false for environments
    # where migrations are managed externally.
    init_db(settings)
    yield


app = FastAPI(
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
