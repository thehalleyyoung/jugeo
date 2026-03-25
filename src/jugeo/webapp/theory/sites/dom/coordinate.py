"""DOM-aware coordinate and morphism types for the jugeo webapp theory layer.

Each HTML/SVG/MathML node in a document corresponds to a :class:`DOMCoordinate`
that wraps an underlying :class:`~jugeo.geometry.site.Coordinate`.  Structural
and cross-document relationships are expressed as :class:`DOMTreeMorphism`
arrows whose :class:`DOMMorphismKind` classifies the edge semantically.

The module is intentionally free of external dependencies — only the stdlib
``re`` module is used for CSS selector parsing.
"""

from __future__ import annotations

__all__ = [
    "DOMMorphismKind",
    "DOMCoordinate",
    "DOMTreeMorphism",
]

import re
from dataclasses import dataclass, field
from typing import Any

from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind


# ---------------------------------------------------------------------------
# Regex helpers for CSS selector tokenisation
# ---------------------------------------------------------------------------

# Simple tag name: "div", "h1", "svg:circle", "*"
_RE_TAG = re.compile(r"^([a-zA-Z][a-zA-Z0-9:-]*|\*)")

# #id fragment
_RE_ID = re.compile(r"#([a-zA-Z_][a-zA-Z0-9_-]*)")

# .class fragments
_RE_CLASS = re.compile(r"\.([a-zA-Z_][a-zA-Z0-9_-]*)")

# [attr] or [attr=val] or [attr="val"] — captures attribute name and optional value.
# We support the exact-match operator `=` only (and bare presence); the value may be
# optionally quoted with single or double quotes.
_RE_ATTR = re.compile(r'\[([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:=(?:"([^"]*)"|\'([^\']*)\'|([^\]]*)))?\]')


def _parse_simple_selector(
    selector: str,
) -> dict[str, Any]:
    """Return a dict describing a *simple* (non-compound) CSS selector.

    Keys:
      ``tag``     – lower-cased tag name or ``""`` if absent.
      ``id``      – id string or ``None``.
      ``classes`` – frozenset of class names.
      ``attrs``   – dict mapping attribute names to expected values (or ``None``
                    for bare ``[attr]`` presence tests).
    """
    s = selector.strip()
    tag = ""
    m = _RE_TAG.match(s)
    if m:
        raw = m.group(1)
        tag = "" if raw == "*" else raw.lower()

    id_val: str | None = None
    m = _RE_ID.search(s)
    if m:
        id_val = m.group(1)

    classes: frozenset[str] = frozenset(c for c in _RE_CLASS.findall(s))

    attrs: dict[str, str | None] = {}
    for m in _RE_ATTR.finditer(s):
        attr_name = m.group(1)
        # Groups 2/3/4 are the three possible capture groups for the value.
        val = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else m.group(4)
        )
        attrs[attr_name] = val  # None means bare [attr] presence test

    return {"tag": tag, "id": id_val, "classes": classes, "attrs": attrs}


# ---------------------------------------------------------------------------
# DOMMorphismKind — string constants for DOM edge semantics
# ---------------------------------------------------------------------------


