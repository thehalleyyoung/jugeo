"""
Large-repo performance optimisations for the co-evolution orchestration engine.

Activated when coordinate count exceeds *site_size_threshold* (default 10 000).
Provides frontier pruning, history compaction, partition-aware scheduling,
and memory pressure responses.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .models import MoveCategory, MoveHistory, MoveResult

__all__ = ["LargeRepoOptimizer"]


class LargeRepoOptimizer:
    """Performance optimiser for large sites (>10 000 coordinates)."""

    def __init__(self, site_size_threshold: int = 10000) -> None:
        self._site_size_threshold = site_size_threshold
        self._frontier_prune_count: int = 0
        self._compaction_count: int = 0
        self._batch_size: int = 50

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def should_activate(self, coordinate_count: int) -> bool:
        """True if the site is large enough to warrant optimisation."""
        return coordinate_count >= self._site_size_threshold

    # ------------------------------------------------------------------
    # Frontier optimisation
    # ------------------------------------------------------------------

    def optimize_frontier(
        self, frontier: list[dict[str, Any]], max_size: int = 1000
    ) -> list[dict[str, Any]]:
        """Prune the frontier to at most *max_size* entries by score."""
        if len(frontier) <= max_size:
            return frontier
        scored = sorted(frontier, key=self._approximate_score, reverse=True)
        self._frontier_prune_count += 1
        return scored[:max_size]

    def _approximate_score(self, move: dict[str, Any]) -> float:
        """Quick heuristic score for frontier ordering."""
        priority = move.get("priority", 0.0)
        cost = move.get("estimated_cost", 1.0)
        drift_reduction = move.get("expected_drift_reduction", 0.0)
        return priority + drift_reduction - cost * 0.01

    # ------------------------------------------------------------------
    # History compaction
    # ------------------------------------------------------------------

    def compact_move_history(
        self, history: MoveHistory, keep_recent: int = 100
    ) -> MoveHistory:
        """Compact old moves into macro-moves, keeping the most recent."""
        moves = list(history.moves)
        if len(moves) <= keep_recent:
            return history

        old_moves = moves[: -keep_recent]
        recent_moves = moves[-keep_recent:]

        macro = self._create_macro_move(old_moves)
        compacted = list(history.compacted_moves) + [macro]

        self._compaction_count += 1

        return MoveHistory(
            moves=recent_moves,
            compacted_moves=compacted,
            total_moves=history.total_moves,
            moves_since_last_compaction=len(recent_moves),
        )

    def _create_macro_move(self, moves: list[MoveResult]) -> dict[str, Any]:
        """Summarise a batch of moves into a single macro-move dict."""
        total = len(moves)
        successes = sum(1 for m in moves if m.success)
        all_sections: set[str] = set()
        all_obligations_gen: set[str] = set()
        all_obligations_dis: set[str] = set()
        total_duration = 0.0
        for m in moves:
            all_sections.update(m.sections_modified)
            all_obligations_gen.update(m.obligations_generated)
            all_obligations_dis.update(m.obligations_discharged)
            total_duration += m.duration_ms

        return {
            "type": "macro_move",
            "move_count": total,
            "success_count": successes,
            "sections_modified": sorted(all_sections),
            "obligations_generated": sorted(all_obligations_gen),
            "obligations_discharged": sorted(all_obligations_dis),
            "total_duration_ms": total_duration,
        }

    # ------------------------------------------------------------------
    # Partition-aware scheduling
    # ------------------------------------------------------------------

    def partition_aware_scheduling(
        self,
        moves: list[dict[str, Any]],
        partitions: list[list[str]],
    ) -> list[list[dict[str, Any]]]:
        """Group moves by partition for parallel execution.

        Each move is assigned to the partition that owns the majority of its
        target coordinates.
        """
        if not partitions:
            return [moves]

        coord_to_part: dict[str, int] = {}
        for idx, part in enumerate(partitions):
            for cid in part:
                coord_to_part[cid] = idx

        buckets: list[list[dict[str, Any]]] = [[] for _ in partitions]
        unassigned: list[dict[str, Any]] = []

        for move in moves:
            targets = move.get("target_coordinates", [])
            if not targets:
                unassigned.append(move)
                continue
            # Majority vote
            counts: dict[int, int] = {}
            for cid in targets:
                pidx = coord_to_part.get(cid)
                if pidx is not None:
                    counts[pidx] = counts.get(pidx, 0) + 1
            if counts:
                best = max(counts, key=lambda k: counts[k])
                buckets[best].append(move)
            else:
                unassigned.append(move)

        # Spread unassigned round-robin
        for i, m in enumerate(unassigned):
            buckets[i % len(buckets)].append(m)

        return buckets

    # ------------------------------------------------------------------
    # Cross-region treaties
    # ------------------------------------------------------------------

    def cross_region_treaty_batch(
        self, treaties: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Batch related treaties to minimise cross-region round trips."""
        if not treaties:
            return []
        by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for t in treaties:
            key = (
                str(t.get("region_a", "")),
                str(t.get("region_b", "")),
            )
            by_pair.setdefault(key, []).append(t)

        batched: list[dict[str, Any]] = []
        for (ra, rb), group in by_pair.items():
            batched.append({
                "region_a": ra,
                "region_b": rb,
                "treaties": group,
                "count": len(group),
            })
        return batched

    # ------------------------------------------------------------------
    # Incremental drift
    # ------------------------------------------------------------------

    def incremental_drift_check(
        self,
        changed_surfaces: list[str],
        state: Any,
    ) -> list[str]:
        """Return only the drift edges that involve changed surfaces."""
        drift_edges = getattr(state, "drift_edges", [])
        affected: list[str] = []
        changed_set = set(changed_surfaces)
        for edge in drift_edges:
            sa = getattr(edge, "surface_a", None)
            sb = getattr(edge, "surface_b", None)
            sa_val = sa.value if sa else ""
            sb_val = sb.value if sb else ""
            if sa_val in changed_set or sb_val in changed_set:
                affected.append(f"{sa_val}-{sb_val}")
        return affected

    # ------------------------------------------------------------------
    # Memory pressure
    # ------------------------------------------------------------------

    def memory_pressure_response(
        self, memory_mb: float, threshold_mb: float
    ) -> dict[str, Any]:
        """Decide what to shed when memory pressure is high."""
        if memory_mb < threshold_mb:
            return {"action": "none", "shed": []}

        shed: list[str] = []
        ratio = memory_mb / max(threshold_mb, 1.0)

        if ratio > 2.0:
            shed.extend(["frontier", "history", "drift_cache"])
        elif ratio > 1.5:
            shed.extend(["history", "drift_cache"])
        else:
            shed.append("drift_cache")

        return {
            "action": "shed",
            "shed": shed,
            "memory_mb": memory_mb,
            "threshold_mb": threshold_mb,
            "ratio": ratio,
        }

    # ------------------------------------------------------------------
    # Adaptive batching
    # ------------------------------------------------------------------

    def adaptive_batch_size(
        self,
        current_latency_ms: float,
        target_latency_ms: float = 100.0,
    ) -> int:
        """Adjust batch size to stay near *target_latency_ms*."""
        if current_latency_ms <= 0:
            return self._batch_size

        ratio = target_latency_ms / current_latency_ms
        new_size = max(1, int(self._batch_size * ratio))
        # Clamp
        new_size = max(1, min(new_size, 10000))
        self._batch_size = new_size
        return new_size

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        """Return optimiser statistics."""
        return {
            "site_size_threshold": self._site_size_threshold,
            "frontier_prune_count": self._frontier_prune_count,
            "compaction_count": self._compaction_count,
            "current_batch_size": self._batch_size,
        }
