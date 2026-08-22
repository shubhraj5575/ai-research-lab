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

        from ..runtime.repro import spec_hash as _spec_hash

        baseline_label = None
        base_params = plugin.baseline_variant()
        for label, params in variants.items():
            if params == base_params:
                baseline_label = label
                break
        # config identity EXCLUDES the seed root: identical comparisons with
        # different seeds are replications, and strategies must not loop on them
        config_key = _spec_hash({
            "domain": plugin.name, "task": task, "task_params": task_params,
            "variants": variants,
            "baseline": baseline_label or sorted(variants)[0],
            "n_seeds": n_seeds,
        })
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
                "budget_key": plugin.budget_key(task, task_params),
                "config_key": config_key,
            },
        )
        self.announce(session_id, "designed", task=task,
                      variants=len(variants), seeds=n_seeds)
        return Design(config=config)
