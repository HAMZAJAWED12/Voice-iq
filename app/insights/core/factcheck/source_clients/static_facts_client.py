"""Static-fact source client (Wikipedia REST page summary).

Currently supports the STATIC_FACT subtype "capital of <country> is <X>".
Verification strategy:

  1. Fetch the Wikipedia page summary for the country.
  2. Scan the extract for a "capital" phrase followed by a proper noun.
  3. Compare against the claimed capital with case-insensitive,
     accent-insensitive matching.

When the extract does not yield a clear capital token, the client returns
``None`` so the engine emits ``UNVERIFIED`` rather than guessing.
"""

from __future__ import annotations

import re
import unicodedata
from typing import ClassVar, Optional

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence


# Patterns for extracting the capital from a Wikipedia summary. Tried in
# declared order; first match wins. Patterns intentionally conservative -
# false positives here would corrupt every static-fact verdict.
#
# Real-world phrasings observed in Wikipedia REST extracts:
#   * "with its capital in Paris"
#   * "Its capital is Tokyo"
#   * "capital and largest city is Berlin"
#   * "Washington, D.C., is the capital"
#   * "Islamabad is the nation's capital"
#   * "The capital is Madrid"
_CAPITAL_TOKEN = r"[A-Z][A-Za-zÀ-ſ.\-]+(?:\s+[A-Z][A-Za-zÀ-ſ.\-]+){0,2}"

_CAPITAL_PATTERNS = [
    # Flexible "...capital ... is X" form. Handles all of:
    #   "capital is Paris"
    #   "Its capital is Tokyo"
    #   "capital and largest city is Berlin"
    #   "Its capital, largest city and main cultural and economic centre is Paris"
    # The bridge (`[^.]{0,120}?`) is non-greedy and forbids periods so we
    # don't span sentence boundaries.
    re.compile(
        r"\b(?:its\s+|the\s+)?capital"
        r"(?:[^.]{0,120}?)?"
        r"\s+is\s+"
        r"(?P<capital>" + _CAPITAL_TOKEN + r")"
    ),
    # "X is the capital" / "X is the nation's/country's capital"
    # Stricter token (no `.` allowed, max 2 words) so we don't swallow
    # a preceding sentence like "South Asia. Islamabad ..."
    re.compile(
        r"\b(?P<capital>[A-Z][a-zÀ-ſ]+(?:\s+[A-Z][a-zÀ-ſ]+)?)\s+is\s+"
        r"(?:the\s+|its\s+|the\s+nation's\s+|the\s+country's\s+)?capital"
    ),
    # "with its capital in Paris" / "capital in Paris" / "capital at Paris"
    re.compile(
        r"(?:its\s+)?capital\s+(?:in|at)\s+(?P<capital>" + _CAPITAL_TOKEN + r")"
    ),
    # "capital city is Madrid"
    re.compile(
        r"capital\s+city\s+(?:is\s+)?(?P<capital>" + _CAPITAL_TOKEN + r")"
    ),
]


def _normalize(text: str) -> str:
    """Lowercase + strip accents for forgiving comparison."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    no_marks = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return no_marks.casefold().strip()


class StaticFactsClient(BaseSourceClient):
    """Resolve STATIC_FACT claims via the Wikipedia REST summary endpoint."""

    name: ClassVar[str] = "wikipedia"
    BASE_URL: ClassVar[str] = "https://en.wikipedia.org/api/rest_v1/page/summary/"

    def fetch(self, claim: DetectedClaim) -> Optional[Evidence]:
        if claim.claim_type != "STATIC_FACT":
            return None

        country = claim.subject.get("country")
        if not country:
            return None

        # URL-friendly title; spaces → underscores. The REST endpoint is
        # case-insensitive on the first letter but not on subsequent words.
        title = country.strip().replace(" ", "_")
        url = f"{self.BASE_URL}{title}"

        payload = self._get_json(url)
        if not payload:
            return None

        extract = payload.get("extract") or ""
        capital = self._extract_capital(extract)
        if not capital:
            return None

        return Evidence(
            source=self.name,
            value_text=capital,
            raw={
                "country": country,
                "title": payload.get("title", title),
                "extract_snippet": extract[:240],
            },
        )

    # ------------------------------------------------------------------ #
    # Internal                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_capital(extract: str) -> Optional[str]:
        if not extract:
            return None
        # Scan up to the first 2000 chars. Wikipedia summaries often place
        # the capital sentence near the end of the lead paragraph (e.g.
        # France's "...is Paris." sentence sits past the 900-char mark).
        # 2000 chars still keeps us inside the lead, well clear of any
        # historical-capitals trivia in the body.
        head = extract[:2000]
        for pattern in _CAPITAL_PATTERNS:
            m = pattern.search(head)
            if m:
                candidate = m.group("capital").strip().rstrip(",.;:")
                if candidate:
                    return candidate
        return None

    @staticmethod
    def matches(claimed: str, actual: str) -> bool:
        """Public helper for the comparator: case + accent insensitive equal."""
        return _normalize(claimed) == _normalize(actual)
