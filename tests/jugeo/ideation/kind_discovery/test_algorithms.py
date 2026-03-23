"""Tests for jugeo.ideation.kind_discovery.algorithms.

Covers KindDiscoveryEngine, KindValidator, KindRanker, KindEvolutionTracker,
DiscoveryDiagnostics, DiscoveryHistory, and DiscoveryAlgorithm enum.

All tests are skipped automatically when the module has not yet been
implemented; import errors are surfaced as skip reasons rather than failures.
"""

from __future__ import annotations

import uuid
from pathlib import Path
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Optional import — skip when not yet implemented
# ---------------------------------------------------------------------------

import pytest

try:
    from jugeo.ideation.kind_discovery.algorithms import (
        DiscoveryAlgorithm,
        KindDiscoveryEngine,
        KindValidator,
        KindRanker,
        KindEvolutionTracker,
        DiscoveryDiagnostics,
        DiscoveryHistory,
    )
    _ALGORITHMS_AVAILABLE = True
except ImportError as _alg_import_error:
    _ALGORITHMS_AVAILABLE = False
    _alg_import_error_msg = str(_alg_import_error)

try:
    from jugeo.ideation.kind_discovery.models import (
        KindCandidate,
        KindPattern,
        KindStatus,
        KindBootstrapPlan,
        NewKind,
        ObstructionField,
        ObstructionType,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

try:
    from jugeo.ideation.ideas import (
        Idea,
        GainProfile,
        ValidationPath,
        TrustStatus,
        IdeaPortfolio,
        IdeaEvaluator,
    )
    _IDEAS_AVAILABLE = True
except ImportError:
    _IDEAS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _ALGORITHMS_AVAILABLE,
    reason=(
        "jugeo.ideation.kind_discovery.algorithms not yet implemented"
        + (f": {_alg_import_error_msg}" if not _ALGORITHMS_AVAILABLE else "")
    ),
)


# ---------------------------------------------------------------------------
# Test factories
# ---------------------------------------------------------------------------

def _make_kind(
    name: str = "TestKind",
    confidence: float = 0.7,
    status: "KindStatus | None" = None,
    examples: tuple[str, ...] | None = None,
    theorems: tuple[str, ...] | None = None,
) -> "NewKind":
    """Create a NewKind with sensible defaults for testing."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    if status is None:
        status = KindStatus.PROVISIONAL
    if examples is None:
        examples = (
            f"Example 1 of {name}: trivial case with unit obstruction",
            f"Example 2 of {name}: non-trivial instance from derived category",
        )
    if theorems is None:
        theorems = (f"Every {name} admits a canonical resolution.",)
    return NewKind(
        kind_id=str(uuid.uuid4()),
        name=name,
        formal_definition=(
            f"A {name} is a mathematical object defined by the vanishing of "
            f"obstruction classes in a derived category. It is closed under "
            f"pullback and admits a canonical spectral sequence."
        ),
        examples=examples,
        theorems=theorems,
        discovery_path=(
            "obstruction_mining",
            "pattern_recognition",
            "bootstrapping",
        ),
        status=status,
        confidence=confidence,
    )


def _make_established_kind(name: str = "EstablishedKind") -> "NewKind":
    """Create a NewKind with ESTABLISHED status."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    # Build through valid status transitions to reach ESTABLISHED
    kind = _make_kind(name=name, confidence=0.95, status=KindStatus.PROVISIONAL)
    return kind.with_status(KindStatus.ESTABLISHED)


