"""
Visual invariants module for the JuGeo webapp.

Implements the 6 invariant families from §3.6 of GEOMETRY_OF_WEB_APPLICATIONS.md:
topological, proportional, threshold, behavioral, structural, conditional_device.

Also implements the cross-device sheaf model (§3.6.4) and rendering functor
R: W → V (§3.5).

Visual invariants are properties that must hold across ALL devices — they are
global sections of a presheaf over the device site.
"""
from .models import (
    InvariantFamily,
    InvariantStatus,
    DeviceClass,
    STANDARD_DEVICE_CLASSES,
    InvariantResult,
    VisualInvariant,
    InvariantSuite,
    CrossDeviceDescentResult,
)
from .topological import (
    ContainmentInvariant,
    ReadingOrderInvariant,
    VisualClusterInvariant,
    NonOcclusionInvariant,
    TopologicalChecker,
)
from .proportional import (
    ProportionalInvariant,
    UniformityInvariant,
    ProportionalChecker,
)
from .threshold import ThresholdInvariant, ThresholdChecker
from .behavioral import BehavioralInvariant, TriggerKind, BehavioralChecker
from .structural import StructuralInvariant, StructuralChecker
from .conditional_device import (
    ConditionalDeviceInvariant,
    DeviceCondition,
    ConditionalDeviceChecker,
)
from .device_site import DeviceSite, CrossDeviceDescentChecker, DeviceSiteBuilder
from .rendering_functor import (
    VisualSite,
    ViewportRegion,
    TextRun,
    InteractiveZone,
    RenderingFunctor,
    VisualDescentChecker,
)

__all__ = [
    "InvariantFamily",
    "InvariantStatus",
    "DeviceClass",
    "STANDARD_DEVICE_CLASSES",
    "InvariantResult",
    "VisualInvariant",
    "InvariantSuite",
    "CrossDeviceDescentResult",
    "ContainmentInvariant",
    "ReadingOrderInvariant",
    "VisualClusterInvariant",
    "NonOcclusionInvariant",
    "TopologicalChecker",
    "ProportionalInvariant",
    "UniformityInvariant",
    "ProportionalChecker",
    "ThresholdInvariant",
    "ThresholdChecker",
    "BehavioralInvariant",
    "TriggerKind",
    "BehavioralChecker",
    "StructuralInvariant",
    "StructuralChecker",
    "ConditionalDeviceInvariant",
    "DeviceCondition",
    "ConditionalDeviceChecker",
    "DeviceSite",
    "CrossDeviceDescentChecker",
    "DeviceSiteBuilder",
    "VisualSite",
    "ViewportRegion",
    "TextRun",
    "InteractiveZone",
    "RenderingFunctor",
    "VisualDescentChecker",
]
