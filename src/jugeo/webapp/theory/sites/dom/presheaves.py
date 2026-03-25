"""DOM presheaves: model DOM content, attributes, validity, and accessibility
as presheaves over the DOM site.

A presheaf assigns data to each node, with restriction maps from parent to
child.  Each class here models one "fibre" of data and provides the
corresponding restriction maps as required by the site topology.
"""

from __future__ import annotations

import math

from jugeo.geometry.site import Coordinate, Morphism
from jugeo.geometry.descent import LocalSection, DescentEngine, DescentResult

__all__ = [
    "DOMContentPresheaf",
    "DOMAttributePresheaf",
    "DOMValidityPresheaf",
    "AccessibilityPresheaf",
]

# ---------------------------------------------------------------------------
# 1. DOMContentPresheaf
# ---------------------------------------------------------------------------

class DOMContentPresheaf:
    """Assigns textual/structural content to DOM nodes.

    The restriction map is the identity on subtrees: the text content of a
    parent is the concatenation of its children's text (as in the DOM
    ``textContent`` property).
    """

    def __init__(self) -> None:
        self._sections: dict[str, str] = {}
        self._html_sections: dict[str, str] = {}

    # -- mutation ------------------------------------------------------------

    def set_text(self, coord_name: str, text: str) -> None:
        """Set the text content for *coord_name*."""
        self._sections[coord_name] = text

    def set_html(self, coord_name: str, html: str) -> None:
        """Set the innerHTML for *coord_name*."""
        self._html_sections[coord_name] = html

    # -- query ---------------------------------------------------------------

    def get_text(self, coord_name: str) -> str | None:
        """Return the stored text content for *coord_name*, or None."""
        return self._sections.get(coord_name)

    def text_content(self, coord_name: str, children: list[str]) -> str:
        """Return the text content of the subtree rooted at *coord_name*.

        Implements the presheaf restriction: parent text equals the
        concatenation of children's text (depth-first, left-to-right).
        If the node has no stored text and no children, returns ``""``.
        """
        child_texts = [self._sections.get(c, "") for c in children]
        if child_texts:
            return "".join(child_texts)
        return self._sections.get(coord_name, "")

    def to_local_section(self, coord_name: str) -> LocalSection | None:
        """Lift the content at *coord_name* into a :class:`LocalSection`.

        Returns ``None`` when no content has been recorded for the node.
        """
        text = self._sections.get(coord_name)
        html = self._html_sections.get(coord_name)
        if text is None and html is None:
            return None
        judgment_data: dict[str, object] = {}
        evidence: list[str] = []
        if text is not None:
            judgment_data["text_content"] = text
            evidence.append(f"text:{coord_name}")
        if html is not None:
            judgment_data["html_content"] = html
            evidence.append(f"html:{coord_name}")
        return LocalSection(
            coordinate=coord_name,
            judgment_data=judgment_data,
            evidence_bundle=tuple(evidence),
            trust_level=1.0,
            provenance=("DOMContentPresheaf.to_local_section",),
            is_partial=False,
            residual_obligations=[],
        )

    def restriction_map(self, parent: str, child: str) -> str | None:
        """Return the portion of *child*'s content that is "part of" *parent*.

        In the DOM content presheaf the restriction map is the identity on
        the child's own text: the child contributes exactly its own stored
        text to the parent's aggregate content.  Returns ``None`` when the
        child has no recorded content.
        """
        return self._sections.get(child)


# ---------------------------------------------------------------------------
# 2. DOMAttributePresheaf
# ---------------------------------------------------------------------------

_INHERITABLE_ATTRS: frozenset[str] = frozenset({"lang", "dir", "hidden"})


