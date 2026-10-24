# Fact-Check Agent — Live Status

Owner: engineering · Status: **Phase 0 complete; Phase 1 may start** · Created: 2026-10-11 · Updated: 2026-10-24

The one place that says where Sprint 7 stands. Update it with every
milestone. Decision status is **not** kept here — it lives only in the
register, [`FACTCHECK-AGENT-PHASE-0.md` §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
Source plan: `VoiceIQ_Fact_Check_Agent_Implementation_Plan.pdf` v1.1
(2026-09-04, historical; not edited).

---

## Current status

| | |
|---|---|
| Date | 2026-10-24 |
| Branch | `main` @ `4025199` (pull request #1 merged `sprint7/doc-sync` at `49063bb`: M1 at `c1b35d9` + `cedfe96`; M2 at `9aff207`; M3 at `dc03a44`; M4 at `8e51a94`; M5 at `1dbfac7`; M6 at `dc9caf3`; M7 at `dbae452`; pull-request review fix round at `8563572`, `efdd1a9`, `ee2e15b`); the Phase 0 sign-off is recorded on `sprint7/phase0-decisions`, merged by pull request #2 (`6e92898`); the diarization fix (`9887e0b`) was merged by pull request #3 (`4025199`); the removal of diarization's Hugging Face login is on `sprint7/diarization-no-login` (`670b4de`), not merged |
| Current phase | **Phase 0 — Contract and scope lock: complete** (signed 2026-10-19). **Phase 1 may start**; gold-set labelling may start at once (D3). Phases 1–7 not started; no Sprint 7 application code exists. |
| Overall progress | **4.17 %** |

**How the percentage is computed** — countable plan deliverables only. Each
phase's bullets are its "Implementation work" plus "Exit deliverables" items
in the plan; a drafted-but-unsigned item counts as not done; infrastructure
that predates Sprint 7 counts as reusable, not done.

```
overall = Σ_phase (effort_weight / Σ effort_weight) × (bullets done / bullets total)

weights (weeks, plan estimates):  P0 0.5 · P1 2 · P2 1 · P3 1.5 · P4 2 · P5 2 · P6 1.5 · P7 1.5  = 12.0
done / total:                     P0 10/10 · P1–P7 0 of 48                                      = 10/58

overall = (0.5 / 12.0) × (10 / 10) = 0.0417 → 4.17 %
```

Other countable measures: the 10 original register decisions are all
signed, and D3b, D10-T and U1–U10 were added and signed; Phase 0 exit
deliverables approved **3 / 3**. Some values are still open (see
*Blockers*); none blocks the start of Phase 1, and the earliest is needed
before the first bake-off run. Prerequisite fixes (such
as M1) are not plan deliverables and do not move the percentage.

## Completed

Verified against the repository on 2026-10-24 (`670b4de`).

- **Phase 0 complete (2026-10-19).** The project owner (sole maintainer),
  who holds every role in the register, signed the remaining register
  decisions, added D3b, D10-T and U1–U10 to the register with their own
  phase dates, approved §2, §3 and §6 with their amendments (§5 and §7 with
  §3), confirmed the per-phase gate reading and the `ClaimSpan` freeze-list
  change, and recorded the gate dates: Phase 0 opened 2026-09-04. See
  [PHASE-0 §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register) and §8, and
  [DECISIONS #50–#52](DECISIONS.md#phase-0-sign-off-2026-10-19). D1, D2 and
  D10 were signed earlier (`2a539d3`, `dacc926`).
- **Phase 0 policies written** (`c6284b6`) and approved on 2026-10-19: §2 API
  contract, §3 compatibility, §5 verdict mapping, §6 privacy and source
  policy and §7 configuration; §4 persistence is binding through D1.
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
  - **Diarization fix** (2026-10-24), approved by the owner; on
    `sprint7/diarization-fix`, merged by pull request #3 (`4025199`):
    - `9887e0b` — diarization loads `pyannote/speaker-diarization-3.1`
      with PyTorch's weights-only loader on (six checkpoint classes
      allowed by name); `requirements.txt` declares matplotlib; a run
      whose pipeline never loaded carries `DIARIZATION_UNAVAILABLE`; the
      mock reads the duration from the file header
      ([DECISIONS #53](DECISIONS.md#53-diarization-uses-pyannote-31-under-the-weights-only-loader)).
      10 new tests, red-checked (6 of the first 7 failed on the previous
      code), plus a check on the real models.

    Suite 1794 passed locally; light stack 1790 passed, 2 skipped; ruff
    clean; mypy clean with `--no-site-packages`.
  - **Diarization without a Hugging Face login** (2026-10-24), a
    follow-up from the review of the diarization fix; on
    `sprint7/diarization-no-login`, not merged:
    - `670b4de` — the diarization service no longer calls
      `huggingface_hub.login()` on each cold load; the token still goes
      to pyannote with the load. No login network call, no token written
      to disk, and with the models cached diarization now loads under
      `HF_HUB_OFFLINE=1` ([DATA-FLOW.md](DATA-FLOW.md) §3). 3 new tests,
      red-checked (2 failed on the previous code; the token control
      passed), plus an offline check on the real models.

    Suite 1797 passed locally; light stack 1793 passed, 2 skipped; ruff
    clean; mypy clean with `--no-site-packages`.
- **2026-10-11 audit and documentation sync** (this branch): stale
  operational docs corrected, PHASE-0 wording made consistent with its own
  register, this status document created.

## In progress

- **Phase 1, gold set** — labelling may start now, before the rest of
  Phase 1 (D3's clause; [DECISIONS #52](DECISIONS.md#52-gold-set-labelling-may-start-before-the-phase-1-gate)).
  English, synthetic-first, Layers A+B; drafts are machine-assisted and the
  project owner checks every item; consistency is a blind re-label of 40
  items after one week. Data preparation, no code.
- **Prerequisite fixes** — only fixes that need no pending decision, one
  per milestone, smallest first. M1–M7, the old-rows fix, the decision-free
  hardening items, the owner-decided items and the pull-request review fix
  round are done. The items the owner parked are tracked privately.
- **Working rules** (project owner, 2026-10-16): the claim detector gets no
  new heuristics until Phase 3, only crash, security or performance fixes
  ([DECISIONS #26](DECISIONS.md#26-claim-detector-no-new-heuristics-until-phase-3));
  a milestone or security fix that grows past roughly twice its planned
  size, or needs a second rework round after review, stops for the owner
  ([DECISIONS #27](DECISIONS.md#27-size-rule-for-milestones-and-security-fixes)).

## Remaining

Ordered backlog. Phase numbers are the plan's.

1. **Open values** (see *Blockers*), each set before the phase that uses it.
2. **Prerequisite fixes**, smallest first (earliest phase each one gates
   in brackets):
   1. Java callback fires on every Agent Brain run — needs the D10 final-pass predicate and an exactly-one test; lands with Phase 6, which adds the re-run [6].
   2. Claim-detector limits left after M5 — past prices read as current ("I bought ETH at 1200"), capitalised acronyms read as tickers, any number after a coin, ticker or commodity read as a price ("Gold is 29.99 with free shipping"); listed in [FEATURES.md §4.3](FEATURES.md#43-known-functional-limits-of-the-detector). *Wait for Phase 3* (DECISIONS #26) [3].
   3. A Unicode-capable PDF font — M6 only contains the failure (DECISIONS #31) [6].
3. **Phase 1** — gold set (may start now, DECISIONS #52), local model server (U3), the D6 criteria note before the first bake-off run, the bake-off, a pinned model and runtime.
4. **Phase 2** — typed v2 models, OpenAI-compatible client, the `VOICEIQ_` settings named in PHASE-0 §7 (claim cap and run deadline renamed `FACTCHECK_AGENT_*`; no key reuses an existing name), stubbed boundary, the DB-polled runner's run model (U1).
5. **Phase 3** — claim extraction with segment/speaker/timestamp traceability, checkability gate, dedup, claim cap.
6. **Phase 4** — retrieval interface, trusted clients first, SearXNG adapter, `url_guard` SSRF controls, frozen fixtures.
7. **Phase 5** — evidence extraction, evidence-only verdicts, citation-ID validation, deterministic confidence, evidence cache.
8. **Phase 6** — job runner and `/v2/fact-check` routes, new tables, `fact_check_report`, v2 verdict map, D10 re-run (Agent Brain, then PDF) with exactly one callback, Unicode-safe cited PDF.
9. **Phase 7** — deadlines, concurrency bounds, circuit breaking, metrics, `DATA-FLOW.md` update, staged rollout.

## Decisions

Register: [`FACTCHECK-AGENT-PHASE-0.md` §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
Summary counts only: the **10** original register decisions are signed, and
**17** rows were added and signed on 2026-10-19 (D3b, D10-T, U1–U3, U4a–b,
U5a–c, U6, U7, U8a–c, U9, U10), so the register has 27 rows. Phase 0
exit deliverables: **3 / 3** approved. One project owner (sole maintainer)
holds every role ([DECISIONS #50](DECISIONS.md#50-one-owner-holds-every-phase-0-role)).

## Blockers

Real blockers only. None blocks the start of Phase 1; the D5 run deadline
is needed before the first bake-off run. Each open value below is listed in
[PHASE-0 §8](FACTCHECK-AGENT-PHASE-0.md#8-exit-checklist) and must be set
before the phase that uses it:

- the agent's provisional run deadline (D5) — before the D6 criteria note
  is signed (first bake-off run), then the Phase 2 runner and admission
  defaults that follow it;
- the maximum evidence age per claim type, the remaining provider and
  Wikipedia terms, and the maximum excerpt length (PHASE-0 §6.3–§6.5) —
  Phases 4 and 5;
- whether `fact_check_report` shows `disabled` or `skipped` when the agent is
  enabled but its tables are missing (U7) — Phase 6;
- the `app.main` deployment recipe (U9) — before Phase 6 testing;
- whether `app.main` shares its `data/` volume with `app.insight_main` (U9)
  — phase not set (open);
- the Java action layer team's acknowledgement of PHASE-0 §3 — before the
  callback changes of Phase 6.

## Next milestone

**Phase 1 — model bake-off** (plan v1.1, Phase 1). It starts with the gold
set, which may begin now: labelling is data preparation and needs no code.
Before the first bake-off run: D5's open run deadline is set, the D6
criteria note (who may win, the thresholds and the licence check), whose
per-claim latency gate uses that figure, is signed, and the local model server of
record is set up (U3: llama.cpp with model files pinned by revision and
SHA-256). Fetching model files needs the project owner's go.

## Acceptance evidence

Phase 1 is complete when all of the following hold (plan v1.1, Phase 1 exit
deliverables):

- the Layer A+B gold set is frozen and versioned, with its intra-rater
  agreement reported (D3);
- the bake-off has run on the gold set with the D6 criteria note signed
  first, and its report gives the measured baseline (extraction precision and
  recall, checkability F1, JSON validity, memory and latency on the D5
  machine);
- a model and its serving runtime are pinned;
- the operating limits move from provisional to binding where Phase 1
  measured them, including D5's open run deadline;
- this document moves Phase 1 to *Completed*.
