from __future__ import annotations

"""Theorems of experiment design for mathematical ideation optimization.

Chapter 53 — Experiment Design for Mathematical Ideation Optimization.

This module provides a machine-readable catalogue of the theorems that
underpin the JuGeo experiment design framework.  Each theorem is expressed as a
:class:`Theorem` frozen dataclass carrying its formal statement, a proof sketch,
the conditions under which it applies, and the implications for experimental
practice.

The module-level constant :data:`THEOREM_CATALOG` is a fully populated
:class:`TheoremCatalog` instance containing all 15 theorems defined in Chapter
53.  Client code can query the catalog by tag, chapter, or keyword, and can
verify which theorems apply to a given experimental context via
:class:`TheoremVerifier`.

Mathematical notation used throughout:
    - Y          : response / yield
    - ε          : small positive threshold
    - θ          : model parameter vector
    - θ̂          : estimated parameter vector
    - δ          : effect size or precision margin
    - α          : significance level
    - β          : type-II error probability (power = 1-β)
    - n          : sample size
    - k          : number of factors / components
    - L          : number of levels per factor
    - τ          : true treatment effect
    - τ̂          : estimated treatment effect
    - EIG        : expected information gain
    - KL(P∥Q)   : Kullback-Leibler divergence from Q to P
    - FWER       : family-wise error rate
    - z_{α/2}    : (1-α/2)-quantile of the standard normal distribution
    - SUTVA      : stable unit treatment value assumption

Usage example::

    from jugeo.ideation.experiment_design.theorems import THEOREM_CATALOG, TheoremVerifier

    context = {
        "additivity of yield components": True,
        "ε > 0 threshold set": True,
        "baseline yield Y(full) > 0": True,
    }
    verifier = TheoremVerifier(THEOREM_CATALOG)
    result = verifier.verify("theorem_53_1", context)
    # result == {'applicable': True, 'unmet_conditions': [], 'confidence': 1.0}
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "Theorem",
    "TheoremCatalog",
    "TheoremVerifier",
    "THEOREM_CATALOG",
]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _format_theorem(theorem: Theorem) -> str:
    """Return a multi-line formatted string representation of a theorem.

    The output follows the conventional mathematical paper layout:
    theorem ID and name on the first line, conditions as a numbered list,
    statement indented under "Statement:", proof sketch under "Proof:", and
    implications under "Implications:".

    Args:
        theorem: :class:`Theorem` to format.

    Returns:
        Multi-line string suitable for display in a terminal or documentation.
    """
    lines: list[str] = [
        f"{'─' * 72}",
        f"Theorem {theorem.theorem_id} — {theorem.name}",
        f"Chapter: {theorem.chapter}  |  Tags: {', '.join(theorem.tags) or 'none'}",
        f"{'─' * 72}",
        "",
        "Conditions:",
    ]
    for i, cond in enumerate(theorem.conditions, start=1):
        lines.append(f"  {i}. {cond}")
    lines += [
        "",
        "Statement:",
        f"  {theorem.statement}",
        "",
        "Proof sketch:",
        f"  {theorem.proof_sketch}",
        "",
        "Implications:",
    ]
    for i, impl in enumerate(theorem.implications, start=1):
        lines.append(f"  {i}. {impl}")
    lines.append("")
    return "\n".join(lines)


def _check_conditions_met(theorem: Theorem, context: dict) -> list[str]:
    """Return the list of theorem conditions that are unmet in *context*.

    A condition is considered met if it appears as a key in *context* with a
    truthy value, or if a case-folded substring of the condition appears as a
    truthy key in *context*.

    Args:
        theorem: :class:`Theorem` whose conditions are to be checked.
        context: Dict mapping condition descriptions (or proxies) to bool-like
            values.  Keys are normalised to lower-case for matching.

    Returns:
        List of condition strings from ``theorem.conditions`` that could not be
        matched to a truthy entry in *context*.
    """
    unmet: list[str] = []
    lowered_context = {k.lower(): bool(v) for k, v in context.items()}
    for condition in theorem.conditions:
        cond_lower = condition.lower()
        # Exact match first
        if lowered_context.get(cond_lower, False):
            continue
        # Substring match: any context key that contains the condition
        matched = any(
            cond_lower in ctx_key or ctx_key in cond_lower
            for ctx_key, ctx_val in lowered_context.items()
            if ctx_val
        )
        if not matched:
            unmet.append(condition)
    return unmet


def _theorem_key(name: str) -> str:
    """Normalise a theorem name or number into a canonical ``theorem_53_N`` key.

    Strips non-alphanumeric characters and lower-cases the result.  If the
    input already matches the canonical format it is returned unchanged.

    Args:
        name: Raw name string such as ``"53.1"``, ``"Theorem 53.1"``, or
            ``"theorem_53_1"``.

    Returns:
        Canonical key string in the form ``"theorem_53_N"`` or the lowered
        slug if the canonical form cannot be inferred.

    Example:
        >>> _theorem_key("53.1")
        'theorem_53_1'
        >>> _theorem_key("Theorem 53.12")
        'theorem_53_12'
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # If it looks like "53_1" or "53_12" prefix with "theorem_"
    if re.fullmatch(r"\d+_\d+", slug):
        return f"theorem_{slug}"
    # If it looks like "theorem_53_1" already, keep as-is
    if re.fullmatch(r"theorem_\d+_\d+", slug):
        return slug
    return slug


