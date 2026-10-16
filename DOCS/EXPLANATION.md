Last verified: 2026-10-16, 793e02e
# VoiceIQ in Plain English

This guide is for readers who do not write code: product owners, managers,
reviewers, and anyone deciding how far to trust VoiceIQ's output. It follows
one example call from upload to report, explains each part of the result,
says plainly what VoiceIQ cannot do, and decodes the project's internal
labels.

Other documents cover the rest. The technical design is in
[ARCHITECTURE.md](ARCHITECTURE.md) and [SYSTEM.md](SYSTEM.md). The feature
list is in [FEATURES.md](FEATURES.md) and security controls are in
[SECURITY.md](SECURITY.md). [DATA-FLOW.md](DATA-FLOW.md) lists every point
where data leaves the server.

Each section ends with a *Source* line naming the code it was checked
against, so an engineer can confirm it.

---

## 1. What VoiceIQ does, and for whom

VoiceIQ takes a recording of a conversation and returns a written analysis:
what was said, who said it, how it sounded, where it got tense, and what
might need doing next. It also produces a PDF (Portable Document Format)
report. It is built around two-party customer conversations, and labels the
two main voices CUSTOMER and AGENT.

VoiceIQ is a service, not an app with screens. Other software sends it a
recording through its API (application programming interface: a set way for
one program to make requests of another). The result comes back as JSON
(JavaScript Object Notation: structured text that programs read easily).

| Role | What they do with VoiceIQ |
|---|---|
| Calling application | Sends recordings and receives results. Any program that holds a valid access key can call VoiceIQ. The wider VoiceIQ platform also has a separate service written in Java, called the action layer, which can ask VoiceIQ for action suggestions directly and can receive them back from VoiceIQ. |
| Reviewer | A person who reads the results, such as a support team lead or a quality reviewer, and decides what the findings mean. |
| Approver | A person who accepts or rejects each suggested action in the platform before anything happens. VoiceIQ itself never acts. |
| Operator | Whoever runs the service. They issue access keys, switch optional features on or off, and keep the server running. |

The full audio route is the main entry point. Three narrower entry points
also exist: fact-check a piece of text, analyse a conversation that has
already been transcribed, and generate action suggestions from data the
platform supplies. This guide follows the full audio route.

*Source:* `app/routes/process_audio.py:118`;
`app/insights/api/factcheck_routes.py`; `app/insights/api/insight_routes.py`;
`app/agent_brain/api/agent_routes.py:26-38`; CUSTOMER/AGENT labels in
`app/services/alignment_service.py:446-455`; approval flag in
`app/agent_brain/models/recommendation.py:55`.

---

## 2. One call, start to finish

### The example call (illustrative)

The call below is invented for this guide. It is a 39-second support call:
a customer whose internet has stopped working talks to a support agent.

| Time (seconds) | Speaker | What was said |
|---|---|---|
| 0.0 – 3.5 | Agent | Hello, thanks for calling. How can I help? |
| 3.8 – 15.0 | Customer | My internet has not been working since Monday and I am really frustrated. This is the third time I have called about the same problem and nobody has fixed it. |
| 15.3 – 21.0 | Agent | I'm sorry about that. I can see a fault on your line. I will send an engineer on Friday. |
| 21.4 – 31.0 | Customer | Okay. Can you email me the booking details? And if it is not fixed by Friday I want to cancel my contract. |
| 31.3 – 37.0 | Agent | Of course. I will email you today, and my supervisor will call you tomorrow to follow up. |
| 37.3 – 39.0 | Customer | Thank you. Bye. |

**How the example results were produced.** The rule-based stages (intents,
flags, emotions, statistics, insights and action suggestions) were run on
this script using the service's own code. Two inputs were assumed rather
than measured: the sentiment readings (turn by turn: positive, negative,
neutral, negative, neutral, positive) and the voice labels (customer
SPEAKER_00, agent SPEAKER_01). The speech-recognition and AI (artificial
intelligence) model stages were not run for this guide, so the guide
describes them but shows no output from them. Real figures will differ from
call to call.

### The stages in order

| Step | Stage | Can it stop the call? |
|---|---|---|
| 1 | Upload checks | Yes |
| 2 | Audio clean-up and quality check | Yes |
| 3 | Speech-to-text | No |
| 4 | Who spoke when | No |
| 5 | Matching words to speakers | No |
| 6 | Reading the words: statistics, sentiment, keywords, gender, emotion, topic, summary, intent, flags, fact-check candidates | No |
| 7 | Insights: escalation, inconsistency, timeline, scores | No |
| 8 | Action suggestions (optional, off by default) | No |
| 9 | PDF report | No |
| 10 | Fact-check (optional, off by default) | No |

From step 3 onwards, a stage that fails is skipped and a warning is
recorded, and the remaining stages carry on. Fact-check runs last, after
the report is built. Its verdicts therefore appear in the response but not
in the PDF or the action suggestions.