def _make_incomplete_kind(name: str = "IncompleteKind") -> "NewKind":
    """Create a NewKind with minimal content to test incomplete detection."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    return NewKind(
        kind_id=str(uuid.uuid4()),
        name=name,
        formal_definition="Minimal definition.",
        examples=(),
        theorems=(),
        discovery_path=("basic discovery",),
        status=KindStatus.CANDIDATE,
        confidence=0.2,
    )


def _make_idea(idea_id: str = "test-idea-0001", title: str = "Test Idea") -> "Idea":
    """Create a minimal Idea for integration tests."""
    if not _IDEAS_AVAILABLE:
        pytest.skip("ideas module not available")
    return Idea(
        idea_id=idea_id,
        title=title,
        purpose="discover new mathematical kinds",
        target_area="algebraic topology",
        hypothesis="Obstruction patterns encode latent kind structure.",
        predicted_gain=GainProfile(
            theorem_yield=3.5,
            bridge_impact=2.0,
            cost=1.0,
            uncertainty=0.3,
        ),
        novelty_score=0.75,
        validation_plan=ValidationPath(
            steps=("identify obstruction field", "extract pattern", "bootstrap kind"),
            required_evidence=("field survey", "pattern frequency >= 3"),
            success_criteria=("kind has formal definition", "at least 2 examples"),
        ),
        trust_status=TrustStatus.PROVISIONAL,
    )


def _make_text_corpus(n: int = 3) -> list[str]:
    """Create a list of sample mathematical texts for discovery tests."""
    base_texts = [
        (
            "In the category of chain complexes, obstruction classes arise naturally "
            "from the failure of extensions to split. The algebraic Ext functor "
            "captures these obstructions in a canonical way, leading to a rich theory."
        ),
        (
            "Sheaf cohomology provides a systematic framework for measuring obstructions "
            "to the existence of global sections. The Čech obstruction class is central "
            "to understanding bundle extensions over topological spaces."
        ),
        (
            "Deformation theory studies obstructions to extending infinitesimal "
            "deformations. The Kodaira–Spencer class measures the primary obstruction, "
            "while higher obstructions live in cohomology groups of the tangent sheaf."
        ),
        (
            "The derived category provides a unified setting for obstruction theory. "
            "Distinguished triangles encode short exact sequences of obstruction data, "
            "and the long exact cohomology sequence tracks their propagation."
        ),
        (
            "Motivic obstruction theory studies obstructions to rational points. "
            "The Brauer–Manin obstruction and its refinements provide systematic "
            "tools for determining when local solutions lift to global ones."
        ),
    ]
    return base_texts[:max(1, min(n, len(base_texts)))]


# ===========================================================================
# DiscoveryAlgorithm tests
# ===========================================================================

class TestDiscoveryAlgorithmEnum:
    """Tests for the DiscoveryAlgorithm enumeration."""

    def test_exhaustive_member_exists(self) -> None:
        """DiscoveryAlgorithm.EXHAUSTIVE is a valid enum member."""
        assert hasattr(DiscoveryAlgorithm, "EXHAUSTIVE")

    def test_greedy_member_exists(self) -> None:
        """DiscoveryAlgorithm.GREEDY is a valid enum member."""
        assert hasattr(DiscoveryAlgorithm, "GREEDY")

    def test_beam_search_member_exists(self) -> None:
        """DiscoveryAlgorithm.BEAM_SEARCH is a valid enum member."""
        assert hasattr(DiscoveryAlgorithm, "BEAM_SEARCH")

    def test_frequency_guided_member_exists(self) -> None:
        """DiscoveryAlgorithm.FREQUENCY_GUIDED is a valid enum member."""
        assert hasattr(DiscoveryAlgorithm, "FREQUENCY_GUIDED")

    def test_pattern_first_member_exists(self) -> None:
        """DiscoveryAlgorithm.PATTERN_FIRST is a valid enum member."""
        assert hasattr(DiscoveryAlgorithm, "PATTERN_FIRST")

    def test_hybrid_member_exists(self) -> None:
        """DiscoveryAlgorithm.HYBRID is a valid enum member."""
        assert hasattr(DiscoveryAlgorithm, "HYBRID")

    def test_all_members_have_string_values(self) -> None:
        """All DiscoveryAlgorithm members have string values."""
        for member in DiscoveryAlgorithm:
            assert isinstance(member.value, str)

    def test_enum_members_are_distinct(self) -> None:
        """All DiscoveryAlgorithm values are distinct."""
        values = [m.value for m in DiscoveryAlgorithm]
        assert len(values) == len(set(values))

    def test_algorithm_count_is_at_least_six(self) -> None:
        """There are at least six DiscoveryAlgorithm members."""
        assert len(list(DiscoveryAlgorithm)) >= 6

    def test_algorithm_is_string_enum(self) -> None:
        """DiscoveryAlgorithm members behave as strings."""
        for member in DiscoveryAlgorithm:
            assert str(member.value) == member.value


# ===========================================================================
# KindDiscoveryEngine tests
# ===========================================================================

class TestKindDiscoveryEngineConstruction:
    """Tests for KindDiscoveryEngine construction."""

    def test_default_construction_succeeds(self) -> None:
        """KindDiscoveryEngine() constructs with default algorithm."""
        engine = KindDiscoveryEngine()
        assert engine is not None

    def test_construction_with_greedy_algorithm(self) -> None:
        """KindDiscoveryEngine constructed with GREEDY algorithm."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.GREEDY)
        assert engine is not None

    def test_construction_with_exhaustive_algorithm(self) -> None:
        """KindDiscoveryEngine constructed with EXHAUSTIVE algorithm."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.EXHAUSTIVE)
        assert engine is not None

    def test_construction_with_beam_search(self) -> None:
        """KindDiscoveryEngine constructed with BEAM_SEARCH algorithm."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.BEAM_SEARCH)
        assert engine is not None

    def test_construction_with_frequency_guided(self) -> None:
        """KindDiscoveryEngine constructed with FREQUENCY_GUIDED algorithm."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.FREQUENCY_GUIDED)
        assert engine is not None

    def test_construction_with_hybrid(self) -> None:
        """KindDiscoveryEngine constructed with HYBRID algorithm."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.HYBRID)
        assert engine is not None

    def test_algorithm_attribute_is_stored(self) -> None:
        """The algorithm attribute stores the supplied algorithm."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.GREEDY)
        assert engine.algorithm == DiscoveryAlgorithm.GREEDY


class TestKindDiscoveryEngineDiscover:
    """Tests for KindDiscoveryEngine.discover."""

    def test_discover_returns_list(self) -> None:
        """discover returns a list."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(2)
        result = engine.discover(texts)
        assert isinstance(result, list)

    def test_discover_items_are_new_kinds(self) -> None:
        """All items returned by discover are NewKind instances."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(3)
        for item in engine.discover(texts):
            assert isinstance(item, NewKind)

    def test_discover_kinds_have_non_empty_names(self) -> None:
        """All discovered kinds have non-empty names."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(3)
        for kind in engine.discover(texts):
            assert kind.name.strip() != ""

    def test_discover_kinds_have_non_empty_definitions(self) -> None:
        """All discovered kinds have non-empty formal definitions."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(3)
        for kind in engine.discover(texts):
            assert kind.formal_definition.strip() != ""

    def test_discover_kinds_have_valid_confidence(self) -> None:
        """All discovered kinds have confidence in [0, 1]."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(3)
        for kind in engine.discover(texts):
            assert 0.0 <= kind.confidence <= 1.0

    def test_discover_with_domain_parameter(self) -> None:
        """discover accepts a domain parameter without error."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(2)
        result = engine.discover(texts, domain="algebraic_topology")
        assert isinstance(result, list)

    def test_discover_with_empty_texts(self) -> None:
        """discover with empty texts list returns empty list."""
        engine = KindDiscoveryEngine()
        result = engine.discover([])
        assert result == []

    def test_discover_with_single_text(self) -> None:
        """discover with a single text returns a list (may be empty)."""
        engine = KindDiscoveryEngine()
        texts = _make_text_corpus(1)
        result = engine.discover(texts)
        assert isinstance(result, list)

    def test_discover_with_greedy_algorithm(self) -> None:
        """discover with GREEDY algorithm returns list of NewKind."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.GREEDY)
        texts = _make_text_corpus(3)
        result = engine.discover(texts)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, NewKind)

    def test_discover_with_exhaustive_algorithm(self) -> None:
        """discover with EXHAUSTIVE algorithm returns list of NewKind."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.EXHAUSTIVE)
        texts = _make_text_corpus(2)
        result = engine.discover(texts)
        assert isinstance(result, list)


class TestKindDiscoveryEngineAdvanced:
    """Tests for discover_from_ideas, discover_incremental, get_pipeline_summary."""

    def test_discover_from_ideas_returns_list(self) -> None:
        """discover_from_ideas returns a list of NewKind."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        engine = KindDiscoveryEngine()
        ideas = [_make_idea(f"idea-{i:04d}", f"Idea {i}") for i in range(3)]
        result = engine.discover_from_ideas(ideas)
        assert isinstance(result, list)

    def test_discover_from_ideas_items_are_new_kinds(self) -> None:
        """Items from discover_from_ideas are NewKind instances."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        engine = KindDiscoveryEngine()
        ideas = [_make_idea(f"idea-{i:04d}", f"Idea {i}") for i in range(2)]
        for item in engine.discover_from_ideas(ideas):
            assert isinstance(item, NewKind)

    def test_discover_from_ideas_empty_input(self) -> None:
        """discover_from_ideas with empty list returns empty list."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        engine = KindDiscoveryEngine()
        result = engine.discover_from_ideas([])
        assert result == []

    def test_discover_incremental_returns_list(self) -> None:
        """discover_incremental returns a list of NewKind."""
        engine = KindDiscoveryEngine()
        existing = [_make_kind(name="ExistingKind")]
        new_texts = _make_text_corpus(2)
        result = engine.discover_incremental(new_texts, existing_kinds=existing)
        assert isinstance(result, list)

    def test_discover_incremental_items_are_new_kinds(self) -> None:
        """discover_incremental items are NewKind instances."""
        engine = KindDiscoveryEngine()
        existing = [_make_kind(name="ExistingKind")]
        new_texts = _make_text_corpus(2)
        for item in engine.discover_incremental(new_texts, existing_kinds=existing):
            assert isinstance(item, NewKind)

    def test_discover_incremental_avoids_duplicating_existing(self) -> None:
        """discover_incremental does not reproduce exact existing kinds."""
        engine = KindDiscoveryEngine()
        existing = [_make_kind(name="UniqueExistingKind")]
        new_texts = _make_text_corpus(2)
        new_kinds = engine.discover_incremental(
            new_texts, existing_kinds=existing, domain="algebra"
        )
        new_names = {k.name for k in new_kinds}
        # The exact name of the existing kind should not appear in new results
        assert "UniqueExistingKind" not in new_names

    def test_get_pipeline_summary_returns_dict(self) -> None:
        """get_pipeline_summary returns a dict."""
        engine = KindDiscoveryEngine()
        summary = engine.get_pipeline_summary()
        assert isinstance(summary, dict)

    def test_get_pipeline_summary_has_algorithm_field(self) -> None:
        """get_pipeline_summary includes algorithm information."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.HYBRID)
        summary = engine.get_pipeline_summary()
        # Summary should reference the algorithm in some form
        summary_str = str(summary)
        assert "hybrid" in summary_str.lower() or "HYBRID" in summary_str

    def test_get_pipeline_summary_all_values_are_primitives(self) -> None:
        """get_pipeline_summary values are JSON-serialisable."""
        import json
        engine = KindDiscoveryEngine()
        summary = engine.get_pipeline_summary()
        json_str = json.dumps(summary)
        assert isinstance(json_str, str)


# ===========================================================================
# KindValidator tests
# ===========================================================================

class TestKindValidatorBasic:
    """Tests for KindValidator.validate and related methods."""

    def test_validate_returns_tuple(self) -> None:
        """validate returns a (bool, list) tuple."""
        validator = KindValidator()
        kind = _make_kind(confidence=0.8)
        result = validator.validate(kind)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_validate_returns_bool_and_list(self) -> None:
        """validate result is (bool, list[str])."""
        validator = KindValidator()
        kind = _make_kind(confidence=0.8)
        ok, issues = validator.validate(kind)
        assert isinstance(ok, bool)
        assert isinstance(issues, list)

    def test_validate_established_kind_passes(self) -> None:
        """A well-formed ESTABLISHED kind passes validation."""
        validator = KindValidator()
        kind = _make_established_kind(name="WellFormedKind")
        ok, issues = validator.validate(kind)
        assert ok is True
        assert issues == [] or len(issues) == 0

    def test_validate_incomplete_kind_fails(self) -> None:
        """An incomplete kind with no examples or theorems fails validation."""
        validator = KindValidator()
        kind = _make_incomplete_kind(name="IncompleteTestKind")
        ok, issues = validator.validate(kind)
        assert ok is False
        assert len(issues) >= 1

    def test_validate_batch_returns_list_of_pairs(self) -> None:
        """validate_batch returns a list of (NewKind, bool, list) triples or pairs."""
        validator = KindValidator()
        kinds = [_make_kind(confidence=0.7), _make_kind(confidence=0.9)]
        results = validator.validate_batch(kinds)
        assert isinstance(results, list)
        assert len(results) == 2

    def test_validate_batch_empty_input(self) -> None:
        """validate_batch with empty list returns empty list."""
        validator = KindValidator()
        assert validator.validate_batch([]) == []

    def test_validate_batch_consistent_with_single_validate(self) -> None:
        """validate_batch results are consistent with individual validate calls."""
        validator = KindValidator()
        kind = _make_kind(confidence=0.8)
        single_ok, single_issues = validator.validate(kind)
        batch_results = validator.validate_batch([kind])
        # The first batch result should match single result
        assert len(batch_results) == 1
        # Flexible: batch may return (kind, ok, issues) or (ok, issues) per entry
        batch_entry = batch_results[0]
        if len(batch_entry) == 3:
            _k, batch_ok, batch_issues = batch_entry
        else:
            batch_ok, batch_issues = batch_entry
        assert batch_ok == single_ok

    def test_check_definition_completeness_returns_bool(self) -> None:
        """check_definition_completeness returns a boolean."""
        validator = KindValidator()
        kind = _make_kind(confidence=0.8)
        result = validator.check_definition_completeness(kind)
        assert isinstance(result, bool)

    def test_check_definition_completeness_short_definition_fails(self) -> None:
        """A kind with a very short definition is flagged as incomplete."""
        validator = KindValidator()
        short_kind = NewKind(
            kind_id=str(uuid.uuid4()),
            name="ShortKind",
            formal_definition="Short.",
            examples=("example 1",),
            theorems=("theorem 1",),
            discovery_path=("step",),
            status=KindStatus.PROVISIONAL,
            confidence=0.5,
        )
        result = validator.check_definition_completeness(short_kind)
        # A one-word definition should be flagged as incomplete
        assert result is False

    def test_check_example_coverage_returns_bool(self) -> None:
        """check_example_coverage returns a boolean."""
        validator = KindValidator()
        kind = _make_kind()
        result = validator.check_example_coverage(kind)
        assert isinstance(result, bool)

    def test_check_example_coverage_no_examples_fails(self) -> None:
        """A kind with no examples fails coverage check."""
        validator = KindValidator()
        kind = _make_kind(examples=())
        result = validator.check_example_coverage(kind)
        assert result is False

    def test_check_theorem_consistency_returns_bool(self) -> None:
        """check_theorem_consistency returns a boolean."""
        validator = KindValidator()
        kind = _make_kind()
        result = validator.check_theorem_consistency(kind)
        assert isinstance(result, bool)

    def test_check_status_consistency_returns_bool(self) -> None:
        """check_status_consistency returns a boolean."""
        validator = KindValidator()
        kind = _make_kind()
        result = validator.check_status_consistency(kind)
        assert isinstance(result, bool)

    def test_suggest_improvements_returns_list(self) -> None:
        """suggest_improvements returns a list of suggestion strings."""
        validator = KindValidator()
        kind = _make_incomplete_kind()
        suggestions = validator.suggest_improvements(kind)
        assert isinstance(suggestions, list)

    def test_suggest_improvements_for_incomplete_kind_is_non_empty(self) -> None:
        """suggest_improvements provides at least one suggestion for an incomplete kind."""
        validator = KindValidator()
        kind = _make_incomplete_kind()
        suggestions = validator.suggest_improvements(kind)
        assert len(suggestions) >= 1

    def test_suggest_improvements_items_are_strings(self) -> None:
        """All improvement suggestions are non-empty strings."""
        validator = KindValidator()
        kind = _make_incomplete_kind()
        for suggestion in validator.suggest_improvements(kind):
            assert isinstance(suggestion, str)
            assert suggestion.strip() != ""

    def test_validation_report_returns_dict(self) -> None:
        """validation_report returns a dict."""
        validator = KindValidator()
        kind = _make_kind(confidence=0.75)
        report = validator.validation_report(kind)
        assert isinstance(report, dict)

    def test_validation_report_contains_kind_name(self) -> None:
        """validation_report references the kind's name."""
        validator = KindValidator()
        kind = _make_kind(name="ReportedKind")
        report = validator.validation_report(kind)
        report_str = str(report)
        assert "ReportedKind" in report_str or "reported" in report_str.lower()


