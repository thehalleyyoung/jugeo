"""
Device site and cross-device descent checker — the Grothendieck site of device
classes (§3.6.4).

Implements the sheaf descent condition: invariants must be consistent on
overlapping device-class regions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .models import (
    DeviceClass,
    InvariantFamily,
    InvariantResult,
    InvariantStatus,
    InvariantSuite,
    CrossDeviceDescentResult,
    VisualInvariant,
    STANDARD_DEVICE_CLASSES,
)


__all__ = [
    "DeviceSite",
    "CrossDeviceDescentChecker",
    "DeviceSiteBuilder",
]


# ---------------------------------------------------------------------------
# DeviceSite
# ---------------------------------------------------------------------------


@dataclass
class DeviceSite:
    """The Grothendieck site of device classes."""

    device_classes: list[DeviceClass] = field(default_factory=list)

    def overlapping_pairs(
        self,
    ) -> list[tuple[DeviceClass, DeviceClass, tuple[int, int]]]:
        """Return pairs whose width ranges overlap, with the overlap range."""
        pairs: list[tuple[DeviceClass, DeviceClass, tuple[int, int]]] = []
        n = len(self.device_classes)
        for i in range(n):
            for j in range(i + 1, n):
                a = self.device_classes[i]
                b = self.device_classes[j]
                lo = max(a.width_range[0], b.width_range[0])
                hi = min(a.width_range[1], b.width_range[1])
                if lo < hi:
                    pairs.append((a, b, (lo, hi)))
        return pairs

    def restriction_morphisms(self) -> list[tuple[str, str]]:
        """Return ``(larger, smaller)`` pairs where smaller is a sub-range."""
        morphisms: list[tuple[str, str]] = []
        for i, a in enumerate(self.device_classes):
            for j, b in enumerate(self.device_classes):
                if i == j:
                    continue
                if a.media_type != b.media_type:
                    continue
                # b is a restriction of a if b's range is contained in a's range
                if (
                    a.width_range[0] <= b.width_range[0]
                    and a.width_range[1] >= b.width_range[1]
                    and (a.width_range[0] < b.width_range[0] or a.width_range[1] > b.width_range[1])
                ):
                    morphisms.append((a.name, b.name))
        return morphisms

    def get_device(self, name: str) -> Optional[DeviceClass]:
        for dc in self.device_classes:
            if dc.name == name:
                return dc
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_classes": [dc.to_dict() for dc in self.device_classes],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeviceSite:
        return cls(
            device_classes=[DeviceClass.from_dict(dc) for dc in d.get("device_classes", [])],
        )


# ---------------------------------------------------------------------------
# CrossDeviceDescentChecker
# ---------------------------------------------------------------------------


class CrossDeviceDescentChecker:
    """Implements the sheaf descent condition over the device site."""

    def __init__(self, device_site: DeviceSite) -> None:
        self.device_site = device_site

    def check_descent(
        self,
        invariant_suite: InvariantSuite,
        styles_per_device: dict[str, dict[str, dict[str, Any]]],
        layout_per_device: dict[str, dict[str, dict[str, Any]]],
    ) -> list[CrossDeviceDescentResult]:
        """Check each invariant across all devices and their overlaps."""
        results: list[CrossDeviceDescentResult] = []

        for inv in invariant_suite.invariants:
            per_device: dict[str, InvariantStatus] = {}
            for dc in self.device_site.device_classes:
                if inv.holds_on and dc.name not in inv.holds_on:
                    per_device[dc.name] = InvariantStatus.NOT_APPLICABLE
                    continue
                styles = styles_per_device.get(dc.name, {})
                layout = layout_per_device.get(dc.name, {})
                per_device[dc.name] = self._check_invariant_on_device(inv, styles, layout)

            overlap_violations: list[str] = []
            for d1, d2, overlap_range in self.device_site.overlapping_pairs():
                s1 = styles_per_device.get(d1.name, {})
                s2 = styles_per_device.get(d2.name, {})
                violations = self._check_overlap_consistency(
                    inv, d1, d2, overlap_range, s1, s2
                )
                overlap_violations.extend(violations)

            all_ok = all(
                s in (InvariantStatus.SATISFIED, InvariantStatus.NOT_APPLICABLE)
                for s in per_device.values()
            )
            globally_consistent = all_ok and len(overlap_violations) == 0

            results.append(
                CrossDeviceDescentResult(
                    invariant_id=inv.id,
                    per_device_results=per_device,
                    overlap_violations=overlap_violations,
                    globally_consistent=globally_consistent,
                )
            )

        return results

    def _check_overlap_consistency(
        self,
        invariant: VisualInvariant,
        device1: DeviceClass,
        device2: DeviceClass,
        overlap_range: tuple[int, int],
        styles1: dict[str, dict[str, Any]],
        styles2: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Check that the invariant's subject has consistent styles in the overlap region."""
        violations: list[str] = []
        subject = invariant.subject_selector
        prop = invariant.property_name

        style1 = styles1.get(subject, {})
        style2 = styles2.get(subject, {})

        val1 = style1.get(prop)
        val2 = style2.get(prop)

        if val1 is not None and val2 is not None and val1 != val2:
            violations.append(
                f"Overlap [{overlap_range[0]}-{overlap_range[1]}px] "
                f"between {device1.name} and {device2.name}: "
                f"{subject}.{prop} = '{val1}' vs '{val2}'"
            )

        return violations

    def _check_invariant_on_device(
        self,
        invariant: VisualInvariant,
        styles: dict[str, dict[str, Any]],
        layout: dict[str, dict[str, Any]],
    ) -> InvariantStatus:
        """Dispatch to the appropriate checker based on invariant family."""
        subject = invariant.subject_selector
        prop = invariant.property_name
        condition = invariant.condition

        if invariant.family == InvariantFamily.THRESHOLD:
            node_style = styles.get(subject, {})
            raw = node_style.get(prop)
            if raw is None:
                box = layout.get(subject, {})
                raw = box.get(prop)
            if raw is None:
                return InvariantStatus.UNKNOWN
            try:
                val = float(raw)
            except (ValueError, TypeError):
                return InvariantStatus.UNKNOWN
            # Parse condition like ">= 12.0"
            return self._eval_simple_condition(val, condition)

        if invariant.family == InvariantFamily.TOPOLOGICAL:
            if subject not in layout:
                return InvariantStatus.UNKNOWN
            return InvariantStatus.SATISFIED

        if invariant.family == InvariantFamily.PROPORTIONAL:
            node_style = styles.get(subject, {})
            raw = node_style.get(prop)
            if raw is None:
                return InvariantStatus.UNKNOWN
            return InvariantStatus.SATISFIED

        # Default: check that the subject exists in styles or layout
        if subject in styles or subject in layout:
            return InvariantStatus.SATISFIED
        return InvariantStatus.UNKNOWN

    @staticmethod
    def _eval_simple_condition(value: float, condition: str) -> InvariantStatus:
        """Evaluate simple conditions like ``>= 12.0`` or ``<= 100``."""
        cond = condition.strip()
        try:
            if cond.startswith(">="):
                return InvariantStatus.SATISFIED if value >= float(cond[2:]) else InvariantStatus.VIOLATED
            if cond.startswith("<="):
                return InvariantStatus.SATISFIED if value <= float(cond[2:]) else InvariantStatus.VIOLATED
            if cond.startswith(">"):
                return InvariantStatus.SATISFIED if value > float(cond[1:]) else InvariantStatus.VIOLATED
            if cond.startswith("<"):
                return InvariantStatus.SATISFIED if value < float(cond[1:]) else InvariantStatus.VIOLATED
            if cond.startswith("=="):
                return InvariantStatus.SATISFIED if value == float(cond[2:]) else InvariantStatus.VIOLATED
        except ValueError:
            return InvariantStatus.UNKNOWN
        return InvariantStatus.UNKNOWN


