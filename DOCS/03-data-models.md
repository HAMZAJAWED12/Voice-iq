# 03 — Data Models

Every Pydantic model used by the Insight Service. Field types are quoted exactly as defined in code. Bounds and validators are reproduced verbatim.

---

## Input models — `app/insights/models/input_models.py`

### `SentimentLabel`

```python
SentimentLabel = Literal["positive", "neutral", "negative"]
```

### `SentimentInput`

| Field | Type | Bounds | Notes |
|-------|------|--------|-------|
| `label` | `Optional[SentimentLabel]` | one of `positive` / `neutral` / `negative` | optional |
| `score` | `Optional[float]` | `ge=0.0, le=1.0` | optional |

### `EmotionInput`

| Field | Type | Bounds |
|-------|------|--------|
| `values` | `Dict[str, float]` | each value `0.0–1.0` (validated by `model_validator`) |

A model validator rejects construction if any emotion value is outside `[0.0, 1.0]`.

### `UtteranceInput`

| Field | Type | Bounds | Default |
|-------|------|--------|---------|
| `id` | `str` | required | — |
| `speaker` | `str` | required | — |
| `start` | `float` | `ge=0.0` | required |
| `end` | `float` | `ge=0.0` | required |
| `text` | `str` | — | `""` |
| `word_count` | `Optional[int]` | `ge=0` | `None` |
| `sentiment` | `Optional[SentimentInput]` | — | `None` |
| `emotion` | `Optional[EmotionInput]` | — | `None` |
| `overlap` | `bool` | — | `False` |
| `confidence` | `Optional[float]` | `0.0–1.0` | `None` |
| `diarization_confidence` | `Optional[float]` | `0.0–1.0` | `None` |

A model validator enforces `end ≥ start`.

### `SessionMetaInput`

`source`, `language`, `created_at`, `pipeline_version`, all `Optional[str]`.

### `SessionInput`

| Field | Type | Default |
|-------|------|---------|
| `session_id` | `str` | required |
| `duration_sec` | `Optional[float]` (`ge=0.0`) | `None` |
| `speakers` | `List[str]` | `[]` |
| `utterances` | `List[UtteranceInput]` | `[]` |
| `meta` | `Optional[SessionMetaInput]` | `None` |
| `warnings` | `List[str]` | `[]` |
| `speaker_stats` | `Dict[str, dict]` | `{}` |
| `conversation_stats` | `Dict[str, dict]` | `{}` |

A model validator enforces `len(utterances) ≥ 1`. The class also defines an OpenAPI example via `model_config["json_schema_extra"]` keyed `sample-call-2026-04-22`, which is what surfaces in `/docs`.

---

## Analytics models — `app/insights/models/analytics_models.py`

### `ValidationIssue`

`code: str`, `message: str`, `field: Optional[str]`, `severity: str = "warning"`.

### `ValidationResult`

`valid: bool = True`, `errors: List[ValidationIssue]`, `warnings: List[ValidationIssue]`.

### `PauseMetric`

`start_after_utterance_id`, `end_before_utterance_id`, `duration_sec` (`ge=0.0`), `speaker_before`, `speaker_after`. All optional except `duration_sec`.

### `SessionMetrics`

| Field | Type | Default |
|-------|------|---------|
| `total_duration_sec` | `float` | `0.0` |
| `total_speakers` | `int` | `0` |
| `total_utterances` | `int` | `0` |
| `total_words` | `int` | `0` |
| `avg_utterance_length_words` | `float` | `0.0` |
| `avg_utterance_duration_sec` | `float` | `0.0` |
| `total_questions` | `int` | `0` |
| `total_pauses` | `int` | `0` |
| `avg_pause_sec` | `float` | `0.0` |
| `max_pause_sec` | `float` | `0.0` |

### `SpeakerMetrics`

