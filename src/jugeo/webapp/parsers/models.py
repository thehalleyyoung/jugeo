"""
Data models for the JuGeo webapp parsers.

All models use @dataclass with to_dict / from_dict for serialisation.
Enums use the (str, Enum) pattern so they serialise as plain strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CoordinateKind(str, Enum):
    """Kind of parsed coordinate (code construct)."""
    ROUTE_HANDLER = "route_handler"
    VIEW_FUNCTION = "view_function"
    MODEL_CLASS = "model_class"
    FORM_CLASS = "form_class"
    MIDDLEWARE = "middleware"
    BLUEPRINT = "blueprint"
    ERROR_HANDLER = "error_handler"
    AUTH_DECORATOR = "auth_decorator"

    TEMPLATE_BLOCK = "template_block"
    TEMPLATE_MACRO = "template_macro"
    TEMPLATE_VARIABLE = "template_variable"
    TEMPLATE_FILTER = "template_filter"
    CONDITIONAL_RENDER = "conditional_render"

    HTML_ELEMENT = "html_element"
    HTML_ATTRIBUTE = "html_attribute"
    HTML_FORM = "html_form"
    HTML_LINK = "html_link"

    JS_FUNCTION = "js_function"
    JS_FETCH_CALL = "js_fetch_call"
    JS_DOM_MANIPULATION = "js_dom_manipulation"
    JS_EVENT_HANDLER = "js_event_handler"
    JS_MODULE = "js_module"
    JS_STATE_VARIABLE = "js_state_variable"

    CSS_RULE = "css_rule"
    CSS_PROPERTY = "css_property"
    CSS_MEDIA_QUERY = "css_media_query"
    CSS_ANIMATION = "css_animation"
    CSS_STYLESHEET = "css_stylesheet"

    DB_TABLE = "db_table"
    DB_COLUMN = "db_column"
    DB_CONSTRAINT = "db_constraint"
    DB_INDEX = "db_index"


class ReferenceType(str, Enum):
    """Type of cross-language or intra-language reference."""
    RENDERS_TEMPLATE = "renders_template"
    URL_FOR = "url_for"
    CONFIG_ACCESS = "config_access"
    SESSION_ACCESS = "session_access"

    TEMPLATE_EXTENDS = "template_extends"
    TEMPLATE_INCLUDES = "template_includes"
    TEMPLATE_URL_FOR = "template_url_for"
    STATIC_REFERENCE = "static_reference"

    API_CALL = "api_call"
    DOM_ACCESS = "dom_access"
    CLASS_MANIPULATION = "class_manipulation"
    STYLE_MUTATION = "style_mutation"
    MODULE_IMPORT = "module_import"

    FK_REFERENCE = "fk_reference"

    HTML_SCRIPT = "html_script"
    HTML_STYLESHEET = "html_stylesheet"
    HTML_HREF = "html_href"


class ErrorSeverity(str, Enum):
    """Severity of a parse error."""
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Language(str, Enum):
    """Source language of a parsed file."""
    PYTHON = "python"
    JINJA2 = "jinja2"
    JAVASCRIPT = "javascript"
    CSS = "css"
    HTML = "html"
    SQL = "sql"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParsedCoordinate:
    """A single code construct extracted from source."""
    id: str
    kind: CoordinateKind
    name: str
    file_path: str
    line_number: int
    end_line: int
    language: Language
    metadata: dict = field(default_factory=dict)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "end_line": self.end_line,
            "language": self.language.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedCoordinate:
        return cls(
            id=data["id"],
            kind=CoordinateKind(data["kind"]),
            name=data["name"],
            file_path=data["file_path"],
            line_number=data["line_number"],
            end_line=data["end_line"],
            language=Language(data["language"]),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ParsedReference:
    """A reference from one coordinate to another (possibly cross-language)."""
    source_id: str
    target_name: str
    reference_type: ReferenceType
    file_path: str
    line_number: int
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_name": self.target_name,
            "reference_type": self.reference_type.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedReference:
        return cls(
            source_id=data["source_id"],
            target_name=data["target_name"],
            reference_type=ReferenceType(data["reference_type"]),
            file_path=data["file_path"],
            line_number=data["line_number"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class ParseError:
    """An error encountered while parsing a file."""
    file_path: str
    line_number: int
    message: str
    severity: ErrorSeverity

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "message": self.message,
            "severity": self.severity.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParseError:
        return cls(
            file_path=data["file_path"],
            line_number=data["line_number"],
            message=data["message"],
            severity=ErrorSeverity(data["severity"]),
        )


@dataclass
class ParseResult:
    """Result of parsing a single source file."""
    file_path: str
    language: Language
    coordinates: list[ParsedCoordinate] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    parse_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language.value,
            "coordinates": [c.to_dict() for c in self.coordinates],
            "references": [r.to_dict() for r in self.references],
            "errors": [e.to_dict() for e in self.errors],
            "parse_time_ms": self.parse_time_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParseResult:
        return cls(
            file_path=data["file_path"],
            language=Language(data["language"]),
            coordinates=[ParsedCoordinate.from_dict(c) for c in data.get("coordinates", [])],
            references=[ParsedReference.from_dict(r) for r in data.get("references", [])],
            errors=[ParseError.from_dict(e) for e in data.get("errors", [])],
            parse_time_ms=data.get("parse_time_ms", 0.0),
        )


@dataclass
class ProjectParseResult:
    """Aggregated result of parsing an entire project."""
    files: list[ParseResult] = field(default_factory=list)
    all_coordinates: list[ParsedCoordinate] = field(default_factory=list)
    all_references: list[ParsedReference] = field(default_factory=list)
    cross_language_refs: list[ParsedReference] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [f.to_dict() for f in self.files],
            "all_coordinates": [c.to_dict() for c in self.all_coordinates],
            "all_references": [r.to_dict() for r in self.all_references],
            "cross_language_refs": [r.to_dict() for r in self.cross_language_refs],
            "errors": [e.to_dict() for e in self.errors],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectParseResult:
        return cls(
            files=[ParseResult.from_dict(f) for f in data.get("files", [])],
            all_coordinates=[ParsedCoordinate.from_dict(c) for c in data.get("all_coordinates", [])],
            all_references=[ParsedReference.from_dict(r) for r in data.get("all_references", [])],
            cross_language_refs=[ParsedReference.from_dict(r) for r in data.get("cross_language_refs", [])],
            errors=[ParseError.from_dict(e) for e in data.get("errors", [])],
        )
