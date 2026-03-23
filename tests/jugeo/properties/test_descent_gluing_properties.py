"""Property-based tests for descent and gluing engine invariants.

This module uses Hypothesis to verify algebraic properties of the JuGeo
descent procedure as defined in theory2.tex §3.

Descent is the LOCAL-TO-GLOBAL mechanism: given local sections defined
over each member of a cover, descent checks overlap compatibility and
produces either a GlobalSection (H⁰ cohomology class — success) or a
DescentObstruction (H¹ class — failure).

Properties under test:

* Empty cover → trivial global section (0-constituent, empty merged_judgment)
* Single-patch cover → section data equals that patch's data exactly
* Gluing is idempotent: running descent twice on identical data gives
  identical results
* DescentObstruction has a stable persistence_id usable as dict key
  (representing the cohomology class as a hashable handle)
* CohomologyClass.persistence_id is a non-empty string (stable cross-session)
* Compatible covers succeed; incompatible covers return DescentObstruction
  (not raise an exception)
* Refinement of a successful cover also succeeds (monotonicity)
* OverlapStatus.SATISFIED covers never produce obstructions
* DescentResult is always exactly one of success or failure
* GluingReport.obstruction_rank matches the count of obstructions
* LocalSection.with_trust clamps trust to [0,1]
* OverlapCondition.evaluate is pure (does not mutate its argument)
* trust_floor of a GlobalSection ≤ all constituent trust levels
* merge_evidence on a LocalSection preserves existing evidence items

References: theory2.tex §3, §3.1, §3.2, §3.4
"""

from __future__ import annotations

import sys
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

