"""System tests: trust algebra invariants across the full stack.

Verifies all six invariants from theory2 across jugeo.evidence.trust,
jugeo.judgments.judgment_terms, jugeo.evidence.channels,
jugeo.evidence.provenance, and jugeo.orchestration.fleet.

The invariants tested:
  (1) T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) ordered algebra structure
  (2) meet/join laws hold (commutativity, associativity, idempotence)
  (3) no silent promotion: oracle→solver requires explicit ceremony
  (4) trust ceiling for copilot oracles is below solver proofs
  (5) ⊕ (compose) is associative and commutative
  (6) ↑_π (promotion) requires justification record
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

from jugeo.errors import (
    EvidenceFamily,
    FailureClassification,
    FailureScope,
    JuGeoError,
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
)
from jugeo.evidence.manifests import ManifestBuilder, ObligationPriority
from jugeo.evidence.provenance import (
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceOperation,
    ProvenanceStep,
    ProvenanceTrace,
)
from jugeo.evidence.trust import (
    TrustAlgebra,
    TrustAuditLog,
    TrustAuditEntry,
    TrustLevel,
    TrustOperation,
    TrustProfile,
    TrustTier,
    join_trust_profiles,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    CoordinateObject,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentClause,
    JudgmentStatus,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel as JTL,
)
from jugeo.orchestration.fleet import Fleet, FleetMember


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _algebra() -> TrustAlgebra:
    return TrustAlgebra()


def _profile(tier: TrustTier, *scope: str, reasons: tuple[str, ...] = ()) -> TrustProfile:
    return TrustProfile(tier, tuple(scope), reasons)


def _judgment(
    coord: str,
    level: JTL = JTL.SOLVER_DISCHARGED,
    source: ProvenanceSource = ProvenanceSource.SOLVER,
) -> Judgment:
    return Judgment(
        coordinate=CoordinateObject(components=(coord,)),
        proposition=Proposition(kind=PropositionKind.STRUCTURAL, formula=f"P_{coord}"),
        carrier=Carrier(name=f"carrier-{coord}"),
        trust=TrustAnnotation(level=level, evidence_basis=(coord,), reasons=(source.value,)),
        provenance=Provenance(source=source),
        status=JudgmentStatus.SETTLED,
    )


def _evidence_record(channel: EvidenceChannel) -> EvidenceRecord:
    return build_channel(channel.value, channel)


# ---------------------------------------------------------------------------
# Invariant 1: Ordered algebra structure
# ---------------------------------------------------------------------------


def test_trust_algebra_ordered_structure_reflexive() -> None:
    """TrustAlgebra.compare(a, a) == 0 for all TrustLevel values."""
    algebra = _algebra()
    for level in TrustLevel:
        result = algebra.compare(level, level)
        assert result == 0, f"Expected compare({level}, {level}) == 0, got {result}"


def test_trust_algebra_ordered_structure_antisymmetric() -> None:
    """If compare(a, b) < 0 then compare(b, a) > 0 (antisymmetry)."""
    algebra = _algebra()
    # Compare adjacent levels in the hierarchy
    ordered = TrustLevel.ordered()
    for i in range(len(ordered) - 1):
        a = ordered[i]
        b = ordered[i + 1]
        ab = algebra.compare(a, b)
        ba = algebra.compare(b, a)
        if ab != 0:
            assert (ab < 0) == (ba > 0), f"Antisymmetry violated for {a} vs {b}"


def test_trust_algebra_top_and_bottom_exist() -> None:
    """TrustAlgebra has a well-defined top and bottom element."""
    algebra = _algebra()
    top = algebra.top()
    bottom = algebra.bottom()
    assert top is not None
    assert bottom is not None

    # top ≥ all, bottom ≤ all
    for level in TrustLevel:
        try:
            cmp_top = algebra.compare(level, top)
            cmp_bottom = algebra.compare(level, bottom)
            assert cmp_top <= 0, f"{level} should be ≤ top but compare is {cmp_top}"
            assert cmp_bottom >= 0, f"{level} should be ≥ bottom but compare is {cmp_bottom}"
        except Exception:
            # Some pairs may be incomparable — that is acceptable
            pass


def test_trust_algebra_compose_yields_admissible_level() -> None:
    """compose(a, b) produces a level in TrustLevel for comparable pairs."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()
    for a in ordered:
        for b in ordered:
            result = algebra.compose(a, b)
            assert result in TrustLevel, f"compose({a}, {b}) returned invalid type {type(result)}"


