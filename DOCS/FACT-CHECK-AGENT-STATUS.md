# Fact-Check Agent — Live Status

Owner: engineering · Status: **Phase 0 in sign-off** · Created: 2026-10-11 · Updated: 2026-10-19

The one place that says where Sprint 7 stands. Update it with every
milestone. Decision status is **not** kept here — it lives only in the
register, [`FACTCHECK-AGENT-PHASE-0.md` §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
Source plan: `VoiceIQ_Fact_Check_Agent_Implementation_Plan.pdf` v1.1
(2026-09-04, historical; not edited).

---

## Current status

| | |
|---|---|
| Date | 2026-10-19 |
| Branch | `sprint7/doc-sync` (from `main` @ `8fd3972`); M1 at `c1b35d9` + `cedfe96`; M2 at `9aff207`; M3 at `dc03a44`; M4 at `8e51a94`; M5 at `1dbfac7`; M6 at `dc9caf3`; M7 at `dbae452`; pull-request review fix round at `8563572`, `efdd1a9`, `ee2e15b` |
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

Verified against the repository on 2026-10-19 (`ee2e15b`).

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
  - `8e51a94` — **M4: over-long numbers in the transcript never 500 either
    endpoint.** The detector drops a claim whose number has more than 15
    digits (a float's guaranteed decimal precision; real prices, rates and
    temperatures need far fewer), so no claimed value can become infinite;
    `POST /v1/fact-check` answers 200 with that claim dropped. As a second
    layer, `/v1/process-audio` checks the fact-check payload as strict JSON
    before saving or returning it and falls back to the failed stub, so it
    cannot 500 because of fact-check. 35 new tests. Light suite 1078 passed,
    1 skipped.
  - `1dbfac7` — **M5: the claim detector stops reading names and counts as
    prices.** A crypto, stock or commodity number followed by what it counts
    or measures, or by another currency ("Ada is 30 years old", "Meta is 30
    people", "5 per cent", "50 a month", "95000 rupees") is no longer a price
    claim. A subject that is also a name or an everyday word ("Ada", "Sol",
    "Brent"), or a stock ticker ("Meta"), needs price context ("$",
    "dollars", "a barrel", or a price verb) unless written in capitals. "1 USD = 280 PKR"
    is read as a rate (amounts of 1, 10, 100, ...), and "95k", "24 hundred"
    and "1.2 million" scale the value. Every detector pattern now scans in
    linear time. No v1 model change. 423 new tests, red-checked against the
    previous detector (352 of 433 fail there); two adversarial review
    rounds. Light suite 1501 passed, 1 skipped. Remaining limits:
    [FEATURES.md §4.3](FEATURES.md#43-known-functional-limits-of-the-detector).
  - `dc9caf3` — **M6: text outside Latin-1 no longer fails the PDF.** Common
    punctuation maps to its nearest Latin-1 form (curly quotes, dashes,
    ellipsis, the euro sign as "EUR") and any other character prints as "?",
    so the report is produced and `PDF_FAILED` no longer fires for it; the
    JSON response keeps the original text. 6 new tests, red-checked first
    (`UnicodeEncodeError`, `PDF_FAILED`). M6 is containment only (owner,
    2026-10-17): a Unicode-capable PDF font is still required before
    Phase 6, for readable Urdu and Arabic.
  - `a712146` — **Stored rows with non-finite numbers read back as null**
    (the approved old-rows fix). Re-checking showed the route already
    rendered them as null through its serializer; the repository now returns
    None for every non-finite number itself. 2 new tests.
  - **Security and reliability hardening** (2026-10-16 to 2026-10-17), from a
    review whose detailed list is tracked privately; fixed in small commits,
    each code fix with its own regression tests:
    - `ff874de` — API key and declared size are checked before a request
      body is read; the bytes that arrive are counted.
    - `2bb6593` — `/v1/process-audio` runs the pipeline in a worker thread,
      one run at a time per worker (the event-loop part of the process-audio concurrency prerequisite).
    - `1d967c5` — the fact-check evidence cache is safe across threads.
    - `93dda14` — insight routes and models accept only finite numbers.
    - `702d23d`, `395f6ff` — rate-limit buckets use only accepted keys; the
      staging recipe sets a key.
    - `8103360` — Agent Brain keeps one review per fact-check claim
      (the Agent Brain dedup prerequisite).
    - `0011dd7` — fixed public error messages; logs carry the exception class.
    - `83803b5` — provider replies are read up to 1 MiB; a failed subject is
      fetched once per run.
    - `718d763`, `5696e32`, `98a319a`, `3bf7506`, `c661bad` — SQLite
      directory path, partial ffmpeg output removed, faster title
      comparison, accurate purge count, CI checks the gitleaks checksum.

    Light suite 1579 passed, 1 skipped.
  - **Owner decisions implemented** (2026-10-17), one small commit each,
    under the size rule; records in
    [DECISIONS #33–#49](DECISIONS.md#security-and-reliability-decisions-2026-10-17):
    - `dc97a84` — a failed topic step returns a fixed message.
    - `d7f9075` — staging fails closed without keys; compose binds to
      loopback.
    - `948c49d` — `/v1/process-audio`: one run and two waiting uploads per
      worker by default, then 503 with `Retry-After: 30` (completes the
      process-audio concurrency prerequisite).
    - `6c17c72` — audio longer than two hours gets 413 `AUDIO_TOO_LONG`,
      checked with ffprobe before decoding and an ffmpeg hard stop; never
      truncated.
    - `e802fcd` — insight writes 60/minute and reads 120/minute per caller;
      the session list takes `limit` (default 100, at most 500) and
      `offset`.
    - `da92ae4` — a fact-check run checks at most 25 claims and starts no new check after 20 s;
      the rest are counted as skipped.
    - `cbdafb4` — insight and fact-check records are deleted after 30 days,
      by the retention sweep, now at startup and hourly.
    - `f8bc3ed`, `c53c71d`, `0f9c4c1` — an error never overwrites a good
      stored record; an insight error fails its stage and skips the Agent
      Brain; a missing stage package fails only that stage.
    - `98730c3` — pipeline sentiment reaches the insight engines as
      positivity, so insight scores for processed audio change.
    - `2721a73`, `e25a3b2`, `660558c`, `fb56d7d` — mock diarization is
      flagged; the log level is applied and threshold profiles are marked
      not implemented; `/readyz`; compose CPU and memory limits and TLS at
      the ingress.
    - `45a5b22` — currency rates from open.er-api.com (the forex decision,
      option A), with DATA-FLOW.md and the PHASE-0 §6.4 source policy in
      the same commit.
    - Recorded without code: one trust group for API keys, to revisit
      before any direct customer access (DECISIONS #33); the
      authentication-before-422 order and the raw insight route's refusal
      of non-finite numbers were accepted.
    - The approved dependency scan ran once, read-only; its results are
      tracked privately and nothing was upgraded.
  - `dbae452` — **M7: keyword rules match whole words only.** The flag rules
    (hesitation, aggression, lie risk), the intent rules and the
    email-draft triggers match a keyword only where it is not part of a
    longer word, so "um" is no longer found in "number" or "send a" in
    "send all" (`app/utils/text_match.py`). The other Agent Brain agents
    keep substring matching; no vocabulary changed. 49 new tests,
    red-checked against the previous code (all 17 part-of-a-word cases
    fail there).

    Light suite 1730 passed, 1 skipped; `ruff check .` and mypy clean.
  - **Pull-request review fix round** (2026-10-19), approved by the owner
    after two independent reviews of the pull request; one commit per fix,
    each red-checked and reviewed:
    - `ee2e15b` — whole-word rules list the article, pronoun and inflected
      forms they would otherwise miss (email triggers such as "send an" and
      "share these"; "umm", "hates", "apologized" and others); the email
      title drops a leading article only as a whole word
      ([DECISIONS #32](DECISIONS.md#32-m7-keyword-rules-match-whole-words-only)).
    - `efdd1a9` — fact-check provider replies are requested uncompressed,
      and a reply in any other content encoding is refused before its body
      is read.
    - `8563572` — the test suite never reads `.env` and uses a temporary
      database and job folder.
    - A docs commit corrected claims that went further than the code:
      fact-check egress, when the process-audio 503 and 429 come, the
      fact-check deadline, the callback replay check and deployment
      requirements.

    Light suite 1781 passed, 1 skipped; ruff and mypy clean.
- **2026-10-11 audit and documentation sync** (this branch): stale
  operational docs corrected, PHASE-0 wording made consistent with its own
  register, this status document created.

## In progress

- **Phase 0 sign-off** — owner-side: the 7 pending register decisions, the
  §2/§3/§6 approvals, and confirmation of the two pending PHASE-0 wording
  changes (per-phase gate reading, `ClaimSpan` in the v1 freeze list).
- **Prerequisite fixes** — until Phase 0 lets a phase start, no Sprint 7
  application code is written; only fixes that need no pending decision, one
  per milestone, smallest first. M1–M7, the old-rows fix, the decision-free
  hardening items and the owner-decided items are done. The items the
  owner parked are tracked privately.
- **Working rules** (project owner, 2026-10-16): the claim detector gets no
  new heuristics until Phase 3, only crash, security or performance fixes
  ([DECISIONS #26](DECISIONS.md#26-claim-detector-no-new-heuristics-until-phase-3));
  a milestone or security fix that grows past roughly twice its planned
  size, or needs a second rework round after review, stops for the owner
  ([DECISIONS #27](DECISIONS.md#27-size-rule-for-milestones-and-security-fixes)).

## Remaining

Ordered backlog. Phase numbers are the plan's.

1. **Phase 1 gate.** Sign every register decision due before Phase 1; approve
   §2, §3 and §6; confirm the per-phase gate reading; record the kick-off date
   and signer names; add the unregistered decisions listed under *Blockers*
   to §1. Until the per-phase gate reading is confirmed, every register
   decision must be signed before Phase 1; once confirmed, those due later
   (before Phase 1 ends, Phase 5 or Phase 6 — see PHASE-0 §8) can follow.
2. **Prerequisite fixes**, smallest first (earliest phase each one gates
   in brackets):
   1. Java callback fires on every Agent Brain run — needs the D10 final-pass predicate and an exactly-one test; lands with Phase 6, which adds the re-run [6].
   2. Claim-detector limits left after M5 — past prices read as current ("I bought ETH at 1200"), capitalised acronyms read as tickers, any number after a coin, ticker or commodity read as a price ("Gold is 29.99 with free shipping"); listed in [FEATURES.md §4.3](FEATURES.md#43-known-functional-limits-of-the-detector). *Wait for Phase 3* (DECISIONS #26) [3].
   3. A Unicode-capable PDF font — M6 only contains the failure (DECISIONS #31) [6].
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
    single uvicorn worker, and in-memory state that other workers would not share;
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
    age out (the retention sweep knows only `data/jobs/` directories and the
    v1 insight and fact-check tables) — Phase 6.

## Next milestone

**None ready for engineering.** M7 was the last fix that needed no pending
decision, and the owner-decided items are done. The next step is owner-side:
the **Phase 1 gate** (*Remaining*, item 1). The prerequisites left wait for
Phase 3 or Phase 6.

## Acceptance evidence

The Phase 1 gate (*Remaining*, item 1) is met when all of the following hold (the first two items are the PHASE-0 §8 checklist):

- every register decision is signed, with signer and date in
  [PHASE-0 §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register) (only those
  due before Phase 1, if the per-phase gate reading is confirmed; until then
  every decision counts);
- §2, §3 and §6 are approved;
- the per-phase gate reading and the `ClaimSpan` freeze-list change are
  confirmed or withdrawn;
- the decisions listed under *Blockers* that gate Phase 1 are added to the
  register;
- this document records the kick-off date and moves Phase 0 to *Completed*.
