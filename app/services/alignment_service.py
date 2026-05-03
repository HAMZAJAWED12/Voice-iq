# app/services/alignment_service.py
from __future__ import annotations

import math
from typing import Any

from app.utils.logger import logger


class AlignmentService:
    """
    Speaker-text alignment service.

    Responsibilities:
    - Normalize Whisper ASR output into timestamped segments
    - Approximate word timings (word slicing)
    - Assign words to diarization time windows
    - Merge adjacent blocks for cleaner turns
    - Compute confidence using Whisper segment metadata
    - Overlapping speech policy: mark overlap or assign dominant speaker
    - Build higher-level conversation turns with inferred roles (CUSTOMER/AGENT)
    """

    def __init__(
        self,
        max_gap_merge: float = 0.75,
        overlap_policy: str = "mark_overlap",  # "mark_overlap" | "dominant"
        unknown_label: str = "SPEAKER_UNKNOWN",
        overlap_min_sec: float = 0.15,
    ):
        self.max_gap_merge = float(max_gap_merge)
        self.overlap_policy = overlap_policy
        self.unknown_label = unknown_label
        self.overlap_min_sec = float(overlap_min_sec)

        if self.overlap_policy not in ("mark_overlap", "dominant"):
            raise ValueError("overlap_policy must be 'mark_overlap' or 'dominant'")

    # ------------------------------------------------------------
    # Confidence & overlap helpers
    # ------------------------------------------------------------
    @staticmethod
    def _confidence_from_whisper(seg: dict) -> float:
        """
        Convert Whisper segment metadata to a clean [0,1] confidence score.
        Uses:
          avg_logprob → typical ~ [-1,0]
          no_speech_prob → penalty
        """
        avg_logprob = seg.get("avg_logprob", -1.0)
        no_speech_prob = seg.get("no_speech_prob", 0.0)

        conf = 1.0 / (1.0 + math.exp(-avg_logprob))
        conf *= 1.0 - no_speech_prob
        return max(0.0, min(1.0, float(conf)))

    @staticmethod
    def _overlap(a_start, a_end, b_start, b_end) -> float:
        return max(0.0, min(a_end, b_end) - max(a_start, b_start))

    @classmethod
    def _best_asr_for_window(cls, window: dict, asr: list[dict]) -> dict | None:
        """Find which ASR segment overlaps the window the most."""
        best = None
        best_overlap = 0.0

        s_start = float(window["start"])
        s_end = float(window["end"])

        for a in asr:
            a_start, a_end = float(a["start"]), float(a["end"])
            ov = cls._overlap(s_start, s_end, a_start, a_end)
            if ov > best_overlap:
                best_overlap = ov
                best = a

        return best

    @staticmethod
    def _find_diarization_overlap_windows(diarization: list[dict], min_overlap: float) -> list[dict]:
        """
        Detect overlap regions between diarization segments.
        Returns list of windows: [{"start":..., "end":...}, ...]
        """
        if not diarization:
            return []

        diar = sorted(diarization, key=lambda d: (float(d["start"]), float(d["end"])))
        overlap_windows: list[dict] = []

        for i in range(len(diar)):
            for j in range(i + 1, len(diar)):
                a = diar[i]
                b = diar[j]

                a_start, a_end = float(a["start"]), float(a["end"])
                b_start, b_end = float(b["start"]), float(b["end"])

                # early break: because diar is sorted by start
                if b_start >= a_end:
                    break

                ov = max(0.0, min(a_end, b_end) - max(a_start, b_start))
                if ov >= min_overlap:
                    overlap_windows.append(
                        {
                            "start": float(max(a_start, b_start)),
                            "end": float(min(a_end, b_end)),
                        }
                    )

        # merge overlap windows that touch/overlap each other
        if not overlap_windows:
            return []

        overlap_windows = sorted(overlap_windows, key=lambda w: w["start"])
        merged = [overlap_windows[0]]
        for w in overlap_windows[1:]:
            last = merged[-1]
            if w["start"] <= last["end"]:
                last["end"] = max(last["end"], w["end"])
            else:
                merged.append(w)

        return merged

    # ------------------------------------------------------------
    # 1) Extract ASR segments
    # ------------------------------------------------------------
    @staticmethod
    def _extract_asr_segments(asr_result: Any) -> list[dict]:
        """
        Normalise ASR output into:
        [
          { start, end, text, avg_logprob, no_speech_prob }
        ]
        Accepts:
          - Whisper raw dict: {"segments": [...]}
          - Service dict: {"meta": {"segments": [...]} }
          - Service dict: {"segments": [...] }
          - List[segments]
        """
        segs = None

        if isinstance(asr_result, dict):
            if isinstance(asr_result.get("segments"), list):
                segs = asr_result["segments"]
            elif isinstance(asr_result.get("meta"), dict) and isinstance(asr_result["meta"].get("segments"), list):
                segs = asr_result["meta"]["segments"]
        elif isinstance(asr_result, list):
            segs = asr_result

        if not segs:
            return []

        norm: list[dict] = []
        for s in segs:
            if "start" not in s or "end" not in s:
                continue
            norm.append(
                {
                    "start": float(s["start"]),
                    "end": float(s["end"]),
                    "text": (s.get("text") or "").strip(),
                    "avg_logprob": s.get("avg_logprob", -1.0),
                    "no_speech_prob": s.get("no_speech_prob", 0.0),
                }
            )

        return norm

    # ------------------------------------------------------------
    # 2) Word slicing
    # ------------------------------------------------------------
    @staticmethod
    def _to_word_segments(asr_segments: list[dict]) -> list[dict]:
        """Approximate word timings by uniformly slicing segment duration."""
        words_all: list[dict] = []

        for seg in asr_segments:
            text = seg.get("text", "")
            if not text:
                continue

            start = float(seg["start"])
            end = float(seg["end"])
            duration = max(end - start, 1e-6)

            words = text.split()
            if not words:
                continue

            w_dur = duration / len(words)

            for i, w in enumerate(words):
                w_start = start + i * w_dur
                w_end = end if i == len(words) - 1 else start + (i + 1) * w_dur
                words_all.append({"start": w_start, "end": w_end, "word": w})

        return words_all

    # ------------------------------------------------------------
    # 3) Map words → diarization windows
    # ------------------------------------------------------------
    @staticmethod
    def _align_words_to_diarization(word_segments: list[dict], diarization: list[dict]) -> list[dict]:
        aligned: list[dict] = []
        if not word_segments or not diarization:
            return aligned

        word_segments = sorted(word_segments, key=lambda x: x["start"])
        diarization = sorted(diarization, key=lambda x: x["start"])

        for d in diarization:
            d_start, d_end = float(d["start"]), float(d["end"])
            speaker = d.get("speaker", "UNKNOWN")

            words = [w for w in word_segments if not (w["end"] <= d_start or w["start"] >= d_end)]
            if not words:
                continue

            text = " ".join(w["word"] for w in words).strip()

            aligned.append(
                {
                    "start": round(d_start, 3),
                    "end": round(d_end, 3),
                    "speaker": speaker,
                    "text": text,
                    # propagate diarization confidence if present
                    "diarization_confidence": float(d.get("diarization_confidence", d.get("confidence", 1.0))),
                }
            )

        return aligned

    # ------------------------------------------------------------
    # 4) Merge blocks
    # ------------------------------------------------------------
    def _merge_blocks(self, segments: list[dict]) -> list[dict]:
        if not segments:
            return []

        segs = sorted(segments, key=lambda x: x["start"])
        merged = [segs[0]]

        for seg in segs[1:]:
            last = merged[-1]
            gap = float(seg["start"]) - float(last["end"])

            if seg["speaker"] == last["speaker"] and 0 <= gap <= self.max_gap_merge:
                last["end"] = float(seg["end"])
                last["text"] = (last["text"] + " " + seg["text"]).strip()
                last["diarization_confidence"] = float(
                    (last.get("diarization_confidence", 1.0) + seg.get("diarization_confidence", 1.0)) / 2.0
                )
            else:
                merged.append(seg)

        return merged

    # ------------------------------------------------------------
    # Overlap policy enforcement
    # ------------------------------------------------------------
    def _apply_overlap_policy(self, merged: list[dict], diarization: list[dict]) -> list[dict]:
        """
        If diarization has overlapping segments, enforce policy:
          - dominant: keep speaker as-is, but mark overlap=True and reduce confidences
          - mark_overlap: set speaker=SPEAKER_UNKNOWN, overlap=True and reduce confidences
        """
        if not merged or not diarization:
            return merged

        overlap_windows = self._find_diarization_overlap_windows(diarization, min_overlap=self.overlap_min_sec)
        if not overlap_windows:
            # ensure fields exist
            for s in merged:
                s.setdefault("overlap", False)
            return merged

        for seg in merged:
            seg.setdefault("overlap", False)

            s_start = float(seg["start"])
            s_end = float(seg["end"])

            # if intersects any overlap window, mark
            for ow in overlap_windows:
                if self._overlap(s_start, s_end, float(ow["start"]), float(ow["end"])) > 0:
                    seg["overlap"] = True

                    # degrade confidence because attribution is uncertain
                    seg["confidence"] = float(min(seg.get("confidence", 1.0), 0.55))
                    seg["diarization_confidence"] = float(min(seg.get("diarization_confidence", 1.0), 0.55))

                    if self.overlap_policy == "mark_overlap":
                        seg["speaker"] = self.unknown_label

                    break

        return merged

    # ------------------------------------------------------------
    # Public API: align()
    # ------------------------------------------------------------
    def align(self, asr_result: Any, diarization_result: list[dict]) -> dict[str, list[dict]]:
        diar = diarization_result or []
        asr = self._extract_asr_segments(asr_result)

        if not asr or not diar:
            logger.warning("Alignment failed: missing ASR or diarization segments.")
            return {"speaker_segments": []}

        words = self._to_word_segments(asr)
        raw = self._align_words_to_diarization(words, diar)
        merged = self._merge_blocks(raw)

        # Attach ASR confidence + placeholders
        for seg in merged:
            best = self._best_asr_for_window({"start": seg["start"], "end": seg["end"]}, asr)
            seg["confidence"] = self._confidence_from_whisper(best) if best else 0.5
            seg.setdefault("diarization_confidence", 1.0)
            seg.setdefault("overlap", False)

            seg["gender"] = None
            seg["gender_confidence"] = None

        # Apply overlap marking rule
        merged = self._apply_overlap_policy(merged, diar)

        logger.info(
            f"AlignmentService: produced {len(merged)} speaker segments "
            f"from {len(asr)} ASR segments and {len(diar)} diarization segments."
        )

        return {"speaker_segments": merged}

    # ------------------------------------------------------------
    # Higher-level: build conversation turns
    # ------------------------------------------------------------
    def build_conversation(self, asr_result: Any, diarization_result: list[dict]) -> list[dict]:
        aligned = self.align(asr_result, diarization_result)
        segments = aligned.get("speaker_segments", [])
        if not segments:
            return []

        segments = sorted(segments, key=lambda s: s["start"])

        # Speaking time per speaker (UNKNOWN can exist)
        time_map: dict[str, float] = {}
        for seg in segments:
            dur = float(seg["end"]) - float(seg["start"])
            spk = seg.get("speaker", "UNKNOWN")
            time_map[spk] = time_map.get(spk, 0.0) + max(dur, 0.0)

        speaker_order = sorted(time_map.items(), key=lambda x: x[1], reverse=True)

        # Assign roles (if only UNKNOWN exists, keep it)
        if len(speaker_order) >= 2:
            customer = speaker_order[0][0]
            agent = speaker_order[1][0]
            roles = {customer: "CUSTOMER", agent: "AGENT"}
        elif len(speaker_order) == 1:
            roles = {speaker_order[0][0]: "CUSTOMER"}
        else:
            roles = {}

        conv: list[dict] = []
        current = None

        for seg in segments:
            spk = seg.get("speaker", "UNKNOWN")
            role = roles.get(spk, spk)

            if (
                current
                and current["speaker_raw"] == spk
                and float(seg["start"]) - float(current["end"]) <= self.max_gap_merge
            ):
                current["end"] = seg["end"]
                current["text"] = (current["text"] + " " + (seg.get("text") or "")).strip()
                current["overlap"] = bool(current.get("overlap") or seg.get("overlap"))
            else:
                if current:
                    conv.append(current)
                current = {
                    "start": seg["start"],
                    "end": seg["end"],
                    "speaker": role,
                    "speaker_raw": spk,
                    "text": seg.get("text", ""),
                    "overlap": bool(seg.get("overlap", False)),
                }

        if current:
            conv.append(current)

        return conv


# -------------------------------------------------------------------
# Compatibility wrappers (keeps existing imports working)
# -------------------------------------------------------------------
def align_transcript_with_speakers(asr_result: Any, diarization_result: list[dict]):
    return AlignmentService().align(asr_result, diarization_result)


def build_conversation(asr_result: Any, diarization_result: list[dict]) -> list[dict]:
    return AlignmentService().build_conversation(asr_result, diarization_result)