class DOMAttributePresheaf:
    """Attributes as a presheaf with CSS/HTML inheritance.

    The restriction map propagates inheritable attributes (``lang``,
    ``dir``, ``hidden``) from parent to child when the child has no
    locally-set value.
    """

    ARIA_ATTRS: frozenset[str] = frozenset({
        "aria-activedescendant",
        "aria-atomic",
        "aria-autocomplete",
        "aria-busy",
        "aria-checked",
        "aria-colcount",
        "aria-colindex",
        "aria-colspan",
        "aria-controls",
        "aria-current",
        "aria-describedby",
        "aria-details",
        "aria-disabled",
        "aria-dropeffect",
        "aria-errormessage",
        "aria-expanded",
        "aria-flowto",
        "aria-grabbed",
        "aria-haspopup",
        "aria-hidden",
        "aria-invalid",
        "aria-keyshortcuts",
        "aria-label",
        "aria-labelledby",
        "aria-level",
        "role",
    })

    def __init__(self) -> None:
        self._attributes: dict[str, dict[str, str]] = {}

    # -- mutation ------------------------------------------------------------

    def set_attr(self, coord_name: str, name: str, value: str) -> None:
        """Set attribute *name* to *value* on *coord_name*."""
        self._attributes.setdefault(coord_name, {})[name] = value

    # -- query ---------------------------------------------------------------

    def get_attr(self, coord_name: str, name: str) -> str | None:
        """Return the locally-set value of *name* on *coord_name*, or None."""
        return self._attributes.get(coord_name, {}).get(name)

    def inherited_value(
        self,
        coord_name: str,
        attr_name: str,
        ancestors: list[str],
    ) -> str | None:
        """Walk *ancestors* (nearest first) for an inheritable attribute.

        Only ``lang``, ``dir``, and ``hidden`` are inheritable in HTML.
        Returns the first value found among *coord_name* itself and then
        each ancestor in order, or ``None`` if none define the attribute.
        """
        if attr_name not in _INHERITABLE_ATTRS:
            return self.get_attr(coord_name, attr_name)
        local = self.get_attr(coord_name, attr_name)
        if local is not None:
            return local
        for ancestor in ancestors:
            value = self.get_attr(ancestor, attr_name)
            if value is not None:
                return value
        return None

    def aria_attrs(self, coord_name: str) -> dict[str, str]:
        """Return all aria-* and role attributes set on *coord_name*."""
        attrs = self._attributes.get(coord_name, {})
        return {
            k: v
            for k, v in attrs.items()
            if k.startswith("aria-") or k == "role"
        }

    def data_attrs(self, coord_name: str) -> dict[str, str]:
        """Return all data-* attributes set on *coord_name*."""
        attrs = self._attributes.get(coord_name, {})
        return {k: v for k, v in attrs.items() if k.startswith("data-")}

    def to_local_section(self, coord_name: str) -> LocalSection:
        """Lift the attribute map at *coord_name* into a :class:`LocalSection`."""
        attrs = dict(self._attributes.get(coord_name, {}))
        evidence = tuple(f"attr:{k}={v}" for k, v in sorted(attrs.items()))
        return LocalSection(
            coordinate=coord_name,
            judgment_data={"attributes": attrs},
            evidence_bundle=evidence,
            trust_level=1.0,
            provenance=("DOMAttributePresheaf.to_local_section",),
            is_partial=False,
            residual_obligations=[],
        )


# ---------------------------------------------------------------------------
# 3. DOMValidityPresheaf
# ---------------------------------------------------------------------------

