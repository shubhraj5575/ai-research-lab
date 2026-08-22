"""Domain plugin interface.

A *domain* encapsulates a computational research area:

* ``template_source``  – self-contained, deterministic Python runner code that
  executes ONE seeded repetition of ONE variant and writes ``result.json``
* tasks                – environment/problem families inside the domain
* baseline             – the reference variant others must beat
* knobs                – tunable parameters the hypothesis agent may sweep
* starter hypotheses   – first hypotheses when a session begins

Everything the agents know about a domain comes from this interface, so new
research areas can be added without touching the orchestration logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Knob:
    """A tunable parameter with candidate values for sensitivity sweeps."""

    name: str
    values: tuple[Any, ...]
    applies_to_policies: frozenset[str] | None = None  # None = all variants


@dataclass(frozen=True)
class HypothesisDraft:
    """A hypothesis proposal with an optional concrete experiment sketch.

    The structured fields let the designer translate the draft into an
    ExperimentConfig mechanically; the prose fields document the science.
    """

    claim: str
    reasoning: str
    expected_result: str
    falsification_condition: str
    required_experiment: str
    predicted_variant: str | None = None       # label of the variant expected to win
    suggested_task: str | None = None
    suggested_task_params: dict[str, Any] | None = None
    suggested_variants: dict[str, dict[str, Any]] | None = None   # label -> params
    suggested_seeds: int | None = None
    strategy: str = "starter"                  # which agent strategy produced it


class DomainPlugin(ABC):
    name: str = ""
    primary_metric: str = ""          # e.g. "total_regret"
    direction: str = "minimize"       # or "maximize"

    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def default_question(self) -> str: ...

    @abstractmethod
    def tasks(self) -> list[tuple[str, str]]:      # [(task_id, human description)]
        ...

    @abstractmethod
    def task_defaults(self, task_id: str) -> dict[str, Any]:
        """Environment parameters used unless overridden."""

    @abstractmethod
    def budget_options(self) -> list[dict[str, Any]]:
        """Discrete budget levels (e.g. horizons / eval counts)."""

    @abstractmethod
    def baseline_variant(self) -> dict[str, Any]:
        """Params of the mandatory baseline variant."""

    @abstractmethod
    def default_variant_params(self, policy: str) -> dict[str, Any]:
        """Sensible mid-range params for a policy/solver family."""

    @abstractmethod
    def difficulty_axes(self) -> dict[str, list[Any]]:
        """Escalation axes for stress-testing, e.g. {'gap_min': [...]}."""

    @abstractmethod
    def validate_variant(self, params: dict[str, Any]) -> None:
        """Raise ValueError on malformed variant params."""

    @abstractmethod
    def variant_label(self, params: dict[str, Any]) -> str:
        """Stable, readable label like 'ucb1@c=1.00'."""

    @abstractmethod
    def knobs(self) -> list[Knob]: ...

    @abstractmethod
    def starter_hypotheses(self) -> list[HypothesisDraft]: ...

    @abstractmethod
    def literature_queries(self) -> list[str]: ...

    @abstractmethod
    def kernel_path(self) -> Path:
        """Path of the standalone kernel module (bundled verbatim)."""

    # ------------------------------------------------------------------
    def kernel_source(self) -> str:
        return self.kernel_path().read_text(encoding="utf-8")

    def budget_key(self, task_id: str, task_params: dict[str, Any]) -> str:
        """Canonical identity of a (task, budget) combination.

        Derived from the parameter names that budget_options actually vary,
        NOT from display labels, so different components agree on it.
        """
        budget_names: set[str] = set()
        for opt in self.budget_options():
            budget_names |= set(opt.get("task_params", {}))
        parts = sorted(f"{k}={task_params[k]}" for k in budget_names
                       if k in task_params)
        return f"{task_id}|{','.join(parts)}"

    def code_version_hash(self) -> str:
        from ..ids import short_hash

        return short_hash(self.kernel_source(), n=32)

    def dataset_ref(self, task_id: str, task_params: dict[str, Any],
                    seed_root: int) -> dict[str, Any]:
        return {
            "kind": "synthetic-procedural",
            "domain": self.name,
            "task": task_id,
            "params": task_params,
            "seed_root": seed_root,
            "note": "environment derived from seed_root inside the kernel; no external data",
        }
