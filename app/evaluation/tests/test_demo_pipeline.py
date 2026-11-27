"""The demo pipeline: the fixtures, the frozen prompts, extraction, the mapping rule and the probe calls (demo
specification; S2 §3.8, §3.11, §4; PHASE-0 §2.2).

No test sends through a real transport or starts a process: the model is a `ScriptedServer` behind
`httpx.MockTransport`, over the small synthetic fixture of `_demo_fixtures` unless a test says otherwise.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff import client as client_module
from app.evaluation.bakeoff.client import CHAT_PATH, COUNT_PATH
from app.evaluation.bakeoff.frozen import FrozenInputError
from app.evaluation.bakeoff.inputs import input_text
from app.evaluation.bakeoff.probe import ProbeOutput
from app.evaluation.demo import pipeline
from app.evaluation.demo.pipeline import DemoError
from app.evaluation.tests._client_fixtures import (
    block_sockets,
    completions,
    error_response,
    input_tokens_response,
    paths,
)
from app.evaluation.tests._demo_fixtures import (
    DEMO_PROMPTS,
    FIXTURES_DIR,
    LF,
    MARK,
    count,
    cut,
    extraction_json,
    ok,
    probe_json,
    read_bytes,
    run_small,
    small_bundles,
    small_transcripts,
    source,
    write_fixtures,
)
from app.evaluation.tests._h5_fixtures import h5_input_bytes, write_h5_inputs
from app.evaluation.tests._runner_fixtures import block_processes

ON_TWO = extraction_json([("The Brask valley tram opened in 2030.", ["syn_s02"])])
LONE = chr(0xD800)  # a lone surrogate: valid as a JSON escape, but no request body can carry it


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


def bodies(server: Any) -> list[dict[str, Any]]:
    return [json.loads(request.content) for request in completions(server)]


def sent_input(body: dict[str, Any]) -> Any:
    """The input JSON of a request: the line between the delimiters of `DEMO_PROMPTS`' user templates."""
    return json.loads(body["messages"][1]["content"].split(LF)[2])


def only_claim(results: tuple[pipeline.TranscriptResult, ...]) -> pipeline.ClaimResult:
    (transcript,) = results
    (claim,) = transcript.claims
    return claim


def ids(sources: tuple[pipeline.Source, ...]) -> list[str]:
    return [row.source_id for row in sources]


# ---------------------------------------------------------------- the happy path


def test_the_happy_path_sends_the_frozen_requests_and_shows_the_cited_verdict(tmp_path: Path) -> None:
    results, server = run_small(
        tmp_path,
        ok(extraction_json([("The Brask valley tram opened in 2030.", ["syn_s02"])], [("opinion", ["syn_s01"], None)])),
        ok(probe_json("SUPPORTED", "Source one gives 2030.", "src_1")),
    )
    (transcript,) = results
    claim = only_claim(results)
    assert transcript.fault is None and transcript.extraction is not None
    assert [entry.reason_kind for entry in transcript.extraction.non_checkable] == ["opinion"]
    assert (claim.status, claim.fault, claim.bundle.bundle_id) == ("verdict", None, "syn_b1")
    assert claim.output == ProbeOutput("SUPPORTED", "Source one gives 2030.", ("src_1",))
    assert (ids(claim.cited), ids(claim.uncited)) == (["src_1"], ["src_2"])
    rows = small_transcripts()["transcripts"][0]["request"]["segments"]
    segments = [{key: row[key] for key in pipeline.CONTRACT_KEYS} for row in rows]
    sources = [{"sourceId": row["sourceId"], "text": row["text"]} for row in small_bundles()["bundles"][0]["sources"]]
    expected = (
        DEMO_PROMPTS.extraction_messages(input_text({"language": "en", "segments": segments})),
        DEMO_PROMPTS.probe_messages(input_text({"claim": "The Brask valley tram opened in 2030.", "sources": sources})),
    )
    extraction, probe = bodies(server)
    for body, messages in zip((extraction, probe), expected, strict=True):
        assert body["messages"] == [{"content": message.content, "role": message.role} for message in messages]
    assert list(sent_input(extraction)["segments"][0]) == list(pipeline.CONTRACT_KEYS)
    for body, max_tokens in ((extraction, 1024), (probe, 384)):
        assert (body["temperature"], body["seed"], body["top_k"], body["max_tokens"]) == (0.0, 42, 1, max_tokens)
        assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["cache_prompt"] is False
        assert body["response_format"]["type"] == "json_schema"
    assert paths(server) == [CHAT_PATH, CHAT_PATH]


