"""CSS cascade engine — descent procedure for gluing local sections.

The CSS cascade takes local sections (CSS rules over selector open-sets)
and glues them into a global section (computed style) per node.  Conflicts
are resolved by specificity ordering; remaining ambiguities are obstructions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque

from .models import (
    CSSRule,
    CSSValue,
    CSSValueType,
    CascadeObstruction,
    CascadeObstructionKind,
    ComputedStyle,
    DOMNode,
    DOMNodeKind,
    ObstructionSeverity,
    Specificity,
)
from .dom_site import DOMSite, SelectorParser
from .specificity import compute_specificity, specificity_sort


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INHERITABLE_PROPERTIES: frozenset[str] = frozenset({
    "color", "font-size", "font-family", "font-weight", "font-style",
    "line-height", "letter-spacing", "word-spacing", "text-align",
    "text-indent", "text-transform", "visibility", "cursor",
    "list-style", "list-style-type", "quotes", "direction",
    "white-space", "word-break", "overflow-wrap",
})

INITIAL_VALUES: dict[str, str] = {
    "display": "inline",
    "position": "static",
    "color": "black",
    "font-size": "16px",
    "font-family": "serif",
    "font-weight": "normal",
    "font-style": "normal",
    "line-height": "normal",
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
    "width": "auto",
    "height": "auto",
    "z-index": "auto",
    "overflow": "visible",
    "opacity": "1",
    "visibility": "visible",
    "background-color": "transparent",
    "border": "none",
    "flex-direction": "row",
    "text-align": "left",
}


# ---------------------------------------------------------------------------
# Cascade engine
# ---------------------------------------------------------------------------

class CSSCascadeEngine:
    """Resolve the CSS cascade for a whole DOM.

    For every node the engine collects applicable rules, sorts by
    specificity + source-order, and builds a ``ComputedStyle``.
    Inheritance and initial values are applied afterwards.
    """

    def resolve(
        self, dom: DOMSite, rules: list[CSSRule]
    ) -> dict[str, ComputedStyle]:
        """Resolve cascade for all nodes.  Returns ``node_id -> ComputedStyle``."""
        styles: dict[str, ComputedStyle] = {}

        for nid, node in dom.nodes.items():
            if node.node_kind != DOMNodeKind.ELEMENT:
                styles[nid] = ComputedStyle(node_id=nid)
                continue
            applicable = self._applicable_rules(node, rules, dom)
            style = self._apply_cascade(node, applicable)
            styles[nid] = style

        # Inheritance pass (BFS from root)
        self._resolve_inheritance_all(dom, styles)

        # Initial values for anything still missing
        for style in styles.values():
            self._apply_initial_values(style)

        return styles

    def _applicable_rules(
        self, node: DOMNode, rules: list[CSSRule], dom: DOMSite
    ) -> list[CSSRule]:
        """Rules whose selectors match *node*."""
        result: list[CSSRule] = []
        for rule in rules:
            parts = SelectorParser._split_comma(rule.selector)
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                chain = SelectorParser.parse_selector(part)
                if SelectorParser.matches(chain, node, dom):
                    result.append(rule)
                    break
        return result

    def _apply_cascade(
        self, node: DOMNode, applicable_rules: list[CSSRule]
    ) -> ComputedStyle:
        """Apply cascade ordering: higher specificity + later source order wins."""
        sorted_rules = specificity_sort(applicable_rules)
        props: dict[str, CSSValue] = {}
        for rule in sorted_rules:
            for prop_name, css_val in rule.properties.items():
                props[prop_name] = css_val  # last writer wins
        return ComputedStyle(node_id=node.node_id, properties=props)

    def _resolve_inheritance_all(
        self, dom: DOMSite, styles: dict[str, ComputedStyle]
    ) -> None:
        """BFS from root: for each inheritable property, fill in from parent."""
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
            self._resolve_inheritance(nid, styles, dom)
            for cid in node.children:
                queue.append(cid)

    def _resolve_inheritance(
        self, node_id: str, styles: dict[str, ComputedStyle], dom: DOMSite
    ) -> None:
        """Inherit missing inheritable properties from parent."""
        style = styles.get(node_id)
        if style is None:
            return
        node = dom.nodes.get(node_id)
        if node is None or node.parent_id is None:
            return
        parent_style = styles.get(node.parent_id)
        if parent_style is None:
            return
        for prop in INHERITABLE_PROPERTIES:
            if prop in style.properties:
                val = style.properties[prop]
                if val.raw == "inherit" and prop in parent_style.properties:
                    style.properties[prop] = parent_style.properties[prop]
                continue
            if prop in parent_style.properties:
                style.properties[prop] = parent_style.properties[prop]

    def _apply_initial_values(self, style: ComputedStyle) -> None:
        """Fill missing properties with initial values."""
        for prop, initial in INITIAL_VALUES.items():
            if prop not in style.properties:
                style.properties[prop] = CSSValue(raw=initial)


# ---------------------------------------------------------------------------
# Cascade descent checker — finds obstructions
# ---------------------------------------------------------------------------

class CascadeDescentChecker:
    """Check the cascade for obstructions (descent failures)."""

    def check_cascade(
        self, dom: DOMSite, rules: list[CSSRule]
    ) -> list[CascadeObstruction]:
        """Full cascade check — returns all obstructions found."""
        engine = CSSCascadeEngine()
        styles = engine.resolve(dom, rules)
        obstructions: list[CascadeObstruction] = []
        obstructions.extend(self._detect_specificity_conflicts(rules, dom))
        obstructions.extend(self._detect_inheritance_gaps(dom, styles))
        obstructions.extend(self._detect_cascade_leaks(dom, rules))
        obstructions.extend(self._detect_zindex_confusion(dom, styles))
        return obstructions

    def _detect_specificity_conflicts(
        self, rules: list[CSSRule], dom: DOMSite
    ) -> list[CascadeObstruction]:
        """Two rules with equal specificity setting same property on same node."""
        obstructions: list[CascadeObstruction] = []
        n = len(rules)
        for i in range(n):
            for j in range(i + 1, n):
                r1, r2 = rules[i], rules[j]
                if r1.specificity != r2.specificity:
                    continue
                common_props = set(r1.properties) & set(r2.properties)
                if not common_props:
                    continue
                # Check if the selectors overlap on any node
                overlap = dom.selector_overlap(r1.selector, r2.selector)
                if overlap:
                    for prop in sorted(common_props):
                        if r1.properties[prop].raw != r2.properties[prop].raw:
                            obstructions.append(CascadeObstruction(
                                kind=CascadeObstructionKind.SPECIFICITY_CONFLICT,
                                selector1=r1.selector,
                                selector2=r2.selector,
                                property_name=prop,
                                message=(
                                    f"Rules '{r1.selector}' and '{r2.selector}' have "
                                    f"equal specificity {r1.specificity} and set "
                                    f"'{prop}' to different values"
                                ),
                                severity=ObstructionSeverity.MEDIUM,
                                node_ids=[n.node_id for n in overlap],
                            ))
        return obstructions

    def _detect_inheritance_gaps(
        self, dom: DOMSite, styles: dict[str, ComputedStyle]
    ) -> list[CascadeObstruction]:
        """Nodes missing inheritable properties that no ancestor provides."""
        obstructions: list[CascadeObstruction] = []
        for nid, node in dom.nodes.items():
            if node.node_kind != DOMNodeKind.ELEMENT:
                continue
            style = styles.get(nid)
            if style is None:
                continue
            for prop in INHERITABLE_PROPERTIES:
                if prop in style.properties:
                    val = style.properties[prop]
                    # Check if this came from initial values
                    if val.raw == INITIAL_VALUES.get(prop, ""):
                        # Might be an inheritance gap if parent doesn't have it
                        pass  # acceptable — initial value was applied
                    continue
                # Property truly missing — gap
                obstructions.append(CascadeObstruction(
                    kind=CascadeObstructionKind.INHERITANCE_GAP,
                    property_name=prop,
                    message=f"Node {nid} has no value for inheritable property '{prop}'",
                    severity=ObstructionSeverity.LOW,
                    node_ids=[nid],
                ))
        return obstructions

    def _detect_cascade_leaks(
        self, dom: DOMSite, rules: list[CSSRule]
    ) -> list[CascadeObstruction]:
        """Rules with very broad selectors affecting many nodes."""
        obstructions: list[CascadeObstruction] = []
        total_elements = len(dom.all_element_nodes())
        if total_elements == 0:
            return obstructions
        threshold = total_elements * 0.5
        for rule in rules:
            spec = rule.specificity
            # Only flag rules with element-count-only specificity
            if spec.id_count > 0 or spec.class_count > 0:
                continue
            matched = dom.nodes_matching(rule.selector)
            if len(matched) > threshold:
                obstructions.append(CascadeObstruction(
                    kind=CascadeObstructionKind.CASCADE_LEAK,
                    selector1=rule.selector,
                    message=(
                        f"Rule '{rule.selector}' matches {len(matched)}/{total_elements} "
                        f"elements — overly broad"
                    ),
                    severity=ObstructionSeverity.MEDIUM,
                    node_ids=[n.node_id for n in matched],
                ))
        return obstructions

    def _detect_zindex_confusion(
        self, dom: DOMSite, styles: dict[str, ComputedStyle]
    ) -> list[CascadeObstruction]:
        """Multiple positioned elements with explicit z-index in same context."""
        obstructions: list[CascadeObstruction] = []
        positioned_with_zindex: list[tuple[str, int]] = []
        for nid, style in styles.items():
            pos_val = style.get("position")
            z_val = style.get("z-index")
            if pos_val is None or z_val is None:
                continue
            if pos_val.raw in ("absolute", "relative", "fixed", "sticky"):
                if z_val.raw != "auto":
                    try:
                        z = int(z_val.raw)
                    except ValueError:
                        continue
                    positioned_with_zindex.append((nid, z))

        # Check for confusion: multiple elements with same z-index
        z_groups: dict[int, list[str]] = {}
        for nid, z in positioned_with_zindex:
            z_groups.setdefault(z, []).append(nid)

        for z, nids in z_groups.items():
            if len(nids) > 1:
                obstructions.append(CascadeObstruction(
                    kind=CascadeObstructionKind.ZINDEX_CONFUSION,
                    property_name="z-index",
                    message=f"Multiple positioned elements share z-index={z}",
                    severity=ObstructionSeverity.MEDIUM,
                    node_ids=nids,
                ))
        return obstructions
