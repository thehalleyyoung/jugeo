"""Property-based tests for the JuGeo trust ordered algebra.

This module uses Hypothesis to verify algebraic laws of trust from
``preliminaries/theory2.tex`` §252.  The trust algebra is defined as:

    T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ)

where ⪯ is the partial order, ⊕ is conservative composition (meet),
⊖ is attenuation, ↑_π is promotion with justification, and ↓_χ is
ceiling enforcement.

Key algebraic properties under test:

* Partial order axioms (reflexivity, antisymmetry, transitivity) hold for
  all comparable pairs of TrustLevel values.
* meet(a, b) = meet(b, a)   — commutativity
* meet(meet(a, b), c) = meet(a, meet(b, c))   — associativity
* join(a, b) = join(b, a)   — commutativity
* meet(a, join(a, b)) = a   — absorption (lower)
* join(a, meet(a, b)) = a   — absorption (upper)
* Idempotence: meet(a, a) = a, join(a, a) = a
* TrustAlgebra.compose = TrustAlgebra.meet (compose is meet)
* Attenuation by 0 is identity
* Attenuation is monotone non-increasing
* Promotion requires non-empty justification (raises JuGeoError otherwise)
* Serialization round-trips preserve TrustLevel identity
* TrustProfile (legacy) promotion requires explicit=True

References: theory2.tex §252, §354
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap (mirrors existing test pattern)
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
from jugeo.evidence.trust import (
    TrustLevel,
    TrustAlgebra,
    TrustAttenuation,
    TrustComposition,
    TrustPromotion,
    TrustSerializer,
    TrustTier,
    TrustProfile,
    join_trust_profiles,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# The main trust chain (levels that are mutually comparable).  CONTRADICTED
# is an isolated element in the partial order (not comparable to others via ≤),
# so we split strategies where needed.
CHAIN_LEVELS = [
    TrustLevel.UNVERIFIED,
    TrustLevel.COPILOT_SUGGESTED,
    TrustLevel.ORACLE_PROPOSED,
    TrustLevel.HUMAN_ATTESTED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.MECHANICALLY_VERIFIED,
]

# All levels including CONTRADICTED (use for tests that don't rely on ≤)
ALL_LEVELS = list(TrustLevel)

# Hypothesis strategies
chain_level_st: SearchStrategy[TrustLevel] = st.sampled_from(CHAIN_LEVELS)
any_level_st: SearchStrategy[TrustLevel] = st.sampled_from(ALL_LEVELS)
level_pair_chain_st = st.tuples(chain_level_st, chain_level_st)
level_triple_chain_st = st.tuples(chain_level_st, chain_level_st, chain_level_st)

nonempty_justification_st: SearchStrategy[str] = st.text(
    min_size=1,
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
).filter(lambda s: s.strip())

attenuation_factor_st: SearchStrategy[int] = st.integers(min_value=0, max_value=10)

tier_st: SearchStrategy[TrustTier] = st.sampled_from(list(TrustTier))
scope_st: SearchStrategy[tuple[str, ...]] = st.frozensets(
    st.text(min_size=1, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz_"),
    max_size=4,
).map(tuple)
reason_st: SearchStrategy[tuple[str, ...]] = st.lists(
    st.text(min_size=1, max_size=20), max_size=3
).map(tuple)

_alg = TrustAlgebra()
_attn = TrustAttenuation(algebra=_alg)
_comp = TrustComposition(algebra=_alg)


# ---------------------------------------------------------------------------
# 1. Partial order axioms on the chain (all levels comparable)
# ---------------------------------------------------------------------------


@given(chain_level_st)
def test_reflexivity(a: TrustLevel) -> None:
    """Every trust level is ≤ itself (reflexivity of partial order)."""
    assert a <= a
    assert a >= a
    assert not (a < a)
    assert not (a > a)


@given(level_pair_chain_st)
def test_antisymmetry(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """If a ≤ b and b ≤ a then a = b (antisymmetry)."""
    a, b = pair
    if a <= b and b <= a:
        assert a is b, (
            f"Antisymmetry violation: {a.name} ≤ {b.name} and "
            f"{b.name} ≤ {a.name} but {a.name} ≠ {b.name}"
        )


@given(level_triple_chain_st)
def test_transitivity(triple: tuple[TrustLevel, TrustLevel, TrustLevel]) -> None:
    """If a ≤ b and b ≤ c then a ≤ c (transitivity)."""
    a, b, c = triple
    if a <= b and b <= c:
        assert a <= c, (
            f"Transitivity violation: {a.name} ≤ {b.name}, "
            f"{b.name} ≤ {c.name} but {a.name} ≰ {c.name}"
        )


@given(level_pair_chain_st)
def test_totality_of_chain(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """Chain levels are totally ordered: for any a, b either a ≤ b or b ≤ a."""
    a, b = pair
    assert a <= b or b <= a, (
        f"Chain totality violation: {a.name} and {b.name} are incomparable"
    )


@given(chain_level_st)
def test_rank_index_consistent_with_order(a: TrustLevel) -> None:
    """rank_index() is consistent with the partial order on the chain."""
    for b in CHAIN_LEVELS:
        if a <= b:
            assert a.rank_index() <= b.rank_index(), (
                f"{a.name} ≤ {b.name} but rank({a.name})={a.rank_index()} "
                f"> rank({b.name})={b.rank_index()}"
            )
        if a >= b:
            assert a.rank_index() >= b.rank_index()


# ---------------------------------------------------------------------------
# 2. Meet (⊕) commutativity and associativity
# ---------------------------------------------------------------------------


@given(level_pair_chain_st)
def test_meet_commutativity(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """meet(a, b) = meet(b, a) — commutativity of ⊕."""
    a, b = pair
    assert _alg.meet(a, b) is _alg.meet(b, a), (
        f"meet({a.name}, {b.name}) ≠ meet({b.name}, {a.name})"
    )


@given(level_triple_chain_st)
def test_meet_associativity(triple: tuple[TrustLevel, TrustLevel, TrustLevel]) -> None:
    """meet(meet(a, b), c) = meet(a, meet(b, c)) — associativity of ⊕."""
    a, b, c = triple
    left = _alg.meet(_alg.meet(a, b), c)
    right = _alg.meet(a, _alg.meet(b, c))
    assert left is right, (
        f"meet(meet({a.name},{b.name}),{c.name})={left.name} ≠ "
        f"meet({a.name},meet({b.name},{c.name}))={right.name}"
    )


@given(chain_level_st)
def test_meet_idempotence(a: TrustLevel) -> None:
    """meet(a, a) = a — idempotence of meet."""
    assert _alg.meet(a, a) is a, f"meet({a.name}, {a.name}) ≠ {a.name}"


@given(level_pair_chain_st)
def test_meet_is_lower_bound(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """meet(a, b) ≤ a and meet(a, b) ≤ b (meet is a lower bound)."""
    a, b = pair
    m = _alg.meet(a, b)
    assert m <= a, f"meet({a.name},{b.name})={m.name} ≰ {a.name}"
    assert m <= b, f"meet({a.name},{b.name})={m.name} ≰ {b.name}"


@given(level_pair_chain_st)
def test_meet_is_greatest_lower_bound(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """meet(a, b) is the greatest lower bound: any c ≤ a and c ≤ b implies c ≤ meet(a,b)."""
    a, b = pair
    m = _alg.meet(a, b)
    for c in CHAIN_LEVELS:
        if c <= a and c <= b:
            assert c <= m, (
                f"{c.name} ≤ {a.name} and {c.name} ≤ {b.name} "
                f"but {c.name} ≰ meet({a.name},{b.name})={m.name}"
            )


# ---------------------------------------------------------------------------
# 3. Join (⊔) commutativity and absorption
# ---------------------------------------------------------------------------


@given(level_pair_chain_st)
def test_join_commutativity(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """join(a, b) = join(b, a) — commutativity of ⊔."""
    a, b = pair
    assert _alg.join(a, b) is _alg.join(b, a), (
        f"join({a.name},{b.name}) ≠ join({b.name},{a.name})"
    )


@given(chain_level_st)
def test_join_idempotence(a: TrustLevel) -> None:
    """join(a, a) = a — idempotence of join."""
    assert _alg.join(a, a) is a, f"join({a.name},{a.name}) ≠ {a.name}"


@given(level_pair_chain_st)
def test_join_is_upper_bound(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """a ≤ join(a, b) and b ≤ join(a, b) (join is an upper bound)."""
    a, b = pair
    j = _alg.join(a, b)
    assert a <= j, f"{a.name} ≰ join({a.name},{b.name})={j.name}"
    assert b <= j, f"{b.name} ≰ join({a.name},{b.name})={j.name}"


@given(level_pair_chain_st)
def test_absorption_meet_of_join(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """meet(a, join(a, b)) = a — lower absorption law."""
    a, b = pair
    j = _alg.join(a, b)
    m = _alg.meet(a, j)
    assert m is a, (
        f"Absorption violation: meet({a.name}, join({a.name},{b.name}))="
        f"{m.name} ≠ {a.name}"
    )


@given(level_pair_chain_st)
def test_absorption_join_of_meet(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """join(a, meet(a, b)) = a — upper absorption law."""
    a, b = pair
    m = _alg.meet(a, b)
    j = _alg.join(a, m)
    assert j is a, (
        f"Upper absorption violation: join({a.name}, meet({a.name},{b.name}))="
        f"{j.name} ≠ {a.name}"
    )


# ---------------------------------------------------------------------------
# 4. compose = meet
# ---------------------------------------------------------------------------


@given(level_pair_chain_st)
def test_compose_equals_meet(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """TrustAlgebra.compose(a, b) = TrustAlgebra.meet(a, b)."""
    a, b = pair
    assert _alg.compose(a, b) is _alg.meet(a, b), (
        f"compose({a.name},{b.name}) ≠ meet({a.name},{b.name})"
    )


@given(level_triple_chain_st)
def test_composition_associativity_via_compose(triple: tuple[TrustLevel, TrustLevel, TrustLevel]) -> None:
    """TrustComposition.associativity_check passes for all chain triples."""
    a, b, c = triple
    assert _comp.associativity_check(a, b, c), (
        f"Composition not associative for ({a.name},{b.name},{c.name})"
    )


# ---------------------------------------------------------------------------
# 5. Attenuation properties
# ---------------------------------------------------------------------------


@given(chain_level_st)
def test_attenuation_by_zero_is_identity(a: TrustLevel) -> None:
    """Attenuating by 0 steps returns the same level."""
    assert _alg.attenuate(a, 0) is a, (
        f"attenuate({a.name}, 0) ≠ {a.name}"
    )


@given(chain_level_st, attenuation_factor_st)
def test_attenuation_is_non_increasing(a: TrustLevel, factor: int) -> None:
    """Attenuation never raises trust level."""
    result = _alg.attenuate(a, factor)
    # result should be ≤ a in the linearized order
    assert result.rank_index() <= a.rank_index(), (
        f"attenuate({a.name}, {factor}) = {result.name} raised trust"
    )


@given(chain_level_st, attenuation_factor_st, attenuation_factor_st)
def test_attenuation_cumulative(a: TrustLevel, f1: int, f2: int) -> None:
    """Attenuating by f1 then f2 equals attenuating by f1+f2."""
    step1 = _alg.attenuate(a, f1)
    step2 = _alg.attenuate(step1, f2)
    combined = _alg.attenuate(a, f1 + f2)
    assert step2 is combined, (
        f"attenuate(attenuate({a.name},{f1}),{f2})={step2.name} ≠ "
        f"attenuate({a.name},{f1+f2})={combined.name}"
    )


@given(chain_level_st, attenuation_factor_st)
def test_attenuation_saturates_at_unverified_for_transport(
    a: TrustLevel, hops: int
) -> None:
    """Transport attenuation saturates at UNVERIFIED, not CONTRADICTED."""
    result = _attn.attenuate_through_transport(a, hops)
    assert result >= TrustLevel.UNVERIFIED or result is TrustLevel.UNVERIFIED, (
        f"Transport attenuation went below UNVERIFIED: {result.name}"
    )


# ---------------------------------------------------------------------------
# 6. Promotion requires explicit justification
# ---------------------------------------------------------------------------


def test_promote_raises_on_empty_justification() -> None:
    """promote() with empty string raises JuGeoError."""
    with pytest.raises(JuGeoError):
        _alg.promote(TrustLevel.ORACLE_PROPOSED, "")


def test_promote_raises_on_whitespace_only_justification() -> None:
    """promote() with whitespace-only string raises JuGeoError."""
    with pytest.raises(JuGeoError):
        _alg.promote(TrustLevel.ORACLE_PROPOSED, "   ")


@given(nonempty_justification_st)
def test_promote_with_valid_justification_succeeds(reason: str) -> None:
    """promote() with non-empty justification does not raise."""
    result = _alg.promote(TrustLevel.ORACLE_PROPOSED, reason)
    assert result is not None
    # Result should be strictly above ORACLE_PROPOSED or same (if already at top)
    assert result.rank_index() >= TrustLevel.ORACLE_PROPOSED.rank_index()


@given(nonempty_justification_st)
def test_promote_advances_by_exactly_one_step(reason: str) -> None:
    """Promotion advances by exactly one step in the linearized order."""
    levels = list(TrustLevel.ordered())
    for lvl in levels[:-1]:  # all except top
        promoted = _alg.promote(lvl, reason)
        assert promoted.rank_index() == lvl.rank_index() + 1, (
            f"Promotion from {lvl.name} advanced by wrong number of steps"
        )


def test_promote_at_top_returns_top() -> None:
    """Promoting from MECHANICALLY_VERIFIED stays at MECHANICALLY_VERIFIED."""
    top = TrustLevel.MECHANICALLY_VERIFIED
    result = _alg.promote(top, "explicit promotion at top")
    assert result is top


# ---------------------------------------------------------------------------
# 7. Demotion (ceiling enforcement)
# ---------------------------------------------------------------------------


@given(level_pair_chain_st)
def test_demote_enforces_ceiling(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """demote(t, ceiling) ≤ ceiling always."""
    t, ceiling = pair
    result = _alg.demote(t, ceiling)
    assert result.rank_index() <= ceiling.rank_index(), (
        f"demote({t.name},{ceiling.name})={result.name} exceeds ceiling"
    )


@given(chain_level_st)
def test_demote_is_identity_when_below_ceiling(a: TrustLevel) -> None:
    """demote(a, top) = a since a ≤ top always."""
    top = TrustLevel.MECHANICALLY_VERIFIED
    assert _alg.demote(a, top) is a, (
        f"demote({a.name}, top) should be identity but got {_alg.demote(a, top).name}"
    )


@given(chain_level_st)
def test_demote_oracle_ceiling_from_high_trust(a: TrustLevel) -> None:
    """demote(a, ORACLE_PROPOSED) ≤ ORACLE_PROPOSED for any a."""
    ceiling = TrustLevel.ORACLE_PROPOSED
    result = _alg.demote(a, ceiling)
    assert result.rank_index() <= ceiling.rank_index()


# ---------------------------------------------------------------------------
# 8. Lattice bounds
# ---------------------------------------------------------------------------


@given(chain_level_st)
def test_bottom_is_below_all_chain_levels(a: TrustLevel) -> None:
    """CONTRADICTED special: bottom() returns CONTRADICTED but CONTRADICTED
    is isolated in the partial order.  The UNVERIFIED level is the practical
    minimum for the main chain."""
    assert _alg.bottom() is TrustLevel.CONTRADICTED


@given(chain_level_st)
def test_top_is_above_all_chain_levels(a: TrustLevel) -> None:
    """Every chain level is ≤ MECHANICALLY_VERIFIED."""
    assert a <= _alg.top(), f"{a.name} ≰ top (MECHANICALLY_VERIFIED)"


def test_meet_bottom_with_chain_is_contradicted() -> None:
    """meet(UNVERIFIED, CONTRADICTED) = CONTRADICTED since it's the only common lower bound."""
    # CONTRADICTED is isolated so meet finds it as the candidate via reversed scan
    result = _alg.meet(TrustLevel.UNVERIFIED, TrustLevel.CONTRADICTED)
    assert result is TrustLevel.CONTRADICTED