# ---------------------------------------------------------------------------
# Theorem dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Theorem:
    """Frozen dataclass encoding one theorem from Chapter 53.

    Attributes:
        theorem_id: Canonical identifier, e.g. ``"theorem_53_1"``.
        name: Short descriptive name used in indexes and summaries.
        statement: Full formal statement of the theorem, possibly including
            mathematical notation.
        proof_sketch: Abbreviated proof argument or proof strategy.
        conditions: Tuple of pre-condition strings that must hold for the
            theorem to be applicable.
        implications: Tuple of practical consequences for experiment design.
        chapter: Chapter label (default ``"53"``).
        tags: Tuple of free-form tags for filtering (e.g. ``("ablation",)``).
    """

    theorem_id: str
    name: str
    statement: str
    proof_sketch: str
    conditions: tuple[str, ...]
    implications: tuple[str, ...]
    chapter: str = "53"
    tags: tuple[str, ...] = ()

    def applies_to(self, context: dict) -> bool:
        """Return True if all conditions could be met from *context*.

        Delegates to :func:`_check_conditions_met` and returns ``True`` iff
        the list of unmet conditions is empty.

        Args:
            context: Dict mapping condition descriptions to truthy/falsy values.

        Returns:
            ``True`` if every condition in ``self.conditions`` is satisfied.
        """
        return len(_check_conditions_met(self, context)) == 0

    def summary(self) -> str:
        """Return a one-line summary combining the theorem_id and name.

        Returns:
            String of the form ``"theorem_53_1: Ablation Identifies Necessary Components"``.
        """
        return f"{self.theorem_id}: {self.name}"

    def full_text(self) -> str:
        """Return the complete theorem text as a formatted multi-line string.

        Combines theorem_id, name, statement, and proof_sketch in the canonical
        layout produced by :func:`_format_theorem`.

        Returns:
            Multi-line formatted string.
        """
        return _format_theorem(self)


# ---------------------------------------------------------------------------
# TheoremCatalog
# ---------------------------------------------------------------------------