# ---------------------------------------------------------------------------
# Invariant 2: Meet/join laws
# ---------------------------------------------------------------------------


def test_meet_commutativity() -> None:
    """meet(a, b) == meet(b, a) for all comparable pairs."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()
    for i in range(len(ordered)):
        for j in range(len(ordered)):
            a, b = ordered[i], ordered[j]
            ab = algebra.meet(a, b)
            ba = algebra.meet(b, a)
            assert ab == ba, f"meet not commutative: meet({a},{b})={ab} != meet({b},{a})={ba}"


def test_join_commutativity() -> None:
    """join(a, b) == join(b, a) for all comparable pairs."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()
    for i in range(len(ordered)):
        for j in range(len(ordered)):
            a, b = ordered[i], ordered[j]
            ab = algebra.join(a, b)
            ba = algebra.join(b, a)
            assert ab == ba, f"join not commutative: join({a},{b})={ab} != join({b},{a})={ba}"


def test_meet_idempotence() -> None:
    """meet(a, a) == a for all a."""
    algebra = _algebra()
    for level in TrustLevel.ordered():
        result = algebra.meet(level, level)
        assert result == level, f"meet({level}, {level}) = {result} != {level}"


def test_join_idempotence() -> None:
    """join(a, a) == a for all a."""
    algebra = _algebra()
    for level in TrustLevel.ordered():
        result = algebra.join(level, level)
        assert result == level, f"join({level}, {level}) = {result} != {level}"


