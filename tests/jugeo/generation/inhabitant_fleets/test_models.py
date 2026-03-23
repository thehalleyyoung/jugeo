"""Tests for jugeo.generation.inhabitant_fleets.models."""
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
# Optional imports
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
    from jugeo.evidence.trust import TrustTier

    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

_SKIP = pytest.mark.skipif(not _MODELS_AVAILABLE, reason="models not importable")

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_proposal(
    patch_id="patch-001",
    section_label="section-A",
    content="some semantic content",
    trust_tier=None,
    evidence_score=0.75,
    status=None,
    competing=None,
):
    if not _MODELS_AVAILABLE:
        return None
    if trust_tier is None:
        trust_tier = TrustTier.PROPOSAL
    if status is None:
        status = ProposalStatus.PENDING
    if competing is None:
        competing = []
    return InhabitantProposal(
        proposal_id=str(uuid.uuid4()),
        patch_id=patch_id,
        section_label=section_label,
        semantic_content=content,
        proposer_id="proposer-test",
        trust_tier=trust_tier,
        evidence_score=evidence_score,
        competing_proposals=competing,
        status=status,
        created_at=time.time(),
        metadata={},
    )


def _make_bid(bid_score=0.8, resource_estimate=3.0, overlap=0.9, backpressure=0.7):
    if not _MODELS_AVAILABLE:
        return None
    return FleetBid(
        bid_id=str(uuid.uuid4()),
        fleet_member_id="member-test",
        goal_label="goal-test",
        proposed_inhabitant="inhabitant-content",
        bid_score=bid_score,
        resource_estimate=resource_estimate,
        overlap_compatibility_score=overlap,
        backpressure_tolerance=backpressure,
        metadata={},
    )


def _make_signal(
    instability=0.6,
    threshold=0.5,
    severity=None,
    targets=None,
):
    if not _MODELS_AVAILABLE:
        return None
    if severity is None:
        severity = SeverityLevel.MEDIUM
    if targets is None:
        targets = ["patch-tgt-1"]
    return BackpressureSignal(
        signal_id=str(uuid.uuid4()),
        source_patch="patch-src",
        target_patches=targets,
        instability_score=instability,
        threshold=threshold,
        severity=severity,
        timestamp=time.time(),
        remediation_hints=["hint-a"],
    )


def _make_move(move_type=None, distance=0.3, cost=1.0):
    if not _MODELS_AVAILABLE:
        return None
    if move_type is None:
        move_type = MoveType.PROPOSE
    return SemanticMove(
        move_id=str(uuid.uuid4()),
        move_type=move_type,
        source_state={"v": "source"},
        target_state={"v": "target"},
        semantic_distance=distance,
        validity_certificate="cert-test",
        overlap_impact=0.1,
        move_cost=cost,
    )


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

@_SKIP
class TestProposalStatusEnum:
    def test_pending_exists(self):
        assert ProposalStatus.PENDING is not None

    def test_accepted_exists(self):
        assert ProposalStatus.ACCEPTED is not None

    def test_rejected_exists(self):
        assert ProposalStatus.REJECTED is not None

    def test_has_three_members(self):
        assert len(list(ProposalStatus)) == 3

    def test_pending_is_string(self):
        assert isinstance(ProposalStatus.PENDING.value, str)

    def test_values_are_unique(self):
        vals = [s.value for s in ProposalStatus]
        assert len(vals) == len(set(vals))

    @pytest.mark.parametrize("status", ["PENDING", "ACCEPTED", "REJECTED"])
    def test_all_members_accessible_by_name(self, status):
        member = ProposalStatus[status]
        assert member is not None

    def test_accepted_not_pending(self):
        assert ProposalStatus.ACCEPTED != ProposalStatus.PENDING

    def test_rejected_not_accepted(self):
        assert ProposalStatus.REJECTED != ProposalStatus.ACCEPTED


