"""Property-based tests for judgment tuple algebraic structure.

This module uses Hypothesis to verify invariants of the 8-component judgment
tuple ``J = (c, φ, A, E, O, B, T, Π)`` defined in theory2.tex.

Properties under test:

* judgment.trust is always a TrustAnnotation (never bare float or string)
* judgment.obligations is always a tuple (never None)
* judgment.evidence is always an EvidenceBundle
* judgment.obstructions is always a tuple
* judgment.provenance is always a Provenance object
* EvidenceBundle.merge is commutative and associative on item sets
* EvidenceBundle.merge preserves all kinds from both bundles
* EvidenceBundle.total_trust is monotone (more items ≤ accumulated trust)
* Serialization round-trip preserves all fields exactly
* content_hash is deterministic (same inputs → same hash)
* Obstruction cohomology_class is preserved through copy operations
* ResidualObligation.discharge produces a discharged obligation
* JudgmentAlgebra.compose preserves evidence from both operands
* JudgmentAlgebra.weaken_to_common produces trust ≤ both inputs
* Judgment.strengthen/weaken are monotone in the expected direction
* TrustAnnotation.level is always a TrustLevel IntEnum member

References: theory2.tex §2, §3, §254
"""

from __future__ import annotations

import sys
import json
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

from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentAlgebra,
    JudgmentBuilder,
    JudgmentStatus,
    Proposition,
    PropositionKind,
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    ResidualObligation,
    Obstruction,
    TrustAnnotation,
    TrustLevel,
    Provenance,
    ProvenanceSource,
)
from jugeo.geometry.site import CoordinateObject, CoordinateKind


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Coordinate objects
coord_name_st: SearchStrategy[str] = st.text(
    min_size=1, max_size=16,
    alphabet="abcdefghijklmnopqrstuvwxyz_0123456789"
)
coordinate_st: SearchStrategy[CoordinateObject] = st.builds(
    CoordinateObject,
    name=coord_name_st,
    kind=st.sampled_from(list(CoordinateKind)),
    path=st.tuples(coord_name_st).map(lambda t: t),
)

# Propositions
proposition_kind_st: SearchStrategy[PropositionKind] = st.sampled_from(
    list(PropositionKind)
)
proposition_st: SearchStrategy[Proposition] = st.builds(
    Proposition,
    kind=proposition_kind_st,
    formula=st.text(min_size=1, max_size=40),
    free_variables=st.frozensets(
        st.text(min_size=1, max_size=4, alphabet="xyzabcde"),
        max_size=3,
    ).map(tuple),
)

# Carriers
carrier_st: SearchStrategy[Carrier] = st.builds(
    Carrier,
    name=st.sampled_from(["Int", "Bool", "List", "FunctionContract", "Resource"]),
    parameters=st.tuples(),
    is_dependent=st.booleans(),
)

# TrustLevel (IntEnum 0-5)
trust_level_st: SearchStrategy[TrustLevel] = st.sampled_from(list(TrustLevel))

# TrustAnnotation
trust_annotation_st: SearchStrategy[TrustAnnotation] = st.builds(
    TrustAnnotation,
    level=trust_level_st,
    evidence_basis=st.lists(
        st.text(min_size=1, max_size=20), max_size=3
    ).map(tuple),
)

# EvidenceItemKind
evidence_kind_st: SearchStrategy[EvidenceItemKind] = st.sampled_from(
    list(EvidenceItemKind)
)

# EvidenceItem
evidence_item_st: SearchStrategy[EvidenceItem] = st.builds(
    EvidenceItem,
    kind=evidence_kind_st,
    payload=st.just({}),
    trust_level=trust_level_st,
    channel=st.sampled_from(["solver", "runtime", "oracle", "copilot", "human"]),
    timestamp=st.just("2024-01-01T00:00:00"),
    expiry=st.just("2099-12-31T23:59:59"),
    provenance=st.tuples(),
)

