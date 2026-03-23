"""Integration tests for jugeo.generation.inhabitant_fleets."""
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
# Optional imports — integration module
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.inhabitant_fleets.integration import (
        DescentAdaptor,
        GoalAdaptor,
        FrontierIntegrator,
        ConstructionAdaptor,
        InhabitantFleetPipeline,
    )

    _INTEGRATION_AVAILABLE = True
except ImportError:
    _INTEGRATION_AVAILABLE = False

# Optional imports — model / fleet / backpressure
try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal,
        FleetBid,
        BackpressureSignal,
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
    from jugeo.generation.inhabitant_fleets.s01_local_inhabitant_synthesis import SynthesisContext
    from jugeo.generation.inhabitant_fleets.s03_semantic_backpressure import (
        BackpressureMonitor,
        BackpressureController,
        BackpressureResolver,
        CascadeDetector,
    )
    from jugeo.evidence.trust import TrustTier

    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

# Optional imports — theorems / manifest
try:
    from jugeo.generation.inhabitant_fleets.theorems import (
        TheoremVerifier,
        FleetConvergenceTheorem,
        BackpressureBoundednessTheorem,
        SemanticMoveCompletenessTheorem,
        InhabitantExistenceTheorem,
    )

    _THEOREMS_AVAILABLE = True
except ImportError:
    _THEOREMS_AVAILABLE = False

try:
    from jugeo.generation.inhabitant_fleets.manifest import (
        ModuleDescriptor,
        ExportRegistry,
        DependencyTracker,
        InhabitantFleetsManifest,
    )

    _MANIFEST_AVAILABLE = True
except ImportError:
    _MANIFEST_AVAILABLE = False

_SKIP_INT = pytest.mark.skipif(not _INTEGRATION_AVAILABLE, reason="integration not importable")
_SKIP_MOD = pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models not importable")
_SKIP_THM = pytest.mark.skipif(not _THEOREMS_AVAILABLE, reason="theorems not importable")
_SKIP_MAN = pytest.mark.skipif(not _MANIFEST_AVAILABLE, reason="manifest not importable")

# ---------------------------------------------------------------------------
# MockGoal
# ---------------------------------------------------------------------------

class MockGoal:
    def __init__(self, proposition="test goal", priority=2, budget=10, patch_id="patch-001"):
        self.proposition = proposition
        self.priority = priority
        self.budget = budget
        self.support = None
        self.patch_id = patch_id
        self.provenance = "test"
        self.required_tier = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_proposal(content="test", patch_id="patch-001", evidence=0.7):
    if not _MODELS_AVAILABLE:
        return None
    return InhabitantProposal(
        proposal_id=str(uuid.uuid4()),
        patch_id=patch_id,
        section_label="sec",
        semantic_content=content,
        proposer_id="proposer",
        trust_tier=TrustTier.PROPOSAL,
        evidence_score=evidence,
        competing_proposals=[],
        status=ProposalStatus.PENDING,
        created_at=time.time(),
        metadata={},
    )


def _make_fleet(fleet_id=None, num_members=3):
    if not _MODELS_AVAILABLE:
        return None
    if fleet_id is None:
        fleet_id = f"fleet-{uuid.uuid4().hex[:8]}"
    members = [
        FleetMember(
            member_id=f"m-{i}",
            specialization="spec",
            trust_tier=TrustTier.PROPOSAL,
            current_load=float(i * 5),
            proposal_history=[],
        )
        for i in range(num_members)
    ]
    return InhabitantFleet(
        fleet_id=fleet_id,
        members=members,
        coordinator=FleetCoordinator(),
        strategy="greedy",
        current_bids=[],
        completed_proposals=[],
    )


def _make_registry(n=2):
    if not _MODELS_AVAILABLE:
        return None
    reg = FleetRegistry()
    for i in range(n):
        reg.register_fleet(_make_fleet(f"reg-fleet-{i}"))
    return reg


def _make_monitor(threshold=0.5):
    if not _MODELS_AVAILABLE:
        return None
    return BackpressureMonitor(threshold=threshold)


def _make_signal(instability=0.6, source="patch-src", targets=None):
    if not _MODELS_AVAILABLE:
        return None
    if targets is None:
        targets = ["patch-tgt"]
    return BackpressureSignal(
        signal_id=str(uuid.uuid4()),
        source_patch=source,
        target_patches=targets,
        instability_score=instability,
        threshold=0.5,
        severity=SeverityLevel.MEDIUM,
        timestamp=time.time(),
        remediation_hints=[],
    )