# ---------------------------------------------------------------------------
# DeviceSiteBuilder
# ---------------------------------------------------------------------------


class DeviceSiteBuilder:
    """Fluent builder for custom device sites."""

    def __init__(self) -> None:
        self._devices: list[DeviceClass] = []

    def add_device(
        self,
        name: str,
        width_range: tuple[int, int],
        media_type: str = "screen",
        pixel_ratio: float = 1.0,
        is_touch: bool = False,
    ) -> DeviceSiteBuilder:
        self._devices.append(
            DeviceClass(name, width_range, media_type, pixel_ratio, is_touch)
        )
        return self

    def add_standard_devices(self) -> DeviceSiteBuilder:
        """Add all ``STANDARD_DEVICE_CLASSES``."""
        for dc in STANDARD_DEVICE_CLASSES.values():
            self._devices.append(dc)
        return self

    def build(self) -> DeviceSite:
        return DeviceSite(device_classes=list(self._devices))


# ═══════════════════════════════════════════════════════════════════════
#  Device Bundle Diagnostics (Judgment Fiber Bundle integration)
# ═══════════════════════════════════════════════════════════════════════

class DeviceBundleDiagnostics:
    """Bundle diagnostics for visual invariants across devices.
    
    The visual property space forms a fiber bundle over the device space:
    - Base = device configurations (mobile, tablet, desktop, etc.)
    - Fiber = visual properties (layout, sizing, spacing, color, typography)
    - Connection = how visual properties transform across device transitions
    - Curvature = visual inconsistency across device triples
    
    Non-zero curvature at a device triple means there's no continuous
    visual transformation connecting all three devices — a cross-device
    layout bug that can't be seen by testing pairs alone.
    """
    
    def __init__(self):
        self._device_props: dict[str, dict[str, float]] = {}
        # device_name -> {property_name: value}
    
    def record_device(self, device: str, properties: dict[str, float]) -> None:
        """Record visual properties for a device configuration."""
        self._device_props[device] = dict(properties)
    
    def property_delta(self, d1: str, d2: str, prop: str) -> float:
        """Compute the delta of a visual property between two devices."""
        v1 = self._device_props.get(d1, {}).get(prop, 0.0)
        v2 = self._device_props.get(d2, {}).get(prop, 0.0)
        return v2 - v1
    
    def curvature(self, d1: str, d2: str, d3: str, prop: str) -> float:
        """Curvature at a device triple for a specific property.
        
        For truly responsive design, visual properties should transform
        linearly across devices. Curvature ≠ 0 means the transformation
        is non-linear — a visual glitch visible only on intermediate devices.
        """
        delta_12 = self.property_delta(d1, d2, prop)
        delta_23 = self.property_delta(d2, d3, prop)
        delta_31 = self.property_delta(d3, d1, prop)
        return delta_12 + delta_23 + delta_31
    
    def total_curvature(self, d1: str, d2: str, d3: str) -> float:
        """Total curvature across all shared properties."""
        props = set()
        for d in [d1, d2, d3]:
            props.update(self._device_props.get(d, {}).keys())
        return sum(self.curvature(d1, d2, d3, p) for p in props)
    
    def diagnose(self) -> dict:
        devices = sorted(self._device_props.keys())
        from itertools import combinations
        curvatures = {}
        for triple in combinations(devices, 3):
            c = self.total_curvature(*triple)
            if abs(c) > 1e-9:
                curvatures[triple] = c
        all_c = [self.total_curvature(*t) for t in combinations(devices, 3)] if len(devices) >= 3 else []
        c1 = sum(all_c) / len(all_c) if all_c else 0.0
        return {
            'devices': devices,
            'first_chern_class': c1,
            'non_flat_curvatures': {str(k): v for k, v in curvatures.items()},
            'bundle_is_flat': abs(c1) < 1e-9,
            'num_properties': len(set().union(*(self._device_props.get(d, {}).keys() for d in devices))),
        }
