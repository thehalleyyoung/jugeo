"""DOM algorithms — diff, coverage analysis, and accessibility checks.

These are concrete algorithms that operate on the DOM site and CSS rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import (
    CSSRule,
    CSSValue,
    CascadeObstruction,
    CascadeObstructionKind,
    ComputedStyle,
    DOMNode,
    DOMNodeKind,
    ObstructionSeverity,
)
from .dom_site import DOMSite, SelectorParser


# ---------------------------------------------------------------------------
# DOM change tracking
# ---------------------------------------------------------------------------

class DOMChangeKind(str, Enum):
    """Kind of DOM mutation."""
    NODE_ADDED = "node_added"
    NODE_REMOVED = "node_removed"
    ATTRIBUTE_CHANGED = "attribute_changed"
    TEXT_CHANGED = "text_changed"
    NODE_MOVED = "node_moved"
    CLASS_CHANGED = "class_changed"


@dataclass
class DOMChange:
    """A single DOM change."""

    kind: DOMChangeKind
    node_id: str
    old_value: str | None = None
    new_value: str | None = None
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "node_id": self.node_id,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DOMChange:
        return cls(
            kind=DOMChangeKind(data.get("kind", "node_added")),
            node_id=data.get("node_id", ""),
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

class DOMDiffEngine:
    """Compute structural diffs between two DOM states."""

    def diff(self, dom1: DOMSite, dom2: DOMSite) -> list[DOMChange]:
        """Return the list of changes from *dom1* to *dom2*."""
        changes: list[DOMChange] = []
        ids1 = set(dom1.nodes)
        ids2 = set(dom2.nodes)

        # Removed nodes
        for nid in sorted(ids1 - ids2):
            changes.append(DOMChange(
                kind=DOMChangeKind.NODE_REMOVED,
                node_id=nid,
                description=f"Node {nid} removed",
            ))

        # Added nodes
        for nid in sorted(ids2 - ids1):
            changes.append(DOMChange(
                kind=DOMChangeKind.NODE_ADDED,
                node_id=nid,
                description=f"Node {nid} added",
            ))

        # Changed nodes
        for nid in sorted(ids1 & ids2):
            self._diff_node(dom1.nodes[nid], dom2.nodes[nid], changes)

        return changes

    def _diff_node(
        self,
        node1: DOMNode | None,
        node2: DOMNode | None,
        changes: list[DOMChange],
    ) -> None:
        if node1 is None or node2 is None:
            return
        nid = node1.node_id

        # Attribute changes (compare attributes dicts)
        if node1.attributes != node2.attributes:
            changes.append(DOMChange(
                kind=DOMChangeKind.ATTRIBUTE_CHANGED,
                node_id=nid,
                old_value=str(node1.attributes),
                new_value=str(node2.attributes),
                description=f"Attributes changed on {nid}",
            ))

        # Class changes
        if sorted(node1.classes) != sorted(node2.classes):
            changes.append(DOMChange(
                kind=DOMChangeKind.CLASS_CHANGED,
                node_id=nid,
                old_value=" ".join(node1.classes),
                new_value=" ".join(node2.classes),
                description=f"Classes changed on {nid}",
            ))

        # Text content
        if node1.text_content != node2.text_content:
            changes.append(DOMChange(
                kind=DOMChangeKind.TEXT_CHANGED,
                node_id=nid,
                old_value=node1.text_content,
                new_value=node2.text_content,
                description=f"Text changed on {nid}",
            ))

        # Parent changed (node moved)
        if node1.parent_id != node2.parent_id:
            changes.append(DOMChange(
                kind=DOMChangeKind.NODE_MOVED,
                node_id=nid,
                old_value=node1.parent_id,
                new_value=node2.parent_id,
                description=f"Node {nid} moved from {node1.parent_id} to {node2.parent_id}",
            ))


# ---------------------------------------------------------------------------
# Selector coverage analysis
# ---------------------------------------------------------------------------

class SelectorCoverageAnalyzer:
    """Analyse how well CSS rules cover the DOM."""

    def uncovered_nodes(
        self, dom: DOMSite, rules: list[CSSRule]
    ) -> list[DOMNode]:
        """ELEMENT nodes not targeted by any CSS rule."""
        covered: set[str] = set()
        for rule in rules:
            for node in dom.nodes_matching(rule.selector):
                covered.add(node.node_id)
        return [
            n for n in dom.all_element_nodes()
            if n.node_id not in covered
        ]

    def over_targeted_nodes(
        self, dom: DOMSite, rules: list[CSSRule], threshold: int = 5
    ) -> list[tuple[DOMNode, int]]:
        """Nodes targeted by more than *threshold* rules.

        Returns ``(node, rule_count)`` sorted descending by count.
        """
        counts: dict[str, int] = {}
        for rule in rules:
            for node in dom.nodes_matching(rule.selector):
                counts[node.node_id] = counts.get(node.node_id, 0) + 1
        result = [
            (dom.nodes[nid], cnt)
            for nid, cnt in counts.items()
            if cnt > threshold and nid in dom.nodes
        ]
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def coverage_report(self, dom: DOMSite, rules: list[CSSRule]) -> dict:
        """Summary statistics."""
        all_elems = dom.all_element_nodes()
        total = len(all_elems)
        uncovered = self.uncovered_nodes(dom, rules)
        over_targeted = self.over_targeted_nodes(dom, rules)
        covered = total - len(uncovered)
        pct = (covered / total * 100) if total > 0 else 0.0
        return {
            "total_nodes": total,
            "covered": covered,
            "uncovered": len(uncovered),
            "coverage_pct": round(pct, 2),
            "over_targeted": len(over_targeted),
        }


# ---------------------------------------------------------------------------
# Accessibility checker
# ---------------------------------------------------------------------------

class AccessibilityChecker:
    """Basic accessibility checks on the DOM."""

    def check_alt_text(self, dom: DOMSite) -> list[str]:
        """Return node_ids of ``<img>`` elements without ``alt`` attribute."""
        return [
            n.node_id for n in dom.all_element_nodes()
            if n.tag == "img" and "alt" not in n.attributes
        ]

    def check_heading_hierarchy(self, dom: DOMSite) -> list[str]:
        """Find heading-level violations (e.g. h1 → h3 skipping h2)."""
        headings: list[tuple[str, int]] = []
        for n in dom.all_element_nodes():
            if n.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(n.tag[1])
                headings.append((n.node_id, level))

        warnings: list[str] = []
        for i in range(1, len(headings)):
            prev_level = headings[i - 1][1]
            cur_level = headings[i][1]
            if cur_level > prev_level + 1:
                warnings.append(
                    f"Heading hierarchy violation: {headings[i-1][0]} (h{prev_level}) "
                    f"followed by {headings[i][0]} (h{cur_level}), skipping h{prev_level+1}"
                )
        return warnings

    def check_form_labels(self, dom: DOMSite) -> list[str]:
        """Return node_ids of ``<input>`` elements without an associated label."""
        # Collect all label "for" targets
        label_fors: set[str] = set()
        for n in dom.all_element_nodes():
            if n.tag == "label":
                for_attr = n.attributes.get("for", "")
                if for_attr:
                    label_fors.add(for_attr)

        unlabelled: list[str] = []
        for n in dom.all_element_nodes():
            if n.tag == "input":
                input_id = n.id_attr or n.attributes.get("id", "")
                if not input_id or input_id not in label_fors:
                    unlabelled.append(n.node_id)
        return unlabelled

    def check_lang_attribute(self, dom: DOMSite) -> list[str]:
        """Check that ``<html>`` element has ``lang`` attribute."""
        issues: list[str] = []
        for n in dom.all_element_nodes():
            if n.tag == "html":
                if "lang" not in n.attributes:
                    issues.append(n.node_id)
        return issues

    def check_button_text(self, dom: DOMSite) -> list[str]:
        """Find ``<button>``/``<a>`` elements with no text or aria-label."""
        issues: list[str] = []
        for n in dom.all_element_nodes():
            if n.tag in ("button", "a"):
                has_text = False
                # Check direct text children
                for cid in n.children:
                    child = dom.nodes.get(cid)
                    if child and child.node_kind == DOMNodeKind.TEXT and child.text_content.strip():
                        has_text = True
                        break
                if not has_text and "aria-label" not in n.attributes:
                    issues.append(n.node_id)
        return issues

    def run_all_checks(self, dom: DOMSite) -> dict[str, list[str]]:
        """Run all checks and return ``{check_name: [issues]}``."""
        return {
            "alt_text": self.check_alt_text(dom),
            "heading_hierarchy": self.check_heading_hierarchy(dom),
            "form_labels": self.check_form_labels(dom),
            "lang_attribute": self.check_lang_attribute(dom),
            "button_text": self.check_button_text(dom),
        }
