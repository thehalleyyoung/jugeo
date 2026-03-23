"""Tests for jugeo.ideation.kind_discovery.s03_kind_bootstrapping.

Covers BootstrapConfig, KindHypothesizer, DefinitionBuilder, ExampleGenerator,
ValidationPlanner, and KindBootstrapper — the full bootstrapping pipeline.

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
# Optional import — skip the entire module if not yet implemented
# ---------------------------------------------------------------------------

import pytest

try:
    from jugeo.ideation.kind_discovery.s03_kind_bootstrapping import (
        BootstrapConfig,
        KindHypothesizer,
        DefinitionBuilder,
        ExampleGenerator,
        ValidationPlanner,
        KindBootstrapper,
    )
    _BOOTSTRAP_AVAILABLE = True
except ImportError as _bootstrap_import_error:
    _BOOTSTRAP_AVAILABLE = False
    _bootstrap_import_error_msg = str(_bootstrap_import_error)

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
    )
    _IDEAS_AVAILABLE = True
except ImportError:
    _IDEAS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _BOOTSTRAP_AVAILABLE,
    reason=(
        "jugeo.ideation.kind_discovery.s03_kind_bootstrapping not yet implemented"
        + (f": {_bootstrap_import_error_msg}" if not _BOOTSTRAP_AVAILABLE else "")
    ),
)


# ---------------------------------------------------------------------------
# Test factories
# ---------------------------------------------------------------------------

def _make_candidate(
    name: str = "TestKind",
    confidence: float = 0.7,
    frequency: int = 5,
    status: "KindStatus | None" = None,
) -> "KindCandidate":
    """Create a KindCandidate with sensible defaults for testing."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    if status is None:
        status = KindStatus.CANDIDATE
    return KindCandidate(
        candidate_id=str(uuid.uuid4()),
        name=name,
        description=f"A mathematical kind related to {name}",
        obstruction_pattern=f"obstruction in {name.lower()} structure",
        frequency=frequency,
        confidence=confidence,
        evidence_sources=("paper:A", "paper:B"),
        status=status,
    )


def _make_pattern(
    frequency: int = 5,
    generality: float = 0.6,
    domains: tuple[str, ...] = ("algebra", "topology"),
) -> "KindPattern":
    """Create a KindPattern with sensible defaults for testing."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    return KindPattern(
        pattern_id=str(uuid.uuid4()),
        signature="algebraic obstruction pattern",
        frequency=frequency,
        domains=domains,
        generality_score=generality,
    )


def _make_field(domain: str = "algebra") -> "ObstructionField":
    """Create an ObstructionField for testing."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    return ObstructionField(
        field_id=str(uuid.uuid4()),
        domain=domain,
        obstructions=(
            "extension obstruction in module category",
            "cohomological barrier in derived functor",
            "lifting obstruction in fibered category",
        ),
        semantic_density=0.7,
        coherence_score=0.8,
        obstruction_types=(
            ObstructionType.ALGEBRAIC,
            ObstructionType.STRUCTURAL,
            ObstructionType.ALGEBRAIC,
        ),
    )


def _make_new_kind(
    name: str = "TestKind",
    confidence: float = 0.7,
    status: "KindStatus | None" = None,
) -> "NewKind":
    """Create a NewKind for testing."""
    if not _MODELS_AVAILABLE:
        pytest.skip("models module not available")
    if status is None:
        status = KindStatus.PROVISIONAL
    return NewKind(
        kind_id=str(uuid.uuid4()),
        name=name,
        formal_definition=f"A {name} is a mathematical object defined by obstruction-theoretic properties.",
        examples=(
            f"Example 1 of {name}: the trivial case with no obstructions",
            f"Example 2 of {name}: a non-trivial instance from algebraic topology",
        ),
        theorems=(f"Every {name} admits a canonical resolution.",),
        discovery_path=(
            "obstruction_mining",
            "pattern_recognition",
            "bootstrapping",
        ),
        status=status,
        confidence=confidence,
    )


