"""Experiment Designer: hypothesis drafts -> concrete, deduped ExperimentConfigs."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import LabConfig
from ..domain.base import DomainPlugin
from ..events import EventBus
from ..models import ExperimentConfig
from .base import Agent


class DesignError(ValueError):
    pass


@dataclass
class Design:
    config: ExperimentConfig
    notes: str = ""


class ExperimentDesigner(Agent):
    role = "designer"

    def __init__(self, bus: EventBus, cfg: LabConfig):
        super().__init__(bus)
        self.cfg = cfg

    def design(self, session_id: str, plugin: DomainPlugin,
               draft) -> Design:
        """Translate a HypothesisDraft into a validated ExperimentConfig."""
        task = draft.suggested_task or plugin.tasks()[0][0]
        task_params = dict(draft.suggested_task_params or plugin.task_defaults(task))
        variants = dict(draft.suggested_variants or {})
        if not variants:
            raise DesignError("draft carries no suggested variants")

        # validate every variant through the domain contract
        for label, params in variants.items():
            try:
                plugin.validate_variant(params)
            except ValueError as exc:
                raise DesignError(f"variant {label!r} invalid: {exc}") from exc

        n_seeds = int(draft.suggested_seeds or self.cfg.seeds_per_config)
        n_seeds = max(4, min(n_seeds, 200))

        baseline_label = None
        base_params = plugin.baseline_variant()
        for label, params in variants.items():
            if params == base_params:
                baseline_label = label
                break
        if draft.predicted_variant is not None and len(variants) > 1 and baseline_label is None:
            # comparisons need a reference point; include baseline unless this
            # is a pure head-to-head where champion acts as the reference
            pass

        budget_label = f"{task}@{task_params.get('T', task_params.get('n_evals', '?'))}"
        config = ExperimentConfig(
            domain=plugin.name,
            task=task,
            variants=variants,
            baseline=baseline_label or sorted(variants)[0],
            n_seeds=n_seeds,
            seed_root=0,  # filled by orchestrator (session-derived)
            budget_label=budget_label,
            extra={
                "task_params": task_params,
                "primary_metric": plugin.primary_metric,
                "direction": plugin.direction,
            },
        )
        self.announce(session_id, "designed", task=task,
                      variants=len(variants), seeds=n_seeds)
        return Design(config=config)
