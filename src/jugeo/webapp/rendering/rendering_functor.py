"""Rendering functor R: W -> V and descent checker.

Standalone module; imports only from sibling submodules.
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
from jugeo.webapp.rendering.visual_site import VisualSite


# ═══════════════════════════════════════════════════════════════════════════
# RenderingFunctor
# ═══════════════════════════════════════════════════════════════════════════

class RenderingFunctor:
    """The rendering functor R: W -> V mapping web app to visual site."""

    def apply(
        self,
        dom: dict,
        styles: dict,
        layout: list,
        viewport_w: int = 1280,
        viewport_h: int = 800,
    ) -> VisualSite:
        """Apply the functor to produce a VisualSite."""
        regions = self._map_layout_to_regions(layout)
        texts = self._map_text_nodes(dom, styles)
        zones = self._map_interactive_elements(dom)
        elements = self._compose_visual_elements(regions, texts, zones)
        return VisualSite(
            elements=elements,
            viewport_width=float(viewport_w),
            viewport_height=float(viewport_h),
        )

    # ── mapping helpers ─────────────────────────────────────────────────

    def _map_layout_to_regions(self, layout_boxes: list) -> list:
        """Map layout boxes to ViewportRegions.

        Each layout box: {"id": str, "x": float, "y": float,
                          "width": float, "height": float, "z_index": int}
        """
        regions: list = []
        for box in layout_boxes:
            regions.append(ViewportRegion(
                x=box.get("x", 0.0),
                y=box.get("y", 0.0),
                width=box.get("width", 0.0),
                height=box.get("height", 0.0),
                node_id=box.get("id", ""),
                z_index=box.get("z_index", 0),
            ))
        return regions

    def _map_text_nodes(self, dom: dict, styles: dict) -> list:
        """Map text nodes from DOM to TextRuns."""
        texts: list = []
        if not dom:
            return texts
        children = dom.get("children", [])
        for child in children:
            if child.get("type") == "text" or "content" in child:
                node_id = child.get("id", "")
                node_style = styles.get(node_id, {}) if styles else {}
                texts.append(TextRun(
                    content=child.get("content", ""),
                    font_family=node_style.get("font_family", "sans-serif"),
                    font_size=node_style.get("font_size", 16.0),
                    color=node_style.get("color", "#000000"),
                    node_id=node_id,
                ))
        return texts

    def _map_interactive_elements(
        self, dom: dict, event_handlers: dict = None
    ) -> list:
        """Map interactive DOM elements to InteractiveZones."""
        zones: list = []
        if not dom:
            return zones
        children = dom.get("children", [])
        interactive_tags = {"button", "a", "input", "select", "textarea", "form"}
        for child in children:
            tag = child.get("tag", "")
            if tag in interactive_tags:
                node_id = child.get("id", "")
                bbox = child.get("bbox", (0.0, 0.0, 0.0, 0.0))
                if isinstance(bbox, list):
                    bbox = tuple(bbox)
                events = ["click"]
                if tag == "input":
                    events = ["click", "focus", "blur", "keypress"]
                elif tag == "form":
                    events = ["submit"]
                elif tag == "a":
                    events = ["click"]
                zones.append(InteractiveZone(
                    node_id=node_id,
                    event_types=events,
                    bbox=bbox,
                    z_index=child.get("z_index", 0),
                ))
        return zones

    def _compose_visual_elements(
        self,
        regions: list,
        texts: list,
        zones: list,
    ) -> list:
        """Compose regions, texts, zones into VisualElements."""
        # Build lookup by node_id
        text_by_id: dict = {}
        for t in texts:
            text_by_id.setdefault(t.node_id, []).append(t)

        zone_by_id: dict = {}
        for z in zones:
            zone_by_id.setdefault(z.node_id, []).append(z)

        elements: list = []
        for region in regions:
            nid = region.node_id
            el_texts = text_by_id.get(nid, [])
            el_zones = zone_by_id.get(nid, [])
            elements.append(VisualElement(
                region=region,
                text_runs=el_texts,
                interactive_zones=el_zones,
            ))
        return elements


# ═══════════════════════════════════════════════════════════════════════════
# RenderingDescentChecker
# ═══════════════════════════════════════════════════════════════════════════

class RenderingDescentChecker:
    """Checks for visual descent issues in a VisualSite."""

    def check_visual_overlap(self, visual: VisualSite) -> list:
        """Find elements that visually occlude each other unintentionally."""
        issues: list = []
        regions = visual.regions()
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i], regions[j]
                if a.overlaps(b) and a.z_index == b.z_index:
                    issues.append({
                        "type": "visual_overlap",
                        "node_a": a.node_id,
                        "node_b": b.node_id,
                        "z_index": a.z_index,
                    })
        return issues

    def check_interaction_dead_zones(self, visual: VisualSite) -> list:
        """Find clickable elements that are obscured by other elements."""
        issues: list = []
        zones = visual.interactive_zones()
        regions = visual.regions()
        for zone in zones:
            zx, zy, zw, zh = zone.bbox
            zone_region = ViewportRegion(x=zx, y=zy, width=zw, height=zh, z_index=zone.z_index)
            for region in regions:
                if region.node_id != zone.node_id and region.overlaps(zone_region):
                    if region.z_index > zone.z_index:
                        issues.append({
                            "type": "interaction_dead_zone",
                            "obscured": zone.node_id,
                            "obscured_by": region.node_id,
                        })
        return issues

    def check_layout_thrashing(self, frames: list) -> list:
        """Find layout instability across frames."""
        issues: list = []
        if len(frames) < 2:
            return issues
        for i in range(len(frames) - 1):
            page_a = frames[i]
            page_b = frames[i + 1]
            el_a = {
                e.region.node_id: e
                for e in (page_a.elements if hasattr(page_a, "elements") else [])
                if hasattr(e, "region") and e.region.node_id
            }
            for el in (page_b.elements if hasattr(page_b, "elements") else []):
                if not hasattr(el, "region"):
                    continue
                nid = el.region.node_id
                if nid in el_a:
                    prev = el_a[nid].region
                    curr = el.region
                    dx = abs(curr.x - prev.x)
                    dy = abs(curr.y - prev.y)
                    dw = abs(curr.width - prev.width)
                    dh = abs(curr.height - prev.height)
                    if dx > 10 or dy > 10 or dw > 10 or dh > 10:
                        issues.append({
                            "type": "layout_thrashing",
                            "node_id": nid,
                            "frame": i,
                            "delta": {"dx": dx, "dy": dy, "dw": dw, "dh": dh},
                        })
        return issues

    def check_text_legibility(self, visual: VisualSite) -> list:
        """Find text that is too small or has low contrast."""
        issues: list = []
        for tr in visual.text_runs():
            if tr.font_size < 10.0:
                issues.append({
                    "type": "text_too_small",
                    "node_id": tr.node_id,
                    "font_size": tr.font_size,
                    "minimum": 10.0,
                })
        return issues

    def check_scroll_dependency(self, visual: VisualSite) -> list:
        """Find content only visible after scrolling."""
        issues: list = []
        vh = visual._viewport_height
        for region in visual.regions():
            if region.y > vh:
                issues.append({
                    "type": "below_fold",
                    "node_id": region.node_id,
                    "y": region.y,
                    "viewport_height": vh,
                })
        return issues