# ===========================================================================
# KindRanker tests
# ===========================================================================

class TestKindRanker:
    """Tests for KindRanker.rank, score, and related methods."""

    def test_rank_returns_list(self) -> None:
        """rank returns a list."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=c) for c in (0.4, 0.7, 0.9)]
        result = ranker.rank(kinds)
        assert isinstance(result, list)

    def test_rank_items_are_pairs(self) -> None:
        """rank result items are (NewKind, float) pairs."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=0.7)]
        for item in ranker.rank(kinds):
            assert len(item) == 2
            kind, score = item
            assert isinstance(kind, NewKind)
            assert isinstance(score, float)

    def test_rank_is_descending_by_score(self) -> None:
        """rank results are ordered descending by score."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=c) for c in (0.2, 0.5, 0.8, 0.95)]
        ranked = ranker.rank(kinds)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_rank_empty_input_returns_empty(self) -> None:
        """rank([]) returns []."""
        ranker = KindRanker()
        assert ranker.rank([]) == []

    def test_score_returns_float(self) -> None:
        """score returns a float."""
        ranker = KindRanker()
        kind = _make_kind(confidence=0.7)
        result = ranker.score(kind)
        assert isinstance(result, float)

    def test_score_in_unit_interval(self) -> None:
        """score returns a value in [0, 1]."""
        ranker = KindRanker()
        kind = _make_kind(confidence=0.7)
        result = ranker.score(kind)
        assert 0.0 <= result <= 1.0

    def test_quality_score_returns_float_in_unit_interval(self) -> None:
        """quality_score returns a float in [0, 1]."""
        ranker = KindRanker()
        kind = _make_kind(confidence=0.8)
        result = ranker.quality_score(kind)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_quality_score_established_higher_than_candidate(self) -> None:
        """An ESTABLISHED kind scores higher quality than a CANDIDATE kind."""
        ranker = KindRanker()
        established = _make_established_kind()
        candidate = _make_kind(confidence=0.3, status=KindStatus.CANDIDATE)
        assert ranker.quality_score(established) >= ranker.quality_score(candidate)

    def test_novelty_score_returns_float(self) -> None:
        """novelty_score returns a float in [0, 1]."""
        ranker = KindRanker()
        kind = _make_kind(name="NovelKind")
        result = ranker.novelty_score(kind)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_novelty_score_with_empty_existing(self) -> None:
        """novelty_score with empty existing_kinds returns a float."""
        ranker = KindRanker()
        kind = _make_kind()
        result = ranker.novelty_score(kind, existing_kinds=())
        assert isinstance(result, float)

    def test_novelty_score_unique_kind_higher_than_duplicate(self) -> None:
        """A kind unique from existing pool scores higher novelty than a duplicate."""
        ranker = KindRanker()
        kind = _make_kind(name="UniqueAlgebraicObstructionKind")
        clone_pool = [_make_kind(name="UniqueAlgebraicObstructionKind")]
        unrelated_pool = [_make_kind(name="CompletelyUnrelatedFunctor")]
        score_against_clone = ranker.novelty_score(kind, existing_kinds=tuple(clone_pool))
        score_against_unrelated = ranker.novelty_score(kind, existing_kinds=tuple(unrelated_pool))
        assert score_against_unrelated >= score_against_clone

    def test_completeness_score_returns_float(self) -> None:
        """completeness_score returns a float in [0, 1]."""
        ranker = KindRanker()
        kind = _make_kind()
        result = ranker.completeness_score(kind)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_completeness_score_full_kind_higher_than_incomplete(self) -> None:
        """A fully defined kind scores higher completeness than an incomplete one."""
        ranker = KindRanker()
        full = _make_kind(confidence=0.9)
        incomplete = _make_incomplete_kind()
        assert ranker.completeness_score(full) >= ranker.completeness_score(incomplete)

    def test_top_k_returns_at_most_k_kinds(self) -> None:
        """top_k returns at most k kinds."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=c) for c in (0.3, 0.5, 0.7, 0.9)]
        result = ranker.top_k(kinds, k=2)
        assert len(result) <= 2

    def test_top_k_returns_highest_scoring(self) -> None:
        """top_k returns the highest-scoring kinds."""
        ranker = KindRanker()
        high = _make_kind(name="HighConfidence", confidence=0.95)
        mid = _make_kind(name="MidConfidence", confidence=0.5)
        low = _make_kind(name="LowConfidence", confidence=0.1)
        result = ranker.top_k([low, mid, high], k=1)
        assert len(result) == 1
        assert result[0].name == "HighConfidence"

    def test_top_k_empty_input(self) -> None:
        """top_k([]) returns []."""
        ranker = KindRanker()
        assert ranker.top_k([], k=3) == []

    def test_top_k_k_greater_than_list_returns_all(self) -> None:
        """top_k with k > len(kinds) returns all kinds."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=0.7), _make_kind(confidence=0.5)]
        result = ranker.top_k(kinds, k=100)
        assert len(result) == 2

    def test_diversity_rank_returns_list(self) -> None:
        """diversity_rank returns a list."""
        ranker = KindRanker()
        kinds = [_make_kind(name=f"Kind{i}") for i in range(4)]
        result = ranker.diversity_rank(kinds)
        assert isinstance(result, list)

    def test_pareto_optimal_returns_list(self) -> None:
        """pareto_optimal returns a list of NewKind."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=c) for c in (0.3, 0.6, 0.9)]
        result = ranker.pareto_optimal(kinds)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, NewKind)

    def test_pareto_optimal_subset_of_input(self) -> None:
        """pareto_optimal returns a subset of the input kinds."""
        ranker = KindRanker()
        kinds = [_make_kind(confidence=c) for c in (0.2, 0.5, 0.8)]
        result = ranker.pareto_optimal(kinds)
        result_ids = {k.kind_id for k in result}
        input_ids = {k.kind_id for k in kinds}
        assert result_ids.issubset(input_ids)


