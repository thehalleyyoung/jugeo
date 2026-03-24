"""Data models for Flask app generation — all stdlib, no Flask imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ResponseType(str, Enum):
    TEMPLATE = "template"
    JSON = "json"
    REDIRECT = "redirect"
    FORM = "form"


class ColumnType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TEXT = "text"
    DATE = "date"
    DATETIME = "datetime"
    JSON = "json"


class FormFieldType(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    PASSWORD = "password"
    NUMBER = "number"
    TEXTAREA = "textarea"
    SELECT = "select"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    FILE = "file"
    HIDDEN = "hidden"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enum_val(v: Any) -> Any:
    """Return the .value of an Enum, or the object itself."""
    return v.value if isinstance(v, Enum) else v


# ---------------------------------------------------------------------------
# Column / Model
# ---------------------------------------------------------------------------

@dataclass
class ColumnSpec:
    name: str
    type: ColumnType = ColumnType.STRING
    nullable: bool = True
    default: Any = None
    primary_key: bool = False
    unique: bool = False
    foreign_key: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": _enum_val(self.type),
            "nullable": self.nullable,
            "default": self.default,
            "primary_key": self.primary_key,
            "unique": self.unique,
            "foreign_key": self.foreign_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnSpec":
        d = dict(d)
        if "type" in d:
            d["type"] = ColumnType(d["type"])
        return cls(**d)


@dataclass
class ModelSpec:
    name: str
    table_name: str = ""
    columns: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    indexes: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.table_name:
            self.table_name = self.name.lower() + "s"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "table_name": self.table_name,
            "columns": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.columns],
            "relationships": list(self.relationships),
            "indexes": list(self.indexes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelSpec":
        d = dict(d)
        if "columns" in d:
            d["columns"] = [
                ColumnSpec.from_dict(c) if isinstance(c, dict) else c
                for c in d["columns"]
            ]
        return cls(**d)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@dataclass
class RouteSpec:
    url: str
    methods: list = field(default_factory=lambda: ["GET"])
    handler_name: str = ""
    template: str = ""
    response_type: ResponseType = ResponseType.TEMPLATE
    auth_required: bool = False
    params: list = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "methods": list(self.methods),
            "handler_name": self.handler_name,
            "template": self.template,
            "response_type": _enum_val(self.response_type),
            "auth_required": self.auth_required,
            "params": list(self.params),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RouteSpec":
        d = dict(d)
        if "response_type" in d:
            d["response_type"] = ResponseType(d["response_type"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Template / Static
# ---------------------------------------------------------------------------

@dataclass
class TemplateSpec:
    name: str
    extends: str = "base.html"
    blocks: dict = field(default_factory=dict)
    variables: list = field(default_factory=list)
    macros: list = field(default_factory=list)
    includes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "extends": self.extends,
            "blocks": dict(self.blocks),
            "variables": list(self.variables),
            "macros": list(self.macros),
            "includes": list(self.includes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TemplateSpec":
        return cls(**d)


@dataclass
class StaticFileSpec:
    path: str
    content_type: str = "text/plain"
    content: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "content_type": self.content_type,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StaticFileSpec":
        return cls(**d)


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

@dataclass
class BlueprintSpec:
    name: str
    url_prefix: str = ""
    routes: list = field(default_factory=list)
    models: list = field(default_factory=list)
    templates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url_prefix": self.url_prefix,
            "routes": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.routes],
            "models": [m.to_dict() if hasattr(m, "to_dict") else m for m in self.models],
            "templates": [t.to_dict() if hasattr(t, "to_dict") else t for t in self.templates],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BlueprintSpec":
        d = dict(d)
        if "routes" in d:
            d["routes"] = [RouteSpec.from_dict(r) if isinstance(r, dict) else r for r in d["routes"]]
        if "models" in d:
            d["models"] = [ModelSpec.from_dict(m) if isinstance(m, dict) else m for m in d["models"]]
        if "templates" in d:
            d["templates"] = [TemplateSpec.from_dict(t) if isinstance(t, dict) else t for t in d["templates"]]
        return cls(**d)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ConfigSpec:
    secret_key: str = ""
    database_url: str = "sqlite:///app.db"
    debug: bool = False
    custom_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "secret_key": self.secret_key,
            "database_url": self.database_url,
            "debug": self.debug,
            "custom_config": dict(self.custom_config),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConfigSpec":
        return cls(**d)


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

@dataclass
class FormFieldSpec:
    name: str
    field_type: FormFieldType = FormFieldType.TEXT
    label: str = ""
    required: bool = False
    validators: list = field(default_factory=list)
    choices: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "field_type": _enum_val(self.field_type),
            "label": self.label,
            "required": self.required,
            "validators": list(self.validators),
            "choices": list(self.choices),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FormFieldSpec":
        d = dict(d)
        if "field_type" in d:
            d["field_type"] = FormFieldType(d["field_type"])
        return cls(**d)


@dataclass
class FormSpec:
    name: str
    fields: list = field(default_factory=list)
    action_url: str = ""
    method: str = "POST"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fields": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.fields],
            "action_url": self.action_url,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FormSpec":
        d = dict(d)
        if "fields" in d:
            d["fields"] = [FormFieldSpec.from_dict(f) if isinstance(f, dict) else f for f in d["fields"]]
        return cls(**d)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@dataclass
class TestCaseSpec:
    name: str
    method: str = "GET"
    url: str = "/"
    expected_status: int = 200
    expected_content: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "method": self.method,
            "url": self.url,
            "expected_status": self.expected_status,
            "expected_content": self.expected_content,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TestCaseSpec":
        return cls(**d)


@dataclass
class TestSpec:
    name: str
    test_cases: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "test_cases": [t.to_dict() if hasattr(t, "to_dict") else t for t in self.test_cases],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TestSpec":
        d = dict(d)
        if "test_cases" in d:
            d["test_cases"] = [
                TestCaseSpec.from_dict(t) if isinstance(t, dict) else t
                for t in d["test_cases"]
            ]
        return cls(**d)


# ---------------------------------------------------------------------------
# App / Generation result
# ---------------------------------------------------------------------------

@dataclass
class AppSpec:
    name: str
    description: str = ""
    port: int = 5000
    routes: list = field(default_factory=list)
    models: list = field(default_factory=list)
    templates: list = field(default_factory=list)
    static_files: list = field(default_factory=list)
    blueprints: list = field(default_factory=list)
    config: ConfigSpec = field(default_factory=ConfigSpec)
    dependencies: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "port": self.port,
            "routes": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.routes],
            "models": [m.to_dict() if hasattr(m, "to_dict") else m for m in self.models],
            "templates": [t.to_dict() if hasattr(t, "to_dict") else t for t in self.templates],
            "static_files": [s.to_dict() if hasattr(s, "to_dict") else s for s in self.static_files],
            "blueprints": [b.to_dict() if hasattr(b, "to_dict") else b for b in self.blueprints],
            "config": self.config.to_dict() if hasattr(self.config, "to_dict") else self.config,
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppSpec":
        d = dict(d)
        if "routes" in d:
            d["routes"] = [RouteSpec.from_dict(r) if isinstance(r, dict) else r for r in d["routes"]]
        if "models" in d:
            d["models"] = [ModelSpec.from_dict(m) if isinstance(m, dict) else m for m in d["models"]]
        if "templates" in d:
            d["templates"] = [TemplateSpec.from_dict(t) if isinstance(t, dict) else t for t in d["templates"]]
        if "static_files" in d:
            d["static_files"] = [StaticFileSpec.from_dict(s) if isinstance(s, dict) else s for s in d["static_files"]]
        if "blueprints" in d:
            d["blueprints"] = [BlueprintSpec.from_dict(b) if isinstance(b, dict) else b for b in d["blueprints"]]
        if "config" in d and isinstance(d["config"], dict):
            d["config"] = ConfigSpec.from_dict(d["config"])
        return cls(**d)


@dataclass
class GenerationResult:
    output_dir: str
    files_created: list = field(default_factory=list)
    app_spec: Optional[AppSpec] = None
    verification_results: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "output_dir": self.output_dir,
            "files_created": list(self.files_created),
            "app_spec": self.app_spec.to_dict() if self.app_spec else None,
            "verification_results": dict(self.verification_results),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GenerationResult":
        d = dict(d)
        if d.get("app_spec") and isinstance(d["app_spec"], dict):
            d["app_spec"] = AppSpec.from_dict(d["app_spec"])
        return cls(**d)
