"""Claim fidelity and per-proposition recall as the project owner ruled them (AMB-4; S2 §5.1-§5.3).

AMB4-1 to AMB4-8: what a value is, the normaliser and its order, ISO dates, parts, the claim judged, rows and parts
with no value, per-proposition recall and the outputs scored. Every row and claim here is synthetic.
"""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from fractions import Fraction as F
from pathlib import Path

import pytest

from app.evaluation.bakeoff import fidelity
from app.evaluation.bakeoff.extraction import (
    ExtractedClaim,
    Extraction,
    ExtractionFailure,
    ExtractionResult,
    NonCheckableEntry,
)
from app.evaluation.bakeoff.fidelity import (
    INDEX_NAMES,
    ISO_4217_CODES,
    SEPARATOR,
    FidelityFigures,
    FidelityMetrics,
    Value,
    claim_parts,
    score_fidelity,
    text_values,
)
from app.evaluation.bakeoff.gold import GoldSet, load_gold_set
from app.evaluation.bakeoff.scoring import score_run
from app.evaluation.tests._fixtures import (
    load_f,
    load_x,
    make_conversation,
    make_item,
    run_e1t,
    run_xm,
    run_xp,
    seg,
    write_version,
)

FTSE, DJIA, NASDAQ = INDEX_NAMES
NBSP = chr(160)


def N(digits: str) -> Value:
    return Value("number", Decimal(digits))


def C(code: str) -> Value:
    return Value("code", code)


def I(name: str) -> Value:  # noqa: E743 - the index kind, beside N and C
    return Value("index", name)


# ---------------------------------------------------------------- the normaliser (AMB4-1 to AMB4-3)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # AMB4-2: values compare by exact value; one thousands comma pass; digits wherever they stand.
        ("1.10", {N("1.1")}),
        ("1,234,567", {N("1234567")}),
        ("1,23", {N("1"), N("23")}),
        ("1,2345", {N("1"), N("2345")}),
        ("1,234.50", {N("1234.5")}),
        ("3rd", {N("3")}),
        ("400k", {N("400")}),
        ("Q4", {N("4")}),
        (".5", {N("5")}),
        ("1.2.3", {N("1.2"), N("3")}),
        ("1.2 million", {N("1.2")}),  # magnitude words are not converted
        ("S&P 500", {N("500")}),  # digits of other index names are numbers
        # AMB4-1: signs and units are not values.
        ("-2", {N("2")}),
        ("Rome is at 20 C", {N("20")}),
        # AMB4-1/2: the three index names; a full name takes its digits with it.
        ("FTSE 100", {I(FTSE)}),
        ("the ftse 100 index", {I(FTSE)}),
        ("FTSE" + NBSP + "100", {I(FTSE)}),
        ("FTSE 250", {N("250")}),
        ("FTSE100", {N("100")}),
        ("FTSE 1000", {N("1000")}),
        ("the Dow", {I(DJIA)}),
        ("Dow Jones Industrial Average", {I(DJIA)}),
        ("DOW JONES INDUSTRIAL AVERAGE", {I(DJIA)}),
        ("Dow Jones", {I(DJIA)}),
        ("Dow, at 34,000", {I(DJIA), N("34000")}),
        ("Dow 34,000", {N("34000")}),  # the next token starts with a digit: not the index
        ("Nasdaq 100", {N("100")}),
        ("the Nasdaq", {I(NASDAQ)}),
        ("nasdaq composite", {I(NASDAQ)}),
        # AMB4C-2: a leftover FTSE, Dow or Nasdaq in any letter case.
        ("the dow", {I(DJIA)}),
        ("NASDAQ", {I(NASDAQ)}),
        ("ftse at 8,000", {I(FTSE), N("8000")}),
        # AMB4C-1: a hyphen counts like a space between an index name and its digits, and between a full name's words.
        ("FTSE-100", {I(FTSE)}),
        ("ftse - 100", {I(FTSE)}),
        ("FTSE -100", {I(FTSE)}),
        ("FTSE-250 at 8,000", {N("250"), N("8000")}),
        ("Nasdaq-100 at 18,000", {N("100"), N("18000")}),
        ("nasdaq- 100", {N("100")}),
        ("Dow -34,000", {N("34000")}),
        ("FTSE-1000", {N("1000")}),
        ("Dow-Jones-Industrial-Average", {I(DJIA)}),
        ("Nasdaq - Composite 5", {I(NASDAQ), N("5")}),
        ("FTSE-", {I(FTSE)}),
        # Only the ASCII hyphen-minus counts: after an en dash the next token does not start with a digit.
        ("FTSE" + chr(0x2013) + "100", {I(FTSE), N("100")}),
        ("Nasdaq" + chr(0x2013) + "100", {I(NASDAQ), N("100")}),
        # AMB4-3: only ISO dates are removed, wherever they touch letters; no other wording is a date.
        ("on 2025-03-12", set()),
        ("x2025-03-12y", set()),
        ("2025-03-12T10:00", {N("10"), N("00")}),
        ("2025-03-129", {N("2025"), N("3"), N("129")}),
        ("2025-13-01", {N("2025"), N("13"), N("1")}),
        ("3 March", {N("3")}),
        ("Q4 2024", {N("4"), N("2024")}),
        ("1903", {N("1903")}),
        # AMB4-1: whole upper-case runs on the ISO 4217 list; a pair is its two codes.
        ("EUR/USD", {C("EUR"), C("USD")}),
        ("EURUSD", set()),
        ("usd 5", {N("5")}),
        ("BTC 60,000", {N("60000")}),
        ("XAU USD 2,400", {C("XAU"), C("USD"), N("2400")}),
        # Only ASCII digits are numbers (Arabic-Indic one, two).
        (chr(0x661) + chr(0x662), set()),
        ("", set()),
    ],
)
def test_text_values(text: str, expected: set[Value]) -> None:
    assert text_values(text) == frozenset(expected)


