# 01 — System Overview

## What the Insight Service is

The **VoiceIQ Insight Service** is the conversational-intelligence layer of the larger VoiceIQ-AI platform. It consumes already-processed conversational data — speaker-attributed utterances with optional sentiment and emotion vectors — and returns a structured, scored, explainable assessment of the conversation. Outputs include session-level sentiment and emotion aggregates, per-speaker insights, a chronological timeline of behavioural markers, five normalised scores (dominance, engagement, conflict, cooperation, emotion volatility), explainable flags, an escalation assessment, an inconsistency assessment, and a written summary suite.

The service is a FastAPI application backed by SQLAlchemy/SQLite. It runs standalone (via `app.insight_main:app`) without any of the heavy ML stack, or as part of the full VoiceIQ pipeline (via `app.main:app`) which additionally mounts the `/v1/process-audio` route.

## Where it sits in the pipeline

The full VoiceIQ-AI pipeline is:

```
Audio Upload
    → ASR (Whisper)
    → Speaker Diarization (pyannote.audio, fail-soft)
    → Alignment & Conversation Build
    → Audio Quality / Metadata
    → NLP Enrichment (sentiment, emotion, intent, keywords, etc.)
    → INSIGHT SERVICE  ← this layer
    → PDF Reporting
    → JSON / API Response
```

The Insight Service consumes the structured output of the NLP enrichment stage. It does **not** call any model itself. It does **not** parse audio. It is a deterministic, rule-driven analytics layer designed to be fast, predictable, explainable, and safe to run on every session without GPU resources.

## Design principles (enforced across every engine)

These principles are applied uniformly across `app/insights/core/*`. They are visible in the code style (every detector returns explicit `(signal, windows)` pairs, every score is clamped, every output carries a `reason` and `evidence`) and they are part of the project instructions for any future engine added to the system.

1. **Clean architecture, one engine per file.** Every capability lives in `app/insights/core/<feature>_engine.py` with its Pydantic models in `app/insights/models/<feature>_models.py`. Engines do not call each other through hidden side effects; they receive their inputs as explicit arguments and return Pydantic objects.
2. **Production safety / fail-soft.** No engine raises on missing data. Empty utterance sentiment, empty emotion vectors, empty timelines, single-speaker sessions, and missing duration are all handled with safe defaults. The validator and `InsightService` layer convert exceptions into structured `ValidationIssue` objects so the API never returns an unhelpful 500.
3. **Pydantic-strict typing.** Every model uses Pydantic v2 with explicit field types and bounds (`Field(ge=0.0, le=1.0)` is used liberally, e.g. on every score and probability). Model validators enforce invariants like "end ≥ start" and "session must have ≥ 1 utterance" at construction time.
4. **Explainability.** Every signal, every flag, every timeline marker, every escalation/inconsistency window includes both a human-readable `reason` and a structured `evidence` dict. There are no black-box numeric outputs without a textual rationale and the data points that produced them.
5. **Score discipline.** Every score is in `[0.0, 1.0]`. Aggregating scores always uses `_clamp` (or the equivalent `clamp01`). Per-detector caps (`_CAP_*` in escalation and inconsistency engines) prevent any single detector from saturating the aggregate.
6. **Timeline integrity.** Marker thresholds are tuned to avoid spurious noise. The timeline engine specifically applies a `minimum_shift_delta` (0.06) on emotional-shift detection and gates pause-marker emission on configurable thresholds. The rule engine sorts the timeline after appending inconsistency-derived markers so consumers always see a strictly chronological view.
7. **No hidden dependencies on upstream.** The Insight Service does not modify any field on its input. It does not log raw transcript text. It treats the upstream NLP signals as authoritative and only derives new signals from them.

## What the Insight Service does NOT do

It does not run ASR. It does not run diarization. It does not perform sentiment classification — it consumes the labels produced upstream. It does not generate audio or transcripts. It does not currently emit PDF reports (the PDF service lives elsewhere in the repository under the audio pipeline). It does not maintain user accounts, authentication, or rate limiting; these are out of scope and intentionally absent from the current build.
