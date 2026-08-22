"""DataAnalyst unit tests with synthetic runs in a real store.

Regression coverage for the delta/CI/effect-size sign-convention bug:
delta, CI bounds, and Cohen's d must all share the orientation
``mean_b - mean_a`` and 'better' must agree with the metric direction.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rlab.agents.analyst import DataAnalyst
from rlab.config import LabConfig
from rlab.domain import get_domain
from rlab.events import EventBus
from rlab.models import Experiment, ExperimentConfig
from rlab.runtime.repro import derive_seed
from rlab.store import Store


def make_experiment(tmp_path: Path) -> Experiment:
    cfg = ExperimentConfig(
        domain="bandit", task="bernoulli",
        variants={"good": {"policy": "ucb1", "c": 1.0},
                  "base": {"policy": "epsilon_greedy", "eps": 0.1}},
        baseline="base", n_seeds=30, seed_root=123,
        budget_label="bernoulli@5000",
        extra={"task_params": {"K": 10, "T": 5000},
               "primary_metric": "total_regret"},
    )
    return Experiment(id="ex_an1", session_id="rs_an", hypothesis_id="hy_an",
                      iteration=1, config=cfg, spec_hash="h", code_version="v",
                      git_commit="c", env_json={}, dataset_ref={})


@pytest.fixture()
def lab(tmp_path: Path):
    cfg = LabConfig(root=tmp_path, bootstrap_iters=400)
    store = Store(cfg.db_path)
    analyst = DataAnalyst(EventBus(), cfg, store)
    return store, analyst


def _inject_runs(store: Store, exp: Experiment, good_offset: float,
                 noise_sd: float = 20.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    names = sorted(exp.config.variants)  # ['base', 'good']
    for ri in range(exp.config.n_seeds):
        shared_seed = derive_seed(exp.config.seed_root, ri)
        latent = rng.normal(0, 1)  # CRN: shared environment difficulty
        for name in names:
            base_value = 300.0 + 60.0 * latent + rng.normal(0, noise_sd)
            value = base_value - good_offset if name == "good" else base_value
            from rlab.models import RunResult
            from rlab.ids import new_id

            store.save_run(RunResult(
                id=new_id("run"), experiment_id=exp.id, variant=name,
                seed=shared_seed, metrics={"total_regret": round(value, 4)},
                status="ok", result_hash=f"hash-{name}-{ri}",
            ))


def test_sign_convention_delta_ci_effect_agree(lab, tmp_path):
    store, analyst = lab
    exp = make_experiment(tmp_path)
    # 'good' has 80 lower regret than baseline -> a better, delta=mean_b-mean_a>0
    _inject_runs(store, exp, good_offset=80.0)
    analysis = analyst.analyze("rs_an", exp)
    assert analysis is not None
    comp = analysis.comparisons[0]
    assert comp.variant_a == "good"
    assert comp.delta > 40, f"delta={comp.delta} should be ~+80"
    assert comp.ci_low > 0 and comp.ci_high > 0, "CI must match delta sign"
    assert comp.effect_size > 0
    assert comp.better == "a"
    assert comp.significant is True


def test_reversed_comparison_flips_better(lab, tmp_path):
    store, analyst = lab
    exp = make_experiment(tmp_path)
    _inject_runs(store, exp, good_offset=-50.0)  # 'good' actually worse
    analysis = analyst.analyze("rs_an", exp)
    comp = analysis.comparisons[0]
    assert comp.delta < -20
    assert comp.ci_high < 0
    assert comp.better == "b"


def test_ranking_uses_minimize_direction(lab, tmp_path):
    store, analyst = lab
    exp = make_experiment(tmp_path)
    _inject_runs(store, exp, good_offset=100.0)
    analysis = analyst.analyze("rs_an", exp)
    assert analysis.ranking[0][0] == "good"
    assert analysis.best_variant == "good"


def test_no_comparisons_when_single_variant(lab, tmp_path):
    store, analyst = lab
    exp = make_experiment(tmp_path)
    exp.config.variants = {"base": exp.config.variants["base"]}
    _inject_runs(store, exp, good_offset=0.0)
    analysis = analyst.analyze("rs_an", exp)
    assert analysis is not None
    assert analysis.comparisons == []
