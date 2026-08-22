# Architecture

## 1. System overview

```
┌──────────────────────────── CLI / Dashboard ────────────────────────────┐
│  rlab run|demo|report|serve|verify        FastAPI (read-only) + SSE    │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  ResearchDirector   │  budget guards, dedup,
                        │   (orchestrator)    │  champion memory, resolution
                        └──────────┬──────────┘
        ┌───────────┬──────────────┼───────────────┬──────────────┐
        ▼           ▼              ▼               ▼              ▼
  Literature   Hypothesis     Designer       Implementation  Critic
    Agent        Agent          │               Agent (+      Agent
        │           │           │            Execution via     │
        ▼           ▼           ▼             Experiment-      ▼
   Sources/Gaps  Drafts    Experiment-       Runner)       Findings/
   /Themes                 Config                          Verdicts
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │      SQLite store        │  sessions, hypotheses,
                    │  (WAL, migrations v1/v2) │  experiments, runs,
                    └──────────────────────────┘  analyses, critiques, events
```

## 2. The iteration pipeline

`ResearchDirector.run_single_iteration` executes, in order:

1. **propose** — `HypothesisAgent.propose` walks a strategy ladder; the first
   strategy producing a draft wins. Ladder order: `starter → replication →
   sensitivity_sweep → transfer_test → head_to_head → stress_escalation →
   exploration`. Strategies consult `ResearchMemory` (tested labels, knob
   values tried, canonical combo keys, seed-independent config keys, champion
   & rival context) so the session adapts to its own results.
2. **design** — drafts carry structured experiment sketches
   (`suggested_variants/task_params/seeds`). The designer validates every
   variant against the domain contract, computes the canonical
   `budget_key`/`config_key`, and fills defaults.
3. **repetition guard** — if the config key was already tested this session,
   the hypothesis is superseded and the *strategy is retired* rather than
   paying for a redundant run.
4. **deduplication** — full spec hash (including seed root) is checked against
   prior completed experiments; identical specs reuse their cached analysis.
5. **implement** — provenance snapshot (git commit, Python/numpy/platform),
   dataset derivation record, code-version hash of the kernel bundle.
6. **execute** — every (variant × seed) repetition materializes an isolated
   workdir (`kernel.py` + canonical `run_config.json`) and runs as
   `python -I kernel.py` under rlimits + wall-clock kill, in a worker pool.
7. **analyze** — CRN-paired comparisons vs the baseline family: paired t-test,
   Mann-Whitney robustness p, paired bootstrap CI (deterministic per-comparison
   seed), Cohen's d; Holm step-down across the family.
8. **critique** — mechanical findings (`MISSING_RUNS`, `IRREPRODUCIBLE`,
   `CI_INCLUDES_ZERO`, `WRONG_DIRECTION`, `SMALL_SAMPLE`, `NEGLIGIBLE_EFFECT`,
   `VARIANCE_DOMINATED`, `NO_BASELINE`, `SINGLE_COMPARATOR`,
   `SCOPE_OVERREACH`) plus a sandboxed reproducibility spot-check.
9. **resolve** — hypothesis status transitions (`SUPPORTED`, `REFUTED`,
   `INCONCLUSIVE`, …) derive only from verdicts + headline comparison
   statistics; confidence is a function of |effect size|.

## 3. Determinism & reproducibility model

* Child seeds derive from `SeedSequence(entropy=seed_root, spawn_key=(rep,))`
  — deliberately independent of variant index so all variants see identical
  environments (**common random numbers**), enabling paired inference.
* Kernels use one seeded numpy Generator; string hashing is pinned via
  `PYTHONHASHSEED=0` in the sanitized child environment.
* Canonical JSON (sorted keys, no whitespace) feeds both `spec_hash` and
  result hashes. Result hash = sha256 of `{metrics, series}`.
* Bootstrap seeds derive from comparison identity strings, never from
  Python's randomized `hash()`.
* `rlab verify <experiment_id>` re-materializes sampled workdirs from scratch
  and compares hashes; the critic does this automatically per experiment.

## 4. Statistics engine

Implemented from first principles (no SciPy):

* Regularized incomplete beta via continued fractions (Lentz) → exact-ish
  Student-t two-sided p-values; validated against published t-tables.
* Monte-Carlo calibration test asserts Welch p-values are ~Uniform(0,1)
  under H0 (KS bound), and that observed power matches planned power.
* Mann–Whitney U normal approximation with tie correction and signed
  continuity correction.
* Percentile bootstrap (paired and unpaired variants).
* Holm step-down and Benjamini–Hochberg corrections.

## 5. Domains

A domain plugin provides: tasks, task defaults, budget options, baseline
variant, param validation ranges, knobs (with sweep values), difficulty axes,
starter hypotheses **with explicit experiment sketches**, literature queries,
and the kernel source path. Kernels are standalone modules copied verbatim
into sandboxes — they depend only on numpy+stdlib and write a strict
`result.json` contract.

## 6. Persistence

SQLite in WAL mode behind a lock-guarded connection; versioned migrations.
Tables mirror the dataclasses in `models.py`. Every bus event is also appended
to the `events` table (audit trail powering the dashboard timeline).

## 7. Observability

* Structured JSON logs (`RLAB_LOG_FORMAT=text` for humans).
* Event bus → SSE stream → dashboard live feed; all events persisted.
* Per-experiment artifacts on disk: `kernel.py`, `run_config.json`,
  `stdout.log`, `stderr.log`, `result.json`.
* Health endpoint; benchmark script (`scripts/bench.py`).

## 8. Security model

* Experiments are machine-generated but run as separate OS processes with:
  `-I` isolated interpreter, sanitized env (minimal PATH, HOME=workdir), CPU
  and file-size rlimits, process-group SIGKILL on timeout, capped log files.
* Local executor explicitly does NOT provide network isolation; Docker
  executor (`--network none`, mem/cpu/pids caps, unprivileged user) is the
  hard boundary option.
* Dashboard binds loopback by default, exposes only read-only endpoints,
  validates identifiers against the Crockford alphabet, and never proxies
  filesystem paths.
* Secrets (LLM API keys) are read from the environment at call time, never
  persisted into snapshots, DB rows or artifacts.

## 9. Reasoning layers

`HeuristicReasoner` composes text from structured facts only (templates +
numbers already computed). `LLMReasoner` (optional) wraps Anthropic/OpenAI
HTTP APIs for narrative polish; on any failure it degrades to heuristic text
and records why. Provenance (`reasoner`, `model`) travels with outputs.
