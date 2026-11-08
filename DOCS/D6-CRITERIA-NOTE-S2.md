# D6 bake-off criteria note — signed version 2 (S2)

Status: **signed 2026-10-30 in the senior sponsor role, before any bake-off run** · For:
`DOCS/FACTCHECK-AGENT-PHASE-0.md` §1 D6 · Written: 2026-10-25; S2 drafted 2026-10-30

S2 replaces S1 ([D6-CRITERIA-NOTE-S1.md](D6-CRITERIA-NOTE-S1.md), signed 2026-10-28) before any bake-off run.
The signature is the commit that adds this file, as for S1. The private sign-off record holds the
signer's name and that commit's hash, and Annex A records the hash.

**Changes from S1.** Only section 3.3 changes. Three flags are added to the launch line, each with its reason,
and the start-up checks gain a command-line check. Every other section is S1's text unchanged. Where a section
says a value was fixed or signed "in S1", S2 keeps that value. Section 8's limits on later signed versions
apply after the first run, and S2 comes before it.

**Roles.**

- Pack guardrail (b) has the tech lead write this note and the senior sponsor sign it. Guardrail (f) does the same
  for the bake-off report (section 9).
- The project owner (sole maintainer) holds both roles.
- The note names roles only.

**What was signed.**

- Values marked **[owner]** were set by the project owner on 2026-10-25. Every value in section 12 was accepted
  as proposed, and the margin reading is the literal one (section 8).
- Pack guardrails are safeguards from the Phase 0 decision pack of 2026-10-19, which is kept privately. Each one
  adopted here is cited as such and restated in full.
- On 2026-10-19 only the D6 sign-off line, and rider (g) through A2, were signed. This note is the criteria note
  that the D6 line requires.

**References.** A section sign (§) without a document name refers to
[FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md). "The guide" is
[GOLD-SET-LABELLING-GUIDE.md](GOLD-SET-LABELLING-GUIDE.md). "The plan" is the implementation plan v1.1
(historical, not in the repository).

**What it is.** The signed D6 line says Qwen3 is not mandatory. Qwen3.5-4B may be pinned only if:

- its model card and licence are confirmed before the first bake-off run; and
- it beats Qwen3-4B by the margin in this note, which is signed before the first run.

Otherwise the smallest model that passes this note's gates is pinned. Qwen3-8B is eligible only if Qwen3-4B fails a
gate and D5's hardware fits it. Any change from the Qwen3 baseline is recorded in the signed bake-off report. The
Phase 1 pin is provisional on verdict quality.

This note fixes all of the following before any result exists:

- the candidates;
- the checks;
- the run conditions;
- the scoring, including the guide's Q11 questions and the date-anchor part of Q12;
- the gates;
- the margin;
- the decision rule;
- the report;
- the re-opening triggers.

**What it does not do.**

- It does not measure verdict quality. The gold set has no verdict labels (D3 Option A); verdicts come with D3b,
  after Phase 4.
- It sets no source-authority rules. Those are D9 (erratum W3).
- It changes no contract: `model.name` is a free string (A2, D6 rider g).
- It does not choose the production runtime. That is chosen after D5 by the tech lead and Infrastructure (U3).

---

## 1. Candidates

**The field is closed.** The candidates are exactly three:

| Candidate | Role | May be pinned when |
|---|---|---|
| `Qwen/Qwen3-4B` | Default | It passes every gate, and Qwen3.5-4B does not win under section 8 rule 3 |
| `Qwen/Qwen3.5-4B` | Challenger | Its card and licence are confirmed (section 2), it passes every gate, and it beats Qwen3-4B under the section 8 margin test |
| `Qwen/Qwen3-8B` | Third candidate | Qwen3-4B fails a gate, Qwen3.5-4B does not win, and Qwen3-8B passes every gate, including memory on this PC |

- **Model type.** Each must be the post-trained model, not `-Base`. This is checked per candidate in Annex A.
- **No other models.** No other model, of any family, size or variant, may be run for this decision or pinned.
- **Dated checkpoints.** A dated checkpoint of the default (for example `-Instruct-2507`) may be used only if the
  senior sponsor records at signing that it is "Qwen3-4B" under D6. Otherwise it is a change from the Qwen3 baseline
  and needs a new signed decision.
- **Every candidate that loads is run** for quality and timing, whatever its gate M result, so the report shows the
  full picture. **[owner]** Running a candidate never makes it pinnable outside the rules above.
- **Fallback if Qwen3.5-4B's checks fail.** It cannot be pinned under the signed D6 line. The note adopts pack
  guardrail (a): Qwen3.5-4B is still measured, for information only, if it can run.
- **Settings recorded from the winner.** Its id becomes `FACTCHECK_LLM_MODEL`. Its source revision and GGUF SHA-256
  become `FACTCHECK_LLM_REVISION` (§7, U3).

## 2. Card and licence checks (before the first run)

**Card confirmed.** The tech lead records in Annex A, for each candidate:

