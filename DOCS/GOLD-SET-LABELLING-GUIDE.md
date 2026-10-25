# Fact-Check Agent gold set: labelling guide

Status: **v0.7, approved by the project owner (sole maintainer) on 2026-10-24**: every section 14 proposal
accepted, plus rulings G1-G10 from the calibration batch, G11-G23 from batch 02, G24-G31 from batch 03 and
G32-G45 from batch 04 (section 15). The approved data lives in [`app/evaluation/gold/`](../app/evaluation/gold/)
(synthetic only; see its README).
Follows [D3-GOLD-SET-BRIEF.md](D3-GOLD-SET-BRIEF.md) Option A as signed on 2026-10-19
(register row D3 in [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register)),
together with D7 (domains), U2 (English only), U10 (synthetic only), U5b (reason kinds), A6 (private
individuals) and D9 (what counts as high-stakes). No code. No verdict labels.

Where this guide makes a choice that no signed decision makes, it says so and lists the choice in
section 14 for the owner.

---

## 1. What this set is for, and what it is not

The gold set measures two things in the Phase 1 model bake-off:

1. **Extraction** (Layer A): which statements in a conversation assert a checkable claim, and what that claim
   is. Feeds extraction precision and recall, and the plan's section 9 target "claim extraction at least 0.85
   recall".
2. **Checkability and scope** (Layer B): whether each statement is a checkable claim, an opinion, a
   prediction, a request or vague, and which domain it belongs to. Feeds checkability F1 and the
   domain-scope check.

It does **not** label verdicts. Verdict labels (Layer C) belong to register row D3b: evidence-relative,
labelled after Phase 4 and D4, and never on out-of-scope rows. Nothing in this set is a verdict.
`deliberately_false` (section 4.4) records what the drafter intended. It is drafted only on in-scope
checkable rows (one narrow exception, section 4.4), and D3b later checks those rows against evidence.

How the labels are scored (per proposition or per segment, how model output is matched to items, whether
date anchors count, binary or per-class checkability F1, thresholds) is fixed in the D6 criteria note before
the first bake-off run, not here. The labels support each option.

Fixed by the signed decisions:

| Rule | Source |
|---|---|
| English only in this version. The plan's code-switched Urdu/English slice is deferred, not dropped. | U2 Option B; D3 |
| Synthetic conversations only. No real call content, including the repository's sample audio. | U10 |
| Layers A and B only. | D3 Option A |
| Domain list: in scope `GENERAL`, `MARKET_FINANCE`, `WEATHER`; out of scope `PERSONAL_FINANCE`, `HEALTH`, `LEGAL`, `NEWS`, `OWN_COMPANY`, `OTHER`. | D3 and D7, signed together |
| Out-of-scope rows get Layer B labels only and never a verdict label. | D3, D7, D3b |
| The D3 brief's "never `HEALTH`/`LEGAL`" rule (brief §4, §9) now applies to verdict labels only. `HEALTH` and `LEGAL` are Layer B domain values here. The brief's warning stands: results from this set, and later from D3b, say nothing about how the agent performs in regulated domains, and must not be read as evidence that those domains are safe. | D3, signed with D7 |
| A claim about a private individual is out of scope. | A6 |
| Reason kinds match U5b: `opinion`, `prediction`, `request`, `vague`, `outOfScope`. | U5b; PHASE-0 §2.2 |
| Drafts are AI-assisted and the project owner checks every item. | D3 |
| Consistency: the owner re-labels 40 items blind after one week (intra-rater agreement), in place of a second annotator and the brief's 20% double-labelled overlap (brief §6, §10 item 4). | D3 |

---

## 2. Units

| Unit | What it is | Id |
|---|---|---|
| **Conversation** | One synthetic call excerpt between two speakers, 4 to 12 segments, with a synthetic call date. | `source_id`: `syn_001` |
| **Segment** | One speaker turn, as the transcription pipeline would emit it. | `segment_id`: `seg_001` (restarts per conversation) |
| **Item** (candidate row) | One segment that a reasonable extractor might surface as a statement. **The set holds at least 200 items; the target is exactly 200.** | `claim_id`: `gs_0001` (never reused) |

**Which segments become items.** Every segment that makes a statement or a request about the
conversation's subject matter: a factual assertion, an opinion, a prediction, a vague assertion, or a request
or question.

**Not items** (they stay in the conversation text): greetings, thanks and goodbyes; acknowledgements of
what the other speaker said ("okay", "mm-hm", "right"), including a reply that only agrees with a checkable
tag, negative or presupposing question ("yeah", "yeah it is", "that's right"); turns that only manage the
call ("one moment", "bear with me", "let me just check"), including an opener that only names the call's topic
("I'm calling about a refund", "it's about the export issue"); and small talk with no content ("how are you?").
A goodbye or closing line that only repeats a detail already
given in the call, adding or changing no value ("see you Friday then"), is a goodbye (G45).
An opener that states a specific want the listener can act on (a destination, date, amount or product) is a
`REQUEST` item (5.6; G19).
A reply that adds or changes a value is an item. A statement about the speaker's own feelings is an item
only when it is about the subject matter (section 5.6).

A bare "yes" or "no" answering a question that asks whether a value holds ("is it still raining in Leeds?" /
"yeah") is an item (section 5.6). Drafts avoid it: such questions are answered with at least a short clause.

One item per segment, as in the brief's examples. If a segment holds two separate statements, the row
covers both (sections 7.1, 7.2 and 8); it is never split into two rows.

