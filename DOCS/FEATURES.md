Last verified: 2026-10-17, 3bf7506
# Feature Catalogue

This is a list of what VoiceIQ can do today. For each feature it gives the status, how you reach it, its default, and its known functional limits. Every row cites the code or test it was checked against.

It does not explain how the parts fit together. For that, see [ARCHITECTURE.md](ARCHITECTURE.md). For security controls and limitations, see [SECURITY.md](SECURITY.md). For every point where data leaves the host, see [DATA-FLOW.md](DATA-FLOW.md). The AI brief is [CLAUDE.md](../CLAUDE.md).

## Status legend

| Status | Meaning |
|---|---|
| shipped | In the code on this branch, reachable, and covered by tests. |
| partial | Reachable, but part of the feature is a placeholder, a fallback, or does not work today. The limit is stated in the row. |
| planned | Not built. No application code exists. |

Defaults below come from `app/insights/config/settings.py` (`VOICEIQ_` prefix), `app/agent_brain/config/settings.py` (`VOICEIQ_AGENT_` prefix) and `.env.example`.

## Entry points

| Entry point | Serves | Notes |
|---|---|---|
| `app.main` | `/v1/process-audio`, `/v1/insights/*`, `/v1/fact-check`, `/internal/v1/agent-brain/*`, `/healthz`, `/version` | Full pipeline. Its pipeline stages need the heavy ML stack (`requirements.txt`). `app/main.py:60-82` |
| `app.insight_main` | Everything above except `/v1/process-audio` | Lightweight. No ASR or diarization. `app/insight_main.py:71-92` |
| Docker image | `app.insight_main` on port 8888 | Built from `requirements-insight.txt`. Sets `VOICEIQ_ENVIRONMENT=production`. `Dockerfile:45-49,60,83,89` |

`/v1/fact-check` is mounted through the combined insight router, so both entry points serve it (`app/insights/api/__init__.py:10-12`).

---

## 1. Audio pipeline: `POST /v1/process-audio`

