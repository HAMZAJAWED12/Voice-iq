# app/routes/process_audio.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from typing import Optional
import uuid
import os

from app.utils.logger import logger
from app.utils.job_io import JobIO
from app.pipeline.orchestrator import VoiceIQOrchestrator

router = APIRouter()


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

    return result