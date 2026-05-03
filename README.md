# VoiceIQ-AI
**Audio Conversation Intelligence Platform (ASR · Diarization · NLP · PDF Reporting)**

---

## 1. Overview

**VoiceIQ-AI** is a FastAPI-based audio intelligence system that processes conversational audio and produces structured insights, analytics, and a professional PDF report.

The system performs:

- Automatic Speech Recognition (ASR) using **OpenAI Whisper**
- Speaker diarization using **pyannote.audio** (with fail-soft fallback)
- Speaker–text alignment with overlap handling
- NLP analytics (sentiment, keywords, topic, summary, intents, flags, fact checks)
- Audio-quality guardrails (silence, SNR, noise detection)
- A comprehensive **PDF report** returned as Base64
- Development-only evaluation utilities

The architecture is designed to be **robust, fail-soft, and production-safe**, while exposing clear warnings when results may be unreliable.

---

## 2. Core Features

### 2.1 Speech-to-Text (ASR)
- Powered by Whisper (`tiny` → `large`, default: `base`)
- Produces transcript + time-aligned segments
- CPU and GPU supported
- Language auto-detection or manual override

### 2.2 Speaker Diarization
- Uses `pyannote/speaker-diarization`
- Supports:
  - Expected speaker count (optional)
  - Maximum speaker cap (prevents noisy over-segmentation)
  - Segment smoothing and merging
- **Fail-soft behavior**:
  - If diarization fails or token missing, system falls back safely
  - Transcript + NLP still returned
  - Clear warnings included

### 2.3 Alignment & Conversation Building
- Aligns Whisper words to diarization windows
- Generates clean `speaker_segments`
- Builds higher-level conversation turns
- Handles overlapping speech with explicit `overlap=true` marking
- Assigns inferred roles (CUSTOMER / AGENT) when applicable

### 2.4 Audio Guardrails
Before analysis, audio is evaluated for quality:

- Silence / near-silence detection
- RMS and peak level analysis
- Heuristic Signal-to-Noise Ratio (SNR)
- Heavy-noise detection

Guardrail behavior:
- Silent or near-silent audio is rejected
- Low SNR triggers warnings and reduced confidence
- Certain analyses (gender/emotion) are skipped if unreliable

### 2.5 NLP Analytics
- Sentiment (segment-level)
- Keyword extraction
- Topic classification
- Summarization
- Intent classification
- Behavioral flags (hesitation, aggression, risk indicators)
- Fact-check candidate extraction

### 2.6 PDF Reporting
- Auto-generated professional PDF
- Includes:
  - Summary and topic
  - Conversation and speaker statistics
  - Emotion overview
  - Intent distribution
  - Flags and fact-checks
  - Transcript excerpt
  - Audio-quality warnings
- Returned as Base64 in API response

---

## 3. High-Level Pipeline

Audio Upload
↓
Normalize to WAV (16 kHz, mono)
↓
Audio Quality Analysis (guardrails)
↓
Whisper ASR
↓
Speaker Diarization (fail-soft)
↓
Alignment & Conversation Builder
↓
NLP Analytics
↓
PDF Generation
↓
JSON Response


---

## 4. Repository Structure

voiceiq-AI/
├─ app/
│ ├─ main.py
│ ├─ routes/
│ │ └─ process_audio.py
│ ├─ services/
│ │ ├─ asr_service.py
│ │ ├─ diarization_service.py
│ │ ├─ alignment_service.py
│ │ ├─ sentiment_service.py
│ │ ├─ keyword_service.py
│ │ ├─ topic_service.py
│ │ ├─ summary_service.py
│ │ ├─ emotion_service.py
│ │ ├─ intent_service.py
│ │ ├─ factcheck_service.py
│ │ ├─ flag_service.py
│ │ └─ pdf_service.py
│ ├─ utils/
│ │ ├─ audio_utils.py
│ │ ├─ audio_quality.py
│ │ └─ logger.py
│ └─ evaluation/
│ └─ evaluator.py
├─ run_eval_dev.py
├─ requirements.txt
└─ README.md


---

## 5. Requirements

### 5.1 System
- Python **3.10+**
- FFmpeg installed and accessible via PATH

Verify FFmpeg:

```bash
ffmpeg -version

5.2 Python Dependencies (Core)
fastapi, uvicorn
whisper
torch, torchaudio
transformers
sentence-transformers
spacy
soundfile
numpy
fpdf
huggingface_hub
5.3 Optional (Diarization)
pyannote.audio
Hugging Face access token (required for diarization models)

6. Hugging Face Token (Required for Pyannote)
```

7. Running the API
7.1 Start Server
uvicorn app.main:app --reload --port 8000

7.2 Endpoint

POST /v1/process-audio

Upload an audio file and receive analysis + PDF.

Example:

curl -X POST http://127.0.0.1:8000/v1/process-audio \
  -F "file=@data/DIALOGUE.mp3"

8. API Response (Summary)

The response includes:

transcript

asr_meta

diarization segments

speaker_segments

conversation (with intents)

speaker_stats

conversation_stats

topic and summary

flags and fact-checks

emotion overview

warnings

single_speaker_mode flag

audio_quality report

report_pdf_base64

Warnings explicitly communicate degraded conditions.

9. Guardrails & Fail-Soft Design
Input Sanity

Unsupported formats rejected

Silent / near-silent audio rejected

Noise Handling

Low SNR triggers warnings

Diarization and audio-based inferences become conservative

Single-Speaker Mode

Activated when:

Only one speaker is detected

Diarization fails

Speaker changes are negligible

In this mode:

Dominance/interruptions insights are limited

Transcript and NLP analytics remain available

Fail-Soft Philosophy

If any subsystem fails:

The pipeline continues

Partial results are returned

Clear warnings explain limitations

10. Development Evaluation (Dev-Only)

The repository includes a development-only evaluation runner.

Run:

python run_eval_dev.py


Current evaluation provides:

End-to-end latency

Proxy metrics (coverage, compression ratio, distributions)

The evaluation module can be extended to support:

WER / CER (ASR)

DER (diarization)

Precision / Recall / F1 (NLP components)

Dependencies often required:

pip install jiwer rouge-score absl-py nltk

11. Known Limitations

Diarization accuracy degrades with heavy overlap and noise

Emotion and gender inference are heuristic and skipped under low SNR

Evaluation metrics are proxy-based unless ground truth is supplied

12. Roadmap

GPU optimization and batching

Async processing / background jobs

Authentication and rate limiting

Persistent storage for transcripts and reports

Full evaluation datasets with ground truth

Scalable deployment (Docker, Kubernetes)

13. Disclaimer

This system provides automated analytical assistance.
Results should not be treated as ground truth, especially under noisy or adversarial audio conditions.

Warnings are included to ensure transparency and responsible use.