# ---------------------------------------------------------------- the mapping rule


def test_a_claim_takes_the_first_bundle_whose_anchors_overlap_its_segments(tmp_path: Path) -> None:
    results, server = run_small(
        tmp_path,
        ok(
            extraction_json(
                [("Rainfall reached 40 millimetres.", ["syn_s03"]), ("Tram and rain.", ["syn_s03", "syn_s02"])]
            )
        ),
        ok(probe_json("SUPPORTED", "The rainfall source.", "src_1")),
        ok(probe_json("PARTIALLY_SUPPORTED", "Only the tram part.", "src_2")),
    )
    first, second = results[0].claims
    assert (first.bundle.bundle_id, second.bundle.bundle_id) == ("syn_b2", "syn_b1")
    assert [len(sent_input(body)["sources"]) for body in bodies(server)[1:]] == [1, 2]


def test_a_claim_maps_only_to_bundles_of_its_own_transcript(tmp_path: Path) -> None:
    """syn_t2 reuses the segment id syn_s02 and has no bundle, so its claim must not take syn_t1's bundle."""
    transcripts = small_transcripts()
    other = copy.deepcopy(transcripts["transcripts"][0])
    other.update(transcriptId="syn_t2", title="Synthetic second test call")
    transcripts["transcripts"].append(other)
    results, server = run_small(
        tmp_path, ok(ON_TWO), ok(probe_json("SUPPORTED", "Yes.", "src_1")), ok(ON_TWO), transcripts=transcripts
    )
    assert [(claim.status, claim.bundle) for claim in results[1].claims] == [("no_evidence_fixture", None)]
    assert results[0].claims[0].bundle.bundle_id == "syn_b1"
    assert paths(server) == [CHAT_PATH, CHAT_PATH, CHAT_PATH]


def test_a_claim_with_no_bundle_shows_no_evidence_fixture_and_sends_nothing(tmp_path: Path) -> None:
    results, server = run_small(tmp_path, ok(extraction_json([("The cooperative sold 120 tonnes.", ["syn_s04"])])))
    claim = only_claim(results)
    assert (claim.status, claim.bundle, claim.call, claim.fault, claim.output) == (
        "no_evidence_fixture",
        None,
        None,
        None,
        None,
    )
    assert paths(server) == [CHAT_PATH]


def test_an_extracted_claim_on_an_out_of_scope_segment_is_leaked_and_sends_nothing(tmp_path: Path) -> None:
    claims = [("Tram noise ruins sleep.", ["syn_s05"]), ("The tram and the noise.", ["syn_s02", "syn_s05"])]
    results, server = run_small(tmp_path, ok(extraction_json(claims)))
    assert [(claim.status, claim.call) for claim in results[0].claims] == [("leaked", None), ("leaked", None)]
    assert paths(server) == [CHAT_PATH]


# ---------------------------------------------------------------- invalid output, one repair