- the model card resolves at a pinned source revision;
- the card names the architecture and total parameter count;
- the card says the model is post-trained;
- the pinned llama.cpp build converts and runs it (section 3).

**Licence. [owner, legal role]** Signed D4 says "open-source" covers the LLM. A weight licence therefore passes only
if both hold:

- it is approved by the Open Source Initiative (for example Apache-2.0 or MIT);
- it places no field-of-use, user-count or revenue restriction on use, modification or redistribution.

The tech lead records the licence name, where it was read (the card at the pinned revision) and the date.

- Qwen3 releases are understood to be Apache-2.0. That is drafter knowledge and must be verified.
- The project owner read Qwen3.5-4B's card on 2026-10-28: Apache-2.0. The model is multimodal (a vision
  encoder; about 5B parameters on disk) and is used as a text model only. Annex A records the card and licence
  at the pinned revision.
- llama.cpp's licence (reportedly MIT) is recorded too.

## 3. Run conditions (identical for every candidate)

### 3.1 Machine

This PC only, the D5 host:

- NVIDIA GeForce RTX 5060 Laptop GPU: 8151 MiB, of which 7899 MiB was free at idle on 2026-10-25;
- 15,578 MB RAM;
- AMD Ryzen 9 8940HX;
- Windows 11 Home.

It is both the development machine and the production-class machine. How it is run:

- AC power, with one named Windows power mode and GPU profile, both recorded.
- GPU clocks, temperature and power are logged during timing.
- Before timing, Ollama is quit, and nvidia-smi confirms that no CUDA process other than `llama-server` is running.

### 3.2 Environment

`llama-server` runs in WSL2 Ubuntu, the environment the audio pipeline already uses here. Windows application
control on this PC blocks unsigned native binaries. Changing it is the owner's security decision, and it is not a
precondition of this note.

- **Build.** A Linux CUDA build of one pinned llama.cpp commit, compiled for sm_120 with CUDA 12.8 or later.
- **Model files.** GGUF files are read from the WSL ext4 filesystem, not from `/mnt/c`.
- **Network (U3, W6a).** The server and the harness run together in one Linux network namespace that has only the
  loopback interface.
  - The server binds to 127.0.0.1 inside that namespace, so it has no outbound network access while serving and is
    never reachable from outside the host.
  - Annex A records the WSL networking mode (NAT or mirrored) and the localhost-forwarding setting.
  - Pre-run checks:
    - an outbound connection from inside the namespace fails;
    - the port cannot be reached from the WSL `eth0` address;
    - the port cannot be reached from the LAN.
- **Recorded.** The WSL kernel, the CUDA version, and the `.wslconfig` memory and swap settings.
- **Tools.** The harness sends no tools and uses no function calling.

### 3.3 Launch line

The launch line is frozen and recorded in Annex A:

`llama-server -m <file> -c <ctx> -np 1 -ngl 999 -fa on -ctk <kv> -ctv <kv> -b 2048 -ub 512 --jinja --host 127.0.0.1 --port <p> -fit off --cache-ram 0 -ctxcp 0`

- Flash attention is on for every candidate.
- `<kv>` is the KV cache type from section 3.5.
- Any automatic fitting of layers or context is off (`-fit off`). The pinned build turns fitting on by default.
- The host-RAM prompt cache is off (`--cache-ram 0`). With it on, the server copies a slot's state into host RAM
  when it selects the slot, even for a request that sends `cache_prompt: false`. That would put cache work inside
  timed calls and cache memory inside gate M's RAM figure.
- Context checkpoints are off (`-ctxcp 0`). The pinned build makes them only for models whose memory cannot be
  rolled back, such as Qwen3.5-4B's hybrid layers. It never restores them when `cache_prompt` is false. With them
  on, one candidate's timed calls would include state copies and batch splits that the others' do not.
- Every timed request sends `cache_prompt: false`.

From each candidate's start-up log, the pre-run record holds:

| Item | Expected |
|---|---|
| Layers offloaded | N of N |
| Context per slot | equal to `<ctx>` |
| Graph splits | more than 2 means operations running on the CPU; recorded as part of that candidate's runtime result |
| Buffer sizes | the CUDA model, KV and compute buffers |
| Command line | the running process's command line (`/proc/<pid>/cmdline`) equals the frozen line, with the values filled in |

### 3.4 Weights and quantization **[owner]**

All three GGUF files are made from the pinned source safetensors:

1. Convert with the pinned build's `convert_hf_to_gguf.py`.
2. Quantize with `llama-quantize Q4_K_M`, with no importance matrix.
3. Record the commands.

Published third-party quantizations are not used. Downloading the bf16 source weights needs the owner's go: about
8 GB for each 4B model and about 16 GB for the 8B. Their source and SHA-256 are recorded in DATA-FLOW.md in the same
change (W6b).

### 3.5 Context and KV cache **[owner]**

