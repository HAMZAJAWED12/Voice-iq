from __future__ import annotations

from typing import Any

from app.insights.models.analytics_models import ValidationIssue, ValidationResult


class InsightValidator:
    REQUIRED_TOP_LEVEL_FIELDS = ["session_id", "utterances"]

    @classmethod
    def validate_raw_payload(cls, payload: Any) -> ValidationResult:
        result = ValidationResult(valid=True)

        if not isinstance(payload, dict):
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="invalid_payload_type",
                    message="Payload must be a dictionary.",
                    field="root",
                    severity="warning",
                )
            )
            return result

        cls._validate_top_level(payload, result)
        cls._validate_utterances(payload.get("utterances"), result)

        return result

    @classmethod
    def _validate_top_level(cls, payload: dict[str, Any], result: ValidationResult) -> None:
        for field in cls.REQUIRED_TOP_LEVEL_FIELDS:
            if field not in payload:
                result.valid = False
                result.errors.append(
                    ValidationIssue(
                        code="missing_required_field",
                        message=f"Missing required field '{field}'.",
                        field=field,
                        severity="warning",
                    )
                )

        session_id = payload.get("session_id")
        if session_id is not None and not isinstance(session_id, str | int):
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="invalid_session_id",
                    message="session_id must be a string or integer-like value.",
                    field="session_id",
                    severity="warning",
                )
            )

        duration_sec = payload.get("duration_sec")
        if duration_sec is not None and not cls._is_non_negative_number(duration_sec):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_duration_sec",
                    message="duration_sec should be a non-negative number; it may be recomputed.",
                    field="duration_sec",
                    severity="warning",
                )
            )

        speakers = payload.get("speakers")
        if speakers is not None and not isinstance(speakers, list):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_speakers_type",
                    message="speakers should be a list; it may be inferred from utterances.",
                    field="speakers",
                    severity="warning",
                )
            )

    @classmethod
    def _validate_utterances(cls, utterances: Any, result: ValidationResult) -> None:
        if utterances is None:
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="missing_utterances",
                    message="Payload must contain 'utterances'.",
                    field="utterances",
                    severity="warning",
                )
            )
            return

        if not isinstance(utterances, list):
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="invalid_utterances_type",
                    message="'utterances' must be a list.",
                    field="utterances",
                    severity="warning",
                )
            )
            return

        if len(utterances) == 0:
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="empty_utterances",
                    message="At least one utterance is required.",
                    field="utterances",
                    severity="warning",
                )
            )
            return

        previous_start: float | None = None

        for idx, utt in enumerate(utterances):
            field_prefix = f"utterances.{idx}"

            if not isinstance(utt, dict):
                result.valid = False
                result.errors.append(
                    ValidationIssue(
                        code="invalid_utterance_type",
                        message=f"Utterance at index {idx} must be a dictionary.",
                        field=field_prefix,
                        severity="warning",
                    )
                )
                continue

            cls._validate_single_utterance(idx, utt, result)

            start = utt.get("start")
            if cls._is_number(start):
                start_f = float(start)
                if previous_start is not None and start_f < previous_start:
                    result.warnings.append(
                        ValidationIssue(
                            code="utterance_order_irregular",
                            message=f"Utterance at index {idx} starts before the previous utterance in source order.",
                            field=f"{field_prefix}.start",
                            severity="warning",
                        )
                    )
                previous_start = start_f

    @classmethod
    def _validate_single_utterance(
        cls,
        idx: int,
        utt: dict[str, Any],
        result: ValidationResult,
    ) -> None:
        field_prefix = f"utterances.{idx}"

        for field in ["start", "end"]:
            if field not in utt:
                result.valid = False
                result.errors.append(
                    ValidationIssue(
                        code="missing_utterance_field",
                        message=f"Utterance {idx} is missing required field '{field}'.",
                        field=f"{field_prefix}.{field}",
                        severity="warning",
                    )
                )

        utt_id = utt.get("id")
        if utt_id is None:
            result.warnings.append(
                ValidationIssue(
                    code="missing_utterance_id",
                    message=f"Utterance {idx} has no id; one may be generated.",
                    field=f"{field_prefix}.id",
                    severity="warning",
                )
            )

        speaker = utt.get("speaker")
        if speaker is None or str(speaker).strip() == "":
            result.warnings.append(
                ValidationIssue(
                    code="missing_speaker",
                    message=f"Utterance {idx} has no speaker label; a fallback may be applied.",
                    field=f"{field_prefix}.speaker",
                    severity="warning",
                )
            )

        text = utt.get("text")
        if text is None:
            result.warnings.append(
                ValidationIssue(
                    code="missing_text",
                    message=f"Utterance {idx} has no text; empty text will be used.",
                    field=f"{field_prefix}.text",
                    severity="warning",
                )
            )
        elif not isinstance(text, str):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_text_type",
                    message=f"Utterance {idx} text should be a string; it will be coerced.",
                    field=f"{field_prefix}.text",
                    severity="warning",
                )
            )

        start = utt.get("start")
        end = utt.get("end")

        if start is not None and not cls._is_non_negative_number(start):
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="invalid_start_time",
                    message=f"Utterance {idx} start must be a non-negative number.",
                    field=f"{field_prefix}.start",
                    severity="warning",
                )
            )

        if end is not None and not cls._is_non_negative_number(end):
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="invalid_end_time",
                    message=f"Utterance {idx} end must be a non-negative number.",
                    field=f"{field_prefix}.end",
                    severity="warning",
                )
            )

        if cls._is_number(start) and cls._is_number(end) and float(end) < float(start):
            result.valid = False
            result.errors.append(
                ValidationIssue(
                    code="invalid_time_order",
                    message=f"Utterance {idx} end must be greater than or equal to start.",
                    field=field_prefix,
                    severity="warning",
                )
            )

        word_count = utt.get("word_count")
        if word_count is not None and not cls._is_non_negative_int(word_count):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_word_count",
                    message=f"Utterance {idx} word_count should be a non-negative integer.",
                    field=f"{field_prefix}.word_count",
                    severity="warning",
                )
            )

        overlap = utt.get("overlap")
        if overlap is not None and not isinstance(overlap, bool):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_overlap_type",
                    message=f"Utterance {idx} overlap should be boolean; it will be coerced.",
                    field=f"{field_prefix}.overlap",
                    severity="warning",
                )
            )

        for conf_field in ["confidence", "diarization_confidence"]:
            conf_value = utt.get(conf_field)
            if conf_value is not None and not cls._is_probability(conf_value):
                result.warnings.append(
                    ValidationIssue(
                        code="invalid_probability",
                        message=f"Utterance {idx} field '{conf_field}' should be between 0 and 1.",
                        field=f"{field_prefix}.{conf_field}",
                        severity="warning",
                    )
                )

        sentiment = utt.get("sentiment")
        if sentiment is not None and not isinstance(sentiment, dict):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_sentiment_type",
                    message=f"Utterance {idx} sentiment should be an object/dict.",
                    field=f"{field_prefix}.sentiment",
                    severity="warning",
                )
            )

        emotion = utt.get("emotion")
        if emotion is not None and not isinstance(emotion, dict):
            result.warnings.append(
                ValidationIssue(
                    code="invalid_emotion_type",
                    message=f"Utterance {idx} emotion should be an object/dict.",
                    field=f"{field_prefix}.emotion",
                    severity="warning",
                )
            )

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, int | float) and not isinstance(value, bool)

    @classmethod
    def _is_non_negative_number(cls, value: Any) -> bool:
        return cls._is_number(value) and float(value) >= 0.0

    @staticmethod
    def _is_non_negative_int(value: Any) -> bool:
        return isinstance(value, int) and value >= 0

    @classmethod
    def _is_probability(cls, value: Any) -> bool:
        return cls._is_number(value) and 0.0 <= float(value) <= 1.0
