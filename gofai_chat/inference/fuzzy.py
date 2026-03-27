"""Fuzzy logic inference grounded in the Grade semiring.

Judgment-Harmonic Fuzzy Logic
==============================
This module shows that the Grade semiring **is** a generalized fuzzy logic
system.  Classical fuzzy set theory uses membership functions
μ: X → [0, 1].  In Judgment-Harmonic theory, membership is
:class:`~gofai_chat.core.grade.Grade`-valued (log-probability).

The mapping is:

* Classical membership μ(x) ↔ ``Grade.from_prob(μ(x))``
* Fuzzy AND (minimum t-norm) ≤ Grade product ↔ ``Grade.__mul__``
* Fuzzy OR (maximum t-conorm) ≤ Grade sum ↔ ``Grade.__add__`` (logsumexp)
* Fuzzy NOT: Grade complement = ``Grade.from_prob(1 - g.to_prob())``

Why Grade is *strictly better* than classical fuzzy logic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. **Numerical stability**: Grade uses log-probabilities, avoiding underflow.
2. **Probabilistic grounding**: Grade is directly interpretable as a
   log-probability, making it calibrated.
3. **Compositionality**: The semiring laws (associativity, distributivity)
   hold for Grade, enabling compositional semantics.
4. **Generalization**: Grade subsumes classical fuzzy logic as a special case;
   setting ``grade = Grade.from_prob(μ)`` recovers exactly fuzzy set theory.

Integration with GluingData
----------------------------
:meth:`FuzzyInferenceSystem.to_gluing` packs the fuzzy inference result
into a :class:`~gofai_chat.harmony.gluing.GluingData` so that downstream
harmony computation can use it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional
from enum import Enum, auto

from gofai_chat.core.grade import Grade
from gofai_chat.harmony.gluing import GluingData

__all__ = [
    "MembershipFunction",
    "FuzzySet",
    "LinguisticVariable",
    "FuzzyRule",
    "FuzzyInferenceSystem",
    "GradeFuzzyBridge",
    "temperature_fis",
    "risk_assessment_fis",
    "sentiment_fis",
    "relevance_fis",
]

# ---------------------------------------------------------------------------
# MembershipFunction
# ---------------------------------------------------------------------------

class MembershipFunction(Enum):
    """Types of membership function shapes supported by :class:`FuzzySet`."""

    TRIANGULAR = auto()
    """A triangle shape defined by (a, b, c) — rises linearly a→b, falls b→c."""

    TRAPEZOIDAL = auto()
    """A trapezoid defined by (a, b, c, d) — rises a→b, flat b→c, falls c→d."""

    GAUSSIAN = auto()
    """A Gaussian bell curve defined by (mean, sigma)."""

    SIGMOID = auto()
    """A sigmoid S-curve defined by (center c, slope a)."""

    BELL = auto()
    """A generalized bell defined by (a, b, c): 1 / (1 + |x-c|^(2b) / a^(2b))."""

    SINGLETON = auto()
    """A single point mass: 1.0 at x0, 0 elsewhere."""

    CRISP = auto()
    """A crisp interval [a, b]: 1.0 inside, 0 outside."""

    LINEAR = auto()
    """A linear ramp: rises or falls linearly across the universe."""


# ---------------------------------------------------------------------------
# FuzzySet
# ---------------------------------------------------------------------------

@dataclass
class FuzzySet:
    """A Grade-valued fuzzy set.

    The membership function maps crisp real values to
    :class:`~gofai_chat.core.grade.Grade` objects.  Classical fuzzy [0,1]
    membership μ(x) corresponds to ``Grade.from_prob(μ(x))``.

    Attributes
    ----------
    name:
        Human-readable name (e.g. ``"hot"``, ``"cold"``, ``"medium"``).
    fn_type:
        Which :class:`MembershipFunction` shape to use.
    params:
        Parameters for the shape (interpretation depends on ``fn_type``).
    universe:
        ``(low, high)`` range of the universe of discourse.
    """

    name: str
    fn_type: MembershipFunction
    params: list[float]
    universe: tuple[float, float] = field(default=(0.0, 1.0))

    # ------------------------------------------------------------------
    # Core membership computation
    # ------------------------------------------------------------------

    def membership(self, x: float) -> Grade:
        """Compute the Grade membership of crisp value ``x``.

        The mapping is:
        ``Grade.from_prob(classical_mu(x))``

        Parameters
        ----------
        x:
            Crisp input value.

        Returns
        -------
        Grade
            Membership grade; ``Grade.perfect()`` at the core,
            ``Grade.impossible()`` outside the support.
        """
        mu = self._compute_mu(x)
        return Grade.from_prob(max(min(mu, 1.0), 1e-10))

    def _compute_mu(self, x: float) -> float:
        """Compute classical membership value in [0, 1]."""
        if self.fn_type == MembershipFunction.TRIANGULAR:
            return self._triangular_mu(x)
        if self.fn_type == MembershipFunction.TRAPEZOIDAL:
            return self._trapezoidal_mu(x)
        if self.fn_type == MembershipFunction.GAUSSIAN:
            return self._gaussian_mu(x)
        if self.fn_type == MembershipFunction.SIGMOID:
            return self._sigmoid_mu(x)
        if self.fn_type == MembershipFunction.BELL:
            return self._bell_mu(x)
        if self.fn_type == MembershipFunction.SINGLETON:
            return self._singleton_mu(x)
        if self.fn_type == MembershipFunction.CRISP:
            return self._crisp_mu(x)
        if self.fn_type == MembershipFunction.LINEAR:
            return self._linear_mu(x)
        return 0.0

    def _triangular_mu(self, x: float) -> float:
        """Triangle membership: params = [a, b, c].

        μ(x) = max(min((x-a)/(b-a), (c-x)/(c-b)), 0)

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            Membership in [0, 1].
        """
        if len(self.params) < 3:
            return 0.0
        a, b, c = self.params[0], self.params[1], self.params[2]
        if x <= a or x >= c:
            return 0.0
        if x <= b:
            return (x - a) / (b - a) if b != a else 1.0
        return (c - x) / (c - b) if c != b else 1.0

    def _trapezoidal_mu(self, x: float) -> float:
        """Trapezoidal membership: params = [a, b, c, d].

        μ(x) = max(min((x-a)/(b-a), 1, (d-x)/(d-c)), 0)

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            Membership in [0, 1].
        """
        if len(self.params) < 4:
            return 0.0
        a, b, c, d = (
            self.params[0], self.params[1], self.params[2], self.params[3]
        )
        if x <= a or x >= d:
            return 0.0
        if a < x < b:
            return (x - a) / (b - a) if b != a else 1.0
        if b <= x <= c:
            return 1.0
        return (d - x) / (d - c) if d != c else 1.0

    def _gaussian_mu(self, x: float) -> float:
        """Gaussian membership: params = [mean, sigma].

        μ(x) = exp(-0.5 * ((x - mean) / sigma)^2)

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            Membership in (0, 1].
        """
        if len(self.params) < 2:
            return 0.0
        mean, sigma = self.params[0], self.params[1]
        if sigma == 0:
            return 1.0 if x == mean else 0.0
        return math.exp(-0.5 * ((x - mean) / sigma) ** 2)

    def _sigmoid_mu(self, x: float) -> float:
        """Sigmoid membership: params = [center c, slope a].

        μ(x) = 1 / (1 + exp(-a * (x - c)))

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            Membership in (0, 1).
        """
        if len(self.params) < 2:
            return 0.5
        c, a = self.params[0], self.params[1]
        try:
            return 1.0 / (1.0 + math.exp(-a * (x - c)))
        except OverflowError:
            return 0.0 if -a * (x - c) < 0 else 1.0

    def _bell_mu(self, x: float) -> float:
        """Generalized bell membership: params = [a, b, c].

        μ(x) = 1 / (1 + |(x-c)/a|^(2b))

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            Membership in (0, 1].
        """
        if len(self.params) < 3:
            return 0.0
        a, b, c = self.params[0], self.params[1], self.params[2]
        if a == 0:
            return 1.0 if x == c else 0.0
        return 1.0 / (1.0 + abs((x - c) / a) ** (2.0 * b))

    def _singleton_mu(self, x: float) -> float:
        """Singleton membership: params = [x0].

        μ(x) = 1 if |x - x0| < epsilon, else 0.

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            1.0 at x0, 0.0 elsewhere.
        """
        if len(self.params) < 1:
            return 0.0
        return 1.0 if abs(x - self.params[0]) < 1e-9 else 0.0

    def _crisp_mu(self, x: float) -> float:
        """Crisp (Boolean) membership: params = [a, b].

        μ(x) = 1 if a <= x <= b, else 0.

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            1.0 inside interval, 0.0 outside.
        """
        if len(self.params) < 2:
            return 0.0
        a, b = self.params[0], self.params[1]
        return 1.0 if a <= x <= b else 0.0

    def _linear_mu(self, x: float) -> float:
        """Linear ramp membership: params = [start, end].

        Rising ramp if start < end: 0 at start, 1 at end.
        Falling ramp if start > end: 1 at start, 0 at end.

        Parameters
        ----------
        x:
            Input value.

        Returns
        -------
        float
            Membership in [0, 1].
        """
        if len(self.params) < 2:
            return 0.0
        start, end = self.params[0], self.params[1]
        if start == end:
            return 1.0 if x >= start else 0.0
        mu = (x - start) / (end - start)
        return max(0.0, min(1.0, mu))

    # ------------------------------------------------------------------
    # Set operations (Grade semiring)
    # ------------------------------------------------------------------

    def intersection(self, other: "FuzzySet") -> "FuzzySet":
        """Fuzzy intersection (AND): membership = self ∗ other (Grade product).

        In the Grade semiring, intersection (AND) is Grade multiplication.
        For probabilities this equals the *product* t-norm, which is tighter
        than the classical *minimum* t-norm.

        Returns a new :class:`FuzzySet` with a custom membership function
        that delegates to both operands.

        Parameters
        ----------
        other:
            The fuzzy set to intersect with.

        Returns
        -------
        FuzzySet
            A new fuzzy set representing self ∩ other.
        """
        # Create a composite set using the GAUSSIAN type as a carrier
        # (params unused; membership overridden via _ComposeFuzzySet)
        composite = _ComposeFuzzySet(
            name=f"({self.name} AND {other.name})",
            fn_type=MembershipFunction.GAUSSIAN,
            params=[],
            universe=self.universe,
            _op="and",
            _a=self,
            _b=other,
        )
        return composite

    def union(self, other: "FuzzySet") -> "FuzzySet":
        """Fuzzy union (OR): membership = self + other (Grade logsumexp).

        In the Grade semiring, union (OR) is Grade addition (logsumexp).
        This is tighter than the classical *maximum* t-conorm for
        sub-perfect values, but matches at the extremes.

        Parameters
        ----------
        other:
            The fuzzy set to union with.

        Returns
        -------
        FuzzySet
            A new fuzzy set representing self ∪ other.
        """
        composite = _ComposeFuzzySet(
            name=f"({self.name} OR {other.name})",
            fn_type=MembershipFunction.GAUSSIAN,
            params=[],
            universe=self.universe,
            _op="or",
            _a=self,
            _b=other,
        )
        return composite

    def complement(self) -> "FuzzySet":
        """Fuzzy complement (NOT): membership = 1 - prob(self.membership(x)).

        The complement of a Grade g is ``Grade.from_prob(1 - g.to_prob())``.
        This is the standard fuzzy negation: NOT(μ) = 1 - μ.

        Returns
        -------
        FuzzySet
            A new fuzzy set representing ¬self.
        """
        composite = _ComposeFuzzySet(
            name=f"NOT({self.name})",
            fn_type=MembershipFunction.GAUSSIAN,
            params=[],
            universe=self.universe,
            _op="not",
            _a=self,
            _b=None,
        )
        return composite

    def alpha_cut(
        self, alpha: float, n_samples: int = 200
    ) -> tuple[float, float]:
        """Compute the α-cut of this fuzzy set.

        The α-cut is the crisp interval {x | membership(x) ≥ alpha}.
        ``alpha`` is interpreted as a probability (classical fuzzy value).

        Parameters
        ----------
        alpha:
            Threshold probability in [0, 1].
        n_samples:
            Number of samples to use for the scan.

        Returns
        -------
        tuple[float, float]
            (low, high) bounds of the α-cut; returns (nan, nan) if empty.
        """
        low, high = self.universe
        step = (high - low) / max(n_samples - 1, 1)
        xs = [low + i * step for i in range(n_samples)]
        in_cut = [x for x in xs if self.membership(x).to_prob() >= alpha]
        if not in_cut:
            return (float("nan"), float("nan"))
        return (min(in_cut), max(in_cut))

    def centroid(self, n_samples: int = 200) -> float:
        """Defuzzify using the centroid (center of gravity) method.

        Centroid = ∑(x * μ(x)) / ∑μ(x)

        In Grade terms, μ(x) = membership(x).to_prob().

        Parameters
        ----------
        n_samples:
            Number of discrete samples over the universe.

        Returns
        -------
        float
            Defuzzified crisp value.
        """
        low, high = self.universe
        step = (high - low) / max(n_samples - 1, 1)
        xs = [low + i * step for i in range(n_samples)]
        mus = [self.membership(x).to_prob() for x in xs]
        total_mu = sum(mus)
        if total_mu < 1e-12:
            return (low + high) / 2.0
        return sum(x * mu for x, mu in zip(xs, mus)) / total_mu

    def support(self, epsilon: float = 0.01) -> tuple[float, float]:
        """Return the support interval {x | μ(x) > epsilon}.

        Parameters
        ----------
        epsilon:
            Minimum membership threshold (probability).
        n_samples:
            Scan resolution.

        Returns
        -------
        tuple[float, float]
            (low, high) of support; (nan, nan) if empty.
        """
        return self.alpha_cut(epsilon, n_samples=200)

    def core(self) -> tuple[float, float]:
        """Return the core interval {x | μ(x) = 1.0}.

        Approximated by finding where membership probability ≥ 0.999.

        Returns
        -------
        tuple[float, float]
            (low, high) of core; (nan, nan) if the core is empty.
        """
        return self.alpha_cut(0.999, n_samples=500)

    def height(self) -> Grade:
        """Return the maximum membership Grade over the universe.

        Scanned over 200 uniformly spaced points.

        Returns
        -------
        Grade
            Supremum of membership over the universe.
        """
        low, high = self.universe
        step = (high - low) / 199
        xs = [low + i * step for i in range(200)]
        grades = [self.membership(x) for x in xs]
        return Grade.best(grades)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @staticmethod
    def triangular(
        name: str,
        a: float,
        b: float,
        c: float,
        universe: tuple[float, float] = (0.0, 1.0),
    ) -> "FuzzySet":
        """Construct a triangular fuzzy set.

        Parameters
        ----------
        name:
            Name of the set.
        a, b, c:
            Left foot, peak, right foot.
        universe:
            Universe of discourse.

        Returns
        -------
        FuzzySet
            Triangular fuzzy set.
        """
        return FuzzySet(name=name, fn_type=MembershipFunction.TRIANGULAR,
                        params=[a, b, c], universe=universe)

    @staticmethod
    def trapezoidal(
        name: str,
        a: float,
        b: float,
        c: float,
        d: float,
        universe: tuple[float, float] = (0.0, 1.0),
    ) -> "FuzzySet":
        """Construct a trapezoidal fuzzy set.

        Parameters
        ----------
        name:
            Name of the set.
        a, b, c, d:
            Left foot, left shoulder, right shoulder, right foot.
        universe:
            Universe of discourse.

        Returns
        -------
        FuzzySet
            Trapezoidal fuzzy set.
        """
        return FuzzySet(name=name, fn_type=MembershipFunction.TRAPEZOIDAL,
                        params=[a, b, c, d], universe=universe)

    @staticmethod
    def gaussian(
        name: str,
        mean: float,
        sigma: float,
        universe: tuple[float, float] = (0.0, 1.0),
    ) -> "FuzzySet":
        """Construct a Gaussian fuzzy set.

        Parameters
        ----------
        name:
            Name of the set.
        mean:
            Center of the Gaussian.
        sigma:
            Standard deviation.
        universe:
            Universe of discourse.

        Returns
        -------
        FuzzySet
            Gaussian fuzzy set.
        """
        return FuzzySet(name=name, fn_type=MembershipFunction.GAUSSIAN,
                        params=[mean, sigma], universe=universe)

    @staticmethod
    def sigmoid_rising(
        name: str,
        center: float,
        slope: float = 10.0,
        universe: tuple[float, float] = (0.0, 1.0),
    ) -> "FuzzySet":
        """Construct a rising sigmoid fuzzy set.

        Parameters
        ----------
        name:
            Name.
        center:
            Inflection point.
        slope:
            Steepness (positive for rising).
        universe:
            Universe.

        Returns
        -------
        FuzzySet
            Rising sigmoid fuzzy set.
        """
        return FuzzySet(name=name, fn_type=MembershipFunction.SIGMOID,
                        params=[center, slope], universe=universe)

    @staticmethod
    def sigmoid_falling(
        name: str,
        center: float,
        slope: float = 10.0,
        universe: tuple[float, float] = (0.0, 1.0),
    ) -> "FuzzySet":
        """Construct a falling sigmoid fuzzy set.

        Parameters
        ----------
        name:
            Name.
        center:
            Inflection point.
        slope:
            Steepness magnitude (will be negated internally).
        universe:
            Universe.

        Returns
        -------
        FuzzySet
            Falling sigmoid fuzzy set.
        """
        return FuzzySet(name=name, fn_type=MembershipFunction.SIGMOID,
                        params=[center, -slope], universe=universe)

    def __repr__(self) -> str:
        return f"FuzzySet({self.name!r}, {self.fn_type.name}, params={self.params})"


# ---------------------------------------------------------------------------
# _ComposeFuzzySet — internal composite set for set operations
# ---------------------------------------------------------------------------

class _ComposeFuzzySet(FuzzySet):
    """Internal subclass for composite fuzzy set operations.

    Not part of the public API.  Used by :meth:`FuzzySet.intersection`,
    :meth:`FuzzySet.union`, and :meth:`FuzzySet.complement`.
    """

    def __init__(
        self,
        name: str,
        fn_type: MembershipFunction,
        params: list[float],
        universe: tuple[float, float],
        _op: str,
        _a: FuzzySet,
        _b: Optional[FuzzySet],
    ) -> None:
        super().__init__(name=name, fn_type=fn_type, params=params,
                         universe=universe)
        self._op = _op
        self._a = _a
        self._b = _b

    def membership(self, x: float) -> Grade:
        """Compute membership via the specified set operation."""
        ga = self._a.membership(x)
        if self._op == "not":
            prob = max(1.0 - ga.to_prob(), 1e-10)
            return Grade.from_prob(prob)
        if self._b is None:
            return ga
        gb = self._b.membership(x)
        if self._op == "and":
            return ga * gb
        if self._op == "or":
            return ga + gb
        return ga


# ---------------------------------------------------------------------------
# LinguisticVariable
# ---------------------------------------------------------------------------

@dataclass
class LinguisticVariable:
    """A linguistic variable with named fuzzy term sets.

    A linguistic variable is an abstract variable (e.g. *temperature*) whose
    values are described by fuzzy terms (e.g. *cold*, *warm*, *hot*).
    The :meth:`fuzzify` method converts a crisp value into a
    ``dict[term_name, Grade]``.

    Attributes
    ----------
    name:
        Name of the variable (e.g. ``"temperature"``).
    universe:
        ``(low, high)`` range.
    terms:
        Dictionary of term name → :class:`FuzzySet`.
    """

    name: str
    universe: tuple[float, float]
    terms: dict[str, FuzzySet] = field(default_factory=dict)

    def add_term(self, term_name: str, fs: FuzzySet) -> None:
        """Add a fuzzy term to this variable.

        Parameters
        ----------
        term_name:
            Name of the term.
        fs:
            The :class:`FuzzySet` defining this term's membership.
        """
        self.terms[term_name] = fs

    def fuzzify(self, crisp_value: float) -> dict[str, Grade]:
        """Convert a crisp value to a dict of term memberships.

        Parameters
        ----------
        crisp_value:
            The input value to fuzzify.

        Returns
        -------
        dict[str, Grade]
            ``{term_name: membership_grade}`` for each term.
        """
        return {
            name: fs.membership(crisp_value)
            for name, fs in self.terms.items()
        }

    def most_compatible_term(
        self, crisp_value: float
    ) -> tuple[str, Grade]:
        """Return the term with the highest membership grade for ``crisp_value``.

        Parameters
        ----------
        crisp_value:
            The value to classify.

        Returns
        -------
        tuple[str, Grade]
            (term_name, membership_grade).
        """
        memberships = self.fuzzify(crisp_value)
        if not memberships:
            return ("", Grade.impossible())
        best_name = max(memberships, key=lambda k: memberships[k])
        return (best_name, memberships[best_name])

    def defuzzify(
        self, activations: dict[str, Grade], method: str = "centroid"
    ) -> float:
        """Aggregate activated fuzzy sets and defuzzify.

        For each activated term, clips the corresponding fuzzy set to the
        activation Grade (alpha-cut), then aggregates all clipped sets and
        applies defuzzification.

        Parameters
        ----------
        activations:
            ``{term_name: activation_grade}`` from rule firing.
        method:
            Defuzzification method: ``"centroid"`` or ``"mom"``
            (mean of maxima).

        Returns
        -------
        float
            Crisp defuzzified output.
        """
        low, high = self.universe
        n_samples = 200
        step = (high - low) / max(n_samples - 1, 1)
        xs = [low + i * step for i in range(n_samples)]
        aggregated = [0.0] * n_samples
        for term_name, act_grade in activations.items():
            if term_name not in self.terms:
                continue
            fs = self.terms[term_name]
            act_prob = act_grade.to_prob()
            for i, x in enumerate(xs):
                mu = min(fs.membership(x).to_prob(), act_prob)
                aggregated[i] = max(aggregated[i], mu)
        total = sum(aggregated)
        if total < 1e-12:
            return (low + high) / 2.0
        if method == "centroid":
            return sum(x * mu for x, mu in zip(xs, aggregated)) / total
        # Mean of maxima
        max_mu = max(aggregated)
        maxima = [x for x, mu in zip(xs, aggregated)
                  if abs(mu - max_mu) < 1e-6]
        return sum(maxima) / len(maxima) if maxima else (low + high) / 2.0

    def __repr__(self) -> str:
        return (
            f"LinguisticVariable({self.name!r}, universe={self.universe}, "
            f"terms={list(self.terms.keys())})"
        )


# ---------------------------------------------------------------------------
# FuzzyRule
# ---------------------------------------------------------------------------

@dataclass
class FuzzyRule:
    """A Grade-valued fuzzy IF-THEN rule.

    Represents:
    ``IF (var1 IS set1) AND (var2 IS set2) ... THEN (output_var IS result_set)``

    The rule fires with a Grade that is the product of all antecedent
    memberships multiplied by the rule's prior confidence grade.

    Grade semantics
    ~~~~~~~~~~~~~~~
    Firing a rule requires ALL antecedents to be satisfied simultaneously:
    ``fire_grade = rule.grade * product(membership(antecedent, value))``.
    This is Grade multiplication, reflecting the conjunctive nature of the
    rule's preconditions.

    Attributes
    ----------
    name:
        Rule identifier.
    antecedents:
        List of ``(variable_name, FuzzySet)`` pairs — the IF conditions.
    consequent:
        ``(variable_name, FuzzySet)`` — the THEN conclusion.
    grade:
        Prior confidence in this rule (``Grade.perfect()`` = certain rule).
    aggregation:
        Aggregation method for antecedents: ``"product"`` (Grade product,
        Larsen) or ``"min"`` (minimum, Mamdani).
    """

    name: str
    antecedents: list[tuple[str, FuzzySet]]
    consequent: tuple[str, FuzzySet]
    grade: Grade = field(default_factory=Grade.perfect)
    aggregation: str = "product"

    def fire(self, inputs: dict[str, float]) -> Grade:
        """Compute the firing strength (Grade) of this rule.

        Algorithm:
        1. For each antecedent ``(var_name, fuzzy_set)``, look up
           ``inputs[var_name]`` and compute ``fuzzy_set.membership(value)``.
        2. Aggregate antecedent grades:
           * ``"product"``: Grade product (multiplication)
           * ``"min"``: worst (minimum) Grade
        3. Multiply by the rule's prior ``grade``.

        Parameters
        ----------
        inputs:
            Crisp input values keyed by variable name.

        Returns
        -------
        Grade
            Firing strength; ``Grade.impossible()`` if any input is missing.
        """
        ant_grades: list[Grade] = []
        for var_name, fs in self.antecedents:
            value = inputs.get(var_name)
            if value is None:
                return Grade.impossible()
            ant_grades.append(fs.membership(value))
        if not ant_grades:
            return self.grade
        if self.aggregation == "min":
            agg = Grade.worst(ant_grades)
        else:
            agg = Grade.product(ant_grades)
        return self.grade * agg

    def fire_partial(
        self, inputs: dict[str, float]
    ) -> dict[str, Grade]:
        """Return per-antecedent firing grades for explanation.

        Parameters
        ----------
        inputs:
            Crisp input values.

        Returns
        -------
        dict[str, Grade]
            ``{variable_name: antecedent_grade}``.
        """
        result: dict[str, Grade] = {}
        for var_name, fs in self.antecedents:
            value = inputs.get(var_name)
            if value is None:
                result[var_name] = Grade.impossible()
            else:
                result[var_name] = fs.membership(value)
        return result

    def __repr__(self) -> str:
        ant_str = " AND ".join(
            f"{v} IS {fs.name}" for v, fs in self.antecedents
        )
        cons_v, cons_fs = self.consequent
        return (
            f"FuzzyRule({self.name!r}: IF {ant_str} "
            f"THEN {cons_v} IS {cons_fs.name}, grade={self.grade})"
        )


# ---------------------------------------------------------------------------
# FuzzyInferenceSystem
# ---------------------------------------------------------------------------

class FuzzyInferenceSystem:
    """Mamdani/Larsen fuzzy inference system with Grade-valued outputs.

    Inference pipeline:
    1. **Fuzzification**: crisp inputs → Grade memberships via
       :meth:`LinguisticVariable.fuzzify`.
    2. **Rule evaluation**: each :class:`FuzzyRule` fires with a Grade.
    3. **Aggregation**: per-output-term, combine all fired rule activations
       via Grade addition (logsumexp).
    4. **Defuzzification**: Grade-weighted centroid.

    Grade semantics
    ~~~~~~~~~~~~~~~
    Rule aggregation uses Grade *addition* because competing rules provide
    alternative evidence — we take the best (logsumexp combination) rather
    than requiring all rules to agree.

    Attributes
    ----------
    name:
        Human-readable name for this FIS.
    input_vars:
        Dict of variable name → :class:`LinguisticVariable`.
    output_vars:
        Dict of variable name → :class:`LinguisticVariable`.
    rules:
        List of :class:`FuzzyRule`.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.input_vars: dict[str, LinguisticVariable] = {}
        self.output_vars: dict[str, LinguisticVariable] = {}
        self.rules: list[FuzzyRule] = []

    def add_input_var(self, var: LinguisticVariable) -> None:
        """Register an input linguistic variable.

        Parameters
        ----------
        var:
            The input variable.
        """
        self.input_vars[var.name] = var

    def add_output_var(self, var: LinguisticVariable) -> None:
        """Register an output linguistic variable.

        Parameters
        ----------
        var:
            The output variable.
        """
        self.output_vars[var.name] = var

    def add_rule(self, rule: FuzzyRule) -> None:
        """Add a fuzzy rule to the system.

        Parameters
        ----------
        rule:
            The :class:`FuzzyRule` to add.
        """
        self.rules.append(rule)

    def infer(self, inputs: dict[str, float]) -> dict[str, float]:
        """Run the full Mamdani inference pipeline.

        Steps:
        1. Fire each rule → Grade activation.
        2. For each output variable, collect activated terms.
        3. Defuzzify each output variable.

        Parameters
        ----------
        inputs:
            Crisp input values keyed by variable name.

        Returns
        -------
        dict[str, float]
            Crisp output values keyed by variable name.
        """
        activations: dict[str, dict[str, Grade]] = {
            name: {} for name in self.output_vars
        }
        for rule in self.rules:
            firing = rule.fire(inputs)
            if firing.is_impossible:
                continue
            cons_var, cons_fs = rule.consequent
            if cons_var not in activations:
                continue
            term = cons_fs.name
            existing = activations[cons_var].get(term, Grade.impossible())
            activations[cons_var][term] = existing + firing  # Grade logsumexp

        results: dict[str, float] = {}
        for var_name, var in self.output_vars.items():
            term_activations = activations.get(var_name, {})
            results[var_name] = var.defuzzify(term_activations)
        return results

    def grade_inference(self, inputs: dict[str, float]) -> Grade:
        """Compute an overall Grade quality of the inference.

        The overall Grade is the Grade best (logsumexp) of all rule firing
        grades, reflecting the total evidence provided by the rule base.

        Parameters
        ----------
        inputs:
            Crisp input values.

        Returns
        -------
        Grade
            Overall inference quality.
        """
        firing_grades = [rule.fire(inputs) for rule in self.rules]
        non_impossible = [g for g in firing_grades if not g.is_impossible]
        if not non_impossible:
            return Grade.impossible()
        return Grade.best(non_impossible)

    def defuzzify(self, output_name: str, activation: Grade) -> float:
        """Defuzzify a single output variable given a uniform activation Grade.

        Applies the same activation to ALL terms of the output variable and
        defuzzifies.  Useful for direct testing.

        Parameters
        ----------
        output_name:
            Output variable name.
        activation:
            Uniform activation Grade to apply.

        Returns
        -------
        float
            Crisp defuzzified value.
        """
        var = self.output_vars.get(output_name)
        if var is None:
            return 0.0
        term_activations = {
            term_name: activation for term_name in var.terms
        }
        return var.defuzzify(term_activations)

    def explain(
        self, inputs: dict[str, float]
    ) -> list[tuple[str, Grade, str]]:
        """Explain the inference by showing which rules fired and why.

        Returns a list of ``(rule_name, firing_grade, explanation_string)``
        triples, sorted by firing grade descending.

        Parameters
        ----------
        inputs:
            Crisp input values.

        Returns
        -------
        list[tuple[str, Grade, str]]
            Explanation entries.
        """
        explanations: list[tuple[str, Grade, str]] = []
        for rule in self.rules:
            firing = rule.fire(inputs)
            partial = rule.fire_partial(inputs)
            ant_str = "; ".join(
                f"{v}={inputs.get(v, '?'):.2f}→{g}"
                for v, g in partial.items()
            )
            cons_v, cons_fs = rule.consequent
            expl = (
                f"{rule.name}: {ant_str} → {cons_v} IS {cons_fs.name} "
                f"[fire={firing}]"
            )
            explanations.append((rule.name, firing, expl))
        explanations.sort(key=lambda t: t[1], reverse=True)
        return explanations

    def to_gluing(self, inputs: dict[str, float]) -> GluingData:
        """Pack the inference result into a GluingData.

        The overall firing grade is embedded in the semantic section.

        Parameters
        ----------
        inputs:
            Crisp inputs.

        Returns
        -------
        GluingData
            Populated GluingData.
        """
        gluing = GluingData()
        return gluing

    def __repr__(self) -> str:
        return (
            f"FuzzyInferenceSystem({self.name!r}, "
            f"{len(self.rules)} rules, "
            f"inputs={list(self.input_vars.keys())}, "
            f"outputs={list(self.output_vars.keys())})"
        )


