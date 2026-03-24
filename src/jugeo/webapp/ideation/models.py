"""Ideation domain models for the jugeo webapp.

Standalone module – no jugeo imports, Python stdlib only.
Defines the enums and dataclasses used throughout the ideation
pipeline: coordinate taxonomy (§5.2), gap analysis, idea proposals,
validation, ranking, and the top-level IdeationResult container.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ApplicationCoordinate(str, Enum):
    """Twenty-two application-space coordinates from §5.2."""

    DATA_INGESTION = "DATA_INGESTION"
    DATA_TRANSFORMATION = "DATA_TRANSFORMATION"
    DATA_VISUALIZATION = "DATA_VISUALIZATION"
    DATA_EXPORT = "DATA_EXPORT"
    COMPUTATION_ON_DEMAND = "COMPUTATION_ON_DEMAND"
    BATCH_PROCESSING = "BATCH_PROCESSING"
    COMPARISON = "COMPARISON"
    AGGREGATION = "AGGREGATION"
    FORM_WORKFLOW = "FORM_WORKFLOW"
    FILE_PROCESSING = "FILE_PROCESSING"
    REAL_TIME_FEEDBACK = "REAL_TIME_FEEDBACK"
    COLLABORATIVE_EDITING = "COLLABORATIVE_EDITING"
    SCHEDULING = "SCHEDULING"
    INVENTORY = "INVENTORY"
    MATCHING = "MATCHING"
    SIMULATION = "SIMULATION"
    AUDIT_TRAIL = "AUDIT_TRAIL"
    CONSTRAINT_SATISFACTION = "CONSTRAINT_SATISFACTION"
    STATIC_REPORT = "STATIC_REPORT"
    INTERACTIVE_DASHBOARD = "INTERACTIVE_DASHBOARD"
    NOTIFICATION = "NOTIFICATION"
    API_PROVISION = "API_PROVISION"


class GapType(str, Enum):
    """Classification of a gap in the application landscape."""

    UNSERVED = "UNSERVED"
    UNDERSERVED = "UNDERSERVED"
    WRONG_METHOD = "WRONG_METHOD"
    WRONG_AUDIENCE = "WRONG_AUDIENCE"
    DISCONTINUED = "DISCONTINUED"


class IdeaSource(str, Enum):
    """How an idea was generated."""

    GAP_DETECTION = "GAP_DETECTION"
    ANALOGY_TRANSPORT = "ANALOGY_TRANSPORT"
    INTERSECTION_DETECTION = "INTERSECTION_DETECTION"
    MANUAL = "MANUAL"


class ValidationStatus(str, Enum):
    """Outcome of validating a proposed idea."""

    VALIDATED = "VALIDATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    UNCERTAIN = "UNCERTAIN"
    INFEASIBLE = "INFEASIBLE"
    OBSTACLE_FOUND = "OBSTACLE_FOUND"


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _coord_set_to_list(coords: set[ApplicationCoordinate]) -> list[str]:
    """Serialize a set of coordinates to a deterministic sorted list."""
    return sorted(c.value for c in coords)


def _list_to_coord_set(items: list[str]) -> set[ApplicationCoordinate]:
    """Deserialize a list of coordinate strings back to a set."""
    return {ApplicationCoordinate(v) for v in items}


def _coord_tuple_to_list(coords: tuple[ApplicationCoordinate, ...]) -> list[str]:
    """Serialize a tuple of coordinates to a list (preserving order)."""
    return [c.value for c in coords]


def _list_to_coord_tuple(items: list[str]) -> tuple[ApplicationCoordinate, ...]:
    """Deserialize a list of coordinate strings back to a tuple."""
    return tuple(ApplicationCoordinate(v) for v in items)


def _tuple_key_to_str(key: tuple) -> str:
    """Convert a tuple key to a string for JSON-safe dict keys."""
    return "|".join(str(k) for k in key)


def _str_to_tuple_key(key_str: str) -> tuple:
    """Convert a string key back to a tuple of strings."""
    if not key_str:
        return ()
    return tuple(key_str.split("|"))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AppIdeationPurpose:
    """Captures *why* we are running the ideation pipeline.

    Parameters
    ----------
    domain : str
        The domain of interest (e.g. "legal-tech").
    user_population : str
        Target user group description.
    constraint_tags : tuple
        Freeform tags that constrain the search.
    value_axis : str
        Which value metric to optimise for.
    leverage_weight : float
        Weight for the leverage factor (sums to 1 with the others).
    tractability_weight : float
        Weight for the tractability factor.
    relevance_weight : float
        Weight for the relevance factor.
    """

    domain: str
    user_population: str
    constraint_tags: tuple = field(default_factory=tuple)
    value_axis: str = "user_hours_saved"
    leverage_weight: float = 0.35
    tractability_weight: float = 0.30
    relevance_weight: float = 0.35

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "domain": self.domain,
            "user_population": self.user_population,
            "constraint_tags": list(self.constraint_tags),
            "value_axis": self.value_axis,
            "leverage_weight": self.leverage_weight,
            "tractability_weight": self.tractability_weight,
            "relevance_weight": self.relevance_weight,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppIdeationPurpose:
        """Reconstruct from a plain dict."""
        return cls(
            domain=data["domain"],
            user_population=data["user_population"],
            constraint_tags=tuple(data.get("constraint_tags", ())),
            value_axis=data.get("value_axis", "user_hours_saved"),
            leverage_weight=float(data.get("leverage_weight", 0.35)),
            tractability_weight=float(data.get("tractability_weight", 0.30)),
            relevance_weight=float(data.get("relevance_weight", 0.35)),
        )


@dataclass
class ExistingApp:
    """An application that already exists in the landscape.

    Parameters
    ----------
    name : str
        Human-readable application name.
    url : str
        Primary URL for the application.
    description : str
        Brief description of what the application does.
    coordinates : set
        The application-space coordinates this app covers.
    quality_tier : str
        One of ``"high"``, ``"medium"``, ``"low"``.
    user_base_estimate : int
        Rough number of active users.
    """

    name: str
    url: str
    description: str
    coordinates: set  # set[ApplicationCoordinate]
    quality_tier: str = "medium"
    user_base_estimate: int = 0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation.

        Coordinates are stored as a deterministic sorted list of strings.
        """
        return {
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "coordinates": _coord_set_to_list(self.coordinates),
            "quality_tier": self.quality_tier,
            "user_base_estimate": self.user_base_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExistingApp:
        """Reconstruct from a plain dict."""
        return cls(
            name=data["name"],
            url=data["url"],
            description=data["description"],
            coordinates=_list_to_coord_set(data.get("coordinates", [])),
            quality_tier=data.get("quality_tier", "medium"),
            user_base_estimate=int(data.get("user_base_estimate", 0)),
        )


@dataclass
class IdeaPortfolio:
    """A collection of existing applications forming the current landscape.

    Parameters
    ----------
    ideas : list
        The existing applications in this portfolio.
    domain : str
        Domain label for the portfolio.
    construction_method : str
        How the portfolio was assembled (``"builtin"``, ``"scraped"``, etc.).
    """

    ideas: list  # list[ExistingApp]
    domain: str = ""
    construction_method: str = "builtin"

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation.

        Each :class:`ExistingApp` in *ideas* is recursively serialised.
        """
        return {
            "ideas": [app.to_dict() for app in self.ideas],
            "domain": self.domain,
            "construction_method": self.construction_method,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdeaPortfolio:
        """Reconstruct from a plain dict, including nested ExistingApp objects."""
        ideas = [ExistingApp.from_dict(item) for item in data.get("ideas", [])]
        return cls(
            ideas=ideas,
            domain=data.get("domain", ""),
            construction_method=data.get("construction_method", "builtin"),
        )


@dataclass
class Gap:
    """A detected gap in the application landscape.

    Parameters
    ----------
    coordinates : tuple
        The coordinate combination where the gap was found.
    coverage : float
        Current coverage level (0.0 – 1.0) at this coordinate combination.
    gap_type : GapType
        Classification of the gap.
    description : str
        Human-readable explanation.
    """

    coordinates: tuple  # tuple[ApplicationCoordinate, ...]
    coverage: float
    gap_type: GapType
    description: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "coordinates": _coord_tuple_to_list(self.coordinates),
            "coverage": self.coverage,
            "gap_type": self.gap_type.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Gap:
        """Reconstruct from a plain dict."""
        return cls(
            coordinates=_list_to_coord_tuple(data.get("coordinates", [])),
            coverage=float(data.get("coverage", 0.0)),
            gap_type=GapType(data["gap_type"]),
            description=data.get("description", ""),
        )


@dataclass
class CoverageReport:
    """Full coverage analysis of the application landscape.

    Parameters
    ----------
    coordinate_coverage : dict
        Mapping from coordinate tuples to coverage floats.
    need_coverage : dict
        Mapping from need labels to coverage floats.
    quality_coverage : dict
        Mapping from quality tier labels to coverage floats.
    gaps : list
        Detected gaps.
    """

    coordinate_coverage: dict  # dict[tuple, float]
    need_coverage: dict  # dict[str, float]
    quality_coverage: dict  # dict[str, float]
    gaps: list  # list[Gap]

    # -- helpers -------------------------------------------------------------

    def coverage_at(self, coord_tuple: tuple) -> float:
        """Return the coverage at a specific coordinate tuple.

        If the exact tuple is not present, returns ``0.0``.
        """
        return self.coordinate_coverage.get(coord_tuple, 0.0)

    def gap_count(self) -> int:
        """Return the number of detected gaps."""
        return len(self.gaps)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation.

        Tuple keys in *coordinate_coverage* are converted to pipe-delimited
        strings so the result is JSON-safe.
        """
        serialised_coord_cov: dict[str, float] = {}
        for key, value in self.coordinate_coverage.items():
            str_key = _tuple_key_to_str(key)
            serialised_coord_cov[str_key] = value

        return {
            "coordinate_coverage": serialised_coord_cov,
            "need_coverage": dict(self.need_coverage),
            "quality_coverage": dict(self.quality_coverage),
            "gaps": [g.to_dict() for g in self.gaps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageReport:
        """Reconstruct from a plain dict.

        Pipe-delimited string keys in *coordinate_coverage* are converted
        back to tuples.
        """
        coord_cov: dict[tuple, float] = {}
        for str_key, value in data.get("coordinate_coverage", {}).items():
            coord_cov[_str_to_tuple_key(str_key)] = float(value)

        gaps = [Gap.from_dict(g) for g in data.get("gaps", [])]

        return cls(
            coordinate_coverage=coord_cov,
            need_coverage={k: float(v) for k, v in data.get("need_coverage", {}).items()},
            quality_coverage={k: float(v) for k, v in data.get("quality_coverage", {}).items()},
            gaps=gaps,
        )


@dataclass
class GainProfile:
    """Quantitative gain profile for an idea.

    Parameters
    ----------
    theorem_yield : float
        Analogous value delivery metric.
    bridge_impact : float
        How effectively the idea bridges detected gaps.
    cost : float
        Estimated development cost in person-hours.
    uncertainty : float
        Uncertainty factor in the range ``[0, 1]``.
    """

    theorem_yield: float
    bridge_impact: float
    cost: float
    uncertainty: float

    # -- helpers -------------------------------------------------------------

    def roi(self) -> float:
        """Return the return-on-investment ratio.

        Computed as ``bridge_impact / (cost + ε)`` where *ε* = 1e-9
        to avoid division by zero.
        """
        return self.bridge_impact / (self.cost + 1e-9)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "theorem_yield": self.theorem_yield,
            "bridge_impact": self.bridge_impact,
            "cost": self.cost,
            "uncertainty": self.uncertainty,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GainProfile:
        """Reconstruct from a plain dict."""
        return cls(
            theorem_yield=float(data["theorem_yield"]),
            bridge_impact=float(data["bridge_impact"]),
            cost=float(data["cost"]),
            uncertainty=float(data["uncertainty"]),
        )


@dataclass
class IdeaProposal:
    """A concrete idea proposed by the ideation pipeline.

    Parameters
    ----------
    id : str
        Unique identifier (UUID4 hex string).
    title : str
        Short human-readable title.
    hypothesis : str
        The core hypothesis motivating this idea.
    target_area : str
        Which area / sub-domain this idea targets.
    coordinates : set
        The application-space coordinates this idea would occupy.
    gain : GainProfile
        Quantitative gain profile.
    source : IdeaSource
        How the idea was generated.
    analogy_source : str or None
        If generated via analogy, the source domain / app.
    analogy_fidelity : float
        How faithful the analogy is (0.0 – 1.0).
    feasibility_score : float
        Estimated feasibility (0.0 – 1.0).
    novelty_score : float
        Estimated novelty (0.0 – 1.0).
    """

    id: str
    title: str
    hypothesis: str
    target_area: str
    coordinates: set  # set[ApplicationCoordinate]
    gain: GainProfile
    source: IdeaSource
    analogy_source: Optional[str] = None
    analogy_fidelity: float = 0.0
    feasibility_score: float = 0.5
    novelty_score: float = 0.5

    # -- factory -------------------------------------------------------------

    @classmethod
    def create(
        cls,
        title: str,
        hypothesis: str,
        target_area: str,
        coordinates: set,
        gain: GainProfile,
        source: IdeaSource,
        *,
        analogy_source: Optional[str] = None,
        analogy_fidelity: float = 0.0,
        feasibility_score: float = 0.5,
        novelty_score: float = 0.5,
    ) -> IdeaProposal:
        """Create a new :class:`IdeaProposal` with an auto-generated UUID."""
        return cls(
            id=uuid.uuid4().hex,
            title=title,
            hypothesis=hypothesis,
            target_area=target_area,
            coordinates=coordinates,
            gain=gain,
            source=source,
            analogy_source=analogy_source,
            analogy_fidelity=analogy_fidelity,
            feasibility_score=feasibility_score,
            novelty_score=novelty_score,
        )

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation.

        Coordinates are stored as a deterministic sorted list of strings.
        """
        return {
            "id": self.id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "target_area": self.target_area,
            "coordinates": _coord_set_to_list(self.coordinates),
            "gain": self.gain.to_dict(),
            "source": self.source.value,
            "analogy_source": self.analogy_source,
            "analogy_fidelity": self.analogy_fidelity,
            "feasibility_score": self.feasibility_score,
            "novelty_score": self.novelty_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdeaProposal:
        """Reconstruct from a plain dict, including nested GainProfile."""
        return cls(
            id=data["id"],
            title=data["title"],
            hypothesis=data["hypothesis"],
            target_area=data["target_area"],
            coordinates=_list_to_coord_set(data.get("coordinates", [])),
            gain=GainProfile.from_dict(data["gain"]),
            source=IdeaSource(data["source"]),
            analogy_source=data.get("analogy_source"),
            analogy_fidelity=float(data.get("analogy_fidelity", 0.0)),
            feasibility_score=float(data.get("feasibility_score", 0.5)),
            novelty_score=float(data.get("novelty_score", 0.5)),
        )


@dataclass
class ValidationResult:
    """Outcome of validating an :class:`IdeaProposal`.

    Parameters
    ----------
    status : ValidationStatus
        High-level verdict.
    confidence : float
        How confident we are in the verdict (0.0 – 1.0).
    demand_signals : list
        Evidence of demand (e.g. forum posts, search volume).
    known_obstacles : list
        Known blockers or risks.
    partial_solutions : list
        Existing partial solutions that could be leveraged.
    recommendation : str
        Actionable recommendation text.
    """

    status: ValidationStatus
    confidence: float
    demand_signals: list  # list[str]
    known_obstacles: list  # list[str]
    partial_solutions: list  # list[str]
    recommendation: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "status": self.status.value,
            "confidence": self.confidence,
            "demand_signals": list(self.demand_signals),
            "known_obstacles": list(self.known_obstacles),
            "partial_solutions": list(self.partial_solutions),
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidationResult:
        """Reconstruct from a plain dict."""
        return cls(
            status=ValidationStatus(data["status"]),
            confidence=float(data["confidence"]),
            demand_signals=list(data.get("demand_signals", [])),
            known_obstacles=list(data.get("known_obstacles", [])),
            partial_solutions=list(data.get("partial_solutions", [])),
            recommendation=data.get("recommendation", ""),
        )


@dataclass
class RankedIdea:
    """An :class:`IdeaProposal` together with its ranking metadata.

    Parameters
    ----------
    idea : IdeaProposal
        The underlying idea.
    marginal_value : float
        Marginal value this idea adds to the portfolio.
    final_score : float
        Composite score used for the final ranking.
    ranking_components : dict
        Breakdown of the score into named components.
    """

    idea: IdeaProposal
    marginal_value: float
    final_score: float
    ranking_components: dict  # dict[str, float]

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation.

        The nested :class:`IdeaProposal` is recursively serialised.
        """
        return {
            "idea": self.idea.to_dict(),
            "marginal_value": self.marginal_value,
            "final_score": self.final_score,
            "ranking_components": dict(self.ranking_components),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankedIdea:
        """Reconstruct from a plain dict, including nested IdeaProposal."""
        return cls(
            idea=IdeaProposal.from_dict(data["idea"]),
            marginal_value=float(data["marginal_value"]),
            final_score=float(data["final_score"]),
            ranking_components={
                k: float(v) for k, v in data.get("ranking_components", {}).items()
            },
        )


@dataclass
class IdeationResult:
    """Top-level container for a complete ideation pipeline run.

    Parameters
    ----------
    purpose : AppIdeationPurpose
        The purpose / objective that drove this run.
    portfolio : IdeaPortfolio
        The existing-app portfolio used as input.
    coverage : CoverageReport
        Coverage analysis produced during the run.
    candidates : list
        All candidate ideas generated.
    ranked_ideas : list
        Candidates after scoring and ranking.
    pipeline_metadata : dict
        Arbitrary metadata about the pipeline execution
        (timings, versions, parameters, etc.).
    """

    purpose: AppIdeationPurpose
    portfolio: IdeaPortfolio
    coverage: CoverageReport
    candidates: list  # list[IdeaProposal]
    ranked_ideas: list  # list[RankedIdea]
    pipeline_metadata: dict

    # -- helpers -------------------------------------------------------------

    def top_ideas(self, n: int = 5) -> list[RankedIdea]:
        """Return the top *n* ranked ideas by ``final_score`` (descending).

        If fewer than *n* ranked ideas exist, all are returned.
        """
        sorted_ideas = sorted(
            self.ranked_ideas,
            key=lambda ri: ri.final_score,
            reverse=True,
        )
        return sorted_ideas[:n]

    def summary(self) -> str:
        """Return a human-readable summary of the ideation run.

        Includes domain, portfolio size, gap count, candidate count,
        and the titles of the top-5 ranked ideas.
        """
        lines: list[str] = [
            f"Ideation Result for domain={self.purpose.domain!r}",
            f"  user_population: {self.purpose.user_population}",
            f"  portfolio size:  {len(self.portfolio.ideas)}",
            f"  gaps detected:   {self.coverage.gap_count()}",
            f"  candidates:      {len(self.candidates)}",
            f"  ranked ideas:    {len(self.ranked_ideas)}",
        ]
        top = self.top_ideas(5)
        if top:
            lines.append("  top ideas:")
            for i, ri in enumerate(top, 1):
                lines.append(
                    f"    {i}. {ri.idea.title} "
                    f"(score={ri.final_score:.3f})"
                )
        return "\n".join(lines)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation.

        All nested objects are recursively serialised.
        """
        return {
            "purpose": self.purpose.to_dict(),
            "portfolio": self.portfolio.to_dict(),
            "coverage": self.coverage.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "ranked_ideas": [ri.to_dict() for ri in self.ranked_ideas],
            "pipeline_metadata": dict(self.pipeline_metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdeationResult:
        """Reconstruct from a plain dict, including all nested objects."""
        purpose = AppIdeationPurpose.from_dict(data["purpose"])
        portfolio = IdeaPortfolio.from_dict(data["portfolio"])
        coverage = CoverageReport.from_dict(data["coverage"])
        candidates = [
            IdeaProposal.from_dict(c) for c in data.get("candidates", [])
        ]
        ranked_ideas = [
            RankedIdea.from_dict(ri) for ri in data.get("ranked_ideas", [])
        ]
        return cls(
            purpose=purpose,
            portfolio=portfolio,
            coverage=coverage,
            candidates=candidates,
            ranked_ideas=ranked_ideas,
            pipeline_metadata=dict(data.get("pipeline_metadata", {})),
        )
