# Fact-Check Agent — Phase 0: Contract and Scope Lock

Owner: engineering · Status: **signed 2026-10-19 — every §1 decision signed; §2, §3 and §6 approved; some values still open, see §8** · Created: 2026-09-04 (Phase 0 opened)
Source plan: `VoiceIQ_Fact_Check_Agent_Implementation_Plan.pdf` **v1.1**

Phase 0 ships **no application code**. Its deliverables are the four policies
below plus a signed decision register. No phase of 1–7 starts until the §1
decisions that gate it (listed in §1 and §8) are signed and §2, §3 and §6 are
approved — that is the whole point of the phase: freeze the contract so later
phases cannot churn it. *(This per-phase reading is in force: W1 was
confirmed on 2026-10-19 — see §1.)*

Everything here is **normative**. Where a rule says *must*, a later phase that
breaks it is a defect, not a judgement call.

| Exit deliverable (plan §6, Phase 0) | Where | Status |
|---|---|---|
| Approved API schema + compatibility policy | §2, §3 | ✅ **approved 2026-10-19** — A2 and A3, with their amendments; §5 and §7 approved with §3 (W7). **open:** the Java action layer team's acknowledgement of §3 |
| Approved privacy boundary + source policy | §6 | ✅ **approved 2026-10-19** — A6, with W6a–W6f merged |
| Persistence option, execution mode, decision table w/ owners + dates | §1, §4 | ✅ D1, D2, D10 **signed**; D3–D9, D3b, D10-T and U1–U10 **signed 2026-10-19** |

---

## 1. Decision register

Ten decisions (D10 was added on 2026-09-20, raised by N4b L5), plus D3b,
D10-T and U1–U10, registered on 2026-10-19 under W1 (below). Each gates a
phase, following its *Needed by* column: D1–D5 and D7 (*+N days* from
kick-off) block Phase 1; D6 must be signed before Phase 1 ends; D9 blocks
Phase 5; D8 and D10 block Phase 6; each registered row names its own phase.
v1.0 of the plan listed the questions but assigned neither owner nor date,
so none could be chased.

**W1 — confirmed, in force** (owner decision 2026-10-19). The per-phase
reading above was written on 2026-10-11 to follow the register's own *Needed
by* dates. It replaces plan v1.1 §11's "every row blocks Phase 1", which
contradicts those dates. The project owner (sole maintainer) confirmed it on
2026-10-19 in the tech lead and engineering manager roles. Unregistered
decisions are added to this table, each with its own phase date. See
[`FACT-CHECK-AGENT-STATUS.md`](FACT-CHECK-AGENT-STATUS.md).

`Needed by` is relative to the Phase 0 kick-off date. **Gate record G1**
(owner decision 2026-10-19) records two dates: Phase 0 opened 2026-09-04
(contract drafted; D1 and D2 signed), and the decision clock for the
*+N days* rows still open started 2026-10-19; D3, D4, D5 and D7 were signed
that day. Signers are recorded by role: every row signed on 2026-10-19 was
signed by the project owner (sole maintainer), who holds every internal role
in the Owner column (tech lead, engineering manager, product, senior
sponsor, infrastructure, legal). Names are kept only in a private sign-off
record outside the repository.

| ID | Decision | Owner | Needed by | Recommendation | Status |
|----|----------|-------|-----------|----------------|--------|
| D1 | Persistence: new tables only, or adopt Alembic? | Tech lead | +2 days | **Option A — new tables only** | ✅ **signed 2026-09-04 — Option A** |
| D2 | Synchronous or job-based execution? | Tech lead + product | +3 days | **Job-based** until hardware proves otherwise | ✅ **signed 2026-09-04 — job-based** |
| D3 | Who owns and resources the gold set? | Eng manager | +2 days | — (must be named) | ✅ **signed 2026-10-19 — Option A**: Layers A+B, English, synthetic-first; labelling (data preparation, no code) may start before the Phase 1 gate. Gold-set owner: project owner (sole maintainer); drafts are machine-assisted and the owner checks every item; consistency check: the owner re-labels 40 items blind after one week (intra-rater agreement), in place of a second annotator; verdict labels move to D3b |
| D3b | Verdict labels for the gold set (evidence-relative), split from D3 | Project owner (sole maintainer) | with Phase 4, after D4 | Evidence-relative labels after Phase 4 and D4 | ✅ **owner and approach signed 2026-10-19** with D3; labelling waits for Phase 4 and excludes out-of-scope rows |
| D4 | Does "open-source" cover web search too, or only the LLM? | Senior sponsor | +3 days | Self-host search (SearXNG) | ✅ **signed 2026-10-19 — Option B**: covers the LLM and the search software; self-hosted SearXNG, whose upstream engines are third-party destinations governed by U4b |
| D5 | Production GPU/RAM, and max acceptable fact-check latency? | Infrastructure | +5 days | — (must be measured) | ✅ **signed 2026-10-19** — model server on the VoiceIQ host's single NVIDIA laptop GPU (8 GB VRAM; 16 GB-class system RAM), shared with the audio pipeline, in its own process, never a third-party hosted API; Phase 1 measures on this host; p95 ≤ 5 min from run submission (end of audio processing; acceptance for /v2) to the final result (end of the D10 final pass for process-audio runs, terminal status for /v2 runs) at the claim cap, queueing included. **provisional run deadline set 2026-10-28** (project owner (sole maintainer), in the Infrastructure role, latency co-signed in the Product role): 125 s (`FACTCHECK_AGENT_RUN_DEADLINE_SEC`, §7), with a D10 re-run allowance of 25 s recorded beside it (§7); one run may wait, since (1 + 1) × (125 s + 25 s) = 300 s; re-checked once Phase 1 has measured latency |
| D6 | Is Qwen3 mandatory, or may Qwen3.5 win the bake-off? | Senior sponsor | before Phase 1 ends | Let the measurement decide | ✅ **signed 2026-10-19 — Option 2**: Qwen3.5-4B may win under a criteria note signed before the first bake-off run; Qwen3-4B stays the default; the Phase 1 pin stays provisional on verdict quality until D3b labels exist. Criteria note signed before any bake-off run: S1 on 2026-10-28 ([D6-CRITERIA-NOTE-S1.md](D6-CRITERIA-NOTE-S1.md)), replaced on 2026-10-30 by S2 ([D6-CRITERIA-NOTE-S2.md](D6-CRITERIA-NOTE-S2.md)), which changes only the launch line (literal margin reading) |
| D7 | Which fact domains ship first? | Product | +5 days | General knowledge + finance; **exclude** healthcare/legal | ✅ **signed 2026-10-19 — Option B**: general knowledge, market data and weather; weather and stock prices only once their API keys are confirmed in the target deployment or D4's web fallback exists; healthcare, legal, personal-financial advice, current news and own-company claims are out of scope, get no verdict and create no review recommendation |
| D8 | How long must v1 + legacy process-audio fields survive? | Product + consumers | before Phase 6 | Indefinitely within this project | ✅ **signed 2026-10-19 — Option B**, in the product role: v1 routes and fields unchanged through this project (Phase 3 detector improvements may change v1 results); removal later needs its own ticket, v2 meeting its accuracy gates, staged-rollout sign-off, written API-consumer sign-off and 1 month's notice |
| D9 | Source-authority rules for high-stakes claims? | Product + legal | before Phase 5 | Tier 1 only — see §6.3 | ✅ **signed 2026-10-19 — Option A**: Tier 1 only, with four clauses — high-stakes defined (health, legal, personal financial); tiers from a reviewed domain table, never the model; classification fails closed; non-enabled domains stopped before retrieval |
| D10 | **Raised by N4b L5.** Does the fact-check job re-run *every* downstream consumer, or only the PDF? | Tech lead | before Phase 6 | A — re-run all consumers | ✅ **signed 2026-09-20 — Option A** |
| D10-T | D10 edge cases: which pass sends the Java callback, and the delivery rule | Project owner (sole maintainer) | before Phase 6 | A — at most once | ✅ **signed 2026-10-19 — Option A**: only the pass that moves the run from pending to a terminal state sends the callback; delivery at most once (record, then send), so a crash between the two leaves no callback and is logged. The Java receiver is run by a separate party |
| U1 | Job runner behind D2 | Project owner (sole maintainer) | before Phase 2 | B — DB-polled worker | ✅ **signed 2026-10-19 — Option B**: `factcheck_agent_runs` is the queue; runs are claimed under a lease with an attempt cap |
| U2 | Supported languages for the agent, its report and the gold set | Project owner (sole maintainer) | before Phase 1; PDF script support before Phase 6 | B — English first, code-switched later | ✅ **signed 2026-10-19 — Option B**: English at launch; code-switched Urdu/English (Roman script) only after a measured, signed-off step; other languages skipped with a stated reason; Phase 1 gold set English-only |
| U3 | Local development model server | Project owner (sole maintainer) | before Phase 1 (before the first bake-off run) | A — llama.cpp | ✅ **signed 2026-10-19 — Option A**: llama.cpp `llama-server` serving GGUF files pinned by revision and SHA-256; Ollama only for day-to-day work on the same files |
| U4a | Who hosts and runs SearXNG | Project owner (sole maintainer) | before Phase 4 | A — internal sidecar | ✅ **signed 2026-10-19 — Option A**: pinned, internal-only sidecar, operated by the project owner (sole maintainer); no public or third-party instance |
| U4b | robots.txt and terms position for page fetching and search engines | Project owner (sole maintainer) | before Phase 4 | A — allow-list only | ✅ **signed 2026-10-19 — Option A**: fetch only allow-listed domains whose terms are cleared; robots.txt always honoured; excerpts only |
| U5a | `POST /v2/fact-check` request schema and the run envelope | Project owner (sole maintainer) | with the §2 approval (before Phase 1) | A — camelCase segment-based body, one envelope | ✅ **signed 2026-10-19 — Option A** |
| U5b | Non-checkable and out-of-scope statements in v2 output | Project owner (sole maintainer) | with the §2 approval (before Phase 1) | A — separate list | ✅ **signed 2026-10-19 — Option A**: a separate `nonCheckable[]` list, never a verdict |
| U5c | `/v2` auth, payload caps, rate limits and run admission | Project owner (sole maintainer) | with the §3 approval (before Phase 1); numbers after D5 | A — v1 mechanisms, v2-specific groups | ✅ **signed 2026-10-19 — Option A**; numeric defaults re-checked once Phase 1 has measured latency |
| U6 | When `fact_check_report` is absent or `disabled`, and how the flags and `?fact_check` combine | Project owner (sole maintainer) | before Phase 1 (with the §2 and §6 approvals) | A — master flag plus the existing consent gate | ✅ **signed 2026-10-19 — Option A** |
| U7 | How the new tables reach a deployment with `VOICEIQ_DATABASE_AUTO_CREATE=false` | Project owner (sole maintainer) | before Phase 6 | A — operators own the schema | ✅ **signed 2026-10-19 — Option A**: no silent creation; missing tables are logged at startup. **open:** whether `fact_check_report` then shows `disabled` or `skipped` |
| U8a | Where the D10 re-run gets its inputs | Project owner (sole maintainer) | before Phase 6 | A-now — snapshot to the job folder | ✅ **signed 2026-10-19 — A-now**: the re-run reads only the job folder and the run's results; if the folder is gone, the run ends `failed` |
| U8b | How the regenerated PDF and final recommendations are delivered | Project owner (sole maintainer) | with the §2 approval (before Phase 1) | A-now — read back through `GET /v2/fact-check/{runId}` | ✅ **signed 2026-10-19 — A-now**: the read-back fields are written into §2 |
| U8c | How agent run rows age out | Project owner (sole maintainer) | before Phase 6 | A — rows follow `db_retention_days` | ✅ **signed 2026-10-19 — Option A**: rows follow `db_retention_days`; job-folder artifacts and stored request input follow `job_retention_hours` |
| U9 | Production deployment shape of `app.main` | Project owner (sole maintainer) | before Phase 1 | A — one host, one process, SQLite | ✅ **signed 2026-10-19 — Option A**: one `app.main` process on one host, SQLite. **open:** whether it shares its `data/` volume with `app.insight_main`. **open:** the deployment recipe, to be written before Phase 6 testing |
| U10 | Real-call corpus: whose calls, what consent, what retention | Project owner (sole maintainer) | before any real call is used (gold set, code-switched slice, Phase 7 shadow run) | B — staged: synthetic now, real calls under a written rule | ✅ **signed 2026-10-19**: synthetic only until a written rule for real calls exists |