- **Context.** 12,288 tokens for every candidate. That is enough for the cap sessions in section 6: an estimated 9-10k
  tokens of prompt, input and 4,096 output tokens. The harness checks that every request uses no more than 90% of the
  context. If it does not fit, the context is raised for every candidate before any run.
- **KV cache.** f16.
- **Accuracy runs** use the same context and cache type. The KV size does not change outputs.

**Memory arithmetic.** These are estimates for the owner; Annex A records the measured sizes.

| Candidate | Weights | KV cache (f16) | Buffers | Total |
|---|---|---|---|---|
| Qwen3-4B | about 2.5 GiB | 1.69 GiB (144 KiB/token at 12,288) | about 0.6-1.0 GiB | about 4.8-5.2 GiB |
| Qwen3-8B | about 4.7 GiB | 1.69 GiB (144 KiB/token at 12,288) | about 0.6-1.0 GiB | about 7.0-7.4 GiB |
| Qwen3.5-4B | from its config at the pre-run check | from its config | from its config | from its config |

- Gate M's budget is at most 7,387 MiB even with no audio pipeline. Qwen3-8B therefore probably fails gate M at f16.
- With a q8_0 KV cache for all candidates, the KV cache halves to about 0.84 GiB. Qwen3-8B would then need the audio
  pipeline to hold under about 1 GiB.
- This choice largely decides whether Qwen3-8B can ever be eligible. The owner set f16 on 2026-10-25 (section 12
  row 5), so Qwen3-8B probably fails gate M. It is still run (section 1).

### 3.6 Thinking mode **[owner]**

Off for every candidate, using each template's documented switch (to be verified per candidate).

- The pre-run check confirms that no thinking text appears, either in the content or in any separate reasoning
  field.
- A candidate that cannot turn thinking off is measured for information only and cannot be pinned.
- Chain-of-thought is never returned (§2.2).

### 3.7 Sampling **[owner]**

Deterministic and identical for every candidate:

- temperature 0;
- seed 42;
- top-k 1;
- no repetition penalty.

Output caps:

| Request | Cap (tokens) |
|---|---|
| Single-conversation extraction | 1,024 |
| Cap-session extraction | 4,096 |
| Reasoning probe | 384 |

Each model card's recommended settings are recorded but not used.

### 3.8 Structured output and retry

The deployed form is used:

- **Schema.** A constrained JSON schema in which `sourceId` and `segmentIds` are free strings, not an enum of the
  supplied ids.
- **Validation.** Pydantic validation of the §2.2 extraction shape follows.
- **One retry,** the deployed repair request: the original messages, the invalid output and the validation error
  text, at the same settings. Its wording is frozen and hashed with the prompts. A timed-out or failed attempt is
  retried with the same request, and is expected to repeat.
- **Scoring.** The scored output is the retry's, and its time counts.

**What "invalid" means.** The output fails validation. That includes:

- an empty `segmentIds` list;
- `segmentIds` that are not in the request;
- output cut off at the token cap (`finish_reason = length`).

Outputs cut off at the cap are counted and reported separately.

**Diagnostic pass.** One unconstrained pass per candidate measures raw JSON validity. It is reported but not gated.

### 3.9 Timeouts

- **Single conversations and probe calls.** 30 s per attempt (`FACTCHECK_LLM_TIMEOUT_SEC`, the provisional §7
  value). The harness's HTTP timeout is the same.
- **Cap-session calls.** These are not cut at 30 s. Their timeout is a fixed 300 s, D5's whole p95 window, which no
  run deadline can exceed. Their times are stored uncensored, and gate T compares the recorded totals with the budget.

### 3.10 Prompts

There is one prompt set, identical for every candidate and in the deployed form:

- transcript text is delimited and labelled as untrusted data (§6.6 rule 3);
- the output shape is the §2.2 extraction shape: `claims[]` with `segmentIds`, and `nonCheckable[]` with
  `reasonKind`.

Prompts are developed only on material outside the gold set (the guide's worked examples, or a separate synthetic dev
set), never on gold rows of any version. The report records which models were used during prompt development.
Prompts are frozen and hashed before the first run.

### 3.11 Input

- **Single conversations.** One v2-style request per conversation: `segmentId`, `speakerId`, `start`, `end`, `text`
  and `asrConfidence`, with language `en`.
- **Cap sessions.** Four further requests (section 6). In these, `segmentId` is re-keyed as
  `<source_id>:<segment_id>`, and `speakerId` is re-keyed per conversation, because every conversation reuses the
  same ids. The re-keyed requests are frozen and hashed.
- **Not sent.** No call date and no `setting` field, because the v2 request carries neither.

### 3.12 Data

- Gold-set v1.0 only: synthetic (U10) and English (U2).
- Before the first run, it is checked against its `MANIFEST.sha256` using LF bytes.
- Gold rows go only to the host's model server, never to a hosted model, and that includes any scoring or matching
  step (W6a; guide Q15).
- Outputs are written outside OneDrive, or with sync paused.

### 3.13 Runs

Each candidate runs everything three times.

