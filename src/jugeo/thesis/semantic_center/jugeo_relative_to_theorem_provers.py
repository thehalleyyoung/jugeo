"""Comparative chapter surface for JuGeo relative to theorem provers and peers.

This module is the machine-readable companion to
``preliminaries/theory2.tex`` section 2 of chapter 1,
"JuGeo relative to theorem provers, coding assistants, and agentic
verifiers", together with the compiled witness ``preliminaries/theory2.pdf``
and the structural hints recorded in ``theory2-src-blueprint.json`` and
``theory2-generation-order.json``.

Design commitments
------------------
* Preserve the section's worldview in explicit Python records rather than in
  free-floating prose.
* Keep each trust boundary, semantic boundary, and settlement authority
  readable in plain text and in typed data.
* Support both the blueprint's canonical classes
  ``JuGeoRelativeTheoremProversCoordinator``,
  ``JuGeoRelativeTheoremProversAnalyzer``, and
  ``JuGeoRelativeTheoremProversWitness`` and the older package imports
  ``ComparativePositioning``, ``TheoremProverRelation``,
  ``DepTypeRelation``, ``ModelCheckerRelation``, and ``SolverRelation``.
* Use real local-to-global logic where possible by adapting to
  ``jugeo.geometry.descent`` rather than faking a comparison layer.
* Stay honest about current scope: this file analyzes comparative positioning
  and scenario-level gluing, but it does not claim to replace JuGeo's future
  theorem catalog or package-wide integration layer. In other words, the
  future theorem catalog remains future theorem catalog work, and the
  package-wide integration layer remains separate.

Theory-facing summary
---------------------
The governing section makes three contrasts explicit.

* Relative to theorem provers, JuGeo preserves explicit proof obligations and
  evidence provenance while adding a local-to-global state model.
* Relative to coding assistants, JuGeo rejects plausible text as a success
  condition.
* Relative to ordinary agentic verifiers, JuGeo models covers, overlap
  treaties, obstruction classes, and replay-local invalidation rather than
  reducing orchestration to a queue over files.

The same section also states concrete advantages that this module surfaces as
first-class data and analyzable contracts.

* Repair complexity can be quantified rather than merely reported as failure.
* Equivalence can be treated as a descent question for relational evidence.
* Incremental analysis can be justified algebraically rather than with coarse
  cache heuristics.
* Specification checking can use product covers that expose reusable overlap
  transport.
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from types import MappingProxyType
from typing import Any, Final, Iterable, Mapping, Sequence

from jugeo.errors import FailureClassification, FailureScope, StructuredFailure, raise_with_scope
from jugeo.evidence.trust import TrustAlgebra, TrustLevel
from jugeo.geometry.descent import GluingData, LocalSection, OverlapCondition
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.thesis.semantic_center.manifest import (
    CHAPTER_NUMBER,
    CHAPTER_TITLE,
    INTRODUCTION_SOURCE_SECTIONS,
    MANIFEST_SPEC_PROVENANCE,
    PART_NUMBER,
    WORLDVIEW_COMMITMENTS,
)
from jugeo.thesis.semantic_center.models import JUGEO_WORLDVIEW, JuGeoWorldview

__all__ = [
    "S02_SPEC_PROVENANCE",
    "SECTION_WORLDVIEW_LINES",
    "SECTION_ADVANTAGES",
    "SECTION_RUNTIME_OBJECTS",
    "SECTION_CAPABILITY_IDS",
    "ToolKind",
    "ComparisonVerdict",
    "CapabilityKind",
    "EvidenceMapping",
    "ToolProfile",
    "ComparativeCapability",
    "ComparativeObservation",
    "ComparativeGap",
    "RepairComplexityEstimate",
    "ComparativeAssessment",
    "ComparativeScenarioReport",
    "JuGeoRelativeTheoremProversWitness",
    "JuGeoRelativeTheoremProversAnalyzer",
    "JuGeoRelativeTheoremProversCoordinator",
    "ComparativePositioning",
    "FormalToolRelation",
    "TheoremProverRelation",
    "DepTypeRelation",
    "ModelCheckerRelation",
    "SolverRelation",
    "DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS",
    "DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_COORDINATOR",
    "COMPARATIVE_POSITIONING",
]

S02_SPEC_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        "semantic_source": str(MANIFEST_SPEC_PROVENANCE["semantic_source"]),
        "semantic_source_role": str(MANIFEST_SPEC_PROVENANCE["semantic_source_role"]),
        "semantic_source_pdf": str(MANIFEST_SPEC_PROVENANCE["semantic_source_pdf"]),
        "semantic_pdf_role": str(MANIFEST_SPEC_PROVENANCE["semantic_pdf_role"]),
        "structural_blueprint": str(MANIFEST_SPEC_PROVENANCE["structural_blueprint"]),
        "structural_generation_order": str(MANIFEST_SPEC_PROVENANCE["structural_generation_order"]),
        "structural_hint_role": str(MANIFEST_SPEC_PROVENANCE["structural_hint_role"]),
        "target_file": "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
        "target_test": "tests/jugeo/thesis/semantic_center/test_jugeo_relative_to_theorem_provers.py",
        "stage": "chapter-01",
        "sequence": 62,
        "chapter_number": CHAPTER_NUMBER,
        "part_number": PART_NUMBER,
        "chapter_title": CHAPTER_TITLE,
        "section_title": INTRODUCTION_SOURCE_SECTIONS[1],
    }
)

SECTION_WORLDVIEW_LINES: Final[tuple[str, ...]] = (
    "JuGeo preserves explicit proof obligations and evidence provenance from theorem-prover culture.",
    "JuGeo adds a local-to-global state model that ordinary proof scripts rarely maintain directly.",
    "JuGeo refuses to treat plausible generated text as success merely because the text looks convincing.",
    "JuGeo does not reduce agentic verification to queueing tools over files.",
    "JuGeo gives the controller access to covers, overlap treaties, obstruction classes, and replay-local invalidation.",
)

SECTION_ADVANTAGES: Final[tuple[str, ...]] = (
    "JuGeo can quantify repair complexity rather than merely report failure.",
    "JuGeo can state equivalence as a global descent question for relational evidence.",
    "JuGeo can justify incremental analysis algebraically via Mayer-Vietoris style recomputation.",
    "JuGeo can factor specification checking through product covers and overlap transport.",
)

SECTION_RUNTIME_OBJECTS: Final[tuple[str, ...]] = (
    "explicit covers and hypercovers",
    "overlap matrices or treaty graphs",
    "coboundary operators for admitted exact fragments",
    "gluing reports",
    "replay seals",
)

SECTION_CAPABILITY_IDS: Final[tuple[str, ...]] = (
    "proof-provenance-preservation",
    "geometric-orchestration",
    "repair-complexity-quantification",
    "equivalence-by-descent",
    "mayer-vietoris-incrementality",
    "product-cover-specification-checking",
)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _normalize_text_tuple(values: Iterable[str] | str | None, *, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    items = (values,) if isinstance(values, str) else tuple(values)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_required_text(item, field_name=field_name)
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_mapping(value: Mapping[str, Any] | None, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return MappingProxyType({str(key): value[key] for key in value})


def _normalize_coordinate(
    value: Coordinate | Sequence[str] | str,
    *,
    field_name: str,
    kind: CoordinateKind = CoordinateKind.REGION,
) -> Coordinate:
    if isinstance(value, Coordinate):
        return value
    if isinstance(value, str):
        text = _normalize_required_text(value, field_name=field_name)
        if "/" in text:
            parts = tuple(part for part in text.split("/") if part)
        elif "." in text:
            parts = tuple(part for part in text.split(".") if part)
        else:
            parts = (text,)
        return Coordinate(parts, kind=kind)
    return Coordinate(tuple(str(part).strip() for part in value if str(part).strip()), kind=kind)


def _normalize_claim_text(value: str) -> str:
    return " ".join(_normalize_required_text(value, field_name="claim").lower().split())


def _stable_json(value: Any) -> str:
    def _convert(item: Any) -> Any:
        if isinstance(item, Coordinate):
            return item.serialize()
        if isinstance(item, TrustLevel):
            return item.value
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, Mapping):
            return {str(key): _convert(item[key]) for key in sorted(item)}
        if isinstance(item, tuple):
            return [_convert(entry) for entry in item]
        if isinstance(item, list):
            return [_convert(entry) for entry in item]
        if isinstance(item, set):
            return sorted(_convert(entry) for entry in item)
        return item

    return json.dumps(_convert(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_digest(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(_stable_json(part).encode("utf-8"))
    return digest.hexdigest()[:16]


def _aggregate_trust(levels: Sequence[TrustLevel]) -> TrustLevel:
    if not levels:
        return TrustLevel.UNVERIFIED
    algebra = TrustAlgebra()
    current = levels[0]
    for level in levels[1:]:
        current = algebra.meet(current, level)
    return current


def _trust_meets_floor(level: TrustLevel, floor: TrustLevel) -> bool:
    return level >= floor


def _unique_text(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _trust_fraction(level: TrustLevel) -> float:
    ordered = TrustLevel.ordered()
    denominator = max(1, len(ordered) - 1)
    return level.rank_index() / denominator


def _failure_for_invalid_input(message: str, *, coordinate: str, metadata: Mapping[str, Any] | None = None) -> StructuredFailure:
    return StructuredFailure(
        message=message,
        scope=FailureScope.CHAPTER,
        classification=FailureClassification.INVALID_VALUE,
        coordinate=coordinate,
        trust_boundary="s02-input",
        metadata=dict(metadata or {}),
        recoverable=True,
    )


class ToolKind(str, Enum):
    """Tool families named by the comparative section."""

    THEOREM_PROVER = "theorem_prover"
    DEPENDENT_TYPE_ASSISTANT = "dependent_type_assistant"
    MODEL_CHECKER = "model_checker"
    SMT_SOLVER = "smt_solver"
    CODING_ASSISTANT = "coding_assistant"
    AGENTIC_VERIFIER = "agentic_verifier"
    JUGEO = "jugeo"


class ComparisonVerdict(str, Enum):
    """High-level verdict for a tool's relation to JuGeo."""

    PRESERVED_AND_EXTENDED = "preserved_and_extended"
    LOCALLY_STRONG_BUT_GLOBALLY_INCOMPLETE = "locally_strong_but_globally_incomplete"
    PROPOSAL_ONLY = "proposal_only"
    ORCHESTRATION_WITHOUT_GEOMETRY = "orchestration_without_geometry"
    NATIVE_JUGEO_CAPABILITY = "native_jugeo_capability"


