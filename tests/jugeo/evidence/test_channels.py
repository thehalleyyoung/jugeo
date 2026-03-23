from pathlib import Path
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.channels import (
    AggregationPolicy,
    AggregationPolicyError,
    ChannelAdmissibilityError,
    ChannelJurisdiction,
    ClaimPolarity,
    ComparisonNormalForm,
    EvidenceConflictError,
    EvidenceFederationRecord,
    EvidenceKind,
    EvidenceRecord,
    build_channel,
    build_evidence_bundle,
    build_support_route,
    channel_is_admissible,
    merge_evidence_channels,
)
from jugeo.evidence.manifests import build_evidence_manifest
from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.errors import FailureClassification, FailureScope


PROOF_QUERY = 'structural-claim'
SOLVER_QUERY = 'arithmetic-fragment'
RUNTIME_QUERY = 'resource-claim'
SEMANTIC_QUERY = 'semantic-refinement'
PROPOSAL_QUERY = 'proposal'
HUMAN_QUERY = 'human-ratification'


def _channel(kind: EvidenceKind, *, name: str | None = None, challengeable: bool = False, notes: tuple[str, ...] = ()):
    return build_channel(name or kind.value, kind, challengeable=challengeable, notes=notes)


def _shared_channel(kind: EvidenceKind, *, query_family: str = 'shared-clause', name: str | None = None):
    base = ChannelJurisdiction.for_kind(kind)
    return build_channel(
        name or kind.value,
        kind,
        jurisdiction=ChannelJurisdiction(
            admissible_queries=base.admissible_queries + (query_family,),
            evidence_families=base.evidence_families,
            escalation_limits=tuple(limit for limit in base.escalation_limits if limit != query_family),
            non_theorems=tuple(limit for limit in base.non_theorems if limit != query_family),
            notes=base.notes + ('shared-test-query',),
        ),
    )


def _route(channel, *, query_family: str, region: str, target_form: str | None = None, reason: str = 'theory-route'):
    evidence_form = target_form or channel.jurisdiction.evidence_families[0]
    return build_support_route(
        channel,
        target_evidence_form=evidence_form,
        success_witness_schema=f'{evidence_form}-success',
        failure_witness_schema=f'{evidence_form}-failure',
        invalidation_policy='recompute-on-support-change',
        support_region=region,
        reason=reason,
        admissible_query_family=query_family,
    )


def _record(
    channel,
    claim: str,
    *,
    region: str,
    polarity: ClaimPolarity = ClaimPolarity.SUPPORT,
    obligations: tuple[str, ...] = (),
    payload: dict[str, object] | None = None,
    route_reason: str = 'theory-route',
    query_family: str | None = None,
):
    route = _route(
        channel,
        query_family=query_family
        or {
            EvidenceKind.PROOF: PROOF_QUERY,
            EvidenceKind.SOLVER: SOLVER_QUERY,
            EvidenceKind.RUNTIME: RUNTIME_QUERY,
            EvidenceKind.SEMANTIC: SEMANTIC_QUERY,
            EvidenceKind.PROPOSAL: PROPOSAL_QUERY,
            EvidenceKind.HUMAN: HUMAN_QUERY,
        }[channel.kind],
        region=region,
        reason=route_reason,
    )
    base_payload = {'polarity': polarity.value, 'support_region': region}
    if payload:
        base_payload.update(payload)
    return EvidenceRecord(
        channel,
        claim,
        payload=base_payload,
        obligations=obligations,
        provenance=('source:tests',),
        support_routes=(route,),
        explicit_support_regions=(region,),
        challenge_notes=('route-materialized',),
    )


def _bundle(
    channel,
    claim: str,
    *,
    query_family: str,
    region: str,
    polarity: ClaimPolarity = ClaimPolarity.SUPPORT,
    obligations: tuple[str, ...] = (),
    residual_obligations: tuple[str, ...] = (),
    note: str = 'bundle-note',
):
    record = _record(channel, claim, region=region, polarity=polarity, obligations=obligations, query_family=query_family)
    route = record.support_routes[0]
    return build_evidence_bundle(
        channel,
        query_family,
        (record,),
        target_evidence_form=route.target_evidence_form,
        expected_success_witness_schema=route.success_witness_schema,
        expected_failure_witness_schema=route.failure_witness_schema,
        invalidation_policy=route.invalidation_policy,
        support_routes=(route,),
        residual_obligations=residual_obligations,
        notes=(note,),
    )