def test_values_of_each_kind_are_distinct_and_compare_by_value() -> None:
    assert text_values("1.10") == text_values("1.1")
    assert hash(N("1.10")) == hash(N("1.1"))
    assert text_values("FTSE 100") != text_values("100")
    assert N("100") != I(FTSE)
    assert C("USD") != Value("index", "USD")
    assert isinstance(text_values("x"), frozenset)


def test_the_order_of_steps_1_to_4() -> None:
    """AMB4-2 (AMB4C-7): step 2 (full names) runs before step 4 (thousands commas), so 'FTSE 100,000' is the FTSE 100
    and 0, not 100000; step 1 (ISO dates) runs before step 3 (a leftover word), so the date does not make 'Dow' a
    word followed by a digit."""
    assert text_values("FTSE 100,000") == {I(FTSE), N("0")}
    assert text_values("Dow 2025-03-12 at 5") == {I(DJIA), N("5")}


LONG_S, DOTLESS_I, KELVIN = chr(0x17F), chr(0x131), chr(0x212A)


def test_non_ascii_letters_never_match_a_name() -> None:
    """AMB4C-7: names match in any ASCII letter case only. Under re.IGNORECASE each of these non-ASCII letters would
    fold into its ASCII twin, which is why the names are built from explicit classes."""
    for letter, ascii_twin in ((LONG_S, "s"), (DOTLESS_I, "i"), (KELVIN, "k")):
        assert re.fullmatch(ascii_twin, letter, re.IGNORECASE)
        assert re.fullmatch(fidelity._caseless(ascii_twin), letter) is None
        assert re.fullmatch(fidelity._caseless(ascii_twin), ascii_twin.upper())
    assert text_values("Na" + LONG_S + "daq") == set()
    assert text_values("Na" + LONG_S + "daq Composite at 5") == {N("5")}
    full_djia = dict(fidelity._FULL_NAMES)[DJIA]
    assert full_djia.search("Dow Jones " + DOTLESS_I + "ndustrial Average") is None
    assert full_djia.search("DOW JONES INDUSTRIAL AVERAGE")
    # The leftover 'Dow' still reads as the index here: the next token does not start with a digit.
    assert text_values("Dow Jones " + DOTLESS_I + "ndustrial Average") == {I(DJIA)}
    assert text_values(KELVIN + " FTSE 100") == {I(FTSE)}


def test_names_use_no_case_or_ascii_flag() -> None:
    """AMB4C-7: no re.IGNORECASE and no re.ASCII, so `\\s` stays Unicode whitespace (the no-break space case)."""
    tree = ast.parse(Path(fidelity.__file__).read_text(encoding="utf-8"))
    banned = {"I", "IGNORECASE", "A", "ASCII"}
    assert {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in banned} == set()
    assert all(pattern.flags == re.UNICODE for _, pattern in (*fidelity._FULL_NAMES, *fidelity._SHORT_NAMES))


# ---------------------------------------------------------------- the ISO 4217 list (AMB4-1)


def test_the_iso_4217_list() -> None:
    assert type(ISO_4217_CODES) is frozenset
    assert len(ISO_4217_CODES) == 182
    assert all(re.fullmatch(r"[A-Z]{3}", code) for code in ISO_4217_CODES)
    assert {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "XAU", "XAG", "XDR"} <= ISO_4217_CODES
    assert not {"BTC", "HRK", "CNH"} & ISO_4217_CODES


