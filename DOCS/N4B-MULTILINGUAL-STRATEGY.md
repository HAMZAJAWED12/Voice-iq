# N4b — Multilingual (ur / ar): dependency + CI strategy

Owner: engineering · Status: **decision drafted, awaiting sign-off** · Created: 2026-09-19

`DOCS/TIER-N-PLAN.md` gates this item:

> **N4b — multilingual ur/ar + model-based extraction (own sprint, deferred).**
> spaCy/Stanza pipelines, non-Latin script handling, per-language models.
> Heavy deps + model artifacts — the exact class that broke CI in Tier S.
> Needs its own dep-isolation strategy and CI lane decision **before** any code.

This document is that decision. **No code.**

---

## Headline

The gate assumed N4b means "add spaCy/Stanza". Measurement says otherwise.

**Most of N4b needs no new dependency at all, and no CI lane ever needs to
change.** The extraction layer's failure on Urdu and Arabic is overwhelmingly
a *vocabulary* gap, not a *model* gap. Exactly one capability — extracting a
person's name from native-script text — genuinely needs a model, and that one
can reuse the model server Sprint 7 is already standing up rather than
introducing spaCy or Stanza.

---

## 1. Evidence

Probe run against the live extractors (`app/agent_brain/extraction/`, 306 LOC
total, 198 of which is the N4a date resolver). Recorded behaviour, not
intent:

| lang | case | assignee | date | time | priority | signals |
|---|---|---|---|---|---|---|
| en | baseline | `Ali` | `by Friday` | `2 PM` | MEDIUM | will send, by friday |
| en | urgent | None | None | None | **CRITICAL** | please |
| mixed | **Roman Urdu** | **None** | **None** | None | **MEDIUM** | **[]** |
| mixed | Roman Urdu urgent | None | None | None | **MEDIUM** | [] |
| mixed | code-switched | `Ali` | `by Friday` | None | MEDIUM | by friday |
| ur | Urdu (Nastaliq) | None | None | None | MEDIUM | [] |
| ur | Urdu urgent | None | None | None | MEDIUM | **فوری, ابھی** |
| ar | Arabic | None | None | None | MEDIUM | [] |
| ar | Arabic urgent | None | None | None | MEDIUM | **عاجل, فورا** |
| ar | Arabic-Indic digits (`٢ مساءً`) | None | None | **None** | MEDIUM | [] |

Four things fall out of that table.

**1. Substring matching already works in Arabic script.** `signals.py` lowercases
and does `in` — and `.lower()` is a no-op on Arabic script (verified:
`text.lower() == text` for every ur/ar case). Passing a translated signal list
matched immediately: `['فوری', 'ابھی']`, `['عاجل', 'فورا']`. The signal layer
needs **translated word lists, not code and not models.**

**2. Priority classification is the same story.** `_CRITICAL_TERMS` is an English
tuple. A translated tuple matches ur and ar correctly with zero new
dependencies — verified in the probe.

**3. Roman Urdu is the worst-served case, and it is pure vocabulary.** "Ali kal
tak report bhej dega" yields *nothing* — no assignee, no date, no priority,
no signals. Yet it is Latin script and `Ali` is capitalized. The assignee
regex failed only because its verb list is English-only
(`will|shall|should|must|needs to|has to|is going to`); "bhej dega" is absent.
Add the vocabulary and the existing regex works unchanged.

This matters more than the native-script cases: Roman Urdu is the realistic
register for a Pakistani call centre, and it is `mixed`, not `ur`.

**4. Native-script *names* are the one genuine wall.** `assignee_extractor` keys
on `[A-Z][a-z]+`. Arabic script has no letter case, so no amount of
vocabulary rescues it. This — and only this — needs a model.

Arabic-Indic digits are a footnote, not a wall: `٢` fails because the meridiem
token `مساءً` is not in `am|pm`, and stdlib `unicodedata.digit()` normalizes
the numeral. Vocabulary plus stdlib.

## 2. Two structural findings

**`AgentContext.language` is write-only.** `LanguageCode = Literal["en","ur","ar","mixed"]`
exists, `AgentContext.language` exists, `PipelineAdapter.to_context` accepts
it — and **nothing anywhere reads it.** Grepping `.language` across
`core/`, `service.py` and `extraction/` returns nothing. Every extractor is
unconditionally English.