### D1 — Persistence · ✅ **signed: Option A, new tables only**

**Evidence.** `app/insights/repository/db.py` ends `init_db()` with:

```python
Base.metadata.create_all(bind=engine)
```

There is no Alembic in this repository. `create_all()` creates **missing
tables**; it never issues `ALTER TABLE`. A new column on the existing
`fact_check_results` table therefore appears on a fresh database and is
silently absent on any database that already holds rows — every
fresh-database test passes, and the failure only reaches an environment with
prior data.

| | Option A — new tables only | Option B — adopt Alembic |
|---|---|---|
| Works today | yes | no, needs its own workstream |
| Touches existing tables | never | yes |
| Cost | zero | own project, own approval, own risk |
| Blocks Phase 6 | no | yes, until shipped |

**Decision: Option A.** The new agent's results are a new shape
anyway — run, claim, evidence and citation records are one-to-many and do not
belong bolted onto the flat `fact_check_results` row. Option B remains
available later as an independent decision; nothing in this plan should
create pressure to rush it.

**Consequence:** §4 is binding. No phase may assume `ALTER TABLE`.

### D2 — Execution mode · ✅ **signed: job-based**

Order-of-magnitude anchor (plan §5, to be replaced by Phase 1 measurement):
a 4B quantized model on CPU runs ~5–15 tokens/sec, and each claim costs two
model calls — its share of extraction, plus reasoning. Twenty claims plus
bounded retrieval is **minutes on CPU**, low single-digit seconds per claim on
a modern GPU, and that is additive on top of the tens of seconds ASR and
diarization already spend.

This decides the shape of the integration, so it cannot be deferred to Phase 1:

- **Synchronous** — the agent runs inside `orchestrator.run()` before PDF
  generation. Simple; the PDF gets real verdicts on the first pass. Only
  viable if p95 lands inside the existing request budget.
- **Job-based** — the agent runs after the audio response returns, and the
  report is regenerated or amended. Survives slow hardware; costs a second
  artifact-write path and a status field.

**Decision: job-based.** Designing synchronous-first and retrofitting async
is the expensive direction, and D5 has not yet supplied hardware that would
make synchronous viable. If D5 later reports a GPU that fits p95 inside the
existing request budget, a synchronous path may be added as an *option* —
`FACTCHECK_EXECUTION_MODE=sync` — but the job-based path stays the default
and stays supported.

**Consequences, binding on Phases 2, 5 and 6:**

1. `/v1/process-audio` returns **before** the fact-check finishes. Its
   `fact_check_report` key therefore carries a *state*, not just results —
   see §2.3.
2. The run needs a retrievable identity and a terminal status, so
   `factcheck_agent_runs` (§4) carries `status` and both timestamps. A run
   that is never collected must still age out under the existing retention
   sweep: its rows on `db_retention_days`, its job-folder artifacts on
   `job_retention_hours` — see U8c under §D10.

   *Reworded by U8c (owner decision 2026-10-19).* This originally named
   only `job_retention_hours`; since 2026-10-17 the sweep also deletes
   database rows, on their own clock, `db_retention_days`.
3. **Downstream consumers** cannot contain verdicts on the first pass. Phase 6
   must either re-run them when the job completes, or emit them only after
   completion. **Nothing may silently ship with an empty fact-check
   section** — that is the same class of defect as the ordering bug this
   whole sprint exists to fix.

   *Reworded by D10.* This originally named only the PDF, because the PDF was
   the only consumer in scope when D2 was signed. There are now two — see
   §D10 for the list and the re-run rule.
4. Failure of the job never changes the audio-processing status. That
   response has already been sent.

#### U1 — job runner · ✅ **signed: Option B, DB-polled worker** (owner decision 2026-10-19)

`factcheck_agent_runs` is the queue. Binding on Phases 2 and 6:

- **One consumer per app process**, started from the lifespan only when the
  agent flag is on (R3), with job concurrency **1** by default. It claims
  only runs of an origin it can serve, so `app.insight_main`, which has no
  job folders, never claims a `process-audio` run.
- **Claims are atomic, under a lease, with an attempt cap.** An expired
  lease is reclaimed until the cap is reached; after that the run is closed
  as `failed` (U8c). If the flag is turned off for rollback, pending runs
  are closed as `failed` at startup or by U8c's stale-run close, and D10-T
  governs their callback. The lease outlasts the run deadline plus the longest
  model call with its retry plus the D10 re-run time, or is renewed while
  the worker lives. D5's provisional run deadline is 125 s and the D10
  re-run allowance 25 s (2026-10-28, §7).
- **Only the worker that still holds the claim may finish a run**, through a
  conditional update on run, worker and attempt, and only that worker runs
  the D10 final pass. A retried attempt never duplicates claim, evidence or
  citation rows.
- **The callback is sent inside the job and recorded** (D10-T), not from a
  daemon thread.
- **The claim, lease, origin and callback fields are in the Phase 2 run
  model** from the first create, because D1 forbids adding them once the
  table exists (§4). The runner's §7 keys (concurrency, poll interval, lease
  length) default safe and are named `FACTCHECK_AGENT_*` (A3 amendment (i)).
- **Acceptance:** the exactly-one-callback tests also hold across a restart,
  and when a lease expires while the first worker is still alive, except in
  D10-T's accepted zero-callback cases.

#### U9 — where `app.main` runs · ✅ **signed: Option A, one host** (owner decision 2026-10-19)