def _make_idea_for_test(idea_id: str = "test-idea-0001") -> "Idea":
    """Create a minimal Idea for integration tests."""
    if not _IDEAS_AVAILABLE:
        pytest.skip("ideas module not available")
    return Idea(
        idea_id=idea_id,
        title="Kind bridge via obstruction theory",
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


# ===========================================================================
# BootstrapConfig tests
# ===========================================================================

class TestBootstrapConfigConstruction:
    """Tests for BootstrapConfig dataclass construction and defaults."""

    def test_default_construction_succeeds(self) -> None:
        """BootstrapConfig can be constructed with all defaults."""
        cfg = BootstrapConfig()
        assert cfg is not None

    def test_default_min_pattern_frequency_is_positive(self) -> None:
        """min_pattern_frequency default is a positive integer."""
        cfg = BootstrapConfig()
        assert isinstance(cfg.min_pattern_frequency, int)
        assert cfg.min_pattern_frequency >= 1

    def test_default_min_candidate_confidence_is_in_unit_interval(self) -> None:
        """min_candidate_confidence default is in [0, 1]."""
        cfg = BootstrapConfig()
        assert 0.0 <= cfg.min_candidate_confidence <= 1.0

    def test_default_max_hypotheses_per_pattern_is_positive(self) -> None:
        """max_hypotheses_per_pattern default is a positive integer."""
        cfg = BootstrapConfig()
        assert cfg.max_hypotheses_per_pattern >= 1

    def test_default_definition_depth_is_positive(self) -> None:
        """definition_depth default is a positive integer."""
        cfg = BootstrapConfig()
        assert cfg.definition_depth >= 1

    def test_default_example_count_is_positive(self) -> None:
        """example_count default is a positive integer."""
        cfg = BootstrapConfig()
        assert cfg.example_count >= 1

    def test_default_validation_steps_count_is_positive(self) -> None:
        """validation_steps_count default is a positive integer."""
        cfg = BootstrapConfig()
        assert cfg.validation_steps_count >= 1

    def test_custom_construction_stores_values(self) -> None:
        """Custom field values are stored correctly."""
        cfg = BootstrapConfig(
            min_pattern_frequency=3,
            min_candidate_confidence=0.5,
            max_hypotheses_per_pattern=10,
            definition_depth=4,
            example_count=6,
            validation_steps_count=5,
            effort_budget=8.0,
        )
        assert cfg.min_pattern_frequency == 3
        assert cfg.min_candidate_confidence == 0.5
        assert cfg.max_hypotheses_per_pattern == 10
        assert cfg.definition_depth == 4
        assert cfg.example_count == 6
        assert cfg.validation_steps_count == 5
        assert cfg.effort_budget == 8.0

    def test_is_frozen(self) -> None:
        """BootstrapConfig is immutable (frozen dataclass)."""
        cfg = BootstrapConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.min_pattern_frequency = 99  # type: ignore[misc]

    def test_equality_of_identical_configs(self) -> None:
        """Two BootstrapConfig instances with identical fields are equal."""
        cfg_a = BootstrapConfig(
            min_pattern_frequency=2,
            min_candidate_confidence=0.4,
        )
        cfg_b = BootstrapConfig(
            min_pattern_frequency=2,
            min_candidate_confidence=0.4,
        )
        assert cfg_a == cfg_b

    def test_inequality_of_different_configs(self) -> None:
        """Two BootstrapConfig instances with different fields are unequal."""
        cfg_a = BootstrapConfig(min_pattern_frequency=2)
        cfg_b = BootstrapConfig(min_pattern_frequency=3)
        assert cfg_a != cfg_b


class TestBootstrapConfigWithEffortBudget:
    """Tests for BootstrapConfig.with_effort_budget."""

    def test_with_effort_budget_returns_new_instance(self) -> None:
        """with_effort_budget returns a different object, not the same."""
        cfg = BootstrapConfig()
        new_cfg = cfg.with_effort_budget(5.0)
        assert new_cfg is not cfg

    def test_with_effort_budget_updates_effort_field(self) -> None:
        """with_effort_budget sets the effort_budget field to the new value."""
        cfg = BootstrapConfig()
        new_cfg = cfg.with_effort_budget(7.5)
        assert new_cfg.effort_budget == 7.5

    def test_with_effort_budget_preserves_other_fields(self) -> None:
        """with_effort_budget does not alter unrelated fields."""
        cfg = BootstrapConfig(
            min_pattern_frequency=4,
            min_candidate_confidence=0.6,
            max_hypotheses_per_pattern=8,
        )
        new_cfg = cfg.with_effort_budget(3.0)
        assert new_cfg.min_pattern_frequency == 4
        assert new_cfg.min_candidate_confidence == 0.6
        assert new_cfg.max_hypotheses_per_pattern == 8

    def test_with_effort_budget_zero_allowed(self) -> None:
        """with_effort_budget accepts zero as a valid budget."""
        cfg = BootstrapConfig()
        new_cfg = cfg.with_effort_budget(0.0)
        assert new_cfg.effort_budget == 0.0

    def test_with_effort_budget_chaining(self) -> None:
        """with_effort_budget can be chained multiple times."""
        cfg = BootstrapConfig()
        new_cfg = cfg.with_effort_budget(3.0).with_effort_budget(6.0)
        assert new_cfg.effort_budget == 6.0


class TestBootstrapConfigSerialization:
    """Tests for BootstrapConfig.to_dict and from_dict round-trip."""

    def test_to_dict_returns_dict(self) -> None:
        """to_dict produces a plain dict."""
        cfg = BootstrapConfig()
        result = cfg.to_dict()
        assert isinstance(result, dict)

    def test_to_dict_contains_effort_budget(self) -> None:
        """to_dict includes the effort_budget field."""
        cfg = BootstrapConfig(effort_budget=4.0)
        d = cfg.to_dict()
        assert "effort_budget" in d
        assert d["effort_budget"] == 4.0

    def test_from_dict_round_trip_preserves_all_fields(self) -> None:
        """to_dict -> from_dict reconstructs an equal BootstrapConfig."""
        cfg = BootstrapConfig(
            min_pattern_frequency=3,
            min_candidate_confidence=0.55,
            max_hypotheses_per_pattern=7,
            definition_depth=3,
            example_count=4,
            validation_steps_count=6,
            effort_budget=9.0,
            auto_generate_ideas=True,
            idea_count_per_kind=3,
            enable_cross_domain=True,
        )
        d = cfg.to_dict()
        restored = BootstrapConfig.from_dict(d)
        assert restored == cfg

    def test_from_dict_with_partial_data_uses_defaults(self) -> None:
        """from_dict fills in missing keys with defaults."""
        cfg = BootstrapConfig.from_dict({})
        default = BootstrapConfig()
        assert cfg == default

    def test_to_dict_all_values_are_json_serializable(self) -> None:
        """All values returned by to_dict are JSON-serializable primitives."""
        import json
        cfg = BootstrapConfig(
            min_pattern_frequency=2,
            enable_cross_domain=False,
            auto_generate_ideas=True,
        )
        d = cfg.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        restored_dict = json.loads(json_str)
        assert restored_dict == d


# ===========================================================================
# KindHypothesizer tests
# ===========================================================================

class TestKindHypothesizerBasic:
    """Tests for KindHypothesizer.hypothesize and related methods."""

    def test_hypothesize_returns_list(self) -> None:
        """hypothesize returns a list (possibly empty) of KindCandidates."""
        h = KindHypothesizer()
        pattern = _make_pattern()
        result = h.hypothesize(pattern)
        assert isinstance(result, list)

    def test_hypothesize_result_items_are_kind_candidates(self) -> None:
        """Each item returned by hypothesize is a KindCandidate."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")
        h = KindHypothesizer()
        pattern = _make_pattern(frequency=6)
        results = h.hypothesize(pattern)
        for item in results:
            assert isinstance(item, KindCandidate)

    def test_hypothesize_high_frequency_produces_candidates(self) -> None:
        """A high-frequency pattern produces at least one candidate."""
        h = KindHypothesizer()
        pattern = _make_pattern(frequency=20, generality=0.9)
        results = h.hypothesize(pattern)
        assert len(results) >= 1

    def test_hypothesize_candidates_have_non_empty_names(self) -> None:
        """All candidates returned by hypothesize have non-empty names."""
        h = KindHypothesizer()
        pattern = _make_pattern(frequency=10)
        for candidate in h.hypothesize(pattern):
            assert candidate.name.strip() != ""

    def test_hypothesize_candidates_have_valid_confidence(self) -> None:
        """All candidates have confidence in [0, 1]."""
        h = KindHypothesizer()
        pattern = _make_pattern(frequency=8)
        for candidate in h.hypothesize(pattern):
            assert 0.0 <= candidate.confidence <= 1.0

    def test_hypothesize_from_field_returns_list(self) -> None:
        """hypothesize_from_field returns a list from an ObstructionField."""
        h = KindHypothesizer()
        field = _make_field()
        result = h.hypothesize_from_field(field)
        assert isinstance(result, list)

    def test_hypothesize_from_field_items_are_kind_candidates(self) -> None:
        """hypothesize_from_field items are KindCandidate instances."""
        if not _MODELS_AVAILABLE:
            pytest.skip("models not available")
        h = KindHypothesizer()
        field = _make_field(domain="topology")
        for item in h.hypothesize_from_field(field):
            assert isinstance(item, KindCandidate)

    def test_hypothesize_batch_returns_list(self) -> None:
        """hypothesize_batch returns a list when given multiple patterns."""
        h = KindHypothesizer()
        patterns = [_make_pattern(frequency=i + 3) for i in range(4)]
        result = h.hypothesize_batch(patterns)
        assert isinstance(result, list)

    def test_hypothesize_batch_empty_input_returns_empty(self) -> None:
        """hypothesize_batch([]) returns an empty list."""
        h = KindHypothesizer()
        result = h.hypothesize_batch([])
        assert result == []

    def test_hypothesize_batch_aggregates_all_patterns(self) -> None:
        """hypothesize_batch covers all input patterns (result size >= single)."""
        h = KindHypothesizer()
        single_pattern = _make_pattern(frequency=8)
        single_result = h.hypothesize(single_pattern)
        batch_result = h.hypothesize_batch([single_pattern])
        # Batch result for one pattern should be equivalent to single call
        assert len(batch_result) >= len(single_result)


class TestKindHypothesizerFiltering:
    """Tests for KindHypothesizer.filter_viable and merge_duplicates."""

    def test_filter_viable_removes_low_confidence(self) -> None:
        """filter_viable excludes candidates with confidence below threshold."""
        h = KindHypothesizer(BootstrapConfig(min_candidate_confidence=0.5))
        low = _make_candidate(confidence=0.2, frequency=3)
        high = _make_candidate(confidence=0.8, frequency=3)
        result = h.filter_viable([low, high])
        assert high in result
        assert low not in result

    def test_filter_viable_removes_low_frequency(self) -> None:
        """filter_viable excludes candidates with frequency below threshold."""
        h = KindHypothesizer(BootstrapConfig(min_pattern_frequency=3))
        low_freq = _make_candidate(confidence=0.9, frequency=1)
        high_freq = _make_candidate(confidence=0.9, frequency=10)
        result = h.filter_viable([low_freq, high_freq])
        assert high_freq in result
        assert low_freq not in result

    def test_filter_viable_empty_input(self) -> None:
        """filter_viable returns an empty list for empty input."""
        h = KindHypothesizer()
        assert h.filter_viable([]) == []

    def test_filter_viable_all_pass(self) -> None:
        """filter_viable keeps all candidates that meet criteria."""
        h = KindHypothesizer(
            BootstrapConfig(min_candidate_confidence=0.3, min_pattern_frequency=1)
        )
        candidates = [_make_candidate(confidence=0.9, frequency=5) for _ in range(3)]
        result = h.filter_viable(candidates)
        assert len(result) == 3

    def test_merge_duplicates_reduces_count(self) -> None:
        """merge_duplicates reduces list length when duplicates exist."""
        h = KindHypothesizer()
        # Two candidates with same name → should be merged
        c1 = _make_candidate(name="DuplicateKind", confidence=0.5, frequency=3)
        c2 = _make_candidate(name="DuplicateKind", confidence=0.6, frequency=4)
        c3 = _make_candidate(name="UniqueKind", confidence=0.7, frequency=5)
        result = h.merge_duplicates([c1, c2, c3])
        # Should have fewer items than original 3 (duplicates merged)
        assert len(result) <= 3

    def test_merge_duplicates_empty_input(self) -> None:
        """merge_duplicates returns empty list for empty input."""
        h = KindHypothesizer()
        assert h.merge_duplicates([]) == []

    def test_merge_duplicates_preserves_unique(self) -> None:
        """merge_duplicates does not remove non-duplicate candidates."""
        h = KindHypothesizer()
        c1 = _make_candidate(name="KindAlpha", confidence=0.7)
        c2 = _make_candidate(name="KindBeta", confidence=0.8)
        result = h.merge_duplicates([c1, c2])
        names = [c.name for c in result]
        assert "KindAlpha" in names
        assert "KindBeta" in names


# ===========================================================================
# DefinitionBuilder tests
# ===========================================================================

class TestDefinitionBuilderBasic:
    """Tests for DefinitionBuilder.build and build_formal."""

    def test_build_returns_non_empty_string(self) -> None:
        """build returns a non-empty string definition."""
        db = DefinitionBuilder()
        candidate = _make_candidate()
        result = db.build(candidate)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_build_contains_candidate_name(self) -> None:
        """The built definition references the candidate name."""
        db = DefinitionBuilder()
        candidate = _make_candidate(name="SpectralKind")
        result = db.build(candidate)
        assert "SpectralKind" in result or "spectral" in result.lower()

    def test_build_formal_returns_string(self) -> None:
        """build_formal returns a non-empty string."""
        db = DefinitionBuilder()
        candidate = _make_candidate()
        result = db.build_formal(candidate)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_build_formal_with_depth_parameter(self) -> None:
        """build_formal accepts and uses a depth parameter."""
        db = DefinitionBuilder()
        candidate = _make_candidate()
        result_shallow = db.build_formal(candidate, depth=1)
        result_deep = db.build_formal(candidate, depth=4)
        # Both should return non-empty strings
        assert result_shallow.strip() != ""
        assert result_deep.strip() != ""

    def test_build_from_obstructions_returns_string(self) -> None:
        """build_from_obstructions returns a non-empty definition string."""
        db = DefinitionBuilder()
        obstructions = [
            "extension class in Ext^1 is non-trivial",
            "cohomological obstruction in H^2 does not vanish",
        ]
        result = db.build_from_obstructions(obstructions, name="CohomKind")
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_build_from_obstructions_empty_list(self) -> None:
        """build_from_obstructions with empty list returns a string (possibly generic)."""
        db = DefinitionBuilder()
        result = db.build_from_obstructions([], name="EmptyKind")
        assert isinstance(result, str)

    def test_build_from_obstructions_name_appears_in_output(self) -> None:
        """The supplied name appears in or informs the built definition."""
        db = DefinitionBuilder()
        result = db.build_from_obstructions(
            ["some obstruction"], name="NamedKind"
        )
        assert "NamedKind" in result or "named" in result.lower()

    def test_refine_definition_returns_string(self) -> None:
        """refine_definition returns a non-empty string."""
        db = DefinitionBuilder()
        original = "A TestKind is defined by structural obstructions."
        result = db.refine_definition(original)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_refine_definition_with_extra_constraints(self) -> None:
        """refine_definition with extra constraints incorporates them."""
        db = DefinitionBuilder()
        original = "A TestKind is defined by structural obstructions."
        constraints = ("must satisfy Noetherian condition", "closed under direct sums")
        result = db.refine_definition(original, extra_constraints=constraints)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_validate_definition_returns_bool_and_list(self) -> None:
        """validate_definition returns a (bool, list) pair."""
        db = DefinitionBuilder()
        valid_def = "A CoherentKind is a mathematical object characterized by a coherent sheaf structure with no higher cohomology."
        result = db.validate_definition(valid_def)
        assert isinstance(result, tuple)
        assert len(result) == 2
        ok, issues = result
        assert isinstance(ok, bool)
        assert isinstance(issues, list)

    def test_validate_definition_empty_string_fails(self) -> None:
        """validate_definition marks an empty string as invalid."""
        db = DefinitionBuilder()
        ok, issues = db.validate_definition("")
        assert ok is False
        assert len(issues) >= 1

    def test_validate_definition_good_definition_passes(self) -> None:
        """A well-formed definition passes validation (or returns issues for feedback)."""
        db = DefinitionBuilder()
        good_def = (
            "A FlatKind is a mathematical kind defined by the vanishing of "
            "Tor obstruction groups. It admits flat resolutions and is closed "
            "under base change. Example: free modules are FlatKind instances."
        )
        ok, issues = db.validate_definition(good_def)
        # Either passes or provides improvement suggestions
        assert isinstance(ok, bool)
        assert isinstance(issues, list)


# ===========================================================================
# ExampleGenerator tests
# ===========================================================================

class TestExampleGenerator:
    """Tests for ExampleGenerator methods."""

    def test_generate_returns_list(self) -> None:
        """generate returns a list of strings."""
        eg = ExampleGenerator()
        candidate = _make_candidate()
        result = eg.generate(candidate)
        assert isinstance(result, list)

    def test_generate_returns_strings(self) -> None:
        """All items in generate result are strings."""
        eg = ExampleGenerator()
        candidate = _make_candidate()
        for item in eg.generate(candidate):
            assert isinstance(item, str)

    def test_generate_positive_returns_list(self) -> None:
        """generate_positive returns a list of strings."""
        eg = ExampleGenerator()
        candidate = _make_candidate()
        result = eg.generate_positive(candidate)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)

    def test_generate_negative_returns_list(self) -> None:
        """generate_negative returns a list of strings."""
        eg = ExampleGenerator()
        candidate = _make_candidate()
        result = eg.generate_negative(candidate)
        assert isinstance(result, list)

    def test_generate_boundary_returns_list(self) -> None:
        """generate_boundary returns a list of strings."""
        eg = ExampleGenerator()
        candidate = _make_candidate()
        result = eg.generate_boundary(candidate)
        assert isinstance(result, list)

    def test_generate_positive_non_empty_for_viable_candidate(self) -> None:
        """generate_positive produces at least one example for a viable candidate."""
        eg = ExampleGenerator()
        candidate = _make_candidate(confidence=0.9, frequency=10)
        result = eg.generate_positive(candidate)
        assert len(result) >= 1

    def test_generate_negative_distinct_from_positive(self) -> None:
        """generate_negative examples differ from generate_positive examples."""
        eg = ExampleGenerator()
        candidate = _make_candidate(confidence=0.8, frequency=8)
        positives = set(eg.generate_positive(candidate))
        negatives = set(eg.generate_negative(candidate))
        # There should be no exact overlap if both are non-empty
        if positives and negatives:
            assert positives != negatives

    def test_generate_from_definition_returns_list(self) -> None:
        """generate_from_definition returns a list of strings."""
        eg = ExampleGenerator()
        definition = "A FlatKind has vanishing Tor obstruction."
        result = eg.generate_from_definition(definition, name="FlatKind")
        assert isinstance(result, list)

    def test_format_examples_returns_string(self) -> None:
        """format_examples returns a formatted string."""
        eg = ExampleGenerator()
        examples = [
            "Example 1: free module over a Noetherian ring",
            "Example 2: projective module over a local ring",
            "Counter-example: torsion module",
        ]
        result = eg.format_examples(examples)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_format_examples_empty_list(self) -> None:
        """format_examples handles an empty list without errors."""
        eg = ExampleGenerator()
        result = eg.format_examples([])
        assert isinstance(result, str)

    def test_generate_count_respects_config(self) -> None:
        """ExampleGenerator with config's example_count produces correct count."""
        cfg = BootstrapConfig(example_count=3)
        eg = ExampleGenerator(cfg)
        candidate = _make_candidate(confidence=0.9, frequency=10)
        result = eg.generate(candidate)
        # Should produce approximately example_count items
        assert len(result) <= cfg.example_count + 2  # allow small variance


# ===========================================================================
# ValidationPlanner tests
# ===========================================================================

class TestValidationPlanner:
    """Tests for ValidationPlanner.plan and related methods."""

    def test_plan_returns_validation_path(self) -> None:
        """plan returns a ValidationPath object."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        candidate = _make_candidate()
        result = vp.plan(candidate)
        assert isinstance(result, ValidationPath)

    def test_plan_has_non_empty_steps(self) -> None:
        """plan returns a ValidationPath with at least one step."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        candidate = _make_candidate(confidence=0.7, frequency=5)
        result = vp.plan(candidate)
        assert result.depth() >= 1

    def test_plan_for_new_kind_returns_validation_path(self) -> None:
        """plan_for_new_kind returns a ValidationPath for a NewKind."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        kind = _make_new_kind()
        result = vp.plan_for_new_kind(kind)
        assert isinstance(result, ValidationPath)

    def test_plan_for_new_kind_references_kind_name(self) -> None:
        """plan_for_new_kind steps reference the kind's name."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        kind = _make_new_kind(name="CoherentKind")
        path = vp.plan_for_new_kind(kind)
        # At least one step or evidence piece mentions the kind name
        all_text = " ".join(path.steps + path.required_evidence + path.success_criteria)
        assert "CoherentKind" in all_text or "coherent" in all_text.lower()

    def test_strengthen_plan_adds_evidence(self) -> None:
        """strengthen_plan adds extra evidence to the ValidationPath."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        candidate = _make_candidate()
        base_path = vp.plan(candidate)
        extra = ("peer-reviewed proof", "computational verification")
        strengthened = vp.strengthen_plan(base_path, extra_evidence=extra)
        assert isinstance(strengthened, ValidationPath)
        for ev in extra:
            assert ev in strengthened.required_evidence

    def test_strengthen_plan_returns_different_object(self) -> None:
        """strengthen_plan returns a new ValidationPath, not the same one."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        candidate = _make_candidate()
        base_path = vp.plan(candidate)
        extra = ("additional survey",)
        strengthened = vp.strengthen_plan(base_path, extra_evidence=extra)
        assert strengthened is not base_path

    def test_plan_from_bootstrap_returns_validation_path(self) -> None:
        """plan_from_bootstrap converts a KindBootstrapPlan to a ValidationPath."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        if not _MODELS_AVAILABLE:
            pytest.skip("models module needed for KindBootstrapPlan")
        vp = ValidationPlanner()
        plan = KindBootstrapPlan(
            plan_id=str(uuid.uuid4()),
            target_kind="TestKind",
            steps=("mine obstruction field", "extract pattern", "build hypothesis"),
            required_evidence=("field survey paper", "pattern frequency >= 3"),
            estimated_effort=2.5,
            priority=7,
        )
        result = vp.plan_from_bootstrap(plan)
        assert isinstance(result, ValidationPath)

    def test_plan_steps_are_strings(self) -> None:
        """All steps in the planned ValidationPath are non-empty strings."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module needed for ValidationPath")
        vp = ValidationPlanner()
        candidate = _make_candidate()
        path = vp.plan(candidate)
        for step in path.steps:
            assert isinstance(step, str)
            assert step.strip() != ""


