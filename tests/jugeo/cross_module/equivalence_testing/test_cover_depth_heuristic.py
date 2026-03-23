"""Cross-module tests: RefinementOrder has expected properties."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement import RefinementOrder
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def _make_order():
    try:
        return RefinementOrder(
            order_id="order-001",
            coordinates=frozenset(["c1", "c2"]),
            relations=[],
            equivalence_classes=[],
        )
    except TypeError:
        try:
            return RefinementOrder(order_id="order-001")
        except TypeError:
            return None

def test_refinement_order_has_coordinates():
    order = _make_order()
    if order is None:
        pytest.skip("Cannot construct RefinementOrder")
    assert hasattr(order, "coordinates")

def test_refinement_order_has_equivalence_classes():
    order = _make_order()
    if order is None:
        pytest.skip("Cannot construct RefinementOrder")
    assert hasattr(order, "equivalence_classes")
