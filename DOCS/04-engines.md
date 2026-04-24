# 04 — Engines

This is the per-engine reference. Each section documents one file under `app/insights/core/`, in the order the rule engine invokes it. Every detector, every threshold, every score component is reproduced from the source as it currently exists.

---

## `validator.py` — `InsightValidator`

Pure structural validation of the raw incoming payload. Produces a `ValidationResult` (`valid`, `errors`, `warnings`) without raising. The validator runs *before* normalization and is responsible for surfacing the exact field that failed.

### Validation codes

| Code | Severity | What triggers it |
|------|----------|-----------------|
| `missing_required_field` | error | required key absent at top level |
| `invalid_session_id` | error | `session_id` missing or empty |
| `invalid_duration_sec` | error | `duration_sec` present but not a non-negative number |
| `invalid_speakers_type` | error | `speakers` present but not a list of strings |
| `missing_utterances` | error | `utterances` key absent |
| `invalid_utterances_type` | error | `utterances` not a list |
| `empty_utterances` | error | `utterances` is `[]` |
| `invalid_utterance_type` | error | a list element is not a dict |
| `missing_utterance_field` | error | required key absent on an utterance |
| `invalid_start_time` / `invalid_end_time` | error | non-numeric or negative |
| `invalid_time_order` | error | `end < start` |
| `invalid_word_count` | error | present but not a non-negative integer |
| `invalid_overlap_type` | error | present but not a bool |
| `invalid_probability` | error | sentiment score / confidence outside `[0.0, 1.0]` |
| `invalid_sentiment_type` | error | not a dict |
| `invalid_emotion_type` | error | not a dict |
| `utterance_order_irregular` | warning | utterances not strictly chronological by `start` |

### Behaviour

- The validator never raises. Every failure becomes a `ValidationIssue` appended to the result.
- `severity` is either `"error"` or `"warning"`. The `valid` boolean is `False` if and only if there is at least one `error`.
- Field-level codes carry the offending field path in `field`, e.g. `"utterances[3].start"`.
- The validator only inspects raw dict structure. It does not coerce types and does not call any model.

---

## `normalizer.py` — `InsightNormalizer`

Coerces a raw dict into a Pydantic-validated `SessionInput`. This is what `InsightService.generate_from_raw` calls before it can run anything else.

### Defaults

```python
DEFAULT_SPEAKER = "UNKNOWN"
DEFAULT_LANGUAGE = "unknown"
```

### Per-utterance normalization

- `id` defaults to a deterministic `f"utt_{idx}"` if missing.
- `speaker` is upper-cased and `-`/space replaced with `_`. Missing or non-string speaker becomes `DEFAULT_SPEAKER`.
- `start` and `end` are coerced to floats, clamped to `≥ 0.0`, and `end` is forced to `max(end, start)` to satisfy the model invariant.
- `text` defaults to `""`. `word_count` is computed from `text.split()` if missing.
- `sentiment.label` is lower-cased and validated against `{"positive", "neutral", "negative"}`. Anything else becomes `None`.
- `sentiment.score` and `confidence` are clamped into `[0.0, 1.0]`.
- `emotion.values` keys are lower-cased; values outside `[0.0, 1.0]` are dropped.
- `overlap` is coerced to bool. Truthy strings (`"true"`, `"1"`) are accepted.

### Session-level normalization

- `speakers` is rebuilt from the union of utterance speakers if missing, preserving first-seen order.
- `duration_sec`, if absent, is left as `None`. The analytics engine fills it from `max(end)` later.
- `meta.language` defaults to `DEFAULT_LANGUAGE`.

The output is always a fully-validated `SessionInput` or — if even normalization fails — a `SessionInput` populated with safe defaults plus accumulated warnings on the `warnings` list.

---

## `analytics_engine.py` — `InsightAnalyticsEngine`

Pure metrics over the validated `SessionInput`. No NLP, no rules. Produces `AnalyticsBundle` (`session_metrics`, `speaker_metrics`, `pauses`).

### `SessionMetrics`

- `total_duration_sec` = `session.duration_sec` if provided, else `max(u.end for u in utterances)`.
- `total_speakers` = unique speakers across utterances.
- `total_utterances` = `len(utterances)`.
- `total_words` = sum of `word_count` (or `len(text.split())` fallback).
- `avg_utterance_length_words` = `total_words / total_utterances` (zero-safe).
- `avg_utterance_duration_sec` = mean of `(end - start)` per utterance.
- `total_questions` = count of utterances whose `text` ends with `?`.
- `total_pauses`, `avg_pause_sec`, `max_pause_sec` derived from the `pauses` list.