*Source:* `app/pipeline/orchestrator.py:180-228` (stage order; the four
stop conditions); `app/routes/process_audio.py:200-231` (fact-check after
the pipeline).

### Step 1: Upload checks

VoiceIQ checks the request before it analyses anything:

- The caller must present a valid access key (an API key) once the operator
  has set keys up. A production server with no keys set refuses every
  request ([SECURITY.md](SECURITY.md) has the details). Each caller may send
  only a limited number of uploads per minute (default 30).
- The file must be MP3, WAV, M4A or FLAC, four common audio formats. The
  file name is checked first.
- The first bytes of the file must look like real audio, so a document or
  program that has been renamed is refused.
- The file must be under the size limit, which is 200 MB (megabytes) by
  default. VoiceIQ counts the bytes as the file arrives instead of trusting
  the size the caller declares.

An upload that fails a check is refused with a standard web error code and
a short reason, such as 413 (too large) or 415 (not audio), and nothing else
runs. In the example, `support-call.mp3` passes all four checks.

An accepted call gets a random request ID (identifier) and its own folder on
the server for the files it produces. These folders are deleted after 24
hours by default.

*Source:* `app/routes/process_audio.py:23-59, 132-198`;
`app/utils/upload.py:15-36`; `app/utils/audio_sniff.py`;
`app/insights/config/settings.py:100-108, 123-133, 144-149, 169-178`.

### Step 2: Audio clean-up and quality check

VoiceIQ converts the recording into one standard form: a single channel at
16,000 samples per second. This means every later stage receives the same
kind of audio. If the file cannot be converted, or the conversion takes
longer than the time limit (default 120 seconds), the call is refused.

VoiceIQ then measures the audio:

- If the recording is silent or nearly silent, the call is refused because
  there is nothing to analyse.
- It estimates the SNR (signal-to-noise ratio), which compares how loud the
  voices are with the background noise. A low SNR adds the warning
  `LOW_SNR_AUDIO`, and a very low SNR also adds `HEAVY_NOISE_AUDIO`. On noisy
  audio VoiceIQ skips the gender and emotion readings and treats speaker
  separation as less reliable.

Once an upload has passed step 1, only four things can stop the analysis:
no audio file, a failed conversion, a conversion that timed out, or
silence.

Assume the example is a clear recording, so it passes with no warnings.

*Source:* `app/pipeline/orchestrator.py:239-345, 549-589`;
`app/utils/audio_utils.py`; `app/utils/audio_quality.py:122-126`;
`app/routes/process_audio.py:209-222`.

### Step 3: Speech-to-text

Whisper, an open-source speech-recognition model, turns the audio into text.
It detects the language itself and reports it. The upload route always uses
Whisper's "base" size, near the small end of Whisper's range from "tiny" to
"large" ([README.md](../README.md)). The caller cannot change it.

For the example, assume Whisper hears every word correctly and detects
English.

If speech-to-text fails, the analysis continues with an empty transcript and
the warning `ASR_FAILED` (ASR stands for automatic speech recognition).
Later stages that need text are then skipped.

*Source:* `app/services/asr_service.py`;
`app/routes/process_audio.py:200-207`;
`app/pipeline/orchestrator.py:347-389`.

### Step 4: Who spoke when

A second open-source model, pyannote, splits the audio into stretches of
speech and labels each stretch by voice: SPEAKER_00, SPEAKER_01 and so on.
It does not know who anyone is. This step is called diarization.

- VoiceIQ assumes there are two speakers unless the caller says how many
  people are on the call.
- It keeps at most eight speakers. A voice with less than 5% of the
  talking time (7% on noisy audio) is relabelled SPEAKER_UNKNOWN.
- If the diarization model cannot be loaded, for example because the
  operator has not supplied its access token, VoiceIQ treats the whole call
  as one speaker and adds the warning `SINGLE_SPEAKER_MODE`.

For the example, assume the customer is labelled SPEAKER_00 and the agent
SPEAKER_01.

*Source:* `app/services/diarization_service.py:45-66, 76-128, 133-146,
246-329`; `app/pipeline/orchestrator.py:391-441`;
`app/routes/process_audio.py:121, 204`.

### Step 5: Matching words to speakers

VoiceIQ lines up the words from step 3 with the voice stretches from step 4.
Each piece of text then has a speaker, a start time and an end time. Where
two people talk at once, the overlap is marked. Consecutive pieces from the
same speaker are joined into turns.

The two main voices are then given roles based only on talking time. The
speaker who talks longest is labelled CUSTOMER and the next AGENT. In the
example the customer talks for about 22.5 seconds and the agent for about 15,
so the labels are correct. On a call where the agent talks longer, the labels
come out the wrong way round.

*Source:* `app/services/alignment_service.py:12-24, 430-490`;
`app/pipeline/orchestrator.py:443-513`.

### Step 6: Reading the words

