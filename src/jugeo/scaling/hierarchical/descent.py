"""Hierarchical descent algorithm for multi-level site verification.

The descent proceeds bottom-up: finer levels (EXPRESSION, BRANCH, FUNCTION)
are checked first so that coarser-level checks can rely on already-verified
sub-components.

Key classes:

- ``OverlapIndex``       — fast adjacency/overlap index built from morphisms
- ``HierarchicalDescent`` — orchestrates descent across all levels
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from jugeo.scaling.hierarchical.levels import LevelHeuristic
from jugeo.scaling.hierarchical.models import (
    DescentLevel,
    HierarchicalDescentResult,
    LevelView,
    SiteLevel,
)

# ---------------------------------------------------------------------------
# OverlapIndex
# ---------------------------------------------------------------------------


class OverlapIndex:
    """Spatial index for efficient overlap/adjacency queries.

    An overlap is defined as: two coordinates are *overlapping* if there
    exists at least one morphism connecting them in either direction.
    """

    def __init__(self) -> None:
        # adjacency: coord_id → set of coord_ids it overlaps with
        self._adj: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        coordinates: list[Any],  # HierarchicalCoordinate or dicts
        morphisms: list[dict[str, Any]],
    ) -> None:
        """Build the overlap index from a list of coordinates and morphisms.

        Coordinates may be ``HierarchicalCoordinate`` objects or plain dicts
        with an ``id`` key.
        """
        # Initialise empty adjacency for every coordinate
        for c in coordinates:
            cid = c.id if hasattr(c, "id") else c["id"]
            if cid not in self._adj:
                self._adj[cid] = set()

        # Populate from morphisms (treat as undirected for overlap purposes)
        for m in morphisms:
            src = m.get("source_id", "")
            tgt = m.get("target_id", "")
            if src and tgt and src != tgt:
                if src not in self._adj:
                    self._adj[src] = set()
                if tgt not in self._adj:
                    self._adj[tgt] = set()
                self._adj[src].add(tgt)
                self._adj[tgt].add(src)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def overlaps_of(self, coord_id: str) -> list[str]:
        """Return the list of coordinate ids that overlap with coord_id."""
        return list(self._adj.get(coord_id, set()))

    def overlaps_between(
        self, set_a: list[str] | set[str], set_b: list[str] | set[str]
    ) -> list[tuple[str, str]]:
        """Return all (a, b) pairs where a ∈ set_a, b ∈ set_b, and they overlap."""
        a = set(set_a)
        b = set(set_b)
        result: list[tuple[str, str]] = []
        for cid in a:
            for neighbor in self._adj.get(cid, set()):
                if neighbor in b:
                    result.append((cid, neighbor))
        return result

    def degree(self, coord_id: str) -> int:
        """Return the number of coordinates that overlap with coord_id."""
        return len(self._adj.get(coord_id, set()))

    def max_degree(self) -> int:
        """Return the maximum overlap degree across all coordinates."""
        if not self._adj:
            return 0
        return max(len(neighbors) for neighbors in self._adj.values())

    def avg_degree(self) -> float:
        """Return the average overlap degree."""
        if not self._adj:
            return 0.0
        total = sum(len(neighbors) for neighbors in self._adj.values())
        return total / len(self._adj)

    def all_overlap_pairs(self) -> list[tuple[str, str]]:
        """Return all unique (a, b) overlap pairs (a < b lexicographically)."""
        seen: set[tuple[str, str]] = set()
        result: list[tuple[str, str]] = []
        for cid, neighbors in self._adj.items():
            for neighbor in neighbors:
                key = (min(cid, neighbor), max(cid, neighbor))
                if key not in seen:
                    seen.add(key)
                    result.append(key)
        return result

    def has_overlap(self, coord_a: str, coord_b: str) -> bool:
        """Return True if the two coordinates overlap."""
        return coord_b in self._adj.get(coord_a, set())

    def coord_ids(self) -> list[str]:
        """Return all coordinate ids in the index."""
        return list(self._adj.keys())

    def __len__(self) -> int:
        return len(self._adj)


# ---------------------------------------------------------------------------
# HierarchicalDescent
# ---------------------------------------------------------------------------


class HierarchicalDescent:
    """Hierarchical descent algorithm for multi-level site verification.

    Checks descent conditions at each SiteLevel from finest to coarsest
    (EXPRESSION → PROJECT).  At each level it:
    1. Finds all overlap pairs (connected by a morphism at that level).
    2. Checks each pair against the provided sections / propositions.
    3. Records pass/fail counts and any obstructions.

    Usage::

        descent = HierarchicalDescent()
        result  = descent.descend(site, sections={}, propositions=[])
    """

    # ------------------------------------------------------------------
    # Full descent
    # ------------------------------------------------------------------

    def descend(
        self,
        site: Any,  # HierarchicalSite
        sections: dict[str, Any],
        propositions: list[Any],
    ) -> HierarchicalDescentResult:
        """Run hierarchical descent over all levels of *site*.

        Levels are processed in bottom-up order (finest → coarsest).
        """
        start = time.perf_counter()
        level_results: list[DescentLevel] = []

        for level in self._level_order():
            level_view = site.get_level_view(level)
            dl = self.descend_at_level(level_view, sections, propositions)
            level_results.append(dl)

        duration_ms = (time.perf_counter() - start) * 1000.0
        return self.compose_level_results(level_results, duration_ms=duration_ms)

    # ------------------------------------------------------------------
    # Level descent
    # ------------------------------------------------------------------

    def descend_at_level(
        self,
        level_view: LevelView,
        sections: dict[str, Any],
        propositions: list[Any],
    ) -> DescentLevel:
        """Run descent for a single level.

        Finds all sparse overlaps (pairs connected by morphisms) and checks
        each one.
        """
        overlap_pairs = self._find_sparse_overlaps(
            level_view.coordinates,
            level_view.morphisms,
        )

        dl = DescentLevel.create(level_view.level, overlap_pairs=overlap_pairs)
        dl.checks_required = len(overlap_pairs)

        passed = 0
        failed = 0
        obstructions: list[Any] = []

        for pair in overlap_pairs:
            section_a = sections.get(pair[0], {})
            section_b = sections.get(pair[1], {})
            shared_coords = self._shared_coords(pair[0], pair[1], level_view.morphisms)
            ok = self._check_overlap(section_a, section_b, shared_coords)
            if ok:
                passed += 1
            else:
                failed += 1
                obstructions.append(
                    {
                        "pair": list(pair),
                        "level": level_view.level.to_dict(),
                        "reason": "overlap_check_failed",
                    }
                )

        dl.checks_passed = passed
        dl.checks_failed = failed
        dl.obstructions = obstructions
        return dl

    # ------------------------------------------------------------------
    # Parallel descent at one level
    # ------------------------------------------------------------------

    def parallel_descent_at_level(
        self,
        level_view: LevelView,
        sections: dict[str, Any],
        propositions: list[Any],
        max_workers: int = 4,
    ) -> DescentLevel:
        """Check overlaps at a level using a thread pool.

        Falls back gracefully if only a small number of pairs exist.
        """
        overlap_pairs = self._find_sparse_overlaps(
            level_view.coordinates,
            level_view.morphisms,
        )

        dl = DescentLevel.create(level_view.level, overlap_pairs=overlap_pairs)
        dl.checks_required = len(overlap_pairs)

        if not overlap_pairs:
            return dl

        passed = 0
        failed = 0
        obstructions: list[Any] = []

        def _check(pair: tuple[str, str]) -> tuple[tuple[str, str], bool]:
            sa = sections.get(pair[0], {})
            sb = sections.get(pair[1], {})
            shared = self._shared_coords(pair[0], pair[1], level_view.morphisms)
            return pair, self._check_overlap(sa, sb, shared)

        effective_workers = min(max_workers, len(overlap_pairs))
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {executor.submit(_check, p): p for p in overlap_pairs}
            for future in as_completed(futures):
                pair, ok = future.result()
                if ok:
                    passed += 1
                else:
                    failed += 1
                    obstructions.append(
                        {
                            "pair": list(pair),
                            "level": level_view.level.to_dict(),
                            "reason": "overlap_check_failed",
                        }
                    )

        dl.checks_passed = passed
        dl.checks_failed = failed
        dl.obstructions = obstructions
        return dl

    # ------------------------------------------------------------------
    # Incremental descent
    # ------------------------------------------------------------------

    def incremental_descent(
        self,
        changed_coords: list[str],
        site: Any,  # HierarchicalSite
        sections: dict[str, Any],
        propositions: list[Any],
    ) -> HierarchicalDescentResult:
        """Re-check only levels (and overlaps) affected by changed coordinates.

        For each affected level, only the overlap pairs that involve at least
        one changed coordinate are re-checked.
        """
        start = time.perf_counter()
        affected = set(self._affected_levels(changed_coords, site))
        changed_set = set(changed_coords)
        level_results: list[DescentLevel] = []

        for level in self._level_order():
            level_view = site.get_level_view(level)
            if level not in affected:
                # Emit a trivially-passing empty DescentLevel to keep the
                # level list complete
                dl = DescentLevel.create(level)
                level_results.append(dl)
                continue

            # Restrict overlap pairs to those touching changed coords
            all_pairs = self._find_sparse_overlaps(
                level_view.coordinates,
                level_view.morphisms,
            )
            relevant_pairs = [
                p for p in all_pairs if p[0] in changed_set or p[1] in changed_set
            ]

            restricted_view = LevelView.build(
                level,
                level_view.coordinates,
                level_view.morphisms,
                level_view.covers,
            )
            # Override pairs via a lightweight wrapper check
            dl = DescentLevel.create(level, overlap_pairs=relevant_pairs)
            dl.checks_required = len(relevant_pairs)
            passed = 0
            failed = 0
            obstructions: list[Any] = []
            for pair in relevant_pairs:
                sa = sections.get(pair[0], {})
                sb = sections.get(pair[1], {})
                shared = self._shared_coords(pair[0], pair[1], level_view.morphisms)
                ok = self._check_overlap(sa, sb, shared)
                if ok:
                    passed += 1
                else:
                    failed += 1
                    obstructions.append({"pair": list(pair), "level": level.to_dict()})
            dl.checks_passed = passed
            dl.checks_failed = failed
            dl.obstructions = obstructions
            level_results.append(dl)

        duration_ms = (time.perf_counter() - start) * 1000.0
        return self.compose_level_results(level_results, duration_ms=duration_ms)

    def _affected_levels(
        self, changed_coords: list[str], site: Any
    ) -> list[SiteLevel]:
        """Return the unique levels of the changed coordinates plus their ancestors."""
        levels: set[SiteLevel] = set()
        for cid in changed_coords:
            coord = site.get_coordinate(cid)
            if coord is None:
                continue
            levels.add(coord.level)
            # Mark all ancestor levels as affected too
            for ancestor_id in site.get_ancestors(cid):
                anc = site.get_coordinate(ancestor_id)
                if anc is not None:
                    levels.add(anc.level)
        return list(levels)

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose_level_results(
        self,
        level_results: list[DescentLevel],
        duration_ms: float = 0.0,
    ) -> HierarchicalDescentResult:
        """Aggregate per-level results into a single HierarchicalDescentResult."""
        return HierarchicalDescentResult.from_levels(level_results, duration_ms=duration_ms)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_overlap(
        self,
        section_a: dict[str, Any],
        section_b: dict[str, Any],
        shared_coords: list[str],
    ) -> bool:
        """Check compatibility of two overlapping sections on their shared region.

        Checks in two passes:
        1. Explicitly provided shared_coords — keys derived from morphism topology.
        2. Common keys present in both section dicts — catches direct key conflicts
           even when no intermediate morphism targets exist.

        In both cases the two sections conflict if they provide non-None
        ``"value"`` entries that differ.
        """

        def _values_conflict(va: Any, vb: Any) -> bool:
            if isinstance(va, dict) and isinstance(vb, dict):
                a_val = va.get("value")
                b_val = vb.get("value")
                if a_val is not None and b_val is not None and a_val != b_val:
                    return True
            return False

        # Pass 1: morphism-derived shared coordinates
        for cid in shared_coords:
            if _values_conflict(section_a.get(cid, {}), section_b.get(cid, {})):
                return False

        # Pass 2: common keys in both section dicts
        for key in set(section_a.keys()) & set(section_b.keys()):
            if _values_conflict(section_a[key], section_b[key]):
                return False

        return True

    def _find_sparse_overlaps(
        self,
        coordinates: list[str],
        morphisms: list[dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Return overlap pairs: only pairs connected by at least one morphism.

        This avoids the O(n²) cost of checking all pairs.
        """
        coord_set = set(coordinates)
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[str, str]] = []

        for m in morphisms:
            src = m.get("source_id", "")
            tgt = m.get("target_id", "")
            if src in coord_set and tgt in coord_set and src != tgt:
                key = (min(src, tgt), max(src, tgt))
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)

        return pairs

    def _shared_coords(
        self, coord_a: str, coord_b: str, morphisms: list[dict[str, Any]]
    ) -> list[str]:
        """Return coordinate ids that appear as intermediaries between a and b.

        In the absence of explicit intersection information we use the set of
        target coordinates that are reachable from both a and b via one hop.
        """
        targets_of_a: set[str] = set()
        targets_of_b: set[str] = set()
        for m in morphisms:
            if m.get("source_id") == coord_a:
                targets_of_a.add(m["target_id"])
            if m.get("source_id") == coord_b:
                targets_of_b.add(m["target_id"])
        shared = targets_of_a & targets_of_b
        return list(shared)

    def _level_order(self) -> list[SiteLevel]:
        """Return levels in bottom-up order: EXPRESSION → PROJECT."""
        return LevelHeuristic.all_levels_fine_to_coarse()


__all__ = ["HierarchicalDescent", "OverlapIndex"]