For the first release `app.main` runs as **one process on one host** (the
VoiceIQ host) **with SQLite** — the shape U1 and D5 design against. Phase 7
revisits it before any wider rollout.

- **Open:** whether `app.main` shares its `data/` volume with
  `app.insight_main` — not yet decided.
- **Open:** the `app.main` deployment recipe (image or service definition,
  with its restart policy) — none exists yet; it is to be written before
  Phase 6 testing.

### D10 — does the fact-check job re-run downstream consumers? *(raised by N4b L5)*

**Not a new architecture — a widening of a decision already taken.** §D2
consequence 3 says the PDF cannot hold verdicts on the first pass and must be
regenerated or deferred when the job completes. That is a
*downstream-consumer* rule, and the PDF was simply the only consumer in scope
when D2 was signed.

N4b L5 added a second one. `/v1/process-audio` now optionally runs the Agent
Brain over the pipeline's own output
(`VOICEIQ_AGENT_BRAIN_AUTO_RUN`, off by default). It runs inside the
orchestrator, before the response returns — so under D2 a `FactCheckResponse`
does not exist yet and will not by the time it runs.

Consequence today: the Agent Brain's stage passes `fact_check=None`, so
**`FactCheckReviewAgent` is dormant on the pipeline path**. The other four
agents are unaffected. That is recorded behaviour, not a defect — but it
means a `CONTRADICTED` verdict produces no manual-review recommendation from
this path until Phase 6 ships the D10 re-run.

| Option | Consequence |
|---|---|
| **A — job re-runs all consumers** | One Agent Brain run with complete input; `FactCheckReviewAgent` works. Costs a second agent run, or deferring the first. Consistent with whatever the PDF does. |
| **B — job re-runs the PDF only** | `FactCheckReviewAgent` stays dormant on this path permanently. Fact-check-driven review then belongs to Java via the pull route, and that should be stated rather than left implicit. |

### ✅ Decision: Option A — the job re-runs every downstream consumer

Signed 2026-09-20. B was rejected because it leaves one of five agents
permanently dead on the push path without saying so anywhere a reader would
look. §D2 consequence 3 is reworded accordingly.

**The consumer list is now normative.** When the fact-check job reaches a
terminal state, Phase 6 re-runs, in this order:

| # | Consumer | Why it must re-run |
|---|---|---|
| 1 | Agent Brain (`_run_agent_brain`) | `FactCheckReviewAgent` needs the claims; the other four agents are unaffected by the re-run but are re-run with it, because splitting them would mean two recommendation sets per session. |
| 2 | PDF (`_run_pdf`) | Renders verdicts, confidence and citations, **and** whatever the Agent Brain just produced. Must run *after* 1. |

Adding a third consumer later means adding a row here, not rediscovering the
rule.

#### U8a — where the re-run gets its inputs · ✅ **signed: A-now, snapshot in the job folder** (owner decision 2026-10-19)

The first Agent Brain pass writes its exact inputs to the job folder, and
the re-run rebuilds the Agent Brain and PDF inputs **only** from that folder
plus the run's results. If the folder is gone when the re-run starts, the
run ends as `failed` (D10-T then governs the callback). Phase 6 tests that
the first-pass and reloaded inputs match (sha256 over canonical JSON).

#### The trap Option A creates: do not fire the Java callback twice

This is the part that will bite Phase 6 if it is not written down now.

The Agent Brain currently fires an HMAC callback to the Java Action Layer at
the end of its run (L5-2, `_dispatch_agent_callback`, off unless a URL *and*
a secret are configured). Under Option A the Agent Brain runs **twice** for
one session — once in-pipeline with `fact_check=None`, once after the job
with the claims. Naively, Java receives two callbacks carrying **different
recommendation sets for the same `sessionId`**, and has no rule for which
wins.

Java's replay check does **not** save this. It is keyed on the v2
signature ([`JAVA-CALLBACK-V2.md`](JAVA-CALLBACK-V2.md) §4 step 5); each
send is signed separately and the two payloads genuinely differ, so they are
two legitimate messages rather than a replay. *(Corrected under W3, owner
decision 2026-10-19: this read "trace-id de-duplication"; the trace id is a
per-send correlation id, not the replay key.)*

**Rule.** When the fact-check feature is enabled for a request, the
in-pipeline Agent Brain run is *provisional*: it populates the response and
the artifact but **must not fire the callback**. Only the post-job re-run
fires it. When fact-check is disabled, skipped or the feature flag is off,
there is no second pass and the in-pipeline run fires the callback as it does
today.

Stated as a predicate Phase 6 can implement directly:

```
fire_callback  ==  (this is the final pass for this session)
```

**Acceptance:** a test asserting that exactly one callback is dispatched per
session when both the Agent Brain and the fact-check job are enabled. Not
"at least one" — exactly one.

#### D10-T — edge cases of the final-pass rule · ✅ **signed: Option A, at-most-once delivery** (owner decision 2026-10-19)

- **The final pass** is the one that atomically moves the run from `pending`
  to `complete` or `failed`. Only that pass dispatches the Java callback. A
  lost job is closed as `failed` by the same change (U8c), so it still gets
  its final pass, and closing it cannot cause a second one.
- **Delivery is at most once:** the job records the dispatch, then sends it.
  The Java receiver is run by a separate party.
- **Accepted zero-callback cases** — every other session gets exactly one:
  1. no callback is configured;
  2. no final-pass recommendation meets `min_confidence`, including when the
     Agent Brain does not run on the final pass;
  3. a crash between recording and sending, which is logged.
- **A refused submission is not one of them** (U6). The U6 gate is resolved
  before the pipeline runs, so the Agent Brain stage knows whether its pass
  is provisional, and Phase 6 either submits or reserves the run before
  that stage or fires the held-back callback when submission fails.

The acceptance test above and U1's tests use this list and no other.

#### U8c — how agent runs age out · ✅ **signed: Option A** (owner decision 2026-10-19)

| Data | Clock |
|---|---|
| `factcheck_agent_*` run, claim, evidence and citation rows | `db_retention_days`, by the run's `started_at`, so runs that never finished still age out; children are deleted first |
| Stored `/v2` request input (transcript segments, speaker ids) | `job_retention_hours`, or it is not stored at all |
| Job-folder artifacts, including the U8a snapshot | `job_retention_hours`, unchanged |

A run still `pending` once its lease (U1) expires is closed as `failed` by
the same atomic pending-to-failed change D10-T uses, well inside
`job_retention_hours`, so its final pass can still read the job folder.
§D2 consequence 2 is reworded to match.

#### What does not change

- **Dormancy is now time-bounded, not permanent.** `FactCheckReviewAgent`
  stays dormant on the pipeline path until Phase 6 ships the re-run. Nothing
  in the current code changes because of this decision; D10 is a Phase 6
  obligation, and signing it does not un-dormant anything today.
- The first pass still populates `recommendations` in the
  `/v1/process-audio` response. That response has already been sent by the
  time the job finishes, so the second pass updates the stored artifact and
  the callback — not the response that already went out. Consumers who need
  the final set read it back, the same way they read `fact_check_report`:
  once the run is terminal, `GET /v2/fact-check/{runId}` also returns the
  final recommendations and the regenerated PDF while the job folder exists
  (U8b, §2). Until then the first-pass recommendations are provisional and
  the first-pass PDF says the fact-check is pending.

### Errata against plan v1.1 · ✅ **W3 confirmed** (owner decision 2026-10-19)

The plan PDF is historical and is not edited, so text that later decisions
superseded is corrected here. None of these changes a decision.

| Plan v1.1 | Read instead |
|---|---|
| §6, Phase 6, first implementation bullet: run the agent before PDF generation, inside the orchestrator | **Superseded** by D2 (job-based: the agent runs after the audio response returns) and D10 (the job then re-runs every downstream consumer). |
| §2 non-goals: high-stakes domains need the source-authority rules of "decision D6" | **D9**, as the plan's own §11 table and this register say. |

W3 also corrected §D10 above: the Java mechanism is the v2-signature replay
check, not trace-id de-duplication.

---

## 2. API contract lock

✅ **Approved 2026-10-19** by the project owner (sole maintainer) as the
Phase 0 API-schema exit deliverable: A2, Option 2, with amendments (a)–(i),
each written into the subsection it changes and tagged *A2 amendment* (owner
decision 2026-10-19). U5c (§2.2) and D8 (§2.1) were signed the same day.

### 2.1 v1 is frozen

Recorded here verbatim so drift is detectable by diff. Source:
`app/insights/models/factcheck_models.py`.

```
POST /v1/fact-check
  request  : FactCheckRequest  { conversation_id, speaker_id, transcript_text }
  response : FactCheckResponse { conversation_id, speaker_id,
                                 fact_check_results[], stats }

ClaimType  = CURRENCY_RATE | COMMODITY_PRICE | CRYPTO_PRICE
           | STOCK_PRICE | WEATHER | STATIC_FACT
Verdict    = TRUE | FALSE | PARTIALLY_TRUE
           | UNVERIFIED | UNSUPPORTED_CLAIM_TYPE | SOURCE_UNAVAILABLE
ConfidenceLabel = HIGH | MEDIUM | LOW | NONE
MAX_TRANSCRIPT_CHARS = 10_000
```

