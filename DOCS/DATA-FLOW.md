# Data Flow — what leaves the trust boundary

Status: current as of wave S-D (S3); model-download, legacy fact-check and
callback-content facts re-verified against the code 2026-10-11; currency
provider changed to open.er-api.com 2026-10-17. Companion to
[SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md).

This document enumerates every point where VoiceIQ transmits data to a
third party. Everything **not** listed here stays on the host: audio,
transcripts, diarization, alignment, NLP enrichment, insights, the PDF and
the SQLite database are all processed and stored locally.

---

## Summary

| Flow | Trigger | Destination | Default |
|---|---|---|---|
| Fact-check verification | `/v1/fact-check`, or `/v1/process-audio` **only when enrichment is requested** | 5 public APIs (below) | **opt-in (off)** |
| Java Action Layer callback | only the opt-in `/v1/process-audio` Agent Brain stage (`VOICEIQ_AGENT_BRAIN_AUTO_RUN=true`); the internal Agent Brain route never calls out | your own Java service | off unless auto-run + URL + secret set |
| Model weights + Hugging Face checks | first load of an ML service in each process (full pipeline only) | openaipublic.azureedge.net (Whisper), huggingface.co (pyannote, Transformers, sentence-transformers) | on (weights cached; see §3) |

Nothing else egresses. No telemetry, no analytics, no error reporting SaaS.

---

## 1. Fact-check → external providers  (opt-in)

### What is sent

The fact-check engine detects claims in a transcript and extracts a small
**claim subject** per claim. Only those extracted fields are transmitted —
never the transcript, never the audio, never speaker identities.

| Client | Host | Transmitted | Needs API key |
|---|---|---|---|
| `ForexClient` | `open.er-api.com` | the **base** currency code only, in the URL path (e.g. `USD`); the quote currency is picked out of the reply on the host | no — see note |
| `CoinGeckoClient` | `api.coingecko.com` | a mapped coin id (e.g. `bitcoin`) from `subject.asset` | no |
| `OpenWeatherClient` | `api.openweathermap.org` | `city` name + units + your `appid` | **yes** — `VOICEIQ_OPENWEATHER_API_KEY` |
| `StockClient` | `www.alphavantage.co` | ticker `symbol` + your `apikey` | **yes** — `VOICEIQ_ALPHAVANTAGE_API_KEY` |
| `StaticFactsClient` | `en.wikipedia.org` | `country` name, percent-encoded in the URL path | no |

A client without its required key is skipped (verdict `SOURCE_UNAVAILABLE`) —
it makes no request at all.

