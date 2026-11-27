"""The demo page and record (demo specification): the golden render, escaping, the self-contained page, the banner,
the run header, the per-claim display, the footer and the JSON record.

No test sends through a real transport or starts a process. The golden files hold `render_page(golden_run())` and
`record_bytes(golden_run())`, compared over their LF bytes so that a CRLF checkout still matches. After a deliberate
change to the page, write those two values to the golden files again and read the diff before keeping it.
"""

from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.evaluation.bakeoff.frozen import lf_bytes
from app.evaluation.bakeoff.probe import VERDICTS
from app.evaluation.bakeoff.records import record_loads
from app.evaluation.demo.render import (
    BANNER,
    FAILED_CALL_NOTE,
    LABEL,
    NOTHING_SENT,
    DemoRun,
    no_verdict_text,
    record_bytes,
    render_page,
)
from app.evaluation.tests._client_fixtures import block_sockets, error_response
from app.evaluation.tests._demo_fixtures import (
    GOLDEN_DIR,
    LF,
    count,
    extraction_json,
    golden_run,
    ok,
    probe_json,
    scripted_header,
    small_bundles,
    small_run,
    small_transcripts,
    source,
)
from app.evaluation.tests._runner_fixtures import block_processes

ALLOWED_TAGS = frozenset(
    {
        "html",
        "head",
        "meta",
        "title",
        "style",
        "body",
        "div",
        "strong",
        "span",
        "header",
        "h1",
        "h2",
        "h3",
        "small",
        "table",
        "tr",
        "th",
        "td",
        "p",
        "code",
        "main",
        "section",
        "a",
        "ul",
        "li",
        "footer",
    }
)
ALLOWED_ATTRIBUTES = frozenset({"lang", "charset", "http-equiv", "content", "name", "class", "id", "href"})
CSP = (
    "meta",
    [("http-equiv", "Content-Security-Policy"), ("content", "default-src 'none'; style-src 'unsafe-inline'")],
)
HOSTILE = "<script>alert('x')</script><img src=x onerror=alert(1)><a href='javascript:alert(2)'>go</a>"


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture
def golden() -> DemoRun:
    return golden_run()


