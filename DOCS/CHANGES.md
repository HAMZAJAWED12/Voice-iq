Last verified: 2026-11-10, 0bd99d0
# Changelog

What changed in VoiceIQ, backfilled from git history. Newest first, grouped
by sprint, tier or milestone. Each row is a one-line summary with the short
commit hash(es). `git show <hash>` gives the full message and diff.

This file records what each commit did. It is not a status page:

- Where Sprint 7 stands now, and the next milestone:
  [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md).
- The status of every Sprint 7 decision: the register in
  [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
  Decisions already in force: [DECISIONS.md](DECISIONS.md).
- Current security controls: [SECURITY.md](SECURITY.md). Every outbound
  call: [DATA-FLOW.md](DATA-FLOW.md). How the parts fit:
  [ARCHITECTURE.md](ARCHITECTURE.md).

## Counts

Taken from git at write time.

| Measure | Value | Command |
|---|---|---|
| Commits reachable from HEAD (`0bd99d0`) | 250 | `git rev-list --count HEAD` |
| Non-merge commits | 238 | `git rev-list --count --no-merges HEAD` |
| Merge commits | 12 (`032d822`, `49063bb`, `6e92898`, `4025199`, `c021e03`, `3a22ed2`, `a16f72c`, `c86f0fc`, `cbc3e2f`, `6534094`, `b2f32d1`, `0bd99d0`) | `git rev-list --count --merges HEAD` |
| Commits on `origin/main` (`0bd99d0`) | 250 | `git rev-list --count origin/main` |
| Commits merged from `sprint7/doc-sync` by pull request #1 | 72 | `git rev-list --count 8fd3972..aa453f0` |
| Commits merged from `sprint7/phase0-decisions` by pull request #2 | 2 | `git rev-list --count 49063bb..415dd88` |
| Commits merged from `sprint7/diarization-fix` by pull request #3 | 2 | `git rev-list --count 6e92898..378d748` |
| Commits merged from `sprint7/phase1-gold-set` by pull request #4 | 4 | `git rev-list --count 6e92898..d7071b2` |
| Commits merged from `sprint7/phase1-d5-deadline` by pull request #5 | 2 | `git rev-list --count c021e03..ed6792c` |
| Commits merged from `sprint7/phase1-bakeoff-weights` by pull request #6 | 1 | `git rev-list --count c021e03..be42d0d` |
| Commits merged from `sprint7/diarization-no-login` by pull request #7 | 2 | `git rev-list --count 4025199..551fc85` |
| Commits merged from `sprint7/phase1-harness` by pull request #8 | 10 | `git rev-list --count c86f0fc..247d8a1` |
| Commits merged from `sprint7/phase1-harness` by pull request #9 | 1 | `git rev-list --count 6534094..8a62a37` |
| Commits merged from `sprint7/phase1-harness` by pull request #10 | 3 | `git rev-list --count b2f32d1..a97679c` |
| First commit | `f21300a`, 2026-02-10 | `git log --reverse --format="%h %ad" --date=short` |

Each of the 250 commits reachable from HEAD is listed in exactly one row
below; a few hashes also appear elsewhere as cross-references. Small
commits of the same kind (wave-completion notes, a batch of stage
extractions) share a row. Sections are ordered by their most recent
commit. Dates are author dates.

| Section | Dates | Commits |
|---|---|---|
| [D6 bake-off harness on `sprint7/phase1-harness`](#d6-bake-off-harness-on-sprint7phase1-harness) | 2026-11-04 to 2026-11-10 | 17 |
| [Diarization login removal on `sprint7/diarization-no-login`](#diarization-login-removal-on-sprint7diarization-no-login) | 2026-10-24 to 2026-11-09 | 3 |
| [Bake-off model-weight record on `sprint7/phase1-bakeoff-weights`](#bake-off-model-weight-record-on-sprint7phase1-bakeoff-weights) | 2026-10-30 to 2026-11-09 | 2 |
| [D5 run deadline and D6 criteria note on `sprint7/phase1-d5-deadline`](#d5-run-deadline-and-d6-criteria-note-on-sprint7phase1-d5-deadline) | 2026-10-28 to 2026-11-09 | 3 |
| [Gold set on `sprint7/phase1-gold-set`](#gold-set-on-sprint7phase1-gold-set) | 2026-10-23 to 2026-10-25 | 5 |
| [Diarization fix on `sprint7/diarization-fix`](#diarization-fix-on-sprint7diarization-fix) | 2026-10-24 | 3 |
| [Phase 0 sign-off on `sprint7/phase0-decisions`](#phase-0-sign-off-on-sprint7phase0-decisions) | 2026-10-19 to 2026-10-20 | 3 |
| [Sprint 7 on `sprint7/doc-sync`](#sprint-7-on-sprint7doc-sync) | 2026-10-11 to 2026-10-19 | 73 |
| [Sprint 7 Phase 0 and prerequisites](#sprint-7-phase-0-and-prerequisites) | 2026-09-04 to 2026-09-20 | 7 |
| [N4b multilingual and L5 pipeline wiring](#n4b-multilingual-and-l5-pipeline-wiring) | 2026-09-19 to 2026-09-20 | 10 |
| [Tier 3 Wave E](#tier-3-wave-e) | 2026-06-21 to 2026-09-19 | 34 |
| [Tier N](#tier-n) | 2026-07-25 | 12 |
| [Tier S](#tier-s) | 2026-07-25 | 14 |
| [Sprint 6: Agent Brain](#sprint-6-agent-brain) | 2026-06-21 to 2026-06-22 | 15 |
| [Tier 3 Waves A to D](#tier-3-waves-a-to-d) | 2026-06-14 to 2026-06-21 | 13 |
| [Tier 2](#tier-2) | 2026-06-01 to 2026-06-14 | 7 |
| [Tier 1](#tier-1) | 2026-05-20 to 2026-05-23 | 6 |
| [Tooling, first CI and Docker](#tooling-first-ci-and-docker) | 2026-04-25 to 2026-05-20 | 12 |
| [Sprint 5: fact-check engine](#sprint-5-fact-check-engine) | 2026-05-03 | 2 |
| [Initial code and Sprints 1 to 4](#initial-code-and-sprints-1-to-4) | 2026-02-10 to 2026-04-25 | 9 |

## D6 bake-off harness on `sprint7/phase1-harness`

17 commits, 2026-11-04 to 2026-11-10: 10 from a branch cut from `main`
at `c021e03` (three harness milestones, a merge of `main` into the
branch, three docs commits and three commits that correct stale notes),
the merge commit of pull request #8 (`6534094`), which merged them
into `main` on 2026-11-09, and 6 later commits: 4 from the branch and
the merge commits of pull requests #9 (`b2f32d1`) and #10 (`0bd99d0`).
Pull request #9 merged the first, a docs commit, into `main` on
2026-11-09. Pull request #10 merged the other 3 on 2026-11-10: harness
milestone H4a, a docs commit and a commit that makes the harness
messages cite the signed D6 note. The harness is Phase 1
evaluation tooling in `app/evaluation/bakeoff/`, with its tests in
`app/evaluation/tests/`, outside CI's pytest and mypy paths and linted
by CI's ruff ([D6-CRITERIA-NOTE-S2.md](D6-CRITERIA-NOTE-S2.md) section
11 item 9).

| Date | Change | Commit |
|---|---|---|
| 2026-11-04 | Harness H1, the scoring core. `gold.py` loads a gold-set version and checks its SHA-256 manifest and that `items.csv` equals `items.jsonl`. `extraction.py` parses one extraction response strictly. `scoring.py` matches rows and computes recall, precision, F1, Layer B routing, checkability F1, leaks, and gates A and S, on exact fractions. `stats.py` holds the paired bootstrap and the three-run margin test. Nothing calls a model or the network. 548 tests on synthetic fixtures. Readings the note does not settle are marked NEEDS-OWNER in the code (AMB-1 to AMB-4). | `fb4a5d8` |
| 2026-11-05 | Harness H2, the model-server client. `schemas.py` holds the constrained schemas with pinned hashes and `canonical_json`. `probe.py` parses one reasoning-probe output strictly. `client.py` builds request bodies, accepts only a base URL of `http://127.0.0.1:<port>`, makes at most two attempts (the repair or same-request retry), and applies the 30 s and 300 s timeouts and the 90 % context check. No prompt text. The tests use `httpx.MockTransport` with sockets refused: 1561 new passing tests and 11 strict xfails, which pin a record rule marked NEEDS-OWNER(AMB-52) in `client.py`. NEEDS-OWNER: AMB-26 to AMB-30. | `91d8289` |
| 2026-11-08 | Harness H3, the runner and its records. `inputs.py` builds the frozen requests and their hashes, only from gold v1.0. `launch.py` holds the frozen launch line and the server's start-up checks. `records.py` keeps an append-only run directory outside OneDrive. `runner.py` runs the pre-run checks and three interleaved runs, each starting with a discarded warm-up, with fault handling and voids. Records are stored as ASCII-escaped JSON (AMB-52, option (a)), so H2's 11 strict xfails now pass and the NEEDS-OWNER(AMB-52) marker is gone. The tests use fake processes and `httpx.MockTransport`, with sockets and process starts refused: 1329 new passing tests, plus 3 that skip on a host without symlinks or trailing-space names; 3449 pass in all. NEEDS-OWNER: AMB-66 and AMB-69. Before the commit, one local end-to-end pass on synthetic inputs, in a loopback-only network namespace, drove the pinned server through the runner. | `cf874ee` |
| 2026-11-09 | `main` at `c86f0fc` is merged into the branch, bringing in pull requests #5 to #7. No conflicts. | `cbc3e2f` |
| 2026-11-09 | The status doc's branch row lists pull requests #4 to #7 with their merge commits, and WORKFLOW.md names `sprint7/phase1-harness` as the current branch and in the push rule. Docs only. | `911ddb1` |
| 2026-11-09 | The status doc records Phase 1 as in progress: the gold set up to v0.4, D5's run deadline and the D6 criteria note S1 and S2, the model-weight record and harness H1 to H3. This file gains sections for the gold set, the D5 deadline and D6 notes, the weight record and the harness, with merge rows for pull requests #4 to #7 and counts at `911ddb1`. ARCHITECTURE.md describes the harness and the gold-set data, and CONTEXT, EXPLANATION, SYSTEM, WORKFLOW and README no longer say Phase 1 has not started. Docs only. | `86fd358` |
| 2026-11-09 | CLAUDE.md drops 79 lines that the repository already records (the layout tree, the sprint table and the resolved known issues) and says Phase 1 is in progress. No rule is removed. Docs only. | `0ea4ddd` |
| 2026-11-09 | `stats.py` gives the "passes every gate" part and decision rules 1 to 5 to H4, not H3, and the message that `scoring.py`'s two claim-fidelity stubs raise names open point AMB-4 instead of a bake-off spec outside the repository. Docstrings and one error message. | `3059a59` |
| 2026-11-09 | Five more stale notes in `scoring.py`, `stats.py`, `extraction.py`, `gold.py` and `test_extraction.py` now name the right milestone or open point. Docstrings and comments only. | `962e0da` |
| 2026-11-09 | `client.py`'s notes on `/props` now say what H3 uses it for: the ready wait, the launch checks and the launch records, not the pre-run record. Docstrings only. | `247d8a1` |
| 2026-11-09 | Pull request #8 merges `sprint7/phase1-harness` into `main` (merge commit; the tree equals `247d8a1`). | `6534094` |
| 2026-11-09 | The docs record pull request #8 and the harness on `main` at `6534094`: the status doc's branch row, Completed and In progress, the merge history and timeline in WORKFLOW.md and CONTEXT.md, and ARCHITECTURE.md, which also corrects two older statements. This file takes its counts at `6534094` and gains rows for `86fd358` to `247d8a1` and the merge. References to the sprint table that `0ea4ddd` removed from CLAUDE.md now say so (CONTEXT.md, CHANGES.md and SECURITY-HARDENING-PLAN.md). Docs only. | `8a62a37` |
| 2026-11-09 | Pull request #9 merges `sprint7/phase1-harness` into `main` (merge commit; the tree equals `8a62a37`). | `b2f32d1` |
| 2026-11-09 | Harness H4a, scoring a stored run directory and writing its report. `evidence.py` holds the machine-evidence values that gate M reads, with no file access. `gates.py` holds the integrity checks on the stored records, the schedule end, the units to mark, gates L, J, C, T and M beside H1's A and S, and the reported-only blocks. `decision.py` applies the note's section 8 case-1 margin on the literal reading, with the section 3.13 three-run variant, then pin rules 1 to 5 with each step recorded, and gives the `FACTCHECK_LLM_MODEL` and `FACTCHECK_LLM_REVISION` values of a pin. `report.py` writes the body of `report-<n>.json`, the report of record, as one ASCII JSON line with exact fractions. It also holds the section 9 caveats and a fixed Markdown placeholder until H5 renders the Markdown, and refuses any configured path or home-directory form in the body. `entry.py` holds the report and void commands as the Python function `entry.main(argv)`; there is no command-line wiring yet. `records.py` and `launch.py` gain the hooks H4 needs; `runner.py` changes a docstring only. H4a loads no machine evidence, so gate M never passes, no candidate is pinnable and a scored report pins nothing (rule 5); a run to be scored that holds an attempt's host record is refused. No model or server has run through this code. The tests use synthetic data, with sockets and process starts refused: 983 new passing tests, plus the same 3 skips; 4432 pass in all. NEEDS-OWNER: AMB-106, AMB-116, AMB-129, AMB-130, AMB-134 to AMB-137 and AMB-139. | `ad6b79e` |
| 2026-11-10 | The docs record harness H4a and pull request #9. The status doc's branch row names `main` at `b2f32d1` and pull request #9; the doc gains H4a under Completed with its test evidence and H4a's NEEDS-OWNER markers, and records the split of H4 into H4a and H4b in In progress, Remaining and Next milestone, with H4b next. This file takes its counts at `ad6b79e` and gains a counts row for pull request #9 and rows for `8a62a37` and `ad6b79e`. ARCHITECTURE.md lists the H4a modules and their I/O rules; WORKFLOW.md, CONTEXT.md, EXPLANATION.md, SYSTEM.md and CLAUDE.md are brought up to date. Docs only. | `39e51d3` |
| 2026-11-10 | Twelve operator messages of the H4a report and void commands, and one note in every scored report body, cited rule ids of a milestone spec outside the repository. Eleven now cite sections of [D6-CRITERIA-NOTE-S2.md](D6-CRITERIA-NOTE-S2.md): in `gates.py` the gate M messages cite section 6 and the re-opening messages section 10; in `report.py` the re-run allowance note cites sections 6 (gate T) and 10 and the path refusal section 9; in `entry.py` the void detail refusal cites section 9. The two operator-value pattern messages drop the citation. A new check in `test_hygiene.py` fails when a string a harness module uses at run time holds such an id. Only string text changes in the three modules. 24 new passing tests, plus the same 3 skips; 4456 pass in all. | `a97679c` |
| 2026-11-10 | Pull request #10 merges `sprint7/phase1-harness` into `main` (merge commit; the tree equals `a97679c`). | `0bd99d0` |

## Diarization login removal on `sprint7/diarization-no-login`

3 commits, 2026-10-24 to 2026-11-09 (2 from the branch plus the merge
commit). Pull request #7 merged them into `main` on 2026-11-09.

| Date | Change | Commit |
|---|---|---|
| 2026-10-24 | The diarization service no longer calls `huggingface_hub.login()` on each cold load: the token still goes to pyannote with the load, nothing writes it to disk, and with the models cached diarization loads offline (`HF_HUB_OFFLINE=1`). 3 new tests. | `670b4de` |
| 2026-10-24 | The login removal is recorded and the test counts refreshed. Docs only. | `551fc85` |
| 2026-11-09 | Pull request #7 merges `sprint7/diarization-no-login` into `main` (merge commit). | `c86f0fc` |

## Bake-off model-weight record on `sprint7/phase1-bakeoff-weights`

2 commits, 2026-10-30 to 2026-11-09 (1 from the branch plus the merge
commit). Pull request #6 merged them into `main` on 2026-11-09.

| Date | Change | Commit |
|---|---|---|
| 2026-10-30 | DATA-FLOW.md records the bake-off model-weight source (W6b entry 4) in a summary row and a new section 4. The downloads are manual and happen at provisioning time only, over HTTPS from huggingface.co, with no call content and no token. Every file of the three candidates is pinned by revision and SHA-256 (42 files). The model server, llama.cpp `b11936`, serves on loopback only. Docs only. | `be42d0d` |
| 2026-11-09 | Pull request #6 merges `sprint7/phase1-bakeoff-weights` into `main` (merge commit). | `a16f72c` |

## D5 run deadline and D6 criteria note on `sprint7/phase1-d5-deadline`

3 commits, 2026-10-28 to 2026-11-09 (2 from the branch plus the merge
commit). Pull request #5 merged them into `main` on 2026-11-09.

| Date | Change | Commit |
|---|---|---|
| 2026-10-28 | PHASE-0 §1 and §7 record D5's run deadline: 125 s, with a 25 s D10 re-run allowance. [D6-CRITERIA-NOTE-S1.md](D6-CRITERIA-NOTE-S1.md) is added. It fixes the candidates, the run conditions, the scoring, the gates, the margin and the decision rule before any result exists. PHASE-0 §6 gains the gold-set guide's Q15 note. DECISIONS #54. Docs only. | `420f659` |
| 2026-10-30 | [D6-CRITERIA-NOTE-S2.md](D6-CRITERIA-NOTE-S2.md) is S1 with the section 3.3 launch line changed (`-fit off`, `--cache-ram 0`, `-ctxcp 0`) and a start-up check of the running server's command line; nothing else changes. PHASE-0 §1 and §8 point to S2. DECISIONS #55. Docs only. | `ed6792c` |
| 2026-11-09 | Pull request #5 merges `sprint7/phase1-d5-deadline` into `main` (merge commit; the tree equals `ed6792c`). | `3a22ed2` |

## Gold set on `sprint7/phase1-gold-set`

5 commits, 2026-10-23 to 2026-10-25 (4 from the branch plus the merge
commit). Pull request #4 merged them into `main` on 2026-10-25. Data
preparation only; no code changes. An AI assistant drafted every item and
the project owner checked each one. The data is in `app/evaluation/gold/`;
the rules are in [GOLD-SET-LABELLING-GUIDE.md](GOLD-SET-LABELLING-GUIDE.md).

| Date | Change | Commit |
|---|---|---|
| 2026-10-23 | The labelling guide v0.4 (D3 Option A: Layers A and B, English, synthetic, no verdict labels; rulings G1 to G10) and the calibration batch, set v0.1: 20 synthetic conversations and 65 items with a SHA-256 manifest. `app/evaluation/gold/README.md` marks the set as synthetic, unverified, possibly deliberately false and not advice. 00-index.md lists the guide. | `ab14378` |
| 2026-10-23 | Guide v0.5 (rulings G11 to G23) and set v0.2, batches 01 and 02: 35 conversations, 116 items. Three batch 01 false values move closer to their range (G22). | `1652a57` |
| 2026-10-24 | Guide v0.6 (rulings G24 to G31) and set v0.3, batches 01 to 03: 53 conversations, 166 items. | `50a7d25` |
| 2026-10-25 | Guide v0.7 (rulings G32 to G45) and set v0.4, batches 01 to 04: 63 conversations and 200 items, meeting every target and minimum in guide section 12. The re-check changed nine approved items. | `d7071b2` |
| 2026-10-25 | Pull request #4 merges `sprint7/phase1-gold-set` into `main` (merge commit). | `c021e03` |

## Diarization fix on `sprint7/diarization-fix`

3 commits, 2026-10-24 (2 from the branch plus the merge commit). Pull
request #3 merged them into `main` on 2026-10-24.

| Date | Change | Commit |
|---|---|---|
| 2026-10-24 | Diarization loads `pyannote/speaker-diarization-3.1` with PyTorch's weights-only loader on: the six classes its checkpoints store are allowed by name. `requirements.txt` declares matplotlib, which pyannote.audio 3.3.2 imports without declaring. A run whose pipeline never loaded carries `DIARIZATION_UNAVAILABLE`, and the mock reads the duration from the file header instead of loading the audio. 10 new tests (DECISIONS #53). | `9887e0b` |
| 2026-10-24 | The diarization fix is recorded and the test counts refreshed. Docs only. | `378d748` |
| 2026-10-24 | Pull request #3 merges `sprint7/diarization-fix` into `main` (merge commit; the tree equals `378d748`). | `4025199` |

## Phase 0 sign-off on `sprint7/phase0-decisions`

3 commits, 2026-10-19 to 2026-10-20 (2 from the branch plus the merge
commit). Pull request #2 merged them into `main` on 2026-10-20.

| Date | Change | Commit |
|---|---|---|
| 2026-10-19 | The Phase 0 sign-off is recorded; Phase 1 may start. Docs only. | `e4398b1` |
| 2026-10-20 | The knowledge docs are brought up to date with the merge and Phase 0. Docs only. | `415dd88` |
| 2026-10-20 | Pull request #2 merges `sprint7/phase0-decisions` into `main` (merge commit; the tree equals `415dd88`). | `6e92898` |

## Sprint 7 on `sprint7/doc-sync`

73 commits, 2026-10-11 to 2026-10-19 (72 from the branch plus the merge
commit): a documentation sync, prerequisite
fixes that need no pending decision (one per milestone), security and
reliability fixes, the owner-decided changes that followed them, and the
fix round after the pull-request review. Pull request #1 merged them into
`main` with a merge commit on 2026-10-19 (`49063bb`). Why
the work is shaped this way: "In progress" in the
[status doc](FACT-CHECK-AGENT-STATUS.md#in-progress).

### Pull-request review fix round

The owner approved these on 2026-10-18 after two independent reviews of the
pull request. Each code fix is its own commit with its own tests.

| Date | Change | Commit |
|---|---|---|
| 2026-10-19 | Whole-word rules list the forms they would otherwise miss: email triggers "send an", "send/share/forward these, those, them"; flags "umm", "uhh", "erm", "ahh", "crappy", "stupidity", "idiots", "hates", "hated"; intents "apologized", "upsetting". The email title drops a leading article only as a whole word. "Send an engineer" is an email suggestion too, accepted by the owner (DECISIONS #32). | `ee2e15b` |
| 2026-10-19 | Fact-check provider requests ask for uncompressed replies; a reply in any other content encoding is refused before its body is read, so the 1 MiB cap counts the bytes that arrive. | `efdd1a9` |
| 2026-10-19 | Doc claims brought in line with the code (fact-check egress, when the process-audio 503 and 429 come, the run deadline, the reply cap, the callback replay check, deployment requirements); the round recorded. Docs and comments only. | `aa453f0` |
| 2026-10-19 | The test suite never reads `.env` and uses a temporary database and job folder (`app/conftest.py`). Test-only. | `8563572` |

### Merge into `main`

| Date | Change | Commit |
|---|---|---|
| 2026-10-19 | Pull request #1 merges `sprint7/doc-sync` into `main` (merge commit; the tree equals `aa453f0`). | `49063bb` |

### Milestones M1 to M7

| Milestone | Date | Change | Commit |
|---|---|---|---|
| M7 | 2026-10-17 | Keyword rules match whole words only: the flag rules, the intent rules and the email-draft triggers no longer match a keyword inside a longer word ("um" in "number", "send a" in "send all"). New helper `app/utils/text_match.py`; the other Agent Brain agents keep substring matching; no vocabulary changed (forms added later in `ee2e15b`). 49 new tests. | `dbae452` |
| M6 | 2026-10-17 | Text outside Latin-1 no longer fails the PDF: common punctuation maps to Latin-1, anything else prints as "?" (`app/services/pdf_service.py`, `to_latin1`). 6 new tests. Also listed under hardening below. | see below |
| M5 | 2026-10-16 | The claim detector stops reading names, ages and counts as prices. A crypto, stock or commodity number followed by what it counts or measures, or by another currency, is dropped; a subject that is also a name or an everyday word, or a stock ticker not in capitals, needs price context (`_keep_match`, `app/insights/core/factcheck/claim_detector.py:385-467`). "1 USD = 280 PKR" is read as a rate; "95k", "24 hundred" and "1.2 million" scale the value. Every detector pattern scans in linear time. No v1 model change. 423 new tests. | `1dbfac7` |
| M4 | 2026-10-16 | Over-long numbers in a transcript no longer cause a 500 on either endpoint. The claim detector drops a claim whose number has more than 15 digits (`MAX_VALUE_DIGITS`, now `app/insights/core/factcheck/claim_detector.py:193`). `/v1/process-audio` checks the fact-check payload as strict JSON before saving or returning it, and falls back to the failed stub (now `app/routes/process_audio.py:101-104`). 35 new tests. | `8e51a94` |
| Tooling | 2026-10-16 | The trailing-whitespace, end-of-file-fixer and check-merge-conflict hooks run through `python -m` in a `repo: local` block of `.pre-commit-config.yaml`, because Windows Application Control blocks the generated `.exe` launchers on some machines. Same hooks, same behaviour. | `7b25b3f` |
| M3 | 2026-10-16 | exchangerate.host error replies are named instead of failing silently. The provider now requires an access key and none is configured, so currency-rate claims end as `SOURCE_UNAVAILABLE` with that reason; the log carries only the provider's error code and type. Provider unchanged. 11 new tests. | `dc03a44` |
| M2 | 2026-10-11 | Bad provider replies (non-finite numbers, JSON that is not an object or cannot be decoded, wrong nested types, over-deep metadata) end that one claim as `SOURCE_UNAVAILABLE`, never a verdict or a 500. The engine isolates each claim. No v1 model change. Tests in `app/insights/tests/test_factcheck_source_replies.py`. | `9aff207` |
| M1 | 2026-10-11 | Outbound request URLs kept out of the logs: `setup_logging()` pins the `httpx` and `httpcore` loggers to WARNING (`app/utils/logger.py`). Follow-up: a failed Java callback logs the exception class and HTTP status only. Tests in `app/insights/tests/test_logging_hygiene.py`. | `c1b35d9`, `cedfe96` |

Phase 0 was signed on 2026-10-19, and Phase 1 followed. Where it stands
now: [Next milestone](FACT-CHECK-AGENT-STATUS.md#next-milestone).

### Status-document updates

| Date | Change | Commit |
|---|---|---|
| 2026-10-18 | WORKFLOW.md says where test counts are recorded; WORKFLOW.md and CLAUDE.md name only CLAUDE.md as holding the repository URL. | `a2f3105` |
| 2026-10-18 | WORKFLOW.md branch rows match the pushed branch. | `99006c0` |
| 2026-10-18 | WORKFLOW.md says where each check ran: local, reduced local and CI. | `16654b5` |
| 2026-10-17 | M7 and the owner-decided changes recorded; test counts refreshed. | `2966527` |
| 2026-10-17 | M6 and the security and reliability fixes recorded; M7 promoted; test counts refreshed. | `42382db` |
| 2026-10-16 | Owner decisions recorded: M5 choices accepted, no new detector heuristics until Phase 3, size rule; M6 and the old-rows fix approved; reporting steps that work either way. | `43a9c1b` |
| 2026-10-16 | M5 recorded as done; M6 promoted; detector limits documented; test counts refreshed. | `10b0039` |
| 2026-10-16 | M4 recorded as done; M5 promoted to next milestone; test counts refreshed. | `793e02e` |
| 2026-10-16 | M3 recorded; the forex-provider question and its options written down; the over-long-number case promoted to M4. DATA-FLOW.md: exchangerate.host needs a key and none is configured. | `2350738` |
| 2026-10-11 | M2 recorded as done; M3 promoted; test counts refreshed. | `b370390` |
| 2026-10-11 | The callback follow-up (`cedfe96`) added to the M1 record. | `0aec936` |
| 2026-10-11 | M1 recorded as done; M2 promoted. DATA-FLOW.md notes that request URLs are not logged. | `b1054be` |

### Security and reliability hardening

Fixes from a security and reliability review (its detailed list is tracked
privately), plus M6 and the approved old-rows fix, which landed in the same
run. Each code fix is its own commit with its own regression tests.

| Date | Change | Commit |
|---|---|---|
| 2026-10-17 | `purge_expired` counts only job directories that were actually removed and logs the rest. | `3bf7506` |
| 2026-10-17 | Agent Brain title comparison checks cheap upper bounds before the full ratio; verdicts unchanged. | `98a319a` |
| 2026-10-17 | CI verifies the gitleaks archive against the release checksums before installing it. | `c661bad` |
| 2026-10-17 | ffmpeg runs with `-nostdin`, and a failed run deletes its partial WAV. | `5696e32` |
| 2026-10-17 | The SQLite parent directory is created from SQLAlchemy's parsed URL, so absolute and driver-qualified URLs work. | `718d763` |
| 2026-10-17 | Fact-check provider replies are read up to 1 MiB of decoded body; a subject whose fetch failed is fetched once per run. | `83803b5` |
| 2026-10-17 | Fixed public messages for a failed insight run and a failed fact-check enrichment; logs carry the exception class only. | `0011dd7` |
| 2026-10-17 | The fact-check repository returns stored non-finite numbers as null (the approved old-rows fix). | `a712146` |
| 2026-10-17 | Agent Brain keeps one review per fact-check claim (the Agent Brain dedup prerequisite). | `8103360` |
| 2026-10-17 | M6: text outside Latin-1 no longer fails the PDF. | `dc9caf3` |
| 2026-10-17 | The staging recipe in DOCS/08-deployment.md sets an API key (completes `702d23d`). | `395f6ff` |
| 2026-10-16 | Rate-limit buckets use only accepted API keys, else the client IP. | `702d23d` |
| 2026-10-16 | Insight routes and models accept only finite numbers. | `93dda14` |
| 2026-10-16 | The fact-check evidence cache is safe across threads. | `1d967c5` |
| 2026-10-16 | `/v1/process-audio` runs the pipeline in a worker thread, one run at a time per worker (the event-loop part of the process-audio concurrency prerequisite). | `2bb6593` |
| 2026-10-16 | API key and declared size are checked before a request body is read; the bytes that arrive are counted. | `ff874de` |

### Owner-decided security and reliability changes

**Insight scores change for processed audio.** From `98730c3`, pipeline
sentiment reaches the insight engines as positivity instead of the model's
confidence in its label, so scores, sentiment-shift markers and summaries
built from `/v1/process-audio` differ from earlier runs on the same audio.
`/v1/insights` callers that send their own scores are unaffected.

The project owner decided these on 2026-10-17. Each is its own commit with
its own tests; they are DECISIONS #34 to #49 (#33 was recorded without code).

| Date | Change | Commit |
|---|---|---|
| 2026-10-17 | Currency rates from open.er-api.com: keyless, only the base currency is sent, each base fetched at most hourly, attribution on every evidence record. DATA-FLOW.md and the PHASE-0 §6.4 source policy updated in the same commit. | `45a5b22` |
| 2026-10-17 | `VOICEIQ_LOG_LEVEL` is applied at startup (`httpx` and `httpcore` stay at WARNING); `VOICEIQ_THRESHOLD_PROFILE` is marked not implemented and logs a startup warning when set. | `e25a3b2` |
| 2026-10-17 | `docker-compose.yml` caps the container at 2 CPUs and 1 GB; TLS terminates at the ingress (SECURITY.md). | `fb56d7d` |
| 2026-10-17 | `GET /readyz`: database reachable and, on the full service, ffmpeg present; 503 naming the failed check otherwise. `/healthz` unchanged. | `660558c` |
| 2026-10-17 | A run that used mock diarization carries `MOCK_DIARIZATION_USED`; the two-speaker default is unchanged. | `2721a73` |
| 2026-10-17 | Pipeline sentiment reaches the insight engines as positivity (see the note above). | `98730c3` |
| 2026-10-17 | A stage whose package cannot be imported fails softly with its `*_FAILED` warning instead of the whole request. | `0f9c4c1` |
| 2026-10-17 | An insight error result marks the pipeline stage failed, adds `INSIGHTS_FAILED` and skips the Agent Brain. | `c53c71d` |
| 2026-10-17 | An error result never replaces a stored ok or warning insight record. | `f8bc3ed` |
| 2026-10-17 | Insight and fact-check records are deleted after `VOICEIQ_DB_RETENTION_DAYS` (30); the retention sweep moved to the app lifespans, at startup and hourly. | `cbdafb4` |
| 2026-10-17 | A fact-check run checks at most 25 claims and starts no check after 20 s; the rest are counted as skipped. | `da92ae4` |
| 2026-10-17 | Insight writes 60/minute and reads 120/minute per caller, each route group with its own counter; `GET /v1/insights/` takes `limit` and `offset`. | `e802fcd` |
| 2026-10-17 | Audio longer than `VOICEIQ_MAX_AUDIO_SECONDS` (7200) gets 413 `AUDIO_TOO_LONG`: ffprobe before decoding, an ffmpeg hard stop, never truncated. A fifth hard-fail gate. | `6c17c72` |
| 2026-10-17 | `/v1/process-audio` admits one running and two waiting uploads per worker by default, then answers 503 with `Retry-After: 30`. | `948c49d` |
| 2026-10-17 | Staging fails closed without API keys; compose publishes the port on loopback only. | `d7f9075` |
| 2026-10-17 | A failed topic step returns a fixed message; the exception text stays in the job's status file. | `dc97a84` |

### Housekeeping

| Date | Change | Commit |
|---|---|---|
| 2026-10-17 | CI's mypy gate also covers `app/utils/` and `app/security/`; WORKFLOW.md and SYSTEM.md say so. | `6aa8990` |
| 2026-10-17 | Code comments corrected for the fifth hard-fail gate and `/readyz`. No code change. | `5095cde` |
| 2026-10-17 | A test comment describes the callback trace id accurately. No code change. | `427feed` |
| 2026-10-16 | Stale code comments corrected (payload cap, callback trace id, rate-limit storage); notes about old git history reworded; `.git-blame-ignore-revs` points at the current hash of the bulk-format commit; DECISIONS entry 17 marked as followed in practice with formal approval pending. No code change. | `8f09739` |

### Project knowledge set

Both on 2026-10-16.

| Change | Commit |
|---|---|
| DOCS/00-index.md lists the knowledge set; CLAUDE.md links it and adds the rule for which docs each milestone updates; the status doc points to it. | `50254f6` |
| Nine knowledge docs added: DECISIONS, WORKFLOW, CHANGES, EXPLANATION, FEATURES, SECURITY, ARCHITECTURE, SYSTEM and CONTEXT, each checked against code, tests and git. | `16a7c1e` |

### Documentation sync

All on 2026-10-11.

| Change | Commit |
|---|---|
| Leftover stale statements fixed in `08-deployment.md`, `.env.example`, JAVA-CALLBACK-V2.md, the N4b and Tier N plans (stale line references) and orchestrator docstrings. No code change. | `3d970bf` |
| DATA-FLOW.md: model-download and fact-check egress facts corrected. | `6702ed6` |
| `.env.example`: settings it was missing added as placeholders with their code defaults. | `25925b0` |
| README: boot, egress, test-count and CI claims corrected; status doc linked. | `9a997c5` |
| CLAUDE.md: stale items refreshed; it now links the status doc and the register instead of copying decision status. | `d948252` |
| DOCS/00 to 10 marked as the Sprint 1 to 4 snapshot with a banner; only actively harmful lines corrected (environment values that fail startup, an incomplete install list, a bare `pytest` command that aborts, a production example without API keys). | `8f58a78` |
| FACT-CHECK-AGENT-STATUS.md added: the live Sprint 7 status document. | `6c8c89b` |
| PHASE-0: proposed wording to add `ClaimSpan` to the v1 freeze list. Whether it applies: see the status doc. | `51cf75b` |
| PHASE-0: proposed per-phase reading of the Phase 0 gate. Whether it applies: see the status doc. | `c3f0e53` |
| PHASE-0: stale facts corrected. No decision changed. | `ace45e4` |

## Sprint 7 Phase 0 and prerequisites

7 commits on `main`, 2026-09-04 to 2026-09-20. Phase 0 writes contracts and
decisions only, no application code. The rows below say which register
entries a commit touched; the outcome and current status of each decision
are in the [register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).

| Date | Change | Commit |
|---|---|---|
| 2026-09-20 | [D3-GOLD-SET-BRIEF.md](D3-GOLD-SET-BRIEF.md): scoping brief for decision D3 (the evaluation gold set), checked against the codebase. A recommendation. | `8fd3972` |
| 2026-09-20 | README brought up to date with shipped work: the Agent Brain pipeline stage, language-aware extraction, the rest of Tier S, the egress posture and a planned-work section. | `6f692e6` |
| 2026-09-20 | Register update for D10 (what the fact-check job re-runs when it finishes); D2's wording widened from "the PDF" to "downstream consumers". No code change. | `dacc926` |
| 2026-09-19 | The Agent Brain verdict map loses its default: an unmapped verdict now raises instead of becoming `UNVERIFIED`. Pinned by `test_verdict_map_is_total`. No change for current verdicts. | `1cd5350` |
| 2026-09-19 | Tests pin what `create_all()` can and cannot change on an existing database (`app/insights/tests/test_db_schema_evolution.py`), the behaviour the new-tables-only rule relies on. | `ff84664` |
| 2026-09-04 | Register update for D1 (persistence) and D2 (execution mode), with D2's contract consequences. | `2a539d3` |
| 2026-09-04 | Phase 0 contract and scope lock: [FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md) with the decision register (D1 to D9), the v1 contract recorded verbatim and marked frozen, a v2 contract specification, and compatibility, verdict-mapping, privacy, source and configuration policies. | `c6284b6` |

## N4b multilingual and L5 pipeline wiring

10 commits, 2026-09-19 to 2026-09-20. Plan:
[N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md). N4b is
Urdu and Arabic support in the Agent Brain's extraction; L5 gave the
pipeline adapter a production caller.

| Date | Change | Commit |
|---|---|---|
| 2026-09-20 | Docs: L5 closed in the N4b strategy; D10 added to the Phase 0 register; CLAUDE.md updated (L5-4). | `17606d6` |
| 2026-09-20 | Tests prove `PipelineAdapter.to_context` is called from the production pipeline and the detected language reaches the agents (L5-3). | `1d8f2f6` |
| 2026-09-20 | The pipeline's Agent Brain stage sends the signed Java callback without blocking the response (daemon thread); `min_confidence` filters the callback only, not the API response; off unless both callback URL and secret are set (L5-2). | `f619f7d` |
| 2026-09-20 | The Agent Brain runs as an opt-in `/v1/process-audio` stage (`VOICEIQ_AGENT_BRAIN_AUTO_RUN`, default off, `app/insights/config/settings.py:259`); the `recommendations` key appears only when the stage produced output (L5-1). | `f5e4d84` |
| 2026-09-20 | AgentRunner's duplicate sweep is no longer quadratic. Output byte-identical; 2,308 ms to 95 ms on a 16-minute call (L5-0). | `1fff8aa` |
| 2026-09-20 | Docs: N4b-1 Phase 1 recorded as done; Phase 2 (vocabulary entry) depends on decision L1. | `9ac7487` |
| 2026-09-20 | 34 tests over the language seam, using a synthetic sentinel instead of real Urdu or Arabic words. | `c2fadaa` |
| 2026-09-20 | Extraction vocabulary tables keyed by language; English moved verbatim under `"en"`; no other language ships content. | `404de5b` |
| 2026-09-20 | `AgentContext.language` becomes a read signal: an explicit argument wins, then the language Whisper detected, then English. | `359cd5f` |
| 2026-09-19 | N4b dependency and CI gate: probes show a vocabulary gap, not a model gap. N4b split into N4b-1 (vocabulary and wiring) and N4b-2 (native-script named-entity recognition); no new CI lane. | `cbcc7ee` |

## Tier 3 Wave E

34 commits, 2026-06-21 to 2026-09-19. Structural items E1 to E5, deferred
from the first Tier 3 pass.

### E3 completion (2026-09-19)

| Change | Commit |
|---|---|
| Docs: test-count table actually updated (731 local; 728 passed + 1 skipped for the CI lane, reproduced locally). | `d79ef0e` |
| Docs: Wave E closed; two more stale E3 mentions fixed. | `6cac476` |
| Docs: E3 marked done, with why the first half-fix (N3b) looked finished but was not. | `fb781d1` |
| Alignment: `_best_asr_for_window` bounded below as well, by bisecting a running maximum of segment ends. `align()` 441 ms to 42 ms on the 60-minute fixture; output byte-identical; 17 new tests. | `706d80d` |
| Docs: test-count reconciliation refreshed (631 to 714). | `1f6409a` |

### E2 Phase 2: orchestrator decomposition (2026-07-20)

| Change | Commit |
|---|---|
| README: orchestrator description corrected after the decomposition. | `07ffed7` |
| Docs: E2 Phase 2 closed. | `c2086d0` |
| The four hard-fail gates extracted; they raise a private `_HardFail` that `run()` catches once. | `a1e9ef7` |
| Stages moved verbatim into `_run_<stage>` methods: stats, sentiment, keywords; gender, emotion, topic, summary; intent, fact-check, flags; insights, PDF; ASR, diarization, alignment. No behaviour change; the 51-test harness passed after each batch. | `00c86ea`, `b536e2c`, `083cb52`, `fe95eb0`, `d2c11c7` |
| `_PipelineState` dataclass holds the state shared between stages. | `e81ca02` |
| Types: `JobIO.load_text`'s `default` parameter and return type widened to `str \| None` (runtime unchanged). | `b5d0c6d` |

### E2 Phase 1: orchestrator test harness (2026-07-17 to 2026-07-19)

| Date | Change | Commit |
|---|---|---|
| 2026-07-19 | README rewritten for the current platform scope; a leftover merge-conflict duplicate script removed. | `31585b4` |
| 2026-07-19 | Docs: why test counts differed between local runs and the two CI jobs; reading CI failures from annotations. | `b6a028e` |
| 2026-07-19 | Dependencies: scikit-learn declared explicitly. | `163ce7b` |
| 2026-07-19 | Harness mocks KeywordService, whose spaCy model is not installed in CI. | `077aaef` |
| 2026-07-19 | CI: harness failures surfaced as an annotation. | `76a8da7` |
| 2026-07-19 | Dependencies: fpdf declared in `requirements.txt`. | `854ca1e` |
| 2026-07-19 | CI: separate orchestrator-harness job with the heavy ML dependencies (retired in `4df98c6`). | `dee30d5` |
| 2026-07-17 | CLAUDE.md: harness done; the interpreter trap documented. | `a6a414b` |
| 2026-07-17 | Orchestrator harness, `app/insights/tests/test_orchestrator.py`: 51 tests over every stage plus end-to-end invariants; `orchestrator.py` coverage 0% to 100%; no production code changed. | `a17eac4`, `2ca4f08`, `f6bb074`, `6fc2099`, `24dc644` |

### E1, E4, E5 and first assessments (2026-06-21)

| Change | Commit |
|---|---|
| Summary engine: an unknown severity explicitly ranks 0 (E5). | `00ebf52` |
| Docs: E3 profiled; no actionable hot path at that scale (done later in `1f79131` and `706d80d`). | `1e67117` |
| Docs: E2 deferred until a test harness exists. | `7e46986` |
| Fact-check scorer uses the shared clamp (E1.b). | `c58dd9a` |
| Audio uploads checked by magic bytes (WAV, FLAC, MP4/M4A, MP3); anything else gets HTTP 415 (`app/utils/audio_sniff.py`, E4). | `e8a294f` |
| One shared `clamp` in `app/insights/core/_math.py` replaces three engine-local copies (E1). | `e9cbd1f` |

## Tier N

12 commits, all on 2026-07-25. Four previously deferred items, N1 to N4. Plan:
[TIER-N-PLAN.md](TIER-N-PLAN.md).

| Change | Commit |
|---|---|
| Agent Brain router also served from the lightweight image (`app/insight_main.py:80`). | `1774209` |
| Agent Brain router mounted on `app/main.py` (it had never been mounted, so the route was unreachable), with a payload-size cap and its own rate limit. | `df8eec3` |
| Docs: N4a done; Tier N tractable work complete, N4b deferred. | `3723efb` |
| Agent Brain deadline phrases resolved to ISO-8601 dates with the standard library, no new dependency; additive `deadlineDate` field (N4a). 62 new tests. | `99dfc96` |
| Docs: N2 done. | `2a17b91` |
| CI: heavy orchestrator-harness job retired; the whole suite runs in the light lane (N2). | `4df98c6` |
| Orchestrator imports ML libraries inside each stage, so the harness runs on the light requirements (N2). | `9e57cb4` |
| Docs: Tier N status and N2 pre-work findings. | `b107089` |
| Alignment: two quadratic scans bounded with bisect; about 5x faster on the 60-minute fixture, identical output (N3b). | `1f79131` |
| Alignment characterization suite: 74 tests, `alignment_service.py` coverage 78% to 99% (N3a). | `5c43f45` |
| [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md): integration spec for the timestamp-bound callback signature, for the Java team (N1). | `2bf9b76` |
| Docs: Tier N plan for the four deferred items. | `36697fd` |

## Tier S

14 commits, all on 2026-07-25. Eight security items in five waves. Plan:
[SECURITY-HARDENING-PLAN.md](SECURITY-HARDENING-PLAN.md). Current controls:
[SECURITY.md](SECURITY.md).

| Change | Commit |
|---|---|
| Docs: Tier S closed, all eight items done. | `ec8dc1e` |
| S8: interactive API docs (`/docs`, `/redoc`, `/openapi.json`) off in production; `/healthz` and `/version` stay public. | `c7c34db` |
| S7: a timestamp-bound v2 callback signature sent alongside v1; the callback URL must use HTTPS (local development hosts excepted). | `a54944b` |
| S3: fact-check enrichment on `/v1/process-audio` is opt-in (`?fact_check=` or `VOICEIQ_FACTCHECK_AUTO_ENRICH`, default off, `app/insights/config/settings.py:247`); [DATA-FLOW.md](DATA-FLOW.md) added. | `61fbf56` |
| S1: per-endpoint rate limits on `/v1/process-audio` and `/v1/fact-check`; over the limit returns 429 with `Retry-After`. | `93f73b1` |
| S2: job-artifact retention sweep (`VOICEIQ_JOB_RETENTION_HOURS`, default 24, `app/insights/config/settings.py:153`). | `3c3adb2` |
| S6: country name percent-encoded in the Wikipedia request path. | `7b0c313` |
| S5: an upload with no filename returns 400; the extension allowlist moved to `app/utils/upload.py`. | `525316d` |
| S4: a failed upload no longer returns exception detail to the client. | `f7ecd55` |
| Docs: wave completion notes for S-D, S-C, S-B (with a retention note) and S-A. | `f19e564`, `49fdf7e`, `cece640`, `cc211ec` |
| Docs: security hardening plan. | `58f81f2` |

## Sprint 6: Agent Brain

15 commits, 2026-06-21 to 2026-06-22. The rule-based recommendation layer in
`app/agent_brain/`.

| Date | Change | Commit |
|---|---|---|
| 2026-06-22 | Sprint 6 closed; `app/agent_brain/` added to the CI mypy gate and test job; callback settings documented in `.env.example`. | `ef005dd` |
| 2026-06-22 | End-to-end scenario tests; every agent_brain module at 100% coverage (103 package tests). | `bda33f9` |
| 2026-06-22 | Java callback client: body signed with HMAC-SHA256 (hash-based message authentication code), disabled unless URL and secret are both set; `AgentBrainSettings` with the `VOICEIQ_AGENT_` prefix (`app/agent_brain/config/settings.py:14`). | `3e362e9` |
| 2026-06-22 | Recommendations API route and the pipeline adapter. | `d4cfbe0` |
| 2026-06-22 | Agent runner and service: fan out to all five agents, refine, de-duplicate, rank; a failing agent is logged and skipped. | `0970819` |
| 2026-06-22 | Ranker: priority first, then confidence. | `ce4afd9` |
| 2026-06-22 | De-duplication with the standard-library `difflib`. | `9227881` |
| 2026-06-22 | Confidence refinement from speech-recognition (ASR) confidence, repetition and fact-check links. | `e64e011` |
| 2026-06-21 to 2026-06-22 | The five agents: Task, Follow-Up Meeting, Email Draft, Escalation and Fact-Check Review. The Escalation commit also rounds every recommendation's confidence to four decimal places at the model. | `c6d3bb4`, `c070a07`, `c74f99f`, `ec4ae84`, `55e43af` |
| 2026-06-21 | `BaseAgent` and extraction utilities: signals, assignee, date-time phrases, priority. | `822771e` |
| 2026-06-21 | Recommendation and context models; `requires_approval` is always true at schema level. | `ef5b5e8` |

## Tier 3 Waves A to D

13 commits, 2026-06-14 to 2026-06-21. Cleanups, schema fixes and the mypy
gate. Labels in parentheses are the Tier 3 item ids from the commit subjects.

| Date | Change | Commit |
|---|---|---|
| 2026-06-21 | Docs: Waves A, B and D closed; Wave E scope defined. | `b013e4a` |
| 2026-06-21 | CI: mypy becomes a required check (D). | `a094120` |
| 2026-06-21 | Pydantic mypy plugin and type annotations take `mypy app/insights/` from 126 errors to 0, type-only changes. | `768d8e2` |
| 2026-06-18 | Validator: hard errors carry `severity="error"` (B1). | `f16709a` |
| 2026-06-18 | CLAUDE.md: the orchestration fault-isolation contract written down (C5). | `3c2b14a` |
| 2026-06-18 | Rule engine: unused `threshold_profile` parameter removed (S2). | `16ceeb8` |
| 2026-06-18 | `TimelineMarker.reason` must be non-empty (C4). | `c83d92b` |
| 2026-06-18 | Timeline: a sentiment label flip marks an emotional shift only when the change also clears the threshold (B3). | `e656c12` |
| 2026-06-18 | Validator: `bool` rejected as a non-negative integer (B2). | `a54cdc1` |
| 2026-06-16 | Summary engine: unused `session` parameter removed. | `2a3d689` |
| 2026-06-16 | Summary engine: `speaker_insight` parameter annotated. | `2df83e0` |
| 2026-06-16 | Rule engine: dead identity `severity_map` removed. | `a199184` |
| 2026-06-14 | CLAUDE.md: index of the Tier 3 punch list. | `bc03d8e` |

## Tier 2

7 commits, 2026-06-01 to 2026-06-14. Test coverage and CI gates.

| Date | Change | Commit |
|---|---|---|
| 2026-06-14 | CI: lint job (ruff, mypy as information only, gitleaks on the working tree) and a Python 3.10 / 3.11 test matrix. | `1a9fbed` |
| 2026-06-01 to 2026-06-14 | Unit and edge-case tests take six insight-core engines to 100% coverage: normalizer, validator, analytics, summary, timeline, rule. | `0bd88b9`, `36d7c8a`, `a232811`, `17e1acf`, `4a561ad`, `d78691e` |

## Tier 1

6 commits, 2026-05-20 to 2026-05-23. Production-readiness security.

| Date | Change | Commit |
|---|---|---|
| 2026-05-23 | Model-loader cold start serialised with a lock in the diarization, sentiment and summary services. | `7c9f4fc` |
| 2026-05-23 | `.env.example` documents `VOICEIQ_FFMPEG_TIMEOUT_SEC`. | `cdbab6c` |
| 2026-05-23 | ffmpeg runs with a timeout (default 120 s, `app/insights/config/settings.py:139`); a timeout returns 422. | `80cd4f0` |
| 2026-05-23 | Fact-check repository: rows with the same `created_at` are ordered by id. | `c895a49` |
| 2026-05-23 | Payload-size caps on the `/v1` routes (411, 400 or 413); uploads streamed to disk in 1 MB chunks under a size cap (default 200 MB, `app/insights/config/settings.py:104`). | `ce3cb82` |
| 2026-05-20 | `X-API-Key` authentication on `/v1/insights/*`, `/v1/fact-check` and `/v1/process-audio`; `/healthz` and `/version` stay public. | `bb6e61b` |

## Tooling, first CI and Docker

12 commits, 2026-04-25 to 2026-05-20.

| Date | Change | Commit |
|---|---|---|
| 2026-05-20 | Commands reference PDF added. | `755f374` |
| 2026-05-05 | `08-deployment.md` updated for the Docker setup. | `9b093e1` |
| 2026-05-05 | Dockerfile pinned to Python 3.11 and driven by `requirements-insight.txt`, port 8888; `.dockerignore` extended; `docker-compose.yml` added. | `6c12d9f` |
| 2026-05-04 | `timezone.utc` instead of `datetime.UTC`, for Python 3.10. | `e55d46c` |
| 2026-05-03 to 2026-05-04 | CLAUDE.md: completed and resolved items removed from the task and known-issue lists. | `3d80668`, `3412338`, `b03215d` |
| 2026-05-03 | `.git-blame-ignore-revs` skips the bulk-format commit. | `a4cc8c3` |
| 2026-05-03 | Bulk ruff-format, end-of-file and trailing-whitespace baseline. | `96f2e32` |
| 2026-05-03 | Lint fixes; ruff configuration relaxed (line length 120); `data/` runtime artifacts untracked. | `3b98dc8` |
| 2026-05-03 | pre-commit hooks: ruff, ruff-format, gitleaks and file hygiene. | `fceeeac` |
| 2026-04-25 | First CI workflow: a lightweight GitHub Actions run of the insight tests. | `37ee449` |

## Sprint 5: fact-check engine

2 commits, 2026-05-03.

| Change | Commit |
|---|---|
| CLAUDE.md updated for the fact-check engine. | `6a2ae3e` |
| Rule-based fact-check engine: claim detector, classifier, comparator, scorer and engine; five source clients (exchangerate.host, CoinGecko, OpenWeather, Alpha Vantage, Wikipedia REST); SQLite `fact_check_results` table; `POST /v1/fact-check` and `GET /v1/fact-check/{id}`; auto-enrichment in `/v1/process-audio` (opt-in since `61fbf56`); 91 new tests. | `592e8e7` |

## Initial code and Sprints 1 to 4

9 commits, 2026-02-10 to 2026-04-25. Sprint scopes, from the sprint table
that CLAUDE.md held until `0ea4ddd` (lines 79 to 82 at `c86f0fc`): 1, foundation engines; 2, escalation
and inconsistency engines; 3, SQLite persistence and the Dockerfile; 4,
handoff documentation. DOCS/00 to 10 (index:
[00-index.md](00-index.md)) are the Sprint 1 to 4 snapshot.

| Date | Change | Commit |
|---|---|---|
| 2026-04-25 | Tracked bytecode and a stale source archive removed; archive files blocked in `.gitignore`. | `2d017b7` |
| 2026-04-25 | Merge of the remote `main`, keeping the local Sprint 2/3 version. The first merge commit. | `032d822` |
| 2026-04-25 | Insight service from Sprints 1 to 3 (`app/insights/`: engines, models, API, SQLite repository, tests), `app/insight_main.py`, Dockerfile, `.env.example`, and the Sprint 4 handoff set DOCS/00 to 10 with the handoff `.docx`. | `94f6479` |
| 2026-02-12 | Pipeline orchestrator (`app/pipeline/orchestrator.py`), job storage (`app/utils/job_io.py`) and one sample job's artifacts. | `b4c5c86` |
| 2026-02-10 to 2026-02-12 | README revisions. | `7220d33`, `472dfd2`, `f0c04a1` |
| 2026-02-10 | First application code: FastAPI app, `/v1/process-audio` route, audio and language-analysis services, evaluator, early tests. | `4c54e03` |
| 2026-02-10 | Initial commit: LICENSE and README. | `f21300a` |
