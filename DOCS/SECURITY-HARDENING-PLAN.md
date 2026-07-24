# Security Hardening Plan (Tier S)

Owner: engineering · Status: **planned** · Created: 2026-07-17

Outcome of a defensive-security + logic review of the VoiceIQ service. Core
AppSec (injection, secrets, authn, SSRF) is already solid — see
[§ Verified clean](#verified-clean-no-change-needed). The gaps are
**operational and data-governance**, plus a few low-risk hardening fixes.

This document is the work plan. Each item below is self-contained: scope,
files, approach, acceptance criteria, and tests. Work proceeds one item per
commit, in the wave order in [§ Sequencing](#sequencing). Nothing here changes
the pipeline's behavioral contract — the `test_orchestrator.py` harness (51
tests, 100%) stays green throughout.

---

## Findings register

| ID | Severity | Title | Type |
|----|----------|-------|------|
| S1 | Medium | No rate limiting on any endpoint | DoS / quota-exhaustion |
| S2 | Medium | No job-artifact retention (disk growth + PII persistence) | DoS / privacy |
| S3 | Medium | Fact-check exfiltrates transcript fragments to 3rd parties, silently | data governance |
| S4 | Low | Raw exception detail leaked to client (500) | info disclosure |
| S5 | Low | `file.filename is None` → unhandled 500 | robustness |
| S6 | Low | Unencoded value in Wikipedia URL path | request integrity |
| S7 | Low | HMAC callback replayable; `callback_url` scheme unchecked | integrity |
| S8 | Low | `/docs` + `/openapi.json` public in production | info disclosure |

Severity = likelihood × impact in this deployment (internal service, API-key
gated). None are remote-unauth RCE-class; the Mediums are the ones that block
a "production-grade" claim.

---

## Item specs

### S1 — Rate limiting  *(Medium)*

**Why.** Zero throttle. `POST /v1/process-audio` runs heavy ML + disk writes
per call; `_auto_run_factcheck` + `POST /v1/fact-check` fan out to five
external APIs. A single valid key (or dev mode) can exhaust CPU/disk or burn
the OpenWeather/AlphaVantage quotas and get the host IP banned upstream.

**Files.** new `app/security/rate_limit.py`; wire into `app/routes/process_audio.py`
+ `app/insights/api/factcheck_routes.py`; new settings fields in
`app/insights/config/settings.py`.

**DECISION (confirm at step start).** Limiter implementation:
- **(a) slowapi** — battle-tested FastAPI limiter (adds `slowapi`+`limits`, both
  pure-python/light; goes in `requirements-insight.txt`). *Recommended.*
- **(b) in-proc token bucket** — ~40 LOC, zero new deps, per-process only (no
  shared state across workers). Fine for single-worker/dev; weaker under
  horizontal scale.

**Approach.** Key the limit on the presented API key, falling back to client IP.
Per-endpoint limits from settings (env-overridable), tighter on process-audio
(e.g. `10/minute`) and fact-check (e.g. `20/minute`) than insight reads. A
test-mode switch (`rate_limit_enabled=False`) so the existing 503 suite is
unaffected.

**Acceptance.** N+1-th request in the window → **429** with `Retry-After`;
limits configurable via env; disabled in tests; no change to the 503-test suite.

**Tests.** `app/insights/tests/test_rate_limit.py` — burst past limit → 429;
under limit → 200; disabled flag → no limiting; per-key isolation.

---

### S2 — Job-artifact retention  *(Medium)*

**Why.** Every request writes `data/jobs/<uuid>/` (raw audio, `transcript.txt`,
`whisper.json`, PDF, insights) and nothing deletes it. Two problems: disk fills
→ crash-DoS; and raw call audio + full transcripts persist on disk **with no
TTL** — a privacy gap under the same rule the logging correctly honors.

**Files.** `app/utils/job_io.py` (add `purge_expired`); `app/main.py` +
`app/insight_main.py` (sweep on startup); settings field
`job_retention_hours`.

**Approach.** `JobIO.purge_expired(max_age_hours)` removes job dirs whose
`meta.json` mtime (or dir mtime) is older than the TTL, fail-soft per dir.
Call once on startup (lifespan) and opportunistically at the start of
`process_audio` (bounded: at most one sweep per configurable interval, so it
never adds latency to the hot path). Default TTL from settings (e.g. 24h);
`0` disables. Document the retention policy in `DOCS/`.

**Acceptance.** Dirs older than TTL removed on sweep; TTL configurable; `0`
disables; sweep never raises into the request path.

**Tests.** `test_job_retention.py` — create dirs with backdated mtimes → sweep
removes only the expired ones; TTL=0 → no-op; unreadable dir → skipped, no raise.

---

### S3 — Fact-check data governance  *(Medium, behavior change)*

**Why.** Claim subjects (city, country, currency, asset) parsed from user
transcripts are sent to CoinGecko / OpenWeather / AlphaVantage / Wikipedia /
exchangerate.host. It is auto-run on **every** `/v1/process-audio`
(`process_audio.py:180`), so call-content leaves the trust boundary with no
consent gate.

**Files.** `app/routes/process_audio.py` (gate `_auto_run_factcheck`); settings
flag `factcheck_auto_enrich`; new `DOCS/DATA-FLOW.md`.

**DECISION (confirm at step start).** Default posture:
- **(a) opt-in** — `factcheck_auto_enrich=False` default; caller sets
  `?fact_check=true` or flips the env flag. Privacy-first. *Recommended.*
- **(b) opt-out** — keep current default-on, add `?fact_check=false` to disable.
  Preserves existing behavior; weaker default.

**Approach.** Wrap the auto-run in the flag + a per-request query override.
`DOCS/DATA-FLOW.md` enumerates, per source, exactly which claim fields are
transmitted and to which host, plus which sources need API keys.

**Acceptance.** Flag/param off → **zero external fact-check calls**;
`fact_checks_v2` reports `status="disabled"`; flag/param on → current behavior.
Data-flow doc merged.

**Tests.** `test_process_audio_factcheck_gate` — flag off → external client
never called (assert with mock); on → called once. (Extends the existing route
tests; no live network.)

---

### S4 — Error-detail leak  *(Low)*

**File.** `app/routes/process_audio.py:139`.
**Approach.** Replace `detail=f"Failed to save upload: {e}"` with a generic
client message; `logger.exception(...)` the real error server-side.
**Acceptance.** 500 response body contains no exception text / filesystem path.
**Test.** Force a write error → assert body is the generic string.

---

### S5 — `filename is None` guard  *(Low)*

**File.** `app/routes/process_audio.py:97`.
**Approach.** Guard `file.filename` before `.lower()`; `None`/empty → 400
"Missing filename". (Currently → `AttributeError` → unhandled 500.)
**Acceptance.** Multipart part with no filename → 400, not 500.
**Test.** Upload without filename → 400.

---

### S6 — URL-encode Wikipedia title  *(Low)*

**File.** `app/insights/core/factcheck/source_clients/static_facts_client.py:88`.
**Approach.** `title = urllib.parse.quote(country.strip().replace(" ", "_"), safe="")`
before concatenation. Host is fixed + redirects off (not SSRF), but encoding
prevents `?`/`#`/`/` in a country string from mangling the request.
**Acceptance.** Title with a space / `?` / slash → single encoded path segment.
**Test.** Feed a claim whose country contains `?` → the requested URL is encoded.

---

### S7 — HMAC callback replay + scheme  *(Low)*

**File.** `app/agent_brain/integrations/java_callback_client.py`.
**Approach.** Add an `X-VoiceIQ-Timestamp` header **included in the signed
material** (sign `timestamp + "." + body`), so Java can reject stale/replayed
calls; require `callback_url` to be `https://` (warn + refuse otherwise).
**Acceptance.** Signature covers the timestamp; non-https callback URL is
rejected before send.
**Test.** `test_java_callback` — signature changes with timestamp; http URL →
send refused. (Coordinate the signing scheme with the Java side before merge.)

---

### S8 — Gate docs in production  *(Low, optional)*

**Files.** `app/main.py`, `app/insight_main.py`.
**Approach.** When `environment == "production"`, construct `FastAPI(docs_url=None,
redoc_url=None, openapi_url=None)` (or gate behind the API key). Dev keeps docs.
**Acceptance.** In production, `/docs` + `/openapi.json` → 404; dev unchanged.
**Test.** App built with `environment=production` → those routes 404.

---

## Sequencing

Grouped by risk/coupling. Each wave is independently shippable; every commit is
green on its own.

| Wave | Items | Rationale | Deps |
|------|-------|-----------|------|
| **S-A** | S4, S5, S6 | Pure low-risk fixes + tests, no design calls, no deps | none |
| **S-B** | S2 | Retention — self-contained, adds settings + sweeper | none |
| **S-C** | S1 | Rate limiting — needs the (a)/(b) dep decision | maybe `slowapi` |
| **S-D** | S3 | Data governance — behavior change + `DATA-FLOW.md` | none |
| **S-E** | S7, S8 | Callback replay + docs gating (S7 needs Java-side coordination) | none |

Recommended first pass: **S-A + S-B** (all local, zero design risk), then S-C,
then S-D, then S-E.

---

## Cadence (unchanged from Tier 3 / Sprint 6)

Per commit, all via the venv interpreter (`.venv\Scripts\python.exe -m ...`):
- new/updated tests for the item, covering the fix + its failure branch
- full suite green (`app/insights/tests/ app/agent_brain/tests/`), harness 51/51
- `ruff check` clean, `mypy app/insights/ app/agent_brain/` = 0
- ≥95% coverage on touched modules
- atomic commit, no squash; stop-on-fail; local until reviewed, then pushed
- CI (4 jobs) green before the wave is called done

---

## Documentation deliverables

- **This plan** — living; each item marked done as it lands.
- **`DOCS/DATA-FLOW.md`** (S3) — what leaves the trust boundary, to where.
- **Retention policy note** (S2) — TTL default + how to configure.
- **`CLAUDE.md`** — add a "Tier S — security hardening" row to the sprint table
  and record the retention + rate-limit config knobs under "Working with this repo".
- **`CHANGELOG.md`** (if started) — one line per item.
- Commit messages carry the per-item rationale (as with Tier 3 / E2).

---

## Verified clean (no change needed)

Recorded so the review scope is auditable:

- **SQL injection** — none; all SQLAlchemy ORM statements, parameterized.
- **Command injection** — ffmpeg is list-form, no `shell=True`, server-controlled paths.
- **SSRF** — fixed hosts; values via httpx `params=` (auto-encoded); `follow_redirects=False`.
- **Secrets** — none hardcoded (`callback_secret`/API keys default empty); `.env` gitignored and **never in git history**.
- **API-key compare** — no key-position short-circuit; prod **fails closed** (503) without keys.
- **Uploads** — extension allowlist + magic-byte sniff (415) + streamed size cap (413) + ffmpeg timeout (422).
- **Logging** — transcript content never logged (counts only). No `eval`/`pickle`/`yaml.load`/`verify=False`.

---

## Out of scope (this tier)

- Larger ASR/diarization models for accuracy (product decision).
- Sprint 6 NLP Phase 2 (dateparser, multilingual).
- E3 alignment `O(n²)` (profiled, below the bar).
- Purging old bytecode/secrets from deep git history (Tier 4 candidate).