class DOMValidityPresheaf:
    """Structural HTML validity rules modelled as a descent condition.

    Each validity rule is a predicate on the fibre of the presheaf at a
    node.  The global section (valid document) exists iff every local
    section (every node) satisfies the rule.
    """

    VOID_ELEMENTS: frozenset[str] = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
        "command", "keygen", "menuitem",
    })

    # Simplified content model: maps a tag to the categories it *accepts*.
    CONTENT_MODEL: dict[str, list[str]] = {
        # sectioning roots / flow containers
        "body": ["flow"],
        "div": ["flow"],
        "section": ["flow", "sectioning"],
        "article": ["flow", "sectioning"],
        "aside": ["flow", "sectioning"],
        "nav": ["flow", "sectioning"],
        "main": ["flow"],
        "header": ["flow"],
        "footer": ["flow"],
        "figure": ["flow"],
        "figcaption": ["flow"],
        "details": ["flow"],
        "summary": ["phrasing"],
        # heading / phrasing containers
        "h1": ["phrasing"],
        "h2": ["phrasing"],
        "h3": ["phrasing"],
        "h4": ["phrasing"],
        "h5": ["phrasing"],
        "h6": ["phrasing"],
        "p": ["phrasing"],
        "span": ["phrasing"],
        "a": ["transparent", "phrasing"],
        "em": ["phrasing"],
        "strong": ["phrasing"],
        "label": ["phrasing"],
        # list containers
        "ul": ["list-item"],
        "ol": ["list-item"],
        "dl": ["dt-dd"],
        # table
        "table": ["table-section"],
        "thead": ["table-row"],
        "tbody": ["table-row"],
        "tfoot": ["table-row"],
        "tr": ["table-cell"],
        "td": ["flow"],
        "th": ["flow"],
        # form
        "form": ["flow"],
        "fieldset": ["flow"],
        "legend": ["phrasing"],
        # media
        "picture": ["source", "img"],
        "video": ["source", "track", "flow"],
        "audio": ["source", "track", "flow"],
    }

    # Simplified category membership for tags.
    _TAG_CATEGORY: dict[str, str] = {
        "div": "flow", "section": "flow", "article": "flow", "aside": "flow",
        "nav": "flow", "main": "flow", "header": "flow", "footer": "flow",
        "h1": "heading", "h2": "heading", "h3": "heading",
        "h4": "heading", "h5": "heading", "h6": "heading",
        "p": "flow", "blockquote": "flow", "pre": "flow", "hr": "flow",
        "ul": "flow", "ol": "flow", "li": "list-item",
        "dl": "flow", "dt": "dt-dd", "dd": "dt-dd",
        "table": "flow", "thead": "table-section", "tbody": "table-section",
        "tfoot": "table-section", "tr": "table-row",
        "td": "table-cell", "th": "table-cell",
        "span": "phrasing", "a": "phrasing", "em": "phrasing",
        "strong": "phrasing", "code": "phrasing", "abbr": "phrasing",
        "b": "phrasing", "i": "phrasing", "s": "phrasing", "u": "phrasing",
        "small": "phrasing", "mark": "phrasing", "sub": "phrasing",
        "sup": "phrasing", "time": "phrasing", "cite": "phrasing",
        "q": "phrasing", "dfn": "phrasing", "kbd": "phrasing",
        "samp": "phrasing", "var": "phrasing",
        "img": "phrasing", "input": "phrasing", "button": "phrasing",
        "select": "phrasing", "textarea": "phrasing", "label": "phrasing",
        "form": "flow", "fieldset": "flow", "legend": "phrasing",
        "figure": "flow", "figcaption": "flow",
        "source": "source", "track": "track",
        "video": "flow", "audio": "flow", "picture": "flow",
        "details": "flow", "summary": "phrasing",
        "script": "phrasing", "noscript": "phrasing",
    }

    # Required attributes per tag: {tag: [(attr, alternatives), ...]}
    # An entry (attr, alts) means: attr OR any of alts must be present.
    _REQUIRED: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        "img": [("src", ()), ("alt", ())],
        "a": [("href", ("role",))],
        "input": [("type", ())],
        "td": [],
        "th": [],
        "iframe": [("src", ("srcdoc",))],
        "area": [("alt", ())],
        "track": [("src", ())],
        "script": [],
        "link": [("href", ())],
        "meta": [],
    }

    # -- validity checks -----------------------------------------------------

    def check_void_children(self, tag: str, has_children: bool) -> bool:
        """Return False if a void element has children; True otherwise."""
        if tag.lower() in self.VOID_ELEMENTS and has_children:
            return False
        return True

    def check_nesting(self, parent_tag: str, child_tag: str) -> bool:
        """Simplified content-model check for *child_tag* inside *parent_tag*.

        Returns True when *child_tag* is allowed as a direct child of
        *parent_tag* according to the simplified content model.  Unknown
        tags default to permissive (True).
        """
        p = parent_tag.lower()
        c = child_tag.lower()
        if p in self.VOID_ELEMENTS:
            return False
        allowed = self.CONTENT_MODEL.get(p)
        if allowed is None:
            return True
        child_cat = self._TAG_CATEGORY.get(c, "flow")
        return child_cat in allowed or "transparent" in allowed

    def check_required_attrs(
        self, tag: str, attrs: dict[str, str]
    ) -> list[str]:
        """Return names of missing required attributes for *tag*.

        For each required attribute, if neither the primary attribute nor
        any of its alternatives is present, the primary name is reported as
        missing.
        """
        t = tag.lower()
        rules = self._REQUIRED.get(t, [])
        missing: list[str] = []
        for primary, alternatives in rules:
            if primary in attrs:
                continue
            if any(alt in attrs for alt in alternatives):
                continue
            missing.append(primary)
        return missing

    def validate_subtree(
        self, nodes: list[tuple[str, str, dict[str, str]]]
    ) -> list[tuple[str, str]]:
        """Validate a list of ``(coord_name, tag, attrs)`` triples.

        Returns a list of ``(coord_name, error_message)`` pairs for each
        validation failure found.  An empty list means the subtree is valid.
        """
        errors: list[tuple[str, str]] = []
        for coord_name, tag, attrs in nodes:
            t = tag.lower()
            # void-element children check: attrs may carry a "has_children" hint
            has_children = bool(attrs.get("_has_children", ""))
            if not self.check_void_children(t, has_children):
                errors.append((coord_name, f"<{t}> is void and must not have children"))
            # required attributes
            for missing_attr in self.check_required_attrs(t, attrs):
                errors.append((coord_name, f"<{t}> is missing required attribute '{missing_attr}'"))
        return errors


# ---------------------------------------------------------------------------
# 4. AccessibilityPresheaf
# ---------------------------------------------------------------------------

