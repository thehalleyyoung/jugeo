"""
CSS Cascade as a 4-Stage Functor Pipeline
==========================================

This module models the CSS cascade as a sequence of functorial transformations,
each mapping property values from one stage to the next.  The pipeline mirrors
the CSS specification's concept of *value processing*:

    specified value → computed value → used value → actual value

Why functors?  Each stage is a natural transformation on the category of CSS
property-value pairs, preserving the identity of the property while changing
the representation of its value.  Composition of these transformations gives the
full cascade pipeline as a functor from raw author declarations to device-pixel-
resolved values.

Stage boundaries
----------------
1. **Specified → Computed**
   Relative units (em, rem, vw, vh, ch, %) and keywords (thin, medium, thick,
   smaller, larger, …) are resolved to absolute values.  Font inheritance is
   applied here.  This stage is a functor because it is deterministic given the
   layout context: applying it twice gives the same result (idempotent on
   already-computed values) and it commutes with property-wise composition.

2. **Computed → Used**
   Percentage values that depend on layout (e.g. ``width: 50%``) are resolved
   against the containing block.  Heights whose containing block is ``auto``
   remain unresolved at this stage and are set to ``None`` to signal that layout
   must supply the value later.  This stage maps from the pre-layout category to
   the post-layout category.

3. **Used → Actual**
   Values are rounded or snapped to device-pixel boundaries.  Sub-pixel widths
   become integer pixels; fractional font-sizes are rounded by the OS text
   renderer.  This stage is the final quantisation functor from continuous
   geometric values to discrete device values.

References
----------
* CSS Cascading and Inheritance Level 5 — https://www.w3.org/TR/css-cascade-5/
* CSS Values and Units Level 4 — https://www.w3.org/TR/css-values-4/
"""

from __future__ import annotations

