"""Semantic-center doctrine for chapter 1 of the JuGeo thesis.

This module is the machine-readable companion to
``preliminaries/theory2.tex`` section 1, "Judgment geometry as the semantic
center", together with the compiled witness ``preliminaries/theory2.pdf`` and
the structural hints recorded in ``theory2-src-blueprint.json`` and
``theory2-generation-order.json``.

Design goals
------------
* Keep the semantic-center worldview explicit and inspectable.
* Offer a production-friendly API that is easy for humans and LLMs to read.
* Preserve provenance, trust, and authority boundaries instead of hiding them
  behind implicit runtime state.
* Remain compatible with the already-generated package surface, especially the
  ``SemanticCenter`` / ``SheafTheoreticalBasis`` names still re-exported from
  ``jugeo.thesis.semantic_center.__init__``.
* Honor the blueprint's canonical chapter-1 classes:
  ``JudgmentGeometrySemanticCenterCoordinator``,
  ``JudgmentGeometrySemanticCenterAnalyzer``, and
  ``JudgmentGeometrySemanticCenterWitness``.

The module intentionally does not pretend to be JuGeo's whole descent engine.
Instead it provides a typed, seam-friendly chapter surface that can evaluate a
family of local patch observations against the semantic-center doctrine without
requiring the entire future dependency graph to exist already:

* Are the local judgment patches mutually compatible on overlaps?
* Are there residual obligations that block publication?
* Does the current trust floor justify settlement?
* If not, what repair frontier is honest to report now?
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from dataclasses import dataclass, field
from itertools import combinations
from types import MappingProxyType
from typing import Any, Final, Mapping, Sequence

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
    "SEMANTIC_CENTER_SPEC_PROVENANCE",
    "SEMANTIC_CENTER_WORLDVIEW_LINES",
    "SEMANTIC_CENTER_RUNTIME_OBJECTS",
    "SEMANTIC_CENTER_OPERATION_FAMILIES",
    "CoordinateAxis",
    "OpenCoverElement",
    "RestrictionMap",
    "GluingCondition",
    "SemanticPatchObservation",
    "SemanticOverlapDiscrepancy",
    "JudgmentGeometrySemanticCenterWitness",
    "SemanticProductSpace",
    "JudgmentGeometryFoundation",
    "SheafTheoreticalBasis",
    "JudgmentGeometrySemanticCenterAnalyzer",
    "JudgmentGeometrySemanticCenterCoordinator",
    "CoordinatedVerification",
    "SemanticCenter",
    "DEFAULT_COORDINATE_AXES",
    "DEFAULT_OPEN_COVER",
    "DEFAULT_RESTRICTION_MAPS",
    "DEFAULT_GLUING_CONDITIONS",
    "DEFAULT_SEMANTIC_CENTER_COORDINATOR",
]

SEMANTIC_CENTER_SPEC_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        "semantic_source": str(MANIFEST_SPEC_PROVENANCE["semantic_source"]),
        "semantic_source_role": str(MANIFEST_SPEC_PROVENANCE["semantic_source_role"]),
        "semantic_source_pdf": str(MANIFEST_SPEC_PROVENANCE["semantic_source_pdf"]),
        "semantic_pdf_role": str(MANIFEST_SPEC_PROVENANCE["semantic_pdf_role"]),
        "structural_blueprint": str(MANIFEST_SPEC_PROVENANCE["structural_blueprint"]),
        "structural_generation_order": str(MANIFEST_SPEC_PROVENANCE["structural_generation_order"]),
        "structural_hint_role": str(MANIFEST_SPEC_PROVENANCE["structural_hint_role"]),
        "target_file": "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
        "target_test": "tests/jugeo/thesis/semantic_center/test_judgment_geometry_as_the_semantic.py",
        "stage": "chapter-01",
        "sequence": 61,
        "chapter_number": CHAPTER_NUMBER,
        "part_number": PART_NUMBER,
        "chapter_title": CHAPTER_TITLE,
        "section_title": INTRODUCTION_SOURCE_SECTIONS[0],
    }
)

SEMANTIC_CENTER_WORLDVIEW_LINES: Final[tuple[str, ...]] = (
    "JuGeo is a single semantic machine whose primary object is the judgment state of a project.",
    "The project is modeled over a site of semantic coordinates rather than a flat file index.",
    "Local artifacts are treated as sections of presheaves over semantic coordinates.",
    "Globally coherent artifacts are descended or sheafified sections rather than merely plausible text.",
    "Persistent failures are obstruction classes attached to a chosen cover or hypercover.",
    "Verification, generation, repair, equivalence, and ideation are operations on the same semantic object.",
    "Controlled AI proposal may help search the space, but it may not settle global closure by itself.",
)

SEMANTIC_CENTER_RUNTIME_OBJECTS: Final[tuple[str, ...]] = (
    "site of semantic coordinates",
    "context-sensitive judgment state",
    "local sections",
    "overlap treaties",
    "residual obligations",
    "obstruction records",
    "support regions",
    "repair frontier",
)

SEMANTIC_CENTER_OPERATION_FAMILIES: Final[tuple[str, ...]] = (
    "verification",
    "generation",
    "repair",
    "equivalence",
    "orchestration",
    "ideation",
)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _normalize_text_tuple(values: Sequence[str] | str | None, *, field_name: str) -> tuple[str, ...]:
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


def _normalize_coordinate(value: Coordinate | Sequence[str] | str, *, field_name: str) -> Coordinate:
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
        return Coordinate(components=parts, kind=CoordinateKind.REGION)
    parts = tuple(_normalize_required_text(str(part), field_name=field_name) for part in value)
    return Coordinate(components=parts, kind=CoordinateKind.REGION)


def _normalize_mapping(value: Mapping[str, Any] | None, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return MappingProxyType({str(key): value[key] for key in value})


def _normalize_claim_text(value: str) -> str:
    return " ".join(_normalize_required_text(value, field_name="claim").lower().split())


def _stable_json(value: Any) -> str:
    def _convert(item: Any) -> Any:
        if isinstance(item, Coordinate):
            return item.serialize()
        if isinstance(item, TrustLevel):
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


def _unique_text(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _trust_meets_floor(level: TrustLevel, floor: TrustLevel) -> bool:
    return level >= floor


def _failure_for_invalid_input(message: str, *, coordinate: str, metadata: Mapping[str, Any] | None = None) -> StructuredFailure:
    return StructuredFailure(
        message=message,
        scope=FailureScope.CHAPTER,
        classification=FailureClassification.INVALID_VALUE,
        coordinate=coordinate,
        trust_boundary="semantic-center-input",
        metadata=dict(metadata or {}),
        recoverable=True,
    )


@dataclass(frozen=True, slots=True)
class CoordinateAxis:
    """One axis in JuGeo's chapter-1 semantic product space."""

    name: str
    judgment_component: str
    description: str
    value_shape: str
    semantic_role: str
    provenance: tuple[str, ...] = ()
    authority_boundary: str = "chapter-1 semantic-center doctrine"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _normalize_required_text(self.name, field_name="name"))
        object.__setattr__(self, "judgment_component", _normalize_required_text(self.judgment_component, field_name="judgment_component"))
        object.__setattr__(self, "description", _normalize_required_text(self.description, field_name="description"))
        object.__setattr__(self, "value_shape", _normalize_required_text(self.value_shape, field_name="value_shape"))
        object.__setattr__(self, "semantic_role", _normalize_required_text(self.semantic_role, field_name="semantic_role"))
        object.__setattr__(self, "authority_boundary", _normalize_required_text(self.authority_boundary, field_name="authority_boundary"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def label(self) -> str:
        return f"{self.name} [{self.judgment_component}] -> {self.value_shape}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "judgment_component": self.judgment_component,
            "description": self.description,
            "value_shape": self.value_shape,
            "semantic_role": self.semantic_role,
            "provenance": list(self.provenance),
            "authority_boundary": self.authority_boundary,
        }


@dataclass(frozen=True, slots=True)
class OpenCoverElement:
    """A named member of the chapter-1 semantic cover."""

    cover_id: str
    semantic_role: str
    coordinate_prefixes: tuple[str, ...]
    evidence_channel: str
    trust_floor: TrustLevel
    support_regions: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cover_id", _normalize_required_text(self.cover_id, field_name="cover_id"))
        object.__setattr__(self, "semantic_role", _normalize_required_text(self.semantic_role, field_name="semantic_role"))
        object.__setattr__(self, "evidence_channel", _normalize_required_text(self.evidence_channel, field_name="evidence_channel"))
        object.__setattr__(self, "coordinate_prefixes", _normalize_text_tuple(self.coordinate_prefixes, field_name="coordinate_prefixes"))
        object.__setattr__(self, "support_regions", _normalize_text_tuple(self.support_regions, field_name="support_regions"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def matches_coordinate(self, coordinate: Coordinate | str) -> bool:
        coordinate_obj = _normalize_coordinate(coordinate, field_name="coordinate")
        name = coordinate_obj.name
        key = coordinate_obj.key
        return any(name.startswith(prefix) or key.startswith(prefix) for prefix in self.coordinate_prefixes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover_id": self.cover_id,
            "semantic_role": self.semantic_role,
            "coordinate_prefixes": list(self.coordinate_prefixes),
            "evidence_channel": self.evidence_channel,
            "trust_floor": self.trust_floor.value,
            "support_regions": list(self.support_regions),
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class RestrictionMap:
    """A visible restriction map between semantic neighborhoods."""

    source_cover_id: str
    target_cover_id: str
    description: str
    trust_attenuation_steps: int = 0
    preserved_fields: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_cover_id", _normalize_required_text(self.source_cover_id, field_name="source_cover_id"))
        object.__setattr__(self, "target_cover_id", _normalize_required_text(self.target_cover_id, field_name="target_cover_id"))
        object.__setattr__(self, "description", _normalize_required_text(self.description, field_name="description"))
        object.__setattr__(self, "trust_attenuation_steps", max(0, int(self.trust_attenuation_steps)))
        object.__setattr__(self, "preserved_fields", _normalize_text_tuple(self.preserved_fields, field_name="preserved_fields"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def attenuate(self, trust_level: TrustLevel) -> TrustLevel:
        return TrustAlgebra().attenuate(trust_level, self.trust_attenuation_steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_cover_id": self.source_cover_id,
            "target_cover_id": self.target_cover_id,
            "description": self.description,
            "trust_attenuation_steps": self.trust_attenuation_steps,
            "preserved_fields": list(self.preserved_fields),
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class GluingCondition:
    """A chapter-local overlap law used when comparing patch observations."""

    condition_id: str
    description: str
    payload_field: str
    match_mode: str = "exact"
    failure_classification: FailureClassification = FailureClassification.DESCENT_OBSTRUCTION
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "condition_id", _normalize_required_text(self.condition_id, field_name="condition_id"))
        object.__setattr__(self, "description", _normalize_required_text(self.description, field_name="description"))
        object.__setattr__(self, "payload_field", _normalize_required_text(self.payload_field, field_name="payload_field"))
        object.__setattr__(self, "match_mode", _normalize_required_text(self.match_mode, field_name="match_mode"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def compare_payloads(self, left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any] | None:
        left_value = left.get(self.payload_field)
        right_value = right.get(self.payload_field)
        if self.match_mode == "exact":
            if left_value == right_value:
                return None
        elif self.match_mode == "set-equality":
            if set(left_value or ()) == set(right_value or ()):
                return None
        elif self.match_mode == "set-overlap":
            if not left_value and not right_value:
                return None
            if set(left_value or ()) & set(right_value or ()):
                return None
        else:
            raise ValueError(f"Unknown match_mode: {self.match_mode!r}")
        return {
            "condition_id": self.condition_id,
            "field": self.payload_field,
            "match_mode": self.match_mode,
            "left": left_value,
            "right": right_value,
            "classification": self.failure_classification.value,
            "description": self.description,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "description": self.description,
            "payload_field": self.payload_field,
            "match_mode": self.match_mode,
            "failure_classification": self.failure_classification.value,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class SemanticPatchObservation:
    """One local judgment patch used by the semantic-center analyzer."""

    patch_id: str
    coordinate: Coordinate | Sequence[str] | str
    target_coordinate: Coordinate | Sequence[str] | str
    claim: str
    clauses: tuple[str, ...] = ()
    evidence_keys: tuple[str, ...] = ()
    residual_obligations: tuple[str, ...] = ()
    support_regions: tuple[str, ...] = ()
    treaty_tags: tuple[str, ...] = ()
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    provenance: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "patch_id", _normalize_required_text(self.patch_id, field_name="patch_id"))
        object.__setattr__(self, "coordinate", _normalize_coordinate(self.coordinate, field_name="coordinate"))
        object.__setattr__(self, "target_coordinate", _normalize_coordinate(self.target_coordinate, field_name="target_coordinate"))
        object.__setattr__(self, "claim", _normalize_required_text(self.claim, field_name="claim"))
        object.__setattr__(self, "clauses", _normalize_text_tuple(self.clauses, field_name="clauses"))
        object.__setattr__(self, "evidence_keys", _normalize_text_tuple(self.evidence_keys, field_name="evidence_keys"))
        object.__setattr__(self, "residual_obligations", _normalize_text_tuple(self.residual_obligations, field_name="residual_obligations"))
        object.__setattr__(self, "support_regions", _normalize_text_tuple(self.support_regions, field_name="support_regions"))
        object.__setattr__(self, "treaty_tags", _normalize_text_tuple(self.treaty_tags, field_name="treaty_tags"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        object.__setattr__(self, "metadata", _normalize_mapping(self.metadata, field_name="metadata"))

    @property
    def normalized_claim(self) -> str:
        return _normalize_claim_text(self.claim)

    def comparison_payload(self) -> dict[str, Any]:
        return {
            "normalized_claim": self.normalized_claim,
            "clauses": tuple(sorted(self.clauses)),
            "support_regions": tuple(sorted(self.support_regions)),
            "treaty_tags": tuple(sorted(self.treaty_tags)),
            "target_coordinate": self.target_coordinate.key,
        }

    def to_local_section(self) -> LocalSection:
        return LocalSection(
            coordinate=self.patch_id,
            judgment_data=self.comparison_payload(),
            evidence_bundle=self.evidence_keys,
            trust_level=float(self.trust_level.rank_index()) / max(1, len(TrustLevel.ordered()) - 1),
            provenance=self.provenance,
            is_partial=bool(self.residual_obligations),
            residual_obligations=list(self.residual_obligations),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "coordinate": self.coordinate.serialize(),
            "target_coordinate": self.target_coordinate.serialize(),
            "claim": self.claim,
            "normalized_claim": self.normalized_claim,
            "clauses": list(self.clauses),
            "evidence_keys": list(self.evidence_keys),
            "residual_obligations": list(self.residual_obligations),
            "support_regions": list(self.support_regions),
            "treaty_tags": list(self.treaty_tags),
            "trust_level": self.trust_level.value,
            "provenance": list(self.provenance),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SemanticOverlapDiscrepancy:
    """A concrete overlap-level disagreement between two local patches."""

    left_patch_id: str
    right_patch_id: str
    overlap_coordinate: str
    disagreements: Mapping[str, Any]
    failure_classification: FailureClassification = FailureClassification.DESCENT_OBSTRUCTION
    repair_hints: tuple[str, ...] = ()
    persistent: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "left_patch_id", _normalize_required_text(self.left_patch_id, field_name="left_patch_id"))
        object.__setattr__(self, "right_patch_id", _normalize_required_text(self.right_patch_id, field_name="right_patch_id"))
        object.__setattr__(self, "overlap_coordinate", _normalize_required_text(self.overlap_coordinate, field_name="overlap_coordinate"))
        object.__setattr__(self, "disagreements", _normalize_mapping(self.disagreements, field_name="disagreements"))
        object.__setattr__(self, "repair_hints", _normalize_text_tuple(self.repair_hints, field_name="repair_hints"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_patch_id": self.left_patch_id,
            "right_patch_id": self.right_patch_id,
            "overlap_coordinate": self.overlap_coordinate,
            "disagreements": dict(self.disagreements),
            "failure_classification": self.failure_classification.value,
            "repair_hints": list(self.repair_hints),
            "persistent": self.persistent,
        }


@dataclass(frozen=True, slots=True)
class JudgmentGeometrySemanticCenterWitness:
    """Structured witness for a chapter-1 semantic-center analysis."""

    witness_id: str
    target_coordinate: Coordinate
    objective: str
    worldview_record_id: str
    patch_observations: tuple[SemanticPatchObservation, ...]
    discrepancies: tuple[SemanticOverlapDiscrepancy, ...]
    residual_obligations: tuple[str, ...]
    repair_frontier: tuple[str, ...]
    trust_level: TrustLevel
    settlement_floor: TrustLevel
    publishable: bool
    semantic_state: Mapping[str, Any]
    provenance: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "witness_id", _normalize_required_text(self.witness_id, field_name="witness_id"))
        object.__setattr__(self, "objective", _normalize_required_text(self.objective, field_name="objective"))
        object.__setattr__(self, "worldview_record_id", _normalize_required_text(self.worldview_record_id, field_name="worldview_record_id"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))
        object.__setattr__(self, "residual_obligations", _normalize_text_tuple(self.residual_obligations, field_name="residual_obligations"))
        object.__setattr__(self, "repair_frontier", _normalize_text_tuple(self.repair_frontier, field_name="repair_frontier"))
        object.__setattr__(self, "semantic_state", _normalize_mapping(self.semantic_state, field_name="semantic_state"))

    @property
    def patch_count(self) -> int:
        return len(self.patch_observations)

    @property
    def is_globally_coherent(self) -> bool:
        return not self.discrepancies and not self.residual_obligations

    @property
    def canonical_digest(self) -> str:
        if "canonical_digest" in self.semantic_state:
            return str(self.semantic_state["canonical_digest"])
        return _stable_digest(
            self.witness_id,
            self.target_coordinate.serialize(),
            self.objective,
            self.worldview_record_id,
            [patch.to_dict() for patch in self.patch_observations],
            [discrepancy.to_dict() for discrepancy in self.discrepancies],
            self.residual_obligations,
            self.repair_frontier,
            self.trust_level.value,
            self.settlement_floor.value,
            self.publishable,
            dict(self.semantic_state),
            self.provenance,
            self.notes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "target_coordinate": self.target_coordinate.serialize(),
            "objective": self.objective,
            "worldview_record_id": self.worldview_record_id,
            "patch_count": self.patch_count,
            "patch_observations": [patch.to_dict() for patch in self.patch_observations],
            "discrepancies": [discrepancy.to_dict() for discrepancy in self.discrepancies],
            "residual_obligations": list(self.residual_obligations),
            "repair_frontier": list(self.repair_frontier),
            "trust_level": self.trust_level.value,
            "settlement_floor": self.settlement_floor.value,
            "publishable": self.publishable,
            "semantic_state": dict(self.semantic_state),
            "provenance": list(self.provenance),
            "notes": list(self.notes),
            "canonical_digest": self.canonical_digest if "canonical_digest" not in self.semantic_state else self.semantic_state["canonical_digest"],
        }

    def summary_lines(self) -> list[str]:
        lines = [
            f"Witness {self.witness_id} for {self.target_coordinate.name}",
            f"  objective: {self.objective}",
            f"  patch_count: {self.patch_count}",
            f"  trust: {self.trust_level.value} (floor={self.settlement_floor.value})",
            f"  publishable: {self.publishable}",
            f"  discrepancies: {len(self.discrepancies)}",
            f"  residual obligations: {len(self.residual_obligations)}",
        ]
        if self.repair_frontier:
            lines.append("  repair frontier:")
            lines.extend(f"    - {item}" for item in self.repair_frontier)
        return lines


@dataclass(frozen=True, slots=True)
class SemanticProductSpace:
    """Readable chapter-1 view of the semantic product space."""

    axes: tuple[CoordinateAxis, ...]
    semantic_objects: tuple[str, ...] = SEMANTIC_CENTER_RUNTIME_OBJECTS
    provenance: tuple[str, ...] = (
        "preliminaries/theory2.tex#section-1.1",
        "preliminaries/theory2.pdf#chapter-1",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        if not self.axes:
            raise ValueError("axes must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": [axis.to_dict() for axis in self.axes],
            "semantic_objects": list(self.semantic_objects),
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class JudgmentGeometryFoundation:
    """Foundational doctrine for chapter-1 semantic-center analysis."""

    worldview: JuGeoWorldview = JUGEO_WORLDVIEW
    central_claim: str = "JuGeo should be described as judgment geometry with local-to-global semantics."
    operation_families: tuple[str, ...] = SEMANTIC_CENTER_OPERATION_FAMILIES
    runtime_objects: tuple[str, ...] = SEMANTIC_CENTER_RUNTIME_OBJECTS
    theorem_targets: tuple[str, ...] = (
        "global-section criterion for publishable artifacts",
        "no silent trust promotion",
        "support-aware repair frontier honesty",
    )
    authority_boundary: str = (
        "This chapter may analyze judgment geometry and local-to-global closure, "
        "but it does not certify all downstream theorem obligations by itself."
    )
    provenance: tuple[str, ...] = (
        "preliminaries/theory2.tex#section-1.1",
        "preliminaries/theory2.pdf#chapter-1",
        "theory2-src-blueprint.json#semantic_center/s01",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "central_claim", _normalize_required_text(self.central_claim, field_name="central_claim"))
        object.__setattr__(self, "operation_families", _normalize_text_tuple(self.operation_families, field_name="operation_families"))
        object.__setattr__(self, "runtime_objects", _normalize_text_tuple(self.runtime_objects, field_name="runtime_objects"))
        object.__setattr__(self, "theorem_targets", _normalize_text_tuple(self.theorem_targets, field_name="theorem_targets"))
        object.__setattr__(self, "authority_boundary", _normalize_required_text(self.authority_boundary, field_name="authority_boundary"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "worldview_record_id": self.worldview.record_id,
            "central_claim": self.central_claim,
            "operation_families": list(self.operation_families),
            "runtime_objects": list(self.runtime_objects),
            "theorem_targets": list(self.theorem_targets),
            "authority_boundary": self.authority_boundary,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class SheafTheoreticalBasis:
    """Explicit cover/restriction/gluing surface for the semantic center."""

    cover: tuple[OpenCoverElement, ...]
    restriction_maps: tuple[RestrictionMap, ...]
    gluing_conditions: tuple[GluingCondition, ...]
    provenance: tuple[str, ...] = (
        "preliminaries/theory2.tex#section-1.1",
        "jugeo.geometry.descent.GluingData",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        if not self.cover:
            raise ValueError("cover must be non-empty")
        if not self.gluing_conditions:
            raise ValueError("gluing_conditions must be non-empty")

    def compare_patches(self, left: SemanticPatchObservation, right: SemanticPatchObservation) -> dict[str, Any]:
        left_payload = left.comparison_payload()
        right_payload = right.comparison_payload()
        disagreements: dict[str, Any] = {}
        for condition in self.gluing_conditions:
            discrepancy = condition.compare_payloads(left_payload, right_payload)
            if discrepancy is not None:
                disagreements[condition.payload_field] = discrepancy
        return disagreements

    def build_gluing_data(self, observations: Sequence[SemanticPatchObservation]) -> GluingData:
        gluing = GluingData()
        observation_map = {observation.patch_id: observation for observation in observations}
        for observation in observations:
            gluing.add_section(observation.to_local_section())
        for left, right in combinations(observations, 2):
            def predicate(
                left_data: Mapping[str, Any],
                right_data: Mapping[str, Any],
                *,
                left_id: str = left.patch_id,
                right_id: str = right.patch_id,
            ) -> bool:
                return not self.compare_patches(observation_map[left_id], observation_map[right_id])

            gluing.add_overlap(
                OverlapCondition(
                    left_coordinate=left.patch_id,
                    right_coordinate=right.patch_id,
                    overlap_coordinate=f"{left.coordinate.key}∩{right.coordinate.key}",
                    compatibility_predicate=predicate,
                )
            )
        return gluing

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover": [entry.to_dict() for entry in self.cover],
            "restriction_maps": [entry.to_dict() for entry in self.restriction_maps],
            "gluing_conditions": [entry.to_dict() for entry in self.gluing_conditions],
            "provenance": list(self.provenance),
        }


class JudgmentGeometrySemanticCenterAnalyzer:
    """Analyze whether local patch observations realize the semantic-center doctrine."""

    def __init__(
        self,
        product_space: SemanticProductSpace,
        foundation: JudgmentGeometryFoundation,
        sheaf_basis: SheafTheoreticalBasis,
        *,
        settlement_floor: TrustLevel = TrustLevel.HUMAN_ATTESTED,
    ) -> None:
        self.product_space = product_space
        self.foundation = foundation
        self.sheaf_basis = sheaf_basis
        self.settlement_floor = settlement_floor

    def analyze(
        self,
        target_coordinate: Coordinate | Sequence[str] | str,
        observations: Sequence[SemanticPatchObservation],
        *,
        objective: str = "publishable-artifact",
    ) -> JudgmentGeometrySemanticCenterWitness:
        target = _normalize_coordinate(target_coordinate, field_name="target_coordinate")
        if not observations:
            raise_with_scope(
                "At least one semantic patch observation is required",
                scope=FailureScope.CHAPTER,
                classification=FailureClassification.INVALID_VALUE,
                coordinate=target.key,
                metadata={"failure": _failure_for_invalid_input("missing observations", coordinate=target.key).to_dict()},
            )
        normalized_observations = tuple(observations)
        aggregate_trust = _aggregate_trust([observation.trust_level for observation in normalized_observations])
        gluing = self.sheaf_basis.build_gluing_data(normalized_observations)
        overlaps = gluing.verify_all_overlaps()
        discrepancies = self._build_discrepancies(normalized_observations)
        residual_obligations = _unique_text(
            [obligation for observation in normalized_observations for obligation in observation.residual_obligations]
        )
        repair_frontier = self._build_repair_frontier(discrepancies, residual_obligations, aggregate_trust)
        semantic_state = {
            "target_coordinate": target.key,
            "coordinates": [observation.coordinate.key for observation in normalized_observations],
            "claims": [observation.normalized_claim for observation in normalized_observations],
            "clauses": sorted({clause for observation in normalized_observations for clause in observation.clauses}),
            "evidence_keys": sorted({key for observation in normalized_observations for key in observation.evidence_keys}),
            "residual_obligations": list(residual_obligations),
            "supports": sorted({support for observation in normalized_observations for support in observation.support_regions}),
            "treaties": sorted({tag for observation in normalized_observations for tag in observation.treaty_tags}),
            "overlap_statuses": [
                {
                    "pair": [overlap.left_coordinate, overlap.right_coordinate],
                    "status": overlap.status.value,
                    "overlap_coordinate": overlap.overlap_coordinate,
                }
                for overlap in overlaps
            ],
            "obstructions": [entry.to_dict() for entry in discrepancies],
            "repair_frontier": list(repair_frontier),
            "trust_level": aggregate_trust.value,
        }
        semantic_state["canonical_digest"] = _stable_digest(semantic_state)
        publishable = not discrepancies and not residual_obligations and _trust_meets_floor(aggregate_trust, self.settlement_floor)
        witness_id = f"semantic-center-{target.key or 'root'}-{_stable_digest(objective, semantic_state)}"
        notes: list[str] = []
        if publishable:
            notes.append("Local judgments glue and the current trust floor supports publication.")
        if discrepancies:
            notes.append("Persistent overlap disagreements remain, so the honest output is an obstruction-aware witness.")
        if residual_obligations:
            notes.append("Residual obligations remain open, so the artifact is not yet fit to be reported as settled.")
        if not _trust_meets_floor(aggregate_trust, self.settlement_floor):
            notes.append("Trust remains below the settlement floor; controlled proposal may guide search but not settle closure.")
        return JudgmentGeometrySemanticCenterWitness(
            witness_id=witness_id,
            target_coordinate=target,
            objective=objective,
            worldview_record_id=self.foundation.worldview.record_id,
            patch_observations=normalized_observations,
            discrepancies=tuple(discrepancies),
            residual_obligations=residual_obligations,
            repair_frontier=repair_frontier,
            trust_level=aggregate_trust,
            settlement_floor=self.settlement_floor,
            publishable=publishable,
            semantic_state=MappingProxyType(semantic_state),
            provenance=(
                SEMANTIC_CENTER_SPEC_PROVENANCE["semantic_source"],
                SEMANTIC_CENTER_SPEC_PROVENANCE["semantic_source_pdf"],
                SEMANTIC_CENTER_SPEC_PROVENANCE["structural_blueprint"],
                "jugeo.geometry.descent.GluingData",
                "jugeo.evidence.trust.TrustAlgebra",
            ),
            notes=tuple(notes),
        )

    def _build_discrepancies(self, observations: Sequence[SemanticPatchObservation]) -> list[SemanticOverlapDiscrepancy]:
        discrepancies: list[SemanticOverlapDiscrepancy] = []
        for left, right in combinations(observations, 2):
            mismatch = self.sheaf_basis.compare_patches(left, right)
            if not mismatch:
                continue
            repair_hints: list[str] = []
            if "normalized_claim" in mismatch or "clauses" in mismatch:
                repair_hints.append("local repair within the present cover")
            if "support_regions" in mismatch:
                repair_hints.append("change of cover or hypercover")
            if "treaty_tags" in mismatch:
                repair_hints.append("strengthen the overlap treaty")
            discrepancies.append(
                SemanticOverlapDiscrepancy(
                    left_patch_id=left.patch_id,
                    right_patch_id=right.patch_id,
                    overlap_coordinate=f"{left.coordinate.key}∩{right.coordinate.key}",
                    disagreements=mismatch,
                    repair_hints=tuple(repair_hints),
                )
            )
        return discrepancies

    def _build_repair_frontier(
        self,
        discrepancies: Sequence[SemanticOverlapDiscrepancy],
        residual_obligations: Sequence[str],
        trust_level: TrustLevel,
    ) -> tuple[str, ...]:
        frontier: list[str] = []
        for discrepancy in discrepancies:
            frontier.extend(discrepancy.repair_hints)
        if residual_obligations:
            frontier.append("discharge residual obligations with admissible evidence")
        if not _trust_meets_floor(trust_level, self.settlement_floor):
            frontier.append("raise trust above the settlement floor with explicit non-oracle evidence")
        return _unique_text(frontier)


class JudgmentGeometrySemanticCenterCoordinator:
    """Top-level chapter-1 coordinator for semantic-center analysis."""

    def __init__(
        self,
        foundation: JudgmentGeometryFoundation,
        product_space: SemanticProductSpace,
        sheaf_basis: SheafTheoreticalBasis,
        analyzer: JudgmentGeometrySemanticCenterAnalyzer | None = None,
    ) -> None:
        self.foundation = foundation
        self.product_space = product_space
        self.sheaf_basis = sheaf_basis
        self.analyzer = analyzer or JudgmentGeometrySemanticCenterAnalyzer(product_space, foundation, sheaf_basis)

    @classmethod
    def build_default(cls) -> "JudgmentGeometrySemanticCenterCoordinator":
        product_space = SemanticProductSpace(axes=DEFAULT_COORDINATE_AXES)
        foundation = JudgmentGeometryFoundation()
        sheaf_basis = SheafTheoreticalBasis(DEFAULT_OPEN_COVER, DEFAULT_RESTRICTION_MAPS, DEFAULT_GLUING_CONDITIONS)
        return cls(foundation, product_space, sheaf_basis)

    def coordinate(
        self,
        target_coordinate: Coordinate | Sequence[str] | str,
        observations: Sequence[SemanticPatchObservation],
        *,
        objective: str = "publishable-artifact",
    ) -> JudgmentGeometrySemanticCenterWitness:
        return self.analyzer.analyze(target_coordinate, observations, objective=objective)

    def runtime_contract(self) -> dict[str, Any]:
        return {
            "foundation": self.foundation.to_dict(),
            "product_space": self.product_space.to_dict(),
            "sheaf_basis": self.sheaf_basis.to_dict(),
        }

    def render_report(self, witness: JudgmentGeometrySemanticCenterWitness) -> str:
        return "\n".join(witness.summary_lines())


class CoordinatedVerification:
    """Compatibility wrapper preserving the legacy package surface."""

    def __init__(self, coordinator: JudgmentGeometrySemanticCenterCoordinator | None = None) -> None:
        self.coordinator = coordinator or JudgmentGeometrySemanticCenterCoordinator.build_default()

    def verify(
        self,
        target_coordinate: Coordinate | Sequence[str] | str,
        observations: Sequence[SemanticPatchObservation],
        *,
        objective: str = "publishable-artifact",
    ) -> JudgmentGeometrySemanticCenterWitness:
        return self.coordinator.coordinate(target_coordinate, observations, objective=objective)


class SemanticCenter:
    """Compatibility-friendly top-level semantic-center object.

    The package ``__init__`` still expects a ``SemanticCenter`` class. This
    wrapper exposes the same conceptual pieces now produced by the blueprint-led
    coordinator/analyzer design: a foundation, product space, sheaf basis, and
    coordinated verification surface.
    """

    def __init__(self, coordinator: JudgmentGeometrySemanticCenterCoordinator | None = None) -> None:
        self.coordinator = coordinator or JudgmentGeometrySemanticCenterCoordinator.build_default()
        self.foundation = self.coordinator.foundation
        self.product_space = self.coordinator.product_space
        self.sheaf_basis = self.coordinator.sheaf_basis
        self.verification = CoordinatedVerification(self.coordinator)

    def analyze(
        self,
        target_coordinate: Coordinate | Sequence[str] | str,
        observations: Sequence[SemanticPatchObservation],
        *,
        objective: str = "publishable-artifact",
    ) -> JudgmentGeometrySemanticCenterWitness:
        return self.coordinator.coordinate(target_coordinate, observations, objective=objective)

    def summary(self) -> str:
        return textwrap.dedent(
            f"""
            SemanticCenter
              chapter: {CHAPTER_NUMBER} - {CHAPTER_TITLE}
              section: {INTRODUCTION_SOURCE_SECTIONS[0]}
              worldview: {self.foundation.central_claim}
              operations: {", ".join(self.foundation.operation_families)}
            """
        ).strip()


DEFAULT_COORDINATE_AXES: Final[tuple[CoordinateAxis, ...]] = (
    CoordinateAxis("coordinate", "c", "Semantic coordinate identifying the region whose judgment state is under analysis.", "jugeo.geometry.site.Coordinate", "site-indexed location", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("claim", "J_claim", "Normalized local claim inhabiting the chosen coordinate.", "normalized string", "local section content", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("clauses", "J_clause", "Clausewise decomposition used to keep claims localizable.", "tuple[str, ...]", "specification granularity", ("preliminaries/theory2.tex#section-376",)),
    CoordinateAxis("evidence", "E", "Named evidence keys supporting the local judgment.", "tuple[str, ...]", "evidence plurality", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("residuals", "O", "Residual obligations that prevent the local judgment from counting as settled.", "tuple[str, ...]", "open obligations", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("obstructions", "B", "Overlap discrepancies treated as obstruction records rather than disposable error strings.", "tuple[SemanticOverlapDiscrepancy, ...]", "persistent failure geometry", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("supports", "S", "Support regions naming where the local judgment is intended to hold.", "tuple[str, ...]", "support-aware locality", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("treaties", "T", "Overlap treaties or agreement tags that constrain gluing across patches.", "tuple[str, ...]", "overlap agreement structure", ("preliminaries/theory2.tex#section-1.1",)),
    CoordinateAxis("trust", "Pi", "Trust level carried by the evidence, preserving no-silent-promotion semantics.", "jugeo.evidence.trust.TrustLevel", "settlement boundary", ("preliminaries/theory2.tex#section-1.1",)),
)

DEFAULT_OPEN_COVER: Final[tuple[OpenCoverElement, ...]] = (
    OpenCoverElement("U_proof", "mechanically checked neighborhood", ("src", "theorem", "proof"), "proof", TrustLevel.MECHANICALLY_VERIFIED, ("proof", "theorem"), ("preliminaries/theory2.tex#section-1.2",)),
    OpenCoverElement("U_solver", "solver-discharged neighborhood", ("src", "solver", "spec"), "solver", TrustLevel.SOLVER_DISCHARGED, ("solver", "specification"), ("preliminaries/theory2.tex#section-1.2",)),
    OpenCoverElement("U_runtime", "runtime-witnessed neighborhood", ("src", "tests", "runtime"), "runtime", TrustLevel.RUNTIME_WITNESSED, ("tests", "runtime"), ("preliminaries/theory2.tex#section-1.2",)),
    OpenCoverElement("U_oracle", "proposal/search neighborhood", ("src", "agent", "copilot"), "oracle", TrustLevel.ORACLE_PROPOSED, ("proposal", "search"), ("preliminaries/theory2.tex#section-1.1",)),
)

DEFAULT_RESTRICTION_MAPS: Final[tuple[RestrictionMap, ...]] = (
    RestrictionMap("U_proof", "U_solver", "Forget proof terms but preserve clausewise obligations and solver-relevant consequences.", 1, ("claim", "clauses", "residuals"), ("preliminaries/theory2.tex#section-1.2",)),
    RestrictionMap("U_solver", "U_runtime", "Project solver-backed knowledge to runtime witnesses without silently strengthening trust.", 1, ("clauses", "supports"), ("preliminaries/theory2.tex#section-1.2",)),
    RestrictionMap("U_runtime", "U_oracle", "Expose only search-relevant residuals and support hints to proposal channels.", 1, ("residuals", "supports", "treaties"), ("preliminaries/theory2.tex#section-1.1",)),
)

DEFAULT_GLUING_CONDITIONS: Final[tuple[GluingCondition, ...]] = (
    GluingCondition("claim-agreement", "Local patches must agree on the normalized claim they are attempting to realize.", "normalized_claim", "exact", provenance=("preliminaries/theory2.tex#section-1.1",)),
    GluingCondition("clause-agreement", "Clause bundles must match so the semantic center remains clause-registry-first rather than slogan-first.", "clauses", "set-equality", provenance=("preliminaries/theory2.tex#section-376",)),
    GluingCondition("support-overlap", "Support regions should overlap so patch claims genuinely address the same neighborhood.", "support_regions", "set-overlap", provenance=("preliminaries/theory2.tex#section-1.1",)),
    GluingCondition("treaty-agreement", "Overlap treaties should agree before a global section is reported as settled.", "treaty_tags", "set-equality", provenance=("preliminaries/theory2.tex#section-1.1",)),
)

DEFAULT_SEMANTIC_CENTER_COORDINATOR: Final[JudgmentGeometrySemanticCenterCoordinator] = JudgmentGeometrySemanticCenterCoordinator.build_default()
