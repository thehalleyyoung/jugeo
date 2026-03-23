"""Shared fixtures for jugeo.generation.inhabitant_fleets tests."""
from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import pytest
import time
import uuid

# ---------------------------------------------------------------------------
# Optional jugeo.geometry helpers
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.geometry.supports import SupportRegion

    def make_support(patch="p"):
        coord = CoordinateObject(components=("coord",), kind=CoordinateKind.REGION)
        return SupportRegion(coordinate=coord, patch_keys=frozenset({patch}))

    _JUGEO_AVAILABLE = True
except ImportError:
    _JUGEO_AVAILABLE = False

    def make_support(patch="p"):
        return None


# ---------------------------------------------------------------------------
# Optional model / fleet imports
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal,
        FleetBid,
        BackpressureSignal,
        SemanticMove,
        NormalizedProposal,
        ProposalStatus,
        SeverityLevel,
        MoveType,
    )
    from jugeo.generation.inhabitant_fleets.s02_ai_fleets import (
        FleetMember,
        FleetCoordinator,
        InhabitantFleet,
        FleetRegistry,
        BidAggregator,
    )
    from jugeo.generation.inhabitant_fleets.s01_local_inhabitant_synthesis import (
        SynthesisContext,
    )
    from jugeo.evidence.trust import TrustTier

    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Minimal mock goal (usable even without jugeo imports)
# ---------------------------------------------------------------------------
class MockGoal:
    """Lightweight stand-in for ConstructionGoal in tests."""

    def __init__(self, proposition="test proposition", priority=2, budget=5):
        self.proposition = proposition
        self.priority = priority
        self.budget = budget
        self.support = None
        self.provenance = "test"
        self.required_tier = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def goal_dict():
    """A plain-dict goal usable regardless of import state."""
    return {
        "proposition": "test goal proposition",
        "priority": 3,
        "budget": 10,
        "patch_id": "patch-fixture-001",
        "label": "test-label",
    }


@pytest.fixture
def mock_goal():
    return MockGoal(proposition="fixture proposition", priority=2, budget=5)


# The factory and model fixtures below skip automatically when models unavailable.

@pytest.fixture
def make_proposal():
    """Factory-function fixture for InhabitantProposal."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")

    def _factory(
        patch_id="patch-001",
        section_label="section-A",
        content="semantic content here",
        trust_tier_value=TrustTier.PROPOSAL,
        evidence_score=0.75,
    ):
        return InhabitantProposal(
            proposal_id=str(uuid.uuid4()),
            patch_id=patch_id,
            section_label=section_label,
            semantic_content=content,
            proposer_id="proposer-001",
            trust_tier=trust_tier_value,
            evidence_score=evidence_score,
            competing_proposals=[],
            status=ProposalStatus.PENDING,
            created_at=time.time(),
            metadata={},
        )

    return _factory


@pytest.fixture
def sample_proposal(make_proposal):
    return make_proposal()


@pytest.fixture
def sample_bid():
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")
    return FleetBid(
        bid_id=str(uuid.uuid4()),
        fleet_member_id="member-001",
        goal_label="goal-label-A",
        proposed_inhabitant="inhabitant-content",
        bid_score=0.8,
        resource_estimate=3.0,
        overlap_compatibility_score=0.9,
        backpressure_tolerance=0.7,
        metadata={"source": "fixture"},
    )


@pytest.fixture
def sample_signal():
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")
    return BackpressureSignal(
        signal_id=str(uuid.uuid4()),
        source_patch="patch-src",
        target_patches=["patch-tgt-1", "patch-tgt-2"],
        instability_score=0.6,
        threshold=0.5,
        severity=SeverityLevel.MEDIUM,
        timestamp=time.time(),
        remediation_hints=["reduce load", "defer proposals"],
    )


@pytest.fixture
def sample_move():
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")
    return SemanticMove(
        move_id=str(uuid.uuid4()),
        move_type=MoveType.PROPOSE,
        source_state={"key": "source"},
        target_state={"key": "target"},
        semantic_distance=0.3,
        validity_certificate="cert-abc",
        overlap_impact=0.1,
        move_cost=1.0,
    )


@pytest.fixture
def sample_fleet():
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")
    members = [
        FleetMember(
            member_id=f"member-{i}",
            specialization=f"spec-{i}",
            trust_tier=TrustTier.PROPOSAL,
            current_load=float(i * 10),
            proposal_history=[],
        )
        for i in range(3)
    ]
    coordinator = FleetCoordinator()
    fleet = InhabitantFleet(
        fleet_id="fleet-fixture-001",
        members=members,
        coordinator=coordinator,
        strategy="greedy",
        current_bids=[],
        completed_proposals=[],
    )
    return fleet


@pytest.fixture
def sample_context():
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")
    return SynthesisContext(
        available_budget=20,
        active_treaties=[],
        backpressure_state={},
        fleet_registry=None,
    )


@pytest.fixture
def fleet_registry():
    if not _MODELS_AVAILABLE:
        pytest.skip("models not importable")
    registry = FleetRegistry()
    for i in range(2):
        members = [
            FleetMember(
                member_id=f"reg-member-{i}-{j}",
                specialization=f"spec-{j}",
                trust_tier=TrustTier.REVIEWED,
                current_load=5.0,
                proposal_history=[],
            )
            for j in range(2)
        ]
        fleet = InhabitantFleet(
            fleet_id=f"reg-fleet-{i}",
            members=members,
            coordinator=FleetCoordinator(),
            strategy="heuristic",
            current_bids=[],
            completed_proposals=[],
        )
        registry.register_fleet(fleet)
    return registry


# ---------------------------------------------------------------------------
# make_support re-exported so test files can import from conftest
# ---------------------------------------------------------------------------
@pytest.fixture
def support_factory():
    """Returns the make_support helper."""
    return make_support
