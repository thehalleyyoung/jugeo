"""Core data models for jugeo.ideation.analogy_transport (theory2.tex Ch60).

Defines the canonical data structures for analogy-based idea transport:
AnalogyMap, StructurePreservation, PurposePreservation, TransportedIdea,
and the quality/fidelity enumerations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AnalogyQuality(str, Enum):
    """Quality classification for an analogy map."""

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    PERFECT = "perfect"

    @classmethod
    def from_score(cls, score: float) -> "AnalogyQuality":
        """Derive quality from a numeric faithfulness score in [0, 1]."""
        if score >= 0.95:
            return cls.PERFECT
        if score >= 0.75:
            return cls.STRONG
        if score >= 0.45:
            return cls.MODERATE
        return cls.WEAK


class TransportFidelity(str, Enum):
    """Fidelity level of an idea transport operation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXACT = "exact"

    @classmethod
    def from_score(cls, score: float) -> "TransportFidelity":
        """Derive fidelity from a numeric score in [0, 1]."""
        if score >= 0.95:
            return cls.EXACT
        if score >= 0.75:
            return cls.HIGH
        if score >= 0.45:
            return cls.MEDIUM
        return cls.LOW

    def to_float(self) -> float:
        """Convert fidelity level to a representative float value."""
        return {
            TransportFidelity.LOW: 0.25,
            TransportFidelity.MEDIUM: 0.60,
            TransportFidelity.HIGH: 0.85,
            TransportFidelity.EXACT: 1.0,
        }[self]


# ---------------------------------------------------------------------------
# AnalogyMap
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AnalogyMap:
    """An analogy between two domains, given by a set of correspondences.

    Parameters
    ----------
    map_id:
        Unique identifier for this map.
    source_domain:
        Name of the source domain.
    target_domain:
        Name of the target domain.
    correspondences:
        Tuple of ``(source_concept, target_concept)`` pairs.
    faithfulness_score:
        Fraction of source structure preserved in the target (0–1).
    coverage_score:
        Fraction of source concepts covered by the map (0–1).
    created_at:
        ISO-8601 creation timestamp.
    """

    map_id: str
    source_domain: str
    target_domain: str
    correspondences: tuple[tuple[str, str], ...]
    faithfulness_score: float
    coverage_score: float
    created_at: str

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def quality(self) -> AnalogyQuality:
        """Return the quality classification of this analogy."""
        return AnalogyQuality.from_score(self.faithfulness_score)

    def invert(self) -> "AnalogyMap":
        """Return the inverse analogy (target → source)."""
        return AnalogyMap(
            map_id=_uid(),
            source_domain=self.target_domain,
            target_domain=self.source_domain,
            correspondences=tuple((t, s) for s, t in self.correspondences),
            faithfulness_score=self.faithfulness_score,
            coverage_score=self.coverage_score,
            created_at=_now_iso(),
        )

    def compose(self, other: "AnalogyMap") -> "AnalogyMap | None":
        """Compose this map with *other*, where self: A→B and other: B→C.

        Returns ``None`` when the domains are incompatible.
        """
        if self.target_domain != other.source_domain:
            return None
        # Build composed correspondences by chaining through the bridge domain.
        bridge: dict[str, str] = dict(self.correspondences)
        other_map: dict[str, str] = dict(other.correspondences)
        composed: list[tuple[str, str]] = []
        for src, mid in bridge.items():
            if mid in other_map:
                composed.append((src, other_map[mid]))
        combined_faith = self.faithfulness_score * other.faithfulness_score
        combined_coverage = self.coverage_score * other.coverage_score
        return AnalogyMap(
            map_id=_uid(),
            source_domain=self.source_domain,
            target_domain=other.target_domain,
            correspondences=tuple(composed),
            faithfulness_score=combined_faith,
            coverage_score=combined_coverage,
            created_at=_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "map_id": self.map_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "correspondences": list(self.correspondences),
            "faithfulness_score": self.faithfulness_score,
            "coverage_score": self.coverage_score,
            "created_at": self.created_at,
            "quality": self.quality().value,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"AnalogyMap({self.source_domain!r} → {self.target_domain!r}, "
            f"faith={self.faithfulness_score:.2f}, "
            f"coverage={self.coverage_score:.2f}, "
            f"quality={self.quality().value})"
        )


