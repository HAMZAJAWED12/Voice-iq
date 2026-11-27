"""The demo page and record: one static HTML page and one JSON record of a demo run.

The page is self-contained: no script, no external font, link or image, and a content security policy that would
refuse them anyway. Every text on it is escaped, and a character that does not print is shown as its escape, so
nothing a fixture or the model wrote can become markup. The banner says what the demo is not, and on a scripted
render that no model ran; the run header says what ran, with the fixture hashes; each transcript shows its segments,
its claims linked to their segments and its non-checkable entries; and the footer lists every call with its time,
attempts and repairs, a run fault with the attempts it had made. A failed call shows no verdict, and the page says
that this is not the v2 contract's fallback (PHASE-0 §2.2 invariant 4). The record holds the same run as one JSON
line in the run store's form, with every call record whole and every attempt a run fault kept.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Literal

from app.evaluation.bakeoff.client import AttemptRecord, CallRecord
from app.evaluation.bakeoff.records import call_to_dict, record_json
from app.evaluation.demo.pipeline import ClaimResult, Source, Transcript, TranscriptResult

LABEL = "no bake-off, no pin"
BANNER = "Synthetic data. Fictional sources. Verdict quality not measured. No D6 standing. Not advice."
FAILED_CALL_NOTE = (
    "A call that failed, or whose output stayed invalid after its one repair, shows no verdict and its fault kind. "
    "That is not the v2 contract's INSUFFICIENT_EVIDENCE fallback (PHASE-0 section 2.2, invariant 4): the demo "
    "shows the failure as it happened."
)
RUN_KINDS: Mapping[str, str] = MappingProxyType(
    {"live": "live local run", "scripted": "scripted render: no model ran (MockTransport)"}
)
NOTHING_SENT: Mapping[str, str] = MappingProxyType(
    {
        "leaked": "Nothing was sent: the claim sits on an out-of-scope segment.",
        "no_evidence_fixture": "Nothing was sent: no bundle anchors these segments.",
    }
)
TIME_NOTE = "Time is the client's wall time for the call, both attempts added (S2 section 3.8)."
_LF = chr(10)
_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoiceIQ demo: claims to cited verdicts (no bake-off, no pin)</title>
<style>
body{margin:0;font-family:system-ui,sans-serif;font-size:15px;line-height:1.45;color:#1d232a;background:#f5f6f8}
.banner{position:sticky;top:0;background:#8a1c1c;color:#fff;padding:10px 24px}
.label{margin-left:10px;padding:1px 8px;border:1px solid #fff;border-radius:4px}
header,main,footer{max-width:1080px;margin:0 auto;padding:8px 24px}
section{background:#fff;border:1px solid #dde1e6;border-radius:6px;padding:4px 18px 14px;margin:18px 0}
table{border-collapse:collapse;width:100%;margin:6px 0;background:#fff}
th,td{border:1px solid #dde1e6;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#eef0f3;font-weight:600}
code{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}
.note{background:#fff7e0;border-left:4px solid #d9a400;padding:8px 12px}
.claim{border-left:4px solid #9aa4af;padding:2px 12px;margin:12px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:13px;font-weight:600}
.v-supported{background:#d6f0dc;color:#14532d}
.v-contradicted{background:#fadad7;color:#7f1d1d}
.v-partially_supported{background:#fdecc8;color:#7a4a00}
.v-insufficient_evidence{background:#e3e7ec;color:#334155}
.s-no_verdict{background:#ececec;color:#444;border:1px dashed #888}
.s-leaked{background:#ffe2c2;color:#7a3300}
.s-no_evidence_fixture{background:#eef2ff;color:#3730a3}
.scope{font-size:12px;color:#7a3300;border:1px solid #e0a070;border-radius:4px;padding:0 4px}
.source{white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;padding:6px 8px;margin:4px 0}
</style>
</head>
<body>"""