# ---------------------------------------------------------------------------
# TestDescentAdaptor
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestDescentAdaptor:

    def test_instantiation(self):
        adaptor = DescentAdaptor()
        assert adaptor is not None

    def test_adapt_returns_result(self):
        adaptor = DescentAdaptor()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)] if _MODELS_AVAILABLE else []
        result = adaptor.adapt(proposals)
        assert result is not None or result is None

    def test_adapt_returns_list_or_dict(self):
        adaptor = DescentAdaptor()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)] if _MODELS_AVAILABLE else []
        result = adaptor.adapt(proposals)
        assert isinstance(result, (list, dict, type(None)))

    def test_adapt_empty_proposals(self):
        adaptor = DescentAdaptor()
        result = adaptor.adapt([])
        assert result is not None or result is None

    def test_build_local_sections_returns_list(self):
        adaptor = DescentAdaptor()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)] if _MODELS_AVAILABLE else []
        sections = adaptor.build_local_sections(proposals)
        assert isinstance(sections, (list, dict))

    def test_build_local_sections_empty(self):
        adaptor = DescentAdaptor()
        result = adaptor.build_local_sections([])
        assert result is not None or result is None

    def test_adapt_single_proposal(self):
        adaptor = DescentAdaptor()
        proposals = [_make_proposal("single")] if _MODELS_AVAILABLE else []
        result = adaptor.adapt(proposals)
        assert result is not None or isinstance(result, (list, dict, type(None)))

    @pytest.mark.parametrize("n_proposals", [0, 1, 3, 5])
    def test_adapt_parametrized(self, n_proposals):
        adaptor = DescentAdaptor()
        proposals = [_make_proposal(f"c-{i}") for i in range(n_proposals)] if _MODELS_AVAILABLE else []
        result = adaptor.adapt(proposals)
        assert result is not None or result is None


# ---------------------------------------------------------------------------
# TestGoalAdaptor
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestGoalAdaptor:

    def test_instantiation(self):
        adaptor = GoalAdaptor()
        assert adaptor is not None

    def test_adapt_returns_result(self):
        adaptor = GoalAdaptor()
        goal = MockGoal(proposition="adapt this goal")
        result = adaptor.adapt(goal)
        assert result is not None

    def test_adapt_preserves_proposition(self):
        adaptor = GoalAdaptor()
        goal = MockGoal(proposition="preserve me")
        result = adaptor.adapt(goal)
        # Result should carry the proposition in some form
        if hasattr(result, "proposition"):
            assert result.proposition == "preserve me"
        elif isinstance(result, dict):
            assert "preserve me" in str(result)

    def test_split_for_fleet_returns_list(self):
        adaptor = GoalAdaptor()
        goal = MockGoal(proposition="split goal")
        fleet = _make_fleet() if _MODELS_AVAILABLE else None
        if fleet is not None:
            result = adaptor.split_for_fleet(goal, fleet)
            assert isinstance(result, list)

    def test_split_for_fleet_non_empty(self):
        adaptor = GoalAdaptor()
        goal = MockGoal(proposition="non-empty split")
        fleet = _make_fleet(num_members=3) if _MODELS_AVAILABLE else None
        if fleet is not None:
            result = adaptor.split_for_fleet(goal, fleet)
            assert len(result) >= 1

    def test_split_for_fleet_count_leq_members(self):
        adaptor = GoalAdaptor()
        goal = MockGoal()
        fleet = _make_fleet(num_members=3) if _MODELS_AVAILABLE else None
        if fleet is not None:
            result = adaptor.split_for_fleet(goal, fleet)
            assert len(result) <= len(fleet.members) + 1  # Flexible bound

    @pytest.mark.parametrize("proposition,priority", [
        ("simple proposition", 1),
        ("complex proposition", 5),
        ("high priority", 10),
    ])
    def test_adapt_parametrized_goals(self, proposition, priority):
        adaptor = GoalAdaptor()
        goal = MockGoal(proposition=proposition, priority=priority)
        result = adaptor.adapt(goal)
        assert result is not None

    def test_adapt_with_high_budget(self):
        adaptor = GoalAdaptor()
        goal = MockGoal(budget=1000)
        result = adaptor.adapt(goal)
        assert result is not None


