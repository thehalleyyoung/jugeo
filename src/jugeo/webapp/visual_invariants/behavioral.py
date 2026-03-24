"""
Behavioral invariant family — hover, focus, error visibility, toggle.

Checks that interactive states produce the expected visual changes.

Part of §3.6 invariant family 4 (behavioral).
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import InvariantResult, InvariantStatus


__all__ = [
    "TriggerKind",
    "BehavioralInvariant",
    "BehavioralChecker",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TriggerKind(str, Enum):
    """Kinds of user interaction triggers."""

    HOVER = "hover"
    FOCUS = "focus"
    CLICK = "click"
    FORM_SUBMIT = "form_submit"
    FORM_ERROR = "form_error"
    SCROLL = "scroll"
    RESIZE = "resize"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class BehavioralInvariant:
    """An expected visual change in response to a trigger."""

    subject: str
    trigger: TriggerKind
    property: str
    expected_value: str
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"behavioral_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "trigger": self.trigger.value,
            "property": self.property,
            "expected_value": self.expected_value,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BehavioralInvariant:
        return cls(
            subject=d["subject"],
            trigger=TriggerKind(d["trigger"]),
            property=d["property"],
            expected_value=d["expected_value"],
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"behavioral_{uuid.uuid4().hex[:8]}"),
        )


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------


def _find_node(dom: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    """DFS search for a node with the given ``id`` in the DOM tree."""
    if dom.get("id") == node_id:
        return dom
    for child in dom.get("children", []):
        found = _find_node(child, node_id)
        if found is not None:
            return found
    return None


def _find_all(dom: dict[str, Any], tag: str | None = None) -> list[dict[str, Any]]:
    """Collect all nodes matching *tag* (or all nodes if *tag* is None)."""
    results: list[dict[str, Any]] = []
    if tag is None or dom.get("tag") == tag:
        results.append(dom)
    for child in dom.get("children", []):
        results.extend(_find_all(child, tag))
    return results


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

_VISUAL_PROPS = [
    "color", "background_color", "border", "box_shadow", "outline",
    "text_decoration", "opacity", "transform", "filter", "cursor",
]


class BehavioralChecker:
    """Checks behavioral invariants — state-change visual effects."""

    # ---- public API -------------------------------------------------------

    def check_hover_distinction(
        self,
        styles_default: dict[str, dict[str, Any]],
        styles_hover: dict[str, dict[str, Any]],
        node_id: str,
    ) -> InvariantResult:
        """At least one visual property differs between default and hover."""
        inv_id = f"behavioral_hover_{node_id}"
        default_style = styles_default.get(node_id, {})
        hover_style = styles_hover.get(node_id, {})

        for prop in _VISUAL_PROPS:
            dv = default_style.get(prop)
            hv = hover_style.get(prop)
            if dv != hv:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.SATISFIED,
                    evidence={"property": prop, "default": dv, "hover": hv},
                    message=f"Hover distinction on '{node_id}': {prop} changed",
                )

        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            message=f"No visual change on hover for '{node_id}'",
        )

    def check_focus_visible(
        self,
        styles_default: dict[str, dict[str, Any]],
        styles_focus: dict[str, dict[str, Any]],
        node_id: str,
    ) -> InvariantResult:
        """Focus state must have a visible indicator."""
        inv_id = f"behavioral_focus_{node_id}"
        default_style = styles_default.get(node_id, {})
        focus_style = styles_focus.get(node_id, {})

        focus_indicators = ["outline", "border", "box_shadow"]
        for prop in focus_indicators:
            dv = default_style.get(prop)
            fv = focus_style.get(prop)
            if dv != fv:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.SATISFIED,
                    evidence={"property": prop, "default": dv, "focus": fv},
                    message=f"Focus visible on '{node_id}': {prop} changed",
                )

        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            message=f"No visible focus indicator for '{node_id}'",
        )

    def check_error_visibility(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        error_node_id: str,
        viewport_height: float,
    ) -> InvariantResult:
        """Error element is within the viewport and has non-zero height."""
        inv_id = f"behavioral_error_{error_node_id}"
        box = layout_boxes.get(error_node_id)
        if box is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{error_node_id}' not found in layout_boxes",
            )

        y = box.get("y", 0)
        h = box.get("height", 0)
        if y < viewport_height and h > 0:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                evidence={"y": y, "height": h, "viewport_height": viewport_height},
                message=f"Error element '{error_node_id}' visible in viewport",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"y": y, "height": h, "viewport_height": viewport_height},
            message=f"Error element '{error_node_id}' not visible (y={y}, h={h})",
        )

    def check_toggle_visibility(
        self,
        before_state: dict[str, dict[str, Any]],
        after_state: dict[str, dict[str, Any]],
        node_id: str,
    ) -> InvariantResult:
        """display or visibility changes between before and after states."""
        inv_id = f"behavioral_toggle_{node_id}"
        before = before_state.get(node_id, {})
        after = after_state.get(node_id, {})

        for prop in ("display", "visibility"):
            bv = before.get(prop)
            av = after.get(prop)
            if bv != av:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.SATISFIED,
                    evidence={"property": prop, "before": bv, "after": av},
                    message=f"Toggle on '{node_id}': {prop} changed from '{bv}' to '{av}'",
                )

        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            message=f"No visibility toggle for '{node_id}'",
        )

    def simulate_trigger(
        self,
        dom: dict[str, Any],
        styles: dict[str, dict[str, Any]],
        trigger: TriggerKind,
        target_node_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Simulate style changes from a trigger.  Returns modified styles dict."""
        new_styles = copy.deepcopy(styles)

        if trigger == TriggerKind.HOVER:
            node_style = new_styles.setdefault(target_node_id, {})
            if not node_style.get("outline"):
                node_style["outline"] = "2px solid blue"
            return new_styles

        if trigger == TriggerKind.FOCUS:
            node_style = new_styles.setdefault(target_node_id, {})
            if not node_style.get("outline"):
                node_style["outline"] = "2px solid orange"
            return new_styles

        if trigger == TriggerKind.CLICK:
            node = _find_node(dom, target_node_id)
            if node is not None:
                for child in node.get("children", []):
                    cls_attr = child.get("attrs", {}).get("class", "")
                    if "dropdown" in cls_attr:
                        child_id = child.get("id", "")
                        if child_id:
                            child_style = new_styles.setdefault(child_id, {})
                            if child_style.get("display") == "none":
                                child_style["display"] = "block"
                            else:
                                child_style["display"] = "none"
            return new_styles

        if trigger == TriggerKind.FORM_SUBMIT or trigger == TriggerKind.FORM_ERROR:
            # Make all elements with class "error" visible
            error_nodes = _find_all(dom)
            for enode in error_nodes:
                cls_attr = enode.get("attrs", {}).get("class", "")
                if "error" in cls_attr:
                    eid = enode.get("id", "")
                    if eid:
                        e_style = new_styles.setdefault(eid, {})
                        e_style["display"] = "block"
                        e_style["visibility"] = "visible"
            return new_styles

        return new_styles

    def check_all(
        self,
        invariants: list[Any],
        dom: dict[str, Any],
        styles: dict[str, dict[str, Any]],
        layout_boxes: dict[str, dict[str, Any]],
    ) -> list[InvariantResult]:
        """Dispatch each behavioral invariant to its check method."""
        results: list[InvariantResult] = []
        for inv in invariants:
            if not isinstance(inv, BehavioralInvariant):
                continue
            triggered_styles = self.simulate_trigger(dom, styles, inv.trigger, inv.subject)
            if inv.trigger == TriggerKind.HOVER:
                results.append(
                    self.check_hover_distinction(styles, triggered_styles, inv.subject)
                )
            elif inv.trigger == TriggerKind.FOCUS:
                results.append(
                    self.check_focus_visible(styles, triggered_styles, inv.subject)
                )
            elif inv.trigger == TriggerKind.FORM_ERROR:
                results.append(
                    self.check_error_visibility(layout_boxes, inv.subject, 800.0)
                )
            elif inv.trigger == TriggerKind.CLICK:
                results.append(
                    self.check_toggle_visibility(styles, triggered_styles, inv.subject)
                )
            else:
                results.append(
                    InvariantResult(
                        invariant_id=inv.id,
                        status=InvariantStatus.NOT_APPLICABLE,
                        message=f"Trigger '{inv.trigger.value}' not simulated",
                    )
                )
        return results
