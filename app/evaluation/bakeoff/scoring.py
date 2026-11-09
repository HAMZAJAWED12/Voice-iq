"""Score extraction outputs against the gold set (bake-off harness H1).

Implements, from the signed D6 criteria note S2:

- §5.1, matching. The unit is the segment: a model claim matches a gold row when the row's `segment_id` is in the
  claim's `segmentIds`, within its own conversation. Each in-scope `CHECKABLE` row is found at most once; a later
  claim whose in-scope `CHECKABLE` rows were all found already is a duplicate, dropped from both terms of precision.
  A claim that cites an in-scope `CHECKABLE` row predicts `CHECKABLE` for those rows only, and every other row it
  cites is a neutral context citation, scored from `nonCheckable[]` alone and never a leak. Correction pairs change
  nothing; a flagged repeat is matched once; flagged groups are reported separately. Spans and claim text are never
  compared, so date anchors are ignored.
- §5.2, metrics. Recall, precision and F1; false positive against leak; Layer B routing accuracy over every row, a
  claim winning a shared segment only for the rows it predicts `CHECKABLE`, and the gold order checkability first,
  then scope; checkability binary F1 and six-class macro F1 (reported); leaks counted per row, over in-scope
  non-checkable rows and over out-of-scope rows; high-stakes leaks per domain.
- §5.3, failures and rows used. A request that failed after its retry scores as empty output: its rows are not found
  and are omitted, and failed conversations are listed with their out-of-scope rows. Gates use all rows, with
  high-confidence-only figures beside them; recall is also reported for utterances with digits and with spelled
  numbers; `deliberately_false` is never used (the gold loader drops it).
- §6, gates A and S, on exact fractions.

Claim fidelity and per-proposition recall (§5.2, reported only) wait for the owner's definitions (spec AMB-4). §9
reports every section 5 metric and the note fixes the scoring before any result exists, so that ruling is needed
before the first run. Every ratio is an exact `Fraction`; a ratio with a zero denominator is None and fails any check
on it. Nothing here calls a model or the network (§3.12: gold rows never leave the host, scoring included).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Literal, NoReturn, TypeAlias, TypeVar

from app.evaluation.bakeoff.extraction import (
    FAILURE_KINDS,
    Extraction,
    ExtractionFailure,
    ExtractionResult,
    FailureKind,
)
from app.evaluation.bakeoff.gold import HIGH_STAKES_ORDER, OUT_OF_SCOPE_BUCKETS, REASON_KIND_CLASSES, GoldItem, GoldSet
from app.evaluation.bakeoff.stats import (
    HIGH_STAKES_LEAK_MAX,
    OOS_LEAK_RATE_MAX,
    PRECISION_FLOOR,
    RECALL_FLOOR,
    RECALL_TARGET,
    ROUTING_FLOOR,
    ConversationCounts,
    RunSummary,
)

RowKey: TypeAlias = tuple[str, str]
ClaimKind = Literal["finding", "duplicate", "false_positive", "leak_only"]
RoutingOutcome = Literal["correct", "leaked", "misrouted", "omitted"]
PredictedLabel = Literal["", "opinion", "prediction", "request", "vague", "outOfScope", "omitted"]
FlagKind = Literal["correction", "repeat"]

FLAG_KINDS = ("correction", "repeat")
SPELLED_NUMBER_WORDS = frozenset(
    {
        "zero",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "trillion",
    }
)
FIDELITY_PENDING = (
    "claim fidelity / per-proposition recall: definitions pending the owner (criteria note S2 §5.2); "
    "open point AMB-4"
)

_DIGIT = re.compile(r"[0-9]")
_WORD = re.compile(r"[a-z]+")
_Unit: TypeAlias = frozenset[RowKey]
_K = TypeVar("_K")
_V = TypeVar("_V")


def _frozen(mapping: Mapping[_K, _V]) -> Mapping[_K, _V]:
    return mapping if isinstance(mapping, MappingProxyType) else MappingProxyType(dict(mapping))


def _ratio(numerator: int, denominator: int) -> Fraction | None:
    """An exact ratio; None when the denominator is 0 (spec C1, AMB-15)."""
    return None if denominator == 0 else Fraction(numerator, denominator)


def _f1(tp: int, fp: int, fn: int) -> Fraction | None:
    """2TP / (2TP + FP + FN): equal to 2PR / (P + R) when both are defined and TP > 0; 0 when TP is 0 and the
    denominator is positive; None when the denominator is 0."""
    denominator = 2 * tp + fp + fn
    return None if denominator == 0 else Fraction(2 * tp, denominator)


@dataclass(frozen=True, slots=True)
class FlaggedGroup:
    """A correction pair or a flagged repeat from the gold notes (S2 §5.1). v0.4 has no notes field, so groups are an
    explicit input, empty by default (spec AMB-2)."""

    kind: FlagKind
    claim_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        # NEEDS-OWNER(AMB-2): flags have no source in v0.4/v1.0; repeat = one recall unit
        if self.kind not in FLAG_KINDS:
            raise ValueError(f"FlaggedGroup.kind must be one of {FLAG_KINDS}, not {self.kind!r}")
        if not isinstance(self.claim_ids, tuple) or not all(isinstance(cid, str) for cid in self.claim_ids):
            raise ValueError("FlaggedGroup.claim_ids must be a tuple of claim_ids")
        if len(set(self.claim_ids)) < 2 or len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("FlaggedGroup needs 2 or more distinct claim_ids")


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """One claim's class (§5.1, §5.2): `predicted` are the rows it predicts `CHECKABLE`, `neutral` its context rows,
    `non_item_segment_ids` the non-item segments it cites, `new_units` the units it found first."""

    index: int
    kind: ClaimKind
    predicted: frozenset[RowKey]
    neutral: frozenset[RowKey]
    non_item_segment_ids: frozenset[str]
    new_units: int


@dataclass(frozen=True, slots=True)
class ConversationScore:
    source_id: str
    failure: FailureKind | None
    claim_outcomes: tuple[ClaimOutcome, ...]
    found_rows: frozenset[RowKey]
    leaked_rows: frozenset[RowKey]
    labels: Mapping[RowKey, PredictedLabel]
    routing: Mapping[RowKey, RoutingOutcome]
    counts: ConversationCounts

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", _frozen(self.labels))
        object.__setattr__(self, "routing", _frozen(self.routing))


@dataclass(frozen=True, slots=True)
class FailedConversation:
    """S2 §5.3: a failed conversation, with the sorted claim_ids of the out-of-scope rows it held."""

    source_id: str
    kind: FailureKind
    out_of_scope_claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FlaggedGroupStatus:
    """S2 §5.1: flagged pairs are reported separately. `found[claim_id]` is True when that row's own segment was cited
    with the row predicted `CHECKABLE`; `unit_found` is set for repeat groups only."""

    kind: str
    claim_ids: tuple[str, ...]
    found: Mapping[str, bool]
    unit_found: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "found", _frozen(self.found))


@dataclass(frozen=True, slots=True)
class SliceRecall:
    rows: int
    found: int
    recall: Fraction | None


@dataclass(frozen=True, slots=True)
class HighConfidenceMetrics:
    """S2 §5.3: the row-based figures again over rows with `confidence_in_label == "high"`. Reported, never gated."""

    n_checkable_units: int
    found_units: int
    fp_claims: int
    recall: Fraction | None
    precision: Fraction | None
    f1: Fraction | None
    n_rows: int
    routing_correct: int
    routing_accuracy: Fraction | None
    checkability_binary_f1: Fraction | None
    checkability_class_f1: Mapping[str, Fraction | None]
    checkability_macro_f1: Fraction | None
    n_in_scope_non_checkable: int
    leaked_in_scope_non_checkable: tuple[str, ...]
    in_scope_non_checkable_leak_rate: Fraction | None
    n_out_of_scope: int
    leaked_out_of_scope: tuple[str, ...]
    out_of_scope_leak_rate: Fraction | None
    n_high_stakes: int
    high_stakes_leaks: int
    high_stakes_leaks_by_domain: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkability_class_f1", _frozen(self.checkability_class_f1))
        object.__setattr__(self, "high_stakes_leaks_by_domain", _frozen(self.high_stakes_leaks_by_domain))


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """Every S2 §5 figure for one run (run 1, single-conversation outputs: S2 §3.13)."""

    # Extraction (§5.2)
    n_checkable_units: int
    found_units: int
    fp_claims: int
    recall: Fraction | None
    precision: Fraction | None
    f1: Fraction | None
    recall_target_met: bool | None
    # Claim accounting (§5.1, §9); raw = finding + duplicate + false positive + leak_only
    raw_claims: int
    finding_claims: int
    duplicate_claims: int
    leak_only_claims: int
    neutral_context_citations: int
    neutral_context_out_of_scope: int
    neutral_context_high_stakes: int
    non_item_context_citations: int
    non_checkable_entries: int
    non_checkable_non_item_citations: int
    conflicting_non_checkable_rows: tuple[str, ...]
    # Layer B (§5.2)
    n_rows: int
    routing_correct: int
    routing_leaked: int
    routing_misrouted: int
    routing_omitted: int
    routing_accuracy: Fraction | None
    checkability_binary_f1: Fraction | None
    checkability_class_f1: Mapping[str, Fraction | None]
    checkability_macro_f1: Fraction | None
    # Leaks (§5.2)
    n_in_scope_non_checkable: int
    leaked_in_scope_non_checkable: tuple[str, ...]
    in_scope_non_checkable_leak_rate: Fraction | None
    n_out_of_scope: int
    leaked_out_of_scope: tuple[str, ...]
    out_of_scope_leak_rate: Fraction | None
    out_of_scope_leaks_by_domain: Mapping[str, int]
    n_high_stakes: int
    high_stakes_leaks: int
    high_stakes_leaks_by_domain: Mapping[str, int]
    # Failures (§5.3)
    failed_conversations: tuple[FailedConversation, ...]
    failures_by_kind: Mapping[str, int]
    # Rows used (§5.3)
    recall_digits: SliceRecall
    recall_spelled: SliceRecall
    recall_no_number: SliceRecall
    digits_and_spelled_rows: int
    high_confidence: HighConfidenceMetrics
    # Other
    flagged_groups: tuple[FlaggedGroupStatus, ...]
    per_conversation: Mapping[str, ConversationCounts]
    conversation_scores: Mapping[str, ConversationScore]

    def __post_init__(self) -> None:
        for name in (
            "checkability_class_f1",
            "out_of_scope_leaks_by_domain",
            "high_stakes_leaks_by_domain",
            "failures_by_kind",
            "per_conversation",
            "conversation_scores",
        ):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    def summary(self) -> RunSummary:
        """The figures the margin test reads (S2 §8). ValueError when F1 or routing accuracy is undefined."""
        if self.f1 is None or self.routing_accuracy is None:
            raise ValueError("F1 or routing accuracy is undefined (0/0), so this run cannot enter the margin test")
        return RunSummary(
            f1=self.f1,
            routing_accuracy=self.routing_accuracy,
            high_stakes_leaks=self.high_stakes_leaks,
            per_conversation=self.per_conversation,
        )


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    value: Fraction | int | None
    limit: Fraction | int
    op: Literal[">=", "<="]
    passed: bool


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: Literal["A", "S"]
    passed: bool
    checks: tuple[GateCheck, ...]


def has_digits(text: str) -> bool:
    """S2 §5.3 slice: the utterance contains an ASCII digit 0-9 (never `str.isdigit`)."""
    return _DIGIT.search(text) is not None


def has_spelled_number(text: str) -> bool:
    """S2 §5.3 slice: a spelled number word. Tokens are runs of a-z in the lower-cased text, so hyphens, apostrophes,
    digits and punctuation split words. "one", "oh" and "point" never count on their own (spec AMB-20)."""
    return any(token in SPELLED_NUMBER_WORDS for token in _WORD.findall(text.lower()))


def claim_fidelity(*args: object, **kwargs: object) -> NoReturn:
    """S2 §5.2 claim fidelity (reported only). Not implemented until the owner rules on its definition."""
    # NEEDS-OWNER(AMB-4): what a "value" is, whose text is normalised, and which dates are ignored are not settled
    raise NotImplementedError(FIDELITY_PENDING)


def per_proposition_recall(*args: object, **kwargs: object) -> NoReturn:
    """S2 §5.2 per-proposition recall (reported only). Not implemented until the owner rules on claim fidelity."""
    # NEEDS-OWNER(AMB-4): depends on claim fidelity; whether it covers compound rows only is not settled
    raise NotImplementedError(FIDELITY_PENDING)


def _units(
    gold: GoldSet, flagged_groups: Iterable[FlaggedGroup]
) -> tuple[dict[RowKey, _Unit], tuple[FlaggedGroup, ...]]:
    """Recall units: every in-scope `CHECKABLE` row on its own, except that a flagged repeat group is one unit.

    Every group, of either kind, needs 2 or more distinct existing claim_ids, all in one conversation, every row
    in-scope `CHECKABLE` (only those rows count for recall), and a row in at most one group.
    """
    # NEEDS-OWNER(AMB-2): flags have no source in v0.4/v1.0; repeat = one recall unit
    groups = tuple(flagged_groups)
    unit_of: dict[RowKey, _Unit] = {item.key: frozenset({item.key}) for item in gold.in_scope_checkable()}
    owner: dict[str, int] = {}
    for number, group in enumerate(groups):
        if not isinstance(group, FlaggedGroup):
            raise TypeError("flagged_groups must hold FlaggedGroup values")
        members: list[GoldItem] = []
        for claim_id in group.claim_ids:
            try:
                item = gold.item_by_claim_id(claim_id)
            except KeyError:
                raise ValueError(f"flagged group {number}: unknown claim_id {claim_id!r}") from None
            if not item.is_in_scope_checkable:
                raise ValueError(f"flagged group {number}: {claim_id} is not an in-scope CHECKABLE row")
            if claim_id in owner:
                raise ValueError(f"flagged group {number}: {claim_id} is already in flagged group {owner[claim_id]}")
            owner[claim_id] = number
            members.append(item)
        if len({item.source_id for item in members}) != 1:
            raise ValueError(f"flagged group {number}: its rows are not all in one conversation")
        if group.kind == "repeat":
            unit = frozenset(item.key for item in members)
            for key in unit:
                unit_of[key] = unit
    return unit_of, groups


@dataclass(frozen=True, slots=True)
class _NonCheckableStats:
    entries: int
    non_item_citations: int
    conflicting: frozenset[RowKey]


def _check_cited_ids(extraction: Extraction, allowed: frozenset[str]) -> None:
    """S2 §3.13: only run-1 single-conversation outputs are scored. An id outside the conversation is a harness
    error, never a non-item segment (cap-session ids are re-keyed and never valid here)."""
    for array, entries in (("claims", extraction.claims), ("nonCheckable", extraction.non_checkable)):
        for entry in entries:
            for segment_id in entry.segment_ids:
                if segment_id not in allowed:
                    raise ValueError(
                        f"{extraction.source_id}: {array}[{entry.index}] cites {segment_id!r}, which is not a segment "
                        "of this conversation"
                    )


def _score(
    gold: GoldSet, result: ExtractionResult, unit_of: Mapping[RowKey, _Unit]
) -> tuple[ConversationScore, _NonCheckableStats]:
    if not isinstance(result, Extraction | ExtractionFailure):
        raise TypeError("a result must be an Extraction or an ExtractionFailure")
    source_id = result.source_id
    try:
        items = gold.items_for(source_id)
    except KeyError:
        raise ValueError(f"{source_id!r} is not a conversation of gold set {gold.version}") from None
    n_units = len({unit_of[item.key] for item in items if item.is_in_scope_checkable})
    if isinstance(result, ExtractionFailure):
        # S2 §5.3: scores as empty output. Rows not found and omitted; no claims, no leaks; rows stay in every
        # denominator (AMB-14). FL3: nothing here turns a failure into claims or verdicts.
        omitted: dict[RowKey, PredictedLabel] = {item.key: "omitted" for item in items}
        score = ConversationScore(
            source_id=source_id,
            failure=result.kind,
            claim_outcomes=(),
            found_rows=frozenset(),
            leaked_rows=frozenset(),
            labels=omitted,
            routing={item.key: "omitted" for item in items},
            counts=ConversationCounts(tp=0, fp=0, n_checkable=n_units),
        )
        return score, _NonCheckableStats(entries=0, non_item_citations=0, conflicting=frozenset())

    _check_cited_ids(result, gold.segment_ids(source_id))
    item_on: dict[str, GoldItem] = {item.segment_id: item for item in items}
    found_units: set[_Unit] = set()
    found_rows: set[RowKey] = set()
    predicted_rows: set[RowKey] = set()
    outcomes: list[ClaimOutcome] = []
    fp_claims = 0
    for claim in result.claims:
        # M1: segment ids only; never text, speakerId, claimId or id order.
        cited = [item_on[s] for s in claim.segment_ids if s in item_on]  # G(c)
        checkable = [item for item in cited if item.is_in_scope_checkable]  # K(c)
        non_item = frozenset(s for s in claim.segment_ids if s not in item_on)  # X(c)
        kind: ClaimKind
        if checkable:
            # §5.1 context rule: predicts CHECKABLE for K(c) only; the rest of G(c) is neutral (AMB-7, literal).
            predicted = frozenset(item.key for item in checkable)
            neutral = frozenset(item.key for item in cited if not item.is_in_scope_checkable)
            new_units = {unit_of[key] for key in predicted} - found_units
            # claims[] order decides the duplicate (AMB-9); context rows or non-item ids do not stop it (AMB-8).
            kind = "finding" if new_units else "duplicate"
            found_units |= new_units
            found_rows |= predicted
        else:
            # AMB-5: with no in-scope CHECKABLE row, the claim predicts CHECKABLE for every item row it cites.
            predicted = frozenset(item.key for item in cited)
            neutral = frozenset()
            new_units = set()
            if non_item or any(item.in_scope for item in cited):
                kind = "false_positive"  # AMB-6: its rows are still leaked; AMB-10: never de-duplicated.
                fp_claims += 1
            else:
                kind = "leak_only"  # §5.2: "A claim citing only out-of-scope rows is counted as a leak instead."
        predicted_rows |= predicted
        outcomes.append(ClaimOutcome(claim.index, kind, predicted, neutral, non_item, len(new_units)))

    nc_kinds: dict[RowKey, list[PredictedLabel]] = {}
    non_item_citations = 0
    for entry in result.non_checkable:
        # AMB-13: an entry gives its kind to every row it cites; entries on non-item segments affect nothing.
        for segment_id in entry.segment_ids:
            row = item_on.get(segment_id)
            if row is None:
                non_item_citations += 1
            else:
                nc_kinds.setdefault(row.key, []).append(entry.reason_kind)

    labels: dict[RowKey, PredictedLabel] = {}
    routing: dict[RowKey, RoutingOutcome] = {}
    for item in items:
        kinds = nc_kinds.get(item.key, [])
        if item.key in predicted_rows:
            # §5.2: a claim wins a shared segment for the rows it predicts CHECKABLE.
            labels[item.key] = ""
            routing[item.key] = "correct" if item.reason_kind == "" else "leaked"
        elif kinds:
            # NEEDS-OWNER(AMB-1): existential reading: a row is correct if any nonCheckable entry carries the gold kind
            present = item.reason_kind != "" and item.reason_kind in kinds
            routing[item.key] = "correct" if present else "misrouted"
            labels[item.key] = item.reason_kind if present else kinds[0]
        else:
            labels[item.key] = "omitted"
            routing[item.key] = "omitted"
    item_by_key = {item.key: item for item in items}
    score = ConversationScore(
        source_id=source_id,
        failure=None,
        claim_outcomes=tuple(outcomes),
        found_rows=frozenset(found_rows),
        leaked_rows=frozenset(key for key in predicted_rows if item_by_key[key].reason_kind != ""),
        labels=labels,
        routing=routing,
        counts=ConversationCounts(tp=len(found_units), fp=fp_claims, n_checkable=n_units),
    )
    stats = _NonCheckableStats(
        entries=len(result.non_checkable),
        non_item_citations=non_item_citations,
        conflicting=frozenset(key for key, kinds in nc_kinds.items() if len(set(kinds)) >= 2),
    )
    return score, stats


def score_conversation(
    gold: GoldSet, result: ExtractionResult, *, flagged_groups: Sequence[FlaggedGroup] = ()
) -> ConversationScore:
    """Score one conversation's run-1 result (S2 §5.1-§5.3). A failure scores as empty output."""
    unit_of, _ = _units(gold, flagged_groups)
    return _score(gold, result, unit_of)[0]


