"""
spatial_logic.py — Gestalt principles as predicates over a visual field.

Spatial relationships are a genuine logic: proximity, similarity, and
continuity are measurable properties, not heuristics. White space is a
coordinate, not absence.
"""

from __future__ import annotations

__all__ = [
    "VisualElement",
    "SpatialDistance",
    "GestaltAnalyzer",
    "VisualHierarchy",
    "NegativeSpace",
]

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 1. VisualElement
# ---------------------------------------------------------------------------

@dataclass
class VisualElement:
    """A rectangular element in a 2-D visual field."""

    element_id: str
    x: float
    y: float
    width: float
    height: float
    z_index: int = 0
    color_hue: float | None = None        # 0–360
    color_lightness: float | None = None  # 0–100
    shape_kind: str = "rect"              # rect | circle | text | image

    # -- geometric properties ------------------------------------------------

    def center_x(self) -> float:
        return self.x + self.width / 2.0

    def center_y(self) -> float:
        return self.y + self.height / 2.0

    def area(self) -> float:
        return self.width * self.height

    def aspect_ratio(self) -> float:
        if self.height == 0:
            return float("inf")
        return self.width / self.height

    def bounding_rect(self) -> tuple[float, float, float, float]:
        """Return (x, y, width, height)."""
        return (self.x, self.y, self.width, self.height)


# ---------------------------------------------------------------------------
# 2. SpatialDistance
# ---------------------------------------------------------------------------

class SpatialDistance:
    """Measures of spatial separation between VisualElements."""

    @staticmethod
    def euclidean(a: VisualElement, b: VisualElement) -> float:
        """Center-to-center Euclidean distance."""
        dx = a.center_x() - b.center_x()
        dy = a.center_y() - b.center_y()
        return math.hypot(dx, dy)

    @staticmethod
    def edge_to_edge(a: VisualElement, b: VisualElement) -> float:
        """Minimum distance between the edges of two bounding rectangles.

        Returns 0 when the rectangles overlap or touch.
        """
        # Horizontal gap: positive when b is to the right, negative when overlapping.
        h_gap = max(0.0, max(a.x, b.x) - min(a.x + a.width, b.x + b.width))
        # Vertical gap
        v_gap = max(0.0, max(a.y, b.y) - min(a.y + a.height, b.y + b.height))
        return math.hypot(h_gap, v_gap)

    @staticmethod
    def relative_proximity(
        a: VisualElement,
        b: VisualElement,
        viewport_size: float,
    ) -> float:
        """Proximity normalised to [0, 1].

        0 = touching or overlapping, 1 = maximally far apart (half the
        viewport diagonal apart, clamped).
        """
        if viewport_size <= 0:
            return 0.0
        dist = SpatialDistance.edge_to_edge(a, b)
        # Normalise against half the viewport diagonal so that two elements
        # at opposite corners score 1.0.
        max_dist = viewport_size / 2.0
        return min(dist / max_dist, 1.0)


# ---------------------------------------------------------------------------
# 3. GestaltAnalyzer
# ---------------------------------------------------------------------------

