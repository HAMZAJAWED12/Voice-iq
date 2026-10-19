# app/services/intent_service.py

from app.utils.text_match import contains_phrase, starts_with_phrase


class IntentService:
    """
    Simple intent classifier.
    You can later swap this with a transformer / Rasa / etc.
    Keywords match whole words only ("ok" is not found in "book").
    """

    @staticmethod
    def classify_utterance(text: str) -> str:
        t = (text or "").strip().lower()

        if not t:
            return "other"

        if any(starts_with_phrase(t, x) for x in ["hi", "hello", "hey"]):
            return "greeting"

        if t.endswith("?") or any(contains_phrase(t, w) for w in ["?", "could you", "would you", "can you"]):
            return "question"

        if any(contains_phrase(t, w) for w in ["sorry", "apologize", "apologized", "my fault"]):
            return "apology"

        if any(
            contains_phrase(t, w) for w in ["angry", "mad", "upset", "upsetting", "frustrated", "terrible", "horrible"]
        ):
            return "complaint"

        if any(
            contains_phrase(t, w)
            for w in ["story", "when i was", "i remember", "i tell you", "basically what this is about"]
        ):
            return "storytelling"

        if any(contains_phrase(t, w) for w in ["thanks", "thank you", "appreciate it"]):
            return "gratitude"

        if any(contains_phrase(t, w) for w in ["okay", "ok", "that works", "sounds good"]):
            return "agreement"

        if any(contains_phrase(t, w) for w in ["bye", "goodbye", "talk to you later"]):
            return "closing"

        # default: info sharing
        if len(t.split()) > 5:
            return "information_sharing"

        return "other"

    @classmethod
    def annotate_conversation(cls, conversation: list[dict]) -> list[dict]:
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
    def summarize_intents(cls, conversation_with_intents: list[dict]) -> dict[str, int]:
        """
        Returns a simple frequency dict of intents across the whole conversation.
        """
        counts: dict[str, int] = {}
        for turn in conversation_with_intents or []:
            intent = turn.get("intent", "other")
            counts[intent] = counts.get(intent, 0) + 1
        return counts
