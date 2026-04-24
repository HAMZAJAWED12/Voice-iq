# app/utils/audio_quality.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional

import numpy as np
import soundfile as sf

from app.utils.logger import logger


@dataclass
class AudioQualityReport:
    duration_sec: float
    sample_rate: int
    channels: int

    rms_db: float
    peak_db: float
    silence_ratio: float

    snr_db: Optional[float]  # heuristic, can be None if estimation fails

    is_silent: bool
    is_near_silent: bool
    low_snr: bool
    very_low_snr: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _db(x: float, eps: float = 1e-12) -> float:
    return float(20.0 * np.log10(max(x, eps)))


def analyze_audio_quality(
    wav_path: str,
    silence_rms_db: float = -60.0,
    near_silence_rms_db: float = -45.0,
    frame_ms: int = 30,
    speech_gate_db: float = -35.0,
) -> AudioQualityReport:
    """
    Lightweight audio quality analysis for guardrails:
      - duration, rms_db, peak_db
      - silence_ratio (fraction of frames below silence threshold)
      - heuristic snr_db (speech vs non-speech frame energy)

    Designed to be fast and dependency-light (numpy + soundfile only).
    """

    data, sr = sf.read(wav_path, always_2d=True)
    if data is None or len(data) == 0 or sr is None or sr <= 0:
        # fail-soft: treat as silent
        return AudioQualityReport(
            duration_sec=0.0,
            sample_rate=int(sr or 0),
            channels=int(data.shape[1]) if isinstance(data, np.ndarray) and data.ndim == 2 else 0,
            rms_db=-120.0,
            peak_db=-120.0,
            silence_ratio=1.0,
            snr_db=None,
            is_silent=True,
            is_near_silent=True,
            low_snr=True,
            very_low_snr=True,
        )

    channels = int(data.shape[1])
    mono = np.mean(data, axis=1).astype(np.float32)

    n = int(len(mono))
    duration = float(n / float(sr)) if sr > 0 else 0.0

    # RMS and peak
    rms = float(np.sqrt(np.mean(mono**2)) if n else 0.0)
    peak = float(np.max(np.abs(mono)) if n else 0.0)

    rms_db = _db(rms)
    peak_db = _db(peak)

    # Frame-based analysis
    frame_len = max(1, int(sr * frame_ms / 1000))
    num_frames = max(1, int(np.ceil(n / frame_len)))

    frame_rms = []
    for i in range(num_frames):
        start = i * frame_len
        end = min(n, start + frame_len)
        frame = mono[start:end]
        if len(frame) == 0:
            frame_rms.append(0.0)
        else:
            frame_rms.append(float(np.sqrt(np.mean(frame**2))))
    frame_rms = np.array(frame_rms, dtype=np.float32)

    frame_rms_db = np.array([_db(x) for x in frame_rms], dtype=np.float32)

    silence_frames = frame_rms_db < float(silence_rms_db)
    silence_ratio = float(np.mean(silence_frames)) if len(frame_rms_db) else 1.0

    # SNR heuristic:
    # speech frames = above speech_gate_db, noise frames = below that gate
    snr_db: Optional[float] = None
    try:
        speech_mask = frame_rms_db >= float(speech_gate_db)
        noise_mask = ~speech_mask

        speech_energy = float(np.mean(frame_rms[speech_mask] ** 2)) if np.any(speech_mask) else 0.0
        noise_energy = float(np.mean(frame_rms[noise_mask] ** 2)) if np.any(noise_mask) else 0.0

        if speech_energy > 0 and noise_energy > 0:
            snr_db = float(10.0 * np.log10(speech_energy / max(noise_energy, 1e-12)))
        else:
            snr_db = None
    except Exception as e:
        logger.warning(f"Audio quality SNR estimation failed: {e}")
        snr_db = None

    is_silent = bool(rms_db <= float(silence_rms_db))
    is_near_silent = bool((not is_silent) and (rms_db <= float(near_silence_rms_db)))

    low_snr = bool(snr_db is not None and snr_db < 10.0)
    very_low_snr = bool(snr_db is not None and snr_db < 5.0)

    return AudioQualityReport(
        duration_sec=float(round(duration, 3)),
        sample_rate=int(sr),
        channels=int(channels),
        rms_db=float(round(rms_db, 3)),
        peak_db=float(round(peak_db, 3)),
        silence_ratio=float(round(silence_ratio, 4)),
        snr_db=None if snr_db is None else float(round(snr_db, 3)),
        is_silent=is_silent,
        is_near_silent=is_near_silent,
        low_snr=low_snr,
        very_low_snr=very_low_snr,
    )