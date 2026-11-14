"""The machine module: its constants, parsers, /proc thread and evidence loader (S2 §3.1, §3.13, §6, §9).

Every log here is built in the test, in the form of the operator's private capture of 2026-11-10, and written under
`tmp_path` only. Real threads run only in the `_start_thread` rows, each released and joined before the test ends.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
import re
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from app.evaluation.bakeoff import evidence, machine
from app.evaluation.bakeoff.evidence import (
    AttemptEvidence,
    ContentionRecord,
    GpuSample,
    HostRecord,
    ProcSample,
)
from app.evaluation.bakeoff.launch import (
    CANDIDATE_IDS,
    CLEAN_PATH,
    LOG_POLL_SEC,
    ConfigError,
    ServerStopFailed,
    cmdline_bytes,
    frozen_argv,
)
from app.evaluation.bakeoff.machine import (
    APPS_DEADLINE_SEC,
    APPS_HEADER,
    PROC_INTERVAL_SEC,
    SAMPLE_INTERVAL_MS,
    SAMPLER_READY_SEC,
    THREAD_JOIN_SEC,
    TIMESTAMP_PATTERN,
    ContentionFailed,
    CudaCheckFailed,
    MachineConfig,
    MachineFault,
    MachineHooks,
    MachineTools,
    NoRssSample,
    OtherCudaProcess,
    ProcSamplerFailed,
    SampleClockBackwards,
    SampleRateTooLow,
    SamplerDied,
    SamplerFailed,
    ServerNotFound,
    UnparsableSample,
    apps_argv,
    apps_process_lines,
    check_sample_rate,
    gpu_argv,
    load_evidence,
    parse_gpu_line,
    parse_gpu_log,
    parse_pswpout,
    parse_rss_anon,
)
from app.evaluation.bakeoff.records import RunKey, StoreError, _unit_name, machine_log_name, record_json, record_loads
from app.evaluation.bakeoff.schemas import canonical_sha256
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._fixtures import NEEDS_DIGIT_LIMIT
from app.evaluation.tests._h4_fixtures import (
    BASELINE,
    LOADED,
    MACHINE_CONFIG,
    NVIDIA_SMI,
    ContentionScript,
    FakeMachinePopen,
    FakeThreads,
    GpuScript,
    apps_output,
    gpu_line,
    gpu_log,
    install_machine,
    machine_tools,
    proc_status,
    vmstat,
)
from app.evaluation.tests._runner_fixtures import (
    CONDITIONS,
    SCHEDULE_CANDIDATES,
    VALID_OPERATOR,
    FakeFiles,
    FakeHost,
    FakeProcess,
    ScheduleRig,
    ServerScript,
    block_processes,
    frozen_inputs,
    launch_tools,
    no_fsync,
    schedule_rig,
    small_inputs,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
TIMED = RunKey(Q4B, "timed_run", 2, 1)
CONTENDED = RunKey(Q8B, "contended", None, 1)
CONTENDED_UNIT = "qwen3-8b.contended.a1"  # the unit name of CONTENDED, as host.json stores it
OTHER_UNIT = "qwen3-8b.contended.a2"  # the unit name of another attempt
P0_LINE = "2026/11/10 01:26:09.702, 0 MiB, 7899 MiB, 1552 MHz, 11001 MHz, 55, 9.86 W"
P0_HEADER_HEX = "7069642c2070726f636573735f6e616d652c20757365645f6770755f6d656d6f7279205b4d69425d0a"


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


def fields_line(*fields: str) -> str:
    return ", ".join(fields)


def p0_fields() -> list[str]:
    return P0_LINE.split(", ")


def with_field(index: int, value: str) -> str:
    fields = p0_fields()
    fields[index] = value
    return fields_line(*fields)


# ---------------------------------------------------------------- constants and config


def test_the_argvs_are_the_captured_commands() -> None:
    assert gpu_argv(MACHINE_CONFIG) == (
        NVIDIA_SMI,
        "--query-gpu=timestamp,memory.used,memory.free,clocks.gr,clocks.mem,temperature.gpu,power.draw",
        "--format=csv,noheader",
        "--loop-ms=50",
    )
    assert apps_argv(MACHINE_CONFIG) == (
        NVIDIA_SMI,
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv",
    )


def test_the_apps_header_is_the_captured_bytes() -> None:
    assert (APPS_HEADER + "\n").encode("ascii") == bytes.fromhex(P0_HEADER_HEX)


def test_the_timing_constants() -> None:
    assert SAMPLE_INTERVAL_MS == 50
    assert machine._LOOP_FLAG == "--loop-ms=50"
    assert (SAMPLER_READY_SEC, APPS_DEADLINE_SEC, PROC_INTERVAL_SEC, THREAD_JOIN_SEC) == (10.0, 30.0, 0.1, 5.0)
    assert machine._HOOKED == ("timed_run", "contended")
    assert machine._VMSTAT == "/proc/vmstat"


def test_the_timestamp_pattern_is_the_captured_form() -> None:
    assert TIMESTAMP_PATTERN.fullmatch("2026/11/10 01:26:09.702")
    assert TIMESTAMP_PATTERN.flags & re.ASCII
    for text in ("2026-11-10 01:26:09.702", "2026/11/10 01:26:09.70", "2026/11/10 01:26:09.70" + chr(0x0661)):
        assert TIMESTAMP_PATTERN.fullmatch(text) is None, text


def test_the_floor_is_the_evidence_constant() -> None:
    assert machine.MIN_SAMPLES_PER_SEC is evidence.MIN_SAMPLES_PER_SEC
    assert machine.sample_rate is evidence.sample_rate


def test_a_valid_config_wraps_its_environment() -> None:
    env = {"PATH": "/usr/bin:/bin"}
    config = MachineConfig(NVIDIA_SMI, ("/opt/audio/contention",), env, 0)
    env["PATH"] = "changed"
    assert isinstance(config.contention_env, MappingProxyType)
    assert dict(config.contention_env) == {"PATH": "/usr/bin:/bin"}
    assert MACHINE_CONFIG.contention_lead_sec == 5.0


@pytest.mark.parametrize(
    "change",
    [
        {"nvidia_smi_path": "usr/lib/wsl/lib/nvidia-smi"},
        {"nvidia_smi_path": "/usr/lib/wsl/../lib/nvidia-smi"},
        {"nvidia_smi_path": "/usr/lib/wsl/./lib/nvidia-smi"},
        {"nvidia_smi_path": "/usr/bin/python3"},
        {"nvidia_smi_path": "/usr/lib/wsl/lib/nvidia-smi.exe"},
        {"contention_argv": ()},
        {"contention_argv": ["/opt/audio/contention"]},
        {"contention_argv": ("opt/audio/contention",)},
        {"contention_argv": ("/opt/audio/contention", 3)},
        {"contention_argv": ("/opt/audio/contention", "")},
        {"contention_env": {"PATH": 1}},
        {"contention_env": {1: "x"}},
        {"contention_env": [("PATH", "/usr/bin")]},
        {"contention_lead_sec": -1},
        {"contention_lead_sec": True},
        {"contention_lead_sec": math.nan},
        {"contention_lead_sec": math.inf},
        {"contention_lead_sec": "5"},
        {"contention_lead_sec": 10**400},
        {"contention_lead_sec": -(10**400)},
    ],
)
def test_the_config_refuses_each_bad_field(change: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "nvidia_smi_path": NVIDIA_SMI,
        "contention_argv": ("/opt/audio/contention",),
        "contention_env": {},
        "contention_lead_sec": 5.0,
        **change,
    }
    with pytest.raises(ConfigError):
        MachineConfig(**fields)


def test_an_int_lead_too_large_for_a_float_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match=re.escape("MachineConfig.contention_lead_sec")):
        MachineConfig(NVIDIA_SMI, ("/opt/audio/contention",), {}, 10**400)
    assert MachineConfig(NVIDIA_SMI, ("/opt/audio/contention",), {}, 10**6).contention_lead_sec == 10**6


def test_the_tools_default_to_the_real_listing_and_thread() -> None:
    tools = MachineTools(launch=launch_tools())
    assert tools.list_pids is machine._list_pids
    assert tools.start_thread is machine._start_thread


# ---------------------------------------------------------------- parse_gpu_line


def test_the_captured_line_parses_exactly() -> None:
    assert parse_gpu_line(P0_LINE, None) == GpuSample(0, 0, 7899, "1552 MHz", "11001 MHz", "55", "9.86 W")
    assert gpu_line(0) == P0_LINE


def test_a_line_is_timed_from_the_first_stamp() -> None:
    first = machine._stamp_ms("2026/11/10 01:26:09.702")
    assert parse_gpu_line(gpu_line(57, *LOADED), first) == GpuSample(
        57, 4308, 3592, "1552 MHz", "11001 MHz", "55", "9.86 W"
    )


def test_na_in_power_and_the_clocks_is_kept_raw() -> None:
    line = gpu_line(0, clocks_gr="[N/A]", clocks_mem="[N/A]", power="[N/A]")
    assert parse_gpu_line(line, None) == GpuSample(0, 0, 7899, "[N/A]", "[N/A]", "55", "[N/A]")


@pytest.mark.parametrize(
    "line",
    [
        with_field(1, "[N/A]"),
        with_field(2, "[N/A]"),
        fields_line(*p0_fields()[:6]),
        fields_line(*p0_fields(), "1 W"),
        with_field(1, "4308 MB"),
        with_field(1, "0MiB"),
        with_field(1, "-1 MiB"),
        with_field(2, "7899"),
        with_field(5, "55" + chr(0xB0)),
        with_field(1, chr(0x0661) + " MiB"),
        with_field(0, "2026/02/30 01:26:09.702"),
        with_field(0, "2026/11/10 24:00:00.000"),
        with_field(0, "2026/11/10 01:60:00.000"),
        with_field(0, "0000/11/10 01:26:09.702"),
        P0_LINE.replace(", 0 MiB", ",  0 MiB"),
        P0_LINE.replace(", 55", ",  55"),
        P0_LINE + "\r",
        with_field(3, ""),
        with_field(4, "1552\tMHz"),
    ],
)
def test_parse_gpu_line_refuses_each_form(line: str) -> None:
    with pytest.raises(UnparsableSample):
        parse_gpu_line(line, None)


# ---------------------------------------------------------------- parse_gpu_log


def test_captured_lines_give_their_offsets() -> None:
    samples = parse_gpu_log(gpu_log(*(gpu_line(t, *LOADED) for t in (0, 57, 108, 160, 211))))
    assert [sample.t_ms for sample in samples] == [0, 57, 108, 160, 211]
    assert all((sample.used_mib, sample.free_mib) == LOADED for sample in samples)


def test_a_cut_tail_is_ignored() -> None:
    raw = gpu_log(gpu_line(0), gpu_line(51), tail=b"2026/11/10 01:26:09.8")
    assert [sample.t_ms for sample in parse_gpu_log(raw)] == [0, 51]
    assert parse_gpu_log(b"") == ()
    assert parse_gpu_log(gpu_line(0).encode("ascii")) == ()
    assert parse_gpu_log(gpu_log(gpu_line(0), tail=b"\xff garbage")) == (parse_gpu_line(P0_LINE, None),)


@pytest.mark.parametrize(
    "raw",
    [
        gpu_log(gpu_line(0), "", gpu_line(51)),
        b"\n",
        gpu_line(0).encode("ascii") + b"\r\n",
        gpu_log(gpu_line(0)) + gpu_line(51).encode("ascii") + b"\xff\n",
    ],
)
def test_parse_gpu_log_refuses_empty_crlf_and_non_ascii_lines(raw: bytes) -> None:
    with pytest.raises(UnparsableSample):
        parse_gpu_log(raw)


def test_midnight_is_crossed_by_difference() -> None:
    first = with_field(0, "2026/11/10 23:59:59.990")
    second = with_field(0, "2026/11/11 00:00:00.040")
    assert [sample.t_ms for sample in parse_gpu_log(gpu_log(first, second))] == [0, 50]
    assert parse_gpu_line(second, machine._stamp_ms("2026/11/10 23:59:59.990")).t_ms == 50


def test_a_backwards_stamp_is_refused() -> None:
    with pytest.raises(SampleClockBackwards):
        parse_gpu_log(gpu_log(gpu_line(0), gpu_line(100), gpu_line(99)))


def test_an_unparsable_line_wins_over_a_backwards_one() -> None:
    with pytest.raises(UnparsableSample):
        parse_gpu_log(gpu_log(gpu_line(0), gpu_line(100), gpu_line(50), with_field(1, "[N/A]")))


def test_equal_stamps_are_accepted() -> None:
    assert [sample.t_ms for sample in parse_gpu_log(gpu_log(gpu_line(0), gpu_line(50), gpu_line(50)))] == [0, 50, 50]


def test_a_captured_length_log_passes() -> None:
    samples = parse_gpu_log(gpu_log(*(gpu_line(51 * n, *LOADED) for n in range(232))))
    assert len(samples) == 232
    assert samples[-1].t_ms == 51 * 231
    check_sample_rate(samples, 51 * 231 + 30)


# ---------------------------------------------------------------- check_sample_rate


def samples_at(*t_ms: int) -> tuple[GpuSample, ...]:
    return tuple(GpuSample(t, *BASELINE, "1552 MHz", "11001 MHz", "55", "9.86 W") for t in t_ms)


TEN_EACH = (*range(0, 3000, 100), 3000)  # 10 in each of three whole seconds, and the last on the boundary


def test_ten_in_every_whole_second_passes() -> None:
    check_sample_rate(samples_at(*TEN_EACH), 3000)


def test_nine_in_one_second_fails() -> None:
    with pytest.raises(SampleRateTooLow):
        check_sample_rate(samples_at(*(t for t in TEN_EACH if t != 1500)), 3000)


def test_the_partial_last_second_is_ignored() -> None:
    check_sample_rate(samples_at(*TEN_EACH, 3500), 3500)


def test_the_count_term_against_harness_time() -> None:
    with pytest.raises(SampleRateTooLow):
        check_sample_rate(samples_at(*range(0, 2500, 100)), 3000)
    check_sample_rate(samples_at(0), 999)
    with pytest.raises(SampleRateTooLow):
        check_sample_rate(samples_at(0), 5000)


def test_the_rate_comes_from_the_evidence_function(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def spy(samples: tuple[GpuSample, ...], elapsed_ms: int) -> evidence.SampleRate:
        calls.append((samples, elapsed_ms))
        return evidence.sample_rate(samples, elapsed_ms)

    monkeypatch.setattr(machine, "sample_rate", spy)
    samples = samples_at(*TEN_EACH)
    check_sample_rate(samples, 3000)
    assert calls == [(samples, 3000)]


# ---------------------------------------------------------------- the other parsers


def test_apps_process_lines_counts_rows_after_the_header() -> None:
    assert apps_process_lines(apps_output()) == 0
    assert apps_process_lines(apps_output(133532)) == 1
    assert apps_process_lines(apps_output(9001, 9002)) == 2
    assert apps_process_lines(apps_output(9001) + b"\n\n") == 1
    assert apps_process_lines(apps_output() + b"\n") == 0


@pytest.mark.parametrize(
    "raw",
    [
        b"pid, name, used_memory [MiB]\n",
        apps_output().replace(b"\n", b"\r\n"),
        b"",
        APPS_HEADER.encode("ascii"),
        apps_output() + b"\xff\n",
        b"\n" + apps_output(),
    ],
)
def test_apps_process_lines_refuses_a_bad_output(raw: bytes) -> None:
    with pytest.raises(CudaCheckFailed):
        apps_process_lines(raw)


def test_parse_rss_anon_reads_the_kb_value() -> None:
    assert parse_rss_anon(proc_status()) == 269_428
    assert parse_rss_anon(proc_status(12)) == 12
    assert parse_rss_anon(b"Name:\tx\nRssAnon:   77 kB\n") == 77
    assert parse_rss_anon(b"RssAnon: \t 5 kB") == 5
    assert parse_rss_anon(b"Name:\tx\nRssFile:\t  133772 kB\n") is None
    assert parse_rss_anon(b"") is None


@pytest.mark.parametrize(
    "status",
    [
        b"RssAnon:\t12 MB\n",
        b"RssAnon:\t\n",
        b"RssAnon:12 kB\n",
        b"RssAnon:\t-1 kB\n",
        proc_status() + b"RssAnon:\t  5 kB\n",
    ],
)
def test_parse_rss_anon_refuses_a_malformed_or_doubled_line(status: bytes) -> None:
    with pytest.raises(MachineFault):
        parse_rss_anon(status)


def test_parse_pswpout_reads_the_counter() -> None:
    assert parse_pswpout(vmstat(1204)) == 1204
    assert parse_pswpout(b"pswpout 0") == 0


@pytest.mark.parametrize(
    "raw",
    [b"pswpin 0\n", b"", vmstat(1) + b"pswpout 2\n", b"pswpout x\n", b"pswpout 5 6\n", b"pswpout  5\n"],
)
def test_parse_pswpout_refuses_a_missing_doubled_or_malformed_line(raw: bytes) -> None:
    with pytest.raises(MachineFault):
        parse_pswpout(raw)


HUGE = "9" * 5000  # past CPython's integer-string limit


@NEEDS_DIGIT_LIMIT
def test_a_number_past_the_digit_limit_is_a_machine_fault() -> None:
    for line in (with_field(1, HUGE + " MiB"), with_field(2, HUGE + " MiB")):
        with pytest.raises(UnparsableSample):
            parse_gpu_line(line, None)
        with pytest.raises(UnparsableSample):
            parse_gpu_log(gpu_log(gpu_line(0), line))
    with pytest.raises(MachineFault):
        parse_rss_anon(f"RssAnon:\t{HUGE} kB\n".encode("ascii"))
    with pytest.raises(MachineFault):
        parse_pswpout(f"pswpout {HUGE}\n".encode("ascii"))


# ---------------------------------------------------------------- _list_pids and _start_thread


def test_list_pids_keeps_ascii_digits_only(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def listdir(path: str) -> list[str]:
        asked.append(path)
        return ["42", "self", "1", "x1", chr(0x0661)]

    monkeypatch.setattr(machine.os, "listdir", listdir)
    assert machine._list_pids() == (1, 42)
    assert asked == ["/proc"]


def test_a_step_that_ends_gives_none() -> None:
    calls: list[int] = []
    finished = threading.Event()

    def step() -> bool:
        calls.append(1)
        if len(calls) == 3:
            finished.set()
            return False
        return True

    stop = machine._start_thread(step, 0.001)
    assert finished.wait(THREAD_JOIN_SEC)
    assert stop(THREAD_JOIN_SEC) is None
    assert len(calls) == 3


def test_a_raising_step_gives_its_class_name() -> None:
    called = threading.Event()

    def step() -> bool:
        called.set()
        raise ValueError("synthetic")

    stop = machine._start_thread(step, 0.001)
    assert called.wait(THREAD_JOIN_SEC)
    assert stop(THREAD_JOIN_SEC) == "ValueError"


def test_a_blocked_step_gives_still_running() -> None:
    entered = threading.Event()
    release = threading.Event()

    def step() -> bool:
        entered.set()
        release.wait(THREAD_JOIN_SEC)
        return False

    stop = machine._start_thread(step, 0.001)
    try:
        assert entered.wait(THREAD_JOIN_SEC)
        assert stop(0.05) == "still running"
    finally:
        release.set()
        assert stop(THREAD_JOIN_SEC) is None


def test_the_stopper_ends_a_step_that_keeps_going() -> None:
    calls: list[int] = []
    ran = threading.Event()

    def step() -> bool:
        calls.append(1)
        ran.set()
        return True

    stop = machine._start_thread(step, 0.001)
    try:
        assert ran.wait(THREAD_JOIN_SEC)
    finally:
        result = stop(THREAD_JOIN_SEC)
    assert result is None
    thread = inspect.getclosurevars(stop).nonlocals["thread"]
    assert not thread.is_alive()
    assert thread.daemon
    count = len(calls)
    assert count >= 1
    assert len(calls) == count


# ---------------------------------------------------------------- the log formats and load_evidence


def contention(ended_ms: int | None = None) -> ContentionRecord:
    ended = ended_ms is not None
    return ContentionRecord("c" * 64, 5.0, 0 if ended else -15, ended, ended_ms, None if ended else "terminated")


def host(key: RunKey = TIMED, *, problem: str | None = None, record: ContentionRecord | None = None) -> HostRecord:
    return HostRecord(key, 0, 1204, 1204, 3000, "terminated", True, problem, record)


PROC = (ProcSample(0, 269_000), ProcSample(100, 269_428))
GPU_RAW = gpu_log(gpu_line(0, *BASELINE), *(gpu_line(t, *LOADED) for t in range(50, 3001, 50)))


def write_logs(
    run_dir: Path,
    key: RunKey,
    *,
    host_raw: bytes | None = None,
    proc_raw: bytes | None = None,
    gpu_raw: bytes | None = GPU_RAW,
    record: HostRecord | None = None,
) -> None:
    (run_dir / "logs").mkdir(exist_ok=True)
    files = {
        "host": machine._host_json(record or host(key)) if host_raw is None else host_raw,
        "proc": machine._proc_csv(PROC) if proc_raw is None else proc_raw,
        "gpu": gpu_raw,
    }
    for kind, raw in files.items():
        if raw is not None:
            (run_dir / machine_log_name(key, kind)).write_bytes(raw)


def host_dict(record: HostRecord) -> dict[str, Any]:
    return dict(record_loads(machine._host_json(record)[:-1]))


def test_the_proc_csv_form() -> None:
    assert machine._proc_csv(()) == b"elapsed_ms,rss_anon_kib\n"
    assert machine._proc_csv(PROC) == b"elapsed_ms,rss_anon_kib\n0,269000\n100,269428\n"


def test_the_host_json_form() -> None:
    raw = machine._host_json(host(CONTENDED, record=contention(2000)))
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert record_loads(raw[:-1]) == {
        "record": "host",
        "schema": 1,
        "key": CONTENDED_UNIT,
        "apps_lines": 0,
        "pswpout_before": 1204,
        "pswpout_after": 1204,
        "elapsed_ms": 3000,
        "sampler_stopped_how": "terminated",
        "server_pid_found": True,
        "problem": None,
        "contention": {
            "command_sha256": "c" * 64,
            "lead_sec": 5.0,
            "exit_code": 0,
            "ended_before_stop": True,
            "ended_before_stop_ms": 2000,
            "stopped_how": None,
        },
    }
    assert set(machine._HOST_KEYS) == set(record_loads(raw[:-1]))


@pytest.mark.parametrize(
    ("key", "record"),
    [(TIMED, None), (CONTENDED, contention()), (CONTENDED, contention(2000))],
)
def test_the_logs_load_back_field_by_field(tmp_path: Path, key: RunKey, record: ContentionRecord | None) -> None:
    written = host(key, record=record)
    write_logs(tmp_path, key, record=written)
    loaded = load_evidence(str(tmp_path), key, FakeFiles())
    assert loaded == AttemptEvidence(key, parse_gpu_log(GPU_RAW), PROC, written)
    assert loaded is not None
    assert loaded.host == written
    assert loaded.proc == PROC
    assert loaded.rise_mib == 4308
    assert loaded.peak_rss_anon_bytes == 269_428 * 1024


def test_no_host_record_reads_nothing_else(tmp_path: Path) -> None:
    write_logs(tmp_path, TIMED, host_raw=None)
    (tmp_path / machine_log_name(TIMED, "host")).unlink()
    files = FakeFiles()
    assert load_evidence(str(tmp_path), TIMED, files) is None
    assert files.reads == [f"{tmp_path}/{machine_log_name(TIMED, 'host')}"]


def bad_host(change: dict[str, Any]) -> bytes:
    data = host_dict(host(CONTENDED, record=contention()))
    for name, value in change.items():
        if value is KeyError:
            del data[name]
        else:
            data[name] = value
    return record_json(data) + b"\n"


def bad_contention(name: str, value: Any) -> bytes:
    data = host_dict(host(CONTENDED, record=contention()))
    data["contention"][name] = value
    return record_json(data) + b"\n"


@pytest.mark.parametrize(
    "host_raw",
    [
        b"{\n",
        b"",
        machine._host_json(host(CONTENDED)).rstrip(b"\n"),
        machine._host_json(host(CONTENDED)) * 2,
        b"[]\n",
        bad_host({"extra": 1}),
        bad_host({"problem": KeyError}),
        bad_host({"key": OTHER_UNIT}),
        bad_host({"record": "launch"}),
        bad_host({"schema": 2}),
        bad_host({"schema": True}),
        bad_host({"apps_lines": True}),
        bad_host({"elapsed_ms": 3000.0}),
        bad_host({"server_pid_found": 1}),
        bad_host({"sampler_stopped_how": None}),
        bad_host({"problem": 1}),
        bad_host({"contention": []}),
        bad_contention("lead_sec", True),
        bad_contention("lead_sec", 10**400),
        bad_contention("lead_sec", 0.5).replace(b'"lead_sec":0.5', b'"lead_sec":1e400'),
        bad_contention("exit_code", "0"),
        bad_contention("ended_before_stop", 0),
        bad_contention("stopped_how", 1),
        bad_contention("extra", 1),
        machine._host_json(host(CONTENDED)).replace(b'"host"', b'"h\xc3\xa9"'),
        machine._host_json(host(CONTENDED)).replace(b'"problem":null', b'"problem":null,"problem":null'),
    ],
)
def test_a_bad_host_record_is_refused_naming_the_unit(tmp_path: Path, host_raw: bytes) -> None:
    write_logs(tmp_path, CONTENDED, host_raw=host_raw)
    with pytest.raises(StoreError, match=re.escape(_unit_name(CONTENDED))) as caught:
        load_evidence(str(tmp_path), CONTENDED, FakeFiles())
    assert str(tmp_path) not in str(caught.value)


BAD_LOGS = {
    "proc_missing": {"proc_raw": b"MISSING"},
    "gpu_missing": {"gpu_raw": None},
    "proc_header": {"proc_raw": b"elapsed,rss\n0,1\n"},
    "proc_row": {"proc_raw": b"elapsed_ms,rss_anon_kib\n0,1\n2;3\n"},
    "proc_negative": {"proc_raw": b"elapsed_ms,rss_anon_kib\n-1,5\n"},
    "proc_no_lf": {"proc_raw": b"elapsed_ms,rss_anon_kib\n0,1"},
    "proc_non_ascii": {"proc_raw": b"elapsed_ms,rss_anon_kib\n0,1\xff\n"},
    "gpu_unparsable": {"gpu_raw": gpu_log(gpu_line(0), with_field(1, "[N/A]"))},
    "gpu_backwards": {"gpu_raw": gpu_log(gpu_line(100), gpu_line(0))},
    "gpu_no_line": {"gpu_raw": gpu_line(0).encode("ascii")},
    "gpu_empty": {"gpu_raw": b""},
}


def apply_bad_log(tmp_path: Path, key: RunKey, name: str, problem: str | None) -> None:
    options = dict(BAD_LOGS[name])
    missing_proc = options.get("proc_raw") == b"MISSING"
    if missing_proc:
        options.pop("proc_raw")
    write_logs(tmp_path, key, record=host(key, problem=problem), **options)
    if missing_proc:
        (tmp_path / machine_log_name(key, "proc")).unlink()


@pytest.mark.parametrize("name", sorted(BAD_LOGS))
def test_a_counted_attempt_with_a_bad_log_is_refused(tmp_path: Path, name: str) -> None:
    apply_bad_log(tmp_path, TIMED, name, None)
    with pytest.raises(StoreError, match=re.escape(_unit_name(TIMED))) as caught:
        load_evidence(str(tmp_path), TIMED, FakeFiles())
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize("name", sorted(set(BAD_LOGS) - {"proc_missing", "gpu_missing"}))
def test_a_voided_attempt_with_a_bad_log_gives_none(tmp_path: Path, name: str) -> None:
    apply_bad_log(tmp_path, TIMED, name, "UnparsableSample")
    assert load_evidence(str(tmp_path), TIMED, FakeFiles()) is None


@pytest.mark.parametrize("name", ["proc_missing", "gpu_missing"])
@pytest.mark.parametrize("problem", [None, "UnparsableSample", "ProcSamplerFailed: still running"])
def test_a_missing_log_is_refused_whatever_the_problem(tmp_path: Path, name: str, problem: str | None) -> None:
    apply_bad_log(tmp_path, TIMED, name, problem)
    with pytest.raises(StoreError, match=re.escape(_unit_name(TIMED))):
        load_evidence(str(tmp_path), TIMED, FakeFiles())


def test_an_out_of_range_lead_is_refused_by_name(tmp_path: Path) -> None:
    for raw in (
        bad_contention("lead_sec", 10**400),
        bad_contention("lead_sec", 0.5).replace(b'"lead_sec":0.5', b'"lead_sec":1e400'),
    ):
        write_logs(tmp_path, CONTENDED, host_raw=raw)
        with pytest.raises(StoreError, match="lead_sec is out of range") as caught:
            load_evidence(str(tmp_path), CONTENDED, FakeFiles())
        assert _unit_name(CONTENDED) in str(caught.value)
        assert str(tmp_path) not in str(caught.value)


DIGIT_LIMIT_LOGS = {
    "proc": {"proc_raw": f"elapsed_ms,rss_anon_kib\n0,{HUGE}\n".encode("ascii")},
    "gpu": {"gpu_raw": gpu_log(gpu_line(0), with_field(1, HUGE + " MiB"))},
}


@NEEDS_DIGIT_LIMIT
@pytest.mark.parametrize("name", sorted(DIGIT_LIMIT_LOGS))
@pytest.mark.parametrize("problem", [None, "UnparsableSample"])
def test_a_number_past_the_digit_limit_is_refused_or_voided(tmp_path: Path, name: str, problem: str | None) -> None:
    write_logs(tmp_path, TIMED, record=host(TIMED, problem=problem), **DIGIT_LIMIT_LOGS[name])
    if problem is not None:
        assert load_evidence(str(tmp_path), TIMED, FakeFiles()) is None
        return
    with pytest.raises(StoreError, match=re.escape(_unit_name(TIMED))) as caught:
        load_evidence(str(tmp_path), TIMED, FakeFiles())
    assert str(tmp_path) not in str(caught.value)


def test_a_voided_attempt_with_readable_logs_gives_its_evidence(tmp_path: Path) -> None:
    written = host(TIMED, problem="SampleRateTooLow")
    write_logs(tmp_path, TIMED, record=written, proc_raw=machine._proc_csv(()))
    loaded = load_evidence(str(tmp_path), TIMED, FakeFiles())
    assert loaded == AttemptEvidence(TIMED, parse_gpu_log(GPU_RAW), (), written)


def test_the_loader_matches_the_evidence_loader_type() -> None:
    assert list(inspect.signature(load_evidence).parameters) == ["run_dir", "key", "read_bytes"]


# ---------------------------------------------------------------- the hooks: a rig on a fake host

NO_INPUTS = SimpleNamespace(all_requests=lambda: ())
UNIT = _unit_name(TIMED)
SERVER_PID = 4100
Wrap = Callable[[MachineTools], MachineTools]


@dataclass
class Rig:
    host: FakeHost
    popen: FakeMachinePopen
    threads: FakeThreads
    hooks: MachineHooks
    run_dir: Path
    fsyncs: list[int]

    def files(self) -> set[str]:
        return {path.name for path in (self.run_dir / "logs").iterdir()}

    def host_json(self, key: RunKey = TIMED) -> dict[str, Any]:
        raw = (self.run_dir / machine_log_name(key, "host")).read_bytes()
        return dict(record_loads(raw[:-1]))

    def proc_csv(self, key: RunKey = TIMED) -> bytes:
        return (self.run_dir / machine_log_name(key, "proc")).read_bytes()

    def serve(self, key: RunKey = TIMED, pid: int = SERVER_PID, status: Any = None) -> None:
        """A running server for `key`'s candidate in the fake /proc, as `FakeHost.popen` registers one."""
        candidate = next(row for row in SCHEDULE_CANDIDATES if row.candidate_id == key.candidate)
        self.host.processes[f"server{pid}"] = FakeProcess(pid=pid)
        table = self.host.files.table
        table[f"/proc/{pid}/cmdline"] = cmdline_bytes(frozen_argv(CONDITIONS, candidate))
        table[f"/proc/{pid}/status"] = proc_status() if status is None else status

    def gone(self, pid: int = SERVER_PID, error: type[OSError] = FileNotFoundError) -> None:
        for entry in ("cmdline", "status"):
            self.host.files.table[f"/proc/{pid}/{entry}"] = error(entry)

    def step(self) -> bool:
        step = self.threads.current
        assert step is not None
        return step()

    def clean_attempt(self, key: RunKey = TIMED, seconds: float = 3.0) -> None:
        """Before-hook, a found server with one reading, and `seconds` of fake time."""
        self.hooks.before_timed_run(key)
        self.serve(key)
        self.threads.drive()
        self.host.clock.now += seconds

    def assert_left_clean(self, key: RunKey = TIMED) -> None:
        assert self.popen.running() == []
        assert self.hooks._live is None
        assert not (self.run_dir / machine_log_name(key, "host")).exists()


