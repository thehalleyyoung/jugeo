"""Tests for LargeRepoOptimizer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jugeo.orchestration.large_scale.models import MoveHistory, MoveResult
from jugeo.orchestration.large_scale.large_repo import LargeRepoOptimizer


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

class TestShouldActivate:
    def test_should_activate_below_threshold(self) -> None:
        opt = LargeRepoOptimizer(site_size_threshold=10000)
        assert opt.should_activate(5000) is False

    def test_should_activate_above_threshold(self) -> None:
        opt = LargeRepoOptimizer(site_size_threshold=10000)
        assert opt.should_activate(15000) is True

    def test_should_activate_exact_threshold(self) -> None:
        opt = LargeRepoOptimizer(site_size_threshold=10000)
        assert opt.should_activate(10000) is True


# ---------------------------------------------------------------------------
# Frontier optimisation
# ---------------------------------------------------------------------------

class TestOptimizeFrontier:
    def test_optimize_frontier_small(self) -> None:
        opt = LargeRepoOptimizer()
        frontier = [{"priority": 1.0, "estimated_cost": 0.5} for _ in range(5)]
        result = opt.optimize_frontier(frontier, max_size=10)
        assert len(result) == 5  # unchanged

    def test_optimize_frontier_large(self) -> None:
        opt = LargeRepoOptimizer()
        frontier = [
            {"priority": float(i), "estimated_cost": 1.0, "expected_drift_reduction": 0.0}
            for i in range(100)
        ]
        result = opt.optimize_frontier(frontier, max_size=10)
        assert len(result) == 10
        # Should have highest-priority items
        assert result[0]["priority"] >= result[-1]["priority"]


class TestApproximateScore:
    def test_approximate_score(self) -> None:
        opt = LargeRepoOptimizer()
        move = {"priority": 5.0, "estimated_cost": 1.0, "expected_drift_reduction": 0.2}
        score = opt._approximate_score(move)
        assert score > 0


# ---------------------------------------------------------------------------
# History compaction
# ---------------------------------------------------------------------------

class TestCompactHistory:
    def test_compact_history_empty(self) -> None:
        opt = LargeRepoOptimizer()
        history = MoveHistory()
        result = opt.compact_move_history(history, keep_recent=100)
        assert result.moves == []

    def test_compact_history_large(self) -> None:
        opt = LargeRepoOptimizer()
        moves = [
            MoveResult(
                move_id=f"m-{i}",
                success=i % 3 != 0,
                sections_modified=[f"s-{i}"],
                duration_ms=float(i),
            )
            for i in range(200)
        ]
        history = MoveHistory(
            moves=moves,
            total_moves=200,
            moves_since_last_compaction=200,
        )
        result = opt.compact_move_history(history, keep_recent=50)
        assert len(result.moves) == 50
        assert len(result.compacted_moves) >= 1
        macro = result.compacted_moves[-1]
        assert macro["type"] == "macro_move"
        assert macro["move_count"] == 150


# ---------------------------------------------------------------------------
# Partition-aware scheduling
# ---------------------------------------------------------------------------

class TestPartitionAwareScheduling:
    def test_partition_aware_scheduling(self) -> None:
        opt = LargeRepoOptimizer()
        moves = [
            {"id": "m1", "target_coordinates": ["c1", "c2"]},
            {"id": "m2", "target_coordinates": ["c3"]},
            {"id": "m3", "target_coordinates": ["c1"]},
        ]
        partitions = [["c1", "c2"], ["c3", "c4"]]
        result = opt.partition_aware_scheduling(moves, partitions)
        assert len(result) == 2
        # m1, m3 → partition 0 (c1, c2); m2 → partition 1 (c3)
        ids_0 = [m["id"] for m in result[0]]
        ids_1 = [m["id"] for m in result[1]]
        assert "m1" in ids_0
        assert "m3" in ids_0
        assert "m2" in ids_1


# ---------------------------------------------------------------------------
# Memory pressure
# ---------------------------------------------------------------------------

class TestMemoryPressureResponse:
    def test_memory_pressure_below_threshold(self) -> None:
        opt = LargeRepoOptimizer()
        resp = opt.memory_pressure_response(memory_mb=500.0, threshold_mb=1000.0)
        assert resp["action"] == "none"

    def test_memory_pressure_above_threshold(self) -> None:
        opt = LargeRepoOptimizer()
        resp = opt.memory_pressure_response(memory_mb=2000.0, threshold_mb=1000.0)
        assert resp["action"] == "shed"
        assert len(resp["shed"]) > 0


# ---------------------------------------------------------------------------
# Adaptive batch size
# ---------------------------------------------------------------------------

class TestAdaptiveBatchSize:
    def test_adaptive_batch_size_increase(self) -> None:
        opt = LargeRepoOptimizer()
        opt._batch_size = 50
        # Latency is low → batch size should increase
        new_size = opt.adaptive_batch_size(current_latency_ms=50.0, target_latency_ms=100.0)
        assert new_size > 50

    def test_adaptive_batch_size_decrease(self) -> None:
        opt = LargeRepoOptimizer()
        opt._batch_size = 50
        # Latency is high → batch size should decrease
        new_size = opt.adaptive_batch_size(current_latency_ms=200.0, target_latency_ms=100.0)
        assert new_size < 50


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStatistics:
    def test_statistics(self) -> None:
        opt = LargeRepoOptimizer(site_size_threshold=5000)
        stats = opt.statistics()
        assert stats["site_size_threshold"] == 5000
        assert stats["frontier_prune_count"] == 0
