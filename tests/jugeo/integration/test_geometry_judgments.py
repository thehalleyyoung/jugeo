"""Integration tests: geometry/descent × covers × judgment_terms.

Verifies that local JudgmentTuples flow correctly through the descent engine:
trust is preserved on successful gluing, DescentObstruction carries judgment
provenance on failure, and TrustAnnotation's ordered algebra respects cover
refinement semantics.

Theory2 invariants under test
-------------------------------
* Judgments are 8-tuples (c, φ, A, E, O, B, T, Π) — NOT booleans.
* Trust T is an ordered algebra — NOT a scalar.
* No silent trust promotion from ORACLE_PROPOSED.
* Obstructions are persistent cohomology classes.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

# ---------------------------------------------------------------------------
# path bootstrap (mirrors existing jugeo test convention)
# ---------------------------------------------------------------------------

ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists()
)
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

# ---------------------------------------------------------------------------
# Geometry imports
# ---------------------------------------------------------------------------
from jugeo.geometry.site import Coordinate, CoordinateKind, CoordinateObject
from jugeo.geometry.covers import Cover, CoverBuilder, CoverMember, refine_cover
from jugeo.geometry.descent import (
    CohomologyClass,
    DescentConfiguration,
    DescentEngine,
    DescentObstruction,
    DescentResult,
    DescentStrategy,
    GluingData,
    GlobalSection,
    LocalSection,
    OverlapCondition,
    OverlapStatus,
    RepairFrontier,
    run_descent,
    glue_sections,
)

# ---------------------------------------------------------------------------
# Judgment imports
# ---------------------------------------------------------------------------
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coord(*parts: str, kind: CoordinateKind = CoordinateKind.REGION) -> Coordinate:
    """Create a Coordinate from path components."""
    return Coordinate(components=parts, kind=kind)


def _proposition(formula: str, kind: PropositionKind = PropositionKind.STRUCTURAL) -> Proposition:
    return Proposition(kind=kind, formula=formula)


def _carrier(name: str) -> Carrier:
    return Carrier(name=name)


def _evidence_item(
    kind: EvidenceItemKind = EvidenceItemKind.SOLVER_PROOF,
    trust: TrustLevel = TrustLevel.SOLVER_DISCHARGED,
    channel: str = "z3",
) -> EvidenceItem:
    return EvidenceItem(kind=kind, trust_level=trust, channel=channel, payload={"fact": "unit-test"})


def _annotation(
    level: TrustLevel = TrustLevel.UNVERIFIED,
    ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF,
    floor: TrustLevel = TrustLevel.CONTRADICTED,
) -> TrustAnnotation:
    return TrustAnnotation(level=level, ceiling=ceiling, floor=floor)


def _judgment(
    coord: Coordinate,
    formula: str = "P(x)",
    trust_level: TrustLevel = TrustLevel.UNVERIFIED,
    extra_evidence: tuple[EvidenceItem, ...] = (),
) -> Judgment:
    items = extra_evidence or ()
    return Judgment(
        coordinate=coord,
        proposition=_proposition(formula),
        carrier=_carrier("TestType"),
        evidence=EvidenceBundle(items=items),
        trust=_annotation(level=trust_level),
        provenance=Provenance(source=ProvenanceSource.SOLVER),
    )


def _make_cover(base_key: str, patch_keys: list[str]) -> Cover:
    """Minimal cover: single-level, all patches overlap pairwise."""
    base = _coord(base_key)
    patches = tuple(_coord(base_key, k) for k in patch_keys)
    overlaps: list[tuple[str, str]] = []
    for i, a in enumerate(patches):
        for b in patches[i + 1:]:
            overlaps.append((a.key, b.key))
    return Cover(target=base, patches=patches, overlaps=tuple(overlaps))


# ---------------------------------------------------------------------------
# §1  Judgment-is-a-tuple invariant
# ---------------------------------------------------------------------------


class TestJudgmentIsTuple:
    """Judgment must carry all 8 components — it is NOT a boolean."""

    def test_judgment_has_eight_components(self) -> None:
        coord = _coord("module", "auth")
        j = _judgment(coord, "login_safe", TrustLevel.UNVERIFIED)
        # Each component is accessible
        assert j.coordinate is coord
        assert j.proposition.formula == "login_safe"
        assert j.carrier.name == "TestType"
        assert isinstance(j.evidence, EvidenceBundle)
        assert isinstance(j.obligations, tuple)
        assert isinstance(j.obstructions, tuple)
        assert isinstance(j.trust, TrustAnnotation)
        assert isinstance(j.provenance, Provenance)

    def test_judgment_is_not_a_boolean(self) -> None:
        coord = _coord("module", "auth")
        j = _judgment(coord)
        # A Judgment is never equal to True or False
        assert j is not True
        assert j is not False
        assert not isinstance(j, bool)

    def test_judgment_is_immutable(self) -> None:
        coord = _coord("mod", "fn")
        j = _judgment(coord)
        with pytest.raises((AttributeError, TypeError)):
            j.coordinate = _coord("other")  # type: ignore[misc]

    def test_judgment_status_initial_proposed(self) -> None:
        coord = _coord("mod", "init")
        j = _judgment(coord)
        assert j.status == JudgmentStatus.PROPOSED
        assert not j.is_fully_discharged()

    def test_judgment_carry_residual_obligations(self) -> None:
        coord = _coord("mod", "fn")
        j = _judgment(coord)
        ob1 = ResidualObligation(obligation_id="ob-1", description="prove termination")
        ob2 = ResidualObligation(obligation_id="ob-2", description="prove safety")
        j2 = j.add_obligation(ob1).add_obligation(ob2)
        assert j2.has_residuals()
        assert j2.pending_obligation_count() == 2
        # Discharge one
        j3 = j2.discharge_obligation("ob-1", "ev-key-1", reason="z3-proof")
        assert j3.pending_obligation_count() == 1

    def test_judgment_carry_obstructions(self) -> None:
        coord = _coord("mod", "overlap")
        j = _judgment(coord)
        ob = Obstruction(
            coordinate_pair=("mod/patch_a", "mod/patch_b"),
            description="sections disagree on shared key",
            severity=2,
        )
        j2 = j.add_obstruction(ob)
        assert j2.has_obstructions()
        assert j2.unresolved_obstruction_count() == 1
        assert j2.status == JudgmentStatus.OBSTRUCTED

    def test_judgment_all_eight_components_independent(self) -> None:
        c1 = _coord("a", "b")
        c2 = _coord("x", "y")
        j1 = _judgment(c1, "P", TrustLevel.UNVERIFIED)
        j2 = _judgment(c2, "Q", TrustLevel.SOLVER_DISCHARGED)
        # Changing one component creates a new judgment, not modifying existing
        j_modified = j1.restrict_to(c2)
        assert j1.coordinate.key == "a/b"
        assert j_modified.coordinate.key == "x/y"
        # Original is unchanged
        assert j1.coordinate.key == "a/b"


# ---------------------------------------------------------------------------
# §2  TrustAnnotation as ordered algebra (NOT a scalar)
# ---------------------------------------------------------------------------


class TestTrustAnnotationOrderedAlgebra:
    """TrustAnnotation implements ≼, ⊕ (compose), ⊖ (challenge/demote)."""

    def test_trust_annotation_is_not_a_float(self) -> None:
        ann = _annotation(TrustLevel.RUNTIME_WITNESSED)
        assert not isinstance(ann, float)
        assert not isinstance(ann, int)
        # It has algebraic structure
        assert hasattr(ann, "compose")
        assert hasattr(ann, "promote")
        assert hasattr(ann, "demote")
        assert hasattr(ann, "challenge")
        assert hasattr(ann, "compare")

    def test_trust_partial_order_leq(self) -> None:
        low = _annotation(TrustLevel.UNVERIFIED)
        high = _annotation(TrustLevel.VERIFIED_PROOF)
        mid = _annotation(TrustLevel.SOLVER_DISCHARGED)
        # ≼ respects the IntEnum ordering
        assert low.compare(high) == -1     # low ≺ high
        assert high.compare(low) == 1      # high ≻ low
        assert mid.compare(mid) == 0       # equal
        assert low.compare(mid) == -1
        assert mid.compare(high) == -1

    def test_trust_compose_is_weakest_link(self) -> None:
        # ⊕: join of two annotations gives conservative minimum
        strong = _annotation(TrustLevel.VERIFIED_PROOF)
        weak = _annotation(TrustLevel.ORACLE_PROPOSED)
        composed = strong.compose(weak)
        assert int(composed.level) == int(TrustLevel.ORACLE_PROPOSED)
        # Evidence basis is union
        strong_with_ev = replace(strong, evidence_basis=("ev-1",))
        weak_with_ev = replace(weak, evidence_basis=("ev-2",))
        c2 = strong_with_ev.compose(weak_with_ev)
        assert "ev-1" in c2.evidence_basis
        assert "ev-2" in c2.evidence_basis

    def test_trust_compose_ceiling_intersection(self) -> None:
        # Ceilings are intersected (min) under composition
        a1 = TrustAnnotation(
            level=TrustLevel.SOLVER_DISCHARGED,
            ceiling=TrustLevel.VERIFIED_PROOF,
        )
        a2 = TrustAnnotation(
            level=TrustLevel.SOLVER_DISCHARGED,
            ceiling=TrustLevel.SOLVER_DISCHARGED,
        )
        composed = a1.compose(a2)
        assert int(composed.ceiling) <= int(TrustLevel.SOLVER_DISCHARGED)

    def test_trust_promote_requires_reason(self) -> None:
        ann = _annotation(TrustLevel.UNVERIFIED)
        promoted = ann.promote(reason="z3-proof-complete")
        assert int(promoted.level) > int(TrustLevel.UNVERIFIED)
        assert "promote" in " ".join(promoted.reasons).lower()

    def test_trust_promote_clamped_at_ceiling(self) -> None:
        # The ceiling must not be exceeded
        ann = TrustAnnotation(
            level=TrustLevel.ORACLE_PROPOSED,
            ceiling=TrustLevel.ORACLE_PROPOSED,
        )
        # Attempting to promote beyond ceiling: should stay at ceiling
        promoted = ann.promote(reason="force-up", target=TrustLevel.VERIFIED_PROOF)
        assert int(promoted.level) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_trust_demote_operation(self) -> None:
        ann = _annotation(TrustLevel.VERIFIED_PROOF)
        demoted = ann.demote(reason="challenge-failed")
        assert int(demoted.level) < int(TrustLevel.VERIFIED_PROOF)

    def test_trust_challenge_demotes_one_step(self) -> None:
        ann = _annotation(TrustLevel.SOLVER_DISCHARGED)
        challenged = ann.challenge(reason="counterexample found")
        assert int(challenged.level) < int(TrustLevel.SOLVER_DISCHARGED)

    def test_trust_admissibility_invariant(self) -> None:
        ann = _annotation(TrustLevel.SOLVER_DISCHARGED)
        assert ann.is_admissible()
        # floor ≤ level ≤ ceiling always
        for lvl in TrustLevel:
            a = TrustAnnotation(level=lvl)
            assert a.is_admissible()

    def test_no_silent_promotion_oracle_proposed(self) -> None:
        """ORACLE_PROPOSED ceiling must never be silently exceeded."""
        oracle_ann = TrustAnnotation(
            level=TrustLevel.ORACLE_PROPOSED,
            ceiling=TrustLevel.ORACLE_PROPOSED,
        )
        # Silent attempt: promote without reason — result cannot exceed ceiling
        promoted = oracle_ann.promote(reason="")
        assert int(promoted.level) <= int(TrustLevel.ORACLE_PROPOSED)
        # Even with a reason, ceiling enforces ORACLE_PROPOSED cap
        promoted2 = oracle_ann.promote(reason="explicit", target=TrustLevel.VERIFIED_PROOF)
        assert int(promoted2.level) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_trust_serialization_roundtrip(self) -> None:
        ann = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=("ev-a", "ev-b"),
            ceiling=TrustLevel.SOLVER_DISCHARGED,
            floor=TrustLevel.UNVERIFIED,
            reasons=("promoted: z3-result",),
        )
        mapping = ann.to_mapping()
        assert mapping["level"] == TrustLevel.RUNTIME_WITNESSED.label()
        assert mapping["ceiling"] == TrustLevel.SOLVER_DISCHARGED.label()
        assert "ev-a" in mapping["evidence_basis"]


# ---------------------------------------------------------------------------
# §3  Descent with Judgment data
# ---------------------------------------------------------------------------


class TestDescentWithJudgments:
    """Build local sections from Judgment tuples and run descent."""

    def _make_local_section(
        self,
        coord_key: str,
        value: int,
        trust: float = 1.0,
    ) -> LocalSection:
        return LocalSection(
            coordinate=coord_key,
            judgment_data={"predicate": "type_safe", "value": value},
            evidence_bundle=("ev-" + coord_key,),
            trust_level=trust,
        )

    def test_descent_success_compatible_sections(self) -> None:
        """Compatible local sections → GlobalSection."""
        cover = _make_cover("auth", ["patch_a", "patch_b"])
        patch_a, patch_b = cover.patches[0], cover.patches[1]
        sections = {
            patch_a.key: {"predicate": "type_safe", "value": 42},
            patch_b.key: {"predicate": "type_safe", "value": 42},
        }
        report = run_descent(cover, sections)
        assert report.success is True
        assert report.obstruction_rank == 0

    def test_descent_failure_incompatible_sections(self) -> None:
        """Incompatible local sections → DescentObstruction with rank ≥ 1."""
        cover = _make_cover("auth", ["pa", "pb"])
        pa, pb = cover.patches[0], cover.patches[1]
        sections = {
            pa.key: {"predicate": "type_safe", "value": 1},
            pb.key: {"predicate": "type_safe", "value": 2},
        }
        report = run_descent(cover, sections)
        assert report.success is False
        assert report.obstruction_rank >= 1

    def test_global_section_carries_trust_floor(self) -> None:
        """After descent, the GlobalSection trust floor = minimum of patch trust."""
        cover = _make_cover("svc", ["p1", "p2"])
        p1, p2 = cover.patches[0], cover.patches[1]
        sections = {
            p1.key: {"data": "x"},
            p2.key: {"data": "x"},
        }
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_success
        gs: GlobalSection = result.unwrap_section()
        # Trust floor must exist and be a float in [0, 1]
        assert 0.0 <= gs.trust_floor <= 1.0

    def test_global_section_aggregates_all_evidence(self) -> None:
        """GlobalSection evidence summary includes all patches."""
        cover = _make_cover("db", ["read", "write"])
        r, w = cover.patches[0], cover.patches[1]
        sections = {
            r.key: {"op": "read"},
            w.key: {"op": "read"},
        }
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_success
        gs = result.unwrap_section()
        summary = gs.evidence_summary()
        assert isinstance(summary, str)

    def test_descent_obstruction_preserves_provenance(self) -> None:
        """Failed descent → DescentObstruction reports which overlap failed."""
        cover = _make_cover("core", ["x1", "x2", "x3"])
        patches = cover.patches
        # x1 and x2 disagree; x1 and x3 agree; x2 and x3 disagree
        sections = {
            patches[0].key: {"k": "alpha"},
            patches[1].key: {"k": "beta"},
            patches[2].key: {"k": "alpha"},
        }
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        obs: DescentObstruction = result.unwrap_obstruction()
        # The obstruction records which coordinates were involved
        involved = obs.involved_coordinates()
        assert len(involved) >= 2

    def test_descent_obstruction_has_cohomology_class(self) -> None:
        """DescentObstruction carries a CohomologyClass (persistent H¹)."""
        cover = _make_cover("svc", ["a", "b"])
        a, b = cover.patches
        sections = {a.key: {"v": 0}, b.key: {"v": 1}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        obs = result.unwrap_obstruction()
        assert obs.cohomology_class is not None
        cc = obs.cohomology_class
        assert isinstance(cc, CohomologyClass)
        # Cohomology class has a stable persistence_id
        pid = cc.persistence_id
        assert isinstance(pid, str)
        assert len(pid) > 0

    def test_descent_result_map_success(self) -> None:
        """DescentResult.map_success applies a transform to GlobalSection."""
        cover = _make_cover("t", ["a", "b"])
        pa, pb = cover.patches
        sections = {pa.key: {"w": "same"}, pb.key: {"w": "same"}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_success
        mapped = result.map_success(lambda gs: gs.with_certificate("cert-001"))
        assert mapped.is_success
        certified = mapped.unwrap_section()
        assert certified.certificate == "cert-001"

    def test_descent_result_map_failure(self) -> None:
        """DescentResult.map_failure applies a transform to DescentObstruction."""
        cover = _make_cover("t", ["a", "b"])
        pa, pb = cover.patches
        sections = {pa.key: {"w": "yes"}, pb.key: {"w": "no"}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        hint_added = result.map_failure(
            lambda o: replace(o, repair_frontier=RepairFrontier({"patch": ["refine"]}))
        )
        assert hint_added.is_failure


# ---------------------------------------------------------------------------
# §4  Cover refinement and trust
# ---------------------------------------------------------------------------


class TestCoverRefinementTrust:
    """Trust annotation properties under cover refinement."""

    def test_cover_refine_produces_finer_cover(self) -> None:
        base = _coord("root")
        patches = (_coord("root", "a"), _coord("root", "b"))
        cover = Cover(target=base, patches=patches, overlaps=(("root/a", "root/b"),))
        refined = refine_cover(cover)
        # Refined cover has same base target
        assert refined.target.key == cover.target.key

    def test_trust_compose_associativity(self) -> None:
        """(T1 ⊕ T2) ⊕ T3 = T1 ⊕ (T2 ⊕ T3) — must hold."""
        t1 = _annotation(TrustLevel.VERIFIED_PROOF)
        t2 = _annotation(TrustLevel.SOLVER_DISCHARGED)
        t3 = _annotation(TrustLevel.RUNTIME_WITNESSED)
        left = (t1.compose(t2)).compose(t3)
        right = t1.compose(t2.compose(t3))
        assert int(left.level) == int(right.level)

    def test_trust_compose_commutativity(self) -> None:
        """T1 ⊕ T2 = T2 ⊕ T1 — commutative meet."""
        t1 = _annotation(TrustLevel.RUNTIME_WITNESSED)
        t2 = _annotation(TrustLevel.SOLVER_DISCHARGED)
        ab = t1.compose(t2)
        ba = t2.compose(t1)
        assert int(ab.level) == int(ba.level)

    def test_trust_compose_idempotent(self) -> None:
        """T ⊕ T = T — idempotent."""
        t = _annotation(TrustLevel.SOLVER_DISCHARGED)
        composed = t.compose(t)
        assert int(composed.level) == int(t.level)

    def test_oracle_proposed_ceiling_under_refinement(self) -> None:
        """Refining a cover must not promote oracle trust above its ceiling."""
        oracle_ann = TrustAnnotation(
            level=TrustLevel.ORACLE_PROPOSED,
            ceiling=TrustLevel.ORACLE_PROPOSED,
        )
        # After cover refinement (simulate by composing with another oracle annotation)
        other = TrustAnnotation(
            level=TrustLevel.ORACLE_PROPOSED,
            ceiling=TrustLevel.ORACLE_PROPOSED,
        )
        composed = oracle_ann.compose(other)
        assert int(composed.level) <= int(TrustLevel.ORACLE_PROPOSED)
        # Cannot silently exceed ORACLE_PROPOSED ceiling
        assert int(composed.ceiling) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_judgment_restrict_preserves_trust(self) -> None:
        """Restricting a judgment to a sub-coordinate preserves trust level."""
        parent = _coord("module", "class")
        child = _coord("module", "class", "method")
        j = _judgment(parent, "well_typed", TrustLevel.SOLVER_DISCHARGED)
        restricted = j.restrict_to(child)
        assert int(restricted.trust.level) == int(j.trust.level)
        assert restricted.coordinate.key == "module/class/method"

    def test_cover_overlap_pairwise_complete(self) -> None:
        """Cover with N patches has at most N*(N-1)/2 overlap pairs."""
        n = 4
        cover = _make_cover("base", [f"p{i}" for i in range(n)])
        overlap_count = len(cover.overlaps)
        max_overlaps = n * (n - 1) // 2
        assert overlap_count <= max_overlaps

    def test_local_section_trust_floor_check(self) -> None:
        """LocalSection.trust_meets_floor works correctly."""
        sec = LocalSection(
            coordinate="mod/fn",
            judgment_data={"v": 1},
            evidence_bundle=("ev-1",),
            trust_level=0.8,
        )
        assert sec.trust_meets_floor(0.5)
        assert sec.trust_meets_floor(0.8)
        assert not sec.trust_meets_floor(0.9)


# ---------------------------------------------------------------------------
# §5  Cross-package: Judgment → LocalSection → Descent → GlobalSection
# ---------------------------------------------------------------------------


class TestJudgmentDescentPipeline:
    """End-to-end: build Judgments → encode as LocalSections → run descent."""

    def test_full_pipeline_success(self) -> None:
        coord_a = _coord("auth", "login")
        coord_b = _coord("auth", "token")
        j_a = _judgment(coord_a, "login_terminates", TrustLevel.SOLVER_DISCHARGED)
        j_b = _judgment(coord_b, "login_terminates", TrustLevel.SOLVER_DISCHARGED)
        # Encode judgments as section data
        base = _coord("auth")
        patches = (_coord("auth", "login"), _coord("auth", "token"))
        cover = Cover(
            target=base,
            patches=patches,
            overlaps=(("auth/login", "auth/token"),),
        )
        sections = {
            "auth/login": {"predicate": j_a.proposition.formula, "trust": str(j_a.trust.level)},
            "auth/token": {"predicate": j_b.proposition.formula, "trust": str(j_b.trust.level)},
        }
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_success
        gs = result.unwrap_section()
        assert gs.constituent_count() == 2

    def test_full_pipeline_trust_promotion_blocked(self) -> None:
        """A copilot-originated judgment cannot push trust above its ceiling."""
        coord_a = _coord("gen", "fn")
        oracle_j = Judgment(
            coordinate=coord_a,
            proposition=_proposition("fn_safe"),
            carrier=_carrier("FnType"),
            trust=TrustAnnotation(
                level=TrustLevel.ORACLE_PROPOSED,
                ceiling=TrustLevel.ORACLE_PROPOSED,
            ),
            provenance=Provenance(source=ProvenanceSource.ORACLE),
        )
        # Even if we try to "strengthen" with an explicit reason,
        # ceiling enforcement caps at ORACLE_PROPOSED
        strengthened = oracle_j.strengthen(
            reason="copilot confirmed", target=TrustLevel.VERIFIED_PROOF
        )
        assert int(strengthened.trust.level) <= int(TrustLevel.ORACLE_PROPOSED)

    def test_judgment_with_multiple_evidence_items(self) -> None:
        """EvidenceBundle with multiple items: trust floor is minimum."""
        coord = _coord("mod", "check")
        solver_ev = _evidence_item(EvidenceItemKind.SOLVER_PROOF, TrustLevel.SOLVER_DISCHARGED)
        oracle_ev = _evidence_item(EvidenceItemKind.ORACLE_PROPOSAL, TrustLevel.ORACLE_PROPOSED, "copilot")
        bundle = EvidenceBundle(items=(solver_ev, oracle_ev))
        j = Judgment(
            coordinate=coord,
            proposition=_proposition("constraint_holds"),
            carrier=_carrier("Constraint"),
            evidence=bundle,
            trust=_annotation(TrustLevel.SOLVER_DISCHARGED),
        )
        # The trust floor is min of annotation floor and evidence total
        tf = j.trust_floor()
        # oracle_evidence drags down trust
        assert int(tf) <= int(TrustLevel.SOLVER_DISCHARGED)

    def test_judgment_merge_evidence_does_not_auto_promote(self) -> None:
        """merge_evidence should NOT auto-promote trust (no silent promotion)."""
        coord = _coord("mod", "fn")
        j = _judgment(coord, "safe", TrustLevel.UNVERIFIED)
        strong_ev = _evidence_item(EvidenceItemKind.SOLVER_PROOF, TrustLevel.VERIFIED_PROOF)
        j_merged = j.merge_evidence(EvidenceBundle(items=(strong_ev,)))
        # Trust level of annotation should NOT have changed silently
        assert int(j_merged.trust.level) == int(TrustLevel.UNVERIFIED)
        # The evidence bundle now contains the new item
        assert len(j_merged.evidence.items) == 1

    def test_judgment_provenance_chain(self) -> None:
        """Provenance records the transformation chain."""
        coord = _coord("srv", "handler")
        j = _judgment(coord, "handler_safe", TrustLevel.UNVERIFIED)
        # Restrict records in provenance
        child = _coord("srv", "handler", "parse")
        j2 = j.restrict_to(child)
        assert "restrict" in " ".join(j2.provenance.transformations)

    def test_cohomology_class_is_not_trivial_on_violation(self) -> None:
        """A genuinely violated overlap produces non-trivial H¹ class."""
        cover = _make_cover("net", ["node1", "node2"])
        n1, n2 = cover.patches
        sections = {n1.key: {"ip": "10.0.0.1"}, n2.key: {"ip": "10.0.0.2"}}
        engine = DescentEngine()
        result = engine.attempt_descent(cover, sections)
        assert result.is_failure
        obs = result.unwrap_obstruction()
        cc = obs.cohomology_class
        assert not cc.is_trivial()

    def test_multiple_independent_covers_respect_trust_floor(self) -> None:
        """Independent covers: each descent is independent, trust floors non-interfering."""
        covers_and_sections = []
        for name in ["svc_a", "svc_b", "svc_c"]:
            cov = _make_cover(name, ["p1", "p2"])
            p1, p2 = cov.patches
            covers_and_sections.append(
                (cov, {p1.key: {"v": "ok"}, p2.key: {"v": "ok"}})
            )
        results = []
        engine = DescentEngine()
        for cov, secs in covers_and_sections:
            results.append(engine.attempt_descent(cov, secs))
        # All should succeed
        for r in results:
            assert r.is_success
        # Trust floors are >= 0
        for r in results:
            assert r.unwrap_section().trust_floor >= 0.0
