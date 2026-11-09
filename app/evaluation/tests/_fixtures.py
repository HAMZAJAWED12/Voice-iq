"""Synthetic fixtures for the bake-off harness tests (H1 spec section 4: fixtures F, F+ and X).

Every conversation and row here is invented for the tests and built in code. Nothing is read from, or copied out of,
`app/evaluation/gold/`. A version directory is written under pytest's `tmp_path` the way the gold README describes
it: JSONL with LF endings, `items.csv` from `csv.DictWriter`, and a `MANIFEST.sha256` over the LF bytes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.bakeoff.extraction import Extraction, ExtractionFailure, ExtractionResult, parse_extraction
from app.evaluation.bakeoff.gold import ITEM_KEYS, MANIFEST_FILES, GoldSet, load_gold_set

# An independent copy of the derivation rules (guide §3 step 3, §4.2, §4.3), so a loader bug cannot hide in both.
_IN_SCOPE = {"GENERAL", "MARKET_FINANCE", "WEATHER"}

# A JSON integer longer than 4,300 digits is a ValueError only under CPython's integer-string limit (3.10.7 and later).
NEEDS_DIGIT_LIMIT = pytest.mark.skipif(
    getattr(sys, "get_int_max_str_digits", lambda: 0)() == 0, reason="this interpreter has no integer-string limit"
)

Row = dict[str, Any]
SegmentRef = int | str


def seg(number: SegmentRef) -> str:
    return number if isinstance(number, str) else f"seg_{number:03d}"


def make_segment(number: int, text: str, *, confidence: float = 0.9) -> Row:
    start = 3.0 * (number - 1)
    return {
        "segment_id": seg(number),
        "speaker": "SPEAKER_00" if number % 2 else "SPEAKER_01",
        "start": start,
        "end": start + 2.5,
        "text": text,
        "confidence": confidence,
    }


def make_conversation(source_id: str, texts: Sequence[str], **overrides: Any) -> Row:
    conversation = {
        "source_id": source_id,
        "call_date": "2025-03-12",
        "setting": "synthetic test call",
        "language": "en",
        "provenance": "synthetic",
        "drafted_by": "claude-draft",
        "guide_version": "v0.7",
        "segments": [make_segment(number, text) for number, text in enumerate(texts, start=1)],
    }
    conversation.update(overrides)
    return conversation


def make_item(
    claim_id: str,
    conversation: Row,
    number: int,
    checkability: str,
    domain: str,
    *,
    private_individual: str = "no",
    confidence: str = "high",
    **overrides: Any,
) -> Row:
    """A row on segment `number`, with `in_scope`, `is_claim` and `reason_kind` derived (G4). Invalid-data tests
    override fields explicitly."""
    segment = conversation["segments"][number - 1]
    in_scope = domain in _IN_SCOPE and private_individual == "no"
    if checkability != "CHECKABLE":
        reason_kind = checkability.lower()
    elif not in_scope:
        reason_kind = "outOfScope"
    else:
        reason_kind = ""
    layer_a = in_scope and checkability == "CHECKABLE"
    row = {
        "claim_id": claim_id,
        "source_id": conversation["source_id"],
        "segment_id": segment["segment_id"],
        "speaker_label": segment["speaker"],
        "utterance": segment["text"],
        "checkability": checkability,
        "domain": domain,
        "private_individual": private_individual,
        "in_scope": "yes" if in_scope else "no",
        "is_claim": ("yes" if checkability == "CHECKABLE" else "no") if in_scope else "",
        "span_text": segment["text"] if layer_a else "",
        "claim_text": f"synthetic proposition for {claim_id} on 2025-03-12" if layer_a else "",
        "is_compound": "no",
        "time_sensitive": "no",
        "deliberately_false": "no",
        "reason_kind": reason_kind,
        "confidence_in_label": confidence,
        "provenance": "synthetic",
        "drafted_by": "claude-draft",
        "label_status": "owner_ok",
    }
    row.update(overrides)
    return row


def jsonl_bytes(objects: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(obj) + "\n" for obj in objects).encode("utf-8")


def csv_bytes(rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str] = ITEM_KEYS) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fieldnames), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def manifest_bytes(lf_files: Mapping[str, bytes]) -> bytes:
    """`<sha256 of the LF bytes>  <name>` lines, sorted by name."""
    return "".join(f"{hashlib.sha256(lf_files[name]).hexdigest()}  {name}\n" for name in sorted(lf_files)).encode(
        "ascii"
    )


def write_version(
    root: Path,
    conversations: Sequence[Row],
    items: Sequence[Row],
    *,
    name: str = "v9.9",
    crlf: bool = False,
    csv_rows: Sequence[Row] | None = None,
    csv_fieldnames: Sequence[str] = ITEM_KEYS,
) -> Path:
    """Write a version directory. With `crlf=True` the files get CRLF endings and the manifest stays over LF bytes."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "conversations.jsonl": jsonl_bytes(conversations),
        "items.jsonl": jsonl_bytes(items),
        "items.csv": csv_bytes(items if csv_rows is None else csv_rows, csv_fieldnames),
    }
    for file_name, data in files.items():
        (directory / file_name).write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    (directory / "MANIFEST.sha256").write_bytes(manifest_bytes(files))
    return directory


