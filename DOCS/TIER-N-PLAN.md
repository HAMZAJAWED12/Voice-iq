# Tier N — Deferred-Work Plan

Owner: engineering · Status: **planned** · Created: 2026-07-25

Covers the four items left open after Tier S. Investigation changed the shape
of three of them — read [§ What the investigation changed](#what-the-investigation-changed)
before starting, the naive version of each is wrong.

| ID | Item | Feasible here? | Risk |
|----|------|----------------|------|
| N1 | Java callback v2 validation | ✅ **spec shipped** `2bf9b76` — Java team owns the code | none (doc) |
| N2 | Lazy ML imports → harness rejoins light CI | ✅ **done** — `9e57cb4` (code) + `4df98c6` (CI 4 jobs → 2) | was medium |
| N3 | E3 alignment `O(n²)` → two-pointer/bisect | ✅ **done** — `5c43f45` (tests) + `1f79131` (perf, ~5×) | was high; net in place |
| N4a | dateparser date resolution | ⏳ **not started** | medium |
| N4b | multilingual ur/ar + model extraction | ⛔ **deferred** — own sprint | high |

---

## What the investigation changed

1. **N1 has no code in this repo.** `find . -name "*.java"` returns nothing —
   the Java Action Layer is a separate service. Nothing to implement here.
   The honest deliverable is an integration spec + reference implementation
   the Java team applies on their side. **Replay protection stays inactive
   until they ship it**; no amount of Python work closes it.

2. **N3 has no dedicated tests.** `alignment_service.py` (409 LOC) has
   **zero** test files of its own — its 78% coverage is incidental, from
   `test_orchestrator.py` exercising it through the pipeline. Rewriting the
   two hot loops into a two-pointer sweep with no characterization suite is
   exactly the "refactor blind" the E2 harness existed to prevent. **Tests
   first, optimization second** — same two-phase shape as E2.

3. **N2 collides with the harness.** `test_orchestrator.py` patches
   module-level names (`app.pipeline.orchestrator.ASRService`, …) at **9
   sites** covering 12 services. Moving those imports inside functions
   deletes the attributes the patches bind to → all 51 harness tests break.
   The lazy conversion must retarget the patches to the source modules in
   the same commit.

4. **N4 is not one task.** Real date resolution (`dateparser`, a light pure-
   python dep) is tractable now. Multilingual ur/ar + spaCy/Stanza model
   extraction is a genuine sprint with heavy deps that fight the light CI
   lane. Ship the first, schedule the second.

---

## Item specs

### N1 — Java v2 callback validation *(spec only)*

**Why.** Python already emits `X-VoiceIQ-Timestamp` + `X-VoiceIQ-Signature-V2`
= `HMAC-SHA256(secret, "{timestamp}.{body}")` alongside the unchanged v1
header (`a54944b`). v1 is body-only, so a captured request replays forever.
Until Java validates v2 **and checks timestamp skew**, replay protection is
emitted but not enforced.

**Deliverable.** `DOCS/JAVA-CALLBACK-V2.md`: header contract, exact signed
material (byte-level), a reference Java verification method, the freshness
window (recommend ±5 min), trace-id de-duplication guidance, and the
migration/rollback sequence ending in v1 removal from
`java_callback_client.py`.

**Acceptance.** Doc merged; a Java engineer can implement without reading
Python. **Not** closable by this repo — track the Java ticket separately.

---

### N2 — Lazy ML imports *(harness rejoins light CI)*

**Why.** `orchestrator.py` imports whisper/torch/pyannote/transformers/
librosa at module top level, so `test_orchestrator.py` can't run on
`requirements-insight.txt`. That forced the dedicated `orchestrator-harness`
job (~8–12 min vs ~30 s). Lazy imports retire that job and put all 541 tests
in one fast lane.

**Approach.**
1. Move the 12 heavy service imports into the `_run_<stage>` methods that
   use them (import from the source module, e.g.
   `from app.services.asr_service import ASRService`).
2. **Same commit:** retarget the harness patches from
   `app.pipeline.orchestrator.<Name>` to `app.services.<mod>.<Name>`.
3. Only once green: drop `--ignore` from the light CI job and delete the
   `orchestrator-harness` job.

**Acceptance.** `python -c "import app.pipeline.orchestrator"` works with
only `requirements-insight.txt` installed (verify in a scratch venv, not by
assertion); harness 51/51 unchanged; CI drops to 3 jobs; light lane covers
all 541.

**Risk control.** Steps 1–2 are one commit (harness must never be red). Step
3 is a separate commit so a CI-config problem is revertible on its own.

**OUTCOME (done).** 8 imports moved (not 9 — `EmotionService` guards its own
torch import). Rather than lose `autospec`, patching became **adaptive**:
autospec against the real module where importable, `sys.modules` stub
injection where not — same assertions, strongest guarantee per environment.
`fpdf` moved to `requirements-insight.txt` (the light stack now imports the
orchestrator, which pulls `PDFService`). Verified in a venv built from the
light requirements alone: harness 51/51 in 8.25 s, full suite 628 + 1 skip in
15.7 s. CI: 4 jobs → 2, heavy job retired.

**Pre-work done — only 9 of the 12 imports are actually heavy.** Verified by
reading each service's top-level imports:

| Must become lazy (heavy top-level import) | Already light — leave alone |
|---|---|
| `ASRService` (whisper) | `FactCheckService` (httpx) |
| `DiarizationService` (torch, soundfile, huggingface_hub) | `PDFService` (fpdf) |
| `SentimentService` (transformers) | `normalize_to_wav` (subprocess only) |
| `KeywordService` (spacy, sentence_transformers, sklearn) | |
| `GenderService` (librosa, numpy) | |
| `TopicService` (transformers) | |
| `SummaryService` (transformers) | |
| `analyze_audio_quality` (numpy, soundfile) | |

`EmotionService` is a surprise: its only top-level import is the logger, so
it may already be light — **confirm before moving it**.

`AudioQualityReport` is used only as the `_PipelineState.aq` annotation.
With `from __future__ import annotations` in force, move it under
`if TYPE_CHECKING:` rather than importing it at runtime.

Each heavy name is used in exactly one `_run_<stage>` method, so each import
moves to exactly one call site (grep line numbers recorded at investigation
time: ASR 325, Diar 368, Sentiment 490, Keyword 505, Gender 523, Emotion
542/544, Topic 559, Summary 571, normalize 231, audio_quality 280).

---

### N3 — Alignment `O(n²)` → two-pointer / bisect

**Why (and the honest caveat).** Profiled at ~1.15 s on a 60-min fixture —
**<5 % of ML-dominated wall-clock**, which is why it was deferred. Optimizing
now is a pre-emptive win, not a measured bottleneck. It pays off when audio
gets much longer or denser. Proceeding at the user's explicit direction.

Two genuine quadratics (see tech-debt #4):
- `_best_asr_for_window` ~1.19 s — O(M·A), 600×600 → 360k `_overlap` calls
- `_align_words_to_diarization` listcomp ~0.72 s — O(D·W)

**Phase N3a — characterization tests (no production change).**
Build `app/insights/tests/test_alignment_service.py` pinning current
behavior: word slicing, overlap policy, merge/gap rules, speaker roles,
`build_conversation` turn merging, and the empty/missing-input edges.
Target ≥95 % on `alignment_service.py`. Include a **property test**: for
randomized ASR+diar inputs, record outputs as a golden baseline.

**Phase N3b — optimize.** Both inputs are already time-sorted:
- `_align_words_to_diarization` → single two-pointer sweep, O(D+W)
- `_best_asr_for_window` → `bisect` to bound the candidate window, O(M·log A)

Re-run N3a after each; **byte-identical output required**.

**Acceptance.** N3a suite green before and after N3b with zero assertion
edits. Benchmark recorded before/after on the documented 60-min fixture.

**Stop condition.** If any output differs and the difference is arguably a
bug-fix, **stop and report** — do not silently "improve" alignment.

---

### N4 — Sprint 6 Phase 2 NLP *(split)*

**N4a — real date resolution (tractable now).**
`datetime_extractor.py` currently extracts the phrase verbatim ("next
Monday") and deliberately does not resolve it. Add `dateparser` (light,
pure-python) to resolve to an absolute ISO datetime **alongside** the
existing phrase — never replacing it, so the Recommendation contract stays
backward compatible. Needs an explicit "now" reference for determinism
(inject a clock; never call `datetime.now()` inside the extractor).

*Acceptance:* "next Monday" + a fixed reference date → correct ISO date;
unparseable phrases → `None`, phrase preserved; extractor is deterministic
under a frozen clock.

**N4b — multilingual ur/ar + model-based extraction (own sprint, deferred).**
spaCy/Stanza pipelines, non-Latin script handling, per-language models.
Heavy deps + model artifacts — the exact class that broke CI in Tier S (the
spaCy `en_core_web_sm` artifact). Needs its own dep-isolation strategy and
CI lane decision **before** any code. Do not start inside Tier N.

---

## Sequencing

| Wave | Items | Why this order |
|------|-------|----------------|
| **N-A** | N1 spec | Zero risk, unblocks the Java team immediately (they're the long pole) |
| **N-B** | N3a alignment characterization tests | Pure gain, no behavior change; unblocks N3b |
| **N-C** | N3b two-pointer + bisect | Only safe once N-B exists |
| **N-D** | N2 lazy imports (+ patch retarget), then CI simplification | Independent; touches the harness, so do it when the harness is otherwise stable |
| **N-E** | N4a dateparser resolution | Self-contained feature |
| — | N4b multilingual | **Deferred** — own sprint, decide CI strategy first |

---

## Cadence (unchanged)

Per commit, via `.venv\Scripts\python.exe -m ...`: new/updated tests · full
suite green · harness 51/51 · `ruff check` clean · `mypy app/insights/
app/agent_brain/` = 0 · atomic commit · stop-on-fail · local until reviewed.

**Hard rule carried from E2:** the harness is the spec. If it goes red, the
code drifted — fix the code, not the test. The only sanctioned harness edit
in this tier is the N2 patch-target retarget, which is a target change, not
an assertion change.