class CapabilityKind(str, Enum):
    """Kinds of capabilities surfaced by the section's comparison."""

    TRUST_PRESERVATION = "trust_preservation"
    GEOMETRIC_ORCHESTRATION = "geometric_orchestration"
    REPAIR_ANALYSIS = "repair_analysis"
    EQUIVALENCE = "equivalence"
    INCREMENTALITY = "incrementality"
    SPECIFICATION_CHECKING = "specification_checking"


@dataclass(frozen=True, slots=True)
class EvidenceMapping:
    """How a peer tool's native artifact enters JuGeo's semantic accounting."""

    tool_kind: ToolKind
    native_artifact: str
    jugeo_evidence_role: str
    trust_ceiling: TrustLevel
    settlement_authority: str
    provenance: tuple[str, ...] = ()
    semantic_boundary: str = "chapter-01/s02 comparative positioning"
    obligations_supported: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "native_artifact", _normalize_required_text(self.native_artifact, field_name="native_artifact"))
        object.__setattr__(self, "jugeo_evidence_role", _normalize_required_text(self.jugeo_evidence_role, field_name="jugeo_evidence_role"))
        object.__setattr__(self, "settlement_authority", _normalize_required_text(self.settlement_authority, field_name="settlement_authority"))
        object.__setattr__(self, "semantic_boundary", _normalize_required_text(self.semantic_boundary, field_name="semantic_boundary"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        object.__setattr__(self, "obligations_supported", _normalize_text_tuple(self.obligations_supported, field_name="obligations_supported"))
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))
        if not isinstance(self.trust_ceiling, TrustLevel):
            raise TypeError("trust_ceiling must be a TrustLevel")

    def summary_line(self) -> str:
        return (
            f"{self.tool_kind.value}: {self.native_artifact} -> {self.jugeo_evidence_role} "
            f"@ {self.trust_ceiling.value}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_kind": self.tool_kind.value,
            "native_artifact": self.native_artifact,
            "jugeo_evidence_role": self.jugeo_evidence_role,
            "trust_ceiling": self.trust_ceiling.value,
            "settlement_authority": self.settlement_authority,
            "provenance": list(self.provenance),
            "semantic_boundary": self.semantic_boundary,
            "obligations_supported": list(self.obligations_supported),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ToolProfile:
    """Stable comparative profile for one peer tool family."""

    tool_name: str
    tool_kind: ToolKind
    exemplars: tuple[str, ...]
    retained_strengths: tuple[str, ...]
    missing_semantic_objects: tuple[str, ...]
    jugeo_additions: tuple[str, ...]
    evidence_mapping: EvidenceMapping
    theory_alignment: str
    trust_boundary: str
    authority_boundary: str
    supports_local_proof: bool
    supports_global_descent: bool
    uses_cover_objects: bool
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_name", _normalize_required_text(self.tool_name, field_name="tool_name"))
        object.__setattr__(self, "theory_alignment", _normalize_required_text(self.theory_alignment, field_name="theory_alignment"))
        object.__setattr__(self, "trust_boundary", _normalize_required_text(self.trust_boundary, field_name="trust_boundary"))
        object.__setattr__(self, "authority_boundary", _normalize_required_text(self.authority_boundary, field_name="authority_boundary"))
        object.__setattr__(self, "exemplars", _normalize_text_tuple(self.exemplars, field_name="exemplars"))
        object.__setattr__(self, "retained_strengths", _normalize_text_tuple(self.retained_strengths, field_name="retained_strengths"))
        object.__setattr__(self, "missing_semantic_objects", _normalize_text_tuple(self.missing_semantic_objects, field_name="missing_semantic_objects"))
        object.__setattr__(self, "jugeo_additions", _normalize_text_tuple(self.jugeo_additions, field_name="jugeo_additions"))
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))
        if not self.retained_strengths:
            raise ValueError("retained_strengths must be non-empty")
        if not self.missing_semantic_objects and self.tool_kind is not ToolKind.JUGEO:
            raise ValueError("non-JuGeo profiles must name missing semantic objects")

    def preserves_explicit_provenance(self) -> bool:
        return any("provenance" in item.lower() for item in self.retained_strengths)

    def short_label(self) -> str:
        return f"{self.tool_name} ({self.tool_kind.value})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_kind": self.tool_kind.value,
            "exemplars": list(self.exemplars),
            "retained_strengths": list(self.retained_strengths),
            "missing_semantic_objects": list(self.missing_semantic_objects),
            "jugeo_additions": list(self.jugeo_additions),
            "evidence_mapping": self.evidence_mapping.to_dict(),
            "theory_alignment": self.theory_alignment,
            "trust_boundary": self.trust_boundary,
            "authority_boundary": self.authority_boundary,
            "supports_local_proof": self.supports_local_proof,
            "supports_global_descent": self.supports_global_descent,
            "uses_cover_objects": self.uses_cover_objects,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ComparativeCapability:
    """A concrete JuGeo capability articulated by the comparative section."""

    capability_id: str
    title: str
    kind: CapabilityKind
    description: str
    theorem_anchor: str
    concrete_runtime_objects: tuple[str, ...]
    partially_present_in: tuple[ToolKind, ...] = ()
    delivered_by_jugeo: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _normalize_required_text(self.capability_id, field_name="capability_id"))
        object.__setattr__(self, "title", _normalize_required_text(self.title, field_name="title"))
        object.__setattr__(self, "description", _normalize_required_text(self.description, field_name="description"))
        object.__setattr__(self, "theorem_anchor", _normalize_required_text(self.theorem_anchor, field_name="theorem_anchor"))
        object.__setattr__(self, "concrete_runtime_objects", _normalize_text_tuple(self.concrete_runtime_objects, field_name="concrete_runtime_objects"))
        if not self.concrete_runtime_objects:
            raise ValueError("concrete_runtime_objects must be non-empty")
        for entry in self.partially_present_in:
            if not isinstance(entry, ToolKind):
                raise TypeError("partially_present_in entries must be ToolKind values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "title": self.title,
            "kind": self.kind.value,
            "description": self.description,
            "theorem_anchor": self.theorem_anchor,
            "concrete_runtime_objects": list(self.concrete_runtime_objects),
            "partially_present_in": [entry.value for entry in self.partially_present_in],
            "delivered_by_jugeo": self.delivered_by_jugeo,
        }


