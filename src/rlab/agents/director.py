"""Research Director: orchestrates the autonomous research loop.

Per iteration:

    propose -> design -> implement -> execute -> analyze -> critique -> resolve

with budget guards, spec deduplication (identical configs are never re-run),
champion tracking, and hypothesis resolution driven by structured evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import LabConfig
from ..domain import get_domain
from ..domain.base import DomainPlugin
from ..events import EventBus
from ..ids import new_id, short_hash
from ..jsonlog import get_logger
from ..models import (
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    OriginKind,
)
from ..runtime.runner import ExperimentRunner
from ..runtime.repro import spec_hash
from ..store import Store
from .analyst import DataAnalyst
from .base import Agent
from .critic import CriticAgent
from .designer import DesignError, ExperimentDesigner
from .hypothesis import Champion, HypothesisAgent, ResearchMemory
from .implementation import ImplementationAgent
from .literature import LiteratureAgent

log = get_logger("director")


@dataclass
class SessionContext:
    session_id: str
    question: str
    plugin: DomainPlugin
    memory: ResearchMemory = field(default_factory=ResearchMemory)
    iteration: int = 0
    started_at: float = field(default_factory=time.time)
    outcomes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IterationOutcome:
    iteration: int
    hypothesis_id: str
    experiment_id: str | None
    status: str                       # completed | skipped_duplicate | failed | error
    verdict: str | None = None
    hypothesis_status: str | None = None
    detail: str = ""


class ResearchDirector(Agent):
    role = "director"

    def __init__(self, bus: EventBus, cfg: LabConfig, store: Store):
        super().__init__(bus)
        self.cfg = cfg
        self.store = store
        self._brief_gaps: list = []
        # persist every bus event into the audit trail
        self.bus.subscribe(lambda e: self.store.persist_event(e.to_dict()))
        self.literature = LiteratureAgent(bus, cfg, store)
        self.hypothesis_agent = HypothesisAgent(bus, cfg)
        self.designer = ExperimentDesigner(bus, cfg)
        self.implementation = ImplementationAgent(bus, cfg, store)
        self.runner = ExperimentRunner(store, cfg, self._make_executor(), bus)
        self.analyst = DataAnalyst(bus, cfg, store)
        self.critic = CriticAgent(bus, cfg, store, self.runner)

    def _make_executor(self):
        from ..sandbox import make_executor
        return make_executor(self.cfg.executor, image=self.cfg.docker_image)

    # ------------------------------------------------------------------
    def start_session(self, domain_name: str, question: str | None = None,
                      title: str = "") -> SessionContext:
        from ..runtime.provenance import current_git_commit

        plugin = get_domain(domain_name)
        q = question or plugin.default_question()
        session_id = new_id("session")
        commit = current_git_commit()
        self.store.create_session(
            session_id=session_id, question=q, domain=domain_name,
            config_snapshot=self.cfg.snapshot(), git_commit=commit,
            title=title or f"{domain_name}: {q[:70]}",
        )
        ctx = SessionContext(session_id=session_id, question=q, plugin=plugin)
        self.bus.publish("session.started", session_id=session_id,
                         question=q, domain=domain_name, git_commit=commit)
        brief = self.literature.survey(session_id, q, plugin)
        self._brief_gaps = brief.gaps
        return ctx

    # ------------------------------------------------------------------
    def run_session(self, ctx: SessionContext,
                    max_iterations: int | None = None,
                    wall_budget_minutes: float | None = None) -> dict[str, Any]:
        max_it = max_iterations or self.cfg.max_iterations
        budget_min = wall_budget_minutes or self.cfg.wall_budget_minutes
        deadline = ctx.started_at + budget_min * 60.0

        while ctx.iteration < max_it and time.time() < deadline:
            outcome = self.run_single_iteration(ctx)
            ctx.outcomes.append(outcome.__dict__)
            if outcome.status == "error":
                break
        status = "completed" if ctx.iteration >= max_it else "budget_exhausted"
        self.store.set_session_status(ctx.session_id, status)
        self.bus.publish("session.finished", session_id=ctx.session_id,
                         iterations=ctx.iteration, status=status)
        return {
            "session_id": ctx.session_id,
            "iterations": ctx.iteration,
            "status": status,
            "outcomes": [o for o in ctx.outcomes],
        }

    # ------------------------------------------------------------------
    def run_single_iteration(self, ctx: SessionContext) -> IterationOutcome:
        ctx.iteration += 1
        it = ctx.iteration
        sid = ctx.session_id
        self.bus.publish("iteration.started", session_id=sid, iteration=it)

        # ---- propose -----------------------------------------------------
        draft = None
        for attempt in range(4):
            try:
                draft, ctx.memory = self.hypothesis_agent.propose(
                    sid, ctx.question, ctx.plugin, ctx.memory, gaps=self._brief_gaps)
                break
            except RuntimeError as exc:
                log.warning("proposal_exhausted", extra={"error": str(exc)})
                break
        if draft is None:
            return IterationOutcome(iteration=it, hypothesis_id="",
                                    experiment_id=None, status="error",
                                    detail="no proposal possible")

        hyp = Hypothesis(
            id=new_id("hypothesis"),
            session_id=sid,
            number=len(self.store.list_hypotheses(sid)) + 1,
            claim=draft.claim,
            reasoning=draft.reasoning,
            expected_result=draft.expected_result,
            falsification_condition=draft.falsification_condition,
            required_experiment=draft.required_experiment,
            origin=(OriginKind.INITIAL if draft.strategy == "starter" else OriginKind.PRIOR_RESULT),
            predicted_variant=draft.predicted_variant,
            predicted_metric=ctx.plugin.primary_metric,
        )
        self.store.save_hypothesis(hyp)
        self.bus.publish("hypothesis.proposed", session_id=sid,
                         hypothesis_id=hyp.id, number=hyp.number,
                         strategy=draft.strategy, claim=hyp.claim)

        # ---- design --------------------------------------------------------
        try:
            design = self.designer.design(sid, ctx.plugin, draft)
        except DesignError as exc:
            self.hypothesis_agent.mark_failed_strategy(ctx.memory, draft, str(exc))
            self.store.update_hypothesis(hyp.id, status=HypothesisStatus.SUPERSEDED,
                                         resolved_at=time.time(),
                                         resolution_note=f"design failed: {exc}")
            return IterationOutcome(iteration=it, hypothesis_id=hyp.id,
                                    experiment_id=None, status="failed",
                                    detail=f"design error: {exc}")

        # seed_root derived deterministically from session identity + iteration
        design.config.seed_root = int(short_hash(f"{sid}:{hyp.id}", n=8), 16)

        # ---- deduplication -----------------------------------------------
        payload = {
            "domain": design.config.domain, "task": design.config.task,
            "task_params": design.config.extra.get("task_params", {}),
            "variants": design.config.variants, "baseline": design.config.baseline,
            "n_seeds": design.config.n_seeds, "seed_root": design.config.seed_root,
            "budget_label": design.config.budget_label,
        }
        spec_h = spec_hash(payload)
        existing = self.store.find_experiment_by_spec(spec_h)
        if existing is not None and existing.status == ExperimentStatus.COMPLETED:
            analysis = self.store.get_analysis(existing.id)
            if analysis is not None:
                self.store.update_hypothesis(
                    hyp.id, parent_experiment_id=existing.id,
                    status=self._resolve_from_analysis(analysis, hyp),
                    resolved_at=time.time(),
                    resolution_note=f"duplicate of {existing.id}")
                self._absorb_analysis(ctx, existing.id, analysis)
                return IterationOutcome(iteration=it, hypothesis_id=hyp.id,
                                        experiment_id=existing.id,
                                        status="skipped_duplicate",
                                        detail=f"reused {existing.id}")

        exp = self.implementation.implement(sid, it, hyp.id, design.config, ctx.plugin)

        # ---- execute -------------------------------------------------------
        try:
            self.runner.run_experiment(exp, ctx.plugin)
        except Exception as exc:
            log.error("execution_crashed", extra={"experiment_id": exp.id,
                                                  "error": repr(exc)})
            self.store.update_experiment(exp.id, status=ExperimentStatus.FAILED,
                                         finished_at=time.time(),
                                         error=repr(exc))
            return IterationOutcome(iteration=it, hypothesis_id=hyp.id,
                                    experiment_id=exp.id, status="failed",
                                    detail="execution crashed")

        fresh_exp = self.store.get_experiment(exp.id)
        assert fresh_exp is not None
        if fresh_exp.status == ExperimentStatus.FAILED:
            self.store.update_hypothesis(hyp.id, status=HypothesisStatus.INCONCLUSIVE,
                                         parent_experiment_id=exp.id,
                                         resolved_at=time.time(),
                                         resolution_note="all runs failed")
            ctx.memory.critic_codes.append("MISSING_RUNS")
            return IterationOutcome(iteration=it, hypothesis_id=hyp.id,
                                    experiment_id=exp.id, status="failed",
                                    detail="all runs failed")

        # ---- analyze + critique ---------------------------------------------
        analysis = self.analyst.analyze(sid, fresh_exp)
        critique = self.critic.review(sid, fresh_exp, analysis, ctx.plugin)

        # ---- resolve hypothesis ----------------------------------------------
        new_status, note = self._resolve(critique, analysis, hyp)
        confidence = self._confidence(analysis, hyp, new_status)
        self.store.update_hypothesis(hyp.id, status=new_status, confidence=confidence,
                                     resolved_at=time.time(),
                                     resolution_note=note[:500])

        # ---- update research memory -------------------------------------------
        self._update_memory(ctx, fresh_exp, analysis, critique)

        outcome = IterationOutcome(
            iteration=it, hypothesis_id=hyp.id, experiment_id=exp.id,
            status="completed", verdict=str(critique.verdict),
            hypothesis_status=str(new_status),
        )
        self.bus.publish("iteration.completed", session_id=sid, iteration=it,
                         **{k: getattr(outcome, k) for k in
                            ("hypothesis_id", "experiment_id", "verdict",
                             "hypothesis_status")})
        return outcome

    # ------------------------------------------------------------------
    def _resolve_from_analysis(self, analysis, hyp: Hypothesis) -> str:
        """Resolve against a cached (duplicate) analysis."""
        comp = self._headline(analysis, hyp.predicted_variant)
        if comp is not None and comp.significant and hyp.predicted_variant and \
                comp.variant_a == hyp.predicted_variant and comp.better == "a":
            return HypothesisStatus.SUPPORTED
        return HypothesisStatus.INCONCLUSIVE

    @staticmethod
    def _headline(analysis, predicted):
        if analysis is None or not analysis.comparisons:
            return None
        comps = analysis.comparisons
        if predicted:
            for c in comps:
                if c.variant_a == predicted:
                    return c
        return comps[0]

    def _resolve(self, critique, analysis, hyp: Hypothesis) -> tuple[HypothesisStatus, str]:
        codes = [f.code for f in critique.issues]
        note_bits = [f"verdict={critique.verdict}"]
        if "IRREPRODUCIBLE" in codes:
            return HypothesisStatus.INCONCLUSIVE, "irreproducible results"
        if critique.verdict.value == "reject":
            if "WRONG_DIRECTION" in codes:
                return HypothesisStatus.REFUTED, "; ".join(note_bits) + " wrong direction"
            return HypothesisStatus.INCONCLUSIVE, "; ".join(note_bits)
        if critique.verdict.value == "revise":
            if "CI_INCLUDES_ZERO" in codes or "NO_BASELINE" in codes:
                return HypothesisStatus.INCONCLUSIVE, "; ".join(note_bits)
            return HypothesisStatus.TESTING, "; ".join(note_bits) + " needs replication"
        # accept path
        comp = self._headline(analysis, hyp.predicted_variant)
        if comp is None:
            return HypothesisStatus.INCONCLUSIVE, "no comparisons available"
        favored_won = False
        if hyp.predicted_variant:
            if comp.variant_a == hyp.predicted_variant:
                favored_won = comp.better == "a"
            elif comp.variant_b == hyp.predicted_variant:
                favored_won = comp.better == "b"
        else:
            favored_won = True  # open head-to-head: informative either way
        if favored_won and comp.significant:
            note_bits.append("significant advantage")
            return HypothesisStatus.SUPPORTED, "; ".join(note_bits)
        if favored_won and not comp.significant:
            return HypothesisStatus.INCONCLUSIVE, "; ".join(note_bits) + " CI includes zero"
        if comp.better == "tie":
            return HypothesisStatus.INCONCLUSIVE, "tie"
        return HypothesisStatus.REFUTED, "; ".join(note_bits) + " favored variant lost"

    def _confidence(self, analysis, hyp, status) -> float | None:
        if analysis is None:
            return None
        comp = self._headline(analysis, hyp.predicted_variant)
        if comp is None:
            return None
        base = 0.5
        if status == HypothesisStatus.SUPPORTED:
            d = abs(comp.effect_size)
            base += min(0.45, d * 0.25)
        elif status == HypothesisStatus.REFUTED:
            base = max(0.05, 0.5 - abs(comp.effect_size) * 0.2)
        else:
            base = 0.5
        return round(base, 3)

    # ------------------------------------------------------------------
    def _update_memory(self, ctx: SessionContext, exp, analysis, critique) -> None:
        mem = ctx.memory
        mem.critic_codes = [f.code for f in critique.issues]
        if exp.status != ExperimentStatus.COMPLETED or analysis is None:
            return
        for label, params in exp.config.variants.items():
            mem.tested_labels.add(label)
            policy = params.get("policy")
            for knob in ctx.plugin.knobs():
                applies = knob.applies_to_policies is None or (
                    policy in knob.applies_to_policies)
                if applies and knob.name in params:
                    key = f"{policy}::{knob.name}"
                    mem.knob_tried.setdefault(key, set()).add(params[knob.name])
        mem.combo_tested.add((exp.config.task, exp.config.budget_label))

        ranking = analysis.ranking  # ascending by primary metric (minimize)
        if not ranking:
            return
        task_params = exp.config.extra.get("task_params", {})

        def _champion_from(label: str, mean: float) -> Champion:
            return Champion(variant_label=label, params=exp.config.variants[label],
                            task=exp.config.task, task_params=task_params,
                            budget_label=exp.config.budget_label, mean_metric=mean)

        old = mem.champion
        # A champion can only be displaced within a comparable context
        # (same task + budget); otherwise rankings live in different worlds.
        same_context = (
            old is None
            or (old.task == exp.config.task and old.budget_label == exp.config.budget_label)
        )
        best_label, best_mean = ranking[0]
        if same_context and (old is None or best_mean < old.mean_metric * 0.98):
            mem.champion = _champion_from(best_label, best_mean)

        rival_pool = [(v, m) for v, m in ranking[1:]
                      if mem.champion is None or v != mem.champion.variant_label]
        # prefer a rival from the champion's context for stress tests
        rival_candidates = [
            (v, m) for v, m in rival_pool
            if mem.champion is not None
            and exp.config.task == mem.champion.task
            and exp.config.budget_label == mem.champion.budget_label
        ] or rival_pool
        if rival_candidates:
            rv, rm = rival_candidates[0]
            mem.rival = _champion_from(rv, rm)

    def _absorb_analysis(self, ctx: SessionContext, exp_id: str, analysis) -> None:
        exp = self.store.get_experiment(exp_id)
        if exp is None:
            return
        class _NullCritique:
            issues: list = []
            verdict = type("V", (), {"value": "accept"})()
        self._update_memory(ctx, exp, analysis, _NullCritique())
