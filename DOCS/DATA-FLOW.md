# Data Flow — what leaves the trust boundary

Status: current as of wave S-D (S3); model-download, legacy fact-check and
callback-content facts re-verified against the code 2026-10-11; currency
provider changed to open.er-api.com 2026-10-17; fact-check default and
reply encoding made precise 2026-10-19; diarization model changed to
pyannote 3.1 2026-10-24. Companion to
[SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md).

This document enumerates every point where VoiceIQ transmits data to a
third party. Everything **not** listed here stays on the host: audio,
transcripts, diarization, alignment, NLP enrichment, insights, the PDF and
the SQLite database are all processed and stored locally.

---

## Summary

| Flow | Trigger | Destination | Default |
|---|---|---|---|
| Fact-check verification | `/v1/fact-check`, or `/v1/process-audio` **only when enrichment is requested** | 5 public APIs (below) | `/v1/fact-check`: **every call**, on both entry points; no setting turns it off. `/v1/process-audio`: **opt-in (off)** |
| Java Action Layer callback | only the opt-in `/v1/process-audio` Agent Brain stage (`VOICEIQ_AGENT_BRAIN_AUTO_RUN=true`); the internal Agent Brain route never calls out | your own Java service | off unless auto-run + URL + secret set |
| Model weights + Hugging Face checks | first load of an ML service in each process (full pipeline only) | openaipublic.azureedge.net (Whisper), huggingface.co (pyannote, Transformers, sentence-transformers) | on (weights cached; see §3) |
| Fact-check agent model weights (Phase 1 bake-off) | manual provisioning by the operator, once per pinned revision; never by the service | huggingface.co (`Qwen/Qwen3-4B`, `Qwen/Qwen3.5-4B`, `Qwen/Qwen3-8B`) | manual; no call content (§4) |

Nothing else egresses. No telemetry, no analytics, no error reporting SaaS.

---

## 1. Fact-check → external providers

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
  session/speaker identifiers. They ask for uncompressed replies
  (`Accept-Encoding: identity`); a reply in any other encoding is refused
  unread and counts as no evidence.
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
| pyannote diarization | `huggingface.co` | Downloads `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0` and `pyannote/wespeaker-voxceleb-resnet34-LM` into pyannote's own cache (`~/.cache/torch/pyannote`). Needs `PYANNOTE_AUTH_TOKEN` (fallback `HUGGINGFACE_TOKEN`) from an account that has accepted the conditions of the two gated pyannote models. Every cold load calls `huggingface_hub.login()`, a network call that sends the token and writes it to the local Hugging Face cache. If that call fails (offline, or offline mode set) diarization falls back to a single-speaker mock, and the response carries the `DIARIZATION_UNAVAILABLE` and `MOCK_DIARIZATION_USED` warnings. |
| spaCy `en_core_web_sm` | — | Never fetched at runtime; it must be installed separately (`python -m spacy download en_core_web_sm`). Without it the keyword stage fails (`KEYWORDS_FAILED`), and so does sentiment (`SENTIMENT_FAILED`), because `SentimentService.analyze_speaker_segments` runs keyword extraction on every segment. |

Air-gapped deployments: pre-populating the caches covers Whisper and the
Transformers models (with the offline variables set), but **not** pyannote
diarization as written, because of the unconditional `login()` call.

---

## Data retention (local)

Per-request artifacts under `data/jobs/<uuid>/` — raw audio, transcript,
`whisper.json`, PDF, insights — are purged after `job_retention_hours`
(default **24h**, `0` disables). See the retention note in the hardening
plan. Insight records (by last update) and fact-check results (by creation)
are deleted from SQLite after `db_retention_days` (default **30**, `0` keeps
them). Both purges run at startup and then hourly.

---

## 4. Fact-check agent model weights (Phase 1 bake-off)