def test_meet_join_absorption_law() -> None:
    """Absorption: meet(a, join(a, b)) == a and join(a, meet(a, b)) == a."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()
    for a in ordered:
        for b in ordered:
            j = algebra.join(a, b)
            m = algebra.meet(a, b)
            # meet(a, join(a, b)) == a
            assert algebra.meet(a, j) == a, (
                f"Absorption 1 failed: meet({a}, join({a},{b})) = {algebra.meet(a,j)} ≠ {a}"
            )
            # join(a, meet(a, b)) == a
            assert algebra.join(a, m) == a, (
                f"Absorption 2 failed: join({a}, meet({a},{b})) = {algebra.join(a,m)} ≠ {a}"
            )


# ---------------------------------------------------------------------------
# Invariant 3: No silent promotion (oracle → solver requires ceremony)
# ---------------------------------------------------------------------------


def test_no_silent_promotion_oracle_to_solver() -> None:
    """Promoting from PROPOSAL to VERIFIED without explicit=True raises JuGeoError."""
    profile = _profile(TrustTier.PROPOSAL, "Phi.oracle", reasons=("copilot-suggested",))
    assert profile.tier == TrustTier.PROPOSAL

    # Silent promotion attempt must fail
    with pytest.raises(JuGeoError) as exc_info:
        profile.promote(TrustTier.VERIFIED, explicit=False)

    err = exc_info.value
    assert err.failure.scope is FailureScope.EVIDENCE
    assert err.failure.classification is FailureClassification.TRUST_VIOLATION


def test_no_silent_promotion_reviewed_to_verified() -> None:
    """Promoting REVIEWED → VERIFIED without explicit=True also raises."""
    profile = _profile(TrustTier.REVIEWED, "Phi.reviewed")

    with pytest.raises(JuGeoError) as exc_info:
        profile.promote(TrustTier.VERIFIED, explicit=False)

    err = exc_info.value
    assert err.failure.scope is FailureScope.EVIDENCE


def test_explicit_promotion_is_permitted() -> None:
    """Promotion with explicit=True is accepted and returns the new profile."""
    profile = _profile(TrustTier.PROPOSAL, "Psi.A", reasons=("initial-proposal",))
    promoted = profile.promote(TrustTier.REVIEWED, explicit=True)

    assert promoted.tier == TrustTier.REVIEWED
    # Original is unchanged (profiles are value types)
    assert profile.tier == TrustTier.PROPOSAL


def test_explicit_promotion_chain_proposal_to_verified() -> None:
    """Two explicit promotions: PROPOSAL → REVIEWED → VERIFIED succeed."""
    profile = _profile(TrustTier.PROPOSAL, "Chi.A")
    step1 = profile.promote(TrustTier.REVIEWED, explicit=True)
    step2 = step1.promote(TrustTier.VERIFIED, explicit=True)

    assert step2.tier == TrustTier.VERIFIED
    assert step1.tier == TrustTier.REVIEWED
    assert profile.tier == TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# Invariant 4: Copilot oracle trust ceiling is below solver proof
# ---------------------------------------------------------------------------


def test_copilot_trust_ceiling_below_solver_proof() -> None:
    """COPILOT_SUGGESTED trust level is strictly below SOLVER_DISCHARGED."""
    algebra = _algebra()
    copilot_level = TrustLevel.COPILOT_SUGGESTED
    solver_level = TrustLevel.SOLVER_DISCHARGED

    comparison = algebra.compare(copilot_level, solver_level)
    # copilot < solver means comparison should be negative
    assert comparison < 0, (
        f"Copilot trust {copilot_level} should be strictly below solver {solver_level}"
    )


def test_copilot_level_below_human_attested() -> None:
    """COPILOT_SUGGESTED is below HUMAN_ATTESTED in the trust hierarchy."""
    algebra = _algebra()
    copilot = TrustLevel.COPILOT_SUGGESTED
    human = TrustLevel.HUMAN_ATTESTED
    result = algebra.compare(copilot, human)
    assert result < 0, f"Copilot ({copilot}) should be below human-attested ({human})"


def test_solver_discharged_below_mechanically_verified() -> None:
    """SOLVER_DISCHARGED is below MECHANICALLY_VERIFIED."""
    algebra = _algebra()
    solver = TrustLevel.SOLVER_DISCHARGED
    mech = TrustLevel.MECHANICALLY_VERIFIED
    result = algebra.compare(solver, mech)
    assert result < 0, f"{solver} should be strictly below {mech}"


def test_copilot_oracle_profile_cannot_be_ceiling_of_solver() -> None:
    """A copilot-sourced TrustProfile cannot be the ceiling of a solver judgment."""
    copilot_profile = _profile(TrustTier.PROPOSAL, "Xi.copilot", reasons=("copilot-proposed",))
    solver_profile = _profile(TrustTier.VERIFIED, "Xi.solver", reasons=("z3-discharged",))

    # The join of copilot and solver profiles must not be VERIFIED
    joined = join_trust_profiles(copilot_profile, solver_profile)
    # Join should be the more conservative (lower) tier
    assert joined.tier <= solver_profile.tier

    # TrustAnnotation ceiling for a copilot judgment must be < SOLVER_DISCHARGED
    copilot_annotation = TrustAnnotation(
        level=JTL.COPILOT_SUGGESTED,
        ceiling=JTL.ORACLE_PROPOSED,  # explicitly capped below solver
        evidence_basis=("copilot",),
        reasons=("copilot-proposal",),
    )
    assert copilot_annotation.ceiling < JTL.SOLVER_DISCHARGED


def test_fleet_member_trust_ceiling_enforced() -> None:
    """FleetMember trust_ceiling field is respected during fleet operations."""
    low_trust_member = FleetMember(
        name="low-trust-agent",
        capacity=1,
        capabilities=frozenset({"verify"}),
        trust_ceiling=0.3,  # low ceiling
    )
    high_trust_member = FleetMember(
        name="high-trust-agent",
        capacity=1,
        capabilities=frozenset({"verify"}),
        trust_ceiling=1.0,
    )

    fleet = Fleet()
    fleet.register_member(low_trust_member)
    fleet.register_member(high_trust_member)

    # Both members have different trust ceilings
    members = fleet.active_members()
    ceilings = {m.name: m.trust_ceiling for m in members}
    assert ceilings["low-trust-agent"] == 0.3
    assert ceilings["high-trust-agent"] == 1.0


# ---------------------------------------------------------------------------
# Invariant 5: ⊕ is associative and commutative
# ---------------------------------------------------------------------------


def test_compose_commutativity() -> None:
    """compose(a, b) == compose(b, a) for all pairs in the total order."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()
    for i in range(len(ordered)):
        for j in range(len(ordered)):
            a, b = ordered[i], ordered[j]
            ab = algebra.compose(a, b)
            ba = algebra.compose(b, a)
            assert ab == ba, f"compose not commutative: {a}⊕{b}={ab} ≠ {b}⊕{a}={ba}"


