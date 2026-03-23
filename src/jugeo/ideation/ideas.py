"""Rich idea management primitives for JuGeo ideation.

This module models the theory2.tex notion of an idea as a typed proposal
about a reachable semantic future under explicit purpose and evidence
discipline.  The classes below treat ideas as structured objects with
predicted theorem yield, bridge impact, cost, uncertainty, validation
paths, lifecycle transitions, dependency structure, and diagnostic views.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
import json
import math
import time
from typing import Any, Iterable, Mapping, Sequence

from jugeo.geometry.supports import SupportRegion

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentBuilder,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
    )
except ImportError:  # pragma: no cover
    Judgment = None  # type: ignore[assignment,misc]
    JudgmentBuilder = None  # type: ignore[assignment,misc]
    Proposition = None  # type: ignore[assignment,misc]
    PropositionKind = None  # type: ignore[assignment,misc]
    Carrier = None  # type: ignore[assignment,misc]
    EvidenceBundle = None  # type: ignore[assignment,misc]
    TrustAnnotation = None  # type: ignore[assignment,misc]
    Provenance = None  # type: ignore[assignment,misc]
    ProvenanceSource = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import Site, Coordinate, CoordinateKind, build_site
except ImportError:  # pragma: no cover
    Site = None  # type: ignore[assignment,misc]
    Coordinate = None  # type: ignore[assignment,misc]
    CoordinateKind = None  # type: ignore[assignment,misc]
    build_site = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.channels import (
        ChannelDescriptor,
        EvidenceChannel,
        EvidenceRequest,
        build_channel,
    )
except ImportError:  # pragma: no cover
    ChannelDescriptor = None  # type: ignore[assignment,misc]
    EvidenceChannel = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    build_channel = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.structural_frontier import (
        StructuralFrontierDefiner,
        get_default_definer,
    )
except ImportError:  # pragma: no cover
    StructuralFrontierDefiner = None  # type: ignore[assignment,misc]
    get_default_definer = None  # type: ignore[assignment,misc]


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp a numeric value to a closed interval."""

    return max(lower, min(upper, float(value)))


def _normalize_text(text: str) -> str:
    """Normalize free text into a compact comparison-friendly form."""

    return " ".join(part for part in text.strip().lower().split() if part)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    """Return values in first-seen order with duplicates removed."""

    return tuple(dict.fromkeys(value for value in values if value))