Several stages then add notes to each piece of text. Each stage runs
separately, so if one fails the others still run.

| Stage | What it adds | How it works |
|---|---|---|
| Statistics | For each speaker: talking time, word count, words per minute, longest stretch, share of the conversation. For the whole call: length, number of text pieces, word count. | Arithmetic on the timed text |
| Sentiment | Positive, neutral or negative for each piece of text, with the model's confidence | An open-source AI model. A phrase of fewer than four words counts as neutral unless the model is very sure. |
| Keywords | Up to five key phrases per piece of text | Language tools that pick out noun phrases and rank them |
| Gender | An estimate per piece of text | Voice pitch. Skipped on noisy audio. |
| Emotion | Neutral, happy, sad, angry or fear, per piece of text | Read from the words and the sentiment, not from the voice. No audio emotion model is installed. Skipped on noisy audio. |
| Topic | One label for the whole call, chosen from a fixed list of eleven: sales, support, technical issue, billing, account management, personal development, health & wellness, product inquiry, customer success, complaint, general conversation | An AI model that reads only the first 512 characters |
| Summary | A short paragraph | An AI summarisation model that reads only the first 4,000 characters |
| Intent | One label per turn: greeting, question, apology, complaint, storytelling, gratitude, agreement, closing, information sharing, or other | Fixed word rules |
| Flags | Hesitation, aggression or lie risk, attached to a turn | Fixed word lists. The lie-risk flag states that it is a weak hint and not proof of deception. |
| Fact-check candidates | Every web address that starts with "http" and every number written in digits, marked "to verify" | A text scan on the server. Nothing is verified and nothing is sent anywhere. |

**What the example gets:**

- **Intents**, turn by turn: greeting, complaint, apology, question,
  information sharing, gratitude.