class _Tags(HTMLParser):
    """Every start tag of a page, with its attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


def tags_of(page: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    parser = _Tags()
    parser.feed(page)
    parser.close()
    return parser.tags


def text_of(run: DemoRun) -> str:
    return render_page(run).decode("utf-8")


# ---------------------------------------------------------------- the golden render


def test_the_golden_render_is_byte_identical(golden: DemoRun) -> None:
    assert render_page(golden) == lf_bytes((GOLDEN_DIR / "page.html").read_bytes())
    assert record_bytes(golden) == lf_bytes((GOLDEN_DIR / "record.json").read_bytes())


def test_the_golden_run_shows_every_case_a_page_can_show(golden: DemoRun) -> None:
    claims = [claim for transcript in golden.transcripts for claim in transcript.claims]
    assert {claim.output.verdict for claim in claims if claim.output is not None} == set(VERDICTS)
    assert {(claim.status, claim.fault) for claim in claims if claim.status != "verdict"} == {
        ("leaked", None),
        ("no_evidence_fixture", None),
        ("no_verdict", "cut_off"),
        ("no_verdict", "invalid_output"),
    }
    assert any(claim.output is not None and not claim.cited for claim in claims)
    calls = [result.call for result in golden.transcripts] + [claim.call for claim in claims]
    retries = {attempt.retry_type for call in calls if call is not None for attempt in call.attempts}
    assert retries == {"none", "repair", "same_request"}
    page = text_of(golden)
    shown = ("leaked, no verdict", "no evidence fixture", no_verdict_text("cut_off"), no_verdict_text("invalid_output"))
    for text in (*shown, "not pinned", "Not cited", "Cited sources", "Not checkable"):
        assert html.escape(text) in page


# ---------------------------------------------------------------- escaping


def test_hostile_markup_from_the_fixtures_and_the_model_is_escaped(tmp_path: Path) -> None:
    transcripts, bundles = small_transcripts(), small_bundles()
    transcripts["transcripts"][0]["title"] = "Title " + HOSTILE
    transcripts["transcripts"][0]["request"]["segments"][1]["text"] += " " + HOSTILE
    bundles["bundles"][0]["sources"][0] = {**source("src_1", "Source " + HOSTILE), "title": "Title " + HOSTILE}
    bundles["bundles"][0]["sources"][1]["title"] = "Uncited " + HOSTILE
    control = chr(27) + "[31m" + chr(0x2028) + chr(0)
    entry = ("opinion", ["syn_s01"], "Entry " + HOSTILE, "Why " + HOSTILE)
    run = small_run(
        tmp_path,
        ok(extraction_json([("Claim " + HOSTILE, ["syn_s02"])], [entry])),
        ok(probe_json("SUPPORTED", "Reason " + HOSTILE + control, "src_1")),
        header=scripted_header(source_revision="rev " + HOSTILE),
        transcripts=transcripts,
        bundles=bundles,
    )
    page = text_of(run)
    assert "<script" not in page.lower() and "<img" not in page.lower()
    assert page.count(html.escape(HOSTILE)) == 10
    assert f"({html.escape('Why ' + HOSTILE)})</li>" in page
    assert f"Not cited: src_2 ({html.escape('Uncited ' + HOSTILE)})</p>" in page
    assert not {chr(27), chr(0x2028), chr(0), chr(13)} & set(page)
    tags = tags_of(page)
    assert {tag for tag, _ in tags} <= ALLOWED_TAGS
    assert all(value.startswith("#") for _, attrs in tags for name, value in attrs if name == "href" and value)


# ---------------------------------------------------------------- a self-contained static page


def test_the_page_is_self_contained_static_html(golden: DemoRun) -> None:
    raw = render_page(golden)
    page = raw.decode("utf-8")
    tags = tags_of(page)
    assert {tag for tag, _ in tags} <= ALLOWED_TAGS
    assert CSP in tags
    attributes = [(name, value or "") for _, attrs in tags for name, value in attrs]
    assert {name for name, _ in attributes} <= ALLOWED_ATTRIBUTES
    anchors = [value for name, value in attributes if name == "id"]
    hrefs = [value for name, value in attributes if name == "href"]
    assert len(anchors) == len(set(anchors))
    assert hrefs and all(href.startswith("#") and href[1:] in anchors for href in hrefs)
    for needle in ("url(", "@import", "<script", "<link", "src=", "@font-face"):
        assert needle not in page.lower()
    assert raw.endswith(LF.encode()) and not raw.endswith((LF + LF).encode()) and chr(13).encode() not in raw
    assert all(line == line.rstrip() for line in page.split(LF))


# ---------------------------------------------------------------- the banner and the run header


def test_the_banner_and_the_run_header_say_what_the_page_is(golden: DemoRun) -> None:
    for words in (
        "synthetic data",
        "fictional sources",
        "verdict quality not measured",
        "no d6 standing",
        "not advice",
    ):
        assert words in BANNER.lower()
    assert LABEL == "no bake-off, no pin"
    assert "INSUFFICIENT_EVIDENCE" in FAILED_CALL_NOTE and "invariant 4" in FAILED_CALL_NOTE
    page = text_of(golden)
    header = golden.header
    for text in (BANNER, LABEL, FAILED_CALL_NOTE, "not pinned", "scripted render", header.model_id):
        assert html.escape(text) in page
    for text in (header.prompts_sha256, header.date, header.launch_line, header.llama_build, header.gguf_sha256):
        assert f"<td><code>{html.escape(text)}</code></td>" in page
    assert f"<td><code>{html.escape(header.source_revision)}</code></td>" in page
    banner = f'<div class="banner"><strong>{html.escape(BANNER)}</strong><span class="label">{html.escape(LABEL)}'
    assert page.index(banner) < page.index("<main>")
    label = page[page.index(banner) + len(banner) : page.index("</span></div>")]
    assert html.escape("; scripted render: no model ran") in label
    for name, sha256 in golden.fixtures_sha256.items():
        assert f"{html.escape(name)} {sha256}" in page[: page.index("<main>")]


def test_a_live_header_says_live_and_keeps_not_pinned(tmp_path: Path) -> None:
    launch = {"source_revision": "rev-4b", "llama_build": "b1-27b7a8a", "launch_line": "/opt/llama/llama-server -m x"}
    header = scripted_header(run_kind="live", gguf_sha256="c" * 64, **launch)
    page = text_of(small_run(tmp_path, ok(extraction_json()), header=header))
    assert "live local run" in page and "scripted render" not in page and "not pinned" in page
    assert f'<span class="label">{html.escape(LABEL)}</span></div>' in page
    assert all(html.escape(value) in page for value in (*launch.values(), "c" * 64))


@pytest.mark.parametrize(("changes", "message"), [({"run_kind": "dry"}, "run_kind"), ({"model_id": " "}, "blank")])
def test_a_header_refuses_an_unknown_run_kind_or_a_blank_field(changes: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        scripted_header(**changes)


# ---------------------------------------------------------------- claims, entries and the footer


def test_each_claim_shows_its_badge_segments_and_note_and_each_entry_its_links(golden: DemoRun) -> None:
    page = text_of(golden)
    for result in golden.transcripts:
        transcript_id = result.transcript.transcript_id
        for claim in result.claims:
            assert claim.item_id == f"{transcript_id}.c{claim.claim.index + 1:02d}"
            output = claim.output
            style = f"v-{output.verdict.lower()}" if output is not None else f"s-{claim.status}"
            block = page[page.index(f'<div class="claim" id="{claim.item_id}">') :]
            block = block[: block.index("</div>")]
            assert f'<span class="badge {style}">' in block
            assert output is None or f'<span class="badge {style}">{output.verdict}</span>' in block
            links = [f'<a href="#{transcript_id}-{sid}">{sid}</a>' for sid in claim.claim.segment_ids]
            assert f"<p>Segments: {', '.join(links)}" in block
            note = NOTHING_SENT.get(claim.status)
            assert f"<p>{html.escape(note)}</p>" in block if note is not None else "Nothing was sent" not in block
        section = page[page.index(f'<section id="{transcript_id}">') :]
        section = section[: section.index("</section>")]
        entries = () if result.extraction is None else result.extraction.non_checkable
        checkable = section[section.index("<h3>Not checkable</h3>") :]
        for entry in entries:
            links_text = ", ".join(f'<a href="#{transcript_id}-{sid}">{sid}</a>' for sid in entry.segment_ids)
            assert f"<li><strong>{entry.reason_kind}</strong> on {links_text}" in checkable
        out = sum(row.segment_id in result.transcript.out_of_scope for row in result.transcript.segments)
        segments = section[: section.index("<h3>Claims</h3>")]
        assert segments.count('<span class="scope">out of scope</span>') == out > 0


def test_an_unknown_cited_id_is_never_shown(tmp_path: Path) -> None:
    unknown = probe_json("SUPPORTED", "Sources one and nine.", "src_1", "src_9")
    claims = extraction_json([("The Brask valley tram opened in 2030.", ["syn_s02"])])
    page = text_of(small_run(tmp_path, ok(claims), ok(unknown), count(), ok(unknown)))
    assert "src_9" not in page and html.escape(no_verdict_text("invalid_output")) in page


def test_a_run_fault_row_shows_the_attempt_already_made(tmp_path: Path) -> None:
    """The repair's token count failed after attempt 1 reached the model: the footer and the record keep it."""
    claims = extraction_json([("The tram opened in 2030.", ["syn_s02"]), ("Rainfall reached 40 mm.", ["syn_s03"])])
    steps = (ok(claims), ok("no json"), error_response(500, "server_error", "no count"))
    run = small_run(tmp_path, *steps, ok(probe_json("SUPPORTED", "Rain source.", "src_1")))
    (attempt,) = run.transcripts[0].claims[0].fault_attempts
    cells = ("syn_t1.c01", "probe", "run fault: ServerCheckError", "1", "0", "0", "invalid_output")
    time_cell = f"<td>{attempt.wall_ns / 1_000_000:.1f}</td></tr>"
    assert "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + time_cell in text_of(run)
    value = record_loads(record_bytes(run)[:-1])
    first = value["transcripts"][0]["claims"][0]
    kept = [(row["attempt"], row["content"]) for row in first["fault_attempts"]]
    assert (first["call"], first["fault"], kept) == (None, "ServerCheckError", [(1, "no json")])
    assert first["fault_attempts"][0]["request_sha256"] == attempt.request_sha256


