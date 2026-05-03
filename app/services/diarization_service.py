# app/services/diarization_service.py
from __future__ import annotations

import os
from collections import defaultdict

import soundfile as sf
import torch
from huggingface_hub import login

from app.utils.logger import logger

# Optional import: pyannote
try:
    from pyannote.audio import Pipeline

    _HAS_PYANNOTE = True
except ImportError:
    Pipeline = None
    _HAS_PYANNOTE = False

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Module-level cache (loads once per process)
_DIARIZATION_PIPELINE = None


class DiarizationService:
    """
    Speaker diarization service using pyannote.audio with safe fallback.

    New Guardrails:
    - Speaker-count bounds (cap) to prevent explosion in noisy audio
    - expected_speakers support (fixed speakers when known)
    - low_snr support (tighten smoothing + lower confidence)
    - diarization_confidence heuristic + SPEAKER_UNKNOWN for uncertain/minor/capped speakers
    - fail-soft warnings via diarize_with_warnings()
    """

    def __init__(
        self,
        model_id: str = "pyannote/speaker-diarization",
        clustering_threshold: float = 0.65,
        force_conversation_mode: bool = True,
        min_speakers: int = 2,
        max_speakers: int = 2,
        max_speakers_cap: int = 8,
        unknown_label: str = "SPEAKER_UNKNOWN",
    ):
        self.model_id = model_id
        self.clustering_threshold = clustering_threshold
        self.force_conversation_mode = force_conversation_mode
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers

        # New
        self.max_speakers_cap = int(max_speakers_cap)
        self.unknown_label = unknown_label

        self.pipeline = self._load_pipeline()

    # ------------------------------------------------------------
    # Load & Cache Pyannote Pipeline
    # ------------------------------------------------------------
    def _load_pipeline(self):
        global _DIARIZATION_PIPELINE

        if _DIARIZATION_PIPELINE is not None:
            return _DIARIZATION_PIPELINE

        if not _HAS_PYANNOTE:
            logger.warning("Pyannote not installed. Using mock diarization.")
            return None

        token = os.getenv("PYANNOTE_AUTH_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
        if not token:
            logger.warning("No PYANNOTE_AUTH_TOKEN or HUGGINGFACE_TOKEN found. Using mock diarization.")
            return None

        logger.info("Loading Pyannote speaker-diarization pipeline...")

        try:
            # Login to HF for gated models
            login(token=token)

            # --- PyTorch 2.6+ safe-loading allowlist (fixes OmegaConf errors) ---
            try:
                import torch.serialization
                from omegaconf.dictconfig import DictConfig
                from omegaconf.listconfig import ListConfig

                torch.serialization.add_safe_globals([ListConfig, DictConfig])
            except Exception:
                pass

            _DIARIZATION_PIPELINE = Pipeline.from_pretrained(self.model_id, use_auth_token=token).to(DEVICE)

            # Optional: adjust clustering threshold
            try:
                _DIARIZATION_PIPELINE.instantiate(
                    {"clustering": {"method": "centroid", "threshold": float(self.clustering_threshold)}}
                )
                logger.info("Adjusted clustering threshold for enhanced speaker separation.")
            except Exception as e:
                logger.warning(f"Could not adjust clustering threshold: {e}")

            logger.info(f"Pyannote diarization pipeline loaded successfully on {DEVICE}.")

        except Exception as e:
            logger.error(f"Failed to load Pyannote pipeline: {e}")
            _DIARIZATION_PIPELINE = None

        return _DIARIZATION_PIPELINE  # have add logger

    # ------------------------------------------------------------
    # Mock fallback diarization
    # ------------------------------------------------------------
    def _mock_diarization(self, wav_path: str) -> list[dict]:
        data, sr = sf.read(wav_path)
        duration = len(data) / sr if sr else 0.0
        logger.info(f"Mock diarization for {duration:.1f}s audio.")
        return [
            {
                "start": 0.0,
                "end": round(duration, 3),
                "speaker": "SPEAKER_00",
                "confidence": 1.0,
                "diarization_confidence": 1.0,
                "source": "mock",
            }
        ]

    # ------------------------------------------------------------
    # Segment smoothing helpers (anti flicker)
    # ------------------------------------------------------------
    def _smooth_segments(
        self,
        segments: list[dict],
        min_duration: float = 0.5,  # tighter default than before
        max_gap_merge: float = 0.3,
    ) -> list[dict]:
        """Merge tiny segments & same-speaker segments that are very close."""
        if not segments:
            return []

        segments = sorted(segments, key=lambda s: s["start"])
        smoothed: list[dict] = []

        for seg in segments:
            duration = float(seg["end"]) - float(seg["start"])
            if duration <= 0:
                continue

            if smoothed:
                last = smoothed[-1]
                gap = float(seg["start"]) - float(last["end"])

                # Merge if same speaker and gap small
                if seg["speaker"] == last["speaker"] and 0 <= gap <= max_gap_merge:
                    last["end"] = max(float(last["end"]), float(seg["end"]))
                    last["confidence"] = float((last.get("confidence", 1.0) + seg.get("confidence", 1.0)) / 2.0)
                    last["diarization_confidence"] = float(
                        (last.get("diarization_confidence", 1.0) + seg.get("diarization_confidence", 1.0)) / 2.0
                    )
                    continue

                # Merge very short segments into previous
                if duration < min_duration:
                    last["end"] = max(float(last["end"]), float(seg["end"]))
                    last["confidence"] = float((last.get("confidence", 1.0) + seg.get("confidence", 1.0)) / 2.0)
                    last["diarization_confidence"] = float(
                        (last.get("diarization_confidence", 1.0) + seg.get("diarization_confidence", 1.0)) / 2.0
                    )
                    continue

            smoothed.append(seg)

        return smoothed

    # ------------------------------------------------------------
    # Speaker cap + SPEAKER_UNKNOWN collapse
    # ------------------------------------------------------------
    def _cap_speakers(
        self,
        segments: list[dict],
        max_speakers: int,
        minor_share_threshold: float = 0.05,
    ) -> tuple[list[dict], bool]:
        """
        Cap number of speakers by total speaking time.
        Speakers outside the top-K are collapsed to SPEAKER_UNKNOWN.
        Also collapses very minor speakers (< share threshold).
        Returns (new_segments, cap_applied).
        """
        if not segments:
            return segments, False

        totals = defaultdict(float)
        total_time = 0.0
        for s in segments:
            dur = float(s["end"]) - float(s["start"])
            if dur > 0:
                totals[s["speaker"]] += dur
                total_time += dur

        speakers_sorted = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        cap_applied = len(speakers_sorted) > max_speakers

        keep = set([spk for spk, _ in speakers_sorted[:max_speakers]])

        new_segments: list[dict] = []
        for s in segments:
            spk = s.get("speaker", self.unknown_label)
            share = (totals.get(spk, 0.0) / max(total_time, 1e-9)) if total_time else 0.0

            if (spk not in keep) or (share < minor_share_threshold):
                s2 = dict(s)
                s2["speaker"] = self.unknown_label
                # degrade confidence because it's uncertain / minor / capped
                s2["diarization_confidence"] = float(min(s2.get("diarization_confidence", 1.0), 0.6))
                s2["confidence"] = float(min(s2.get("confidence", 1.0), 0.6))
                new_segments.append(s2)
            else:
                new_segments.append(s)

        return new_segments, cap_applied

    # ------------------------------------------------------------
    # Main API (new): returns segments + warnings
    # ------------------------------------------------------------
    def diarize_with_warnings(
        self,
        wav_path: str,
        expected_speakers: int | None = None,
        max_speakers_cap: int | None = None,
        low_snr: bool = False,
    ) -> tuple[list[dict], list[str]]:
        """
        Run diarization with guardrails.
        Returns: (segments, warnings)
        """
        warnings: list[str] = []
        cap = int(max_speakers_cap or self.max_speakers_cap)
        cap = max(1, min(cap, 12))

        logger.info(f"Running diarization for: {wav_path}")

        if self.pipeline is None:
            return self._mock_diarization(wav_path), warnings

        try:
            diarization = None

            # If expected speakers is given, force fixed K (bounded)
            if expected_speakers is not None:
                k = max(1, min(int(expected_speakers), cap))
                diarization = self.pipeline({"audio": wav_path}, min_speakers=k, max_speakers=k)

            # Else try conversation-mode (2 speakers) first if enabled
            elif self.force_conversation_mode:
                try:
                    diarization = self.pipeline(
                        {"audio": wav_path},
                        min_speakers=min(self.min_speakers, cap),
                        max_speakers=min(self.max_speakers, cap),
                    )
                except Exception as e:
                    logger.warning(f"Auto-speaker diarization fallback: {e}")
                    diarization = self.pipeline({"audio": wav_path}, min_speakers=1, max_speakers=cap)
            else:
                diarization = self.pipeline({"audio": wav_path}, min_speakers=1, max_speakers=cap)

            raw_segments: list[dict] = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                raw_segments.append(
                    {
                        "start": float(round(turn.start, 3)),
                        "end": float(round(turn.end, 3)),
                        "speaker": str(speaker),
                        "confidence": 1.0,
                        "diarization_confidence": 1.0,  # heuristic baseline
                        "source": "pyannote",
                    }
                )

            # Tighten smoothing if low SNR
            min_d = 0.5 * (1.25 if low_snr else 1.0)
            max_gap = 0.3 * (1.25 if low_snr else 1.0)
            smoothed = self._smooth_segments(raw_segments, min_duration=min_d, max_gap_merge=max_gap)

            # Cap speakers (and collapse minor speakers)
            minor_thr = 0.07 if low_snr else 0.05
            capped, cap_applied = self._cap_speakers(smoothed, max_speakers=cap, minor_share_threshold=minor_thr)
            if cap_applied:
                warnings.append("SPEAKER_CAP_APPLIED")

            # Degrade confidence if low SNR
            if low_snr:
                warnings.append("LOW_SNR_DIARIZATION_UNRELIABLE")
                for s in capped:
                    s["diarization_confidence"] = float(min(s.get("diarization_confidence", 1.0), 0.7))
                    s["confidence"] = float(min(s.get("confidence", 1.0), 0.7))

            logger.info(
                f"Raw segments: {len(raw_segments)}, Smoothed: {len(smoothed)}, Final: {len(capped)}, "
                f"Speakers detected: {len(set(s['speaker'] for s in capped))}"
            )

            return capped, warnings

        except Exception as e:
            logger.error(f"Diarization failed: {e}")
            warnings.append("DIARIZATION_FAILED_FALLBACK")
            return self._mock_diarization(wav_path), warnings

    # ------------------------------------------------------------
    # Backwards-compatible API (old): returns segments only
    # ------------------------------------------------------------
    def diarize(self, wav_path: str) -> list[dict]:
        """
        Backwards-compatible method:
        old code expects diarize(wav_path) -> List[Dict]
        """
        segs, _warnings = self.diarize_with_warnings(wav_path)
        return segs
