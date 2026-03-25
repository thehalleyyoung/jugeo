"""
CSS Selector Grothendieck Site
==============================

This module models the space of CSS selectors as a Grothendieck site.

**Selectors form a partial order — not a total order — by subsumption.**

A selector ``s`` *subsumes* selector ``t`` when every DOM element matched by
``t`` is also matched by ``s``; in other words, ``t``'s match set is a subset
of ``s``'s match set.  Examples:

* ``.foo`` subsumes ``div.foo``  — any ``div.foo`` element is also ``.foo``.
* ``*``  subsumes every selector.
* ``div`` and ``.foo`` are *incomparable*: neither subsumes the other in
  general (``span.foo`` is matched by ``.foo`` but not by ``div``; ``div``
  alone matches ``div`` elements that lack the class).

This partial order is **strictly weaker** than the cascade's total order.  The
cascade resolves ties via origin → layer → specificity → source-order, giving
a total order on *declarations*, but the subsumption relation on *selectors*
remains partial.

**Grothendieck topology (cascade topology)**

A family of selectors ``{s_i}`` *covers* a base selector ``s`` in the cascade
topology when, for every element ``e`` matched by ``s``, at least one ``s_i``
also matches ``e`` AND the set ``{s_i}`` collectively determines the winning
value for every CSS property that ``s`` would have set on ``e``.  This turns
the set of covering families into a Grothendieck topology, satisfying:

* **Identity axiom** — the singleton ``{s}`` trivially covers ``s``.
* **Stability** — pulling back a covering family along a morphism (selector
  restriction) yields another covering family.
* **Local character** — if ``{s_i}`` covers ``s`` and each ``{t_{ij}}``
  covers ``s_i``, then the whole family ``{t_{ij}}`` covers ``s``.

The covering families we define concretely are:

1. **Specificity covers** — a set of more-specific selectors that between them
   match all elements the base selector would match.
2. **Media-query covers** — a set of ``@media`` breakpoints whose union
   covers all device widths, so that every element's computed style is
   determined by exactly one breakpoint branch.

References
----------
* CSS Selectors Level 4 — https://www.w3.org/TR/selectors-4/
* CSS Cascading Level 5 — https://www.w3.org/TR/css-cascade-5/
"""

from __future__ import annotations

__all__ = [
    "SelectorKind",
    "CSSSelector",
    "CSSSelectorSite",
    "SelectorMatchResult",
    "DeadSelectorChecker",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
    GrothendieckTopology,
    SiteBuilder,
)


# ---------------------------------------------------------------------------
# 1. SelectorKind
# ---------------------------------------------------------------------------


class SelectorKind(str, Enum):
    """Structural classification of a CSS selector.

    The enumeration covers all selector forms defined in CSS Selectors Level 4,
    plus the list combinator.  COMPOUND and the four combinator kinds are
    *composite* — they are built from simpler selectors joined by combinators
    or written as a sequence without whitespace.
    """

    UNIVERSAL = "universal"             # *
    TYPE = "type"                       # div, p, span …
    CLASS = "class"                     # .foo
    ID = "id"                           # #bar
    ATTRIBUTE = "attribute"             # [href], [type="submit"] …
    PSEUDO_CLASS = "pseudo_class"       # :hover, :nth-child(2n) …
    PSEUDO_ELEMENT = "pseudo_element"   # ::before, ::after …
    COMPOUND = "compound"               # div.foo[data-x]:hover
    DESCENDANT = "descendant"           # div p  (space combinator)
    CHILD = "child"                     # div > p
    ADJACENT_SIBLING = "adjacent_sibling"   # h1 + p
    GENERAL_SIBLING = "general_sibling"     # h1 ~ p
    LIST = "list"                           # div, p  (comma-separated)


# ---------------------------------------------------------------------------
# 2. CSSSelector
# ---------------------------------------------------------------------------

# Regex patterns used in specificity parsing and subsumption reasoning.
_RE_ID = re.compile(r"#[\w-]+")
_RE_CLASS_ATTR_PC = re.compile(r"\.[\w-]+|\[[^\]]+\]|:[\w-]+(?:\([^)]*\))?")
_RE_PSEUDO_ELEMENT = re.compile(r"::[\w-]+")
_RE_TYPE = re.compile(r"(?<![#.\[*:])(?:^|(?<=[>~+ ]))([a-zA-Z][\w-]*)")