def test_a_lone_surrogate_in_a_model_text_is_shown_as_its_escape(tmp_path: Path) -> None:
    lone = chr(0xD800)
    claims = extraction_json([("Claim " + lone, ["syn_s02"])], [("opinion", ["syn_s01"], "Entry " + lone)])
    run = small_run(tmp_path, ok(claims))
    page = text_of(run)
    assert html.escape(no_verdict_text("UnicodeEncodeError")) in page
    assert "Claim " + repr(lone)[1:-1] in page and "Entry " + repr(lone)[1:-1] in page
    assert record_loads(record_bytes(run)[:-1])["transcripts"][0]["claims"][0]["text"] == "Claim " + lone


def test_a_failed_extraction_shows_no_claims_with_its_fault(tmp_path: Path) -> None:
    page = text_of(small_run(tmp_path, ok("not json"), count(), ok("not json either")))
    assert html.escape("no claims (extraction call failed: invalid_output)") in page


def test_the_footer_lists_every_call_with_its_time_attempts_and_repairs(golden: DemoRun) -> None:
    page = text_of(golden)
    footer = page[page.index("<footer>") :]
    calls = [result.call for result in golden.transcripts]
    calls += [claim.call for result in golden.transcripts for claim in result.claims]
    recorded = [call for call in calls if call is not None]
    assert len(recorded) == 3 + 13
    for call in recorded:
        row = next(line for line in footer.split(LF) if f"<td>{html.escape(call.item_id)}</td>" in line)
        repairs = sum(attempt.retry_type == "repair" for attempt in call.attempts)
        same = sum(attempt.retry_type == "same_request" for attempt in call.attempts)
        assert f"<td>{call.total_wall_ns / 1_000_000:.1f}</td>" in row
        assert f"<td>{len(call.attempts)}</td><td>{repairs}</td><td>{same}</td>" in row


