"""Comprehensive tests for the research software orchestration module.

Tests the full sheaf-theoretic orchestration pipeline:
- WorkspaceSite construction and consistency checking
- Overlap detection (numerical mismatches, stale sections, missing evidence)
- Trust algebra (conservative join, attenuation, no silent promotion)
- Semantic moves (all 17 move kinds)
- Phase ordering (fixed, adaptive, greedy strategies)
- Hallucination detection (FM-1)
- Stale section invalidation (FM-2)
- Specification drift / pivot (FM-3)
- Full orchestration loop
- Edge cases and invariants
"""

import pytest

from jugeo.research_orchestration import (
    ArtifactSurface,
    ConsistencyReport,
    MorphismType,
    MoveKind,
    MoveRecord,
    ObstructionKind,
    PhaseKind,
    ResearchOrchestrator,
    ResearchStrategy,
    SurfaceKind,
    WorkspaceObstruction,
    WorkspaceSite,
    WORKSPACE_MORPHISMS,
    COVERING_FAMILIES,
    PHASE_MOVES,
    MOVE_TARGET,
    MOVE_TRUST,
)


# ═══════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def consistent_workspace():
    """A workspace where all surfaces agree — should be consistent."""
    return WorkspaceSite.from_artifacts(
        theory="We define barrier certificates for safety verification",
        code="def check_barrier(x, B): return B(x) >= 0",
        evidence={"test_cases": 90, "benchmark_f1": 0.768, "precision": 0.704},
        claims="We achieve benchmark_f1=0.768 on test_cases=90 with precision=0.704",
        name="consistent-repo",
    )


@pytest.fixture
def inconsistent_workspace():
    """A workspace where claims don't match evidence — hallucination."""
    return WorkspaceSite.from_artifacts(
        theory="We propose a novel verification framework",
        code="def verify(program): return True",
        evidence={"benchmark_f1": 0.71, "test_cases": 90},
        claims="We achieve benchmark_f1=0.83 on test_cases=90",  # 0.83 != 0.71!
        name="hallucinated-repo",
    )


@pytest.fixture
def empty_workspace():
    """A workspace with minimal content."""
    return WorkspaceSite.from_artifacts(name="empty-repo")


@pytest.fixture
def complex_workspace():
    """A workspace with rich content across all surfaces."""
    return WorkspaceSite.from_artifacts(
        theory="Barrier certificates: B(x) > 0 for all x in safe set. "
               "Lyapunov function V(x) decreasing along trajectories. "
               "Completeness theorem: all safe systems admit barriers.",
        code="""
def barrier_check(x, B, domain):
    for point in domain:
        if B(point) < 0:
            return False
    return True

def lyapunov_decrease(V, x, dynamics, dt=0.01):
    x_next = dynamics(x, dt)
    return V(x_next) < V(x)

def find_barrier(system, template, solver='z3'):
    # Use SMT solver to synthesize barrier
    pass
""",
        evidence={
            "benchmark_f1": 0.768,
            "precision": 0.704,
            "recall": 0.844,
            "test_cases": 90,
            "ablation_kitchensink": -0.052,
            "ablation_interprocedural": -0.012,
        },
        claims="We achieve benchmark_f1=0.768 with precision=0.704 and recall=0.844 "
               "on test_cases=90. Ablation: ablation_kitchensink=-0.052, "
               "ablation_interprocedural=-0.012.",
        name="a3-flagship",
    )


# ═══════════════════════════════════════════════════════════════════════
#  1. WorkspaceSite construction
# ═══════════════════════════════════════════════════════════════════════

