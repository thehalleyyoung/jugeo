"""
Conditional-device invariant family — device-specific constraints.

Invariants that only apply when a device condition is met (e.g. mobile collapse,
desktop sidebar, print hiding, high-DPI images).

Part of §3.6 invariant family 6 (conditional_device).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import DeviceClass, InvariantResult, InvariantStatus, STANDARD_DEVICE_CLASSES


__all__ = [
    "DeviceCondition",
    "ConditionalDeviceInvariant",
    "ConditionalDeviceChecker",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DeviceCondition:
    """A predicate that matches specific device classes."""

    device_class_name: str = ""
    min_width: int = 0
    max_width: int = 9999
    media_type: str = "screen"
    pixel_ratio_min: float = 1.0

    def matches(self, device: DeviceClass) -> bool:
        """Return ``True`` if this condition matches *device*."""
        if self.device_class_name and self.device_class_name != device.name:
            return False
        if device.media_type != self.media_type:
            return False
        if device.pixel_ratio < self.pixel_ratio_min:
            return False
        dev_min, dev_max = device.width_range
        if dev_max < self.min_width or dev_min > self.max_width:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_class_name": self.device_class_name,
            "min_width": self.min_width,
            "max_width": self.max_width,
            "media_type": self.media_type,
            "pixel_ratio_min": self.pixel_ratio_min,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeviceCondition:
        return cls(
            device_class_name=d.get("device_class_name", ""),
            min_width=d.get("min_width", 0),
            max_width=d.get("max_width", 9999),
            media_type=d.get("media_type", "screen"),
            pixel_ratio_min=d.get("pixel_ratio_min", 1.0),
        )


@dataclass
class ConditionalDeviceInvariant:
    """An invariant that is only checked when *condition* is met."""

    condition: DeviceCondition
    subject: str
    property: str
    expected_value: str
    id: str = field(default_factory=lambda: f"conditional_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition.to_dict(),
            "subject": self.subject,
            "property": self.property,
            "expected_value": self.expected_value,
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConditionalDeviceInvariant:
        return cls(
            condition=DeviceCondition.from_dict(d["condition"]),
            subject=d["subject"],
            property=d["property"],
            expected_value=d["expected_value"],
            id=d.get("id", f"conditional_{uuid.uuid4().hex[:8]}"),
        )


# ---------------------------------------------------------------------------
# DOM helper
# ---------------------------------------------------------------------------


def _find_all_dom(dom: dict[str, Any], tag: Optional[str] = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if tag is None or dom.get("tag") == tag:
        results.append(dom)
    for child in dom.get("children", []):
        results.extend(_find_all_dom(child, tag))
    return results


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class ConditionalDeviceChecker:
    """Checks conditional-device invariants across device-specific style sets."""

    def check_invariant(
        self,
        invariant: ConditionalDeviceInvariant,
        styles_per_device: dict[str, dict[str, dict[str, Any]]],
    ) -> InvariantResult:
        """For each device where condition matches, check property == expected_value."""
        inv_id = invariant.id
        violations: list[str] = []

        for device_name, device_styles in styles_per_device.items():
            device = STANDARD_DEVICE_CLASSES.get(device_name)
            if device is None:
                continue
            if not invariant.condition.matches(device):
                continue
            node_style = device_styles.get(invariant.subject, {})
            actual = node_style.get(invariant.property, "")
            if str(actual) != invariant.expected_value:
                violations.append(
                    f"{device_name}: {invariant.subject}.{invariant.property}="
                    f"'{actual}' (expected '{invariant.expected_value}')"
                )

        if violations:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"violations": violations},
                message="; ".join(violations),
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message=f"Conditional invariant '{inv_id}' satisfied on all matching devices",
        )

    def check_mobile_collapse(
        self,
        styles_per_device: dict[str, dict[str, dict[str, Any]]],
        nav_selector: str,
    ) -> InvariantResult:
        """*nav_selector* should be hidden on ``mobile_portrait``."""
        inv_id = f"conditional_mobile_collapse_{nav_selector}"
        mobile_styles = styles_per_device.get("mobile_portrait", {})
        nav_style = mobile_styles.get(nav_selector, {})
        display = nav_style.get("display", "")
        visibility = nav_style.get("visibility", "")

        if display == "none" or visibility == "hidden":
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                message=f"'{nav_selector}' hidden on mobile_portrait",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"display": display, "visibility": visibility},
            message=f"'{nav_selector}' visible on mobile_portrait (display='{display}', visibility='{visibility}')",
        )

    def check_desktop_sidebar(
        self,
        styles_per_device: dict[str, dict[str, dict[str, Any]]],
        sidebar_selector: str,
    ) -> InvariantResult:
        """*sidebar_selector* should be visible on ``desktop``."""
        inv_id = f"conditional_desktop_sidebar_{sidebar_selector}"
        desktop_styles = styles_per_device.get("desktop", {})
        sidebar_style = desktop_styles.get(sidebar_selector, {})
        display = sidebar_style.get("display", "block")

        if display != "none":
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                message=f"'{sidebar_selector}' visible on desktop",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            evidence={"display": display},
            message=f"'{sidebar_selector}' hidden on desktop (display='{display}')",
        )

    def check_print_hide(
        self,
        styles_per_device: dict[str, dict[str, dict[str, Any]]],
        selectors: list[str],
    ) -> InvariantResult:
        """Each selector should be ``display:none`` on ``print``."""
        inv_id = "conditional_print_hide"
        print_styles = styles_per_device.get("print", {})
        visible: list[str] = []

        for sel in selectors:
            sel_style = print_styles.get(sel, {})
            display = sel_style.get("display", "block")
            if display != "none":
                visible.append(sel)

        if visible:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"visible_on_print": visible},
                message=f"Elements visible on print: {visible}",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message=f"All {len(selectors)} selectors hidden on print",
        )

    def check_high_dpi_images(
        self,
        dom: dict[str, Any],
        device_class: DeviceClass,
    ) -> InvariantResult:
        """If pixel_ratio >= 2.0, images should have srcset attribute."""
        inv_id = "conditional_high_dpi_images"
        if device_class.pixel_ratio < 2.0:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.NOT_APPLICABLE,
                message=f"Device pixel_ratio={device_class.pixel_ratio} < 2.0",
            )

        images = _find_all_dom(dom, tag="img")
        if not images:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                message="No images found",
            )

        missing: list[str] = []
        for img in images:
            attrs = img.get("attrs", {})
            if "srcset" not in attrs and "data-srcset" not in attrs:
                missing.append(img.get("id", "<anonymous>"))

        if missing:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"missing_srcset": missing},
                message=f"High-DPI images without srcset: {missing}",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message=f"All {len(images)} images have srcset for high-DPI device",
        )

    def check_all(
        self,
        invariants: list[Any],
        styles_per_device: dict[str, dict[str, dict[str, Any]]],
    ) -> list[InvariantResult]:
        """Dispatch each conditional-device invariant."""
        results: list[InvariantResult] = []
        for inv in invariants:
            if isinstance(inv, ConditionalDeviceInvariant):
                results.append(self.check_invariant(inv, styles_per_device))
        return results
