Last verified: 2026-10-17, 3bf7506
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
`/v1/process-audio` (`app/insight_main.py:71-92`, `Dockerfile:8-11`,
`Dockerfile:89`). `app.main:app` adds `/v1/process-audio` for the full
pipeline (`app/main.py:60-82`).

The Docker image serves plain HTTP (`Dockerfile:89` starts uvicorn with no
TLS options), and the repository has no TLS configuration for either entry
point. Put the service behind a trusted reverse proxy that terminates TLS
([08-deployment.md](08-deployment.md) line 194). The limitations below assume
this setup.

---

## Controls in place

| # | Control | Main code | Tests | Key commits |
|---|---|---|---|---|
| 1 | `X-API-Key` authentication | `app/security/api_key.py`, `app/security/request_guard.py` | `app/insights/tests/test_auth.py`, `test_request_guard.py` | `bb6e61b`, `ff874de` |
| 2 | Rate limits on the expensive routes | `app/security/rate_limit.py` | `app/insights/tests/test_rate_limit.py` | `93f73b1`, `702d23d` |
| 3 | Payload and upload size caps | `app/security/payload_size.py`, `app/security/request_guard.py`, `app/routes/process_audio.py` | `app/insights/tests/test_payload_size.py`, `test_request_guard.py` | `ce3cb82`, `ff874de` |
| 4 | Extension allowlist and magic-byte check on uploads | `app/utils/upload.py`, `app/utils/audio_sniff.py` | `test_upload.py`, `test_audio_sniff.py` | `525316d`, `e8a294f` |
| 5 | ffmpeg timeout | `app/utils/audio_utils.py` | `test_audio_utils.py`, `test_audio_utils_cleanup.py` | `80cd4f0`, `5696e32` |
| 6 | Job-artifact retention | `app/utils/job_io.py` | `test_job_retention.py` | `3c3adb2` |
| 7 | API docs hidden in production | `app/main.py`, `app/insight_main.py` | `test_docs_gating.py` | `c7c34db` |
| 8 | Opt-in egress | `app/utils/factcheck_gate.py` | `test_factcheck_gate.py` | `61fbf56` |
| 9 | HMAC-signed callback, https only | `app/agent_brain/integrations/java_callback_client.py` | `app/agent_brain/tests/test_java_callback.py`, `app/agent_brain/tests/test_pipeline_callback.py` | `a54944b` |
| 10 | Secrets kept out of logs | `app/utils/logger.py`, `app/pipeline/orchestrator.py` | `test_logging_hygiene.py` | `c1b35d9`, `cedfe96`, `dc03a44` |
| 11 | Internal detail kept out of responses (where fixed) | several, see below | `test_factcheck_source_replies.py`, `test_error_text.py`, `test_non_finite_inputs.py`, `test_factcheck_reply_limits.py` | `f7ecd55`, `9aff207`, `8e51a94`, `0011dd7`, `93dda14`, `83803b5` |
| 12 | Secret scanning (gitleaks) | `.pre-commit-config.yaml`, `.github/workflows/test.yml` | — | `fceeeac`, `1a9fbed`, `c661bad` |
| 13 | `.env` kept out of git and images | `.gitignore`, `.dockerignore` | — | — |

Test files without a path are in `app/insights/tests/`.

### 1. API-key authentication

These routes check the `X-API-Key` header: `/v1/insights/*`, `/v1/fact-check`
and `/v1/fact-check/{conversation_id}`, `/v1/process-audio` and
`/internal/v1/agent-brain/recommendations/generate`.
The check is a router dependency in each module
(`app/insights/api/insight_routes.py:35-43`,
`app/insights/api/factcheck_routes.py:46-53`,
`app/routes/process_audio.py:74-79`,
`app/agent_brain/api/agent_routes.py:25-33`). `/healthz` and `/version` are
public so health checks work (`app/main.py:71-82`, `app/insight_main.py:81-92`).

For POST, PUT and PATCH requests under these route prefixes, a request guard
runs the same check first, before the body is read, with the same status and
message. A refusal closes the connection, and the body never reaches the app
(`app/security/request_guard.py:38-48`, `:65-81`, `:113-118`). Both entry
points install it (`app/main.py:66-68`, `app/insight_main.py:76-78`). The
guard runs before routing, so an unauthenticated request with an invalid body
gets the authentication error rather than 422, and a body-carrying request to
an unknown path under a guarded prefix gets the authentication or size
error rather than 404 or 405
(`ff874de`).

What happens depends on the configuration (`app/security/api_key.py:7-13`, `:58-116`):