def test_the_iso_4217_list_is_one_string_literal() -> None:
    tree = ast.parse(Path(fidelity.__file__).read_text(encoding="utf-8"))
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert [s for s in strings if len(s) == 3 and s.isupper()] == []


# ---------------------------------------------------------------- parts (AMB4-4)


def _one_row_gold(tmp_path: Path, claim_text: str, *, compound: bool = False) -> GoldSet:
    """One conversation, `syn_r`, with one in-scope CHECKABLE row on seg_001."""
    conversation = make_conversation("syn_r", ["a synthetic line", "another synthetic line"])
    row = make_item(
        "r_01", conversation, 1, "CHECKABLE", "GENERAL", claim_text=claim_text, is_compound="yes" if compound else "no"
    )
    return load_gold_set(write_version(tmp_path, [conversation], [row]))


@pytest.mark.parametrize(
    ("claim_text", "compound", "expected"),
    [
        ("a 1 | b 2", True, (("a 1", "b 2"), False)),
        ("a 1 |  b 2 ", True, (("a 1", "b 2"), False)),
        ("a 1|b 2", True, (("a 1|b 2",), True)),  # flag yes, no separator: one part
        ("Rome is at 20 C | Rome is sunny", False, (("Rome is at 20 C | Rome is sunny",), True)),
        ("x 1", False, (("x 1",), False)),
        ("a 1 | ", True, (("a 1", ""), False)),  # an empty piece is a part with no value
    ],
)
def test_claim_parts(tmp_path: Path, claim_text: str, compound: bool, expected: object) -> None:
    assert SEPARATOR == " | "
    assert claim_parts(_one_row_gold(tmp_path, claim_text, compound=compound).items[0]) == expected


# ---------------------------------------------------------------- score_fidelity on fixture FID (AMB4-4 to AMB4-8)

FID_ROWS = (
    # (claim_id, segment, domain, or OPINION for a GENERAL opinion row; is_compound; claim_text)
    ("fd_01", 1, "MARKET_FINANCE", False, "Gold is at USD 2,400 per troy ounce on 2025-03-12"),
    ("fd_02", 2, "MARKET_FINANCE", True, "EUR/USD is 1.08 on 2025-03-12 | GBP/USD is 1.27 on 2025-03-12"),
    ("fd_03", 3, "GENERAL", False, "The ECB is scheduled to meet on 2025-03-20"),
    ("fd_04", 4, "GENERAL", False, ""),
    ("fd_05", 5, "GENERAL", True, "Canberra is the capital of Australia | Canberra has about 400,000 people"),
    ("fd_06", 6, "WEATHER", True, "Oslo is at -3 C and snowing on 2025-03-12"),
    ("fd_07", 7, "WEATHER", False, "Rome is at 20 C | Rome is sunny"),
    ("fd_08", 8, "OPINION", True, "x 9 | y 10"),  # not in-scope CHECKABLE: ignored everywhere
)
REFERENCE = (
    ("Gold is USD 2400 an ounce", 1),
    ("EUR/USD is 1.08", 2),
    ("GBP/USD at 1.27", 2),  # a duplicate on fd_02
    ("the ECB meets on 2025-03-20", 3),
    ("Canberra has 400000 people", 5),
    ("Oslo is cold and snowing", 6),
)


def _fd_gold(root: Path, low: frozenset[str] = frozenset()) -> GoldSet:
    """Fixture FID; the rows named in `low` have confidence_in_label low, every other row high."""
    conversation = make_conversation("syn_fd", [f"synthetic line {n}" for n in range(1, 9)])
    rows = [
        make_item(
            claim_id,
            conversation,
            number,
            "OPINION" if kind == "OPINION" else "CHECKABLE",
            "GENERAL" if kind == "OPINION" else kind,
            confidence="low" if claim_id in low else "high",
            claim_text=text,
            is_compound="yes" if compound else "no",
        )
        for claim_id, number, kind, compound, text in FID_ROWS
    ]
    return load_gold_set(write_version(root, [conversation], rows))


@pytest.fixture
def gold_fd(tmp_path: Path) -> GoldSet:
    return _fd_gold(tmp_path)


def _run(source_id: str, *claims: tuple[str, int], nc: tuple[NonCheckableEntry, ...] = ()) -> dict[str, Extraction]:
    built = tuple(ExtractedClaim(i, text, (seg(number),)) for i, (text, number) in enumerate(claims))
    return {source_id: Extraction(source_id, built, nc)}


