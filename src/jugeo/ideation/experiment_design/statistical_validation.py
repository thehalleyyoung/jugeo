from __future__ import annotations

"""Statistical Validation for Ideation Experiments — Chapter 53
===============================================================

This module provides a self-contained statistical validation toolkit for
ideation experiments, implementing the core tests described in Chapter 53.
All arithmetic is implemented from first principles using only :mod:`math`,
:mod:`statistics`, and :mod:`random` from the standard library, so there is
no external dependency on NumPy, SciPy, or similar packages.

Core formulas implemented
--------------------------

**Two-sample t-statistic** (equal variances, pooled):

    t = (x̄₁ - x̄₂) / [s_p · √(1/n₁ + 1/n₂)]

where the pooled standard deviation is

    s_p = √[ ((n₁-1)s₁² + (n₂-1)s₂²) / (n₁ + n₂ - 2) ]

**Welch's t-statistic** (unequal variances):

    t_W = (x̄₁ - x̄₂) / √(s₁²/n₁ + s₂²/n₂)

with degrees of freedom given by the Welch–Satterthwaite equation:

    df_W = (s₁²/n₁ + s₂²/n₂)² / [(s₁²/n₁)²/(n₁-1) + (s₂²/n₂)²/(n₂-1)]

**Cohen's d** (standardised effect size):

    d = (μ̂₁ - μ̂₂) / σ̂_pooled

**Minimum sample size** (power analysis, two-sample, equal allocation):

    n ≥ (z_{α/2} + z_β)² · (σ₁² + σ₂²) / δ²

which simplifies for equal-variance populations (σ₁ = σ₂ = σ, δ = d·σ) to:

    n ≥ 2 · (z_{α/2} + z_β)² / d²

**Multiple testing corrections**

- *Bonferroni*:          p̃ᵢ = min(n · pᵢ, 1)
- *Holm step-down*:      p̃ᵢ = min(max_{j≤i}[(n-j+1)·p_(j)], 1)
- *Benjamini–Hochberg*:  p̃ᵢ = min_{j≥i}[n/j · p_(j)]   (FDR)

Design Notes
------------
- :class:`StatisticalValidator` is the primary computational engine.
- :class:`SignificanceThreshold` is a frozen configuration dataclass that
  encodes α, β, and minimum effect size for a study.
- :class:`MultipleTestingCorrection` handles family-wise error rate control.
- :class:`ReportGenerator` renders human-readable reports from raw results.

References
----------
- Student (W. S. Gosset, 1908).  "The probable error of a mean."  *Biometrika*.
- Welch, B. L. (1947).  "The generalization of 'Student's' problem".
  *Biometrika*.
- Cohen, J. (1988).  *Statistical Power Analysis for the Behavioral Sciences*
  (2nd ed.).  Hillsdale, NJ: Lawrence Erlbaum Associates.
- Benjamini, Y. & Hochberg, Y. (1995).  "Controlling the false discovery rate."
  *Journal of the Royal Statistical Society, Series B*, 57(1), 289–300.
"""