- **Emotions**, turn by turn: happy, sad, sad (the agent's "I'm sorry"),
  sad, neutral, happy (the customer's "Thank you").
- **Flags:** three hesitation flags, none of which reflects real hesitation.
  The hesitation word list includes "er", and the rule finds it inside
  other words, so "internet", "engineer" and "supervisor" each trigger a
  flag.
- **Fact-check candidates:** none, because the script contains no digits and
  no web addresses.

*Source:* `app/services/metadata_service.py`;
`app/services/sentiment_service.py:37-40, 110-170`;
`app/services/keyword_service.py`; `app/services/gender_service.py`;
`app/services/emotion_service.py:17-80`;
`app/services/topic_service.py:12-24, 59`;
`app/services/summary_service.py:38-58`; `app/services/intent_service.py`;
`app/services/flag_service.py:6-8, 26-79`;
`app/services/factcheck_service.py`;
`app/pipeline/orchestrator.py:493-658`.

### Step 7: Insights

The insight engines read the annotated conversation and look for patterns
across the whole call. Every finding comes with a written reason and the
evidence behind it, and every score lies between 0 and 1.

| Insight | What it means | Values |
|---|---|---|
| Escalation | Is the conversation heating up? It combines how much of the call is negative, a downward trend, signs of emotional strain, interruptions, and clusters of tense moments. | none, mild, moderate, severe |
| Inconsistency | Do the signals contradict each other? Examples: negative words with a positive sentiment reading, a sudden emotional reversal, a speaker who agrees and then disagrees, or a calm emotion reading over negative words. | none, low, medium, high |
| Timeline markers | Moments worth a look, each with a time and a reason: emotional shift, high tension, interruption, dominance window, inconsistency candidate, engagement drop (a long pause), decline in tone | Severity low, medium or high |
| Scores | Five indicators: dominance, engagement, conflict, cooperation and emotion volatility, each with a breakdown of what drove it | 0 to 1 |
| Flags | Findings about the whole call or one speaker, such as speaker dominance, high tension, negative mood or frequent interruptions | Severity low, medium or high |
| Summaries | An overall written summary, one summary per speaker, a list of notable concerns, and a paragraph on key moments | Text |

The insights refer to speakers by their voice labels from step 4
(SPEAKER_00, SPEAKER_01), not by the CUSTOMER and AGENT roles.

**What the example gets:**

- **Escalation: moderate** (score about 0.50). It is driven by how much
  of the call is negative and by the customer's emotional strain.
- **Inconsistency: low** (score 0.25). Both speakers' emotion labels swing
  within 30 seconds: the agent from "happy" on the greeting to "sad" on the
  apology, and the customer from "sad" to "happy" on "Thank you". The rules
  count these as abrupt reversals, though a person would see ordinary
  politeness.
- **Flags:** the customer dominates the call with 60% of the talking time.
  There are also call-level flags for the moderate escalation and the low
  inconsistency.
- **Scores:** dominance 0.60, engagement 0.81, conflict 0.00 (no
  interruptions or overlaps), cooperation 0.74, emotion volatility 0.33.

*Source:* `app/insights/service.py:59-104`;
`app/insights/core/rule_engine.py`;
`app/insights/core/escalation_engine.py:16-70, 276-283`;
`app/insights/core/inconsistency_engine.py:309-745`;
`app/insights/core/timeline_engine.py`;
`app/insights/core/scoring_engine.py`;
`app/insights/core/summary_engine.py`;
`app/insights/models/insight_models.py`;
`app/pipeline/orchestrator.py:660-715`.

### Step 8: Action suggestions (optional)

This step is off by default, and only the operator can switch it on (for
every call). When it is on, the **Agent Brain** reads the conversation and
the insights and proposes next steps. It has five rule-based agents, each
looking for one kind of action:

| Agent | Looks for | Suggests |
|---|---|---|
| Task | Commitments and assignments ("will", "need to", "make sure") | A task |
| Follow-up meeting | Requests to meet or talk again ("let's meet", "schedule a call") | A calendar entry |
| Email draft | Requests to send something ("email me", "send the") | An email with a draft subject and body |
| Escalation | Anger, complaints and risk words ("frustrated", "refund", "cancel my") | A support escalation |
| Fact-check review | Fact-check results that are false or unverified | A manual review |

Each suggestion includes:

- a title and description;
- a priority (LOW, MEDIUM, HIGH or CRITICAL) and a confidence from 0 to 1;
- the exact piece of the transcript it came from;
- any names and deadlines found, with spoken deadlines such as "today"
  turned into calendar dates when they are unambiguous;
- an explanation.

Duplicates are merged and the list is ranked. **Every suggestion is marked
as needing approval.** VoiceIQ never sends an email, books a meeting or
raises a ticket itself.

The fact-check review agent does nothing on this route, because fact-check
runs later (step 10). The other four agents work normally.

The operator can also connect VoiceIQ to the platform's action layer. When
that connection is set up, suggestions with a confidence of 0.50 or higher
(the default threshold) are sent to the action layer in the background,
with a signature the action layer can check to confirm they came from
VoiceIQ. The response itself
keeps every suggestion, so a reviewer can still see the low-confidence ones.

**What the example gets** (five suggestions):

| Suggestion | Triggered by | Priority | Confidence |
|---|---|---|---|
| Send the booking details by email | "Can you email me the booking details?" | HIGH | 0.925 |
| Escalate a service issue | The customer's first turn ("frustrated", "not working") | HIGH | 0.885 |
| Task: email today, supervisor to call (deadline "today") | "I will email you today…" | HIGH | 0.725 |
| Task: send an engineer (deadline "on Friday") | "I will send an engineer on Friday." | MEDIUM | 0.725 |
| A second email draft | "send an engineer", which the email rule's "send a" matched by mistake | HIGH | 0.925 |

The supervisor's promise to "call you tomorrow to follow up" produced a task
but no follow-up-meeting suggestion, because that wording is not on the
follow-up agent's phrase list.

*Source:* `app/pipeline/orchestrator.py:717-850`;
`app/agent_brain/core/agent_runner.py`;
`app/agent_brain/core/task_agent.py`, `followup_agent.py`,
`email_draft_agent.py`, `escalation_agent.py`,
`factcheck_review_agent.py`;
`app/agent_brain/models/recommendation.py:43-60`;
`app/agent_brain/config/settings.py:16-28`;
`app/insights/config/settings.py:205-216`.

### Step 9: PDF report

VoiceIQ builds a PDF report. The report is returned inside the response as
encoded text and is also saved in the call's folder. It has these sections:
warnings, audio quality, topic, summary, conversation statistics, speaker
statistics, emotion overview per speaker, intent counts, flags (hesitation,
aggression, lie risk), fact-check candidates, and the transcript (first 4,000
characters only).

The report leaves out the insight results from step 7 (escalation,
inconsistency, timeline markers and scores), the action suggestions, and the
fact-check verdicts. Those appear only in the response.

In the example, the PDF lists the three hesitation flags from step 6 under
Flags and has no fact-check candidates section.

The PDF can show only characters from the Latin-1 character set. Text in
Urdu or Arabic script, and even a euro sign or a long dash, makes the PDF
step fail. When that happens the response has no PDF and carries the warning
`PDF_FAILED`.

*Source:* `app/services/pdf_service.py:86-270` (fixed Helvetica font,
`fpdf==1.7.2` in `requirements.txt`);
`app/pipeline/orchestrator.py:852-880`; listed as a known issue in
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Remaining*).

### Step 10: Fact-check (optional, off by default)

Fact-check looks for statements of fact that it knows how to test. For each
one it asks a public data service for the real value and gives a verdict.

**Why it is off by default.** To check a claim, VoiceIQ sends a few words
taken from the call to an outside service: a currency code, a coin name, a
city, a share symbol or a country. It never sends the transcript or the
audio, but those few words are enough for the outside service to guess what
the call was about. Fact-check therefore runs only when the caller asks for
it on that request or the operator switches it on for every call. When it is
off, VoiceIQ makes no fact-check requests to outside services and the
response says `disabled`.

| Kind of claim | Example wording that is detected | Checked against |
|---|---|---|
| Exchange rate | "USD to PKR is 280" (US dollars to Pakistani rupees) | A currency-rate service (see below) |
| Cryptocurrency price | "Bitcoin is at 95000 dollars" | A public crypto-price service |
| Share price | "AAPL is at 175" (AAPL is a share symbol) | A stock-price service, only if the operator has supplied its key |
| Weather | "The weather in Karachi is 30 degrees" | A weather service, only if the operator has supplied its key |
| Country capital | "The capital of France is Paris" | Wikipedia |
| Commodity price | "Gold is at 2000 dollars" | No service yet. Always reported as unsupported. |

Only a short list of currencies, coins, share symbols and commodities is
recognised, and the wording has to follow patterns like the ones above.

A number is compared with the service's current value at the moment of
checking, not the value on the day the call took place. A difference of up
to 3% is TRUE, more than 3% and up to 10% is PARTIALLY_TRUE, and more than
10% is FALSE. A country's capital is compared by name. Each verdict comes with a
confidence label and a reason. Only the first 10,000 characters of the
transcript are checked, and the results are also saved in the service's
database.

At present every exchange-rate claim comes back `SOURCE_UNAVAILABLE`,
because the currency service now requires an access key and none is
configured. Which service to use instead is an open decision; see
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).