# ---------------------------------------------------------------------------
# 9. TrustComposition multi-level properties
# ---------------------------------------------------------------------------


@given(st.lists(chain_level_st, min_size=1, max_size=8))
def test_compose_all_equals_iterated_meet(levels: list[TrustLevel]) -> None:
    """compose_all is the iterated meet from left to right."""
    result = _comp.compose_all(levels)
    expected = levels[0]
    for lvl in levels[1:]:
        expected = _alg.meet(expected, lvl)
    assert result is expected


@given(st.lists(chain_level_st, min_size=2, max_size=6))
def test_compose_heterogeneous_leq_each_input(levels: list[TrustLevel]) -> None:
    """compose_heterogeneous result ≤ every input level."""
    result = _comp.compose_heterogeneous(levels)
    for lvl in levels:
        assert result.rank_index() <= lvl.rank_index(), (
            f"compose_heterogeneous result {result.name} > {lvl.name}"
        )


@given(chain_level_st)
def test_compose_homogeneous_is_identity(a: TrustLevel) -> None:
    """compose_homogeneous([a, a, a]) = a."""
    result = _comp.compose_homogeneous([a, a, a])
    assert result is a


# ---------------------------------------------------------------------------
# 10. Serialization round-trips
# ---------------------------------------------------------------------------


