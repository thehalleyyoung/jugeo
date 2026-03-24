"""
Threshold invariant family — minimum sizes, contrast ratios, scroll checks.

Ensures that absolute constraints (WCAG minimums, touch-target sizes, etc.)
are met on each device class.

Part of §3.6 invariant family 3 (threshold).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import InvariantResult, InvariantStatus


__all__ = [
    "ThresholdInvariant",
    "ThresholdChecker",
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class ThresholdInvariant:
    """A threshold constraint on a single property.

    ``subject.property <relation> threshold``
    """

    subject: str
    property: str  # "font_size", "touch_target", "contrast_ratio", "width", "height"
    relation: str  # "gte", "lte", "gt", "lt"
    threshold: float
    reference: str = ""
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"threshold_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "property": self.property,
            "relation": self.relation,
            "threshold": self.threshold,
            "reference": self.reference,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ThresholdInvariant:
        return cls(
            subject=d["subject"],
            property=d["property"],
            relation=d["relation"],
            threshold=d["threshold"],
            reference=d.get("reference", ""),
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"threshold_{uuid.uuid4().hex[:8]}"),
        )


# ---------------------------------------------------------------------------
# Named CSS colours (subset)
# ---------------------------------------------------------------------------

_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "transparent": (0, 0, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "silver": (192, 192, 192),
    "maroon": (128, 0, 0),
    "navy": (0, 0, 128),
    "teal": (0, 128, 128),
    "olive": (128, 128, 0),
    "lime": (0, 255, 0),
    "aqua": (0, 255, 255),
    "fuchsia": (255, 0, 255),
    "pink": (255, 192, 203),
    "brown": (165, 42, 42),
    "coral": (255, 127, 80),
    "gold": (255, 215, 0),
    "ivory": (255, 255, 240),
    "khaki": (240, 230, 140),
    "lavender": (230, 230, 250),
    "beige": (245, 245, 220),
}

# Regex patterns for colour parsing
_HEX6_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
_HEX3_RE = re.compile(r"^#([0-9a-fA-F]{3})$")
_RGB_RE = re.compile(r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$")


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class ThresholdChecker:
    """Checks threshold invariants — absolute minimums / maximums."""

    # ---- public API -------------------------------------------------------

    def check_font_size(
        self,
        styles: dict[str, dict[str, Any]],
        node_id: str,
        min_px: float = 12.0,
    ) -> InvariantResult:
        """``font_size >= min_px``."""
        inv_id = f"threshold_font_size_{node_id}"
        style = styles.get(node_id)
        if style is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{node_id}' not found in styles",
            )

        raw = style.get("font_size")
        if raw is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"'font_size' not set for '{node_id}'",
            )

        try:
            size = float(raw)
        except (ValueError, TypeError):
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Cannot parse font_size '{raw}'",
            )

        if size >= min_px:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                evidence={"font_size": size, "min_px": min_px},
                message=f"font_size={size}px >= {min_px}px",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"font_size": size, "min_px": min_px},
            message=f"font_size={size}px < {min_px}px",
        )

    def check_touch_target(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        node_id: str,
        min_px: float = 44.0,
    ) -> InvariantResult:
        """Both width and height >= *min_px*."""
        inv_id = f"threshold_touch_target_{node_id}"
        box = layout_boxes.get(node_id)
        if box is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{node_id}' not found in layout_boxes",
            )

        w = box.get("width", 0)
        h = box.get("height", 0)
        if w >= min_px and h >= min_px:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                evidence={"width": w, "height": h, "min_px": min_px},
                message=f"Touch target {w}×{h} >= {min_px}px",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"width": w, "height": h, "min_px": min_px},
            message=f"Touch target {w}×{h} too small (min {min_px}px)",
        )

    def check_no_horizontal_scroll(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        viewport_width: float,
    ) -> InvariantResult:
        """No element extends beyond *viewport_width*."""
        inv_id = "threshold_no_horizontal_scroll"
        for nid, box in layout_boxes.items():
            right_edge = box.get("x", 0) + box.get("width", 0)
            if right_edge > viewport_width:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.VIOLATED,
                    evidence={"node": nid, "right_edge": right_edge, "viewport_width": viewport_width},
                    message=f"'{nid}' extends to {right_edge}px > viewport {viewport_width}px",
                )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message=f"All elements within {viewport_width}px viewport",
        )

    def check_contrast_ratio(
        self,
        styles: dict[str, dict[str, Any]],
        node_id: str,
        min_ratio: float = 4.5,
    ) -> InvariantResult:
        """WCAG contrast ratio between foreground and background >= *min_ratio*."""
        inv_id = f"threshold_contrast_{node_id}"
        style = styles.get(node_id)
        if style is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{node_id}' not found in styles",
            )

        fg_str = style.get("color")
        bg_str = style.get("background_color")
        if fg_str is None or bg_str is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Missing color or background_color for '{node_id}'",
            )

        try:
            fg = self._parse_color(fg_str)
            bg = self._parse_color(bg_str)
        except ValueError as exc:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Cannot parse colour: {exc}",
            )

        l1 = self._relative_luminance(*fg)
        l2 = self._relative_luminance(*bg)
        ratio = self._contrast_ratio(l1, l2)

        if ratio >= min_ratio:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                evidence={"contrast_ratio": ratio, "min_ratio": min_ratio},
                message=f"Contrast ratio {ratio:.2f}:1 >= {min_ratio}:1",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"contrast_ratio": ratio, "min_ratio": min_ratio},
            message=f"Contrast ratio {ratio:.2f}:1 < {min_ratio}:1",
        )

    def check_all(
        self,
        invariants: list[Any],
        layout_boxes: dict[str, dict[str, Any]],
        styles: dict[str, dict[str, Any]],
    ) -> list[InvariantResult]:
        """Dispatch each threshold invariant to its check method."""
        results: list[InvariantResult] = []
        for inv in invariants:
            if not isinstance(inv, ThresholdInvariant):
                continue
            prop = inv.property
            if prop == "font_size":
                results.append(
                    self.check_font_size(styles, inv.subject, inv.threshold)
                )
            elif prop == "touch_target":
                results.append(
                    self.check_touch_target(layout_boxes, inv.subject, inv.threshold)
                )
            elif prop == "contrast_ratio":
                results.append(
                    self.check_contrast_ratio(styles, inv.subject, inv.threshold)
                )
            elif prop == "no_horizontal_scroll":
                results.append(
                    self.check_no_horizontal_scroll(layout_boxes, inv.threshold)
                )
            else:
                # Generic threshold: extract value and compare
                results.append(
                    self._check_generic(layout_boxes, styles, inv)
                )
        return results

    # ---- colour helpers ---------------------------------------------------

    def _parse_color(self, color_str: str) -> tuple[int, int, int]:
        """Parse hex (#rrggbb, #rgb), rgb(r,g,b), or named colour → (r, g, b)."""
        s = color_str.strip().lower()

        if s in _NAMED_COLORS:
            return _NAMED_COLORS[s]

        m = _HEX6_RE.match(s)
        if m:
            h = m.group(1)
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

        m = _HEX3_RE.match(s)
        if m:
            h = m.group(1)
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))

        m = _RGB_RE.match(s)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

        raise ValueError(f"Unrecognised colour: '{color_str}'")

    def _relative_luminance(self, r: int, g: int, b: int) -> float:
        """WCAG 2.x relative luminance.  Input 0-255, output 0.0–1.0."""
        def _channel(c: int) -> float:
            c_lin = c / 255.0
            if c_lin <= 0.04045:
                return c_lin / 12.92
            return ((c_lin + 0.055) / 1.055) ** 2.4

        return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)

    def _contrast_ratio(self, l1: float, l2: float) -> float:
        """WCAG contrast ratio: ``(lighter + 0.05) / (darker + 0.05)``."""
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)

    # ---- generic fallback -------------------------------------------------

    def _check_generic(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        styles: dict[str, dict[str, Any]],
        inv: ThresholdInvariant,
    ) -> InvariantResult:
        """Generic threshold check for width, height, or arbitrary style props."""
        inv_id = inv.id
        # Try layout_boxes first
        box = layout_boxes.get(inv.subject)
        val: Optional[float] = None
        if box is not None and inv.property in box:
            try:
                val = float(box[inv.property])
            except (ValueError, TypeError):
                pass
        if val is None:
            style = styles.get(inv.subject)
            if style is not None and inv.property in style:
                try:
                    val = float(style[inv.property])
                except (ValueError, TypeError):
                    pass

        if val is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Cannot resolve '{inv.property}' for '{inv.subject}'",
            )

        satisfied = False
        if inv.relation == "gte":
            satisfied = val >= inv.threshold
        elif inv.relation == "gt":
            satisfied = val > inv.threshold
        elif inv.relation == "lte":
            satisfied = val <= inv.threshold
        elif inv.relation == "lt":
            satisfied = val < inv.threshold

        status = InvariantStatus.SATISFIED if satisfied else InvariantStatus.VIOLATED
        return InvariantResult(
            invariant_id=inv_id,
            status=status,
            evidence={"value": val, "threshold": inv.threshold},
            message=f"{inv.subject}.{inv.property}={val} {'satisfies' if satisfied else 'violates'} {inv.relation} {inv.threshold}",
        )
