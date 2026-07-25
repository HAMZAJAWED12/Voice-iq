# Data Flow — what leaves the trust boundary

Status: current as of wave S-D (S3). Companion to
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
| Java Action Layer callback | Agent Brain recommendation, when configured | your own Java service | off unless URL + secret set |
| Hugging Face model download | first run of an ML service | huggingface.co | on (models cached after) |

Nothing else egresses. No telemetry, no analytics, no error reporting SaaS.

---

## 1. Fact-check → external providers  (opt-in)

### What is sent

The fact-check engine detects claims in a transcript and extracts a small
**claim subject** per claim. Only those extracted fields are transmitted —
never the transcript, never the audio, never speaker identities.

| Client | Host | Transmitted | Needs API key |
|---|---|---|---|
| `ForexClient` | `api.exchangerate.host` | currency codes: `base`, `symbols` (e.g. `USD`, `EUR`) | no |
| `CoinGeckoClient` | `api.coingecko.com` | a mapped coin id (e.g. `bitcoin`) from `subject.asset` | no |
| `OpenWeatherClient` | `api.openweathermap.org` | `city` name + units + your `appid` | **yes** — `VOICEIQ_OPENWEATHER_API_KEY` |
| `StockClient` | `www.alphavantage.co` | ticker `symbol` + your `apikey` | **yes** — `VOICEIQ_ALPHAVANTAGE_API_KEY` |
| `StaticFactsClient` | `en.wikipedia.org` | `country` name, percent-encoded in the URL path | no |

A client without its required key is skipped (verdict `SOURCE_UNAVAILABLE`) —
it makes no request at all.

### Privacy characteristics

- Claim subjects are short factual tokens (`USD`, `bitcoin`, `Paris`,
  `AAPL`, `France`), but they are **derived from the call's content**, so a
  provider can infer what a conversation touched on.
- Requests carry an identifying User-Agent
  (`VoiceIQ-FactCheck/1.0 (+<repo url>) httpx`), no auth cookies, and no
  session/speaker identifiers.
- Hosts are fixed constants; query values go through httpx `params=`
  (auto-encoded) and redirects are disabled, so a transcript cannot redirect
  a request elsewhere.

### Controls

| Surface | Behaviour |
|---|---|
| `POST /v1/process-audio` | **Off by default.** Runs only with `?fact_check=true`, or globally via `VOICEIQ_FACTCHECK_AUTO_ENRICH=true`. When skipped, the response carries `fact_checks_v2: {"status": "disabled", ...}` and **zero external calls are made**. |
| `POST /v1/fact-check` | Explicit endpoint — calling it *is* the consent. Rate-limited (default `60/minute`). |
| Per-request override | `?fact_check=false` forces it off even when the global setting is on. |

> The legacy top-level `fact_checks` key produced by the orchestrator's own
> `FactCheckService` is a **separate, older** path from the Sprint-5
> `fact_checks_v2` engine. Both are governed by the same opt-in gate on
> `/v1/process-audio`.

---

## 2. Java Action Layer callback  (off unless configured)

Agent Brain can POST its recommendations to your Java service.

- **Destination:** whatever `AgentBrainSettings.callback_url` is set to —
  your infrastructure, not a third party.
- **Sent:** the `CallbackPayload` (session id, generated recommendations).
- **Integrity:** HMAC-SHA256 over the body using `callback_secret`, in the
  `X-VoiceIQ-Signature` header.
- **Disabled by default:** with no URL *and* secret configured, `send()` is a
  no-op that returns `False`.

---

## 3. Model downloads  (first run only)

The ML services (Whisper, pyannote, Transformers, sentence-transformers,
spaCy) fetch weights from Hugging Face on first use, then cache locally.
pyannote additionally requires `PYANNOTE_AUTH_TOKEN`. **No call data is
involved** — this is dependency fetching. Air-gapped deployments should
pre-populate the model cache.

---

## Data retention (local)

Per-request artifacts under `data/jobs/<uuid>/` — raw audio, transcript,
`whisper.json`, PDF, insights — are purged after `job_retention_hours`
(default **24h**, `0` disables). See the retention note in the hardening
plan. Fact-check results and insight records persist in SQLite until
removed by the operator.