def test_evidence_record_keeps_channel_kind() -> None:
    channel = build_channel('solver', EvidenceKind.SOLVER)
    record = EvidenceRecord(channel, 'x = x')
    assert record.canonical_key().startswith('solver:solver:')


def test_build_channel_installs_default_jurisdiction_for_kind() -> None:
    channel = _channel(EvidenceKind.PROOF)
    assert channel.jurisdiction.admits_query(PROOF_QUERY)
    assert 'proof-term' in channel.jurisdiction.evidence_families


def test_human_kind_is_present_for_shared_foundation_compatibility() -> None:
    channel = _channel(EvidenceKind.HUMAN)
    assert channel.kind is EvidenceKind.HUMAN
    assert channel.trust_floor == 'reviewed'
    assert HUMAN_QUERY in channel.jurisdiction.admissible_queries


@pytest.mark.parametrize(
    ('kind', 'query_family'),
    [
        (EvidenceKind.PROOF, PROOF_QUERY),
        (EvidenceKind.SOLVER, SOLVER_QUERY),
        (EvidenceKind.RUNTIME, RUNTIME_QUERY),
        (EvidenceKind.SEMANTIC, SEMANTIC_QUERY),
        (EvidenceKind.PROPOSAL, PROPOSAL_QUERY),
        (EvidenceKind.HUMAN, HUMAN_QUERY),
    ],
)
def test_channel_is_admissible_for_authorized_query_families(kind: EvidenceKind, query_family: str) -> None:
    channel = _channel(kind)
    assert channel_is_admissible(channel, query_family)


@pytest.mark.parametrize(
    ('kind', 'query_family'),
    [
        (EvidenceKind.SOLVER, 'author-intent'),
        (EvidenceKind.RUNTIME, 'structural-claim'),
        (EvidenceKind.PROPOSAL, 'settlement'),
        (EvidenceKind.SEMANTIC, 'silent-trust-promotion'),
    ],
)
def test_channel_is_not_admissible_for_non_theorems(kind: EvidenceKind, query_family: str) -> None:
    channel = _channel(kind)
    assert channel_is_admissible(channel, query_family) is False


def test_require_admissibility_raises_structured_authority_error() -> None:
    channel = _channel(EvidenceKind.SOLVER)
    with pytest.raises(ChannelAdmissibilityError) as excinfo:
        channel.require_admissibility('author-intent', evidence_family='solver-model', claim='intent(x)')
    failure = excinfo.value.failure
    assert failure.classification is FailureClassification.JURISDICTION_EXCEEDED
    assert failure.scope is FailureScope.AUTHORITY
    assert failure.metadata['required_obligation'] == 'escalate:solver:author-intent'


def test_channel_jurisdiction_to_mapping_is_auditable() -> None:
    jurisdiction = ChannelJurisdiction.for_kind(EvidenceKind.RUNTIME)
    payload = jurisdiction.to_mapping()
    assert 'resource-claim' in payload['admissible_queries']
    assert 'proof-generalization' in payload['escalation_limits']


def test_support_route_keeps_all_explicit_route_components() -> None:
    channel = _channel(EvidenceKind.PROOF, notes=('copilot review disabled for settlement',))
    route = _route(channel, query_family=PROOF_QUERY, region='coord/theorem')
    assert route.channel_kind is EvidenceKind.PROOF
    assert route.support_region == 'coord/theorem'
    assert route.success_witness_schema.endswith('-success')
    assert 'coord/theorem' in route.canonical_key()


def test_clause_support_inherits_schema_and_region_from_route() -> None:
    channel = _channel(EvidenceKind.RUNTIME)
    record = _record(channel, 'memory <= budget', region='coord/runtime')
    clause_support = record.clause_support[0]
    assert clause_support.support_region == 'coord/runtime'
    assert clause_support.witness_schema == record.support_routes[0].success_witness_schema
    assert clause_support.failure_schema == record.support_routes[0].failure_witness_schema


def test_record_builds_evidence_vector_descriptor_by_default() -> None:
    channel = _channel(EvidenceKind.SOLVER)
    record = _record(channel, 'x + y >= 0', region='coord/arith')
    assert record.evidence_vector['solver'][0].startswith('solver:solver:')
    assert 'coord/arith' in record.support_regions()


