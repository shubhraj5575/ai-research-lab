# Development Guide

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]"
```

Optional stronger sandboxing:

```bash
docker build -t rlab-sandbox:latest -f deploy/Dockerfile.sandbox .
rlab run --executor docker ...
```

## Running tests

```bash
pytest tests -q              # full suite (~10 min; spawns many sandboxes)
pytest tests/test_stats.py   # statistics only (fast)
```

Test taxonomy:

| Suite | Kind | Notes |
|---|---|---|
| `test_stats.py` | unit + Monte-Carlo | table values, null calibration, permutation ground truth |
| `test_sandbox.py` | security/integration | timeouts, rlimits, env sanitization |
| `test_runtime.py` | integration | real subprocess execution, reproducibility |
| `test_domain_*.py` | scientific correctness | known optima, determinism, budget accounting |
| `test_analyst.py` | regression | sign conventions of comparisons |
| `test_orchestrator.py` | end-to-end loop | tiny budgets, real sandboxes |
| `test_strategy_diversity.py` | regression | ladder must not repeat designs |
| `test_graph.py` / `test_reports.py` | output integrity | XML validity, claim→experiment referential integrity |
| `test_server.py` | API contract | TestClient + one real-socket SSE test |

## Code layout conventions

* Dataclasses with explicit `to_dict`; JSON stored via `Store._j` (sorted keys).
* All randomness flows from explicit seeds — never bare `random`, never
  Python's `hash()` for identity across processes.
* New domain = subclass `DomainPlugin` + standalone kernel module + register in
  `domain/__init__.py`. No orchestrator changes required.

## Adding a research domain checklist

1. Kernel module with `run_episode(...)`-style entry and `__main__` block that
   reads `run_config.json` and writes `result.json` (see bandit/kernel.py).
2. Plugin class: tasks, defaults, budget options, baseline,
   `validate_variant`, `variant_label`, knobs, difficulty axes, starter
   hypotheses **with suggested configs**, literature queries.
3. Domain tests: determinism (same seed ⇒ identical metrics), budget
   accounting, invalid-input rejection.
4. Optional: seed-corpus entries for offline literature.

## Debugging a session

```bash
sqlite3 runs/<sid>/lab.db "SELECT number,status,claim FROM hypotheses ORDER BY number"
ls runs/<sid>/exp003/                 # artifact dirs per run
cat runs/<sid>/exp003/<label>/stderr.log
RLAB_LOG_LEVEL=DEBUG RLAB_LOG_FORMAT=text rlab run --iterations 2 --seeds 6
```

## Performance notes

See `scripts/bench.py`. Rough numbers on an 8-core M-series MacBook:
bandit episode T=5000 ≈ 0.15 s; DE 4000 evals ≈ 0.3 s; sandbox spawn ≈ 0.4 s;
parallel scaling ≈ 2.5× at 4 workers (process-spawn bound). If sessions feel
slow, reduce seeds or raise `--workers`; SQLite writes are not the bottleneck.

## Release checklist

- [ ] `pytest tests` green
- [ ] `rlab demo` completes ≥ 20 iterations with ≥ 3 distinct strategies used
- [ ] `rlab report <sid>` generates paper + figures without warnings
- [ ] `rlab verify <eid>` passes on fresh DB
- [ ] dashboard renders the demo session (overview/graph/timeline tabs)