| Keys in `VOICEIQ_API_KEYS` | `VOICEIQ_ENVIRONMENT` | Result |
|---|---|---|
| one or more | any | missing header → 401, wrong key → 403 |
| none | `production` | every protected request → 503, logged as critical |
| none | `development` (the default), `staging` or `test` | request allowed, warning logged |

- The default environment is `development` (`app/insights/config/settings.py:52-55`).
  The Docker image sets `production` (`Dockerfile:60`).
- `VOICEIQ_API_KEYS` is a comma-separated list. Blank entries are dropped
  (`settings.py:169-190`).
- The presented key is compared in constant time against every configured
  key, and the check does not stop at the first match. Keys are compared as
  UTF-8 bytes, so a header with non-ASCII characters gets 403 like any other
  wrong key (`api_key.py:41-55`).

### 2. Rate limits on the expensive routes

| Route | Default | Setting |
|---|---|---|
| `POST /v1/process-audio` | 30/minute | `VOICEIQ_RATE_LIMIT_PROCESS_AUDIO` |
| `POST /v1/fact-check` | 60/minute | `VOICEIQ_RATE_LIMIT_FACTCHECK` |
| `POST /internal/v1/agent-brain/recommendations/generate` | 60/minute | `VOICEIQ_RATE_LIMIT_AGENT_BRAIN` |

- A request over the limit gets 429 with a `Retry-After` header
  (`app/security/rate_limit.py:60-74`).
- Limits are counted per caller. The bucket is the API key when keys are
  configured and the header holds one of them, and the client IP otherwise,
  so while authentication is off a header cannot choose its own bucket
  (`rate_limit.py:43-52`, `702d23d`).
- Counts are kept in each process's memory. Several workers or replicas
  each count separately (`rate_limit.py:36-40`).
- `VOICEIQ_RATE_LIMIT_ENABLED` defaults to `true`. Setting it to `false`
  turns all three limits off (`rate_limit.py:61-63`). Defaults are in
  `settings.py:136-162`. The limits are attached at `process_audio.py:140`,
  `factcheck_routes.py:90` and `agent_routes.py:31`.

### 3. Payload and upload size caps

- **JSON routes** (`/v1/insights/*`, `/v1/fact-check`, the Agent Brain
  route): `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB`, default 2048. The
  `Content-Length` header of POST, PUT and PATCH requests is checked
  (`payload_size.py:37`, `:63-70`). A missing header gets 411. A header
  that is not an integer, or is negative, gets 400. A declared length over
  the cap gets 413 (`payload_size.py:63-95`; wiring at `insight_routes.py:28-29`,
  `factcheck_routes.py:40-41`, `agent_routes.py:22-23`; `settings.py:91-99`).
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
  any way gets 413 (`process_audio.py:30-31`, `:170-220`; `app/main.py:68`;
  `settings.py:100-108`).
- Both caps are read when the module is imported. Restart to change them
  (`process_audio.py:26-30`, `insight_routes.py:26-28`).

### 4. Upload type checks

- Only `.mp3`, `.wav`, `.m4a` and `.flac` names are accepted. A missing
  filename or any other extension gets 400 with a fixed message
  (`app/utils/upload.py:14-36`, `process_audio.py:157-162`).
- The first bytes of the upload must look like WAV, FLAC, MP4/M4A or MP3. If
  they do not, the response is 415 and the partial file is deleted
  (`app/utils/audio_sniff.py:15-34`, `process_audio.py:186-193`, `:208-213`).

### 5. ffmpeg timeout

ffmpeg runs from an argument list with no shell and with `-nostdin`, under a
wall-clock timeout (`app/utils/audio_utils.py:61-95`). The timeout is
`VOICEIQ_FFMPEG_TIMEOUT_SEC`: default 120 seconds, allowed range 1–600
(`settings.py:109-122`). A timeout returns 422 with the warning
`AUDIO_NORMALIZATION_TIMEOUT`. Other normalisation failures return 400
(`process_audio.py:241-254`). On a timeout or failure the partly written
output file is deleted (`audio_utils.py:84-95`, `5696e32`).

### 6. Job-artifact retention

Each request writes its files (raw audio, transcript, PDF, insights) to
`data/jobs/<request id>/`. These folders are deleted after
`VOICEIQ_JOB_RETENTION_HOURS` (default 24; `0` turns deletion off). The sweep
runs at startup and at most once an hour during requests. If it fails, the
request still completes (`app/utils/job_io.py:127-168`, `app/main.py:30-38`,
`process_audio.py:33-51`, `:165`, `settings.py:123-133`). Rows in the SQLite
database are not swept (see [DATA-FLOW.md](DATA-FLOW.md), "Data retention").

### 7. API docs hidden in production

