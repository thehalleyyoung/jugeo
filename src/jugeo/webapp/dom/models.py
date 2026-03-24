"""Data models for the DOM theory module.

The DOM is a presheaf on the category of CSS selectors.  Each CSS selector
defines an "open set" of DOM nodes.  CSS rules are local sections; the
cascade glues them into a global computed style.  Obstructions to gluing
correspond to CSS bugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DOMNodeKind(str, Enum):
    """Kind of DOM node."""
    ELEMENT = "element"
    TEXT = "text"
    COMMENT = "comment"
    DOCUMENT = "document"
    FRAGMENT = "fragment"


class CSSPropertyKind(str, Enum):
    """Well-known CSS property names."""
    DISPLAY = "display"
    POSITION = "position"
    COLOR = "color"
    FONT_SIZE = "font-size"
    FONT_FAMILY = "font-family"
    FONT_WEIGHT = "font-weight"
    LINE_HEIGHT = "line-height"
    MARGIN = "margin"
    MARGIN_TOP = "margin-top"
    MARGIN_RIGHT = "margin-right"
    MARGIN_BOTTOM = "margin-bottom"
    MARGIN_LEFT = "margin-left"
    PADDING = "padding"
    PADDING_TOP = "padding-top"
    PADDING_RIGHT = "padding-right"
    PADDING_BOTTOM = "padding-bottom"
    PADDING_LEFT = "padding-left"
    WIDTH = "width"
    HEIGHT = "height"
    MAX_WIDTH = "max-width"
    MAX_HEIGHT = "max-height"
    MIN_WIDTH = "min-width"
    MIN_HEIGHT = "min-height"
    FLEX_DIRECTION = "flex-direction"
    FLEX_WRAP = "flex-wrap"
    FLEX_GROW = "flex-grow"
    GRID_TEMPLATE = "grid-template"
    GRID_TEMPLATE_COLUMNS = "grid-template-columns"
    GRID_TEMPLATE_ROWS = "grid-template-rows"
    Z_INDEX = "z-index"
    OVERFLOW = "overflow"
    OPACITY = "opacity"
    TRANSFORM = "transform"
    TRANSITION = "transition"
    BACKGROUND_COLOR = "background-color"
    BACKGROUND = "background"
    BORDER = "border"
    BORDER_RADIUS = "border-radius"
    TEXT_ALIGN = "text-align"
    VISIBILITY = "visibility"
    CURSOR = "cursor"


class CSSValueType(str, Enum):
    """Type of a CSS value token."""
    KEYWORD = "keyword"
    LENGTH = "length"
    PERCENTAGE = "percentage"
    COLOR_VALUE = "color"
    NUMBER = "number"
    CALC = "calc"
    URL = "url"
    INHERIT = "inherit"
    INITIAL = "initial"
    UNSET = "unset"


class CascadeObstructionKind(str, Enum):
    """Kind of obstruction encountered during cascade descent."""
    SPECIFICITY_CONFLICT = "specificity_conflict"
    INHERITANCE_GAP = "inheritance_gap"
    CASCADE_LEAK = "cascade_leak"
    MEDIA_QUERY_DISCONTINUITY = "media_query_discontinuity"
    ZINDEX_CONFUSION = "zindex_confusion"
    SELECTOR_UNREACHABLE = "selector_unreachable"


class ObstructionSeverity(str, Enum):
    """Severity of a cascade obstruction."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Specificity:
    """CSS selector specificity as a triple (id, class, element).

    Comparison is lexicographic: id_count is most significant.
    """

    id_count: int = 0
    class_count: int = 0
    element_count: int = 0

    # -- comparison (lexicographic) ------------------------------------------

    def _as_tuple(self) -> tuple[int, int, int]:
        return (self.id_count, self.class_count, self.element_count)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Specificity):
            return NotImplemented
        return self._as_tuple() == other._as_tuple()

    def __lt__(self, other: Specificity) -> bool:
        if not isinstance(other, Specificity):
            return NotImplemented
        return self._as_tuple() < other._as_tuple()

    def __le__(self, other: Specificity) -> bool:
        if not isinstance(other, Specificity):
            return NotImplemented
        return self._as_tuple() <= other._as_tuple()

    def __gt__(self, other: Specificity) -> bool:
        if not isinstance(other, Specificity):
            return NotImplemented
        return self._as_tuple() > other._as_tuple()

    def __ge__(self, other: Specificity) -> bool:
        if not isinstance(other, Specificity):
            return NotImplemented
        return self._as_tuple() >= other._as_tuple()

    def __hash__(self) -> int:
        return hash(self._as_tuple())

    # -- arithmetic ----------------------------------------------------------

    def __add__(self, other: Specificity) -> Specificity:
        if not isinstance(other, Specificity):
            return NotImplemented
        return Specificity(
            self.id_count + other.id_count,
            self.class_count + other.class_count,
            self.element_count + other.element_count,
        )

    # -- repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return f"({self.id_count},{self.class_count},{self.element_count})"

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id_count": self.id_count,
            "class_count": self.class_count,
            "element_count": self.element_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Specificity:
        return cls(
            id_count=data.get("id_count", 0),
            class_count=data.get("class_count", 0),
            element_count=data.get("element_count", 0),
        )


