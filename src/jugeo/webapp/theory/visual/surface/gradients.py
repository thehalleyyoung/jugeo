"""Surface qualities: gradients, blend modes, and Porter-Duff compositing.

Gradients are functions from position to color (sections over visual regions).
Blend modes are color-space transformations applied before compositing.
Porter-Duff compositing is exact math on RGBA tuples.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "ColorStop",
    "LinearGradient",
    "RadialGradient",
    "BlendMode",
    "PorterDuffCompositor",
    "BackgroundLayer",
    "RGBA",
]

# Type alias
RGBA = tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# ColorStop
# ---------------------------------------------------------------------------

@dataclass
class ColorStop:
    """A single color stop in a gradient.

    ``position`` is the normalised offset along the gradient axis (0–1).
    ``r``, ``g``, ``b`` are sRGB components in [0, 1].  ``alpha`` ∈ [0, 1].
    """

    position: float
    r: float
    g: float
    b: float
    alpha: float = 1.0

    def interpolate(self, other: ColorStop, t: float) -> ColorStop:
        """Return a new stop linearly interpolated at *t* ∈ [0, 1]."""
        def lerp(a: float, b: float) -> float:
            return a + (b - a) * t

        return ColorStop(
            position=lerp(self.position, other.position),
            r=lerp(self.r, other.r),
            g=lerp(self.g, other.g),
            b=lerp(self.b, other.b),
            alpha=lerp(self.alpha, other.alpha),
        )


# ---------------------------------------------------------------------------
# LinearGradient
# ---------------------------------------------------------------------------

@dataclass
class LinearGradient:
    """CSS-style linear gradient.

    ``angle_deg`` follows CSS convention: 0° = up, 90° = right, 180° = down,
    270° = left.
    """

    angle_deg: float
    stops: list[ColorStop]

    def color_at(self, progress: float) -> ColorStop:
        """Return the interpolated color at *progress* ∈ [0, 1]."""
        if not self.stops:
            raise ValueError("LinearGradient has no stops")

        progress = max(0.0, min(1.0, progress))
        stops = sorted(self.stops, key=lambda s: s.position)

        if progress <= stops[0].position:
            s = stops[0]
            return ColorStop(progress, s.r, s.g, s.b, s.alpha)

        if progress >= stops[-1].position:
            s = stops[-1]
            return ColorStop(progress, s.r, s.g, s.b, s.alpha)

        for i in range(len(stops) - 1):
            lo, hi = stops[i], stops[i + 1]
            if lo.position <= progress <= hi.position:
                span = hi.position - lo.position
                t = (progress - lo.position) / span if span > 0 else 0.0
                return lo.interpolate(hi, t)

        return stops[-1]

    def to_css(self) -> str:
        """Return a CSS ``linear-gradient(...)`` string.

        Example: ``linear-gradient(90deg, #ff0000 0%, #0000ff 100%)``
        """
        parts = ", ".join(
            f"{self._stop_color_hex(s)} {round(s.position * 100)}%"
            for s in sorted(self.stops, key=lambda s: s.position)
        )
        return f"linear-gradient({round(self.angle_deg)}deg, {parts})"

    def _stop_color_hex(self, stop: ColorStop) -> str:
        """Convert a stop to a CSS hex color string (#rrggbb or #rrggbbaa)."""
        def byte(v: float) -> int:
            return max(0, min(255, round(v * 255)))

        r, g, b = byte(stop.r), byte(stop.g), byte(stop.b)
        if stop.alpha >= 1.0:
            return f"#{r:02x}{g:02x}{b:02x}"
        return f"#{r:02x}{g:02x}{b:02x}{byte(stop.alpha):02x}"


# ---------------------------------------------------------------------------
# RadialGradient
# ---------------------------------------------------------------------------

@dataclass
class RadialGradient:
    """CSS-style elliptical radial gradient.

    ``cx``, ``cy`` — centre in normalised element coordinates [0, 1].
    ``rx``, ``ry`` — semi-axes in the same normalised space.
    """

    cx: float
    cy: float
    rx: float
    ry: float
    stops: list[ColorStop]

    def color_at_point(self, x: float, y: float, w: float, h: float) -> ColorStop:
        """Return the interpolated color at pixel coordinates (*x*, *y*).

        *w* and *h* are the element's pixel dimensions.  Computes the
        normalised elliptical distance from the gradient centre and looks up
        the corresponding stop::

            d = sqrt(((x - cx*w) / (rx*w))^2 + ((y - cy*h) / (ry*h))^2)
        """
        if not self.stops:
            raise ValueError("RadialGradient has no stops")

        cx_px, cy_px = self.cx * w, self.cy * h
        rx_px, ry_px = self.rx * w, self.ry * h

        dx = (x - cx_px) / rx_px if rx_px > 0 else 0.0
        dy = (y - cy_px) / ry_px if ry_px > 0 else 0.0
        progress = max(0.0, min(1.0, math.sqrt(dx * dx + dy * dy)))

        stops = sorted(self.stops, key=lambda s: s.position)

        if progress <= stops[0].position:
            s = stops[0]
            return ColorStop(progress, s.r, s.g, s.b, s.alpha)
        if progress >= stops[-1].position:
            s = stops[-1]
            return ColorStop(progress, s.r, s.g, s.b, s.alpha)

        for i in range(len(stops) - 1):
            lo, hi = stops[i], stops[i + 1]
            if lo.position <= progress <= hi.position:
                span = hi.position - lo.position
                t = (progress - lo.position) / span if span > 0 else 0.0
                return lo.interpolate(hi, t)

        return stops[-1]


# ---------------------------------------------------------------------------
# BlendMode
# ---------------------------------------------------------------------------

class BlendMode(str, Enum):
    """CSS / SVG blend modes.  Values are the CSS keyword strings."""

    NORMAL      = "normal"
    MULTIPLY    = "multiply"
    SCREEN      = "screen"
    OVERLAY     = "overlay"
    DARKEN      = "darken"
    LIGHTEN     = "lighten"
    COLOR_DODGE = "color-dodge"
    COLOR_BURN  = "color-burn"
    HARD_LIGHT  = "hard-light"
    SOFT_LIGHT  = "soft-light"
    DIFFERENCE  = "difference"
    EXCLUSION   = "exclusion"
    HUE         = "hue"
    SATURATION  = "saturation"
    COLOR_BM    = "color"
    LUMINOSITY  = "luminosity"


# ---------------------------------------------------------------------------
# PorterDuffCompositor
# ---------------------------------------------------------------------------

class PorterDuffCompositor:
    """Porter-Duff RGBA compositing and CSS blend-mode operations.

    All methods accept and return :data:`RGBA` tuples with components in
    [0, 1].  Inputs are treated as linear-light values, matching the CSS
    Compositing and Blending Level 1 specification.

    **Compositing as a monoid**

    ``over`` forms a monoid over RGBA values:

    * *Associativity* — ``over(A, over(B, C)) == over(over(A, B), C)``
      because each application accumulates alpha and pre-multiplied colour
      linearly; grouping does not change the result.

    * *Identity* — transparent black ``(0, 0, 0, 0)`` is the identity:
      ``over(c, (0,0,0,0)) == c`` and ``over((0,0,0,0), c) == c``.

    This structure is why CSS background stacks, SVG filter primitives, and
    canvas layers all compose predictably — any parenthesisation of ``over``
    yields the same final pixel.

    Algorithms from: Porter & Duff (1984) and W3C CSS Compositing Level 1.
    """

    def over(self, src: RGBA, dst: RGBA) -> RGBA:
        """Composite *src* over *dst*.

        ``out_a = src_a + dst_a*(1-src_a)``
        ``out_c = (src_c*src_a + dst_c*dst_a*(1-src_a)) / out_a``
        """
        sr, sg, sb, sa = src
        dr, dg, db, da = dst
        out_a = sa + da * (1.0 - sa)
        if out_a == 0.0:
            return (0.0, 0.0, 0.0, 0.0)
        f = 1.0 - sa
        return (
            (sr * sa + dr * da * f) / out_a,
            (sg * sa + dg * da * f) / out_a,
            (sb * sa + db * da * f) / out_a,
            out_a,
        )

    def multiply(self, src: RGBA, dst: RGBA) -> RGBA:
        """Multiply blend: ``out_c = src_c * dst_c``, then composite over."""
        sr, sg, sb, sa = src
        dr, dg, db, _  = dst
        return self.over((sr * dr, sg * dg, sb * db, sa), dst)

    def screen(self, src: RGBA, dst: RGBA) -> RGBA:
        """Screen blend: ``out_c = 1 - (1-src_c)*(1-dst_c)``."""
        sr, sg, sb, sa = src
        dr, dg, db, _  = dst
        blended = (
            1.0 - (1.0 - sr) * (1.0 - dr),
            1.0 - (1.0 - sg) * (1.0 - dg),
            1.0 - (1.0 - sb) * (1.0 - db),
            sa,
        )
        return self.over(blended, dst)

    def overlay(self, src: RGBA, dst: RGBA) -> RGBA:
        """Overlay blend: multiply when dst < 0.5, screen otherwise.

        ``if dc < 0.5: 2*sc*dc   else: 1 - 2*(1-sc)*(1-dc)``
        """
        sr, sg, sb, sa = src
        dr, dg, db, _  = dst

        def _ch(sc: float, dc: float) -> float:
            return 2.0 * sc * dc if dc < 0.5 else 1.0 - 2.0 * (1.0 - sc) * (1.0 - dc)

        return self.over((_ch(sr, dr), _ch(sg, dg), _ch(sb, db), sa), dst)

    def darken(self, src: RGBA, dst: RGBA) -> RGBA:
        """Darken blend: ``out_c = min(src_c, dst_c)`` per channel."""
        sr, sg, sb, sa = src
        dr, dg, db, _  = dst
        return self.over((min(sr, dr), min(sg, dg), min(sb, db), sa), dst)

    def lighten(self, src: RGBA, dst: RGBA) -> RGBA:
        """Lighten blend: ``out_c = max(src_c, dst_c)`` per channel."""
        sr, sg, sb, sa = src
        dr, dg, db, _  = dst
        return self.over((max(sr, dr), max(sg, dg), max(sb, db), sa), dst)

    def difference(self, src: RGBA, dst: RGBA) -> RGBA:
        """Difference blend: ``out_c = |src_c - dst_c|`` per channel."""
        sr, sg, sb, sa = src
        dr, dg, db, _  = dst
        return self.over(
            (abs(sr - dr), abs(sg - dg), abs(sb - db), sa), dst
        )

    def apply(self, mode: BlendMode, src: RGBA, dst: RGBA) -> RGBA:
        """Dispatch *src* over *dst* using the given *mode*.

        Non-separable modes (HUE, SATURATION, COLOR_BM, LUMINOSITY) and
        remaining separable modes (COLOR_DODGE, COLOR_BURN, HARD_LIGHT,
        SOFT_LIGHT, EXCLUSION) fall back to ``over``; full implementations
        require HSL decomposition beyond this module's scope.
        """
        _dispatch = {
            BlendMode.NORMAL:     self.over,
            BlendMode.MULTIPLY:   self.multiply,
            BlendMode.SCREEN:     self.screen,
            BlendMode.OVERLAY:    self.overlay,
            BlendMode.DARKEN:     self.darken,
            BlendMode.LIGHTEN:    self.lighten,
            BlendMode.DIFFERENCE: self.difference,
        }
        fn = _dispatch.get(mode)
        return fn(src, dst) if fn is not None else self.over(src, dst)


# ---------------------------------------------------------------------------
# BackgroundLayer
# ---------------------------------------------------------------------------

@dataclass
class BackgroundLayer:
    """One layer in a CSS multi-layer background.

    A layer carries either a gradient or a flat colour (or neither).
    Layers are composited bottom-to-top by :meth:`composite_stack`.
    """

    gradient: LinearGradient | RadialGradient | None = None
    color: tuple[float, float, float, float] | None = None
    blend_mode: BlendMode = BlendMode.NORMAL
    opacity: float = 1.0

    def _sample(self, progress: float = 0.0) -> RGBA:
        """Return the RGBA value for this layer at the given *progress*."""
        if self.gradient is not None:
            if isinstance(self.gradient, LinearGradient):
                stop = self.gradient.color_at(progress)
            else:
                stop = self.gradient.color_at_point(
                    progress * self.gradient.rx, 0.0, 1.0, 1.0
                )
            return (stop.r, stop.g, stop.b, stop.alpha * self.opacity)

        if self.color is not None:
            r, g, b, a = self.color
            return (r, g, b, a * self.opacity)

        return (0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def composite_stack(
        layers: list[BackgroundLayer],
        progress: float = 0.0,
    ) -> tuple[float, float, float, float]:
        """Composite *layers* bottom-to-top and return the final RGBA tuple.

        Index 0 is the bottom layer; the last element is topmost.  Each layer
        is sampled at *progress* (0–1), blended with the accumulator using
        its :attr:`blend_mode`, and composited ``over`` the result so far.
        The identity for an empty stack is transparent black ``(0, 0, 0, 0)``.

        The monoid structure of ``over`` guarantees that this fold is
        independent of how layers are parenthesised — only their order matters.
        """
        compositor = PorterDuffCompositor()
        result: RGBA = (0.0, 0.0, 0.0, 0.0)
        for layer in layers:
            result = compositor.apply(layer.blend_mode, layer._sample(progress), result)
        return result
