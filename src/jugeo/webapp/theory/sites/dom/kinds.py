"""DOMNodeKind and DOMMorphismKind enums for the DOM site in the jugeo sheaf framework.

The DOM is modelled as a Grothendieck site whose open sets are subtrees of the
node tree.  ``DOMNodeKind`` labels the *objects* (nodes) in that site while
``DOMMorphismKind`` labels the *morphisms* (structural and semantic relationships
between nodes) that form the transition maps of the DOM sheaf.

In sheaf-theoretic terms:

* Each node kind determines which *stalk* type lives above that node in the DOM
  presheaf.  For example, ELEMENT nodes carry an attribute map stalk whereas
  TEXT nodes carry a string stalk.

* Each morphism kind corresponds to a *restriction map* along a covering sieve
  in the Grothendieck topology, or — for non-covering relationships such as
  ARIA_REFERENCE — a *global section* condition linking stalks over disjoint
  open sets.

The module is intentionally free of external dependencies so that it can be
imported in browser-side transpilation contexts as well as server-side analysis
pipelines.
"""
from __future__ import annotations

from enum import Enum
from typing import ClassVar

__all__ = [
    "DOMNodeKind",
    "DOMMorphismKind",
]

# ---------------------------------------------------------------------------
# DOMNodeKind
# ---------------------------------------------------------------------------


