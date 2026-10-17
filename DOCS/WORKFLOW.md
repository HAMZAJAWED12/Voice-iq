Last verified: 2026-10-17, 2966527
# Workflow

How work gets done in this repository: branches, the local Python environment, the checks, the commit rules and the milestone loop. AI assistants read [CLAUDE.md](../CLAUDE.md) first. For where Sprint 7 stands, see [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md). For what the project is, see [CONTEXT.md](CONTEXT.md).

## Before every commit

Run these from the repository root. All of them passed at `dbae452` on 2026-10-17.

```powershell
.venv\Scripts\python.exe -m pytest -v app/insights/tests/ app/agent_brain/tests/
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m mypy --ignore-missing-imports --check-untyped-defs app/insights/ app/agent_brain/ app/utils/ app/security/
git status --short
```

Then stage files by explicit path and commit. The pre-commit hooks run on their own.

## Branches

| Rule | Detail |
|---|---|
| Base branch | `main`. |
| Working branch | A `sprint7/<topic>` branch cut from `main`. The current one is `sprint7/doc-sync`, cut from `main` at `8fd3972`. It holds the 2026-10-11 documentation sync, milestones M1 to M7, the project knowledge set, a set of security and reliability fixes and the fixes the owner decided on 2026-10-17: 62 commits beyond `main` at `dbae452` (`git rev-list --count main..HEAD`). |
| `main` | Never commit to `main` directly. |
| Push | Never push without the owner's go-ahead. `sprint7/doc-sync` has no upstream yet: `git rev-parse --abbrev-ref sprint7/doc-sync@{upstream}` fails. |
| CI trigger | CI runs on a push to `main` and on a pull request into `main`, nothing else ([.github/workflows/test.yml](../.github/workflows/test.yml), lines 3-7). Until a pull request is open, nothing checks a branch except the local checks. |

The repository sits in a synced OneDrive folder, which sometimes causes `index.lock` collisions. If git starts fighting itself, pause the sync ([CLAUDE.md](../CLAUDE.md), *Working with this repo*).

## Local Python environment

### What `.venv` holds

`.venv` lives at the repository root and git ignores it ([.gitignore](../.gitignore)). It holds the light stack only:

| Package set | Source |
|---|---|
| FastAPI, Pydantic, SQLAlchemy, httpx, slowapi, fpdf, pytest | [requirements-insight.txt](../requirements-insight.txt) |
| `ruff==0.7.4`, `mypy==1.13.0` | the pins CI installs (test.yml, line 29) |
| `pre-commit==4.0.1` | the pin in [requirements-dev.txt](../requirements-dev.txt). The rest of that file is evaluation extras, which routine work does not need. |

The current `.venv` runs CPython 3.12.10 (`.venv/pyvenv.cfg`). CI tests on 3.10 and 3.11 and lints on 3.11 (test.yml, lines 21 and 62). Ruff targets `py310` ([pyproject.toml](../pyproject.toml), line 3), so code must run on 3.10. Commit `e55d46c` is an example of a 3.10 compatibility fix.

To rebuild `.venv`, use any CPython from 3.10 to 3.12:

```powershell
<python> -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-insight.txt ruff==0.7.4 mypy==1.13.0 pre-commit==4.0.1
.venv\Scripts\python.exe -m pre_commit install
```

### Rules

