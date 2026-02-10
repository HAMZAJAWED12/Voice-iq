# app/services/factcheck_service.py

import re
from typing import List, Dict
from app.utils.logger import logger


URL_RE = re.compile(r"\bhttps?://[^\s]+", re.IGNORECASE)
NUM_RE = re.compile(r"\b\d+\b")


class FactCheckService:
    """
    Scaffold for 'fact-checking against knowledge graphs'.

    Right now:
      - Extracts URLs and numbers from transcript.
      - Marks them as 'TO_VERIFY' so UI / later pipeline can call real KG APIs.

    Later:
      - You can connect this to Wikidata / DBpedia / custom KG / LLM tool-calls.
    """

    @staticmethod
    def extract_candidates(transcript: str) -> List[Dict]:
        t = transcript or ""
        urls = URL_RE.findall(t)
        nums = NUM_RE.findall(t)

        candidates: List[Dict] = []

        for u in urls:
            candidates.append(
                {
                    "type": "url",
                    "value": u,
                    "status": "TO_VERIFY",
                    "source": "regex",
                    "note": "URL mentioned in transcript; not yet checked against any knowledge graph.",
                }
            )

        for n in nums:
            candidates.append(
                {
                    "type": "number",
                    "value": n,
                    "status": "TO_VERIFY",
                    "source": "regex",
                    "note": "Numeric value mentioned; could be age, quantity, etc.",
                }
            )

        return candidates

    @classmethod
    def fact_check(cls, transcript: str) -> List[Dict]:
        """
        High-level API: returns 'fact_checks' list.

        Each item:
          {
            "type": "url" | "number" | ...,
            "value": "...",
            "status": "TO_VERIFY",
            "note": "..."
          }
        """
        logger.info("FactCheckService: extracting fact-check candidates.")
        return cls.extract_candidates(transcript or "")
