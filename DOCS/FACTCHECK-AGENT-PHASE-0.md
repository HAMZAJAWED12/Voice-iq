# Fact-Check Agent — Phase 0: Contract and Scope Lock

Owner: engineering · Status: **awaiting sign-off** · Created: 2026-09-04
Source plan: `VoiceIQ_Fact_Check_Agent_Implementation_Plan.pdf` **v1.1**

Phase 0 ships **no application code**. Its deliverables are the four policies
below plus a signed decision register. Phases 1–7 do not start until §1 is
signed — that is the whole point of the phase: freeze the contract so later
phases cannot churn it.

Everything here is **normative**. Where a rule says *must*, a later phase that
breaks it is a defect, not a judgement call.

| Exit deliverable (plan §6, Phase 0) | Where | Status |
|---|---|---|
| Approved API schema + compatibility policy | §2, §3 | drafted — needs sign-off |
| Approved privacy boundary + source policy | §6 | drafted — needs sign-off |
| Persistence option, execution mode, decision table w/ owners + dates | §1, §4 | **blocked on D1–D9** |

---

## 1. Decision register

Nine decisions. Every one blocks Phase 1. v1.0 of the plan listed the
questions but assigned neither owner nor date, so none could be chased.

`Needed by` is relative to the Phase 0 kick-off date.

| ID | Decision | Owner | Needed by | Recommended default | Status |
|----|----------|-------|-----------|---------------------|--------|
| D1 | Persistence: new tables only, or adopt Alembic? | Tech lead | +2 days | **Option A — new tables only** | ⬜ pending |
| D2 | Synchronous or job-based execution? | Tech lead + product | +3 days | **Job-based** until hardware proves otherwise | ⬜ pending |
| D3 | Who owns and resources the gold set? | Eng manager | +2 days | — (must be named) | ⬜ pending |
| D4 | Does "open-source" cover web search too, or only the LLM? | Senior sponsor | +3 days | Self-host search (SearXNG) | ⬜ pending |
| D5 | Production GPU/RAM, and max acceptable fact-check latency? | Infrastructure | +5 days | — (must be measured) | ⬜ pending |
| D6 | Is Qwen3 mandatory, or may Qwen3.5 win the bake-off? | Senior sponsor | before Phase 1 ends | Let the measurement decide | ⬜ pending |
| D7 | Which fact domains ship first? | Product | +5 days | General knowledge + finance; **exclude** healthcare/legal | ⬜ pending |
| D8 | How long must v1 + legacy process-audio fields survive? | Product + consumers | before Phase 6 | Indefinitely within this project | ⬜ pending |
| D9 | Source-authority rules for high-stakes claims? | Product + legal | before Phase 5 | Tier 1 only — see §6.3 | ⬜ pending |

### D1 — Persistence *(recommendation: Option A, new tables only)*

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

**Recommendation: Option A.** The new agent's results are a new shape
anyway — run, claim, evidence and citation records are one-to-many and do not
belong bolted onto the flat `fact_check_results` row. Option B remains
available later as an independent decision; nothing in this plan should
create pressure to rush it.

**Consequence if adopted:** §4 becomes binding and no phase may assume
`ALTER TABLE`.

### D2 — Execution mode *(recommendation: job-based until measured)*

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

**Recommendation: assume job-based** until D5 supplies hardware that proves
synchronous is viable. Designing synchronous-first and retrofitting async is
the expensive direction.

---

## 2. API contract lock

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
MAX_TRANSCRIPT_CHARS = 10_000
```

**Rule.** No field of `FactCheckRequest`, `FactCheckResponse`,
`FactCheckResult`, `DetectedClaim`, `Evidence`, `Confidence` or
`FactCheckStats` may be added, removed, renamed or retyped by this project.
No member may be added to or removed from `ClaimType` or `Verdict`.
`GET /v1/fact-check/{id}` is frozen on the same terms.

The new agent gets its **own** models. It does not extend these.

### 2.2 v2 contract

`POST /v2/fact-check`. New route, new models, no shared mutable types with v1.

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

Deprecating either older key is a **separate later ticket** gated on D8 and
on consumer sign-off. Not in scope here.

---

## 3. Compatibility policy

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
| `POST /v2/fact-check` | new route | additive | remove route |
| `fact_checks` | none | no change | n/a |
| `fact_checks_v2` | none | no change | n/a |
| `fact_check_report` | new key | additive | flag off ⇒ key absent |
| Database | new tables only | additive | drop new tables |
| Agent Brain statuses | new verdicts mapped to **existing** statuses | additive | revert mapping |
| PDF report | new verdict + citations section | additive | flag off ⇒ section omitted |
| Config | new `VOICEIQ_` keys, all defaulting off/safe | additive | unset ⇒ current behaviour |
| Network egress | **new destinations**: model server, search backend | **new boundary** | flag off ⇒ zero calls (R3) |

---

## 4. Persistence policy *(binding if D1 = Option A)*

**Allowed.** Creating new tables. `create_all()` picks them up on next start.

**Forbidden.** Adding, removing, renaming or retyping any column on
`fact_check_results`, `insights`, or any other existing table. Forbidden
because the mechanism to do it does not exist and its absence fails silently.

**Shape.** Four new tables, one-to-many downward:

```
factcheck_agent_runs      (run_id, session_id, model_name, model_revision,
                           started_at, finished_at, status, counts…)
factcheck_agent_claims    (claim_id, run_id → runs, text, speaker_id,
                           segment_ids, is_checkable, verdict, confidence, reason)
factcheck_agent_evidence  (evidence_id, claim_id → claims, url, title,
                           publisher, published_at, retrieved_at, excerpt,
                           content_hash, tier, stance)
