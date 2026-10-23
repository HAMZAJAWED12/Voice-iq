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

## Files

| File | What it is |
|---|---|
| `conversations.jsonl` | One conversation per line: `source_id`, `call_date`, `setting`, `language`, `provenance`, `drafted_by`, `guide_version`, and `segments` (`segment_id`, `speaker`, `start`, `end`, `text`, `confidence`). |
| `items.csv` | The canonical item table, one row per item (guide section 4.2). |
| `items.jsonl` | Generated from `items.csv` for the evaluation harness; never edited by hand. |
| `MANIFEST.sha256` | SHA-256 of the three files above. |

The hashes are of the files as stored in git, with LF line endings. Check them from a clone with
`git show HEAD:app/evaluation/gold/v0.1/items.csv | sha256sum`, or run `sha256sum -c MANIFEST.sha256` in a checkout
that keeps LF endings. A Windows checkout with `core.autocrlf=true` writes CRLF and will not match.

## v0.1 in numbers

| Slice | Items |
|---|---|
| In scope, `CHECKABLE` | 31: `GENERAL` 12, `MARKET_FINANCE` 12, `WEATHER` 7 (8 compound, 16 time-sensitive, 12 deliberately false) |
| In scope, not checkable | 18: `OPINION` 5, `PREDICTION` 5, `VAGUE` 4, `REQUEST` 4 |
| Out of scope | 16. `CHECKABLE` 14: `OWN_COMPANY` 3, `NEWS` 3 (one about a private individual), `PERSONAL_FINANCE` 2, `HEALTH` 2, `LEGAL` 2, `OTHER` 1, and one `MARKET_FINANCE` claim about a private individual. Not checkable 2: an own-company request and a personal-finance opinion |
| `confidence_in_label` `low` | 10 |