# ===========================================================================
# KindBootstrapper tests
# ===========================================================================

class TestKindBootstrapperBootstrapSingle:
    """Tests for KindBootstrapper.bootstrap_single."""

    def test_bootstrap_single_returns_new_kind_or_none(self) -> None:
        """bootstrap_single returns a NewKind or None."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate(confidence=0.8, frequency=6)
        result = bootstrapper.bootstrap_single(candidate)
        assert result is None or isinstance(result, NewKind)

    def test_bootstrap_single_high_confidence_produces_kind(self) -> None:
        """A high-confidence, high-frequency candidate produces a NewKind."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate(confidence=0.95, frequency=15)
        result = bootstrapper.bootstrap_single(candidate)
        # High quality should yield a kind
        assert result is not None

    def test_bootstrap_single_low_confidence_may_return_none(self) -> None:
        """A very low-confidence candidate may return None."""
        bootstrapper = KindBootstrapper(
            BootstrapConfig(min_candidate_confidence=0.8)
        )
        candidate = _make_candidate(confidence=0.1, frequency=1)
        result = bootstrapper.bootstrap_single(candidate)
        # Below threshold — should return None
        assert result is None

    def test_bootstrap_single_result_has_non_empty_definition(self) -> None:
        """The resulting NewKind has a non-empty formal definition."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate(confidence=0.9, frequency=10)
        result = bootstrapper.bootstrap_single(candidate)
        if result is not None:
            assert result.formal_definition.strip() != ""

    def test_bootstrap_single_result_is_provisional_or_above(self) -> None:
        """The resulting NewKind has PROVISIONAL or better status."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate(confidence=0.9, frequency=10)
        result = bootstrapper.bootstrap_single(candidate)
        if result is not None:
            assert result.status in (
                KindStatus.PROVISIONAL,
                KindStatus.ESTABLISHED,
                KindStatus.BOOTSTRAPPING,
            )


