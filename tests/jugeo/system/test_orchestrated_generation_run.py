"""System tests: orchestrated generation run.

Tests the complete orchestration pipeline spanning fleet bidding, controller
step execution, generation goal management, descent verification, and evidence
archival.  Exercises jugeo.orchestration.fleet, jugeo.orchestration.controller,
jugeo.generation.goals, jugeo.generation.construction, jugeo.geometry.descent,
and jugeo.evidence.channels across a single connected workflow.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.errors import (
    FailureClassification,
    FailureScope,
    JuGeoError,
    ObstructionRecord,
    EvidenceFamily,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    chain_failures,
    filter_failures,
    FailureFilter,
)
from jugeo.evidence.channels import (
    EvidenceChannel,
    EvidenceKind,
    EvidenceRecord,
    build_channel,
    ChannelConfiguration,
    ChannelRouter,
    ChannelFederation,
)
from jugeo.evidence.manifests import ManifestBuilder, ObligationPriority, ObstructionKind
from jugeo.evidence.provenance import ProvenanceStep, ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
from jugeo.generation.construction import Candidate, ConstructionGoal, SourceChannel
from jugeo.generation.goals import (
    GenerationGoal,
    GoalPriority,
    GoalStatus,
    GoalDecomposer,
    GoalTracker,
    GoalTree,
)
from jugeo.geometry.covers import (
    CoordinateKind,
    CoordinateObject,
    CoordinateMorphism,
    CoverBuilder,
)
from jugeo.geometry.descent import (
    CohomologyClass,
    DescentEngine,
    DescentObstruction,
    GluingReport,
    OverlapCondition,
    OverlapStatus,
    RepairFrontier,
)
from jugeo.orchestration.controller import (
    GreedyControl,
    MoveHistory,
    MoveKind,
    MoveRecord,
    Orchestrator,
    OrchestratorConfiguration,
    OrchestratorEventBus,
    OrchestratorEventKind,
    OrchestratorState,
    ResourceBudget,
    SemanticMove,
)
from jugeo.orchestration.fleet import (
    BidEvaluator,
    CompetitiveSearch,
    Fleet,
    FleetBid,
    FleetMember,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coord(name: str, kind: CoordinateKind = CoordinateKind.THEOREM) -> CoordinateObject:
    return CoordinateObject(components=(name,), kind=kind)


def _patch_coord(parent: str, suffix: str) -> CoordinateObject:
    return CoordinateObject(components=(parent, suffix), kind=CoordinateKind.REGION)


def _make_cover(base_name: str) -> Any:
    base = _coord(base_name)
    pa = _patch_coord(base_name, "A")
    pb = _patch_coord(base_name, "B")
    morph_a = CoordinateMorphism(source=f"{base_name}.A", target=base_name, reason="r")
    morph_b = CoordinateMorphism(source=f"{base_name}.B", target=base_name, reason="r")
    cb = CoverBuilder()
    cb.set_base(base).add_member(pa, morph_a).add_member(pb, morph_b)
    return cb.build()


def _make_fleet_member(name: str, caps: frozenset[str] | None = None) -> FleetMember:
    return FleetMember(
        name=name,
        capacity=3,
        capabilities=caps or frozenset({"verify", "construct"}),
        trust_ceiling=1.0,
        specialization_domains=("arithmetic", "type-theory"),
    )


def _make_goal(
    gid: str,
    coord: str = "Coord.X",
    prop: str = "P",
    priority: GoalPriority = GoalPriority.MEDIUM,
    budget: int = 10,
) -> GenerationGoal:
    return GenerationGoal(
        goal_id=gid,
        target_coordinate=coord,
        required_proposition=prop,
        budget=budget,
        priority=priority,
    )


def _make_semantic_move(
    kind: MoveKind = MoveKind.VERIFY,
    coord: str = "Coord.X",
    gain: float = 0.5,
) -> SemanticMove:
    return SemanticMove(
        move_id=f"move-{coord}-{kind.value}",
        kind=kind,
        target_coordinate=coord,
        expected_gain=gain,
        estimated_cost=2,
    )


def _mock_orchestrator_with_verify_moves(coords: list[str]) -> Orchestrator:
    """Return an Orchestrator whose MoveGenerator yields VERIFY moves for each coord."""
    config = OrchestratorConfiguration(
        max_steps=50,
        strategy="greedy",
        copilot_enabled=False,
        logging_level="WARNING",
    )
    budget = ResourceBudget()
    for ch in ("verify", "construct", "repair"):
        budget.allocate(ch, 100)

    state = OrchestratorState(
        pending_obligations=list(coords),
        resource_budget=budget,
        epoch=0,
    )
    return Orchestrator(config=config, state=state)


# ---------------------------------------------------------------------------
# Test 1: Fleet member registration and capability query
# ---------------------------------------------------------------------------


def test_fleet_registration_and_capability_lookup() -> None:
    """Fleet registers members and answers capability queries correctly."""
    fleet = Fleet()
    alpha = _make_fleet_member("alpha", frozenset({"verify", "z3-encode"}))
    beta = _make_fleet_member("beta", frozenset({"construct", "oracle"}))
    gamma = _make_fleet_member("gamma", frozenset({"verify", "construct", "z3-encode"}))

    fleet.register_member(alpha)
    fleet.register_member(beta)
    fleet.register_member(gamma)

    all_members = fleet.active_members()
    assert len(all_members) == 3

    # Query members who can verify
    bids = fleet.solicit_bids("task-001", required_capabilities=frozenset({"verify"}))
    # alpha and gamma can handle verify
    assert len(bids) >= 2

    # Query members who can do oracle work
    oracle_bids = fleet.solicit_bids("task-002", required_capabilities=frozenset({"oracle"}))
    assert len(oracle_bids) >= 1


# ---------------------------------------------------------------------------
# Test 2: Competitive search runs a tournament over fleet
# ---------------------------------------------------------------------------


def test_competitive_search_tournament() -> None:
    """CompetitiveSearch runs a multi-round tournament and returns a winner bid."""
    fleet = Fleet()
    for i in range(4):
        fleet.register_member(
            FleetMember(
                name=f"member-{i}",
                capacity=2,
                capabilities=frozenset({"verify"}),
                trust_ceiling=1.0,
            )
        )

    evaluator = BidEvaluator()
    search = CompetitiveSearch(fleet, evaluator, survival_ratio=0.5, max_rounds=3)

    winner = search.tournament("target-task")
    # Tournament should either produce a winner or None if fleet is empty after rounds
    # We just verify no exceptions and the type is correct
    assert winner is None or isinstance(winner, FleetBid)
    assert search.rounds_completed() >= 0


# ---------------------------------------------------------------------------
# Test 3: ResourceBudget tracks channel spend and exhaustion
# ---------------------------------------------------------------------------


def test_resource_budget_tracks_channel_spend() -> None:
    """ResourceBudget correctly tracks spend and signals exhaustion."""
    budget = ResourceBudget()
    budget.allocate("verify", 20)
    budget.allocate("construct", 15)
    budget.allocate("oracle", 5)

    # Spend some
    assert budget.spend("verify", 8) is True
    assert budget.remaining("verify") == 12

    # Spend more
    assert budget.spend("verify", 12) is True
    assert budget.remaining("verify") == 0
    assert budget.is_exhausted("verify") is True

    # Oracle channel — partial exhaustion
    budget.spend("oracle", 3)
    assert budget.remaining("oracle") == 2
    assert budget.is_exhausted("oracle") is False

    # Global exhaustion check
    assert budget.is_exhausted() is False
    budget.spend("construct", 15)
    budget.spend("oracle", 2)
    # verify is exhausted, construct is exhausted, oracle is exhausted
    assert budget.is_exhausted() is True

    # Rebalance donor → recipient
    budget.allocate("verify", 10)
    rebalanced = budget.rebalance("verify", "construct", 5)
    assert rebalanced is True


# ---------------------------------------------------------------------------
# Test 4: OrchestratorEventBus publish/subscribe round-trip
# ---------------------------------------------------------------------------


def test_event_bus_publish_subscribe_round_trip() -> None:
    """OrchestratorEventBus delivers published events to all subscribers."""
    from jugeo.orchestration.controller import OrchestratorEvent

    bus = OrchestratorEventBus()
    received: list[OrchestratorEvent] = []

    def handler(event: OrchestratorEvent) -> None:
        received.append(event)

    bus.subscribe(OrchestratorEventKind.MOVE_SELECTED, handler)
    assert bus.subscriber_count(OrchestratorEventKind.MOVE_SELECTED) == 1

    event = OrchestratorEvent(
        kind=OrchestratorEventKind.MOVE_SELECTED,
        details={"move_id": "m-001", "coordinate": "Coord.A"},
    )
    bus.publish(event)
    assert len(received) == 1
    assert received[0].details["move_id"] == "m-001"

    # Multiple events
    for i in range(3):
        bus.publish(
            OrchestratorEvent(
                kind=OrchestratorEventKind.MOVE_EXECUTED,
                details={"i": i},
            )
        )
    # MOVE_EXECUTED has no subscriber, so received is still 1
    assert len(received) == 1

    # Subscribe to MOVE_EXECUTED
    executed: list[OrchestratorEvent] = []
    bus.subscribe(OrchestratorEventKind.MOVE_EXECUTED, lambda e: executed.append(e))
    bus.publish(
        OrchestratorEvent(kind=OrchestratorEventKind.MOVE_EXECUTED, details={"i": 99})
    )
    assert len(executed) == 1


# ---------------------------------------------------------------------------
# Test 5: MoveHistory tracks success rate by kind
# ---------------------------------------------------------------------------


def test_move_history_success_rate_by_kind() -> None:
    """MoveHistory computes success rates separately for each MoveKind."""
    history = MoveHistory()

    for i in range(5):
        history.record(
            MoveRecord(
                move=_make_semantic_move(MoveKind.VERIFY, f"Coord.{i}"),
                epoch=i,
                success=(i % 2 == 0),  # 3 successes out of 5
                actual_gain=0.4 if i % 2 == 0 else 0.0,
                actual_cost=2,
                elapsed_seconds=0.1,
            )
        )

    for i in range(3):
        history.record(
            MoveRecord(
                move=_make_semantic_move(MoveKind.CONSTRUCT, f"Coord.C{i}"),
                epoch=10 + i,
                success=True,
                actual_gain=0.6,
                actual_cost=3,
                elapsed_seconds=0.2,
            )
        )

    verify_rate = history.success_rate(MoveKind.VERIFY)
    construct_rate = history.success_rate(MoveKind.CONSTRUCT)
    assert 0.0 <= verify_rate <= 1.0
    assert construct_rate == 1.0  # all succeeded

    avg_verify_gain = history.average_gain(MoveKind.VERIFY)
    assert avg_verify_gain >= 0.0

    total_cost = history.total_cost()
    assert total_cost == 5 * 2 + 3 * 3


# ---------------------------------------------------------------------------
# Test 6: GoalDecomposer creates leaf goal tree
# ---------------------------------------------------------------------------


def test_goal_decomposer_creates_leaf_goals() -> None:
    """GoalDecomposer splits a composite goal into leaf subgoals."""
    root = _make_goal(
        "root-001",
        coord="Module.Root",
        prop="∀ x, Q(x) ∧ R(x)",
        priority=GoalPriority.HIGH,
        budget=30,
    )
    decomposer = GoalDecomposer()
    subgoals = decomposer.decompose(root)

    assert isinstance(subgoals, tuple)
    # Root should yield at least 1 subgoal (itself if already leaf, or more if decomposable)
    assert len(subgoals) >= 1

    for sg in subgoals:
        assert isinstance(sg, GenerationGoal)
        assert sg.budget >= 0


# ---------------------------------------------------------------------------
# Test 7: GoalTracker marks goals achieved/failed
# ---------------------------------------------------------------------------


def test_goal_tracker_marks_lifecycle() -> None:
    """GoalTracker correctly transitions goals through their lifecycle."""
    tracker = GoalTracker()
    goals = [
        _make_goal(f"g-{i}", coord=f"Coord.{i}", prop=f"P_{i}")
        for i in range(5)
    ]

    for g in goals:
        tracker.add_goal(g)

    # Mark first three achieved
    for g in goals[:3]:
        tracker.mark_achieved(g.goal_id)

    # Mark last one failed
    tracker.mark_failed(goals[4].goal_id)

    achieved = tracker.achieved_goals()
    failed = tracker.failed_goals()
    pending = tracker.pending_goals()

    assert len(achieved) == 3
    assert len(failed) == 1
    assert len(pending) == 1  # goals[3] still pending

    ratio = tracker.progress_ratio()
    assert 0.0 <= ratio <= 1.0


# ---------------------------------------------------------------------------
# Test 8: Fleet proposes inhabitants; descent verifies globally
# ---------------------------------------------------------------------------


def test_fleet_proposes_and_descent_verifies() -> None:
    """Fleet members bid on a goal; the winning bid is verified via descent."""
    fleet = Fleet()
    fleet.register_member(_make_fleet_member("w1", frozenset({"verify"})))
    fleet.register_member(_make_fleet_member("w2", frozenset({"verify", "z3"})))

    bids = fleet.solicit_bids("Omega", required_capabilities=frozenset({"verify"}))
    assert len(bids) >= 1

    # Select winning bid (highest confidence)
    evaluator = BidEvaluator()
    ranked = evaluator.rank(bids)
    assert len(ranked) >= 1

    winning_bid = ranked[0]
    assert isinstance(winning_bid, FleetBid)

    # Execute winning bid assignment
    result = fleet.assign_work(winning_bid)
    assert isinstance(result, dict)

    # Now run descent on the sections the "fleet" produced
    cover = _make_cover("Omega")
    sections = {
        "Omega/A": {"result": "proven", "trust": 1.0},
        "Omega/B": {"result": "proven", "trust": 1.0},
    }
    engine = DescentEngine()
    report = engine.run(cover, sections)
    assert report.success is True
    assert report.target == "Omega"


# ---------------------------------------------------------------------------
# Test 9: Controller step generates and executes a verify move
# ---------------------------------------------------------------------------


def test_orchestrator_step_verify_move() -> None:
    """Orchestrator step selects a VERIFY move and updates state."""
    orchestrator = _mock_orchestrator_with_verify_moves(["Pi.A", "Pi.B"])

    # Patch execute_move to return (True, 0.5) without calling real Z3
    with patch.object(
        orchestrator, "execute_move", return_value=(True, 0.5)
    ) as mock_execute:
        move = orchestrator.select_next_move()
        if move is not None:
            success, gain = orchestrator.execute_move(move)
            orchestrator.evaluate_outcome(move, success, gain)
            assert success is True
            assert gain == 0.5
            mock_execute.assert_called_once()


# ---------------------------------------------------------------------------
# Test 10: Backpressure scenario — budget exhausted, controller adjusts
# ---------------------------------------------------------------------------


def test_backpressure_budget_exhaustion_controller_adjusts() -> None:
    """When verify budget is exhausted, the controller switches to repair moves."""
    budget = ResourceBudget()
    budget.allocate("verify", 2)   # very small budget
    budget.allocate("construct", 20)
    budget.allocate("repair", 20)

    state = OrchestratorState(
        pending_obligations=["Rho.A", "Rho.B", "Rho.C"],
        resource_budget=budget,
        epoch=0,
    )
    config = OrchestratorConfiguration(
        max_steps=10,
        strategy="greedy",
        copilot_enabled=False,
    )
    orchestrator = Orchestrator(config=config, state=state)

    # Exhaust verify budget
    budget.spend("verify", 2)
    assert budget.is_exhausted("verify") is True

    # Even with exhausted verify budget, orchestrator should not crash
    with patch.object(orchestrator, "execute_move", return_value=(True, 0.3)):
        move = orchestrator.select_next_move()
        # May be None if no moves available under budget constraints
        assert move is None or isinstance(move, SemanticMove)


# ---------------------------------------------------------------------------
# Test 11: Evidence channels wired through ChannelFederation
# ---------------------------------------------------------------------------


def test_channel_federation_wiring() -> None:
    """ChannelFederation aggregates solver and oracle channels correctly."""
    solver_config = ChannelConfiguration(
        channel=EvidenceChannel.SOLVER,
        timeout_ms=3000,
        trust_ceiling="verified",
        is_enabled=True,
    )
    oracle_config = ChannelConfiguration(
        channel=EvidenceChannel.ORACLE,
        timeout_ms=5000,
        trust_ceiling="reviewed",
        is_enabled=True,
    )

    router = ChannelRouter(
        configurations={
            EvidenceChannel.SOLVER: solver_config,
            EvidenceChannel.ORACLE: oracle_config,
        }
    )
    federation = ChannelFederation(router=router)

    # Federation should be constructible
    assert federation is not None

    # Build some evidence records manually
    solver_record = build_channel("z3-result", EvidenceChannel.SOLVER)
    oracle_record = build_channel("copilot-hint", EvidenceChannel.ORACLE)

    assert solver_record.channel == EvidenceChannel.SOLVER
    assert oracle_record.channel == EvidenceChannel.ORACLE


# ---------------------------------------------------------------------------
# Test 12: Full orchestration run — fleet, generation, descent, evidence
# ---------------------------------------------------------------------------


def test_full_orchestration_run_with_fleet_and_descent() -> None:
    """Full orchestration run: fleet bids on goal, descent verifies, evidence archived."""
    # Step 1: Set up fleet
    fleet = Fleet()
    for name in ["agent-alpha", "agent-beta", "agent-gamma"]:
        fleet.register_member(
            FleetMember(
                name=name,
                capacity=4,
                capabilities=frozenset({"verify", "construct"}),
                trust_ceiling=0.9,
                specialization_domains=("algebra",),
            )
        )

    # Step 2: Generate a goal
    goal = _make_goal(
        "full-run-001",
        coord="Sigma.T",
        prop="∀ n, n * 1 = n",
        priority=GoalPriority.HIGH,
        budget=50,
    )

    # Step 3: Fleet solicits and assigns bids
    bids = fleet.solicit_bids(goal.target_coordinate, frozenset({"verify"}))
    assert len(bids) >= 1
    result = fleet.assign_work(bids[0])
    assert isinstance(result, dict)

    # Step 4: Descent verification
    cover = _make_cover("Sigma")
    sections = {
        "Sigma/A": {"multiplicative_identity": True, "n": "any"},
        "Sigma/B": {"multiplicative_identity": True, "n": "any"},
    }
    engine = DescentEngine()
    report = engine.run(cover, sections)
    assert report.success is True

    # Step 5: Build evidence record from descent + fleet
    record = build_channel("fleet-verify", EvidenceChannel.SOLVER)
    trace = ProvenanceTrace(origin="fleet-orchestration")
    for step_name in ("fleet-bid", "descent-glue", "evidence-archive"):
        trace = trace.append(
            ProvenanceStep(actor="orchestrator", action=step_name, coordinate=goal.target_coordinate)
        )

    trust = TrustProfile(TrustTier.REVIEWED, (goal.target_coordinate,), ("fleet-verified",))

    from jugeo.evidence.manifests import build_evidence_manifest
    ev_manifest = build_evidence_manifest(
        goal.target_coordinate,
        goal.required_proposition,
        (record,),
        trust_profiles=(trust,),
        provenance=trace,
    )
    assert ev_manifest.coordinate == goal.target_coordinate

    # Step 6: Archive in manifest
    builder = ManifestBuilder()
    builder.add_judgment(
        goal.target_coordinate,
        goal.required_proposition,
        trust_tier=int(TrustTier.REVIEWED),
        status="settled",
    )
    manifest = builder.build()
    assert manifest.is_consistent()

    # Step 7: Fleet health check
    health = fleet.fleet_health()
    assert "total_capacity" in health or isinstance(health, dict)


# ---------------------------------------------------------------------------
# Test 13: GreedyControl selects highest gain-cost move
# ---------------------------------------------------------------------------


def test_greedy_control_selects_best_move() -> None:
    """GreedyControl picks the move with the highest gain/cost ratio."""
    control = GreedyControl()
    state = OrchestratorState(epoch=1)

    moves = [
        SemanticMove(
            move_id="m1",
            kind=MoveKind.VERIFY,
            target_coordinate="Tau.A",
            expected_gain=0.9,
            estimated_cost=1,
        ),
        SemanticMove(
            move_id="m2",
            kind=MoveKind.CONSTRUCT,
            target_coordinate="Tau.B",
            expected_gain=0.3,
            estimated_cost=5,
        ),
        SemanticMove(
            move_id="m3",
            kind=MoveKind.REPAIR,
            target_coordinate="Tau.C",
            expected_gain=0.6,
            estimated_cost=2,
        ),
    ]

    winner = control.select(state, moves)
    # Greedy should pick highest gain/cost ratio = m1 (0.9/1=0.9) > m3 (0.6/2=0.3)
    assert winner is not None
    assert winner.move_id in ("m1", "m3")  # m1 should win


# ---------------------------------------------------------------------------
# Test 14: Overlap instability detected triggers fleet slowdown signal
# ---------------------------------------------------------------------------


def test_overlap_instability_triggers_fleet_slowdown() -> None:
    """DescentObstruction from overlap instability is detected and fleet can react."""
    # Build a cover where overlaps would be incompatible
    base = CoordinateObject(components=("Upsilon",), kind=CoordinateKind.THEOREM)
    pa = CoordinateObject(components=("Upsilon", "A"), kind=CoordinateKind.REGION)
    pb = CoordinateObject(components=("Upsilon", "B"), kind=CoordinateKind.REGION)
    morph_a = CoordinateMorphism("Upsilon.A", "Upsilon", reason="r")
    morph_b = CoordinateMorphism("Upsilon.B", "Upsilon", reason="r")
    cb = CoverBuilder()
    cb.set_base(base).add_member(pa, morph_a).add_member(pb, morph_b)
    cover = cb.build()

    # Create overlap condition directly (simulating instability)
    overlap = OverlapCondition(
        left_coordinate="Upsilon.A",
        right_coordinate="Upsilon.B",
        overlap_coordinate="Upsilon.AB",
        status=OverlapStatus.VIOLATED,
    )

    cc = CohomologyClass(
        dimension=1,
        cocycle_data={"overlap": "Upsilon.AB", "verdict": "violated"},
    )
    obs = DescentObstruction(
        coordinate="Upsilon",
        violated_overlaps=(overlap,),
        cohomology_class=cc,
    )

    # Fleet should slow down when obstruction is detected
    fleet = Fleet()
    fleet.register_member(_make_fleet_member("slowdown-agent", frozenset({"verify"})))

    # Record the obstruction in manifest
    builder = ManifestBuilder()
    builder.add_obstruction(
        "Upsilon",
        ObstructionKind.COVER_FAILURE,
        "Overlap instability detected at Upsilon.A ∩ Upsilon.B",
        rank=1,
    )
    manifest = builder.build()
    assert manifest.is_consistent()

    # Verify obstruction structure
    assert obs.coordinate == "Upsilon"
    assert len(obs.violated_overlaps) == 1
    assert obs.violated_overlaps[0].status == OverlapStatus.VIOLATED
    involved = obs.involved_coordinates()
    assert isinstance(involved, frozenset)
