"""Simplified layout model for the DOM.

Maps computed styles to layout boxes with the CSS box model, and provides
detectors for visual overlaps, overflow, and containment violations.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .models import CSSValue, ComputedStyle, DOMNodeKind
from .dom_site import DOMSite


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class LayoutKind(str, Enum):
    """Layout display mode."""
    BLOCK = "block"
    INLINE = "inline"
    FLEX = "flex"
    GRID = "grid"
    ABSOLUTE = "absolute"
    FIXED = "fixed"
    STICKY = "sticky"
    INLINE_BLOCK = "inline-block"
    NONE = "none"


# ---------------------------------------------------------------------------
# Box model
# ---------------------------------------------------------------------------

@dataclass
class BoxModel:
    """CSS box model dimensions."""

    margin_top: float = 0
    margin_right: float = 0
    margin_bottom: float = 0
    margin_left: float = 0
    border_top: float = 0
    border_right: float = 0
    border_bottom: float = 0
    border_left: float = 0
    padding_top: float = 0
    padding_right: float = 0
    padding_bottom: float = 0
    padding_left: float = 0
    content_width: float = 0
    content_height: float = 0

    def total_width(self) -> float:
        return (
            self.margin_left + self.border_left + self.padding_left
            + self.content_width
            + self.padding_right + self.border_right + self.margin_right
        )

    def total_height(self) -> float:
        return (
            self.margin_top + self.border_top + self.padding_top
            + self.content_height
            + self.padding_bottom + self.border_bottom + self.margin_bottom
        )

    def to_dict(self) -> dict:
        return {
            "margin_top": self.margin_top,
            "margin_right": self.margin_right,
            "margin_bottom": self.margin_bottom,
            "margin_left": self.margin_left,
            "border_top": self.border_top,
            "border_right": self.border_right,
            "border_bottom": self.border_bottom,
            "border_left": self.border_left,
            "padding_top": self.padding_top,
            "padding_right": self.padding_right,
            "padding_bottom": self.padding_bottom,
            "padding_left": self.padding_left,
            "content_width": self.content_width,
            "content_height": self.content_height,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BoxModel:
        return cls(
            margin_top=data.get("margin_top", 0),
            margin_right=data.get("margin_right", 0),
            margin_bottom=data.get("margin_bottom", 0),
            margin_left=data.get("margin_left", 0),
            border_top=data.get("border_top", 0),
            border_right=data.get("border_right", 0),
            border_bottom=data.get("border_bottom", 0),
            border_left=data.get("border_left", 0),
            padding_top=data.get("padding_top", 0),
            padding_right=data.get("padding_right", 0),
            padding_bottom=data.get("padding_bottom", 0),
            padding_left=data.get("padding_left", 0),
            content_width=data.get("content_width", 0),
            content_height=data.get("content_height", 0),
        )


# ---------------------------------------------------------------------------
# Layout box
# ---------------------------------------------------------------------------

@dataclass
class LayoutBox:
    """A positioned layout box for one DOM node."""

    node_id: str
    kind: LayoutKind = LayoutKind.BLOCK
    box: BoxModel = field(default_factory=BoxModel)
    x: float = 0
    y: float = 0
    children: list[str] = field(default_factory=list)
    z_index: int = 0
    is_positioned: bool = False

    def bounds(self) -> tuple[float, float, float, float]:
        """(x, y, x + total_width, y + total_height)."""
        return (
            self.x,
            self.y,
            self.x + self.box.total_width(),
            self.y + self.box.total_height(),
        )

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "box": self.box.to_dict(),
            "x": self.x,
            "y": self.y,
            "children": list(self.children),
            "z_index": self.z_index,
            "is_positioned": self.is_positioned,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LayoutBox:
        return cls(
            node_id=data.get("node_id", ""),
            kind=LayoutKind(data.get("kind", "block")),
            box=BoxModel.from_dict(data.get("box", {})),
            x=data.get("x", 0),
            y=data.get("y", 0),
            children=data.get("children", []),
            z_index=data.get("z_index", 0),
            is_positioned=data.get("is_positioned", False),
        )


# ---------------------------------------------------------------------------
# Layout engine (simplified)
# ---------------------------------------------------------------------------

class LayoutEngine:
    """Compute a simplified layout from DOM + computed styles."""

    def compute_layout(
        self,
        dom: DOMSite,
        styles: dict[str, ComputedStyle],
        viewport_width: int = 1280,
        viewport_height: int = 800,
    ) -> dict[str, LayoutBox]:
        """Compute layout boxes for all element nodes."""
        boxes: dict[str, LayoutBox] = {}
        if not dom.root_id:
            return boxes
        self._layout_subtree(
            dom.root_id, styles, float(viewport_width), 0.0, 0.0, dom, boxes
        )
        return boxes

    def _layout_subtree(
        self,
        node_id: str,
        styles: dict[str, ComputedStyle],
        available_width: float,
        x: float,
        y: float,
        dom: DOMSite,
        boxes: dict[str, LayoutBox],
    ) -> float:
        """Layout a node and its children.  Returns total height consumed."""
        node = dom.nodes.get(node_id)
        if node is None or node.node_kind != DOMNodeKind.ELEMENT:
            return 0.0
        style = styles.get(node_id, ComputedStyle(node_id=node_id))
        layout_box = self._layout_node(node_id, style, available_width, x, y, dom, styles)
        boxes[node_id] = layout_box

        if layout_box.kind == LayoutKind.NONE:
            return 0.0

        # Layout children
        inner_x = x + layout_box.box.margin_left + layout_box.box.border_left + layout_box.box.padding_left
        inner_y = y + layout_box.box.margin_top + layout_box.box.border_top + layout_box.box.padding_top
        inner_w = layout_box.box.content_width
        child_y = inner_y
        child_ids: list[str] = []

        for cid in node.children:
            cnode = dom.nodes.get(cid)
            if cnode is None or cnode.node_kind != DOMNodeKind.ELEMENT:
                continue
            child_ids.append(cid)
            ch = self._layout_subtree(cid, styles, inner_w, inner_x, child_y, dom, boxes)
            child_y += ch

        layout_box.children = child_ids
        # Update content height to fit children
        content_h = child_y - inner_y
        if content_h > layout_box.box.content_height:
            layout_box.box.content_height = content_h

        return layout_box.box.total_height()

    def _parse_length(self, value: str, available: float = 0) -> float:
        """Parse a CSS length value to float pixels."""
        if not value or value == "auto" or value == "none":
            return 0.0
        value = value.strip()
        if value.endswith("px"):
            try:
                return float(value[:-2])
            except ValueError:
                return 0.0
        if value.endswith("%"):
            try:
                return float(value[:-1]) * available / 100.0
            except ValueError:
                return 0.0
        if value.endswith("em") or value.endswith("rem"):
            suffix_len = 3 if value.endswith("rem") else 2
            try:
                return float(value[:-suffix_len]) * 16.0
            except ValueError:
                return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0

    def _get_layout_kind(self, style: ComputedStyle) -> LayoutKind:
        """Determine layout kind from display and position properties."""
        pos = style.get("position")
        disp = style.get("display")
        if pos and pos.raw == "absolute":
            return LayoutKind.ABSOLUTE
        if pos and pos.raw == "fixed":
            return LayoutKind.FIXED
        if pos and pos.raw == "sticky":
            return LayoutKind.STICKY
        if disp:
            d = disp.raw
            if d == "none":
                return LayoutKind.NONE
            if d == "flex":
                return LayoutKind.FLEX
            if d == "grid":
                return LayoutKind.GRID
            if d == "inline":
                return LayoutKind.INLINE
            if d == "inline-block":
                return LayoutKind.INLINE_BLOCK
        return LayoutKind.BLOCK

    def _layout_node(
        self,
        node_id: str,
        style: ComputedStyle,
        available_width: float,
        x: float,
        y: float,
        dom: DOMSite,
        styles: dict[str, ComputedStyle],
    ) -> LayoutBox:
        """Create a LayoutBox for a single node."""
        kind = self._get_layout_kind(style)

        def _val(prop: str) -> str:
            v = style.get(prop)
            return v.raw if v else "0"

        box = BoxModel(
            margin_top=self._parse_length(_val("margin-top"), available_width),
            margin_right=self._parse_length(_val("margin-right"), available_width),
            margin_bottom=self._parse_length(_val("margin-bottom"), available_width),
            margin_left=self._parse_length(_val("margin-left"), available_width),
            padding_top=self._parse_length(_val("padding-top"), available_width),
            padding_right=self._parse_length(_val("padding-right"), available_width),
            padding_bottom=self._parse_length(_val("padding-bottom"), available_width),
            padding_left=self._parse_length(_val("padding-left"), available_width),
        )

        # Content width
        w_val = _val("width")
        if w_val and w_val not in ("auto", "none"):
            box.content_width = self._parse_length(w_val, available_width)
        else:
            box.content_width = max(
                0,
                available_width - box.margin_left - box.border_left
                - box.padding_left - box.padding_right
                - box.border_right - box.margin_right,
            )

        # Content height (default; will be expanded by children)
        h_val = _val("height")
        if h_val and h_val not in ("auto", "none"):
            box.content_height = self._parse_length(h_val, 0)

        is_positioned = kind in (LayoutKind.ABSOLUTE, LayoutKind.FIXED, LayoutKind.STICKY)
        z_val = style.get("z-index")
        z_index = 0
        if z_val and z_val.raw not in ("auto", ""):
            try:
                z_index = int(z_val.raw)
            except ValueError:
                pass

        return LayoutBox(
            node_id=node_id,
            kind=kind,
            box=box,
            x=x,
            y=y,
            z_index=z_index,
            is_positioned=is_positioned,
        )


# ---------------------------------------------------------------------------
# Overlap detector
# ---------------------------------------------------------------------------

class OverlapDetector:
    """Detect visual overlaps and overflow."""

    def detect_visual_overlaps(
        self, boxes: list[LayoutBox]
    ) -> list[tuple[str, str]]:
        """Pairs of positioned elements whose bounding boxes intersect."""
        positioned = [b for b in boxes if b.is_positioned]
        overlaps: list[tuple[str, str]] = []
        n = len(positioned)
        for i in range(n):
            for j in range(i + 1, n):
                if self._boxes_overlap(positioned[i], positioned[j]):
                    overlaps.append((positioned[i].node_id, positioned[j].node_id))
        return overlaps

    def detect_overflow(
        self, boxes: list[LayoutBox], viewport_width: int, viewport_height: int
    ) -> list[str]:
        """Node IDs of elements that extend beyond the viewport."""
        overflowing: list[str] = []
        for b in boxes:
            _, _, x2, y2 = b.bounds()
            if x2 > viewport_width or y2 > viewport_height:
                overflowing.append(b.node_id)
        return overflowing

    def _boxes_overlap(self, a: LayoutBox, b: LayoutBox) -> bool:
        ax1, ay1, ax2, ay2 = a.bounds()
        bx1, by1, bx2, by2 = b.bounds()
        if ax1 >= bx2 or bx1 >= ax2:
            return False
        if ay1 >= by2 or by1 >= ay2:
            return False
        return True


# ---------------------------------------------------------------------------
# Containment checker
# ---------------------------------------------------------------------------

class ContainmentChecker:
    """Check parent-child visual containment."""

    def check_containment(self, parent: LayoutBox, child: LayoutBox) -> bool:
        """True if *child* is visually inside *parent*'s bounds."""
        px1, py1, px2, py2 = parent.bounds()
        cx1, cy1, cx2, cy2 = child.bounds()
        return cx1 >= px1 and cy1 >= py1 and cx2 <= px2 and cy2 <= py2

    def check_reading_order(
        self, boxes: list[LayoutBox]
    ) -> list[tuple[str, str]]:
        """Pairs where DOM order (list order) conflicts with visual order.

        Visual order is top-to-bottom, left-to-right.
        """
        conflicts: list[tuple[str, str]] = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                # In DOM order a comes before b
                # In visual order: b should not be above-left of a
                ax1, ay1, _, _ = a.bounds()
                bx1, by1, _, _ = b.bounds()
                if by1 < ay1 or (by1 == ay1 and bx1 < ax1):
                    conflicts.append((a.node_id, b.node_id))
        return conflicts