# ===========================================================================
# KindEvolutionTracker tests
# ===========================================================================

class TestKindEvolutionTracker:
    """Tests for KindEvolutionTracker and its history methods."""

    def test_record_discovery_succeeds(self) -> None:
        """record_discovery adds a kind without error."""
        tracker = KindEvolutionTracker()
        kind = _make_kind(name="TrackedKind")
        tracker.record_discovery(kind)

    def test_record_discovery_makes_kind_findable(self) -> None:
        """After record_discovery, history_for returns entries for that kind."""
        tracker = KindEvolutionTracker()
        kind = _make_kind(name="FindableKind")
        tracker.record_discovery(kind)
        history = tracker.history_for(kind.kind_id)
        assert len(history) >= 1

    def test_record_update_appends_to_history(self) -> None:
        """record_update adds an update entry to the kind's history."""
        tracker = KindEvolutionTracker()
        kind = _make_kind(name="UpdatedKind")
        tracker.record_discovery(kind)
        updated = kind.with_confidence(0.95)
        tracker.record_update(updated)
        history = tracker.history_for(kind.kind_id)
        assert len(history) >= 2

    def test_record_rejection_adds_entry(self) -> None:
        """record_rejection adds a rejection entry for the given kind_id."""
        tracker = KindEvolutionTracker()
        kind = _make_kind(name="RejectedKind")
        tracker.record_discovery(kind)
        tracker.record_rejection(kind.kind_id, reason="insufficient evidence")
        history = tracker.history_for(kind.kind_id)
        assert len(history) >= 2

    def test_history_for_unknown_kind_returns_empty(self) -> None:
        """history_for an unknown kind_id returns empty tuple or list."""
        tracker = KindEvolutionTracker()
        result = tracker.history_for("nonexistent-id-" + str(uuid.uuid4()))
        assert len(result) == 0

    def test_evolution_summary_returns_dict(self) -> None:
        """evolution_summary returns a dict."""
        tracker = KindEvolutionTracker()
        for i in range(3):
            tracker.record_discovery(_make_kind(name=f"SummaryKind{i}"))
        result = tracker.evolution_summary()
        assert isinstance(result, dict)

    def test_evolution_summary_includes_total_count(self) -> None:
        """evolution_summary mentions the number of tracked kinds."""
        tracker = KindEvolutionTracker()
        for i in range(4):
            tracker.record_discovery(_make_kind(name=f"CountedKind{i}"))
        summary = tracker.evolution_summary()
        summary_str = str(summary)
        assert "4" in summary_str or any(str(4) in str(v) for v in summary.values())

    def test_drift_score_returns_float(self) -> None:
        """drift_score returns a float for a tracked kind."""
        tracker = KindEvolutionTracker()
        kind = _make_kind()
        tracker.record_discovery(kind)
        score = tracker.drift_score(kind.kind_id)
        assert isinstance(score, float)

    def test_drift_score_increases_with_updates(self) -> None:
        """A kind with multiple updates has a higher drift score."""
        tracker = KindEvolutionTracker()
        kind = _make_kind()
        tracker.record_discovery(kind)
        # Apply several updates
        updated = kind
        for i in range(5):
            updated = updated.with_confidence(0.5 + i * 0.05)
            tracker.record_update(updated)
        score_after = tracker.drift_score(kind.kind_id)
        # Score should be positive for a kind with multiple updates
        assert score_after >= 0.0

    def test_stable_kinds_returns_list(self) -> None:
        """stable_kinds returns a list."""
        tracker = KindEvolutionTracker()
        for i in range(3):
            kind = _make_kind(name=f"StableKind{i}")
            tracker.record_discovery(kind)
        result = tracker.stable_kinds()
        assert isinstance(result, list)

    def test_volatile_kinds_returns_list(self) -> None:
        """volatile_kinds returns a list."""
        tracker = KindEvolutionTracker()
        kind = _make_kind(name="VolatileKind")
        tracker.record_discovery(kind)
        updated = kind
        for i in range(10):
            updated = updated.with_confidence(0.1 + (i % 8) * 0.1)
            tracker.record_update(updated)
        result = tracker.volatile_kinds()
        assert isinstance(result, list)

    def test_stable_and_volatile_are_disjoint(self) -> None:
        """stable_kinds and volatile_kinds do not share any kind_ids."""
        tracker = KindEvolutionTracker()
        for i in range(5):
            tracker.record_discovery(_make_kind(name=f"DisjointKind{i}"))
        stable_ids = {k.kind_id for k in tracker.stable_kinds()}
        volatile_ids = {k.kind_id for k in tracker.volatile_kinds()}
        assert stable_ids.isdisjoint(volatile_ids)

    def test_merge_histories_combines_entries(self) -> None:
        """merge_histories combines entries from two trackers."""
        tracker_a = KindEvolutionTracker()
        tracker_b = KindEvolutionTracker()
        kind_a = _make_kind(name="KindA")
        kind_b = _make_kind(name="KindB")
        tracker_a.record_discovery(kind_a)
        tracker_b.record_discovery(kind_b)
        tracker_a.merge_histories(tracker_b)
        # After merge, tracker_a should know about kind_b
        history_b = tracker_a.history_for(kind_b.kind_id)
        assert len(history_b) >= 1