class TestWorkspaceSiteConstruction:
    """Test workspace site creation and structure."""

    def test_from_artifacts_creates_four_surfaces(self, consistent_workspace):
        ws = consistent_workspace
        assert len(ws.surfaces) == 4
        for kind in SurfaceKind:
            assert kind in ws.surfaces

    def test_from_artifacts_creates_four_sections(self, consistent_workspace):
        ws = consistent_workspace
        assert len(ws.sections) == 4
        for kind in SurfaceKind:
            assert kind in ws.sections

    def test_surface_has_content_hash(self, consistent_workspace):
        for surface in consistent_workspace.surfaces.values():
            assert len(surface.content_hash) == 16

    def test_surface_hashes_differ(self, consistent_workspace):
        hashes = [s.content_hash for s in consistent_workspace.surfaces.values()]
        assert len(set(hashes)) == len(hashes)  # All unique

    def test_empty_workspace_creates_surfaces(self, empty_workspace):
        assert len(empty_workspace.surfaces) == 4

    def test_name_preserved(self):
        ws = WorkspaceSite.from_artifacts(name="my-repo")
        assert ws.name == "my-repo"


# ═══════════════════════════════════════════════════════════════════════
#  2. Claim extraction
# ═══════════════════════════════════════════════════════════════════════

class TestClaimExtraction:
    """Test extraction of verifiable claims from surfaces."""

    def test_evidence_dict_extracted(self, consistent_workspace):
        ev = consistent_workspace.sections[SurfaceKind.EVIDENCE]
        assert ev.claims["benchmark_f1"] == 0.768
        assert ev.claims["test_cases"] == 90

    def test_text_numerical_claims_extracted(self, consistent_workspace):
        cl = consistent_workspace.sections[SurfaceKind.CLAIMS]
        assert "benchmark_f1" in cl.claims
        assert cl.claims["benchmark_f1"] == 0.768

    def test_inconsistent_claims_differ(self, inconsistent_workspace):
        ev = inconsistent_workspace.sections[SurfaceKind.EVIDENCE]
        cl = inconsistent_workspace.sections[SurfaceKind.CLAIMS]
        assert ev.claims["benchmark_f1"] == 0.71
        assert cl.claims["benchmark_f1"] == 0.83
        assert ev.claims["benchmark_f1"] != cl.claims["benchmark_f1"]

    def test_empty_string_produces_empty_claims(self, empty_workspace):
        theory = empty_workspace.sections[SurfaceKind.THEORY]
        # Empty string → no numerical claims (may have _text if non-empty)
        numeric_claims = {k: v for k, v in theory.claims.items() if not k.startswith("_")}
        assert len(numeric_claims) == 0


# ═══════════════════════════════════════════════════════════════════════
#  3. Overlap checking (sheaf condition)
# ═══════════════════════════════════════════════════════════════════════

class TestOverlapChecking:
    """Test the core sheaf condition: overlap compatibility."""

    def test_consistent_overlap_passes(self, consistent_workspace):
        result = consistent_workspace.check_overlap(
            SurfaceKind.EVIDENCE, SurfaceKind.CLAIMS, MorphismType.REPORT)
        assert result.compatible

    def test_inconsistent_overlap_fails(self, inconsistent_workspace):
        result = inconsistent_workspace.check_overlap(
            SurfaceKind.EVIDENCE, SurfaceKind.CLAIMS, MorphismType.REPORT)
        assert not result.compatible
        assert len(result.mismatches) > 0

    def test_mismatch_reports_delta(self, inconsistent_workspace):
        result = inconsistent_workspace.check_overlap(
            SurfaceKind.EVIDENCE, SurfaceKind.CLAIMS, MorphismType.REPORT)
        mismatch = result.mismatches[0]
        assert mismatch["type"] == "numerical"
        assert mismatch["key"] == "benchmark_f1"
        assert abs(mismatch["claimed"] - 0.83) < 1e-6
        assert abs(mismatch["actual"] - 0.71) < 1e-6

    def test_overlap_trust_is_conservative_join(self, consistent_workspace):
        """Trust of an overlap is min(source, target) — conservative join."""
        ws = consistent_workspace
        ws.sections[SurfaceKind.EVIDENCE].trust = 0.8
        ws.sections[SurfaceKind.CLAIMS].trust = 0.3
        result = ws.check_overlap(
            SurfaceKind.EVIDENCE, SurfaceKind.CLAIMS, MorphismType.REPORT)
        assert result.trust == 0.3  # Conservative join