def _counts(m: FidelityFigures) -> tuple[object, ...]:
    return (
        m.found_rows,
        m.rows_not_judgeable,
        m.rows_passing,
        m.rows_judgeable,
        m.claim_fidelity,
        m.compound_rows,
        m.compound_rows_found,
        m.parts_not_judgeable,
        m.parts_found,
        m.parts_judgeable,
        m.per_proposition_recall,
        m.flag_separator_disagreements,
    )


def test_reference_run(gold_fd: GoldSet) -> None:
    """fd_01 and fd_05 pass; fd_02 fails although both its parts are found, because values are never pooled across
    claims; fd_03 holds only a date and is not judgeable; fd_06's one part is not found; fd_04 and fd_07 are not
    found; fd_06 and fd_07 are the flag and separator disagreements."""
    m = score_fidelity(gold_fd, _run("syn_fd", *REFERENCE))
    assert _counts(m) == (5, 1, 2, 4, F(1, 2), 3, 3, 1, 3, 4, F(3, 4), 2)
    assert m.high_confidence == FidelityFigures(*_counts(m))  # every row is high


def test_the_high_confidence_figures(tmp_path: Path) -> None:
    """AMB4C-3: the same figures over rows with confidence_in_label high, beside the all-rows figures, as scoring's
    AMB-21: matching is unchanged, so the all-rows figures stay as in the reference run. With fd_01 and fd_02 low, the
    high found rows are fd_03 (not judgeable), fd_05 (passes) and fd_06 (fails); the high compound rows are fd_05 and
    fd_06, whose judgeable parts are found once in two; fd_06 and fd_07 still disagree."""
    m = score_fidelity(_fd_gold(tmp_path, frozenset({"fd_01", "fd_02"})), _run("syn_fd", *REFERENCE))
    assert type(m) is FidelityMetrics and type(m.high_confidence) is FidelityFigures
    assert _counts(m) == (5, 1, 2, 4, F(1, 2), 3, 3, 1, 3, 4, F(3, 4), 2)
    assert _counts(m.high_confidence) == (3, 1, 1, 2, F(1, 2), 2, 2, 1, 1, 2, F(1, 2), 2)


def test_no_high_confidence_row_gives_none(tmp_path: Path) -> None:
    low = frozenset(claim_id for claim_id, *_ in FID_ROWS)
    m = score_fidelity(_fd_gold(tmp_path, low), _run("syn_fd", *REFERENCE))
    assert _counts(m.high_confidence) == (0, 0, 0, 0, None, 0, 0, 0, 0, 0, None, 0)
    assert m.found_rows == 5


def test_a_duplicate_claim_holding_every_value_passes(gold_fd: GoldSet) -> None:
    m = score_fidelity(gold_fd, _run("syn_fd", *REFERENCE, ("EUR/USD 1.08 and GBP/USD 1.27", 2)))
    assert (m.rows_passing, m.claim_fidelity) == (3, F(3, 4))
    assert (m.parts_found, m.parts_judgeable, m.per_proposition_recall) == (3, 4, F(3, 4))


def test_a_failed_conversation_is_empty_output(gold_fd: GoldSet) -> None:
    """AMB4-8: no claims and no found rows; the compound rows stay in the recall denominator, so recall is 0."""
    m = score_fidelity(gold_fd, {"syn_fd": ExtractionFailure("syn_fd", "timeout")})
    assert _counts(m) == (0, 0, 0, 0, None, 3, 0, 1, 0, 4, F(0), 2)


def test_no_compound_rows_gives_none(tmp_path: Path) -> None:
    gold = load_x(tmp_path)
    m = score_fidelity(gold, run_xp(gold))
    assert (m.compound_rows, m.parts_judgeable, m.per_proposition_recall) == (0, 0, None)


def test_found_rows_are_score_runs(tmp_path: Path) -> None:
    gold_plus = load_f(tmp_path / "f", plus=True)
    gold_x = load_x(tmp_path / "x")
    for gold, run in ((gold_plus, run_e1t(gold_plus)), (gold_x, run_xm(gold_x))):
        assert score_fidelity(gold, run).found_rows == score_run(gold, run).found_units  # no flagged groups


def test_results_contract_is_score_runs(gold_fd: GoldSet) -> None:
    run: dict[str, ExtractionResult] = dict(_run("syn_fd", *REFERENCE))
    with pytest.raises(ValueError, match="missing"):
        score_fidelity(gold_fd, {})
    with pytest.raises(ValueError, match="extra"):
        score_fidelity(gold_fd, {**run, "cap_1": Extraction("cap_1", (), ())})
    with pytest.raises(ValueError, match="not a segment"):
        score_fidelity(gold_fd, _run("syn_fd", ("USD 1", 99)))