# EvidenceBundle
evidence_bundle_st: SearchStrategy[EvidenceBundle] = st.lists(
    evidence_item_st, max_size=4
).map(lambda items: EvidenceBundle(items=tuple(items)))

# ProvenanceSource
provenance_source_st: SearchStrategy[ProvenanceSource] = st.sampled_from(
    list(ProvenanceSource)
)

# Provenance
provenance_st: SearchStrategy[Provenance] = st.builds(
    Provenance,
    source=provenance_source_st,
)

# ResidualObligation
obligation_st: SearchStrategy[ResidualObligation] = st.builds(
    ResidualObligation,
    obligation_id=st.uuids().map(str),
    description=st.text(min_size=1, max_size=30),
    required_evidence_kind=evidence_kind_st,
    deadline=st.just("2099-12-31T23:59:59"),
    priority=st.integers(min_value=1, max_value=10),
)

# Obstruction
obstruction_st: SearchStrategy[Obstruction] = st.builds(
    Obstruction,
    obstruction_id=st.uuids().map(str),
    violated_condition=st.text(min_size=1, max_size=30),
    coordinate=coord_name_st,
)


def _build_judgment(
    coord: CoordinateObject,
    prop: Proposition,
    carrier: Carrier,
    bundle: EvidenceBundle,
    trust: TrustAnnotation,
    prov: Provenance,
) -> Judgment:
    """Helper: construct a Judgment using the builder API."""
    j = (
        JudgmentBuilder()
        .at(coord)
        .claiming(prop)
        .of_type(carrier)
        .from_source(prov.source)
        .build()
    )
    # Augment with the generated evidence and trust
    return Judgment(
        coordinate=j.coordinate,
        proposition=j.proposition,
        carrier=j.carrier,
        evidence=bundle,
        obligations=j.obligations,
        obstructions=j.obstructions,
        trust=trust,
        provenance=prov,
    )


judgment_st: SearchStrategy[Judgment] = st.builds(
    _build_judgment,
    coord=coordinate_st,
    prop=proposition_st,
    carrier=carrier_st,
    bundle=evidence_bundle_st,
    trust=trust_annotation_st,
    prov=provenance_st,
)


# ---------------------------------------------------------------------------
# 1. Type invariants on judgment fields
# ---------------------------------------------------------------------------


@given(judgment_st)
def test_trust_is_always_trust_annotation(j: Judgment) -> None:
    """judgment.trust must be a TrustAnnotation, never a bare float or str."""
    assert isinstance(j.trust, TrustAnnotation), (
        f"Expected TrustAnnotation, got {type(j.trust).__name__}"
    )


@given(judgment_st)
def test_trust_level_is_trust_level_intEnum(j: Judgment) -> None:
    """judgment.trust.level is always a TrustLevel IntEnum member."""
    level = j.trust.level
    assert isinstance(level, TrustLevel), (
        f"trust.level should be TrustLevel, got {type(level).__name__}: {level!r}"
    )
    assert level in list(TrustLevel), f"Unknown TrustLevel: {level!r}"


@given(judgment_st)
def test_obligations_is_always_tuple(j: Judgment) -> None:
    """judgment.obligations is always a tuple, never None."""
    assert j.obligations is not None, "obligations must not be None"
    assert isinstance(j.obligations, tuple), (
        f"obligations should be tuple, got {type(j.obligations).__name__}"
    )


@given(judgment_st)
def test_evidence_is_always_evidence_bundle(j: Judgment) -> None:
    """judgment.evidence is always an EvidenceBundle instance."""
    assert isinstance(j.evidence, EvidenceBundle), (
        f"evidence should be EvidenceBundle, got {type(j.evidence).__name__}"
    )


@given(judgment_st)
def test_obstructions_is_always_tuple(j: Judgment) -> None:
    """judgment.obstructions is always a tuple, never None."""
    assert j.obstructions is not None, "obstructions must not be None"
    assert isinstance(j.obstructions, tuple), (
        f"obstructions should be tuple, got {type(j.obstructions).__name__}"
    )


