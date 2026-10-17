Last verified: 2026-10-17, dbae452
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

*Source:* `app/routes/process_audio.py:129`;
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
this script using the service's own code, most recently at `dbae452`. Two inputs were assumed rather
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

*Source:* `app/pipeline/orchestrator.py:183-231` (stage order; the five
stop conditions); `app/routes/process_audio.py:244-281` (fact-check after
the pipeline).

### Step 1: Upload checks

VoiceIQ checks the request before it analyses anything:

- The caller must present a valid access key (an API key) once the operator
  has set keys up. A production or staging server with no keys set refuses
  every request ([SECURITY.md](SECURITY.md) has the details). Each caller may
  send only a limited number of uploads per minute (default 30).
- The server must have room for the recording. By default each running copy
  of the service analyses one recording at a time and lets two more wait
  their turn. When that queue is full, a further upload is turned away at
  once with 503 (busy) and a "try again later" hint (by default, after 30
  seconds).
- The file must be MP3, WAV, M4A or FLAC, four common audio formats. The
  file name is checked first.
- The first bytes of the file must look like real audio, so a document or
  program that has been renamed is refused.
- The file must be under the size limit, which is 200 MB (megabytes) by
  default. VoiceIQ counts the bytes as the file arrives instead of trusting
  the size the caller declares.

An upload that fails a check is refused with a standard web error code and
a short reason, such as 413 (too large), 415 (not audio) or 503 (busy), and
nothing else runs. In the example, `support-call.mp3` passes every check.

An accepted call gets a random request ID (identifier) and its own folder on
the server for the files it produces. These folders are deleted after 24
hours by default.

*Source:* `app/routes/process_audio.py:26-68, 143-232`;
`app/security/request_guard.py:38-101` (key and declared size checked
before the upload is read); `app/security/api_key.py:36`;
`app/utils/upload.py:15-36`; `app/utils/audio_sniff.py`;
`app/insights/config/settings.py:104-130, 153-163, 184-189, 223-232`.

### Step 2: Audio clean-up and quality check

VoiceIQ converts the recording into one standard form: a single channel at
16,000 samples per second. This means every later stage receives the same
kind of audio. If the file cannot be converted, or the conversion takes
longer than the time limit (default 120 seconds), the call is refused. A
recording longer than the length limit, two hours by default, is refused
with 413 (too long); VoiceIQ never cuts it short and analyses only part.

VoiceIQ then measures the audio:

- If the recording is silent or nearly silent, the call is refused because
  there is nothing to analyse.
- It estimates the SNR (signal-to-noise ratio), which compares how loud the
  voices are with the background noise. A low SNR adds the warning
  `LOW_SNR_AUDIO`, and a very low SNR also adds `HEAVY_NOISE_AUDIO`. On noisy
  audio VoiceIQ skips the gender and emotion readings and treats speaker
  separation as less reliable.

Once an upload has passed step 1, only five things can stop the analysis:
no audio file, a failed conversion, a conversion that timed out, a
recording over the length limit, or silence.

Assume the example is a clear recording, so it passes with no warnings.

*Source:* `app/pipeline/orchestrator.py:242-340, 548-588`;
`app/utils/audio_utils.py`; `app/utils/audio_quality.py:122-126`;
`app/routes/process_audio.py:253-272`;
`app/insights/config/settings.py:131-138`.

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
`app/routes/process_audio.py:244-251`;
`app/pipeline/orchestrator.py:342-384`.

### Step 4: Who spoke when

A second open-source model, pyannote, splits the audio into stretches of
speech and labels each stretch by voice: SPEAKER_00, SPEAKER_01 and so on.
It does not know who anyone is. This step is called diarization.

- VoiceIQ assumes there are two speakers unless the caller says how many
  people are on the call.
- It keeps at most eight speakers. A voice with less than 5% of the
  talking time (7% on noisy audio) is relabelled SPEAKER_UNKNOWN.
- If the diarization model cannot be loaded or fails, for example because
  the operator has not supplied its access token, VoiceIQ treats the whole
  call as one speaker and adds the warnings `SINGLE_SPEAKER_MODE` and
  `MOCK_DIARIZATION_USED`. The second one says plainly that speaker
  separation did not happen.

For the example, assume the customer is labelled SPEAKER_00 and the agent
SPEAKER_01.

