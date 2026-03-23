"""Cross-module tests: merge_repair_frontiers from repair_semantics."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import merge_repair_frontiers, RepairFrontier
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_frontier(fid, coords=None):
    return RepairFrontier(
        frontier_id=fid,
        coordinates=frozenset(coords or []),
        status="ACTIVE",
    )

def test_merge_repair_frontiers_importable():
    assert callable(merge_repair_frontiers)

def test_merge_two_frontiers():
    f1 = _make_frontier("f1", ["coord_a"])
    f2 = _make_frontier("f2", ["coord_b"])
    merged = merge_repair_frontiers(f1, f2)
    assert merged is not None
