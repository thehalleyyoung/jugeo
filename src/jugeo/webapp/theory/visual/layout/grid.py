"""CSS Grid track sizing as formal constraint satisfaction.

Grid tracks (rows/columns) are coordinates in the layout site; named areas are
covering families over those coordinates.

The central constraint is the CSS Grid Track Sizing Algorithm (CSS Grid Level 1
§11).  fr units are resolved *last*: non-flexible tracks claim their intrinsic
or fixed sizes first; only the leftover space is proportionally distributed
among fr tracks.  This mirrors the spec's "maximize non-flexible tracks, then
expand flexible tracks" ordering.
"""

from __future__ import annotations

__all__ = [
    "GridTrackKind",
    "GridTrackSize",
    "GridPlacement",
    "CSSGrid",
    "GridTrackSolver",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 1. GridTrackKind
# ---------------------------------------------------------------------------

class GridTrackKind(str, Enum):
    """Classifies a grid track by how it was introduced and its axis."""

    EXPLICIT_COLUMN = "explicit-column"
    EXPLICIT_ROW = "explicit-row"
    IMPLICIT_COLUMN = "implicit-column"
    IMPLICIT_ROW = "implicit-row"
    SUBGRID_COLUMN = "subgrid-column"
    SUBGRID_ROW = "subgrid-row"


# ---------------------------------------------------------------------------
# 2. GridTrackSize
# ---------------------------------------------------------------------------

_LENGTH_RE = re.compile(
    r"^\d+(\.\d+)?(px|fr|%|em|rem|vw|vh|vmin|vmax|ch|ex|cm|mm|in|pt|pc)$"
)
_VALID_KEYWORDS = {"auto", "min-content", "max-content"}


def _valid_size(value: str) -> bool:
    return value in _VALID_KEYWORDS or bool(_LENGTH_RE.match(value))


@dataclass
class GridTrackSize:
    """A CSS track-sizing function expressed as minmax(min_size, max_size).

    Both *min_size* and *max_size* accept:
    - keywords: ``"auto"``, ``"min-content"``, ``"max-content"``
    - length values: ``"100px"``, ``"1fr"``, ``"10%"``

    The ``fr`` unit is only valid as a *max_size*; it represents a flexible
    fraction of the remaining free space after all non-flexible tracks are
    resolved.
    """

    min_size: str
    max_size: str

    def __post_init__(self) -> None:
        if not _valid_size(self.min_size):
            raise ValueError(f"Invalid min_size: {self.min_size!r}")
        if not _valid_size(self.max_size):
            raise ValueError(f"Invalid max_size: {self.max_size!r}")

    # ------------------------------------------------------------------
    # Classmethods
    # ------------------------------------------------------------------

    @classmethod
    def fixed(cls, px: float) -> GridTrackSize:
        """minmax(Npx, Npx) — a track with an exact pixel size."""
        size = f"{px}px"
        return cls(min_size=size, max_size=size)

    @classmethod
    def fr(cls, value: float) -> GridTrackSize:
        """minmax(auto, Nfr) — a flexible track that takes a fractional share
        of the remaining free space."""
        return cls(min_size="auto", max_size=f"{value}fr")

    @classmethod
    def auto(cls) -> GridTrackSize:
        """minmax(auto, auto)."""
        return cls(min_size="auto", max_size="auto")

    @classmethod
    def min_content(cls) -> GridTrackSize:
        """minmax(min-content, min-content)."""
        return cls(min_size="min-content", max_size="min-content")

    @classmethod
    def max_content(cls) -> GridTrackSize:
        """minmax(max-content, max-content)."""
        return cls(min_size="max-content", max_size="max-content")

    @classmethod
    def fit_content(cls, limit: float) -> GridTrackSize:
        """min(max-content, max(min-content, limit)).

        Represented as minmax(min-content, <limit>px); the caller is
        responsible for interpreting the limit semantics during sizing.
        """
        return cls(min_size="min-content", max_size=f"{limit}px")

    # ------------------------------------------------------------------
    # Instance helpers
    # ------------------------------------------------------------------

    def is_flexible(self) -> bool:
        """Return True when *max_size* is a ``fr`` value."""
        return self.max_size.endswith("fr")

    def fr_value(self) -> float:
        """Return the numeric fr coefficient, or 0 if the track is not flexible."""
        if self.is_flexible():
            return float(self.max_size[:-2])
        return 0.0

    def _parse_px(self, size: str) -> Optional[float]:
        """Return the pixel value of a size string, or None if not a fixed px."""
        if size.endswith("px"):
            try:
                return float(size[:-2])
            except ValueError:
                return None
        return None

    def base_size(self) -> float:
        """Initial base size: fixed tracks start at their fixed px value; others 0."""
        px = self._parse_px(self.min_size)
        if px is not None:
            return px
        return 0.0

    def growth_limit(self) -> Optional[float]:
        """Growth limit for the max sizing function.

        Returns the px value if *max_size* is a fixed length, or ``None``
        for keywords / fr units (treated as unbounded).
        """
        return self._parse_px(self.max_size)

    def is_intrinsic(self) -> bool:
        """True when the max sizing function is keyword-based (not fixed / fr)."""
        return self.max_size in _VALID_KEYWORDS

    def is_fixed(self) -> bool:
        """True when both min and max are the same fixed px value."""
        if self.min_size == self.max_size:
            return self._parse_px(self.min_size) is not None
        return False


# ---------------------------------------------------------------------------
# 3. GridPlacement
# ---------------------------------------------------------------------------

@dataclass
class GridPlacement:
    """Describes where a grid item is placed on the grid.

    Line numbers are 1-based positive integers (or named lines as strings).
    When *col_end* / *row_end* is ``"auto"`` the corresponding span is used.
    """

    item_id: str
    col_start: int | str
    col_end: int | str
    row_start: int | str
    row_end: int | str
    col_span: int = 1
    row_span: int = 1

    def resolved_col_span(self) -> int:
        """Compute the column span from explicit end line or fall back to col_span."""
        if isinstance(self.col_start, int) and isinstance(self.col_end, int):
            span = self.col_end - self.col_start
            return span if span > 0 else self.col_span
        return self.col_span

    def resolved_row_span(self) -> int:
        """Compute the row span from explicit end line or fall back to row_span."""
        if isinstance(self.row_start, int) and isinstance(self.row_end, int):
            span = self.row_end - self.row_start
            return span if span > 0 else self.row_span
        return self.row_span


# ---------------------------------------------------------------------------
# 4. CSSGrid
# ---------------------------------------------------------------------------

@dataclass
class CSSGrid:
    """A CSS Grid container definition.

    *named_areas* maps an area name to a 4-tuple
    ``(row_start, col_start, row_end, col_end)`` using 1-based inclusive line
    numbers (exclusive end — same convention as CSS).
    """

    container_id: str
    column_tracks: list[GridTrackSize]
    row_tracks: list[GridTrackSize]
    column_gap: float = 0.0
    row_gap: float = 0.0
    named_areas: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    auto_flow: str = "row"

    def __post_init__(self) -> None:
        valid_flows = {"row", "column", "row dense", "column dense"}
        if self.auto_flow not in valid_flows:
            raise ValueError(
                f"auto_flow must be one of {valid_flows!r}, got {self.auto_flow!r}"
            )

    @property
    def num_columns(self) -> int:
        return len(self.column_tracks)

    @property
    def num_rows(self) -> int:
        return len(self.row_tracks)


# ---------------------------------------------------------------------------
# 5. GridTrackSolver
# ---------------------------------------------------------------------------

class GridTrackSolver:
    """Implements the CSS Grid Track Sizing Algorithm (CSS Grid Level 1 §11).

    Algorithm overview
    ------------------
    The algorithm runs in five steps, mirroring the spec:

    1. **Initialise base sizes** — fixed tracks (``Npx``) start at their
       declared size.  All other tracks start at 0.

    2. **Resolve intrinsic minimum contributions** — for tracks sized with
       ``min-content``, ``max-content``, or ``auto`` minimums, set the base
       size to the maximum of the current base size and the smallest
       contribution from any item in that track.

    3. **Resolve intrinsic maximum contributions** — similarly for the growth
       limit dimension.

    4. **Maximise non-flexible tracks** — distribute free space (available
       minus fixed-track totals and gaps) to auto / intrinsic tracks up to
       their growth limits, growing them as much as possible.

    5. **Expand flexible tracks** — *only after* all non-flexible tracks have
       claimed their space, the remaining free space is divided proportionally
       among ``fr`` tracks.  One ``fr`` equals ``remaining / total_fr_units``,
       subject to each track's base-size floor.

    **Why fr is last**: The spec intentionally resolves fr units after
    intrinsic tracks so that content-driven sizes are honoured first and the
    flexible tracks absorb the *leftover* space.  If fr tracks were resolved
    first, a large item in an auto track could exceed the available space.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def solve_tracks(
        self,
        tracks: list[GridTrackSize],
        available_space: float,
        items_per_track: dict[int, list[float]],
    ) -> list[float]:
        """Run the track-sizing algorithm and return resolved pixel sizes.

        Parameters
        ----------
        tracks:
            Ordered list of track sizing functions.
        available_space:
            Total pixel width (or height) available to the grid container,
            *excluding* gaps (the caller should subtract gap totals before
            passing this value, or pass the gross container size and let the
            solver account for gaps via *items_per_track*).
        items_per_track:
            Mapping from 0-based track index to a list of item contributions
            (floats) in that track.  Used for intrinsic sizing.  Tracks
            absent from the mapping are treated as having no items.

        Returns
        -------
        list[float]
            One resolved size per track, in input order.
        """
        n = len(tracks)
        if n == 0:
            return []

        # Step 1 — initialise base sizes and growth limits
        base_sizes: list[float] = []
        growth_limits: list[float] = []
        for track in tracks:
            bs = track.base_size()
            gl = track.growth_limit()
            base_sizes.append(bs)
            # Growth limit of None means "unbounded"; use infinity internally.
            growth_limits.append(gl if gl is not None else float("inf"))

        # Step 2 — resolve intrinsic minimums (min-content / auto min)
        for idx, track in enumerate(tracks):
            if track.min_size in ("min-content", "auto"):
                items = items_per_track.get(idx, [])
                if items:
                    min_contribution = min(items)
                    base_sizes[idx] = max(base_sizes[idx], min_contribution)

        # Step 3 — resolve intrinsic maximums (max-content / auto max)
        for idx, track in enumerate(tracks):
            if track.max_size in ("max-content", "auto"):
                items = items_per_track.get(idx, [])
                if items:
                    max_contribution = max(items)
                    growth_limits[idx] = max(growth_limits[idx], max_contribution)
                    # Base size cannot exceed growth limit
                    base_sizes[idx] = min(base_sizes[idx], growth_limits[idx])

        # Step 4 — maximise non-flexible tracks
        flexible_indices = [i for i, t in enumerate(tracks) if t.is_flexible()]
        non_flexible_indices = [i for i in range(n) if i not in flexible_indices]

        # Space consumed by non-flexible tracks so far
        used_by_non_flex = sum(base_sizes[i] for i in non_flexible_indices)
        flex_base_total = sum(base_sizes[i] for i in flexible_indices)
        free_for_non_flex = available_space - used_by_non_flex - flex_base_total

        if free_for_non_flex > 0:
            # Identify auto / intrinsic non-flexible tracks that can grow
            growable = [
                i for i in non_flexible_indices if tracks[i].is_intrinsic()
            ]
            if growable:
                # Distribute equally up to growth limits
                remaining = free_for_non_flex
                # Iterative distribution: keep growing tracks that still have
                # room until space is exhausted or all tracks hit their limits.
                for _ in range(n + 1):  # at most n passes
                    if remaining <= 0 or not growable:
                        break
                    share = remaining / len(growable)
                    still_growable = []
                    gained = 0.0
                    for i in growable:
                        headroom = growth_limits[i] - base_sizes[i]
                        if headroom <= 0:
                            continue
                        grow_by = min(share, headroom)
                        base_sizes[i] += grow_by
                        gained += grow_by
                        if base_sizes[i] < growth_limits[i]:
                            still_growable.append(i)
                    remaining -= gained
                    growable = still_growable

        # Step 5 — expand flexible tracks
        # Recalculate space consumed by non-flexible tracks after step 4.
        used_non_flex = sum(base_sizes[i] for i in non_flexible_indices)
        free_for_flex = available_space - used_non_flex

        if flexible_indices and free_for_flex > 0:
            total_fr = sum(tracks[i].fr_value() for i in flexible_indices)
            if total_fr > 0:
                fr_unit = free_for_flex / total_fr
                for i in flexible_indices:
                    base_sizes[i] = max(
                        base_sizes[i],
                        tracks[i].fr_value() * fr_unit,
                    )

                # Clamp so that total does not exceed available_space due to
                # floating-point drift.
                total_flex = sum(base_sizes[i] for i in flexible_indices)
                if total_flex > free_for_flex:
                    scale = free_for_flex / total_flex
                    for i in flexible_indices:
                        base_sizes[i] *= scale

        return base_sizes

    # ------------------------------------------------------------------

    def place_items_auto(
        self,
        grid: CSSGrid,
        item_ids: list[str],
        explicit_placements: list[GridPlacement],
    ) -> list[GridPlacement]:
        """Auto-placement algorithm (CSS Grid §8.5).

        Items with explicit placements are honoured first; remaining items are
        flowed into the next available cell according to ``grid.auto_flow``.

        Parameters
        ----------
        grid:
            The grid container definition (track counts, auto_flow direction).
        item_ids:
            All item identifiers that need a placement (ordered by source
            order, which determines auto-placement priority).
        explicit_placements:
            Items whose position has already been determined (e.g. via
            ``grid-column`` / ``grid-row`` properties or named areas).

        Returns
        -------
        list[GridPlacement]
            A complete list of placements for every item in *item_ids*, in
            source order.  Explicit placements are returned unchanged; auto-
            placed items receive concrete integer line numbers.
        """
        flow_axis = "column" if grid.auto_flow.startswith("column") else "row"
        dense = "dense" in grid.auto_flow

        # Index existing explicit placements by item_id for quick lookup
        explicit_map: dict[str, GridPlacement] = {
            p.item_id: p for p in explicit_placements
        }

        # --- build occupancy grid ----------------------------------------
        # We use a set of (row, col) tuples (1-based) to mark occupied cells.
        # The grid can grow implicitly; we'll track the extent separately.
        occupied: set[tuple[int, int]] = set()
        num_cols = max(grid.num_columns, 1)
        num_rows = max(grid.num_rows, 1)

        def mark_occupied(
            r_start: int, c_start: int, r_span: int, c_span: int
        ) -> None:
            nonlocal num_rows, num_cols
            for r in range(r_start, r_start + r_span):
                for c in range(c_start, c_start + c_span):
                    occupied.add((r, c))
                    num_rows = max(num_rows, r)
                    num_cols = max(num_cols, c)

        # Mark cells claimed by explicit placements
        for p in explicit_placements:
            if (
                isinstance(p.col_start, int)
                and isinstance(p.row_start, int)
            ):
                mark_occupied(
                    p.row_start,
                    p.col_start,
                    p.resolved_row_span(),
                    p.resolved_col_span(),
                )

        # --- auto-placement cursor ---------------------------------------
        # cursor moves along the major axis (row for "row" flow, col for
        # "column" flow).
        cursor_major = 1
        cursor_minor = 1

        def _next_free(
            row_span: int, col_span: int
        ) -> tuple[int, int]:
            """Find the next unoccupied (row_start, col_start) for a span."""
            nonlocal cursor_major, cursor_minor, num_rows, num_cols

            if flow_axis == "row":
                major_limit = num_rows + row_span + 1
                minor_limit = num_cols
                r = cursor_major if not dense else 1
                c = cursor_minor if not dense else 1
                while r <= major_limit:
                    while c <= minor_limit - col_span + 1:
                        if _fits(r, c, row_span, col_span):
                            if not dense:
                                cursor_major = r
                                cursor_minor = c
                            return r, c
                        c += 1
                    r += 1
                    c = 1
                # implicit row needed
                r = num_rows + 1
                return r, 1
            else:  # column flow
                major_limit = num_cols + col_span + 1
                minor_limit = num_rows
                col = cursor_major if not dense else 1
                row = cursor_minor if not dense else 1
                while col <= major_limit:
                    while row <= minor_limit - row_span + 1:
                        if _fits(row, col, row_span, col_span):
                            if not dense:
                                cursor_major = col
                                cursor_minor = row
                            return row, col
                        row += 1
                    col += 1
                    row = 1
                col = num_cols + 1
                return 1, col

        def _fits(
            r: int, c: int, r_span: int, c_span: int
        ) -> bool:
            for dr in range(r_span):
                for dc in range(c_span):
                    if (r + dr, c + dc) in occupied:
                        return False
            return True

        # --- place each item ---------------------------------------------
        results: list[GridPlacement] = []

        for item_id in item_ids:
            if item_id in explicit_map:
                results.append(explicit_map[item_id])
                continue

            # default span
            r_span = 1
            c_span = 1

            r_start, c_start = _next_free(r_span, c_span)
            mark_occupied(r_start, c_start, r_span, c_span)

            results.append(
                GridPlacement(
                    item_id=item_id,
                    col_start=c_start,
                    col_end=c_start + c_span,
                    row_start=r_start,
                    row_end=r_start + r_span,
                    col_span=c_span,
                    row_span=r_span,
                )
            )

        return results