def _checkability_f1(
    rows: Sequence[GoldItem], labels: Mapping[RowKey, str]
) -> tuple[Fraction | None, dict[str, Fraction | None], Fraction | None]:
    """S2 §5.2 checkability F1 over gold rows only (non-item segments never count): binary (in-scope `CHECKABLE`
    against everything else) and per class over the six classes; "omitted" is in no class. Macro F1 is the mean of
    the classes whose F1 is defined (AMB-15)."""
    binary = _f1(
        sum(1 for row in rows if row.reason_kind == "" and labels[row.key] == ""),
        sum(1 for row in rows if row.reason_kind != "" and labels[row.key] == ""),
        sum(1 for row in rows if row.reason_kind == "" and labels[row.key] != ""),
    )
    per_class: dict[str, Fraction | None] = {}
    for kind in REASON_KIND_CLASSES:
        per_class[kind] = _f1(
            sum(1 for row in rows if row.reason_kind == kind and labels[row.key] == kind),
            sum(1 for row in rows if row.reason_kind != kind and labels[row.key] == kind),
            sum(1 for row in rows if row.reason_kind == kind and labels[row.key] != kind),
        )
    defined = [value for value in per_class.values() if value is not None]
    macro = Fraction(sum(defined, Fraction(0)), len(defined)) if defined else None
    return binary, per_class, macro