| Field | Type | Default |
|-------|------|---------|
| `speaker` | `str` | required |
| `speaking_time_sec` | `float` | `0.0` |
| `utterance_count` | `int` | `0` |
| `word_count` | `int` | `0` |
| `speaking_ratio` | `float` | `0.0` |
| `word_ratio` | `float` | `0.0` |
| `avg_utterance_length_words` | `float` | `0.0` |
| `avg_utterance_duration_sec` | `float` | `0.0` |
| `question_count` | `int` | `0` |
| `interruption_count` | `int` | `0` |
| `overlap_count` | `int` | `0` |
| `first_spoke_at_sec` | `Optional[float]` | `None` |
| `last_spoke_at_sec` | `Optional[float]` | `None` |

### `AnalyticsBundle`

`session_metrics: SessionMetrics`, `speaker_metrics: Dict[str, SpeakerMetrics]`, `pauses: List[PauseMetric]`.

---

## Signal models — `app/insights/models/signal_models.py`

### `SentimentAggregate`

`label: Optional[str]`, `avg_score: Optional[float]`, `distribution: Dict[str, int]`, `sample_count: int = 0`.

### `EmotionAggregate`

`dominant: Optional[str]`, `distribution: Dict[str, float]`, `sample_count: int = 0`.

### `SentimentTrendPoint`

`utterance_id`, `speaker`, `start`, `end` (all required); `label: Optional[str]`, `score: Optional[float]`.

### `SessionSentimentTrend`

`direction: Optional[str]` — values used by `compute_sentiment_trend` are `"stable"`, `"improving"`, `"declining"`, `"mixed"`.
`slope: Optional[float]`.
`points: List[SentimentTrendPoint]`.

### `AggregatedSignals`

| Field | Type |
|-------|------|
| `session_sentiment` | `SentimentAggregate` |
| `session_emotion` | `EmotionAggregate` |
| `session_sentiment_trend` | `SessionSentimentTrend` |
| `speaker_sentiment` | `Dict[str, SentimentAggregate]` |
| `speaker_emotion` | `Dict[str, EmotionAggregate]` |
| `emotion_volatility_score` | `float = 0.0` |

---

## Insight models — `app/insights/models/insight_models.py`

### `SeverityLevel`

```python
SeverityLevel = Literal["low", "medium", "high"]
```

### `MarkerType`

```python
MarkerType = Literal[
    "emotional_shift",
    "high_tension",
    "interruption",
    "dominance_window",
    "inconsistency_candidate",
    "engagement_drop",
    "session_tone_decline",
]
```

### `ScalarEvidence`

`Union[float, int, str, bool]` — the only types allowed inside `evidence` dicts.

### `InsightFlag`

`type: str`, `speaker: Optional[str]`, `severity: SeverityLevel = "low"`, `reason: str`, `evidence: Dict[str, ScalarEvidence]`.

### `ScoreBreakdownItem`

`component: str`, `weight: float`, `value: float`, `reason: str`.

### `InsightScores`

| Field | Bounds | Default |
|-------|--------|---------|
| `dominance_score` | `0.0–1.0` | `0.0` |
| `engagement_score` | `0.0–1.0` | `0.0` |
| `conflict_score` | `0.0–1.0` | `0.0` |
| `cooperation_score` | `0.0–1.0` | `0.0` |
| `emotion_volatility_score` | `0.0–1.0` | `0.0` |
| `breakdown` | `Dict[str, List[ScoreBreakdownItem]]` | `{}` |

### `TimelineMarker`

`marker_id: str`, `type: MarkerType`, `time_sec: float (ge=0.0)`, `speaker: Optional[str]`, `severity: SeverityLevel = "low"`, `reason: str`, `start_sec: Optional[float] (ge=0.0)`, `end_sec: Optional[float] (ge=0.0)`, `evidence: Dict[str, ScalarEvidence]`.

### `SpeakerInsight`

`speaker: str`, `sentiment: Optional[SentimentAggregate]`, `emotion: Optional[EmotionAggregate]`, `dominance_ratio: float = 0.0`, `engagement_ratio: float = 0.0`, `flags: List[InsightFlag]`.

### `InsightBundle`