@given(any_level_st)
def test_serialize_round_trip_level(level: TrustLevel) -> None:
    """TrustSerializer preserves TrustLevel identity through label round-trip."""
    serializer = TrustSerializer()
    label = serializer.serialize_level(level)
    recovered = serializer.deserialize_level(label)
    assert recovered is level, (
        f"Round-trip failure: {level.name} → '{label}' → {recovered.name}"
    )


@given(any_level_st)
def test_level_label_round_trip(level: TrustLevel) -> None:
    """TrustLevel.label() and TrustLevel.from_label() are inverses."""
    label = level.label()
    recovered = TrustLevel.from_label(label)
    assert recovered is level


@given(st.lists(any_level_st, min_size=1, max_size=6))
def test_serialize_levels_round_trip(levels: list[TrustLevel]) -> None:
    """Serializing a list of levels round-trips exactly."""
    serializer = TrustSerializer()
    serialized = serializer.serialize_levels(levels)
    recovered = serializer.deserialize_levels(serialized)
    assert len(recovered) == len(levels)
    for orig, rec in zip(levels, recovered):
        assert rec is orig, f"Level {orig.name} did not round-trip (got {rec.name})"


# ---------------------------------------------------------------------------
# 11. TrustProfile (legacy) — no silent promotion
# ---------------------------------------------------------------------------