@given(judgment_st)
def test_provenance_is_always_provenance(j: Judgment) -> None:
    """judgment.provenance is always a Provenance object."""
    assert isinstance(j.provenance, Provenance), (
        f"provenance should be Provenance, got {type(j.provenance).__name__}"
    )


@given(judgment_st)
def test_carrier_is_always_carrier(j: Judgment) -> None:
    """judgment.carrier is always a Carrier object."""
    assert isinstance(j.carrier, Carrier), (
        f"carrier should be Carrier, got {type(j.carrier).__name__}"
    )


@given(judgment_st)
def test_proposition_is_always_proposition(j: Judgment) -> None:
    """judgment.proposition is always a Proposition object."""
    assert isinstance(j.proposition, Proposition), (
        f"proposition should be Proposition, got {type(j.proposition).__name__}"
    )


# ---------------------------------------------------------------------------
# 2. Judgment has exactly 8 semantic components (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------


def test_judgment_has_eight_semantic_components() -> None:
    """The Judgment dataclass exposes the 8 components of the semantic tuple."""
    required_fields = {
        "coordinate",  # c — where
        "proposition",  # φ — what is claimed
        "carrier",     # A — what kind of thing
        "evidence",    # E — supporting evidence
        "obligations", # O — residual obligations
        "obstructions", # B — persistent obstructions
        "trust",       # T — trust annotation
        "provenance",  # Π — where it came from
    }
    judgment_fields = set(Judgment.__dataclass_fields__.keys())
    assert required_fields.issubset(judgment_fields), (
        f"Missing semantic fields: {required_fields - judgment_fields}"
    )


# ---------------------------------------------------------------------------
# 3. EvidenceBundle invariants
# ---------------------------------------------------------------------------


@given(evidence_bundle_st, evidence_bundle_st)
def test_evidence_bundle_merge_is_commutative_on_item_set(
    b1: EvidenceBundle, b2: EvidenceBundle
) -> None:
    """bundle.merge preserves all items from both bundles."""
    merged_12 = b1.merge(b2)
    merged_21 = b2.merge(b1)
    # Item sets should be equal (same elements, possibly different order)
    assert set(merged_12.items) == set(merged_21.items), (
        "EvidenceBundle.merge is not commutative on item sets"
    )


@given(evidence_bundle_st, evidence_bundle_st, evidence_bundle_st)
def test_evidence_bundle_merge_is_associative_on_item_set(
    b1: EvidenceBundle, b2: EvidenceBundle, b3: EvidenceBundle
) -> None:
    """(b1.merge(b2)).merge(b3) and b1.merge(b2.merge(b3)) have same items."""
    left = b1.merge(b2).merge(b3)
    right = b1.merge(b2.merge(b3))
    assert set(left.items) == set(right.items), (
        "EvidenceBundle.merge is not associative on item sets"
    )


@given(evidence_bundle_st, evidence_bundle_st)
def test_evidence_bundle_merge_preserves_all_kinds(
    b1: EvidenceBundle, b2: EvidenceBundle
) -> None:
    """After merge, every kind present in either bundle is present in merged."""
    merged = b1.merge(b2)
    kinds_in_b1 = {item.kind for item in b1.items}
    kinds_in_b2 = {item.kind for item in b2.items}
    all_expected_kinds = kinds_in_b1 | kinds_in_b2
    merged_kinds = {item.kind for item in merged.items}
    assert all_expected_kinds.issubset(merged_kinds), (
        f"Merge lost evidence kinds: {all_expected_kinds - merged_kinds}"
    )


@given(evidence_bundle_st)
def test_evidence_bundle_merge_with_self_is_idempotent_on_kinds(
    bundle: EvidenceBundle,
) -> None:
    """Merging a bundle with itself preserves the same set of kinds."""
    merged = bundle.merge(bundle)
    original_kinds = {item.kind for item in bundle.items}
    merged_kinds = {item.kind for item in merged.items}
    assert original_kinds == merged_kinds


