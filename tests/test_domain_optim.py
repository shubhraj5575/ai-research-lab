"""Optim kernel correctness tests."""

from __future__ import annotations

import numpy as np
import pytest

from rlab.domain.optim import kernel as K


def test_problems_have_zero_optimum_at_reference_point():
    assert K.sphere(np.zeros(6)) == pytest.approx(0.0)
    assert K.rosenbrock(np.ones(6)) == pytest.approx(0.0)
    assert K.rastrigin(np.zeros(6)) == pytest.approx(0.0)
    assert K._ackley(np.zeros(6)) == pytest.approx(0.0)


def test_budget_is_respected_exactly():
    calls = {"n": 0}

    def counting_fn(x):
        calls["n"] += 1
        return float(np.sum(x * x))

    problem = {"fn": counting_fn, "f_star": 0.0, "bounds": (-5.0, 5.0), "tol": 1e-3}
    rng = np.random.default_rng(0)
    for solver_name in K.SOLVERS:
        calls["n"] = 0
        budget = K.Budget(300)
        params = {"policy": solver_name}
        if solver_name == "differential_evolution":
            params |= {"pop_size": 16}
        K.SOLVERS[solver_name](budget, problem, 4, rng, params)
        # every solver must stop within [budget-? , budget]: no overshoot allowed
        assert budget.remaining == 0 or budget.used <= 300
        assert calls["n"] <= 300


def test_all_solvers_deterministic():
    for solver_name in K.SOLVERS:
        params = {"policy": solver_name}
        if solver_name == "hill_climb":
            params |= {"sigma": 0.3}
        if solver_name == "simulated_annealing":
            params |= {"t0": 1.0, "alpha": 0.995, "sigma_scale": 0.05}
        if solver_name == "differential_evolution":
            params |= {"pop_size": 12, "F": 0.7, "CR": 0.9}
        a = K.run_solve("sphere", {"dim": 4, "n_evals": 400}, params, seed=99)
        b = K.run_solve("sphere", {"dim": 4, "n_evals": 400}, params, seed=99)
        assert a["metrics"] == b["metrics"], f"{solver_name} not deterministic"


def test_adaptive_hc_beats_random_on_sphere():
    def mean_regret(solver_params, n=30):
        vals = [
            K.run_solve("sphere", {"dim": 8, "n_evals": 2000}, solver_params,
                        seed=500 + i)["metrics"]["final_regret"]
            for i in range(n)
        ]
        return float(np.mean(vals))

    rs = mean_regret({"policy": "random_search"})
    es = mean_regret({"policy": "hill_climb_adaptive", "sigma0": 0.5})
    assert es < rs, f"adaptive ES ({es:.3f}) should beat random search ({rs:.3f})"


def test_invalid_solver_or_problem_raise():
    with pytest.raises(ValueError):
        K.run_solve("weierstrass", {}, {}, seed=1)
    with pytest.raises(ValueError):
        K.run_solve("sphere", {"dim": 8, "n_evals": 100}, {"policy": "magic"}, seed=1)
    with pytest.raises(ValueError):
        K.run_solve("sphere", {"dim": 8, "n_evals": 10},
                    {"policy": "random_search"}, seed=1)  # budget < 50


def test_de_param_out_of_range_rejected():
    with pytest.raises(ValueError):
        K.run_solve("sphere", {"dim": 8, "n_evals": 600},
                    {"policy": "differential_evolution", "pop_size": 2, "F": 0.7,
                     "CR": 0.9}, seed=1)