### `SpeakerMetrics` (per speaker)

- `speaking_time_sec` = sum of `(end - start)`.
- `speaking_ratio` = `speaking_time_sec / total_speaking_time` (sum across all speakers, zero-safe).
- `word_count`, `word_ratio` = analogous to speaking ratios.
- `avg_utterance_length_words`, `avg_utterance_duration_sec`.
- `question_count` = count of utterances ending in `?`.
- `interruption_count` = number of utterances by this speaker that begin while another speaker is still talking (`start < previous_speaker_end`).
- `overlap_count` = count of `overlap == True` flags on this speaker's utterances.
- `first_spoke_at_sec` / `last_spoke_at_sec` = `min(start)` / `max(end)`.

### `PauseMetric`

For each consecutive utterance pair, if `next.start - current.end > 0`, a `PauseMetric` is emitted with both bounding utterance ids, the duration, and both speakers. Pauses are not filtered by minimum here — every gap is a pause; thresholding happens later in the timeline engine.

---

## `signal_aggregation.py` — `SignalAggregationEngine`

Aggregates utterance-level sentiment and emotion into session and per-speaker views, plus the chronological sentiment trend and an emotion volatility score. Output: `AggregatedSignals`.

### `SentimentAggregate`

- `label` = the majority label across utterances with a label, with ties broken by ordering `negative > positive > neutral`.
- `avg_score` = mean of `score` across utterances that carry one (label-agnostic).
- `distribution` = count of each label seen.
- `sample_count` = number of utterances contributing.

### `EmotionAggregate`

- `dominant` = the emotion with the largest accumulated weight across all utterances (sum of per-utterance values for that emotion).
- `distribution` = normalised totals per emotion (each value in `[0.0, 1.0]`, summing to ~1.0 when at least one utterance had emotion data).
- `sample_count` = utterances that contributed at least one emotion value.

### `compute_sentiment_trend` → `SessionSentimentTrend`

- `points` is the chronological list of `SentimentTrendPoint` for utterances that carry a sentiment. The point's `score` is the raw `sentiment.score` if present, else `1.0` for positive, `0.0` for negative, `0.5` for neutral.
- `slope` is computed by simple linear regression over `(t, score)` where `t` is the midpoint of `(start, end)`. Returns `None` if fewer than two points.
- `direction` mapping:
  - `"stable"` if `|slope| < 0.005`
  - `"improving"` if `slope ≥ 0.005`
  - `"declining"` if `slope ≤ -0.005`
  - `"mixed"` if the trend reverses sign more than twice along the series (high oscillation)

### `emotion_volatility_score`

A `[0.0, 1.0]` score capturing how much the dominant emotion shifts utterance-to-utterance. Computed as the mean L1 distance between consecutive emotion vectors, normalised by a soft cap of `2.0`. Zero when fewer than two utterances carry emotion data.

### Per-speaker views

`speaker_sentiment` and `speaker_emotion` apply the same aggregation algorithms but partitioned by `utterance.speaker`. The session-level aggregates are recomputed from all utterances rather than averaged from speaker aggregates.

---

## `timeline_engine.py` — `InsightTimelineEngine`

Builds the chronological list of `TimelineMarker` entries. The orchestrator is `build_timeline(session, analytics, signals, thresholds)`. It calls each `_detect_*` helper in sequence, then sorts the result by `(time_sec, marker_id)`.

### `_detect_dominance_markers`

Emits one `dominance_window` per speaker whose `speaking_ratio ≥ thresholds.dominance_speaking_ratio_threshold` (default `0.60`) **or** `word_ratio ≥ thresholds.dominance_word_ratio_threshold` (default `0.60`).

- `time_sec` = the speaker's `first_spoke_at_sec`
- `start_sec` = `first_spoke_at_sec`, `end_sec` = `last_spoke_at_sec`
- `severity`: `"high"` if `speaking_ratio ≥ 0.75`, `"medium"` if `≥ 0.65`, else `"low"`
- `evidence`: `speaking_ratio`, `word_ratio`, `utterance_count`

### `_detect_pause_markers`

Emits an `engagement_drop` for each pause whose `duration_sec ≥ thresholds.engagement_drop_pause_threshold_sec` (default `3.0`).