**What the example gets:** the call contains nothing the fact-checker
recognises, so with fact-check switched on the result lists zero claims. If
the customer had said "USD to PKR is 280", that sentence would be detected as
an exchange-rate claim and returned as `SOURCE_UNAVAILABLE`, with the reason
that the provider requires an access key.

If fact-check itself fails, the response says `failed` and gives a reason;
the rest of the analysis is unaffected. A bad reply from one outside service
affects only the claim it was answering.

*Source:* `app/routes/process_audio.py:62-116, 224-229`;
`app/utils/factcheck_gate.py`;
`app/insights/core/factcheck/factcheck_engine.py:60-80, 160-245`;
`app/insights/core/factcheck/claim_detector.py:42-110` (patterns; the
example wordings were run through the detector);
`app/insights/core/factcheck/comparator.py:34-35, 54-140`;
`app/insights/core/factcheck/source_clients/forex_client.py:1-9, 24-30`;
`app/insights/models/factcheck_models.py:23-39, 155`;
[DATA-FLOW.md](DATA-FLOW.md); commits `9aff207`, `dc03a44`, `8e51a94`.

---

## 3. What the response contains

The field names below are the labels a reader will see in the raw response.

| Field | What it means |
|---|---|
| `request_id` | The call's unique reference |
| `transcript` | The full text from speech-to-text |
| `asr_meta` | Speech-to-text details: model size, detected language, the timed pieces of text, and a call-length field that can be empty |
| `segments` | The voice stretches from step 4: start, end, voice label, confidence |
| `speaker_segments` | Each piece of text with its voice label, times and notes (sentiment, keywords, gender, emotion) |
| `conversation` | The turns from step 5, with CUSTOMER/AGENT roles and an intent for each turn |
| `timeline` | A simpler list of the same turns: start, end, speaker, text, intent, overlap. This is different from the insight timeline markers. |
| `speaker_stats` | For each speaker: talking time, words, words per minute, longest stretch, first and last time heard, share of talking time and of words |
| `conversation_stats` | For the whole call: length, number of pieces, words, average turn length, number of speakers |
| `topic` | The topic label and the model's confidence |
| `summary` | The AI-written summary paragraph |
| `intents_summary` | How many turns had each intent |
| `flags` | Hesitation, aggression and lie-risk hints, each with speaker, time, a fixed score and a note |
| `emotion_overview` | For each speaker, the share of their text read as each emotion |
| `insights` | Everything from step 7, with reasons, evidence and a status. Empty if the step was skipped. If the insight engines hit an error, the status says `error` and the findings are blank. |
| `fact_checks` | The older "to verify" list of numbers and web addresses from step 6. These are not verified. |
| `fact_checks_v2` | The fact-check result from step 10: verdicts and counts, or a status of `disabled`, `failed` or `skipped` |
| `recommendations` | The action suggestions from step 8. Present only when that feature is on and the step succeeded. |
| `report_pdf_base64` | The PDF report, encoded as text. Empty if the PDF step failed. |
| `audio_quality` | Length, loudness, share of silence, estimated SNR, and the silent and noisy flags |
| `warnings` | Codes for anything that was skipped, degraded or failed (see the next table) |
| `single_speaker_mode` | True when only one voice was found, or speaker separation was unavailable |
| `pipeline_meta` | Run status, time spent on each stage, and the stages skipped |

### Warning codes

