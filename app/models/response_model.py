from pydantic import BaseModel, Field

# -------------------------
# ASR Models
# -------------------------


class ASRSegment(BaseModel):
    id: int | None
    start: float | None
    end: float | None
    text: str | None
    tokens: list[int] | None
    temperature: float | None
    avg_logprob: float | None
    compression_ratio: float | None
    no_speech_prob: float | None


class ASRMeta(BaseModel):
    model: str | None
    language: str | None
    duration: float | None
    segments: list[ASRSegment] | None


# -------------------------
# Diarization + Alignment Models
# -------------------------


class DiarizationSegment(BaseModel):
    start: float
    end: float
    speaker: str


class SpeakerSegment(BaseModel):
    """
    Speaker segments after alignment with ASR.
    Each segment combines timing, speaker ID, and transcribed text.
    """

    start: float = Field(..., description="Segment start time in seconds")
    end: float = Field(..., description="Segment end time in seconds")
    speaker: str = Field(..., description="Speaker label, e.g., SPEAKER_00")
    text: str | None = Field(None, description="Text spoken by this speaker during this segment")


# -------------------------
# Final API Response
# -------------------------


class ProcessAudioResponse(BaseModel):
    """
    Unified API response model for /v1/process-audio endpoint.
    Includes raw ASR, diarization, and aligned speaker transcript.
    """

    request_id: str
    transcript: str
    asr_meta: ASRMeta
    segments: list[DiarizationSegment]  # raw diarization output
    speaker_segments: list[SpeakerSegment] | None  # aligned speaker transcript

    class Config:
        schema_extra = {
            "example": {
                "request_id": "a12b34c5-6789-0def-1234-56789abcdef0",
                "transcript": "Hello, this is a sample transcript from Whisper.",
                "asr_meta": {
                    "model": "base",
                    "language": "en",
                    "duration": 123.45,
                    "segments": [
                        {
                            "id": 0,
                            "start": 0.0,
                            "end": 4.2,
                            "text": "Hello world",
                            "tokens": [50364, 123, 456],
                            "temperature": 0.0,
                            "avg_logprob": -0.25,
                            "compression_ratio": 1.2,
                            "no_speech_prob": 0.02,
                        }
                    ],
                },
                "segments": [
                    {"start": 0.0, "end": 4.2, "speaker": "SPEAKER_00"},
                    {"start": 4.3, "end": 9.6, "speaker": "SPEAKER_01"},
                ],
                "speaker_segments": [
                    {"start": 0.0, "end": 4.2, "speaker": "SPEAKER_00", "text": "Hello world."},
                    {"start": 4.3, "end": 9.6, "speaker": "SPEAKER_01", "text": "Hi, how are you?"},
                ],
            }
        }