@given(tier_st)
def test_trust_profile_promotion_requires_explicit_flag(tier: TrustTier) -> None:
    """TrustProfile.promote(target, explicit=False) raises JuGeoError when
    promoting to a higher tier."""
    profile = TrustProfile(tier)
    # Only try to promote if we can go higher
    if tier is TrustTier.VERIFIED:
        return  # Already at top; nothing to test
    higher = tier.step_stronger()
    with pytest.raises(JuGeoError) as exc_info:
        profile.promote(higher, explicit=False)
    assert exc_info.value.failure.scope.value == "evidence"


@given(tier_st)
def test_trust_profile_promotion_with_explicit_true_succeeds(tier: TrustTier) -> None:
    """TrustProfile.promote(target, explicit=True) succeeds without error."""
    profile = TrustProfile(tier)
    target = tier.step_stronger()  # stays same if already at top
    promoted = profile.promote(target, explicit=True)
    assert promoted.tier == target


@given(tier_st, scope_st, reason_st)
def test_trust_profile_challenge_demotes(
    tier: TrustTier,
    scope: tuple[str, ...],
    reasons: tuple[str, ...],
) -> None:
    """TrustProfile.challenge() produces a profile with tier ≤ original."""
    profile = TrustProfile(tier, scope, reasons)
    challenged = profile.challenge(reason="test-challenge")
    assert challenged.tier <= profile.tier, (
        f"Challenge raised tier from {profile.tier.name} to {challenged.tier.name}"
    )