| Group | Codes | Meaning |
|---|---|---|
| Call refused | `MISSING_INPUT_AUDIO`, `AUDIO_NORMALIZATION_FAILED`, `AUDIO_NORMALIZATION_TIMEOUT`, `AUDIO_SILENT_OR_NEAR_SILENT` | The call stopped before any analysis, and the code comes back with the error |
| Audio quality | `LOW_SNR_AUDIO`, `HEAVY_NOISE_AUDIO`, `AUDIO_QUALITY_FAILED` | Noisy audio, or the quality check itself could not run |
| Speakers | `SINGLE_SPEAKER_MODE`, `SPEAKER_CAP_APPLIED`, `LOW_SNR_DIARIZATION_UNRELIABLE`, `DIARIZATION_FAILED_FALLBACK`, `FLAGS_LIMITED_SINGLE_SPEAKER` | Only one voice, extra voices merged, or speaker separation unreliable or unavailable |
| No words | `EMPTY_TRANSCRIPT`, `ASR_FAILED` | Speech-to-text found no words, or failed |
| Stage skipped | Codes containing `_SKIPPED_`, for example `GENDER_SKIPPED_LOW_SNR` | The stage did not run; the code says why |
| Stage failed | Any other code ending `_FAILED`, for example `TOPIC_FAILED`, `INSIGHTS_FAILED`, `PDF_FAILED` | The stage broke and its part of the response is empty; everything else continued |

*Source:* `app/pipeline/orchestrator.py:239-965` (every warning code and
the response fields); `app/insights/service.py:86-104, 310-339` (insight
error status); `app/routes/process_audio.py:62-116, 224-231`
(`fact_checks_v2`); `app/utils/factcheck_gate.py:18-21`.

---

## 4. What VoiceIQ does not do

**It is decision support, not a source of truth.** Its findings point a
reviewer to where they should look. They are not a verdict on the
conversation or the people in it.

- **Most labels come from simple rules, not from judgement.** Intents, flags,
  emotions and inconsistencies come from fixed word lists and thresholds.
  These rules misfire. In the example, "internet" triggered a hesitation
  flag, and "send an engineer" produced an email suggestion. The lie-risk
  flag states that it is not proof of deception.
- **Emotion is read from the words, not the voice.** No audio emotion model
  is installed, so the emotion label follows the wording and the sentiment.
- **Gender is a guess from voice pitch.**
- **CUSTOMER and AGENT are assigned by talking time.** If the agent talks
  more than the customer, the roles are swapped.
- **It assumes two speakers.** Unless the caller gives the number of
  speakers, VoiceIQ looks for two. Minor voices are grouped as unknown. If
  the diarization model is unavailable, the whole call is treated as one
  speaker.
- **Its vocabulary is English-only.** Speech-to-text detects other
  languages, but the word lists used afterwards are English. The action
  suggestion rules have only an English list, and any other language falls
  back to it. Sentiment strips out characters that are not basic Latin
  letters, digits or punctuation, so text in Urdu or Arabic script is read as
  neutral. Adding Urdu and Arabic vocabulary is planned work (N4b; see the
  glossary).
- **It reads only the start of a long call for some outputs.** The topic
  uses the first 512 characters, the summary and the PDF transcript the first
  4,000, and fact-check the first 10,000.
- **The PDF report is partial and fragile.** It leaves out the insights,
  the action suggestions and the fact-check verdicts. It fails entirely on
  any character outside Latin-1.
- **Fact-check is narrow.** It recognises only exchange rates, crypto and
  share prices, weather, country capitals and (without a source) commodity
  prices. Exchange-rate claims cannot be verified at present. Share-price and
  weather claims need keys that the operator must supply. The claim detector
  can also mistake a name for a price: "Ada is 30" is read as a
  cryptocurrency price claim, because ADA is also a coin's symbol. Such claims
  are typically reported FALSE with high confidence. Milestone M5, which is
  planned, addresses this.
- **Scores are indicators, not probabilities.** Each score is calculated by
  fixed rules from measures such as share of talking time, pauses, questions
  and interruptions, then kept within 0–1.
- **It does not act.** Every action suggestion needs human approval, and
  VoiceIQ sends no emails and books nothing.
- **The caller waits for the whole analysis.** There is no "submit now,
  collect later" mode. Sprint 7's plan moves fact-check, not the whole
  analysis, into a background job (Phase 6; see D2 in the glossary). That
  work is planned, not built.
- **It is not an archive.** Each call's files are deleted after 24 hours by
  default. When fact-check runs, its results stay in the service's database
  until the operator removes them.

*Source:* `app/services/flag_service.py:13-19, 62-79`;
`app/services/emotion_service.py:17-24, 56-66`;
`app/services/gender_service.py:9-17`;
`app/services/alignment_service.py:446-455`;
`app/services/diarization_service.py:49-51, 202, 264`;
`app/agent_brain/extraction/language.py`;
`app/agent_brain/core/email_draft_agent.py:19-34, 40-44`;
`app/services/sentiment_service.py:91`;
`app/insights/core/factcheck/factcheck_engine.py:69-82`;
`app/insights/core/factcheck/scorer.py:1-18`;
`app/insights/core/scoring_engine.py`;
`app/agent_brain/models/recommendation.py:44, 55`;
`app/routes/process_audio.py:200-207`;
`app/insights/config/settings.py:123-133`;
[DATA-FLOW.md](DATA-FLOW.md) (*Data retention*);
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Remaining*,
*Next milestone*).