class DOMNodeKind(str, Enum):
    """Classification of every node type that can appear in a living DOM tree.

    This enum covers both the low-level ``Node.nodeType`` taxonomy exposed by
    the Web API and the higher-level *semantic* categories that matter for
    sheaf-theoretic analysis of HTML documents.

    Low-level structural kinds
    --------------------------
    DOCUMENT
        The root ``#document`` node.  Acts as the terminal object in the DOM
        category; every other node has a unique path to DOCUMENT.
    DOCTYPE
        The ``DocumentType`` node (``<!DOCTYPE html>``).  Carries metadata
        about the document's validation grammar.
    ELEMENT
        A generic ``Element`` node.  The most common kind; subsumes all named
        HTML and SVG elements not further specialised below.
    TEXT
        A ``Text`` node containing character data between element tags.  The
        stalk over a TEXT node in the content presheaf is a Unicode string.
    COMMENT
        An HTML comment node (``<!-- ... -->``).  Invisible to layout but
        present in the DOM tree.
    CDATA
        A ``CDATASection`` node; rare in HTML5 but present in SVG/MathML
        embedded content.
    FRAGMENT
        A ``DocumentFragment`` — a lightweight, off-document sub-tree used as
        a staging area before insertion into the live DOM.
    SHADOW_ROOT
        The root of a Shadow DOM tree attached to a *shadow host* element.
        Introduces a separate Grothendieck topology for encapsulated subtrees.
    SLOT
        A ``<slot>`` element inside Shadow DOM that acts as a *projection*
        morphism from the light-DOM children to the shadow-DOM layout.
    TEMPLATE
        A ``<template>`` element whose content lives in an inert
        ``DocumentFragment`` until cloned and instantiated.

    Semantic content-model kinds
    ----------------------------
    BLOCK
        Block-level flow content (``<div>``, ``<p>``, ``<section>``, …).
        Forms the *coarse* open sets in the layout topology.
    INLINE
        Inline-level phrasing content (``<span>``, ``<em>``, ``<strong>``, …).
        Nested within BLOCK elements; their stalks inherit box-model data.
    VOID
        Void elements with no children (``<br>``, ``<hr>``, ``<wbr>``).
        Terminal objects in the subtree rooted at their parent.
    RAW_TEXT
        Elements whose content is treated as raw text by the HTML parser
        (``<script>``, ``<style>``).  The child TEXT node stalk is opaque to
        DOM-level processing.
    HEADING
        Heading elements ``<h1>`` through ``<h6>``.  Participate in the
        document *outline* sheaf which computes section hierarchy.
    SECTIONING
        Sectioning content elements (``<article>``, ``<aside>``, ``<nav>``,
        ``<section>``).  Define the *open cover* of the outline sheaf.
    INTERACTIVE
        Elements that accept user input via pointer or keyboard
        (``<a>``, ``<button>``, ``<details>``, ``<summary>``).
    EMBEDDED
        Replaced / embedded content (``<img>``, ``<iframe>``, ``<object>``,
        ``<embed>``, ``<canvas>``, ``<picture>``).  Their stalk contains an
        external resource reference.
    METADATA
        Metadata elements in ``<head>`` (``<meta>``, ``<link>``, ``<base>``,
        ``<title>``).  Contribute to the *global section* of the document
        metadata sheaf.
    SCRIPTING
        Script-bearing elements (``<script>``, ``<noscript>``).  Their stalks
        encode executable or conditional-executable payloads.
    TABLE_RELATED
        Table structure elements (``<table>``, ``<thead>``, ``<tbody>``,
        ``<tfoot>``, ``<tr>``, ``<td>``, ``<th>``, ``<colgroup>``, ``<col>``,
        ``<caption>``).
    FORM_RELATED
        Form and form-control elements (``<form>``, ``<input>``, ``<select>``,
        ``<textarea>``, ``<button>``, ``<fieldset>``, ``<legend>``,
        ``<datalist>``, ``<output>``).  Participate in the constraint sheaf
        that validates user-supplied data.
    LIST_RELATED
        List container and item elements (``<ul>``, ``<ol>``, ``<li>``,
        ``<dl>``, ``<dt>``, ``<dd>``).
    MEDIA
        Audio/video media elements (``<video>``, ``<audio>``, ``<track>``,
        ``<source>``).  Their stalks reference time-indexed media resources.
    SVG_CONTAINER
        Top-level ``<svg>`` elements that introduce a separate coordinate
        system and rendering context within the HTML tree.
    CUSTOM_ELEMENT
        Autonomous or customised built-in custom elements registered via the
        Custom Elements API.  Their semantic category is opaque to the static
        analyser and must be resolved at runtime.
    """

    # -- Structural node types -----------------------------------------------
    DOCUMENT = "document"
    DOCTYPE = "doctype"
    ELEMENT = "element"
    TEXT = "text"
    COMMENT = "comment"
    CDATA = "cdata"
    FRAGMENT = "fragment"
    SHADOW_ROOT = "shadow_root"
    SLOT = "slot"
    TEMPLATE = "template"

    # -- Semantic content-model kinds ----------------------------------------
    BLOCK = "block"
    INLINE = "inline"
    VOID = "void"
    RAW_TEXT = "raw_text"
    HEADING = "heading"
    SECTIONING = "sectioning"
    INTERACTIVE = "interactive"
    EMBEDDED = "embedded"
    METADATA = "metadata"
    SCRIPTING = "scripting"
    TABLE_RELATED = "table_related"
    FORM_RELATED = "form_related"
    LIST_RELATED = "list_related"
    MEDIA = "media"
    SVG_CONTAINER = "svg_container"
    CUSTOM_ELEMENT = "custom_element"

    # -- Lookup table (populated after class body) ---------------------------
    _tag_map: ClassVar[dict[str, DOMNodeKind]]

    @classmethod
    def semantic_category(cls, tag_name: str) -> DOMNodeKind:
        """Return the semantic ``DOMNodeKind`` for a lower-cased HTML tag name.

        The mapping covers the most common HTML5 elements.  Tags not found in
        the built-in table fall back to ``ELEMENT`` (generic element), except
        for names that contain a hyphen which are classified as
        ``CUSTOM_ELEMENT`` per the HTML specification.

        Parameters
        ----------
        tag_name:
            The HTML tag name, case-insensitive (e.g. ``"DIV"``, ``"div"``).

        Returns
        -------
        DOMNodeKind
            The most specific semantic category for the tag.

        Examples
        --------
        >>> DOMNodeKind.semantic_category("div")
        <DOMNodeKind.BLOCK: 'block'>
        >>> DOMNodeKind.semantic_category("x-my-component")
        <DOMNodeKind.CUSTOM_ELEMENT: 'custom_element'>
        """
        tag = tag_name.lower()
        if "-" in tag:
            return cls.CUSTOM_ELEMENT
        return _TAG_TO_KIND.get(tag, cls.ELEMENT)

    def is_structural(self) -> bool:
        """Return ``True`` if this kind describes a structural (non-semantic) node."""
        return self in {
            DOMNodeKind.DOCUMENT,
            DOMNodeKind.DOCTYPE,
            DOMNodeKind.ELEMENT,
            DOMNodeKind.TEXT,
            DOMNodeKind.COMMENT,
            DOMNodeKind.CDATA,
            DOMNodeKind.FRAGMENT,
            DOMNodeKind.SHADOW_ROOT,
            DOMNodeKind.SLOT,
            DOMNodeKind.TEMPLATE,
        }

    def is_semantic(self) -> bool:
        """Return ``True`` if this kind describes a semantic content-model category."""
        return not self.is_structural()

    def carries_text_stalk(self) -> bool:
        """Return ``True`` if nodes of this kind may carry a direct text stalk."""
        return self in {DOMNodeKind.TEXT, DOMNodeKind.COMMENT, DOMNodeKind.CDATA}


