"""
Core models for the visual_invariants module.

Defines the fundamental data types used across all invariant families:
device classes, invariant results, visual invariants, suites, and
cross-device descent results.

Part of §3.6 of GEOMETRY_OF_WEB_APPLICATIONS.md.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


__all__ = [
    "InvariantFamily",
    "InvariantStatus",
    "DeviceClass",
    "STANDARD_DEVICE_CLASSES",
    "InvariantResult",
    "VisualInvariant",
    "InvariantSuite",
    "CrossDeviceDescentResult",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class InvariantFamily(str, Enum):
    """The six invariant families from §3.6."""

    TOPOLOGICAL = "topological"
    PROPORTIONAL = "proportional"
    THRESHOLD = "threshold"
    BEHAVIORAL = "behavioral"
    STRUCTURAL = "structural"
    CONDITIONAL_DEVICE = "conditional_device"


class InvariantStatus(str, Enum):
    """Outcome of checking a single invariant."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


# ---------------------------------------------------------------------------
# DeviceClass
# ---------------------------------------------------------------------------


@dataclass
class DeviceClass:
    """A device class in the device site (§3.6.4).

    Attributes:
        name: Human-readable device class identifier.
        width_range: ``(min_width, max_width)`` in CSS pixels.
        media_type: One of ``"screen"``, ``"print"``, ``"speech"``.
        pixel_ratio: Device pixel ratio (1.0 standard, 2.0 retina).
        is_touch: Whether the device supports touch input.
    """

    name: str
    width_range: tuple[int, int]
    media_type: str = "screen"
    pixel_ratio: float = 1.0
    is_touch: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width_range": list(self.width_range),
            "media_type": self.media_type,
            "pixel_ratio": self.pixel_ratio,
            "is_touch": self.is_touch,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeviceClass:
        return cls(
            name=d["name"],
            width_range=tuple(d["width_range"]),
            media_type=d.get("media_type", "screen"),
            pixel_ratio=d.get("pixel_ratio", 1.0),
            is_touch=d.get("is_touch", False),
        )


# ---------------------------------------------------------------------------
# Standard device classes
# ---------------------------------------------------------------------------


STANDARD_DEVICE_CLASSES: dict[str, DeviceClass] = {
    "mobile_portrait": DeviceClass(
        "mobile_portrait", (320, 480), "screen", 2.0, True
    ),
    "mobile_landscape": DeviceClass(
        "mobile_landscape", (480, 768), "screen", 2.0, True
    ),
    "tablet": DeviceClass(
        "tablet", (768, 1024), "screen", 1.5, True
    ),
    "desktop": DeviceClass(
        "desktop", (1024, 1920), "screen", 1.0, False
    ),
    "ultrawide": DeviceClass(
        "ultrawide", (1920, 3840), "screen", 1.0, False
    ),
    "print": DeviceClass(
        "print", (0, 9999), "print", 1.0, False
    ),
    "screen_reader": DeviceClass(
        "screen_reader", (0, 9999), "speech", 1.0, False
    ),
}


# ---------------------------------------------------------------------------
# InvariantResult
# ---------------------------------------------------------------------------


@dataclass
class InvariantResult:
    """The outcome of checking one invariant on one device.

    Attributes:
        invariant_id: ID of the invariant that was checked.
        status: Outcome status.
        evidence: Arbitrary evidence dict (values observed, etc.).
        counterexample_device: Device name that witnessed a violation (if any).
        message: Human-readable explanation.
    """

    invariant_id: str
    status: InvariantStatus
    evidence: dict[str, Any] = field(default_factory=dict)
    counterexample_device: Optional[str] = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "status": self.status.value,
            "evidence": self.evidence,
            "counterexample_device": self.counterexample_device,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InvariantResult:
        return cls(
            invariant_id=d["invariant_id"],
            status=InvariantStatus(d["status"]),
            evidence=d.get("evidence", {}),
            counterexample_device=d.get("counterexample_device"),
            message=d.get("message", ""),
        )


# ---------------------------------------------------------------------------
# VisualInvariant
# ---------------------------------------------------------------------------


@dataclass
class VisualInvariant:
    """A single visual invariant declaration.

    Attributes:
        id: Unique identifier.
        family: Which of the six invariant families this belongs to.
        description: Human-readable description.
        subject_selector: CSS-like selector or node ID.
        property_name: The visual property being constrained.
        condition: The constraint expression (e.g. ``">= 44px"``).
        holds_on: Device class names; empty list means all devices.
    """

    id: str
    family: InvariantFamily
    description: str
    subject_selector: str
    property_name: str
    condition: str
    holds_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family.value,
            "description": self.description,
            "subject_selector": self.subject_selector,
            "property_name": self.property_name,
            "condition": self.condition,
            "holds_on": list(self.holds_on),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualInvariant:
        return cls(
            id=d["id"],
            family=InvariantFamily(d["family"]),
            description=d["description"],
            subject_selector=d["subject_selector"],
            property_name=d["property_name"],
            condition=d["condition"],
            holds_on=d.get("holds_on", []),
        )


# ---------------------------------------------------------------------------
# InvariantSuite
# ---------------------------------------------------------------------------


@dataclass
class InvariantSuite:
    """A named collection of visual invariants to check together.

    Attributes:
        id: Suite identifier.
        invariants: Ordered list of invariants in this suite.
        description: Human-readable description.
    """

    id: str
    invariants: list[VisualInvariant] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "invariants": [inv.to_dict() for inv in self.invariants],
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InvariantSuite:
        return cls(
            id=d["id"],
            invariants=[VisualInvariant.from_dict(i) for i in d.get("invariants", [])],
            description=d.get("description", ""),
        )


# ---------------------------------------------------------------------------
# CrossDeviceDescentResult
# ---------------------------------------------------------------------------


@dataclass
class CrossDeviceDescentResult:
    """Result of the sheaf descent check across devices (§3.6.4).

    Attributes:
        invariant_id: Which invariant was checked.
        per_device_results: ``{device_name: status}`` for each device.
        overlap_violations: Messages about overlapping-device inconsistencies.
        globally_consistent: ``True`` when all devices agree AND overlaps are
            consistent — the sheaf condition holds.
    """

    invariant_id: str
    per_device_results: dict[str, InvariantStatus] = field(default_factory=dict)
    overlap_violations: list[str] = field(default_factory=list)
    globally_consistent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "per_device_results": {
                k: v.value for k, v in self.per_device_results.items()
            },
            "overlap_violations": list(self.overlap_violations),
            "globally_consistent": self.globally_consistent,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CrossDeviceDescentResult:
        return cls(
            invariant_id=d["invariant_id"],
            per_device_results={
                k: InvariantStatus(v)
                for k, v in d.get("per_device_results", {}).items()
            },
            overlap_violations=d.get("overlap_violations", []),
            globally_consistent=d.get("globally_consistent", True),
        )
