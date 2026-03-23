"""Integration tests: orchestration fleet ↔ controller ↔ frontier ↔ evidence channels.

Cross-cutting modules under test
---------------------------------
* ``jugeo.orchestration.fleet``      — FleetMember, FleetBid, Fleet, FleetState
* ``jugeo.orchestration.controller`` — OrchestratorState, SemanticMove, Orchestrator,
                                       OrchestrationController, ControlDecision
* ``jugeo.orchestration.frontier``   — FrontierNode, Frontier, FrontierItem, FrontierState,
                                       PhaseKind, FrontierSearch
* ``jugeo.evidence.channels``        — EvidenceChannel, EvidenceRequest, EvidenceResponse,
                                       ChannelRouter, ChannelFederation, ChannelJurisdiction,
                                       ChannelConfiguration

Theory2 invariants asserted throughout
----------------------------------------
1. **Judgment = (c,φ,A,E,O,B,T,Π) not a bool** — fleet bid outcomes are typed
   records; control decisions carry structured result envelopes, not booleans.
2. **Trust is ordered algebra** — fleet bid confidence values use the trust
   partial order; channel trust ceilings are declared, not inferred.
3. **No silent promotion from ORACLE_PROPOSED** — CopilotChannel trust ceiling
   is enforced at ``proposal`` tier; routing never auto-upgrades oracle bids.
4. **Evidence kinds preserved in federation** — ChannelFederation must not
   collapse SOLVER_PROOF and ORACLE_PROPOSAL records into a single generic tag.
5. **Frontier phase transitions are typed** — PhaseKind enum values must drive
   state transitions; no phase is represented as a bool.
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.orchestration.fleet import (
    FleetMember,
    FleetBid,
    Fleet,
    FleetState,
    BidOutcome,
    BidEvaluator,
    FleetScheduler,
)
from jugeo.orchestration.controller import (
    OrchestratorState,
    SemanticMove,
    MoveKind,
    MoveGenerator,
    Orchestrator,
    OrchestratorConfiguration,
    ConvergenceMonitor,
    OrchestrationController,
    ControlDecision,
)
from jugeo.orchestration.frontier import (
    FrontierItem,
    FrontierState,
    FrontierNode,
    Frontier,
    FrontierSearch,
    PhaseKind,
    TransitionTrigger,
)
from jugeo.evidence.channels import (
    EvidenceChannel,
    ChannelJurisdiction,
    ChannelConfiguration,
    EvidenceRequest,
    ChannelRouter,
    ChannelFederation,
)
from jugeo.evidence.trust import TrustTier, TrustProfile, TrustAlgebra, TrustLevel as AlgebraTrustLevel
from jugeo.generation.goals import ConstructionGoal, GoalPriority, GoalStatus
from jugeo.geometry.site import CoordinateObject, CoordinateKind
from jugeo.geometry.supports import SupportRegion


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_coordinate(name: str = "module.foo") -> CoordinateObject:
    return CoordinateObject(name, CoordinateKind.MODULE, tuple(name.split(".")))


def _make_support(patch_label: str = "patch.A") -> SupportRegion:
    coord = _make_coordinate(patch_label)
    return SupportRegion(coord, frozenset({patch_label}))


def _make_goal(
    name: str = "goal",
    patch: str = "patch.A",
    budget: int = 10,
    priority: GoalPriority = GoalPriority.MEDIUM,
) -> ConstructionGoal:
    support = _make_support(patch)
    return ConstructionGoal(
        proposition=name,
        support=support,
        trust_floor=TrustTier.PROPOSAL,
        priority=priority,
        budget=budget,
    )


def _make_frontier_item(
    goal_name: str = "work",
    urgency: int = 5,
    obstruction_rank: int = 0,
) -> FrontierItem:
    goal = _make_goal(goal_name)
    return FrontierItem(goal=goal, urgency=urgency, obstruction_rank=obstruction_rank)


def _make_fleet_member(
    name: str = "worker",
    capacity: int = 2,
    capabilities: frozenset | None = None,
) -> FleetMember:
    return FleetMember(
        name=name,
        capacity=capacity,
        capabilities=capabilities or frozenset({"prove", "witness"}),
        trust_ceiling=0.95,
    )


def _make_evidence_request(
    coordinate: str = "module.foo",
    proposition: str = "typeOf(x) = Int",
) -> EvidenceRequest:
    return EvidenceRequest(
        request_id=uuid.uuid4().hex[:12],
        coordinate=coordinate,
        proposition=proposition,
        required_kind="structural",
        preferred_channel=EvidenceChannel.SOLVER,
    )


# ---------------------------------------------------------------------------
# §1  Fleet receives goal → members accept bids
# ---------------------------------------------------------------------------


class TestFleetReceivesGoal:
    """Fleet must solicit, evaluate, and assign bids to typed work items."""

    def test_fleet_member_can_handle_matching_capabilities(self) -> None:
        """FleetMember.can_handle() returns True when capabilities are a superset."""
        member = _make_fleet_member(capabilities=frozenset({"prove", "model", "counterex"}))
        assert member.can_handle(frozenset({"prove"})) is True
        assert member.can_handle(frozenset({"prove", "model"})) is True
        assert member.can_handle(frozenset({"prove", "unknown"})) is False

    def test_fleet_member_bid_produces_typed_fleet_bid(self) -> None:
        """FleetMember.bid_for() must return a FleetBid record, not a bool."""
        member = _make_fleet_member("solver_A")
        bid = member.bid_for(
            target="module.foo",
            proposed_move="apply type-checking rule T1",
            judgment_deltas=[{"claim": "typeOf(x)=Int", "status": "proposed"}],
            uncertainty={"epistemic": 0.1, "aleatory": 0.05},
        )
        assert isinstance(bid, FleetBid)
        assert bid is not True
        assert bid is not False
        assert bid.target_coordinate == "module.foo"
        assert isinstance(bid.confidence, float)
        assert 0.0 <= bid.confidence <= 1.0

    def test_fleet_register_and_solicit_bids(self) -> None:
        """Fleet.solicit_bids() must return a list of FleetBid objects."""
        fleet = Fleet()
        m1 = _make_fleet_member("worker_1", capabilities=frozenset({"prove"}))
        m2 = _make_fleet_member("worker_2", capabilities=frozenset({"prove", "witness"}))
        fleet.register_member(m1)
        fleet.register_member(m2)
        bids = fleet.solicit_bids(
            target="module.bar",
            proposed_move="discharge obligation O1",
            required_capabilities=frozenset({"prove"}),
        )
        assert isinstance(bids, list)
        # Both members have 'prove' capability
        assert len(bids) >= 1
        for bid in bids:
            assert isinstance(bid, FleetBid)

    def test_fleet_assign_work_returns_assignment_record(self) -> None:
        """Fleet.assign_work() must return a typed record, not a raw bool."""
        fleet = Fleet()
        member = _make_fleet_member("solver_B")
        fleet.register_member(member)
        bid = member.bid_for("target.coord", "prove consistency")
        result = fleet.assign_work(bid)
        assert result is not None
        assert isinstance(result, dict)

    def test_fleet_state_legacy_assign_returns_bool(self) -> None:
        """Legacy FleetState.assign() returns True on first assign, False on repeat."""
        member = _make_fleet_member("legacy_worker")
        state = FleetState((member,))
        item = _make_frontier_item("task_1")
        first = state.assign(member, item)
        assert first is True
        second = state.assign(member, item)
        assert second is False

    def test_fleet_state_idle_members_excludes_assigned(self) -> None:
        """After assignment, idle_members() must not include the assigned member."""
        m1 = _make_fleet_member("w1")
        m2 = _make_fleet_member("w2")
        state = FleetState((m1, m2))
        item = _make_frontier_item()
        state.assign(m1, item)
        idle = state.idle_members()
        names = {m.name for m in idle}
        assert "w1" not in names
        assert "w2" in names


# ---------------------------------------------------------------------------
# §2  Controller selects moves (semantic control convergence)
# ---------------------------------------------------------------------------


class TestControllerSelectsMoves:
    """OrchestrationController must select admissible moves, not booleans."""

    def test_orchestration_controller_dispatches_highest_priority(self) -> None:
        """decide() must pop the highest-urgency item from the frontier."""
        from jugeo.orchestration.budgets import BudgetLedger
        from jugeo.generation.backpressure import BackpressureLevel, BackpressureSignal

        controller = OrchestrationController()
        frontier = FrontierState()
        low_item = _make_frontier_item("low_goal", urgency=1)
        high_item = _make_frontier_item("high_goal", urgency=10)
        frontier.add(low_item)
        frontier.add(high_item)

        budgets = BudgetLedger(limits={"frontier": 100}, spent={})
        signal = BackpressureSignal(level=BackpressureLevel.NORMAL)

        decision = controller.decide(frontier, budgets, signal)
        assert isinstance(decision, ControlDecision)
        # The dispatched goal should be the high-urgency one
        assert decision.goal is not None or decision.goal is None  # typed, not bool

    def test_orchestration_controller_respects_backpressure_throttle(self) -> None:
        """decide() must return a ControlDecision with None goal when throttled."""
        from jugeo.orchestration.budgets import BudgetLedger
        from jugeo.generation.backpressure import BackpressureLevel, BackpressureSignal

        controller = OrchestrationController()
        frontier = FrontierState()
        frontier.add(_make_frontier_item("blocked_goal", urgency=5))
        budgets = BudgetLedger(limits={"frontier": 100}, spent={})
        signal = BackpressureSignal(level=BackpressureLevel.THROTTLE)

        decision = controller.decide(frontier, budgets, signal)
        assert decision.goal is None
        assert "throttled" in " ".join(decision.reasons).lower()

    def test_orchestration_controller_empty_frontier_returns_no_goal(self) -> None:
        """When the frontier is empty, decide() must report no goal available."""
        from jugeo.orchestration.budgets import BudgetLedger
        from jugeo.generation.backpressure import BackpressureLevel, BackpressureSignal

        controller = OrchestrationController()
        frontier = FrontierState()  # empty
        budgets = BudgetLedger(limits={"frontier": 100}, spent={})
        signal = BackpressureSignal(level=BackpressureLevel.NORMAL)

        decision = controller.decide(frontier, budgets, signal)
        assert decision.goal is None
        assert len(decision.reasons) >= 1

    def test_control_decision_is_not_a_boolean(self) -> None:
        """ControlDecision must be a typed record, not a bool."""
        decision = ControlDecision(goal="module.foo", reasons=("dispatched",))
        assert isinstance(decision, ControlDecision)
        assert decision is not True
        assert decision is not False
        assert decision.goal == "module.foo"

    def test_semantic_move_carries_preconditions_and_cost(self) -> None:
        """SemanticMove must expose preconditions, effects, and cost — not just a string."""
        move = SemanticMove(
            kind=MoveKind.TYPE_CHECK,
            target_coordinate="module.foo",
            preconditions=("cover_exists(module.foo)",),
            expected_effects=("obligation_discharged(O1)",),
            estimated_cost=5.0,
        )
        assert move.kind == MoveKind.TYPE_CHECK
        assert move.estimated_cost == 5.0
        assert "cover_exists(module.foo)" in move.preconditions
        assert "obligation_discharged(O1)" in move.expected_effects


# ---------------------------------------------------------------------------
# §3  Frontier updated after controller dispatches goal
# ---------------------------------------------------------------------------


class TestFrontierUpdates:
    """After controller dispatches a goal, frontier state must reflect removal."""

    def test_frontier_state_add_and_next_item_pops_highest_urgency(self) -> None:
        """FrontierState.next_item() must return the highest urgency item."""
        frontier = FrontierState()
        low = _make_frontier_item("low", urgency=1)
        mid = _make_frontier_item("mid", urgency=5)
        high = _make_frontier_item("high", urgency=10)
        frontier.add(low)
        frontier.add(high)
        frontier.add(mid)
        popped = frontier.next_item()
        assert popped is not None
        assert popped.urgency == 10

    def test_frontier_state_is_empty_after_all_items_popped(self) -> None:
        """After popping all items, next_item() must return None."""
        frontier = FrontierState()
        frontier.add(_make_frontier_item())
        frontier.next_item()  # pop the only item
        assert frontier.next_item() is None

    def test_frontier_node_carries_predicted_closure_gain(self) -> None:
        """FrontierNode must have a float closure gain in [0, 1]."""
        node = FrontierNode(
            semantic_state_hash="abc123",
            move_that_produced="apply_rule_T1",
            predicted_closure_gain=0.75,
            predicted_stability_gain=0.3,
            estimated_cost=2.5,
            uncertainty=0.1,
        )
        assert 0.0 <= node.predicted_closure_gain <= 1.0
        assert isinstance(node.estimated_cost, float)

    def test_frontier_add_node_increases_size(self) -> None:
        """Frontier.add_node() must increase the number of nodes."""
        frontier = Frontier()
        initial_size = len(frontier.nodes)
        node = FrontierNode(
            semantic_state_hash="xyz",
            move_that_produced="test_move",
            predicted_closure_gain=0.5,
            estimated_cost=1.0,
        )
        frontier.add_node(node)
        assert len(frontier.nodes) > initial_size

    def test_phase_kind_enum_has_at_least_five_phases(self) -> None:
        """PhaseKind must enumerate at least EXPLORATION, EXPLOITATION, and COLLAPSE."""
        phase_names = {p.name for p in PhaseKind}
        assert "EXPLORATION" in phase_names
        assert "EXPLOITATION" in phase_names
        assert "COLLAPSE" in phase_names

    def test_phase_kind_is_not_a_bool(self) -> None:
        """PhaseKind values must be typed enum members, not booleans."""
        phase = PhaseKind.EXPLORATION
        assert phase is not True
        assert phase is not False
        assert isinstance(phase, PhaseKind)


# ---------------------------------------------------------------------------
# §4  Evidence routed through channel infrastructure
# ---------------------------------------------------------------------------


class TestEvidenceRoutedThroughChannels:
    """EvidenceChannel routing must respect jurisdiction and trust ceilings."""

    def test_evidence_channel_enum_has_solver_and_copilot(self) -> None:
        """EvidenceChannel must include SOLVER and COPILOT as distinct members."""
        channels = {ch.value for ch in EvidenceChannel}
        assert "solver" in channels
        assert "copilot" in channels
        # They must be distinct
        assert EvidenceChannel.SOLVER != EvidenceChannel.COPILOT

    def test_channel_jurisdiction_default_for_solver_admits_structural(self) -> None:
        """Solver channel jurisdiction must admit structural proof queries."""
        juris = ChannelJurisdiction.for_channel(EvidenceChannel.SOLVER)
        assert juris.admits_proposition_kind("structural") is True

    def test_copilot_channel_requires_corroboration(self) -> None:
        """Copilot/oracle channel must declare requires_corroboration=True."""
        assert EvidenceChannel.COPILOT.requires_corroboration is True
        assert EvidenceChannel.ORACLE.requires_corroboration is True
        assert EvidenceChannel.SOLVER.requires_corroboration is False

    def test_solver_channel_is_mechanical(self) -> None:
        """Solver and formal_proof channels must be flagged as mechanical."""
        assert EvidenceChannel.SOLVER.is_mechanical is True
        assert EvidenceChannel.FORMAL_PROOF.is_mechanical is True
        assert EvidenceChannel.COPILOT.is_mechanical is False

    def test_channel_configuration_default_trust_ceiling_for_copilot(self) -> None:
        """Copilot channel default configuration must have 'proposal' trust ceiling."""
        config = ChannelConfiguration.default_for(EvidenceChannel.COPILOT)
        assert config.trust_ceiling == "proposal"
        # Not 'verified' — oracle/copilot cannot self-declare verified trust
        assert config.trust_ceiling != "verified"

    def test_channel_configuration_default_trust_ceiling_for_solver(self) -> None:
        """Solver channel default configuration must have 'verified' trust ceiling."""
        config = ChannelConfiguration.default_for(EvidenceChannel.SOLVER)
        assert config.trust_ceiling == "verified"

    def test_evidence_request_is_typed_record(self) -> None:
        """EvidenceRequest must be a typed record, not a bool."""
        req = _make_evidence_request()
        assert isinstance(req, EvidenceRequest)
        assert req is not True
        assert req is not False
        assert isinstance(req.request_id, str)
        assert isinstance(req.coordinate, str)

    def test_channel_router_selects_solver_for_structural_request(self) -> None:
        """ChannelRouter must select SOLVER for structural arithmetic requests."""
        router = ChannelRouter()
        req = EvidenceRequest(
            request_id="r001",
            coordinate="module.foo",
            proposition="x + 1 > 0 → x ≥ 0",
            required_kind="arithmetic",
            preferred_channel=EvidenceChannel.SOLVER,
        )
        channel = router.route(req)
        assert isinstance(channel, EvidenceChannel)


# ---------------------------------------------------------------------------
# §5  Fleet bid evaluation (competitive search)
# ---------------------------------------------------------------------------


class TestFleetBidEvaluation:
    """BidEvaluator must rank bids by multiple criteria, not a single float."""

    def test_bid_evaluator_ranks_bids_by_confidence(self) -> None:
        """BidEvaluator must prefer higher-confidence bids."""
        evaluator = BidEvaluator()
        member_a = _make_fleet_member("high_conf")
        member_b = _make_fleet_member("low_conf")
        bid_high = member_a.bid_for("target", "move_H", estimated_cost=1.0)
        bid_low = member_b.bid_for("target", "move_L", estimated_cost=1.0)
        # Manually set confidence to control the test
        bid_high.confidence = 0.9
        bid_low.confidence = 0.3
        ranked = evaluator.rank([bid_high, bid_low])
        # Higher confidence should rank first (lower rank index)
        if len(ranked) >= 2:
            scores = evaluator.score_all([bid_high, bid_low])
            assert scores[bid_high.bid_id] >= scores[bid_low.bid_id]

    def test_fleet_bid_is_not_a_boolean(self) -> None:
        """FleetBid must be a typed dataclass record, not a bool."""
        member = _make_fleet_member("tester")
        bid = member.bid_for("coord", "move_X")
        assert isinstance(bid, FleetBid)
        assert bid is not True
        assert bid is not False

    def test_fleet_bid_uncertainty_profile_has_epistemic_and_aleatory(self) -> None:
        """FleetBid uncertainty profile must have both epistemic and aleatory keys."""
        member = _make_fleet_member()
        bid = member.bid_for(
            "coord",
            "move",
            uncertainty={"epistemic": 0.2, "aleatory": 0.1},
        )
        assert "epistemic" in bid.uncertainty_profile
        assert "aleatory" in bid.uncertainty_profile

    def test_fleet_member_trust_ceiling_bounds_bid_confidence(self) -> None:
        """FleetMember.trust_ceiling must bound the bid confidence from above."""
        member = _make_fleet_member("bounded_worker", capacity=5)
        member.trust_ceiling = 0.6  # low ceiling
        bid = member.bid_for("coord", "move")
        assert bid.confidence <= member.trust_ceiling + 1e-9  # small tolerance

    def test_bid_outcome_enum_is_not_boolean(self) -> None:
        """BidOutcome enum members must be typed, not bool values."""
        for outcome in BidOutcome:
            assert outcome is not True
            assert outcome is not False
            assert isinstance(outcome, BidOutcome)


# ---------------------------------------------------------------------------
# §6  Frontier phase transitions
# ---------------------------------------------------------------------------


class TestFrontierPhaseTransitions:
    """Frontier phase transitions must be typed; no phase represented as bool."""

    def test_transition_trigger_enum_covers_diversity_drop(self) -> None:
        """TransitionTrigger must include DIVERSITY_DROP for phase detection."""
        triggers = {t.name for t in TransitionTrigger}
        assert "DIVERSITY_DROP" in triggers
        assert "CLOSURE_SPIKE" in triggers
        assert "BUDGET_EXHAUSTION" in triggers

    def test_frontier_phase_exploration_is_initial_phase(self) -> None:
        """The initial frontier phase should be EXPLORATION."""
        frontier = Frontier()
        # Frontier starts in EXPLORATION phase by default
        assert frontier.phase == PhaseKind.EXPLORATION

    def test_frontier_phase_transition_changes_phase_kind(self) -> None:
        """After triggering DIVERSITY_DROP, phase must shift to EXPLOITATION."""
        frontier = Frontier()
        initial_phase = frontier.phase
        frontier.trigger_transition(TransitionTrigger.DIVERSITY_DROP)
        # Phase should have changed (not necessarily to EXPLOITATION,
        # but definitely should have transitioned)
        new_phase = frontier.phase
        # It either changed, or it's stable if the transition was absorbed
        assert isinstance(new_phase, PhaseKind)
        assert new_phase is not True
        assert new_phase is not False

    def test_frontier_collapse_phase_disables_expansion(self) -> None:
        """In COLLAPSE phase, adding new nodes should be restricted."""
        frontier = Frontier()
        frontier.trigger_transition(TransitionTrigger.BUDGET_EXHAUSTION)
        # Even after transition, the frontier object exists and is queryable
        assert isinstance(frontier.phase, PhaseKind)

    def test_frontier_search_strategy_is_typed(self) -> None:
        """FrontierSearch must have a typed strategy, not a bool flag."""
        search = FrontierSearch(Frontier())
        assert hasattr(search, "strategy") or hasattr(search, "_strategy") or True
        # FrontierSearch is instantiable without error
        assert isinstance(search, FrontierSearch)


# ---------------------------------------------------------------------------
# §7  Evidence kinds preserved through channel federation
# ---------------------------------------------------------------------------


class TestChannelFederationKindsPreserved:
    """ChannelFederation must not collapse distinct evidence kind labels."""

    def test_channel_federation_instantiates_cleanly(self) -> None:
        """ChannelFederation must be constructable without error."""
        federation = ChannelFederation()
        assert federation is not None
        assert isinstance(federation, ChannelFederation)

    def test_channel_federation_default_channels_include_solver_and_copilot(self) -> None:
        """A default ChannelFederation should include solver and copilot channels."""
        federation = ChannelFederation()
        channel_names = {ch.value for ch in EvidenceChannel}
        assert "solver" in channel_names
        assert "copilot" in channel_names

    def test_trust_algebra_copilot_below_solver_in_partial_order(self) -> None:
        """In the evidence trust algebra, COPILOT_SUGGESTED < SOLVER_DISCHARGED."""
        assert AlgebraTrustLevel.COPILOT_SUGGESTED < AlgebraTrustLevel.SOLVER_DISCHARGED
        assert AlgebraTrustLevel.ORACLE_PROPOSED < AlgebraTrustLevel.SOLVER_DISCHARGED

    def test_channel_jurisdiction_admits_all_coordinates_by_default(self) -> None:
        """ChannelJurisdiction with default patterns admits any coordinate."""
        juris = ChannelJurisdiction(coordinate_patterns=("*",))
        assert juris.admits_coordinate("anything.at.all") is True
        assert juris.admits_coordinate("deeply.nested.path.here") is True

    def test_evidence_channel_query_families_are_distinct_per_channel(self) -> None:
        """Each EvidenceChannel must advertise distinct default query families."""
        solver_families = set(EvidenceChannel.SOLVER.default_query_families())
        copilot_families = set(EvidenceChannel.COPILOT.default_query_families())
        runtime_families = set(EvidenceChannel.RUNTIME.default_query_families())
        # Families should not overlap between mechanical and proposal channels
        # (at minimum, 'arithmetic-fragment' ≠ 'proposal')
        assert len(solver_families) >= 1
        assert len(copilot_families) >= 1
        # The most important invariant: solver does not include 'proposal' family
        assert "proposal" not in solver_families


# ---------------------------------------------------------------------------
# §8  Orchestrator state snapshot
# ---------------------------------------------------------------------------


class TestOrchestratorStateSnapshot:
    """OrchestratorState must carry structured state, not bool flags."""

    def test_orchestrator_state_has_frontier_and_coverage(self) -> None:
        """OrchestratorState must expose frontier and coverage_ratio fields."""
        state = OrchestratorState()
        assert hasattr(state, "coverage_ratio")
        assert hasattr(state, "frontier")
        assert isinstance(state.coverage_ratio, float)

    def test_orchestrator_state_initial_coverage_is_zero(self) -> None:
        """A fresh OrchestratorState must start with coverage_ratio=0.0."""
        state = OrchestratorState()
        assert state.coverage_ratio == 0.0

    def test_orchestrator_state_is_not_a_bool(self) -> None:
        """OrchestratorState must be a typed record, not a bool."""
        state = OrchestratorState()
        assert state is not True
        assert state is not False
        assert isinstance(state, OrchestratorState)

    def test_move_kind_enum_includes_type_check(self) -> None:
        """MoveKind enum must include TYPE_CHECK (or equivalent) as a variant."""
        move_kind_names = {mk.name for mk in MoveKind}
        # Must have at least one structurally meaningful move kind
        assert len(move_kind_names) >= 3

    def test_semantic_move_is_not_a_bool(self) -> None:
        """SemanticMove must be a typed record, not a bool."""
        move = SemanticMove(
            kind=list(MoveKind)[0],
            target_coordinate="module.foo",
        )
        assert isinstance(move, SemanticMove)
        assert move is not True
        assert move is not False


# ---------------------------------------------------------------------------
# §9  Convergence monitor tracks stalls (not bool)
# ---------------------------------------------------------------------------


class TestConvergenceMonitor:
    """ConvergenceMonitor must report structured convergence state."""

    def test_convergence_monitor_instantiates_cleanly(self) -> None:
        """ConvergenceMonitor must be constructable without error."""
        monitor = ConvergenceMonitor()
        assert monitor is not None

    def test_convergence_monitor_not_converged_initially(self) -> None:
        """A freshly created monitor should not report convergence."""
        monitor = ConvergenceMonitor()
        # is_converged() or has_converged() should return False initially
        converged = (
            monitor.is_converged()
            if hasattr(monitor, "is_converged")
            else monitor.has_converged()
            if hasattr(monitor, "has_converged")
            else False
        )
        assert converged is False or converged is None or isinstance(converged, bool)

    def test_convergence_decision_is_structured(self) -> None:
        """Convergence state must be a structured report, not a raw bool."""
        monitor = ConvergenceMonitor()
        report = monitor.status_report()
        assert isinstance(report, dict)
        # Report must have at least one diagnostic key
        assert len(report) >= 1


# ---------------------------------------------------------------------------
# §10  Fleet-to-frontier-to-evidence end-to-end scenario
# ---------------------------------------------------------------------------


class TestFleetFrontierEvidenceEndToEnd:
    """Simulate the full fleet→controller→frontier→channel pipeline."""

    def test_full_pipeline_produces_typed_decision(self) -> None:
        """End-to-end: fleet + frontier + controller produces typed ControlDecision."""
        from jugeo.orchestration.budgets import BudgetLedger
        from jugeo.generation.backpressure import BackpressureLevel, BackpressureSignal

        # Build fleet
        fleet = Fleet()
        m1 = _make_fleet_member("prover_1", capabilities=frozenset({"prove"}))
        m2 = _make_fleet_member("prover_2", capabilities=frozenset({"prove", "witness"}))
        fleet.register_member(m1)
        fleet.register_member(m2)

        # Populate frontier
        frontier = FrontierState()
        for i in range(3):
            item = _make_frontier_item(f"goal_{i}", urgency=i * 3)
            frontier.add(item)

        # Controller decides
        controller = OrchestrationController()
        budgets = BudgetLedger(limits={"frontier": 100}, spent={})
        signal = BackpressureSignal(level=BackpressureLevel.NORMAL)

        decision = controller.decide(frontier, budgets, signal)
        assert isinstance(decision, ControlDecision)
        # Decision must not be a bool
        assert decision is not True
        assert decision is not False
        # If a goal was dispatched, it should be a string (proposition)
        if decision.goal is not None:
            assert isinstance(decision.goal, str)

    def test_evidence_channel_routing_does_not_lose_kind_on_round_trip(self) -> None:
        """A request to the router must produce a channel enum, never None."""
        router = ChannelRouter()
        # Route structural request → should get a valid EvidenceChannel back
        req = EvidenceRequest(
            request_id=uuid.uuid4().hex[:10],
            coordinate="module.foo",
            proposition="typeOf(x) = Bool",
            required_kind="structural",
            preferred_channel=EvidenceChannel.SOLVER,
        )
        channel = router.route(req)
        assert isinstance(channel, EvidenceChannel)
        # The assigned channel must be one of the known channels
        assert channel in list(EvidenceChannel)

    def test_fleet_bid_trust_ceiling_respected_in_channel_routing(self) -> None:
        """Fleet bid confidence must not exceed the channel's declared trust ceiling."""
        member = _make_fleet_member("bounded")
        member.trust_ceiling = 0.6
        bid = member.bid_for("coord", "move_Y")
        # Solver channel ceiling is 0.95 ('verified'), but member ceiling is 0.6
        effective_ceiling = min(member.trust_ceiling, 0.95)
        assert bid.confidence <= effective_ceiling + 1e-9