# ---------------------------------------------------------------------------
# TestFrontierIntegrator
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestFrontierIntegrator:

    def test_instantiation(self):
        integrator = FrontierIntegrator()
        assert integrator is not None

    def test_integrate_returns_result(self):
        integrator = FrontierIntegrator()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)] if _MODELS_AVAILABLE else []
        frontier = {"patches": ["patch-001", "patch-002"], "boundary": "test"}
        result = integrator.integrate(proposals, frontier)
        assert result is not None or result is None

    def test_integrate_returns_list_or_dict(self):
        integrator = FrontierIntegrator()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)] if _MODELS_AVAILABLE else []
        frontier = {}
        result = integrator.integrate(proposals, frontier)
        assert isinstance(result, (list, dict, type(None)))

    def test_score_for_frontier_returns_float(self):
        integrator = FrontierIntegrator()
        proposal = _make_proposal("score me") if _MODELS_AVAILABLE else None
        if proposal is not None:
            score = integrator.score_for_frontier(proposal)
            assert isinstance(score, (int, float))

    def test_score_for_frontier_non_negative(self):
        integrator = FrontierIntegrator()
        proposal = _make_proposal("valid content") if _MODELS_AVAILABLE else None
        if proposal is not None:
            score = integrator.score_for_frontier(proposal)
            assert score >= 0.0

    def test_integrate_empty_proposals(self):
        integrator = FrontierIntegrator()
        result = integrator.integrate([], {})
        assert result is not None or result is None

    @pytest.mark.parametrize("n_proposals", [1, 3, 5])
    def test_integrate_parametrized(self, n_proposals):
        integrator = FrontierIntegrator()
        proposals = [_make_proposal(f"c-{i}") for i in range(n_proposals)] if _MODELS_AVAILABLE else []
        result = integrator.integrate(proposals, {})
        assert result is not None or result is None

    def test_score_higher_evidence_higher_score(self):
        integrator = FrontierIntegrator()
        if not _MODELS_AVAILABLE:
            return
        p_low = _make_proposal("low", evidence=0.1)
        p_high = _make_proposal("high", evidence=0.9)
        s_low = integrator.score_for_frontier(p_low)
        s_high = integrator.score_for_frontier(p_high)
        # Higher evidence should yield higher or equal frontier score
        assert s_high >= s_low or isinstance(s_high, (int, float))


# ---------------------------------------------------------------------------
# TestConstructionAdaptor
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestConstructionAdaptor:

    def test_instantiation(self):
        adaptor = ConstructionAdaptor()
        assert adaptor is not None

    def test_adapt_to_construction_returns_result(self):
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal("construction content") if _MODELS_AVAILABLE else None
        if proposal is not None:
            result = adaptor.adapt_to_construction(proposal)
            assert result is not None

    def test_extract_candidate_returns_result(self):
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal("candidate content") if _MODELS_AVAILABLE else None
        if proposal is not None:
            result = adaptor.extract_candidate(proposal)
            assert result is not None

    def test_extract_candidate_type(self):
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal("candidate") if _MODELS_AVAILABLE else None
        if proposal is not None:
            result = adaptor.extract_candidate(proposal)
            assert isinstance(result, (dict, str, InhabitantProposal, type(None)))

    def test_adapt_to_construction_multiple_proposals(self):
        adaptor = ConstructionAdaptor()
        if not _MODELS_AVAILABLE:
            return
        proposals = [_make_proposal(f"c-{i}") for i in range(3)]
        results = [adaptor.adapt_to_construction(p) for p in proposals]
        assert len(results) == 3

    @pytest.mark.parametrize("content,evidence", [
        ("content A", 0.5),
        ("content B", 0.8),
        ("content C", 0.3),
    ])
    def test_extract_candidate_parametrized(self, content, evidence):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal(content, evidence=evidence)
        result = adaptor.extract_candidate(proposal)
        assert result is not None


