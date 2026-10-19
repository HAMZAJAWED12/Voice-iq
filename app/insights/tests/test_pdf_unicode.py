"""The PDF report survives text outside Latin-1.

The report uses fpdf's core Helvetica font, which covers Latin-1 only. One
character outside it used to fail the whole PDF. Now common punctuation is
mapped to its nearest Latin-1 form and any other such character prints as a
visible placeholder; Latin-1 text prints unchanged.
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

from app.pipeline.orchestrator import VoiceIQOrchestrator, _PipelineState
from app.services.pdf_service import PDFService, to_latin1
from app.utils.job_io import JobIO

MIXED = "It’s 5 € – “done”… یہ ٹیسٹ ہے مرحبا 😀"
LATIN1 = "Café déjà vu: ñ ü ß ½ © ±"


def _report(transcript: str, summary: str = "", topic: str = "") -> bytes:
    return PDFService.generate_pdf_report(
        transcript=transcript,
        speaker_segments=[],
        summary=summary,
        topic=topic,
        conversation_stats={},
        speaker_stats={},
        flags=[{"type": "note", "text": transcript}],
        warnings=["NOTE"],
    )


def _page_text(pdf: bytes) -> str:
    """The text of every compressed page stream, decoded as Latin-1."""
    texts = []
    for raw in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            texts.append(zlib.decompress(raw).decode("latin-1"))
        except zlib.error:
            continue
    return "\n".join(texts)


def test_text_outside_latin1_still_yields_a_pdf():
    pdf = _report(MIXED, summary=MIXED, topic="مرحبا")

    assert pdf.startswith(b"%PDF-")


def test_common_punctuation_is_mapped_to_latin1():
    text = _page_text(_report(MIXED))

    assert 'It\'s 5 EUR - "done"...' in text.replace("\\(", "(").replace("\\)", ")")


def test_other_characters_print_as_a_visible_placeholder():
    text = _page_text(_report("abc یہ xyz"))

    assert "abc ?? xyz" in text


def test_latin1_text_prints_unchanged():
    assert to_latin1(LATIN1) == LATIN1
    assert LATIN1 in _page_text(_report(LATIN1))


def test_every_latin1_character_is_kept():
    printable = "".join(chr(c) for c in list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)))

    assert to_latin1(printable) == printable


def test_the_pipeline_reports_no_pdf_failure_for_non_latin1_text(tmp_path: Path):
    io = JobIO(base_dir=tmp_path / "jobs")
    job = io.init_job("pdf-unicode")
    state = _PipelineState(
        job=job,
        meta={"warnings": [], "timings_ms": {}, "skipped_steps": []},
        job_id="pdf-unicode",
        transcript_text=MIXED,
        summary_text=MIXED,
    )

    VoiceIQOrchestrator(job_io=io)._run_pdf(state)

    assert "PDF_FAILED" not in state.meta["warnings"]
    assert io.load_json(job, "artifacts/report/report.status.json")["status"] == "ok"
    assert io.load_text(job, "artifacts/report/report_base64.txt", default="")
