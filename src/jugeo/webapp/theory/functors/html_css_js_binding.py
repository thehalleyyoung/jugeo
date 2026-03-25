"""html_css_js_binding.py — Functors between HTML, CSS, and JS sites.

Models how HTML structure, CSS styling, and JavaScript behavior relate as
functors between sites. Selector matching is a morphism from the CSS selector
site to the DOM site. JS DOM queries are section lookups.
"""
from __future__ import annotations

__all__ = [
    "SelectorSpecificity",
    "CSSBinding",
    "CSSBindingFunctor",
    "JSQueryKind",
    "JSDOMQuery",
    "JSHTMLBindingFunctor",
    "HTMLCSSJSCoherenceReport",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind
from jugeo.geometry.descent import LocalSection, DescentResult, DescentObstruction
from jugeo.geometry.descent import GlobalSection


# ---------------------------------------------------------------------------
# 1. SelectorSpecificity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelectorSpecificity:
    """Lexicographic CSS specificity triple (a, b, c).

    a — ID selectors (#id)
    b — class selectors (.class), attribute selectors ([attr]), pseudo-classes (:pseudo)
    c — type selectors (tag), pseudo-elements (::pseudo)
    """

    a: int = 0  # ID count
    b: int = 0  # class / attribute / pseudo-class count
    c: int = 0  # type / pseudo-element count

    # ------------------------------------------------------------------
    # Comparison (lexicographic)
    # ------------------------------------------------------------------

    def _tuple(self) -> tuple[int, int, int]:
        return (self.a, self.b, self.c)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SelectorSpecificity):
            return NotImplemented
        return self._tuple() < other._tuple()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SelectorSpecificity):
            return NotImplemented
        return self._tuple() > other._tuple()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SelectorSpecificity):
            return NotImplemented
        return self._tuple() <= other._tuple()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SelectorSpecificity):
            return NotImplemented
        return self._tuple() >= other._tuple()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SelectorSpecificity):
            return NotImplemented
        return self._tuple() == other._tuple()

    def __hash__(self) -> int:
        return hash(self._tuple())

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, selector: str) -> SelectorSpecificity:
        """Count specificity components by scanning the selector string."""
        working = selector

        # Strip pseudo-element content to avoid double-counting
        working = re.sub(r'::[\w-]+', '\x00pe\x00', working)

        # Count IDs
        ids = len(re.findall(r'#[\w-]+', working))

        # Count classes, attribute selectors, pseudo-classes
        # Remove attribute selector contents first (they may contain pseudo-like text)
        without_attr = re.sub(r'\[[^\]]*\]', '\x00attr\x00', working)
        attr_count = working.count('\x00attr\x00') if False else len(
            re.findall(r'\[[^\]]*\]', working)
        )
        class_count = len(re.findall(r'\.[\w-]+', without_attr))
        # Pseudo-classes (single colon, not ::)
        pseudo_class_count = len(re.findall(r'(?<!:):[\w-]+(?!\()', without_attr))
        # Pseudo-classes with parentheses like :nth-child(2)
        pseudo_func_count = len(re.findall(r'(?<!:):[\w-]+\(', without_attr))
        b = class_count + attr_count + pseudo_class_count + pseudo_func_count

        # Count type selectors (tag names) and pseudo-elements
        # Remove id/class/attr/pseudo markers so we only count bare tags
        stripped = re.sub(r'#[\w-]+', ' ', working)
        stripped = re.sub(r'\.[\w-]+', ' ', stripped)
        stripped = re.sub(r'\[[^\]]*\]', ' ', stripped)
        stripped = re.sub(r'::[\w-]+', ' ', stripped)
        stripped = re.sub(r'(?<!:):[\w-]+(?:\([^)]*\))?', ' ', stripped)
        # Combinators and whitespace are not type selectors
        stripped = re.sub(r'[>~+]', ' ', stripped)
        # Universal selector * does not count
        stripped = re.sub(r'\*', ' ', stripped)
        tag_matches = re.findall(r'\b[a-zA-Z][\w-]*\b', stripped)
        pe_count = len(re.findall(r'\x00pe\x00', working))
        c = len(tag_matches) + pe_count

        return cls(a=ids, b=b, c=c)


