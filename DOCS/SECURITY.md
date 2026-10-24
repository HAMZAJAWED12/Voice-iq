Last verified: 2026-10-24, 670b4de
# Security

This page covers the security controls in the VoiceIQ service at the commit
above, how to report a vulnerability, how secrets are handled, and the known
limitations in general terms. It does not repeat two related documents:

- [DATA-FLOW.md](DATA-FLOW.md) lists every point where data leaves the host.
- [SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md) records the Tier S
  hardening work (S1–S8) and why each item was done.

File references are relative to the repository root.

---

## Reporting a vulnerability

Report security problems privately.

Whether GitHub private vulnerability reporting is enabled is the repository
owner's choice. These steps work either way:

1. **If the repository's Security tab shows a "Report a vulnerability"
   button**, use it. That is GitHub private vulnerability reporting.
2. **If it does not:** open an ordinary issue titled "Security contact
   request" that only asks the maintainers for a private contact channel.
   Leave out every detail: no affected route, no steps, no proof of
   concept, no logs, no keys. If issues are turned off, use the contact
   options on the repository owner's GitHub profile in the same way.
3. **Never** post exploit details, proofs of concept or secrets in public
   issues, pull requests, discussions or commits.

A useful private report gives the commit you tested, the affected component
or route, how to reproduce the problem, and what an attacker gains. If you
come across a credential, do not use it. Report where you saw it.

The repository has no tagged releases (`git tag` lists none), so fixes land
on the main branch.

---

## How the service is deployed

There are two entry points; [SYSTEM.md](SYSTEM.md) describes them. The
Docker image runs `app.insight_main:app`, which serves every route except
`/v1/process-audio` (`app/insight_main.py:76-102`, `Dockerfile:8-11`,
`Dockerfile:89`). `app.main:app` adds `/v1/process-audio` for the full
pipeline (`app/main.py:64-91`).

The Docker image serves plain HTTP (`Dockerfile:89` starts uvicorn with no
TLS options), and the repository has no TLS configuration for either entry
point. A deployment must therefore terminate TLS at the ingress: a trusted
reverse proxy or load balancer in front of the service holds the
certificate, and only it talks to the container, over a private network.
Nothing in the repository enforces this. `docker-compose.yml` publishes the
port on the loopback interface only and sets CPU and memory limits for the
container. The limitations below assume this setup.

---

## Controls in place