@dataclass(frozen=True, slots=True)
class RunHeader:
    """What ran. A scripted render launched nothing, so its launch fields say so; the model is never pinned here."""

    run_kind: Literal["live", "scripted"]
    model_id: str
    source_revision: str
    gguf_sha256: str
    prompts_sha256: str
    llama_build: str
    launch_line: str
    date: str

    def __post_init__(self) -> None:
        if self.run_kind not in RUN_KINDS:
            raise ValueError(f"RunHeader.run_kind must be one of {tuple(RUN_KINDS)}, not {self.run_kind!r}")
        values = (self.model_id, self.source_revision, self.gguf_sha256, self.prompts_sha256, self.llama_build)
        if not all(isinstance(value, str) and value.strip() for value in (*values, self.launch_line, self.date)):
            raise ValueError("RunHeader fields must be non-blank strings")


@dataclass(frozen=True, slots=True)
class DemoRun:
    header: RunHeader
    fixtures_sha256: Mapping[str, str]
    transcripts: tuple[TranscriptResult, ...]


def no_verdict_text(fault: str) -> str:
    return f"no verdict (call failed: {fault})"


def banner_label(run_kind: str) -> str:
    """The label beside the banner. A scripted render says on its face that no model ran."""
    return LABEL if run_kind == "live" else f"{LABEL}; {RUN_KINDS[run_kind]}"


def shown_as(claim: ClaimResult) -> str:
    """The badge text: the verdict, or what stands in its place."""
    output = claim.output
    if output is not None:
        return output.verdict
    if claim.status == "leaked":
        return "leaked, no verdict"
    if claim.status == "no_evidence_fixture":
        return "no evidence fixture"
    return no_verdict_text(claim.fault or "unknown")


_Call = tuple[str, str, CallRecord | None, str | None, tuple[AttemptRecord, ...]]


def _calls(transcripts: tuple[TranscriptResult, ...]) -> list[_Call]:
    """Every call in order, as (item id, kind, record, fault, attempts a run fault made); a run fault leaves no
    record."""
    calls: list[_Call] = []
    for result in transcripts:
        calls.append((result.transcript.transcript_id, "extraction", result.call, result.fault, result.fault_attempts))
        sent = (claim for claim in result.claims if claim.status in ("verdict", "no_verdict"))
        calls += [(claim.item_id, "probe", claim.call, claim.fault, claim.fault_attempts) for claim in sent]
    return calls


# ---------------------------------------------------------------- the record


def _claim_value(claim: ClaimResult) -> dict[str, Any]:
    output = claim.output
    return {
        "bundle_id": None if claim.bundle is None else claim.bundle.bundle_id,
        "call": None if claim.call is None else call_to_dict(claim.call),
        "cited_source_ids": [source.source_id for source in claim.cited],
        "fault": claim.fault,
        "fault_attempts": [asdict(attempt) for attempt in claim.fault_attempts],
        "index": claim.claim.index,
        "item_id": claim.item_id,
        "reason": None if output is None else output.reason,
        "segment_ids": list(claim.claim.segment_ids),
        "shown_as": shown_as(claim),
        "status": claim.status,
        "text": claim.claim.text,
        "uncited_source_ids": [source.source_id for source in claim.uncited],
        "verdict": None if output is None else output.verdict,
    }


def _transcript_value(result: TranscriptResult) -> dict[str, Any]:
    transcript, extraction = result.transcript, result.extraction
    entries = () if extraction is None else extraction.non_checkable
    return {
        "claims": [_claim_value(claim) for claim in result.claims],
        "extraction_call": None if result.call is None else call_to_dict(result.call),
        "fault": result.fault,
        "fault_attempts": [asdict(attempt) for attempt in result.fault_attempts],
        "non_checkable": [
            {
                "index": entry.index,
                "reason": entry.reason,
                "reason_kind": entry.reason_kind,
                "segment_ids": list(entry.segment_ids),
                "speaker_id": entry.speaker_id,
                "text": entry.text,
            }
            for entry in entries
        ],
        "out_of_scope_segment_ids": sorted(transcript.out_of_scope),
        "segments": [segment.request_row() for segment in transcript.segments],
        "title": transcript.title,
        "transcript_id": transcript.transcript_id,
    }