- **Order.** Runs are interleaved: run 1 of every candidate in rotated order, then run 2, then run 3. Each run starts
  with one discarded warm-up request.
- **Which run counts.**
  - Quality metrics and gates J, C, A and S use run 1.
  - Gate T uses every session-run of all three runs.
  - Gate M uses the peak over all runs.
- **Determinism.** The share of requests whose output differs from run 1 is reported. If it is above 0 for either 4B
  model, every case-1 condition in section 8 must hold on the mean over runs and in at least two of the three runs.
- **Voided runs.** A run is void only for an infrastructure fault logged before its scores are computed. Voided runs
  are listed in the report.

## 4. Reasoning probe (timing and citation ids only)

The gold set has no evidence bundles, and the second model call per claim needs one.

**[owner]** 20 hand-built synthetic claims. Each comes with a bundle at the deployed cap:

- 5 delimited sources with stable ids (`FACTCHECK_MAX_SOURCES_PER_CLAIM`);
- each source 300 tokens long **[owner]**;
- at least one source bearing on the claim.

PHASE-0 §6.5's excerpt length is still open (U4b), so the source length is this note's value until §6.5 sets one
(section 10). The probe is drafted privately, approved by the owner and hashed before the first run. It is synthetic
and has no verdict labels.

It measures only reasoning-call latency and citation ids. With n = 20, gate C is a coarse floor.

## 5. Scoring (settles guide Q11 and the date-anchor part of Q12)

### 5.1 Matching

- **Unit.** A model claim matches a gold item when the item's `segment_id` is in the claim's `segmentIds`. Items are
  keyed by (`source_id`, `segment_id`).
- **Each row counts once.** A gold in-scope `CHECKABLE` row is found at most once. Further claims matched only to rows
  already found are dropped from both the numerator and the denominator. Raw claim counts and duplicates are reported
  for each candidate.
- **Context segments.**
  - A claim that cites at least one in-scope `CHECKABLE` row predicts `CHECKABLE` only for those rows.
  - Any other row it cites, such as the question it answers or a pronoun's antecedent, is a neutral context citation.
    That row is scored from `nonCheckable[]` alone, and it is not a leak.
  - The number of neutral context citations is reported per candidate.
- **Correction pairs and repeats.**
  - Each row is matched on its own segment.
  - Both rows of a correction pair count for recall, as the guide labels them.
  - A repeat flagged in the notes is matched once.
  - Flagged pairs are reported separately.
- **Spans.** v2 claims carry no span field, so spans are not compared, and edge-word differences never count against
  a model.
- **Date anchors (Q12).** Option (a): date anchors are ignored when claim text is compared. No reference-date field is
  added to v2, so there is no contract change and no extra signature.

### 5.2 Metrics

**Extraction**

| Metric | Definition | Use |
|---|---|---|
| Recall | Found in-scope `CHECKABLE` rows ÷ all such rows in v1.0 (95 in v0.4) | Gate A; part of F1 |
| Precision | Found rows ÷ (found rows + false-positive claims) | Gate A; part of F1 |
| F1 | Harmonic mean of recall and precision | The score in the margin test |

- **False positive.** A claim that predicts `CHECKABLE` for no in-scope `CHECKABLE` row, and cites a non-item segment
  or an in-scope non-checkable row.
- **Leak, not false positive.** A claim citing only out-of-scope rows is counted as a leak instead.

**Reported only**

- **Claim fidelity.** For each found row, every value in the gold `claim_text` appears in the model's claim. Numbers
  are written as digits with units, currencies as ISO codes, and index names normalised, as the guide does. Dates are
  ignored.
- **Per-proposition recall.** A compound row's part counts as found when a claim matched to the row passes fidelity on
  that part's values.

**Layer B routing accuracy** (gated). The share of all rows (200 in v0.4) whose prediction matches gold:

| Gold reason kind | Correct prediction |
|---|---|
| Blank | A claim that predicts `CHECKABLE` for the row under section 5.1 |
| Any other | A `nonCheckable[]` entry with that reason kind |

- An omission is wrong.
- If a segment has both a claim and a non-checkable entry, it counts as a claim only for rows the claim predicts
  `CHECKABLE` under section 5.1.
- The gold order is checkability first, then scope (guide Q1).

**Checkability F1** (reported). Binary (in-scope `CHECKABLE` against everything else), plus macro F1 over the six
classes. Too noisy to gate: the four non-checkable classes have only 15-17 rows each in v0.4. The other two classes
are blank (95 rows) and outOfScope (40).

**Leaks.** A leak is a claim that predicts `CHECKABLE` (section 5.1) for a row whose gold reason kind is not blank
(guide section 7).

- Leaks are counted per row: a row is leaked if any claim predicts `CHECKABLE` for it. They are reported over
  in-scope non-checkable rows (60) and over out-of-scope rows (45).
- High-stakes leaks are out-of-scope rows in `HEALTH`, `LEGAL` or `PERSONAL_FINANCE` (19 in v0.4), with each domain
  shown separately.

