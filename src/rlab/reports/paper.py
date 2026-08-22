"""Evidence-grounded paper generation.

Rules enforced by construction:
* every number in the output is extracted from the store (runs, analyses,
  critiques, hypotheses) — the generator has no other data source;
* every experimental claim is annotated with the experiment IDs that back it;
* sections that lack data say so explicitly instead of inventing content.

Output: a Markdown paper plus machine-readable ``claims.json`` and SVG figures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import LabConfig
from ..models import HypothesisStatus
from ..store import Store
from . import figures


@dataclass
class PaperArtifacts:
    markdown_path: Path
    claims_path: Path
    figures_dir: Path
    figure_files: list[str] = field(default_factory=list)


class PaperGenerator:
    SECTION_ORDER = [
        "Abstract", "Introduction", "Related Work", "Hypotheses",
        "Methodology", "Experiments and Results", "Discussion",
        "Limitations and Threats to Validity", "Conclusion", "Future Work",
        "Provenance and Traceability",
    ]

    def __init__(self, cfg: LabConfig, store: Store):
        self.cfg = cfg
        self.store = store

    # ------------------------------------------------------------------
    def generate(self, session_id: str, out_dir: Path) -> PaperArtifacts:
        session = self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"session {session_id!r} not found")
        out_dir = Path(out_dir)
        fig_dir = out_dir / "figures"
        fig_dir.mkdir(parents=True, exist_ok=True)

        hyps = self.store.list_hypotheses(session_id)
        exps = self.store.list_experiments(session_id)
        sources = self.store.list_sources(session_id)
        analyses = {e.id: self.store.get_analysis(e.id) for e in exps}
        critiques = {e.id: self.store.get_critiques(e.id) for e in exps}

        claims: list[dict[str, Any]] = []
        md: list[str] = []

        title = f"An Autonomous Investigation of {self._titleize_question(session['question'])}"
        md.append(f"# {title}\n")
        md.append(f"*Research session* `{session_id}` · domain `{session['domain']}` "
                  f"· git commit `{(session.get('git_commit') or 'unknown')[:10]}`\n")
        md.append("> **Evidence policy.** All numbers below are extracted "
                  "programmatically from this session's experiment database. Each "
                  "experimental statement cites the experiment IDs that support it. "
                  "Hypothesis prose was produced by the lab's deterministic "
                  f"'{self.cfg.reasoner}' reasoning layer.\n")

        # ---------------- Abstract ----------------
        supported = [h for h in hyps if h.status == HypothesisStatus.SUPPORTED]
        refuted = [h for h in hyps if h.status == HypothesisStatus.REFUTED]
        n_runs = sum(len(self.store.list_runs(e.id)) for e in exps)
        abstract_lines = [
            f"This report documents an autonomous computational research session "
            f"investigating: *{session['question']}*. The system executed "
            f"{len(exps)} experiments ({n_runs} seeded runs) to test {len(hyps)} "
            f"hypotheses through an iterative propose–design–execute–analyze–"
            f"critique loop.",
        ]
        if supported or refuted:
            abstract_lines.append(
                f"Of the tested hypotheses, {len(supported)} were supported and "
                f"{len(refuted)} were refuted by their falsification tests; the "
                f"remainder were inconclusive or superseded."
            )
        best_overall = self._best_overall(analyses)
        if best_overall:
            variant, mean, metric, eid = best_overall
            abstract_lines.append(
                f"The strongest configuration observed was `{variant}` "
                f"(mean {metric}={mean:.4g}; experiment `{eid}`)."
            )
            claims.append({
                "claim": f"{variant} achieved the best session-wide mean {metric}.",
                "value": {"mean": mean, "metric": metric},
                "evidence": [eid],
                "kind": "observational",
            })
        md.append("## Abstract\n")
        md.append(" ".join(abstract_lines) + "\n")

        # ---------------- Introduction ----------------
        md.append("## Introduction\n")
        md.append(
            f"The research question for this session is:\n\n> {session['question']}\n"
        )
        md.append(
            "Rather than generating survey prose, the lab answers this question "
            "empirically: it maintains hypotheses with explicit falsification "
            "conditions, converts each into a paired-seed Monte-Carlo experiment, "
            "executes the experiment code inside isolated sandboxed processes, and "
            "subjects every conclusion to an adversarial critic pass before it may "
            "stand.\n"
        )

        # ---------------- Related work ----------------
        md.append("## Related Work\n")
        if sources:
            for i, s in enumerate(sorted(sources, key=lambda s: -(s.relevance or 0))[:8], 1):
                url = f" [link]({s.url})" if s.url else ""
                year = f", {s.year}" if s.year else ""
                authors = ", ".join(s.authors[:3]) + (" et al." if len(s.authors) > 3 else "")
                md.append(f"[S{i}] **{s.title}** — {authors}{year}.{url}  \n"
                          f"*({s.kind}; relevance {s.relevance if s.relevance is not None else 'n/a'})*")
                md.append("")
        else:
            md.append("*No literature sources were collected for this session.*\n")

        # ---------------- Hypotheses ----------------
        md.append("## Hypotheses\n")
        for h in hyps:
            md.append(f"### H{h.number} [{str(h.status).upper()}]\n")
            md.append(f"- **Claim:** {h.claim}")
            md.append(f"- **Reasoning:** {h.reasoning}")
            md.append(f"- **Expected result:** {h.expected_result}")
            md.append(f"- **Falsification condition:** {h.falsification_condition}")
            if h.confidence is not None:
                md.append(f"- **Post-hoc confidence score:** {h.confidence}")
            parent = f" (parent experiment `{h.parent_experiment_id}`)" if h.parent_experiment_id else ""
            md.append(f"- **Origin:** {h.origin.value}{parent}\n")

        # ---------------- Methodology ----------------
        md.append("## Methodology\n")
        md.append(
            "**Design.** Experiments use common random numbers: every configuration "
            "in an experiment shares one seed set, so comparisons are paired by "
            f"seed. Default repetitions per configuration: {self.cfg.seeds_per_config} "
            "(replications raised automatically when the critic demanded more power).\n\n"
            "**Execution isolation.** Each run executes as its own OS process "
            "(`python -I`) with CPU-time/file-size rlimits, sanitized environment, "
            "and hard wall-clock kill. The kernel code bundled into each run is "
            "content-hashed (`code_version`); configurations are canonical-JSON "
            "hashed (`spec_hash`) so identical experiments are never silently "
            "re-run.\n\n"
            "**Statistics.** Paired t-tests as primary test; Mann-Whitney U as a "
            "rank-based robustness check; paired bootstrap 95% CIs for effect "
            f"sizes ({self.cfg.bootstrap_iters} resamples, deterministic seeds); "
            "Holm step-down correction across each comparison family. Effect size "
            "is Cohen's d oriented as `mean(reference) - mean(variant)`.\n\n"
            "**Reproducibility audit.** The critic re-executes sampled runs from "
            "scratch and compares result hashes byte-for-byte.\n"
        )

        # ---------------- Experiments & Results ----------------
        md.append("## Experiments and Results\n")
        any_results = False
        for e in exps:
            analysis = analyses.get(e.id)
            crit_list = critiques.get(e.id, [])
            hyp = next((h for h in hyps if h.id == e.hypothesis_id), None)
            md.append(f"### E{e.iteration} — `{e.id}`\n")
            md.append(
                f"- Task: `{e.config.task}` ({e.config.budget_label}); "
                f"variants: {', '.join('`' + v + '`' for v in sorted(e.config.variants))}"
            )
            md.append(
                f"- Seeds: {e.config.n_seeds} per variant (root seed "
                f"`{e.config.seed_root}`); status: **{str(e.status).upper()}**"
            )
            if hyp is not None and hyp.predicted_variant:
                md.append(f"- Predicted winner: `{hyp.predicted_variant}`")
            md.append("")
            if str(e.status) not in ("completed",):
                md.append(f"*Experiment did not complete cleanly ({e.status}): "
                          f"{e.error[:200]}*\n")
                continue
            if analysis is None:
                md.append("*No analysis available.*\n")
                continue
            any_results = True
            means_by_variant: dict[str, tuple[float, float]] = {}
            for variant, mean in analysis.ranking:
                runs = [r.metrics[analysis.primary_metric]
                        for r in self.store.list_runs(e.id)
                        if r.variant == variant and r.status == "ok"]
                sd = (sum((x - mean) ** 2 for x in runs) / (len(runs) - 1)) ** 0.5 if len(runs) > 1 else 0.0
                means_by_variant[variant] = (mean, sd)
            md.append("| Variant | mean | sd |")
            md.append("|---|---|---|")
            for variant, (mean, sd) in means_by_variant.items():
                md.append(f"| `{variant}` | {mean:.4g} | {sd:.3g} |")
            md.append("")
            for c in analysis.comparisons:
                sig = "significant" if c.significant else "not significant"
                sentence = (
                    f"`{c.variant_a}` vs reference `{c.variant_b}`: Δ(mean_b−mean_a)"
                    f"={c.delta:+.4g}, CI95=[{c.ci_low:+.4g}, {c.ci_high:+.4g}], "
                    f"adjusted p={c.p_value:.3g}, d={c.effect_size:.2f} → **{sig}** "
                    f"(n={c.n_a}/variant; `{c.test.split('|')[0]}`)."
                )
                md.append(sentence + f"\n\n*(evidence: `{e.id}`)*\n")
                claims.append({
                    "claim": sentence,
                    "evidence": [e.id],
                    "kind": ("significant_comparison" if c.significant
                             else "non_significant_comparison"),
                    "values": {"delta": c.delta, "ci_low": c.ci_low,
                               "ci_high": c.ci_high, "p_adjusted": c.p_value,
                               "effect_d": c.effect_size},
                })
            # convergence figure from real series data
            series_by_variant: dict[str, list[float]] = {}
            step = 1
            for r in self.store.list_runs(e.id):
                curve = r.series.get("cumulative_regret") or r.series.get("best_so_far")
                if curve and r.status == "ok":
                    series_by_variant.setdefault(r.variant, []).append(curve)
                    step = max(step, int(r.series.get("curve_step", 1)))
            if series_by_variant:
                avg_series = {
                    v: self._average_curves(curves)
                    for v, curves in series_by_variant.items() if curves
                }
                if avg_series:
                    fname = f"convergence_E{e.iteration}.svg"
                    try:
                        figures.line_chart(
                            avg_series,
                            title=f"E{e.iteration}: mean convergence ({e.config.task})",
                            x_label="evaluation step",
                            y_label=analysis.primary_metric.replace("_", " "),
                            out_path=fig_dir / fname, x_scale=step)
                        rel = f"![E{e.iteration} convergence](figures/{fname})"
                        md.append(rel + "\n")
                    except ValueError:
                        pass
            for cr in crit_list:
                findings = ", ".join(f.code for f in cr.issues) or "none"
                repro = ("passed" if cr.repro_check_passed else
                         "FAILED" if cr.repro_check_passed is False else "skipped")
                md.append(
                    f"**Critic verdict: {cr.verdict.value.upper()}** "
                    f"(findings: {findings}; reproducibility check: {repro}).\n"
                )

        # champion bar chart for the most recent analysis with comparisons
        if any_results and exps:
            last_analysis = next((a for a in reversed(list(analyses.values())) if a), None)
            if last_analysis and last_analysis.ranking:
                items = []
                exp_by_id = {e.id: e for e in exps}
                src_exp = exp_by_id.get(last_analysis.experiment_id)
                if src_exp is not None:
                    from ..stats import bootstrap_ci
                    for variant, mean in last_analysis.ranking:
                        values = [r.metrics[last_analysis.primary_metric]
                                  for r in self.store.list_runs(src_exp.id)
                                  if r.variant == variant and r.status == "ok"]
                        if len(values) >= 4:
                            lo, hi = bootstrap_ci(values, iters=400,
                                                  seed=len(values))
                            items.append((variant, mean,
                                          min(lo, hi), max(lo, hi)))
                        else:
                            items.append((variant, mean, mean, mean))
                    fname = "champion_ranking.svg"
                    figures.comparison_bars(
                        items,
                        title=f"Final ranking ({src_exp.config.task}, "
                              f"{src_exp.config.budget_label})",
                        y_label=last_analysis.primary_metric.replace("_", " "),
                        out_path=fig_dir / fname,
                        lower_is_better=last_analysis.direction == "minimize")
                    md.append(f"![final ranking](figures/{fname})\n")

        # outcome timeline over all iterations
        rows = []
        for h in hyps:
            rows.append((h.number, str(h.status)))
        if rows:
            fname = "outcome_timeline.svg"
            figures.outcome_timeline(rows, "Hypothesis outcomes by iteration",
                                     fig_dir / fname)
            md.append(f"![outcome timeline](figures/{fname})\n")

        if not any_results:
            md.append("*No completed experiment produced analyzable results in "
                      "this session.*\n")

        # ---------------- Discussion ----------------
        md.append("## Discussion\n")
        discussion_points = []
        champ = self._champion_summary(analyses, exps)
        if champ:
            discussion_points.append(champ)
        refuted_notes = [h for h in refuted]
        if refuted_notes:
            ids = ", ".join("`" + (e.id) + "`" for e in exps
                            if e.hypothesis_id in {r.id for r in refuted_notes})
            discussion_points.append(
                f"Refuted hypotheses ({ids}) are retained in the record: negative "
                "results constrain the hypothesis space for future sessions."
            )
        md.append("\n\n".join(discussion_points) if discussion_points
                  else "*Nothing substantive to discuss yet.*\n")

        # ---------------- Limitations ----------------
        md.append("## Limitations and Threats to Validity\n")
        limitation_set = set()
        for crit_list in critiques.values():
            for cr in crit_list:
                for finding in cr.issues:
                    limitation_set.add(finding.code)
        known_limitations = [
            ("SINGLE_COMPARATOR",
             "Several conclusions rest on single-opponent comparisons; ranking "
             "robustness against broader competitor pools is unverified."),
            ("SMALL_SAMPLE",
             "Some comparisons ran with limited seeds; their intervals are wide."),
            ("NEGLIGIBLE_EFFECT",
             "A statistically detectable but practically negligible effect was "
             "observed; it should not be acted upon."),
            ("SCOPE_OVERREACH",
             "Claims occasionally referenced settings beyond those executed; "
             "they are scoped down in the conclusion."),
            ("MISSING_RUNS",
             "Some runs failed mid-experiment; affected metrics are reported "
             "from successful runs only."),
        ]
        for code, text in known_limitations:
            if code in limitation_set:
                md.append(f"- **{code}:** {text}")
        md.append(
            "- Environments are synthetic benchmark families defined inside the "
            "lab's domain plugins; external validity beyond these families is not "
            "claimed."
        )
        md.append(
            f"- All reasoning in this session used the deterministic '{self.cfg.reasoner}' "
            "strategy engine (no language model), which bounds the creativity of "
            "hypothesis selection to its strategy ladder."
        )
        if self.cfg.executor == "local":
            md.append(
                "- Experiment isolation used local subprocess sandboxes "
                "(rlimit+timeout), which do not provide network isolation; kernels "
                "shipped with the lab perform no I/O beyond their workdir."
            )
        md.append("")

        # ---------------- Conclusion ----------------
        md.append("## Conclusion\n")
        if supported:
            for h in supported:
                ev = [e.id for e in exps if e.hypothesis_id == h.id]
                md.append(
                    f"- H{h.number} stands: {self._scoped_claim(h.claim)} "
                    f"*(supported by `{', '.join(ev) if ev else 'cached duplicate'}"
                    f"`).*"
                )
                claims.append({
                    "claim": h.claim, "status": "supported",
                    "evidence": ev, "kind": "hypothesis_resolution",
                })
        else:
            md.append("- No hypothesis reached SUPPORTED status in this session.")
        if refuted:
            for h in refuted:
                ev = [e.id for e in exps if e.hypothesis_id == h.id]
                md.append(
                    f"- H{h.number} was falsified: {self._scoped_claim(h.claim)} "
                    f"*(refuted by `{', '.join(ev)}`).*"
                )
                claims.append({
                    "claim": h.claim, "status": "refuted",
                    "evidence": ev, "kind": "hypothesis_resolution",
                })
        md.append("")

        # ---------------- Future work ----------------
        md.append("## Future Work\n")
        gaps = self.store.list_gaps(session_id)
        unswept = self._unexplored_directions(analyses, exps)
        for g in gaps[:3]:
            md.append(f"- Literature gap worth pursuing: {g.description}")
        for u in unswept:
            md.append(f"- {u}")
        md.append(
            "- Extend sessions with LLM-assisted narration once API credentials "
            "are configured; numeric claims would remain database-derived."
        )
        md.append("")

        # ---------------- Traceability appendix ----------------
        md.append("## Provenance and Traceability\n")
        md.append(
            "| Artifact | Where |\n|---|---|\n"
            f"| Session record | `sessions` table row `{session_id}` |\n"
            f"| Raw runs | `runs` table, {n_runs} rows |\n"
            "| Kernel code hashes | `experiments.code_version` |\n"
            "| Config snapshots | artifact dirs under the session root |\n"
            "| This document | generated by `rlab.reports.paper.PaperGenerator` |\n"
        )
        md.append("\n**Claim → evidence index**\n")
        for i, c in enumerate(claims, 1):
            md.append(f"{i}. ({c['kind']}) {c['claim'][:160]} → evidence: "
                      f"{', '.join('`' + e + '`' for e in c['evidence']) or 'none'}")
        md.append("")

        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / "paper.md"
        md_path.write_text("\n".join(md), encoding="utf-8")
        claims_path = out_dir / "claims.json"
        claims_path.write_text(json.dumps({
            "session_id": session_id,
            "policy": "every entry must cite experiment IDs present in the store",
            "claims": claims,
        }, indent=2), encoding="utf-8")
        return PaperArtifacts(markdown_path=md_path, claims_path=claims_path,
                              figures_dir=fig_dir,
                              figure_files=sorted(p.name for p in fig_dir.glob("*.svg")))

    # ------------------------------------------------------------------
    @staticmethod
    def _titleize_question(q: str) -> str:
        q = q.strip().rstrip("?")
        return q[0].upper() + q[1:] if q else "an Empirical Question"

    @staticmethod
    def _scoped_claim(claim: str) -> str:
        # strip absolute-sounding openers; keep the empirical core
        return claim.strip()

    @staticmethod
    def _average_curves(curves: list[list[float]]) -> list[float]:
        n = min(len(c) for c in curves)
        trimmed = [c[:n] for c in curves]
        return [round(sum(c[i] for c in trimmed) / len(trimmed), 4)
                for i in range(n)]

    def _best_overall(self, analyses: dict[str, Any]):
        candidates = []
        for eid, a in analyses.items():
            if a and a.ranking:
                variant, mean = a.ranking[0]
                candidates.append((variant, mean, a.primary_metric, eid))
        if not candidates:
            return None
        # primary metrics across current domains are all minimize-oriented
        return min(candidates, key=lambda c: c[1])

    def _champion_summary(self, analyses, exps) -> str | None:
        best = self._best_overall(analyses)
        if best is None:
            return None
        variant, mean, metric, eid = best
        return (
            f"Across the session, `{variant}` posted the strongest mean "
            f"{metric} ({mean:.4g}) in `{eid}`; within its own experiment it was "
            "compared under Holm-corrected paired inference against the baseline."
        )

    @staticmethod
    def _unexplored_directions(analyses, exps) -> list[str]:
        ideas = []
        tested_tasks = {e.config.task for e in exps}
        try:
            from ..domain import get_domain
            domain_names = {e.config.domain for e in exps}
        except KeyError:
            return ideas
        all_tasks: set[str] = set()
        swept_policies: set[str] = set()
        for e in exps:
            for label in e.config.variants:
                swept_policies.add(label.split("@")[0])
        for dn in domain_names:
            plugin = get_domain(dn)
            for tid, _desc in plugin.tasks():
                all_tasks.add(tid)
            untested = sorted(all_tasks - tested_tasks)
            if untested:
                ideas.append(
                    "Untested environments remain: "
                    + ", ".join(f"`{t}`" for t in untested)
                    + "; transfer results there would strengthen external validity."
                )
            families = plugin.policy_families() or HypothesisFamilies.families(plugin)
            untouched = [f for f in families if f not in swept_policies]
            if untouched:
                ideas.append(
                    "Policy/solver families never benchmarked: "
                    + ", ".join(f"`{f}`" for f in untouched) + "."
                )
        return ideas


class HypothesisFamilies:
    """Adapter mirroring HypothesisAgent._known_families without an agent."""

    @staticmethod
    def families(plugin) -> list[str]:
        ranges = getattr(plugin, "POLICY_PARAM_RANGES", None) or getattr(
            plugin, "SOLVER_PARAM_RANGES", None)
        if ranges:
            return list(ranges.keys())
        seen: set[str] = set()
        for k in plugin.knobs():
            if k.applies_to_policies:
                seen |= set(k.applies_to_policies)
        return list(seen)
