"""D6 bake-off harness: the scoring core (milestone H1), the model-server client (milestone H2) and the runner with its
inputs, launch checks and records (milestone H3).

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

Milestone H3:

- `inputs`: the frozen requests and their hashes (S2 §3.10-§3.13, §4, §11).
- `launch`: the frozen launch line, the server process and its start-up checks (S2 §1, §3.2, §3.3, §3.5, Annex A).
- `records`: the run directory, stored calls, faults and voids (S2 §3.12, §3.13, §9, §10).
- `runner`: the schedule, its faults and repeats, and the determinism share (S2 §1, §3.1, §3.5, §3.6, §3.8, §3.13, §6).

`gold`, `extraction`, `schemas` and `stats` import nothing from this package; `scoring` imports `gold`, `extraction`
and `stats`; `probe` imports `extraction`; `client` imports `extraction`, `probe` and `schemas`; `inputs` imports
`client`, `gold`, `probe` and `schemas`; `launch` imports `client`; `records` imports `client`, `extraction`, `launch`,
`probe` and `schemas`; `runner` imports `client`, `inputs`, `launch`, `records` and `schemas`. Only `client` talks to
the network, and only to http://127.0.0.1:<port> (S2 §3.2, §3.12): gold rows never leave the host. Only `launch` starts
a process, and only `records` writes results, outside OneDrive. There is no prompt text here: H5 supplies it.
"""
