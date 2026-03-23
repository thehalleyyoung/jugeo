"""The AG+DTT+AI thesis as an explicit chapter-1 runtime surface.

This module is the machine-readable companion to
``preliminaries/theory2.tex`` section 3 of chapter 1,
"The AG+DTT+AI thesis", together with the compiled witness
``preliminaries/theory2.pdf`` and the structural hints recorded in
``theory2-src-blueprint.json`` and ``theory2-generation-order.json``.

Design commitments
------------------
* Keep the AG, DTT, and AI layers explicit as distinct authority-bearing
  components rather than flattening them into marketing prose.
* Preserve provenance, trust, and semantic boundaries in typed records.
* Reuse real JuGeo primitives such as ``TrustAlgebra`` and ``GluingData`` so
  the chapter module has honest seams to the future runtime.
* Honor both the blueprint's chapter-facing names
  ``TheAGDTTAICoordinator``, ``TheAGDTTAIAnalyzer``, and
  ``TheAGDTTAIWitness`` and the already-generated package re-export contract
  expecting ``AlgebraicGeometryComponent``, ``DependentTypeComponent``,
  ``AIComponent``, ``ThesisUnification``, ``AGDTTAIThesis``,
  ``ComponentInteraction``, and ``THE_AG_DTT_AI_THESIS``.
* Stay honest about scope: the AI layer may propose, search, and route, but it
  does not silently acquire settlement authority.

Theory-facing summary
---------------------
The governing section states the thesis in a strict sense. Algebraic geometry
contributes the base site, covers, hypercovers, presheaves, sheaves, descent
data, gluing laws, Cech cocycles, and obstruction classes. Dependent type
theory contributes context-sensitive judgments, evidence-bearing claims,
witness terms, and residual obligations. AI contributes controlled proposal,
analogical transfer, and frontier search over future semantic states. Remove
any one of these three and the architecture no longer explains project-scale
generation and verification.
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from dataclasses import dataclass, field
from enum import Enum
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
    "S03_SPEC_PROVENANCE",
    "THESIS_WORLDVIEW_LINES",
    "THESIS_RUNTIME_OBJECTS",
    "THESIS_AUTHORITY_CENTERS",
    "THESIS_COMPONENT_ORDER",
    "ThesisComponentKind",
    "AGDTTAIObservation",
    "AGDTTAIDiscrepancy",
    "ComponentInteraction",
    "AlgebraicGeometryComponent",
    "DependentTypeComponent",
    "AIComponent",
    "ThesisUnification",
    "AGDTTAIThesis",
    "TheAGDTTAIWitness",
    "TheAGDTTAIAnalyzer",
    "TheAGDTTAICoordinator",
    "DEFAULT_COMPONENT_INTERACTIONS",
    "DEFAULT_THE_AG_DTT_AI_COORDINATOR",
    "THE_AG_DTT_AI_THESIS",
]

S03_SPEC_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        "semantic_source": str(MANIFEST_SPEC_PROVENANCE["semantic_source"]),
        "semantic_source_role": str(MANIFEST_SPEC_PROVENANCE["semantic_source_role"]),
        "semantic_source_pdf": str(MANIFEST_SPEC_PROVENANCE["semantic_source_pdf"]),
        "semantic_pdf_role": str(MANIFEST_SPEC_PROVENANCE["semantic_pdf_role"]),
        "structural_blueprint": str(MANIFEST_SPEC_PROVENANCE["structural_blueprint"]),
        "structural_generation_order": str(MANIFEST_SPEC_PROVENANCE["structural_generation_order"]),
        "structural_hint_role": str(MANIFEST_SPEC_PROVENANCE["structural_hint_role"]),
        "target_file": "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
        "target_test": "tests/jugeo/thesis/semantic_center/test_the_ag_dtt_ai_thesis.py",
        "stage": "chapter-01",
        "sequence": 63,
        "chapter_number": CHAPTER_NUMBER,
        "part_number": PART_NUMBER,
        "chapter_title": CHAPTER_TITLE,
        "section_title": INTRODUCTION_SOURCE_SECTIONS[2],
    }
)

THESIS_WORLDVIEW_LINES: Final[tuple[str, ...]] = (
    "JuGeo is AG+DTT+AI in a strict sense rather than by branding shorthand.",
    "Algebraic geometry contributes sites, covers, hypercovers, descent data, gluing laws, Cech cocycles, and obstruction classes.",
    "Dependent type theory contributes context-sensitive judgment form, evidence-bearing claims, witness terms, and residual obligations.",
    "AI contributes controlled proposal, analogical transfer, and frontier search over future semantic states.",
    "Remove any one of these three and the architecture no longer explains project-scale generation and verification.",
    "AI may widen search and suggest repairs, but settlement authority remains below proof-backed closure until stronger evidence arrives.",
)

THESIS_RUNTIME_OBJECTS: Final[tuple[str, ...]] = (
    "site authority",
    "cover and hypercover records",
    "context authority",
    "judgment authority",
    "local sections",
    "overlap treaties",
    "witness terms",
    "residual obligations",
    "obstruction authority",
    "certificate authority",
    "AI routing authority",
    "replay authority",
)

THESIS_AUTHORITY_CENTERS: Final[tuple[str, ...]] = (
    "SiteAuthority",
    "ContextAuthority",
    "JudgmentAuthority",
    "ObstructionAuthority",
    "MemoryAuthority",
    "PackAuthority",
    "BridgeAuthority",
    "ReplayAuthority",
    "CertificateAuthority",
    "AIRoutingAuthority",
)

THESIS_COMPONENT_ORDER: Final[tuple[str, ...]] = ("ag", "dtt", "ai")
_EMPTY_MAPPING: Final[Mapping[str, Any]] = MappingProxyType({})


class ThesisComponentKind(str, Enum):
    """The three irreducible components of the chapter's thesis."""

    AG = "ag"
    DTT = "dtt"
    AI = "ai"


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
        text = _normalize_required_text(str(item), field_name=field_name)
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_mapping(value: Mapping[str, Any] | None, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return _EMPTY_MAPPING
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return MappingProxyType({str(key): value[key] for key in value})


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
        return Coordinate(parts, kind=CoordinateKind.REGION)
    parts = tuple(_normalize_required_text(str(part), field_name=field_name) for part in value)
    return Coordinate(parts, kind=CoordinateKind.REGION)


def _normalize_component_kind(value: ThesisComponentKind | str) -> ThesisComponentKind:
    if isinstance(value, ThesisComponentKind):
        return value
    return ThesisComponentKind(_normalize_required_text(value, field_name="component").lower())


def _normalize_claim_text(value: str) -> str:
    return " ".join(_normalize_required_text(value, field_name="claim").lower().split())


def _stable_json(value: Any) -> str:
    def _convert(item: Any) -> Any:
        if isinstance(item, Coordinate):
            return item.serialize()
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


def _unique_text(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _trust_weight(level: TrustLevel) -> float:
    ordered = TrustLevel.ordered()
    denominator = max(1, len(ordered) - 1)
    return level.rank_index() / denominator


def _failure_for_invalid_input(message: str, *, coordinate: str, metadata: Mapping[str, Any] | None = None) -> StructuredFailure:
    return StructuredFailure(
        message=message,
        scope=FailureScope.CHAPTER,
        classification=FailureClassification.INVALID_VALUE,
        coordinate=coordinate,
        trust_boundary="ag-dtt-ai-input",
        metadata=dict(metadata or {}),
        recoverable=True,
    )


def _component_sort_key(component: ThesisComponentKind) -> int:
    return THESIS_COMPONENT_ORDER.index(component.value)


def _expected_component_sequence() -> tuple[ThesisComponentKind, ...]:
    return tuple(ThesisComponentKind(entry) for entry in THESIS_COMPONENT_ORDER)


@dataclass(frozen=True, slots=True)
class AGDTTAIObservation:
    """One local observation offered by one component of the synthesis."""

    observation_id: str
    component: ThesisComponentKind
    coordinate: Coordinate
    target_coordinate: Coordinate
    thesis_clause: str
    claim: str
    semantic_tags: tuple[str, ...]
    runtime_objects: tuple[str, ...]
    evidence_keys: tuple[str, ...] = ()
    residual_obligations: tuple[str, ...] = ()
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    source_role: str = "chapter-analysis"
    provenance: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _normalize_required_text(self.observation_id, field_name="observation_id"))
        object.__setattr__(self, "component", _normalize_component_kind(self.component))
        object.__setattr__(self, "thesis_clause", _normalize_required_text(self.thesis_clause, field_name="thesis_clause"))
        object.__setattr__(self, "claim", _normalize_required_text(self.claim, field_name="claim"))
        object.__setattr__(self, "semantic_tags", _normalize_text_tuple(self.semantic_tags, field_name="semantic_tags"))
        object.__setattr__(self, "runtime_objects", _normalize_text_tuple(self.runtime_objects, field_name="runtime_objects"))
        object.__setattr__(self, "evidence_keys", _normalize_text_tuple(self.evidence_keys, field_name="evidence_keys"))
        object.__setattr__(self, "residual_obligations", _normalize_text_tuple(self.residual_obligations, field_name="residual_obligations"))
        object.__setattr__(self, "source_role", _normalize_required_text(self.source_role, field_name="source_role"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        object.__setattr__(self, "metadata", _normalize_mapping(self.metadata, field_name="metadata"))
        if not isinstance(self.coordinate, Coordinate):
            raise TypeError("coordinate must be a Coordinate")
        if not isinstance(self.target_coordinate, Coordinate):
            raise TypeError("target_coordinate must be a Coordinate")
        if not isinstance(self.trust_level, TrustLevel):
            raise TypeError("trust_level must be a TrustLevel")

    @property
    def canonical_claim(self) -> str:
        return _normalize_claim_text(self.claim)

    @property
    def section_key(self) -> str:
        return f"{self.component.value}:{self.coordinate.key}"

    def to_local_section(self) -> LocalSection:
        return LocalSection(
            coordinate=self.section_key,
            judgment_data={
                "component": self.component.value,
                "target_coordinate": self.target_coordinate.key,
                "thesis_clause": self.thesis_clause,
                "canonical_claim": self.canonical_claim,
                "semantic_tags": self.semantic_tags,
                "runtime_objects": self.runtime_objects,
                "source_role": self.source_role,
                "claim_digest": _stable_digest(self.component.value, self.target_coordinate.key, self.thesis_clause),
            },
            evidence_bundle=self.evidence_keys,
            trust_level=_trust_weight(self.trust_level),
            provenance=self.provenance,
            is_partial=bool(self.residual_obligations),
            residual_obligations=list(self.residual_obligations),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "component": self.component.value,
            "coordinate": self.coordinate.serialize(),
            "target_coordinate": self.target_coordinate.serialize(),
            "thesis_clause": self.thesis_clause,
            "claim": self.claim,
            "canonical_claim": self.canonical_claim,
            "semantic_tags": list(self.semantic_tags),
            "runtime_objects": list(self.runtime_objects),
            "evidence_keys": list(self.evidence_keys),
            "residual_obligations": list(self.residual_obligations),
            "trust_level": self.trust_level.value,
            "source_role": self.source_role,
            "provenance": list(self.provenance),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class AGDTTAIDiscrepancy:
    """Conflict discovered on an overlap between two component observations."""

    discrepancy_id: str
    left_observation_id: str
    right_observation_id: str
    overlap_coordinate: str
    conflicting_fields: tuple[str, ...]
    explanation: str
    repair_moves: tuple[str, ...]
    severity: str = "high"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "discrepancy_id", _normalize_required_text(self.discrepancy_id, field_name="discrepancy_id"))
        object.__setattr__(self, "left_observation_id", _normalize_required_text(self.left_observation_id, field_name="left_observation_id"))
        object.__setattr__(self, "right_observation_id", _normalize_required_text(self.right_observation_id, field_name="right_observation_id"))
        object.__setattr__(self, "overlap_coordinate", _normalize_required_text(self.overlap_coordinate, field_name="overlap_coordinate"))
        object.__setattr__(self, "conflicting_fields", _normalize_text_tuple(self.conflicting_fields, field_name="conflicting_fields"))
        object.__setattr__(self, "explanation", _normalize_required_text(self.explanation, field_name="explanation"))
        object.__setattr__(self, "repair_moves", _normalize_text_tuple(self.repair_moves, field_name="repair_moves"))
        object.__setattr__(self, "severity", _normalize_required_text(self.severity, field_name="severity"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def summary_line(self) -> str:
        return f"{self.severity}: {self.left_observation_id} vs {self.right_observation_id} on {', '.join(self.conflicting_fields)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "discrepancy_id": self.discrepancy_id,
            "left_observation_id": self.left_observation_id,
            "right_observation_id": self.right_observation_id,
            "overlap_coordinate": self.overlap_coordinate,
            "conflicting_fields": list(self.conflicting_fields),
            "explanation": self.explanation,
            "repair_moves": list(self.repair_moves),
            "severity": self.severity,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class ComponentInteraction:
    """An explicit law describing how two thesis components cooperate."""

    interaction_id: str
    left_component: ThesisComponentKind
    right_component: ThesisComponentKind
    interaction_law: str
    shared_runtime_objects: tuple[str, ...]
    failure_modes: tuple[str, ...]
    semantic_payoff: str
    trust_boundary: str = "ag-dtt-ai-synthesis"
    settlement_authority: str = "global settlement still requires explicit evidence and review"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "interaction_id", _normalize_required_text(self.interaction_id, field_name="interaction_id"))
        object.__setattr__(self, "left_component", _normalize_component_kind(self.left_component))
        object.__setattr__(self, "right_component", _normalize_component_kind(self.right_component))
        object.__setattr__(self, "interaction_law", _normalize_required_text(self.interaction_law, field_name="interaction_law"))
        object.__setattr__(self, "shared_runtime_objects", _normalize_text_tuple(self.shared_runtime_objects, field_name="shared_runtime_objects"))
        object.__setattr__(self, "failure_modes", _normalize_text_tuple(self.failure_modes, field_name="failure_modes"))
        object.__setattr__(self, "semantic_payoff", _normalize_required_text(self.semantic_payoff, field_name="semantic_payoff"))
        object.__setattr__(self, "trust_boundary", _normalize_required_text(self.trust_boundary, field_name="trust_boundary"))
        object.__setattr__(self, "settlement_authority", _normalize_required_text(self.settlement_authority, field_name="settlement_authority"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))

    def summary_line(self) -> str:
        return f"{self.left_component.value}+{self.right_component.value}: {self.semantic_payoff}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "left_component": self.left_component.value,
            "right_component": self.right_component.value,
            "interaction_law": self.interaction_law,
            "shared_runtime_objects": list(self.shared_runtime_objects),
            "failure_modes": list(self.failure_modes),
            "semantic_payoff": self.semantic_payoff,
            "trust_boundary": self.trust_boundary,
            "settlement_authority": self.settlement_authority,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class AlgebraicGeometryComponent:
    """The AG layer: sites, covers, descent, and obstruction structure."""

    component_name: str = "AG"
    tradition: str = "algebraic geometry"
    theory_role: str = "Provides the base site, admissible covers and hypercovers, descent data, gluing laws, and obstruction classes."
    key_objects: tuple[str, ...] = ("site", "cover", "hypercover", "local section", "descent datum", "gluing law", "Cech cocycle", "obstruction class")
    runtime_objects: tuple[str, ...] = ("site authority", "cover and hypercover records", "local sections", "overlap treaties", "obstruction authority")
    semantic_tags: tuple[str, ...] = ("local-to-global", "project-scale", "geometry", "descent", "obstruction")
    authority_boundary: str = "AG specifies locality and gluing structure but does not itself manufacture typed witnesses."
    provenance: tuple[str, ...] = ("preliminaries/theory2.tex", "preliminaries/theory2.pdf")

    def chapter_claim(self) -> str:
        return "Algebraic geometry gives JuGeo the site of semantic coordinates, the admissible covers, and the obstruction-sensitive local-to-global discipline required for project-scale closure."

    def summary_lines(self) -> list[str]:
        return [f"[{self.component_name}] {self.tradition}", textwrap.fill(self.theory_role, width=88), "Key objects: " + ", ".join(self.key_objects), "Authority boundary: " + self.authority_boundary]

    def as_observation(self, target_coordinate: Coordinate | Sequence[str] | str, *, thesis_clause: str = "strict-ag-dtt-ai-synthesis", trust_level: TrustLevel = TrustLevel.HUMAN_ATTESTED) -> AGDTTAIObservation:
        target = _normalize_coordinate(target_coordinate, field_name="target_coordinate")
        return AGDTTAIObservation(
            observation_id=f"ag:{target.key or 'root'}",
            component=ThesisComponentKind.AG,
            coordinate=Coordinate(target.components + ("ag",), kind=CoordinateKind.REGION),
            target_coordinate=target,
            thesis_clause=thesis_clause,
            claim=self.chapter_claim(),
            semantic_tags=self.semantic_tags,
            runtime_objects=self.runtime_objects,
            evidence_keys=("tex:chapter1:s03", "pdf:chapter1:s03"),
            trust_level=trust_level,
            source_role="human-reviewed-theory",
            provenance=("default-ag-observation",),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"component": "ag", "component_name": self.component_name, "tradition": self.tradition, "theory_role": self.theory_role, "key_objects": list(self.key_objects), "runtime_objects": list(self.runtime_objects), "semantic_tags": list(self.semantic_tags), "authority_boundary": self.authority_boundary, "provenance": list(self.provenance)}


@dataclass(frozen=True, slots=True)
class DependentTypeComponent:
    """The DTT layer: contexts, dependent claims, witnesses, and obligations."""

    component_name: str = "DTT"
    tradition: str = "dependent type theory"
    theory_role: str = "Provides context-sensitive judgment form, evidence-bearing claims, witness terms, normalization discipline, and explicit residual obligations."
    key_objects: tuple[str, ...] = ("context", "dependent claim", "witness term", "judgment form", "residual obligation", "validation condition")
    runtime_objects: tuple[str, ...] = ("context authority", "judgment authority", "witness terms", "residual obligations", "certificate authority")
    semantic_tags: tuple[str, ...] = ("local-to-global", "project-scale", "typed-witnesses", "evidence-bearing", "obligations")
    authority_boundary: str = "DTT specifies admissible typed claims and witnesses but does not by itself explore the frontier of future semantic states."
    provenance: tuple[str, ...] = ("preliminaries/theory2.tex", "preliminaries/theory2.pdf")

    def chapter_claim(self) -> str:
        return "Dependent type theory gives JuGeo context-sensitive judgments, evidence-bearing claims, witness discipline, and explicit residual obligations so that no patch is semantically untyped."

    def summary_lines(self) -> list[str]:
        return [f"[{self.component_name}] {self.tradition}", textwrap.fill(self.theory_role, width=88), "Key objects: " + ", ".join(self.key_objects), "Authority boundary: " + self.authority_boundary]

    def as_observation(self, target_coordinate: Coordinate | Sequence[str] | str, *, thesis_clause: str = "strict-ag-dtt-ai-synthesis", trust_level: TrustLevel = TrustLevel.HUMAN_ATTESTED) -> AGDTTAIObservation:
        target = _normalize_coordinate(target_coordinate, field_name="target_coordinate")
        return AGDTTAIObservation(
            observation_id=f"dtt:{target.key or 'root'}",
            component=ThesisComponentKind.DTT,
            coordinate=Coordinate(target.components + ("dtt",), kind=CoordinateKind.REGION),
            target_coordinate=target,
            thesis_clause=thesis_clause,
            claim=self.chapter_claim(),
            semantic_tags=self.semantic_tags,
            runtime_objects=self.runtime_objects,
            evidence_keys=("tex:chapter1:s03", "models:JUGEO_WORLDVIEW"),
            trust_level=trust_level,
            source_role="human-reviewed-theory",
            provenance=("default-dtt-observation",),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"component": "dtt", "component_name": self.component_name, "tradition": self.tradition, "theory_role": self.theory_role, "key_objects": list(self.key_objects), "runtime_objects": list(self.runtime_objects), "semantic_tags": list(self.semantic_tags), "authority_boundary": self.authority_boundary, "provenance": list(self.provenance)}


@dataclass(frozen=True, slots=True)
class AIComponent:
    """The AI layer: controlled proposal, search, routing, and refinement."""

    component_name: str = "AI"
    tradition: str = "controlled AI proposal and search"
    theory_role: str = "Provides controlled proposal, analogical transfer, frontier search, and candidate semantic refinements over future states."
    key_objects: tuple[str, ...] = ("proposal slot", "analogy", "future-state search", "cover refinement candidate", "repair candidate", "routing policy")
    runtime_objects: tuple[str, ...] = ("AI routing authority", "proposal records", "search traces", "replay authority", "memory authority")
    semantic_tags: tuple[str, ...] = ("local-to-global", "project-scale", "proposal", "search", "future-state")
    proposal_ceiling: TrustLevel = TrustLevel.ORACLE_PROPOSED
    reviewed_summary_trust: TrustLevel = TrustLevel.HUMAN_ATTESTED
    authority_boundary: str = "AI may propose, rank, and route; it may not silently settle global closure without stronger evidence."
    provenance: tuple[str, ...] = ("preliminaries/theory2.tex", "preliminaries/theory2.pdf")

    def chapter_claim(self) -> str:
        return "AI gives JuGeo controlled proposal, analogical transfer, and semantic frontier search over future semantic states while remaining explicitly subordinate to settlement authority."

    def prohibits_silent_settlement(self) -> bool:
        return self.proposal_ceiling <= TrustLevel.ORACLE_PROPOSED and self.reviewed_summary_trust >= TrustLevel.HUMAN_ATTESTED

    def summary_lines(self) -> list[str]:
        return [f"[{self.component_name}] {self.tradition}", textwrap.fill(self.theory_role, width=88), "Key objects: " + ", ".join(self.key_objects), f"Proposal ceiling: {self.proposal_ceiling.value}", "Authority boundary: " + self.authority_boundary]

    def as_observation(self, target_coordinate: Coordinate | Sequence[str] | str, *, thesis_clause: str = "strict-ag-dtt-ai-synthesis", trust_level: TrustLevel | None = None) -> AGDTTAIObservation:
        target = _normalize_coordinate(target_coordinate, field_name="target_coordinate")
        return AGDTTAIObservation(
            observation_id=f"ai:{target.key or 'root'}",
            component=ThesisComponentKind.AI,
            coordinate=Coordinate(target.components + ("ai",), kind=CoordinateKind.REGION),
            target_coordinate=target,
            thesis_clause=thesis_clause,
            claim=self.chapter_claim(),
            semantic_tags=self.semantic_tags,
            runtime_objects=self.runtime_objects,
            evidence_keys=("tex:chapter1:s03", "trust:no-silent-promotion"),
            trust_level=trust_level or self.reviewed_summary_trust,
            source_role="human-reviewed-theory",
            provenance=("default-ai-observation",),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"component": "ai", "component_name": self.component_name, "tradition": self.tradition, "theory_role": self.theory_role, "key_objects": list(self.key_objects), "runtime_objects": list(self.runtime_objects), "semantic_tags": list(self.semantic_tags), "proposal_ceiling": self.proposal_ceiling.value, "reviewed_summary_trust": self.reviewed_summary_trust.value, "authority_boundary": self.authority_boundary, "provenance": list(self.provenance)}


DEFAULT_COMPONENT_INTERACTIONS: Final[tuple[ComponentInteraction, ...]] = (
    ComponentInteraction("ag-dtt", ThesisComponentKind.AG, ThesisComponentKind.DTT, "The AG layer supplies the site and overlap structure on which DTT judgments and witnesses are interpreted.", ("context authority", "judgment authority", "local sections", "overlap treaties"), ("support not attached to witness", "overlap law underspecified", "typed witness cannot descend"), "Typed evidence lives on explicit geometric support rather than in an unlocated proof script.", provenance=("preliminaries/theory2.tex#s03",)),
    ComponentInteraction("ag-ai", ThesisComponentKind.AG, ThesisComponentKind.AI, "The AG layer bounds the AI search space by legal coordinates, supports, covers, and cover refinements.", ("site authority", "cover and hypercover records", "proposal records", "search traces"), ("proposal outside support", "cover refinement not admissible", "repair candidate ignores overlap law"), "Proposal pressure stays geometrically meaningful because the search is constrained by locality and gluing structure.", provenance=("preliminaries/theory2.tex#s03",)),
    ComponentInteraction("dtt-ai", ThesisComponentKind.DTT, ThesisComponentKind.AI, "The DTT layer constrains AI outputs to typed candidates with explicit witnesses or residual obligations.", ("witness terms", "residual obligations", "proposal records", "certificate authority"), ("proposal lacks typing context", "residual obligations hidden", "oracle suggestion mistaken for settlement"), "AI can explore future semantic states without erasing the discipline of evidence-bearing judgment forms.", provenance=("preliminaries/theory2.tex#s03",)),
)


@dataclass(frozen=True, slots=True)
class ThesisUnification:
    """The strict synthesis statement tying AG, DTT, and AI together."""

    unification_id: str
    section_title: str
    thesis_statement: str
    strictness_claim: str
    interactions: tuple[ComponentInteraction, ...]
    runtime_objects: tuple[str, ...]
    authority_centers: tuple[str, ...]
    worldview_commitments: tuple[str, ...]
    authority_boundary: str
    settlement_rule: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "unification_id", _normalize_required_text(self.unification_id, field_name="unification_id"))
        object.__setattr__(self, "section_title", _normalize_required_text(self.section_title, field_name="section_title"))
        object.__setattr__(self, "thesis_statement", _normalize_required_text(self.thesis_statement, field_name="thesis_statement"))
        object.__setattr__(self, "strictness_claim", _normalize_required_text(self.strictness_claim, field_name="strictness_claim"))
        object.__setattr__(self, "runtime_objects", _normalize_text_tuple(self.runtime_objects, field_name="runtime_objects"))
        object.__setattr__(self, "authority_centers", _normalize_text_tuple(self.authority_centers, field_name="authority_centers"))
        object.__setattr__(self, "worldview_commitments", _normalize_text_tuple(self.worldview_commitments, field_name="worldview_commitments"))
        object.__setattr__(self, "authority_boundary", _normalize_required_text(self.authority_boundary, field_name="authority_boundary"))
        object.__setattr__(self, "settlement_rule", _normalize_required_text(self.settlement_rule, field_name="settlement_rule"))
        object.__setattr__(self, "provenance", _normalize_mapping(self.provenance, field_name="provenance"))

    @property
    def canonical_digest(self) -> str:
        return _stable_digest(self.unification_id, self.thesis_statement, tuple(item.to_dict() for item in self.interactions))

    def requires_all_three_components(self) -> bool:
        joined = f"{self.thesis_statement} {self.strictness_claim}".lower()
        return "remove any one of these three" in joined and "project-scale generation and verification" in joined

    def summary_lines(self) -> list[str]:
        lines = [self.section_title, textwrap.fill(self.thesis_statement, width=88), textwrap.fill(self.strictness_claim, width=88), "Runtime objects: " + ", ".join(self.runtime_objects), "Authority centers: " + ", ".join(self.authority_centers), "Interactions:"]
        lines.extend(f"  - {interaction.summary_line()}" for interaction in self.interactions)
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {"unification_id": self.unification_id, "section_title": self.section_title, "thesis_statement": self.thesis_statement, "strictness_claim": self.strictness_claim, "interactions": [item.to_dict() for item in self.interactions], "runtime_objects": list(self.runtime_objects), "authority_centers": list(self.authority_centers), "worldview_commitments": list(self.worldview_commitments), "authority_boundary": self.authority_boundary, "settlement_rule": self.settlement_rule, "provenance": dict(self.provenance), "canonical_digest": self.canonical_digest}


@dataclass(frozen=True, slots=True)
class AGDTTAIThesis:
    """Stable chapter object for the AG+DTT+AI synthesis claim."""

    thesis_id: str
    chapter_number: int
    part_number: int
    chapter_title: str
    section_title: str
    worldview_record_id: str
    algebraic_geometry: AlgebraicGeometryComponent
    dependent_type_theory: DependentTypeComponent
    ai: AIComponent
    unification: ThesisUnification
    worldview_lines: tuple[str, ...]
    runtime_objects: tuple[str, ...]
    authority_centers: tuple[str, ...]
    settlement_floor: TrustLevel = TrustLevel.HUMAN_ATTESTED
    provenance: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "thesis_id", _normalize_required_text(self.thesis_id, field_name="thesis_id"))
        object.__setattr__(self, "chapter_title", _normalize_required_text(self.chapter_title, field_name="chapter_title"))
        object.__setattr__(self, "section_title", _normalize_required_text(self.section_title, field_name="section_title"))
        object.__setattr__(self, "worldview_record_id", _normalize_required_text(self.worldview_record_id, field_name="worldview_record_id"))
        object.__setattr__(self, "worldview_lines", _normalize_text_tuple(self.worldview_lines, field_name="worldview_lines"))
        object.__setattr__(self, "runtime_objects", _normalize_text_tuple(self.runtime_objects, field_name="runtime_objects"))
        object.__setattr__(self, "authority_centers", _normalize_text_tuple(self.authority_centers, field_name="authority_centers"))
        object.__setattr__(self, "provenance", _normalize_mapping(self.provenance, field_name="provenance"))
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))

    @property
    def canonical_digest(self) -> str:
        return _stable_digest(self.thesis_id, self.unification.to_dict(), self.worldview_lines)

    def component(self, name: ThesisComponentKind | str) -> AlgebraicGeometryComponent | DependentTypeComponent | AIComponent:
        component = _normalize_component_kind(name)
        if component is ThesisComponentKind.AG:
            return self.algebraic_geometry
        if component is ThesisComponentKind.DTT:
            return self.dependent_type_theory
        return self.ai

    def supports_worldview(self) -> bool:
        joined = " ".join(self.worldview_lines).lower()
        return "strict sense" in joined and "project-scale generation and verification" in joined and "settlement authority" in joined

    def default_observations(self, target_coordinate: Coordinate | Sequence[str] | str) -> tuple[AGDTTAIObservation, ...]:
        target = _normalize_coordinate(target_coordinate, field_name="target_coordinate")
        return (self.algebraic_geometry.as_observation(target), self.dependent_type_theory.as_observation(target), self.ai.as_observation(target))

    def authority_contract(self) -> dict[str, Any]:
        return {"authority_centers": list(self.authority_centers), "settlement_floor": self.settlement_floor.value, "authority_boundary": self.unification.authority_boundary, "settlement_rule": self.unification.settlement_rule}

    def summary_lines(self) -> list[str]:
        lines = [f"Chapter: {self.chapter_number} - {self.chapter_title}", f"Section: {self.section_title}", f"Worldview record: {self.worldview_record_id}", f"Settlement floor: {self.settlement_floor.value}", "Worldview:"]
        lines.extend(f"  - {line}" for line in self.worldview_lines)
        lines.append("Components:")
        for component in (self.algebraic_geometry, self.dependent_type_theory, self.ai):
            lines.extend(f"  {line}" for line in component.summary_lines())
        lines.append("Unification:")
        lines.extend(f"  {line}" for line in self.unification.summary_lines())
        return lines

    def summary(self) -> str:
        return "\n".join(self.summary_lines())

    def to_dict(self) -> dict[str, Any]:
        return {"thesis_id": self.thesis_id, "chapter_number": self.chapter_number, "part_number": self.part_number, "chapter_title": self.chapter_title, "section_title": self.section_title, "worldview_record_id": self.worldview_record_id, "algebraic_geometry": self.algebraic_geometry.to_dict(), "dependent_type_theory": self.dependent_type_theory.to_dict(), "ai": self.ai.to_dict(), "unification": self.unification.to_dict(), "worldview_lines": list(self.worldview_lines), "runtime_objects": list(self.runtime_objects), "authority_centers": list(self.authority_centers), "settlement_floor": self.settlement_floor.value, "provenance": dict(self.provenance), "notes": list(self.notes), "canonical_digest": self.canonical_digest}


