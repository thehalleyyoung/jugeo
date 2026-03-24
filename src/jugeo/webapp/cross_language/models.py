"""
Data models for cross-language analysis.

These model the cohomology obstructions — Čech H¹ cocycles from §2.5 —
that arise at language-layer boundaries in web applications.

All models use @dataclass with to_dict / from_dict for serialisation.
Enums use the (str, Enum) pattern so they serialise as plain strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


__all__ = [
    "OverlapKind",
    "OverlapCondition",
    "OverlapViolation",
    "CrossReference",
    "MorphismEvidence",
    "DescentReport",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OverlapKind(str, Enum):
    """
    The ten overlap conditions between web-application language layers.

    Each value names a pair of layers whose coordinate systems must agree
    on the overlap (the sheaf condition).  A mismatch is a Čech 1-cocycle.
    """
    ROUTE_TEMPLATE = "route_template"
    """Flask route context ↔ Jinja2 template variables."""

    ROUTE_JS_FETCH = "route_js_fetch"
    """Flask JSON response shape ↔ JS fetch / destructuring."""

    MODEL_DB_SCHEMA = "model_db_schema"
    """SQLAlchemy model columns ↔ DDL / migration schema."""

    JS_DOM_HTML = "js_dom_html"
    """JS getElementById / querySelector ↔ HTML id attributes."""

    JS_CLASS_CSS = "js_class_css"
    """JS classList.add / toggle ↔ CSS class definitions."""

    FORM_ROUTE = "form_route"
    """HTML form action + fields ↔ Flask route pattern + args."""

    TEMPLATE_CSS = "template_css"
    """Jinja2 template class usage ↔ CSS class definitions."""

    AUTH_SESSION = "auth_session"
    """@login_required decorator ↔ session['user_id'] checks."""

    DB_CONSTRAINT_HANDLER = "db_constraint_handler"
    """NOT NULL / UNIQUE constraints ↔ handler null/duplicate checks."""

    ERROR_HANDLER_JS = "error_handler_js"
    """Server errorhandler status codes ↔ client-side JS catch handling."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OverlapCondition:
    """
    A concrete overlap condition between two language layers.

    Represents a single Čech-nerve intersection where two coordinate
    patches must agree.  ``left_coordinates`` and ``right_coordinates``
    list the specific identifiers that must match.
    """
    id: str
    kind: OverlapKind
    left_layer: str
    right_layer: str
    description: str
    left_coordinates: list[str] = field(default_factory=list)
    right_coordinates: list[str] = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "left_layer": self.left_layer,
            "right_layer": self.right_layer,
            "description": self.description,
            "left_coordinates": list(self.left_coordinates),
            "right_coordinates": list(self.right_coordinates),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OverlapCondition:
        return cls(
            id=data["id"],
            kind=OverlapKind(data["kind"]),
            left_layer=data["left_layer"],
            right_layer=data["right_layer"],
            description=data["description"],
            left_coordinates=data.get("left_coordinates", []),
            right_coordinates=data.get("right_coordinates", []),
        )


@dataclass
class OverlapViolation:
    """
    A detected violation of an overlap condition.

    When a cocycle is non-trivial the sheaf condition fails;
    ``repair_hint`` suggests how to trivialise it.
    """
    id: str
    condition_id: str
    kind: OverlapKind
    message: str
    severity: str
    left_detail: str
    right_detail: str
    repair_hint: str
    file_path: str = ""
    line_number: int = 0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "condition_id": self.condition_id,
            "kind": self.kind.value,
            "message": self.message,
            "severity": self.severity,
            "left_detail": self.left_detail,
            "right_detail": self.right_detail,
            "repair_hint": self.repair_hint,
            "file_path": self.file_path,
            "line_number": self.line_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OverlapViolation:
        return cls(
            id=data["id"],
            condition_id=data["condition_id"],
            kind=OverlapKind(data["kind"]),
            message=data["message"],
            severity=data["severity"],
            left_detail=data["left_detail"],
            right_detail=data["right_detail"],
            repair_hint=data["repair_hint"],
            file_path=data.get("file_path", ""),
            line_number=data.get("line_number", 0),
        )