def make_rig(
    tmp_path: Path,
    *,
    threads: FakeThreads | None = None,
    wrap: Wrap | None = None,
    **popen_options: Any,
) -> Rig:
    run_dir = tmp_path / "run_1"
    (run_dir / "logs").mkdir(parents=True)
    host = FakeHost(NO_INPUTS, proc_status=proc_status())  # type: ignore[arg-type]
    threads = threads or FakeThreads()
    install_machine(host, threads)
    popen = FakeMachinePopen(host.clock, **popen_options)
    tools = machine_tools(host, popen, threads)
    fsyncs: list[int] = []
    hooks = MachineHooks(
        MACHINE_CONFIG,
        str(run_dir),
        CONDITIONS,
        SCHEDULE_CANDIDATES,
        wrap(tools) if wrap is not None else tools,
        fsyncs.append,
    )
    return Rig(host, popen, threads, hooks, run_dir, fsyncs)


def with_launch(**changes: Any) -> Wrap:
    return lambda tools: dataclasses.replace(tools, launch=dataclasses.replace(tools.launch, **changes))


def interrupt_on(seconds: float, clock: list[Any]) -> Callable[[float], None]:
    """A sleep that raises `KeyboardInterrupt` when asked to sleep `seconds`, and otherwise moves `clock[0]`."""

    def sleep(asked: float) -> None:
        if asked == seconds:
            raise KeyboardInterrupt
        clock[0].sleep(asked)

    return sleep


