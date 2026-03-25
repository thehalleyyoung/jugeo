"""responsive.py — CSS media queries modelled as a covering family over the viewport coordinate space.

Each breakpoint partitions the viewport into regions; this module provides tools for
analysing coverage, gaps, and property values across those regions.
"""
from __future__ import annotations

__all__ = [
    "MediaFeature",
    "Breakpoint",
    "BreakpointSystem",
    "ResponsiveProperty",
    "ContainerQuery",
    "ResponsiveLayoutChecker",
]

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 1. MediaFeature
# ---------------------------------------------------------------------------

class MediaFeature(str, Enum):
    """CSS media feature keywords, modelled as string-valued enum members."""

    WIDTH = "width"
    HEIGHT = "height"
    MIN_WIDTH = "min-width"
    MAX_WIDTH = "max-width"
    MIN_HEIGHT = "min-height"
    MAX_HEIGHT = "max-height"
    ORIENTATION = "orientation"
    PREFERS_COLOR_SCHEME = "prefers-color-scheme"
    PREFERS_REDUCED_MOTION = "prefers-reduced-motion"
    HOVER = "hover"
    POINTER = "pointer"
    DISPLAY_MODE = "display-mode"
    RESOLUTION = "resolution"


# ---------------------------------------------------------------------------
# 2. Breakpoint
# ---------------------------------------------------------------------------

@dataclass
class Breakpoint:
    """A named viewport-width interval used in CSS media queries.

    Either or both of *min_width_px* and *max_width_px* may be ``None``,
    meaning the interval is unbounded on that side.
    """

    name: str
    min_width_px: Optional[float]
    max_width_px: Optional[float]

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def matches(self, viewport_width_px: float) -> bool:
        """Return ``True`` when *viewport_width_px* falls inside this breakpoint."""
        if self.min_width_px is not None and viewport_width_px < self.min_width_px:
            return False
        if self.max_width_px is not None and viewport_width_px > self.max_width_px:
            return False
        return True

    # ------------------------------------------------------------------
    # CSS generation
    # ------------------------------------------------------------------

    def to_media_query(self) -> str:
        """Emit an ``@media`` rule string for this breakpoint.

        Examples::

            "@media (min-width: 768px) and (max-width: 1199px)"
            "@media (min-width: 1024px)"
            "@media (max-width: 575px)"
            "@media all"   # no constraints
        """
        parts: list[str] = []
        if self.min_width_px is not None:
            parts.append(f"(min-width: {self.min_width_px:g}px)")
        if self.max_width_px is not None:
            parts.append(f"(max-width: {self.max_width_px:g}px)")
        if not parts:
            return "@media all"
        return "@media " + " and ".join(parts)

    # ------------------------------------------------------------------
    # Preset factories
    # ------------------------------------------------------------------

    @classmethod
    def tailwind_defaults(cls) -> list[Breakpoint]:
        """Return the five standard Tailwind CSS v3 breakpoints (mobile-first).

        Each breakpoint activates at *min-width* and has no upper bound,
        matching the Tailwind convention where ``lg:`` means "lg and above".

        Breakpoints (px): sm=640, md=768, lg=1024, xl=1280, 2xl=1536.
        """
        return [
            cls("sm",  640,    None),
            cls("md",  768,    None),
            cls("lg",  1024,   None),
            cls("xl",  1280,   None),
            cls("2xl", 1536,   None),
        ]

    @classmethod
    def bootstrap_defaults(cls) -> list[Breakpoint]:
        """Return the five standard Bootstrap breakpoints (bounded intervals).

        Boundaries follow the Bootstrap convention: xs ends at 575px, sm starts
        at 576px, and so on.  The sub-pixel range (575, 576) is deliberately
        left uncovered — real Bootstrap uses a 0.02 px fudge to handle this,
        but that detail is below the resolution of integer viewport widths.

        Breakpoints (px): xs(<576), sm(576–767), md(768–991), lg(992–1199), xl(1200+).
        """
        return [
            cls("xs", None, 575),
            cls("sm", 576,  767),
            cls("md", 768,  991),
            cls("lg", 992,  1199),
            cls("xl", 1200, None),
        ]


# ---------------------------------------------------------------------------
# 3. BreakpointSystem
# ---------------------------------------------------------------------------