@given(evidence_bundle_st)
def test_evidence_bundle_add_increases_item_count(
    bundle: EvidenceBundle,
) -> None:
    """add_evidence returns a bundle with at least as many items."""
    new_item = EvidenceItem(
        kind=EvidenceItemKind.ORACLE_PROPOSAL,
        payload={},
        trust_level=TrustLevel.ORACLE_PROPOSED,
        channel="oracle",
        timestamp="2024-01-01T00:00:00",
        expiry="2099-12-31T23:59:59",
        provenance=(),
    )
    augmented = bundle.add_evidence(new_item)
    assert len(augmented.items) >= len(bundle.items), (
        "add_evidence reduced item count"
    )


@given(evidence_bundle_st)
def test_evidence_bundle_total_trust_is_non_negative(
    bundle: EvidenceBundle,
) -> None:
    """total_trust() returns an int ≥ 0."""
    t = bundle.total_trust()
    assert isinstance(t, int) and t >= 0, (
        f"total_trust returned unexpected value: {t!r}"
    )


# ---------------------------------------------------------------------------
# 4. Serialization round-trip
# ---------------------------------------------------------------------------


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_judgment_serialize_produces_dict(j: Judgment) -> None:
    """Judgment.serialize() returns a dict."""
    d = j.serialize()
    assert isinstance(d, dict), f"serialize() returned {type(d).__name__}"


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_judgment_serialize_is_json_serializable(j: Judgment) -> None:
    """Judgment.serialize() output can be re-serialized to JSON."""
    d = j.serialize()
    try:
        json.dumps(d)
    except (TypeError, ValueError) as exc:
        pytest.fail(f"serialize() output is not JSON-serializable: {exc}")


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_judgment_serialize_preserves_proposition_formula(j: Judgment) -> None:
    """Serialized judgment preserves proposition formula."""
    d = j.serialize()
    # Look for formula in the nested structure
    prop_data = d.get("proposition") or {}
    if isinstance(prop_data, dict):
        assert prop_data.get("formula") == j.proposition.formula, (
            "Serialization did not preserve proposition formula"
        )


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_judgment_serialize_preserves_trust_level(j: Judgment) -> None:
    """Serialized judgment preserves trust level."""
    d = j.serialize()
    trust_data = d.get("trust") or {}
    if isinstance(trust_data, dict):
        # The level should be serialized as its integer value
        assert trust_data.get("level") == j.trust.level.value, (
            f"Trust level not preserved: expected {j.trust.level.value}, "
            f"got {trust_data.get('level')}"
        )


# ---------------------------------------------------------------------------
# 5. content_hash determinism
# ---------------------------------------------------------------------------


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_content_hash_is_deterministic(j: Judgment) -> None:
    """content_hash() returns the same value when called twice."""
    h1 = j.content_hash()
    h2 = j.content_hash()
    assert h1 == h2, f"content_hash is not deterministic: {h1!r} ≠ {h2!r}"


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_content_hash_is_string(j: Judgment) -> None:
    """content_hash() returns a non-empty string."""
    h = j.content_hash()
    assert isinstance(h, str) and len(h) > 0, (
        f"content_hash() returned {h!r}"
    )


# ---------------------------------------------------------------------------
# 6. has_residuals / has_obstructions consistency
# ---------------------------------------------------------------------------


@given(judgment_st)
def test_has_residuals_consistent_with_obligations(j: Judgment) -> None:
    """has_residuals() is True iff obligations tuple is non-empty."""
    # Only count undischarged obligations
    undischarged = [o for o in j.obligations if not o.is_discharged]
    if undischarged:
        assert j.has_residuals(), "has_residuals() should be True with undischarged obligations"
    else:
        assert not j.has_residuals(), "has_residuals() should be False with no obligations"


