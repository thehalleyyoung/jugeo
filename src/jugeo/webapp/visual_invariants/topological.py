"""
Topological invariant family — containment, reading order, clustering, occlusion.

Layout boxes are dicts mapping node IDs to geometry:
``{node_id: {"x": int, "y": int, "width": int, "height": int, "z_index": int}}``

Part of §3.6 invariant family 1 (topological).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import InvariantResult, InvariantStatus


__all__ = [
    "ContainmentInvariant",
    "ReadingOrderInvariant",
    "VisualClusterInvariant",
    "NonOcclusionInvariant",
    "TopologicalChecker",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ContainmentInvariant:
    """Subject element must be fully inside container element."""

    subject: str
    container: str
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"containment_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "container": self.container,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContainmentInvariant:
        return cls(
            subject=d["subject"],
            container=d["container"],
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"containment_{uuid.uuid4().hex[:8]}"),
        )


@dataclass
class ReadingOrderInvariant:
    """First element must appear before second in reading order."""

    first: str
    second: str
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"reading_order_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "first": self.first,
            "second": self.second,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReadingOrderInvariant:
        return cls(
            first=d["first"],
            second=d["second"],
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"reading_order_{uuid.uuid4().hex[:8]}"),
        )


@dataclass
class VisualClusterInvariant:
    """All members must be within *max_gap* pixels of each other."""

    members_selector: list[str]
    container_selector: str
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"cluster_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "members_selector": list(self.members_selector),
            "container_selector": self.container_selector,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualClusterInvariant:
        return cls(
            members_selector=list(d["members_selector"]),
            container_selector=d["container_selector"],
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"cluster_{uuid.uuid4().hex[:8]}"),
        )


@dataclass
class NonOcclusionInvariant:
    """Subject element must not be occluded by a higher-z-index element."""

    subject: str
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"non_occlusion_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NonOcclusionInvariant:
        return cls(
            subject=d["subject"],
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"non_occlusion_{uuid.uuid4().hex[:8]}"),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rects_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return ``True`` if two layout boxes (with x, y, width, height) overlap."""
    if a["x"] + a["width"] <= b["x"]:
        return False
    if b["x"] + b["width"] <= a["x"]:
        return False
    if a["y"] + a["height"] <= b["y"]:
        return False
    if b["y"] + b["height"] <= a["y"]:
        return False
    return True


def _box_center(box: dict[str, Any]) -> tuple[float, float]:
    """Return the centre (cx, cy) of a layout box."""
    return (
        box["x"] + box["width"] / 2.0,
        box["y"] + box["height"] / 2.0,
    )


