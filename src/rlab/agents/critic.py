"""Critic Agent: adversarial reviewer for every completed experiment.

The critic runs mechanical, auditable checks against the analysis and raw
runs. Its job is to *disprove* the hypothesis if it can. Every finding is
structured (code, severity, recommendation) so downstream agents can act on
it deterministically.

Checks
------
MISSING_RUNS          failed/timed-out repetitions undermine reliability
IRREPRODUCIBLE        fresh re-execution of sampled runs changed results
CI_INCLUDES_ZERO      headline delta not significant -> claim unsupported
WRONG_DIRECTION       predicted variant actually lost
SMALL_SAMPLE          underpowered given the observed effect size
VARIANCE_DOMINATED    statistically significant but negligible effect size
NO_BASELINE           no reference comparison available
SCOPE_OVERREACH       claim generalizes beyond what was tested
SINGLE_COMPARATOR     evidence rests on exactly one opponent
"""

from __future__ import annotations

import re

from ..config import LabConfig
from ..domain.base import DomainPlugin
from ..events import EventBus
from ..ids import new_id
from ..models import (
    Analysis,
    Critique,
    CritiqueFinding,
    CritiqueVerdict,
    Experiment,
)
from ..runtime.runner import ExperimentRunner
from ..stats import required_n_per_group
from ..store import Store
from .base import Agent

budget_label_re = re.compile(r"t\s*=\s*\d{3,}|evals\s*=\s*\d{3,}")