- Severity: `"high"` if `duration_sec ≥ severe_engagement_drop_pause_threshold_sec` (default `6.0`), `"medium"` if `≥ 4.5`, else `"low"`
- `time_sec` = the pause's start (end of the previous utterance)
- `evidence`: `duration_sec`, `speaker_before`, `speaker_after`

### `_detect_interruption_markers`

For each utterance whose `start < previous.end` (speaker-different overlap onset) emits an `interruption` marker.

- `severity` is `"low"` for the first interruption per speaker pair, `"medium"` from the second, `"high"` from the fourth onward (counted across the session)
- `time_sec` = the interrupting utterance's `start`
- `evidence`: `interrupter`, `interrupted`, `overlap_sec`

### `_detect_emotional_shift_markers`

Walks consecutive utterances on the same speaker. For each pair, computes the L1 distance between their emotion vectors. A marker is emitted when:

- `delta ≥ thresholds.emotional_shift_delta_threshold` (default `0.45`), AND
- `delta ≥ minimum_shift_delta` (constant `0.06`) — this guards against noise when the threshold profile is set very low

Severity: `"high"` if `delta ≥ severe_emotional_shift_delta_threshold` (default `0.70`), `"medium"` if `≥ 0.55`, else `"low"`. `evidence`: `delta`, `from_dominant`, `to_dominant`, `speaker`.

### `_detect_high_tension_markers`

A `high_tension` marker is emitted at the timestamp of any utterance that satisfies *all* of:

- speaker carries `frustrated` or `angry` ≥ `0.60` in its emotion vector, AND
- the utterance is itself an interruption or carries an `overlap` flag, AND
- the utterance's sentiment label is `negative`

Severity: `"high"` if the dominant emotion weight is `≥ 0.80`, `"medium"` if `≥ 0.70`, else `"low"`. `evidence`: `dominant_emotion`, `dominant_weight`, `is_interruption`, `is_overlap`.

### `_detect_session_tone_decline`

A single `session_tone_decline` marker if `signals.session_sentiment_trend.direction == "declining"` and `slope ≤ -0.02`. Placed at the midpoint of the session. Severity scales with `|slope|` (`≥ 0.05` → high, `≥ 0.03` → medium, else low). `evidence`: `slope`, `direction`.

### Sorting

After all detectors run, the marker list is sorted by `(time_sec, marker_id)`. `marker_id` follows a deterministic naming scheme: `f"{type}_{index}"`, ensuring stable ordering when two markers share the same timestamp.

---

## `scoring_engine.py` — `InsightScoringEngine`

Produces five normalised scores in `[0.0, 1.0]` plus a `breakdown` mapping each score name to a list of `ScoreBreakdownItem(component, weight, value, reason)`. Every score is wrapped in `_clamp(value, 0.0, 1.0)`.

### `dominance_score`

= `max(speaker_metrics[s].speaking_ratio for s in speakers)`. Single component breakdown: `("max_speaking_ratio", weight=1.0, value=score, reason="Highest individual share of speaking time")`.

### `engagement_score`

`engagement = 0.7 * pause_component + 0.3 * question_component + trend_bonus`

- `pause_component` = `1.0 - clamp(avg_pause_sec / 5.0, 0.0, 1.0)` — short pauses → high engagement
- `question_component` = `clamp(total_questions / max(total_utterances, 1), 0.0, 1.0)` — more questions → more engagement
- `trend_bonus` = `+0.05` if sentiment trend is `"improving"`, `-0.05` if `"declining"`, else `0.0`

Final value clamped to `[0.0, 1.0]`. Breakdown carries all three components.

### `conflict_score`

= `clamp((total_interruptions + total_overlaps) / max(total_utterances, 1), 0.0, 1.0)`.

`total_interruptions` and `total_overlaps` are summed across `speaker_metrics`. Breakdown: two components (`interruptions_per_utterance`, `overlaps_per_utterance`) each at weight `0.5`.

### `cooperation_score`

`cooperation = 0.35 * (1 - dominance_score) + 0.40 * (1 - conflict_score) + 0.25 * engagement_score`

Equal-balance speakers + low conflict + active engagement → high cooperation. Each of the three components appears in the breakdown with its weight and the source score it inverts.

### `emotion_volatility_score`

= `signals.emotion_volatility_score` (already `[0.0, 1.0]`). Single-component breakdown.

### Score discipline