### 5.3 Failures and rows used

- **Failures.**
  - A single-conversation request that fails after its retry scores as empty output: its rows are not found and are
    omitted. The cause can be invalid output, a timeout or a server error.
  - Failed conversations, and the out-of-scope rows they held, are listed.
  - The verdict fallback to `INSUFFICIENT_EVIDENCE` never hides an extraction failure here.
- **Rows used.**
  - Gates use all rows, including low-confidence rows. High-confidence-only figures are reported beside them.
  - Recall is also reported separately for utterances with digits and with spelled numbers.
  - `deliberately_false` is never used: it is construction metadata, not truth.

## 6. Gates (all must pass for a candidate to be pinnable)

| Gate | Rule |
|---|---|
| **L — Licence and card** | The section 2 checks passed before the first run |
| **M — Memory** | See below |
| **J — Validity** | Single-conversation extraction requests in run 1: at most 3 fail on the first attempt, and at most 1 still fails after the retry. Probe requests: at most 1 of 20 fails after the retry. Timeouts and server errors count as failures. **[owner]** |
| **C — Citation ids** | On run-1 probe responses, a first attempt fails if it cites an id outside its bundle or cites none. At most 1 of 20 may fail. **[owner]** Also reported on the unconstrained pass. Unknown ids stay a hard failure in the product, whatever the model (§2.2). |
| **T — Latency at the cap** | See below |
| **A — Accuracy floors** | Extraction recall at least 0.75, extraction precision at least 0.75, and Layer B routing accuracy at least 0.70. **[owner]** |
| **S — Scope safety** | At most 2 high-stakes leaks, and an out-of-scope leak rate of at most 0.20. **[owner]** |

### Gate M — memory

- **Budget.** Idle free VRAM (7,899 MiB measured), minus the audio pipeline's VRAM, minus 512 MiB of headroom.
  **[owner]**
- **Audio pipeline's VRAM.** Measured in WSL as the rise in nvidia-smi `memory.used`, sampled at least 10 times a
  second. It is taken at the peak while the pipeline processes one representative allowed audio file, and again as
  the memory it keeps after the file finishes. The larger figure is used.
- **Model server's VRAM.** The rise in `memory.used` from server start through the longest cap-session request. It is
  cross-checked against the CUDA buffer sizes in the server log. It must fit the budget with all layers on the GPU,
  at the section 3.5 context and KV type.
- **Shared GPU memory.** It must not rise during loading or runs (Windows GPU counters). Alternatively, the owner sets
  the NVIDIA "CUDA Sysmem Fallback Policy" to "Prefer No Sysmem Fallback", and that setting is recorded.
- **RAM.** The server's peak private (anonymous) memory is at most 4 GB **[owner]**, with memory-mapped loading on for
  every candidate. The WSL VM must not swap.
- **How it is measured.** In one session, with the audio pipeline loaded and the model server running together.

### Gate T — latency at the claim cap

**Cap sessions.** There are 4 frozen sessions. Each joins whole gold conversations, in a fixed order, until it holds at
least 20 in-scope `CHECKABLE` rows. The figure of 20 is `FACTCHECK_AGENT_MAX_CLAIMS`, which is provisional and is not
the v1 key `FACTCHECK_MAX_CLAIMS`.

**What is timed.** Each session-run is the cap-session extraction call, retry included, plus 20 reasoning-probe calls
taken from the probe in a fixed order.

**Pass condition.** Every one of the 12 session-runs (all three runs) completes within the model-time budget. All of
the following must hold:

- the cap-session extraction call is valid after its retry;
- the extraction returns at least 10 claims **[owner]**;
- the total time is within the budget.

Probe-call validity is gated by J and C only. A failed probe call counts its full time, retry included, toward the
session total.

The budget is `FACTCHECK_AGENT_RUN_DEADLINE_SEC` (whole run) times the model share. The model share is fixed in this
note at 0.5 **[owner]**; the rest is reserved for Phase 4 retrieval and overheads. A cap-session failure (invalid
output, a timeout, or too few claims) fails gate T.

**Contention.** Timing runs are done with the audio pipeline stopped, so gate T measures the uncontended case. One
extra session-run per candidate is also timed while the audio pipeline processes a file. It is reported, not gated.
D5 compliance under contention is checked again in Phase 6 testing.

**Reported:**

