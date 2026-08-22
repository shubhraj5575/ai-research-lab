"""Derivative-free optimization research kernel.

Runs ONE seeded repetition of ONE solver on a classic benchmark function
under a strict function-evaluation budget. Standalone: numpy + stdlib only.

Benchmark functions (all with known global optimum):
    sphere(d)      f*(x)=0        at x=0          box [-5, 5]
    rosenbrock(d)  f*=0           at x=1          box [-2.048, 2.048]
    rastrigin(d)   f*=0           at x=0          box [-5, 5]
    ackley(d)      f*=0           at x=0          box [-5, 5]

Budget accounting: EVERY objective call counts, including population
initialization. Solvers stop exactly at ``n_evals``.
"""

from __future__ import annotations

import json
import math
import sys
import time

import numpy as np


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------
def sphere(x: np.ndarray) -> float:
    return float(np.sum(x * x))


def rosenbrock(x: np.ndarray) -> float:
    return float(np.sum(100.0 * (x[1:] - x[:-1] ** 2) ** 2 + (1.0 - x[:-1]) ** 2))


def rastrigin(x: np.ndarray) -> float:
    return float(10.0 * len(x) + np.sum(x * x - 10.0 * np.cos(2.0 * np.pi * x)))


def _ackley(x: np.ndarray) -> float:
    d = len(x)
    s1 = -0.2 * math.sqrt(np.sum(x * x) / d)
    s2 = np.sum(np.cos(2.0 * np.pi * x)) / d
    return float(-20.0 * math.exp(s1) - math.exp(s2) + 20.0 + math.e)


PROBLEMS = {
    "sphere": {"fn": sphere, "f_star": 0.0, "bounds": (-5.0, 5.0), "tol": 1e-3},
    "rosenbrock": {"fn": rosenbrock, "f_star": 0.0, "bounds": (-2.048, 2.048), "tol": 0.5},
    "rastrigin": {"fn": rastrigin, "f_star": 0.0, "bounds": (-5.0, 5.0), "tol": 1.0},
    "ackley": {"fn": _ackley, "f_star": 0.0, "bounds": (-5.0, 5.0), "tol": 0.5},
}


# ---------------------------------------------------------------------------
# Budgeted objective wrapper
# ---------------------------------------------------------------------------
class Budget:
    def __init__(self, n_evals: int):
        self.remaining = int(n_evals)
        self.used = 0

    def call(self, fn, x: np.ndarray) -> float | None:
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        self.used += 1
        return fn(x)


# ---------------------------------------------------------------------------
# Solvers (each returns (best_x, best_f, history_of_best))
# ---------------------------------------------------------------------------
def random_search(budget: Budget, problem: dict, dim: int, rng, params: dict):
    lo, hi = problem["bounds"]
    best_x, best_f = None, math.inf
    history = []
    while budget.remaining > 0:
        x = rng.uniform(lo, hi, size=dim)
        f = budget.call(problem["fn"], x)
        assert f is not None
        if f < best_f:
            best_f, best_x = f, x.copy()
        history.append(best_f)
    return best_x, best_f, history


