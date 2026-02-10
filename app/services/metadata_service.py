# app/services/metadata_service.py

from typing import List, Dict
from collections import defaultdict


class MetadataExtractor:

    @staticmethod
    def compute_speaker_stats(speaker_segments: List[Dict]) -> Dict:
        """
        Compute per-speaker analytics from aligned segments.
        Input: speaker_segments from EnhancedAligner
        """
        if not speaker_segments:
            return {}

        stats = defaultdict(lambda: {
            "total_speaking_time": 0.0,
            "segment_count": 0,
            "total_words": 0,
            "longest_monologue": 0.0,
            "first_spoke_at": None,
            "last_spoke_at": None,
        })

        for seg in speaker_segments:
            spk = seg["speaker"]
            duration = seg["end"] - seg["start"]
            text = seg.get("text") or ""
            words = text.split()
            word_count = len(words)

            stats[spk]["total_speaking_time"] += duration
            stats[spk]["segment_count"] += 1
            stats[spk]["total_words"] += word_count
            stats[spk]["longest_monologue"] = max(
                stats[spk]["longest_monologue"], duration
            )

            # first spoken
            if stats[spk]["first_spoke_at"] is None:
                stats[spk]["first_spoke_at"] = seg["start"]
            else:
                stats[spk]["first_spoke_at"] = min(
                    stats[spk]["first_spoke_at"], seg["start"]
                )

            # last spoken
            if stats[spk]["last_spoke_at"] is None:
                stats[spk]["last_spoke_at"] = seg["end"]
            else:
                stats[spk]["last_spoke_at"] = max(
                    stats[spk]["last_spoke_at"], seg["end"]
                )

        # derive extra ratios
        total_audio_talk_time = sum(s["total_speaking_time"] for s in stats.values())
        total_audio_words = sum(s["total_words"] for s in stats.values())

        for spk, s in stats.items():
            s["avg_segment_length"] = s["total_speaking_time"] / max(
                s["segment_count"], 1
            )
            s["wpm"] = (
                (s["total_words"] / s["total_speaking_time"] * 60)
                if s["total_speaking_time"] > 0
                else 0
            )
            s["speaking_ratio"] = s["total_speaking_time"] / max(
                total_audio_talk_time, 1.0
            )
            s["word_ratio"] = s["total_words"] / max(total_audio_words, 1.0)

        return dict(stats)

    @staticmethod
    def compute_conversation_stats(
        speaker_segments: List[Dict], diarization_segments: List[Dict]
    ) -> Dict:
        """
        Higher-level conversation analytics.
        """
        if not speaker_segments or not diarization_segments:
            return {}

        start_time = diarization_segments[0]["start"]
        end_time = diarization_segments[-1]["end"]
        total_duration = end_time - start_time

        total_words = sum(
            len((seg.get("text") or "").split()) for seg in speaker_segments
        )

        return {
            "total_duration": total_duration,
            "total_segments": len(speaker_segments),
            "total_words": total_words,
            "avg_turn_length": total_duration / max(len(speaker_segments), 1),
            "speaker_count": len(set(seg["speaker"] for seg in speaker_segments)),
            "conversation_start": start_time,
            "conversation_end": end_time,
        }