"""Computed style presheaf for the DOM site.

Models the transformation from declared CSS values to the actual values a
browser applies to each element, covering cascade, inheritance, and
initial-value resolution.
"""
from __future__ import annotations

__all__ = [
    "ComputedValueStage",
    "InheritedProperty",
    "NonInheritedProperty",
    "ComputedStyleRule",
    "ComputedStyleMap",
    "CSSCustomPropertyResolver",
    "InheritanceChain",
    "ComputedStyleAnalyzer",
    "UsedVsComputedExample",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 1. ComputedValueStage
# ---------------------------------------------------------------------------

class ComputedValueStage(str, Enum):
    """The pipeline stage at which a CSS property value was resolved."""

    DECLARED = "declared"
    """Values from all stylesheets before the cascade runs."""

    CASCADED = "cascaded"
    """The single winner after specificity, origin, and !important sorting."""

    SPECIFIED = "specified"
    """Cascaded value plus inheritance and initial values filled in for every
    property so that every element has a value for every property."""

    COMPUTED = "computed"
    """Absolute values resolved where possible without layout: em→px,
    percentages resolved for some properties, keywords replaced."""

    USED = "used"
    """Final values used for layout; percentage widths/heights resolved to
    pixels using the parent's concrete dimensions."""

    ACTUAL = "actual"
    """Pixel-snapped (or otherwise device-adjusted) values that are
    literally rendered to the screen."""

    # Convenience alias used by inheriting rules
    INHERITED = "specified"  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 2. InheritedProperty
# ---------------------------------------------------------------------------

class InheritedProperty(str, Enum):
    """CSS properties whose initial behaviour is to inherit from the parent."""

    COLOR = "color"
    FONT_FAMILY = "font-family"
    FONT_SIZE = "font-size"
    FONT_STYLE = "font-style"
    FONT_WEIGHT = "font-weight"
    LINE_HEIGHT = "line-height"
    LETTER_SPACING = "letter-spacing"
    TEXT_ALIGN = "text-align"
    TEXT_TRANSFORM = "text-transform"
    VISIBILITY = "visibility"
    CURSOR = "cursor"
    DIRECTION = "direction"
    WORD_SPACING = "word-spacing"
    WHITE_SPACE = "white-space"
    QUOTES = "quotes"

    def is_font_related(self) -> bool:
        """Return True for properties that directly affect font rendering."""
        return self in {
            InheritedProperty.FONT_FAMILY,
            InheritedProperty.FONT_SIZE,
            InheritedProperty.FONT_WEIGHT,
            InheritedProperty.FONT_STYLE,
            InheritedProperty.LINE_HEIGHT,
        }


# ---------------------------------------------------------------------------
# 3. NonInheritedProperty
# ---------------------------------------------------------------------------

_NON_INHERITED_INITIAL: dict[str, str] = {
    "width": "auto",
    "height": "auto",
    "margin": "0",
    "padding": "0",
    "border": "medium none currentColor",
    "background": "transparent",
    "display": "inline",
    "position": "static",
    "top": "auto",
    "right": "auto",
    "bottom": "auto",
    "left": "auto",
    "float": "none",
    "clear": "none",
    "overflow": "visible",
    "z-index": "auto",
    "opacity": "1",
    "transform": "none",
    "transition": "none",
    "animation": "none",
    "flex": "0 1 auto",
    "grid": "none",
    "box-shadow": "none",
}


class NonInheritedProperty(str, Enum):
    """CSS properties that do *not* inherit; each element starts from the
    initial value unless explicitly set."""

    WIDTH = "width"
    HEIGHT = "height"
    MARGIN = "margin"
    PADDING = "padding"
    BORDER = "border"
    BACKGROUND = "background"
    DISPLAY = "display"
    POSITION = "position"
    TOP = "top"
    RIGHT = "right"
    BOTTOM = "bottom"
    LEFT = "left"
    FLOAT = "float"
    CLEAR = "clear"
    OVERFLOW = "overflow"
    Z_INDEX = "z-index"
    OPACITY = "opacity"
    TRANSFORM = "transform"
    TRANSITION = "transition"
    ANIMATION = "animation"
    FLEX = "flex"
    GRID = "grid"
    BOX_SHADOW = "box-shadow"

    def initial_value(self) -> str:
        """Return the CSS specification's initial value for this property."""
        return _NON_INHERITED_INITIAL.get(self.value, "")


# ---------------------------------------------------------------------------
# 4. ComputedStyleRule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComputedStyleRule:
    """A single resolved CSS property on one element.

    Tracks both the raw declared value and the final computed value so that
    tooling can explain how the browser arrived at the result.
    """

    property_name: str
    declared_value: str
    computed_value: str
    stage: ComputedValueStage
    inherited_from: str | None = None
    """Coordinate (node_coord) of the ancestor this value was inherited from,
    or None if the value originates on the element itself."""


# ---------------------------------------------------------------------------
# 5. ComputedStyleMap
# ---------------------------------------------------------------------------

@dataclass
class ComputedStyleMap:
    """The full set of resolved CSS rules for one DOM node.

    Analogous to ``window.getComputedStyle(element)`` in a browser.
    """

    node_coord: str
    """Coordinate identifying the element (e.g. ``"div.card#hero"``)."""

    rules: list[ComputedStyleRule] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def get(self, property_name: str) -> str | None:
        """Return the computed value for *property_name*, or None."""
        for rule in self.rules:
            if rule.property_name == property_name:
                return rule.computed_value
        return None

    def get_stage(self, property_name: str) -> ComputedValueStage | None:
        """Return the pipeline stage at which *property_name* was resolved."""
        for rule in self.rules:
            if rule.property_name == property_name:
                return rule.stage
        return None

    def inherited_properties(self) -> list[ComputedStyleRule]:
        """Return rules whose value came from an ancestor element."""
        return [r for r in self.rules if r.inherited_from is not None]

    def initial_value_properties(self) -> list[ComputedStyleRule]:
        """Return rules resolved at the SPECIFIED stage (initial value fill-in)
        that have no ancestor source — i.e. the browser supplied the default."""
        return [
            r for r in self.rules
            if r.stage == ComputedValueStage.SPECIFIED and r.inherited_from is None
        ]


# ---------------------------------------------------------------------------
# 6. CSSCustomPropertyResolver
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(
    r"var\(\s*(--[\w-]+)\s*(?:,\s*((?:[^()]+|\([^()]*\))*))?\s*\)"
)


class CSSCustomPropertyResolver:
    """Resolve CSS custom properties (``--var-name``) with scope and fallback
    support.

    Scoped variables are stored per element coordinate; ``:root`` acts as the
    global fallback scope matching the CSS spec.
    """

    def __init__(self) -> None:
        # {scope: {name: value}}
        self._store: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, value: str, scope: str = ":root") -> None:
        """Store a custom property *name* = *value* in *scope*.

        ``name`` should include the ``--`` prefix (e.g. ``"--color-primary"``).
        """
        if not name.startswith("--"):
            raise ValueError(f"Custom property name must start with '--': {name!r}")
        self._store.setdefault(scope, {})[name] = value

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, value: str, scope: str = ":root") -> str:
        """Replace all ``var(--name)`` tokens in *value* with their stored values.

        Handles fallback syntax: ``var(--missing, fallback)``.
        Checks *scope* first, then ``:root``.
        """
        def _replace(match: re.Match) -> str:
            var_name: str = match.group(1)
            fallback: str | None = match.group(2)

            # Element-local scope first, then :root
            for search_scope in (scope, ":root"):
                if search_scope in self._store:
                    resolved = self._store[search_scope].get(var_name)
                    if resolved is not None:
                        # Recursively resolve nested vars in the resolved value
                        return self.resolve(resolved, scope)

            if fallback is not None:
                return self.resolve(fallback.strip(), scope)

            # Unresolvable: spec says treat declaration as invalid
            return "unset"

        # Iterate until no more var() tokens remain (handles nesting)
        previous = None
        result = value
        while previous != result:
            previous = result
            result = _VAR_RE.sub(_replace, result)
        return result

    # ------------------------------------------------------------------
    # Circular reference detection
    # ------------------------------------------------------------------

    def detect_circular(self, name: str, scope: str = ":root") -> bool:
        """Return True if resolving *name* would produce a circular reference.

        A variable is circular if its value (transitively) contains a
        ``var(--name)`` reference back to itself.
        """
        if not name.startswith("--"):
            return False

        def _has_ref(var_name: str, target: str, visited: set[str]) -> bool:
            if var_name in visited:
                return False  # already explored this branch
            visited.add(var_name)
            for search_scope in (scope, ":root"):
                raw = self._store.get(search_scope, {}).get(var_name)
                if raw is None:
                    continue
                for match in _VAR_RE.finditer(raw):
                    ref = match.group(1)
                    if ref == target:
                        return True
                    if _has_ref(ref, target, visited):
                        return True
            return False

        return _has_ref(name, name, set())