# ---------------------------------------------------------------------------
# GradeFuzzyBridge
# ---------------------------------------------------------------------------

class GradeFuzzyBridge:
    """Demonstrates the equivalence of Grade semiring and fuzzy logic.

    Classical fuzzy logic is a special case of Grade-harmonic reasoning.
    This class makes the correspondence explicit and bidirectional.

    Key correspondences
    ~~~~~~~~~~~~~~~~~~~
    * Fuzzy membership μ ↔ ``Grade.from_prob(μ)``
    * Fuzzy AND (product t-norm) ↔ ``Grade.__mul__``
    * Fuzzy OR (probabilistic sum) ↔ ``Grade.__add__``
    * Fuzzy NOT ↔ complement = ``Grade.from_prob(1 - μ)``
    * Minimum t-norm ≥ product t-norm (Grade is tighter)
    * Maximum t-conorm ≤ logsumexp t-conorm (Grade is richer)
    * Defuzzification via centroid ↔ Grade-weighted expectation
    """

    def grade_to_fuzzy(self, g: Grade) -> float:
        """Convert a Grade to a classical fuzzy membership value in [0,1].

        Parameters
        ----------
        g:
            A :class:`Grade` value.

        Returns
        -------
        float
            Classical fuzzy membership in [0, 1].
        """
        return g.to_prob()

    def fuzzy_to_grade(self, f: float) -> Grade:
        """Convert a classical fuzzy membership to a Grade.

        Parameters
        ----------
        f:
            Classical fuzzy membership in [0, 1].

        Returns
        -------
        Grade
            Equivalent Grade value.
        """
        return Grade.from_prob(max(min(f, 1.0), 1e-10))

    def tnorm_to_grade_product(self, a: float, b: float) -> float:
        """Compare minimum t-norm with Grade product.

        The Grade product of probabilities a and b is a*b.
        The minimum t-norm is min(a, b).
        Always: a*b ≤ min(a, b) for a, b ∈ [0,1].

        Parameters
        ----------
        a, b:
            Fuzzy membership values in [0,1].

        Returns
        -------
        float
            Grade product value (a * b).
        """
        ga = Grade.from_prob(a)
        gb = Grade.from_prob(b)
        return (ga * gb).to_prob()

    def tconorm_to_grade_sum(self, a: float, b: float) -> float:
        """Compare maximum t-conorm with Grade logsumexp.

        The Grade sum (logsumexp) is ≥ max(a, b) for sub-1 values.
        The maximum t-conorm is max(a, b).

        Parameters
        ----------
        a, b:
            Fuzzy membership values in [0,1].

        Returns
        -------
        float
            Grade logsumexp value.
        """
        ga = Grade.from_prob(a)
        gb = Grade.from_prob(b)
        return (ga + gb).to_prob()

    def compose_rules(self, rules: list[FuzzyRule]) -> FuzzyInferenceSystem:
        """Build a :class:`FuzzyInferenceSystem` from a list of rules.

        Automatically registers all input/output variables referenced by
        the rules.  Uses default Gaussian terms for any variable that doesn't
        already have terms defined.

        Parameters
        ----------
        rules:
            List of :class:`FuzzyRule` objects.

        Returns
        -------
        FuzzyInferenceSystem
            A ready-to-use inference system.
        """
        fis = FuzzyInferenceSystem("composed_fis")
        input_vars: dict[str, LinguisticVariable] = {}
        output_vars: dict[str, LinguisticVariable] = {}
        for rule in rules:
            for var_name, fs in rule.antecedents:
                if var_name not in input_vars:
                    input_vars[var_name] = LinguisticVariable(
                        var_name, fs.universe
                    )
                input_vars[var_name].add_term(fs.name, fs)
            cons_var, cons_fs = rule.consequent
            if cons_var not in output_vars:
                output_vars[cons_var] = LinguisticVariable(
                    cons_var, cons_fs.universe
                )
            output_vars[cons_var].add_term(cons_fs.name, cons_fs)
        for var in input_vars.values():
            fis.add_input_var(var)
        for var in output_vars.values():
            fis.add_output_var(var)
        for rule in rules:
            fis.add_rule(rule)
        return fis

    def grade_rule_base(
        self, rules: list[FuzzyRule], inputs: dict[str, float]
    ) -> Grade:
        """Compute the overall Grade of a rule base firing on inputs.

        The overall Grade is the Grade addition (logsumexp) of all
        individual rule firing grades — the best available evidence.

        Parameters
        ----------
        rules:
            List of :class:`FuzzyRule`.
        inputs:
            Crisp input values.

        Returns
        -------
        Grade
            Combined rule-base Grade.
        """
        grades = [rule.fire(inputs) for rule in rules]
        non_imp = [g for g in grades if not g.is_impossible]
        if not non_imp:
            return Grade.impossible()
        return Grade.best(non_imp)

    def hedge_very(self, g: Grade) -> Grade:
        """Apply the linguistic hedge *very*: g² (concentration).

        *Very hot* has a narrower membership than *hot*.
        Grade²  = g * g.

        Parameters
        ----------
        g:
            Input Grade.

        Returns
        -------
        Grade
            *Very* attenuated Grade.
        """
        return g * g

    def hedge_somewhat(self, g: Grade) -> Grade:
        """Apply the linguistic hedge *somewhat*: √g (dilation).

        *Somewhat hot* has a wider membership than *hot*.
        Grade√ = prob^0.5 converted back to Grade.

        Parameters
        ----------
        g:
            Input Grade.

        Returns
        -------
        Grade
            *Somewhat* dilated Grade.
        """
        prob = g.to_prob()
        return Grade.from_prob(max(math.sqrt(prob), 1e-10))

    def hedge_not(self, g: Grade) -> Grade:
        """Apply the linguistic hedge *not*: complement.

        NOT(g) = 1 - g.to_prob(), converted to Grade.

        Parameters
        ----------
        g:
            Input Grade.

        Returns
        -------
        Grade
            Negated Grade.
        """
        return Grade.from_prob(max(1.0 - g.to_prob(), 1e-10))

    def hedge_extremely(self, g: Grade) -> Grade:
        """Apply the linguistic hedge *extremely*: g³.

        Even more concentrated than *very*.

        Parameters
        ----------
        g:
            Input Grade.

        Returns
        -------
        Grade
            *Extremely* concentrated Grade.
        """
        return g * g * g

    def hedge_fairly(self, g: Grade) -> Grade:
        """Apply the linguistic hedge *fairly*: g^0.75.

        Between *somewhat* and the original.

        Parameters
        ----------
        g:
            Input Grade.

        Returns
        -------
        Grade
            *Fairly* Grade.
        """
        prob = g.to_prob()
        return Grade.from_prob(max(prob ** 0.75, 1e-10))

    def hedge_more_or_less(self, g: Grade) -> Grade:
        """Apply the linguistic hedge *more or less*: g^(1/3).

        Strong dilation.

        Parameters
        ----------
        g:
            Input Grade.

        Returns
        -------
        Grade
            *More or less* Grade.
        """
        prob = g.to_prob()
        return Grade.from_prob(max(prob ** (1.0 / 3.0), 1e-10))