- **Do not install `requirements.txt` for routine work.** It pulls the heavy ML stack: torch, torchaudio, transformers, pyannote.audio, openai-whisper, librosa, sentence-transformers and spaCy ([requirements.txt](../requirements.txt)). The whole test suite runs without it. Only one 3-test module skips on the light stack (see [Tests](#tests)). Ask the owner before installing it.
- **Run every tool as `.venv\Scripts\python.exe -m <tool>`.** A bare `python` or `pytest` on PATH may be a different interpreter that lacks the project's dependencies ([CLAUDE.md](../CLAUDE.md), *Working with this repo*). On some Windows machines, Application Control also blocks the unsigned `.exe` console-script launchers that pre-commit generates, while the venv's `python.exe` is not blocked (`7b25b3f`). `python.exe -m` avoids both problems.
- **Never open, read, print or search `.env`.** It holds live secrets and git ignores it. Take environment variable names and defaults from [app/insights/config/settings.py](../app/insights/config/settings.py), [app/agent_brain/config/settings.py](../app/agent_brain/config/settings.py) and [.env.example](../.env.example). Exclude `.env` from every search, for example `rg --glob '!.env'` or `grep --exclude=.env`.

## Tests

### The command

CI runs this command (test.yml, line 92, with `PYTHONPATH: .` at line 100):

```bash
pytest -v app/insights/tests/ app/agent_brain/tests/
```

Run it locally through the venv:

```powershell
.venv\Scripts\python.exe -m pytest -v app/insights/tests/ app/agent_brain/tests/
```

Locally it passes with or without `PYTHONPATH` set.

### Current count

On the light stack, at `dbae452`: **1730 passed, 1 skipped**, measured on 2026-10-17 on `.venv` (CPython 3.12.10). The CI-equivalent run with `--ignore=app/insights/tests/test_model_load_concurrency.py` gives 1730 passed.

The skip is `app/insights/tests/test_model_load_concurrency.py`. It holds 3 tests and calls `pytest.importorskip` on torch, transformers, soundfile, huggingface_hub and pyannote.audio at module level (lines 25-29). The full reconciliation (1733 on the heavy stack, derived rather than measured) lives in [CLAUDE.md](../CLAUDE.md), section *One test lane*. Update it there, not here.

### Why a bare `pytest` at the root fails

| Invocation | pytest rootdir and config | Result |
|---|---|---|
| `pytest app/insights/tests/ app/agent_brain/tests/` | rootdir `app/`, config `app/pytest.ini` | 1730 passed, 1 skipped |
| `pytest` (no paths, from the root) | rootdir is the repository root. The config is `pyproject.toml`, which has no pytest settings, so `app/pytest.ini` (`testpaths = app/insights/tests`) does not apply. | pytest walks the whole tree, collects the legacy `tests/` folder and stops: `Interrupted: 2 errors during collection` |

The two errors come from the legacy folder. `tests/test_diarization.py` imports `app.services.diarization_service`, which needs `soundfile`. `tests/test_diarization_load.py` imports `huggingface_hub`. The light stack includes neither. Always pass the two paths.

CI job logs cannot be read without repository admin rights. The workflow surfaces failures as annotations instead; see [CLAUDE.md](../CLAUDE.md), *CI pipeline notes*. Rules for heavy services (mock anything that imports an ML library or loads a model artifact) are in [CLAUDE.md](../CLAUDE.md), *Testing requirements*.

## Lint and types

| Check | Local command | In CI? |
|---|---|---|
| Ruff lint | `.venv\Scripts\python.exe -m ruff check .` | Yes (test.yml, line 32) |
| Ruff format | `.venv\Scripts\python.exe -m ruff format --check .` | No. The `ruff-format` pre-commit hook enforces it. |
| Mypy | `.venv\Scripts\python.exe -m mypy --ignore-missing-imports --check-untyped-defs app/insights/ app/agent_brain/ app/utils/ app/security/` | Yes, as a hard gate (test.yml, line 35; made required in `a094120`; `app/utils/` and `app/security/` added after `2966527`) |
| Gitleaks | Runs through the pre-commit hook (see below) | Yes, as `gitleaks dir . --no-banner --redact`, a scan of the working tree (test.yml, line 54). CI installs gitleaks only after checking the archive against the release checksums (lines 37-48; `c661bad`). |

Tool versions are pinned: ruff 0.7.4 and mypy 1.13.0 in CI (test.yml, line 29), and ruff v0.7.4 in the hook config.

Configuration lives in [pyproject.toml](../pyproject.toml):

- **Ruff:** line length 120, target `py310`, rule sets E, F, I, B and UP, with `B008` ignored for the FastAPI `Depends` idiom. It excludes `.venv`, `samples`, `DOCS`, `data`, `tmp`, `uploads` and `outputs` (lines 1-9).
- **Mypy:** pydantic plugin, `ignore_missing_imports`, `check_untyped_defs`. Test directories are excluded (lines 11-18).

At `dbae452`, ruff reported "All checks passed!" with 207 files already formatted, and mypy found no issues in 84 source files; with `app/utils/` and `app/security/` added to the gate, 99 source files.

## Pre-commit hooks

`.venv\Scripts\python.exe -m pre_commit install` writes `.git/hooks/pre-commit`. That hook calls the venv's `python.exe -m pre_commit`. The hooks run in this order ([.pre-commit-config.yaml](../.pre-commit-config.yaml)):

| Hook | Source | What it does | Edits files? |
|---|---|---|---|
| `ruff` | ruff-pre-commit v0.7.4 | `ruff check --force-exclude --fix` on Python, stub and notebook files | Yes (auto-fix) |
| `ruff-format` | ruff-pre-commit v0.7.4 | `ruff format --force-exclude` on the same files | Yes |
| `trailing-whitespace` | `repo: local`, pre-commit-hooks 5.0.0 | Trims trailing whitespace in text files | Yes |
| `end-of-file-fixer` | `repo: local`, pre-commit-hooks 5.0.0 | Makes each text file empty or end with exactly one newline | Yes |
| `check-merge-conflict` | `repo: local`, pre-commit-hooks 5.0.0 | Flags conflict markers. As upstream, it only checks while a merge is in progress (`7b25b3f`). | No |
| `gitleaks` | gitleaks v8.21.2 | `gitleaks protect --verbose --redact --staged`, a secret scan of the staged changes | No |

### Why there is a `repo: local` block

On some machines, Windows Application Control blocks the unsigned `.exe` console-script launchers that pre-commit generates for the remote pre-commit-hooks repository. Two commits, `dc03a44` and `2350738`, had to skip these three checks before the fix. Commit `7b25b3f` moved them into a `repo: local` block, which uses `entry: python -m pre_commit_hooks.<module>`, `language: python` and `additional_dependencies: [pre-commit-hooks==5.0.0]`. Ids, names, descriptions, file types and stages match the upstream v5.0.0 manifest; the entry is what changed (config lines 11-40).

The config says to bump `pre-commit-hooks==5.0.0` by hand in that block, not through `pre-commit autoupdate` (line 15).

### Hook rules

- **Never use `--no-verify`.** When a hook fails, fix the cause.
- **Re-stage auto-fixed files.** When `ruff`, `ruff-format`, `trailing-whitespace` or `end-of-file-fixer` change a file, the commit stops. Review the change, `git add` those exact paths again and commit again.
- **A hook that cannot run** may be skipped by id (`SKIP=<id>`) only with the owner's explicit approval. Before skipping it, run the same check by hand on exactly the staged files, and say so in the commit message. `dc03a44` and `2350738` are the only precedents recorded in commit messages; `7b25b3f` then removed the cause.
- **Run every hook on demand:** `.venv\Scripts\python.exe -m pre_commit run --all-files`.

## Commits

### Message format

Use a conventional prefix, `type(scope): summary`, in plain words that describe the effect:

| Type | Use for | Example |
|---|---|---|
| `fix` | A behaviour fix | `9aff207 fix(factcheck): bad provider replies never become verdicts or 500s` |
| `feat` | New behaviour | `f619f7d feat(pipeline): wire the Java callback + min_confidence filter (L5-2)` |
| `test` | Tests only | `ff84664 test(db): pin what create_all() can and cannot change` |
| `refactor` | Shape change, same behaviour | `404de5b refactor(extraction): vocabulary tables keyed by language (N4b-1 Phase 1)` |
| `perf` | Speed, same output | `706d80d perf(alignment): bound _best_asr_for_window below as well (E3 completion)` |
| `docs` | Documentation | `793e02e docs(sprint7): record M4 as done; promote M5; refresh test counts` |
| `chore` / `ci` / `style` | Tooling, CI, formatting | `7b25b3f chore(pre-commit): run the pre-commit-hooks checks through python -m` |

The body says what was wrong, what changed, which tests pin it, the red-check result and the full-suite count. `c1b35d9`, `9aff207` and `8e51a94` follow this pattern.

### Staging and content rules

- **Stage explicit paths only:** `git add <path> <path>`. Never use `git add .` or `git add -A`: `git add .` once staged the whole `.venv`, more than 10,000 files ([CLAUDE.md](../CLAUDE.md), *Working with this repo*). Check `git status --short` before staging and `git diff --cached --stat` before committing.
- **Keep docs separate from code.** First commit the fix, then a separate `docs(sprint7)` commit that records it. M1 went `c1b35d9` then `b1054be`, M2 went `9aff207` then `b370390`, M4 went `8e51a94` then `793e02e`, and M5 went `1dbfac7` then `10b0039`. A docs commit may correct a docstring but never changes behaviour (`3d970bf`).
- **Never commit these untracked files** unless the owner says so: `AGENTS.md`, `postman/`, `.postman/` and `VoiceIQ-Manual-Test-Guide.pdf` (`git status` at `dbae452`).
- **Never commit ignored material:** `.env`, `.venv/`, `data/`, `*.db`, logs, uploads and archives such as `*.zip` ([.gitignore](../.gitignore)).

## The milestone loop

Each Sprint 7 prerequisite fix is one milestone: the smallest first, and only fixes that need no pending decision ([FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md), *In progress*). M1 to M6 followed this loop; M6 landed in one batch with security fixes and shares their docs commit. M7 (keyword rules match whole words only), a tech-debt fix that came after the prerequisites, followed the same loop (`dbae452`). The fixes the owner decided on 2026-10-17 landed as small commits under the size rule below, one per decision that needed code (`dc97a84` to `45a5b22`; [DECISIONS.md](DECISIONS.md) #34 to #49; #33 was recorded without code). No engineering milestone is ready now; the next step is owner-side ([FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md), *Next milestone*).

| Step | What happens | Evidence |
|---|---|---|
| 1. Plan | Set the scope, the files, the tests and the acceptance evidence. Record them under *Next milestone* and *Acceptance evidence* in the status doc. Implementation starts only after the owner approves the plan. | Status doc, *Next milestone* and *Acceptance evidence* |
| 2. Red-check | Write the new tests first and run them against the current code. They must fail for the stated reason, apart from deliberate controls that pin behaviour that must not change. Record the counts. | `c1b35d9`: 10 of 14 fail. `9aff207`: 212 failed, 11 controls passed. `8e51a94`: 25 failed, 10 at-bound controls passed. `1dbfac7`: 352 of 433 fail against the previous detector. `dc9caf3`: `UnicodeEncodeError` and `PDF_FAILED` before the fix. |
| 3. Implement | Make the smallest change that turns the tests green. Follow the rules in [CLAUDE.md](../CLAUDE.md): the engineering standards, the Sprint 7 hard constraints (for example, v1 is frozen) and, under *Testing requirements*, no heavy imports at the orchestrator's module scope. | `9aff207`: "no v1 model or literal changes" |
| 4. Verify | In one session: the full light suite, ruff check, ruff format check and mypy, all as in [Before every commit](#before-every-commit). Record the new test count. | Full-suite counts in each fix commit body |
| 5. Adversarial review | An independent review pass tries to break the change, looking for secrets or URLs in logs, new 500 paths, missed input shapes and contract drift. Fix every valid finding, with its own red-checked test. Before any push that touches auth, secrets or external calls, run `/security-review` ([CLAUDE.md](../CLAUDE.md), *Style for AI assistants*). | `cedfe96` is a review finding on `c1b35d9` |
| 6. Checkpoint | Commit the code, then commit the docs (no push). Report the evidence to the owner and stop. The next milestone starts on the owner's approval. | Docs commits "record Mn as done; promote Mn+1" |

**Size rule** (project owner, 2026-10-16): if a milestone or a security fix grows past roughly twice its planned size, or needs a second rework round after review, stop and ask the owner before continuing ([DECISIONS #27](DECISIONS.md#27-size-rule-for-milestones-and-security-fixes)).

## Writing docs in a public repository

The repository is public. Everything committed must be safe to publish:

- Name roles, never people. No author names, usernames, or links that contain a username. (Exception, by owner decision: the repository URL in CLAUDE.md and README.md stays.)
- No quotes from internal messages, chats or voice notes.
- No internal URLs, hostnames, credentials or secrets of any kind.
- Describe security limitations in general terms only. The detailed gap list with severities is tracked privately. [SECURITY.md](SECURITY.md) is the public view.
- Decision status lives only in the register, [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register). Link to it; never copy it.
- Each knowledge doc starts with `Last verified: <date>, <commit>`. Re-verify the doc against code, tests and git before changing that line. Take every count from git at write time.

## What to update after each milestone

| Doc | Update it when |
|---|---|
| [FACT-CHECK-AGENT-STATUS.md](FACT-CHECK-AGENT-STATUS.md) | Every milestone. Move the milestone to *Completed* with its commit and test evidence, promote the next one, and refresh the date and branch rows. The doc says to update it with every milestone (lines 5-6). |
| [FACTCHECK-AGENT-PHASE-0.md §1](FACTCHECK-AGENT-PHASE-0.md#1-decision-register) | The owner signs or changes a decision. This is the only place decision status is written. |
| [DATA-FLOW.md](DATA-FLOW.md) | Anything about data leaving the host changes: an external call is added or removed, a destination changes, or what is sent or logged changes. `b1054be` and `2350738` updated it alongside M1 and M3. |
| [CLAUDE.md](../CLAUDE.md) and [README.md](../README.md) test counts | The test count changes. Re-measure with the CI command, then update the *One test lane* table in CLAUDE.md and the test line in README.md (line 34). The heavy figure is the light figure plus 3. `b370390`, `2350738` and `793e02e` did this. |
| [DECISIONS.md](DECISIONS.md) | A decision is signed in the register, or a decision is implemented. Pending items stay listed as open, with links to the register and the status doc. |
| [WORKFLOW.md](WORKFLOW.md) | Branch, environment, CI, hook or commit practice changes: [test.yml](../.github/workflows/test.yml), [.pre-commit-config.yaml](../.pre-commit-config.yaml), [pyproject.toml](../pyproject.toml), [app/pytest.ini](../app/pytest.ini) or a `requirements*.txt` file. |
| [CHANGES.md](CHANGES.md) | Every milestone. Add the new commits and refresh the counts from git. |
| [EXPLANATION.md](EXPLANATION.md) | Something a business reader would notice in the example call changes: a stage, an output, or how a failure degrades. |
| [FEATURES.md](FEATURES.md) | A user-visible capability is added, removed or behaves differently: a route, a response field or an opt-in flag. |
| [SECURITY.md](SECURITY.md) | A control is added or changed, or a known limitation opens or closes. Use general terms only; the detail is tracked privately. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | A package, a route, a module boundary, a fault boundary or a data store changes, or a Sprint 7 part moves from planned to built. |
| [SYSTEM.md](SYSTEM.md) | How the service runs changes: entry points, the Docker image, settings and environment variables, data stores, external services, logging or CI. |
| [CONTEXT.md](CONTEXT.md) | The project's phase, scope or open blockers change. |
| [00-index.md](00-index.md) | A maintained doc is added, renamed or retired. Update the *Current documents* table. |