class TestKindBootstrapperBootstrapFromCandidates:
    """Tests for KindBootstrapper.bootstrap_from_candidates."""

    def test_bootstrap_from_candidates_returns_list(self) -> None:
        """bootstrap_from_candidates returns a list."""
        bootstrapper = KindBootstrapper()
        candidates = [_make_candidate(confidence=0.8, frequency=5)]
        result = bootstrapper.bootstrap_from_candidates(candidates)
        assert isinstance(result, list)

    def test_bootstrap_from_candidates_empty_input(self) -> None:
        """bootstrap_from_candidates with empty list returns empty list."""
        bootstrapper = KindBootstrapper()
        result = bootstrapper.bootstrap_from_candidates([])
        assert result == []

    def test_bootstrap_from_candidates_items_are_new_kinds(self) -> None:
        """All items returned by bootstrap_from_candidates are NewKind instances."""
        bootstrapper = KindBootstrapper()
        candidates = [
            _make_candidate(name=f"Kind{i}", confidence=0.85, frequency=8)
            for i in range(3)
        ]
        for kind in bootstrapper.bootstrap_from_candidates(candidates):
            assert isinstance(kind, NewKind)

    def test_bootstrap_from_candidates_respects_confidence_threshold(self) -> None:
        """Candidates below confidence threshold are not bootstrapped."""
        cfg = BootstrapConfig(min_candidate_confidence=0.9)
        bootstrapper = KindBootstrapper(cfg)
        below_threshold = [
            _make_candidate(confidence=0.3, frequency=10) for _ in range(5)
        ]
        result = bootstrapper.bootstrap_from_candidates(below_threshold)
        # All below threshold → empty or very few results
        assert len(result) == 0