# ---------------------------------------------------------------------------
# 2. CSSBinding
# ---------------------------------------------------------------------------

@dataclass
class CSSBinding:
    """A single CSS property declaration bound to matched DOM elements."""

    selector: str
    property_name: str
    value: str
    specificity: SelectorSpecificity
    source_order: int
    matched_elements: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Cascade
    # ------------------------------------------------------------------

    def beats(self, other: CSSBinding) -> bool:
        """Return True if this binding wins the cascade over *other*.

        Assumes both target the same *property_name*.  Higher specificity wins;
        ties are broken by later source_order.
        """
        if self.specificity != other.specificity:
            return self.specificity > other.specificity
        return self.source_order > other.source_order

    # ------------------------------------------------------------------
    # Descent integration
    # ------------------------------------------------------------------

    def to_local_section(self, element_coord: str) -> LocalSection:
        """Represent this binding as a local section on *element_coord*."""
        return LocalSection(
            coordinate=element_coord,
            judgment_data={
                "selector": self.selector,
                "property": self.property_name,
                "value": self.value,
                "specificity": (
                    self.specificity.a,
                    self.specificity.b,
                    self.specificity.c,
                ),
                "source_order": self.source_order,
            },
            evidence_bundle=(
                f"css_binding:{self.selector}:{self.property_name}",
            ),
            trust_level=1.0,
            provenance=(f"css_source_order:{self.source_order}",),
        )


# ---------------------------------------------------------------------------
# 3. CSSBindingFunctor
# ---------------------------------------------------------------------------

class CSSBindingFunctor:
    """Functor mapping CSS selector space → DOM element space.

    Selector matching is a morphism: given a CSS rule (an object in the
    CSS-selector site) the functor produces the set of DOM coordinates
    (objects in the DOM site) that the rule applies to.
    """

    # ------------------------------------------------------------------
    # Selector matching
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_compound(selector: str) -> tuple[str | None, set[str], str | None]:
        """Split a compound selector (no combinators) into (tag, classes, id).

        Returns ``(tag_or_None, {classes}, id_or_None)``.
        """
        tag_match = re.match(r'^([a-zA-Z][\w-]*|\*)', selector)
        tag: str | None = tag_match.group(1) if tag_match else None
        if tag == '*':
            tag = None

        ids = re.findall(r'#([\w-]+)', selector)
        el_id: str | None = ids[0] if ids else None

        classes = set(re.findall(r'\.([\w-]+)', selector))

        return tag, classes, el_id

    def match(
        self,
        selector: str,
        elements: list[tuple[str, str, set[str], str | None]],
    ) -> list[str]:
        """Return coord_names of *elements* matched by *selector*.

        Parameters
        ----------
        selector:
            A combinator-free compound selector string, e.g. ``"div.foo#bar"``.
        elements:
            List of ``(coord_name, tag, classes, id)`` tuples describing DOM
            elements.

        Supports: tag, ``.class``, ``#id``, and compound combinations thereof.
        """
        sel_tag, sel_classes, sel_id = self._parse_compound(selector.strip())
        matched: list[str] = []
        for coord_name, el_tag, el_classes, el_id in elements:
            if sel_tag is not None and sel_tag.lower() != el_tag.lower():
                continue
            if sel_id is not None and sel_id != el_id:
                continue
            if sel_classes and not sel_classes.issubset(el_classes):
                continue
            matched.append(coord_name)
        return matched

    # ------------------------------------------------------------------
    # Cascade resolution
    # ------------------------------------------------------------------

    def resolve_cascade(
        self,
        bindings: list[CSSBinding],
        element_coord: str,
    ) -> dict[str, str]:
        """Return the winning CSS property → value map for *element_coord*.

        For each property, the binding with the highest specificity (then latest
        source_order) wins.
        """
        winners: dict[str, CSSBinding] = {}
        for binding in bindings:
            if element_coord not in binding.matched_elements:
                continue
            prop = binding.property_name
            if prop not in winners or binding.beats(winners[prop]):
                winners[prop] = binding
        return {prop: b.value for prop, b in winners.items()}

    # ------------------------------------------------------------------
    # Dead-rule detection
    # ------------------------------------------------------------------

    def dead_rules(self, bindings: list[CSSBinding]) -> list[str]:
        """Return selectors whose ``matched_elements`` list is empty."""
        return [b.selector for b in bindings if not b.matched_elements]