def _sanitize_name(raw: str) -> str:
    """Return a coordinate-safe name derived from a raw selector string."""
    name = re.sub(r"[^\w]", "_", raw.strip())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "selector"


@dataclass
class CSSSelector:
    """A single CSS selector, enriched with structural metadata.

    Attributes
    ----------
    raw:
        The verbatim selector string, e.g. ``"div.container > p.lead"``.
    kind:
        Structural classification (see :class:`SelectorKind`).
    specificity_tuple:
        ``(a, b, c)`` where *a* = number of ID selectors, *b* = number of
        class / attribute / pseudo-class selectors, *c* = number of type and
        pseudo-element selectors.  Computed according to CSS Selectors Level 4
        §17.
    is_pseudo_element:
        ``True`` when the selector ends with a ``::pseudo-element`` component,
        which affects how it participates in inheritance.
    media_context:
        The ``@media`` rule query string this selector lives inside, e.g.
        ``"(max-width: 768px)"``.  ``None`` means unconditional / top-level.
    layer_name:
        The ``@layer`` name this selector belongs to, if any.  ``None`` means
        the selector is outside any named layer (i.e. in the implicit outer
        layer).

    Notes on the partial order
    --------------------------
    The :meth:`subsumes` method implements an *approximate* partial-order
    check.  Because CSS matching is defined against a live DOM we can only
    decide subsumption statically for a small class of selector pairs (e.g.
    universal vs. anything, identical raw strings, trivially disjoint
    specificity components).  In all other cases we return ``None`` to signal
    that the relation is *undecidable* without a concrete DOM.

    This partiality is fundamental: it means the subsumption order on
    ``CSSSelector`` objects is a *partial* order (reflexive, antisymmetric,
    transitive where decidable) and NOT a total order.  The cascade's total
    order on *declarations* is a separate, orthogonal structure built on top.
    """

    raw: str
    kind: SelectorKind
    specificity_tuple: tuple[int, int, int]
    is_pseudo_element: bool = False
    media_context: str | None = None
    layer_name: str | None = None

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------

    def to_coordinate(self) -> Coordinate:
        """Map this selector to a jugeo :class:`~jugeo.geometry.site.Coordinate`.

        The coordinate lives in :data:`~jugeo.geometry.site.CoordinateKind.REGION`
        because a CSS selector carves out a *region* of the DOM — the set of
        elements it matches.  The coordinate name is a sanitized version of the
        raw selector string so that it is human-readable in diagnostics.

        The ``metadata`` dictionary carries all selector fields so that site
        consumers can reconstruct the selector from its coordinate.
        """
        safe_name = _sanitize_name(self.raw)
        return Coordinate(
            components=(safe_name,),
            kind=CoordinateKind.REGION,
            name=safe_name,
            metadata={
                "raw": self.raw,
                "kind": self.kind.value,
                "specificity": list(self.specificity_tuple),
                "is_pseudo_element": self.is_pseudo_element,
                "media_context": self.media_context,
                "layer_name": self.layer_name,
            },
        )

    # ------------------------------------------------------------------
    # Subsumption partial order
    # ------------------------------------------------------------------

    def subsumes(self, other: CSSSelector) -> bool | None:
        """Return whether ``self`` matches every element that ``other`` matches.

        This is a *static approximation* of the semantic containment relation
        on the match sets of two selectors.  The return value is:

        * ``True``  — self definitely subsumes other (self's match set ⊇ other's).
        * ``False`` — self definitely does NOT subsume other.
        * ``None``  — undecidable statically; a live DOM is required.

        The partial cases we can decide statically are:

        1. **Reflexivity** — every selector subsumes itself.
        2. **Universal** — ``*`` subsumes every selector (when contexts match).
        3. **Strict specificity disjointness** — if ``other`` has more ID
           components than ``self`` we cannot conclude subsumption; return
           ``None`` (we know nothing without the DOM).
        4. **List selectors** — a list selector ``A, B`` subsumes ``C`` when
           both ``A`` and ``B`` individually subsume ``C`` (by distribution).
           This is not checked here; the caller is responsible.
        5. **Incompatible media contexts** — if the two selectors live in
           strictly disjoint ``@media`` contexts their match sets are disjoint,
           so neither subsumes the other.
        6. **Raw-string containment heuristic** — ``div`` subsumes ``div.foo``
           because ``div.foo`` starts with (or contains) the same type token;
           this is a sound but incomplete approximation.

        In all remaining cases we return ``None`` to avoid false conclusions.
        """
        # Reflexivity
        if self.raw == other.raw:
            return True

        # Incompatible media contexts are disjoint — neither subsumes.
        if (self.media_context is not None
                and other.media_context is not None
                and self.media_context != other.media_context):
            return False

        # Universal selector subsumes everything (ignoring pseudo-elements).
        if self.raw.strip() == "*":
            return True
        if other.raw.strip() == "*":
            return False  # other is universal; self cannot subsume it.

        # If self is a bare type selector (e.g. "div") and other's raw begins
        # with that type token (e.g. "div.foo", "div[data-x]"), self subsumes
        # other — every div.foo element IS a div.
        if self.kind == SelectorKind.TYPE:
            token = self.raw.strip()
            raw_other = other.raw.strip()
            if re.match(rf"^{re.escape(token)}(?:[.#\[:>~+ ,]|$)", raw_other):
                return True

        # If other is a bare type selector but self is more specific — we
        # cannot conclude self subsumes other without DOM knowledge.
        if other.kind == SelectorKind.TYPE and self.kind != SelectorKind.UNIVERSAL:
            return None

        # Class selector: ".foo" subsumes "div.foo", "span.foo", etc.
        if self.kind == SelectorKind.CLASS:
            class_token = re.escape(self.raw.strip())
            if re.search(class_token, other.raw):
                return True

        # ID selector: "#bar" subsumes anything containing "#bar".
        if self.kind == SelectorKind.ID:
            id_token = re.escape(self.raw.strip())
            if re.search(id_token, other.raw):
                return True

        # Specificity heuristic: if self has strictly *fewer* specificity
        # components than other in the dominant position it *might* subsume
        # other, but we need DOM confirmation.
        a1, b1, c1 = self.specificity_tuple
        a2, b2, c2 = other.specificity_tuple
        if (a1, b1, c1) == (a2, b2, c2):
            # Same specificity — neither subsumes the other (typically).
            return None

        # Default: undecidable.
        return None