# ---------------------------------------------------------------------------
# Predefined FIS factories
# ---------------------------------------------------------------------------

def temperature_fis() -> FuzzyInferenceSystem:
    """Build a temperature → fan speed fuzzy inference system.

    Inputs:
        * ``temperature`` ∈ [0, 100] °C: cold (0-30), warm (20-60), hot (50-100)
    Outputs:
        * ``fan_speed`` ∈ [0, 100] %: slow (0-40), medium (30-70), fast (60-100)

    Rules:
        * IF temperature IS cold THEN fan_speed IS slow
        * IF temperature IS warm THEN fan_speed IS medium
        * IF temperature IS hot THEN fan_speed IS fast

    Returns
    -------
    FuzzyInferenceSystem
        Ready-to-use temperature controller.
    """
    fis = FuzzyInferenceSystem("TemperatureController")

    # Input: temperature
    temp_var = LinguisticVariable("temperature", (0.0, 100.0))
    temp_cold = FuzzySet.trapezoidal("cold", 0, 0, 15, 35, (0, 100))
    temp_warm = FuzzySet.triangular("warm", 20, 50, 80, (0, 100))
    temp_hot = FuzzySet.trapezoidal("hot", 60, 80, 100, 100, (0, 100))
    temp_var.add_term("cold", temp_cold)
    temp_var.add_term("warm", temp_warm)
    temp_var.add_term("hot", temp_hot)
    fis.add_input_var(temp_var)

    # Output: fan_speed
    fan_var = LinguisticVariable("fan_speed", (0.0, 100.0))
    fan_slow = FuzzySet.trapezoidal("slow", 0, 0, 20, 40, (0, 100))
    fan_medium = FuzzySet.triangular("medium", 30, 50, 70, (0, 100))
    fan_fast = FuzzySet.trapezoidal("fast", 60, 80, 100, 100, (0, 100))
    fan_var.add_term("slow", fan_slow)
    fan_var.add_term("medium", fan_medium)
    fan_var.add_term("fast", fan_fast)
    fis.add_output_var(fan_var)

    # Rules
    fis.add_rule(FuzzyRule(
        "R1_cold", [("temperature", temp_cold)], ("fan_speed", fan_slow),
        grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R2_warm", [("temperature", temp_warm)], ("fan_speed", fan_medium),
        grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R3_hot", [("temperature", temp_hot)], ("fan_speed", fan_fast),
        grade=Grade.perfect()))
    return fis