| Field | Type |
|-------|------|
| `session_sentiment` | `Optional[SentimentAggregate]` |
| `session_emotion` | `Optional[EmotionAggregate]` |
| `session_sentiment_trend` | `Optional[SessionSentimentTrend]` |
| `speaker_insights` | `Dict[str, SpeakerInsight]` |
| `scores` | `InsightScores` (required) |
| `flags` | `List[InsightFlag]` |
| `timeline` | `List[TimelineMarker]` |
| `escalation` | `Optional[EscalationAssessment]` |
| `inconsistency` | `Optional[InconsistencyAssessment]` |

---

## Escalation models — `app/insights/models/escalation_models.py`

### `EscalationLevel`

```python
EscalationLevel = Literal["none", "mild", "moderate", "severe"]
```

### `EscalationSignal`

`signal_type: str`, `severity: str = "low"`, `score: float = 0.0`, `reason: str`, `evidence: Dict[str, float|int|str|bool]`. **No speaker field.**

### `EscalationWindow`

`start_sec: float`, `end_sec: float`, `level: EscalationLevel`, `primary_speaker: Optional[str]`, `reason: str`, `evidence: Dict[str, scalar]`.

### `EscalationAssessment`

`level: EscalationLevel = "none"`, `score: float = 0.0`, `signals: List[EscalationSignal]`, `windows: List[EscalationWindow]`, `primary_speaker: Optional[str]`, `summary: str = "No escalation detected."`

---

## Inconsistency models — `app/insights/models/inconsistency_models.py`

### `InconsistencyLevel`

```python
InconsistencyLevel = Literal["none", "low", "medium", "high"]
```

Note that the level scale is `low / medium / high` here — different from escalation's `mild / moderate / severe`. The `_build_session_flags` mapping in the rule engine accounts for this.

### `InconsistencySignal`

`signal_type: str`, `severity: str = "low"`, `score: float = 0.0`, `speaker: Optional[str]`, `reason: str`, `evidence: Dict[str, scalar]`. **Has a `speaker` field**, unlike `EscalationSignal`.

### `InconsistencyWindow`

`start_sec: float`, `end_sec: float`, `level: InconsistencyLevel = "low"`, `speaker: Optional[str]`, `reason: str`, `evidence: Dict[str, scalar]`.

### `InconsistencyAssessment`

`level: InconsistencyLevel = "none"`, `score: float = 0.0`, `signals: List[InconsistencySignal]`, `windows: List[InconsistencyWindow]`, `primary_speaker: Optional[str]`, `summary: str = "No inconsistency detected."`

---

## API models — `app/insights/models/api_models.py`

### `SummaryBundle`

`overall_summary: str`, `speaker_summaries: Dict[str, str]`, `notable_concerns: List[str]`, `key_moments_summary: str`.

### `InsightMeta`

`service_version: str = "1.0.0"`, `threshold_profile: str = "default"`, `generated_at: Optional[str]`, `processing_ms: Optional[int]`.

### `InsightGenerateResponse` (top-level response)

| Field | Type |
|-------|------|
| `session_id` | `str` |
| `status` | `str` (`ok` / `warning` / `error`) |
| `validation` | `ValidationResult` |
| `analytics` | `AnalyticsBundle` |
| `insights` | `InsightBundle` |
| `summaries` | `SummaryBundle` |
| `warnings` | `List[str]` |
| `meta` | `InsightMeta` |

### `InsightSummaryResponse`

`session_id`, `overall_summary`, `notable_concerns`, `key_moments_summary`.

### `InsightSpeakersResponse`

`session_id`, `speaker_metrics: Dict[str, dict]`, `speaker_insights: Dict[str, dict]`, `speaker_summaries: Dict[str, str]`.

### `InsightTimelineResponse`

`session_id`, `timeline: List[dict]`.

### `InsightStoredRecord`

`session_id: str`, `status: str`, `payload: InsightGenerateResponse`. This is the persistence shape: the full response is stored verbatim against its session id.