# ═══════════════════════════════════════════════════════════════════════
#  4. Consistency checking (full descent)
# ═══════════════════════════════════════════════════════════════════════

class TestConsistencyChecking:
    """Test full workspace descent."""

    def test_consistent_workspace_passes(self, consistent_workspace):
        report = consistent_workspace.check_consistency()
        # Check no numerical mismatches (there may be H⁰ missing sections)
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) == 0

    def test_inconsistent_workspace_detects_hallucination(self, inconsistent_workspace):
        report = inconsistent_workspace.check_consistency()
        assert not report.consistent
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) > 0

    def test_report_has_correct_counts(self, inconsistent_workspace):
        report = inconsistent_workspace.check_consistency()
        assert report.overlaps_checked == len(WORKSPACE_MORPHISMS)
        assert report.overlaps_failed >= 1

    def test_consistency_rate(self, consistent_workspace):
        report = consistent_workspace.check_consistency()
        assert 0.0 <= report.consistency_rate <= 1.0

    def test_obstruction_has_repair_hints(self, inconsistent_workspace):
        report = inconsistent_workspace.check_consistency()
        for obs in report.obstructions:
            if obs.kind == ObstructionKind.NUMERICAL_MISMATCH:
                assert len(obs.repair_hints) > 0

    def test_complex_workspace_consistency(self, complex_workspace):
        report = complex_workspace.check_consistency()
        # Complex workspace has matching numbers — should have no numerical mismatches
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) == 0


# ═══════════════════════════════════════════════════════════════════════
#  5. Trust algebra
# ═══════════════════════════════════════════════════════════════════════

class TestTrustAlgebra:
    """Test trust operations: conservative join, attenuation, promotion."""

    def test_initial_trust_is_zero(self, consistent_workspace):
        for section in consistent_workspace.sections.values():
            assert section.trust == 0.0

    def test_update_resets_trust(self, consistent_workspace):
        ws = consistent_workspace
        ws.sections[SurfaceKind.CODE].trust = 0.8
        ws.update_surface(SurfaceKind.CODE, "def new_code(): pass")
        assert ws.sections[SurfaceKind.CODE].trust == 0.0

    def test_conservative_join(self, consistent_workspace):
        """Trust of overlap = min(trust_a, trust_b)."""
        ws = consistent_workspace
        ws.sections[SurfaceKind.EVIDENCE].trust = 0.9
        ws.sections[SurfaceKind.CLAIMS].trust = 0.4
        result = ws.check_overlap(
            SurfaceKind.EVIDENCE, SurfaceKind.CLAIMS, MorphismType.REPORT)
        assert result.trust == 0.4


# ═══════════════════════════════════════════════════════════════════════
#  6. Stale section detection (FM-2)
# ═══════════════════════════════════════════════════════════════════════

class TestStaleSections:
    """Test world-model staleness detection."""

    def test_fresh_section_not_stale(self, consistent_workspace):
        ws = consistent_workspace
        for section in ws.sections.values():
            assert section.hash_at_creation != ""

    def test_update_invalidates_section(self, consistent_workspace):
        ws = consistent_workspace
        old_hash = ws.sections[SurfaceKind.CODE].hash_at_creation
        ws.update_surface(SurfaceKind.CODE, "def totally_new(): return 42")
        new_hash = ws.sections[SurfaceKind.CODE].hash_at_creation
        assert old_hash != new_hash

    def test_version_increments_on_update(self, consistent_workspace):
        ws = consistent_workspace
        assert ws.surfaces[SurfaceKind.CODE].version == 0
        ws.update_surface(SurfaceKind.CODE, "v2")
        assert ws.surfaces[SurfaceKind.CODE].version == 1
        ws.update_surface(SurfaceKind.CODE, "v3")
        assert ws.surfaces[SurfaceKind.CODE].version == 2


# ═══════════════════════════════════════════════════════════════════════
#  7. Hallucination detection (FM-1)
# ═══════════════════════════════════════════════════════════════════════

