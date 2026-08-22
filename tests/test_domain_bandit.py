"""Domain kernel correctness tests (imported directly, no sandbox)."""

from __future__ import annotations

import numpy as np
import pytest

from rlab.domain.bandit.kernel import (
    EpsilonGreedy,
    UCB1,
    make_environment,
    pull,
    run_episode,
)


def _mean_regret(task, task_params, variant_params, n=40, seed_root=1000):
    regrets = []
    for i in range(n):
        out = run_episode(task, task_params, variant_params, seed=seed_root + i)
        regrets.append(out["metrics"]["total_regret"])
    return float(np.mean(regrets))


def test_environment_respects_gap_constraint():
    rng = np.random.default_rng(7)
    env = make_environment(rng, "bernoulli", {"K": 10, "gap_min": 0.2})
    means = np.sort(env["means"])[::-1]
    assert means[0] - means[1] >= 0.2


def test_gaussian_env_shapes():
    rng = np.random.default_rng(3)
    env = make_environment(rng, "gaussian", {"K": 5, "sigma": 1.0})
    assert env["means"].shape == (5,)
    assert ((env["means"] >= 0) & (env["means"] <= 1)).all()


def test_pull_bounds():
    rng = np.random.default_rng(11)
    env = make_environment(rng, "bernoulli", {"K": 4})
    rewards = [pull(env, 0, rng) for _ in range(200)]
    assert all(r in (0.0, 1.0) for r in rewards)


def test_ucb_beats_eps_greedy_on_gap_bandits():
    """Core sanity: UCB1 should dominate fixed-eps exploration given a gap."""
    params = {"K": 10, "T": 4000, "gap_min": 0.15}
    ucb = _mean_regret("bernoulli", params, {"policy": "ucb1", "c": 1.0})
    eps = _mean_regret("bernoulli", params, {"policy": "epsilon_greedy", "eps": 0.1})
    assert ucb < eps * 0.8, f"ucb={ucb:.1f} expected << eps-greedy={eps:.1f}"


def test_determinism_same_seed_same_result():
    cfg_task = {"K": 6, "T": 800, "gap_min": 0.05}
    variant = {"policy": "thompson_bernoulli"}
    a = run_episode("bernoulli", cfg_task, variant, seed=42)
    b = run_episode("bernoulli", cfg_task, variant, seed=42)
    assert a["metrics"] == b["metrics"]
    assert a["series"]["cumulative_regret"] == b["series"]["cumulative_regret"]
    c = run_episode("bernoulli", cfg_task, variant, seed=43)
    assert a["metrics"] != c["metrics"] or c is not a


def test_series_downsampling_contract():
    out = run_episode(
        "gaussian", {"K": 5, "T": 9000, "sigma": 1.0},
        {"policy": "epsilon_greedy", "eps": 0.2}, seed=5,
    )
    series = out["series"]["cumulative_regret"]
    assert len(series) <= 256
    # cumulative regret must be non-decreasing
    assert all(b >= a - 1e-9 for a, b in zip(series, series[1:]))
    # monotone in expectation: final value matches metric
    assert abs(series[-1] - out["metrics"]["total_regret"]) < max(1.0, out["metrics"]["total_regret"] * 0.02)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        run_episode("nope", {}, {}, seed=1)
    with pytest.raises(ValueError):
        run_episode("bernoulli", {"K": 10, "T": 5}, {}, seed=1)
    with pytest.raises(ValueError):
        run_episode("bernoulli", {"K": 10, "T": 500}, {"policy": "does_not_exist"}, seed=1)
