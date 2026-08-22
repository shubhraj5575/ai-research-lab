"""Bandit domain plugin: stochastic multi-armed bandits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import DomainPlugin, HypothesisDraft, Knob


class BanditDomain(DomainPlugin):
    name = "bandit"
    primary_metric = "total_regret"
    direction = "minimize"

    def description(self) -> str:
        return (
            "Stochastic K-armed bandits. Policies balance exploration/exploitation "
            "under a fixed horizon; the primary metric is cumulative regret against "
            "the best arm's mean reward."
        )

    def default_question(self) -> str:
        return (
            "Which exploration policy minimizes cumulative regret on stochastic "
            "K-armed bandits, and how do policy parameters, arm difficulty and "
            "horizon length change that ranking?"
        )

    # ------------------------------------------------------------------
    def tasks(self) -> list[tuple[str, str]]:
        return [
            ("bernoulli", "Bernoulli arms with means in [0.05, 0.95]"),
            ("gaussian", "Gaussian arms, means in [0, 1], known sigma=1"),
        ]

    def task_defaults(self, task_id: str) -> dict[str, Any]:
        base = {"K": 10, "T": 5000}
        if task_id == "bernoulli":
            return base | {"gap_min": 0.0}
        if task_id == "gaussian":
            return base | {"sigma": 1.0}
        raise ValueError(f"unknown task {task_id!r}")

    def budget_options(self) -> list[dict[str, Any]]:
        # horizons are the compute budget of a bandit episode
        return [{"label": "T=2000", "task_params": {"T": 2000}},
                {"label": "T=10000", "task_params": {"T": 10000}}]

    def baseline_variant(self) -> dict[str, Any]:
        # epsilon-greedy at eps=0.1 is the canonical simple baseline
        return {"policy": "epsilon_greedy", "eps": 0.1}

    def default_variant_params(self, policy: str) -> dict[str, Any]:
        defaults = {
            "epsilon_greedy": {"eps": 0.1},
            "ucb1": {"c": 1.0},
            "ucb_tuned": {},
            "thompson_bernoulli": {"prior_strength": 1.0},
            "thompson_gaussian": {},
            "optimistic_greedy": {"init_value": 1.0},
        }
        if policy not in defaults:
            raise ValueError(f"unknown policy {policy!r}")
        return {"policy": policy} | defaults[policy]

    def difficulty_axes(self) -> dict[str, list[Any]]:
        return {
            "gap_min": [0.0, 0.1, 0.25],
            "K": [5, 10, 20],
            "T": [2000, 5000, 10000],
        }

    # ------------------------------------------------------------------
    POLICY_PARAM_RANGES: dict[str, dict[str, tuple[float, float]]] = {
        "epsilon_greedy": {"eps": (0.0, 1.0)},
        "ucb1": {"c": (0.01, 10.0)},
        "ucb_tuned": {},
        "thompson_bernoulli": {"prior_strength": (0.5, 20.0)},
        "thompson_gaussian": {},
        "optimistic_greedy": {"init_value": (0.0, 2.0)},
    }

    def validate_variant(self, params: dict[str, Any]) -> None:
        policy = params.get("policy")
        if policy not in self.POLICY_PARAM_RANGES:
            raise ValueError(f"unknown policy {policy!r}")
        for key, (lo, hi) in self.POLICY_PARAM_RANGES[policy].items():
            if key not in params:
                raise ValueError(f"policy {policy!r} requires param {key!r}")
            value = float(params[key])
            if not lo <= value <= hi:
                raise ValueError(f"{key}={value} outside [{lo}, {hi}]")
        extra = set(params) - {"policy"} - set(self.POLICY_PARAM_RANGES[policy])
        if extra:
            raise ValueError(f"unexpected params for {policy!r}: {sorted(extra)}")

    def variant_label(self, params: dict[str, Any]) -> str:
        policy = params["policy"]
        extras = {k: v for k, v in params.items() if k != "policy"}
        if not extras:
            return policy
        inner = ",".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
                         for k, v in sorted(extras.items()))
        return f"{policy}@{inner}"

    # ------------------------------------------------------------------
    def knobs(self) -> list[Knob]:
        return [
            Knob("eps", (0.01, 0.03, 0.1, 0.25, 0.4), frozenset({"epsilon_greedy"})),
            Knob("c", (0.25, 0.5, 1.0, 2.0), frozenset({"ucb1"})),
            Knob("prior_strength", (0.5, 1.0, 5.0), frozenset({"thompson_bernoulli"})),
            Knob("init_value", (0.5, 1.0), frozenset({"optimistic_greedy"})),
        ]

    def policy_families(self) -> list[str]:
        return list(self.POLICY_PARAM_RANGES.keys())

    # ------------------------------------------------------------------
    def starter_hypotheses(self) -> list[HypothesisDraft]:
        # Starters carry explicit experiment sketches so the designer never
        # has to parse prose to build configurations.
        t_mid = {"K": 10, "T": 5000, "gap_min": 0.1}
        h1_variants = {
            "ucb1@c=1": {"policy": "ucb1", "c": 1.0},
            "epsilon_greedy@eps=0.1": {"policy": "epsilon_greedy", "eps": 0.1},
        }
        t_hard_short = {"K": 10, "T": 2000, "gap_min": 0.2}
        h2_variants = {
            "thompson_bernoulli@prior_strength=1": {"policy": "thompson_bernoulli",
                                                    "prior_strength": 1.0},
            "ucb1@c=1": {"policy": "ucb1", "c": 1.0},
        }
        return [
            HypothesisDraft(
                claim=(
                    "On Bernoulli bandits with a visible gap (gap_min >= 0.1), "
                    "UCB1(c=1) achieves lower mean total regret than the "
                    "epsilon-greedy(0.1) baseline at horizon T=5000."
                ),
                reasoning=(
                    "UCB1's exploration bonus shrinks as O(sqrt(log t / n_i)) while "
                    "fixed-eps keeps paying linear regret eps*Delta*T forever; "
                    "literature predicts asymptotic superiority which should already "
                    "be measurable at T=5000."
                ),
                expected_result="Mean total regret of UCB1 < baseline by >= 30%, p < 0.05.",
                falsification_condition=(
                    "UCB1 mean regret >= baseline mean regret, or paired-bootstrap "
                    "CI of the difference includes zero."
                ),
                required_experiment=(
                    "Bernoulli task, K=10, T=5000, gap_min=0.1; UCB1(c=1) vs "
                    "epsilon_greedy(eps=0.1); n>=30 paired seeds per variant."
                ),
                predicted_variant="ucb1@c=1",
                suggested_task="bernoulli",
                suggested_task_params=t_mid,
                suggested_variants=h1_variants,
                suggested_seeds=30,
            ),
            HypothesisDraft(
                claim=(
                    "Thompson sampling (Beta prior) dominates UCB1(c=1) on hard-gap "
                    "Bernoulli bandits (gap_min >= 0.2) at short horizons (T=2000)."
                ),
                reasoning=(
                    "Posterior sampling adapts exploration to remaining uncertainty; "
                    "on hard gaps where few arms look similar, adaptive methods are "
                    "reported to reach near-oracle behavior faster than UCB bonuses."
                ),
                expected_result="Thompson mean regret < UCB1 mean regret by >= 15%.",
                falsification_condition=(
                    "No significant regret reduction (paired-bootstrap CI includes "
                    "zero after Holm correction across the comparison family)."
                ),
                required_experiment=(
                    "Bernoulli task, K=10, T=2000, gap_min=0.2; thompson_bernoulli vs "
                    "ucb1(c=1); n>=30 paired seeds."
                ),
                predicted_variant="thompson_bernoulli@prior_strength=1",
                suggested_task="bernoulli",
                suggested_task_params=t_hard_short,
                suggested_variants=h2_variants,
                suggested_seeds=30,
            ),
        ]

    def literature_queries(self) -> list[str]:
        return [
            "multi-armed bandit regret minimization",
            "UCB Thompson sampling comparison",
            "exploration exploitation trade-off stochastic bandits",
        ]

    def kernel_path(self) -> Path:
        return Path(__file__).parent / "kernel.py"
