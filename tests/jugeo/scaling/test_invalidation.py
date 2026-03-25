from __future__ import annotations

import time

import pytest

from src.jugeo.scaling.invalidation.models import (
    ContractBoundary,
    DampeningConfig,
    InvalidationStrategy,
)
from src.jugeo.scaling.invalidation.dampener import InvalidationDampener


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _simple_graph() -> dict:
    """A -> B -> C -> D, A -> E"""
    return {
        "A": ["B", "E"],
        "B": ["C"],
        "C": ["D"],
        "D": [],
        "E": [],
    }


def _large_graph(size: int = 120) -> dict:
    """Chain of nodes: n0 -> n1 -> n2 -> ..."""
    g: dict = {}
    for i in range(size):
        g[f"n{i}"] = [f"n{i+1}"] if i < size - 1 else []
    return g


# ===================================================================
# InvalidationDampener tests
# ===================================================================


class TestInvalidationDampener:
    def test_full_cascade(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        result = d.invalidate("A", _simple_graph())
        invalidated = set(result["invalidated"])
        assert {"A", "B", "C", "D", "E"} == invalidated

    def test_contract_bounded(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        d.add_contract(ContractBoundary(
            coordinate_id="C",
            contract_hash="abc123",
            verified_at=time.time(),
            trust_level="STRONG",
        ))
        result = d.invalidate("A", _simple_graph())
        invalidated = set(result["invalidated"])
        # C should be included (as a boundary) but D should NOT
        assert "A" in invalidated
        assert "B" in invalidated
        assert "C" in invalidated
        assert "D" not in invalidated

    def test_tiered(self) -> None:
        cfg = DampeningConfig(tiered_depths=[1, 3, 10])
        d = InvalidationDampener(cfg)
        # Force TIERED strategy by not having contracts
        result = d._tiered("A", _simple_graph())
        assert "immediate" in result
        assert "deferred" in result
        assert "lazy" in result
        # B and E at depth 1 => immediate
        assert "B" in result["immediate"] or "E" in result["immediate"]

    def test_probabilistic(self) -> None:
        cfg = DampeningConfig(probabilistic_sample_rate=1.0)  # 100% sample
        d = InvalidationDampener(cfg)
        result = d._probabilistic("A", _simple_graph(), 1.0)
        # With 100% sample rate, should get everything
        assert "A" in result
        assert "B" in result

    def test_semantic_analysis(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        change_detail = {
            "type": "method_signature",
            "affects": ["B", "C"],
        }
        result = d._semantic_analysis("A", change_detail, _simple_graph())
        # Only A (source) and affected (B, C) should be included
        assert "A" in result
        assert "B" in result
        assert "C" in result
        assert "D" not in result
        assert "E" not in result

    def test_choose_strategy_contract(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        d.add_contract(ContractBoundary(
            coordinate_id="A",
            contract_hash="h",
            verified_at=time.time(),
            trust_level="STRONG",
        ))
        assert d.choose_strategy("A", _simple_graph()) == InvalidationStrategy.CONTRACT_BOUNDED

    def test_choose_strategy_large_graph(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        assert d.choose_strategy("n0", _large_graph(200)) == InvalidationStrategy.PROBABILISTIC

    def test_choose_strategy_default(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        assert d.choose_strategy("A", _simple_graph()) == InvalidationStrategy.FULL_CASCADE

    def test_statistics(self) -> None:
        cfg = DampeningConfig()
        d = InvalidationDampener(cfg)
        d.invalidate("A", _simple_graph())
        stats = d.statistics()
        assert stats["invalidations"] == 1
        assert stats["total_coords_invalidated"] > 0