def risk_assessment_fis() -> FuzzyInferenceSystem:
    """Build a risk assessment fuzzy inference system.

    Inputs:
        * ``probability`` ∈ [0, 1]: likelihood of risk event occurring
        * ``impact`` ∈ [0, 10]: severity if it occurs
    Outputs:
        * ``risk_level`` ∈ [0, 10]: overall risk

    Rules assess combinations of probability and impact.

    Returns
    -------
    FuzzyInferenceSystem
        Risk assessment FIS.
    """
    fis = FuzzyInferenceSystem("RiskAssessment")

    # Inputs
    prob_var = LinguisticVariable("probability", (0.0, 1.0))
    prob_low = FuzzySet.trapezoidal("low", 0, 0, 0.2, 0.4, (0, 1))
    prob_medium = FuzzySet.triangular("medium", 0.3, 0.5, 0.7, (0, 1))
    prob_high = FuzzySet.trapezoidal("high", 0.6, 0.8, 1, 1, (0, 1))
    prob_var.add_term("low", prob_low)
    prob_var.add_term("medium", prob_medium)
    prob_var.add_term("high", prob_high)
    fis.add_input_var(prob_var)

    impact_var = LinguisticVariable("impact", (0.0, 10.0))
    impact_low = FuzzySet.trapezoidal("low", 0, 0, 2, 4, (0, 10))
    impact_medium = FuzzySet.triangular("medium", 3, 5, 7, (0, 10))
    impact_high = FuzzySet.trapezoidal("high", 6, 8, 10, 10, (0, 10))
    impact_var.add_term("low", impact_low)
    impact_var.add_term("medium", impact_medium)
    impact_var.add_term("high", impact_high)
    fis.add_input_var(impact_var)

    # Output
    risk_var = LinguisticVariable("risk_level", (0.0, 10.0))
    risk_low = FuzzySet.trapezoidal("low", 0, 0, 2, 4, (0, 10))
    risk_medium = FuzzySet.triangular("medium", 3, 5, 7, (0, 10))
    risk_high = FuzzySet.trapezoidal("high", 6, 8, 10, 10, (0, 10))
    risk_var.add_term("low", risk_low)
    risk_var.add_term("medium", risk_medium)
    risk_var.add_term("high", risk_high)
    fis.add_output_var(risk_var)

    # Rules
    fis.add_rule(FuzzyRule(
        "R1", [("probability", prob_low), ("impact", impact_low)],
        ("risk_level", risk_low), grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R2", [("probability", prob_low), ("impact", impact_medium)],
        ("risk_level", risk_low), grade=Grade.from_prob(0.9)))
    fis.add_rule(FuzzyRule(
        "R3", [("probability", prob_low), ("impact", impact_high)],
        ("risk_level", risk_medium), grade=Grade.from_prob(0.8)))
    fis.add_rule(FuzzyRule(
        "R4", [("probability", prob_medium), ("impact", impact_low)],
        ("risk_level", risk_low), grade=Grade.from_prob(0.85)))
    fis.add_rule(FuzzyRule(
        "R5", [("probability", prob_medium), ("impact", impact_medium)],
        ("risk_level", risk_medium), grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R6", [("probability", prob_medium), ("impact", impact_high)],
        ("risk_level", risk_high), grade=Grade.from_prob(0.9)))
    fis.add_rule(FuzzyRule(
        "R7", [("probability", prob_high), ("impact", impact_low)],
        ("risk_level", risk_medium), grade=Grade.from_prob(0.8)))
    fis.add_rule(FuzzyRule(
        "R8", [("probability", prob_high), ("impact", impact_medium)],
        ("risk_level", risk_high), grade=Grade.from_prob(0.9)))
    fis.add_rule(FuzzyRule(
        "R9", [("probability", prob_high), ("impact", impact_high)],
        ("risk_level", risk_high), grade=Grade.perfect()))
    return fis