@dataclass
class CrossReference:
    """
    A resolved (or unresolved) cross-language reference.

    Tracks a name flowing from one language layer to another, e.g. a
    Python render_template kwarg used as ``{{ var }}`` in Jinja2.
    """
    source_file: str
    source_line: int
    source_layer: str
    target_name: str
    target_layer: str
    reference_type: str
    resolved: bool
    resolution_target: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_layer": self.source_layer,
            "target_name": self.target_name,
            "target_layer": self.target_layer,
            "reference_type": self.reference_type,
            "resolved": self.resolved,
            "resolution_target": self.resolution_target,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrossReference:
        return cls(
            source_file=data["source_file"],
            source_line=data["source_line"],
            source_layer=data["source_layer"],
            target_name=data["target_name"],
            target_layer=data["target_layer"],
            reference_type=data["reference_type"],
            resolved=data["resolved"],
            resolution_target=data.get("resolution_target", ""),
        )


@dataclass
class MorphismEvidence:
    """
    Evidence that a cross-language morphism is well-defined.

    Captures *why* we believe a particular correspondence holds —
    the trust level and the concrete details backing the claim.
    """
    morphism_id: str
    source_layer: str
    target_layer: str
    evidence_type: str
    trust_level: str
    details: dict = field(default_factory=dict)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "morphism_id": self.morphism_id,
            "source_layer": self.source_layer,
            "target_layer": self.target_layer,
            "evidence_type": self.evidence_type,
            "trust_level": self.trust_level,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MorphismEvidence:
        return cls(
            morphism_id=data["morphism_id"],
            source_layer=data["source_layer"],
            target_layer=data["target_layer"],
            evidence_type=data["evidence_type"],
            trust_level=data["trust_level"],
            details=data.get("details", {}),
        )


@dataclass
class DescentReport:
    """
    Full report from a cross-language descent analysis.

    ``coverage_score`` ∈ [0, 1] estimates what fraction of inter-layer
    interfaces are covered by resolved references.
    ``layer_connectivity`` maps each layer to the layers it connects to.
    """
    violations: list[OverlapViolation] = field(default_factory=list)
    cross_references: list[CrossReference] = field(default_factory=list)
    coverage_score: float = 0.0
    layer_connectivity: dict[str, list[str]] = field(default_factory=dict)
    summary: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "violations": [v.to_dict() for v in self.violations],
            "cross_references": [r.to_dict() for r in self.cross_references],
            "coverage_score": self.coverage_score,
            "layer_connectivity": {
                k: list(v) for k, v in self.layer_connectivity.items()
            },
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DescentReport:
        return cls(
            violations=[
                OverlapViolation.from_dict(v)
                for v in data.get("violations", [])
            ],
            cross_references=[
                CrossReference.from_dict(r)
                for r in data.get("cross_references", [])
            ],
            coverage_score=data.get("coverage_score", 0.0),
            layer_connectivity=data.get("layer_connectivity", {}),
            summary=data.get("summary", ""),
        )

    # -- helpers -------------------------------------------------------------

    @property
    def error_count(self) -> int:
        """Number of violations with severity 'error'."""
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        """Number of violations with severity 'warning'."""
        return sum(1 for v in self.violations if v.severity == "warning")

    @property
    def unresolved_references(self) -> list[CrossReference]:
        """Cross-references that could not be resolved."""
        return [r for r in self.cross_references if not r.resolved]

    def violations_by_kind(self) -> dict[str, list[OverlapViolation]]:
        """Group violations by their OverlapKind value."""
        result: dict[str, list[OverlapViolation]] = {}
        for v in self.violations:
            result.setdefault(v.kind.value, []).append(v)
        return result

    def layers_involved(self) -> set[str]:
        """Set of all language layers mentioned in cross-references."""
        layers: set[str] = set()
        for ref in self.cross_references:
            layers.add(ref.source_layer)
            layers.add(ref.target_layer)
        return layers