The `source_id` prefix `syn_` (the brief's example uses `call_`) marks every conversation as synthetic in
the id itself.

---

## 3. The labelling procedure

Label each item in this order.

1. **Checkability** (section 5): `CHECKABLE`, `OPINION`, `PREDICTION`, `REQUEST` or `VAGUE`.
2. **Domain** (section 6) and **private individual** (section 6.3).
3. **Scope** follows from step 2: the row is **in scope** when the domain is `GENERAL`, `MARKET_FINANCE` or
   `WEATHER` and `private_individual` is `no`; otherwise it is **out of scope**.
4. **Layer A**, in-scope rows only (section 7): `is_claim` (derived), `span_text`, `claim_text`.
5. **Compound** and **time-sensitive** (sections 8 and 9), all rows.
6. **Confidence** (section 10), all rows.

`in_scope`, `is_claim` and `reason_kind` are derived, never typed by hand.

---

## 4. Fields

### 4.1 Per conversation (`conversations.jsonl`, one line per conversation) — **new**

The brief has no per-conversation file. This one is needed because the extractor reads whole
conversations: Phase 3 passes speaker-aware segments, timestamps and ASR confidence (plan, Phase 3), and
an extraction from a segment that is not an item can only be scored if that segment is recorded.

| Field | Values | Notes |
|---|---|---|
| `source_id` | `syn_001` | |
| `call_date` | `2025-03-12` | Synthetic. Anchors "today", "yesterday" and "this week" in `claim_text`, the `NEWS` window and D3b. The v2 request has no date field, so the agent under test does not see it (Q12). |
| `setting` | short free text | e.g. "freight forwarder, customer asks about a shipment". For the reader only: it never resolves a label (section 5.5). |
| `language` | `en` | Always `en` in this version. |
| `provenance` | `synthetic` | Always. |
| `drafted_by` | `claude-draft` | The AI assistant drafted the conversation. |
| `guide_version` | `v0.3` | The guide version the batch was labelled under. |
| `segments` | list | Each: `segment_id`, `speaker` (`SPEAKER_00`, `SPEAKER_01`), `start`, `end` (seconds), `text`, `confidence` (synthetic ASR confidence, 0.0 to 1.0). Maps one to one onto the v2 request segment (PHASE-0 §2.2): `segment_id` to `segmentId`, `speaker` to `speakerId`, `confidence` to `asrConfidence`. |

### 4.2 Per item (`items.csv`, the canonical table; `items.jsonl`, generated from it for the harness)

The brief's schema (D3 brief section 4), Layers A and B. **New** marks a field the brief does not have;
**changed** marks a brief field whose meaning changes.

| Field | Filled by | Values | Notes |
|---|---|---|---|
| `claim_id` | pre-filled | `gs_0001` | Stable, never reused. |
| `source_id` | pre-filled | `syn_001` | |
| `segment_id` | pre-filled | `seg_003` | |
| `speaker_label` | pre-filled | `SPEAKER_00` / `SPEAKER_01` | Never a name. |
| `utterance` | pre-filled | verbatim text | A copy of the segment's `text` in `conversations.jsonl`, which holds the full transcript, filler segments included. |
| `checkability` | labeller (B) | `CHECKABLE` / `OPINION` / `PREDICTION` / `REQUEST` / `VAGUE` | Section 5. |
| `domain` | labeller (B) | `GENERAL` / `MARKET_FINANCE` / `WEATHER` / `PERSONAL_FINANCE` / `HEALTH` / `LEGAL` / `NEWS` / `OWN_COMPANY` / `OTHER` | Section 6. **Changed**: replaces the brief's `GENERAL`/`FINANCE`/`NEWS`/`OTHER` (D3 and D7, signed together). |
| `private_individual` **new** | labeller (B) | `yes` / `no` | Section 6.3 (A6). |
| `in_scope` **new** | derived | `yes` / `no` | Step 3 of section 3. |
| `is_claim` **changed** | derived | `yes` / `no` / blank | On in-scope rows `yes` exactly when `checkability` is `CHECKABLE`, otherwise `no`; blank on out-of-scope rows (Layer B only). The brief has the labeller fill it. |
| `span_text` **new** | labeller (A) | exact substring of `utterance` | In-scope `CHECKABLE` rows only. Section 7.1. |
| `claim_text` **changed** | labeller (A) | normalised proposition(s) | In-scope `CHECKABLE` rows only. One proposition per part, separated by ` \| ` (section 7.2); the brief's example holds one. |
| `is_compound` **changed** | labeller (B) | `yes` / `no` | Section 8. Per row (segment), not per sentence as in the brief. |
| `time_sensitive` | labeller (B) | `yes` / `no` | Section 9. |
| `deliberately_false` | pre-filled | `yes` / `no` | Construction metadata, not a verdict. Section 4.4. |
| `reason_kind` **new** | derived | blank / `opinion` / `prediction` / `request` / `vague` / `outOfScope` | Section 4.3. |
| `confidence_in_label` | labeller | `high` / `low` | Section 10. |
| `provenance` **new** | pre-filled | `synthetic` | Always. |
| `drafted_by` **new** | pre-filled | `claude-draft` | Always: every label starts as an AI draft. |
| `label_status` **new** | owner | `draft` / `owner_ok` / `owner_fixed` | After the owner's check. Replaces the brief's `annotator` initials, so no person is named in the repository. |

Not in this version: `verdict`, `evidence_bundle_id`, `verdict_note` (Layer C, D3b).

### 4.3 `reason_kind` (derived)

Uses the `nonCheckable[]` reason kinds in PHASE-0 §2.2 (U5b), so the gold set and the agent's output use one
taxonomy.

| Row | `reason_kind` | Where the v2 agent should put it |
|---|---|---|
| `checkability` is not `CHECKABLE` | the category in lower case (`opinion`, `prediction`, `request`, `vague`) | `nonCheckable[]` |
| `CHECKABLE` and out of scope (any out-of-scope domain, or `private_individual` `yes`) | `outOfScope` | `nonCheckable[]` |
| `CHECKABLE` and in scope | blank | `claims[]` |

Order: checkability first, then scope. A non-checkable statement keeps its checkability reason even when
its topic is out of scope ("I reckon that diet is nonsense" is `opinion`, not `outOfScope`). PHASE-0 says the
checkability gate runs before the verdict stage and the scope check runs before retrieval, but not which of
the two runs first; this order is the guide's reading (Q1).

### 4.4 `deliberately_false`

Set by the drafter before labelling. `yes` means the drafter wrote a claim that is false at the call date by
construction. `no` means the drafter did not intend it to be false; it does **not** mean true. It is never a
verdict, and it is never flipped, because it records what the drafter intended.

- `yes` is drafted only on in-scope `CHECKABLE` rows, and only those count towards the 40 (section 12). D3b
  later labels a verdict on them independently against evidence.
- `no` on every out-of-scope row, and on every non-checkable row except one kept under (a) below.
  Out-of-scope rows are never drafted to be false: D3b never checks them, so a `yes` there would be an
  unchecked truth label.
- (a) If the owner's check moves a `yes` row to a non-checkable category and it stays in scope, the drafter
  rewrites the utterance as a sincere checkable claim for the owner to re-check, or the row keeps `yes` with
  a note and is left out of the 40.
- (b) If the owner's check moves a `yes` row out of scope, it never keeps `yes`. The drafter rewrites it so
  that it is in scope again (the owner re-checks), or withdraws the whole conversation (its `claim_id`s are
  retired, never reused) and batch 4 replaces it.

---

## 5. Checkability

One value per row. A ruling in section 5.6 settles the case it names; otherwise ask these questions in order
and stop at the first `yes`.

1. **Does any clause assert a specific proposition (subject, predicate and value, with place or time where
   needed) that someone other than the speaker could confirm or refute as of the call date, from records or
   observation, public or not?** Then `CHECKABLE`, even if other clauses are opinions, requests or
   predictions. The value need not be a number ("raining", "Canberra"). Hedges such as "I think",
   "apparently" and "if I remember right" do not change this. Whether the record is public, and whether this
   release checks the claim, is decided by `domain` and `private_individual` (section 6), not here: the
   domain-scope check is kept separate from checkability (PHASE-0 §2.2 and §5 point 1; U5b). A clause the
   speaker plainly does not mean literally, marked as sarcasm by the wording ("oh sure", "yeah right",
   laughter), is not an assertion.
2. **Is the main clause about a future state that cannot be settled at the call date?** `PREDICTION`. A
   scheduled, timetabled or booked event stated as fact is not a prediction (section 5.6).
3. **Is it an evaluation whose point is the speaker's judgement of quality, value or preference** (good, bad,
   best, too expensive, a scam, worth it), **advice framed as the speaker's judgement** ("you should", "I'd"),
   or the speaker's feelings, hopes or attitudes about the subject? `OPINION`. A loose descriptive word ("mad",
   "weird", "crazy") attached to an unspecified change or level is not an evaluation: go to rule 5. Nor is a
   performance word about a market asset's unmeasured movement ("done all right", "had a good run", "crept up"):
   it describes direction and is `VAGUE`. A judgement measured against the speaker's expectations or interests
   ("better than I thought", "good for us") is `OPINION` (G26). A metaphor of an asset's standing or status with
   no word of movement or level ("the underdog", "the poor relation", "a safe haven") is a judgement of
   standing: `OPINION`, confidence `high`; a metaphor of movement ("on a tear", "through the roof") is `VAGUE`
   (G34).
4. **Does it ask or tell the listener to do, find or confirm something?** `REQUEST`, including questions and
   instructions.
5. **Otherwise it asserts something, but without the subject, quantity, place or time a checker would need.**
   `VAGUE`. `VAGUE` is for missing detail, and for marked sarcasm with no other clause (section 5.6). A fully
   specified statement is never `VAGUE` because its record is private.

### 5.1 `CHECKABLE`

A specific proposition that someone other than the speaker could confirm or refute as of the call date.

| Utterance | Label | Why |
|---|---|---|
| "the capital of australia is canberra right" | `CHECKABLE`, `GENERAL` | A stable public fact. The tag "right" states a believed value (section 5.6). |
| "I think the euro's at about 1.08 against the dollar today" | `CHECKABLE`, `MARKET_FINANCE` | A hedge on a specific rate. |
| "it was minus three in Oslo this morning, honestly" | `CHECKABLE`, `WEATHER` | An observed temperature at a place and time. |
| "it's pouring in Leeds right now" | `CHECKABLE`, `WEATHER` | An observable condition at a named place and time. |
| "the forecast says heavy rain in Leeds tomorrow" | `CHECKABLE`, `WEATHER` | The claim is about what the published forecast says, which can be checked now (section 5.6). |
| "your container left Rotterdam on Tuesday" | `CHECKABLE`, `OWN_COMPANY` | Specific; the company's own records settle it. Out of scope. |