| # | Control | Main code | Tests | Key commits |
|---|---|---|---|---|
| 1 | `X-API-Key` authentication; fails closed in `production` and `staging` | `app/security/api_key.py`, `app/security/request_guard.py` | `app/insights/tests/test_auth.py`, `test_request_guard.py`, `test_deployment_defaults.py` | `bb6e61b`, `ff874de`, `d7f9075` |
| 2 | Per-caller rate limits, including the insight routes | `app/security/rate_limit.py` | `app/insights/tests/test_rate_limit.py`, `test_insight_limits.py` | `93f73b1`, `702d23d`, `e802fcd` |
| 3 | Payload and upload size caps | `app/security/payload_size.py`, `app/security/request_guard.py`, `app/routes/process_audio.py` | `app/insights/tests/test_payload_size.py`, `test_request_guard.py` | `ce3cb82`, `ff874de` |
| 4 | Extension allowlist and magic-byte check on uploads | `app/utils/upload.py`, `app/utils/audio_sniff.py` | `test_upload.py`, `test_audio_sniff.py` | `525316d`, `e8a294f` |
| 5 | ffmpeg timeout | `app/utils/audio_utils.py` | `test_audio_utils.py`, `test_audio_utils_cleanup.py` | `80cd4f0`, `5696e32` |
| 6 | Retention of job files and stored records | `app/utils/retention.py`, `app/utils/job_io.py` | `test_job_retention.py`, `test_record_retention.py` | `3c3adb2`, `cbdafb4` |
| 7 | API docs hidden in production | `app/main.py`, `app/insight_main.py` | `test_docs_gating.py` | `c7c34db` |
| 8 | Egress controls | `app/utils/factcheck_gate.py` | `test_factcheck_gate.py` | `61fbf56` |
| 9 | HMAC-signed callback, https only | `app/agent_brain/integrations/java_callback_client.py` | `app/agent_brain/tests/test_java_callback.py`, `app/agent_brain/tests/test_pipeline_callback.py` | `a54944b` |
| 10 | Secrets kept out of logs | `app/utils/logger.py`, `app/pipeline/orchestrator.py` | `test_logging_hygiene.py`, `test_logging_settings.py` | `c1b35d9`, `cedfe96`, `dc03a44`, `e25a3b2`, `45a5b22` |
| 11 | Internal detail kept out of responses (where fixed) | several, see below | `test_factcheck_source_replies.py`, `test_error_text.py`, `test_non_finite_inputs.py`, `test_factcheck_reply_limits.py`, `test_pipeline_error_text.py`, `test_readiness.py` | `f7ecd55`, `9aff207`, `8e51a94`, `0011dd7`, `93dda14`, `83803b5`, `dc97a84`, `660558c` |
| 12 | Secret scanning (gitleaks) | `.pre-commit-config.yaml`, `.github/workflows/test.yml` | — | `fceeeac`, `1a9fbed`, `c661bad` |
| 13 | `.env` kept out of git and images | `.gitignore`, `.dockerignore` | — | — |
| 14 | Bounded admission for audio processing | `app/routes/process_audio.py` | `test_process_audio_concurrency.py` | `2bb6593`, `948c49d` |
| 15 | Audio length limit | `app/utils/audio_utils.py`, `app/pipeline/orchestrator.py` | `test_audio_duration.py`, `test_orchestrator.py` | `6c17c72` |
| 16 | Fact-check run caps | `app/insights/core/factcheck/factcheck_engine.py` | `test_factcheck_run_limits.py` | `da92ae4` |
| 17 | Weights-only loading of the diarization checkpoints | `app/services/diarization_service.py` | `test_diarization_service.py` | `9887e0b` |

Test files without a path are in `app/insights/tests/`.

### 1. API-key authentication

These routes check the `X-API-Key` header: `/v1/insights/*`, `/v1/fact-check`
and `/v1/fact-check/{conversation_id}`, `/v1/process-audio` and
`/internal/v1/agent-brain/recommendations/generate`.
The check is a router dependency in each module
(`app/insights/api/insight_routes.py:41-49`,
`app/insights/api/factcheck_routes.py:46-53`,
`app/routes/process_audio.py:63-68`,
`app/agent_brain/api/agent_routes.py:25-33`). `/healthz`, `/readyz` and
`/version` are public so health and readiness checks work (`app/main.py:75-91`,
`app/insight_main.py:86-102`).

For POST, PUT and PATCH requests under these route prefixes, a request guard
runs the same check first, before the body is read, with the same status and
message. A refusal closes the connection, and the body never reaches the app
(`app/security/request_guard.py:38-48`, `:65-81`, `:113-118`). Both entry
points install it (`app/main.py:70-72`, `app/insight_main.py:81-83`). The
guard runs before routing, so an unauthenticated request with an invalid body
gets the authentication error rather than 422, and a body-carrying request to
an unknown path under a guarded prefix gets the authentication or size
error rather than 404 or 405
(`ff874de`).

What happens depends on the configuration (`app/security/api_key.py:7-15`, `:36`, `:63-122`):

| Keys in `VOICEIQ_API_KEYS` | `VOICEIQ_ENVIRONMENT` | Result |
|---|---|---|
| one or more | any | missing header → 401, wrong key → 403 |
| none | `production` or `staging` | every protected request → 503, logged as critical |
| none | `development` (the default) or `test` | request allowed, warning logged |

`staging` fails closed since `d7f9075`; before that only `production` did.