**Language is already detected, and already thrown away.** Whisper
auto-detects (`process_audio.py:200` passes `language=None`), `ASRService`
records it in `meta["language"]`, and `orchestrator.py:764` already surfaces
it on the response as **`result["asr_meta"]["language"]`**. It simply never
reaches Agent Brain. Phase 1 is therefore a wiring job with a known source
path and a known sink (`AgentContext.language`) — not a detection problem.

**Consequence: N4b needs no language-detection dependency** — no `langdetect`,
no fastText. The signal exists; the wiring does not. Whisper emits ISO-639-1
(`en`/`ur`/`ar`), which maps onto `LanguageCode` directly; `mixed` is never
emitted by Whisper and has to be inferred (see open decision L2).

> Related, and worth a separate ticket: `PipelineAdapter.to_context` is called
> from **tests only** — no production caller. Same class as the Agent Brain
> router that was never mounted. N4b-1's language wiring runs through this
> adapter, so it needs a real caller to be worth anything.

---

## 3. The split

| | N4b-1 — vocabulary + wiring | N4b-2 — native-script NER |
|---|---|---|
| Covers | priority, signals, date/time phrases, Roman-Urdu assignee, digit normalization | person names in Urdu/Arabic script |
| New runtime deps | **none** | one model, out of process |
| New model artifacts | **none** | yes, outside the app |
| CI lane change | **none** | **none** (mocked at the HTTP seam) |
| Blocked by | nothing | Sprint 7 Phase 2 LLM client |
| Share of the table above | 9 of 10 failing cells | 3 cells (ur/ar assignee) |

N4b-1 is most of the value and carries none of the risk that caused the
deferral. It should not wait behind N4b-2.

---

## 4. Decision — dependency isolation for N4b-2

Three options considered.

**Option A — reuse the Sprint 7 model server. ✅ Recommended.**
Sprint 7 Phase 2 stands up an httpx client against an OpenAI-compatible
endpoint, with the model held outside the VoiceIQ process
(`DOCS/FACTCHECK-AGENT-PHASE-0.md` §7). Qwen extracts a person's name from an
Urdu sentence perfectly well. Reusing that boundary means **N4b never adds
spaCy or Stanza at all**, and the repo keeps *one* model-serving pattern
instead of two.

- *Cost:* couples N4b-2 to Sprint 7 Phase 2 landing first. N4b-2 is deferred
  anyway, so this is a sequencing note, not a blocker.
- *Cost:* a network hop for what spaCy would do in-process. Acceptable —
  assignee extraction is per-recommendation, not per-word, and the call is
  already bounded by Sprint 7's deadline/caps machinery.

**Option B — optional extra (`pip install voiceiq[ur]`), lazy import, feature flag.**
The `EmotionService` pattern: guard the import, degrade when absent. Workable,
and it is what we would do if Sprint 7 did not exist. Rejected as the primary
because it puts a second model-loading pattern in the codebase and reintroduces
model artifacts to dev machines — the same divergence that produced the
`KEYWORDS_FAILED` CI failure in Tier S.

**Option C — dedicated CI job with cached model artifacts. ❌ Rejected.**
CLAUDE.md forbids it outright: *"Never fix this class of failure by installing
the model in CI — a safety net must not depend on model artifacts or a network
fetch."* Listed only so nobody proposes it again.

## 5. Decision — CI lane

**No new lane. No change to `.github/workflows/test.yml`.**

- **N4b-1** is pure-python vocabulary tables and stdlib normalization. It runs
  on the existing light lane as-is.
- **N4b-2** is mocked at the HTTP seam, exactly as Sprint 7 stubs its LLM
  client. CI never needs a model, a model artifact, or the network.

This satisfies the standing rule — *a service is heavy if it imports an ML
library **or** loads a model artifact; heavy ⇒ mocked* — without a lane
decision at all, because nothing heavy ever enters the process.

The one new test obligation: assert that with the NER seam disabled, native-
script assignee extraction returns `None` and **makes zero outbound calls** —
the same "off means silent" rule as Sprint 7 (`FACTCHECK-AGENT-PHASE-0.md` §3 R3).

