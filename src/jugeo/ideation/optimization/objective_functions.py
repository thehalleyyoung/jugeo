"""Concrete objective function implementations for JuGeo ideation optimization (Ch50).

Each objective evaluates a single quality criterion on an IdeaProposal,
enabling multi-objective optimization of mathematical research ideas.

Objectives cover novelty (token-based linguistic diversity), feasibility
(inverse payoff saturation), purpose alignment (keyword matching), research
yield (direct payoff scaling), and cost estimation (payoff × factor).

A :class:`CompositeObjective` aggregates several objectives with individual
weights, and :class:`ObjectiveFactory` provides a convenient creation API.
The :class:`ObjectiveEvaluator` orchestrates evaluation of an entire idea pool.
"""
from __future__ import annotations

import logging
import math
import statistics
import string
from typing import Any

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:
    IdeaProposal = Any  # type: ignore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Module-level helper functions
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Split *text* into a set of lowercase tokens with punctuation stripped.

    Tokens shorter than two characters are discarded to reduce noise.

    Parameters
    ----------
    text:
        Raw string to tokenise.

    Returns
    -------
    set[str]
        De-duplicated lowercase tokens.
    """
    translator = str.maketrans("", "", string.punctuation)
    cleaned = text.translate(translator).lower()
    return {tok for tok in cleaned.split() if len(tok) >= 2}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *v* clamped to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        Value to clamp.
    lo:
        Lower bound (default 0.0).
    hi:
        Upper bound (default 1.0).

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, v))


def _jaccard(a: set, b: set) -> float:
    """Compute the Jaccard similarity between two sets.

    J(A,B) = |A ∩ B| / |A ∪ B|.  Returns 0.0 when both sets are empty.

    Parameters
    ----------
    a:
        First set.
    b:
        Second set.

    Returns
    -------
    float
        Jaccard similarity in [0, 1].
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _normalize(scores: list[float]) -> list[float]:
    """Min-max normalise *scores* to [0, 1].

    If all values are equal the function returns a list of 0.5 values to
    avoid division by zero.

    Parameters
    ----------
    scores:
        Input list of raw numeric scores.

    Returns
    -------
    list[float]
        Normalised scores of the same length.
    """
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    span = hi - lo
    if span == 0.0:
        return [0.5] * len(scores)
    return [(s - lo) / span for s in scores]


def _keyword_count(text: str, keywords: set[str]) -> int:
    """Count how many *keywords* appear in the tokenised *text*.

    Parameters
    ----------
    text:
        Input string to scan.
    keywords:
        Set of target keywords (already lowercase).

    Returns
    -------
    int
        Number of matching keywords found in the token set of *text*.
    """
    tokens = _tokenize(text)
    return sum(1 for kw in keywords if kw in tokens)