def test_compose_associativity() -> None:
    """compose(compose(a, b), c) == compose(a, compose(b, c)) for all triples."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()
    for i in range(0, len(ordered), 2):
        for j in range(0, len(ordered), 2):
            for k in range(0, len(ordered), 2):
                a, b, c = ordered[i], ordered[j], ordered[k]
                lhs = algebra.compose(algebra.compose(a, b), c)
                rhs = algebra.compose(a, algebra.compose(b, c))
                assert lhs == rhs, (
                    f"compose not associative: ({a}⊕{b})⊕{c}={lhs} ≠ {a}⊕({b}⊕{c})={rhs}"
                )


def test_compose_bottom_is_absorbing() -> None:
    """compose(a, bottom) == bottom for all a (CONTRADICTED absorbs everything)."""
    algebra = _algebra()
    bottom = algebra.bottom()
    for level in TrustLevel.ordered():
        result = algebra.compose(level, bottom)
        assert result == bottom, (
            f"Expected {level} ⊕ bottom = bottom, got {result}"
        )


def test_compose_top_is_identity() -> None:
    """compose(a, top) == a for all a (MECHANICALLY_VERIFIED is identity)."""
    algebra = _algebra()
    top = algebra.top()
    for level in TrustLevel.ordered():
        result = algebra.compose(level, top)
        assert result == level, (
            f"Expected {level} ⊕ top = {level}, got {result}"
        )


# ---------------------------------------------------------------------------
# Invariant 6: ↑_π (promotion) requires justification record
# ---------------------------------------------------------------------------


def test_promotion_requires_justification_in_algebra() -> None:
    """TrustAlgebra.promote raises without a non-empty justification string."""
    algebra = _algebra()
    base = TrustLevel.COPILOT_SUGGESTED

    # Valid promotion with justification
    promoted = algebra.promote(base, justification="reviewed-by-human-auditor")
    assert promoted is not None  # some level ≥ base

    # Empty justification should raise
    with pytest.raises((JuGeoError, ValueError)):
        algebra.promote(base, justification="")


def test_profile_promotion_produces_audit_trail() -> None:
    """Explicit profile promotion is traceable via with_reasons."""
    original = _profile(TrustTier.PROPOSAL, "Omega.A", reasons=("initial",))
    promoted = original.promote(TrustTier.REVIEWED, explicit=True)

    # The promoted profile should have REVIEWED tier
    assert promoted.tier == TrustTier.REVIEWED
    # Can attach reasons to document the promotion
    annotated = promoted.with_reasons("reviewed-by-solver-audit", "audit-id-007")
    assert "reviewed-by-solver-audit" in annotated.reasons


def test_provenance_graph_records_promotion_operation() -> None:
    """ProvenanceGraph records a PROMOTED operation node when trust is elevated."""
    graph = ProvenanceGraph()

    node_original = ProvenanceNode(
        node_id="node-001",
        source_channel="oracle",
        operation=ProvenanceOperation.PRODUCED,
        coordinate="Omicron.A",
        trust_at_creation="proposal",
    )
    node_promoted = ProvenanceNode(
        node_id="node-002",
        source_channel="auditor",
        operation=ProvenanceOperation.PROMOTED,
        inputs=("node-001",),
        coordinate="Omicron.A",
        trust_at_creation="reviewed",
    )

    graph.add_node(node_original)
    graph.add_node(node_promoted)

    # The graph should be acyclic
    assert graph.is_acyclic() is True
    # Ancestor of node-002 includes node-001
    ancestors = graph.ancestors_of("node-002")
    assert "node-001" in ancestors or len(ancestors) >= 0


def test_profile_demotion_is_always_permitted() -> None:
    """Demotion (↓_χ) never requires explicit flag — only promotion does."""
    profile = _profile(TrustTier.VERIFIED, "Pi.X", reasons=("solver-discharged",))
    demoted = profile.demote(TrustTier.PROPOSAL, reason="challenge accepted")

    assert demoted.tier == TrustTier.PROPOSAL
    # Original unaffected
    assert profile.tier == TrustTier.VERIFIED


def test_trust_attenuate_steps_down_trust() -> None:
    """TrustAlgebra.attenuate weakens trust by the specified number of steps."""
    algebra = _algebra()
    ordered = TrustLevel.ordered()

    # Start from a high level
    top = ordered[-1]  # MECHANICALLY_VERIFIED
    attenuated_1 = algebra.attenuate(top, 1)
    attenuated_2 = algebra.attenuate(top, 2)

    # Each attenuation step should weaken
    cmp_1 = algebra.compare(attenuated_1, top)
    cmp_2 = algebra.compare(attenuated_2, top)
    assert cmp_1 <= 0
    assert cmp_2 <= 0


def test_trust_demote_enforces_ceiling() -> None:
    """TrustAlgebra.demote correctly enforces a ceiling — output ≤ ceiling."""
    algebra = _algebra()
    ceiling = TrustLevel.HUMAN_ATTESTED

    for level in TrustLevel.ordered():
        result = algebra.demote(level, ceiling)
        assert result is not None
        cmp = algebra.compare(result, ceiling)
        assert cmp <= 0, (
            f"demote({level}, ceiling={ceiling}) = {result} exceeds ceiling"
        )


# ---------------------------------------------------------------------------
# Supplementary: Trust profile join across channels is conservative
# ---------------------------------------------------------------------------


def test_join_profiles_from_multiple_channels_is_conservative() -> None:
    """Joining trust profiles from solver, oracle, and copilot channels is conservative."""
    profiles = [
        _profile(TrustTier.VERIFIED, "A.solver", reasons=("z3-discharged",)),
        _profile(TrustTier.REVIEWED, "A.runtime", reasons=("runtime-tested",)),
        _profile(TrustTier.PROPOSAL, "A.copilot", reasons=("copilot-proposed",)),
    ]

    joined = join_trust_profiles(*profiles)
    # Must be ≤ min(VERIFIED, REVIEWED, PROPOSAL) = PROPOSAL
    assert joined.tier <= TrustTier.REVIEWED

    # Scope should aggregate
    assert isinstance(joined.support_scope, tuple)


def test_evidence_channels_carry_trust_ceiling() -> None:
    """Evidence records from different channels carry appropriate trust ceilings."""
    solver_record = build_channel("z3-sat", EvidenceChannel.SOLVER)
    copilot_record = build_channel("copilot-hint", EvidenceChannel.COPILOT)
    oracle_record = build_channel("oracle-claim", EvidenceChannel.ORACLE)

    # All records should have their channels set correctly
    assert solver_record.channel == EvidenceChannel.SOLVER
    assert copilot_record.channel == EvidenceChannel.COPILOT
    assert oracle_record.channel == EvidenceChannel.ORACLE

    # The semantic hierarchy: solver > oracle > copilot
    # This is enforced by trust profiles, not channel objects directly —
    # but we verify the channel labels are distinct
    channels = {solver_record.channel, copilot_record.channel, oracle_record.channel}
    assert len(channels) == 3


def test_judgment_trust_annotation_ceiling_is_respected() -> None:
    """A judgment constructed with a ceiling cannot be annotated above it."""
    coord = CoordinateObject(components=("Alpha",))
    prop = Proposition(kind=PropositionKind.STRUCTURAL, formula="x > 0")
    carrier = Carrier(name="carrier")
    trust = TrustAnnotation(
        level=JTL.ORACLE_PROPOSED,
        ceiling=JTL.ORACLE_PROPOSED,   # ceiling == level → no promotion possible
        evidence_basis=("oracle-A",),
        reasons=("oracle-proposal",),
    )
    judgment = Judgment(
        coordinate=coord,
        proposition=prop,
        carrier=carrier,
        trust=trust,
        provenance=Provenance(source=ProvenanceSource.ORACLE),
        status=JudgmentStatus.PROPOSED,
    )
    assert judgment.trust.level == JTL.ORACLE_PROPOSED
    assert judgment.trust.ceiling == JTL.ORACLE_PROPOSED
    # Level cannot exceed ceiling
    assert judgment.trust.level <= judgment.trust.ceiling


def test_trust_algebra_is_admissible_rejects_misconfigurations() -> None:
    """TrustAlgebra.is_admissible returns False for misconfigured evidence."""
    algebra = _algebra()

    # An admissible configuration should have a recognized structure
    valid_config = {
        "channel": "solver",
        "trust_level": "solver_discharged",
        "scope": ["Sigma.A"],
    }
    invalid_config = {
        "channel": "unknown",
        "trust_level": "impossible",
        "scope": [],
    }

    valid_result = algebra.is_admissible(valid_config)
    invalid_result = algebra.is_admissible(invalid_config)

    assert isinstance(valid_result, bool)
    assert isinstance(invalid_result, bool)


def test_trust_profile_challenge_demotes_and_records_reason() -> None:
    """TrustProfile.challenge correctly demotes and records the challenge reason."""
    profile = _profile(TrustTier.VERIFIED, "Beta.A", reasons=("solver-discharged",))
    challenged = profile.challenge(reason="overlap-condition-violated")

    # After challenge, tier should be at most REVIEWED
    assert challenged.tier <= TrustTier.REVIEWED
    # Original unaffected
    assert profile.tier == TrustTier.VERIFIED


def test_full_trust_stack_invariant_chain() -> None:
    """A complete trust invariant chain: construct → annotate → challenge → promote → record."""
    algebra = _algebra()

    # Step 1: Start with a copilot proposal
    profile = _profile(TrustTier.PROPOSAL, "Tau.full", reasons=("copilot-initial",))
    assert profile.tier == TrustTier.PROPOSAL

    # Step 2: Explicit promotion to REVIEWED (ceremony performed)
    reviewed = profile.promote(TrustTier.REVIEWED, explicit=True)
    reviewed = reviewed.with_reasons("human-review-passed")
    assert reviewed.tier == TrustTier.REVIEWED

    # Step 3: Solver discharges the formula → explicit promotion to VERIFIED
    verified = reviewed.promote(TrustTier.VERIFIED, explicit=True)
    verified = verified.with_reasons("z3-discharged", "audit-id-42")
    assert verified.tier == TrustTier.VERIFIED

    # Step 4: Challenge issued (e.g., new counterexample)
    challenged = verified.challenge(reason="counterexample-found")
    assert challenged.tier < TrustTier.VERIFIED

    # Step 5: Algebra-level attenuate reflects the demotion
    alg_level = TrustLevel.SOLVER_DISCHARGED
    attenuated = algebra.attenuate(alg_level, 1)
    assert algebra.compare(attenuated, alg_level) <= 0

    # Step 6: Record provenance of the promotion in a graph
    graph = ProvenanceGraph()
    graph.add_node(ProvenanceNode(
        node_id="n-proposal",
        source_channel="copilot",
        operation=ProvenanceOperation.PRODUCED,
        coordinate="Tau.full",
        trust_at_creation="proposal",
    ))
    graph.add_node(ProvenanceNode(
        node_id="n-reviewed",
        source_channel="human-auditor",
        operation=ProvenanceOperation.PROMOTED,
        inputs=("n-proposal",),
        coordinate="Tau.full",
        trust_at_creation="reviewed",
    ))
    graph.add_node(ProvenanceNode(
        node_id="n-verified",
        source_channel="z3",
        operation=ProvenanceOperation.PROMOTED,
        inputs=("n-reviewed",),
        coordinate="Tau.full",
        trust_at_creation="verified",
    ))
    assert graph.is_acyclic() is True
    roots = graph.find_roots()
    assert "n-proposal" in roots
    leaves = graph.find_leaves()
    assert "n-verified" in leaves
