"""Fact-Check Engine — orchestrator.

Wires the pieces together:

    transcript_text
        → ClaimDetector.detect()
        → ClaimClassifier.classify()
        → per claim:
            → route to source client by claim_type
            → FactCheckComparator.compare()
            → FactCheckScorer.score()
            → assemble FactCheckResult
        → assemble FactCheckResponse + stats

The engine is constructed with a `SourceRouter` that maps
`ClaimType → BaseSourceClient`. Tests inject a router with mock clients,
production code calls `SourceRouter.from_settings()` to build the real one.

A small in-memory TTL cache de-duplicates identical claim signatures
within a short window — protects scarce free-tier quotas (Alpha Vantage).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.insights.core.factcheck.claim_detector import ClaimDetector
from app.insights.core.factcheck.classifier import ClaimClassifier
from app.insights.core.factcheck.comparator import FactCheckComparator
from app.insights.core.factcheck.scorer import FactCheckScorer
from app.insights.core.factcheck.source_clients import (
    CoinGeckoClient,
    ForexClient,
    OpenWeatherClient,
    StaticFactsClient,
    StockClient,
)
from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import (
    ClaimType,
    DetectedClaim,
    Evidence,
    FactCheckResponse,
    FactCheckResult,
    FactCheckStats,
)

# --------------------------------------------------------------------------- #
# Source routing                                                              #
# --------------------------------------------------------------------------- #


class SourceRouter:
    """Maps each `ClaimType` to the source client that resolves it."""

    def __init__(self, mapping: dict[ClaimType, BaseSourceClient]) -> None:
        self._mapping = dict(mapping)

    def client_for(self, claim_type: ClaimType) -> BaseSourceClient | None:
        return self._mapping.get(claim_type)

    @classmethod
    def from_settings(cls, settings) -> SourceRouter:
        """Build the production router from `InsightSettings`.

        COMMODITY_PRICE has no implemented client yet → engine returns
        UNSUPPORTED_CLAIM_TYPE for commodity claims until added later.
        """
        timeout = settings.factcheck_http_timeout_sec
        return cls(
            {
                "CURRENCY_RATE": ForexClient(timeout_sec=timeout),
                "CRYPTO_PRICE": CoinGeckoClient(timeout_sec=timeout),
                "STOCK_PRICE": StockClient(api_key=settings.alphavantage_api_key, timeout_sec=timeout),
                "WEATHER": OpenWeatherClient(api_key=settings.openweather_api_key, timeout_sec=timeout),
                "STATIC_FACT": StaticFactsClient(timeout_sec=timeout),
            }
        )


# --------------------------------------------------------------------------- #
# Lightweight TTL cache for repeated claims                                   #
# --------------------------------------------------------------------------- #


@dataclass
class _CacheEntry:
    evidence: Evidence | None
    expires_at: float


class _TTLCache:
    """Tiny per-process cache. Not thread-safe; FastAPI endpoints are async
    and the engine is invoked synchronously per request — sufficient here.
    """

    def __init__(self, ttl_sec: float = 60.0, max_entries: int = 256) -> None:
        self._ttl = ttl_sec
        self._max = max_entries
        self._store: dict[tuple[str, str], _CacheEntry] = {}

    def get(self, key: tuple[str, str]) -> Evidence | None | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return entry.evidence

    def set(self, key: tuple[str, str], evidence: Evidence | None) -> None:
        if len(self._store) >= self._max:
            # Drop the oldest entry. Cheap O(N) scan; acceptable at small N.
            oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
            self._store.pop(oldest_key, None)
        self._store[key] = _CacheEntry(evidence=evidence, expires_at=time.monotonic() + self._ttl)


def _cache_key(claim: DetectedClaim) -> tuple[str, str]:
    """Deterministic cache signature for a claim (independent of claim_id)."""
    parts: list[str] = [claim.claim_type]
    for k in sorted(claim.subject):
        parts.append(f"{k}={claim.subject[k]}")
    return (claim.claim_type, "|".join(parts))


# --------------------------------------------------------------------------- #
# Engine                                                                      #
# --------------------------------------------------------------------------- #


class FactCheckEngine:
    """End-to-end orchestrator for the rule-based fact-check pipeline."""

    def __init__(
        self,
        router: SourceRouter,
        *,
        cache_ttl_sec: float = 60.0,
    ) -> None:
        self._router = router
        self._cache = _TTLCache(ttl_sec=cache_ttl_sec)

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        conversation_id: str,
        speaker_id: str,
        transcript_text: str,
    ) -> FactCheckResponse:
        """Run the full pipeline against `transcript_text` and return the
        structured response. Never raises on per-claim failures.
        """
        detected = ClaimDetector.detect(transcript_text)
        claims = ClaimClassifier.classify(detected)

        results: list[FactCheckResult] = []
        verified_count = 0
        skipped_count = 0

        for claim in claims:
            client = self._router.client_for(claim.claim_type)
            if client is None:
                results.append(
                    FactCheckResult(
                        claim=claim,
                        evidence=None,
                        verdict="UNSUPPORTED_CLAIM_TYPE",
                        confidence=FactCheckScorer.score("UNSUPPORTED_CLAIM_TYPE", None),
                        diff_pct=None,
                        reason=f"No source client registered for {claim.claim_type}.",
                    )
                )
                skipped_count += 1
                continue

            evidence = self._fetch_with_cache(claim, client)
            outcome = FactCheckComparator.compare(claim, evidence)
            confidence = FactCheckScorer.score(outcome.verdict, outcome.diff_pct)

            results.append(
                FactCheckResult(
                    claim=claim,
                    evidence=evidence,
                    verdict=outcome.verdict,
                    confidence=confidence,
                    diff_pct=outcome.diff_pct,
                    reason=outcome.reason,
                )
            )
            if outcome.verdict in {"TRUE", "FALSE", "PARTIALLY_TRUE"}:
                verified_count += 1
            else:
                skipped_count += 1

        stats = FactCheckStats(
            claims_detected=len(claims),
            verified=verified_count,
            skipped=skipped_count,
        )
        return FactCheckResponse(
            conversation_id=conversation_id,
            speaker_id=speaker_id,
            fact_check_results=results,
            stats=stats,
        )

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _fetch_with_cache(self, claim: DetectedClaim, client: BaseSourceClient) -> Evidence | None:
        key = _cache_key(claim)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        evidence = client.fetch(claim)
        self._cache.set(key, evidence)
        return evidence