# ---------------------------------------------------------------- the record


def test_the_record_is_one_json_line_holding_the_whole_run(golden: DemoRun) -> None:
    raw = record_bytes(golden)
    assert raw.endswith(LF.encode()) and raw.count(LF.encode()) == 1
    value = record_loads(raw[:-1])
    assert (value["record"], value["schema"], value["label"], value["banner"]) == ("demo", 1, LABEL, BANNER)
    assert (value["model_id"], value["pinned"], value["run_kind"]) == ("Qwen/Qwen3-4B", False, "scripted")
    assert value["fixtures_sha256"] == dict(golden.fixtures_sha256)
    shown = [(row["status"], row["verdict"], row["fault"]) for item in value["transcripts"] for row in item["claims"]]
    expected = [
        (claim.status, None if claim.output is None else claim.output.verdict, claim.fault)
        for result in golden.transcripts
        for claim in result.claims
    ]
    assert shown == expected
    rows = [row for item in value["transcripts"] for row in item["claims"]]
    claims = [claim for result in golden.transcripts for claim in result.claims]
    assert [(row["text"], row["reason"]) for row in rows] == [
        (claim.claim.text, None if claim.output is None else claim.output.reason) for claim in claims
    ]
    entries = [(row["reason_kind"], row["text"]) for item in value["transcripts"] for row in item["non_checkable"]]
    assert entries == [
        (entry.reason_kind, entry.text)
        for result in golden.transcripts
        for entry in (() if result.extraction is None else result.extraction.non_checkable)
    ]
    assert all((row["call"] is None) == (row["status"] in ("leaked", "no_evidence_fixture")) for row in rows)
    assert all(attempt["request_sha256"] for row in rows if row["call"] for attempt in row["call"]["attempts"])
