# D3 — gold set: scoping brief

Status: **decision brief, awaiting sign-off** · Created: 2026-09-20
For: `DOCS/FACTCHECK-AGENT-PHASE-0.md` §1 D3 · No code proposed.

D3 is currently one register row — *"Who owns and resources the gold set?"* —
with the substance in the plan PDF v1.1 Phase 1: 200+ claims, stratified,
two annotators, 5–8 person-days, English plus code-switched Urdu/English.

This brief checks that specification against the codebase before anyone is
asked to sign it. Three of its assumptions do not survive contact.

---

## Summary of findings

| # | Finding | Effect on D3 |
|---|---|---|
| 1 | **Only two acceptance gates actually need the gold set.** The rest of §9 is self-referential and measurable today. | D3 is smaller than it looks |
| 2 | **The corpus does not exist.** One real call in the repo, 4.6 minutes, containing **zero** detectable claims. | Source material blocks labelling, not labeller time |
| 3 | **Verdict labels have a chicken-and-egg with Phase 4** and the plan is ambiguous about which metric it wants | The expensive layer cannot be finished today at any headcount |
| 4 | The repo is **public** and `data/` is gitignored | Constrains where the set can live |

---

## 1. What gets labelled

Three separable layers. The plan treats them as one deliverable; they have
different costs, different skill requirements, and different blockers.

| Layer | Label | Consumed by | Blocked? |
|---|---|---|---|
| **A** | Claim spans — which utterances assert a checkable claim | Phase 1 *"extraction precision and recall"*; §9 *"Claim extraction ≥0.85 recall"* | no |
| **B** | Checkability — checkable / opinion / prediction / request / vague | Phase 1 *"checkability F1"*; Phase 3 classifies these | no |
| **C** | Verdict — SUPPORTED / CONTRADICTED / PARTIALLY_SUPPORTED / INSUFFICIENT_EVIDENCE | Phase 1 *"verdict macro-F1"*; §9 *"Verdict quality ≥0.80 macro-F1"* | **yes — see §3** |

### What does *not* need it

Worth stating, because it shrinks the decision. From §9, these are
**binding now** and need no labels at all — each is checkable against the
system's own inputs:

| Target | Why no gold set |
|---|---|
| Response validity (100% schema-valid) | Pydantic validates; measurable on any input |
| Citation integrity (zero invented IDs) | The cited ID is checked against the bundle *supplied to the model*. Self-referential by construction. |
| Confidence bounds (0.0–1.0) | Arithmetic |
| Failure safety | Fault-injection tests |
| Egress off when opted out | Call-count assertion |

Phase 4's exit deliverables are *"frozen network fixtures and a passing
security-test suite"* — **fixtures, not ground truth.** Phase 4 does not
consume the gold set at all. Phase 5's *"zero invented citation URLs in the
evaluation suite"* is the self-referential check above.

**So the gold set exists to do exactly two things:** choose between Qwen3-4B
/ 8B / 3.5-4B in the Phase 1 bake-off, and convert two provisional accuracy
numbers into binding gates. It is not on the path to shipping a *correct*
agent — it is on the path to knowing *how good* one is.

---

## 2. How many — and the corpus problem

### Measured, not estimated

Against the only real pipeline output in the repo (a ~4.6-minute two-party
business call, `data/DIALOGUE.mp3`, run through the full pipeline):

| | |
|---|---|
| words | 642 (~4.6 min at 140 wpm) |
| sentences | 29 |
| assertive, non-question utterances | 15 |
| **claims found by today's detector** | **0** |

Zero is real, not a harness error: the detector fires 5 of its 6 types on
probe text, and the transcript contains none of its trigger domains — no
currency, ticker, crypto, commodity, weather or "capital of" markers.

**Consequence: the gold set cannot be harvested.** Running real calls through
the current pipeline and labelling the output yields an empty set. Claims must
be *constructed*, or sourced from calls that happen to contain them.

### The corpus, not the labeller, is the bottleneck

| | |
|---|---|
| Real calls in the repo | **1** |
| Audio samples | `DIALOGUE.mp3`, `sample.wav` |
| `data/` in git | no — gitignored |

At the observed density (≈15 assertive utterances per 4.6 min, of which some
fraction are genuine checkable claims), 200 claims implies **tens of calls**
that do not exist yet. Naming an owner today does not start labelling; it
starts *corpus acquisition*, which is a different task with different
approvals (whose calls? what consent? what retention?).

