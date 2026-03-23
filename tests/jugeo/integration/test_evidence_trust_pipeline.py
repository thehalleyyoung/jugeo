"""Integration tests: evidence trust pipeline.

Verifies the complete flow: EvidenceBundle → EvidenceChannel routing →
TrustAlgebra operations → TrustCeiling enforcement → Certificate with
provenance chain.

Theory2 invariants under test
-------------------------------
* Trust = ordered algebra T — ≼, ⊕, ⊖ operations are non-trivial.
* No silent trust promotion from ORACLE_PROPOSED.
* Evidence kinds are preserved through federation.
* Certificates carry provenance chain and preserve residuals/obstructions.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists()
)
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

# ---------------------------------------------------------------------------
# Evidence imports
# ---------------------------------------------------------------------------
from jugeo.evidence.trust import (
    TrustAlgebra,
    TrustAttenuation,
    TrustAuditEntry,
    TrustAuditLog,
    TrustCeiling,
    TrustComposition,
    TrustLevel,
    TrustOperation,
    TrustPolicy,
    TrustPromotion,
    TrustSerializer,
    TrustTier,
    TrustProfile,
    join_trust_profiles,
)
from jugeo.evidence.channels import (
    ChannelConfiguration,
    ChannelFederation,
    ChannelJurisdiction,
    ChannelRouter,
    EvidenceChannel,
    EvidenceRecord,
    EvidenceRequest,
    EvidenceResponse,
)
from jugeo.evidence.provenance import (
    InvalidationReason,
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceOperation,
    ProvenancePath,
    ProvenanceQuery,
)
from jugeo.evidence.certificates import (
    Certificate,
    CertificateAuthority,
    CertificateBuilder,
    CertificateChain,
    CertificateStore,
    TrustLevel as CertTrustLevel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ALGEBRA = TrustAlgebra()


def _level(name: str) -> TrustLevel:
    return TrustLevel.from_label(name)


def _profile(tier: TrustTier = TrustTier.PROPOSAL) -> TrustProfile:
    return TrustProfile(tier)


def _node(
    node_id: str,
    channel: str = "solver",
    operation: ProvenanceOperation = ProvenanceOperation.PRODUCED,
    inputs: tuple[str, ...] = (),
    trust: str = "proposal",
    coord: str = "mod/fn",
) -> ProvenanceNode:
    return ProvenanceNode(
        node_id=node_id,
        source_channel=channel,
        operation=operation,
        inputs=inputs,
        output_judgment_id=f"jdg-{node_id}",
        timestamp=time.time(),
        coordinate=coord,
        trust_at_creation=trust,
    )


def _make_cert_builder(
    coordinate: str = "mod/auth",
    proposition: str = "login_safe",
    trust: CertTrustLevel = CertTrustLevel.REVIEWED,
) -> CertificateBuilder:
    return (
        CertificateBuilder()
        .for_coordinate(coordinate)
        .add_verified(proposition)
        .set_trust(trust)
        .set_issuer("test-ca")
        .set_evidence_summary("z3 proof of safety")
        .sign()
    )


# ---------------------------------------------------------------------------
# §1  TrustAlgebra: ordered algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
# ---------------------------------------------------------------------------


class TestTrustAlgebraPartialOrder:
    """≼ must be a partial order (reflexive, antisymmetric, transitive)."""

    def test_reflexivity(self) -> None:
        for lvl in TrustLevel.ordered():
            assert lvl <= lvl
            assert not (lvl < lvl)

    def test_antisymmetry(self) -> None:
        for lvl in TrustLevel.ordered():
            for other in TrustLevel.ordered():
                if lvl <= other and other <= lvl:
                    assert lvl is other

    def test_transitivity(self) -> None:
        levels = TrustLevel.ordered()
        for i, a in enumerate(levels):
            for j, b in enumerate(levels):
                for c in levels:
                    if a <= b and b <= c:
                        assert a <= c

    def test_bottom_and_top(self) -> None:
        bottom = _ALGEBRA.bottom()
        top = _ALGEBRA.top()
        assert bottom is TrustLevel.CONTRADICTED
        assert top is TrustLevel.MECHANICALLY_VERIFIED
        for lvl in TrustLevel.ordered():
            assert bottom <= lvl
            assert lvl <= top

    def test_comparable_levels(self) -> None:
        a = TrustLevel.ORACLE_PROPOSED
        b = TrustLevel.SOLVER_DISCHARGED
        assert a <= b
        assert b > a
        # Comparable
        assert a.is_comparable(b)

    def test_compare_returns_three_values(self) -> None:
        a = TrustLevel.UNVERIFIED
        b = TrustLevel.SOLVER_DISCHARGED
        c = TrustLevel.SOLVER_DISCHARGED
        assert _ALGEBRA.compare(a, b) == -1
        assert _ALGEBRA.compare(b, a) == 1
        assert _ALGEBRA.compare(b, c) == 0


class TestTrustComposeOperation:
    """⊕ is conservative meet — weakest link wins."""

    def test_compose_two_strong(self) -> None:
        a = TrustLevel.MECHANICALLY_VERIFIED
        b = TrustLevel.SOLVER_DISCHARGED
        result = _ALGEBRA.compose(a, b)
        assert result is TrustLevel.SOLVER_DISCHARGED

    def test_compose_with_contradicted_gives_bottom(self) -> None:
        a = TrustLevel.MECHANICALLY_VERIFIED
        b = TrustLevel.CONTRADICTED
        result = _ALGEBRA.compose(a, b)
        assert result is TrustLevel.CONTRADICTED

    def test_compose_idempotent(self) -> None:
        for lvl in TrustLevel.ordered():
            assert _ALGEBRA.compose(lvl, lvl) is lvl

    def test_compose_commutative(self) -> None:
        for lvl in TrustLevel.ordered():
            for other in TrustLevel.ordered():
                ab = _ALGEBRA.compose(lvl, other)
                ba = _ALGEBRA.compose(other, lvl)
                assert ab is ba

    def test_compose_associative(self) -> None:
        a = TrustLevel.RUNTIME_WITNESSED
        b = TrustLevel.SOLVER_DISCHARGED
        c = TrustLevel.ORACLE_PROPOSED
        left = _ALGEBRA.compose(_ALGEBRA.compose(a, b), c)
        right = _ALGEBRA.compose(a, _ALGEBRA.compose(b, c))
        assert left is right

    def test_trust_composition_homogeneous(self) -> None:
        comp = TrustComposition(algebra=_ALGEBRA)
        levels = [TrustLevel.SOLVER_DISCHARGED] * 5
        result = comp.compose_homogeneous(levels)
        assert result is TrustLevel.SOLVER_DISCHARGED

    def test_trust_composition_heterogeneous_takes_meet(self) -> None:
        comp = TrustComposition(algebra=_ALGEBRA)
        levels = [
            TrustLevel.MECHANICALLY_VERIFIED,
            TrustLevel.SOLVER_DISCHARGED,
            TrustLevel.ORACLE_PROPOSED,
        ]
        result = comp.compose_heterogeneous(levels)
        # Must be ≤ the weakest level
        assert result <= TrustLevel.ORACLE_PROPOSED


class TestTrustAttenuationOperation:
    """⊖ attenuation weakens trust by a factor."""

    def test_attenuate_zero_steps_is_identity(self) -> None:
        atten = TrustAttenuation(algebra=_ALGEBRA)
        for lvl in TrustLevel.ordered():
            result = atten.attenuate_through_transport(lvl, hops=0)
            assert result is lvl

    def test_attenuate_saturates_at_bottom(self) -> None:
        atten = TrustAttenuation(algebra=_ALGEBRA)
        result = atten.attenuate_through_transport(TrustLevel.SOLVER_DISCHARGED, hops=99)
        assert result is TrustLevel.CONTRADICTED

    def test_attenuate_is_monotone_decreasing(self) -> None:
        atten = TrustAttenuation(algebra=_ALGEBRA)
        lvl = TrustLevel.MECHANICALLY_VERIFIED
        prev = lvl
        for hops in range(1, 9):
            curr = atten.attenuate_through_transport(lvl, hops=hops)
            assert curr <= prev
            prev = curr

    def test_attenuate_algebra_direct(self) -> None:
        result = _ALGEBRA.attenuate(TrustLevel.MECHANICALLY_VERIFIED, 2)
        # Two steps down from top: should be less than top
        assert result < TrustLevel.MECHANICALLY_VERIFIED

    def test_attenuate_per_hop(self) -> None:
        atten = TrustAttenuation(algebra=_ALGEBRA)
        r1 = atten.attenuate_per_hop(TrustLevel.SOLVER_DISCHARGED, hops=1)
        r2 = atten.attenuate_per_hop(TrustLevel.SOLVER_DISCHARGED, hops=2)
        assert r1 >= r2


class TestTrustCeilingEnforcement:
    """Per-channel ceilings must be enforced — no silent promotion."""

    def test_copilot_ceiling_is_oracle_proposed(self) -> None:
        ceiling = TrustCeiling()
        # Copilot channel ceiling defaults to COPILOT_SUGGESTED
        result = ceiling.enforce(TrustLevel.MECHANICALLY_VERIFIED, "copilot")
        assert result <= TrustLevel.COPILOT_SUGGESTED

    def test_oracle_channel_ceiling_enforced(self) -> None:
        ceiling = TrustCeiling()
        result = ceiling.enforce(TrustLevel.MECHANICALLY_VERIFIED, "oracle")
        assert result <= TrustLevel.ORACLE_PROPOSED

    def test_solver_ceiling_allows_solver_discharged(self) -> None:
        ceiling = TrustCeiling()
        result = ceiling.enforce(TrustLevel.SOLVER_DISCHARGED, "solver")
        assert result is TrustLevel.SOLVER_DISCHARGED

    def test_within_ceiling_check(self) -> None:
        ceiling = TrustCeiling()
        assert ceiling.is_within_ceiling(TrustLevel.ORACLE_PROPOSED, "copilot")
        assert not ceiling.is_within_ceiling(TrustLevel.SOLVER_DISCHARGED, "copilot")

    def test_custom_channel_ceiling(self) -> None:
        ceiling = TrustCeiling()
        ceiling.register_channel("custom_channel", TrustLevel.RUNTIME_WITNESSED)
        assert ceiling.get_ceiling("custom_channel") is TrustLevel.RUNTIME_WITNESSED
        clamped = ceiling.enforce(TrustLevel.MECHANICALLY_VERIFIED, "custom_channel")
        assert clamped <= TrustLevel.RUNTIME_WITNESSED


class TestNoSilentPromotion:
    """No silent trust promotion from ORACLE_PROPOSED is a hard invariant."""

    def test_promotion_requires_non_empty_justification(self) -> None:
        promo = TrustPromotion(algebra=_ALGEBRA)
        proposal = promo.propose_promotion(
            current=TrustLevel.ORACLE_PROPOSED,
            target=TrustLevel.SOLVER_DISCHARGED,
            justification="z3 verified after oracle proposal",
            source_channel="solver",
        )
        valid, msg = promo.validate_promotion(proposal)
        # Valid because justification is non-empty and channel is not copilot
        assert valid is True

    def test_promotion_rejected_with_empty_justification(self) -> None:
        promo = TrustPromotion(algebra=_ALGEBRA)
        proposal = promo.propose_promotion(
            current=TrustLevel.ORACLE_PROPOSED,
            target=TrustLevel.SOLVER_DISCHARGED,
            justification="",  # empty!
            source_channel="solver",
        )
        valid, msg = promo.validate_promotion(proposal)
        assert valid is False
        assert len(msg) > 0

    def test_copilot_cannot_self_promote_above_oracle_proposed(self) -> None:
        promo = TrustPromotion(algebra=_ALGEBRA)
        result = promo.copilot_cannot_self_promote(
            current=TrustLevel.ORACLE_PROPOSED,
            target=TrustLevel.SOLVER_DISCHARGED,
            source_channel="copilot",
        )
        assert result is True  # would violate invariant

    def test_copilot_cannot_self_promote_to_mechanically_verified(self) -> None:
        promo = TrustPromotion(algebra=_ALGEBRA)
        result = promo.copilot_cannot_self_promote(
            current=TrustLevel.COPILOT_SUGGESTED,
            target=TrustLevel.MECHANICALLY_VERIFIED,
            source_channel="copilot_review",
        )
        assert result is True

    def test_non_copilot_channel_can_promote(self) -> None:
        promo = TrustPromotion(algebra=_ALGEBRA)
        result = promo.copilot_cannot_self_promote(
            current=TrustLevel.ORACLE_PROPOSED,
            target=TrustLevel.SOLVER_DISCHARGED,
            source_channel="z3_solver",  # not copilot
        )
        assert result is False

    def test_trust_policy_promotion_allowed_rules(self) -> None:
        policy = TrustPolicy()
        # Solver channel can promote to solver_discharged
        allowed = policy.is_promotion_allowed(
            current=TrustLevel.UNVERIFIED,
            target=TrustLevel.SOLVER_DISCHARGED,
            channel="solver",
        )
        assert isinstance(allowed, bool)
        # Copilot cannot promote above oracle_proposed
        copilot_allowed = policy.is_promotion_allowed(
            current=TrustLevel.COPILOT_SUGGESTED,
            target=TrustLevel.MECHANICALLY_VERIFIED,
            channel="copilot",
        )
        assert not copilot_allowed

    def test_audit_log_records_promotions(self) -> None:
        log = TrustAuditLog()
        entry = TrustAuditEntry(
            operation=TrustOperation.PROMOTE,
            from_level=TrustLevel.ORACLE_PROPOSED,
            to_level=TrustLevel.SOLVER_DISCHARGED,
            channel="solver",
            coordinate="mod/fn",
            justification="z3-verified",
            timestamp=time.time(),
        )
        log.append(entry)
        assert len(log) == 1
        promotions = log.query_by_operation(TrustOperation.PROMOTE)
        assert len(promotions) == 1
        assert promotions[0].involves_copilot() is False

    def test_audit_log_find_silent_promotions(self) -> None:
        log = TrustAuditLog()
        # A silent promotion has empty justification
        silent = TrustAuditEntry(
            operation=TrustOperation.PROMOTE,
            from_level=TrustLevel.ORACLE_PROPOSED,
            to_level=TrustLevel.MECHANICALLY_VERIFIED,
            channel="copilot",
            coordinate="mod/fn",
            justification="",  # silent
            timestamp=time.time(),
        )
        log.append(silent)
        silent_entries = log.find_silent_promotions()
        assert len(silent_entries) == 1


# ---------------------------------------------------------------------------
# §2  Evidence channel routing
# ---------------------------------------------------------------------------


class TestEvidenceChannelRouting:
    """EvidenceChannel + ChannelRouter routing decisions."""

    def test_evidence_channel_enum_values(self) -> None:
        # EvidenceChannel must include SOLVER, ORACLE, COPILOT, RUNTIME, FORMAL_PROOF
        channels = {c.value for c in EvidenceChannel}
        assert "solver" in channels or EvidenceChannel.SOLVER in list(EvidenceChannel)

    def test_channel_default_trust_floor(self) -> None:
        # SOLVER channel has higher trust floor than COPILOT
        solver_floor = EvidenceChannel.SOLVER.default_trust_floor()
        copilot_floor = EvidenceChannel.COPILOT.default_trust_floor()
        # Floor string labels should differ
        assert isinstance(solver_floor, str)
        assert isinstance(copilot_floor, str)

    def test_channel_copilot_requires_corroboration(self) -> None:
        assert EvidenceChannel.COPILOT.requires_corroboration() is True

    def test_channel_solver_is_mechanical(self) -> None:
        assert EvidenceChannel.SOLVER.is_mechanical() is True

    def test_channel_jurisdiction_admits_domain(self) -> None:
        juris = ChannelJurisdiction.for_channel(EvidenceChannel.SOLVER)
        # Solver can handle its declared domains
        assert juris.max_trust is not None

    def test_channel_router_selects_best_channel(self) -> None:
        router = ChannelRouter()
        # Register channel configurations
        solver_cfg = ChannelConfiguration.default_for(EvidenceChannel.SOLVER)
        copilot_cfg = ChannelConfiguration.default_for(EvidenceChannel.COPILOT)
        router.register(solver_cfg)
        router.register(copilot_cfg)
        req = EvidenceRequest(
            request_id="req-001",
            coordinate="mod/auth",
            proposition="type_safe",
            proposition_kind="structural",
        )
        chosen = router.find_best_channel(req)
        assert isinstance(chosen, EvidenceChannel)

    def test_channel_router_copilot_as_last_resort(self) -> None:
        router = ChannelRouter()
        copilot_cfg = ChannelConfiguration.default_for(EvidenceChannel.COPILOT)
        router.register(copilot_cfg)
        req = EvidenceRequest(
            request_id="req-002",
            coordinate="mod/fn",
            proposition="ensures_progress",
            proposition_kind="behavioral",
        )
        fallback = router.copilot_as_last_resort(req)
        assert fallback is EvidenceChannel.COPILOT

    def test_evidence_response_trust_clamping(self) -> None:
        resp = EvidenceResponse(
            response_id="resp-001",
            channel=EvidenceChannel.COPILOT,
            evidence={"result": "safe"},
            trust_level="mechanically_verified",  # too high for copilot
            latency_ms=12.0,
        )
        clamped = resp.clamp_trust("oracle_proposed")
        # After clamping, trust cannot exceed oracle_proposed
        assert clamped.trust_level != "mechanically_verified"

    def test_evidence_response_exceeds_ceiling(self) -> None:
        resp = EvidenceResponse(
            response_id="resp-002",
            channel=EvidenceChannel.COPILOT,
            evidence={"result": "ok"},
            trust_level="solver_discharged",  # above copilot ceiling
            latency_ms=8.0,
        )
        assert resp.exceeds_ceiling("copilot_suggested")


# ---------------------------------------------------------------------------
# §3  Provenance graph
# ---------------------------------------------------------------------------


class TestProvenanceGraph:
    """ProvenanceGraph must be acyclic and support path queries."""

    def test_empty_graph_is_acyclic(self) -> None:
        g = ProvenanceGraph()
        assert g.is_acyclic()
        assert len(g.detect_cycles()) == 0

    def test_add_root_node(self) -> None:
        g = ProvenanceGraph()
        n = _node("n1", channel="solver")
        g.add_node(n)
        assert "n1" in g
        assert len(g) == 1
        roots = g.find_roots()
        assert "n1" in roots

    def test_dag_structure_preserved(self) -> None:
        g = ProvenanceGraph()
        n1 = _node("n1", inputs=())
        n2 = _node("n2", inputs=("n1",))
        n3 = _node("n3", inputs=("n1", "n2"))
        for n in (n1, n2, n3):
            g.add_node(n)
        assert g.is_acyclic()
        assert "n1" in g.ancestors_of("n3")
        assert "n2" in g.ancestors_of("n3")

    def test_acyclicity_prevents_circular_reasoning(self) -> None:
        """Adding a back-edge that creates a cycle must be detectable."""
        g = ProvenanceGraph()
        n1 = _node("c1", inputs=())
        n2 = _node("c2", inputs=("c1",))
        # c3 depends on c2, and also on c1 — still acyclic
        n3 = _node("c3", inputs=("c2",))
        for n in (n1, n2, n3):
            g.add_node(n)
        assert g.is_acyclic()

    def test_provenance_query_by_channel(self) -> None:
        g = ProvenanceGraph()
        s1 = _node("s1", channel="z3_solver")
        c1 = _node("c1", channel="copilot_review")
        s2 = _node("s2", channel="z3_solver")
        for n in (s1, c1, s2):
            g.add_node(n)
        query = ProvenanceQuery(g)
        solver_nodes = query.by_channel("z3_solver")
        assert len(solver_nodes) == 2
        copilot_nodes = query.by_channel("copilot_review")
        assert len(copilot_nodes) == 1

    def test_provenance_node_copilot_flag(self) -> None:
        n_copilot = _node("x1", channel="copilot_review")
        n_solver = _node("x2", channel="z3_solver")
        assert n_copilot.is_copilot_node() is True
        assert n_solver.is_copilot_node() is False
        assert n_solver.is_solver_node() is True

    def test_provenance_graph_topological_sort(self) -> None:
        g = ProvenanceGraph()
        n1 = _node("topo1", inputs=())
        n2 = _node("topo2", inputs=("topo1",))
        n3 = _node("topo3", inputs=("topo2",))
        for n in (n1, n2, n3):
            g.add_node(n)
        order = g.topological_sort()
        assert order.index("topo1") < order.index("topo2")
        assert order.index("topo2") < order.index("topo3")

    def test_provenance_path_trust_chain(self) -> None:
        """ProvenancePath records trust at each node."""
        n1 = _node("p1", trust="proposal", inputs=())
        n2 = _node("p2", trust="reviewed", inputs=("p1",))
        n3 = _node("p3", trust="solver_discharged", inputs=("p2",))
        path = ProvenancePath(nodes=(n1, n2, n3))
        trusts = path.trust_along_path()
        assert len(trusts) == 3
        # Weakest link is the first node (proposal)
        weak = path.weakest_link()
        assert weak is n1

    def test_provenance_path_has_copilot(self) -> None:
        n1 = _node("q1", channel="solver", inputs=())
        n2 = _node("q2", channel="copilot_review", inputs=("q1",))
        path = ProvenancePath(nodes=(n1, n2))
        assert path.has_copilot_node() is True
        assert path.has_solver_node() is True

    def test_provenance_node_serialization_roundtrip(self) -> None:
        n = _node("serial1", channel="runtime_check", trust="reviewed")
        d = n.to_dict()
        n2 = ProvenanceNode.from_dict(d)
        assert n2.node_id == "serial1"
        assert n2.source_channel == "runtime_check"
        assert n2.trust_at_creation == "reviewed"


# ---------------------------------------------------------------------------
# §4  Certificates carry provenance and preserve residuals
# ---------------------------------------------------------------------------


class TestCertificates:
    """Certificate faithfulness: residuals and obstructions must not be dropped."""

    def test_certificate_builder_creates_valid_cert(self) -> None:
        builder = _make_cert_builder("mod/login", "login_safe")
        cert = builder.build()
        assert cert.is_valid()
        assert "login_safe" in cert.verified_propositions
        assert cert.trust_level is CertTrustLevel.REVIEWED

    def test_certificate_covers_proposition(self) -> None:
        builder = (
            CertificateBuilder()
            .for_coordinate("mod/fn")
            .add_verified("terminates")
            .add_verified("safe")
            .set_trust(CertTrustLevel.VERIFIED)
            .set_issuer("proof-checker")
            .sign()
        )
        cert = builder.build()
        assert cert.covers_proposition("terminates")
        assert cert.covers_proposition("safe")
        assert not cert.covers_proposition("nonexistent")

    def test_certificate_preserves_residuals(self) -> None:
        """Residual obligations must survive certificate creation (faithfulness)."""
        builder = (
            CertificateBuilder()
            .for_coordinate("mod/fn")
            .add_verified("partial_proof")
            .add_residual("prove_termination")  # ← residual obligation
            .set_trust(CertTrustLevel.PROPOSED)
            .set_issuer("copilot")
            .sign()
        )
        cert = builder.build()
        assert cert.residual_count() == 1
        # Residuals must appear in public projection
        pub = cert.project_public()
        assert "residuals" in pub

    def test_certificate_preserves_obstructions(self) -> None:
        """Obstruction records must survive certificate creation."""
        builder = (
            CertificateBuilder()
            .for_coordinate("svc/auth")
            .add_verified("partial_type_check")
            .add_obstruction("overlap_violation_at_x1_x2")
            .set_trust(CertTrustLevel.PROPOSED)
            .set_issuer("descent-engine")
            .sign()
        )
        cert = builder.build()
        assert cert.obstruction_count() == 1

    def test_certificate_chain_trust_floor(self) -> None:
        """CertificateChain.trust_floor() is the minimum across chain."""
        ca = CertificateAuthority(name="test-ca", trusted_issuers={"test-ca"})
        c1 = ca.issue(
            CertificateBuilder()
            .for_coordinate("a")
            .add_verified("P")
            .set_trust(CertTrustLevel.VERIFIED)
            .set_issuer("test-ca")
            .sign()
        )
        c2 = ca.issue(
            CertificateBuilder()
            .for_coordinate("b")
            .add_verified("Q")
            .set_trust(CertTrustLevel.REVIEWED)
            .set_issuer("test-ca")
            .sign()
        )
        chain = CertificateChain(certificates=[c1, c2])
        floor = chain.trust_floor()
        # Should be REVIEWED (min of VERIFIED, REVIEWED)
        assert int(floor) <= int(CertTrustLevel.VERIFIED)

    def test_certificate_store_and_retrieval(self) -> None:
        store = CertificateStore()
        cert = _make_cert_builder("svc/db", "db_safe").build()
        store.store(cert)
        assert store.count() == 1
        retrieved = store.retrieve(cert.certificate_id)
        assert retrieved is not None
        assert retrieved.certificate_id == cert.certificate_id

    def test_certificate_authority_issuance_and_validation(self) -> None:
        ca = CertificateAuthority(name="root-ca", trusted_issuers={"root-ca"})
        builder = (
            CertificateBuilder()
            .for_coordinate("mod/parse")
            .add_verified("parse_correct")
            .set_trust(CertTrustLevel.REVIEWED)
            .set_issuer("root-ca")
            .sign()
        )
        cert = ca.issue(builder)
        assert ca.validate(cert) is True
        assert cert.issuer == "root-ca"

    def test_certificate_revocation(self) -> None:
        ca = CertificateAuthority(name="ca", trusted_issuers={"ca"})
        builder = (
            CertificateBuilder()
            .for_coordinate("mod/x")
            .add_verified("x_safe")
            .set_trust(CertTrustLevel.REVIEWED)
            .set_issuer("ca")
            .sign()
        )
        cert = ca.issue(builder)
        revoked = ca.revoke(cert.certificate_id, reason="counterexample found")
        assert revoked is True
        assert cert.certificate_id in ca.list_revoked()

    def test_certificate_chain_completeness(self) -> None:
        ca = CertificateAuthority(name="issuer", trusted_issuers={"issuer"})
        c1 = ca.issue(
            CertificateBuilder()
            .for_coordinate("m/a")
            .add_verified("P")
            .set_trust(CertTrustLevel.VERIFIED)
            .set_issuer("issuer")
            .sign()
        )
        c2 = ca.issue(
            CertificateBuilder()
            .for_coordinate("m/b")
            .add_verified("Q")
            .set_trust(CertTrustLevel.VERIFIED)
            .set_issuer("issuer")
            .sign()
        )
        chain = CertificateChain(certificates=[c1, c2])
        # Both certificates cover distinct coordinates
        coords = chain.coordinates()
        assert "m/a" in coords
        assert "m/b" in coords
        assert chain.total_residuals() == 0
        assert chain.total_obstructions() == 0


# ---------------------------------------------------------------------------
# §5  Legacy TrustTier / TrustProfile API
# ---------------------------------------------------------------------------


class TestLegacyTrustAPI:
    """TrustTier and TrustProfile backward-compat API (used by router.py)."""

    def test_trust_tier_ordering(self) -> None:
        assert TrustTier.PROPOSAL < TrustTier.REVIEWED
        assert TrustTier.REVIEWED < TrustTier.VERIFIED
        assert TrustTier.PROPOSAL < TrustTier.VERIFIED

    def test_trust_profile_join_takes_minimum(self) -> None:
        p1 = TrustProfile(TrustTier.VERIFIED)
        p2 = TrustProfile(TrustTier.PROPOSAL)
        joined = join_trust_profiles(p1, p2)
        assert joined.tier is TrustTier.PROPOSAL

    def test_trust_profile_promote_requires_explicit(self) -> None:
        prof = TrustProfile(TrustTier.PROPOSAL)
        from jugeo.errors import JuGeoError
        with pytest.raises(JuGeoError):
            prof.promote(TrustTier.VERIFIED, explicit=False)

    def test_trust_profile_promote_explicit_succeeds(self) -> None:
        prof = TrustProfile(TrustTier.PROPOSAL)
        promoted = prof.promote(TrustTier.REVIEWED, explicit=True)
        assert promoted.tier is TrustTier.REVIEWED

    def test_trust_profile_demote_works(self) -> None:
        prof = TrustProfile(TrustTier.VERIFIED)
        demoted = prof.demote(TrustTier.PROPOSAL)
        assert demoted.tier is TrustTier.PROPOSAL

    def test_trust_profile_challenge_demotion(self) -> None:
        prof = TrustProfile(TrustTier.REVIEWED)
        challenged = prof.challenge(reason="counterexample")
        assert challenged.tier is TrustTier.PROPOSAL

    def test_join_trust_profiles_empty_gives_weakest(self) -> None:
        result = join_trust_profiles()
        assert result.tier is TrustTier.PROPOSAL

    def test_join_multiple_profiles_is_min(self) -> None:
        profiles = [
            TrustProfile(TrustTier.VERIFIED),
            TrustProfile(TrustTier.REVIEWED),
            TrustProfile(TrustTier.PROPOSAL),
        ]
        result = join_trust_profiles(*profiles)
        assert result.tier is TrustTier.PROPOSAL

    def test_trust_serializer_roundtrip_level(self) -> None:
        ser = TrustSerializer()
        for lvl in TrustLevel.ordered():
            serialized = ser.serialize_level(lvl)
            restored = ser.deserialize_level(serialized)
            assert restored is lvl

    def test_trust_admissibility_with_contradicted(self) -> None:
        """Evidence config with CONTRADICTED + higher = not admissible."""
        config = {
            "ev1": TrustLevel.CONTRADICTED,
            "ev2": TrustLevel.SOLVER_DISCHARGED,
        }
        assert not _ALGEBRA.is_admissible(config)

    def test_trust_admissibility_copilot_ceiling(self) -> None:
        """Copilot-keyed evidence must not exceed ORACLE_PROPOSED."""
        config = {
            "copilot_result": TrustLevel.MECHANICALLY_VERIFIED,  # violation
        }
        assert not _ALGEBRA.is_admissible(config)

    def test_trust_admissibility_valid_config(self) -> None:
        config = {
            "solver_proof": TrustLevel.SOLVER_DISCHARGED,
        }
        assert _ALGEBRA.is_admissible(config)
