# Fact-Check Agent gold set

**Synthetic, unverified, may be deliberately false; not advice.**

Evaluation data for the Sprint 7 Fact-Check Agent's Phase 1 model bake-off: claim extraction (Layer A) and
checkability and scope (Layer B), as scoped by [D3-GOLD-SET-BRIEF.md](../../../DOCS/D3-GOLD-SET-BRIEF.md) Option A.
Labelled under [GOLD-SET-LABELLING-GUIDE.md](../../../DOCS/GOLD-SET-LABELLING-GUIDE.md). There are no verdict labels:
those belong to register row D3b, after Phase 4.

- **Synthetic only** (U10). No real call content. Every conversation and every label was drafted by an AI assistant
  (`drafted_by: claude-draft`) and checked by the project owner (`label_status: owner_ok`).
- **English only** in this version (U2).
- **`deliberately_false: yes`** marks a claim written to be false at its call date. `no` means it was not written to
  be false; it does not mean true. Nothing here is a verdict.
- **Real people and companies** appear only in neutral public facts that are not deliberately false. Public
  institutions appear only for their published rates, forecasts, warnings and schedules.

## Versions

Each directory holds the whole set at that version. The set is frozen as `v1.0` before any bake-off run and never
edited afterwards; a correction makes a new version.

| Version | Contents | Guide |
|---|---|---|
| `v0.1` | Calibration batch: 20 conversations, 65 items | labelled under v0.3, re-checked under v0.4 |
| `v0.2` | Batches 01 and 02: 35 conversations, 116 items (`gs_0001`-`gs_0116`) | batch 02 labelled under v0.4; both re-checked under v0.5 |
| `v0.3` | Batches 01 to 03: 53 conversations, 166 items (`gs_0001`-`gs_0166`) | batch 03 labelled under v0.5; all re-checked under v0.6 |
| `v0.4` | Batches 01 to 04: 63 conversations, 200 items (`gs_0001`-`gs_0200`) | batch 04 labelled under v0.6; all re-checked under v0.7 |

From `v0.1` to `v0.2`, three deliberately false values in batch 01 moved closer to their range, following ruling G22
(a false value lies outside the range but within half its width): silver USD 12 to 20 (`gs_0020`), USD/JPY 110 to
132 (`gs_0023`), and the Dow 30,000 to 34,000 (`gs_0057`). No other batch 01 row changed. From `v0.2` to `v0.3` no batch 01 or 02 item changed; only each conversation's
`guide_version` moved to v0.6.

From `v0.3` to `v0.4`, the re-check under v0.7 changed nine approved items. Ruling G36 (a row not written to be false
that states a date-dependent value needs a known reference level for its date) reached the weather rows whose recorded
level was only climatology or recollection. Public airport observations (METAR) and a reanalysis were checked for
each call date, and every row the record contradicted was corrected:

- `gs_0004` (Glasgow): -2 °C and frost became 7 °C under a clear sky, and the question before it (`gs_0003`)
  became an open question.
- `gs_0067` (Reykjavik): about 3 °C became about 1 °C.
- `gs_0091` (Belfast): 6 °C became 4 °C, the temperature when the drizzle fell.
- `gs_0114` (Edinburgh): 11 °C became 16 °C.
- `gs_0166` (Galway): the gust went from about 40 to about 30 km/h, and `syn_053` moved from 2025-10-17, when no rain
  fell, to 2025-11-07.
- `gs_0150` (Manchester fog): `syn_047` moved from 2025-11-10 to 2025-02-28, a foggy morning.
- `gs_0075`: a published forecast that could not be checked became the observed Cardiff high for the day before.

Ruling G38 (a rule that differs by jurisdiction needs the jurisdiction in the conversation) added "UK" to `gs_0152`.
Every conversation's `guide_version` moved to v0.7.

## Files

| File | What it is |
|---|---|
| `conversations.jsonl` | One conversation per line: `source_id`, `call_date`, `setting`, `language`, `provenance`, `drafted_by`, `guide_version`, and `segments` (`segment_id`, `speaker`, `start`, `end`, `text`, `confidence`). |
| `items.csv` | The canonical item table, one row per item (guide section 4.2). |
| `items.jsonl` | Generated from `items.csv` for the evaluation harness; never edited by hand. |
| `MANIFEST.sha256` | SHA-256 of the three files above. |

The hashes are of the files as stored in git, with LF line endings. Check them from a clone with
`git show HEAD:app/evaluation/gold/v0.4/items.csv | sha256sum`, or run `sha256sum -c MANIFEST.sha256` in a checkout
that keeps LF endings. A Windows checkout with `core.autocrlf=true` writes CRLF and will not match.

## v0.4 in numbers

| Slice | Items |
|---|---|
| In scope, `CHECKABLE` | 95: `GENERAL` 37, `MARKET_FINANCE` 34 (currency 9, crypto 9, stock and index 8, commodity 8), `WEATHER` 24 (31 compound, 49 time-sensitive, 40 deliberately false) |
| In scope, not checkable | 60: `OPINION` 16, `PREDICTION` 16, `VAGUE` 15, `REQUEST` 13 |
| Out of scope | 45. `CHECKABLE` 40: `OWN_COMPANY` 8, `NEWS` 6 (one about a private individual), `PERSONAL_FINANCE` 6, `HEALTH` 6, `LEGAL` 6, `OTHER` 4, and four claims about a private individual (three `MARKET_FINANCE`, one `GENERAL`). Not checkable 5: two own-company requests, an own-company prediction, a personal-finance opinion and a vague remark about third-party accommodation |
| `confidence_in_label` `low` | 27 |

The set has reached 200 items and every minimum in guide section 12. The guide's checks on `v0.4` find no phrase on
three or more in-scope `CHECKABLE` rows that are all deliberately false or all not (ruling G31, also with numbers
normalised). Within each domain, no temperature band, past-dated reading, tag question or compound pattern is all one
way (G32). The reference levels of the weather rows re-checked under G36 come from public airport observations and a
reanalysis, and are kept with the private review sheets. Next: a stratified blind re-label of 40 items, at least seven days after this approval, then the freeze
as `v1.0`.