def test_invalid_json_then_one_frozen_repair_then_valid_output(tmp_path: Path) -> None:
    results, server = run_small(
        tmp_path, ok("Here are the claims."), count(), ok(ON_TWO), ok(probe_json("SUPPORTED", "Yes.", "src_1"))
    )
    (transcript,) = results
    call = transcript.call
    assert [(attempt.retry_type, attempt.failure_kind) for attempt in call.attempts] == [
        ("none", "invalid_output"),
        ("repair", None),
    ]
    assert (call.outcome, transcript.fault, only_claim(results).status) == ("valid", None, "verdict")
    repair = bodies(server)[1]
    assert repair["messages"][-2] == {"content": "Here are the claims.", "role": "assistant"}
    assert repair["messages"][-1]["content"] == DEMO_PROMPTS.repair.render(call.attempts[0].error_text)
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH, CHAT_PATH]


def test_a_second_invalid_output_ends_the_call_with_no_third_attempt(tmp_path: Path) -> None:
    results, server = run_small(tmp_path, ok("not json"), count(), ok("still not json"))
    (transcript,) = results
    assert (transcript.fault, transcript.claims, transcript.extraction) == ("invalid_output", (), None)
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH]


def test_an_extraction_cut_off_is_repaired_once(tmp_path: Path) -> None:
    results, _server = run_small(
        tmp_path, cut('{"claims": [{"segmentIds"'), count(), ok(ON_TWO), ok(probe_json("SUPPORTED", "Yes."))
    )
    (transcript,) = results
    assert [attempt.failure_kind for attempt in transcript.call.attempts] == ["cut_off", None]
    assert only_claim(results).status == "verdict"


# ---------------------------------------------------------------- a cut-off


def test_a_probe_cut_off_twice_shows_no_verdict_with_its_fault_kind(tmp_path: Path) -> None:
    results, server = run_small(
        tmp_path, ok(ON_TWO), cut('{"citations": [{"sourceId"'), count(), cut('{"citations": [{"sourceId"')
    )
    claim = only_claim(results)
    assert (claim.status, claim.fault, claim.output, claim.cited) == ("no_verdict", "cut_off", None, ())
    assert [attempt.failure_kind for attempt in claim.call.attempts] == ["cut_off", "cut_off"]
    assert ids(claim.uncited) == ["src_1", "src_2"]
    assert server.steps == []


# ---------------------------------------------------------------- a timeout, then a same-request retry


def test_a_timeout_then_a_same_request_retry_gives_the_retry_verdict(tmp_path: Path) -> None:
    results, server = run_small(
        tmp_path, ok(ON_TWO), httpx.ReadTimeout, ok(probe_json("CONTRADICTED", "Source two says 2029.", "src_2"))
    )
    claim = only_claim(results)
    assert (claim.status, claim.output.verdict) == ("verdict", "CONTRADICTED")
    assert [(attempt.retry_type, attempt.failure_kind) for attempt in claim.call.attempts] == [
        ("none", "timeout"),
        ("same_request", None),
    ]
    first, retry = completions(server)[1:]
    assert first.content == retry.content
    assert paths(server) == [CHAT_PATH, CHAT_PATH, CHAT_PATH]


@pytest.mark.parametrize(
    ("steps", "fault"),
    [
        ((httpx.ReadTimeout, httpx.ReadTimeout), "timeout"),
        (
            (error_response(500, "server_error", "boom"), error_response(503, "unavailable_error", "busy")),
            "server_error",
        ),
    ],
)
def test_a_probe_that_fails_twice_shows_no_verdict(tmp_path: Path, steps: tuple[Any, ...], fault: str) -> None:
    claim = only_claim(run_small(tmp_path, ok(ON_TWO), *steps)[0])
    assert (claim.status, claim.fault, claim.output) == ("no_verdict", fault, None)


# ---------------------------------------------------------------- an unknown sourceId


def test_an_unknown_source_id_fails_the_output_and_is_never_shown(tmp_path: Path) -> None:
    unknown = probe_json("SUPPORTED", "Sources one and nine.", "src_1", "src_9")
    claim = only_claim(run_small(tmp_path, ok(ON_TWO), ok(unknown), count(), ok(unknown))[0])
    assert (claim.status, claim.fault, claim.output, claim.cited) == ("no_verdict", "invalid_output", None, ())
    assert [attempt.unknown_source_ids for attempt in claim.call.attempts] == [("src_9",), ("src_9",)]
    assert "src_9" not in ids(claim.uncited)


