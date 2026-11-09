"""The machine evidence values: H4a brief section 3 and step A2 (S2 §6 gate M, §3.13, §9).

Every sample here is built in the test. No nvidia-smi text, gpu.csv or host.json is written: parsing and loading are
H4b's, and so are the `check_sample_rate` rows.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Callable

import pytest

from app.evaluation.bakeoff.evidence import (
    MIN_SAMPLES_PER_SEC,
    AttemptEvidence,
    ContentionRecord,
    EvidenceLoader,
    GpuSample,
    HostRecord,
    ProcSample,
    SampleRate,
    sample_rate,
)
from app.evaluation.bakeoff.launch import CANDIDATE_IDS
from app.evaluation.bakeoff.records import RunKey
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._runner_fixtures import block_processes

Q4B, Q35, Q8B = CANDIDATE_IDS
KEY = RunKey(Q4B, "timed_run", 2, 1)
CONTENDED = RunKey(Q8B, "contended", None, 1)


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


def gpu(t_ms: int, used: int = 3600, free: int = 4300) -> GpuSample:
    return GpuSample(t_ms, used, free, "1500 MHz", "7000 MHz", "45", "[N/A]")


def every(step_ms: int, until_ms: int, start_ms: int = 0) -> tuple[GpuSample, ...]:
    """Samples every `step_ms` from `start_ms` up to and including `until_ms`."""
    return tuple(gpu(t) for t in range(start_ms, until_ms + 1, step_ms))


def host(
    key: RunKey = KEY,
    *,
    pswpout: tuple[int, int] = (40, 40),
    elapsed_ms: int = 3000,
    contention: ContentionRecord | None = None,
) -> HostRecord:
    return HostRecord(key, 0, pswpout[0], pswpout[1], elapsed_ms, "terminated", True, None, contention)


def evidence(
    used: tuple[int, ...] = (3600, 3600),
    *,
    rss_kib: tuple[int, ...] = (269_428,),
    pswpout: tuple[int, int] = (40, 40),
    key: RunKey = KEY,
    contention: ContentionRecord | None = None,
) -> AttemptEvidence:
    samples = tuple(gpu(index * 100, value, 8000 - value) for index, value in enumerate(used))
    proc = tuple(ProcSample(index * 100, value) for index, value in enumerate(rss_kib))
    return AttemptEvidence(key, samples, proc, host(key, pswpout=pswpout, contention=contention))


def test_the_floor_is_s2s_ten_a_second() -> None:
    assert MIN_SAMPLES_PER_SEC == 10


def test_the_baseline_is_the_first_sample_not_the_lowest() -> None:
    attempt = evidence((3600, 3500, 7900, 7400))
    assert attempt.baseline_used_mib == 3600
    assert attempt.baseline_free_mib == 8000 - 3600
    assert attempt.peak_used_mib == 7900
    assert attempt.rise_mib == 4300


def test_one_sample_is_its_own_baseline_and_peak() -> None:
    attempt = evidence((3600,))
    assert (attempt.baseline_used_mib, attempt.peak_used_mib, attempt.rise_mib) == (3600, 3600, 0)


def test_the_peak_may_come_before_the_end() -> None:
    attempt = evidence((3600, 9904, 7000))
    assert attempt.peak_used_mib == 9904
    assert attempt.rise_mib == 6304


@pytest.mark.parametrize(
    ("rss_kib", "expected"),
    [
        ((269_428,), 269_428 * 1024),
        ((100, 327_616, 2000), 327_616 * 1024),
        ((3_906_250,), 4_000_000_000),
        ((3_906_251,), 4_000_001_024),
        ((), None),
    ],
)
def test_the_peak_rss_is_in_bytes_or_none_without_a_reading(rss_kib: tuple[int, ...], expected: int | None) -> None:
    assert evidence(rss_kib=rss_kib).peak_rss_anon_bytes == expected


@pytest.mark.parametrize(("pswpout", "swapped"), [((40, 40), False), ((40, 41), True), ((0, 9000), True)])
def test_swapped_means_the_swap_out_counter_moved(pswpout: tuple[int, int], swapped: bool) -> None:
    assert evidence(pswpout=pswpout).swapped is swapped


def test_the_rate_describes_the_gpu_samples_against_the_hosts_elapsed_time() -> None:
    samples = every(100, 2900)
    attempt = AttemptEvidence(KEY, samples, (), host(elapsed_ms=3010))
    assert attempt.rate == sample_rate(samples, 3010)
    assert attempt.rate.elapsed_ms == 3010


def test_an_attempt_without_gpu_samples_is_refused() -> None:
    with pytest.raises(ValueError, match="baseline"):
        AttemptEvidence(KEY, (), (ProcSample(0, 1),), host())


@pytest.mark.parametrize(
    "other", [RunKey(Q4B, "timed_run", 2, 2), RunKey(Q4B, "timed_run", 3, 1), RunKey(Q35, "timed_run", 2, 1)]
)
def test_a_host_record_of_another_attempt_is_refused(other: RunKey) -> None:
    with pytest.raises(ValueError, match="another attempt"):
        AttemptEvidence(KEY, every(100, 900), (), host(other))


def test_a_contended_attempt_carries_its_contention_record() -> None:
    record = ContentionRecord("0" * 64, 5.0, 0, True, 1200, None)
    attempt = evidence(key=CONTENDED, contention=record)
    assert attempt.host.contention == record
    assert attempt.key == attempt.host.key == CONTENDED


@pytest.mark.parametrize(
    "value",
    [
        gpu(0),
        ProcSample(0, 1),
        SampleRate(0, 0, 0, None, None),
        ContentionRecord("0" * 64, 0.0, None, False, None, "killed"),
        host(),
        evidence(),
    ],
    ids=lambda value: type(value).__name__,
)
def test_every_type_is_a_frozen_slotted_value(value: object) -> None:
    assert dataclasses.is_dataclass(value)
    assert not hasattr(value, "__dict__")
    first = dataclasses.fields(value)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, first, getattr(value, first))


def test_ten_a_second_gives_exact_figures() -> None:
    assert sample_rate(every(100, 2900), 3000) == SampleRate(30, 3000, 2900, 10, 100)


def test_uneven_samples_give_the_fewest_in_a_whole_second_and_the_longest_gap() -> None:
    samples = tuple(gpu(t) for t in (0, 50, 400, 1000, 1100, 2500))
    assert sample_rate(samples, 2600) == SampleRate(6, 2600, 2500, 2, 1400)


def test_a_short_second_is_described_not_refused() -> None:
    samples = (*every(100, 900), *every(125, 1875, 1000), *every(100, 3000, 2000))
    rate = sample_rate(samples, 3100)
    assert rate == SampleRate(29, 3100, 3000, 8, 125)
    assert rate.min_per_whole_second is not None and rate.min_per_whole_second < MIN_SAMPLES_PER_SEC


def test_the_partial_last_second_is_ignored() -> None:
    sparse_tail = (gpu(2000), gpu(2900))
    rate = sample_rate((*every(100, 1900), *sparse_tail), 3000)
    assert (rate.first_to_last_ms, rate.min_per_whole_second, rate.max_gap_ms) == (2900, 10, 900)


def test_a_sample_on_the_last_second_boundary_opens_the_partial_second() -> None:
    assert sample_rate(every(100, 2000), 2000) == SampleRate(21, 2000, 2000, 10, 100)


def test_seconds_count_from_the_first_sample() -> None:
    assert sample_rate(every(100, 7900, start_ms=5000), 3000) == SampleRate(30, 3000, 2900, 10, 100)


def test_less_than_one_whole_second_has_no_minimum() -> None:
    assert sample_rate(every(100, 900), 950) == SampleRate(10, 950, 900, None, 100)


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ((), SampleRate(0, 1200, 0, None, None)),
        ((gpu(0),), SampleRate(1, 1200, 0, None, None)),
        ((gpu(400),), SampleRate(1, 1200, 0, None, None)),
    ],
    ids=["empty log", "one sample", "one late sample"],
)
def test_fewer_than_two_samples_have_no_span_and_no_gap(samples: tuple[GpuSample, ...], expected: SampleRate) -> None:
    assert sample_rate(samples, 1200) == expected


def test_equal_timestamps_give_a_zero_span_and_a_zero_gap() -> None:
    assert sample_rate((gpu(0), gpu(0), gpu(0)), 500) == SampleRate(3, 500, 0, None, 0)


def test_the_harness_time_is_reported_as_given() -> None:
    samples = every(100, 2900)
    assert sample_rate(samples, 12_345).elapsed_ms == 12_345
    assert sample_rate(samples, 12_345).first_to_last_ms == 2900


def test_the_loader_type_has_the_signature_of_load_evidence() -> None:
    arguments, result = typing.get_args(EvidenceLoader)
    assert arguments == [str, RunKey, Callable[[str], bytes]]
    assert result == AttemptEvidence | None

    def no_evidence(run_dir: str, key: RunKey, read_bytes: Callable[[str], bytes]) -> AttemptEvidence | None:
        return None

    loader: EvidenceLoader = no_evidence
    assert loader("/runs/bakeoff-1", KEY, lambda path: b"") is None
