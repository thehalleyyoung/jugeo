"""Theorems for the DOM sheaf-theoretic model.

Each theorem is a class with a ``check(...)`` method that verifies
whether the theorem holds for a given DOM/CSS configuration and
returns evidence or a counterexample.
"""

from __future__ import annotations

from .models import (
    CSSRule,
    CSSValue,
    CascadeObstruction,
    CascadeObstructionKind,
    ComputedStyle,
    DOMNodeKind,
)
from .dom_site import DOMSite, SelectorParser
from .specificity import specificity_sort
from .css_cascade import CSSCascadeEngine, INHERITABLE_PROPERTIES
from .media_queries import MediaQuery, MediaQueryOverlapAnalyzer, MediaQueryParser
from .layout_model import LayoutBox, ContainmentChecker
from .inheritance import INHERITABLE


class CascadeDescentTheorem:
    """Theorem (Cascade Descent): If all CSS rules have distinct specificities
    for each property-selector pair, the cascade descent has no obstructions.
    That is, for each (node, property) pair, there is at most one rule with
    maximal specificity, ensuring a unique computed value.
    """

    def check(self, dom: DOMSite, rules: list[CSSRule]) -> dict:
        engine = CSSCascadeEngine()
        # For each node, for each property, collect applicable rules
        for nid, node in dom.nodes.items():
            if node.node_kind != DOMNodeKind.ELEMENT:
                continue
            applicable = engine._applicable_rules(node, rules, dom)
            # Group by property
            prop_rules: dict[str, list[CSSRule]] = {}
            for rule in applicable:
                for prop in rule.properties:
                    prop_rules.setdefault(prop, []).append(rule)
            # Check for equal top specificity
            for prop, prules in prop_rules.items():
                if len(prules) < 2:
                    continue
                sorted_r = specificity_sort(prules)
                # Check if top two have equal specificity
                top = sorted_r[-1]
                second = sorted_r[-2]
                if top.specificity == second.specificity:
                    v1 = top.properties.get(prop)
                    v2 = second.properties.get(prop)
                    if v1 and v2 and v1.raw != v2.raw:
                        return {
                            "holds": False,
                            "evidence": (
                                f"Obstruction at node '{nid}', property '{prop}': "
                                f"rules '{top.selector}' and '{second.selector}' "
                                f"have equal specificity {top.specificity}"
                            ),
                            "counterexample": {
                                "node_id": nid,
                                "property": prop,
                                "rule1": top.selector,
                                "rule2": second.selector,
                                "specificity": repr(top.specificity),
                            },
                        }
        return {
            "holds": True,
            "evidence": "All (node, property) pairs have unique maximal-specificity rules",
            "counterexample": None,
        }


class InheritanceCompletionTheorem:
    """Theorem (Inheritance Completion): If every inheritable property is
    defined on the root element, the inheritance chain is complete for all
    descendants — every node has a computed value for each inheritable
    property.
    """

    def check(self, dom: DOMSite, styles: dict[str, ComputedStyle]) -> dict:
        if not dom.root_id:
            return {
                "holds": False,
                "evidence": "No root node",
                "counterexample": {"reason": "empty DOM"},
            }
        root_style = styles.get(dom.root_id)
        if root_style is None:
            return {
                "holds": False,
                "evidence": "Root has no computed style",
                "counterexample": {"reason": "no root style"},
            }
        # Check root has all inheritable properties
        missing_on_root: list[str] = []
        for prop in sorted(INHERITABLE):
            if prop not in root_style.properties:
                missing_on_root.append(prop)

        if missing_on_root:
            return {
                "holds": False,
                "evidence": f"Root missing inheritable properties: {missing_on_root[:5]}",
                "counterexample": {
                    "node_id": dom.root_id,
                    "missing_properties": missing_on_root,
                },
            }
        return {
            "holds": True,
            "evidence": (
                "Root defines all inheritable properties; inheritance chain is complete"
            ),
            "counterexample": None,
        }


class SelectorCoverageTheorem:
    """Theorem (Selector Coverage): If a set of selectors covers the DOM
    (every element node is matched by at least one selector), then every node
    has at least one CSS rule applied, ensuring no node is left unstyled.
    """

    def check(self, dom: DOMSite, rules: list[CSSRule]) -> dict:
        selectors = [r.selector for r in rules]
        elem_ids = {n.node_id for n in dom.all_element_nodes()}
        covered: set[str] = set()
        for sel in selectors:
            for node in dom.nodes_matching(sel):
                covered.add(node.node_id)
        uncovered = elem_ids - covered
        if uncovered:
            sample = sorted(uncovered)[:5]
            return {
                "holds": False,
                "evidence": f"{len(uncovered)} element nodes are not matched by any rule",
                "counterexample": {"uncovered_node_ids": sample},
            }
        return {
            "holds": True,
            "evidence": "All element nodes are matched by at least one CSS rule",
            "counterexample": None,
        }


class MediaQueryGluingTheorem:
    """Theorem (Media Query Gluing): If media query ranges partition
    [0, max_width] without gaps or contradictions, the responsive design
    is globally consistent — every viewport width has exactly one active
    design state for each styled property.
    """

    def check(
        self,
        queries: list[MediaQuery],
        rules_by_query: dict[str, list[CSSRule]],
        total_range: tuple[int, int] = (0, 1920),
    ) -> dict:
        analyzer = MediaQueryOverlapAnalyzer()
        gaps = analyzer.find_gaps(queries, total_range)
        contradictions = analyzer.find_contradictions(rules_by_query)

        if gaps:
            return {
                "holds": False,
                "evidence": f"Gaps in media query coverage: {gaps}",
                "counterexample": {"gaps": gaps},
            }
        if contradictions:
            msgs = [c.message for c in contradictions[:3]]
            return {
                "holds": False,
                "evidence": f"Contradictions: {msgs}",
                "counterexample": {"contradictions": msgs},
            }
        return {
            "holds": True,
            "evidence": (
                "Media queries partition the viewport range without gaps or contradictions"
            ),
            "counterexample": None,
        }


class ContainmentPreservationTheorem:
    """Theorem (Containment Preservation): If parent-child containment holds
    at all breakpoints individually, it holds globally. That is, if for every
    media query range a child element is visually contained within its parent,
    then no breakpoint transition breaks containment.
    """

    def check(
        self,
        parent_boxes_by_breakpoint: dict[int, LayoutBox],
        child_boxes_by_breakpoint: dict[int, LayoutBox],
    ) -> dict:
        checker = ContainmentChecker()
        for bp in sorted(parent_boxes_by_breakpoint):
            parent_box = parent_boxes_by_breakpoint[bp]
            child_box = child_boxes_by_breakpoint.get(bp)
            if child_box is None:
                continue
            if not checker.check_containment(parent_box, child_box):
                return {
                    "holds": False,
                    "evidence": f"Containment fails at breakpoint {bp}px",
                    "counterexample": {
                        "breakpoint": bp,
                        "parent_bounds": parent_box.bounds(),
                        "child_bounds": child_box.bounds(),
                    },
                }
        return {
            "holds": True,
            "evidence": "Containment holds at all breakpoints",
            "counterexample": None,
        }