@dataclass(frozen=True, slots=True)
class _Leaks:
    n_in_scope_non_checkable: int
    leaked_in_scope_non_checkable: tuple[str, ...]
    n_out_of_scope: int
    leaked_out_of_scope: tuple[str, ...]
    out_of_scope_by_bucket: dict[str, int]
    n_high_stakes: int
    high_stakes_leaks: int
    high_stakes_by_domain: dict[str, int]


def _leaks(rows: Sequence[GoldItem], leaked: frozenset[RowKey]) -> _Leaks:
    """S2 §5.2: a row is leaked if any claim predicts `CHECKABLE` for it and its gold reason kind is not blank.
    The out-of-scope denominator is every out-of-scope row, the non-checkable ones included (AMB-17)."""
    in_scope_nc = [row for row in rows if row.is_in_scope_non_checkable]
    out_of_scope = [row for row in rows if row.is_out_of_scope]
    high_stakes = [row for row in rows if row.is_high_stakes]
    by_bucket = dict.fromkeys(OUT_OF_SCOPE_BUCKETS, 0)
    for row in out_of_scope:
        bucket = row.out_of_scope_bucket
        if bucket is not None and row.key in leaked:
            by_bucket[bucket] += 1
    by_domain = dict.fromkeys(HIGH_STAKES_ORDER, 0)
    for row in high_stakes:
        if row.key in leaked:
            by_domain[row.domain] += 1
    return _Leaks(
        n_in_scope_non_checkable=len(in_scope_nc),
        leaked_in_scope_non_checkable=tuple(sorted(row.claim_id for row in in_scope_nc if row.key in leaked)),
        n_out_of_scope=len(out_of_scope),
        leaked_out_of_scope=tuple(sorted(row.claim_id for row in out_of_scope if row.key in leaked)),
        out_of_scope_by_bucket=by_bucket,
        n_high_stakes=len(high_stakes),
        high_stakes_leaks=sum(by_domain.values()),
        high_stakes_by_domain=by_domain,
    )