The numbers in this guide's examples are illustrative only, not market levels.

### 5.2 `PREDICTION`

A claim about the future that cannot be settled at the call date.

| Utterance | Label |
|---|---|
| "bitcoin's going to hit 200k by Christmas you watch" | `PREDICTION`, `MARKET_FINANCE` |
| "it'll probably rain all weekend in Manchester" | `PREDICTION`, `WEATHER`. Compare "the forecast says...", which is `CHECKABLE`. |
| "if gold keeps going like this it'll be three thousand next year" | `PREDICTION`, `MARKET_FINANCE`. Conditionals about the future are predictions. |

### 5.3 `OPINION`

An evaluation, advice or attitude that evidence would not settle.

| Utterance | Label |
|---|---|
| "honestly crypto is just gambling" | `OPINION`, `MARKET_FINANCE` (section 6.2 ruling) |
| "Lisbon's the nicest city in Europe for a conference" | `OPINION`, `GENERAL` |
| "the dollar's way too strong right now it's ridiculous" | `OPINION`, `MARKET_FINANCE`. "Too strong" is a judgement; no number is asserted. |

Boundary: "the dollar is at a twenty-year high" is `CHECKABLE`. A superlative with a measurable basis is
checkable; a superlative of taste is not.

### 5.4 `REQUEST`

Asks or tells the listener to do, find or confirm something. Includes questions and instructions.

| Utterance | Label |
|---|---|
| "can you check what the yen's doing before we book" | `REQUEST`, `MARKET_FINANCE`, `private_individual` `no` |
| "what's the weather like in Dubai this week" | `REQUEST`, `WEATHER` |
| "is the price of copper still above $9,000" | `REQUEST`, `MARKET_FINANCE`. A question asking whether a value holds asserts nothing. |
| "send me the rate sheet before five" | `REQUEST`, `OWN_COMPANY` |

Boundary: "copper's above nine thousand, can you lock that in?" is `CHECKABLE` (rule 1: a clause asserts).

### 5.5 `VAGUE`

Asserts something, but leaves out what a checker would need: the subject, the quantity, the place or the time.

| Utterance | Label |
|---|---|
| "prices have gone up a lot recently" | `VAGUE`, `GENERAL`. Which prices, where, by how much? |
| "the weather's been mad lately" | `VAGUE`, `WEATHER`. No place, no measure. |
| "oil did something weird last week" | `VAGUE`, `MARKET_FINANCE` |
| "it's boiling in Dubai today" | `VAGUE`, `WEATHER`. A word of degree with no number (section 5.6). |

Boundary: when the conversation supplies the missing piece in an earlier or later segment, use it. If the
previous turn was "Brent's at $82 a barrel", then "it's dropped below 80" is `CHECKABLE` (section 7.2). Only
segment text resolves context. The `setting` field never does, because the agent never sees it: "lovely and
sunny here today" resolves its place only when a segment names the place; otherwise it is `VAGUE`.

### 5.6 Rulings for common cases

| Case | Ruling | Confidence |
|---|---|---|
| Hedged fact ("I'm pretty sure Rome is older than London"); a belief stating a specific value ("I believe gold's over two thousand") | `CHECKABLE` (rule 1) | `high` |
| Reported speech about a present or past fact ("my manager said the pound is at 1.25 against the dollar") | `CHECKABLE`. The embedded proposition is the claim; who said it is not. | `high` |
| A future weather state attributed to a published forecast, warning or outlook ("the forecast says", "the Met Office says it'll", "my weather app says") | `CHECKABLE`, `WEATHER`; `claim_text` keeps the source ("The published forecast for Leeds for 2025-03-13 is heavy rain") | `high` |
| A future state attributed to a person or an unnamed "they" ("my mate says it'll rain all weekend", "they reckon it'll snow") | `PREDICTION` | `low` |
| A scheduled, timetabled or booked event stated as fact, marked by "is scheduled", "meets on", "is booked on", "is due" or a timetable time ("the ECB meets next Thursday", "the 7:40 gets in at 10:10", "your container is booked on Thursday's sailing"). The markers are examples: an announced or legislated change, or a scheduled event, stated as fact in the present simple or progressive with a date ("is going up by 4.1% this April", "the clocks go back this Sunday") is a schedule too (G15). A date that a calendar rule fixes and no one can move (Easter, a solstice) is not a schedule but a reference fact: `claim_text` says "falls on <date>", `time_sensitive` `no` (G29) | `CHECKABLE`; `claim_text` says "is scheduled for <date>"; `time_sensitive` `yes` | `high` |
| An expectation, estimate or intention, or a modal or hedged form of a schedule ("should arrive Thursday", "probably lands about six", "we'll ship it Friday") | `PREDICTION` | `high` |
| Tag, negative or presupposing question that states a specific value as believed ("gold's over two thousand isn't it", "isn't Canberra the capital", "did you see gold hit 2,500 yesterday", "have you heard the pound's below 1.20") | `CHECKABLE` | `low` |
| Question that asks whether a value holds or asks for it ("is X still above Y?", "what's X at?") | `REQUEST` | `high` |
| Tag question on a statement that states no specific value ("it'll be hot, won't it?") (G2) | The label of the statement | as for the statement |
| A stated need or want addressed to someone who can meet it ("I need about 800 euros", "I'd like a quote") (G3) | `REQUEST`; `OWN_COMPANY` when it is this call's own order | `high` |
| "you know X" | A statement: "you know" is a filler (section 7.1) | as for X |
| A rule, rate, right or allowance that differs between jurisdictions (a tax rate, a legal right, a driving rule) (G38) | `CHECKABLE` only when segment text names or clearly implies the jurisdiction (a named country or a place in it, a "£" amount, a named authority, a product or scheme that exists in one jurisdiction only); otherwise `VAGUE` (missing place, rule 5) | `high` |
| Bare "yes"/"no" answering a question that asks whether a value holds | `CHECKABLE`; `claim_text` built from the question; `span_text` is the answer word (the one exception to 7.1) | `low` |
| An answer word ("yes", "no", "not yet") to a question that asks whether a value or condition holds, followed by a clause ("Not yet, it's about 12°C and sunny here") (G13) | The answer is a part of its own, with its `claim_text` built from the question, unless the clause restates it ("Yeah, it's chucking it down" restates "raining"). A "yes", "yeah" or "that's right" agreeing with a tag, negative or presupposing question is not a part (section 2). A negated word of degree ("not really" to "is it roasting?") is vague and not a part | as for the row |
| An observable condition at a named place and time with no number ("raining", "snowing", "sunny", "below freezing") | `CHECKABLE` | `high` |
| A word of degree with no number about a present or past state ("boiling", "roasting", "freezing" without "below", "really weak") | `VAGUE`; a number or "below freezing" makes it `CHECKABLE` | `high` |
| A future state stated with a word of degree ("it'll be properly hot") (G2) | `PREDICTION` | `high` |
| "Too X", "ridiculous" | `OPINION` | `high` |
| Specific claim about a speaker's own organisation ("we shipped 400 units last month") | `CHECKABLE`, `OWN_COMPANY`; scope makes it `outOfScope` | `high` |
| Past personal experience ("I bought bitcoin at 60k last month") | `CHECKABLE`, `MARKET_FINANCE`, `private_individual` `yes`; scope makes it `outOfScope` | `high` |
| Advice ("you should fix the rate before the payment goes", "I'd wait if I were you", said about buying currency for a payment) | `OPINION`, `MARKET_FINANCE` (section 6.2 ruling) | `high` |
| Instruction to the listener ("fix the rate now", "book it for Thursday") | `REQUEST`, `OWN_COMPANY` | `high` |
| Sarcasm marked in the wording ("oh sure, bitcoin's at a million") | Label by the other clauses, or `VAGUE` if there are none. Drafts avoid it. | `low` |
| A sincere assertion of an implausible value ("gold's at $400 an ounce now") | `CHECKABLE`, however false | `high` |
| The speaker's own feelings, hopes or attitudes about the subject ("I'm worried about the rate", "I hope it stays dry for Saturday") | `OPINION`, `private_individual` `no` | `high` |
| A claim retracted later in the call | Both rows stay `CHECKABLE` and count for recall; the notes link the pair | `high` |

