"""Claim fidelity and per-proposition recall (bake-off harness; open point AMB-4, ruled before the first run).

Implements the two reported-only metrics of the signed D6 criteria note S2 §5.2 as the project owner ruled them on
2026-11-18 (S3 is to state the same definitions under §5.2):

- Values: numbers (digit runs with an optional decimal part), ISO 4217 currency codes on `ISO_4217_CODES` (a pair
  BASE/QUOTE is its two codes) and the three headline index names of the labelling guide's section 7.2. Units other
  than codes, and signs, are not compared; crypto names and other index names are not values.
- One normaliser over the gold part and the model's claim, in this order: ISO dates (YYYY-MM-DD) are removed, which
  is how §5.1's date-anchor rule applies (only ISO dates are ignored); full index names, in any ASCII letter case and
  with whitespace or a hyphen between their words, are read and removed, digits included; a leftover FTSE, Dow or
  Nasdaq, in any ASCII letter case, not followed by a token starting with a digit (a hyphen before the digit counts
  like whitespace: 'FTSE-100' is the FTSE 100, 'Nasdaq-100' is the number 100) is read as the full name; a thousands
  comma is removed; currency codes; numbers, compared by exact value. Nothing else is converted.
- Parts: on a row with is_compound yes, claim_text splits on the exact separator ' | ' into trimmed parts; otherwise
  the row is one part.
- Matching (§5.1): found rows are exactly `scoring`'s; a matched claim is every claim whose segmentIds hold the row's
  segment_id, finding or duplicate. A row passes, and a part is found, when one matched claim holds every value;
  values are never pooled across claims. A row or part with no value is not judgeable.
- §5.3: both metrics again over rows with confidence_in_label high, matching unchanged (as scoring's AMB-21).
- Outputs (§5.3): what `score_run` takes; a failed conversation scores as empty output.

This is the only module that reads claim text: the gold row's `claim_text` and the model claim's `text`, nothing
else. Every ratio is an exact `Fraction` from `scoring._ratio`, None on a zero denominator. Nothing here calls a
model or the network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal

from app.evaluation.bakeoff.extraction import Extraction, ExtractionResult
from app.evaluation.bakeoff.gold import GoldItem, GoldSet
from app.evaluation.bakeoff.scoring import _check_results, _ratio, score_conversation

ValueKind = Literal["number", "code", "index"]

SEPARATOR = " | "
# The labelling guide's three headline index names (DOCS/GOLD-SET-LABELLING-GUIDE.md section 7.2, lines 460-462).
INDEX_NAMES = ("FTSE 100", "Dow Jones Industrial Average", "Nasdaq Composite")
# ISO 4217 List One alphabetic codes as known without a network source: currencies, funds codes (BOV, CHE, CHW, CLF,
# COU, MXV, USN, UYI, UYW) and the X codes (precious metals, SDR, bond-market units, XSU, XUA, XTS, XXX); withdrawn
# codes are left out. The project owner reviews this list (AMB-4).
ISO_4217_CODES: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE
    CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ
    GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD
    MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG
    QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD
    TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU
    XTS XUA XXX YER ZAR ZMW ZWG ZWL
    """.split()
)

# ASCII classes only, so Unicode digits and letters are never values; `\s` is Unicode whitespace. Letter case comes
# from explicit ASCII classes ([Ff][Tt][Ss][Ee]), never a flag, so no non-ASCII letter folds into a name. Only the
# ASCII hyphen-minus counts as a hyphen. Matches are only substituted or listed (sub, subn, findall), never located.
_DATE = re.compile(r"(?<![0-9])[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])(?![0-9])")
_EDGE = "(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"
_JOIN = r"(?:\s+|\s*-\s*)"  # between the words of a full name: whitespace, or a hyphen (AMB4C-1)
_DIGIT_NEXT = r"(?!\s*(?:-\s*)?[0-9])"  # a leftover word whose next token starts with a digit is not the index


def _caseless(word: str) -> str:
    """`word` in any ASCII letter case, as explicit classes: 'Dow' gives '[Dd][Oo][Ww]' (AMB4C-2, AMB4C-7)."""
    return "".join(f"[{c.upper()}{c.lower()}]" if c.isascii() and c.isalpha() else re.escape(c) for c in word)


_FULL_NAMES = tuple((name, re.compile(_EDGE.format(_JOIN.join(map(_caseless, name.split()))))) for name in INDEX_NAMES)
_SHORT_NAMES = tuple((name, re.compile(_EDGE.format(_caseless(name.split()[0])) + _DIGIT_NEXT)) for name in INDEX_NAMES)
_THOUSANDS = re.compile(r"(?<=[0-9]),(?=[0-9]{3}(?![0-9]))")
_UPPER_RUN = re.compile(r"(?<![A-Z])[A-Z]+")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True, slots=True)
class Value:
    """One value (AMB-4). `value` is a Decimal for a number, the code for a code, the index's full name for an index;
    values of different kinds never compare equal."""

    kind: ValueKind
    value: Decimal | str


@dataclass(frozen=True, slots=True)
class FidelityFigures:
    """S2 §5.2 claim fidelity and per-proposition recall over one set of rows, as ruled (AMB-4). Reported only, never
    gated. Each ratio keeps its raw numerator and denominator beside it, because a `Fraction` reduces."""

    found_rows: int  # found in-scope CHECKABLE rows (scoring's found_rows), judgeable or not
    rows_not_judgeable: int  # found rows with no value
    rows_passing: int  # claim fidelity numerator
    rows_judgeable: int  # claim fidelity denominator = found_rows - rows_not_judgeable
    claim_fidelity: Fraction | None
    compound_rows: int  # in-scope CHECKABLE rows with is_compound True
    compound_rows_found: int
    parts_not_judgeable: int  # parts of compound rows, found or not, with no value
    parts_found: int  # per-proposition recall numerator
    parts_judgeable: int  # per-proposition recall denominator
    per_proposition_recall: Fraction | None
    flag_separator_disagreements: int  # in-scope CHECKABLE rows where is_compound != (SEPARATOR in claim_text)


