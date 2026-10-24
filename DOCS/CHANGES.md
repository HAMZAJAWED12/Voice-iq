Last verified: 2026-10-24, 670b4de
# Changelog

What changed in VoiceIQ, backfilled from git history. Newest first, grouped
by sprint, tier or milestone. Each row is a one-line summary with the short
commit hash(es). `git show <hash>` gives the full message and diff.

This file records what each commit did. It is not a status page:

- Where Sprint 7 stands now, and the next milestone:
  [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).
- The status of every Sprint 7 decision: the register in
  [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
  Decisions already in force: [DECISIONS.md](DECISIONS.md).
- Current security controls: [SECURITY.md](SECURITY.md). Every outbound
  call: [DATA-FLOW.md](DATA-FLOW.md). How the parts fit:
  [ARCHITECTURE.md](ARCHITECTURE.md).

## Counts

Taken from git at write time.

| Measure | Value | Command |
|---|---|---|
| Commits reachable from HEAD (`670b4de`) | 221 | `git rev-list --count HEAD` |
| Non-merge commits | 217 | `git rev-list --count --no-merges HEAD` |
| Merge commits | 4 (`032d822`, `49063bb`, `6e92898`, `4025199`) | `git rev-list --count --merges HEAD` |
| Commits on `origin/main` (`4025199`) | 220 | `git rev-list --count origin/main` |
| Commits merged from `sprint7/doc-sync` by pull request #1 | 72 | `git rev-list --count 8fd3972..aa453f0` |
| Commits merged from `sprint7/phase0-decisions` by pull request #2 | 2 | `git rev-list --count 49063bb..415dd88` |
| Commits merged from `sprint7/diarization-fix` by pull request #3 | 2 | `git rev-list --count 6e92898..378d748` |
| First commit | `f21300a`, 2026-02-10 | `git log --reverse --format="%h %ad" --date=short` |

Each of the 221 commits is listed in exactly one row below; a few hashes
also appear elsewhere as cross-references. Small commits of the same kind
(wave-completion notes, a batch of stage extractions) share a row. Sections
are ordered by their most recent commit. Dates are author dates.

| Section | Dates | Commits |
|---|---|---|
| [Diarization login removal on `sprint7/diarization-no-login`](#diarization-login-removal-on-sprint7diarization-no-login) | 2026-10-24 | 1 |
| [Diarization fix on `sprint7/diarization-fix`](#diarization-fix-on-sprint7diarization-fix) | 2026-10-24 | 3 |
| [Phase 0 sign-off on `sprint7/phase0-decisions`](#phase-0-sign-off-on-sprint7phase0-decisions) | 2026-10-19 to 2026-10-20 | 3 |
| [Sprint 7 on `sprint7/doc-sync`](#sprint-7-on-sprint7doc-sync) | 2026-10-11 to 2026-10-19 | 73 |
| [Sprint 7 Phase 0 and prerequisites](#sprint-7-phase-0-and-prerequisites) | 2026-09-04 to 2026-09-20 | 7 |
| [N4b multilingual and L5 pipeline wiring](#n4b-multilingual-and-l5-pipeline-wiring) | 2026-09-19 to 2026-09-20 | 10 |
| [Tier 3 Wave E](#tier-3-wave-e) | 2026-06-21 to 2026-09-19 | 34 |
| [Tier N](#tier-n) | 2026-07-25 | 12 |
| [Tier S](#tier-s) | 2026-07-25 | 14 |
| [Sprint 6: Agent Brain](#sprint-6-agent-brain) | 2026-06-21 to 2026-06-22 | 15 |
| [Tier 3 Waves A to D](#tier-3-waves-a-to-d) | 2026-06-14 to 2026-06-21 | 13 |
| [Tier 2](#tier-2) | 2026-06-01 to 2026-06-14 | 7 |
| [Tier 1](#tier-1) | 2026-05-20 to 2026-05-23 | 6 |
| [Tooling, first CI and Docker](#tooling-first-ci-and-docker) | 2026-04-25 to 2026-05-20 | 12 |
| [Sprint 5: fact-check engine](#sprint-5-fact-check-engine) | 2026-05-03 | 2 |
| [Initial code and Sprints 1 to 4](#initial-code-and-sprints-1-to-4) | 2026-02-10 to 2026-04-25 | 9 |

## Diarization login removal on `sprint7/diarization-no-login`

1 commit, 2026-10-24, on a branch cut from `main` at `4025199`; not merged.

| Date | Change | Commit |
|---|---|---|
| 2026-10-24 | The diarization service no longer calls `huggingface_hub.login()` on each cold load: the token still goes to pyannote with the load, nothing writes it to disk, and with the models cached diarization loads offline (`HF_HUB_OFFLINE=1`). 3 new tests. | `670b4de` |

## Diarization fix on `sprint7/diarization-fix`

3 commits, 2026-10-24 (2 from the branch plus the merge commit). Pull
request #3 merged them into `main` on 2026-10-24.

| Date | Change | Commit |
|---|---|---|
| 2026-10-24 | Diarization loads `pyannote/speaker-diarization-3.1` with PyTorch's weights-only loader on: the six classes its checkpoints store are allowed by name. `requirements.txt` declares matplotlib, which pyannote.audio 3.3.2 imports without declaring. A run whose pipeline never loaded carries `DIARIZATION_UNAVAILABLE`, and the mock reads the duration from the file header instead of loading the audio. 10 new tests (DECISIONS #53). | `9887e0b` |
| 2026-10-24 | The diarization fix is recorded and the test counts refreshed. Docs only. | `378d748` |
| 2026-10-24 | Pull request #3 merges `sprint7/diarization-fix` into `main` (merge commit; the tree equals `378d748`). | `4025199` |

## Phase 0 sign-off on `sprint7/phase0-decisions`

3 commits, 2026-10-19 to 2026-10-20 (2 from the branch plus the merge
commit). Pull request #2 merged them into `main` on 2026-10-20.

| Date | Change | Commit |
|---|---|---|
| 2026-10-19 | The Phase 0 sign-off is recorded; Phase 1 may start. Docs only. | `e4398b1` |
| 2026-10-20 | The knowledge docs are brought up to date with the merge and Phase 0. Docs only. | `415dd88` |
| 2026-10-20 | Pull request #2 merges `sprint7/phase0-decisions` into `main` (merge commit; the tree equals `415dd88`). | `6e92898` |

## Sprint 7 on `sprint7/doc-sync`

73 commits, 2026-10-11 to 2026-10-19 (72 from the branch plus the merge
commit): a documentation sync, prerequisite
fixes that need no pending decision (one per milestone), security and
reliability fixes, the owner-decided changes that followed them, and the
fix round after the pull-request review. Pull request #1 merged them into
`main` with a merge commit on 2026-10-19 (`49063bb`). Why
the work is shaped this way: "In progress" in the
[status doc](FACT-CHECK-AGENT-STATUS.md#in-progress).

### Pull-request review fix round

The owner approved these on 2026-10-18 after two independent reviews of the
pull request. Each code fix is its own commit with its own tests.

| Date | Change | Commit |
|---|---|---|
| 2026-10-19 | Whole-word rules list the forms they would otherwise miss: email triggers "send an", "send/share/forward these, those, them"; flags "umm", "uhh", "erm", "ahh", "crappy", "stupidity", "idiots", "hates", "hated"; intents "apologized", "upsetting". The email title drops a leading article only as a whole word. "Send an engineer" is an email suggestion too, accepted by the owner (DECISIONS #32). | `ee2e15b` |
| 2026-10-19 | Fact-check provider requests ask for uncompressed replies; a reply in any other content encoding is refused before its body is read, so the 1 MiB cap counts the bytes that arrive. | `efdd1a9` |
| 2026-10-19 | Doc claims brought in line with the code (fact-check egress, when the process-audio 503 and 429 come, the run deadline, the reply cap, the callback replay check, deployment requirements); the round recorded. Docs and comments only. | `aa453f0` |
| 2026-10-19 | The test suite never reads `.env` and uses a temporary database and job folder (`app/conftest.py`). Test-only. | `8563572` |

### Merge into `main`

| Date | Change | Commit |
|---|---|---|
| 2026-10-19 | Pull request #1 merges `sprint7/doc-sync` into `main` (merge commit; the tree equals `aa453f0`). | `49063bb` |

### Milestones M1 to M7

| Milestone | Date | Change | Commit |
|---|---|---|---|
| M7 | 2026-10-17 | Keyword rules match whole words only: the flag rules, the intent rules and the email-draft triggers no longer match a keyword inside a longer word ("um" in "number", "send a" in "send all"). New helper `app/utils/text_match.py`; the other Agent Brain agents keep substring matching; no vocabulary changed (forms added later in `ee2e15b`). 49 new tests. | `dbae452` |
| M6 | 2026-10-17 | Text outside Latin-1 no longer fails the PDF: common punctuation maps to Latin-1, anything else prints as "?" (`app/services/pdf_service.py`, `to_latin1`). 6 new tests. Also listed under hardening below. | see below |
| M5 | 2026-10-16 | The claim detector stops reading names, ages and counts as prices. A crypto, stock or commodity number followed by what it counts or measures, or by another currency, is dropped; a subject that is also a name or an everyday word, or a stock ticker not in capitals, needs price context (`_keep_match`, `app/insights/core/factcheck/claim_detector.py:385-467`). "1 USD = 280 PKR" is read as a rate; "95k", "24 hundred" and "1.2 million" scale the value. Every detector pattern scans in linear time. No v1 model change. 423 new tests. | `1dbfac7` |
| M4 | 2026-10-16 | Over-long numbers in a transcript no longer cause a 500 on either endpoint. The claim detector drops a claim whose number has more than 15 digits (`MAX_VALUE_DIGITS`, now `app/insights/core/factcheck/claim_detector.py:193`). `/v1/process-audio` checks the fact-check payload as strict JSON before saving or returning it, and falls back to the failed stub (now `app/routes/process_audio.py:101-104`). 35 new tests. | `8e51a94` |
| Tooling | 2026-10-16 | The trailing-whitespace, end-of-file-fixer and check-merge-conflict hooks run through `python -m` in a `repo: local` block of `.pre-commit-config.yaml`, because Windows Application Control blocks the generated `.exe` launchers on some machines. Same hooks, same behaviour. | `7b25b3f` |
| M3 | 2026-10-16 | exchangerate.host error replies are named instead of failing silently. The provider now requires an access key and none is configured, so currency-rate claims end as `SOURCE_UNAVAILABLE` with that reason; the log carries only the provider's error code and type. Provider unchanged. 11 new tests. | `dc03a44` |
| M2 | 2026-10-11 | Bad provider replies (non-finite numbers, JSON that is not an object or cannot be decoded, wrong nested types, over-deep metadata) end that one claim as `SOURCE_UNAVAILABLE`, never a verdict or a 500. The engine isolates each claim. No v1 model change. Tests in `app/insights/tests/test_factcheck_source_replies.py`. | `9aff207` |
| M1 | 2026-10-11 | Outbound request URLs kept out of the logs: `setup_logging()` pins the `httpx` and `httpcore` loggers to WARNING (`app/utils/logger.py`). Follow-up: a failed Java callback logs the exception class and HTTP status only. Tests in `app/insights/tests/test_logging_hygiene.py`. | `c1b35d9`, `cedfe96` |

Next: Phase 0 is complete (signed 2026-10-19) and Phase 1 may start.
See [Next milestone](FACT-CHECK-AGENT-STATUS.md#next-milestone).

### Status-document updates

| Date | Change | Commit |
|---|---|---|
| 2026-10-18 | WORKFLOW.md says where test counts are recorded; WORKFLOW.md and CLAUDE.md name only CLAUDE.md as holding the repository URL. | `a2f3105` |
| 2026-10-18 | WORKFLOW.md branch rows match the pushed branch. | `99006c0` |
| 2026-10-18 | WORKFLOW.md says where each check ran: local, reduced local and CI. | `16654b5` |
| 2026-10-17 | M7 and the owner-decided changes recorded; test counts refreshed. | `2966527` |
| 2026-10-17 | M6 and the security and reliability fixes recorded; M7 promoted; test counts refreshed. | `42382db` |
| 2026-10-16 | Owner decisions recorded: M5 choices accepted, no new detector heuristics until Phase 3, size rule; M6 and the old-rows fix approved; reporting steps that work either way. | `43a9c1b` |
| 2026-10-16 | M5 recorded as done; M6 promoted; detector limits documented; test counts refreshed. | `10b0039` |
| 2026-10-16 | M4 recorded as done; M5 promoted to next milestone; test counts refreshed. | `793e02e` |
| 2026-10-16 | M3 recorded; the forex-provider question and its options written down; the over-long-number case promoted to M4. DATA-FLOW.md: exchangerate.host needs a key and none is configured. | `2350738` |
| 2026-10-11 | M2 recorded as done; M3 promoted; test counts refreshed. | `b370390` |
| 2026-10-11 | The callback follow-up (`cedfe96`) added to the M1 record. | `0aec936` |
| 2026-10-11 | M1 recorded as done; M2 promoted. DATA-FLOW.md notes that request URLs are not logged. | `b1054be` |

### Security and reliability hardening

Fixes from a security and reliability review (its detailed list is tracked
privately), plus M6 and the approved old-rows fix, which landed in the same
run. Each code fix is its own commit with its own regression tests.

| Date | Change | Commit |
|---|---|---|
| 2026-10-17 | `purge_expired` counts only job directories that were actually removed and logs the rest. | `3bf7506` |
| 2026-10-17 | Agent Brain title comparison checks cheap upper bounds before the full ratio; verdicts unchanged. | `98a319a` |
| 2026-10-17 | CI verifies the gitleaks archive against the release checksums before installing it. | `c661bad` |
| 2026-10-17 | ffmpeg runs with `-nostdin`, and a failed run deletes its partial WAV. | `5696e32` |
| 2026-10-17 | The SQLite parent directory is created from SQLAlchemy's parsed URL, so absolute and driver-qualified URLs work. | `718d763` |
| 2026-10-17 | Fact-check provider replies are read up to 1 MiB of decoded body; a subject whose fetch failed is fetched once per run. | `83803b5` |
| 2026-10-17 | Fixed public messages for a failed insight run and a failed fact-check enrichment; logs carry the exception class only. | `0011dd7` |
| 2026-10-17 | The fact-check repository returns stored non-finite numbers as null (the approved old-rows fix). | `a712146` |
| 2026-10-17 | Agent Brain keeps one review per fact-check claim (the Agent Brain dedup prerequisite). | `8103360` |
| 2026-10-17 | M6: text outside Latin-1 no longer fails the PDF. | `dc9caf3` |
| 2026-10-17 | The staging recipe in DOCS/08-deployment.md sets an API key (completes `702d23d`). | `395f6ff` |
| 2026-10-16 | Rate-limit buckets use only accepted API keys, else the client IP. | `702d23d` |
| 2026-10-16 | Insight routes and models accept only finite numbers. | `93dda14` |
| 2026-10-16 | The fact-check evidence cache is safe across threads. | `1d967c5` |
| 2026-10-16 | `/v1/process-audio` runs the pipeline in a worker thread, one run at a time per worker (the event-loop part of the process-audio concurrency prerequisite). | `2bb6593` |
| 2026-10-16 | API key and declared size are checked before a request body is read; the bytes that arrive are counted. | `ff874de` |

### Owner-decided security and reliability changes

**Insight scores change for processed audio.** From `98730c3`, pipeline
sentiment reaches the insight engines as positivity instead of the model's
confidence in its label, so scores, sentiment-shift markers and summaries
built from `/v1/process-audio` differ from earlier runs on the same audio.
`/v1/insights` callers that send their own scores are unaffected.

The project owner decided these on 2026-10-17. Each is its own commit with
its own tests; they are DECISIONS #34 to #49 (#33 was recorded without code).

| Date | Change | Commit |
|---|---|---|
| 2026-10-17 | Currency rates from open.er-api.com: keyless, only the base currency is sent, each base fetched at most hourly, attribution on every evidence record. DATA-FLOW.md and the PHASE-0 §6.4 source policy updated in the same commit. | `45a5b22` |
| 2026-10-17 | `VOICEIQ_LOG_LEVEL` is applied at startup (`httpx` and `httpcore` stay at WARNING); `VOICEIQ_THRESHOLD_PROFILE` is marked not implemented and logs a startup warning when set. | `e25a3b2` |
| 2026-10-17 | `docker-compose.yml` caps the container at 2 CPUs and 1 GB; TLS terminates at the ingress (SECURITY.md). | `fb56d7d` |
| 2026-10-17 | `GET /readyz`: database reachable and, on the full service, ffmpeg present; 503 naming the failed check otherwise. `/healthz` unchanged. | `660558c` |
| 2026-10-17 | A run that used mock diarization carries `MOCK_DIARIZATION_USED`; the two-speaker default is unchanged. | `2721a73` |
| 2026-10-17 | Pipeline sentiment reaches the insight engines as positivity (see the note above). | `98730c3` |
| 2026-10-17 | A stage whose package cannot be imported fails softly with its `*_FAILED` warning instead of the whole request. | `0f9c4c1` |
| 2026-10-17 | An insight error result marks the pipeline stage failed, adds `INSIGHTS_FAILED` and skips the Agent Brain. | `c53c71d` |
| 2026-10-17 | An error result never replaces a stored ok or warning insight record. | `f8bc3ed` |
| 2026-10-17 | Insight and fact-check records are deleted after `VOICEIQ_DB_RETENTION_DAYS` (30); the retention sweep moved to the app lifespans, at startup and hourly. | `cbdafb4` |
| 2026-10-17 | A fact-check run checks at most 25 claims and starts no check after 20 s; the rest are counted as skipped. | `da92ae4` |
| 2026-10-17 | Insight writes 60/minute and reads 120/minute per caller, each route group with its own counter; `GET /v1/insights/` takes `limit` and `offset`. | `e802fcd` |
| 2026-10-17 | Audio longer than `VOICEIQ_MAX_AUDIO_SECONDS` (7200) gets 413 `AUDIO_TOO_LONG`: ffprobe before decoding, an ffmpeg hard stop, never truncated. A fifth hard-fail gate. | `6c17c72` |
| 2026-10-17 | `/v1/process-audio` admits one running and two waiting uploads per worker by default, then answers 503 with `Retry-After: 30`. | `948c49d` |
| 2026-10-17 | Staging fails closed without API keys; compose publishes the port on loopback only. | `d7f9075` |
| 2026-10-17 | A failed topic step returns a fixed message; the exception text stays in the job's status file. | `dc97a84` |

### Housekeeping

| Date | Change | Commit |
|---|---|---|
| 2026-10-17 | CI's mypy gate also covers `app/utils/` and `app/security/`; WORKFLOW.md and SYSTEM.md say so. | `6aa8990` |
| 2026-10-17 | Code comments corrected for the fifth hard-fail gate and `/readyz`. No code change. | `5095cde` |
| 2026-10-17 | A test comment describes the callback trace id accurately. No code change. | `427feed` |
| 2026-10-16 | Stale code comments corrected (payload cap, callback trace id, rate-limit storage); notes about old git history reworded; `.git-blame-ignore-revs` points at the current hash of the bulk-format commit; DECISIONS entry 17 marked as followed in practice with formal approval pending. No code change. | `8f09739` |

### Project knowledge set

Both on 2026-10-16.

| Change | Commit |
|---|---|
| DOCS/00-index.md lists the knowledge set; CLAUDE.md links it and adds the rule for which docs each milestone updates; the status doc points to it. | `50254f6` |
| Nine knowledge docs added: DECISIONS, WORKFLOW, CHANGES, EXPLANATION, FEATURES, SECURITY, ARCHITECTURE, SYSTEM and CONTEXT, each checked against code, tests and git. | `16a7c1e` |

### Documentation sync

All on 2026-10-11.

| Change | Commit |
|---|---|
| Leftover stale statements fixed in `08-deployment.md`, `.env.example`, JAVA-CALLBACK-V2.md, the N4b and Tier N plans (stale line references) and orchestrator docstrings. No code change. | `3d970bf` |
| DATA-FLOW.md: model-download and fact-check egress facts corrected. | `6702ed6` |
| `.env.example`: settings it was missing added as placeholders with their code defaults. | `25925b0` |
| README: boot, egress, test-count and CI claims corrected; status doc linked. | `9a997c5` |
| CLAUDE.md: stale items refreshed; it now links the status doc and the register instead of copying decision status. | `d948252` |
| DOCS/00 to 10 marked as the Sprint 1 to 4 snapshot with a banner; only actively harmful lines corrected (environment values that fail startup, an incomplete install list, a bare `pytest` command that aborts, a production example without API keys). | `8f58a78` |
| FACT-CHECK-AGENT-STATUS.md added: the live Sprint 7 status document. | `6c8c89b` |
| PHASE-0: proposed wording to add `ClaimSpan` to the v1 freeze list. Whether it applies: see the status doc. | `51cf75b` |
| PHASE-0: proposed per-phase reading of the Phase 0 gate. Whether it applies: see the status doc. | `c3f0e53` |
| PHASE-0: stale facts corrected. No decision changed. | `ace45e4` |

## Sprint 7 Phase 0 and prerequisites

7 commits on `main`, 2026-09-04 to 2026-09-20. Phase 0 writes contracts and
decisions only, no application code. The rows below say which register
entries a commit touched; the outcome and current status of each decision
are in the [register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).

| Date | Change | Commit |
|---|---|---|
| 2026-09-20 | [D3-GOLD-SET-BRIEF.md](D3-GOLD-SET-BRIEF.md): scoping brief for decision D3 (the evaluation gold set), checked against the codebase. A recommendation. | `8fd3972` |
| 2026-09-20 | README brought up to date with shipped work: the Agent Brain pipeline stage, language-aware extraction, the rest of Tier S, the egress posture and a planned-work section. | `6f692e6` |
| 2026-09-20 | Register update for D10 (what the fact-check job re-runs when it finishes); D2's wording widened from "the PDF" to "downstream consumers". No code change. | `dacc926` |
| 2026-09-19 | The Agent Brain verdict map loses its default: an unmapped verdict now raises instead of becoming `UNVERIFIED`. Pinned by `test_verdict_map_is_total`. No change for current verdicts. | `1cd5350` |
| 2026-09-19 | Tests pin what `create_all()` can and cannot change on an existing database (`app/insights/tests/test_db_schema_evolution.py`), the behaviour the new-tables-only rule relies on. | `ff84664` |
| 2026-09-04 | Register update for D1 (persistence) and D2 (execution mode), with D2's contract consequences. | `2a539d3` |
| 2026-09-04 | Phase 0 contract and scope lock: [FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md) with the decision register (D1 to D9), the v1 contract recorded verbatim and marked frozen, a v2 contract specification, and compatibility, verdict-mapping, privacy, source and configuration policies. | `c6284b6` |

## N4b multilingual and L5 pipeline wiring

10 commits, 2026-09-19 to 2026-09-20. Plan:
[N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md). N4b is
Urdu and Arabic support in the Agent Brain's extraction; L5 gave the
pipeline adapter a production caller.

| Date | Change | Commit |
|---|---|---|
| 2026-09-20 | Docs: L5 closed in the N4b strategy; D10 added to the Phase 0 register; CLAUDE.md updated (L5-4). | `17606d6` |
| 2026-09-20 | Tests prove `PipelineAdapter.to_context` is called from the production pipeline and the detected language reaches the agents (L5-3). | `1d8f2f6` |
| 2026-09-20 | The pipeline's Agent Brain stage sends the signed Java callback without blocking the response (daemon thread); `min_confidence` filters the callback only, not the API response; off unless both callback URL and secret are set (L5-2). | `f619f7d` |
| 2026-09-20 | The Agent Brain runs as an opt-in `/v1/process-audio` stage (`VOICEIQ_AGENT_BRAIN_AUTO_RUN`, default off, `app/insights/config/settings.py:259`); the `recommendations` key appears only when the stage produced output (L5-1). | `f5e4d84` |
| 2026-09-20 | AgentRunner's duplicate sweep is no longer quadratic. Output byte-identical; 2,308 ms to 95 ms on a 16-minute call (L5-0). | `1fff8aa` |
| 2026-09-20 | Docs: N4b-1 Phase 1 recorded as done; Phase 2 (vocabulary entry) depends on decision L1. | `9ac7487` |
| 2026-09-20 | 34 tests over the language seam, using a synthetic sentinel instead of real Urdu or Arabic words. | `c2fadaa` |
| 2026-09-20 | Extraction vocabulary tables keyed by language; English moved verbatim under `"en"`; no other language ships content. | `404de5b` |
| 2026-09-20 | `AgentContext.language` becomes a read signal: an explicit argument wins, then the language Whisper detected, then English. | `359cd5f` |
| 2026-09-19 | N4b dependency and CI gate: probes show a vocabulary gap, not a model gap. N4b split into N4b-1 (vocabulary and wiring) and N4b-2 (native-script named-entity recognition); no new CI lane. | `cbcc7ee` |

## Tier 3 Wave E

34 commits, 2026-06-21 to 2026-09-19. Structural items E1 to E5, deferred
from the first Tier 3 pass.

### E3 completion (2026-09-19)

| Change | Commit |
|---|---|
| Docs: test-count table actually updated (731 local; 728 passed + 1 skipped for the CI lane, reproduced locally). | `d79ef0e` |
| Docs: Wave E closed; two more stale E3 mentions fixed. | `6cac476` |
| Docs: E3 marked done, with why the first half-fix (N3b) looked finished but was not. | `fb781d1` |
| Alignment: `_best_asr_for_window` bounded below as well, by bisecting a running maximum of segment ends. `align()` 441 ms to 42 ms on the 60-minute fixture; output byte-identical; 17 new tests. | `706d80d` |
| Docs: test-count reconciliation refreshed (631 to 714). | `1f6409a` |

### E2 Phase 2: orchestrator decomposition (2026-07-20)

| Change | Commit |
|---|---|
| README: orchestrator description corrected after the decomposition. | `07ffed7` |
| Docs: E2 Phase 2 closed. | `c2086d0` |
| The four hard-fail gates extracted; they raise a private `_HardFail` that `run()` catches once. | `a1e9ef7` |
| Stages moved verbatim into `_run_<stage>` methods: stats, sentiment, keywords; gender, emotion, topic, summary; intent, fact-check, flags; insights, PDF; ASR, diarization, alignment. No behaviour change; the 51-test harness passed after each batch. | `00c86ea`, `b536e2c`, `083cb52`, `fe95eb0`, `d2c11c7` |
| `_PipelineState` dataclass holds the state shared between stages. | `e81ca02` |
| Types: `JobIO.load_text`'s `default` parameter and return type widened to `str \| None` (runtime unchanged). | `b5d0c6d` |

### E2 Phase 1: orchestrator test harness (2026-07-17 to 2026-07-19)

| Date | Change | Commit |
|---|---|---|
| 2026-07-19 | README rewritten for the current platform scope; a leftover merge-conflict duplicate script removed. | `31585b4` |
| 2026-07-19 | Docs: why test counts differed between local runs and the two CI jobs; reading CI failures from annotations. | `b6a028e` |
| 2026-07-19 | Dependencies: scikit-learn declared explicitly. | `163ce7b` |
| 2026-07-19 | Harness mocks KeywordService, whose spaCy model is not installed in CI. | `077aaef` |
| 2026-07-19 | CI: harness failures surfaced as an annotation. | `76a8da7` |
| 2026-07-19 | Dependencies: fpdf declared in `requirements.txt`. | `854ca1e` |
| 2026-07-19 | CI: separate orchestrator-harness job with the heavy ML dependencies (retired in `4df98c6`). | `dee30d5` |
| 2026-07-17 | CLAUDE.md: harness done; the interpreter trap documented. | `a6a414b` |
| 2026-07-17 | Orchestrator harness, `app/insights/tests/test_orchestrator.py`: 51 tests over every stage plus end-to-end invariants; `orchestrator.py` coverage 0% to 100%; no production code changed. | `a17eac4`, `2ca4f08`, `f6bb074`, `6fc2099`, `24dc644` |

### E1, E4, E5 and first assessments (2026-06-21)

| Change | Commit |
|---|---|
| Summary engine: an unknown severity explicitly ranks 0 (E5). | `00ebf52` |
| Docs: E3 profiled; no actionable hot path at that scale (done later in `1f79131` and `706d80d`). | `1e67117` |
| Docs: E2 deferred until a test harness exists. | `7e46986` |
| Fact-check scorer uses the shared clamp (E1.b). | `c58dd9a` |
| Audio uploads checked by magic bytes (WAV, FLAC, MP4/M4A, MP3); anything else gets HTTP 415 (`app/utils/audio_sniff.py`, E4). | `e8a294f` |
| One shared `clamp` in `app/insights/core/_math.py` replaces three engine-local copies (E1). | `e9cbd1f` |

## Tier N

12 commits, all on 2026-07-25. Four previously deferred items, N1 to N4. Plan:
[TIER-N-PLAN.md](TIER-N-PLAN.md).

| Change | Commit |
|---|---|
| Agent Brain router also served from the lightweight image (`app/insight_main.py:80`). | `1774209` |
| Agent Brain router mounted on `app/main.py` (it had never been mounted, so the route was unreachable), with a payload-size cap and its own rate limit. | `df8eec3` |
| Docs: N4a done; Tier N tractable work complete, N4b deferred. | `3723efb` |
| Agent Brain deadline phrases resolved to ISO-8601 dates with the standard library, no new dependency; additive `deadlineDate` field (N4a). 62 new tests. | `99dfc96` |
| Docs: N2 done. | `2a17b91` |
| CI: heavy orchestrator-harness job retired; the whole suite runs in the light lane (N2). | `4df98c6` |
| Orchestrator imports ML libraries inside each stage, so the harness runs on the light requirements (N2). | `9e57cb4` |
| Docs: Tier N status and N2 pre-work findings. | `b107089` |
| Alignment: two quadratic scans bounded with bisect; about 5x faster on the 60-minute fixture, identical output (N3b). | `1f79131` |
| Alignment characterization suite: 74 tests, `alignment_service.py` coverage 78% to 99% (N3a). | `5c43f45` |
| [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md): integration spec for the timestamp-bound callback signature, for the Java team (N1). | `2bf9b76` |
| Docs: Tier N plan for the four deferred items. | `36697fd` |

## Tier S

14 commits, all on 2026-07-25. Eight security items in five waves. Plan:
[SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md). Current controls:
[SECURITY.md](SECURITY.md).

| Change | Commit |
|---|---|
| Docs: Tier S closed, all eight items done. | `ec8dc1e` |
| S8: interactive API docs (`/docs`, `/redoc`, `/openapi.json`) off in production; `/healthz` and `/version` stay public. | `c7c34db` |
| S7: a timestamp-bound v2 callback signature sent alongside v1; the callback URL must use HTTPS (local development hosts excepted). | `a54944b` |
| S3: fact-check enrichment on `/v1/process-audio` is opt-in (`?fact_check=` or `VOICEIQ_FACTCHECK_AUTO_ENRICH`, default off, `app/insights/config/settings.py:247`); [DATA-FLOW.md](DATA-FLOW.md) added. | `61fbf56` |
| S1: per-endpoint rate limits on `/v1/process-audio` and `/v1/fact-check`; over the limit returns 429 with `Retry-After`. | `93f73b1` |
| S2: job-artifact retention sweep (`VOICEIQ_JOB_RETENTION_HOURS`, default 24, `app/insights/config/settings.py:153`). | `3c3adb2` |
| S6: country name percent-encoded in the Wikipedia request path. | `7b0c313` |
| S5: an upload with no filename returns 400; the extension allowlist moved to `app/utils/upload.py`. | `525316d` |
| S4: a failed upload no longer returns exception detail to the client. | `f7ecd55` |
| Docs: wave completion notes for S-D, S-C, S-B (with a retention note) and S-A. | `f19e564`, `49fdf7e`, `cece640`, `cc211ec` |
| Docs: security hardening plan. | `58f81f2` |

## Sprint 6: Agent Brain

15 commits, 2026-06-21 to 2026-06-22. The rule-based recommendation layer in
`app/agent_brain/`.

| Date | Change | Commit |
|---|---|---|
| 2026-06-22 | Sprint 6 closed; `app/agent_brain/` added to the CI mypy gate and test job; callback settings documented in `.env.example`. | `ef005dd` |
| 2026-06-22 | End-to-end scenario tests; every agent_brain module at 100% coverage (103 package tests). | `bda33f9` |
| 2026-06-22 | Java callback client: body signed with HMAC-SHA256 (hash-based message authentication code), disabled unless URL and secret are both set; `AgentBrainSettings` with the `VOICEIQ_AGENT_` prefix (`app/agent_brain/config/settings.py:14`). | `3e362e9` |
| 2026-06-22 | Recommendations API route and the pipeline adapter. | `d4cfbe0` |
| 2026-06-22 | Agent runner and service: fan out to all five agents, refine, de-duplicate, rank; a failing agent is logged and skipped. | `0970819` |
| 2026-06-22 | Ranker: priority first, then confidence. | `ce4afd9` |
| 2026-06-22 | De-duplication with the standard-library `difflib`. | `9227881` |
| 2026-06-22 | Confidence refinement from speech-recognition (ASR) confidence, repetition and fact-check links. | `e64e011` |
| 2026-06-21 to 2026-06-22 | The five agents: Task, Follow-Up Meeting, Email Draft, Escalation and Fact-Check Review. The Escalation commit also rounds every recommendation's confidence to four decimal places at the model. | `c6d3bb4`, `c070a07`, `c74f99f`, `ec4ae84`, `55e43af` |
| 2026-06-21 | `BaseAgent` and extraction utilities: signals, assignee, date-time phrases, priority. | `822771e` |
| 2026-06-21 | Recommendation and context models; `requires_approval` is always true at schema level. | `ef5b5e8` |

## Tier 3 Waves A to D

13 commits, 2026-06-14 to 2026-06-21. Cleanups, schema fixes and the mypy
gate. Labels in parentheses are the Tier 3 item ids from the commit subjects.

| Date | Change | Commit |
|---|---|---|
| 2026-06-21 | Docs: Waves A, B and D closed; Wave E scope defined. | `b013e4a` |
| 2026-06-21 | CI: mypy becomes a required check (D). | `a094120` |
| 2026-06-21 | Pydantic mypy plugin and type annotations take `mypy app/insights/` from 126 errors to 0, type-only changes. | `768d8e2` |
| 2026-06-18 | Validator: hard errors carry `severity="error"` (B1). | `f16709a` |
| 2026-06-18 | CLAUDE.md: the orchestration fault-isolation contract written down (C5). | `3c2b14a` |
| 2026-06-18 | Rule engine: unused `threshold_profile` parameter removed (S2). | `16ceeb8` |
| 2026-06-18 | `TimelineMarker.reason` must be non-empty (C4). | `c83d92b` |
| 2026-06-18 | Timeline: a sentiment label flip marks an emotional shift only when the change also clears the threshold (B3). | `e656c12` |
| 2026-06-18 | Validator: `bool` rejected as a non-negative integer (B2). | `a54cdc1` |
| 2026-06-16 | Summary engine: unused `session` parameter removed. | `2a3d689` |
| 2026-06-16 | Summary engine: `speaker_insight` parameter annotated. | `2df83e0` |
| 2026-06-16 | Rule engine: dead identity `severity_map` removed. | `a199184` |
| 2026-06-14 | CLAUDE.md: index of the Tier 3 punch list. | `bc03d8e` |

## Tier 2

7 commits, 2026-06-01 to 2026-06-14. Test coverage and CI gates.

| Date | Change | Commit |
|---|---|---|
| 2026-06-14 | CI: lint job (ruff, mypy as information only, gitleaks on the working tree) and a Python 3.10 / 3.11 test matrix. | `1a9fbed` |
| 2026-06-01 to 2026-06-14 | Unit and edge-case tests take six insight-core engines to 100% coverage: normalizer, validator, analytics, summary, timeline, rule. | `0bd88b9`, `36d7c8a`, `a232811`, `17e1acf`, `4a561ad`, `d78691e` |

## Tier 1

6 commits, 2026-05-20 to 2026-05-23. Production-readiness security.

| Date | Change | Commit |
|---|---|---|
| 2026-05-23 | Model-loader cold start serialised with a lock in the diarization, sentiment and summary services. | `7c9f4fc` |
| 2026-05-23 | `.env.example` documents `VOICEIQ_FFMPEG_TIMEOUT_SEC`. | `cdbab6c` |
| 2026-05-23 | ffmpeg runs with a timeout (default 120 s, `app/insights/config/settings.py:139`); a timeout returns 422. | `80cd4f0` |
| 2026-05-23 | Fact-check repository: rows with the same `created_at` are ordered by id. | `c895a49` |
| 2026-05-23 | Payload-size caps on the `/v1` routes (411, 400 or 413); uploads streamed to disk in 1 MB chunks under a size cap (default 200 MB, `app/insights/config/settings.py:104`). | `ce3cb82` |
| 2026-05-20 | `X-API-Key` authentication on `/v1/insights/*`, `/v1/fact-check` and `/v1/process-audio`; `/healthz` and `/version` stay public. | `bb6e61b` |

## Tooling, first CI and Docker

12 commits, 2026-04-25 to 2026-05-20.

| Date | Change | Commit |
|---|---|---|
| 2026-05-20 | Commands reference PDF added. | `755f374` |
| 2026-05-05 | `08-deployment.md` updated for the Docker setup. | `9b093e1` |
| 2026-05-05 | Dockerfile pinned to Python 3.11 and driven by `requirements-insight.txt`, port 8888; `.dockerignore` extended; `docker-compose.yml` added. | `6c12d9f` |
| 2026-05-04 | `timezone.utc` instead of `datetime.UTC`, for Python 3.10. | `e55d46c` |
| 2026-05-03 to 2026-05-04 | CLAUDE.md: completed and resolved items removed from the task and known-issue lists. | `3d80668`, `3412338`, `b03215d` |
| 2026-05-03 | `.git-blame-ignore-revs` skips the bulk-format commit. | `a4cc8c3` |
| 2026-05-03 | Bulk ruff-format, end-of-file and trailing-whitespace baseline. | `96f2e32` |
| 2026-05-03 | Lint fixes; ruff configuration relaxed (line length 120); `data/` runtime artifacts untracked. | `3b98dc8` |
| 2026-05-03 | pre-commit hooks: ruff, ruff-format, gitleaks and file hygiene. | `fceeeac` |
| 2026-04-25 | First CI workflow: a lightweight GitHub Actions run of the insight tests. | `37ee449` |

## Sprint 5: fact-check engine

2 commits, 2026-05-03.

| Change | Commit |
|---|---|
| CLAUDE.md updated for the fact-check engine. | `6a2ae3e` |
| Rule-based fact-check engine: claim detector, classifier, comparator, scorer and engine; five source clients (exchangerate.host, CoinGecko, OpenWeather, Alpha Vantage, Wikipedia REST); SQLite `fact_check_results` table; `POST /v1/fact-check` and `GET /v1/fact-check/{id}`; auto-enrichment in `/v1/process-audio` (opt-in since `61fbf56`); 91 new tests. | `592e8e7` |

## Initial code and Sprints 1 to 4

9 commits, 2026-02-10 to 2026-04-25. Sprint scopes, from the
[CLAUDE.md](../CLAUDE.md) sprint table (lines 79 to 82): 1, foundation engines; 2, escalation
and inconsistency engines; 3, SQLite persistence and the Dockerfile; 4,
handoff documentation. DOCS/00 to 10 (index:
[00-index.md](00-index.md)) are the Sprint 1 to 4 snapshot.

| Date | Change | Commit |
|---|---|---|
| 2026-04-25 | Tracked bytecode and a stale source archive removed; archive files blocked in `.gitignore`. | `2d017b7` |
| 2026-04-25 | Merge of the remote `main`, keeping the local Sprint 2/3 version. The first merge commit. | `032d822` |
| 2026-04-25 | Insight service from Sprints 1 to 3 (`app/insights/`: engines, models, API, SQLite repository, tests), `app/insight_main.py`, Dockerfile, `.env.example`, and the Sprint 4 handoff set DOCS/00 to 10 with the handoff `.docx`. | `94f6479` |
| 2026-02-12 | Pipeline orchestrator (`app/pipeline/orchestrator.py`), job storage (`app/utils/job_io.py`) and one sample job's artifacts. | `b4c5c86` |
| 2026-02-10 to 2026-02-12 | README revisions. | `7220d33`, `472dfd2`, `f0c04a1` |
| 2026-02-10 | First application code: FastAPI app, `/v1/process-audio` route, audio and language-analysis services, evaluator, early tests. | `4c54e03` |
| 2026-02-10 | Initial commit: LICENSE and README. | `f21300a` |