def _slice_recall(rows: Sequence[GoldItem], row_found: Mapping[RowKey, bool]) -> SliceRecall:
    found = sum(1 for row in rows if row_found[row.key])
    return SliceRecall(rows=len(rows), found=found, recall=_ratio(found, len(rows)))


def _high_confidence(
    gold: GoldSet,
    unit_of: Mapping[RowKey, _Unit],
    found_units: set[_Unit],
    outcomes: Sequence[ClaimOutcome],
    labels: Mapping[RowKey, str],
    routing: Mapping[RowKey, str],
    leaked: frozenset[RowKey],
) -> HighConfidenceMetrics:
    """S2 §5.3 high-confidence-only figures (AMB-21). Matching is unchanged: each claim keeps its all-rows class. A
    unit counts only when every member is high; an FP claim counts only when it cites a non-item segment or a
    high-confidence in-scope non-checkable row; a finding whose rows are all low adds nothing."""
    rows = [item for item in gold.items if item.confidence_in_label == "high"]
    high_keys = frozenset(row.key for row in rows)
    high_in_scope_nc = frozenset(row.key for row in rows if row.is_in_scope_non_checkable)
    units = {unit for unit in unit_of.values() if unit <= high_keys}
    found = len(found_units & units)
    fp_claims = sum(
        1
        for outcome in outcomes
        if outcome.kind == "false_positive" and (outcome.non_item_segment_ids or outcome.predicted & high_in_scope_nc)
    )
    correct = sum(1 for row in rows if routing[row.key] == "correct")
    binary, per_class, macro = _checkability_f1(rows, labels)
    leaks = _leaks(rows, leaked)
    return HighConfidenceMetrics(
        n_checkable_units=len(units),
        found_units=found,
        fp_claims=fp_claims,
        recall=_ratio(found, len(units)),
        precision=_ratio(found, found + fp_claims),
        f1=_f1(found, fp_claims, len(units) - found),
        n_rows=len(rows),
        routing_correct=correct,
        routing_accuracy=_ratio(correct, len(rows)),
        checkability_binary_f1=binary,
        checkability_class_f1=per_class,
        checkability_macro_f1=macro,
        n_in_scope_non_checkable=leaks.n_in_scope_non_checkable,
        leaked_in_scope_non_checkable=leaks.leaked_in_scope_non_checkable,
        in_scope_non_checkable_leak_rate=_ratio(
            len(leaks.leaked_in_scope_non_checkable), leaks.n_in_scope_non_checkable
        ),
        n_out_of_scope=leaks.n_out_of_scope,
        leaked_out_of_scope=leaks.leaked_out_of_scope,
        out_of_scope_leak_rate=_ratio(len(leaks.leaked_out_of_scope), leaks.n_out_of_scope),
        n_high_stakes=leaks.n_high_stakes,
        high_stakes_leaks=leaks.high_stakes_leaks,
        high_stakes_leaks_by_domain=leaks.high_stakes_by_domain,
    )


