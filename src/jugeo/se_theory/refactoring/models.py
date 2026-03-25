"""Refactoring models: structure-preserving morphisms in judgment-geometry.

In the judgment-geometry framework, a *refactoring* is a morphism of sites
that preserves the descent condition.  Concretely this means:

* every overlap agreement that held before the refactoring still holds after,
* trust levels do not decrease (the refactoring is a *refinement*), and
* boundary structures (treaties, public projections) remain consistent.

The models below capture proposals, refinement relations, migration plans,
and results of applying refactoring operations.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enums
    "RefactoringKind",
    # Dataclasses
    "RefinementRelation",
    "RefactoringProposal",
    "MigrationPlan",
    "RefactoringResult",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RefactoringKind(str, Enum):
    """Catalogue of refactoring operations expressible as site morphisms."""

    RENAME = "rename"
    EXTRACT_FUNCTION = "extract_function"
    INLINE_FUNCTION = "inline_function"
    MOVE_TO_MODULE = "move_to_module"
    EXTRACT_CLASS = "extract_class"
    CHANGE_SIGNATURE = "change_signature"
    INTRODUCE_PARAMETER = "introduce_parameter"
    REPLACE_INHERITANCE_WITH_DELEGATION = "replace_inheritance_with_delegation"
    LIBRARY_MIGRATION = "library_migration"
    DECOMPOSE_CONDITIONAL = "decompose_conditional"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RefinementRelation:
    """Tracks whether a refactoring step is a valid refinement (after >= before).

    A refinement means the target judgment is at least as strong as the source
    in the trust ordering.  Equivalence holds when the refinement is
    bidirectional, i.e. trust levels are identical.
    """

    source_judgment_id: str
    target_judgment_id: str
    is_refinement: bool
    is_equivalence: bool
    delta_trust: str
    affected_propositions: list[str] = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_judgment_id": self.source_judgment_id,
            "target_judgment_id": self.target_judgment_id,
            "is_refinement": self.is_refinement,
            "is_equivalence": self.is_equivalence,
            "delta_trust": self.delta_trust,
            "affected_propositions": list(self.affected_propositions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefinementRelation:
        return cls(
            source_judgment_id=data["source_judgment_id"],
            target_judgment_id=data["target_judgment_id"],
            is_refinement=data["is_refinement"],
            is_equivalence=data["is_equivalence"],
            delta_trust=data["delta_trust"],
            affected_propositions=list(data.get("affected_propositions", [])),
        )


@dataclass
class RefactoringProposal:
    """A proposed refactoring operation on the judgment-geometry site."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: RefactoringKind = RefactoringKind.RENAME
    target_coordinates: list[str] = field(default_factory=list)
    description: str = ""
    blast_radius: int = 0
    safety_score: float = 0.0
    affected_overlaps: list[str] = field(default_factory=list)
    affected_treaties: list[str] = field(default_factory=list)
    estimated_verification_cost: float = 0.0
    preserves_descent: Optional[bool] = None

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "target_coordinates": list(self.target_coordinates),
            "description": self.description,
            "blast_radius": self.blast_radius,
            "safety_score": self.safety_score,
            "affected_overlaps": list(self.affected_overlaps),
            "affected_treaties": list(self.affected_treaties),
            "estimated_verification_cost": self.estimated_verification_cost,
            "preserves_descent": self.preserves_descent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefactoringProposal:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            kind=RefactoringKind(data["kind"]),
            target_coordinates=list(data.get("target_coordinates", [])),
            description=data.get("description", ""),
            blast_radius=data.get("blast_radius", 0),
            safety_score=data.get("safety_score", 0.0),
            affected_overlaps=list(data.get("affected_overlaps", [])),
            affected_treaties=list(data.get("affected_treaties", [])),
            estimated_verification_cost=data.get("estimated_verification_cost", 0.0),
            preserves_descent=data.get("preserves_descent"),
        )


@dataclass
class MigrationPlan:
    """Plan for migrating between libraries, expressed as a sequence of site morphisms."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    source_library: str = ""
    target_library: str = ""
    coordinate_mapping: dict[str, str] = field(default_factory=dict)
    morphism_mapping: dict[str, str] = field(default_factory=dict)
    unmapped_coordinates: list[str] = field(default_factory=list)
    compatibility_score: float = 0.0
    steps: list[RefactoringProposal] = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_library": self.source_library,
            "target_library": self.target_library,
            "coordinate_mapping": dict(self.coordinate_mapping),
            "morphism_mapping": dict(self.morphism_mapping),
            "unmapped_coordinates": list(self.unmapped_coordinates),
            "compatibility_score": self.compatibility_score,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationPlan:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            source_library=data.get("source_library", ""),
            target_library=data.get("target_library", ""),
            coordinate_mapping=dict(data.get("coordinate_mapping", {})),
            morphism_mapping=dict(data.get("morphism_mapping", {})),
            unmapped_coordinates=list(data.get("unmapped_coordinates", [])),
            compatibility_score=data.get("compatibility_score", 0.0),
            steps=[
                RefactoringProposal.from_dict(s)
                for s in data.get("steps", [])
            ],
        )


@dataclass
class RefactoringResult:
    """Outcome of applying a refactoring proposal."""

    proposal_id: str = ""
    applied: bool = False
    descent_preserved: bool = False
    regressions: list[str] = field(default_factory=list)
    new_trust_levels: dict[str, str] = field(default_factory=dict)
    verification_duration_ms: float = 0.0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "applied": self.applied,
            "descent_preserved": self.descent_preserved,
            "regressions": list(self.regressions),
            "new_trust_levels": dict(self.new_trust_levels),
            "verification_duration_ms": self.verification_duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefactoringResult:
        return cls(
            proposal_id=data.get("proposal_id", ""),
            applied=data.get("applied", False),
            descent_preserved=data.get("descent_preserved", False),
            regressions=list(data.get("regressions", [])),
            new_trust_levels=dict(data.get("new_trust_levels", {})),
            verification_duration_ms=data.get("verification_duration_ms", 0.0),
        )
