"""End-to-end smoke test for the Inconsistency Engine.

Run from the project root:
    python run_inconsistency_smoke.py

Exercises three scenarios:
  1. A "noisy" session that should trigger multiple inconsistency signals.
  2. A benign session that should produce zero inconsistency.
  3. The full rule_engine pipeline output, including timeline + flags +
     summary, so you can eyeball the integration.
"""

from __future__ import annotations

from app.insights.core.analytics_engine import InsightAnalyticsEngine
from app.insights.core.inconsistency_engine import InsightInconsistencyEngine
from app.insights.core.rule_engine import InsightRuleEngine
from app.insights.core.summary_engine import InsightSummaryEngine
from app.insights.models.input_models import (
    EmotionInput,
    SentimentInput,
    SessionInput,
    UtteranceInput,
)
from app.insights.models.signal_models import AggregatedSignals


def _utt(uid, speaker, start, end, text="", sl=None, ss=None, emo=None):
    return UtteranceInput(
        id=uid,
        speaker=speaker,
        start=start,
        end=end,
        text=text,
        word_count=max(1, len(text.split())),
        sentiment=SentimentInput(label=sl, score=ss) if sl else None,
        emotion=EmotionInput(values=emo) if emo else None,
    )


def divider(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def show_assessment(label, assessment):
    print(f"\n[{label}]  level={assessment.level}  score={assessment.score:.3f}")
    print(f"  primary_speaker: {assessment.primary_speaker}")
    print(f"  summary: {assessment.summary}")
    if assessment.signals:
        print(f"  signals ({len(assessment.signals)}):")
        for s in assessment.signals:
            print(f"    - {s.signal_type}  score={s.score:.3f}  severity={s.severity}")
            print(f"        reason: {s.reason}")
            print(f"        evidence: {s.evidence}")
    if assessment.windows:
        print(f"  windows ({len(assessment.windows)}):")
        for w in assessment.windows[:5]:
            print(
                f"    - [{w.start_sec:.1f}s..{w.end_sec:.1f}s] "
                f"speaker={w.speaker} level={w.level}"
            )
            print(f"        {w.reason}")


# --------------------------------------------------------------------------- #
# Scenario 1: noisy session — should fire multiple signals.
# --------------------------------------------------------------------------- #
divider("Scenario 1: NOISY session (expect multiple inconsistency signals)")

noisy = SessionInput(
    session_id="noisy_demo",
    utterances=[
        _utt(
            "u1", "S1", 0.0, 2.0,
            "this is terrible and awful",
            sl="positive", ss=0.9,
            emo={"angry": 0.85, "calm": 0.15},
        ),
        _utt(
            "u2", "S2", 2.5, 4.0,
            "I really hate this experience",
            sl="positive", ss=0.85,
            emo={"frustrated": 0.7, "calm": 0.3},
        ),
        _utt(
            "u3", "S1", 4.5, 6.0,
            "yes I agree completely",
            sl="positive", ss=0.8,
            emo={"happy": 0.7, "calm": 0.3},
        ),
        _utt(
            "u4", "S1", 6.5, 8.0,
            "actually no I disagree",
            sl="negative", ss=0.3,
            emo={"angry": 0.7, "frustrated": 0.3},
        ),
    ],
)

result = InsightInconsistencyEngine.assess(
    noisy,
    InsightAnalyticsEngine.run(noisy),
    AggregatedSignals(),
)
show_assessment("noisy", result)


# --------------------------------------------------------------------------- #
# Scenario 2: benign session — should fire NO signals.
# --------------------------------------------------------------------------- #
divider("Scenario 2: BENIGN session (expect level='none')")

benign = SessionInput(
    session_id="benign_demo",
    utterances=[
        _utt(
            "u1", "S1", 0.0, 2.0,
            "thanks for the helpful explanation",
            sl="positive", ss=0.85,
            emo={"happy": 0.7, "calm": 0.3},
        ),
        _utt(
            "u2", "S2", 2.5, 4.5,
            "glad I could help",
            sl="positive", ss=0.8,
            emo={"happy": 0.65, "calm": 0.35},
        ),
    ],
)

result = InsightInconsistencyEngine.assess(
    benign,
    InsightAnalyticsEngine.run(benign),
    AggregatedSignals(),
)
show_assessment("benign", result)


# --------------------------------------------------------------------------- #
# Scenario 3: full pipeline (rule_engine + summary_engine) on noisy session.
# --------------------------------------------------------------------------- #
divider("Scenario 3: FULL PIPELINE on the noisy session")

analytics = InsightAnalyticsEngine.run(noisy)
bundle = InsightRuleEngine.run(noisy, analytics)
summaries = InsightSummaryEngine.run(noisy, analytics, bundle)

print(f"\nescalation:    level={bundle.escalation.level}  score={bundle.escalation.score:.3f}")
print(f"inconsistency: level={bundle.inconsistency.level}  score={bundle.inconsistency.score:.3f}")

print(f"\nflags ({len(bundle.flags)}):")
for f in bundle.flags:
    print(f"  - {f.type}  [{f.severity}]  {f.reason}")

print(f"\ntimeline ({len(bundle.timeline)} markers):")
for m in bundle.timeline:
    print(f"  - {m.type:<26} @ {m.time_sec:>5.1f}s  [{m.severity}]  {m.reason[:80]}")

print("\noverall_summary:")
print(f"  {summaries.overall_summary}")

print("\nnotable_concerns:")
for c in summaries.notable_concerns:
    print(f"  - {c}")

divider("DONE")