@given(tier_st, scope_st)
def test_trust_profile_join_is_conservative(
    tier: TrustTier,
    scope: tuple[str, ...],
) -> None:
    """join of two profiles yields tier ≤ min of inputs."""
    p1 = TrustProfile(TrustTier.VERIFIED, scope)
    p2 = TrustProfile(tier, scope)
    joined = p1.join(p2)
    assert joined.tier <= min(TrustTier.VERIFIED, tier), (
        f"join elevated tier above conservative minimum"
    )


@given(st.lists(tier_st, min_size=1, max_size=5))
def test_join_trust_profiles_result_leq_all_inputs(
    tiers: list[TrustTier],
) -> None:
    """join_trust_profiles result tier ≤ every input tier."""
    profiles = [TrustProfile(t) for t in tiers]
    joined = join_trust_profiles(*profiles)
    for p in profiles:
        assert joined.tier <= p.tier, (
            f"join_trust_profiles result {joined.tier.name} > {p.tier.name}"
        )


# ---------------------------------------------------------------------------
# 12. Oracle tier ceiling property
# ---------------------------------------------------------------------------


def test_oracle_tier_never_exceeds_solver_proof_ceiling() -> None:
    """For all t in the oracle domain, demote(t, SOLVER_DISCHARGED) ≤ SOLVER_DISCHARGED."""
    oracle_ceiling = TrustLevel.ORACLE_PROPOSED
    proof_ceiling = TrustLevel.SOLVER_DISCHARGED
    result = _alg.demote(oracle_ceiling, proof_ceiling)
    assert result is oracle_ceiling  # oracle is already below solver