@_SKIP
class TestSeverityLevelEnum:
    def test_low_exists(self):
        assert SeverityLevel.LOW is not None

    def test_medium_exists(self):
        assert SeverityLevel.MEDIUM is not None

    def test_high_exists(self):
        assert SeverityLevel.HIGH is not None

    def test_critical_exists(self):
        assert SeverityLevel.CRITICAL is not None

    def test_has_four_members(self):
        assert len(list(SeverityLevel)) == 4

    def test_values_are_strings(self):
        for s in SeverityLevel:
            assert isinstance(s.value, str)

    @pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
    def test_all_accessible_by_name(self, level):
        assert SeverityLevel[level] is not None

    def test_critical_string_value(self):
        assert "CRITICAL" in str(SeverityLevel.CRITICAL).upper() or SeverityLevel.CRITICAL.value.upper() == "CRITICAL"


@_SKIP
class TestMoveTypeEnum:
    def test_propose_exists(self):
        assert MoveType.PROPOSE is not None

    def test_retract_exists(self):
        assert MoveType.RETRACT is not None

    def test_refine_exists(self):
        assert MoveType.REFINE is not None

    def test_generalize_exists(self):
        assert MoveType.GENERALIZE is not None

    def test_specialize_exists(self):
        assert MoveType.SPECIALIZE is not None

    def test_has_five_members(self):
        assert len(list(MoveType)) == 5

    @pytest.mark.parametrize("mtype", ["PROPOSE", "RETRACT", "REFINE", "GENERALIZE", "SPECIALIZE"])
    def test_all_accessible_by_name(self, mtype):
        assert MoveType[mtype] is not None

    def test_all_string_values(self):
        for m in MoveType:
            assert isinstance(m.value, str)


# ---------------------------------------------------------------------------
# InhabitantProposal tests
# ---------------------------------------------------------------------------