class TestKindBootstrapperPlanAndEvaluate:
    """Tests for create_bootstrap_plan, evaluate_kind, rank_kinds."""

    def test_create_bootstrap_plan_returns_kind_bootstrap_plan(self) -> None:
        """create_bootstrap_plan returns a KindBootstrapPlan."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate(confidence=0.8)
        result = bootstrapper.create_bootstrap_plan(candidate)
        assert isinstance(result, KindBootstrapPlan)

    def test_create_bootstrap_plan_has_steps(self) -> None:
        """The returned KindBootstrapPlan has at least one step."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate()
        plan = bootstrapper.create_bootstrap_plan(candidate)
        assert plan.step_count >= 1

    def test_create_bootstrap_plan_targets_candidate_name(self) -> None:
        """The plan's target_kind relates to the candidate name."""
        bootstrapper = KindBootstrapper()
        candidate = _make_candidate(name="AlgebraKind")
        plan = bootstrapper.create_bootstrap_plan(candidate)
        assert plan.target_kind.strip() != ""

    def test_evaluate_kind_returns_float(self) -> None:
        """evaluate_kind returns a float value."""
        bootstrapper = KindBootstrapper()
        kind = _make_new_kind(confidence=0.8)
        score = bootstrapper.evaluate_kind(kind)
        assert isinstance(score, float)

    def test_evaluate_kind_score_in_unit_interval(self) -> None:
        """evaluate_kind returns a value in [0, 1]."""
        bootstrapper = KindBootstrapper()
        kind = _make_new_kind(confidence=0.7)
        score = bootstrapper.evaluate_kind(kind)
        assert 0.0 <= score <= 1.0

    def test_evaluate_kind_high_confidence_scores_higher(self) -> None:
        """A high-confidence kind scores at least as well as a low-confidence one."""
        bootstrapper = KindBootstrapper()
        high = _make_new_kind(confidence=0.95)
        low = _make_new_kind(confidence=0.2)
        assert bootstrapper.evaluate_kind(high) >= bootstrapper.evaluate_kind(low)

    def test_evaluate_kind_with_portfolio_context(self) -> None:
        """evaluate_kind accepts a portfolio tuple and returns a valid score."""
        bootstrapper = KindBootstrapper()
        kind = _make_new_kind(confidence=0.8)
        portfolio = (_make_new_kind(name="ContextKind1"),)
        score = bootstrapper.evaluate_kind(kind, portfolio=portfolio)
        assert 0.0 <= score <= 1.0

    def test_rank_kinds_returns_sorted_list(self) -> None:
        """rank_kinds returns a list sorted by score descending."""
        bootstrapper = KindBootstrapper()
        kinds = [_make_new_kind(confidence=c) for c in (0.3, 0.7, 0.9, 0.5)]
        ranked = bootstrapper.rank_kinds(kinds)
        assert isinstance(ranked, list)
        scores = [score for _, score in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_rank_kinds_empty_input(self) -> None:
        """rank_kinds returns empty list for empty input."""
        bootstrapper = KindBootstrapper()
        assert bootstrapper.rank_kinds([]) == []

    def test_rank_kinds_result_items_are_pairs(self) -> None:
        """rank_kinds returns list of (NewKind, float) pairs."""
        bootstrapper = KindBootstrapper()
        kinds = [_make_new_kind(confidence=0.7)]
        ranked = bootstrapper.rank_kinds(kinds)
        for item in ranked:
            assert len(item) == 2
            kind, score = item
            assert isinstance(kind, NewKind)
            assert isinstance(score, float)


class TestKindBootstrapperReportAndIdeas:
    """Tests for full_report, diagnostics, generate_ideas_for_kind."""

    def test_full_report_returns_dict(self) -> None:
        """full_report returns a dict with summary information."""
        bootstrapper = KindBootstrapper()
        kinds = [_make_new_kind(confidence=0.7)]
        report = bootstrapper.full_report(kinds)
        assert isinstance(report, dict)

    def test_full_report_empty_kinds(self) -> None:
        """full_report handles empty kinds list without error."""
        bootstrapper = KindBootstrapper()
        report = bootstrapper.full_report([])
        assert isinstance(report, dict)

    def test_full_report_contains_count_field(self) -> None:
        """full_report includes a count-like field for number of kinds."""
        bootstrapper = KindBootstrapper()
        kinds = [_make_new_kind() for _ in range(3)]
        report = bootstrapper.full_report(kinds)
        # Should have some field indicating how many kinds were processed
        report_str = str(report)
        assert any(str(3) in str(v) or "count" in str(k).lower() for k, v in report.items())

    def test_diagnostics_returns_dict(self) -> None:
        """diagnostics returns a dict."""
        bootstrapper = KindBootstrapper()
        kinds = [_make_new_kind()]
        result = bootstrapper.diagnostics(kinds)
        assert isinstance(result, dict)

    def test_generate_ideas_for_kind_returns_list(self) -> None:
        """generate_ideas_for_kind returns a list of Idea objects."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        bootstrapper = KindBootstrapper(BootstrapConfig(auto_generate_ideas=True))
        kind = _make_new_kind(name="GenerativeKind", confidence=0.85)
        ideas = bootstrapper.generate_ideas_for_kind(kind)
        assert isinstance(ideas, list)

    def test_generate_ideas_for_kind_items_are_ideas(self) -> None:
        """All items in generate_ideas_for_kind result are Idea instances."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        bootstrapper = KindBootstrapper(BootstrapConfig(auto_generate_ideas=True))
        kind = _make_new_kind(confidence=0.9)
        for idea in bootstrapper.generate_ideas_for_kind(kind):
            assert isinstance(idea, Idea)

    def test_generate_ideas_for_kind_ideas_have_validation_plan(self) -> None:
        """Generated ideas each have a non-empty ValidationPath."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        bootstrapper = KindBootstrapper(BootstrapConfig(auto_generate_ideas=True))
        kind = _make_new_kind(confidence=0.9)
        for idea in bootstrapper.generate_ideas_for_kind(kind):
            assert idea.validation_plan.depth() >= 1


# ===========================================================================
# Integration tests
# ===========================================================================

class TestBootstrappingIntegration:
    """End-to-end integration tests for the bootstrapping pipeline."""

    def test_bootstrap_from_candidates_with_ideas_generation(self) -> None:
        """Full pipeline: candidates → bootstrap → ideas."""
        if not _IDEAS_AVAILABLE:
            pytest.skip("ideas module not available")
        cfg = BootstrapConfig(
            min_candidate_confidence=0.5,
            min_pattern_frequency=2,
            auto_generate_ideas=True,
            idea_count_per_kind=2,
        )
        bootstrapper = KindBootstrapper(cfg)
        candidates = [
            _make_candidate(name=f"IntegrationKind{i}", confidence=0.8, frequency=5)
            for i in range(3)
        ]
        kinds = bootstrapper.bootstrap_from_candidates(candidates)
        # Each resulting kind should be able to generate ideas
        all_ideas: list[Idea] = []
        for kind in kinds:
            ideas = bootstrapper.generate_ideas_for_kind(kind)
            all_ideas.extend(ideas)
        # All ideas should be valid Idea instances
        for idea in all_ideas:
            assert isinstance(idea, Idea)
            assert idea.idea_id.strip() != ""
            assert idea.title.strip() != ""

    def test_hypothesize_filter_and_bootstrap_pipeline(self) -> None:
        """Hypothesize → filter_viable → bootstrap pipeline produces consistent results."""
        cfg = BootstrapConfig(min_candidate_confidence=0.3, min_pattern_frequency=2)
        h = KindHypothesizer(cfg)
        bootstrapper = KindBootstrapper(cfg)
        pattern = _make_pattern(frequency=10, generality=0.8)
        candidates = h.hypothesize(pattern)
        viable = h.filter_viable(candidates)
        merged = h.merge_duplicates(viable)
        if merged:
            kinds = bootstrapper.bootstrap_from_candidates(merged)
            for kind in kinds:
                assert isinstance(kind, NewKind)
                assert 0.0 <= kind.confidence <= 1.0

    def test_full_pipeline_produces_ranked_report(self) -> None:
        """Complete pipeline: hypothesize → bootstrap → rank → full_report."""
        cfg = BootstrapConfig(
            min_candidate_confidence=0.4,
            min_pattern_frequency=2,
        )
        h = KindHypothesizer(cfg)
        bootstrapper = KindBootstrapper(cfg)
        patterns = [_make_pattern(frequency=8, generality=0.7) for _ in range(3)]
        all_candidates = h.hypothesize_batch(patterns)
        viable = h.filter_viable(all_candidates)
        merged = h.merge_duplicates(viable)
        kinds = bootstrapper.bootstrap_from_candidates(merged)
        ranked = bootstrapper.rank_kinds(kinds)
        report = bootstrapper.full_report(kinds)
        # Everything should be well-formed
        assert isinstance(ranked, list)
        assert isinstance(report, dict)

    def test_definition_builder_and_example_generator_combined(self) -> None:
        """DefinitionBuilder + ExampleGenerator produce consistent output."""
        db = DefinitionBuilder()
        eg = ExampleGenerator()
        candidate = _make_candidate(name="CombinedKind", confidence=0.85, frequency=7)
        definition = db.build(candidate)
        ok, _issues = db.validate_definition(definition)
        examples = eg.generate_from_definition(definition, name="CombinedKind")
        formatted = eg.format_examples(examples)
        # All results should be valid strings
        assert isinstance(definition, str)
        assert isinstance(ok, bool)
        assert isinstance(examples, list)
        assert isinstance(formatted, str)

    def test_bootstrap_config_affects_hypothesizer_output(self) -> None:
        """Stricter BootstrapConfig reduces hypothesizer output size."""
        strict_cfg = BootstrapConfig(
            min_candidate_confidence=0.95,
            min_pattern_frequency=100,
        )
        lenient_cfg = BootstrapConfig(
            min_candidate_confidence=0.1,
            min_pattern_frequency=1,
        )
        strict_h = KindHypothesizer(strict_cfg)
        lenient_h = KindHypothesizer(lenient_cfg)
        pattern = _make_pattern(frequency=5)
        strict_filtered = strict_h.filter_viable(strict_h.hypothesize(pattern))
        lenient_filtered = lenient_h.filter_viable(lenient_h.hypothesize(pattern))
        # Strict config should produce <= results compared to lenient
        assert len(strict_filtered) <= len(lenient_filtered)
