"""Special functions needed for exact-ish statistical tests.

Implemented from first principles so we do not depend on SciPy:

* regularized incomplete beta function ``betainc`` via continued fractions
  (Lentz's method; standard Numerical Recipes formulation)
* Student-t survival/two-sided p-values
* normal CDF / quantile

Unit-tested against published table values in ``tests/test_stats.py``.
"""

from __future__ import annotations

import math

_EPS = 3.0e-12
_FPMIN = 1.0e-300


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta (NR 3rd ed., eq. 6.4.4)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """Survival function P(T > t) for Student-t."""
    if df <= 0:
        raise ValueError("df must be positive")
    t2 = t * t
    p_two_sided = betainc(0.5 * df, 0.5, df / (df + t2))
    # For t>0: sf = p_two_sided / 2 ; symmetric handling below is exact.
    if t >= 0:
        return 0.5 * p_two_sided
    return 1.0 - 0.5 * p_two_sided


def t_pvalue(t: float, df: float) -> float:
    """Two-sided p-value."""
    if df <= 0:
        raise ValueError("df must be positive")
    return min(1.0, betainc(0.5 * df, 0.5, df / (df + t * t)))


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_quantile(p: float) -> float:
    """Inverse CDF by bisection; plenty accurate (|x|<9) and dependency-free."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
