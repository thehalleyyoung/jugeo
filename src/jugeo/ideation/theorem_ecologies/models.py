"""Core data models for theorem ecologies — theory2.tex Ch61.

Module layout::

    EcologyHealth        – enum: health tier for a theorem ecology
    DynamicType          – enum: ecological dynamic type
    TheoremEcology       – frozen dataclass: a theorem ecology snapshot
    LemmaPortfolio       – frozen dataclass: a portfolio of reusable lemmas
    CompoundingEffect    – frozen dataclass: a detected compounding effect
    EcologicalDynamic    – frozen dataclass: a pairwise ecological dynamic
    PortfolioOptimization – frozen dataclass: result of portfolio optimization
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    return time.time()


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EcologyHealth(str, Enum):
    """Health tier for a theorem ecology."""

    CRITICAL = "critical"
    POOR = "poor"
    FAIR = "fair"
    GOOD = "good"
    EXCELLENT = "excellent"

    def __lt__(self, other: EcologyHealth) -> bool:  # type: ignore[override]
        order = [EcologyHealth.CRITICAL, EcologyHealth.POOR,
                 EcologyHealth.FAIR, EcologyHealth.GOOD, EcologyHealth.EXCELLENT]
        return order.index(self) < order.index(other)

    def numeric(self) -> float:
        """Return a numeric representation in [0, 1]."""
        mapping = {
            EcologyHealth.CRITICAL: 0.0,
            EcologyHealth.POOR: 0.25,
            EcologyHealth.FAIR: 0.5,
            EcologyHealth.GOOD: 0.75,
            EcologyHealth.EXCELLENT: 1.0,
        }
        return mapping[self]


class DynamicType(str, Enum):
    """Types of ecological dynamics between theorem/lemma nodes."""

    SYMBIOSIS = "symbiosis"
    COMPETITION = "competition"
    PARASITISM = "parasitism"
    COMMENSALISM = "commensalism"
    MUTUALISM = "mutualism"
    NEUTRALISM = "neutralism"

    def is_positive(self) -> bool:
        return self in (DynamicType.SYMBIOSIS, DynamicType.MUTUALISM,
                        DynamicType.COMMENSALISM)

    def is_negative(self) -> bool:
        return self in (DynamicType.COMPETITION, DynamicType.PARASITISM)


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TheoremEcology:
    """Snapshot of a theorem ecology at a point in time.

    Attributes
    ----------
    ecology_id:
        Unique identifier for this ecology instance.
    name:
        Human-readable name for the ecology.
    theorem_ids:
        Tuple of theorem node identifiers in this ecology.
    lemma_ids:
        Tuple of lemma node identifiers in this ecology.
    dependencies:
        Mapping from node_id to tuple of dependency node_ids.
    health_score:
        Overall health score in [0, 1].
    diversity_index:
        Diversity index in [0, 1].
    created_at:
        Unix timestamp of creation.
    metadata:
        Arbitrary metadata dictionary.
    """

    ecology_id: str = field(default_factory=_uid)
    name: str = "unnamed_ecology"
    theorem_ids: tuple[str, ...] = ()
    lemma_ids: tuple[str, ...] = ()
    dependencies: dict[str, tuple[str, ...]] = field(default_factory=dict)
    health_score: float = 0.0
    diversity_index: float = 0.0
    created_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "health_score", max(0.0, min(1.0, self.health_score)))
        object.__setattr__(self, "diversity_index", max(0.0, min(1.0, self.diversity_index)))

    @property
    def all_node_ids(self) -> tuple[str, ...]:
        return self.theorem_ids + self.lemma_ids

    @property
    def size(self) -> int:
        return len(self.theorem_ids) + len(self.lemma_ids)

    @property
    def health_tier(self) -> EcologyHealth:
        if self.health_score >= 0.85:
            return EcologyHealth.EXCELLENT
        elif self.health_score >= 0.65:
            return EcologyHealth.GOOD
        elif self.health_score >= 0.45:
            return EcologyHealth.FAIR
        elif self.health_score >= 0.25:
            return EcologyHealth.POOR
        else:
            return EcologyHealth.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "ecology_id": self.ecology_id,
            "name": self.name,
            "theorem_ids": list(self.theorem_ids),
            "lemma_ids": list(self.lemma_ids),
            "dependencies": {k: list(v) for k, v in self.dependencies.items()},
            "health_score": self.health_score,
            "diversity_index": self.diversity_index,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoremEcology:
        return cls(
            ecology_id=data.get("ecology_id", _uid()),
            name=data.get("name", "unnamed_ecology"),
            theorem_ids=tuple(data.get("theorem_ids", [])),
            lemma_ids=tuple(data.get("lemma_ids", [])),
            dependencies={k: tuple(v) for k, v in data.get("dependencies", {}).items()},
            health_score=data.get("health_score", 0.0),
            diversity_index=data.get("diversity_index", 0.0),
            created_at=data.get("created_at", _utcnow()),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class LemmaPortfolio:
    """A managed portfolio of reusable lemmas.

    Attributes
    ----------
    portfolio_id:
        Unique identifier.
    name:
        Human-readable label.
    lemma_ids:
        Ordered tuple of lemma identifiers in this portfolio.
    utility_scores:
        Mapping from lemma_id to estimated utility in [0, 1].
    coverage:
        Fraction of target theorems covered by this portfolio in [0, 1].
    reuse_counts:
        How many times each lemma has been reused.
    created_at:
        Unix timestamp of creation.
    """

    portfolio_id: str = field(default_factory=_uid)
    name: str = "unnamed_portfolio"
    lemma_ids: tuple[str, ...] = ()
    utility_scores: dict[str, float] = field(default_factory=dict)
    coverage: float = 0.0
    reuse_counts: dict[str, int] = field(default_factory=dict)
    created_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", max(0.0, min(1.0, self.coverage)))

    @property
    def size(self) -> int:
        return len(self.lemma_ids)

    def utility_of(self, lemma_id: str) -> float:
        return self.utility_scores.get(lemma_id, 0.0)

    def reuse_count_of(self, lemma_id: str) -> int:
        return self.reuse_counts.get(lemma_id, 0)

    def average_utility(self) -> float:
        if not self.utility_scores:
            return 0.0
        return sum(self.utility_scores.values()) / len(self.utility_scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "name": self.name,
            "lemma_ids": list(self.lemma_ids),
            "utility_scores": dict(self.utility_scores),
            "coverage": self.coverage,
            "reuse_counts": dict(self.reuse_counts),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LemmaPortfolio:
        return cls(
            portfolio_id=data.get("portfolio_id", _uid()),
            name=data.get("name", "unnamed_portfolio"),
            lemma_ids=tuple(data.get("lemma_ids", [])),
            utility_scores=data.get("utility_scores", {}),
            coverage=data.get("coverage", 0.0),
            reuse_counts=data.get("reuse_counts", {}),
            created_at=data.get("created_at", _utcnow()),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class CompoundingEffect:
    """A detected semantic compounding effect between theorem/lemma nodes.

    Compounding occurs when two or more nodes amplify each other's utility
    beyond the sum of their individual contributions.

    Attributes
    ----------
    effect_id:
        Unique identifier for this compounding effect.
    source_ids:
        Tuple of node IDs that participate in this compound.
    compound_result:
        Human-readable description of the compounded insight.
    synergy:
        Estimated synergy value in [0, 1].
    amplification_factor:
        How much the compound exceeds individual contributions (>= 1.0).
    confidence:
        Confidence in this detection in [0, 1].
    required_conditions:
        Any preconditions required for this compound to hold.
    detected_at:
        Unix timestamp of detection.
    """

    effect_id: str = field(default_factory=_uid)
    source_ids: tuple[str, ...] = ()
    compound_result: str = ""
    synergy: float = 0.0
    amplification_factor: float = 1.0
    confidence: float = 0.0
    required_conditions: tuple[str, ...] = ()
    detected_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "synergy", max(0.0, min(1.0, self.synergy)))
        object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))
        object.__setattr__(self, "amplification_factor",
                           max(1.0, self.amplification_factor))

    @property
    def order(self) -> int:
        """Number of sources (2 = pairwise, 3 = triple, etc.)."""
        return len(self.source_ids)

    def net_value(self) -> float:
        """Weighted combination of synergy, amplification, and confidence."""
        return self.synergy * self.confidence * min(self.amplification_factor / 2.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "source_ids": list(self.source_ids),
            "compound_result": self.compound_result,
            "synergy": self.synergy,
            "amplification_factor": self.amplification_factor,
            "confidence": self.confidence,
            "required_conditions": list(self.required_conditions),
            "detected_at": self.detected_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompoundingEffect:
        return cls(
            effect_id=data.get("effect_id", _uid()),
            source_ids=tuple(data.get("source_ids", [])),
            compound_result=data.get("compound_result", ""),
            synergy=data.get("synergy", 0.0),
            amplification_factor=data.get("amplification_factor", 1.0),
            confidence=data.get("confidence", 0.0),
            required_conditions=tuple(data.get("required_conditions", [])),
            detected_at=data.get("detected_at", _utcnow()),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class EcologicalDynamic:
    """A pairwise ecological dynamic between two theorem/lemma nodes.

    Ecological dynamics model the interaction pattern between nodes in a
    theorem ecology.  Positive dynamics (symbiosis, mutualism) strengthen
    both parties; negative dynamics (competition, parasitism) reduce the
    effective contribution of at least one party.

    Attributes
    ----------
    dynamic_id:
        Unique identifier for this dynamic.
    dynamic_type:
        The type of ecological interaction.
    source_id:
        The initiating node.
    target_id:
        The receiving node.
    strength:
        Interaction strength in [0, 1].
    notes:
        Optional human-readable annotation.
    observed_at:
        Unix timestamp of observation.
    """

    dynamic_id: str = field(default_factory=_uid)
    dynamic_type: DynamicType = DynamicType.NEUTRALISM
    source_id: str = ""
    target_id: str = ""
    strength: float = 0.0
    notes: str = ""
    observed_at: float = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "strength", max(0.0, min(1.0, self.strength)))

    def is_beneficial(self) -> bool:
        return self.dynamic_type.is_positive()

    def is_harmful(self) -> bool:
        return self.dynamic_type.is_negative()

    def signed_strength(self) -> float:
        """Strength with sign: positive for beneficial, negative for harmful."""
        if self.is_harmful():
            return -self.strength
        return self.strength

    def to_dict(self) -> dict[str, Any]:
        return {
            "dynamic_id": self.dynamic_id,
            "dynamic_type": self.dynamic_type.value,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "strength": self.strength,
            "notes": self.notes,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class PortfolioOptimization:
    """Result of a portfolio optimization pass.

    Attributes
    ----------
    optimization_id:
        Unique identifier for this optimization event.
    portfolio_id:
        The portfolio that was optimized.
    removed_lemmas:
        Lemmas that were pruned from the portfolio.
    added_lemmas:
        Lemmas that were added to the portfolio.
    coverage_before:
        Coverage fraction before optimization.
    coverage_after:
        Coverage fraction after optimization.
    utility_improvement:
        Absolute improvement in average utility.
    notes:
        Human-readable summary.
    optimized_at:
        Unix timestamp.
    """

    optimization_id: str = field(default_factory=_uid)
    portfolio_id: str = ""
    removed_lemmas: tuple[str, ...] = ()
    added_lemmas: tuple[str, ...] = ()
    coverage_before: float = 0.0
    coverage_after: float = 0.0
    utility_improvement: float = 0.0
    notes: str = ""
    optimized_at: float = field(default_factory=_utcnow)

    @property
    def coverage_delta(self) -> float:
        return self.coverage_after - self.coverage_before

    @property
    def net_lemma_change(self) -> int:
        return len(self.added_lemmas) - len(self.removed_lemmas)

    def is_improvement(self) -> bool:
        return self.coverage_delta > 0 or self.utility_improvement > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimization_id": self.optimization_id,
            "portfolio_id": self.portfolio_id,
            "removed_lemmas": list(self.removed_lemmas),
            "added_lemmas": list(self.added_lemmas),
            "coverage_before": self.coverage_before,
            "coverage_after": self.coverage_after,
            "utility_improvement": self.utility_improvement,
            "notes": self.notes,
            "optimized_at": self.optimized_at,
        }


__all__ = [
    "EcologyHealth",
    "DynamicType",
    "TheoremEcology",
    "LemmaPortfolio",
    "CompoundingEffect",
    "EcologicalDynamic",
    "PortfolioOptimization",
]