- `_clamp(value, lo, hi)` is the only entry point for bounding. It never raises on type errors; non-numeric inputs become `0.0`.
- The breakdown is the audit trail for every score. The summary engine reads it when generating concerns ("conflict score is high primarily due to interruptions_per_utterance").

---

## `summary_engine.py` — `InsightSummaryEngine`

Produces a `SummaryBundle(overall_summary, speaker_summaries, notable_concerns, key_moments_summary)`. The engine is deterministic and template-based. It does not call any LLM.

### `overall_summary`

A single paragraph composed of:

1. **Tone**: derived from sentiment and conflict
   - `"strained"` if `conflict_score ≥ 0.7`
   - `"active and reasonably cooperative"` if `engagement_score ≥ 0.7` and `conflict_score < 0.3`
   - `"low-energy"` if `engagement_score < 0.35`
   - else `"moderately engaged"`
2. **Pause behaviour**:
   - `"substantial hesitation"` if `max_pause_sec ≥ 6.0`
   - `"noticeable pauses"` if `avg_pause_sec ≥ 3.0`
   - `"smooth pacing"` if `avg_pause_sec ≤ 1.0`
   - else `"steady pacing"`
3. **Sentiment trend**: pulled from `signals.session_sentiment_trend.direction`.
4. **Escalation/inconsistency**: appended only if either is at `"moderate"`/`"medium"` or higher.

### `speaker_summaries`

One entry per speaker. Each summary classifies role:

- `"dominant"` if `speaking_ratio ≥ 0.60`
- `"limited"` if `speaking_ratio ≤ 0.20`
- else `"balanced"`

Plus one tone descriptor (`"largely positive"`, `"predominantly negative"`, `"neutral"`, `"mixed"`) and an interaction descriptor flagging interruptions, overlaps, or low question-asking.

### `notable_concerns`

Up to 6 entries, each a single sentence. Built by ranking `InsightFlag` entries by severity (`high > medium > low`), then alphabetically by flag type. Format: `f"{severity_label}: {flag.reason}"`. Only flags whose severity is `"medium"` or `"high"` are surfaced.

### `key_moments_summary`

Top 5 timeline markers, ranked by severity, then by `time_sec`. Each rendered as `f"At {time_sec:.1f}s — {reason}"`. The summary string concatenates them with semicolons, or `"No notable moments."` if no markers reached `"medium"` or higher.

---

## `escalation_engine.py` — `InsightEscalationEngine`

Detects and quantifies conversation-wide escalation. Output: `EscalationAssessment(level, score, signals, windows, primary_speaker, summary)`.

### Detector outputs

Each detector returns `(Optional[EscalationSignal], List[EscalationWindow])`. The five detectors are:

1. **`_detect_sentiment_trend`** — fires when `signals.session_sentiment_trend.direction == "declining"` and `slope ≤ -0.02`. Score scales with `|slope|`, capped by `_CAP_SENTIMENT_TREND = 0.30`.
2. **`_detect_negative_density`** — fraction of utterances whose sentiment label is `"negative"`. Fires when fraction `≥ 0.30`. Capped by `_CAP_NEGATIVE_DENSITY = 0.30`.
3. **`_detect_emotional_strain`** — counts utterances where any of `frustrated`, `angry`, `fearful`, `disgusted` is `≥ 0.55`. Fires when count `≥ 2`. Capped by `_CAP_EMOTIONAL_STRAIN = 0.25`.
4. **`_detect_interruption_overlap_density`** — `(total_interruptions + total_overlaps) / max(total_utterances, 1)`. Fires when ratio `≥ 0.20`. Capped by `_CAP_INTERRUPTIONS = 0.20`.
5. **`_detect_marker_clusters`** — windows of 30 seconds containing ≥ 3 markers of types `{interruption, high_tension, emotional_shift}`. Each cluster contributes a window. Capped by `_CAP_CLUSTERS = 0.30`.

### Aggregation

`score = clamp(sum_of_signal_scores, 0.0, 1.0)` (scores already capped per detector to keep any one signal from saturating the aggregate).

### Level mapping

| Score band | Level |
|------------|-------|
| `< 0.20` | `"none"` |
| `0.20 – 0.39` | `"mild"` |
| `0.40 – 0.69` | `"moderate"` |
| `≥ 0.70` | `"severe"` |

### `primary_speaker`

The speaker with the highest emotional strain (or the highest interruption count if no strain detected). `None` if no detector contributed strain or interruptions.

