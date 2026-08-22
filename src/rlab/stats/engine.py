"""Statistical analysis engine.

All tests are implemented locally (numpy + :mod:`rlab.stats.dists`) so the lab
has no heavyweight scientific stack requirement. Every function is unit-tested
against known reference values.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np

from .dists import betainc, normal_quantile, t_pvalue


# ---------------------------------------------------------------------------
# Descriptive
# ---------------------------------------------------------------------------
def describe(values: Sequence[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return {"n": 0, "mean": float("nan"), "stdev": float("nan"),
                "median": float("nan"), "min": float("nan"), "max": float("nan")}
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "stdev": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "median": float(np.median(arr)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


# ---------------------------------------------------------------------------
# Hypothesis tests
# ---------------------------------------------------------------------------
def welch_ttest(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """Welch's unequal-variance t-test.

    Returns ``(t_stat, df, two_sided_p)``. Means of ``a`` and ``b`` must both
    exist; if both samples are constant we return t=inf/NaN safely.
    """
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    n1, n2 = x.size, y.size
    if n1 < 2 or n2 < 2:
        raise ValueError("welch_ttest requires >=2 observations per sample")
    m1, m2 = x.mean(), y.mean()
    v1, v2 = x.var(ddof=1), y.var(ddof=1)
    # Degenerate: both constant and equal -> no evidence of difference.
    if v1 == 0 and v2 == 0:
        if m1 == m2:
            return 0.0, float(n1 + n2 - 2), 1.0
        return math.inf * math.copysign(1.0, m1 - m2), float(n1 + n2 - 2), 0.0
    se_sq = v1 / n1 + v2 / n2
    t = (m1 - m2) / math.sqrt(se_sq)
    df = se_sq ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return float(t), float(df), float(t_pvalue(t, df))


def paired_ttest(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """Paired t-test over matched samples; returns (t, df, two-sided p)."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    if x.size != y.size or x.size < 2:
        raise ValueError("paired_ttest requires equal-length samples with n>=2")
    diff = x - y
    n = diff.size
    sd = diff.std(ddof=1)
    mean_d = diff.mean()
    if sd == 0:
        if mean_d == 0:
            return 0.0, float(n - 1), 1.0
        return math.inf * math.copysign(1.0, mean_d), float(n - 1), 0.0
    t = mean_d / (sd / math.sqrt(n))
    return float(t), float(n - 1), float(t_pvalue(t, n - 1))


def _rankdata_with_ties(arr: np.ndarray) -> np.ndarray:
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(arr.size, dtype=float)
    sorted_vals = arr[order]
    i = 0
    while i < arr.size:
        j = i
        while j + 1 < arr.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        ranks[order[i:j + 1]] = avg_rank
        i = j + 1
    return ranks


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """Mann-Whitney U with normal approximation (tie-corrected).

    Valid for n>=8 per group; returns (U, z, two-sided p)."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    nx, ny = x.size, y.size
    if nx < 8 or ny < 8:
        raise ValueError("mann_whitney_u needs n>=8 per group")
    combined = np.concatenate([x, y])
    ranks = _rankdata_with_ties(combined)
    r_x = ranks[:nx].sum()
    u_x = r_x - nx * (nx + 1) / 2
    u_y = nx * ny - u_x
    u = min(u_x, u_y)
    mu = nx * ny / 2
    _, counts = np.unique(combined, return_counts=True)
    tie_term = ((counts ** 3 - counts).sum()) / (combined.size * (combined.size - 1))
    sigma = math.sqrt(nx * ny / 12 * ((combined.size + 1) - tie_term))
    if sigma == 0:
        return float(u), 0.0, 1.0
    diff = u - mu
    if diff == 0:
        return float(u), 0.0, 1.0
    # continuity correction moves half a unit toward the null center,
    # preserving the sign of the deviation
    z_cc = (abs(diff) - 0.5) / sigma * math.copysign(1.0, diff)
    p = 2.0 * (1.0 - _norm_cdf(abs(z_cc)))
    return float(u), float(z_cc), float(min(1.0, max(0.0, p)))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Bootstrap confidence intervals
# ---------------------------------------------------------------------------
def bootstrap_ci(values: Sequence[float], stat: Callable[[np.ndarray], float] | None = None,
                 iters: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI for ``stat(values)`` (default: mean)."""
    arr = np.asarray(values, dtype=float)
    stat_fn = stat if stat is not None else (lambda s: float(s.mean()))
    rng = np.random.default_rng(seed)
    n = arr.size
    stats = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, n, size=n)
        stats[i] = stat_fn(arr[idx])
    lo_q, hi_q = 100 * alpha / 2, 100 * (1 - alpha / 2)
    return float(np.percentile(stats, lo_q)), float(np.percentile(stats, hi_q))