@dataclass(frozen=True, slots=True)
class ComparativeObservation:
    """One local observation used in a comparative scenario analysis.

    The observation intentionally preserves both trust and locality. This makes
    it possible to ask honest questions such as whether a theorem prover and a
    solver agree on an overlap, or whether a copilot proposal remains merely a
    local suggestion because the overlap treaty still fails.
    """

    observation_id: str
    coordinate: Coordinate
    tool_kind: ToolKind
    claim: str
    clauses: tuple[str, ...]
    trust_level: TrustLevel
    residual_obligations: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    overlap_tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _normalize_required_text(self.observation_id, field_name="observation_id"))
        object.__setattr__(self, "coordinate", _normalize_coordinate(self.coordinate, field_name="coordinate"))
        object.__setattr__(self, "claim", _normalize_required_text(self.claim, field_name="claim"))
        object.__setattr__(self, "clauses", _normalize_text_tuple(self.clauses, field_name="clauses"))
        object.__setattr__(self, "residual_obligations", _normalize_text_tuple(self.residual_obligations, field_name="residual_obligations"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        object.__setattr__(self, "overlap_tags", _normalize_text_tuple(self.overlap_tags, field_name="overlap_tags"))
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))
        object.__setattr__(self, "metadata", _normalize_mapping(self.metadata, field_name="metadata"))
        if not isinstance(self.trust_level, TrustLevel):
            raise TypeError("trust_level must be a TrustLevel")
        if not self.clauses:
            raise ValueError("clauses must be non-empty")

    @property
    def normalized_claim(self) -> str:
        return _normalize_claim_text(self.claim)

    @property
    def coordinate_key(self) -> str:
        return self.coordinate.key

    def clause_set(self) -> frozenset[str]:
        return frozenset(_normalize_claim_text(clause) for clause in self.clauses)

    def overlaps_with(self, other: ComparativeObservation) -> bool:
        if self.coordinate_key == other.coordinate_key:
            return True
        if self.normalized_claim == other.normalized_claim:
            return True
        return bool(set(self.overlap_tags).intersection(other.overlap_tags))

    def to_local_section(self) -> LocalSection:
        return LocalSection(
            coordinate=self.coordinate_key,
            judgment_data={
                "claim": self.normalized_claim,
                "clauses": tuple(sorted(self.clause_set())),
                "overlap_tags": tuple(sorted(self.overlap_tags)),
            },
            evidence_bundle=(f"tool:{self.tool_kind.value}",) + tuple(f"prov:{entry}" for entry in self.provenance),
            trust_level=_trust_fraction(self.trust_level),
            provenance=tuple(self.provenance),
            is_partial=bool(self.residual_obligations),
            residual_obligations=list(self.residual_obligations),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "coordinate": self.coordinate.serialize(),
            "tool_kind": self.tool_kind.value,
            "claim": self.claim,
            "clauses": list(self.clauses),
            "trust_level": self.trust_level.value,
            "residual_obligations": list(self.residual_obligations),
            "provenance": list(self.provenance),
            "overlap_tags": list(self.overlap_tags),
            "metadata": dict(self.metadata),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ComparativeGap:
    """A violated overlap or treaty gap discovered during scenario analysis."""

    gap_id: str
    claim: str
    left_observation_id: str
    right_observation_id: str
    overlap_key: str
    shared_clauses: tuple[str, ...]
    conflicting_clauses: tuple[str, ...]
    cocycle_payload: Mapping[str, Any]
    repair_hints: tuple[str, ...]
    theory_reference: str = "chapter-01/s02"

    def __post_init__(self) -> None:
        object.__setattr__(self, "gap_id", _normalize_required_text(self.gap_id, field_name="gap_id"))
        object.__setattr__(self, "claim", _normalize_required_text(self.claim, field_name="claim"))
        object.__setattr__(self, "left_observation_id", _normalize_required_text(self.left_observation_id, field_name="left_observation_id"))
        object.__setattr__(self, "right_observation_id", _normalize_required_text(self.right_observation_id, field_name="right_observation_id"))
        object.__setattr__(self, "overlap_key", _normalize_required_text(self.overlap_key, field_name="overlap_key"))
        object.__setattr__(self, "shared_clauses", _normalize_text_tuple(self.shared_clauses, field_name="shared_clauses"))
        object.__setattr__(self, "conflicting_clauses", _normalize_text_tuple(self.conflicting_clauses, field_name="conflicting_clauses"))
        object.__setattr__(self, "repair_hints", _normalize_text_tuple(self.repair_hints, field_name="repair_hints"))
        object.__setattr__(self, "theory_reference", _normalize_required_text(self.theory_reference, field_name="theory_reference"))
        object.__setattr__(self, "cocycle_payload", _normalize_mapping(self.cocycle_payload, field_name="cocycle_payload"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "claim": self.claim,
            "left_observation_id": self.left_observation_id,
            "right_observation_id": self.right_observation_id,
            "overlap_key": self.overlap_key,
            "shared_clauses": list(self.shared_clauses),
            "conflicting_clauses": list(self.conflicting_clauses),
            "cocycle_payload": dict(self.cocycle_payload),
            "repair_hints": list(self.repair_hints),
            "theory_reference": self.theory_reference,
        }


@dataclass(frozen=True, slots=True)
class RepairComplexityEstimate:
    """Repair-count estimate motivated by the section's obstruction language."""

    binary_conflict_rank: int
    independent_fix_lower_bound: int
    exact_fragment_fix_count: int | None
    affected_claims: tuple[str, ...]
    supporting_gap_ids: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "affected_claims", _normalize_text_tuple(self.affected_claims, field_name="affected_claims"))
        object.__setattr__(self, "supporting_gap_ids", _normalize_text_tuple(self.supporting_gap_ids, field_name="supporting_gap_ids"))
        object.__setattr__(self, "explanation", _normalize_required_text(self.explanation, field_name="explanation"))

    @classmethod
    def from_gaps(cls, gaps: Sequence[ComparativeGap]) -> RepairComplexityEstimate:
        if not gaps:
            return cls(
                binary_conflict_rank=0,
                independent_fix_lower_bound=0,
                exact_fragment_fix_count=0,
                affected_claims=(),
                supporting_gap_ids=(),
                explanation=(
                    "No violated overlaps were found, so the first obstruction space "
                    "is treated as trivial for this scenario."
                ),
            )
        claims = _unique_text(gap.claim for gap in gaps)
        rank = len(claims)
        exact_fragment_count: int | None = rank
        for gap in gaps:
            if len(gap.conflicting_clauses) != 1:
                exact_fragment_count = None
                break
        if exact_fragment_count is None:
            explanation = (
                "The number of affected claims yields a lower bound on independent "
                "repairs. Because some overlap discrepancies involve multi-clause "
                "conflicts, the exact POPL-style fragment count is not asserted."
            )
        else:
            explanation = (
                "Each violated claim contributes one binary-fragment discrepancy, so "
                "the lower bound and the exact fragment count coincide for this case."
            )
        return cls(
            binary_conflict_rank=rank,
            independent_fix_lower_bound=rank,
            exact_fragment_fix_count=exact_fragment_count,
            affected_claims=claims,
            supporting_gap_ids=tuple(gap.gap_id for gap in gaps),
            explanation=explanation,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary_conflict_rank": self.binary_conflict_rank,
            "independent_fix_lower_bound": self.independent_fix_lower_bound,
            "exact_fragment_fix_count": self.exact_fragment_fix_count,
            "affected_claims": list(self.affected_claims),
            "supporting_gap_ids": list(self.supporting_gap_ids),
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class ComparativeAssessment:
    """Summary of what JuGeo preserves or adds relative to one tool family."""

    tool_profile: ToolProfile
    verdict: ComparisonVerdict
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rationale", _normalize_required_text(self.rationale, field_name="rationale"))

    @property
    def tool_kind(self) -> ToolKind:
        return self.tool_profile.tool_kind

    @property
    def tool_name(self) -> str:
        return self.tool_profile.tool_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_profile": self.tool_profile.to_dict(),
            "verdict": self.verdict.value,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class ComparativeScenarioReport:
    """Typed result of comparing multiple local observations."""

    scenario_id: str
    observations: tuple[ComparativeObservation, ...]
    gaps: tuple[ComparativeGap, ...]
    repair_estimate: RepairComplexityEstimate
    aggregate_trust: TrustLevel
    trust_floor: TrustLevel
    residual_obligations: tuple[str, ...]
    honest_to_publish: bool
    gluing_summary: str
    recommended_next_moves: tuple[str, ...]
    cited_advantages: tuple[str, ...]
    semantic_boundary: str
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _normalize_required_text(self.scenario_id, field_name="scenario_id"))
        object.__setattr__(self, "residual_obligations", _normalize_text_tuple(self.residual_obligations, field_name="residual_obligations"))
        object.__setattr__(self, "gluing_summary", _normalize_required_text(self.gluing_summary, field_name="gluing_summary"))
        object.__setattr__(self, "recommended_next_moves", _normalize_text_tuple(self.recommended_next_moves, field_name="recommended_next_moves"))
        object.__setattr__(self, "cited_advantages", _normalize_text_tuple(self.cited_advantages, field_name="cited_advantages"))
        object.__setattr__(self, "semantic_boundary", _normalize_required_text(self.semantic_boundary, field_name="semantic_boundary"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "observations": [observation.to_dict() for observation in self.observations],
            "gaps": [gap.to_dict() for gap in self.gaps],
            "repair_estimate": self.repair_estimate.to_dict(),
            "aggregate_trust": self.aggregate_trust.value,
            "trust_floor": self.trust_floor.value,
            "residual_obligations": list(self.residual_obligations),
            "honest_to_publish": self.honest_to_publish,
            "gluing_summary": self.gluing_summary,
            "recommended_next_moves": list(self.recommended_next_moves),
            "cited_advantages": list(self.cited_advantages),
            "semantic_boundary": self.semantic_boundary,
            "provenance": list(self.provenance),
        }

    def render_summary(self) -> str:
        status = "publishable" if self.honest_to_publish else "not yet publishable"
        return (
            f"Scenario {self.scenario_id} is {status}; "
            f"trust={self.aggregate_trust.value}, gaps={len(self.gaps)}, "
            f"residuals={len(self.residual_obligations)}"
        )


@dataclass(frozen=True, slots=True)
class JuGeoRelativeTheoremProversWitness:
    """Stable witness bundle for the chapter section's comparative claims."""

    witness_id: str
    section_title: str
    semantic_positioning_lines: tuple[str, ...]
    advantages: tuple[str, ...]
    runtime_objects: tuple[str, ...]
    profiles: tuple[ToolProfile, ...]
    capability_catalog: tuple[ComparativeCapability, ...]
    worldview_commitments: tuple[str, ...]
    authority_boundary: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "witness_id", _normalize_required_text(self.witness_id, field_name="witness_id"))
        object.__setattr__(self, "section_title", _normalize_required_text(self.section_title, field_name="section_title"))
        object.__setattr__(self, "semantic_positioning_lines", _normalize_text_tuple(self.semantic_positioning_lines, field_name="semantic_positioning_lines"))
        object.__setattr__(self, "advantages", _normalize_text_tuple(self.advantages, field_name="advantages"))
        object.__setattr__(self, "runtime_objects", _normalize_text_tuple(self.runtime_objects, field_name="runtime_objects"))
        object.__setattr__(self, "worldview_commitments", _normalize_text_tuple(self.worldview_commitments, field_name="worldview_commitments"))
        object.__setattr__(self, "authority_boundary", _normalize_required_text(self.authority_boundary, field_name="authority_boundary"))
        object.__setattr__(self, "provenance", _normalize_mapping(self.provenance, field_name="provenance"))
        if not self.profiles:
            raise ValueError("profiles must be non-empty")
        if not self.capability_catalog:
            raise ValueError("capability_catalog must be non-empty")

    def profile_by_kind(self, tool_kind: ToolKind) -> ToolProfile:
        for profile in self.profiles:
            if profile.tool_kind is tool_kind:
                return profile
        raise_with_scope(
            "missing-tool-profile",
            message=f"No comparative profile exists for {tool_kind.value}",
            scope=FailureScope.CHAPTER,
            classification=FailureClassification.MISSING_KEY,
            coordinate="chapter-01/s02/witness/profile_by_kind",
            trust_boundary="comparative-profile-selection",
            recoverable=True,
        )

    def capability_by_id(self, capability_id: str) -> ComparativeCapability:
        normalized = _normalize_required_text(capability_id, field_name="capability_id")
        for capability in self.capability_catalog:
            if capability.capability_id == normalized:
                return capability
        raise_with_scope(
            "missing-capability",
            message=f"No comparative capability exists for {normalized}",
            scope=FailureScope.CHAPTER,
            classification=FailureClassification.MISSING_KEY,
            coordinate="chapter-01/s02/witness/capability_by_id",
            trust_boundary="comparative-capability-selection",
            recoverable=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "section_title": self.section_title,
            "semantic_positioning_lines": list(self.semantic_positioning_lines),
            "advantages": list(self.advantages),
            "runtime_objects": list(self.runtime_objects),
            "profiles": [profile.to_dict() for profile in self.profiles],
            "capability_catalog": [capability.to_dict() for capability in self.capability_catalog],
            "worldview_commitments": list(self.worldview_commitments),
            "authority_boundary": self.authority_boundary,
            "provenance": dict(self.provenance),
        }

    def render_digest(self) -> str:
        return _stable_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ComparativePositioning:
    """Compatibility-facing comparative package surface.

    This class preserves the legacy package export while carrying the blueprint's
    richer witness and assessment information underneath.
    """

    witness: JuGeoRelativeTheoremProversWitness
    assessments: tuple[ComparativeAssessment, ...]
    worldview: JuGeoWorldview = JUGEO_WORLDVIEW

    def assessment_by_kind(self, tool_kind: ToolKind) -> ComparativeAssessment:
        for assessment in self.assessments:
            if assessment.tool_kind is tool_kind:
                return assessment
        raise_with_scope(
            "missing-assessment",
            message=f"No comparative assessment exists for {tool_kind.value}",
            scope=FailureScope.CHAPTER,
            classification=FailureClassification.MISSING_KEY,
            coordinate="chapter-01/s02/positioning/assessment_by_kind",
            trust_boundary="comparative-positioning",
            recoverable=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness": self.witness.to_dict(),
            "assessments": [assessment.to_dict() for assessment in self.assessments],
            "worldview_record_id": self.worldview.record_id,
        }

    def render_table(self) -> str:
        lines = ["JuGeo comparative positioning"]
        for assessment in self.assessments:
            lines.append(f"- {assessment.tool_name}: {assessment.verdict.value}")
        return "\n".join(lines)


class FormalToolRelation:
    """OO facade around a tool profile for callers that prefer methods."""

    TOOL_KIND: ToolKind = ToolKind.JUGEO

    def __init__(self, profile: ToolProfile) -> None:
        self._profile = profile

    @property
    def profile(self) -> ToolProfile:
        return self._profile

    def what_tool_does(self) -> str:
        return "; ".join(self._profile.retained_strengths)

    def what_jugeo_adds(self) -> str:
        return "; ".join(self._profile.jugeo_additions)

    def evidence_mapping(self) -> EvidenceMapping:
        return self._profile.evidence_mapping

    def limitations_addressed(self) -> tuple[str, ...]:
        return self._profile.missing_semantic_objects

    def what_jugeo_does_not_replace(self) -> str:
        if self._profile.tool_kind is ToolKind.CODING_ASSISTANT:
            return "JuGeo does not replace fast proposal and drafting loops."
        if self._profile.tool_kind is ToolKind.AGENTIC_VERIFIER:
            return "JuGeo does not replace ordinary tool orchestration; it refines it with geometry."
        return "JuGeo does not replace the tool's specialized local reasoning strengths."

    def copilot_summary(self) -> str:
        mapping = self.evidence_mapping().summary_line()
        return textwrap.dedent(
            f"""
            Relation: {self._profile.short_label()}
            Mapping: {mapping}
            Retained strengths: {'; '.join(self._profile.retained_strengths)}
            JuGeo additions: {'; '.join(self._profile.jugeo_additions)}
            Limitations addressed: {'; '.join(self._profile.missing_semantic_objects)}
            """
        ).strip()


class TheoremProverRelation(FormalToolRelation):
    TOOL_KIND = ToolKind.THEOREM_PROVER

    def __init__(self, profile: ToolProfile | None = None) -> None:
        super().__init__(profile or DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS.profile_by_kind(self.TOOL_KIND))


class DepTypeRelation(FormalToolRelation):
    TOOL_KIND = ToolKind.DEPENDENT_TYPE_ASSISTANT

    def __init__(self, profile: ToolProfile | None = None) -> None:
        super().__init__(profile or DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS.profile_by_kind(self.TOOL_KIND))


class ModelCheckerRelation(FormalToolRelation):
    TOOL_KIND = ToolKind.MODEL_CHECKER

    def __init__(self, profile: ToolProfile | None = None) -> None:
        super().__init__(profile or DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS.profile_by_kind(self.TOOL_KIND))


class SolverRelation(FormalToolRelation):
    TOOL_KIND = ToolKind.SMT_SOLVER

    def __init__(self, profile: ToolProfile | None = None) -> None:
        super().__init__(profile or DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS.profile_by_kind(self.TOOL_KIND))


class JuGeoRelativeTheoremProversAnalyzer:
    """Analyzer for the chapter's comparative claims and example scenarios."""

    def __init__(
        self,
        witness: JuGeoRelativeTheoremProversWitness,
        *,
        worldview: JuGeoWorldview = JUGEO_WORLDVIEW,
        algebra: TrustAlgebra | None = None,
    ) -> None:
        self.witness = witness
        self.worldview = worldview
        self.algebra = algebra or TrustAlgebra()

    def profile(self, tool: ToolKind | str) -> ToolProfile:
        tool_kind = tool if isinstance(tool, ToolKind) else ToolKind(_normalize_required_text(tool, field_name="tool"))
        return self.witness.profile_by_kind(tool_kind)

    def compare_tool(self, tool: ToolKind | str | ToolProfile | FormalToolRelation) -> ComparativeAssessment:
        if isinstance(tool, FormalToolRelation):
            profile = tool.profile
        elif isinstance(tool, ToolProfile):
            profile = tool
        else:
            profile = self.profile(tool)
        verdict_map = {
            ToolKind.THEOREM_PROVER: ComparisonVerdict.PRESERVED_AND_EXTENDED,
            ToolKind.DEPENDENT_TYPE_ASSISTANT: ComparisonVerdict.PRESERVED_AND_EXTENDED,
            ToolKind.MODEL_CHECKER: ComparisonVerdict.LOCALLY_STRONG_BUT_GLOBALLY_INCOMPLETE,
            ToolKind.SMT_SOLVER: ComparisonVerdict.LOCALLY_STRONG_BUT_GLOBALLY_INCOMPLETE,
            ToolKind.CODING_ASSISTANT: ComparisonVerdict.PROPOSAL_ONLY,
            ToolKind.AGENTIC_VERIFIER: ComparisonVerdict.ORCHESTRATION_WITHOUT_GEOMETRY,
            ToolKind.JUGEO: ComparisonVerdict.NATIVE_JUGEO_CAPABILITY,
        }
        rationale = (
            f"JuGeo retains {profile.tool_name}'s local strengths while adding "
            f"{'; '.join(profile.jugeo_additions[:2])}."
            if profile.tool_kind is not ToolKind.JUGEO
            else "JuGeo is the only profile here that natively treats global settlement as a descent question."
        )
        return ComparativeAssessment(tool_profile=profile, verdict=verdict_map[profile.tool_kind], rationale=rationale)

    def compare_tooling_landscape(self, tool_kinds: Sequence[ToolKind] | None = None) -> tuple[ComparativeAssessment, ...]:
        selected = tuple(tool_kinds) if tool_kinds is not None else tuple(profile.tool_kind for profile in self.witness.profiles)
        return tuple(self.compare_tool(kind) for kind in selected)

    def explain_jugeo_advantages(self) -> tuple[str, ...]:
        return tuple(capability.title for capability in self.witness.capability_catalog if capability.delivered_by_jugeo)

    def analyze_observations(
        self,
        observations: Sequence[ComparativeObservation],
        *,
        trust_floor: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
    ) -> ComparativeScenarioReport:
        if not observations:
            failure = _failure_for_invalid_input(
                "comparative scenario analysis requires at least one observation",
                coordinate="chapter-01/s02/analyze_observations",
            )
            raise_with_scope(
                "missing-observations",
                message=failure.message,
                scope=failure.scope,
                classification=failure.classification,
                coordinate=failure.coordinate,
                trust_boundary=failure.trust_boundary,
                recoverable=True,
            )
        normalized_observations = tuple(observations)
        gluing = self._build_gluing_data(normalized_observations)
        gluing.verify_all_overlaps()
        cocycle = gluing.compute_cocycle()
        gaps = self._build_gaps(normalized_observations, gluing, cocycle.cocycle_data)
        repair_estimate = RepairComplexityEstimate.from_gaps(gaps)
        aggregate_trust = _aggregate_trust([observation.trust_level for observation in normalized_observations])
        residual_obligations = _unique_text(
            obligation
            for observation in normalized_observations
            for obligation in observation.residual_obligations
        )
        honest_to_publish = not gaps and not residual_obligations and _trust_meets_floor(aggregate_trust, trust_floor)
        recommended_moves: list[str] = []
        if gaps:
            recommended_moves.append("strengthen overlap treaty or refine the cover around the conflicting claim")
            recommended_moves.append("seek stronger evidence on each violated overlap before claiming global settlement")
        if residual_obligations:
            recommended_moves.append("discharge remaining obligations before reporting closure")
        if not _trust_meets_floor(aggregate_trust, trust_floor):
            recommended_moves.append("raise the trust floor with solver-backed, runtime-backed, or mechanically verified evidence")
        if not recommended_moves:
            recommended_moves.append("the local family currently glues under the declared trust floor")
        gluing_summary = gluing.summary()
        scenario_id = _stable_digest(
            [observation.to_dict() for observation in normalized_observations],
            trust_floor.value,
            gluing_summary,
        )
        return ComparativeScenarioReport(
            scenario_id=scenario_id,
            observations=normalized_observations,
            gaps=gaps,
            repair_estimate=repair_estimate,
            aggregate_trust=aggregate_trust,
            trust_floor=trust_floor,
            residual_obligations=residual_obligations,
            honest_to_publish=honest_to_publish,
            gluing_summary=gluing_summary,
            recommended_next_moves=tuple(recommended_moves),
            cited_advantages=self.explain_jugeo_advantages(),
            semantic_boundary="chapter-01/s02 comparative scenario analysis",
            provenance=("theory2.tex", "theory2.pdf", "jugeo.geometry.descent"),
        )

    def _build_gluing_data(self, observations: Sequence[ComparativeObservation]) -> GluingData:
        gluing = GluingData()
        for observation in observations:
            gluing.add_section(observation.to_local_section())
        for left, right in combinations(observations, 2):
            if not left.overlaps_with(right):
                continue
            overlap_key = f"{left.coordinate_key}∩{right.coordinate_key}"
            gluing.add_overlap(
                OverlapCondition(
                    left_coordinate=left.coordinate_key,
                    right_coordinate=right.coordinate_key,
                    overlap_coordinate=overlap_key,
                    compatibility_predicate=self._compatible_local_sections,
                )
            )
        return gluing

    @staticmethod
    def _compatible_local_sections(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        left_claim = _normalize_claim_text(str(left.get("claim", "")))
        right_claim = _normalize_claim_text(str(right.get("claim", "")))
        left_clauses = tuple(left.get("clauses", ()))
        right_clauses = tuple(right.get("clauses", ()))
        return left_claim == right_claim and tuple(sorted(left_clauses)) == tuple(sorted(right_clauses))

    def _build_gaps(
        self,
        observations: Sequence[ComparativeObservation],
        gluing: GluingData,
        cocycle_payload: Mapping[str, Any],
    ) -> tuple[ComparativeGap, ...]:
        observation_map = {observation.coordinate_key: observation for observation in observations}
        gaps: list[ComparativeGap] = []
        for overlap in gluing.find_violated_overlaps():
            left_observation = observation_map[overlap.left_coordinate]
            right_observation = observation_map[overlap.right_coordinate]
            overlap_payload = cocycle_payload.get(overlap.overlap_key, {})
            shared_clauses = tuple(sorted(left_observation.clause_set().intersection(right_observation.clause_set())))
            conflicting_clauses = tuple(
                sorted(left_observation.clause_set().symmetric_difference(right_observation.clause_set()))
            )
            claim = left_observation.claim if left_observation.normalized_claim == right_observation.normalized_claim else (
                f"{left_observation.claim} / {right_observation.claim}"
            )
            gap_id = _stable_digest(left_observation.observation_id, right_observation.observation_id, overlap.overlap_key)
            repair_hints = (
                "align the local clauses on the overlap",
                "record an explicit overlap treaty if the clauses are equivalent under transport",
                "refine the cover if the disagreement is genuinely localized",
            )
            gaps.append(
                ComparativeGap(
                    gap_id=gap_id,
                    claim=claim,
                    left_observation_id=left_observation.observation_id,
                    right_observation_id=right_observation.observation_id,
                    overlap_key=overlap.overlap_key,
                    shared_clauses=shared_clauses,
                    conflicting_clauses=conflicting_clauses,
                    cocycle_payload=overlap_payload if isinstance(overlap_payload, Mapping) else {"payload": overlap_payload},
                    repair_hints=repair_hints,
                )
            )
        return tuple(gaps)


class JuGeoRelativeTheoremProversCoordinator:
    """Convenience coordinator bundling witness, analyzer, and facade surfaces."""

    def __init__(
        self,
        witness: JuGeoRelativeTheoremProversWitness,
        analyzer: JuGeoRelativeTheoremProversAnalyzer | None = None,
        *,
        worldview: JuGeoWorldview = JUGEO_WORLDVIEW,
    ) -> None:
        self.witness = witness
        self.worldview = worldview
        self.analyzer = analyzer or JuGeoRelativeTheoremProversAnalyzer(witness, worldview=worldview)

    @classmethod
    def build_default(cls) -> JuGeoRelativeTheoremProversCoordinator:
        witness = build_default_witness()
        return cls(witness=witness, worldview=JUGEO_WORLDVIEW)

    def compare_tool(self, tool: ToolKind | str | ToolProfile | FormalToolRelation) -> ComparativeAssessment:
        return self.analyzer.compare_tool(tool)

    def compare_tooling_landscape(self, tool_kinds: Sequence[ToolKind] | None = None) -> ComparativePositioning:
        return ComparativePositioning(
            witness=self.witness,
            assessments=self.analyzer.compare_tooling_landscape(tool_kinds),
            worldview=self.worldview,
        )

    def analyze_scenario(
        self,
        observations: Sequence[ComparativeObservation],
        *,
        trust_floor: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
    ) -> ComparativeScenarioReport:
        return self.analyzer.analyze_observations(observations, trust_floor=trust_floor)

    def render_operator_briefing(self) -> str:
        positioning = self.compare_tooling_landscape(
            (
                ToolKind.THEOREM_PROVER,
                ToolKind.CODING_ASSISTANT,
                ToolKind.AGENTIC_VERIFIER,
                ToolKind.JUGEO,
            )
        )
        lines = [
            INTRODUCTION_SOURCE_SECTIONS[1],
            f"Witness: {self.witness.render_digest()}",
            *[f"- {line}" for line in SECTION_WORLDVIEW_LINES],
            positioning.render_table(),
        ]
        return "\n".join(lines)


def _mapping(
    tool_kind: ToolKind,
    native_artifact: str,
    jugeo_evidence_role: str,
    trust_ceiling: TrustLevel,
    settlement_authority: str,
    *,
    obligations_supported: Sequence[str],
    notes: Sequence[str] = (),
) -> EvidenceMapping:
    return EvidenceMapping(
        tool_kind=tool_kind,
        native_artifact=native_artifact,
        jugeo_evidence_role=jugeo_evidence_role,
        trust_ceiling=trust_ceiling,
        settlement_authority=settlement_authority,
        provenance=("theory2.tex", "theory2.pdf", "s02 comparative map"),
        obligations_supported=tuple(obligations_supported),
        notes=tuple(notes),
    )


def _build_default_profiles() -> tuple[ToolProfile, ...]:
    theorem_prover = ToolProfile(
        tool_name="Theorem prover",
        tool_kind=ToolKind.THEOREM_PROVER,
        exemplars=("Lean", "Coq", "Isabelle"),
        retained_strengths=(
            "explicit proof obligations",
            "evidence provenance",
            "mechanically checkable local proofs",
        ),
        missing_semantic_objects=(
            "local-to-global project state model",
            "explicit covers and hypercovers",
            "overlap treaties",
            "replay-local invalidation",
            "repair complexity quantification across a project cover",
        ),
        jugeo_additions=(
            "local-to-global settlement by descent rather than by isolated proof success",
            "obstruction-class reporting for failed glue",
            "support-aware reopening after local change",
        ),
        evidence_mapping=_mapping(
            ToolKind.THEOREM_PROVER,
            "proof term or proof script",
            "local proof-backed section",
            TrustLevel.MECHANICALLY_VERIFIED,
            "May settle local obligations, but JuGeo decides whether those local results glue into honest global closure.",
            obligations_supported=("proof obligations", "refinement obligations"),
        ),
        theory_alignment="Preserve theorem-prover strengths while adding local-to-global semantic state.",
        trust_boundary="proof artifacts remain proof-backed and are not silently widened into global settlement.",
        authority_boundary="theorem provers certify local proofs; JuGeo certifies declared-scope closure after descent.",
        supports_local_proof=True,
        supports_global_descent=False,
        uses_cover_objects=False,
        notes=("ordinary proof scripts rarely store explicit overlap treaties",),
    )
    dep_type = ToolProfile(
        tool_name="Dependent-type assistant",
        tool_kind=ToolKind.DEPENDENT_TYPE_ASSISTANT,
        exemplars=("Agda", "Idris 2"),
        retained_strengths=(
            "context-sensitive judgments",
            "typed inhabitants as evidence",
            "explicit proof obligations and provenance",
        ),
        missing_semantic_objects=(
            "project-scale cover bookkeeping",
            "mixed-evidence gluing across channels",
            "obstruction objects spanning code, tests, and documentation",
        ),
        jugeo_additions=(
            "mixed-evidence transport across semantic coordinates",
            "descent-aware reporting across modules and interfaces",
            "explicit support regions for reopening",
        ),
        evidence_mapping=_mapping(
            ToolKind.DEPENDENT_TYPE_ASSISTANT,
            "typed term inhabiting a proposition",
            "dependent local section",
            TrustLevel.MECHANICALLY_VERIFIED,
            "Can settle dependent local obligations but not by itself the project-level gluing question.",
            obligations_supported=("typing obligations", "proof obligations"),
        ),
        theory_alignment="Preserve dependent judgment discipline while extending it with semantic geometry.",
        trust_boundary="typed evidence stays typed evidence; no silent promotion to whole-project settlement.",
        authority_boundary="dependent-type assistants own local typing/proof soundness, not cross-cover orchestration.",
        supports_local_proof=True,
        supports_global_descent=False,
        uses_cover_objects=False,
        notes=("close in spirit to JuGeo's DTT layer but not sufficient for the AG layer on its own",),
    )
    model_checker = ToolProfile(
        tool_name="Model checker",
        tool_kind=ToolKind.MODEL_CHECKER,
        exemplars=("TLA+", "SPIN", "NuSMV"),
        retained_strengths=(
            "systematic state-space exploration",
            "temporal and safety counterexamples",
            "fast local falsification",
        ),
        missing_semantic_objects=(
            "explicit semantic coordinate site shared with proofs and generated artifacts",
            "descent-based equivalence witnesses",
            "replay-local invalidation with chapter-level treaty objects",
        ),
        jugeo_additions=(
            "counterexamples placed inside a common judgment geometry",
            "equivalence tracked as descended relational evidence",
            "global obstruction accounting across overlapping semantic regions",
        ),
        evidence_mapping=_mapping(
            ToolKind.MODEL_CHECKER,
            "counterexample trace or checked temporal property",
            "state-space evidence on a local coordinate",
            TrustLevel.SOLVER_DISCHARGED,
            "Can settle the checked model property locally; JuGeo decides how it interacts with other local sections.",
            obligations_supported=("temporal obligations", "safety obligations"),
        ),
        theory_alignment="Preserve counterexample power while integrating it into a shared local-to-global state.",
        trust_boundary="trace evidence is strong for the checked model but not automatically a global certificate.",
        authority_boundary="model checkers own local state exploration, not mixed-evidence gluing across a project.",
        supports_local_proof=False,
        supports_global_descent=False,
        uses_cover_objects=False,
        notes=("especially valuable when fed back into obstruction and repair-frontier objects",),
    )
    solver = ToolProfile(
        tool_name="SMT solver",
        tool_kind=ToolKind.SMT_SOLVER,
        exemplars=("Z3", "CVC5", "Yices"),
        retained_strengths=(
            "high-throughput obligation discharge",
            "precise arithmetic and logical reasoning",
            "useful local unsat/sat witnesses",
        ),
        missing_semantic_objects=(
            "cover-aware specification factoring",
            "overlap treaties for transporting local discharge",
            "repair-count semantics over project obstructions",
        ),
        jugeo_additions=(
            "product-cover discipline for specification checking",
            "transport of local discharge across overlaps when justified",
            "global obstruction objects when local solver success does not glue",
        ),
        evidence_mapping=_mapping(
            ToolKind.SMT_SOLVER,
            "solver discharge result",
            "solver-backed local section",
            TrustLevel.SOLVER_DISCHARGED,
            "Can discharge local solver obligations, but JuGeo owns the declaration that the resulting family coheres globally.",
            obligations_supported=("arithmetic obligations", "logical obligations", "path-by-conjunct obligations"),
        ),
        theory_alignment="Preserve fast local discharge while adding a geometric account of how discharge transports.",
        trust_boundary="solver discharge remains local to the encoded obligation unless explicitly transported.",
        authority_boundary="solvers own encoded local discharge, not project-scale cover selection or treaty law.",
        supports_local_proof=True,
        supports_global_descent=False,
        uses_cover_objects=False,
        notes=("the section's product-cover example is specifically about making solver work compositional",),
    )
    coding_assistant = ToolProfile(
        tool_name="Coding assistant",
        tool_kind=ToolKind.CODING_ASSISTANT,
        exemplars=("copilot", "cursor", "chat completion systems"),
        retained_strengths=(
            "fast proposal and drafting",
            "semantic search over nearby text and APIs",
            "useful candidate local sections",
        ),
        missing_semantic_objects=(
            "explicit proof obligations",
            "trustworthy evidence provenance",
            "obstruction classes",
            "declared-scope closure criteria",
        ),
        jugeo_additions=(
            "proposal channels held below settlement authority",
            "trust ceilings that prevent silent promotion",
            "explicit repair-frontier integration when proposals fail to glue",
        ),
        evidence_mapping=_mapping(
            ToolKind.CODING_ASSISTANT,
            "plausible generated text or patch proposal",
            "oracle-style candidate local section",
            TrustLevel.COPILOT_SUGGESTED,
            "May propose sections and refinements but may not certify truth or publishable closure by itself.",
            obligations_supported=("draft obligations",),
            notes=("copilot text is useful search guidance but not a certificate",),
        ),
        theory_alignment="Reject plausible text as a success condition while still using proposal power.",
        trust_boundary="copilot-backed proposals remain below solver and proof evidence unless explicitly justified.",
        authority_boundary="coding assistants can suggest, summarize, and route; they cannot settle global closure.",
        supports_local_proof=False,
        supports_global_descent=False,
        uses_cover_objects=False,
        notes=("the comparative section names this failure mode explicitly",),
    )
    agentic = ToolProfile(
        tool_name="Agentic verifier",
        tool_kind=ToolKind.AGENTIC_VERIFIER,
        exemplars=("tool orchestration loops", "multi-step CI controllers"),
        retained_strengths=(
            "tool orchestration",
            "batch execution over multiple artifacts",
            "pragmatic integration of tests, solvers, and static checks",
        ),
        missing_semantic_objects=(
            "covers and hypercovers as first-class orchestration state",
            "overlap treaties",
            "obstruction classes",
            "replay-local invalidation seals",
        ),
        jugeo_additions=(
            "geometric orchestration rather than queueing over files",
            "support-aware reopening after local edits",
            "explicit gluing reports and replay seals",
        ),
        evidence_mapping=_mapping(
            ToolKind.AGENTIC_VERIFIER,
            "controller log or orchestration summary",
            "routing and aggregation metadata",
            TrustLevel.UNVERIFIED,
            "May summarize work and collect evidence, but the summary itself does not discharge the underlying obligations.",
            obligations_supported=("routing obligations",),
        ),
        theory_alignment="Preserve useful orchestration but demand explicit geometry for local-to-global control.",
        trust_boundary="controller summaries inherit the trust of collected evidence instead of creating trust themselves.",
        authority_boundary="agentic verifiers route tools; JuGeo adds the semantic ontology that governs reopening and gluing.",
        supports_local_proof=False,
        supports_global_descent=False,
        uses_cover_objects=False,
        notes=("the chapter explicitly contrasts this with geometry-aware control",),
    )
    jugeo = ToolProfile(
        tool_name="JuGeo",
        tool_kind=ToolKind.JUGEO,
        exemplars=("judgment geometry runtime",),
        retained_strengths=(
            "explicit proof obligations and provenance where available",
            "covers, overlap treaties, and obstruction objects",
            "declared-scope global settlement only after descent and obligation discharge",
        ),
        missing_semantic_objects=(),
        jugeo_additions=(
            "repair complexity estimates from obstruction structure",
            "equivalence as descended relational evidence",
            "Mayer-Vietoris style incremental recomputation",
            "product-cover specification factoring",
        ),
        evidence_mapping=_mapping(
            ToolKind.JUGEO,
            "glued judgment section or obstruction witness",
            "declared-scope semantic certificate",
            TrustLevel.MECHANICALLY_VERIFIED,
            "May report closure only when local sections glue and residual obligations are discharged on the declared scope.",
            obligations_supported=("global settlement obligations", "comparative obligations", "replay obligations"),
        ),
        theory_alignment="Treat the whole comparison as one semantic machine rather than a list of disconnected products.",
        trust_boundary="JuGeo cannot silently promote weak evidence; its own trust accounting is explicit.",
        authority_boundary="JuGeo owns the local-to-global settlement judgment, not each peer tool's specialized inner logic.",
        supports_local_proof=True,
        supports_global_descent=True,
        uses_cover_objects=True,
        notes=("JuGeo is a coordination framework with semantic commitments, not merely a tool wrapper",),
    )
    return (
        theorem_prover,
        dep_type,
        model_checker,
        solver,
        coding_assistant,
        agentic,
        jugeo,
    )


def _build_default_capabilities() -> tuple[ComparativeCapability, ...]:
    return (
        ComparativeCapability(
            capability_id="proof-provenance-preservation",
            title="Preserve explicit proof obligations and evidence provenance",
            kind=CapabilityKind.TRUST_PRESERVATION,
            description=(
                "JuGeo keeps theorem-prover style obligations and provenance explicit "
                "instead of flattening them into a generic pass/fail bit."
            ),
            theorem_anchor="theory2.tex chapter 1 section 2",
            concrete_runtime_objects=("typed evidence records", "trust ceilings", "provenance-preserving certificates"),
            partially_present_in=(ToolKind.THEOREM_PROVER, ToolKind.DEPENDENT_TYPE_ASSISTANT),
        ),
        ComparativeCapability(
            capability_id="geometric-orchestration",
            title="Geometric orchestration with covers and overlap treaties",
            kind=CapabilityKind.GEOMETRIC_ORCHESTRATION,
            description=(
                "JuGeo exposes covers, overlap treaties, obstruction classes, and replay-local invalidation "
                "to the controller instead of reducing orchestration to queueing tools over files."
            ),
            theorem_anchor="theory2.tex chapter 1 section 2",
            concrete_runtime_objects=("explicit covers and hypercovers", "overlap matrices or treaty graphs", "replay seals"),
            partially_present_in=(ToolKind.AGENTIC_VERIFIER,),
        ),
        ComparativeCapability(
            capability_id="repair-complexity-quantification",
            title="Repair-complexity quantification from obstruction structure",
            kind=CapabilityKind.REPAIR_ANALYSIS,
            description=(
                "On a fixed cover, the rank of the first obstruction space provides a lower bound, "
                "and in exact fragments an exact count, for independent fixes."
            ),
            theorem_anchor="theory2.tex chapter 1 section 2",
            concrete_runtime_objects=("coboundary operators for admitted exact fragments", "obstruction witnesses", "repair frontiers"),
        ),
        ComparativeCapability(
            capability_id="equivalence-by-descent",
            title="Equivalence as descended relational evidence",
            kind=CapabilityKind.EQUIVALENCE,
            description=(
                "Local relational evidence becomes a global equivalence witness exactly when the corresponding "
                "descent obstruction vanishes on the chosen relational cover."
            ),
            theorem_anchor="theory2.tex chapter 1 section 2",
            concrete_runtime_objects=("relational local sections", "gluing reports", "obstruction witnesses"),
            partially_present_in=(ToolKind.MODEL_CHECKER, ToolKind.SMT_SOLVER),
        ),
        ComparativeCapability(
            capability_id="mayer-vietoris-incrementality",
            title="Mayer-Vietoris style incremental recomputation",
            kind=CapabilityKind.INCREMENTALITY,
            description=(
                "Branch-local or module-local recomputation can be combined algebraically rather than with coarse caches, "
                "which makes reopening obligations more honest and more local."
            ),
            theorem_anchor="theory2.tex chapter 1 section 2",
            concrete_runtime_objects=("support regions", "overlap matrices", "replay seals"),
        ),
        ComparativeCapability(
            capability_id="product-cover-specification-checking",
            title="Product-cover specification checking",
            kind=CapabilityKind.SPECIFICATION_CHECKING,
            description=(
                "Large verification conditions can be factored into path-by-conjunct obligations, with overlap transport "
                "available to reuse local discharge where the treaty law permits it."
            ),
            theorem_anchor="theory2.tex chapter 1 section 2",
            concrete_runtime_objects=("product covers", "path-by-conjunct obligations", "transport-aware overlap laws"),
            partially_present_in=(ToolKind.SMT_SOLVER,),
        ),
    )


def build_default_witness() -> JuGeoRelativeTheoremProversWitness:
    profiles = _build_default_profiles()
    capabilities = _build_default_capabilities()
    witness_payload = {
        "profiles": [profile.to_dict() for profile in profiles],
        "capabilities": [capability.to_dict() for capability in capabilities],
        "section_title": INTRODUCTION_SOURCE_SECTIONS[1],
    }
    witness_id = _stable_digest(witness_payload, S02_SPEC_PROVENANCE)
    return JuGeoRelativeTheoremProversWitness(
        witness_id=witness_id,
        section_title=INTRODUCTION_SOURCE_SECTIONS[1],
        semantic_positioning_lines=SECTION_WORLDVIEW_LINES,
        advantages=SECTION_ADVANTAGES,
        runtime_objects=SECTION_RUNTIME_OBJECTS,
        profiles=profiles,
        capability_catalog=capabilities,
        worldview_commitments=WORLDVIEW_COMMITMENTS,
        authority_boundary=(
            "This section may compare tool families and analyze local-to-global scenarios, "
            "but it may not collapse theorem provers, coding assistants, and orchestration logs into one trust regime."
        ),
        provenance={
            **dict(S02_SPEC_PROVENANCE),
            "worldview_record_id": JUGEO_WORLDVIEW.record_id,
            "worldview_digest": _stable_digest(JUGEO_WORLDVIEW.to_dict()),
        },
    )


DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS: Final[JuGeoRelativeTheoremProversWitness] = build_default_witness()
DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_COORDINATOR: Final[JuGeoRelativeTheoremProversCoordinator] = JuGeoRelativeTheoremProversCoordinator(
    witness=DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS,
    worldview=JUGEO_WORLDVIEW,
)
COMPARATIVE_POSITIONING: Final[ComparativePositioning] = DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_COORDINATOR.compare_tooling_landscape(
    (
        ToolKind.THEOREM_PROVER,
        ToolKind.DEPENDENT_TYPE_ASSISTANT,
        ToolKind.MODEL_CHECKER,
        ToolKind.SMT_SOLVER,
        ToolKind.CODING_ASSISTANT,
        ToolKind.AGENTIC_VERIFIER,
        ToolKind.JUGEO,
    )
)
