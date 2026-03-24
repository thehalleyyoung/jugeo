"""Data models for the web application site."""
from __future__ import annotations
from dataclasses import dataclass, field
from .coordinate_kinds import WebCoordinateKind
from .morphism_kinds import CrossLanguageMorphismKind


@dataclass
class WebCoordinate:
    id: str
    kind: WebCoordinateKind
    name: str
    file_path: str = ""
    line_number: int = 0
    language_layer: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.language_layer:
            self.language_layer = self.kind.language_layer()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "language_layer": self.language_layer,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WebCoordinate:
        return cls(
            id=data["id"],
            kind=WebCoordinateKind(data["kind"]),
            name=data["name"],
            file_path=data.get("file_path", ""),
            line_number=data.get("line_number", 0),
            language_layer=data.get("language_layer", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class WebMorphism:
    id: str
    source_id: str
    target_id: str
    kind: CrossLanguageMorphismKind
    label: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind.value,
            "label": self.label,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WebMorphism:
        return cls(
            id=data["id"],
            source_id=data["source_id"],
            target_id=data["target_id"],
            kind=CrossLanguageMorphismKind(data["kind"]),
            label=data.get("label", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class WebCoveringFamily:
    id: str
    base_id: str
    member_ids: list[str] = field(default_factory=list)
    label: str = ""
    lifecycle_stage: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "base_id": self.base_id,
            "member_ids": list(self.member_ids),
            "label": self.label,
            "lifecycle_stage": self.lifecycle_stage,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WebCoveringFamily:
        return cls(
            id=data["id"],
            base_id=data["base_id"],
            member_ids=data.get("member_ids", []),
            label=data.get("label", ""),
            lifecycle_stage=data.get("lifecycle_stage", ""),
        )


@dataclass
class RequestLifecycle:
    id: str
    route_url: str
    method: str = "GET"
    stages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "route_url": self.route_url,
            "method": self.method,
            "stages": list(self.stages),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RequestLifecycle:
        return cls(
            id=data["id"],
            route_url=data["route_url"],
            method=data.get("method", "GET"),
            stages=data.get("stages", []),
        )


@dataclass
class DescentCondition:
    id: str
    overlap_name: str
    description: str
    left_coordinate_id: str
    right_coordinate_id: str
    condition_type: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "overlap_name": self.overlap_name,
            "description": self.description,
            "left_coordinate_id": self.left_coordinate_id,
            "right_coordinate_id": self.right_coordinate_id,
            "condition_type": self.condition_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DescentCondition:
        return cls(
            id=data["id"],
            overlap_name=data["overlap_name"],
            description=data["description"],
            left_coordinate_id=data["left_coordinate_id"],
            right_coordinate_id=data["right_coordinate_id"],
            condition_type=data["condition_type"],
        )


@dataclass
class DescentViolation:
    id: str
    condition_id: str
    message: str
    severity: str = "error"
    repair_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "condition_id": self.condition_id,
            "message": self.message,
            "severity": self.severity,
            "repair_hint": self.repair_hint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DescentViolation:
        return cls(
            id=data["id"],
            condition_id=data["condition_id"],
            message=data["message"],
            severity=data.get("severity", "error"),
            repair_hint=data.get("repair_hint", ""),
        )


@dataclass
class LanguageLayer:
    name: str
    trust_floor: str
    trust_ceiling: str
    is_server_side: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "trust_floor": self.trust_floor,
            "trust_ceiling": self.trust_ceiling,
            "is_server_side": self.is_server_side,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LanguageLayer:
        return cls(
            name=data["name"],
            trust_floor=data["trust_floor"],
            trust_ceiling=data["trust_ceiling"],
            is_server_side=data["is_server_side"],
        )


LANGUAGE_LAYERS: dict[str, LanguageLayer] = {
    "python": LanguageLayer("python", "middleware_enforced", "db_constraint_enforced", True),
    "template": LanguageLayer("template", "template_type_checked", "server_validated", True),
    "javascript": LanguageLayer("javascript", "client_validated", "js_type_checked", False),
    "css": LanguageLayer("css", "css_linted", "browser_tested", False),
    "html": LanguageLayer("html", "browser_tested", "browser_tested", False),
    "database": LanguageLayer("database", "orm_type_checked", "db_constraint_enforced", True),
    "http": LanguageLayer("http", "schema_validated", "api_contract_tested", True),
    "auth": LanguageLayer("auth", "server_validated", "db_constraint_enforced", True),
}
