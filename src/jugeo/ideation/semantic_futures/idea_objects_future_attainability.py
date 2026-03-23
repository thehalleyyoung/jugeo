"""
jugeo.ideation.semantic_futures.idea_objects_future_attainability
======================================================================

Idea Objects: Future Attainability, Predicted Leverage, and Support Scope
(Theory Ch. — Ideation as Search over Semantic Futures, §1).

Theory
------
An *idea* in JuGeo is a formal proposal for a new theorem, invariant, or
semantic region.  Ideas are not free-form text: they are structured objects
with computable attributes.

Formally, an idea :math:`I` is a tuple:

.. math::

   I = (\\tau, \\lambda, \\Sigma, \\rho)

where:

* :math:`\\tau` is the *idea type* (theorem, invariant, or semantic region).
* :math:`\\lambda \\in [0, 1]` is the *predicted leverage* — how much the idea
  is expected to reduce the total obstruction count if implemented.
* :math:`\\Sigma \\subseteq \\mathcal{C}` is the *support scope* — the set of
  coordinates that would benefit from this idea.
* :math:`\\rho \\in [0, 1]` is the *attainability* — a prior estimate of
  whether this idea can actually be implemented given the current portfolio.

Predicted leverage is computed as:

.. math::

   \\lambda(I) = \\frac{|\\{c \\in \\mathcal{C} : c \\text{ is obstructed and } c \\in \\Sigma\\}|}{|\\mathcal{C}_{\\text{obstructed}}|}

Attainability is computed from three factors:
1. **Semantic distance** from the idea's required background to the current portfolio.
2. **Dependency availability** — are all prerequisite results already in the portfolio?
3. **Complexity estimate** — a rough estimate of implementation difficulty.
"""
from __future__ import annotations