# ---------------------------------------------------------------------------
# Tag → DOMNodeKind mapping (module-level; referenced by semantic_category)
# ---------------------------------------------------------------------------

_TAG_TO_KIND: dict[str, DOMNodeKind] = {
    # Block elements
    "address": DOMNodeKind.BLOCK,
    "article": DOMNodeKind.SECTIONING,
    "aside": DOMNodeKind.SECTIONING,
    "blockquote": DOMNodeKind.BLOCK,
    "details": DOMNodeKind.INTERACTIVE,
    "dialog": DOMNodeKind.INTERACTIVE,
    "div": DOMNodeKind.BLOCK,
    "fieldset": DOMNodeKind.FORM_RELATED,
    "figcaption": DOMNodeKind.BLOCK,
    "figure": DOMNodeKind.BLOCK,
    "footer": DOMNodeKind.SECTIONING,
    "form": DOMNodeKind.FORM_RELATED,
    "header": DOMNodeKind.SECTIONING,
    "hgroup": DOMNodeKind.BLOCK,
    "main": DOMNodeKind.SECTIONING,
    "nav": DOMNodeKind.SECTIONING,
    "p": DOMNodeKind.BLOCK,
    "pre": DOMNodeKind.BLOCK,
    "section": DOMNodeKind.SECTIONING,
    "summary": DOMNodeKind.INTERACTIVE,
    # Headings
    "h1": DOMNodeKind.HEADING,
    "h2": DOMNodeKind.HEADING,
    "h3": DOMNodeKind.HEADING,
    "h4": DOMNodeKind.HEADING,
    "h5": DOMNodeKind.HEADING,
    "h6": DOMNodeKind.HEADING,
    # Inline / phrasing
    "abbr": DOMNodeKind.INLINE,
    "b": DOMNodeKind.INLINE,
    "bdi": DOMNodeKind.INLINE,
    "bdo": DOMNodeKind.INLINE,
    "cite": DOMNodeKind.INLINE,
    "code": DOMNodeKind.INLINE,
    "data": DOMNodeKind.INLINE,
    "dfn": DOMNodeKind.INLINE,
    "em": DOMNodeKind.INLINE,
    "i": DOMNodeKind.INLINE,
    "kbd": DOMNodeKind.INLINE,
    "mark": DOMNodeKind.INLINE,
    "q": DOMNodeKind.INLINE,
    "rp": DOMNodeKind.INLINE,
    "rt": DOMNodeKind.INLINE,
    "ruby": DOMNodeKind.INLINE,
    "s": DOMNodeKind.INLINE,
    "samp": DOMNodeKind.INLINE,
    "small": DOMNodeKind.INLINE,
    "span": DOMNodeKind.INLINE,
    "strong": DOMNodeKind.INLINE,
    "sub": DOMNodeKind.INLINE,
    "sup": DOMNodeKind.INLINE,
    "time": DOMNodeKind.INLINE,
    "u": DOMNodeKind.INLINE,
    "var": DOMNodeKind.INLINE,
    # Interactive / anchor
    "a": DOMNodeKind.INTERACTIVE,
    "button": DOMNodeKind.INTERACTIVE,
    # Void elements
    "area": DOMNodeKind.VOID,
    "br": DOMNodeKind.VOID,
    "col": DOMNodeKind.TABLE_RELATED,
    "embed": DOMNodeKind.EMBEDDED,
    "hr": DOMNodeKind.VOID,
    "img": DOMNodeKind.EMBEDDED,
    "input": DOMNodeKind.FORM_RELATED,
    "link": DOMNodeKind.METADATA,
    "meta": DOMNodeKind.METADATA,
    "param": DOMNodeKind.VOID,
    "source": DOMNodeKind.MEDIA,
    "track": DOMNodeKind.MEDIA,
    "wbr": DOMNodeKind.VOID,
    # Embedded / replaced
    "canvas": DOMNodeKind.EMBEDDED,
    "iframe": DOMNodeKind.EMBEDDED,
    "math": DOMNodeKind.EMBEDDED,
    "object": DOMNodeKind.EMBEDDED,
    "picture": DOMNodeKind.EMBEDDED,
    # Media
    "audio": DOMNodeKind.MEDIA,
    "video": DOMNodeKind.MEDIA,
    # Scripting
    "noscript": DOMNodeKind.SCRIPTING,
    "script": DOMNodeKind.SCRIPTING,
    # Metadata / head elements
    "base": DOMNodeKind.METADATA,
    "head": DOMNodeKind.METADATA,
    "style": DOMNodeKind.METADATA,
    "title": DOMNodeKind.METADATA,
    # Raw-text (parser-level)
    # 'script' and 'style' are already above; listed here for clarity
    # Table-related
    "caption": DOMNodeKind.TABLE_RELATED,
    "colgroup": DOMNodeKind.TABLE_RELATED,
    "table": DOMNodeKind.TABLE_RELATED,
    "tbody": DOMNodeKind.TABLE_RELATED,
    "td": DOMNodeKind.TABLE_RELATED,
    "tfoot": DOMNodeKind.TABLE_RELATED,
    "th": DOMNodeKind.TABLE_RELATED,
    "thead": DOMNodeKind.TABLE_RELATED,
    "tr": DOMNodeKind.TABLE_RELATED,
    # Form-related
    "datalist": DOMNodeKind.FORM_RELATED,
    "label": DOMNodeKind.FORM_RELATED,
    "legend": DOMNodeKind.FORM_RELATED,
    "meter": DOMNodeKind.FORM_RELATED,
    "optgroup": DOMNodeKind.FORM_RELATED,
    "option": DOMNodeKind.FORM_RELATED,
    "output": DOMNodeKind.FORM_RELATED,
    "progress": DOMNodeKind.FORM_RELATED,
    "select": DOMNodeKind.FORM_RELATED,
    "textarea": DOMNodeKind.FORM_RELATED,
    # List-related
    "dd": DOMNodeKind.LIST_RELATED,
    "dl": DOMNodeKind.LIST_RELATED,
    "dt": DOMNodeKind.LIST_RELATED,
    "li": DOMNodeKind.LIST_RELATED,
    "menu": DOMNodeKind.LIST_RELATED,
    "ol": DOMNodeKind.LIST_RELATED,
    "ul": DOMNodeKind.LIST_RELATED,
    # SVG container
    "svg": DOMNodeKind.SVG_CONTAINER,
    # Template / shadow
    "slot": DOMNodeKind.SLOT,
    "template": DOMNodeKind.TEMPLATE,
}