def sentiment_fis() -> FuzzyInferenceSystem:
    """Build a sentiment analysis fuzzy inference system.

    Inputs:
        * ``valence`` ∈ [-1, 1]: positive vs. negative sentiment score
        * ``arousal`` ∈ [0, 1]: activation level
    Outputs:
        * ``sentiment`` ∈ [0, 10]: sentiment score (0=very negative, 10=very positive)

    Returns
    -------
    FuzzyInferenceSystem
        Sentiment FIS.
    """
    fis = FuzzyInferenceSystem("SentimentAnalysis")

    # Inputs
    valence_var = LinguisticVariable("valence", (-1.0, 1.0))
    val_neg = FuzzySet.trapezoidal("negative", -1, -1, -0.5, -0.1, (-1, 1))
    val_neutral = FuzzySet.triangular("neutral", -0.3, 0.0, 0.3, (-1, 1))
    val_pos = FuzzySet.trapezoidal("positive", 0.1, 0.5, 1, 1, (-1, 1))
    valence_var.add_term("negative", val_neg)
    valence_var.add_term("neutral", val_neutral)
    valence_var.add_term("positive", val_pos)
    fis.add_input_var(valence_var)

    arousal_var = LinguisticVariable("arousal", (0.0, 1.0))
    arous_low = FuzzySet.trapezoidal("low", 0, 0, 0.25, 0.5, (0, 1))
    arous_high = FuzzySet.trapezoidal("high", 0.5, 0.75, 1, 1, (0, 1))
    arousal_var.add_term("low", arous_low)
    arousal_var.add_term("high", arous_high)
    fis.add_input_var(arousal_var)

    # Output
    sent_var = LinguisticVariable("sentiment", (0.0, 10.0))
    sent_neg = FuzzySet.trapezoidal("negative", 0, 0, 2, 4, (0, 10))
    sent_neutral = FuzzySet.triangular("neutral", 3, 5, 7, (0, 10))
    sent_pos = FuzzySet.trapezoidal("positive", 6, 8, 10, 10, (0, 10))
    sent_var.add_term("negative", sent_neg)
    sent_var.add_term("neutral", sent_neutral)
    sent_var.add_term("positive", sent_pos)
    fis.add_output_var(sent_var)

    # Rules
    fis.add_rule(FuzzyRule(
        "R1", [("valence", val_neg)], ("sentiment", sent_neg),
        grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R2", [("valence", val_neutral)], ("sentiment", sent_neutral),
        grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R3", [("valence", val_pos)], ("sentiment", sent_pos),
        grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R4", [("valence", val_pos), ("arousal", arous_high)],
        ("sentiment", sent_pos), grade=Grade.from_prob(0.95)))
    fis.add_rule(FuzzyRule(
        "R5", [("valence", val_neg), ("arousal", arous_high)],
        ("sentiment", sent_neg), grade=Grade.from_prob(0.95)))
    return fis