@dataclass(frozen=True, slots=True)
class TheAGDTTAIWitness:
    """Structured witness bundle returned by the analyzer."""

    witness_id: str
    target_coordinate: Coordinate
    objective: str
    worldview_record_id: str
    thesis_id: str
    observations: tuple[AGDTTAIObservation, ...]
    component_coverage: tuple[ThesisComponentKind, ...]
    discrepancies: tuple[AGDTTAIDiscrepancy, ...]
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
        object.__setattr__(self, "thesis_id", _normalize_required_text(self.thesis_id, field_name="thesis_id"))
        object.__setattr__(self, "residual_obligations", _normalize_text_tuple(self.residual_obligations, field_name="residual_obligations"))
        object.__setattr__(self, "repair_frontier", _normalize_text_tuple(self.repair_frontier, field_name="repair_frontier"))
        object.__setattr__(self, "semantic_state", _normalize_mapping(self.semantic_state, field_name="semantic_state"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def discrepancy_count(self) -> int:
        return len(self.discrepancies)

    @property
    def canonical_digest(self) -> str:
        return _stable_digest(self.witness_id, tuple(item.to_dict() for item in self.observations), tuple(item.to_dict() for item in self.discrepancies), self.semantic_state)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Witness {self.witness_id} for {self.target_coordinate.name}",
            f"  objective: {self.objective}",
            f"  observation_count: {self.observation_count}",
            f"  trust: {self.trust_level.value} (floor={self.settlement_floor.value})",
            f"  publishable: {self.publishable}",
            f"  discrepancies: {self.discrepancy_count}",
            f"  residual obligations: {len(self.residual_obligations)}",
        ]
        if self.repair_frontier:
            lines.append("  repair frontier:")
            lines.extend(f"    - {item}" for item in self.repair_frontier)
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {"witness_id": self.witness_id, "target_coordinate": self.target_coordinate.serialize(), "objective": self.objective, "worldview_record_id": self.worldview_record_id, "thesis_id": self.thesis_id, "observations": [item.to_dict() for item in self.observations], "component_coverage": [item.value for item in self.component_coverage], "discrepancies": [item.to_dict() for item in self.discrepancies], "residual_obligations": list(self.residual_obligations), "repair_frontier": list(self.repair_frontier), "trust_level": self.trust_level.value, "settlement_floor": self.settlement_floor.value, "publishable": self.publishable, "semantic_state": dict(self.semantic_state), "provenance": list(self.provenance), "notes": list(self.notes), "canonical_digest": self.canonical_digest}


class TheAGDTTAIAnalyzer:
    """Analyzer for the chapter's AG+DTT+AI synthesis claim."""

    def __init__(self, thesis: AGDTTAIThesis, *, worldview: JuGeoWorldview = JUGEO_WORLDVIEW, algebra: TrustAlgebra | None = None) -> None:
        self.thesis = thesis
        self.worldview = worldview
        self.algebra = algebra or TrustAlgebra()

    def component_roles(self) -> dict[str, dict[str, Any]]:
        return {"ag": self.thesis.algebraic_geometry.to_dict(), "dtt": self.thesis.dependent_type_theory.to_dict(), "ai": self.thesis.ai.to_dict()}

    def analyze(self, target_coordinate: Coordinate | Sequence[str] | str, observations: Sequence[AGDTTAIObservation], *, objective: str = "publishable-thesis-section", settlement_floor: TrustLevel | None = None) -> TheAGDTTAIWitness:
        if not observations:
            failure = _failure_for_invalid_input("AG+DTT+AI analysis requires at least one observation", coordinate="chapter-01/s03/analyze")
            raise_with_scope(
                "missing-observations",
                message=failure.message,
                scope=failure.scope,
                classification=failure.classification,
                coordinate=failure.coordinate,
                trust_boundary=failure.trust_boundary,
                provenance={"module": __name__, "reason": "empty-observation-set"},
                notes=("AG+DTT+AI analysis requires explicit local observations.",),
                recoverable=failure.recoverable,
            )
        target = _normalize_coordinate(target_coordinate, field_name="target_coordinate")
        floor = settlement_floor or self.thesis.settlement_floor
        normalized = tuple(observations)
        coverage = tuple(sorted({_normalize_component_kind(item.component) for item in normalized}, key=_component_sort_key))
        gluing = self._build_gluing_data(normalized)
        gluing.verify_all_overlaps()
        discrepancies = self._build_discrepancies(gluing, normalized)
        trust_level = _aggregate_trust(tuple(item.trust_level for item in normalized))
        residuals = self._build_residual_obligations(normalized, coverage, trust_level, floor, discrepancies)
        repair_frontier = self._build_repair_frontier(coverage, trust_level, floor, discrepancies, residuals)
        publishable = not discrepancies and not residuals and _trust_meets_floor(trust_level, floor)
        semantic_state = MappingProxyType({"target_coordinate": target.key, "observation_count": len(normalized), "component_coverage": [item.value for item in coverage], "runtime_objects": [obj for item in normalized for obj in item.runtime_objects], "authority_centers": list(self.thesis.authority_centers), "discrepancy_count": len(discrepancies), "residual_obligation_count": len(residuals), "trust_level": trust_level.value, "settlement_floor": floor.value, "publishable": publishable, "worldview_record_id": self.worldview.record_id})
        return TheAGDTTAIWitness(f"ag-dtt-ai:{_stable_digest(target.key, tuple(item.observation_id for item in normalized))}", target, objective, self.worldview.record_id, self.thesis.thesis_id, normalized, coverage, discrepancies, residuals, repair_frontier, trust_level, floor, publishable, semantic_state, provenance=("TheAGDTTAIAnalyzer", __name__), notes=("AI observations remain bounded by explicit trust ceilings.",))

    def _build_gluing_data(self, observations: Sequence[AGDTTAIObservation]) -> GluingData:
        data = GluingData()
        for observation in observations:
            data.add_section(observation.to_local_section())
        for left, right in combinations(observations, 2):
            if left.target_coordinate.key != right.target_coordinate.key:
                continue
            data.add_overlap(OverlapCondition(left.section_key, right.section_key, f"{left.section_key}&{right.section_key}", compatibility_predicate=self._overlap_predicate))
        return data

    @staticmethod
    def _overlap_predicate(left_data: Mapping[str, Any], right_data: Mapping[str, Any]) -> bool:
        return left_data.get("target_coordinate") == right_data.get("target_coordinate") and left_data.get("thesis_clause") == right_data.get("thesis_clause") and bool(set(left_data.get("semantic_tags", ())) & set(right_data.get("semantic_tags", ())))

    def _build_discrepancies(self, gluing: GluingData, observations: Sequence[AGDTTAIObservation]) -> tuple[AGDTTAIDiscrepancy, ...]:
        lookup = {observation.section_key: observation for observation in observations}
        discrepancies: list[AGDTTAIDiscrepancy] = []
        for overlap in gluing.find_violated_overlaps():
            left = lookup[overlap.left_coordinate]
            right = lookup[overlap.right_coordinate]
            fields: list[str] = []
            if left.thesis_clause != right.thesis_clause:
                fields.append("thesis_clause")
            if not (set(left.semantic_tags) & set(right.semantic_tags)):
                fields.append("semantic_tags")
            discrepancies.append(AGDTTAIDiscrepancy(f"disc:{_stable_digest(left.observation_id, right.observation_id, fields)}", left.observation_id, right.observation_id, overlap.overlap_coordinate, tuple(fields or ["compatibility"]), "The local observations do not glue on overlap because they disagree about clause identity or the shared semantic vocabulary needed for synthesis.", ("align the thesis clause identifier across components", "repair semantic tags so the overlap names a common local-to-global obligation"), severity="high", provenance=(left.observation_id, right.observation_id)))
        return tuple(discrepancies)

    def _build_residual_obligations(self, observations: Sequence[AGDTTAIObservation], coverage: Sequence[ThesisComponentKind], trust_level: TrustLevel, floor: TrustLevel, discrepancies: Sequence[AGDTTAIDiscrepancy]) -> tuple[str, ...]:
        obligations = [item for observation in observations for item in observation.residual_obligations]
        if tuple(coverage) != _expected_component_sequence():
            missing = [component.value for component in _expected_component_sequence() if component not in coverage]
            obligations.append("missing component coverage: " + ", ".join(missing))
        if not _trust_meets_floor(trust_level, floor):
            obligations.append(f"trust level {trust_level.value} remains below the settlement floor {floor.value}")
        if discrepancies:
            obligations.append("local observations do not yet glue into a global synthesis witness")
        return _unique_text(obligations)

    def _build_repair_frontier(self, coverage: Sequence[ThesisComponentKind], trust_level: TrustLevel, floor: TrustLevel, discrepancies: Sequence[AGDTTAIDiscrepancy], residuals: Sequence[str]) -> tuple[str, ...]:
        frontier: list[str] = []
        if tuple(coverage) != _expected_component_sequence():
            missing = [component.value for component in _expected_component_sequence() if component not in coverage]
            frontier.append("restore missing synthesis component(s): " + ", ".join(missing))
        if discrepancies:
            frontier.append("align overlap claims so AG, DTT, and AI speak about the same thesis clause")
            frontier.extend(move for discrepancy in discrepancies for move in discrepancy.repair_moves)
        if not _trust_meets_floor(trust_level, floor):
            frontier.append("raise trust above the settlement floor with explicit non-proposal evidence or human review")
        if any("obligation" in item.lower() for item in residuals):
            frontier.append("discharge residual obligations instead of treating them as narrative-only notes")
        return _unique_text(frontier)


class TheAGDTTAICoordinator:
    """Top-level coordinator bundling the thesis object and analyzer."""

    def __init__(self, thesis: AGDTTAIThesis, analyzer: TheAGDTTAIAnalyzer | None = None, *, worldview: JuGeoWorldview = JUGEO_WORLDVIEW) -> None:
        self.thesis = thesis
        self.worldview = worldview
        self.analyzer = analyzer or TheAGDTTAIAnalyzer(thesis, worldview=worldview)

    @classmethod
    def build_default(cls) -> "TheAGDTTAICoordinator":
        return cls(thesis=THE_AG_DTT_AI_THESIS, worldview=JUGEO_WORLDVIEW)

    def coordinate(self, target_coordinate: Coordinate | Sequence[str] | str, observations: Sequence[AGDTTAIObservation], *, objective: str = "publishable-thesis-section", settlement_floor: TrustLevel | None = None) -> TheAGDTTAIWitness:
        return self.analyzer.analyze(target_coordinate, observations, objective=objective, settlement_floor=settlement_floor)

    def runtime_contract(self) -> dict[str, Any]:
        return {"thesis": self.thesis.to_dict(), "authority_contract": self.thesis.authority_contract(), "component_roles": self.analyzer.component_roles()}

    def summary(self) -> str:
        return self.thesis.summary()


_DEFAULT_AG_COMPONENT: Final[AlgebraicGeometryComponent] = AlgebraicGeometryComponent()
_DEFAULT_DTT_COMPONENT: Final[DependentTypeComponent] = DependentTypeComponent()
_DEFAULT_AI_COMPONENT: Final[AIComponent] = AIComponent()
_DEFAULT_UNIFICATION: Final[ThesisUnification] = ThesisUnification(
    "chapter-01.s03.unification",
    INTRODUCTION_SOURCE_SECTIONS[2],
    "The system is AG+DTT+AI in a strict sense: algebraic geometry gives the site, covers, hypercovers, local sections, gluing laws, and obstruction classes; dependent type theory gives context-sensitive judgments and evidence-bearing witnesses; AI gives controlled proposal and search over future semantic states.",
    "Remove any one of these three and the architecture no longer explains project-scale generation and verification.",
    DEFAULT_COMPONENT_INTERACTIONS,
    THESIS_RUNTIME_OBJECTS,
    THESIS_AUTHORITY_CENTERS,
    WORLDVIEW_COMMITMENTS,
    "This chapter-level synthesis may describe how AG, DTT, and AI cooperate, but it must still preserve separate authority centers and trust boundaries.",
    "A globally settled claim requires gluing-compatible observations plus evidence at or above the declared settlement floor; AI proposal alone is insufficient.",
    provenance=S03_SPEC_PROVENANCE,
)

THE_AG_DTT_AI_THESIS: Final[AGDTTAIThesis] = AGDTTAIThesis(
    "chapter-01.s03.ag-dtt-ai-thesis",
    CHAPTER_NUMBER,
    PART_NUMBER,
    CHAPTER_TITLE,
    INTRODUCTION_SOURCE_SECTIONS[2],
    JUGEO_WORLDVIEW.record_id,
    _DEFAULT_AG_COMPONENT,
    _DEFAULT_DTT_COMPONENT,
    _DEFAULT_AI_COMPONENT,
    _DEFAULT_UNIFICATION,
    THESIS_WORLDVIEW_LINES,
    THESIS_RUNTIME_OBJECTS,
    THESIS_AUTHORITY_CENTERS,
    settlement_floor=TrustLevel.HUMAN_ATTESTED,
    provenance=S03_SPEC_PROVENANCE,
    notes=("The thesis object is a chapter-facing contract, not a replacement for later theorem catalogs.",),
)

DEFAULT_THE_AG_DTT_AI_COORDINATOR: Final[TheAGDTTAICoordinator] = TheAGDTTAICoordinator.build_default()