@dataclass
class BreakpointSystem:
    """An ordered collection of breakpoints representing a complete responsive system.

    Breakpoints are sorted by *min_width_px* (``None`` sorts as 0) upon
    initialisation so that iteration proceeds from smallest to largest.
    """

    breakpoints: list[Breakpoint]

    def __post_init__(self) -> None:
        self.breakpoints = sorted(
            self.breakpoints,
            key=lambda bp: bp.min_width_px if bp.min_width_px is not None else 0.0,
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def active_at(self, viewport_width_px: float) -> Optional[Breakpoint]:
        """Return the *last* matching breakpoint at *viewport_width_px*.

        For mobile-first systems (min-width only) the last match is the
        largest applicable breakpoint, which is the conventional active one.
        Returns ``None`` if no breakpoint matches.
        """
        result: Optional[Breakpoint] = None
        for bp in self.breakpoints:
            if bp.matches(viewport_width_px):
                result = bp
        return result

    def all_active_at(self, viewport_width_px: float) -> list[Breakpoint]:
        """Return every breakpoint that matches *viewport_width_px*.

        For min-width-only (Tailwind-style) systems several breakpoints may
        match simultaneously.
        """
        return [bp for bp in self.breakpoints if bp.matches(viewport_width_px)]

    # ------------------------------------------------------------------
    # Coverage analysis
    # ------------------------------------------------------------------

    def covers_full_range(
        self,
        min_check: float = 0.0,
        max_check: float = 3000.0,
    ) -> bool:
        """Return ``True`` iff every pixel in [*min_check*, *max_check*] is covered."""
        return len(self.find_gap(min_check, max_check)) == 0

    def find_gap(
        self,
        min_check: float = 0.0,
        max_check: float = 3000.0,
    ) -> list[tuple[float, float]]:
        """Return a list of uncovered (start, end) intervals within the check range.

        The algorithm sweeps *min_check* → *max_check* by tracking how far the
        current coverage extends, then detects any point not reached by any
        breakpoint's interval.
        """
        # Build a sorted list of [lo, hi] intervals from the breakpoints,
        # clamped to [min_check, max_check].
        intervals: list[tuple[float, float]] = []
        for bp in self.breakpoints:
            lo = bp.min_width_px if bp.min_width_px is not None else 0.0
            hi = bp.max_width_px if bp.max_width_px is not None else math.inf
            lo = max(lo, min_check)
            hi = min(hi, max_check)
            if lo <= hi:
                intervals.append((lo, hi))

        if not intervals:
            return [(min_check, max_check)]

        intervals.sort()
        gaps: list[tuple[float, float]] = []
        covered_up_to = min_check

        for lo, hi in intervals:
            if lo > covered_up_to:
                gaps.append((covered_up_to, lo))
            covered_up_to = max(covered_up_to, hi)

        if covered_up_to < max_check:
            gaps.append((covered_up_to, max_check))

        return gaps


# ---------------------------------------------------------------------------
# 4. ResponsiveProperty
# ---------------------------------------------------------------------------

@dataclass
class ResponsiveProperty:
    """A CSS property whose value varies across breakpoints.

    *values* maps breakpoint names to CSS values.  The special key ``"base"``
    holds the value that applies outside any breakpoint (i.e. the un-wrapped
    rule).
    """

    property_name: str
    values: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Value lookup
    # ------------------------------------------------------------------

    def value_at(
        self,
        viewport_width_px: float,
        system: BreakpointSystem,
    ) -> Optional[str]:
        """Return the most specific CSS value active at *viewport_width_px*.

        Precedence (highest → lowest):
        1. The last matching breakpoint that has an explicit value in *values*.
        2. The ``"base"`` value.
        3. ``None`` if nothing is set.
        """
        active = system.all_active_at(viewport_width_px)
        # Walk from largest to smallest so the most specific wins.
        for bp in reversed(active):
            if bp.name in self.values:
                return self.values[bp.name]
        return self.values.get("base")

    # ------------------------------------------------------------------
    # CSS generation
    # ------------------------------------------------------------------

    def to_css_rules(self, system: BreakpointSystem) -> list[str]:
        """Generate a list of CSS rule strings for every breakpoint value.

        The ``"base"`` value produces a plain rule; all other keys are wrapped
        in the corresponding ``@media`` query.  Breakpoints not present in
        *values* are skipped.
        """
        bp_by_name = {bp.name: bp for bp in system.breakpoints}
        rules: list[str] = []

        if "base" in self.values:
            rules.append(f"{self.property_name}: {self.values['base']};")

        for bp in system.breakpoints:
            if bp.name not in self.values:
                continue
            media = bp.to_media_query()
            rules.append(
                f"{media} {{ {self.property_name}: {self.values[bp.name]}; }}"
            )

        return rules


# ---------------------------------------------------------------------------
# 5. ContainerQuery
# ---------------------------------------------------------------------------

@dataclass
class ContainerQuery:
    """A CSS container query targeting a named containment context.

    Container queries (CSS Containment Level 3) allow components to respond
    to the size of their *container* rather than the viewport.
    """

    container_name: str
    condition: str       # e.g. "min-width: 400px"
    contained_property: str
    value: str

    def to_css(self) -> str:
        """Emit the ``@container`` rule string.

        Example output::

            @container sidebar (min-width: 400px) { color: red; }
        """
        return (
            f"@container {self.container_name} ({self.condition})"
            f" {{ {self.contained_property}: {self.value}; }}"
        )


# ---------------------------------------------------------------------------
# 6. ResponsiveLayoutChecker
# ---------------------------------------------------------------------------

class ResponsiveLayoutChecker:
    """Static analysis utilities for responsive design systems."""

    # Tailwind-style ordered scale names used for font-size scaling.
    _SCALE_NAMES = ("sm", "md", "lg", "xl", "2xl")
    _FONT_MULTIPLIERS = (1.0, 1.0, 1.05, 1.1, 1.15)

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------

    def check_breakpoint_coverage(self, system: BreakpointSystem) -> list[str]:
        """Return warning strings for any gaps in the breakpoint system.

        The check range is 0 → 3000 px, sufficient for all practical viewports.
        """
        gaps = system.find_gap(0.0, 3000.0)
        warnings: list[str] = []
        for lo, hi in gaps:
            warnings.append(
                f"Coverage gap: {lo:g}px – {hi:g}px is not covered by any breakpoint."
            )
        return warnings

    # ------------------------------------------------------------------
    # Mobile-first
    # ------------------------------------------------------------------

    def check_mobile_first(self, breakpoints: list[Breakpoint]) -> bool:
        """Return ``True`` iff every breakpoint uses *only* a min-width constraint.

        A pure min-width system is the mobile-first pattern where styles are
        layered upward from the smallest viewport.
        """
        return all(
            bp.min_width_px is not None and bp.max_width_px is None
            for bp in breakpoints
        )

    # ------------------------------------------------------------------
    # Property consistency
    # ------------------------------------------------------------------

    def check_property_consistency(
        self,
        props: list[ResponsiveProperty],
        system: BreakpointSystem,
    ) -> list[str]:
        """Warn when a property has a ``"base"`` value but skips intermediate breakpoints.

        A skipped breakpoint creates an implicit "value jump" that may produce
        unexpected layout behaviour between the base and the first explicit
        breakpoint override.

        The check only fires when:
        - The property has a ``"base"`` value AND at least one breakpoint value.
        - One or more breakpoints *between* the base and the first overridden
          breakpoint have no value set.
        """
        bp_names = [bp.name for bp in system.breakpoints]
        warnings: list[str] = []

        for prop in props:
            if "base" not in prop.values:
                continue
            overridden = [n for n in bp_names if n in prop.values]
            if not overridden:
                continue
            # Index of first breakpoint that is overridden.
            first_idx = bp_names.index(overridden[0])
            # Breakpoints before the first override that have no value.
            skipped = [bp_names[i] for i in range(first_idx) if bp_names[i] not in prop.values]
            if skipped:
                warnings.append(
                    f"Property '{prop.property_name}' has a base value but skips "
                    f"breakpoint(s) {skipped!r} before the first override "
                    f"'{overridden[0]}' — value may jump unexpectedly."
                )

        return warnings

    # ------------------------------------------------------------------
    # Font size recommendations
    # ------------------------------------------------------------------

    def recommended_font_sizes(
        self,
        system: BreakpointSystem,
        base_px: float = 16.0,
    ) -> dict[str, float]:
        """Return a mapping of breakpoint name → recommended font size in px.

        Sizes are computed by multiplying *base_px* by a small progressive
        scale factor (1.0, 1.0, 1.05, 1.1, 1.15 for sm / md / lg / xl / 2xl).
        Breakpoints not in the predefined scale list receive the base size.

        The returned dict also includes the ``"base"`` key mapped to *base_px*.
        """
        result: dict[str, float] = {"base": base_px}
        scale = dict(zip(self._SCALE_NAMES, self._FONT_MULTIPLIERS))
        for bp in system.breakpoints:
            multiplier = scale.get(bp.name, 1.0)
            result[bp.name] = round(base_px * multiplier, 4)
        return result
