"""CSS Flexbox layout algorithm implemented as constraint satisfaction.

Key insight: during flex-shrink distribution, each item's shrink factor is
``flex-shrink × flex-basis``, not just ``flex-shrink``.  A larger item
contributes proportionally more to the total shrinkage even when all items
share the same ``flex-shrink`` scalar.  This matches the CSS specification
(§9.7.3) and is frequently misunderstood.
"""

from __future__ import annotations

__all__ = [
    "FlexDirection",
    "FlexWrap",
    "AlignValue",
    "FlexItem",
    "FlexContainer",
    "FlexLayoutResult",
    "FlexboxSolver",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Union


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FlexDirection(str, Enum):
    ROW = "row"
    ROW_REVERSE = "row-reverse"
    COLUMN = "column"
    COLUMN_REVERSE = "column-reverse"

    def is_row(self) -> bool:
        return self in (FlexDirection.ROW, FlexDirection.ROW_REVERSE)

    def is_reverse(self) -> bool:
        return self in (FlexDirection.ROW_REVERSE, FlexDirection.COLUMN_REVERSE)


class FlexWrap(str, Enum):
    NOWRAP = "nowrap"
    WRAP = "wrap"
    WRAP_REVERSE = "wrap-reverse"


class AlignValue(str, Enum):
    FLEX_START = "flex-start"
    FLEX_END = "flex-end"
    CENTER = "center"
    STRETCH = "stretch"
    BASELINE = "baseline"
    SPACE_BETWEEN = "space-between"
    SPACE_AROUND = "space-around"
    SPACE_EVENLY = "space-evenly"
    NORMAL = "normal"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FlexItem:
    """One item participating in a flex layout."""

    item_id: str
    flex_grow: float = 0.0
    flex_shrink: float = 1.0
    flex_basis: Union[float, str] = "auto"
    min_size: float = 0.0
    max_size: Union[float, None] = None
    align_self: AlignValue = AlignValue.NORMAL
    order: int = 0

    def hypothetical_main_size(self, container_main_size: float) -> float:
        """Resolve ``flex-basis`` to a concrete pixel value.

        * A numeric basis is used directly as the hypothetical main size.
        * ``"auto"`` and ``"content"`` fall back to 0 px (no intrinsic size
          information is available at this layer; callers may subclass and
          override).
        * ``"min-content"`` resolves to ``min_size``.
        * ``"max-content"`` resolves to ``max_size`` when set, otherwise the
          full ``container_main_size``.

        The result is clamped to ``[min_size, max_size]``.
        """
        if isinstance(self.flex_basis, (int, float)):
            size = float(self.flex_basis)
        elif self.flex_basis == "min-content":
            size = self.min_size
        elif self.flex_basis == "max-content":
            size = self.max_size if self.max_size is not None else container_main_size
        else:
            # "auto" | "content" — treat as 0 when no intrinsic size is known
            size = 0.0

        size = max(size, self.min_size)
        if self.max_size is not None:
            size = min(size, self.max_size)
        return size


@dataclass
class FlexContainer:
    """Configuration for a flex container."""

    container_id: str
    main_size: float
    cross_size: Union[float, None]
    direction: FlexDirection
    wrap: FlexWrap
    justify_content: AlignValue
    align_items: AlignValue
    align_content: AlignValue
    gap_main: float = 0.0
    gap_cross: float = 0.0


@dataclass
class FlexLayoutResult:
    """Resolved geometry for a single flex item."""

    item_id: str
    main_size: float
    cross_size: float
    main_offset: float
    cross_offset: float


# ---------------------------------------------------------------------------
# Solver internals
# ---------------------------------------------------------------------------

def _resolve_grow(
    items: list[FlexItem],
    sizes: list[float],
    free_space: float,
) -> list[float]:
    """Distribute positive free space via ``flex-grow``.

    Returns an updated copy of *sizes*.  Items are grown proportionally to
    their ``flex-grow`` value.  Items that would exceed ``max_size`` are
    clamped and their leftover is re-distributed in subsequent passes.
    """
    sizes = list(sizes)
    remaining = free_space
    active = list(range(len(items)))

    while remaining > 1e-9 and active:
        total_grow = sum(items[i].flex_grow for i in active)
        if total_grow <= 0:
            break

        next_active: list[int] = []
        distributed = 0.0
        for i in active:
            item = items[i]
            delta = remaining * (item.flex_grow / total_grow)
            new_size = sizes[i] + delta
            if item.max_size is not None and new_size > item.max_size:
                distributed += item.max_size - sizes[i]
                sizes[i] = item.max_size
            else:
                sizes[i] = new_size
                distributed += delta
                next_active.append(i)

        if not next_active:
            break
        remaining -= distributed
        active = next_active

    return sizes


def _resolve_shrink(
    items: list[FlexItem],
    sizes: list[float],
    free_space: float,
) -> list[float]:
    """Distribute negative free space via ``flex-shrink × flex-basis``.

    The CSS specification (§9.7.3) weights shrinkage by the *product* of
    ``flex-shrink`` and the item's **flex-basis** (not just ``flex-shrink``).
    Items that would shrink below ``min_size`` are clamped and the remainder
    is re-distributed in subsequent passes.
    """
    sizes = list(sizes)
    overflow = -free_space  # positive quantity to remove
    active = list(range(len(items)))

    while overflow > 1e-9 and active:
        # Weighted shrink factors: flex-shrink * flex-basis
        weights = [
            items[i].flex_shrink * sizes[i] for i in active
        ]
        total_weight = sum(weights)
        if total_weight <= 0:
            break

        next_active: list[int] = []
        removed = 0.0
        for idx, i in enumerate(active):
            if total_weight > 0:
                ratio = weights[idx] / total_weight
            else:
                ratio = 0.0
            delta = overflow * ratio
            new_size = sizes[i] - delta
            if new_size < items[i].min_size:
                removed += sizes[i] - items[i].min_size
                sizes[i] = items[i].min_size
            else:
                sizes[i] = new_size
                removed += delta
                next_active.append(i)

        if not next_active:
            break
        overflow -= removed
        active = next_active

    return sizes


def _gap_count(n: int) -> int:
    """Number of gaps between *n* items."""
    return max(n - 1, 0)


def _justify_offsets(
    n: int,
    sizes: list[float],
    main_size: float,
    gap: float,
    justify: AlignValue,
    reverse: bool,
) -> list[float]:
    """Compute main-axis offsets for *n* items given a justify-content value."""
    total_items_size = sum(sizes)
    total_gap = gap * _gap_count(n)
    free = main_size - total_items_size - total_gap

    offsets: list[float] = []

    if justify == AlignValue.FLEX_END:
        start = free
        pos = start
        for s in sizes:
            offsets.append(pos)
            pos += s + gap

    elif justify == AlignValue.CENTER:
        pos = free / 2.0
        for s in sizes:
            offsets.append(pos)
            pos += s + gap

    elif justify == AlignValue.SPACE_BETWEEN:
        if n <= 1:
            offsets = [0.0] * n
        else:
            between = free / (n - 1)
            pos = 0.0
            for s in sizes:
                offsets.append(pos)
                pos += s + gap + between

    elif justify == AlignValue.SPACE_AROUND:
        around = free / n if n > 0 else 0.0
        pos = around / 2.0
        for s in sizes:
            offsets.append(pos)
            pos += s + gap + around

    elif justify == AlignValue.SPACE_EVENLY:
        evenly = free / (n + 1) if n > 0 else 0.0
        pos = evenly
        for s in sizes:
            offsets.append(pos)
            pos += s + gap + evenly

    else:
        # FLEX_START / NORMAL / STRETCH / fallback
        pos = 0.0
        for s in sizes:
            offsets.append(pos)
            pos += s + gap

    if reverse:
        # Mirror: offset_i = main_size - offset_i - size_i
        offsets = [main_size - offsets[i] - sizes[i] for i in range(n)]

    return offsets


def _cross_offset(
    item_cross: float,
    line_cross: float,
    align: AlignValue,
    reverse: bool,
) -> float:
    """Return the cross-axis offset within a line of height *line_cross*."""
    if align in (AlignValue.STRETCH, AlignValue.FLEX_START, AlignValue.NORMAL):
        base = 0.0
    elif align == AlignValue.FLEX_END:
        base = line_cross - item_cross
    elif align in (AlignValue.CENTER, AlignValue.BASELINE):
        base = (line_cross - item_cross) / 2.0
    else:
        base = 0.0

    if reverse:
        base = line_cross - base - item_cross
    return base


# ---------------------------------------------------------------------------
# Main solver
# ---------------------------------------------------------------------------

class FlexboxSolver:
    """Implements the CSS Flexbox layout algorithm as constraint satisfaction.

    The solver follows the algorithm described in the CSS Flexible Box Layout
    Module Level 1 specification.  Key steps:

    1. Sort items by ``order``.
    2. Resolve each item's hypothetical main size from ``flex-basis``.
    3. Compute free space (container minus items minus gaps).
    4. Grow items (``flex-grow``) when free space is positive.
    5. Shrink items (``flex-shrink × flex-basis``) when free space is negative.
    6. Clamp to ``[min_size, max_size]`` with re-distribution.
    7. Assign cross sizes (STRETCH fills the cross axis).
    8. Compute main-axis offsets from ``justify-content``.
    9. Compute cross-axis offsets from ``align-items`` / ``align-self``.
    """

    def solve(
        self,
        container: FlexContainer,
        items: list[FlexItem],
    ) -> list[FlexLayoutResult]:
        """Return ``FlexLayoutResult`` for every item in *items*.

        Results are returned in visual order (i.e. after applying ``order``
        sorting), not in source order.
        """
        if not items:
            return []

        # ------------------------------------------------------------------
        # Step 1 — sort by order, then source index for stability
        # ------------------------------------------------------------------
        indexed = sorted(enumerate(items), key=lambda t: (t[1].order, t[0]))
        sorted_items = [item for _, item in indexed]

        # ------------------------------------------------------------------
        # Step 2 — hypothetical main sizes
        # ------------------------------------------------------------------
        hyp = [item.hypothetical_main_size(container.main_size) for item in sorted_items]

        # ------------------------------------------------------------------
        # Wrap: partition items into lines
        # ------------------------------------------------------------------
        lines = _partition_lines(sorted_items, hyp, container)

        # ------------------------------------------------------------------
        # Per-line layout
        # ------------------------------------------------------------------
        cross_size_per_line: list[float] = []
        for line_items, line_hyp in lines:
            cross_size_per_line.append(
                _compute_line_cross(line_items, container)
            )

        # Determine container cross size
        if container.cross_size is not None:
            total_cross = container.cross_size
        else:
            total_cross = (
                sum(cross_size_per_line)
                + container.gap_cross * _gap_count(len(lines))
            )

        # align-content distributes lines across the cross axis
        line_cross_offsets = _align_content_offsets(
            cross_size_per_line, total_cross, container.gap_cross,
            container.align_content,
            container.wrap == FlexWrap.WRAP_REVERSE,
        )

        # ------------------------------------------------------------------
        # Build results
        # ------------------------------------------------------------------
        results: list[FlexLayoutResult] = []
        for line_idx, (line_items, line_hyp) in enumerate(lines):
            n = len(line_items)
            sizes = _flex_resolve(line_items, line_hyp, container)

            is_reverse = container.direction.is_reverse()
            main_offsets = _justify_offsets(
                n, sizes, container.main_size, container.gap_main,
                container.justify_content, is_reverse,
            )

            line_cross = cross_size_per_line[line_idx]
            line_cross_base = line_cross_offsets[line_idx]

            cross_reverse = container.wrap == FlexWrap.WRAP_REVERSE

            for j, item in enumerate(line_items):
                effective_align = (
                    item.align_self
                    if item.align_self != AlignValue.NORMAL
                    else container.align_items
                )
                item_cross = (
                    line_cross
                    if effective_align == AlignValue.STRETCH
                    else _intrinsic_cross(item, container)
                )
                c_offset = _cross_offset(
                    item_cross, line_cross, effective_align, cross_reverse
                )
                results.append(FlexLayoutResult(
                    item_id=item.item_id,
                    main_size=sizes[j],
                    cross_size=item_cross,
                    main_offset=main_offsets[j],
                    cross_offset=line_cross_base + c_offset,
                ))

        return results


# ---------------------------------------------------------------------------
# Helpers used by the solver
# ---------------------------------------------------------------------------

def _partition_lines(
    items: list[FlexItem],
    hyp: list[float],
    container: FlexContainer,
) -> list[tuple[list[FlexItem], list[float]]]:
    """Partition *items* into flex lines according to the wrap setting."""
    if container.wrap == FlexWrap.NOWRAP:
        return [(items, hyp)]

    lines: list[tuple[list[FlexItem], list[float]]] = []
    line_items: list[FlexItem] = []
    line_hyp: list[float] = []
    line_used = 0.0

    for item, h in zip(items, hyp):
        gap = container.gap_main if line_items else 0.0
        if line_items and line_used + gap + h > container.main_size + 1e-9:
            lines.append((line_items, line_hyp))
            line_items = [item]
            line_hyp = [h]
            line_used = h
        else:
            line_items.append(item)
            line_hyp.append(h)
            line_used += gap + h

    if line_items:
        lines.append((line_items, line_hyp))

    return lines


def _flex_resolve(
    items: list[FlexItem],
    hyp: list[float],
    container: FlexContainer,
) -> list[float]:
    """Run grow/shrink algorithm for one line, returning final main sizes."""
    n = len(items)
    gap_total = container.gap_main * _gap_count(n)
    free = container.main_size - sum(hyp) - gap_total

    sizes = list(hyp)
    if free > 1e-9:
        has_grow = any(item.flex_grow > 0 for item in items)
        if has_grow:
            sizes = _resolve_grow(items, sizes, free)
    elif free < -1e-9:
        has_shrink = any(item.flex_shrink > 0 for item in items)
        if has_shrink:
            sizes = _resolve_shrink(items, sizes, free)

    # Final clamp pass (handles cases where grow/shrink passes were skipped)
    for i, item in enumerate(items):
        sizes[i] = max(sizes[i], item.min_size)
        if item.max_size is not None:
            sizes[i] = min(sizes[i], item.max_size)

    return sizes


def _intrinsic_cross(item: FlexItem, container: FlexContainer) -> float:
    """Best-effort intrinsic cross size for an item (0 when unknown)."""
    # Without a rendering engine we have no intrinsic cross dimension.
    # Return 0 so callers that don't use STRETCH still produce valid geometry.
    return 0.0


def _compute_line_cross(
    items: list[FlexItem],
    container: FlexContainer,
) -> float:
    """Cross size of a single flex line."""
    if container.cross_size is not None:
        # Single-line containers with a fixed cross size use the full cross.
        return container.cross_size

    # Multi-line: the line height is the maximum item cross size.
    crosses = [_intrinsic_cross(item, container) for item in items]
    return max(crosses) if crosses else 0.0


def _align_content_offsets(
    line_sizes: list[float],
    total_cross: float,
    gap: float,
    align: AlignValue,
    reverse: bool,
) -> list[float]:
    """Compute per-line cross-axis start offsets for ``align-content``."""
    n = len(line_sizes)
    if n == 0:
        return []

    total_lines_size = sum(line_sizes)
    total_gap = gap * _gap_count(n)
    free = total_cross - total_lines_size - total_gap

    offsets: list[float] = []

    if align == AlignValue.FLEX_END:
        pos = free
        for s in line_sizes:
            offsets.append(pos)
            pos += s + gap

    elif align == AlignValue.CENTER:
        pos = free / 2.0
        for s in line_sizes:
            offsets.append(pos)
            pos += s + gap

    elif align == AlignValue.SPACE_BETWEEN:
        if n <= 1:
            offsets = [0.0] * n
        else:
            between = free / (n - 1)
            pos = 0.0
            for s in line_sizes:
                offsets.append(pos)
                pos += s + gap + between

    elif align == AlignValue.SPACE_AROUND:
        around = free / n if n > 0 else 0.0
        pos = around / 2.0
        for s in line_sizes:
            offsets.append(pos)
            pos += s + gap + around

    elif align == AlignValue.SPACE_EVENLY:
        evenly = free / (n + 1) if n > 0 else 0.0
        pos = evenly
        for s in line_sizes:
            offsets.append(pos)
            pos += s + gap + evenly

    elif align == AlignValue.STRETCH:
        # Distribute free space equally among lines
        extra = free / n if n > 0 else 0.0
        pos = 0.0
        for s in line_sizes:
            offsets.append(pos)
            pos += s + extra + gap

    else:
        # FLEX_START / NORMAL / BASELINE / fallback
        pos = 0.0
        for s in line_sizes:
            offsets.append(pos)
            pos += s + gap

    if reverse:
        offsets = [total_cross - offsets[i] - line_sizes[i] for i in range(n)]

    return offsets
