"""Derivative-free optimization domain plugin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import DomainPlugin, HypothesisDraft, Knob


class OptimDomain(DomainPlugin):
    name = "optim"
    primary_metric = "final_regret"
    direction = "minimize"

    def description(self) -> str:
        return (
            "Derivative-free optimization of classic benchmark functions under a "
            "fixed function-evaluation budget. Primary metric is final regret "
            "(best objective found minus global optimum)."
        )

    def default_question(self) -> str:
        return (
            "Which derivative-free solver minimizes expected final regret within a "
            "fixed evaluation budget across unimodal and multimodal landscapes, and "
            "how do solver hyperparameters and dimensionality change the ranking?"
        )

    def tasks(self) -> list[tuple[str, str]]:
        return [
            ("sphere", "convex bowl; tests exploitation speed"),
            ("rosenbrock", "banana valley; tests path following"),
            ("rastrigin", "heavily multimodal; tests exploration"),
            ("ackley", "multimodal with narrow funnel"),
        ]

    def task_defaults(self, task_id: str) -> dict[str, Any]:
        if task_id not in {t for t, _ in self.tasks()}:
            raise ValueError(f"unknown task {task_id!r}")
        return {"dim": 8, "n_evals": 4000}

    def budget_options(self) -> list[dict[str, Any]]:
        return [{"label": "evals=1500", "task_params": {"n_evals": 1500}},
                {"label": "evals=6000", "task_params": {"n_evals": 6000}}]

    def baseline_variant(self) -> dict[str, Any]:
        return {"policy": "random_search"}

    def default_variant_params(self, solver: str) -> dict[str, Any]:
        from .kernel import SOLVER_PARAM_RANGES

        defaults = {
            "random_search": {},
            "hill_climb": {"sigma": 0.3},
            "hill_climb_adaptive": {"sigma0": 0.5},
            "simulated_annealing": {"t0": 1.0, "alpha": 0.995, "sigma_scale": 0.05},
            "differential_evolution": {"pop_size": 32, "F": 0.7, "CR": 0.9},
        }
        if solver not in defaults:
            raise ValueError(f"unknown solver {solver!r}")
        params = {"policy": solver} | defaults[solver]
        self.validate_variant(params)
        return params

    def difficulty_axes(self) -> dict[str, list[Any]]:
        return {
            "task": ["sphere", "rosenbrock", "ackley", "rastrigin"],
            "dim": [2, 8],
            "n_evals": [1500, 4000, 6000],
        }

    def validate_variant(self, params: dict[str, Any]) -> None:
        from .kernel import SOLVER_PARAM_RANGES

        solver = params.get("policy")
        if solver not in SOLVER_PARAM_RANGES:
            raise ValueError(f"unknown solver {solver!r}")
        ranges = SOLVER_PARAM_RANGES[solver]
        for key, value in params.items():
            if key == "policy":
                continue
            if key not in ranges:
                raise ValueError(f"unexpected param {key!r} for solver {solver!r}")
            lo_v, hi_v = ranges[key]
            if not lo_v <= float(value) <= hi_v:
                raise ValueError(f"{key}={value} outside [{lo_v}, {hi_v}]")

    def variant_label(self, params: dict[str, Any]) -> str:
        solver = params["policy"]
        extras = {k: v for k, v in params.items() if k != "policy"}
        if not extras:
            return solver
        inner = ",".join(f"{k}={v:g}" for k, v in sorted(extras.items()))
        return f"{solver}@{inner}"

    def knobs(self) -> list[Knob]:
        return [
            Knob("t0", (0.5, 1.0, 2.0, 5.0), frozenset({"simulated_annealing"})),
            Knob("alpha", (0.99, 0.995, 0.999, 0.9995), frozenset({"simulated_annealing"})),
            Knob("pop_size", (8, 16, 32, 64), frozenset({"differential_evolution"})),
            Knob("F", (0.4, 0.7, 1.0), frozenset({"differential_evolution"})),
            Knob("CR", (0.3, 0.6, 0.9), frozenset({"differential_evolution"})),
            Knob("sigma0", (0.2, 0.5, 1.0), frozenset({"hill_climb_adaptive"})),
            Knob("sigma", (0.1, 0.3, 0.6), frozenset({"hill_climb"})),
        ]

    def starter_hypotheses(self) -> list[HypothesisDraft]:
        sa_params = {"policy": "simulated_annealing", "t0": 1.0, "alpha": 0.995,
                     "sigma_scale": 0.05}
        de_params = {"policy": "differential_evolution", "pop_size": 32, "F": 0.7,
                     "CR": 0.9}
        return [
            HypothesisDraft(
                claim=(
                    "Simulated annealing with geometric cooling (t0=1, alpha=0.995) "
                    "achieves lower mean final regret than random search on the "
                    "unimodal sphere function (dim=8) at budget evals=4000."
                ),
                reasoning=(
                    "SA's biased hill descent should exploit the smooth gradient "
                    "structure that random search ignores; at dim=8 the basin of "
                    "attraction around 0 occupies a measurable fraction of the box."
                ),
                expected_result="SA mean final regret < 50% of random-search baseline.",
                falsification_condition=(
                    "SA regret >= baseline regret or CI includes zero."
                ),
                required_experiment=(
                    "sphere dim=8 n_evals=4000; simulated_annealing(t0=1,alpha=0.995) "
                    "vs random_search; n>=30 paired seeds."
                ),
                predicted_variant="simulated_annealing@alpha=0.995,sigma_scale=0.05,t0=1",
                suggested_task="sphere",
                suggested_task_params={"dim": 8, "n_evals": 4000},
                suggested_variants={
                    "simulated_annealing@alpha=0.995,sigma_scale=0.05,t0=1": sa_params,
                    "random_search": {"policy": "random_search"},
                },
                suggested_seeds=30,
            ),
            HypothesisDraft(
                claim=(
                    "Differential evolution outperforms simulated annealing on the "
                    "multimodal rastrigin landscape (dim=8, evals=4000)."
                ),
                reasoning=(
                    "Population methods maintain diversity across local optima; SA "
                    "with geometric cooling freezes into one basin on rugged terrain."
                ),
                expected_result="DE mean regret < SA mean regret by >= 20%.",
                falsification_condition=(
                    "No significant difference after Holm correction, or DE worse."
                ),
                required_experiment=(
                    "rastrigin dim=8 n_evals=4000; differential_evolution(pop=32) vs "
                    "simulated_annealing(t0=1, alpha=0.995); n>=30 paired seeds."
                ),
                predicted_variant=("differential_evolution@CR=0.9,F=0.7,pop_size=32"),
                suggested_task="rastrigin",
                suggested_task_params={"dim": 8, "n_evals": 4000},
                suggested_variants={
                    "differential_evolution@CR=0.9,F=0.7,pop_size=32": de_params,
                    "simulated_annealing@alpha=0.995,sigma_scale=0.05,t0=1": sa_params,
                },
                suggested_seeds=30,
            ),
        ]

    def literature_queries(self) -> list[str]:
        return [
            "simulated annealing cooling schedule comparison",
            "differential evolution parameter tuning",
            "derivative free optimization benchmark regret",
        ]

    def kernel_path(self) -> Path:
        return Path(__file__).parent / "kernel.py"
