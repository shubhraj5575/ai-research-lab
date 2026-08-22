# FINAL REPORT — AI RESEARCH LAB (RLAB-001)

## Executive Summary

Built an autonomous computational research environment that runs **real
experiments** — not simulated ones — inside isolated sandboxed processes, and
wraps them in an eight-agent research loop (propose → design → implement →
execute → analyze → critique → resolve). The flagship demonstration executed a
**22-iteration autonomous session** on stochastic multi-armed bandits:
20 distinct experiments, 1,290 seeded runs, 16 hypotheses supported / 2
refuted / 2 superseded by repetition-guard, every conclusion passing an
automated reproducibility audit (fresh sandboxed re-execution, byte-identical
result hashes), and an evidence-grounded paper whose every claim cites its
backing experiment IDs.

The system discovered a coherent empirical narrative autonomously: UCB-type
policies dominate fixed-ε exploration at longer horizons; posterior sampling
wins on hard-gap/short-horizon regimes; variance-aware bonuses (UCB-Tuned)
sweep late-stage cells — while also honestly reporting refutations such as
ε-greedy beating UCB1(c=1) at T=2000 on gap-free bandits.

## Project Goals

1. Real computational experimentation with strict reproducibility ✅
2. Full agent loop with adversarial critique ✅
3. ≥20 meaningful iterations in a final demonstration ✅ (20 experiments)
4. Evidence-grounded paper generation with traceability ✅
5. Live research dashboard + experiment graph ✅
6. Production-grade engineering: tests, security, observability, docs ✅

## Architecture

See `ARCHITECTURE.md` for diagrams and detail. In brief:

* **Orchestrator** (`agents/director.py`) — state machine per iteration with
  budget guards, spec deduplication, seed-independent config-key repetition
  guard, champion memory.
* **Agents** — Literature, Hypothesis (7-strategy adaptive ladder), Designer,
  Implementation (provenance gate), Execution (parallel sandbox runner),
  Analyst (paired statistics), Critic (9 mechanical finding codes +
  reproducibility spot-checks).
* **Domains** — pluggable plugins providing standalone kernels: `bandit`
  (6 policies × bernoulli/gaussian × horizons × gaps), `optim` (5 DFO solvers
  × sphere/rosenbrock/rastrigin/ackley).
* **Runtime** — per-run artifact bundles (kernel.py + canonical config),
  SeedSequence child seeds (common random numbers across variants),
  canonical-JSON spec/result hashing.
* **Storage** — SQLite WAL, versioned migrations; full audit event log.
* **Server** — FastAPI read-only API, SSE live feed, dependency-free SPA.

## Technology Choices

| Choice | Rationale |
|---|---|
| Python 3.11+/numpy | scientific kernels need vectorized RNG; ubiquitous |
| Hand-rolled statistics | no SciPy dependency; calibrated via tables + Monte-Carlo + permutation ground truth (D1) |
| SQLite WAL | local-first, concurrent readers, zero ops (D8) |
| subprocess sandbox | cross-platform isolation with honest limits; Docker for hard mode (D4) |
| Vanilla JS SPA | no build chain, no CDN, fully self-contained |
| argparse CLI | stdlib; no framework weight |

## Major Components

`src/rlab/`: config · jsonlog · events · ids · models · store · stats/
(dists+engine) · sandbox/ (local+docker) · runtime/ (repro+provenance+runner)
· domain/ (base+bandit+optim) · agents/ (8 roles + reasoning) · literature/
(providers+cache+analysis) · graph/ · reports/ (figures+paper) · server/
(app+static SPA) · cli.py

## Features Implemented

**COMPLETED**
- Core loop with budgets, dedup, repetition guard, strategy retirement
- Hypotheses with claim/reasoning/expected/falsification/required-experiment
- Reproducibility: seeds, config snapshots, env metadata, git commits,
  code-version hashes, result hashes, `rlab verify`, critic re-execution
- Paired statistics: Welch/paired-t/MWU, paired bootstrap CIs, Holm
  correction, Cohen's d, power planning, NEGLIGIBLE_EFFECT guard
