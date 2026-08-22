"""Experiment runtime integration tests (real sandboxed subprocess runs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rlab.config import LabConfig
from rlab.domain import get_domain
from rlab.events import EventBus
from rlab.models import Experiment, ExperimentConfig, ExperimentStatus
from rlab.runtime import (
    ExperimentRunner,
    RunTask,
    build_run_tasks,
    compute_spec_hash,
    derive_seed,
)
from rlab.sandbox.local import LocalExecutor
from rlab.store import Store


@pytest.fixture()
def lab(tmp_path: Path):
    cfg = LabConfig(root=tmp_path, seeds_per_config=3, max_parallel_workers=3,
                    experiment_timeout_s=30.0)
    store = Store(cfg.db_path)
    bus = EventBus()
    runner = ExperimentRunner(store, cfg, LocalExecutor(), bus)
    return cfg, store, bus, runner


def make_exp(domain_name: str, task: str, variants: dict, n_seeds: int = 3,
             seed_root: int = 7, task_params: dict | None = None,
             baseline: str | None = None) -> Experiment:
    cfg = ExperimentConfig(
        domain=domain_name, task=task, variants=variants,
        baseline=baseline or sorted(variants)[0], n_seeds=n_seeds,
        seed_root=seed_root, budget_label="test",
        extra={"task_params": task_params or {}},
    )
    exp = Experiment(
        id="ex_rt1", session_id="rs_t", hypothesis_id="hy_t", iteration=1,
        config=cfg, spec_hash=compute_spec_hash.__name__, code_version="v",
        git_commit="c", env_json={}, dataset_ref={},
    )
    exp.spec_hash = compute_spec_hash(exp)  # type: ignore[assignment]
    # compute_spec_hash is a function taking exp; emulate properly below.
    from rlab.runtime.repro import spec_hash as shash

    payload = {
        "domain": cfg.domain, "task": cfg.task,
        "task_params": cfg.extra.get("task_params", {}),
        "variants": cfg.variants, "baseline": cfg.baseline,
        "n_seeds": cfg.n_seeds, "seed_root": cfg.seed_root,
        "budget_label": cfg.budget_label,
    }
    exp.spec_hash = shash(payload)  # type: ignore[assignment]
    return exp


def test_seed_derivation_is_stable_and_independent():
    a = derive_seed(42, 0, 0)
    assert derive_seed(42, 0, 0) == a
    seeds = {derive_seed(42, vi, ri) for vi in range(3) for ri in range(4)}
    assert len(seeds) == 12


def test_build_run_tasks_covers_all_variants_and_seeds():
    exp = make_exp("bandit", "bernoulli",
                   {"ucb1": {"policy": "ucb1", "c": 1.0},
                    "eps": {"policy": "epsilon_greedy", "eps": 0.1}},
                   n_seeds=5)
    tasks = build_run_tasks(exp)
    assert len(tasks) == 10
    by_variant = {}
    for t in tasks:
        by_variant.setdefault(t.variant_name, []).append(t.seed)
    assert set(by_variant) == {"eps", "ucb1"}
    assert all(len(sds) == len(set(sds)) for sds in by_variant.values())


def test_full_experiment_execution_bandit(lab):
    cfg, store, bus, runner = lab
    plugin = get_domain("bandit")
    exp = make_exp(
        "bandit", "bernoulli",
        {"ucb1@c=1.0": {"policy": "ucb1", "c": 1.0},
         "eps_greedy@eps=0.10": {"policy": "epsilon_greedy", "eps": 0.1}},
        n_seeds=3,
        task_params={"K": 10, "T": 1000, "gap_min": 0.1},
    )
    events = []
    bus.subscribe(lambda e: events.append(e.type))
    results = runner.run_experiment(exp, plugin)
    assert len(results) == 6
    assert all(r.status == "ok" for r in results)
    for r in results:
        assert "total_regret" in r.metrics
        assert r.result_hash != ""
    stored = store.get_experiment(exp.id)
    assert stored.status == ExperimentStatus.COMPLETED
    assert "experiment.started" in events and "experiment.completed" in events


def test_reproducibility_verification_passes(lab):
    _, _, _, runner = lab
    plugin = get_domain("bandit")
    exp = make_exp("bandit", "bernoulli",
                   {"ucb1@c=1.0": {"policy": "ucb1", "c": 1.0}}, n_seeds=2,
                   task_params={"K": 6, "T": 800, "gap_min": 0.05})
    runner.run_experiment(exp, plugin)
    report = runner.verify_reproducibility(exp, plugin, sample_size=2)
    assert report["checked"] == 2
    assert report["passed"] == 2, report


def test_kernel_error_is_recorded_not_swallowed(lab, tmp_path):
    """A kernel that raises must yield failed runs with the error message."""
    cfg, store, bus, runner = lab

    class BrokenDomain(get_domain("bandit").__class__):  # type: ignore[misc]
        def kernel_source(self) -> str:  # override to inject failure
            src = super().kernel_source()
            return src.replace(
                "rng = np.random.default_rng(seed)",
                "raise RuntimeError('injected failure')\n    rng = None #",
            )

    broken = BrokenDomain()
    exp = make_exp("bandit", "bernoulli",
                   {"ucb1@c=1.0": {"policy": "ucb1", "c": 1.0}},
                   n_seeds=2, task_params={"K": 5, "T": 300})
    results = runner.run_experiment(exp, broken)
    assert all(r.status == "failed" for r in results)
    assert any("RuntimeError" in r.error for r in results)
    stored = store.get_experiment(exp.id)
    assert stored.status == ExperimentStatus.FAILED


def test_timeout_produces_timeout_status(lab, tmp_path: Path):
    cfg, store, bus, runner = lab
    cfg.experiment_timeout_s = 2.0
    plugin = get_domain("bandit")

    class SlowDomain(plugin.__class__):  # type: ignore[name-defined]
        def kernel_source(self) -> str:
            # inject a module-level sleep BEFORE the entry point so it runs
            return super().kernel_source().replace(
                'if __name__ == "__main__":',
                "import time\ntime.sleep(120)\n\n"
                'if __name__ == "__main__":',
            )

    slow = SlowDomain()
    exp = make_exp("bandit", "bernoulli",
                   {"ucb1@c=1.0": {"policy": "ucb1", "c": 1.0}},
                   n_seeds=1, task_params={"K": 5, "T": 300})
    results = runner.run_experiment(exp, slow)
    assert results and results[0].status == "timeout"


def test_spec_hash_changes_when_config_changes():
    e1 = make_exp("bandit", "bernoulli", {"a": {"policy": "ucb1", "c": 1.0}})
    e2 = make_exp("bandit", "bernoulli", {"a": {"policy": "ucb1", "c": 2.0}})
    e3 = make_exp("bandit", "bernoulli", {"a": {"policy": "ucb1", "c": 1.0}},
                  n_seeds=4)
    h = [e.spec_hash for e in (e1, e2, e3)]
    assert h[0] != h[1]
    assert h[0] != h[2]
