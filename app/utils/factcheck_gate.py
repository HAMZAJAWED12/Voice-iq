"""Opt-in gate for fact-check enrichment on the audio pipeline (S3).

Extracted from ``app/routes/process_audio.py`` so the policy is unit-testable
without importing the route (which pulls in the full ML stack via the
orchestrator).

Fact-check transmits transcript-derived claim subjects (city, country,
currency, asset, ticker) to external providers, so it leaves the service's
trust boundary. It is therefore **opt-in**: off unless the caller asks for
it per request or the operator enables it globally. See DOCS/DATA-FLOW.md.
"""

from __future__ import annotations

from app.insights.config.settings import get_settings

#: Body of the ``fact_checks_v2`` stub returned when enrichment is skipped.
DISABLED_STUB: dict[str, str] = {
    "status": "disabled",
    "reason": "fact-check enrichment is opt-in; pass ?fact_check=true or set factcheck_auto_enrich",
}


def factcheck_requested(override: bool | None) -> bool:
    """Resolve whether to run fact-check for this request.

    Args:
        override: the per-request ``?fact_check=`` value, or ``None`` when
            the caller did not specify one.

    Returns:
        True when enrichment should run. The per-request override always
        wins; otherwise the ``factcheck_auto_enrich`` setting decides
        (default False).
    """
    if override is not None:
        return override
    return get_settings().factcheck_auto_enrich