_IMPLICIT_ROLES: dict[str, str] = {
    "a": "link",
    "article": "article",
    "aside": "complementary",
    "button": "button",
    "datalist": "listbox",
    "details": "group",
    "dialog": "dialog",
    "figure": "figure",
    "footer": "contentinfo",
    "form": "form",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "header": "banner",
    "hr": "separator",
    "img": "img",
    "input": "textbox",
    "li": "listitem",
    "link": "link",
    "main": "main",
    "menu": "list",
    "nav": "navigation",
    "ol": "list",
    "option": "option",
    "output": "status",
    "progress": "progressbar",
    "section": "region",
    "select": "combobox",
    "summary": "button",
    "table": "table",
    "tbody": "rowgroup",
    "td": "cell",
    "textarea": "textbox",
    "tfoot": "rowgroup",
    "th": "columnheader",
    "thead": "rowgroup",
    "tr": "row",
    "ul": "list",
}

# Tags that are in the default tab order when no tabindex is set.
_DEFAULT_FOCUSABLE_TAGS: frozenset[str] = frozenset(
    {"a", "button", "input", "select", "textarea", "details"}
)


class AccessibilityPresheaf:
    """Accessibility properties as a presheaf over the DOM site.

    Each method computes a specific accessibility-tree property that forms
    a local section of the accessibility presheaf.  The restriction maps
    are implicit: child accessible names feed into parent computations.
    """

    # -- accessible name -----------------------------------------------------

    @staticmethod
    def compute_accessible_name(
        tag: str,
        attrs: dict[str, str],
        text_content: str,
    ) -> str:
        """Compute the accessible name using the simplified ARIA algorithm.

        Priority (highest first):
        1. ``aria-labelledby`` (value treated as the referenced label text)
        2. ``aria-label``
        3. ``alt`` (for ``<img>``)
        4. ``title``
        5. ``text_content`` (the element's visible text)

        In a real browser the ``aria-labelledby`` value is an IDREF and
        requires a DOM lookup; here we store the referenced text directly
        in the attribute value for testability.
        """
        if "aria-labelledby" in attrs:
            labelledby = attrs["aria-labelledby"].strip()
            if labelledby:
                return labelledby
        if "aria-label" in attrs:
            label = attrs["aria-label"].strip()
            if label:
                return label
        if tag.lower() == "img" and "alt" in attrs:
            return attrs["alt"]
        if "title" in attrs:
            title = attrs["title"].strip()
            if title:
                return title
        return text_content

    # -- ARIA role -----------------------------------------------------------

    @staticmethod
    def compute_role(tag: str, attrs: dict[str, str]) -> str:
        """Return the ARIA role for an element.

        An explicit ``role`` attribute takes precedence.  Otherwise the
        implicit role from the tag name is returned.  Falls back to
        ``"generic"`` for unknown tags.
        """
        explicit = attrs.get("role", "").strip()
        if explicit:
            return explicit
        return _IMPLICIT_ROLES.get(tag.lower(), "generic")

    # -- contrast ratio ------------------------------------------------------

    @staticmethod
    def check_contrast(
        foreground_lab: tuple[float, float, float],
        background_lab: tuple[float, float, float],
        is_large_text: bool,
    ) -> tuple[bool, float]:
        """Compute the WCAG 2.x contrast ratio from CIE L*a*b* lightness.

        Only the L* (lightness) component is used to derive relative
        luminance via the approximation L* ≈ 116·Y^(1/3) − 16 where Y is
        the relative luminance.  This gives Y ≈ ((L* + 16) / 116)^3.

        Returns ``(passes_AA, contrast_ratio)`` where:
        - AA normal text requires contrast ≥ 4.5 : 1
        - AA large text  requires contrast ≥ 3.0 : 1
        """
        def _luminance(lab: tuple[float, float, float]) -> float:
            L = lab[0]
            y = ((L + 16.0) / 116.0) ** 3
            return max(0.0, min(1.0, y))

        lum_fg = _luminance(foreground_lab)
        lum_bg = _luminance(background_lab)
        lighter = max(lum_fg, lum_bg)
        darker = min(lum_fg, lum_bg)
        ratio = (lighter + 0.05) / (darker + 0.05)
        threshold = 3.0 if is_large_text else 4.5
        return (ratio >= threshold, round(ratio, 4))

    # -- keyboard focus ------------------------------------------------------

    @staticmethod
    def keyboard_focusable(tag: str, attrs: dict[str, str]) -> bool:
        """Return True if this element is in the default tab order.

        An element is keyboard-focusable when:
        - it is a natively focusable tag (``a[href]``, ``button``,
          ``input``, ``select``, ``textarea``), **or**
        - it has a non-negative ``tabindex`` attribute.

        Elements with ``tabindex="-1"`` are programmatically focusable but
        are excluded from sequential keyboard navigation and therefore
        return False.
        """
        t = tag.lower()
        tabindex = attrs.get("tabindex")
        if tabindex is not None:
            try:
                idx = int(tabindex)
            except ValueError:
                idx = 0
            return idx >= 0
        if t == "a":
            return "href" in attrs
        if t in _DEFAULT_FOCUSABLE_TAGS:
            # disabled elements are not focusable
            if "disabled" in attrs:
                return False
            return True
        return False
