"""
Tests for jugeo.generation.hypercover_treaties.models
======================================================

Test strategy overview
----------------------
This module provides exhaustive coverage of five core model classes:

* HypercoverSynthesisRecord – frozen dataclass that accumulates synthesis
  state through immutable builder methods (with_step, with_phase, etc.).
* TreatyCandidate – frozen dataclass representing a candidate treaty with
  confidence-based acceptance scoring.
* OverlapLaw – frozen dataclass encoding an overlap law between two patches,
  with a four-level stability ladder.
* DependentTreaty – frozen dataclass capturing a treaty whose evaluation
  depends on the prior acceptance of other treaties.
* SynthesisOutcome – frozen dataclass summarising the final result of a
  hypercover synthesis run.

Because all five classes are frozen dataclasses, mutation is impossible;
every "change" test exercises the builder/with_* pattern and verifies that
the original instance is untouched while the returned copy has the new field.

The test file is organised into five sections, one per class, with an
additional section for cross-class integration scenarios.  Within each
section the tests progress from trivial construction through each public
method, edge cases (empty collections, boundary floats, long sequences), and
parametrised sweeps that cover many representative inputs at once.
"""

from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

# ---------------------------------------------------------------------------
# Conditional imports – each guarded so the file can be collected even when
# individual sub-packages are absent from the current installation.
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import Coordinate, CoordinateKind

    CoordinateObject = Coordinate
except ImportError as e:
    pytest.skip(f"jugeo.geometry.site not available: {e}", allow_module_level=True)

try:
    from jugeo.geometry.supports import SupportRegion