### What breaks at what size

| Size | What it supports | What it cannot do |
|---|---|---|
| **50 claims** | Smoke-level comparison; catches a model that is grossly worse | Any per-class claim. With 4 verdict classes, a class can hold ~12 items — one disagreement moves F1 by ~8 points |
| **100** | Aggregate recall with a usable interval; ranks three models if the gaps are large | Still thin per-class, especially INSUFFICIENT_EVIDENCE |
| **200** (plan) | Per-class macro-F1 with ~50/class; supports the 0.80 gate | Not enough for per-*domain* breakdown |
| **400** | Domain-level breakdown | Doubles a human cost that is already the critical path |

200 is the right number **for the metric the plan asks for**. The jump from
100 → 200 buys per-class resolution on macro-F1; 200 → 400 buys only domain
slicing, which nothing currently requires.

---

## 3. The Layer C problem — read before signing

A verdict label answers *"is this claim true?"* — but the metric it feeds
answers one of two very different questions, and the plan does not say which.

| | **C1 — absolute** | **C2 — evidence-relative** |
|---|---|---|
| Labeller does | Researches the claim independently | Reads a **fixed evidence bundle**, judges what it supports |
| Measures | The system end-to-end, retrieval included | The reasoner in isolation |
| A retrieval miss is | a model failure it cannot fix | correctly `INSUFFICIENT_EVIDENCE` |
| Available today | yes | **no** — bundles come from Phase 4 |

This matters most for the class the plan already flags as hardest. Under C1,
a claim that is *true in the world* but *unsupported by what retrieval found*
must be labelled SUPPORTED — and the model answering INSUFFICIENT_EVIDENCE,
which is the **correct and safe** behaviour, scores as an error. That
systematically punishes abstention, in exactly the class where the plan says
small models are weakest.

C2 measures the thing Phase 5 is actually building. It cannot start until
Phase 4 produces bundles.

**Recommendation: the macro-F1 gate should be C2**, and should therefore be
labelled *after* Phase 4, not before. Layers A and B have no such dependency.

---

## 4. Schema

One row per **claim candidate**. Layer A/B columns are fillable today;
Layer C is greyed until §3 is settled.

| Column | Filled by | Values | Notes |
|---|---|---|---|
| `claim_id` | pre-filled | `gs_0001` | stable, never reused |
| `source_id` | pre-filled | `call_003` | pseudonymous; never a customer name |
| `segment_id` | pre-filled | `seg_012` | ties back to the transcript |
| `speaker_label` | pre-filled | `SPEAKER_00` | never a real name |
| `utterance` | pre-filled | verbatim text | **the only content field** |
| `is_claim` | **labeller (A)** | `yes` / `no` | does this assert a checkable proposition? |
| `claim_text` | **labeller (A)** | free text | the proposition, normalised; blank if `is_claim=no` |
| `checkability` | **labeller (B)** | `CHECKABLE` / `OPINION` / `PREDICTION` / `REQUEST` / `VAGUE` | one value; see the guide |
| `domain` | **labeller (B)** | `GENERAL` / `FINANCE` / `NEWS` / `OTHER` | **never** `HEALTH` / `LEGAL` — see §7 |
| `is_compound` | **labeller (B)** | `yes` / `no` | two propositions in one sentence |
| `time_sensitive` | **labeller (B)** | `yes` / `no` | would the answer change within a month? |
| `deliberately_false` | pre-filled | `yes` / `no` | set by whoever constructs the item |
| `verdict` | **labeller (C)** | `SUPPORTED` / `CONTRADICTED` / `PARTIALLY_SUPPORTED` / `INSUFFICIENT_EVIDENCE` | **defer — see §3** |
| `evidence_bundle_id` | pre-filled (C) | `eb_0001` | required for C2; blank until Phase 4 |
| `verdict_note` | **labeller (C)** | free text | one line: what decided it |
| `annotator` | labeller | initials | |
| `confidence_in_label` | labeller | `high` / `low` | **the escape hatch — see §5** |

### Example row (Layers A+B, fillable today)