class CriticAgent(Agent):
    role = "critic"

    def __init__(self, bus: EventBus, cfg: LabConfig, store: Store,
                 runner: ExperimentRunner):
        super().__init__(bus)
        self.cfg = cfg
        self.store = store
        self.runner = runner

    # ------------------------------------------------------------------
    def review(self, session_id: str, exp: Experiment, analysis: Analysis | None,
               plugin: DomainPlugin) -> Critique:
        findings: list[CritiqueFinding] = []
        repro_report: dict | None = None

        # --- reliability of execution -----------------------------------
        runs = self.store.list_runs(exp.id)
        bad_runs = [r for r in runs if r.status != "ok"]
        if runs and len(bad_runs) / len(runs) > 0.10:
            findings.append(CritiqueFinding(
                code="MISSING_RUNS", severity="major",
                message=f"{len(bad_runs)}/{len(runs)} runs did not complete cleanly.",
                recommendation="Inspect kernel errors; consider re-running.",
            ))

        # --- reproducibility spot check ----------------------------------
        repro_ok: bool | None = None
        if analysis is not None:
            try:
                repro_report = self.runner.verify_reproducibility(
                    exp, plugin, sample_size=2)
                if repro_report["checked"] > 0:
                    repro_ok = repro_report["passed"] == repro_report["checked"]
                    if not repro_ok:
                        findings.append(CritiqueFinding(
                            code="IRREPRODUCIBLE", severity="blocker",
                            message=(
                                f"Re-execution mismatched on "
                                f"{repro_report['checked'] - repro_report['passed']} "
                                "of sampled runs."
                            ),
                            recommendation=(
                                "Do not trust conclusions until determinism is fixed."
                            ),
                        ))
            except Exception as exc:  # pragma: no cover - defensive
                self.log.warning("repro_check_failed", extra={"error": repr(exc)})
                repro_ok = None

        # --- statistical scrutiny ------------------------------------------
        predicted = getattr(self._hypothesis_of(exp), "predicted_variant", None)
        if analysis is not None and analysis.comparisons:
            headline = self._headline_comparison(analysis, predicted)
            includes_zero = headline.ci_low <= 0 <= headline.ci_high
            if includes_zero:
                findings.append(CritiqueFinding(
                    code="CI_INCLUDES_ZERO", severity="major",
                    message=(
                        f"Headline delta ({headline.delta:+.4g}) has bootstrap CI "
                        f"[{headline.ci_low:+.4g}, {headline.ci_high:+.4g}] crossing zero."
                    ),
                    recommendation="Increase seeds or treat as inconclusive.",
                ))
            if predicted is not None:
                favored_is_a = headline.variant_a == predicted
                won = (headline.better == "a") if favored_is_a else (headline.better == "b")
                if not won and headline.better != "tie":
                    findings.append(CritiqueFinding(
                        code="WRONG_DIRECTION", severity="blocker",
                        message=(
                            f"Predicted winner {predicted!r} lost to "
                            f"{headline.variant_b if favored_is_a else headline.variant_a!r}."
                        ),
                        recommendation="Reject the claim or restrict its scope.",
                    ))
            d = abs(headline.effect_size)
            n_min = min(headline.n_a, headline.n_b)
            try:
                need = required_n_per_group(d if d > 0.01 else 2.0)
            except ValueError:
                need = n_min
            if need > 500:
                # chasing an effect this small is impractical; report honestly
                findings.append(CritiqueFinding(
                    code="NEGLIGIBLE_EFFECT", severity="info",
                    message=(
                        f"Observed |d|={d:.3f}; resolving it at alpha="
                        f"{self.cfg.alpha}/power 0.8 would require ~{need} seeds "
                        "per arm - not practically actionable."
                    ),
                    recommendation="Treat as negligible; do not chase with more seeds.",
                ))
            elif n_min < need:
                findings.append(CritiqueFinding(
                    code="SMALL_SAMPLE", severity="minor",
                    message=(
                        f"n={n_min} per arm vs approx. required n={need} for effect "
                        f"size d={d:.2f}."
                    ),
                    recommendation="Replicate with more seeds before trusting.",
                ))
            elif d < self.cfg.min_effect_d and headline.significant:
                findings.append(CritiqueFinding(
                    code="VARIANCE_DOMINATED", severity="minor",
                    message=(
                        f"Effect size d={d:.3f} below meaningful threshold "
                        f"({self.cfg.min_effect_d}) despite significance."
                    ),
                    recommendation="Do not overstate practical importance.",
                ))

        # --- design scrutiny -------------------------------------------------
        if analysis is None or not analysis.comparisons:
            findings.append(CritiqueFinding(
                code="NO_BASELINE", severity="major",
                message="No usable baseline comparison was produced.",
                recommendation="Ensure the reference variant ran successfully.",
            ))
        elif len(analysis.comparisons) == 1:
            findings.append(CritiqueFinding(
                code="SINGLE_COMPARATOR", severity="info",
                message="Evidence rests on a single opponent configuration.",
                recommendation="Broaden comparisons before generalizing.",
            ))

        scope_findings = self._scope_check(exp, analysis)
        findings.extend(scope_findings)

        verdict = self._verdict(findings, analysis)
        text = self._narrate(exp, analysis, findings, verdict, repro_report)

        critique = Critique(
            id=new_id("critique"),
            experiment_id=exp.id,
            hypothesis_id=exp.hypothesis_id,
            verdict=verdict,
            issues=findings,
            text=text,
            repro_check_passed=repro_ok,
        )
        self.store.save_critique(critique)
        codes = [f.code for f in findings]
        self.announce(session_id, "reviewed", experiment_id=exp.id,
                      verdict=str(verdict), findings=codes,
                      repro_passed=repro_ok)
        return critique

    # ------------------------------------------------------------------
    def _hypothesis_of(self, exp: Experiment):
        hyp = self.store.get_hypothesis(exp.hypothesis_id)
        return hyp

    @staticmethod
    def _headline_comparison(analysis: Analysis, predicted):
        comps = analysis.comparisons
        if predicted:
            for c in comps:
                if c.variant_a == predicted or c.variant_b == predicted:
                    return c
        best = min(comps, key=lambda c: abs(c.delta))
        return best

    def _scope_check(self, exp: Experiment,
                     analysis: Analysis | None) -> list[CritiqueFinding]:
        """Does the hypothesis claim more than this experiment tested?

        Detects task names and budget tokens mentioned in the claim but absent
        from the experiment's actual configuration.
        """
        findings: list[CritiqueFinding] = []
        if analysis is None:
            return findings
        hyp = self.store.get_hypothesis(exp.hypothesis_id)
        if hyp is None:
            return findings
        claim = hyp.claim.lower()
        tested_task = exp.config.task.lower()
        # tasks the domain knows about; a claim naming an untested task overreaches
        domain = None
        try:
            from ..domain import get_domain
            domain = get_domain(exp.config.domain)
        except KeyError:
            pass
        if domain is not None:
            for task_id, _desc in domain.tasks():
                token = task_id.replace("_", "")
                if token in claim.replace("_", "").replace(" ", "") and task_id != tested_task:
                    findings.append(CritiqueFinding(
                        code="SCOPE_OVERREACH", severity="major",
                        message=(
                            f"Claim references task '{task_id}' but the experiment "
                            f"only tested '{tested_task}'."
                        ),
                        recommendation=(
                            "Restrict claim scope to the tested task, or run a "
                            "transfer experiment."
                        ),
                    ))
        budget_tokens = [t for t in budget_label_re.findall(claim)]
        if budget_tokens:
            actual = exp.config.budget_label.lower()
            missing = [t for t in budget_tokens if t not in actual]
            if len(missing) == len(budget_tokens) and budget_tokens:
                findings.append(CritiqueFinding(
                    code="SCOPE_OVERREACH", severity="minor",
                    message=(
                        f"Claim cites budgets {budget_tokens} not matching the "
                        f"experiment's budget ({exp.config.budget_label})."
                    ),
                    recommendation="Align claim with the executed budget.",
                ))
        return findings

    # ------------------------------------------------------------------
    @staticmethod
    def _verdict(findings: list[CritiqueFinding],
                 analysis: Analysis | None) -> CritiqueVerdict:
        severities = {f.severity for f in findings}
        if any(f.code in ("IRREPRODUCIBLE", "WRONG_DIRECTION") for f in findings):
            return CritiqueVerdict.REJECT
        if "blocker" in severities:
            return CritiqueVerdict.REJECT
        if {"major"} & severities:
            return CritiqueVerdict.REVISE
        if analysis is None:
            return CritiqueVerdict.REJECT
        return CritiqueVerdict.ACCEPT

    def _narrate(self, exp: Experiment, analysis: Analysis | None,
                 findings: list[CritiqueFinding], verdict: CritiqueVerdict,
                 repro_report: dict | None) -> str:
        parts = [f"Critic verdict: {str(verdict).upper()}."]
        if analysis is not None:
            parts.append(analysis.summary)
        if repro_report is not None and repro_report.get("checked"):
            parts.append(
                f"Reproducibility: {repro_report['passed']}/"
                f"{repro_report['checked']} sampled runs reproduced identical hashes."
            )
        if findings:
            parts.append("Findings:\n" + "\n".join(
                f"- [{f.severity.upper()}] {f.code}: {f.message} → {f.recommendation}"
                for f in findings))
        else:
            parts.append("No integrity issues detected by mechanical checks.")
        return "\n\n".join(parts)