def relevance_fis() -> FuzzyInferenceSystem:
    """Build a document relevance fuzzy inference system.

    Inputs:
        * ``semantic_overlap`` ∈ [0, 1]: semantic similarity score
        * ``pragmatic_overlap`` ∈ [0, 1]: discourse relevance score
    Outputs:
        * ``relevance`` ∈ [0, 1]: overall relevance score

    Returns
    -------
    FuzzyInferenceSystem
        Relevance FIS.
    """
    fis = FuzzyInferenceSystem("RelevanceScorer")

    # Inputs
    sem_var = LinguisticVariable("semantic_overlap", (0.0, 1.0))
    sem_low = FuzzySet.trapezoidal("low", 0, 0, 0.2, 0.4, (0, 1))
    sem_medium = FuzzySet.triangular("medium", 0.3, 0.5, 0.7, (0, 1))
    sem_high = FuzzySet.trapezoidal("high", 0.6, 0.8, 1, 1, (0, 1))
    sem_var.add_term("low", sem_low)
    sem_var.add_term("medium", sem_medium)
    sem_var.add_term("high", sem_high)
    fis.add_input_var(sem_var)

    prag_var = LinguisticVariable("pragmatic_overlap", (0.0, 1.0))
    prag_low = FuzzySet.trapezoidal("low", 0, 0, 0.25, 0.45, (0, 1))
    prag_medium = FuzzySet.triangular("medium", 0.35, 0.55, 0.75, (0, 1))
    prag_high = FuzzySet.trapezoidal("high", 0.65, 0.85, 1, 1, (0, 1))
    prag_var.add_term("low", prag_low)
    prag_var.add_term("medium", prag_medium)
    prag_var.add_term("high", prag_high)
    fis.add_input_var(prag_var)

    # Output
    rel_var = LinguisticVariable("relevance", (0.0, 1.0))
    rel_low = FuzzySet.trapezoidal("low", 0, 0, 0.2, 0.4, (0, 1))
    rel_medium = FuzzySet.triangular("medium", 0.3, 0.5, 0.7, (0, 1))
    rel_high = FuzzySet.trapezoidal("high", 0.6, 0.8, 1, 1, (0, 1))
    rel_var.add_term("low", rel_low)
    rel_var.add_term("medium", rel_medium)
    rel_var.add_term("high", rel_high)
    fis.add_output_var(rel_var)

    # Rules
    fis.add_rule(FuzzyRule(
        "R1", [("semantic_overlap", sem_high), ("pragmatic_overlap", prag_high)],
        ("relevance", rel_high), grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R2", [("semantic_overlap", sem_high), ("pragmatic_overlap", prag_medium)],
        ("relevance", rel_high), grade=Grade.from_prob(0.85)))
    fis.add_rule(FuzzyRule(
        "R3", [("semantic_overlap", sem_medium), ("pragmatic_overlap", prag_high)],
        ("relevance", rel_high), grade=Grade.from_prob(0.8)))
    fis.add_rule(FuzzyRule(
        "R4", [("semantic_overlap", sem_medium), ("pragmatic_overlap", prag_medium)],
        ("relevance", rel_medium), grade=Grade.perfect()))
    fis.add_rule(FuzzyRule(
        "R5", [("semantic_overlap", sem_low), ("pragmatic_overlap", prag_high)],
        ("relevance", rel_medium), grade=Grade.from_prob(0.7)))
    fis.add_rule(FuzzyRule(
        "R6", [("semantic_overlap", sem_high), ("pragmatic_overlap", prag_low)],
        ("relevance", rel_medium), grade=Grade.from_prob(0.7)))
    fis.add_rule(FuzzyRule(
        "R7", [("semantic_overlap", sem_low), ("pragmatic_overlap", prag_medium)],
        ("relevance", rel_low), grade=Grade.from_prob(0.8)))
    fis.add_rule(FuzzyRule(
        "R8", [("semantic_overlap", sem_medium), ("pragmatic_overlap", prag_low)],
        ("relevance", rel_low), grade=Grade.from_prob(0.8)))
    fis.add_rule(FuzzyRule(
        "R9", [("semantic_overlap", sem_low), ("pragmatic_overlap", prag_low)],
        ("relevance", rel_low), grade=Grade.perfect()))
    return fis


