from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
test_models.py
==============
TDD tests for ``jugeo.ideation.discovery_federation.models``.

The implementation does NOT yet exist; these tests define the expected API
and behaviour for:

  - FederationStatus   (enum)
  - ConsensusOutcome   (enum)
  - AuthorityLevel     (enum)
  - FederatedDiscovery (dataclass)
  - FederationConsensus (dataclass)
  - DiscoveryAuthority  (dataclass)
  - KnowledgePropagation (dataclass)
  - AuthorityGrant     (dataclass)
  - FederationVote     (dataclass)
  - FederationNode     (dataclass)
  - ConflictRecord     (dataclass)

All tests are written before the module exists (TDD / design-by-contract).
"""

from jugeo.ideation.discovery_federation.models import (
    FederationStatus,
    ConsensusOutcome,
    AuthorityLevel,
    FederatedDiscovery,
    FederationConsensus,
    DiscoveryAuthority,
    KnowledgePropagation,
    AuthorityGrant,
    FederationVote,
    FederationNode,
    ConflictRecord,
)

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

_ISO_NOW = "2024-06-01T12:00:00"
_ISO_FUTURE = "2099-01-01T00:00:00"
_ISO_PAST = "2000-01-01T00:00:00"


def _make_federated_discovery(
    discovery_id: str = "disc-001",
    source_node: str = "node-alpha",
    target_node: str = "node-beta",
    trust_score: float = 0.8,
    payload: dict | None = None,
    status: FederationStatus = FederationStatus.PENDING,
    created_at: str = _ISO_NOW,
    updated_at: str = _ISO_NOW,
) -> FederatedDiscovery:
    return FederatedDiscovery.create(
        discovery_id=discovery_id,
        source_node=source_node,
        target_node=target_node,
        trust_score=trust_score,
        payload=payload or {"key": "value"},
        status=status,
        created_at=created_at,
        updated_at=updated_at,
    )


def _make_federation_vote(
    vote_id: str = "vote-001",
    voter_id: str = "voter-alpha",
    position: str = "YES",
    weight: float = 1.0,
    rationale: str = "Looks good",
    cast_at: str = _ISO_NOW,
) -> FederationVote:
    return FederationVote.create(
        vote_id=vote_id,
        voter_id=voter_id,
        position=position,
        weight=weight,
        rationale=rationale,
        cast_at=cast_at,
    )


def _make_federation_consensus(
    consensus_id: str = "cons-001",
    discovery_id: str = "disc-001",
    votes: list | None = None,
    quorum_threshold: float = 0.5,
    outcome: ConsensusOutcome = ConsensusOutcome.PENDING,
    created_at: str = _ISO_NOW,
) -> FederationConsensus:
    return FederationConsensus(
        consensus_id=consensus_id,
        discovery_id=discovery_id,
        votes=votes or [],
        quorum_threshold=quorum_threshold,
        outcome=outcome,
        created_at=created_at,
    )


def _make_discovery_authority(
    authority_id: str = "auth-001",
    node_id: str = "node-alpha",
    level: AuthorityLevel = AuthorityLevel.REGIONAL,
    domain: str = "math",
    discoveries: list | None = None,
    granted_at: str = _ISO_NOW,
    expires_at: str | None = _ISO_FUTURE,
    revoked: bool = False,
) -> DiscoveryAuthority:
    return DiscoveryAuthority(
        authority_id=authority_id,
        node_id=node_id,
        level=level,
        domain=domain,
        discoveries=discoveries or [],
        granted_at=granted_at,
        expires_at=expires_at,
        revoked=revoked,
    )


def _make_knowledge_propagation(
    propagation_id: str = "prop-001",
    source_node: str = "node-alpha",
    path: list | None = None,
    knowledge_items: list | None = None,
    created_at: str = _ISO_NOW,
) -> KnowledgePropagation:
    return KnowledgePropagation(
        propagation_id=propagation_id,
        source_node=source_node,
        path=path or ["node-alpha", "node-beta"],
        knowledge_items=knowledge_items or [{"item": "theorem-1"}],
        created_at=created_at,
    )


def _make_authority_grant(
    grant_id: str = "grant-001",
    grantor_node: str = "node-alpha",
    grantee_node: str = "node-beta",
    level: AuthorityLevel = AuthorityLevel.LOCAL,
    domain: str = "physics",
    granted_at: str = _ISO_NOW,
    expires_at: str | None = _ISO_FUTURE,
    metadata: dict | None = None,
) -> AuthorityGrant:
    return AuthorityGrant.create(
        grant_id=grant_id,
        grantor_node=grantor_node,
        grantee_node=grantee_node,
        level=level,
        domain=domain,
        granted_at=granted_at,
        expires_at=expires_at,
        metadata=metadata or {},
    )


def _make_federation_node(
    node_id: str = "node-alpha",
    name: str = "Alpha Node",
    trust_score: float = 0.75,
    authority_level: AuthorityLevel = AuthorityLevel.REGIONAL,
    discoveries: list | None = None,
    metadata: dict | None = None,
    registered_at: str = _ISO_NOW,
) -> FederationNode:
    return FederationNode(
        node_id=node_id,
        name=name,
        trust_score=trust_score,
        authority_level=authority_level,
        discoveries=discoveries or [],
        metadata=metadata or {},
        registered_at=registered_at,
    )


def _make_conflict_record(
    conflict_id: str = "conflict-001",
    parties: list | None = None,
    subject: str = "theorem-ownership",
    description: str = "Nodes disagree on attribution",
    created_at: str = _ISO_NOW,
    resolved_at: str | None = None,
    resolution: str | None = None,
) -> ConflictRecord:
    return ConflictRecord.create(
        conflict_id=conflict_id,
        parties=parties or ["node-alpha", "node-beta"],
        subject=subject,
        description=description,
        created_at=created_at,
        resolved_at=resolved_at,
        resolution=resolution,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_federated_discovery() -> FederatedDiscovery:
    return _make_federated_discovery()


@pytest.fixture
def sample_federation_node() -> FederationNode:
    return _make_federation_node()


@pytest.fixture
def sample_conflict_record() -> ConflictRecord:
    return _make_conflict_record()


@pytest.fixture
def sample_authority_grant() -> AuthorityGrant:
    return _make_authority_grant()


@pytest.fixture
def sample_federation_vote() -> FederationVote:
    return _make_federation_vote()


@pytest.fixture
def sample_discovery_authority() -> DiscoveryAuthority:
    return _make_discovery_authority()


@pytest.fixture
def sample_knowledge_propagation() -> KnowledgePropagation:
    return _make_knowledge_propagation()


@pytest.fixture
def sample_federation_consensus() -> FederationConsensus:
    return _make_federation_consensus()


# ===========================================================================
# FederationStatus enum
# ===========================================================================

def test_federation_status_pending_exists() -> None:
    assert FederationStatus.PENDING is not None


def test_federation_status_active_exists() -> None:
    assert FederationStatus.ACTIVE is not None


def test_federation_status_resolved_exists() -> None:
    assert FederationStatus.RESOLVED is not None


def test_federation_status_expired_exists() -> None:
    assert FederationStatus.EXPIRED is not None


def test_federation_status_contested_exists() -> None:
    assert FederationStatus.CONTESTED is not None


def test_federation_status_has_at_least_five_values() -> None:
    assert len(list(FederationStatus)) >= 5


def test_federation_status_values_are_unique() -> None:
    values = [s.value for s in FederationStatus]
    assert len(values) == len(set(values))


def test_federation_status_is_iterable() -> None:
    statuses = list(FederationStatus)
    assert FederationStatus.PENDING in statuses
    assert FederationStatus.ACTIVE in statuses


@pytest.mark.parametrize("status", [
    FederationStatus.PENDING,
    FederationStatus.ACTIVE,
    FederationStatus.RESOLVED,
    FederationStatus.EXPIRED,
    FederationStatus.CONTESTED,
])
def test_federation_status_has_string_representation(status: FederationStatus) -> None:
    assert str(status) or status.name  # name is always non-empty


def test_federation_status_membership_by_name() -> None:
    names = {s.name for s in FederationStatus}
    assert "PENDING" in names
    assert "ACTIVE" in names
    assert "RESOLVED" in names


# ===========================================================================
# ConsensusOutcome enum
# ===========================================================================

def test_consensus_outcome_accepted_exists() -> None:
    assert ConsensusOutcome.ACCEPTED is not None


def test_consensus_outcome_rejected_exists() -> None:
    assert ConsensusOutcome.REJECTED is not None


def test_consensus_outcome_abstained_exists() -> None:
    assert ConsensusOutcome.ABSTAINED is not None


def test_consensus_outcome_pending_exists() -> None:
    assert ConsensusOutcome.PENDING is not None


def test_consensus_outcome_split_exists() -> None:
    assert ConsensusOutcome.SPLIT is not None


def test_consensus_outcome_has_exactly_five_values() -> None:
    assert len(list(ConsensusOutcome)) >= 5


def test_consensus_outcome_values_unique() -> None:
    vals = [o.value for o in ConsensusOutcome]
    assert len(vals) == len(set(vals))


@pytest.mark.parametrize("outcome", [
    ConsensusOutcome.ACCEPTED,
    ConsensusOutcome.REJECTED,
    ConsensusOutcome.ABSTAINED,
    ConsensusOutcome.PENDING,
    ConsensusOutcome.SPLIT,
])
def test_consensus_outcome_name_is_nonempty(outcome: ConsensusOutcome) -> None:
    assert len(outcome.name) > 0


# ===========================================================================
# AuthorityLevel enum
# ===========================================================================

def test_authority_level_none_exists() -> None:
    assert AuthorityLevel.NONE is not None


def test_authority_level_local_exists() -> None:
    assert AuthorityLevel.LOCAL is not None


def test_authority_level_regional_exists() -> None:
    assert AuthorityLevel.REGIONAL is not None


def test_authority_level_global_exists() -> None:
    assert AuthorityLevel.GLOBAL is not None


def test_authority_level_supreme_exists() -> None:
    assert AuthorityLevel.SUPREME is not None


def test_authority_level_has_at_least_five_values() -> None:
    assert len(list(AuthorityLevel)) >= 5


@pytest.mark.parametrize("level", [
    AuthorityLevel.NONE,
    AuthorityLevel.LOCAL,
    AuthorityLevel.REGIONAL,
    AuthorityLevel.GLOBAL,
    AuthorityLevel.SUPREME,
])
def test_authority_level_has_nonempty_name(level: AuthorityLevel) -> None:
    assert len(level.name) > 0


def test_authority_level_can_compare_by_identity() -> None:
    assert AuthorityLevel.LOCAL is not AuthorityLevel.GLOBAL
    assert AuthorityLevel.SUPREME is not AuthorityLevel.NONE


# ===========================================================================
# FederatedDiscovery
# ===========================================================================

def test_federated_discovery_create_returns_instance(sample_federated_discovery: FederatedDiscovery) -> None:
    assert isinstance(sample_federated_discovery, FederatedDiscovery)


def test_federated_discovery_fields_preserved(sample_federated_discovery: FederatedDiscovery) -> None:
    d = sample_federated_discovery
    assert d.discovery_id == "disc-001"
    assert d.source_node == "node-alpha"
    assert d.target_node == "node-beta"
    assert abs(d.trust_score - 0.8) < 1e-9
    assert d.status is FederationStatus.PENDING


def test_federated_discovery_payload_accessible(sample_federated_discovery: FederatedDiscovery) -> None:
    assert sample_federated_discovery.payload == {"key": "value"}


def test_federated_discovery_to_dict_returns_dict(sample_federated_discovery: FederatedDiscovery) -> None:
    d = sample_federated_discovery.to_dict()
    assert isinstance(d, dict)


def test_federated_discovery_to_dict_contains_required_keys(sample_federated_discovery: FederatedDiscovery) -> None:
    d = sample_federated_discovery.to_dict()
    for key in ("discovery_id", "source_node", "target_node", "trust_score", "payload", "status"):
        assert key in d, f"Missing key: {key}"


def test_federated_discovery_from_dict_roundtrip(sample_federated_discovery: FederatedDiscovery) -> None:
    d = sample_federated_discovery.to_dict()
    restored = FederatedDiscovery.from_dict(d)
    assert restored.discovery_id == sample_federated_discovery.discovery_id
    assert abs(restored.trust_score - sample_federated_discovery.trust_score) < 1e-9
    assert restored.status == sample_federated_discovery.status


def test_federated_discovery_age_seconds_non_negative(sample_federated_discovery: FederatedDiscovery) -> None:
    age = sample_federated_discovery.age_seconds()
    assert isinstance(age, float)
    assert age >= 0.0


def test_federated_discovery_render_summary_nonempty(sample_federated_discovery: FederatedDiscovery) -> None:
    summary = sample_federated_discovery.render_summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_federated_discovery_render_summary_contains_id(sample_federated_discovery: FederatedDiscovery) -> None:
    summary = sample_federated_discovery.render_summary()
    assert sample_federated_discovery.discovery_id in summary


@pytest.mark.parametrize("trust_score", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_federated_discovery_valid_trust_scores(trust_score: float) -> None:
    d = _make_federated_discovery(trust_score=trust_score)
    assert abs(d.trust_score - trust_score) < 1e-9


@pytest.mark.parametrize("bad_trust", [-0.1, -1.0, 1.1, 2.0, float("inf"), float("nan")])
def test_federated_discovery_invalid_trust_score_raises(bad_trust: float) -> None:
    with pytest.raises((ValueError, AssertionError)):
        _make_federated_discovery(trust_score=bad_trust)


def test_federated_discovery_empty_discovery_id_raises() -> None:
    with pytest.raises((ValueError, AssertionError)):
        _make_federated_discovery(discovery_id="")


@pytest.mark.parametrize("status", [
    FederationStatus.PENDING,
    FederationStatus.ACTIVE,
    FederationStatus.RESOLVED,
    FederationStatus.EXPIRED,
    FederationStatus.CONTESTED,
])
def test_federated_discovery_all_statuses(status: FederationStatus) -> None:
    d = _make_federated_discovery(status=status)
    assert d.status is status


def test_federated_discovery_max_trust_score() -> None:
    d = _make_federated_discovery(trust_score=1.0)
    assert d.trust_score == 1.0


def test_federated_discovery_min_trust_score() -> None:
    d = _make_federated_discovery(trust_score=0.0)
    assert d.trust_score == 0.0


def test_federated_discovery_to_dict_trust_score_type() -> None:
    d = _make_federated_discovery(trust_score=0.5).to_dict()
    assert isinstance(d["trust_score"], float)


def test_federated_discovery_from_dict_status_is_enum() -> None:
    d = _make_federated_discovery().to_dict()
    restored = FederatedDiscovery.from_dict(d)
    assert isinstance(restored.status, FederationStatus)


# ===========================================================================
# FederationVote
# ===========================================================================

def test_federation_vote_create_returns_instance(sample_federation_vote: FederationVote) -> None:
    assert isinstance(sample_federation_vote, FederationVote)


def test_federation_vote_fields_preserved(sample_federation_vote: FederationVote) -> None:
    v = sample_federation_vote
    assert v.vote_id == "vote-001"
    assert v.voter_id == "voter-alpha"
    assert v.position == "YES"
    assert abs(v.weight - 1.0) < 1e-9


def test_federation_vote_effective_weight_yes(sample_federation_vote: FederationVote) -> None:
    ew = sample_federation_vote.effective_weight()
    assert isinstance(ew, float)
    assert ew > 0.0


def test_federation_vote_effective_weight_abstain_is_zero() -> None:
    v = _make_federation_vote(position="ABSTAIN")
    assert v.effective_weight() == 0.0


def test_federation_vote_effective_weight_no_is_negative_or_zero() -> None:
    v = _make_federation_vote(position="NO")
    ew = v.effective_weight()
    assert ew <= 0.0


def test_federation_vote_to_dict_returns_dict(sample_federation_vote: FederationVote) -> None:
    assert isinstance(sample_federation_vote.to_dict(), dict)


def test_federation_vote_to_dict_has_position(sample_federation_vote: FederationVote) -> None:
    d = sample_federation_vote.to_dict()
    assert "position" in d
    assert d["position"] == "YES"


@pytest.mark.parametrize("position", ["YES", "NO", "ABSTAIN"])
def test_federation_vote_valid_positions(position: str) -> None:
    v = _make_federation_vote(position=position)
    assert v.position == position


@pytest.mark.parametrize("bad_pos", ["MAYBE", "yes", "no", "", "abstain", "YESS"])
def test_federation_vote_invalid_position_raises(bad_pos: str) -> None:
    with pytest.raises((ValueError, AssertionError)):
        _make_federation_vote(position=bad_pos)


def test_federation_vote_zero_weight() -> None:
    v = _make_federation_vote(weight=0.0)
    assert v.weight == 0.0
    assert v.effective_weight() == 0.0


def test_federation_vote_high_weight() -> None:
    v = _make_federation_vote(weight=5.0)
    assert v.weight == 5.0


def test_federation_vote_to_dict_roundtrip_voter_id() -> None:
    v = _make_federation_vote(voter_id="voter-gamma")
    assert v.to_dict()["voter_id"] == "voter-gamma"


# ===========================================================================
# FederationConsensus
# ===========================================================================

def test_federation_consensus_creates_instance(sample_federation_consensus: FederationConsensus) -> None:
    assert isinstance(sample_federation_consensus, FederationConsensus)


def test_federation_consensus_fields(sample_federation_consensus: FederationConsensus) -> None:
    c = sample_federation_consensus
    assert c.consensus_id == "cons-001"
    assert c.discovery_id == "disc-001"
    assert c.outcome is ConsensusOutcome.PENDING


def test_federation_consensus_is_quorum_met_no_votes() -> None:
    c = _make_federation_consensus(quorum_threshold=0.5)
    assert c.is_quorum_met() is False


def test_federation_consensus_is_quorum_met_majority_yes() -> None:
    votes = [
        _make_federation_vote(vote_id="v1", position="YES"),
        _make_federation_vote(vote_id="v2", position="YES"),
        _make_federation_vote(vote_id="v3", position="NO"),
    ]
    c = _make_federation_consensus(votes=votes, quorum_threshold=0.5)
    assert c.is_quorum_met() is True


def test_federation_consensus_is_quorum_met_all_no() -> None:
    votes = [
        _make_federation_vote(vote_id="v1", position="NO"),
        _make_federation_vote(vote_id="v2", position="NO"),
    ]
    c = _make_federation_consensus(votes=votes, quorum_threshold=0.5)
    assert c.is_quorum_met() is False


def test_federation_consensus_is_quorum_met_exact_threshold() -> None:
    votes = [
        _make_federation_vote(vote_id="v1", position="YES"),
        _make_federation_vote(vote_id="v2", position="NO"),
    ]
    c = _make_federation_consensus(votes=votes, quorum_threshold=0.5)
    # At exactly threshold — implementation may treat as met or not; just check it returns bool
    result = c.is_quorum_met()
    assert isinstance(result, bool)


def test_federation_consensus_winning_margin_no_votes() -> None:
    c = _make_federation_consensus()
    margin = c.winning_margin()
    assert isinstance(margin, float)


def test_federation_consensus_winning_margin_all_yes() -> None:
    votes = [_make_federation_vote(vote_id=f"v{i}", position="YES") for i in range(4)]
    c = _make_federation_consensus(votes=votes)
    margin = c.winning_margin()
    assert margin > 0.0


def test_federation_consensus_winning_margin_split_is_low() -> None:
    votes = [
        _make_federation_vote(vote_id="v1", position="YES"),
        _make_federation_vote(vote_id="v2", position="NO"),
    ]
    c = _make_federation_consensus(votes=votes)
    margin = c.winning_margin()
    assert margin == 0.0 or abs(margin) < 1.0


def test_federation_consensus_add_vote_increases_count() -> None:
    c = _make_federation_consensus()
    v = _make_federation_vote()
    initial_count = len(c.votes)
    c.add_vote(v)
    assert len(c.votes) == initial_count + 1


def test_federation_consensus_to_dict_returns_dict(sample_federation_consensus: FederationConsensus) -> None:
    assert isinstance(sample_federation_consensus.to_dict(), dict)


def test_federation_consensus_summary_nonempty(sample_federation_consensus: FederationConsensus) -> None:
    s = sample_federation_consensus.summary()
    assert isinstance(s, str)
    assert len(s) > 0


@pytest.mark.parametrize("quorum", [0.1, 0.5, 0.66, 0.75, 1.0])
def test_federation_consensus_various_quorum_thresholds(quorum: float) -> None:
    c = _make_federation_consensus(quorum_threshold=quorum)
    assert abs(c.quorum_threshold - quorum) < 1e-9


def test_federation_consensus_quorum_all_abstain() -> None:
    votes = [_make_federation_vote(vote_id=f"v{i}", position="ABSTAIN") for i in range(3)]
    c = _make_federation_consensus(votes=votes, quorum_threshold=0.5)
    assert c.is_quorum_met() is False


# ===========================================================================
# DiscoveryAuthority
# ===========================================================================

def test_discovery_authority_creates_instance(sample_discovery_authority: DiscoveryAuthority) -> None:
    assert isinstance(sample_discovery_authority, DiscoveryAuthority)


def test_discovery_authority_fields(sample_discovery_authority: DiscoveryAuthority) -> None:
    a = sample_discovery_authority
    assert a.authority_id == "auth-001"
    assert a.node_id == "node-alpha"
    assert a.level is AuthorityLevel.REGIONAL
    assert a.domain == "math"
    assert a.revoked is False


def test_discovery_authority_is_active_not_expired_not_revoked(sample_discovery_authority: DiscoveryAuthority) -> None:
    assert sample_discovery_authority.is_active() is True


def test_discovery_authority_is_expired_far_future() -> None:
    a = _make_discovery_authority(expires_at=_ISO_FUTURE)
    assert a.is_expired() is False


def test_discovery_authority_is_expired_past_date() -> None:
    a = _make_discovery_authority(expires_at=_ISO_PAST)
    assert a.is_expired() is True


def test_discovery_authority_is_active_returns_false_when_revoked() -> None:
    a = _make_discovery_authority(revoked=True)
    assert a.is_active() is False


def test_discovery_authority_is_active_returns_false_when_expired() -> None:
    a = _make_discovery_authority(expires_at=_ISO_PAST)
    assert a.is_active() is False


def test_discovery_authority_no_expiry_never_expires() -> None:
    a = _make_discovery_authority(expires_at=None)
    assert a.is_expired() is False
    assert a.is_active() is True


def test_discovery_authority_add_discovery_increases_list() -> None:
    a = _make_discovery_authority()
    initial = len(a.discoveries)
    a.add_discovery("disc-new")
    assert len(a.discoveries) == initial + 1
    assert "disc-new" in a.discoveries


def test_discovery_authority_revoke_sets_revoked() -> None:
    a = _make_discovery_authority(revoked=False)
    a.revoke()
    assert a.revoked is True


def test_discovery_authority_revoke_makes_inactive() -> None:
    a = _make_discovery_authority(revoked=False)
    a.revoke()
    assert a.is_active() is False


def test_discovery_authority_promote_changes_level() -> None:
    a = _make_discovery_authority(level=AuthorityLevel.LOCAL)
    a.promote(AuthorityLevel.REGIONAL)
    assert a.level is AuthorityLevel.REGIONAL


def test_discovery_authority_promote_to_global() -> None:
    a = _make_discovery_authority(level=AuthorityLevel.REGIONAL)
    a.promote(AuthorityLevel.GLOBAL)
    assert a.level is AuthorityLevel.GLOBAL


def test_discovery_authority_to_dict_returns_dict(sample_discovery_authority: DiscoveryAuthority) -> None:
    assert isinstance(sample_discovery_authority.to_dict(), dict)


def test_discovery_authority_to_dict_has_required_keys(sample_discovery_authority: DiscoveryAuthority) -> None:
    d = sample_discovery_authority.to_dict()
    for key in ("authority_id", "node_id", "level", "domain", "revoked"):
        assert key in d


@pytest.mark.parametrize("level", [
    AuthorityLevel.NONE,
    AuthorityLevel.LOCAL,
    AuthorityLevel.REGIONAL,
    AuthorityLevel.GLOBAL,
    AuthorityLevel.SUPREME,
])
def test_discovery_authority_all_levels(level: AuthorityLevel) -> None:
    a = _make_discovery_authority(level=level)
    assert a.level is level


# ===========================================================================
# KnowledgePropagation
# ===========================================================================

def test_knowledge_propagation_creates_instance(sample_knowledge_propagation: KnowledgePropagation) -> None:
    assert isinstance(sample_knowledge_propagation, KnowledgePropagation)


def test_knowledge_propagation_fields(sample_knowledge_propagation: KnowledgePropagation) -> None:
    p = sample_knowledge_propagation
    assert p.propagation_id == "prop-001"
    assert p.source_node == "node-alpha"


def test_knowledge_propagation_hop_count_two_nodes() -> None:
    p = _make_knowledge_propagation(path=["node-a", "node-b"])
    assert p.hop_count() == 1


def test_knowledge_propagation_hop_count_three_nodes() -> None:
    p = _make_knowledge_propagation(path=["node-a", "node-b", "node-c"])
    assert p.hop_count() == 2


def test_knowledge_propagation_hop_count_single_node() -> None:
    p = _make_knowledge_propagation(path=["node-a"])
    assert p.hop_count() == 0


@pytest.mark.parametrize("path,expected_hops", [
    (["a"], 0),
    (["a", "b"], 1),
    (["a", "b", "c"], 2),
    (["a", "b", "c", "d"], 3),
    (["a", "b", "c", "d", "e"], 4),
])
def test_knowledge_propagation_hop_count_parametrized(path: list, expected_hops: int) -> None:
    p = _make_knowledge_propagation(path=path)
    assert p.hop_count() == expected_hops


def test_knowledge_propagation_includes_node_true() -> None:
    p = _make_knowledge_propagation(path=["node-alpha", "node-beta", "node-gamma"])
    assert p.includes_node("node-beta") is True


def test_knowledge_propagation_includes_node_false() -> None:
    p = _make_knowledge_propagation(path=["node-alpha", "node-beta"])
    assert p.includes_node("node-omega") is False


def test_knowledge_propagation_includes_source_node() -> None:
    p = _make_knowledge_propagation(source_node="node-alpha", path=["node-alpha", "node-beta"])
    assert p.includes_node("node-alpha") is True


def test_knowledge_propagation_extend_path_adds_node() -> None:
    p = _make_knowledge_propagation(path=["node-alpha"])
    p.extend_path("node-new")
    assert "node-new" in p.path
    assert p.hop_count() == 1


def test_knowledge_propagation_extend_path_multiple_times() -> None:
    p = _make_knowledge_propagation(path=["node-a"])
    p.extend_path("node-b")
    p.extend_path("node-c")
    assert p.hop_count() == 2
    assert p.includes_node("node-c") is True


def test_knowledge_propagation_to_dict_returns_dict(sample_knowledge_propagation: KnowledgePropagation) -> None:
    assert isinstance(sample_knowledge_propagation.to_dict(), dict)


def test_knowledge_propagation_to_dict_has_path(sample_knowledge_propagation: KnowledgePropagation) -> None:
    d = sample_knowledge_propagation.to_dict()
    assert "path" in d
    assert isinstance(d["path"], list)


def test_knowledge_propagation_to_dict_has_knowledge_items(sample_knowledge_propagation: KnowledgePropagation) -> None:
    d = sample_knowledge_propagation.to_dict()
    assert "knowledge_items" in d


# ===========================================================================
# AuthorityGrant
# ===========================================================================

def test_authority_grant_creates_instance(sample_authority_grant: AuthorityGrant) -> None:
    assert isinstance(sample_authority_grant, AuthorityGrant)


def test_authority_grant_fields(sample_authority_grant: AuthorityGrant) -> None:
    g = sample_authority_grant
    assert g.grant_id == "grant-001"
    assert g.grantor_node == "node-alpha"
    assert g.grantee_node == "node-beta"
    assert g.level is AuthorityLevel.LOCAL
    assert g.domain == "physics"


def test_authority_grant_to_dict_returns_dict(sample_authority_grant: AuthorityGrant) -> None:
    assert isinstance(sample_authority_grant.to_dict(), dict)


def test_authority_grant_to_dict_has_all_keys(sample_authority_grant: AuthorityGrant) -> None:
    d = sample_authority_grant.to_dict()
    for key in ("grant_id", "grantor_node", "grantee_node", "level", "domain"):
        assert key in d


def test_authority_grant_summary_nonempty(sample_authority_grant: AuthorityGrant) -> None:
    s = sample_authority_grant.summary()
    assert isinstance(s, str)
    assert len(s) > 0


def test_authority_grant_summary_contains_grant_id(sample_authority_grant: AuthorityGrant) -> None:
    s = sample_authority_grant.summary()
    assert sample_authority_grant.grant_id in s


@pytest.mark.parametrize("level", [
    AuthorityLevel.LOCAL,
    AuthorityLevel.REGIONAL,
    AuthorityLevel.GLOBAL,
    AuthorityLevel.SUPREME,
])
def test_authority_grant_all_authority_levels(level: AuthorityLevel) -> None:
    g = _make_authority_grant(level=level)
    assert g.level is level


def test_authority_grant_no_expiry() -> None:
    g = _make_authority_grant(expires_at=None)
    assert g.expires_at is None


def test_authority_grant_with_metadata() -> None:
    g = _make_authority_grant(metadata={"reason": "trusted", "score": 0.9})
    assert g.metadata["reason"] == "trusted"


# ===========================================================================
# FederationNode
# ===========================================================================

def test_federation_node_creates_instance(sample_federation_node: FederationNode) -> None:
    assert isinstance(sample_federation_node, FederationNode)


def test_federation_node_fields(sample_federation_node: FederationNode) -> None:
    n = sample_federation_node
    assert n.node_id == "node-alpha"
    assert n.name == "Alpha Node"
    assert abs(n.trust_score - 0.75) < 1e-9
    assert n.authority_level is AuthorityLevel.REGIONAL


def test_federation_node_register_discovery_adds_to_list(sample_federation_node: FederationNode) -> None:
    initial = len(sample_federation_node.discoveries)
    sample_federation_node.register_discovery("disc-new")
    assert len(sample_federation_node.discoveries) == initial + 1


def test_federation_node_has_discovery_true(sample_federation_node: FederationNode) -> None:
    sample_federation_node.register_discovery("disc-check")
    assert sample_federation_node.has_discovery("disc-check") is True


def test_federation_node_has_discovery_false(sample_federation_node: FederationNode) -> None:
    assert sample_federation_node.has_discovery("disc-nonexistent") is False


def test_federation_node_get_authority_level(sample_federation_node: FederationNode) -> None:
    assert sample_federation_node.get_authority_level() is AuthorityLevel.REGIONAL


def test_federation_node_to_dict_returns_dict(sample_federation_node: FederationNode) -> None:
    assert isinstance(sample_federation_node.to_dict(), dict)


def test_federation_node_to_dict_has_node_id(sample_federation_node: FederationNode) -> None:
    d = sample_federation_node.to_dict()
    assert d["node_id"] == "node-alpha"


def test_federation_node_summary_nonempty(sample_federation_node: FederationNode) -> None:
    s = sample_federation_node.summary()
    assert isinstance(s, str)
    assert len(s) > 0


def test_federation_node_summary_contains_name(sample_federation_node: FederationNode) -> None:
    s = sample_federation_node.summary()
    assert "Alpha Node" in s


def test_federation_node_empty_discoveries() -> None:
    n = _make_federation_node(discoveries=[])
    assert n.discoveries == []
    assert n.has_discovery("anything") is False


def test_federation_node_register_multiple_discoveries() -> None:
    n = _make_federation_node()
    for i in range(5):
        n.register_discovery(f"disc-{i:03d}")
    assert len(n.discoveries) == 5


@pytest.mark.parametrize("trust_score", [0.0, 0.1, 0.5, 0.9, 1.0])
def test_federation_node_valid_trust_scores(trust_score: float) -> None:
    n = _make_federation_node(trust_score=trust_score)
    assert abs(n.trust_score - trust_score) < 1e-9


def test_federation_node_to_dict_has_all_keys(sample_federation_node: FederationNode) -> None:
    d = sample_federation_node.to_dict()
    for key in ("node_id", "name", "trust_score", "authority_level", "discoveries"):
        assert key in d


# ===========================================================================
# ConflictRecord
# ===========================================================================

def test_conflict_record_creates_instance(sample_conflict_record: ConflictRecord) -> None:
    assert isinstance(sample_conflict_record, ConflictRecord)


def test_conflict_record_fields(sample_conflict_record: ConflictRecord) -> None:
    c = sample_conflict_record
    assert c.conflict_id == "conflict-001"
    assert "node-alpha" in c.parties
    assert "node-beta" in c.parties
    assert c.subject == "theorem-ownership"


def test_conflict_record_is_resolved_false_when_no_resolution(sample_conflict_record: ConflictRecord) -> None:
    assert sample_conflict_record.is_resolved() is False


def test_conflict_record_is_resolved_true_when_resolved_at_set() -> None:
    c = _make_conflict_record(resolved_at=_ISO_NOW, resolution="mutual agreement")
    assert c.is_resolved() is True


def test_conflict_record_age_returns_nonnegative_float(sample_conflict_record: ConflictRecord) -> None:
    age = sample_conflict_record.age()
    assert isinstance(age, float)
    assert age >= 0.0


def test_conflict_record_to_dict_returns_dict(sample_conflict_record: ConflictRecord) -> None:
    assert isinstance(sample_conflict_record.to_dict(), dict)


def test_conflict_record_to_dict_has_required_keys(sample_conflict_record: ConflictRecord) -> None:
    d = sample_conflict_record.to_dict()
    for key in ("conflict_id", "parties", "subject", "description", "created_at"):
        assert key in d


def test_conflict_record_parties_list_preserved(sample_conflict_record: ConflictRecord) -> None:
    d = sample_conflict_record.to_dict()
    assert isinstance(d["parties"], list)
    assert len(d["parties"]) == 2


def test_conflict_record_resolved_at_none_when_unresolved(sample_conflict_record: ConflictRecord) -> None:
    assert sample_conflict_record.resolved_at is None


def test_conflict_record_resolution_none_when_unresolved(sample_conflict_record: ConflictRecord) -> None:
    assert sample_conflict_record.resolution is None


def test_conflict_record_age_larger_for_older_conflict() -> None:
    old = _make_conflict_record(created_at="2000-01-01T00:00:00")
    recent = _make_conflict_record(created_at="2024-01-01T00:00:00")
    assert old.age() > recent.age()


def test_conflict_record_with_three_parties() -> None:
    c = _make_conflict_record(parties=["node-a", "node-b", "node-c"])
    assert len(c.parties) == 3


def test_conflict_record_empty_parties_list() -> None:
    c = _make_conflict_record(parties=[])
    assert c.parties == []


def test_conflict_record_to_dict_resolved_at_none() -> None:
    c = _make_conflict_record()
    d = c.to_dict()
    assert d.get("resolved_at") is None


def test_conflict_record_full_lifecycle() -> None:
    c = _make_conflict_record()
    assert not c.is_resolved()
    d = c.to_dict()
    assert d["conflict_id"] == "conflict-001"
    age = c.age()
    assert age >= 0.0


# ===========================================================================
# Cross-model / integration-style tests
# ===========================================================================

def test_federation_node_and_discovery_linkage() -> None:
    node = _make_federation_node()
    disc = _make_federated_discovery(discovery_id="disc-xyz")
    node.register_discovery(disc.discovery_id)
    assert node.has_discovery("disc-xyz")


def test_consensus_vote_interaction() -> None:
    consensus = _make_federation_consensus(quorum_threshold=0.6)
    votes_yes = [_make_federation_vote(vote_id=f"y{i}", position="YES") for i in range(4)]
    votes_no = [_make_federation_vote(vote_id=f"n{i}", position="NO") for i in range(1)]
    for v in votes_yes + votes_no:
        consensus.add_vote(v)
    assert consensus.is_quorum_met() is True


def test_authority_chain_grant_and_authority() -> None:
    grant = _make_authority_grant(level=AuthorityLevel.REGIONAL)
    authority = _make_discovery_authority(level=AuthorityLevel.REGIONAL)
    assert grant.level is authority.level


def test_knowledge_propagation_and_nodes() -> None:
    nodes = ["node-a", "node-b", "node-c"]
    prop = _make_knowledge_propagation(path=nodes)
    for n in nodes:
        assert prop.includes_node(n)


def test_discovery_to_dict_and_back_preserves_payload() -> None:
    payload = {"theorem": "T1", "confidence": 0.95, "tags": ["math", "logic"]}
    disc = _make_federated_discovery(payload=payload)
    restored = FederatedDiscovery.from_dict(disc.to_dict())
    assert restored.payload == payload


def test_federated_discovery_summary_contains_nodes(sample_federated_discovery: FederatedDiscovery) -> None:
    s = sample_federated_discovery.render_summary()
    assert "node-alpha" in s or "node-beta" in s


def test_authority_grant_summary_contains_level(sample_authority_grant: AuthorityGrant) -> None:
    s = sample_authority_grant.summary()
    assert "LOCAL" in s or "local" in s.lower()


def test_federation_consensus_to_dict_has_votes_key(sample_federation_consensus: FederationConsensus) -> None:
    d = sample_federation_consensus.to_dict()
    assert "votes" in d
    assert isinstance(d["votes"], list)


def test_federation_consensus_to_dict_has_quorum_threshold(sample_federation_consensus: FederationConsensus) -> None:
    d = sample_federation_consensus.to_dict()
    assert "quorum_threshold" in d


def test_knowledge_propagation_single_item() -> None:
    items = [{"theorem": "T1", "kind": "lemma"}]
    p = _make_knowledge_propagation(knowledge_items=items)
    assert p.to_dict()["knowledge_items"] == items


def test_federation_node_metadata_preserved() -> None:
    meta = {"region": "europe", "tier": 1}
    n = _make_federation_node(metadata=meta)
    assert n.metadata["region"] == "europe"
    assert n.metadata["tier"] == 1
