"""The machine evidence of a hooked attempt, as plain values (bake-off harness H4a).

This module only describes evidence that is already in memory. It reads no file, starts nothing and keeps no clock:
H4b's `machine` samples the GPU, the server's /proc status and the swap counter, writes them beside the server log and
loads them back into these types, and `gates` and `report` read only these types. So neither `gates` nor `report`
imports `machine`, and the evidence types stay the same whichever loader built them.

Implements, from the signed D6 criteria note S2:

- S2 §6, gate M: the model server's VRAM is "the rise in `memory.used` from server start through the longest
  cap-session request", the RAM is "the server's peak private (anonymous) memory", and "the WSL VM must not swap".
  `AttemptEvidence` gives the rise as the peak over the whole attempt minus its first sample, taken before the server
  spawns (AMB-107), the peak RssAnon in bytes, and whether the swap-out counter moved. The audio pipeline's VRAM is
  "sampled at least 10 times a second", which is `MIN_SAMPLES_PER_SEC`.
- S2 §3.13, runs: "Gate M uses the peak over all runs", and "Voided runs are listed in the report", so evidence is
  described per attempt, voided ones included, and `gates` decides which attempts count.
- S2 §9, report: the figures are recorded for every number, so `sample_rate` describes the samples exactly (count,
  harness time, nvidia-smi span, the fewest samples in a whole second, the longest gap) and never judges them. A
  check against the floor is the caller's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from .records import RunKey

# S2 §6, gate M: the audio pipeline's VRAM is "sampled at least 10 times a second". The harness plan, row H4, applies
# the same floor to the server sampler (AMB-108). `gates` reads it for the audio rate; H4b's `machine` imports it and
# never redefines it.
MIN_SAMPLES_PER_SEC = 10


@dataclass(frozen=True, slots=True)
class GpuSample:
    """One nvidia-smi line. `t_ms` is relative to the first line; the non-memory fields stay raw text."""

    t_ms: int
    used_mib: int
    free_mib: int
    clocks_gr: str
    clocks_mem: str
    temperature: str
    power: str


@dataclass(frozen=True, slots=True)
class ProcSample:
    """One reading of the server's `RssAnon`, in kB as /proc reports it."""

    elapsed_ms: int
    rss_anon_kib: int


@dataclass(frozen=True, slots=True)
class SampleRate:
    """What the GPU samples were, not whether they suffice. `elapsed_ms` is harness time; the rest is sample time."""

    samples: int
    elapsed_ms: int
    first_to_last_ms: int
    min_per_whole_second: int | None
    max_gap_ms: int | None


@dataclass(frozen=True, slots=True)
class ContentionRecord:
    """The together session's audio command: its argv's hash only, never the argv itself."""

    command_sha256: str
    lead_sec: float
    exit_code: int | None
    ended_before_stop: bool
    ended_before_stop_ms: int | None
    stopped_how: str | None


@dataclass(frozen=True, slots=True)
class HostRecord:
    """The host side of one hooked attempt: the compute-apps check, the swap counter and how the samplers ended."""

    key: RunKey
    apps_lines: int
    pswpout_before: int
    pswpout_after: int
    elapsed_ms: int
    sampler_stopped_how: str
    server_pid_found: bool
    problem: str | None
    contention: ContentionRecord | None


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    """Everything gate M reads about one hooked attempt. The first GPU sample is the baseline, so there is always one,
    and the host record must belong to the same attempt."""

    key: RunKey
    gpu: tuple[GpuSample, ...]
    proc: tuple[ProcSample, ...]
    host: HostRecord

    def __post_init__(self) -> None:
        if not self.gpu:
            raise ValueError("AttemptEvidence.gpu needs at least one sample: the first one is the baseline")
        if self.host.key != self.key:
            raise ValueError("AttemptEvidence.host belongs to another attempt")

    @property
    def baseline_used_mib(self) -> int:
        return self.gpu[0].used_mib

    @property
    def baseline_free_mib(self) -> int:
        return self.gpu[0].free_mib

    @property
    def peak_used_mib(self) -> int:
        return max(sample.used_mib for sample in self.gpu)

    @property
    def rise_mib(self) -> int:
        return self.peak_used_mib - self.baseline_used_mib

    @property
    def peak_rss_anon_bytes(self) -> int | None:
        """None only as a backstop (G4-19): an attempt with no RSS reading is not measured."""
        if not self.proc:
            return None
        return max(sample.rss_anon_kib for sample in self.proc) * 1024

    @property
    def swapped(self) -> bool:
        return self.host.pswpout_after > self.host.pswpout_before

    @property
    def rate(self) -> SampleRate:
        return sample_rate(self.gpu, self.host.elapsed_ms)


def sample_rate(samples: tuple[GpuSample, ...], elapsed_ms: int) -> SampleRate:
    """Describe the samples; never raise on a low rate.

    Whole seconds count from the first sample's `t_ms`, and the partial last second (up to and including the last
    sample) is ignored. `min_per_whole_second` is None when no whole second exists. With fewer than 2 samples there is
    no span and no gap: `first_to_last_ms` is 0 and `max_gap_ms` is None.
    """
    if len(samples) < 2:
        return SampleRate(len(samples), elapsed_ms, 0, None, None)
    first = samples[0].t_ms
    first_to_last_ms = samples[-1].t_ms - first
    whole_seconds = max(first_to_last_ms // 1000, 0)
    counts = [0] * whole_seconds
    for sample in samples:
        second = (sample.t_ms - first) // 1000
        if 0 <= second < whole_seconds:
            counts[second] += 1
    max_gap_ms = max(samples[index + 1].t_ms - samples[index].t_ms for index in range(len(samples) - 1))
    return SampleRate(len(samples), elapsed_ms, first_to_last_ms, min(counts) if counts else None, max_gap_ms)


# H4a: the signature of H4b's `machine.load_evidence(run_dir, key, read_bytes)`. None means the attempt left no host
# record. `entry` takes a loader of this type, so H4b swaps in the real one without touching scoring.
EvidenceLoader: TypeAlias = Callable[[str, RunKey, Callable[[str], bytes]], AttemptEvidence | None]
