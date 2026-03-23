from pathlib import Path
import sys
from dataclasses import FrozenInstanceError

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

"""Regression tests for the shared JuGeo trust algebra.

These tests are intentionally broad and explicit because ``trust.py`` is a
shared-foundation module consumed by evidence, runtime, solver, and certificate
layers.  The authoritative doctrine lives in ``preliminaries/theory2.tex`` and
says that trust is an ordered algebra of admissible support, not a hidden score.
For this wave of the codebase that means the tests should keep reasserting the
same semantic promises:

* joins are lawful and conservative;
* support scopes stay explicit and do not silently widen in misleading ways;
* challenge and demotion remain first-class, auditable operations;
* promotion is explicit and can never happen accidentally;
* the tiny public API remains compatible with the rest of the shared code.

The file is deliberately verbose so future maintainers, reviewers, and LLMs can
read it as an executable checklist of the trust doctrine.
"""

import pytest

from jugeo.errors import FailureClassification, FailureScope, JuGeoError
from jugeo.evidence.channels import EvidenceKind, EvidenceRecord, build_channel
from jugeo.evidence.manifests import build_evidence_manifest
from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles


def make_profile(
    tier: TrustTier,
    *scope: str,
    reasons: tuple[str, ...] = (),
) -> TrustProfile:
    return TrustProfile(tier, tuple(scope), reasons)



def make_record(kind: EvidenceKind = EvidenceKind.RUNTIME, *, obligations: tuple[str, ...] = ()) -> EvidenceRecord:
    return EvidenceRecord(build_channel(kind.value, kind), 'claim', obligations=obligations)



def assert_failure(error: JuGeoError, *, code: str, current: TrustTier, target: TrustTier) -> None:
    failure = error.failure
    assert failure.message == 'Trust promotion requires explicit acknowledgement.'
    assert failure.scope is FailureScope.EVIDENCE
    assert failure.classification is FailureClassification.TRUST_VIOLATION
    assert dict(failure.metadata) == {
        'code': code,
        'details': {'from': int(current), 'to': int(target)},
    }
    assert dict(failure.trust) == {
        'from_tier': int(current),
        'to_tier': int(target),
        'rule': 'no-silent-promotion',
    }
    assert failure.trust_boundary == 'trust'


# ---------------------------------------------------------------------------
# Canonicalization and tier helpers
# ---------------------------------------------------------------------------


def test_profile_normalizes_scope_and_reason_inputs() -> None:
    profile = TrustProfile(
        TrustTier.REVIEWED,
        (' beta ', 'alpha', 'alpha', '', 'beta'),
        ('  first ', 'second', 'first', '', 'second'),
    )
    assert profile.support_scope == ('alpha', 'beta')
    assert profile.reasons == ('first', 'second')



def test_tier_helper_methods_capture_ordering() -> None:
    assert TrustTier.PROPOSAL.weaker_than(TrustTier.REVIEWED) is True
    assert TrustTier.REVIEWED.stronger_than(TrustTier.PROPOSAL) is True
    assert TrustTier.VERIFIED.step_weaker() is TrustTier.REVIEWED
    assert TrustTier.REVIEWED.step_weaker() is TrustTier.PROPOSAL
    assert TrustTier.PROPOSAL.step_weaker() is TrustTier.PROPOSAL
    assert TrustTier.PROPOSAL.step_stronger() is TrustTier.REVIEWED
    assert TrustTier.REVIEWED.step_stronger() is TrustTier.VERIFIED
    assert TrustTier.VERIFIED.step_stronger() is TrustTier.VERIFIED
    assert TrustTier.ordered() == (TrustTier.PROPOSAL, TrustTier.REVIEWED, TrustTier.VERIFIED)



def test_profile_to_dict_is_serialization_friendly() -> None:
    profile = make_profile(TrustTier.VERIFIED, 'coord:a', 'coord:b', reasons=('proof', 'review'))
    assert profile.to_dict() == {
        'tier': 'verified',
        'support_scope': ['coord:a', 'coord:b'],
        'reasons': ['proof', 'review'],
    }



