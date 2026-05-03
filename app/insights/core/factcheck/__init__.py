"""Rule-based Fact-Check Engine package.

Public surface (re-exported for ergonomic imports elsewhere in the codebase):
    from app.insights.core.factcheck import ClaimDetector, ClaimClassifier
"""

from __future__ import annotations

from app.insights.core.factcheck.claim_detector import ClaimDetector
from app.insights.core.factcheck.classifier import ClaimClassifier
from app.insights.core.factcheck.comparator import FactCheckComparator
from app.insights.core.factcheck.factcheck_engine import (
    FactCheckEngine,
    SourceRouter,
)
from app.insights.core.factcheck.scorer import FactCheckScorer

__all__ = [
    "ClaimDetector",
    "ClaimClassifier",
    "FactCheckComparator",
    "FactCheckEngine",
    "FactCheckScorer",
    "SourceRouter",
]