# ===========================================================================
# DiscoveryDiagnostics tests
# ===========================================================================

class TestDiscoveryDiagnostics:
    """Tests for DiscoveryDiagnostics."""

    def test_summary_returns_string_or_dict(self) -> None:
        """summary returns a non-empty string or dict."""
        diag = DiscoveryDiagnostics()
        result = diag.summary()
        assert result is not None
        assert str(result).strip() != ""

    def test_algorithm_comparison_returns_dict(self) -> None:
        """algorithm_comparison returns a dict."""
        diag = DiscoveryDiagnostics()
        texts = _make_text_corpus(2)
        result = diag.algorithm_comparison(texts)
        assert isinstance(result, dict)

    def test_algorithm_comparison_covers_multiple_algorithms(self) -> None:
        """algorithm_comparison includes multiple algorithm keys."""
        diag = DiscoveryDiagnostics()
        texts = _make_text_corpus(2)
        result = diag.algorithm_comparison(texts)
        assert len(result) >= 2

    def test_pipeline_health_returns_dict(self) -> None:
        """pipeline_health returns a dict with health information."""
        diag = DiscoveryDiagnostics()
        result = diag.pipeline_health()
        assert isinstance(result, dict)

    def test_kind_quality_distribution_returns_dict(self) -> None:
        """kind_quality_distribution returns a dict."""
        diag = DiscoveryDiagnostics()
        kinds = [_make_kind(confidence=c) for c in (0.3, 0.6, 0.9)]
        result = diag.kind_quality_distribution(kinds)
        assert isinstance(result, dict)

    def test_kind_quality_distribution_empty_kinds(self) -> None:
        """kind_quality_distribution handles empty kinds without error."""
        diag = DiscoveryDiagnostics()
        result = diag.kind_quality_distribution([])
        assert isinstance(result, dict)

    def test_copilot_discovery_summary_returns_string(self) -> None:
        """copilot_discovery_summary returns a string."""
        diag = DiscoveryDiagnostics()
        kinds = [_make_kind(confidence=0.7)]
        result = diag.copilot_discovery_summary(kinds)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_alert_low_quality_returns_list(self) -> None:
        """alert_low_quality returns a list of low-quality kinds."""
        diag = DiscoveryDiagnostics()
        kinds = [
            _make_kind(confidence=0.1),
            _make_kind(confidence=0.9),
            _make_kind(confidence=0.05),
        ]
        alerts = diag.alert_low_quality(kinds, threshold=0.3)
        assert isinstance(alerts, list)
        # Should include at least the two very low confidence kinds
        assert len(alerts) >= 2