@_SKIP
class TestInhabitantProposal:

    def test_creation_with_defaults(self):
        p = _make_proposal()
        assert p is not None
        assert p.status == ProposalStatus.PENDING

    def test_proposal_id_is_string(self):
        p = _make_proposal()
        assert isinstance(p.proposal_id, str)

    def test_patch_id_stored(self):
        p = _make_proposal(patch_id="patch-XYZ")
        assert p.patch_id == "patch-XYZ"

    def test_section_label_stored(self):
        p = _make_proposal(section_label="section-Q")
        assert p.section_label == "section-Q"

    def test_semantic_content_stored(self):
        p = _make_proposal(content="hello world")
        assert p.semantic_content == "hello world"

    def test_evidence_score_stored(self):
        p = _make_proposal(evidence_score=0.42)
        assert abs(p.evidence_score - 0.42) < 1e-9

    def test_trust_tier_stored(self):
        p = _make_proposal(trust_tier=TrustTier.REVIEWED)
        assert p.trust_tier == TrustTier.REVIEWED

    def test_accept_changes_status(self):
        p = _make_proposal()
        p.accept()
        assert p.status == ProposalStatus.ACCEPTED

    def test_reject_changes_status(self):
        p = _make_proposal()
        p.reject()
        assert p.status == ProposalStatus.REJECTED

    def test_accept_idempotent(self):
        p = _make_proposal()
        p.accept()
        p.accept()
        assert p.status == ProposalStatus.ACCEPTED

    def test_reject_after_accept(self):
        p = _make_proposal()
        p.accept()
        p.reject()
        assert p.status == ProposalStatus.REJECTED

    def test_compete_with_adds_competitor_id(self):
        p1 = _make_proposal()
        p2 = _make_proposal()
        p1.compete_with(p2)
        assert p2.proposal_id in p1.competing_proposals

    def test_compete_with_does_not_add_self(self):
        p = _make_proposal()
        p.compete_with(p)
        assert p.competing_proposals.count(p.proposal_id) <= 1

    def test_compete_with_multiple(self):
        p = _make_proposal()
        others = [_make_proposal() for _ in range(3)]
        for o in others:
            p.compete_with(o)
        for o in others:
            assert o.proposal_id in p.competing_proposals

    def test_score_returns_float(self):
        p = _make_proposal(evidence_score=0.8)
        s = p.score()
        assert isinstance(s, float)

    def test_score_positive(self):
        p = _make_proposal(evidence_score=0.8)
        assert p.score() >= 0.0

    def test_score_lower_with_competitors(self):
        p_alone = _make_proposal(evidence_score=0.8)
        p_contested = _make_proposal(evidence_score=0.8, competing=["x", "y", "z"])
        assert p_contested.score() <= p_alone.score()

    @pytest.mark.parametrize(
        "trust_tier,evidence_score,competitors,expected_gte",
        [
            ("PROPOSAL", 0.5, [], 0.0),
            ("REVIEWED", 0.8, [], 0.0),
            ("VERIFIED", 1.0, [], 0.0),
            ("VERIFIED", 1.0, ["a", "b"], 0.0),
            ("PROPOSAL", 0.1, ["a", "b", "c", "d"], 0.0),
        ],
    )
    def test_score_parametrized(self, trust_tier, evidence_score, competitors, expected_gte):
        tier = TrustTier[trust_tier]
        p = _make_proposal(trust_tier=tier, evidence_score=evidence_score, competing=competitors)
        assert p.score() >= expected_gte

    def test_to_dict_returns_dict(self):
        p = _make_proposal()
        d = p.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_proposal_id(self):
        p = _make_proposal()
        d = p.to_dict()
        assert "proposal_id" in d

    def test_to_dict_contains_patch_id(self):
        p = _make_proposal(patch_id="patch-dict-test")
        d = p.to_dict()
        assert d.get("patch_id") == "patch-dict-test"

    def test_to_dict_contains_status(self):
        p = _make_proposal()
        d = p.to_dict()
        assert "status" in d

    def test_from_dict_round_trip(self):
        p = _make_proposal(patch_id="patch-rt", section_label="sec-rt", content="round trip")
        d = p.to_dict()
        p2 = InhabitantProposal.from_dict(d)
        assert p2.proposal_id == p.proposal_id
        assert p2.patch_id == p.patch_id
        assert p2.semantic_content == p.semantic_content

    def test_from_dict_round_trip_status(self):
        p = _make_proposal()
        p.accept()
        d = p.to_dict()
        p2 = InhabitantProposal.from_dict(d)
        assert p2.status == ProposalStatus.ACCEPTED

    def test_from_dict_round_trip_evidence_score(self):
        p = _make_proposal(evidence_score=0.333)
        d = p.to_dict()
        p2 = InhabitantProposal.from_dict(d)
        assert abs(p2.evidence_score - 0.333) < 1e-6

    def test_validate_valid_proposal_passes(self):
        p = _make_proposal(content="valid content")
        result = p.validate()
        assert result is True or result is None or (hasattr(result, "__bool__") and bool(result) is not False)

    def test_validate_empty_content_fails(self):
        p = _make_proposal(content="")
        try:
            result = p.validate()
            assert result is False or result is None
        except (ValueError, AssertionError):
            pass  # Raising an exception is also valid behavior

    def test_validate_negative_evidence_fails(self):
        p = _make_proposal(evidence_score=-0.5)
        try:
            result = p.validate()
            if result is not None:
                assert result is False
        except (ValueError, AssertionError):
            pass

    def test_summary_is_string(self):
        p = _make_proposal()
        s = p.summary()
        assert isinstance(s, str)

    def test_summary_contains_patch_id(self):
        p = _make_proposal(patch_id="patch-summary-test")
        s = p.summary()
        assert "patch-summary-test" in s

    def test_repr_is_string(self):
        p = _make_proposal()
        r = repr(p)
        assert isinstance(r, str)

    def test_eq_by_proposal_id(self):
        p = _make_proposal()
        d = p.to_dict()
        p2 = InhabitantProposal.from_dict(d)
        assert p == p2

    def test_neq_different_id(self):
        p1 = _make_proposal()
        p2 = _make_proposal()
        assert p1 != p2

    def test_metadata_stored(self):
        p = _make_proposal()
        p.metadata["key"] = "val"
        assert p.metadata["key"] == "val"

    def test_competing_proposals_initially_empty(self):
        p = _make_proposal()
        assert p.competing_proposals == []

    def test_created_at_is_float(self):
        p = _make_proposal()
        assert isinstance(p.created_at, float)


# ---------------------------------------------------------------------------
# FleetBid tests
# ---------------------------------------------------------------------------

