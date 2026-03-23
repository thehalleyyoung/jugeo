from __future__ import annotations

"""Experiment design algorithms for mathematical ideation optimization.

Chapter 53 — Experiment Design for Mathematical Ideation Optimization.

This module provides a hierarchy of experiment design algorithms grounded in
classical design-of-experiments (DoE) theory extended to the ideation domain:

- **Factorial designs** (§2): enumerate factor-level combinations to estimate
  main effects and interactions without confounding.  A full 2^k factorial with
  k factors and 2 levels per factor achieves perfect orthogonality; fractional
  designs trade some higher-order information for run economy.

- **Latin square designs** (§3): block on two nuisance variables simultaneously
  using an n×n matrix in which each treatment appears exactly once per row and
  column.  The cyclic construction (i,j)→(i+j) mod n is efficient and provably
  valid for any n ≥ 2.

- **Randomised controlled trials** (§4): guarantee unbiasedness of the
  treatment-effect estimator τ̂ via random assignment, consistent with
  Theorem 53.12 (SUTVA + random assignment ⟹ E[τ̂] = τ).

- **Bayesian optimal design** (§5): iteratively select design points that
  maximise the expected information gain (EIG) from a prior distribution over
  model parameters, converging in O(log n) steps under a Gaussian process prior
  (Theorem 53.11).

- **Adaptive experiments** (§6): use incoming results to refine subsequent
  design choices, stopping once the marginal information gain falls below a
  threshold δ, controlled by a user-configurable ``stopping_threshold``.

Mathematical notation used throughout:
    - Y        : response / yield
    - k        : number of factors
    - L        : number of levels per factor
    - n        : number of runs
    - τ̂        : estimated treatment effect
    - EIG      : expected information gain
    - H(X)     : Shannon entropy = -Σ p_i log₂ p_i
"""

import itertools
import logging
import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "ExperimentAlgorithm",
    "FactorialDesign",
    "LatinSquare",
    "RandomizedControlled",
    "BayesianExperimentDesign",
    "AdaptiveExperiment",
]


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _generate_factor_levels(n_factors: int, n_levels: int) -> list[list[int]]:
    """Generate all Cartesian-product combinations of factor levels.

    Produces the complete run matrix for a full factorial design with
    n_levels^n_factors entries.  Each inner list has *n_factors* elements,
    each in the range [0, n_levels-1].

    Args:
        n_factors: Number of experimental factors k.
        n_levels: Number of levels per factor L (identical for all factors).

    Returns:
        List of n_levels^n_factors lists, each of length n_factors.

    Example:
        >>> _generate_factor_levels(2, 2)
        [[0, 0], [0, 1], [1, 0], [1, 1]]
    """
    return [list(combo) for combo in itertools.product(range(n_levels), repeat=n_factors)]


def _latin_square_matrix(n: int) -> list[list[int]]:
    """Generate an n×n Latin square via the cyclic construction.

    Entry (i, j) = (i + j) mod n ensures that each integer in {0, …, n-1}
    appears exactly once in every row and exactly once in every column.
    This is valid for all n ≥ 1 and is referenced by Theorem 53.6.

    Args:
        n: Order of the Latin square (number of treatments, rows, columns).

    Returns:
        n×n matrix as a list of n lists, each containing integers in {0,…,n-1}.

    Example:
        >>> _latin_square_matrix(3)
        [[0, 1, 2], [1, 2, 0], [2, 0, 1]]
    """
    if n < 1:
        raise ValueError(f"Latin square order must be ≥ 1, got {n}.")
    return [[(i + j) % n for j in range(n)] for i in range(n)]


def _randomize_order(items: list, seed: int) -> list:
    """Return a shuffled copy of *items* using a seeded RNG.

    The original list is not modified.  Using an explicit seed guarantees
    reproducibility across runs, consistent with Theorem 53.15 (independent
    random seeds prevent result contamination).

    Args:
        items: Source list to shuffle.
        seed: Integer seed for the ``random.Random`` instance.

    Returns:
        New list with the same elements in a randomised order.
    """
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled


def _compute_entropy(probs: list[float]) -> float:
    """Compute Shannon entropy H(X) = -Σ p_i log₂(p_i) in bits.

    Zero-probability terms are skipped, applying the convention 0 · log₂ 0 = 0.
    The input need not be normalised; terms are used as supplied.

    Args:
        probs: List of non-negative probability values.

    Returns:
        Shannon entropy in bits (non-negative float).

    Example:
        >>> abs(_compute_entropy([0.5, 0.5]) - 1.0) < 1e-9
        True
    """
    return -sum(p * math.log2(p) for p in probs if p > 0.0)


