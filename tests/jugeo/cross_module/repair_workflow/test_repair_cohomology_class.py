"""Cross-module tests: RepairFrontier has expected properties."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import RepairFrontier
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

@pytest.fixture
def frontier():
    return RepairFrontier(
        frontier_id="frontier-001",
        coordinates=frozenset(["coord_a", "coord_b"]),
        status="ACTIVE",
    )

def test_repair_frontier_construction(frontier):
    assert frontier is not None
    assert frontier.frontier_id == "frontier-001"

def test_frontier_expand(frontier):
    new_frontier = frontier.expand({"coord_c"})
    assert "coord_c" in new_frontier.coordinates

def test_frontier_contract(frontier):
    new_frontier = frontier.contract({"coord_a"})
    assert "coord_a" not in new_frontier.coordinates

def test_frontier_contains_coordinate(frontier):
    result = frontier.contains_coordinate("coord_a")
    assert isinstance(result, bool)
    assert result is True
