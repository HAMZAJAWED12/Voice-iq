# app/routes/process_audio.py
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

# Sprint-5 fact-check engine. Imported here so the audio pipeline can
# auto-enrich every transcript with rule-based fact verification.
from app.insights.api.factcheck_routes import get_factcheck_engine
from app.insights.config.settings import get_settings
from app.insights.models.factcheck_models import MAX_TRANSCRIPT_CHARS
from app.insights.repository import factcheck_repository
from app.pipeline.orchestrator import VoiceIQOrchestrator
from app.security import enforce_content_length, verify_api_key
from app.utils.job_io import JobIO
from app.utils.logger import logger

# Hard cap for /v1/process-audio uploads (multipart). Constructed once at
# import time from settings; restart the process to pick up a new value.
# Defence-in-depth: the handler also enforces this cap during streaming
# write so a lying Content-Length cannot bypass it.
MAX_UPLOAD_BYTES = get_settings().api_max_upload_mb * 1024 * 1024
_enforce_upload_size = enforce_content_length(MAX_UPLOAD_BYTES)

# Every route on this router requires a valid X-API-Key header (see
# app.security.api_key.verify_api_key for the dev/prod behaviour matrix)
# and uploads are bounded by api_max_upload_mb.
router = APIRouter(
    dependencies=[
        Depends(verify_api_key),
        Depends(_enforce_upload_size),
    ]
)


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
    expected_speakers: int | None = Query(default=None, description="Optional hint (2 for calls, 3-6 for meetings)"),
):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Received: {file.filename}")

    if not file.filename.lower().endswith((".mp3", ".wav", ".m4a", ".flac")):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    io = JobIO()
    job = io.init_job(request_id)

    ext = os.path.splitext(file.filename)[1].lower() or ".mp3"
    input_path = io.p(job, f"input/original{ext}")

    # Stream the upload to disk in 1 MB chunks. We do not trust the
    # Content-Length header alone: a hostile client can lie, so we count
    # bytes as they arrive and abort the moment we exceed MAX_UPLOAD_BYTES.
    # The router-level enforce_content_length dependency catches honest
    # over-sized requests first; this is defence-in-depth.
    chunk_size = 1024 * 1024
    total_bytes = 0
    exceeded = False
    try:
        with open(input_path, "wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    exceeded = True
                    break
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}") from e

    if exceeded:
        input_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=(f"Upload exceeded the limit of {MAX_UPLOAD_BYTES} bytes " "mid-stream."),
        )

    orch = VoiceIQOrchestrator(job_io=io)
    result = orch.run(
        job_id=request_id,
        expected_speakers=expected_speakers,
        max_speakers_cap=8,
        whisper_model="base",
        language=None,
    )

    if result.get("pipeline_meta", {}).get("status") == "failed":
        # ffmpeg timeout is the caller's fault (corrupt / pathological input,
        # not server malfunction), so surface it as 422. Other failures stay
        # at 400 — malformed file the user can fix by re-encoding.
        warnings = result.get("warnings", [])
        status_code = 422 if "AUDIO_NORMALIZATION_TIMEOUT" in warnings else 400
        raise HTTPException(
            status_code=status_code,
            detail={
                "request_id": request_id,
                "warnings": warnings,
                "pipeline_meta": result.get("pipeline_meta", {}),
            },
        )

    # Auto-enrich with rule-based fact verification (Sprint 5).
    _auto_run_factcheck(request_id, result)

    return result
