"""Test helpers and synthetic data for the demo tests (demo specification; S2 §3.12). Data is synthetic, never gold.

No demo test sends through a real transport or starts a process. The model is a `ScriptedServer` behind
`httpx.MockTransport`, and the live command line runs on the H3 `FakeHost`; each demo test module wraps
`block_sockets` and `block_processes` in its own autouse fixture. The small fixture is invented here for the tests.
The golden run plays scripted answers over the package fixtures, so its page shows every case a page can show.
Call times come from `StepClock`, not `FakeClock`, so that they differ between calls yet repeat exactly.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from app.evaluation.bakeoff.client import RepairTemplate
from app.evaluation.bakeoff.inputs import PromptSet
from app.evaluation.demo import pipeline
from app.evaluation.demo.render import DemoRun, RunHeader
from app.evaluation.tests._client_fixtures import ScriptedServer, chat_response, input_tokens_response, make_client
from app.evaluation.tests._h5_fixtures import pretty_json_bytes

LF = chr(10)
FIXTURES_DIR = Path(pipeline.__file__).resolve().parent / "fixtures"
GOLDEN_DIR = Path(__file__).resolve().parent / "demo_golden"
MARK = pipeline.SOURCE_MARK
SCRIPTED = "none: scripted render, no model ran"
SCRIPTED_FIELDS = {
    "source_revision": SCRIPTED + " (no source revision)",
    "gguf_sha256": SCRIPTED + " (no model file)",
    "llama_build": SCRIPTED + " (no server build)",
    "launch_line": SCRIPTED + " (nothing launched)",
}
SCRIPTED_DATE = "2026-11-27T12:00:00Z"

# Synthetic stand-ins for the frozen prompts, in the H5 file rules: the demo itself holds no wording.
DEMO_PROMPTS = PromptSet(
    "Synthetic demo test prompt: list the checkable claims.",
    LF.join(("Synthetic demo test template.", "TRANSCRIPT START", "<<INPUT_JSON>>", "TRANSCRIPT END")),
    "Synthetic demo test prompt: judge the claim against the sources.",
    LF.join(("Synthetic demo test template.", "CLAIM START", "<<INPUT_JSON>>", "CLAIM END")),
    RepairTemplate(LF.join(("Synthetic demo test repair.", "<<VALIDATION_ERRORS>>", "Answer again."))),
)


def read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


def segment(number: int, text: str, speaker: str) -> dict[str, Any]:
    start = 4.0 * (number - 1)
    return {
        "asrConfidence": 0.92,
        "end": start + 3.5,
        "segmentId": f"syn_s{number:02d}",
        "speakerId": speaker,
        "start": start,
        "text": text,
    }


def source(source_id: str, text: str = "A synthetic test source about the Brask valley tram.") -> dict[str, str]:
    return {"sourceId": source_id, "text": f"{MARK} {text}", "title": f"Synthetic test source {source_id}"}


SMALL_TEXTS = (
    "Welcome to the synthetic Brask valley test call for the demo.",
    "The Brask valley tram opened in 2030 with nine stops.",
    "Brask valley rainfall reached 40 millimetres on the fourth.",
    "The Brask cooperative sold 120 tonnes of barley last season.",
    "My synthetic test doctor says tram noise is ruining my sleep.",
    "The notes read <b>closed</b> & <script>alert('brask')</script> for an hour.",
)


def small_transcripts() -> dict[str, Any]:
    """One transcript of six segments: syn_s05 is out of scope and syn_s06 holds markup-like text."""
    segments = [segment(number, text, f"SPEAKER_0{number % 2}") for number, text in enumerate(SMALL_TEXTS, start=1)]
    transcript = {
        "outOfScopeSegmentIds": ["syn_s05"],
        "request": {"language": "en", "segments": segments},
        "title": "Synthetic small test call",
        "transcriptId": "syn_t1",
    }
    return {"format": pipeline.FORMAT_TRANSCRIPTS, "transcripts": [transcript]}


def small_bundles() -> dict[str, Any]:
    """Two bundles whose anchors overlap on syn_s02, so the mapping rule's "first" is visible."""
    first = [source("src_1"), source("src_2", "A second synthetic source about the tram stops.")]
    second = [source("src_1", "A synthetic source about Brask valley rainfall.")]
    return {
        "bundles": [
            {"anchors": ["syn_s02"], "bundleId": "syn_b1", "sources": first, "transcriptId": "syn_t1"},
            {"anchors": ["syn_s02", "syn_s03"], "bundleId": "syn_b2", "sources": second, "transcriptId": "syn_t1"},
        ],
        "format": pipeline.FORMAT_BUNDLES,
    }