| | |
|---|---|
| Status | shipped |
| Reached by | `POST /v1/process-audio`, multipart field `file`. Optional query parameters: `expected_speakers` (int) and `fact_check` (bool). `app/routes/process_audio.py:140-153` |
| Served by | `app.main` only |
| Guards | X-API-Key, upload size cap, rate limit (default `30/minute`, `VOICEIQ_RATE_LIMIT_PROCESS_AUDIO`). The key and the declared size are checked before the upload is read. See [§6](#6-security-features). |
| Fixed values | Whisper model `base`, language auto-detect, speaker cap 8. The API cannot change these. `app/routes/process_audio.py:232-239` |

The call is synchronous. The response comes back only after every stage has run. The pipeline and the fact-check enrichment run in a worker thread, one run at a time per worker process. While a run is in progress the worker still serves other requests, health checks included. A second upload is received, then its run waits until the first run has finished (`app/routes/process_audio.py:54-68,222-263`; commit `2bb6593`; tests in `app/insights/tests/test_process_audio_concurrency.py`). Whether a worker should allow more than one run, or answer a busy worker's uploads differently, is an open owner decision ([DECISIONS.md #29](DECISIONS.md#29-one-pipeline-run-at-a-time-per-worker-off-the-event-loop)).

**Failure model.** Four gates can fail the whole request: missing input, ffmpeg timeout, ffmpeg failure, and silent audio. They return HTTP 400, or HTTP 422 for an ffmpeg timeout (`app/routes/process_audio.py:241-254`). Every later stage is fail-soft: on error it adds a warning code to `warnings`, records the error in the job folder, and the run continues (`app/pipeline/orchestrator.py:180-228`). Two tests pin this contract: `test_exactly_four_hard_fail_gates` and `test_late_stage_exception_never_flips_status` (`app/insights/tests/test_orchestrator.py:1003,959`).

### 1.1 Stages, in run order

| # | Stage | Status | Method | Switch / default | Warning codes | Code |
|---|---|---|---|---|---|---|
| 0 | Upload gate | shipped | Extension allowlist (`.mp3 .wav .m4a .flac`), magic-byte check on the first chunk, size count while streaming | `VOICEIQ_API_MAX_UPLOAD_MB` = 200 | HTTP 400 / 415 / 413 | `app/utils/upload.py:15-36`, `app/utils/audio_sniff.py:15-34`, `app/routes/process_audio.py:159-220` |
| 1 | Normalisation | shipped | ffmpeg to mono 16 kHz WAV, with a wall-clock timeout | `VOICEIQ_FFMPEG_TIMEOUT_SEC` = 120 (range 1–600) | `AUDIO_NORMALIZATION_TIMEOUT` (422), `AUDIO_NORMALIZATION_FAILED` (400) | `app/pipeline/orchestrator.py:250-298`, `app/utils/audio_utils.py:37-97` |
| 2 | Quality gate | shipped | RMS level and an estimated SNR (signal-to-noise ratio) | Silence at RMS ≤ -60 dB, near-silence at ≤ -45 dB, low SNR < 10 dB, very low < 5 dB | `AUDIO_SILENT_OR_NEAR_SILENT` (400), `LOW_SNR_AUDIO`, `HEAVY_NOISE_AUDIO`, `AUDIO_QUALITY_FAILED` | `app/pipeline/orchestrator.py:300-345`, `app/utils/audio_quality.py:38-44,122-126` |
| 3 | Whisper ASR (speech to text) | shipped | openai-whisper, model `base`, cached per process | Always on | `EMPTY_TRANSCRIPT`, `ASR_FAILED` | `app/pipeline/orchestrator.py:347-389`, `app/services/asr_service.py:28-39` |
| 4 | Diarization (who spoke when) | partial | pyannote `pyannote/speaker-diarization`. Falls back to a one-speaker mock. | Needs `PYANNOTE_AUTH_TOKEN` or `HUGGINGFACE_TOKEN` (empty by default) | `SINGLE_SPEAKER_MODE`, `DIARIZATION_FAILED_FALLBACK`, `SPEAKER_CAP_APPLIED`, `LOW_SNR_DIARIZATION_UNRELIABLE` | `app/pipeline/orchestrator.py:391-441`, `app/services/diarization_service.py:45-53,90-92,133-147,258-329` |
| 5 | Alignment | shipped | Matches ASR segments to speaker turns. Gap merge 0.75 s, overlaps marked. | Always on | `ALIGNMENT_SKIPPED_MISSING_INPUT`, `ALIGNMENT_FAILED` | `app/pipeline/orchestrator.py:443-491` |
| 6 | Stats | shipped | Per-speaker and per-conversation talk statistics | Always on | `STATS_FAILED` | `app/pipeline/orchestrator.py:493-513` |
| 7 | Sentiment | shipped | Transformers model `cardiffnlp/twitter-roberta-base-sentiment-latest` | Always on | `SENTIMENT_SKIPPED_NO_SPEAKER_SEGMENTS`, `SENTIMENT_FAILED` | `app/pipeline/orchestrator.py:515-530`, `app/services/sentiment_service.py:31,175-197` |
| 8 | Keywords | shipped | spaCy `en_core_web_sm` phrases ranked with sentence-transformers `all-MiniLM-L6-v2`. Top 5 per segment. | Always on | `KEYWORDS_SKIPPED_NO_SPEAKER_SEGMENTS`, `KEYWORDS_FAILED` | `app/pipeline/orchestrator.py:532-547`, `app/services/keyword_service.py:20,26,106` |
| 9 | Gender | shipped | Pitch heuristic (librosa pyin) | Skipped on low SNR | `GENDER_SKIPPED_LOW_SNR`, `GENDER_SKIPPED_NO_SPEAKER_SEGMENTS`, `GENDER_FAILED` | `app/pipeline/orchestrator.py:549-567`, `app/services/gender_service.py:36-68` |
| 10 | Emotion | partial | Text and sentiment rules only. No audio emotion model is wired in. | Skipped on low SNR | `EMOTION_SKIPPED_LOW_SNR`, `EMOTION_SKIPPED_NO_SPEAKER_SEGMENTS`, `EMOTION_FAILED` | `app/pipeline/orchestrator.py:569-589`, `app/services/emotion_service.py:29-79` |
| 11 | Topic | shipped | Zero-shot `facebook/bart-large-mnli` over 11 fixed labels | Always on | `TOPIC_FAILED` | `app/pipeline/orchestrator.py:591-603`, `app/services/topic_service.py:12-24,56-62` |
| 12 | Summary | shipped | `sshleifer/distilbart-cnn-12-6`, 60–180 tokens | Always on | `SUMMARY_FAILED` | `app/pipeline/orchestrator.py:605-616`, `app/services/summary_service.py:27-60` |
| 13 | Intent | shipped | Keyword rules per turn: greeting, question, apology, complaint, storytelling, gratitude, agreement, closing, information_sharing, other | Always on | `INTENT_SKIPPED_NO_CONVERSATION`, `INTENT_FAILED` | `app/pipeline/orchestrator.py:618-634`, `app/services/intent_service.py:11-45` |
| 14 | Legacy fact candidates | partial | Regex scan for URLs and digit sequences, each marked `TO_VERIFY`. Nothing is verified and no network call is made. | Always on (not behind the fact-check opt-in) | `FACTCHECK_FAILED` | `app/pipeline/orchestrator.py:636-645`, `app/services/factcheck_service.py:7-69` |
| 15 | Flags | shipped | Heuristic hesitation, aggression and `lie_risk` flags per turn | Always on | `FLAGS_LIMITED_SINGLE_SPEAKER`, `FLAGS_FAILED` | `app/pipeline/orchestrator.py:647-658`, `app/services/flag_service.py:6-80` |
| 16 | Insights | shipped | Runs the Insight Service ([§3](#3-insight-service-v1insights)) over the aligned segments | Always on | `INSIGHTS_SKIPPED_NO_SPEAKER_SEGMENTS`, `INSIGHTS_FAILED` | `app/pipeline/orchestrator.py:660-715` |
| 17 | Agent Brain stage | shipped (opt-in) | Turns the pipeline output into action recommendations ([§5.3](#53-pipeline-stage-and-java-callback)) | `VOICEIQ_AGENT_BRAIN_AUTO_RUN` = false | `AGENT_BRAIN_SKIPPED_NO_INSIGHTS`, `AGENT_BRAIN_FAILED` | `app/pipeline/orchestrator.py:717-787` |
| 18 | PDF report | shipped | fpdf report, returned base64-encoded in `report_pdf_base64` | Always on | `PDF_FAILED` | `app/pipeline/orchestrator.py:852-880`, `app/services/pdf_service.py:119-144` |

After the stages finish, the route adds fact-check enrichment (`fact_checks_v2`, [§2](#2-fact-check-enrichment-on-process-audio)).

### 1.2 Known functional limits

- **Speaker count.** If `expected_speakers` is not given, diarization asks for exactly two speakers. It widens to 1–8 only if that call raises an error (`app/services/diarization_service.py:269-286`).
- **Diarization fallback.** With no token, if pyannote is not installed, or if the model fails to load, the whole file is labelled `SPEAKER_00`. No diarization error warning is raised for this. The signs in the response are the `SINGLE_SPEAKER_MODE` warning and `"source": "mock"` on the entries in `segments` (`app/services/diarization_service.py:86-93,124-128,133-147,263-264`; `app/pipeline/orchestrator.py:438-441,929`).
- **Keyword model must be installed separately.** The keyword stage needs the spaCy `en_core_web_sm` model. No requirements file installs it, so it needs its own install step. The sentiment stage also runs keyword extraction on every segment, so without that model both stages fail (`app/services/sentiment_service.py:184-197`). See [DATA-FLOW.md §3](DATA-FLOW.md#3-model-downloads--full-pipeline-only).
- **Sentiment score is confidence.** `sentiment_score` is the model's confidence in its label. It is not a positivity value. The Insight Service builds its session sentiment trend from that score (`app/insights/core/signal_aggregation.py:70-97,163-170`), so on this path the trend direction can mislead.
- **Topic and summary read only the start of the call.** Topic uses the first 512 characters of the transcript (`app/services/topic_service.py:59`). Summary uses the first 4,000 characters (`app/services/summary_service.py:38-51`).
- **Emotion and gender are rough.** Emotion always uses the text fallback (`app/services/emotion_service.py:66-79`). Gender is a pitch heuristic: segments under 0.3 s, or with pitch between 145 and 185 Hz, return `unknown` (`app/services/gender_service.py:49-65`).
- **Flags are hints, not judgements.** The code itself describes `lie_risk` as a weak heuristic and not proof of deception (`app/services/flag_service.py:63-80`).
- **The insight stage does not check for errors.** If the Insight Service returns `status: "error"`, the stage still records `ok`, and, when enabled, the Agent Brain stage still runs on the empty bundle (`app/pipeline/orchestrator.py:673-689,747`). To catch this, check `insights.status` in the response.
- **PDF prints Latin-1 only.** The report uses the Helvetica core font, which covers Latin-1 only. Text outside it no longer fails the PDF. Curly quotes, dashes, the ellipsis, the bullet and thin spaces print as their nearest Latin-1 form, and the euro sign prints as "EUR". Any other character, such as Urdu or Arabic script, prints as "?"; zero-width spaces and byte-order marks are dropped. The JSON response keeps the original text (`app/services/pdf_service.py:29-58,71`; commit `dc9caf3`; tests in `app/insights/tests/test_pdf_unicode.py`; [DECISIONS.md #31](DECISIONS.md#31-m6-the-pdf-prints-latin-1-and-substitutes-the-rest)).
- **PDF contents.** The PDF includes warnings, audio quality, topic, summary, conversation and speaker stats, emotion overview, intents, flags, legacy fact candidates and the transcript, cut to its first 4,000 characters. It does not include the per-segment detail (the `speaker_segments` argument is accepted but not used), insights, `fact_checks_v2` or recommendations (`app/services/pdf_service.py:119-132,151-299`).

### 1.3 Response keys

Every run returns these top-level keys: `request_id`, `transcript`, `asr_meta`, `segments`, `speaker_segments`, `conversation`, `speaker_stats`, `conversation_stats`, `topic`, `summary`, `report_pdf_base64`, `intents_summary`, `fact_checks` (legacy candidates), `flags`, `insights`, `timeline`, `emotion_overview`, `warnings`, `single_speaker_mode`, `audio_quality` and `pipeline_meta` (`app/pipeline/orchestrator.py:920-962`).

Two more keys:

- `recommendations` appears only when the Agent Brain stage produced output. Otherwise it is left out entirely, not set to null (`app/pipeline/orchestrator.py:963-964`).
- `fact_checks_v2` is added by the route to every successful response (`app/routes/process_audio.py:258-261`).

---

## 2. Fact-check enrichment on process-audio

| | |
|---|---|
| Status | shipped (opt-in) |
| Reached by | `?fact_check=true` on `/v1/process-audio`, or `VOICEIQ_FACTCHECK_AUTO_ENRICH=true`. The per-request value always wins, including `?fact_check=false`. `app/utils/factcheck_gate.py:24-38` |
| Default | Off. `fact_checks_v2` is `{"status": "disabled", ...}` and no external call is made. `app/utils/factcheck_gate.py:18-21` |
| Output | `fact_checks_v2`: a full fact-check v1 response ([§4](#4-fact-check-v1-v1fact-check)), or a `skipped` stub (empty transcript) or a `failed` stub. `app/routes/process_audio.py:82-137` |
| Tests | `app/insights/tests/test_factcheck_gate.py`, `app/insights/tests/test_factcheck_source_replies.py`, `app/insights/tests/test_error_text.py` |

Limits:

- The transcript is cut to 10,000 characters before checking (`app/routes/process_audio.py:101-102`).
- Saving the results is best-effort. A storage failure is logged and does not fail the response (`app/routes/process_audio.py:116-124`).
- The `failed` stub's reason is always "fact-check enrichment failed". The exception text is not returned; the log names only the exception class, for the enrichment and for a storage failure (`app/routes/process_audio.py:116-137`; commit `0011dd7`; test `test_a_failed_fact_check_enrichment_gives_a_fixed_reason`).
- Before saving or returning, the payload is checked as strict JSON (no NaN or Infinity). If the check fails, the response gets the `failed` stub and nothing is saved (`app/routes/process_audio.py:111-115`; commit `8e51a94`; tests `test_process_audio_never_500s_on_an_unrenderable_fact_check` and `test_process_audio_never_saves_an_unrenderable_fact_check`).

---

## 3. Insight Service: `/v1/insights`

| | |
|---|---|
| Status | shipped |
| Reached by | The routes below, on both entry points |
| Guards | X-API-Key. POST bodies capped at `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB` = 2048. Both are checked before the body is read. JSON bodies may hold finite numbers only. `app/insights/api/insight_routes.py:26-43` |
| Storage | SQLite table `insight_records`, at `VOICEIQ_DATABASE_URL` = `sqlite:///./data/insights.db`. Tables are created on startup unless `VOICEIQ_DATABASE_AUTO_CREATE=false`. `app/insights/repository/orm_models.py:33`, `app/main.py:30-38` |
| Tests | `app/insights/tests/test_insight_api.py`, `app/insights/tests/test_non_finite_inputs.py` and the engine tests in `app/insights/tests/` |

| Method | Path | Does | Code |
|---|---|---|---|
| POST | `/v1/insights/generate` | Runs the Insight Service over a validated `SessionInput`, stores the result, and returns it | `insight_routes.py:57-74` |
| POST | `/v1/insights/generate-from-raw` | Takes a loosely typed dict, validates and normalises it, then does the same as `/generate` | `insight_routes.py:77-94` |
| GET | `/v1/insights/{session_id}` | Full stored bundle | `insight_routes.py:97-112` |
| GET | `/v1/insights/{session_id}/summary` | Overall summary, notable concerns, key moments | `insight_routes.py:115-137` |
| GET | `/v1/insights/{session_id}/speakers` | Per-speaker metrics, insights and summaries | `insight_routes.py:140-165` |
| GET | `/v1/insights/{session_id}/timeline` | Timeline markers | `insight_routes.py:168-188` |
| GET | `/v1/insights/` | Lists every stored `session_id` | `insight_routes.py:191-199` |
| DELETE | `/v1/insights/{session_id}` | Deletes a stored record | `insight_routes.py:202-216` |

The engines run in this order: analytics, then rules, then summary (`app/insights/service.py:65-67`). The rule engine itself runs signal aggregation, timeline, escalation, inconsistency and scoring (`app/insights/core/rule_engine.py:32-86`). If an engine fails, the result is a `status: "error"` response, not an HTTP error. Its validation error has code `pipeline_execution_error` and the fixed message "Insight pipeline failed."; the exception text is not returned or stored, and the log names only the exception class (`app/insights/service.py:87-108,313-342`; commit `0011dd7`; test `test_a_failed_insight_run_does_not_return_the_exception_text`).

**Finite numbers only.** A POST body with `NaN`, `Infinity`, `-Infinity` or a number such as `1e999` that overflows to infinity gets HTTP 422 with the standard `json_invalid` error while it is parsed, and nothing is stored. This applies to both POST routes; `/generate-from-raw` refuses such a body rather than correcting it (`app/insights/api/finite_json.py:21-49`, `insight_routes.py:38`; commit `93dda14`; [DECISIONS.md #30](DECISIONS.md#30-insight-routes-accept-only-finite-numbers)). For in-process callers, `SessionInput` and its utterance, sentiment and emotion models refuse non-finite values in their number fields, and the raw-session normaliser treats a non-finite start, end or duration like any invalid value and uses its default (`app/insights/models/input_models.py:12,16,23,36,79`; `app/insights/core/normalizer.py:203-216`).

Limits:

- **Re-posting overwrites.** Posting a `session_id` that already exists replaces the stored record, even when the new result is an `error` response (`app/insights/repository/insight_repository.py:79-95`).
- **Two settings do nothing.** `VOICEIQ_THRESHOLD_PROFILE` (default `default`) is validated but never read, so only the built-in thresholds apply. `VOICEIQ_LOG_LEVEL` (default `INFO`) is also validated but never read: the root logger is fixed at INFO (`app/utils/logger.py:12-15`). An invalid value for either still stops startup (`app/insights/config/settings.py:56-59,82-88`; `.env.example:14-16,32-35`).

---

## 4. Fact-check v1: `/v1/fact-check`

| | |
|---|---|
| Status | partial: the engine is shipped, but some claim types never get a verdict (see the table below) |
| Reached by | `POST /v1/fact-check` with `{conversation_id, speaker_id, transcript_text}`. `transcript_text` can be up to 10,000 characters; longer text gets HTTP 422. `GET /v1/fact-check/{conversation_id}` lists stored results and returns 404 if there are none. `app/insights/api/factcheck_routes.py:79-131`, `app/insights/models/factcheck_models.py:155-163` |
| Guards | X-API-Key, 2048 KB body cap, rate limit on POST (default `60/minute`, `VOICEIQ_RATE_LIMIT_FACTCHECK`) |
| Storage | SQLite table `fact_check_results`. Saving is best-effort. `app/insights/repository/factcheck_orm_models.py:33`, `factcheck_routes.py:103-112` |
| Egress | Claim subjects are sent to external providers. See [DATA-FLOW.md §1](DATA-FLOW.md#1-fact-check--external-providers--opt-in). |
| Tests | `test_factcheck_api.py`, `test_factcheck_engine.py`, `test_claim_detector.py`, `test_classifier.py`, `test_comparator.py`, `test_scorer.py`, `test_source_clients.py`, `test_factcheck_source_replies.py`, `test_factcheck_reply_limits.py`, `test_factcheck_cache_threads.py`, `test_factcheck_old_rows.py` (all in `app/insights/tests/`) |

How a check runs: a regex detector finds claims, a classifier normalises them, a source client fetches evidence, a comparator decides the verdict, and a scorer sets the confidence (`app/insights/core/factcheck/factcheck_engine.py:167-244`). No LLM (large language model) is used.

Verdict bands for numbers: a difference of up to 3% is `TRUE`, up to 10% is `PARTIALLY_TRUE`, and anything larger is `FALSE` (`app/insights/core/factcheck/comparator.py:34-35`). If identical claims repeat, the evidence is reused from a cache for 60 seconds, up to 256 entries (`factcheck_engine.py:108,158-161`).

### 4.1 Claim types and sources

| Claim type | Example the detector matches | Source client | API key | What happens today |
|---|---|---|---|---|
| `CURRENCY_RATE` | "USD to PKR is 280", "1 USD = 280 PKR" (11 currency codes) | exchangerate.host | The provider requires one. The service has no setting for it, and none is configured. | `SOURCE_UNAVAILABLE`. The request is still sent; the provider rejects it, and the client reports the reason "provider requires an access key". |
| `CRYPTO_PRICE` | "Bitcoin is at 95000" (7 assets) | CoinGecko | none | Checked against the USD price |
| `STOCK_PRICE` | "AAPL is at 175" (14 tickers) | Alpha Vantage | `VOICEIQ_ALPHAVANTAGE_API_KEY`, empty by default | With no key: `SOURCE_UNAVAILABLE`, and no request is made |
| `WEATHER` | "temperature in Karachi is 32 C" | OpenWeather | `VOICEIQ_OPENWEATHER_API_KEY`, empty by default | With no key: `SOURCE_UNAVAILABLE`, and no request is made |
| `COMMODITY_PRICE` | "gold price 2400" | none | — | Always `UNSUPPORTED_CLAIM_TYPE` |
| `STATIC_FACT` | "capital of France is Paris" | Wikipedia REST | none | Checked against the page summary |

Code: detector patterns `app/insights/core/factcheck/claim_detector.py:100-180`, match filter `claim_detector.py:385-467`; routing `factcheck_engine.py:70-85`; forex behaviour `source_clients/forex_client.py:1-9,73-84` (commit `dc03a44`); key checks `source_clients/stock_client.py:36-37` and `source_clients/openweather_client.py:32-34`. The per-call HTTP timeout is `VOICEIQ_FACTCHECK_HTTP_TIMEOUT_SEC` = 5.0 (range 0.5–30). For the forex provider question, see [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) and the [decision register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).

### 4.2 Robustness

- **A bad provider reply affects only claims about the same subject in that run.** A transport error, an HTTP error, invalid JSON, a reply that is not an object, a non-finite number, or a client that raises: each gives that claim, and later claims about the same subject in the run, `SOURCE_UNAVAILABLE` with a reason. It never produces a verdict and never causes a 500 (`factcheck_engine.py:203-216,250-269`; `source_clients/base_client.py:130-205`; commit `9aff207`; tests `test_one_bad_reply_affects_only_its_own_claim` and `test_fact_check_api_never_500s_on_a_bad_reply`).
- **Provider replies are capped at 1 MiB.** Each reply is streamed and read only up to `MAX_REPLY_BYTES` (1 MiB), counted after decompression. A larger reply gives its claim `SOURCE_UNAVAILABLE`, like any other unusable reply (`source_clients/base_client.py:41-57,155-160`; commit `83803b5`; tests `test_an_oversized_reply_is_not_read_to_the_end` and `test_the_limit_counts_decompressed_bytes`).
- **A failed subject is fetched once per run.** When the fetch for a subject fails, later claims about the same subject in that run reuse the first result and reason. The next run fetches it again (`factcheck_engine.py:183-185,203-209`; commit `83803b5`; tests `test_a_failed_subject_is_fetched_once_per_run` and `test_a_failed_subject_is_fetched_again_in_the_next_run`).
- **The evidence cache is thread-safe.** The cache is shared by every request in the process. A lock guards its dictionary operations, never a provider call. When it is full, expired entries are dropped before the oldest live one (`factcheck_engine.py:99-135`; commit `1d967c5`; tests in `test_factcheck_cache_threads.py`).
- **Stored non-finite numbers read back as null.** The repository returns any non-finite number in a stored row, at any depth and in the evidence too, as `null`. Stored data is not changed (`app/insights/repository/factcheck_repository.py:209-241`; commit `a712146`; tests in `test_factcheck_old_rows.py`).
- **Over-long numbers are dropped.** A number with more than 15 digits is not a checkable claim, so the detector drops that claim. Other claims and their ids are kept (`claim_detector.py:193,470-484,512`; commit `8e51a94`; tests `test_detector_drops_a_claim_whose_number_is_too_long` and `test_dropping_an_over_long_claim_keeps_the_others_and_their_ids`).
- **Names, ages and counts are not prices.** Before overlaps are resolved, each match passes `_keep_match` (`claim_detector.py:352,385-467`; commit `1dbfac7`; tests in `test_claim_detector.py`):
  - A crypto, stock or commodity number followed on the same line by what it counts or measures, or by another currency, is dropped: "Bitcoin is 2 years old", "META is 30 people", "Bitcoin is 5 per cent up", "Gold is 50 a month", "Meta is 30 dollars a day", "BTC is 95000 rupees", "Bitcoin is 90000 in euros", "Gold is 5%", "Bitcoin is at 3:15". For a rising range the word after its end counts ("Bitcoin is 10-20 minutes"). "Gold is 24k" is a purity in karats. The word lists are `_NOT_PRICE_WORDS`, `_NOT_PRICE_PER_UNITS` and `_OTHER_CURRENCY_WORDS` (`claim_detector.py:243-313`). Crypto and stock sources quote USD, which is why other currencies are dropped.
  - A subject that is also a name or an everyday word (ada, sol, eth, doge, brent), or any stock ticker ("Meta", "Nvda"), needs price context unless it is written in capitals ("ADA", "META"): a "$", a dollar word ("US dollars", "U.S. dollars"), "a barrel" / "per share", or a price verb (price, costs, trading at, stock). So "Ada is 30", "my friend Sol is 25" and "Brent is 30" give no claim; "Meta is 500 a share" and "Sol is $150" do.
- **Common price phrasing is read in full.** "1 USD = 280 PKR" and "100 USD is 28000 PKR" are rates (value divided by an amount of 1, 10, 100, ...); "100 USD to PKR is 28000" is a total and is dropped. "95k", "1.2m", "24 hundred", "95 grand", "1.5 million", "1.2 mil" and "0.1 billion" scale the value exactly, and "95,000.50" is read whole.
- **Detection runs in linear time.** Every pattern scans 10,000 hostile characters in milliseconds; whitespace sub-patterns that backtracked quadratically (0.5–2 s) were rewritten (commit `1dbfac7`; test `test_scan_time_stays_linear_on_hostile_input`).

### 4.3 Known functional limits of the detector

These were checked by running the detector at `1dbfac7` on 2026-10-16:

- A past price reads as a current one: "I bought ETH at 1200 back in 2021" gives an ETH claim of 1200.
- Capitalised acronyms read as tickers: "The ADA is 30" and "Our CRM is at 5" give claims. "Solana is 25" gives a claim, because only ada, sol, eth, doge and brent count as names.
- Any number after a coin, ticker or commodity counts, whatever the surrounding sentence: "The minimum for Bitcoin is 10" and "Gold is 29.99 with free shipping" give claims. A word after the number is checked only against the lists above, so "AMD is 30 interns" gives a claim.
- Prices are not converted: "DOGE is 12 cents" and "2 EUR is 650 PKR" (an amount other than 1, 10, 100, ...) give no claim. "Bitcoin is 21 million" reads as a price of 21,000,000; "Bitcoin is 95 thousand 500" reads as 95,000.
- A decimal comma reads as a thousands separator: "1 EUR = 1,08 USD" gives 108. A total for a number of units other than 10, 100, ... reads as the rate: "21 USD to PKR is 5880" gives 5880. A round amount in other wording reads as a rate: "Every 100 USD is 5 EUR cashback" gives 0.05.
- Patterns are English only.

These limits wait for Phase 3: until then the detector changes only to fix a crash, a security problem or a performance problem ([DECISIONS.md #26](DECISIONS.md#26-claim-detector-no-new-heuristics-until-phase-3)).

---

## 5. Agent Brain

The Agent Brain turns a processed conversation into ranked action recommendations. Every recommendation has `requiresApproval: true`. It only recommends; Java owns whether anything is carried out (`app/agent_brain/models/recommendation.py:55`; `app/agent_brain/models/enums.py:44-56`).

### 5.1 The five agents

| Agent | Action type | Detects | Code |
|---|---|---|---|
| Task | `TASK` | To-dos, commitments, assignments | `app/agent_brain/core/task_agent.py` |
| Follow-Up Meeting | `CALENDAR` | A future meeting or call that needs arranging | `app/agent_brain/core/followup_agent.py` |
| Email Draft | `EMAIL` | An email that needs sending. Drafts only. | `app/agent_brain/core/email_draft_agent.py` |
| Escalation | `SUPPORT` | Anger, complaint and risk wording | `app/agent_brain/core/escalation_agent.py` |
| Fact-Check Review | `FACT_CHECK` | Claims marked `FALSE`, `UNVERIFIED` or `NEEDS_REVIEW`. Priority is `CRITICAL` for `FALSE`, otherwise `HIGH`. | `app/agent_brain/core/factcheck_review_agent.py` |

The runner calls every agent. If one agent fails, it is logged and skipped, and the others' results still stand. The runner then refines confidence, removes duplicates and ranks the results (`app/agent_brain/core/agent_runner.py:19-75`). Two recommendations of the same action type are duplicates when they share an assignee and deadline, a source segment, or a similar title (similarity 0.82). Fact-Check Review recommendations are told apart by claim instead: reviews of different claims are never duplicates, and reviews without claim ids compare the claim text. So each reviewed claim keeps its own recommendation, and one claim's review does not raise another's confidence (`app/agent_brain/core/deduplication.py:33,73-103`; commit `8103360`; tests in `app/agent_brain/tests/test_dedup_fact_check_claims.py`). Deadline phrases such as "next Monday" are turned into calendar dates (`app/agent_brain/extraction/datetime_resolver.py`; commit `99dfc96`).

### 5.2 Internal route

| | |
|---|---|
| Status | shipped |
| Reached by | `POST /internal/v1/agent-brain/recommendations/generate`, on both entry points. Request and response use camelCase: `AgentContext` in, `RecommendationResponse` out. `app/agent_brain/api/agent_routes.py:25-41` |
| Guards | X-API-Key, 2048 KB body cap, rate limit (default `60/minute`, `VOICEIQ_RATE_LIMIT_AGENT_BRAIN`) |
| Calls out? | No. This route never triggers the Java callback. |
| Tests | `app/insights/tests/test_agent_brain_mounted.py`, `app/agent_brain/tests/` |

### 5.3 Pipeline stage and Java callback

| | |
|---|---|
| Status | shipped (opt-in) |
| Pipeline stage | `VOICEIQ_AGENT_BRAIN_AUTO_RUN` = false. When on, `/v1/process-audio` adds a `recommendations` key (`app/pipeline/orchestrator.py:717-787`; commit `f5e4d84`; tests `TestAgentBrainStage` in `app/insights/tests/test_orchestrator.py:718`). |
| Java callback | Fires only from the pipeline stage, only when both `VOICEIQ_AGENT_CALLBACK_URL` and `VOICEIQ_AGENT_CALLBACK_SECRET` are set, and only if at least one recommendation passes the confidence filter below. Both settings are empty by default (`app/agent_brain/config/settings.py:14-28`; `app/pipeline/orchestrator.py:811-824`; commit `f619f7d`). The request is HMAC-signed. The contract is in [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md). |
| Callback timeout | `VOICEIQ_AGENT_CALLBACK_TIMEOUT_SEC` = 5.0 (range 1–60) |
| Confidence filter | `VOICEIQ_AGENT_MIN_CONFIDENCE` = 0.50. It applies to the callback only: the API response still carries every recommendation (`app/pipeline/orchestrator.py:815-824`). |
| Tests | `app/agent_brain/tests/test_pipeline_callback.py`, `app/agent_brain/tests/test_java_callback.py` |

### 5.4 Known functional limits

- **Fact-Check Review is dormant in the pipeline.** On the pipeline path the stage passes no fact-check results (`fact_check=None`), so this agent never fires there. The other four agents run (`app/pipeline/orchestrator.py:725-733,763`). For the planned Sprint 7 work that touches this path, see [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).
- **English only.** Only English vocabulary ships. The values `ur`, `ar` and `mixed` are accepted, but they fall back to the English rules (`app/agent_brain/extraction/language.py`; `app/agent_brain/tests/test_language_seam.py`). The plan is in [N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md).
- **Dates use the processing date.** Relative deadlines resolve against the server's date when the call is processed, not the date of the call (`app/agent_brain/core/base_agent.py:29-38`).
- **The callback is single-shot and fire-and-forget.** It is one POST on a background daemon thread, with no retry. A send still in flight at shutdown is dropped. Retries and de-duplication belong to the Java side (`app/pipeline/orchestrator.py:789-850`; `app/agent_brain/integrations/java_callback_client.py:65-100`).

---

## 6. Security features

One line each. The controls, how to report a vulnerability, and known limitations are in [SECURITY.md](SECURITY.md). The history of the hardening work is in [SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md).

| Feature | Setting / default | Code or commit |
|---|---|---|
| X-API-Key auth on every `/v1` and `/internal` route. A production deployment with no keys configured refuses requests. | `VOICEIQ_API_KEYS` (empty) | `app/security/api_key.py`; `bb6e61b` |
| Body and upload size caps (HTTP 413) | 2048 KB / 200 MB | `app/security/payload_size.py`; `ce3cb82` |
| API key, then declared size, checked before a POST, PUT or PATCH body is read on the guarded routes, with the same status and detail as the route checks. A refusal closes the connection. A body longer than its declared length gets 413. | — | `app/security/request_guard.py`, installed at `app/main.py:68` and `app/insight_main.py:78`; `ff874de`; [DECISIONS.md #28](DECISIONS.md#28-request-checks-run-before-the-body-is-read) |
| Upload extension allowlist and magic-byte check | — | `app/utils/upload.py`, `app/utils/audio_sniff.py`; `525316d`, `e8a294f` |
| ffmpeg timeout | 120 s | `app/utils/audio_utils.py`; `80cd4f0` |
| Per-caller rate limits (HTTP 429 with Retry-After) on `POST /v1/process-audio`, `POST /v1/fact-check` and the internal Agent Brain route. The bucket is the API key only when it is an accepted key, otherwise the client IP. | `VOICEIQ_RATE_LIMIT_ENABLED` = true | `app/security/rate_limit.py:43-52`; `93f73b1`, `702d23d` |
| Job files deleted after a retention period | `VOICEIQ_JOB_RETENTION_HOURS` = 24 (0 turns it off) | `app/utils/job_io.py:127`; `3c3adb2` |
| Fact-check enrichment is opt-in | `VOICEIQ_FACTCHECK_AUTO_ENRICH` = false | `app/utils/factcheck_gate.py`; `61fbf56` |
| Upload failures return a generic message | — | `app/routes/process_audio.py:201-206`; `f7ecd55` |
| A failed insight run and a failed fact-check enrichment return fixed messages, not the exception text; their logs name only the exception class | — | `app/insights/service.py:87-98`, `app/routes/process_audio.py:116-137`; `0011dd7` |
| Wikipedia lookups percent-encode the country | — | `source_clients/static_facts_client.py`; `7b0c313` |
| Callback signatures (v1 and timestamped v2); HTTPS required except to local hosts | — | `java_callback_client.py:45-57,78-95`; `a54944b` |
| `/docs`, `/redoc` and `/openapi.json` hidden in production | `VOICEIQ_ENVIRONMENT=production` | `app/main.py:43-48`, `app/insight_main.py:53-58`; `c7c34db` |
| Model loading serialised on first use (diarization, sentiment and summary models) | — | `app/services/diarization_service.py`, `app/services/sentiment_service.py`, `app/services/summary_service.py`; `7c9f4fc` |
| Outbound request URLs kept out of logs; a failed callback logs only the error class and HTTP status | — | `app/utils/logger.py:4-15`, `app/pipeline/orchestrator.py:836-846`; `c1b35d9`, `cedfe96` |

Tests: `test_auth.py`, `test_payload_size.py`, `test_request_guard.py`, `test_upload.py`, `test_audio_sniff.py`, `test_rate_limit.py`, `test_job_retention.py`, `test_factcheck_gate.py`, `test_docs_gating.py`, `test_logging_hygiene.py` and `test_error_text.py` (all in `app/insights/tests/`).

---

## 7. Ops endpoints

| Method | Path | Returns | Auth |
|---|---|---|---|
| GET | `/healthz` | `{"status": "ok"}` | none |
| GET | `/version` | version, service name, environment | none |
| GET | `/docs`, `/redoc`, `/openapi.json` | Interactive API docs and schema | none. Not served when `VOICEIQ_ENVIRONMENT=production`. |

Both entry points serve these (`app/main.py:43-48,71-82`, `app/insight_main.py:53-58,81-92`). The Docker health check calls `/healthz` (`Dockerfile:85-87`). The Docker image sets `VOICEIQ_ENVIRONMENT=production`, so it does not serve the interactive docs (`Dockerfile:60`).

---

## 8. Planned: Sprint 7 fact-check agent

| | |
|---|---|
| Status | **planned, not built.** No Sprint 7 application code exists: `app/` has no `/v2/fact-check` route and no `fact_check_report` key. |
| Scope | An open-source LLM fact-check agent that extracts claims, retrieves evidence and returns cited verdicts. The contract text (v2 routes, verdicts, and the `fact_check_report` key on process-audio) is in [FACTCHECK-AGENT-PHASE-0.md §2](FACTCHECK-AGENT-PHASE-0.md#2-api-contract-lock). |
| Where it stands | [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md): current phase, progress and next milestone |
| Decisions | Only in the register: [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register) |

The contract text in [FACTCHECK-AGENT-PHASE-0.md §2.1](FACTCHECK-AGENT-PHASE-0.md#21-v1-is-frozen) keeps the v1 fact-check engine ([§4](#4-fact-check-v1-v1fact-check)) unchanged and gives the planned agent its own models. Whether that contract is approved is tracked in the status doc and the register, not here.

---

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md): how the components fit together
- [SYSTEM.md](SYSTEM.md): the running system
- [SECURITY.md](SECURITY.md): controls, reporting, known limitations
- [DATA-FLOW.md](DATA-FLOW.md): every egress point
- [CHANGES.md](CHANGES.md): change history
- [DECISIONS.md](DECISIONS.md): signed and implemented decisions
- [EXPLANATION.md](EXPLANATION.md): plain-English walkthrough
- [CONTEXT.md](CONTEXT.md), [WORKFLOW.md](WORKFLOW.md)
- [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md), [N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md), [00-index.md](00-index.md)
- [../README.md](../README.md), [../CLAUDE.md](../CLAUDE.md)