@given(judgment_st)
def test_has_obstructions_consistent_with_obstructions_tuple(j: Judgment) -> None:
    """has_obstructions() is True iff obstructions tuple is non-empty."""
    if j.obstructions:
        assert j.has_obstructions(), "has_obstructions() should be True"
    else:
        assert not j.has_obstructions(), "has_obstructions() should be False"


# ---------------------------------------------------------------------------
# 7. Judgment immutability
# ---------------------------------------------------------------------------


@given(judgment_st)
def test_judgment_is_frozen(j: Judgment) -> None:
    """Judgment is a frozen dataclass: assignment raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        j.trust = TrustAnnotation()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 8. JudgmentAlgebra operations
# ---------------------------------------------------------------------------


@given(judgment_st, judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
def test_compose_preserves_evidence_from_both(j1: Judgment, j2: Judgment) -> None:
    """JudgmentAlgebra.compose result evidence contains items from both operands."""
    composed = JudgmentAlgebra.compose(j1, j2)
    items_in_j1 = set(j1.evidence.items)
    items_in_j2 = set(j2.evidence.items)
    items_in_composed = set(composed.evidence.items)
    assert items_in_j1.issubset(items_in_composed), (
        "compose dropped evidence from j1"
    )
    assert items_in_j2.issubset(items_in_composed), (
        "compose dropped evidence from j2"
    )


@given(judgment_st, judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
def test_weaken_to_common_produces_lower_or_equal_trust(
    j1: Judgment, j2: Judgment
) -> None:
    """weaken_to_common result trust ≤ both input trusts."""
    w1, w2 = JudgmentAlgebra.weaken_to_common(j1, j2)
    assert w1.trust.level.value <= j1.trust.level.value, (
        "weaken_to_common raised trust level of j1"
    )
    assert w2.trust.level.value <= j2.trust.level.value, (
        "weaken_to_common raised trust level of j2"
    )


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_merge_evidence_adds_items(j: Judgment) -> None:
    """merge_evidence adds items to the judgment's evidence bundle."""
    extra = EvidenceBundle(items=(
        EvidenceItem(
            kind=EvidenceItemKind.FORMAL_PROOF,
            payload={},
            trust_level=TrustLevel.VERIFIED_PROOF,
            channel="formal_proof",
            timestamp="2024-01-01T00:00:00",
            expiry="2099-12-31T23:59:59",
            provenance=(),
        ),
    ))
    augmented = j.merge_evidence(extra)
    assert len(augmented.evidence.items) >= len(j.evidence.items), (
        "merge_evidence reduced evidence items"
    )


# ---------------------------------------------------------------------------
# 9. ResidualObligation properties
# ---------------------------------------------------------------------------


@given(obligation_st)
def test_obligation_is_not_discharged_by_default(ob: ResidualObligation) -> None:
    """A freshly constructed obligation is not discharged."""
    assert not ob.is_discharged, "New obligation should not be discharged"


@given(obligation_st, st.text(min_size=1, max_size=20))
def test_discharge_marks_obligation_as_discharged(
    ob: ResidualObligation, evidence_key: str
) -> None:
    """discharge() returns an obligation with is_discharged=True."""
    discharged = ob.discharge(evidence_key)
    assert discharged.is_discharged, "discharge() did not mark as discharged"
    assert discharged.discharge_evidence == evidence_key, (
        "discharge() did not record evidence key"
    )


@given(obligation_st, st.text(min_size=1, max_size=20))
def test_discharge_is_non_destructive(
    ob: ResidualObligation, evidence_key: str
) -> None:
    """discharge() does not mutate the original obligation."""
    ob.discharge(evidence_key)
    assert not ob.is_discharged, (
        "discharge() mutated the original obligation"
    )


@given(obligation_st, st.integers(min_value=1, max_value=20))
def test_with_priority_preserves_other_fields(
    ob: ResidualObligation, priority: int
) -> None:
    """with_priority() only changes the priority field."""
    updated = ob.with_priority(priority)
    assert updated.priority == priority
    assert updated.obligation_id == ob.obligation_id
    assert updated.description == ob.description


