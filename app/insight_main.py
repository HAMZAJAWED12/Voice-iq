"""Standalone FastAPI entrypoint for the Insight Service.

Mounts only the `/insights/*` routes plus the ops endpoints, so the
container image does not need to carry the heavy audio-processing stack
(torch, whisper, pyannote, librosa, soundfile, ...).

The full pipeline lives in `app.main`, which additionally wires
`/v1/process-audio` and friends. Use that entrypoint when you also need
ASR / diarization in the same image.

Run locally:
    uvicorn app.insight_main:app --reload --port 8000

Run in Docker (this is what the bundled Dockerfile invokes):
    uvicorn app.insight_main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dep

    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False


from fastapi import FastAPI

from app.insights.api import router as insight_router
from app.insights.config.settings import get_settings
from app.insights.repository.db import init_db

load_dotenv()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings)
    yield


app = FastAPI(
    title=f"{settings.service_name} — Insight Service",
    description=(
        "Standalone Insight Service surface. Exposes the conversational "
        "intelligence layer (sentiment, escalation, inconsistency, "
        "timeline, summaries) on top of pre-processed pipeline output. "
        "ASR / diarization / alignment are NOT included in this image — "
        "use `app.main` for the full pipeline."
    ),
    version=settings.service_version,
    lifespan=lifespan,
)

app.include_router(insight_router, prefix="/v1")


@app.get("/healthz", tags=["Ops"], summary="Liveness probe")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/version", tags=["Ops"], summary="Service version + environment")
def version() -> dict:
    return {
        "version": app.version,
        "service": settings.service_name,
        "environment": settings.environment,
    }
