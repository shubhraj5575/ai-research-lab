"""Data Analyst: statistical analysis of experiment runs.

Methodology notes
-----------------
* Experiments use *common random numbers*: every variant sees the same seed
  set, so comparisons are paired by seed. We use the paired t-test as primary,
  with Welch's t-test reported alongside.
* Uncertainty for headline deltas uses percentile bootstrap over seeds.
* When several variants are compared to one baseline, Holm step-down controls
  family-wise error across that comparison family.
"""

from __future__ import annotations

import math

from ..config import LabConfig
from ..events import EventBus
from ..ids import new_id
from ..models import Analysis, Comparison, Experiment, RunResult
from ..stats import (
    bootstrap_delta_ci,
    bootstrap_delta_ci_paired,
    cohens_d,
    describe,
    holm_bonferroni,
    mann_whitney_u,
    paired_ttest,
    welch_ttest,
)
from ..store import Store
from .base import Agent


def _direction_for(domain_name: str) -> str:
    try:
        from ..domain import get_domain
        return get_domain(domain_name).direction
    except KeyError:
        return "minimize"


class DataAnalyst(Agent):
    role = "analyst"

    def __init__(self, bus: EventBus, cfg: LabConfig, store: Store):
        super().__init__(bus)
        self.cfg = cfg
        self.store = store

    # ------------------------------------------------------------------
    def analyze(self, session_id: str, exp: Experiment) -> Analysis | None:
        runs = [r for r in self.store.list_runs(exp.id) if r.status == "ok"]
        if not runs:
            return None

        # group successful runs per variant first
        grouped: dict[str, list[RunResult]] = {}
        for r in runs:
            grouped.setdefault(r.variant, []).append(r)
        if not grouped:
            return None

        # choose the primary metric: explicit override, else first key common
        # to every variant
        metric = exp.config.extra.get("primary_metric")
        if not metric:
            common = None
            for variant, rr_list in grouped.items():
                keys = set(rr_list[0].metrics.keys())
                for rr in rr_list[1:]:
                    keys &= set(rr.metrics.keys())
                common = keys if common is None else (common & keys)
            if not common:
                return None
            metric = sorted(common)[0]

        by_variant: dict[str, dict[int, float]] = {}
        for variant, rr_list in grouped.items():
            seeded = {rr.seed: rr.metrics[metric] for rr in rr_list if metric in rr.metrics}
            if seeded:
                by_variant[variant] = seeded
        if len(by_variant) < 1:
            return None

        direction = _direction_for(exp.config.domain)
        means = {v: sum(d.values()) / len(d) for v, d in by_variant.items()}
        if direction == "minimize":
            ranking = sorted(means.items(), key=lambda kv: kv[1])  # ascending
        else:
            ranking = sorted(means.items(), key=lambda kv: -kv[1])
        baseline_label = (exp.config.baseline
                          if exp.config.baseline in by_variant else ranking[-1][0])

        comparisons: list[Comparison] = []
        pvalues_family: list[float] = []

        base_seeds = by_variant[baseline_label]
        for variant in sorted(by_variant):
            if variant == baseline_label:
                continue
            comp = self._compare(variant, by_variant[variant], baseline_label,
                                 base_seeds, metric, direction)
            comparisons.append(comp)
            pvalues_family.append(comp.p_value)

        corrected = holm_bonferroni(pvalues_family, alpha=self.cfg.alpha)
        for comp, (adj_p, significant) in zip(comparisons, corrected):
            comp.p_value = adj_p
            comp.significant = significant

        best_variant = ranking[0][0]
        summary = self._summary(metric, direction, ranking, baseline_label,
                                comparisons, exp)
        analysis = Analysis(
            id=new_id("analysis"),
            experiment_id=exp.id,
            primary_metric=metric,
            direction=direction,
            comparisons=comparisons,
            summary=summary,
            best_variant=best_variant,
            ranking=[(v, round(m, 6)) for v, m in ranking],
        )
        self.store.save_analysis(analysis)
        self.announce(session_id, "analyzed", experiment_id=exp.id,
                      best=best_variant, n_comparisons=len(comparisons))
        return analysis

    # ------------------------------------------------------------------
    def _compare(self, variant_a: str, a: dict[int, float], variant_b: str,
                 b: dict[int, float], metric: str, direction: str) -> Comparison:
        """Compare A vs reference B; delta = mean(A) - mean(B)."""
        shared = sorted(set(a) & set(b))
        av = [a[s] for s in shared]
        bv = [b[s] for s in shared]
        all_a = [a[s] for s in sorted(a)]
        all_b = [b[s] for s in sorted(b)]
        test_used = ""
        if len(shared) >= max(6, min(len(all_a), len(all_b)) // 2):
            t_stat, df, p = paired_ttest(av, bv)
            test_used = f"paired_t(df={df:.0f})"
        else:
            t_stat, df, p = welch_ttest(all_a, all_b)
            test_used = f"welch_t(df={df:.0f})"
        try:
            _, _, p_mwu = mann_whitney_u(all_a, all_b)
            robustness_note = p_mwu
        except ValueError:
            robustness_note = None
        # deterministic bootstrap seed derived from the comparison identity
        # (never Python's randomized string hash)
        pair_key = f"{variant_a}||{variant_b}||{metric}".encode()
        boot_seed = int.from_bytes(pair_key[:8], "big") % (2**31)
        if len(shared) == len(all_a) == len(all_b) and len(shared) >= 6:
            lo, hi = bootstrap_delta_ci_paired(av, bv, iters=self.cfg.bootstrap_iters,
                                               seed=boot_seed, alpha=self.cfg.alpha)
            ci_kind = "paired"
        else:
            lo, hi = bootstrap_delta_ci(all_b, all_a, iters=self.cfg.bootstrap_iters,
                                        seed=boot_seed, alpha=self.cfg.alpha)
            ci_kind = "unpaired"
        effect = cohens_d(all_b, all_a)  # sign: A relative to B
        mean_a = sum(all_a) / len(all_a)
        mean_b = sum(all_b) / len(all_b)
        delta = mean_a - mean_b
        better = "a" if ((delta < 0) if direction == "minimize" else (delta > 0)) else "b"
        comp = Comparison(
            variant_a=variant_a, variant_b=variant_b, metric=metric,
            n_a=len(all_a), n_b=len(all_b),
            mean_a=round(mean_a, 6), mean_b=round(mean_b, 6),
            delta=round(delta, 6), ci_low=round(lo, 6), ci_high=round(hi, 6),
            p_value=min(1.0, p), test=test_used,
            effect_size=round(effect, 4),
            significant=False,  # filled after Holm correction
            better=better if abs(delta) > 0 else "tie",
        )
        comp.test += f"|ci={ci_kind}"
        # attach MWU agreement as metadata through test string (kept simple)
        if robustness_note is not None and not math.isnan(robustness_note):
            comp.test += f"|mwu_p={robustness_note:.3g}"
        return comp

    # ------------------------------------------------------------------
    def _summary(self, metric: str, direction: str, ranking, baseline_label,
                 comparisons, exp: Experiment) -> str:
        lines = [
            f"Primary metric '{metric}' ({direction}); "
            f"{exp.config.n_seeds} paired seeds per variant on task "
            f"'{exp.config.task}' ({exp.config.budget_label})."
        ]
        rank_str = ", ".join(f"{i + 1}. {v} (mean {m:.4g})"
                             for i, (v, m) in enumerate(ranking))
        lines.append(f"Ranking — {rank_str}.")
        for c in comparisons:
            verdict = "significant" if c.significant else "not significant"
            lines.append(
                f"{c.variant_a} vs {c.variant_b}: delta={c.delta:+.4g} "
                f"[CI95 {c.ci_low:+.4g}, {c.ci_high:+.4g}], adjusted p={c.p_value:.3g}, "
                f"d={c.effect_size:.2f} → {verdict}; better={c.better}."
            )
        desc = describe([m for _, m in ranking])
        if desc["n"] > 1:
            spread = desc["max"] - desc["min"]
            lines.append(f"Mean-metric spread across variants: {spread:.4g}.")
        return "\n".join(lines)
