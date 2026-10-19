# app/routes/process_audio.py
import asyncio
import json
import uuid
import weakref
from dataclasses import dataclass

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

# Sprint-5 fact-check engine. Imported here so the audio pipeline can
# auto-enrich every transcript with rule-based fact verification.
from app.insights.api.factcheck_routes import get_factcheck_engine
from app.insights.config.settings import get_settings
from app.insights.models.factcheck_models import MAX_TRANSCRIPT_CHARS
from app.insights.repository import factcheck_repository
from app.pipeline.orchestrator import VoiceIQOrchestrator
from app.security import enforce_content_length, verify_api_key
from app.security.rate_limit import process_audio_rate_limit
from app.utils.audio_sniff import is_recognized_audio
from app.utils.factcheck_gate import DISABLED_STUB, factcheck_requested
from app.utils.job_io import JobIO
from app.utils.logger import logger
from app.utils.upload import resolve_audio_ext

# Hard cap for /v1/process-audio uploads (multipart). Constructed once at
# import time from settings; restart the process to pick up a new value.
# Defence-in-depth: the handler also enforces this cap during streaming
# write so a lying Content-Length cannot bypass it.
MAX_UPLOAD_BYTES = get_settings().api_max_upload_mb * 1024 * 1024
_enforce_upload_size = enforce_content_length(MAX_UPLOAD_BYTES)


@dataclass
class _Admission:
    """Pipeline slots and admitted uploads for one event loop (one worker)."""

    slots: anyio.CapacityLimiter
    inflight: int = 0  # admitted uploads: running or waiting for a slot


# Pipeline runs execute in a worker thread so the event loop keeps serving
# other requests. At most `process_audio_max_running` run at once per event
# loop (one loop per worker process) and `process_audio_max_waiting` more
# wait; a further upload gets 503 with Retry-After. Keyed by loop because a
# limiter belongs to the loop it was created on; the counter changes only on
# that loop, so it needs no lock.
_ADMISSIONS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Admission]" = weakref.WeakKeyDictionary()


def _admission() -> _Admission:
    loop = asyncio.get_running_loop()
    admission = _ADMISSIONS.get(loop)
    if admission is None:
        slots = anyio.CapacityLimiter(get_settings().process_audio_max_running)
        admission = _ADMISSIONS[loop] = _Admission(slots=slots)
    return admission


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
        payload = response.model_dump(mode="json")
        # The audio response is rendered as strict JSON (no NaN/Infinity): check
        # the fact-check part first, so it degrades to the failed stub below
        # instead of failing the whole response — and is never persisted.
        json.dumps(payload, allow_nan=False)
        # Persistence is best-effort - log and continue on failure.
        try:
            factcheck_repository.save_response(response)
        except Exception as persist_exc:
            logger.warning(
                "fact-check persistence failed for request_id=%s (%s)",
                request_id,
                type(persist_exc).__name__,
            )
        result["fact_checks_v2"] = payload
    except Exception as exc:
        # The exception text can carry provider or input details: log the
        # class only and give the caller a fixed reason.
        logger.warning(
            "fact-check auto-run failed for request_id=%s (%s)",
            request_id,
            type(exc).__name__,
        )
        result["fact_checks_v2"] = {
            "status": "failed",
            "reason": "fact-check enrichment failed",
        }


@router.post("/process-audio", dependencies=[Depends(process_audio_rate_limit)])
async def process_audio(
    file: UploadFile = File(...),
    expected_speakers: int | None = Query(default=None, description="Optional hint (2 for calls, 3-6 for meetings)"),
    fact_check: bool | None = Query(
        default=None,
        description=(
            "Run fact-check enrichment for this request. Omit to use the "
            "factcheck_auto_enrich setting (default off). Fact-check sends "
            "transcript-derived claim subjects to external providers — see "
            "DOCS/DATA-FLOW.md."
        ),
    ),
):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Received: {file.filename}")

    # First gate: cheap filename-extension allowlist. Also guards a missing
    # filename (multipart part with no name) — previously an unhandled 500.
    try:
        ext = resolve_audio_ext(file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    settings = get_settings()
    admission = _admission()
    if admission.inflight >= settings.process_audio_max_running + settings.process_audio_max_waiting:
        raise HTTPException(
            status_code=503,
            detail="Audio processing is at capacity. Retry later.",
            headers={"Retry-After": str(settings.process_audio_retry_after_sec)},
        )
    admission.inflight += 1
    try:
        return await _admitted_request(request_id, file, ext, expected_speakers, fact_check, admission)
    finally:
        admission.inflight -= 1


async def _admitted_request(
    request_id: str,
    file: UploadFile,
    ext: str,
    expected_speakers: int | None,
    fact_check: bool | None,
    admission: _Admission,
) -> dict:
    """Save the upload, then run the pipeline in a slot."""
    io = JobIO()
    job = io.init_job(request_id)

    input_path = io.p(job, f"input/original{ext}")

    # Stream the upload to disk in 1 MB chunks. We do not trust the
    # Content-Length header alone: a hostile client can lie, so we count
    # bytes as they arrive and abort the moment we exceed MAX_UPLOAD_BYTES.
    # The router-level enforce_content_length dependency catches honest
    # over-sized requests first; this is defence-in-depth.
    chunk_size = 1024 * 1024
    total_bytes = 0
    exceeded = False
    bad_content = False
    first_chunk = True
    try:
        with open(input_path, "wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                # Second gate: magic-byte content sniff on the first chunk.
                # Defends against a renamed non-audio file slipping past the
                # extension check (e.g. evil.exe -> evil.mp3).
                if first_chunk:
                    first_chunk = False
                    if not is_recognized_audio(chunk[:64]):
                        bad_content = True
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
        # Log the real cause server-side; do NOT echo the exception (it can
        # carry filesystem paths / internals) back to the client.
        logger.exception("[%s] Failed to save upload", request_id)
        raise HTTPException(status_code=500, detail="Failed to save the uploaded file.") from e

    if bad_content:
        input_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=415,
            detail="File content is not a recognized audio format.",
        )

    if exceeded:
        input_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=(f"Upload exceeded the limit of {MAX_UPLOAD_BYTES} bytes " "mid-stream."),
        )

    return await anyio.to_thread.run_sync(
        _run_pipeline, request_id, io, expected_speakers, fact_check, limiter=admission.slots
    )


def _run_pipeline(request_id: str, io: JobIO, expected_speakers: int | None, fact_check: bool | None) -> dict:
    """The blocking part of a request: the pipeline, then fact-check enrichment.

    Runs in a worker thread, under the worker's admission limiter.
    """
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
        # not server malfunction), so surface it as 422; audio over the length
        # limit is 413. Other failures stay at 400 — malformed file the user
        # can fix by re-encoding.
        warnings = result.get("warnings", [])
        if "AUDIO_NORMALIZATION_TIMEOUT" in warnings:
            status_code = 422
        elif "AUDIO_TOO_LONG" in warnings:
            status_code = 413
        else:
            status_code = 400
        raise HTTPException(
            status_code=status_code,
            detail={
                "request_id": request_id,
                "warnings": warnings,
                "pipeline_meta": result.get("pipeline_meta", {}),
            },
        )

    # Auto-enrich with rule-based fact verification (Sprint 5). Opt-in: this
    # transmits transcript-derived claim subjects to external providers.
    if factcheck_requested(fact_check):
        _auto_run_factcheck(request_id, result)
    else:
        result["fact_checks_v2"] = dict(DISABLED_STUB)

    return result