def test_record_with_support_route_preserves_route_and_region() -> None:
    channel = _channel(EvidenceKind.PROPOSAL)
    base = EvidenceRecord(channel, 'candidate lemma')
    route = _route(channel, query_family=PROPOSAL_QUERY, region='coord/lemma', target_form='candidate-route')
    updated = base.with_support_route(route)
    assert updated.support_routes[0] == route
    assert updated.explicit_support_regions == ('coord/lemma',)


def test_bundle_validates_all_records_belong_to_same_channel() -> None:
    proof = _channel(EvidenceKind.PROOF)
    solver = _channel(EvidenceKind.SOLVER)
    record = _record(solver, 'x = x', region='coord/solver')
    with pytest.raises(ValueError):
        build_evidence_bundle(proof, PROOF_QUERY, (record,), target_evidence_form='proof-term')


def test_bundle_support_regions_include_record_and_route_regions() -> None:
    channel = _channel(EvidenceKind.PROOF)
    bundle = _bundle(channel, 'lemma', query_family=PROOF_QUERY, region='coord/lemma')
    assert bundle.support_regions() == ('coord/lemma',)
    assert bundle.evidence_vector_descriptor()['proof'][0].startswith('proof:proof:')


def test_bundle_admissibility_checks_query_family_and_evidence_form() -> None:
    channel = _channel(EvidenceKind.HUMAN)
    bundle = _bundle(channel, 'policy exception', query_family=HUMAN_QUERY, region='coord/policy')
    bundle.require_admissibility()
    assert channel_is_admissible(bundle, HUMAN_QUERY)


def test_merge_preserves_solver_kind_for_solver_only_federation() -> None:
    solver = _channel(EvidenceKind.SOLVER)
    left = _bundle(solver, 'x >= 0', query_family=SOLVER_QUERY, region='coord/solver/a')
    right = _bundle(solver, 'y >= 0', query_family=SOLVER_QUERY, region='coord/solver/b')
    federation = merge_evidence_channels((left, right))
    assert federation.has_kind(EvidenceKind.SOLVER)
    assert federation.kind_supports(EvidenceKind.SOLVER) == (
        'solver:solver:x >= 0',
        'solver:solver:y >= 0',
    )


def test_merge_preserves_proof_kind_for_proof_only_federation() -> None:
    proof = _channel(EvidenceKind.PROOF)
    first = _bundle(proof, 'glue(A,B)', query_family=PROOF_QUERY, region='coord/proof/a')
    second = _bundle(proof, 'agree(A,B)', query_family=PROOF_QUERY, region='coord/proof/b')
    federation = merge_evidence_channels((first, second))
    assert federation.kind_supports(EvidenceKind.PROOF) == (
        'proof:proof:glue(A,B)',
        'proof:proof:agree(A,B)',
    )
    assert federation.demoted is False


def test_merge_preserves_multiple_kinds_in_clausewise_vector() -> None:
    proof = _shared_channel(EvidenceKind.PROOF)
    runtime = _shared_channel(EvidenceKind.RUNTIME)
    proof_bundle = _bundle(proof, 'interface stable', query_family='shared-clause', region='coord/proof')
    runtime_bundle = _bundle(runtime, 'latency observed', query_family='shared-clause', region='coord/runtime')
    federation = merge_evidence_channels((proof_bundle, runtime_bundle), allow_demotion=True)
    assert federation.has_kind(EvidenceKind.PROOF)
    assert federation.has_kind(EvidenceKind.RUNTIME)
    assert federation.evidence_vector['proof'] == ('proof:proof:interface stable',)
    assert federation.evidence_vector['runtime'] == ('runtime:runtime:latency observed',)


def test_merge_requires_clause_and_support_normal_form() -> None:
    solver = _channel(EvidenceKind.SOLVER)
    bundle = _bundle(solver, 'x >= 0', query_family=SOLVER_QUERY, region='coord/solver')
    with pytest.raises(AggregationPolicyError) as excinfo:
        merge_evidence_channels((bundle,), comparison_normal_form=ComparisonNormalForm.EVIDENCE_FAMILY_DESCRIPTOR)
    assert excinfo.value.failure.classification is FailureClassification.ENCODING_MISMATCH


