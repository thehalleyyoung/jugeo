"""Core data models for jugeo.ideation.novelty_search – theory2.tex Ch57.

Defines the structured types used throughout the novelty search subsystem:
optimization problems, coverage metrics, distance specs, search results,
and diversity constraints.

Module layout::

    SearchStrategy        – search strategy enumeration
    MetricKind            – distance metric kind enumeration
    NoveltySearchProblem  – optimization problem specification
    PortfolioCoverage     – coverage metrics for an idea portfolio
    NoveltyMetricSpec     – distance metric specification (local type)
    SearchResult          – single search result record
    DiversityConstraint   – diversity constraint specification

Theory background (Ch57):
    The models in this module are the typed carriers for the novelty search
    pipeline.  A ``NoveltySearchProblem`` captures the full problem statement
    (portfolio snapshot, purpose, budget, strategy).  ``SearchResult`` records
    a single candidate together with its novelty, coverage-gain, and feasibility
    scores.  ``DiversityConstraint`` encodes the structural diversity requirements
    that the final result set must satisfy.

    ``PortfolioCoverage`` quantifies how uniformly the current portfolio occupies
    the idea space, and ``NoveltyMetricSpec`` declares the distance metric to
    use during search without coupling to a specific implementation.

Usage example::

    from jugeo.ideation.novelty_search.models import (
        NoveltySearchProblem,
        SearchStrategy,
        MetricKind,
        make_default_problem,
        make_strict_constraint,
    )

    problem = make_default_problem(
        purpose="Develop new proof techniques for HoTT",
        portfolio_ids=["thm-001", "thm-002", "thm-003"],
    )
    constraint = make_strict_constraint()
    print(problem.describe())
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import (
    Idea,
    IdeaPortfolio,
    GainProfile,
    ValidationPath,
    TrustStatus,
    LifecycleStatus,
    EvaluationResult,
)
from jugeo.ideation.novelty import (
    NoveltyScore,
    NoveltyMetric as _NoveltyMetricBase,
    TheoremPortfolio,
    PurposeAlignmentChecker,
)

# ---------------------------------------------------------------------------
# Private helper utilities
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* into the closed interval [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenize *text* into a frozenset of lower-case alphanumeric tokens (len > 1)."""
    return frozenset(t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1)


def _slugify(text: str) -> str:
    """Convert *text* to a URL-safe slug."""
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower().strip()).strip("-")