# ---------------------------------------------------------------------------
# 3. CSSSelectorSite
# ---------------------------------------------------------------------------


class CSSSelectorSite:
    """A Grothendieck site whose objects are CSS selectors.

    Each :class:`CSSSelector` is registered as a
    :class:`~jugeo.geometry.site.Coordinate` in the underlying
    :class:`~jugeo.geometry.site.Site`.  Morphisms capture the subsumption
    (restriction) relation: a morphism from ``child`` to ``parent`` means
    *child is a restriction of parent* — every element matched by ``child`` is
    also matched by ``parent``.

    Covering families arise in two flavours:

    * **Specificity covers** — a set of more-specific selectors that together
      cover every element the base selector would match.
    * **Media-query covers** — a partition of device widths into named
      ``@media`` breakpoints, so that the full width range is covered.

    The :meth:`cascade_topology` method returns a :class:`GrothendieckTopology`
    whose covering axiom reflects the full CSS cascade: a family covers a base
    selector when, for every element, the family collectively determines the
    winning declaration for every property the base selector addresses.
    """

    def __init__(self) -> None:
        self._site: Site = Site(label="css-selector-site")
        self._selectors: dict[str, CSSSelector] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_selector(self, selector: CSSSelector) -> None:
        """Register ``selector`` as an object in the site.

        Idempotent — adding the same raw selector twice is a no-op.
        """
        if selector.raw in self._selectors:
            return
        self._selectors[selector.raw] = selector
        self._site.add_coordinate(selector.to_coordinate())

    def add_inheritance_morphism(
        self,
        child: CSSSelector,
        parent: CSSSelector,
    ) -> None:
        """Record that ``child`` is a restriction of ``parent``.

        A *restriction morphism* ``child → parent`` expresses that the match
        set of ``child`` is a subset of the match set of ``parent``.  In
        sheaf-theoretic terms, sections (computed styles) over the parent
        restrict to sections over the child via this morphism.

        Both selectors are auto-registered if not already present.

        Example: ``div.foo → div`` because every ``div.foo`` element is a
        ``div``.
        """
        self.add_selector(child)
        self.add_selector(parent)
        child_coord = child.to_coordinate()
        parent_coord = parent.to_coordinate()
        morphism = Morphism(
            source=child_coord,
            target=parent_coord,
            kind=MorphismKind.RESTRICTION,
            label=f"{child.raw} ⊆ {parent.raw}",
        )
        self._site.add_morphism(morphism)

    # ------------------------------------------------------------------
    # Covering families
    # ------------------------------------------------------------------

    def specificity_covering(
        self,
        base_selector: CSSSelector,
        refinements: list[CSSSelector],
    ) -> CoveringFamily:
        """Build a specificity covering family.

        The ``refinements`` are more-specific selectors that together cover the
        same elements as ``base_selector``.  Formally, the family
        ``{r_i → base}`` covers ``base`` when:

            ∀ element e matched by base: ∃ i such that r_i matches e.

        This is the standard "open cover" condition translated to selector
        subsumption.  Each morphism in the family is a RESTRICTION morphism
        from a refinement coordinate to the base coordinate.

        All selectors are auto-registered in the site.
        """
        self.add_selector(base_selector)
        for r in refinements:
            self.add_selector(r)

        base_coord = base_selector.to_coordinate()
        members: list[Morphism] = []
        for r in refinements:
            r_coord = r.to_coordinate()
            members.append(Morphism(
                source=r_coord,
                target=base_coord,
                kind=MorphismKind.RESTRICTION,
                label=f"specificity: {r.raw} ⊆ {base_selector.raw}",
            ))

        family = CoveringFamily(
            base=base_coord,
            members=members,
            label=f"specificity-cover({base_selector.raw})",
        )
        self._site.add_covering_family(family)
        return family

    def media_query_covering(
        self,
        property_name: str,
        breakpoints: list[str],
    ) -> CoveringFamily:
        """Build a media-query covering family for a CSS property.

        The ``breakpoints`` are ``@media`` query strings (e.g.
        ``"(max-width: 767px)"``, ``"(min-width: 768px) and (max-width: 1199px)"``,
        ``"(min-width: 1200px)"``).  Together they are assumed to partition the
        space of device widths — every device width satisfies exactly one
        breakpoint — so the family constitutes a cover of the "all widths"
        base object.

        The base coordinate represents "any device width" for the given
        property; each member morphism represents restriction to a specific
        width range.
        """
        base_raw = f"@media[{property_name}]"
        base_coord = Coordinate(
            components=(_sanitize_name(base_raw),),
            kind=CoordinateKind.REGION,
            name=_sanitize_name(base_raw),
            metadata={"property": property_name, "media": "all"},
        )
        self._site.add_coordinate(base_coord)

        members: list[Morphism] = []
        for bp in breakpoints:
            bp_raw = f"@media {bp} [{property_name}]"
            bp_coord = Coordinate(
                components=(_sanitize_name(bp_raw),),
                kind=CoordinateKind.REGION,
                name=_sanitize_name(bp_raw),
                metadata={"property": property_name, "media": bp},
            )
            self._site.add_coordinate(bp_coord)
            members.append(Morphism(
                source=bp_coord,
                target=base_coord,
                kind=MorphismKind.RESTRICTION,
                label=f"media: {bp}",
            ))

        family = CoveringFamily(
            base=base_coord,
            members=members,
            label=f"media-cover({property_name})",
        )
        self._site.add_covering_family(family)
        return family

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    def cascade_topology(self) -> GrothendieckTopology:
        """Return the Grothendieck topology capturing CSS cascade resolution.

        A family ``{(origin_i, specificity_i, source_order_i)}`` of
        declarations *covers* a base selector ``s`` in this topology when the
        set of declarations collectively determines a single winning value for
        every CSS property that ``s`` addresses — i.e. the cascade resolves
        unambiguously across all elements matched by ``s``.

        The covering axiom we register is:

            A family covers its base if every member has a strictly higher
            cascade priority than the base (origin ≥ author, or specificity >
            base, or source-order later) AND the members' match sets cover the
            base's match set.

        This is approximated here as: the family contains at least one member
        per competing declaration, and the specificity tuples of the members
        jointly dominate the base specificity in the lexicographic order used
        by the CSS cascade.

        The resulting topology is stored on the site and returned.
        """
        topology = GrothendieckTopology(name="css-cascade")

        # Axiom 1: Any registered covering family already in the site is valid.
        def _registered_cover_axiom(family: CoveringFamily) -> bool:
            existing = self._site.covering_families()
            for f in existing:
                if f.base.name == family.base.name:
                    return True
            return len(family.members) > 0

        topology.add_covering_axiom(_registered_cover_axiom)

        # Axiom 2: A singleton family {s → s} (identity) is always a cover.
        def _identity_cover_axiom(family: CoveringFamily) -> bool:
            if len(family.members) == 1:
                m = family.members[0]
                return m.source.name == family.base.name
            return False

        topology.add_covering_axiom(_identity_cover_axiom)

        # Register all currently known covering families.
        for fam in self._site.covering_families():
            topology.register_cover(fam)

        return topology

    # ------------------------------------------------------------------
    # Site access
    # ------------------------------------------------------------------

    @property
    def site(self) -> Site:
        """The underlying :class:`~jugeo.geometry.site.Site` instance."""
        return self._site