- The default environment is `development` (`app/insights/config/settings.py:52-55`).
  The Docker image sets `production` (`Dockerfile:60`).
- `VOICEIQ_API_KEYS` is a comma-separated list. Blank entries are dropped
  (`settings.py:223-244`).
- The presented key is compared in constant time against every configured
  key, and the check does not stop at the first match. Keys are compared as
  UTF-8 bytes, so a header with non-ASCII characters gets 403 like any other
  wrong key (`api_key.py:46-60`).

### 2. Per-caller rate limits

| Route | Default | Setting |
|---|---|---|
| `POST /v1/process-audio` | 30/minute | `VOICEIQ_RATE_LIMIT_PROCESS_AUDIO` |
| `POST /v1/fact-check` | 60/minute | `VOICEIQ_RATE_LIMIT_FACTCHECK` |
| `POST /internal/v1/agent-brain/recommendations/generate` | 60/minute | `VOICEIQ_RATE_LIMIT_AGENT_BRAIN` |
| Insight routes that store or delete a record (`POST /v1/insights/generate`, `POST /v1/insights/generate-from-raw`, `DELETE /v1/insights/{session_id}`) | 60/minute | `VOICEIQ_RATE_LIMIT_INSIGHT_WRITE` |
| Insight routes that read (`GET /v1/insights/`, `GET /v1/insights/{session_id}` and its `/summary`, `/speakers` and `/timeline`) | 120/minute | `VOICEIQ_RATE_LIMIT_INSIGHT_READ` |

- A request over the limit gets 429 with a `Retry-After` header
  (`app/security/rate_limit.py:62-76`).
- Each route group keeps its own counter, so two groups with the same limit
  do not share a budget (`rate_limit.py:10-14`, `:79-101`, `e802fcd`).
- Limits are counted per caller. The bucket is the API key when keys are
  configured and the header holds one of them, and the client IP otherwise,
  so while authentication is off a header cannot choose its own bucket
  (`rate_limit.py:45-54`, `702d23d`).
- Counts are kept in each process's memory. Several workers or replicas
  each count separately (`rate_limit.py:38-42`).
- `VOICEIQ_RATE_LIMIT_ENABLED` defaults to `true`. Setting it to `false`
  turns all five limits off (`rate_limit.py:63-65`). Defaults are in
  `settings.py:176-216`. The limits are attached at `process_audio.py:129`,
  `factcheck_routes.py:94`, `agent_routes.py:31` and `insight_routes.py:38-39`.

### 3. Payload and upload size caps

- **JSON routes** (`/v1/insights/*`, `/v1/fact-check`, the Agent Brain
  route): `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB`, default 2048. The
  `Content-Length` header of POST, PUT and PATCH requests is checked
  (`payload_size.py:37`, `:63-70`). A missing header gets 411. A header
  that is not an integer, or is negative, gets 400. A declared length over
  the cap gets 413 (`payload_size.py:63-95`; wiring at `insight_routes.py:29-30`,
  `factcheck_routes.py:40-41`, `agent_routes.py:22-23`; `settings.py:95-103`).
- **Before the body is read:** the request guard (control 1) runs this
  check right after the key check, with the same caps, status and message,
  so a body that is too large, or has no valid `Content-Length`, never
  reaches the app. A request that passes has its body bytes counted as they
  arrive, and a body longer than its declared length gets 413
  (`request_guard.py:38-48`, `:74-101`).
- **Audio upload:** `VOICEIQ_API_MAX_UPLOAD_MB`, default 200. The
  `Content-Length` header is checked, first by the request guard in
  `app.main` and then by the router, and the handler counts bytes again
  while it writes the upload to disk in 1 MB chunks. Going over the cap
  any way gets 413 (`process_audio.py:30-31`, `:182-232`; `app/main.py:72`;
  `settings.py:104-112`).
- Both caps are read when the module is imported. Restart to change them
  (`process_audio.py:26-30`, `insight_routes.py:27-29`).

### 4. Upload type checks