# ---------------------------------------------------------------- one row, one clause each (AMB4-1 to AMB4-6)


@pytest.mark.parametrize(
    ("gold_text", "claim_text", "passes"),
    [
        ("X is 1.10", "X is 1.1", True),  # value equality
        ("Oslo is at -2 C", "Oslo is at 2 C", True),  # signs are not compared
        ("Rome is at 20 C", "Rome is at 20 F", True),  # units are not compared
        ("revenue is GBP 1.2 million", "GBP 1,200,000", False),  # magnitude words are not converted
        ("revenue is GBP 1.2 million", "GBP 1.2m", True),
        ("The S&P 500 is at 5,000", "S&P 500 at 5000", True),  # other index digits are numbers
        ("The S&P 500 is at 5,000", "the S&P at 5000", False),
        ("The FTSE 100 is at 8,000", "the FTSE is at 8000", True),
        ("The FTSE 100 is at 8,000", "FTSE 250 at 8,000", False),
        ("The FTSE 100 is at 8,000", "index at 8,000 and 100", False),
        ("The Dow Jones Industrial Average is at 34,000", "the Dow is at 34,000", True),
        ("The Dow Jones Industrial Average is at 34,000", "Dow 34,000", False),
        ("The Nasdaq 100 is at 18,000", "Nasdaq 100 at 18000", True),
        ("The Nasdaq 100 is at 18,000", "the Nasdaq Composite is at 18,000", False),
        ("The FTSE 100 is at 8,000", "FTSE-100 at 8000", True),  # AMB4C-1
        ("The Nasdaq Composite is at 18,000", "Nasdaq-100 at 18,000", False),
        ("The Nasdaq-100 is at 18,000", "Nasdaq 100 at 18000", True),
        ("Brent is below USD 80 per barrel on 2025-03-12", "Brent below USD 80 on 2025-03-11", True),  # ISO dates
        ("EUR/USD is 1.08", "EUR/USD 1.08", True),  # a pair is its two codes
        ("EUR/USD is 1.08", "USD/EUR 1.08", True),
        ("EUR/USD is 1.08", "EURUSD 1.08", False),
        ("EUR/USD is 1.08", "eur/usd 1.08", False),
        ("Gold is USD 2,400", "USD 2400, EUR 2200 and 5", True),  # extra values never count
    ],
)
def test_one_row_clause(tmp_path: Path, gold_text: str, claim_text: str, passes: bool) -> None:
    m = score_fidelity(_one_row_gold(tmp_path, gold_text), _run("syn_r", (claim_text, 1)))
    assert (m.found_rows, m.rows_judgeable, m.rows_passing) == (1, 1, int(passes))


def test_a_date_only_part_is_not_judgeable(tmp_path: Path) -> None:
    gold = _one_row_gold(tmp_path, "X is scheduled for 2025-03-20 | X costs USD 5", compound=True)
    m = score_fidelity(gold, _run("syn_r", ("X costs USD 5", 1)))
    assert (m.rows_passing, m.rows_judgeable) == (1, 1)
    assert (m.parts_not_judgeable, m.parts_judgeable, m.parts_found, m.per_proposition_recall) == (1, 1, 1, F(1))


def test_an_empty_claim_text_is_not_judgeable(tmp_path: Path) -> None:
    m = score_fidelity(_one_row_gold(tmp_path, ""), _run("syn_r", ("anything 1", 1)))
    assert (m.found_rows, m.rows_not_judgeable, m.rows_judgeable, m.claim_fidelity) == (1, 1, 0, None)


def test_non_checkable_text_is_never_read(tmp_path: Path) -> None:
    entry = NonCheckableEntry(0, (seg(1),), "opinion", text="USD 2400")
    m = score_fidelity(_one_row_gold(tmp_path, "Gold is USD 2,400"), _run("syn_r", ("gold is up", 1), nc=(entry,)))
    assert (m.found_rows, m.rows_passing, m.claim_fidelity) == (1, 0, F(0))


def test_a_claim_on_another_segment_is_not_matched(tmp_path: Path) -> None:
    m = score_fidelity(_one_row_gold(tmp_path, "Gold is USD 2,400"), _run("syn_r", ("Gold is USD 2400", 2)))
    assert (m.found_rows, m.rows_judgeable, m.claim_fidelity) == (0, 0, None)
