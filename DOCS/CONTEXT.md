Last verified: 2026-10-17, 3bf7506
# Project Context

Background for people joining, reviewing or sponsoring VoiceIQ: what the
service is for, who is involved, how it got here, where it stands and what is
holding it up.

This doc is for humans. AI assistants working in the repo have their own
brief, [CLAUDE.md](../CLAUDE.md), which describes itself as the project brief
for any AI assistant (`CLAUDE.md` line 3). Where the two overlap, link to the
other rather than copying.

---

## What VoiceIQ is

| | |
|---|---|
| What this repo is | The Python audio-intelligence service of the wider VoiceIQ platform (`README.md` line 11). |
| What it does | Takes a call recording and returns a transcript with speakers, NLP (natural language processing) signals such as sentiment and topics, explainable insights, optional fact-checks, optional action recommendations and a PDF report (`README.md` lines 3-9). Automatic fact-checks and recommendations are both off by default (`app/insights/config/settings.py` lines 193-194 and 205-206). |
| How it runs | A FastAPI app with two entry points: `app/main.py` mounts the full pipeline, including `/v1/process-audio` (`app/main.py` line 60; `app/routes/process_audio.py` line 140); `app/insight_main.py` is the lightweight app, which serves the insight and Agent Brain routes but no audio route (`app/insight_main.py` lines 71-75). |
| Who acts on the results | A separate Java action layer (`README.md` line 11). No Java code lives in this repo (`git ls-files '*.java'` returns nothing). |

The Python service and the Java action layer meet in two places:

- **Pull.** The Java layer calls
  `POST /internal/v1/agent-brain/recommendations/generate` with its own
  context (`app/agent_brain/api/agent_routes.py` lines 25-26 and 38; the
  comment at `app/main.py` lines 62-64).
- **Push.** When the opt-in Agent Brain stage runs inside the pipeline
  (`VOICEIQ_AGENT_BRAIN_AUTO_RUN`, default off:
  `app/insights/config/settings.py` lines 205-206;
  `app/pipeline/orchestrator.py` line 741), the service sends signed
  recommendations to a Java callback (`app/pipeline/orchestrator.py` lines
  808-835; signing described in
  `app/agent_brain/integrations/java_callback_client.py` lines 1-14). The
  callback stays off unless both a callback URL and a shared
  secret are set (`app/agent_brain/config/settings.py` lines 16-17 and
  27-28).

For more detail, see [FEATURES.md](FEATURES.md) (what it does),
[ARCHITECTURE.md](ARCHITECTURE.md) and [SYSTEM.md](SYSTEM.md) (how it is
built), [EXPLANATION.md](EXPLANATION.md) (one call walked through for a
business reader) and [DATA-FLOW.md](DATA-FLOW.md) (every point where data
leaves the host).

---

## Who is involved

This section uses roles only. Each row says what the repo's own docs assign to
that role. Some roles may be held by the same person; the docs do not say.

