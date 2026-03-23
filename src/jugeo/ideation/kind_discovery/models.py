"""Domain models for kind-discovery (theory2.tex Ch 56).

These dataclasses represent the core objects used across the kind-discovery
pipeline: obstruction fields, patterns, candidates, bootstrap plans, and
fully-realised new kinds.

Module layout::

    KindStatus          - lifecycle status of a discovered kind
    ObstructionType     - classification of obstructions
    ObstructionField    - a field/domain of mathematical obstructions
    KindPattern         - a pattern detected in obstruction data
    KindCandidate       - a candidate for a new kind, before formalisation
    KindBootstrapPlan   - execution plan for bootstrapping a kind
    NewKind             - a fully defined and formalised new kind
"""

from __future__ import annotations
import collections
import dataclasses
import datetime
import enum
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _tokenize(text: str) -> list:
    return re.findall(r"[a-z]+", text.lower())


def _jaccard(a, b) -> float:
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _coerce_obstruction_type(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in {member.value for member in ObstructionType}:
        return text

    keyword_map = {
        "algebra": ObstructionType.ALGEBRAIC.value,
        "cohom": ObstructionType.ALGEBRAIC.value,
        "ext^": ObstructionType.ALGEBRAIC.value,
        "module": ObstructionType.ALGEBRAIC.value,
        "struct": ObstructionType.STRUCTURAL.value,
        "defin": ObstructionType.DEFINITIONAL.value,
        "comput": ObstructionType.COMPUTATIONAL.value,
        "logic": ObstructionType.LOGICAL.value,
        "empir": ObstructionType.EMPIRICAL.value,
        "bound": ObstructionType.BOUNDARY.value,
        "categor": ObstructionType.CATEGORICAL.value,
        "relation": ObstructionType.RELATIONAL.value,
        "semantic": ObstructionType.SEMANTIC.value,
        "syntax": ObstructionType.SYNTACTIC.value,
    }
    for needle, normalized in keyword_map.items():
        if needle in text:
            return normalized
    return text


def _normalize_obstruction_types(values: Any) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values or ():
        item = _coerce_obstruction_type(value)
        if item and item not in normalized:
            normalized.append(item)
    return tuple(normalized)


class KindStatus(str, enum.Enum):
    PROPOSED = "proposed"
    PROVISIONAL = "proposed"
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    ESTABLISHED = "validated"
    BOOTSTRAPPING = "bootstrap_active"
    REJECTED = "rejected"
    RETIRED = "retired"
    BOOTSTRAP_PENDING = "bootstrap_pending"
    BOOTSTRAP_ACTIVE = "bootstrap_active"
    BOOTSTRAP_COMPLETE = "bootstrap_complete"

    def is_terminal(self) -> bool:
        return self in (KindStatus.VALIDATED, KindStatus.REJECTED, KindStatus.RETIRED)

    def is_bootstrap_phase(self) -> bool:
        return self in (
            KindStatus.BOOTSTRAP_PENDING,
            KindStatus.BOOTSTRAP_ACTIVE,
            KindStatus.BOOTSTRAP_COMPLETE,
        )

    def can_advance(self) -> bool:
        return not self.is_terminal() and self != KindStatus.BOOTSTRAP_COMPLETE


class ObstructionType(str, enum.Enum):
    STRUCTURAL = "structural"
    DEFINITIONAL = "definitional"
    COMPUTATIONAL = "computational"
    LOGICAL = "logical"
    EMPIRICAL = "empirical"
    BOUNDARY = "boundary"
    CATEGORICAL = "categorical"
    RELATIONAL = "relational"
    SEMANTIC = "semantic"
    ALGEBRAIC = "algebraic"
    SYNTACTIC = "syntactic"   # Obstructions arising from syntax/grammar constraints.

    def description(self) -> str:
        _descs = {
            "structural": "Obstructions arising from the shape or topology of a structure.",
            "definitional": "Obstructions where a concept lacks a well-formed definition.",
            "computational": "Obstructions related to computability, complexity, or decidability.",
            "logical": "Obstructions that stem from logical contradictions or inconsistencies.",
            "empirical": "Obstructions that arise from observed or experimental data.",
            "boundary": "Obstructions occurring at edge cases, limits, or singular points.",
            "categorical": "Obstructions involving category-theoretic constructs such as functors.",
            "relational": "Obstructions arising from incompatible relations or dependencies.",
        }
        return _descs[self.value]


@dataclass(frozen=True)
class ObstructionField:
    field_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    domain: str = ""
    obstruction_count: int = 0
    total_weight: float = 0.0
    obstruction_types: tuple = ()
    related_fields: tuple = ()
    created_at: str = field(default_factory=_now_iso)
    obstructions: tuple[str, ...] = ()
    semantic_density: float = 0.0
    coherence_score: float = 0.0

    def __post_init__(self) -> None:
        normalized_types = _normalize_obstruction_types(self.obstruction_types)
        if normalized_types != tuple(self.obstruction_types):
            object.__setattr__(self, "obstruction_types", normalized_types)
        if self.obstructions and not self.obstruction_count:
            object.__setattr__(self, "obstruction_count", len(self.obstructions))

    def weight_per_obstruction(self) -> float:
        return self.total_weight / max(1, self.obstruction_count)

    def has_obstruction_type(self, t: str) -> bool:
        return t in self.obstruction_types

    def merge(self, other) -> "ObstructionField":
        combined_types = tuple(dict.fromkeys(list(self.obstruction_types) + list(other.obstruction_types)))
        combined_related = tuple(dict.fromkeys(list(self.related_fields) + list(other.related_fields)))
        return replace(
            self,
            obstruction_count=self.obstruction_count + other.obstruction_count,
            total_weight=self.total_weight + other.total_weight,
            obstruction_types=combined_types,
            related_fields=combined_related,
        )

    def to_dict(self) -> dict:
        return {
            "field_id": self.field_id,
            "name": self.name,
            "domain": self.domain,
            "obstruction_count": self.obstruction_count,
            "total_weight": self.total_weight,
            "obstruction_types": list(self.obstruction_types),
            "related_fields": list(self.related_fields),
            "created_at": self.created_at,
            "obstructions": list(self.obstructions),
            "semantic_density": self.semantic_density,
            "coherence_score": self.coherence_score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ObstructionField":
        return cls(
            field_id=d.get("field_id", str(uuid.uuid4())),
            name=d.get("name", ""),
            domain=d.get("domain", ""),
            obstruction_count=int(d.get("obstruction_count", 0)),
            total_weight=float(d.get("total_weight", 0.0)),
            obstruction_types=tuple(d.get("obstruction_types", [])),
            related_fields=tuple(d.get("related_fields", [])),
            created_at=d.get("created_at", _now_iso()),
            obstructions=tuple(d.get("obstructions", [])),
            semantic_density=float(d.get("semantic_density", 0.0)),
            coherence_score=float(d.get("coherence_score", 0.0)),
        )

    def __str__(self) -> str:
        return f"ObstructionField({self.name!r}, {self.domain!r}, {self.obstruction_count} obstructions)"


@dataclass(frozen=True)
class KindPattern:
    pattern_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    pattern_type: str = ""
    frequency: int = 0
    confidence: float = 0.0
    supporting_obstructions: tuple = ()
    obstruction_types: tuple = ()
    field_ids: tuple = ()
    description: str = ""
    keywords: tuple = ()
    created_at: str = field(default_factory=_now_iso)
    signature: str = ""
    domains: tuple[str, ...] = ()
    generality_score: float = 0.0

    def __post_init__(self) -> None:
        if self.signature and not self.name:
            object.__setattr__(self, "name", self.signature)
        if self.generality_score and not self.confidence:
            object.__setattr__(self, "confidence", self.generality_score)
        normalized_types = _normalize_obstruction_types(self.obstruction_types)
        if normalized_types != tuple(self.obstruction_types):
            object.__setattr__(self, "obstruction_types", normalized_types)
        if self.domains and not self.field_ids:
            object.__setattr__(self, "field_ids", tuple(self.domains))
        if not self.keywords and self.name:
            object.__setattr__(self, "keywords", tuple(_tokenize(self.name))[:8])

    def is_significant(self, min_freq: int = 2, min_conf: float = 0.5) -> bool:
        return self.frequency >= min_freq and self.confidence >= min_conf

    def overlap(self, other: "KindPattern") -> float:
        a = frozenset(self.keywords)
        b = frozenset(other.keywords)
        return _jaccard(a, b)

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "pattern_type": self.pattern_type,
            "frequency": self.frequency,
            "confidence": self.confidence,
            "supporting_obstructions": list(self.supporting_obstructions),
            "obstruction_types": [t.value if hasattr(t, "value") else t for t in self.obstruction_types],
            "field_ids": list(self.field_ids),
            "description": self.description,
            "keywords": list(self.keywords),
            "created_at": self.created_at,
            "signature": self.signature or self.name,
            "domains": list(self.domains),
            "generality_score": self.generality_score if self.generality_score else self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KindPattern":
        return cls(
            pattern_id=d.get("pattern_id", str(uuid.uuid4())),
            name=d.get("name", ""),
            pattern_type=d.get("pattern_type", ""),
            frequency=int(d.get("frequency", 0)),
            confidence=float(d.get("confidence", 0.0)),
            supporting_obstructions=tuple(d.get("supporting_obstructions", [])),
            obstruction_types=tuple(ObstructionType(v) for v in d.get("obstruction_types", [])),
            field_ids=tuple(d.get("field_ids", [])),
            description=d.get("description", ""),
            keywords=tuple(d.get("keywords", [])),
            created_at=d.get("created_at", _now_iso()),
            signature=d.get("signature", d.get("name", "")),
            domains=tuple(d.get("domains", [])),
            generality_score=float(d.get("generality_score", d.get("confidence", 0.0))),
        )

    def __str__(self) -> str:
        return f"KindPattern({self.name!r}, type={self.pattern_type!r}, freq={self.frequency}, conf={self.confidence:.2f})"


@dataclass(frozen=True)
class KindCandidate:
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    pattern_ids: tuple = ()
    obstruction_types: tuple = ()
    field_ids: tuple = ()
    confidence: float = 0.0
    novelty: float = 0.0
    status: KindStatus = KindStatus.PROPOSED
    supporting_evidence: tuple = ()
    counter_evidence: tuple = ()
    definition_draft: str = ""
    examples: tuple = ()
    created_at: str = field(default_factory=_now_iso)
    obstruction_pattern: str = ""
    frequency: int = 0
    evidence_sources: tuple = ()

    def __post_init__(self) -> None:
        normalized_types = list(_normalize_obstruction_types(self.obstruction_types))
        if not normalized_types and self.obstruction_pattern:
            normalized_types.extend(
                _normalize_obstruction_types((self.obstruction_pattern,))
            )
        if normalized_types:
            object.__setattr__(
                self, "obstruction_types", tuple(dict.fromkeys(normalized_types))
            )
        if self.evidence_sources and not self.supporting_evidence:
            object.__setattr__(
                self, "supporting_evidence", tuple(self.evidence_sources)
            )

    def is_viable(self, min_confidence: float = 0.3) -> bool:
        return (
            self.confidence >= min_confidence
            and self.status not in (KindStatus.REJECTED, KindStatus.RETIRED)
        )

    def evidence_balance(self) -> float:
        total = len(self.supporting_evidence) + len(self.counter_evidence)
        return len(self.supporting_evidence) / max(1, total)

    def promote(self) -> "KindCandidate":
        if self.status == KindStatus.PROPOSED:
            return replace(self, status=KindStatus.CANDIDATE)
        if self.status == KindStatus.CANDIDATE:
            return replace(self, status=KindStatus.VALIDATED)
        raise ValueError(f"Cannot promote KindCandidate from status {self.status!r}")

    def reject(self, reason: str = "") -> "KindCandidate":
        new_counter = (*self.counter_evidence, reason) if reason else self.counter_evidence
        return replace(self, status=KindStatus.REJECTED, counter_evidence=new_counter)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "description": self.description,
            "pattern_ids": list(self.pattern_ids),
            "obstruction_types": [t.value if hasattr(t, "value") else t for t in self.obstruction_types],
            "field_ids": list(self.field_ids),
            "confidence": self.confidence,
            "novelty": self.novelty,
            "status": self.status.value,
            "supporting_evidence": list(self.supporting_evidence),
            "counter_evidence": list(self.counter_evidence),
            "definition_draft": self.definition_draft,
            "examples": list(self.examples),
            "created_at": self.created_at,
            "obstruction_pattern": self.obstruction_pattern,
            "frequency": self.frequency,
            "evidence_sources": list(self.evidence_sources),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KindCandidate":
        return cls(
            candidate_id=d.get("candidate_id", str(uuid.uuid4())),
            name=d.get("name", ""),
            description=d.get("description", ""),
            pattern_ids=tuple(d.get("pattern_ids", [])),
            obstruction_types=tuple(ObstructionType(v) for v in d.get("obstruction_types", [])),
            field_ids=tuple(d.get("field_ids", [])),
            confidence=float(d.get("confidence", 0.0)),
            novelty=float(d.get("novelty", 0.0)),
            status=KindStatus(d.get("status", KindStatus.PROPOSED.value)),
            supporting_evidence=tuple(d.get("supporting_evidence", [])),
            counter_evidence=tuple(d.get("counter_evidence", [])),
            definition_draft=d.get("definition_draft", ""),
            examples=tuple(d.get("examples", [])),
            created_at=d.get("created_at", _now_iso()),
            obstruction_pattern=d.get("obstruction_pattern", ""),
            frequency=int(d.get("frequency", 0)),
            evidence_sources=tuple(d.get("evidence_sources", [])),
        )

    def __str__(self) -> str:
        return f"KindCandidate({self.name!r}, status={self.status.value!r}, conf={self.confidence:.2f})"


@dataclass(frozen=True)
class KindBootstrapPlan:
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id: str = ""
    kind_name: str = ""
    steps: tuple = ()
    required_evidence: tuple = ()
    success_criteria: tuple = ()
    effort_estimate: float = 0.0
    estimated_effort: float = 0.0
    priority: float = 0.0
    target_kind: str = ""
    dependencies: tuple = ()
    status: KindStatus = KindStatus.BOOTSTRAP_PENDING
    created_at: str = field(default_factory=_now_iso)
    notes: str = ""

    def __post_init__(self) -> None:
        if self.target_kind and not self.kind_name:
            object.__setattr__(self, "kind_name", self.target_kind)
        if self.kind_name and not self.target_kind:
            object.__setattr__(self, "target_kind", self.kind_name)
        if self.estimated_effort and not self.effort_estimate:
            object.__setattr__(self, "effort_estimate", self.estimated_effort)
        if self.effort_estimate and not self.estimated_effort:
            object.__setattr__(self, "estimated_effort", self.effort_estimate)

    def estimated_completion_steps(self) -> int:
        if self.steps:
            return len(self.steps)
        return max(1, int(math.ceil(self.effort_estimate)))

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def is_ready(self) -> bool:
        return self.status == KindStatus.BOOTSTRAP_PENDING and len(self.dependencies) == 0

    def with_step(self, step: str) -> "KindBootstrapPlan":
        return replace(self, steps=(*self.steps, step))

    def start(self) -> "KindBootstrapPlan":
        if self.status != KindStatus.BOOTSTRAP_PENDING:
            raise ValueError(f"Cannot start plan in status {self.status!r}; expected BOOTSTRAP_PENDING.")
        return replace(self, status=KindStatus.BOOTSTRAP_ACTIVE)

    def complete(self) -> "KindBootstrapPlan":
        if self.status != KindStatus.BOOTSTRAP_ACTIVE:
            raise ValueError(f"Cannot complete plan in status {self.status!r}; expected BOOTSTRAP_ACTIVE.")
        return replace(self, status=KindStatus.BOOTSTRAP_COMPLETE)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "candidate_id": self.candidate_id,
            "kind_name": self.kind_name,
            "target_kind": self.target_kind or self.kind_name,
            "steps": list(self.steps),
            "required_evidence": list(self.required_evidence),
            "success_criteria": list(self.success_criteria),
            "effort_estimate": self.effort_estimate,
            "estimated_effort": self.estimated_effort or self.effort_estimate,
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "created_at": self.created_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KindBootstrapPlan":
        return cls(
            plan_id=d.get("plan_id", str(uuid.uuid4())),
            candidate_id=d.get("candidate_id", ""),
            kind_name=d.get("kind_name", d.get("target_kind", "")),
            steps=tuple(d.get("steps", [])),
            required_evidence=tuple(d.get("required_evidence", [])),
            success_criteria=tuple(d.get("success_criteria", [])),
            effort_estimate=float(
                d.get("effort_estimate", d.get("estimated_effort", 0.0))
            ),
            estimated_effort=float(
                d.get("estimated_effort", d.get("effort_estimate", 0.0))
            ),
            priority=float(d.get("priority", 0.0)),
            target_kind=d.get("target_kind", d.get("kind_name", "")),
            dependencies=tuple(d.get("dependencies", [])),
            status=KindStatus(d.get("status", KindStatus.BOOTSTRAP_PENDING.value)),
            created_at=d.get("created_at", _now_iso()),
            notes=d.get("notes", ""),
        )


@dataclass(frozen=True)
class NewKind:
    kind_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    formal_name: str = ""
    definition: str = ""
    formal_definition: str = ""
    description: str = ""
    domain: str = ""
    examples: tuple = ()
    counter_examples: tuple = ()
    theorems: tuple = ()
    related_kinds: tuple = ()
    discovery_path: tuple = ()
    obstruction_types: tuple = ()
    status: KindStatus = KindStatus.CANDIDATE
    confidence: float = 0.0
    novelty: float = 0.0
    candidate_id: str = ""
    plan_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    validated_at: str = ""

    def __post_init__(self) -> None:
        definition = self.definition or self.formal_definition
        formal_definition = self.formal_definition or self.definition

        if definition and not self.definition:
            object.__setattr__(self, "definition", definition)
        if formal_definition and not self.formal_definition:
            object.__setattr__(self, "formal_definition", formal_definition)
        if not self.formal_name and self.name:
            object.__setattr__(self, "formal_name", self.name)

    def is_validated(self) -> bool:
        return self.status == KindStatus.VALIDATED

    def with_status(self, status: KindStatus) -> "NewKind":
        updates = {"status": status}
        if status == KindStatus.VALIDATED and not self.validated_at:
            updates["validated_at"] = _now_iso()
        return replace(self, **updates)

    def with_confidence(self, confidence: float) -> "NewKind":
        return replace(self, confidence=_clamp(float(confidence), 0.0, 1.0))

    def summary(self) -> str:
        defn_preview = (self.definition[:80] + "...") if len(self.definition) > 80 else self.definition
        lines = [
            f"Name        : {self.name}",
            f"Formal name : {self.formal_name}",
            f"Domain      : {self.domain}",
            f"Definition  : {defn_preview}",
            f"Confidence  : {self.confidence:.2f}",
            f"Status      : {self.status.value}",
        ]
        return "\n".join(lines)

    def validate(self) -> "NewKind":
        if self.confidence < 0.5:
            raise ValueError(f"Cannot validate NewKind {self.name!r}: confidence {self.confidence:.2f} is below 0.5.")
        return replace(self, status=KindStatus.VALIDATED, validated_at=_now_iso())

    def retire(self) -> "NewKind":
        return replace(self, status=KindStatus.RETIRED)

    def to_dict(self) -> dict:
        return {
            "kind_id": self.kind_id,
            "name": self.name,
            "formal_name": self.formal_name,
            "definition": self.definition,
            "formal_definition": self.formal_definition or self.definition,
            "description": self.description,
            "domain": self.domain,
            "examples": list(self.examples),
            "counter_examples": list(self.counter_examples),
            "theorems": list(self.theorems),
            "related_kinds": list(self.related_kinds),
            "discovery_path": list(self.discovery_path),
            "obstruction_types": [t.value if hasattr(t, "value") else t for t in self.obstruction_types],
            "status": self.status.value,
            "confidence": self.confidence,
            "novelty": self.novelty,
            "candidate_id": self.candidate_id,
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "validated_at": self.validated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NewKind":
        return cls(
            kind_id=d.get("kind_id", str(uuid.uuid4())),
            name=d.get("name", ""),
            formal_name=d.get("formal_name", ""),
            definition=d.get("definition", ""),
            formal_definition=d.get("formal_definition", d.get("definition", "")),
            description=d.get("description", ""),
            domain=d.get("domain", ""),
            examples=tuple(d.get("examples", [])),
            counter_examples=tuple(d.get("counter_examples", [])),
            theorems=tuple(d.get("theorems", [])),
            related_kinds=tuple(d.get("related_kinds", [])),
            discovery_path=tuple(d.get("discovery_path", [])),
            obstruction_types=tuple(ObstructionType(v) for v in d.get("obstruction_types", [])),
            status=KindStatus(d.get("status", KindStatus.CANDIDATE.value)),
            confidence=float(d.get("confidence", 0.0)),
            novelty=float(d.get("novelty", 0.0)),
            candidate_id=d.get("candidate_id", ""),
            plan_id=d.get("plan_id", ""),
            created_at=d.get("created_at", _now_iso()),
            validated_at=d.get("validated_at", ""),
        )

    def __str__(self) -> str:
        return f"NewKind({self.name!r}, formal={self.formal_name!r}, status={self.status.value!r}, conf={self.confidence:.2f})"