def _safe_mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or 0.0 when the list is empty.

    Parameters
    ----------
    values:
        Numeric values to average.

    Returns
    -------
    float
        Mean value, or 0.0 on empty input.
    """
    if not values:
        return 0.0
    return statistics.mean(values)


# ---------------------------------------------------------------------------
# 2. Base objective
# ---------------------------------------------------------------------------

class BaseObjective:
    """Abstract-style base class for a single optimisation objective.

    Subclasses must override :meth:`evaluate` to return a score in [0, 1]
    where higher values are *always* considered better by convention (the
    Pareto engine knows about objective directions separately).

    Attributes
    ----------
    name:
        Short unique identifier for this objective.
    weight:
        Relative importance weight used in scalarisation.
    """

    def __init__(self, name: str = "base", weight: float = 1.0) -> None:
        """Initialise base objective.

        Parameters
        ----------
        name:
            Human-readable objective identifier.
        weight:
            Non-negative importance multiplier.
        """
        if weight < 0:
            raise ValueError(f"weight must be non-negative, got {weight!r}")
        self.name: str = name
        self.weight: float = weight
        _log.debug("Created objective %r with weight=%.3f", name, weight)

    # ------------------------------------------------------------------
    def evaluate(self, idea: Any) -> float:
        """Evaluate *idea* and return a score in [0, 1].

        The base implementation always returns 0.5.  Override in subclasses.

        Parameters
        ----------
        idea:
            An :class:`IdeaProposal` or compatible object exposing at least
            ``title``, ``hypothesis``, and ``payoff`` attributes.

        Returns
        -------
        float
            Score in [0, 1]; higher is better.
        """
        return 0.5

    def description(self) -> str:
        """Return a human-readable description of what this objective measures.

        Returns
        -------
        str
            Plain-English description string.
        """
        return f"BaseObjective '{self.name}': always returns 0.5 (stub)."

    def weighted_evaluate(self, idea: Any) -> float:
        """Return :meth:`evaluate` multiplied by :attr:`weight`.

        Parameters
        ----------
        idea:
            Idea to evaluate.

        Returns
        -------
        float
            Weighted score (may exceed [0, 1] if weight > 1).
        """
        return self.evaluate(idea) * self.weight

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, weight={self.weight:.3f})"


# ---------------------------------------------------------------------------
# 3. Novelty objective
# ---------------------------------------------------------------------------

class NoveltyObjective(BaseObjective):
    """Measures the linguistic novelty of an idea based on token density.

    A title with many distinct tokens and a long hypothesis both contribute
    positively.  The ``token_diversity`` helper captures how *different*
    the title and hypothesis vocabularies are (inverse Jaccard).

    Attributes
    ----------
    min_tokens:
        Minimum token count before any novelty credit is awarded.
    """

    def __init__(self, weight: float = 1.0, min_tokens: int = 3) -> None:
        """Initialise NoveltyObjective.

        Parameters
        ----------
        weight:
            Importance multiplier.
        min_tokens:
            Minimum token threshold; ideas with fewer title tokens score 0.
        """
        super().__init__(name="novelty", weight=weight)
        self.min_tokens: int = min_tokens

    # ------------------------------------------------------------------
    def evaluate(self, idea: Any) -> float:
        """Score the novelty of *idea* in [0, 1].

        Formula:
            score = clamp(n_title * 0.07 + n_hypothesis * 0.03)

        where *n_title* and *n_hypothesis* are token counts of the
        respective fields.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``title`` and ``hypothesis`` fields.

        Returns
        -------
        float
            Novelty score in [0, 1].
        """
        title_tokens = _tokenize(idea.title)
        hypothesis_tokens = _tokenize(idea.hypothesis)
        token_count_title = len(title_tokens)
        raw = token_count_title * 0.07 + len(hypothesis_tokens) * 0.03
        score = _clamp(raw)
        _log.debug(
            "NoveltyObjective: title_tokens=%d hyp_tokens=%d raw=%.4f score=%.4f",
            token_count_title,
            len(hypothesis_tokens),
            raw,
            score,
        )
        return score

    def description(self) -> str:
        """Return description string for NoveltyObjective."""
        return (
            "NoveltyObjective: scores linguistic novelty using token counts "
            "from the idea's title and hypothesis.  Higher token density "
            "implies more novel / expressive content."
        )

    def token_diversity(self, idea: Any) -> float:
        """Return inverse Jaccard between title and hypothesis tokens.

        A higher value indicates the two fields use very different
        vocabularies, suggesting richer conceptual content.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``title`` and ``hypothesis``.

        Returns
        -------
        float
            Token diversity in [0, 1] (1 = completely disjoint).
        """
        title_tokens = _tokenize(idea.title)
        hyp_tokens = _tokenize(idea.hypothesis)
        return _clamp(1.0 - _jaccard(title_tokens, hyp_tokens))


# ---------------------------------------------------------------------------
# 4. Feasibility objective
# ---------------------------------------------------------------------------

class FeasibilityObjective(BaseObjective):
    """Measures how feasible (low-cost) an idea is relative to its payoff.

    The scoring function is a decreasing sigmoid-like curve: ideas with
    low payoff score close to 1.0, while high-payoff ideas score lower
    because they are harder to execute.

    Attributes
    ----------
    saturation:
        Reference value used in the saturation helper.
    """

    def __init__(self, weight: float = 1.0, saturation: float = 10.0) -> None:
        """Initialise FeasibilityObjective.

        Parameters
        ----------
        weight:
            Importance multiplier.
        saturation:
            Denominator used by :meth:`saturation_point`.
        """
        super().__init__(name="feasibility", weight=weight)
        self.saturation: float = saturation

    # ------------------------------------------------------------------
    def evaluate(self, idea: Any) -> float:
        """Score feasibility of *idea* via an inverse payoff transform.

        Formula: ``1.0 / (1.0 + idea.payoff * 0.1)``, clamped to [0, 1].

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``payoff`` field.

        Returns
        -------
        float
            Feasibility score in [0, 1]; higher means easier to execute.
        """
        raw = 1.0 / (1.0 + idea.payoff * 0.1)
        score = _clamp(raw)
        _log.debug(
            "FeasibilityObjective: payoff=%d raw=%.4f score=%.4f",
            idea.payoff,
            raw,
            score,
        )
        return score

    def description(self) -> str:
        """Return description string for FeasibilityObjective."""
        return (
            "FeasibilityObjective: higher scores mean the idea is easier to "
            "execute.  Uses an inverse proportional transform of idea.payoff."
        )

    def saturation_point(self, idea: Any) -> float:
        """Return the fraction of *saturation* consumed by *idea*'s payoff.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``payoff`` field.

        Returns
        -------
        float
            Clamped ratio ``idea.payoff / self.saturation``.
        """
        return _clamp(idea.payoff / self.saturation)


# ---------------------------------------------------------------------------
# 5. Purpose objective
# ---------------------------------------------------------------------------

class PurposeObjective(BaseObjective):
    """Measures alignment with core mathematical research purposes.

    A set of purpose keywords is matched against tokens in the idea's
    title and hypothesis; a higher overlap fraction yields a higher score.

    Attributes
    ----------
    purpose_keywords:
        Set of domain keywords; defaults to a standard mathematical set.
    """

    _DEFAULT_KEYWORDS: frozenset[str] = frozenset({
        "theorem", "proof", "conjecture", "lemma", "optimal",
        "structure", "geometry", "algebra", "analysis", "topology",
    })

    def __init__(
        self,
        weight: float = 1.0,
        purpose_keywords: set[str] | None = None,
    ) -> None:
        """Initialise PurposeObjective.

        Parameters
        ----------
        weight:
            Importance multiplier.
        purpose_keywords:
            Custom keyword set; if ``None`` uses the 10-element default.
        """
        super().__init__(name="purpose", weight=weight)
        self.purpose_keywords: set[str] = (
            set(purpose_keywords) if purpose_keywords is not None
            else set(self._DEFAULT_KEYWORDS)
        )

    # ------------------------------------------------------------------
    def evaluate(self, idea: Any) -> float:
        """Score purpose alignment of *idea*.

        Counts keyword matches in the combined token set of
        ``idea.title + idea.hypothesis``, then applies:

            score = clamp(matches / max(1, len(keywords)) * 2
                          + (len(idea.hypothesis) % 10) * 0.02)

        The small ``hypothesis`` length modulus term introduces fine-grained
        differentiation between ideas with identical keyword counts.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``title``, ``hypothesis``.

        Returns
        -------
        float
            Purpose score in [0, 1].
        """
        combined = idea.title + " " + idea.hypothesis
        matches = _keyword_count(combined, self.purpose_keywords)
        base = matches / max(1, len(self.purpose_keywords)) * 2
        bonus = (len(idea.hypothesis) % 10) * 0.02
        score = _clamp(base + bonus)
        _log.debug(
            "PurposeObjective: matches=%d base=%.4f bonus=%.4f score=%.4f",
            matches,
            base,
            bonus,
            score,
        )
        return score

    def description(self) -> str:
        """Return description string for PurposeObjective."""
        kws = ", ".join(sorted(self.purpose_keywords))
        return (
            f"PurposeObjective: measures keyword alignment with mathematical "
            f"research purposes.  Keywords: {kws}."
        )

    def keyword_overlap(self, idea: Any) -> set[str]:
        """Return the set of purpose keywords found in *idea*.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``title`` and ``hypothesis``.

        Returns
        -------
        set[str]
            Subset of :attr:`purpose_keywords` present in the idea.
        """
        combined_tokens = _tokenize(idea.title + " " + idea.hypothesis)
        return self.purpose_keywords & combined_tokens


# ---------------------------------------------------------------------------
# 6. Yield objective
# ---------------------------------------------------------------------------

class YieldObjective(BaseObjective):
    """Measures the raw research yield (payoff) of an idea.

    Higher payoff ideas score higher.  The score is linearly capped at
    ``max_yield``.

    Attributes
    ----------
    max_yield:
        Payoff value that maps to a score of 1.0.
    """

    def __init__(self, weight: float = 1.0, max_yield: float = 20.0) -> None:
        """Initialise YieldObjective.

        Parameters
        ----------
        weight:
            Importance multiplier.
        max_yield:
            Upper reference for normalisation.
        """
        super().__init__(name="yield", weight=weight)
        self.max_yield: float = max_yield

    # ------------------------------------------------------------------
    def evaluate(self, idea: Any) -> float:
        """Score yield of *idea* as ``min(1.0, idea.payoff / max_yield)``.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``payoff`` field.

        Returns
        -------
        float
            Yield score in [0, 1].
        """
        score = min(1.0, idea.payoff / self.max_yield)
        _log.debug(
            "YieldObjective: payoff=%d max_yield=%.1f score=%.4f",
            idea.payoff,
            self.max_yield,
            score,
        )
        return score

    def description(self) -> str:
        """Return description string for YieldObjective."""
        return (
            f"YieldObjective: linearly maps idea.payoff to [0, 1] using "
            f"max_yield={self.max_yield}."
        )

    def raw_yield(self, idea: Any) -> float:
        """Return the raw (uncapped) payoff as a float.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``payoff`` field.

        Returns
        -------
        float
            ``float(idea.payoff)``.
        """
        return float(idea.payoff)


# ---------------------------------------------------------------------------
# 7. Cost objective
# ---------------------------------------------------------------------------

class CostObjective(BaseObjective):
    """Estimates the execution cost of an idea proportional to its payoff.

    A cost factor converts payoff units to a fractional cost score.
    Counter-intuitively this scores *higher* for higher-payoff ideas,
    which lets the Pareto engine balance cost against yield.

    Attributes
    ----------
    cost_factor:
        Multiplier applied to ``idea.payoff`` before clamping.
    """

    def __init__(self, weight: float = 1.0, cost_factor: float = 0.08) -> None:
        """Initialise CostObjective.

        Parameters
        ----------
        weight:
            Importance multiplier.
        cost_factor:
            Conversion factor from payoff units to cost score.
        """
        super().__init__(name="cost", weight=weight)
        self.cost_factor: float = cost_factor

    # ------------------------------------------------------------------
    def evaluate(self, idea: Any) -> float:
        """Score cost of *idea* as ``min(1.0, idea.payoff * cost_factor)``.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``payoff`` field.

        Returns
        -------
        float
            Cost score in [0, 1].
        """
        score = min(1.0, idea.payoff * self.cost_factor)
        _log.debug(
            "CostObjective: payoff=%d factor=%.3f score=%.4f",
            idea.payoff,
            self.cost_factor,
            score,
        )
        return score

    def description(self) -> str:
        """Return description string for CostObjective."""
        return (
            f"CostObjective: proportional cost estimate using "
            f"cost_factor={self.cost_factor}.  Higher payoff → higher cost."
        )

    def estimated_cost(self, idea: Any) -> float:
        """Return the unclamped raw cost estimate.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` with ``payoff`` field.

        Returns
        -------
        float
            ``idea.payoff * self.cost_factor`` (no clamping).
        """
        return float(idea.payoff) * self.cost_factor


# ---------------------------------------------------------------------------
# 8. Composite objective
# ---------------------------------------------------------------------------

class CompositeObjective:
    """Aggregates multiple :class:`BaseObjective` instances into one score.

    Each component carries its own weight; the composite score is the
    weighted mean of component scores.  Weights can be normalised so they
    sum to 1.0 via :meth:`normalize_weights`.

    Attributes
    ----------
    name:
        Identifier for the composite objective.
    """

    def __init__(self, name: str = "composite") -> None:
        """Initialise an empty CompositeObjective.

        Parameters
        ----------
        name:
            Human-readable identifier.
        """
        self.name: str = name
        self._components: list[tuple[BaseObjective, float]] = []
        _log.debug("Created CompositeObjective %r", name)

    # ------------------------------------------------------------------
    def add(self, obj: BaseObjective, weight: float = 1.0) -> None:
        """Add a component objective with a given local weight override.

        Parameters
        ----------
        obj:
            The objective to include.
        weight:
            Local weight for this composite (overrides ``obj.weight``).
        """
        if weight < 0:
            raise ValueError(f"Component weight must be non-negative, got {weight!r}")
        self._components.append((obj, weight))
        _log.debug("CompositeObjective %r: added %r w=%.3f", self.name, obj.name, weight)

    def evaluate(self, idea: Any) -> float:
        """Return the normalised weighted sum of all component scores.

        If the total weight is zero (no components or all weights zero),
        returns 0.0.

        Parameters
        ----------
        idea:
            Idea to evaluate.

        Returns
        -------
        float
            Weighted mean score in [0, 1].
        """
        if not self._components:
            return 0.0
        total_weight = sum(w for _, w in self._components)
        if total_weight == 0.0:
            return 0.0
        weighted_sum = sum(obj.evaluate(idea) * w for obj, w in self._components)
        return _clamp(weighted_sum / total_weight)

    def normalize_weights(self) -> None:
        """Normalise all component weights so they sum to 1.0 in place.

        No-op if there are no components or total weight is already zero.
        """
        total = sum(w for _, w in self._components)
        if total == 0.0:
            return
        self._components = [(obj, w / total) for obj, w in self._components]
        _log.debug("CompositeObjective %r: normalised weights", self.name)

    def component_count(self) -> int:
        """Return the number of component objectives registered.

        Returns
        -------
        int
            Number of components.
        """
        return len(self._components)

    def description(self) -> str:
        """Return a multi-line description listing all components.

        Returns
        -------
        str
            Human-readable breakdown of the composite.
        """
        lines = [f"CompositeObjective '{self.name}' ({self.component_count()} components):"]
        for obj, w in self._components:
            lines.append(f"  [{obj.name}] weight={w:.3f}")
        return "\n".join(lines)

    def evaluate_breakdown(self, idea: Any) -> dict[str, float]:
        """Return a per-component score dictionary.

        Parameters
        ----------
        idea:
            Idea to evaluate.

        Returns
        -------
        dict[str, float]
            Mapping of objective name → raw (unweighted) score.
        """
        return {obj.name: obj.evaluate(idea) for obj, _ in self._components}

    def __repr__(self) -> str:
        return (
            f"CompositeObjective(name={self.name!r}, "
            f"components={self.component_count()})"
        )


# ---------------------------------------------------------------------------
# 9. Objective factory
# ---------------------------------------------------------------------------

class ObjectiveFactory:
    """Static factory for creating objective instances by name.

    Supports the names: ``"novelty"``, ``"feasibility"``, ``"purpose"``,
    ``"yield"``, ``"cost"``.
    """

    _REGISTRY: dict[str, type] = {
        "novelty": NoveltyObjective,
        "feasibility": FeasibilityObjective,
        "purpose": PurposeObjective,
        "yield": YieldObjective,
        "cost": CostObjective,
    }

    @classmethod
    def create(cls, name: str, weight: float = 1.0, **kwargs: Any) -> BaseObjective:
        """Construct an objective by *name* with optional keyword arguments.

        Parameters
        ----------
        name:
            One of ``"novelty"``, ``"feasibility"``, ``"purpose"``,
            ``"yield"``, ``"cost"``.
        weight:
            Importance multiplier forwarded to the constructor.
        **kwargs:
            Additional keyword arguments forwarded to the objective
            constructor (e.g., ``min_tokens``, ``saturation``).

        Returns
        -------
        BaseObjective
            Constructed objective instance.

        Raises
        ------
        ValueError
            If *name* is not in the registry.
        """
        key = name.lower()
        if key not in cls._REGISTRY:
            raise ValueError(
                f"Unknown objective name {name!r}. "
                f"Available: {list(cls._REGISTRY)}"
            )
        klass = cls._REGISTRY[key]
        obj = klass(weight=weight, **kwargs)
        _log.debug("ObjectiveFactory.create: %r → %r", name, obj)
        return obj

    @classmethod
    def create_standard_suite(
        cls,
        weights: dict[str, float] | None = None,
    ) -> list[BaseObjective]:
        """Create the standard five-objective suite with optional weight overrides.

        The standard suite is: novelty, feasibility, purpose, yield, cost.

        Parameters
        ----------
        weights:
            Optional mapping of ``{name: weight}`` overrides.  Unspecified
            objectives use a weight of 1.0.

        Returns
        -------
        list[BaseObjective]
            List of five configured objective instances.
        """
        w = weights or {}
        suite: list[BaseObjective] = [
            NoveltyObjective(weight=w.get("novelty", 1.0)),
            FeasibilityObjective(weight=w.get("feasibility", 1.0)),
            PurposeObjective(weight=w.get("purpose", 1.0)),
            YieldObjective(weight=w.get("yield", 1.0)),
            CostObjective(weight=w.get("cost", 1.0)),
        ]
        _log.debug("ObjectiveFactory: created standard suite of %d objectives", len(suite))
        return suite

    @classmethod
    def available_names(cls) -> list[str]:
        """Return a sorted list of all registered objective names.

        Returns
        -------
        list[str]
            Sorted objective names.
        """
        return sorted(cls._REGISTRY.keys())


# ---------------------------------------------------------------------------
# 10. Objective evaluator
# ---------------------------------------------------------------------------

class ObjectiveEvaluator:
    """Orchestrates evaluation of multiple objectives over a pool of ideas.

    Maintains an ordered list of :class:`BaseObjective` instances and
    provides bulk evaluation, ranking, and top-k selection helpers.

    Attributes
    ----------
    objectives:
        The active list of objectives.
    """

    def __init__(self, objectives: list[BaseObjective] | None = None) -> None:
        """Initialise ObjectiveEvaluator.

        Parameters
        ----------
        objectives:
            Pre-loaded list of objectives; empty list used if ``None``.
        """
        self.objectives: list[BaseObjective] = list(objectives or [])
        _log.debug(
            "ObjectiveEvaluator created with %d objectives", len(self.objectives)
        )

    # ------------------------------------------------------------------
    def add(self, obj: BaseObjective) -> None:
        """Append *obj* to the evaluator's objective list.

        Parameters
        ----------
        obj:
            Objective instance to register.
        """
        self.objectives.append(obj)
        _log.debug("ObjectiveEvaluator: added objective %r", obj.name)

    def evaluate_all(self, idea: Any) -> dict[str, float]:
        """Evaluate all registered objectives on *idea*.

        Parameters
        ----------
        idea:
            :class:`IdeaProposal` to score.

        Returns
        -------
        dict[str, float]
            Mapping of ``{objective.name: score}`` for every objective.
        """
        return {obj.name: obj.evaluate(idea) for obj in self.objectives}

    def rank_ideas(self, ideas: list[Any]) -> list[tuple[Any, float]]:
        """Rank *ideas* by descending average weighted score.

        Each idea's aggregate score is the mean of all weighted objective
        scores.

        Parameters
        ----------
        ideas:
            List of :class:`IdeaProposal` instances to rank.

        Returns
        -------
        list[tuple[Any, float]]
            Descending list of ``(idea, aggregate_score)`` pairs.
        """
        scored: list[tuple[Any, float]] = []
        for idea in ideas:
            weighted_scores = [obj.weighted_evaluate(idea) for obj in self.objectives]
            agg = _safe_mean(weighted_scores)
            scored.append((idea, agg))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def top_k(self, ideas: list[Any], k: int) -> list[tuple[Any, float]]:
        """Return the top-*k* ideas by aggregate weighted score.

        Parameters
        ----------
        ideas:
            Pool of ideas to rank.
        k:
            Number of ideas to return.

        Returns
        -------
        list[tuple[Any, float]]
            Up to *k* ``(idea, score)`` pairs, best first.
        """
        ranked = self.rank_ideas(ideas)
        return ranked[:max(0, k)]

    def objective_names(self) -> list[str]:
        """Return the list of objective name strings in registration order.

        Returns
        -------
        list[str]
            Ordered objective names.
        """
        return [obj.name for obj in self.objectives]

    def __repr__(self) -> str:
        names = [obj.name for obj in self.objectives]
        return f"ObjectiveEvaluator(objectives={names})"


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # helpers
    "_tokenize",
    "_clamp",
    "_jaccard",
    "_normalize",
    "_keyword_count",
    "_safe_mean",
    # classes
    "BaseObjective",
    "NoveltyObjective",
    "FeasibilityObjective",
    "PurposeObjective",
    "YieldObjective",
    "CostObjective",
    "CompositeObjective",
    "ObjectiveFactory",
    "ObjectiveEvaluator",
]
