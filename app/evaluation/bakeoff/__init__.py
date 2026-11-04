"""D6 bake-off harness: the scoring core (milestone H1) and the model-server client (milestone H2).

Modules, each citing the signed D6 criteria note S2 (`DOCS/D6-CRITERIA-NOTE-S2.md`) section it implements.

Milestone H1:

- `gold`: load and verify a gold-set version directory (S2 §3.12, §5.1-§5.3).
- `extraction`: parse one model extraction output in the PHASE-0 §2.2 shape (S2 §3.8, §3.10, §5.3).
- `scoring`: matching, metrics, failures and rows used, gates A and S (S2 §5.1-§5.3, §6).
- `stats`: the paired bootstrap, run averaging and the case-1 margin test (S2 §8, §3.13).

Milestone H2:

- `schemas`: the constrained JSON schemas and the canonical-JSON hash helper (S2 §3.8, §3.10, §4).
- `probe`: parse one reasoning-probe output (S2 §3.8, §4, §6).
- `client`: request bodies, the attempt loop, the context check and the records (S2 §3.2-§3.9).

`gold`, `extraction`, `schemas` and `stats` import nothing from this package; `scoring` imports `gold`, `extraction`
and `stats`; `probe` imports `extraction`; `client` imports `extraction`, `probe` and `schemas`. Only `client` talks to
the network, and only to http://127.0.0.1:<port> (S2 §3.2, §3.12): gold rows never leave the host. There is no runner
or prompt text here yet.
"""
