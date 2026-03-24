"""Core data models for the rendering sub-package.

All classes are standalone dataclasses with ``to_dict`` / ``from_dict``
round-trip serialisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════

class RenderChangeKind(str, Enum):
    """Kind of change detected between two render frames."""

    POSITION_CHANGED = "position_changed"
    SIZE_CHANGED = "size_changed"
    STYLE_CHANGED = "style_changed"
    VISIBILITY_CHANGED = "visibility_changed"
    CONTENT_CHANGED = "content_changed"
    ADDED = "added"
    REMOVED = "removed"


# ═══════════════════════════════════════════════════════════════════════════
# ViewportRegion
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ViewportRegion:
    """Axis-aligned bounding box inside the viewport."""

    x: float
    y: float
    width: float
    height: float
    node_id: str = ""
    z_index: int = 0

    # -- helpers -------------------------------------------------------------

    def contains_point(self, px: float, py: float) -> bool:
        return (self.x <= px <= self.x + self.width
                and self.y <= py <= self.y + self.height)

    def overlaps(self, other: ViewportRegion) -> bool:
        return not (
            self.x + self.width <= other.x
            or other.x + other.width <= self.x
            or self.y + self.height <= other.y
            or other.y + other.height <= self.y
        )

    @property
    def area(self) -> float:
        return self.width * self.height

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "node_id": self.node_id,
            "z_index": self.z_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ViewportRegion:
        return cls(**data)


# ═══════════════════════════════════════════════════════════════════════════
# TextRun
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TextRun:
    """A contiguous run of styled text."""

    content: str
    font_family: str = "sans-serif"
    font_size: float = 16.0
    color: str = "#000000"
    node_id: str = ""
    position: tuple = field(default_factory=lambda: (0.0, 0.0))

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "color": self.color,
            "node_id": self.node_id,
            "position": list(self.position),
        }

    @classmethod
    def from_dict(cls, data: dict) -> TextRun:
        d = dict(data)
        if "position" in d:
            d["position"] = tuple(d["position"])
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════════════
# InteractiveZone
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class InteractiveZone:
    """A region that can receive user interaction events."""

    node_id: str
    event_types: list = field(default_factory=list)
    bbox: tuple = field(default_factory=lambda: (0.0, 0.0, 0.0, 0.0))
    z_index: int = 0
    cursor_style: str = "pointer"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "event_types": list(self.event_types),
            "bbox": list(self.bbox),
            "z_index": self.z_index,
            "cursor_style": self.cursor_style,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InteractiveZone:
        d = dict(data)
        if "bbox" in d:
            d["bbox"] = tuple(d["bbox"])
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════════════
# AnimationFrame
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AnimationFrame:
    """A single animation keyframe."""

    time_ms: float
    node_id: str
    properties_changed: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "time_ms": self.time_ms,
            "node_id": self.node_id,
            "properties_changed": dict(self.properties_changed),
        }

    @classmethod
    def from_dict(cls, data: dict) -> AnimationFrame:
        return cls(**data)


# ═══════════════════════════════════════════════════════════════════════════
# VisualElement
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class VisualElement:
    """A visual element composed of a region, text runs, interactive zones,
    and child elements."""

    region: ViewportRegion
    text_runs: list = field(default_factory=list)
    interactive_zones: list = field(default_factory=list)
    children: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "region": self.region.to_dict(),
            "text_runs": [t.to_dict() if hasattr(t, "to_dict") else t for t in self.text_runs],
            "interactive_zones": [z.to_dict() if hasattr(z, "to_dict") else z for z in self.interactive_zones],
            "children": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict) -> VisualElement:
        region = ViewportRegion.from_dict(data["region"])
        text_runs = [TextRun.from_dict(t) for t in data.get("text_runs", [])]
        interactive_zones = [InteractiveZone.from_dict(z) for z in data.get("interactive_zones", [])]
        children = [VisualElement.from_dict(c) for c in data.get("children", [])]
        return cls(
            region=region,
            text_runs=text_runs,
            interactive_zones=interactive_zones,
            children=children,
        )


# ═══════════════════════════════════════════════════════════════════════════
# VisualPage
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class VisualPage:
    """A full page rendered at a specific viewport."""

    viewport_width: float
    viewport_height: float
    elements: list = field(default_factory=list)
    device_class: str = "desktop"

    def to_dict(self) -> dict:
        return {
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "elements": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.elements],
            "device_class": self.device_class,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VisualPage:
        elements = [VisualElement.from_dict(e) for e in data.get("elements", [])]
        return cls(
            viewport_width=data["viewport_width"],
            viewport_height=data["viewport_height"],
            elements=elements,
            device_class=data.get("device_class", "desktop"),
        )


# ═══════════════════════════════════════════════════════════════════════════
# RenderChange / RenderDiff
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RenderChange:
    """A single change between two render states."""

    kind: RenderChangeKind
    node_id: str
    old_value: Any = None
    new_value: Any = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value if isinstance(self.kind, RenderChangeKind) else self.kind,
            "node_id": self.node_id,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RenderChange:
        d = dict(data)
        if "kind" in d:
            d["kind"] = RenderChangeKind(d["kind"])
        return cls(**d)


@dataclass
class RenderDiff:
    """Aggregated set of changes between two render states."""

    changes: list = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0

    def to_dict(self) -> dict:
        return {
            "changes": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.changes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RenderDiff:
        changes = [RenderChange.from_dict(c) for c in data.get("changes", [])]
        return cls(changes=changes)