import heapq
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "IdeaType",
    "IdeaAttainabilityFactors",
    "IdeaObject",
    "IdeaPortfolio",
    "AttainabilityEstimator",
    "LeveragePredictor",
    "SupportScopeExpander",
    "IdeaObjectsFutureAttainabilityAnalyzer",
    "IdeaObjectsFutureAttainabilityWitness",
    "IdeaObjectsFutureAttainabilityCoordinator",
    "compute_semantic_distance",
    "compute_dependency_availability",
    "estimate_complexity",
    "rank_ideas_by_priority",
    "filter_ideas_by_attainability",
    "ideas_covering_coordinate",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on any error.

    Args:
        value: The value to convert.
        default: The fallback value if conversion fails.

    Returns:
        A float representation of value, or default on failure.
    """
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: The value to clamp.
        lo: The lower bound (inclusive).
        hi: The upper bound (inclusive).

    Returns:
        The clamped value.
    """
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _ema(previous: float, current: float, alpha: float = 0.1) -> float:
    """Compute an exponential moving average update.

    Given a previous EMA value and a new observation, return the updated EMA
    using the smoothing factor *alpha*.

    Args:
        previous: The previous EMA value.
        current: The new observation.
        alpha: The smoothing factor in (0, 1].  Defaults to 0.1.

    Returns:
        The updated EMA value.
    """
    alpha = _clamp(_safe_float(alpha, 0.1), 1e-9, 1.0)
    return alpha * _safe_float(current, 0.0) + (1.0 - alpha) * _safe_float(previous, 0.0)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class IdeaType(Enum):
    """Enumeration of idea types in JuGeo's semantic search framework.

    Attributes:
        THEOREM: A formal mathematical theorem to be proved.
        INVARIANT: A structural invariant over a family of objects.
        SEMANTIC_REGION: A new semantic region or conceptual territory.
        CONJECTURE: An unproved but plausible mathematical statement.
        CONSTRUCTION: An explicit construction of a mathematical object.
    """

    THEOREM = "theorem"
    INVARIANT = "invariant"
    SEMANTIC_REGION = "semantic_region"
    CONJECTURE = "conjecture"
    CONSTRUCTION = "construction"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdeaAttainabilityFactors:
    """The three primary factors contributing to idea attainability.

    Attributes:
        semantic_distance: Fraction of required background *not* present in
            the current portfolio.  0 = all background available; 1 = none.
        dependency_availability: Fraction of required dependencies that are
            already available.  0 = none available; 1 = all available.
        complexity_estimate: A rough estimate of implementation difficulty,
            normalised to [0, 1].  Higher means more complex.
        manual_override: If provided, bypasses the composite calculation and
            returns this value directly.  Must be in [0, 1] if set.
    """

    semantic_distance: float
    dependency_availability: float
    complexity_estimate: float
    manual_override: float | None = None

    def composite(self) -> float:
        """Compute the composite attainability score.

        If *manual_override* is set, that value is returned directly (clamped
        to [0, 1]).  Otherwise the composite is:

            0.4 * (1 - semantic_distance)
          + 0.4 * dependency_availability
          + 0.2 * (1 - complexity_estimate)

        Returns:
            A float in [0, 1] representing the composite attainability.
        """
        if self.manual_override is not None:
            return _clamp(_safe_float(self.manual_override, 0.0), 0.0, 1.0)
        sd = _safe_float(self.semantic_distance, 0.0)
        da = _safe_float(self.dependency_availability, 0.0)
        ce = _safe_float(self.complexity_estimate, 0.0)
        raw = 0.4 * (1.0 - sd) + 0.4 * da + 0.2 * (1.0 - ce)
        return _clamp(raw, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class IdeaObject:
    """A structured idea object in JuGeo's ideation system.

    An *IdeaObject* is the fundamental unit of ideation.  It encapsulates all
    computable attributes of an idea including its type, predicted leverage,
    attainability, and the set of coordinates in its support scope.

    Attributes:
        idea_id: A unique identifier for this idea.
        description: A human-readable description of the idea.
        idea_type: The type of idea (theorem, invariant, etc.).
        predicted_leverage: Expected fractional reduction in obstructions.
        attainability: Prior estimate of implementability in [0, 1].
        support_scope: The frozenset of coordinate IDs that benefit from this.
        background_required: The frozenset of background results required.
        metadata: Arbitrary additional metadata.
    """

    idea_id: str
    description: str
    idea_type: IdeaType
    predicted_leverage: float
    attainability: float
    support_scope: frozenset[str]
    background_required: frozenset[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def priority_score(self) -> float:
        """Compute the priority score for this idea.

        The priority score is the product of predicted leverage and
        attainability, giving a measure of expected impact discounted by
        the probability of successful implementation.

        Returns:
            A float in [0, 1] representing the priority score.
        """
        lev = _clamp(_safe_float(self.predicted_leverage, 0.0), 0.0, 1.0)
        att = _clamp(_safe_float(self.attainability, 0.0), 0.0, 1.0)
        return lev * att

    def is_attainable(self, threshold: float = 0.5) -> bool:
        """Determine whether the idea meets an attainability threshold.

        Args:
            threshold: The minimum attainability score required.  Defaults to
                0.5 (50%).

        Returns:
            True if attainability >= threshold, False otherwise.
        """
        return _safe_float(self.attainability, 0.0) >= _safe_float(threshold, 0.5)


# ---------------------------------------------------------------------------
# Mutable dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IdeaPortfolio:
    """A mutable collection of IdeaObject instances.

    The portfolio tracks a set of ideas identified by their idea_id.  It
    provides filtering and ranking operations over the idea collection.

    Attributes:
        portfolio_id: A unique identifier for this portfolio.
        _ideas: Internal dict mapping idea_id -> IdeaObject.
    """

    portfolio_id: str
    _ideas: dict[str, IdeaObject] = field(default_factory=dict)

    def add(self, idea: IdeaObject) -> None:
        """Add an IdeaObject to the portfolio.

        If an idea with the same idea_id already exists it is replaced.

        Args:
            idea: The IdeaObject to add.
        """
        if not isinstance(idea, IdeaObject):
            raise TypeError(f"Expected IdeaObject, got {type(idea)}")
        self._ideas[idea.idea_id] = idea

    def remove(self, idea_id: str) -> None:
        """Remove an idea from the portfolio by its idea_id.

        Args:
            idea_id: The ID of the idea to remove.

        Raises:
            KeyError: If the idea_id is not in the portfolio.
        """
        if idea_id not in self._ideas:
            raise KeyError(f"Idea '{idea_id}' not found in portfolio '{self.portfolio_id}'")
        del self._ideas[idea_id]

    def by_type(self, t: IdeaType) -> list[IdeaObject]:
        """Return all ideas of a given IdeaType.

        Args:
            t: The IdeaType to filter by.

        Returns:
            A list of IdeaObjects with idea_type == t.
        """
        return [idea for idea in self._ideas.values() if idea.idea_type == t]

    def top_k(self, k: int, key: str = "priority") -> list[IdeaObject]:
        """Return the top-k ideas sorted by a given key.

        Args:
            k: The number of ideas to return.
            key: Either "priority" (sort by priority_score) or "attainability"
                (sort by attainability).  Defaults to "priority".

        Returns:
            A list of up to k IdeaObjects in descending order.
        """
        k = max(0, int(k))
        ideas = list(self._ideas.values())
        if key == "attainability":
            ideas.sort(key=lambda i: _safe_float(i.attainability, 0.0), reverse=True)
        else:
            ideas.sort(key=lambda i: i.priority_score(), reverse=True)
        return ideas[:k]

    def __len__(self) -> int:
        """Return the number of ideas in the portfolio."""
        return len(self._ideas)

    def all_ideas(self) -> list[IdeaObject]:
        """Return all ideas in the portfolio as a list.

        Returns:
            A list of all IdeaObjects currently in the portfolio.
        """
        return list(self._ideas.values())


@dataclass
class AttainabilityEstimator:
    """Estimates the attainability of an IdeaObject given the current context.

    The estimator computes the three attainability factors (semantic distance,
    dependency availability, complexity) and combines them via the
    IdeaAttainabilityFactors.composite() method.

    Attributes:
        estimator_id: A unique identifier for this estimator instance.
        semantic_weight: Weight for the semantic distance factor.
        dep_weight: Weight for the dependency availability factor.
        complexity_weight: Weight for the complexity estimate factor.
    """

    estimator_id: str
    semantic_weight: float = 0.4
    dep_weight: float = 0.4
    complexity_weight: float = 0.2

    def estimate(
        self,
        idea: IdeaObject,
        portfolio: IdeaPortfolio,
        available_results: set[str],
    ) -> float:
        """Estimate the attainability of *idea* given current context.

        Computes:
          - semantic_distance from idea.background_required vs available_results
          - dependency_availability from idea.background_required vs available_results
          - complexity from idea.idea_type and len(idea.support_scope)

        Args:
            idea: The idea to assess.
            portfolio: The current idea portfolio (used for context).
            available_results: The set of currently available background results.

        Returns:
            A float in [0, 1] representing estimated attainability.
        """
        sem_dist = compute_semantic_distance(idea.background_required, available_results)
        dep_avail = compute_dependency_availability(idea.background_required, available_results)
        complexity = estimate_complexity(idea.idea_type, len(idea.support_scope))
        factors = IdeaAttainabilityFactors(
            semantic_distance=sem_dist,
            dependency_availability=dep_avail,
            complexity_estimate=complexity,
        )
        return factors.composite()


@dataclass
class LeveragePredictor:
    """Predicts the leverage of an idea given the current obstruction state.

    Leverage measures the fraction of obstructed coordinates that fall within
    the idea's support scope.

    Attributes:
        predictor_id: A unique identifier for this predictor instance.
    """

    predictor_id: str

    def predict(
        self,
        support_scope: frozenset[str],
        obstructed_coords: set[str],
        total_coords: int,
    ) -> float:
        """Predict leverage as |scope ∩ obstructed| / max(1, |obstructed|).

        Args:
            support_scope: The frozenset of coordinates the idea supports.
            obstructed_coords: The set of currently obstructed coordinates.
            total_coords: Total number of coordinates (used for normalisation).

        Returns:
            A float in [0, 1] representing predicted leverage.
        """
        if not obstructed_coords:
            return 0.0
        intersection = len(support_scope & obstructed_coords)
        denom = max(1, len(obstructed_coords))
        return _clamp(intersection / denom, 0.0, 1.0)


@dataclass
class SupportScopeExpander:
    """Expands an idea's support scope via transitive dependency traversal.

    Starting from a seed set of coordinates, the expander performs a BFS over
    the dependency graph up to *max_depth* hops, collecting all reachable
    coordinates.

    Attributes:
        expander_id: A unique identifier for this expander instance.
        max_depth: Maximum number of BFS hops.  Defaults to 3.
    """

    expander_id: str
    max_depth: int = 3

    def expand(
        self,
        seed_coords: frozenset[str],
        dependency_graph: dict[str, set[str]],
    ) -> frozenset[str]:
        """Expand the support scope via BFS over the dependency graph.

        Starting from *seed_coords*, iteratively adds all coordinates that the
        seed set depends on (transitively), up to *max_depth* hops.

        Args:
            seed_coords: The initial set of coordinates to expand from.
            dependency_graph: A dict mapping coord_id -> set of dependencies.

        Returns:
            A frozenset of all reachable coordinates within max_depth hops.
        """
        visited: set[str] = set(seed_coords)
        frontier: set[str] = set(seed_coords)
        depth = 0
        while frontier and depth < self.max_depth:
            next_frontier: set[str] = set()
            for coord in frontier:
                deps = dependency_graph.get(coord, set())
                for dep in deps:
                    if dep not in visited:
                        visited.add(dep)
                        next_frontier.add(dep)
            frontier = next_frontier
            depth += 1
        return frozenset(visited)

    def scope_size(self, scope: frozenset[str]) -> int:
        """Return the cardinality of the given scope.

        Args:
            scope: A frozenset of coordinate IDs.

        Returns:
            The number of coordinates in the scope.
        """
        return len(scope)


@dataclass
class IdeaObjectsFutureAttainabilityAnalyzer:
    """Analyzes ideas for future attainability, leverage, and coverage.

    Provides both per-idea analysis and portfolio-level coverage reports.

    Attributes:
        analyzer_id: A unique identifier for this analyzer instance.
    """

    analyzer_id: str

    def analyze(
        self,
        idea: IdeaObject,
        portfolio: IdeaPortfolio,
        available_results: set[str],
    ) -> dict[str, Any]:
        """Produce a detailed analysis report for a single idea.

        Computes attainability, leverage, priority, support scope size,
        attainability flag, background gap, and a textual recommendation.

        Args:
            idea: The idea to analyze.
            portfolio: The current idea portfolio.
            available_results: The set of currently available background results.

        Returns:
            A dict containing:
              - attainability (float)
              - leverage (float)
              - priority_score (float)
              - support_scope_size (int)
              - is_attainable (bool)
              - background_gap (set[str])
              - recommendation (str)
        """
        attainability = _safe_float(idea.attainability, 0.0)
        leverage = _safe_float(idea.predicted_leverage, 0.0)
        priority = idea.priority_score()
        scope_size = len(idea.support_scope)
        is_att = idea.is_attainable(threshold=0.5)
        background_gap: set[str] = set(idea.background_required) - available_results

        if is_att and leverage >= 0.5:
            recommendation = (
                f"IMPLEMENT: Idea '{idea.idea_id}' is highly attainable ({attainability:.2f}) "
                f"with strong leverage ({leverage:.2f}). Prioritise immediately."
            )
        elif is_att and leverage < 0.5:
            recommendation = (
                f"QUEUE: Idea '{idea.idea_id}' is attainable ({attainability:.2f}) "
                f"but has low leverage ({leverage:.2f}). Queue for a future iteration."
            )
        elif not is_att and len(background_gap) > 0:
            gap_list = ", ".join(sorted(background_gap)[:5])
            recommendation = (
                f"DEFER: Idea '{idea.idea_id}' is not yet attainable "
                f"(attainability={attainability:.2f}). "
                f"Missing background: [{gap_list}]. Acquire prerequisites first."
            )
        else:
            recommendation = (
                f"RECONSIDER: Idea '{idea.idea_id}' has low attainability ({attainability:.2f}) "
                f"and low leverage ({leverage:.2f}). Consider revising or archiving."
            )

        return {
            "attainability": attainability,
            "leverage": leverage,
            "priority_score": priority,
            "support_scope_size": scope_size,
            "is_attainable": is_att,
            "background_gap": background_gap,
            "recommendation": recommendation,
        }

    def coverage_report(
        self,
        portfolio: IdeaPortfolio,
        total_coords: int,
    ) -> dict[str, Any]:
        """Produce a portfolio-level coverage report.

        Args:
            portfolio: The idea portfolio to report on.
            total_coords: Total number of coordinates in the search space.

        Returns:
            A dict containing:
              - total_ideas (int)
              - covered_coords (int)
              - coverage_fraction (float)
              - type_breakdown (dict mapping IdeaType -> count)
        """
        all_ideas = portfolio.all_ideas()
        covered: set[str] = set()
        for idea in all_ideas:
            covered.update(idea.support_scope)

        covered_count = len(covered)
        coverage_fraction = covered_count / max(1, total_coords)

        type_breakdown: dict[str, int] = {}
        for t in IdeaType:
            type_breakdown[t.value] = sum(1 for i in all_ideas if i.idea_type == t)

        return {
            "total_ideas": len(all_ideas),
            "covered_coords": covered_count,
            "coverage_fraction": _clamp(coverage_fraction, 0.0, 1.0),
            "type_breakdown": type_breakdown,
        }


@dataclass(frozen=True, slots=True)
class IdeaObjectsFutureAttainabilityWitness:
    """An immutable witness record for an attainability analysis run.

    Witnesses serve as audit records, capturing the outputs of a coordinator
    run for reproducibility and downstream inspection.

    Attributes:
        witness_id: A unique identifier for this witness.
        timestamp: Unix timestamp of the analysis.
        success: Whether the analysis completed without error.
        idea_id: The ID of the analyzed idea.
        attainability: The computed attainability score.
        leverage: The computed leverage score.
        support_scope_size: The size of the (possibly expanded) support scope.
        analysis_report: The full dict returned by the analyzer.
        summary: A human-readable one-line summary of the result.
    """

    witness_id: str
    timestamp: float
    success: bool
    idea_id: str
    attainability: float
    leverage: float
    support_scope_size: int
    analysis_report: dict[str, Any]
    summary: str


@dataclass
class IdeaObjectsFutureAttainabilityCoordinator:
    """Coordinator for idea attainability analysis in JuGeo.

    # copilot: The coordinator orchestrates idea attainability analysis.
    # copilot: It accepts an IdeaObject, portfolio, and context and:
    # copilot:
    # copilot: 1. Computes attainability factors (semantic distance, dep availability, complexity).
    # copilot: 2. Estimates overall attainability score via AttainabilityEstimator.
    # copilot: 3. Predicts leverage from support scope vs obstructed coords.
    # copilot: 4. Expands support scope via transitive dependencies.
    # copilot: 5. Reconstructs the IdeaObject with updated attainability/leverage.
    # copilot: 6. Runs IdeaObjectsFutureAttainabilityAnalyzer for quality report.
    # copilot: 7. Seals and returns an IdeaObjectsFutureAttainabilityWitness.

    Attributes:
        session_id: A unique identifier for this coordinator session.
        attainability_threshold: Minimum attainability to consider an idea viable.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attainability_threshold: float = 0.5

    def run(
        self,
        idea: IdeaObject,
        portfolio: IdeaPortfolio,
        available_results: set[str],
        obstructed_coords: set[str],
        dependency_graph: dict[str, set[str]],
    ) -> IdeaObjectsFutureAttainabilityWitness:
        """Execute the full attainability analysis pipeline.

        Args:
            idea: The IdeaObject to analyze.
            portfolio: The current idea portfolio.
            available_results: Background results currently in the portfolio.
            obstructed_coords: Coordinates that are currently obstructed.
            dependency_graph: A dict mapping coord_id -> set of dependency IDs.

        Returns:
            An IdeaObjectsFutureAttainabilityWitness capturing all outputs.
        """
        t0 = time.time()
        success = True
        error_note = ""

        try:
            # Step 1-2: Estimate attainability
            estimator = AttainabilityEstimator(
                estimator_id=f"{self.session_id}:est",
            )
            new_attainability = estimator.estimate(idea, portfolio, available_results)

            # Step 3: Predict leverage
            predictor = LeveragePredictor(predictor_id=f"{self.session_id}:pred")
            new_leverage = predictor.predict(idea.support_scope, obstructed_coords, 0)

            # Step 4: Expand support scope
            expander = SupportScopeExpander(expander_id=f"{self.session_id}:exp")
            expanded_scope = expander.expand(idea.support_scope, dependency_graph)

            # Step 5: Reconstruct updated idea (we cannot mutate frozen, so create new)
            updated_idea = IdeaObject(
                idea_id=idea.idea_id,
                description=idea.description,
                idea_type=idea.idea_type,
                predicted_leverage=new_leverage,
                attainability=new_attainability,
                support_scope=expanded_scope,
                background_required=idea.background_required,
                metadata={**idea.metadata, "coordinator_session": self.session_id},
            )

            # Step 6: Run analyzer
            analyzer = IdeaObjectsFutureAttainabilityAnalyzer(
                analyzer_id=f"{self.session_id}:anal"
            )
            report = analyzer.analyze(updated_idea, portfolio, available_results)
            scope_size = len(expanded_scope)

        except Exception as exc:  # pragma: no cover
            success = False
            error_note = str(exc)
            new_attainability = _safe_float(idea.attainability, 0.0)
            new_leverage = _safe_float(idea.predicted_leverage, 0.0)
            scope_size = len(idea.support_scope)
            report = {"error": error_note}

        summary = (
            f"[{self.session_id}] idea={idea.idea_id} "
            f"attainability={new_attainability:.3f} "
            f"leverage={new_leverage:.3f} "
            f"scope={scope_size} "
            f"success={success}"
        )

        return IdeaObjectsFutureAttainabilityWitness(
            witness_id=str(uuid.uuid4()),
            timestamp=time.time(),
            success=success,
            idea_id=idea.idea_id,
            attainability=new_attainability,
            leverage=new_leverage,
            support_scope_size=scope_size,
            analysis_report=report,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Module-level standalone functions
# ---------------------------------------------------------------------------


def compute_semantic_distance(
    idea_background: frozenset[str],
    portfolio_results: set[str],
) -> float:
    """Compute the semantic distance between required background and available results.

    The semantic distance is the fraction of required background items that are
    NOT present in the portfolio.  A distance of 0 means all background is
    available; 1 means none is available.

    Args:
        idea_background: The frozenset of background result IDs required by an idea.
        portfolio_results: The set of result IDs currently in the portfolio.

    Returns:
        A float in [0, 1] representing the fraction of unavailable background.
    """
    if not idea_background:
        return 0.0
    missing = len(idea_background - portfolio_results)
    return _clamp(missing / len(idea_background), 0.0, 1.0)


def compute_dependency_availability(
    required: frozenset[str],
    available: set[str],
) -> float:
    """Compute the fraction of required dependencies that are available.

    Args:
        required: The frozenset of required result/dependency IDs.
        available: The set of currently available result IDs.

    Returns:
        A float in [0, 1]: 1.0 if all required are available, 0.0 if none are.
    """
    if not required:
        return 1.0
    present = len(required & available)
    return _clamp(present / len(required), 0.0, 1.0)


def estimate_complexity(idea_type: IdeaType, support_scope_size: int) -> float:
    """Estimate implementation complexity for a given idea type and scope size.

    Base complexity by type:
      CONSTRUCTION   → 0.9
      THEOREM        → 0.7
      INVARIANT      → 0.5
      SEMANTIC_REGION→ 0.4
      CONJECTURE     → 0.3

    The base is then scaled upward by log(1 + support_scope_size) / 10 to
    penalise large support scopes.

    Args:
        idea_type: The IdeaType of the idea.
        support_scope_size: The number of coordinates in the support scope.

    Returns:
        A float in [0, 1] representing estimated complexity.
    """
    base: dict[IdeaType, float] = {
        IdeaType.CONSTRUCTION: 0.9,
        IdeaType.THEOREM: 0.7,
        IdeaType.INVARIANT: 0.5,
        IdeaType.SEMANTIC_REGION: 0.4,
        IdeaType.CONJECTURE: 0.3,
    }
    b = base.get(idea_type, 0.5)
    scale = math.log(1.0 + max(0, support_scope_size)) / 10.0
    return _clamp(b + scale, 0.0, 1.0)


def rank_ideas_by_priority(ideas: list[IdeaObject]) -> list[IdeaObject]:
    """Return ideas sorted in descending order of priority_score.

    Args:
        ideas: A list of IdeaObjects to rank.

    Returns:
        A new list of IdeaObjects sorted from highest to lowest priority_score.
    """
    return sorted(ideas, key=lambda i: i.priority_score(), reverse=True)


def filter_ideas_by_attainability(
    ideas: list[IdeaObject],
    threshold: float,
) -> list[IdeaObject]:
    """Filter ideas by attainability threshold.

    Args:
        ideas: A list of IdeaObjects to filter.
        threshold: Only ideas with attainability >= threshold are returned.

    Returns:
        A list of IdeaObjects that meet or exceed the threshold.
    """
    return [i for i in ideas if i.is_attainable(threshold)]


def ideas_covering_coordinate(
    ideas: list[IdeaObject],
    coord_id: str,
) -> list[IdeaObject]:
    """Return all ideas whose support scope includes a given coordinate.

    Args:
        ideas: A list of IdeaObjects to search.
        coord_id: The coordinate ID to check coverage for.

    Returns:
        A list of IdeaObjects that have coord_id in their support_scope.
    """
    return [i for i in ideas if coord_id in i.support_scope]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== IdeaObjects Future Attainability Smoke Test ===\n")

    # Build a small set of ideas
    idea_a = IdeaObject(
        idea_id="idea-001",
        description="Prove the main structure theorem for triangulated categories",
        idea_type=IdeaType.THEOREM,
        predicted_leverage=0.8,
        attainability=0.6,
        support_scope=frozenset({"coord-1", "coord-2", "coord-3"}),
        background_required=frozenset({"res-A", "res-B"}),
    )
    idea_b = IdeaObject(
        idea_id="idea-002",
        description="Construct the canonical semantic region functor",
        idea_type=IdeaType.CONSTRUCTION,
        predicted_leverage=0.5,
        attainability=0.3,
        support_scope=frozenset({"coord-2", "coord-4"}),
        background_required=frozenset({"res-B", "res-C", "res-D"}),
    )
    idea_c = IdeaObject(
        idea_id="idea-003",
        description="Invariant characterising obstruction-free coordinates",
        idea_type=IdeaType.INVARIANT,
        predicted_leverage=0.4,
        attainability=0.9,
        support_scope=frozenset({"coord-1", "coord-5"}),
        background_required=frozenset({"res-A"}),
    )

    portfolio = IdeaPortfolio(portfolio_id="portfolio-main")
    for idea in [idea_a, idea_b, idea_c]:
        portfolio.add(idea)

    print(f"Portfolio size: {len(portfolio)}")
    print("Top-2 by priority:", [i.idea_id for i in portfolio.top_k(2)])
    print("Theorems:", [i.idea_id for i in portfolio.by_type(IdeaType.THEOREM)])

    # Attainability factors
    factors = IdeaAttainabilityFactors(
        semantic_distance=0.2,
        dependency_availability=0.8,
        complexity_estimate=0.4,
    )
    print(f"\nComposite attainability: {factors.composite():.4f}")

    # Standalone functions
    available = {"res-A", "res-B"}
    print(f"Semantic distance (idea_a): {compute_semantic_distance(idea_a.background_required, available):.4f}")
    print(f"Dep availability (idea_b): {compute_dependency_availability(idea_b.background_required, available):.4f}")
    print(f"Complexity (THEOREM, scope=3): {estimate_complexity(IdeaType.THEOREM, 3):.4f}")

    ranked = rank_ideas_by_priority([idea_a, idea_b, idea_c])
    print(f"\nRanked by priority: {[i.idea_id for i in ranked]}")
    attainable = filter_ideas_by_attainability([idea_a, idea_b, idea_c], threshold=0.5)
    print(f"Attainable (threshold=0.5): {[i.idea_id for i in attainable]}")
    covering = ideas_covering_coordinate([idea_a, idea_b, idea_c], "coord-2")
    print(f"Ideas covering coord-2: {[i.idea_id for i in covering]}")

    # Expander
    dep_graph: dict[str, set[str]] = {
        "coord-1": {"coord-6", "coord-7"},
        "coord-2": {"coord-8"},
        "coord-6": {"coord-9"},
    }
    expander = SupportScopeExpander(expander_id="exp-01", max_depth=2)
    expanded = expander.expand(frozenset({"coord-1", "coord-2"}), dep_graph)
    print(f"\nExpanded scope from {{coord-1, coord-2}}: {sorted(expanded)}")

    # Coordinator
    coordinator = IdeaObjectsFutureAttainabilityCoordinator(attainability_threshold=0.4)
    obstructed = {"coord-1", "coord-2", "coord-3", "coord-4"}
    witness = coordinator.run(idea_a, portfolio, available, obstructed, dep_graph)
    print(f"\nWitness summary: {witness.summary}")
    print(f"Recommendation: {witness.analysis_report.get('recommendation', 'N/A')}")
    print(f"\nBackground gap for idea_b: {witness.analysis_report.get('background_gap', set())}")

    # Coverage report
    analyzer = IdeaObjectsFutureAttainabilityAnalyzer(analyzer_id="anal-main")
    cov = analyzer.coverage_report(portfolio, total_coords=10)
    print(f"\nCoverage report: {cov}")

    print("\n=== All smoke tests passed ===")
