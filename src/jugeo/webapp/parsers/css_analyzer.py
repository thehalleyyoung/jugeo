"""
CSS parser and specificity calculator.

Uses regular expressions to extract CSS rules, media queries, keyframe
animations, individual properties, and to compute selector specificity.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

from .models import (
    CoordinateKind,
    ErrorSeverity,
    Language,
    ParsedCoordinate,
    ParsedReference,
    ParseError,
    ParseResult,
    ReferenceType,
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Strip block comments
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Full rule: selector { ... }
_RULE_RE = re.compile(
    r"([^{}@/]+?)\s*\{([^{}]*)\}",
)

# Media queries
_MEDIA_RE = re.compile(
    r"@media\s+([^{]+)\s*\{",
)

# Keyframes
_KEYFRAMES_RE = re.compile(
    r"@keyframes\s+(\S+)\s*\{",
)

# Individual properties inside a rule body
_PROP_RE = re.compile(
    r"\s*([\w-]+)\s*:\s*([^;]+);",
)

# Selector component patterns for specificity
_ID_SEL_RE = re.compile(r"#[\w-]+")
_CLASS_SEL_RE = re.compile(r"\.[\w-]+")
_ATTR_SEL_RE = re.compile(r"\[[^\]]+\]")
_PSEUDO_CLASS_RE = re.compile(r":(?!:)[\w-]+(?:\([^)]*\))?")
_PSEUDO_ELEM_RE = re.compile(r"::[\w-]+")
_ELEM_SEL_RE = re.compile(r"(?:^|[\s>+~])(\w[\w-]*)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord_id(file_path: str, kind: str, name: str, line: int) -> str:
    raw = f"{file_path}::{kind}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()


def _line_number_at(source: str, pos: int) -> int:
    return source[:pos].count("\n") + 1


def _strip_comments(source: str) -> str:
    return _COMMENT_RE.sub("", source)


# ---------------------------------------------------------------------------
# CSSSpecificityCalculator
# ---------------------------------------------------------------------------

class CSSSpecificityCalculator:
    """Compute CSS selector specificity as ``(a, b, c)`` where:

    * *a* = number of ID selectors
    * *b* = number of class selectors + attribute selectors + pseudo-classes
    * *c* = number of type (element) selectors + pseudo-elements
    """

    def compute(self, selector: str) -> tuple[int, int, int]:
        # Remove :not() wrapper but keep contents
        sel = re.sub(r":not\(([^)]*)\)", r" \1", selector)
        # Remove the universal selector
        sel = sel.replace("*", "")

        a = len(_ID_SEL_RE.findall(sel))

        # Remove IDs so they don't interfere with element matching
        sel_no_id = _ID_SEL_RE.sub("", sel)

        classes = len(_CLASS_SEL_RE.findall(sel_no_id))
        attrs = len(_ATTR_SEL_RE.findall(sel_no_id))

        # Remove classes and attrs before counting pseudo-classes
        sel_stripped = _CLASS_SEL_RE.sub("", sel_no_id)
        sel_stripped = _ATTR_SEL_RE.sub("", sel_stripped)

        pseudo_elems = len(_PSEUDO_ELEM_RE.findall(sel_stripped))
        # Remove pseudo-elements before counting pseudo-classes
        sel_stripped2 = _PSEUDO_ELEM_RE.sub("", sel_stripped)
        pseudo_classes = len(_PSEUDO_CLASS_RE.findall(sel_stripped2))

        b = classes + attrs + pseudo_classes

        # Remove pseudo-classes before counting elements
        sel_for_elems = _PSEUDO_CLASS_RE.sub("", sel_stripped2)
        elements = len(_ELEM_SEL_RE.findall(sel_for_elems))

        c = elements + pseudo_elems

        return (a, b, c)


# ---------------------------------------------------------------------------
# CSSParser
# ---------------------------------------------------------------------------

class CSSParser:
    """Parse a CSS source file and extract coordinates."""

    def parse(self, source: str, file_path: str) -> ParseResult:
        t0 = time.monotonic()
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []
        errors: list[ParseError] = []

        try:
            clean = _strip_comments(source)
            coords.extend(self._extract_rules(clean, file_path))
            coords.extend(self._extract_media_queries(clean, file_path))
            coords.extend(self._extract_animations(clean, file_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(ParseError(
                file_path=file_path,
                line_number=0,
                message=f"CSS parse error: {exc}",
                severity=ErrorSeverity.ERROR,
            ))

        elapsed = (time.monotonic() - t0) * 1000
        return ParseResult(
            file_path=file_path,
            language=Language.CSS,
            coordinates=coords,
            references=refs,
            errors=errors,
            parse_time_ms=elapsed,
        )

    def _extract_rules(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        # Remove @media blocks for top-level rule scanning
        no_at = re.sub(r"@\w+[^{]*\{[^{}]*(?:\{[^}]*\}[^{}]*)*\}", "", source)
        for m in _RULE_RE.finditer(no_at):
            selector = m.group(1).strip()
            body = m.group(2)
            if not selector or selector.startswith("@"):
                continue
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.CSS_RULE.value, selector, line)
            rule_coord = ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.CSS_RULE,
                name=selector,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.CSS,
                metadata={"selector": selector},
            )
            coords.append(rule_coord)
            coords.extend(self._extract_properties(body, rule_coord, file_path))
        return coords

    def _extract_properties(self, body: str, rule_coord: ParsedCoordinate,
                            file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _PROP_RE.finditer(body):
            prop = m.group(1).strip()
            value = m.group(2).strip()
            cid = _make_coord_id(
                file_path, CoordinateKind.CSS_PROPERTY.value,
                f"{rule_coord.name}::{prop}", rule_coord.line_number,
            )
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.CSS_PROPERTY,
                name=prop,
                file_path=file_path,
                line_number=rule_coord.line_number,
                end_line=rule_coord.line_number,
                language=Language.CSS,
                metadata={"value": value, "parent_selector": rule_coord.name},
            ))
        return coords

    def _extract_media_queries(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _MEDIA_RE.finditer(source):
            query = m.group(1).strip()
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.CSS_MEDIA_QUERY.value, query, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.CSS_MEDIA_QUERY,
                name=query,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.CSS,
                metadata={"query": query},
            ))
        return coords

    def _extract_animations(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _KEYFRAMES_RE.finditer(source):
            name = m.group(1).strip()
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.CSS_ANIMATION.value, name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.CSS_ANIMATION,
                name=name,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.CSS,
                metadata={"animation_name": name},
            ))
        return coords


# ---------------------------------------------------------------------------
# CSSCascadeAnalyzer
# ---------------------------------------------------------------------------

class CSSCascadeAnalyzer:
    """Analyse specificity conflicts and unreferenced classes."""

    def __init__(self) -> None:
        self._calc = CSSSpecificityCalculator()

    def find_specificity_conflicts(self, coords: list[ParsedCoordinate]) -> list[dict]:
        """Find selectors that target the same simple selector with different specificities."""
        rules = [c for c in coords if c.kind == CoordinateKind.CSS_RULE]
        # Group by the first simple selector token
        groups: dict[str, list[ParsedCoordinate]] = {}
        for rule in rules:
            tokens = re.split(r"[\s>+~]+", rule.name.strip())
            for token in tokens:
                base = re.sub(r"[.#:\[\]].*", "", token)
                if base:
                    groups.setdefault(base, []).append(rule)
                    break

        conflicts: list[dict] = []
        for base, members in groups.items():
            if len(members) < 2:
                continue
            specs = [(m, self._calc.compute(m.name)) for m in members]
            specs.sort(key=lambda x: x[1])
            if specs[0][1] != specs[-1][1]:
                conflicts.append({
                    "element": base,
                    "selectors": [
                        {"selector": s.name, "specificity": list(sp)} for s, sp in specs
                    ],
                })
        return conflicts

    def find_unreferenced_classes(
        self,
        css_coords: list[ParsedCoordinate],
        html_coords: list[ParsedCoordinate],
    ) -> list[str]:
        """Return CSS class selectors that don't appear in any HTML coordinate."""
        css_classes: set[str] = set()
        for c in css_coords:
            if c.kind == CoordinateKind.CSS_RULE:
                for m in _CLASS_SEL_RE.finditer(c.name):
                    css_classes.add(m.group(0)[1:])  # strip leading '.'

        html_classes: set[str] = set()
        for c in html_coords:
            for cls in c.metadata.get("classes", []):
                html_classes.add(cls)

        return sorted(css_classes - html_classes)

    def find_media_query_overlaps(self, coords: list[ParsedCoordinate]) -> list[dict]:
        """Find media queries whose ranges might overlap."""
        mqs = [c for c in coords if c.kind == CoordinateKind.CSS_MEDIA_QUERY]
        overlaps: list[dict] = []

        def _parse_width(query: str) -> tuple[int | None, int | None]:
            min_w = max_w = None
            m_min = re.search(r"min-width\s*:\s*(\d+)", query)
            m_max = re.search(r"max-width\s*:\s*(\d+)", query)
            if m_min:
                min_w = int(m_min.group(1))
            if m_max:
                max_w = int(m_max.group(1))
            return min_w, max_w

        for i, a in enumerate(mqs):
            for b in mqs[i + 1:]:
                a_min, a_max = _parse_width(a.name)
                b_min, b_max = _parse_width(b.name)
                if a_min is None and a_max is None:
                    continue
                if b_min is None and b_max is None:
                    continue
                a_lo = a_min or 0
                a_hi = a_max or 99999
                b_lo = b_min or 0
                b_hi = b_max or 99999
                if a_lo <= b_hi and b_lo <= a_hi:
                    overlaps.append({
                        "query_a": a.name,
                        "query_b": b.name,
                        "overlap_range": (max(a_lo, b_lo), min(a_hi, b_hi)),
                    })
        return overlaps


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def extract_css_coordinates(source: str, file_path: str) -> ParseResult:
    """Convenience wrapper around ``CSSParser.parse``."""
    return CSSParser().parse(source, file_path)


def extract_css_classes(source: str) -> set[str]:
    """Return all ``.class-name`` selectors found in *source*."""
    clean = _strip_comments(source)
    classes: set[str] = set()
    for m in _RULE_RE.finditer(clean):
        selector = m.group(1).strip()
        for cm in _CLASS_SEL_RE.finditer(selector):
            classes.add(cm.group(0)[1:])
    return classes


def extract_css_ids(source: str) -> set[str]:
    """Return all ``#id`` selectors found in *source*."""
    clean = _strip_comments(source)
    ids: set[str] = set()
    for m in _RULE_RE.finditer(clean):
        selector = m.group(1).strip()
        for im in _ID_SEL_RE.finditer(selector):
            ids.add(im.group(0)[1:])
    return ids


def compute_specificity(selector: str) -> tuple[int, int, int]:
    """Convenience wrapper around ``CSSSpecificityCalculator.compute``."""
    return CSSSpecificityCalculator().compute(selector)
