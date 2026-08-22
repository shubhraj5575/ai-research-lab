"""Regression: repeated verification must succeed (fresh sandbox each time)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlab.config import LabConfig
from rlab.domain import get_domain
from rlab.events import EventBus
from rlab.runtime.runner import ExperimentRunner
from rlab.sandbox.local import LocalExecutor
from rlab.store import Store
from helpers import make_exp


@pytest.fixture()
def runner(tmp_path: Path):
    cfg = LabConfig(root=tmp_path, seeds_per_config=2, max_parallel_workers=2,
                    experiment_timeout_s=30.0)
    store = Store(cfg.db_path)
    return cfg, store, ExperimentRunner(store, cfg, LocalExecutor(), EventBus())


def test_verify_twice_in_a_row_passes(runner):
    """Revealed mkdir(exist_ok=False) collision on _verify workdirs."""
    cfg, store, r = runner
    plugin = get_domain("bandit")
    exp = make_exp("bandit", "bernoulli",
                   {"ucb1@c=1.0": {"policy": "ucb1", "c": 1.0}}, n_seeds=2,
                   task_params={"K": 6, "T": 600, "gap_min": 0.05})
    r.run_experiment(exp, plugin)
    first = r.verify_reproducibility(exp, plugin, sample_size=2)
    second = r.verify_reproducibility(exp, plugin, sample_size=2)
    assert first["checked"] == 2 and first["passed"] == 2
    assert second["checked"] == 2 and second["passed"] == 2


def test_materialize_replaces_stale_workdir(runner, tmp_path: Path):
    _, _, r = runner
    plugin = get_domain("bandit")
    target = tmp_path / "stale"
    target.mkdir(parents=True)
    (target / "leftover.txt").write_text("old junk")
    r.materialize(target, plugin, "bernoulli",
                  {"K": 5, "T": 300}, {"policy": "ucb1", "c": 1.0}, seed=1)
    assert not (target / "leftover.txt").exists()
    assert (target / "kernel.py").exists()
    assert (target / "run_config.json").exists()