def test_profile_explain_is_human_readable() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('human-review',))
    assert profile.explain() == 'reviewed [coord] :: human-review'



def test_profile_is_frozen_and_immutable() -> None:
    profile = make_profile(TrustTier.PROPOSAL, 'coord')
    with pytest.raises(FrozenInstanceError):
        profile.tier = TrustTier.VERIFIED  # type: ignore[misc]



def test_with_reasons_preserves_existing_profile_when_no_new_information_arrives() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('a', 'b'))
    assert profile.with_reasons() is profile
    assert profile.with_reasons('a') is profile



def test_with_reasons_deduplicates_and_preserves_order() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('first',))
    updated = profile.with_reasons('second', 'first', 'third')
    assert updated.reasons == ('first', 'second', 'third')
    assert profile.reasons == ('first',)



def test_covers_reports_membership_inside_support_scope() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'alpha', 'beta')
    assert profile.covers('alpha') is True
    assert profile.covers('gamma') is False


# ---------------------------------------------------------------------------
# Lawful joins
# ---------------------------------------------------------------------------


def test_join_trust_profiles_is_conservative() -> None:
    joined = join_trust_profiles(TrustProfile(TrustTier.REVIEWED, ('a',)), TrustProfile(TrustTier.PROPOSAL, ('a', 'b')))
    assert joined.tier is TrustTier.PROPOSAL
    with pytest.raises(JuGeoError):
        joined.promote(TrustTier.VERIFIED, explicit=False)


@pytest.mark.parametrize(
    ('left_tier', 'right_tier', 'expected'),
    [
        (TrustTier.PROPOSAL, TrustTier.PROPOSAL, TrustTier.PROPOSAL),
        (TrustTier.PROPOSAL, TrustTier.REVIEWED, TrustTier.PROPOSAL),
        (TrustTier.PROPOSAL, TrustTier.VERIFIED, TrustTier.PROPOSAL),
        (TrustTier.REVIEWED, TrustTier.REVIEWED, TrustTier.REVIEWED),
        (TrustTier.REVIEWED, TrustTier.VERIFIED, TrustTier.REVIEWED),
        (TrustTier.VERIFIED, TrustTier.VERIFIED, TrustTier.VERIFIED),
    ],
)
def test_join_uses_the_weaker_tier(left_tier: TrustTier, right_tier: TrustTier, expected: TrustTier) -> None:
    left = make_profile(left_tier, 'left')
    right = make_profile(right_tier, 'right')
    joined = left.join(right)
    assert joined.tier is expected
    assert joined.tier <= left.tier
    assert joined.tier <= right.tier



def test_join_prefers_common_scope_when_it_exists() -> None:
    left = make_profile(TrustTier.VERIFIED, 'a', 'b', 'c')
    right = make_profile(TrustTier.REVIEWED, 'b', 'c', 'd')
    joined = left.join(right)
    assert joined.support_scope == ('b', 'c')



def test_join_uses_union_when_inputs_do_not_overlap() -> None:
    left = make_profile(TrustTier.REVIEWED, 'alpha')
    right = make_profile(TrustTier.REVIEWED, 'beta')
    joined = left.join(right)
    assert joined.support_scope == ('alpha', 'beta')



def test_join_respects_empty_scope_as_unrefined_support() -> None:
    left = make_profile(TrustTier.REVIEWED)
    right = make_profile(TrustTier.PROPOSAL, 'alpha', 'beta')
    assert left.join(right).support_scope == ('alpha', 'beta')
    assert right.join(left).support_scope == ('alpha', 'beta')



def test_join_deduplicates_reasons_in_arrival_order() -> None:
    left = make_profile(TrustTier.REVIEWED, 'coord', reasons=('proof', 'review'))
    right = make_profile(TrustTier.PROPOSAL, 'coord', reasons=('review', 'runtime', 'proof'))
    joined = left.join(right)
    assert joined.reasons == ('proof', 'review', 'runtime')



def test_join_returns_left_instance_when_other_adds_no_new_information() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('review',))
    joined = profile.join(make_profile(TrustTier.REVIEWED, 'coord', reasons=('review',)))
    assert joined is profile