**`ForexClient` (provider decided 2026-10-17).** ExchangeRate-API's open
access endpoint needs no key and allows commercial use. One request returns
the base currency's rate against every other currency, so the quote currency
never leaves the host. Rates change once a day; the client asks for each base
currency at most once an hour (an in-process cache), so many claims about one
currency send one request per hour. The provider's terms require
attribution: every evidence record from this client carries "Rates By
Exchange Rate API (https://www.exchangerate-api.com)" in
`evidence.raw.attribution`. Switching provider or adding a key changes this
table in the same change.

### Privacy characteristics

- Claim subjects are short factual tokens (`USD`, `bitcoin`, `Paris`,
  `AAPL`, `France`), but they are **derived from the call's content**, so a
  provider can infer what a conversation touched on.
- Requests carry an identifying User-Agent
  (`VoiceIQ-FactCheck/1.0 (+<repo url>) httpx`), no auth cookies, and no
  session/speaker identifiers.
- Hosts are fixed constants; query values go through httpx `params=`
  (auto-encoded), the two path values are percent-encoded (Wikipedia) or
  must be three capital letters (the forex base currency), and redirects are
  disabled, so a transcript cannot redirect a request elsewhere.
- Request URLs — which carry the claim subject and, for OpenWeather and
  Alpha Vantage, your API key — are not written to the logs:
  `app/utils/logger.py` pins the `httpx` and `httpcore` loggers to WARNING.

### Controls

| Surface | Behaviour |
|---|---|
| `POST /v1/process-audio` | **Off by default.** Runs only with `?fact_check=true`, or globally via `VOICEIQ_FACTCHECK_AUTO_ENRICH=true`. When skipped, the response carries `fact_checks_v2: {"status": "disabled", ...}` and **zero external calls are made**. |
| `POST /v1/fact-check` | Explicit endpoint — calling it *is* the consent. Rate-limited (default `60/minute`). |
| Per-request override | `?fact_check=false` forces it off even when the global setting is on. |

> The legacy top-level `fact_checks` key produced by the orchestrator's own
> `FactCheckService` is a **separate, older** path from the Sprint-5
> `fact_checks_v2` engine. It is **not** behind the opt-in gate — it runs on
> every pipeline run — but it is a local regex scan (URLs and numbers marked
> `TO_VERIFY`) that makes **no network call**, so nothing leaves the host.
> The opt-in gate above governs `fact_checks_v2` only.

---

## 2. Java Action Layer callback  (off unless configured)

Agent Brain can POST its recommendations to your Java service.

- **Destination:** whatever `AgentBrainSettings.callback_url` is set to —
  your infrastructure, not a third party.
- **Sent:** the `CallbackPayload` (session id, generated recommendations).
  Each recommendation carries its evidence span — the verbatim transcript
  segment text, speaker id/label and timestamps — plus extracted entities
  (assignee, deadline, customer name, topic). Treat the payload as call
  content, not metadata.
- **Trigger:** the internal Agent Brain route never calls out; the callback
  fires from the `/v1/process-audio` Agent Brain stage
  (`VOICEIQ_AGENT_BRAIN_AUTO_RUN=true`) when URL and secret are set.
- **Integrity:** HMAC-SHA256 over the body using `callback_secret`, in the
  `X-VoiceIQ-Signature` header.
- **Disabled by default:** with no URL *and* secret configured, `send()` is a
  no-op that returns `False`.

---

## 3. Model downloads  (full pipeline only)

Applies to `app.main` (`/v1/process-audio`); the insight-only image makes
none of these calls. **No call data is involved** — this is dependency
fetching.

| Service | Source | Notes |
|---|---|---|
| Whisper (`openai-whisper`) | `openaipublic.azureedge.net` | Downloaded once to `~/.cache/whisper`, then verified locally by SHA-256; no network while the cached file is intact. |
| Sentiment, topic, summary (Transformers), keywords (sentence-transformers) | `huggingface.co` | Weights cached after the first download, but the Hugging Face client still makes a metadata request on each process's first load unless `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` is set (nothing in this repo sets them). |
| pyannote diarization | `huggingface.co` | Needs `PYANNOTE_AUTH_TOKEN` (fallback `HUGGINGFACE_TOKEN`). Every cold load calls `huggingface_hub.login()`, a network call that sends the token and writes it to the local Hugging Face cache. If that call fails (offline, or offline mode set) diarization silently falls back to a single-speaker mock. |
| spaCy `en_core_web_sm` | — | Never fetched at runtime; it must be installed separately (`python -m spacy download en_core_web_sm`). Without it the keyword stage fails (`KEYWORDS_FAILED`), and so does sentiment (`SENTIMENT_FAILED`), because `SentimentService.analyze_speaker_segments` runs keyword extraction on every segment. |

Air-gapped deployments: pre-populating the caches covers Whisper and the
Transformers models (with the offline variables set), but **not** pyannote
diarization as written, because of the unconditional `login()` call.

---

## Data retention (local)

Per-request artifacts under `data/jobs/<uuid>/` — raw audio, transcript,
`whisper.json`, PDF, insights — are purged after `job_retention_hours`
(default **24h**, `0` disables). See the retention note in the hardening
plan. Fact-check results and insight records persist in SQLite until
removed by the operator.
