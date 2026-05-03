from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.insights.core.analytics_engine import InsightAnalyticsEngine
from app.insights.core.normalizer import InsightNormalizer
from app.insights.core.rule_engine import InsightRuleEngine
from app.insights.core.summary_engine import InsightSummaryEngine
from app.insights.core.validator import InsightValidator
from app.insights.models.analytics_models import (
    AnalyticsBundle,
    SessionMetrics,
    ValidationIssue,
    ValidationResult,
)
from app.insights.models.api_models import (
    InsightGenerateResponse,
    InsightMeta,
    SummaryBundle,
)
from app.insights.models.input_models import SessionInput
from app.insights.models.insight_models import InsightBundle, InsightScores


class InsightService:
    DEFAULT_SERVICE_VERSION = "1.0.0"
    DEFAULT_THRESHOLD_PROFILE = "default"

    @classmethod
    def generate(
        cls,
        session_input: SessionInput | dict,
        *,
        service_version: str | None = None,
        threshold_profile: str | None = None,
    ) -> InsightGenerateResponse:
        started = perf_counter()
        service_version = service_version or cls.DEFAULT_SERVICE_VERSION
        threshold_profile = threshold_profile or cls.DEFAULT_THRESHOLD_PROFILE
        warnings: list[str] = []

        validation, session = cls._parse_session_input(session_input)

        if session is None:
            return cls._build_error_response(
                session_id=cls._extract_session_id_fallback(session_input),
                validation=validation,
                warnings=warnings,
                started=started,
                service_version=service_version,
                threshold_profile=threshold_profile,
            )

        validation = cls._augment_validation(session, validation)

        if session.warnings:
            warnings.extend(session.warnings)

        try:
            analytics = InsightAnalyticsEngine.run(session)
            insights = InsightRuleEngine.run(session, analytics, threshold_profile=threshold_profile)
            summaries = InsightSummaryEngine.run(session, analytics, insights)

            status = "ok" if validation.valid else "warning"

            return InsightGenerateResponse(
                session_id=session.session_id,
                status=status,
                validation=validation,
                analytics=analytics,
                insights=insights,
                summaries=summaries,
                warnings=warnings,
                meta=InsightMeta(
                    service_version=service_version,
                    threshold_profile=threshold_profile,
                    generated_at=cls._utc_now_iso(),
                    processing_ms=cls._elapsed_ms(started),
                ),
            )

        except Exception as exc:
            validation.valid = False
            validation.errors.append(
                ValidationIssue(
                    code="pipeline_execution_error",
                    message=f"Insight pipeline failed: {exc}",
                    field="pipeline",
                    severity="warning",
                )
            )
            warnings.append("Insight generation encountered an internal pipeline error.")

            return cls._build_error_response(
                session_id=session.session_id,
                validation=validation,
                warnings=warnings,
                started=started,
                service_version=service_version,
                threshold_profile=threshold_profile,
            )

    @classmethod
    def generate_from_raw(
        cls,
        raw_payload: dict[str, Any],
        *,
        service_version: str | None = None,
        threshold_profile: str | None = None,
    ) -> InsightGenerateResponse:
        raw_validation = InsightValidator.validate_raw_payload(raw_payload)

        if not raw_validation.valid:
            return cls._build_error_response(
                session_id=str(raw_payload.get("session_id", "unknown_session")),
                validation=raw_validation,
                warnings=[],
                started=perf_counter(),
                service_version=service_version or cls.DEFAULT_SERVICE_VERSION,
                threshold_profile=threshold_profile or cls.DEFAULT_THRESHOLD_PROFILE,
            )

        try:
            session = InsightNormalizer.normalize_to_session_input(raw_payload)
        except ValidationError as exc:
            validation = ValidationResult(valid=False)
            for err in exc.errors():
                field_path = ".".join(str(x) for x in err.get("loc", [])) or None
                validation.errors.append(
                    ValidationIssue(
                        code=err.get("type", "validation_error"),
                        message=err.get("msg", "Invalid normalized payload."),
                        field=field_path,
                        severity="warning",
                    )
                )
            return cls._build_error_response(
                session_id=str(raw_payload.get("session_id", "unknown_session")),
                validation=validation,
                warnings=[],
                started=perf_counter(),
                service_version=service_version or cls.DEFAULT_SERVICE_VERSION,
                threshold_profile=threshold_profile or cls.DEFAULT_THRESHOLD_PROFILE,
            )

        response = cls.generate(
            session,
            service_version=service_version,
            threshold_profile=threshold_profile,
        )

        response.validation.errors.extend(raw_validation.errors)
        response.validation.warnings.extend(raw_validation.warnings)
        response.validation.valid = response.validation.valid and raw_validation.valid

        return response

    @classmethod
    def _parse_session_input(
        cls,
        session_input: SessionInput | dict,
    ) -> tuple[ValidationResult, SessionInput | None]:
        validation = ValidationResult(valid=True)

        if isinstance(session_input, SessionInput):
            return validation, session_input

        try:
            session = SessionInput.model_validate(session_input)
            return validation, session
        except ValidationError as exc:
            validation.valid = False
            for err in exc.errors():
                field_path = ".".join(str(x) for x in err.get("loc", [])) or None
                validation.errors.append(
                    ValidationIssue(
                        code=err.get("type", "validation_error"),
                        message=err.get("msg", "Invalid input."),
                        field=field_path,
                        severity="warning",
                    )
                )
            return validation, None

    @classmethod
    def _augment_validation(
        cls,
        session: SessionInput,
        validation: ValidationResult,
    ) -> ValidationResult:
        if not session.speakers:
            validation.warnings.append(
                ValidationIssue(
                    code="speakers_missing",
                    message="Speakers list was not supplied and will be inferred from utterances.",
                    field="speakers",
                    severity="warning",
                )
            )

        if session.duration_sec is None:
            validation.warnings.append(
                ValidationIssue(
                    code="duration_missing",
                    message="Session duration was not supplied and will be inferred from utterances.",
                    field="duration_sec",
                    severity="warning",
                )
            )

        if len(session.utterances) == 1:
            validation.warnings.append(
                ValidationIssue(
                    code="single_utterance_session",
                    message="Session contains only one utterance; insight quality may be limited.",
                    field="utterances",
                    severity="warning",
                )
            )

        unique_speakers = {u.speaker for u in session.utterances}
        if len(unique_speakers) == 1:
            validation.warnings.append(
                ValidationIssue(
                    code="single_speaker_session",
                    message="Only one distinct speaker detected; interactional insights may be limited.",
                    field="utterances.speaker",
                    severity="warning",
                )
            )

        empty_text_count = sum(1 for u in session.utterances if not u.text.strip())
        if empty_text_count > 0:
            validation.warnings.append(
                ValidationIssue(
                    code="empty_utterance_text",
                    message=f"{empty_text_count} utterance(s) contain empty text.",
                    field="utterances.text",
                    severity="warning",
                )
            )

        missing_word_count = sum(1 for u in session.utterances if u.word_count is None)
        if missing_word_count > 0:
            validation.warnings.append(
                ValidationIssue(
                    code="word_count_missing",
                    message=(
                        f"{missing_word_count} utterance(s) are missing word_count; "
                        f"counts will be derived from text."
                    ),
                    field="utterances.word_count",
                    severity="warning",
                )
            )

        has_sentiment = any(u.sentiment is not None for u in session.utterances)
        if not has_sentiment:
            validation.warnings.append(
                ValidationIssue(
                    code="sentiment_missing",
                    message="No sentiment data supplied; emotional shift detection may be limited.",
                    field="utterances.sentiment",
                    severity="warning",
                )
            )

        has_emotion = any(u.emotion is not None for u in session.utterances)
        if not has_emotion:
            validation.warnings.append(
                ValidationIssue(
                    code="emotion_missing",
                    message="No emotion vectors supplied; emotion aggregation may be limited.",
                    field="utterances.emotion",
                    severity="warning",
                )
            )

        irregular = cls._count_out_of_order_utterances(session)
        if irregular > 0:
            validation.warnings.append(
                ValidationIssue(
                    code="utterance_order_irregular",
                    message=f"{irregular} utterance transition(s) appear out of chronological order.",
                    field="utterances.start",
                    severity="warning",
                )
            )

        return validation

    @staticmethod
    def _count_out_of_order_utterances(session: SessionInput) -> int:
        if len(session.utterances) < 2:
            return 0

        irregular = 0
        prev = session.utterances[0]
        for curr in session.utterances[1:]:
            if curr.start < prev.start:
                irregular += 1
            prev = curr
        return irregular

    @classmethod
    def _build_error_response(
        cls,
        *,
        session_id: str,
        validation: ValidationResult,
        warnings: list[str],
        started: float,
        service_version: str,
        threshold_profile: str,
    ) -> InsightGenerateResponse:
        return InsightGenerateResponse(
            session_id=session_id,
            status="error",
            validation=validation,
            analytics=cls._empty_analytics_bundle(),
            insights=cls._empty_insight_bundle(),
            summaries=SummaryBundle(
                overall_summary="No valid conversational content was available for summary generation.",
                speaker_summaries={},
                notable_concerns=[],
                key_moments_summary="No notable timeline markers were generated.",
            ),
            warnings=warnings,
            meta=InsightMeta(
                service_version=service_version,
                threshold_profile=threshold_profile,
                generated_at=cls._utc_now_iso(),
                processing_ms=cls._elapsed_ms(started),
            ),
        )

    @staticmethod
    def _empty_analytics_bundle() -> AnalyticsBundle:
        return AnalyticsBundle(
            session_metrics=SessionMetrics(),
            speaker_metrics={},
            pauses=[],
        )

    @staticmethod
    def _empty_insight_bundle() -> InsightBundle:
        return InsightBundle(
            session_sentiment=None,
            session_emotion=None,
            speaker_insights={},
            scores=InsightScores(),
            flags=[],
            timeline=[],
        )

    @staticmethod
    def _extract_session_id_fallback(session_input: SessionInput | dict) -> str:
        if isinstance(session_input, SessionInput):
            return session_input.session_id
        if isinstance(session_input, dict):
            return str(session_input.get("session_id", "unknown_session"))
        return "unknown_session"

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((perf_counter() - started) * 1000)