def test_join_rejects_non_profile_inputs() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord')
    with pytest.raises(TypeError):
        profile.join('not-a-profile')  # type: ignore[arg-type]



def test_empty_variadic_join_defaults_to_proposal() -> None:
    profile = join_trust_profiles()
    assert profile == TrustProfile(TrustTier.PROPOSAL)
    assert profile.support_scope == ()
    assert profile.reasons == ()



def test_variadic_join_is_left_fold_stable_for_reasons_and_scope() -> None:
    profiles = (
        make_profile(TrustTier.VERIFIED, 'a', 'b', reasons=('proof',)),
        make_profile(TrustTier.REVIEWED, 'b', 'c', reasons=('review',)),
        make_profile(TrustTier.PROPOSAL, 'b', 'd', reasons=('runtime', 'review')),
    )
    folded = join_trust_profiles(*profiles)
    manual = profiles[0].join(profiles[1]).join(profiles[2])
    assert folded == manual
    assert folded.tier is TrustTier.PROPOSAL
    assert folded.support_scope == ('b',)
    assert folded.reasons == ('proof', 'review', 'runtime')



def test_join_of_three_disjoint_profiles_retains_conservative_union_scope() -> None:
    joined = join_trust_profiles(
        make_profile(TrustTier.VERIFIED, 'a', reasons=('proof',)),
        make_profile(TrustTier.REVIEWED, 'b', reasons=('review',)),
        make_profile(TrustTier.PROPOSAL, 'c', reasons=('proposal',)),
    )
    assert joined.support_scope == ('a', 'b', 'c')
    assert joined.tier is TrustTier.PROPOSAL



def test_join_monotonicity_never_strengthens_when_more_profiles_are_added() -> None:
    base = join_trust_profiles(
        make_profile(TrustTier.VERIFIED, 'a', reasons=('proof',)),
        make_profile(TrustTier.REVIEWED, 'a', reasons=('review',)),
    )
    extended = join_trust_profiles(
        base,
        make_profile(TrustTier.PROPOSAL, 'a', 'b', reasons=('challenge-pending',)),
    )
    assert extended.tier <= base.tier
    assert extended.support_scope == ('a',)
    assert 'challenge-pending' in extended.reasons



def test_join_keeps_reason_order_stable_across_multiple_duplicates() -> None:
    joined = join_trust_profiles(
        make_profile(TrustTier.VERIFIED, 'x', reasons=('proof', 'review', 'proof')),
        make_profile(TrustTier.REVIEWED, 'x', reasons=('review', 'runtime')),
        make_profile(TrustTier.REVIEWED, 'x', reasons=('runtime', 'proof', 'audit')),
    )
    assert joined.reasons == ('proof', 'review', 'runtime', 'audit')


# ---------------------------------------------------------------------------
# Promotion rules and no-silent-strengthening
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('current', 'target'),
    [
        (TrustTier.PROPOSAL, TrustTier.REVIEWED),
        (TrustTier.PROPOSAL, TrustTier.VERIFIED),
        (TrustTier.REVIEWED, TrustTier.VERIFIED),
    ],
)
def test_silent_promotion_raises_structured_failure(current: TrustTier, target: TrustTier) -> None:
    profile = make_profile(current, 'coord', reasons=('seed',))
    with pytest.raises(JuGeoError) as excinfo:
        profile.promote(target, explicit=False)
    assert_failure(excinfo.value, code='silent-trust-promotion', current=current, target=target)



def test_explicit_promotion_succeeds_and_records_audit_reasons() -> None:
    promoted = make_profile(TrustTier.PROPOSAL, 'coord', reasons=('seed',)).promote(TrustTier.VERIFIED, explicit=True)
    assert promoted.tier is TrustTier.VERIFIED
    assert promoted.support_scope == ('coord',)
    assert promoted.reasons == (
        'seed',
        'explicit-promotion',
        'promotion:proposal->verified',
    )



def test_promote_to_same_tier_returns_original_instance() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('review',))
    assert profile.promote(TrustTier.REVIEWED, explicit=False) is profile



