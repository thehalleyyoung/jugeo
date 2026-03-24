"""Interaction model — events, traces, and accessibility checking.

Standalone module; imports only from sibling submodules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from jugeo.webapp.rendering.models import InteractiveZone, VisualElement
from jugeo.webapp.rendering.visual_site import VisualSite


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════

class InteractionKind(str, Enum):
    """Type of user interaction."""

    CLICK = "click"
    HOVER = "hover"
    FOCUS = "focus"
    BLUR = "blur"
    SCROLL = "scroll"
    RESIZE = "resize"
    KEYPRESS = "keypress"
    TOUCH = "touch"


# ═══════════════════════════════════════════════════════════════════════════
# InteractionEvent
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class InteractionEvent:
    """A single interaction event."""

    kind: InteractionKind
    target_node_id: str
    position: tuple = field(default_factory=lambda: (0.0, 0.0))
    timestamp: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value if isinstance(self.kind, InteractionKind) else self.kind,
            "target_node_id": self.target_node_id,
            "position": list(self.position),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> InteractionEvent:
        d = dict(data)
        if "kind" in d:
            d["kind"] = InteractionKind(d["kind"])
        if "position" in d:
            d["position"] = tuple(d["position"])
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════════════
# InteractionTrace
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class InteractionTrace:
    """An ordered sequence of interaction events and resulting states."""

    events: list = field(default_factory=list)
    resulting_states: list = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        """Elapsed time from first to last event."""
        if len(self.events) < 2:
            return 0.0
        first = self.events[0]
        last = self.events[-1]
        t0 = first.timestamp if hasattr(first, "timestamp") else first.get("timestamp", 0)
        t1 = last.timestamp if hasattr(last, "timestamp") else last.get("timestamp", 0)
        return t1 - t0

    def to_dict(self) -> dict:
        return {
            "events": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.events],
            "resulting_states": list(self.resulting_states),
        }

    @classmethod
    def from_dict(cls, data: dict) -> InteractionTrace:
        events = [InteractionEvent.from_dict(e) for e in data.get("events", [])]
        return cls(events=events, resulting_states=data.get("resulting_states", []))


# ═══════════════════════════════════════════════════════════════════════════
# InteractionSimulator
# ═══════════════════════════════════════════════════════════════════════════

class InteractionSimulator:
    """Simulates user interactions on a VisualSite."""

    def simulate_click(self, visual: VisualSite, x: float, y: float) -> dict:
        """Simulate a click at (x, y)."""
        hits = visual.elements_at_point(x, y)
        target_ids = [el.region.node_id for el in hits if el.region.node_id]
        return {
            "event": "click",
            "position": (x, y),
            "targets": target_ids,
            "hit_count": len(hits),
        }

    def simulate_hover(self, visual: VisualSite, node_id: str) -> dict:
        """Simulate hovering over a node."""
        zones = visual.interactive_zones()
        matched = [z for z in zones if z.node_id == node_id]
        return {
            "event": "hover",
            "node_id": node_id,
            "has_handler": len(matched) > 0,
        }

    def simulate_focus(self, visual: VisualSite, node_id: str) -> dict:
        """Simulate focusing on a node."""
        zones = visual.interactive_zones()
        matched = [z for z in zones if z.node_id == node_id]
        return {
            "event": "focus",
            "node_id": node_id,
            "focusable": len(matched) > 0,
        }

    def simulate_form_submit(
        self, visual: VisualSite, form_id: str, data: dict
    ) -> dict:
        """Simulate form submission."""
        zones = visual.interactive_zones()
        form_zones = [z for z in zones if z.node_id == form_id]
        return {
            "event": "form_submit",
            "form_id": form_id,
            "data": dict(data),
            "found": len(form_zones) > 0,
        }

    def trace_user_flow(
        self, visual: VisualSite, events: list
    ) -> InteractionTrace:
        """Execute a sequence of events and return a trace."""
        trace_events: list = []
        states: list = []
        t = 0.0
        for evt in events:
            if isinstance(evt, InteractionEvent):
                trace_events.append(evt)
            elif isinstance(evt, dict):
                kind = InteractionKind(evt.get("kind", "click"))
                trace_events.append(InteractionEvent(
                    kind=kind,
                    target_node_id=evt.get("target_node_id", ""),
                    position=tuple(evt.get("position", (0.0, 0.0))),
                    timestamp=evt.get("timestamp", t),
                    metadata=evt.get("metadata", {}),
                ))
            t += 100.0  # 100 ms between events
            states.append({"time": t, "event_count": len(trace_events)})
        return InteractionTrace(events=trace_events, resulting_states=states)


# ═══════════════════════════════════════════════════════════════════════════
# InteractionAccessibilityChecker
# ═══════════════════════════════════════════════════════════════════════════

class InteractionAccessibilityChecker:
    """Checks accessibility of interactive elements."""

    def check_keyboard_navigable(self, visual: VisualSite) -> list:
        """Check that interactive zones are keyboard-navigable."""
        issues: list = []
        zones = visual.interactive_zones()
        for zone in zones:
            if "click" in zone.event_types and "keypress" not in zone.event_types:
                issues.append({
                    "type": "not_keyboard_navigable",
                    "node_id": zone.node_id,
                    "events": list(zone.event_types),
                })
        return issues

    def check_focus_visible(self, visual: VisualSite) -> list:
        """Check that focused elements have visible indicators."""
        issues: list = []
        zones = visual.interactive_zones()
        for zone in zones:
            if "focus" not in zone.event_types and "click" in zone.event_types:
                issues.append({
                    "type": "no_focus_indicator",
                    "node_id": zone.node_id,
                })
        return issues

    def check_touch_targets(self, visual: VisualSite) -> list:
        """Check that touch targets meet minimum size requirements (44x44)."""
        issues: list = []
        zones = visual.interactive_zones()
        for zone in zones:
            bx, by, bw, bh = zone.bbox
            if bw < 44 or bh < 44:
                issues.append({
                    "type": "touch_target_too_small",
                    "node_id": zone.node_id,
                    "width": bw,
                    "height": bh,
                    "minimum": 44,
                })
        return issues