def test_merge_rejects_explicit_promotion_without_opt_in() -> None:
    proof = _channel(EvidenceKind.PROOF)
    bundle = _bundle(proof, 'P', query_family=PROOF_QUERY, region='coord/proof')
    with pytest.raises(AggregationPolicyError) as excinfo:
        merge_evidence_channels((bundle,), aggregation_policy=AggregationPolicy.EXPLICIT_PROMOTION)
    assert excinfo.value.failure.metadata['allow_trust_promotion'] is False


def test_merge_conflicting_channels_raise_typed_conflict() -> None:
    proof = _shared_channel(EvidenceKind.PROOF)
    runtime = _shared_channel(EvidenceKind.RUNTIME)
    left = _bundle(proof, 'service available', query_family='shared-clause', region='coord/proof')
    right = _bundle(runtime, 'service available', query_family='shared-clause', region='coord/runtime', polarity=ClaimPolarity.REFUTE)
    with pytest.raises(EvidenceConflictError) as excinfo:
        merge_evidence_channels((left, right))
    failure = excinfo.value.failure
    assert failure.classification is FailureClassification.DESCENT_OBSTRUCTION
    assert failure.metadata['conflicts'][0]['contradiction'] == 'claim-polarity'


def test_merge_can_demote_instead_of_throwing_when_requested() -> None:
    proof = _shared_channel(EvidenceKind.PROOF)
    runtime = _shared_channel(EvidenceKind.RUNTIME)
    left = _bundle(proof, 'service available', query_family='shared-clause', region='coord/proof')
    right = _bundle(runtime, 'service available', query_family='shared-clause', region='coord/runtime', polarity=ClaimPolarity.REFUTE)
    federation = merge_evidence_channels((left, right), aggregation_policy=AggregationPolicy.CONSERVATIVE, allow_demotion=True)
    assert federation.demoted is True
    assert 'challenge:service available' in federation.residual_obligations
    assert 'coord/runtime' in federation.support_regions()


def test_merge_collects_record_and_bundle_residual_obligations() -> None:
    solver = _channel(EvidenceKind.SOLVER)
    bundle = _bundle(
        solver,
        'x >= 0',
        query_family=SOLVER_QUERY,
        region='coord/solver',
        obligations=('need-bounds',),
        residual_obligations=('need-domain',),
    )
    federation = merge_evidence_channels((bundle,))
    assert federation.residual_obligations == ('need-domain', 'need-bounds')


def test_federation_record_preserves_explicit_support_routes() -> None:
    proof = _channel(EvidenceKind.PROOF)
    bundle = _bundle(proof, 'lemma', query_family=PROOF_QUERY, region='coord/lemma')
    federation = merge_evidence_channels((bundle,))
    assert federation.support_routes[0].admissible_query_family == PROOF_QUERY
    assert federation.clause_support[0].route == federation.support_routes[0]


def test_federation_record_to_mapping_is_clausewise_and_parseable() -> None:
    proof = _channel(EvidenceKind.PROOF)
    bundle = _bundle(proof, 'lemma', query_family=PROOF_QUERY, region='coord/lemma')
    federation = merge_evidence_channels((bundle,))
    payload = federation.to_mapping()
    assert payload['comparison_normal_form'] == 'clause-and-support'
    assert payload['clause_support'][0]['clause'] == 'lemma'
    assert payload['support_routes'][0]['support_region'] == 'coord/lemma'


def test_build_evidence_bundle_uses_channel_defaults_when_requested() -> None:
    channel = _channel(EvidenceKind.SEMANTIC)
    record = _record(channel, 'interpret docs', region='coord/docs')
    bundle = build_evidence_bundle(channel, SEMANTIC_QUERY, (record,))
    assert bundle.target_evidence_form == channel.jurisdiction.evidence_families[0]
    assert bundle.expected_success_witness_schema.endswith('-witness')


def test_support_route_must_match_channel_identity() -> None:
    proof = _channel(EvidenceKind.PROOF)
    solver = _channel(EvidenceKind.SOLVER)
    foreign_route = _route(solver, query_family=SOLVER_QUERY, region='coord/solver')
    with pytest.raises(ValueError):
        build_channel('proof', EvidenceKind.PROOF, support_routes=(foreign_route,))


def test_proposal_channel_keeps_proposal_trust_floor() -> None:
    channel = _channel(EvidenceKind.PROPOSAL, notes=('copilot suggestion queue',))
    assert channel.trust_floor == 'proposal'
    assert 'candidate-bridge' in channel.jurisdiction.admissible_queries