def write_fixtures(
    directory: Path, transcripts: dict[str, Any] | None = None, bundles: dict[str, Any] | None = None
) -> str:
    """Write the two fixture files in their canonical form, by default the small fixture."""
    directory.mkdir(parents=True, exist_ok=True)
    values = {
        pipeline.TRANSCRIPTS_FILE: small_transcripts() if transcripts is None else transcripts,
        pipeline.BUNDLES_FILE: small_bundles() if bundles is None else bundles,
    }
    for name, value in values.items():
        (directory / name).write_bytes(pretty_json_bytes(value))
    return str(directory)


def extraction_json(
    claims: Sequence[tuple[str, Sequence[str]]] = (),
    non_checkable: Sequence[tuple[Any, ...]] = (),
) -> str:
    """A model extraction in the PHASE-0 §2.2 shape: claims as (text, segment ids), entries as (kind, ids, text) or
    (kind, ids, text, reason)."""
    entries: list[dict[str, Any]] = []
    for kind, ids, text, *reason in non_checkable:
        entry: dict[str, Any] = {"reasonKind": kind, "segmentIds": list(ids)}
        if text is not None:
            entry["text"] = text
        if reason:
            entry["reason"] = reason[0]
        entries.append(entry)
    rows = [{"segmentIds": list(ids), "text": text} for text, ids in claims]
    return json.dumps({"claims": rows, "nonCheckable": entries})


def probe_json(verdict: str, reason: str, *source_ids: str) -> str:
    """A model probe output: the verdict, the reason and the cited ids."""
    citations = [{"sourceId": source_id} for source_id in source_ids]
    return json.dumps({"citations": citations, "reason": reason, "verdict": verdict})


def ok(content: str) -> httpx.Response:
    return chat_response(content)


def cut(content: str) -> httpx.Response:
    """An answer cut off at the cap; 384 tokens is within either kind's cap."""
    return chat_response(content, finish_reason="length", completion_tokens=384)


def count() -> httpx.Response:
    """The token count before a repair: well inside the limit."""
    return input_tokens_response(900)


class StepClock:
    """A `perf_counter_ns` stand-in that moves by the next step of a fixed cycle on each read. An exchange reads it
    twice, a short step and then a long one apart, so call times differ but repeat exactly."""

    STEPS = (2_000_000, 1_840_000_000, 3_000_000, 655_000_000, 1_000_000, 2_970_000_000)

    def __init__(self) -> None:
        self.now = 0
        self.reads = 0

    def __call__(self) -> int:
        self.now += self.STEPS[self.reads % len(self.STEPS)]
        self.reads += 1
        return self.now


def scripted_header(**changes: str) -> RunHeader:
    """The header of a scripted render: nothing was launched, so the launch fields say so, each in its own words."""
    fields: dict[str, Any] = {
        "run_kind": "scripted",
        "model_id": pipeline.MODEL_ID,
        "prompts_sha256": DEMO_PROMPTS.sha256(),
        "date": SCRIPTED_DATE,
        **SCRIPTED_FIELDS,
    }
    return RunHeader(**{**fields, **changes})


def run_small(
    directory: Path,
    *steps: Any,
    transcripts: dict[str, Any] | None = None,
    bundles: dict[str, Any] | None = None,
) -> tuple[tuple[pipeline.TranscriptResult, ...], ScriptedServer]:
    """The demo over the small fixture, or the given one, against a server playing `steps`."""
    fixtures = pipeline.load_fixtures(write_fixtures(directory, transcripts, bundles), read_bytes)
    client, server = make_client(*steps, clock=StepClock())
    return pipeline.run_demo(client, DEMO_PROMPTS, fixtures), server


def small_run(directory: Path, *steps: Any, header: RunHeader | None = None, **fixtures: Any) -> DemoRun:
    """`run_small` as a page's run, with a scripted header unless one is given."""
    transcripts, _server = run_small(directory, *steps, **fixtures)
    sha256 = {pipeline.BUNDLES_FILE: "b" * 64, pipeline.TRANSCRIPTS_FILE: "a" * 64}
    return DemoRun(scripted_header() if header is None else header, sha256, transcripts)