| Role | Part in the project | Where the docs say so |
|---|---|---|
| Engineering (this repo's team) | Writes and maintains the Python service and the planning docs. | "Owner: engineering" headers in [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md), [FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md), [TIER-N-PLAN.md](TIER-N-PLAN.md) |
| Project owner | Owns the open decision on the currency-rate provider. | [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) *Blockers* |
| Senior sponsor | Decides register questions on open-source scope and on the model choice. | Register rows D4 and D6 |
| Tech lead | Decides the persistence and re-run questions and co-decides execution mode with product; confirms pending wording changes to the Phase 0 contract, together with the engineering manager; decides several N4b questions. | Register rows D1, D2, D10; [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) *Blockers*; [N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md) §7 (L2-L5) |
| Engineering manager | Decides who owns and resources the gold set; co-confirms pending wording changes to the Phase 0 contract. | Register row D3; [D3-GOLD-SET-BRIEF.md](D3-GOLD-SET-BRIEF.md); [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) *Blockers* |
| Product | Decides first-release fact domains; co-decides execution mode (with the tech lead), how long v1 fields survive (with API consumers) and source-authority rules (with legal); decides who supplies and reviews the Urdu, Arabic and Roman-Urdu vocabulary. | Register rows D2, D7, D8, D9; N4b decision L1 |
| Infrastructure | Decides production hardware and the acceptable fact-check latency. | Register row D5 |
| Legal | Co-decides source-authority rules for high-stakes claims, with product. | Register row D9 |
| API consumers | Co-decide, with product, how long v1 and legacy `process-audio` fields must survive. | Register row D8 |
| Java action layer team | Runs the separate Java service; owns the Java side of the v2 callback signature. | [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md) lines 3-4; [TIER-N-PLAN.md](TIER-N-PLAN.md) N1 |
| Native-speaker reviewers (N4b) | Supply and review the Urdu, Arabic and Roman-Urdu vocabulary. This is language work, not engineering work. | [N4B-MULTILINGUAL-STRATEGY.md](N4B-MULTILINGUAL-STRATEGY.md) §6 Phase 2 and §7 (L1) |

The register is [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).
It is the only place decision status is kept; this doc does not repeat it.

---

## History

### Counts from git (at 3bf7506)

| Measure | Value | Command |
|---|---|---|
| First commit | `f21300a`, 2026-02-10 | `git log --reverse` |
| Commits reachable from HEAD | 184 (183 non-merge, 1 merge) | `git rev-list --count [--no-merges / --merges] HEAD` |
| The one merge | `032d822`, 2026-04-25 | `git rev-list --merges HEAD` |
| Commits on `main` | 141 (tip `8fd3972`, 2026-09-20) | `git rev-list --count main` |
| Commits on `sprint7/doc-sync` beyond `main` | 43 (16 on 2026-10-11, 16 on 2026-10-16, 11 on 2026-10-17) | `git rev-list --count main..HEAD` |

Commits per month (`git log --date=format:%Y-%m`): Feb 6, Apr 4, May 19,
Jun 41, Jul 49, Sep 22, Oct 43. There are no commits in March or August 2026.

### Timeline

The work ran in **sprints** (the planned blocks of work in the sprint table in
[CLAUDE.md](../CLAUDE.md)) and **tiers** (hardening, test-coverage, clean-up
and deferred-work passes run alongside and between the sprints). Date ranges
are commit dates; some ranges overlap.

| Dates | Work | Commits |
|---|---|---|
| 2026-02-10 to 02-12 | Repository created. The first audio pipeline is uploaded: the `process-audio` route, ASR (automatic speech recognition), diarization (who spoke when), alignment and NLP services, then the pipeline orchestrator and job file handling. | `f21300a`, `4c54e03`, `b4c5c86` |
| 2026-04-25 | Sprints 1-3 (the insight engines, the escalation and inconsistency engines, SQLite persistence) and the Sprint 4 handoff docs (`DOCS/00`-`10`) land in one commit. First CI (continuous integration) workflow. | `94f6479`, `032d822`, `37ee449` |
| 2026-05-03 to 05-05 | Sprint 5: rule-based fact-check engine. Pre-commit hooks. Docker hardening. | `592e8e7`, `fceeeac`, `6c12d9f` |
| 2026-05-20 to 05-23 | Tier 1: production-readiness security (API-key auth, payload caps, ffmpeg timeout, model-load locks). | `bb6e61b` to `7c9f4fc` |
| 2026-06-01 to 06-14 | Tier 2: unit and edge-case tests for six insight-core engines; CI lint and type checks added. | `0bd88b9` to `d78691e`, `1a9fbed` |
| 2026-06-14 to 06-21 | Tier 3, waves A, B and D: clean-ups, schema fixes, mypy made a required check. | `bc03d8e` to `b013e4a` |
| 2026-06-21 to 06-22 | Sprint 6: the Agent Brain, five rule-based recommendation agents plus the Java callback client. | `ef5b5e8` to `ef005dd` |
| 2026-06-21 to 09-19 | Tier 3, wave E: shared clamp helper, audio upload content check, orchestrator decomposition, alignment speed-up. | `e9cbd1f` to `d79ef0e` |
| 2026-07-25 | Tier S: eight security-hardening items, all committed the same day. | `58f81f2` to `ec8dc1e` |
| 2026-07-25 to 09-20 | Tier N: callback v2 spec (N1), lazy ML imports (N2), alignment tests and speed-up (N3), deadline phrases (N4a), language wiring for Urdu and Arabic (N4b-1 Phase 1), and the Agent Brain wired into the pipeline (L5). | `36697fd` to `17606d6` |
| 2026-09-04 onward | Sprint 7, Phase 0: contract and decision register, gold-set scoping brief, documentation sync, prerequisite fixes M1-M6, security and reliability fixes. | `c6284b6`, `8fd3972`, `ace45e4` to `3bf7506` |

Sprint and tier labels come from commit subjects and from the sprint table in
[CLAUDE.md](../CLAUDE.md). The historical Sprint 1-4 view is
[00-index.md](00-index.md) and [10-current-status.md](10-current-status.md).
Per-change detail is in [CHANGES.md](CHANGES.md).

---

## Where the project is now

**Sprint 7 is the Fact-Check Agent**: claim extraction with an open-source
language model, evidence retrieval, and verdicts that cite their evidence
(`CLAUDE.md` line 90). It is planned, not built. The plan has eight phases,
0 to 7 (source plan v1.1, 2026-09-04, named in
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) lines 8-9; "Phases
0–7" in `CLAUDE.md` line 90).

- **Phase 0, contract and scope lock**, is the current phase. It ships no
  application code. Its outputs are written policies and a signed decision
  register ([FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md) lines
  6-8).
- **Phase 1 is measurement.** Implementation starts in Phase 2
  ([FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md) lines 558-559).
- **No Sprint 7 application code exists at HEAD.** `git grep` finds no
  `/v2/fact-check` route, `fact_check_report` or agent master flag under
  `app/`.

While Phase 0 waits, engineering lands small fixes to the existing service
that need no pending decision, one milestone at a time, smallest first
([FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) *In progress*):

| Milestone | Commits | What changed |
|---|---|---|
| M1 | `c1b35d9`, `cedfe96` | Outbound request URLs kept out of the logs. |
| M2 | `9aff207` | A malformed reply from a fact-check provider ends as "source unavailable" for that claim, never a verdict or a server error. |
| M3 | `dc03a44` | Currency-rate provider errors are named instead of failing silently. |
| M4 | `8e51a94` | Over-long numbers in a transcript no longer cause a server error on either fact-check path. |
| M5 | `1dbfac7` | The claim detector no longer reads names, ages and counts as prices, and reads common price and currency phrasing ("1 USD = 280 PKR", "95k"). |
| M6 | `dc9caf3` | Text outside Latin-1 no longer fails the PDF report: common punctuation maps to its nearest Latin-1 form and any other character prints as "?". |

`7b25b3f` also moved three pre-commit checks to run through `python -m`,
because Windows Application Control blocked their generated `.exe` launchers
on a development machine (commit message of `7b25b3f`; day-to-day practice is
in [WORKFLOW.md](WORKFLOW.md)). Alongside the milestones, engineering landed
a set of security and reliability fixes from a review, 2026-10-16 to
2026-10-17 (`ff874de` to `3bf7506`; listed in [CHANGES.md](CHANGES.md)). The
remaining items from that review each wait on an owner decision. The next
milestone, **M7**, is a tech-debt fix: keyword rules match whole words only.
It is planned and awaits approval.

For live progress, the next milestone and its acceptance checks, read
[FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md). This doc does not
track progress.

---

## What is blocked, and why

General summary only. The live list is
[FACT-CHECK-AGENT-STATUS.md › Blockers](FACT-CHECK-AGENT-STATUS.md#blockers);
decision status is in the
[register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register).

| Blocked | Why | Who can unblock it | Read more |
|---|---|---|---|
| Sprint 7 Phase 1 | Phase 1 starts only when the owner decisions that gate it are made and the Phase 0 policies it depends on are approved. Several decisions that gate phases, such as the choice of job runner, are not yet in the register at all; adding them is part of the Phase 1 gate. One register decision concerns who owns and resources the gold set, the labelled examples the agent will be measured against. | The decision owners listed in [Who is involved](#who-is-involved) | [Register](FACTCHECK-AGENT-PHASE-0.md#1-decision-register), [Blockers](FACT-CHECK-AGENT-STATUS.md#blockers), [D3-GOLD-SET-BRIEF.md](D3-GOLD-SET-BRIEF.md), [DECISIONS.md](DECISIONS.md) |
| Checking currency-rate claims | The current provider, exchangerate.host, now rejects calls that carry no access key. The client sends none, so every currency-rate claim ends as "source unavailable" (`app/insights/core/factcheck/source_clients/forex_client.py` lines 3-8). Which provider to use is an open decision. It gates Phase 4. A new provider, or adding a key, changes what leaves the host, so [DATA-FLOW.md](DATA-FLOW.md) must change with it. | Project owner | [Blockers](FACT-CHECK-AGENT-STATUS.md#blockers) |
| Urdu and Arabic recommendations (N4b) | Language routing is built and tested (`app/agent_brain/extraction/language.py` lines 34-51; `c2fadaa`, `9ac7487`), but every vocabulary table holds English only. Filling them needs native speakers, because guessed terms would produce confident false positives in recommendations. | Product, by deciding who supplies and reviews the vocabulary (decision L1) | [N4B-MULTILINGUAL-STRATEGY.md §7](N4B-MULTILINGUAL-STRATEGY.md#7-open-decisions) |
| Callback signature v2 | The Python side shipped in `a54944b`. The Java side is outstanding, and it lives in the separate Java service, so no work in this repo can finish it. | Java action layer team | [JAVA-CALLBACK-V2.md](JAVA-CALLBACK-V2.md) |

Security controls and known limitations, in general terms, are in
[SECURITY.md](SECURITY.md).

---

## Terms used in this doc

| Term | Meaning here |
|---|---|
| Sprint | A planned block of work in the sprint table in [CLAUDE.md](../CLAUDE.md) (Sprints 1-7). |
| Tier | A hardening, test-coverage, clean-up or deferred-work pass outside the sprint plan (Tiers 1, 2, 3, S and N). |
| N4b | The Tier N item for Urdu and Arabic (multilingual) support ([TIER-N-PLAN.md](TIER-N-PLAN.md) line 15). |
| Phase | One of the eight steps, 0 to 7, of the Sprint 7 plan. |
| Milestone (M1, M2, ...) | One small fix, made one at a time while Phase 0 is open. M1 to M6 are prerequisite fixes; M7, planned, is tech debt. |
| Decision register | [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register): the Sprint 7 decisions (D1-D10), each with an owner role. |
| L1-L5 | The N4b multilingual decisions in [N4B-MULTILINGUAL-STRATEGY.md §7](N4B-MULTILINGUAL-STRATEGY.md#7-open-decisions). |
| Agent Brain | The rule-based recommendation layer in `app/agent_brain/` (Sprint 6). |
| Gold set | Labelled examples used to measure the fact-check agent's accuracy. |

---

## Related docs

| Doc | Use it for |
|---|---|
| [CLAUDE.md](../CLAUDE.md) | AI-assistant brief: rules, traps, test commands |
| [README.md](../README.md) | Public overview and quick start |
| [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) | Live Sprint 7 status |
| [FACTCHECK-AGENT-PHASE-0.md](FACTCHECK-AGENT-PHASE-0.md) | Phase 0 contract and the decision register |
| [DECISIONS.md](DECISIONS.md) | Decisions made so far, with links to the register |
| [CHANGES.md](CHANGES.md) | What changed, commit by commit |
| [WORKFLOW.md](WORKFLOW.md) | How work is done in this repo |
| [00-index.md](00-index.md) | Index of the historical Sprint 1-4 handoff set |
