"""Regression: the hypothesis ladder must explore, not loop.

Revealed by the first overnight demo: the canonical combo key used by the
transfer strategy ('task|T=2000') never matched the memory key recorded by
the director ('bernoulli@2000'), so iterations 5-22 re-ran one identical
experiment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlab.agents import ResearchDirector
from rlab.config import LabConfig
from rlab.events import EventBus
from rlab.sandbox.local import LocalExecutor
from rlab.store import Store


def test_transfer_strategy_cycles_through_combos(tmp_path: Path):
    """Directly exercise the ladder memory to prove combo keys rotate."""
    from rlab.agents.hypothesis import HypothesisAgent, ResearchMemory
    from rlab.domain import get_domain

    cfg = LabConfig(root=tmp_path)
    agent = HypothesisAgent(EventBus(), cfg)
    plugin = get_domain("bandit")
    mem = ResearchMemory()
    mem.champion = None

    # simulate a completed session state where several combos are done
    done = set()
    for task_id, _ in plugin.tasks():
        for b in plugin.budget_options():
            params = plugin.task_defaults(task_id) | b["task_params"]
            done.add(plugin.budget_key(task_id, params))
    mem.combo_tested = done
    draft = agent._transfer(plugin, mem, [])
    assert draft is None, "transfer must be exhausted when all combos are tested"


def test_session_designs_do_not_repeat(tmp_path: Path):
    """An 8-iteration session must contain >= 5 distinct designs."""
    cfg = LabConfig(root=tmp_path, seeds_per_config=6, max_iterations=8,
                    max_parallel_workers=6, bootstrap_iters=250,
                    offline_corpus=True, experiment_timeout_s=45.0,
                    wall_budget_minutes=20)
    store = Store(cfg.db_path)
    original = ResearchDirector._make_executor
    ResearchDirector._make_executor = lambda self: LocalExecutor()
    try:
        director = ResearchDirector(bus=EventBus(), cfg=cfg, store=store)
        ctx = director.start_session("bandit")
        summary = director.run_session(ctx, max_iterations=8)
    finally:
        ResearchDirector._make_executor = original

    exps = store.list_experiments(ctx.session_id)
    signatures = set()
    for e in exps:
        tp = e.config.extra.get("task_params", {})
        sig = (
            e.config.task, tuple(sorted(tp.items())),
            tuple(sorted(e.config.variants.keys())),
        )
        signatures.add(sig)
    assert len(exps) >= 6, f"expected real experiments, got {len(exps)}"
    assert len(signatures) >= 5, (
        f"design diversity collapsed: only {len(signatures)} distinct designs "
        f"across {len(exps)} experiments"
    )
    # wasted iterations (superseded/skipped) must stay a small fraction
    wasted = sum(1 for o in summary["outcomes"]
                 if o["status"] in ("skipped_repeated", "failed"))
    assert wasted <= 3, f"{wasted} iterations wasted on repeated proposals"
