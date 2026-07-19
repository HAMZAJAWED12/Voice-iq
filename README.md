# VoiceIQ — Audio Conversation Intelligence Service

**FastAPI service that turns a raw call recording into structured, explainable intelligence** — transcript, speakers, sentiment, topics, summaries, timelines, escalation signals, live fact-checks and action recommendations — plus an auto-generated PDF report.

```
Audio Upload → Normalize (ffmpeg) → Quality Guardrails → Whisper ASR
            → pyannote Diarization → Speaker/Word Alignment → NLP Enrichment
            → Insight Engines → Fact-Check → Agent Brain → PDF + JSON Response
```

Built as the Python audio-AI service of the VoiceIQ platform (an internship project at beepeeO; a separate Java action layer consumes this service's HMAC-signed callbacks).

## What it does

- **Speech-to-text:** OpenAI Whisper (`tiny`→`large`, default `base`), CPU/GPU, cached model load.
- **Speaker diarization:** `pyannote/speaker-diarization` with speaker cap, smoothing and a fail-soft fallback — if diarization is unavailable the pipeline still returns transcript + NLP with explicit warnings.
- **Alignment & conversation building:** Whisper words mapped to diarization turns, overlap marked explicitly, CUSTOMER/AGENT roles inferred.
- **NLP enrichment (Hugging Face Transformers):**
  - Sentiment — `cardiffnlp/twitter-roberta-base-sentiment-latest`
  - Zero-shot topics — `facebook/bart-large-mnli`
  - Summaries — `sshleifer/distilbart-cnn-12-6`
  - Keywords — `sentence-transformers/all-MiniLM-L6-v2` + spaCy
  - Gender/emotion — transparent heuristics (documented as such, skipped under low SNR)
- **Insight Service:** nine explainable engines (signal aggregation, rules, timeline, scoring, summary, escalation, inconsistency, analytics, normalization/validation) — every output carries `reason` and `evidence`; all scores clamped to `[0, 1]`.
- **Live fact-checking:** claim detection → classification → verification against five external sources (exchangerate.host, CoinGecko, OpenWeather, Alpha Vantage, Wikipedia REST) with TTL caching and SQLite persistence.
- **Agent Brain:** five rule-based recommendation agents (Task / Follow-Up / Email Draft / Escalation / Fact-Check Review) with confidence refinement, dedup and ranking — per-agent fault isolation, so one agent failing never drops the others.
- **PDF report:** summary, speaker stats, emotion overview, intents, flags, fact-checks, transcript excerpt and quality warnings, returned as base64.

## Engineering highlights

- **Fail-soft by design.** Exactly four hard-fail gates (missing audio, normalization timeout/failure, silent input); every other stage failure degrades gracefully with a warning code instead of crashing the run. Per-stage timings are recorded in `meta.timings_ms`.
- **Pydantic v2 contracts at every boundary** — strict, validated schemas throughout.
- **Test suite: 503 tests** (452 lightweight + 51 orchestrator harness with `orchestrator.py` at 100% coverage). The light lane runs in seconds without any ML dependency.
- **CI:** GitHub Actions — ruff, mypy (hard gate), gitleaks, pytest on Python 3.10 & 3.11; heavy ML tests isolated in their own job to keep CI ~30 s.
- **Security tier:** X-API-Key auth (production refuses to boot without keys), payload-size caps, magic-byte upload sniffing (415 on fakes), ffmpeg subprocess timeout, HMAC-SHA256-signed callbacks.
- **Ops:** multi-stage non-root Dockerfile with healthcheck + docker-compose (lightweight insight-only image on port 8888).

## API surface (summary)

| Endpoint | Purpose |
|---|---|
| `POST /v1/process-audio` | Full pipeline: audio in → insights + PDF out |
| `POST /v1/fact-check` / `GET /v1/fact-check/{id}` | Stand-alone claim verification |
| `/v1/insights/...` | Insight-only routes (analytics, timeline, scores, summaries) |
| `POST /internal/v1/agent-brain/recommendations/generate` | Action recommendations |
| `GET /healthz`, `GET /version` | Ops |

## Quick start

**Lightweight (insight service only — no ML downloads):**

```bash
pip install -r requirements-insight.txt
uvicorn app.insight_main:app --port 8888
# or: docker compose up --build
```

**Full pipeline (ASR + diarization + NLP):**

```bash
# prerequisites: Python 3.10+, ffmpeg on PATH
pip install -r requirements.txt
export PYANNOTE_AUTH_TOKEN=hf_...   # Hugging Face token for pyannote
uvicorn app.main:app --port 8000

curl -X POST http://127.0.0.1:8000/v1/process-audio -H "X-API-Key: <key>" -F "file=@samples/your_audio.wav"
```

**Tests:**

```bash
pytest app/insights/tests/ app/agent_brain/tests/
```

## Repository structure

```
app/
├── main.py / insight_main.py   # full vs lightweight entrypoints
├── pipeline/                   # orchestrator (7 disk-backed stages, per-stage timings)
├── services/                   # ASR, diarization, alignment, sentiment, topics, ...
├── insights/                   # 9 insight engines + models + API + SQLite repository
├── agent_brain/                # 5 rule-based agents + extraction + HMAC callback client
├── routes/ · utils/
DOCS/                           # full handoff documentation set
.github/workflows/test.yml      # CI (light + heavy jobs)
Dockerfile · docker-compose.yml
```

## Honest limitations

- Emotion, intent and gender are **heuristics**, not trained models — labelled as such in the output.
- No published accuracy metrics; the evaluator ships proxy metrics (latency, coverage, distributions). WER/DER evaluation requires supplying ground-truth data.
- Diarization degrades with heavy overlap or noise; warnings make this visible instead of hiding it.
- Results are decision support, not ground truth.

## License

MIT — see [LICENSE](LICENSE).