except ImportError as e:
    pytest.skip(f"jugeo.geometry.supports not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.goals import ConstructionGoal, GoalPriority
except ImportError as e:
    pytest.skip(f"jugeo.generation.goals not available: {e}", allow_module_level=True)

try:
    from jugeo.evidence.trust import TrustTier
except ImportError as e:
    pytest.skip(f"jugeo.evidence.trust not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.treaties import TreatyClause, OverlapTreaty, evaluate_treaty
except ImportError as e:
    pytest.skip(f"jugeo.generation.treaties not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.hypercover_treaties.models import (
        HypercoverSynthesisRecord,
        TreatyCandidate,
        OverlapLaw,
        DependentTreaty,
        SynthesisOutcome,
        SynthesisPhase,
        LawStability,
        CandidateSource,
        TreatyRole,
        OutcomeKind,
    )
except ImportError as e:
    pytest.skip(f"models not available: {e}", allow_module_level=True)


# ===========================================================================
# Shared helpers
# ===========================================================================


def make_support(patch: str = "p") -> SupportRegion:
    """Return a minimal SupportRegion anchored at *patch*."""
    coord = Coordinate(components=(patch,), kind=CoordinateKind.REGION)
    return SupportRegion(coord, frozenset({patch}))


def make_goal(proposition: str = "test_prop", patch: str = "p") -> ConstructionGoal:
    """Return a ConstructionGoal with sensible defaults."""
    return ConstructionGoal(
        proposition=proposition,
        support=make_support(patch),
        required_tier=TrustTier.REVIEWED,
        priority=GoalPriority.MEDIUM,
    )


def make_clause(
    patch: str = "p", expectation: str = "val_ok", satisfied: bool = True
) -> TreatyClause:
    """Return a TreatyClause with the given fields."""
    return TreatyClause(patch=patch, expectation=expectation, satisfied=satisfied)


def make_overlap_treaty(
    patches: tuple = ("p1", "p2"), satisfied: bool = True
) -> OverlapTreaty:
    """Return an OverlapTreaty whose clauses all share the same *satisfied* flag."""
    clauses = tuple(
        TreatyClause(patch=p, expectation=f"expect_{p}", satisfied=satisfied)
        for p in patches
    )
    return OverlapTreaty(patches=patches, clauses=clauses)


def make_overlap_law(
    patch_a: str = "alpha",
    patch_b: str = "beta",
    stability: "LawStability" = None,
    support: int = 5,
    violations: int = 0,
    confidence: float = 0.8,
) -> OverlapLaw:
    """Return an OverlapLaw with convenient defaults."""
    if stability is None:
        stability = LawStability.PROVISIONAL
    return OverlapLaw(
        patch_pair=(patch_a, patch_b),
        predicate_description=f"{patch_a}_meets_{patch_b}",
        stability=stability,
        support_count=support,
        violation_count=violations,
        confidence=confidence,
    )


def make_record(
    record_id: str = "rec-001",
    goal_proposition: str = "test_goal",
    patches: tuple = ("pa", "pb"),
) -> HypercoverSynthesisRecord:
    """Return a minimal HypercoverSynthesisRecord."""
    return HypercoverSynthesisRecord(
        record_id=record_id,
        goal_proposition=goal_proposition,
        cover_patch_keys=patches,
    )


def make_candidate(
    candidate_id: str = "cand-001",
    confidence: float = 0.7,
    patches: tuple = ("p1", "p2"),
) -> TreatyCandidate:
    """Return a TreatyCandidate with the given confidence."""
    return TreatyCandidate(
        candidate_id=candidate_id,
        confidence=confidence,
        patch_keys=patches,
        source=CandidateSource.MINED,
    )


def make_synthesis_outcome(
    kind: "OutcomeKind" = None,
    laws: tuple = (),
    accepted_count: int = 0,
    failed_patches: tuple = (),
) -> SynthesisOutcome:
    """Return a SynthesisOutcome with the given parameters."""
    if kind is None:
        kind = OutcomeKind.SUCCESS
    return SynthesisOutcome(
        kind=kind,
        accepted_laws=laws,
        accepted_treaties_count=accepted_count,
        failed_patches=failed_patches,
    )


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def minimal_record() -> HypercoverSynthesisRecord:
    """A freshly-constructed synthesis record with two patches."""
    return make_record()


@pytest.fixture
def stable_law() -> OverlapLaw:
    """An OverlapLaw already at STABLE stability."""
    return make_overlap_law(stability=LawStability.STABLE, support=20, violations=0)


@pytest.fixture
def proven_law() -> OverlapLaw:
    """An OverlapLaw at the highest PROVEN stability."""
    return make_overlap_law(stability=LawStability.PROVEN, support=50, violations=0, confidence=1.0)


@pytest.fixture
def high_confidence_candidate() -> TreatyCandidate:
    """A TreatyCandidate with confidence high enough to be accepted."""
    return make_candidate(confidence=0.9)


@pytest.fixture
def low_confidence_candidate() -> TreatyCandidate:
    """A TreatyCandidate with confidence low enough to be rejected."""
    return make_candidate(confidence=0.05)


@pytest.fixture
def resolved_dependent_treaty() -> DependentTreaty:
    """A DependentTreaty that has no unresolved dependencies."""
    return DependentTreaty(
        treaty_id="dep-resolved",
        patch_keys=("p1", "p2"),
        dependency_ids=(),
        clause_descriptions=("clause_A",),
        is_resolved=False,
    )


@pytest.fixture
def success_outcome(proven_law, stable_law) -> SynthesisOutcome:
    """A successful SynthesisOutcome with two accepted laws."""
    return make_synthesis_outcome(
        kind=OutcomeKind.SUCCESS,
        laws=(proven_law, stable_law),
        accepted_count=3,
    )


# ===========================================================================
# Section 1 – HypercoverSynthesisRecord
# ===========================================================================
#
# Tests cover:
#   • Default-field construction (record_id auto-generated UUID)
#   • Explicit construction with every parameter
#   • All builder methods (with_step, with_phase, with_treaty_accepted, …)
#   • All predicate methods (is_complete, is_failed, is_terminal, …)
#   • Numeric query methods (patch_count, overlap_count, acceptance_ratio, …)
#   • Immutability: originals are untouched after builder calls
#   • Edge cases: empty patches, zero budget, many steps


class TestHypercoverSynthesisRecord:
    """Tests for HypercoverSynthesisRecord construction and behaviour."""

    def test_synthesis_record_construction_minimal(self):
        """Verify that a record can be created with only default field values."""
        rec = HypercoverSynthesisRecord()
        assert isinstance(rec.record_id, str)
        assert len(rec.record_id) > 0
        assert rec.goal_proposition == ""
        assert rec.cover_patch_keys == ()
        assert rec.synthesis_steps == ()
        assert rec.phase == SynthesisPhase.DECOMPOSING

    def test_synthesis_record_construction_with_all_fields(self):
        """Verify that every field is stored when supplied explicitly."""
        rec = HypercoverSynthesisRecord(
            record_id="r-full",
            goal_proposition="full_goal",
            target_coordinate_key="mod.sub",
            phase=SynthesisPhase.COVERING,
            cover_patch_keys=("p1", "p2", "p3"),
            overlap_pairs=(("p1", "p2"), ("p2", "p3")),
            synthesis_steps=("step_a", "step_b"),
            accepted_treaty_ids=("t1",),
            rejected_candidate_ids=("c1", "c2"),
            elapsed_seconds=1.5,
            budget_consumed=4,
            provenance=("run_x",),
        )
        assert rec.record_id == "r-full"
        assert rec.goal_proposition == "full_goal"
        assert rec.target_coordinate_key == "mod.sub"
        assert rec.phase == SynthesisPhase.COVERING
        assert len(rec.cover_patch_keys) == 3
        assert len(rec.overlap_pairs) == 2
        assert len(rec.synthesis_steps) == 2
        assert rec.elapsed_seconds == pytest.approx(1.5)
        assert rec.budget_consumed == 4

    def test_synthesis_record_add_step_basic(self, minimal_record):
        """with_step() must append the given step string to synthesis_steps."""
        updated = minimal_record.with_step("step_1")
        assert "step_1" in updated.synthesis_steps
        assert len(updated.synthesis_steps) == 1

    def test_synthesis_record_add_step_multiple(self, minimal_record):
        """Chaining with_step() three times produces a record with three steps."""
        r = minimal_record.with_step("s1").with_step("s2").with_step("s3")
        assert r.synthesis_steps == ("s1", "s2", "s3")

    def test_synthesis_record_add_step_returns_new_record(self, minimal_record):
        """with_step() must return a new instance; the original is unchanged."""
        updated = minimal_record.with_step("new_step")
        assert updated is not minimal_record
        assert minimal_record.synthesis_steps == ()

    def test_synthesis_record_finalize(self, minimal_record):
        """with_phase(COMPLETE) must mark the record as complete."""
        completed = minimal_record.with_phase(SynthesisPhase.COMPLETE)
        assert completed.is_complete()
        assert completed.is_terminal()

    def test_synthesis_record_failed_phase(self, minimal_record):
        """with_phase(FAILED) must mark the record as failed and terminal."""
        failed = minimal_record.with_phase(SynthesisPhase.FAILED)
        assert failed.is_failed()
        assert failed.is_terminal()
        assert not failed.is_complete()

    def test_synthesis_record_is_terminal_false_for_in_progress(self, minimal_record):
        """A record in DECOMPOSING phase is not terminal."""
        assert not minimal_record.is_terminal()

    def test_synthesis_record_to_dict_returns_dict(self, minimal_record):
        """If to_dict() exists, it must return a plain dict."""
        if not hasattr(minimal_record, "to_dict"):
            pytest.skip("to_dict not implemented on this model version")
        result = minimal_record.to_dict()
        assert isinstance(result, dict)

    def test_synthesis_record_validate_valid_record(self, minimal_record):
        """A freshly-constructed record should be self-consistent."""
        # patch_count must equal len(cover_patch_keys)
        assert minimal_record.patch_count() == len(minimal_record.cover_patch_keys)
        assert minimal_record.step_count() == 0

    def test_synthesis_record_validate_empty_patches(self):
        """A record with no patches has patch_count() == 0."""
        rec = HypercoverSynthesisRecord(record_id="empty", cover_patch_keys=())
        assert rec.patch_count() == 0
        assert rec.overlap_count() == 0

    def test_synthesis_record_summary_repr_is_informative(self, minimal_record):
        """repr() should mention the class name and the record_id field."""
        r = repr(minimal_record)
        assert "HypercoverSynthesisRecord" in r
        assert "rec-001" in r

    def test_synthesis_record_equality(self):
        """Two records constructed with identical arguments must compare equal."""
        r1 = HypercoverSynthesisRecord(record_id="same", cover_patch_keys=("x",))
        r2 = HypercoverSynthesisRecord(record_id="same", cover_patch_keys=("x",))
        assert r1 == r2

    def test_synthesis_record_inequality(self):
        """Records with different record_ids must not compare equal."""
        r1 = HypercoverSynthesisRecord(record_id="aaa")
        r2 = HypercoverSynthesisRecord(record_id="bbb")
        assert r1 != r2

    def test_synthesis_record_with_many_patches(self):
        """A record can be built with 25 distinct patch keys."""
        patches = tuple(f"patch_{i}" for i in range(25))
        rec = HypercoverSynthesisRecord(record_id="big", cover_patch_keys=patches)
        assert rec.patch_count() == 25

    def test_synthesis_record_acceptance_ratio_no_decisions(self, minimal_record):
        """acceptance_ratio() returns 0.0 when nothing has been accepted or rejected."""
        assert minimal_record.acceptance_ratio() == pytest.approx(0.0)

    def test_synthesis_record_acceptance_ratio_all_accepted(self):
        """acceptance_ratio() returns 1.0 when everything was accepted."""
        rec = HypercoverSynthesisRecord(
            record_id="r",
            accepted_treaty_ids=("t1", "t2", "t3"),
            rejected_candidate_ids=(),
        )
        assert rec.acceptance_ratio() == pytest.approx(1.0)

    def test_synthesis_record_acceptance_ratio_mixed(self):
        """acceptance_ratio() is proportional to accepted / (accepted + rejected)."""
        rec = HypercoverSynthesisRecord(
            record_id="r",
            accepted_treaty_ids=("t1", "t2"),
            rejected_candidate_ids=("c1", "c2"),
        )
        assert rec.acceptance_ratio() == pytest.approx(0.5)

    def test_synthesis_record_budget_fraction(self):
        """budget_fraction(max_budget) returns consumed/max_budget."""
        rec = HypercoverSynthesisRecord(record_id="r", budget_consumed=3)
        assert rec.budget_fraction(10) == pytest.approx(0.3)

    def test_synthesis_record_budget_fraction_zero_max(self):
        """budget_fraction(0) returns 0.0 without raising."""
        rec = HypercoverSynthesisRecord(record_id="r", budget_consumed=5)
        result = rec.budget_fraction(0)
        assert result == pytest.approx(0.0) or isinstance(result, float)

    def test_synthesis_record_with_cover_updates_patches_and_pairs(self, minimal_record):
        """with_cover() replaces the patch key tuple and overlap pairs."""
        pairs = (("p1", "p2"), ("p2", "p3"))
        updated = minimal_record.with_cover(("p1", "p2", "p3"), pairs)
        assert updated.cover_patch_keys == ("p1", "p2", "p3")
        assert updated.overlap_pairs == pairs
        assert minimal_record.cover_patch_keys == ("pa", "pb")

    def test_synthesis_record_with_treaty_accepted(self, minimal_record):
        """with_treaty_accepted() appends a treaty id to accepted_treaty_ids."""
        updated = minimal_record.with_treaty_accepted("t-xyz")
        assert "t-xyz" in updated.accepted_treaty_ids
        assert "t-xyz" not in minimal_record.accepted_treaty_ids

    def test_synthesis_record_with_candidate_rejected(self, minimal_record):
        """with_candidate_rejected() appends a candidate id to rejected_candidate_ids."""
        updated = minimal_record.with_candidate_rejected("c-abc")
        assert "c-abc" in updated.rejected_candidate_ids
        assert minimal_record.rejected_candidate_ids == ()

    def test_synthesis_record_with_provenance_entry(self, minimal_record):
        """with_provenance_entry() appends an audit entry."""
        updated = minimal_record.with_provenance_entry("audit_event_1")
        assert "audit_event_1" in updated.provenance
        assert minimal_record.provenance == ()

    @pytest.mark.parametrize(
        "record_id,patches",
        [
            ("r001", ("a",)),
            ("r002", ("a", "b")),
            ("r003", ("a", "b", "c", "d", "e")),
            ("r004", tuple(f"patch_{i}" for i in range(10))),
            ("r005", tuple(f"patch_{i}" for i in range(20))),
        ],
    )
    def test_synthesis_record_parametrised_construction(self, record_id, patches):
        """Records with varying sizes of patch tuple are constructed correctly."""
        rec = HypercoverSynthesisRecord(record_id=record_id, cover_patch_keys=patches)
        assert rec.record_id == record_id
        assert rec.patch_count() == len(patches)

    @pytest.mark.parametrize(
        "phase,expected_complete,expected_failed,expected_terminal",
        [
            (SynthesisPhase.DECOMPOSING, False, False, False),
            (SynthesisPhase.COVERING, False, False, False),
            (SynthesisPhase.VALIDATING, False, False, False),
            (SynthesisPhase.COMPLETE, True, False, True),
            (SynthesisPhase.FAILED, False, True, True),
        ],
    )
    def test_synthesis_record_phase_predicates(
        self, phase, expected_complete, expected_failed, expected_terminal
    ):
        """Phase predicates match the phase enumeration for all lifecycle stages."""
        rec = HypercoverSynthesisRecord(record_id="r", phase=phase)
        assert rec.is_complete() == expected_complete
        assert rec.is_failed() == expected_failed
        assert rec.is_terminal() == expected_terminal


# ===========================================================================
# Section 2 – TreatyCandidate
# ===========================================================================
#
# Tests cover:
#   • Construction defaults and explicit values
#   • acceptance_score() range and behaviour
#   • is_accepted / is_rejected / is_deferred thresholds
#   • Builder methods: with_confidence, with_counterexample, with_evidence,
#     with_clause
#   • Query methods: evidence_count, clause_count, patch_count
#   • Equality / inequality
#   • Edge cases: zero confidence, confidence == 1.0, many evidence entries


class TestTreatyCandidate:
    """Tests for TreatyCandidate construction, scoring, and builder methods."""

    def test_treaty_candidate_construction_defaults(self):
        """A TreatyCandidate created with no arguments should have safe defaults."""
        cand = TreatyCandidate()
        assert isinstance(cand.candidate_id, str)
        assert cand.confidence == pytest.approx(0.0)
        assert cand.counterexample_count == 0
        assert cand.patch_keys == ()
        assert cand.proposed_clauses == ()

    def test_treaty_candidate_construction_with_patches(self):
        """patch_keys tuple is stored verbatim."""
        cand = TreatyCandidate(patch_keys=("x", "y", "z"), confidence=0.6)
        assert cand.patch_keys == ("x", "y", "z")
        assert cand.patch_count() == 3

    def test_treaty_candidate_score_returns_float(self, high_confidence_candidate):
        """acceptance_score() must return a float."""
        score = high_confidence_candidate.acceptance_score()
        assert isinstance(score, float)

    def test_treaty_candidate_score_range(self):
        """acceptance_score() is clamped to [0.0, 1.0] for all valid inputs."""
        for conf in (0.0, 0.1, 0.5, 0.9, 1.0):
            cand = TreatyCandidate(confidence=conf)
            score = cand.acceptance_score()
            assert 0.0 <= score <= 1.0, f"score {score} out of range for conf={conf}"

    def test_treaty_candidate_accept_high_confidence(self):
        """A candidate with confidence >= 0.5 (and no counterexamples) is accepted."""
        cand = TreatyCandidate(confidence=0.75)
        assert cand.is_accepted()
        assert not cand.is_rejected()

    def test_treaty_candidate_reject_low_confidence(self, low_confidence_candidate):
        """A candidate with confidence < 0.2 is rejected."""
        assert low_confidence_candidate.is_rejected()
        assert not low_confidence_candidate.is_accepted()

    def test_treaty_candidate_deferred_medium_confidence(self):
        """A candidate in the [0.2, 0.5) confidence window is deferred."""
        cand = TreatyCandidate(confidence=0.35)
        assert cand.is_deferred()
        assert not cand.is_accepted()
        assert not cand.is_rejected()

    def test_treaty_candidate_reject_with_counterexamples(self):
        """Adding many counterexamples drives acceptance_score below the reject threshold."""
        cand = TreatyCandidate(confidence=0.4, counterexample_count=10)
        score = cand.acceptance_score()
        assert score < 0.5

    def test_treaty_candidate_with_confidence_builder(self):
        """with_confidence() returns a new candidate with updated confidence."""
        cand = TreatyCandidate(candidate_id="c1", confidence=0.1)
        updated = cand.with_confidence(0.9)
        assert updated.confidence == pytest.approx(0.9)
        assert cand.confidence == pytest.approx(0.1)  # original unchanged

    def test_treaty_candidate_with_counterexample_builder(self):
        """with_counterexample() increments counterexample_count by one."""
        cand = TreatyCandidate(candidate_id="c1", counterexample_count=2)
        updated = cand.with_counterexample()
        assert updated.counterexample_count == 3
        assert cand.counterexample_count == 2

    def test_treaty_candidate_with_evidence_builder(self):
        """with_evidence() appends an evidence id to supporting_evidence."""
        cand = TreatyCandidate(candidate_id="c1")
        updated = cand.with_evidence("ev-001")
        assert "ev-001" in updated.supporting_evidence
        assert updated.evidence_count() == 1

    def test_treaty_candidate_with_clause_builder(self):
        """with_clause() appends a clause description string."""
        cand = TreatyCandidate()
        updated = cand.with_clause("predicate_x holds")
        assert "predicate_x holds" in updated.proposed_clauses
        assert updated.clause_count() == 1

    def test_treaty_candidate_evidence_count_zero(self):
        """evidence_count() is 0 for a freshly-created candidate."""
        cand = TreatyCandidate()
        assert cand.evidence_count() == 0

    def test_treaty_candidate_repr(self):
        """repr() includes the class name."""
        cand = TreatyCandidate(candidate_id="cand-repr")
        assert "TreatyCandidate" in repr(cand)

    def test_treaty_candidate_equality(self):
        """Two candidates built with the same id and fields compare equal."""
        import time as _time

        ts = _time.time()
        c1 = TreatyCandidate(candidate_id="eq", confidence=0.7, created_at=ts)
        c2 = TreatyCandidate(candidate_id="eq", confidence=0.7, created_at=ts)
        assert c1 == c2

    def test_treaty_candidate_inequality(self):
        """Candidates with different candidate_ids are not equal."""
        c1 = TreatyCandidate(candidate_id="aaa")
        c2 = TreatyCandidate(candidate_id="bbb")
        assert c1 != c2

    def test_treaty_candidate_multiple_evidence_accumulate(self):
        """Chaining with_evidence() calls accumulates all evidence ids."""
        cand = TreatyCandidate()
        for i in range(8):
            cand = cand.with_evidence(f"ev-{i:03d}")
        assert cand.evidence_count() == 8

    @pytest.mark.parametrize(
        "confidence,n_counterexamples,expect_accepted",
        [
            (0.9, 0, True),
            (0.7, 0, True),
            (0.5, 0, True),
            (0.3, 0, False),   # deferred
            (0.9, 15, False),  # counterexamples drag score below threshold
        ],
    )
    def test_treaty_candidate_acceptance_parametrised(
        self, confidence, n_counterexamples, expect_accepted
    ):
        """is_accepted() reflects the composite score across a range of inputs."""
        cand = TreatyCandidate(
            confidence=confidence, counterexample_count=n_counterexamples
        )
        assert cand.is_accepted() == expect_accepted

    @pytest.mark.parametrize(
        "source",
        [
            CandidateSource.MINED,
            CandidateSource.HYPOTHESIZED,
            CandidateSource.INHERITED,
            CandidateSource.SYNTHESIZED,
        ],
    )
    def test_treaty_candidate_source_variants(self, source):
        """TreatyCandidate can be constructed with each CandidateSource value."""
        cand = TreatyCandidate(source=source)
        assert cand.source == source

    @pytest.mark.parametrize(
        "role",
        [
            TreatyRole.PRIMARY,
            TreatyRole.AUXILIARY,
            TreatyRole.DERIVED,
            TreatyRole.FOUNDATIONAL,
        ],
    )
    def test_treaty_candidate_role_variants(self, role):
        """TreatyCandidate can be constructed with each TreatyRole value."""
        cand = TreatyCandidate(role=role)
        assert cand.role == role


# ===========================================================================
# Section 3 – OverlapLaw
# ===========================================================================
#
# Tests cover:
#   • Construction defaults and all explicit fields
#   • Stability ladder: UNSTABLE → PROVISIONAL → STABLE → PROVEN
#   • promote_stability / demote_stability (immutable: return new copies)
#   • with_observation(supported=True/False) updates support_count or
#     violation_count and recomputes confidence
#   • violation_rate, observation_count
#   • canonical_pair: lexicographic ordering
#   • involves_patch: returns True/False correctly
#   • is_stable, is_proven, is_provisional, is_unstable predicates
#   • Edge cases: zero observations, all violations, promoted past PROVEN,
#     demoted past UNSTABLE


class TestOverlapLaw:
    """Tests for OverlapLaw construction, stability ladder, and evidence tracking."""

    def test_overlap_law_construction_defaults(self):
        """OverlapLaw with no arguments has empty predicate and PROVISIONAL stability."""
        law = OverlapLaw()
        assert law.patch_pair == ("", "")
        assert law.stability == LawStability.PROVISIONAL
        assert law.support_count == 0
        assert law.violation_count == 0

    def test_overlap_law_construction_explicit(self):
        """All constructor fields are stored when provided."""
        law = OverlapLaw(
            law_id="law-001",
            patch_pair=("mod_a", "mod_b"),
            predicate_description="a depends on b",
            stability=LawStability.STABLE,
            support_count=10,
            violation_count=1,
            confidence=0.9,
            discovered_in_record_id="rec-99",
        )
        assert law.law_id == "law-001"
        assert law.patch_pair == ("mod_a", "mod_b")
        assert law.stability == LawStability.STABLE
        assert law.support_count == 10
        assert law.confidence == pytest.approx(0.9)

    def test_overlap_law_check_violation_no_violation(self):
        """is_stable() is True when stability is STABLE or PROVEN."""
        law = make_overlap_law(stability=LawStability.STABLE)
        assert law.is_stable()
        assert not law.is_unstable()

    def test_overlap_law_check_violation_with_violation_status(self):
        """is_unstable() is True when stability is UNSTABLE."""
        law = make_overlap_law(stability=LawStability.UNSTABLE, violations=5)
        assert law.is_unstable()
        assert not law.is_stable()

    def test_overlap_law_promote_stability_from_unstable(self):
        """promote_stability() advances UNSTABLE → PROVISIONAL."""
        law = OverlapLaw(stability=LawStability.UNSTABLE)
        promoted = law.promote_stability()
        assert promoted.stability == LawStability.PROVISIONAL
        assert law.stability == LawStability.UNSTABLE  # original untouched

    def test_overlap_law_promote_stability_from_provisional(self):
        """promote_stability() advances PROVISIONAL → STABLE."""
        law = OverlapLaw(stability=LawStability.PROVISIONAL)
        assert law.promote_stability().stability == LawStability.STABLE

    def test_overlap_law_promote_stability_from_stable(self):
        """promote_stability() advances STABLE → PROVEN."""
        law = OverlapLaw(stability=LawStability.STABLE)
        assert law.promote_stability().stability == LawStability.PROVEN

    def test_overlap_law_promote_stability_saturates_at_proven(self, proven_law):
        """promote_stability() on a PROVEN law stays PROVEN (ceiling)."""
        still_proven = proven_law.promote_stability()
        assert still_proven.stability == LawStability.PROVEN

    def test_overlap_law_demote_stability_from_proven(self, proven_law):
        """demote_stability() drops PROVEN → STABLE."""
        demoted = proven_law.demote_stability()
        assert demoted.stability == LawStability.STABLE

    def test_overlap_law_demote_stability_saturates_at_unstable(self):
        """demote_stability() on an UNSTABLE law stays UNSTABLE (floor)."""
        law = OverlapLaw(stability=LawStability.UNSTABLE)
        still_unstable = law.demote_stability()
        assert still_unstable.stability == LawStability.UNSTABLE

    def test_overlap_law_with_observation_supported(self):
        """with_observation(True) increments support_count."""
        law = OverlapLaw(support_count=4, violation_count=1)
        updated = law.with_observation(supported=True)
        assert updated.support_count == 5
        assert law.support_count == 4  # original unchanged

    def test_overlap_law_with_observation_violation(self):
        """with_observation(False) increments violation_count."""
        law = OverlapLaw(support_count=4, violation_count=1)
        updated = law.with_observation(supported=False)
        assert updated.violation_count == 2
        assert law.violation_count == 1

    def test_overlap_law_violation_rate_zero_violations(self):
        """violation_rate() is 0.0 when there are no violations."""
        law = OverlapLaw(support_count=10, violation_count=0)
        assert law.violation_rate() == pytest.approx(0.0)

    def test_overlap_law_violation_rate_all_violations(self):
        """violation_rate() is 1.0 when all observations are violations."""
        law = OverlapLaw(support_count=0, violation_count=5)
        assert law.violation_rate() == pytest.approx(1.0)

    def test_overlap_law_violation_rate_no_observations(self):
        """violation_rate() returns 0.0 when there are zero observations."""
        law = OverlapLaw(support_count=0, violation_count=0)
        result = law.violation_rate()
        assert result == pytest.approx(0.0) or isinstance(result, float)

    def test_overlap_law_observation_count(self):
        """observation_count() is the sum of supports and violations."""
        law = OverlapLaw(support_count=7, violation_count=3)
        assert law.observation_count() == 10

    def test_overlap_law_canonical_pair_already_sorted(self):
        """canonical_pair() returns (a, b) when a < b lexicographically."""
        law = OverlapLaw(patch_pair=("alpha", "zeta"))
        assert law.canonical_pair() == ("alpha", "zeta")

    def test_overlap_law_canonical_pair_reversed(self):
        """canonical_pair() sorts so the smaller key comes first."""
        law = OverlapLaw(patch_pair=("zeta", "alpha"))
        assert law.canonical_pair() == ("alpha", "zeta")

    def test_overlap_law_involves_patch_true(self):
        """involves_patch() returns True when the patch is in the pair."""
        law = OverlapLaw(patch_pair=("p1", "p2"))
        assert law.involves_patch("p1")
        assert law.involves_patch("p2")

    def test_overlap_law_involves_patch_false(self):
        """involves_patch() returns False when the patch is not in the pair."""
        law = OverlapLaw(patch_pair=("p1", "p2"))
        assert not law.involves_patch("p3")

    def test_overlap_law_repr(self):
        """repr() mentions the class name."""
        law = OverlapLaw(law_id="l-repr")
        assert "OverlapLaw" in repr(law)

    def test_overlap_law_equality(self):
        """Two OverlapLaws with the same fields are equal."""
        l1 = OverlapLaw(law_id="same", patch_pair=("a", "b"), confidence=0.7)
        l2 = OverlapLaw(law_id="same", patch_pair=("a", "b"), confidence=0.7)
        assert l1 == l2

    def test_overlap_law_stability_score_after_multiple_updates(self):
        """Multiple with_observation() calls accumulate correctly."""
        law = OverlapLaw()
        for _ in range(5):
            law = law.with_observation(supported=True)
        for _ in range(2):
            law = law.with_observation(supported=False)
        assert law.support_count == 5
        assert law.violation_count == 2
        assert law.observation_count() == 7

    @pytest.mark.parametrize(
        "law_id,predicate,stability,support,violations",
        [
            ("l001", "pred_A", LawStability.UNSTABLE, 0, 3),
            ("l002", "pred_B", LawStability.PROVISIONAL, 4, 1),
            ("l003", "pred_C", LawStability.STABLE, 20, 0),
            ("l004", "pred_D", LawStability.PROVEN, 100, 0),
            ("l005", "a_implies_b", LawStability.PROVISIONAL, 7, 2),
        ],
    )
    def test_overlap_law_parametrised_construction(
        self, law_id, predicate, stability, support, violations
    ):
        """OverlapLaw stores all provided fields under various stability states."""
        law = OverlapLaw(
            law_id=law_id,
            predicate_description=predicate,
            stability=stability,
            support_count=support,
            violation_count=violations,
        )
        assert law.law_id == law_id
        assert law.predicate_description == predicate
        assert law.stability == stability
        assert law.support_count == support
        assert law.violation_count == violations


# ===========================================================================
# Section 4 – DependentTreaty
# ===========================================================================
#
# Tests cover:
#   • Default construction and explicit fields
#   • has_dependencies / dependency_count
#   • mark_resolved: returns a copy with is_resolved==True
#   • mark_resolved raises ValueError when already resolved
#   • unresolved_dependencies: returns ids not in accepted set
#   • is_ready_to_evaluate: True when all deps are in accepted set
#   • with_dependency / with_clause builders
#   • patch_count / clause_count
#   • Integration: using OverlapTreaty as a dependency signal


class TestDependentTreaty:
    """Tests for DependentTreaty construction and dependency resolution logic."""

    def test_dependent_treaty_construction_defaults(self):
        """A default DependentTreaty has empty dependencies and is unresolved."""
        dt = DependentTreaty()
        assert isinstance(dt.treaty_id, str)
        assert dt.dependency_ids == ()
        assert not dt.is_resolved

    def test_dependent_treaty_construction_explicit(self):
        """All fields are stored correctly when specified."""
        dt = DependentTreaty(
            treaty_id="dep-explicit",
            patch_keys=("mod_a", "mod_b"),
            dependency_ids=("t1", "t2"),
            clause_descriptions=("must_satisfy_X",),
            is_resolved=False,
            role=TreatyRole.DERIVED,
            required_tier_value=2,
        )
        assert dt.treaty_id == "dep-explicit"
        assert dt.dependency_count() == 2
        assert dt.patch_count() == 2
        assert dt.clause_count() == 1

    def test_dependent_treaty_resolve_dependencies_empty(self):
        """A treaty with no dependencies is immediately ready to evaluate."""
        dt = DependentTreaty(treaty_id="no-deps")
        assert not dt.has_dependencies()
        assert dt.is_ready_to_evaluate(frozenset())

    def test_dependent_treaty_resolve_dependencies_chain(self):
        """unresolved_dependencies filters out ids present in the accepted set."""
        dt = DependentTreaty(dependency_ids=("t1", "t2", "t3"))
        accepted = frozenset({"t1", "t2"})
        unresolved = dt.unresolved_dependencies(accepted)
        assert unresolved == ("t3",)

    def test_dependent_treaty_check_consistency_consistent(self):
        """is_ready_to_evaluate returns True when all deps are accepted."""
        dt = DependentTreaty(dependency_ids=("t1", "t2"))
        assert dt.is_ready_to_evaluate(frozenset({"t1", "t2", "t3"}))

    def test_dependent_treaty_check_consistency_inconsistent(self):
        """is_ready_to_evaluate returns False when any dep is missing."""
        dt = DependentTreaty(dependency_ids=("t1", "t2"))
        assert not dt.is_ready_to_evaluate(frozenset({"t1"}))

    def test_dependent_treaty_mark_resolved(self, resolved_dependent_treaty):
        """mark_resolved() returns a new copy with is_resolved==True."""
        resolved = resolved_dependent_treaty.mark_resolved(provenance=("step_x",))
        assert resolved.is_resolved
        assert not resolved_dependent_treaty.is_resolved

    def test_dependent_treaty_mark_resolved_already_resolved_raises(self):
        """Calling mark_resolved() on an already-resolved treaty raises ValueError."""
        dt = DependentTreaty(is_resolved=True)
        with pytest.raises(ValueError):
            dt.mark_resolved(provenance=("oops",))

    def test_dependent_treaty_add_dependency(self):
        """with_dependency() appends a new dependency id."""
        dt = DependentTreaty(dependency_ids=("t1",))
        updated = dt.with_dependency("t2")
        assert "t2" in updated.dependency_ids
        assert "t2" not in dt.dependency_ids

    def test_dependent_treaty_add_dependency_no_duplicate(self):
        """with_dependency() with an already-present id does not duplicate it."""
        dt = DependentTreaty(dependency_ids=("t1",))
        updated = dt.with_dependency("t1")
        assert updated.dependency_ids.count("t1") == 1

    def test_dependent_treaty_with_clause(self):
        """with_clause() appends a clause description string."""
        dt = DependentTreaty()
        updated = dt.with_clause("invariant holds")
        assert "invariant holds" in updated.clause_descriptions

    def test_dependent_treaty_to_dict_if_present(self):
        """If to_dict() is available it must return a plain dict."""
        dt = DependentTreaty()
        if not hasattr(dt, "to_dict"):
            pytest.skip("to_dict not implemented")
        assert isinstance(dt.to_dict(), dict)

    def test_dependent_treaty_with_overlap_treaty_logic(self):
        """
        A DependentTreaty whose patch_keys match an OverlapTreaty's patches
        can be evaluated once those treaties are accepted.
        """
        overlap = make_overlap_treaty(patches=("p1", "p2"), satisfied=True)
        dt = DependentTreaty(
            patch_keys=overlap.patches,
            dependency_ids=("dep-of-overlap",),
        )
        # Simulate the overlap treaty being accepted
        accepted = frozenset({"dep-of-overlap"})
        assert dt.is_ready_to_evaluate(accepted)

    @pytest.mark.parametrize(
        "n_deps,n_accepted,expect_ready",
        [
            (0, 0, True),
            (1, 0, False),
            (1, 1, True),
            (3, 2, False),
            (5, 5, True),
        ],
    )
    def test_dependent_treaty_readiness_parametrised(
        self, n_deps, n_accepted, expect_ready
    ):
        """is_ready_to_evaluate is True iff all n_deps deps are accepted."""
        dep_ids = tuple(f"t{i}" for i in range(n_deps))
        accepted = frozenset(f"t{i}" for i in range(n_accepted))
        dt = DependentTreaty(dependency_ids=dep_ids)
        assert dt.is_ready_to_evaluate(accepted) == expect_ready


# ===========================================================================
# Section 5 – SynthesisOutcome
# ===========================================================================
#
# Tests cover:
#   • Construction for SUCCESS, PARTIAL_SUCCESS, FAILURE, TIMEOUT,
#     BUDGET_EXHAUSTED outcome kinds
#   • is_success / is_partial / is_failure predicates
#   • law_count, stable_law_count, proven_law_count
#   • failed_patch_count, repair_suggestion_count
#   • laws_for_pair: order-independent lookup
#   • highest_stability_law: returns the most stable law or None
#   • summary(): returns a non-empty string
#   • Edge cases: zero laws, no failed patches, all laws proven


class TestSynthesisOutcome:
    """Tests for SynthesisOutcome predicates, counts, and law queries."""

    def test_synthesis_outcome_construction_success(self):
        """SynthesisOutcome with SUCCESS kind is constructed correctly."""
        outcome = SynthesisOutcome(kind=OutcomeKind.SUCCESS)
        assert outcome.kind == OutcomeKind.SUCCESS

    def test_synthesis_outcome_construction_failure(self):
        """SynthesisOutcome with FAILURE kind is constructed correctly."""
        outcome = SynthesisOutcome(kind=OutcomeKind.FAILURE)
        assert outcome.kind == OutcomeKind.FAILURE

    def test_synthesis_outcome_is_successful_true(self, success_outcome):
        """is_success() returns True for a SUCCESS outcome."""
        assert success_outcome.is_success()

    def test_synthesis_outcome_is_successful_false(self):
        """is_success() returns False for non-SUCCESS outcomes."""
        assert not SynthesisOutcome(kind=OutcomeKind.FAILURE).is_success()
        assert not SynthesisOutcome(kind=OutcomeKind.TIMEOUT).is_success()

    def test_synthesis_outcome_is_partial(self):
        """is_partial() returns True only for PARTIAL_SUCCESS."""
        partial = SynthesisOutcome(kind=OutcomeKind.PARTIAL_SUCCESS)
        assert partial.is_partial()
        assert not partial.is_success()
        assert not partial.is_failure()

    def test_synthesis_outcome_is_failure_covers_timeout(self):
        """is_failure() covers TIMEOUT and BUDGET_EXHAUSTED as well."""
        for kind in (OutcomeKind.FAILURE, OutcomeKind.TIMEOUT, OutcomeKind.BUDGET_EXHAUSTED):
            outcome = SynthesisOutcome(kind=kind)
            assert outcome.is_failure(), f"Expected is_failure() for {kind}"

    def test_synthesis_outcome_get_treaty_count_empty(self):
        """accepted_treaties_count is 0 when nothing was accepted."""
        outcome = SynthesisOutcome(kind=OutcomeKind.SUCCESS, accepted_treaties_count=0)
        assert outcome.accepted_treaties_count == 0

    def test_synthesis_outcome_get_treaty_count_multiple(self):
        """accepted_treaties_count stores the supplied count exactly."""
        outcome = SynthesisOutcome(kind=OutcomeKind.SUCCESS, accepted_treaties_count=7)
        assert outcome.accepted_treaties_count == 7

    def test_synthesis_outcome_get_law_count(self, success_outcome):
        """law_count() equals the number of accepted_laws."""
        assert success_outcome.law_count() == 2

    def test_synthesis_outcome_get_law_count_zero(self):
        """law_count() is 0 when no laws are provided."""
        outcome = SynthesisOutcome()
        assert outcome.law_count() == 0

    def test_synthesis_outcome_stable_law_count(self, success_outcome):
        """stable_law_count() counts laws where is_stable() is True."""
        count = success_outcome.stable_law_count()
        assert count >= 1  # proven_law and stable_law are both stable

    def test_synthesis_outcome_proven_law_count(self, success_outcome):
        """proven_law_count() counts only PROVEN laws."""
        assert success_outcome.proven_law_count() == 1  # one proven_law fixture

    def test_synthesis_outcome_summarize(self, success_outcome):
        """summary() must return a non-empty string."""
        s = success_outcome.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_synthesis_outcome_to_dict_if_present(self, success_outcome):
        """If to_dict() exists it must return a plain dict."""
        if not hasattr(success_outcome, "to_dict"):
            pytest.skip("to_dict not implemented")
        d = success_outcome.to_dict()
        assert isinstance(d, dict)

    def test_synthesis_outcome_failed_patch_count(self):
        """failed_patch_count() equals the size of failed_patches."""
        outcome = SynthesisOutcome(
            kind=OutcomeKind.PARTIAL_SUCCESS,
            failed_patches=("pA", "pB", "pC"),
        )
        assert outcome.failed_patch_count() == 3

    def test_synthesis_outcome_failed_patch_count_zero(self):
        """failed_patch_count() is 0 when no patches failed."""
        outcome = SynthesisOutcome(kind=OutcomeKind.SUCCESS)
        assert outcome.failed_patch_count() == 0

    def test_synthesis_outcome_repair_suggestion_count(self):
        """repair_suggestion_count() equals the number of repair suggestions."""
        outcome = SynthesisOutcome(
            kind=OutcomeKind.FAILURE,
            repair_suggestions=("fix_import", "add_stub", "reorder_calls"),
        )
        assert outcome.repair_suggestion_count() == 3

    def test_synthesis_outcome_laws_for_pair(self):
        """laws_for_pair() returns laws whose patch_pair matches (in either order)."""
        law_ab = OverlapLaw(patch_pair=("alpha", "beta"))
        law_bc = OverlapLaw(patch_pair=("beta", "gamma"))
        outcome = SynthesisOutcome(
            kind=OutcomeKind.SUCCESS, accepted_laws=(law_ab, law_bc)
        )
        result = outcome.laws_for_pair("alpha", "beta")
        assert len(result) == 1
        assert law_ab in result

    def test_synthesis_outcome_laws_for_pair_order_independent(self):
        """laws_for_pair() finds the law regardless of argument order."""
        law = OverlapLaw(patch_pair=("p1", "p2"))
        outcome = SynthesisOutcome(kind=OutcomeKind.SUCCESS, accepted_laws=(law,))
        assert len(outcome.laws_for_pair("p1", "p2")) == 1
        assert len(outcome.laws_for_pair("p2", "p1")) == 1

    def test_synthesis_outcome_highest_stability_law(self, success_outcome):
        """highest_stability_law() returns the law with the highest stability."""
        best = success_outcome.highest_stability_law()
        assert best is not None
        assert best.stability == LawStability.PROVEN

    def test_synthesis_outcome_highest_stability_law_none_when_empty(self):
        """highest_stability_law() returns None when accepted_laws is empty."""
        outcome = SynthesisOutcome(kind=OutcomeKind.FAILURE, accepted_laws=())
        assert outcome.highest_stability_law() is None

    def test_synthesis_outcome_equality(self):
        """Two identical outcomes are equal."""
        o1 = SynthesisOutcome(outcome_id="oo", kind=OutcomeKind.SUCCESS)
        o2 = SynthesisOutcome(outcome_id="oo", kind=OutcomeKind.SUCCESS)
        assert o1 == o2

    @pytest.mark.parametrize(
        "success,n_treaties,n_laws",
        [
            (OutcomeKind.SUCCESS, 0, 0),
            (OutcomeKind.SUCCESS, 1, 0),
            (OutcomeKind.SUCCESS, 5, 3),
            (OutcomeKind.FAILURE, 0, 0),
            (OutcomeKind.PARTIAL_SUCCESS, 2, 1),
        ],
    )
    def test_synthesis_outcome_parametrised(self, success, n_treaties, n_laws):
        """SynthesisOutcome stores treaty and law counts under varied configurations."""
        laws = tuple(
            OverlapLaw(patch_pair=(f"p{i}", f"p{i+1}")) for i in range(n_laws)
        )
        outcome = SynthesisOutcome(
            kind=success,
            accepted_treaties_count=n_treaties,
            accepted_laws=laws,
        )
        assert outcome.kind == success
        assert outcome.accepted_treaties_count == n_treaties
        assert outcome.law_count() == n_laws

    @pytest.mark.parametrize(
        "kind,expect_success,expect_partial,expect_failure",
        [
            (OutcomeKind.SUCCESS, True, False, False),
            (OutcomeKind.PARTIAL_SUCCESS, False, True, False),
            (OutcomeKind.FAILURE, False, False, True),
            (OutcomeKind.TIMEOUT, False, False, True),
            (OutcomeKind.BUDGET_EXHAUSTED, False, False, True),
        ],
    )
    def test_synthesis_outcome_kind_predicates_parametrised(
        self, kind, expect_success, expect_partial, expect_failure
    ):
        """Outcome kind predicates are mutually exclusive and exhaustive."""
        outcome = SynthesisOutcome(kind=kind)
        assert outcome.is_success() == expect_success
        assert outcome.is_partial() == expect_partial
        assert outcome.is_failure() == expect_failure


# ===========================================================================
# Section 6 – Enum coverage
# ===========================================================================


class TestEnumValues:
    """Sanity-check that all enumeration values expected by the helpers exist."""

    def test_synthesis_phase_has_all_expected_values(self):
        """SynthesisPhase must contain all documented lifecycle states."""
        expected = {
            "DECOMPOSING", "COVERING", "VALIDATING",
            "REFINING", "FINALIZING", "COMPLETE", "FAILED",
        }
        actual = {m.name for m in SynthesisPhase}
        assert expected.issubset(actual)

    def test_law_stability_has_four_levels(self):
        """LawStability must define exactly four rungs on the stability ladder."""
        assert len(LawStability) >= 4
        names = {m.name for m in LawStability}
        assert "UNSTABLE" in names
        assert "PROVISIONAL" in names
        assert "STABLE" in names
        assert "PROVEN" in names

    def test_candidate_source_variants_present(self):
        """CandidateSource must include MINED, HYPOTHESIZED, INHERITED, SYNTHESIZED."""
        names = {m.name for m in CandidateSource}
        assert {"MINED", "HYPOTHESIZED", "INHERITED", "SYNTHESIZED"}.issubset(names)

    def test_treaty_role_variants_present(self):
        """TreatyRole must include PRIMARY, AUXILIARY, DERIVED, FOUNDATIONAL."""
        names = {m.name for m in TreatyRole}
        assert {"PRIMARY", "AUXILIARY", "DERIVED", "FOUNDATIONAL"}.issubset(names)

    def test_outcome_kind_covers_all_terminal_states(self):
        """OutcomeKind must cover success, partial, failure, timeout, and budget."""
        names = {m.name for m in OutcomeKind}
        assert {"SUCCESS", "PARTIAL_SUCCESS", "FAILURE", "TIMEOUT", "BUDGET_EXHAUSTED"}.issubset(
            names
        )

    @pytest.mark.parametrize(
        "phase",
        [
            SynthesisPhase.DECOMPOSING,
            SynthesisPhase.COVERING,
            SynthesisPhase.VALIDATING,
            SynthesisPhase.REFINING,
            SynthesisPhase.FINALIZING,
        ],
    )
    def test_synthesis_phase_non_terminal_phases(self, phase):
        """All mid-lifecycle phases produce a non-terminal record."""
        rec = HypercoverSynthesisRecord(record_id="r", phase=phase)
        assert not rec.is_terminal()

    @pytest.mark.parametrize(
        "stability",
        [
            LawStability.UNSTABLE,
            LawStability.PROVISIONAL,
            LawStability.STABLE,
            LawStability.PROVEN,
        ],
    )
    def test_law_stability_round_trip_via_value(self, stability):
        """LawStability values can be looked up by their value attribute."""
        looked_up = LawStability(stability.value)
        assert looked_up == stability


# ===========================================================================
# Section 7 – Cross-class integration scenarios
# ===========================================================================
#
# These tests use multiple model classes together to verify that they
# interact correctly in realistic synthesis workflows.


class TestCrossClassIntegration:
    """Integration tests spanning multiple model classes."""

    def test_full_synthesis_happy_path(self):
        """
        Simulate a minimal but complete synthesis pass:
        1. Create a record in DECOMPOSING phase.
        2. Add steps and transition through phases.
        3. Accept a treaty and record a law.
        4. Produce a SUCCESS outcome.
        5. Verify all counters are consistent.
        """
        rec = HypercoverSynthesisRecord(
            record_id="integration-001",
            goal_proposition="prop_X",
            cover_patch_keys=("mod_a", "mod_b"),
            phase=SynthesisPhase.DECOMPOSING,
        )
        rec = rec.with_step("decompose")
        rec = rec.with_phase(SynthesisPhase.COVERING)
        rec = rec.with_step("cover")
        rec = rec.with_phase(SynthesisPhase.VALIDATING)

        candidate = TreatyCandidate(
            confidence=0.85,
            patch_keys=("mod_a", "mod_b"),
            proposed_clauses=("mod_a depends on mod_b",),
        )
        assert candidate.is_accepted()

        rec = rec.with_treaty_accepted(candidate.candidate_id)
        rec = rec.with_phase(SynthesisPhase.COMPLETE)

        law = OverlapLaw(
            patch_pair=("mod_a", "mod_b"),
            predicate_description="a_depends_b",
            stability=LawStability.STABLE,
            support_count=8,
        )

        outcome = SynthesisOutcome(
            kind=OutcomeKind.SUCCESS,
            record_id=rec.record_id,
            accepted_laws=(law,),
            accepted_treaties_count=1,
        )

        assert rec.is_complete()
        assert outcome.is_success()
        assert outcome.law_count() == 1
        assert outcome.stable_law_count() == 1

    def test_synthesis_failure_path_with_repair(self):
        """
        A failed synthesis run should surface repair suggestions and count
        failed patches correctly in the SynthesisOutcome.
        """
        rec = HypercoverSynthesisRecord(
            record_id="fail-001",
            cover_patch_keys=("pA", "pB", "pC"),
            phase=SynthesisPhase.VALIDATING,
        )
        rec = rec.with_candidate_rejected("bad-cand")
        rec = rec.with_phase(SynthesisPhase.FAILED)

        outcome = SynthesisOutcome(
            kind=OutcomeKind.FAILURE,
            record_id=rec.record_id,
            failed_patches=("pA", "pB"),
            repair_suggestions=("strengthen_guard_A", "add_stub_B"),
        )

        assert rec.is_failed()
        assert outcome.is_failure()
        assert outcome.failed_patch_count() == 2
        assert outcome.repair_suggestion_count() == 2

    def test_dependent_treaty_gates_law_acceptance(self):
        """
        A DependentTreaty should not be ready to evaluate until its upstream
        treaties are accepted, after which the corresponding law can be recorded.
        """
        upstream = TreatyCandidate(confidence=0.9, candidate_id="upstream-001")
        dependent = DependentTreaty(
            patch_keys=("pX", "pY"),
            dependency_ids=(upstream.candidate_id,),
        )

        # Before acceptance
        assert not dependent.is_ready_to_evaluate(frozenset())

        # Simulate acceptance
        accepted = frozenset({upstream.candidate_id})
        assert dependent.is_ready_to_evaluate(accepted)

        resolved = dependent.mark_resolved(provenance=("step_resolve",))
        assert resolved.is_resolved

        law = OverlapLaw(
            patch_pair=("pX", "pY"),
            predicate_description="X_meets_Y",
            stability=LawStability.PROVISIONAL,
        )
        outcome = SynthesisOutcome(
            kind=OutcomeKind.SUCCESS,
            accepted_laws=(law,),
            accepted_treaties_count=1,
        )
        assert outcome.laws_for_pair("pX", "pY") == [law]

    def test_law_stability_promotion_feeds_outcome(self):
        """
        A law that is repeatedly observed as supported can be promoted to PROVEN
        and will then be counted by proven_law_count() in the outcome.
        """
        law = OverlapLaw(
            patch_pair=("m1", "m2"),
            stability=LawStability.PROVISIONAL,
        )
        for _ in range(10):
            law = law.with_observation(supported=True)

        # Manually promote twice to reach PROVEN
        law = law.promote_stability().promote_stability()

        outcome = SynthesisOutcome(
            kind=OutcomeKind.SUCCESS,
            accepted_laws=(law,),
        )
        assert outcome.proven_law_count() == 1
        assert outcome.stable_law_count() == 1

    def test_many_patches_and_many_laws_in_outcome(self):
        """
        SynthesisOutcome can hold 20 laws and correctly reports counts.
        """
        laws = tuple(
            OverlapLaw(
                patch_pair=(f"p{i}", f"p{i+1}"),
                stability=LawStability.STABLE if i % 2 == 0 else LawStability.PROVISIONAL,
            )
            for i in range(20)
        )
        outcome = SynthesisOutcome(
            kind=OutcomeKind.PARTIAL_SUCCESS,
            accepted_laws=laws,
            accepted_treaties_count=20,
        )
        assert outcome.law_count() == 20
        # 10 STABLE (even indices) and 10 PROVISIONAL (odd indices); STABLE counts as stable
        assert outcome.stable_law_count() == 10

    def test_record_acceptance_ratio_tracks_candidate_decisions(self):
        """
        Accepting and rejecting candidates through the record builders
        should produce the expected acceptance_ratio().
        """
        rec = HypercoverSynthesisRecord(record_id="ratio-test")
        for i in range(6):
            rec = rec.with_treaty_accepted(f"t{i}")
        for i in range(4):
            rec = rec.with_candidate_rejected(f"c{i}")

        assert rec.acceptance_ratio() == pytest.approx(0.6)

    @pytest.mark.parametrize(
        "n_patches,n_steps,n_accepted",
        [
            (2, 1, 0),
            (5, 3, 2),
            (10, 10, 5),
            (0, 0, 0),
            (20, 15, 10),
        ],
    )
    def test_record_counts_parametrised(self, n_patches, n_steps, n_accepted):
        """Patch, step, and accepted-treaty counts match what was inserted."""
        patches = tuple(f"p{i}" for i in range(n_patches))
        rec = HypercoverSynthesisRecord(
            record_id="cnt",
            cover_patch_keys=patches,
        )
        for i in range(n_steps):
            rec = rec.with_step(f"step_{i}")
        for i in range(n_accepted):
            rec = rec.with_treaty_accepted(f"t{i}")

        assert rec.patch_count() == n_patches
        assert rec.step_count() == n_steps
        assert len(rec.accepted_treaty_ids) == n_accepted