```
claim_id | source_id | segment_id | speaker_label | utterance                                          | is_claim | claim_text                          | checkability | domain  | is_compound | time_sensitive | deliberately_false | annotator | confidence_in_label
gs_0001  | call_003  | seg_012    | SPEAKER_01    | "Our uptime last quarter was 99.9 percent."        | yes      | Uptime in Q3 2026 was 99.9%         | CHECKABLE    | GENERAL | no          | no             | no                 | AK        | high
gs_0002  | call_003  | seg_014    | SPEAKER_00    | "I think this is going to get much worse."         | no       |                                     | PREDICTION   | GENERAL | no          | no             | no                 | AK        | high
gs_0003  | call_003  | seg_019    | SPEAKER_01    | "We are ISO certified and we ship in two days."    | yes      | Company holds ISO certification     | CHECKABLE    | GENERAL | yes         | no             | no                 | AK        | low
```

Row 3 is the shape that makes `is_compound` earn its place: one sentence,
two independently checkable propositions, and the labeller flagged low
confidence because splitting it is a judgement call.

---

## 5. Who can do it

| Layer | Skill needed | Can a non-specialist do it? |
|---|---|---|
| A — is this a claim | Careful reading. Mechanical once the guide is written. | **Yes** |
| B — checkability, compound, time-sensitive | Same, plus the discipline to apply a taxonomy consistently | **Yes** |
| B — domain | Mechanical for GENERAL/FINANCE/NEWS | **Yes** |
| C — verdict | Research skill; under C1 also subject-matter judgement | **No** — and see §3 |

**Urdu/Arabic: keep the v1 gold set English-only.** Two reasons, both
concrete:

1. **It would measure a system that does not exist.** The extraction
   vocabulary is English-only today; the ur/ar tables are wired but empty
   pending **L1**. Scoring the current system on Urdu input measures the
   English fallback, not Urdu support.
2. **It serialises two blockers onto one person.** L1 needs a native
   speaker to author vocabulary; an ur/ar gold set needs a native speaker
   to label. Same scarce resource. Doing both at once makes each wait for
   the other.

Add the multilingual slice **after** L1 lands and N4b Phase 2 ships — at
which point it measures something real. Note this narrows the plan's stated
scope ("English plus code-switched Urdu/English") and is a deliberate change,
not an omission.

---

## 6. Inter-annotator agreement

**One labeller is not enough, but not for every field.**

| Field | Risk | Needs a second pass? |
|---|---|---|
| `is_claim` | Moderate — the boundary between an assertion and an aside is genuinely fuzzy | **Yes**, on an overlap |
| `checkability` | **High** — PREDICTION vs CHECKABLE turns on tense and hedging; VAGUE is inherently subjective | **Yes** |
| `is_compound` | Moderate — where to split is a judgement | Yes, on the overlap |
| `domain`, `time_sensitive` | Low — largely mechanical | No |
| `verdict` | **Highest** — CONTRADICTED vs INSUFFICIENT_EVIDENCE on thin evidence is exactly where reasonable people differ | **Yes, mandatory** |

The failure mode if one person labels alone: their judgement becomes
**unfalsifiable ground truth**. A model that disagrees is marked wrong with
no way to tell whether the model or the label was at fault — and the 0.80
macro-F1 gate then measures agreement with one individual rather than
correctness.

**Minimum viable:** a **20% overlap** double-labelled, with Cohen's κ
reported per field. κ < 0.6 on `checkability` or `verdict` means the *guide*
is underspecified, not that the labellers are careless — fix the guide and
re-label, rather than averaging the disagreement away.

The `confidence_in_label` column is the cheap mitigation: a single labeller
flagging their own uncertainty lets low-confidence rows be excluded from the
gate, or routed to a second opinion, without double-labelling everything.

---

## 7. Where it lives

**Constraint: the repo is public** (`github.com/HAMZAJAWED12/Voice-iq`,
`visibility: public`), and `data/` is already gitignored precisely because
job artifacts hold transcript content.

The `utterance` column is verbatim call content. Committing real customer
utterances to a public repository would publish conversation data — the same
category the retention TTL and DATA-FLOW boundary exist to protect.

| Option | Verdict |
|---|---|
| Commit the full set to the public repo | ❌ **No.** Publishes customer speech. |
| Commit only **synthetic** rows; keep real-call rows out of git | ✅ Workable, and the practical answer given §2 |
| Private storage, referenced by hash from the repo | ✅ Correct for real-call rows; needs a storage decision |