# ---------------------------------------------------------------------------
# 4. JSQueryKind
# ---------------------------------------------------------------------------

class JSQueryKind(str, Enum):
    """Kinds of JS DOM query, modelling different API entry points."""

    GET_BY_ID = "getElementById"
    QUERY_SELECTOR = "querySelector"
    QUERY_SELECTOR_ALL = "querySelectorAll"
    GET_BY_CLASS = "getElementsByClassName"
    GET_BY_TAG = "getElementsByTagName"
    GET_BY_NAME = "getElementsByName"


# ---------------------------------------------------------------------------
# 5. JSDOMQuery
# ---------------------------------------------------------------------------

@dataclass
class JSDOMQuery:
    """A JavaScript DOM query modelled as a section lookup in the DOM site."""

    query_id: str
    kind: JSQueryKind
    selector: str  # id string, CSS selector, class name, tag name, or name attr
    source_location: str = ""  # "file:line"

    # ------------------------------------------------------------------
    # Descent integration
    # ------------------------------------------------------------------

    def to_local_section(self, component_coord: str) -> LocalSection:
        """Represent this query as a local section on *component_coord*."""
        return LocalSection(
            coordinate=component_coord,
            judgment_data={
                "query_id": self.query_id,
                "kind": self.kind.value,
                "selector": self.selector,
                "source_location": self.source_location,
                "expected_result_count": self.expected_result_count(),
            },
            evidence_bundle=(f"js_query:{self.query_id}",),
            trust_level=1.0,
            provenance=(
                f"js_source:{self.source_location}" if self.source_location
                else "js_source:unknown",
            ),
        )

    def expected_result_count(self) -> str:
        """Return cardinality hint: 'zero_or_one' or 'zero_or_more'."""
        if self.kind in (JSQueryKind.GET_BY_ID, JSQueryKind.QUERY_SELECTOR):
            return "zero_or_one"
        return "zero_or_more"


# ---------------------------------------------------------------------------
# 6. JSHTMLBindingFunctor
# ---------------------------------------------------------------------------

