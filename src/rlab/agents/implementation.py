"""Implementation Agent: turns designs into persisted experiment records.

The heavy lifting of code generation happens in the runtime (kernel bundles);
this agent is the review gate: it validates configuration integrity, records
provenance (git commit, environment snapshot, dataset derivation), computes
the spec hash used for deduplication and reproducibility, and only then does
an experiment become runnable.
"""

from __future__ import annotations

import time

from ..config import LabConfig
from ..domain.base import DomainPlugin
from ..events import EventBus
from ..models import Experiment, ExperimentConfig, ExperimentStatus
from ..runtime.provenance import current_git_commit, environment_snapshot
from ..runtime.repro import spec_hash
from ..store import Store
from .base import Agent


class ImplementationAgent(Agent):
    role = "implementation"

    def __init__(self, bus: EventBus, cfg: LabConfig, store: Store):
        super().__init__(bus)
        self.cfg = cfg
        self.store = store

    def implement(self, session_id: str, iteration: int,
                  hypothesis_id: str, config: ExperimentConfig,
                  plugin: DomainPlugin) -> Experiment:
        from ..ids import new_id

        env = environment_snapshot()
        commit = current_git_commit()
        payload = {
            "domain": config.domain,
            "task": config.task,
            "task_params": config.extra.get("task_params", {}),
            "variants": config.variants,
            "baseline": config.baseline,
            "n_seeds": config.n_seeds,
            "seed_root": config.seed_root,
            "budget_label": config.budget_label,
        }
        exp = Experiment(
            id=new_id("experiment"),
            session_id=session_id,
            hypothesis_id=hypothesis_id,
            iteration=iteration,
            config=config,
            spec_hash=spec_hash(payload),
            code_version=plugin.code_version_hash(),
            git_commit=commit,
            env_json=env,
            dataset_ref=plugin.dataset_ref(config.task,
                                           config.extra.get("task_params", {}),
                                           config.seed_root),
            status=ExperimentStatus.PENDING,
        )
        self.store.save_experiment(exp)
        self.announce(session_id, "implemented", experiment_id=exp.id,
                      spec_hash=exp.spec_hash[:12],
                      git_commit=commit[:10] if commit != "unknown" else "unknown")
        return exp

    @staticmethod
    def now() -> float:
        return time.time()