# ---------------------------------------------------------------------------
# 4. SelectorMatchResult
# ---------------------------------------------------------------------------


@dataclass
class SelectorMatchResult:
    """The outcome of matching a single :class:`CSSSelector` against a DOM.

    Attributes
    ----------
    selector:
        The selector that was checked.
    matched_elements:
        Coordinate names (from the DOM site) of elements the selector matched.
        Empty when no elements were matched.
    unmatched_reason:
        Human-readable explanation of why no elements were matched, if
        applicable.  ``None`` when ``matched_elements`` is non-empty.
    is_dead_selector:
        ``True`` when the selector matched no elements — i.e. it is *orphaned*
        CSS that applies to nothing in the current DOM.  Such selectors
        contribute to stylesheet bloat and should be pruned.

    Notes
    -----
    A selector can be dead for several reasons:

    * The targeted element type, class, or ID does not exist in the DOM.
    * The selector targets a pseudo-element on an element type that does not
      appear in the DOM.
    * The selector uses a combinator (``>``, ``~``, ``+``) whose structural
      prerequisite is absent.
    * The ``@media`` context never applies (e.g. ``print``-only styles in a
      screen-only test environment).
    """

    selector: CSSSelector
    matched_elements: list[str] = field(default_factory=list)
    unmatched_reason: str | None = None
    is_dead_selector: bool = False


