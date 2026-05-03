# app/routes/process_audio.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from typing import Optional
import uuid
import os

from app.utils.logger import logger
from app.utils.job_io import JobIO
from app.pipeline.orchestrator import VoiceIQOrchestrator

# Sprint-5 fact-check engine. Imported here so the audio pipeline can
# auto-enrich every transcript with rule-based fact verification.
from app.insights.api.factcheck_routes import get_factcheck_engine
from app.insights.models.factcheck_models import MAX_TRANSCRIPT_CHARS
from app.insights.repository import factcheck_repository

router = APIRouter()


def _auto_run_factcheck(request_id: str, result: dict) -> None:
    """Best-effort: enrich the orchestrator result with fact-check verdicts.

    Adds a top-level ``fact_checks_v2`` key containing the FactCheckResponse
    payload (or an error stub). The legacy ``fact_checks`` key produced by
    the orchestrator is preserved untouched for backward compatibility.

    Failures here MUST NOT break the audio response.
    """
    transcript = (result.get("transcript") or "").strip()
    if not transcript:
        result["fact_checks_v2"] = {
            "status": "skipped",
            "reason": "empty transcript",
        }
        return

    # The fact-check API limits transcript size; truncate defensively here
    # so very long calls do not raise validation errors mid-pipeline.
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]

    try:
        engine = get_factcheck_engine()
        response = engine.run(
            conversation_id=request_id,
            speaker_id="pipeline",
            transcript_text=transcript,
        )
        # Persistence is best-effort - log and continue on failure.
        try:
            factcheck_repository.save_response(response)
        except Exception as persist_exc:
            logger.warning(
                "fact-check persistence failed for request_id=%s (%s)",
                request_id,
                persist_exc,
            )
        result["fact_checks_v2"] = response.model_dump(mode="json")
    except Exception as exc:
        logger.warning(
            "fact-check auto-run failed for request_id=%s (%s)",
            request_id,
            exc,
        )
        result["fact_checks_v2"] = {
            "status": "failed",
            "reason": str(exc),
        }


@router.post("/process-audio")
async def process_audio(
    file: UploadFile = File(...),
    expected_speakers: Optional[int] = Query(default=None, description="Optional hint (2 for calls, 3-6 for meetings)")
):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Received: {file.filename}")

    if not file.filename.lower().endswith((".mp3", ".wav", ".m4a", ".flac")):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    io = JobIO()
    job = io.init_job(request_id)

    ext = os.path.splitext(file.filename)[1].lower() or ".mp3"
    input_path = io.p(job, f"input/original{ext}")

    try:
        input_path.write_bytes(await file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}")

    orch = VoiceIQOrchestrator(job_io=io)
    result = orch.run(
        job_id=request_id,
        expected_speakers=expected_speakers,
        max_speakers_cap=8,
        whisper_model="base",
        language=None,
    )

    if result.get("pipeline_meta", {}).get("status") == "failed":
        raise HTTPException(status_code=400, detail={
            "request_id": request_id,
            "warnings": result.get("warnings", []),
            "pipeline_meta": result.get("pipeline_meta", {}),
        })

    # Auto-enrich with rule-based fact verification (Sprint 5).
    _auto_run_factcheck(request_id, result)

    return result