This is W6b entry 4 ([PHASE-0 §6](FACTCHECK-AGENT-PHASE-0.md#6-privacy-boundary-and-source-policy)), the
model-weight source. The download happens at provisioning time only, for the Phase 1 bake-off
([D6-CRITERIA-NOTE-S1.md](D6-CRITERIA-NOTE-S1.md) §3.4). **No call content is involved**, and no token is sent,
because the three repositories are public and ungated. The service never fetches these files. The operator
downloads them once into WSL on the VoiceIQ host, converts them to GGUF with the pinned llama.cpp build, and
quantizes them to Q4_K_M. The model server is llama.cpp `b11936` (commit `27b7a8a`). It is built without an
HTTPS client (`LLAMA_OPENSSL=OFF`) and serves only on loopback inside the host (W6a; D6 note §3.2).

- **Trigger:** manual, once per pinned revision (2026-10-28). By default nothing is fetched.
- **Source:** `https://huggingface.co/<repository>/resolve/<revision>/<file>`. Downloads use HTTPS only,
  including Hugging Face's redirects to its download hosts.
- **Pinning (U3):** every file is pinned by repository revision and SHA-256. Each large file matched its Git LFS
  SHA-256 and each small file matched its Git blob id before use. The GGUF files built from them, and their
  SHA-256, are recorded in the bake-off report (D6 note §9).
- **Licence:** Apache-2.0 for all three repositories, according to the model card metadata at these revisions.
  The D6 pre-run record (Annex A) confirms each licence.

| Repository | Revision | Files | Bytes |
|---|---|---|---|
| `Qwen/Qwen3-4B` | `1cfa9a7208912126459214e8b04321603b3df60c` | 13 | 8,060,926,626 |
| `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 14 | 9,342,907,469 |
| `Qwen/Qwen3-8B` | `b968826d9c46dd6066d109eabc6255188de91218` | 15 | 16,397,461,266 |

| Repository | File | Bytes | SHA-256 |
|---|---|---|---|
| `Qwen/Qwen3-4B` | `.gitattributes` | 1,570 | `34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930` |
| `Qwen/Qwen3-4B` | `LICENSE` | 11,343 | `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e` |
| `Qwen/Qwen3-4B` | `README.md` | 16,857 | `71add1cd091c309b1cb1a6b943f2476eb2b004124b3bd1d32dcd0b24cc1abc47` |
| `Qwen/Qwen3-4B` | `config.json` | 726 | `8ba006f74fecfaaeb392872a60f4a480e7ec9860153d2e1b769ec81f9a147f8a` |
| `Qwen/Qwen3-4B` | `generation_config.json` | 239 | `2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2` |
| `Qwen/Qwen3-4B` | `merges.txt` | 1,671,853 | `8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5` |
| `Qwen/Qwen3-4B` | `model-00001-of-00003.safetensors` | 3,957,900,840 | `328a91d3122359d5547f9d79521205bc0a46e1f79a792dfe650e99fc2d651223` |
| `Qwen/Qwen3-4B` | `model-00002-of-00003.safetensors` | 3,987,450,520 | `6cd087b316306a68c562436b5492edbcf6e16c6dba3a1308279caa5a58e21ca5` |
| `Qwen/Qwen3-4B` | `model-00003-of-00003.safetensors` | 99,630,640 | `e4bf436957184f4eeb86a80e9db394503f1f56446b2e6b7edeac5b81470f4ca1` |
| `Qwen/Qwen3-4B` | `model.safetensors.index.json` | 32,819 | `6dc0981b8829fead746441f68f38f24c5ca4a3a66351f652c26c6df0efc43ab2` |
| `Qwen/Qwen3-4B` | `tokenizer.json` | 11,422,654 | `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4` |
| `Qwen/Qwen3-4B` | `tokenizer_config.json` | 9,732 | `d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101` |
| `Qwen/Qwen3-4B` | `vocab.json` | 2,776,833 | `ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910` |
| `Qwen/Qwen3.5-4B` | `.gitattributes` | 1,570 | `34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930` |
| `Qwen/Qwen3.5-4B` | `LICENSE` | 11,544 | `bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a` |
| `Qwen/Qwen3.5-4B` | `README.md` | 77,661 | `1406be1b6b8fd8a6545870da516912804756593628a1d0fb0a7965211e82a7bb` |
| `Qwen/Qwen3.5-4B` | `chat_template.jinja` | 7,756 | `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715` |
| `Qwen/Qwen3.5-4B` | `config.json` | 3,161 | `ddc63e1c717afa86c865bb5e01313d89d72bb53b97ad4a8a03ba8510c0621670` |
| `Qwen/Qwen3.5-4B` | `merges.txt` | 3,353,259 | `a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d` |
| `Qwen/Qwen3.5-4B` | `model.safetensors-00001-of-00002.safetensors` | 5,329,398,688 | `26a93f066e1916adb13453dae5a0c707c0fbc71299ed98779571a907b8e74c61` |
| `Qwen/Qwen3.5-4B` | `model.safetensors-00002-of-00002.safetensors` | 3,990,429,408 | `cb544bd9bfae93dc59b0f22b292f5933573854a7f9b97835c67060d7d910e188` |
| `Qwen/Qwen3.5-4B` | `model.safetensors.index.json` | 76,196 | `cf3f798ee02ba45f9622aa8892a47369ab667d0afbf154ee7c2212de42e6302d` |
| `Qwen/Qwen3.5-4B` | `preprocessor_config.json` | 390 | `27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516` |
| `Qwen/Qwen3.5-4B` | `tokenizer.json` | 12,807,982 | `5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42` |
| `Qwen/Qwen3.5-4B` | `tokenizer_config.json` | 16,710 | `316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8` |
| `Qwen/Qwen3.5-4B` | `video_preprocessor_config.json` | 385 | `7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13` |
| `Qwen/Qwen3.5-4B` | `vocab.json` | 6,722,759 | `ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003` |
| `Qwen/Qwen3-8B` | `.gitattributes` | 1,570 | `34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930` |
| `Qwen/Qwen3-8B` | `LICENSE` | 11,343 | `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e` |
| `Qwen/Qwen3-8B` | `README.md` | 16,660 | `0f36caaff9c2516411a7738db384606263ba653c1e63e61d72f511606164d5a6` |
| `Qwen/Qwen3-8B` | `config.json` | 728 | `f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30` |
| `Qwen/Qwen3-8B` | `generation_config.json` | 239 | `2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2` |
| `Qwen/Qwen3-8B` | `merges.txt` | 1,671,853 | `8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5` |
| `Qwen/Qwen3-8B` | `model-00001-of-00005.safetensors` | 3,996,250,744 | `31d6a825ae35f11fb85b195b4c42c146c051e446433125a215336abdf95cbf5f` |
| `Qwen/Qwen3-8B` | `model-00002-of-00005.safetensors` | 3,993,160,032 | `5991236cea6fe21f3d43cab0f0e84448734fbbe0789816202989f2ddc9d18282` |
| `Qwen/Qwen3-8B` | `model-00003-of-00005.safetensors` | 3,959,604,768 | `c5185c4794be2d8a9784d5753c9922db38df478ce11f9ed0b415b7304d896836` |
| `Qwen/Qwen3-8B` | `model-00004-of-00005.safetensors` | 3,187,841,392 | `b5ee7de71fbf17db3d5704e0c8f2bc7d005ca9e1d7ca2aeb19827b0cfcaa917a` |
| `Qwen/Qwen3-8B` | `model-00005-of-00005.safetensors` | 1,244,659,840 | `20c2d6366ab85c90786ccdd829cd2b9e7d30ef3b2ebbb998280e7e4014b542ff` |
| `Qwen/Qwen3-8B` | `model.safetensors.index.json` | 32,878 | `f9fdbcb91c23971c13ec5d5f2573d2349e8f61f2f049371ec699281748fdb1bc` |
| `Qwen/Qwen3-8B` | `tokenizer.json` | 11,422,654 | `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4` |
| `Qwen/Qwen3-8B` | `tokenizer_config.json` | 9,732 | `d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101` |
| `Qwen/Qwen3-8B` | `vocab.json` | 2,776,833 | `ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910` |