class TheoremCatalog:
    """Ordered collection of :class:`Theorem` objects for Chapter 53.

    Provides add/get/list/search operations.  Theorems are stored in
    insertion order and indexed by theorem_id for O(1) lookup.

    Attributes:
        _theorems: Dict mapping theorem_id to :class:`Theorem`.
        _insertion_order: List tracking insertion sequence.
    """

    def __init__(self) -> None:
        """Initialise an empty catalog."""
        self._theorems: dict[str, Theorem] = {}
        self._insertion_order: list[str] = []
        _log.debug("TheoremCatalog initialised.")

    def add(self, theorem: Theorem) -> None:
        """Add *theorem* to the catalog.

        If a theorem with the same ``theorem_id`` already exists it is
        silently replaced.

        Args:
            theorem: :class:`Theorem` to register.
        """
        if theorem.theorem_id not in self._theorems:
            self._insertion_order.append(theorem.theorem_id)
        self._theorems[theorem.theorem_id] = theorem
        _log.debug("Added theorem %r to catalog.", theorem.theorem_id)

    def get(self, theorem_id: str) -> Theorem | None:
        """Retrieve a theorem by its canonical ID.

        Normalises the lookup key via :func:`_theorem_key` so that
        ``get("53.1")``, ``get("theorem_53_1")``, and ``get("Theorem 53.1")``
        all resolve to the same entry.

        Args:
            theorem_id: Raw or canonical theorem identifier.

        Returns:
            Matching :class:`Theorem` or ``None`` if not found.
        """
        canonical = _theorem_key(theorem_id)
        return self._theorems.get(canonical) or self._theorems.get(theorem_id)

    def list_all(self) -> list[Theorem]:
        """Return all theorems in insertion order.

        Returns:
            List of :class:`Theorem` objects.
        """
        return [self._theorems[tid] for tid in self._insertion_order]

    def by_tag(self, tag: str) -> list[Theorem]:
        """Return theorems whose tags include *tag* (exact match, case-sensitive).

        Args:
            tag: Tag string to search for.

        Returns:
            List of matching :class:`Theorem` objects in insertion order.
        """
        return [t for t in self.list_all() if tag in t.tags]

    def by_chapter(self, chapter: str) -> list[Theorem]:
        """Return theorems from the specified chapter.

        Args:
            chapter: Chapter label to filter by (e.g. ``"53"``).

        Returns:
            List of :class:`Theorem` objects in insertion order.
        """
        return [t for t in self.list_all() if t.chapter == chapter]

    def search(self, keyword: str) -> list[Theorem]:
        """Return theorems whose name or statement contains *keyword*.

        The search is case-insensitive substring matching over both the
        ``name`` and ``statement`` fields.

        Args:
            keyword: Substring to search for.

        Returns:
            List of :class:`Theorem` objects in insertion order.
        """
        kw_lower = keyword.lower()
        return [
            t for t in self.list_all()
            if kw_lower in t.name.lower() or kw_lower in t.statement.lower()
        ]


# ---------------------------------------------------------------------------
# TheoremVerifier
# ---------------------------------------------------------------------------