- Only `.mp3`, `.wav`, `.m4a` and `.flac` names are accepted. A missing
  filename or any other extension gets 400 with a fixed message
  (`app/utils/upload.py:14-36`, `process_audio.py:146-151`).
- The first bytes of the upload must look like WAV, FLAC, MP4/M4A or MP3. If
  they do not, the response is 415 and the partial file is deleted
  (`app/utils/audio_sniff.py:15-34`, `process_audio.py:198-205`, `:220-225`).

### 5. ffmpeg timeout

ffprobe and ffmpeg run from argument lists with no shell, ffmpeg with
`-nostdin`, under the same wall-clock timeout (`app/utils/audio_utils.py:38-55`,
`:106-149`). The timeout is
`VOICEIQ_FFMPEG_TIMEOUT_SEC`: default 120 seconds, allowed range 1–600
(`settings.py:139-152`). A timeout returns 422 with the warning
`AUDIO_NORMALIZATION_TIMEOUT`. Audio over the length limit returns 413
(control 15). Other normalisation failures return 400
(`process_audio.py:253-272`). On a timeout or failure the partly written
output file is deleted (`audio_utils.py:138-149`, `5696e32`).

### 6. Retention of job files and stored records

Each request writes its files (raw audio, transcript, PDF, insights) to
`data/jobs/<request id>/`. These folders are deleted after
`VOICEIQ_JOB_RETENTION_HOURS` (default 24; `0` turns deletion off). Insight
records not updated for `VOICEIQ_DB_RETENTION_DAYS` days, and fact-check
results created that long ago, are deleted from the database (default 30;
`0` keeps every record). One sweep handles both. Each app's startup runs it
once, then a background task runs it every hour while the app is up, in a
worker thread. A sweep that fails is logged and never raised
(`app/utils/retention.py`, `lifespan` in `app/main.py` and
`app/insight_main.py`, `settings.py:153-173`, `cbdafb4`). The sweep no
longer runs inside audio requests.

### 7. API docs hidden in production

When `VOICEIQ_ENVIRONMENT=production`, `/docs`, `/redoc` and `/openapi.json`
are turned off in both entry points (`app/main.py:45-52`,
`app/insight_main.py:56-63`).

### 8. Egress controls