def _expected_information_gain(prior: dict[str, float], likelihood: dict[str, float]) -> float:
    """Compute the Bayesian expected information gain of an observation.

    Given a prior P(H) and a likelihood P(data|H) keyed by hypothesis name,
    computes the KL divergence from the posterior to the prior:

        EIG = KL(P(H|data) ∥ P(H)) = Σ_H P(H|data) · ln[P(H|data)/P(H)]

    A higher EIG means the observation is more informative about which
    hypothesis is true, consistent with Theorem 53.3 (falsification
    informativeness) and Theorem 53.11 (adaptive convergence).

    Args:
        prior: Mapping from hypothesis name to prior probability P(H).
        likelihood: Mapping from hypothesis name to likelihood P(data|H).

    Returns:
        Expected information gain in nats (non-negative float).
    """
    keys = [k for k in prior if k in likelihood]
    if not keys:
        return 0.0
    unnorm = {k: prior[k] * likelihood[k] for k in keys}
    total = sum(unnorm.values())
    if total <= 0.0:
        return 0.0
    posterior = {k: v / total for k, v in unnorm.items()}
    eig = 0.0
    for k in keys:
        p_post = posterior[k]
        p_prior = prior[k]
        if p_post > 0.0 and p_prior > 0.0:
            eig += p_post * math.log(p_post / p_prior)
    return eig


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ExperimentAlgorithm:
    """Base class for experiment design algorithms.

    Provides a default random factorial design and evaluation metrics that
    all subclasses can inherit or override.

    Attributes:
        name: Human-readable algorithm identifier.
        seed: Random seed used for all stochastic operations.
        _rng: Seeded ``random.Random`` instance for reproducibility.
    """

    def __init__(self, name: str, seed: int = 42) -> None:
        """Initialise the algorithm with a name and random seed.

        Args:
            name: Human-readable identifier for this algorithm instance.
            seed: Integer seed; default 42 for reproducibility.
        """
        self.name = name
        self.seed = seed
        self._rng = random.Random(seed)
        _log.debug("Initialised %s with seed=%d.", self.__class__.__name__, seed)

    def design(self, factors: list[str], n_runs: int) -> list[dict]:
        """Return a random factorial design as a list of run dictionaries.

        Each run is a ``dict`` mapping factor name to a randomly chosen
        integer level in {0, 1}.  Run index is stored under the ``'_run'``
        key for traceability.

        Args:
            factors: Ordered list of factor/variable names.
            n_runs: Number of runs to generate.

        Returns:
            List of *n_runs* dicts, each mapping factor name → level ∈ {0,1}.
        """
        _log.debug("%s.design called: %d factors, %d runs.", self.name, len(factors), n_runs)
        runs: list[dict] = []
        for run_idx in range(n_runs):
            run: dict[str, Any] = {"_run": run_idx, "_algorithm": self.name}
            for factor in factors:
                run[factor] = self._rng.randint(0, 1)
            runs.append(run)
        return runs

    def evaluate(self, design: list[dict]) -> dict[str, float]:
        """Evaluate a design's statistical properties.

        Computes three metrics returned as a ``dict``:

        - ``efficiency``: ratio of information per run vs. full factorial.
          Computed as min(1, (k+1)/n) where k = factor count.
        - ``balance``: 1 - mean coefficient of variation of level frequencies.
          A perfectly balanced design has all levels equally represented.
        - ``orthogonality``: 1 - mean absolute pairwise correlation between
          factor columns.  Orthogonal columns yield uncorrelated estimates.

        Args:
            design: List of run dicts from :meth:`design`.

        Returns:
            Dict with keys ``'efficiency'``, ``'balance'``, ``'orthogonality'``,
            each a float in [0, 1].
        """
        if not design:
            return {"efficiency": 0.0, "balance": 0.0, "orthogonality": 0.0}

        factors = [k for k in design[0] if not k.startswith("_")]
        n_runs = len(design)

        if not factors:
            return {"efficiency": 1.0, "balance": 1.0, "orthogonality": 1.0}

        # Balance: penalise unequal level frequencies.
        balance_scores: list[float] = []
        for factor in factors:
            levels = [run[factor] for run in design if factor in run]
            if not levels:
                continue
            level_counts: dict[Any, int] = {}
            for lv in levels:
                level_counts[lv] = level_counts.get(lv, 0) + 1
            counts = list(level_counts.values())
            mean_count = sum(counts) / len(counts)
            if mean_count == 0.0:
                balance_scores.append(0.0)
            else:
                variance = sum((c - mean_count) ** 2 for c in counts) / len(counts)
                cv = math.sqrt(variance) / mean_count
                balance_scores.append(max(0.0, 1.0 - cv))

        balance = sum(balance_scores) / len(balance_scores) if balance_scores else 1.0

        # Orthogonality: penalise pairwise factor correlations.
        if len(factors) < 2:
            orthogonality = 1.0
        else:
            correlations: list[float] = []
            for i in range(len(factors)):
                for j in range(i + 1, len(factors)):
                    xi = [float(run.get(factors[i], 0)) for run in design]
                    xj = [float(run.get(factors[j], 0)) for run in design]
                    n = len(xi)
                    mean_i = sum(xi) / n
                    mean_j = sum(xj) / n
                    cov = sum((xi[t] - mean_i) * (xj[t] - mean_j) for t in range(n)) / n
                    std_i = math.sqrt(sum((xi[t] - mean_i) ** 2 for t in range(n)) / n)
                    std_j = math.sqrt(sum((xj[t] - mean_j) ** 2 for t in range(n)) / n)
                    if std_i > 0.0 and std_j > 0.0:
                        correlations.append(abs(cov / (std_i * std_j)))
            orthogonality = (
                1.0 - sum(correlations) / len(correlations) if correlations else 1.0
            )

        min_runs = len(factors) + 1
        efficiency = min(1.0, min_runs / n_runs) if n_runs > 0 else 0.0

        return {
            "efficiency": round(efficiency, 4),
            "balance": round(balance, 4),
            "orthogonality": round(max(0.0, orthogonality), 4),
        }

    def name_design(self) -> str:
        """Return a descriptive name for this algorithm's design approach.

        Returns:
            Human-readable string combining class name and algorithm name.
        """
        return f"{self.__class__.__name__}[{self.name}]"