def golden_steps() -> list[Any]:
    """Scripted answers over the package fixtures, in call order. demo_t1: claims 1 to 3 and 6 go to the probe, the
    sixth after one repair; claim 4 sits on an out-of-scope segment and claim 5 has no bundle. demo_t2: the extraction
    is cut off once and repaired; claim 3 times out once; claim 4 cites nothing. demo_t3: claim 2 is cut off twice and
    claim 3 cites an id outside its bundle twice."""
    t1 = extraction_json(
        [
            ("The 3 October storm closed Port Ketterel harbour for thirty-one hours.", ["t1_s02"]),
            ("The Varnhoe Weather Service logged gusts of 112 km/h at the north mole that night.", ["t1_s03"]),
            ("Varnhoe Link Ferries cut winter crossings from six a day to four from 1 November.", ["t1_s05"]),
            ("Diesel fumes along the quay cause chest infections.", ["t1_s11"]),
            ("The old Ketterel lighthouse was built in 1868.", ["t1_s12"]),
            ("The KCX-40 index closed at 4,812 points on Friday.", ["t1_s13"]),
        ],
        [("opinion", ["t1_s06"], None), ("request", ["t1_s08"], None), ("vague", ["t1_s14"], None)]
        + [("prediction", ["t1_s15"], "I bet the ferry fares will double by next summer.")],
    )
    t2 = extraction_json(
        [
            ("December kelp futures settled at 318 varn marks a tonne on Friday.", ["t2_s02"]),
            ("Kelp rose nine percent on the week, its biggest weekly gain since March.", ["t2_s03"]),
            ("The varn mark weakened to 1.27 against the Ostrian taler on Friday afternoon.", ["t2_s06"]),
            ("Exchange notice 41 keeps trading hours at 08:30 to 16:30 through the winter.", ["t2_s09"]),
            ("Brennick Freight moved 1.9 million tonnes through Port Ketterel last year.", ["t2_s11"]),
        ],
        [("opinion", ["t2_s05"], None), ("outOfScope", ["t2_s07"], None), ("request", ["t2_s08"], None)]
        + [("prediction", ["t2_s12"], None), ("vague", ["t2_s14"], None)],
    )
    t3 = extraction_json(
        [
            ("The coldest reading on Varnhoe last night was minus three degrees at Hollin Fell.", ["t3_s02"]),
            ("Last night was the coldest October night on record for Varnhoe.", ["t3_s03"]),
            ("The Sorrel Point library reopened on Monday after roof repairs.", ["t3_s05"]),
            ("Wool at the Ketterel autumn auction fetched 6.40 varn marks a kilo, a five-year high.", ["t3_s13"]),
            ("Rainfall for the first half of October was 74 mm at the Varnhoe airport gauge.", ["t3_s10"]),
        ],
        [("outOfScope", ["t3_s07"], None), ("prediction", ["t3_s11"], None), ("request", ["t3_s14"], None)]
        + [("vague", ["t3_s15"], None)],
    )
    unknown = probe_json("CONTRADICTED", "The council notice gives Tuesday 14 October.", "src_9")
    cut_probe = '{"citations": [{"sourceId": "src_2"}], "reason": "The October record low'
    return [
        ok(t1),
        ok(probe_json("CONTRADICTED", "The Harbour Board notice gives a closure of 26 hours, not 31.", "src_1")),
        ok(probe_json("SUPPORTED", "The station log records a 112 km/h gust at the north mole.", "src_2", "src_3")),
        ok(probe_json("PARTIALLY_SUPPORTED", "The cut starts on 1 November, but to five crossings a day.", "src_1")),
        ok("Here is my verdict on the claim."),
        count(),
        ok(probe_json("CONTRADICTED", "The weekly bulletin gives a Friday close of 4,790.5 points.", "src_1")),
        cut('{"claims": [{"segmentIds": ["t2_s02"], "text": "December kelp'),
        count(),
        ok(t2),
        ok(probe_json("SUPPORTED", "The daily settlement report gives 318 varn marks a tonne.", "src_1")),
        ok(probe_json("PARTIALLY_SUPPORTED", "Up 9.0 percent, but the biggest gain since January.", "src_2")),
        httpx.ReadTimeout,
        ok(probe_json("CONTRADICTED", "The afternoon fixing gives 1.24 talers per varn mark.", "src_1")),
        ok(probe_json("INSUFFICIENT_EVIDENCE", "Notice 41 keeps the hours only until a review ends.")),
        ok(t3),
        ok(probe_json("SUPPORTED", "The overnight table gives minus 3.0 degrees at Hollin Fell.", "src_1")),
        cut(cut_probe),
        count(),
        cut(cut_probe),
        ok(unknown),
        count(),
        ok(unknown),
        ok(probe_json("PARTIALLY_SUPPORTED", "6.40 a kilo is right; it is the highest since 2029.", "src_1")),
        ok(probe_json("INSUFFICIENT_EVIDENCE", "The note covers 1 to 12 October only.", "src_3")),
    ]


def golden_run() -> DemoRun:
    """The golden scenario: the package fixtures, `DEMO_PROMPTS`, `golden_steps` and a `StepClock`."""
    fixtures = pipeline.load_fixtures(str(FIXTURES_DIR), read_bytes)
    client, server = make_client(*golden_steps(), clock=StepClock())
    transcripts = pipeline.run_demo(client, DEMO_PROMPTS, fixtures)
    if server.steps:
        raise AssertionError(f"{len(server.steps)} scripted golden steps were not used")
    return DemoRun(scripted_header(), fixtures.sha256, transcripts)
