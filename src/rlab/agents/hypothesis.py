"""Hypothesis Agent.

Proposes the next hypothesis from an ordered strategy ladder. Strategies are
grounded in actual experimental history (the research memory) — every claim,
expected result, and falsification condition references real numbers from
prior analyses. The ladder implements a classic empirical-research pattern:

    1. seed hypotheses      – from domain knowledge / literature gaps
    2. replication          – answer critic demands for more evidence
    3. sensitivity sweeps   – probe parameter neighborhoods of champions
    4. transfer tests       – does the effect survive new tasks/budgets?
    5. head-to-heads        – challenge the champion with fresh competitors
    6. stress escalation    – harder environments separate methods more sharply
    7. exploration fallback – cover untried cells systematically
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..config import LabConfig
from ..domain.base import DomainPlugin, HypothesisDraft, Knob
from ..events import EventBus
from ..models import Analysis, Critique
from .base import Agent


@dataclass
class Champion:
    variant_label: str
    params: dict[str, Any]
    task: str
    task_params: dict[str, Any]
    budget_label: str
    mean_metric: float


@dataclass
class ResearchMemory:
    tested_labels: set[str] = field(default_factory=set)
    tested_families: set[str] = field(default_factory=set)   # policy/solver names
    knob_tried: dict[str, set] = field(default_factory=dict)
    combo_tested: set[str] = field(default_factory=set)   # canonical budget keys
    tested_config_keys: set[str] = field(default_factory=set)  # seed-independent ids
    explored_cells: set[str] = field(default_factory=set)  # "family::budget_key"
    champion: Champion | None = None
    rival: Champion | None = None
    critic_codes: list[str] = field(default_factory=list)
    n_hypotheses_proposed: int = 0
    failed_strategies: list[str] = field(default_factory=list)


def _knob_key(policy: str, knob: str) -> str:
    return f"{policy}::{knob}"


def _fmt_pct(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}{x * 100:.1f}%"


class HypothesisAgent(Agent):
    role = "hypothesis"

    def __init__(self, bus: EventBus, cfg: LabConfig):
        super().__init__(bus)
        self.cfg = cfg

    # ------------------------------------------------------------------
    def propose(self, session_id: str, question: str, plugin: DomainPlugin,
                memory: ResearchMemory,
                gaps: list | None = None) -> tuple[HypothesisDraft, ResearchMemory]:
        strategies = [
            ("starter", self._starter),
            ("replication", self._replication),
            ("sensitivity_sweep", self._sensitivity),
            ("transfer_test", self._transfer),
            ("head_to_head", self._head_to_head),
            ("stress_escalation", self._stress),
            ("exploration", self._exploration),
        ]
        attempted: list[str] = []
        for name, fn in strategies:
            if name in memory.failed_strategies:
                continue
            draft = fn(plugin, memory, gaps or [])
            if draft is None:
                attempted.append(name)
                continue
            draft = replace(draft, strategy=name)
            memory.n_hypotheses_proposed += 1
            self.announce(session_id, "proposed", strategy=name,
                          claim=draft.claim[:180])
            return draft, memory
        raise RuntimeError("hypothesis agent exhausted all strategies")

    def mark_failed_strategy(self, memory: ResearchMemory, draft: HypothesisDraft,
                             error: str) -> None:
        if draft.strategy not in memory.failed_strategies:
            memory.failed_strategies.append(draft.strategy)

    # ------------------------------------------------------------------
    # Strategy 1: seed hypotheses from domain knowledge / literature gaps
    # ------------------------------------------------------------------
    def _starter(self, plugin: DomainPlugin, memory: ResearchMemory,
                 gaps: list) -> HypothesisDraft | None:
        """Seed hypotheses carry their own explicit experiment sketches."""
        used = memory.n_hypotheses_proposed
        starters = plugin.starter_hypotheses()
        if used >= len(starters):
            return None
        return starters[used]

    # ------------------------------------------------------------------
    # Strategy 2: replicate when the critic demanded more evidence
    # ------------------------------------------------------------------
    def _replication(self, plugin: DomainPlugin, memory: ResearchMemory,
                     gaps: list) -> HypothesisDraft | None:
        if "SMALL_SAMPLE" not in memory.critic_codes or memory.champion is None:
            return None
        champ = memory.champion
        label = plugin.variant_label(champ.params)
        baseline_params = plugin.baseline_variant()
        baseline_label = plugin.variant_label(baseline_params)
        # If the champion *is* the baseline, a champion-vs-baseline replication
        # degenerates; replicate the champion-vs-rival contrast instead.
        if label == baseline_label:
            if memory.rival is None:
                return None
            opponent = memory.rival
            opponent_label = plugin.variant_label(opponent.params)
            if opponent.task != champ.task or opponent.budget_label != champ.budget_label:
                return None
            variants = {label: champ.params, opponent_label: opponent.params}
            contrast = f"{label} vs {opponent_label}"
        else:
            variants = {label: champ.params, baseline_label: baseline_params}
            contrast = f"{label} vs {baseline_label}"
        return HypothesisDraft(
            claim=(
                f"Replication check: {label}'s observed standing "
                f"(mean {champ.mean_metric:.4g} on {champ.task}/{champ.budget_label}) "
                "remains statistically stable under a larger sample."
            ),
            reasoning=(
                "The critic flagged SMALL_SAMPLE on the prior comparison; a "
                "replication at increased n both re-estimates the effect and "
                "tightens its confidence interval."
            ),
            expected_result="Same ranking direction with CI excluding zero.",
            falsification_condition="Ranking flips or CI includes zero at larger n.",
            required_experiment=(
                f"{champ.task} @ {champ.budget_label}; variants {contrast}; "
                "n doubled."
            ),
            predicted_variant=label,
            suggested_task=champ.task,
            suggested_task_params=dict(champ.task_params),
            suggested_variants=variants,
            suggested_baseline=(baseline_label if label != baseline_label
                                else opponent_label),
            suggested_seeds=min(120, int(self.cfg.seeds_per_config * 2)),
            strategy="replication",
        )

    # ------------------------------------------------------------------
    # Strategy 3: sweep an unswept knob of the current champion
    # ------------------------------------------------------------------
    def _sensitivity(self, plugin: DomainPlugin, memory: ResearchMemory,
                     gaps: list) -> HypothesisDraft | None:
        if memory.champion is None:
            return None
        champ = memory.champion
        policy = champ.params.get("policy", "")
        for knob in plugin.knobs():
            if knob.applies_to_policies is not None and policy not in knob.applies_to_policies:
                continue
            key = _knob_key(policy, knob.name)
            tried = memory.knob_tried.get(key, set())
            untried = [v for v in knob.values if v not in tried]
            if not untried:
                continue
            variants: dict[str, dict[str, Any]] = {}
            labels = []
            for value in untried[:3]:
                params = dict(champ.params)
                params[knob.name] = value
                lbl = plugin.variant_label(params)
                variants[lbl] = params
                labels.append(lbl)
            # the incumbent must be IN the comparison as the reference;
            # otherwise sweeps compare swept cells against an arbitrary order
            incumbent_label = champ.variant_label
            if incumbent_label not in variants:
                variants[incumbent_label] = dict(champ.params)
            best_guess = labels[0]
            return HypothesisDraft(
                claim=(
                    f"Tuning {knob.name} materially changes {policy} performance: "
                    f"at least one of {labels} beats the incumbent setting "
                    f"({incumbent_label}, mean {champ.mean_metric:.4g})."
                ),
                reasoning=(
                    "Sensitivity sweeps around a champion quantify how much of the "
                    "advantage is parameter luck vs method property."
                ),
                expected_result=(
                    f"A monotone or U-shaped response in {knob.name}; best swept "
                    f"value improves mean {plugin.primary_metric} by >5%."
                ),
                falsification_condition=(
                    "All swept values within noise of incumbent (all CIs include 0)."
                ),
                required_experiment=(
                    f"{champ.task} @ {champ.budget_label}; sweep {knob.name}"
                    f"={untried[:3]} vs incumbent; paired seeds."
                ),
                predicted_variant=best_guess,
                suggested_task=champ.task,
                suggested_task_params=dict(champ.task_params),
                suggested_variants=variants,
                suggested_baseline=incumbent_label,
                suggested_seeds=self.cfg.seeds_per_config,
                strategy="sensitivity_sweep",
            )
        return None

    # ------------------------------------------------------------------
    # Strategy 4: transfer test on a different task/budget combination
    # ------------------------------------------------------------------
    def _transfer(self, plugin: DomainPlugin, memory: ResearchMemory,
                  gaps: list) -> HypothesisDraft | None:
        if memory.champion is None:
            return None
        champ = memory.champion
        candidates = []
        for task_id, _desc in plugin.tasks():
            for b in plugin.budget_options():
                task_params = plugin.task_defaults(task_id) | b["task_params"]
                key = plugin.budget_key(task_id, task_params)
                if key not in memory.combo_tested:
                    candidates.append((task_id, b, key, task_params))
        if not candidates:
            return None
        task_id, budget, combo_key, task_params = candidates[0]
        if task_id == "bernoulli" and "gap_min" not in task_params:
            task_params["gap_min"] = 0.0
        champ_label = champ.variant_label
        baseline_params = plugin.baseline_variant()
        baseline_label = plugin.variant_label(baseline_params)
        if champ_label == baseline_label:
            # Champion IS the baseline policy: a champion-vs-baseline contrast
            # would collapse to a single variant. Contrast against the rival.
            rival = memory.rival
            if rival is None:
                return None
            rival_label = plugin.variant_label(rival.params)
            if rival_label == champ_label:
                return None
            variants = {
                champ_label: champ.params,
                rival_label: rival.params,
            }
            reference_label = rival_label
            comparator_desc = f"{rival_label} (rival)"
        else:
            variants = {
                champ_label: champ.params,
                baseline_label: baseline_params,
            }
            reference_label = baseline_label
            comparator_desc = f"{baseline_label} (baseline)"
        return HypothesisDraft(
            claim=(
                f"The champion's standing transfers to {task_id} at "
                f"{budget['label']} without retuning: {champ_label} stays ahead "
                f"of {comparator_desc}."
            ),
            reasoning=(
                "An effect that only holds in its original setting is fragile; "
                "transfer tests are the cheapest falsification attempt available."
            ),
            expected_result=f"{champ_label} still ranks above {comparator_desc}.",
            falsification_condition=(
                f"{champ_label} no better than {reference_label} on {task_id} "
                f"({budget['label']}); CI of difference includes 0 or reverses."
            ),
            required_experiment=(
                f"{task_id} @ {budget['label']}: {champ_label} vs "
                f"{reference_label}; paired seeds."
            ),
            predicted_variant=champ_label,
            suggested_task=task_id,
            suggested_task_params=task_params,
            suggested_variants=variants,
            suggested_baseline=reference_label,
            suggested_seeds=self.cfg.seeds_per_config,
            strategy="transfer_test",
        )

    # ------------------------------------------------------------------
    # Strategy 5: challenge champion with an untried competitor family
    # ------------------------------------------------------------------
    def _head_to_head(self, plugin: DomainPlugin, memory: ResearchMemory,
                      gaps: list) -> HypothesisDraft | None:
        if memory.champion is None:
            return None
        champ = memory.champion
        families = self._known_families(plugin)
        fresh = [fam for fam in families if fam not in memory.tested_families]
        if not fresh:
            return None
        competitor_policy = fresh[0]
        competitor = plugin.default_variant_params(competitor_policy)
        comp_label = plugin.variant_label(competitor)
        return HypothesisDraft(
            claim=(
                f"{comp_label} challenges champion {champ.variant_label} on its own "
                f"home ground ({champ.task}, {champ.budget_label})."
            ),
            reasoning=(
                f"{competitor_policy} uses a distinct exploration mechanism from "
                "every method tried so far; a direct match tests whether the "
                "current ranking reflects method class or specific implementation."
            ),
            expected_result=(
                f"{comp_label} either surpasses the champion by >10% or loses by "
                ">20% - an informative outcome either way."
            ),
            falsification_condition=(
                "Ambiguous near-tie (CI includes 0) would leave the ranking "
                "unresolved and trigger a replication."
            ),
            required_experiment=(
                f"{champ.task} @ {champ.budget_label}: {comp_label} vs "
                f"{champ.variant_label}; paired seeds."
            ),
            predicted_variant=None,  # genuinely open outcome
            suggested_task=champ.task,
            suggested_task_params=dict(champ.task_params),
            suggested_variants={
                comp_label: competitor,
                champ.variant_label: champ.params,
            },
            suggested_baseline=champ.variant_label,
            suggested_seeds=self.cfg.seeds_per_config,
            strategy="head_to_head",
        )

    # ------------------------------------------------------------------
    # Strategy 6: escalate environment difficulty
    # ------------------------------------------------------------------
    def _stress(self, plugin: DomainPlugin, memory: ResearchMemory,
                gaps: list) -> HypothesisDraft | None:
        if memory.champion is None or memory.rival is None:
            return None
        axes = plugin.difficulty_axes()
        champ = memory.champion
        for axis, levels in axes.items():
            if axis == "task":
                continue
            current = champ.task_params.get(axis)
            if current is None or axis in ("T", "n_evals"):
                higher = [lv for lv in levels if isinstance(current, (int, float))
                          and isinstance(lv, (int, float)) and lv > current]
                if higher:
                    new_level = min(higher)
                    task_params = dict(champ.task_params)
                    task_params[axis] = new_level
                    c_label, r_label = champ.variant_label, memory.rival.variant_label
                    return HypothesisDraft(
                        claim=(
                            f"Under escalated difficulty ({axis}={new_level}), the "
                            f"champion-vs-rival ordering persists: {c_label} stays "
                            f"ahead of {r_label}."
                        ),
                        reasoning=(
                            "Harder settings amplify method differences; robust "
                            "rankings survive escalation while brittle ones invert."
                        ),
                        expected_result=(
                            f"{c_label} retains its lead (CI excludes 0)."
                        ),
                        falsification_condition=f"{r_label} overtakes {c_label}.",
                        required_experiment=(
                            f"{champ.task} with {axis}={new_level}: {c_label} vs "
                            f"{r_label}; paired seeds."
                        ),
                        predicted_variant=c_label,
                        suggested_task=champ.task,
                        suggested_task_params=task_params,
                        suggested_variants={
                            c_label: champ.params,
                            r_label: memory.rival.params,
                        },
                        suggested_baseline=r_label,
                        suggested_seeds=self.cfg.seeds_per_config,
                        strategy="stress_escalation",
                    )
        return None

    # ------------------------------------------------------------------
    # Strategy 7: deterministic exploration of untried cells
    # ------------------------------------------------------------------
    def _exploration(self, plugin: DomainPlugin, memory: ResearchMemory,
                     gaps: list) -> HypothesisDraft | None:
        """Systematic coverage of (family × task/budget) cells.

        Cells are recorded the moment a draft is proposed so this strategy
        cannot loop even if the director rejects the design later.
        """
        tasks = list(plugin.tasks())
        budgets = plugin.budget_options()
        families = self._known_families(plugin)
        for fam in families:
            params = plugin.default_variant_params(fam)
            label = plugin.variant_label(params)
            for task_id, _desc in tasks:
                base_params = plugin.task_defaults(task_id)
                if task_id == "bernoulli" and "gap_min" not in base_params:
                    base_params["gap_min"] = 0.0
                for b in budgets:
                    task_params = dict(base_params) | b["task_params"]
                    cell_key = f"{fam}::{plugin.budget_key(task_id, task_params)}"
                    if cell_key in memory.explored_cells:
                        continue
                    memory.explored_cells.add(cell_key)
                    baseline_params = plugin.baseline_variant()
                    gap_note = ""
                    if gaps:
                        gap_note = (" Motivated by literature gap: "
                                    + gaps[0].description.split("(")[0].strip() + ".")
                    return HypothesisDraft(
                        claim=(
                            f"Open-cell exploration: {label} on {task_id} @ "
                            f"{b['label']} beats the baseline "
                            f"({plugin.variant_label(baseline_params)})."
                        ) + gap_note,
                        reasoning=(
                            "Systematic coverage of the configuration space "
                            "guards against converging prematurely on a local "
                            "region of method space."
                        ),
                        expected_result=(
                            f"{label} improves on baseline mean "
                            f"{plugin.primary_metric}."
                        ),
                        falsification_condition=(
                            "Baseline equal or better (CI excludes improvement)."
                        ),
                        required_experiment=(
                            f"{task_id} @ {b['label']}: {label} vs baseline."
                        ),
                        predicted_variant=label,
                        suggested_task=task_id,
                        suggested_task_params=task_params,
                        suggested_variants={
                            label: params,
                            plugin.variant_label(baseline_params): baseline_params,
                        },
                        suggested_baseline=plugin.variant_label(baseline_params),
                        suggested_seeds=self.cfg.seeds_per_config,
                        strategy="exploration",
                    )
        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _known_families(plugin: DomainPlugin) -> list[str]:
        """Policy/solver families known to the domain, baseline excluded."""
        try:
            baseline = plugin.baseline_variant()["policy"]
        except Exception:
            baseline = ""
        names = plugin.policy_families()
        if not names:
            names = [baseline or "default"]
        ordered = [n for n in names if n != baseline]
        return ordered