class TheoremVerifier:
    """Verifies which theorems apply to a given experimental context.

    Wraps a :class:`TheoremCatalog` and provides context-aware applicability
    checking, including a confidence score and consistency checking.

    Attributes:
        catalog: The :class:`TheoremCatalog` to verify against.
    """

    def __init__(self, catalog: TheoremCatalog) -> None:
        """Initialise the verifier with a populated catalog.

        Args:
            catalog: :class:`TheoremCatalog` containing theorems to verify.
        """
        self.catalog = catalog

    def verify(self, theorem_id: str, context: dict) -> dict[str, Any]:
        """Check whether theorem *theorem_id* applies to *context*.

        Computes which conditions are unmet and derives a confidence score as
        the fraction of conditions that are met.

        Args:
            theorem_id: Canonical or raw theorem identifier.
            context: Dict mapping condition descriptions to truthy/falsy values.

        Returns:
            Dict with:
                - ``'applicable'``: ``True`` iff all conditions are met.
                - ``'unmet_conditions'``: List of unmet condition strings.
                - ``'confidence'``: Float in [0, 1]; 1.0 means fully applicable.
                - ``'theorem_found'``: ``True`` iff the theorem_id was found.
        """
        theorem = self.catalog.get(theorem_id)
        if theorem is None:
            return {
                "applicable": False,
                "unmet_conditions": [f"Theorem {theorem_id!r} not found in catalog."],
                "confidence": 0.0,
                "theorem_found": False,
            }
        unmet = _check_conditions_met(theorem, context)
        total = len(theorem.conditions)
        met = total - len(unmet)
        confidence = met / total if total > 0 else 1.0
        return {
            "applicable": len(unmet) == 0,
            "unmet_conditions": unmet,
            "confidence": round(confidence, 4),
            "theorem_found": True,
        }

    def find_applicable(self, context: dict) -> list[Theorem]:
        """Return all theorems in the catalog that apply to *context*.

        Args:
            context: Dict mapping condition descriptions to truthy/falsy values.

        Returns:
            List of fully applicable :class:`Theorem` objects.
        """
        return [t for t in self.catalog.list_all() if t.applies_to(context)]

    def check_consistency(self, theorem_ids: list[str]) -> bool:
        """Check whether a set of theorems are mutually consistent.

        Two theorems are considered inconsistent if one of the implications of
        the first directly contradicts a condition of the second (detected by
        checking for the word "not" or "never" in implications vs. conditions).
        In the absence of formal contradiction detection, this implementation
        returns ``True`` (consistent) unless any implication of one theorem
        exactly negates a condition of another.

        Args:
            theorem_ids: List of theorem IDs to check pairwise.

        Returns:
            ``True`` if no detected contradictions exist, ``False`` otherwise.
        """
        theorems = [self.catalog.get(tid) for tid in theorem_ids]
        theorems_found = [t for t in theorems if t is not None]
        for i, t1 in enumerate(theorems_found):
            for t2 in theorems_found[i + 1:]:
                # Check if any implication of t1 negates a condition of t2
                for impl in t1.implications:
                    impl_lower = impl.lower()
                    for cond in t2.conditions:
                        cond_lower = cond.lower()
                        # Simple heuristic: if implication says "not X" and condition says "X"
                        if f"not {cond_lower}" in impl_lower or f"never {cond_lower}" in impl_lower:
                            _log.warning(
                                "Potential inconsistency between %r and %r.",
                                t1.theorem_id, t2.theorem_id,
                            )
                            return False
        return True


# ---------------------------------------------------------------------------
# Theorem definitions
# ---------------------------------------------------------------------------