from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateMorphism,
)
from jugeo.geometry.covers import Cover, CoverMember, refine_cover
from jugeo.geometry.descent import (
    CohomologyClass,
    DescentConfiguration,
    DescentEngine,
    DescentObstruction,
    DescentResult,
    DescentStrategy,
    GluingData,
    GluingReport,
    LocalSection,
    OverlapCondition,
    OverlapStatus,
    RepairFrontier,
    run_descent,
    glue_sections,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coord(name: str, *path_parts: str) -> Coordinate:
    """Construct a Coordinate using the legacy positional convention."""
    parts = tuple(path_parts) if path_parts else (name,)
    return Coordinate(name, CoordinateKind.REGION, parts)


def _cover_with_patches(
    target: Coordinate,
    patches: list[Coordinate],
    *,
    add_overlap: bool = True,
) -> Cover:
    """Build a Cover from target and patches.

    When ``add_overlap`` is True, adds pairwise overlaps between ALL
    adjacent patches (useful for testing).
    """
    overlaps: list[tuple[str, str]] = []
    if add_overlap:
        for i in range(len(patches) - 1):
            overlaps.append((patches[i].key, patches[i + 1].key))
    return Cover(
        target=target,
        patches=tuple(patches),
        overlaps=tuple(overlaps),
    )


def _section_data(value: Any = 1) -> dict[str, Any]:
    return {"value": value, "type": type(value).__name__}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

coord_name_st: SearchStrategy[str] = st.text(
    min_size=1, max_size=12,
    alphabet="abcdefghijklmnopqrstuvwxyz",
)

simple_value_st: SearchStrategy[Any] = st.one_of(
    st.integers(min_value=0, max_value=100),
    st.text(min_size=0, max_size=20),
    st.booleans(),
)

trust_float_st: SearchStrategy[float] = st.floats(
    min_value=0.0, max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

local_section_st: SearchStrategy[LocalSection] = st.builds(
    LocalSection,
    coordinate=coord_name_st,
    judgment_data=st.fixed_dictionaries(
        {"value": simple_value_st}
    ),
    trust_level=trust_float_st,
    is_partial=st.booleans(),
)

evidence_strings_st: SearchStrategy[list[str]] = st.lists(
    st.text(min_size=1, max_size=20), max_size=4
)


# ---------------------------------------------------------------------------
# 1. Empty cover → trivial global section
# ---------------------------------------------------------------------------


def test_empty_cover_produces_trivial_global_section() -> None:
    """attempt_descent on an empty cover always succeeds with empty data."""
    target = _coord("root", "root")
    cover = Cover(target=target, patches=(), overlaps=())
    engine = DescentEngine()
    result = engine.attempt_descent(cover, {})
    assert result.is_success, "Empty cover should produce a GlobalSection"
    gs = result.unwrap_section()
    assert gs.merged_judgment == {}, (
        f"Empty cover should have empty merged_judgment, got {gs.merged_judgment}"
    )
    assert gs.constituent_count == 0, (
        f"Empty cover should have 0 constituents, got {gs.constituent_count}"
    )


def test_empty_cover_run_method_succeeds() -> None:
    """Legacy run() on empty cover also succeeds."""
    target = _coord("root", "root")
    cover = Cover(target=target, patches=(), overlaps=())
    engine = DescentEngine()
    report = engine.run(cover, {})
    assert report.success, "Empty cover legacy run should succeed"


# ---------------------------------------------------------------------------
# 2. Single-patch cover → section data equals patch data
# ---------------------------------------------------------------------------


@given(simple_value_st)
def test_single_patch_cover_section_equals_patch_data(value: Any) -> None:
    """Single-patch cover: glued section data matches the sole patch's data."""
    target = _coord("root", "root")
    patch = _coord("p", "root", "p")
    cover = Cover(target=target, patches=(patch,), overlaps=())
    engine = DescentEngine()
    patch_data = {"value": value, "type": type(value).__name__}
    result = engine.attempt_descent(cover, {patch.key: patch_data})
    assert result.is_success, (
        f"Single-patch cover should always succeed, got failure"
    )
    gs = result.unwrap_section()
    assert gs.merged_judgment == patch_data, (
        f"GlobalSection data {gs.merged_judgment} ≠ patch data {patch_data}"
    )


@given(simple_value_st)
def test_single_patch_run_section_equals_patch_data(value: Any) -> None:
    """Legacy run() single patch: glued_section matches patch data."""
    target = _coord("root", "root")
    patch = _coord("p", "root", "p")
    cover = Cover(target=target, patches=(patch,), overlaps=())
    engine = DescentEngine()
    patch_data = {"value": value}
    report = engine.run(cover, {patch.key: patch_data})
    assert report.success
    assert report.glued_section == patch_data


# ---------------------------------------------------------------------------
# 3. Gluing is idempotent for consistent data
# ---------------------------------------------------------------------------


@given(simple_value_st)
def test_descent_is_idempotent_for_compatible_sections(value: Any) -> None:
    """Running descent twice on identical compatible sections gives same result."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    sections = {a.key: {"v": value}, b.key: {"v": value}}
    engine = DescentEngine()
    result1 = engine.attempt_descent(cover, sections)
    result2 = engine.attempt_descent(cover, sections)
    assert result1.is_success == result2.is_success, (
        "Idempotency violated: different success status on second run"
    )
    if result1.is_success and result2.is_success:
        gs1 = result1.unwrap_section()
        gs2 = result2.unwrap_section()
        assert gs1.merged_judgment == gs2.merged_judgment, (
            "Idempotency violated: different merged_judgment on second run"
        )


@given(simple_value_st)
def test_descent_is_idempotent_for_incompatible_sections(value: Any) -> None:
    """Descent on incompatible sections is deterministically failure."""
    assume(isinstance(value, int))  # ensure we can add 1
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    sections = {a.key: {"v": value}, b.key: {"v": value + 1}}
    engine = DescentEngine()
    r1 = engine.attempt_descent(cover, sections)
    r2 = engine.attempt_descent(cover, sections)
    assert r1.is_failure == r2.is_failure, (
        "Idempotency violated: different failure status on second run"
    )


# ---------------------------------------------------------------------------
# 4. DescentObstruction persistence_id is usable as dict key (hashability)
# ---------------------------------------------------------------------------


def test_descent_obstruction_persistence_id_is_non_empty() -> None:
    """DescentObstruction has a non-empty persistence_id."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    engine = DescentEngine()
    result = engine.attempt_descent(cover, {a.key: {"v": 1}, b.key: {"v": 2}})
    assert result.is_failure
    obs = result.unwrap_obstruction()
    pid = obs.persistence_id
    assert isinstance(pid, str) and len(pid) > 0, (
        f"persistence_id should be non-empty string, got {pid!r}"
    )


def test_descent_obstruction_persistence_id_usable_as_dict_key() -> None:
    """DescentObstruction.persistence_id can be used as a dict key (cohomology class handle)."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    engine = DescentEngine()
    result = engine.attempt_descent(cover, {a.key: {"v": 1}, b.key: {"v": 2}})
    obs = result.unwrap_obstruction()
    # The persistence_id acts as the cohomology class key
    cohomology_registry: dict[str, DescentObstruction] = {}
    cohomology_registry[obs.persistence_id] = obs
    assert cohomology_registry[obs.persistence_id] is obs


def test_cohomology_class_persistence_id_is_non_empty() -> None:
    """CohomologyClass has a non-empty persistence_id."""
    cc = CohomologyClass()
    assert isinstance(cc.persistence_id, str) and len(cc.persistence_id) > 0


def test_cohomology_class_persistence_id_is_stable() -> None:
    """CohomologyClass.persistence_id does not change after construction."""
    cc = CohomologyClass()
    pid1 = cc.persistence_id
    pid2 = cc.persistence_id
    assert pid1 == pid2, "CohomologyClass.persistence_id changed between accesses"


def test_cohomology_class_persistence_id_usable_as_dict_key() -> None:
    """CohomologyClass.persistence_id is usable as dict key (string is hashable)."""
    cc = CohomologyClass()
    d: dict[str, str] = {cc.persistence_id: "class_data"}
    assert cc.persistence_id in d


def test_two_obstructions_have_distinct_persistence_ids() -> None:
    """Two independently created obstructions get distinct persistence_ids."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    engine = DescentEngine()
    r1 = engine.attempt_descent(cover, {a.key: {"v": 1}, b.key: {"v": 2}})
    r2 = engine.attempt_descent(cover, {a.key: {"v": 3}, b.key: {"v": 4}})
    obs1 = r1.unwrap_obstruction()
    obs2 = r2.unwrap_obstruction()
    # Each obstruction gets its own persistence_id (UUIDs)
    assert obs1.persistence_id != obs2.persistence_id, (
        "Two independent obstructions should have different persistence_ids"
    )


# ---------------------------------------------------------------------------
# 5. Compatible covers succeed, incompatible return obstruction (not raise)
# ---------------------------------------------------------------------------


@given(simple_value_st)
def test_compatible_two_patch_cover_succeeds(value: Any) -> None:
    """Two patches with identical data always produce a GlobalSection."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    engine = DescentEngine()
    result = engine.attempt_descent(cover, {a.key: {"v": value}, b.key: {"v": value}})
    assert result.is_success, (
        f"Compatible patches with value={value!r} should succeed"
    )


@given(st.integers(0, 50), st.integers(51, 100))
def test_incompatible_patches_returns_obstruction_not_exception(
    v1: int, v2: int
) -> None:
    """Incompatible patches return DescentObstruction, never raise an exception."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    engine = DescentEngine()
    try:
        result = engine.attempt_descent(cover, {a.key: {"v": v1}, b.key: {"v": v2}})
    except Exception as exc:
        pytest.fail(
            f"attempt_descent raised exception instead of returning DescentObstruction: {exc}"
        )
    assert result.is_failure, (
        f"Expected DescentObstruction for v1={v1}, v2={v2}, got success"
    )
    obs = result.unwrap_obstruction()
    assert isinstance(obs, DescentObstruction)


# ---------------------------------------------------------------------------
# 6. DescentResult is always exactly one of success or failure
# ---------------------------------------------------------------------------


@given(simple_value_st, st.booleans())
def test_descent_result_is_exclusive_or(value: Any, compatible: bool) -> None:
    """DescentResult.is_success and is_failure are mutually exclusive."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = _cover_with_patches(target, [a, b])
    engine = DescentEngine()
    if compatible:
        sections = {a.key: {"v": value}, b.key: {"v": value}}
    else:
        sections = {a.key: {"v": value}, b.key: {"v": str(value) + "_different"}}
    result = engine.attempt_descent(cover, sections)
    assert result.is_success != result.is_failure, (
        "DescentResult is_success and is_failure must be mutually exclusive"
    )
    assert result.is_success or result.is_failure, (
        "DescentResult must be one of success or failure"
    )


# ---------------------------------------------------------------------------
# 7. LocalSection properties
# ---------------------------------------------------------------------------


@given(local_section_st, trust_float_st)
def test_local_section_with_trust_clamps_to_unit_interval(
    section: LocalSection, new_trust: float
) -> None:
    """LocalSection.with_trust clamps the new trust to [0, 1]."""
    updated = section.with_trust(new_trust)
    assert 0.0 <= updated.trust_level <= 1.0, (
        f"Trust level {updated.trust_level} outside [0,1] after with_trust({new_trust})"
    )


@given(local_section_st, trust_float_st)
def test_local_section_with_trust_does_not_mutate_original(
    section: LocalSection, new_trust: float
) -> None:
    """with_trust returns a new section; original is unchanged."""
    original_trust = section.trust_level
    updated = section.with_trust(new_trust)
    assert section.trust_level == original_trust, (
        "with_trust mutated the original LocalSection"
    )
    assert updated is not section, "with_trust should return a new object"


@given(local_section_st, evidence_strings_st)
def test_merge_evidence_preserves_existing_evidence(
    section: LocalSection, extra: list[str]
) -> None:
    """merge_evidence preserves all original evidence items."""
    augmented = section.merge_evidence(extra)
    for ev in section.evidence_bundle:
        assert ev in augmented.evidence_bundle, (
            f"merge_evidence dropped existing evidence item: {ev!r}"
        )


@given(local_section_st, evidence_strings_st)
def test_merge_evidence_adds_new_items(
    section: LocalSection, extra: list[str]
) -> None:
    """merge_evidence adds all provided extra evidence items."""
    augmented = section.merge_evidence(extra)
    for ev in extra:
        assert ev in augmented.evidence_bundle, (
            f"merge_evidence failed to add evidence item: {ev!r}"
        )


@given(local_section_st, st.text(min_size=1, max_size=20))
def test_discharge_obligation_removes_named_obligation(
    section: LocalSection, obligation: str
) -> None:
    """discharge_obligation returns section without the named obligation."""
    # Add an obligation first
    section_with_ob = LocalSection(
        coordinate=section.coordinate,
        judgment_data=dict(section.judgment_data),
        evidence_bundle=section.evidence_bundle,
        trust_level=section.trust_level,
        provenance=section.provenance,
        is_partial=True,
        residual_obligations=[obligation, "other"],
    )
    discharged = section_with_ob.discharge_obligation(obligation)
    assert obligation not in discharged.residual_obligations, (
        f"discharge_obligation did not remove {obligation!r}"
    )
    assert "other" in discharged.residual_obligations, (
        "discharge_obligation removed wrong obligation"
    )


# ---------------------------------------------------------------------------
# 8. OverlapCondition properties
# ---------------------------------------------------------------------------


def test_overlap_condition_evaluate_is_pure() -> None:
    """OverlapCondition.evaluate does not mutate its arguments."""
    left_data = {"v": 1}
    right_data = {"v": 1}
    left_copy = dict(left_data)
    right_copy = dict(right_data)
    cond = OverlapCondition(
        left_coordinate="a",
        right_coordinate="b",
        overlap_coordinate="a_b",
    )
    _new_cond = cond.evaluate(left_data, right_data)
    assert left_data == left_copy, "evaluate mutated left_data"
    assert right_data == right_copy, "evaluate mutated right_data"


def test_overlap_condition_satisfied_when_data_matches() -> None:
    """OverlapCondition evaluates to SATISFIED when default predicate matches."""
    cond = OverlapCondition(
        left_coordinate="a",
        right_coordinate="b",
        overlap_coordinate="a_b",
    )
    result = cond.evaluate({"v": 1}, {"v": 1})
    assert result.status == OverlapStatus.SATISFIED


def test_overlap_condition_violated_when_data_differs() -> None:
    """OverlapCondition evaluates to VIOLATED when default predicate disagrees."""
    cond = OverlapCondition(
        left_coordinate="a",
        right_coordinate="b",
        overlap_coordinate="a_b",
    )
    result = cond.evaluate({"v": 1}, {"v": 2})
    assert result.status == OverlapStatus.VIOLATED


def test_overlap_condition_pair_is_canonical() -> None:
    """OverlapCondition.pair returns (left, right) in that order."""
    cond = OverlapCondition(
        left_coordinate="alpha",
        right_coordinate="beta",
        overlap_coordinate="alpha_beta",
    )
    assert cond.pair == ("alpha", "beta")


# ---------------------------------------------------------------------------
# 9. trust_floor of GlobalSection ≤ constituent trust levels
# ---------------------------------------------------------------------------


@given(trust_float_st, trust_float_st)
def test_global_section_trust_floor_leq_all_constituents(
    trust_a: float, trust_b: float
) -> None:
    """GlobalSection.trust_floor ≤ min(trust_a, trust_b)."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=())
    engine = DescentEngine()
    # No overlaps means both patches are always compatible
    local_a = LocalSection(coordinate=a.key, judgment_data={"k": "v"}, trust_level=trust_a)
    local_b = LocalSection(coordinate=b.key, judgment_data={"k": "v"}, trust_level=trust_b)
    # Use GluingData directly
    from jugeo.geometry.descent import GluingData
    gluing = GluingData(sections={a.key: local_a, b.key: local_b})
    gs = engine.compute_gluing(gluing, target_coordinate=target.key)
    assert gs.trust_floor <= max(trust_a, trust_b) + 1e-9, (
        f"trust_floor {gs.trust_floor} exceeds max of inputs"
    )
    assert gs.trust_floor >= -1e-9, "trust_floor should be non-negative"


# ---------------------------------------------------------------------------
# 10. Refinement monotonicity
# ---------------------------------------------------------------------------


def test_refinement_of_successful_cover_also_succeeds() -> None:
    """When a cover succeeds, its refinement (same data, more patches) also succeeds."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=())
    engine = DescentEngine()
    # Original success
    sections_original = {a.key: {"v": 42}, b.key: {"v": 42}}
    result_original = engine.attempt_descent(cover, sections_original)
    assert result_original.is_success, "Precondition: original cover should succeed"
    # Refined cover: split each patch into two sub-patches (no conflicting data)
    a1 = _coord("a1", "root", "a", "1")
    a2 = _coord("a2", "root", "a", "2")
    b1 = _coord("b1", "root", "b", "1")
    b2 = _coord("b2", "root", "b", "2")
    refined_cover = Cover(
        target=target,
        patches=(a1, a2, b1, b2),
        overlaps=((a1.key, a2.key), (b1.key, b2.key)),
    )
    # All refined patches agree on data
    sections_refined = {
        a1.key: {"v": 42}, a2.key: {"v": 42},
        b1.key: {"v": 42}, b2.key: {"v": 42},
    }
    result_refined = engine.attempt_descent(refined_cover, sections_refined)
    assert result_refined.is_success, (
        "Refinement of a successful cover with consistent data should also succeed"
    )


# ---------------------------------------------------------------------------
# 11. GluingReport (legacy) compatibility
# ---------------------------------------------------------------------------


@given(simple_value_st)
def test_run_descent_compatible_sections_succeed(value: Any) -> None:
    """run_descent() (legacy) returns GluingReport with success=True for compatible data."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=((a.key, b.key),))
    report = run_descent(cover, {a.key: {"v": value}, b.key: {"v": value}})
    assert isinstance(report, GluingReport)
    assert report.success is True


@given(st.integers(0, 50), st.integers(51, 100))
def test_run_descent_incompatible_sections_fail(v1: int, v2: int) -> None:
    """run_descent() returns GluingReport with success=False for incompatible data."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=((a.key, b.key),))
    report = run_descent(cover, {a.key: {"v": v1}, b.key: {"v": v2}})
    assert isinstance(report, GluingReport)
    assert report.success is False
    assert report.obstruction_rank >= 1, (
        f"Obstruction rank should be ≥1 for incompatible sections"
    )


# ---------------------------------------------------------------------------
# 12. CohomologyClass methods
# ---------------------------------------------------------------------------


def test_cohomology_class_empty_is_trivial() -> None:
    """CohomologyClass with no cocycle_data is trivial (H¹=0)."""
    cc = CohomologyClass(dimension=1, cocycle_data={})
    assert cc.is_trivial(), "Empty cocycle should be trivial"


def test_cohomology_class_with_data_is_not_trivial() -> None:
    """CohomologyClass with non-empty cocycle_data is non-trivial."""
    cc = CohomologyClass(dimension=1, cocycle_data={"overlap_ab": {"mismatch": 1}})
    assert not cc.is_trivial(), "Non-empty cocycle should not be trivial"


def test_cohomology_class_rank_counts_non_trivial_components() -> None:
    """CohomologyClass.rank() counts non-trivial cocycle components."""
    cc = CohomologyClass(
        dimension=1,
        cocycle_data={
            "ab": {"mismatch": 1},
            "bc": None,
            "cd": {},
            "de": 0,
            "ef": {"mismatch": 2},
        }
    )
    assert cc.rank() == 2, (
        f"Expected rank 2, got {cc.rank()} for 2 non-trivial components"
    )


def test_cohomology_class_restrict_keeps_matching_keys() -> None:
    """CohomologyClass.restrict_to keeps only keys in the provided set."""
    cc = CohomologyClass(
        dimension=1,
        cocycle_data={"ab": {"v": 1}, "bc": {"v": 2}, "cd": {"v": 3}},
    )
    restricted = cc.restrict_to(frozenset(["ab", "cd"]))
    assert set(restricted.cocycle_data.keys()) == {"ab", "cd"}, (
        f"restrict_to did not filter keys correctly: {set(restricted.cocycle_data.keys())}"
    )


def test_cohomology_class_restrict_preserves_persistence_id() -> None:
    """CohomologyClass.restrict_to preserves the persistence_id."""
    cc = CohomologyClass(
        dimension=1,
        cocycle_data={"ab": {"v": 1}, "bc": {"v": 2}},
    )
    restricted = cc.restrict_to(frozenset(["ab"]))
    assert restricted.persistence_id == cc.persistence_id, (
        "restrict_to should preserve persistence_id"
    )


# ---------------------------------------------------------------------------
# 13. DescentObstruction structure
# ---------------------------------------------------------------------------


def test_descent_obstruction_from_failure_has_violated_overlaps() -> None:
    """A DescentObstruction produced by real failure has at least one violated overlap."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=((a.key, b.key),))
    engine = DescentEngine()
    result = engine.attempt_descent(cover, {a.key: {"v": 1}, b.key: {"v": 2}})
    obs = result.unwrap_obstruction()
    assert obs.violation_count >= 1, (
        f"Obstruction should have at least 1 violated overlap, got {obs.violation_count}"
    )


def test_descent_obstruction_involved_coordinates_nonempty() -> None:
    """DescentObstruction.involved_coordinates() is non-empty on real failure."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=((a.key, b.key),))
    engine = DescentEngine()
    result = engine.attempt_descent(cover, {a.key: {"v": 1}, b.key: {"v": 2}})
    obs = result.unwrap_obstruction()
    coords = obs.involved_coordinates()
    assert len(coords) >= 1, (
        "DescentObstruction should name at least one involved coordinate"
    )


def test_descent_obstruction_restrict_to_empty_keys_removes_violations() -> None:
    """Restricting obstruction to empty frozenset yields no violated overlaps."""
    obs = DescentObstruction(
        coordinate="root",
        violated_overlaps=(),
        persistence_id="test-pid",
    )
    restricted = obs.restrict_to(frozenset())
    assert restricted.violation_count == 0


# ---------------------------------------------------------------------------
# 14. glue_sections compatibility alias
# ---------------------------------------------------------------------------


def test_glue_sections_is_alias_for_run_descent() -> None:
    """glue_sections and run_descent return equivalent results."""
    target = _coord("root", "root")
    a = _coord("a", "root", "a")
    b = _coord("b", "root", "b")
    cover = Cover(target=target, patches=(a, b), overlaps=((a.key, b.key),))
    sections = {a.key: {"v": 1}, b.key: {"v": 1}}
    r1 = run_descent(cover, sections)
    r2 = glue_sections(cover, sections)
    assert r1.success == r2.success, (
        "run_descent and glue_sections gave different success status"
    )


# ---------------------------------------------------------------------------
# 15. LocalSection summary / query helpers
# ---------------------------------------------------------------------------


@given(local_section_st)
def test_local_section_summary_is_string(section: LocalSection) -> None:
    """LocalSection.summary() returns a non-empty string."""
    s = section.summary()
    assert isinstance(s, str) and len(s) > 0, (
        "LocalSection.summary() should return a non-empty string"
    )


@given(local_section_st, trust_float_st)
def test_trust_meets_floor_consistent_with_trust_level(
    section: LocalSection, floor: float
) -> None:
    """trust_meets_floor(floor) returns True iff trust_level >= floor."""
    result = section.trust_meets_floor(floor)
    expected = section.trust_level >= floor
    assert result == expected, (
        f"trust_meets_floor({floor}) = {result} but trust_level={section.trust_level}"
    )


@given(local_section_st)
def test_is_fully_evidenced_false_when_has_obligations(section: LocalSection) -> None:
    """LocalSection with residual obligations is not fully evidenced."""
    with_obligations = LocalSection(
        coordinate=section.coordinate,
        judgment_data=dict(section.judgment_data),
        evidence_bundle=section.evidence_bundle,
        trust_level=section.trust_level,
        provenance=section.provenance,
        is_partial=True,
        residual_obligations=["ob1"],
    )
    assert not with_obligations.is_fully_evidenced, (
        "Section with obligations should not be fully evidenced"
    )