def test_copilot_suggested_below_oracle_proposed() -> None:
    """COPILOT_SUGGESTED < ORACLE_PROPOSED in the partial order."""
    assert TrustLevel.COPILOT_SUGGESTED < TrustLevel.ORACLE_PROPOSED


def test_oracle_proposed_below_solver_discharged() -> None:
    """ORACLE_PROPOSED < SOLVER_DISCHARGED."""
    assert TrustLevel.ORACLE_PROPOSED < TrustLevel.SOLVER_DISCHARGED


def test_solver_discharged_below_mechanically_verified() -> None:
    """SOLVER_DISCHARGED < MECHANICALLY_VERIFIED."""
    assert TrustLevel.SOLVER_DISCHARGED < TrustLevel.MECHANICALLY_VERIFIED


@given(chain_level_st)
def test_oracle_ceiling_enforcement_for_all_levels(a: TrustLevel) -> None:
    """After demoting to ORACLE_PROPOSED ceiling, result is never above it."""
    ceiling = TrustLevel.ORACLE_PROPOSED
    result = _alg.demote(a, ceiling)
    # result should be in the set {UNVERIFIED, COPILOT_SUGGESTED, ORACLE_PROPOSED}
    allowed = {TrustLevel.UNVERIFIED, TrustLevel.COPILOT_SUGGESTED, TrustLevel.ORACLE_PROPOSED}
    # Since demote goes to meet(a, ceiling) or ceiling:
    assert result.rank_index() <= ceiling.rank_index()


# ---------------------------------------------------------------------------
# 13. TrustAttenuation through provenance hops
# ---------------------------------------------------------------------------