def test_promote_to_weaker_tier_remains_compatible_with_existing_callers() -> None:
    profile = make_profile(TrustTier.VERIFIED, 'coord', reasons=('proof',))
    demoted = profile.promote(TrustTier.REVIEWED, explicit=False)
    assert demoted.tier is TrustTier.REVIEWED
    assert demoted.reasons == ('proof',)
    assert demoted.support_scope == ('coord',)



def test_explicit_promotion_deduplicates_existing_audit_reasons() -> None:
    profile = make_profile(
        TrustTier.REVIEWED,
        'coord',
        reasons=('explicit-promotion', 'promotion:reviewed->verified', 'review'),
    )
    promoted = profile.promote(TrustTier.VERIFIED, explicit=True)
    assert promoted.reasons == ('explicit-promotion', 'promotion:reviewed->verified', 'review')


# ---------------------------------------------------------------------------
# Explicit demotion and challenge conservativity
# ---------------------------------------------------------------------------



def test_demote_rejects_attempted_strengthening() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord')
    with pytest.raises(JuGeoError) as excinfo:
        profile.demote(TrustTier.VERIFIED, reason='this would strengthen')
    assert_failure(excinfo.value, code='silent-trust-promotion', current=TrustTier.REVIEWED, target=TrustTier.VERIFIED)



def test_demote_can_lower_tier_without_mutating_input() -> None:
    profile = make_profile(TrustTier.VERIFIED, 'coord', reasons=('proof',))
    demoted = profile.demote(TrustTier.REVIEWED, reason='replay-failed')
    assert demoted.tier is TrustTier.REVIEWED
    assert demoted.reasons == ('proof', 'replay-failed')
    assert profile.tier is TrustTier.VERIFIED
    assert profile.reasons == ('proof',)



def test_demote_can_narrow_scope_to_residual_frontier() -> None:
    profile = make_profile(TrustTier.VERIFIED, 'a', 'b', 'c', reasons=('proof',))
    demoted = profile.demote(TrustTier.REVIEWED, reason='scope-narrowed', residual_scope=('b', 'z'))
    assert demoted.support_scope == ('b',)
    assert demoted.reasons == ('proof', 'scope-narrowed')



def test_demote_uses_requested_scope_when_current_scope_is_empty() -> None:
    profile = make_profile(TrustTier.REVIEWED, reasons=('review',))
    demoted = profile.demote(TrustTier.PROPOSAL, reason='new-boundary', residual_scope=('subset',))
    assert demoted.support_scope == ('subset',)
    assert demoted.reasons == ('review', 'new-boundary')



def test_challenge_defaults_to_one_step_demotions() -> None:
    assert make_profile(TrustTier.VERIFIED, 'coord').challenge(reason='counterexample').tier is TrustTier.REVIEWED
    assert make_profile(TrustTier.REVIEWED, 'coord').challenge(reason='counterexample').tier is TrustTier.PROPOSAL
    assert make_profile(TrustTier.PROPOSAL, 'coord').challenge(reason='counterexample').tier is TrustTier.PROPOSAL



def test_challenge_records_reason_and_residualizes_scope() -> None:
    profile = make_profile(TrustTier.VERIFIED, 'a', 'b', 'c', reasons=('proof',))
    challenged = profile.challenge(reason='runtime divergence', residual_scope=('b', 'd'))
    assert challenged.tier is TrustTier.REVIEWED
    assert challenged.support_scope == ('b',)
    assert challenged.reasons == ('proof', 'challenge:runtime divergence')



def test_challenge_can_target_an_explicit_tier() -> None:
    profile = make_profile(TrustTier.VERIFIED, 'coord', reasons=('proof',))
    challenged = profile.challenge(reason='serious objection', demote_to=TrustTier.PROPOSAL)
    assert challenged.tier is TrustTier.PROPOSAL
    assert challenged.reasons == ('proof', 'challenge:serious objection')