def rewrite(directory: Path, name: str, data: bytes) -> None:
    """Replace one gold file and recompute the manifest over every file's LF bytes."""
    (directory / name).write_bytes(data)
    lf = {file_name: (directory / file_name).read_bytes().replace(b"\r\n", b"\n") for file_name in MANIFEST_FILES}
    (directory / "MANIFEST.sha256").write_bytes(manifest_bytes(lf))


# Fixture F (syn_900) and F+ (adds syn_901). Non-item segments: syn_900 seg_001 and seg_009; syn_901 seg_003.
F_TEXTS_900 = (
    "hi, thanks for calling",
    "gold is at 2,400 an ounce",
    "could you check the Leeds forecast for tomorrow",
    "minus three in Oslo and snowing",
    "I reckon the euro is overrated",
    "that vaccine is given in two doses",
    "our depot ships every order within two days",
    "I think fixed savings bonds are a poor deal",
    "okay, bye for now",
)
F_TEXTS_901 = (
    "Canberra is the capital of Australia",
    "landlords must return a deposit within ten days",
    "right, thanks",
)


def fixture_f(plus: bool = False) -> tuple[list[Row], list[Row]]:
    c900 = make_conversation("syn_900", F_TEXTS_900)
    items = [
        make_item("fx_01", c900, 2, "CHECKABLE", "MARKET_FINANCE"),  # MK
        make_item("fx_02", c900, 3, "REQUEST", "WEATHER"),  # RQ
        make_item(
            "fx_03",
            c900,
            4,
            "CHECKABLE",
            "WEATHER",
            confidence="low",
            is_compound="yes",
            claim_text="Oslo is at -3 C on 2025-03-12 | it is snowing in Oslo on 2025-03-12",
        ),  # WX
        make_item("fx_04", c900, 5, "OPINION", "MARKET_FINANCE"),  # OP
        make_item("fx_05", c900, 6, "CHECKABLE", "HEALTH"),  # HE
        make_item("fx_06", c900, 7, "CHECKABLE", "OWN_COMPANY"),  # OC
        make_item("fx_07", c900, 8, "OPINION", "PERSONAL_FINANCE"),  # PF
    ]
    conversations = [c900]
    if plus:
        c901 = make_conversation("syn_901", F_TEXTS_901)
        conversations.append(c901)
        items += [
            make_item("fx_08", c901, 1, "CHECKABLE", "GENERAL"),  # GN
            make_item("fx_09", c901, 2, "CHECKABLE", "LEGAL"),  # LG
        ]
    return conversations, items


# Fixture X (syn_a, syn_b). Non-item segments: a1 and b3.
X_TEXTS_A = (
    "morning, how are things",
    "about twenty degrees in Rome",
    "the dollar looks weak to me",
    "the surgery runs a walk-in clinic on Saturdays",
    "it is snowing in Leeds",
    "please send me the invoice again",
)
X_TEXTS_B = (
    "sterling is at 1.27",
    "the state pension age there is sixty-six",
    "mm, right",
    "prices are sort of all over the place",
)


def fixture_x() -> tuple[list[Row], list[Row]]:
    a = make_conversation("syn_a", X_TEXTS_A)
    b = make_conversation("syn_b", X_TEXTS_B)
    items = [
        make_item("x_01", a, 2, "CHECKABLE", "GENERAL"),
        make_item("x_02", a, 3, "OPINION", "MARKET_FINANCE"),
        make_item("x_03", a, 4, "CHECKABLE", "HEALTH"),
        make_item("x_04", a, 5, "CHECKABLE", "WEATHER", confidence="low"),
        make_item("x_05", a, 6, "REQUEST", "OWN_COMPANY"),
        make_item("x_06", b, 1, "CHECKABLE", "MARKET_FINANCE"),
        make_item("x_07", b, 2, "CHECKABLE", "PERSONAL_FINANCE"),
        make_item("x_08", b, 4, "VAGUE", "GENERAL"),
    ]
    return [a, b], items