def _euclidean(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    return (dx * dx + dy * dy) ** 0.5


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class TopologicalChecker:
    """Checks topological invariants against a set of layout boxes."""

    def check_containment(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        subject_id: str,
        container_id: str,
    ) -> InvariantResult:
        """Return SATISFIED if *subject_id* is fully inside *container_id*."""
        if subject_id not in layout_boxes or container_id not in layout_boxes:
            missing = subject_id if subject_id not in layout_boxes else container_id
            return InvariantResult(
                invariant_id=f"containment_{subject_id}_{container_id}",
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{missing}' not found in layout_boxes",
            )

        s = layout_boxes[subject_id]
        c = layout_boxes[container_id]

        inside = (
            s["x"] >= c["x"]
            and s["y"] >= c["y"]
            and s["x"] + s["width"] <= c["x"] + c["width"]
            and s["y"] + s["height"] <= c["y"] + c["height"]
        )

        if inside:
            return InvariantResult(
                invariant_id=f"containment_{subject_id}_{container_id}",
                status=InvariantStatus.SATISFIED,
                message=f"'{subject_id}' is fully contained in '{container_id}'",
            )
        return InvariantResult(
            invariant_id=f"containment_{subject_id}_{container_id}",
            status=InvariantStatus.VIOLATED,
            evidence={"subject": s, "container": c},
            message=f"'{subject_id}' is NOT fully contained in '{container_id}'",
        )

    def check_reading_order(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        first_id: str,
        second_id: str,
    ) -> InvariantResult:
        """Return SATISFIED if *first_id* appears before *second_id* in reading order."""
        if first_id not in layout_boxes or second_id not in layout_boxes:
            missing = first_id if first_id not in layout_boxes else second_id
            return InvariantResult(
                invariant_id=f"reading_order_{first_id}_{second_id}",
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{missing}' not found in layout_boxes",
            )

        f = layout_boxes[first_id]
        s = layout_boxes[second_id]

        same_line_tolerance = 5
        if abs(f["y"] - s["y"]) < same_line_tolerance:
            ordered = f["x"] < s["x"]
        else:
            ordered = f["y"] < s["y"]

        if ordered:
            return InvariantResult(
                invariant_id=f"reading_order_{first_id}_{second_id}",
                status=InvariantStatus.SATISFIED,
                message=f"'{first_id}' precedes '{second_id}' in reading order",
            )
        return InvariantResult(
            invariant_id=f"reading_order_{first_id}_{second_id}",
            status=InvariantStatus.VIOLATED,
            evidence={"first": f, "second": s},
            message=f"'{first_id}' does NOT precede '{second_id}' in reading order",
        )

    def check_visual_cluster(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        member_ids: list[str],
        max_gap: float = 50.0,
    ) -> InvariantResult:
        """Return SATISFIED if all members are within *max_gap* of each other."""
        missing = [m for m in member_ids if m not in layout_boxes]
        if missing:
            return InvariantResult(
                invariant_id="visual_cluster",
                status=InvariantStatus.UNKNOWN,
                message=f"Missing nodes: {missing}",
            )

        if len(member_ids) < 2:
            return InvariantResult(
                invariant_id="visual_cluster",
                status=InvariantStatus.SATISFIED,
                message="Fewer than 2 members — trivially clustered",
            )

        centres = {mid: _box_center(layout_boxes[mid]) for mid in member_ids}
        for i, a in enumerate(member_ids):
            for b in member_ids[i + 1:]:
                dist = _euclidean(centres[a], centres[b])
                if dist > max_gap:
                    return InvariantResult(
                        invariant_id="visual_cluster",
                        status=InvariantStatus.VIOLATED,
                        evidence={"pair": (a, b), "distance": dist, "max_gap": max_gap},
                        message=(
                            f"'{a}' and '{b}' are {dist:.1f}px apart "
                            f"(max_gap={max_gap})"
                        ),
                    )

        return InvariantResult(
            invariant_id="visual_cluster",
            status=InvariantStatus.SATISFIED,
            message=f"All {len(member_ids)} members within {max_gap}px",
        )

    def check_non_occlusion(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        subject_id: str,
    ) -> InvariantResult:
        """Return SATISFIED if no higher-z-index element overlaps *subject_id*."""
        if subject_id not in layout_boxes:
            return InvariantResult(
                invariant_id=f"non_occlusion_{subject_id}",
                status=InvariantStatus.UNKNOWN,
                message=f"Node '{subject_id}' not found in layout_boxes",
            )

        subj = layout_boxes[subject_id]
        subj_z = subj.get("z_index", 0)

        for nid, box in layout_boxes.items():
            if nid == subject_id:
                continue
            other_z = box.get("z_index", 0)
            if other_z > subj_z and _rects_overlap(subj, box):
                return InvariantResult(
                    invariant_id=f"non_occlusion_{subject_id}",
                    status=InvariantStatus.VIOLATED,
                    evidence={"occluder": nid, "occluder_z": other_z, "subject_z": subj_z},
                    message=f"'{subject_id}' is occluded by '{nid}' (z={other_z} > {subj_z})",
                )

        return InvariantResult(
            invariant_id=f"non_occlusion_{subject_id}",
            status=InvariantStatus.SATISFIED,
            message=f"'{subject_id}' is not occluded",
        )

    def check_all(
        self,
        invariants: list[Any],
        layout_boxes: dict[str, dict[str, Any]],
    ) -> list[InvariantResult]:
        """Dispatch each invariant to its corresponding check method."""
        results: list[InvariantResult] = []
        for inv in invariants:
            if isinstance(inv, ContainmentInvariant):
                results.append(
                    self.check_containment(layout_boxes, inv.subject, inv.container)
                )
            elif isinstance(inv, ReadingOrderInvariant):
                results.append(
                    self.check_reading_order(layout_boxes, inv.first, inv.second)
                )
            elif isinstance(inv, VisualClusterInvariant):
                results.append(
                    self.check_visual_cluster(layout_boxes, inv.members_selector)
                )
            elif isinstance(inv, NonOcclusionInvariant):
                results.append(
                    self.check_non_occlusion(layout_boxes, inv.subject)
                )
        return results