# ---------------------------------------------------------------------------
# DOMMorphismKind
# ---------------------------------------------------------------------------


class DOMMorphismKind(str, Enum):
    """Classification of structural and semantic relationships between DOM nodes.

    Each member represents a *kind* of morphism in the DOM category.  In
    sheaf-theoretic terms a morphism determines a *restriction map* — a
    function from the stalk at the source node to the stalk at the target node
    that must satisfy the functoriality and gluing axioms of the DOM presheaf.

    Tree-structural morphisms
    -------------------------
    PARENT_CHILD
        The canonical covering morphism in the Grothendieck topology of the
        DOM site.  A sieve generated by ``{parent → child}`` arrows forms an
        open cover of the parent's subtree.  The corresponding restriction map
        projects a section defined on the parent's open set down to the child's
        open set.

    CHILD_PARENT
        The reverse of PARENT_CHILD: the inclusion of a child's open set into
        its parent's.  This is the *coface map* in the Čech complex of the
        tree.  In practice this corresponds to reading ``node.parentElement``.

    NEXT_SIBLING
        A morphism between adjacent siblings in document order.  Two siblings
        share the same parent open set; this morphism represents the
        *coboundary* between consecutive open sets in the sibling cover.

    PREV_SIBLING
        The reverse sibling morphism (``node.previousSibling``).  Together
        with NEXT_SIBLING it generates the sibling *groupoid* over each
        parent node.

    DESCENDANT
        A transitive closure of PARENT_CHILD arrows.  Represents membership
        in a subtree open set; the corresponding restriction map is the
        composite of all intermediate PARENT_CHILD restrictions along the
        unique path in the tree.

    ANCESTOR
        The dual of DESCENDANT: the unique path from a node up to any of its
        ancestors.  Corresponds to inclusion of a deeper open set into a
        shallower one.

    Shadow DOM morphisms
    --------------------
    SHADOW_HOST_TO_ROOT
        The morphism from a shadow host element to its attached ShadowRoot.
        In sheaf terms this creates a *new topology* on the shadow tree; the
        restriction map must respect the encapsulation boundary so that
        sections inside the shadow do not leak to the light DOM.

    SLOT_ASSIGNMENT
        The projection morphism from a ``<slot>`` element inside a shadow tree
        to the *slotted* light-DOM nodes assigned to it.  This is the key
        *gluing morphism* that reconciles the light-DOM presheaf with the
        shadow-DOM presheaf; a global section of the composed sheaf must agree
        on the overlap defined by the slot boundary.

    Document-level morphisms
    ------------------------
    DOCUMENT_TO_ROOT
        The morphism from the ``#document`` node to the ``<html>`` root
        element.  Acts as the *terminal map* in the DOM category; every other
        morphism factors through DOCUMENT_TO_ROOT up to the root.

    Attribute and content morphisms
    --------------------------------
    ATTRIBUTE_ACCESS
        A morphism from an ELEMENT node to one of its ``Attr`` objects.  The
        corresponding restriction map projects the element's attribute-map
        stalk to the stalk of a single named attribute.  In CSS-selector
        terms this morphism underlies the ``[attr=value]`` covering condition.

    TEXT_CONTENT_ACCESS
        A morphism from an ELEMENT or DOCUMENT_FRAGMENT node to its
        concatenated text content.  Corresponds to reading ``node.textContent``
        and represents the *global section* of the string sheaf over that
        subtree.

    Cross-reference morphisms
    --------------------------
    ID_REFERENCE
        A non-tree morphism from any node that holds an ``href="#id"`` or
        ``for="id"`` reference to the target node with that ``id``.  Because
        this morphism does not respect the tree topology it represents a
        *non-local section condition*: the sheaf must carry consistent data
        across the two disjoint open sets linked by the id reference.

    ARIA_REFERENCE
        A morphism induced by an ARIA relationship attribute
        (``aria-labelledby``, ``aria-describedby``, ``aria-controls``,
        ``aria-owns``, ``aria-flowto``).  Like ID_REFERENCE this is a
        non-local morphism; it appears in the accessibility sheaf where the
        stalk at each node includes the node's accessible name and description.

    FORM_ASSOCIATION
        The morphism from a form control element to its associated ``<form>``
        element, established either by DOM ancestry or by the ``form``
        attribute containing the form's ``id``.  In the constraint sheaf the
        restriction map along FORM_ASSOCIATION propagates validation rules from
        the form to its controls.

    LABEL_FOR
        The morphism from a ``<label>`` element to its labelled form control,
        established by the ``for`` attribute or by DOM containment.  In the
        accessibility sheaf the restriction map along LABEL_FOR carries the
        label's text stalk to the control's accessible-name stalk.
    """

    # -- Tree-structural morphisms -------------------------------------------
    PARENT_CHILD = "parent_child"
    CHILD_PARENT = "child_parent"
    NEXT_SIBLING = "next_sibling"
    PREV_SIBLING = "prev_sibling"
    DESCENDANT = "descendant"
    ANCESTOR = "ancestor"

    # -- Shadow DOM morphisms ------------------------------------------------
    SHADOW_HOST_TO_ROOT = "shadow_host_to_root"
    SLOT_ASSIGNMENT = "slot_assignment"

    # -- Document-level morphisms --------------------------------------------
    DOCUMENT_TO_ROOT = "document_to_root"

    # -- Attribute and content morphisms -------------------------------------
    ATTRIBUTE_ACCESS = "attribute_access"
    TEXT_CONTENT_ACCESS = "text_content_access"

    # -- Cross-reference morphisms -------------------------------------------
    ID_REFERENCE = "id_reference"
    ARIA_REFERENCE = "aria_reference"
    FORM_ASSOCIATION = "form_association"
    LABEL_FOR = "label_for"

    def is_tree_structural(self) -> bool:
        """Return ``True`` if this morphism follows the tree topology."""
        return self in {
            DOMMorphismKind.PARENT_CHILD,
            DOMMorphismKind.CHILD_PARENT,
            DOMMorphismKind.NEXT_SIBLING,
            DOMMorphismKind.PREV_SIBLING,
            DOMMorphismKind.DESCENDANT,
            DOMMorphismKind.ANCESTOR,
            DOMMorphismKind.DOCUMENT_TO_ROOT,
        }

    def is_cross_reference(self) -> bool:
        """Return ``True`` if this morphism crosses the tree topology boundary.

        Cross-reference morphisms link nodes that are not in an ancestor/
        descendant relationship; they require a *non-local gluing condition*
        in the DOM sheaf.
        """
        return self in {
            DOMMorphismKind.ID_REFERENCE,
            DOMMorphismKind.ARIA_REFERENCE,
            DOMMorphismKind.FORM_ASSOCIATION,
            DOMMorphismKind.LABEL_FOR,
        }

    def is_shadow_dom(self) -> bool:
        """Return ``True`` if this morphism crosses a Shadow DOM boundary."""
        return self in {
            DOMMorphismKind.SHADOW_HOST_TO_ROOT,
            DOMMorphismKind.SLOT_ASSIGNMENT,
        }

    def dual(self) -> DOMMorphismKind:
        """Return the categorical dual (opposite arrow) of this morphism kind.

        For morphisms with a well-defined dual in the DOM category the dual is
        the reverse morphism.  Morphisms that have no natural dual return
        *themselves* as a fixed point.
        """
        _DUALS: dict[DOMMorphismKind, DOMMorphismKind] = {
            DOMMorphismKind.PARENT_CHILD: DOMMorphismKind.CHILD_PARENT,
            DOMMorphismKind.CHILD_PARENT: DOMMorphismKind.PARENT_CHILD,
            DOMMorphismKind.NEXT_SIBLING: DOMMorphismKind.PREV_SIBLING,
            DOMMorphismKind.PREV_SIBLING: DOMMorphismKind.NEXT_SIBLING,
            DOMMorphismKind.DESCENDANT: DOMMorphismKind.ANCESTOR,
            DOMMorphismKind.ANCESTOR: DOMMorphismKind.DESCENDANT,
        }
        return _DUALS.get(self, self)
