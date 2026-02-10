import io
import wave
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.asr_service import transcribe_local


# Create a test client
client = TestClient(app)


# --------------------------
# Helper: Generate valid silent WAV
# --------------------------
def generate_silent_wav(duration=0.3, sr=16000):
    """
    Generate a short silent WAV file so ffmpeg can process it.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * int(sr * duration))
    buf.seek(0)
    return buf


# --------------------------
# Mock heavy dependencies
# --------------------------
@pytest.fixture(autouse=True)
def patch_services(monkeypatch):
    # Mock ASR
    def mock_transcribe_local(path):
        return "Hello world. How are you?", {
            "model": "base",
            "language": "en",
            "duration": 3.5,
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.5, "text": "Hello world"},
                {"id": 1, "start": 1.6, "end": 3.5, "text": "How are you"},
            ],
        }

    # Mock diarization
    def mock_diarize_audio(path):
        return [
            {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"},
            {"start": 1.6, "end": 3.5, "speaker": "SPEAKER_01"},
        ]

    # Mock alignment
    def mock_align(asr_result, diarization_result):
        return {
            "speaker_segments": [
                {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00", "text": "Hello world"},
                {"start": 1.6, "end": 3.5, "speaker": "SPEAKER_01", "text": "How are you"},
            ]
        }

    # IMPORTANT — patch the imported names inside **process_audio**
    monkeypatch.setattr("app.routes.process_audio.transcribe_local", mock_transcribe_local)
    monkeypatch.setattr("app.routes.process_audio.diarize_audio", mock_diarize_audio)
    monkeypatch.setattr("app.routes.process_audio.align_transcript_with_speakers", mock_align)

    yield


# --------------------------
# Test 1: Process Audio Route
# --------------------------
def test_process_audio_route():
    """
    Verify that the /v1/process-audio route:
      • accepts a WAV file
      • runs all mocked stages
      • returns a coherent response
    """
    dummy_audio = generate_silent_wav()
    files = {"file": ("sample.wav", dummy_audio, "audio/wav")}

    response = client.post("/v1/process-audio", files=files)
    assert response.status_code == 200, response.text

    data = response.json()

    # Validate structure
    assert "request_id" in data
    assert "transcript" in data
    assert "asr_meta" in data
    assert "segments" in data
    assert "speaker_segments" in data

    # Validate diarization + alignment
    assert len(data["speaker_segments"]) == 2
    assert data["speaker_segments"][0]["speaker"] == "SPEAKER_00"
    assert data["speaker_segments"][1]["speaker"] == "SPEAKER_01"

    # Validate ASR meta
    assert data["asr_meta"]["model"] == "base"
    assert data["asr_meta"]["language"] == "en"
    assert abs(data["asr_meta"]["duration"] - 3.5) < 0.1

    print("\n /v1/process-audio route test passed successfully.")


# --------------------------
# Test 2: Response Schema Integrity
# --------------------------
def test_response_schema_integrity():
    """
    Ensure speaker_segments contain start, end, speaker, and text.
    """
    dummy_audio = generate_silent_wav()
    files = {"file": ("check.wav", dummy_audio, "audio/wav")}
    response = client.post("/v1/process-audio", files=files)
    assert response.status_code == 200, response.text
    payload = response.json()

    for seg in payload["speaker_segments"]:
        assert all(k in seg for k in ("start", "end", "speaker", "text"))

    print("\n Response schema verified successfully.")