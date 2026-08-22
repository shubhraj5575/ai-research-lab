"""Benchmarks for lab hot paths.

Usage:  python scripts/bench.py [--quick]

Measures and prints a table; results are informational (this machine, this
build) rather than absolute. Each benchmark returns (name, seconds, extra).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np


def bench_stats(quick: bool):
    from rlab.stats import bootstrap_delta_ci_paired, welch_ttest, mann_whitney_u

    rng = np.random.default_rng(0)
    n = 60 if quick else 200
    a = rng.normal(100, 15, n).tolist()
    b = [x - 5 + rng.normal(0, 2) for x in a]

    t0 = time.perf_counter()
    for _ in range(200 if quick else 1000):
        welch_ttest(a, b)
    t_welch = time.perf_counter() - t0

    iters = 500 if quick else 4000
    t0 = time.perf_counter()
    bootstrap_delta_ci_paired(a, b, iters=iters, seed=1)
    t_boot = time.perf_counter() - t0

    t0 = time.perf_counter()
    mann_whitney_u(a * (n // n), b)
    t_mwu = time.perf_counter() - t0
    return [
        ("stats.welch_ttest x1000", t_welch, f"{1000 / t_welch:.0f} calls/s"),
        (f"stats.paired_bootstrap({iters})", t_boot, f"{iters / t_boot:.0f} resamples/s"),
        ("stats.mann_whitney_u", t_mwu, ""),
    ]


def bench_kernel_bandit(quick: bool):
    from rlab.domain.bandit.kernel import run_episode

    task_params = {"K": 10, "T": 5000, "gap_min": 0.1}
    variant = {"policy": "thompson_bernoulli"}
    reps = 20 if quick else 100
    times = []
    for i in range(reps):
        t0 = time.perf_counter()
        run_episode("bernoulli", task_params, variant, seed=i)
        times.append(time.perf_counter() - t0)
    return [("kernel.bandit T=5000", statistics.median(times),
             f"median of {reps}; {1 / statistics.median(times):.0f} episodes/s")]


def bench_kernel_optim(quick: bool):
    from rlab.domain.optim.kernel import run_solve

    reps = 8 if quick else 40
    times = []
    for i in range(reps):
        t0 = time.perf_counter()
        run_solve("rastrigin", {"dim": 8, "n_evals": 4000},
                  {"policy": "differential_evolution", "pop_size": 32,
                   "F": 0.7, "CR": 0.9}, seed=i)
        times.append(time.perf_counter() - t0)
    return [("kernel.optim DE evals=4000", statistics.median(times),
             f"median of {reps}")]


def bench_store(quick: bool):
    from rlab.store import Store
    from rlab.models import RunResult
    from rlab.ids import new_id

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "bench.db")
        rows = 500 if quick else 3000
        t0 = time.perf_counter()
        for i in range(rows):
            store.save_run(RunResult(
                id=new_id("run"), experiment_id="ex_bench",
                variant=f"v{i % 4}", seed=i,
                metrics={"total_regret": float(i), "avg_reward": 0.5},
                series={"cumulative_regret": [float(j) for j in range(64)]},
                result_hash=f"h{i}",
            ))
        dt_write = time.perf_counter() - t0
        t0 = time.perf_counter()
        got = store.list_runs("ex_bench")
        dt_read = time.perf_counter() - t0
        assert len(got) == rows
    return [
        (f"store.save_run x{rows}", dt_write, f"{rows / dt_write:.0f} rows/s (WAL)"),
        (f"store.list_runs({rows})", dt_read, f"{rows / dt_read:.0f} rows/s"),
    ]


def bench_parallel_scaling(quick: bool):
    """End-to-end sandbox throughput at different worker counts."""
    from concurrent.futures import ThreadPoolExecutor
    from rlab.config import LabConfig
    from rlab.domain import get_domain
    from rlab.events import EventBus
    from rlab.runtime.runner import ExperimentRunner, RunTask
    from rlab.runtime.repro import derive_seed
    from rlab.sandbox.local import LocalExecutor
    from rlab.store import Store
    from rlab.models import Experiment, ExperimentConfig

    with tempfile.TemporaryDirectory() as td:
        cfg = LabConfig(root=Path(td), experiment_timeout_s=30.0)
        store = Store(cfg.db_path)
        plugin = get_domain("bandit")
        runner = ExperimentRunner(store, cfg, LocalExecutor(), EventBus())

        exp = Experiment(id="ex_bench", session_id="rs_bench",
                         hypothesis_id="hy_bench", iteration=1,
                         config=ExperimentConfig(
                             domain="bandit", task="bernoulli",
                             variants={"ucb1@c=1": {"policy": "ucb1", "c": 1.0}},
                             baseline="ucb1@c=1", n_seeds=1, seed_root=7,
                             extra={"task_params": {"K": 8, "T": 1500}}),
                         spec_hash="x", code_version="x", git_commit="x",
                         env_json={}, dataset_ref={})
        task_params = {"K": 8, "T": 1500}

        def one(i: int) -> float:
            rt = RunTask("ex_bench", "ucb1@c=1", 0, i, derive_seed(7, i))
            t0 = time.perf_counter()
            rr = runner.execute_run(plugin, Path(td) / f"w{i}", exp, task_params,
                                    rt, exp.config.variants["ucb1@c=1"])
            assert rr.status == "ok"
            return time.perf_counter() - t0

        results = []
        workers_list = [1, 4] if quick else [1, 2, 4, 8]
        for w in workers_list:
            total = 16 if quick else 32
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=w) as pool:
                list(pool.map(one, range(total)))
            dt = time.perf_counter() - t0
            results.append((f"sandbox.parallel workers={w}", dt,
                            f"{total} runs -> {total / dt:.1f} runs/s"))
        return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    quick = args.quick

    print(f"{'benchmark':<44} {'seconds':>9}  notes")
    print("-" * 88)
    for fn in (bench_stats, bench_kernel_bandit, bench_kernel_optim,
               bench_store, bench_parallel_scaling):
        try:
            rows = fn(quick)
            for name, secs, note in rows:
                print(f"{name:<44} {secs:>9.3f}  {note}")
        except Exception as exc:
            print(f"{fn.__name__:<44} FAILED: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