---

## 6. Phased plan for N4b-1

Unblocked by this document. No new dependency in any phase.

**Phase 1 — read the language that already exists. ✅ DONE.**

Shipped: `extraction/language.py` (`resolve_language` + `vocabulary_for`),
`PipelineAdapter.to_context(asr_meta=...)`, language-keyed tables in all four
extractors, and all four agents reading `context.language` and passing it
down. 34 seam tests; every touched module 100%. All 731 pre-existing tests
passed unedited — English behaviour is unchanged, which is the point.

`AgentContext.language` is no longer write-only.

> **L5 — CLOSED.** The seam is now load-bearing. `_run_agent_brain` in the
> orchestrator calls `PipelineAdapter.to_context` with
> `asr_meta=st.asr_out["meta"]`, so Whisper's detected language reaches
> `AgentContext.language` on the real pipeline path. Opt-in via
> `VOICEIQ_AGENT_BRAIN_AUTO_RUN` (off by default); the `recommendations`
> response key is omitted entirely when off. Proven end-to-end by
> `TestAgentBrainStage::test_detected_language_reaches_the_agent_context`.
>
> **Phase 2 vocabulary is therefore reachable** the moment L1 lands.

**Phase 2 — fill the vocabulary tables. 🔴 BLOCKED on L1.**

The tables themselves already exist and are wired (that was Phase 1); every
one carries a single populated `"en"` key. Phase 2 is **data entry, not
engineering**: add a `"ur"` / `"ar"` key and it takes effect immediately,
because `vocabulary_for()` treats absent and empty identically.

Tables awaiting entries:

| Module | Table |
|---|---|
| `priority_classifier` | `_CRITICAL_TERMS`, `_HIGH_TERMS` |
| `assignee_extractor` | `_PATTERNS` |
| `datetime_extractor` | `_PATTERNS` |
| `task_agent` | `_TASK_SIGNALS` |
| `email_draft_agent` | `_EMAIL_SIGNALS` |
| `followup_agent` | `_FOLLOWUP_SIGNALS` |
| `escalation_agent` | `_ANGER`, `_COMPLAINT`, `_RISK` |

**No engineer should invent these terms.** L1 exists precisely because
guessed vocabulary in a language you do not speak produces confident,
untraceable false positives in a customer-facing recommendation.

**Phase 3 — Roman Urdu vocabulary.**
Extend the assignee verb alternation and the signal lists with Roman-Urdu
forms ("bhej dega", "karega", "zaroori", "kal tak"). Highest value-per-line in
the whole item, and it is regex vocabulary only.

**Phase 4 — script and numeral normalization.**
`unicodedata.digit()` for Arabic-Indic numerals, ur/ar meridiem and weekday
tokens for the datetime extractor, extending the N4a resolver grammar.

**Phase 5 — the seam for N4b-2.**
Define the assignee-extraction interface so the native-script implementation
can be dropped in behind it later without touching callers. Default
implementation stays the current regex; the model-backed one is registered
only when configured.

**Acceptance for N4b-1 overall:** every existing English test passes with no
assertion edited, and the probe table above flips from `None` to a correct
value in every cell except the three native-script assignee cells.

---

## 7. Open decisions

| # | Decision | Owner | Needed by |
|---|---|---|---|
| L1 | Who supplies and reviews the ur / ar / Roman-Urdu vocabulary? It is native-speaker work, not engineering work, and it is the critical path for Phases 2-4. | Product | before Phase 2 |
| L2 | How is `mixed` decided? Whisper never emits it. Options: script-ratio heuristic on the transcript, or drop `mixed` from routing and always fall back to `en` vocabulary plus the language-specific table. | Tech lead | before Phase 1 |
| L3 | Does Roman Urdu route as `ur` or as `mixed`? It is Latin script with Urdu vocabulary, so it fits neither cleanly. Affects how the tables are keyed. | Tech lead | before Phase 2 |
| L4 | Confirm Option A: N4b-2 reuses the Sprint 7 model server rather than adding spaCy/Stanza. | Tech lead | before N4b-2 |
| L5 | Is `PipelineAdapter` given a production caller? ✅ **CLOSED** — `_run_agent_brain` in the orchestrator, opt-in via `VOICEIQ_AGENT_BRAIN_AUTO_RUN`. See below. | Tech lead | done |