**Rule.** No field of `FactCheckRequest`, `FactCheckResponse`,
`FactCheckResult`, `DetectedClaim`, `ClaimSpan` (the type of
`DetectedClaim.span`), `Evidence`, `Confidence` or `FactCheckStats` may be
added, removed, renamed or retyped by this project. *(`ClaimSpan` was added
to this list on 2026-10-11; confirmed by the project owner (sole maintainer)
on 2026-10-19, W2.)*
No member may be added to or removed from `ClaimType`, `Verdict` or
`ConfidenceLabel`.

**Freeze by reachability** (W2 Option 2, A2 amendment (a)). The list above
is not the boundary: no model, `Literal` or field constraint reachable from
`FactCheckRequest`, `FactCheckResponse` or the rows `GET /v1/fact-check/{id}`
returns may change. That explicitly covers `ClaimSpan`, `ConfidenceLabel`,
`MAX_TRANSCRIPT_CHARS` and constraints such as `max_length` and the
`Confidence.score` bounds, and a nested type is covered without a list edit.

`GET /v1/fact-check/{id}` is frozen on the same terms (A2 amendment (e)). It
returns untyped repository rows, so its contract is a key set: the keys of
`FactCheckResultORM.to_dict()` plus those `FactCheckRepository._row_to_dict()`
adds. No key may be added, removed, renamed or retyped.

The new agent gets its **own** models. It does not extend these.

**Support window** (D8, Option B; owner decision 2026-10-19). `POST` and
`GET /v1/fact-check` and the process-audio keys `fact_checks` and
`fact_checks_v2` stay supported, unchanged, through Phase 7 of this project.
*Unchanged* means the routes, the §2.1 models and enums, the keys and their
status values, and the meaning of the v1 settings and of the `?fact_check` /
`VOICEIQ_FACTCHECK_AUTO_ENRICH` opt-in gate stay fixed. Behaviour fixes stay
allowed, and Phase 3 detector improvements may change v1 results. The PDF
keeps its legacy "Fact-Check Candidates" section. Removal needs a separate
ticket with all of: v2 meeting its binding accuracy gates, staged-rollout
sign-off, written sign-off from the API consumer naming the fields it reads,
and 1 month's notice.

### 2.2 v2 contract

Two new routes, new models, no shared mutable types with v1:

- `POST /v2/fact-check` — submit a session for verification. Answers
  **202** with the run envelope (§2.3); the caller always polls.
- `GET /v2/fact-check/{runId}` — collect a run. **Required**, not optional:
  D2 is job-based, so without it a submitted run is unreachable. Returns the
  same envelope; **404** when the `runId` is unknown or has aged out (U8c).

**Request body** (U5a Option A, A2 amendment (b)). A v2-owned camelCase
model. Caps on segment count, characters per segment and total characters
live in the model; the body carries no client-supplied URLs, sources or
prompts.

```jsonc
{
  "sessionId": "…",
  "language": "en",           // optional; v2-owned literal, values set by U2
  "segments": [
    { "segmentId": "…", "speakerId": "…", "start": 0.0, "end": 4.2,
      "text": "…", "asrConfidence": 0.91 }    // asrConfidence optional
  ]
}
```

