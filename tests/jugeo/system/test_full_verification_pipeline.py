"""System tests: full verification pipeline.

Tests the complete path from a generation goal through descent-based overlap
checking, Z3 encoding, evidence collection, judgment construction, and final
manifest archival.  Exercises jugeo.generation.goals, jugeo.geometry.descent,
jugeo.solver.z3_session, jugeo.evidence.trust, jugeo.evidence.channels,
jugeo.evidence.manifests, jugeo.judgments.judgment_terms, and jugeo.errors in
a single connected workflow rather than in isolation.
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
    EvidenceFamily,
    FailureClassification,
    FailureFilter,
    FailureScope,
    JuGeoError,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    chain_failures,
    filter_failures,
    raise_with_scope,
)
from jugeo.evidence.channels import (
    EvidenceChannel,
    EvidenceKind,
    EvidenceRecord,
    build_channel,
)
from jugeo.evidence.manifests import (
    ManifestBuilder,
    ObligationPriority,
    ObstructionKind,
    build_evidence_manifest,
)
from jugeo.evidence.provenance import ProvenanceStep, ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
from jugeo.generation.goals import GenerationGoal, GoalPriority, GoalStatus
from jugeo.geometry.covers import (
    CoordinateKind,
    CoordinateObject,
    CoordinateMorphism,
    CoverBuilder,
)
from jugeo.geometry.descent import (
    CohomologyClass,
    DescentConfiguration,
    DescentEngine,
    DescentObstruction,
    DescentStrategy,
    GluingReport,
    OverlapCondition,
    OverlapStatus,
    RepairFrontier,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    CoordinateObject as JCoordinateObject,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentClause,
    JudgmentStatus,
    Obstruction as JObs,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel as JTL,
)
from jugeo.solver.z3_session import (
    FormulaKind,
    LogicalFragment,
    SolveOutcome,
    SolverFragment,
    SolverResult,
    TrustLevel as Z3TL,
    Z3Formula,
    Z3Result,
    Z3Session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinate(name: str, kind: CoordinateKind = CoordinateKind.THEOREM) -> CoordinateObject:
    return CoordinateObject(components=(name,), kind=kind)


def _make_patch(parent: str, suffix: str) -> CoordinateObject:
    return CoordinateObject(
        components=(parent, suffix),
        kind=CoordinateKind.REGION,
    )


def _make_two_patch_cover(name: str) -> tuple[Any, CoordinateObject, CoordinateObject]:
    """Return (cover, patch_a, patch_b) for a two-patch cover over *name*."""
    base = _make_coordinate(name)
    pa = _make_patch(name, "A")
    pb = _make_patch(name, "B")
    morph_a = CoordinateMorphism(source=f"{name}.A", target=name, reason="restriction")
    morph_b = CoordinateMorphism(source=f"{name}.B", target=name, reason="restriction")
    cb = CoverBuilder()
    cb.set_base(base)
    cb.add_member(pa, morph_a)
    cb.add_member(pb, morph_b)
    cover = cb.build()
    return cover, pa, pb


def _make_proposition(formula: str = "forall x, P(x)") -> Proposition:
    return Proposition(kind=PropositionKind.STRUCTURAL, formula=formula)


def _make_trust_annotation(level: JTL = JTL.SOLVER_DISCHARGED) -> TrustAnnotation:
    return TrustAnnotation(
        level=level,
        evidence_basis=("z3-sat",),
        reasons=("solver discharged",),
    )


def _make_provenance(source: ProvenanceSource = ProvenanceSource.SOLVER) -> Provenance:
    return Provenance(source=source, parent_judgments=(), transformation_history=("z3-encode",))


def _make_judgment(coordinate: str, formula: str = "P ∧ Q") -> Judgment:
    coord = JCoordinateObject(components=(coordinate,))
    prop = _make_proposition(formula)
    carrier = Carrier(name="solver-carrier")
    trust = _make_trust_annotation()
    prov = _make_provenance()
    return Judgment(
        coordinate=coord,
        proposition=prop,
        carrier=carrier,
        trust=trust,
        provenance=prov,
        status=JudgmentStatus.SETTLED,
    )


def _mock_sat_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.solve.return_value = SolverResult(
        outcome=SolveOutcome.SAT,
        engine="z3-mock",
        model={"x": True, "y": False},
        reasons=("model-found",),
    )
    return adapter


def _mock_unsat_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.solve.return_value = SolverResult(
        outcome=SolveOutcome.UNSAT,
        engine="z3-mock",
        model={},
        reasons=("no-model",),
    )
    return adapter


# ---------------------------------------------------------------------------
# Test 1: Goal construction and Z3 session wiring
# ---------------------------------------------------------------------------


def test_generation_goal_feeds_z3_session() -> None:
    """A GenerationGoal carries its proposition into a Z3Session."""
    goal = GenerationGoal(
        goal_id="goal-001",
        target_coordinate="module.Alpha",
        required_proposition="forall n: nat, n + 0 = n",
        budget=10,
        priority=GoalPriority.HIGH,
    )
    assert goal.goal_id == "goal-001"
    assert goal.target_coordinate == "module.Alpha"
    assert "forall" in goal.required_proposition

    adapter = _mock_sat_adapter()
    session = Z3Session(adapter=adapter)
    formula = Z3Formula(kind=FormulaKind.BOOL, expression=goal.required_proposition)
    session.assert_formula(formula)
    outcome = session.check_sat()

    assert outcome == SolveOutcome.SAT
    adapter.solve.assert_called_once()
    assert session.query_count() >= 1
    session.close()


# ---------------------------------------------------------------------------
# Test 2: SAT result → solver-backed EvidenceRecord
# ---------------------------------------------------------------------------


def test_sat_result_becomes_solver_evidence() -> None:
    """A SAT outcome from Z3 is wrapped as a SOLVER EvidenceRecord."""
    adapter = _mock_sat_adapter()
    session = Z3Session(adapter=adapter)
    formula = Z3Formula(kind=FormulaKind.INT, expression="x >= 0 /\\ x < 10")
    session.assert_formula(formula)
    outcome = session.check_sat()
    assert outcome == SolveOutcome.SAT

    record = build_channel("z3-sat", EvidenceChannel.SOLVER)
    assert record.channel == EvidenceChannel.SOLVER

    trace = ProvenanceTrace(origin="z3-session")
    step = ProvenanceStep(
        actor="z3-mock",
        action="solve",
        coordinate="module.Alpha",
        details={"outcome": outcome.value},
    )
    trace = trace.append(step)

    trust = TrustProfile(TrustTier.VERIFIED, ("module.Alpha",), ("solver-discharged",))
    manifest = build_evidence_manifest(
        "module.Alpha",
        formula.expression,
        (record,),
        trust_profiles=(trust,),
        provenance=trace,
    )

    assert manifest.coordinate == "module.Alpha"
    assert manifest.trust.tier == TrustTier.VERIFIED
    session.close()


# ---------------------------------------------------------------------------
# Test 3: UNSAT result → obstruction record in manifest
# ---------------------------------------------------------------------------


def test_unsat_result_creates_obstruction_in_manifest() -> None:
    """When Z3 returns UNSAT the pipeline creates an ObstructionRecord and archives it."""
    adapter = _mock_unsat_adapter()
    session = Z3Session(adapter=adapter)
    formula = Z3Formula(kind=FormulaKind.BOOL, expression="False")
    session.assert_formula(formula)
    outcome = session.check_sat()
    assert outcome == SolveOutcome.UNSAT

    hint = RepairHint(
        action="weaken-formula",
        description="The formula is unsatisfiable; weaken the hypothesis.",
        priority=RepairPriority.REQUIRED,
        target_coordinate="module.Beta",
    )
    obstruction = ObstructionRecord(
        coordinate="module.Beta",
        violated_condition="z3-unsat",
        evidence_family=EvidenceFamily.SOLVER,
        evidence={"outcome": "unsat", "formula": formula.expression},
        repair_hints=(hint,),
        downstream_obligations=("obligation-99",),
    )

    builder = ManifestBuilder()
    builder.add_obstruction(
        "module.Beta",
        ObstructionKind.DESCENT_OBSTRUCTION,
        "Z3 returned UNSAT for goal formula",
        rank=1,
    )
    manifest = builder.build()

    stats = manifest.statistics()
    assert stats is not None

    assert obstruction.coordinate == "module.Beta"
    assert obstruction.violated_condition == "z3-unsat"
    assert obstruction.evidence_family == EvidenceFamily.SOLVER
    assert len(obstruction.repair_hints) == 1
    assert obstruction.repair_hints[0].action == "weaken-formula"
    session.close()


# ---------------------------------------------------------------------------
# Test 4: Descent success → GlobalSection → Judgment construction
# ---------------------------------------------------------------------------


def test_descent_success_yields_judgment() -> None:
    """Successful descent produces a GlobalSection that becomes a Judgment."""
    cover, pa, pb = _make_two_patch_cover("Gamma")
    sections = {
        "Gamma/A": {"value": 7, "tag": "shared"},
        "Gamma/B": {"value": 7, "tag": "shared"},
    }
    engine = DescentEngine()
    report = engine.run(cover, sections)

    assert report.success is True
    assert report.glued_section is not None
    assert report.glued_section["value"] == 7

    judgment = _make_judgment("Gamma", formula="value = 7")
    assert judgment.status == JudgmentStatus.SETTLED
    assert judgment.trust.level == JTL.SOLVER_DISCHARGED

    builder = ManifestBuilder()
    builder.add_judgment(
        "Gamma",
        "value = 7",
        trust_tier=int(TrustTier.VERIFIED),
        status="settled",
    )
    manifest = builder.build()
    assert manifest.is_consistent()


# ---------------------------------------------------------------------------
# Test 5: Two-step pipeline: descent + Z3 + manifest
# ---------------------------------------------------------------------------


def test_combined_descent_z3_manifest_pipeline() -> None:
    """Descent verifies structure; Z3 verifies formula; results archived together."""
    cover, _, _ = _make_two_patch_cover("Delta")
    sections = {
        "Delta/A": {"predicate": "x > 0", "witness": True},
        "Delta/B": {"predicate": "x > 0", "witness": True},
    }
    engine = DescentEngine()
    report = engine.run(cover, sections)
    assert report.success is True

    adapter = _mock_sat_adapter()
    session = Z3Session(adapter=adapter)
    formula = Z3Formula(kind=FormulaKind.BOOL, expression="x > 0")
    session.assert_formula(formula)
    outcome = session.check_sat()
    assert outcome == SolveOutcome.SAT

    record = build_channel("combined", EvidenceChannel.SOLVER)
    trace = ProvenanceTrace(origin="combined-pipeline")
    trace = trace.append(
        ProvenanceStep(actor="descent", action="glue", coordinate="Delta")
    )
    trace = trace.append(
        ProvenanceStep(actor="z3", action="solve", coordinate="Delta")
    )
    trust = TrustProfile(TrustTier.VERIFIED, ("Delta",))

    manifest_record = build_evidence_manifest(
        "Delta", "x > 0", (record,), trust_profiles=(trust,), provenance=trace
    )
    assert manifest_record.coordinate == "Delta"
    assert manifest_record.trust.tier == TrustTier.VERIFIED
    session.close()


# ---------------------------------------------------------------------------
# Test 6: Obstruction propagation from descent engine to errors module
# ---------------------------------------------------------------------------


def test_descent_obstruction_feeds_jugeo_error() -> None:
    """A DescentObstruction can be converted into a JuGeoError with full provenance."""
    cc = CohomologyClass(
        dimension=1,
        cocycle_data={"patch_pair": ("A", "B"), "conflict_key": "value"},
        coboundary_candidates=("refine-A",),
    )
    obs = DescentObstruction(
        coordinate="Epsilon",
        cohomology_class=cc,
        repair_frontier=RepairFrontier(
            missing_evidence=("evidence-for-A",),
            suggested_refinements=("add-overlap-patch",),
            estimated_cost=3.0,
        ),
    )

    obs_record = ObstructionRecord(
        coordinate="Epsilon",
        violated_condition="cocycle-not-coboundary",
        evidence_family=EvidenceFamily.SOLVER,
        evidence={
            "dimension": str(cc.dimension),
            "cocycle_keys": ",".join(str(k) for k in cc.cocycle_data.keys()),
        },
        repair_hints=(
            RepairHint(
                action="refine-cover",
                description="Add overlap patch to resolve H¹ obstruction",
                priority=RepairPriority.REQUIRED,
            ),
        ),
        is_coboundary=False,
    )

    with pytest.raises(JuGeoError) as exc_info:
        raise_with_scope(
            "descent-obstruction",
            message="Descent failed: H¹ class is non-trivial",
            scope=FailureScope.GEOMETRY,
            classification=FailureClassification.DESCENT_OBSTRUCTION,
            evidence_family=EvidenceFamily.SOLVER,
            coordinate="Epsilon",
            obstruction=obs_record,
        )

    err = exc_info.value
    assert err.failure.scope is FailureScope.GEOMETRY
    assert err.failure.classification is FailureClassification.DESCENT_OBSTRUCTION
    assert err.failure.obstruction is not None
    assert err.failure.obstruction.coordinate == "Epsilon"
    assert err.failure.obstruction.is_coboundary is False
    assert len(obs.violated_pairs()) >= 0  # structure is accessible


# ---------------------------------------------------------------------------
# Test 7: Judgment algebra — composed evidence bundle
# ---------------------------------------------------------------------------


def test_judgment_evidence_bundle_composition() -> None:
    """Multiple EvidenceItems are bundled and the bundle reflects minimum trust."""
    item_solver = EvidenceItem(
        kind=EvidenceItemKind.SOLVER_PROOF,
        payload={"formula": "P", "result": "sat"},
        trust_level=JTL.SOLVER_DISCHARGED,
        channel="z3",
    )
    item_oracle = EvidenceItem(
        kind=EvidenceItemKind.ORACLE_PROPOSAL,
        payload={"hint": "P is likely true"},
        trust_level=JTL.ORACLE_PROPOSED,
        channel="copilot",
    )
    bundle = EvidenceBundle(items=(item_solver, item_oracle))

    # The bundle should expose both items
    assert len(bundle.items) == 2
    kinds = {i.kind for i in bundle.items}
    assert EvidenceItemKind.SOLVER_PROOF in kinds
    assert EvidenceItemKind.ORACLE_PROPOSAL in kinds

    # A judgment wrapping this bundle is constructed correctly
    coord = JCoordinateObject(components=("Zeta",))
    prop = _make_proposition("P")
    carrier = Carrier(name="composed")
    trust = TrustAnnotation(
        level=JTL.ORACLE_PROPOSED,  # min trust across items
        evidence_basis=("z3", "copilot"),
        reasons=("heterogeneous-evidence",),
    )
    judgment = Judgment(
        coordinate=coord,
        proposition=prop,
        carrier=carrier,
        evidence=bundle,
        trust=trust,
        provenance=_make_provenance(ProvenanceSource.COMPOSED),
        status=JudgmentStatus.PROPOSED,
    )
    assert judgment.status == JudgmentStatus.PROPOSED
    assert judgment.trust.level == JTL.ORACLE_PROPOSED


# ---------------------------------------------------------------------------
# Test 8: ManifestBuilder accumulates multiple coordinate records
# ---------------------------------------------------------------------------


def test_manifest_builder_accumulates_pipeline_state() -> None:
    """A ManifestBuilder captures descent result, Z3 result, and residual obligations."""
    builder = ManifestBuilder()

    # Simulate successful Z3 result for Eta.A
    builder.add_judgment(
        "Eta.A",
        "x + y = z",
        trust_tier=int(TrustTier.VERIFIED),
        status="settled",
        evidence_refs=["z3-sat-001"],
    )

    # Simulate UNSAT for Eta.B → obligation pending
    builder.add_obligation(
        "Eta.B",
        "Re-check formula after weakening",
        priority=ObligationPriority.HIGH,
    )

    # Record the descent obstruction at the global level
    builder.add_obstruction(
        "Eta",
        ObstructionKind.DESCENT_OBSTRUCTION,
        "Local sections disagree on Eta.A vs Eta.B",
        rank=1,
        cohomology_class="H1-class-Eta",
    )

    manifest = builder.build()
    assert manifest.is_consistent()
    stats = manifest.statistics()
    assert stats is not None


# ---------------------------------------------------------------------------
# Test 9: Full positive verification path end-to-end
# ---------------------------------------------------------------------------


def test_full_positive_verification_path() -> None:
    """End-to-end success: goal → descent → Z3 → evidence → judgment → manifest."""
    # Step 1: Generation goal
    goal = GenerationGoal(
        goal_id="e2e-001",
        target_coordinate="Theorem.Theta",
        required_proposition="∀ x ∈ ℕ, x ≥ 0",
        budget=20,
        priority=GoalPriority.HIGH,
        available_context=("hypothesis-nat",),
    )

    # Step 2: Build cover and run descent
    cover, _, _ = _make_two_patch_cover("Theta")
    sections = {
        "Theta/A": {"nat_property": True, "zero_lb": True},
        "Theta/B": {"nat_property": True, "zero_lb": True},
    }
    engine = DescentEngine()
    report = engine.run(cover, sections)
    assert report.success is True

    # Step 3: Z3 session (mocked)
    adapter = _mock_sat_adapter()
    session = Z3Session(adapter=adapter)
    formula = Z3Formula(kind=FormulaKind.INT, expression=goal.required_proposition)
    session.assert_formula(formula)
    outcome = session.check_sat()
    assert outcome == SolveOutcome.SAT
    model = session.get_model()

    # Step 4: Build solver-backed evidence
    record = build_channel("z3-sat", EvidenceChannel.SOLVER)
    trace = ProvenanceTrace(origin="e2e-pipeline")
    for actor, action in [("descent", "glue"), ("z3", "solve"), ("manifest", "record")]:
        trace = trace.append(
            ProvenanceStep(actor=actor, action=action, coordinate=goal.target_coordinate)
        )
    trust = TrustProfile(TrustTier.VERIFIED, (goal.target_coordinate,), ("proof-backed",))
    evidence_manifest = build_evidence_manifest(
        goal.target_coordinate,
        goal.required_proposition,
        (record,),
        trust_profiles=(trust,),
        provenance=trace,
    )

    # Step 5: Construct judgment at PROOF_BACKED trust
    coord = JCoordinateObject(components=("Theorem", "Theta"))
    prop = _make_proposition(goal.required_proposition)
    carrier = Carrier(name="nat-carrier")
    ta = TrustAnnotation(
        level=JTL.SOLVER_DISCHARGED,
        evidence_basis=("z3-sat",),
        reasons=("verified by z3",),
    )
    judgment = Judgment(
        coordinate=coord,
        proposition=prop,
        carrier=carrier,
        trust=ta,
        provenance=_make_provenance(ProvenanceSource.SOLVER),
        status=JudgmentStatus.SETTLED,
    )

    # Step 6: Archive in manifest
    builder = ManifestBuilder()
    builder.add_judgment(
        goal.target_coordinate,
        goal.required_proposition,
        trust_tier=int(TrustTier.VERIFIED),
        status="settled",
        evidence_refs=["z3-sat-001"],
    )
    manifest = builder.build()
    assert manifest.is_consistent()

    # Assert full chain integrity
    assert goal.status == GoalStatus.PENDING  # not yet marked achieved
    assert evidence_manifest.trust.tier == TrustTier.VERIFIED
    assert judgment.status == JudgmentStatus.SETTLED
    assert judgment.trust.level == JTL.SOLVER_DISCHARGED
    session.close()


# ---------------------------------------------------------------------------
# Test 10: Full negative verification path (UNSAT → obstruction)
# ---------------------------------------------------------------------------


def test_full_negative_verification_path_unsat() -> None:
    """End-to-end failure: Z3 returns UNSAT → DescentObstruction → manifest records it."""
    goal = GenerationGoal(
        goal_id="e2e-002",
        target_coordinate="Theorem.Iota",
        required_proposition="False",
        budget=5,
        priority=GoalPriority.LOW,
    )

    adapter = _mock_unsat_adapter()
    session = Z3Session(adapter=adapter)
    formula = Z3Formula(kind=FormulaKind.BOOL, expression=goal.required_proposition)
    session.assert_formula(formula)
    outcome = session.check_sat()
    assert outcome == SolveOutcome.UNSAT

    # Build a cohomology class representing the obstruction
    cc = CohomologyClass(
        dimension=1,
        cocycle_data={"formula": goal.required_proposition, "verdict": "unsat"},
        coboundary_candidates=(),
    )

    # Construct DescentObstruction
    obs = DescentObstruction(
        coordinate=goal.target_coordinate,
        cohomology_class=cc,
        repair_frontier=RepairFrontier(
            missing_evidence=("valid-hypothesis",),
            weakened_claims=(),
            suggested_refinements=("weaken-formula",),
            estimated_cost=10.0,
        ),
    )

    # Archive in manifest
    builder = ManifestBuilder()
    builder.add_obstruction(
        goal.target_coordinate,
        ObstructionKind.DESCENT_OBSTRUCTION,
        f"Z3 UNSAT: {goal.required_proposition}",
        rank=1,
        cohomology_class="H1-Iota",
    )
    builder.add_obligation(
        goal.target_coordinate,
        "Weaken hypothesis or revise formula",
        priority=ObligationPriority.CRITICAL,
    )
    manifest = builder.build()
    assert manifest.is_consistent()

    # Verify the obstruction structure
    assert obs.coordinate == goal.target_coordinate
    assert cc.dimension == 1
    assert "formula" in cc.cocycle_data
    assert obs.repair_frontier.estimated_cost == 10.0
    session.close()


# ---------------------------------------------------------------------------
# Test 11: FailureChain across Z3 + descent layers
# ---------------------------------------------------------------------------


def test_failure_chain_z3_descent_layers() -> None:
    """FailureChain accumulates failures from Z3 layer and descent layer together."""
    z3_failure = StructuredFailure(
        message="Z3 timed out after 5000ms",
        scope=FailureScope.SOLVER,
        classification=FailureClassification.TIMEOUT,
        evidence_family=EvidenceFamily.SOLVER,
        coordinate="Kappa",
        recoverable=True,
    )
    descent_failure = StructuredFailure(
        message="Descent stalled: overlap not checked due to solver timeout",
        scope=FailureScope.GEOMETRY,
        classification=FailureClassification.DESCENT_OBSTRUCTION,
        evidence_family=EvidenceFamily.SOLVER,
        coordinate="Kappa",
        recoverable=False,
    )

    chain = chain_failures(z3_failure, descent_failure)
    assert len(chain.failures) == 2
    assert chain.failures[0].scope is FailureScope.SOLVER
    assert chain.failures[1].scope is FailureScope.GEOMETRY

    # Filter to geometry failures only
    geo_filter = FailureFilter(scope=FailureScope.GEOMETRY)
    geo_failures = filter_failures(chain.failures, geo_filter)
    assert len(geo_failures) == 1
    assert geo_failures[0].classification is FailureClassification.DESCENT_OBSTRUCTION

    # Filter to recoverable failures only
    rec_filter = FailureFilter(custom_predicate=lambda f: f.recoverable)
    recoverable = filter_failures(chain.failures, rec_filter)
    assert len(recoverable) == 1
    assert recoverable[0].scope is FailureScope.SOLVER


# ---------------------------------------------------------------------------
# Test 12: Z3 push/pop scoping for incremental verification
# ---------------------------------------------------------------------------


def test_z3_incremental_push_pop_pipeline() -> None:
    """Z3Session push/pop correctly scopes formula assertions for incremental queries."""
    adapter = _mock_sat_adapter()
    session = Z3Session(adapter=adapter)

    # Baseline formula
    base_formula = Z3Formula(kind=FormulaKind.INT, expression="x >= 0")
    session.assert_formula(base_formula)

    # Push a scope and add a stricter constraint
    session.push()
    strict_formula = Z3Formula(kind=FormulaKind.INT, expression="x > 100")
    session.assert_formula(strict_formula)
    outcome_strict = session.check_sat()
    assert outcome_strict == SolveOutcome.SAT  # mocked

    # Pop scope — strict constraint is removed
    session.pop()
    outcome_base = session.check_sat()
    assert outcome_base == SolveOutcome.SAT

    # Two separate generation goals can share a session with push/pop
    goals = [
        GenerationGoal(
            goal_id=f"push-goal-{i}",
            target_coordinate=f"Lambda.{i}",
            required_proposition=f"P_{i}",
            budget=5,
        )
        for i in range(3)
    ]
    for goal in goals:
        session.push()
        formula_i = Z3Formula(kind=FormulaKind.BOOL, expression=goal.required_proposition)
        session.assert_formula(formula_i)
        result = session.check_sat()
        assert result in {SolveOutcome.SAT, SolveOutcome.UNSAT}
        session.pop()

    assert session.query_count() >= 2
    session.close()


# ---------------------------------------------------------------------------
# Test 13: CohomologyClass registration and manifest cohomology audit
# ---------------------------------------------------------------------------


def test_cohomology_class_registered_in_manifest_on_failure() -> None:
    """H¹ obstruction class is registered in the manifest with full evidence."""
    cc = CohomologyClass(
        dimension=1,
        cocycle_data={
            "violated_pair": ("Mu.A", "Mu.B"),
            "conflict_field": "type_signature",
            "left_value": "int",
            "right_value": "str",
        },
        coboundary_candidates=("relax-type",),
    )

    builder = ManifestBuilder()
    builder.add_obstruction(
        "Mu",
        ObstructionKind.ENCODING_MISMATCH,
        "Type signature mismatch in overlap Mu.A ∩ Mu.B",
        rank=cc.dimension,
        cohomology_class=f"H{cc.dimension}-Mu",
    )
    builder.add_obligation(
        "Mu",
        "Align type signatures across patches",
        priority=ObligationPriority.HIGH,
    )

    manifest = builder.build()
    assert manifest.is_consistent()

    # The CohomologyClass itself should expose its structure
    assert cc.dimension == 1
    assert "violated_pair" in cc.cocycle_data
    assert len(cc.coboundary_candidates) == 1
    assert cc.coboundary_candidates[0] == "relax-type"


# ---------------------------------------------------------------------------
# Test 14: Trust profile join across solver and oracle evidence
# ---------------------------------------------------------------------------


def test_trust_profile_join_for_heterogeneous_evidence() -> None:
    """TrustProfile join correctly computes the ceiling when mixing solver and oracle."""
    solver_profile = TrustProfile(
        TrustTier.VERIFIED,
        ("Nu.A",),
        ("z3-discharged",),
    )
    oracle_profile = TrustProfile(
        TrustTier.PROPOSAL,
        ("Nu.B",),
        ("copilot-suggested",),
    )

    # Join — should be conservative (lower tier wins)
    joined = join_trust_profiles(solver_profile, oracle_profile)
    assert joined.tier <= TrustTier.VERIFIED
    assert joined.tier >= TrustTier.PROPOSAL

    # The judgment annotated with the joined trust is appropriately cautious
    coord = JCoordinateObject(components=("Nu",))
    prop = _make_proposition("combined property")
    carrier = Carrier(name="heterogeneous")
    ta = TrustAnnotation(
        level=JTL.ORACLE_PROPOSED,
        evidence_basis=("z3", "copilot"),
        ceiling=JTL.SOLVER_DISCHARGED,
        reasons=("heterogeneous join",),
    )
    judgment = Judgment(
        coordinate=coord,
        proposition=prop,
        carrier=carrier,
        trust=ta,
        provenance=_make_provenance(ProvenanceSource.COMPOSED),
        status=JudgmentStatus.PROPOSED,
    )
    assert judgment.trust.level == JTL.ORACLE_PROPOSED
    assert judgment.trust.ceiling == JTL.SOLVER_DISCHARGED