def _make_union_find(ids: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    parent = {i: i for i in ids}
    rank = {i: 0 for i in ids}
    return parent, rank


def _find(parent: dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]  # path compression
        x = parent[x]
    return x


def _union(parent: dict[str, str], rank: dict[str, int], a: str, b: str) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra == rb:
        return
    if rank[ra] < rank[rb]:
        ra, rb = rb, ra
    parent[rb] = ra
    if rank[ra] == rank[rb]:
        rank[ra] += 1


def _groups_from_union_find(
    ids: list[str], parent: dict[str, str]
) -> list[list[str]]:
    buckets: dict[str, list[str]] = {}
    for i in ids:
        root = _find(parent, i)
        buckets.setdefault(root, []).append(i)
    return list(buckets.values())


def _hue_distance(h1: float, h2: float) -> float:
    """Circular distance between two hues on [0, 360)."""
    diff = abs(h1 - h2) % 360.0
    return min(diff, 360.0 - diff)


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Graham-scan convex hull. Returns vertices in counter-clockwise order."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o: tuple, a: tuple, b: tuple) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


class GestaltAnalyzer:
    """Predicates implementing Gestalt perceptual-grouping laws."""

    # -- Proximity -----------------------------------------------------------

    def proximity_groups(
        self,
        elements: list[VisualElement],
        threshold_px: float,
    ) -> list[list[str]]:
        """Group elements whose edge-to-edge distance is below *threshold_px*.

        Uses union-find so transitively close chains collapse into one group.
        Returns a list of groups, each group being a list of element_ids.
        """
        ids = [e.element_id for e in elements]
        parent, rank = _make_union_find(ids)

        for i in range(len(elements)):
            for j in range(i + 1, len(elements)):
                dist = SpatialDistance.edge_to_edge(elements[i], elements[j])
                if dist < threshold_px:
                    _union(parent, rank, elements[i].element_id, elements[j].element_id)

        return _groups_from_union_find(ids, parent)

    # -- Similarity ----------------------------------------------------------

    def similarity_groups(
        self,
        elements: list[VisualElement],
        color_threshold: float = 30.0,
        size_threshold: float = 0.2,
    ) -> list[list[str]]:
        """Group elements that share similar hue AND similar area.

        Two elements are similar when:
        - Their hues are within *color_threshold* degrees (circular), AND
        - Their areas differ by at most *size_threshold* as a ratio of the
          larger area.

        Elements without a hue are placed in their own singleton group unless
        they also lack area variation (area check still applies).
        """
        ids = [e.element_id for e in elements]
        parent, rank = _make_union_find(ids)

        for i in range(len(elements)):
            for j in range(i + 1, len(elements)):
                a, b = elements[i], elements[j]

                # Color similarity — treat None hue as incomparable
                if a.color_hue is not None and b.color_hue is not None:
                    if _hue_distance(a.color_hue, b.color_hue) > color_threshold:
                        continue
                elif a.color_hue != b.color_hue:
                    # One has a hue and the other doesn't → not similar
                    continue

                # Size similarity
                area_a, area_b = a.area(), b.area()
                max_area = max(area_a, area_b)
                if max_area > 0:
                    ratio = abs(area_a - area_b) / max_area
                    if ratio > size_threshold:
                        continue

                _union(parent, rank, a.element_id, b.element_id)

        return _groups_from_union_find(ids, parent)

    # -- Continuity ----------------------------------------------------------

    def continuity_path(
        self,
        elements: list[VisualElement],
        direction_tolerance_deg: float = 30.0,
    ) -> list[list[str]]:
        """Find sequences of elements forming a roughly linear path.

        Strategy:
        1. Sort elements by their center_x (left-to-right reading order).
        2. For each consecutive triple (prev, curr, next), compute the angle
           between the (prev→curr) and (curr→next) vectors.
        3. If the angle change is within *direction_tolerance_deg*, curr is on
           the same path; otherwise start a new path.

        Returns a list of paths (each a list of element_ids). Isolated
        elements not forming a path of ≥ 2 are returned as singletons.
        """
        if not elements:
            return []

        tol_rad = math.radians(direction_tolerance_deg)
        sorted_els = sorted(elements, key=lambda e: (e.center_x(), e.center_y()))

        paths: list[list[str]] = []
        current_path: list[str] = [sorted_els[0].element_id]

        def _angle(e1: VisualElement, e2: VisualElement) -> float:
            return math.atan2(e2.center_y() - e1.center_y(), e2.center_x() - e1.center_x())

        def _angle_diff(a1: float, a2: float) -> float:
            diff = abs(a1 - a2)
            return min(diff, 2 * math.pi - diff)

        prev_angle: float | None = None

        for idx in range(1, len(sorted_els)):
            prev_el = sorted_els[idx - 1]
            curr_el = sorted_els[idx]
            angle = _angle(prev_el, curr_el)

            if prev_angle is None or _angle_diff(prev_angle, angle) <= tol_rad:
                current_path.append(curr_el.element_id)
                prev_angle = angle
            else:
                paths.append(current_path)
                current_path = [prev_el.element_id, curr_el.element_id]
                prev_angle = angle

        paths.append(current_path)
        return paths

    # -- Figure / Ground -----------------------------------------------------

    def figure_ground_score(
        self,
        figure: VisualElement,
        background: VisualElement,
    ) -> float:
        """Compute how strongly *figure* stands out against *background*.

        Score ∈ [0, 1] composed of three sub-signals (equal weight):

        - **Size contrast**: smaller figure relative to background → stronger.
        - **Lightness contrast**: large lightness delta → stronger.
        - **Isolation**: whether figure is well within the background bounds.
        """
        scores: list[float] = []

        # Size contrast — figure should be smaller than background.
        bg_area = background.area()
        if bg_area > 0:
            fig_fraction = figure.area() / bg_area
            # A figure covering < 25 % of the background scores full points.
            size_score = max(0.0, 1.0 - fig_fraction / 0.25)
            scores.append(min(1.0, size_score))
        else:
            scores.append(0.0)

        # Lightness contrast.
        if figure.color_lightness is not None and background.color_lightness is not None:
            delta = abs(figure.color_lightness - background.color_lightness)
            scores.append(min(delta / 100.0, 1.0))
        else:
            scores.append(0.0)

        # Isolation — figure centre should be well inside background bounds.
        bg_x1, bg_y1 = background.x, background.y
        bg_x2, bg_y2 = background.x + background.width, background.y + background.height
        margin_x = min(figure.center_x() - bg_x1, bg_x2 - figure.center_x())
        margin_y = min(figure.center_y() - bg_y1, bg_y2 - figure.center_y())
        bg_half_w = background.width / 2.0 if background.width > 0 else 1.0
        bg_half_h = background.height / 2.0 if background.height > 0 else 1.0
        isolation = min(margin_x / bg_half_w, margin_y / bg_half_h)
        scores.append(max(0.0, min(1.0, isolation)))

        return sum(scores) / len(scores)

    # -- Closure / Convex Hull -----------------------------------------------

    def closure_hull(
        self,
        groups: list[list[str]],
        all_elements: dict[str, VisualElement],
    ) -> list[list[tuple[float, float]]]:
        """Compute the convex hull of each group's bounding-rect corners.

        Returns one hull polygon per group (list of (x, y) vertices in CCW
        order). Groups whose elements are all missing from *all_elements* yield
        an empty list.
        """
        hulls: list[list[tuple[float, float]]] = []
        for group in groups:
            points: list[tuple[float, float]] = []
            for eid in group:
                el = all_elements.get(eid)
                if el is None:
                    continue
                x, y, w, h = el.bounding_rect()
                points.extend([
                    (x, y),
                    (x + w, y),
                    (x + w, y + h),
                    (x, y + h),
                ])
            hulls.append(_convex_hull(points))
        return hulls


# ---------------------------------------------------------------------------
# 4. VisualHierarchy
# ---------------------------------------------------------------------------

_GOLDEN = (1.0 + math.sqrt(5)) / 2.0  # ≈ 1.618
_INV_GOLDEN = 1.0 / _GOLDEN            # ≈ 0.618


class VisualHierarchy:
    """Tools for computing attentional weight and reading order."""

    @staticmethod
    def attention_weight(
        element: VisualElement,
        viewport_width: float,
        viewport_height: float,
    ) -> float:
        """Composite attentional weight ∈ [0, ∞).

        Four additive signals (each normalised to roughly [0, 1]):

        1. **Size**: fraction of viewport area occupied.
        2. **Lightness contrast**: distance from a 50 % mid-grey.
        3. **Position**: proximity to the upper-centre focal region.
        4. **Isolation** (placeholder — requires neighbour list; here we use
           z_index as a proxy: higher z → more isolated / emphasised).
        """
        vp_area = viewport_width * viewport_height
        if vp_area <= 0:
            return 0.0

        # 1. Size weight
        size_w = element.area() / vp_area

        # 2. Contrast weight
        if element.color_lightness is not None:
            contrast_w = abs(element.color_lightness - 50.0) / 50.0
        else:
            contrast_w = 0.0

        # 3. Position weight — peak at (viewport_width/2, viewport_height*0.25)
        focal_x = viewport_width / 2.0
        focal_y = viewport_height * 0.25
        dist_to_focal = math.hypot(
            element.center_x() - focal_x,
            element.center_y() - focal_y,
        )
        max_dist = math.hypot(viewport_width, viewport_height)
        position_w = 1.0 - min(dist_to_focal / max_dist, 1.0)

        # 4. Z-index proxy for isolation
        z_w = element.z_index / 10.0  # normalise loosely

        return size_w + contrast_w + position_w + z_w

    @staticmethod
    def hierarchy_order(
        elements: list[VisualElement],
        viewport_width: float,
        viewport_height: float,
    ) -> list[str]:
        """Return element_ids sorted by attention_weight, highest first."""
        scored = [
            (
                VisualHierarchy.attention_weight(e, viewport_width, viewport_height),
                e.element_id,
            )
            for e in elements
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [eid for _, eid in scored]

    @staticmethod
    def golden_ratio_zones(
        viewport_width: float,
        viewport_height: float,
    ) -> dict[str, tuple[float, float, float, float]]:
        """Named zones based on golden-ratio intersections.

        Returns a mapping from zone name to (x, y, width, height).

        Zones:
        - ``"primary_focal"``  — small region around the primary golden-ratio
          intersection (upper-left rule-of-thirds equivalent, but φ-based).
        - ``"secondary_focal"`` — lower-right counter-point.
        - ``"center"``          — central region (middle third × middle third).
        - ``"margins"``         — band around the outer edge.
        """
        w, h = viewport_width, viewport_height

        # Golden-ratio intersections
        gx = w * _INV_GOLDEN   # ≈ 0.618 * w
        gy = h * _INV_GOLDEN

        focal_size_x = w * 0.1
        focal_size_y = h * 0.1

        primary_focal = (
            gx - focal_size_x / 2,
            gy - focal_size_y / 2 - h * _INV_GOLDEN / 2,  # upper intersection
            focal_size_x,
            focal_size_y,
        )

        secondary_focal = (
            w - gx - focal_size_x / 2,
            h - gy - focal_size_y / 2 + h * (1 - _INV_GOLDEN) / 2,
            focal_size_x,
            focal_size_y,
        )

        # Central third
        center = (w / 3.0, h / 3.0, w / 3.0, h / 3.0)

        # Margin band — outer 10 %
        margin = min(w, h) * 0.10
        margins = (0.0, 0.0, w, margin)  # top margin as representative strip

        return {
            "primary_focal": primary_focal,
            "secondary_focal": secondary_focal,
            "center": center,
            "margins": margins,
        }


# ---------------------------------------------------------------------------
# 5. NegativeSpace
# ---------------------------------------------------------------------------

class NegativeSpace:
    """Formalise white space as a measurable coordinate."""

    @staticmethod
    def gaps_between(
        elements: list[VisualElement],
        viewport_width: float,
        viewport_height: float,
        min_gap_px: float = 10.0,
    ) -> list[tuple[float, float, float, float]]:
        """Discover rectangular regions of empty space in the viewport.

        Approach:
        - Scan horizontal *rows* (bands of height ``min_gap_px``) for columns
          that are entirely unoccupied by any element.
        - Merge adjacent empty bands into gap rectangles.

        Returns a list of (x, y, width, height) rectangles.
        """
        if not elements or viewport_width <= 0 or viewport_height <= 0:
            return [(0.0, 0.0, viewport_width, viewport_height)]

        band = max(min_gap_px, 1.0)
        gaps: list[tuple[float, float, float, float]] = []

        y = 0.0
        while y < viewport_height:
            row_bottom = min(y + band, viewport_height)
            # Which elements overlap this horizontal band?
            row_els = [
                e for e in elements
                if e.y < row_bottom and e.y + e.height > y
            ]

            if not row_els:
                # Entire row is empty — record full-width gap band.
                gaps.append((0.0, y, viewport_width, row_bottom - y))
                y = row_bottom
                continue

            # Find the covered x-intervals within this row.
            intervals: list[tuple[float, float]] = sorted(
                (max(0.0, e.x), min(viewport_width, e.x + e.width))
                for e in row_els
            )

            # Merge overlapping intervals, then find gaps.
            merged: list[tuple[float, float]] = []
            for start, end in intervals:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            # Empty region before first element.
            if merged[0][0] > min_gap_px:
                gaps.append((0.0, y, merged[0][0], row_bottom - y))

            # Gaps between elements.
            for k in range(len(merged) - 1):
                gap_start = merged[k][1]
                gap_end = merged[k + 1][0]
                if gap_end - gap_start >= min_gap_px:
                    gaps.append((gap_start, y, gap_end - gap_start, row_bottom - y))

            # Empty region after last element.
            if viewport_width - merged[-1][1] > min_gap_px:
                gaps.append((merged[-1][1], y, viewport_width - merged[-1][1], row_bottom - y))

            y = row_bottom

        # Merge vertically adjacent, same-x gaps (simple coalesce pass).
        return _coalesce_vertical_gaps(gaps)

    @staticmethod
    def breathing_room(
        element: VisualElement,
        neighbors: list[VisualElement],
    ) -> float:
        """Minimum edge-to-edge distance to any neighbour.

        Returns ``float('inf')`` when *neighbors* is empty (perfect isolation).
        This value represents the emphasis/isolation of *element*.
        """
        if not neighbors:
            return float("inf")
        return min(
            SpatialDistance.edge_to_edge(element, n)
            for n in neighbors
        )


def _coalesce_vertical_gaps(
    gaps: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Merge vertically adjacent gap rectangles with the same x and width."""
    if not gaps:
        return []

    # Sort by (x, y) so adjacent rows with matching x columns are contiguous.
    sorted_gaps = sorted(gaps, key=lambda g: (g[0], g[1]))
    merged: list[tuple[float, float, float, float]] = [sorted_gaps[0]]

    for gx, gy, gw, gh in sorted_gaps[1:]:
        px, py, pw, ph = merged[-1]
        # Merge if same x/width and vertically adjacent (within 1 px).
        if abs(gx - px) < 1e-6 and abs(gw - pw) < 1e-6 and abs((py + ph) - gy) < 1.0:
            merged[-1] = (px, py, pw, ph + gh)
        else:
            merged.append((gx, gy, gw, gh))

    return merged