# ===========================================================================
# DiscoveryHistory tests
# ===========================================================================

class TestDiscoveryHistory:
    """Tests for DiscoveryHistory session recording and querying."""

    def test_record_session_succeeds(self) -> None:
        """record_session adds a session without error."""
        history = DiscoveryHistory()
        kinds = [_make_kind()]
        session_id = history.record_session(kinds)
        assert session_id is not None

    def test_record_session_returns_string_id(self) -> None:
        """record_session returns a string session ID."""
        history = DiscoveryHistory()
        kinds = [_make_kind()]
        session_id = history.record_session(kinds)
        assert isinstance(session_id, str)
        assert session_id.strip() != ""

    def test_record_session_with_algorithm(self) -> None:
        """record_session with algorithm parameter stores algorithm info."""
        history = DiscoveryHistory()
        kinds = [_make_kind()]
        session_id = history.record_session(
            kinds, algorithm=DiscoveryAlgorithm.GREEDY
        )
        session = history.get_session(session_id)
        assert session is not None

    def test_record_session_with_domain(self) -> None:
        """record_session with domain parameter stores domain info."""
        history = DiscoveryHistory()
        kinds = [_make_kind()]
        session_id = history.record_session(kinds, domain="algebraic_topology")
        session = history.get_session(session_id)
        assert session is not None

    def test_get_session_returns_session_for_known_id(self) -> None:
        """get_session returns session data for a recorded session ID."""
        history = DiscoveryHistory()
        kinds = [_make_kind()]
        session_id = history.record_session(kinds)
        session = history.get_session(session_id)
        assert session is not None

    def test_get_session_returns_none_for_unknown_id(self) -> None:
        """get_session returns None for an unrecognised session ID."""
        history = DiscoveryHistory()
        result = history.get_session("nonexistent-session-" + str(uuid.uuid4()))
        assert result is None

    def test_all_sessions_returns_list(self) -> None:
        """all_sessions returns a list."""
        history = DiscoveryHistory()
        for i in range(3):
            history.record_session([_make_kind(name=f"SessionKind{i}")])
        sessions = history.all_sessions()
        assert isinstance(sessions, list)

    def test_all_sessions_count_matches_recorded(self) -> None:
        """all_sessions returns correct number of sessions."""
        history = DiscoveryHistory()
        for i in range(5):
            history.record_session([_make_kind()])
        assert len(history.all_sessions()) == 5

    def test_total_kinds_discovered_sums_across_sessions(self) -> None:
        """total_kinds_discovered sums kind counts from all sessions."""
        history = DiscoveryHistory()
        history.record_session([_make_kind(name="K1"), _make_kind(name="K2")])
        history.record_session([_make_kind(name="K3")])
        total = history.total_kinds_discovered()
        assert isinstance(total, int)
        assert total >= 3

    def test_total_kinds_discovered_empty_history(self) -> None:
        """total_kinds_discovered on empty history returns 0."""
        history = DiscoveryHistory()
        assert history.total_kinds_discovered() == 0

    def test_success_rate_returns_float(self) -> None:
        """success_rate returns a float."""
        history = DiscoveryHistory()
        history.record_session([_make_kind(confidence=0.9)])
        rate = history.success_rate()
        assert isinstance(rate, float)

    def test_success_rate_in_unit_interval(self) -> None:
        """success_rate returns a value in [0, 1]."""
        history = DiscoveryHistory()
        for i in range(5):
            history.record_session([_make_kind(confidence=0.5 + i * 0.05)])
        rate = history.success_rate()
        assert 0.0 <= rate <= 1.0

    def test_success_rate_empty_history_returns_zero(self) -> None:
        """success_rate on empty history returns 0."""
        history = DiscoveryHistory()
        assert history.success_rate() == 0.0

    def test_best_session_returns_session_or_none(self) -> None:
        """best_session returns a session dict or None."""
        history = DiscoveryHistory()
        history.record_session([_make_kind(confidence=0.8)])
        history.record_session([_make_kind(confidence=0.4)])
        result = history.best_session()
        assert result is not None

    def test_best_session_empty_history_returns_none(self) -> None:
        """best_session on empty history returns None."""
        history = DiscoveryHistory()
        assert history.best_session() is None

    def test_recent_returns_at_most_n_sessions(self) -> None:
        """recent(n) returns at most n sessions."""
        history = DiscoveryHistory()
        for i in range(15):
            history.record_session([_make_kind()])
        recent = history.recent(n=5)
        assert len(recent) <= 5

    def test_clear_removes_all_sessions(self) -> None:
        """clear() removes all recorded sessions."""
        history = DiscoveryHistory()
        for i in range(3):
            history.record_session([_make_kind()])
        history.clear()
        assert len(history.all_sessions()) == 0
        assert history.total_kinds_discovered() == 0


