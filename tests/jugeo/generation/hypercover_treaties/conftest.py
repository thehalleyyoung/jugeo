"""Shared fixtures for the hypercover_treaties test suite.

Every test file in this package imports from conftest.py.  The fixtures here
provide:

* basic_support      — a minimal SupportRegion
* basic_goal         — a minimal ConstructionGoal
* basic_treaty       — a minimal OverlapTreaty with one satisfied clause
* basic_overlap_treaty — same as basic_treaty but with two patches
* sample_synthesis_record — a HypercoverSynthesisRecord (skipped if unavailable)
* sample_synthesis_outcome — a SynthesisOutcome (skipped if unavailable)

All jugeo imports are wrapped in try/except so that the test suite degrades
gracefully when optional modules are absent.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

# ---------------------------------------------------------------------------
# Core geometry / generation imports — skip whole session if unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import Coordinate, CoordinateKind
    CoordinateObject = Coordinate
except ImportError as _e:
    pytest.skip(f"jugeo.geometry.site not available: {_e}", allow_module_level=True)

try:
    from jugeo.geometry.supports import SupportRegion
except ImportError as _e:
    pytest.skip(f"jugeo.geometry.supports not available: {_e}", allow_module_level=True)

try:
    from jugeo.generation.goals import ConstructionGoal, GoalPriority
except ImportError as _e:
    pytest.skip(f"jugeo.generation.goals not available: {_e}", allow_module_level=True)

try:
    from jugeo.evidence.trust import TrustTier
except ImportError as _e:
    pytest.skip(f"jugeo.evidence.trust not available: {_e}", allow_module_level=True)

try:
    from jugeo.generation.treaties import TreatyClause, OverlapTreaty, evaluate_treaty
except ImportError as _e:
    pytest.skip(f"jugeo.generation.treaties not available: {_e}", allow_module_level=True)

try:
    from jugeo.geometry.covers import Cover
except ImportError as _e:
    pytest.skip(f"jugeo.geometry.covers not available: {_e}", allow_module_level=True)

try:
    from jugeo.geometry.descent import DescentEngine
except ImportError as _e:
    pytest.skip(f"jugeo.geometry.descent not available: {_e}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helper functions (also available for import into test files)
# ---------------------------------------------------------------------------

def make_coordinate(name: str = "patch_a", kind: CoordinateKind = CoordinateKind.REGION) -> Coordinate:
    """Create a Coordinate with a single component."""
    return Coordinate(components=(name,), kind=kind)


def make_support(patch: str = "p") -> SupportRegion:
    """Create a minimal SupportRegion for testing."""
    coord = Coordinate(components=(patch,), kind=CoordinateKind.REGION)
    return SupportRegion(coord, frozenset({patch}))


def make_goal(
    proposition: str = "test_prop",
    patch: str = "p",
    priority: "GoalPriority" = None,
    tier: "TrustTier" = None,
    budget: int = 1,
) -> "ConstructionGoal":
    """Create a minimal ConstructionGoal for testing."""
    if priority is None:
        priority = GoalPriority.MEDIUM
    if tier is None:
        tier = TrustTier.REVIEWED
    return ConstructionGoal(
        proposition=proposition,
        support=make_support(patch),
        required_tier=tier,
        priority=priority,
        budget=budget,
    )


def make_clause(
    patch: str = "p",
    expectation: str = "value_matches",
    satisfied: bool = True,
) -> "TreatyClause":
    """Create a TreatyClause for testing."""
    return TreatyClause(patch=patch, expectation=expectation, satisfied=satisfied)


def make_overlap_treaty(
    patches: tuple[str, ...] = ("p1", "p2"),
    satisfied: bool = True,
    provenance: tuple[str, ...] = (),
) -> "OverlapTreaty":
    """Create an OverlapTreaty with one clause per patch."""
    clauses = tuple(
        TreatyClause(patch=p, expectation=f"expect_{p}", satisfied=satisfied)
        for p in patches
    )
    return OverlapTreaty(patches=patches, clauses=clauses, provenance=provenance)


def make_cover(
    target_name: str = "target",
    patch_names: tuple[str, ...] = ("a", "b"),
    overlaps: tuple[tuple[str, str], ...] = (),
) -> "Cover":
    """Create a Cover with the given target and patches."""
    target = Coordinate(components=(target_name,), kind=CoordinateKind.REGION)
    patches = tuple(
        Coordinate(components=(name,), kind=CoordinateKind.REGION)
        for name in patch_names
    )
    return Cover(target=target, patches=patches, overlaps=overlaps)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def basic_support() -> "SupportRegion":
    """A minimal SupportRegion with a single patch key 'p'."""
    return make_support("p")


@pytest.fixture
def basic_goal() -> "ConstructionGoal":
    """A minimal ConstructionGoal at REVIEWED tier with MEDIUM priority."""
    return make_goal("test_proposition", "p")


@pytest.fixture
def basic_treaty() -> "OverlapTreaty":
    """A minimal OverlapTreaty with one satisfied clause."""
    clause = TreatyClause(patch="p", expectation="value_matches", satisfied=True)
    return OverlapTreaty(patches=("p",), clauses=(clause,))


@pytest.fixture
def basic_overlap_treaty() -> "OverlapTreaty":
    """An OverlapTreaty with two patches, all clauses satisfied."""
    return make_overlap_treaty(patches=("p1", "p2"), satisfied=True)


@pytest.fixture
def unsatisfied_treaty() -> "OverlapTreaty":
    """An OverlapTreaty with two patches, no clauses satisfied."""
    return make_overlap_treaty(patches=("p1", "p2"), satisfied=False)


@pytest.fixture
def multi_patch_treaty() -> "OverlapTreaty":
    """An OverlapTreaty with five patches, all satisfied."""
    return make_overlap_treaty(patches=("p1", "p2", "p3", "p4", "p5"), satisfied=True)


@pytest.fixture
def basic_cover() -> "Cover":
    """A Cover with two patches and one overlap."""
    return make_cover(
        target_name="root",
        patch_names=("a", "b"),
        overlaps=(("a", "b"),),
    )


@pytest.fixture
def descent_engine() -> "DescentEngine":
    """A default DescentEngine instance."""
    return DescentEngine()


@pytest.fixture
def sample_goals() -> list["ConstructionGoal"]:
    """A list of five ConstructionGoals with different propositions."""
    return [
        make_goal(f"proposition_{i}", f"patch_{i}", budget=i + 1)
        for i in range(5)
    ]


@pytest.fixture
def high_priority_goal() -> "ConstructionGoal":
    """A HIGH priority ConstructionGoal."""
    return make_goal("high_prio_prop", "hp", priority=GoalPriority.HIGH)


@pytest.fixture
def low_priority_goal() -> "ConstructionGoal":
    """A LOW priority ConstructionGoal."""
    return make_goal("low_prio_prop", "lp", priority=GoalPriority.LOW)


@pytest.fixture
def verified_goal() -> "ConstructionGoal":
    """A ConstructionGoal at VERIFIED trust tier."""
    return make_goal("verified_prop", "vp", tier=TrustTier.VERIFIED)


@pytest.fixture
def proposal_goal() -> "ConstructionGoal":
    """A ConstructionGoal at PROPOSAL trust tier."""
    return make_goal("proposal_prop", "pp", tier=TrustTier.PROPOSAL)


# ---------------------------------------------------------------------------
# Fixtures that depend on the hypercover_treaties package itself
# (skipped when the package is not available)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_synthesis_record():
    """A HypercoverSynthesisRecord loaded from the source package.

    Skipped if models.py is not yet implemented.
    """
    try:
        from jugeo.generation.hypercover_treaties.models import HypercoverSynthesisRecord
    except ImportError as exc:
        pytest.skip(f"HypercoverSynthesisRecord not available: {exc}")

    return HypercoverSynthesisRecord(
        record_id="test_record_001",
        patches=("p1", "p2", "p3"),
        steps=[],
    )


@pytest.fixture
def sample_synthesis_outcome():
    """A SynthesisOutcome loaded from the source package.

    Skipped if models.py is not yet implemented.
    """
    try:
        from jugeo.generation.hypercover_treaties.models import SynthesisOutcome
    except ImportError as exc:
        pytest.skip(f"SynthesisOutcome not available: {exc}")

    return SynthesisOutcome(
        success=True,
        treaties=[],
        laws=[],
        record_id="test_outcome_001",
    )
