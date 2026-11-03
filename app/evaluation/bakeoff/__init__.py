"""D6 bake-off harness, milestone H1: the scoring core.

Modules, each citing the signed D6 criteria note S2 (`DOCS/D6-CRITERIA-NOTE-S2.md`) section it implements:

- `gold`: load and verify a gold-set version directory (S2 §3.12, §5.1-§5.3).
- `extraction`: parse one model extraction output in the PHASE-0 §2.2 shape (S2 §3.8, §3.10, §5.3).
- `scoring`: matching, metrics, failures and rows used, gates A and S (S2 §5.1-§5.3, §6).
- `stats`: the paired bootstrap, run averaging and the case-1 margin test (S2 §8, §3.13).

`gold`, `extraction` and `stats` import nothing from this package; `scoring` imports the other three. Nothing here
calls a model or the network: gold rows never leave the host (S2 §3.12). There is no model client, runner or prompt
in H1.
"""
