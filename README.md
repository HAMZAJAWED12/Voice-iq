VoiceIQ-AI

Modular Audio Conversation Intelligence Platform (ASR · Diarization · NLP · Guardrails · PDF Reporting)

```1. Overview

VoiceIQ-AI is a modular FastAPI-based audio intelligence system that transforms conversational audio into structured analytics and a professional PDF report.

The system performs:
🎙 Automatic Speech Recognition (Whisper)
👥 Speaker diarization (pyannote with fail-soft fallback)
🔗 Speaker-text alignment with overlap detection
🧠 NLP analytics (sentiment, keywords, topic, summary, intents, flags, fact checks)
🛡 Audio-quality guardrails (silence, SNR, noise detection)
📄 Professional PDF reporting (Base64 encoded)
🧪 Development evaluation utilities
The architecture is modular, independent, fail-soft, and production-oriented.
Each service:
Can run independently
Saves its output before triggering the next stage
Fails gracefully without breaking the full pipeline
```

```2. Core Capabilities
2.1 Automatic Speech Recognition (ASR)

Powered by OpenAI Whisper

Default model: base

Supports CPU and GPU

Produces:
Full transcript
Time-aligned segments
Confidence metadata
2.2 Speaker Diarization
Uses pyannote/speaker-diarization

Supports:

Expected speaker count (optional)
Maximum speaker cap (default: 8)
Segment smoothing
Confidence heuristics
Fail-Soft Behavior
If diarization fails:
Transcript is still returned
NLP analysis continues
warnings[] field explains fallback
single_speaker_mode is activated

2.3 Alignment & Conversation Builder

Maps ASR word timing to diarization windows
Builds clean speaker_segments
Merges small fragments
Handles overlapping speech with overlap=true

Assigns inferred roles:

CUSTOMER
AGENT

2.4 Audio Guardrails (Production Safety Layer)

Before analysis begins, audio is evaluated:
Silence detection
Near-silence detection
RMS and peak levels
Signal-to-Noise Ratio (SNR)
Heavy noise detection

Guardrail Logic
Condition	Behavior
Silent / Near Silent	Request rejected
Low SNR	Warning issued
Heavy Noise	Conservative diarization
Low SNR	Gender/emotion skipped

All degradations are explicitly surfaced via:

"warnings": [...]

2.5 NLP Analytics

Sentiment analysis (segment-level)

Keyword extraction (spaCy)

Topic classification (zero-shot BART)

Summarization (DistilBART)

Intent detection
Behavioral flags:
Hesitation
Aggression
Risk indicators
Fact-check candidate extraction

2.6 PDF Report Generation
Professional PDF generated automatically including:

Topic & summary
Conversation statistics
Speaker statistics
Emotion overview
Intent distribution
Flags
Fact-checks
Audio quality report
Guardrail warnings
Transcript excerpt
Returned as:
"report_pdf_base64": "..."
```

```3. High-Level Pipeline
Audio Upload
    ↓
Normalize (16kHz mono WAV)
    ↓
Audio Quality Analysis (Guardrails)
    ↓
ASR (Whisper)
    ↓
Diarization (Fail-Soft)
    ↓
Alignment & Conversation Builder
    ↓
NLP Analytics
    ↓
PDF Generation
    ↓
Structured JSON Response


Each stage:

Saves its output

Validates input availability

Raises warnings instead of crashing
```

```4. Repository Structure
voiceiq-AI/
│
├── app/
│   ├── main.py
│   │
│   ├── routes/
│   │   └── process_audio.py
│   │
│   ├── services/
│   │   ├── asr_service.py
│   │   ├── diarization_service.py
│   │   ├── alignment_service.py
│   │   ├── sentiment_service.py
│   │   ├── keyword_service.py
│   │   ├── topic_service.py
│   │   ├── summary_service.py
│   │   ├── emotion_service.py
│   │   ├── intent_service.py
│   │   ├── factcheck_service.py
│   │   ├── flag_service.py
│   │   └── pdf_service.py
│   │
│   ├── utils/
│   │   ├── audio_utils.py
│   │   ├── audio_quality.py
│   │   └── logger.py
│   │
│   └── evaluation/
│       └── evaluator.py
│
├── data/
├── tests/
├── run_eval_dev.py
├── requirements.txt
├── requirements-dev.txt
├── LICENSE
└── README.md
```

```5. Installation
5.1 System Requirements

Python 3.10+

FFmpeg installed and available in PATH

Verify:

ffmpeg -version

5.2 Install Dependencies
pip install -r requirements.txt


Optional development:

pip install -r requirements-dev.txt

5.3 Hugging Face Token (Required for Diarization)
export HUGGINGFACE_TOKEN=your_token_here


Or on Windows:

setx HUGGINGFACE_TOKEN "your_token_here"
```

```6. Running the API

Start server:

uvicorn app.main:app --reload --port 8000


Endpoint:

POST /v1/process-audio


Example:

curl -X POST http://127.0.0.1:8000/v1/process-audio \
  -F "file=@data/DIALOGUE.mp3"
```
```7. API Response Structure

Includes:

transcript
asr_meta
segments (diarization)
speaker_segments
conversation
speaker_stats
conversation_stats
topic
summary
emotion_overview
flags
fact_checks
intents_summary
timeline
warnings
single_speaker_mode
audio_quality
report_pdf_base64
```
```8. Guardrails & Fail-Soft Philosophy

Input Validation
Unsupported formats rejected
Silent/near-silent audio rejected
Noise Handling
Low SNR triggers warnings
Audio-sensitive features skipped
Single-Speaker Mode

Activated when:
Only one speaker detected
Diarization fails
Speaker changes negligible

In this mode:
Dominance/interruptions insights limited
Transcript and NLP still available
Fail-Soft Design
If any subsystem fails:
Pipeline continues
Partial results returned
Clear warnings included
```
```9. Development Evaluation

Run:

python run_eval_dev.py

Current metrics:

End-to-end latency
Proxy alignment coverage
Compression ratio
Sentiment distribution
Topic confidence

Extendable to:
WER / CER (jiwer)
DER (diarization error rate)
ROUGE (summary quality)
Precision / Recall / F1
Dev dependencies:
pip install jiwer rouge-score absl-py nltk
```
```10. Production Considerations

To reach production level:
GPU acceleration for diarization
Background job queue (Celery / Redis)
Model caching across workers
Async processing
Rate limiting & authentication
Persistent storage (S3 / DB)
Docker containerization
Kubernetes scaling
Monitoring & metrics (Prometheus)
```
```11. Known Limitations

Diarization degrades in heavy overlap/noise
Emotion & gender inference are heuristic
Evaluation metrics are proxy-based unless ground truth provided
CPU diarization can be slow
```
```12. Roadmap

Speaker embedding clustering improvements

Faster diarization backend
Streaming support
Real-time processing
Full ground-truth evaluation suite
SaaS deployment
```
```13. Disclaimer

This system provides automated analytical assistance.
Results should not be treated as legal, forensic, or clinical ground truth — especially under noisy or adversarial conditions.
Guardrail warnings are included to ensure transparency and responsible use.
```