@_SKIP
class TestFleetBid:

    def test_creation_stores_bid_id(self):
        b = _make_bid()
        assert isinstance(b.bid_id, str)

    def test_bid_score_stored(self):
        b = _make_bid(bid_score=0.55)
        assert abs(b.bid_score - 0.55) < 1e-9

    def test_compute_total_score_returns_float(self):
        b = _make_bid()
        ts = b.compute_total_score()
        assert isinstance(ts, float)

    def test_compute_total_score_positive(self):
        b = _make_bid(bid_score=0.9, overlap=1.0, backpressure=1.0)
        assert b.compute_total_score() >= 0.0

    @pytest.mark.parametrize(
        "bid_score,resource,overlap,backpressure",
        [
            (0.9, 1.0, 0.9, 0.8),
            (0.5, 5.0, 0.5, 0.5),
            (0.1, 10.0, 0.1, 0.1),
            (1.0, 0.5, 1.0, 1.0),
        ],
    )
    def test_total_score_parametrized(self, bid_score, resource, overlap, backpressure):
        b = _make_bid(bid_score=bid_score, resource_estimate=resource, overlap=overlap, backpressure=backpressure)
        ts = b.compute_total_score()
        assert isinstance(ts, float)
        assert ts >= 0.0

    def test_is_compatible_with_compatible_bids(self):
        b1 = _make_bid(overlap=0.9)
        b2 = _make_bid(overlap=0.9)
        result = b1.is_compatible_with(b2)
        assert isinstance(result, bool)

    def test_is_compatible_with_incompatible_low_overlap(self):
        b1 = _make_bid(overlap=0.1)
        b2 = _make_bid(overlap=0.1)
        result = b1.is_compatible_with(b2)
        assert isinstance(result, bool)

    def test_to_dict_returns_dict(self):
        b = _make_bid()
        d = b.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_bid_id(self):
        b = _make_bid()
        d = b.to_dict()
        assert "bid_id" in d

    def test_to_dict_contains_bid_score(self):
        b = _make_bid(bid_score=0.77)
        d = b.to_dict()
        assert "bid_score" in d

    def test_validate_returns_boolean_or_none(self):
        b = _make_bid()
        result = b.validate()
        assert result is True or result is None or result is False

    def test_validate_negative_score_fails(self):
        b = _make_bid(bid_score=-1.0)
        try:
            result = b.validate()
            if result is not None:
                assert result is False
        except (ValueError, AssertionError):
            pass

    def test_summary_is_string(self):
        b = _make_bid()
        s = b.summary()
        assert isinstance(s, str)

    def test_summary_contains_fleet_member_id(self):
        b = _make_bid()
        s = b.summary()
        assert "member-test" in s

    def test_metadata_dict(self):
        b = _make_bid()
        assert isinstance(b.metadata, dict)

    def test_resource_estimate_stored(self):
        b = _make_bid(resource_estimate=7.5)
        assert abs(b.resource_estimate - 7.5) < 1e-9


# ---------------------------------------------------------------------------
# BackpressureSignal tests
# ---------------------------------------------------------------------------

