"""jugeo.webapp.rendering — Visual site model and rendering functor.

Models the rendering pipeline R: W -> V from the web application site W
to the visual site V, as described in S3.5 of the JuGeo paper.

Public API:
    VisualSite          — the visual site V
    RenderingFunctor    — the functor R: W -> V
    Viewport            — viewport/device model
    ViewportPresets     — standard device viewports
    ViewportSimulator   — multi-viewport simulation
    InteractionSimulator — interaction simulation
"""
from __future__ import annotations

from jugeo.webapp.rendering.models import (
    ViewportRegion,
    TextRun,
    InteractiveZone,
    AnimationFrame,
    VisualElement,
    VisualPage,
    RenderDiff,
    RenderChange,
    RenderChangeKind,
)
from jugeo.webapp.rendering.visual_site import VisualSite
from jugeo.webapp.rendering.rendering_functor import RenderingFunctor, RenderingDescentChecker
from jugeo.webapp.rendering.viewport_model import Viewport, ViewportPresets, ViewportSimulator
from jugeo.webapp.rendering.interaction_model import (
    InteractionKind,
    InteractionEvent,
    InteractionTrace,
    InteractionSimulator,
    InteractionAccessibilityChecker,
)

__all__ = [
    "ViewportRegion", "TextRun", "InteractiveZone", "AnimationFrame",
    "VisualElement", "VisualPage", "RenderDiff", "RenderChange", "RenderChangeKind",
    "VisualSite",
    "RenderingFunctor", "RenderingDescentChecker",
    "Viewport", "ViewportPresets", "ViewportSimulator",
    "InteractionKind", "InteractionEvent", "InteractionTrace",
    "InteractionSimulator", "InteractionAccessibilityChecker",
]
