"""Visual obligation presheaf — sheaf-theoretic governance of web-app appearance.

From the judgment-geometry perspective, every visual property of a web
application is a *section* of the **visual obligation presheaf** over
the view site.  A section assigns, to each view (page/route), a bundle
of visual requirements — layout, colour, typography, spacing, animation,
responsive behaviour, hierarchy, contrast, and more.

Presheaf structure
------------------
Let **V** be the site of views (objects = pages, morphisms = navigations).
The visual obligation presheaf  O_vis : V^op → Set  sends each view v
to the set of visual obligations that generated code at v must satisfy.
A morphism f : v → w (navigation from v to w) induces a restriction
map  O_vis(w) → O_vis(v)  which propagates global-level obligations
(e.g., the colour palette) down to every page.

Descent = visual consistency
----------------------------
A *descent datum* is a compatible family of local obligation sections.
**Descent** holds when a globally consistent theme can be recovered from
the per-view obligations — i.e., there exists a unique global section
whose restrictions reproduce the local data.  In practice this means:

  • The colour palette is used uniformly across all pages.
  • Typography scale is consistent (headings don't jump size between pages).
  • Spacing is a multiple of the base unit everywhere.
  • Responsive breakpoints behave identically on every page.

Obstructions
------------
An *obstruction* is a failure of descent — a witness that local visual
sections are incompatible.  Concrete examples:

  • A layout that collapses on mobile because the responsive strategy was
    not applied to a particular view.
  • Insufficient colour contrast on a card component that violates WCAG AA.
  • Missing loading-state styling on an async view while sibling views
    have shimmer placeholders.
  • Inconsistent heading sizes between a dashboard and a detail page.

The generator uses the presheaf to *check* descent after emitting CSS and
to *enrich* the output when an obstruction is detected.

This module is **general-purpose** — it works for any web application,
not any specific one.  Concrete apps instantiate the presheaf with their
own palette, typography, and layout choices.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


__all__ = [
    # Enums
    "VisualDomain",
    "LayoutKind",
    "ColorRole",
    # Core data types
    "TypographyScale",
    "SpacingSystem",
    "ColorPalette",
    "Breakpoint",
    "ResponsiveStrategy",
    "AnimationPreset",
    "VisualObligation",
    # Presheaf
    "VisualObligationPresheaf",
    # Standard breakpoints
    "BREAKPOINT_MOBILE",
    "BREAKPOINT_TABLET",
    "BREAKPOINT_DESKTOP",
    "BREAKPOINT_WIDE",
    "STANDARD_BREAKPOINTS",
    # Standard animation presets
    "PRESET_FADE_IN",
    "PRESET_SLIDE_UP",
    "PRESET_SCALE_IN",
    "PRESET_SHIMMER",
    "PRESET_PULSE",
    "PRESET_SHAKE",
    "PRESET_ROTATE",
    "STANDARD_ANIMATION_PRESETS",
    # Builders & generators
    "VisualPresetBuilder",
    "ThemeGenerator",
]


# ══════════════════════════════════════════════════════════════════════
# 1. VisualDomain — the domains of visual obligation
# ══════════════════════════════════════════════════════════════════════

class VisualDomain(str, Enum):
    """Enumeration of all visual-obligation domains.

    Each domain represents an independent axis of visual quality that
    the presheaf tracks.  Obligations in different domains are
    orthogonal — satisfying one does not imply satisfying another.
    """

    LAYOUT = "layout"
    COLOR = "color"
    TYPOGRAPHY = "typography"
    SPACING = "spacing"
    ANIMATION = "animation"
    RESPONSIVE = "responsive"
    HIERARCHY = "hierarchy"
    CONTRAST = "contrast"
    ICONOGRAPHY = "iconography"
    IMAGERY = "imagery"
    SHADOW_DEPTH = "shadow_depth"
    BORDER = "border"
    LOADING_STATES = "loading_states"
    EMPTY_STATES = "empty_states"
    ERROR_STATES = "error_states"


# ══════════════════════════════════════════════════════════════════════
# 2. LayoutKind — layout models
# ══════════════════════════════════════════════════════════════════════

class LayoutKind(str, Enum):
    """Supported layout models for generated views."""

    FLEX_ROW = "flex_row"
    FLEX_COLUMN = "flex_column"
    GRID_2COL = "grid_2col"
    GRID_3COL = "grid_3col"
    GRID_4COL = "grid_4col"
    GRID_MASONRY = "grid_masonry"
    SIDEBAR_LEFT = "sidebar_left"
    SIDEBAR_RIGHT = "sidebar_right"
    HOLY_GRAIL = "holy_grail"
    STICKY_HEADER = "sticky_header"
    FIXED_SIDEBAR = "fixed_sidebar"
    FULL_BLEED = "full_bleed"
    SPLIT_SCREEN = "split_screen"
    CARD_GRID = "card_grid"
    STACK = "stack"
    CENTER_CONTENT = "center_content"


# ══════════════════════════════════════════════════════════════════════
# 3. ColorRole — semantic colour roles
# ══════════════════════════════════════════════════════════════════════

class ColorRole(str, Enum):
    """Semantic colour roles for a design system.

    Each role maps to a hex colour in a :class:`ColorPalette`.  The
    names are intentionally abstract so that palettes can be swapped
    without changing component code.
    """

    PRIMARY = "primary"
    PRIMARY_LIGHT = "primary_light"
    PRIMARY_DARK = "primary_dark"
    ACCENT = "accent"
    BACKGROUND = "background"
    BACKGROUND_ELEVATED = "background_elevated"
    BACKGROUND_CARD = "background_card"
    TEXT = "text"
    TEXT_MUTED = "text_muted"
    TEXT_INVERSE = "text_inverse"
    BORDER = "border"
    SHADOW = "shadow"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"
    LINK = "link"
    LINK_HOVER = "link_hover"
    OVERLAY = "overlay"
    GRADIENT_START = "gradient_start"
    GRADIENT_END = "gradient_end"
    FOCUS_RING = "focus_ring"


# ══════════════════════════════════════════════════════════════════════
# 4. TypographyScale — the type scale
# ══════════════════════════════════════════════════════════════════════

@dataclass
class TypographyScale:
    """A modular type scale derived from a base size and ratio.

    The scale follows the formula  size(level) = base_size × ratio^level
    where level 0 is the body text size.  Negative levels produce
    smaller text (captions, labels); positive levels produce headings.
    """

    base_size: float = 16.0
    scale_ratio: float = 1.25
    font_body: str = "system-ui, -apple-system, sans-serif"
    font_heading: str = "system-ui, -apple-system, sans-serif"
    font_mono: str = "ui-monospace, 'Cascadia Code', monospace"
    line_height_body: float = 1.6
    line_height_heading: float = 1.2
    weight_normal: int = 400
    weight_bold: int = 700
    weight_heading: int = 700

    def size_at_level(self, level: int) -> float:
        """Compute the font size (px) at the given scale *level*.

        Level 0 → base_size, level 1 → base_size * ratio, etc.
        """
        return round(self.base_size * (self.scale_ratio ** level), 2)

    def to_css_properties(self) -> dict[str, str]:
        """Emit CSS custom properties for the type scale."""
        props: dict[str, str] = {
            "--font-body": self.font_body,
            "--font-heading": self.font_heading,
            "--font-mono": self.font_mono,
            "--font-size-base": f"{self.base_size}px",
            "--line-height-body": str(self.line_height_body),
            "--line-height-heading": str(self.line_height_heading),
            "--font-weight-normal": str(self.weight_normal),
            "--font-weight-bold": str(self.weight_bold),
            "--font-weight-heading": str(self.weight_heading),
        }
        # Named levels: xs(-2), sm(-1), base(0), lg(1), xl(2), 2xl(3), 3xl(4), 4xl(5)
        level_names = [
            ("xs", -2),
            ("sm", -1),
            ("base", 0),
            ("lg", 1),
            ("xl", 2),
            ("2xl", 3),
            ("3xl", 4),
            ("4xl", 5),
        ]
        for name, lvl in level_names:
            props[f"--font-size-{name}"] = f"{self.size_at_level(lvl)}px"
        return props


# ══════════════════════════════════════════════════════════════════════
# 5. SpacingSystem — the spacing scale
# ══════════════════════════════════════════════════════════════════════

@dataclass
class SpacingSystem:
    """A spacing scale built from a base unit.

    All spacing values are integer multiples of *base_unit* (typically
    4 px or 8 px).  This guarantees a rhythmic, predictable layout.
    """

    base_unit: int = 4

    # Named multipliers
    _named: dict[str, int] = field(default_factory=lambda: {
        "xs": 1,
        "sm": 2,
        "md": 4,
        "lg": 6,
        "xl": 8,
        "xxl": 12,
    }, repr=False)

    def at(self, multiplier: int) -> int:
        """Return spacing value for the given *multiplier*."""
        return self.base_unit * multiplier

    @property
    def xs(self) -> int:
        return self.at(self._named["xs"])

    @property
    def sm(self) -> int:
        return self.at(self._named["sm"])

    @property
    def md(self) -> int:
        return self.at(self._named["md"])

    @property
    def lg(self) -> int:
        return self.at(self._named["lg"])

    @property
    def xl(self) -> int:
        return self.at(self._named["xl"])

    @property
    def xxl(self) -> int:
        return self.at(self._named["xxl"])

    def to_css_properties(self) -> dict[str, str]:
        """Emit CSS custom properties for named spacing tokens."""
        props: dict[str, str] = {
            "--space-unit": f"{self.base_unit}px",
        }
        for name, mult in self._named.items():
            props[f"--space-{name}"] = f"{self.at(mult)}px"
        return props


# ══════════════════════════════════════════════════════════════════════
# 6. ColorPalette — a complete colour system
# ══════════════════════════════════════════════════════════════════════

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert a hex colour string to (r, g, b)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(r: int, g: int, b: int) -> float:
    """Compute WCAG 2.x relative luminance from sRGB values."""
    components: list[float] = []
    for c in (r, g, b):
        s = c / 255.0
        components.append(s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * components[0] + 0.7152 * components[1] + 0.0722 * components[2]


@dataclass
class ColorPalette:
    """A complete colour system mapping :class:`ColorRole` to hex values.

    Provides WCAG contrast checking and CSS custom-property generation.
    """

    roles: dict[ColorRole, str] = field(default_factory=dict)
    theme: str = "light"

    def contrast_ratio(self, role_a: ColorRole, role_b: ColorRole) -> float:
        """WCAG 2.x contrast ratio between two colour roles.

        Returns a value ≥ 1.0; the ratio is symmetric.
        """
        hex_a = self.roles.get(role_a, "#000000")
        hex_b = self.roles.get(role_b, "#ffffff")
        lum_a = _relative_luminance(*_hex_to_rgb(hex_a))
        lum_b = _relative_luminance(*_hex_to_rgb(hex_b))
        lighter = max(lum_a, lum_b)
        darker = min(lum_a, lum_b)
        return (lighter + 0.05) / (darker + 0.05)

    def meets_wcag_aa(self) -> bool:
        """Check that text/background pairs meet WCAG AA (≥ 4.5:1).

        Verifies TEXT on BACKGROUND, TEXT_MUTED on BACKGROUND, and
        TEXT_INVERSE on PRIMARY.
        """
        pairs = [
            (ColorRole.TEXT, ColorRole.BACKGROUND),
            (ColorRole.TEXT_MUTED, ColorRole.BACKGROUND),
            (ColorRole.TEXT_INVERSE, ColorRole.PRIMARY),
            (ColorRole.TEXT, ColorRole.BACKGROUND_CARD),
            (ColorRole.TEXT, ColorRole.BACKGROUND_ELEVATED),
        ]
        for a, b in pairs:
            if a in self.roles and b in self.roles:
                if self.contrast_ratio(a, b) < 4.5:
                    return False
        return True

    def to_css_properties(self) -> dict[str, str]:
        """Emit CSS custom properties for every defined colour role."""
        return {
            f"--color-{role.value}": hex_val
            for role, hex_val in self.roles.items()
        }

    @classmethod
    def light_default(cls) -> ColorPalette:
        """A sensible light-theme default palette."""
        return cls(
            theme="light",
            roles={
                ColorRole.PRIMARY: "#2563eb",
                ColorRole.PRIMARY_LIGHT: "#60a5fa",
                ColorRole.PRIMARY_DARK: "#1d4ed8",
                ColorRole.ACCENT: "#8b5cf6",
                ColorRole.BACKGROUND: "#ffffff",
                ColorRole.BACKGROUND_ELEVATED: "#f9fafb",
                ColorRole.BACKGROUND_CARD: "#ffffff",
                ColorRole.TEXT: "#111827",
                ColorRole.TEXT_MUTED: "#6b7280",
                ColorRole.TEXT_INVERSE: "#ffffff",
                ColorRole.BORDER: "#e5e7eb",
                ColorRole.SHADOW: "rgba(0,0,0,0.1)",
                ColorRole.SUCCESS: "#16a34a",
                ColorRole.WARNING: "#d97706",
                ColorRole.DANGER: "#dc2626",
                ColorRole.INFO: "#0891b2",
                ColorRole.LINK: "#2563eb",
                ColorRole.LINK_HOVER: "#1d4ed8",
                ColorRole.OVERLAY: "rgba(0,0,0,0.5)",
                ColorRole.GRADIENT_START: "#2563eb",
                ColorRole.GRADIENT_END: "#8b5cf6",
                ColorRole.FOCUS_RING: "rgba(37,99,235,0.5)",
            },
        )

    @classmethod
    def dark_default(cls) -> ColorPalette:
        """A sensible dark-theme default palette."""
        return cls(
            theme="dark",
            roles={
                ColorRole.PRIMARY: "#3b82f6",
                ColorRole.PRIMARY_LIGHT: "#93c5fd",
                ColorRole.PRIMARY_DARK: "#1e40af",
                ColorRole.ACCENT: "#a78bfa",
                ColorRole.BACKGROUND: "#0f172a",
                ColorRole.BACKGROUND_ELEVATED: "#1e293b",
                ColorRole.BACKGROUND_CARD: "#1e293b",
                ColorRole.TEXT: "#f1f5f9",
                ColorRole.TEXT_MUTED: "#94a3b8",
                ColorRole.TEXT_INVERSE: "#0f172a",
                ColorRole.BORDER: "#334155",
                ColorRole.SHADOW: "rgba(0,0,0,0.4)",
                ColorRole.SUCCESS: "#22c55e",
                ColorRole.WARNING: "#f59e0b",
                ColorRole.DANGER: "#ef4444",
                ColorRole.INFO: "#06b6d4",
                ColorRole.LINK: "#60a5fa",
                ColorRole.LINK_HOVER: "#93c5fd",
                ColorRole.OVERLAY: "rgba(0,0,0,0.7)",
                ColorRole.GRADIENT_START: "#3b82f6",
                ColorRole.GRADIENT_END: "#a78bfa",
                ColorRole.FOCUS_RING: "rgba(59,130,246,0.5)",
            },
        )


# ══════════════════════════════════════════════════════════════════════
# 7. Breakpoint — a responsive breakpoint
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Breakpoint:
    """A responsive breakpoint definition.

    *min_width* = 0 represents the smallest screen (mobile).
    *max_columns* controls grid column counts at this breakpoint.
    *container_width* sets the max-width of the content container.
    """

    name: str
    min_width: int
    max_columns: int
    container_width: int

    def to_media_query(self, approach: str = "mobile_first") -> str | None:
        """Return the ``@media`` rule for this breakpoint, or *None* for the base."""
        if approach == "mobile_first":
            if self.min_width == 0:
                return None  # base styles, no media query needed
            return f"@media (min-width: {self.min_width}px)"
        else:
            # desktop_first: use max-width
            if self.min_width == 0:
                return f"@media (max-width: 767px)"
            return f"@media (max-width: {self.min_width - 1}px)"


# Standard breakpoint set
BREAKPOINT_MOBILE = Breakpoint(name="mobile", min_width=0, max_columns=1, container_width=100)
BREAKPOINT_TABLET = Breakpoint(name="tablet", min_width=768, max_columns=2, container_width=720)
BREAKPOINT_DESKTOP = Breakpoint(name="desktop", min_width=1024, max_columns=3, container_width=960)
BREAKPOINT_WIDE = Breakpoint(name="wide", min_width=1440, max_columns=4, container_width=1280)

STANDARD_BREAKPOINTS = [BREAKPOINT_MOBILE, BREAKPOINT_TABLET, BREAKPOINT_DESKTOP, BREAKPOINT_WIDE]


# ══════════════════════════════════════════════════════════════════════
# 8. ResponsiveStrategy — responsive design approach
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ResponsiveStrategy:
    """High-level responsive design configuration.

    Bundles breakpoints with the design methodology (mobile-first vs.
    desktop-first), fluid typography preference, and maximum container
    width.
    """

    breakpoints: list[Breakpoint] = field(default_factory=lambda: list(STANDARD_BREAKPOINTS))
    approach: str = "mobile_first"
    fluid_typography: bool = True
    container_max_width: int = 1280

    def to_media_queries(self) -> list[str]:
        """Return a list of ``@media`` rule strings for all breakpoints.

        Excludes the base breakpoint (min_width=0 for mobile-first)
        since it doesn't need a media query wrapper.
        """
        queries: list[str] = []
        for bp in sorted(self.breakpoints, key=lambda b: b.min_width):
            mq = bp.to_media_query(self.approach)
            if mq is not None:
                queries.append(mq)
        return queries


# ══════════════════════════════════════════════════════════════════════
# 9. AnimationPreset — an animation definition
# ══════════════════════════════════════════════════════════════════════

@dataclass
class AnimationPreset:
    """A reusable CSS animation/transition preset.

    *keyframes* holds the ``@keyframes`` body (without the wrapper).
    When *respects_reduced_motion* is True (the default), generated CSS
    wraps the animation in a ``prefers-reduced-motion: no-preference``
    check.
    """

    name: str
    duration_ms: int
    easing: str
    keyframes: str
    respects_reduced_motion: bool = True

    def to_css(self) -> str:
        """Emit the full ``@keyframes`` block and a utility class."""
        lines = [
            f"@keyframes {self.name} {{",
            f"  {self.keyframes}",
            "}",
            "",
            f".animate-{self.name} {{",
            f"  animation: {self.name} {self.duration_ms}ms {self.easing} both;",
            "}",
        ]
        if self.respects_reduced_motion:
            lines.extend([
                "",
                "@media (prefers-reduced-motion: reduce) {",
                f"  .animate-{self.name} {{",
                "    animation: none;",
                "  }",
                "}",
            ])
        return "\n".join(lines)


# Standard animation presets
PRESET_FADE_IN = AnimationPreset(
    name="fade-in",
    duration_ms=300,
    easing="ease-out",
    keyframes="from { opacity: 0; } to { opacity: 1; }",
)

PRESET_SLIDE_UP = AnimationPreset(
    name="slide-up",
    duration_ms=400,
    easing="cubic-bezier(0.16, 1, 0.3, 1)",
    keyframes="from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); }",
)

PRESET_SCALE_IN = AnimationPreset(
    name="scale-in",
    duration_ms=250,
    easing="cubic-bezier(0.16, 1, 0.3, 1)",
    keyframes="from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); }",
)

PRESET_SHIMMER = AnimationPreset(
    name="shimmer",
    duration_ms=1500,
    easing="linear",
    keyframes=(
        "0% { background-position: -200% 0; }"
        " 100% { background-position: 200% 0; }"
    ),
)

PRESET_PULSE = AnimationPreset(
    name="pulse",
    duration_ms=2000,
    easing="cubic-bezier(0.4, 0, 0.6, 1)",
    keyframes="0%, 100% { opacity: 1; } 50% { opacity: 0.5; }",
)

PRESET_SHAKE = AnimationPreset(
    name="shake",
    duration_ms=500,
    easing="ease-in-out",
    keyframes=(
        "0%, 100% { transform: translateX(0); }"
        " 10%, 30%, 50%, 70%, 90% { transform: translateX(-4px); }"
        " 20%, 40%, 60%, 80% { transform: translateX(4px); }"
    ),
)

PRESET_ROTATE = AnimationPreset(
    name="rotate",
    duration_ms=1000,
    easing="linear",
    keyframes="from { transform: rotate(0deg); } to { transform: rotate(360deg); }",
)

STANDARD_ANIMATION_PRESETS = [
    PRESET_FADE_IN,
    PRESET_SLIDE_UP,
    PRESET_SCALE_IN,
    PRESET_SHIMMER,
    PRESET_PULSE,
    PRESET_SHAKE,
    PRESET_ROTATE,
]


# ══════════════════════════════════════════════════════════════════════
# 10. VisualObligation — a single visual obligation
# ══════════════════════════════════════════════════════════════════════

@dataclass
class VisualObligation:
    """A single visual obligation — a stalk of the visual presheaf.

    Each obligation constrains one aspect of the generated visual output.
    The *domain* classifies it (layout, colour, etc.), *css_property*
    optionally pins it to a specific CSS property, and *threshold*
    provides a numeric lower bound when applicable.
    """

    domain: VisualDomain
    description: str
    css_property: str | None = None
    threshold: float | None = None
    required: bool = True

    def to_css_comment(self) -> str:
        """Render this obligation as a CSS comment for traceability."""
        req = "REQUIRED" if self.required else "optional"
        parts = [f"/* [{self.domain.value}] {self.description} ({req})"]
        if self.css_property:
            parts.append(f"   CSS: {self.css_property}")
        if self.threshold is not None:
            parts.append(f"   threshold: {self.threshold}")
        parts.append(" */")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "description": self.description,
            "css_property": self.css_property,
            "threshold": self.threshold,
            "required": self.required,
        }


# ══════════════════════════════════════════════════════════════════════
# 11. VisualObligationPresheaf — the presheaf
# ══════════════════════════════════════════════════════════════════════

_GLOBAL_VIEW = "__global__"


class VisualObligationPresheaf:
    """The visual obligation presheaf  O_vis : V^op → Set.

    Stores obligations per view (page/route) and globally.  Global
    obligations are inherited by every view via the restriction maps
    of the presheaf.

    Descent check
    ~~~~~~~~~~~~~
    The :meth:`unsatisfied` method takes a raw CSS string and returns
    all obligations whose corresponding CSS property is absent from
    the stylesheet.  This is a lightweight *obstruction detector*.
    """

    def __init__(self) -> None:
        self._sections: dict[str, list[VisualObligation]] = {_GLOBAL_VIEW: []}

    # ── Mutators ──────────────────────────────────────────────────

    def add_obligation(self, view_id: str, obligation: VisualObligation) -> None:
        """Add an obligation to a specific view (or globally if *view_id* is ``__global__``)."""
        self._sections.setdefault(view_id, []).append(obligation)

    def add_global(self, obligation: VisualObligation) -> None:
        """Add an obligation that applies to every view."""
        self._sections[_GLOBAL_VIEW].append(obligation)

    # ── Queries ───────────────────────────────────────────────────

    def global_obligations(self) -> list[VisualObligation]:
        """Return obligations that apply to all views."""
        return list(self._sections.get(_GLOBAL_VIEW, []))

    def obligations_for_view(self, view_id: str) -> list[VisualObligation]:
        """Return all obligations for *view_id*, including globals.

        This is the *section* of the presheaf at coordinate *view_id*:
        the union of view-specific obligations with global ones.
        """
        local = list(self._sections.get(view_id, []))
        return self.global_obligations() + local

    def all_view_ids(self) -> list[str]:
        """Return all registered view ids (excluding the global sentinel)."""
        return [v for v in self._sections if v != _GLOBAL_VIEW]

    def section_at(self, view_id: str) -> dict[str, Any]:
        """Return the visual section at *view_id* as a JSON-serialisable dict.

        Groups obligations by domain for convenient inspection.
        """
        obls = self.obligations_for_view(view_id)
        by_domain: dict[str, list[dict[str, Any]]] = {}
        for obl in obls:
            by_domain.setdefault(obl.domain.value, []).append(obl.to_dict())
        return {
            "view_id": view_id,
            "obligation_count": len(obls),
            "domains": by_domain,
        }

    # ── Obstruction detection ─────────────────────────────────────

    def unsatisfied(self, css: str) -> list[VisualObligation]:
        """Return obligations whose *css_property* is absent from *css*.

        This is a lightweight check: it scans the CSS text for the
        property name.  It is NOT a full CSS parser — it catches the
        most common obstructions (missing custom properties, missing
        media queries, missing animations).
        """
        unmet: list[VisualObligation] = []
        css_lower = css.lower()
        all_obligations: list[VisualObligation] = []
        for view_id in self._sections:
            all_obligations.extend(self._sections[view_id])
        seen: set[tuple[str, str | None]] = set()
        for obl in all_obligations:
            key = (obl.description, obl.css_property)
            if key in seen:
                continue
            seen.add(key)
            if obl.css_property and obl.css_property.lower() not in css_lower:
                unmet.append(obl)
        return unmet

    def domain_coverage(self) -> dict[str, int]:
        """Return the number of obligations per domain (including globals)."""
        counts: dict[str, int] = {}
        for obls in self._sections.values():
            for obl in obls:
                counts[obl.domain.value] = counts.get(obl.domain.value, 0) + 1
        return counts

    def merge(self, other: VisualObligationPresheaf) -> None:
        """Merge another presheaf into this one (union of sections)."""
        for view_id, obls in other._sections.items():
            self._sections.setdefault(view_id, []).extend(obls)


# ══════════════════════════════════════════════════════════════════════
# 12. VisualPresetBuilder — build obligation presets
# ══════════════════════════════════════════════════════════════════════

class VisualPresetBuilder:
    """Factory for progressively richer visual obligation presheaves.

    Four preset levels, each a strict superset of the previous one:
      • *minimal*    — bare essentials (colour, typography, basic layout)
      • *standard*   — adds spacing, responsive, hierarchy, borders
      • *polished*   — adds animation, contrast, shadows, states
      • *production* — adds iconography, imagery, full a11y coverage
    """

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _obl(
        domain: VisualDomain,
        description: str,
        *,
        css_property: str | None = None,
        threshold: float | None = None,
        required: bool = True,
    ) -> VisualObligation:
        return VisualObligation(
            domain=domain,
            description=description,
            css_property=css_property,
            threshold=threshold,
            required=required,
        )

    # ── presets ────────────────────────────────────────────────────

    @classmethod
    def minimal(cls) -> VisualObligationPresheaf:
        """Bare-minimum visual obligations for any web page."""
        p = VisualObligationPresheaf()
        _o = cls._obl

        # Layout
        p.add_global(_o(VisualDomain.LAYOUT, "Page has a top-level layout container",
                        css_property="display"))
        p.add_global(_o(VisualDomain.LAYOUT, "Content has a max-width constraint",
                        css_property="max-width"))

        # Color
        p.add_global(_o(VisualDomain.COLOR, "Background colour is set",
                        css_property="background-color"))
        p.add_global(_o(VisualDomain.COLOR, "Text colour is set",
                        css_property="color"))

        # Typography
        p.add_global(_o(VisualDomain.TYPOGRAPHY, "Font family is specified",
                        css_property="font-family"))
        p.add_global(_o(VisualDomain.TYPOGRAPHY, "Base font size is set",
                        css_property="font-size"))
        p.add_global(_o(VisualDomain.TYPOGRAPHY, "Line height is set",
                        css_property="line-height"))

        return p

    @classmethod
    def standard(cls) -> VisualObligationPresheaf:
        """Standard visual obligations — good for most generated apps."""
        p = cls.minimal()
        _o = cls._obl

        # Spacing
        p.add_global(_o(VisualDomain.SPACING, "Padding uses spacing system",
                        css_property="padding"))
        p.add_global(_o(VisualDomain.SPACING, "Margin uses spacing system",
                        css_property="margin"))
        p.add_global(_o(VisualDomain.SPACING, "Gap is set on flex/grid containers",
                        css_property="gap"))

        # Responsive
        p.add_global(_o(VisualDomain.RESPONSIVE, "At least one responsive breakpoint exists",
                        css_property="@media"))
        p.add_global(_o(VisualDomain.RESPONSIVE, "Viewport meta tag behaviour is consistent",
                        required=False))

        # Hierarchy
        p.add_global(_o(VisualDomain.HIERARCHY, "Headings use a type scale",
                        css_property="font-size"))
        p.add_global(_o(VisualDomain.HIERARCHY, "Primary actions are visually distinct",
                        required=True))

        # Borders
        p.add_global(_o(VisualDomain.BORDER, "Cards and sections have border or separation",
                        css_property="border"))
        p.add_global(_o(VisualDomain.BORDER, "Border radius is consistent",
                        css_property="border-radius"))

        return p

    @classmethod
    def polished(cls) -> VisualObligationPresheaf:
        """Polished visual obligations — for apps that should feel refined."""
        p = cls.standard()
        _o = cls._obl

        # Animation
        p.add_global(_o(VisualDomain.ANIMATION, "Page transitions are animated",
                        css_property="animation"))
        p.add_global(_o(VisualDomain.ANIMATION, "Hover effects use transitions",
                        css_property="transition"))
        p.add_global(_o(VisualDomain.ANIMATION, "Reduced motion is respected",
                        css_property="prefers-reduced-motion"))

        # Contrast
        p.add_global(_o(VisualDomain.CONTRAST, "Text-background contrast meets WCAG AA",
                        threshold=4.5))
        p.add_global(_o(VisualDomain.CONTRAST, "Interactive elements have focus-visible styles",
                        css_property="outline"))

        # Shadow/depth
        p.add_global(_o(VisualDomain.SHADOW_DEPTH, "Cards have box shadow for depth",
                        css_property="box-shadow"))
        p.add_global(_o(VisualDomain.SHADOW_DEPTH, "Modals have overlay backdrop",
                        css_property="backdrop-filter", required=False))

        # Loading states
        p.add_global(_o(VisualDomain.LOADING_STATES, "Async content has a loading indicator",
                        required=True))
        p.add_global(_o(VisualDomain.LOADING_STATES, "Skeleton screens use shimmer animation",
                        css_property="animation", required=False))

        # Empty states
        p.add_global(_o(VisualDomain.EMPTY_STATES, "Empty lists show an empty-state message",
                        required=True))

        # Error states
        p.add_global(_o(VisualDomain.ERROR_STATES, "Form errors are visually indicated",
                        css_property="border-color"))
        p.add_global(_o(VisualDomain.ERROR_STATES, "Error messages use the danger colour",
                        css_property="color"))

        return p

    @classmethod
    def production(cls) -> VisualObligationPresheaf:
        """Production-grade visual obligations — comprehensive coverage."""
        p = cls.polished()
        _o = cls._obl

        # Iconography
        p.add_global(_o(VisualDomain.ICONOGRAPHY, "Actions have associated icons",
                        required=False))
        p.add_global(_o(VisualDomain.ICONOGRAPHY, "Icons have consistent size and weight",
                        css_property="width"))
        p.add_global(_o(VisualDomain.ICONOGRAPHY, "Icon colour inherits from text colour",
                        css_property="currentColor", required=False))

        # Imagery
        p.add_global(_o(VisualDomain.IMAGERY, "Images have aspect-ratio containers",
                        css_property="aspect-ratio"))
        p.add_global(_o(VisualDomain.IMAGERY, "Images have alt-text placeholders",
                        required=True))
        p.add_global(_o(VisualDomain.IMAGERY, "Images use object-fit for consistent display",
                        css_property="object-fit"))

        # Extended responsive
        p.add_global(_o(VisualDomain.RESPONSIVE, "Container queries are used for components",
                        css_property="container-type", required=False))
        p.add_global(_o(VisualDomain.RESPONSIVE, "Touch targets are at least 44px on mobile",
                        threshold=44.0))

        # Extended contrast / a11y
        p.add_global(_o(VisualDomain.CONTRAST, "Non-text contrast meets WCAG AA (3:1)",
                        threshold=3.0))
        p.add_global(_o(VisualDomain.CONTRAST, "Focus ring is visible on all interactive elements",
                        css_property="box-shadow"))

        # Extended hierarchy
        p.add_global(_o(VisualDomain.HIERARCHY, "Breadcrumbs or nav indicate location",
                        required=False))
        p.add_global(_o(VisualDomain.HIERARCHY, "Z-index layering follows a scale",
                        css_property="z-index"))

        # Extended states
        p.add_global(_o(VisualDomain.LOADING_STATES, "Buttons show loading state on submit",
                        required=True))
        p.add_global(_o(VisualDomain.EMPTY_STATES, "Search results show no-results illustration",
                        required=False))
        p.add_global(_o(VisualDomain.ERROR_STATES, "500/404 pages have custom styling",
                        required=True))

        return p


# ══════════════════════════════════════════════════════════════════════
# 13. ThemeGenerator — generate CSS from visual obligations
# ══════════════════════════════════════════════════════════════════════

class ThemeGenerator:
    """Generate CSS stylesheets from visual-obligation building blocks.

    The generator emits CSS that satisfies the visual obligations by
    translating design tokens (palette, typography, spacing) into CSS
    custom properties and structural rules.

    Each ``generate_*`` method produces a self-contained CSS fragment
    that can be concatenated into a complete stylesheet.
    """

    @staticmethod
    def generate_root_css(
        palette: ColorPalette,
        typography: TypographyScale,
        spacing: SpacingSystem,
    ) -> str:
        """Emit a ``:root`` block with all design-token custom properties."""
        props: dict[str, str] = {}
        props.update(palette.to_css_properties())
        props.update(typography.to_css_properties())
        props.update(spacing.to_css_properties())

        lines = [":root {"]
        for prop, val in props.items():
            lines.append(f"  {prop}: {val};")
        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def generate_reset_css() -> str:
        """Emit a modern CSS reset (box-sizing, margins, typography)."""
        return "\n".join([
            "/* ── Reset ─────────────────────────────────────────── */",
            "*, *::before, *::after {",
            "  box-sizing: border-box;",
            "}",
            "",
            "* {",
            "  margin: 0;",
            "  padding: 0;",
            "}",
            "",
            "html {",
            "  -webkit-text-size-adjust: 100%;",
            "  -moz-tab-size: 4;",
            "  tab-size: 4;",
            "}",
            "",
            "body {",
            "  font-family: var(--font-body);",
            "  font-size: var(--font-size-base);",
            "  line-height: var(--line-height-body);",
            "  color: var(--color-text);",
            "  background-color: var(--color-background);",
            "  -webkit-font-smoothing: antialiased;",
            "  -moz-osx-font-smoothing: grayscale;",
            "}",
            "",
            "img, picture, video, canvas, svg {",
            "  display: block;",
            "  max-width: 100%;",
            "}",
            "",
            "input, button, textarea, select {",
            "  font: inherit;",
            "}",
            "",
            "h1, h2, h3, h4, h5, h6 {",
            "  font-family: var(--font-heading);",
            "  font-weight: var(--font-weight-heading);",
            "  line-height: var(--line-height-heading);",
            "  overflow-wrap: break-word;",
            "}",
            "",
            "p {",
            "  overflow-wrap: break-word;",
            "}",
            "",
            "a {",
            "  color: var(--color-link);",
            "  text-decoration: none;",
            "}",
            "",
            "a:hover {",
            "  color: var(--color-link_hover);",
            "}",
        ])

    @staticmethod
    def generate_responsive_css(strategy: ResponsiveStrategy) -> str:
        """Emit responsive container and grid rules from a strategy."""
        lines = [
            "/* ── Responsive ────────────────────────────────────── */",
            ".container {",
            "  width: 100%;",
            f"  max-width: {strategy.container_max_width}px;",
            "  margin-inline: auto;",
            "  padding-inline: var(--space-md);",
            "}",
            "",
        ]

        if strategy.fluid_typography:
            lines.extend([
                "/* Fluid typography */",
                "html {",
                "  font-size: clamp(14px, 0.875rem + 0.5vw, 18px);",
                "}",
                "",
            ])

        for bp in sorted(strategy.breakpoints, key=lambda b: b.min_width):
            mq = bp.to_media_query(strategy.approach)
            if mq is None:
                # Base styles (mobile)
                lines.extend([
                    f"/* {bp.name} — base styles */",
                    ".grid {",
                    "  display: grid;",
                    f"  grid-template-columns: repeat({bp.max_columns}, 1fr);",
                    "  gap: var(--space-md);",
                    "}",
                    "",
                ])
            else:
                lines.extend([
                    f"{mq} {{",
                    f"  /* {bp.name} */",
                    f"  .container {{",
                    f"    max-width: {bp.container_width}px;",
                    "  }",
                    "",
                    "  .grid {",
                    f"    grid-template-columns: repeat({bp.max_columns}, 1fr);",
                    "  }",
                    "}",
                    "",
                ])

        return "\n".join(lines)

    @staticmethod
    def generate_animation_css(presets: list[AnimationPreset] | None = None) -> str:
        """Emit ``@keyframes`` and utility classes for animation presets."""
        if presets is None:
            presets = STANDARD_ANIMATION_PRESETS
        lines = ["/* ── Animations ────────────────────────────────────── */"]
        for preset in presets:
            lines.append(preset.to_css())
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def generate_utility_css() -> str:
        """Emit a set of general-purpose utility classes.

        Covers display, flex, text alignment, spacing helpers,
        visibility, and screen-reader utilities.
        """
        return "\n".join([
            "/* ── Utilities ─────────────────────────────────────── */",
            "",
            "/* Display */",
            ".d-none { display: none; }",
            ".d-block { display: block; }",
            ".d-flex { display: flex; }",
            ".d-grid { display: grid; }",
            ".d-inline { display: inline; }",
            ".d-inline-block { display: inline-block; }",
            "",
            "/* Flexbox */",
            ".flex-row { flex-direction: row; }",
            ".flex-col { flex-direction: column; }",
            ".flex-wrap { flex-wrap: wrap; }",
            ".items-center { align-items: center; }",
            ".items-start { align-items: flex-start; }",
            ".items-end { align-items: flex-end; }",
            ".justify-center { justify-content: center; }",
            ".justify-between { justify-content: space-between; }",
            ".justify-end { justify-content: flex-end; }",
            ".flex-1 { flex: 1; }",
            ".flex-auto { flex: auto; }",
            ".flex-none { flex: none; }",
            "",
            "/* Gap */",
            ".gap-xs { gap: var(--space-xs); }",
            ".gap-sm { gap: var(--space-sm); }",
            ".gap-md { gap: var(--space-md); }",
            ".gap-lg { gap: var(--space-lg); }",
            ".gap-xl { gap: var(--space-xl); }",
            "",
            "/* Text alignment */",
            ".text-left { text-align: left; }",
            ".text-center { text-align: center; }",
            ".text-right { text-align: right; }",
            "",
            "/* Text size */",
            ".text-xs { font-size: var(--font-size-xs); }",
            ".text-sm { font-size: var(--font-size-sm); }",
            ".text-base { font-size: var(--font-size-base); }",
            ".text-lg { font-size: var(--font-size-lg); }",
            ".text-xl { font-size: var(--font-size-xl); }",
            ".text-2xl { font-size: var(--font-size-2xl); }",
            ".text-3xl { font-size: var(--font-size-3xl); }",
            ".text-4xl { font-size: var(--font-size-4xl); }",
            "",
            "/* Font weight */",
            ".font-normal { font-weight: var(--font-weight-normal); }",
            ".font-bold { font-weight: var(--font-weight-bold); }",
            "",
            "/* Colours */",
            ".text-muted { color: var(--color-text_muted); }",
            ".text-primary { color: var(--color-primary); }",
            ".text-success { color: var(--color-success); }",
            ".text-warning { color: var(--color-warning); }",
            ".text-danger { color: var(--color-danger); }",
            ".text-info { color: var(--color-info); }",
            "",
            "/* Spacing helpers */",
            ".m-0 { margin: 0; }",
            ".p-0 { padding: 0; }",
            ".mx-auto { margin-inline: auto; }",
            ".p-xs { padding: var(--space-xs); }",
            ".p-sm { padding: var(--space-sm); }",
            ".p-md { padding: var(--space-md); }",
            ".p-lg { padding: var(--space-lg); }",
            ".p-xl { padding: var(--space-xl); }",
            ".m-xs { margin: var(--space-xs); }",
            ".m-sm { margin: var(--space-sm); }",
            ".m-md { margin: var(--space-md); }",
            ".m-lg { margin: var(--space-lg); }",
            ".m-xl { margin: var(--space-xl); }",
            "",
            "/* Width */",
            ".w-full { width: 100%; }",
            ".w-auto { width: auto; }",
            ".max-w-screen { max-width: 100vw; }",
            "",
            "/* Border radius */",
            ".rounded-none { border-radius: 0; }",
            ".rounded-sm { border-radius: 4px; }",
            ".rounded { border-radius: 8px; }",
            ".rounded-lg { border-radius: 12px; }",
            ".rounded-xl { border-radius: 16px; }",
            ".rounded-full { border-radius: 9999px; }",
            "",
            "/* Overflow */",
            ".overflow-hidden { overflow: hidden; }",
            ".overflow-auto { overflow: auto; }",
            ".overflow-scroll { overflow: scroll; }",
            "",
            "/* Visibility / a11y */",
            ".invisible { visibility: hidden; }",
            ".sr-only {",
            "  position: absolute;",
            "  width: 1px;",
            "  height: 1px;",
            "  padding: 0;",
            "  margin: -1px;",
            "  overflow: hidden;",
            "  clip: rect(0, 0, 0, 0);",
            "  white-space: nowrap;",
            "  border-width: 0;",
            "}",
            "",
            "/* Truncation */",
            ".truncate {",
            "  overflow: hidden;",
            "  text-overflow: ellipsis;",
            "  white-space: nowrap;",
            "}",
            "",
            "/* Transitions */",
            ".transition {",
            "  transition-property: color, background-color, border-color,",
            "    text-decoration-color, fill, stroke, opacity, box-shadow,",
            "    transform, filter, backdrop-filter;",
            "  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);",
            "  transition-duration: 150ms;",
            "}",
            ".transition-colors {",
            "  transition-property: color, background-color, border-color,",
            "    text-decoration-color, fill, stroke;",
            "  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);",
            "  transition-duration: 150ms;",
            "}",
        ])

    @staticmethod
    def generate_component_css() -> str:
        """Emit general component styles (cards, buttons, modals, badges, etc.).

        These styles rely on the CSS custom properties from
        :meth:`generate_root_css`; they are theme-agnostic.
        """
        return "\n".join([
            "/* ── Components ────────────────────────────────────── */",
            "",
            "/* Card */",
            ".card {",
            "  background-color: var(--color-background_card);",
            "  border: 1px solid var(--color-border);",
            "  border-radius: 8px;",
            "  padding: var(--space-lg);",
            "  box-shadow: 0 1px 3px var(--color-shadow);",
            "}",
            "",
            ".card-header {",
            "  font-weight: var(--font-weight-heading);",
            "  margin-bottom: var(--space-sm);",
            "}",
            "",
            ".card-body {",
            "  color: var(--color-text);",
            "}",
            "",
            ".card-footer {",
            "  margin-top: var(--space-md);",
            "  padding-top: var(--space-sm);",
            "  border-top: 1px solid var(--color-border);",
            "}",
            "",
            "/* Button */",
            ".btn {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  gap: var(--space-xs);",
            "  padding: var(--space-sm) var(--space-md);",
            "  font-weight: var(--font-weight-bold);",
            "  font-size: var(--font-size-sm);",
            "  line-height: 1;",
            "  border: 1px solid transparent;",
            "  border-radius: 6px;",
            "  cursor: pointer;",
            "  transition: background-color 150ms ease, box-shadow 150ms ease;",
            "}",
            "",
            ".btn:focus-visible {",
            "  outline: 2px solid var(--color-focus_ring);",
            "  outline-offset: 2px;",
            "}",
            "",
            ".btn-primary {",
            "  background-color: var(--color-primary);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".btn-primary:hover {",
            "  background-color: var(--color-primary_dark);",
            "}",
            "",
            ".btn-secondary {",
            "  background-color: var(--color-background_elevated);",
            "  color: var(--color-text);",
            "  border-color: var(--color-border);",
            "}",
            "",
            ".btn-secondary:hover {",
            "  background-color: var(--color-border);",
            "}",
            "",
            ".btn-danger {",
            "  background-color: var(--color-danger);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".btn-sm {",
            "  padding: var(--space-xs) var(--space-sm);",
            "  font-size: var(--font-size-xs);",
            "}",
            "",
            ".btn-lg {",
            "  padding: var(--space-md) var(--space-lg);",
            "  font-size: var(--font-size-lg);",
            "}",
            "",
            ".btn-loading {",
            "  pointer-events: none;",
            "  opacity: 0.7;",
            "}",
            "",
            "/* Modal */",
            ".modal-overlay {",
            "  position: fixed;",
            "  inset: 0;",
            "  background-color: var(--color-overlay);",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  z-index: 50;",
            "}",
            "",
            ".modal {",
            "  background-color: var(--color-background_elevated);",
            "  border-radius: 12px;",
            "  padding: var(--space-xl);",
            "  box-shadow: 0 20px 60px var(--color-shadow);",
            "  max-width: 500px;",
            "  width: 90%;",
            "}",
            "",
            ".modal-header {",
            "  font-size: var(--font-size-xl);",
            "  font-weight: var(--font-weight-heading);",
            "  margin-bottom: var(--space-md);",
            "}",
            "",
            ".modal-footer {",
            "  margin-top: var(--space-lg);",
            "  display: flex;",
            "  justify-content: flex-end;",
            "  gap: var(--space-sm);",
            "}",
            "",
            "/* Badge */",
            ".badge {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  padding: 2px var(--space-sm);",
            "  font-size: var(--font-size-xs);",
            "  font-weight: var(--font-weight-bold);",
            "  border-radius: 9999px;",
            "  line-height: 1.4;",
            "}",
            "",
            ".badge-primary {",
            "  background-color: var(--color-primary_light);",
            "  color: var(--color-primary_dark);",
            "}",
            "",
            ".badge-success {",
            "  background-color: var(--color-success);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".badge-warning {",
            "  background-color: var(--color-warning);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".badge-danger {",
            "  background-color: var(--color-danger);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            "/* Form controls */",
            ".form-group {",
            "  display: flex;",
            "  flex-direction: column;",
            "  gap: var(--space-xs);",
            "  margin-bottom: var(--space-md);",
            "}",
            "",
            ".form-label {",
            "  font-size: var(--font-size-sm);",
            "  font-weight: var(--font-weight-bold);",
            "  color: var(--color-text);",
            "}",
            "",
            ".form-input {",
            "  padding: var(--space-sm) var(--space-md);",
            "  border: 1px solid var(--color-border);",
            "  border-radius: 6px;",
            "  background-color: var(--color-background);",
            "  color: var(--color-text);",
            "  font-size: var(--font-size-base);",
            "  transition: border-color 150ms ease, box-shadow 150ms ease;",
            "}",
            "",
            ".form-input:focus {",
            "  outline: none;",
            "  border-color: var(--color-primary);",
            "  box-shadow: 0 0 0 3px var(--color-focus_ring);",
            "}",
            "",
            ".form-input.is-invalid {",
            "  border-color: var(--color-danger);",
            "}",
            "",
            ".form-error {",
            "  font-size: var(--font-size-xs);",
            "  color: var(--color-danger);",
            "}",
            "",
            "/* Alert */",
            ".alert {",
            "  padding: var(--space-md);",
            "  border-radius: 8px;",
            "  border: 1px solid transparent;",
            "  font-size: var(--font-size-sm);",
            "}",
            "",
            ".alert-success {",
            "  background-color: var(--color-success);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".alert-warning {",
            "  background-color: var(--color-warning);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".alert-danger {",
            "  background-color: var(--color-danger);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            ".alert-info {",
            "  background-color: var(--color-info);",
            "  color: var(--color-text_inverse);",
            "}",
            "",
            "/* Table */",
            ".table {",
            "  width: 100%;",
            "  border-collapse: collapse;",
            "}",
            "",
            ".table th,",
            ".table td {",
            "  padding: var(--space-sm) var(--space-md);",
            "  text-align: left;",
            "  border-bottom: 1px solid var(--color-border);",
            "}",
            "",
            ".table th {",
            "  font-weight: var(--font-weight-bold);",
            "  font-size: var(--font-size-xs);",
            "  text-transform: uppercase;",
            "  letter-spacing: 0.05em;",
            "  color: var(--color-text_muted);",
            "}",
            "",
            ".table tbody tr:hover {",
            "  background-color: var(--color-background_elevated);",
            "}",
            "",
            "/* Loading */",
            ".skeleton {",
            "  background: linear-gradient(",
            "    90deg,",
            "    var(--color-background_elevated) 25%,",
            "    var(--color-border) 50%,",
            "    var(--color-background_elevated) 75%",
            "  );",
            "  background-size: 200% 100%;",
            "  animation: shimmer 1.5s linear infinite;",
            "  border-radius: 4px;",
            "}",
            "",
            ".skeleton-text {",
            "  height: 1em;",
            "  margin-bottom: var(--space-xs);",
            "}",
            "",
            ".skeleton-avatar {",
            "  width: 40px;",
            "  height: 40px;",
            "  border-radius: 50%;",
            "}",
            "",
            "/* Empty state */",
            ".empty-state {",
            "  display: flex;",
            "  flex-direction: column;",
            "  align-items: center;",
            "  justify-content: center;",
            "  padding: var(--space-xxl) var(--space-lg);",
            "  text-align: center;",
            "  color: var(--color-text_muted);",
            "}",
            "",
            ".empty-state-icon {",
            "  font-size: var(--font-size-4xl);",
            "  margin-bottom: var(--space-md);",
            "  opacity: 0.4;",
            "}",
            "",
            ".empty-state-title {",
            "  font-size: var(--font-size-lg);",
            "  font-weight: var(--font-weight-bold);",
            "  margin-bottom: var(--space-xs);",
            "}",
            "",
            "/* Tooltip */",
            ".tooltip {",
            "  position: relative;",
            "}",
            "",
            ".tooltip::after {",
            "  content: attr(data-tooltip);",
            "  position: absolute;",
            "  bottom: calc(100% + var(--space-xs));",
            "  left: 50%;",
            "  transform: translateX(-50%);",
            "  padding: var(--space-xs) var(--space-sm);",
            "  background-color: var(--color-text);",
            "  color: var(--color-text_inverse);",
            "  font-size: var(--font-size-xs);",
            "  border-radius: 4px;",
            "  white-space: nowrap;",
            "  pointer-events: none;",
            "  opacity: 0;",
            "  transition: opacity 150ms ease;",
            "}",
            "",
            ".tooltip:hover::after {",
            "  opacity: 1;",
            "}",
        ])