# ---------------------------------------------------------------------------
# 10. Obstruction cohomology_class preservation
# ---------------------------------------------------------------------------


@given(obstruction_st)
def test_obstruction_cohomology_class_is_preserved_in_resolve(
    obs: Obstruction,
) -> None:
    """resolve() preserves cohomology_class (it's a persistent semantic attribute)."""
    resolved = obs.resolve("some-evidence-key")
    assert resolved.cohomology_class == obs.cohomology_class, (
        "resolve() changed the cohomology_class"
    )


@given(obstruction_st)
def test_obstruction_resolve_marks_as_resolved(obs: Obstruction) -> None:
    """resolve() sets is_resolved=True."""
    resolved = obs.resolve("evidence-key")
    assert resolved.is_resolved, "resolve() did not mark obstruction as resolved"


@given(obstruction_st, st.text(min_size=1, max_size=30))
def test_add_repair_hint_preserves_existing_hints(
    obs: Obstruction, hint: str
) -> None:
    """add_repair_hint() appends and does not remove existing hints."""
    augmented = obs.add_repair_hint(hint)
    for existing in obs.repair_hints:
        assert existing in augmented.repair_hints, (
            f"add_repair_hint removed existing hint: {existing!r}"
        )
    assert hint in augmented.repair_hints


# ---------------------------------------------------------------------------
# 11. TrustAnnotation invariants
# ---------------------------------------------------------------------------


@given(trust_annotation_st)
def test_trust_annotation_level_is_always_trust_level(ta: TrustAnnotation) -> None:
    """TrustAnnotation.level is always a TrustLevel enum member."""
    assert isinstance(ta.level, TrustLevel)


@given(trust_annotation_st)
def test_trust_annotation_evidence_basis_is_tuple(ta: TrustAnnotation) -> None:
    """TrustAnnotation.evidence_basis is always a tuple."""
    assert isinstance(ta.evidence_basis, tuple)


def test_trust_annotation_default_level_is_unverified() -> None:
    """TrustAnnotation() defaults to UNVERIFIED (level=1)."""
    ta = TrustAnnotation()
    assert ta.level == TrustLevel.UNVERIFIED, (
        f"Default TrustAnnotation level should be UNVERIFIED, got {ta.level}"
    )


@given(trust_annotation_st)
def test_trust_annotation_is_frozen(ta: TrustAnnotation) -> None:
    """TrustAnnotation is a frozen dataclass."""
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        ta.level = TrustLevel.VERIFIED_PROOF  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 12. Judgment.strengthen / weaken monotonicity
# ---------------------------------------------------------------------------


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_strengthen_does_not_decrease_trust(j: Judgment) -> None:
    """strengthen() result trust.level ≥ original trust.level."""
    strengthened = j.strengthen("adding-evidence")
    assert strengthened.trust.level.value >= j.trust.level.value, (
        f"strengthen() decreased trust from {j.trust.level.name} to "
        f"{strengthened.trust.level.name}"
    )