@given(chain_level_st, st.lists(st.text(min_size=1, max_size=10), min_size=0, max_size=6))
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_attenuation_per_hop_trace_length(a: TrustLevel, hops: list[str]) -> None:
    """attenuate_per_hop trace has exactly len(hops) entries."""
    final, trace = _attn.attenuate_per_hop(a, hops)
    assert len(trace) == len(hops), (
        f"Trace length {len(trace)} ≠ {len(hops)} hops"
    )


@given(chain_level_st, st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=6))
def test_attenuation_per_hop_monotone_trace(a: TrustLevel, hops: list[str]) -> None:
    """Each step in the attenuation trace is ≤ the previous step."""
    _, trace = _attn.attenuate_per_hop(a, hops)
    prev_rank = a.rank_index()
    for hop_name, level in trace:
        assert level.rank_index() <= prev_rank, (
            f"Hop '{hop_name}' raised trust from rank {prev_rank} to {level.rank_index()}"
        )
        prev_rank = level.rank_index()


# ---------------------------------------------------------------------------
# 14. is_comparable symmetry
# ---------------------------------------------------------------------------


@given(level_pair_chain_st)
def test_is_comparable_symmetric(pair: tuple[TrustLevel, TrustLevel]) -> None:
    """a.is_comparable(b) ⟺ b.is_comparable(a)."""
    a, b = pair
    assert a.is_comparable(b) == b.is_comparable(a)


@given(chain_level_st)
def test_self_comparable(a: TrustLevel) -> None:
    """Every level is comparable to itself."""
    assert a.is_comparable(a)


# ---------------------------------------------------------------------------
# 15. Lattice distributivity (partial)
# ---------------------------------------------------------------------------


@given(level_triple_chain_st)
def test_meet_distributes_over_join(triple: tuple[TrustLevel, TrustLevel, TrustLevel]) -> None:
    """meet(a, join(b, c)) = join(meet(a, b), meet(a, c)) — distributive lattice."""
    a, b, c = triple
    lhs = _alg.meet(a, _alg.join(b, c))
    rhs = _alg.join(_alg.meet(a, b), _alg.meet(a, c))
    assert lhs is rhs, (
        f"Distributivity violation: meet({a.name},join({b.name},{c.name}))={lhs.name} ≠ "
        f"join(meet({a.name},{b.name}),meet({a.name},{c.name}))={rhs.name}"
    )


@given(level_triple_chain_st)
def test_join_distributes_over_meet(triple: tuple[TrustLevel, TrustLevel, TrustLevel]) -> None:
    """join(a, meet(b, c)) = meet(join(a, b), join(a, c)) — distributive lattice."""
    a, b, c = triple
    lhs = _alg.join(a, _alg.meet(b, c))
    rhs = _alg.meet(_alg.join(a, b), _alg.join(a, c))
    assert lhs is rhs, (
        f"Join distributivity violation: "
        f"join({a.name},meet({b.name},{c.name}))={lhs.name} ≠ "
        f"meet(join({a.name},{b.name}),join({a.name},{c.name}))={rhs.name}"
    )


# ---------------------------------------------------------------------------
# 16. ordered() total ordering consistency
# ---------------------------------------------------------------------------


def test_ordered_returns_all_levels() -> None:
    """TrustLevel.ordered() returns all 8 trust levels exactly once."""
    ordered = TrustLevel.ordered()
    assert len(ordered) == 8
    assert set(ordered) == set(TrustLevel)


def test_ordered_is_weakest_to_strongest() -> None:
    """TrustLevel.ordered() starts at CONTRADICTED and ends at MECHANICALLY_VERIFIED."""
    ordered = TrustLevel.ordered()
    assert ordered[0] is TrustLevel.CONTRADICTED
    assert ordered[-1] is TrustLevel.MECHANICALLY_VERIFIED


def test_rank_index_is_position_in_ordered() -> None:
    """rank_index matches position in TrustLevel.ordered()."""
    for i, level in enumerate(TrustLevel.ordered()):
        assert level.rank_index() == i, (
            f"{level.name}.rank_index()={level.rank_index()} ≠ position {i}"
        )