def test_an_unknown_source_id_then_a_clean_repair_gives_the_repair_verdict(tmp_path: Path) -> None:
    unknown = probe_json("SUPPORTED", "Source nine.", "src_9")
    clean = probe_json("SUPPORTED", "Source one.", "src_1")
    claim = only_claim(run_small(tmp_path, ok(ON_TWO), ok(unknown), count(), ok(clean))[0])
    assert (claim.status, claim.output.cited_source_ids) == ("verdict", ("src_1",))
    assert [attempt.retry_type for attempt in claim.call.attempts] == ["none", "repair"]


def test_a_valid_output_citing_outside_its_bundle_is_a_hard_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo checks the cited ids itself, so a validator that let one through cannot put it on a page."""
    real = client_module.parse_probe

    def lenient(raw: str, *, bundle_source_ids: Any, finish_reason: str | None = None) -> ProbeOutput:
        return real(raw, bundle_source_ids=frozenset(bundle_source_ids) | {"src_9"}, finish_reason=finish_reason)

    monkeypatch.setattr(client_module, "parse_probe", lenient)
    with pytest.raises(DemoError, match="outside its bundle"):
        run_small(tmp_path, ok(ON_TWO), ok(probe_json("SUPPORTED", "Source nine.", "src_9")))


def test_cited_sources_keep_the_citation_order(tmp_path: Path) -> None:
    claim = only_claim(run_small(tmp_path, ok(ON_TWO), ok(probe_json("SUPPORTED", "Both.", "src_2", "src_1")))[0])
    assert (ids(claim.cited), ids(claim.uncited)) == (["src_2", "src_1"], [])


# ---------------------------------------------------------------- empty citations


def test_empty_citations_keep_the_verdict_and_list_every_source_as_uncited(tmp_path: Path) -> None:
    answer = probe_json("INSUFFICIENT_EVIDENCE", "Nothing in the bundle covers it.")
    claim = only_claim(run_small(tmp_path, ok(ON_TWO), ok(answer))[0])
    assert (claim.status, claim.output.verdict, claim.output.cited_source_ids) == (
        "verdict",
        "INSUFFICIENT_EVIDENCE",
        (),
    )
    assert (ids(claim.cited), ids(claim.uncited)) == ([], ["src_1", "src_2"])


# ---------------------------------------------------------------- failed extractions and run faults


@pytest.mark.parametrize(
    ("steps", "fault"),
    [
        ((error_response(500, "server_error", "boom"), error_response(500, "server_error", "boom")), "server_error"),
        ((httpx.ReadTimeout, httpx.ReadTimeout), "timeout"),
        ((ok("not json"), error_response(500, "server_error", "no count")), "ServerCheckError"),
    ],
)
def test_a_failed_extraction_shows_its_fault_and_sends_no_probe(
    tmp_path: Path, steps: tuple[Any, ...], fault: str
) -> None:
    results, server = run_small(tmp_path, *steps)
    (transcript,) = results
    assert (transcript.fault, transcript.claims, transcript.extraction) == (fault, (), None)
    assert (transcript.call is None) is (fault == "ServerCheckError")
    kept = [(attempt.attempt, attempt.failure_kind) for attempt in transcript.fault_attempts]
    assert kept == ([(1, "invalid_output")] if fault == "ServerCheckError" else [])
    assert server.steps == []


@pytest.mark.parametrize(
    ("answer", "fault"),
    [
        (error_response(500, "server_error", "no count"), "ServerCheckError"),
        (input_tokens_response(20_000), "ContextBudgetExceeded"),
    ],
)
def test_a_run_fault_on_a_probe_repair_keeps_attempt_one_shows_no_verdict_and_the_run_goes_on(
    tmp_path: Path, answer: httpx.Response, fault: str
) -> None:
    """The repair's count failed or the repair does not fit, after attempt 1 reached the model: the attempt is kept."""
    claims = extraction_json([("The tram opened in 2030.", ["syn_s02"]), ("Rainfall reached 40 mm.", ["syn_s03"])])
    steps = (ok(claims), ok("no json"), answer)
    results, server = run_small(tmp_path, *steps, ok(probe_json("SUPPORTED", "Rain source.", "src_1")))
    first, second = results[0].claims
    assert (first.status, first.fault, first.call, first.output) == ("no_verdict", fault, None, None)
    attempts = [(row.attempt, row.retry_type, row.failure_kind, row.content) for row in first.fault_attempts]
    assert attempts == [(1, "none", "invalid_output", "no json")]
    assert (second.status, second.output.verdict, second.fault_attempts) == ("verdict", "SUPPORTED", ())
    assert paths(server) == [CHAT_PATH, CHAT_PATH, COUNT_PATH, CHAT_PATH]