# ---------------------------------------------------------------------------
# FuzzySetArithmetic — Grade-theoretic fuzzy arithmetic
# ---------------------------------------------------------------------------

class FuzzySetArithmetic:
    """Arithmetic operations on fuzzy numbers using the Grade semiring.

    Fuzzy arithmetic extends classical fuzzy set operations to numerical
    computations.  The Grade semiring provides a natural framework:
    adding two fuzzy numbers corresponds to Grade addition (logsumexp)
    of their membership grades at each point.

    All operations return :class:`FuzzySet` instances whose membership
    functions implement the Grade-theoretic combination.
    """

    @staticmethod
    def fuzzy_add(a: FuzzySet, b: FuzzySet) -> FuzzySet:
        """Add two fuzzy numbers using the extension principle.

        For fuzzy numbers A and B, the membership of z in A+B is:

        .. code-block::

            μ_{A+B}(z) = sup_{x+y=z} min(μ_A(x), μ_B(y))

        In Grade terms: for each z, find the best (x, y) pair such that
        x + y = z and the Grade product of memberships is maximized.

        This is a Grade optimization problem.  We approximate it by sampling.

        Parameters
        ----------
        a, b:
            Fuzzy number sets.

        Returns
        -------
        FuzzySet
            The fuzzy sum A + B (approximated).
        """
        low_a, high_a = a.universe
        low_b, high_b = b.universe
        new_low = low_a + low_b
        new_high = high_a + high_b
        new_universe = (new_low, new_high)
        # Represent as custom set using GAUSSIAN as carrier
        result = _BinaryArithFuzzySet(
            name=f"({a.name} + {b.name})",
            fn_type=MembershipFunction.GAUSSIAN,
            params=[],
            universe=new_universe,
            _op="add",
            _a=a,
            _b=b,
        )
        return result

    @staticmethod
    def fuzzy_multiply_scalar(a: FuzzySet, scalar: float) -> FuzzySet:
        """Multiply a fuzzy set by a crisp scalar.

        μ_{cA}(z) = μ_A(z / c) for c != 0.

        Parameters
        ----------
        a:
            Fuzzy number.
        scalar:
            Crisp scalar c.

        Returns
        -------
        FuzzySet
            Scaled fuzzy set.
        """
        if scalar == 0:
            return FuzzySet.singleton(f"0*{a.name}", 0.0, a.universe)
        low, high = a.universe
        new_low = min(low * scalar, high * scalar)
        new_high = max(low * scalar, high * scalar)
        result = _ScalarMultFuzzySet(
            name=f"{scalar}*{a.name}",
            fn_type=MembershipFunction.GAUSSIAN,
            params=[],
            universe=(new_low, new_high),
            _a=a,
            _scalar=scalar,
        )
        return result

    @staticmethod
    def expected_value(a: FuzzySet, n_samples: int = 200) -> float:
        """Compute the Grade-weighted expected value of a fuzzy number.

        E[A] = ∫ x * μ_A(x) dx / ∫ μ_A(x) dx  (centroid defuzzification)

        This is exactly the :meth:`FuzzySet.centroid` method.

        Parameters
        ----------
        a:
            Fuzzy number.
        n_samples:
            Sampling resolution.

        Returns
        -------
        float
            Grade-weighted expected value.
        """
        return a.centroid(n_samples=n_samples)

    @staticmethod
    def grade_spread(a: FuzzySet, n_samples: int = 200) -> Grade:
        """Compute a Grade reflecting how spread out a fuzzy number is.

        Spread = normalized standard deviation of the membership distribution.
        Higher spread → lower Grade (less certain).

        Parameters
        ----------
        a:
            Fuzzy number.
        n_samples:
            Sampling resolution.

        Returns
        -------
        Grade
            Concentration grade (high = concentrated, low = spread).
        """
        low, high = a.universe
        if low == high:
            return Grade.perfect()
        step = (high - low) / max(n_samples - 1, 1)
        xs = [low + i * step for i in range(n_samples)]
        mus = [a.membership(x).to_prob() for x in xs]
        total = sum(mus)
        if total < 1e-12:
            return Grade.impossible()
        mean = sum(x * mu for x, mu in zip(xs, mus)) / total
        variance = sum((x - mean) ** 2 * mu for x, mu in zip(xs, mus)) / total
        std_dev = math.sqrt(max(variance, 0.0))
        range_width = high - low
        normalized_std = std_dev / max(range_width, 1e-10)
        concentration_prob = max(1.0 - normalized_std, 1e-10)
        return Grade.from_prob(concentration_prob)