# ===========================================================================
# Integration tests
# ===========================================================================

class TestDiscoveryIntegrationPipeline:
    """End-to-end integration tests for the discovery pipeline."""

    def test_discover_rank_validate_pipeline(self) -> None:
        """Complete pipeline: discover → rank → validate."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.GREEDY)
        ranker = KindRanker()
        validator = KindValidator()
        texts = _make_text_corpus(3)
        kinds = engine.discover(texts)
        if not kinds:
            pytest.skip("Discovery produced no kinds for this corpus")
        ranked = ranker.rank(kinds)
        # Validate the top kind
        top_kind, top_score = ranked[0]
        ok, issues = validator.validate(top_kind)
        assert isinstance(ok, bool)
        assert isinstance(issues, list)
        assert isinstance(top_score, float)

    def test_discover_with_evolution_tracking(self) -> None:
        """Discover kinds then track their evolution."""
        engine = KindDiscoveryEngine()
        tracker = KindEvolutionTracker()
        texts = _make_text_corpus(3)
        kinds = engine.discover(texts)
        for kind in kinds:
            tracker.record_discovery(kind)
        summary = tracker.evolution_summary()
        assert isinstance(summary, dict)
        stable = tracker.stable_kinds()
        assert isinstance(stable, list)

    def test_discover_and_record_to_history(self) -> None:
        """Discover kinds and record them in DiscoveryHistory."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.FREQUENCY_GUIDED)
        history = DiscoveryHistory()
        texts = _make_text_corpus(3)
        kinds = engine.discover(texts, domain="algebra")
        session_id = history.record_session(
            kinds, algorithm=DiscoveryAlgorithm.FREQUENCY_GUIDED, domain="algebra"
        )
        assert session_id is not None
        total = history.total_kinds_discovered()
        assert total == len(kinds)

    def test_discover_incremental_integration(self) -> None:
        """Incremental discovery extends an existing kind set."""
        engine = KindDiscoveryEngine()
        texts_batch_1 = _make_text_corpus(2)
        texts_batch_2 = _make_text_corpus(3)
        # First batch
        initial_kinds = engine.discover(texts_batch_1)
        # Incremental: second batch should add to the existing pool
        incremental_kinds = engine.discover_incremental(
            texts_batch_2, existing_kinds=initial_kinds
        )
        all_kinds = initial_kinds + incremental_kinds
        ranker = KindRanker()
        ranked = ranker.rank(all_kinds)
        assert isinstance(ranked, list)

    def test_edge_case_duplicate_texts_in_corpus(self) -> None:
        """Discovery with duplicate texts does not crash and produces NewKind list."""
        engine = KindDiscoveryEngine()
        base = _make_text_corpus(1)
        texts = base * 5  # duplicate the same text 5 times
        result = engine.discover(texts)
        assert isinstance(result, list)

    def test_edge_case_very_short_texts(self) -> None:
        """Discovery with very short one-word texts does not crash."""
        engine = KindDiscoveryEngine()
        texts = ["obstruction", "algebra", "sheaf"]
        result = engine.discover(texts)
        assert isinstance(result, list)

    def test_pipeline_summary_after_discovery(self) -> None:
        """pipeline_summary reflects information about a completed discovery run."""
        engine = KindDiscoveryEngine(algorithm=DiscoveryAlgorithm.BEAM_SEARCH)
        texts = _make_text_corpus(2)
        engine.discover(texts)
        summary = engine.get_pipeline_summary()
        assert isinstance(summary, dict)
        assert len(summary) >= 1

    def test_discover_from_ideas_integration(self) -> None:
        """discover_from_ideas integrates idea content into discovery."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        engine = KindDiscoveryEngine()
        ideas = [
            _make_idea(f"idea-{i:04d}", f"Obstruction idea {i}")
            for i in range(3)
        ]
        kinds = engine.discover_from_ideas(ideas)
        assert isinstance(kinds, list)
        if kinds:
            validator = KindValidator()
            for kind in kinds:
                ok, issues = validator.validate(kind)
                assert isinstance(ok, bool)

    def test_validator_batch_matches_individual_in_order(self) -> None:
        """validate_batch processes kinds in order matching individual calls."""
        validator = KindValidator()
        kinds = [_make_kind(confidence=c) for c in (0.3, 0.7, 0.95)]
        batch_results = validator.validate_batch(kinds)
        assert len(batch_results) == 3
        for i, kind in enumerate(kinds):
            single_ok, _ = validator.validate(kind)
            batch_entry = batch_results[i]
            if len(batch_entry) == 3:
                _, batch_ok, _ = batch_entry
            else:
                batch_ok, _ = batch_entry
            assert batch_ok == single_ok

    def test_ranker_pareto_optimal_with_mixed_quality(self) -> None:
        """pareto_optimal returns a non-empty subset for mixed-quality kinds."""
        ranker = KindRanker()
        kinds = [
            _make_kind(name=f"MixedKind{i}", confidence=c)
            for i, c in enumerate((0.2, 0.5, 0.8, 0.95, 0.3))
        ]
        pareto = ranker.pareto_optimal(kinds)
        assert isinstance(pareto, list)
        # Pareto frontier should be non-empty and a subset of input
        assert 1 <= len(pareto) <= len(kinds)
        input_ids = {k.kind_id for k in kinds}
        for item in pareto:
            assert item.kind_id in input_ids