import logging
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "StatisticalValidator",
    "SignificanceThreshold",
    "MultipleTestingCorrection",
    "ReportGenerator",
]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*.

    Parameters
    ----------
    values:
        Non-empty list of numeric values.

    Returns
    -------
    float
        Arithmetic mean x̄ = (1/n) Σ xᵢ.

    Raises
    ------
    ValueError
        If *values* is empty.

    Examples
    --------
    >>> _mean([1.0, 2.0, 3.0])
    2.0
    """
    if not values:
        raise ValueError("Cannot compute mean of an empty sequence.")
    return sum(values) / len(values)


def _variance(values: list[float], ddof: int = 1) -> float:
    """Return the variance of *values* with *ddof* degrees-of-freedom correction.

    Parameters
    ----------
    values:
        List of numeric values.  Requires at least ``ddof + 1`` elements.
    ddof:
        Delta degrees of freedom.  ``ddof=1`` gives the unbiased sample
        variance s²; ``ddof=0`` gives the population variance σ².

    Returns
    -------
    float
        Variance s² = [Σ(xᵢ - x̄)²] / (n - ddof).

    Raises
    ------
    ValueError
        If *values* has fewer than ``ddof + 1`` elements.

    Examples
    --------
    >>> abs(_variance([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) - 4.571) < 0.01
    True
    """
    n = len(values)
    if n <= ddof:
        raise ValueError(
            f"Need at least {ddof + 1} values for variance with ddof={ddof}, "
            f"got {n}."
        )
    mu = _mean(values)
    return sum((x - mu) ** 2 for x in values) / (n - ddof)


def _std(values: list[float], ddof: int = 1) -> float:
    """Return the standard deviation of *values*.

    Parameters
    ----------
    values:
        List of numeric values.
    ddof:
        Delta degrees of freedom.  Passed directly to :func:`_variance`.

    Returns
    -------
    float
        Standard deviation s = √(variance).

    Examples
    --------
    >>> abs(_std([1.0, 2.0, 3.0]) - 1.0) < 1e-10
    True
    """
    return math.sqrt(_variance(values, ddof=ddof))


def _pooled_std(s1: float, n1: int, s2: float, n2: int) -> float:
    """Return the pooled standard deviation for two independent samples.

    The pooled estimate is:

        s_p = √[ ((n₁-1)·s₁² + (n₂-1)·s₂²) / (n₁ + n₂ - 2) ]

    Parameters
    ----------
    s1:
        Standard deviation of sample 1.
    n1:
        Size of sample 1 (must be ≥ 2).
    s2:
        Standard deviation of sample 2.
    n2:
        Size of sample 2 (must be ≥ 2).

    Returns
    -------
    float
        Pooled standard deviation s_p.

    Raises
    ------
    ValueError
        If either sample has fewer than 2 observations.

    Examples
    --------
    >>> abs(_pooled_std(1.0, 10, 1.0, 10) - 1.0) < 1e-10
    True
    """
    if n1 < 2 or n2 < 2:
        raise ValueError(
            f"Both samples must have at least 2 observations; got n1={n1}, n2={n2}."
        )
    numerator = (n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2
    denominator = n1 + n2 - 2
    return math.sqrt(numerator / denominator)


def _normal_cdf(z: float) -> float:
    """Return Φ(z), the standard-normal CDF evaluated at *z*.

    Uses the identity Φ(z) = [1 + erf(z / √2)] / 2.

    Parameters
    ----------
    z:
        Standard-normal quantile.

    Returns
    -------
    float
        Probability P(Z ≤ z) ∈ (0, 1).

    Examples
    --------
    >>> abs(_normal_cdf(0.0) - 0.5) < 1e-10
    True
    >>> abs(_normal_cdf(1.96) - 0.975) < 0.001
    True
    """
    return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0


def _reg_inc_beta(x: float, a: float, b: float) -> float:
    """Compute the regularized incomplete beta function I_x(a, b).

    Uses the modified Lentz continued-fraction algorithm (Numerical Recipes,
    §6.4).  For x > (a+1)/(a+b+2), the symmetry relation
    I_x(a,b) = 1 − I_{1−x}(b,a) is applied to improve convergence.

    Parameters
    ----------
    x:
        Evaluation point ∈ [0, 1].
    a, b:
        Shape parameters (both must be > 0).

    Returns
    -------
    float
        Regularised incomplete beta I_x(a, b) ∈ [0, 1].
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # Symmetry: swap for better convergence when x is large
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _reg_inc_beta(1.0 - x, b, a)
    # Log of the front factor
    log_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    front = math.exp(log_front) / a
    # Modified Lentz continued-fraction evaluation
    FPMIN = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        # Even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        # Odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            break
    return front * h


def _t_cdf(t: float, df: int) -> float:
    """Return P(T ≤ t) for a t-distribution with *df* degrees of freedom.

    For df ≥ 200 the standard-normal approximation is used; for smaller df
    the exact regularized incomplete beta representation is evaluated via
    :func:`_reg_inc_beta`.

    The relationship used is:

        P(T ≤ t) = 1 − ½ · I_{df/(df+t²)}(df/2, ½)   for t ≥ 0

    Parameters
    ----------
    t:
        t-statistic.
    df:
        Degrees of freedom (positive integer).

    Returns
    -------
    float
        Cumulative probability P(T ≤ t) ∈ (0, 1).

    Raises
    ------
    ValueError
        If *df* is not positive.

    Examples
    --------
    >>> abs(_t_cdf(0.0, 10) - 0.5) < 1e-6
    True
    >>> _t_cdf(3.0, 30) > 0.99
    True
    """
    if df <= 0:
        raise ValueError(f"Degrees of freedom must be positive, got {df}.")
    if df >= 200:
        return _normal_cdf(t)
    x = float(df) / (float(df) + t * t)
    p_tail = _reg_inc_beta(x, df / 2.0, 0.5)
    if t >= 0.0:
        return 1.0 - 0.5 * p_tail
    else:
        return 0.5 * p_tail


def _beta_quantile(p: float) -> float:
    """Return an approximate standard-normal quantile (z-score) for probability *p*.

    Uses the rational approximation of Abramowitz & Stegun (formula 26.2.17),
    accurate to ±4.5 × 10⁻⁴.  This is used in power analysis to convert
    α and β levels to z-scores.

    Parameters
    ----------
    p:
        Probability ∈ (0, 1).  Values outside (0, 1) are clamped.

    Returns
    -------
    float
        Approximate z-score z such that Φ(z) ≈ p.

    Examples
    --------
    >>> abs(_beta_quantile(0.975) - 1.96) < 0.01
    True
    >>> abs(_beta_quantile(0.5)) < 0.001
    True
    """
    p = max(1e-15, min(1.0 - 1e-15, p))
    if p < 0.5:
        sign = -1.0
        q = p
    else:
        sign = 1.0
        q = 1.0 - p
    t = math.sqrt(-2.0 * math.log(q))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    z = t - (c0 + c1 * t + c2 * t ** 2) / (1.0 + d1 * t + d2 * t ** 2 + d3 * t ** 3)
    return sign * z


# ---------------------------------------------------------------------------
# StatisticalValidator
# ---------------------------------------------------------------------------


class StatisticalValidator:
    """Core statistical engine for validating ideation experiment results.

    Implements two-sample and one-sample t-tests, Welch's t-test, bootstrap
    confidence intervals, Cohen's d, and sample-size power analysis entirely
    in standard-library Python.

    Parameters
    ----------
    alpha:
        Significance level α for hypothesis tests (default 0.05).

    Examples
    --------
    >>> sv = StatisticalValidator(alpha=0.05)
    >>> result = sv.t_test([1.0, 2.0, 3.0, 4.0, 5.0],
    ...                    [2.0, 3.0, 4.0, 5.0, 6.0])
    >>> "p_value" in result and "statistic" in result
    True
    """

    def __init__(self, alpha: float = 0.05) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1), got {alpha}.")
        self.alpha = alpha
        _log.debug("StatisticalValidator initialised (alpha=%.3f)", alpha)

    def t_test(
        self, sample1: list[float], sample2: list[float]
    ) -> dict[str, Any]:
        """Compute an independent two-sample t-test assuming equal variances.

        The pooled-variance t-statistic is:

            t = (x̄₁ − x̄₂) / [s_p · √(1/n₁ + 1/n₂)]

        with df = n₁ + n₂ − 2 degrees of freedom.  A two-tailed p-value is
        computed from :func:`_t_cdf`.

        Parameters
        ----------
        sample1, sample2:
            Independent numeric samples (each must have at least 2 elements).

        Returns
        -------
        dict
            Keys: ``'statistic'`` (float), ``'p_value'`` (float),
            ``'df'`` (int), ``'ci_lower'`` (float), ``'ci_upper'`` (float),
            ``'significant'`` (bool), ``'mean1'`` (float), ``'mean2'`` (float),
            ``'pooled_std'`` (float).

        Raises
        ------
        ValueError
            If either sample has fewer than 2 elements.

        Examples
        --------
        >>> sv = StatisticalValidator()
        >>> r = sv.t_test(list(range(10)), [x + 2 for x in range(10)])
        >>> r["significant"]
        True
        """
        n1, n2 = len(sample1), len(sample2)
        if n1 < 2 or n2 < 2:
            raise ValueError(
                f"Each sample must have ≥ 2 observations; got n1={n1}, n2={n2}."
            )
        m1, m2 = _mean(sample1), _mean(sample2)
        s1, s2 = _std(sample1), _std(sample2)
        sp = _pooled_std(s1, n1, s2, n2)
        se = sp * math.sqrt(1.0 / n1 + 1.0 / n2)
        if se == 0.0:
            se = 1e-300
        t_stat = (m1 - m2) / se
        df = n1 + n2 - 2
        # Two-tailed p-value: p = 2 * min(CDF(t), 1 - CDF(t))
        cdf_val = _t_cdf(t_stat, df)
        p_value = 2.0 * min(cdf_val, 1.0 - cdf_val)
        # 95 % confidence interval for the difference in means
        t_crit = _beta_quantile(1.0 - self.alpha / 2.0)
        margin = t_crit * se
        diff = m1 - m2
        _log.debug(
            "t_test: t=%.4f df=%d p=%.4f sig=%s",
            t_stat, df, p_value, p_value < self.alpha,
        )
        return {
            "statistic": t_stat,
            "p_value": p_value,
            "df": df,
            "ci_lower": diff - margin,
            "ci_upper": diff + margin,
            "significant": p_value < self.alpha,
            "mean1": m1,
            "mean2": m2,
            "pooled_std": sp,
        }

    def bootstrap_ci(
        self,
        data: list[float],
        n: int = 1000,
        confidence: float = 0.95,
        seed: int = 42,
    ) -> tuple[float, float]:
        """Compute a bootstrap confidence interval for the mean of *data*.

        Resamples *data* with replacement *n* times, computes the mean of
        each resample, then takes the empirical percentile-based interval.

        Parameters
        ----------
        data:
            Numeric observations.
        n:
            Number of bootstrap resamples (default 1000).
        confidence:
            Confidence level, e.g. 0.95 for a 95 % interval.
        seed:
            Random seed for reproducibility.

        Returns
        -------
        tuple[float, float]
            ``(lower, upper)`` confidence interval for the mean.

        Raises
        ------
        ValueError
            If *data* is empty.

        Examples
        --------
        >>> sv = StatisticalValidator()
        >>> lo, hi = sv.bootstrap_ci([1.0] * 50, n=200)
        >>> lo == hi == 1.0
        True
        """
        if not data:
            raise ValueError("Cannot compute bootstrap CI of an empty dataset.")
        rng = random.Random(seed)
        boot_means: list[float] = []
        k = len(data)
        for _ in range(n):
            resample = [rng.choice(data) for _ in range(k)]
            boot_means.append(_mean(resample))
        boot_means.sort()
        alpha_tail = (1.0 - confidence) / 2.0
        lo_idx = max(0, int(math.floor(alpha_tail * n)))
        hi_idx = min(n - 1, int(math.ceil((1.0 - alpha_tail) * n)) - 1)
        _log.debug(
            "bootstrap_ci: n_resamples=%d conf=%.2f CI=[%.4f, %.4f]",
            n, confidence, boot_means[lo_idx], boot_means[hi_idx],
        )
        return boot_means[lo_idx], boot_means[hi_idx]

    def effect_size(self, sample1: list[float], sample2: list[float]) -> float:
        """Compute Cohen's d for the difference between *sample1* and *sample2*.

        The pooled-standard-deviation formula is used:

            d = (x̄₁ − x̄₂) / s_pooled

        A positive d indicates sample1 has a higher mean.

        Parameters
        ----------
        sample1, sample2:
            Independent numeric samples (each ≥ 2 elements).

        Returns
        -------
        float
            Cohen's d.  Conventional thresholds: |d| < 0.2 negligible,
            0.2–0.5 small, 0.5–0.8 medium, ≥ 0.8 large.

        Examples
        --------
        >>> sv = StatisticalValidator()
        >>> abs(sv.effect_size([3.0] * 10, [0.0] * 10) - 3.0) < 1e-6
        True
        """
        n1, n2 = len(sample1), len(sample2)
        if n1 < 2 or n2 < 2:
            raise ValueError(
                f"Each sample must have ≥ 2 observations; got n1={n1}, n2={n2}."
            )
        m1, m2 = _mean(sample1), _mean(sample2)
        s1, s2 = _std(sample1), _std(sample2)
        sp = _pooled_std(s1, n1, s2, n2)
        if sp == 0.0:
            return 0.0
        return (m1 - m2) / sp

    def power_analysis(
        self, effect_size: float, alpha: float, power: float
    ) -> int:
        """Return the minimum per-group sample size for a two-sample t-test.

        Uses the normal approximation:

            n ≥ 2 · (z_{α/2} + z_β)² / d²

        where d is the target Cohen's d, z_{α/2} is the critical z for the
        two-tailed test, and z_β = Φ⁻¹(power).

        Parameters
        ----------
        effect_size:
            Target Cohen's d (must be > 0).
        alpha:
            Two-tailed significance level α ∈ (0, 1).
        power:
            Desired statistical power 1−β ∈ (0, 1).

        Returns
        -------
        int
            Minimum per-group sample size n (always ≥ 1).

        Raises
        ------
        ValueError
            If *effect_size* is ≤ 0.

        Examples
        --------
        >>> sv = StatisticalValidator()
        >>> sv.power_analysis(0.5, 0.05, 0.80) >= 50
        True
        """
        if effect_size <= 0.0:
            raise ValueError(
                f"effect_size must be positive for power analysis, got {effect_size}."
            )
        z_alpha = _beta_quantile(1.0 - alpha / 2.0)
        z_beta = _beta_quantile(power)
        n_float = 2.0 * (z_alpha + z_beta) ** 2 / effect_size ** 2
        n = max(1, int(math.ceil(n_float)))
        _log.debug(
            "power_analysis: d=%.3f alpha=%.3f power=%.3f → n=%d",
            effect_size, alpha, power, n,
        )
        return n

    def one_sample_t_test(
        self, sample: list[float], mu0: float = 0.0
    ) -> dict[str, Any]:
        """Compute a one-sample t-test against the null hypothesis μ = *mu0*.

        The test statistic is:

            t = (x̄ − μ₀) / (s / √n)

        with df = n − 1 degrees of freedom.

        Parameters
        ----------
        sample:
            Numeric observations (at least 2 elements).
        mu0:
            Null-hypothesis population mean (default 0.0).

        Returns
        -------
        dict
            Keys: ``'statistic'``, ``'p_value'``, ``'df'``, ``'ci_lower'``,
            ``'ci_upper'``, ``'significant'``, ``'mean'``, ``'std'``.

        Raises
        ------
        ValueError
            If *sample* has fewer than 2 elements.

        Examples
        --------
        >>> sv = StatisticalValidator()
        >>> r = sv.one_sample_t_test([5.0] * 20, mu0=5.0)
        >>> r["significant"]
        False
        """
        n = len(sample)
        if n < 2:
            raise ValueError(f"Sample must have ≥ 2 observations, got {n}.")
        m = _mean(sample)
        s = _std(sample)
        se = s / math.sqrt(n)
        if se == 0.0:
            se = 1e-300
        t_stat = (m - mu0) / se
        df = n - 1
        cdf_val = _t_cdf(t_stat, df)
        p_value = 2.0 * min(cdf_val, 1.0 - cdf_val)
        t_crit = _beta_quantile(1.0 - self.alpha / 2.0)
        margin = t_crit * se
        return {
            "statistic": t_stat,
            "p_value": p_value,
            "df": df,
            "ci_lower": m - margin,
            "ci_upper": m + margin,
            "significant": p_value < self.alpha,
            "mean": m,
            "std": s,
        }

    def welch_t_test(
        self, sample1: list[float], sample2: list[float]
    ) -> dict[str, Any]:
        """Compute Welch's t-test for two independent samples with unequal variances.

        Uses the Welch–Satterthwaite degrees-of-freedom correction:

            df_W = (s₁²/n₁ + s₂²/n₂)² / [(s₁²/n₁)²/(n₁−1) + (s₂²/n₂)²/(n₂−1)]

        This test is preferred over the equal-variance t-test when sample
        sizes or variances differ substantially.

        Parameters
        ----------
        sample1, sample2:
            Independent numeric samples (each ≥ 2 elements).

        Returns
        -------
        dict
            Keys: ``'statistic'``, ``'p_value'``, ``'df'`` (float, Welch df),
            ``'ci_lower'``, ``'ci_upper'``, ``'significant'``,
            ``'mean1'``, ``'mean2'``, ``'var1'``, ``'var2'``.

        Raises
        ------
        ValueError
            If either sample has fewer than 2 elements.

        Examples
        --------
        >>> sv = StatisticalValidator()
        >>> r = sv.welch_t_test([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
        >>> r["significant"]
        True
        """
        n1, n2 = len(sample1), len(sample2)
        if n1 < 2 or n2 < 2:
            raise ValueError(
                f"Each sample must have ≥ 2 observations; got n1={n1}, n2={n2}."
            )
        m1, m2 = _mean(sample1), _mean(sample2)
        v1 = _variance(sample1)
        v2 = _variance(sample2)
        v1n = v1 / n1
        v2n = v2 / n2
        se = math.sqrt(v1n + v2n)
        if se == 0.0:
            se = 1e-300
        t_stat = (m1 - m2) / se
        # Welch–Satterthwaite effective degrees of freedom
        df_float = (v1n + v2n) ** 2 / (v1n ** 2 / (n1 - 1) + v2n ** 2 / (n2 - 1))
        df = max(1, int(round(df_float)))
        cdf_val = _t_cdf(t_stat, df)
        p_value = 2.0 * min(cdf_val, 1.0 - cdf_val)
        t_crit = _beta_quantile(1.0 - self.alpha / 2.0)
        margin = t_crit * se
        diff = m1 - m2
        _log.debug(
            "welch_t_test: t=%.4f df=%.1f p=%.4f sig=%s",
            t_stat, df_float, p_value, p_value < self.alpha,
        )
        return {
            "statistic": t_stat,
            "p_value": p_value,
            "df": df,
            "ci_lower": diff - margin,
            "ci_upper": diff + margin,
            "significant": p_value < self.alpha,
            "mean1": m1,
            "mean2": m2,
            "var1": v1,
            "var2": v2,
        }