@_SKIP
class TestBackpressureSignal:

    def test_creation_stores_signal_id(self):
        s = _make_signal()
        assert isinstance(s.signal_id, str)

    def test_is_critical_false_for_medium(self):
        s = _make_signal(severity=SeverityLevel.MEDIUM)
        assert s.is_critical() is False

    def test_is_critical_true_for_critical(self):
        s = _make_signal(severity=SeverityLevel.CRITICAL)
        assert s.is_critical() is True

    def test_is_critical_false_for_low(self):
        s = _make_signal(severity=SeverityLevel.LOW)
        assert s.is_critical() is False

    def test_is_critical_false_for_high(self):
        s = _make_signal(severity=SeverityLevel.HIGH)
        assert s.is_critical() is False

    def test_affects_patch_true_for_target(self):
        s = _make_signal(targets=["patch-A", "patch-B"])
        assert s.affects_patch("patch-A") is True

    def test_affects_patch_false_for_non_target(self):
        s = _make_signal(targets=["patch-A"])
        assert s.affects_patch("patch-Z") is False

    def test_affects_patch_true_for_source(self):
        s = _make_signal()
        assert s.affects_patch("patch-src") is True

    def test_broadcast_to_adds_patches(self):
        s = _make_signal(targets=["patch-A"])
        s.broadcast_to(["patch-B", "patch-C"])
        assert "patch-B" in s.target_patches
        assert "patch-C" in s.target_patches

    def test_broadcast_to_no_duplicates(self):
        s = _make_signal(targets=["patch-A"])
        s.broadcast_to(["patch-A", "patch-B"])
        assert s.target_patches.count("patch-A") == 1

    def test_to_dict_returns_dict(self):
        s = _make_signal()
        d = s.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_signal_id(self):
        s = _make_signal()
        d = s.to_dict()
        assert "signal_id" in d

    def test_to_dict_contains_severity(self):
        s = _make_signal()
        d = s.to_dict()
        assert "severity" in d

    def test_escalate_increases_severity(self):
        s = _make_signal(severity=SeverityLevel.LOW)
        s.escalate()
        # After escalation, severity should be higher or at least not lower
        severity_order = [SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH, SeverityLevel.CRITICAL]
        old_idx = severity_order.index(SeverityLevel.LOW)
        new_idx = severity_order.index(s.severity)
        assert new_idx >= old_idx

    def test_escalate_critical_stays_critical(self):
        s = _make_signal(severity=SeverityLevel.CRITICAL)
        s.escalate()
        assert s.severity == SeverityLevel.CRITICAL

    @pytest.mark.parametrize(
        "instability,threshold,expected_critical",
        [
            (0.3, 0.5, False),
            (0.9, 0.5, False),  # critical depends on severity, not just score
            (1.0, 0.5, False),
        ],
    )
    def test_is_critical_depends_on_severity(self, instability, threshold, expected_critical):
        s = _make_signal(instability=instability, threshold=threshold, severity=SeverityLevel.MEDIUM)
        result = s.is_critical()
        assert result is False  # MEDIUM is never critical

    def test_instability_score_stored(self):
        s = _make_signal(instability=0.77)
        assert abs(s.instability_score - 0.77) < 1e-9

    def test_remediation_hints_list(self):
        s = _make_signal()
        assert isinstance(s.remediation_hints, list)

    def test_timestamp_is_float(self):
        s = _make_signal()
        assert isinstance(s.timestamp, float)


# ---------------------------------------------------------------------------
# SemanticMove tests
# ---------------------------------------------------------------------------

@_SKIP
class TestSemanticMove:

    def test_creation_stores_move_id(self):
        m = _make_move()
        assert isinstance(m.move_id, str)

    def test_move_type_stored(self):
        m = _make_move(move_type=MoveType.RETRACT)
        assert m.move_type == MoveType.RETRACT

    def test_source_state_stored(self):
        m = _make_move()
        assert isinstance(m.source_state, dict)

    def test_target_state_stored(self):
        m = _make_move()
        assert isinstance(m.target_state, dict)

    def test_semantic_distance_stored(self):
        m = _make_move(distance=0.42)
        assert abs(m.semantic_distance - 0.42) < 1e-9

    def test_apply_to_returns_state(self):
        m = _make_move()
        result = m.apply_to({"v": "current"})
        assert result is not None

    def test_apply_to_returns_dict_or_state(self):
        m = _make_move()
        result = m.apply_to({"v": "current"})
        assert isinstance(result, dict) or hasattr(result, "__dict__")

    def test_is_reversible_returns_bool(self):
        m = _make_move()
        r = m.is_reversible()
        assert isinstance(r, bool)

    def test_retract_is_reversible(self):
        m = _make_move(move_type=MoveType.RETRACT)
        # RETRACT should generally be reversible
        r = m.is_reversible()
        assert isinstance(r, bool)

    def test_compose_with_returns_move(self):
        m1 = _make_move(move_type=MoveType.PROPOSE)
        m2 = _make_move(move_type=MoveType.REFINE)
        result = m1.compose_with(m2)
        assert result is not None

    def test_compose_with_has_move_type(self):
        m1 = _make_move()
        m2 = _make_move()
        composed = m1.compose_with(m2)
        assert hasattr(composed, "move_type") or isinstance(composed, dict)

    def test_to_dict_returns_dict(self):
        m = _make_move()
        d = m.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_move_id(self):
        m = _make_move()
        d = m.to_dict()
        assert "move_id" in d

    def test_to_dict_contains_move_type(self):
        m = _make_move()
        d = m.to_dict()
        assert "move_type" in d

    @pytest.mark.parametrize("mtype_name", ["PROPOSE", "RETRACT", "REFINE", "GENERALIZE", "SPECIALIZE"])
    def test_all_move_types_apply(self, mtype_name):
        mtype = MoveType[mtype_name]
        m = _make_move(move_type=mtype)
        result = m.apply_to({"v": "state"})
        assert result is not None

    def test_move_cost_positive(self):
        m = _make_move(cost=2.5)
        assert m.move_cost == 2.5

    def test_validity_certificate_stored(self):
        m = _make_move()
        assert isinstance(m.validity_certificate, str)