def record_value(run: DemoRun) -> dict[str, Any]:
    """The run as one JSON value: the labels, the header, the fixture hashes and every transcript, claim and call."""
    header = run.header
    return {
        "banner": BANNER,
        "date": header.date,
        "failed_call_note": FAILED_CALL_NOTE,
        "fixtures_sha256": dict(run.fixtures_sha256),
        "gguf_sha256": header.gguf_sha256,
        "label": LABEL,
        "launch_line": header.launch_line,
        "llama_build": header.llama_build,
        "model_id": header.model_id,
        "pinned": False,
        "prompts_sha256": header.prompts_sha256,
        "record": "demo",
        "run_kind": header.run_kind,
        "schema": 1,
        "source_revision": header.source_revision,
        "transcripts": [_transcript_value(result) for result in run.transcripts],
    }


def record_bytes(run: DemoRun) -> bytes:
    """The record file: the run store's one-line JSON form (ASCII, sorted keys) and one LF."""
    return record_json(record_value(run)) + _LF.encode("ascii")


# ---------------------------------------------------------------- the page


def _e(value: object) -> str:
    """Escaped text: a character that does not print becomes its escape, then HTML escaping, quotes included."""
    shown = "".join(char if char.isprintable() else repr(char)[1:-1] for char in str(value))
    return html.escape(shown, quote=True)


def _anchor(transcript: Transcript, segment_id: str) -> str:
    return _e(f"{transcript.transcript_id}-{segment_id}")


def _header(header: RunHeader, fixtures_sha256: Mapping[str, str]) -> list[str]:
    fixtures = "; ".join(f"{name} {sha256}" for name, sha256 in sorted(fixtures_sha256.items()))
    rows = (
        ("Run", RUN_KINDS[header.run_kind]),
        ("Model", header.model_id),
        ("Pin", "not pinned"),
        ("Source revision", header.source_revision),
        ("GGUF SHA-256", header.gguf_sha256),
        ("Prompt-and-retry hash", header.prompts_sha256),
        ("Fixture SHA-256", fixtures),
        ("llama.cpp build", header.llama_build),
        ("Launch line", header.launch_line),
        ("Date", header.date),
    )
    lines = ["<header>", "<h1>Claims to cited verdicts</h1>", '<table class="run">']
    lines += [f"<tr><th>{_e(name)}</th><td><code>{_e(value)}</code></td></tr>" for name, value in rows]
    return [*lines, "</table>", f'<p class="note">{_e(FAILED_CALL_NOTE)}</p>', "</header>"]


def _sources(sources: tuple[Source, ...]) -> list[str]:
    lines = ["<ul>"]
    for source in sources:
        lines.append(f"<li><strong>{_e(source.source_id)}</strong> {_e(source.title)}")
        lines.append(f'<div class="source">{_e(source.text)}</div></li>')
    return [*lines, "</ul>"]


def _links(transcript: Transcript, segment_ids: tuple[str, ...]) -> str:
    return ", ".join(f'<a href="#{_anchor(transcript, sid)}">{_e(sid)}</a>' for sid in segment_ids)


def _claim(transcript: Transcript, claim: ClaimResult) -> list[str]:
    output = claim.output
    style = f"v-{output.verdict.lower()}" if output is not None else f"s-{claim.status}"
    bundle = "" if claim.bundle is None else f"; bundle {_e(claim.bundle.bundle_id)}"
    lines = [
        f'<div class="claim" id="{_e(claim.item_id)}">',
        f'<p><span class="badge {_e(style)}">{_e(shown_as(claim))}</span> {_e(claim.claim.text)}</p>',
        f"<p>Segments: {_links(transcript, claim.claim.segment_ids)}{bundle}</p>",
    ]
    if claim.status in NOTHING_SENT:
        lines.append(f"<p>{_e(NOTHING_SENT[claim.status])}</p>")
    if output is not None:
        lines.append(f"<p>Reason: {_e(output.reason)}</p>")
        lines += ["<p>Cited sources:</p>", *_sources(claim.cited)] if claim.cited else ["<p>Cited sources: none.</p>"]
    if claim.uncited:
        listed = "; ".join(f"{_e(source.source_id)} ({_e(source.title)})" for source in claim.uncited)
        lines.append(f"<p>Not cited: {listed}</p>")
    return [*lines, "</div>"]