factcheck_agent_citations (citation_id, claim_id → claims, evidence_id → evidence,
                           ordinal)
```

**Required regression test** (Phase 6 exit criterion): start the service
against a database that already contains the pre-change schema and rows, and
assert it serves traffic and writes a run. This is the only test that would
have caught the Option-B assumption in plan v1.0.

---

## 5. Verdict mapping policy

The v2 verdicts must reach the existing `FactCheckReviewAgent` without
inventing new Agent Brain statuses (R1). Today the adapter maps with a
default:

```python
status=_VERDICT_TO_STATUS.get(result.verdict, "UNVERIFIED")
```

That default is currently dead — the existing map covers all six v1 verdicts.
It becomes live the moment v2 verdicts arrive, and it fails in two directions:

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
   verdict (§2.2) and must never produce a recommendation.
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

### 6.1 What may leave the host

Only these, and only when the feature is explicitly requested:

- the normalized claim string,
- extracted entity tokens (asset, currency, city, country, organisation,
  person named *in the claim itself*),
- temporal scope (a date or range),
- the derived search query built from the above.

### 6.2 What must never leave the host

Raw transcript text beyond the claim span · full transcript segments ·
speaker identifiers or diarization labels · session, job or conversation ids ·
audio, in any form · VoiceIQ API keys · any user-supplied header.

This extends, and does not weaken, the existing rule in
[`DATA-FLOW.md`](DATA-FLOW.md) §1: claim subjects are still content-derived,
so a search backend can infer what a conversation touched on. **The feature
stays opt-in for exactly this reason** — `VOICEIQ_FACTCHECK_AUTO_ENRICH`
remains `false` by default and the new agent inherits the same gate.

`DATA-FLOW.md` gains the two new destinations (model server, search backend)
in **Phase 7**, when they become real. It is not edited now, because it
documents what the code does today.

### 6.3 Source authority tiers

| Tier | What | Use |
|---|---|---|
| 1 | Primary/official: government, standards bodies, regulators, the named entity itself, peer-reviewed literature | any verdict |
| 2 | Established reference works with visible citations | any verdict |
| 3 | Reputable press with a named publisher and publication date | any verdict, but prefer corroboration |
| 4 | Everything else | corroboration only — **never the sole basis for `CONTRADICTED`** |
| ⛔ | User-generated content, content farms, pages with no identifiable publisher or date | not evidence; discard |

**High-stakes rule (pending D9).** Claims in healthcare, legal, or personal
financial domains require Tier 1 evidence. Without it the verdict is forced
to `INSUFFICIENT_EVIDENCE` regardless of what lower tiers say. D7's
recommended default excludes these domains from the first release entirely,
which is the safer sequencing.

**Recency rule.** Time-sensitive claim types (crypto, stock, forex, weather)
require evidence newer than their cache TTL (plan §6, Phase 5). Stale
evidence degrades the verdict to `INSUFFICIENT_EVIDENCE`; it is never used to
contradict.

---

## 7. Configuration policy

All keys carry the existing `VOICEIQ_` prefix (`env_prefix` in
`InsightSettings`). Every key defaults to off, empty, or a safe bound, so an
un-configured deployment behaves exactly as it does today (R3).

| Key | Type | Default | Note |
|---|---|---|---|
| `FACTCHECK_AGENT_ENABLED` | bool | `false` | master flag; off ⇒ zero egress |
| `FACTCHECK_LLM_BASE_URL` | str | `""` | OpenAI-compatible endpoint |
| `FACTCHECK_LLM_MODEL` | str | `""` | pinned model id |
| `FACTCHECK_LLM_REVISION` | str | `""` | pinned revision, recorded in every result |
| `FACTCHECK_LLM_TIMEOUT_SEC` | float | `30.0` | per model call |
| `FACTCHECK_RUN_DEADLINE_SEC` | float | `120.0` | whole run |
| `FACTCHECK_MAX_CLAIMS` | int | `20` | per session |
| `FACTCHECK_MAX_SOURCES_PER_CLAIM` | int | `5` | |
| `FACTCHECK_MAX_PAGES_PER_CLAIM` | int | `3` | bounds page fetching |
| `FACTCHECK_SEARCH_BASE_URL` | str | `""` | SearXNG instance; empty ⇒ no web fallback |
| `FACTCHECK_CACHE_ENABLED` | bool | `true` | local only, no egress; safe on |
| `FACTCHECK_EXECUTION_MODE` | str | pending **D2** | `sync` \| `job` |

An empty `FACTCHECK_LLM_BASE_URL` or `FACTCHECK_SEARCH_BASE_URL` disables
that path outright — it must not fall back to a public default. Same failure
posture the existing source clients already use: a client without its key is
skipped, it does not guess.

---

## 8. Exit checklist

Phase 1 may start when all nine boxes are ticked.

- [ ] D1 signed — persistence option chosen
- [ ] D2 signed — execution mode chosen
- [ ] D3 signed — gold-set owner named and resourced (5–8 person-days)
- [ ] D4 signed — open-source scope
- [ ] D5 signed — hardware + latency target
- [ ] D6 signed — Qwen3 mandatory or bake-off decides
- [ ] D7 signed — first-release domains
- [ ] D8 signed — v1 support window
- [ ] D9 signed — high-stakes source rules
- [ ] §2 API contract approved
- [ ] §3 compatibility policy approved
- [ ] §6 privacy boundary + source policy approved

**Nothing above requires code.** The first line of implementation belongs to
Phase 2; Phase 1 is measurement. A phase started before its box is ticked is
building on an unfrozen contract, which is the exact failure this phase
exists to prevent.