When `VOICEIQ_ENVIRONMENT=production`, `/docs`, `/redoc` and `/openapi.json`
are turned off in both entry points (`app/main.py:41-48`,
`app/insight_main.py:51-58`).

### 8. Opt-in egress

- **Fact-check enrichment in `/v1/process-audio`** runs only with
  `?fact_check=true` or `VOICEIQ_FACTCHECK_AUTO_ENRICH=true`.
  `?fact_check=false` always wins. When enrichment is off, no external call is
  made and `fact_checks_v2` holds a `disabled` stub
  (`app/utils/factcheck_gate.py:17-38`, `process_audio.py:256-261`,
  `settings.py:193-203`).
- **The Agent Brain stage** in `/v1/process-audio` runs only with
  `VOICEIQ_AGENT_BRAIN_AUTO_RUN=true` (`settings.py:205-216`,
  `app/pipeline/orchestrator.py:741-742`).
- **The callback** is off unless both its URL and its secret are set
  (`app/agent_brain/config/settings.py:26-28`,
  `java_callback_client.py:67-69`).
- **Fact-check source clients** call fixed hosts. Query values are passed
  through httpx `params`, so httpx encodes them
  (`app/insights/core/factcheck/source_clients/base_client.py:130-155`). The
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
  left out of the callback (`orchestrator.py:815-823`,
  `app/agent_brain/config/settings.py:19-24`).
- The send runs on a background thread. A failure is logged with the
  exception class and HTTP status only (`orchestrator.py:831-850`).
- The receiving service has to verify the v2 signature and enforce a
  freshness window. The contract is [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md).

### 10. Secrets kept out of logs (milestone M1)

- The `httpx` and `httpcore` loggers are pinned to WARNING
  (`app/utils/logger.py:4-15`). Their request URLs carry the OpenWeather and
  Alpha Vantage API keys and transcript-derived claim subjects, and are no
  longer logged (`c1b35d9`).
- A failed callback logs the exception class and HTTP status, never the
  exception text, which would include the callback URL
  (`orchestrator.py:836-846`, `cedfe96`).
- exchangerate.host errors log only the provider's error code and type
  (`app/insights/core/factcheck/source_clients/forex_client.py:73-82`,
  `dc03a44`).
- Source-client warnings carry the client name, attempt number, HTTP status
  or exception class (`base_client.py:158-200`). The authentication code
  never logs key values (`api_key.py:58-116`).
- A failed insight run, and a failed fact-check enrichment or fact-check
  save in `/v1/process-audio`, log the exception class only
  (`app/insights/service.py:87-89`, `process_audio.py:117-133`, `0011dd7`).
- `test_logging_hygiene.py` checks, for the OpenWeather and Alpha Vantage
  clients, that neither the key nor the claim subject reaches any log record.
  It also checks that importing the Docker entrypoint leaves both loggers at
  WARNING.
- A logging configuration applied after `app/utils/logger.py` is imported can
  lower these levels again (`logger.py:7-8`). Leave `httpx` and `httpcore`
  out of any such configuration.

### 11. Internal detail kept out of responses (where fixed)

- If saving the upload fails, the client gets a fixed message. The cause is
  logged on the server only (`process_audio.py:201-206`, `f7ecd55`).
- Filename errors are fixed strings (`upload.py:1-8`, `:29-34`).
- A failed insight run reports the fixed message "Insight pipeline failed."
  under the code `pipeline_execution_error`. A failed fact-check enrichment
  in `/v1/process-audio` gives `fact_checks_v2` the fixed reason
  "fact-check enrichment failed". Neither returns the exception text
  (`app/insights/service.py:87-99`, `process_audio.py:126-137`, `0011dd7`).
- A failing fact-check source client gives `SOURCE_UNAVAILABLE` for that
  claim, and for later claims about the same subject in the same run. For an unexpected exception the reason names only the exception
  class. A client that knows why it failed gives its own short reason
  (`app/insights/core/factcheck/factcheck_engine.py:250-269`). The client
  contract says those reasons must never contain URLs, keys, response bodies
  or claim text (`base_client.py:71-76`).
- Malformed provider replies give `SOURCE_UNAVAILABLE` per claim, never a
  verdict and never a 500. This covers non-finite numbers, JSON that is not
  an object, and bodies that cannot be decoded (`9aff207`, milestone M2).
  It also covers replies larger than 1 MiB once decompressed: the client
  stops reading at that point (`base_client.py:38-57`, `:155-160`,
  `83803b5`).
