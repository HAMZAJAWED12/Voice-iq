# VoiceIQ Insight Service — Developer Handoff Documentation

This `DOCS/` folder is the canonical handoff package for the **VoiceIQ Insight Service**. It documents the system as it currently exists in this repository — every engine, every model, every endpoint, every persistence and configuration knob, every test, every container concern. It does **not** contain forward-looking roadmap items or speculative work; that is intentional and is left for the receiving developer to define.

The documentation is organised so it can be read top-to-bottom by a senior engineer joining the project, or used as a reference once the codebase is loaded.

| # | File | Purpose |
|---|------|---------|
| 01 | [`01-overview.md`](01-overview.md) | What the system is, where the Insight Service sits in the larger pipeline, and the design principles every engine follows. |
| 02 | [`02-architecture.md`](02-architecture.md) | Module topology, dependency direction, request lifecycle from HTTP entry to persisted bundle, and the data contract between layers. |
| 03 | [`03-data-models.md`](03-data-models.md) | Every Pydantic model used at the API edge, in analytics, signals, insights, escalation, inconsistency, and persistence. |
| 04 | [`04-engines.md`](04-engines.md) | Per-engine deep dive: validator, normalizer, analytics, signal aggregation, timeline, scoring, summary, escalation, inconsistency, rule engine. Every detector, every threshold, every score component. |
| 05 | [`05-api-reference.md`](05-api-reference.md) | Every HTTP route, request/response shape, status semantics, error contract, and `curl` examples. |
| 06 | [`06-persistence-and-config.md`](06-persistence-and-config.md) | Settings system, environment variables, SQLAlchemy engine wiring, ORM model, repository contract, schema bootstrapping. |
| 07 | [`07-developer-setup.md`](07-developer-setup.md) | Cloning, Python environment, dependency install, running locally with `uvicorn`, hitting the service from Postman, Swagger UI. |
| 08 | [`08-deployment.md`](08-deployment.md) | Multi-stage Dockerfile, build and run commands, healthcheck, volume mounting, environment overrides for staging/prod. |
| 09 | [`09-testing.md`](09-testing.md) | Test layout, fixtures, what each test asserts, how to run the suite, current pass count. |
| 10 | [`10-current-status.md`](10-current-status.md) | Sprint-by-sprint history of what has been delivered and is currently on disk, including the most recently shipped Inconsistency Engine. |

## How to use this package

If you are picking up the project, start with `01-overview.md` and read sequentially. Sections 02–04 are the architectural and engine-level reference, sections 05–06 are the operational contract, sections 07–09 are the developer hand-loop, and section 10 is the current ship state.

Every claim in these documents is derived from the source files in `app/insights/` as they exist on this branch. Where a path is referenced (e.g. `app/insights/core/escalation_engine.py`) the file is present and current. Where a threshold or score cap is quoted, the value is the value in code, not in design notes.