### `summary`

Templated prose: `f"{Level} escalation detected: contributing factors include {comma-separated signal_types}."` or `"No escalation detected."` when level is `"none"`.

### Signal field

`EscalationSignal` does **not** carry a `speaker` field. Per-speaker attribution is on the windows (`primary_speaker`) and on the assessment itself.

---

## `inconsistency_engine.py` — `InsightInconsistencyEngine`

Detects mismatches between sentiment, emotion, and text signals. Output: `InconsistencyAssessment(level, score, signals, windows, primary_speaker, summary)`.

### Detector outputs

Each detector returns `(Optional[InconsistencySignal], List[InconsistencyWindow])`. The detectors are:

1. **`_detect_sentiment_emotion_mismatch`** — counts utterances where sentiment label is `"positive"` but emotion vector contains `frustrated`, `angry`, or `disgusted` `≥ 0.50` (or vice versa: `"negative"` label paired with `happy` or `excited` `≥ 0.50`). Fires when count `≥ 2`. Capped by `_CAP_SENTIMENT_EMOTION = 0.30`.
2. **`_detect_text_sentiment_contradiction`** — utterances whose text contains explicit positive markers (`"thanks"`, `"great"`, `"appreciate"`) but carry a negative label, or text contains negative markers (`"refund"`, `"angry"`, `"complaint"`) but carry a positive label. Fires when count `≥ 1`. Capped by `_CAP_TEXT_SENTIMENT = 0.25`.
3. **`_detect_abrupt_reversals`** — same-speaker consecutive utterances whose sentiment scores differ by `≥ 0.50`. Fires when count `≥ 2`. Capped by `_CAP_REVERSALS = 0.25`.
4. **`_detect_emotion_oscillation`** — same-speaker consecutive utterances where the dominant emotion changes between calming categories (`calm`, `happy`) and tense categories (`frustrated`, `angry`, `fearful`) more than twice. Capped by `_CAP_OSCILLATION = 0.20`.
5. **`_detect_masking_patterns`** — speakers whose mean sentiment score is `≥ 0.65` (positive lean) but whose dominant emotion is `frustrated`, `angry`, `fearful`, or `disgusted`. Often surfaces as "polite hostility". Capped by `_CAP_MASKING = 0.30`.

### Aggregation

`score = clamp(sum_of_signal_scores, 0.0, 1.0)`. Per-detector caps keep any single channel from dominating.

### Level mapping

| Score band | Level |
|------------|-------|
| `< 0.15` | `"none"` |
| `0.15 – 0.34` | `"low"` |
| `0.35 – 0.59` | `"medium"` |
| `≥ 0.60` | `"high"` |

Note that the scale uses `low / medium / high`, distinct from escalation's `mild / moderate / severe`. The rule engine's `_build_session_flags` accounts for this difference when synthesising the `conversation_inconsistency` flag.

### `primary_speaker`

The speaker who appears most often as the source of mismatch or masking signals. Falls back to the first speaker with any mismatch utterance, or `None`.

### Window emission

Each detector that fires also emits `InconsistencyWindow` entries bounded by the offending utterances' `(start, end)`, carrying `speaker`, `level`, `reason`, and `evidence`. These windows are lifted into the timeline as `inconsistency_candidate` markers by the rule engine.

### `summary`

`f"{Level.title()} inconsistency detected: {comma-separated signal_types}."` or `"No inconsistency detected."`.

### Signal field

`InconsistencySignal` **does** carry a `speaker` field, unlike `EscalationSignal`. This lets the summary engine attribute masking and contradiction patterns to the right person.

---

## `rule_engine.py` — `InsightRuleEngine`

Orchestrates everything inside the rules layer. `run(session, analytics, threshold_profile=None, thresholds=None)` is the only public method. It returns a fully-populated `InsightBundle`.

### Threshold resolution

If `thresholds` is passed explicitly, it is used. Otherwise the `threshold_profile` (default `"default"`) is resolved via `app.insights.config.defaults.get_thresholds_for_profile(profile)`. The current set of profiles is:

- `"default"` — the literal `DEFAULT_THRESHOLDS` instance
- `"strict"` — lowers the dominance / interruption thresholds and tightens the emotional-shift delta
- `"lenient"` — raises thresholds and increases minimum shift delta

### Execution sequence (fixed order)