- The insight routes refuse JSON bodies that hold `NaN`, `Infinity` or a
  number such as `1e999` that overflows to infinity, with the standard 422 `json_invalid` error,
  before anything is stored (`app/insights/api/finite_json.py:21-49`,
  `insight_routes.py:35-38`, `93dda14`). Stored fact-check rows are read back
  with any non-finite number as null
  (`app/insights/repository/factcheck_repository.py:209-241`, `a712146`).
- The claim detector drops numbers with more than 15 digits
  (`app/insights/core/factcheck/claim_detector.py:193`, `:478`).
  `/v1/process-audio` checks the fact-check payload as strict JSON before it
  saves or returns it (`process_audio.py:112-115`, `8e51a94`, milestone M4).
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

### Also in place

- The container runs as a non-root user (`Dockerfile:63`, `Dockerfile:82`).
- Database access uses SQLAlchemy statement objects, never raw SQL strings
  (`app/insights/repository/insight_repository.py:122-181`,
  `app/insights/repository/factcheck_repository.py:157-197`).

---

## Secrets handling

| Secret | Variable | Read in |
|---|---|---|
| Caller API keys | `VOICEIQ_API_KEYS` | `app/insights/config/settings.py:169-190` |
| OpenWeather key | `VOICEIQ_OPENWEATHER_API_KEY` | `settings.py:217-223` |
| Alpha Vantage key | `VOICEIQ_ALPHAVANTAGE_API_KEY` | `settings.py:224-227` |
| Callback HMAC secret | `VOICEIQ_AGENT_CALLBACK_SECRET` | `app/agent_brain/config/settings.py:17` |
| Callback URL (a secret if it embeds a token) | `VOICEIQ_AGENT_CALLBACK_URL` | `app/agent_brain/config/settings.py:16` |
| Hugging Face token (full pipeline only) | `PYANNOTE_AUTH_TOKEN`, then `HUGGINGFACE_TOKEN` | `app/services/diarization_service.py:90` |
| Database credentials, if the URL has any | `VOICEIQ_DATABASE_URL` | `settings.py:62-68` |

This table lists only the settings that hold secrets. Every setting, with
its default, is in [SYSTEM.md §3](SYSTEM.md#3-configuration).

**Rules**

- Secrets come from environment variables. For local work, a `.env` file in
  the working directory is also read (`settings.py:35-41`,
  `app/agent_brain/config/settings.py:14`), and both entry points call
  `load_dotenv()` (`app/main.py:24`, `app/insight_main.py:37`). Start from
  `.env.example`, which holds placeholders only.
- Never commit `.env`. It is gitignored (control 13).
- Every key, token and callback setting defaults to empty or unset. An empty
  provider key turns that client off. An empty callback URL or secret turns
  the callback off (`settings.py:169-177`, `:217-227`,
  `app/agent_brain/config/settings.py:16-17`, `:26-28`,
  `diarization_service.py:90-93`).
- The service's own code does not write key values to its logs (control 10).
  Do not add log lines that print settings objects, source-client request
  URLs or httpx exception text.
- Use long random values for `VOICEIQ_API_KEYS`. The example values in
  `.env.example:80` are placeholders, not keys to deploy.
- Settings are cached once per process (`settings.py:241-249`,
  `app/agent_brain/config/settings.py:31-33`). A rotated key takes effect
  only after a restart.
- Each cold load of the diarization model calls `huggingface_hub.login()`
  (`diarization_service.py:99`). Per [DATA-FLOW.md](DATA-FLOW.md) §3, that
  call sends the token to huggingface.co and writes it to the local Hugging
  Face cache. Protect that directory as you would the `.env` file.

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
- Only the three routes in control 2 have application rate limits, and each
  process keeps its own counts. Enforce rate limits at the reverse proxy as
  well.
- All API keys have the same access. Any valid key can read, replace or
  delete any stored record. Treat everyone who holds a key as one trust
  domain.
- Authentication and the API docs depend on the environment label. With no
  keys configured, every environment except `production` lets requests
  through, and only `production` hides the docs. For any shared or networked
  deployment, set `VOICEIQ_ENVIRONMENT=production` and `VOICEIQ_API_KEYS`.
  Also check that no env file overrides the environment that the image sets.
- Callback replay protection depends on the receiving service verifying the
  v2 signature with a freshness window. The legacy v1 signature is still
  sent until that migration is finished
  ([JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md)).
- Some responses still include internal error text or server-side paths. Do
  not pass API responses straight to end users.
- Records in the SQLite database stay until an operator removes them, and
  the service does not encrypt them. Protect the data volume.
- Audio processing is CPU- and memory-heavy, and the application's own
  limits on it are coarse. Each worker runs one audio job at a time, and
  further uploads wait their turn. Give the full-pipeline process CPU and
  memory limits, and keep it apart from latency-sensitive services.

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