def _tokenize(*parts: str) -> set[str]:
    """Tokenize text fragments into lowercase lexical units."""

    tokens: set[str] = set()
    for part in parts:
        normalized = _normalize_text(part)
        for token in normalized.replace("/", " ").replace("-", " ").split():
            if token:
                tokens.add(token)
    return tokens


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Compute a Jaccard overlap score between two token collections."""

    left_tokens = set(left)
    right_tokens = set(right)
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


class TrustStatus(str, Enum):
    """Trust stages used to express evidential maturity for an idea."""

    SPECULATIVE = "speculative"
    PROVISIONAL = "provisional"
    GROUNDED = "grounded"
    VALIDATED = "validated"
    RETIRED = "retired"


_TRUST_ORDER: dict[TrustStatus, int] = {
    TrustStatus.SPECULATIVE: 0,
    TrustStatus.PROVISIONAL: 1,
    TrustStatus.GROUNDED: 2,
    TrustStatus.VALIDATED: 3,
    TrustStatus.RETIRED: 4,
}


class LifecycleStatus(str, Enum):
    """Lifecycle stages for idea portfolio management."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class GainProfile:
    """Structured forecast describing what an idea may produce.

    Attributes:
        theorem_yield: Predicted amount of theorem or lemma generation.
        bridge_impact: Expected cross-area impact when the idea succeeds.
        cost: Estimated implementation or proof burden.
        uncertainty: Forecast uncertainty on a normalized 0-1 scale.
    """

    theorem_yield: float
    bridge_impact: float
    cost: float
    uncertainty: float

    def __post_init__(self) -> None:
        """Normalize numeric ranges after initialization."""

        object.__setattr__(self, "theorem_yield", max(0.0, float(self.theorem_yield)))
        object.__setattr__(self, "bridge_impact", max(0.0, float(self.bridge_impact)))
        object.__setattr__(self, "cost", max(0.0, float(self.cost)))
        object.__setattr__(self, "uncertainty", _clamp(self.uncertainty))

    def composite_value(self) -> float:
        """Return a compact value forecast that rewards gain and penalizes risk."""

        gain = 0.6 * self.theorem_yield + 0.4 * self.bridge_impact
        risk_penalty = self.cost * (0.25 + 0.75 * self.uncertainty)
        return max(0.0, gain - risk_penalty)

    def feasibility_margin(self) -> float:
        """Return a lightweight estimate of how executable the idea appears."""

        return max(0.0, 1.0 - (self.cost / (self.theorem_yield + self.bridge_impact + 1.0)) - 0.5 * self.uncertainty)

    def adjusted(
        self,
        *,
        theorem_delta: float = 0.0,
        bridge_delta: float = 0.0,
        cost_delta: float = 0.0,
        uncertainty_delta: float = 0.0,
    ) -> GainProfile:
        """Return a new gain profile with targeted deltas applied."""

        return GainProfile(
            theorem_yield=self.theorem_yield + theorem_delta,
            bridge_impact=self.bridge_impact + bridge_delta,
            cost=self.cost + cost_delta,
            uncertainty=self.uncertainty + uncertainty_delta,
        )

    def to_dict(self) -> dict[str, float]:
        """Serialize the gain profile into a JSON-friendly dictionary."""

        return {
            "theorem_yield": self.theorem_yield,
            "bridge_impact": self.bridge_impact,
            "cost": self.cost,
            "uncertainty": self.uncertainty,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GainProfile:
        """Construct a gain profile from a mapping."""

        return cls(
            theorem_yield=float(data["theorem_yield"]),
            bridge_impact=float(data["bridge_impact"]),
            cost=float(data["cost"]),
            uncertainty=float(data["uncertainty"]),
        )


@dataclass(frozen=True, slots=True)
class ValidationPath:
    """Evidence-oriented validation path for an idea.

    Attributes:
        steps: Ordered validation actions.
        required_evidence: Evidence types or artifacts required by the path.
        success_criteria: Concrete conditions for considering the idea validated.
    """

    steps: tuple[str, ...]
    required_evidence: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize path contents into stable ordered tuples."""

        object.__setattr__(self, "steps", _ordered_unique(step.strip() for step in self.steps))
        object.__setattr__(
            self,
            "required_evidence",
            _ordered_unique(evidence.strip() for evidence in self.required_evidence),
        )
        object.__setattr__(
            self,
            "success_criteria",
            _ordered_unique(criteria.strip() for criteria in self.success_criteria),
        )

    def depth(self) -> int:
        """Return the number of explicit validation steps."""

        return len(self.steps)

    def discipline_score(self) -> float:
        """Score how explicit and evidence-sensitive the path is."""

        evidence_weight = min(1.0, len(self.required_evidence) / max(1, len(self.steps)))
        criteria_weight = min(1.0, len(self.success_criteria) / max(1, len(self.steps)))
        return _clamp(0.4 + 0.3 * evidence_weight + 0.3 * criteria_weight)

    def append(
        self,
        step: str,
        *,
        evidence: str | None = None,
        criterion: str | None = None,
    ) -> ValidationPath:
        """Return a new path extended with a step and optional evidence details."""

        steps = self.steps + (step,)
        evidence_items = self.required_evidence + ((evidence,) if evidence else ())
        criteria = self.success_criteria + ((criterion,) if criterion else ())
        return ValidationPath(steps, evidence_items, criteria)

    def strengthened(self, *, evidence: Iterable[str] = (), criteria: Iterable[str] = ()) -> ValidationPath:
        """Return a path with additional evidence requirements or success criteria."""

        return ValidationPath(
            self.steps,
            self.required_evidence + tuple(evidence),
            self.success_criteria + tuple(criteria),
        )

    def to_dict(self) -> dict[str, tuple[str, ...]]:
        """Serialize the validation path into a JSON-friendly mapping."""

        return {
            "steps": self.steps,
            "required_evidence": self.required_evidence,
            "success_criteria": self.success_criteria,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ValidationPath:
        """Construct a validation path from a mapping."""

        return cls(
            steps=tuple(str(item) for item in data.get("steps", ())),
            required_evidence=tuple(str(item) for item in data.get("required_evidence", ())),
            success_criteria=tuple(str(item) for item in data.get("success_criteria", ())),
        )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Summary of evaluator scores and the resulting recommendation."""

    novelty: float
    feasibility: float
    compounding: float
    alignment: float
    total_score: float
    recommendation: str

    def to_dict(self) -> dict[str, float | str]:
        """Serialize the evaluation result into a dictionary."""

        return {
            "novelty": self.novelty,
            "feasibility": self.feasibility,
            "compounding": self.compounding,
            "alignment": self.alignment,
            "total_score": self.total_score,
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A single dated event in an idea's lifecycle history."""

    idea_id: str
    event: str
    status: str
    timestamp: float
    notes: str = ""

    def to_dict(self) -> dict[str, float | str]:
        """Serialize the history entry into a mapping."""

        return {
            "idea_id": self.idea_id,
            "event": self.event,
            "status": self.status,
            "timestamp": self.timestamp,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class Idea:
    """Typed proposal for a reachable semantic future in JuGeo.

    An idea binds a purpose to a target area, states a hypothesis, encodes an
    explicit gain forecast, and names a validation path.  Unlike a free-form
    brainstorm note, this class tracks theorem yield, bridge impact, cost, and
    uncertainty as structured quantities.
    """

    idea_id: str
    title: str
    purpose: str
    target_area: str
    hypothesis: str
    predicted_gain: GainProfile
    novelty_score: float
    validation_plan: ValidationPath
    trust_status: TrustStatus

    def __post_init__(self) -> None:
        """Normalize core fields after initialization."""

        object.__setattr__(self, "idea_id", self.idea_id.strip())
        object.__setattr__(self, "title", " ".join(self.title.split()))
        object.__setattr__(self, "purpose", " ".join(self.purpose.split()))
        object.__setattr__(self, "target_area", " ".join(self.target_area.split()))
        object.__setattr__(self, "hypothesis", " ".join(self.hypothesis.split()))
        object.__setattr__(self, "novelty_score", _clamp(self.novelty_score))
        if not self.idea_id:
            raise ValueError("idea_id must be non-empty")
        if not self.title:
            raise ValueError("title must be non-empty")
        if not self.validation_plan.steps:
            raise ValueError("validation_plan must contain at least one step")

    def theorem_yield(self) -> float:
        """Return the predicted theorem yield component."""

        return self.predicted_gain.theorem_yield

    def bridge_impact(self) -> float:
        """Return the predicted bridge impact component."""

        return self.predicted_gain.bridge_impact

    def estimated_cost(self) -> float:
        """Return the forecast implementation or proof cost."""

        return self.predicted_gain.cost

    def uncertainty(self) -> float:
        """Return the normalized uncertainty forecast."""

        return self.predicted_gain.uncertainty

    def validation_depth(self) -> int:
        """Return the number of explicit validation steps."""

        return self.validation_plan.depth()

    def evidence_discipline_score(self) -> float:
        """Score how explicit the idea is about evidence and validation."""

        trust_bonus = 0.1 * _TRUST_ORDER[self.trust_status]
        return _clamp(self.validation_plan.discipline_score() + trust_bonus, 0.0, 1.5)

    def expected_value(self) -> float:
        """Estimate the net value of pursuing the idea.

        The score is intentionally simple and stable rather than fully
        probabilistic.  It rewards novelty when supported by evidence
        discipline and discounts uncertain, expensive proposals.
        """

        base_gain = self.predicted_gain.composite_value()
        novelty_multiplier = 0.75 + 0.5 * self.novelty_score
        discipline_multiplier = 0.7 + 0.3 * min(1.0, self.evidence_discipline_score())
        uncertainty_penalty = 1.0 - 0.5 * self.uncertainty()
        trust_bonus = 0.05 * _TRUST_ORDER[self.trust_status]
        return max(0.0, base_gain * novelty_multiplier * discipline_multiplier * uncertainty_penalty + trust_bonus)

    def is_trusted(self, minimum_status: TrustStatus = TrustStatus.GROUNDED) -> bool:
        """Return whether the idea meets a minimum trust threshold."""

        return _TRUST_ORDER[self.trust_status] >= _TRUST_ORDER[minimum_status]

    def with_adjusted_gain(
        self,
        *,
        theorem_delta: float = 0.0,
        bridge_delta: float = 0.0,
        cost_delta: float = 0.0,
        uncertainty_delta: float = 0.0,
        novelty_delta: float = 0.0,
    ) -> Idea:
        """Return a copy of the idea with an adjusted gain forecast."""

        return replace(
            self,
            predicted_gain=self.predicted_gain.adjusted(
                theorem_delta=theorem_delta,
                bridge_delta=bridge_delta,
                cost_delta=cost_delta,
                uncertainty_delta=uncertainty_delta,
            ),
            novelty_score=_clamp(self.novelty_score + novelty_delta),
        )

    def with_validation_step(
        self,
        step: str,
        *,
        evidence: str | None = None,
        criterion: str | None = None,
    ) -> Idea:
        """Return a copy of the idea with a strengthened validation plan."""

        return replace(
            self,
            validation_plan=self.validation_plan.append(step, evidence=evidence, criterion=criterion),
        )

    def as_prompt(self) -> str:
        """Render the idea as a compact prompt for human or copilot review."""

        return (
            f"Idea {self.idea_id}: {self.title}\n"
            f"Purpose: {self.purpose}\n"
            f"Target area: {self.target_area}\n"
            f"Hypothesis: {self.hypothesis}\n"
            f"Theorem yield={self.theorem_yield():.2f}, bridge impact={self.bridge_impact():.2f}, "
            f"cost={self.estimated_cost():.2f}, uncertainty={self.uncertainty():.2f}\n"
            f"Validation path: {', '.join(self.validation_plan.steps)}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the idea into a JSON-friendly dictionary."""

        return {
            "idea_id": self.idea_id,
            "title": self.title,
            "purpose": self.purpose,
            "target_area": self.target_area,
            "hypothesis": self.hypothesis,
            "predicted_gain": self.predicted_gain.to_dict(),
            "novelty_score": self.novelty_score,
            "validation_plan": self.validation_plan.to_dict(),
            "trust_status": self.trust_status.value,
        }


@dataclass(frozen=True, slots=True)
class IdeaProposal:
    """Legacy JuGeo proposal model retained for compatibility.

    Existing ideation helpers use this smaller structure.  The richer
    :class:`Idea` model lives alongside it so existing tests and modules
    continue to work while newer code can opt into typed semantic futures.
    """

    title: str
    hypothesis: str
    support: SupportRegion | None = None
    payoff: int | float = 0
    predicted_yield: float | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.predicted_yield is not None and self.payoff == 0:
            object.__setattr__(self, "payoff", float(self.predicted_yield))

    def normalized_payoff(self) -> float:
        """Return a bounded payoff score for light-weight ranking."""

        return max(0.0, float(self.payoff))

    def support_size(self) -> int:
        """Return the size of the support region patch set."""

        if self.support is None:
            return 0
        return len(self.support.patch_keys)

    def provenance_count(self) -> int:
        """Return the number of provenance references attached to the proposal."""

        return len(self.provenance)

    def confidence_hint(self) -> float:
        """Return a heuristic confidence score for the proposal."""

        return _clamp(0.4 + 0.1 * self.provenance_count() + 0.05 * self.support_size())

    def as_idea(
        self,
        *,
        idea_id: str,
        purpose: str = "legacy ideation carryover",
        target_area: str = "semantic futures",
    ) -> Idea:
        """Convert the legacy proposal into the richer idea model."""

        validation = ValidationPath(
            steps=("restate the hypothesis in modern terms", "check the support against current geometry"),
            required_evidence=("support-region witness",),
            success_criteria=("bridge candidate yields a coherent semantic forecast",),
        )
        gain = GainProfile(
            theorem_yield=float(self.payoff),
            bridge_impact=max(1.0, float(self.payoff) / 2.0),
            cost=max(1.0, 2.0 - self.confidence_hint()),
            uncertainty=1.0 - self.confidence_hint(),
        )
        return Idea(
            idea_id=idea_id,
            title=self.title,
            purpose=purpose,
            target_area=target_area,
            hypothesis=self.hypothesis,
            predicted_gain=gain,
            novelty_score=_clamp(0.3 + 0.05 * self.support_size()),
            validation_plan=validation,
            trust_status=TrustStatus.PROVISIONAL,
        )

    # ------------------------------------------------------------------
    # Judgment-geometric integration
    # ------------------------------------------------------------------

    def judgment_formulation(self) -> dict[str, Any]:
        """Formulate this idea as a judgment 8-tuple.

        Uses :mod:`jugeo.judgments.judgment_terms` to construct a
        :class:`~jugeo.judgments.judgment_terms.Judgment` via the fluent
        :class:`~jugeo.judgments.judgment_terms.JudgmentBuilder`, encoding
        the hypothesis as a proposition and attaching provenance metadata.

        Returns a dict representation when the judgment subsystem is
        available, or a lightweight stub dict otherwise.
        """
        if JudgmentBuilder is None or Proposition is None:
            return {
                "status": "unavailable",
                "reason": "jugeo.judgments.judgment_terms not installed",
                "title": self.title,
                "hypothesis": self.hypothesis,
            }

        builder = JudgmentBuilder()
        builder = builder.claiming_formula(
            self.hypothesis,
            kind=PropositionKind.SEMANTIC if PropositionKind is not None else None,
        )
        builder = builder.of_type_named(self.title)
        if ProvenanceSource is not None:
            builder = builder.from_source(ProvenanceSource.ORACLE)

        if Coordinate is not None and CoordinateKind is not None:
            coord = Coordinate(
                components=(self.title,),
                kind=CoordinateKind.REGION,
                support_labels=frozenset(
                    t for t in _tokenize(self.title, self.hypothesis) if len(t) > 2
                ),
                metadata={"payoff": float(self.payoff)},
            )
            builder = builder.at(coord)

        judgment = builder.build()
        return judgment.to_dict() if hasattr(judgment, "to_dict") else {"judgment": str(judgment)}

    def geometric_embedding(self) -> dict[str, Any]:
        """Embed this idea into the judgment site.

        Uses :mod:`jugeo.geometry.site` to construct a
        :class:`~jugeo.geometry.site.Coordinate` representing the idea's
        position in the Grothendieck site, and returns embedding metadata
        including coordinate key and support labels.

        Returns a dict with embedding data, or a stub if the geometry
        subsystem is unavailable.
        """
        if Coordinate is None or CoordinateKind is None:
            return {
                "status": "unavailable",
                "reason": "jugeo.geometry.site not installed",
                "title": self.title,
            }

        support_labels = frozenset(
            token
            for token in _tokenize(self.title, self.hypothesis)
            if len(token) > 2
        )
        coord = Coordinate(
            components=(self.title,),
            kind=CoordinateKind.REGION,
            support_labels=support_labels,
            metadata={"payoff": float(self.payoff), "provenance_count": self.provenance_count()},
        )
        return {
            "coordinate_key": coord.key if hasattr(coord, "key") else str(coord),
            "kind": coord.kind.value if hasattr(coord.kind, "value") else str(coord.kind),
            "support_labels": sorted(support_labels),
            "support_size": len(support_labels),
            "proposal_title": self.title,
        }

    def evidence_requirements(self) -> dict[str, Any]:
        """Compute required evidence channels for this idea.

        Uses :mod:`jugeo.evidence.channels` to determine which evidence
        channels must be activated to validate the hypothesis.  The channel
        selection is driven by payoff magnitude and provenance richness.

        Returns a dict mapping channel names to requirement metadata, or
        a stub if the evidence subsystem is unavailable.
        """
        if EvidenceChannel is None or ChannelDescriptor is None:
            return {
                "status": "unavailable",
                "reason": "jugeo.evidence.channels not installed",
                "title": self.title,
            }

        channels: list[dict[str, Any]] = []
        # Every idea requires at least oracle review.
        channels.append({
            "channel": EvidenceChannel.ORACLE.value if hasattr(EvidenceChannel.ORACLE, "value") else "oracle",
            "priority": "required",
            "rationale": "baseline oracle review for all proposals",
        })
        # High-payoff ideas should go through solver verification.
        if float(self.payoff) >= 5.0:
            channels.append({
                "channel": EvidenceChannel.SOLVER.value if hasattr(EvidenceChannel.SOLVER, "value") else "solver",
                "priority": "recommended",
                "rationale": f"payoff {self.payoff} warrants solver discharge",
            })
        # Ideas with provenance merit formal proof channels.
        if self.provenance_count() >= 2:
            channels.append({
                "channel": EvidenceChannel.FORMAL_PROOF.value if hasattr(EvidenceChannel.FORMAL_PROOF, "value") else "formal_proof",
                "priority": "optional",
                "rationale": f"{self.provenance_count()} provenance refs support formal treatment",
            })
        # Copilot channel for confidence assessment.
        channels.append({
            "channel": EvidenceChannel.COPILOT.value if hasattr(EvidenceChannel.COPILOT, "value") else "copilot",
            "priority": "advisory",
            "rationale": "copilot confidence cross-check",
        })
        return {
            "title": self.title,
            "channel_count": len(channels),
            "channels": channels,
            "confidence_hint": self.confidence_hint(),
        }

    def encoding_feasibility(self) -> dict[str, Any]:
        """Check whether this idea is encodable in the structural frontier.

        Uses :mod:`jugeo.encodings.structural_frontier` to assess whether
        the hypothesis falls within decidable logic fragments, and reports
        the estimated encoding cost via the
        :class:`~jugeo.encodings.structural_frontier.DecidabilityOracle`.

        Returns a dict with feasibility classification and cost estimate,
        or a stub if the encodings subsystem is unavailable.
        """
        if get_default_definer is None or StructuralFrontierDefiner is None:
            return {
                "status": "unavailable",
                "reason": "jugeo.encodings.structural_frontier not installed",
                "title": self.title,
            }

        definer = get_default_definer()
        oracle = definer.oracle if hasattr(definer, "oracle") else definer
        # Classify the hypothesis formula.
        classification = (
            oracle.classify(self.hypothesis)
            if hasattr(oracle, "classify")
            else "unknown"
        )
        # Estimate crossing cost if on the boundary.
        crossing_cost = 0.0
        if hasattr(definer, "boundary_locator"):
            locator = definer.boundary_locator
            if hasattr(locator, "crossing_cost"):
                crossing_cost = float(locator.crossing_cost(self.hypothesis))

        is_decidable = classification not in ("undecidable", "unknown")
        return {
            "title": self.title,
            "hypothesis": self.hypothesis,
            "decidability_class": str(classification),
            "is_encodable": is_decidable,
            "crossing_cost": crossing_cost,
            "confidence_hint": self.confidence_hint(),
        }


@dataclass(slots=True)
class IdeaPortfolio:
    """Mutable portfolio of typed ideas with ranking and filtering helpers."""

    ideas: dict[str, Idea] = field(default_factory=dict)

    def add(self, idea: Idea) -> None:
        """Insert an idea into the portfolio, rejecting duplicate identifiers."""

        if idea.idea_id in self.ideas:
            raise ValueError(f"duplicate idea_id: {idea.idea_id}")
        self.ideas[idea.idea_id] = idea

    def remove(self, idea_id: str) -> Idea:
        """Remove an idea by identifier and return the removed value."""

        if idea_id not in self.ideas:
            raise KeyError(idea_id)
        return self.ideas.pop(idea_id)

    def rank(
        self,
        evaluator: IdeaEvaluator | None = None,
        *,
        purpose: str | None = None,
        area: str | None = None,
    ) -> list[tuple[Idea, float]]:
        """Rank ideas by evaluator score, optionally under a narrower objective."""

        evaluator = evaluator or IdeaEvaluator()
        ranked: list[tuple[Idea, float]] = []
        for idea in self.ideas.values():
            if purpose and _normalize_text(idea.purpose) != _normalize_text(purpose):
                continue
            if area and _normalize_text(idea.target_area) != _normalize_text(area):
                continue
            result = evaluator.evaluate(idea, portfolio=self.ideas.values(), purpose=purpose, area=area)
            ranked.append((idea, result.total_score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def shortlist(
        self,
        *,
        limit: int = 5,
        evaluator: IdeaEvaluator | None = None,
        purpose: str | None = None,
        area: str | None = None,
    ) -> tuple[Idea, ...]:
        """Return the highest-ranked ideas under optional purpose or area filters."""

        ranked = self.rank(evaluator=evaluator, purpose=purpose, area=area)
        return tuple(idea for idea, _score in ranked[: max(0, limit)])

    def by_purpose(self, purpose: str) -> tuple[Idea, ...]:
        """Return ideas whose purpose matches the supplied normalized purpose."""

        target = _normalize_text(purpose)
        return tuple(idea for idea in self.ideas.values() if _normalize_text(idea.purpose) == target)

    def by_area(self, area: str) -> tuple[Idea, ...]:
        """Return ideas whose target area matches the supplied normalized area."""

        target = _normalize_text(area)
        return tuple(idea for idea in self.ideas.values() if _normalize_text(idea.target_area) == target)

    def diversity_score(self) -> float:
        """Estimate idea diversity across purposes and target areas.

        The score averages normalized Shannon entropy across both the purpose
        distribution and the target-area distribution.
        """

        if len(self.ideas) < 2:
            return 0.0

        def _entropy(counter: Counter[str]) -> float:
            total = sum(counter.values())
            if total <= 1 or len(counter) <= 1:
                return 0.0
            entropy = 0.0
            for count in counter.values():
                probability = count / total
                entropy -= probability * math.log(probability)
            return entropy / math.log(len(counter))

        purpose_counts = Counter(_normalize_text(idea.purpose) for idea in self.ideas.values())
        area_counts = Counter(_normalize_text(idea.target_area) for idea in self.ideas.values())
        return _clamp((_entropy(purpose_counts) + _entropy(area_counts)) / 2.0)


@dataclass(slots=True)
class IdeaGenerator:
    """Heuristic generator for purpose-driven semantic future proposals."""

    counter: int = 0

    def _next_id(self, prefix: str = "idea") -> str:
        """Return a monotonically increasing idea identifier."""

        self.counter += 1
        return f"{prefix}-{self.counter:04d}"

    def _build_idea(
        self,
        *,
        title: str,
        purpose: str,
        target_area: str,
        hypothesis: str,
        seed_tokens: Iterable[str],
    ) -> Idea:
        """Construct an idea from lexical signals and deterministic heuristics."""

        tokens = set(seed_tokens)
        density = max(1.0, len(tokens))
        theorem_yield = 2.0 + 0.4 * density + 0.2 * len(_tokenize(hypothesis))
        bridge_impact = 1.5 + 0.3 * len(_tokenize(purpose, target_area))
        cost = max(1.0, 1.5 + 0.1 * len(title.split()) + 0.05 * len(hypothesis.split()))
        uncertainty = _clamp(0.65 - 0.03 * len(tokens), 0.15, 0.85)
        plan = ValidationPath(
            steps=(
                "formalize the semantic objective",
                "derive a small witness theorem",
                "stress-test the bridge on a nearby obstruction",
            ),
            required_evidence=("purpose fit memo", "counterexample check"),
            success_criteria=("forecast yields at least one reusable theorem schema",),
        )
        return Idea(
            idea_id=self._next_id(),
            title=title,
            purpose=purpose,
            target_area=target_area,
            hypothesis=hypothesis,
            predicted_gain=GainProfile(theorem_yield, bridge_impact, cost, uncertainty),
            novelty_score=_clamp(0.45 + 0.02 * len(tokens), 0.0, 0.95),
            validation_plan=plan,
            trust_status=TrustStatus.SPECULATIVE,
        )

    def generate(
        self,
        purpose: str,
        target_area: str,
        *,
        observations: Sequence[str] = (),
        count: int = 3,
    ) -> tuple[Idea, ...]:
        """Generate a batch of ideas constrained by purpose and observed signals."""

        observation_tokens = _ordered_unique(observations)
        if count <= 0:
            return ()
        ideas: list[Idea] = []
        base_tokens = _tokenize(purpose, target_area, *observation_tokens)
        observation_phrase = ", ".join(observation_tokens) if observation_tokens else "current semantic friction"
        for index in range(count):
            title = f"{target_area.title()} bridge {index + 1}"
            hypothesis = (
                f"If {target_area} is reorganized around {purpose}, then {observation_phrase} "
                f"can be converted into a reusable theorem bridge."
            )
            ideas.append(
                self._build_idea(
                    title=title,
                    purpose=purpose,
                    target_area=target_area,
                    hypothesis=hypothesis,
                    seed_tokens=base_tokens | {str(index + 1)},
                )
            )
        return tuple(ideas)

    def mutate(self, idea: Idea, *, emphasis: str = "novelty") -> Idea:
        """Mutate an idea while preserving its original purpose."""

        emphasis_text = _normalize_text(emphasis) or "novelty"
        if emphasis_text == "validation":
            return idea.with_validation_step(
                "compare the proposal against a deliberately hostile countermodel",
                evidence="countermodel witness",
                criterion="countermodel either fails or exposes a repair path",
            ).with_adjusted_gain(cost_delta=0.2, uncertainty_delta=-0.1)
        if emphasis_text == "cost":
            return idea.with_adjusted_gain(cost_delta=-0.4, theorem_delta=-0.1, novelty_delta=-0.05)
        return replace(
            idea.with_adjusted_gain(theorem_delta=0.4, bridge_delta=0.2, uncertainty_delta=0.05, novelty_delta=0.08),
            title=f"{idea.title} mutation",
            hypothesis=f"{idea.hypothesis} The mutation emphasizes {emphasis_text}.",
        )

    def analogize(self, idea: Idea, source_area: str) -> Idea:
        """Project an idea through an analogy with another source area."""

        source_tokens = _tokenize(source_area)
        analogical_gap = 1.0 - _jaccard(source_tokens, _tokenize(idea.target_area))
        return replace(
            idea.with_adjusted_gain(
                theorem_delta=0.2 + 0.6 * analogical_gap,
                bridge_delta=0.4 + 0.5 * analogical_gap,
                cost_delta=0.3 * analogical_gap,
                uncertainty_delta=0.2 * analogical_gap,
                novelty_delta=0.15 * analogical_gap,
            ),
            idea_id=self._next_id("analogy"),
            title=f"{idea.title} via {source_area}",
            hypothesis=(
                f"Analogy from {source_area}: {idea.hypothesis} "
                f"Treat structures in {idea.target_area} as if they inherit the transport discipline of {source_area}."
            ),
        )

    def extrapolate(self, idea: Idea, *, horizon: float = 1.5) -> Idea:
        """Extrapolate an idea toward a longer semantic horizon."""

        horizon = max(1.0, horizon)
        return replace(
            idea.with_adjusted_gain(
                theorem_delta=idea.theorem_yield() * 0.25 * (horizon - 1.0),
                bridge_delta=idea.bridge_impact() * 0.2 * (horizon - 1.0),
                cost_delta=idea.estimated_cost() * 0.1 * (horizon - 1.0),
                uncertainty_delta=0.08 * (horizon - 1.0),
                novelty_delta=0.05 * (horizon - 1.0),
            ),
            idea_id=self._next_id("future"),
            title=f"{idea.title} horizon x{horizon:.1f}",
            hypothesis=f"{idea.hypothesis} The extrapolation assumes the semantic future remains reachable over horizon {horizon:.1f}.",
        )

    def bridge_from_obstruction(self, obstruction: str, purpose: str, target_area: str) -> Idea:
        """Generate an idea that turns an obstruction into a bridging target."""

        normalized = _normalize_text(obstruction)
        title = f"Bridge around {normalized or 'obstruction'}"
        hypothesis = (
            f"If the obstruction '{obstruction}' is reframed as an interface boundary, "
            f"then {target_area} can support {purpose} through a smaller trusted bridge."
        )
        return self._build_idea(
            title=title,
            purpose=purpose,
            target_area=target_area,
            hypothesis=hypothesis,
            seed_tokens=_tokenize(obstruction, purpose, target_area),
        ).with_validation_step(
            "verify that the obstruction is genuinely reduced rather than renamed",
            evidence="before-after obstruction ledger",
            criterion="bridge shrinks the obstruction frontier",
        )

    def copilot_generate(self, prompt: str, *, purpose: str = "copilot-assisted discovery", target_area: str = "ideation") -> Idea:
        """Generate an idea framed as a copilot-guided proposal.

        The method does not call any external system.  It simply translates the
        prompt into a structured proposal whose title and hypothesis make the
        copilot relationship explicit.
        """

        tokens = _tokenize(prompt, purpose, target_area, "copilot")
        title = "Copilot-guided semantic bridge"
        hypothesis = (
            f"A copilot can turn the prompt '{prompt.strip() or 'unspecified prompt'}' "
            f"into a typed validation agenda for {target_area} serving {purpose}."
        )
        return replace(
            self._build_idea(
                title=title,
                purpose=purpose,
                target_area=target_area,
                hypothesis=hypothesis,
                seed_tokens=tokens,
            ),
            trust_status=TrustStatus.PROVISIONAL,
        )


@dataclass(frozen=True, slots=True)
class IdeaEvaluator:
    """Evaluate ideas across novelty, feasibility, compounding, and alignment."""

    novelty_weight: float = 0.28
    feasibility_weight: float = 0.24
    compounding_weight: float = 0.24
    alignment_weight: float = 0.24

    def evaluate(
        self,
        idea: Idea,
        *,
        portfolio: Iterable[Idea] = (),
        dependencies: Iterable[Idea] = (),
        purpose: str | None = None,
        area: str | None = None,
    ) -> EvaluationResult:
        """Return a weighted evaluation result for an idea."""

        novelty = self.score_novelty(idea, portfolio=portfolio)
        feasibility = self.score_feasibility(idea)
        compounding = self.score_compounding(idea, dependencies=dependencies)
        alignment = self.score_alignment(idea, purpose=purpose, area=area)
        total = (
            novelty * self.novelty_weight
            + feasibility * self.feasibility_weight
            + compounding * self.compounding_weight
            + alignment * self.alignment_weight
        )
        if total >= 0.75:
            recommendation = "accept"
        elif total >= 0.55:
            recommendation = "refine"
        else:
            recommendation = "defer"
        return EvaluationResult(novelty, feasibility, compounding, alignment, _clamp(total), recommendation)

    def score_novelty(self, idea: Idea, *, portfolio: Iterable[Idea] = ()) -> float:
        """Score novelty using the declared novelty plus portfolio distinctness."""

        portfolio_list = list(portfolio)
        if not portfolio_list:
            return idea.novelty_score
        overlaps: list[float] = []
        idea_tokens = _tokenize(idea.title, idea.hypothesis, idea.target_area)
        for other in portfolio_list:
            if other.idea_id == idea.idea_id:
                continue
            other_tokens = _tokenize(other.title, other.hypothesis, other.target_area)
            overlaps.append(_jaccard(idea_tokens, other_tokens))
        distinctness = 1.0 - (sum(overlaps) / len(overlaps) if overlaps else 0.0)
        return _clamp(0.6 * idea.novelty_score + 0.4 * distinctness)

    def score_feasibility(self, idea: Idea) -> float:
        """Score feasibility using cost, uncertainty, trust, and validation detail."""

        gain = idea.theorem_yield() + idea.bridge_impact()
        cost_penalty = idea.estimated_cost() / (gain + 1.0)
        validation_bonus = min(1.0, idea.validation_depth() / 5.0)
        trust_bonus = 0.15 * _TRUST_ORDER[idea.trust_status]
        feasibility = 0.55 + 0.25 * validation_bonus + trust_bonus - 0.4 * cost_penalty - 0.35 * idea.uncertainty()
        return _clamp(feasibility)

    def score_compounding(self, idea: Idea, *, dependencies: Iterable[Idea] = ()) -> float:
        """Score how much the idea could compound through theorem reuse and bridges."""

        dependency_list = list(dependencies)
        dependency_gain = sum(dep.bridge_impact() + dep.theorem_yield() for dep in dependency_list)
        local_gain = idea.theorem_yield() + 1.2 * idea.bridge_impact()
        compounding = (local_gain + 0.2 * dependency_gain) / (local_gain + dependency_gain + 2.0)
        return _clamp(compounding + 0.15 * idea.evidence_discipline_score())

    def score_alignment(self, idea: Idea, *, purpose: str | None = None, area: str | None = None) -> float:
        """Score alignment against a chosen purpose and area focus."""

        purpose_tokens = _tokenize(purpose or idea.purpose)
        area_tokens = _tokenize(area or idea.target_area)
        idea_tokens = _tokenize(idea.purpose, idea.target_area, idea.hypothesis)
        purpose_match = _jaccard(purpose_tokens, idea_tokens)
        area_match = _jaccard(area_tokens, idea_tokens)
        return _clamp(0.5 * purpose_match + 0.5 * area_match)


@dataclass(frozen=True, slots=True)
class IdeaRefiner:
    """Transform ideas so they better match cost, scope, and validation demands."""

    def refine(self, idea: Idea, evaluation: EvaluationResult | None = None) -> Idea:
        """Refine an idea according to evaluation feedback.

        Lower-scoring ideas are made cheaper and more explicit.  Higher-scoring
        ideas are tightened so they become easier to validate.
        """

        if evaluation is None:
            evaluation = IdeaEvaluator().evaluate(idea)
        refined = idea
        if evaluation.feasibility < 0.6:
            refined = self.reduce_cost(refined, proportion=0.2)
            refined = self.strengthen_validation(refined, extra_evidence=("cost justification",))
        if evaluation.alignment < 0.6:
            refined = self.narrow_scope(refined, factor=0.85)
        if evaluation.novelty < 0.45:
            refined = self.widen_scope(refined, factor=1.15)
        return refined

    def narrow_scope(self, idea: Idea, *, factor: float = 0.85) -> Idea:
        """Narrow an idea so its claim becomes easier to validate."""

        factor = _clamp(factor, 0.4, 1.0)
        return replace(
            idea.with_adjusted_gain(
                theorem_delta=idea.theorem_yield() * (factor - 1.0),
                bridge_delta=idea.bridge_impact() * (factor - 1.0),
                cost_delta=idea.estimated_cost() * (factor - 1.0) * 0.8,
                uncertainty_delta=(factor - 1.0) * 0.15,
            ),
            title=f"Scoped {idea.title}",
            hypothesis=f"{idea.hypothesis} The scope is narrowed to the smallest demonstrable bridge.",
        )

    def widen_scope(self, idea: Idea, *, factor: float = 1.15) -> Idea:
        """Widen an idea when its promise seems under-specified."""

        factor = max(1.0, factor)
        return replace(
            idea.with_adjusted_gain(
                theorem_delta=idea.theorem_yield() * (factor - 1.0) * 0.7,
                bridge_delta=idea.bridge_impact() * (factor - 1.0),
                cost_delta=idea.estimated_cost() * (factor - 1.0) * 0.5,
                uncertainty_delta=(factor - 1.0) * 0.1,
                novelty_delta=0.04 * (factor - 1.0),
            ),
            title=f"Extended {idea.title}",
            hypothesis=f"{idea.hypothesis} The widened scope seeks a broader semantic landing zone.",
        )

    def strengthen_validation(self, idea: Idea, *, extra_evidence: Iterable[str] = ()) -> Idea:
        """Strengthen the idea's validation path with extra evidence expectations."""

        return replace(
            idea,
            validation_plan=idea.validation_plan.strengthened(
                evidence=extra_evidence or ("bridge audit", "counterexample search"),
                criteria=("validation artefacts remain stable under replay",),
            ),
            trust_status=TrustStatus.GROUNDED if idea.trust_status is TrustStatus.PROVISIONAL else idea.trust_status,
        )

    def reduce_cost(self, idea: Idea, *, proportion: float = 0.15) -> Idea:
        """Reduce the expected cost of an idea while preserving its purpose."""

        proportion = _clamp(proportion, 0.0, 0.75)
        return replace(
            idea.with_adjusted_gain(
                cost_delta=-idea.estimated_cost() * proportion,
                theorem_delta=-idea.theorem_yield() * proportion * 0.15,
                bridge_delta=-idea.bridge_impact() * proportion * 0.1,
                uncertainty_delta=-0.05 * proportion,
            ),
            hypothesis=f"{idea.hypothesis} The refinement removes the most expensive non-essential step.",
        )


@dataclass(slots=True)
class IdeaLifecycle:
    """Track lifecycle decisions for ideas and optionally record them in history."""

    status_by_id: dict[str, LifecycleStatus] = field(default_factory=dict)
    notes_by_id: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    history: IdeaHistory | None = None

    def _record(self, idea_id: str, event: str, status: LifecycleStatus, note: str) -> LifecycleStatus:
        """Persist lifecycle state and optionally mirror it into history."""

        self.status_by_id[idea_id] = status
        if note:
            self.notes_by_id[idea_id].append(note)
        if self.history is not None:
            self.history.record(idea_id, event, status.value, note)
        return status

    def propose(self, idea: Idea, *, note: str = "") -> LifecycleStatus:
        """Register an idea as proposed."""

        return self._record(idea.idea_id, "propose", LifecycleStatus.PROPOSED, note)

    def accept(self, idea_id: str, *, note: str = "") -> LifecycleStatus:
        """Mark a previously proposed idea as accepted."""

        if idea_id not in self.status_by_id:
            raise KeyError(idea_id)
        return self._record(idea_id, "accept", LifecycleStatus.ACCEPTED, note)

    def defer(self, idea_id: str, *, note: str = "") -> LifecycleStatus:
        """Mark an idea as deferred for later reconsideration."""

        if idea_id not in self.status_by_id:
            raise KeyError(idea_id)
        return self._record(idea_id, "defer", LifecycleStatus.DEFERRED, note)

    def reject(self, idea_id: str, *, note: str = "") -> LifecycleStatus:
        """Reject an idea and preserve the accompanying rationale."""

        if idea_id not in self.status_by_id:
            raise KeyError(idea_id)
        return self._record(idea_id, "reject", LifecycleStatus.REJECTED, note)

    def revisit(self, idea_id: str, *, note: str = "") -> LifecycleStatus:
        """Move a deferred or rejected idea back into the proposal state."""

        if self.status_by_id.get(idea_id) not in {LifecycleStatus.DEFERRED, LifecycleStatus.REJECTED, LifecycleStatus.ACCEPTED}:
            raise ValueError(f"idea {idea_id} is not in a revisit-ready state")
        return self._record(idea_id, "revisit", LifecycleStatus.PROPOSED, note)

    def retire(self, idea_id: str, *, note: str = "") -> LifecycleStatus:
        """Retire an idea when it has either succeeded fully or become obsolete."""

        if idea_id not in self.status_by_id:
            raise KeyError(idea_id)
        return self._record(idea_id, "retire", LifecycleStatus.RETIRED, note)


@dataclass(slots=True)
class IdeaDependencyGraph:
    """Directed acyclic dependency graph between idea identifiers."""

    edges: dict[str, set[str]] = field(default_factory=dict)

    def add_dependency(self, idea_id: str, prerequisite_id: str) -> None:
        """Add a dependency edge from an idea to one prerequisite."""

        self.edges.setdefault(idea_id, set()).add(prerequisite_id)
        self.edges.setdefault(prerequisite_id, set())
        if idea_id in self.prerequisites(prerequisite_id):
            self.edges[idea_id].remove(prerequisite_id)
            raise ValueError("dependency would create a cycle")

    def remove_dependency(self, idea_id: str, prerequisite_id: str) -> None:
        """Remove a direct dependency edge if present."""

        if idea_id in self.edges:
            self.edges[idea_id].discard(prerequisite_id)

    def dependencies(self, idea_id: str) -> tuple[str, ...]:
        """Return the direct dependencies for an idea."""

        return tuple(sorted(self.edges.get(idea_id, ())))

    def prerequisites(self, idea_id: str) -> tuple[str, ...]:
        """Return the transitive prerequisite closure for an idea."""

        visited: set[str] = set()

        def visit(current: str) -> None:
            for dependency in self.edges.get(current, ()):
                if dependency not in visited:
                    visited.add(dependency)
                    visit(dependency)

        visit(idea_id)
        return tuple(sorted(visited))

    def enabling_ideas(self, idea_id: str) -> tuple[str, ...]:
        """Return ideas that depend directly or indirectly on the supplied idea."""

        reverse: dict[str, set[str]] = defaultdict(set)
        for current, dependencies in self.edges.items():
            for dependency in dependencies:
                reverse[dependency].add(current)
        visited: set[str] = set()

        def visit(current: str) -> None:
            for dependent in reverse.get(current, ()):
                if dependent not in visited:
                    visited.add(dependent)
                    visit(dependent)

        visit(idea_id)
        return tuple(sorted(visited))

    def critical_paths(self, idea_id: str) -> tuple[tuple[str, ...], ...]:
        """Return maximal prerequisite paths ending at the supplied idea."""

        if not self.edges.get(idea_id):
            return ((idea_id,),)
        paths: list[tuple[str, ...]] = []
        for dependency in sorted(self.edges.get(idea_id, ())):
            for path in self.critical_paths(dependency):
                paths.append(path + (idea_id,))
        if not paths:
            return ((idea_id,),)
        max_length = max(len(path) for path in paths)
        return tuple(path for path in paths if len(path) == max_length)

    def topological_order(self) -> tuple[str, ...]:
        """Return a topological ordering of the dependency graph."""

        indegree: dict[str, int] = {idea_id: 0 for idea_id in self.edges}
        for dependencies in self.edges.values():
            for dependency in dependencies:
                indegree[dependency] = indegree.get(dependency, 0)
        for idea_id, dependencies in self.edges.items():
            indegree[idea_id] += len(dependencies)
        queue = sorted(idea_id for idea_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        reverse: dict[str, set[str]] = defaultdict(set)
        for idea_id, dependencies in self.edges.items():
            for dependency in dependencies:
                reverse[dependency].add(idea_id)
        while queue:
            current = queue.pop(0)
            order.append(current)
            for dependent in sorted(reverse.get(current, ())):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
                    queue.sort()
        if len(order) != len(indegree):
            raise ValueError("graph contains a cycle")
        return tuple(order)


@dataclass(slots=True)
class IdeaHistory:
    """Chronological store of lifecycle events and validation outcomes."""

    entries: list[HistoryEntry] = field(default_factory=list)

    def record(self, idea_id: str, event: str, status: str, notes: str = "") -> HistoryEntry:
        """Append a history event and return the stored entry."""

        entry = HistoryEntry(idea_id=idea_id, event=event, status=status, timestamp=time.time(), notes=notes)
        self.entries.append(entry)
        return entry

    def revisit_count(self, idea_id: str) -> int:
        """Count how often a given idea has been revisited."""

        return sum(1 for entry in self.entries if entry.idea_id == idea_id and entry.event == "revisit")

    def success_rate(self) -> float:
        """Return the proportion of proposed ideas that reached acceptance or retirement."""

        proposed = {entry.idea_id for entry in self.entries if entry.event == "propose"}
        successful = {
            entry.idea_id
            for entry in self.entries
            if entry.event in {"accept", "retire"} and entry.status in {LifecycleStatus.ACCEPTED.value, LifecycleStatus.RETIRED.value}
        }
        if not proposed:
            return 0.0
        return len(proposed & successful) / len(proposed)

    def time_to_validation(self, idea_id: str) -> float | None:
        """Return elapsed time from proposal to acceptance or retirement."""

        proposal_times = [entry.timestamp for entry in self.entries if entry.idea_id == idea_id and entry.event == "propose"]
        validation_times = [
            entry.timestamp
            for entry in self.entries
            if entry.idea_id == idea_id and entry.event in {"accept", "retire"}
        ]
        if not proposal_times or not validation_times:
            return None
        return min(validation_times) - min(proposal_times)

    def abandonment_reasons(self) -> dict[str, int]:
        """Aggregate rejection, deferral, and retirement reasons from notes."""

        reasons: Counter[str] = Counter()
        for entry in self.entries:
            if entry.event not in {"defer", "reject", "retire"} or not entry.notes.strip():
                continue
            reason = entry.notes.split(".")[0].split(";")[0].strip().lower()
            if reason:
                reasons[reason] += 1
        return dict(reasons)

    def events_for(self, idea_id: str) -> tuple[HistoryEntry, ...]:
        """Return all history entries for a specific idea."""

        return tuple(entry for entry in self.entries if entry.idea_id == idea_id)


class IdeaSerializer:
    """Serialize ideas and portfolios to and from JSON payloads."""

    @staticmethod
    def to_dict(idea: Idea) -> dict[str, Any]:
        """Convert an idea into a dictionary."""

        return idea.to_dict()

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Idea:
        """Construct an idea from a serialized dictionary."""

        return Idea(
            idea_id=str(data["idea_id"]),
            title=str(data["title"]),
            purpose=str(data["purpose"]),
            target_area=str(data["target_area"]),
            hypothesis=str(data["hypothesis"]),
            predicted_gain=GainProfile.from_dict(data["predicted_gain"]),
            novelty_score=float(data["novelty_score"]),
            validation_plan=ValidationPath.from_dict(data["validation_plan"]),
            trust_status=TrustStatus(str(data["trust_status"])),
        )

    @staticmethod
    def to_json(idea: Idea, *, pretty: bool = True) -> str:
        """Serialize an idea to a JSON string."""

        return json.dumps(IdeaSerializer.to_dict(idea), indent=2 if pretty else None, sort_keys=True)

    @staticmethod
    def from_json(payload: str) -> Idea:
        """Deserialize a JSON string into an idea."""

        return IdeaSerializer.from_dict(json.loads(payload))

    @staticmethod
    def portfolio_to_json(portfolio: IdeaPortfolio, *, pretty: bool = True) -> str:
        """Serialize an idea portfolio to a JSON string."""

        data = {"ideas": [idea.to_dict() for idea in portfolio.ideas.values()]}
        return json.dumps(data, indent=2 if pretty else None, sort_keys=True)

    @staticmethod
    def portfolio_from_json(payload: str) -> IdeaPortfolio:
        """Deserialize a JSON string into an idea portfolio."""

        raw = json.loads(payload)
        portfolio = IdeaPortfolio()
        for item in raw.get("ideas", ()):
            portfolio.add(IdeaSerializer.from_dict(item))
        return portfolio


@dataclass(frozen=True, slots=True)
class IdeaDiagnostics:
    """Human-readable summaries and reports for idea work."""

    evaluator: IdeaEvaluator = field(default_factory=IdeaEvaluator)

    def summary(self, idea: Idea) -> str:
        """Return a concise one-line summary for an idea."""

        result = self.evaluator.evaluate(idea)
        return (
            f"{idea.idea_id} [{idea.trust_status.value}] {idea.title}: "
            f"score={result.total_score:.2f}, yield={idea.theorem_yield():.1f}, "
            f"bridge={idea.bridge_impact():.1f}, cost={idea.estimated_cost():.1f}"
        )

    def portfolio_report(self, portfolio: IdeaPortfolio) -> str:
        """Return a multiline ranked report for a portfolio."""

        lines = [
            f"portfolio size={len(portfolio.ideas)} diversity={portfolio.diversity_score():.2f}",
        ]
        for index, (idea, score) in enumerate(portfolio.rank(self.evaluator), start=1):
            lines.append(f"{index}. {idea.idea_id} {idea.title} -> {score:.2f}")
        return "\n".join(lines)

    def novelty_report(self, portfolio: IdeaPortfolio) -> str:
        """Return a report focused on novelty distribution inside the portfolio."""

        ranked = portfolio.rank(self.evaluator)
        if not ranked:
            return "no ideas to analyze"
        values = [self.evaluator.score_novelty(idea, portfolio=portfolio.ideas.values()) for idea, _score in ranked]
        mean = sum(values) / len(values)
        best = max(ranked, key=lambda item: self.evaluator.score_novelty(item[0], portfolio=portfolio.ideas.values()))[0]
        return (
            f"novelty mean={mean:.2f}\n"
            f"most novel={best.idea_id} {best.title}\n"
            f"distribution={', '.join(f'{value:.2f}' for value in values)}"
        )

    def dependency_report(self, graph: IdeaDependencyGraph, idea_id: str) -> str:
        """Return a report focused on dependencies for one idea."""

        dependencies = ", ".join(graph.dependencies(idea_id)) or "none"
        prerequisites = ", ".join(graph.prerequisites(idea_id)) or "none"
        paths = "; ".join(" -> ".join(path) for path in graph.critical_paths(idea_id))
        return (
            f"idea={idea_id}\n"
            f"dependencies={dependencies}\n"
            f"prerequisites={prerequisites}\n"
            f"critical_paths={paths or idea_id}"
        )

    def copilot_idea_summary(self, idea: Idea) -> str:
        """Return a copilot-friendly summary that emphasizes actionability."""

        result = self.evaluator.evaluate(idea)
        return (
            f"Copilot view for {idea.idea_id}: pursue '{idea.title}' because it serves {idea.purpose}. "
            f"Expected score {result.total_score:.2f}; validation starts with {idea.validation_plan.steps[0]}."
        )


__all__ = [
    "EvaluationResult",
    "GainProfile",
    "HistoryEntry",
    "Idea",
    "IdeaDependencyGraph",
    "IdeaDiagnostics",
    "IdeaEvaluator",
    "IdeaGenerator",
    "IdeaHistory",
    "IdeaLifecycle",
    "IdeaPortfolio",
    "IdeaProposal",
    "IdeaRefiner",
    "IdeaSerializer",
    "LifecycleStatus",
    "TrustStatus",
    "ValidationPath",
]

# copilot: shared-core marker for future LLM orchestration.
