# Fact-Check Agent — Live Status

Owner: engineering · Status: **Phase 0 in sign-off** · Created: 2026-10-11 · Updated: 2026-10-11

The one place that says where Sprint 7 stands. Update it with every
milestone. Decision status is **not** kept here — it lives only in the
register, [`FACTCHECK-AGENT-PHASE-0.md` §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
Source plan: `VoiceIQ_Fact_Check_Agent_Implementation_Plan.pdf` v1.1
(2026-09-04, historical; not edited).

---

## Current status

| | |
|---|---|
| Date | 2026-10-11 |
| Branch | `sprint7/doc-sync` (from `main` @ `8fd3972`); M1 at `c1b35d9` + `cedfe96`; M2 at `9aff207`; M3 at `dc03a44` |
| Current phase | **Phase 0 — Contract and scope lock: partially complete.** Phases 1–7 not started (no Sprint 7 application code exists). |
| Overall progress | **0.83 %** |

**How the percentage is computed** — countable plan deliverables only. Each
phase's bullets are its "Implementation work" plus "Exit deliverables" items
in the plan; a drafted-but-unsigned item counts as not done; infrastructure
that predates Sprint 7 counts as reusable, not done.

```
overall = Σ_phase (effort_weight / Σ effort_weight) × (bullets done / bullets total)

weights (weeks, plan estimates):  P0 0.5 · P1 2 · P2 1 · P3 1.5 · P4 2 · P5 2 · P6 1.5 · P7 1.5  = 12.0
done / total:                     P0 2/10 · P1–P7 0 of 48                                       = 2/58

overall = (0.5 / 12.0) × (2 / 10) = 0.0083 → 0.83 %
```

Other countable measures: decisions signed **3 / 10**; Phase 0 exit
deliverables approved **0 / 3**. Prerequisite fixes (such as M1) are not
plan deliverables and do not move the percentage.

## Completed

Verified against the repository on 2026-10-11.

- **Phase 0: 3 of 10 register decisions signed** — see
  [PHASE-0 §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register)
  (`2a539d3`, `dacc926`).
- **Phase 0 policies written** (`c6284b6`) — §2 API contract, §3
  compatibility, §5 verdict mapping, §6 privacy and source policy and §7
  configuration are drafted, not yet approved; §4 persistence is binding
  through the signed persistence decision.
- **D3 gold-set scoping brief** written — [`D3-GOLD-SET-BRIEF.md`](D3-GOLD-SET-BRIEF.md)
  (`8fd3972`). A recommendation, not a decision.
- **Prerequisites landed early** (not counted as phase deliverables):
  - `1cd5350` — the v1 verdict map has no default; an unmapped verdict
    raises. Pinned by `test_verdict_map_is_total`.
  - `ff84664` — `app/insights/tests/test_db_schema_evolution.py` pins what
    `create_all()` can and cannot change — the behaviour the new-tables-only
    rule relies on.
  - `c1b35d9`, `cedfe96` — **M1: outbound request URLs kept out of the
    logs.** `setup_logging()` pins the `httpx` and `httpcore` loggers to
    WARNING, so API keys and claim subjects in source-client URLs no longer
    reach INFO logs; a failed Java callback logs the error class and HTTP
    status instead of httpx's error text (which embeds the callback URL).
    Pinned by `app/insights/tests/test_logging_hygiene.py` (17 tests:
    success, 4xx, 5xx-then-retry and transport-error paths; failed callback;
    Docker entrypoint import; unchanged `POST /v1/fact-check` response).
    Light suite 809 passed, 1 skipped.
  - `9aff207` — **M2: bad provider replies never become verdicts or 500s.**
    Non-finite numbers, non-object or undecodable JSON, wrong nested types,
    over-large or degenerate values and over-deep metadata from any source
    client end as the existing `SOURCE_UNAVAILABLE` for that claim only; the
    engine isolates each claim, `POST /v1/fact-check` stays 200 with strict
    JSON, and `/v1/process-audio` stays 200 with `fact_checks_v2` degraded per
    claim. No v1 model change. Pinned by
    `app/insights/tests/test_factcheck_source_replies.py` (223 tests). Light
    suite 1032 passed, 1 skipped.
  - `dc03a44` — **M3: `exchangerate.host` failures are named, not silent.** The provider
    requires an access key on every call (owner-verified 2026-10-11: keyless
    `/latest` returns `success: false`, error 101 `missing_access_key`), and no
    key is configured, so every `CURRENCY_RATE` claim is `SOURCE_UNAVAILABLE`.
    The result now says why ("provider requires an access key") and the log
    carries the provider's error code and type only. Provider unchanged. 11 new
    tests in the same module. Light suite 1043 passed, 1 skipped.
- **2026-10-11 audit and documentation sync** (this branch): stale
  operational docs corrected, PHASE-0 wording made consistent with its own
  register, this status document created.

## In progress

- **Phase 0 sign-off** — owner-side: the 7 pending register decisions, the
  §2/§3/§6 approvals, and confirmation of the two pending PHASE-0 wording
  changes (per-phase gate reading, `ClaimSpan` in the v1 freeze list).
- **Prerequisite fixes** — until Phase 0 lets a phase start, no Sprint 7
  application code is written; only fixes that need no pending decision, one
  per milestone, smallest first. M1–M3 are done; M4 is the next milestone below.

## Remaining

Ordered backlog. Phase numbers are the plan's.

1. **Phase 1 gate.** Sign every register decision due before Phase 1; approve
   §2, §3 and §6; confirm the per-phase gate reading; record the kick-off date
   and signer names; add the unregistered decisions listed under *Blockers*
   to §1. Until the per-phase gate reading is confirmed, every register
   decision must be signed before Phase 1; once confirmed, those due later
   (before Phase 1 ends, Phase 5 or Phase 6 — see PHASE-0 §8) can follow.
2. **Decision-independent prerequisite fixes**, smallest first (earliest
   phase each one gates in brackets):
   1. A number of 309 or more digits after a claim keyword in the transcript becomes an infinite claimed value. `POST /v1/fact-check` returns 500, and so does `/v1/process-audio` when fact-checking is on — the whole audio response is lost. The ASR text reaches the fact-check unmodified (only a 10,000-character cap), so audio that makes Whisper emit such a run triggers it [3] — *next milestone*.
   2. Regex claim detector false positives ("Ada is 30" → crypto) and misses ("1 USD = 280 PKR") [3].
   3. PDF fails entirely on any non-Latin-1 character — contain it [6].
   4. `/v1/process-audio` is `async def` but runs blocking work on the event loop [6].
   5. Agent Brain dedup merges distinct fact-check reviews [6].
   6. Java callback fires on every Agent Brain run — needs the D10 final-pass predicate and an exactly-one test [6].
3. **Phase 1** — gold set, model server, bake-off, pinned model.
4. **Phase 2** — typed v2 models, OpenAI-compatible client, `VOICEIQ_FACTCHECK_*` settings, stubbed boundary.
5. **Phase 3** — claim extraction with segment/speaker/timestamp traceability, checkability gate, dedup, claim cap.
6. **Phase 4** — retrieval interface, trusted clients first, SearXNG adapter, `url_guard` SSRF controls, frozen fixtures.
7. **Phase 5** — evidence extraction, evidence-only verdicts, citation-ID validation, deterministic confidence, evidence cache.
8. **Phase 6** — job runner and `/v2/fact-check` routes, new tables, `fact_check_report`, v2 verdict map, D10 re-run (Agent Brain, then PDF) with exactly one callback, Unicode-safe cited PDF.
9. **Phase 7** — deadlines, concurrency bounds, circuit breaking, metrics, `DATA-FLOW.md` update, staged rollout.

## Decisions

Register: [`FACTCHECK-AGENT-PHASE-0.md` §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
Summary counts only: **10** decisions in the register — **3** signed,
**7** pending. Phase 0 exit deliverables: **0 / 3** approved. Decisions not
yet in the register are listed under *Blockers*.

## Blockers

Real blockers only.

- **Phase 1 cannot start:** not every register decision due before Phase 1
  is signed, and §2, §3 and §6 are not approved (PHASE-0 §1, §8).
- **Pending owner confirmation (not in force):** two PHASE-0 wording changes
  of 2026-10-11 — the per-phase gate reading that follows the register's
  *Needed by* column (plan v1.1 §11 said every decision blocks Phase 1), and
  `ClaimSpan` added to the §2.1 v1 freeze list. Until the tech lead and the
  engineering manager confirm, every register decision blocks Phase 1.
- **Decisions missing from the register** — no document settles them, and
  each gates a phase:
  - the job runner behind D2 (in-process thread, DB-polled worker or queue) —
    Phase 6, and the Phase 2 run/status models; today there is no runner, a
    single uvicorn worker, and per-process rate-limit and cache state;
  - supported languages for the agent and its report — Phases 1, 3 and 6;
  - local development model server (Ollama or llama.cpp) — Phase 1;
  - who hosts SearXNG, and the robots.txt / terms-of-service position for
    page fetching — Phase 4;
  - the `POST /v2/fact-check` request schema, how non-checkable statements
    appear in v2 output, and the `/v2` auth, payload-cap and rate-limit
    rules — Phases 2 and 6;
  - when `fact_check_report` is absent versus `disabled`, and how
    `FACTCHECK_AGENT_ENABLED`, `FACTCHECK_AUTO_ENRICH` and `?fact_check`
    combine — Phase 6;
  - how the D1 tables reach a deployment with
    `VOICEIQ_DATABASE_AUTO_CREATE=false` (`init_db` then skips
    `create_all`) — Phase 6;
  - how the D10 re-run rebuilds its inputs after the response has gone, how
    the regenerated PDF and recommendations are delivered, and how run rows
    age out (the retention sweep removes only `data/jobs/` directories) —
    Phase 6.
- **Open decision: forex provider** (owner: project owner; gates Phase 4,
  which routes structured claims through these clients). exchangerate.host
  now needs an access key on every call, so `CURRENCY_RATE` claims cannot be
  verified today. Options:
  - **(A)** switch to open.er-api.com — keyless, covers all 11 currencies the
    detector recognises, daily rates, commercial use allowed, attribution
    required; fetch at most hourly per base currency;
  - **(B)** keep exchangerate.host with a paid key — `/live` endpoint,
    `quotes.USDxxx` reply shape (free plan: 100 requests/month, USD source
    only, non-commercial);
  - **(C)** mark `CURRENCY_RATE` unsupported until decided.
  A or B changes an egress destination or what is sent, so `DATA-FLOW.md`
  and the PHASE-0 §6 source policy must change in the same milestone.

## Next milestone

**M4 — An over-long number in the transcript never 500s either endpoint.** A
decision-independent prerequisite fix (gates Phase 3, which keeps the regex
detector as its fast path). Status: **planned, awaiting approval**.

The detector's value groups accept any run of digits, and a run of 309 or
more digits converts to an infinite float. The claimed value then reaches
the response, which cannot be serialized as standard JSON:
`POST /v1/fact-check` returns 500, and `/v1/process-audio` returns 500 when
fact-checking is on, losing the whole audio response (route-level probe,
2026-10-16). Whether Whisper emits such a run for real audio is unverified,
but nothing between the ASR text and the fact-check bounds it.

## Acceptance evidence

M4 is done when all of the following ran in the same session and passed:

- Tests put a 309-digit and a 1,000-digit number after each numeric claim
  keyword, and assert that no result carries a non-finite value, that
  `POST /v1/fact-check` returns 200 with strict JSON, and that the real
  `/v1/process-audio` route (pipeline stubbed, fact-check on) returns 200
  with `fact_checks_v2` not replaced by an error stub. They are red-checked
  against the current code first.
- The full light suite —
  `.venv\Scripts\python.exe -m pytest app/insights/tests/ app/agent_brain/tests/ -q` —
  passes with the new tests added to the 1043 + 1 skipped baseline.
- `ruff==0.7.4 check .` and
  `mypy==1.13.0 --ignore-missing-imports --check-untyped-defs app/insights/ app/agent_brain/`
  are clean.
- This document is updated: M4 moved to *Completed*, the next prerequisite
  promoted.
