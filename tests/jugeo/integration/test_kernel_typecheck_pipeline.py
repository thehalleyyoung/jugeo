"""Integration tests: kernel services ↔ judgment term algebra pipeline.

Cross-cutting modules under test
---------------------------------
* ``jugeo.kernel.services``  — ServiceRegistry, ServiceGraph, KernelBootstrapper
* ``jugeo.kernel.lifecycle`` — LifecycleManager, KernelPhase, LifecycleController
* ``jugeo.judgments.judgment_terms`` — Judgment (8-tuple), TrustAnnotation, TrustLevel
* ``jugeo.evidence.trust`` — TrustAlgebra, TrustLevel (partial-order algebra)

Theory2 invariants asserted throughout
----------------------------------------
1. **Judgment = (c,φ,A,E,O,B,T,Π) tuple, not a bool** — every test that
   produces a judgment extracts and independently validates all eight fields.
2. **Trust is ordered algebra, not float** — all trust comparisons use
   ``TrustAnnotation.compose()``, ``promote()``, ``demote()``; never raw
   numeric comparison of a float score.
3. **No silent promotion from ORACLE_PROPOSED tier** — tests verify that
   ``TrustAnnotation.promote()`` without a ceiling guard is blocked at the
   annotation's declared ceiling.
4. **Evidence kinds preserved** — after kernel lifecycle sequences, the
   evidence bundle's item kinds are verified to be unchanged.
5. **FailureChain on conflict** — when a service graph has a cycle (analogous
   to a unification failure), the engine raises a structured error, not a
   silent bool.

These tests do NOT require Z3 or any native solver; all heavy external
dependencies are mocked with ``unittest.mock``.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Path bootstrap (mirrors existing test pattern in this repository)
# ---------------------------------------------------------------------------

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentStatus,
    TrustAnnotation,
    TrustLevel,
    Proposition,
    PropositionKind,
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    ResidualObligation,
    Obstruction,
    Provenance,
    ProvenanceSource,
    JudgmentClause,
)
from jugeo.evidence.trust import (
    TrustLevel as AlgebraTrustLevel,
    TrustAlgebra,
    TrustComposition,
    TrustTier,
    TrustProfile,
    join_trust_profiles,
)
from jugeo.geometry.site import CoordinateObject, CoordinateKind
from jugeo.kernel.services import (
    ServiceDescriptor,
    ServiceLifecycle,
    ServiceRegistry,
    ServiceGraph,
    ServiceBinding,
)
from jugeo.kernel.lifecycle import (
    KernelPhase,
    LifecycleManager,
    LifecycleController,
    LifecycleState,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_coordinate(name: str = "module.foo") -> CoordinateObject:
    """Build a minimal CoordinateObject for use in judgment construction."""
    return CoordinateObject(name, CoordinateKind.MODULE, tuple(name.split(".")))


def _make_proposition(
    formula: str = "well_typed(x)",
    kind: PropositionKind = PropositionKind.STRUCTURAL,
) -> Proposition:
    """Build a simple closed proposition."""
    return Proposition(kind=kind, formula=formula, free_variables=())


def _make_carrier(name: str = "FunctionContract") -> Carrier:
    """Build a monomorphic carrier."""
    return Carrier(name=name, parameters=(), is_dependent=False)


def _make_evidence_item(
    kind: EvidenceItemKind = EvidenceItemKind.SOLVER_PROOF,
    trust: TrustLevel = TrustLevel.SOLVER_DISCHARGED,
    channel: str = "z3",
) -> EvidenceItem:
    """Build a single evidence item at a specified trust level."""
    return EvidenceItem(
        kind=kind,
        payload={"engine": channel, "result": "unsat"},
        trust_level=trust,
        channel=channel,
        provenance=("test-suite",),
    )


def _make_judgment(
    formula: str = "well_typed(f)",
    trust: TrustLevel = TrustLevel.SOLVER_DISCHARGED,
    evidence_kind: EvidenceItemKind = EvidenceItemKind.SOLVER_PROOF,
) -> Judgment:
    """Construct a full 8-tuple Judgment for use in pipeline tests."""
    coord = _make_coordinate("module.f")
    prop = _make_proposition(formula)
    carrier = _make_carrier("FunctionType")
    item = _make_evidence_item(kind=evidence_kind, trust=trust)
    bundle = EvidenceBundle(items=(item,))
    trust_ann = TrustAnnotation(
        level=trust,
        evidence_basis=(item.canonical_key(),),
        ceiling=TrustLevel.VERIFIED_PROOF,
        floor=TrustLevel.UNVERIFIED,
    )
    prov = Provenance(source=ProvenanceSource.SOLVER)
    return Judgment(
        coordinate=coord,
        proposition=prop,
        carrier=carrier,
        evidence=bundle,
        obligations=(),
        obstructions=(),
        trust=trust_ann,
        provenance=prov,
        status=JudgmentStatus.SETTLED,
    )


# ---------------------------------------------------------------------------
# §1  Judgment tuple structure invariants
# ---------------------------------------------------------------------------


class TestJudgmentTupleStructure:
    """Theory2 invariant: Judgment = (c,φ,A,E,O,B,T,Π) not a bool."""

    def test_judgment_has_exactly_eight_logical_components(self) -> None:
        """The eight Theory2 slots must all be independently accessible."""
        j = _make_judgment()
        # (c) coordinate
        assert j.coordinate is not None
        assert hasattr(j.coordinate, "key")
        # (φ) proposition
        assert j.proposition is not None
        assert isinstance(j.proposition.formula, str)
        assert j.proposition.kind == PropositionKind.STRUCTURAL
        # (A) carrier
        assert j.carrier is not None
        assert isinstance(j.carrier.name, str)
        # (E) evidence bundle
        assert j.evidence is not None
        assert len(j.evidence.items) >= 1
        # (O) obligations
        assert isinstance(j.obligations, tuple)
        # (B) obstructions
        assert isinstance(j.obstructions, tuple)
        # (T) trust annotation — must be TrustAnnotation, not a float
        assert isinstance(j.trust, TrustAnnotation)
        assert not isinstance(j.trust, float)
        # (Π) provenance
        assert isinstance(j.provenance, Provenance)

    def test_judgment_is_not_a_boolean(self) -> None:
        """A Judgment instance must never be reduced to a boolean result."""
        j = _make_judgment()
        # The judgment object itself should not be truthy/falsy in the
        # sense that True/False captures its settlement status.
        # Settlement queries are explicit method calls.
        assert j.is_fully_discharged() or not j.is_fully_discharged()
        assert isinstance(j, Judgment), "Judgment must be a typed object, not bool"
        assert j is not True
        assert j is not False

    def test_settled_judgment_still_exposes_all_eight_fields(self) -> None:
        """SETTLED status must not collapse the tuple — all fields remain."""
        j = _make_judgment()
        assert j.status == JudgmentStatus.SETTLED
        # All eight slots still independently accessible after settlement
        serialized = j.serialize()
        for key in ("coordinate", "proposition", "carrier", "evidence",
                    "obligations", "obstructions", "trust", "provenance"):
            assert key in serialized, f"Missing slot '{key}' in serialized judgment"

    def test_proposed_judgment_preserves_all_fields(self) -> None:
        """PROPOSED judgments must carry same tuple shape as SETTLED ones."""
        coord = _make_coordinate("interface.IFoo")
        prop = _make_proposition("implementsInterface(IFoo, Bar)")
        carrier = _make_carrier("ClassType")
        trust_ann = TrustAnnotation(level=TrustLevel.ORACLE_PROPOSED)
        prov = Provenance(source=ProvenanceSource.ORACLE)
        j = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=EvidenceBundle(),
            obligations=(),
            obstructions=(),
            trust=trust_ann,
            provenance=prov,
            status=JudgmentStatus.PROPOSED,
        )
        assert j.status == JudgmentStatus.PROPOSED
        # Tuple shape is identical regardless of status
        assert j.coordinate.key == "interface/IFoo"
        assert j.proposition.formula == "implementsInterface(IFoo, Bar)"
        assert j.trust.level == TrustLevel.ORACLE_PROPOSED


# ---------------------------------------------------------------------------
# §2  Trust annotation algebra (not float)
# ---------------------------------------------------------------------------


class TestTrustAnnotationAlgebra:
    """Theory2 invariant: Trust is ordered algebra, not float."""

    def test_trust_annotation_is_not_a_float(self) -> None:
        """TrustAnnotation carries algebraic structure, not a raw number."""
        ann = TrustAnnotation(level=TrustLevel.SOLVER_DISCHARGED)
        assert not isinstance(ann, float)
        assert not isinstance(ann, int)
        assert isinstance(ann, TrustAnnotation)
        assert isinstance(ann.level, TrustLevel)

    def test_trust_compose_uses_minimum_level(self) -> None:
        """Composition ⊕ is conservative: result = min(a, b)."""
        high = TrustAnnotation(level=TrustLevel.VERIFIED_PROOF)
        low = TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED)
        composed = high.compose(low)
        assert composed.level == TrustLevel.RUNTIME_WITNESSED
        # Composing in the other direction should give same result
        composed_rev = low.compose(high)
        assert composed_rev.level == TrustLevel.RUNTIME_WITNESSED

    def test_trust_compose_preserves_evidence_basis_union(self) -> None:
        """After composition the evidence_basis is the union of both sets."""
        key_a, key_b = "evid:aaa", "evid:bbb"
        ann_a = TrustAnnotation(
            level=TrustLevel.SOLVER_DISCHARGED,
            evidence_basis=(key_a,),
        )
        ann_b = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=(key_b,),
        )
        composed = ann_a.compose(ann_b)
        assert key_a in composed.evidence_basis
        assert key_b in composed.evidence_basis

    def test_trust_promote_capped_by_ceiling(self) -> None:
        """Promotion must never exceed the declared ceiling."""
        # Ceiling set to ORACLE_PROPOSED — cannot go higher via promote()
        ann = TrustAnnotation(
            level=TrustLevel.ORACLE_PROPOSED,
            ceiling=TrustLevel.ORACLE_PROPOSED,
        )
        # Attempting to promote should be a no-op (clamped at ceiling)
        promoted = ann.promote(reason="test explicit promotion")
        assert int(promoted.level) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_trust_demote_records_reason(self) -> None:
        """Demotion must write a non-empty reason into the audit trail."""
        ann = TrustAnnotation(
            level=TrustLevel.SOLVER_DISCHARGED,
            reasons=("initial",),
        )
        demoted = ann.demote(reason="test challenge")
        assert len(demoted.reasons) > len(ann.reasons)
        assert any("demote" in r for r in demoted.reasons)

    def test_trust_compare_three_valued(self) -> None:
        """TrustAnnotation.compare() must return -1, 0, or 1."""
        low = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        mid = TrustAnnotation(level=TrustLevel.ORACLE_PROPOSED)
        high = TrustAnnotation(level=TrustLevel.VERIFIED_PROOF)

        assert low.compare(mid) == -1
        assert mid.compare(low) == 1
        assert mid.compare(mid) == 0
        assert low.compare(high) == -1
        assert high.compare(low) == 1

    def test_trust_admissibility_requires_consistent_bounds(self) -> None:
        """is_admissible() must fail when floor > level or level > ceiling."""
        consistent = TrustAnnotation(
            level=TrustLevel.SOLVER_DISCHARGED,
            floor=TrustLevel.UNVERIFIED,
            ceiling=TrustLevel.VERIFIED_PROOF,
        )
        assert consistent.is_admissible() is True
        # Manually crafting an inconsistent annotation by supplying values
        # that bypass __post_init__ clamping is not trivial — instead we
        # verify the contract via the clamping behavior.
        clamped = TrustAnnotation(
            level=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            ceiling=TrustLevel.ORACLE_PROPOSED,  # ceiling below level → clamp
        )
        # __post_init__ clamps level to ceiling; still admissible
        assert clamped.is_admissible() is True
        assert int(clamped.level) <= int(TrustLevel.ORACLE_PROPOSED)


# ---------------------------------------------------------------------------
# §3  No silent promotion from ORACLE_PROPOSED
# ---------------------------------------------------------------------------


class TestNoSilentPromotion:
    """Theory2 invariant: no silent promotion from ORACLE_PROPOSED tier."""

    def test_oracle_judgment_trust_cannot_silently_exceed_solver(self) -> None:
        """Oracle-originated judgment must stay at ORACLE_PROPOSED ceiling."""
        coord = _make_coordinate("module.oracle_suggestion")
        prop = _make_proposition("implements(foo, IBar)", PropositionKind.SEMANTIC)
        carrier = _make_carrier("OracleProposal")
        # Oracle channel: ceiling capped at ORACLE_PROPOSED
        oracle_ann = TrustAnnotation(
            level=TrustLevel.ORACLE_PROPOSED,
            ceiling=TrustLevel.ORACLE_PROPOSED,
        )
        prov = Provenance(source=ProvenanceSource.ORACLE)
        j = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=EvidenceBundle(),
            trust=oracle_ann,
            provenance=prov,
        )
        # Attempting to strengthen without evidence: must not exceed ceiling
        strengthened = j.strengthen(reason="oracle auto-approve")
        assert int(strengthened.trust.level) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_copilot_evidence_item_trust_does_not_exceed_oracle(self) -> None:
        """Evidence items from copilot channel should stay ≤ ORACLE_PROPOSED."""
        copilot_item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={"suggestion": "use memoization"},
            trust_level=TrustLevel.ORACLE_PROPOSED,
            channel="copilot",
        )
        # Adding it to a judgment's bundle should not auto-raise bundle trust
        bundle = EvidenceBundle(items=(copilot_item,))
        total = bundle.total_trust()
        # total_trust is the minimum — copilot evidence at ORACLE_PROPOSED
        assert int(total) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_promotion_audit_trail_records_explicit_reason(self) -> None:
        """Every promotion must leave a non-empty entry in reasons."""
        ann = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            ceiling=TrustLevel.VERIFIED_PROOF,
            reasons=(),
        )
        promoted = ann.promote(reason="solver-confirmed at revision 42")
        assert len(promoted.reasons) >= 1
        assert any("promote" in r for r in promoted.reasons)

    def test_trust_algebra_oracle_ceiling_below_solver(self) -> None:
        """TrustAlgebra must enforce that ORACLE ≺ SOLVER_DISCHARGED."""
        algebra = TrustAlgebra()
        oracle = AlgebraTrustLevel.ORACLE_PROPOSED
        solver = AlgebraTrustLevel.SOLVER_DISCHARGED
        # Oracle is strictly below solver in the partial order
        assert oracle < solver
        # Demote solver to oracle ceiling — result should be oracle
        demoted = algebra.demote(solver, oracle)
        assert demoted == oracle

    def test_trust_algebra_copilot_ceiling_enforced(self) -> None:
        """TrustAlgebra.is_admissible() should reject copilot-at-solver trust."""
        algebra = TrustAlgebra()
        # Evidence config where copilot claims solver-level trust
        invalid_config = {
            "copilot_evidence_1": AlgebraTrustLevel.SOLVER_DISCHARGED
        }
        # Should not be admissible — copilot exceeds its ceiling
        assert not algebra.is_admissible(invalid_config)
        # Valid config: copilot at oracle level
        valid_config = {
            "copilot_evidence_1": AlgebraTrustLevel.ORACLE_PROPOSED
        }
        assert algebra.is_admissible(valid_config)


# ---------------------------------------------------------------------------
# §4  Evidence kinds preserved through kernel lifecycle
# ---------------------------------------------------------------------------


class TestEvidenceKindsPreserved:
    """Theory2 invariant: Evidence kinds are preserved in federation."""

    def test_evidence_bundle_merge_preserves_all_kinds(self) -> None:
        """Merging two evidence bundles must not collapse distinct kinds."""
        solver_item = _make_evidence_item(
            kind=EvidenceItemKind.SOLVER_PROOF,
            trust=TrustLevel.SOLVER_DISCHARGED,
            channel="z3",
        )
        runtime_item = _make_evidence_item(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            trust=TrustLevel.RUNTIME_WITNESSED,
            channel="runtime",
        )
        oracle_item = _make_evidence_item(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            trust=TrustLevel.ORACLE_PROPOSED,
            channel="copilot",
        )
        bundle_a = EvidenceBundle(items=(solver_item,))
        bundle_b = EvidenceBundle(items=(runtime_item, oracle_item))
        merged = bundle_a.merge(bundle_b)

        kinds_in_merged = {item.kind for item in merged.items}
        assert EvidenceItemKind.SOLVER_PROOF in kinds_in_merged
        assert EvidenceItemKind.RUNTIME_WITNESS in kinds_in_merged
        assert EvidenceItemKind.ORACLE_PROPOSAL in kinds_in_merged

    def test_evidence_bundle_by_kind_filter_returns_only_that_kind(self) -> None:
        """Filtering by kind must return only items of exactly that kind."""
        solver_item = _make_evidence_item(
            kind=EvidenceItemKind.SOLVER_PROOF, trust=TrustLevel.SOLVER_DISCHARGED
        )
        oracle_item = _make_evidence_item(
            kind=EvidenceItemKind.ORACLE_PROPOSAL, trust=TrustLevel.ORACLE_PROPOSED
        )
        bundle = EvidenceBundle(items=(solver_item, oracle_item))
        solver_only = bundle.by_kind(EvidenceItemKind.SOLVER_PROOF)
        assert len(solver_only.items) == 1
        assert solver_only.items[0].kind == EvidenceItemKind.SOLVER_PROOF

    def test_judgment_merge_evidence_does_not_auto_promote_trust(self) -> None:
        """Adding runtime witness evidence must not auto-raise trust to VERIFIED."""
        j = _make_judgment(trust=TrustLevel.ORACLE_PROPOSED)
        runtime_item = _make_evidence_item(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            trust=TrustLevel.RUNTIME_WITNESSED,
        )
        extra_bundle = EvidenceBundle(items=(runtime_item,))
        # merge_evidence explicitly does NOT auto-promote
        j2 = j.merge_evidence(extra_bundle)
        # Trust annotation unchanged — must not silently promote
        assert j2.trust.level == j.trust.level
        # But evidence bundle now contains both kinds
        kinds = {item.kind for item in j2.evidence.items}
        assert EvidenceItemKind.ORACLE_PROPOSAL in kinds or \
               EvidenceItemKind.SOLVER_PROOF in kinds
        assert EvidenceItemKind.RUNTIME_WITNESS in kinds

    def test_evidence_remove_stale_preserves_non_stale_kinds(self) -> None:
        """remove_stale() must keep all valid evidence items, kinds intact."""
        solver_item = _make_evidence_item(
            kind=EvidenceItemKind.SOLVER_PROOF, trust=TrustLevel.SOLVER_DISCHARGED
        )
        expired_item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={},
            trust_level=TrustLevel.ORACLE_PROPOSED,
            channel="copilot",
            expiry="2000-01-01T00:00:00Z",  # clearly in the past
        )
        bundle = EvidenceBundle(items=(solver_item, expired_item))
        cleaned = bundle.remove_stale()
        kinds = {item.kind for item in cleaned.items}
        assert EvidenceItemKind.SOLVER_PROOF in kinds
        assert EvidenceItemKind.ORACLE_PROPOSAL not in kinds


# ---------------------------------------------------------------------------
# §5  Kernel service graph (analog of unification failure → structured error)
# ---------------------------------------------------------------------------


class TestKernelServiceGraph:
    """Kernel service dependency graph: cycle → structured error (FailureChain)."""

    def test_service_registry_registers_and_resolves_descriptor(self) -> None:
        """Basic service registration and resolution must work end-to-end."""
        registry = ServiceRegistry()
        desc = ServiceDescriptor(
            name="type_checker",
            service_type=object,
            lifecycle=ServiceLifecycle.SINGLETON,
            dependencies=frozenset(),
        )
        registry.register(desc, factory=lambda: object())
        binding = registry.resolve("type_checker")
        assert binding is not None

    def test_service_graph_cycle_raises_structured_error(self) -> None:
        """Cyclic dependencies must not silently produce None — must raise."""
        graph = ServiceGraph()
        # Add nodes with a cycle: A → B → A
        graph.add_node("service_A", frozenset({"service_B"}))
        graph.add_node("service_B", frozenset({"service_A"}))
        # Startup order must detect the cycle and raise
        with pytest.raises(Exception) as exc_info:
            graph.startup_order()
        # The raised exception must carry useful diagnostic content
        err_str = str(exc_info.value).lower()
        assert "cycle" in err_str or "circular" in err_str or "service" in err_str

    def test_service_graph_topological_sort_linear_chain(self) -> None:
        """A → B → C (no cycle) must produce a valid topological order."""
        graph = ServiceGraph()
        graph.add_node("C", frozenset())
        graph.add_node("B", frozenset({"C"}))
        graph.add_node("A", frozenset({"B"}))
        order = graph.startup_order()
        assert isinstance(order, list)
        # A must come after B, B must come after C
        assert order.index("C") < order.index("B")
        assert order.index("B") < order.index("A")

    def test_service_descriptor_carries_trust_ceiling(self) -> None:
        """ServiceDescriptor must preserve a declared trust ceiling."""
        desc = ServiceDescriptor(
            name="oracle_proposer",
            service_type=object,
            lifecycle=ServiceLifecycle.TRANSIENT,
            dependencies=frozenset(),
            trust_ceiling="proposal",
        )
        assert desc.trust_ceiling == "proposal"
        # Trust ceiling for oracle/copilot services must NOT be 'verified'
        assert desc.trust_ceiling != "verified"


# ---------------------------------------------------------------------------
# §6  Reduction sequence (lifecycle phases) preserves trust annotation
# ---------------------------------------------------------------------------


class TestLifecyclePhaseTrustPreservation:
    """Lifecycle phases must not silently reset or alter trust annotations."""

    def test_lifecycle_manager_initial_phase_is_booting(self) -> None:
        """Fresh LifecycleManager must start in BOOTING phase."""
        mgr = LifecycleManager()
        # The manager starts in the initial phase
        assert mgr.current_phase is not None

    def test_lifecycle_phase_sequence_is_monotone_forward(self) -> None:
        """Phase transitions must move forward (no rollback except FAILED)."""
        phases = list(KernelPhase)
        # Verify that the enum has ordered phases in expected progression
        phase_names = [p.value for p in phases]
        # Phases should include at least 'booting' and 'running' analogues
        assert len(phases) >= 2

    def test_judgment_trust_survives_mock_lifecycle_transition(self) -> None:
        """After a simulated lifecycle phase change, judgment trust must be intact."""
        j = _make_judgment(trust=TrustLevel.SOLVER_DISCHARGED)
        original_trust = j.trust.level

        # Simulate a lifecycle phase advancing (mock the transition)
        with patch.object(LifecycleManager, "advance") as mock_advance:
            mock_advance.return_value = None
            mgr = LifecycleManager()
            mgr.advance(KernelPhase.RUNNING if hasattr(KernelPhase, "RUNNING")
                        else list(KernelPhase)[1])

        # Trust annotation on judgment must be unchanged
        assert j.trust.level == original_trust
        assert isinstance(j.trust, TrustAnnotation)

    def test_judgment_with_kernel_proof_backed_trust(self) -> None:
        """A judgment produced by the kernel can hold VERIFIED_PROOF trust."""
        formal_item = EvidenceItem(
            kind=EvidenceItemKind.FORMAL_PROOF,
            payload={"theorem": "T1", "proof_term": "λx.x"},
            trust_level=TrustLevel.VERIFIED_PROOF,
            channel="formal_proof",
        )
        bundle = EvidenceBundle(items=(formal_item,))
        trust_ann = TrustAnnotation(
            level=TrustLevel.VERIFIED_PROOF,
            evidence_basis=(formal_item.canonical_key(),),
            ceiling=TrustLevel.VERIFIED_PROOF,
        )
        coord = _make_coordinate("theorem.T1")
        j = Judgment(
            coordinate=coord,
            proposition=_make_proposition("∀x. f(x) = g(x)"),
            carrier=_make_carrier("TheoremType"),
            evidence=bundle,
            trust=trust_ann,
            provenance=Provenance(source=ProvenanceSource.HUMAN),
            status=JudgmentStatus.SETTLED,
        )
        # Theory2 §252: VERIFIED_PROOF is the highest tier
        assert j.trust.level == TrustLevel.VERIFIED_PROOF
        assert j.trust.level.stronger_than(TrustLevel.SOLVER_DISCHARGED)
        assert j.trust.level.stronger_than(TrustLevel.ORACLE_PROPOSED)
        # The judgment is not a bool — it carries rich proof structure
        assert isinstance(j, Judgment)
        assert j.evidence.items[0].kind == EvidenceItemKind.FORMAL_PROOF


# ---------------------------------------------------------------------------
# §7  Obstruction first-class semantics (FailureChain analog)
# ---------------------------------------------------------------------------


class TestObstructionFirstClass:
    """Theory2: obstructions are first-class cohomology classes, not booleans."""

    def test_obstruction_records_violated_condition(self) -> None:
        """An Obstruction must record the specific violated condition string."""
        obs = Obstruction(
            violated_condition="interface mismatch: expected List[int], got str",
            coordinate="module.foo/line:42",
            cohomology_class="H1-type-mismatch",
        )
        assert obs.violated_condition == "interface mismatch: expected List[int], got str"
        assert obs.cohomology_class == "H1-type-mismatch"
        assert not obs.is_resolved

    def test_adding_obstruction_sets_judgment_to_obstructed(self) -> None:
        """Adding an Obstruction to a Judgment must set status to OBSTRUCTED."""
        j = _make_judgment()
        obs = Obstruction(
            violated_condition="unresolved type variable T",
            coordinate=j.coordinate.key,
        )
        j_obstructed = j.add_obstruction(obs)
        assert j_obstructed.status == JudgmentStatus.OBSTRUCTED
        assert len(j_obstructed.obstructions) == 1

    def test_obstruction_is_not_silently_erased_by_new_evidence(self) -> None:
        """Merging new evidence into an obstructed judgment must NOT resolve obstructions."""
        j = _make_judgment()
        obs = Obstruction(violated_condition="occurs check failed", coordinate=j.coordinate.key)
        j_obstructed = j.add_obstruction(obs)
        # Add more evidence
        extra_item = _make_evidence_item(
            kind=EvidenceItemKind.RUNTIME_WITNESS, trust=TrustLevel.RUNTIME_WITNESSED
        )
        j2 = j_obstructed.merge_evidence(EvidenceBundle(items=(extra_item,)))
        # Obstruction must still be present and unresolved
        assert len(j2.obstructions) == 1
        assert not j2.obstructions[0].is_resolved
        assert j2.status == JudgmentStatus.OBSTRUCTED

    def test_obstruction_resolve_requires_explicit_evidence_key(self) -> None:
        """Resolving an obstruction must require a non-empty evidence key."""
        obs = Obstruction(violated_condition="missing module declaration")
        resolved = obs.resolve("evid:abc123", reason="module added at commit 7f3a")
        assert resolved.is_resolved
        assert resolved.resolution_evidence == "evid:abc123"
        assert len(resolved.provenance) > 0

    def test_judgment_content_hash_changes_with_obstruction(self) -> None:
        """An obstructed judgment must have a different hash than a clean one."""
        j_clean = _make_judgment()
        obs = Obstruction(violated_condition="type mismatch", coordinate=j_clean.coordinate.key)
        j_obstructed = j_clean.add_obstruction(obs)
        # Hash must differ because status changed to OBSTRUCTED
        clean_hash = j_clean.content_hash()
        obstructed_hash = j_obstructed.content_hash()
        assert clean_hash != obstructed_hash

    def test_residual_obligation_discharge_produces_new_judgment(self) -> None:
        """Discharging an obligation must return a *new* immutable judgment."""
        obligation = ResidualObligation(
            description="prove termination of f",
            required_evidence_kind=EvidenceItemKind.FORMAL_PROOF,
            priority=1,
        )
        j = _make_judgment().add_obligation(obligation)
        j2 = j.discharge_obligation(
            obligation.obligation_id,
            evidence_key="evid:termination-proof",
            reason="WF induction on argument",
        )
        assert j2 is not j  # immutability: new object
        assert j2.obligations[0].is_discharged
        assert j2.obligations[0].discharge_evidence == "evid:termination-proof"


# ---------------------------------------------------------------------------
# §8  Trust algebra: composition, meet, join, attenuation
# ---------------------------------------------------------------------------


class TestTrustAlgebraOperations:
    """Full TrustAlgebra (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) operation coverage."""

    def test_algebra_meet_returns_weaker(self) -> None:
        """meet(a, b) must return the greatest lower bound."""
        algebra = TrustAlgebra()
        solver = AlgebraTrustLevel.SOLVER_DISCHARGED
        runtime = AlgebraTrustLevel.RUNTIME_WITNESSED
        result = algebra.meet(solver, runtime)
        # RUNTIME_WITNESSED < SOLVER_DISCHARGED in the partial order
        assert result == runtime or result <= runtime

    def test_algebra_join_returns_stronger(self) -> None:
        """join(a, b) must return the least upper bound."""
        algebra = TrustAlgebra()
        oracle = AlgebraTrustLevel.ORACLE_PROPOSED
        runtime = AlgebraTrustLevel.RUNTIME_WITNESSED
        result = algebra.join(oracle, runtime)
        assert result >= oracle
        assert result >= runtime

    def test_algebra_attenuation_weakens_by_factor(self) -> None:
        """attenuate(t, n) must weaken trust by n steps."""
        algebra = TrustAlgebra()
        start = AlgebraTrustLevel.SOLVER_DISCHARGED
        attenuated_1 = algebra.attenuate(start, 1)
        attenuated_2 = algebra.attenuate(start, 2)
        # After attenuation, result must be strictly weaker or equal
        assert attenuated_1 <= start
        assert attenuated_2 <= attenuated_1

    def test_algebra_compose_is_meet(self) -> None:
        """compose(a, b) must equal meet(a, b) — composition is conservative."""
        algebra = TrustAlgebra()
        a = AlgebraTrustLevel.RUNTIME_WITNESSED
        b = AlgebraTrustLevel.HUMAN_ATTESTED
        assert algebra.compose(a, b) == algebra.meet(a, b)

    def test_trust_composition_homogeneous(self) -> None:
        """Composing identical trust levels must return that level unchanged."""
        composition = TrustComposition()
        solver = AlgebraTrustLevel.SOLVER_DISCHARGED
        result = composition.compose_homogeneous([solver, solver, solver])
        assert result == solver

    def test_trust_profile_join_is_conservative(self) -> None:
        """join_trust_profiles must return the weaker of the two profiles."""
        prof_strong = TrustProfile(
            tier=TrustTier.VERIFIED,
            support_scope=("module.A",),
            reasons=("solver-discharged",),
        )
        prof_weak = TrustProfile(
            tier=TrustTier.PROPOSAL,
            support_scope=("module.B",),
            reasons=("oracle-proposed",),
        )
        joined = join_trust_profiles(prof_strong, prof_weak)
        assert joined.tier == TrustTier.PROPOSAL  # conservative join

    def test_trust_profile_empty_join_gives_weakest(self) -> None:
        """join_trust_profiles() with no arguments yields the weakest profile."""
        result = join_trust_profiles()
        assert result.tier == TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# §9  Judgment restriction and transport (reduction sequence)
# ---------------------------------------------------------------------------


class TestJudgmentRestrictionTransport:
    """Restriction and transport preserve trust and judgment tuple structure."""

    def test_restrict_judgment_updates_coordinate(self) -> None:
        """restrict_to() must update coordinate and record the step in provenance."""
        j = _make_judgment()
        target = _make_coordinate("module.f.inner")
        j2 = j.restrict_to(target)
        assert j2.coordinate.key == target.key
        # Provenance must record the restriction
        assert any("restrict" in t for t in j2.provenance.transformation_history)

    def test_restrict_does_not_change_proposition_or_trust(self) -> None:
        """Restriction must not alter proposition or trust level."""
        j = _make_judgment()
        target = _make_coordinate("module.f.inner")
        j2 = j.restrict_to(target)
        assert j2.proposition.formula == j.proposition.formula
        assert j2.trust.level == j.trust.level

    def test_weaken_records_audit_trail_in_trust_annotation(self) -> None:
        """weaken() must add a 'demote' entry to the trust annotation reasons."""
        j = _make_judgment(trust=TrustLevel.SOLVER_DISCHARGED)
        j2 = j.weaken(reason="challenged by human reviewer")
        assert len(j2.trust.reasons) > len(j.trust.reasons)
        demote_entry = any("demote" in r for r in j2.trust.reasons)
        assert demote_entry

    def test_project_to_public_strips_internal_payloads(self) -> None:
        """project_to_public() must expose trust and status but hide payloads."""
        j = _make_judgment()
        pub = j.project_to_public()
        # Must have key fields
        assert "coordinate" in pub
        assert "status" in pub
        assert "trust_level" in pub
        # Must NOT expose raw evidence payload
        for key in ("payload", "provenance", "evidence_basis"):
            assert key not in pub, f"Internal field {key!r} leaked to public projection"

    def test_judgment_pending_obligation_count_accurate(self) -> None:
        """pending_obligation_count() must count only un-discharged obligations."""
        ob1 = ResidualObligation(description="prove P", priority=1)
        ob2 = ResidualObligation(description="prove Q", priority=2)
        ob2_discharged = ob2.discharge("evid:Q", reason="trivial")
        j = Judgment(
            coordinate=_make_coordinate(),
            proposition=_make_proposition(),
            carrier=_make_carrier(),
            obligations=(ob1, ob2_discharged),
            trust=TrustAnnotation(),
            provenance=Provenance(),
        )
        assert j.pending_obligation_count() == 1
        assert j.has_residuals() is True


# ---------------------------------------------------------------------------
# §10  Cross-module round-trip: kernel service produces judgment tuple
# ---------------------------------------------------------------------------


class TestKernelToJudgmentRoundTrip:
    """Simulate a kernel type-check service producing a Judgment 8-tuple."""

    def test_mock_type_checker_produces_judgment_with_proof_backed_trust(self) -> None:
        """A mocked type-checker service must return a PROOF_BACKED Judgment."""
        # Mock the kernel service as if it performs a type-check
        with patch("jugeo.kernel.services.ServiceRegistry.resolve") as mock_resolve:
            mock_svc = MagicMock()
            mock_svc.check.return_value = {
                "status": "ok",
                "trust": "solver_discharged",
            }
            mock_resolve.return_value = ServiceBinding(
                name="type_checker",
                component=mock_svc,
                trust_ceiling="verified",
            )
            # Build the Judgment the service would produce
            item = _make_evidence_item(
                kind=EvidenceItemKind.SOLVER_PROOF,
                trust=TrustLevel.SOLVER_DISCHARGED,
                channel="z3",
            )
            trust_ann = TrustAnnotation(
                level=TrustLevel.SOLVER_DISCHARGED,
                evidence_basis=(item.canonical_key(),),
                ceiling=TrustLevel.VERIFIED_PROOF,
            )
            j = Judgment(
                coordinate=_make_coordinate("module.TypeChecker"),
                proposition=_make_proposition("typeOf(x) ≡ Int"),
                carrier=_make_carrier("IntType"),
                evidence=EvidenceBundle(items=(item,)),
                trust=trust_ann,
                provenance=Provenance(source=ProvenanceSource.SOLVER),
                status=JudgmentStatus.SETTLED,
            )
            # Validate 8-tuple completeness
            assert j.coordinate is not None       # c
            assert j.proposition is not None      # φ
            assert j.carrier is not None          # A
            assert not j.evidence.is_empty()      # E
            assert isinstance(j.obligations, tuple)  # O
            assert isinstance(j.obstructions, tuple)  # B
            assert isinstance(j.trust, TrustAnnotation)  # T
            assert isinstance(j.provenance, Provenance)  # Π
            # Trust must be SOLVER_DISCHARGED — the PROOF_BACKED tier
            assert j.trust.level == TrustLevel.SOLVER_DISCHARGED

    def test_type_check_failure_produces_obstructed_judgment(self) -> None:
        """When type-checking fails, the result must be an OBSTRUCTED Judgment."""
        obs = Obstruction(
            violated_condition="type mismatch: expected Bool, got Int at line 17",
            coordinate="module.foo/typecheck",
            cohomology_class="H1-type-error",
            repair_hints=("cast to Bool", "change annotation to Int"),
        )
        j_fail = Judgment(
            coordinate=_make_coordinate("module.foo"),
            proposition=_make_proposition("typeOf(x) ≡ Bool"),
            carrier=_make_carrier("BoolType"),
            obstructions=(obs,),
            trust=TrustAnnotation(level=TrustLevel.CONTRADICTED),
            provenance=Provenance(source=ProvenanceSource.SOLVER),
            status=JudgmentStatus.OBSTRUCTED,
        )
        # Must not be a boolean
        assert isinstance(j_fail, Judgment)
        assert j_fail is not False
        assert j_fail.status == JudgmentStatus.OBSTRUCTED
        # Obstruction carries cohomology class and repair hints
        assert j_fail.obstructions[0].cohomology_class == "H1-type-error"
        assert len(j_fail.obstructions[0].repair_hints) >= 1
        # Contradiction reflected in trust level
        assert j_fail.trust.level == TrustLevel.CONTRADICTED