- per-call latency (median, p95, max);
- the number of single-conversation calls over 30 s;
- the longest call with its retry (the input to U1's lease);
- session time divided by 20.

**Not measured.** Run-level time including queueing. Gate T is only the model-time part of D5's per-run budget.

### What gates A and S are for

They are sanity floors, against pinning a clearly unfit model. They are not the plan's targets.

- **Recall target.** The plan's 0.85 recall target (at least 81 of 95 rows in v0.4) is reported as met or not met. It
  stays a target until a baseline exists (plan §9).
- **System properties.** The plan's 100%-schema-valid and zero-unknown-id targets are system properties: §2.2
  invariants 2 and 4 enforce them for any model.
- **Not measured here.** Confidence bounds, failure safety and egress are not bake-off measurements.

## 7. The run deadline (a D5 input, cited, not set here)

- **Where the deadline is set.** It is the placeholder in the signed D5 line. It is filled in the Infrastructure role,
  with latency co-signed by Product, and entered in PHASE-0 §1 row D5 and in §7 `FACTCHECK_AGENT_RUN_DEADLINE_SEC`.
  It is removed from §8's open items before this note is signed.
- **Where the D10 re-run allowance is recorded.** It is not part of D5. It is recorded in the Infrastructure role,
  next to the D5 entry, as an input to U1's lease and U5c's waiting depth.
- **This note only cites both.**

**The rule.** With one job at a time:

> (W + 1) × (deadline + re-run allowance) ≤ 300 s, where W is the number of runs allowed to wait.

| Choice | Deadline + re-run | W | Model budget (share 0.5) |
|---|---|---|---|
| **(a), set in D5** | 125 s + 25 s, uses the full 300 s | 1 | 62.5 s per cap session |
| (b) | 120 s (the §7 draft's deadline) + an assumed 30 s re-run | 1 | 60 s |
| (c) | 75 s + 25 s (the D5 brief's ~100 s example) | 2 | 37.5 s |

These are planning numbers, not measurements. Section 10 says what happens when they are re-checked after Phase 1.

The owner chose (a) on 2026-10-25. It was entered in D5 on 2026-10-28, in the change that adds this note:
PHASE-0 §1 row D5, §7 and §8.

## 8. Margin and decision rule

**Score.** Extraction F1 from run 1. Where section 3.13 applies, the case-1 conditions use the mean over runs and must
also hold in at least two of the three runs. The bootstrap then averages each conversation's per-run results.

**Margin test, case 1. [owner, senior sponsor]** Qwen3.5-4B beats Qwen3-4B by the margin when it passes every gate
and all four of these hold against Qwen3-4B's scores:

1. Its extraction F1 is at least **0.03** higher (about 3 of 95 rows).
2. A paired bootstrap over the conversations gives a one-sided 95% lower bound on the F1 difference above 0. It uses
   10,000 resamples, with the seed recorded in Annex A.
3. Its Layer B routing accuracy is no more than 0.02 lower.
4. It has no more high-stakes leaks.

A smaller gap is below what one labeller and 95 rows can resolve, so the default stays.

Qwen3-4B's scores always exist, because every candidate that loads is run (section 1). This test applies whether or
not Qwen3-4B passed its gates: the literal reading of "beats Qwen3-4B by the margin".

**Reading: literal. [owner]** The owner chose the literal reading on 2026-10-25 (section 12 row 16), so case 1 is
the only margin test. An alternative reading (case 2) was considered and not adopted. Under it, if Qwen3-4B failed
a gate that Qwen3.5-4B passed, Qwen3.5-4B would have won with an extraction F1 not lower than Qwen3-4B's and no
more high-stakes leaks. The sign-off line records the literal reading.

**Decision rule.** Applied mechanically:

1. **Apply the gates.** A candidate that is ineligible under sections 1, 2 or 3.6 cannot be pinned.
2. **Qwen3-4B passes every gate.** Qwen3-4B is pinned, unless rule 3 applies.
3. **Qwen3.5-4B wins.** If Qwen3.5-4B is eligible and beats Qwen3-4B under the margin test, Qwen3.5-4B is pinned.
   Qwen3-8B is never pinned under rules 2 or 3.
4. **Qwen3-4B fails a gate.** Check in this order:
   - Qwen3.5-4B is pinned if rule 3 applies (case 1, the literal reading).
   - Otherwise Qwen3-8B is pinned, if it passes every gate.
   - Otherwise go to rule 5.
5. **No model passes every gate.** Adapted from pack guardrail (c): nothing is pinned under this note. The senior
   sponsor either:
   - (a) amends the run conditions within the limits under "Fixed in advance" below, and re-runs every candidate; or
   - (b) revisits D5 or D6 by a new signed decision.

   A model that failed gate L or gate M is never pinned. Pinning a default that failed a gate, even provisionally,
   needs a new signed D6 decision.

"Smallest" means the total parameter count on the card. The two 4B models count as one size class, and rules 2-4
settle between them.

**Fixed in advance.** Nothing in this note changes after the first run, except as follows.

- **When an amendment is allowed.** A later signed version is allowed only under rule 5(a), or to fix an
  infrastructure fault logged before scoring.
- **What it may change.** Run conditions only: the runtime build, the context, the KV type, the timeouts or the probe
  length.
- **What it may not change.** Thinking mode, and any other setting that decides eligibility. The candidate list, the
  eligibility rules, the gates and their thresholds, the model share, the score and the margin stay as signed in S1,
  because signed D6 ties the margin to the note signed before the first run.
- **Prompts and labels.** They may not be changed in response to gold-set results, unless a fresh held-out set
  replaces the gold set for the re-run.
- **Effect of an amendment.** It voids every earlier result, and every candidate is re-run.
- **Anything else** needs a new signed D6 decision.

## 9. Report

The tech lead prepares the bake-off report, and the senior sponsor signs it (pack guardrail f).

**Recorded for every number:**

- the machine and environment (WSL kernel, CUDA, networking mode);
- the GPU driver and power mode;
- the llama.cpp commit and launch line;
- the model file SHA-256 and the quantization commands;
- the context, KV type, sampling settings and thinking mode;
- the prompt and probe hashes;
- the gold-set version and its manifest values.

**Per candidate:**

- every section 5 metric;
- the gate table;
- raw and duplicate claim counts;
- neutral context citations;
- outputs cut off at the cap;
- the determinism share;
- voided runs;
- memory and latency distributions;
- the contended session-run.

**The decision:**

- the section 8 rule that produced it, under the literal margin reading;
- the winner's id, revision and SHA-256, beside the runtime of record for these numbers (llama.cpp commit and launch
  line, U3). The production runtime is chosen separately (U3);
- any change from the Qwen3 baseline (D6).

**Proposed binding values** for the §7 limits that Phase 1 measured: the per-call timeout and the U1 lease input. The
owner decides whether to bind them.

**Caveats:**

- The set is synthetic and English only.
- ASR style is unmeasured, so recall may not transfer to real calls.
- There is one labeller (intra-rater check only).
- Verdict quality is not measured.
- Urdu and Arabic are not measured (U2; N4b L4 not measured).
- No per-domain accuracy conclusions are drawn. In-scope `CHECKABLE` rows per domain in v0.4 are GENERAL 37,
  MARKET_FINANCE 34 and WEATHER 24, and leaks are reported by out-of-scope domain only.
- Nothing here bears on the safety of regulated domains.
- In a few approved rows, a market professional states a false value (the guide's realism note).

Synthetic outputs may be kept with the report. Logs hold no prompts or transcripts beyond the synthetic gold text.

## 10. Provisional pin and re-opening

The Phase 1 pin is provisional on verdict quality. That is a deliberate deviation from the plan's Phase 1 exit,
accepted with D6. The pack names the cost: a possible re-pin around Phase 4/5.

The four measurement triggers (deadline or re-run allowance, claim cap, excerpt length, production context) each
re-apply section 8 mechanically, and the result becomes the pin. That is committed here in S1, so no free
re-decision follows a result. The D3b and U2 re-openings follow their own rules, below.

**D5's run deadline or the re-run allowance changes.** Gate T is recomputed from the stored measurements, with no new
runs. Section 8 is then re-applied to every candidate. The model share is fixed in S1 and does not change.

**`FACTCHECK_AGENT_MAX_CLAIMS` changes.** The cap sessions are rebuilt at the new cap. Gate-T timing only is then
re-run for every candidate, under section 3, and section 8 is re-applied.

**§6.5 sets the excerpt length.** If it differs from section 4's 300 tokens, the probe is rebuilt at that length.
Gate-T timing and gate C are re-run for every candidate, and section 8 is re-applied.

**D3b verdict labels exist.** The pin and every other candidate that passed every Phase 1 gate are run on them:

- with the then-deployed prompts and settings, identical for all;
- and also with the Phase 1 frozen prompts, reported side by side;
- otherwise under section 3.

The report states each model's verdict macro-F1 against the plan's 0.80 target. The senior sponsor then re-confirms
the pin, as signed D6 requires. Any change of pin needs a new signed D6 decision. The incumbent's advantage from the
Phase 2-3 prompt work is an accepted cost.

**Before code-switched Urdu/English sessions are enabled.** The pin's section 5 extraction metrics are re-run on the
measured code-switched slice, and Product and the tech lead sign off on the result (U2).

**When the production context is set.** If it differs from section 3.5's, gate M is re-checked at that context.

## 11. Preconditions before the first run

1. **Gold set frozen as v1.0.** First, the owner blind re-labels 40 items, at least seven days after the 2026-10-25
   draw (guide section 13), so from 2026-11-01. Kappa on `checkability` must be at least 0.6; if it is lower, the
   guide is fixed and the rows re-labelled first. v1.0 must still meet every minimum in guide section 12, with at
   least 200 items. Its manifest hashes are recorded.
2. **Run deadline.** Done on 2026-10-28, before signing: D5's run deadline is 125 s and the re-run allowance
   25 s, recorded as in section 7.
3. **Checks recorded.** The section 2 checks are recorded in Annex A.
4. **Weights.**
   - The owner's go to download the bf16 weights.
   - The DATA-FLOW record (W6b).
   - The GGUF files built as in section 3.4.
5. **llama.cpp.** Built in WSL as in section 3.2.
   - It is started, and it answers one non-gold request.
   - The network-namespace checks pass.
   - The section 3.3 start-up log checks are recorded.
6. **Frozen and hashed:**
   - the prompts and the retry wording;
   - the probe;
   - the cap-session list and its re-keyed requests.
7. **Memory measured:** the audio pipeline's VRAM and the idle free VRAM (gate M).
8. **Q15 note.** Done on 2026-10-28: the one-line Q15 note was added to PHASE-0 §6, with the owner's sign-off: "synthetic rows drafted and
   revised with the drafting assistant are outside W6a (D3, U10); bake-off runs and any scoring or matching step use
   only the host's model server, never a hosted model."
9. **Harness location. [owner]** Set: evaluation tooling under `app/evaluation/` (ruff-linted, outside CI's mypy
   and pytest paths), not application code.
10. **Signed.** S1 was signed on 2026-10-28 and S2 on 2026-10-30, both in the senior sponsor role. Every
    **[owner]** value was set on 2026-10-25, and the margin reading is literal.

## 12. Values the owner set (2026-10-25)

The owner accepted every proposed value on 2026-10-25. The last column keeps the alternatives that were not taken.

| # | Value | Set | Alternatives not taken, or notes |
|---|---|---|---|
| 1 | Licence standard | OSI-approved, with no use restrictions | Stricter only. Accepting restricted licences would conflict with D4 |
| 2 | Default's checkpoint | `Qwen/Qwen3-4B` | A dated checkpoint, recorded at signing as "Qwen3-4B" under D6 |
| 3 | Run every candidate that loads | Yes | Run Qwen3-8B only if Qwen3-4B fails a gate |
| 4 | Quantization | Q4_K_M, self-converted, no imatrix | Q5_K_M (more VRAM) |
| 5 | Context and KV cache | 12,288; f16 | q8_0 KV for all: Qwen3-8B may then fit (section 3.5) |
| 6 | Thinking mode | Off; a candidate that cannot turn it off is information only | |
| 7 | Sampling | temperature 0, seed 42, top-k 1 | Each card's recommended settings (the 3.13 determinism rule then matters more) |
| 8 | Probe | 20 claims, 5 sources of 300 tokens each | 40 claims: more power for gate C |
| 9 | Model share of the deadline | 0.5, fixed in S1 | 0.6 or 0.4 |
| 10 | Memory headroom; RAM cap | 512 MiB; 4 GB private | |
| 11 | Validity gate | ≤3 failed first attempts, ≤1 after retry | Also gate the unconstrained raw rate |
| 12 | Citation-id gate | ≤1 of 20 | |
| 13 | Accuracy floors | recall ≥ 0.75, precision ≥ 0.75, routing accuracy ≥ 0.70 | Make the plan's 0.85 recall a gate |
| 14 | Scope safety | ≤2 high-stakes leaks; out-of-scope leak rate ≤ 0.20 | |
| 15 | Margin, case 1 | F1 +0.03 with the bootstrap bound; routing accuracy no more than 0.02 lower; no more high-stakes leaks | A larger margin favours the default more |
| 16 | Margin reading | Literal (case 1 always) | Case 2: a passing Qwen3.5-4B beats a gate-failing Qwen3-4B if its F1 is not lower and it has no more high-stakes leaks |
| 17 | Minimum claims in a cap session | 10 | |
| 18 | Optional verdict probe (D3 owner) | Not run | About 50 fixtures, verdict accuracy reported only, never gated |
| 19 | Harness location | `app/evaluation/` | A private folder |
| — | Run deadline and re-run allowance (D5 and Infrastructure inputs) | (a) 125 s + 25 s, entered in D5 on 2026-10-28 | (b) or (c) in section 7 |

## Annex A — Pre-run record (filled before the first run)

| Item | Qwen3-4B | Qwen3.5-4B | Qwen3-8B |
|---|---|---|---|
| Repository id and source revision | | | |
| Card resolves; architecture; total parameters; post-trained | | | |
| Licence, where read, date | | | |
| Conversion and quantization commands; GGUF SHA-256 | | | |
| Start-up log: layers offloaded, context per slot, graph splits, buffers | | | |
| Thinking-off switch verified | | | |

Common to all candidates:

- environment: WSL kernel, CUDA version, networking mode and forwarding;
- network-namespace check results;
- llama.cpp commit and launch line, with the context and KV type;
- GPU driver, power mode and the sysmem fallback setting;
- idle free VRAM, the audio pipeline's VRAM and the gate M budget;
- run deadline, re-run allowance and the model budget (gate T);
- prompt and retry hash, probe hash, and the cap-session list hash;
- gold-set v1.0 manifest;
- bootstrap seed;
- the signature commits: S1 `420f659`, and S2 (the commit that adds this note).

**Sign-off line.**

> D6 criteria note, signed version 2 (S2): signed before the first bake-off run, in the senior sponsor role, on
> 2026-10-30. It replaces S1 (signed 2026-10-28) and changes only section 3.3. Owner values are as set in section 12
> on 2026-10-25, and the margin reading is literal. The signature is the commit that adds this file. Names are
> kept in the private sign-off record.
