# Decision Records

Chronological record of significant engineering decisions, each with context
and consequences. Newest at the bottom.

## D1 — Python 3.12 + numpy only as core runtime dependency

*Decision:* implement statistics (incomplete beta, t/MWU tests, bootstrap)
from first principles instead of depending on SciPy.

*Context:* the lab's credibility rests on statistics; a hand-rolled engine is
a liability unless verified. But SciPy is a ~120MB dependency for a handful of
functions.

*Consequences:* we wrote `stats/dists.py` (continued-fraction incomplete
beta) and calibrated it with published t-table values, Monte-Carlo null
uniformity (KS bound), permutation-test agreement and power-simulation tests.
Verification burden accepted consciously; tests run in CI on 3.11/3.12.

## D2 — Common random numbers across variants

*Decision:* child seeds derive from `(seed_root, repetition)` without mixing
the variant index.

*Context:* paired designs dramatically increase power for Monte-Carlo
algorithm comparisons; identical environments per seed make paired t-tests and
paired bootstrap CIs meaningful.

*Consequences:* comparisons are paired by construction; an unpaired fallback
exists for ragged data. First demo session surfaced that UCB1 vs ε-greedy
differences of <1% of mean regret were resolvable — impossible unpaired.

## D3 — Deterministic heuristic reasoner is the default; LLM optional

*Decision:* no LLM is required anywhere in the loop. An optional narration
layer exists behind provider+key configuration.

*Context:* no API credentials are guaranteed in autonomous environments;
fabricated numbers would be fatal to the project's honesty requirements.

*Consequences:* all hypothesis prose derives from structured facts via
templates. Papers annotate their reasoning mode. The LLM path is real code,
tested by contract, degraded gracefully.

## D4 — Kernels are standalone source bundles, not library calls

*Decision:* experiments execute a verbatim copy of a domain kernel file in an
empty sandbox workdir rather than importing the lab package.

*Context:* reproducibility requires the executed code to be inspectable and
hashable as an artifact; isolation requires minimal ambient authority.

*Consequences:* every artifact dir contains exactly what ran; `code_version`
is its content hash; kernels cannot silently drift from the recorded version.

## D5 — Structured error contract between kernel and runtime

*Decision:* kernels catch their own exceptions and write
`result.json {"status":"error", ...}`; the runtime prefers this contract over
exit codes.

*Context:* discovered when an injected kernel failure produced exit=2 but the
structured message was ignored, leaving "failed" runs without causes.

*Consequences:* failures carry actionable messages into the DB, critic
findings and dashboard; sandbox-level failures (timeout/rlimit) remain
distinct statuses.

## D6 — Canonical budget keys instead of display labels

*Decision:* identity of a (task, budget) combination is derived from the
parameters that `budget_options()` actually vary (`DomainPlugin.budget_key`),
not from human labels.

*Context:* the first overnight demo looped iterations 5–22 on one transfer
experiment because the director recorded `"bernoulli@2000"` while the strategy
queried `"T=2000"`.

*Consequences:* strategies can no longer disagree about combo identity.
Additionally, seed-independent `config_key`s let the director retire any
strategy that proposes an already-tested comparison.

## D7 — Paired bootstrap as headline uncertainty; Holm across families

*Decision:* significance = Holm-corrected paired-t p-values; uncertainty =
paired bootstrap CIs; both reported always.

*Context:* an early session showed paired p≈3e-9 beside an unpaired CI
crossing zero — confusing and avoidable.

*Consequences:* CI and test share the pairing structure so they agree;
regression test locks the sign conventions (delta ≡ mean_b − mean_a).

## D8 — SQLite + WAL over a client/server database

*Decision:* single-file storage with explicit versioned migrations.

*Context:* research sessions are local-first; the dashboard reads concurrently
with the orchestrator writing.

*Consequences:* zero-ops deployment, trivially portable artifacts; write
throughput measured ~2k rows/s — far above requirement (~60 runs/experiment).

## D9 — Read-only dashboard

*Decision:* the server exposes no mutation endpoints at all.

*Context:* an autonomous system must not offer accidental kill/steer surfaces.

*Consequences:* steering happens through CLI flags and config env vars;
dashboard risk reduced to information disclosure on loopback.

## D10 — SVG figures generated from store data

*Decision:* hand-written deterministic SVG chart engine instead of matplotlib.

*Context:* papers embed figures; byte-determinism aids verification; keeping
the dependency footprint small matters for the sandbox image too.

*Consequences:* convergence curves / ranking bars / outcome timelines render
identically everywhere; XML-validated in tests.