---

## 6. Domain

Every row gets one domain, including non-checkable rows. For a non-checkable row, use the domain its
subject would take if it were stated as a checkable fact: an opinion about a currency is `MARKET_FINANCE`, an
opinion about a city is `GENERAL`, a request about the speakers' own order or quote is `OWN_COMPANY`.

Which clause decides: on a `CHECKABLE` row, only the checkable clauses decide domain and scope; an opinion,
request, prediction or vague aside in the same segment does not ("it's 34 in Dubai today, drink loads of
water" is `WEATHER`). On a non-checkable row, domain and scope come from the clause that decided
checkability (the first rule in section 5 that fired, or the 5.6 ruling that applied). Only asserted predicates count;
a presupposition inside a definite description ("in the gales last week") is not a separate proposition and does not
decide the domain. A presupposition counts only under the 5.6 presupposing-question ruling (G7).

When a row could fit two domains, take the first that fits in this order (this guide's proposal, Q10):

1. `PERSONAL_FINANCE` (rules and amounts on tax, pensions, benefits, credit, loans or insurance, even when set
   by statute), then `HEALTH`, then `LEGAL` (other law and rights). This mirrors D9 clause 3, under which the
   agent fails closed: if, after applying the definitions below, a row is still torn between a high-stakes
   domain and another, choose the high-stakes one and set `confidence_in_label` to `low`.
2. `OWN_COMPANY`
3. `MARKET_FINANCE` or `WEATHER`
4. `NEWS`
5. `GENERAL`
6. `OTHER`

**Decide from the conversation only. Never look up when a real event happened.**

### 6.1 In scope

| Domain | Covers | Examples |
|---|---|---|
| `GENERAL` | Public facts of the kind a reference work or official statistics record: geography, history, science, units and measures, public institutions' public facts, sports records, and past events of public record (history, science, sport, public institutions) not placed within 30 days of the call date. Climate normals and records are `GENERAL`, not `WEATHER`; a reading is a record only when segment text presents it as one ("the hottest day ever"), otherwise it is an observed reading, `WEATHER`, even when the labeller knows it is a record (G44). | "Mount Everest is 8,849 metres", "the Treaty of Rome was signed in 1957", "London gets about 600 mil of rain a year" |
| `MARKET_FINANCE` | Any statement about the price, rate, level or movement of a currency, crypto asset, listed stock, stock index or commodity on its market, including forecasts of and opinions about it, whatever its date. D7 names currency, crypto, stock and commodity prices, and D9 clause 1 says market-data quotes are not high-stakes. Including stock-index levels, and predictions and opinions about prices, is this guide's reading (Q6). | "sterling's at 1.27 against the dollar", "Brent's at $82 a barrel", "the FTSE's at eight thousand two hundred" |
| `WEATHER` | Observed, current or forecast weather at a named place, and published weather warnings, whatever their date. The consequences of weather (a port closed, flights cancelled, flood damage) are `NEWS` within the `NEWS` window, otherwise `GENERAL`. Weather for a whole month or season, or for a period mostly beyond published forecasts (more than about 14 days after the call date), is a climate normal: `GENERAL`, `time_sensitive` `no`; within the forecast horizon it is `WEATHER` (G9). G9 covers typical or expected weather and weather beyond the forecast horizon: observed weather over a month or season still running, or ended within 30 days of the call date, is `WEATHER`, and section 9 decides `time_sensitive` (G35). | "it's 34 in Dubai today", "the Met Office has a wind warning out for Cornwall" |

D7 enables weather and stock-price checks only once their provider keys are confirmed in the target
deployment, or D4's web fallback exists. That is a deployment switch, not a labelling rule: label these rows
in scope.

### 6.2 Out of scope (Layer B only)

| Domain | Covers | Examples |
|---|---|---|
| `PERSONAL_FINANCE` | D9 clause 1's personal-financial definition: tax, pensions, benefits, credit, loans and mortgages, insurance, and how suitable an investment is or what it promises to return. Includes neutral facts on these topics as well as statements that recommend or judge buying, holding or selling an investment, or say what return a product promises or guarantees. D7's signed exclusion says "personal-financial advice"; reading it through D9 clause 1, so that neutral facts on these topics are out of scope too, is this guide's reading (Q6). | "the ISA allowance is twenty thousand", "mortgage rates are about 5% now", "I'd talk to an adviser before you put savings into crypto" |
| `HEALTH` | Health and medical claims: conditions, treatments, medicines, health services, health effects. A population statistic that names no condition, treatment, medicine, service or health effect (life expectancy, birth or death rates) is official statistics, `GENERAL` or `NEWS` by the boundary ruling below; a statistic about a named condition, treatment or service (prevalence, vaccine uptake, waiting times) is `HEALTH` (G33). | "flu jabs are free for over-65s in England" |
| `LEGAL` | Law, regulation and legal rights not covered by `PERSONAL_FINANCE`. | "you've got thirty days to reject faulty goods by law" |
| `NEWS` | Events the conversation places within 30 days before the call date ("on Monday", "last week", "just announced"), or presents as an ongoing story. Excludes market prices and levels, and weather readings, forecasts and warnings, whatever their date: those are `MARKET_FINANCE` or `WEATHER`. | "the port strike was called off on Monday" |
| `OWN_COMPANY` | Claims about either speaker's own organisation, or this call's own order, booking, invoice or account: products, own prices and fees, policies, performance, staff, delivery. | "our uptime last quarter was 99.9%", "your container left Rotterdam on Tuesday" |
| `OTHER` | A subject in none of the domains above: third-party consumer products and services, retail prices, timetables, local business details, including events about third-party products, services or local businesses, whatever their date: such an event is `OTHER` even within the `NEWS` window, because `NEWS` is for events about public matters (strikes, public bodies, and generic public or industry events) (G30). | "the 7:40 from Leeds gets into King's Cross at ten past ten" |

The brief's rows `gs_0001` and `gs_0003` (uptime, ISO certification) predate D7. Under D7, which puts
own-company claims out of scope, they are `OWN_COMPANY` and out of scope.

**Boundary rulings** (this guide's proposals, Q6):

| Case | Ruling |
|---|---|
| Central-bank policy rate ("the base rate is 4%") | Not a market quote, so not `MARKET_FINANCE`. `NEWS` when the conversation places the change within 30 days, otherwise `GENERAL`. |
| Official statistics (inflation, unemployment, house-price indices) | As the policy rate: `NEWS` when the conversation places the release within 30 days, otherwise `GENERAL`. |
| A predicted event of any kind, weather consequences included: a policy-rate change, statistic, company result, closure or disruption ("they'll cut again before the summer") (G6, G28) | `GENERAL`; `NEWS` only when the conversation presents it as part of a current news story. |
| Company results ("a listed retailer's profits were up last year") | `NEWS` when the conversation places them within 30 days, otherwise `GENERAL`. Drafted only in this unnamed form, which makes the row `VAGUE` (drafting rule 4 bars real companies' results). |
| Scheduled event ("the ECB meets again next Thursday") | `NEWS` when the conversation presents it as part of a current story, otherwise `GENERAL`. |
| A law or regulation coming into force (a speed limit, a parking scheme, a new rule) (G16) | `LEGAL`, even when placed within 30 days, with confidence `low`. |
| Rates at a currency desk | A market rate stated as the market's ("the euro's at 1.08 against the dollar today") is `MARKET_FINANCE`. The desk's own buy or sell price, margin or fee ("we're selling at 1.12") is `OWN_COMPANY`. Timing advice from the desk is `OWN_COMPANY` only when it names the desk's product or quote or says "with us"; otherwise `MARKET_FINANCE` (G10). |
| Retail or consumer prices (pump prices, a bureau's or airport's exchange rate, energy tariffs) | `OTHER`, or `OWN_COMPANY` for the speakers' own prices. Only a market quote is `MARKET_FINANCE`. |
| General judgement of a market ("crypto is just gambling") | `MARKET_FINANCE`. |
| Advice on when to buy, sell or fix currency for a payment or trip | `MARKET_FINANCE`. Advice to take the speakers' own product or quote ("fix the rate with us now") is `OWN_COMPANY`. Advice on buying, holding or selling an asset as an investment or savings is `PERSONAL_FINANCE`. |

Drafts put an explicit time cue in the conversation for every policy-rate, statistics, company-result and
scheduled-event row, so these rulings never need outside knowledge.

### 6.3 `private_individual` (A6)

`yes` when the claim's subject is a private individual: a speaker, a colleague, a relative, a named or
unnamed member of the public. The row is then out of scope whatever its domain. Public figures acting in
their public role (heads of state, chief executives of listed companies on company matters, athletes on
their records) are not private individuals; a public figure acting outside their public role is. A6 does
not define the term; this definition is the guide's proposal (Q9).

On a non-checkable row, `private_individual` is `yes` only when the topic itself is a private person's life or
circumstances ("my brother's going to lose his job"). A speaker who asks, advises, feels, hopes or intends
something about a public topic is not its subject. Speakers or staff acting for an organisation, and this
call's own order, booking or account, are `no` (domain `OWN_COMPANY`).

| Utterance | `checkability` | `domain` | `private_individual` |
|---|---|---|---|
| "my brother got fined for parking in the bus lane" | `CHECKABLE` | `LEGAL` | `yes` |
| "I bought bitcoin at 60k last month" | `CHECKABLE` | `MARKET_FINANCE` | `yes` |
| "I'm worried about the rate" | `OPINION` | `MARKET_FINANCE` | `no` |
| "Marie Curie won two Nobel prizes" | `CHECKABLE` | `GENERAL` | `no` |

---

## 7. Layer A (in-scope rows only)

Out-of-scope rows leave `is_claim`, `span_text` and `claim_text` blank (signed: Layer B only). On in-scope
rows `is_claim` is `yes` exactly when `checkability` is `CHECKABLE`, and `no` otherwise.

What the labels support, for the D6 criteria note to choose from:

- **Extraction recall** over in-scope `CHECKABLE` rows.
- **Extraction precision** over every extraction except those matched to out-of-scope rows. Extractions from
  non-item segments and from in-scope non-checkable rows are false positives.
- **Layer B** on every item row, in or out of scope, against its gold `reason_kind`: *routed* (a
  `nonCheckable[]` entry with that reason kind, or a `claims[]` entry for a blank reason kind), *leaked* (a
  `claims[]` entry for a row whose gold reason kind is not blank) or *omitted* (no entry). An omission counts
  as wrong. Leaks can be reported by domain, with `HEALTH`, `LEGAL` and `PERSONAL_FINANCE` shown separately.

### 7.1 `span_text`

The exact words of `utterance` that carry the claim, copied character for character, so a script can check
that it is a substring. Shortest stretch that still contains the subject as it appears in the utterance (a
pronoun, or nothing when context supplies it), the predicate and the value. Leave out hedges and fillers at
the edges ("I think", "um", "you know"). Include a time or place word only when the proposition needs it to be
specific (a past date such as "yesterday", or a place); leave out a present-time word at the edge ("today",
"now", "at the moment"), because the tense already carries it (G4). The D6 criteria note is asked to tolerate span
differences that are only edge words. For a compound row, one span covering all propositions.

### 7.2 `claim_text`

The proposition a checker would look up, as one plain sentence.

- Resolve pronouns and ellipsis from other segments (segment text only, never `setting`): "it's dropped
  below 80" after "Brent's at $82 a barrel" becomes "Brent crude is below USD 80 per barrel on 2025-03-12".
- Anchor time: "today" becomes the call date; "yesterday" the day before; "last quarter" the quarter before
  the call date. A time-of-day word ("this morning", "this afternoon", "overnight", "tonight") is anchored as "on
  the morning of <date>" or the matching part of the day; the span still leaves it out at the edge (G4, G24).
- Numbers as digits, units stated explicitly, currencies as ISO codes ("USD", "GBP/USD"): "one oh eight"
  becomes "1.08"; "quid" becomes "GBP". Add a currency or unit only when the conversation makes it clear;
  otherwise keep it as said and set `confidence_in_label` to `low`. A bare "dollar" or "$" is the US dollar and
  a bare "pound" or "sterling" the pound sterling, as in this guide's examples, unless segment text points to
  another ("an Aussie dollar"); a name that only one traded currency carries (the yen, the won) is clear. Other
  names shared by several traded currencies (krone, peso, franc, rupee, crown) are clear only when segment text
  names the country, or a place in it, as the call's subject at or before that turn; a cue that comes only later,
  or only inside a conditional, still sets the ISO code, at confidence `low` (G40).
- Temperatures (G1): drafts write the scale in the utterance ("-2°C", drafting rule 8). If no segment gives the
  scale, write "(scale not stated)" in `claim_text` and set `confidence_in_label` to `low`, unless the segment's
  own words rule out one scale.
- Normalise names: "the Met" to "the Met Office" when context makes it clear. Colloquial index names mean the
  headline index: "the FTSE" is the FTSE 100, "the Dow" the Dow Jones Industrial Average, "the Nasdaq" the Nasdaq
  Composite, unless segment text points elsewhere (G14).
- A gold, silver, platinum or palladium price said "an ounce" is written "per troy ounce" (G12).
- Keep the value as asserted, even when it is false. Never correct the claim.
- A comparison with an earlier date that gives no value for that date ("about the same as a year ago", "higher
  than last week") stays a comparison, with both dates anchored; it is never turned into a value for the
  earlier date (G41).
- Drop hedges and attributions, except a published forecast or warning (section 5.6): "my manager says the
  pound is at 1.25 against the dollar" becomes "GBP/USD is 1.25 on 2025-03-12".
- Fix an evident transcription slip only when the intended word is unambiguous, say so in the notes, and set
  `confidence_in_label` to `low`.
- Compound: one proposition per part, separated by ` | `.

---

## 8. `is_compound`

`yes` when the row holds two or more propositions that could each be checked on their own. A part that is an
opinion, prediction, request or vague remark does not count as a proposition here, so a non-checkable row is
always `no`. Only asserted predicates count: a presupposition inside a definite description is not a proposition
(G7). Labelled on every row; per row (segment), not per sentence.

| Utterance | `is_compound` |
|---|---|
| "Canberra's the capital and it's got about 400,000 people" | `yes` |
| "the euro's at one oh eight and the pound's at 1.27" | `yes` |
| "gold closed at $2,400 an ounce on Friday" | `no`: one proposition with qualifiers |
| "it's between 20 and 22 degrees in Rome" | `no`: a range is one proposition |
| "Paris is the capital of France, lovely city" | `no`: the second part is an opinion |

When the checkable parts fall in different domains, the row takes whichever part's domain ranks highest in
the section 6 order, and the notes name the other part's domain.

---

## 9. `time_sensitive`

`yes` when the correct answer could change within a month of the call date (the brief's test). Labelled on
every row. A compound row is `yes` if any checkable part is.

| Case | `time_sensitive` |
|---|---|
| A present-tense price, rate, level or weather condition | `yes` |
| A statutory or officially set rate, allowance, threshold or fee (tax rates and bands, allowances, the minimum wage) (G42) | `no`, unless the conversation places a change within a month of the call date |
| A forecast as currently published | `yes` |
| A price, rate or weather reading for a past date or closed period, stated or resolved by 7.2 ("on 3 March", "yesterday") | `no`: the answer for that date no longer changes |
| States, totals and movements over a period still running at the call date ("this week") | `yes` |
| A trend over a trailing window of about a year or more ("in the last few years") (G27) | `no`, unless its subject is a market price or level, which is `yes` |
| A completed event inside a period still running ("earlier this month") (G5) | `no`: it counts as a past date |
| A movement, reading or event stated in the past simple for an earlier part of the call date ("jumped this morning", "rained overnight") (G43) | `no`, as G5; a state or total over a part of the day still running ("has been", "since this morning") is `yes` |
| Typical weather for a whole month or season, or weather mostly beyond the forecast horizon (G9, G35) | `no`: a climate normal; observed weather over a month or season still running follows the rows above |
| Stable reference facts (capitals, dates of history, physical constants) | `no` |
| An office holder ("the current UN Secretary-General is...") | `no`, unless the office is known to change within the month |
| A schedule ("the meeting is next Thursday") | `yes` |
| A request or stated want (G11, G25) | By what it asks about, with the rows above applied to it: a price, rate, level, condition or availability at the present or a future date is `yes`; at a past date or in a closed period it is `no`; a booking want, destination or order with no time-varying value is `no` |

For non-checkable rows, label what the statement is about: "bitcoin's going to crash" is `yes`.

---

## 10. `confidence_in_label`

`high` by default. `low` only when:

- a ruling in this guide says `low` (section 5.6 table; section 6: a row still torn between a high-stakes
  domain and another; section 7.2: a currency or unit not clear from context, an interpreted transcription
  slip); or
- the context needed to resolve the claim is ambiguous; or
- two labels seem equally right.

Low-confidence rows stay in the set. The evaluation can report metrics with and without them; they are not
silently dropped.

---

## 11. Drafting rules (for the AI-drafted conversations)

1. **Synthetic only** (U10). Never copy or paraphrase a real call, including the repository's sample audio.
2. **No real private people.** Speakers are `SPEAKER_00` and `SPEAKER_01`. Any private person mentioned is
   fictional and unnamed, or has a common first name only; so is a public figure shown acting outside their
   public role. No real phone numbers, email addresses, account numbers or street addresses.
3. **Invented names** only for the speakers' own organisations and in out-of-scope rows; prefer distinctive
   coined names. Before a batch is approved, each invented company name is searched by exact name on a
   public company register and on the web; any name that matches or closely resembles a registered business
   is replaced. The result is recorded in the review sheet and the owner confirms it. The same check applies
   to the example name in this guide before it is committed. In-scope `CHECKABLE` rows name only real
   entities, or none.
4. **Real people and companies.** No deliberately false row names a real company, organisation or person, or
   identifies one by implication: any real organisation or person whose records are the only way to check the
   claim counts, named or not (G8). A false row may name a place, landmark or historical event, but not a name
   that also denotes a real organisation or institution (the Houses of Parliament, the White House, the Bank of
   England) (G18); the market indices that this rule allows are the exception;
   false market rows use indices, currencies, commodities or crypto assets, never a named company's share
   price. Real people and real companies appear only in rows that are not deliberately false and state a
   neutral, checkable public fact. They never appear in an opinion, prediction, vague remark or request about
   them, or in a claim about their conduct, results, health, legal matters or private life. Public
   institutions (central banks, weather services, statistics offices) may appear in any row that is not
   deliberately false and is about their published rates, forecasts, warnings or schedules.
5. **High-stakes rows are harmless if acted on.** No `HEALTH`, `LEGAL` or `PERSONAL_FINANCE` row, true or
   not, says that a medicine, dose, combination or treatment is safe or advisable; tells anyone to start,
   stop, skip or combine a medicine or treatment, or to put savings, a pension or borrowed money into a
   particular investment; or says that someone may ignore a legal, tax or debt obligation. Neutral public
   facts (allowances, statutory periods) and harmless advice ("talk to an adviser first") are allowed.
   Out-of-scope rows are never deliberately false (section 4.4).
6. **News rows use fictional or generic events** ("the port strike", "the hauliers' strike"), never invented
   events about real named people or companies, and always carry a time cue. An invented event must not be
   placeable, through any segment, on a real organisation or real local body (a named city's council, a named
   port's operators); if the conversation names a place, the event is not local to it (G17). True published
   rates, forecasts, warnings and schedules of public institutions (rule 4) are not invented events.
7. **Call dates and reference levels.** Call dates fall in the past, within the drafter's knowledge (proposal:
   2025, Q12). For every time-sensitive in-scope `CHECKABLE` row, the drafter records in the private review
   sheet the reference level it used for that date, from its own knowledge and marked unverified (for a
   `PREDICTION` that names a level, optionally the current level) (G23); the owner spot-checks them (Q12). The
   same record is kept for every in-scope `CHECKABLE` row not written to be false that states a date-dependent
   value (a past-dated reading, a past market close); a row whose level the drafter does not know is not
   drafted (G36). A
   deliberately false value lies outside the asset's range over the 12 months before the call date, or outside
   the place's recorded range for that month, but no further beyond the nearest edge than about half the
   range's width: realistic in form, never absurd (G22). A false value is also physically possible at that place
   and date: where a physical limit sets the edge of the range (sunshine longer than the day), any value beyond
   it counts as absurd (G37). A `no` row is written close to the recorded level; a
   `no` row that states a threshold ("over", "below") keeps the recorded level clearly on the stated side for
   the whole day, and otherwise uses "about" (G21). False values are said sincerely (for example
   mis-remembered) and left uncorrected in the call, never as an obvious joke. Realism note, not a rule
   (batch 04 proposal G46, not adopted): a few approved rows have a market professional state a false value
   or leave one uncorrected; their labels are unaffected, and the D6 criteria note may mention it.
8. **ASR style** (a judgement of this guide, not measured; Q14). Fillers ("um", "uh", "you know"), false
   starts, missing punctuation and occasional mild recognition slips that do not change what is claimed,
   because clean prose would overstate extraction recall on real calls. Casing, punctuation and numbers
   follow the pipeline's Whisper output: mostly cased, with lapses, and numbers mostly as digits with symbols
   ("1.08", "$82", "-3°C"). A minority of numbers are spelled out ("one oh eight"); the owner sets the share
   (proposal: about one in five, Q14). A script tells digits from spelled numbers in each utterance, so recall
   can be reported both ways.
9. **Business-call settings**, varied: freight and logistics, travel booking, event venues, software support,
   procurement, a currency-exchange desk, a weather-dependent outdoor business. Each excerpt is chosen for its
   in-scope content. Own-company turns (requests, commitments or statements about the speakers'
   organisation or this call's order, booking or account) appear only where section 12 needs them; otherwise
   the excerpt starts after or ends before the business handling. Small talk is where many general-knowledge
   and weather claims appear naturally. Weather small talk names the place in some segment. In-scope
   `CHECKABLE` weather rows state a number or an observable condition.
10. **English only.** No code-switching.
11. **Each conversation**: 4 to 12 segments, 2 to 5 items, plus filler segments; timestamps increase and match
    the text length at roughly 140 words a minute; synthetic ASR confidence between 0.75 and 0.98. No claim is
    repeated in two items of one conversation. Questions that ask whether a value holds are answered with at
    least a short clause. No row mixes in-scope and out-of-scope parts, checkable or not, and in-scope
    `CHECKABLE` rows carry no high-stakes aside. No item reuses the wording or template of a guide example or an
    earlier batch item (same construction and same kind of subject); each batch varies structure and subjects
    (G20). No phrase, frame or hedge is used only on deliberately false rows or only on rows not written to be
    false. A script reports every two-to-four-word phrase (with "'s" counted as its own token, and phrases made
    only of function words left out) that appears on three or more in-scope `CHECKABLE` rows that are all false
    or all not false; the next batch balances what it reports, and the frozen `v1.0` set has none (G31). The same
    script reports, per in-scope domain, the false and not-false split of each value band (10 °C bands for
    temperatures, counted per row) and each construction (past-dated reading, tag or presupposing question,
    compound) that holds three or more in-scope `CHECKABLE` rows; none is all false or all not false, and false
    values err both above and below the recorded range (G32). A frame carried onto the other truth value to
    balance a G31 report is exempt from G20 for that frame only: the carrier varies everything else, carries
    one balancing frame, and its rationale names it (G39).
12. **Every item and conversation** carries `provenance` `synthetic` and `drafted_by` `claude-draft`.

---

## 12. Target mix (200 items)

The plan's minimums (plan Phase 1: 60 checkable factual, 40 opinion/prediction/vague, 30 compound,
30 time-sensitive, 40 deliberately false) are kept and counted on in-scope rows, since those are the rows
the extraction metrics and D3b's verdict labels use. The plan's code-switched slice is deferred (U2).

| Slice | Rows | Breakdown |
|---|---|---|
| In scope, `CHECKABLE` | 95 | `GENERAL` 37; `MARKET_FINANCE` 34 (currency 9, crypto 9, stock and index 8, commodity 8); `WEATHER` 24 |
| In scope, not checkable | 60 | `OPINION` 16, `PREDICTION` 16, `VAGUE` 15, `REQUEST` 13 |
| Out of scope | 45 | `CHECKABLE`: `OWN_COMPANY` 8, `HEALTH` 6, `LEGAL` 6, `PERSONAL_FINANCE` 6, `NEWS` 6, `OTHER` 4, private individual 4 (domain `GENERAL`, `MARKET_FINANCE` or `WEATHER`, out of scope only through A6, not counted in the domain figures); not checkable 5 |
| **Total** | **200** | |

Cross-cutting minimums, all within in-scope `CHECKABLE` rows: at least 30 compound, at least 30
time-sensitive, and at least 40 deliberately false. Across the 40 false rows, at most about a third are tag or
presupposing questions, and their domains roughly follow the mix above (G22). The owner's check can move rows between slices; batch 4
closes any shortfall. The frozen `v1.0` set never holds fewer than 200 items, or fewer than any plan minimum.

**Batches.**

| Batch | Conversations | Items |
|---|---|---|
| Calibration | 20 | about 60: every in-scope domain and market sub-type, every out-of-scope domain, a private-individual row, every non-checkable category, compound and deliberately false rows |
| 2 | about 15 | about 47 |
| 3 | about 15 | about 47 |
| 4 | as needed | the rest, to reach 200 and every minimum |

---

## 13. Review, consistency check and versioning

**Review.** For each batch the drafter supplies the items and a review sheet (CSV, opens in Excel). One row
per item shows `claim_id`, the conversation, the utterance and every labelled or pre-filled field
(`checkability`, `domain`, `private_individual`, `in_scope`, `is_claim`, `span_text`, `claim_text`,
`is_compound`, `time_sensitive`, `deliberately_false`, `reason_kind`, `confidence_in_label`), the drafter's
one-line rationale, the reference level for time-sensitive rows, an owner column (`OK` or `fix`) and a notes
column. One row per conversation lists its non-item segments with an owner column "no missed items: `OK` or
`fix`", and the company-name check (drafting rule 3). A script flags non-item segments that contain a digit, a
number word or a domain keyword. The owner checks every row. The drafter applies the fixes and re-issues the
batch; the owner approves it.

**Guide changes.** The calibration batch also updates this guide. Each batch records the guide version it
was labelled or last re-checked under (calibration batch `v0.1`: labelled under v0.3, re-checked under v0.4;
set `v0.2`, batches 01 and 02, re-checked under v0.5; set `v0.3`, batches 01 to 03, re-checked under v0.6; set `v0.4`, batches 01 to 04, re-checked under v0.7). When the guide changes, every approved row the change affects is re-checked,
calibration rows included, before `v1.0`. The owner times the first half-day of checking so that the cost
estimate in brief §8 can be corrected.

**Where approved data goes.** Drafts and review sheets stay outside git. An approved batch goes into the
repository under `app/evaluation/gold/` (the brief's location for synthetic rows): `conversations.jsonl`,
`items.csv` (canonical), `items.jsonl` (generated from `items.csv` by a script, never edited by hand), a
`MANIFEST.sha256` covering all three, and a `README.md` that says "Synthetic, unverified, may be deliberately
false; not advice." The set's version is in the directory name: `v0.1` after calibration, `v0.2` and so on per
batch, `v1.0` when frozen. The review columns and notes stay private; the repository copy keeps
`label_status` only. This guide goes into the repository with the first approved batch.

**Consistency check (D3).** When all 200 items are approved and the guide has stopped changing, 40 items are
drawn and the list is kept private. Proposal (Q13): a stratified random draw, so that every field has enough
cases to compare: at least 4 of each non-checkable category, at least 8 in-scope `CHECKABLE` rows including
compound and time-sensitive ones, every private-individual row, and at least one row per out-of-scope
domain. At least seven days later the owner re-labels the 40 from a sheet that shows only the conversation and
the utterance, with every label blank. Reported per field (`checkability`, binary `CHECKABLE`-versus-not,
`domain`, `private_individual`, `is_compound`, `time_sensitive`): raw agreement and Cohen's kappa
(intra-rater) with a bootstrap 95% interval; for rare yes/no fields, the count of positive agreements
instead of kappa. The statistic and the 0.6 threshold come from brief §6, applied intra-rater: kappa below
0.6 on `checkability` means the guide is underspecified, so fix the guide and re-label the affected rows
rather than averaging the disagreement away. Because the draw is stratified, the figures describe the sample,
not the whole set. Two more figures separate anchoring from inconsistency: agreement between the blind
labels and the original AI drafts, and the share of rows the owner fixed (`owner_fixed`).

**Freeze.** The set is frozen and versioned as `v1.0` before any bake-off run, and never edited afterwards;
a correction makes `v1.1`.

---

## 14. Open questions for the owner (settle with this guide)

Each has a proposal. All proposals were accepted by the project owner on 2026-10-23. Q11 and the date-anchor part of Q12 are
recorded here for the D6 criteria note, not settled by this guide.

| # | Question | Proposal |
|---|---|---|
| Q1 | Order of checkability and scope for `reason_kind` | Checkability first (section 4.3). |
| Q2 | Out-of-scope rows "labelled for Layer B only" | Read as: `is_claim`, `span_text` and `claim_text` blank on them; extraction recall on in-scope rows; Layer B scored on every row (section 7). Filling Layer A on them would need the signed D3/D7 text amended. |
| Q3 | What makes a segment an item | Every statement or subject-matter request or question; greetings, thanks, acknowledgements (including agreement with a stated value), call management and contentless small talk are not items (section 2). |
| Q4 | Plan minimums counted on in-scope rows | Yes, with the mix in section 12. |
| Q5 | `NEWS` window | Events the conversation places within 30 days of the call date, decided from the conversation only (section 6.2). |
| Q6 | Domain readings and boundary rulings | Stock-index levels and predictions and opinions about market prices are `MARKET_FINANCE`; `PERSONAL_FINANCE` uses D9 clause 1's full definition, so neutral tax, pension and mortgage facts are out of scope, not only advice; policy rates, official statistics, company results and scheduled events by time cue; currency-desk and retail-price rulings; "crypto is just gambling" and currency-timing advice are `MARKET_FINANCE`, investment advice is `PERSONAL_FINANCE` (sections 6.1 and 6.2). |
| Q7 | Schema changes from the brief | New: `conversations.jsonl` with `call_date`, `setting`, `language`, `guide_version` and `segments` (4.1); `span_text`, `private_individual`, `in_scope`, `reason_kind`, `provenance`, `drafted_by`, `label_status`. Changed: `domain` values, `is_claim` derived and blank on out-of-scope rows, `claim_text` parts, `is_compound` per row; `syn_` source ids; `annotator` replaced by `label_status` (section 4). |
| Q8 | Files and versions in the repository | `app/evaluation/gold/v0.N/`, `v1.0` at freeze; this guide at `DOCS/GOLD-SET-LABELLING-GUIDE.md`, committed with the first approved batch (section 13). |
| Q9 | Who is a private individual | Anyone who is not a public figure acting in their public role; on non-checkable rows, only when the topic is a private person's life (section 6.3). |
| Q10 | Domain precedence | `PERSONAL_FINANCE`, `HEALTH`, `LEGAL`, then `OWN_COMPANY`, then `MARKET_FINANCE`/`WEATHER`, then `NEWS`, `GENERAL`, `OTHER`; fail closed only when still torn after the definitions, with confidence `low` (section 6). |
| Q11 | How model output is matched to items (by `segmentIds`; a segment that yields both a claim and a `nonCheckable` entry; compound rows per part or per row; correction pairs) | For the harness and the D6 criteria note before the first bake-off run. Drafts avoid repeated claims, so de-duplication should not arise; any repeat or correction pair that slips through is flagged in the notes. |
| Q12 | Call dates, reference levels, date anchors | Settled here: call dates in 2025; the drafter records reference levels from its own knowledge, marked unverified, and the owner spot-checks them (the cheapest firmer rule: check each deliberately false row's 12-month range from a public source). For the D6 criteria note: the v2 request carries no date, so either date anchors are ignored when comparing `claim_text`, or the v2 request gains an optional reference-date field, an additive contract change that needs the owner's signature. The labels support either. |
| Q13 | How the 40 re-label items are drawn | Stratified random draw (section 13), not a plain random draw, which could miss whole categories. |
| Q14 | ASR style and the share of spelled-out numbers | Adopt the style in drafting rule 8 as this guide's judgement; spelled-out numbers about one in five. If the owner prefers to attribute the style to D3, it is recorded in DECISIONS #52 before commit. |
| Q15 | Drafting with the AI assistant and PHASE-0 §6 (W6a: gold-set rows go to the model server "and nowhere else") | W6a governs transcript content at run time. Synthetic rows drafted and revised with the drafting assistant are outside it (D3, U10). Bake-off runs and any scoring or matching step use only the host's model server, never a hosted model. If confirmed, a one-line note is added to PHASE-0 §6 with the owner's sign-off. |
| Q16 | Optional: blind check of item selection | Not proposed. The per-conversation "no missed items" column covers it. If wanted, about 10 non-item segments are added to the blind sheet and reported separately. |

---

## 15. Rulings added in v0.4 to v0.7

The calibration batch was labelled twice, blind, by two independent agents that had not seen the draft labels; a
judge ruled on every disagreement. Checkability agreed on every item. Ten cases where v0.3 did not settle the
label became these rulings, each approved by the project owner on 2026-10-23 and written into the sections named.

| # | Ruling | Section |
|---|---|---|
| G1 | Drafts write the temperature scale; with no scale given, "(scale not stated)" and confidence `low`. | 7.2 |
| G2 | A future state with a word of degree is `PREDICTION`; a tag question that states no value takes its statement's label. | 5.6 |
| G3 | A stated need or want addressed to someone who can meet it is `REQUEST`. | 5.6 |
| G4 | Spans leave out present-time edge words and keep past dates and places. | 7.1 |
| G5 | A completed event inside a running period is a past date for `time_sensitive`. | 9 |
| G6 | A predicted policy-rate change, statistic or company result is `GENERAL` unless part of a current story. | 6.2 |
| G7 | Only asserted predicates count as propositions; presuppositions in definite descriptions do not. | 6, 8 |
| G8 | Drafting rule 4 also covers a real organisation or person identified by implication. | 11 |
| G9 | Weather for a whole month or season, or beyond about 14 days, is a climate normal: `GENERAL`. | 6.1, 9 |
| G10 | The desk's timing advice is `OWN_COMPANY` only when it names the desk's product or quote. | 6.2 |

Batch 02 was checked the same way (checkability agreed on every item). These thirteen rulings were approved by the
project owner on 2026-10-23 for v0.5.

| # | Ruling | Section |
|---|---|---|
| G11 | A request or stated want is time-sensitive by what it asks about. | 9 |
| G12 | Precious-metal prices said "an ounce" are written "per troy ounce". | 7.2 |
| G13 | An answer word plus a clause to a whether-question is a part of its own unless the clause restates it. | 5.6 |
| G14 | Colloquial index names mean the headline index. | 7.2 |
| G15 | Scheduled-event markers are examples; an announced change stated as fact with a date is a schedule. | 5.6 |
| G16 | A law or regulation coming into force is `LEGAL`, confidence `low`. | 6.2, 11 |
| G17 | An invented news event is not placeable on a real organisation or local body. | 11 |
| G18 | A false row does not use a name that also denotes a real institution. | 11 |
| G19 | A topic-only call opener is call management; one stating a specific want is a `REQUEST` item. | 2 |
| G20 | No item reuses a guide example's or earlier item's wording or template. | 11 |
| G21 | A threshold on a `no` row stays clearly on its side all day; otherwise "about". | 11 |
| G22 | False values within half a range-width of the range; at most a third of false rows are tag questions. | 11, 12 |
| G23 | Reference levels are recorded for time-sensitive in-scope `CHECKABLE` rows. | 11 |

Batch 03 was checked the same way (checkability agreed on every item), then rechecked three times. These eight
rulings were approved by the project owner on 2026-10-24 for v0.6.

| # | Ruling | Section |
|---|---|---|
| G24 | A time-of-day word is anchored as the matching part of the day. | 7.2 |
| G25 | A request about a past-dated value is not time-sensitive. | 9 |
| G26 | A performance word about an asset's unmeasured movement is `VAGUE`; a judgement against expectations is `OPINION`. | 5 (rule 3) |
| G27 | A trend over a year or more is not time-sensitive, unless its subject is a market price or level. | 9 |
| G28 | Every predicted event, weather consequences included, is `GENERAL` unless part of a current news story. | 6.2 |
| G29 | A date fixed by a calendar rule is a reference fact, not a schedule. | 5.6 |
| G30 | An event about a third-party product, service or local business is `OTHER` whatever its date. | 6.2 |
| G31 | No phrase, frame or hedge marks false rows or rows not written to be false; a script checks it. | 11 |

Batch 04 was checked in four rounds: a blind double re-label, a rules check and a judge, and from the second
round two skeptics on every proposed change. These fourteen rulings were approved by the project owner on
2026-10-24 for v0.7. Proposal G46 (a speaker whose job is to know a value never states a false one or leaves
it uncorrected) was not adopted; it is kept as the realism note in drafting rule 7.

| # | Ruling | Section |
|---|---|---|
| G32 | No value band or construction is all false or all not false; false values err both ways. | 11 |
| G33 | A population statistic that names no condition or service is official statistics, not `HEALTH`. | 6.2 |
| G34 | A metaphor of an asset's standing is `OPINION`; a metaphor of movement is `VAGUE`. | 5 (rule 3) |
| G35 | Observed weather over a running or recent month or season is `WEATHER`. | 6.1, 9 |
| G36 | Every not-false row with a date-dependent value has a reference level; with none known, it is not drafted. | 11 |
| G37 | A false value is physically possible. | 11 |
| G38 | A rule or rate that differs by jurisdiction is `CHECKABLE` only when the jurisdiction is named or clear. | 5.6 |
| G39 | A frame carried to balance a G31 report is exempt from G20 for that frame only. | 11 |
| G40 | A name shared by several traded currencies gets its ISO code at `high` only with a country cue at or before the turn; a bare dollar or pound is USD or GBP. | 7.2 |
| G41 | A comparison with an earlier date stays a comparison in `claim_text`. | 7.2 |
| G42 | Statutory rates and allowances are not time-sensitive unless a change is placed within a month. | 9 |
| G43 | A past-simple movement earlier on the call date is not time-sensitive. | 9 |
| G44 | A reading is a record only when the conversation presents it as one. | 6.1 |
| G45 | A closing line that only repeats a detail already given is a goodbye. | 2 |
