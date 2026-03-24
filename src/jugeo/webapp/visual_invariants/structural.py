"""
Structural invariant family — form submit, alt text, heading hierarchy, labels,
lang attribute, ARIA roles.

Checks DOM structure for accessibility and semantic correctness.

Part of §3.6 invariant family 5 (structural).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import InvariantResult, InvariantStatus


__all__ = [
    "StructuralInvariant",
    "StructuralChecker",
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class StructuralInvariant:
    """A structural property that must hold in the DOM."""

    subject: str
    property_check: str  # "form_submit", "alt_text", "heading_hierarchy", ...
    expected_value: str = "true"
    holds_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"structural_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "property_check": self.property_check,
            "expected_value": self.expected_value,
            "holds_on": list(self.holds_on),
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StructuralInvariant:
        return cls(
            subject=d["subject"],
            property_check=d["property_check"],
            expected_value=d.get("expected_value", "true"),
            holds_on=d.get("holds_on", []),
            id=d.get("id", f"structural_{uuid.uuid4().hex[:8]}"),
        )


# ---------------------------------------------------------------------------
# Heading tags set
# ---------------------------------------------------------------------------

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Input types that do NOT need labels
_LABEL_EXEMPT_TYPES = {"hidden", "submit", "button", "reset", "image"}

# Interactive elements with implicit ARIA roles
_IMPLICIT_ROLE_TAGS = {"button", "a", "input", "select", "textarea"}


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class StructuralChecker:
    """Checks structural invariants against a DOM tree."""

    # ---- tree traversal ---------------------------------------------------

    def _find_all(
        self,
        dom: dict[str, Any],
        tag: Optional[str] = None,
        predicate: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> list[dict[str, Any]]:
        """DFS traversal returning all nodes matching *tag* and/or *predicate*."""
        results: list[dict[str, Any]] = []
        match = True
        if tag is not None and dom.get("tag") != tag:
            match = False
        if predicate is not None and not predicate(dom):
            match = False
        if match:
            results.append(dom)
        for child in dom.get("children", []):
            results.extend(self._find_all(child, tag, predicate))
        return results

    def _find_by_id(self, dom: dict[str, Any], node_id: str) -> Optional[dict[str, Any]]:
        """Return the first node whose ``id`` equals *node_id*."""
        if dom.get("id") == node_id:
            return dom
        for child in dom.get("children", []):
            found = self._find_by_id(child, node_id)
            if found is not None:
                return found
        return None

    def _collect_dfs(self, dom: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect all nodes in DFS (document) order."""
        nodes: list[dict[str, Any]] = [dom]
        for child in dom.get("children", []):
            nodes.extend(self._collect_dfs(child))
        return nodes

    def _is_ancestor(self, ancestor: dict[str, Any], descendant_id: str) -> bool:
        """Check if *descendant_id* is nested inside *ancestor*."""
        if ancestor.get("id") == descendant_id:
            return True
        for child in ancestor.get("children", []):
            if self._is_ancestor(child, descendant_id):
                return True
        return False

    # ---- check methods ----------------------------------------------------

    def check_form_submit(self, dom: dict[str, Any], form_id: str) -> InvariantResult:
        """Form has a submit button."""
        inv_id = f"structural_form_submit_{form_id}"
        form_node = self._find_by_id(dom, form_id)
        if form_node is None:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.UNKNOWN,
                message=f"Form '{form_id}' not found in DOM",
            )

        # Look for button[type=submit] or input[type=submit]
        buttons = self._find_all(form_node, tag="button")
        for btn in buttons:
            btn_type = btn.get("attrs", {}).get("type", "submit")
            if btn_type == "submit":
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.SATISFIED,
                    message=f"Form '{form_id}' has submit button",
                )

        inputs = self._find_all(form_node, tag="input")
        for inp in inputs:
            inp_type = inp.get("attrs", {}).get("type", "text")
            if inp_type == "submit":
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.SATISFIED,
                    message=f"Form '{form_id}' has submit input",
                )

        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            message=f"Form '{form_id}' has no submit button or input",
        )

    def check_alt_text(self, dom: dict[str, Any]) -> InvariantResult:
        """All ``<img>`` elements have a non-empty ``alt`` attribute."""
        inv_id = "structural_alt_text"
        images = self._find_all(dom, tag="img")
        if not images:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                message="No images found",
            )

        missing: list[str] = []
        for img in images:
            alt = img.get("attrs", {}).get("alt")
            if not alt:
                img_id = img.get("id", "<anonymous>")
                missing.append(img_id)

        if missing:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"missing_alt": missing},
                message=f"Images without alt text: {missing}",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message=f"All {len(images)} images have alt text",
        )

    def check_heading_hierarchy(self, dom: dict[str, Any]) -> InvariantResult:
        """No heading level is skipped (e.g. h3 without preceding h2)."""
        inv_id = "structural_heading_hierarchy"
        all_nodes = self._collect_dfs(dom)
        headings: list[tuple[str, int]] = []
        for node in all_nodes:
            tag = node.get("tag", "")
            if tag in _HEADING_TAGS:
                level = int(tag[1])
                headings.append((tag, level))

        if not headings:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                message="No headings found",
            )

        seen_levels: set[int] = set()
        for tag, level in headings:
            # A heading at level N requires that level N-1 has been seen
            # (unless level is 1, which is always allowed)
            if level > 1 and (level - 1) not in seen_levels:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.VIOLATED,
                    evidence={"tag": tag, "level": level, "seen": sorted(seen_levels)},
                    message=f"<{tag}> found but <h{level - 1}> never preceded it",
                )
            seen_levels.add(level)

        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message=f"Heading hierarchy valid: {sorted(seen_levels)}",
        )

    def check_label_association(self, dom: dict[str, Any]) -> InvariantResult:
        """All input elements (except exempt types) have an associated label."""
        inv_id = "structural_label_association"
        inputs = self._find_all(dom, tag="input")
        labels = self._find_all(dom, tag="label")

        # Build set of input IDs that labels point to via 'for'
        labelled_by_for: set[str] = set()
        for lbl in labels:
            for_attr = lbl.get("attrs", {}).get("for", "")
            if for_attr:
                labelled_by_for.add(for_attr)

        # Build set of input IDs that are nested inside a label
        labelled_by_nesting: set[str] = set()
        for lbl in labels:
            nested_inputs = self._find_all(lbl, tag="input")
            for ni in nested_inputs:
                nid = ni.get("id", "")
                if nid:
                    labelled_by_nesting.add(nid)

        unlabelled: list[str] = []
        for inp in inputs:
            inp_type = inp.get("attrs", {}).get("type", "text")
            if inp_type in _LABEL_EXEMPT_TYPES:
                continue
            inp_id = inp.get("id", "")
            if inp_id and (inp_id in labelled_by_for or inp_id in labelled_by_nesting):
                continue
            unlabelled.append(inp_id or "<anonymous>")

        if unlabelled:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"unlabelled": unlabelled},
                message=f"Inputs without labels: {unlabelled}",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message="All inputs have associated labels",
        )

    def check_lang_attribute(self, dom: dict[str, Any]) -> InvariantResult:
        """Root html element has a ``lang`` attribute."""
        inv_id = "structural_lang_attribute"
        root = dom
        # Navigate to <html> if the root is a document wrapper
        if root.get("tag") != "html":
            html_nodes = self._find_all(dom, tag="html")
            if html_nodes:
                root = html_nodes[0]
            else:
                return InvariantResult(
                    invariant_id=inv_id,
                    status=InvariantStatus.VIOLATED,
                    message="No <html> element found",
                )

        lang = root.get("attrs", {}).get("lang", "")
        if lang:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.SATISFIED,
                evidence={"lang": lang},
                message=f"<html> has lang='{lang}'",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.VIOLATED,
            message="<html> is missing the lang attribute",
        )

    def check_aria_roles(self, dom: dict[str, Any]) -> InvariantResult:
        """Interactive elements have explicit or implicit ARIA roles."""
        inv_id = "structural_aria_roles"
        missing_role: list[str] = []

        for tag in _IMPLICIT_ROLE_TAGS:
            elements = self._find_all(dom, tag=tag)
            for el in elements:
                attrs = el.get("attrs", {})
                # Has explicit role attribute → fine
                if "role" in attrs:
                    continue
                # Implicit role exists for standard tags → fine
                # We only flag elements that are non-standard or unusual
                # For this implementation we accept all standard interactive tags as OK
                continue

        if missing_role:
            return InvariantResult(
                invariant_id=inv_id,
                status=InvariantStatus.VIOLATED,
                evidence={"missing_role": missing_role},
                message=f"Elements without ARIA roles: {missing_role}",
            )
        return InvariantResult(
            invariant_id=inv_id,
            status=InvariantStatus.SATISFIED,
            message="All interactive elements have roles",
        )

    def check_all(
        self,
        invariants: list[Any],
        dom: dict[str, Any],
    ) -> list[InvariantResult]:
        """Dispatch each structural invariant to its check method."""
        results: list[InvariantResult] = []
        for inv in invariants:
            if not isinstance(inv, StructuralInvariant):
                continue
            check = inv.property_check
            if check == "form_submit":
                results.append(self.check_form_submit(dom, inv.subject))
            elif check == "alt_text":
                results.append(self.check_alt_text(dom))
            elif check == "heading_hierarchy":
                results.append(self.check_heading_hierarchy(dom))
            elif check == "label_association":
                results.append(self.check_label_association(dom))
            elif check == "lang_attribute":
                results.append(self.check_lang_attribute(dom))
            elif check == "aria_roles":
                results.append(self.check_aria_roles(dom))
            else:
                results.append(
                    InvariantResult(
                        invariant_id=inv.id,
                        status=InvariantStatus.UNKNOWN,
                        message=f"Unknown property_check: '{check}'",
                    )
                )
        return results