# ---------------------------------------------------------------------------
# 7. InheritanceChain
# ---------------------------------------------------------------------------

class InheritanceChain:
    """Walk an ancestor chain to compute inherited property values."""

    @staticmethod
    def compute_inherited_value(
        property_name: str,
        ancestors: list[ComputedStyleMap],
    ) -> str | None:
        """Return the first explicit computed value for *property_name* found
        when walking *ancestors* from nearest to farthest.

        Returns None if the property is not inheritable or no ancestor carries
        an explicit value.
        """
        # Check that the property is actually inherited
        inherited_css_names = {p.value for p in InheritedProperty}
        if property_name not in inherited_css_names:
            return None

        for ancestor in ancestors:
            value = ancestor.get(property_name)
            if value is not None:
                return value

        return None


# ---------------------------------------------------------------------------
# 8. ComputedStyleAnalyzer
# ---------------------------------------------------------------------------

_INLINE_STYLE_RE = re.compile(
    r'style\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE
)
_STYLE_BLOCK_RE = re.compile(
    r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE
)
_CSS_RULE_RE = re.compile(
    r"([^{]+)\{([^}]*)\}", re.DOTALL
)
_PROP_RE = re.compile(
    r"([\w-]+)\s*:\s*([^;]+)"
)
# Specificity patterns: id, class/attr/pseudo-class, element/pseudo-element
_ID_SEL = re.compile(r"#[\w-]+")
_CLASS_SEL = re.compile(r"\.[\w-]+|\[[\w-]+|:(?!:)[\w-]+")
_ELEM_SEL = re.compile(r"(?<![#.\[])(?<![a-zA-Z0-9_-])[a-zA-Z][\w-]*|::")


