"""CSS inheritance model.

Inheritance is a fibre-wise operation: for each inheritable property,
the value propagates down the DOM tree from ancestor to descendant
unless overridden by a more specific rule.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .models import CSSValue, CSSValueType, ComputedStyle, DOMNodeKind
from .dom_site import DOMSite


# ---------------------------------------------------------------------------
# Property classification
# ---------------------------------------------------------------------------

INHERITABLE: frozenset[str] = frozenset({
    "color", "font-size", "font-family", "font-weight", "font-style",
    "font-variant", "font-stretch", "line-height", "letter-spacing",
    "word-spacing", "text-align", "text-indent", "text-transform",
    "text-decoration", "visibility", "cursor", "list-style",
    "list-style-type", "list-style-position", "list-style-image",
    "quotes", "direction", "unicode-bidi", "white-space",
    "word-break", "overflow-wrap", "word-wrap", "tab-size",
    "border-collapse", "border-spacing", "caption-side",
    "empty-cells", "table-layout", "orphans", "widows", "page-break-inside",
    "speak", "speak-header", "speak-numeral", "speak-punctuation", "speech-rate",
    "pitch", "richness", "stress", "voice-family", "volume",
})

NON_INHERITABLE: frozenset[str] = frozenset({
    "display", "position", "top", "right", "bottom", "left",
    "width", "height", "max-width", "max-height", "min-width", "min-height",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-width", "border-style", "border-color",
    "background", "background-color", "background-image",
    "z-index", "overflow", "opacity", "transform", "transition",
    "flex-direction", "flex-wrap", "flex-grow", "flex-shrink",
    "grid-template", "grid-template-columns", "grid-template-rows",
    "float", "clear", "clip", "content", "counter-reset", "counter-increment",
})


# ---------------------------------------------------------------------------
# Inheritance model
# ---------------------------------------------------------------------------

class InheritanceModel:
    """Walk the DOM tree to resolve inherited CSS values."""

    INHERITABLE = INHERITABLE
    NON_INHERITABLE = NON_INHERITABLE

    def resolve_inheritance_chain(
        self,
        node_id: str,
        property_name: str,
        styles: dict[str, ComputedStyle],
        dom: DOMSite,
    ) -> CSSValue | None:
        """Walk up ancestors to find the inherited value for *property_name*."""
        ancestors = dom.ancestors_of(node_id)
        for anc in ancestors:
            style = styles.get(anc.node_id)
            if style is None:
                continue
            val = style.get(property_name)
            if val is not None:
                return val
        return None

    def detect_inheritance_gaps(
        self,
        dom: DOMSite,
        styles: dict[str, ComputedStyle],
    ) -> list[dict]:
        """Find nodes where an inheritable property is expected but absent.

        Returns ``[{"node_id": ..., "property": ..., "message": ...}, ...]``
        """
        gaps: list[dict] = []
        for nid, node in dom.nodes.items():
            if node.node_kind != DOMNodeKind.ELEMENT:
                continue
            style = styles.get(nid)
            if style is None:
                continue
            for prop in self.INHERITABLE:
                if prop in style.properties:
                    continue
                # Check if any ancestor has it
                val = self.resolve_inheritance_chain(nid, prop, styles, dom)
                if val is None:
                    gaps.append({
                        "node_id": nid,
                        "property": prop,
                        "message": (
                            f"Node '{nid}' has no value for inheritable property "
                            f"'{prop}' and no ancestor provides one"
                        ),
                    })
        return gaps

    def apply_inheritance(
        self,
        dom: DOMSite,
        styles: dict[str, ComputedStyle],
    ) -> None:
        """Fill inherited values for all nodes (BFS order, parents first).

        Modifies *styles* in place.
        """
        if not dom.root_id:
            return
        queue = deque([dom.root_id])
        visited: set[str] = set()
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            node = dom.nodes.get(nid)
            if node is None:
                continue
            style = styles.get(nid)
            if style is None:
                style = ComputedStyle(node_id=nid)
                styles[nid] = style
            if node.parent_id and node.parent_id in styles:
                parent_style = styles[node.parent_id]
                for prop in self.INHERITABLE:
                    if prop in style.properties:
                        val = style.properties[prop]
                        if val.raw == "inherit" and prop in parent_style.properties:
                            style.properties[prop] = parent_style.properties[prop]
                        continue
                    if prop in parent_style.properties:
                        style.properties[prop] = parent_style.properties[prop]
            for cid in node.children:
                queue.append(cid)

    def is_inheritable(self, property_name: str) -> bool:
        """Return True if *property_name* is an inheritable CSS property."""
        return property_name in self.INHERITABLE


# ---------------------------------------------------------------------------
# Initial value registry
# ---------------------------------------------------------------------------

class InitialValueRegistry:
    """Registry of CSS initial (default) values."""

    _values: dict[str, str] = {
        "display": "inline",
        "position": "static",
        "color": "black",
        "font-size": "16px",
        "font-family": "serif",
        "font-weight": "normal",
        "font-style": "normal",
        "line-height": "normal",
        "letter-spacing": "normal",
        "word-spacing": "normal",
        "text-align": "left",
        "text-decoration": "none",
        "text-transform": "none",
        "text-indent": "0",
        "vertical-align": "baseline",
        "white-space": "normal",
        "visibility": "visible",
        "cursor": "auto",
        "overflow": "visible",
        "opacity": "1",
        "z-index": "auto",
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
        "border": "none",
        "background": "none",
        "background-color": "transparent",
        "width": "auto",
        "height": "auto",
        "max-width": "none",
        "min-width": "0",
        "float": "none",
        "clear": "none",
        "flex-direction": "row",
        "flex-wrap": "nowrap",
        "flex-grow": "0",
    }

    def get_initial(self, property_name: str) -> CSSValue:
        """Get the initial value for *property_name*."""
        raw = self._values.get(property_name, "")
        return CSSValue(raw=raw)

    def get_all_initial_values(self) -> dict[str, CSSValue]:
        """Return all known initial values as ``CSSValue`` objects."""
        return {k: CSSValue(raw=v) for k, v in self._values.items()}