---

## 5. Glossary of internal codes

The project uses short codes for its pieces of work and its decisions. This
glossary gives only what each code means. **For the current status of any
decision, see the decision register in
[FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register)
(D1–D10) or the open-decisions table in
[N4B-MULTILINGUAL-STRATEGY.md §7](N4B-MULTILINGUAL-STRATEGY.md#7-open-decisions)
(L1–L5). For where the project stands now, see
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).**

### Work stages

| Code | Meaning |
|---|---|
| Sprint 1 | The first insight engines: signal aggregation, rules, timeline, scoring, summary |
| Sprint 2 | The escalation and inconsistency engines |
| Sprint 3 | Saving results in a small file-based database (SQLite), operator settings, and packaging to run in a container (Docker) |
| Sprint 4 | The first handover documentation set, files `01`–`10` in this folder (see [00-index.md](00-index.md)) |
| Sprint 5 | The rule-based fact-check engine described in step 10 (commit `592e8e7`) |
| Sprint 6 | The Agent Brain described in step 8 (commits `ef5b5e8` to `ef005dd`) |
| Sprint 7 | The **Fact-Check Agent**: a planned replacement for the rule-based fact-checker that uses an open-source LLM (large language model) to find claims, search for evidence and cite sources. It is organised in Phases 0–7. Sprint 7 Phases 1–7 are planned, not built. |
| Phase 0 | Sprint 7's first phase: agreeing the contract and scope, and signing the decisions D1–D10. It produces documents only, with no code. |
| Phase 1 | Build a gold set (a hand-checked collection of example claims with known answers, used to measure accuracy), set up a model server, run a "bake-off" between candidate models, and pin the winner |
| Phase 2 | New version-2 data models, the connection to the language model, and settings |
| Phase 3 | Claim extraction that records which speaker said what and when, a check of whether a statement can be verified at all, de-duplication, and a limit on claims per call |
| Phase 4 | Evidence search: trusted data sources first, then a web-search adapter, with safeguards on which addresses may be fetched |
| Phase 5 | Verdicts based only on the evidence found, checked citations, and consistent confidence scores |
| Phase 6 | Running fact-check as a background job with its own version-2 routes and database tables, a status field in the response, and a PDF with citations that can print any character |
| Phase 7 | Time limits, load limits, failure handling, metrics, and a staged rollout |
| M1–M5 | Milestones: small fixes made one at a time while Sprint 7 work waits on Phase 0. M1 kept outbound request details out of the logs (`c1b35d9`, `cedfe96`). M2 made a bad reply from a data service produce `SOURCE_UNAVAILABLE` for that claim instead of a verdict or a crash (`9aff207`). M3 made currency-service errors say what went wrong (`dc03a44`). M4 stopped over-long numbers in a transcript from crashing the fact-check route or the audio route (`8e51a94`). M5, which is planned, stops the claim detector mistaking names and counts for prices and teaches it common currency phrasing. |
| Tier 1, 2, 3 | Earlier production-readiness rounds: access keys, size limits and time limits (Tier 1), test coverage (Tier 2), clean-ups and stricter automated checks (Tier 3) |
| Wave E (E1–E5) | Structural clean-ups from Tier 3, for example the audio content check (E4) and the faster speaker matching (E3) |
| Tier S | Eight security-hardening items, S1–S8, such as per-caller request limits, automatic deletion of old call files, and making fact-check opt-in. Record: [SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md). Current controls: [SECURITY.md](SECURITY.md). |
| Tier N | Four items left open after Tier S: N1 (a stronger signature scheme for messages to the action layer; [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md)), N2 (lighter test setup), N3 (faster speaker matching) and N4, split into N4a (turning spoken deadlines into dates) and N4b. See [TIER-N-PLAN.md](TIER-N-PLAN.md). |
| N4b | Support for Urdu and Arabic. N4b-1 adds vocabulary and wiring so the rules can use per-language word lists. N4b-2 recognises people's names written in Urdu or Arabic script. N4b-1 has its own numbered phases (1 to 5), which are separate from the Sprint 7 phases. See [N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md). |
| v1, v2 | Versions of the API. The routes in use today are version 1. Version 2 is the planned Sprint 7 fact-check interface. |

### Decisions

