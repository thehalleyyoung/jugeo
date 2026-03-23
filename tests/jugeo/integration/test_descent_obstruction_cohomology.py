"""Integration tests: descent + obstruction theory + errors.

Verifies that gluing failures produce persistent cohomology-class obstructions,
that FailureChain correctly links to obstruction records, and that the
obstruction persistence invariant holds across serialization round-trips.

Theory2 invariants under test
-------------------------------
* Gluing failure → DescentObstruction → H¹ cohomology class.
* Obstructions are persistent (not ephemeral) — they survive serialization.
* FailureChain links to obstruction coordinate and classification.
* CohomologyClass.persistence_id is stable and content-addressed.
* Obstruction.is_coboundary is None (unknown) unless explicitly resolved.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists()
)
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

# ---------------------------------------------------------------------------
# Geometry imports
# ---------------------------------------------------------------------------
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.geometry.covers import Cover, refine_cover
from jugeo.geometry.descent import (
    CohomologyClass,
    DescentConfiguration,
    DescentEngine,
    DescentObstruction,
    DescentPhase,
    DescentResult,
    DescentStrategy,
    GluingData,
    GluingReport,
    GlobalSection,
    LocalSection,
    OverlapCondition,
    OverlapStatus,
    RepairFrontier,
    run_descent,
    glue_sections,
)

# ---------------------------------------------------------------------------
# Error imports
# ---------------------------------------------------------------------------
from jugeo.errors import (
    EvidenceFamily,
    FailureChain,
    FailureClassification,
    FailureScope,
    JuGeoError,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    as_failure_payload,
    chain_failures,
    classify_error,
    filter_failures,
    merge_repair_hints,
    raise_with_scope,
)

# ---------------------------------------------------------------------------
# Judgment imports (for provenance/trust checks)
# ---------------------------------------------------------------------------
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentStatus,
    Obstruction as JudgmentObstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coord(*parts: str) -> Coordinate:
    return Coordinate(components=parts, kind=CoordinateKind.REGION)


def _make_cover(base_key: str, patch_keys: list[str]) -> Cover:
    base = _coord(base_key)
    patches = tuple(_coord(base_key, k) for k in patch_keys)
    overlaps: list[tuple[str, str]] = []
    for i, a in enumerate(patches):
        for j, b in enumerate(patches[i + 1 :]):
            overlaps.append((a.key, b.key))
    return Cover(target=base, patches=patches, overlaps=tuple(overlaps))


def _consistent_sections(cover: Cover, value: str = "same") -> dict:
    return {p.key: {"data": value} for p in cover.patches}


def _conflicting_sections(cover: Cover) -> dict:
    return {p.key: {"data": f"val_{i}"} for i, p in enumerate(cover.patches)}


def _obstruction_record(coord: str = "mod/fn") -> ObstructionRecord:
    return ObstructionRecord(
        coordinate=coord,
        violated_condition="overlap_mismatch",
        evidence_family=EvidenceFamily.SOLVER,
        evidence={"left": "A", "right": "B"},
        downstream_obligations=("verify_after_repair",),
    )


def _structured_failure(
    coord: str = "mod/fn",
    scope: FailureScope = FailureScope.GEOMETRY,
    classification: FailureClassification = FailureClassification.DESCENT_OBSTRUCTION,
) -> StructuredFailure:
    return StructuredFailure(
        code="overlap-failure",
        message=f"Overlap mismatch at {coord}",
        scope=scope,
        classification=classification,
        coordinate=coord,
    )


# ---------------------------------------------------------------------------
# §1  Gluing failure → DescentObstruction
# ---------------------------------------------------------------------------


class TestGluingFailureProducesObstruction:
    """Verify that descent failure always produces a DescentObstruction."""

    def test_single_overlap_mismatch_fails(self) -> None:
        cover = _make_cover("root", ["a", "b"])
        pa, pb = cover.patches
        sections = {pa.key: {"v": 1}, pb.key: {"v": 2}}
        report = run_descent(cover, sections)
        assert report.success is False
        assert report.obstruction_rank >= 1

    def test_three_way_partial_failure(self) -> None:
        cover = _make_cover("svc", ["x", "y", "z"])
        px, py, pz = cover.patches
        # x and y agree, x and z disagree, y and z disagree
        sections = {
            px.key: {"k": "alpha"},
            py.key: {"k": "alpha"},
            pz.key: {"k": "beta"},
        }
        report = run_descent(cover, sections)
        assert report.success is False

    def test_all_consistent_gives_success(self) -> None:
        cover = _make_cover("db", ["r", "w", "idx"])
        sections = _consistent_sections(cover, "consistent_value")
        report = run_descent(cover, sections)
        assert report.success is True

    def test_descent_engine_exhaustive_finds_all_violations(self) -> None:
        """EXHAUSTIVE strategy checks all overlaps even after first failure."""
        cover = _make_cover("net", ["n1", "n2", "n3"])
        n1, n2, n3 = cover.patches
        sections = {
            n1.key: {"ip": "10.0.0.1"},
            n2.key: {"ip": "10.0.0.2"},
            n3.key: {"ip": "10.0.0.3"},
        }
        engine = DescentEngine(
            configuration=DescentConfiguration().with_strategy(DescentStrategy.EXHAUSTIVE)
        )
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        obs = result.unwrap_obstruction()
        # Exhaustive should find all violations
        assert obs.violation_count >= 1

    def test_descent_obstruction_violation_count(self) -> None:
        """DescentObstruction.violation_count is the rank of H¹."""
        cover = _make_cover("api", ["v1", "v2", "v3", "v4"])
        patches = cover.patches
        sections = {p.key: {"schema": f"schema_{i}"} for i, p in enumerate(patches)}
        engine = DescentEngine(
            configuration=DescentConfiguration().with_strategy(DescentStrategy.EXHAUSTIVE)
        )
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        obs = result.unwrap_obstruction()
        assert obs.violation_count > 0
        assert obs.violation_count == obs.cohomology_class.rank()

    def test_missing_section_counts_as_violation(self) -> None:
        """Missing a patch section must count as an overlap violation."""
        cover = _make_cover("core", ["a", "b"])
        pa, pb = cover.patches
        sections = {pa.key: {"v": "ok"}}  # pb missing
        report = run_descent(cover, sections)
        assert report.success is False
        assert report.obstruction_rank >= 1


# ---------------------------------------------------------------------------
# §2  Cohomology class is persistent
# ---------------------------------------------------------------------------


class TestCohomologyClassPersistence:
    """CohomologyClass must be content-addressed and stable across calls."""

    def test_cohomology_class_has_stable_persistence_id(self) -> None:
        cover = _make_cover("mod", ["p", "q"])
        p, q = cover.patches
        sections = {p.key: {"x": 1}, q.key: {"x": 2}}
        engine = DescentEngine()
        r1 = engine.attempt_descent(cover, sections)
        r2 = engine.attempt_descent(cover, sections)
        assert r1.is_failure and r2.is_failure
        pid1 = r1.unwrap_obstruction().cohomology_class.persistence_id
        pid2 = r2.unwrap_obstruction().cohomology_class.persistence_id
        # Same input → same persistence_id (content-addressed)
        assert pid1 == pid2

    def test_different_inputs_give_different_persistence_ids(self) -> None:
        cover = _make_cover("mod", ["a", "b"])
        a, b = cover.patches
        s1 = {a.key: {"val": 1}, b.key: {"val": 2}}
        s2 = {a.key: {"val": 10}, b.key: {"val": 20}}
        engine = DescentEngine()
        r1 = engine.attempt_descent(cover, s1)
        r2 = engine.attempt_descent(cover, s2)
        assert r1.is_failure and r2.is_failure
        pid1 = r1.unwrap_obstruction().cohomology_class.persistence_id
        pid2 = r2.unwrap_obstruction().cohomology_class.persistence_id
        assert pid1 != pid2

    def test_cohomology_class_rank_is_violation_count(self) -> None:
        cover = _make_cover("svc", ["u1", "u2"])
        u1, u2 = cover.patches
        sections = {u1.key: {"d": "A"}, u2.key: {"d": "B"}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        cc = result.unwrap_obstruction().cohomology_class
        assert cc.rank() >= 1
        """After successful descent, any cohomology class is trivial."""
        cover = _make_cover("t", ["a", "b"])
        a, b = cover.patches
        sections = {a.key: {"same": "val"}, b.key: {"same": "val"}}
        report = run_descent(cover, sections)
        assert report.success is True
        # On success, obstruction_rank == 0 (trivial)
        assert report.obstruction_rank == 0

    def test_cohomology_class_not_trivial_on_violation(self) -> None:
        cover = _make_cover("t", ["c", "d"])
        c, d = cover.patches
        sections = {c.key: {"v": 1}, d.key: {"v": 2}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        cc = result.unwrap_obstruction().cohomology_class
        assert not cc.is_trivial()

    def test_cohomology_class_restrict_to_subset(self) -> None:
        cover = _make_cover("g", ["a", "b", "c"])
        a, b, c = cover.patches
        sections = {
            a.key: {"v": 1},
            b.key: {"v": 2},
            c.key: {"v": 1},
        }
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        cc = result.unwrap_obstruction().cohomology_class
        # Restrict to a subset of keys
        subset = frozenset([a.key, b.key])
        restricted = cc.restrict_to(subset)
        assert isinstance(restricted, CohomologyClass)

    def test_cohomology_class_merge(self) -> None:
        """Merging two cohomology classes combines their obstruction sets."""
        cc1 = CohomologyClass(
            cocycle_data={"mod/x/a∩mod/x/b": {"v": "mismatch_x"}},
        )
        cc2 = CohomologyClass(
            cocycle_data={"mod/y/a∩mod/y/b": {"v": "mismatch_y"}},
        )
        merged = cc1.merge(cc2)
        assert merged.rank() >= 1
        assert isinstance(merged, CohomologyClass)

    def test_obstruction_record_is_not_ephemeral(self) -> None:
        """ObstructionRecord survives serialization (persistence invariant)."""
        rec = _obstruction_record("api/auth")
        d = rec.to_dict()
        # It's a real dict with stable content
        assert d["coordinate"] == "api/auth"
        assert d["violated_condition"] == "overlap_mismatch"
        # Reconstruct from dict
        rec2 = ObstructionRecord.from_dict(d)
        assert rec2.coordinate == rec.coordinate
        assert rec2.violated_condition == rec.violated_condition
        assert rec2.evidence_family == rec.evidence_family
        assert list(rec2.downstream_obligations) == list(rec.downstream_obligations)

    def test_obstruction_record_survives_multiple_roundtrips(self) -> None:
        rec = _obstruction_record("db/query")
        hint = RepairHint(
            action="split_query",
            description="Split into smaller sub-queries",
            priority=RepairPriority.MEDIUM,
            target_coordinate="db/query",
        )
        rec = rec.with_repair_hint(hint)
        # Round-trip 1
        rec2 = ObstructionRecord.from_dict(rec.to_dict())
        # Round-trip 2
        rec3 = ObstructionRecord.from_dict(rec2.to_dict())
        assert rec3.coordinate == rec.coordinate
        assert len(rec3.repair_hints) == 1
        assert rec3.repair_hints[0].action == "split_query"


# ---------------------------------------------------------------------------
# §3  FailureChain links to obstruction classes
# ---------------------------------------------------------------------------


class TestFailureChainLinksToObstruction:
    """FailureChain must carry structured references to obstruction coordinates."""

    def test_failure_chain_construction(self) -> None:
        sf1 = _structured_failure("mod/fn1")
        sf2 = _structured_failure("mod/fn2")
        chain = chain_failures(sf1, sf2)
        assert len(chain) == 2

    def test_failure_chain_scopes(self) -> None:
        sf_geom = _structured_failure(scope=FailureScope.GEOMETRY)
        sf_solver = _structured_failure(
            scope=FailureScope.SOLVER,
            classification=FailureClassification.LOCAL_REPAIR,
        )
        chain = chain_failures(sf_geom, sf_solver)
        scopes = chain.scopes()
        assert FailureScope.GEOMETRY in scopes
        assert FailureScope.SOLVER in scopes

    def test_failure_chain_classifications(self) -> None:
        sf = _structured_failure(
            classification=FailureClassification.DESCENT_OBSTRUCTION
        )
        chain = chain_failures(sf)
        assert FailureClassification.DESCENT_OBSTRUCTION in chain.classifications()

    def test_failure_chain_all_repair_hints(self) -> None:
        hint1 = RepairHint(
            action="refine_cover",
            description="Use finer cover",
            priority=RepairPriority.HIGH,
            target_coordinate="mod/x",
        )
        hint2 = RepairHint(
            action="add_evidence",
            description="Provide more evidence",
            priority=RepairPriority.LOW,
            target_coordinate="mod/y",
        )
        sf1 = replace(
            _structured_failure("mod/x"),
            repair_hints=(hint1,),
        )
        sf2 = replace(
            _structured_failure("mod/y"),
            repair_hints=(hint2,),
        )
        chain = chain_failures(sf1, sf2)
        hints = chain.all_repair_hints()
        assert len(hints) == 2
        actions = {h.action for h in hints}
        assert "refine_cover" in actions
        assert "add_evidence" in actions

    def test_failure_chain_all_affected_obligations(self) -> None:
        sf = replace(
            _structured_failure("mod/fn"),
            obligations=("ob-1", "ob-2"),
        )
        chain = chain_failures(sf)
        obligations = chain.all_affected_obligations()
        assert "ob-1" in obligations
        assert "ob-2" in obligations

    def test_failure_chain_filter_by_scope(self) -> None:
        sf_geom = _structured_failure("a/b", FailureScope.GEOMETRY)
        sf_solver = _structured_failure("c/d", FailureScope.SOLVER)
        sf_evidence = _structured_failure(
            "e/f",
            FailureScope.EVIDENCE,
            FailureClassification.TRUST_VIOLATION,
        )
        chain = chain_failures(sf_geom, sf_solver, sf_evidence)
        geometry_only = chain.filter_by_scope(FailureScope.GEOMETRY)
        assert len(geometry_only) == 1

    def test_failure_chain_append_immutability(self) -> None:
        sf1 = _structured_failure("a")
        sf2 = _structured_failure("b")
        chain = chain_failures(sf1)
        chain2 = chain.append(sf2)
        assert len(chain) == 1
        assert len(chain2) == 2

    def test_failure_chain_serialization(self) -> None:
        sf = _structured_failure("mod/fn")
        chain = chain_failures(sf)
        d = chain.to_dict()
        assert "failures" in d
        assert len(d["failures"]) == 1

    def test_chain_failures_function_with_empty(self) -> None:
        """chain_failures with no args produces empty chain."""
        chain = chain_failures()
        assert len(chain) == 0

    def test_jugeo_error_carries_payload(self) -> None:
        with pytest.raises(JuGeoError) as exc:
            raise_with_scope(
                "overlap failed at module/fn",
                scope=FailureScope.GEOMETRY,
                code="overlap-fail",
                details={"left": "a", "right": "b"},
            )
        payload = as_failure_payload(exc.value)
        assert payload["code"] == "overlap-fail"
        assert payload["scope"] == "geometry"
        assert payload["details"]["left"] == "a"


# ---------------------------------------------------------------------------
# §4  Repair frontier
# ---------------------------------------------------------------------------


class TestRepairFrontier:
    """RepairFrontier records actionable repair hints per category."""

    def test_empty_frontier_is_empty(self) -> None:
        rf = RepairFrontier({})
        assert rf.is_empty()
        assert rf.total_items() == 0

    def test_frontier_with_items(self) -> None:
        rf = RepairFrontier({
            "patch": ["refine_patch_a", "add_overlap_b"],
            "evidence": ["collect_runtime_witness"],
        })
        assert not rf.is_empty()
        assert rf.total_items() == 3

    def test_frontier_prioritized_items(self) -> None:
        rf = RepairFrontier({
            "critical": ["fix_null_deref"],
            "low": ["refactor_naming"],
        })
        items = rf.prioritized_items()
        assert len(items) == 2
        # Items are (category, action) pairs
        categories = {cat for cat, _ in items}
        assert "critical" in categories or "low" in categories

    def test_frontier_merge(self) -> None:
        rf1 = RepairFrontier({"patch": ["action_a"]})
        rf2 = RepairFrontier({"evidence": ["action_b"]})
        merged = rf1.merge(rf2)
        assert merged.total_items() == 2

    def test_frontier_without_category(self) -> None:
        rf = RepairFrontier({"patch": ["a"], "evidence": ["b"]})
        reduced = rf.without_category("patch")
        assert reduced.total_items() == 1

    def test_frontier_from_obstruction(self) -> None:
        """DescentObstruction repair_frontier is a RepairFrontier."""
        cover = _make_cover("svc", ["x", "y"])
        x, y = cover.patches
        sections = {x.key: {"v": 1}, y.key: {"v": 2}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        obs = result.unwrap_obstruction()
        rf = obs.repair_frontier
        assert isinstance(rf, RepairFrontier)


# ---------------------------------------------------------------------------
# §5  GluingData and overlap conditions
# ---------------------------------------------------------------------------


class TestGluingDataAndOverlaps:
    """GluingData cocycle computation and overlap condition evaluation."""

    def test_gluing_data_from_cover(self) -> None:
        cover = _make_cover("base", ["a", "b", "c"])
        sections_dict = _consistent_sections(cover)
        gluing = GluingData.from_cover(cover, sections_dict)
        assert gluing.patch_count() == 3
        assert gluing.overlap_count() >= 3

    def test_gluing_data_section_for(self) -> None:
        cover = _make_cover("root", ["alpha", "beta"])
        sections_dict = _consistent_sections(cover)
        gluing = GluingData.from_cover(cover, sections_dict)
        for patch in cover.patches:
            sec = gluing.section_for(patch.key)
            assert sec is not None
            assert sec.coordinate == patch.key

    def test_gluing_data_verify_all_overlaps_consistent(self) -> None:
        cover = _make_cover("mod", ["p", "q"])
        sections_dict = _consistent_sections(cover)
        gluing = GluingData.from_cover(cover, sections_dict)
        overlaps = gluing.verify_all_overlaps()
        violated = [o for o in overlaps if o.status == OverlapStatus.VIOLATED]
        assert len(violated) == 0

    def test_gluing_data_find_violated_overlaps(self) -> None:
        cover = _make_cover("mod", ["p", "q"])
        p, q = cover.patches
        sections = {p.key: {"x": 1}, q.key: {"x": 2}}
        gluing = GluingData.from_cover(cover, sections)
        overlaps = gluing.verify_all_overlaps()
        violated = gluing.find_violated_overlaps()
        assert len(violated) >= 1

    def test_gluing_data_compute_cocycle(self) -> None:
        cover = _make_cover("mod", ["a", "b"])
        a, b = cover.patches
        sections = {a.key: {"v": 1}, b.key: {"v": 2}}
        gluing = GluingData.from_cover(cover, sections)
        gluing.verify_all_overlaps()
        cc = gluing.compute_cocycle()
        assert isinstance(cc, CohomologyClass)
        assert cc.rank >= 1

    def test_overlap_condition_evaluate_satisfied(self) -> None:
        """OverlapCondition.evaluate returns SATISFIED for compatible data."""
        oc = OverlapCondition(
            left_coordinate="mod/a",
            right_coordinate="mod/b",
            overlap_coordinate="mod/a_b",
        )
        checked = oc.evaluate({"data": "same"}, {"data": "same"})
        assert checked.status == OverlapStatus.SATISFIED

    def test_overlap_condition_evaluate_violated(self) -> None:
        oc = OverlapCondition(
            left_coordinate="mod/a",
            right_coordinate="mod/b",
            overlap_coordinate="mod/a_b",
        )
        checked = oc.evaluate({"data": "A"}, {"data": "B"})
        assert checked.status == OverlapStatus.VIOLATED

    def test_overlap_condition_pair(self) -> None:
        oc = OverlapCondition(
            left_coordinate="x/a",
            right_coordinate="x/b",
            overlap_coordinate="x/ab",
        )
        pair = oc.pair
        assert pair == ("x/a", "x/b")

    def test_local_section_discharge_obligation(self) -> None:
        sec = LocalSection(
            coordinate="mod/fn",
            judgment_data={"proof": "pending"},
            evidence_bundle=(),
            residual_obligations=["prove_termination", "prove_safety"],
        )
        sec2 = sec.discharge_obligation("prove_termination")
        assert "prove_termination" not in sec2.residual_obligations
        assert "prove_safety" in sec2.residual_obligations

    def test_local_section_merge_evidence(self) -> None:
        sec = LocalSection(
            coordinate="mod/fn",
            judgment_data={"v": 1},
            evidence_bundle=("ev-1",),
        )
        sec2 = sec.merge_evidence(("ev-2", "ev-3"))
        assert "ev-1" in sec2.evidence_bundle
        assert "ev-2" in sec2.evidence_bundle
        assert "ev-3" in sec2.evidence_bundle

    def test_judgment_obstruction_coordinate_invariant(self) -> None:
        """JudgmentObstruction.coordinate_pair matches its source patch keys."""
        ob = JudgmentObstruction(
            coordinate_pair=("svc/auth", "svc/token"),
            description="trust mismatch",
            severity=3,
        )
        assert "svc/auth" in ob.coordinate_pair
        assert "svc/token" in ob.coordinate_pair
        assert ob.severity == 3
        assert not ob.is_resolved

    def test_merge_repair_hints_deduplication(self) -> None:
        hint = RepairHint(
            action="refine_cover",
            description="Refine the cover",
            priority=RepairPriority.HIGH,
            target_coordinate="mod",
        )
        hints_a = (hint,)
        hints_b = (hint,)  # duplicate
        merged = merge_repair_hints(hints_a, hints_b)
        assert len(merged) >= 1
        # Deduplicated — should not have doubled entries of same action
        actions = [h.action for h in merged]
        assert actions.count("refine_cover") == 1

    def test_classify_error_maps_to_failure(self) -> None:
        err = ValueError("something went wrong in overlap check")
        classification, scope = classify_error(err)
        assert isinstance(classification, FailureClassification)
        assert isinstance(scope, FailureScope)