class DOMMorphismKind:
    """Namespace of string constants classifying edges between DOM coordinates.

    **Structural** edges describe the static tree topology:

    * ``PARENT_CHILD``    — directed edge from a parent node to one of its children.
    * ``CHILD_PARENT``    — inverse of PARENT_CHILD (useful for traversal morphisms).
    * ``SIBLING_NEXT``    — edge from a node to its immediately following sibling.
    * ``SIBLING_PREV``    — edge from a node to its immediately preceding sibling.
    * ``SHADOW_HOST``     — edge from a shadow-host element to its shadow root.
    * ``SLOT_ASSIGNMENT`` — edge from a ``<slot>`` element to its assigned node.

    **Reference** edges describe semantic cross-references within the document:

    * ``ID_REFERENCE``    — an attribute value references another element by ``id``.
    * ``ARIA_REFERENCE``  — an ARIA attribute (``aria-labelledby``, ``aria-controls``,
                            etc.) references another element.
    * ``LABEL_FOR``       — a ``<label for="…">`` points to a form control.
    * ``HREF_TARGET``     — a hyperlink ``href`` points to a document or fragment.
    * ``FORM_OWNER``      — a form control is associated with a ``<form>`` element.
    """

    # Structural
    PARENT_CHILD = "parent_child"
    CHILD_PARENT = "child_parent"
    SIBLING_NEXT = "sibling_next"
    SIBLING_PREV = "sibling_prev"
    SHADOW_HOST = "shadow_host"
    SLOT_ASSIGNMENT = "slot_assignment"

    # Reference
    ID_REFERENCE = "id_reference"
    ARIA_REFERENCE = "aria_reference"
    LABEL_FOR = "label_for"
    HREF_TARGET = "href_target"
    FORM_OWNER = "form_owner"

    # Convenience sets used by DOMTreeMorphism predicates.
    _STRUCTURAL: frozenset[str] = frozenset(
        {PARENT_CHILD, CHILD_PARENT, SIBLING_NEXT, SIBLING_PREV, SHADOW_HOST, SLOT_ASSIGNMENT}
    )
    _REFERENCE: frozenset[str] = frozenset(
        {ID_REFERENCE, ARIA_REFERENCE, LABEL_FOR, HREF_TARGET, FORM_OWNER}
    )

    # Mapping from DOM kind to the closest jugeo MorphismKind.
    _JUGEO_KIND: dict[str, MorphismKind] = {
        PARENT_CHILD: MorphismKind.RESTRICTION,   # narrowing scope downward
        CHILD_PARENT: MorphismKind.INCLUSION,     # embedding into wider scope
        SIBLING_NEXT: MorphismKind.TRANSPORT,     # lateral movement
        SIBLING_PREV: MorphismKind.TRANSPORT,
        SHADOW_HOST: MorphismKind.INCLUSION,      # host contains shadow tree
        SLOT_ASSIGNMENT: MorphismKind.RESTRICTION,
        ID_REFERENCE: MorphismKind.REFINEMENT,    # semantic link / re-labelling
        ARIA_REFERENCE: MorphismKind.REFINEMENT,
        LABEL_FOR: MorphismKind.REFINEMENT,
        HREF_TARGET: MorphismKind.TRANSPORT,
        FORM_OWNER: MorphismKind.REFINEMENT,
    }


# ---------------------------------------------------------------------------
# DOMCoordinate
# ---------------------------------------------------------------------------


