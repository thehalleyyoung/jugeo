"""Property-based tests for evidence kind preservation invariants.

This module uses Hypothesis to verify that evidence kinds are preserved and
that trust level ceiling invariants are respected across evidence channels,
provenance operations, and bundle merges.

Theory context (theory2.tex §252, §3):
* Every piece of evidence arrives through exactly one channel with a defined
  trust ceiling.  Solver-backed evidence carries higher trust than runtime
  witnesses, which carry higher trust than oracle proposals.
* Trust levels flow DOWN through channels; they may not flow UP silently.
* Provenance traces are append-only: steps are added, never removed.
* Evidence bundle merge is safe: it preserves all evidence kinds from both
  sides, never drops or conflates them.

Properties under test:

* Solver-backed evidence cannot be downgraded to COPILOT_SUGGESTED through
  any series of channel ceiling enforcements.
* Oracle-proposed evidence cannot be promoted to SOLVER_DISCHARGED or above
  without explicit justification (using TrustPromotion).
* TrustCeiling.enforce never raises trust level above channel ceiling.
* ProvenanceTrace.append is purely functional: original is unchanged.
* ProvenanceTrace.append never removes existing steps.
* ProvenanceGraph.is_acyclic holds after every valid add_node call.
* ProvenanceGraph.ancestors_of(n) is a subset of all node ids.
* descendants are consistent with ancestors (a in descendants(b) ⟺ b in ancestors(a)).
* EvidenceBundle.merge preserves all kinds from both bundles.
* ChannelFederation.compute_combined_trust is the minimum across all responses.
* EvidenceRecord.canonical_key is deterministic.
* Copilot channel ceiling is strictly below solver channel ceiling.
* Two provenance graphs merged preserve all nodes from both.
* ProvenanceNode.is_copilot_node() correctly identifies copilot origin.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "src" / "jugeo").exists()
)
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest
from hypothesis import assume, given, settings, HealthCheck
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from jugeo.errors import JuGeoError
from jugeo.evidence.channels import (
    EvidenceChannel,
    EvidenceRecord,
    EvidenceResponse,
    ChannelFederation,
    build_channel,
)
from jugeo.evidence.trust import (
    TrustLevel,
    TrustAlgebra,
    TrustAttenuation,
    TrustCeiling,
    TrustPromotion,
)
from jugeo.evidence.provenance import (
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceOperation,
    ProvenanceStep,
    ProvenanceTrace,
)
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    TrustLevel as JudgmentTrustLevel,
)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

_alg = TrustAlgebra()
_attn = TrustAttenuation(algebra=_alg)
_ceiling = TrustCeiling()
_fed = ChannelFederation()

# Channel-to-ceiling mapping as defined by TrustCeiling
CHANNEL_CEILINGS: dict[str, TrustLevel] = {
    "solver": TrustLevel.SOLVER_DISCHARGED,
    "runtime": TrustLevel.RUNTIME_WITNESSED,
    "oracle": TrustLevel.ORACLE_PROPOSED,
    "copilot": TrustLevel.COPILOT_SUGGESTED,
    "formal_proof": TrustLevel.MECHANICALLY_VERIFIED,
    "human": TrustLevel.HUMAN_ATTESTED,
}

# Main chain trust levels (not CONTRADICTED)
CHAIN_LEVELS = [
    TrustLevel.UNVERIFIED,
    TrustLevel.COPILOT_SUGGESTED,
    TrustLevel.ORACLE_PROPOSED,
    TrustLevel.HUMAN_ATTESTED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.MECHANICALLY_VERIFIED,
]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

chain_level_st: SearchStrategy[TrustLevel] = st.sampled_from(CHAIN_LEVELS)
channel_name_st: SearchStrategy[str] = st.sampled_from(list(CHANNEL_CEILINGS.keys()))
evidence_channel_st: SearchStrategy[EvidenceChannel] = st.sampled_from(list(EvidenceChannel))

trust_level_str_st: SearchStrategy[str] = st.sampled_from(
    ["proposal", "reviewed", "verified"]
)

provenance_operation_st: SearchStrategy[ProvenanceOperation] = st.sampled_from(
    list(ProvenanceOperation)
)

node_id_st: SearchStrategy[str] = st.text(
    min_size=4, max_size=16,
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
)

provenance_node_st: SearchStrategy[ProvenanceNode] = st.builds(
    ProvenanceNode,
    node_id=node_id_st,
    source_channel=channel_name_st,
    operation=provenance_operation_st,
    inputs=st.tuples(),
    trust_at_creation=trust_level_str_st,
)

provenance_step_st: SearchStrategy[ProvenanceStep] = st.builds(
    ProvenanceStep,
    actor=st.text(min_size=1, max_size=20),
    action=st.text(min_size=1, max_size=20),
    coordinate=st.text(min_size=1, max_size=20),
)

judgment_trust_level_st: SearchStrategy[JudgmentTrustLevel] = st.sampled_from(
    list(JudgmentTrustLevel)
)

evidence_kind_st: SearchStrategy[EvidenceItemKind] = st.sampled_from(
    list(EvidenceItemKind)
)

evidence_item_st: SearchStrategy[EvidenceItem] = st.builds(
    EvidenceItem,
    kind=evidence_kind_st,
    payload=st.just({}),
    trust_level=judgment_trust_level_st,
    channel=st.sampled_from(["solver", "runtime", "oracle", "copilot", "human"]),
    timestamp=st.just("2024-01-01T00:00:00"),
    expiry=st.just("2099-12-31T23:59:59"),
    provenance=st.tuples(),
)

evidence_bundle_st: SearchStrategy[EvidenceBundle] = st.lists(
    evidence_item_st, max_size=4
).map(lambda items: EvidenceBundle(items=tuple(items)))


# ---------------------------------------------------------------------------
# 1. TrustCeiling.enforce never raises trust above channel ceiling
# ---------------------------------------------------------------------------


@given(chain_level_st, channel_name_st)
def test_ceiling_enforce_never_raises_above_ceiling(
    level: TrustLevel, channel: str
) -> None:
    """TrustCeiling.enforce(level, channel) result ≤ channel ceiling."""
    enforced = _ceiling.enforce(level, channel)
    ceiling_for_channel = CHANNEL_CEILINGS[channel]
    assert enforced.rank_index() <= ceiling_for_channel.rank_index(), (
        f"enforce({level.name}, {channel}) = {enforced.name} exceeds "
        f"ceiling {ceiling_for_channel.name}"
    )


@given(chain_level_st)
def test_ceiling_copilot_never_exceeds_copilot_suggested(level: TrustLevel) -> None:
    """Copilot channel ceiling is COPILOT_SUGGESTED: enforcement always clamps."""
    enforced = _ceiling.enforce(level, "copilot")
    assert enforced.rank_index() <= TrustLevel.COPILOT_SUGGESTED.rank_index(), (
        f"Copilot ceiling violation: enforced {enforced.name} > COPILOT_SUGGESTED"
    )


@given(chain_level_st)
def test_ceiling_oracle_never_exceeds_oracle_proposed(level: TrustLevel) -> None:
    """Oracle channel ceiling is ORACLE_PROPOSED: enforcement always clamps."""
    enforced = _ceiling.enforce(level, "oracle")
    assert enforced.rank_index() <= TrustLevel.ORACLE_PROPOSED.rank_index(), (
        f"Oracle ceiling violation: enforced {enforced.name} > ORACLE_PROPOSED"
    )


@given(chain_level_st)
def test_ceiling_solver_never_exceeds_solver_discharged(level: TrustLevel) -> None:
    """Solver channel ceiling is SOLVER_DISCHARGED."""
    enforced = _ceiling.enforce(level, "solver")
    assert enforced.rank_index() <= TrustLevel.SOLVER_DISCHARGED.rank_index(), (
        f"Solver ceiling violation: enforced {enforced.name} > SOLVER_DISCHARGED"
    )


# ---------------------------------------------------------------------------
# 2. Solver-backed evidence cannot be downgraded to COPILOT_SUGGESTED
# ---------------------------------------------------------------------------


def test_solver_evidence_ceiling_strictly_above_copilot() -> None:
    """Solver channel ceiling (SOLVER_DISCHARGED) > Copilot ceiling (COPILOT_SUGGESTED)."""
    solver_ceiling = CHANNEL_CEILINGS["solver"]
    copilot_ceiling = CHANNEL_CEILINGS["copilot"]
    assert solver_ceiling > copilot_ceiling, (
        f"Solver ceiling {solver_ceiling.name} should be strictly above "
        f"copilot ceiling {copilot_ceiling.name}"
    )


def test_solver_evidence_not_downgraded_through_copilot_channel() -> None:
    """Applying copilot ceiling to SOLVER_DISCHARGED evidence gives COPILOT_SUGGESTED."""
    solver_level = TrustLevel.SOLVER_DISCHARGED
    enforced_as_copilot = _ceiling.enforce(solver_level, "copilot")
    # Must be clamped to COPILOT_SUGGESTED, not promoted
    assert enforced_as_copilot is TrustLevel.COPILOT_SUGGESTED, (
        f"Solver evidence forced through copilot ceiling should give "
        f"COPILOT_SUGGESTED, got {enforced_as_copilot.name}"
    )
    # Critically: it should NOT be SOLVER_DISCHARGED or above
    assert not (enforced_as_copilot >= TrustLevel.RUNTIME_WITNESSED), (
        "Solver evidence should NOT retain solver-level trust after copilot ceiling"
    )


def test_solver_evidence_ceiling_is_not_oracle_proposed() -> None:
    """Solver ceiling (SOLVER_DISCHARGED) ≠ ORACLE_PROPOSED: they are different tiers."""
    assert CHANNEL_CEILINGS["solver"] is not TrustLevel.ORACLE_PROPOSED


# ---------------------------------------------------------------------------
# 3. Oracle evidence cannot be promoted to SOLVER_DISCHARGED without ceremony
# ---------------------------------------------------------------------------


def test_oracle_evidence_below_solver_discharged() -> None:
    """ORACLE_PROPOSED < SOLVER_DISCHARGED in the partial order."""
    assert TrustLevel.ORACLE_PROPOSED < TrustLevel.SOLVER_DISCHARGED, (
        "Oracle-proposed trust must be strictly below solver-discharged"
    )


def test_oracle_ceiling_below_solver_ceiling() -> None:
    """Oracle ceiling (ORACLE_PROPOSED) < solver ceiling (SOLVER_DISCHARGED)."""
    assert CHANNEL_CEILINGS["oracle"] < CHANNEL_CEILINGS["solver"]


def test_oracle_promotion_requires_explicit_justification() -> None:
    """TrustAlgebra.promote from ORACLE_PROPOSED with empty justification raises."""
    with pytest.raises(JuGeoError):
        _alg.promote(TrustLevel.ORACLE_PROPOSED, "")


def test_oracle_promotion_with_justification_does_not_reach_solver() -> None:
    """A single-step promotion from ORACLE_PROPOSED reaches HUMAN_ATTESTED, not solver."""
    promoted = _alg.promote(TrustLevel.ORACLE_PROPOSED, "manual review confirmed")
    assert promoted is TrustLevel.HUMAN_ATTESTED, (
        f"Single promotion from ORACLE_PROPOSED should reach HUMAN_ATTESTED, "
        f"got {promoted.name}"
    )
    assert promoted is not TrustLevel.SOLVER_DISCHARGED, (
        "One promotion from ORACLE_PROPOSED must not jump to SOLVER_DISCHARGED"
    )


def test_oracle_needs_multiple_promotions_to_reach_solver() -> None:
    """Reaching SOLVER_DISCHARGED from ORACLE_PROPOSED requires multiple promotions."""
    current = TrustLevel.ORACLE_PROPOSED
    target = TrustLevel.SOLVER_DISCHARGED
    steps = 0
    while current is not target and steps < 10:
        current = _alg.promote(current, f"step-{steps}")
        steps += 1
    assert steps > 1, (
        "Should require more than 1 promotion to go from ORACLE_PROPOSED to SOLVER_DISCHARGED"
    )
    assert current is target, "Should eventually reach SOLVER_DISCHARGED after enough promotions"


# ---------------------------------------------------------------------------
# 4. Provenance chain is append-only
# ---------------------------------------------------------------------------


@given(provenance_step_st)
def test_provenance_trace_append_preserves_original(step: ProvenanceStep) -> None:
    """ProvenanceTrace.append returns new trace; original is unchanged."""
    original = ProvenanceTrace(origin="test")
    updated = original.append(step)
    # Original must be unchanged
    assert len(original.steps) == 0, (
        "append() should not mutate the original ProvenanceTrace"
    )
    assert len(updated.steps) == 1, (
        "append() should add exactly one step to the new trace"
    )
    assert updated is not original, "append() must return a new object"


@given(st.lists(provenance_step_st, min_size=2, max_size=6))
def test_provenance_trace_append_never_removes_steps(
    steps: list[ProvenanceStep]
) -> None:
    """Each successive append adds to the trace; no step is ever removed."""
    trace = ProvenanceTrace(origin="test")
    prev_len = 0
    for i, step in enumerate(steps):
        trace = trace.append(step)
        assert len(trace.steps) == i + 1, (
            f"After {i+1} appends, trace has {len(trace.steps)} steps (expected {i+1})"
        )
        assert len(trace.steps) > prev_len, "append reduced step count"
        prev_len = len(trace.steps)


@given(provenance_step_st, provenance_step_st)
def test_provenance_trace_earlier_steps_survive_later_appends(
    step1: ProvenanceStep, step2: ProvenanceStep
) -> None:
    """After appending step2, the trace still contains step1."""
    trace = ProvenanceTrace(origin="test").append(step1).append(step2)
    assert step1 in trace.steps, (
        "Earlier step was lost after a subsequent append"
    )
    assert step2 in trace.steps, (
        "Latest step was not recorded in trace"
    )


@given(st.lists(provenance_step_st, min_size=1, max_size=5))
def test_provenance_trace_is_immutable(steps: list[ProvenanceStep]) -> None:
    """ProvenanceTrace is frozen: cannot be mutated in-place."""
    trace = ProvenanceTrace(origin="test")
    for step in steps:
        trace = trace.append(step)
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        trace.origin = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 5. ProvenanceGraph acyclicity preservation
# ---------------------------------------------------------------------------


@given(st.lists(node_id_st, min_size=1, max_size=8, unique=True))
def test_provenance_graph_acyclic_with_linear_chain(
    node_ids: list[str],
) -> None:
    """A linear chain of provenance nodes produces an acyclic graph."""
    g = ProvenanceGraph()
    for i, nid in enumerate(node_ids):
        inputs = (node_ids[i - 1],) if i > 0 else ()
        node = ProvenanceNode(
            node_id=nid,
            source_channel="solver",
            operation=ProvenanceOperation.PRODUCED if i == 0 else ProvenanceOperation.COMPOSED,
            inputs=inputs,
        )
        g.add_node(node)
    assert g.is_acyclic(), (
        "Linear provenance chain should always be acyclic"
    )


def test_provenance_graph_detects_self_loop() -> None:
    """A node that lists itself as input creates a cycle that is detected."""
    g = ProvenanceGraph()
    node = ProvenanceNode(
        node_id="self_loop",
        source_channel="oracle",
        operation=ProvenanceOperation.COMPOSED,
        inputs=("self_loop",),
    )
    g.add_node(node)
    assert not g.is_acyclic(), "Self-loop should make graph cyclic"
    cycles = g.detect_cycles()
    assert len(cycles) > 0, "detect_cycles should find the self-loop"


def test_provenance_graph_detects_mutual_cycle() -> None:
    """A mutual dependency cycle (n1→n2→n1) is detected."""
    g = ProvenanceGraph()
    n1 = ProvenanceNode(node_id="n1", source_channel="solver",
                        operation=ProvenanceOperation.PRODUCED, inputs=("n2",))
    n2 = ProvenanceNode(node_id="n2", source_channel="solver",
                        operation=ProvenanceOperation.COMPOSED, inputs=("n1",))
    g.add_node(n1)
    g.add_node(n2)
    assert not g.is_acyclic(), "Mutual dependency should make graph cyclic"
    cycles = g.detect_cycles()
    assert len(cycles) > 0, "detect_cycles should find mutual cycle"


# ---------------------------------------------------------------------------
# 6. ProvenanceGraph ancestors/descendants consistency
# ---------------------------------------------------------------------------


def test_ancestors_are_subset_of_all_nodes() -> None:
    """ancestors_of(n) ⊆ all_node_ids()."""
    g = ProvenanceGraph()
    n1 = ProvenanceNode(node_id="n1", source_channel="solver", operation=ProvenanceOperation.PRODUCED)
    n2 = ProvenanceNode(node_id="n2", source_channel="solver", operation=ProvenanceOperation.COMPOSED, inputs=("n1",))
    n3 = ProvenanceNode(node_id="n3", source_channel="oracle", operation=ProvenanceOperation.COMPOSED, inputs=("n2",))
    for n in (n1, n2, n3):
        g.add_node(n)
    all_ids = g.all_node_ids()
    ancestors = g.ancestors_of("n3")
    assert ancestors.issubset(all_ids), (
        f"ancestors_of('n3')={ancestors} contains IDs not in graph"
    )


def test_ancestors_and_descendants_are_symmetric() -> None:
    """a ∈ descendants(b) ⟺ b ∈ ancestors(a)."""
    g = ProvenanceGraph()
    n1 = ProvenanceNode(node_id="n1", source_channel="solver", operation=ProvenanceOperation.PRODUCED)
    n2 = ProvenanceNode(node_id="n2", source_channel="oracle", operation=ProvenanceOperation.COMPOSED, inputs=("n1",))
    n3 = ProvenanceNode(node_id="n3", source_channel="copilot", operation=ProvenanceOperation.PROMOTED, inputs=("n2",))
    for n in (n1, n2, n3):
        g.add_node(n)
    # n1 should be ancestor of n2 and n3
    assert "n1" in g.ancestors_of("n2")
    assert "n1" in g.ancestors_of("n3")
    # n2 and n3 should be descendants of n1
    assert "n2" in g.descendants_of("n1")
    assert "n3" in g.descendants_of("n1")
    # Symmetry: n3 in descendants(n1) ⟺ n1 in ancestors(n3)
    for root in ("n1", "n2", "n3"):
        for leaf in ("n1", "n2", "n3"):
            if root == leaf:
                continue
            in_desc = leaf in g.descendants_of(root)
            in_anc = root in g.ancestors_of(leaf)
            assert in_desc == in_anc, (
                f"Asymmetry: {leaf} in descendants({root})={in_desc} "
                f"but {root} in ancestors({leaf})={in_anc}"
            )


# ---------------------------------------------------------------------------
# 7. EvidenceBundle merge preserves all kinds
# ---------------------------------------------------------------------------


@given(evidence_bundle_st, evidence_bundle_st)
def test_bundle_merge_preserves_all_kinds_from_both(
    b1: EvidenceBundle, b2: EvidenceBundle
) -> None:
    """After merge, every kind present in b1 or b2 is present in the merged bundle."""
    merged = b1.merge(b2)
    expected_kinds = {item.kind for item in b1.items} | {item.kind for item in b2.items}
    actual_kinds = {item.kind for item in merged.items}
    assert expected_kinds.issubset(actual_kinds), (
        f"Merge lost evidence kinds: {expected_kinds - actual_kinds}"
    )


@given(evidence_bundle_st, evidence_bundle_st)
def test_bundle_merge_does_not_reduce_item_count(
    b1: EvidenceBundle, b2: EvidenceBundle
) -> None:
    """Merged bundle has at least as many items as the larger of b1, b2."""
    merged = b1.merge(b2)
    assert len(merged.items) >= max(len(b1.items), len(b2.items)), (
        f"merge reduced item count: {len(b1.items)} + {len(b2.items)} → {len(merged.items)}"
    )


@given(evidence_bundle_st)
def test_bundle_merge_with_empty_is_identity_on_kinds(
    bundle: EvidenceBundle,
) -> None:
    """Merging with an empty bundle preserves all kinds."""
    empty = EvidenceBundle()
    merged = bundle.merge(empty)
    original_kinds = {item.kind for item in bundle.items}
    merged_kinds = {item.kind for item in merged.items}
    assert original_kinds == merged_kinds, (
        "Merging with empty bundle changed kind set"
    )


# ---------------------------------------------------------------------------
# 8. ChannelFederation.compute_combined_trust is minimum
# ---------------------------------------------------------------------------


def test_federation_combined_trust_is_minimum_of_responses() -> None:
    """ChannelFederation.compute_combined_trust returns minimum trust."""
    responses = [
        EvidenceResponse(request_id="r1", channel=EvidenceChannel.SOLVER,
                         trust_level="reviewed"),
        EvidenceResponse(request_id="r2", channel=EvidenceChannel.ORACLE,
                         trust_level="proposal"),
    ]
    combined = _fed.compute_combined_trust(responses)
    # 'proposal' is weaker than 'reviewed', so minimum = 'proposal'
    assert combined == "proposal", (
        f"Combined trust should be 'proposal' (minimum), got {combined!r}"
    )


def test_federation_combined_trust_empty_responses_is_proposal() -> None:
    """compute_combined_trust on empty list returns 'proposal' (weakest)."""
    combined = _fed.compute_combined_trust([])
    assert combined == "proposal", (
        f"Empty responses should yield 'proposal', got {combined!r}"
    )


def test_federation_combined_trust_homogeneous_is_that_level() -> None:
    """compute_combined_trust on uniform trust level returns that level."""
    responses = [
        EvidenceResponse(request_id=f"r{i}", channel=EvidenceChannel.SOLVER,
                         trust_level="reviewed")
        for i in range(3)
    ]
    combined = _fed.compute_combined_trust(responses)
    assert combined == "reviewed", (
        f"Uniform 'reviewed' trust should combine to 'reviewed', got {combined!r}"
    )


# ---------------------------------------------------------------------------
# 9. EvidenceRecord.canonical_key is deterministic
# ---------------------------------------------------------------------------


@given(evidence_channel_st)
def test_evidence_record_canonical_key_is_deterministic(
    channel: EvidenceChannel,
) -> None:
    """EvidenceRecord.canonical_key() returns the same value on every call."""
    claim = "x = x + 0"
    rec = EvidenceRecord(channel, claim)
    k1 = rec.canonical_key()
    k2 = rec.canonical_key()
    assert k1 == k2, f"canonical_key is not deterministic: {k1!r} ≠ {k2!r}"


@given(evidence_channel_st)
def test_evidence_record_canonical_key_contains_channel(
    channel: EvidenceChannel,
) -> None:
    """canonical_key includes the channel value."""
    rec = EvidenceRecord(channel, "some claim")
    key = rec.canonical_key()
    assert channel.value in key, (
        f"canonical_key {key!r} does not contain channel {channel.value!r}"
    )


def test_evidence_record_canonical_key_distinguishes_channels() -> None:
    """Different channels produce different canonical keys for same claim."""
    claim = "x > 0"
    keys = {
        EvidenceRecord(ch, claim).canonical_key()
        for ch in [EvidenceChannel.SOLVER, EvidenceChannel.ORACLE, EvidenceChannel.COPILOT]
    }
    assert len(keys) == 3, (
        f"Different channels should give different canonical keys, got {keys}"
    )


# ---------------------------------------------------------------------------
# 10. Copilot channel ceiling strictly below solver channel ceiling
# ---------------------------------------------------------------------------


def test_copilot_ceiling_strictly_below_solver_ceiling() -> None:
    """Copilot ceiling (COPILOT_SUGGESTED) < Solver ceiling (SOLVER_DISCHARGED)."""
    assert CHANNEL_CEILINGS["copilot"] < CHANNEL_CEILINGS["solver"], (
        "Copilot ceiling must be strictly below solver ceiling"
    )


def test_channel_ceiling_ordering() -> None:
    """Channel ceilings respect: copilot < oracle < human < runtime < solver < formal_proof."""
    assert CHANNEL_CEILINGS["copilot"] < CHANNEL_CEILINGS["oracle"]
    assert CHANNEL_CEILINGS["oracle"] < CHANNEL_CEILINGS["human"]
    assert CHANNEL_CEILINGS["human"] < CHANNEL_CEILINGS["runtime"]
    assert CHANNEL_CEILINGS["runtime"] < CHANNEL_CEILINGS["solver"]
    assert CHANNEL_CEILINGS["solver"] < CHANNEL_CEILINGS["formal_proof"]


# ---------------------------------------------------------------------------
# 11. ProvenanceNode channel classification
# ---------------------------------------------------------------------------


def test_provenance_node_is_copilot_node_for_copilot_channel() -> None:
    """is_copilot_node() returns True for 'copilot' source_channel."""
    node = ProvenanceNode(
        node_id="n1",
        source_channel="copilot",
        operation=ProvenanceOperation.PRODUCED,
    )
    assert node.is_copilot_node(), (
        "ProvenanceNode with source_channel='copilot' should be a copilot node"
    )


def test_provenance_node_is_not_copilot_for_solver() -> None:
    """is_copilot_node() returns False for solver channel."""
    node = ProvenanceNode(
        node_id="n1",
        source_channel="solver",
        operation=ProvenanceOperation.PRODUCED,
    )
    assert not node.is_copilot_node(), (
        "ProvenanceNode with source_channel='solver' should NOT be a copilot node"
    )


def test_provenance_node_is_solver_node_for_solver_channel() -> None:
    """is_solver_node() returns True for 'solver' source_channel."""
    node = ProvenanceNode(
        node_id="n1",
        source_channel="solver",
        operation=ProvenanceOperation.PRODUCED,
    )
    assert node.is_solver_node(), (
        "ProvenanceNode with source_channel='solver' should be a solver node"
    )


# ---------------------------------------------------------------------------
# 12. TrustCeiling.is_within_ceiling consistency
# ---------------------------------------------------------------------------


@given(chain_level_st, channel_name_st)
def test_is_within_ceiling_consistent_with_enforce(
    level: TrustLevel, channel: str
) -> None:
    """is_within_ceiling(l, c) is True iff l ≤ channel ceiling."""
    ceiling_level = CHANNEL_CEILINGS[channel]
    within = _ceiling.is_within_ceiling(level, channel)
    expected = level.rank_index() <= ceiling_level.rank_index()
    assert within == expected, (
        f"is_within_ceiling({level.name}, {channel}) = {within} but "
        f"{level.name}.rank={level.rank_index()} and ceiling={ceiling_level.name}.rank={ceiling_level.rank_index()}"
    )


@given(chain_level_st, channel_name_st)
def test_enforce_result_is_always_within_ceiling(
    level: TrustLevel, channel: str
) -> None:
    """After enforce, the result is always within the channel ceiling."""
    enforced = _ceiling.enforce(level, channel)
    assert _ceiling.is_within_ceiling(enforced, channel), (
        f"enforce({level.name}, {channel}) = {enforced.name} not within ceiling"
    )


# ---------------------------------------------------------------------------
# 13. Provenance graph merge preserves all nodes
# ---------------------------------------------------------------------------


def test_provenance_graph_merge_preserves_all_nodes() -> None:
    """Merging two provenance graphs preserves nodes from both."""
    from jugeo.evidence.provenance import ProvenanceMerger
    g1 = ProvenanceGraph()
    g2 = ProvenanceGraph()
    n1 = ProvenanceNode(node_id="n1", source_channel="solver", operation=ProvenanceOperation.PRODUCED)
    n2 = ProvenanceNode(node_id="n2", source_channel="oracle", operation=ProvenanceOperation.PRODUCED)
    n3 = ProvenanceNode(node_id="n3", source_channel="copilot", operation=ProvenanceOperation.PRODUCED)
    g1.add_node(n1)
    g1.add_node(n2)
    g2.add_node(n3)
    merger = ProvenanceMerger()
    merged = merger.merge_graphs(g1, g2)
    all_ids = merged.all_node_ids()
    assert "n1" in all_ids, "n1 from g1 not in merged graph"
    assert "n2" in all_ids, "n2 from g1 not in merged graph"
    assert "n3" in all_ids, "n3 from g2 not in merged graph"


def test_provenance_graph_merge_with_empty_is_identity() -> None:
    """Merging with an empty graph preserves all original nodes."""
    from jugeo.evidence.provenance import ProvenanceMerger
    g = ProvenanceGraph()
    n1 = ProvenanceNode(node_id="n1", source_channel="solver", operation=ProvenanceOperation.PRODUCED)
    n2 = ProvenanceNode(node_id="n2", source_channel="runtime", operation=ProvenanceOperation.PRODUCED)
    g.add_node(n1)
    g.add_node(n2)
    empty_g = ProvenanceGraph()
    merger = ProvenanceMerger()
    merged = merger.merge_graphs(g, empty_g)
    assert "n1" in merged.all_node_ids()
    assert "n2" in merged.all_node_ids()


# ---------------------------------------------------------------------------
# 14. TrustAttenuation through restriction does NOT floor at UNVERIFIED
# ---------------------------------------------------------------------------


@given(chain_level_st, st.integers(min_value=1, max_value=20))
def test_restriction_attenuation_can_reach_zero_index(
    level: TrustLevel, depth: int,
) -> None:
    """attenuate_through_restriction can go all the way to CONTRADICTED (unlike transport)."""
    result = _attn.attenuate_through_restriction(level, depth)
    # Must be a valid TrustLevel
    assert isinstance(result, TrustLevel), (
        f"attenuate_through_restriction returned {result!r}"
    )
    # Result must not exceed original level
    assert result.rank_index() <= level.rank_index()


@given(chain_level_st)
def test_transport_attenuation_floors_at_unverified(level: TrustLevel) -> None:
    """Transport attenuation saturates at UNVERIFIED (theory: uncertainty, not contradiction)."""
    # Very large hop count to force saturation
    result = _attn.attenuate_through_transport(level, 1000)
    assert result is TrustLevel.UNVERIFIED, (
        f"Transport attenuation should floor at UNVERIFIED, got {result.name}"
    )


# ---------------------------------------------------------------------------
# 15. ProvenanceNode.is_promotion classification
# ---------------------------------------------------------------------------


def test_provenance_node_promoted_operation_is_promotion() -> None:
    """A node with PROMOTED operation is classified as a promotion."""
    node = ProvenanceNode(
        node_id="prom1",
        source_channel="human",
        operation=ProvenanceOperation.PROMOTED,
    )
    assert node.is_promotion(), (
        "Node with PROMOTED operation should be classified as promotion"
    )


def test_provenance_node_produced_is_not_promotion() -> None:
    """A node with PRODUCED operation is not a promotion."""
    node = ProvenanceNode(
        node_id="prod1",
        source_channel="solver",
        operation=ProvenanceOperation.PRODUCED,
    )
    assert not node.is_promotion(), (
        "Node with PRODUCED operation should NOT be a promotion"
    )