# ---------------------------------------------------------------------------
# SignificanceThreshold
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignificanceThreshold:
    """Frozen configuration dataclass encoding the significance policy for a study.

    All study-level decisions about what constitutes a "significant" result,
    an "adequately powered" effect, or an "acceptable" sample size should be
    encoded in a single :class:`SignificanceThreshold` instance and shared
    across all analyses in the study.

    Attributes
    ----------
    alpha:
        Two-tailed significance level α (default 0.05).
    beta:
        Type-II error rate β; power = 1 − β (default 0.20 → 80 % power).
    minimum_effect_size:
        Minimum Cohen's d considered scientifically meaningful (default 0.2).
    minimum_sample_size:
        Minimum number of observations per group below which results are
        considered underpowered regardless of the p-value (default 10).

    Examples
    --------
    >>> thresh = SignificanceThreshold()
    >>> thresh.is_significant(0.03)
    True
    >>> thresh.is_significant(0.06)
    False
    """

    alpha: float = 0.05
    beta: float = 0.20
    minimum_effect_size: float = 0.20
    minimum_sample_size: int = 10

    def is_significant(self, p_value: float) -> bool:
        """Return ``True`` if *p_value* clears the significance threshold.

        Parameters
        ----------
        p_value:
            Observed p-value from a statistical test.

        Returns
        -------
        bool
            ``True`` iff p_value < self.alpha.

        Examples
        --------
        >>> SignificanceThreshold(alpha=0.01).is_significant(0.005)
        True
        """
        return p_value < self.alpha

    def has_adequate_power(self, observed_effect: float) -> bool:
        """Return ``True`` if *observed_effect* clears the minimum effect size.

        A result is considered to have adequate power when the observed
        Cohen's d is at least as large as :attr:`minimum_effect_size`.

        Parameters
        ----------
        observed_effect:
            Observed Cohen's |d| (absolute value is taken internally).

        Returns
        -------
        bool

        Examples
        --------
        >>> thresh = SignificanceThreshold(minimum_effect_size=0.5)
        >>> thresh.has_adequate_power(0.6)
        True
        >>> thresh.has_adequate_power(0.3)
        False
        """
        return abs(observed_effect) >= self.minimum_effect_size

    def summary(self) -> str:
        """Return a human-readable summary of this threshold configuration.

        Returns
        -------
        str
            Multi-line string.

        Examples
        --------
        >>> print(SignificanceThreshold().summary())  # doctest: +ELLIPSIS
        SignificanceThreshold...
        """
        power = 1.0 - self.beta
        lines = [
            "SignificanceThreshold",
            f"  α (significance level)      : {self.alpha}",
            f"  β (type-II error rate)       : {self.beta}  →  power = {power:.0%}",
            f"  Minimum effect size (Cohen d): {self.minimum_effect_size}",
            f"  Minimum sample size per group: {self.minimum_sample_size}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MultipleTestingCorrection
# ---------------------------------------------------------------------------


class MultipleTestingCorrection:
    """Apply family-wise error rate or FDR corrections to a vector of p-values.

    When multiple hypotheses are tested simultaneously the probability of at
    least one Type-I error inflates beyond α.  This class implements three
    widely-used corrections:

    - **Bonferroni** (most conservative, controls FWER).
    - **Holm** (uniformly more powerful than Bonferroni, controls FWER).
    - **Benjamini–Hochberg** (controls false discovery rate).

    Examples
    --------
    >>> mtc = MultipleTestingCorrection()
    >>> raw = [0.01, 0.04, 0.20, 0.003]
    >>> adj = mtc.bonferroni(raw)
    >>> adj[0]
    0.04
    """

    def __init__(self) -> None:
        _log.debug("MultipleTestingCorrection initialised")

    def bonferroni(self, p_values: list[float]) -> list[float]:
        """Apply the Bonferroni correction: p̃ᵢ = min(n · pᵢ, 1).

        The Bonferroni correction is the most conservative family-wise
        correction.  Each adjusted p-value is simply the raw value multiplied
        by the number of tests, clipped to 1.0.

        Parameters
        ----------
        p_values:
            Raw p-values from independent (or positively correlated) tests.

        Returns
        -------
        list[float]
            Bonferroni-adjusted p-values, preserving input order.

        Examples
        --------
        >>> MultipleTestingCorrection().bonferroni([0.01, 0.05]) == [0.02, 0.10]
        True
        """
        n = len(p_values)
        return [min(p * n, 1.0) for p in p_values]

    def holm(self, p_values: list[float]) -> list[float]:
        """Apply the Holm step-down correction (controls FWER).

        Procedure:
        1. Sort p-values in ascending order, keeping track of original indices.
        2. Multiply the i-th sorted p-value (0-indexed) by (n − i).
        3. Enforce monotonicity: each adjusted value is at least as large
           as the previous one.
        4. Return adjusted values in the original index order.

        Parameters
        ----------
        p_values:
            Raw p-values.

        Returns
        -------
        list[float]
            Holm-adjusted p-values ∈ [0, 1], in original order.

        Examples
        --------
        >>> mtc = MultipleTestingCorrection()
        >>> adj = mtc.holm([0.01, 0.04, 0.20])
        >>> adj[0] <= adj[1] <= adj[2]
        True
        """
        n = len(p_values)
        order = sorted(range(n), key=lambda i: p_values[i])
        adjusted = [0.0] * n
        running_max = 0.0
        for rank, idx in enumerate(order):
            factor = n - rank
            adj = min(p_values[idx] * factor, 1.0)
            running_max = max(running_max, adj)
            adjusted[idx] = running_max
        return adjusted

    def benjamini_hochberg(self, p_values: list[float]) -> list[float]:
        """Apply the Benjamini–Hochberg FDR correction.

        Procedure:
        1. Sort p-values in ascending order, tracking original indices.
        2. Assign adjusted p-value for rank i (1-indexed from smallest):
           p̃_(i) = min_{j ≥ i} [n / j · p_(j)]
        3. Enforce monotonicity from the top down.
        4. Clip to [0, 1].

        Parameters
        ----------
        p_values:
            Raw p-values.

        Returns
        -------
        list[float]
            BH-adjusted p-values ∈ [0, 1], in original order.

        Examples
        --------
        >>> mtc = MultipleTestingCorrection()
        >>> raw = [0.001, 0.008, 0.039, 0.041, 0.210]
        >>> adj = mtc.benjamini_hochberg(raw)
        >>> adj[0] < adj[-1]
        True
        """
        n = len(p_values)
        order = sorted(range(n), key=lambda i: p_values[i])
        adjusted = [0.0] * n
        running_min = 1.0
        for rank in range(n - 1, -1, -1):
            idx = order[rank]
            adj = min(p_values[idx] * n / (rank + 1), 1.0)
            running_min = min(running_min, adj)
            adjusted[idx] = running_min
        return adjusted

    def apply(
        self, p_values: list[float], method: str = "bonferroni"
    ) -> list[float]:
        """Apply a named correction method to *p_values*.

        Parameters
        ----------
        p_values:
            Raw p-values.
        method:
            One of ``'bonferroni'``, ``'holm'``, ``'benjamini_hochberg'``
            (or ``'bh'`` as an alias).

        Returns
        -------
        list[float]
            Adjusted p-values.

        Raises
        ------
        ValueError
            If *method* is not recognised.

        Examples
        --------
        >>> MultipleTestingCorrection().apply([0.01], method="holm")
        [0.01]
        """
        method_lc = method.lower().replace("-", "_")
        if method_lc == "bonferroni":
            return self.bonferroni(p_values)
        if method_lc in ("holm", "holm_bonferroni"):
            return self.holm(p_values)
        if method_lc in ("benjamini_hochberg", "bh", "fdr"):
            return self.benjamini_hochberg(p_values)
        raise ValueError(
            f"Unknown correction method '{method}'.  "
            "Choose one of: 'bonferroni', 'holm', 'benjamini_hochberg'."
        )

    def adjusted_significance(
        self,
        p_values: list[float],
        alpha: float = 0.05,
        method: str = "bonferroni",
    ) -> list[bool]:
        """Return a list of significance flags after applying *method*.

        Parameters
        ----------
        p_values:
            Raw p-values.
        alpha:
            Significance level applied to adjusted p-values.
        method:
            Correction method name (see :meth:`apply`).

        Returns
        -------
        list[bool]
            ``True`` where the adjusted p-value is < *alpha*.

        Examples
        --------
        >>> mtc = MultipleTestingCorrection()
        >>> mtc.adjusted_significance([0.001, 0.5], alpha=0.05)
        [True, False]
        """
        adjusted = self.apply(p_values, method=method)
        return [p < alpha for p in adjusted]


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------


class ReportGenerator:
    """Render human-readable statistical reports from raw result dicts.

    All ``generate_*`` methods return plain-text strings that can be logged,
    written to a file, or displayed in a terminal.  No external formatting
    libraries are required.

    Parameters
    ----------
    title:
        Banner title used in multi-experiment summary reports.

    Examples
    --------
    >>> rg = ReportGenerator("My Study")
    >>> print(rg.format_p_value(0.0003))
    p < 0.001
    """

    def __init__(self, title: str = "Statistical Validation Report") -> None:
        self.title = title
        _log.debug("ReportGenerator initialised (title=%r)", title)

    def generate_t_test_report(self, result: dict[str, Any]) -> str:
        """Render a rich text report for a t-test *result* dict.

        Accepts output from :meth:`StatisticalValidator.t_test`,
        :meth:`~StatisticalValidator.one_sample_t_test`, or
        :meth:`~StatisticalValidator.welch_t_test`.

        Parameters
        ----------
        result:
            Dict with at minimum ``'statistic'``, ``'p_value'``,
            ``'df'``, ``'significant'`` keys.

        Returns
        -------
        str
            Multi-line formatted report.

        Examples
        --------
        >>> rg = ReportGenerator()
        >>> sv = StatisticalValidator()
        >>> res = sv.t_test(list(range(1, 11)), list(range(3, 13)))
        >>> "t-statistic" in rg.generate_t_test_report(res)
        True
        """
        t = result.get("statistic", float("nan"))
        p = result.get("p_value", float("nan"))
        df = result.get("df", "?")
        sig = result.get("significant", False)
        ci_lo = result.get("ci_lower")
        ci_hi = result.get("ci_upper")
        m1 = result.get("mean1")
        m2 = result.get("mean2")
        sig_str = "✓ SIGNIFICANT" if sig else "✗ not significant"
        lines = [
            "─" * 52,
            "  t-Test Result",
            "─" * 52,
        ]
        if m1 is not None and m2 is not None:
            lines.append(f"  Mean (group 1)  : {m1:>10.4f}")
            lines.append(f"  Mean (group 2)  : {m2:>10.4f}")
            lines.append(f"  Difference      : {m1 - m2:>+10.4f}")
        lines.append(f"  t-statistic     : {t:>10.4f}")
        lines.append(f"  Degrees of free.: {df}")
        lines.append(f"  p-value         : {self.format_p_value(p)}")
        if ci_lo is not None and ci_hi is not None:
            ci_str = self.format_confidence_interval((ci_lo, ci_hi), "mean diff")
            lines.append(f"  95 % CI         : {ci_str}")
        if "pooled_std" in result:
            lines.append(f"  Pooled std      : {result['pooled_std']:>10.4f}")
        lines.append(f"  Decision        : {sig_str}")
        lines.append("─" * 52)
        return "\n".join(lines)

    def generate_power_report(
        self,
        effect_size: float,
        alpha: float,
        power: float,
        n: int,
    ) -> str:
        """Render a power-analysis report.

        Parameters
        ----------
        effect_size:
            Target Cohen's d.
        alpha:
            Significance level α.
        power:
            Desired power 1 − β.
        n:
            Computed minimum per-group sample size.

        Returns
        -------
        str
            Multi-line formatted report.

        Examples
        --------
        >>> rg = ReportGenerator()
        >>> "sample size" in rg.generate_power_report(0.5, 0.05, 0.80, 64).lower()
        True
        """
        z_alpha = _beta_quantile(1.0 - alpha / 2.0)
        z_beta = _beta_quantile(power)
        lines = [
            "─" * 52,
            "  Power Analysis",
            "─" * 52,
            f"  Target effect size (Cohen d) : {effect_size:.3f}",
            f"  Significance level α         : {alpha}",
            f"  Desired power (1 − β)        : {power:.0%}",
            f"  z_{{α/2}}                       : {z_alpha:.4f}",
            f"  z_β                          : {z_beta:.4f}",
            f"  ─────────────────────────────────────────",
            f"  Required sample size (per grp): {n}",
            f"  Total sample size (2 groups)  : {2 * n}",
            "─" * 52,
        ]
        return "\n".join(lines)

    def generate_summary_report(self, results: list[dict[str, Any]]) -> str:
        """Render a multi-experiment summary report.

        Produces one row per result dict, showing a running tally of
        significant and non-significant results and an overall table.

        Parameters
        ----------
        results:
            List of t-test result dicts (from
            :meth:`StatisticalValidator.t_test` etc.).

        Returns
        -------
        str
            Multi-line formatted summary report.

        Examples
        --------
        >>> rg = ReportGenerator("Demo")
        >>> sv = StatisticalValidator()
        >>> rs = [sv.t_test([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])]
        >>> "Demo" in rg.generate_summary_report(rs)
        True
        """
        n_total = len(results)
        n_sig = sum(1 for r in results if r.get("significant", False))
        n_ns = n_total - n_sig
        header = [
            "=" * 60,
            f"  {self.title}",
            f"  {n_total} experiment(s) — {n_sig} significant, {n_ns} not significant",
            "=" * 60,
            f"  {'#':>3}  {'t':>8}  {'df':>5}  {'p-value':>12}  {'sig':>5}",
            "  " + "─" * 40,
        ]
        rows: list[str] = []
        for i, r in enumerate(results, start=1):
            t = r.get("statistic", float("nan"))
            df = r.get("df", "?")
            p = r.get("p_value", float("nan"))
            sig = "✓" if r.get("significant", False) else "✗"
            rows.append(
                f"  {i:>3}  {t:>8.4f}  {str(df):>5}  "
                f"{self.format_p_value(p):>12}  {sig:>5}"
            )
        footer = [
            "  " + "─" * 40,
            f"  Significant at α = 0.05: {n_sig}/{n_total} "
            f"({n_sig / n_total:.0%})" if n_total else "  No results.",
            "=" * 60,
        ]
        return "\n".join(header + rows + footer)

    def format_confidence_interval(
        self, ci: tuple[float, float], label: str = ""
    ) -> str:
        """Format a confidence interval as a human-readable string.

        Parameters
        ----------
        ci:
            ``(lower, upper)`` tuple.
        label:
            Optional label prepended to the interval string.

        Returns
        -------
        str
            E.g. ``"mean diff: [−0.3412, 1.7823]"`` or
            ``"[−0.3412, 1.7823]"`` if label is empty.

        Examples
        --------
        >>> ReportGenerator().format_confidence_interval((−1.5, 2.3), "diff")
        'diff: [−1.5000, 2.3000]'
        """
        lo, hi = ci
        interval = f"[{lo:+.4f}, {hi:+.4f}]"
        if label:
            return f"{label}: {interval}"
        return interval

    def format_p_value(self, p: float) -> str:
        """Format *p* in the style used by most statistical journals.

        Returns ``"p < 0.001"`` for very small values, otherwise
        ``"p = X.XXX"`` with three decimal places.

        Parameters
        ----------
        p:
            p-value ∈ [0, 1].

        Returns
        -------
        str
            Formatted p-value string.

        Examples
        --------
        >>> ReportGenerator().format_p_value(0.0002)
        'p < 0.001'
        >>> ReportGenerator().format_p_value(0.043)
        'p = 0.043'
        """
        if math.isnan(p):
            return "p = NaN"
        if p < 0.001:
            return "p < 0.001"
        if p < 0.01:
            return f"p = {p:.3f}"
        if p > 0.999:
            return "p > 0.999"
        return f"p = {p:.3f}"