```
1. signals = SignalAggregationEngine.aggregate(session.utterances)
2. speaker_insights = _build_speaker_insights(session, analytics, signals, thresholds)
3. timeline = InsightTimelineEngine.build_timeline(session, analytics, signals, thresholds)
4. escalation = InsightEscalationEngine.assess(session, analytics, signals, timeline)
5. inconsistency = InsightInconsistencyEngine.assess(session, analytics, signals)
6. timeline += _build_inconsistency_markers(inconsistency)  # then re-sort by (time_sec, marker_id)
7. session_flags = _build_session_flags(analytics, signals, escalation, inconsistency, thresholds)
8. flags = speaker_flags + session_flags
9. scores = InsightScoringEngine.compute_scores(analytics, timeline, speaker_insights, signals)
```

The order is load-bearing: the timeline must be built before escalation reads marker clusters, and the inconsistency-derived markers are appended *after* escalation so they don't perturb the cluster detector.

### `_build_speaker_insights`

For each speaker in `analytics.speaker_metrics`, builds a `SpeakerInsight` containing:

- `sentiment` and `emotion` from the corresponding entries in `signals.speaker_sentiment` / `signals.speaker_emotion`
- `dominance_ratio` = `speaking_ratio`
- `engagement_ratio` = a blend of `question_count` and `1 - normalised_pauses_around_this_speaker`
- `flags` — see below

### Speaker-level flag rules

| Flag type | Condition | Severity |
|-----------|-----------|----------|
| `frequent_interruptions` | `interruption_count ≥ thresholds.frequent_interruptions_threshold` (default `2`) | `"high"` if `≥ 4` else `"medium"` |
| `high_overlap_participation` | `overlap_count ≥ thresholds.high_overlap_participation_threshold` (default `2`) | `"medium"` |
| `low_inquiry_behavior` | `total_utterances ≥ thresholds.low_inquiry_min_utterances` (default `4`) AND `question_count == 0` | `"low"` |
| `speaker_emotional_strain` | speaker's emotion aggregate has `frustrated`, `angry`, or `fearful` `≥ 0.5` | `"high"` if `≥ 0.7` else `"medium"` |

All four flags carry `reason` and `evidence` per the explainability standard.

### `_build_session_flags`

Synthesises session-level flags by reading from analytics, signals, escalation, and inconsistency.

| Flag type | Condition | Severity |
|-----------|-----------|----------|
| `speaker_dominance` | `max(speaking_ratio) ≥ 0.60` | `"high"` if `≥ 0.75` else `"medium"` |
| `high_tension` | count of `high_tension` markers `≥ 1` | `"high"` if `≥ 4` else `"medium"` |
| `negative_session_mood` | `session_sentiment.label == "negative"` AND `avg_score ≤ 0.35` | `"medium"` |
| `declining_session_tone` | `session_sentiment_trend.direction == "declining"` and `slope ≤ -0.02` | `"high"` if `slope ≤ -0.05` else `"medium"` |
| `conversation_escalation` | `escalation.level in {"moderate", "severe"}` | `"high"` if `"severe"` else `"medium"` |
| `conversation_inconsistency` | `inconsistency.level in {"medium", "high"}` | `"high"` if `"high"` else `"medium"` |

The level-mapping for `conversation_inconsistency` is the explicit accounting for inconsistency's `low / medium / high` scale vs escalation's `mild / moderate / severe`.

### `_build_inconsistency_markers`

For each `InconsistencyWindow` in the assessment, emits a `TimelineMarker`:

```python
TimelineMarker(
    marker_id=f"inconsistency_{idx}",
    type="inconsistency_candidate",
    time_sec=window.start_sec,
    start_sec=window.start_sec,
    end_sec=window.end_sec,
    speaker=window.speaker,
    severity=_window_level_to_severity(window.level),
    reason=window.reason,
    evidence=window.evidence,
)
```

`_window_level_to_severity` maps `"high"` → `"high"`, `"medium"` → `"medium"`, `"low"` → `"low"`. Markers are appended to the timeline and the combined list is re-sorted.

### Bundle assembly

```python
return InsightBundle(
    session_sentiment=signals.session_sentiment,
    session_emotion=signals.session_emotion,
    session_sentiment_trend=signals.session_sentiment_trend,
    speaker_insights=speaker_insights,
    scores=scores,
    flags=flags,
    timeline=timeline,
    escalation=escalation,
    inconsistency=inconsistency,
)
```

This is the canonical output of the rules layer and the input to `InsightSummaryEngine`.