THEOREM_CATALOG = TheoremCatalog()

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_1",
    name="Ablation Identifies Necessary Components",
    statement=(
        "Under the additivity assumption ΔY = Σ_i ΔY_i, component ablation "
        "correctly identifies necessary components as those with ΔY_i > ε."
    ),
    proof_sketch=(
        "By the additivity assumption each component i contributes independently "
        "to the yield delta ΔY.  Removing component i reduces Y by exactly ΔY_i.  "
        "Hence ΔY_i > ε ⟺ Y(full) - Y(full \\ {i}) > ε, which is directly "
        "observable.  The threshold ε > 0 prevents noise-driven false positives.  "
        "Under additivity, sequential and simultaneous ablation produce the same "
        "ranking (Theorem 53.10)."
    ),
    conditions=(
        "additivity of yield components",
        "ε > 0 threshold set",
        "baseline yield Y(full) > 0",
    ),
    implications=(
        "ablation study provides valid component importance ranking",
        "critical components can be identified with threshold ε",
        "components with ΔY_i ≤ ε may be safely removed without significant yield loss",
    ),
    tags=("ablation", "component-analysis"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_2",
    name="Calibration Consistency",
    statement=(
        "If the model is identifiable and calibration data is i.i.d. with "
        "n → ∞, then θ̂_n → θ_true in probability."
    ),
    proof_sketch=(
        "Identifiability ensures the mapping θ ↦ P_θ is injective, so distinct "
        "parameters produce distinct data distributions.  By the law of large "
        "numbers the empirical calibration loss converges to its expectation "
        "uniformly in θ.  The unique minimiser of the expected loss is θ_true.  "
        "Hence the argmin θ̂_n → θ_true in probability by the argmax continuous "
        "mapping theorem applied to the negative log-likelihood."
    ),
    conditions=(
        "model is identifiable",
        "i.i.d. calibration data",
        "sufficient sample size n",
    ),
    implications=(
        "estimated parameters converge to true values as n → ∞",
        "calibration error → 0 as n → ∞",
        "larger calibration datasets monotonically improve parameter estimates",
    ),
    tags=("calibration", "consistency"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_3",
    name="Falsification Informativeness",
    statement=(
        "A single falsifying observation provides strictly more information about "
        "hypothesis truth than k confirming observations under Bayesian updating: "
        "I(falsify) > k·I(confirm) for large k."
    ),
    proof_sketch=(
        "Under a Bayesian prior p₀ = P(H = true), one falsifying observation with "
        "likelihood ratio L_false << 1 maps p₀ to a posterior approaching 0 "
        "regardless of p₀.  By contrast each confirming observation with likelihood "
        "ratio L_confirm > 1 increments log-odds by a fixed amount.  The total "
        "information from k confirmations grows as O(k) in log-odds, whereas one "
        "falsification delivers O(1/p₀) information (divergent as p₀ → 0).  Hence "
        "for sufficiently small p₀ or large k the inequality holds."
    ),
    conditions=(
        "Bayesian prior on hypothesis truth",
        "observations are independent",
    ),
    implications=(
        "experiment designs should prioritise falsifiable predictions",
        "one failed replication is more informative than many successful ones",
        "falsification-first design reduces total experiment count",
    ),
    tags=("falsification", "Bayesian", "information"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_4",
    name="Statistical Power Determines Minimum Sample Size",
    statement=(
        "For a two-sample t-test with effect size δ, significance α, and desired "
        "power 1-β, the minimum sample size satisfies "
        "n ≥ (z_{α/2} + z_β)² · 2σ²/δ²."
    ),
    proof_sketch=(
        "The non-central t-distribution under H₁ has non-centrality parameter "
        "λ = δ√(n/2)/σ.  Power 1-β requires P(|T| > t_{α/2} | λ) ≥ 1-β.  "
        "Approximating t_{α/2} ≈ z_{α/2} and inverting yields n ≥ "
        "(z_{α/2} + z_β)² · 2σ²/δ².  The factor of 2 accounts for pooling "
        "variance across two equal-sized groups."
    ),
    conditions=(
        "normally distributed outcomes",
        "known variance σ²",
        "effect size δ > 0",
    ),
    implications=(
        "under-powered studies risk false negatives for real effects",
        "doubling precision (halving δ) quadruples the required n",
        "variance reduction techniques (blocking, Latin squares) reduce required n",
    ),
    tags=("power", "sample-size", "t-test"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_5",
    name="Factorial Design Achieves Minimal Variance",
    statement=(
        "Among balanced designs with n_levels levels per factor, the full factorial "
        "design minimises the variance of main effect estimators."
    ),
    proof_sketch=(
        "For an additive effects model Y = μ + Σ αᵢ + ε the variance of the "
        "least-squares main effect estimator α̂ᵢ is σ²/(n_i^+) where n_i^+ is "
        "the number of runs at level i.  A balanced design distributes runs "
        "equally, maximising n_i^+ subject to the total run constraint.  The "
        "Gauss-Markov theorem guarantees that the OLS estimator achieves the "
        "minimum variance among all linear unbiased estimators.  Full factorial "
        "additionally ensures perfect orthogonality between factor columns, "
        "eliminating cross-term variance inflation."
    ),
    conditions=(
        "balanced design",
        "additive effects model",
        "equal variance across cells",
    ),
    implications=(
        "full factorial is the gold standard for main-effect estimation",
        "fractional designs sacrifice some variance efficiency for run economy",
        "imbalanced designs inflate standard errors relative to balanced factorial",
    ),
    tags=("factorial", "variance", "efficiency"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_6",
    name="Latin Square Efficiency",
    statement=(
        "The n×n Latin square design estimates n treatment effects using only n² "
        "observations, achieving O(n) efficiency over naive O(n³) approaches."
    ),
    proof_sketch=(
        "A naïve design blocking on two nuisance factors independently requires "
        "n × n × n = n³ runs for full coverage.  The Latin square constraint "
        "(each treatment appears once per row and once per column) allows "
        "simultaneous blocking on both nuisance factors in only n² runs.  "
        "The cyclic construction (i,j)→(i+j) mod n guarantees validity for all "
        "n ≥ 2 and produces orthogonal row, column, and treatment estimates."
    ),
    conditions=(
        "n treatments",
        "row and column blocking",
        "additive block effects",
    ),
    implications=(
        "Latin squares achieve O(n) run reduction over naïve factorial blocking",
        "row and column nuisance effects are estimated simultaneously at no extra cost",
        "treatment comparison is free of row-column confounding",
    ),
    tags=("latin-square", "efficiency", "blocking"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_7",
    name="Bootstrap Confidence Interval Consistency",
    statement=(
        "The bootstrap CI [θ̂ - t_{1-α/2}·s_B, θ̂ + t_{1-α/2}·s_B] achieves "
        "nominal coverage 1-α asymptotically under mild regularity conditions."
    ),
    proof_sketch=(
        "By the bootstrap central limit theorem, the bootstrap distribution of "
        "√n(θ̂* - θ̂) converges to the same limiting normal distribution as "
        "√n(θ̂ - θ).  The studentised bootstrap CI is consistent whenever θ̂ is "
        "asymptotically normal and the functional is smooth (Hadamard differentiable).  "
        "With B bootstrap replicates the Monte Carlo error in s_B is O(1/√B), "
        "which is negligible for B ≥ 1000."
    ),
    conditions=(
        "B bootstrap samples sufficient",
        "estimator is asymptotically normal",
        "smooth functional",
    ),
    implications=(
        "bootstrap CIs are valid without parametric distributional assumptions",
        "B ≥ 1000 bootstrap samples suffice for 95% CI in practice",
        "non-smooth statistics (median, quantiles) may require adjusted bootstrap variants",
    ),
    tags=("bootstrap", "confidence-interval", "consistency"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_8",
    name="Bonferroni Correction Controls FWER",
    statement=(
        "For m simultaneous tests each at level α/m, the family-wise error rate "
        "satisfies FWER ≤ α regardless of correlations between tests."
    ),
    proof_sketch=(
        "FWER = P(∃ i : reject H₀ᵢ | all H₀ᵢ true) ≤ Σᵢ P(reject H₀ᵢ | H₀ᵢ true) "
        "= m · (α/m) = α by the union bound.  The bound holds without any "
        "assumption about inter-test correlations because the union bound is "
        "universal.  For positively correlated tests the bound is conservative; "
        "Holm correction (Theorem 53.14) improves power while maintaining the "
        "same FWER guarantee."
    ),
    conditions=(
        "m independent or positively correlated tests",
        "α/m threshold applied",
    ),
    implications=(
        "applying α/m threshold guarantees FWER ≤ α for any m",
        "Bonferroni is conservative; prefer Holm or BH for higher power",
        "pre-specifying m before data collection is required for validity",
    ),
    tags=("multiple-testing", "Bonferroni", "FWER"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_9",
    name="Yield Curve Identifiability",
    statement=(
        "The yield curve Y(b) = Y_∞(1 - e^{-λb}) is identifiable from "
        "observations {(b_i, Y_i)} if and only if the b_i span a sufficient "
        "range [0, b_max] with b_max·λ >> 1."
    ),
    proof_sketch=(
        "The model has two free parameters (Y_∞, λ).  Taking the limit b → ∞ "
        "identifies Y_∞.  The curvature at b=0 identifies λ: dY/db|_{b=0} = Y_∞λ.  "
        "If b_max·λ << 1 all observed points lie in the linear regime Y ≈ Y_∞λb, "
        "where only the product Y_∞λ is identifiable and the parameters are "
        "separated only in the saturation regime.  Hence b_max·λ >> 1 is "
        "necessary and sufficient for parameter-level identifiability."
    ),
    conditions=(
        "observations span sufficient budget range",
        "Y_∞ > 0",
        "λ > 0",
    ),
    implications=(
        "calibration experiments must include high-budget observations for λ identifiability",
        "if only low-budget data are available, only the product Y_∞·λ can be estimated",
        "b_max should satisfy b_max · λ_prior ≥ 3 for practical identifiability",
    ),
    tags=("yield-curve", "identifiability", "calibration"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_10",
    name="Sequential Ablation Path Uniqueness",
    statement=(
        "Under strict component independence, the sequential ablation path is "
        "unique and yields the same component ranking as single-component ablation."
    ),
    proof_sketch=(
        "Strict independence means Y(S) = Σ_{i∈S} Y({i}) for all subsets S.  "
        "Therefore ΔY_i = Y(S) - Y(S\\{i}) = Y({i}) for all S containing i; the "
        "marginal contribution of component i is the same regardless of which "
        "other components are present.  This means the order in which components "
        "are removed does not affect their measured contributions, so all "
        "sequential paths produce the same ranking, and sequential ablation is "
        "equivalent to independent single-component ablation."
    ),
    conditions=(
        "strict component independence",
        "monotone yield function",
    ),
    implications=(
        "under independence, any ablation order gives the same ranking",
        "interaction effects signal violations of the independence assumption",
        "if sequential and parallel ablation disagree, interactions are present",
    ),
    tags=("ablation", "sequential", "uniqueness"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_11",
    name="Adaptive Experiment Convergence",
    statement=(
        "An adaptive experiment that selects design points to maximise expected "
        "information gain converges to the optimal design in O(log n) steps "
        "under Gaussian process priors."
    ),
    proof_sketch=(
        "Under a GP prior with kernel K, the maximum information gain after n "
        "queries satisfies γ_n ≤ O(log n · dim(feature space)) for squared "
        "exponential kernels (Srinivas et al. 2010).  The cumulative regret of "
        "the EIG-maximising policy is therefore sublinear: R_n = O(√(n·γ_n)).  "
        "Since the regret measures the gap from the optimal static design, "
        "convergence to the optimal design follows from R_n/n → 0 as n → ∞."
    ),
    conditions=(
        "Gaussian process prior",
        "bounded noise",
        "continuous parameter space",
    ),
    implications=(
        "Bayesian adaptive designs require O(log n) fewer experiments than static designs",
        "GP prior must be specified before the experiment begins",
        "larger noise variance slows convergence but does not prevent it",
    ),
    tags=("adaptive", "convergence", "Bayesian"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_12",
    name="Randomization Ensures Unbiasedness",
    statement=(
        "In an RCT, random assignment of treatments guarantees E[τ̂] = τ "
        "(unbiased treatment effect estimate) regardless of confounders."
    ),
    proof_sketch=(
        "Under SUTVA, the observed outcome Y_i(w) equals the potential outcome "
        "Y_i(w) under treatment w.  Random assignment makes treatment assignment "
        "Wi independent of all pre-treatment covariates X_i: Wi ⊥ (Y_i(0), Y_i(1)).  "
        "Therefore E[Y_i | Wi=1] = E[Y_i(1)] and E[Y_i | Wi=0] = E[Y_i(0)].  "
        "The difference-in-means estimator τ̂ = Ȳ₁ - Ȳ₀ satisfies E[τ̂] = "
        "E[Y(1)] - E[Y(0)] = τ, irrespective of any unmeasured confounders."
    ),
    conditions=(
        "truly random assignment",
        "stable unit treatment value assumption (SUTVA)",
    ),
    implications=(
        "randomisation is the only design mechanism that eliminates confounding bias",
        "observational studies cannot achieve unbiasedness without strong assumptions",
        "block randomisation reduces variance while preserving unbiasedness",
    ),
    tags=("RCT", "unbiasedness", "randomization"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_13",
    name="Cohen's d Measures Standardized Effect",
    statement=(
        "Cohen's d = (μ₁ - μ₂)/σ_pooled satisfies: d=0.2 (small), d=0.5 (medium), "
        "d=0.8 (large) by conventional thresholds."
    ),
    proof_sketch=(
        "Cohen (1988) calibrated effect size thresholds empirically from a review "
        "of social science literature.  Under normality with equal variances, "
        "d = (μ₁-μ₂)/σ is the natural standardised separation between two "
        "distributions.  The pooled standard deviation σ_pooled = √((σ₁²+σ₂²)/2) "
        "is used when group variances differ.  The thresholds 0.2/0.5/0.8 represent "
        "the 25th, 50th, and 75th percentiles of observed effect sizes in the "
        "original literature review, making them reference points rather than "
        "universal constants."
    ),
    conditions=(
        "normally distributed populations",
        "equal variances",
    ),
    implications=(
        "d < 0.2 signals a negligible effect that may not warrant further study",
        "d ≥ 0.5 suggests a practically meaningful ideation improvement",
        "d must be interpreted alongside n to assess practical significance",
    ),
    tags=("effect-size", "Cohen", "t-test"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_14",
    name="Holm Correction Improves Bonferroni Power",
    statement=(
        "The Holm step-down correction is uniformly more powerful than Bonferroni "
        "correction while maintaining FWER ≤ α."
    ),
    proof_sketch=(
        "Let p_(1) ≤ … ≤ p_(m) be ordered p-values with corresponding "
        "hypotheses H_(1), …, H_(m).  The Holm procedure rejects H_(i) if "
        "p_(j) ≤ α/(m-j+1) for all j ≤ i.  FWER control follows from the "
        "same union-bound argument as Bonferroni, applied to the remaining "
        "true nulls at each step.  Uniform improvement over Bonferroni holds "
        "because at step i the threshold α/(m-i+1) ≥ α/m: once some nulls "
        "are rejected, the remaining threshold is relaxed, recovering power "
        "without inflating FWER."
    ),
    conditions=(
        "m hypothesis tests",
        "independence or positive dependence",
    ),
    implications=(
        "Holm should be preferred over Bonferroni for multiple experiment programmes",
        "Holm is exact (not conservative) under independence",
        "under positive correlation Holm remains valid but Benjamini-Hochberg may be preferable",
    ),
    tags=("multiple-testing", "Holm", "power"),
))

THEOREM_CATALOG.add(Theorem(
    theorem_id="theorem_53_15",
    name="Experiment Independence Prevents Contamination",
    statement=(
        "If experiments E₁,…,Eₖ share no subjects and use independent random "
        "seeds, their results are mutually independent and can be combined via "
        "Fisher's method."
    ),
    proof_sketch=(
        "Disjoint subject pools ensure no subject's outcome in Eⱼ can influence "
        "another subject in Eᵢ (no interference).  Independent random seeds "
        "decouple treatment assignments across experiments.  Under these two "
        "conditions the outcome vectors Y₁,…,Yₖ are mutually independent random "
        "variables.  Fisher's combined test statistic X² = -2Σᵢ ln(pᵢ) follows "
        "a χ²(2k) distribution under the global null, which requires the "
        "independence of the individual p-values pᵢ."
    ),
    conditions=(
        "disjoint subject pools",
        "independent random seeds",
        "no information leakage",
    ),
    implications=(
        "experiment parallelism is statistically valid under disjoint subject pools",
        "shared infrastructure (random seeds, subject IDs) must be isolated across experiments",
        "Fisher's method allows combining independent replication p-values into one test",
    ),
    tags=("independence", "contamination", "combination"),
))