- **`POST /v1/fact-check` has no opt-in.** Every call sends the claim
  subjects it detects to the providers; no setting turns this off
  ([DATA-FLOW.md §1](DATA-FLOW.md#1-fact-check--external-providers)).
- **Fact-check enrichment in `/v1/process-audio`** runs only with
  `?fact_check=true` or `VOICEIQ_FACTCHECK_AUTO_ENRICH=true`.
  `?fact_check=false` always wins. When enrichment is off, no external call is
  made and `fact_checks_v2` holds a `disabled` stub
  (`app/utils/factcheck_gate.py:17-38`, `process_audio.py:274-279`,
  `settings.py:247-257`).
- **The Agent Brain stage** in `/v1/process-audio` runs only with
  `VOICEIQ_AGENT_BRAIN_AUTO_RUN=true` (`settings.py:259-270`,
  `app/pipeline/orchestrator.py:758-759`).
- **The callback** is off unless both its URL and its secret are set
  (`app/agent_brain/config/settings.py:26-28`,
  `java_callback_client.py:67-69`).
- **Fact-check source clients** call fixed hosts. Query values are passed
  through httpx `params`, so httpx encodes them
  (`app/insights/core/factcheck/source_clients/base_client.py:152-178`). The
  Wikipedia client percent-encodes the transcript-derived country before it
  puts it in the URL path (`source_clients/static_facts_client.py:88-92`,
  `7b0c313`).

The full list of egress points, with what is sent to each, is in
[DATA-FLOW.md](DATA-FLOW.md).

### 9. HMAC-signed callback, https only

Every callback carries two signatures (`java_callback_client.py:78-95`):

| Header | Value |
|---|---|
| `X-VoiceIQ-Signature` | HMAC-SHA256(secret, body). Legacy v1. |
| `X-VoiceIQ-Signature-V2` | HMAC-SHA256(secret, `"<timestamp>.<body>"`), sent with `X-VoiceIQ-Timestamp` (Unix seconds) |

- The client refuses to send unless the URL is `https://`. Plain `http://` is
  allowed only to `localhost`, `127.0.0.1`, `::1` and `host.docker.internal`
  for local development (`java_callback_client.py:41-57`, `:71-76`).
- Recommendations below `VOICEIQ_AGENT_MIN_CONFIDENCE` (default 0.50) are
  left out of the callback (`orchestrator.py:832-840`,
  `app/agent_brain/config/settings.py:19-24`).
- The send runs on a background thread. A failure is logged with the
  exception class and HTTP status only (`orchestrator.py:848-867`).
- The receiving service has to verify the v2 signature, enforce a freshness
  window and de-duplicate on the verified v2 signature. The
  contract is [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md).

### 10. Secrets kept out of logs (milestone M1)

- The `httpx` and `httpcore` loggers are pinned to WARNING, whatever
  `VOICEIQ_LOG_LEVEL` sets the root level to at startup
  (`app/utils/logger.py:10-23`, `:31-36`, `e25a3b2`). Their request URLs carry the OpenWeather and
  Alpha Vantage API keys and transcript-derived claim subjects, and are no
  longer logged (`c1b35d9`).
- A failed callback logs the exception class and HTTP status, never the
  exception text, which would include the callback URL
  (`orchestrator.py:853-863`, `cedfe96`).
- The currency provider's errors log only the error type
  (`app/insights/core/factcheck/source_clients/forex_client.py:98-104`,
  `45a5b22`).
- Source-client warnings carry the client name, attempt number, HTTP status
  or exception class (`base_client.py:184-229`). The authentication code
  never logs key values (`api_key.py:63-122`).
- A failed insight run, and a failed fact-check enrichment or fact-check
  save in `/v1/process-audio`, log the exception class only
  (`app/insights/service.py:87-89`, `process_audio.py:106-122`, `0011dd7`).
- `test_logging_hygiene.py` checks, for the OpenWeather and Alpha Vantage
  clients, that neither the key nor the claim subject reaches any log record.
  It also checks that importing the Docker entrypoint leaves both loggers at
  WARNING.
- A logging configuration applied after `app/utils/logger.py` is imported can
  lower these levels again (`logger.py:13-14`). Leave `httpx` and `httpcore`
  out of any such configuration.

### 11. Internal detail kept out of responses (where fixed)

- If saving the upload fails, the client gets a fixed message. The cause is
  logged on the server only (`process_audio.py:213-218`, `f7ecd55`).
- Filename errors are fixed strings (`upload.py:1-8`, `:29-34`).
- A failed insight run reports the fixed message "Insight pipeline failed."
  under the code `pipeline_execution_error`. A failed fact-check enrichment
  in `/v1/process-audio` gives `fact_checks_v2` the fixed reason
  "fact-check enrichment failed". Neither returns the exception text
  (`app/insights/service.py:87-99`, `process_audio.py:115-126`, `0011dd7`).
- A failed topic step in `/v1/process-audio` gives `topic` a fixed message;
  the exception text stays in the job folder
  (`app/pipeline/orchestrator.py:597-606`, `dc97a84`).
- A failed readiness check names only the check in the `/readyz` response;
  the cause goes to the log (`app/utils/readiness.py`, `660558c`).
- A failing fact-check source client gives `SOURCE_UNAVAILABLE` for that
  claim, and for later claims about the same subject in the same run. For an unexpected exception the reason names only the exception
  class. A client that knows why it failed gives its own short reason
  (`app/insights/core/factcheck/factcheck_engine.py:274-293`). The client
  contract says those reasons must never contain URLs, keys, response bodies
  or claim text (`base_client.py:93-98`).
- Malformed provider replies give `SOURCE_UNAVAILABLE` per claim, never a
  verdict and never a 500. This covers non-finite numbers, JSON that is not
  an object, and bodies that cannot be decoded (`9aff207`, milestone M2).
  Requests ask for uncompressed replies, and a reply in any other content
  encoding is refused before its body is read. The client stops reading a
  reply once more than 1 MiB has arrived, so nothing is decompressed or held
  past that size (`base_client.py:38-79`, `:178-189`, `83803b5`, `efdd1a9`).
- The insight routes refuse JSON bodies that hold `NaN`, `Infinity` or a
  number such as `1e999` that overflows to infinity, with the standard 422 `json_invalid` error,
  before anything is stored (`app/insights/api/finite_json.py:21-49`,
  `insight_routes.py:41-44`, `93dda14`). Stored fact-check rows are read back
  with any non-finite number as null
  (`app/insights/repository/factcheck_repository.py:223-255`, `a712146`).
- The claim detector drops numbers with more than 15 digits
  (`app/insights/core/factcheck/claim_detector.py:193`, `:478`).
  `/v1/process-audio` checks the fact-check payload as strict JSON before it
  saves or returns it (`process_audio.py:101-104`, `8e51a94`, milestone M4).
- Every claim-detector pattern scans the 10,000-character transcript cap in
  linear time, so crafted text cannot make detection slow. A test pins
  hostile inputs under 0.25 s (`test_scan_time_stays_linear_on_hostile_input`,
  `1dbfac7`, milestone M5).

Some responses still include internal error text or server-side paths. See
[Known limitations](#known-limitations).

### 12. Secret scanning

- **pre-commit:** gitleaks v8.21.2 runs alongside ruff and the hygiene hooks
  (`.pre-commit-config.yaml:42-45`). Each developer has to run
  `pre-commit install` once (`.pre-commit-config.yaml:1`). The commit
  workflow is in [WORKFLOW.md](WORKFLOW.md).
- **CI:** gitleaks 8.21.2 scans the working tree with `--redact` on every push
  and pull request to `main` (`.github/workflows/test.yml:3-7`, `:50-54`).
  The job installs gitleaks only if the release archive matches the
  release's published SHA-256 checksum (`test.yml:37-48`, `c661bad`).

### 13. `.env` kept out of git and images

- `.gitignore:18-21` ignores `.env`, `.env.local` and `.env.*.local`.
  `.env` is not tracked. The only tracked env file is the template
  `.env.example` (`git ls-files`).
- `.dockerignore:35-38` keeps `.env` out of the image build context. Only the
  placeholder template `.env.example` is copied in (`Dockerfile:77`).
- Archive files (`*.zip`, `*.tar`, `*.7z` and others) are ignored
  (`.gitignore:54-59`).

### 14. Bounded admission for audio processing

Each worker process runs a limited number of audio jobs at once and lets a
limited number more wait for a slot: `VOICEIQ_PROCESS_AUDIO_MAX_RUNNING`
(default 1) and `VOICEIQ_PROCESS_AUDIO_MAX_WAITING` (default 2). Beyond
that, an upload gets 503 with a `Retry-After` header
(`VOICEIQ_PROCESS_AUDIO_RETRY_AFTER_SEC`, default 30). The check runs after
the API key and filename checks and before a job folder is created, but only
once the whole upload has been received: the framework reads the form before
the route runs. The per-caller rate limit's 429 (control 2) is also decided
only after the whole upload has arrived, before the route runs. So these
controls bound pipeline runs, not upload traffic or temporary disk use; only
the API key check and the declared-size cap (controls 1 and 3) run before the
body is read. Other
requests are still served while a job runs (`app/routes/process_audio.py:34-57`,
`:153-165`; `settings.py:113-130`; `948c49d`).

### 15. Audio length limit

Audio longer than `VOICEIQ_MAX_AUDIO_SECONDS` (default 7200, two hours) is
refused with 413 and the warning `AUDIO_TOO_LONG`; it is never truncated.
The duration is checked before decoding where it can be read, decoding
stops shortly past the limit, and a decoded file over the limit is deleted
(`app/utils/audio_utils.py:72-160`, `settings.py:131-138`,
`process_audio.py:253-272`, `6c17c72`).

### 16. Fact-check run caps

One fact-check run checks at most `VOICEIQ_FACTCHECK_MAX_CLAIMS` claims
(default 25) and starts no new claim check after
`VOICEIQ_FACTCHECK_RUN_DEADLINE_SEC` (default 20). The rest are counted as
skipped. This bounds the number of outbound calls. It does not strictly bound
the time: the deadline is checked between claims, and a check already
running can make up to two attempts, each limited by the HTTP timeout per
network operation rather than in total
(`app/insights/core/factcheck/factcheck_engine.py:201-211`,
`settings.py:288-305`, `da92ae4`).

### 17. Weights-only loading of the diarization checkpoints

The pyannote checkpoints load through PyTorch's weights-only loader, the
default since PyTorch 2.6, which restores tensors and a short list of
allowed classes instead of running arbitrary unpickling. The service
allows the six classes the pyannote 3.1 checkpoints store, by name, and
nothing else (`app/services/diarization_service.py:32-60`, `9887e0b`).
The allowance applies to the whole process from the first load attempt,
whether or not the model then loads. The
service never sets `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`; setting it turns
this protection off for every model the process loads, so do not use it
outside a local test. The model repositories are fetched at their latest
revision; no revision is pinned.

### Also in place

- The container runs as a non-root user (`Dockerfile:63`, `Dockerfile:82`).
- The repositories use SQLAlchemy statement objects, never raw SQL strings
  (`app/insights/repository/insight_repository.py:131-212`,
  `app/insights/repository/factcheck_repository.py:158-211`). The only
  literal SQL is the readiness probe's fixed `SELECT 1`
  (`app/utils/readiness.py`).

---

## Secrets handling

| Secret | Variable | Read in |
|---|---|---|
| Caller API keys | `VOICEIQ_API_KEYS` | `app/insights/config/settings.py:223-244` |
| OpenWeather key | `VOICEIQ_OPENWEATHER_API_KEY` | `settings.py:271-277` |
| Alpha Vantage key | `VOICEIQ_ALPHAVANTAGE_API_KEY` | `settings.py:278-281` |
| Callback HMAC secret | `VOICEIQ_AGENT_CALLBACK_SECRET` | `app/agent_brain/config/settings.py:17` |
| Callback URL (a secret if it embeds a token) | `VOICEIQ_AGENT_CALLBACK_URL` | `app/agent_brain/config/settings.py:16` |
| Hugging Face token (full pipeline only) | `PYANNOTE_AUTH_TOKEN`, then `HUGGINGFACE_TOKEN` | `app/services/diarization_service.py:120` |
| Database credentials, if the URL has any | `VOICEIQ_DATABASE_URL` | `settings.py:65-71` |

This table lists only the settings that hold secrets. Every setting, with
its default, is in [SYSTEM.md §3](SYSTEM.md#3-configuration).

**Rules**

- Secrets come from environment variables. For local work, a `.env` file in
  the working directory is also read (`settings.py:35-41`,
  `app/agent_brain/config/settings.py:14`), and both entry points call
  `load_dotenv()` (`app/main.py:26`, `app/insight_main.py:40`). Start from
  `.env.example`, which holds placeholders only.
- Never commit `.env`. It is gitignored (control 13).
- Every key, token and callback setting defaults to empty or unset. An empty
  provider key turns that client off. An empty callback URL or secret turns
  the callback off (`settings.py:223-231`, `:271-281`,
  `app/agent_brain/config/settings.py:16-17`, `:26-28`,
  `diarization_service.py:120-123`).
- The service's own code does not write key values to its logs (control 10).
  Do not add log lines that print settings objects, source-client request
  URLs or httpx exception text.
- Use long random values for `VOICEIQ_API_KEYS`. The example values in
  `.env.example:93` are placeholders, not keys to deploy.
- Settings are cached once per process (`settings.py:313-321`,
  `app/agent_brain/config/settings.py:31-33`). A rotated key takes effect
  only after a restart.
- The diarization service passes the Hugging Face token to pyannote with
  each model load and does not log in, so it never writes the token to
  disk (`diarization_service.py:130-132`, `670b4de`). The token still goes to
  huggingface.co with the model requests ([DATA-FLOW.md](DATA-FLOW.md) §3).
  Before `670b4de`, each cold load called `huggingface_hub.login()`, which
  saved the token in the local Hugging Face cache (by default
  `~/.cache/huggingface/token`). A host that ran an earlier version may
  still hold it there: protect that file as you would the `.env` file, and
  delete it when the token is rotated or the host is retired.

**Rotation after an exposure**

- Rotate any secret that may have appeared in logs, terminal output, an
  issue or a commit. Revoke the old value at the provider as well.
- Before `c1b35d9` (2026-10-11), the OpenWeather and Alpha Vantage keys, if
  configured, reached INFO logs whenever a weather or stock claim was
  checked. If logs from before that commit were kept or shipped anywhere,
  rotate both keys.
- Before `cedfe96` (2026-10-11), a failed callback could log the callback
  URL. If the URL carries a token and those logs were kept, rotate it.
- Change the callback secret on both sides at once. The receiving service
  verifies with the same secret.

---

## Known limitations

These are stated in general terms on purpose.

- The service is designed to run behind a trusted reverse proxy. It serves
  plain HTTP itself, so the proxy should terminate TLS.
- The application's own size limits do not replace limits at the network
  edge. Configure request body-size limits on the reverse proxy.
- Only the five route groups in control 2 have application rate limits, and
  each process keeps its own counts. Enforce rate limits at the reverse proxy
  as well.
- All API keys have the same access. Any valid key can read, replace or
  delete any stored record. This is a recorded owner decision: callers are
  one trust group. It rests on the owner's requirement that only the
  platform's own backend calls the service and that the service stays off the
  public internet; a deployment must meet both. It is to be revisited before any
  direct customer access ([DECISIONS.md #33](DECISIONS.md)). Treat everyone
  who holds a key as one trust domain.
- Authentication and the API docs depend on the environment label. With no
  keys configured, every environment except `production` and `staging` lets
  requests through, and only `production` hides the docs. For any shared or
  networked deployment, set `VOICEIQ_ENVIRONMENT=production` and
  `VOICEIQ_API_KEYS`. Also check that no env file overrides the environment
  that the image sets.
- Callback replay protection depends on the receiving service verifying the
  v2 signature with a freshness window and de-duplicating on the verified
  v2 signature. The legacy v1 signature is still
  sent until that migration is finished
  ([JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md)).
- Some responses still include internal error text or server-side paths. Do
  not pass API responses straight to end users.
- Records in the SQLite database are deleted after the retention period
  (control 6), but until then the service does not encrypt them. Protect the
  data volume.
- Audio processing is CPU- and memory-heavy, and the application's own
  limits on it are coarse. By default each worker runs one audio job at a
  time, lets two more wait and turns further uploads away with 503
  (control 14), and audio over two hours is refused (control 15). Give the
  full-pipeline process CPU and memory limits, and keep it apart from
  latency-sensitive services.

The detailed list of known gaps and suggested fixes is tracked privately.

---

## See also

- [DATA-FLOW.md](DATA-FLOW.md): every egress point.
- [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md): the callback signature contract.
- [SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md): the Tier S record.
- [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md): live milestone
  status.
- [SYSTEM.md](SYSTEM.md): entry points, image and configuration.
- [CHANGES.md](CHANGES.md): change history.
- [../CLAUDE.md](../CLAUDE.md): the project brief. [../README.md](../README.md): overview.