class TestHallucinationDetection:
    """Test detection of fabricated numbers (FM-1)."""

    def test_fabricated_f1_detected(self):
        """When claims say F1=0.83 but evidence shows F1=0.71."""
        ws = WorkspaceSite.from_artifacts(
            evidence={"f1": 0.71},
            claims="We achieve f1=0.83",
            name="hallucination-test",
        )
        report = ws.check_consistency()
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) >= 1
        mismatch = numerical_obs[0].mismatches[0]
        assert abs(mismatch["claimed"] - 0.83) < 1e-6
        assert abs(mismatch["actual"] - 0.71) < 1e-6

    def test_matching_numbers_no_hallucination(self):
        """When claims match evidence, no hallucination detected."""
        ws = WorkspaceSite.from_artifacts(
            evidence={"f1": 0.768},
            claims="We achieve f1=0.768",
        )
        report = ws.check_consistency()
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) == 0

    def test_multiple_hallucinations_detected(self):
        """Multiple fabricated numbers are all detected."""
        ws = WorkspaceSite.from_artifacts(
            evidence={"f1": 0.71, "accuracy": 0.85, "latency": 42.0},
            claims="We achieve f1=0.99 with accuracy=0.99 and latency=10.0",
        )
        report = ws.check_consistency()
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) >= 2  # At least f1 and accuracy


# ═══════════════════════════════════════════════════════════════════════
#  8. Specification drift (FM-3)
# ═══════════════════════════════════════════════════════════════════════

class TestSpecDrift:
    """Test specification pivot handling."""

    def test_pivot_reduces_theory_trust(self, consistent_workspace):
        ws = consistent_workspace
        ws.sections[SurfaceKind.THEORY].trust = 0.8
        orch = ResearchOrchestrator(ws, strategy=ResearchStrategy.FIXED)
        orch._apply_move(MoveKind.PIVOT_SPEC, PhaseKind.HARDEN)
        assert ws.sections[SurfaceKind.THEORY].trust < 0.8


# ═══════════════════════════════════════════════════════════════════════
#  9. Semantic moves
# ═══════════════════════════════════════════════════════════════════════

class TestSemanticMoves:
    """Test individual semantic moves."""

    def test_all_moves_have_targets(self):
        for move in MoveKind:
            assert move in MOVE_TARGET, f"Move {move} has no target"

    def test_all_moves_have_trust(self):
        for move in MoveKind:
            assert move in MOVE_TRUST, f"Move {move} has no trust value"

    def test_all_phases_have_moves(self):
        for phase in PhaseKind:
            assert phase in PHASE_MOVES
            assert len(PHASE_MOVES[phase]) > 0

    def test_ground_claims_fixes_mismatch(self, inconsistent_workspace):
        ws = inconsistent_workspace
        orch = ResearchOrchestrator(ws)

        # Before grounding
        report = ws.check_consistency()
        obs_before = len([o for o in report.obstructions
                         if o.kind == ObstructionKind.NUMERICAL_MISMATCH])
        assert obs_before >= 1

        # Apply grounding
        orch._apply_move(MoveKind.GROUND_CLAIMS, PhaseKind.HARDEN)

        # After grounding
        report = ws.check_consistency()
        obs_after = len([o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH])
        assert obs_after < obs_before

    def test_repair_fixes_obstructions(self, inconsistent_workspace):
        ws = inconsistent_workspace
        orch = ResearchOrchestrator(ws)
        report = ws.check_consistency()
        orch._repair_obstructions(report)
        new_report = ws.check_consistency()
        assert len(new_report.obstructions) <= len(report.obstructions)

    def test_run_benchmark_increases_evidence_trust(self, consistent_workspace):
        ws = consistent_workspace
        ws.sections[SurfaceKind.EVIDENCE].trust = 0.2
        orch = ResearchOrchestrator(ws)
        orch._run_benchmark()
        assert ws.sections[SurfaceKind.EVIDENCE].trust > 0.2

    def test_move_records_are_created(self, consistent_workspace):
        orch = ResearchOrchestrator(consistent_workspace)
        orch._apply_move(MoveKind.CONSTRUCT_THEORY, PhaseKind.SEED)
        assert len(orch.moves) == 1
        record = orch.moves[0]
        assert record.move == MoveKind.CONSTRUCT_THEORY
        assert record.phase == PhaseKind.SEED