def _parse_specificity(selector: str) -> tuple[int, int, int]:
    a = len(_ID_SEL.findall(selector))
    b = len(_CLASS_SEL.findall(selector))
    c = len(_ELEM_SEL.findall(selector))
    return (a, b, c)


class ComputedStyleAnalyzer:
    """Heuristic analysis of computed CSS values from HTML source."""

    def explain_property(self, property_name: str, html_context: str) -> str:
        """Given a CSS property name and an HTML snippet, return a human-readable
        explanation of how the property resolves.

        Searches inline ``style=""`` attributes and ``<style>`` blocks.
        """
        found_values: list[tuple[str, str]] = []  # [(value, source), ...]

        # 1. Inline styles (highest priority)
        for match in _INLINE_STYLE_RE.finditer(html_context):
            declarations = match.group(1)
            for prop_match in _PROP_RE.finditer(declarations):
                if prop_match.group(1).strip() == property_name:
                    found_values.append((prop_match.group(2).strip(), "inline style"))

        # 2. <style> block rules
        for style_block in _STYLE_BLOCK_RE.finditer(html_context):
            css_text = style_block.group(1)
            for rule_match in _CSS_RULE_RE.finditer(css_text):
                selector = rule_match.group(1).strip()
                declarations = rule_match.group(2)
                for prop_match in _PROP_RE.finditer(declarations):
                    if prop_match.group(1).strip() == property_name:
                        found_values.append(
                            (prop_match.group(2).strip(), f"rule '{selector}'")
                        )

        if not found_values:
            # Check inheritance table
            inherited_css_names = {p.value for p in InheritedProperty}
            if property_name in inherited_css_names:
                return (
                    f"{property_name} not explicitly set; resolves via "
                    f"inheritance from ancestor or browser default"
                )
            try:
                prop = NonInheritedProperty(property_name)
                initial = prop.initial_value()
                return (
                    f"{property_name} not set; resolves to initial value "
                    f"'{initial}' (non-inherited, SPECIFIED stage)"
                )
            except ValueError:
                pass
            return f"{property_name} not found in provided HTML context"

        value, source = found_values[-1]  # last match = highest-priority heuristic
        if "inline" in source:
            return f"{property_name} resolves to {value!r} via {source} (CASCADED)"
        return f"{property_name} resolves to {value!r} via {source} (SPECIFIED)"

    def find_specificity_winner(
        self,
        property_name: str,
        rules: list[dict],
    ) -> dict | None:
        """Return the rule with the highest specificity for *property_name*.

        Each rule dict must have keys: ``selector``, ``value``,
        ``specificity_a``, ``specificity_b``, ``specificity_c``.
        Returns None if *rules* is empty.
        """
        if not rules:
            return None

        def _key(rule: dict) -> tuple[int, int, int]:
            return (
                int(rule.get("specificity_a", 0)),
                int(rule.get("specificity_b", 0)),
                int(rule.get("specificity_c", 0)),
            )

        return max(rules, key=_key)