def _transcript(result: TranscriptResult) -> list[str]:
    transcript = result.transcript
    lines = [
        f'<section id="{_e(transcript.transcript_id)}">',
        f"<h2>{_e(transcript.title)} <small>{_e(transcript.transcript_id)}</small></h2>",
        "<h3>Segments</h3>",
        '<table class="segments">',
        "<tr><th>Segment</th><th>Speaker</th><th>Time (s)</th><th>Text</th></tr>",
    ]
    for segment in transcript.segments:
        scope = ' <span class="scope">out of scope</span>' if segment.segment_id in transcript.out_of_scope else ""
        times = f"{segment.start:.1f}-{segment.end:.1f}"
        lines.append(
            f'<tr id="{_anchor(transcript, segment.segment_id)}"><td>{_e(segment.segment_id)}</td>'
            f"<td>{_e(segment.speaker_id)}</td><td>{times}</td><td>{_e(segment.text)}{scope}</td></tr>"
        )
    lines += ["</table>", "<h3>Claims</h3>"]
    if result.fault is not None:
        failed = f"no claims (extraction call failed: {result.fault})"
        lines.append(f'<p><span class="badge s-no_verdict">{_e(failed)}</span></p>')
    elif not result.claims:
        lines.append("<p>No claims extracted.</p>")
    for claim in result.claims:
        lines += _claim(transcript, claim)
    lines.append("<h3>Not checkable</h3>")
    entries = () if result.extraction is None else result.extraction.non_checkable
    if not entries:
        lines.append("<p>None.</p>")
    else:
        lines.append("<ul>")
        for entry in entries:
            text = "" if entry.text is None else f": {_e(entry.text)}"
            reason = "" if entry.reason is None else f" ({_e(entry.reason)})"
            kind = _e(entry.reason_kind)
            lines.append(f"<li><strong>{kind}</strong> on {_links(transcript, entry.segment_ids)}{text}{reason}</li>")
        lines.append("</ul>")
    return [*lines, "</section>"]


def _footer(transcripts: tuple[TranscriptResult, ...]) -> list[str]:
    lines = [
        "<footer>",
        "<h2>Calls</h2>",
        f"<p>{_e(TIME_NOTE)}</p>",
        '<table class="calls">',
        "<tr><th>Call</th><th>Kind</th><th>Outcome</th><th>Attempts</th><th>Repairs</th>"
        "<th>Same-request retries</th><th>Attempt results</th><th>Time (ms)</th></tr>",
    ]
    for item_id, kind, record, fault, fault_attempts in _calls(transcripts):
        attempts = fault_attempts if record is None else record.attempts
        cells = (
            item_id,
            kind,
            f"run fault: {fault}" if record is None else record.outcome,
            str(len(attempts)),
            str(sum(attempt.retry_type == "repair" for attempt in attempts)),
            str(sum(attempt.retry_type == "same_request" for attempt in attempts)),
            ", ".join(attempt.failure_kind or "valid" for attempt in attempts) or "-",
            f"{sum(attempt.wall_ns for attempt in attempts) / 1_000_000:.1f}" if attempts else "-",
        )
        lines.append("<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in cells) + "</tr>")
    return [*lines, "</table>", "</footer>"]


def render_page(run: DemoRun) -> bytes:
    """The page as UTF-8 with LF line ends, one final LF and no trailing spaces."""
    label = banner_label(run.header.run_kind)
    lines = [_HEAD, f'<div class="banner"><strong>{_e(BANNER)}</strong><span class="label">{_e(label)}</span></div>']
    lines += _header(run.header, run.fixtures_sha256)
    lines.append("<main>")
    for result in run.transcripts:
        lines += _transcript(result)
    lines += ["</main>", *_footer(run.transcripts), "</body>", "</html>"]
    return (_LF.join(lines) + _LF).encode("utf-8")