@dataclass
class DOMCoordinate:
    """A jugeo coordinate enriched with DOM-specific metadata.

    The ``coord`` field is the underlying
    :class:`~jugeo.geometry.site.Coordinate`; its ``components`` encode the
    node's position in the document tree (e.g. ``("html", "body", "main",
    "section")``).  All other fields carry DOM-layer information that is
    meaningful only within the browser/HTML context.

    Parameters
    ----------
    coord:
        The jugeo coordinate.  Typically constructed with
        ``kind=CoordinateKind.REGION`` for most DOM nodes.
    node_kind:
        The tag name in lower-case (``"div"``, ``"span"``, …) or a special
        node-type string (``"#text"``, ``"#comment"``, ``"#document"``).
    element_id:
        The ``id`` attribute value, or ``None`` if absent.
    class_list:
        Frozen set of CSS class names present on the element.
    attributes:
        All HTML attributes as a plain ``{name: value}`` mapping.
    namespace:
        Document namespace — one of ``"html"``, ``"svg"``, ``"mathml"``.
    is_shadow_root:
        ``True`` when this coordinate represents a shadow-root boundary node.
    depth:
        Zero-based nesting depth in the document tree (mirrors
        ``len(coord.components) - 1`` when the root is at depth 0).
    """

    coord: Coordinate
    node_kind: str
    element_id: str | None = None
    class_list: frozenset[str] = field(default_factory=frozenset)
    attributes: dict[str, str] = field(default_factory=dict)
    namespace: str = "html"
    is_shadow_root: bool = False
    depth: int = 0

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_selector(cls, selector: str) -> DOMCoordinate:
        """Build a *standalone* :class:`DOMCoordinate` from a simple CSS selector.

        Supports: tag, ``#id``, ``.class`` (multiple), ``[attr]``,
        ``[attr=value]``.  Compound selectors (combinators ``>``, ``+``,
        ``~``, space) are **not** supported — only a single simple-selector
        fragment is accepted.

        Examples
        --------
        >>> DOMCoordinate.from_selector("div#hero.banner")
        DOMCoordinate(node_kind='div', element_id='hero', class_list=frozenset({'banner'}), ...)
        >>> DOMCoordinate.from_selector(".active")
        DOMCoordinate(node_kind='', ...)
        >>> DOMCoordinate.from_selector("[data-role=dialog]")
        DOMCoordinate(node_kind='', attributes={'data-role': 'dialog'}, ...)
        """
        parsed = _parse_simple_selector(selector)
        tag = parsed["tag"]
        id_val: str | None = parsed["id"]
        classes: frozenset[str] = parsed["classes"]

        # Build attribute dict; include [attr=val] entries but not bare [attr].
        attrs: dict[str, str] = {}
        for attr_name, attr_val in parsed["attrs"].items():
            if attr_val is not None:
                attrs[attr_name] = attr_val
            # Bare [attr] — presence only; record with empty string so callers
            # can distinguish "attribute present" from "attribute absent".
            else:
                attrs[attr_name] = ""

        # Derive a single-component coordinate from the tag (or a synthetic
        # name when the selector has no tag, e.g. ".active").
        node_name = tag or (f"#{id_val}" if id_val else "_")
        coord = Coordinate(
            components=(node_name,),
            kind=CoordinateKind.REGION,
        )

        return cls(
            coord=coord,
            node_kind=tag,
            element_id=id_val,
            class_list=classes,
            attributes=attrs,
        )

    # ------------------------------------------------------------------
    # Selector matching
    # ------------------------------------------------------------------

    def matches_selector(self, selector: str) -> bool:
        """Return ``True`` if this element matches the given simple CSS selector.

        Supported selector fragments (may be combined in one string):

        * ``tag``          — ``node_kind == tag`` (case-insensitive)
        * ``#id``          — ``element_id == id``
        * ``.class``       — class name present in ``class_list``
        * ``[attr]``       — attribute key present in ``attributes``
        * ``[attr=value]`` — attribute present *and* value matches exactly

        Pseudo-classes and combinators are **not** supported.
        """
        parsed = _parse_simple_selector(selector)

        # Tag check (empty tag or "*" means any tag).
        if parsed["tag"] and parsed["tag"] != self.node_kind.lower():
            return False

        # ID check.
        if parsed["id"] is not None and parsed["id"] != self.element_id:
            return False

        # Class checks — all listed classes must be present.
        if not parsed["classes"].issubset(self.class_list):
            return False

        # Attribute checks.
        for attr_name, expected_val in parsed["attrs"].items():
            if attr_name not in self.attributes:
                return False
            if expected_val is not None and self.attributes[attr_name] != expected_val:
                return False

        return True

    # ------------------------------------------------------------------
    # Path rendering
    # ------------------------------------------------------------------

    def path_string(self) -> str:
        """Return a CSS selector path representing this element's location.

        Each component of ``coord.components`` is used as the ancestor tag
        name; the final segment is augmented with the element's ``id`` and
        ``class_list``.

        Example::

            # coord.components = ("html", "body", "div", "p")
            # element_id = None, class_list = {"intro"}
            "html > body > div > p.intro"
        """
        components = list(self.coord.components)
        if not components:
            return self._selector_fragment()

        if len(components) == 1:
            return self._selector_fragment()

        # All ancestors use just their component name as-is.
        ancestors = components[:-1]
        parts: list[str] = list(ancestors)

        # Final component gets the full selector decoration.
        parts.append(self._selector_fragment())
        return " > ".join(parts)

    def _selector_fragment(self) -> str:
        """Build a selector fragment for *this* node: ``tag#id.class1.class2``."""
        # Special node types ("#text", "#comment") are not valid CSS selectors;
        # represent them as-is in brackets for readability.
        if self.node_kind.startswith("#"):
            return self.node_kind

        frag = self.node_kind  # may be empty for class/id-only coordinates
        if self.element_id:
            frag += f"#{self.element_id}"
        for cls in sorted(self.class_list):
            frag += f".{cls}"
        return frag or "*"

    # ------------------------------------------------------------------
    # Jugeo interop
    # ------------------------------------------------------------------

    def to_jugeo_coord(self) -> Coordinate:
        """Return the underlying :class:`~jugeo.geometry.site.Coordinate`."""
        return self.coord

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        cls_str = ", ".join(sorted(self.class_list))
        return (
            f"DOMCoordinate("
            f"path={self.path_string()!r}, "
            f"node_kind={self.node_kind!r}, "
            f"id={self.element_id!r}, "
            f"classes={{{cls_str}}}, "
            f"ns={self.namespace!r}"
            f")"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DOMCoordinate):
            return NotImplemented
        return (
            self.coord == other.coord
            and self.node_kind == other.node_kind
            and self.element_id == other.element_id
            and self.class_list == other.class_list
            and self.attributes == other.attributes
            and self.namespace == other.namespace
            and self.is_shadow_root == other.is_shadow_root
            and self.depth == other.depth
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.coord,
                self.node_kind,
                self.element_id,
                self.class_list,
                tuple(sorted(self.attributes.items())),
                self.namespace,
                self.is_shadow_root,
                self.depth,
            )
        )