def _clip(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.minimum(np.maximum(x, lo), hi)


def hill_climb(budget: Budget, problem: dict, dim: int, rng, params: dict):
    lo, hi = problem["bounds"]
    sigma = float(params.get("sigma", 0.3))
    x = rng.uniform(lo, hi, size=dim)
    best_f = budget.call(problem["fn"], x)
    assert best_f is not None
    history = [best_f]
    while budget.remaining > 0:
        cand = _clip(x + rng.normal(0.0, sigma, size=dim), lo, hi)
        f = budget.call(problem["fn"], cand)
        assert f is not None
        if f < best_f:
            x, best_f = cand, f
        history.append(best_f)
    return x, best_f, history


def hill_climb_adaptive(budget: Budget, problem: dict, dim: int, rng, params: dict):
    """(1+1)-ES with Rechenberg's 1/5 success rule."""
    lo, hi = problem["bounds"]
    sigma = float(params.get("sigma0", 0.5))
    window = max(4, int(params.get("adapt_window", 20)))
    target = float(params.get("target_rate", 0.2))
    delta = 0.25
    x = rng.uniform(lo, hi, size=dim)
    best_f = budget.call(problem["fn"], x)
    assert best_f is not None
    history = [best_f]
    successes = 0
    trials = 0
    span = hi - lo
    while budget.remaining > 0:
        cand = _clip(x + rng.normal(0.0, sigma * span / 10.0, size=dim), lo, hi)
        f = budget.call(problem["fn"], cand)
        assert f is not None
        trials += 1
        if f < best_f:
            x, best_f = cand, f
            successes += 1
        history.append(best_f)
        if trials >= window:
            rate = successes / trials
            sigma *= math.exp(delta) if rate > target else math.exp(-delta)
            sigma = min(max(sigma, 1e-4), 10.0)
            successes = trials = 0
    return x, best_f, history


def simulated_annealing(budget: Budget, problem: dict, dim: int, rng, params: dict):
    lo, hi = problem["bounds"]
    t0 = float(params.get("t0", 1.0))
    alpha = float(params.get("alpha", 0.999))
    sigma_scale = float(params.get("sigma_scale", 0.05)) * (hi - lo)
    x = rng.uniform(lo, hi, size=dim)
    cur_f = budget.call(problem["fn"], x)
    assert cur_f is not None
    best_x, best_f = x.copy(), cur_f
    temperature = t0
    history = [cur_f]
    step = 0
    span = hi - lo
    while budget.remaining > 0:
        step += 1
        cand = _clip(x + rng.normal(0.0, sigma_scale * span, size=dim), lo, hi)
        f = budget.call(problem["fn"], cand)
        assert f is not None
        diff = f - cur_f
        if diff <= 0 or rng.random() < math.exp(-diff / max(temperature, 1e-300)):
            x, cur_f = cand, f
            if f < best_f:
                best_x, best_f = cand.copy(), f
        history.append(best_f)
        temperature = t0 * (alpha ** step)
    return best_x, best_f, history


def differential_evolution(budget: Budget, problem: dict, dim: int, rng, params: dict):
    lo, hi = problem["bounds"]
    pop_size = int(params.get("pop_size", 20))
    F = float(params.get("F", 0.7))
    CR = float(params.get("CR", 0.9))

    def rand_point() -> np.ndarray:
        return rng.uniform(lo, hi, size=dim)

    pop = []
    fit = []
    for _ in range(pop_size):
        if budget.remaining <= 0:
            break
        x = rand_point()
        f = budget.call(problem["fn"], x)
        assert f is not None
        pop.append(x)
        fit.append(f)
    if not pop:
        raise ValueError("budget too small for DE initialization")

    history = [min(fit)]
    while budget.remaining > 0:
        i_best = int(np.argmin(fit))
        history.append(min(fit))
        for i in range(pop_size):
            if budget.remaining <= 0:
                break
            idxs = [j for j in range(pop_size) if j != i]
            r1, r2, r3 = (pop[j] for j in rng.choice(idxs, size=3, replace=False))
            cross = rng.random(dim) < CR
            if not cross.any():
                cross[rng.integers(dim)] = True
            trial = _clip(np.where(cross, r1 + F * (r2 - r3), pop[i]), lo, hi)
            f_trial = budget.call(problem["fn"], trial)
            assert f_trial is not None
            if f_trial <= fit[i]:
                pop[i], fit[i] = trial, f_trial
                if i == i_best:
                    history[-1] = min(fit)
    best_i = int(np.argmin(fit))
    return pop[best_i], fit[best_i], history


SOLVERS = {
    "random_search": random_search,
    "hill_climb": hill_climb,
    "hill_climb_adaptive": hill_climb_adaptive,
    "simulated_annealing": simulated_annealing,
    "differential_evolution": differential_evolution,
}

SOLVER_PARAM_RANGES = {
    "random_search": {},
    "hill_climb": {"sigma": (0.001, 2.0)},
    "hill_climb_adaptive": {"sigma0": (0.001, 2.0), "adapt_window": (4, 100),
                            "target_rate": (0.05, 0.6)},
    "simulated_annealing": {"t0": (0.01, 100.0), "alpha": (0.9, 0.99999),
                            "sigma_scale": (0.001, 0.5)},
    "differential_evolution": {"pop_size": (4, 200), "F": (0.1, 1.5), "CR": (0.0, 1.0)},
}


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------
MAX_SERIES_POINTS = 256


def run_solve(problem_id: str, task_params: dict, variant_params: dict, seed: int) -> dict:
    if problem_id not in PROBLEMS:
        raise ValueError(f"unknown problem {problem_id!r}")
    solver_name = variant_params["policy"]
    if solver_name not in SOLVERS:
        raise ValueError(f"unknown solver {solver_name!r}")

    problem = PROBLEMS[problem_id]
    dim = int(task_params.get("dim", 8))
    n_evals = int(task_params["n_evals"])
    if n_evals < 50:
        raise ValueError("budget n_evals must be >= 50")
    if dim < 1 or dim > 64:
        raise ValueError("dim must be in [1, 64]")

    # range-validate any explicitly provided params (missing ones fall back to
    # documented solver defaults); the plugin layer enforces which are required
    for key, (lo_v, hi_v) in SOLVER_PARAM_RANGES[solver_name].items():
        value = variant_params.get(key)
        if value is None:
            continue
        if not lo_v <= float(value) <= hi_v:
            raise ValueError(f"param {key}={value} outside [{lo_v}, {hi_v}]")

    rng = np.random.default_rng(seed)
    budget = Budget(n_evals)
    t_start = time.perf_counter()
    best_x, best_f, history = SOLVERS[solver_name](budget, problem, dim, rng, variant_params)
    kernel_ms = (time.perf_counter() - t_start) * 1000.0

    regret = max(0.0, best_f - problem["f_star"])
    metrics = {
        "final_regret": round(float(regret), 8),
        "success": float(regret <= problem["tol"]),
        "auc_log_regret": round(
            float(np.mean(np.log10(np.maximum(np.asarray(history), 1e-12)))), 6),
        "evals_used": float(budget.used),
    }
    hist_arr = np.asarray(history, dtype=float)
    idx = np.linspace(0, len(hist_arr) - 1, num=min(MAX_SERIES_POINTS, len(hist_arr))).astype(int)
    series = {
        "best_so_far": [round(float(hist_arr[i]), 8) for i in idx],
        "curve_step": int(max(1, len(hist_arr) // MAX_SERIES_POINTS)),
    }
    return {"metrics": metrics, "series": series, "kernel_wall_ms": round(kernel_ms, 2)}


def main() -> int:
    start = time.perf_counter()
    try:
        with open("run_config.json", encoding="utf-8") as fh:
            cfg = json.load(fh)
        outcome = run_solve(
            cfg["task"], cfg["task_params"], cfg["variant_params"], int(cfg["seed"])
        )
        series = outcome.pop("series")
        result = {"status": "ok", "metrics": outcome["metrics"],
                  "series": series,
                  "kernel_wall_ms": outcome.get("kernel_wall_ms", 0.0)}
    except Exception as exc:
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    result["kernel_wall_ms"] = result.get("kernel_wall_ms",
                                          round((time.perf_counter() - start) * 1000.0, 2))
    with open("result.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