def bootstrap_delta_ci(a: Sequence[float], b: Sequence[float], iters: int = 2000,
                       seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Bootstrap CI for ``mean(b) - mean(a)`` using stratified resampling."""
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    rng = np.random.default_rng(seed)
    deltas = np.empty(iters)
    for i in range(iters):
        ia = rng.integers(0, xa.size, size=xa.size)
        ib = rng.integers(0, xb.size, size=xb.size)
        deltas[i] = xb[ib].mean() - xa[ia].mean()
    lo_q, hi_q = 100 * alpha / 2, 100 * (1 - alpha / 2)
    return float(np.percentile(deltas, lo_q)), float(np.percentile(deltas, hi_q))


# ---------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------
def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    """Cohen's d with pooled SD (sign: mean_b - mean_a)."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    nx, ny = x.size, y.size
    if nx < 2 or ny < 2:
        raise ValueError("cohens_d requires >=2 observations per sample")
    sp = math.sqrt(((nx - 1) * x.var(ddof=1) + (ny - 1) * y.var(ddof=1)) / (nx + ny - 2))
    if sp == 0:
        return 0.0 if x.mean() == y.mean() else math.inf * math.copysign(1.0, y.mean() - x.mean())
    return float((y.mean() - x.mean()) / sp)


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Non-parametric effect size in [-1, 1]."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    more = (x[:, None] > y[None, :]).sum()
    less = (x[:, None] < y[None, :]).sum()
    return float((more - less) / (x.size * y.size))


# ---------------------------------------------------------------------------
# Multiple-comparison corrections
# ---------------------------------------------------------------------------
def holm_bonferroni(pvalues: Sequence[float], alpha: float = 0.05) -> list[tuple[float, bool]]:
    """Holm step-down adjustment. Returns [(adjusted_p, significant_after_correction)]."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running_max = 0.0
    rejected_all_true = True
    result: list[tuple[float, bool]] = []
    for rank, idx in enumerate(order):
        adj = min(1.0, (m - rank) * pvalues[idx])
        running_max = max(running_max, adj)
        adjusted[idx] = running_max
    for idx in order:
        sig = rejected_all_true and adjusted[idx] <= alpha
        if not sig:
            rejected_all_true = False
        result.append((idx, sig))
    flags = [False] * m
    for idx, sig in result:
        flags[idx] = sig
    return [(adjusted[i], flags[i]) for i in range(m)]


def benjamini_hochberg(pvalues: Sequence[float], q: float = 0.05) -> list[tuple[float, bool]]:
    """Benjamini-Hochberg FDR control. Returns [(adjusted_p, passes_fdr)]."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [1.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        val = min(prev, pvalues[idx] * m / (rank + 1))
        adjusted[idx] = val
        prev = val
    return [(adjusted[i], adjusted[i] <= q) for i in range(m)]


# ---------------------------------------------------------------------------
# Power / sample size planning (normal approximation; documented as such)
# ---------------------------------------------------------------------------
def required_n_per_group(d: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Approximate per-group n for a two-sided two-sample t-test.

    Uses the large-sample normal approximation n ≈ 2*(z_{α/2}+z_β)²/d²;
    conservative for small d (< 0.3) where exact t power is slightly lower.
    """
    if d == 0:
        raise ValueError("effect size d must be non-zero")
    z_a = normal_quantile(1 - alpha / 2)
    z_b = normal_quantile(power)
    return int(math.ceil(2 * ((z_a + z_b) ** 2) / (d * d)))


__all__ = [
    "describe", "welch_ttest", "paired_ttest", "mann_whitney_u",
    "bootstrap_ci", "bootstrap_delta_ci", "cohens_d", "cliffs_delta",
    "holm_bonferroni", "benjamini_hochberg", "required_n_per_group",
]