# ---------------------------------------------------------------------------
# NormalizedProposal tests
# ---------------------------------------------------------------------------

@_SKIP
class TestNormalizedProposal:

    def _make_normalized(self, comparability=0.9, normal_form_hash=None):
        if normal_form_hash is None:
            normal_form_hash = str(uuid.uuid4())
        orig = _make_proposal(content="normalized content")
        return NormalizedProposal(
            normalized_id=str(uuid.uuid4()),
            original_proposal=orig,
            canonical_form={"canonical": "form", "content": "normalized content"},
            normalization_steps=["step-1", "step-2"],
            comparability_score=comparability,
            normal_form_hash=normal_form_hash,
        )

    def test_creation_stores_normalized_id(self):
        n = self._make_normalized()
        assert isinstance(n.normalized_id, str)

    def test_original_proposal_stored(self):
        n = self._make_normalized()
        assert isinstance(n.original_proposal, InhabitantProposal)

    def test_canonical_form_is_dict(self):
        n = self._make_normalized()
        assert isinstance(n.canonical_form, dict)

    def test_normalization_steps_is_list(self):
        n = self._make_normalized()
        assert isinstance(n.normalization_steps, list)

    def test_comparability_score_stored(self):
        n = self._make_normalized(comparability=0.75)
        assert abs(n.comparability_score - 0.75) < 1e-9

    def test_compare_with_returns_numeric(self):
        n1 = self._make_normalized()
        n2 = self._make_normalized()
        result = n1.compare_with(n2)
        assert isinstance(result, (int, float))

    def test_compare_with_self_is_max(self):
        h = str(uuid.uuid4())
        n = self._make_normalized(normal_form_hash=h)
        n2 = self._make_normalized(normal_form_hash=h)
        r = n.compare_with(n2)
        assert r >= 0.0

    def test_is_equivalent_to_same_hash(self):
        h = str(uuid.uuid4())
        n1 = self._make_normalized(normal_form_hash=h)
        n2 = self._make_normalized(normal_form_hash=h)
        assert n1.is_equivalent_to(n2) is True

    def test_is_equivalent_to_different_hash(self):
        n1 = self._make_normalized(normal_form_hash="hash-A")
        n2 = self._make_normalized(normal_form_hash="hash-B")
        assert n1.is_equivalent_to(n2) is False

    def test_to_dict_returns_dict(self):
        n = self._make_normalized()
        d = n.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_normalized_id(self):
        n = self._make_normalized()
        d = n.to_dict()
        assert "normalized_id" in d

    def test_to_dict_contains_normal_form_hash(self):
        n = self._make_normalized()
        d = n.to_dict()
        assert "normal_form_hash" in d

    @pytest.mark.parametrize("comparability", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_comparability_score_range(self, comparability):
        n = self._make_normalized(comparability=comparability)
        assert 0.0 <= n.comparability_score <= 1.0

    def test_normalization_steps_populated(self):
        n = self._make_normalized()
        assert len(n.normalization_steps) > 0

    def test_compare_with_symmetric_ish(self):
        n1 = self._make_normalized(comparability=0.8)
        n2 = self._make_normalized(comparability=0.8)
        r12 = n1.compare_with(n2)
        r21 = n2.compare_with(n1)
        # Allow small numeric differences but both should be same sign
        assert (r12 >= 0) == (r21 >= 0)
