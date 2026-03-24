"""
Proportional invariant family — ratio and uniformity checks.

Ensures that proportional relationships between elements are maintained
across device classes.

Part of §3.6 invariant family 2 (proportional).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import InvariantResult, InvariantStatus


__all__ = [
    "ProportionalInvariant",
    "UniformityInvariant",
    "ProportionalChecker",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProportionalInvariant:
    """A proportional relationship between two element properties.

    ``subject.property <relation> factor * reference.reference_property``
    """

    subject: str
    property: str
    relation: str  # "eq", "lt", "lte", "gt", "gte", "approx"
    reference: str
    reference_property: str
    factor: float = 1.0
    tolerance: float = 0.05
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"proportional_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "property": self.property,
            "relation": self.relation,
            "reference": self.reference,
            "reference_property": self.reference_property,
            "factor": self.factor,
            "tolerance": self.tolerance,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProportionalInvariant:
        return cls(
            subject=d["subject"],
            property=d["property"],
            relation=d["relation"],
            reference=d["reference"],
            reference_property=d["reference_property"],
            factor=d.get("factor", 1.0),
            tolerance=d.get("tolerance", 0.05),
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"proportional_{uuid.uuid4().hex[:8]}"),
        )


@dataclass
class UniformityInvariant:
    """All subjects must have approximately the same value for *property*."""

    subjects_selector: list[str]
    property: str
    tolerance: float = 0.05
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"uniformity_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subjects_selector": list(self.subjects_selector),
            "property": self.property,
            "tolerance": self.tolerance,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UniformityInvariant:
        return cls(
            subjects_selector=list(d["subjects_selector"]),
            property=d["property"],
            tolerance=d.get("tolerance", 0.05),
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"uniformity_{uuid.uuid4().hex[:8]}"),
        )


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class ProportionalChecker:
    """Checks proportional and uniformity invariants."""

    # ---- public API -------------------------------------------------------

    def check_proportion(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        styles: dict[str, dict[str, Any]],
        subject_id: str,
        property: str,
        relation: str,
        ref_id: str,
        ref_property: str,
        factor: float = 1.0,
        tolerance: float = 0.05,
    ) -> InvariantResult:
        """Check proportional relationship between two elements."""
        inv_id = f"proportional_{subject_id}_{ref_id}"

        subj_val = self._get_property_value(layout_boxes, styles, subject_id, property)
        ref_val = self._get_property_value(layout_boxes, styles, ref_id, ref_property)

        if subj_val is None or ref_val is None:
            missing = subject_id if subj_val is None else ref_id
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Cannot resolve property for '{missing}'",
            )

        target = factor * ref_val
        satisfied = self._evaluate_relation(subj_val, relation, target, tolerance)

        if satisfied:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                evidence={"subject_value": subj_val, "target": target},
                message=(
                    f"{subject_id}.{property}={subj_val} {relation} "
                    f"{factor}*{ref_id}.{ref_property}={target}"
                ),
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"subject_value": subj_val, "target": target},
            message=(
                f"{subject_id}.{property}={subj_val} NOT {relation} "
                f"{factor}*{ref_id}.{ref_property}={target}"
            ),
        )

    def check_uniformity(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        styles: dict[str, dict[str, Any]],
        ids: list[str],
        property: str,
        tolerance: float = 0.05,
    ) -> InvariantResult:
        """Check that all elements have similar values for *property*."""
        inv_id = "uniformity"
        values: list[float] = []
        for nid in ids:
            val = self._get_property_value(layout_boxes, styles, nid, property)
            if val is None:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.UNKNOWN,
                    message=f"Cannot resolve '{property}' for '{nid}'",
                )
            values.append(val)

        if len(values) < 2:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                message="Fewer than 2 elements — trivially uniform",
            )

        mean_val = sum(values) / len(values)
        if mean_val == 0:
            all_zero = all(v == 0 for v in values)
            if all_zero:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.SATISFIED,
                    message="All values are zero",
                )
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"values": values},
                message="Mean is zero but not all values are zero",
            )

        for i, val in enumerate(values):
            deviation = abs(val - mean_val) / abs(mean_val)
            if deviation > tolerance:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.VIOLATED,
                    evidence={
                        "node": ids[i],
                        "value": val,
                        "mean": mean_val,
                        "deviation": deviation,
                    },
                    message=(
                        f"'{ids[i]}' has {property}={val}, "
                        f"mean={mean_val:.2f}, deviation={deviation:.2%}"
                    ),
                )

        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            evidence={"mean": mean_val, "count": len(values)},
            message=f"All {len(values)} elements within {tolerance:.0%} of mean={mean_val:.2f}",
        )

    def check_all(
        self,
        invariants: list[Any],
        layout_boxes: dict[str, dict[str, Any]],
        styles: dict[str, dict[str, Any]],
    ) -> list[InvariantResult]:
        """Dispatch each invariant to its corresponding checker."""
        results: list[InvariantResult] = []
        for inv in invariants:
            if isinstance(inv, ProportionalInvariant):
                results.append(
                    self.check_proportion(
                        layout_boxes,
                        styles,
                        inv.subject,
                        inv.property,
                        inv.relation,
                        inv.reference,
                        inv.reference_property,
                        inv.factor,
                        inv.tolerance,
                    )
                )
            elif isinstance(inv, UniformityInvariant):
                results.append(
                    self.check_uniformity(
                        layout_boxes,
                        styles,
                        inv.subjects_selector,
                        inv.property,
                        inv.tolerance,
                    )
                )
        return results

    # ---- helpers ----------------------------------------------------------

    def _get_property_value(
        self,
        layout_boxes: dict[str, dict[str, Any]],
        styles: dict[str, dict[str, Any]],
        node_id: str,
        property: str,
    ) -> Optional[float]:
        """Extract a numeric value for *property* from layout or styles."""
        layout_keys = {"x", "y", "width", "height", "z_index"}
        if property in layout_keys:
            box = layout_boxes.get(node_id)
            if box is None:
                return None
            raw = box.get(property)
            return float(raw) if raw is not None else None

        style = styles.get(node_id)
        if style is None:
            return None
        raw = style.get(property)
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _evaluate_relation(
        value: float, relation: str, target: float, tolerance: float
    ) -> bool:
        if relation == "eq":
            return value == target
        if relation == "lt":
            return value < target
        if relation == "lte":
            return value <= target
        if relation == "gt":
            return value > target
        if relation == "gte":
            return value >= target
        if relation == "approx":
            if target == 0:
                return abs(value) <= tolerance
            return abs(value - target) / abs(target) <= tolerance
        return False
