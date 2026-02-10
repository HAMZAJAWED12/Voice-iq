# app/services/flag_service.py

from typing import List, Dict
from app.utils.logger import logger


HESITATION_MARKERS = ["um", "uh", "you know", "er", "ah", "kind of", "sort of", "..."]
AGGRESSION_WORDS = ["crap", "stupid", "idiot", "hate", "dumb", "aggressive", "angry"]
ABSOLUTE_WORDS = ["always", "never", "impossible", "absolutely", "definitely"]


class FlagService:
    """
    Generates heuristic 'flags':
      - hesitation
      - aggression
      - lie_risk (very rough / NOT reliable)

    These are *not* truth detectors – just pattern-based hints for analysts.
    """

    @staticmethod
    def _lower(text: str) -> str:
        return (text or "").lower()

    @classmethod
    def generate_flags(cls, conversation: List[Dict]) -> List[Dict]:
        flags: List[Dict] = []

        for turn in conversation or []:
            text = cls._lower(turn.get("text", ""))
            speaker = turn.get("speaker", "UNKNOWN")
            start = turn.get("start", 0.0)
            end = turn.get("end", 0.0)

            # Hesitation
            if any(h in text for h in HESITATION_MARKERS):
                flags.append(
                    {
                        "type": "hesitation",
                        "speaker": speaker,
                        "start": start,
                        "end": end,
                        "text": turn.get("text", ""),
                        "score": 0.6,
                        "note": "Contains hesitation markers (um/uh/you know/etc.).",
                    }
                )

            # Aggression
            if any(w in text for w in AGGRESSION_WORDS):
                flags.append(
                    {
                        "type": "aggression",
                        "speaker": speaker,
                        "start": start,
                        "end": end,
                        "text": turn.get("text", ""),
                        "score": 0.7,
                        "note": "Contains aggressive or rude wording.",
                    }
                )

            # Lie risk (very soft heuristic!)
            if any(a in text for a in ABSOLUTE_WORDS) and any(
                h in text for h in ["i think", "maybe", "probably", "i guess"]
            ):
                flags.append(
                    {
                        "type": "lie_risk",
                        "speaker": speaker,
                        "start": start,
                        "end": end,
                        "text": turn.get("text", ""),
                        "score": 0.5,
                        "note": (
                            "Mix of absolute terms ('always/never') and hedging ('I think/maybe'). "
                            "This is a weak heuristic, NOT proof of deception."
                        ),
                    }
                )

        logger.info(f"FlagService: generated {len(flags)} flags.")
        return flags