class JSHTMLBindingFunctor:
    """Functor checking that JS DOM queries resolve against actual HTML elements.

    JS queries are section lookups: a query on a DOM coordinate either finds a
    section (the element exists) or yields a DescentObstruction (null/empty).
    """

    def __init__(self) -> None:
        self._css_functor = CSSBindingFunctor()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_matching_coords(
        self,
        query: JSDOMQuery,
        dom_elements: list[tuple[str, str, set[str], str | None]],
    ) -> list[str]:
        """Return coord_names that satisfy *query*."""
        kind = query.kind
        sel = query.selector

        if kind == JSQueryKind.GET_BY_ID:
            return [
                coord for coord, _tag, _classes, el_id in dom_elements
                if el_id == sel
            ]

        if kind in (JSQueryKind.QUERY_SELECTOR, JSQueryKind.QUERY_SELECTOR_ALL):
            return self._css_functor.match(sel, dom_elements)

        if kind == JSQueryKind.GET_BY_CLASS:
            return [
                coord for coord, _tag, classes, _id in dom_elements
                if sel in classes
            ]

        if kind == JSQueryKind.GET_BY_TAG:
            return [
                coord for coord, tag, _classes, _id in dom_elements
                if tag.lower() == sel.lower()
            ]

        if kind == JSQueryKind.GET_BY_NAME:
            # getElementsByName matches elements with a matching `name` attribute.
            # We cannot check name attributes from the (coord, tag, classes, id)
            # tuple, so we conservatively return empty — callers should use
            # querySelector('[name="..."]') for richer matching.
            return []

        return []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_query(
        self,
        query: JSDOMQuery,
        dom_elements: list[tuple[str, str, set[str], str | None]],
    ) -> DescentResult:
        """Check whether *query* resolves against *dom_elements*.

        Returns a successful ``DescentResult`` when at least one element is
        found, or a ``DescentResult`` wrapping a ``DescentObstruction`` when
        the query would return ``null`` / empty.
        """
        matches = self._find_matching_coords(query, dom_elements)

        if matches:
            section = GlobalSection(
                coordinate=query.query_id,
                merged_judgment={
                    "query_id": query.query_id,
                    "kind": query.kind.value,
                    "selector": query.selector,
                    "matched": matches,
                },
                constituent_sections=tuple(matches),
                overlap_evidence=(),
                certificate=f"js_query_resolved:{query.query_id}",
                trust_floor=1.0,
            )
            return DescentResult.success(section)

        obstruction = DescentObstruction(
            coordinate=query.query_id,
            violated_overlaps=(),
            partial_section={
                "query_id": query.query_id,
                "kind": query.kind.value,
                "selector": query.selector,
                "reason": "no_matching_elements",
            },
            persistence_id=f"null_query:{query.query_id}",
        )
        return DescentResult.failure(obstruction)

    def check_all_queries(
        self,
        queries: list[JSDOMQuery],
        dom_elements: list[tuple[str, str, set[str], str | None]],
    ) -> list[DescentResult]:
        """Check each query in *queries* against *dom_elements*."""
        return [self.check_query(q, dom_elements) for q in queries]

    def null_dereference_risks(
        self,
        queries: list[JSDOMQuery],
        dom_elements: list[tuple[str, str, set[str], str | None]],
    ) -> list[JSDOMQuery]:
        """Return queries that would return ``null`` (single-element queries).

        Accessing ``.innerHTML`` / ``.style`` etc. on a ``null`` result is a
        ``TypeError`` at runtime.  Only single-result query kinds are flagged
        because ``querySelectorAll`` returns an empty ``NodeList``, not
        ``null``.
        """
        risks: list[JSDOMQuery] = []
        for query in queries:
            if query.expected_result_count() != "zero_or_one":
                continue
            matches = self._find_matching_coords(query, dom_elements)
            if not matches:
                risks.append(query)
        return risks


# ---------------------------------------------------------------------------
# 7. HTMLCSSJSCoherenceReport
# ---------------------------------------------------------------------------

@dataclass
class HTMLCSSJSCoherenceReport:
    """Summary of coherence checks across HTML, CSS, and JS layers."""

    dead_css_rules: list[str]
    null_js_queries: list[JSDOMQuery]
    cascade_conflicts: list[tuple[str, str, str]]  # (element_coord, property, desc)
    all_passed: bool

    # ------------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines: list[str] = ["HTMLCSSJSCoherenceReport"]
        lines.append(f"  all_passed          : {self.all_passed}")
        lines.append(f"  dead_css_rules      : {len(self.dead_css_rules)}")
        for sel in self.dead_css_rules:
            lines.append(f"    - {sel!r} matches no elements")
        lines.append(f"  null_js_queries     : {len(self.null_js_queries)}")
        for q in self.null_js_queries:
            loc = f" @ {q.source_location}" if q.source_location else ""
            lines.append(
                f"    - {q.query_id!r} ({q.kind.value}({q.selector!r})){loc}"
                " → null → TypeError risk"
            )
        lines.append(f"  cascade_conflicts   : {len(self.cascade_conflicts)}")
        for elem, prop, desc in self.cascade_conflicts:
            lines.append(f"    - [{elem}] {prop}: {desc}")
        return "\n".join(lines)
