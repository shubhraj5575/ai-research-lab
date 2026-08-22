# Overnight Log

Working log of the autonomous build: milestones, problems found, fixes,
and honest observations. Newest entries at the bottom.

---

## Phase 0 — Environment & scaffold
- Inspected environment: Python 3.12.6, numpy/fastapi/uvicorn present, no
  LLM keys, gh authenticated as shubhraj5575, Docker 29.4 available, 8 cores.
- Scaffolded `ai-research-lab` with src layout, venv (numpy/pytest/fastapi/
  uvicorn/httpx pinned), git init.

## Phase 1 — Core infrastructure
- ids (Crockford-ish short IDs), structured JSON logging, thread-safe event
  bus with replay buffer, LabConfig with RLAB_* env overrides.
- SQLite store: WAL mode, migrations v1 (all tables) + v2 (hypothesis
  prediction columns added later), typed CRUD over dataclasses.

## Phase 2 — Statistics engine
- Hand-rolled regularized incomplete beta via continued fractions → exact
  Student-t p-values; Welch, paired t, tie-corrected MWU; percentile
  bootstrap (unpaired + paired); Cohen's d, Cliff's delta; Holm/BH;
  normal-approx sample-size planning.
- **Bugs caught by tests:** MWU continuity correction lost its sign (z always
  positive); test-side issues (Cliff's delta convention, BH adjusted-p
  formula, permutation harness rank bug). All fixed with regression coverage:
  published t-table values, KS null-calibration bound, permutation agreement,
  power simulation, bootstrap coverage simulation.

## Phase 3 — Sandbox + runtime
- LocalExecutor: `python -I`, sanitized env (PYTHONHASHSEED=0, threads=1),
  RLIMIT_CPU/FSIZE/NOFILE, process-group kill on wall timeout, capped logs.
  Honest docs: no network isolation locally; Docker executor provided as the
  strong boundary (`--network none`, caps) — opt-in.
- ExperimentRunner: per-(variant,seed) workdir bundles (kernel.py verbatim +
  canonical run_config.json), worker pool, structured error contract,
  SeedSequence child seeds, verify_reproducibility() hash comparison.
- Domains built: bandit (6 policies) and optim (5 DFO solvers × 4 landscapes).

## Phase 4 — Agents + orchestrator
- Reasoning layer: deterministic HeuristicReasoner default (labeled!), real
  Anthropic/OpenAI narration path that degrades gracefully and never emits
  numbers.
- Strategy ladder hypothesis agent; CRN-paired analyst; mechanical critic
  (9 finding codes + sandboxed repro spot-checks); director loop with budget
  guards, spec dedup, champion memory.
- **Bugs found by running a real mini-session:**
  1. Starter drafts parsed prose to pick variants ("Thompson...UCB1..." matched
     UCB1 first). → starters now carry explicit experiment sketches.
  2. Analyst inferred metric alphabetically ("avg_reward" beat
     "total_regret") and hardcoded minimize. → domain-declared metric+direction.
  3. Replication draft degenerated when champion==baseline (single variant →
     NO_BASELINE). → champion-vs-rival fallback.
  4. Unpaired bootstrap CI contradicted paired t-test. → paired bootstrap.
  5. Comparison sign conventions inconsistent (delta vs CI vs d). → unified
     mean_b−mean_a orientation + regression tests with synthetic CRN runs.
  6. SMALL_SAMPLE demanded ~25k seeds for d≈0.03. → NEGLIGIBLE_EFFECT info
     cap above 500 seeds/arm.

## Phase 5 — Graph, reports, dashboard, CLI
- Provenance DAG with edge-schema validation, evidence chains, JSON+GraphML.
- Paper generator: sections strictly from DB; claims.json maps every claim to
  experiment IDs; deterministic SVG figures (convergence/ranking/timeline).
- FastAPI read-only API + SSE + vanilla-JS SPA (4 tabs).
- CLI: run/demo/report/serve/verify/sessions/graph.
- **Bug:** SSE test deadlocked — Starlette TestClient buffers whole responses,
  so infinite streams can never finish there; test moved to a real uvicorn
  socket. Also fixed `_check_id` false-positive test (Crockford alphabet has
  no i/l/o).
- **Bug:** `rlab verify` failed on second call ever — materialize refused to
  reuse existing workdirs. Runtime now owns its dirs (wipe + recreate).

## Phase 6 — Overnight demonstration session #1
- 22 iterations completed on bandit domain. Science worked (H1/H2 supported
  with sensible effect sizes; c-sweep refuted then replicated; transfer to
  T=2000 supported). **Defect discovered:** iterations 5–22 repeated one
  identical transfer design because combo identity used two different label
  formats between director memory and strategy queries.
- Fix: canonical `budget_key()` derived from budget-varying params + seed-
  independent config keys + strategy retirement guard. Regression test
  asserts ≥5 distinct designs across an 8-iteration session.

## Phase 7 — Demonstration session #2 (in progress)
- Restarted demo with all fixes; ladder now rotates transfers (gaussian@2000
  inconclusive, @10000 supported), head-to-heads, sweeps; repetition guard
  superseding loops as designed. Full artifacts + paper generation after
  completion; benchmarks recorded in BENCHMARKS section of FINAL_REPORT.