__all__ = [
    "CSSValueStage",
    "CSSPropertyValue",
    "CSSInheritanceModel",
    "RelativeUnitResolver",
    "CascadePipeline",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.descent import LocalSection, DescentEngine, DescentResult


# ---------------------------------------------------------------------------
# 1. CSSValueStage
# ---------------------------------------------------------------------------

class CSSValueStage(str, Enum):
    """Enumeration of the CSS value-processing pipeline stages.

    The CSS specification defines a strict sequence of transformations that
    every property value passes through before it reaches the screen.  Each
    stage is conceptually a functor: it maps values in one category to values
    in another, preserving the property-name identity morphism.

    Stages
    ------
    DECLARED
        The raw value as written by the author (or browser UA sheet, or user
        sheet).  Multiple declarations for the same property may coexist at
        this stage; only one will survive the cascade sort.

    CASCADED
        The single declaration that wins after applying cascade ordering:
        origin priority → cascade layer → specificity → source order.  This is
        the output of the ``CascadeSorter`` from ``specificity.py``.

    SPECIFIED
        The value that the element actually receives, after accounting for
        inheritance and initial values.  If there is no cascaded value:
        (a) inheritable properties take the parent's *computed* value, or
        (b) non-inheritable properties take the property's *initial value*.

    COMPUTED
        The specified value after resolving relative units (em, rem, vw, vh,
        ch), CSS math functions (calc, clamp, min, max), and keyword aliases
        (thin → 1px, medium → 3px, …).  Computed values are the values stored
        on the *computed style* object and inherited by children.  They are
        independent of layout geometry.

    USED
        The computed value after resolving layout-dependent percentages.
        For example ``width: 50%`` becomes a pixel value once the containing
        block's width is known.  Heights may remain ``None`` if the containing
        block's height is ``auto``.

    ACTUAL
        The used value rounded or snapped to the constraints of the output
        device: integer device pixels, font-size rounding by the OS, border
        widths collapsed to nearest supported thickness, etc.
    """

    DECLARED = "declared"
    CASCADED = "cascaded"
    SPECIFIED = "specified"
    COMPUTED = "computed"
    USED = "used"
    ACTUAL = "actual"


# ---------------------------------------------------------------------------
# 2. CSSPropertyValue
# ---------------------------------------------------------------------------

@dataclass
class CSSPropertyValue:
    """A CSS property value together with its current pipeline stage.

    Attributes
    ----------
    property_name:
        The CSS property name in kebab-case, e.g. ``font-size``.
    raw_value:
        The textual value as it appears in the source declaration or as
        inherited from the parent, e.g. ``"1.5em"`` or ``"inherit"``.
    stage:
        Which pipeline stage this value represents.  As it passes through
        the cascade pipeline the stage advances monotonically from
        ``DECLARED`` toward ``ACTUAL``.
    resolved_value:
        The numeric or structured value produced by the computation stage.
        ``None`` until the ``computed_value`` functor has been applied.
        After computation this holds either a ``float`` (for numeric CSS
        values), a ``str`` (for keyword values that cannot be reduced to a
        number, e.g. ``"auto"``), or a ``tuple`` (for compound values such
        as background-position ``(0.0, 50.0)``).
    unit:
        The CSS unit string after resolution, e.g. ``"px"``, ``"deg"``,
        ``"s"``, or ``None`` for dimensionless values.
    """

    property_name: str
    raw_value: str
    stage: CSSValueStage
    resolved_value: float | str | tuple | None = None
    unit: str | None = None


# ---------------------------------------------------------------------------
# 3. CSSInheritanceModel
# ---------------------------------------------------------------------------

@dataclass
class CSSInheritanceModel:
    """Model of CSS property inheritance behaviour.

    The CSS specification divides properties into *inherited* (those that
    propagate their computed value to descendants by default) and
    *non-inherited* (those that reset to their initial value on each element).

    This class provides the authoritative answer for both categories, plus a
    lookup table of initial values for common properties so that the
    ``specified_value`` stage can supply a concrete fallback rather than the
    opaque string ``"initial"``.

    Class-level frozensets are defined on the *class*, not on instances, so
    that they are shared and immutable across the pipeline.
    """

    INHERITED_BY_DEFAULT: frozenset[str] = field(default_factory=lambda: frozenset({
        # Text and font properties
        "color",
        "font",
        "font-family",
        "font-feature-settings",
        "font-kerning",
        "font-language-override",
        "font-optical-sizing",
        "font-size",
        "font-size-adjust",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-variant-caps",
        "font-variant-numeric",
        "font-weight",
        # Line and text layout
        "letter-spacing",
        "line-height",
        "text-align",
        "text-align-last",
        "text-decoration-color",
        "text-indent",
        "text-justify",
        "text-shadow",
        "text-transform",
        "white-space",
        "word-break",
        "word-spacing",
        "word-wrap",
        # List properties
        "list-style",
        "list-style-image",
        "list-style-position",
        "list-style-type",
        # Pointer / cursor
        "cursor",
        "pointer-events",
        # Visibility
        "visibility",
        # Quote marks
        "quotes",
        # Tab size
        "tab-size",
        # Writing mode
        "direction",
        "writing-mode",
    }))

    NEVER_INHERITED: frozenset[str] = field(default_factory=lambda: frozenset({
        # Box model
        "display",
        "position",
        "float",
        "clear",
        "width",
        "min-width",
        "max-width",
        "height",
        "min-height",
        "max-height",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        # Border
        "border",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        "border-width",
        "border-style",
        "border-color",
        "border-radius",
        # Background
        "background",
        "background-color",
        "background-image",
        "background-position",
        "background-size",
        # Stacking / overflow
        "overflow",
        "overflow-x",
        "overflow-y",
        "z-index",
        "opacity",
        # Flex / grid
        "flex",
        "grid",
        "grid-template",
        "align-items",
        "justify-content",
    }))

    def inherits(self, property_name: str) -> bool:
        """Return True if ``property_name`` inherits by default.

        The check uses a three-way lookup:
        1. If the property is in ``INHERITED_BY_DEFAULT``, return True.
        2. If the property is in ``NEVER_INHERITED``, return False.
        3. Unknown properties are treated as non-inherited (conservative
           default matching CSS spec behaviour for custom properties without
           ``inherits: true`` in ``@property``).
        """
        if property_name in self.INHERITED_BY_DEFAULT:
            return True
        if property_name in self.NEVER_INHERITED:
            return False
        # Unknown / custom properties: conservative default
        return False

    def initial_value(self, property_name: str) -> str:
        """Return the CSS initial value string for ``property_name``.

        Returns the keyword ``"initial"`` for properties not listed here, which
        the computation stage must handle by substituting the browser UA value.
        """
        _INITIAL_VALUES: dict[str, str] = {
            "color": "canvastext",
            "font-family": "sans-serif",
            "font-size": "medium",
            "font-style": "normal",
            "font-variant": "normal",
            "font-weight": "normal",
            "font-stretch": "normal",
            "line-height": "normal",
            "letter-spacing": "normal",
            "word-spacing": "normal",
            "text-align": "start",
            "text-decoration": "none",
            "text-indent": "0",
            "text-transform": "none",
            "white-space": "normal",
            "visibility": "visible",
            "cursor": "auto",
            "display": "inline",
            "position": "static",
            "float": "none",
            "clear": "none",
            "width": "auto",
            "height": "auto",
            "min-width": "0",
            "max-width": "none",
            "min-height": "0",
            "max-height": "none",
            "margin": "0",
            "margin-top": "0",
            "margin-right": "0",
            "margin-bottom": "0",
            "margin-left": "0",
            "padding": "0",
            "padding-top": "0",
            "padding-right": "0",
            "padding-bottom": "0",
            "padding-left": "0",
            "border-width": "medium",
            "border-style": "none",
            "border-color": "currentcolor",
            "background-color": "transparent",
            "background-image": "none",
            "opacity": "1",
            "z-index": "auto",
            "overflow": "visible",
            "list-style-type": "disc",
            "list-style-position": "outside",
            "list-style-image": "none",
            "content": "normal",
            "direction": "ltr",
        }
        return _INITIAL_VALUES.get(property_name, "initial")


# ---------------------------------------------------------------------------
# 4. RelativeUnitResolver
# ---------------------------------------------------------------------------

class RelativeUnitResolver:
    """Resolve CSS relative length units to absolute pixel values.

    Each method is a pure function (no instance state) implementing the
    mathematical definition from the CSS Values and Units specification.
    Together they form the ``specified → computed`` functor for length values:
    they map from the category of *context-relative* lengths to the category
    of *absolute* lengths.

    The functor laws are satisfied:
    - Identity: resolving an already-absolute value (multiplier=1, base=1px)
      returns the input unchanged.
    - Composition: resolving a chain (e.g. em inside a rem context) can be
      decomposed into individual resolver calls whose results compose correctly.
    """

    @staticmethod
    def resolve_em(value: float, font_size_px: float) -> float:
        """Resolve an ``em`` value to pixels.

        1em is defined as the computed font-size of the *current* element.
        This makes em values self-referential for ``font-size`` itself, where
        the spec says the em refers to the *parent's* computed font-size
        instead (handled upstream by the pipeline before calling this method).

        Parameters
        ----------
        value:
            The numeric coefficient, e.g. ``1.5`` for ``1.5em``.
        font_size_px:
            The current element's computed font-size in CSS pixels.
        """
        return value * font_size_px

    @staticmethod
    def resolve_rem(value: float, root_font_size_px: float) -> float:
        """Resolve a ``rem`` (root em) value to pixels.

        1rem is the computed font-size of the root element (``<html>``),
        independent of the current element's font-size.  This makes rem
        values immune to font-size inheritance, which is why rem is preferred
        for accessible typographic scales.

        Parameters
        ----------
        value:
            The numeric coefficient, e.g. ``2.0`` for ``2rem``.
        root_font_size_px:
            The root element's computed font-size in CSS pixels.
        """
        return value * root_font_size_px

    @staticmethod
    def resolve_percent_width(value: float, containing_width_px: float) -> float:
        """Resolve a percentage width to pixels.

        Percentage widths are resolved against the *width* of the containing
        block.  The containing block is the nearest block-level ancestor (for
        normally-positioned elements) or the positioned ancestor (for absolute/
        fixed positioning).

        Parameters
        ----------
        value:
            The percentage as a number in [0, 100], e.g. ``50.0`` for ``50%``.
        containing_width_px:
            The containing block's width in CSS pixels.
        """
        return (value / 100.0) * containing_width_px

    @staticmethod
    def resolve_percent_height(
        value: float, containing_height_px: float | None
    ) -> float | None:
        """Resolve a percentage height to pixels, or return None for auto.

        Percentage heights are resolved against the containing block's
        *height*.  However, if the containing block's height is ``auto``
        (the default for most block elements), the percentage is unresolvable
        at the computed-value stage and must be deferred to layout.  The CSS
        spec says the computed value in this case is treated as ``auto``.

        Parameters
        ----------
        value:
            The percentage as a number in [0, 100].
        containing_height_px:
            The containing block's height in CSS pixels, or ``None`` if the
            containing block's height is ``auto``.

        Returns
        -------
        float | None
            Resolved pixel height, or ``None`` if the containing height is
            ``auto`` and the percentage cannot be resolved.
        """
        if containing_height_px is None:
            return None
        return (value / 100.0) * containing_height_px

    @staticmethod
    def resolve_vw(value: float, viewport_width_px: float) -> float:
        """Resolve a ``vw`` (viewport width unit) to pixels.

        1vw is defined as 1% of the viewport's width.  Viewport units are
        always resolved relative to the *initial containing block* (the
        viewport), never the element's own containing block.
        """
        return (value / 100.0) * viewport_width_px

    @staticmethod
    def resolve_vh(value: float, viewport_height_px: float) -> float:
        """Resolve a ``vh`` (viewport height unit) to pixels.

        1vh is 1% of the viewport's height.  Unlike percentage heights, vh
        is always resolvable because the viewport height is always defined.
        """
        return (value / 100.0) * viewport_height_px

    @staticmethod
    def resolve_ch(value: float, zero_advance_px: float) -> float:
        """Resolve a ``ch`` unit to pixels.

        1ch is the advance measure (width) of the "0" (ZERO, U+0030) glyph
        in the element's font.  If the glyph is unavailable the spec allows
        an approximation of 0.5em.

        Parameters
        ----------
        value:
            The numeric coefficient.
        zero_advance_px:
            The advance width of the "0" glyph in the current font, in pixels.
        """
        return value * zero_advance_px

    @staticmethod
    def resolve_clamp(
        min_val: str, preferred: str, max_val: str, context: dict
    ) -> float:
        """Parse and evaluate a ``clamp(min, preferred, max)`` expression.

        ``clamp(MIN, PREF, MAX)`` is mathematically equivalent to
        ``max(MIN, min(PREF, MAX))``.  Each argument may itself be a simple
        length value with unit or a bare number (treated as pixels).

        The ``context`` dict must contain at minimum:
        - ``"font_size_px"``   — current element's computed font-size
        - ``"root_font_size_px"`` — root element's computed font-size
        - ``"viewport_width_px"``
        - ``"viewport_height_px"``
        - ``"containing_width_px"`` — containing block width for percentages

        Parameters
        ----------
        min_val:
            Minimum CSS value string, e.g. ``"200px"`` or ``"10rem"``.
        preferred:
            Preferred CSS value string.
        max_val:
            Maximum CSS value string.
        context:
            Layout and font-size context required to resolve relative units.

        Returns
        -------
        float
            The clamped pixel value.
        """
        def _parse(s: str) -> float:
            s = s.strip()
            # bare number → pixels
            try:
                return float(s)
            except ValueError:
                pass
            m = re.fullmatch(r"(-?[\d.]+)(px|em|rem|vw|vh|ch|%)", s)
            if not m:
                raise ValueError(f"Cannot parse clamp argument: {s!r}")
            num = float(m.group(1))
            unit = m.group(2)
            if unit == "px":
                return num
            if unit == "em":
                return RelativeUnitResolver.resolve_em(
                    num, context["font_size_px"]
                )
            if unit == "rem":
                return RelativeUnitResolver.resolve_rem(
                    num, context["root_font_size_px"]
                )
            if unit == "vw":
                return RelativeUnitResolver.resolve_vw(
                    num, context["viewport_width_px"]
                )
            if unit == "vh":
                return RelativeUnitResolver.resolve_vh(
                    num, context["viewport_height_px"]
                )
            if unit == "ch":
                return RelativeUnitResolver.resolve_ch(
                    num, context.get("zero_advance_px", context["font_size_px"] * 0.5)
                )
            if unit == "%":
                return RelativeUnitResolver.resolve_percent_width(
                    num, context["containing_width_px"]
                )
            raise ValueError(f"Unsupported unit in clamp argument: {unit!r}")

        lo = _parse(min_val)
        pref = _parse(preferred)
        hi = _parse(max_val)
        return max(lo, min(pref, hi))


# ---------------------------------------------------------------------------
# 5. CascadePipeline
# ---------------------------------------------------------------------------

class CascadePipeline:
    """The full CSS value-processing pipeline modelled as a functor composition.

    The pipeline applies four transformations in sequence:

    1. **Cascade** (``apply_cascade``)
       Sorts all declared values for each property by cascade priority and
       selects the winning declaration.  The output is a dict mapping property
       names to their *cascaded* ``CSSPropertyValue``.

    2. **Specified value** (``specified_value``)
       Given a cascaded value (which may be absent), determines the specified
       value by:
       (a) using the cascaded value if present,
       (b) inheriting the parent's computed value if the property is
           inheritable and no cascaded value exists, or
       (c) falling back to the property's initial value.

    3. **Computed value** (``computed_value``)
       Resolves relative units and keyword aliases to absolute values.  After
       this stage the value is independent of parent context (the parent's font-
       size has already been baked in) and can be stored on the computed style
       and inherited by children.

    4. **LocalSection bridge** (``to_local_section``)
       Wraps a computed ``CSSPropertyValue`` as a ``LocalSection`` for the
       JuGeo descent engine, allowing CSS property values to participate in
       sheaf-theoretic gluing and obstruction analysis.

    Functor composition
    -------------------
    The pipeline is a functor F = F₄ ∘ F₃ ∘ F₂ ∘ F₁ where each Fᵢ is a
    natural transformation.  The composition respects:
    - **Identity preservation**: each stage maps the property name to itself.
    - **Sequential dependency**: Fᵢ₊₁ can only be applied after Fᵢ has run.
    - **Contextual parametricity**: each stage is parametric in a context dict
      (font sizes, viewport dimensions, containing block geometry) that is
      passed explicitly rather than captured as mutable state.
    """

    def __init__(
        self,
        inheritance_model: CSSInheritanceModel,
        unit_resolver: RelativeUnitResolver,
    ) -> None:
        """Initialise the pipeline with its two pure-function collaborators.

        Parameters
        ----------
        inheritance_model:
            Supplies ``inherits()`` and ``initial_value()`` for the specified-
            value stage.
        unit_resolver:
            Supplies the relative-unit resolution methods for the computed-
            value stage.
        """
        self._inheritance = inheritance_model
        self._resolver = unit_resolver

    # ------------------------------------------------------------------
    # Stage 1: apply_cascade
    # ------------------------------------------------------------------

    def apply_cascade(
        self,
        declarations: list[tuple[str, Any]],
        element_coord: str,
    ) -> dict[str, CSSPropertyValue]:
        """Apply the CSS cascade to a list of declarations and select winners.

        This method implements the cascade sort described in CSS Cascading and
        Inheritance Level 5.  Declarations are sorted by the ``CascadeKey``
        total order (origin → layer → specificity → source order); the highest-
        priority declaration for each property survives.

        The ``CascadeKey``-based ordering is imported conceptually from
        ``specificity.py``; here we use a lightweight tuple sort so that this
        module has no hard dependency on ``CascadeSorter``.

        Parameters
        ----------
        declarations:
            A list of ``(property_name, value)`` tuples where ``value`` may be
            a plain string (e.g. ``"1.5em"``) or a dict containing cascade
            metadata keys ``"value"``, ``"origin_priority"``, ``"layer"``,
            ``"specificity"``, ``"source_order"``.
        element_coord:
            The JuGeo coordinate string for the element, used to label the
            resulting ``CSSPropertyValue`` instances.

        Returns
        -------
        dict[str, CSSPropertyValue]
            A mapping from property name to the winning ``CSSPropertyValue``
            at stage ``CASCADED``.
        """
        # Group declarations by property name, tracking cascade priority.
        # A declaration dict may carry explicit cascade metadata; plain strings
        # are assigned minimum priority so author sheets beat bare values.
        grouped: dict[str, list[tuple[tuple, str]]] = {}
        for idx, (prop, val) in enumerate(declarations):
            if isinstance(val, dict):
                raw = str(val.get("value", ""))
                sort_key = (
                    int(val.get("origin_priority", 3)),  # default: AUTHOR
                    val.get("layer", 0),
                    val.get("specificity", (0, 0, 0)),
                    val.get("source_order", idx),
                )
            else:
                raw = str(val)
                sort_key = (3, 0, (0, 0, 0), idx)

            grouped.setdefault(prop, []).append((sort_key, raw))

        result: dict[str, CSSPropertyValue] = {}
        for prop, candidates in grouped.items():
            # Highest cascade priority wins; sort descending on sort_key.
            candidates.sort(key=lambda x: x[0], reverse=True)
            winning_raw = candidates[0][1]
            result[prop] = CSSPropertyValue(
                property_name=prop,
                raw_value=winning_raw,
                stage=CSSValueStage.CASCADED,
            )
        return result

    # ------------------------------------------------------------------
    # Stage 2: specified_value
    # ------------------------------------------------------------------

    def specified_value(
        self,
        property_name: str,
        cascaded: CSSPropertyValue | None,
        parent_computed: dict[str, CSSPropertyValue],
    ) -> CSSPropertyValue:
        """Determine the specified value for a property on an element.

        The specified value is determined by the following algorithm (CSS spec
        §3.3 "Specified Values"):

        1. If the cascaded value is the keyword ``inherit``, unconditionally
           use the parent's computed value.
        2. If the cascaded value is the keyword ``initial``, use the property's
           initial value.
        3. If a cascaded value is present (and is not a CSS-wide keyword), use
           it as-is.
        4. Otherwise (no cascaded value):
           (a) If the property inherits by default and the element has a parent,
               use the parent's computed value.
           (b) If the property inherits by default but the element is the root,
               use the property's initial value.
           (c) If the property does not inherit, use the property's initial
               value.

        Parameters
        ----------
        property_name:
            The CSS property name in kebab-case.
        cascaded:
            The cascaded ``CSSPropertyValue`` from ``apply_cascade``, or
            ``None`` if no declaration exists for this property.
        parent_computed:
            The parent element's computed style dict.

        Returns
        -------
        CSSPropertyValue
            The specified value at stage ``SPECIFIED``.
        """
        # Keyword: inherit — force inheritance regardless of property type.
        if cascaded is not None and cascaded.raw_value.strip().lower() == "inherit":
            parent_val = parent_computed.get(property_name)
            if parent_val is not None:
                return CSSPropertyValue(
                    property_name=property_name,
                    raw_value=parent_val.raw_value,
                    stage=CSSValueStage.SPECIFIED,
                    resolved_value=parent_val.resolved_value,
                    unit=parent_val.unit,
                )
            # No parent (root element): fall back to initial.
            return CSSPropertyValue(
                property_name=property_name,
                raw_value=self._inheritance.initial_value(property_name),
                stage=CSSValueStage.SPECIFIED,
            )

        # Keyword: initial — use initial value unconditionally.
        if cascaded is not None and cascaded.raw_value.strip().lower() == "initial":
            return CSSPropertyValue(
                property_name=property_name,
                raw_value=self._inheritance.initial_value(property_name),
                stage=CSSValueStage.SPECIFIED,
            )

        # Cascaded value present and not a CSS-wide keyword.
        if cascaded is not None:
            return CSSPropertyValue(
                property_name=property_name,
                raw_value=cascaded.raw_value,
                stage=CSSValueStage.SPECIFIED,
            )

        # No cascaded value: apply inheritance or initial.
        if self._inheritance.inherits(property_name):
            parent_val = parent_computed.get(property_name)
            if parent_val is not None:
                return CSSPropertyValue(
                    property_name=property_name,
                    raw_value=parent_val.raw_value,
                    stage=CSSValueStage.SPECIFIED,
                    resolved_value=parent_val.resolved_value,
                    unit=parent_val.unit,
                )

        return CSSPropertyValue(
            property_name=property_name,
            raw_value=self._inheritance.initial_value(property_name),
            stage=CSSValueStage.SPECIFIED,
        )

    # ------------------------------------------------------------------
    # Stage 3: computed_value
    # ------------------------------------------------------------------

    def computed_value(
        self,
        specified: CSSPropertyValue,
        context: dict,
    ) -> CSSPropertyValue:
        """Resolve a specified value to its computed value.

        This stage is the ``specified → computed`` functor.  It resolves:
        - Absolute length units (px, pt, pc, cm, mm, in) → pixels
        - Relative length units (em, rem, vw, vh, ch) → pixels using context
        - The ``clamp()`` math function
        - Font-size keywords (xx-small … xx-large, smaller, larger)
        - Line-height keywords (normal → 1.2 × font-size)
        - Border-width keywords (thin, medium, thick)
        - ``font-weight`` keywords (bold → 700, bolder/lighter relative steps)

        Percentage values whose containing dimension is a layout quantity
        (e.g. ``width: 50%``) are *not* resolved here; they remain as the
        percentage string and are handled by the ``used_value`` stage.

        Parameters
        ----------
        specified:
            The specified ``CSSPropertyValue`` (stage ``SPECIFIED``).
        context:
            A dict supplying layout and font-size information needed for
            relative unit resolution.  Expected keys (all optional with
            sensible defaults):
            - ``font_size_px`` (float, default 16.0)
            - ``root_font_size_px`` (float, default 16.0)
            - ``viewport_width_px`` (float, default 1280.0)
            - ``viewport_height_px`` (float, default 720.0)
            - ``containing_width_px`` (float, default 0.0)
            - ``parent_font_size_px`` (float, default 16.0)
            - ``zero_advance_px`` (float, default font_size_px × 0.5)

        Returns
        -------
        CSSPropertyValue
            The computed value at stage ``COMPUTED``.
        """
        ctx = {
            "font_size_px": 16.0,
            "root_font_size_px": 16.0,
            "viewport_width_px": 1280.0,
            "viewport_height_px": 720.0,
            "containing_width_px": 0.0,
            "parent_font_size_px": 16.0,
            **context,
        }
        ctx.setdefault("zero_advance_px", ctx["font_size_px"] * 0.5)

        raw = specified.raw_value.strip()

        # CSS-wide keywords that survive to computed stage unchanged.
        if raw.lower() in {"auto", "none", "normal", "inherit", "initial",
                           "unset", "revert", "revert-layer"}:
            return CSSPropertyValue(
                property_name=specified.property_name,
                raw_value=raw,
                stage=CSSValueStage.COMPUTED,
                resolved_value=raw,
                unit=None,
            )

        # ---- clamp() -------------------------------------------------------
        clamp_m = re.fullmatch(
            r"clamp\(\s*(.+?)\s*,\s*(.+?)\s*,\s*(.+?)\s*\)", raw, re.IGNORECASE
        )
        if clamp_m:
            resolved = self._resolver.resolve_clamp(
                clamp_m.group(1), clamp_m.group(2), clamp_m.group(3), ctx
            )
            return CSSPropertyValue(
                property_name=specified.property_name,
                raw_value=raw,
                stage=CSSValueStage.COMPUTED,
                resolved_value=resolved,
                unit="px",
            )

        # ---- Numeric value with unit ---------------------------------------
        unit_m = re.fullmatch(r"(-?[\d.]+)(px|pt|pc|cm|mm|in|em|rem|vw|vh|ch|%|deg|rad|s|ms)?", raw)
        if unit_m:
            num = float(unit_m.group(1))
            unit = unit_m.group(2) or ""

            if unit == "px" or unit == "":
                return CSSPropertyValue(
                    property_name=specified.property_name,
                    raw_value=raw,
                    stage=CSSValueStage.COMPUTED,
                    resolved_value=num,
                    unit="px" if not unit else unit,
                )
            if unit == "pt":
                return self._make_computed(specified, num * (4 / 3), "px")
            if unit == "pc":
                return self._make_computed(specified, num * 16.0, "px")
            if unit == "cm":
                return self._make_computed(specified, num * (96 / 2.54), "px")
            if unit == "mm":
                return self._make_computed(specified, num * (96 / 25.4), "px")
            if unit == "in":
                return self._make_computed(specified, num * 96.0, "px")
            if unit == "em":
                # For font-size itself, em is relative to *parent* font-size.
                base = (
                    ctx["parent_font_size_px"]
                    if specified.property_name == "font-size"
                    else ctx["font_size_px"]
                )
                return self._make_computed(
                    specified, self._resolver.resolve_em(num, base), "px"
                )
            if unit == "rem":
                return self._make_computed(
                    specified, self._resolver.resolve_rem(num, ctx["root_font_size_px"]), "px"
                )
            if unit == "vw":
                return self._make_computed(
                    specified, self._resolver.resolve_vw(num, ctx["viewport_width_px"]), "px"
                )
            if unit == "vh":
                return self._make_computed(
                    specified, self._resolver.resolve_vh(num, ctx["viewport_height_px"]), "px"
                )
            if unit == "ch":
                return self._make_computed(
                    specified, self._resolver.resolve_ch(num, ctx["zero_advance_px"]), "px"
                )
            # Percentages and time/angle units pass through unresolved at this stage.
            return CSSPropertyValue(
                property_name=specified.property_name,
                raw_value=raw,
                stage=CSSValueStage.COMPUTED,
                resolved_value=num,
                unit=unit,
            )

        # ---- Font-size keywords -------------------------------------------
        _FONT_SIZE_KW: dict[str, float] = {
            "xx-small": 9.0,
            "x-small": 10.0,
            "small": 13.0,
            "medium": 16.0,
            "large": 18.0,
            "x-large": 24.0,
            "xx-large": 32.0,
            "xxx-large": 48.0,
        }
        if specified.property_name == "font-size":
            kw = raw.lower()
            if kw in _FONT_SIZE_KW:
                return self._make_computed(specified, _FONT_SIZE_KW[kw], "px")
            if kw == "smaller":
                return self._make_computed(
                    specified, ctx["parent_font_size_px"] * (5 / 6), "px"
                )
            if kw == "larger":
                return self._make_computed(
                    specified, ctx["parent_font_size_px"] * (6 / 5), "px"
                )

        # ---- Font-weight keywords -----------------------------------------
        if specified.property_name == "font-weight":
            _FW_KW = {"normal": 400.0, "bold": 700.0}
            kw = raw.lower()
            if kw in _FW_KW:
                return self._make_computed(specified, _FW_KW[kw], None)
            if kw == "bolder":
                parent_fw = float(context.get("parent_font_weight", 400))
                bolder = 700.0 if parent_fw < 600 else 900.0
                return self._make_computed(specified, bolder, None)
            if kw == "lighter":
                parent_fw = float(context.get("parent_font_weight", 400))
                lighter = 100.0 if parent_fw < 600 else 400.0
                return self._make_computed(specified, lighter, None)

        # ---- Border-width keywords ----------------------------------------
        if "border" in specified.property_name and "width" in specified.property_name:
            _BW_KW = {"thin": 1.0, "medium": 3.0, "thick": 5.0}
            kw = raw.lower()
            if kw in _BW_KW:
                return self._make_computed(specified, _BW_KW[kw], "px")

        # ---- Fallback: return raw value as computed string ----------------
        return CSSPropertyValue(
            property_name=specified.property_name,
            raw_value=raw,
            stage=CSSValueStage.COMPUTED,
            resolved_value=raw,
            unit=None,
        )

    # ------------------------------------------------------------------
    # LocalSection bridge
    # ------------------------------------------------------------------

    def to_local_section(
        self,
        property_name: str,
        computed: CSSPropertyValue,
        element_coord: str,
    ) -> LocalSection:
        """Wrap a computed CSS property value as a JuGeo ``LocalSection``.

        This method forms the bridge between the CSS cascade pipeline and the
        JuGeo descent / sheaf-gluing engine.  A ``LocalSection`` represents
        locally-defined data on a coordinate patch; here the coordinate is the
        element's path in the document tree, and the judgment data records the
        fully-processed CSS property value.

        This allows collections of computed styles (across a set of elements or
        across a set of properties) to be glued into a global section using the
        ``DescentEngine``, with obstructions detected when property values from
        different origins (author, UA, user) are incompatible.

        Parameters
        ----------
        property_name:
            The CSS property name, e.g. ``"font-size"``.
        computed:
            The computed ``CSSPropertyValue`` (stage ``COMPUTED`` or later).
        element_coord:
            The JuGeo coordinate for the element, e.g. ``"html/body/div[1]"``.

        Returns
        -------
        LocalSection
            A ``LocalSection`` with:
            - ``coordinate`` = ``f"{element_coord}#{property_name}"``
            - ``judgment_data`` carrying the stage, raw value, resolved value,
              and unit
            - ``trust_level`` derived from the cascade stage (computed values
              have higher trust than specified; actual values have full trust)
            - ``provenance`` recording the pipeline stage as a string
        """
        _stage_trust: dict[CSSValueStage, float] = {
            CSSValueStage.DECLARED: 0.4,
            CSSValueStage.CASCADED: 0.6,
            CSSValueStage.SPECIFIED: 0.7,
            CSSValueStage.COMPUTED: 0.85,
            CSSValueStage.USED: 0.95,
            CSSValueStage.ACTUAL: 1.0,
        }
        trust = _stage_trust.get(computed.stage, 0.5)
        coordinate = f"{element_coord}#{property_name}"
        judgment_data: dict[str, Any] = {
            "property_name": computed.property_name,
            "raw_value": computed.raw_value,
            "stage": computed.stage.value,
            "resolved_value": computed.resolved_value,
            "unit": computed.unit,
        }
        return LocalSection(
            coordinate=coordinate,
            judgment_data=judgment_data,
            evidence_bundle=(f"css-cascade:{computed.stage.value}",),
            trust_level=trust,
            provenance=(f"cascade_pipeline:{computed.stage.value}",),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_computed(
        self,
        specified: CSSPropertyValue,
        resolved: float,
        unit: str | None,
    ) -> CSSPropertyValue:
        """Construct a computed CSSPropertyValue from a resolved float."""
        return CSSPropertyValue(
            property_name=specified.property_name,
            raw_value=specified.raw_value,
            stage=CSSValueStage.COMPUTED,
            resolved_value=resolved,
            unit=unit,
        )
