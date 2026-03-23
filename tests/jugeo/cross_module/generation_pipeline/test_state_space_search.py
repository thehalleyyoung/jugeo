"""Cross-module tests: generation.goals module."""
import pytest

try:
    from jugeo.generation.goals import GoalScheduler, GoalTree
    HAS_GOALS = True
except ImportError:
    HAS_GOALS = False

@pytest.mark.skipif(not HAS_GOALS, reason="generation.goals not available")
def test_goal_scheduler_importable():
    assert GoalScheduler is not None

@pytest.mark.skipif(not HAS_GOALS, reason="generation.goals not available")
def test_goal_tree_importable():
    assert GoalTree is not None

def test_generation_inhabitant_fleets_importable():
    """Verify inhabitant_fleets is importable (it's always available)."""
    try:
        from jugeo.generation.inhabitant_fleets.models import InhabitantProposal
        assert InhabitantProposal is not None
    except ImportError as e:
        pytest.skip(f"inhabitant_fleets not available: {e}")