def test_human_channel_authorizes_policy_ratifcation_but_not_solver_models() -> None:
    channel = _channel(EvidenceKind.HUMAN)
    assert channel_is_admissible(channel, HUMAN_QUERY, evidence_family='human-ratification')
    assert channel_is_admissible(channel, HUMAN_QUERY, evidence_family='solver-model') is False


def test_semantic_channel_rejects_silent_promotion_non_theorem() -> None:
    semantic = _channel(EvidenceKind.SEMANTIC)
    with pytest.raises(ChannelAdmissibilityError):
        semantic.require_admissibility('silent-trust-promotion', evidence_family='semantic-judgment')


def test_merge_rejects_mixed_clause_families_without_splitting() -> None:
    proof = _channel(EvidenceKind.PROOF)
    solver = _channel(EvidenceKind.SOLVER)
    proof_bundle = _bundle(proof, 'lemma', query_family=PROOF_QUERY, region='coord/proof')
    solver_bundle = _bundle(solver, 'x >= 0', query_family=SOLVER_QUERY, region='coord/solver')
    with pytest.raises(AggregationPolicyError):
        merge_evidence_channels((proof_bundle, solver_bundle))


def test_federation_support_regions_stay_visible_after_merge() -> None:
    solver = _channel(EvidenceKind.SOLVER)
    left = _bundle(solver, 'x >= 0', query_family=SOLVER_QUERY, region='coord/left')
    right = _bundle(solver, 'y >= 0', query_family=SOLVER_QUERY, region='coord/right')
    federation = merge_evidence_channels((left, right))
    assert federation.support_regions() == ('coord/left', 'coord/right')


def test_enriched_records_remain_compatible_with_manifest_builder() -> None:
    runtime = _channel(EvidenceKind.RUNTIME)
    record = _record(runtime, 'service healthy', region='coord/runtime', obligations=('observe-next-epoch',))
    manifest = build_evidence_manifest(
        'coord/runtime',
        'service healthy',
        (record,),
        trust_profiles=(TrustProfile(TrustTier.REVIEWED),),
        provenance=ProvenanceTrace('root'),
    )
    assert manifest.residuals == ('observe-next-epoch',)
    assert manifest.records[0].support_routes[0].support_region == 'coord/runtime'


def test_federation_record_object_is_easy_for_humans_and_llms_to_parse() -> None:
    human = _channel(EvidenceKind.HUMAN)
    bundle = _bundle(human, 'policy approval', query_family=HUMAN_QUERY, region='coord/policy')
    federation = merge_evidence_channels((bundle,))
    assert isinstance(federation, EvidenceFederationRecord)
    assert federation.canonical_key() == 'human-ratification:clause-and-support:kind-preserving'
    assert federation.to_mapping()['evidence_vector']['human'] == ['human:human:policy approval']


def test_record_require_query_uses_channel_admissibility() -> None:
    record = _record(_channel(EvidenceKind.RUNTIME), 'heap stable', region='coord/runtime')
    record.require_query(RUNTIME_QUERY, evidence_family='trace-witness')
    with pytest.raises(ChannelAdmissibilityError):
        record.require_query('structural-claim', evidence_family='trace-witness')


def test_bundle_notes_and_manifest_style_fields_are_stable() -> None:
    channel = _channel(EvidenceKind.PROOF)
    bundle = _bundle(channel, 'lemma', query_family=PROOF_QUERY, region='coord/lemma', note='foundation-bundle')
    payload = bundle.to_mapping()
    assert payload['notes'] == ['foundation-bundle']
    assert payload['records'][0]['challenge_notes'] == ['route-materialized']


def test_proof_channel_jurisdiction_blocks_policy_ratifcation_claims() -> None:
    proof = _channel(EvidenceKind.PROOF)
    assert proof.jurisdiction.blocks_query('policy-ratification')
    assert channel_is_admissible(proof, 'policy-ratification') is False


def test_runtime_jurisdiction_marks_structural_claim_for_escalation_honesty() -> None:
    runtime = _channel(EvidenceKind.RUNTIME)
    assert runtime.jurisdiction.needs_escalation('structural-claim')
    with pytest.raises(ChannelAdmissibilityError) as excinfo:
        runtime.require_admissibility('structural-claim', evidence_family='trace-witness')
    assert excinfo.value.failure.metadata['required_obligation'] == 'escalate:runtime:structural-claim'