# ---------------------------------------------------------------------------
# 9. UsedVsComputedExample
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Example:
    """A named illustration of the computed → used value transformation."""

    property_name: str
    declared: str
    computed: str
    used: str
    explanation: str


class UsedVsComputedExample:
    """Named examples that illustrate the gap between computed and used values.

    Computed values are what ``getComputedStyle`` returns; used values are what
    the layout engine finally operates on.
    """

    percentage_width = _Example(
        property_name="width",
        declared="50%",
        computed="50%",
        used="400px",
        explanation=(
            "Percentages on 'width' are not resolved at computed-value time; "
            "the browser leaves '50%' as-is. Only during layout, once the "
            "containing block is known (here 800 px), is it multiplied out "
            "to 400 px (used value)."
        ),
    )

    em_font_size = _Example(
        property_name="font-size",
        declared="1.5em",
        computed="24px",
        used="24px",
        explanation=(
            "Em units on 'font-size' *are* resolved at computed-value time: "
            "1.5em × 16px (parent computed font-size) = 24px. Both computed "
            "and used values are therefore 24px."
        ),
    )

    line_height_unitless = _Example(
        property_name="line-height",
        declared="1.5",
        computed="1.5",
        used="24px",
        explanation=(
            "A unitless line-height is kept as the number '1.5' in the "
            "computed value so that descendants scale correctly. The used "
            "value is 1.5 × 16px (element font-size) = 24px, resolved at "
            "layout time."
        ),
    )

    position_auto = _Example(
        property_name="top",
        declared="auto",
        computed="auto",
        used="0px",
        explanation=(
            "'top: auto' on a statically-positioned element computes to "
            "'auto' (the keyword is not resolved at computed-value time). "
            "During layout the static position is established as 0px, so "
            "the used value becomes 0px."
        ),
    )

    flex_basis = _Example(
        property_name="flex-basis",
        declared="auto",
        computed="auto",
        used="<content size>",
        explanation=(
            "'flex-basis: auto' computes to 'auto', signalling that the "
            "flex item's used flex basis should equal its max-content size. "
            "The used value is only determined after the flex formatting "
            "context measures the item's content."
        ),
    )

    viewport_relative_height = _Example(
        property_name="height",
        declared="50vh",
        computed="50vh",
        used="400px",
        explanation=(
            "Viewport-relative lengths such as 'vh' are not resolved at "
            "computed-value time. The used value is resolved against the "
            "viewport height (here 800px), yielding 400px."
        ),
    )

    @classmethod
    def all_examples(cls) -> list[_Example]:
        """Return every named example as a flat list."""
        return [
            v for v in vars(cls).values() if isinstance(v, _Example)
        ]