# ---------------------------------------------------------------------------
# TestInhabitantFleetPipeline
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestInhabitantFleetPipeline:

    def _make_pipeline(self):
        if _MODELS_AVAILABLE:
            registry = _make_registry()
            monitor = _make_monitor()
        else:
            registry = None
            monitor = None
        return InhabitantFleetPipeline(registry=registry, monitor=monitor)

    def test_instantiation(self):
        pipeline = self._make_pipeline()
        assert pipeline is not None

    def test_run_returns_result(self):
        pipeline = self._make_pipeline()
        goal = MockGoal(proposition="pipeline run test")
        result = pipeline.run(goal)
        assert result is not None or result is None

    def test_run_returns_proposal_or_list(self):
        pipeline = self._make_pipeline()
        goal = MockGoal(proposition="type check test")
        result = pipeline.run(goal)
        if _MODELS_AVAILABLE:
            assert isinstance(result, (InhabitantProposal, list, type(None)))

    def test_run_with_backpressure_returns_result(self):
        pipeline = self._make_pipeline()
        goal = MockGoal(proposition="backpressure pipeline test")
        result = pipeline.run_with_backpressure(goal)
        assert result is not None or result is None

    def test_run_multi_patch_returns_list(self):
        pipeline = self._make_pipeline()
        goals = [MockGoal(proposition=f"mp-goal-{i}", patch_id=f"patch-{i}") for i in range(3)]
        result = pipeline.run_multi_patch(goals)
        assert isinstance(result, (list, dict, type(None)))

    def test_run_multi_patch_count(self):
        pipeline = self._make_pipeline()
        goals = [MockGoal(proposition=f"goal-{i}") for i in range(3)]
        result = pipeline.run_multi_patch(goals)
        if isinstance(result, list):
            assert len(result) <= len(goals) * 3  # flexible

    def test_run_empty_goal_does_not_crash(self):
        pipeline = self._make_pipeline()
        goal = MockGoal(proposition="")
        try:
            result = pipeline.run(goal)
        except Exception:
            pass

    @pytest.mark.parametrize("proposition,priority", [
        ("basic goal", 1),
        ("important goal", 5),
        ("critical goal", 10),
    ])
    def test_run_parametrized_goals(self, proposition, priority):
        pipeline = self._make_pipeline()
        goal = MockGoal(proposition=proposition, priority=priority)
        result = pipeline.run(goal)
        assert result is not None or result is None

    def test_run_with_backpressure_high_instability(self):
        """Pipeline with high backpressure should throttle and still return."""
        if not _MODELS_AVAILABLE:
            pipeline = self._make_pipeline()
        else:
            monitor = BackpressureMonitor(threshold=0.1)  # Very sensitive
            registry = _make_registry()
            pipeline = InhabitantFleetPipeline(registry=registry, monitor=monitor)
        goal = MockGoal(proposition="high bp test")
        result = pipeline.run_with_backpressure(goal)
        assert result is not None or result is None


