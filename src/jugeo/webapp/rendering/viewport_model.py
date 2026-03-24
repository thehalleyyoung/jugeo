"""Viewport model and multi-viewport simulator.

Standalone module; imports only from sibling submodules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jugeo.webapp.rendering.models import (
    RenderChange,
    RenderChangeKind,
    RenderDiff,
    VisualElement,
    VisualPage,
    ViewportRegion,
)
from jugeo.webapp.rendering.visual_site import VisualSite


# ═══════════════════════════════════════════════════════════════════════════
# Viewport
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Viewport:
    """Describes a device viewport."""

    width: int
    height: int
    pixel_ratio: float = 1.0
    media_type: str = "screen"
    orientation: str = "landscape"
    is_touch: bool = False

    @property
    def device_class(self) -> str:
        """Return device class: 'mobile', 'tablet', 'laptop', 'desktop', 'ultrawide'."""
        if self.width < 768:
            return "mobile"
        if self.width < 1024:
            return "tablet"
        if self.width < 1440:
            return "laptop"
        if self.width < 2560:
            return "desktop"
        return "ultrawide"

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "pixel_ratio": self.pixel_ratio,
            "media_type": self.media_type,
            "orientation": self.orientation,
            "is_touch": self.is_touch,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Viewport:
        return cls(**data)


# ═══════════════════════════════════════════════════════════════════════════
# ViewportPresets
# ═══════════════════════════════════════════════════════════════════════════

class ViewportPresets:
    """Pre-defined device viewports."""

    @staticmethod
    def iphone_se() -> Viewport:
        return Viewport(
            width=375, height=667,
            pixel_ratio=2.0, is_touch=True, orientation="portrait",
        )

    @staticmethod
    def iphone_14() -> Viewport:
        return Viewport(
            width=390, height=844,
            pixel_ratio=3.0, is_touch=True, orientation="portrait",
        )

    @staticmethod
    def ipad() -> Viewport:
        return Viewport(
            width=810, height=1080,
            pixel_ratio=2.0, is_touch=True, orientation="landscape",
        )

    @staticmethod
    def laptop_13() -> Viewport:
        return Viewport(
            width=1280, height=800,
            pixel_ratio=2.0, orientation="landscape",
        )

    @staticmethod
    def desktop_1080p() -> Viewport:
        return Viewport(
            width=1920, height=1080,
            pixel_ratio=1.0, orientation="landscape",
        )

    @staticmethod
    def ultrawide() -> Viewport:
        return Viewport(
            width=3440, height=1440,
            pixel_ratio=1.0, orientation="landscape",
        )

    @staticmethod
    def print_a4() -> Viewport:
        return Viewport(
            width=794, height=1123,
            pixel_ratio=1.0, media_type="print", orientation="portrait",
        )

    @staticmethod
    def all_presets() -> dict:
        return {
            "iphone_se": ViewportPresets.iphone_se(),
            "iphone_14": ViewportPresets.iphone_14(),
            "ipad": ViewportPresets.ipad(),
            "laptop_13": ViewportPresets.laptop_13(),
            "desktop_1080p": ViewportPresets.desktop_1080p(),
            "ultrawide": ViewportPresets.ultrawide(),
            "print_a4": ViewportPresets.print_a4(),
        }


# ═══════════════════════════════════════════════════════════════════════════
# ViewportSimulator
# ═══════════════════════════════════════════════════════════════════════════

class ViewportSimulator:
    """Simulates rendering at various viewport sizes."""

    def simulate(self, dom: dict, styles: dict, viewport: Viewport) -> VisualPage:
        """Render app at given viewport."""
        # Simplified simulation: create a single-element page
        elements: list = []
        if dom:
            children = dom.get("children", [])
            for child in children:
                node_id = child.get("id", "")
                region = ViewportRegion(
                    x=0.0, y=0.0,
                    width=float(viewport.width),
                    height=float(viewport.height),
                    node_id=node_id,
                )
                elements.append(VisualElement(region=region))
        device = viewport.device_class
        return VisualPage(
            viewport_width=float(viewport.width),
            viewport_height=float(viewport.height),
            elements=elements,
            device_class=device,
        )

    def simulate_all_presets(self, dom: dict, styles: dict) -> dict:
        """Render app at all presets."""
        presets = ViewportPresets.all_presets()
        results: dict = {}
        for name, vp in presets.items():
            results[name] = self.simulate(dom, styles, vp)
        return results

    def compare_viewports(self, page1: VisualPage, page2: VisualPage) -> RenderDiff:
        """Compare two VisualPages and return differences."""
        changes: list = []

        # Build element maps by node_id
        elems1: dict = {}
        for el in page1.elements:
            if hasattr(el, "region") and el.region.node_id:
                elems1[el.region.node_id] = el
        elems2: dict = {}
        for el in page2.elements:
            if hasattr(el, "region") and el.region.node_id:
                elems2[el.region.node_id] = el

        all_ids = set(elems1.keys()) | set(elems2.keys())
        for nid in all_ids:
            if nid in elems1 and nid not in elems2:
                changes.append(RenderChange(
                    kind=RenderChangeKind.REMOVED,
                    node_id=nid,
                ))
            elif nid not in elems1 and nid in elems2:
                changes.append(RenderChange(
                    kind=RenderChangeKind.ADDED,
                    node_id=nid,
                ))
            else:
                r1 = elems1[nid].region
                r2 = elems2[nid].region
                if r1.x != r2.x or r1.y != r2.y:
                    changes.append(RenderChange(
                        kind=RenderChangeKind.POSITION_CHANGED,
                        node_id=nid,
                        old_value=(r1.x, r1.y),
                        new_value=(r2.x, r2.y),
                    ))
                if r1.width != r2.width or r1.height != r2.height:
                    changes.append(RenderChange(
                        kind=RenderChangeKind.SIZE_CHANGED,
                        node_id=nid,
                        old_value=(r1.width, r1.height),
                        new_value=(r2.width, r2.height),
                    ))

        return RenderDiff(changes=changes)