@dataclass
class CSSValue:
    """A single CSS value with its type and optional computed form."""

    raw: str
    value_type: CSSValueType = CSSValueType.KEYWORD
    computed: str | None = None

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "value_type": self.value_type.value,
            "computed": self.computed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CSSValue:
        return cls(
            raw=data.get("raw", ""),
            value_type=CSSValueType(data.get("value_type", "keyword")),
            computed=data.get("computed", None),
        )


@dataclass
class CSSRule:
    """A CSS rule: selector + property declarations + cascade metadata."""

    selector: str
    properties: dict[str, CSSValue] = field(default_factory=dict)
    file_path: str = ""
    line: int = 0
    specificity: Specificity = field(default_factory=Specificity)
    source_order: int = 0

    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
            "file_path": self.file_path,
            "line": self.line,
            "specificity": self.specificity.to_dict(),
            "source_order": self.source_order,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CSSRule:
        props_raw = data.get("properties", {})
        properties = {k: CSSValue.from_dict(v) for k, v in props_raw.items()}
        return cls(
            selector=data.get("selector", ""),
            properties=properties,
            file_path=data.get("file_path", ""),
            line=data.get("line", 0),
            specificity=Specificity.from_dict(data.get("specificity", {})),
            source_order=data.get("source_order", 0),
        )


@dataclass
class DOMNode:
    """A single DOM node."""

    node_id: str
    tag: str = ""
    node_kind: DOMNodeKind = DOMNodeKind.ELEMENT
    id_attr: str = ""
    classes: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    parent_id: str | None = None
    text_content: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "tag": self.tag,
            "node_kind": self.node_kind.value,
            "id_attr": self.id_attr,
            "classes": list(self.classes),
            "attributes": dict(self.attributes),
            "children": list(self.children),
            "parent_id": self.parent_id,
            "text_content": self.text_content,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DOMNode:
        return cls(
            node_id=data.get("node_id", ""),
            tag=data.get("tag", ""),
            node_kind=DOMNodeKind(data.get("node_kind", "element")),
            id_attr=data.get("id_attr", ""),
            classes=data.get("classes", []),
            attributes=data.get("attributes", {}),
            children=data.get("children", []),
            parent_id=data.get("parent_id", None),
            text_content=data.get("text_content", ""),
        )


@dataclass
class ComputedStyle:
    """The computed style for a single DOM node after cascade resolution."""

    node_id: str
    properties: dict[str, CSSValue] = field(default_factory=dict)

    def get(self, property_name: str) -> CSSValue | None:
        """Look up a computed property value."""
        return self.properties.get(property_name)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> ComputedStyle:
        props_raw = data.get("properties", {})
        properties = {k: CSSValue.from_dict(v) for k, v in props_raw.items()}
        return cls(
            node_id=data.get("node_id", ""),
            properties=properties,
        )


@dataclass
class DOMSection:
    """A section of the DOM presheaf: a selector and the nodes it matches.

    Each section corresponds to a local section over an open set
    (the nodes matching the selector).
    """

    selector: str
    matched_node_ids: list[str] = field(default_factory=list)
    computed_styles: dict[str, ComputedStyle] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "matched_node_ids": list(self.matched_node_ids),
            "computed_styles": {
                k: v.to_dict() for k, v in self.computed_styles.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> DOMSection:
        cs_raw = data.get("computed_styles", {})
        computed_styles = {
            k: ComputedStyle.from_dict(v) for k, v in cs_raw.items()
        }
        return cls(
            selector=data.get("selector", ""),
            matched_node_ids=data.get("matched_node_ids", []),
            computed_styles=computed_styles,
        )


@dataclass
class CascadeObstruction:
    """An obstruction encountered during cascade descent.

    Obstructions are the cohomological witnesses that prevent clean gluing
    of local CSS sections into a global computed style.
    """

    kind: CascadeObstructionKind
    selector1: str = ""
    selector2: str = ""
    property_name: str = ""
    message: str = ""
    severity: ObstructionSeverity = ObstructionSeverity.MEDIUM
    node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "selector1": self.selector1,
            "selector2": self.selector2,
            "property_name": self.property_name,
            "message": self.message,
            "severity": self.severity.value,
            "node_ids": list(self.node_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> CascadeObstruction:
        return cls(
            kind=CascadeObstructionKind(data.get("kind", "specificity_conflict")),
            selector1=data.get("selector1", ""),
            selector2=data.get("selector2", ""),
            property_name=data.get("property_name", ""),
            message=data.get("message", ""),
            severity=ObstructionSeverity(data.get("severity", "medium")),
            node_ids=data.get("node_ids", []),
        )