**Access and load** (U5c Option A; owner decision 2026-10-19). The `/v2`
router is included in both apps and is not mounted when
`FACTCHECK_AGENT_ENABLED` is false (R3). It uses `verify_api_key` plus a
`/v2/fact-check` request-guard row, the session payload cap
(`api_max_session_payload_kb`, 2048 KB) with finite-JSON parsing, separate
submit and read rate-limit groups (`RATE_LIMIT_FACTCHECK_V2_*`, §3 naming
rule), and a run admission bound that answers 503 with `Retry-After` before a
run row is created. API keys are one trust group (DECISIONS #33), so any key
may read any run until #33 is revisited. A direct `POST /v2/fact-check`
counts as consent (A6).
**Numbers are provisional:** confirmed after D5, with the waiting depth
derived from D5's p95 target (which includes queueing), and re-checked once
Phase 1 has measured latency. With D5's provisional run deadline of 125 s
and the 25 s re-run allowance (2026-10-28), the waiting depth is one run
(§7).

Verdict set — four members, deliberately renamed away from v1's truth
language because the agent reports *what the evidence supports*, not what is
true:

| Verdict | Meaning |
|---|---|
| `SUPPORTED` | Retrieved evidence supports the claim. |
| `CONTRADICTED` | Retrieved evidence contradicts the claim. |
| `PARTIALLY_SUPPORTED` | Partly supported; a material part is unsupported or contradicted. |
| `INSUFFICIENT_EVIDENCE` | Not enough usable evidence to decide. Also the failure verdict. |

`NOT_CHECKABLE` is **not** a verdict. Checkability is a separate gate that
runs first; a non-checkable statement never reaches the verdict stage and
never receives a verdict. See §5 for why this distinction is load-bearing.

**Out of scope is not a verdict either** (D7 rider (1), A2 amendment (i)). A
claim in a domain D7 does not enable is stopped before retrieval by a
domain-scope check (D9), kept separate from checkability, and never reaches
the verdict stage. Its §5 part, no recommendation, is approved through W7.

**`nonCheckable[]`** (U5b Option A, A2 amendment (c)). Non-checkable and
out-of-scope statements appear only in this separate list. `claims[]` holds
checkable, in-scope claims only, so its `checkability.isCheckable` is always
`true`. A `nonCheckable[]` entry has no `verdict`, `confidence` or
`citations` field at all:

```jsonc
{ "claimId": "claim_7", "text": "…", "speakerId": "SPEAKER_02",
  "segmentIds": ["segment_30"], "reasonKind": "opinion", "reason": "Fixed public text" }
```

`reasonKind` is a D3 checkability category (`opinion | prediction | request |
vague`) or `outOfScope`: a domain not enabled under D7, or a claim about a
private individual (A6). The D3 labelling guide uses the same values.

**Casing** (A2 amendment (f)). Existing keys keep their current casing.
Top-level process-audio keys are snake_case (`fact_check_report` itself);
Agent Brain payloads, the contents of `fact_check_report` and every `/v2`
payload are camelCase.

Per-claim result shape (camelCase on the wire, matching the Agent Brain /
Java convention):

```jsonc
{
  "claimId": "claim_1",
  "text": "The asserted claim",
  "speakerId": "SPEAKER_01",
  "segmentIds": ["segment_12"],
  "checkability": { "isCheckable": true, "reason": "Externally verifiable" },
  "verdict": "SUPPORTED",
  "confidence": 0.87,
  "reason": "Evidence-grounded explanation",
  "citations": [
    { "sourceId": "source_1", "title": "...", "url": "https://...",
      "excerpt": "...", "publisher": "...", "publishedAt": "...",
      "retrievedAt": "...", "tier": 1 }
  ],
  "model": { "name": "Qwen3-4B", "revision": "pinned-revision" },
  "cacheHit": false
}
```

`model.name` is a free string, not an enum (D6 rider (g), A2 amendment (h)),
so the D6 bake-off outcome never changes the contract.

**Invariants the schema must enforce, not merely document:**

1. `confidence` is `0.0 ≤ x ≤ 1.0`, computed deterministically outside the
   model, clamped with the shared `app/insights/core/_math.py:clamp`.
   A model-reported confidence value is never used as the score.
2. Every `citations[].sourceId` must exist in the evidence bundle supplied to
   the model for that claim. An unknown id is a hard validation failure, not
   a warning.
3. `reason` is a short evidence-grounded explanation. Private
   chain-of-thought is never returned.
4. `verdict` is one of exactly four members. An unparseable model response
   yields `INSUFFICIENT_EVIDENCE`, never a guess.

### 2.3 `process-audio` field policy

One new canonical key: **`fact_check_report`**.

| Key | Fate in this project |
|---|---|
| `fact_checks` (legacy, orchestrator) | untouched |
| `fact_checks_v2` (Sprint 5 engine, route-injected) | untouched |
| `fact_check_report` (new) | added; absent when the feature flag is off |

Because D2 is **job-based**, this key is a *state envelope*, not a result
set. The audio response has already returned by the time the agent runs, so
the key must be able to say "not finished yet" without a consumer having to
guess. It is the **one run envelope** (U5a, A2 amendment (b)):
`POST /v2/fact-check` and `GET /v2/fact-check/{runId}` return the same shape.

```jsonc
{
  "status": "pending",        // pending | complete | failed | disabled | skipped
  "runId": "…",               // random, not guessable; null when disabled,
                              // skipped, or a refused submission (failed)
  "resultsUrl": "/v2/fact-check/…",
  "claims": [],               // §2.2 shape; populated only when status == "complete"
  "nonCheckable": [],         // §2.2, U5b; never carries a verdict
  "stats": {},                // detected, checked, skipped by cause
  "model": { "name": "…", "revision": "…" },
  "reason": null              // fixed public text when failed or skipped
}
```

`GET /v2/fact-check/{runId}` is therefore part of the v2 contract, not an
optional extra: without it a job-based run is unreachable. `status` values
`disabled` and `skipped` reuse the vocabulary the existing `fact_checks_v2`
stub already returns, so consumers meet one convention rather than two.

**When each state applies** (U6 Option A, A2 amendment (d); owner decision
2026-10-19). `FACTCHECK_AGENT_ENABLED` decides whether the key exists; the
existing `?fact_check` / `VOICEIQ_FACTCHECK_AUTO_ENRICH` consent gate decides
whether the agent runs. No new query parameter.

| `fact_check_report` | When | First-pass PDF fact-check section |
|---|---|---|
| *absent* | `FACTCHECK_AGENT_ENABLED` is false, whatever else is set (R3) | omitted |
| `disabled` | flag on; not requested (`?fact_check=false` always forces this) | omitted |
| `skipped` | requested, but the transcript is empty, its language is not supported (U2) or the request is outside a rollout; fixed `reason` | "not run", with the fixed reason |
| `pending` | requested and a run accepted | "fact-check pending" |
| `failed` | submission refused (admission bound full or runner down; `runId` null), or the run ended with no usable result, including a lost run closed at lease expiry (U8c) and a final pass whose job folder is gone (U8a) | "fact-check failed" |
| `complete` | the run finished, including a partial run (rule 2 below) | the verdicts, or "no checkable claims found" |

**Open:** which state `fact_check_report` shows when the agent is enabled
but its tables are missing (U7), `disabled` or `skipped`, is not yet decided.
The audio response is unaffected either way.

**Phase 6 consequences** (U6): the gate is resolved before `orch.run()` and
passed in; the in-pipeline Agent Brain pass is provisional, and fires no
callback (§D10), exactly when the status is `pending`, which also covers
`sync` mode, where the status may already be `complete`; and the run is
submitted or reserved before the Agent Brain stage, or the held-back callback
fires when submission fails. A refused submission is not one of D10-T's
accepted zero-callback cases.

**Run rules** (U5a, A2 amendment (b)):

1. **Execution mode.** One contract in both modes: `POST /v2` always answers
   202 and the caller polls; under `sync`, `fact_check_report` may already be
   `complete` in the process-audio response.
2. **Partial runs.** A run that hits the claim cap or the run deadline ends
   `complete`, with the skipped claims counted by cause in `stats`, as v1
   does; `failed` means no usable result.
3. **Direct runs.** A run submitted directly to `/v2` re-runs no consumer and
   fires no Java callback; only runs started by process-audio do (D10). Each
   POST creates a new run, bounded by the U5c admission limit; an idempotency
   key may be added later as an additive field (R1).
4. **segmentIds.** On the process-audio path they are the 1-based position in
   the response's `speaker_segments`, written `utt_N` as the insight adapter
   already numbers them; on `/v2` they are the request's `segmentId`s.
5. **Run ids** are random and not guessable, like process-audio request ids.
6. **Untrusted input.** Segment text is caller-controlled and reaches the
   extractor and query builder, so the Phase 4 page-fetch controls (§6, W6c)
   apply to every fetch a `/v2` run makes.
7. **Storage.** Run, claim, evidence and citation rows follow
   `db_retention_days` (U8c); claim rows hold the claim text, speaker id and
   segment ids. The request input (segments, speaker ids) is stored only if
   the runner needs it, deleted when the run ends, and never kept longer than
   `job_retention_hours`.

**Read-back** (U8b option A-now, A2 amendment (g); owner decision
2026-10-19). Once a run is terminal, `GET /v2/fact-check/{runId}` also
returns the final Agent Brain recommendations (`RecommendationResponse`
shape) and the regenerated PDF (base64), while the job folder exists; a
direct `/v2` run has neither (rule 3). This is the read-back path §D10 refers
to. While the run is `pending`, the first-pass PDF says "fact-check pending"
and the `recommendations` in the process-audio response are provisional. The
PDF carries transcript text, so this read sits behind the U5c auth and read
limit, and SECURITY.md records it when it ships.

Deprecating either older key is a **separate later ticket** under the D8
removal criteria (§2.1). Not in scope here.

---

## 3. Compatibility policy

✅ **Approved 2026-10-19** by the project owner (sole maintainer) as the
Phase 0 compatibility-policy exit deliverable: A3, Option 2, with amendments
(i)–(iii) below (owner decision 2026-10-19). §5 and §7 are approved together
with it, and §7's numbers stay provisional until D5 and the Phase 1
measurement (W7).

**Open:** the Java action layer team's acknowledgement of the "Java
callback" row below. It is a separate party, and has not yet given it.

Normative restatement of plan §8. Three rules, then the matrix.

> **R1 — Additive only.** This project adds fields, routes, tables and config
> keys. It renames nothing and removes nothing.
>
> **R2 — A flag rolls back behaviour, not data.** A feature flag cannot
> un-send a field a consumer has already read, and cannot un-write a schema.
> Anything a flag cannot reverse must be additive by construction.
>
> **R3 — Off means silent.** With the feature flag off, the system must make
> zero model, search and page-fetch calls, and must produce no new keys.
> This is asserted by test, not assumed.

| Surface | Change | Type | Rollback |
|---|---|---|---|
| `POST /v1/fact-check` | none | no change | n/a |
| `GET /v1/fact-check/{id}` | none | no change | n/a |
| `POST /v2/fact-check` | new route | additive | flag off ⇒ not mounted (U5c); remove route |
| `GET /v2/fact-check/{runId}` | new route | additive | flag off ⇒ not mounted (U5c); remove route |
| `fact_checks` | none | no change | n/a |
| `fact_checks_v2` | none | no change | n/a |
| `fact_check_report` | new key | additive | flag off ⇒ key absent |
| Database | new tables only | additive | drop new tables |
| Agent Brain statuses | new verdicts mapped to **existing** statuses | additive | revert mapping |
| PDF report | new verdict + citations section | additive | flag off ⇒ section omitted |
| Stored PDF and recommendations | when `fact_check_report` is `pending` (U6), rewritten after the job (D10), so the copy in the process-audio response is provisional (U8b) | **behaviour change** | flag off ⇒ a single write |
| Java callback | when `fact_check_report` is `pending` (U6), fires once, on the final pass after the job, not with the response (D10); delivery at most once (D10-T) | **behaviour change**, not additive | flag off ⇒ today's timing |
| Config | new `VOICEIQ_` keys, all defaulting off/safe; none reuses an existing name (amendment (i)) | additive | unset ⇒ current behaviour |
| Network egress | **new destinations**: model server (operator-run, W6a), search backend with its upstream engines, page hosts; each recorded in `DATA-FLOW.md` in the change that adds it (W6b) | **new boundary** | flag off ⇒ zero calls (R3) |

**Amendments** (A3, Option 2; owner decision 2026-10-19):

- **(i) Config naming.** No new key may reuse an existing `VOICEIQ_` name;
  Phase 2 checks each new key against `settings.py`. The v1 engine already
  owns `FACTCHECK_MAX_CLAIMS` and `FACTCHECK_RUN_DEADLINE_SEC` (owner limits,
  DECISIONS #38), so the agent's claim cap and run deadline take the
  `FACTCHECK_AGENT_` names fixed under W7 (§7). Agent keys added later use
  `FACTCHECK_AGENT_*`; new rate-limit groups use `RATE_LIMIT_*`. The §7 text
  change is confirmed under W7.
- **(ii)** the "Stored PDF and recommendations" and "Java callback" rows (D10).
- **(iii)** the `/v2` rows' flag-off rollback: routes not mounted (U5c), so R3
  holds for `/v2` too.

---

## 4. Persistence policy *(binding — D1 signed Option A)*

**Allowed.** Creating new tables. `create_all()` picks them up on next start.

**Forbidden.** Adding, removing, renaming or retyping any column on
`fact_check_results`, `insight_records`, or any other existing table. Forbidden
because the mechanism to do it does not exist and its absence fails silently.

**Shape.** Four new tables, one-to-many downward:

```
factcheck_agent_runs      (run_id, session_id, origin, model_name, model_revision,
                           started_at, finished_at, status, counts…,
                           worker_id, claimed_at, lease_until, attempts,
                           error_class, callback_dispatched_at)
                          -- status is the §2.3 envelope state only; D2 is
                          -- job-based, so a run is submitted, polled and
                          -- collected, and must age out uncollected.
                          -- U1 (owner decision 2026-10-19): this table is the
                          -- job queue; origin is process-audio or /v2; the
                          -- claim, lease and callback fields exist from the
                          -- first create. callback_dispatched_at is written
                          -- before the send (D10-T: at most once)
factcheck_agent_claims    (claim_id, run_id → runs, text, speaker_id,
                           segment_ids, is_checkable, verdict, confidence, reason)
                          -- U5b: a nonCheckable[] entry has no verdict,
                          -- confidence or citations; its reason kind (a
                          -- checkability category or outOfScope) must be
                          -- storable from the first create
factcheck_agent_evidence  (evidence_id, claim_id → claims, url, title,
                           publisher, published_at, retrieved_at, excerpt,
                           content_hash, tier, stance)
factcheck_agent_citations (citation_id, claim_id → claims, evidence_id → evidence,
                           ordinal)
```

**Required regression test** (Phase 6 exit criterion): start the service
against a database that already contains the pre-change schema and rows, and
assert it serves traffic and writes a run. This is the only test that would
have caught the Option-B assumption in plan v1.0. Under U7 it gains a
`VOICEIQ_DATABASE_AUTO_CREATE=false` variant.

**Tables when auto-create is off** (U7, owner decision 2026-10-19). With
`VOICEIQ_DATABASE_AUTO_CREATE=false` the service never creates the
`factcheck_agent_*` tables; operators create them with a documented command,
and the command-line bootstrap does not report the schema as ensured when it
created nothing. If the agent is enabled and the tables are missing, startup
logs the missing table names, no run is attempted, and `fact_check_report`
shows a §2.3 state with a null `runId`; the audio response is unaffected.
**Open:** whether that state is `disabled` or `skipped` (U6 does not settle
it). The check stays out of `/readyz`, which would report the whole service
unavailable over an opt-in feature.

**Retention** (U8c, owner decision 2026-10-19). Run, claim, evidence and
citation rows are deleted after `VOICEIQ_DB_RETENTION_DAYS` by `started_at`,
children first (citations, evidence, claims, then runs), so no database
cascade is relied on. Stored `/v2` request input (transcript segments and
speaker ids) follows `VOICEIQ_JOB_RETENTION_HOURS`, or is not stored;
job-folder artifacts keep `VOICEIQ_JOB_RETENTION_HOURS`. A run still
`pending` once its lease (U1) expires is closed as `failed` by the atomic
pending-to-failed update D10-T uses, well inside
`VOICEIQ_JOB_RETENTION_HOURS`. D2 consequence 2 (§1) is reworded to match.

---

## 5. Verdict mapping policy

**Status: approved 2026-10-19 together with §3** (W7, owner decision
2026-10-19), including the §5 part of D7 rider (1): policy point 1 below.

The v2 verdicts must reach the existing `FactCheckReviewAgent` without
inventing new Agent Brain statuses (R1). Until commit `1cd5350`
(2026-09-19) the v1 adapter mapped with a default:

```python
status=_VERDICT_TO_STATUS.get(result.verdict, "UNVERIFIED")
```

That default is gone: the v1 map is now typed `dict[Verdict, FactCheckStatus]`,
indexed directly, and pinned by `test_verdict_map_is_total`, so an unmapped
verdict raises instead of degrading. The two failures below are what a
default does the moment v2 verdicts arrive — the v2 map must keep the
no-default property:

**Failure 1 — silent downgrade.** `CONTRADICTED` is not a key, so it falls
through to `UNVERIFIED`. The review agent then produces a `HIGH` priority
recommendation instead of `CRITICAL`, at base confidence 0.8 instead of 0.9.
A correct fact-check finding is quietly deprioritized, with no error raised
anywhere.

**Failure 2 — noise flood.** If non-checkable statements were ever routed
through the same map, every opinion, prediction and vague remark in a call
would land on `UNVERIFIED` and generate a manual-review recommendation. The
review queue fills with statements that were never claims.

**Policy.**

1. Non-checkable statements are excluded **before** mapping. They carry no
   verdict (§2.2) and must never produce a recommendation. Out-of-scope
   claims are excluded the same way (D7 rider (1)): a claim in a domain D7
   does not enable, or about a private individual (A6), is stopped by a
   Phase 3 domain-scope check kept separate from checkability, and gets no
   verdict and no recommendation. Both kinds appear only in the separate
   `nonCheckable[]` list (U5b); the adapter reads only `claims[]`.
2. The map is exhaustive with **no default**:

   | v2 verdict | `FactCheckStatus` | Review agent effect |
   |---|---|---|
   | `SUPPORTED` | `TRUE` | no recommendation |
   | `CONTRADICTED` | `FALSE` | `CRITICAL`, base confidence 0.9 |
   | `PARTIALLY_SUPPORTED` | `PARTIALLY_TRUE` | no recommendation |
   | `INSUFFICIENT_EVIDENCE` | `UNVERIFIED` | `HIGH`, base confidence 0.8 |

3. A test asserts every member of the v2 verdict literal has an entry, so the
   suite fails the day a fifth verdict is added without one. Coverage of the
   literal — not of a hand-written list.

---

## 6. Privacy boundary and source policy

✅ **Approved 2026-10-19 (A6)** by the project owner (sole maintainer): §6 is
the binding privacy boundary and source policy for Sprint 7, with W6a–W6f
merged below. The §6.3 high-stakes rule is D9's, signed the same day (owner
decision 2026-10-19).

**The host (W6a).** In this section *the host* means the VoiceIQ service plus
an operator-run model server reached without crossing the public internet;
under D5 that server is a separate process on the VoiceIQ host itself,
never reachable from outside it (U3). Transcript content, including gold-set
rows used in the Phase 1 bake-off, may go to that model server and nowhere
else. No third-party-hosted inference service may be configured: the §7
model-server address must point at an operator endpoint.

**Gold-set drafting (guide Q15; owner sign-off 2026-10-28).** Synthetic rows drafted and revised with the drafting assistant are outside W6a (D3, U10); bake-off runs and any scoring or matching step use only the host's model server, never a hosted model.

### 6.1 What may leave the host

Only these, and only when the feature is explicitly requested — through the
opt-in gate (§6.2), or by calling `POST /v2/fact-check` directly, which counts
as consent just as a direct `POST /v1/fact-check` call does (A6, U5c;
DECISIONS #11):

- the normalized claim string,
- extracted entity tokens (asset, currency, city, country, organisation,
  person named *in the claim itself*; never a private individual, see below),
- temporal scope (a date or range),
- the derived search query built from the above (a model-suggested query
  only under §6.6 rule 4),
- a request for a search-result page and its `robots.txt` on an
  allow-listed domain, carrying no claim text (§6.5).

**Private individuals (A6).** A claim whose subject is a private individual is
out of scope: nothing is sent for it, it gets no verdict, and it appears only
in `nonCheckable[]` with reason kind `outOfScope` (U5b). The D3 labelling
guide uses the same value; enforcing it is a Phase 3 obligation.

### 6.2 What must never leave the host

Raw transcript text beyond the claim span · full transcript segments ·
speaker identifiers or diarization labels · session, job or conversation ids ·
audio, in any form · VoiceIQ API keys · any user-supplied header.

This extends, and does not weaken, the existing rule in
[`DATA-FLOW.md`](DATA-FLOW.md) §1: claim subjects are still content-derived,
so a search backend can infer what a conversation touched on. **The feature
stays opt-in for exactly this reason** — `VOICEIQ_FACTCHECK_AUTO_ENRICH`
remains `false` by default and the new agent inherits the same gate.

**Search (D4 Option B, U4a Option A; owner decision 2026-10-19).**
"Open-source" covers the LLM and the search software we run. Web search uses
a self-hosted SearXNG instance: a pinned, internal-only sidecar next to the
deployment that runs the agent, operated by the project owner (sole
maintainer), never reachable from outside the internal network, and reached
only at its fixed configured address through the internal-service client
(§6.5). No public or third-party SearXNG instance may be used. The upstream
engines it queries are **third-party destinations**: only engines whose terms
the project owner has cleared (U4b), each named in `DATA-FLOW.md`. A §6.1
search query therefore does leave the host, to those engines.

**Same-change rule (W6b).** [`DATA-FLOW.md`](DATA-FLOW.md) records each new
destination in the change that adds it, not in Phase 7, marked as behind the
agent's feature flag, off by default; Phase 7 re-checks it against the code.
Nothing is added before that code exists, because `DATA-FLOW.md` documents
what the code does today. Entries 1–3 are the three kinds of destination that
receive call content; entry 4 receives none:

| Entry | Destination | What it receives |
|---|---|---|
| 1 | Model server, inside the host (W6a) | transcript segments with speaker labels for extraction; gold-set rows in the bake-off; one claim plus its evidence bundle for reasoning; N4b-2 sentences if L4 is adopted |
| 2 | Search backend: the SearXNG instance and every enabled upstream engine, by name | only the derived query built from the §6.1 fields |
| 3 | Page hosts: the U4b allow-list | a GET of the result URL and of `robots.txt`, with the identifying User-Agent; no claim text, no cookies; controls in §6.5 |
| 4 | Model-weight source | a download at provisioning time only, no call content; the pinned file and its SHA-256 (U3) |

Each entry also records its trigger, default and controls (feature flag,
inherited opt-in gate, R3), that URLs and queries are never logged, and the
local retention of the new tables (U8c) and of the evidence cache.

### 6.3 Source authority tiers

| Tier | What | Use |
|---|---|---|
| 1 | Primary/official: government, standards bodies, regulators, the named entity itself, peer-reviewed literature | any verdict |
| 2 | Established reference works with visible citations, including Wikipedia for claims that are not high-stakes (W6e) | any verdict |
| 3 | Reputable press with a named publisher and publication date | any verdict, but prefer corroboration |
| 4 | Everything else | corroboration only — **never the sole basis for `CONTRADICTED`** |
| ⛔ | User-generated content, content farms, open-edit pages without visible citations, pages with no identifiable publisher; for a time-sensitive claim, undated pages | not evidence; discard |

**High-stakes rule (D9 Option A; owner decision 2026-10-19).**

1. *High-stakes* means health or medical; legal (law, regulation, legal
   rights); and personal financial (tax, pensions, benefits, credit, loans
   and mortgages, insurance, and how suitable an investment is or what it
   promises to return). Market-data claims (currency, commodity, crypto and
   stock quotes) are not high-stakes and follow the ordinary tiers and the
   recency rule. A high-stakes claim needs Tier 1 evidence; without it the
   verdict is forced to `INSUFFICIENT_EVIDENCE` whatever lower tiers say.
2. A source's tier comes from a reviewed **domain-to-tier table** owned by
   product and legal — never from the model, never from page content. An
   unlisted domain is not fetched; a listed domain without a rating is
   Tier 4. Its terms column holds each provider's and search engine's terms,
   cleared by the project owner (W6e, U4b), and it is the allow-list
   deployed to the §6.5 egress proxy.
3. Domain classification fails closed: a claim that might be high-stakes is
   treated as high-stakes.
4. A claim in a domain D7 does not enable is stopped before retrieval and
   before the verdict stage: nothing leaves the host, it gets no verdict and
   no review recommendation, and it appears in `nonCheckable[]` with reason
   kind `outOfScope` (U5b).

D7 (signed 2026-10-19) keeps healthcare, legal and personal-financial-advice
claims out of the first release, so clause 4 governs them there and
clauses 1–3 are the backstop for a misclassified claim; they apply in full to
any later release that enables one of these domains.

**Recency rule (W6d).** Time-sensitive claim types (crypto, stock, commodity,
forex, weather; commodity added under D7) need evidence no older than a
**maximum evidence age** per claim type, separate from the cache TTL (plan §6,
Phase 5), measured from the provider's update time where it publishes one,
not the fetch time. Older evidence degrades the verdict to
`INSUFFICIENT_EVIDENCE`; it is never used to contradict. A bundle served from
the evidence cache passes the same check as a fresh one. The provider's update
time travels in `Evidence.raw` or the agent's own evidence model, never as a
new v1 `Evidence` field (§2.1).

| Claim type | Maximum evidence age |
|---|---|
| forex | the provider's update cadence plus a margin (proposed: 26 h for the daily-updated §6.4 provider) |
| crypto, stock, commodity, weather | **open:** not yet set; product sets each value |

### 6.4 Structured-data providers in use

The five v1 source clients that Phase 4 routes structured claims through
(W6e). Each row matches [`DATA-FLOW.md`](DATA-FLOW.md) §1; a change to one
changes both.

| Claim type | Provider | Sent | Terms that bind us |
|---|---|---|---|
| `CURRENCY_RATE` | ExchangeRate-API open access (`open.er-api.com`), decided 2026-10-17 | base currency code only | attribution required ("Rates By Exchange Rate API", linked); fetch each base at most hourly; rates update daily |
| `CRYPTO_PRICE` | CoinGecko (`api.coingecko.com`) | a mapped coin id (e.g. `bitcoin`) | **open:** not yet recorded |
| `STOCK_PRICE` | Alpha Vantage (`www.alphavantage.co`) | ticker symbol + the deployment's own provider key | **open:** not yet recorded |
| `WEATHER` | OpenWeather (`api.openweathermap.org`) | city name + units + the deployment's own provider key | **open:** not yet recorded |
| `STATIC_FACT` (country capitals) | Wikipedia REST (`en.wikipedia.org`) | country name, percent-encoded in the URL path | identifiable User-Agent required (`base_client.py`); the rest **open:** not yet recorded |

No repository document records the four open terms; the project owner reads
and clears each in the domain-to-tier table's terms column (§6.3, D9
clause 2). `COMMODITY_PRICE` has no v1 client.

Each provider's tier comes from that table: a rate aggregator is not Tier 1,
and Wikipedia is Tier 2 for claims that are not high-stakes. The currency
provider's evidence age is measured from its update time (§6.3 recency rule),
which the v1 client does not yet record.

### 6.5 Page-fetch controls *(Phase 4 exit criterion — W6c)*

Fetching pages named by search results is the agent's highest-risk new
boundary: every v1 client calls a fixed host, the page fetcher does not.

**Fetch scope (U4b Option A; owner decision 2026-10-19).** The agent fetches
only domains in the domain-to-tier table whose terms the project owner has
cleared, always honours `robots.txt` with an identifiable User-Agent,
rate-limits each domain, and stores, caches and shows short excerpts plus a
content hash, never whole pages: targeted lookup, not a crawler. The evidence
cache follows the same excerpt and retention rules. The excerpt length limit
is **open:** not yet set. If no general web engine's terms can be cleared,
the first release fetches no pages (structured providers and search snippets
only), never "any URL `robots.txt` allows".

**Controls.**

1. Two clients: an internal-service client that calls only the configured
   model-server and SearXNG addresses and never accepts a search-result URL,
   and a page fetcher, the only component that accepts result URLs, always
   through the URL guard.
2. `http`/`https` on the standard ports only; no credentials in the URL; no
   bare-IP hosts.
3. Every resolved address must be publicly routable (internal, loopback,
   link-local, metadata and reserved ranges are refused, IPv6 and mapped
   forms included); the connection goes to the validated address, with TLS
   still checked against the original hostname.
4. Redirects are never followed automatically; each hop, up to a depth of 3
   (proposed), is re-validated, and an https-to-http downgrade is refused.
5. Bounded replies: a streamed, counted body cap (proposed: 2 MiB),
   uncompressed replies only, HTML and plain text only, a per-request timeout
   inside the run deadline, and the per-claim page cap (§7).
6. No cookies, no auth headers, no incoming header passed on (§6.2).
7. URLs, queries and page bodies are never logged.
8. In staging and production, page fetches go through an allow-list egress
   proxy deployed from the domain-to-tier table; the in-process checks are a
   second layer. Deploying the list does not choose its entries.

**Exit tests:** the plan's SSRF cases; the internal-service client never
accepts a search-result URL; zero page fetches with the flag off (R3).

### 6.6 Prompt-injection containment *(Phase 3–5 exit criteria — W6f)*

A caller's words (Phase 3 extraction) and fetched pages (Phase 5 reasoning)
reach the model. The control is containment, not detection:

1. The model server exposes no tools or function calling and has no outbound
   network access while serving; its weights are fetched only at
   provisioning time (§6.2, entry 4).
2. Output is schema-constrained and validated, and citation ids are checked
   against the supplied list (§2.2 invariants 2 and 4).
3. Page text and transcript text are both delimited and labelled as
   untrusted data.
4. A model-suggested search query is used only if it contains nothing from
   the transcript beyond the claim and its §6.1 fields; otherwise it is
   dropped and the query is built from those fields alone.
5. Model output is never fetched as a URL; the page fetcher takes only
   search-result URLs (§6.5).

**Exit tests:** the plan's prompt-injection and fabricated-URL cases; a
suggested query containing transcript text outside the claim is dropped.

---

## 7. Configuration policy

**Status: approved 2026-10-19 together with §3** (W7, owner decision
2026-10-19); key names fixed under A3 amendment (i). **Every number below is
provisional** until D5 and the Phase 1 measurement set them.

All keys carry the existing `VOICEIQ_` prefix (`env_prefix` in
`InsightSettings`). Every key defaults to off, empty, or a safe bound, so an
un-configured deployment behaves exactly as it does today (R3).

**No key reuses an existing `VOICEIQ_` name** (A3 amendment (i)). The v1
engine already owns `FACTCHECK_MAX_CLAIMS` (25) and
`FACTCHECK_RUN_DEADLINE_SEC` (20 s; DECISIONS #38), so the agent's claim cap
and run deadline are `FACTCHECK_AGENT_MAX_CLAIMS` and
`FACTCHECK_AGENT_RUN_DEADLINE_SEC`. Agent keys added later are
`FACTCHECK_AGENT_*`; new rate-limit groups are `RATE_LIMIT_*`. Phase 2 checks
every new name against `app/insights/config/settings.py`.

| Key | Type | Default | Note |
|---|---|---|---|
| `FACTCHECK_AGENT_ENABLED` | bool | `false` | master flag; off ⇒ zero egress, no `fact_check_report` key (U6), `/v2` routes not mounted (U5c) |
| `FACTCHECK_LLM_BASE_URL` | str | `""` | OpenAI-compatible endpoint of the operator-run model server on the VoiceIQ host (D5, W6a); never a third-party hosted inference API |
| `FACTCHECK_LLM_MODEL` | str | `""` | pinned model id (D6) |
| `FACTCHECK_LLM_REVISION` | str | `""` | pinned revision, recorded in every result (U3: source revision and file SHA-256) |
| `FACTCHECK_LLM_TIMEOUT_SEC` | float | `30.0` | per model call |
| `FACTCHECK_AGENT_RUN_DEADLINE_SEC` | float | `125.0` | whole run; renamed from the draft's `FACTCHECK_RUN_DEADLINE_SEC` (A3 amendment (i)). Set at D5 on 2026-10-28, provisional. D5's rule: the 5-minute p95 budget minus the queue allowance and the D10 re-run time (25 s, below); re-checked once Phase 1 has measured latency |
| `FACTCHECK_AGENT_MAX_CLAIMS` | int | `20` | per session; renamed from the draft's `FACTCHECK_MAX_CLAIMS` (A3 amendment (i)) |
| `FACTCHECK_MAX_SOURCES_PER_CLAIM` | int | `5` | |
| `FACTCHECK_MAX_PAGES_PER_CLAIM` | int | `3` | bounds page fetching |
| `FACTCHECK_SEARCH_BASE_URL` | str | `""` | internal-only SearXNG sidecar (D4, U4a), never a public or third-party instance; empty ⇒ no web fallback |
| `FACTCHECK_CACHE_ENABLED` | bool | `true` | local only, no egress; safe on |
| `FACTCHECK_EXECUTION_MODE` | str | `"job"` | `sync` \| `job`; **D2 signed job-based**. `sync` stays available for GPU deployments but is never the default |
| `RATE_LIMIT_FACTCHECK_V2_SUBMIT` | str | `"10/minute"` | `POST /v2/fact-check` (U5c); a separate group from v1's `RATE_LIMIT_FACTCHECK` |
| `RATE_LIMIT_FACTCHECK_V2_READ` | str | `"120/minute"` | `GET /v2/fact-check/{runId}` (U5c), so polling never uses submit budget |

An empty `FACTCHECK_LLM_BASE_URL` or `FACTCHECK_SEARCH_BASE_URL` disables
that path outright — it must not fall back to a public default. Same failure
posture the existing source clients already use: a client without its key is
skipped, it does not guess.

**Keys still to be added** (U1, U5c; owner decisions 2026-10-19). Phase 2
names them `FACTCHECK_AGENT_*` under the rule above, safe by default:

- U1 job runner: job concurrency (default 1), poll interval and lease
  length. The lease outlasts the run deadline plus the longest model call
  with its retry plus the D10 re-run time, or is renewed while the worker
  lives.
- U5c run admission: maximum running and maximum waiting runs, then 503 with
  `Retry-After`. D5's 5-minute p95 includes queueing, so the waiting depth is
  derived from D5: with one job at a time, at most
  (300 s / (run deadline + D10 re-run time)) - 1 runs wait.

**D10 re-run allowance** (2026-10-28, Infrastructure role, recorded beside
D5): 25 s. With the 125 s run deadline, (300 s / 150 s) - 1 = 1 run waits.
Both are provisional and re-checked once Phase 1 has measured latency.

**Open:** every default here except job concurrency. The waiting depth
follows from the run deadline and the re-run allowance above; the lease
length also needs the longest model call with its retry, which Phase 1
measures.

No model-server credential key is reserved: D5 puts the model server on the
VoiceIQ host, never reachable from outside it (U3). W7 reserves one under the
same rule only for a model server on a separate machine.

---

## 8. Exit checklist

*In force: the per-phase reading (W1) was confirmed on 2026-10-19 — see §1.*

Phase 1 may start when D1–D5, D7, the registered rows due before Phase 1 and
the §2, §3 and §6 approvals are ticked. D6 gates the end of Phase 1; U1 gates
Phase 2; U4a and U4b gate Phase 4; D3b's verdict labelling follows Phase 4
and D4; D9 gates the start of Phase 5; D8, D10, D10-T, U7, U8a and U8c gate
the start of Phase 6; U10 gates any use of real calls. The §6 approval (A6)
leaves out the §6.3 high-stakes rule, which D9 settles; both were signed on
2026-10-19.

- [x] **D1 signed** — Option A, new tables only *(2026-09-04)*
- [x] **D2 signed** — job-based execution *(2026-09-04)*
- [x] **D3 signed** — Option A, owned by the project owner (sole maintainer): Layers A+B, English, synthetic-first, about 3–4 person-days; labelling may start before the Phase 1 gate *(2026-10-19)*
- [x] **D4 signed** — Option B: open-source covers the LLM and the search software (self-hosted SearXNG) *(2026-10-19)*
- [x] **D5 signed** — model server on the VoiceIQ host's 8 GB GPU, shared with the audio pipeline; p95 ≤ 5 min, queueing included; provisional run deadline 125 s with a 25 s D10 re-run allowance, set 2026-10-28 *(2026-10-19)*
- [x] **D6 signed** — Option 2: Qwen3.5-4B may win under a criteria note signed before the first bake-off run; Qwen3-4B stays the default; criteria note signed 2026-10-28 (S1) and replaced on 2026-10-30 by S2 ([D6-CRITERIA-NOTE-S2.md](D6-CRITERIA-NOTE-S2.md), launch line only) *(2026-10-19)*
- [x] **D7 signed** — Option B: general knowledge, market data and weather *(2026-10-19)*
- [x] **D8 signed** — Option B: v1 unchanged through this project; removal later only under the exit criteria in §1 *(2026-10-19)*
- [x] **D9 signed** — Option A: Tier 1 only for high-stakes claims *(2026-10-19)*
- [x] **D10 signed** — Option A, the job re-runs every downstream consumer *(2026-09-20)*
- [x] **D3b, D10-T and U1–U10 registered and signed** — each with its own phase date in §1 *(2026-10-19)*
- [x] **W1, W2 and W3 confirmed** — per-phase gate reading (W1); `ClaimSpan` added to the §2.1 v1 freeze list, which now covers every type and field constraint reachable from the v1 request, response and `GET` row (W2 Option 2, A2 amendment (a)); errata batch (W3 Option A) *(2026-10-19)*
- [x] **G1 recorded** — Phase 0 opened 2026-09-04; decision clock started 2026-10-19; signers by role in §1 *(2026-10-19)*
- [x] **§2 API contract approved** — A2, with amendments W2, U5a, U5b, U6 and U8b, the `GET /v1` key-set and casing rules, `model.name` as a free string (D6) and no verdict for out-of-scope claims (D7) *(2026-10-19)*
- [x] **§3 compatibility policy approved** — A3, with amendments (i)–(iii): no key reuses an existing `VOICEIQ_` name; the agent's claim cap and run deadline become `FACTCHECK_AGENT_MAX_CLAIMS` and `FACTCHECK_AGENT_RUN_DEADLINE_SEC`, keys added later are `FACTCHECK_AGENT_*` and new rate-limit groups `RATE_LIMIT_*`; D10 callback, stored-artifact and `/v2` flag-off rows added. §5 and §7 approved with it under W7; §7's numbers stay provisional until D5 and the Phase 1 measurement. The Java action layer team's acknowledgement is **open** (below) *(2026-10-19)*
- [x] **§6 privacy boundary + source policy approved** — A6, with W6a–W6f merged; claims about a private individual are out of scope (`outOfScope`); a direct `POST /v2/fact-check` counts as consent *(2026-10-19)*

**Open items** (owner decision 2026-10-19 left these unset; none is filled
in until the owner sets it):

- **open:** U7 — whether `fact_check_report` shows `disabled` or `skipped`
  when the agent is enabled but its tables are missing.
- **open:** U9 — whether `app.main` shares its `data/` volume with
  `app.insight_main`.
- **open:** U9 — the `app.main` deployment recipe, to be written before
  Phase 6 testing.
- **open:** A3 — the Java action layer team's acknowledgement of §3. It is
  a separate party, so the project owner's signature does not cover it.
- **open:** §6.3 — the maximum evidence age for crypto, stock, commodity and
  weather claims (W6d); product sets each value.
- **open:** §6.4 — the provider terms for CoinGecko, Alpha Vantage and
  OpenWeather, and Wikipedia's terms beyond its User-Agent rule; the project
  owner clears them in the terms column of the domain-to-tier table.
- **open:** §6.5 — the maximum excerpt length for stored, cached and shown
  excerpts (U4b).
- **open:** §7 — the runner and admission defaults other than job
  concurrency (1); the lease length and the waiting depth follow D5's open
  run deadline.

**Nothing above requires code.** The first line of implementation belongs to
Phase 2; Phase 1 is measurement. A phase started before its box is ticked is
building on an unfrozen contract, which is the exact failure this phase
exists to prevent.
