"""VoiceIQ Agent Brain.

Rule-based recommendation layer that reads processed conversation output
(transcript + insights + fact-checks) and emits action *recommendations*.
It never executes real-world actions — that is the Java Action Layer's
job. Every recommendation carries source evidence, an explanation, and a
confidence score.
"""