# ---------------------------------------------------------------------------
# DOMTreeMorphism
# ---------------------------------------------------------------------------


@dataclass
class DOMTreeMorphism:
    """A typed directed edge between two :class:`DOMCoordinate` nodes.

    This is the DOM-layer equivalent of a jugeo
    :class:`~jugeo.geometry.site.Morphism`.  The ``kind`` field must be one
    of the :class:`DOMMorphismKind` string constants; it controls how the
    morphism is projected back into the jugeo geometry layer via
    :meth:`to_jugeo_morphism`.

    Parameters
    ----------
    source:
        The origin :class:`DOMCoordinate`.
    target:
        The destination :class:`DOMCoordinate`.
    kind:
        One of the :class:`DOMMorphismKind` constants.
    label:
        Optional human-readable label (e.g. an attribute name, slot name, …).
    """

    source: DOMCoordinate
    target: DOMCoordinate
    kind: str
    label: str = ""

    # ------------------------------------------------------------------
    # Jugeo interop
    # ------------------------------------------------------------------

    def to_jugeo_morphism(self) -> Morphism:
        """Project this DOM morphism onto the underlying jugeo geometry layer.

        The DOM ``kind`` is mapped to a :class:`~jugeo.geometry.site.MorphismKind`
        via :attr:`DOMMorphismKind._JUGEO_KIND`.  Unknown kinds default to
        :attr:`~jugeo.geometry.site.MorphismKind.TRANSPORT`.
        """
        jugeo_kind = DOMMorphismKind._JUGEO_KIND.get(self.kind, MorphismKind.TRANSPORT)
        return Morphism(
            source=self.source.to_jugeo_coord(),
            target=self.target.to_jugeo_coord(),
            kind=jugeo_kind,
            label=self.label or self.kind,
        )

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_structural(self) -> bool:
        """Return ``True`` for edges that describe the tree topology.

        Structural kinds: ``PARENT_CHILD``, ``CHILD_PARENT``,
        ``SIBLING_NEXT``, ``SIBLING_PREV``, ``SHADOW_HOST``,
        ``SLOT_ASSIGNMENT``.
        """
        return self.kind in DOMMorphismKind._STRUCTURAL

    def is_reference(self) -> bool:
        """Return ``True`` for edges that represent semantic cross-references.

        Reference kinds: ``ID_REFERENCE``, ``ARIA_REFERENCE``,
        ``LABEL_FOR``, ``HREF_TARGET``, ``FORM_OWNER``.
        """
        return self.kind in DOMMorphismKind._REFERENCE

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"DOMTreeMorphism("
            f"{self.source.path_string()!r} "
            f"--[{self.kind}]--> "
            f"{self.target.path_string()!r}"
            f"{'  ' + self.label if self.label else ''}"
            f")"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DOMTreeMorphism):
            return NotImplemented
        return (
            self.source == other.source
            and self.target == other.target
            and self.kind == other.kind
            and self.label == other.label
        )

    def __hash__(self) -> int:
        return hash((self.source, self.target, self.kind, self.label))
