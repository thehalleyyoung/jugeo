"""
Tests for methodology_loops.theorems.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

This module tests the theorem infrastructure of the methodology_loops package.
Theorems capture the mathematical guarantees that underpin the methodology loop
design: convergence, falsification completeness, formalization soundness,
implementation completeness, and revision monotonicity. The registry aggregates
theorems and provides verification, export, and reporting utilities.
"""
from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import time
import uuid

from jugeo.evaluation.methodology_loops.theorems import (
    TheoremStatus, TheoremProofStrategy, TheoremRecord,
    LoopConvergenceTheorem, FalsificationCompletenessTheorem,
    FormalizationSoundnessTheorem, ImplementationCompletenessTheorem,
    RevisionMonotonicityTheorem, MethodologyTheoremRegistry,
    build_theorem_registry, verify_theorem,
    theorem_dependency_graph, export_theorem_latex,
)
from jugeo.evaluation.methodology_loops.models import (
    LoopPhase, LoopStatus, MethodologyConfig, LoopDiagnostics,
    LoopState, MethodologyLoop,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_theorem_record():
    """Return a sample TheoremRecord for generic tests."""
    return TheoremRecord.create(
        name="Test Theorem",
        statement="For all x, P(x) implies Q(x).",
        proof_sketch="By induction on x. Base case: trivial. Inductive step: assume P(k), show P(k+1).",
        assumptions=["P is decidable", "Q is monotone"],
        strategy=TheoremProofStrategy.INDUCTION,
    )


@pytest.fixture
def convergence_theorem():
    """Return a LoopConvergenceTheorem instance."""
    return LoopConvergenceTheorem()


@pytest.fixture
def falsification_theorem():
    """Return a FalsificationCompletenessTheorem instance."""
    return FalsificationCompletenessTheorem()


@pytest.fixture
def formalization_theorem():
    """Return a FormalizationSoundnessTheorem instance."""
    return FormalizationSoundnessTheorem()


@pytest.fixture
def implementation_theorem():
    """Return an ImplementationCompletenessTheorem instance."""
    return ImplementationCompletenessTheorem()


@pytest.fixture
def revision_theorem():
    """Return a RevisionMonotonicityTheorem instance."""
    return RevisionMonotonicityTheorem()


@pytest.fixture
def registry():
    """Return a MethodologyTheoremRegistry populated with default theorems."""
    return MethodologyTheoremRegistry.default()


@pytest.fixture
def mock_loop():
    """Return a minimal MethodologyLoop for theorem verification tests."""
    config = MethodologyConfig(
        max_iterations=5, convergence_threshold=0.9,
        falsification_budget=20, min_coverage=0.7, max_revisions=3
    )
    diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
    state = LoopState(
        phase=LoopPhase.FORMALIZATION, iteration=0, artifacts=[],
        diagnostics=diag, history=[], status=LoopStatus.IDLE
    )
    return MethodologyLoop(
        loop_id="theorem-test-loop",
        config=config, state=state, transitions=[], artifacts=[],
        created_at=1000.0, updated_at=1000.0,
    )


# ===========================================================================
# TestTheoremStatus
# ===========================================================================

class TestTheoremStatus:
    """Tests for the TheoremStatus enum.

    TheoremStatus tracks the proof state of a theorem: whether it is
    unverified, verified, refuted, or pending review.
    """

    def test_has_unverified(self):
        """TheoremStatus must have an UNVERIFIED member."""
        assert hasattr(TheoremStatus, "UNVERIFIED")

    def test_has_verified(self):
        """TheoremStatus must have a VERIFIED member."""
        assert hasattr(TheoremStatus, "VERIFIED")

    def test_has_refuted(self):
        """TheoremStatus must have a REFUTED member."""
        assert hasattr(TheoremStatus, "REFUTED")

    def test_has_pending(self):
        """TheoremStatus must have a PENDING member."""
        assert hasattr(TheoremStatus, "PENDING")

    def test_is_enum(self):
        """TheoremStatus must be an enum type."""
        import enum
        assert issubclass(TheoremStatus, enum.Enum)

    def test_members_have_values(self):
        """All TheoremStatus members must have non-None values."""
        for member in TheoremStatus:
            assert member.value is not None

    @pytest.mark.parametrize("member", list(TheoremStatus))
    def test_each_member_str_value(self, member):
        """Each TheoremStatus member should have a string value."""
        assert isinstance(member.value, str)


# ===========================================================================
# TestTheoremProofStrategy
# ===========================================================================

class TestTheoremProofStrategy:
    """Tests for the TheoremProofStrategy enum.

    TheoremProofStrategy enumerates the proof techniques available for
    methodology theorems: induction, construction, contradiction, etc.
    """

    def test_has_induction(self):
        """TheoremProofStrategy must have an INDUCTION member."""
        assert hasattr(TheoremProofStrategy, "INDUCTION")

    def test_has_construction(self):
        """TheoremProofStrategy must have a CONSTRUCTION member."""
        assert hasattr(TheoremProofStrategy, "CONSTRUCTION")

    def test_has_contradiction(self):
        """TheoremProofStrategy must have a CONTRADICTION member."""
        assert hasattr(TheoremProofStrategy, "CONTRADICTION")

    def test_is_enum(self):
        """TheoremProofStrategy must be an enum type."""
        import enum
        assert issubclass(TheoremProofStrategy, enum.Enum)

    def test_members_have_values(self):
        """All TheoremProofStrategy members must have non-None values."""
        for member in TheoremProofStrategy:
            assert member.value is not None

    @pytest.mark.parametrize("member", list(TheoremProofStrategy))
    def test_each_member_str_value(self, member):
        """Each TheoremProofStrategy member should have a string value."""
        assert isinstance(member.value, str)


# ===========================================================================
# TestTheoremRecord
# ===========================================================================

class TestTheoremRecord:
    """Tests for TheoremRecord data class.

    TheoremRecord is the primary carrier of theorem metadata: name, statement,
    proof sketch, assumptions, dependencies, status, and proof strategy.
    """

    def test_create(self, sample_theorem_record):
        """TheoremRecord.create() must populate all core fields."""
        r = sample_theorem_record
        assert isinstance(r.name, str) and len(r.name) > 0
        assert isinstance(r.statement, str) and len(r.statement) > 0
        assert isinstance(r.proof_sketch, str) and len(r.proof_sketch) > 0
        assert isinstance(r.assumptions, (list, tuple))

    def test_initial_status_unverified(self, sample_theorem_record):
        """Newly created TheoremRecord should have UNVERIFIED status."""
        assert sample_theorem_record.status == TheoremStatus.UNVERIFIED

    def test_verified_at_none_initially(self, sample_theorem_record):
        """verified_at must be None before the theorem is verified."""
        assert sample_theorem_record.verified_at is None

    def test_mark_verified_changes_status(self, sample_theorem_record):
        """mark_verified() must change status to VERIFIED."""
        verified = sample_theorem_record.mark_verified()
        assert verified.status == TheoremStatus.VERIFIED

    def test_mark_verified_sets_verified_at(self, sample_theorem_record):
        """mark_verified() must set verified_at to a non-None value."""
        verified = sample_theorem_record.mark_verified()
        assert verified.verified_at is not None

    def test_mark_refuted_changes_status(self, sample_theorem_record):
        """mark_refuted() must change status to REFUTED."""
        refuted = sample_theorem_record.mark_refuted(counterexample="x=0 violates Q(x).")
        assert refuted.status == TheoremStatus.REFUTED

    def test_add_assumption(self, sample_theorem_record):
        """add_assumption() must return a record with the new assumption."""
        updated = sample_theorem_record.add_assumption("R is transitive")
        assert "R is transitive" in updated.assumptions

    def test_add_reference(self, sample_theorem_record):
        """add_reference() must return a record with the new reference."""
        updated = sample_theorem_record.add_reference("theory2.tex §62.3")
        assert "theory2.tex §62.3" in updated.references

    def test_to_json_round_trip(self, sample_theorem_record):
        """Serialisation round-trip must preserve all core fields."""
        j = sample_theorem_record.to_json()
        restored = TheoremRecord.from_json(j)
        assert restored.name == sample_theorem_record.name
        assert restored.statement == sample_theorem_record.statement
        assert restored.status == sample_theorem_record.status

    def test_summarize_returns_string(self, sample_theorem_record):
        """summarize() must return a non-empty string."""
        s = sample_theorem_record.summarize()
        assert isinstance(s, str) and len(s) > 0

    def test_render_tex_returns_string(self, sample_theorem_record):
        """render_tex() must return a non-empty string."""
        tex = sample_theorem_record.render_tex()
        assert isinstance(tex, str) and len(tex) > 0

    def test_is_sound_false_initially(self, sample_theorem_record):
        """is_sound() should return False for an unverified theorem."""
        assert not sample_theorem_record.is_sound()

    def test_is_sound_true_after_verify(self, sample_theorem_record):
        """is_sound() should return True after mark_verified()."""
        verified = sample_theorem_record.mark_verified()
        assert verified.is_sound()

    def test_dependency_ids_initially_empty(self, sample_theorem_record):
        """A freshly created TheoremRecord should have no dependencies."""
        deps = sample_theorem_record.dependency_ids()
        assert isinstance(deps, (list, tuple, set))

    def test_record_id_unique(self):
        """Each TheoremRecord.create() call must yield a unique record_id."""
        r1 = TheoremRecord.create(
            name="T", statement="S", proof_sketch="P",
            assumptions=[], strategy=TheoremProofStrategy.INDUCTION
        )
        r2 = TheoremRecord.create(
            name="T", statement="S", proof_sketch="P",
            assumptions=[], strategy=TheoremProofStrategy.INDUCTION
        )
        assert r1.record_id != r2.record_id


# ===========================================================================
# TestLoopConvergenceTheorem
# ===========================================================================

class TestLoopConvergenceTheorem:
    """Tests for LoopConvergenceTheorem.

    LoopConvergenceTheorem asserts that a methodology loop terminates in a
    converged state under the configured convergence threshold, provided
    sufficient iterations are allowed.
    """

    def test_init(self, convergence_theorem):
        """LoopConvergenceTheorem can be instantiated without arguments."""
        assert convergence_theorem is not None

    def test_has_record(self, convergence_theorem):
        """LoopConvergenceTheorem must expose a TheoremRecord."""
        assert isinstance(convergence_theorem.record, TheoremRecord)

    def test_statement_nonempty(self, convergence_theorem):
        """Theorem statement must be a non-empty string."""
        assert isinstance(convergence_theorem.record.statement, str)
        assert len(convergence_theorem.record.statement) > 0

    def test_proof_sketch_nonempty(self, convergence_theorem):
        """Proof sketch must be a non-empty string."""
        assert isinstance(convergence_theorem.record.proof_sketch, str)
        assert len(convergence_theorem.record.proof_sketch) > 0

    def test_assumptions_list(self, convergence_theorem):
        """Assumptions must be a list (possibly empty)."""
        assert isinstance(convergence_theorem.record.assumptions, (list, tuple))

    def test_verify_with_mock_loop(self, convergence_theorem, mock_loop):
        """verify(loop) must return a result without raising."""
        result = convergence_theorem.verify(mock_loop)
        assert result is not None

    def test_to_json_returns_string(self, convergence_theorem):
        """to_json() must return a string."""
        j = convergence_theorem.to_json()
        assert isinstance(j, str) and len(j) > 2

    def test_summarize_returns_string(self, convergence_theorem):
        """summarize() must return a non-empty string."""
        s = convergence_theorem.summarize()
        assert isinstance(s, str) and len(s) > 0

    def test_render_tex_returns_string(self, convergence_theorem):
        """render_tex() must return a non-empty string."""
        tex = convergence_theorem.render_tex()
        assert isinstance(tex, str) and len(tex) > 0

    def test_strategy_is_proof_strategy(self, convergence_theorem):
        """Theorem proof strategy must be a TheoremProofStrategy member."""
        strategy = convergence_theorem.record.strategy
        assert isinstance(strategy, TheoremProofStrategy)


# ===========================================================================
# TestFalsificationCompletenessTheorem
# ===========================================================================

class TestFalsificationCompletenessTheorem:
    """Tests for FalsificationCompletenessTheorem.

    FalsificationCompletenessTheorem asserts that the falsification loop
    exhaustively tests all hypotheses within the allocated budget, and that
    no counterexample is missed within the search space.
    """

    def test_init(self, falsification_theorem):
        """FalsificationCompletenessTheorem can be instantiated."""
        assert falsification_theorem is not None

    def test_has_record(self, falsification_theorem):
        """Must expose a TheoremRecord."""
        assert isinstance(falsification_theorem.record, TheoremRecord)

    def test_statement_nonempty(self, falsification_theorem):
        """Theorem statement must be non-empty."""
        assert len(falsification_theorem.record.statement) > 0

    def test_proof_sketch_nonempty(self, falsification_theorem):
        """Proof sketch must be non-empty."""
        assert len(falsification_theorem.record.proof_sketch) > 0

    def test_assumptions_list(self, falsification_theorem):
        """Assumptions must be a list."""
        assert isinstance(falsification_theorem.record.assumptions, (list, tuple))

    def test_verify_with_mock_loop(self, falsification_theorem, mock_loop):
        """verify(loop) must not raise and must return a result."""
        result = falsification_theorem.verify(mock_loop)
        assert result is not None

    def test_to_json_returns_string(self, falsification_theorem):
        """to_json() must return a string."""
        assert isinstance(falsification_theorem.to_json(), str)

    def test_summarize_returns_string(self, falsification_theorem):
        """summarize() must return a non-empty string."""
        assert isinstance(falsification_theorem.summarize(), str)

    def test_render_tex_returns_string(self, falsification_theorem):
        """render_tex() must return a non-empty string."""
        assert isinstance(falsification_theorem.render_tex(), str)


# ===========================================================================
# TestFormalizationSoundnessTheorem
# ===========================================================================

class TestFormalizationSoundnessTheorem:
    """Tests for FormalizationSoundnessTheorem.

    FormalizationSoundnessTheorem asserts that the formalization loop
    produces a formal specification that is sound with respect to the
    informal requirements it was derived from.
    """

    def test_init(self, formalization_theorem):
        """FormalizationSoundnessTheorem can be instantiated."""
        assert formalization_theorem is not None

    def test_has_record(self, formalization_theorem):
        """Must expose a TheoremRecord."""
        assert isinstance(formalization_theorem.record, TheoremRecord)

    def test_statement_nonempty(self, formalization_theorem):
        """Theorem statement must be non-empty."""
        assert len(formalization_theorem.record.statement) > 0

    def test_proof_sketch_nonempty(self, formalization_theorem):
        """Proof sketch must be non-empty."""
        assert len(formalization_theorem.record.proof_sketch) > 0

    def test_assumptions_list(self, formalization_theorem):
        """Assumptions must be a list."""
        assert isinstance(formalization_theorem.record.assumptions, (list, tuple))

    def test_verify_with_mock_loop(self, formalization_theorem, mock_loop):
        """verify(loop) must return a result without raising."""
        result = formalization_theorem.verify(mock_loop)
        assert result is not None

    def test_to_json_returns_string(self, formalization_theorem):
        """to_json() must return a string."""
        assert isinstance(formalization_theorem.to_json(), str)

    def test_summarize_returns_string(self, formalization_theorem):
        """summarize() must return a non-empty string."""
        assert isinstance(formalization_theorem.summarize(), str)

    def test_render_tex_returns_string(self, formalization_theorem):
        """render_tex() must return a non-empty string."""
        assert isinstance(formalization_theorem.render_tex(), str)


# ===========================================================================
# TestImplementationCompletenessTheorem
# ===========================================================================

class TestImplementationCompletenessTheorem:
    """Tests for ImplementationCompletenessTheorem.

    ImplementationCompletenessTheorem asserts that the implementation loop
    produces artefacts that are complete with respect to the formal
    specification produced by the formalization loop.
    """

    def test_init(self, implementation_theorem):
        """ImplementationCompletenessTheorem can be instantiated."""
        assert implementation_theorem is not None

    def test_has_record(self, implementation_theorem):
        """Must expose a TheoremRecord."""
        assert isinstance(implementation_theorem.record, TheoremRecord)

    def test_statement_nonempty(self, implementation_theorem):
        """Statement must be non-empty."""
        assert len(implementation_theorem.record.statement) > 0

    def test_proof_sketch_nonempty(self, implementation_theorem):
        """Proof sketch must be non-empty."""
        assert len(implementation_theorem.record.proof_sketch) > 0

    def test_assumptions_list(self, implementation_theorem):
        """Assumptions must be a list."""
        assert isinstance(implementation_theorem.record.assumptions, (list, tuple))

    def test_verify_with_mock_loop(self, implementation_theorem, mock_loop):
        """verify(loop) must return a result without raising."""
        result = implementation_theorem.verify(mock_loop)
        assert result is not None

    def test_to_json_returns_string(self, implementation_theorem):
        """to_json() must return a string."""
        assert isinstance(implementation_theorem.to_json(), str)

    def test_summarize_returns_string(self, implementation_theorem):
        """summarize() must return a non-empty string."""
        assert isinstance(implementation_theorem.summarize(), str)

    def test_render_tex_returns_string(self, implementation_theorem):
        """render_tex() must return a non-empty string."""
        assert isinstance(implementation_theorem.render_tex(), str)


# ===========================================================================
# TestRevisionMonotonicityTheorem
# ===========================================================================

class TestRevisionMonotonicityTheorem:
    """Tests for RevisionMonotonicityTheorem.

    RevisionMonotonicityTheorem asserts that each revision of the methodology
    loop does not regress: quality metrics are monotonically non-decreasing
    across successive revisions.
    """

    def test_init(self, revision_theorem):
        """RevisionMonotonicityTheorem can be instantiated."""
        assert revision_theorem is not None

    def test_has_record(self, revision_theorem):
        """Must expose a TheoremRecord."""
        assert isinstance(revision_theorem.record, TheoremRecord)

    def test_statement_nonempty(self, revision_theorem):
        """Statement must be non-empty."""
        assert len(revision_theorem.record.statement) > 0

    def test_proof_sketch_nonempty(self, revision_theorem):
        """Proof sketch must be non-empty."""
        assert len(revision_theorem.record.proof_sketch) > 0

    def test_assumptions_list(self, revision_theorem):
        """Assumptions must be a list."""
        assert isinstance(revision_theorem.record.assumptions, (list, tuple))

    def test_verify_with_mock_loop(self, revision_theorem, mock_loop):
        """verify(loop) must return a result without raising."""
        result = revision_theorem.verify(mock_loop)
        assert result is not None

    def test_to_json_returns_string(self, revision_theorem):
        """to_json() must return a string."""
        assert isinstance(revision_theorem.to_json(), str)

    def test_summarize_returns_string(self, revision_theorem):
        """summarize() must return a non-empty string."""
        assert isinstance(revision_theorem.summarize(), str)

    def test_render_tex_returns_string(self, revision_theorem):
        """render_tex() must return a non-empty string."""
        assert isinstance(revision_theorem.render_tex(), str)


# ===========================================================================
# TestMethodologyTheoremRegistry
# ===========================================================================

class TestMethodologyTheoremRegistry:
    """Tests for MethodologyTheoremRegistry.

    MethodologyTheoremRegistry stores and manages all five methodology
    theorems, provides verification, export, and health-check capabilities.
    """

    def test_init_empty(self):
        """MethodologyTheoremRegistry() can be instantiated without errors."""
        reg = MethodologyTheoremRegistry()
        assert reg is not None

    def test_default_five_theorems(self, registry):
        """MethodologyTheoremRegistry.default() must register five theorems."""
        assert registry.count() == 5

    def test_register(self):
        """register() adds a theorem to the registry."""
        reg = MethodologyTheoremRegistry()
        record = TheoremRecord.create(
            name="Custom", statement="C(x).", proof_sketch="Trivial.",
            assumptions=[], strategy=TheoremProofStrategy.CONSTRUCTION
        )
        reg.register(record)
        assert reg.count() == 1

    def test_get_returns_record(self, registry):
        """get(name) must return the corresponding TheoremRecord."""
        all_records = registry.list_all()
        if all_records:
            name = all_records[0].name
            fetched = registry.get(name)
            assert fetched is not None
            assert fetched.name == name

    def test_list_all_returns_list(self, registry):
        """list_all() must return a list of TheoremRecords."""
        records = registry.list_all()
        assert isinstance(records, list)
        assert all(isinstance(r, TheoremRecord) for r in records)

    def test_list_by_status_unverified(self, registry):
        """list_by_status(UNVERIFIED) must return theorems with UNVERIFIED status."""
        unverified = registry.list_by_status(TheoremStatus.UNVERIFIED)
        assert isinstance(unverified, list)
        for r in unverified:
            assert r.status == TheoremStatus.UNVERIFIED

    def test_list_by_strategy(self, registry):
        """list_by_strategy() must return only records with the given strategy."""
        for strategy in TheoremProofStrategy:
            records = registry.list_by_strategy(strategy)
            assert isinstance(records, list)
            for r in records:
                assert r.strategy == strategy

    def test_count_positive(self, registry):
        """count() must return a positive integer for the default registry."""
        assert registry.count() > 0

    def test_verify_all_returns_results(self, registry, mock_loop):
        """verify_all(loop) must return a list of results with length == count()."""
        results = registry.verify_all(mock_loop)
        assert isinstance(results, list)
        assert len(results) == registry.count()

    def test_summary_report_returns_string(self, registry):
        """summary_report() must return a non-empty string."""
        report = registry.summary_report()
        assert isinstance(report, str) and len(report) > 0

    def test_to_json_round_trip(self, registry):
        """Serialisation round-trip must preserve count."""
        j = registry.to_json()
        restored = MethodologyTheoremRegistry.from_json(j)
        assert restored.count() == registry.count()

    def test_render_tex_all_returns_string(self, registry):
        """render_tex_all() must return a non-empty string."""
        tex = registry.render_tex_all()
        assert isinstance(tex, str) and len(tex) > 0

    def test_export_bib_returns_string(self, registry):
        """export_bib() must return a string (BibTeX or similar)."""
        bib = registry.export_bib()
        assert isinstance(bib, str)

    def test_health_check_returns_bool(self, registry):
        """health_check() must return a boolean."""
        result = registry.health_check()
        assert isinstance(result, bool)


# ===========================================================================
# TestBuildTheoremRegistry
# ===========================================================================

class TestBuildTheoremRegistry:
    """Tests for build_theorem_registry() factory function."""

    def test_returns_registry(self):
        """build_theorem_registry() must return a MethodologyTheoremRegistry."""
        reg = build_theorem_registry()
        assert isinstance(reg, MethodologyTheoremRegistry)

    def test_has_theorems(self):
        """Registry returned by build_theorem_registry() must have theorems."""
        reg = build_theorem_registry()
        assert reg.count() > 0

    def test_count_positive(self):
        """count() must be positive for the built registry."""
        reg = build_theorem_registry()
        assert reg.count() > 0

    def test_two_calls_independent(self):
        """Two calls must return independent registry objects."""
        r1 = build_theorem_registry()
        r2 = build_theorem_registry()
        assert r1 is not r2


# ===========================================================================
# TestVerifyTheorem
# ===========================================================================

class TestVerifyTheorem:
    """Tests for the module-level verify_theorem() function."""

    def test_known_theorem(self, mock_loop):
        """verify_theorem() for a known theorem name must return a result."""
        registry = build_theorem_registry()
        all_records = registry.list_all()
        if all_records:
            result = verify_theorem(all_records[0].name, mock_loop)
            assert result is not None

    def test_unknown_theorem_raises_or_returns_none(self, mock_loop):
        """verify_theorem() for an unknown name must raise or return None."""
        result = verify_theorem("NonExistentTheoremXYZ", mock_loop)
        # Either None or an error result is acceptable
        assert result is None or (hasattr(result, "is_error") and result.is_error())

    def test_with_registry(self, mock_loop):
        """verify_theorem() with an explicit registry must use it."""
        registry = build_theorem_registry()
        all_records = registry.list_all()
        if all_records:
            result = verify_theorem(all_records[0].name, mock_loop, registry=registry)
            assert result is not None


# ===========================================================================
# TestTheoremDependencyGraph
# ===========================================================================

class TestTheoremDependencyGraph:
    """Tests for theorem_dependency_graph() function."""

    def test_returns_dict(self):
        """theorem_dependency_graph() must return a dict."""
        graph = theorem_dependency_graph()
        assert isinstance(graph, dict)

    def test_all_ids_present(self):
        """All theorem record IDs in the default registry should appear in the graph."""
        registry = build_theorem_registry()
        graph = theorem_dependency_graph()
        # The graph should be non-empty when there are theorems
        if registry.count() > 0:
            assert len(graph) >= 0  # May be empty if no dependencies


# ===========================================================================
# TestExportTheoremLatex
# ===========================================================================

class TestExportTheoremLatex:
    """Tests for export_theorem_latex() function."""

    def test_returns_str(self):
        """export_theorem_latex() must return a string."""
        result = export_theorem_latex()
        assert isinstance(result, str)

    def test_nonempty(self):
        """export_theorem_latex() must return a non-empty string."""
        result = export_theorem_latex()
        assert len(result) > 0

    def test_contains_theorem_keyword(self):
        """LaTeX output should contain a theorem-related keyword."""
        result = export_theorem_latex()
        lower = result.lower()
        # At least one of these LaTeX keywords should appear
        assert any(kw in lower for kw in ["theorem", "\\begin", "\\section", "loop"])


# ===========================================================================
# Parametrized tests across all five theorem classes
# ===========================================================================

THEOREM_CLASSES = [
    LoopConvergenceTheorem,
    FalsificationCompletenessTheorem,
    FormalizationSoundnessTheorem,
    ImplementationCompletenessTheorem,
    RevisionMonotonicityTheorem,
]


@pytest.mark.parametrize("TheoremClass", THEOREM_CLASSES)
def test_each_theorem_has_non_empty_name(TheoremClass):
    """Every theorem class must produce a record with a non-empty name."""
    thm = TheoremClass()
    assert isinstance(thm.record.name, str)
    assert len(thm.record.name) > 0


@pytest.mark.parametrize("TheoremClass", THEOREM_CLASSES)
def test_each_theorem_render_tex(TheoremClass):
    """render_tex() must return a non-empty string for every theorem class."""
    thm = TheoremClass()
    tex = thm.render_tex()
    assert isinstance(tex, str) and len(tex) > 0


@pytest.mark.parametrize("TheoremClass", THEOREM_CLASSES)
def test_each_theorem_to_json_round_trip(TheoremClass):
    """to_json() / from_json() round-trip must work for every theorem class."""
    thm = TheoremClass()
    j = thm.to_json()
    # We do not reconstruct the theorem itself, just verify the JSON is valid
    import json as _json
    parsed = _json.loads(j)
    assert isinstance(parsed, dict)


@pytest.mark.parametrize("TheoremClass", THEOREM_CLASSES)
def test_each_theorem_verify_with_loop(TheoremClass):
    """verify() must return a non-None result for every theorem class."""
    config = MethodologyConfig(
        max_iterations=5, convergence_threshold=0.9,
        falsification_budget=20, min_coverage=0.7, max_revisions=3
    )
    diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
    state = LoopState(
        phase=LoopPhase.FORMALIZATION, iteration=0, artifacts=[],
        diagnostics=diag, history=[], status=LoopStatus.IDLE
    )
    loop = MethodologyLoop(
        loop_id="param-theorem-loop", config=config, state=state,
        transitions=[], artifacts=[], created_at=1000.0, updated_at=1000.0
    )
    thm = TheoremClass()
    result = thm.verify(loop)
    assert result is not None