def _normalize(text: str) -> str:
    """Collapse internal whitespace and strip leading/trailing whitespace."""
    return " ".join(text.split()).strip()


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute the Jaccard similarity between two token sets.

    Returns 1.0 when both sets are empty (vacuously equal), 0.0 when their
    union is non-empty but they share no elements.
    """
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _cosine_sim(
    a: tuple[float, ...], b: tuple[float, ...]
) -> float:
    """Compute the cosine similarity between two vectors.

    Parameters
    ----------
    a, b:
        Vectors of equal length.  If either is the zero vector the result
        is 0.0 (no similarity defined).

    Returns
    -------
    float
        Value in [-1, 1], typically clipped to [0, 1] for non-negative vectors.
    """
    if len(a) != len(b):
        raise ValueError(
            f"Vectors must have equal length, got {len(a)} and {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return _clamp(dot / (norm_a * norm_b), -1.0, 1.0)


def _normalize_weights(wv: tuple[float, ...]) -> tuple[float, ...]:
    """Return *wv* normalized so that its elements sum to 1.0.

    If *wv* is empty or sums to zero, return *wv* unchanged.
    """
    if not wv:
        return wv
    total = sum(wv)
    if total < 1e-12:
        return wv
    return tuple(w / total for w in wv)


# ---------------------------------------------------------------------------
# SearchStrategy
# ---------------------------------------------------------------------------


class SearchStrategy(str, Enum):
    """Enumeration of search strategies for novelty-driven idea discovery.

    GREEDY
        Select the locally highest-novelty candidate at each step.  Fast
        and deterministic but may miss globally diverse solutions.

    BEAM
        Maintain a beam of *k* partial solutions, extending each at each step.
        Deterministic and more thorough than greedy.

    DIVERSE
        Explicitly maximize pairwise distance among the final result set,
        trading off individual novelty for structural diversity.

    PARETO
        Select results on the Pareto frontier of (novelty, feasibility),
        returning a non-dominated set.

    RANDOM
        Sample candidates uniformly at random from the feasible set.  Useful
        as a baseline and for ensemble diversity.
    """

    GREEDY = "greedy"
    BEAM = "beam"
    DIVERSE = "diverse"
    PARETO = "pareto"
    RANDOM = "random"

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_deterministic(self) -> bool:
        """Return True if this strategy produces deterministic outputs.

        RANDOM is the only non-deterministic strategy; all others produce
        the same output given the same inputs.
        """
        return self is not SearchStrategy.RANDOM

    def default_beam_width(self) -> int:
        """Return the default beam width for this strategy.

        Only meaningful for BEAM (returns 5); all other strategies return 1
        as a canonical fallback (they do not maintain a beam).
        """
        if self is SearchStrategy.BEAM:
            return 5
        return 1

    def description(self) -> str:
        """Return a one-sentence description of this search strategy."""
        _desc: dict[str, str] = {
            "GREEDY": (
                "Greedy selection of the highest-novelty candidate at each step."
            ),
            "BEAM": (
                "Beam search maintaining k partial solutions for more thorough exploration."
            ),
            "DIVERSE": (
                "Diversity-maximizing search that explicitly maximizes pairwise distance."
            ),
            "PARETO": (
                "Pareto-front selection returning non-dominated (novelty, feasibility) pairs."
            ),
            "RANDOM": (
                "Uniform random sampling from the feasible candidate set."
            ),
        }
        return _desc.get(self.name, f"Strategy: {self.value}")


# ---------------------------------------------------------------------------
# MetricKind
# ---------------------------------------------------------------------------


class MetricKind(str, Enum):
    """Enumeration of distance metric kinds used in novelty scoring.

    SEMANTIC
        Embedding-based cosine distance in a continuous semantic space.
        Requires embedding vectors to be pre-computed.

    STRUCTURAL
        Graph- or syntax-tree-based structural similarity (e.g., tree edit
        distance, dependency graph overlap).  Does not require embeddings.

    TOPOLOGICAL
        Persistent-homology or other topological features of the idea graph.
        Does not require embeddings.

    HYBRID
        A weighted combination of semantic and structural components.
        Requires embeddings for the semantic component.
    """

    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    TOPOLOGICAL = "topological"
    HYBRID = "hybrid"

    def default_weight(self) -> float:
        """Return the default weight assigned to this metric kind in hybrid scoring.

        Weights reflect typical relative importance:
        SEMANTIC → 0.50, STRUCTURAL → 0.25, TOPOLOGICAL → 0.15, HYBRID → 1.0
        (HYBRID represents the combined metric and is not combined further).
        """
        _weights: dict[str, float] = {
            "SEMANTIC": 0.50,
            "STRUCTURAL": 0.25,
            "TOPOLOGICAL": 0.15,
            "HYBRID": 1.0,
        }
        return _weights.get(self.name, 0.0)

    def requires_embedding(self) -> bool:
        """Return True if this metric kind requires pre-computed embedding vectors.

        SEMANTIC and HYBRID require embeddings; STRUCTURAL and TOPOLOGICAL
        operate on structural/graph representations instead.
        """
        return self in (MetricKind.SEMANTIC, MetricKind.HYBRID)

    def description(self) -> str:
        """Return a one-sentence description of this metric kind."""
        _desc: dict[str, str] = {
            "SEMANTIC": (
                "Embedding-based cosine distance in a continuous semantic space."
            ),
            "STRUCTURAL": (
                "Graph or syntax-tree structural similarity without embeddings."
            ),
            "TOPOLOGICAL": (
                "Persistent-homology topological features of the idea graph."
            ),
            "HYBRID": (
                "Weighted combination of semantic and structural distance components."
            ),
        }
        return _desc.get(self.name, f"MetricKind: {self.value}")


# ---------------------------------------------------------------------------
# NoveltySearchProblem
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltySearchProblem:
    """Specification of a novelty search optimization problem.

    A ``NoveltySearchProblem`` bundles together all parameters that define a
    single search run: the current portfolio snapshot (a tuple of idea IDs),
    the research purpose string, the computational budget, the diversity
    weight, and the algorithmic strategy to use.

    Instances are immutable (``frozen=True``) and slot-optimized for cache
    efficiency.  Use ``with_strategy`` / ``with_budget`` to derive modified
    copies without breaking immutability.

    Attributes
    ----------
    problem_id:
        Stable UUID-based identifier for this problem specification.
    portfolio_snapshot:
        Tuple of idea/theorem IDs representing the current portfolio at the
        time the problem was constructed.  A snapshot rather than a live
        reference so the problem specification remains stable.
    purpose:
        Research purpose string.  Used to condition novelty scoring so that
        high-novelty ideas that are irrelevant to the purpose are penalized.
    budget:
        Computational budget (must be > 0).  Interpretation is
        strategy-dependent: for GREEDY it is the max number of candidates
        evaluated; for BEAM it scales with beam_width × budget.
    diversity_weight:
        Trade-off parameter in [0, 1] between individual novelty (0) and
        pairwise diversity (1).
    feasibility_threshold:
        Minimum feasibility score [0, 1] for a candidate to be retained.
    max_results:
        Maximum number of results to return (≥ 1).
    strategy:
        ``SearchStrategy`` controlling the search algorithm.
    metric_kind:
        ``MetricKind`` controlling the distance metric.
    """

    problem_id: str
    portfolio_snapshot: tuple[str, ...]
    purpose: str
    budget: float
    diversity_weight: float = 0.5
    feasibility_threshold: float = 0.1
    max_results: int = 10
    strategy: SearchStrategy = SearchStrategy.GREEDY
    metric_kind: MetricKind = MetricKind.SEMANTIC

    def __post_init__(self) -> None:
        """Normalize and validate all fields after construction."""
        object.__setattr__(self, "problem_id", _normalize(self.problem_id).strip())
        object.__setattr__(self, "purpose", _normalize(self.purpose))

        if not self.problem_id:
            object.__setattr__(self, "problem_id", str(uuid.uuid4()))

        # Clamp continuous parameters
        object.__setattr__(
            self, "diversity_weight", _clamp(self.diversity_weight)
        )
        object.__setattr__(
            self,
            "feasibility_threshold",
            _clamp(self.feasibility_threshold),
        )

        if self.budget <= 0:
            raise ValueError(
                f"budget must be positive, got {self.budget!r}"
            )
        if self.max_results < 1:
            raise ValueError(
                f"max_results must be >= 1, got {self.max_results!r}"
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def portfolio_size(self) -> int:
        """Number of ideas/theorems in the portfolio snapshot."""
        return len(self.portfolio_snapshot)

    @property
    def is_budget_constrained(self) -> bool:
        """Return True if the budget is below the 'large' threshold of 1000."""
        return self.budget < 1000.0

    @property
    def effective_budget(self) -> float:
        """Return the budget scaled by ``max_results`` for per-result estimation."""
        return self.budget / max(self.max_results, 1)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this problem to a plain Python dict."""
        return {
            "problem_id": self.problem_id,
            "portfolio_snapshot": list(self.portfolio_snapshot),
            "purpose": self.purpose,
            "budget": self.budget,
            "diversity_weight": self.diversity_weight,
            "feasibility_threshold": self.feasibility_threshold,
            "max_results": self.max_results,
            "strategy": self.strategy.value,
            "metric_kind": self.metric_kind.value,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "NoveltySearchProblem":
        """Construct a NoveltySearchProblem from a plain Python dict."""
        return cls(
            problem_id=str(d.get("problem_id", str(uuid.uuid4()))),
            portfolio_snapshot=tuple(d.get("portfolio_snapshot", [])),
            purpose=str(d["purpose"]),
            budget=float(d["budget"]),
            diversity_weight=float(d.get("diversity_weight", 0.5)),
            feasibility_threshold=float(d.get("feasibility_threshold", 0.1)),
            max_results=int(d.get("max_results", 10)),
            strategy=SearchStrategy(d.get("strategy", "greedy")),
            metric_kind=MetricKind(d.get("metric_kind", "semantic")),
        )

    def to_json(self) -> str:
        """Serialize this problem to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "NoveltySearchProblem":
        """Construct a NoveltySearchProblem from a JSON string."""
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Derived variants
    # ------------------------------------------------------------------

    def with_strategy(self, strategy: SearchStrategy) -> "NoveltySearchProblem":
        """Return a new problem with the given search strategy."""
        d = self.to_dict()
        d["strategy"] = strategy.value
        return self.__class__.from_dict(d)

    def with_budget(self, budget: float) -> "NoveltySearchProblem":
        """Return a new problem with the given budget."""
        d = self.to_dict()
        d["budget"] = budget
        return self.__class__.from_dict(d)

    # ------------------------------------------------------------------
    # Validation and description
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty = valid)."""
        errors: list[str] = []
        if not self.purpose.strip():
            errors.append("purpose must not be empty")
        if not math.isfinite(self.budget) or self.budget <= 0:
            errors.append(f"budget must be a positive finite number, got {self.budget}")
        if self.max_results < 1:
            errors.append(f"max_results must be >= 1, got {self.max_results}")
        return errors

    def describe(self) -> str:
        """Return a multi-line human-readable description of this problem."""
        lines = [
            f"NoveltySearchProblem: {self.problem_id}",
            f"  Purpose:               {self.purpose}",
            f"  Portfolio size:        {self.portfolio_size}",
            f"  Budget:                {self.budget:.2f}",
            f"  Effective budget:      {self.effective_budget:.2f} per result",
            f"  Max results:           {self.max_results}",
            f"  Diversity weight:      {self.diversity_weight:.3f}",
            f"  Feasibility threshold: {self.feasibility_threshold:.3f}",
            f"  Strategy:              {self.strategy.value}",
            f"  Metric kind:           {self.metric_kind.value}",
            f"  Budget constrained:    {self.is_budget_constrained}",
        ]
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a compact single-line summary."""
        return (
            f"Problem({self.problem_id[:8]}…) "
            f"purpose={self.purpose[:40]!r} "
            f"budget={self.budget:.0f} "
            f"strategy={self.strategy.value}"
        )


# ---------------------------------------------------------------------------
# PortfolioCoverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioCoverage:
    """Coverage metrics describing how well a portfolio occupies the idea space.

    A PortfolioCoverage record captures the result of a single coverage
    analysis pass.  It records which domains are covered, the overall
    density and uniformity, and which regions of the idea space are sparse
    (gap regions).

    Attributes
    ----------
    coverage_id:
        Unique identifier for this coverage record.
    covered_domains:
        Tuple of domain names that have at least one idea in the portfolio.
    coverage_density:
        Fraction of the target idea space that is covered (0–1).
        Estimated from the ratio of covered domains to known domains.
    gap_regions:
        Tuple of domain names or region descriptors with insufficient coverage.
    uniformity_score:
        Measure of how evenly ideas are distributed across domains (0–1).
        1.0 means perfectly uniform; 0.0 means all ideas in one domain.
    total_ideas:
        Total number of ideas in the portfolio at the time of this snapshot.
    timestamp:
        ISO-8601 UTC timestamp of when this coverage was computed.
    """

    coverage_id: str
    covered_domains: tuple[str, ...]
    coverage_density: float
    gap_regions: tuple[str, ...]
    uniformity_score: float
    total_ideas: int
    timestamp: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        """Normalize and validate fields."""
        object.__setattr__(self, "coverage_id", _normalize(self.coverage_id).strip())
        if not self.coverage_id:
            object.__setattr__(self, "coverage_id", str(uuid.uuid4()))

        object.__setattr__(
            self, "coverage_density", _clamp(self.coverage_density)
        )
        object.__setattr__(
            self, "uniformity_score", _clamp(self.uniformity_score)
        )

        # Normalize domain and gap strings
        normed_domains = tuple(_normalize(d) for d in self.covered_domains if d.strip())
        normed_gaps = tuple(_normalize(g) for g in self.gap_regions if g.strip())
        object.__setattr__(self, "covered_domains", normed_domains)
        object.__setattr__(self, "gap_regions", normed_gaps)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def gap_count(self) -> int:
        """Number of identified gap regions."""
        return len(self.gap_regions)

    @property
    def domain_count(self) -> int:
        """Number of covered domains."""
        return len(self.covered_domains)

    @property
    def is_well_covered(self) -> bool:
        """Return True if both density and uniformity exceed healthy thresholds.

        Thresholds: density > 0.7 and uniformity > 0.6.  These values are
        chosen conservatively to flag portfolios that need more diverse ideas.
        """
        return self.coverage_density > 0.7 and self.uniformity_score > 0.6

    @property
    def coverage_score(self) -> float:
        """Composite coverage score combining density and uniformity.

        Computed as ``0.6 * density + 0.4 * uniformity``.  The higher weight
        on density reflects that having broad coverage is more important than
        perfect uniformity.
        """
        return _clamp(0.6 * self.coverage_density + 0.4 * self.uniformity_score)

    # ------------------------------------------------------------------
    # Domain operations
    # ------------------------------------------------------------------

    def gain_if_added(self, domain: str) -> float:
        """Estimate the coverage gain if *domain* were added to the portfolio.

        The estimate is heuristic:
        - If the domain is already covered, gain is 0.0.
        - If the domain fills a known gap, gain is higher (0.15).
        - Otherwise gain is a base increment of 0.08 (diminishing as density
          increases).

        Returns
        -------
        float
            Estimated coverage density gain in [0, 1].
        """
        domain_norm = _normalize(domain)
        if domain_norm in self.covered_domains:
            return 0.0  # already covered
        if domain_norm in self.gap_regions:
            # Filling a known gap has higher marginal value
            base_gain = 0.15
        else:
            base_gain = 0.08
        # Diminishing returns as density increases
        return _clamp(base_gain * (1.0 - self.coverage_density))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain Python dict."""
        return {
            "coverage_id": self.coverage_id,
            "covered_domains": list(self.covered_domains),
            "coverage_density": self.coverage_density,
            "gap_regions": list(self.gap_regions),
            "uniformity_score": self.uniformity_score,
            "total_ideas": self.total_ideas,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PortfolioCoverage":
        """Construct a PortfolioCoverage from a plain Python dict."""
        return cls(
            coverage_id=str(d.get("coverage_id", str(uuid.uuid4()))),
            covered_domains=tuple(d.get("covered_domains", [])),
            coverage_density=float(d.get("coverage_density", 0.0)),
            gap_regions=tuple(d.get("gap_regions", [])),
            uniformity_score=float(d.get("uniformity_score", 0.0)),
            total_ideas=int(d.get("total_ideas", 0)),
            timestamp=str(d.get("timestamp", _now_iso())),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "PortfolioCoverage":
        """Construct a PortfolioCoverage from a JSON string."""
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, other: "PortfolioCoverage") -> "PortfolioCoverage":
        """Merge two PortfolioCoverage records into a combined view.

        The merged record:
        - Combines covered_domains (union)
        - Averages coverage_density and uniformity_score
        - Reduces gap_regions to only those still absent from the merged domains
        - Sums total_ideas
        - Uses the more recent timestamp

        Parameters
        ----------
        other:
            The other coverage record to merge with.

        Returns
        -------
        PortfolioCoverage
            A new record representing the merged view.
        """
        merged_domains = tuple(
            sorted(set(self.covered_domains) | set(other.covered_domains))
        )
        merged_density = _clamp(
            (self.coverage_density + other.coverage_density) / 2.0
        )
        merged_uniformity = _clamp(
            (self.uniformity_score + other.uniformity_score) / 2.0
        )
        merged_gaps = tuple(
            g
            for g in sorted(set(self.gap_regions) | set(other.gap_regions))
            if g not in set(merged_domains)
        )
        merged_total = self.total_ideas + other.total_ideas

        # Use the more recent timestamp
        ts = max(self.timestamp, other.timestamp)

        return PortfolioCoverage(
            coverage_id=str(uuid.uuid4()),
            covered_domains=merged_domains,
            coverage_density=merged_density,
            gap_regions=merged_gaps,
            uniformity_score=merged_uniformity,
            total_ideas=merged_total,
            timestamp=ts,
        )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a multi-line human-readable description."""
        lines = [
            f"PortfolioCoverage: {self.coverage_id}",
            f"  Timestamp:         {self.timestamp}",
            f"  Total ideas:       {self.total_ideas}",
            f"  Covered domains:   {self.domain_count}  ({', '.join(self.covered_domains[:5])}{'…' if self.domain_count > 5 else ''})",
            f"  Coverage density:  {self.coverage_density:.3f}",
            f"  Uniformity score:  {self.uniformity_score:.3f}",
            f"  Composite score:   {self.coverage_score:.3f}",
            f"  Gap regions:       {self.gap_count}  ({', '.join(self.gap_regions[:5])}{'…' if self.gap_count > 5 else ''})",
            f"  Well covered:      {self.is_well_covered}",
        ]
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a compact single-line summary."""
        return (
            f"Coverage({self.coverage_id[:8]}…) "
            f"domains={self.domain_count} "
            f"density={self.coverage_density:.2f} "
            f"uniformity={self.uniformity_score:.2f} "
            f"gaps={self.gap_count}"
        )


# ---------------------------------------------------------------------------
# NoveltyMetricSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltyMetricSpec:
    """Specification for a distance metric used in novelty scoring.

    ``NoveltyMetricSpec`` is a *declarative specification* of a distance
    metric – it carries the metric's identity, purpose-sensitivity, and
    weight vector, but does not contain executable code.  The actual scorer
    (``jugeo.ideation.novelty.NoveltyMetric``) is constructed separately from
    this spec.

    This separation allows serialization, versioning, and exchange of metric
    configurations without importing or executing the full metric
    implementation.

    Attributes
    ----------
    metric_id:
        Stable identifier for this metric specification.
    name:
        Human-readable name, e.g. ``"semantic-cosine-v1"``.
    description:
        Description of what this metric measures and how.
    weight_vector:
        Per-dimension weights used in weighted cosine distance.  Normalized
        to sum to 1.0 during post-init (if non-empty and non-zero).
    purpose_sensitivity:
        How strongly the metric conditions on research purpose (0–1).
        Values > 0.3 make the metric purpose-sensitive.
    kind:
        ``MetricKind`` of this metric.
    version:
        Version string for the metric configuration.
    """

    metric_id: str
    name: str
    description: str
    weight_vector: tuple[float, ...]
    purpose_sensitivity: float
    kind: MetricKind = MetricKind.SEMANTIC
    version: str = "1.0"

    def __post_init__(self) -> None:
        """Normalize and validate fields."""
        object.__setattr__(self, "metric_id", _normalize(self.metric_id).strip())
        if not self.metric_id:
            object.__setattr__(self, "metric_id", str(uuid.uuid4()))

        object.__setattr__(self, "name", _normalize(self.name))
        object.__setattr__(self, "description", _normalize(self.description))
        object.__setattr__(
            self, "purpose_sensitivity", _clamp(self.purpose_sensitivity)
        )

        # Normalize weight vector so it sums to 1.0
        object.__setattr__(
            self, "weight_vector", _normalize_weights(self.weight_vector)
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dimension(self) -> int:
        """Dimensionality of the weight vector (0 if unspecified)."""
        return len(self.weight_vector)

    @property
    def is_purpose_sensitive(self) -> bool:
        """Return True if purpose_sensitivity > 0.3."""
        return self.purpose_sensitivity > 0.3

    @property
    def effective_weights(self) -> tuple[float, ...]:
        """Return the normalized weight vector (same as weight_vector after post-init)."""
        return self.weight_vector

    # ------------------------------------------------------------------
    # Derived variants
    # ------------------------------------------------------------------

    def with_weight_vector(
        self, wv: tuple[float, ...]
    ) -> "NoveltyMetricSpec":
        """Return a new spec with the given weight vector (normalized)."""
        d = self.to_dict()
        d["weight_vector"] = list(wv)
        return self.__class__.from_dict(d)

    # ------------------------------------------------------------------
    # Purpose scaling
    # ------------------------------------------------------------------

    def apply_purpose_scaling(
        self,
        purpose_tokens: frozenset[str],
        idea_tokens: frozenset[str],
    ) -> float:
        """Compute a purpose-conditioned scaling factor in [0, 1].

        Uses Jaccard similarity between purpose tokens and idea tokens,
        scaled by ``purpose_sensitivity``.

        A scaling factor of 1.0 means the metric is fully purpose-aligned;
        a factor < 1.0 penalizes ideas that diverge from the purpose.

        Parameters
        ----------
        purpose_tokens:
            Tokenized research purpose.
        idea_tokens:
            Tokenized idea content (title + hypothesis).

        Returns
        -------
        float
            Scaling factor in [0, 1].
        """
        if not self.is_purpose_sensitive:
            return 1.0  # No purpose conditioning applied
        sim = _jaccard(purpose_tokens, idea_tokens)
        # Blend between 1.0 (no scaling) and sim (full scaling)
        return _clamp(1.0 - self.purpose_sensitivity * (1.0 - sim))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain Python dict."""
        return {
            "metric_id": self.metric_id,
            "name": self.name,
            "description": self.description,
            "weight_vector": list(self.weight_vector),
            "purpose_sensitivity": self.purpose_sensitivity,
            "kind": self.kind.value,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "NoveltyMetricSpec":
        """Construct a NoveltyMetricSpec from a plain Python dict."""
        return cls(
            metric_id=str(d.get("metric_id", str(uuid.uuid4()))),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            weight_vector=tuple(float(w) for w in d.get("weight_vector", [])),
            purpose_sensitivity=float(d.get("purpose_sensitivity", 0.5)),
            kind=MetricKind(d.get("kind", "semantic")),
            version=str(d.get("version", "1.0")),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "NoveltyMetricSpec":
        """Construct a NoveltyMetricSpec from a JSON string."""
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a multi-line human-readable description."""
        lines = [
            f"NoveltyMetricSpec: {self.name}  (id={self.metric_id[:8]}…)",
            f"  Kind:                {self.kind.value}",
            f"  Version:             {self.version}",
            f"  Description:         {self.description}",
            f"  Dimension:           {self.dimension}",
            f"  Purpose sensitivity: {self.purpose_sensitivity:.3f}",
            f"  Purpose sensitive:   {self.is_purpose_sensitive}",
        ]
        if self.weight_vector:
            wv_preview = ", ".join(f"{w:.3f}" for w in self.weight_vector[:6])
            if self.dimension > 6:
                wv_preview += ", …"
            lines.append(f"  Weight vector:       [{wv_preview}]")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Record representing a single result from a novelty search run.

    Each SearchResult pairs a candidate idea with its novelty score, the
    estimated coverage gain it would provide to the portfolio, its
    feasibility score, and its rank in the result list.

    Attributes
    ----------
    result_id:
        Unique identifier for this result record.
    candidate_idea:
        The candidate ``Idea`` that was evaluated.
    novelty_score:
        Novelty score in [0, 1] from the distance metric.
    coverage_gain:
        Estimated fractional increase in portfolio coverage if this idea were
        added (0–1).
    feasibility:
        Estimated probability of successfully formalizing/proving this idea (0–1).
    rank:
        1-based rank in the result list (1 = best).
    strategy_used:
        The ``SearchStrategy`` that produced this result.
    timestamp:
        ISO-8601 UTC timestamp of when this result was generated.
    """

    result_id: str
    candidate_idea: Idea
    novelty_score: float
    coverage_gain: float
    feasibility: float
    rank: int
    strategy_used: SearchStrategy = SearchStrategy.GREEDY
    timestamp: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        """Normalize and validate fields."""
        object.__setattr__(self, "result_id", _normalize(self.result_id).strip())
        if not self.result_id:
            object.__setattr__(self, "result_id", str(uuid.uuid4()))

        object.__setattr__(self, "novelty_score", _clamp(self.novelty_score))
        object.__setattr__(self, "coverage_gain", _clamp(self.coverage_gain))
        object.__setattr__(self, "feasibility", _clamp(self.feasibility))

        if self.rank < 1:
            raise ValueError(f"rank must be >= 1, got {self.rank!r}")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def composite_score(self) -> float:
        """Composite quality score combining novelty, coverage gain, and feasibility.

        Weights:
        - Novelty:       0.40
        - Coverage gain: 0.35
        - Feasibility:   0.25

        The weighting reflects that novelty is the primary objective, coverage
        gain is secondary (ensuring portfolio diversity), and feasibility is a
        soft constraint rather than an optimisation objective.
        """
        return _clamp(
            0.40 * self.novelty_score
            + 0.35 * self.coverage_gain
            + 0.25 * self.feasibility
        )

    @property
    def is_high_quality(self) -> bool:
        """Return True if the composite score exceeds 0.6."""
        return self.composite_score > 0.6

    @property
    def idea_id(self) -> str:
        """Delegate to the candidate idea's ID."""
        return self.candidate_idea.idea_id

    # ------------------------------------------------------------------
    # Derived variants
    # ------------------------------------------------------------------

    def with_rank(self, rank: int) -> "SearchResult":
        """Return a new SearchResult with the given rank."""
        if rank < 1:
            raise ValueError(f"rank must be >= 1, got {rank!r}")
        d = self.to_dict()
        d["rank"] = rank
        idea = self.candidate_idea
        return SearchResult(
            result_id=d["result_id"],
            candidate_idea=idea,
            novelty_score=d["novelty_score"],
            coverage_gain=d["coverage_gain"],
            feasibility=d["feasibility"],
            rank=rank,
            strategy_used=self.strategy_used,
            timestamp=d["timestamp"],
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain Python dict.

        Note: ``candidate_idea`` is serialized via its own ``to_dict()`` method.
        """
        return {
            "result_id": self.result_id,
            "candidate_idea": self.candidate_idea.to_dict(),
            "novelty_score": self.novelty_score,
            "coverage_gain": self.coverage_gain,
            "feasibility": self.feasibility,
            "rank": self.rank,
            "strategy_used": self.strategy_used.value,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "SearchResult":
        """Construct a SearchResult from a plain Python dict."""
        return cls(
            result_id=str(d.get("result_id", str(uuid.uuid4()))),
            candidate_idea=Idea.from_dict(d["candidate_idea"]),
            novelty_score=float(d.get("novelty_score", 0.0)),
            coverage_gain=float(d.get("coverage_gain", 0.0)),
            feasibility=float(d.get("feasibility", 0.0)),
            rank=int(d.get("rank", 1)),
            strategy_used=SearchStrategy(d.get("strategy_used", "greedy")),
            timestamp=str(d.get("timestamp", _now_iso())),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "SearchResult":
        """Construct a SearchResult from a JSON string."""
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a multi-line human-readable description."""
        lines = [
            f"SearchResult #{self.rank}: {self.result_id[:8]}…",
            f"  Idea:            {self.candidate_idea.idea_id} – {self.candidate_idea.title!r}",
            f"  Novelty score:   {self.novelty_score:.3f}",
            f"  Coverage gain:   {self.coverage_gain:.3f}",
            f"  Feasibility:     {self.feasibility:.3f}",
            f"  Composite score: {self.composite_score:.3f}",
            f"  High quality:    {self.is_high_quality}",
            f"  Strategy:        {self.strategy_used.value}",
            f"  Timestamp:       {self.timestamp}",
        ]
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a compact single-line summary."""
        return (
            f"Result#{self.rank}({self.result_id[:8]}…) "
            f"idea={self.idea_id[:8]}… "
            f"novelty={self.novelty_score:.2f} "
            f"composite={self.composite_score:.2f}"
        )


# ---------------------------------------------------------------------------
# DiversityConstraint
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiversityConstraint:
    """Specification of diversity requirements for a novelty search result set.

    A ``DiversityConstraint`` expresses structural requirements that the final
    result set must satisfy.  These constraints are checked *post-hoc* by the
    ``is_satisfied_by`` method and can be enforced by result-set filtering or
    re-ranking in the search loop.

    Attributes
    ----------
    constraint_id:
        Stable identifier for this constraint specification.
    min_pairwise_distance:
        Minimum novelty-score distance between any two results (0–1).
        Enforces that results are not too similar to each other.
    max_cluster_size:
        Maximum number of results from the same domain/cluster (≥ 1).
        Limits domain over-concentration.
    domains_required:
        Tuple of domain names that must each have at least one result.
    purpose_spread:
        Minimum required spread of purpose alignment scores across results
        (0–1).  High spread means results span a range of purpose alignments.
    """

    constraint_id: str
    min_pairwise_distance: float
    max_cluster_size: int
    domains_required: tuple[str, ...]
    purpose_spread: float

    def __post_init__(self) -> None:
        """Normalize and validate fields."""
        object.__setattr__(
            self, "constraint_id", _normalize(self.constraint_id).strip()
        )
        if not self.constraint_id:
            object.__setattr__(self, "constraint_id", str(uuid.uuid4()))

        object.__setattr__(
            self,
            "min_pairwise_distance",
            _clamp(self.min_pairwise_distance),
        )
        object.__setattr__(
            self, "purpose_spread", _clamp(self.purpose_spread)
        )

        if self.max_cluster_size < 1:
            raise ValueError(
                f"max_cluster_size must be >= 1, got {self.max_cluster_size!r}"
            )

        normed = tuple(_normalize(d) for d in self.domains_required if d.strip())
        object.__setattr__(self, "domains_required", normed)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_strict(self) -> bool:
        """Return True if this is a strict constraint (min_pairwise_distance > 0.5)."""
        return self.min_pairwise_distance > 0.5

    @property
    def required_domain_count(self) -> int:
        """Number of domains that must be represented in the result set."""
        return len(self.domains_required)

    @property
    def allows_clustering(self) -> bool:
        """Return True if more than one result per domain/cluster is allowed."""
        return self.max_cluster_size > 1

    # ------------------------------------------------------------------
    # Constraint checking
    # ------------------------------------------------------------------

    def is_satisfied_by(self, results: Sequence["SearchResult"]) -> bool:
        """Return True if *results* satisfies all diversity constraints.

        Checks performed:
        1. All required domains have at least one result.
        2. No domain has more than ``max_cluster_size`` results.
        3. The minimum pairwise novelty-score distance constraint is met.
        4. Purpose spread constraint is met (range of novelty scores ≥ purpose_spread).

        Parameters
        ----------
        results:
            The result set to check.

        Returns
        -------
        bool
            True iff no violation is found.
        """
        return len(self.violations(results)) == 0

    def violations(self, results: Sequence["SearchResult"]) -> list[str]:
        """Return a list of constraint violation messages for *results*.

        Returns an empty list if all constraints are satisfied.

        Parameters
        ----------
        results:
            The result set to check.

        Returns
        -------
        list[str]
            Human-readable violation messages (empty = no violations).
        """
        msgs: list[str] = []

        if not results:
            if self.domains_required:
                msgs.append(
                    "Result set is empty but required domains were specified: "
                    + ", ".join(self.domains_required)
                )
            return msgs

        # 1. Required domains
        covered_domains: set[str] = set()
        for r in results:
            covered_domains.add(_normalize(r.candidate_idea.target_area))

        for domain in self.domains_required:
            if domain not in covered_domains:
                msgs.append(
                    f"Required domain {domain!r} is not represented in the result set."
                )

        # 2. Max cluster size (by target_area)
        domain_counts: dict[str, int] = defaultdict(int)
        for r in results:
            domain_counts[_normalize(r.candidate_idea.target_area)] += 1
        for domain, count in domain_counts.items():
            if count > self.max_cluster_size:
                msgs.append(
                    f"Domain {domain!r} has {count} results, "
                    f"exceeding max_cluster_size={self.max_cluster_size}."
                )

        # 3. Pairwise novelty distance
        if self.min_pairwise_distance > 0 and len(results) >= 2:
            scores = [r.novelty_score for r in results]
            for i in range(len(scores)):
                for j in range(i + 1, len(scores)):
                    dist = abs(scores[i] - scores[j])
                    if dist < self.min_pairwise_distance:
                        msgs.append(
                            f"Results #{results[i].rank} and #{results[j].rank} "
                            f"have pairwise novelty distance {dist:.3f} "
                            f"< min_pairwise_distance={self.min_pairwise_distance:.3f}."
                        )
                        break  # Report at most one violation per pair
                else:
                    continue
                break

        # 4. Purpose spread (score range)
        if self.purpose_spread > 0 and len(results) >= 2:
            scores = [r.novelty_score for r in results]
            actual_spread = max(scores) - min(scores)
            if actual_spread < self.purpose_spread:
                msgs.append(
                    f"Novelty score spread {actual_spread:.3f} "
                    f"< required purpose_spread={self.purpose_spread:.3f}."
                )

        return msgs

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain Python dict."""
        return {
            "constraint_id": self.constraint_id,
            "min_pairwise_distance": self.min_pairwise_distance,
            "max_cluster_size": self.max_cluster_size,
            "domains_required": list(self.domains_required),
            "purpose_spread": self.purpose_spread,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DiversityConstraint":
        """Construct a DiversityConstraint from a plain Python dict."""
        return cls(
            constraint_id=str(d.get("constraint_id", str(uuid.uuid4()))),
            min_pairwise_distance=float(d.get("min_pairwise_distance", 0.0)),
            max_cluster_size=int(d.get("max_cluster_size", 10)),
            domains_required=tuple(d.get("domains_required", [])),
            purpose_spread=float(d.get("purpose_spread", 0.0)),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "DiversityConstraint":
        """Construct a DiversityConstraint from a JSON string."""
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a multi-line human-readable description."""
        lines = [
            f"DiversityConstraint: {self.constraint_id[:8]}…",
            f"  Strict:                  {self.is_strict}",
            f"  Min pairwise distance:   {self.min_pairwise_distance:.3f}",
            f"  Max cluster size:        {self.max_cluster_size}",
            f"  Allows clustering:       {self.allows_clustering}",
            f"  Required domains ({self.required_domain_count}):  "
            + (", ".join(self.domains_required) if self.domains_required else "(none)"),
            f"  Purpose spread:          {self.purpose_spread:.3f}",
        ]
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a compact single-line summary."""
        return (
            f"Constraint({self.constraint_id[:8]}…) "
            f"min_dist={self.min_pairwise_distance:.2f} "
            f"max_cluster={self.max_cluster_size} "
            f"domains={self.required_domain_count} "
            f"strict={self.is_strict}"
        )


# ---------------------------------------------------------------------------
# Module-level factory functions
# ---------------------------------------------------------------------------


def make_default_problem(
    purpose: str, portfolio_ids: Sequence[str]
) -> NoveltySearchProblem:
    """Create a NoveltySearchProblem with sensible defaults.

    Parameters
    ----------
    purpose:
        Research purpose string.
    portfolio_ids:
        Sequence of idea/theorem IDs representing the current portfolio.

    Returns
    -------
    NoveltySearchProblem
        A problem with GREEDY strategy, SEMANTIC metric, budget=100, and
        standard diversity and feasibility settings.

    Example
    -------
    ::

        problem = make_default_problem(
            purpose="Develop new proof techniques for dependent type theory",
            portfolio_ids=["thm-001", "thm-002"],
        )
    """
    return NoveltySearchProblem(
        problem_id=str(uuid.uuid4()),
        portfolio_snapshot=tuple(portfolio_ids),
        purpose=purpose,
        budget=100.0,
        diversity_weight=0.5,
        feasibility_threshold=0.1,
        max_results=10,
        strategy=SearchStrategy.GREEDY,
        metric_kind=MetricKind.SEMANTIC,
    )


def make_coverage_from_ideas(ideas: Sequence[Idea]) -> PortfolioCoverage:
    """Build a PortfolioCoverage snapshot from a sequence of ideas.

    Extracts domain names from ``idea.target_area``, computes coverage
    density as the ratio of distinct domains to a reference total of 20
    (a heuristic upper bound for typical jugeo problem spaces), and
    computes uniformity using normalized Shannon entropy.

    Parameters
    ----------
    ideas:
        Sequence of ``Idea`` instances to analyse.

    Returns
    -------
    PortfolioCoverage
        Coverage record reflecting the current portfolio state.

    Example
    -------
    ::

        from jugeo.ideation.novelty_search.models import make_coverage_from_ideas
        coverage = make_coverage_from_ideas(portfolio.shortlist())
    """
    if not ideas:
        return PortfolioCoverage(
            coverage_id=str(uuid.uuid4()),
            covered_domains=(),
            coverage_density=0.0,
            gap_regions=(),
            uniformity_score=0.0,
            total_ideas=0,
        )

    # Count ideas per domain
    domain_counts: dict[str, int] = defaultdict(int)
    for idea in ideas:
        domain = _normalize(idea.target_area) or "unknown"
        domain_counts[domain] += 1

    covered = tuple(sorted(domain_counts))
    total = len(ideas)
    n_domains = len(covered)

    # Coverage density: domains covered vs. reference total of 20
    ref_total = max(n_domains, 20)
    density = _clamp(n_domains / ref_total)

    # Uniformity: normalized Shannon entropy
    if n_domains <= 1:
        uniformity = 1.0 if n_domains == 1 else 0.0
    else:
        max_entropy = math.log2(n_domains)
        entropy = -sum(
            (c / total) * math.log2(c / total)
            for c in domain_counts.values()
            if c > 0
        )
        uniformity = _clamp(entropy / max_entropy if max_entropy > 0 else 0.0)

    # Gap regions: domains with only one idea (under-represented)
    gaps = tuple(
        d for d, cnt in sorted(domain_counts.items()) if cnt == 1
    )

    return PortfolioCoverage(
        coverage_id=str(uuid.uuid4()),
        covered_domains=covered,
        coverage_density=density,
        gap_regions=gaps,
        uniformity_score=uniformity,
        total_ideas=total,
    )


def make_default_metric_spec(
    kind: MetricKind = MetricKind.SEMANTIC,
) -> NoveltyMetricSpec:
    """Create a NoveltyMetricSpec with standard defaults for the given kind.

    The weight vector is left empty (dimension 0), meaning the scorer will
    use uniform weights by default.  Purpose sensitivity is set to 0.5 for
    SEMANTIC and HYBRID, 0.0 for STRUCTURAL and TOPOLOGICAL.

    Parameters
    ----------
    kind:
        The metric kind to create a spec for.

    Returns
    -------
    NoveltyMetricSpec
        A default spec for the given metric kind.

    Example
    -------
    ::

        spec = make_default_metric_spec(MetricKind.HYBRID)
    """
    purpose_sensitivity_map: dict[MetricKind, float] = {
        MetricKind.SEMANTIC: 0.5,
        MetricKind.STRUCTURAL: 0.0,
        MetricKind.TOPOLOGICAL: 0.0,
        MetricKind.HYBRID: 0.5,
    }
    descriptions: dict[MetricKind, str] = {
        MetricKind.SEMANTIC: (
            "Default semantic cosine distance metric conditioned on research purpose."
        ),
        MetricKind.STRUCTURAL: (
            "Default structural graph-edit-distance metric (no purpose conditioning)."
        ),
        MetricKind.TOPOLOGICAL: (
            "Default topological persistence metric (no purpose conditioning)."
        ),
        MetricKind.HYBRID: (
            "Default hybrid metric combining semantic and structural components."
        ),
    }
    return NoveltyMetricSpec(
        metric_id=str(uuid.uuid4()),
        name=f"default-{kind.value}-metric",
        description=descriptions.get(kind, f"Default {kind.value} metric."),
        weight_vector=(),
        purpose_sensitivity=purpose_sensitivity_map.get(kind, 0.0),
        kind=kind,
        version="1.0",
    )


def make_loose_constraint() -> DiversityConstraint:
    """Create a lenient DiversityConstraint with minimal requirements.

    A loose constraint is suitable for exploratory search where diversity is
    desirable but not strictly enforced.  Settings:

    - min_pairwise_distance = 0.05 (almost no minimum distance required)
    - max_cluster_size = 5 (up to 5 results per domain)
    - domains_required = () (no required domains)
    - purpose_spread = 0.1 (minimal spread required)

    Returns
    -------
    DiversityConstraint
        A lenient constraint instance.

    Example
    -------
    ::

        constraint = make_loose_constraint()
        assert not constraint.is_strict
    """
    return DiversityConstraint(
        constraint_id=str(uuid.uuid4()),
        min_pairwise_distance=0.05,
        max_cluster_size=5,
        domains_required=(),
        purpose_spread=0.1,
    )


def make_strict_constraint() -> DiversityConstraint:
    """Create a strict DiversityConstraint enforcing strong diversity.

    A strict constraint is appropriate for curated result sets where results
    must be clearly distinct and broadly spread.  Settings:

    - min_pairwise_distance = 0.6 (results must be well-separated)
    - max_cluster_size = 1 (one result per domain/cluster)
    - domains_required = () (caller may add specific domains)
    - purpose_spread = 0.4 (substantial spread of scores required)

    Returns
    -------
    DiversityConstraint
        A strict constraint instance.

    Example
    -------
    ::

        constraint = make_strict_constraint()
        assert constraint.is_strict
        assert not constraint.allows_clustering
    """
    return DiversityConstraint(
        constraint_id=str(uuid.uuid4()),
        min_pairwise_distance=0.6,
        max_cluster_size=1,
        domains_required=(),
        purpose_spread=0.4,
    )