@dataclass(frozen=True, slots=True)
class FidelityMetrics(FidelityFigures):
    """The figures for one run over all rows, with the same figures over rows with confidence_in_label high beside
    them (S2 §5.3; AMB4C-3), as `RunMetrics.high_confidence` sits beside scoring's figures."""

    high_confidence: FidelityFigures


@dataclass(frozen=True, slots=True)
class _Row:
    """One in-scope CHECKABLE row, judged once on all rows; the high-confidence figures reuse it (AMB-21)."""

    high: bool
    found: bool  # in scoring's found rows
    judgeable: bool  # the row holds a value
    passes: bool  # one matched claim holds every value of the row
    compound: bool
    parts: tuple[bool | None, ...]  # per part: None when it holds no value, else whether it is found
    disagrees: bool


def text_values(text: str) -> frozenset[Value]:
    """The values of one text under the ruled normaliser, in the ruled order (S2 §5.2; AMB-4)."""
    found: set[Value] = set()
    text = _DATE.sub(" ", text)  # 1: ISO dates, anchor or not (AMB4-3)
    for names in (_FULL_NAMES, _SHORT_NAMES):  # 2: full names, digits included; 3: a leftover word
        for name, pattern in names:
            text, count = pattern.subn(" ", text)
            if count:
                found.add(Value("index", name))
    text = _THOUSANDS.sub("", text)  # 4: one pass, so 1,234,567 reads as 1234567
    found.update(Value("code", run) for run in _UPPER_RUN.findall(text) if run in ISO_4217_CODES)  # 5
    found.update(Value("number", Decimal(digits)) for digits in _NUMBER.findall(text))  # 6: exact, never a float
    return frozenset(found)


def claim_parts(item: GoldItem) -> tuple[tuple[str, ...], bool]:
    """A row's parts (AMB-4): on is_compound yes, claim_text split on the exact separator ' | ' into trimmed pieces;
    otherwise one part. The flag is True when is_compound and the separator disagree."""
    text = item.claim_text  # the module's only read of claim_text
    pieces = text.split(SEPARATOR) if item.is_compound else [text]
    return tuple(piece.strip() for piece in pieces), (SEPARATOR in text) != item.is_compound


def score_fidelity(gold: GoldSet, results: Mapping[str, ExtractionResult]) -> FidelityMetrics:
    """S2 §5.2 claim fidelity and per-proposition recall for one run, over the same results `score_run` takes:
    single-conversation outputs, one per gold conversation; a failed conversation is empty output (§5.3). The
    high-confidence figures restrict the rows judged here to confidence_in_label high: matching is unchanged."""
    _check_results(gold, results)  # score_run's contract: one result per conversation, no cap-session id
    rows: list[_Row] = []
    for source_id in gold.conversation_ids():
        result = results[source_id]
        # Found rows are exactly score_run's (flagged_groups empty, as gates passes them); a failure finds none.
        found = score_conversation(gold, result).found_rows
        claims = result.claims if isinstance(result, Extraction) else ()
        # Every claim is normalised whole, never split; nonCheckable[] text is never read.
        claim_values = [(claim.segment_ids, text_values(claim.text)) for claim in claims]
        for item in gold.items_for(source_id):
            if not item.is_in_scope_checkable:  # blank reason kind, as score_run's in_scope_checkable()
                continue
            parts, disagrees = claim_parts(item)
            part_values = [text_values(part) for part in parts]
            row_values = frozenset(value for values in part_values for value in values)
            # Matched claims cite the row's segment; on an in-scope CHECKABLE row each is a finding or a duplicate.
            matched = [values for segment_ids, values in claim_values if item.segment_id in segment_ids]
            rows.append(
                _Row(
                    high=item.confidence_in_label == "high",
                    found=item.key in found,
                    judgeable=bool(row_values),
                    passes=any(row_values <= values for values in matched),  # one claim holds every value
                    compound=item.is_compound,
                    parts=tuple(any(v <= values for values in matched) if v else None for v in part_values),
                    disagrees=disagrees,
                )
            )
    return FidelityMetrics(**_figures(rows), high_confidence=FidelityFigures(**_figures([r for r in rows if r.high])))


def _figures(rows: Sequence[_Row]) -> dict[str, Any]:
    """The `FidelityFigures` fields over `rows`; only found rows count for claim fidelity, and per-proposition recall
    counts every part of every compound row, found or not (AMB4-6, AMB4-7)."""
    judged = [row for row in rows if row.found and row.judgeable]
    found_rows = sum(row.found for row in rows)
    passing = sum(row.passes for row in judged)
    parts = [part for row in rows if row.compound for part in row.parts]
    judged_parts = [part for part in parts if part is not None]
    parts_found = sum(judged_parts)
    return {
        "found_rows": found_rows,
        "rows_not_judgeable": found_rows - len(judged),
        "rows_passing": passing,
        "rows_judgeable": len(judged),
        "claim_fidelity": _ratio(passing, len(judged)),
        "compound_rows": sum(row.compound for row in rows),
        "compound_rows_found": sum(row.compound and row.found for row in rows),
        "parts_not_judgeable": len(parts) - len(judged_parts),
        "parts_found": parts_found,
        "parts_judgeable": len(judged_parts),
        "per_proposition_recall": _ratio(parts_found, len(judged_parts)),
        "flag_separator_disagreements": sum(row.disagrees for row in rows),
    }