def _check_results(gold: GoldSet, results: Mapping[str, ExtractionResult]) -> None:
    """S2 §3.13: exactly one run-1 single-conversation result per gold conversation. A missing result is a harness
    error, not an extraction failure, and cap-session outputs are never passed in (AMB-16)."""
    if not isinstance(results, Mapping):
        raise TypeError("results must map each source_id to its ExtractionResult")
    expected = set(gold.conversation_ids())
    given = set(results)
    missing = sorted(expected - given)
    extra = sorted(repr(key) for key in given - expected)
    if missing or extra:
        raise ValueError(
            f"results must hold exactly one result per gold conversation; missing {missing}, extra {extra}"
        )
    for key, result in results.items():
        if not isinstance(result, Extraction | ExtractionFailure):
            raise TypeError(f"{key}: a result must be an Extraction or an ExtractionFailure")
        if result.source_id != key:
            raise ValueError(f"results[{key!r}] holds the result for {result.source_id!r}")


def score_run(
    gold: GoldSet, results: Mapping[str, ExtractionResult], *, flagged_groups: Sequence[FlaggedGroup] = ()
) -> RunMetrics:
    """Every S2 §5 figure for one run: run-1 single-conversation results, one per gold conversation (S2 §3.13)."""
    _check_results(gold, results)
    unit_of, groups = _units(gold, flagged_groups)
    scores: dict[str, ConversationScore] = {}
    nc_stats: dict[str, _NonCheckableStats] = {}
    for source_id in gold.conversation_ids():
        scores[source_id], nc_stats[source_id] = _score(gold, results[source_id], unit_of)

    item_by_key = {item.key: item for item in gold.items}
    labels: dict[RowKey, str] = {}
    routing: dict[RowKey, str] = {}
    found_rows: set[RowKey] = set()
    leaked_set: set[RowKey] = set()
    outcomes: list[ClaimOutcome] = []
    for score in scores.values():
        labels.update(score.labels)
        routing.update(score.routing)
        found_rows |= score.found_rows
        leaked_set |= score.leaked_rows
        outcomes.extend(score.claim_outcomes)
    leaked = frozenset(leaked_set)

    # Extraction (§5.2): pooled over every conversation; failed conversations stay in the denominator. Precision is
    # found rows / (found rows + false-positive claims): rows on top, claims in the FP term (AMB-11).
    all_units = set(unit_of.values())
    found_units = {unit_of[key] for key in found_rows}
    n_units = len(all_units)
    found = len(found_units)
    fp_claims = sum(1 for outcome in outcomes if outcome.kind == "false_positive")
    recall = _ratio(found, n_units)

    # Claim accounting (§5.1): reported, never scored.
    kinds = Counter(outcome.kind for outcome in outcomes)
    anchored = [outcome for outcome in outcomes if outcome.kind in ("finding", "duplicate")]
    neutral_pairs = [item_by_key[key] for outcome in anchored for key in outcome.neutral]  # AMB-12: (claim, row) pairs

    # Layer B (§5.2) over every row, failed conversations included.
    rows = gold.items
    outcome_counts = Counter(routing[row.key] for row in rows)
    binary, per_class, macro = _checkability_f1(rows, labels)
    leaks = _leaks(rows, leaked)

    # Failures (§5.3).
    failed = tuple(
        FailedConversation(
            source_id=source_id,
            kind=score.failure,
            out_of_scope_claim_ids=tuple(sorted(i.claim_id for i in gold.items_for(source_id) if i.is_out_of_scope)),
        )
        for source_id, score in scores.items()
        if score.failure is not None
    )
    failures_by_kind = dict.fromkeys(FAILURE_KINDS, 0)
    for failure in failed:
        failures_by_kind[failure.kind] += 1

    # Rows used (§5.3): slices over in-scope CHECKABLE rows by utterance; a row is found when its unit is found.
    checkable_rows = gold.in_scope_checkable()
    row_found = {row.key: unit_of[row.key] in found_units for row in checkable_rows}
    digits = [row for row in checkable_rows if has_digits(row.utterance)]
    spelled = [row for row in checkable_rows if has_spelled_number(row.utterance)]
    neither = [row for row in checkable_rows if not has_digits(row.utterance) and not has_spelled_number(row.utterance)]

    statuses = []
    for group in groups:
        found_by_row = {cid: gold.item_by_claim_id(cid).key in found_rows for cid in group.claim_ids}
        statuses.append(
            FlaggedGroupStatus(
                kind=group.kind,
                claim_ids=group.claim_ids,
                found=found_by_row,
                unit_found=any(found_by_row.values()) if group.kind == "repeat" else None,
            )
        )

    return RunMetrics(
        n_checkable_units=n_units,
        found_units=found,
        fp_claims=fp_claims,
        recall=recall,
        precision=_ratio(found, found + fp_claims),
        f1=_f1(found, fp_claims, n_units - found),
        recall_target_met=None if recall is None else recall >= RECALL_TARGET,
        raw_claims=len(outcomes),
        finding_claims=kinds["finding"],
        duplicate_claims=kinds["duplicate"],
        leak_only_claims=kinds["leak_only"],
        neutral_context_citations=len(neutral_pairs),
        neutral_context_out_of_scope=sum(1 for item in neutral_pairs if item.is_out_of_scope),
        neutral_context_high_stakes=sum(1 for item in neutral_pairs if item.is_high_stakes),
        non_item_context_citations=sum(len(outcome.non_item_segment_ids) for outcome in anchored),
        non_checkable_entries=sum(stats.entries for stats in nc_stats.values()),
        non_checkable_non_item_citations=sum(stats.non_item_citations for stats in nc_stats.values()),
        conflicting_non_checkable_rows=tuple(
            sorted(item_by_key[key].claim_id for stats in nc_stats.values() for key in stats.conflicting)
        ),
        n_rows=len(rows),
        routing_correct=outcome_counts["correct"],
        routing_leaked=outcome_counts["leaked"],
        routing_misrouted=outcome_counts["misrouted"],
        routing_omitted=outcome_counts["omitted"],
        routing_accuracy=_ratio(outcome_counts["correct"], len(rows)),
        checkability_binary_f1=binary,
        checkability_class_f1=per_class,
        checkability_macro_f1=macro,
        n_in_scope_non_checkable=leaks.n_in_scope_non_checkable,
        leaked_in_scope_non_checkable=leaks.leaked_in_scope_non_checkable,
        in_scope_non_checkable_leak_rate=_ratio(
            len(leaks.leaked_in_scope_non_checkable), leaks.n_in_scope_non_checkable
        ),
        n_out_of_scope=leaks.n_out_of_scope,
        leaked_out_of_scope=leaks.leaked_out_of_scope,
        out_of_scope_leak_rate=_ratio(len(leaks.leaked_out_of_scope), leaks.n_out_of_scope),
        out_of_scope_leaks_by_domain=leaks.out_of_scope_by_bucket,
        n_high_stakes=leaks.n_high_stakes,
        high_stakes_leaks=leaks.high_stakes_leaks,
        high_stakes_leaks_by_domain=leaks.high_stakes_by_domain,
        failed_conversations=failed,
        failures_by_kind=failures_by_kind,
        recall_digits=_slice_recall(digits, row_found),
        recall_spelled=_slice_recall(spelled, row_found),
        recall_no_number=_slice_recall(neither, row_found),
        digits_and_spelled_rows=sum(1 for row in digits if has_spelled_number(row.utterance)),
        high_confidence=_high_confidence(gold, unit_of, found_units, outcomes, labels, routing, leaked),
        flagged_groups=tuple(statuses),
        per_conversation={source_id: score.counts for source_id, score in scores.items()},
        conversation_scores=scores,
    )


