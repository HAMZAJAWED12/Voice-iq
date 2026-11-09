"""D6 bake-off harness: the scoring core (milestone H1), the model-server client (milestone H2), the runner with its
inputs, launch checks and records (milestone H3), and the gates, decision, report and report command (milestone H4a).

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
- `records`: the run directory, stored calls, faults, voids and the report pair (S2 §3.12, §3.13, §9, §10).
- `runner`: the schedule, its faults and repeats, and the determinism share (S2 §1, §3.1, §3.5, §3.6, §3.8, §3.13, §6).

Milestone H4a:

- `evidence`: the machine-evidence value types gate M reads, and the sample-rate description (S2 §3.13, §6, §9).
- `gates`: the record integrity checks, the schedule end, the scored units and gates L, J, C, T and M beside H1's A
  and S (S2 §3.6, §3.8, §3.13, §5, §6, §9).
- `decision`: the case-1 margin, pin rules 1 to 5 and the settings values (S2 §1, §2, §3.6, §3.13, §8).
- `report`: the body of `report-<n>.json`, the report of record, and its encoding (S2 §3.13, §9, §10, Annex A).
- `entry`: the `report` and `void` commands over a stored run directory (S2 §3.2, §3.12, §3.13, §9, §11).

`gold`, `extraction`, `schemas` and `stats` import nothing from this package; `scoring` imports `gold`, `extraction`
and `stats`; `probe` imports `extraction`; `client` imports `extraction`, `probe` and `schemas`; `inputs` imports
`client`, `gold`, `probe` and `schemas`; `launch` imports `client`; `records` imports `client`, `extraction`, `launch`,
`probe` and `schemas`; `runner` imports `client`, `inputs`, `launch`, `records` and `schemas`. `evidence` imports
`records`; `gates` imports `client`, `evidence`, `extraction`, `gold`, `inputs`, `launch`, `records`, `runner` and
`scoring`; `decision` imports `gates`, `launch`, `runner` and `stats`; `report` imports `decision`, `evidence`,
`gates`, `launch`, `records`, `runner`, `scoring` and `stats`; `entry` imports `decision`, `evidence`, `gates`, `gold`,
`launch`, `records`, `report`, `runner` and `stats`. No H1 to H3 module imports an H4 module, and nothing imports
`entry`. Only `client` talks to the network, and only to http://127.0.0.1:<port> (S2 §3.2, §3.12): gold rows never
leave the host. Only `launch` starts a process, and only `records` writes results and reports, outside OneDrive.
`evidence`, `gates`, `decision` and `report` read no file, start nothing and read no clock; only `entry` sets scored
markers, before any score is computed (S2 §3.13). This H4a build loads no machine evidence: on a run directory that
holds none, gate M is not measured, and one that holds some is refused (H4b reads it). There is no prompt text here:
H5 supplies it.
"""