def test_a_claim_text_holding_a_lone_surrogate_shows_no_verdict_and_the_run_goes_on(tmp_path: Path) -> None:
    """The model may write a lone surrogate as a JSON escape; the probe request cannot carry it, so nothing is sent."""
    claims = extraction_json([("The tram opened " + LONE, ["syn_s02"]), ("Rainfall reached 40 mm.", ["syn_s03"])])
    results, server = run_small(tmp_path, ok(claims), ok(probe_json("SUPPORTED", "Rain source.", "src_1")))
    first, second = results[0].claims
    assert (first.status, first.fault, first.call, first.fault_attempts) == (
        "no_verdict",
        "UnicodeEncodeError",
        None,
        (),
    )
    assert (second.status, second.output.verdict) == ("verdict", "SUPPORTED")
    assert paths(server) == [CHAT_PATH, CHAT_PATH]


# ---------------------------------------------------------------- the frozen prompts


def test_the_prompts_come_from_the_frozen_inputs_with_their_hashes_checked(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path, h5_input_bytes(prompts=DEMO_PROMPTS))
    assert pipeline.load_prompts(str(tmp_path), read_bytes) == DEMO_PROMPTS


def test_a_frozen_file_that_breaks_its_hash_is_refused_before_any_parse(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path, h5_input_bytes(prompts=DEMO_PROMPTS))
    changed = tmp_path / "prompts" / "probe-system.txt"
    changed.write_bytes(changed.read_bytes().replace(b"judge", b"weigh"))
    with pytest.raises(FrozenInputError, match="INPUTS.sha256"):
        pipeline.load_prompts(str(tmp_path), read_bytes)


# ---------------------------------------------------------------- the package fixtures


def test_the_package_fixtures_load_and_meet_the_specification() -> None:
    fixtures = pipeline.load_fixtures(str(FIXTURES_DIR), read_bytes)
    assert len(fixtures.transcripts) == 3
    texts = [row.text for transcript in fixtures.transcripts for row in transcript.segments]
    assert len(set(texts)) == len(texts) and min(len(text) for text in texts) >= 25
    for transcript in fixtures.transcripts:
        assert 15 <= len(transcript.segments) <= 30
        assert 2 <= len({row.speaker_id for row in transcript.segments}) <= 4
        assert transcript.out_of_scope
        assert sum("<" in row.text and ">" in row.text for row in transcript.segments) == 1
        assert fixtures.bundles_for(transcript.transcript_id)
    for bundle in fixtures.bundles:
        assert 1 <= len(bundle.sources) <= 5
        for row in bundle.sources:
            assert row.text.startswith(MARK + " ")
            assert 75 <= len(row.text.split()) <= 225  # about 100 to 300 tokens
    assert set(fixtures.sha256) == {pipeline.TRANSCRIPTS_FILE, pipeline.BUNDLES_FILE}


# ---------------------------------------------------------------- fixture refusals