**Recommended defaults if no answer comes:** L2 — drop `mixed` from routing,
fall back to `en`; L3 — route Roman Urdu as `mixed` once L2 is resolved, else
`en`; L4 — Option A. L5 is closed.

---

## L5 outcome — what the wiring actually found

L5 was framed as "give `to_context` a caller". Investigation found something
larger: **the entire push integration existed and nothing invoked any of it.**

| Component | Before L5 | After |
|---|---|---|
| `POST /internal/v1/agent-brain/…` (pull) | ✅ wired | unchanged |
| `PipelineAdapter.to_context` | ❌ tests only | ✅ `_run_agent_brain` |
| `JavaCallbackClient` | ❌ never instantiated | ✅ fire-and-forget, flag-gated |
| `min_confidence` threshold | ❌ **never read** | ✅ filters the callback |

`min_confidence` had been documented since Sprint 6 as "dropped before
callback" — a filter that could not run because the callback never fired.

**A live performance bug came out of it too.** `AgentRunner` computed
"how many other candidates duplicate this one?" once per candidate, an O(n²)
sweep over `difflib`. On a 16-minute call that was 44,539
`find_longest_match` calls and ~2.3 s, on the *already-mounted* pull route —
a 2-hour call exceeded a 120-second timeout outright. Fixed in L5-0, output
byte-identical: `run()` 5.006 s → 0.148 s, difflib core 44,539 → 65 calls.

### Live vs dormant

| Agent | On the pipeline path |
|---|---|
| Task, Follow-up, Email draft, Escalation | ✅ live |
| **FactCheckReview** | ⏸ **dormant until Sprint 7 Phase 6** — needs a `FactCheckResponse`, which D2 makes unavailable at this point. |

**D10 is signed (Option A):** when the fact-check job completes it re-runs
*every* downstream consumer — Agent Brain first, then the PDF — so
`FactCheckReviewAgent` gets its claims. Dormancy is therefore **time-bounded,
not permanent**, and nothing in the current code changes because of the
signature: D10 is a Phase 6 obligation.

Until Phase 6 ships it, a `CONTRADICTED` verdict produces no manual-review
recommendation from this path.

> **Carried into Phase 6 with it:** under Option A the Agent Brain runs twice
> per session, so the Java callback must fire **exactly once** — only on the
> final pass. Java's trace-id de-duplication does not cover this, because the
> two payloads genuinely differ. Full rule in `FACTCHECK-AGENT-PHASE-0.md`
> §D10.

---

## Appendix — the probe corpus

The §1 table is only reproducible, and §6's acceptance criterion only
testable, if the exact inputs are on record. These are they. When N4b-1
starts, this becomes the seed fixture for the real test module.

| # | lang | text |
|---|---|---|
| 1 | en | `Ali will send the report by Friday at 2 PM.` |
| 2 | en | `This is urgent, please escalate immediately.` |
| 3 | mixed | `Ali kal tak report bhej dega.` |
| 4 | mixed | `Yeh bohat zaroori hai, abhi karo.` |
| 5 | mixed | `Ali will report bhej dega by Friday.` |
| 6 | ur | `علی جمعہ تک رپورٹ بھیجے گا۔` |
| 7 | ur | `یہ بہت فوری ہے، ابھی کریں۔` |
| 8 | ar | `سيرسل علي التقرير يوم الجمعة.` |
| 9 | ar | `هذا عاجل، يرجى التصعيد فورا.` |
| 10 | ar | `الاجتماع الساعة ٢ مساءً.` |

Translated terms that were verified to match with zero new dependencies:

| | critical-priority terms |
|---|---|
| ur | `فوری`, `ابھی`, `ہنگامی` |
| ar | `عاجل`, `فورا`, `طارئ` |

These are engineering's probe vocabulary, chosen to prove the mechanism —
**not** a reviewed term list. Decision L1 covers sourcing the real one from a
native speaker; do not ship these as-is.

Expected state after N4b-1: rows 3, 4, 5, 7, 9 and 10 return correct values
for every column, and rows 6 and 8 return correct values for everything
**except** assignee, which stays `None` until N4b-2.