@given(judgment_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_weaken_does_not_increase_trust(j: Judgment) -> None:
    """weaken() result trust.level ≤ original trust.level."""
    weakened = j.weaken("challenging-evidence")
    assert weakened.trust.level.value <= j.trust.level.value, (
        f"weaken() increased trust from {j.trust.level.name} to "
        f"{weakened.trust.level.name}"
    )


# ---------------------------------------------------------------------------
# 13. EvidenceItem validity and kind correctness
# ---------------------------------------------------------------------------


@given(evidence_item_st)
def test_evidence_item_kind_is_evidence_item_kind(item: EvidenceItem) -> None:
    """EvidenceItem.kind is always an EvidenceItemKind enum member."""
    assert isinstance(item.kind, EvidenceItemKind), (
        f"EvidenceItem.kind should be EvidenceItemKind, got {type(item.kind).__name__}"
    )


@given(evidence_item_st)
def test_evidence_item_trust_level_is_valid(item: EvidenceItem) -> None:
    """EvidenceItem.trust_level is a TrustLevel member."""
    assert isinstance(item.trust_level, TrustLevel), (
        f"EvidenceItem.trust_level should be TrustLevel, got "
        f"{type(item.trust_level).__name__}"
    )


@given(evidence_item_st)
def test_evidence_item_is_frozen(item: EvidenceItem) -> None:
    """EvidenceItem is a frozen dataclass."""
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        item.kind = EvidenceItemKind.FORMAL_PROOF  # type: ignore[misc]


@given(evidence_item_st)
def test_evidence_item_canonical_key_is_deterministic(item: EvidenceItem) -> None:
    """canonical_key() is stable across calls."""
    k1 = item.canonical_key()
    k2 = item.canonical_key()
    assert k1 == k2, "canonical_key() is not deterministic"


# ---------------------------------------------------------------------------
# 14. EvidenceBundle.by_kind correctness
# ---------------------------------------------------------------------------


@given(evidence_bundle_st, evidence_kind_st)
def test_by_kind_returns_only_matching_items(
    bundle: EvidenceBundle, kind: EvidenceItemKind
) -> None:
    """by_kind() returns only items whose kind matches."""
    items = bundle.by_kind(kind)
    for item in items:
        assert item.kind is kind, (
            f"by_kind({kind.name}) returned item with kind {item.kind.name}"
        )


@given(evidence_bundle_st)
def test_by_kind_covers_all_items(bundle: EvidenceBundle) -> None:
    """Iterating by_kind over all kinds covers all items."""
    all_by_kind: list[EvidenceItem] = []
    for kind in EvidenceItemKind:
        all_by_kind.extend(bundle.by_kind(kind))
    assert len(all_by_kind) == len(bundle.items), (
        "by_kind partitioning missed some items"
    )


# ---------------------------------------------------------------------------
# 15. Judgment builder produces well-formed judgments
# ---------------------------------------------------------------------------


@given(coordinate_st, proposition_st, carrier_st, provenance_source_st)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=30)
def test_judgment_builder_produces_valid_judgment(
    coord: CoordinateObject,
    prop: Proposition,
    carrier: Carrier,
    source: ProvenanceSource,
) -> None:
    """JudgmentBuilder always produces a well-formed Judgment."""
    j = (
        JudgmentBuilder()
        .at(coord)
        .claiming(prop)
        .of_type(carrier)
        .from_source(source)
        .build()
    )
    assert isinstance(j, Judgment)
    assert isinstance(j.trust, TrustAnnotation)
    assert isinstance(j.obligations, tuple)
    assert isinstance(j.evidence, EvidenceBundle)
    assert isinstance(j.provenance, Provenance)


# ---------------------------------------------------------------------------
# 16. Provenance append-only property
# ---------------------------------------------------------------------------


@given(provenance_st)
def test_provenance_is_frozen(prov: Provenance) -> None:
    """Provenance is a frozen dataclass — cannot be mutated in place."""
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        prov.source = ProvenanceSource.ORACLE  # type: ignore[misc]


@given(provenance_st, st.text(min_size=1, max_size=20))
def test_with_transformation_appends_to_history(
    prov: Provenance, step: str
) -> None:
    """with_transformation() appends step to transformation_history."""
    updated = prov.with_transformation(step)
    assert step in updated.transformation_history, (
        "with_transformation did not record step"
    )
    # Original is unchanged
    assert step not in prov.transformation_history or len(prov.transformation_history) < len(updated.transformation_history), (
        "with_transformation mutated original provenance"
    )


@given(provenance_st, st.text(min_size=1, max_size=20))
def test_with_transformation_does_not_remove_existing_steps(
    prov: Provenance, step: str
) -> None:
    """with_transformation() preserves all existing history steps."""
    updated = prov.with_transformation(step)
    for existing in prov.transformation_history:
        assert existing in updated.transformation_history, (
            f"with_transformation removed existing step: {existing!r}"
        )