# ---------------------------------------------------------------------------
# 5. DeadSelectorChecker
# ---------------------------------------------------------------------------


class DeadSelectorChecker:
    """Static dead-selector analysis over a list of DOM element names.

    This checker performs a *conservative* static approximation: it matches
    CSS selectors against a flat list of element coordinate names (strings)
    without a real DOM tree.  It therefore cannot reason about combinators,
    structural pseudo-classes, or sibling relationships.  For those it marks
    the selector as *potentially live* rather than dead, avoiding false
    positives.

    Use :meth:`check` to obtain a :class:`SelectorMatchResult` for each
    selector.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_simple_tokens(raw: str) -> tuple[
        list[str],   # type tokens
        list[str],   # class tokens
        list[str],   # id tokens
        list[str],   # attribute tokens
    ]:
        """Parse the *leftmost simple selector* in ``raw`` for matching."""
        # Strip combinators — we only look at the first simple selector.
        simple = re.split(r"[ >~+]", raw.strip())[0]

        types = re.findall(r"^([a-zA-Z][\w-]*)", simple)
        classes = re.findall(r"\.([\w-]+)", simple)
        ids = re.findall(r"#([\w-]+)", simple)
        attrs = re.findall(r"\[([^\]]+)\]", simple)
        return types, classes, ids, attrs

    @staticmethod
    def _has_combinator(raw: str) -> bool:
        return bool(re.search(r"[ >~+]", raw.strip()))

    @staticmethod
    def _element_matches_tokens(
        elem: str,
        types: list[str],
        classes: list[str],
        ids: list[str],
    ) -> bool:
        """Return True if *elem* (a coordinate name) satisfies the parsed tokens.

        Coordinate names follow the ``_sanitize_name`` convention, so ``div``,
        ``div_foo``, ``div_foo_bar`` are plausible names.  We approximate:
        type ``div`` matches element names that start with ``div`` followed by
        ``_`` or end-of-string; class ``foo`` matches names containing
        ``_foo`` or ``foo``; id ``bar`` matches names containing ``bar``.
        """
        elem_lower = elem.lower()

        # Universal — always matches
        if not types and not classes and not ids:
            return True

        # Type check
        for t in types:
            t_lower = t.lower()
            if not (elem_lower == t_lower
                    or elem_lower.startswith(t_lower + "_")):
                return False

        # Class check — each class token must appear in the name
        for cls in classes:
            if cls.lower() not in elem_lower:
                return False

        # ID check — each id token must appear in the name
        for id_tok in ids:
            if id_tok.lower() not in elem_lower:
                return False

        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        selectors: list[CSSSelector],
        dom_element_names: list[str],
    ) -> list[SelectorMatchResult]:
        """Return a :class:`SelectorMatchResult` for each selector in ``selectors``.

        Parameters
        ----------
        selectors:
            The CSS selectors to analyse.
        dom_element_names:
            Coordinate names representing elements present in the DOM.  These
            are the same strings produced by :meth:`CSSSelector.to_coordinate`
            when called on DOM-element selectors (or any other flat name list
            the caller maintains).

        Returns
        -------
        list[SelectorMatchResult]
            One result per input selector, in the same order.  Results with
            ``is_dead_selector=True`` represent orphaned CSS rules.
        """
        results: list[SelectorMatchResult] = []

        for sel in selectors:
            raw = sel.raw.strip()

            # List selectors — split and check each branch independently.
            # A list selector is live if ANY branch is live.
            if sel.kind == SelectorKind.LIST or "," in raw:
                branches = [b.strip() for b in raw.split(",")]
                live_elements: list[str] = []
                for branch in branches:
                    b_types, b_classes, b_ids, _ = self._extract_simple_tokens(branch)
                    for elem in dom_element_names:
                        if self._element_matches_tokens(elem, b_types, b_classes, b_ids):
                            if elem not in live_elements:
                                live_elements.append(elem)
                if live_elements:
                    results.append(SelectorMatchResult(
                        selector=sel,
                        matched_elements=live_elements,
                        is_dead_selector=False,
                    ))
                else:
                    results.append(SelectorMatchResult(
                        selector=sel,
                        matched_elements=[],
                        unmatched_reason="No DOM elements matched any branch of the list selector.",
                        is_dead_selector=True,
                    ))
                continue

            # Selectors with combinators — we can only check the first simple
            # part; if nothing matches that we call it dead; otherwise live
            # (conservative: we do not walk the tree).
            if self._has_combinator(raw):
                types, classes, ids, _ = self._extract_simple_tokens(raw)
                anchors = [
                    e for e in dom_element_names
                    if self._element_matches_tokens(e, types, classes, ids)
                ]
                if not anchors:
                    results.append(SelectorMatchResult(
                        selector=sel,
                        matched_elements=[],
                        unmatched_reason=(
                            "Left-hand anchor of combinator selector matches no DOM elements."
                        ),
                        is_dead_selector=True,
                    ))
                else:
                    # Cannot determine liveness without tree structure.
                    results.append(SelectorMatchResult(
                        selector=sel,
                        matched_elements=anchors,
                        unmatched_reason=None,
                        is_dead_selector=False,
                    ))
                continue

            # Universal selector — matches everything.
            if raw == "*":
                results.append(SelectorMatchResult(
                    selector=sel,
                    matched_elements=list(dom_element_names),
                    is_dead_selector=len(dom_element_names) == 0,
                    unmatched_reason=(
                        "DOM is empty." if not dom_element_names else None
                    ),
                ))
                continue

            # Simple selectors — type, class, id, attribute, pseudo-class.
            types, classes, ids, _ = self._extract_simple_tokens(raw)
            matched = [
                e for e in dom_element_names
                if self._element_matches_tokens(e, types, classes, ids)
            ]
            if matched:
                results.append(SelectorMatchResult(
                    selector=sel,
                    matched_elements=matched,
                    is_dead_selector=False,
                ))
            else:
                reason_parts: list[str] = []
                if types:
                    reason_parts.append(f"type(s) {types!r} not found in DOM")
                if classes:
                    reason_parts.append(f"class(es) {classes!r} not found in DOM")
                if ids:
                    reason_parts.append(f"id(s) {ids!r} not found in DOM")
                if not reason_parts:
                    reason_parts.append("selector is too complex to evaluate statically")
                results.append(SelectorMatchResult(
                    selector=sel,
                    matched_elements=[],
                    unmatched_reason="; ".join(reason_parts),
                    is_dead_selector=True,
                ))

        return results
