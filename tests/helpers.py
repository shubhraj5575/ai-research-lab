"""Shared test helpers."""

from __future__ import annotations

from rlab.models import Experiment, ExperimentConfig
from rlab.runtime.repro import spec_hash


def make_exp(domain_name: str, task: str, variants: dict, n_seeds: int = 3,
             seed_root: int = 7, task_params: dict | None = None,
             baseline: str | None = None) -> Experiment:
    """Build a well-formed experiment with a correct spec hash."""
    cfg = ExperimentConfig(
        domain=domain_name, task=task, variants=variants,
        baseline=baseline or sorted(variants)[0], n_seeds=n_seeds,
        seed_root=seed_root, budget_label="test",
        extra={"task_params": task_params or {}},
    )
    payload = {
        "domain": cfg.domain, "task": cfg.task,
        "task_params": cfg.extra.get("task_params", {}),
        "variants": cfg.variants, "baseline": cfg.baseline,
        "n_seeds": cfg.n_seeds, "seed_root": cfg.seed_root,
        "budget_label": cfg.budget_label,
    }
    return Experiment(
        id="ex_rt1", session_id="rs_t", hypothesis_id="hy_t", iteration=1,
        config=cfg, spec_hash=spec_hash(payload), code_version="v",
        git_commit="c", env_json={}, dataset_ref={},
    )
