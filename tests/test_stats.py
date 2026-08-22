"""Statistical engine tests.

Verification strategy (deliberately independent of the implementation):
1. Exact analytic identities for the incomplete beta function, which the
   t-distribution p-values are built upon.
2. Published Student-t critical-value table checks.
3. Monte-Carlo calibration: under H0 p-values must be ~uniform; a KS statistic
   bound catches systematic bias in the p machinery.
4. Power check: observed rejection rate must match planned power.
5. Permutation tests as ground truth for Welch/MWU p-values.
6. Hand-computable cases for every effect-size/correction function.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from rlab.stats import (
    benjamini_hochberg,
    bootstrap_ci,
    bootstrap_delta_ci,
    cliffs_delta,
    cohens_d,
    describe,
    dists,
    holm_bonferroni,
    mann_whitney_u,
    paired_ttest,
    required_n_per_group,
    welch_ttest,
)


# ---------------------------------------------------------------------------
# 1. Incomplete beta: exact identities
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("x", [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99])
def test_betainc_unit_params(x):
    # I_x(1, 1) = x
    assert dists.betainc(1, 1, x) == pytest.approx(x, abs=1e-12)


@pytest.mark.parametrize("x", [0.05, 0.2, 0.5, 0.8])
def test_betainc_half_half_closed_form(x):
    # I_x(1/2, 1/2) = (2/pi) * arcsin(sqrt(x))
    expected = 2 / math.pi * math.asin(math.sqrt(x))
    assert dists.betainc(0.5, 0.5, x) == pytest.approx(expected, abs=1e-10)


def test_betainc_symmetry():
    # I_x(a, b) + I_{1-x}(b, a) = 1
    for a, b, x in [(2.5, 3.5, 0.3), (4, 2, 0.7), (1, 5, 0.42)]:
        assert dists.betainc(a, b, x) + dists.betainc(b, a, 1 - x) == pytest.approx(1.0, abs=1e-10)


def test_betainc_midpoint_symmetry():
    assert dists.betainc(3, 3, 0.5) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 2. Student-t: published critical values -> two-sided p
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "t,df,published_two_sided",
    [
        (2.228, 10, 0.05),
        (2.571, 5, 0.05),
        (2.042, 30, 0.05),
        (3.169, 10, 0.01),
        (2.086, 20, 0.05),
        (1.812, 10, 0.10),
        (12.706, 1, 0.05),
    ],
)
def test_t_pvalue_against_tables(t, df, published_two_sided):
    assert dists.t_pvalue(t, df) == pytest.approx(published_two_sided, abs=2e-4)


def test_t_pvalue_symmetry_and_limits():
    assert dists.t_pvalue(1.5, 12) == dists.t_pvalue(-1.5, 12)
    assert dists.t_pvalue(0.0, 12) == 1.0
    # large df -> normal limit: |T|>1.96 has two-sided p -> 0.05
    assert dists.t_pvalue(1.96, 1e6) == pytest.approx(0.05, abs=1e-4)


# ---------------------------------------------------------------------------
# 3. Monte-Carlo calibration of welch_ttest p-values
# ---------------------------------------------------------------------------
def test_welch_pvalue_uniform_under_null():
    rng = np.random.default_rng(12345)
    pvals = []
    for _ in range(1500):
        a = rng.normal(0, 1, 18)
        b = rng.normal(0, 1, 24)
        _, _, p = welch_ttest(a, b)
        pvals.append(p)
    arr = np.sort(np.array(pvals))
    ks = max(abs((i + 1) / len(arr) - v) for i, v in enumerate(arr))
    # KS critical value at alpha=0.001, n=1500 is ~0.0505; use tighter guard
    assert ks < 0.035, f"p-values not uniform under null (KS={ks:.4f})"


def test_welch_power_matches_plan():
    d = 0.8
    n = required_n_per_group(d, alpha=0.05, power=0.8)
    rng = np.random.default_rng(777)
    hits = 0
    trials = 400
    for _ in range(trials):
        a = rng.normal(0, 1, n)
        b = rng.normal(d, 1, n)
        _, _, p = welch_ttest(a, b)
        hits += p < 0.05
    observed_power = hits / trials
    assert observed_power >= 0.72, f"observed power {observed_power:.2f} far below plan"


def test_welch_matches_permutation_test():
    rng = np.random.default_rng(42)
    a = rng.normal(0, 1, 15)
    b = rng.normal(0.7, 1.2, 17)
    _, _, p_welch = welch_ttest(a, b)

    obs = b.mean() - a.mean()
    combined = np.concatenate([a, b])
    na = len(a)
    perm = np.empty(4000)
    state = np.random.default_rng(99)
    for i in range(len(perm)):
        pm = state.permutation(combined)
        perm[i] = pm[na:].mean() - pm[:na].mean()
    p_perm = float((np.abs(perm) >= abs(obs)).mean())
    assert p_welch == pytest.approx(p_perm, abs=0.03)


def test_welch_basic_properties():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [2.0, 3.0, 4.0, 5.0, 6.0]
    t, df, p = welch_ttest(a, b)
    assert t == pytest.approx(-1.0)
    assert df == pytest.approx(8.0)
    assert 0.3 < p < 0.4
    # symmetry: swapping groups negates t, preserves p
    t2, df2, p2 = welch_ttest(b, a)
    assert t2 == pytest.approx(1.0)
    assert df2 == pytest.approx(df)
    assert p2 == pytest.approx(p)
    # identical constants -> no evidence
    t, df, p = welch_ttest([5, 5, 5], [5, 5, 5])
    assert p == 1.0


def test_welch_requires_min_samples():
    with pytest.raises(ValueError):
        welch_ttest([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# 4. Paired t-test
# ---------------------------------------------------------------------------
def test_paired_ttest_constant_shift():
    t, df, p = paired_ttest([1, 2, 3, 4, 5], [3, 4, 5, 6, 7])
    assert math.isinf(t) and p == 0.0 and df == 4


def test_paired_equals_one_sample_on_diffs():
    # Convention follows scipy.stats.ttest_rel: tests mean(a - b).
    a = np.array([3.1, 4.2, 2.9, 5.0, 4.4, 3.8, 4.9])
    b = a + np.array([0.5, -0.2, 0.9, 0.1, -0.4, 1.1, 0.3])
    t, df, p = paired_ttest(a, b)
    diffs = a - b
    t_manual = diffs.mean() / (diffs.std(ddof=1) / math.sqrt(len(diffs)))
    assert t == pytest.approx(t_manual, rel=1e-12)
    assert df == len(a) - 1
    assert p == pytest.approx(dists.t_pvalue(abs(t_manual), len(a) - 1))


def test_paired_requires_equal_length():
    with pytest.raises(ValueError):
        paired_ttest([1, 2, 3], [1, 2])


# ---------------------------------------------------------------------------
# 5. Mann-Whitney U
# ---------------------------------------------------------------------------
def test_rankdata_with_ties():
    from rlab.stats.engine import _rankdata_with_ties

    ranks = _rankdata_with_ties(np.array([10.0, 20.0, 20.0, 30.0]))
    assert ranks.tolist() == [1.0, 2.5, 2.5, 4.0]


def test_mwu_perfect_separation():
    a = list(map(float, range(1, 11)))
    b = list(map(float, range(51, 61)))
    u, z, p = mann_whitney_u(a, b)
    assert u == 0.0
    assert z < -3.0
    # normal approx at n=10/group: exact tail is ~1e-5, normal gives ~1.8e-4
    assert p < 1e-3


def test_mwu_identical_distributions():
    rng = np.random.default_rng(3)
    a = rng.normal(0, 1, 40)
    b = rng.normal(0, 1, 40)
    _, _, p = mann_whitney_u(a, b)
    assert p > 0.05


def test_mwu_agrees_with_permutation():
    rng = np.random.default_rng(11)
    a = rng.normal(0, 1, 14)
    b = rng.normal(0.9, 1.0, 14)
    u, z, p_mwu = mann_whitney_u(a, b)

    from rlab.stats.engine import _rankdata_with_ties

    combined = np.concatenate([a, b])
    nx = len(a)
    state = np.random.default_rng(5)
    hits = 0
    perms = 4000
    for _ in range(perms):
        pm = state.permutation(combined)
        ranks = _rankdata_with_ties(pm)
        ux = ranks[:nx].sum() - nx * (nx + 1) / 2
        uy = nx * (len(b)) - ux
        if min(ux, uy) <= u + 1e-9:
            hits += 1
    p_perm = hits / perms
    assert p_mwu == pytest.approx(p_perm, abs=0.025)


def test_mwu_needs_large_samples():
    with pytest.raises(ValueError):
        mann_whitney_u([1.0] * 5, [2.0] * 5)


# ---------------------------------------------------------------------------
# 6. Bootstrap
# ---------------------------------------------------------------------------
def test_bootstrap_coverage_of_true_mean():
    rng = np.random.default_rng(2024)
    covered = 0
    reps = 220
    for i in range(reps):
        sample = rng.normal(0.0, 1.0, 24)
        lo, hi = bootstrap_ci(sample, iters=400, seed=i)
        covered += lo <= 0.0 <= hi
    rate = covered / reps
    assert 0.88 <= rate <= 0.99, f"bootstrap coverage {rate:.2f}"


def test_bootstrap_delta_detects_shift():
    rng = np.random.default_rng(8)
    a = rng.normal(0, 1, 40)
    b = rng.normal(0.9, 1, 40)
    lo, hi = bootstrap_delta_ci(a, b, iters=800, seed=1)
    # CI must exclude zero comfortably and be centered near the true shift
    assert lo > 0.3
    assert 0.4 < (lo + hi) / 2 < 1.6


def test_bootstrap_deterministic_given_seed():
    vals = np.arange(1, 31) + np.sin(np.arange(30))
    ci1 = bootstrap_ci(vals, seed=7, iters=300)
    ci2 = bootstrap_ci(vals, seed=7, iters=300)
    ci3 = bootstrap_ci(vals, seed=8, iters=300)
    assert ci1 == ci2
    assert ci1 != ci3


# ---------------------------------------------------------------------------
# 7. Effect sizes
# ---------------------------------------------------------------------------
def test_cohens_d_hand_case():
    a = [1.0, 2.0, 3.0]
    b = [5.0, 6.0, 7.0]
    assert cohens_d(a, b) == pytest.approx(4.0)
    assert cohens_d(b, a) == pytest.approx(-4.0)


def test_cliffs_delta_cases():
    # delta = P(X>Y) - P(X<Y); X=first group
    assert cliffs_delta([5, 6, 7, 8], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert cliffs_delta([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)
    assert cliffs_delta([1, 2, 3, 4], [3, 4, 5, 6]) == pytest.approx(-0.75)


# ---------------------------------------------------------------------------
# 8. Multiple comparison corrections
# ---------------------------------------------------------------------------
def test_holm_bonferroni_hand_case():
    pvals = [0.01, 0.04, 0.03, 0.005]
    results = holm_bonferroni(pvals, alpha=0.05)
    flags = [sig for _, sig in results]
    # smallest two survive step-down; 0.03*2=0.06 stops the procedure
    assert flags == [True, False, False, True]


def test_holm_adjusted_not_smaller_than_raw():
    pvals = [0.001, 0.02, 0.2, 0.5, 0.9]
    results = holm_bonferroni(pvals)
    for (adj, _flag), raw in zip(results, pvals):
        assert adj >= raw - 1e-12


def test_bh_all_equal_small():
    results = benjamini_hochberg([0.01, 0.01, 0.01], q=0.05)
    assert all(flag for _, flag in results)
    # BH-adjusted value for equal small p is min_j>=i (m*p_j/j) = 0.01 here
    assert all(adj == pytest.approx(0.01) for adj, _ in results)


def test_bh_monotonic_adjustment():
    pvals = [0.003, 0.01, 0.04, 0.12, 0.44, 0.71]
    results = benjamini_hochberg(pvals, q=0.1)
    for (adj, _), raw in zip(results, pvals):
        assert adj >= raw - 1e-12


# ---------------------------------------------------------------------------
# 9. Planning helpers & misc
# ---------------------------------------------------------------------------
def test_required_n_classic_value():
    # Cohen (1988): d=0.5, alpha=.05, power=.8 -> 64/group (exact t);
    # normal approximation should land at 63-65.
    n = required_n_per_group(0.5)
    assert 62 <= n <= 65


def test_normal_quantile_reference_values():
    assert dists.normal_quantile(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert dists.normal_quantile(0.5) == pytest.approx(0.0, abs=1e-8)
    assert dists.normal_quantile(0.8) == pytest.approx(0.841621, abs=1e-4)


def test_describe_known_array():
    d = describe([1.0, 2.0, 3.0, 4.0, 5.0])
    assert d["n"] == 5
    assert d["mean"] == 3.0
    assert d["stdev"] == pytest.approx(math.sqrt(2.5))
    assert d["median"] == 3.0
    assert d["min"] == 1.0 and d["max"] == 5.0


def test_describe_empty_is_safe():
    d = describe([])
    assert d["n"] == 0
    assert math.isnan(d["mean"])


# ---------------------------------------------------------------------------
# 10. Wilcoxon signed-rank
# ---------------------------------------------------------------------------
def test_wilcoxon_perfect_shift_exact():
    from rlab.stats import wilcoxon_signed_rank

    a = list(map(float, range(1, 11)))
    b = [v + 2.0 for v in a]           # all diffs negative (a - b = -2)
    w, p = wilcoxon_signed_rank(a, b)
    assert w == 0.0
    # exact: P(W+ <= 0) = 1/1024 -> two-sided ~0.002
    assert p == pytest.approx(2 / 1024)


def test_wilcoxon_symmetric_null_not_significant():
    from rlab.stats import wilcoxon_signed_rank

    rng = np.random.default_rng(4)
    diffs = rng.normal(0, 1, 40)
    a = (10 + diffs / 2).tolist()
    b = (10 - diffs / 2).tolist()
    _, p = wilcoxon_signed_rank(a, b)
    assert p > 0.05


def test_wilcoxon_matches_bruteforce_on_small_n():
    """Exact path must agree with an independent brute-force enumeration."""
    from itertools import product

    from rlab.stats import wilcoxon_signed_rank
    from rlab.stats.engine import _rankdata_with_ties

    rng = np.random.default_rng(9)
    base = rng.normal(5, 2, 8)
    a = (base + rng.normal(0.3, 0.8, 8)).tolist()
    b = base.tolist()

    diff = np.asarray(a) - np.asarray(b)
    keep = diff != 0
    ranks = _rankdata_with_ties(np.abs(diff[keep]))
    signs_all = list(product([False, True], repeat=int(keep.sum())))
    w_obs = float(ranks[diff[keep] > 0].sum())

    def w_of(signs):
        return float(sum(r for r, s in zip(ranks, signs) if s))

    ge = sum(1 for sgn in signs_all if w_of(sgn) >= w_obs - 1e-9)
    p_manual = min(1.0, 2.0 * min(ge / len(signs_all), 1.0))
    _, p = wilcoxon_signed_rank(a, b)
    assert p == pytest.approx(p_manual)


def test_wilcoxon_rejects_tiny_samples():
    from rlab.stats import wilcoxon_signed_rank

    with pytest.raises(ValueError):
        wilcoxon_signed_rank([1.0, 2.0], [2.0, 1.0])
