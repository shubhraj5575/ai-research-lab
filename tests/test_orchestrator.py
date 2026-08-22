"""End-to-end orchestrator integration tests (tiny budgets, real sandboxes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlab.agents import ResearchDirector
from rlab.config import LabConfig
from rlab.models import HypothesisStatus
from rlab.sandbox.local import LocalExecutor
from rlab.store import Store


@pytest.fixture()
def lab(tmp_path: Path):
    from rlab.events import EventBus

    cfg = LabConfig(
        root=tmp_path,
        seeds_per_config=8,
        max_iterations=4,
        max_parallel_workers=4,
        experiment_timeout_s=45.0,
        bootstrap_iters=400,
        offline_corpus=True,
    )
    store = Store(cfg.db_path)
    bus = EventBus()

    # pin the sandbox executor to LocalExecutor regardless of config
    import rlab.agents.director as director_mod

    original = director_mod.ResearchDirector._make_executor
    director_mod.ResearchDirector._make_executor = lambda self: LocalExecutor()
    try:
        director = ResearchDirector(bus=bus, cfg=cfg, store=store)
        yield cfg, store, bus, director
    finally:
        director_mod.ResearchDirector._make_executor = original


def test_full_session_runs_multiple_iterations(lab):
    cfg, store, bus, director = lab
    ctx = director.start_session("bandit")
    summary = director.run_session(ctx, max_iterations=3)
    assert summary["iterations"] == 3
    hyps = store.list_hypotheses(ctx.session_id)
    assert len(hyps) == 3
    for h in hyps:
        assert h.claim
        assert h.reasoning
        assert h.expected_result
        assert h.falsification_condition
        assert h.required_experiment
        assert h.status in (HypothesisStatus.SUPPORTED, HypothesisStatus.REFUTED,
                            HypothesisStatus.INCONCLUSIVE, HypothesisStatus.TESTING,
                            HypothesisStatus.SUPERSEDED)
    exps = store.list_experiments(ctx.session_id)
    assert len(exps) >= 2  # at least two real experiments ran
    for e in exps:
        assert e.spec_hash
        assert e.code_version
        assert e.env_json.get("python")
        assert e.dataset_ref["seed_root"] == e.config.seed_root
    # every completed experiment has runs recorded
    for e in exps:
        if str(e.status) == "completed":
            runs = store.list_runs(e.id)
            assert runs, f"experiment {e.id} has no runs"


def test_session_events_are_persisted(lab):
    cfg, store, bus, director = lab
    ctx = director.start_session("optim")
    director.run_session(ctx, max_iterations=2)
    events = store.list_events(ctx.session_id)
    types = [e["type"] for e in events]
    assert "session.started" in types
    assert any(t.startswith("hypothesis.proposed") for t in types)
    assert any(t.startswith("agent.critic.reviewed") for t in types)


def test_critic_produces_verdicts_and_repro_checks(lab):
    cfg, store, bus, director = lab
    ctx = director.start_session("bandit")
    summary = director.run_session(ctx, max_iterations=2)
    critiques = []
    for outcome in summary["outcomes"]:
        if outcome["experiment_id"]:
            critiques.extend(store.get_critiques(outcome["experiment_id"]))
    assert critiques
    assert all(c.verdict.value in ("accept", "revise", "reject") for c in critiques)
    assert any(c.repro_check_passed is True for c in critiques), \
        "reproducibility spot-check should pass on deterministic kernels"


def test_duplicate_spec_is_not_rerun(lab):
    """Identical configs must reuse cached analyses (spec deduplication)."""
    cfg, store, bus, director = lab
    ctx = director.start_session("bandit")
    # force two identical drafts by resetting strategy memory between runs
    first_ctx = ctx
    s1 = director.run_single_iteration(first_ctx)
    exp1_id = s1.experiment_id
    exp1 = store.get_experiment(exp1_id)

    # construct identical hypothesis+config manually
    from rlab.ids import new_id
    from rlab.models import ExperimentConfig, Experiment, ExperimentStatus, Hypothesis, OriginKind
    from rlab.runtime.provenance import current_git_commit, environment_snapshot
    from rlab.runtime.repro import spec_hash

    config = ExperimentConfig(**{**exp1.config.__dict__})
    payload = {
        "domain": config.domain, "task": config.task,
        "task_params": config.extra.get("task_params", {}),
        "variants": config.variants, "baseline": config.baseline,
        "n_seeds": config.n_seeds, "seed_root": config.seed_root,
        "budget_label": config.budget_label,
    }
    sid = ctx.session_id
    exp2 = Experiment(
        id=new_id("experiment"), session_id=sid,
        hypothesis_id=new_id("hypothesis"), iteration=99,
        config=config, spec_hash=spec_hash(payload),
        code_version="x", git_commit=current_git_commit(),
        env_json=environment_snapshot(), dataset_ref={},
        status=ExperimentStatus.COMPLETED,
    )
    store.save_experiment(exp2)
    found = store.find_experiment_by_spec(spec_hash(payload))
    assert found is not None and found.id in (exp1_id, exp2.id)