**Recommended shape:**

- **Format:** CSV for labelling (hands straight into Sheets/Excel), converted
  to JSONL for the evaluation harness. CSV is what a labeller can actually
  use; JSONL is what the test suite should read.
- **Synthetic rows:** `app/evaluation/gold/` in git. Small, no real speech,
  reviewable in PRs.
- **Real-call rows:** outside git, alongside the existing gitignored `data/`.
  The repo stores the schema, the labelling guide, and a **manifest of
  `claim_id` + content hash** so a set can be verified without publishing it.
- **Size:** 200 rows of this schema is well under 1 MB. Size is not the
  constraint; content sensitivity is.

---

## 8. The decision

| | **Option A — Layers A+B now, English, synthetic-first** | **Option B — full spec as written** | **Option C — defer entirely to Phase 4** |
|---|---|---|---|
| Scope | 200 claim candidates, spans + checkability. Verdicts deferred. | 200 claims, all three layers, en + ur/ar, 2 annotators | Nothing until retrieval exists |
| Corpus | Synthetic + the one real call | Needs tens of real calls — **does not exist** | n/a |
| Cost | **~2–3 person-days** labelling + ~1 day writing the guide | 5–8 person-days **plus** corpus acquisition **plus** a native ur/ar speaker | 0 now |
| Signable today | **Yes** | No — blocked on corpus and on L1's person | Yes, trivially |
| Unblocks | Phase 1 extraction + checkability metrics; the bake-off can start on those | Everything, eventually | Nothing |
| Leaves open | Verdict macro-F1 until Phase 4 (§3) | — | Phase 1 stays blocked; Sprint 7 critical path slips |

### Recommendation: **Option A**

- It is the only option that can be **handed to a labeller today**, which is
  what D3 was raised to unblock.
- It defers exactly the layer that is **genuinely blocked** (§3), rather than
  forcing a verdict metric that would punish correct abstention.
- It costs roughly half the plan's estimate because it drops the layer that
  needs research and the language that needs L1.
- The deferred part gets its own row rather than disappearing: **D3b —
  verdict labels, C2 (evidence-relative), scheduled with Phase 4.**

Cost figures are estimates from the schema's field count and the observed
utterance density, not measured throughput. The first half-day of labelling
should be timed and the estimate corrected.

---

## 9. What D3 constrains elsewhere

Flagged now because discovering these at sign-off is more expensive.

| Decision | Interaction |
|---|---|
| **D7 — first-release domains** | **Circular, and D7 should be signed first.** Whatever domains appear in the gold set silently become the domains the system is measured on. The schema's `domain` column forces the choice to be explicit rather than accidental. |
| **D9 — high-stakes source rules** | **D3 can avoid depending on D9, and should.** If the set contains health or legal claims, a labeller cannot assign verdicts without D9's authority rules. The schema therefore **excludes** `HEALTH` and `LEGAL` from `domain`. Keeping them out means D3 does not wait on D9 — but it also means the gate says nothing about regulated domains, which must not later be read as evidence they are safe. |
| **D4 — is web search self-hosted?** | Feeds §3. Under C2, evidence bundles come from whatever retrieval D4 selects; labelling against SearXNG output and later switching backends invalidates the labels. **C2 labelling must start after D4 is signed.** |
| **D6 — Qwen3 mandatory, or may 3.5 win?** | One-directional: D6 is *decided by* the bake-off, which needs D3. D3 does not constrain D6. |
| **D5 — hardware / latency** | No interaction. |
| **L1 — ur/ar vocabulary** | Same scarce person. §5 recommends decoupling them rather than serialising. |

---

## 10. What signing Option A commits to

1. A named owner for the labelling (D3's original question).
2. ~1 day writing a labelling guide with worked examples for the
   `checkability` taxonomy — the field §6 identifies as highest-risk.
3. ~2–3 person-days labelling 200 candidates, Layers A+B, English.
4. A 20% double-labelled overlap with per-field Cohen's κ.
5. Synthetic rows in `app/evaluation/gold/`; real-call rows out of git.
6. **D3b** opened for verdict labels, scheduled with Phase 4, after D4.
7. §9's extraction-recall target becomes measurable; the verdict macro-F1
   target stays provisional and is explicitly *not* unblocked by this.
