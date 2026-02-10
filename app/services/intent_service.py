# app/services/intent_service.py

from typing import List, Dict
from app.utils.logger import logger


class IntentService:
    """
    Simple intent classifier.
    You can later swap this with a transformer / Rasa / etc.
    """

    @staticmethod
    def classify_utterance(text: str) -> str:
        t = (text or "").strip().lower()

        if not t:
            return "other"

        if any(t.startswith(x) for x in ["hi", "hello", "hey"]):
            return "greeting"

        if t.endswith("?") or any(w in t for w in ["?", "could you", "would you", "can you"]):
            return "question"

        if any(w in t for w in ["sorry", "apologize", "my fault"]):
            return "apology"

        if any(w in t for w in ["angry", "mad", "upset", "frustrated", "terrible", "horrible"]):
            return "complaint"

        if any(w in t for w in ["story", "when i was", "i remember", "i tell you", "basically what this is about"]):
            return "storytelling"

        if any(w in t for w in ["thanks", "thank you", "appreciate it"]):
            return "gratitude"

        if any(w in t for w in ["okay", "ok", "that works", "sounds good"]):
            return "agreement"

        if any(w in t for w in ["bye", "goodbye", "talk to you later"]):
            return "closing"

        # default: info sharing
        if len(t.split()) > 5:
            return "information_sharing"

        return "other"

    @classmethod
    def annotate_conversation(cls, conversation: List[Dict]) -> List[Dict]:
        """
        Takes your 'conversation' list and adds 'intent' to each turn.
        """
        result = []
        for turn in conversation or []:
            text = turn.get("text") or ""
            intent = cls.classify_utterance(text)
            turn = dict(turn)
            turn["intent"] = intent
            result.append(turn)
        return result

    @classmethod
    def summarize_intents(cls, conversation_with_intents: List[Dict]) -> Dict[str, int]:
        """
        Returns a simple frequency dict of intents across the whole conversation.
        """
        counts: Dict[str, int] = {}
        for turn in conversation_with_intents or []:
            intent = turn.get("intent", "other")
            counts[intent] = counts.get(intent, 0) + 1
        return counts