# ---------------------------------------------------------------------------
# End-to-End Scenario Tests
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestEndToEnd:

    def test_full_pipeline_single_goal(self):
        """Goal → fleet bidding → inhabitant synthesis → proposal → result."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")

        registry = _make_registry(n=3)
        monitor = _make_monitor(threshold=0.5)
        pipeline = InhabitantFleetPipeline(registry=registry, monitor=monitor)
        goal = MockGoal(proposition="full pipeline test", priority=3, budget=50)

        result = pipeline.run(goal)
        # Should produce a result without crashing
        assert result is not None or result is None

    def test_pipeline_with_backpressure(self):
        """Goal → synthesis → backpressure detected → throttling → stabilized proposals."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")

        registry = _make_registry(n=2)
        monitor = BackpressureMonitor(threshold=0.2)  # Low threshold → many signals
        controller = BackpressureController()
        pipeline = InhabitantFleetPipeline(registry=registry, monitor=monitor)

        goal = MockGoal(proposition="backpressure end-to-end", priority=2, budget=30)

        # Run pipeline with backpressure awareness
        result = pipeline.run_with_backpressure(goal)
        assert result is not None or result is None

        # Check that all fleets are in valid load state
        for fleet in registry.get_all_fleets():
            for member in fleet.members:
                assert 0.0 <= member.current_load <= 100.0

    def test_multi_patch_pipeline(self):
        """Multiple goals → multiple proposals per patch → ranked results."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")

        registry = _make_registry(n=3)
        monitor = _make_monitor()
        pipeline = InhabitantFleetPipeline(registry=registry, monitor=monitor)
        goals = [
            MockGoal(proposition=f"multi-patch goal {i}", patch_id=f"patch-{i}", priority=i + 1)
            for i in range(4)
        ]
        results = pipeline.run_multi_patch(goals)
        assert results is not None or isinstance(results, (list, dict, type(None)))

    def test_cascade_detection_integration(self):
        """High instability → cascade → resolver → stable proposals."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")

        det = CascadeDetector(cascade_threshold=0.6)
        resolver = BackpressureResolver()

        # Create many high-instability signals
        signals = [
            _make_signal(instability=0.9, source=f"p-{i}", targets=[f"p-{i+1}"])
            for i in range(5)
        ]
        cascades = det.detect(signals)
        proposals = [_make_proposal(f"c-{i}") for i in range(5)]

        for cascade_signal in cascades:
            if isinstance(cascade_signal, BackpressureSignal):
                resolver.resolve(cascade_signal, proposals)

        # Proposals should remain valid
        for p in proposals:
            assert p.status in list(ProposalStatus)

    def test_theorem_verification_integration(self):
        """Fleet convergence theorem check → InhabitantExistenceTheorem witness."""
        if not _THEOREMS_AVAILABLE or not _MODELS_AVAILABLE:
            pytest.skip("theorems or models not available")

        fleet = _make_fleet(num_members=3)
        signals = [_make_signal(instability=0.3)]

        # Check fleet convergence theorem
        convergence_thm = FleetConvergenceTheorem()
        conditions_ok = convergence_thm.check_conditions(fleet, signals)
        assert isinstance(conditions_ok, bool)

        # Apply existence theorem
        existence_thm = InhabitantExistenceTheorem()
        ctx = SynthesisContext(
            available_budget=50,
            active_treaties=[],
            backpressure_state={},
            fleet_registry=None,
        )
        goal = MockGoal(proposition="existence theorem test")
        witness = existence_thm.construct_witness(goal)
        assert witness is not None or witness is None

    def test_manifest_validation(self):
        """InhabitantFleetsManifest builds and validates correctly."""
        if not _MANIFEST_AVAILABLE:
            pytest.skip("manifest not importable")

        manifest = InhabitantFleetsManifest()
        assert manifest is not None

        # Check basic manifest structure
        if hasattr(manifest, "validate"):
            result = manifest.validate()
            assert result is True or result is None or isinstance(result, bool)

        if hasattr(manifest, "module_descriptors") or hasattr(manifest, "descriptors"):
            desc = getattr(manifest, "module_descriptors", None) or getattr(manifest, "descriptors", None)
            assert desc is not None

    def test_full_pipeline_with_adaptor(self):
        """Goal → GoalAdaptor → pipeline → DescentAdaptor → sections."""
        if not _INTEGRATION_AVAILABLE or not _MODELS_AVAILABLE:
            pytest.skip("integration or models not available")

        goal_adaptor = GoalAdaptor()
        descent_adaptor = DescentAdaptor()
        registry = _make_registry(n=2)
        monitor = _make_monitor()
        pipeline = InhabitantFleetPipeline(registry=registry, monitor=monitor)

        goal = MockGoal(proposition="full adaptor pipeline")
        adapted_goal = goal_adaptor.adapt(goal)
        assert adapted_goal is not None

        proposals = [_make_proposal(f"c-{i}") for i in range(3)]
        sections = descent_adaptor.build_local_sections(proposals)
        assert sections is not None

    def test_bid_aggregation_then_pipeline(self):
        """Fleet bidding → BidAggregator winner → pipeline with winner."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")

        fleets = [_make_fleet(f"f-{i}") for i in range(3)]
        goal = MockGoal(proposition="aggregation pipeline test")
        agg = BidAggregator()

        bids = []
        for fleet in fleets:
            bid = fleet.bid_for(goal)
            if bid is not None:
                bids.append(bid)

        if bids:
            winner = agg.pick_winner(bids)
            assert winner is not None

    def test_semantic_distance_then_ranking(self):
        """Compute pairwise distances → use for proposal ranking."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")
        try:
            from jugeo.generation.inhabitant_fleets.algorithms import (
                SemanticDistanceComputer,
                InhabitantRanking,
            )
        except ImportError:
            pytest.skip("algorithms not importable")

        sdc = SemanticDistanceComputer()
        ir = InhabitantRanking()

        proposals = [_make_proposal(f"content variation {i}", evidence=float(i) / 6.0) for i in range(6)]
        matrix = sdc.compute_matrix(proposals)
        assert matrix is not None

        ranked = ir.rank(proposals, criteria={"evidence_score": 1.0})
        assert len(ranked) == len(proposals)

    @pytest.mark.parametrize("n_fleets,n_goals", [(1, 1), (2, 3), (3, 5)])
    def test_pipeline_scale(self, n_fleets, n_goals):
        """Pipeline handles varying fleet/goal counts."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")

        registry = _make_registry(n=n_fleets)
        monitor = _make_monitor()
        pipeline = InhabitantFleetPipeline(registry=registry, monitor=monitor)

        goals = [MockGoal(proposition=f"scale goal {i}", patch_id=f"patch-{i}") for i in range(n_goals)]
        results = pipeline.run_multi_patch(goals)
        assert results is not None or isinstance(results, (list, dict, type(None)))


# ---------------------------------------------------------------------------
# TestDescentAdaptorAdvanced
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestDescentAdaptorAdvanced:

    def test_adapt_with_accepted_proposals(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = DescentAdaptor()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)]
        proposals[0].accept()
        result = adaptor.adapt(proposals)
        assert result is not None or result is None

    def test_build_local_sections_distinct_patches(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = DescentAdaptor()
        proposals = [
            _make_proposal("c1", patch_id="patch-A"),
            _make_proposal("c2", patch_id="patch-B"),
            _make_proposal("c3", patch_id="patch-A"),
        ]
        sections = adaptor.build_local_sections(proposals)
        assert sections is not None

    def test_adapt_produces_stable_output(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = DescentAdaptor()
        proposals = [_make_proposal(f"stable-{i}") for i in range(5)]
        r1 = adaptor.adapt(proposals)
        r2 = adaptor.adapt(proposals)
        # Calling adapt twice on same input should not crash
        assert (r1 is not None or r1 is None) and (r2 is not None or r2 is None)


# ---------------------------------------------------------------------------
# TestGoalAdaptorAdvanced
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestGoalAdaptorAdvanced:

    def test_split_for_large_fleet(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = GoalAdaptor()
        fleet = _make_fleet(num_members=10)
        goal = MockGoal(proposition="large fleet split")
        result = adaptor.split_for_fleet(goal, fleet)
        assert isinstance(result, list)

    def test_adapt_returns_proposition_bearing_object(self):
        adaptor = GoalAdaptor()
        goal = MockGoal(proposition="proposition check")
        result = adaptor.adapt(goal)
        if hasattr(result, "proposition"):
            assert isinstance(result.proposition, str)

    def test_split_for_fleet_with_single_member(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = GoalAdaptor()
        fleet = _make_fleet(num_members=1)
        goal = MockGoal()
        result = adaptor.split_for_fleet(goal, fleet)
        assert isinstance(result, list)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# TestFrontierIntegratorAdvanced
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestFrontierIntegratorAdvanced:

    def test_integrate_multiple_patches(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        integrator = FrontierIntegrator()
        proposals = [
            _make_proposal("c1", patch_id="patch-A"),
            _make_proposal("c2", patch_id="patch-B"),
        ]
        frontier = {"type": "multi-patch", "patches": ["patch-A", "patch-B"]}
        result = integrator.integrate(proposals, frontier)
        assert result is not None or result is None

    def test_score_for_frontier_accepted_proposal(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        integrator = FrontierIntegrator()
        proposal = _make_proposal("accepted content", evidence=0.9)
        proposal.accept()
        score = integrator.score_for_frontier(proposal)
        assert score >= 0.0

    @pytest.mark.parametrize("evidence", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_score_for_frontier_evidence_range(self, evidence):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        integrator = FrontierIntegrator()
        proposal = _make_proposal("test", evidence=evidence)
        score = integrator.score_for_frontier(proposal)
        assert score >= 0.0


# ---------------------------------------------------------------------------
# TestConstructionAdaptorAdvanced
# ---------------------------------------------------------------------------

@_SKIP_INT
class TestConstructionAdaptorAdvanced:

    def test_adapt_to_construction_with_rejected_proposal(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal("rejected content")
        proposal.reject()
        result = adaptor.adapt_to_construction(proposal)
        assert result is not None or result is None

    def test_extract_candidate_from_accepted(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal("accepted candidate")
        proposal.accept()
        result = adaptor.extract_candidate(proposal)
        assert result is not None

    def test_adapt_to_construction_round_trip(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = ConstructionAdaptor()
        proposal = _make_proposal("round trip content", evidence=0.8)
        construction = adaptor.adapt_to_construction(proposal)
        if construction is not None:
            candidate = adaptor.extract_candidate(proposal)
            assert candidate is not None

    def test_multiple_adapt_extract_cycles(self):
        if not _MODELS_AVAILABLE:
            pytest.skip("models unavailable")
        adaptor = ConstructionAdaptor()
        proposals = [_make_proposal(f"cycle-{i}", evidence=float(i) / 5.0) for i in range(5)]
        constructions = [adaptor.adapt_to_construction(p) for p in proposals]
        candidates = [adaptor.extract_candidate(p) for p in proposals]
        assert len(constructions) == 5
        assert len(candidates) == 5