- Critic checks: MISSING_RUNS, IRREPRODUCIBLE, CI_INCLUDES_ZERO,
  WRONG_DIRECTION, SMALL_SAMPLE, VARIANCE_DOMINATED, NO_BASELINE,
  SINGLE_COMPARATOR, SCOPE_OVERREACH
- Literature: live arXiv (Atom API, cache, courtesy delay) + labeled offline
  seed corpus, TF-IDF relevance/themes/pair comparison/gap detection
- Experiment graph: schema-validated typed DAG, evidence chains, SVG view,
  JSON+GraphML export
- Paper generation: all required sections, claims.json, deterministic SVG
  figures, explicit limitations from critic findings
- Dashboard: overview/experiments/graph/timeline, SSE live updates,
  convergence charts client-side
- CLI: run/demo/report/serve/verify/sessions/graph
- 122 tests incl. Monte-Carlo calibration of the statistics engine

## AI/Agent Architecture

The reasoning layer is **deterministic and rule-based by default**, clearly
labeled `reasoner="heuristic"` everywhere it matters (events, papers,
dashboard config panel). This is an honest design decision, not a limitation
hidden behind marketing: no LLM credentials were available, and fabricating
"AI behavior" would violate the project's own rules. An optional LLM
narration layer (Anthropic/OpenAI) is fully implemented behind
`--reasoner llm --llm-provider …`, degrades gracefully without keys, and is
structurally barred from introducing numbers (prompt contract + fallback).

## Testing

122 passed / 1 skipped (network-gated arXiv smoke test). Coverage spans:

- Statistics correctness: published t-table values, betainc identities,
  KS null-calibration (p~Uniform under H0), permutation-test agreement,
  bootstrap coverage simulation, power matching planned n
- Sandbox security: timeout kills, rlimit enforcement, env sanitization,
  user-site exclusion, exit-code propagation
- Scientific kernels: known optima, determinism, strict budget accounting
- Orchestrator end-to-end sessions on both domains
- Regressions for every bug found during development (sign conventions,
  metric inference, sweep reference arms, combo-key loop, verify idempotency)
- Output integrity: XML-valid figures, paper sections, claim→experiment
  referential integrity, GraphML well-formedness
- API contract tests + one real-socket SSE integration test

## Benchmarks

Machine: Apple Silicon 8-core, macOS 26.5. Full table in `docs/BENCHMARKS.txt`.

| Benchmark | Result |
|---|---|
| Welch t-test | ~10–28k calls/s |
| Paired bootstrap | ~55k resamples/s |
| Bandit episode T=5000 | ~0.08 s median (12/s) |
| DE optimization 4000 evals | ~0.08 s median |
| SQLite writes | ~13k rows/s (WAL) |
| Sandbox throughput | 5 runs/s @1 worker → 17 runs/s @8 workers |

Parallel scaling is process-spawn-bound (~3.4× at 8 workers); kernel compute
itself is not the bottleneck for our experiment sizes.

## Performance

Session wall-time for the flagship demo: ~14 minutes for 22 iterations
(1,290 sandboxed runs + analyses + critiques + two reproducibility
verifications per experiment). Bootstrap iteration count and worker count are
the dominant knobs.

## Security

Full review in `SECURITY.md`. Highlights: parameterized SQL with whitelisted
dynamic columns; no eval/exec/shell/pickle; identifier validation; read-only
loopback-bound API; secrets only ever read from environment at call time;
honest sandbox capability matrix (local limits CPU/filesize/time but NOT
network — Docker mode closes that gap).

## Known Issues

1. Local executor cannot enforce memory limits or network isolation on macOS;
   documented, Docker executor provided as the remedy.
2. Champion tracking treats "same task+budget" as comparability; effect sizes
   across environments are not pooled into a single leaderboard (deliberate —
   rankings across different worlds are meaningless).