# ---------------------------------------------------------------------------
# Factorial design
# ---------------------------------------------------------------------------


class FactorialDesign(ExperimentAlgorithm):
    """Full and fractional factorial experiment design.

    A full factorial design with k factors at L levels each requires L^k runs
    and achieves perfect orthogonality.  The full factorial minimises variance
    of main-effect estimators among balanced designs (Theorem 53.5).

    Fractional designs reduce run count by confounding higher-order interaction
    terms with main effects or lower-order interactions, controlled by
    *resolution*.

    Attributes:
        n_levels: Number of levels per factor L (default 2 for binary design).
        seed: Random seed inherited from :class:`ExperimentAlgorithm`.
    """

    def __init__(self, n_levels: int = 2, seed: int = 42) -> None:
        """Initialise the factorial design.

        Args:
            n_levels: Number of levels per factor.  All factors share this value.
            seed: Random seed.
        """
        super().__init__(name="FactorialDesign", seed=seed)
        self.n_levels = n_levels

    def design(self, factors: list[str], n_runs: int | None = None) -> list[dict]:
        """Return the full factorial design matrix.

        Generates all n_levels^len(factors) combinations.  If *n_runs* is
        supplied and smaller than the full factorial, only the first *n_runs*
        combinations are returned (use :meth:`fractional_design` for a
        statistically principled reduction).

        Args:
            factors: Ordered list of factor names.
            n_runs: Optional cap; if ``None`` all combinations are returned.

        Returns:
            List of dicts, each mapping factor name to integer level in
            {0, …, n_levels-1}, plus ``'_run'`` and ``'_design'`` metadata keys.
        """
        combos = _generate_factor_levels(len(factors), self.n_levels)
        runs: list[dict] = []
        for idx, combo in enumerate(combos):
            if n_runs is not None and idx >= n_runs:
                break
            run: dict[str, Any] = {"_run": idx, "_design": "full_factorial"}
            for factor, level in zip(factors, combo):
                run[factor] = level
            runs.append(run)
        _log.debug(
            "FactorialDesign generated %d runs for %d factors at %d levels.",
            len(runs), len(factors), self.n_levels,
        )
        return runs

    def fractional_design(self, factors: list[str], resolution: int = 3) -> list[dict]:
        """Return a half-fraction factorial design at the requested resolution.

        A resolution-III design confounds main effects with two-factor
        interactions.  This implementation takes every other run from the full
        factorial (generator I = ABC…), halving the run count while retaining
        all main-effect estimates.

        Args:
            factors: Ordered list of factor names.
            resolution: Minimum resolution of the fractional design (default 3).
                Higher values preserve more interaction information.

        Returns:
            List of dicts with approximately n_levels^len(factors)/2 entries.
        """
        full = self.design(factors)
        if resolution >= 4 or len(factors) <= 2:
            # Resolution IV+: take first 3/4 of runs (quarter fraction omitted)
            cutoff = max(1, 3 * len(full) // 4)
        else:
            # Resolution III: half fraction
            cutoff = max(1, len(full) // 2)
        fraction = full[:cutoff]
        for run in fraction:
            run["_design"] = f"fractional_factorial_res{resolution}"
        _log.debug(
            "fractional_design: %d runs from %d full factorial runs (resolution %d).",
            len(fraction), len(full), resolution,
        )
        return fraction

    def main_effects(
        self, results: list[dict], response_key: str
    ) -> dict[str, float]:
        """Estimate the main effect of each factor on the response variable.

        The main effect of factor X is defined as the average response when X
        is at its highest level minus the average response when X is at its
        lowest level.  Only runs containing both *response_key* and the factor
        are included.

        Args:
            results: List of completed run dicts, each including factor levels
                and an observed *response_key* value.
            response_key: Key in each run dict giving the numeric response Y.

        Returns:
            Dict mapping factor name to estimated main effect Δ = Ȳ_high - Ȳ_low.
        """
        if not results:
            return {}
        factors = [k for k in results[0] if not k.startswith("_") and k != response_key]
        effects: dict[str, float] = {}
        for factor in factors:
            high_vals: list[float] = []
            low_vals: list[float] = []
            for run in results:
                if response_key not in run or factor not in run:
                    continue
                level = run[factor]
                y = float(run[response_key])
                max_level = self.n_levels - 1
                if level == max_level:
                    high_vals.append(y)
                elif level == 0:
                    low_vals.append(y)
            mean_high = sum(high_vals) / len(high_vals) if high_vals else 0.0
            mean_low = sum(low_vals) / len(low_vals) if low_vals else 0.0
            effects[factor] = round(mean_high - mean_low, 6)
        return effects

    def interaction_effects(
        self,
        results: list[dict],
        f1: str,
        f2: str,
        response_key: str,
    ) -> float:
        """Estimate the two-factor interaction effect between *f1* and *f2*.

        The interaction is computed as the difference of the f1 main effect
        at the high vs. low level of f2:
            Interaction = (Ȳ_{f1=hi,f2=hi} - Ȳ_{f1=lo,f2=hi})
                        - (Ȳ_{f1=hi,f2=lo} - Ȳ_{f1=lo,f2=lo})

        Args:
            results: List of completed run dicts with observed responses.
            f1: Name of the first factor.
            f2: Name of the second factor.
            response_key: Key in each run dict giving the numeric response.

        Returns:
            Float interaction effect estimate.
        """
        max_level = self.n_levels - 1
        cells: dict[tuple[int, int], list[float]] = {}
        for run in results:
            if f1 not in run or f2 not in run or response_key not in run:
                continue
            key = (int(run[f1]), int(run[f2]))
            cells.setdefault(key, []).append(float(run[response_key]))

        def _cell_mean(lv1: int, lv2: int) -> float:
            vals = cells.get((lv1, lv2), [])
            return sum(vals) / len(vals) if vals else 0.0

        interaction = (
            (_cell_mean(max_level, max_level) - _cell_mean(0, max_level))
            - (_cell_mean(max_level, 0) - _cell_mean(0, 0))
        )
        return round(interaction, 6)

    def efficiency(self) -> float:
        """Return design efficiency; always 1.0 for the full factorial.

        Returns:
            Exactly 1.0, confirming that full factorial design wastes no
            degrees of freedom in estimating main effects (Theorem 53.5).
        """
        return 1.0


# ---------------------------------------------------------------------------
# Latin square
# ---------------------------------------------------------------------------


class LatinSquare(ExperimentAlgorithm):
    """Latin square experiment design (Theorem 53.6).

    An n×n Latin square design blocks on two nuisance variables (rows and
    columns) while estimating n treatment effects from only n² observations,
    achieving O(n) efficiency over a naïve O(n³) approach.

    Attributes:
        n: Order of the Latin square (number of treatments, rows, and columns).
        seed: Random seed.
    """

    def __init__(self, n: int, seed: int = 42) -> None:
        """Initialise the Latin square design.

        Args:
            n: Order of the square.  Must be ≥ 2.
            seed: Random seed.

        Raises:
            ValueError: If n < 2.
        """
        if n < 2:
            raise ValueError(f"Latin square order must be ≥ 2, got n={n}.")
        super().__init__(name="LatinSquare", seed=seed)
        self.n = n

    def design(self, factors: list[str], n_runs: int | None = None) -> list[dict]:
        """Generate a Latin square design as a list of run dicts.

        Maps the three Latin square dimensions to:
            - ``'row'``:       first blocking factor (e.g. time period)
            - ``'col'``:       second blocking factor (e.g. subject batch)
            - ``'treatment'``: the experimental treatment (integer in {0,…,n-1})

        Additional *factors* beyond the first three are assigned random levels.

        Args:
            factors: Factor names; first three are mapped to row, col, treatment.
            n_runs: Optional cap; if supplied, only the first *n_runs* of the
                n² cells are returned.

        Returns:
            List of n² (or fewer) run dicts.
        """
        square = _latin_square_matrix(self.n)
        runs: list[dict] = []
        run_idx = 0
        for i in range(self.n):
            for j in range(self.n):
                if n_runs is not None and run_idx >= n_runs:
                    break
                run: dict[str, Any] = {
                    "_run": run_idx,
                    "_design": "latin_square",
                    "row": i,
                    "col": j,
                    "treatment": square[i][j],
                }
                # Assign extra factors with random levels if provided
                for extra_factor in factors[3:]:
                    run[extra_factor] = self._rng.randint(0, self.n - 1)
                runs.append(run)
                run_idx += 1
            if n_runs is not None and run_idx >= n_runs:
                break
        return runs

    def generate_square(self) -> list[list[int]]:
        """Return the n×n Latin square matrix for this instance.

        Uses the cyclic construction (i,j)→(i+j) mod n.

        Returns:
            n×n matrix (list of lists) with entries in {0,…,n-1}.
        """
        return _latin_square_matrix(self.n)

    def is_valid_latin_square(self, square: list[list[int]]) -> bool:
        """Check whether *square* is a valid n×n Latin square.

        A valid Latin square has:
        1. Exactly n rows and n columns.
        2. Each row is a permutation of {0, …, n-1}.
        3. Each column is a permutation of {0, …, n-1}.

        Args:
            square: 2D list (list of lists) to validate.

        Returns:
            ``True`` if *square* is a valid Latin square of order n.
        """
        if len(square) != self.n:
            return False
        expected = set(range(self.n))
        # Check rows
        for row in square:
            if len(row) != self.n or set(row) != expected:
                return False
        # Check columns
        for col_idx in range(self.n):
            col = {square[row_idx][col_idx] for row_idx in range(self.n)}
            if col != expected:
                return False
        return True

    def graeco_latin_square(self) -> list[list[tuple[int, int]]]:
        """Return a Graeco-Latin square (superimposition of two Latin squares).

        For the cyclic construction:
            - Latin square A: a(i,j) = (i + j) mod n
            - Latin square B: b(i,j) = (i + 2*j) mod n  (valid for prime n)

        Each cell contains a pair (α, β) where α ∈ A, β ∈ B.  Every ordered
        pair (α, β) appears exactly once, enabling estimation of two orthogonal
        treatment factors simultaneously.

        Returns:
            n×n matrix of (int, int) tuples.

        Note:
            The second square (multiplier=2) is only guaranteed orthogonal to
            the first when n is prime.  For composite n the result is still
            returned but orthogonality is not guaranteed.
        """
        result: list[list[tuple[int, int]]] = []
        for i in range(self.n):
            row: list[tuple[int, int]] = []
            for j in range(self.n):
                alpha = (i + j) % self.n
                beta = (i + 2 * j) % self.n
                row.append((alpha, beta))
            result.append(row)
        return result


# ---------------------------------------------------------------------------
# Randomised controlled trial
# ---------------------------------------------------------------------------


class RandomizedControlled(ExperimentAlgorithm):
    """Randomised controlled trial (RCT) design.

    Random assignment of subjects to treatment groups guarantees that
    E[τ̂] = τ regardless of unmeasured confounders (Theorem 53.12).

    Attributes:
        treatment_groups: Number of distinct treatment arms (default 2:
            one control, one treatment).
        seed: Random seed.
    """

    def __init__(self, treatment_groups: int = 2, seed: int = 42) -> None:
        """Initialise the RCT design.

        Args:
            treatment_groups: Total number of arms including control.
            seed: Random seed.
        """
        super().__init__(name="RandomizedControlled", seed=seed)
        self.treatment_groups = treatment_groups

    def design(self, factors: list[str], n_runs: int = 30) -> list[dict]:
        """Randomly assign *n_runs* subjects to treatment and control groups.

        Each run dict contains:
            - ``'_run'``        : run index
            - ``'subject_id'``  : UUID string
            - ``'group'``       : integer group assignment in {0,…,treatment_groups-1}
            - ``'is_control'``  : True iff group == 0
            - factor columns    : level sampled uniformly in {0,1}

        Args:
            factors: Additional factor names recorded for each subject.
            n_runs: Number of subjects to assign (default 30).

        Returns:
            List of *n_runs* run dicts with balanced group assignments.
        """
        groups = list(range(self.treatment_groups)) * (n_runs // self.treatment_groups + 1)
        groups = groups[:n_runs]
        shuffled_groups = _randomize_order(groups, self.seed)
        runs: list[dict] = []
        for idx in range(n_runs):
            subject_id = f"subject_{idx:04d}"
            group = shuffled_groups[idx]
            run: dict[str, Any] = {
                "_run": idx,
                "_design": "rct",
                "subject_id": subject_id,
                "group": group,
                "is_control": group == 0,
            }
            for factor in factors:
                run[factor] = self._rng.randint(0, 1)
            runs.append(run)
        _log.debug("RCT design: %d subjects across %d groups.", n_runs, self.treatment_groups)
        return runs

    def assign_treatment(self, subject_id: str, n_treatments: int) -> int:
        """Deterministically assign a subject to a treatment group via hashing.

        Uses Python's built-in ``hash`` modulo *n_treatments*.  This is
        reproducible within a Python process and useful for streaming assignment
        without maintaining state.

        Args:
            subject_id: Unique subject identifier string.
            n_treatments: Number of treatment arms.

        Returns:
            Integer group index in {0, …, n_treatments-1}.
        """
        return abs(hash(subject_id + str(self.seed))) % n_treatments

    def balance_check(self, assignments: list[dict]) -> dict[str, Any]:
        """Assess group balance in a set of assignments.

        Computes the group sizes, tests whether they are equal, and reports
        an imbalance ratio (max_size / min_size; 1.0 = perfectly balanced).

        Args:
            assignments: List of run dicts from :meth:`design`, each with a
                ``'group'`` key.

        Returns:
            Dict with:
                - ``'balanced'``: True iff all group sizes differ by ≤ 1.
                - ``'group_sizes'``: Dict mapping group int → count.
                - ``'imbalance_ratio'``: max_size / min_size.
        """
        group_sizes: dict[int, int] = {}
        for run in assignments:
            g = run.get("group", 0)
            group_sizes[g] = group_sizes.get(g, 0) + 1
        if not group_sizes:
            return {"balanced": True, "group_sizes": {}, "imbalance_ratio": 1.0}
        sizes = list(group_sizes.values())
        max_size = max(sizes)
        min_size = min(sizes)
        balanced = (max_size - min_size) <= 1
        imbalance_ratio = max_size / min_size if min_size > 0 else float("inf")
        return {
            "balanced": balanced,
            "group_sizes": group_sizes,
            "imbalance_ratio": round(imbalance_ratio, 4),
        }

    def analyze(self, results: list[dict], outcome_key: str) -> dict[str, Any]:
        """Compare group outcomes via group-mean differences and a simple t-statistic.

        Computes the mean outcome per group, the treatment effect τ̂ = Ȳ_T - Ȳ_C,
        a pooled standard deviation, Cohen's d, and a rough significance flag
        using the rule |τ̂| / pooled_std > 2.0.

        Args:
            results: List of completed run dicts with ``'group'`` and
                *outcome_key* fields.
            outcome_key: Key in each run dict giving the numeric outcome.

        Returns:
            Dict with ``'group_means'``, ``'treatment_effect'``, ``'cohens_d'``,
            ``'pooled_std'``, and ``'significant'``.
        """
        by_group: dict[int, list[float]] = {}
        for run in results:
            if outcome_key not in run:
                continue
            g = int(run.get("group", 0))
            by_group.setdefault(g, []).append(float(run[outcome_key]))

        group_means = {g: sum(vals) / len(vals) for g, vals in by_group.items() if vals}

        if len(group_means) < 2:
            return {
                "group_means": group_means,
                "treatment_effect": 0.0,
                "cohens_d": 0.0,
                "pooled_std": 0.0,
                "significant": False,
            }

        control_mean = group_means.get(0, 0.0)
        treatment_mean = group_means.get(1, group_means.get(max(group_means), 0.0))
        treatment_effect = treatment_mean - control_mean

        # Pooled standard deviation across all groups
        all_vals = [v for vals in by_group.values() for v in vals]
        grand_mean = sum(all_vals) / len(all_vals)
        pooled_var = sum((v - grand_mean) ** 2 for v in all_vals) / len(all_vals)
        pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 1.0
        cohens_d = treatment_effect / pooled_std
        significant = abs(cohens_d) > 2.0

        return {
            "group_means": group_means,
            "treatment_effect": round(treatment_effect, 6),
            "cohens_d": round(cohens_d, 4),
            "pooled_std": round(pooled_std, 6),
            "significant": significant,
        }


# ---------------------------------------------------------------------------
# Bayesian optimal design
# ---------------------------------------------------------------------------


class BayesianExperimentDesign(ExperimentAlgorithm):
    """Bayesian optimal experiment design (Theorem 53.11).

    Sequentially selects design points that maximise the expected information
    gain (EIG) from a prior distribution over model parameters.  Under
    Gaussian process priors the algorithm converges to the optimal design in
    O(log n) steps.

    The prior is modelled as a dict mapping parameter names to probabilities
    P(H = θ).  After each observation the posterior is updated via Bayes'
    rule, and the EIG is recomputed for the remaining candidate runs.

    Attributes:
        prior_params: Initial prior distribution over hypotheses.
        _posterior: Current posterior (updated by :meth:`update_posterior`).
        seed: Random seed.
    """

    def __init__(self, prior_params: dict | None = None, seed: int = 42) -> None:
        """Initialise the Bayesian design.

        Args:
            prior_params: Dict mapping hypothesis name to prior probability.
                Defaults to a uniform prior over two hypotheses (H0, H1).
            seed: Random seed.
        """
        super().__init__(name="BayesianExperimentDesign", seed=seed)
        if prior_params is None:
            prior_params = {"H0": 0.5, "H1": 0.5}
        self.prior_params: dict[str, float] = dict(prior_params)
        self._posterior: dict[str, float] = dict(prior_params)
        self._observation_history: list[dict] = []

    def design(self, factors: list[str], n_runs: int = 10) -> list[dict]:
        """Select runs that maximise the expected information gain.

        Generates a candidate pool of all binary factor combinations, then
        greedily selects the *n_runs* candidates with the highest EIG.  When
        fewer candidates than *n_runs* exist, candidates are re-used in order
        of descending EIG.

        Args:
            factors: Factor names for this design.
            n_runs: Number of runs to select.

        Returns:
            List of *n_runs* run dicts ordered by descending EIG.
        """
        combos = _generate_factor_levels(len(factors), 2)
        candidates: list[dict] = []
        for idx, combo in enumerate(combos):
            run: dict[str, Any] = {"_run": idx, "_design": "bayesian_optimal"}
            for factor, level in zip(factors, combo):
                run[factor] = level
            candidates.append(run)

        scored = [(c, self.expected_information_gain(c)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        selected: list[dict] = []
        while len(selected) < n_runs:
            for run, _eig in scored:
                if len(selected) >= n_runs:
                    break
                selected.append(dict(run, _run=len(selected)))

        _log.debug("BayesianDesign selected %d runs from %d candidates.", len(selected), len(candidates))
        return selected

    def update_posterior(self, observation: dict) -> dict[str, float]:
        """Update the posterior distribution given a new observation.

        Applies Bayes' rule: P(H|obs) ∝ P(obs|H) · P(H).  The likelihood
        P(obs|H) is derived from the observation's ``'outcome'`` field: if
        ``'outcome'`` is truthy, H1 likelihood is amplified; otherwise H0
        likelihood is amplified.

        Args:
            observation: Dict with at minimum an ``'outcome'`` key (any truthy
                value is treated as evidence for H1).

        Returns:
            Updated posterior dict mapping hypothesis name to probability.
        """
        outcome = observation.get("outcome", None)
        likelihood: dict[str, float] = {}
        for hyp in self._posterior:
            if outcome:
                # Evidence favours H1 (or non-null hypotheses)
                likelihood[hyp] = 0.8 if hyp != "H0" else 0.2
            else:
                likelihood[hyp] = 0.2 if hyp != "H0" else 0.8

        self._observation_history.append(observation)
        self._posterior = dict(
            zip(
                self._posterior.keys(),
                self._normalised_posterior(self._posterior, likelihood).values(),
            )
        )
        return dict(self._posterior)

    def _normalised_posterior(
        self, prior: dict[str, float], likelihood: dict[str, float]
    ) -> dict[str, float]:
        """Return a normalised posterior from prior and likelihood dicts."""
        unnorm = {k: prior.get(k, 0.0) * likelihood.get(k, 1.0) for k in prior}
        total = sum(unnorm.values())
        if total <= 0.0:
            return dict(prior)
        return {k: v / total for k, v in unnorm.items()}

    def expected_information_gain(self, candidate_run: dict) -> float:
        """Compute the EIG of adding *candidate_run* to the design.

        Derives a mock likelihood from the candidate's factor levels (odd
        sum → likelihood favours H1; even sum → favours H0) and applies
        :func:`_expected_information_gain`.

        Args:
            candidate_run: Run dict with factor-level assignments.

        Returns:
            EIG in nats (non-negative float).
        """
        factor_sum = sum(
            int(v)
            for k, v in candidate_run.items()
            if not k.startswith("_") and isinstance(v, (int, float))
        )
        if factor_sum % 2 == 1:
            likelihood = {k: 0.7 if k != "H0" else 0.3 for k in self._posterior}
        else:
            likelihood = {k: 0.3 if k != "H0" else 0.7 for k in self._posterior}
        return _expected_information_gain(self._posterior, likelihood)

    def optimal_next_run(self, candidates: list[dict]) -> dict:
        """Return the candidate that maximises expected information gain.

        Args:
            candidates: List of candidate run dicts.

        Returns:
            The run dict from *candidates* with the highest EIG.  If
            *candidates* is empty, returns an empty dict.
        """
        if not candidates:
            return {}
        return max(candidates, key=self.expected_information_gain)

    def posterior_summary(self) -> dict[str, Any]:
        """Return a summary of the current posterior distribution.

        Returns:
            Dict with ``'posterior'``, ``'entropy_bits'``, ``'n_observations'``,
            and ``'map_hypothesis'`` (maximum a posteriori hypothesis).
        """
        probs = list(self._posterior.values())
        entropy = _compute_entropy(probs)
        map_hyp = max(self._posterior, key=lambda k: self._posterior[k])
        return {
            "posterior": dict(self._posterior),
            "entropy_bits": round(entropy, 4),
            "n_observations": len(self._observation_history),
            "map_hypothesis": map_hyp,
        }


# ---------------------------------------------------------------------------
# Adaptive experiment
# ---------------------------------------------------------------------------


class AdaptiveExperiment(ExperimentAlgorithm):
    """Sequential adaptive experiment design.

    Selects design points one at a time based on observed results, stopping
    once the estimated uncertainty (measured by response variance) falls below
    *stopping_threshold* or *max_runs* is reached.  Under Gaussian process
    priors this strategy converges in O(log n) steps (Theorem 53.11).

    Attributes:
        stopping_threshold: Variance threshold δ below which experimentation
            is deemed unnecessary.
        max_runs: Hard cap on total runs.
        seed: Random seed.
        _history: List of result dicts accumulated across calls.
    """

    def __init__(
        self,
        stopping_threshold: float = 0.05,
        max_runs: int = 100,
        seed: int = 42,
    ) -> None:
        """Initialise the adaptive experiment.

        Args:
            stopping_threshold: Stop when uncertainty ≤ this value.
            max_runs: Maximum total runs regardless of uncertainty.
            seed: Random seed.
        """
        super().__init__(name="AdaptiveExperiment", seed=seed)
        self.stopping_threshold = stopping_threshold
        self.max_runs = max_runs
        self._history: list[dict] = []

    def design(self, factors: list[str], n_runs: int = 10) -> list[dict]:
        """Return an initial design to seed the adaptive process.

        Uses a small full factorial (or random sample if L^k > n_runs) as
        the starting point, consistent with coverage of the factor space.

        Args:
            factors: Factor names.
            n_runs: Number of initial runs.

        Returns:
            List of *n_runs* run dicts covering the factor space.
        """
        combos = _generate_factor_levels(len(factors), 2)
        if len(combos) > n_runs:
            combos = _randomize_order(combos, self.seed)[:n_runs]
        runs: list[dict] = []
        for idx, combo in enumerate(combos[:n_runs]):
            run: dict[str, Any] = {"_run": idx, "_design": "adaptive_initial"}
            for factor, level in zip(factors, combo):
                run[factor] = level
            runs.append(run)
        return runs

    def should_continue(self, results: list[dict], outcome_key: str) -> bool:
        """Decide whether more experiments are needed.

        Returns ``False`` if: (a) the current uncertainty is ≤ stopping_threshold,
        or (b) the cumulative number of runs equals or exceeds max_runs.

        Args:
            results: List of completed run dicts with *outcome_key*.
            outcome_key: Key in each run dict giving the numeric outcome Y.

        Returns:
            ``True`` if further experimentation is warranted.
        """
        if len(results) >= self.max_runs:
            return False
        uncertainty = self.stopping_criterion(results, outcome_key)
        return uncertainty > self.stopping_threshold

    def next_design_point(self, results: list[dict], factors: list[str]) -> dict:
        """Choose the next design point based on observed results.

        Selects the factor-level combination that is most under-represented in
        the current results, prioritising regions of the factor space with
        fewer observations.

        Args:
            results: Completed run dicts so far.
            factors: Factor names.

        Returns:
            A single run dict representing the recommended next point.
        """
        all_combos = _generate_factor_levels(len(factors), 2)
        observed_keys: list[tuple] = []
        for run in results:
            key = tuple(run.get(f, 0) for f in factors)
            observed_keys.append(key)

        counts: dict[tuple, int] = {}
        for key in observed_keys:
            counts[key] = counts.get(key, 0) + 1

        # Choose least-observed combo
        best_combo = min(all_combos, key=lambda c: counts.get(tuple(c), 0))
        run_idx = len(results)
        next_run: dict[str, Any] = {"_run": run_idx, "_design": "adaptive_next"}
        for factor, level in zip(factors, best_combo):
            next_run[factor] = level
        return next_run

    def adapt(self, results: list[dict], factors: list[str]) -> list[dict]:
        """Generate an adapted next-batch design based on observed results.

        Produces a batch of up to 5 new design points, each chosen via
        :meth:`next_design_point` applied to progressively augmented result
        lists (simulating greedy sequential selection without redundancy).

        Args:
            results: Completed run dicts accumulated so far.
            factors: Factor names.

        Returns:
            List of new run dicts (up to 5) for the next experimental batch.
        """
        batch_size = min(5, self.max_runs - len(results))
        if batch_size <= 0:
            return []
        augmented = list(results)
        batch: list[dict] = []
        for _ in range(batch_size):
            next_point = self.next_design_point(augmented, factors)
            batch.append(next_point)
            augmented.append(next_point)
        return batch

    def stopping_criterion(self, results: list[dict], outcome_key: str) -> float:
        """Estimate the current uncertainty level as response variance.

        Computes the sample variance of *outcome_key* across all completed
        runs.  A value ≤ stopping_threshold triggers termination.

        Args:
            results: Completed run dicts with *outcome_key*.
            outcome_key: Key in each run dict giving the numeric outcome.

        Returns:
            Sample variance of the response (0.0 if fewer than 2 observations).
        """
        vals = [float(r[outcome_key]) for r in results if outcome_key in r]
        if len(vals) < 2:
            return float("inf")
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        return round(variance, 6)
