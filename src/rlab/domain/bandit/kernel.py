"""Bandit research kernel.

Standalone, deterministic runner for one seeded repetition of one bandit
policy on a stochastic K-armed bandit task. This file is copied verbatim into
an isolated workdir by the experiment runtime and executed there; it must
depend only on numpy + the standard library.

Contract
--------
Input : ``run_config.json``
    {"task": "bernoulli"|"gaussian",
     "task_params": {"K": int, "T": int, "gap_min": float, "sigma": float},
     "variant_params": {"policy": str, ...},
     "seed": int}

Output: ``result.json``
    {"status": "ok"|"error", "error": str?,
     "metrics": {...}, "series": {...}, "kernel_wall_ms": float}

Determinism: all randomness flows from ``numpy.random.default_rng(seed)``.
The environment is drawn first, then the policy interacts with it, both from
the same stream. String hashing is pinned by the executor (PYTHONHASHSEED=0).
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def make_environment(rng: np.random.Generator, task: str, params: dict) -> dict:
    K = int(params["K"])
    if K < 2:
        raise ValueError("K must be >= 2")
    if task == "bernoulli":
        gap_min = float(params.get("gap_min", 0.0))
        means = None
        for _ in range(2000):
            candidate = rng.uniform(0.05, 0.95, size=K)
            ordered = np.sort(candidate)[::-1]
            if ordered[0] - ordered[1] >= gap_min:
                means = candidate
                break
        if means is None:
            raise ValueError("could not draw arms satisfying gap_min within 2000 tries")
        return {"type": "bernoulli", "means": means}
    if task == "gaussian":
        sigma = float(params.get("sigma", 1.0))
        means = rng.uniform(0.0, 1.0, size=K)
        return {"type": "gaussian", "means": means, "sigma": sigma}
    raise ValueError(f"unknown task {task!r}")


def pull(env: dict, arm: int, rng: np.random.Generator) -> float:
    mu = env["means"][arm]
    if env["type"] == "bernoulli":
        return float(rng.random() < mu)
    return float(mu + rng.normal(0.0, env["sigma"]))


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------
class Policy:
    def select(self, t: int) -> int:  # noqa: D102 - see subclasses
        raise NotImplementedError

    def update(self, arm: int, reward: float) -> None:
        raise NotImplementedError


class EpsilonGreedy(Policy):
    def __init__(self, k: int, eps: float, rng: np.random.Generator):
        self.k, self.eps, self.rng = k, float(eps), rng
        self.counts = np.zeros(k)
        self.values = np.zeros(k)

    def select(self, t: int) -> int:
        if self.rng.random() < self.eps:
            return int(self.rng.integers(self.k))
        return int(np.argmax(self.values))

    def update(self, arm: int, reward: float) -> None:
        self.counts[arm] += 1
        n = self.counts[arm]
        self.values[arm] += (reward - self.values[arm]) / n


class UCB1(Policy):
    def __init__(self, k: int, c: float, rng: np.random.Generator):
        self.k, self.c, self.rng = k, float(c), rng
        self.counts = np.zeros(k)
        self.values = np.zeros(k)
        self._next_arm = 0

    def select(self, t: int) -> int:
        if self._next_arm < self.k:          # play each arm once first
            return self._next_arm
        bonus = self.c * np.sqrt(np.log(max(t, 2)) / self.counts)
        return int(np.argmax(self.values + bonus))

    def update(self, arm: int, reward: float) -> None:
        if arm == self._next_arm and self._next_arm < self.k:
            self._next_arm += 1
        self.counts[arm] += 1
        n = self.counts[arm]
        self.values[arm] += (reward - self.values[arm]) / n


class UCBTuned(Policy):
    """UCB-Tuned (Auer et al. 2002): tracks empirical reward variance."""

    def __init__(self, k: int, rng: np.random.Generator):
        self.k, self.rng = k, rng
        self.counts = np.zeros(k)
        self.means = np.zeros(k)
        self.sq_sums = np.zeros(k)
        self._next_arm = 0

    def select(self, t: int) -> int:
        if self._next_arm < self.k:
            return self._next_arm
        n_total = max(self.counts.sum(), 2.0)
        var_hat = np.maximum(self.sq_sums / self.counts - self.means ** 2, 0.0)
        bound = var_hat + np.sqrt(2.0 * np.log(n_total) / self.counts)
        return int(np.argmax(self.means + np.sqrt(np.log(n_total) / self.counts * np.minimum(bound, 0.25))))

    def update(self, arm: int, reward: float) -> None:
        if arm == self._next_arm and self._next_arm < self.k:
            self._next_arm += 1
        self.sq_sums[arm] += reward * reward
        self.counts[arm] += 1
        n = self.counts[arm]
        self.means[arm] += (reward - self.means[arm]) / n


class ThompsonBernoulli(Policy):
    """Beta-Bernoulli posterior sampling with configurable prior strength."""

    def __init__(self, k: int, rng: np.random.Generator, prior_strength: float = 1.0):
        self.k, self.rng = k, rng
        s = float(prior_strength)
        self.alpha = np.full(k, s)
        self.beta = np.full(k, s)

    def select(self, t: int) -> int:
        samples = self.rng.beta(self.alpha, self.beta)
        return int(np.argmax(samples))

    def update(self, arm: int, reward: float) -> None:
        if reward > 0:
            self.alpha[arm] += 1
        else:
            self.beta[arm] += 1


class ThompsonGaussian(Policy):
    """Posterior sampling for Gaussian rewards with KNOWN sigma."""

    def __init__(self, k: int, sigma_known: float, rng: np.random.Generator):
        self.k, self.sigma_k, self.rng = k, float(sigma_known), rng
        self.counts = np.zeros(k)
        self.means = np.zeros(k)

    def select(self, t: int) -> int:
        posterior_sigma = self.sigma_k / np.sqrt(np.maximum(self.counts, 1.0))
        samples = self.rng.normal(self.means, posterior_sigma)
        return int(np.argmax(samples))

    def update(self, arm: int, reward: float) -> None:
        self.counts[arm] += 1
        n = self.counts[arm]
        self.means[arm] += (reward - self.means[arm]) / n


class OptimisticGreedy(Policy):
    def __init__(self, k: int, init_value: float, rng: np.random.Generator):
        self.k, self.rng = k, rng
        self.counts = np.zeros(k)
        self.values = np.full(k, float(init_value))

    def select(self, t: int) -> int:
        return int(np.argmax(self.values))

    def update(self, arm: int, reward: float) -> None:
        self.counts[arm] += 1
        n = self.counts[arm]
        self.values[arm] += (reward - self.values[arm]) / n


def build_policy(params: dict, env: dict, rng: np.random.Generator) -> Policy:
    k = len(env["means"])
    name = params["policy"]
    if name == "epsilon_greedy":
        return EpsilonGreedy(k, params.get("eps", 0.1), rng)
    if name == "ucb1":
        return UCB1(k, params.get("c", 1.0), rng)
    if name == "ucb_tuned":
        return UCBTuned(k, rng)
    if name == "thompson_bernoulli":
        return ThompsonBernoulli(k, rng, params.get("prior_strength", 1.0))
    if name == "thompson_gaussian":
        return ThompsonGaussian(k, env.get("sigma", 1.0), rng)
    if name == "optimistic_greedy":
        return OptimisticGreedy(k, params.get("init_value", 1.0), rng)
    raise ValueError(f"unknown policy {name!r}")


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------
MAX_SERIES_POINTS = 256


def run_episode(task: str, task_params: dict, variant_params: dict, seed: int) -> dict:
    if "T" not in task_params:
        raise ValueError("task_params must include horizon 'T'")
    T = int(task_params["T"])
    if T < 10:
        raise ValueError("horizon T must be >= 10")
    if task not in ("bernoulli", "gaussian"):
        raise ValueError(f"unknown task {task!r}")

    rng = np.random.default_rng(seed)
    env = make_environment(rng, task, task_params)
    policy = build_policy(variant_params, env, rng)

    best_mean = float(env["means"].max())
    best_arm = int(env["means"].argmax())

    cum_regret = 0.0
    total_reward = 0.0
    optimal_pulls = 0
    tail_len = min(100, T)
    tail_optimal = 0
    curve = []

    for t in range(T):
        arm = policy.select(t)
        reward = pull(env, arm, rng)
        policy.update(arm, reward)

        cum_regret += best_mean - float(env["means"][arm])
        total_reward += reward
        is_optimal = arm == best_arm
        optimal_pulls += is_optimal
        if t >= T - tail_len and is_optimal:
            tail_optimal += 1
        curve.append(cum_regret)

    metrics = {
        "total_regret": round(float(cum_regret), 6),
        "avg_reward": round(total_reward / T, 6),
        "best_arm_rate_tail": round(tail_optimal / tail_len, 6),
        "optimal_pull_fraction": round(optimal_pulls / T, 6),
    }

    idx = np.linspace(0, T - 1, num=min(MAX_SERIES_POINTS, T)).astype(int)
    series = {
        "cumulative_regret": [round(float(curve[i]), 4) for i in idx],
        "curve_step": int(idx[1] - idx[0]) if len(idx) > 1 else 1,
    }
    return {"metrics": metrics, "series": series}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    start = time.perf_counter()
    try:
        with open("run_config.json", encoding="utf-8") as fh:
            cfg = json.load(fh)
        outcome = run_episode(
            cfg["task"], cfg["task_params"], cfg["variant_params"], int(cfg["seed"])
        )
        result = {"status": "ok", **outcome}
    except Exception as exc:  # structured failure contract
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    result["kernel_wall_ms"] = round((time.perf_counter() - start) * 1000.0, 2)
    with open("result.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