# ═══════════════════════════════════════════════════════════════════════
#  10. Full orchestration loop
# ═══════════════════════════════════════════════════════════════════════

class TestFullOrchestration:
    """Test the complete orchestration pipeline."""

    def test_fixed_strategy_completes_all_phases(self, consistent_workspace):
        orch = ResearchOrchestrator(
            consistent_workspace, strategy=ResearchStrategy.FIXED)
        result = orch.run()
        assert set(result.phases_completed) == set(PhaseKind)

    def test_adaptive_strategy_runs(self, inconsistent_workspace):
        orch = ResearchOrchestrator(
            inconsistent_workspace, strategy=ResearchStrategy.ADAPTIVE,
            max_moves=30)
        result = orch.run()
        assert result.total_moves > 0
        assert result.total_moves <= 30

    def test_greedy_strategy_runs(self, inconsistent_workspace):
        orch = ResearchOrchestrator(
            inconsistent_workspace, strategy=ResearchStrategy.GREEDY,
            max_moves=20)
        result = orch.run()
        assert result.total_moves > 0

    def test_orchestration_repairs_hallucination(self, inconsistent_workspace):
        orch = ResearchOrchestrator(
            inconsistent_workspace, strategy=ResearchStrategy.ADAPTIVE,
            max_moves=30)
        result = orch.run()
        # After orchestration, numerical mismatches should be repaired
        numerical_obs = [o for o in result.final_consistency.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) == 0

    def test_complex_workspace_orchestration(self, complex_workspace):
        orch = ResearchOrchestrator(
            complex_workspace, strategy=ResearchStrategy.ADAPTIVE,
            max_moves=40)
        result = orch.run()
        assert result.total_moves > 0
        assert result.elapsed > 0
        assert isinstance(result.final_trust, dict)

    def test_result_repr(self, consistent_workspace):
        orch = ResearchOrchestrator(
            consistent_workspace, strategy=ResearchStrategy.FIXED)
        result = orch.run()
        s = repr(result)
        assert "OrchestrationResult" in s

    def test_consistency_report_repr(self, consistent_workspace):
        report = consistent_workspace.check_consistency()
        s = repr(report)
        assert "ConsistencyReport" in s


# ═══════════════════════════════════════════════════════════════════════
#  11. Invariants and properties
# ═══════════════════════════════════════════════════════════════════════

class TestInvariants:
    """Test mathematical invariants of the orchestration system."""

    def test_morphism_count_matches_spec(self):
        """The workspace site has exactly 6 morphisms."""
        assert len(WORKSPACE_MORPHISMS) == 6

    def test_covering_families_cover_all_surfaces(self):
        """Every surface has a covering family."""
        for kind in SurfaceKind:
            assert kind in COVERING_FAMILIES

    def test_consistency_is_deterministic(self, consistent_workspace):
        """Running consistency check twice gives same result."""
        r1 = consistent_workspace.check_consistency()
        r2 = consistent_workspace.check_consistency()
        assert r1.consistent == r2.consistent
        assert len(r1.obstructions) == len(r2.obstructions)

    def test_repair_is_monotone(self, inconsistent_workspace):
        """Repair never increases the number of obstructions."""
        ws = inconsistent_workspace
        orch = ResearchOrchestrator(ws)
        report = ws.check_consistency()
        n_before = len(report.obstructions)
        orch._repair_obstructions(report)
        report2 = ws.check_consistency()
        n_after = len(report2.obstructions)
        assert n_after <= n_before

    def test_trust_bounded(self, consistent_workspace):
        """Trust is always in [0, 1]."""
        orch = ResearchOrchestrator(
            consistent_workspace, strategy=ResearchStrategy.FIXED)
        result = orch.run()
        for trust_val in result.final_trust.values():
            assert 0.0 <= trust_val <= 1.0

    def test_obligation_decay_bounded(self):
        """Obligation vector stays bounded (Proposition 5.2 from Comet-H)."""
        orch = ResearchOrchestrator(
            WorkspaceSite.from_artifacts(), max_moves=100)
        for _ in range(100):
            orch._obligation_vector = [1.0] * 5
            orch._decay_obligations()
        # After decay, all should be < 1
        for val in orch._obligation_vector:
            assert val < 1.0