def _gate_check(name: str, value: Fraction | int | None, limit: Fraction | int, op: Literal[">=", "<="]) -> GateCheck:
    """An inclusive bound on an exact value; None fails (spec C1). Floats are refused: display only (GT1)."""
    if isinstance(value, float):
        raise TypeError(f"{name}: gate values must be exact, not float")
    if value is None:
        passed = False
    elif op == ">=":
        passed = value >= limit
    else:
        passed = value <= limit
    return GateCheck(name=name, value=value, limit=limit, op=op, passed=passed)


def gate_a(m: RunMetrics) -> GateResult:
    """S2 §6 gate A, on all rows (§5.3): recall >= 3/4, precision >= 3/4 and routing accuracy >= 7/10."""
    checks = (
        _gate_check("recall", m.recall, RECALL_FLOOR, ">="),
        _gate_check("precision", m.precision, PRECISION_FLOOR, ">="),
        _gate_check("routing_accuracy", m.routing_accuracy, ROUTING_FLOOR, ">="),
    )
    return GateResult(gate="A", passed=all(check.passed for check in checks), checks=checks)


def gate_s(m: RunMetrics) -> GateResult:
    """S2 §6 gate S, on all rows (§5.3): at most 2 high-stakes leaks and an out-of-scope leak rate of at most 1/5."""
    checks = (
        _gate_check("high_stakes_leaks", m.high_stakes_leaks, HIGH_STAKES_LEAK_MAX, "<="),
        _gate_check("out_of_scope_leak_rate", m.out_of_scope_leak_rate, OOS_LEAK_RATE_MAX, "<="),
    )
    return GateResult(gate="S", passed=all(check.passed for check in checks), checks=checks)
