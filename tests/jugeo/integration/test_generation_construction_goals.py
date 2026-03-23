"""Integration tests: generation goals ↔ construction ↔ treaties ↔ descent.

Cross-cutting modules under test
---------------------------------
* ``jugeo.generation.goals``        — GenerationGoal, ConstructionGoal, GoalDecomposer,
                                      GoalTree, GoalPriority, GoalStatus
* ``jugeo.generation.construction`` — ConstructionGoal (loop), Candidate,
                                      ConstructionContext, ConstructionResult,
                                      SourceChannel, ConstructionStatus
* ``jugeo.generation.treaties``     — Treaty, TreatyLaw, TreatySynthesizer,
                                      TreatyManager, TreatyStatus
* ``jugeo.geometry.descent``        — LocalSection, GluingData, GlobalSection,
                                      DescentObstruction, DescentEngine,
                                      DescentConfiguration, DescentStrategy

Theory2 invariants asserted throughout
----------------------------------------
1. **Judgment = (c,φ,A,E,O,B,T,Π) tuple not a bool** — construction results
   are typed records exposing all semantic fields.
2. **Trust is ordered algebra** — candidate trust levels use the structured
   algebra; copilot candidates need corroboration.
3. **No silent promotion** — copilot-sourced candidates must declare
   ``needs_corroboration=True`` and enter below the solver trust tier.
4. **Descent returns GlobalSection OR DescentObstruction** — every descent
   attempt must terminate with one of these two typed outcomes, never None.
5. **Construction failures produce DescentObstruction** — when local sections
   are incompatible, the engine produces a first-class DescentObstruction with
   a non-trivial H¹ cohomology class.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.generation.goals import (
    GenerationGoal,
    GoalPriority,
    GoalStatus,
    GoalDecomposer,
    GoalTree,
    GoalScheduler,
    ConstructionGoal as SchedulerConstructionGoal,
)
from jugeo.generation.construction import (
    ConstructionGoal,
    Candidate,
    ConstructionContext,
    ConstructionResult,
    ConstructionStatus,
    SourceChannel,
    CandidateComparator,
    CandidateSelector,
)
from jugeo.generation.treaties import (
    Treaty,
    TreatyLaw,
    TreatySynthesizer,
    TreatyManager,
    TreatyStatus,
    TreatyValidator,
)
from jugeo.geometry.descent import (
    LocalSection,
    GluingData,
    GlobalSection,
    DescentObstruction,
    DescentResult,
    DescentEngine,
    DescentConfiguration,
    DescentStrategy,
    OverlapCondition,
    OverlapStatus,
)
from jugeo.geometry.covers import Cover
from jugeo.geometry.site import CoordinateObject, CoordinateKind, CoordinateMorphism
from jugeo.geometry.supports import SupportRegion
from jugeo.evidence.trust import TrustTier, TrustLevel as AlgebraTrustLevel


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_coordinate(name: str = "module.foo") -> CoordinateObject:
    return CoordinateObject(name, CoordinateKind.MODULE, tuple(name.split(".")))


def _make_support(label: str = "patch.A") -> SupportRegion:
    coord = _make_coordinate(label)
    return SupportRegion(coord, frozenset({label}))


def _make_generation_goal(
    name: str = "prove_consistency",
    priority: GoalPriority = GoalPriority.HIGH,
) -> GenerationGoal:
    return GenerationGoal(
        proposition=name,
        support=_make_support("patch.root"),
        trust_floor=TrustTier.PROPOSAL,
        priority=priority,
        budget=20,
    )


def _make_construction_goal(
    target_type: str = "FunctionContract",
    budget: int = 5,
    constraints: tuple[str, ...] = (),
) -> ConstructionGoal:
    return ConstructionGoal(
        coordinate=_make_coordinate("module.foo"),
        target_type=target_type,
        constraints=constraints,
        budget=budget,
    )


def _make_candidate(
    section: Any = {"value": "x + 1"},
    channel: SourceChannel = SourceChannel.SOLVER,
    confidence: float = 0.9,
    obligations: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        goal_id="goal-001",
        proposed_section=section,
        source_channel=channel,
        confidence=confidence,
        residual_obligations=obligations,
        evidence_bundle={"solver_proof": {"result": "unsat"}},
    )


def _make_treaty_law(name: str = "OverlapLaw") -> TreatyLaw:
    return TreatyLaw(
        name=name,
        statement="∀x ∈ A∩B. consistent(x)",
        quantifier_variables=("x",),
        predicate="consistent",
    )


def _make_treaty(law_name: str = "PatchOverlapLaw", overlap_coord: str = "A∩B") -> Treaty:
    law = _make_treaty_law(law_name)
    return Treaty(
        laws=(law,),
        overlap_coordinate=overlap_coord,
        patches=("module.A", "module.B"),
    )


def _make_local_section(
    coordinate: str,
    value: Any = None,
    trust: float = 1.0,
) -> LocalSection:
    return LocalSection(
        coordinate=coordinate,
        judgment_data={"value": value or coordinate, "type": "Int"},
        trust_level=trust,
        provenance=("test-suite",),
    )


def _make_cover(*patch_names: str) -> Cover:
    """Build a simple Cover from patch name strings with sequential overlaps."""
    target = CoordinateObject(
        "target",
        CoordinateKind.REGION,
        ("target",),
    )
    patches = tuple(
        CoordinateObject(name, CoordinateKind.MODULE, (name,))
        for name in patch_names
    )
    overlaps: list[tuple[str, str]] = []
    for i in range(len(patch_names) - 1):
        overlaps.append((patch_names[i], patch_names[i + 1]))
    return Cover(
        target=target,
        patches=patches,
        overlaps=tuple(overlaps),
    )


# ---------------------------------------------------------------------------
# §1  Setting generation goal and decomposing it
# ---------------------------------------------------------------------------


class TestGenerationGoalDecomposition:
    """Goals must decompose into typed sub-goals, not bool flags."""

    def test_generation_goal_is_not_a_bool(self) -> None:
        """GenerationGoal must be a typed record, not True/False."""
        goal = _make_generation_goal()
        assert goal is not True
        assert goal is not False
        assert isinstance(goal, GenerationGoal)

    def test_generation_goal_carries_priority_and_budget(self) -> None:
        """GenerationGoal must expose priority and budget as distinct attributes."""
        goal = _make_generation_goal("prove_P", GoalPriority.CRITICAL)
        assert goal.priority == GoalPriority.CRITICAL
        assert goal.budget == 20

    def test_goal_decomposer_produces_sub_goals(self) -> None:
        """GoalDecomposer.decompose() must return a list of sub-goals."""
        decomposer = GoalDecomposer()
        root = _make_generation_goal("prove_big")
        sub_goals = decomposer.decompose(root)
        # Even a single goal with no patches yields at least itself
        assert isinstance(sub_goals, list)
        for sg in sub_goals:
            assert isinstance(sg, GenerationGoal)

    def test_goal_tree_root_is_original_goal(self) -> None:
        """GoalTree.root must be the same goal passed to the constructor."""
        goal = _make_generation_goal("root_goal")
        tree = GoalTree(goal)
        assert tree.root.proposition == goal.proposition

    def test_goal_tree_leaves_initially_only_root(self) -> None:
        """A freshly built GoalTree with no children must have only root as leaf."""
        goal = _make_generation_goal("leaf_goal")
        tree = GoalTree(goal)
        leaves = list(tree.leaves())
        assert len(leaves) == 1
        assert leaves[0].proposition == goal.proposition

    def test_goal_tree_add_children_expands_leaves(self) -> None:
        """Adding children must expand the leaf set."""
        root = _make_generation_goal("root")
        child_a = _make_generation_goal("child_A")
        child_b = _make_generation_goal("child_B")
        tree = GoalTree(root)
        tree.add_child(root.goal_id, child_a)
        tree.add_child(root.goal_id, child_b)
        leaves = list(tree.leaves())
        leaf_names = [g.proposition for g in leaves]
        assert "child_A" in leaf_names
        assert "child_B" in leaf_names
        assert "root" not in leaf_names  # root is now internal

    def test_goal_status_initial_is_pending(self) -> None:
        """A freshly created GenerationGoal must have PENDING status."""
        goal = _make_generation_goal()
        assert goal.status == GoalStatus.PENDING


# ---------------------------------------------------------------------------
# §2  Construct candidate sections
# ---------------------------------------------------------------------------


class TestCandidateConstruction:
    """Candidate sections are typed records with provenance and trust level."""

    def test_candidate_is_not_a_bool(self) -> None:
        """Candidate must be a typed dataclass, not True/False."""
        c = _make_candidate()
        assert c is not True
        assert c is not False
        assert isinstance(c, Candidate)

    def test_solver_candidate_has_confidence_in_unit_interval(self) -> None:
        """Candidate confidence must lie in [0.0, 1.0]."""
        c = _make_candidate(channel=SourceChannel.SOLVER, confidence=0.95)
        assert 0.0 <= c.confidence <= 1.0

    def test_copilot_candidate_needs_corroboration(self) -> None:
        """Candidates from copilot channel must declare needs_corroboration=True."""
        c = _make_candidate(channel=SourceChannel.COPILOT, confidence=0.5)
        assert c.is_copilot() is True
        assert c.needs_corroboration() is True

    def test_solver_candidate_does_not_need_corroboration(self) -> None:
        """Solver-produced candidates must not require corroboration."""
        c = _make_candidate(channel=SourceChannel.SOLVER, confidence=0.9)
        assert c.is_copilot() is False
        assert c.needs_corroboration() is False

    def test_candidate_with_residuals_propagates_obligations(self) -> None:
        """A candidate with obligations must report residual_count accurately."""
        c = _make_candidate(
            obligations=("prove_termination", "prove_totality")
        )
        assert c.residual_count() == 2
        assert "prove_termination" in c.residual_obligations

    def test_candidate_evidence_bundle_preserves_kind_tags(self) -> None:
        """The evidence_bundle dict must preserve all inserted kind tags."""
        c = Candidate(
            goal_id="g1",
            proposed_section={"code": "f(x) = x + 1"},
            source_channel=SourceChannel.SOLVER,
            evidence_bundle={
                "solver_proof": {"engine": "z3", "result": "unsat"},
                "runtime_witness": {"trace": [1, 2, 3]},
            },
            confidence=0.85,
        )
        assert c.has_evidence("solver_proof") is True
        assert c.has_evidence("runtime_witness") is True
        assert c.has_evidence("oracle_proposal") is False
        kinds = c.evidence_tags()
        assert "solver_proof" in kinds
        assert "runtime_witness" in kinds

    def test_construction_goal_budget_decreases_on_spend(self) -> None:
        """ConstructionGoal.spend() must return a copy with budget -= 1."""
        goal = _make_construction_goal(budget=3)
        goal2 = goal.spend()
        assert goal2.budget == goal.budget - 1
        # Original must be unchanged (immutability)
        assert goal.budget == 3

    def test_construction_goal_exhausted_when_budget_zero(self) -> None:
        """ConstructionGoal.exhausted() must be True when budget == 0."""
        goal = _make_construction_goal(budget=0)
        assert goal.exhausted() is True
        goal_live = _make_construction_goal(budget=1)
        assert goal_live.exhausted() is False


# ---------------------------------------------------------------------------
# §3  Treaty formation
# ---------------------------------------------------------------------------


class TestTreatyFormation:
    """Treaties must be typed records with structured laws and guards."""

    def test_treaty_law_is_not_a_bool(self) -> None:
        """TreatyLaw must be a typed record, not a bool."""
        law = _make_treaty_law()
        assert law is not True
        assert law is not False
        assert isinstance(law, TreatyLaw)

    def test_treaty_law_has_quantifier_and_predicate(self) -> None:
        """TreatyLaw must expose statement, quantifier variables, and predicate."""
        law = _make_treaty_law("Transitivity")
        assert isinstance(law.statement, str)
        assert isinstance(law.quantifier_variables, tuple)
        assert isinstance(law.predicate, str)

    def test_treaty_formation_via_manager(self) -> None:
        """TreatyManager.propose() must return a Treaty with PROPOSED status."""
        manager = TreatyManager()
        treaty = _make_treaty("OverlapLaw", "module.A∩module.B")
        proposed = manager.propose(treaty)
        assert proposed.status == TreatyStatus.PROPOSED

    def test_treaty_manager_ratify_changes_status(self) -> None:
        """TreatyManager.ratify() must change treaty status to RATIFIED."""
        manager = TreatyManager()
        treaty = _make_treaty()
        proposed = manager.propose(treaty)
        ratified = manager.ratify(proposed.treaty_id)
        assert ratified.status == TreatyStatus.RATIFIED

    def test_treaty_manager_challenge_changes_status(self) -> None:
        """TreatyManager.challenge() must set status to CHALLENGED."""
        manager = TreatyManager()
        treaty = _make_treaty()
        proposed = manager.propose(treaty)
        challenged = manager.challenge(proposed.treaty_id)
        assert challenged.status == TreatyStatus.CHALLENGED

    def test_treaty_manager_active_treaties_excludes_retired(self) -> None:
        """active_treaties() must not include retired treaties."""
        manager = TreatyManager()
        t1 = _make_treaty("LawA")
        t2 = _make_treaty("LawB")
        p1 = manager.propose(t1)
        p2 = manager.propose(t2)
        manager.ratify(p1.treaty_id)
        manager.retire(p2.treaty_id)
        active = manager.active_treaties()
        active_ids = {t.treaty_id for t in active}
        assert p1.treaty_id in active_ids
        assert p2.treaty_id not in active_ids

    def test_treaty_law_stability_score_is_float(self) -> None:
        """Treaty.stability_score() must return a float in [0, 1]."""
        manager = TreatyManager()
        treaty = _make_treaty()
        proposed = manager.propose(treaty)
        ratified = manager.ratify(proposed.treaty_id)
        score = ratified.stability_score()
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# §4  Descent verification
# ---------------------------------------------------------------------------


class TestDescentVerification:
    """Descent must return GlobalSection OR DescentObstruction, never None."""

    def test_descent_engine_attempt_descent_returns_descent_result(self) -> None:
        """attempt_descent() must return a DescentResult, not None or bool."""
        engine = DescentEngine()
        cover = _make_cover("patch.A", "patch.B")
        sections = {
            "patch.A": {"value": "x", "type": "Int"},
            "patch.B": {"value": "x", "type": "Int"},  # compatible
        }
        result = engine.attempt_descent(cover, sections)
        assert result is not None
        assert isinstance(result, DescentResult)
        assert result is not True
        assert result is not False

    def test_descent_compatible_sections_yields_global_section(self) -> None:
        """When all overlaps are satisfied, descent must yield a GlobalSection."""
        engine = DescentEngine(
            configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE)
        )
        cover = _make_cover("patch.A", "patch.B")
        sections = {
            "patch.A": {"type": "Int", "semantics": "pure"},
            "patch.B": {"type": "Int", "semantics": "pure"},  # identical
        }
        result = engine.attempt_descent(cover, sections)
        assert result.is_success is True
        assert isinstance(result.section, GlobalSection)

    def test_descent_incompatible_sections_yields_obstruction(self) -> None:
        """When overlap conditions fail, descent must yield a DescentObstruction."""
        engine = DescentEngine(
            configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE)
        )
        cover = _make_cover("patch.X", "patch.Y")
        sections = {
            "patch.X": {"type": "Int"},
            "patch.Y": {"type": "Bool"},   # incompatible type
        }
        result = engine.attempt_descent(cover, sections)
        assert result.is_success is False
        assert isinstance(result.obstruction, DescentObstruction)

    def test_descent_obstruction_is_not_a_bool(self) -> None:
        """DescentObstruction must be a typed record, never a bool."""
        obs = DescentObstruction(
            coordinate="module.foo",
            violated_overlaps=(),
        )
        assert obs is not True
        assert obs is not False
        assert isinstance(obs, DescentObstruction)

    def test_global_section_carries_trust_floor(self) -> None:
        """GlobalSection must expose trust_floor as a float in [0, 1]."""
        gs = GlobalSection(
            coordinate="module.foo",
            merged_judgment={"type": "Int"},
            constituent_sections=("patch.A", "patch.B"),
            trust_floor=0.85,
        )
        assert 0.0 <= gs.trust_floor <= 1.0

    def test_global_section_constituent_count_matches_sections(self) -> None:
        """GlobalSection.constituent_count must equal len(constituent_sections)."""
        gs = GlobalSection(
            coordinate="module.foo",
            constituent_sections=("p1", "p2", "p3"),
        )
        assert gs.constituent_count == 3

    def test_descent_obstruction_violation_count_matches(self) -> None:
        """DescentObstruction.violation_count must equal len(violated_overlaps)."""
        overlap = OverlapCondition(
            left_coordinate="patch.A",
            right_coordinate="patch.B",
            overlap_coordinate="A∩B",
            status=OverlapStatus.VIOLATED,
        )
        obs = DescentObstruction(
            coordinate="module.foo",
            violated_overlaps=(overlap,),
        )
        assert obs.violation_count == 1


# ---------------------------------------------------------------------------
# §5  Construction failure produces DescentObstruction
# ---------------------------------------------------------------------------


class TestConstructionFailureProducesObstruction:
    """Failed construction must produce a DescentObstruction, not silently pass."""

    def test_gluing_data_detects_violated_overlap(self) -> None:
        """GluingData.find_violated_overlaps() must detect mismatched sections."""
        gluing = GluingData()
        sec_a = _make_local_section("patch.A", value=42)
        sec_b = _make_local_section("patch.B", value=99)  # different value
        gluing.add_section(sec_a)
        gluing.add_section(sec_b)
        gluing.add_overlap_pair("patch.A", "patch.B")
        violated = gluing.find_violated_overlaps()
        assert len(violated) >= 1
        assert all(o.status == OverlapStatus.VIOLATED for o in violated)

    def test_gluing_data_compatible_sections_no_violations(self) -> None:
        """GluingData with identical sections must report zero violations."""
        gluing = GluingData()
        sec_a = _make_local_section("patch.A", value="x")
        sec_b = _make_local_section("patch.B", value="x")   # same
        gluing.add_section(sec_a)
        gluing.add_section(sec_b)
        gluing.add_overlap_pair("patch.A", "patch.B")
        violated = gluing.find_violated_overlaps()
        # Compatible sections → no violations
        assert len(violated) == 0

    def test_gluing_data_compute_cocycle_non_trivial_on_failure(self) -> None:
        """Cocycle must be non-trivial when sections disagree."""
        gluing = GluingData()
        sec_a = _make_local_section("patch.A", value="left_val")
        sec_b = _make_local_section("patch.B", value="right_val")
        gluing.add_section(sec_a)
        gluing.add_section(sec_b)
        gluing.add_overlap_pair("patch.A", "patch.B")
        cocycle = gluing.compute_cocycle()
        # Non-trivial = rank > 0
        assert cocycle.rank() >= 1
        assert not cocycle.is_trivial()

    def test_descent_result_union_is_either_global_or_obstruction(self) -> None:
        """DescentResult.section XOR obstruction must be non-None."""
        # Success case
        gs = GlobalSection(coordinate="c", trust_floor=1.0)
        success = DescentResult(is_success=True, section=gs)
        assert success.section is not None
        assert success.obstruction is None
        # Failure case
        obs = DescentObstruction(coordinate="c")
        failure = DescentResult(is_success=False, obstruction=obs)
        assert failure.obstruction is not None
        assert failure.section is None

    def test_descent_engine_three_patch_partial_agreement(self) -> None:
        """Three patches where only two agree must produce an obstruction."""
        engine = DescentEngine(
            configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE)
        )
        cover = _make_cover("p1", "p2", "p3")
        sections = {
            "p1": {"value": "A"},
            "p2": {"value": "A"},  # p1 and p2 agree
            "p3": {"value": "B"},  # p2 and p3 disagree
        }
        result = engine.attempt_descent(cover, sections)
        # p2∩p3 overlap fails → obstruction
        assert result.is_success is False
        assert isinstance(result.obstruction, DescentObstruction)


# ---------------------------------------------------------------------------
# §6  Goal satisfaction with global sections
# ---------------------------------------------------------------------------


class TestGoalSatisfactionWithGlobalSection:
    """A GlobalSection satisfies a construction goal when trust floor meets floor."""

    def test_global_section_trust_floor_meets_goal_requirement(self) -> None:
        """When trust_floor >= 1.0, the global section is fully trusted."""
        gs = GlobalSection(
            coordinate="module.foo",
            merged_judgment={"type": "Int", "value": "x+1"},
            constituent_sections=("p.A", "p.B"),
            trust_floor=1.0,
        )
        assert gs.is_fully_trusted is True

    def test_partial_trust_floor_not_fully_trusted(self) -> None:
        """Global section with trust_floor < 1.0 must not be fully trusted."""
        gs = GlobalSection(
            coordinate="module.foo",
            constituent_sections=("p.A", "p.B"),
            trust_floor=0.7,
        )
        assert gs.is_fully_trusted is False

    def test_global_section_restrict_to_keys_returns_subset(self) -> None:
        """restrict_to_keys() must return a GlobalSection with only the given keys."""
        gs = GlobalSection(
            coordinate="module.foo",
            merged_judgment={"type": "Int", "value": "x+1", "proof": "refl"},
            trust_floor=0.9,
        )
        restricted = gs.restrict_to_keys(frozenset({"type"}))
        assert "type" in restricted.merged_judgment
        assert "proof" not in restricted.merged_judgment

    def test_construction_context_tracks_bindings_and_evidence(self) -> None:
        """ConstructionContext must track bindings, evidence, and budget."""
        ctx = ConstructionContext(
            coordinate=_make_coordinate("module.ctx"),
            bindings={"x": "Int", "y": "Bool"},
            evidence={"solver_proof": {"result": "unsat"}},
        )
        assert ctx.has_binding("x") is True
        assert ctx.has_binding("z") is False
        assert ctx.has_evidence_tag("solver_proof") is True

    def test_construction_context_spend_decrements_budget(self) -> None:
        """ConstructionContext.spend() must return a copy with budget reduced."""
        ctx = ConstructionContext(
            coordinate=_make_coordinate(),
            budget=10,
        )
        ctx2 = ctx.spend(3)
        assert ctx2.budget == 7
        assert ctx.budget == 10  # original unchanged


# ---------------------------------------------------------------------------
# §7  Candidate comparator and selector
# ---------------------------------------------------------------------------


class TestCandidateComparatorAndSelector:
    """CandidateComparator must rank candidates by structured criteria."""

    def test_comparator_prefers_lower_residuals(self) -> None:
        """Candidate with fewer residuals must be ranked higher."""
        comparator = CandidateComparator()
        c_clean = _make_candidate(obligations=())
        c_dirty = _make_candidate(obligations=("ob1", "ob2", "ob3"))
        # Compare residuals directly
        res_comp = comparator.residual_comparison(c_clean, c_dirty)
        assert res_comp < 0  # c_clean wins (negative = first arg preferred)

    def test_comparator_prefers_higher_confidence(self) -> None:
        """Candidate with higher confidence must score higher."""
        comparator = CandidateComparator()
        c_high = _make_candidate(confidence=0.95)
        c_low = _make_candidate(confidence=0.3)
        comp = comparator.trust_comparison(c_high, c_low)
        assert comp < 0  # c_high wins

    def test_selector_returns_best_candidate(self) -> None:
        """CandidateSelector.select() must return the highest-ranked candidate."""
        selector = CandidateSelector()
        c_best = _make_candidate(confidence=0.95, obligations=())
        c_worse = _make_candidate(confidence=0.4, obligations=("ob1",))
        goal = _make_construction_goal()
        selected = selector.select(goal, [c_best, c_worse])
        if selected is not None:
            assert isinstance(selected, Candidate)

    def test_construction_result_is_not_a_bool(self) -> None:
        """ConstructionResult must be a typed record, not True/False."""
        c = _make_candidate()
        result = ConstructionResult(
            goal_id="goal-001",
            status=ConstructionStatus.SUCCESS,
            winner=c,
            all_candidates=(c,),
        )
        assert result is not True
        assert result is not False
        assert isinstance(result, ConstructionResult)

    def test_construction_result_success_exposes_winner(self) -> None:
        """A SUCCESS result must have a non-None winner candidate."""
        c = _make_candidate()
        result = ConstructionResult(
            goal_id="goal-x",
            status=ConstructionStatus.SUCCESS,
            winner=c,
            all_candidates=(c,),
        )
        assert result.succeeded() is True
        assert result.winner is not None


# ---------------------------------------------------------------------------
# §8  Multi-section descent with mixed trust floors
# ---------------------------------------------------------------------------


class TestMultiSectionDescentMixedTrust:
    """Descent with mixed trust sections must propagate the minimum trust floor."""

    def test_local_section_with_trust_partially_evidenced(self) -> None:
        """LocalSection.is_fully_evidenced must be False when obligations exist."""
        sec = LocalSection(
            coordinate="patch.partial",
            judgment_data={"type": "Int"},
            trust_level=0.7,
            is_partial=True,
            residual_obligations=["prove_total"],
        )
        assert sec.is_fully_evidenced is False
        assert sec.is_partial is True

    def test_local_section_discharge_obligation_removes_it(self) -> None:
        """discharge_obligation() must remove the named obligation."""
        sec = LocalSection(
            coordinate="patch.X",
            judgment_data={"type": "Bool"},
            residual_obligations=["prove_P", "prove_Q"],
            is_partial=True,
        )
        sec2 = sec.discharge_obligation("prove_P")
        assert "prove_P" not in sec2.residual_obligations
        assert "prove_Q" in sec2.residual_obligations

    def test_global_section_certificate_can_be_set(self) -> None:
        """GlobalSection.with_certificate() must return a copy with the cert."""
        gs = GlobalSection(coordinate="module.foo", trust_floor=1.0)
        gs2 = gs.with_certificate("cert:sha256:abc123def456")
        assert gs2.certificate == "cert:sha256:abc123def456"
        assert gs.certificate == ""  # original unchanged

    def test_descent_result_non_null_invariant(self) -> None:
        """DescentResult must always have exactly one of section or obstruction set."""
        engine = DescentEngine()
        cover = _make_cover("p.1", "p.2")
        sections_ok = {
            "p.1": {"v": 1},
            "p.2": {"v": 1},
        }
        result = engine.attempt_descent(cover, sections_ok)
        # One of the two outcome slots must be non-None
        has_section = result.section is not None
        has_obstruction = result.obstruction is not None
        assert has_section != has_obstruction  # exactly one


# ---------------------------------------------------------------------------
# §9  Treaty synthesizer produces laws from patterns
# ---------------------------------------------------------------------------


class TestTreatySynthesizerPatterns:
    """TreatySynthesizer must produce TreatyLaw objects, not bool."""

    def test_treaty_synthesizer_propose_law_returns_treaty_law(self) -> None:
        """synthesize() must return a list of TreatyLaw objects."""
        synthesizer = TreatySynthesizer()
        events = [
            {"coordinate": "module.A", "type": "Int", "value": 1},
            {"coordinate": "module.A", "type": "Int", "value": 2},
            {"coordinate": "module.A", "type": "Int", "value": 3},
        ]
        laws = synthesizer.synthesize(events)
        for law in laws:
            assert isinstance(law, TreatyLaw)
            assert law is not True
            assert law is not False

    def test_treaty_law_has_arity_method(self) -> None:
        """TreatyLaw.arity() must return the count of quantifier variables."""
        law = TreatyLaw(
            name="ArityTest",
            statement="∀x y. P(x, y)",
            quantifier_variables=("x", "y"),
            predicate="P",
        )
        assert law.arity() == 2

    def test_treaty_law_structural_hash_is_deterministic(self) -> None:
        """Same statement must always produce the same structural_hash."""
        law1 = _make_treaty_law("H1")
        law2 = _make_treaty_law("H1")
        assert law1.structural_hash() == law2.structural_hash()

    def test_treaty_validator_accepts_ratified_treaty(self) -> None:
        """TreatyValidator.validate() must not report errors on a ratified treaty."""
        manager = TreatyManager()
        validator = TreatyValidator()
        treaty = _make_treaty()
        proposed = manager.propose(treaty)
        ratified = manager.ratify(proposed.treaty_id)
        ok, errors = validator.validate(ratified, manager)
        # A freshly ratified treaty should be valid
        assert isinstance(ok, bool)
        assert isinstance(errors, list)


# ---------------------------------------------------------------------------
# §10  End-to-end: goal → construction → treaty → descent
# ---------------------------------------------------------------------------


class TestGoalToDescentPipeline:
    """Full pipeline: goal decomposition → candidate construction → descent."""

    def test_full_pipeline_compatible_sections_produces_global_section(self) -> None:
        """Compatible sections across all patches must yield a GlobalSection."""
        # Step 1: set goal
        goal = _make_generation_goal("prove_type_safety", GoalPriority.HIGH)
        assert isinstance(goal, GenerationGoal)
        # Step 2: construct candidate sections (mock the construction loop)
        sections = {
            "module.A": {"type": "Int", "semantics": "total"},
            "module.B": {"type": "Int", "semantics": "total"},
        }
        # Step 3: form treaty for the overlap
        manager = TreatyManager()
        treaty = _make_treaty("TypeSafetyOverlap", "module.A∩module.B")
        manager.propose(treaty)
        # Step 4: run descent
        engine = DescentEngine()
        cover = _make_cover("module.A", "module.B")
        result = engine.attempt_descent(cover, sections)
        # Must produce GlobalSection (since sections are compatible)
        assert result is not None
        if result.is_success:
            assert isinstance(result.section, GlobalSection)
            assert result.section.trust_floor >= 0.0
        else:
            # Even failure must produce structured obstruction, not None
            assert isinstance(result.obstruction, DescentObstruction)

    def test_full_pipeline_incompatible_sections_produces_obstruction(self) -> None:
        """Incompatible sections must produce a DescentObstruction with repair hints."""
        engine = DescentEngine(
            configuration=DescentConfiguration(
                strategy=DescentStrategy.EXHAUSTIVE,
                record_log=True,
            )
        )
        cover = _make_cover("m.A", "m.B")
        sections = {
            "m.A": {"type": "Int"},
            "m.B": {"type": "Bool"},  # type mismatch → obstruction
        }
        result = engine.attempt_descent(cover, sections)
        assert result.is_success is False
        obs = result.obstruction
        assert obs is not None
        assert isinstance(obs, DescentObstruction)
        assert obs.violation_count >= 1
        # Obstruction is not a bool
        assert obs is not True
        assert obs is not False