# ═══════════════════════════════════════════════════════════════════════
#  12. Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_max_moves(self, consistent_workspace):
        orch = ResearchOrchestrator(
            consistent_workspace, max_moves=0)
        result = orch.run()
        assert result.total_moves == 0

    def test_single_move(self, consistent_workspace):
        orch = ResearchOrchestrator(
            consistent_workspace, max_moves=1)
        result = orch.run()
        assert result.total_moves <= 1

    def test_update_then_check(self, consistent_workspace):
        ws = consistent_workspace
        ws.update_surface(SurfaceKind.EVIDENCE, {"benchmark_f1": 0.999})
        report = ws.check_consistency()
        # Claims still say 0.768 but evidence now says 0.999
        numerical_obs = [o for o in report.obstructions
                        if o.kind == ObstructionKind.NUMERICAL_MISMATCH]
        assert len(numerical_obs) >= 1

    def test_all_strategies_terminate(self, consistent_workspace):
        for strategy in ResearchStrategy:
            orch = ResearchOrchestrator(
                WorkspaceSite.from_artifacts(
                    evidence={"x": 1.0}, claims="x=1.0"),
                strategy=strategy, max_moves=10)
            result = orch.run()
            assert result.total_moves <= 10


# ═══════════════════════════════════════════════════════════════════════
#  13. Data structure tests
# ═══════════════════════════════════════════════════════════════════════

class TestDataStructures:
    """Test the data structures themselves."""

    def test_surface_kind_values(self):
        assert SurfaceKind.THEORY.value == "theory"
        assert SurfaceKind.CODE.value == "code"
        assert SurfaceKind.EVIDENCE.value == "evidence"
        assert SurfaceKind.CLAIMS.value == "claims"

    def test_morphism_type_values(self):
        assert MorphismType.IMPL.value == "impl"
        assert MorphismType.VALIDATE.value == "validate"

    def test_obstruction_kind_values(self):
        assert ObstructionKind.HALLUCINATION.value == "hallucination"
        assert ObstructionKind.STALE_SECTION.value == "stale_section"
        assert ObstructionKind.SPEC_DRIFT.value == "spec_drift"

    def test_artifact_surface_hash(self):
        s1 = ArtifactSurface(kind=SurfaceKind.CODE, content="hello")
        s2 = ArtifactSurface(kind=SurfaceKind.CODE, content="hello")
        assert s1.content_hash == s2.content_hash

    def test_artifact_surface_different_content_different_hash(self):
        s1 = ArtifactSurface(kind=SurfaceKind.CODE, content="hello")
        s2 = ArtifactSurface(kind=SurfaceKind.CODE, content="world")
        assert s1.content_hash != s2.content_hash

    def test_workspace_obstruction_has_id(self):
        obs = WorkspaceObstruction()
        assert obs.obstruction_id.startswith("obs-")

    def test_move_record_fields(self):
        rec = MoveRecord(
            move=MoveKind.CONSTRUCT_THEORY,
            phase=PhaseKind.SEED,
        )
        assert rec.success is True
        assert rec.elapsed == 0.0

    def test_consistency_report_consistent(self):
        r = ConsistencyReport(consistent=True, H1="0", overlaps_checked=6, overlaps_passed=6)
        assert r.consistency_rate == 1.0

    def test_consistency_report_inconsistent(self):
        r = ConsistencyReport(
            consistent=False, H1="2 obstructions",
            overlaps_checked=6, overlaps_passed=4, overlaps_failed=2)
        assert abs(r.consistency_rate - 4/6) < 1e-6