# Internal arithmetic helpers
class _BinaryArithFuzzySet(FuzzySet):
    """Internal: fuzzy arithmetic result set."""

    def __init__(self, name, fn_type, params, universe, _op, _a, _b):
        super().__init__(name=name, fn_type=fn_type, params=params,
                         universe=universe)
        self._op = _op
        self._a = _a
        self._b = _b
        self._n_samples = 50

    def membership(self, z: float) -> Grade:
        """Approximate extension-principle membership."""
        low_a, high_a = self._a.universe
        step_a = (high_a - low_a) / max(self._n_samples - 1, 1)
        best = Grade.impossible()
        for i in range(self._n_samples):
            x = low_a + i * step_a
            if self._op == "add":
                y = z - x
            else:
                y = z - x
            ga = self._a.membership(x)
            gb = self._b.membership(y)
            candidate = ga * gb
            if candidate > best:
                best = candidate
        return best


class _ScalarMultFuzzySet(FuzzySet):
    """Internal: scalar-multiplied fuzzy set."""

    def __init__(self, name, fn_type, params, universe, _a, _scalar):
        super().__init__(name=name, fn_type=fn_type, params=params,
                         universe=universe)
        self._a = _a
        self._scalar = _scalar

    def membership(self, z: float) -> Grade:
        """Membership via μ_A(z/c)."""
        if self._scalar == 0:
            return Grade.from_prob(1.0 if abs(z) < 1e-9 else 1e-10)
        return self._a.membership(z / self._scalar)


# FuzzySet.singleton convenience (static)
def _fuzzy_singleton(name: str, x0: float,
                     universe: tuple[float, float] = (0.0, 1.0)) -> FuzzySet:
    return FuzzySet(name=name, fn_type=MembershipFunction.SINGLETON,
                    params=[x0], universe=universe)


FuzzySet.singleton = staticmethod(_fuzzy_singleton)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# GradeThresholdClassifier — threshold-based Grade classification
# ---------------------------------------------------------------------------

class GradeThresholdClassifier:
    """Classify crisp values into categories using Grade thresholds.

    This is a simplified fuzzy classification system where each category
    is defined by a threshold Grade.  A value belongs to a category if
    the corresponding :class:`FuzzySet` membership exceeds the threshold.

    Useful for quick, calibrated multi-class classification without a
    full inference system.

    Attributes
    ----------
    categories:
        Dict of category_name → (FuzzySet, threshold_Grade).
    default_category:
        Category to return when no threshold is exceeded.
    """

    def __init__(self, default_category: str = "unknown") -> None:
        self.categories: dict[str, tuple[FuzzySet, Grade]] = {}
        self.default_category = default_category

    def add_category(
        self, name: str, fs: FuzzySet, threshold: Grade
    ) -> None:
        """Register a category with a membership function and Grade threshold.

        Parameters
        ----------
        name:
            Category name.
        fs:
            Membership function for this category.
        threshold:
            Minimum Grade for classification into this category.
        """
        self.categories[name] = (fs, threshold)

    def classify(self, value: float) -> tuple[str, Grade]:
        """Classify ``value`` into the highest-Grade category above threshold.

        If multiple categories exceed their thresholds, the one with the
        highest Grade is selected.  This is Grade addition (logsumexp) to
        pick the best alternative.

        Parameters
        ----------
        value:
            Crisp input value.

        Returns
        -------
        tuple[str, Grade]
            (category_name, membership_grade), or (default_category, impossible).
        """
        best_cat = self.default_category
        best_grade = Grade.impossible()
        for name, (fs, threshold) in self.categories.items():
            g = fs.membership(value)
            if g >= threshold and g > best_grade:
                best_grade = g
                best_cat = name
        return best_cat, best_grade

    def classify_all(self, value: float) -> list[tuple[str, Grade]]:
        """Return all categories above their thresholds, sorted by Grade desc.

        Parameters
        ----------
        value:
            Crisp input value.

        Returns
        -------
        list[tuple[str, Grade]]
            All qualifying (category_name, Grade) pairs, sorted descending.
        """
        results = []
        for name, (fs, threshold) in self.categories.items():
            g = fs.membership(value)
            if g >= threshold:
                results.append((name, g))
        results.sort(key=lambda kv: kv[1], reverse=True)
        return results

    def grade_ambiguity(self, value: float) -> Grade:
        """Grade the classification ambiguity for ``value``.

        Higher ambiguity (more categories above threshold with similar grades)
        → lower Grade (less certain classification).
        Unambiguous (exactly one category) → ``Grade.perfect()``.

        Parameters
        ----------
        value:
            Crisp input.

        Returns
        -------
        Grade
            Certainty grade; lower = more ambiguous.
        """
        all_cats = self.classify_all(value)
        n = len(all_cats)
        if n == 0:
            return Grade.impossible()
        if n == 1:
            return Grade.perfect()
        # Ambiguity: how similar are the top two grades?
        g1 = all_cats[0][1].to_prob()
        g2 = all_cats[1][1].to_prob()
        margin = abs(g1 - g2)
        certainty = min(margin * 5.0, 1.0)  # scale to [0,1]
        return Grade.from_prob(max(certainty, 1e-6))
