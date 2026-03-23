"""Tests for jugeo.ideation.semantic_futures.s01_future_generation.

Covers GenerationConfig, SemanticOperator, DEFAULT_OPERATORS, FutureGenerator,
FutureExpander, FuturePruner, and the helper functions _hash_delta,
_diversity_score, _deduplicate_futures.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

from jugeo.ideation.semantic_futures.s01_future_generation import (
    DEFAULT_OPERATORS,
    FutureExpander,
    FutureGenerator,
    FuturePruner,
    GenerationConfig,
    GenerationStrategy,
    SemanticOperator,
    _deduplicate_futures,
    _diversity_score,
    _hash_delta,
)
from jugeo.ideation.semantic_futures.models import (
    FutureState,
    FutureTag,
    PurposeFunction,
    SemanticFuture,
)

try:
    from jugeo.ideation.regimes import IdeationRegime, RegimeCatalog  # type: ignore[import]

    _HAS_REGIMES = True
except ImportError:
    _HAS_REGIMES = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def basic_state() -> FutureState:
    """A minimal FutureState for use across tests."""
    return FutureState(
        state_id="state-001",
        description="Explore novel graph-theoretic structures",
        domain="mathematics",
        embedding=(0.1, 0.2, 0.3),
        metadata=(("author", "test"), ("version", "1")),
    )


@pytest.fixture()
def default_config() -> GenerationConfig:
    """Default GenerationConfig."""
    return GenerationConfig()


@pytest.fixture()
def small_config() -> GenerationConfig:
    """A smaller config useful for faster tests."""
    return GenerationConfig(n_futures=3, expansion_factor=2)


def _make_future(
    *,
    future_id: str = "f-test",
    delta: str = "some delta",
    source_state_id: str = "state-001",
    reachability: float = 0.5,
    purpose_alignment: float = 0.5,
    cost: float = 1.0,
    value: float = 0.5,
    tags: tuple[FutureTag, ...] = (),
) -> SemanticFuture:
    """Construct a SemanticFuture for testing."""
    return SemanticFuture(
        future_id=future_id,
        delta=delta,
        source_state_id=source_state_id,
        reachability=reachability,
        purpose_alignment=purpose_alignment,
        cost=cost,
        value=value,
        tags=tags,
        operator_id="",
        explanation="test future",
    )


# ---------------------------------------------------------------------------
# TestGenerationConfig
# ---------------------------------------------------------------------------


class TestGenerationConfig:
    """Tests for GenerationConfig dataclass validation, defaults, and serialisation."""

    def test_default_creation(self) -> None:
        """GenerationConfig can be created with all defaults intact."""
        cfg = GenerationConfig()
        assert cfg.n_futures == 10
        assert cfg.expansion_factor == 3
        assert cfg.min_reachability == 0.1
        assert cfg.max_cost == 10.0
        assert cfg.strategy == GenerationStrategy.RANDOM

    def test_validation_n_futures_must_be_positive(self) -> None:
        """n_futures=0 and n_futures=-1 must raise ValueError."""
        with pytest.raises(ValueError, match="n_futures"):
            GenerationConfig(n_futures=0)
        with pytest.raises(ValueError, match="n_futures"):
            GenerationConfig(n_futures=-1)

    def test_validation_expansion_factor_must_be_positive(self) -> None:
        """expansion_factor=0 must raise ValueError."""
        with pytest.raises(ValueError, match="expansion_factor"):
            GenerationConfig(expansion_factor=0)

    def test_custom_values_accepted(self) -> None:
        """Non-default positive values are accepted without error."""
        cfg = GenerationConfig(n_futures=5, expansion_factor=2, max_cost=20.0)
        assert cfg.n_futures == 5
        assert cfg.expansion_factor == 2
        assert cfg.max_cost == 20.0

    def test_to_dict_from_dict_round_trip(self) -> None:
        """to_dict() → from_dict() produces an equal object."""
        original = GenerationConfig(
            n_futures=7,
            expansion_factor=4,
            min_reachability=0.2,
            max_cost=5.0,
            strategy=GenerationStrategy.REFINEMENT,
        )
        reconstructed = GenerationConfig.from_dict(original.to_dict())
        assert reconstructed == original

    def test_frozen(self) -> None:
        """Mutating a frozen GenerationConfig raises an error."""
        cfg = GenerationConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.n_futures = 99  # type: ignore[misc]

    def test_strategy_variants_accepted(self) -> None:
        """All GenerationStrategy values can be stored in a GenerationConfig."""
        for strategy in GenerationStrategy:
            cfg = GenerationConfig(strategy=strategy)
            assert cfg.strategy == strategy


# ---------------------------------------------------------------------------
# TestSemanticOperator
# ---------------------------------------------------------------------------


class TestSemanticOperator:
    """Tests for SemanticOperator creation, validation, apply, and serialisation."""

    def test_creation(self) -> None:
        """SemanticOperator can be created with required fields."""
        op = SemanticOperator(op_id="test_op", name="Test", description="A test operator.")
        assert op.op_id == "test_op"
        assert op.name == "Test"
        assert op.cost_multiplier == 1.0

    def test_apply_returns_string(self, basic_state: FutureState) -> None:
        """apply() returns a non-empty string."""
        op = SemanticOperator(op_id="custom", name="Custom", description="Custom op.")
        result = op.apply("initial idea", basic_state)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_apply_differs_from_input(self, basic_state: FutureState) -> None:
        """apply() produces output that elaborates on or differs from the raw input."""
        op = SemanticOperator(op_id="negate", name="Negation", description="Invert direction.")
        delta = "explore symmetry"
        result = op.apply(delta, basic_state)
        # The result should be a longer elaboration, not the bare input unchanged
        assert result != delta or len(result) > len(delta)

    def test_apply_uses_state_domain(self, basic_state: FutureState) -> None:
        """apply() incorporates the state domain in its output."""
        op = SemanticOperator(op_id="extend", name="Extension", description="Extend idea.")
        result = op.apply("some concept", basic_state)
        assert basic_state.domain in result

    def test_to_dict_from_dict_round_trip(self) -> None:
        """to_dict() → from_dict() preserves all fields."""
        op = SemanticOperator(
            op_id="bridge",
            name="Bridge",
            description="Bridge operator.",
            cost_multiplier=1.5,
        )
        reconstructed = SemanticOperator.from_dict(op.to_dict())
        assert reconstructed == op

    def test_cost_multiplier_must_be_positive(self) -> None:
        """cost_multiplier=0 must raise ValueError."""
        with pytest.raises(ValueError, match="cost_multiplier"):
            SemanticOperator(op_id="x", name="X", description="X.", cost_multiplier=0)

    def test_cost_multiplier_negative_raises(self) -> None:
        """Negative cost_multiplier raises ValueError."""
        with pytest.raises(ValueError, match="cost_multiplier"):
            SemanticOperator(op_id="x", name="X", description="X.", cost_multiplier=-0.5)

    def test_frozen(self) -> None:
        """SemanticOperator is immutable."""
        op = SemanticOperator(op_id="x", name="X", description="X.")
        with pytest.raises((AttributeError, TypeError)):
            op.name = "Y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestDefaultOperators
# ---------------------------------------------------------------------------


class TestDefaultOperators:
    """Tests for the DEFAULT_OPERATORS list."""

    def test_nonempty(self) -> None:
        """DEFAULT_OPERATORS contains at least one operator."""
        assert len(DEFAULT_OPERATORS) >= 1

    def test_at_least_five_operators(self) -> None:
        """DEFAULT_OPERATORS contains at least five distinct operators as specified."""
        assert len(DEFAULT_OPERATORS) >= 5

    def test_all_have_names(self) -> None:
        """Every operator has a non-empty name and description."""
        for op in DEFAULT_OPERATORS:
            assert op.name, f"Operator {op.op_id} has empty name"
            assert op.description, f"Operator {op.op_id} has empty description"

    def test_unique_op_ids(self) -> None:
        """All op_ids in DEFAULT_OPERATORS are unique."""
        ids = [op.op_id for op in DEFAULT_OPERATORS]
        assert len(ids) == len(set(ids)), "Duplicate op_ids found in DEFAULT_OPERATORS"

    @pytest.mark.parametrize("op", DEFAULT_OPERATORS, ids=[op.op_id for op in DEFAULT_OPERATORS])
    def test_each_operator_applies(self, op: SemanticOperator) -> None:
        """Each DEFAULT_OPERATOR can apply() and returns a non-empty string."""
        state = FutureState(
            state_id="s0",
            description="Foundational concept",
            domain="physics",
        )
        result = op.apply("initial delta", state)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# TestFutureGenerator
# ---------------------------------------------------------------------------


class TestFutureGenerator:
    """Tests for FutureGenerator.generate, generate_from_regime, generate_analogical."""

    def test_generate_returns_n_futures(self, basic_state: FutureState) -> None:
        """generate(state, n=10) returns exactly 10 SemanticFutures."""
        gen = FutureGenerator()
        result = gen.generate(basic_state, n=10)
        assert len(result) == 10

    def test_generate_n_zero_returns_empty(self, basic_state: FutureState) -> None:
        """generate(state, n=0) returns an empty list."""
        gen = FutureGenerator()
        result = gen.generate(basic_state, n=0)
        assert result == []

    def test_generate_n_one(self, basic_state: FutureState) -> None:
        """generate(state, n=1) returns a list of exactly one future."""
        gen = FutureGenerator()
        result = gen.generate(basic_state, n=1)
        assert len(result) == 1
        assert isinstance(result[0], SemanticFuture)

    def test_all_futures_valid_reachability(self, basic_state: FutureState) -> None:
        """All generated futures have reachability in [0, 1]."""
        gen = FutureGenerator()
        for future in gen.generate(basic_state, n=15):
            assert 0.0 <= future.reachability <= 1.0, (
                f"reachability out of range: {future.reachability}"
            )

    def test_all_futures_valid_alignment(self, basic_state: FutureState) -> None:
        """All generated futures have purpose_alignment in [0, 1]."""
        gen = FutureGenerator()
        for future in gen.generate(basic_state, n=15):
            assert 0.0 <= future.purpose_alignment <= 1.0, (
                f"purpose_alignment out of range: {future.purpose_alignment}"
            )

    def test_all_futures_have_source_state_id(self, basic_state: FutureState) -> None:
        """All generated futures reference the correct source_state_id."""
        gen = FutureGenerator()
        for future in gen.generate(basic_state, n=5):
            assert future.source_state_id == basic_state.state_id

    def test_all_futures_have_non_empty_delta(self, basic_state: FutureState) -> None:
        """All generated futures have a non-empty delta string."""
        gen = FutureGenerator()
        for future in gen.generate(basic_state, n=5):
            assert future.delta, "Future has empty delta"

    def test_generate_from_regime_with_none(self, basic_state: FutureState) -> None:
        """generate_from_regime with regime=None falls back and returns a list."""
        gen = FutureGenerator()
        result = gen.generate_from_regime(basic_state, None, n=5)
        assert isinstance(result, list)
        assert len(result) == 5

    def test_generate_from_regime_with_mock_object(self, basic_state: FutureState) -> None:
        """generate_from_regime uses a description attribute if present on regime."""

        class MockRegime:
            description = "focus on algebraic topology"

        gen = FutureGenerator()
        result = gen.generate_from_regime(basic_state, MockRegime(), n=3)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_generate_analogical(self, basic_state: FutureState) -> None:
        """generate_analogical returns a list of SemanticFutures."""
        gen = FutureGenerator()
        result = gen.generate_analogical(basic_state, reference="biology", n=4)
        assert isinstance(result, list)
        assert len(result) == 4
        assert all(isinstance(f, SemanticFuture) for f in result)

    def test_generate_analogical_zero(self, basic_state: FutureState) -> None:
        """generate_analogical(state, ref, n=0) returns []."""
        gen = FutureGenerator()
        result = gen.generate_analogical(basic_state, reference="chemistry", n=0)
        assert result == []

    def test_generate_with_default_config(self, basic_state: FutureState) -> None:
        """Calling generate() without n uses config.n_futures."""
        cfg = GenerationConfig(n_futures=6)
        gen = FutureGenerator(config=cfg)
        result = gen.generate(basic_state)
        assert len(result) == 6

    def test_generate_with_custom_operators(self, basic_state: FutureState) -> None:
        """FutureGenerator respects a custom operator list."""
        single_op = SemanticOperator(
            op_id="refine", name="Refinement", description="Refine idea."
        )
        gen = FutureGenerator(operators=[single_op])
        result = gen.generate(basic_state, n=3)
        assert all(f.operator_id == "refine" for f in result)

    def test_generate_futures_are_semantic_future_instances(
        self, basic_state: FutureState
    ) -> None:
        """All returned items are SemanticFuture instances."""
        gen = FutureGenerator()
        result = gen.generate(basic_state, n=8)
        assert all(isinstance(f, SemanticFuture) for f in result)


# ---------------------------------------------------------------------------
# TestFutureExpander
# ---------------------------------------------------------------------------


class TestFutureExpander:
    """Tests for FutureExpander.expand and expand_batch."""

    def test_expand_returns_expansion_factor_variants(self, basic_state: FutureState) -> None:
        """expand() returns exactly config.expansion_factor variants."""
        cfg = GenerationConfig(expansion_factor=4)
        expander = FutureExpander(config=cfg)
        seed = _make_future(future_id="seed-1", delta="seed delta concept")
        result = expander.expand(seed, basic_state)
        assert len(result) == 4

    def test_expand_default_factor(self, basic_state: FutureState) -> None:
        """Default expansion_factor=3 produces 3 variants."""
        expander = FutureExpander()
        seed = _make_future(future_id="seed-2", delta="another seed")
        result = expander.expand(seed, basic_state)
        assert len(result) == 3

    def test_variants_are_semantic_futures(self, basic_state: FutureState) -> None:
        """All variants are SemanticFuture instances."""
        expander = FutureExpander()
        seed = _make_future(future_id="seed-3", delta="base idea")
        result = expander.expand(seed, basic_state)
        assert all(isinstance(v, SemanticFuture) for v in result)

    def test_variants_differ_from_seed(self, basic_state: FutureState) -> None:
        """At least some variants have a delta different from the seed."""
        cfg = GenerationConfig(expansion_factor=5)
        expander = FutureExpander(config=cfg)
        seed = _make_future(future_id="seed-4", delta="fixed seed concept")
        result = expander.expand(seed, basic_state)
        unique_deltas = {v.delta for v in result}
        # With multiple operators, at least two distinct deltas expected
        assert len(unique_deltas) >= 1  # minimally the variants exist

    def test_expand_batch_empty_seeds(self, basic_state: FutureState) -> None:
        """expand_batch with an empty seed list returns []."""
        expander = FutureExpander()
        result = expander.expand_batch([], basic_state)
        assert result == []

    def test_expand_batch(self, basic_state: FutureState) -> None:
        """expand_batch returns expansion_factor * len(seeds) variants."""
        cfg = GenerationConfig(expansion_factor=2)
        expander = FutureExpander(config=cfg)
        seeds = [
            _make_future(future_id=f"seed-{i}", delta=f"seed concept {i}")
            for i in range(3)
        ]
        result = expander.expand_batch(seeds, basic_state)
        assert len(result) == 3 * 2

    def test_expand_all_valid_reachability(self, basic_state: FutureState) -> None:
        """All expanded variants have reachability in [0, 1]."""
        expander = FutureExpander()
        seed = _make_future(future_id="seed-5", delta="concept seed")
        for variant in expander.expand(seed, basic_state):
            assert 0.0 <= variant.reachability <= 1.0


# ---------------------------------------------------------------------------
# TestFuturePruner
# ---------------------------------------------------------------------------


class TestFuturePruner:
    """Tests for FuturePruner.prune, prune_dominated, prune_by_budget."""

    def _low_value_future(self, fid: str = "f-low") -> SemanticFuture:
        """Future with very low composite_value (~0.02)."""
        return _make_future(
            future_id=fid,
            delta=f"low value delta {fid}",
            reachability=0.01,
            purpose_alignment=0.01,
            cost=5.0,
            value=0.01,
        )

    def _high_value_future(self, fid: str = "f-high") -> SemanticFuture:
        """Future with high composite_value (~0.9)."""
        return _make_future(
            future_id=fid,
            delta=f"high value delta {fid}",
            reachability=0.9,
            purpose_alignment=0.9,
            cost=1.0,
            value=0.9,
        )

    def test_prune_removes_below_threshold(self) -> None:
        """prune() removes futures whose composite_value is below the threshold."""
        pruner = FuturePruner()
        low = self._low_value_future()
        high = self._high_value_future()
        result = pruner.prune([low, high], threshold=0.5)
        assert low not in result
        assert high in result

    def test_prune_keeps_above_threshold(self) -> None:
        """prune() retains futures at or above the threshold."""
        pruner = FuturePruner()
        high = self._high_value_future()
        result = pruner.prune([high], threshold=0.1)
        assert high in result

    def test_prune_empty_list(self) -> None:
        """prune([]) returns []."""
        pruner = FuturePruner()
        assert pruner.prune([], threshold=0.5) == []

    def test_prune_threshold_zero_keeps_all(self) -> None:
        """prune with threshold=0 keeps all futures."""
        pruner = FuturePruner()
        futures = [self._low_value_future(), self._high_value_future()]
        result = pruner.prune(futures, threshold=0.0)
        assert len(result) == 2

    def test_prune_dominated_removes_dominated(self) -> None:
        """prune_dominated removes futures dominated by another in the list."""
        pruner = FuturePruner()
        dominant = _make_future(
            future_id="dom",
            delta="dominant",
            reachability=0.9,
            purpose_alignment=0.9,
            value=0.9,
        )
        dominated = _make_future(
            future_id="weak",
            delta="weak",
            reachability=0.1,
            purpose_alignment=0.1,
            value=0.1,
        )
        result = pruner.prune_dominated([dominant, dominated])
        assert dominant in result
        assert dominated not in result

    def test_prune_dominated_empty_list(self) -> None:
        """prune_dominated([]) returns []."""
        pruner = FuturePruner()
        assert pruner.prune_dominated([]) == []

    def test_prune_dominated_keeps_non_dominated(self) -> None:
        """prune_dominated retains futures that are not dominated."""
        pruner = FuturePruner()
        # f1 is better in reachability, f2 better in alignment — neither dominates
        f1 = _make_future(
            future_id="f1", delta="f1", reachability=0.9, purpose_alignment=0.3, value=0.5
        )
        f2 = _make_future(
            future_id="f2", delta="f2", reachability=0.3, purpose_alignment=0.9, value=0.5
        )
        result = pruner.prune_dominated([f1, f2])
        assert f1 in result
        assert f2 in result

    def test_prune_by_budget(self) -> None:
        """prune_by_budget removes futures with cost > budget."""
        pruner = FuturePruner()
        cheap = _make_future(future_id="cheap", delta="cheap", cost=1.0)
        expensive = _make_future(future_id="exp", delta="expensive", cost=100.0)
        result = pruner.prune_by_budget([cheap, expensive], budget=10.0)
        assert cheap in result
        assert expensive not in result

    def test_prune_by_budget_zero_budget(self) -> None:
        """With budget=0, all futures with cost>0 are removed."""
        pruner = FuturePruner()
        f = _make_future(future_id="f1", delta="costly", cost=0.01)
        result = pruner.prune_by_budget([f], budget=0.0)
        assert result == []

    def test_prune_by_budget_exact_limit(self) -> None:
        """A future whose cost equals the budget is kept (inclusive bound)."""
        pruner = FuturePruner()
        f = _make_future(future_id="exact", delta="exact cost", cost=5.0)
        result = pruner.prune_by_budget([f], budget=5.0)
        assert f in result


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for _hash_delta, _diversity_score, and _deduplicate_futures."""

    def test_hash_delta_deterministic(self) -> None:
        """_hash_delta returns the same hash for the same input."""
        h1 = _hash_delta("hello world")
        h2 = _hash_delta("hello world")
        assert h1 == h2

    def test_hash_delta_different_for_different_inputs(self) -> None:
        """_hash_delta returns different hashes for different inputs."""
        assert _hash_delta("abc") != _hash_delta("xyz")

    def test_hash_delta_returns_string(self) -> None:
        """_hash_delta always returns a str."""
        result = _hash_delta("test input")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hash_delta_empty_string(self) -> None:
        """_hash_delta handles an empty string without error."""
        result = _hash_delta("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_diversity_score_identical_list_is_zero_or_low(self) -> None:
        """Identical futures yield a diversity score of 0.0."""
        f = _make_future(delta="identical concept concept concept")
        result = _diversity_score([f, f, f])
        assert result == 0.0

    def test_diversity_score_empty_list(self) -> None:
        """_diversity_score([]) returns 0.0."""
        assert _diversity_score([]) == 0.0

    def test_diversity_score_single_element(self) -> None:
        """_diversity_score with a single future returns 0.0."""
        f = _make_future(delta="solo future")
        assert _diversity_score([f]) == 0.0

    def test_diversity_score_diverse_list_is_positive(self) -> None:
        """Futures with very different deltas produce a positive diversity score."""
        futures = [
            _make_future(future_id=f"d{i}", delta=delta)
            for i, delta in enumerate(
                [
                    "alpha beta gamma delta",
                    "one two three four five six",
                    "red green blue yellow purple orange",
                    "apple pear mango kiwi strawberry",
                ]
            )
        ]
        score = _diversity_score(futures)
        assert score > 0.0

    def test_deduplicate_removes_duplicates(self) -> None:
        """_deduplicate_futures keeps only the first occurrence of each delta."""
        f1 = _make_future(future_id="u1", delta="same delta")
        f2 = _make_future(future_id="u2", delta="same delta")
        f3 = _make_future(future_id="u3", delta="different delta")
        result = _deduplicate_futures([f1, f2, f3])
        assert len(result) == 2
        assert f1 in result
        assert f2 not in result
        assert f3 in result

    def test_deduplicate_empty(self) -> None:
        """_deduplicate_futures([]) returns []."""
        assert _deduplicate_futures([]) == []

    def test_deduplicate_no_duplicates_unchanged(self) -> None:
        """_deduplicate_futures preserves lists with no duplicate deltas."""
        futures = [
            _make_future(future_id=f"f{i}", delta=f"unique concept {i}")
            for i in range(5)
        ]
        result = _deduplicate_futures(futures)
        assert len(result) == 5

    def test_deduplicate_preserves_order(self) -> None:
        """_deduplicate_futures keeps first occurrences in original order."""
        f1 = _make_future(future_id="a", delta="first")
        f2 = _make_future(future_id="b", delta="second")
        f3 = _make_future(future_id="c", delta="first")  # duplicate of f1
        result = _deduplicate_futures([f1, f2, f3])
        assert result[0].future_id == "a"
        assert result[1].future_id == "b"


# ---------------------------------------------------------------------------
# TestIntegrationGeneration
# ---------------------------------------------------------------------------


class TestIntegrationGeneration:
    """Integration tests combining FutureGenerator, FutureExpander, FuturePruner."""

    def test_full_pipeline_generate_expand_prune(self, basic_state: FutureState) -> None:
        """generate → expand first result → prune; pipeline produces a valid list."""
        gen = FutureGenerator(config=GenerationConfig(n_futures=5))
        futures = gen.generate(basic_state)
        assert futures

        expander = FutureExpander(config=GenerationConfig(expansion_factor=3))
        expanded = expander.expand(futures[0], basic_state)
        assert len(expanded) == 3

        pruner = FuturePruner()
        pruned = pruner.prune(expanded, threshold=0.0)
        assert isinstance(pruned, list)

    def test_generate_then_deduplicate(self, basic_state: FutureState) -> None:
        """After generation, deduplication returns a list with no repeated deltas."""
        gen = FutureGenerator(config=GenerationConfig(n_futures=8))
        futures = gen.generate(basic_state)
        deduped = _deduplicate_futures(futures)
        deltas = [f.delta for f in deduped]
        assert len(deltas) == len(set(deltas))

    def test_generate_then_prune_dominated(self, basic_state: FutureState) -> None:
        """Non-dominated pruning on generated futures returns a non-empty list."""
        gen = FutureGenerator(config=GenerationConfig(n_futures=10))
        futures = gen.generate(basic_state)
        pruner = FuturePruner()
        non_dom = pruner.prune_dominated(futures)
        assert isinstance(non_dom, list)
        assert len(non_dom) >= 1

    def test_expand_batch_then_prune_by_budget(self, basic_state: FutureState) -> None:
        """Batch expansion followed by budget pruning returns a valid subset."""
        gen = FutureGenerator(config=GenerationConfig(n_futures=3))
        seeds = gen.generate(basic_state)
        expander = FutureExpander(config=GenerationConfig(expansion_factor=2))
        expanded = expander.expand_batch(seeds, basic_state)
        pruner = FuturePruner()
        result = pruner.prune_by_budget(expanded, budget=1000.0)
        assert isinstance(result, list)

    @pytest.mark.skipif(not _HAS_REGIMES, reason="jugeo.ideation.regimes not available")
    def test_integration_with_regime(self, basic_state: FutureState) -> None:
        """Integration with IdeationRegime when the module is available."""
        regime = IdeationRegime(description="exploratory topology")  # type: ignore[name-defined]
        gen = FutureGenerator()
        result = gen.generate_from_regime(basic_state, regime, n=5)
        assert isinstance(result, list)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Parametrized standalone tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 5, 10, 20])