# ---------------------------------------------------------------------------
# StructurePreservation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StructurePreservation:
    """Audit result for how well an analogy preserves domain structure.

    Parameters
    ----------
    preservation_id:
        Unique identifier for this audit result.
    map_id:
        ID of the :class:`AnalogyMap` that was audited.
    preserved_relations:
        Tuple of relation names that are faithfully preserved.
    violated_relations:
        Tuple of relation names that are violated or distorted.
    preservation_score:
        Aggregate preservation quality in [0, 1].
    checked_at:
        ISO-8601 timestamp of when the audit was performed.
    """

    preservation_id: str
    map_id: str
    preserved_relations: tuple[str, ...]
    violated_relations: tuple[str, ...]
    preservation_score: float
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "preservation_id": self.preservation_id,
            "map_id": self.map_id,
            "preserved_relations": list(self.preserved_relations),
            "violated_relations": list(self.violated_relations),
            "preservation_score": self.preservation_score,
            "checked_at": self.checked_at,
        }


# ---------------------------------------------------------------------------
# PurposePreservation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PurposePreservation:
    """Measures how well an analogy preserves the epistemic purpose of an idea.

    Parameters
    ----------
    preservation_id:
        Unique identifier.
    source_purpose:
        Purpose description in the source domain.
    target_purpose:
        Purpose description in the target domain.
    semantic_alignment:
        Semantic overlap score in [0, 1].
    goal_alignment_score:
        Goal-level alignment score in [0, 1].
    created_at:
        ISO-8601 creation timestamp.
    """

    preservation_id: str
    source_purpose: str
    target_purpose: str
    semantic_alignment: float
    goal_alignment_score: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "preservation_id": self.preservation_id,
            "source_purpose": self.source_purpose,
            "target_purpose": self.target_purpose,
            "semantic_alignment": self.semantic_alignment,
            "goal_alignment_score": self.goal_alignment_score,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# TransportedIdea
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TransportedIdea:
    """An idea that has been transported across domains via an analogy map.

    Parameters
    ----------
    transport_id:
        Unique identifier for this transport record.
    original_idea_id:
        ID of the source :class:`~jugeo.ideation.ideas.Idea`.
    target_domain:
        Name of the target domain.
    transported_content:
        Dictionary representation of the transported idea.
    analogy_map_id:
        ID of the :class:`AnalogyMap` used for transport.
    trust_attenuation:
        Amount by which trust was attenuated during transport.
    fidelity:
        Transport fidelity level.
    created_at:
        ISO-8601 creation timestamp.
    """

    transport_id: str
    original_idea_id: str
    target_domain: str
    transported_content: dict[str, Any]
    analogy_map_id: str
    trust_attenuation: float
    fidelity: TransportFidelity
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_id": self.transport_id,
            "original_idea_id": self.original_idea_id,
            "target_domain": self.target_domain,
            "transported_content": self.transported_content,
            "analogy_map_id": self.analogy_map_id,
            "trust_attenuation": self.trust_attenuation,
            "fidelity": self.fidelity.value,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# AnalogyVerification
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AnalogyVerification:
    """Result of verifying an :class:`AnalogyMap`.

    Parameters
    ----------
    verification_id:
        Unique identifier.
    map_id:
        ID of the map that was verified.
    verification_steps:
        Tuple of step descriptions performed during verification.
    passed_checks:
        Tuple of check names that passed.
    failed_checks:
        Tuple of check names that failed.
    is_valid:
        Whether the analogy passed all required checks.
    confidence:
        Overall confidence score in [0, 1].
    verified_at:
        ISO-8601 timestamp.
    """

    verification_id: str
    map_id: str
    verification_steps: tuple[str, ...]
    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    is_valid: bool
    confidence: float
    verified_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "map_id": self.map_id,
            "verification_steps": list(self.verification_steps),
            "passed_checks": list(self.passed_checks),
            "failed_checks": list(self.failed_checks),
            "is_valid": self.is_valid,
            "confidence": self.confidence,
            "verified_at": self.verified_at,
        }


__all__ = [
    "AnalogyQuality",
    "TransportFidelity",
    "AnalogyMap",
    "StructurePreservation",
    "PurposePreservation",
    "TransportedIdea",
    "AnalogyVerification",
]