*Source:* `app/services/diarization_service.py:45-66, 76-128, 133-146,
246-329`; `app/pipeline/orchestrator.py:386-440`;
`app/routes/process_audio.py:132, 248`.

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
`app/pipeline/orchestrator.py:442-512`.

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
| Intent | One label per turn: greeting, question, apology, complaint, storytelling, gratitude, agreement, closing, information sharing, or other | Fixed word rules that match whole words only |
| Flags | Hesitation, aggression or lie risk, attached to a turn | Fixed word lists, matched as whole words only. The lie-risk flag states that it is a weak hint and not proof of deception. |
| Fact-check candidates | Every web address that starts with "http" and every number written in digits, marked "to verify" | A text scan on the server. Nothing is verified and nothing is sent anywhere. |

**What the example gets:**

- **Intents**, turn by turn: greeting, complaint, apology, question,
  information sharing, gratitude.
- **Emotions**, turn by turn: happy, sad, sad (the agent's "I'm sorry"),
  sad, neutral, happy (the customer's "Thank you").
- **Flags:** none. The hesitation word list includes "er". Before
  milestone M7 the rule also found it inside other words, so "internet",
  "engineer" and "supervisor" each raised a false hesitation flag. The rules
  now match whole words only, so they do not.
- **Fact-check candidates:** none, because the script contains no digits and
  no web addresses.

*Source:* `app/services/metadata_service.py`;
`app/services/sentiment_service.py:37-40, 110-170`;
`app/services/keyword_service.py`; `app/services/gender_service.py`;
`app/services/emotion_service.py:17-80`;
`app/services/topic_service.py:12-24, 59`;
`app/services/summary_service.py:38-58`; `app/services/intent_service.py`;
`app/services/flag_service.py:7-9, 28-81`; `app/utils/text_match.py`;
`app/services/factcheck_service.py`;
`app/pipeline/orchestrator.py:492-662`.

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

The insight engines read each sentiment reading as a positivity score
between 0 and 1: high for positive, low for negative, 0.5 for neutral, and
further from 0.5 the surer the model is. Since 17 October 2026 the audio
route converts its readings this way before the insights see them; before
that, the engines read the model's confidence as if it were positivity.

If the insight engines hit an error, the response says so under `insights`,
the warning `INSIGHTS_FAILED` is added, and step 8 is skipped. The rest of
the analysis still comes back.

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
- **Scores:** dominance 0.60, engagement 0.76, conflict 0.00 (no
  interruptions or overlaps), cooperation 0.73, emotion volatility 0.55.

*Source:* `app/insights/service.py:59-108`;
`app/insights/core/rule_engine.py`;
`app/insights/core/escalation_engine.py:16-70, 276-283`;
`app/insights/core/inconsistency_engine.py:309-745`;
`app/insights/core/timeline_engine.py`;
`app/insights/core/scoring_engine.py`;
`app/insights/core/summary_engine.py`;
`app/insights/models/insight_models.py`;
`app/pipeline/orchestrator.py:664-732`.

### Step 8: Action suggestions (optional)

This step is off by default, and only the operator can switch it on (for
every call). When it is on, the **Agent Brain** reads the conversation and
the insights and proposes next steps. It has five rule-based agents, each
looking for one kind of action:

| Agent | Looks for | Suggests |
|---|---|---|
| Task | Commitments and assignments ("will", "need to", "make sure") | A task |
| Follow-up meeting | Requests to meet or talk again ("let's meet", "schedule a call") | A calendar entry |
| Email draft | Requests to send something ("email me", "send the"), matched as whole words | An email with a draft subject and body |
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
runs later (step 10). The other four agents work normally. If the insights
in step 7 failed or were skipped, this whole step is skipped and the
warning `AGENT_BRAIN_SKIPPED_NO_INSIGHTS` is added.

The operator can also connect VoiceIQ to the platform's action layer. When
that connection is set up, suggestions with a confidence of 0.50 or higher
(the default threshold) are sent to the action layer in the background,
with a signature the action layer can check to confirm they came from
VoiceIQ. The response itself
keeps every suggestion, so a reviewer can still see the low-confidence ones.

**What the example gets** (four suggestions):

| Suggestion | Triggered by | Priority | Confidence |
|---|---|---|---|
| Send the booking details by email | "Can you email me the booking details?" | HIGH | 0.925 |
| Escalate a service issue | The customer's first turn ("frustrated", "not working") | HIGH | 0.885 |
| Task: email today, supervisor to call (deadline "today") | "I will email you today…" | HIGH | 0.725 |
| Task: send an engineer (deadline "on Friday") | "I will send an engineer on Friday." | MEDIUM | 0.725 |

The supervisor's promise to "call you tomorrow to follow up" produced a task
but no follow-up-meeting suggestion, because that wording is not on the
follow-up agent's phrase list. Before milestone M7, the email rule's phrase
"send a" also matched inside "send an engineer" and produced a fifth,
mistaken email suggestion. The email rule now matches whole words only; the
other agents still match their phrases anywhere in the text.

*Source:* `app/pipeline/orchestrator.py:734-867`;
`app/agent_brain/core/agent_runner.py`;
`app/agent_brain/core/task_agent.py`, `followup_agent.py`,
`email_draft_agent.py`, `escalation_agent.py`,
`factcheck_review_agent.py`; `app/agent_brain/extraction/signals.py`
(whole-word matching for the email rule);
`app/agent_brain/models/recommendation.py:43-60`;
`app/agent_brain/config/settings.py:16-28`;
`app/insights/config/settings.py:259-270`.

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

In the example, the PDF has no Flags section and no fact-check candidates
section, because the call produced neither.

The PDF uses a basic font that covers only Latin-1, the Western European
character set. Curly quotation marks, long dashes and similar punctuation
are swapped for their plain forms, and the euro sign prints as "EUR". Any
other character the font cannot show, such as Urdu or Arabic script, prints
as a question mark instead of failing the report. The response keeps the
original text. A report that can print every character is planned for
Sprint 7 (Phase 6).

*Source:* `app/services/pdf_service.py:29-71` (character mapping),
`119-303` (fixed Helvetica font, `fpdf==1.7.2` in `requirements.txt`),
`258-283` (the flags and fact-check sections appear only when there is
something to list); `app/pipeline/orchestrator.py:869-897`;
`app/insights/tests/test_pdf_unicode.py`; milestone M6 (`dc9caf3`) in
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Completed*).

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
| Exchange rate | "USD to PKR is 280" (US dollars to Pakistani rupees) | A free public currency-rate service that needs no key (see below) |
| Cryptocurrency price | "Bitcoin is at 95000 dollars" | A public crypto-price service |
| Share price | "AAPL is at 175" (AAPL is a share symbol) | A stock-price service, only if the operator has supplied its key |
| Weather | "The weather in Karachi is 30 degrees" | A weather service, only if the operator has supplied its key |
| Country capital | "The capital of France is Paris" | Wikipedia |
| Commodity price | "Gold is at 2000 dollars" | No service yet. Always reported as unsupported. |

Only a short list of currencies, coins, share symbols and commodities is
recognised, and the wording has to follow patterns like the ones above.
Wordings such as "1 USD = 280 PKR" and "Bitcoin is 95k" (read as 95,000)
are understood too.

A number that counts or measures something is not treated as a price.
"Ada is 30 years old" (ADA is also a coin's symbol) and "Meta is 30 people"
(META is also a share symbol) give no claim, and neither does "Ada is 30"
on its own: a name needs a sign of price, such as "dollars" or "$", before
it is checked. A price stated in another currency, such as rupees, is
skipped too, because the price services quote US dollars.

A number is compared with the service's current value at the moment of
checking, not the value on the day the call took place. A difference of up
to 3% is TRUE, more than 3% and up to 10% is PARTIALLY_TRUE, and more than
10% is FALSE. A country's capital is compared by name. Each verdict comes with a
confidence label and a reason. Only the first 10,000 characters of the
transcript are checked, and the results are also saved in the service's
database, where they are kept for 30 days by default.

Each fact-check stops after a set amount of work: by default it checks at
most 25 claims, and starts no new check once 20 seconds have passed. Claims
left over get no verdict and are counted as skipped.

Exchange-rate claims are checked against ExchangeRate-API's free open
service. Only the code of the currency being converted from (for example
"USD") is sent, and each currency's rates are fetched at most once an hour.
Until 17 October 2026 every exchange-rate claim came back
`SOURCE_UNAVAILABLE`, because the previous service had started to require
an access key; the owner chose the new service that day.

**What the example gets:** the call contains nothing the fact-checker
recognises, so with fact-check switched on the result lists zero claims. If
the customer had said "USD to PKR is 280", that sentence would be detected as
an exchange-rate claim and compared with the current US dollar to rupee
rate.

If fact-check itself fails, the response says `failed` and gives a reason;
the rest of the analysis is unaffected. A bad reply from an outside service
affects only the claims in that call that ask about the same thing, such as
the same coin.

*Source:* `app/routes/process_audio.py:71-126, 274-279`;
`app/utils/factcheck_gate.py`;
`app/insights/core/factcheck/factcheck_engine.py:66-91, 179-293`;
`app/insights/core/factcheck/claim_detector.py:100-180, 385-467` (patterns and match filter; the
example wordings were run through the detector);
`app/insights/core/factcheck/comparator.py:34-35, 54-140`;
`app/insights/core/factcheck/source_clients/forex_client.py:1-10, 53-96`;
`app/insights/models/factcheck_models.py:23-39, 155`;
`app/insights/config/settings.py:164-173, 288-305` (record retention, run
caps); [DATA-FLOW.md](DATA-FLOW.md); [DECISIONS.md #48](DECISIONS.md);
commits `9aff207`, `8e51a94`, `83803b5`, `da92ae4`, `45a5b22`.

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
| `topic` | The topic label and the model's confidence. If the topic step fails, the label is "unknown" with a short fixed error note. |
| `summary` | The AI-written summary paragraph |
| `intents_summary` | How many turns had each intent |
| `flags` | Hesitation, aggression and lie-risk hints, each with speaker, time, a fixed score and a note |
| `emotion_overview` | For each speaker, the share of their text read as each emotion |
| `insights` | Everything from step 7, with reasons, evidence and a status. Empty if the step was skipped. If the insight engines hit an error, the status says `error`, the findings are blank and the warning `INSIGHTS_FAILED` is added. |
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
| Call refused | `MISSING_INPUT_AUDIO`, `AUDIO_NORMALIZATION_FAILED`, `AUDIO_NORMALIZATION_TIMEOUT`, `AUDIO_TOO_LONG`, `AUDIO_SILENT_OR_NEAR_SILENT` | The call stopped before any analysis, and the code comes back with the error |
| Audio quality | `LOW_SNR_AUDIO`, `HEAVY_NOISE_AUDIO`, `AUDIO_QUALITY_FAILED` | Noisy audio, or the quality check itself could not run |
| Speakers | `SINGLE_SPEAKER_MODE`, `MOCK_DIARIZATION_USED`, `SPEAKER_CAP_APPLIED`, `LOW_SNR_DIARIZATION_UNRELIABLE`, `DIARIZATION_FAILED_FALLBACK`, `FLAGS_LIMITED_SINGLE_SPEAKER` | Only one voice, speaker separation not done, extra voices merged, or speaker separation unreliable or unavailable |
| No words | `EMPTY_TRANSCRIPT`, `ASR_FAILED` | Speech-to-text found no words, or failed |
| Stage skipped | Codes containing `_SKIPPED_`, for example `GENDER_SKIPPED_LOW_SNR` | The stage did not run; the code says why |
| Stage failed | Any other code ending `_FAILED`, for example `TOPIC_FAILED`, `INSIGHTS_FAILED`, `PDF_FAILED` | The stage broke and its part of the response is empty; everything else continued |

*Source:* `app/pipeline/orchestrator.py:242-982` (every warning code and
the response fields); `app/insights/service.py:87-108, 313-342` (insight
error status); `app/routes/process_audio.py:71-126, 274-281`
(`fact_checks_v2`); `app/utils/factcheck_gate.py:18-21`.

---

## 4. What VoiceIQ does not do

**It is decision support, not a source of truth.** Its findings point a
reviewer to where they should look. They are not a verdict on the
conversation or the people in it.

- **Most labels come from simple rules, not from judgement.** Intents, flags,
  emotions and inconsistencies come from fixed word lists and thresholds.
  These rules misfire. Milestone M7 made the flag, intent and
  email-suggestion word rules match whole words only, so "er" no longer
  matches inside "internet" and "send a" no longer matches "send an
  engineer"; before that, both raised false hits in the example. The rules
  still see only words, not meaning, and the other suggestion agents still
  match phrases inside longer words. The lie-risk flag states that it is not
  proof of deception.
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
- **The PDF report is partial.** It leaves out the insights, the action
  suggestions and the fact-check verdicts. Characters its font cannot show,
  such as Urdu or Arabic script, print as a question mark instead of
  failing the report; the response keeps the original text.
- **Fact-check is narrow.** It recognises only exchange rates, crypto and
  share prices, weather, country capitals and (without a source) commodity
  prices. Share-price and weather claims need keys that the operator must
  supply. Each fact-check checks at most 25 claims and stops starting new
  checks after 20 seconds by default. The claim detector
  can still misread some sentences: a price someone paid in the past ("I
  bought Ethereum at 1200") is checked against today's price, and a capitalised
  abbreviation that matches a coin or share symbol can be read as a price.
  Such claims are typically reported FALSE with high confidence. Milestone
  M5 stopped the detector treating names, ages and counts as prices; the
  cases that remain are listed in [FEATURES.md](FEATURES.md) (section 4.3).
- **Scores are indicators, not probabilities.** Each score is calculated by
  fixed rules from measures such as share of talking time, pauses, questions
  and interruptions, then kept within 0–1.
- **It does not act.** Every action suggestion needs human approval, and
  VoiceIQ sends no emails and books nothing.
- **The caller waits for the whole analysis.** There is no "submit now,
  collect later" mode. By default each running copy of the service analyses
  one recording at a time and lets two more wait their turn; when that
  queue is full, a further upload is refused at once with 503 and asked to
  try again later. Other requests are still answered meanwhile. Sprint 7's
  plan moves fact-check, not the whole analysis, into a background job
  (Phase 6; see D2 in the glossary). That work is planned, not built.
- **It does not take very long recordings.** A recording over two hours
  (the default limit) is refused, not cut short.
- **It is not an archive.** Each call's files are deleted after 24 hours by
  default. Results saved in the service's database (stored insights and
  fact-check results) are deleted after 30 days by default.

A round of security and reliability fixes landed in October 2026.
[SECURITY.md](SECURITY.md) describes the security controls and, in general
terms, the known limitations.

*Source:* `app/services/flag_service.py:14-21, 64-81`;
`app/services/intent_service.py:6-51`; `app/utils/text_match.py`
(milestone M7, `dbae452`);
`app/services/emotion_service.py:17-24, 56-66`;
`app/services/gender_service.py:9-17`;
`app/services/alignment_service.py:446-455`;
`app/services/diarization_service.py:49-51, 202, 264`;
`app/agent_brain/extraction/language.py`;
`app/agent_brain/core/email_draft_agent.py:19-34, 57`;
`app/services/sentiment_service.py:91`;
`app/services/pdf_service.py:29-71`;
`app/insights/core/factcheck/factcheck_engine.py:76-89`;
`app/insights/core/factcheck/scorer.py:1-18`;
`app/insights/core/scoring_engine.py`;
`app/agent_brain/models/recommendation.py:44, 55`;
`app/routes/process_audio.py:34-57, 153-165, 234-251` (the caller waits;
a bounded queue per worker, 503 beyond it);
`app/utils/audio_utils.py:72-160` (length limit);
`app/insights/config/settings.py:113-138, 153-173, 288-305`;
`app/utils/retention.py` (deletion of old files and records);
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Completed*,
*Remaining*, *Next milestone*); [CHANGES.md](CHANGES.md) (*Security and
reliability hardening*).

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
| M1–M7 | Milestones: small fixes made one at a time while Sprint 7 work waits on Phase 0. M1 kept outbound request details out of the logs (`c1b35d9`, `cedfe96`). M2 made a bad reply from a data service produce `SOURCE_UNAVAILABLE` for that claim instead of a verdict or a crash (`9aff207`). M3 made currency-service errors say what went wrong (`dc03a44`). M4 stopped over-long numbers in a transcript from crashing the fact-check route or the audio route (`8e51a94`). M5 stopped the claim detector mistaking names, ages and counts for prices and taught it common price and currency phrasing (`1dbfac7`). M6 stopped characters outside Latin-1, the Western European character set the report font covers, from failing the PDF report; they now print as a close plain form or a question mark (`dc9caf3`). M7 made the keyword rules for flags, intents and email suggestions match whole words only, so that "er" no longer matches inside "internet" (`dbae452`). |
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
milestones in git (`c1b35d9`, `cedfe96`, `9aff207`, `dc03a44`, `8e51a94`,
`1dbfac7`, `dc9caf3`, `dbae452`) and
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) (*Next milestone*);
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
