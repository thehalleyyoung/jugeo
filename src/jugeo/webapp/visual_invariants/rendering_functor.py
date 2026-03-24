"""
Rendering functor R: W → V — maps web site to visual site (§3.5).

The functor maps DOM nodes with styles and layout to viewport regions,
text runs, interactive zones, and animation frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


__all__ = [
    "ViewportRegion",
    "TextRun",
    "InteractiveZone",
    "AnimationFrame",
    "VisualSite",
    "RenderingFunctor",
    "VisualDescentChecker",
]


# ---------------------------------------------------------------------------
# Visual site data types
# ---------------------------------------------------------------------------


@dataclass
class ViewportRegion:
    """A rectangular region in the viewport."""

    x: float
    y: float
    width: float
    height: float
    node_id: str = ""

    def contains(self, other: ViewportRegion) -> bool:
        """Return ``True`` if *other* is fully inside this region."""
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.x + other.width <= self.x + self.width
            and other.y + other.height <= self.y + self.height
        )

    def overlaps(self, other: ViewportRegion) -> bool:
        """Return ``True`` if the two regions overlap."""
        if self.x + self.width <= other.x:
            return False
        if other.x + other.width <= self.x:
            return False
        if self.y + self.height <= other.y:
            return False
        if other.y + other.height <= self.y:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "node_id": self.node_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ViewportRegion:
        return cls(
            x=d["x"],
            y=d["y"],
            width=d["width"],
            height=d["height"],
            node_id=d.get("node_id", ""),
        )


@dataclass
class TextRun:
    """A styled run of text in the visual output."""

    content: str
    font: str
    size: float
    color: str
    node_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "font": self.font,
            "size": self.size,
            "color": self.color,
            "node_id": self.node_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TextRun:
        return cls(
            content=d["content"],
            font=d["font"],
            size=d["size"],
            color=d["color"],
            node_id=d.get("node_id", ""),
        )


@dataclass
class InteractiveZone:
    """An interactive region tied to event handlers."""

    node_id: str
    event_types: list[str] = field(default_factory=list)
    bbox: Optional[ViewportRegion] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "event_types": list(self.event_types),
            "bbox": self.bbox.to_dict() if self.bbox else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InteractiveZone:
        bbox_d = d.get("bbox")
        return cls(
            node_id=d["node_id"],
            event_types=d.get("event_types", []),
            bbox=ViewportRegion.from_dict(bbox_d) if bbox_d else None,
        )


@dataclass
class AnimationFrame:
    """A single animation frame descriptor."""

    node_id: str
    duration_ms: float
    easing: str = "linear"
    properties: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "duration_ms": self.duration_ms,
            "easing": self.easing,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnimationFrame:
        return cls(
            node_id=d["node_id"],
            duration_ms=d["duration_ms"],
            easing=d.get("easing", "linear"),
            properties=d.get("properties", {}),
        )


@dataclass
class VisualSite:
    """The output of the rendering functor — the full visual representation."""

    regions: list[ViewportRegion] = field(default_factory=list)
    text_runs: list[TextRun] = field(default_factory=list)
    interactive_zones: list[InteractiveZone] = field(default_factory=list)
    animation_frames: list[AnimationFrame] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regions": [r.to_dict() for r in self.regions],
            "text_runs": [t.to_dict() for t in self.text_runs],
            "interactive_zones": [z.to_dict() for z in self.interactive_zones],
            "animation_frames": [a.to_dict() for a in self.animation_frames],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualSite:
        return cls(
            regions=[ViewportRegion.from_dict(r) for r in d.get("regions", [])],
            text_runs=[TextRun.from_dict(t) for t in d.get("text_runs", [])],
            interactive_zones=[InteractiveZone.from_dict(z) for z in d.get("interactive_zones", [])],
            animation_frames=[AnimationFrame.from_dict(a) for a in d.get("animation_frames", [])],
        )


# ---------------------------------------------------------------------------
# RenderingFunctor
# ---------------------------------------------------------------------------


class RenderingFunctor:
    """The rendering functor ``R: W → V`` mapping web site to visual site."""

    def apply(
        self,
        dom: dict[str, Any],
        styles: dict[str, dict[str, Any]],
        layout_boxes: dict[str, dict[str, Any]],
    ) -> VisualSite:
        """Map a web representation (DOM + styles + layout) to a visual site."""
        vs = VisualSite()
        self._traverse_dom(dom, styles, layout_boxes, vs)
        return vs

    def _map_coordinate_to_region(
        self, layout_box: dict[str, Any], node_id: str = ""
    ) -> ViewportRegion:
        return ViewportRegion(
            x=float(layout_box.get("x", 0)),
            y=float(layout_box.get("y", 0)),
            width=float(layout_box.get("width", 0)),
            height=float(layout_box.get("height", 0)),
            node_id=node_id,
        )

    def _map_text_to_run(
        self, node: dict[str, Any], style: dict[str, Any]
    ) -> TextRun:
        text_content = node.get("text", "")
        if not text_content:
            # Concatenate text from children if no direct text
            for child in node.get("children", []):
                if isinstance(child, str):
                    text_content += child
        return TextRun(
            content=text_content,
            font=str(style.get("font_family", "sans-serif")),
            size=float(style.get("font_size", 16)),
            color=str(style.get("color", "black")),
            node_id=node.get("id", ""),
        )

    def _map_event_handler_to_zone(
        self, node: dict[str, Any], events: list[str]
    ) -> InteractiveZone:
        return InteractiveZone(
            node_id=node.get("id", ""),
            event_types=list(events),
            bbox=None,  # bbox will be set during traversal if layout exists
        )

    def _traverse_dom(
        self,
        dom: dict[str, Any],
        styles: dict[str, dict[str, Any]],
        layout_boxes: dict[str, dict[str, Any]],
        visual_site: VisualSite,
    ) -> None:
        """Recursively traverse DOM and populate *visual_site*."""
        node_id = dom.get("id", "")

        # Map layout box to viewport region
        if node_id and node_id in layout_boxes:
            region = self._map_coordinate_to_region(layout_boxes[node_id], node_id)
            visual_site.regions.append(region)

        # Map text content
        node_style = styles.get(node_id, {}) if node_id else {}
        has_text = dom.get("text", "") or any(isinstance(c, str) for c in dom.get("children", []))
        if has_text and node_id:
            run = self._map_text_to_run(dom, node_style)
            if run.content:
                visual_site.text_runs.append(run)

        # Map event handlers
        events = dom.get("events", [])
        if events and node_id:
            zone = self._map_event_handler_to_zone(dom, events)
            if node_id in layout_boxes:
                zone.bbox = self._map_coordinate_to_region(layout_boxes[node_id], node_id)
            visual_site.interactive_zones.append(zone)

        # Recurse into children
        for child in dom.get("children", []):
            if isinstance(child, dict):
                self._traverse_dom(child, styles, layout_boxes, visual_site)


# ---------------------------------------------------------------------------
# VisualDescentChecker
# ---------------------------------------------------------------------------


class VisualDescentChecker:
    """Post-rendering consistency checks on the visual site."""

    def check_visual_overlap(self, regions: list[ViewportRegion]) -> list[str]:
        """Return violations where one region unexpectedly contains another."""
        violations: list[str] = []
        for i, a in enumerate(regions):
            for j, b in enumerate(regions):
                if i >= j:
                    continue
                if a.contains(b) and a.node_id != b.node_id:
                    violations.append(
                        f"'{a.node_id}' fully contains '{b.node_id}'"
                    )
        return violations

    def check_interaction_dead_zones(
        self,
        zones: list[InteractiveZone],
        regions: list[ViewportRegion],
    ) -> list[str]:
        """Return violations for interactive zones with no visible region."""
        region_ids = {r.node_id for r in regions if r.width > 0 and r.height > 0}
        violations: list[str] = []
        for z in zones:
            has_visible = z.node_id in region_ids
            if z.bbox is not None:
                if z.bbox.width > 0 and z.bbox.height > 0:
                    has_visible = True
            if not has_visible:
                violations.append(
                    f"Interactive zone '{z.node_id}' has no visible region"
                )
        return violations

    def check_text_legibility(self, runs: list[TextRun]) -> list[str]:
        """Return violations for text with font size < 12px."""
        violations: list[str] = []
        for run in runs:
            if run.size < 12.0:
                violations.append(
                    f"Text '{run.content[:30]}' in '{run.node_id}' "
                    f"has font_size={run.size}px (< 12px)"
                )
        return violations