def test_challenge_of_unscoped_profile_can_introduce_residual_scope() -> None:
    profile = make_profile(TrustTier.REVIEWED, reasons=('review',))
    challenged = profile.challenge(reason='local-only', residual_scope=('local',))
    assert challenged.support_scope == ('local',)
    assert challenged.tier is TrustTier.PROPOSAL



def test_challenge_never_drops_prior_reasons() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('review', 'runtime'))
    challenged = profile.challenge(reason='new evidence')
    assert challenged.reasons == ('review', 'runtime', 'challenge:new evidence')



def test_demote_with_no_changes_returns_same_instance() -> None:
    profile = make_profile(TrustTier.PROPOSAL, 'coord', reasons=('seed',))
    assert profile.demote(TrustTier.PROPOSAL, reason=None, residual_scope=None) is profile


# ---------------------------------------------------------------------------
# Shared-code compatibility checks
# ---------------------------------------------------------------------------



def test_manifest_integration_uses_joined_trust_profiles() -> None:
    manifest = build_evidence_manifest(
        'coord',
        'claim',
        (make_record(EvidenceKind.RUNTIME, obligations=('todo',)),),
        trust_profiles=(
            make_profile(TrustTier.VERIFIED, 'coord', reasons=('proof',)),
            make_profile(TrustTier.REVIEWED, 'coord', reasons=('review',)),
            make_profile(TrustTier.PROPOSAL, 'coord', reasons=('proposal',)),
        ),
        provenance=ProvenanceTrace('root'),
    )
    assert manifest.trust.tier is TrustTier.PROPOSAL
    assert manifest.trust.support_scope == ('coord',)
    assert manifest.trust.reasons == ('proof', 'review', 'proposal')
    assert manifest.residuals == ('todo',)



def test_manifest_join_preserves_union_scope_when_support_is_disjoint() -> None:
    manifest = build_evidence_manifest(
        'coord',
        'claim',
        (make_record(EvidenceKind.PROOF),),
        trust_profiles=(
            make_profile(TrustTier.VERIFIED, 'alpha', reasons=('proof',)),
            make_profile(TrustTier.REVIEWED, 'beta', reasons=('review',)),
        ),
        provenance=ProvenanceTrace('root'),
    )
    assert manifest.trust.support_scope == ('alpha', 'beta')
    assert manifest.trust.tier is TrustTier.REVIEWED



def test_join_results_remain_plain_trust_profiles_for_other_subsystems() -> None:
    joined = join_trust_profiles(
        make_profile(TrustTier.REVIEWED, 'coord', reasons=('review',)),
        make_profile(TrustTier.PROPOSAL, 'coord', reasons=('proposal',)),
    )
    assert isinstance(joined, TrustProfile)
    assert joined.to_dict()['tier'] == 'proposal'



def test_explicit_promotion_followed_by_challenge_still_records_full_audit_trail() -> None:
    promoted = make_profile(TrustTier.PROPOSAL, 'coord', reasons=('seed',)).promote(TrustTier.REVIEWED, explicit=True)
    challenged = promoted.challenge(reason='replay mismatch', residual_scope=('coord',))
    assert challenged.tier is TrustTier.PROPOSAL
    assert challenged.reasons == (
        'seed',
        'explicit-promotion',
        'promotion:proposal->reviewed',
        'challenge:replay mismatch',
    )



def test_multiple_challenges_deduplicate_identical_explanations() -> None:
    profile = make_profile(TrustTier.REVIEWED, 'coord', reasons=('review',))
    first = profile.challenge(reason='same issue')
    second = first.challenge(reason='same issue')
    assert second.reasons == ('review', 'challenge:same issue')
    assert second.tier is TrustTier.PROPOSAL



def test_stringifying_a_profile_after_operations_remains_easy_to_read() -> None:
    profile = (
        make_profile(TrustTier.PROPOSAL, 'coord', reasons=('seed',))
        .promote(TrustTier.REVIEWED, explicit=True)
        .challenge(reason='scope drift', residual_scope=('coord',))
    )
    assert profile.explain() == (
        'proposal [coord] :: '
        'seed; explicit-promotion; promotion:proposal->reviewed; challenge:scope drift'
    )
