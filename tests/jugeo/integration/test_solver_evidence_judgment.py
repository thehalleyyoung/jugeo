"""Integration tests: Z3 solver × evidence × judgment integration.

Tests that Z3 solver results flow correctly into the evidence trust pipeline
and produce JudgmentTuples with properly promoted trust levels. Also verifies
that solver failures generate ObstructionRecord entries with correct provenance.

Theory2 invariants under test
-------------------------------
* Solver-backed evidence promotes trust to SOLVER_DISCHARGED (not higher).
* No silent promotion: trust only changes through explicit paths.
* ObstructionRecord is persistent when solver cannot discharge.
* Evidence kinds are preserved after solver produces results.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists()
)
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

# ---------------------------------------------------------------------------
# Solver imports
# ---------------------------------------------------------------------------
from jugeo.solver.z3_session import (
    BuiltinAdapter,
    FormulaKind,
    SolveOutcome,
    SolverResult,
    Z3Formula,
    z3_available,
)
from jugeo.solver.fragments import (
    SolverFragment,
    classify_fragment,
    LogicalFragment,
)
from jugeo.solver.router import (
    BackendDescriptor,
    BackendKind,
    JurisdictionChecker,
    RouterConfiguration,
    RoutingStrategyKind,
    SolverRouter,
    VerificationDomain,
)

# ---------------------------------------------------------------------------
# Evidence imports
# ---------------------------------------------------------------------------
from jugeo.evidence.trust import (
    TrustAlgebra,
    TrustCeiling,
    TrustLevel,
    TrustPromotion,
    TrustTier,
    TrustProfile,
)

# ---------------------------------------------------------------------------
# Judgment imports
# ---------------------------------------------------------------------------
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel as JudgmentTrustLevel,
)

# ---------------------------------------------------------------------------
# Error imports
# ---------------------------------------------------------------------------
from jugeo.errors import (
    EvidenceFamily,
    FailureClassification,
    FailureScope,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    JuGeoError,
    FailureChain,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALGEBRA = TrustAlgebra()


def _coord(*parts: str) -> Coordinate:
    return Coordinate(components=parts, kind=CoordinateKind.REGION)


def _proposition(formula: str) -> Proposition:
    return Proposition(kind=PropositionKind.STRUCTURAL, formula=formula)


def _carrier(name: str = "TestType") -> Carrier:
    return Carrier(name=name)


def _judgment(
    coord: Coordinate,
    formula: str = "P",
    trust: JudgmentTrustLevel = JudgmentTrustLevel.UNVERIFIED,
) -> Judgment:
    return Judgment(
        coordinate=coord,
        proposition=_proposition(formula),
        carrier=_carrier(),
        trust=TrustAnnotation(level=trust),
        provenance=Provenance(source=ProvenanceSource.SOLVER),
    )


def _solver_result(sat: bool, engine: str = "builtin") -> SolverResult:
    return SolverResult(
        outcome=SolveOutcome.SAT if sat else SolveOutcome.UNSAT,
        engine=engine,
        model={"x": True} if sat else {},
        reasons=("unit-test",) if sat else ("contradiction-detected",),
    )


def _make_backend(
    name: str,
    kind: BackendKind = BackendKind.Z3,
    domains: set[VerificationDomain] | None = None,
    trust_ceiling: TrustTier = TrustTier.VERIFIED,
) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        kind=kind,
        domains=frozenset(domains or {VerificationDomain.STRUCTURAL}),
        trust_ceiling=trust_ceiling,
        latency_ms=10.0,
        cost=1,
    )


# ---------------------------------------------------------------------------
# §1  BuiltinAdapter — basic SAT/UNSAT
# ---------------------------------------------------------------------------


class TestBuiltinAdapter:
    """BuiltinAdapter works without Z3 installed."""

    def test_sat_returns_sat_outcome(self) -> None:
        adapter = BuiltinAdapter()
        frag = SolverFragment(
            formula="x and not y",
            fragment=LogicalFragment.PROPOSITIONAL,
            clauses=("x", "noty"),
        )
        result = adapter.solve(frag)
        assert isinstance(result, SolverResult)
        assert result.outcome in (SolveOutcome.SAT, SolveOutcome.UNKNOWN)

    def test_contradiction_detected_as_unsat(self) -> None:
        adapter = BuiltinAdapter()
        frag = SolverFragment(
            formula="x and notx",
            fragment=LogicalFragment.PROPOSITIONAL,
            clauses=("x", "notx"),
        )
        result = adapter.solve(frag)
        assert result.is_unsat()

    def test_solver_result_is_not_a_boolean(self) -> None:
        result = _solver_result(sat=True)
        assert not isinstance(result, bool)
        assert isinstance(result, SolverResult)
        assert result.is_sat()
        assert not result.is_unsat()

    def test_solver_result_unsat(self) -> None:
        result = _solver_result(sat=False)
        assert result.is_unsat()
        assert not result.is_sat()

    def test_solver_result_to_dict(self) -> None:
        result = _solver_result(sat=True, engine="z3-builtin")
        d = result.to_dict()
        assert d["outcome"] == "sat"
        assert d["engine"] == "z3-builtin"
        assert "model" in d
        assert "reasons" in d


# ---------------------------------------------------------------------------
# §2  Solver result → evidence → trust promotion
# ---------------------------------------------------------------------------


class TestSolverResultToEvidence:
    """A solver SAT/UNSAT result should produce evidence at SOLVER_DISCHARGED tier."""

    def _result_to_evidence_item(
        self, result: SolverResult, coord: str = "mod/fn"
    ) -> EvidenceItem:
        """Simulate converting a SolverResult to an EvidenceItem."""
        if result.is_unsat():
            kind = EvidenceItemKind.SOLVER_PROOF
            trust = JudgmentTrustLevel.SOLVER_DISCHARGED
        else:
            kind = EvidenceItemKind.ORACLE_PROPOSAL
            trust = JudgmentTrustLevel.ORACLE_PROPOSED
        return EvidenceItem(
            kind=kind,
            payload={"outcome": result.outcome.value, "engine": result.engine},
            trust_level=trust,
            channel=result.engine,
            provenance=(f"solver:{coord}",),
        )

    def test_unsat_result_gives_solver_discharged_trust(self) -> None:
        result = _solver_result(sat=False, engine="z3")
        ev = self._result_to_evidence_item(result, "mod/constraint")
        assert ev.kind == EvidenceItemKind.SOLVER_PROOF
        assert int(ev.trust_level) == int(JudgmentTrustLevel.SOLVER_DISCHARGED)

    def test_sat_result_does_not_give_solver_trust(self) -> None:
        """A satisfiable result (model found) is not a proof — trust stays lower."""
        result = _solver_result(sat=True, engine="z3")
        ev = self._result_to_evidence_item(result, "mod/fn")
        assert ev.kind != EvidenceItemKind.SOLVER_PROOF
        assert int(ev.trust_level) < int(JudgmentTrustLevel.SOLVER_DISCHARGED)

    def test_judgment_trust_promotion_after_solver_proof(self) -> None:
        """After solver proof, judgment trust may be promoted explicitly."""
        coord = _coord("mod", "constraint")
        j = _judgment(coord, "x >= 0", JudgmentTrustLevel.UNVERIFIED)
        # Simulate: solver proves UNSAT (i.e., ¬P is unsatisfiable → P holds)
        result = _solver_result(sat=False)
        ev = self._result_to_evidence_item(result, "mod/constraint")
        j2 = j.merge_evidence(EvidenceBundle(items=(ev,)))
        # Evidence merged but trust not silently promoted
        assert int(j2.trust.level) == int(JudgmentTrustLevel.UNVERIFIED)
        # Explicit promotion with reason
        j3 = j2.strengthen(reason="z3-unsat-proof-verified")
        assert int(j3.trust.level) > int(JudgmentTrustLevel.UNVERIFIED)

    def test_evidence_kind_preserved_after_merge(self) -> None:
        """EvidenceItem.kind must survive merge into EvidenceBundle."""
        coord = _coord("srv", "handler")
        j = _judgment(coord, "handler_safe")
        solver_ev = EvidenceItem(
            kind=EvidenceItemKind.SOLVER_PROOF,
            payload={"outcome": "unsat"},
            trust_level=JudgmentTrustLevel.SOLVER_DISCHARGED,
            channel="z3",
        )
        j2 = j.merge_evidence(EvidenceBundle(items=(solver_ev,)))
        # Kind must be preserved
        items_by_kind = j2.evidence.by_kind(EvidenceItemKind.SOLVER_PROOF)
        assert len(items_by_kind.items) == 1
        assert items_by_kind.items[0].kind == EvidenceItemKind.SOLVER_PROOF

    def test_solver_trust_ceiling_in_trust_algebra(self) -> None:
        """TrustCeiling for solver channel caps at SOLVER_DISCHARGED."""
        ceiling = TrustCeiling()
        # Solver channel cannot claim MECHANICALLY_VERIFIED
        clamped = ceiling.enforce(TrustLevel.MECHANICALLY_VERIFIED, "solver")
        assert clamped <= TrustLevel.SOLVER_DISCHARGED

    def test_evidence_provenance_recorded_from_solver(self) -> None:
        """Evidence produced by solver carries solver provenance tag."""
        result = _solver_result(sat=False)
        ev = self._result_to_evidence_item(result, "mod/logic")
        assert any("solver" in tag.lower() for tag in ev.provenance)

    def test_trust_composition_solver_plus_oracle(self) -> None:
        """Composing solver evidence with oracle evidence yields min (oracle)."""
        solver_trust = TrustLevel.SOLVER_DISCHARGED
        oracle_trust = TrustLevel.ORACLE_PROPOSED
        composed = _ALGEBRA.compose(solver_trust, oracle_trust)
        assert composed is TrustLevel.ORACLE_PROPOSED


# ---------------------------------------------------------------------------
# §3  Solver failure → ObstructionRecord
# ---------------------------------------------------------------------------


class TestSolverFailureToObstruction:
    """When solver cannot discharge, an ObstructionRecord must be created."""

    def test_obstruction_record_created_on_timeout(self) -> None:
        """Simulate solver timeout → ObstructionRecord with SOLVER scope."""
        coord = "mod/complex_constraint"
        rec = ObstructionRecord(
            coordinate=coord,
            violated_condition="solver_timeout",
            evidence_family=EvidenceFamily.SOLVER,
            evidence={"outcome": "timeout", "engine": "z3", "elapsed_ms": 5000},
            downstream_obligations=("retry_with_larger_budget",),
        )
        assert rec.coordinate == coord
        assert rec.evidence_family == EvidenceFamily.SOLVER
        assert "solver_timeout" in rec.violated_condition
        assert len(rec.downstream_obligations) == 1

    def test_obstruction_record_is_not_coboundary_by_default(self) -> None:
        """Default obstruction is not trivially resolvable."""
        rec = ObstructionRecord(
            coordinate="svc/auth",
            violated_condition="undecidable_fragment",
            evidence_family=EvidenceFamily.PROOF,
        )
        # is_coboundary defaults to None (unknown)
        assert rec.is_coboundary is None

    def test_obstruction_record_with_repair_hint(self) -> None:
        rec = ObstructionRecord(
            coordinate="mod/fn",
            violated_condition="qf_nonlinear_not_supported",
            evidence_family=EvidenceFamily.SOLVER,
        )
        hint = RepairHint(
            action="split_constraint",
            description="Decompose nonlinear formula into linear sub-cases",
            priority=RepairPriority.HIGH,
            target_coordinate="mod/fn",
        )
        rec2 = rec.with_repair_hint(hint)
        assert len(rec2.repair_hints) == 1
        assert rec2.repair_hints[0].action == "split_constraint"

    def test_obstruction_record_with_downstream_effects(self) -> None:
        rec = ObstructionRecord(
            coordinate="api/handler",
            violated_condition="precondition_unverified",
            evidence_family=EvidenceFamily.SOLVER,
        )
        rec2 = rec.with_downstream("api/response_builder", "api/logger")
        assert "api/response_builder" in rec2.downstream_obligations
        assert "api/logger" in rec2.downstream_obligations

    def test_obstruction_record_serialization_roundtrip(self) -> None:
        rec = ObstructionRecord(
            coordinate="db/query",
            violated_condition="null_pointer_possible",
            evidence_family=EvidenceFamily.RUNTIME,
            evidence={"witness": "null_ref_in_test_42"},
            repair_hints=(
                RepairHint(
                    action="add_null_check",
                    description="Guard against null before dereference",
                    priority=RepairPriority.CRITICAL,
                    target_coordinate="db/query",
                ),
            ),
            downstream_obligations=("db/result_handler",),
        )
        d = rec.to_dict()
        rec2 = ObstructionRecord.from_dict(d)
        assert rec2.coordinate == "db/query"
        assert rec2.violated_condition == "null_pointer_possible"
        assert len(rec2.repair_hints) == 1
        assert rec2.repair_hints[0].action == "add_null_check"

    def test_failure_chain_collects_multiple_obstruction_records(self) -> None:
        failures = []
        for i in range(3):
            sf = StructuredFailure(
                code=f"err-{i}",
                message=f"Solver failure {i}",
                scope=FailureScope.SOLVER,
                classification=FailureClassification.LOCAL_REPAIR,
                coordinate=f"mod/fn_{i}",
            )
            failures.append(sf)
        from jugeo.errors import chain_failures
        chain = chain_failures(*failures)
        assert len(chain) == 3
        scopes = chain.scopes()
        assert FailureScope.SOLVER in scopes

    def test_judgment_with_obstruction_is_obstructed(self) -> None:
        coord = _coord("mod", "fn")
        j = _judgment(coord, "fn_safe")
        ob = Obstruction(
            coordinate_pair=("mod/fn", "mod/fn/inner"),
            description="solver_timeout_on_recursive_case",
            severity=3,
        )
        j2 = j.add_obstruction(ob)
        assert j2.status == JudgmentStatus.OBSTRUCTED
        assert j2.has_obstructions()
        assert j2.unresolved_obstruction_count() == 1


# ---------------------------------------------------------------------------
# §4  Router: trust ceilings and jurisdiction
# ---------------------------------------------------------------------------


class TestRouterTrustCeilings:
    """BackendDescriptor.trust_ceiling prevents silent promotion."""

    def test_backend_descriptor_trust_ceiling(self) -> None:
        be = _make_backend("z3-main", BackendKind.Z3, trust_ceiling=TrustTier.REVIEWED)
        assert be.trust_ceiling is TrustTier.REVIEWED

    def test_backend_effective_trust_clamped(self) -> None:
        be = _make_backend("copilot-backend", BackendKind.COPILOT, trust_ceiling=TrustTier.PROPOSAL)
        # Requesting VERIFIED tier → should be clamped to PROPOSAL
        effective = be.effective_trust(TrustTier.VERIFIED)
        assert effective is TrustTier.PROPOSAL

    def test_backend_effective_trust_not_promoted(self) -> None:
        be = _make_backend("solver", BackendKind.Z3, trust_ceiling=TrustTier.VERIFIED)
        effective = be.effective_trust(TrustTier.PROPOSAL)
        # Backend at PROPOSAL does not get silently upgraded to VERIFIED
        assert effective is TrustTier.PROPOSAL

    def test_copilot_backend_is_last_resort_by_design(self) -> None:
        be = _make_backend(
            "copilot",
            BackendKind.COPILOT,
            domains={VerificationDomain.SEMANTIC},
            trust_ceiling=TrustTier.PROPOSAL,
        )
        assert be.kind is BackendKind.COPILOT
        assert be.trust_ceiling is TrustTier.PROPOSAL

    def test_jurisdiction_checker_structural_domain(self) -> None:
        checker = JurisdictionChecker(strict=True)
        be = _make_backend("z3", BackendKind.Z3, domains={VerificationDomain.STRUCTURAL})
        # Z3 covers structural domain
        assert be.covers_domain(VerificationDomain.STRUCTURAL)
        assert not be.covers_domain(VerificationDomain.SEMANTIC)

    def test_jurisdiction_covers_any_and_all(self) -> None:
        be = _make_backend(
            "z3-full",
            BackendKind.Z3,
            domains={VerificationDomain.STRUCTURAL, VerificationDomain.ARITHMETIC},
        )
        assert be.covers_any({VerificationDomain.STRUCTURAL, VerificationDomain.HEAP})
        assert be.covers_all({VerificationDomain.STRUCTURAL, VerificationDomain.ARITHMETIC})
        assert not be.covers_all({VerificationDomain.STRUCTURAL, VerificationDomain.SEMANTIC})

    def test_router_configuration_add_remove_backend(self) -> None:
        cfg = RouterConfiguration()
        be = _make_backend("z3-test")
        cfg.add_backend(be)
        assert cfg.backend_by_name("z3-test") is be
        assert be in cfg.available_backends()
        removed = cfg.remove_backend("z3-test")
        assert removed is True
        assert cfg.backend_by_name("z3-test") is None

    def test_solver_router_creation(self) -> None:
        router = SolverRouter()
        assert router is not None

    def test_solver_router_with_strategy(self) -> None:
        router = SolverRouter(strategy=RoutingStrategyKind.MOST_TRUSTED)
        assert router.strategy == RoutingStrategyKind.MOST_TRUSTED


# ---------------------------------------------------------------------------
# §5  Fragment classification and Z3Formula wrappers
# ---------------------------------------------------------------------------


class TestFragmentClassificationAndFormula:
    """Fragment classifier identifies decidable theory families."""

    def test_classify_propositional_formula(self) -> None:
        frag = classify_fragment("x and y or not z")
        assert frag.fragment in (
            LogicalFragment.PROPOSITIONAL,
            LogicalFragment.QUANTIFIER_FREE,
        )
        assert isinstance(frag, SolverFragment)

    def test_classify_equality_formula(self) -> None:
        frag = classify_fragment("x = y")
        assert frag.fragment in (
            LogicalFragment.EQUALITY,
            LogicalFragment.QUANTIFIER_FREE,
        )

    def test_classify_implication_as_horn(self) -> None:
        frag = classify_fragment("x => y")
        assert frag.fragment in (
            LogicalFragment.HORN,
            LogicalFragment.QUANTIFIER_FREE,
        )

    def test_solver_fragment_clauses_split(self) -> None:
        frag = classify_fragment("a & b & c")
        assert len(frag.clauses) >= 1

    def test_z3_formula_boolean_kind(self) -> None:
        f = Z3Formula.boolean("x > 0")
        assert f.kind == FormulaKind.BOOL
        assert "x" in f.expression

    def test_z3_formula_integer_kind(self) -> None:
        f = Z3Formula.integer("n + 1")
        assert f.kind == FormulaKind.INT

    def test_z3_formula_negate(self) -> None:
        f = Z3Formula.boolean("x >= 0")
        neg = f.negate()
        assert neg.kind == FormulaKind.BOOL
        # Negation changes expression
        assert neg.expression != f.expression

    def test_z3_formula_conjoin(self) -> None:
        f1 = Z3Formula.boolean("x > 0")
        f2 = Z3Formula.boolean("y < 10")
        conj = f1.conjoin(f2)
        assert conj.kind == FormulaKind.BOOL
        assert "x" in conj.expression or "y" in conj.expression

    def test_z3_formula_simplify(self) -> None:
        f = Z3Formula.boolean("  x  >  0  ")
        simplified = f.simplify()
        assert isinstance(simplified, Z3Formula)

    def test_z3_availability_flag(self) -> None:
        # Just verify the flag is a boolean — test works either way
        available = z3_available()
        assert isinstance(available, bool)

    def test_builtin_adapter_empty_formula(self) -> None:
        adapter = BuiltinAdapter()
        frag = SolverFragment(
            formula="",
            fragment=LogicalFragment.QUANTIFIER_FREE,
            clauses=(),
        )
        result = adapter.solve(frag)
        assert result.outcome in (SolveOutcome.UNKNOWN, SolveOutcome.SAT)

    def test_builtin_adapter_sat_gives_model(self) -> None:
        adapter = BuiltinAdapter()
        frag = SolverFragment(
            formula="p",
            fragment=LogicalFragment.PROPOSITIONAL,
            clauses=("p",),
        )
        result = adapter.solve(frag)
        if result.is_sat():
            assert isinstance(result.model, dict)


# ---------------------------------------------------------------------------
# §6  Full cross-package pipeline: solver → evidence → judgment
# ---------------------------------------------------------------------------


class TestSolverEvidenceJudgmentPipeline:
    """End-to-end: solver result → evidence item → judgment trust promotion."""

    def test_full_pipeline_solver_discharged(self) -> None:
        """Simulate Z3 UNSAT → evidence → judgment at SOLVER_DISCHARGED."""
        coord = _coord("math", "constraint")
        j_initial = _judgment(coord, "x_plus_y_ge_zero", JudgmentTrustLevel.UNVERIFIED)
        # Solver proves UNSAT(¬P)
        solver_result = SolverResult(
            outcome=SolveOutcome.UNSAT,
            engine="z3-qf_lia",
            model={},
            reasons=("linear arithmetic proof",),
        )
        ev = EvidenceItem(
            kind=EvidenceItemKind.SOLVER_PROOF,
            payload={"outcome": "unsat", "engine": "z3-qf_lia"},
            trust_level=JudgmentTrustLevel.SOLVER_DISCHARGED,
            channel="z3-qf_lia",
            provenance=("solver:math/constraint",),
        )
        j2 = j_initial.merge_evidence(EvidenceBundle(items=(ev,)))
        # Trust not silently promoted by merge
        assert int(j2.trust.level) == int(JudgmentTrustLevel.UNVERIFIED)
        # Explicit promotion with reason
        j3 = j2.strengthen(
            reason=f"z3 unsat proof: {solver_result.engine}",
            target=JudgmentTrustLevel.SOLVER_DISCHARGED,
        )
        assert int(j3.trust.level) == int(JudgmentTrustLevel.SOLVER_DISCHARGED)
        # Evidence kind preserved
        assert len(j3.evidence.by_kind(EvidenceItemKind.SOLVER_PROOF).items) == 1

    def test_full_pipeline_solver_failure_creates_obstruction(self) -> None:
        """When solver times out, judgment gets obstruction, not silent UNVERIFIED."""
        coord = _coord("complex", "nlq")
        j = _judgment(coord, "nlq_holds", JudgmentTrustLevel.UNVERIFIED)
        # Simulate solver timeout
        timeout_result = SolverResult(
            outcome=SolveOutcome.TIMEOUT,
            engine="z3-nonlinear",
            model={},
            reasons=("timeout after 5000ms",),
        )
        ob = Obstruction(
            coordinate_pair=("complex/nlq", "complex/nlq/bound"),
            description=f"solver_{timeout_result.outcome.value}: {timeout_result.engine}",
            severity=2,
        )
        j2 = j.add_obstruction(ob)
        assert j2.status == JudgmentStatus.OBSTRUCTED
        assert j2.has_obstructions()
        # Trust did NOT get promoted
        assert int(j2.trust.level) == int(JudgmentTrustLevel.UNVERIFIED)

    def test_oracle_proposed_cannot_reach_solver_discharged(self) -> None:
        """ORACLE_PROPOSED cannot reach SOLVER_DISCHARGED without solver evidence."""
        coord = _coord("gen", "fn")
        oracle_j = Judgment(
            coordinate=coord,
            proposition=_proposition("fn_correct"),
            carrier=_carrier(),
            trust=TrustAnnotation(
                level=JudgmentTrustLevel.ORACLE_PROPOSED,
                ceiling=JudgmentTrustLevel.ORACLE_PROPOSED,
            ),
            provenance=Provenance(source=ProvenanceSource.ORACLE),
        )
        # Even after explicit promotion, ceiling prevents reaching SOLVER_DISCHARGED
        promoted = oracle_j.strengthen(
            reason="copilot says it is correct",
            target=JudgmentTrustLevel.SOLVER_DISCHARGED,
        )
        assert int(promoted.trust.level) <= int(JudgmentTrustLevel.ORACLE_PROPOSED)

    def test_judgment_with_multiple_solver_evidence(self) -> None:
        """Multiple solver proofs aggregate conservatively."""
        coord = _coord("net", "protocol")
        ev1 = EvidenceItem(
            kind=EvidenceItemKind.SOLVER_PROOF,
            trust_level=JudgmentTrustLevel.SOLVER_DISCHARGED,
            channel="z3-qf_uf",
            payload={"property": "safety"},
        )
        ev2 = EvidenceItem(
            kind=EvidenceItemKind.SOLVER_PROOF,
            trust_level=JudgmentTrustLevel.SOLVER_DISCHARGED,
            channel="z3-qf_lia",
            payload={"property": "liveness"},
        )
        bundle = EvidenceBundle(items=(ev1, ev2))
        j = Judgment(
            coordinate=coord,
            proposition=_proposition("protocol_correct"),
            carrier=_carrier("Protocol"),
            evidence=bundle,
            trust=TrustAnnotation(level=JudgmentTrustLevel.SOLVER_DISCHARGED),
        )
        # Both pieces preserved
        solver_items = j.evidence.by_kind(EvidenceItemKind.SOLVER_PROOF)
        assert len(solver_items.items) == 2
        # Trust floor accounts for both
        floor = j.trust_floor()
        assert int(floor) > 0

    def test_trust_level_hierarchy_for_solver_evidence(self) -> None:
        """Verify the strict ordering: VERIFIED_PROOF > SOLVER_DISCHARGED > ... > CONTRADICTED."""
        lvls = [
            JudgmentTrustLevel.CONTRADICTED,
            JudgmentTrustLevel.UNVERIFIED,
            JudgmentTrustLevel.ORACLE_PROPOSED,
            JudgmentTrustLevel.RUNTIME_WITNESSED,
            JudgmentTrustLevel.SOLVER_DISCHARGED,
            JudgmentTrustLevel.VERIFIED_PROOF,
        ]
        for i in range(len(lvls) - 1):
            assert lvls[i] < lvls[i + 1]

    def test_structured_failure_from_solver_timeout(self) -> None:
        sf = StructuredFailure(
            code="solver-timeout",
            message="Z3 did not complete within budget",
            scope=FailureScope.SOLVER,
            classification=FailureClassification.LOCAL_REPAIR,
            coordinate="complex/nlq",
            trust_at_failure="unverified",
        )
        assert sf.scope == FailureScope.SOLVER
        d = sf.to_dict()
        assert d["scope"] == "solver"
        assert d["code"] == "solver-timeout"

    def test_failure_chain_filter_by_solver_scope(self) -> None:
        sf1 = StructuredFailure(
            code="e1", message="msg1",
            scope=FailureScope.SOLVER,
            classification=FailureClassification.LOCAL_REPAIR,
            coordinate="a",
        )
        sf2 = StructuredFailure(
            code="e2", message="msg2",
            scope=FailureScope.GEOMETRY,
            classification=FailureClassification.DESCENT_OBSTRUCTION,
            coordinate="b",
        )
        from jugeo.errors import chain_failures
        chain = chain_failures(sf1, sf2)
        solver_only = chain.filter_by_scope(FailureScope.SOLVER)
        assert len(solver_only) == 1
        assert list(solver_only)[0].scope == FailureScope.SOLVER