| Code | The question it answers |
|---|---|
| D1 | Persistence: should Sprint 7 add only new database tables, or adopt a tool for changing existing tables? |
| D2 | Should fact-check run while the caller waits, or as a background job? |
| D3 | Who owns and resources the gold set? (Background: [D3-GOLD-SET-BRIEF.md](D3-GOLD-SET-BRIEF.md).) |
| D4 | Does "open-source" cover web search too, or only the language model? |
| D5 | What GPU (graphics processing unit) and RAM (random-access memory) will production have, and how long may a fact-check take? |
| D6 | Is the Qwen3 model family mandatory, or may a newer version win the bake-off? |
| D7 | Which fact domains ship first? |
| D8 | How long must version 1 and the older response fields keep working? |
| D9 | Which sources count as authoritative for high-stakes claims? |
| D10 | When the fact-check job finishes, does it re-run every later consumer (the Agent Brain and the PDF), or only the PDF? |
| L1 | Who supplies and reviews the Urdu, Arabic and Roman-Urdu vocabulary? |
| L2 | How is a "mixed" language call identified, given that Whisper never reports "mixed"? |
| L3 | Is Roman Urdu (Urdu written in Latin letters) treated as Urdu or as mixed? |
| L4 | Should name recognition for Urdu and Arabic reuse the Sprint 7 model server instead of adding new language libraries? |
| L5 | Should the main audio route run the Agent Brain (step 8)? |

### Fact-check results

| Term | Meaning |
|---|---|
| TRUE | The claimed number is within 3% of the service's current value, or the claimed capital matches |
| PARTIALLY_TRUE | The claimed number is off by more than 3% and up to 10% |
| FALSE | The claimed number is off by more than 10%, or the claimed capital does not match |
| UNVERIFIED | The service answered, but the comparison could not be made (for example a missing value, or a real value of zero) |
| UNSUPPORTED_CLAIM_TYPE | VoiceIQ has no data service for this kind of claim (today, commodity prices) |
| SOURCE_UNAVAILABLE | No usable evidence came back. Causes include a service with no key configured, a service that refused or failed, or a reply that could not be trusted. It says nothing about whether the claim is true. |
| HIGH / MEDIUM / LOW / NONE | The confidence label on a verdict. UNVERIFIED, UNSUPPORTED_CLAIM_TYPE and SOURCE_UNAVAILABLE always have NONE. |
| `disabled`, `failed`, `skipped` | Fact-check status values in the response: not requested, broke while running, or no transcript to check |
| TO_VERIFY | The status on every item in the older `fact_checks` list. It means "not checked". |
| NEEDS_REVIEW | A status that the Agent Brain accepts from the platform. No current fact-check verdict produces it. |

### Components

| Term | Meaning |
|---|---|
| Agent Brain | VoiceIQ's action-suggestion layer: five rule-based agents (task, follow-up meeting, email draft, escalation, fact-check review) whose suggestions are scored, de-duplicated and ranked, and always need approval (step 8). It can run inside the audio route, or the platform can call it directly with its own data. |
| Action layer | The separate VoiceIQ platform service, written in Java, that receives suggestions and handles approval and execution |
| Insight engines | The rule-based analysis in step 7 |

*Source:* sprint history in [10-current-status.md](10-current-status.md)
and git (`94f6479`, `592e8e7`, `ef5b5e8`–`ef005dd`, `c6284b6`); phases in
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Remaining*);
milestones in git (`c1b35d9`, `cedfe96`, `9aff207`, `dc03a44`, `8e51a94`)
and [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Next milestone*);
Tier S in [SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md) and
`ec8dc1e`; Tier N in [TIER-N-PLAN.md](TIER-N-PLAN.md); decisions in
[FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register)
and [N4B-MULTILINGUAL-STRATEGY.md §7](N4B-MULTILINGUAL-STRATEGY.md#7-open-decisions);
verdicts in `app/insights/models/factcheck_models.py:32-41`,
`app/insights/core/factcheck/comparator.py`,
`app/insights/core/factcheck/scorer.py`; Agent Brain statuses in
`app/agent_brain/models/enums.py`,
`app/agent_brain/adapters/pipeline_adapter.py:41-48`.

---

## 6. Further reading

| For | Read |
|---|---|
| The project brief for AI assistants | [CLAUDE.md](../CLAUDE.md) |
| A short overview and quick start | [README.md](../README.md) |
| What the system is made of | [ARCHITECTURE.md](ARCHITECTURE.md), [SYSTEM.md](SYSTEM.md) |
| Every feature | [FEATURES.md](FEATURES.md) |
| Security controls and how to report a problem | [SECURITY.md](SECURITY.md) |
| Every point where data leaves the server | [DATA-FLOW.md](DATA-FLOW.md) |
| Decisions made so far | [DECISIONS.md](DECISIONS.md) |
| How work is done on this project | [WORKFLOW.md](WORKFLOW.md) |
| What changed and when | [CHANGES.md](CHANGES.md) |
| Background and history | [CONTEXT.md](CONTEXT.md) |
| Where Sprint 7 stands today | [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) |
| The earlier Sprint 1–4 handover set | [00-index.md](00-index.md) |
