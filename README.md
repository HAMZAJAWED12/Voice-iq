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
- **Agent Brain:** five rule-based recommendation agents (Task / Follow-Up / Email Draft / Escalation / Fact-Check Review) with confidence refinement, dedup and ranking — per-agent fault isolation, so one agent failing never drops the others. Reachable two ways: a pull route the Java layer calls with its own context, or as an opt-in pipeline stage that runs over VoiceIQ's own output and can push HMAC-signed results back to Java.
- **Language-aware extraction:** every extractor selects a vocabulary table from the language Whisper detected, falling back to English for anything unmapped. English is populated today; adding Urdu or Arabic is a data change, not a code change.
- **PDF report:** summary, speaker stats, emotion overview, intents, flags, fact-checks, transcript excerpt and quality warnings, returned as base64.

## Engineering highlights

- **Fail-soft by design.** Exactly four hard-fail gates (missing audio, normalization timeout/failure, silent input); every other stage failure degrades gracefully with a warning code instead of crashing the run. Per-stage timings are recorded in `meta.timings_ms`.
- **Pydantic v2 contracts at every boundary** — strict, validated schemas throughout.
- **Test suite: 795 tests**, one lane. The orchestrator's ML imports are lazy, so the whole suite — harness included, with `orchestrator.py` at 100% coverage — runs on the lightweight requirements in seconds, with no model artifact and no network. A service counts as heavy if it imports an ML library *or* loads a model artifact; heavy means mocked.
- **CI:** GitHub Actions — ruff, mypy (hard gate), gitleaks, pytest on Python 3.10 & 3.11, ~30 s.
- **Security tier:** X-API-Key auth (production refuses to boot without keys), per-key rate limiting, payload-size caps, magic-byte upload sniffing (415 on fakes), ffmpeg subprocess timeout, job-artifact TTL, docs hidden in production, and HMAC-SHA256-signed callbacks with a timestamp-bound v2 signature for replay resistance.
- **Egress is opt-in and documented.** Fact-checking is off by default because claim subjects leave the host; every external destination is enumerated in [`DOCS/DATA-FLOW.md`](DOCS/DATA-FLOW.md). With it off, zero outbound calls are made.
- **Performance work is measured, not asserted.** Speaker alignment was ~27× faster after bounding two quadratic scans, output verified byte-identical across eight fixture shapes; the Agent Brain's duplicate sweep went 5.0 s → 0.15 s on a 16-minute call. Both are pinned by call-count assertions rather than wall-clock, because a constant-size output makes wall-clock a misleading signal.
- **Ops:** multi-stage non-root Dockerfile with healthcheck + docker-compose (lightweight insight-only image on port 8888).

## API surface (summary)

| Endpoint | Purpose |
|---|---|
| `POST /v1/process-audio` | Full pipeline: audio in → insights + PDF out. `?fact_check=true` opts into external verification; `VOICEIQ_AGENT_BRAIN_AUTO_RUN=true` adds a `recommendations` key |
| `POST /v1/fact-check` / `GET /v1/fact-check/{id}` | Stand-alone claim verification |
| `/v1/insights/...` | Insight-only routes (analytics, timeline, scores, summaries) |
| `POST /internal/v1/agent-brain/recommendations/generate` | Action recommendations |
| `GET /healthz`, `GET /version` | Ops |

Every response key added since v1 is additive — flags off means the key is absent, not null, so existing consumers see byte-identical output.

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
├── pipeline/                   # orchestrator — run() decomposed into named _run_<stage> methods over a _PipelineState; 4 hard-fail gates, per-stage timings
├── services/                   # ASR, diarization, alignment, sentiment, topics, ...
├── insights/                   # 9 insight engines + models + API + SQLite repository
├── agent_brain/                # 5 rule-based agents + language-keyed extraction + HMAC callback client
├── routes/ · utils/
DOCS/                           # handoff docs, data-flow/egress record, and the planning
                                # + decision registers for in-flight work
.github/workflows/test.yml      # CI (light + heavy jobs)
Dockerfile · docker-compose.yml
```

## Honest limitations

- Emotion, intent and gender are **heuristics**, not trained models — labelled as such in the output.
- No published accuracy metrics; the evaluator ships proxy metrics (latency, coverage, distributions). WER/DER evaluation requires supplying ground-truth data.
- **Fact-checking covers six narrow claim types** (currency rate, commodity price, crypto price, stock price, weather, static fact). A general business call typically contains none of them, so the current engine correctly finds nothing on most real audio. Broadening this to open-ended claims is planned work, not shipped.
- **Extraction vocabulary is English-only today.** The routing that selects a per-language vocabulary is in place and tested; the Urdu and Arabic tables are deliberately empty, because guessing terms in a language you don't speak produces confident, untraceable false positives. Non-English input falls back to English behaviour.
- Diarization degrades with heavy overlap or noise; warnings make this visible instead of hiding it.
- Results are decision support, not ground truth.

## Planned work

In-flight design lives in `DOCS/`, with decisions recorded as they are signed rather than assumed:

| Doc | What it covers |
|---|---|
| [`FACTCHECK-AGENT-PHASE-0.md`](DOCS/FACTCHECK-AGENT-PHASE-0.md) | Open-source-LLM fact-check agent: frozen contracts, compatibility policy, privacy boundary, and a 10-row decision register |
| [`N4B-MULTILINGUAL-STRATEGY.md`](DOCS/N4B-MULTILINGUAL-STRATEGY.md) | Urdu/Arabic support — measured as a vocabulary gap rather than a model gap |
| [`JAVA-CALLBACK-V2.md`](DOCS/JAVA-CALLBACK-V2.md) | Replay-resistant callback signature; the Java side is outstanding |
| [`DATA-FLOW.md`](DOCS/DATA-FLOW.md) | Every point where data leaves the host |

## License

MIT — see [LICENSE](LICENSE).
