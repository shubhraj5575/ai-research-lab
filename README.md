# AI Research Lab (RLAB-001)

An autonomous **computational research environment**. The lab does not write
research prose about experiments — it *runs* real experiments in sandboxed
processes, subjects every conclusion to an adversarial critic, and generates
papers where **every number traces to a recorded experiment**.

```
RESEARCH → HYPOTHESIS → EXPERIMENT DESIGN → IMPLEMENTATION → EXECUTION
   ↑                                                        ↓
 CRITIQUE ← ANALYSIS ← RESULT ←──────────────────────────────┘
```

## What it actually does

Given a research question (e.g. *"Which exploration policy minimizes regret on
stochastic bandits?"*), eight software agents run an iterative loop:

| Agent | Responsibility |
|---|---|
| **Research Director** | owns the loop, budgets, deduplication, champion tracking |
| **Literature Agent** | arXiv discovery + bundled corpus, keyphrases, themes, gaps |
| **Hypothesis Agent** | strategy ladder: starters → replications → sweeps → transfers → head-to-heads → stress tests |
| **Experiment Designer** | hypothesis draft → validated config (variants, seeds, budget) |
| **Implementation Agent** | provenance gate: spec hash, code version, env snapshot, git commit |
| **Execution Agent** | parallel sandboxed runs (`python -I` subprocesses with rlimits) |
| **Data Analyst** | paired t-tests, Mann-Whitney U, paired bootstrap CIs, Holm correction, effect sizes |
| **Critic** | mechanical falsification attempts: irreproducibility, CI-vs-zero, wrong direction, small samples, scope overreach |

Every hypothesis carries `claim / reasoning / expected_result /
falsification_condition / required_experiment`. Every experiment records its
config hash, seed derivation, git commit, environment, per-run metrics and
result hashes. The critic re-executes sampled runs from scratch and compares
hashes byte-for-byte before allowing any conclusion to stand.

## Research domains (pluggable)

* **bandit** — stochastic K-armed bandits; policies ε-greedy, UCB1, UCB-Tuned,
  Thompson (Beta & Gaussian), optimistic-greedy; tasks bernoulli/gaussian ×
  horizons × gap difficulty.
* **optim** — derivative-free optimization of sphere/rosenbrock/rastrigin/ackley;
  solvers random search, hill-climb (+adaptive 1/5-rule ES), simulated annealing,
  differential evolution under strict evaluation budgets.

New domains implement one interface (`rlab/domain/base.py`) and immediately get
the full agent pipeline.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[server,dev]"

# autonomous session (22 iterations, ~15 min on an 8-core laptop)
rlab demo --domain bandit --offline-corpus

# or a custom one
rlab run --domain optim --iterations 10 --seeds 30

# evidence-grounded paper + figures
rlab report <session_id>

# interactive dashboard (live SSE updates, graph view)
rlab serve            # http://127.0.0.1:8620

# reproducibility audit of any recorded experiment
rlab verify <experiment_id> --samples 3
```

## Honest capability statement

* The default reasoning layer is **deterministic and rule-based**
  (`--reasoner heuristic`). It is not an LLM, and it is labeled as such in
  events, dashboards and papers. An optional LLM narration layer
  (`--reasoner llm --llm-provider anthropic|openai`) activates only when API
  keys are present and is never allowed to introduce numbers.
* Environments are synthetic benchmark families defined inside domain plugins.
  Findings are real *for those families*; external validity beyond them is
  explicitly scoped in generated papers.
* The local sandbox limits CPU time, file size and wall clock but does **not**
  provide network isolation. For stronger isolation use the opt-in Docker
  executor (`deploy/Dockerfile.sandbox`, `--executor docker`).

See `ARCHITECTURE.md` for internals, `DECISIONS.md` for design rationale,
`FINAL_REPORT.md` for the honest status of every subsystem.

## Repository layout

```
src/rlab/
  agents/        director, hypothesis ladder, designer, analyst, critic…
  domain/        plugin interface + bandit & optim kernels
  runtime/       materialization, sandboxed execution, repro hashing
  sandbox/       local executor (rlimits) + docker executor
  stats/         hand-rolled special functions + test engine
  literature/    arXiv provider, cache, TF-IDF analysis, gaps, themes
  graph/         provenance DAG (JSON/GraphML export)
  reports/       paper generator + SVG figure engine
  server/        FastAPI read-only API + SSE + vanilla-JS dashboard
  cli.py         rlab entry point
tests/           120+ tests incl. Monte-Carlo calibration of statistics
runs/            sessions (gitignored), demo artifacts committed separately
```
