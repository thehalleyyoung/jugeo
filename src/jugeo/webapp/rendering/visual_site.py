"""VisualSite — the composite visual representation of a web application.

Standalone module; imports only from sibling ``models`` module.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from jugeo.webapp.rendering.models import (
    InteractiveZone,
    TextRun,
    ViewportRegion,
    VisualElement,
    VisualPage,
)


class VisualSite:
    """The visual site V — a structured collection of visual elements."""

    def __init__(
        self,
        elements: list,
        viewport_width: float = 1280.0,
        viewport_height: float = 800.0,
    ):
        self._elements: list = list(elements)
        self._viewport_width: float = viewport_width
        self._viewport_height: float = viewport_height

    # ── constructors ────────────────────────────────────────────────────

    @classmethod
    def from_layout(
        cls,
        layout_boxes: list,
        styles: dict,
        dom: dict,
    ) -> VisualSite:
        """Build a VisualSite from raw layout/style/DOM data."""
        elements: list = []
        for box in layout_boxes:
            region = ViewportRegion(
                x=box.get("x", 0.0),
                y=box.get("y", 0.0),
                width=box.get("width", 0.0),
                height=box.get("height", 0.0),
                node_id=box.get("id", ""),
                z_index=box.get("z_index", 0),
            )
            text_runs: list = []
            node_id = box.get("id", "")
            # Look up text content from DOM
            if dom:
                children = dom.get("children", [])
                for child in children:
                    if child.get("id") == node_id and child.get("type") == "text":
                        node_style = styles.get(node_id, {})
                        text_runs.append(TextRun(
                            content=child.get("content", ""),
                            font_family=node_style.get("font_family", "sans-serif"),
                            font_size=node_style.get("font_size", 16.0),
                            color=node_style.get("color", "#000000"),
                            node_id=node_id,
                            position=(box.get("x", 0.0), box.get("y", 0.0)),
                        ))

            interactive_zones: list = []
            tag = ""
            if dom:
                for child in dom.get("children", []):
                    if child.get("id") == node_id:
                        tag = child.get("tag", "")
                        break
            if tag in ("button", "a", "input", "select", "textarea"):
                interactive_zones.append(InteractiveZone(
                    node_id=node_id,
                    event_types=["click"],
                    bbox=(box.get("x", 0.0), box.get("y", 0.0),
                          box.get("width", 0.0), box.get("height", 0.0)),
                    z_index=box.get("z_index", 0),
                ))

            elements.append(VisualElement(
                region=region,
                text_runs=text_runs,
                interactive_zones=interactive_zones,
            ))

        vw = 1280.0
        vh = 800.0
        if layout_boxes:
            max_x = max((b.get("x", 0) + b.get("width", 0)) for b in layout_boxes)
            max_y = max((b.get("y", 0) + b.get("height", 0)) for b in layout_boxes)
            vw = max(vw, max_x)
            vh = max(vh, max_y)

        return cls(elements=elements, viewport_width=vw, viewport_height=vh)

    # ── queries ─────────────────────────────────────────────────────────

    def regions(self) -> list:
        """All viewport regions from all elements (recursive)."""
        result: list = []
        self._collect_regions(self._elements, result)
        return result

    def _collect_regions(self, elements: list, acc: list) -> None:
        for el in elements:
            acc.append(el.region)
            if el.children:
                self._collect_regions(el.children, acc)

    def text_runs(self) -> list:
        """All text runs from all elements."""
        result: list = []
        self._collect_text_runs(self._elements, result)
        return result

    def _collect_text_runs(self, elements: list, acc: list) -> None:
        for el in elements:
            acc.extend(el.text_runs)
            if el.children:
                self._collect_text_runs(el.children, acc)

    def interactive_zones(self) -> list:
        """All interactive zones from all elements."""
        result: list = []
        self._collect_zones(self._elements, result)
        return result

    def _collect_zones(self, elements: list, acc: list) -> None:
        for el in elements:
            acc.extend(el.interactive_zones)
            if el.children:
                self._collect_zones(el.children, acc)

    # ── morphisms ───────────────────────────────────────────────────────

    def spatial_morphisms(self) -> list:
        """SPATIAL_CONTAINMENT morphisms: region A contains B."""
        result: list = []
        self._collect_spatial(self._elements, result)
        return result

    def _collect_spatial(self, elements: list, acc: list) -> None:
        for el in elements:
            parent_id = el.region.node_id
            for child in el.children:
                child_id = child.region.node_id
                if parent_id and child_id:
                    acc.append({
                        "kind": "SPATIAL_CONTAINMENT",
                        "parent": parent_id,
                        "child": child_id,
                    })
            if el.children:
                self._collect_spatial(el.children, acc)

    def temporal_morphisms(self) -> list:
        """TEMPORAL_SEQUENCE morphisms based on element ordering."""
        regions = self.regions()
        result: list = []
        for i in range(len(regions) - 1):
            a = regions[i]
            b = regions[i + 1]
            if a.node_id and b.node_id:
                result.append({
                    "kind": "TEMPORAL_SEQUENCE",
                    "before": a.node_id,
                    "after": b.node_id,
                })
        return result

    def interaction_morphisms(self) -> list:
        """INTERACTION_TRIGGER morphisms: zone -> state change."""
        zones = self.interactive_zones()
        result: list = []
        for zone in zones:
            for evt in zone.event_types:
                result.append({
                    "kind": "INTERACTION_TRIGGER",
                    "source": zone.node_id,
                    "event": evt,
                })
        return result

    # ── hit testing ─────────────────────────────────────────────────────

    def elements_at_point(self, x: float, y: float) -> list:
        """Hit test: elements whose region contains (x, y)."""
        result: list = []
        self._hit_test_point(self._elements, x, y, result)
        return result

    def _hit_test_point(self, elements: list, x: float, y: float, acc: list) -> None:
        for el in elements:
            if el.region.contains_point(x, y):
                acc.append(el)
            if el.children:
                self._hit_test_point(el.children, x, y, acc)

    def elements_in_region(self, x: float, y: float, w: float, h: float) -> list:
        """Elements that overlap the given rectangle."""
        query = ViewportRegion(x=x, y=y, width=w, height=h)
        result: list = []
        self._hit_test_region(self._elements, query, result)
        return result

    def _hit_test_region(self, elements: list, query: ViewportRegion, acc: list) -> None:
        for el in elements:
            if el.region.overlaps(query):
                acc.append(el)
            if el.children:
                self._hit_test_region(el.children, query, acc)

    # ── ordering ────────────────────────────────────────────────────────

    def reading_order(self) -> list:
        """Node IDs in reading order (top-to-bottom, left-to-right)."""
        regions = self.regions()
        sorted_regions = sorted(regions, key=lambda r: (r.y, r.x))
        return [r.node_id for r in sorted_regions if r.node_id]

    def z_order(self) -> list:
        """Node IDs in z-order (front to back, descending z_index)."""
        regions = self.regions()
        sorted_regions = sorted(regions, key=lambda r: -r.z_index)
        return [r.node_id for r in sorted_regions if r.node_id]

    # ── conversion ──────────────────────────────────────────────────────

    def to_visual_page(self) -> VisualPage:
        """Convert to VisualPage."""
        device = "desktop"
        if self._viewport_width < 768:
            device = "mobile"
        elif self._viewport_width < 1024:
            device = "tablet"
        return VisualPage(
            viewport_width=self._viewport_width,
            viewport_height=self._viewport_height,
            elements=list(self._elements),
            device_class=device,
        )

    # ── serialisation ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "viewport_width": self._viewport_width,
            "viewport_height": self._viewport_height,
            "elements": [e.to_dict() if hasattr(e, "to_dict") else e for e in self._elements],
        }

    @classmethod
    def from_dict(cls, data: dict) -> VisualSite:
        elements = [VisualElement.from_dict(e) for e in data.get("elements", [])]
        return cls(
            elements=elements,
            viewport_width=data.get("viewport_width", 1280.0),
            viewport_height=data.get("viewport_height", 800.0),
        )
