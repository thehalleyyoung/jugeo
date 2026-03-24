"""CSS specificity computation and conflict detection.

Specificity is the key ordering that controls the cascade descent.
When two rules have equal specificity for the same property, we have
an obstruction (ambiguous winner resolved only by source order).
"""

from __future__ import annotations

import re

from .models import CSSRule, Specificity


# ---------------------------------------------------------------------------
# Specificity computation
# ---------------------------------------------------------------------------

def _strip_pseudo_elements(s: str) -> tuple[str, int]:
    """Strip ``::pseudo-element`` parts, returning (cleaned, count)."""
    count = len(re.findall(r"::[\w-]+", s))
    cleaned = re.sub(r"::[\w-]+", "", s)
    return cleaned, count


def _handle_where(s: str) -> str:
    """:where(...) contributes zero specificity — remove entirely."""
    result = s
    while ":where(" in result:
        start = result.index(":where(")
        depth = 0
        end = start + 7  # len(":where(") == 7
        for i in range(start + 7, len(result)):
            if result[i] == "(":
                depth += 1
            elif result[i] == ")":
                if depth == 0:
                    end = i + 1
                    break
                depth -= 1
        result = result[:start] + result[end:]
    return result


def _extract_functional_pseudo(s: str, name: str) -> tuple[str, list[str]]:
    """Extract :<name>(...) calls, returning (cleaned, [inner_contents])."""
    inners: list[str] = []
    result = s
    tag = f":{name}("
    while tag in result:
        start = result.index(tag)
        depth = 0
        end = start + len(tag) - 1
        for i in range(start + len(tag), len(result)):
            if result[i] == "(":
                depth += 1
            elif result[i] == ")":
                if depth == 0:
                    inner = result[start + len(tag):i]
                    inners.append(inner)
                    end = i + 1
                    break
                depth -= 1
        result = result[:start] + result[end:]
    return result, inners


def _specificity_of_inner(inner: str) -> Specificity:
    """Compute specificity of a single inner selector (for :not/:is/:has)."""
    # Comma-separated: take the most specific one
    parts = [p.strip() for p in inner.split(",")]
    specs = [compute_specificity(p) for p in parts if p]
    if not specs:
        return Specificity()
    return max(specs)


def compute_specificity(selector: str) -> Specificity:
    """Compute CSS specificity per the CSS Selectors specification.

    Returns a ``Specificity(id_count, class_count, element_count)``.
    """
    s = selector.strip()
    if not s:
        return Specificity()

    # 1. Strip and count pseudo-elements
    s, pseudo_elem_count = _strip_pseudo_elements(s)

    # 2. Handle :where() — zero specificity
    s = _handle_where(s)

    # 3. Handle :not() — inner counts, :not itself doesn't
    s, not_inners = _extract_functional_pseudo(s, "not")
    not_spec = Specificity()
    for inner in not_inners:
        not_spec = not_spec + _specificity_of_inner(inner)

    # 4. Handle :is() — takes specificity of most specific argument
    s, is_inners = _extract_functional_pseudo(s, "is")
    is_spec = Specificity()
    for inner in is_inners:
        is_spec = is_spec + _specificity_of_inner(inner)

    # 5. Handle :has() — same as :is()
    s, has_inners = _extract_functional_pseudo(s, "has")
    has_spec = Specificity()
    for inner in has_inners:
        has_spec = has_spec + _specificity_of_inner(inner)

    # Now count the remaining tokens in s
    id_count = 0
    class_count = 0
    element_count = pseudo_elem_count

    # Remove attribute selectors and count them
    attr_matches = re.findall(r"\[[^\]]*\]", s)
    class_count += len(attr_matches)
    s = re.sub(r"\[[^\]]*\]", "", s)

    # Count #id
    id_matches = re.findall(r"#[\w-]+", s)
    id_count += len(id_matches)
    s = re.sub(r"#[\w-]+", "", s)

    # Count .class
    cls_matches = re.findall(r"\.[\w-]+", s)
    class_count += len(cls_matches)
    s = re.sub(r"\.[\w-]+", "", s)

    # Count :pseudo-class (remaining : that are not ::)
    pseudo_cls_matches = re.findall(r":[\w-]+", s)
    class_count += len(pseudo_cls_matches)
    s = re.sub(r":[\w-]+", "", s)

    # Remove combinators and whitespace
    s = re.sub(r"[>+~\s]+", " ", s).strip()

    # Count element type selectors (not *)
    for token in s.split():
        token = token.strip()
        if token and token != "*":
            element_count += 1

    result = Specificity(id_count, class_count, element_count)
    return result + not_spec + is_spec + has_spec


# ---------------------------------------------------------------------------
# Comparison helper
# ---------------------------------------------------------------------------

def compare_specificity(s1: Specificity, s2: Specificity) -> int:
    """Return -1 if s1 < s2, 0 if equal, 1 if s1 > s2."""
    if s1 < s2:
        return -1
    if s1 > s2:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def specificity_sort(rules: list[CSSRule]) -> list[CSSRule]:
    """Sort *rules* ascending by (specificity, source_order). Returns new list."""
    return sorted(rules, key=lambda r: (r.specificity._as_tuple(), r.source_order))


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

class SpecificityConflictDetector:
    """Detect pairs of rules with equal specificity that target the same property."""

    def find_conflicts(
        self, rules: list[CSSRule]
    ) -> list[tuple[CSSRule, CSSRule, str]]:
        """Return ``(rule1, rule2, property_name)`` for every ambiguous pair.

        Two rules conflict when they share a property and have equal
        specificity (the winner is decided only by source order — fragile).
        """
        conflicts: list[tuple[CSSRule, CSSRule, str]] = []
        n = len(rules)
        for i in range(n):
            for j in range(i + 1, n):
                r1, r2 = rules[i], rules[j]
                if r1.specificity != r2.specificity:
                    continue
                common_props = set(r1.properties) & set(r2.properties)
                for prop in sorted(common_props):
                    conflicts.append((r1, r2, prop))
        return conflicts