# ---------------------------------------------------------------- before_timed_run


def test_each_spawn_has_its_exact_argv_env_and_log(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.clean_attempt()
    (apps_args, apps_kwargs), (gpu_args, gpu_kwargs) = rig.popen.calls
    assert apps_args == list(apps_argv(MACHINE_CONFIG))
    assert gpu_args == list(gpu_argv(MACHINE_CONFIG))
    assert apps_kwargs["env"] == gpu_kwargs["env"] == {"PATH": CLEAN_PATH}
    assert Path(apps_kwargs["stdout"].name) == rig.run_dir / machine_log_name(TIMED, "apps")
    assert Path(gpu_kwargs["stdout"].name) == rig.run_dir / machine_log_name(TIMED, "gpu")
    assert apps_kwargs["stderr"] is gpu_kwargs["stderr"] is subprocess.STDOUT
    assert (rig.run_dir / machine_log_name(TIMED, "apps")).read_bytes() == apps_output()
    assert rig.popen.of("apps")[0].signals == []
    rig.hooks.after_timed_run(TIMED)
    assert rig.popen.running() == []


def test_the_before_hook_runs_apps_vmstat_sampler_thread_in_order(tmp_path: Path) -> None:
    events: list[str] = []

    def wrap(tools: MachineTools) -> MachineTools:
        launch = tools.launch

        def popen(args: list[str], **kwargs: Any) -> Any:
            events.append("apps" if args[1].startswith("--query-compute-apps") else "sampler")
            return launch.popen(args, **kwargs)

        def read_bytes(path: str) -> bytes:
            if path == "/proc/vmstat":
                events.append("vmstat")
            return launch.read_bytes(path)

        def start(step: Callable[[], bool], interval: float) -> Callable[[float], str | None]:
            events.append("thread")
            return tools.start_thread(step, interval)

        return MachineTools(
            dataclasses.replace(launch, popen=popen, read_bytes=read_bytes), tools.list_pids, start_thread=start
        )

    rig = make_rig(tmp_path, wrap=wrap)
    rig.hooks.before_timed_run(TIMED)
    assert events == ["apps", "vmstat", "sampler", "thread"]
    assert rig.hooks._live is not None
    assert rig.hooks._live.pswpout_before == 1204
    rig.serve()
    rig.threads.drive()
    rig.hooks.after_timed_run(TIMED)
    assert rig.popen.running() == []


def test_another_cuda_process_voids_before_the_sampler(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, apps={UNIT: apps_output(9001)})
    with pytest.raises(OtherCudaProcess) as caught:
        rig.hooks.before_timed_run(TIMED)
    assert "9001" not in str(caught.value)
    assert rig.popen.of("gpu") == []
    rig.assert_left_clean()


@pytest.mark.parametrize(
    "options",
    [
        {"apps_polls": {UNIT: (6,)}},
        {"spawn_errors": {"apps": OSError("synthetic")}},
        {"apps": {UNIT: b"pid, name, used_memory [MiB]\n"}},
    ],
    ids=["exit_6", "spawn_error", "wrong_header"],
)
def test_a_failed_apps_query_is_a_cuda_check_failure(tmp_path: Path, options: dict[str, Any]) -> None:
    rig = make_rig(tmp_path, **options)
    with pytest.raises(CudaCheckFailed):
        rig.hooks.before_timed_run(TIMED)
    assert rig.popen.of("gpu") == []
    rig.assert_left_clean()


def test_a_hung_apps_query_is_stopped_after_its_deadline(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, apps_polls={UNIT: ()})
    with pytest.raises(CudaCheckFailed):
        rig.hooks.before_timed_run(TIMED)
    assert rig.host.clock.now >= APPS_DEADLINE_SEC
    assert rig.popen.of("apps")[0].signals == ["terminate"]
    assert rig.popen.of("gpu") == []
    rig.assert_left_clean()


def test_an_apps_query_that_will_not_stop_is_a_cuda_check_failure(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, apps_polls={UNIT: ()}, apps_waits={UNIT: (None, None)})
    with pytest.raises(CudaCheckFailed):
        rig.hooks.before_timed_run(TIMED)
    apps = rig.popen.of("apps")[0]
    assert apps.signals == ["terminate", "kill"]
    assert rig.popen.of("gpu") == []
    assert "/proc/vmstat" not in rig.host.files.reads
    assert rig.popen.running() == [apps]
    assert rig.hooks._live is None
    assert not (rig.run_dir / machine_log_name(TIMED, "host")).exists()


@pytest.mark.parametrize("raw", [b"pswpin 0\n", OSError("synthetic")], ids=["no_pswpout", "unreadable"])
def test_a_bad_vmstat_before_the_attempt_is_a_machine_fault(tmp_path: Path, raw: Any) -> None:
    rig = make_rig(tmp_path)
    rig.host.files.table["/proc/vmstat"] = raw
    with pytest.raises(MachineFault) as caught:
        rig.hooks.before_timed_run(TIMED)
    assert type(caught.value) is MachineFault
    assert rig.popen.of("gpu") == []
    rig.assert_left_clean()


def test_a_sampler_that_does_not_spawn_is_a_sampler_failure(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, spawn_errors={"gpu": OSError("synthetic")})
    with pytest.raises(SamplerFailed):
        rig.hooks.before_timed_run(TIMED)
    rig.assert_left_clean()


def test_no_first_line_is_a_sampler_failure_after_its_wait(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, gpu={UNIT: GpuScript(first_line=False)})
    with pytest.raises(SamplerFailed):
        rig.hooks.before_timed_run(TIMED)
    assert rig.host.clock.now >= SAMPLER_READY_SEC
    assert set(rig.host.clock.sleeps) == {LOG_POLL_SEC}
    assert rig.popen.of("gpu")[0].signals == ["terminate"]
    assert rig.threads.started == 0
    rig.assert_left_clean()


def test_an_exit_before_the_first_line_is_a_sampler_failure(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, gpu={UNIT: GpuScript(first_line=False, exit_early=True)})
    with pytest.raises(SamplerFailed):
        rig.hooks.before_timed_run(TIMED)
    assert rig.popen.of("gpu")[0].signals == []
    assert rig.host.clock.now == 0.0
    rig.assert_left_clean()


def test_a_sampler_that_will_not_stop_still_gives_a_sampler_failure(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, gpu={UNIT: GpuScript(first_line=False, waits=(None, None))})
    with pytest.raises(SamplerFailed):
        rig.hooks.before_timed_run(TIMED)
    sampler = rig.popen.of("gpu")[0]
    assert rig.host.clock.now >= SAMPLER_READY_SEC
    assert sampler.signals == ["terminate", "kill"]
    assert rig.popen.running() == [sampler]
    assert rig.hooks._live is None


def test_a_thread_that_does_not_start_stops_the_sampler(tmp_path: Path) -> None:
    def refuse(step: Callable[[], bool], interval: float) -> Callable[[float], str | None]:
        raise RuntimeError("synthetic")

    rig = make_rig(tmp_path, wrap=lambda tools: dataclasses.replace(tools, start_thread=refuse))
    with pytest.raises(ProcSamplerFailed):
        rig.hooks.before_timed_run(TIMED)
    assert rig.popen.of("gpu")[0].signals == ["terminate"]
    rig.assert_left_clean()


def test_an_interrupt_in_the_first_line_wait_leaves_nothing_running(tmp_path: Path) -> None:
    clock: list[Any] = []
    rig = make_rig(tmp_path, gpu={UNIT: GpuScript(first_line=False)}, wrap=with_launch(sleep=interrupt_on(0.1, clock)))
    clock.append(rig.host.clock)
    with pytest.raises(KeyboardInterrupt):
        rig.hooks.before_timed_run(TIMED)
    assert rig.popen.of("gpu")[0].signals == ["terminate"]
    rig.assert_left_clean()


@pytest.mark.parametrize(
    "key",
    [RunKey(Q4B, "pre_run", None, 1), RunKey(Q4B, "unconstrained", None, 1)],
    ids=["pre_run", "unconstrained"],
)
def test_an_unhooked_phase_is_a_harness_bug(tmp_path: Path, key: RunKey) -> None:
    rig = make_rig(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        rig.hooks.before_timed_run(key)
    assert not isinstance(caught.value, MachineFault)
    assert rig.popen.calls == []


def test_a_second_before_hook_is_a_harness_bug(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    with pytest.raises(RuntimeError):
        rig.hooks.before_timed_run(RunKey(Q4B, "timed_run", 2, 2))
    assert len(rig.popen.calls) == 2
    rig.serve()
    rig.threads.drive()
    rig.hooks.after_timed_run(TIMED)
    assert rig.popen.running() == []


# ---------------------------------------------------------------- the /proc step


def test_no_match_keeps_polling_and_skips_the_other_pids(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    assert rig.step() is True
    assert {"/proc/1/cmdline", "/proc/77/cmdline"} <= set(rig.host.files.reads)
    assert "/proc/1/status" not in rig.host.files.reads
    live = rig.hooks._live
    assert live is not None
    assert (live.pid, live.samples, live.two_matches) == (None, [], False)


def test_the_matching_call_takes_the_first_reading(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve()
    rig.host.clock.now += 0.25
    assert rig.step() is True
    live = rig.hooks._live
    assert live is not None
    assert live.pid == SERVER_PID
    assert live.samples == [ProcSample(250, 269_428)]


@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
def test_a_server_that_leaves_after_k_readings_gives_k_samples(tmp_path: Path, error: type[OSError]) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve()
    for _ in range(3):
        rig.host.clock.now += PROC_INTERVAL_SEC
        assert rig.step() is True
    rig.gone(error=error)
    assert rig.step() is False
    rig.hooks.after_timed_run(TIMED)
    assert rig.proc_csv().count(b"\n") == 1 + 3
    assert rig.host_json()["problem"] is None


def test_a_status_without_rss_anon_ends_the_step(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve(status=b"Name:\tllama-server\nState:\tZ (zombie)\n")
    assert rig.step() is False


def test_two_matches_end_the_step_and_give_server_not_found(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve()
    rig.serve(pid=SERVER_PID + 1)
    assert rig.step() is False
    with pytest.raises(ServerNotFound):
        rig.hooks.after_timed_run(TIMED)
    record = rig.host_json()
    assert (record["problem"], record["server_pid_found"]) == ("ServerNotFound", False)
    assert rig.popen.running() == []


def test_a_server_never_seen_gives_server_not_found(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.threads.drive()
    with pytest.raises(ServerNotFound):
        rig.hooks.after_timed_run(TIMED)
    assert rig.host_json()["problem"] == "ServerNotFound"
    assert rig.host_json()["server_pid_found"] is False


def test_a_server_found_but_never_read_gives_no_rss_sample(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve(status=FileNotFoundError("status"))
    assert rig.step() is False
    with pytest.raises(NoRssSample):
        rig.hooks.after_timed_run(TIMED)
    record = rig.host_json()
    assert (record["problem"], record["server_pid_found"]) == ("NoRssSample", True)


def test_a_malformed_rss_line_makes_the_step_raise(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve(status=b"RssAnon:\t12 MB\n")
    with pytest.raises(MachineFault):
        rig.step()
    rig.threads.drive()
    assert rig.threads.caught == "MachineFault"
    with pytest.raises(ProcSamplerFailed):
        rig.hooks.after_timed_run(TIMED)
    assert rig.host_json()["problem"] == "ProcSamplerFailed: MachineFault"


def test_a_found_pid_is_never_searched_again(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve()
    assert rig.step() is True
    scans = rig.host.files.reads.count("/proc/1/cmdline")
    rig.serve(pid=SERVER_PID + 1)
    assert rig.step() is True
    assert rig.host.files.reads.count("/proc/1/cmdline") == scans
    live = rig.hooks._live
    assert live is not None
    assert (live.pid, len(live.samples), live.two_matches) == (SERVER_PID, 2, False)


def test_any_other_error_propagates_from_the_step(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    rig.serve(status=PermissionError("status"))
    with pytest.raises(PermissionError):
        rig.step()


@pytest.mark.parametrize("served", [False, True], ids=["no_server_yet", "server_up"])
def test_a_thread_may_run_the_step_before_start_returns(tmp_path: Path, served: bool) -> None:
    results: list[bool] = []

    def wrap(tools: MachineTools) -> MachineTools:
        def start(step: Callable[[], bool], interval: float) -> Callable[[float], str | None]:
            results.append(step())
            return tools.start_thread(step, interval)

        return dataclasses.replace(tools, start_thread=start)

    rig = make_rig(tmp_path, wrap=wrap)
    if served:
        rig.serve()
    rig.hooks.before_timed_run(TIMED)
    assert results == [True]
    live = rig.hooks._live
    assert live is not None
    assert len(live.samples) == (1 if served else 0)


def test_a_stale_step_adds_nothing_to_the_next_attempt(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.hooks.before_timed_run(TIMED)
    stale = rig.threads.current
    assert stale is not None
    with pytest.raises(ServerNotFound):
        rig.hooks.after_timed_run(TIMED)
    second = RunKey(Q4B, "timed_run", 2, 2)
    rig.hooks.before_timed_run(second)
    rig.serve(second)
    stale()
    live = rig.hooks._live
    assert live is not None
    assert (live.pid, live.samples) == (None, [])


# ---------------------------------------------------------------- after_timed_run


def test_the_after_hook_stops_the_thread_first_then_the_sampler(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.clean_attempt()
    sampler = rig.popen.of("gpu")[0]
    seen: list[list[str]] = []
    original = rig.threads.stop

    def stop(timeout: float) -> str | None:
        seen.append(list(sampler.signals))
        return original(timeout)

    rig.threads.stop = stop  # type: ignore[method-assign]
    rig.hooks.after_timed_run(TIMED)
    assert seen == [[]]
    assert rig.threads.timeouts == [THREAD_JOIN_SEC]
    assert sampler.signals == ["terminate"]
    assert sampler.exit_code == 0
    assert rig.host_json()["sampler_stopped_how"] == "terminated"


def test_the_logs_are_opened_new_and_synced(tmp_path: Path) -> None:
    opened: list[str] = []

    def wrap(tools: MachineTools) -> MachineTools:
        def open_log(path: str) -> Any:
            opened.append(Path(path).name)
            return tools.launch.open_log(path)

        return with_launch(open_log=open_log)(tools)

    rig = make_rig(tmp_path, wrap=wrap)
    rig.clean_attempt()
    rig.hooks.after_timed_run(TIMED)
    assert opened == [f"{UNIT}.apps.txt", f"{UNIT}.gpu.csv", f"{UNIT}.proc.csv", f"{UNIT}.host.json"]
    assert len(rig.fsyncs) == 2
    with pytest.raises(FileExistsError):
        rig.hooks.before_timed_run(TIMED)


def test_a_clean_attempt_writes_its_exact_host_record(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.clean_attempt()
    rig.host.clock.now += 0.1
    rig.threads.drive()
    rig.hooks.after_timed_run(TIMED)
    assert rig.host_json() == {
        "record": "host",
        "schema": 1,
        "key": UNIT,
        "apps_lines": 0,
        "pswpout_before": 1204,
        "pswpout_after": 1204,
        "elapsed_ms": 3100,
        "sampler_stopped_how": "terminated",
        "server_pid_found": True,
        "problem": None,
        "contention": None,
    }
    assert rig.proc_csv() == b"elapsed_ms,rss_anon_kib\n0,269428\n3100,269428\n"
    loaded = load_evidence(str(rig.run_dir), TIMED, FakeFiles())
    assert loaded is not None
    assert loaded.rise_mib == LOADED[0] - BASELINE[0]
    assert len(loaded.gpu) == 1 + 62
    assert rig.files() == {f"{UNIT}.{suffix}" for suffix in ("apps.txt", "gpu.csv", "proc.csv", "host.json")}
    assert rig.popen.running() == []
    assert rig.hooks._live is None


def test_the_elapsed_window_runs_from_the_first_line_to_the_sampler_stop(tmp_path: Path) -> None:
    """t0 is the time the first complete line is seen, not the sampler's spawn or the apps query; t_end is taken just
    before the sampler's stop, so neither the stop's own wait nor the vmstat read after it counts."""
    clock: list[Any] = []
    sleeps: list[float] = []
    line_seen: list[float] = []
    vmstat_reads: list[str] = []
    gpu_path = tmp_path / "run_1" / machine_log_name(TIMED, "gpu")

    def wrap(tools: MachineTools) -> MachineTools:
        launch = tools.launch

        def sleep(asked: float) -> None:
            clock[0].sleep(asked)
            sleeps.append(asked)
            if len(sleeps) == 3:  # the apps query's one wait, then two waits for the first line
                with gpu_path.open("ab") as log:
                    log.write(gpu_log(gpu_line(0, *BASELINE)))
                line_seen.append(clock[0].now)

        def read_bytes(path: str) -> bytes:
            raw = launch.read_bytes(path)
            if path == "/proc/vmstat":
                vmstat_reads.append(path)
                if len(vmstat_reads) == 2:
                    clock[0].now += 0.5
            return raw

        return with_launch(sleep=sleep, read_bytes=read_bytes)(tools)

    rig = make_rig(tmp_path, wrap=wrap, gpu={UNIT: GpuScript(first_line=False)}, apps_polls={UNIT: (None, 0)})
    clock.append(rig.host.clock)
    rig.hooks.before_timed_run(TIMED)
    assert sleeps == [LOG_POLL_SEC] * 3
    live = rig.hooks._live
    assert live is not None
    assert line_seen == [live.t0]
    assert live.t0 == rig.host.clock.now
    rig.serve()
    rig.host.clock.now += 1.0
    step_time = rig.host.clock.now
    rig.threads.drive()
    rig.host.clock.now += 3.0
    rig.threads.drive()
    sampler = rig.popen.of("gpu")[0]
    original = sampler.wait

    def wait(timeout: float | None = None) -> int:
        rig.host.clock.now += 1.0
        return original(timeout)

    sampler.wait = wait  # type: ignore[method-assign]
    stop_start = rig.host.clock.now
    rig.hooks.after_timed_run(TIMED)
    assert rig.host.clock.now == stop_start + 1.0 + 0.5
    record = rig.host_json()
    assert record["elapsed_ms"] == int((stop_start - line_seen[0]) * 1000)
    assert record["problem"] is None
    first_row = rig.proc_csv().split(b"\n")[1]
    assert first_row == f"{int((step_time - line_seen[0]) * 1000)},269428".encode("ascii")
    assert rig.popen.running() == []


BAD_LINE = with_field(1, "[N/A]").encode("ascii") + b"\n"
PROBLEMS = {
    "still_running": ({"threads": ["still running"]}, ProcSamplerFailed, "ProcSamplerFailed: still running"),
    "step_raised": ({"threads": ["ValueError"]}, ProcSamplerFailed, "ProcSamplerFailed: ValueError"),
    "stop_failed": ({"gpu": GpuScript(waits=(None, None))}, SamplerFailed, "SamplerFailed"),
    "died": ({"gpu": GpuScript(exit_early=True)}, SamplerDied, "SamplerDied"),
    "unparsable": ({"gpu": GpuScript(extra=BAD_LINE)}, UnparsableSample, "UnparsableSample"),
    "backwards": ({"gpu": GpuScript(backwards=True)}, SampleClockBackwards, "SampleClockBackwards"),
    "rate": ({"gpu": GpuScript(rate_hz=8)}, SampleRateTooLow, "SampleRateTooLow"),
    "never_seen": ({"serve": "none"}, ServerNotFound, "ServerNotFound"),
    "two_matches": ({"serve": "two"}, ServerNotFound, "ServerNotFound"),
    "no_rss": ({"serve": "no_status"}, NoRssSample, "NoRssSample"),
}


def problem_rig(tmp_path: Path, setup: dict[str, Any]) -> Rig:
    gpu = {UNIT: setup["gpu"]} if "gpu" in setup else {}
    rig = make_rig(tmp_path, threads=FakeThreads(setup.get("threads", ())), gpu=gpu)
    rig.hooks.before_timed_run(TIMED)
    serve = setup.get("serve", "one")
    if serve == "one":
        rig.serve()
    elif serve == "two":
        rig.serve()
        rig.serve(pid=SERVER_PID + 1)
    elif serve == "no_status":
        rig.serve(status=FileNotFoundError("status"))
    rig.threads.drive()
    rig.host.clock.now += 3.0
    return rig


@pytest.mark.parametrize("name", list(PROBLEMS))
def test_each_problem_is_written_then_raised(tmp_path: Path, name: str) -> None:
    setup, problem, text = PROBLEMS[name]
    rig = problem_rig(tmp_path, setup)
    with pytest.raises(problem):
        rig.hooks.after_timed_run(TIMED)
    record = rig.host_json()
    assert record["problem"] == text
    assert rig.proc_csv().startswith(b"elapsed_ms,rss_anon_kib\n")
    assert rig.hooks._live is None
    sampler = rig.popen.of("gpu")[0]
    if name == "stop_failed":
        assert record["sampler_stopped_how"] == "stop_failed"
        assert sampler.signals == ["terminate", "kill"]
        assert rig.popen.running() == [sampler]
    else:
        assert rig.popen.running() == []
    if name == "died":
        assert record["sampler_stopped_how"] == "already_exited"
        assert sampler.signals == []


def test_the_first_of_two_problems_is_raised(tmp_path: Path) -> None:
    rig = problem_rig(tmp_path, {"gpu": GpuScript(exit_early=True), "serve": "none"})
    with pytest.raises(SamplerDied):
        rig.hooks.after_timed_run(TIMED)
    assert rig.host_json()["problem"] == "SamplerDied"
    assert rig.host_json()["server_pid_found"] is False


def test_the_swap_delta_is_recorded(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.host.files.table["/proc/vmstat"] = [vmstat(1204), vmstat(1300)]
    rig.clean_attempt()
    rig.hooks.after_timed_run(TIMED)
    record = rig.host_json()
    assert (record["pswpout_before"], record["pswpout_after"]) == (1204, 1300)
    loaded = load_evidence(str(rig.run_dir), TIMED, FakeFiles())
    assert loaded is not None
    assert loaded.swapped


@pytest.mark.parametrize("after", [OSError("synthetic"), b"pswpin 0\n"], ids=["unreadable", "no_pswpout"])
def test_a_bad_vmstat_after_the_attempt_writes_proc_csv_only(tmp_path: Path, after: Any) -> None:
    rig = make_rig(tmp_path)
    rig.host.files.table["/proc/vmstat"] = [vmstat(), after]
    rig.clean_attempt()
    with pytest.raises(MachineFault) as caught:
        rig.hooks.after_timed_run(TIMED)
    assert type(caught.value) is MachineFault
    assert rig.proc_csv() == b"elapsed_ms,rss_anon_kib\n0,269428\n"
    rig.assert_left_clean()
    assert load_evidence(str(rig.run_dir), TIMED, FakeFiles()) is None


def test_the_after_hook_runs_while_the_server_is_still_listed(tmp_path: Path) -> None:
    """A server still in /proc at the after-hook, as after a server stop failure, leaves its step returning True; the
    hook still joins it. The hook cannot see the stop failure itself: the schedule row below covers that."""
    rig = make_rig(tmp_path)
    rig.clean_attempt()
    rig.threads.drive()
    assert rig.threads.current is not None
    rig.hooks.after_timed_run(TIMED)
    assert rig.threads.timeouts == [THREAD_JOIN_SEC]
    assert rig.threads.current is None
    assert isinstance(rig.host.files.table[f"/proc/{SERVER_PID}/cmdline"], bytes)
    assert rig.proc_csv() == b"elapsed_ms,rss_anon_kib\n0,269428\n3000,269428\n"
    assert rig.host_json()["problem"] is None
    assert rig.popen.running() == []


def test_an_after_hook_with_no_attempt_or_another_key_is_a_harness_bug(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    with pytest.raises(RuntimeError):
        rig.hooks.after_timed_run(TIMED)
    rig.clean_attempt()
    with pytest.raises(RuntimeError):
        rig.hooks.after_timed_run(RunKey(Q4B, "timed_run", 2, 2))
    assert rig.hooks._live is not None
    rig.hooks.after_timed_run(TIMED)


def test_server_rate_goes_through_the_marked_helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []

    def helper(samples: tuple[GpuSample, ...], elapsed_ms: int) -> None:
        calls.append(elapsed_ms)

    monkeypatch.setattr(machine, "_check_server_rate", helper)
    rig = problem_rig(tmp_path, {"gpu": GpuScript(rate_hz=8)})
    rig.hooks.after_timed_run(TIMED)
    assert calls == [3000]
    source = inspect.getsource(machine)
    marker = "# NEEDS-OWNER(AMB-108): "
    assert source.count(marker) == 1
    assert source.split(marker, 1)[1].split("\n", 2)[1].startswith("def _check_server_rate(")


# ---------------------------------------------------------------- contention


def contended_rig(tmp_path: Path, *scripts: ContentionScript, **options: Any) -> Rig:
    rig = make_rig(tmp_path, contention=scripts, **options)
    rig.clean_attempt(CONTENDED, seconds=0.0)
    return rig


def test_the_contention_command_discards_its_output(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    before = rig.files()
    rig.hooks.start_contention(CONTENDED)
    args, kwargs = rig.popen.calls[-1]
    assert args == list(MACHINE_CONFIG.contention_argv)
    assert kwargs["stdout"] is kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["env"] == dict(MACHINE_CONFIG.contention_env)
    assert rig.files() == before
    assert rig.host.clock.sleeps[-1] == 5.0
    rig.hooks.stop_contention(CONTENDED)
    rig.hooks.after_timed_run(CONTENDED)
    assert rig.popen.running() == []


def test_a_non_zero_exit_in_the_lead_is_a_contention_failure(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path, ContentionScript(lead_exit=3))
    with pytest.raises(ContentionFailed):
        rig.hooks.start_contention(CONTENDED)
    assert rig.popen.of("contention")[0].signals == []
    live = rig.hooks._live
    assert live is not None
    assert (live.contention, live.contention_record) == (None, None)
    rig.hooks.after_timed_run(CONTENDED)
    assert rig.host_json(CONTENDED)["contention"] is None
    assert rig.popen.running() == []


def contention_record_after(rig: Rig, seconds: float) -> dict[str, Any]:
    rig.host.clock.now += seconds
    rig.hooks.stop_contention(CONTENDED)
    rig.hooks.after_timed_run(CONTENDED)
    assert rig.popen.running() == []
    return dict(rig.host_json(CONTENDED)["contention"])


def test_an_exit_0_in_the_lead_ended_early_from_the_lead_end(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path, ContentionScript(lead_exit=0))
    rig.hooks.start_contention(CONTENDED)
    record = contention_record_after(rig, 2.0)
    assert (record["exit_code"], record["ended_before_stop"], record["ended_before_stop_ms"]) == (0, True, 2000)
    assert record["stopped_how"] is None
    assert rig.popen.of("contention")[0].signals == []


def test_a_running_command_is_terminated_at_the_stop(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    rig.hooks.start_contention(CONTENDED)
    record = contention_record_after(rig, 2.0)
    assert record == {
        "command_sha256": canonical_sha256(list(MACHINE_CONFIG.contention_argv)),
        "lead_sec": 5.0,
        "exit_code": 0,
        "ended_before_stop": False,
        "ended_before_stop_ms": None,
        "stopped_how": "terminated",
    }


def test_an_exit_seen_by_the_step_gives_its_ms_before_the_stop(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path, ContentionScript(exit_after_sec=6.0))
    rig.hooks.start_contention(CONTENDED)
    rig.host.clock.now += 1.0
    rig.threads.drive()
    record = contention_record_after(rig, 2.0)
    assert (record["ended_before_stop"], record["ended_before_stop_ms"], record["exit_code"]) == (True, 2000, 0)


def test_an_exit_never_seen_has_no_ms(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path, ContentionScript(exit_after_sec=6.0))
    rig.hooks.start_contention(CONTENDED)
    record = contention_record_after(rig, 3.0)
    assert (record["ended_before_stop"], record["ended_before_stop_ms"], record["stopped_how"]) == (True, None, None)


def test_an_exit_stamped_after_the_stop_began_has_no_ms(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    rig.hooks.start_contention(CONTENDED)
    process = rig.popen.of("contention")[0]
    step = rig.threads.current
    assert step is not None
    original = process.poll
    inside: list[bool] = []

    def poll() -> int | None:
        if not inside:
            inside.append(True)
            process.exit_code = 0
            rig.host.clock.now += 1.0
            step()
        return original()

    rig.host.clock.now += 2.0
    process.poll = poll  # type: ignore[method-assign]
    record = contention_record_after(rig, 0.0)
    assert (record["ended_before_stop"], record["ended_before_stop_ms"]) == (True, None)


def test_a_command_that_will_not_stop_is_a_contention_failure(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path, ContentionScript(stop_waits=(None, None)))
    rig.hooks.start_contention(CONTENDED)
    with pytest.raises(ContentionFailed):
        rig.hooks.stop_contention(CONTENDED)
    process = rig.popen.of("contention")[0]
    assert process.signals == ["terminate", "kill"]
    rig.hooks.after_timed_run(CONTENDED)
    assert process.signals == ["terminate", "kill"]
    assert rig.host_json(CONTENDED)["contention"] == {
        "command_sha256": canonical_sha256(list(MACHINE_CONFIG.contention_argv)),
        "lead_sec": 5.0,
        "exit_code": None,
        "ended_before_stop": False,
        "ended_before_stop_ms": None,
        "stopped_how": None,
    }
    assert rig.popen.running() == [process]


def test_a_contention_spawn_error_is_a_contention_failure(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path, spawn_errors={"contention": OSError("synthetic")})
    with pytest.raises(ContentionFailed):
        rig.hooks.start_contention(CONTENDED)
    rig.hooks.after_timed_run(CONTENDED)
    assert rig.popen.running() == []


def test_only_the_argv_hash_is_written(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    rig.hooks.start_contention(CONTENDED)
    record = contention_record_after(rig, 1.0)
    assert record["command_sha256"] == canonical_sha256(list(MACHINE_CONFIG.contention_argv))
    for path in rig.run_dir.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            for part in MACHINE_CONFIG.contention_argv:
                assert part.encode("ascii") not in raw, (path.name, part)


@pytest.mark.parametrize("hook", ["start_contention", "stop_contention"])
def test_a_contention_hook_outside_its_contended_attempt_is_a_harness_bug(tmp_path: Path, hook: str) -> None:
    rig = make_rig(tmp_path)
    with pytest.raises(RuntimeError):
        getattr(rig.hooks, hook)(CONTENDED)
    rig.clean_attempt()
    with pytest.raises(RuntimeError):
        getattr(rig.hooks, hook)(TIMED)
    with pytest.raises(RuntimeError):
        getattr(rig.hooks, hook)(CONTENDED)
    rig.hooks.after_timed_run(TIMED)
    assert rig.popen.of("contention") == []


def test_a_second_start_or_a_stop_without_start_is_a_harness_bug(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    with pytest.raises(RuntimeError):
        rig.hooks.stop_contention(CONTENDED)
    rig.hooks.start_contention(CONTENDED)
    with pytest.raises(RuntimeError):
        rig.hooks.start_contention(CONTENDED)
    assert len(rig.popen.of("contention")) == 1
    contention_record_after(rig, 1.0)


def test_the_after_hook_stops_a_command_left_running(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    rig.hooks.start_contention(CONTENDED)
    rig.hooks.after_timed_run(CONTENDED)
    assert rig.popen.of("contention")[0].signals == ["terminate"]
    assert rig.host_json(CONTENDED)["contention"] is None
    assert rig.popen.running() == []


def test_an_interrupt_in_the_lead_stops_the_command(tmp_path: Path) -> None:
    clock: list[Any] = []
    rig = make_rig(tmp_path, wrap=with_launch(sleep=interrupt_on(MACHINE_CONFIG.contention_lead_sec, clock)))
    clock.append(rig.host.clock)
    rig.clean_attempt(CONTENDED, seconds=0.0)
    with pytest.raises(KeyboardInterrupt):
        rig.hooks.start_contention(CONTENDED)
    assert rig.popen.of("contention")[0].signals == ["terminate"]
    live = rig.hooks._live
    assert live is not None
    assert live.contention is None
    rig.hooks.after_timed_run(CONTENDED)
    assert rig.popen.running() == []


@pytest.mark.parametrize("where", ["thread_stop", "sampler_stop"])
def test_an_interrupt_in_the_after_hook_leaves_nothing_running(tmp_path: Path, where: str) -> None:
    rig = contended_rig(tmp_path)
    rig.hooks.start_contention(CONTENDED)
    sampler = rig.popen.of("gpu")[0]
    if where == "thread_stop":

        def stop(_timeout: float) -> str | None:
            raise KeyboardInterrupt

        rig.threads.stop = stop  # type: ignore[method-assign]
    else:
        # The stop's wait is where an interrupt lands while the sampler is being stopped.
        original = sampler.wait
        waits: list[float | None] = []

        def wait(timeout: float | None = None) -> int:
            waits.append(timeout)
            if len(waits) == 1:
                raise KeyboardInterrupt
            return original(timeout)

        sampler.wait = wait  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        rig.hooks.after_timed_run(CONTENDED)
    assert "terminate" in sampler.signals
    assert rig.popen.of("contention")[0].signals == ["terminate"]
    assert rig.hooks._live is None
    assert rig.popen.running() == []


def test_an_interrupt_in_the_contention_backstop_still_stops_the_sampler(tmp_path: Path) -> None:
    rig = contended_rig(tmp_path)
    rig.hooks.start_contention(CONTENDED)
    sampler = rig.popen.of("gpu")[0]
    command = rig.popen.of("contention")[0]

    def stop(_timeout: float) -> str | None:
        raise KeyboardInterrupt

    def wait(timeout: float | None = None) -> int:
        raise KeyboardInterrupt

    rig.threads.stop = stop  # type: ignore[method-assign]
    command.wait = wait  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        rig.hooks.after_timed_run(CONTENDED)
    assert command.signals == ["terminate"]
    assert sampler.signals == ["terminate"]
    assert rig.popen.running() == [command]
    assert rig.hooks._live is None


def test_the_real_thread_runs_the_step_beside_the_contention_hooks(tmp_path: Path) -> None:
    read = threading.Event()

    def wrap(tools: MachineTools) -> MachineTools:
        launch = tools.launch

        def read_bytes(path: str) -> bytes:
            raw = launch.read_bytes(path)
            if path.endswith("/status"):
                read.set()
            return raw

        return MachineTools(
            dataclasses.replace(launch, read_bytes=read_bytes), tools.list_pids, start_thread=machine._start_thread
        )

    rig = make_rig(tmp_path, contention=(ContentionScript(exit_after_sec=6.0),), wrap=wrap)
    rig.serve(CONTENDED)
    try:
        rig.hooks.before_timed_run(CONTENDED)
        assert read.wait(THREAD_JOIN_SEC)
        rig.hooks.start_contention(CONTENDED)
        rig.host.clock.now += 2.0
        rig.hooks.stop_contention(CONTENDED)
        rig.hooks.after_timed_run(CONTENDED)
    finally:
        live = rig.hooks._live
        if live is not None and live.stopper is not None:
            live.stopper(THREAD_JOIN_SEC)
    record = rig.host_json(CONTENDED)
    assert record["problem"] is None
    ended_ms = record["contention"]["ended_before_stop_ms"]
    assert ended_ms is None or ended_ms >= 0
    assert rig.popen.running() == []


# ---------------------------------------------------------------- a whole schedule with the real hooks


@dataclass
class MachineSchedule:
    rig: ScheduleRig
    popen: FakeMachinePopen
    threads: FakeThreads

    @property
    def run_dir(self) -> Path:
        return Path(self.rig.run_dir)

    def logs(self) -> set[str]:
        return {path.name for path in (self.run_dir / "logs").iterdir()}


def machine_schedule(
    root: Path,
    scripts: Mapping[str, ServerScript] | None = None,
    *,
    request_sec: float = 0.05,
    **popen_options: Any,
) -> MachineSchedule:
    made: dict[str, Any] = {}

    def factory(fake_host: FakeHost) -> MachineHooks:
        threads = FakeThreads()
        install_machine(fake_host, threads, request_sec=request_sec)
        popen = FakeMachinePopen(fake_host.clock, **popen_options)
        made.update(popen=popen, threads=threads)
        tools = machine_tools(fake_host, popen, threads)
        return MachineHooks(MACHINE_CONFIG, f"{root}/run_1", CONDITIONS, SCHEDULE_CANDIDATES, tools, no_fsync)

    inputs = small_inputs(frozen_inputs(root))
    rig = schedule_rig(inputs, root, scripts, hooks=factory, proc_status=proc_status(), operator=VALID_OPERATOR)
    return MachineSchedule(rig, made["popen"], made["threads"])


HOOKED_PHASES = ("timed_run", "contended")


def test_a_clean_schedule_writes_the_four_logs_of_every_hooked_attempt(tmp_path: Path) -> None:
    schedule = machine_schedule(tmp_path)
    result = schedule.rig.run()
    assert result.voided == ()
    view = schedule.rig.view()
    assert view.faults == ()
    logs = schedule.logs()
    hooked = [run.key for run in view.runs if run.key.phase in HOOKED_PHASES]
    assert len(hooked) == 12
    for run in view.runs:
        names = {Path(machine_log_name(run.key, kind)).name for kind in ("apps", "gpu", "proc", "host")}
        assert (names <= logs) is (run.key.phase in HOOKED_PHASES), run.key
    for record in view.pre_run:
        unit = _unit_name(RunKey(record.candidate, "pre_run", None, 1))
        assert not any(name.startswith(f"{unit}.") and not name.endswith(".log") for name in logs)
    for key in hooked:
        loaded = load_evidence(str(schedule.run_dir), key, FakeFiles())
        assert loaded is not None, key
        assert loaded.host.problem is None
        assert loaded.rise_mib == 4308
        assert loaded.peak_rss_anon_bytes == 269_428 * 1024
        assert (loaded.host.contention is not None) is (key.phase == "contended")
    assert schedule.popen.running() == []


def test_another_cuda_process_voids_the_attempt_before_its_launch(tmp_path: Path) -> None:
    schedule = machine_schedule(tmp_path, apps={"qwen3-4b.run2.a1": apps_output(9001)})
    schedule.rig.run()
    view = schedule.rig.view()
    first = RunKey(Q4B, "timed_run", 2, 1)
    assert [(fault.key, fault.detail) for fault in view.faults] == [(first, "before_timed_run: OtherCudaProcess")]
    assert [void.key for void in view.voids] == [first]
    assert "qwen3-4b.run2.a1" not in schedule.rig.host.launches
    assert "qwen3-4b.run2.a2" in schedule.rig.host.launches
    assert load_evidence(str(schedule.run_dir), first, FakeFiles()) is None
    assert schedule.popen.running() == []


def test_a_slow_sampler_voids_its_attempt(tmp_path: Path) -> None:
    schedule = machine_schedule(tmp_path, request_sec=0.25, gpu={"qwen3-8b.run1.a1": GpuScript(rate_hz=8)})
    schedule.rig.run()
    view = schedule.rig.view()
    first = RunKey(Q8B, "timed_run", 1, 1)
    assert [(fault.key, fault.detail) for fault in view.faults] == [(first, "after_timed_run: SampleRateTooLow")]
    assert [void.key for void in view.voids] == [first]
    loaded = load_evidence(str(schedule.run_dir), first, FakeFiles())
    assert loaded is not None
    assert loaded.host.problem == "SampleRateTooLow"
    assert loaded.host.elapsed_ms >= 2000
    assert loaded.rate.min_per_whole_second == 8


def test_a_contention_command_failing_its_lead_leaves_the_unit_absent(tmp_path: Path) -> None:
    schedule = machine_schedule(tmp_path, contention=[ContentionScript(lead_exit=1)] * 3)
    schedule.rig.run()
    view = schedule.rig.view()
    failed = [fault for fault in view.faults if fault.key.candidate == Q4B]
    assert [fault.detail for fault in failed] == ["start_contention: ContentionFailed"] * 3
    assert all(fault.key.phase == "contended" for fault in failed)
    assert not any(run.status == "complete" for run in view.runs if run.key == RunKey(Q4B, "contended", None, 3))
    assert not any(
        run.status == "complete" and run.key.candidate == Q4B and run.key.phase == "contended" for run in view.runs
    )
    assert schedule.popen.running() == []


def test_a_server_that_will_not_stop_still_gets_its_host_record(tmp_path: Path) -> None:
    unit = "qwen3-4b.run1.a1"
    schedule = machine_schedule(tmp_path, {unit: ServerScript(waits=(None, None))})
    with pytest.raises(ServerStopFailed):
        schedule.rig.run()
    key = RunKey(Q4B, "timed_run", 1, 1)
    assert f"{unit}.host.json" in schedule.logs()
    record = load_evidence(str(schedule.run_dir), key, FakeFiles())
    assert record is not None
    assert record.host.sampler_stopped_how == "terminated"
    assert schedule.threads.current is None
    assert schedule.threads.started >= 1
    assert schedule.threads.timeouts == [THREAD_JOIN_SEC] * schedule.threads.started
    assert record.proc != ()
    assert record.peak_rss_anon_bytes == 269_428 * 1024
    assert record.host.problem is None
    assert record.host.server_pid_found is True
    view = schedule.rig.view()
    assert [(fault.key, fault.kind) for fault in view.faults] == [(key, "server_not_stopped")]
    assert [void.key for void in view.voids] == [key]
    assert schedule.popen.running() == []


def test_a_machine_spawn_leaves_the_host_server_alone(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    server, events = rig.host._server, list(rig.host.events)
    rig.hooks.before_timed_run(TIMED)
    assert (rig.host._server, rig.host.events, rig.host.launches) == (server, events, {})
    rig.serve()
    rig.threads.drive()
    rig.hooks.after_timed_run(TIMED)
    assert (rig.host._server, rig.host.events, rig.host.launches) == (server, events, {})