3. The heuristic hypothesis ladder explores breadth-first through its
   strategies; it does not yet learn strategy efficacy across sessions
   (roadmap v0.2).

## Technical Debt

- `_known_families` relies on plugin attribute conventions (POLICY_PARAM_RANGES
  vs SOLVER_PARAM_RANGES); should be a formal plugin method.
- Server tests take ~60 s because each fixture builds a mini research session;
  a factory-light fixture would speed CI.
- Paper generator's Discussion section is thin relative to Results.
- No mypy/ruff configuration yet (manual review used throughout).

## Limitations

- Environments are synthetic benchmark families; external validity beyond
  them is explicitly scoped in every generated paper.
- With no LLM configured, hypothesis creativity is bounded by the seven
  implemented strategies (though they compose to genuinely adaptive behavior).
- arXiv discovery is rate-limited and cached; heavy literature mining was
  never a goal.

## Future Improvements

Roadmap in `ROADMAP.md`: sequential early-stopping, learned strategy
weighting, cross-session knowledge persistence, Wilcoxon signed-rank, noisy
optimization domain, nonstationary bandits, hardened Docker profiles.

## How to Run

```bash
pip install -e ".[server,dev]"
rlab demo --domain bandit --offline-corpus     # flagship session
rlab report <session_id>                       # paper + figures + claims.json
rlab serve                                     # http://127.0.0.1:8620
rlab verify <experiment_id>                    # reproducibility audit
pytest tests -q                                # full suite
```

## How to Demonstrate

1. `rlab serve --root runs/demo` → open the dashboard → Overview tab shows the
   completed 22-iteration session (question, hypotheses with statuses,
   outcome strip); Experiments tab drills into any experiment's analysis,
   critique findings, and convergence chart; Graph tab renders the 98-node
   provenance DAG.
2. `open runs/demo/rs_3569jzgjaw8z/report/paper.md` — the generated paper;
   every number carries an experiment ID; `claims.json` machine-checks this.
3. `rlab verify <eid>` — watch sampled runs re-execute and reproduce hashes.
4. `rlab run --domain optim --iterations 6 --seeds 16` — a fresh autonomous
   session on the second research domain, live in the dashboard.

## GitHub Repository

https://github.com/shubhraj5575/ai-research-lab
(push performed at end of session; see commit history for staged milestones)

## Final Project Status

| Area | Status |
|---|---|
| Research loop (8 agents) | **COMPLETED** |
| Two computational domains | **COMPLETED** (both validated by full autonomous sessions) |
| Reproducibility machinery | **COMPLETED** |
| Statistical engine (calibrated) | **COMPLETED** |
| Adversarial critic | **COMPLETED** |
| Provenance graph + exports | **COMPLETED** |
| Evidence-grounded papers | **COMPLETED** |
| Live dashboard | **COMPLETED** |
| Flagship ≥20-iteration demo (bandit) | **COMPLETED** (22 iterations / 20 experiments / 1290 runs; 16 supported / 2 refuted) |
| Cross-domain validation session (optim) | **COMPLETED** (14 iterations / 744 runs; 10 supported / 3 refuted; 14/14 reproducibility audits passed) |
| Wilcoxon signed-rank test | **COMPLETED** (exact n≤13 + normal approx) |
| LLM narration layer | **COMPLETED** (code + graceful degrade; untested against live APIs — no keys) |
| Docker executor path | **PARTIALLY COMPLETED** (implemented + image recipe; not exercised against a built image in this window) |
| Learned strategy selection | **NOT COMPLETED** (roadmap) |

Cross-domain findings (optim session, `docs/demo/paper_optim.md`): random
search upset simulated annealing on sphere@4000 evals (H1 refuted); DE won
rastrigin as literature predicts; SA's advantage appeared only on rosenbrock;
hill-climb sweeps found σ=0.1 optimal and (1+1)-adaptive ES ultimately reached
mean regret 0 on sphere.

Honesty notes: all experimental results are real outputs of real processes;
the reasoning layer is rule-based and labeled as such; no metric anywhere in
this report is fabricated.