def load_f(tmp_path: Path, plus: bool = False, **kwargs: Any) -> GoldSet:
    return load_gold_set(write_version(tmp_path, *fixture_f(plus), **kwargs))


def load_x(tmp_path: Path, **kwargs: Any) -> GoldSet:
    return load_gold_set(write_version(tmp_path, *fixture_x(), **kwargs))


Claims = Sequence[Sequence[SegmentRef]]
NonCheckables = Sequence[tuple[str, Sequence[SegmentRef]]]


def output_json(claims: Claims = (), nc: NonCheckables = (), *, text: str | None = None) -> str:
    """A raw model response in the PHASE-0 §2.2 extraction shape."""
    return json.dumps(
        {
            "claims": [
                {"text": text if text is not None else f"claim {i}", "segmentIds": [seg(s) for s in ids]}
                for i, ids in enumerate(claims)
            ],
            "nonCheckable": [{"segmentIds": [seg(s) for s in ids], "reasonKind": kind} for kind, ids in nc],
        }
    )


def extraction(gold: GoldSet, source_id: str, claims: Claims = (), nc: NonCheckables = (), **kwargs: Any) -> Extraction:
    return parse_extraction(
        output_json(claims, nc, **kwargs), source_id=source_id, request_segment_ids=gold.segment_ids(source_id)
    )


# Output E1 for syn_900 (spec section 4).
E1_CLAIMS: Claims = ([2], [2, 3], [4, 6], [1], [7], [5, 8])
E1_NC: NonCheckables = (("request", [3]), ("outOfScope", [6]), ("opinion", [5]), ("vague", [9]))


def f_run(
    gold: GoldSet,
    claims: Claims = (),
    nc: NonCheckables = (),
    *,
    syn_901: ExtractionResult | None = None,
    **kwargs: Any,
) -> dict[str, ExtractionResult]:
    """syn_900 from `claims`/`nc`; on F+, syn_901 is `syn_901` or a valid empty Extraction."""
    run: dict[str, ExtractionResult] = {"syn_900": extraction(gold, "syn_900", claims, nc, **kwargs)}
    if "syn_901" in gold.conversation_ids():
        run["syn_901"] = syn_901 if syn_901 is not None else extraction(gold, "syn_901")
    return run


def run_e1(gold: GoldSet, claims: Claims = E1_CLAIMS, nc: NonCheckables = E1_NC) -> dict[str, ExtractionResult]:
    return f_run(gold, claims, nc)


def run_e1t(gold_plus: GoldSet) -> dict[str, ExtractionResult]:
    return f_run(gold_plus, E1_CLAIMS, E1_NC, syn_901=ExtractionFailure("syn_901", "timeout"))


def x_run(
    gold: GoldSet,
    a: Claims = (),
    a_nc: NonCheckables = (),
    b: Claims = (),
    b_nc: NonCheckables = (),
    *,
    a_fail: str | None = None,
    b_fail: str | None = None,
) -> dict[str, ExtractionResult]:
    """Fixture X run: each conversation is the given claims and entries (a valid, maybe empty, Extraction), or an
    ExtractionFailure of the given kind."""
    run: dict[str, ExtractionResult] = {}
    for source_id, claims, nc, failure in (("syn_a", a, a_nc, a_fail), ("syn_b", b, b_nc, b_fail)):
        if failure is not None:
            run[source_id] = ExtractionFailure(source_id, failure)  # type: ignore[arg-type]
        else:
            run[source_id] = extraction(gold, source_id, claims, nc)
    return run


XM_A: Claims = ([2, 1], [2], [4], [5, 3])
XM_A_NC: NonCheckables = (("opinion", [3]), ("outOfScope", [6]), ("outOfScope", [4]))
XW_A: Claims = ([1, 2, 3, 4, 5, 6],)
XW_A_NC: NonCheckables = (("opinion", [3, 4, 6]), ("request", [3, 4, 6]), ("outOfScope", [3, 4, 6]))
XP_A: Claims = ([2], [5])
XP_A_NC: NonCheckables = (("opinion", [3]), ("outOfScope", [4]), ("request", [6]))
XP_B: Claims = ([1],)
XP_B_NC: NonCheckables = (("outOfScope", [2]), ("vague", [4]))


def run_xm(gold: GoldSet) -> dict[str, ExtractionResult]:
    return x_run(gold, XM_A, XM_A_NC, b_fail="timeout")


def run_xw(gold: GoldSet) -> dict[str, ExtractionResult]:
    return x_run(gold, XW_A, XW_A_NC)


def run_xp(gold: GoldSet) -> dict[str, ExtractionResult]:
    return x_run(gold, XP_A, XP_A_NC, XP_B, XP_B_NC)