def _extra_sources(bundles: dict[str, Any]) -> None:
    bundles["bundles"][0]["sources"].extend(source(f"src_{number}") for number in range(3, 7))


Change = Callable[[dict[str, Any], dict[str, Any]], None]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t, b: t.update(format="demo-transcripts/9"), "format"),
        (lambda t, b: t["transcripts"][0]["request"].update(language="fr"), "language"),
        (lambda t, b: t["transcripts"][0]["request"]["segments"][0].update(start=9.0), "start"),
        (lambda t, b: t["transcripts"][0]["request"]["segments"][1].update(segmentId="syn_s01"), "twice"),
        (lambda t, b: t["transcripts"][0]["outOfScopeSegmentIds"].append("syn_s42"), "not a segment"),
        (lambda t, b: t["transcripts"].append(t["transcripts"][0]), "twice"),
        (lambda t, b: b["bundles"][0].update(transcriptId="syn_t9"), "unknown transcript"),
        (lambda t, b: b["bundles"][0]["anchors"].append("syn_s05"), "out of scope"),
        (lambda t, b: b["bundles"][0]["anchors"].append("syn_s99"), "not a segment"),
        (lambda t, b: b["bundles"][0].update(anchors=[]), "anchors"),
        (lambda t, b: _extra_sources(b), "1 to 5"),
        (lambda t, b: b["bundles"][0]["sources"][1].update(sourceId="src_1"), "twice"),
        (lambda t, b: b["bundles"][0]["sources"][0].update(text="An unmarked synthetic source."), "mark"),
        (lambda t, b: b["bundles"][1].update(bundleId="syn_b1"), "twice"),
        (lambda t, b: b["bundles"][0].update(extra=1), "exactly the keys"),
        (lambda t, b: t["transcripts"][0]["request"]["segments"][1].update(text="Tram " + LONE), "lone surrogate"),
        (lambda t, b: t["transcripts"][0].update(title="Call " + LONE), "lone surrogate"),
        (lambda t, b: b["bundles"][0]["sources"][0].update(text=MARK + " Tram " + LONE), "lone surrogate"),
        (lambda t, b: b["bundles"][0]["sources"][0].update(title="Source " + LONE), "lone surrogate"),
    ],
)
def test_the_fixture_loader_refuses_each_broken_rule(tmp_path: Path, change: Change, message: str) -> None:
    transcripts, bundles = small_transcripts(), small_bundles()
    change(transcripts, bundles)
    with pytest.raises(DemoError, match=message):
        pipeline.load_fixtures(write_fixtures(tmp_path, transcripts, bundles), read_bytes)


def test_the_fixture_hashes_are_taken_over_lf_bytes(tmp_path: Path) -> None:
    """A CRLF checkout of the fixtures loads and hashes as the LF files do, so the record's hashes match."""
    write_fixtures(tmp_path / "lf")
    (tmp_path / "crlf").mkdir()
    for name in (pipeline.TRANSCRIPTS_FILE, pipeline.BUNDLES_FILE):
        raw = (tmp_path / "lf" / name).read_bytes()
        (tmp_path / "crlf" / name).write_bytes(raw.replace(LF.encode(), (chr(13) + LF).encode()))
    lf, crlf = (pipeline.load_fixtures(str(tmp_path / name), read_bytes) for name in ("lf", "crlf"))
    assert (tmp_path / "crlf" / pipeline.BUNDLES_FILE).read_bytes().count(chr(13).encode()) > 1
    assert dict(crlf.sha256) == dict(lf.sha256) and crlf.transcripts == lf.transcripts


def test_a_fixture_file_not_in_canonical_form_is_refused(tmp_path: Path) -> None:
    write_fixtures(tmp_path)
    path = tmp_path / pipeline.BUNDLES_FILE
    path.write_bytes(path.read_bytes().replace(b"  ", b"    ", 1))
    with pytest.raises(FrozenInputError, match="canonical"):
        pipeline.load_fixtures(str(tmp_path), read_bytes)