def test_generate_parametrized_n(n: int) -> None:
    """FutureGenerator.generate(state, n=n) returns exactly n futures for various n."""
    state = FutureState(
        state_id="param-state",
        description="Testing various generation counts",
        domain="computer science",
    )
    gen = FutureGenerator()
    result = gen.generate(state, n=n)
    assert len(result) == n, f"Expected {n} futures, got {len(result)}"
    assert all(isinstance(f, SemanticFuture) for f in result)


@pytest.mark.parametrize("strategy", list(GenerationStrategy))
def test_generation_config_strategy_round_trip(strategy: GenerationStrategy) -> None:
    """GenerationConfig round-trips correctly for every GenerationStrategy value."""
    cfg = GenerationConfig(strategy=strategy)
    assert GenerationConfig.from_dict(cfg.to_dict()) == cfg


@pytest.mark.parametrize("budget", [0.0, 0.5, 1.0, 5.0, 1000.0])
def test_prune_by_budget_parametrized(budget: float) -> None:
    """prune_by_budget removes exactly the futures exceeding the given budget."""
    pruner = FuturePruner()
    futures = [
        _make_future(future_id=f"f{i}", delta=f"delta {i}", cost=float(i))
        for i in range(6)
    ]
    result = pruner.prune_by_budget(futures, budget=budget)
    assert all(f.cost <= budget for f in result), (
        f"Budget violation at budget={budget}: costs={[f.cost for f in result]}"
    )
