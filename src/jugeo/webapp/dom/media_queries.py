"""Media query analysis — detecting discontinuities in responsive design.

Media queries partition the viewport-width axis.  Gaps or contradictions
in this partition are obstructions to the responsive-design sheaf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .models import (
    CSSRule,
    CSSValue,
    CascadeObstruction,
    CascadeObstructionKind,
    ObstructionSeverity,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MediaType(str, Enum):
    """CSS media type."""
    ALL = "all"
    SCREEN = "screen"
    PRINT = "print"
    SPEECH = "speech"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MediaQuery:
    """Parsed ``@media`` condition."""

    condition: str = ""
    media_type: MediaType = MediaType.ALL
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None
    orientation: str | None = None
    prefers_color_scheme: str | None = None
    features: dict[str, str] = field(default_factory=dict)

    def applies_at_width(self, width: int) -> bool:
        """Return True if this query is active at *width* pixels."""
        if self.min_width is not None and width < self.min_width:
            return False
        if self.max_width is not None and width > self.max_width:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "media_type": self.media_type.value,
            "min_width": self.min_width,
            "max_width": self.max_width,
            "min_height": self.min_height,
            "max_height": self.max_height,
            "orientation": self.orientation,
            "prefers_color_scheme": self.prefers_color_scheme,
            "features": dict(self.features),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MediaQuery:
        return cls(
            condition=data.get("condition", ""),
            media_type=MediaType(data.get("media_type", "all")),
            min_width=data.get("min_width"),
            max_width=data.get("max_width"),
            min_height=data.get("min_height"),
            max_height=data.get("max_height"),
            orientation=data.get("orientation"),
            prefers_color_scheme=data.get("prefers_color_scheme"),
            features=data.get("features", {}),
        )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class MediaQueryParser:
    """Parse ``@media`` condition strings."""

    _FEATURE_RE = re.compile(
        r"\(\s*([\w-]+)\s*:\s*([^)]+)\s*\)"
    )

    @classmethod
    def parse_media_query(cls, condition: str) -> MediaQuery:
        """Parse a media query condition string into a ``MediaQuery``."""
        mq = MediaQuery(condition=condition)
        c = condition.strip()

        # Handle "not" prefix
        is_negated = False
        if c.lower().startswith("not "):
            is_negated = True
            c = c[4:].strip()

        # Extract media type
        for mt in MediaType:
            if c.lower().startswith(mt.value):
                mq.media_type = mt
                c = c[len(mt.value):].strip()
                if c.lower().startswith("and "):
                    c = c[4:].strip()
                break

        # Extract features
        for m in cls._FEATURE_RE.finditer(c):
            feature = m.group(1).strip().lower()
            value = m.group(2).strip()
            mq.features[feature] = value

            if feature == "min-width":
                mq.min_width = cls._parse_px(value)
            elif feature == "max-width":
                mq.max_width = cls._parse_px(value)
            elif feature == "min-height":
                mq.min_height = cls._parse_px(value)
            elif feature == "max-height":
                mq.max_height = cls._parse_px(value)
            elif feature == "orientation":
                mq.orientation = value
            elif feature == "prefers-color-scheme":
                mq.prefers_color_scheme = value

        if is_negated:
            # Negate: swap min/max or set to None
            mq.min_width = None
            mq.max_width = None
            mq.min_height = None
            mq.max_height = None

        return mq

    @staticmethod
    def _parse_px(value: str) -> int | None:
        """Extract integer pixel value from e.g. ``768px``."""
        m = re.search(r"(\d+)", value)
        if m:
            return int(m.group(1))
        return None


# ---------------------------------------------------------------------------
# Overlap analysis
# ---------------------------------------------------------------------------

class MediaQueryOverlapAnalyzer:
    """Analyse overlaps, contradictions, and gaps among media queries."""

    def find_overlaps(
        self, queries: list[MediaQuery]
    ) -> list[tuple[MediaQuery, MediaQuery, tuple[int, int]]]:
        """Return pairs of queries that overlap at some viewport width."""
        overlaps: list[tuple[MediaQuery, MediaQuery, tuple[int, int]]] = []
        n = len(queries)
        for i in range(n):
            for j in range(i + 1, n):
                q1, q2 = queries[i], queries[j]
                rng = self._overlap_range(q1, q2)
                if rng is not None:
                    overlaps.append((q1, q2, rng))
        return overlaps

    def find_contradictions(
        self, rules_by_query: dict[str, list[CSSRule]]
    ) -> list[CascadeObstruction]:
        """Find overlapping queries that set the same property/selector to different values."""
        parser = MediaQueryParser()
        obstructions: list[CascadeObstruction] = []
        conditions = list(rules_by_query.keys())
        queries = [parser.parse_media_query(c) for c in conditions]

        for i in range(len(queries)):
            for j in range(i + 1, len(queries)):
                rng = self._overlap_range(queries[i], queries[j])
                if rng is None:
                    continue
                rules1 = rules_by_query[conditions[i]]
                rules2 = rules_by_query[conditions[j]]
                for r1 in rules1:
                    for r2 in rules2:
                        if r1.selector != r2.selector:
                            continue
                        common = set(r1.properties) & set(r2.properties)
                        for prop in sorted(common):
                            if r1.properties[prop].raw != r2.properties[prop].raw:
                                obstructions.append(CascadeObstruction(
                                    kind=CascadeObstructionKind.MEDIA_QUERY_DISCONTINUITY,
                                    selector1=r1.selector,
                                    selector2=r2.selector,
                                    property_name=prop,
                                    message=(
                                        f"Media queries '{conditions[i]}' and "
                                        f"'{conditions[j]}' overlap at widths "
                                        f"{rng} and set '{prop}' on '{r1.selector}' "
                                        f"to different values"
                                    ),
                                    severity=ObstructionSeverity.HIGH,
                                ))
        return obstructions

    def find_gaps(
        self, queries: list[MediaQuery], total_range: tuple[int, int] = (0, 4000)
    ) -> list[tuple[int, int]]:
        """Find viewport-width ranges not covered by any query."""
        lo, hi = total_range
        # Build sorted list of covered intervals
        intervals: list[tuple[int, int]] = []
        for q in queries:
            qmin = q.min_width if q.min_width is not None else lo
            qmax = q.max_width if q.max_width is not None else hi
            if qmin > hi or qmax < lo:
                continue
            intervals.append((max(qmin, lo), min(qmax, hi)))

        if not intervals:
            return [(lo, hi)]

        intervals.sort()
        gaps: list[tuple[int, int]] = []
        covered_up_to = lo
        for start, end in intervals:
            if start > covered_up_to:
                gaps.append((covered_up_to, start - 1))
            covered_up_to = max(covered_up_to, end + 1)
        if covered_up_to <= hi:
            gaps.append((covered_up_to, hi))
        return gaps

    def _overlap_range(
        self, q1: MediaQuery, q2: MediaQuery
    ) -> tuple[int, int] | None:
        lo1 = q1.min_width if q1.min_width is not None else 0
        hi1 = q1.max_width if q1.max_width is not None else 99999
        lo2 = q2.min_width if q2.min_width is not None else 0
        hi2 = q2.max_width if q2.max_width is not None else 99999
        lo = max(lo1, lo2)
        hi = min(hi1, hi2)
        if lo <= hi:
            return (lo, hi)
        return None


# ---------------------------------------------------------------------------
# Breakpoint analysis
# ---------------------------------------------------------------------------

class BreakpointAnalyzer:
    """Analyse responsive breakpoints."""

    def extract_breakpoints(self, queries: list[MediaQuery]) -> list[int]:
        """All min_width / max_width values, sorted ascending."""
        bps: set[int] = set()
        for q in queries:
            if q.min_width is not None:
                bps.add(q.min_width)
            if q.max_width is not None:
                bps.add(q.max_width)
        return sorted(bps)

    def validate_breakpoint_continuity(
        self, queries: list[MediaQuery]
    ) -> list[str]:
        """Warn about gaps in breakpoint coverage."""
        analyzer = MediaQueryOverlapAnalyzer()
        gaps = analyzer.find_gaps(queries)
        warnings: list[str] = []
        for lo, hi in gaps:
            warnings.append(
                f"Gap in media query coverage: viewport widths {lo}–{hi}px have no active query"
            )
        return warnings

    @staticmethod
    def common_breakpoints() -> list[int]:
        """Standard responsive breakpoints."""
        return [320, 480, 768, 1024, 1280, 1440, 1920]
