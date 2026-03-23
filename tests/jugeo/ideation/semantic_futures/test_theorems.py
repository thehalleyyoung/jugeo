"""Tests for jugeo.ideation.semantic_futures.theorems (Ch. 49 — Semantic Futures).

Covers TheoremStatement, TheoremHypothesis, TheoremCatalog, TheoremVerifier,
TheoremDifficulty, and the 15 pre-built theorem constants (THEOREM_49_1 through
THEOREM_49_15) exported from the module.  Integration tests exercise the
verifier in a realistic scenario.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

pytest.importorskip("jugeo.ideation.semantic_futures.theorems")

from jugeo.ideation.semantic_futures.theorems import (
    TheoremStatement,
    TheoremHypothesis,
    TheoremCatalog,
    TheoremVerifier,
    TheoremDifficulty,
    THEOREM_CATALOG,
    THEOREM_49_1,
    THEOREM_49_2,
    THEOREM_49_3,
    THEOREM_49_4,
    THEOREM_49_5,
    THEOREM_49_6,
    THEOREM_49_7,
    THEOREM_49_8,
    THEOREM_49_9,
    THEOREM_49_10,
    THEOREM_49_11,
    THEOREM_49_12,
    THEOREM_49_13,
    THEOREM_49_14,
    THEOREM_49_15,
)

# Collect the 15 theorems for parametrised use
ALL_THEOREMS = [
    THEOREM_49_1, THEOREM_49_2, THEOREM_49_3, THEOREM_49_4, THEOREM_49_5,
    THEOREM_49_6, THEOREM_49_7, THEOREM_49_8, THEOREM_49_9, THEOREM_49_10,
    THEOREM_49_11, THEOREM_49_12, THEOREM_49_13, THEOREM_49_14, THEOREM_49_15,
]
ALL_THEOREM_IDS = [f"49.{i}" for i in range(1, 16)]


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _make_hypothesis(
    *,
    label: str = "h1",
    description: str = "The ideation budget is positive.",
    required_context_keys: tuple[str, ...] = ("budget",),
) -> TheoremHypothesis:
    return TheoremHypothesis(
        label=label,
        description=description,
        required_context_keys=required_context_keys,
    )


def _make_theorem(
    *,
    theorem_id: str = "49.0",
    statement_text: str = "For all finite budgets B > 0 there exists an optimal allocation.",
    hypotheses: tuple[TheoremHypothesis, ...] | None = None,
    conclusion: str = "The optimal allocation maximises expected yield.",
    proof_sketch: str = "By induction on the budget decomposition.",
    chapter_ref: str = "Chapter 49",
    difficulty: TheoremDifficulty = TheoremDifficulty.MODERATE,
    tags: tuple[str, ...] = ("budget", "optimality"),
) -> TheoremStatement:
    if hypotheses is None:
        hypotheses = (_make_hypothesis(),)
    return TheoremStatement(
        theorem_id=theorem_id,
        statement_text=statement_text,
        hypotheses=hypotheses,
        conclusion=conclusion,
        proof_sketch=proof_sketch,
        chapter_ref=chapter_ref,
        difficulty=difficulty,
        tags=tags,
    )


def _make_catalog_with_n(n: int) -> TheoremCatalog:
    """Return a TheoremCatalog pre-loaded with n distinct theorems."""
    catalog = TheoremCatalog()
    for i in range(1, n + 1):
        t = _make_theorem(
            theorem_id=f"test.{i}",
            statement_text=f"Test theorem {i}.",
            tags=("test", f"group-{i % 3}"),
        )
        catalog.add(t)
    return catalog


# ---------------------------------------------------------------------------
# TestTheoremStatement
# ---------------------------------------------------------------------------

class TestTheoremStatement:
    """Tests for TheoremStatement — the core immutable theorem dataclass.

    Covers creation, to_dict/from_dict round-trips, __str__, is_constructive,
    and frozen immutability.
    """

    def test_basic_creation(self) -> None:
        """TheoremStatement can be created with all required fields."""
        t = _make_theorem()
        assert t.theorem_id == "49.0"
        assert len(t.statement_text) > 0
        assert len(t.hypotheses) >= 1
        assert len(t.conclusion) > 0
        assert len(t.proof_sketch) > 0
        assert "49" in t.chapter_ref

    def test_to_dict_returns_dict(self) -> None:
        """to_dict returns a plain dict with expected keys."""
        t = _make_theorem()
        d = t.to_dict()
        assert isinstance(d, dict)
        assert "theorem_id" in d
        assert "statement_text" in d
        assert "conclusion" in d
        assert "proof_sketch" in d

    def test_from_dict_round_trip(self) -> None:
        """from_dict(to_dict(t)) restores an equivalent theorem."""
        t = _make_theorem()
        d = t.to_dict()
        restored = TheoremStatement.from_dict(d)
        assert restored.theorem_id == t.theorem_id
        assert restored.statement_text == t.statement_text
        assert restored.conclusion == t.conclusion
        assert restored.chapter_ref == t.chapter_ref

    def test_str_is_non_empty(self) -> None:
        """__str__ returns a non-empty string."""
        t = _make_theorem()
        s = str(t)
        assert isinstance(s, str)
        assert len(s) > 0

    def test_str_contains_theorem_id(self) -> None:
        """__str__ includes the theorem_id somewhere."""
        t = _make_theorem(theorem_id="49.7")
        assert "49.7" in str(t)

    def test_is_constructive_true_when_proof_has_construct(self) -> None:
        """is_constructive is True when proof_sketch contains 'construct'."""
        t = _make_theorem(proof_sketch="We construct a witness allocation.")
        assert t.is_constructive is True

    def test_is_constructive_false_when_no_construct(self) -> None:
        """is_constructive is False when proof_sketch has no constructive keywords."""
        t = _make_theorem(proof_sketch="Follows from the axiom of choice.")
        assert t.is_constructive is False

    def test_is_constructive_true_for_build_keyword(self) -> None:
        """is_constructive is True when proof_sketch contains 'build'."""
        t = _make_theorem(proof_sketch="We build an explicit sequence.")
        assert t.is_constructive is True

    def test_frozen_immutability(self) -> None:
        """TheoremStatement is immutable (frozen dataclass)."""
        t = _make_theorem()
        with pytest.raises((AttributeError, TypeError)):
            t.statement_text = "mutated"  # type: ignore[misc]

    def test_tags_stored(self) -> None:
        """Tags are stored and accessible."""
        t = _make_theorem(tags=("convergence", "budget", "pareto"))
        assert "convergence" in t.tags
        assert "budget" in t.tags

    def test_difficulty_field(self) -> None:
        """difficulty is a valid TheoremDifficulty value."""
        t = _make_theorem(difficulty=TheoremDifficulty.ADVANCED)
        assert t.difficulty == TheoremDifficulty.ADVANCED

    def test_chapter_ref_preserved(self) -> None:
        """chapter_ref is stored verbatim."""
        t = _make_theorem(chapter_ref="Chapter 49, Section 3")
        assert t.chapter_ref == "Chapter 49, Section 3"


# ---------------------------------------------------------------------------
# TestTheoremHypothesis
# ---------------------------------------------------------------------------

class TestTheoremHypothesis:
    """Tests for TheoremHypothesis — a single named precondition for a theorem."""

    def test_basic_creation(self) -> None:
        """TheoremHypothesis is created with label, description, required keys."""
        h = _make_hypothesis()
        assert h.label == "h1"
        assert len(h.description) > 0
        assert isinstance(h.required_context_keys, tuple)

    def test_to_dict_round_trip(self) -> None:
        """from_dict(to_dict(h)) restores the hypothesis."""
        h = _make_hypothesis(
            label="H-budget",
            description="Budget must be positive.",
            required_context_keys=("budget", "regime"),
        )
        d = h.to_dict()
        restored = TheoremHypothesis.from_dict(d)
        assert restored.label == h.label
        assert restored.description == h.description
        assert set(restored.required_context_keys) == set(h.required_context_keys)

    def test_empty_required_keys(self) -> None:
        """Hypothesis with no required context keys is valid."""
        h = TheoremHypothesis(
            label="unconditional",
            description="Always holds.",
            required_context_keys=(),
        )
        assert h.required_context_keys == ()

    def test_frozen(self) -> None:
        """TheoremHypothesis is immutable."""
        h = _make_hypothesis()
        with pytest.raises((AttributeError, TypeError)):
            h.label = "mutated"  # type: ignore[misc]

    def test_multiple_required_keys(self) -> None:
        """Hypothesis can require several context keys."""
        h = _make_hypothesis(required_context_keys=("budget", "purpose", "frontier_size"))
        assert len(h.required_context_keys) == 3


# ---------------------------------------------------------------------------
# TestTheoremCatalog
# ---------------------------------------------------------------------------

class TestTheoremCatalog:
    """Tests for TheoremCatalog — a mutable collection of TheoremStatements.

    Covers add, get, list_all, filter_by_tag, filter_by_chapter, size,
    to_dict/from_dict, and duplicate-add protection.
    """

    def test_empty_catalog(self) -> None:
        """A freshly created catalog has size 0."""
        catalog = TheoremCatalog()
        assert catalog.size() == 0

    def test_add_and_get(self) -> None:
        """After adding a theorem, get() by theorem_id returns it."""
        catalog = TheoremCatalog()
        t = _make_theorem(theorem_id="X.1")
        catalog.add(t)
        retrieved = catalog.get("X.1")
        assert retrieved is not None
        assert retrieved.theorem_id == "X.1"

    def test_get_missing_raises(self) -> None:
        """get() raises KeyError for a theorem_id not in the catalog."""
        catalog = TheoremCatalog()
        with pytest.raises(KeyError):
            catalog.get("nonexistent")

    def test_list_all_returns_all_added(self) -> None:
        """list_all() returns a list with exactly the added theorems."""
        catalog = _make_catalog_with_n(5)
        all_theorems = catalog.list_all()
        assert len(all_theorems) == 5

    def test_add_duplicate_raises(self) -> None:
        """Adding a theorem with a duplicate theorem_id raises ValueError."""
        catalog = TheoremCatalog()
        t = _make_theorem(theorem_id="dup")
        catalog.add(t)
        with pytest.raises((ValueError, KeyError)):
            catalog.add(t)

    def test_filter_by_tag_correct(self) -> None:
        """filter_by_tag returns only theorems that carry the requested tag."""
        catalog = TheoremCatalog()
        t1 = _make_theorem(theorem_id="a", tags=("convergence", "budget"))
        t2 = _make_theorem(theorem_id="b", tags=("pareto",))
        t3 = _make_theorem(theorem_id="c", tags=("convergence",))
        for t in (t1, t2, t3):
            catalog.add(t)
        result = catalog.filter_by_tag("convergence")
        ids = [t.theorem_id for t in result]
        assert "a" in ids
        assert "c" in ids
        assert "b" not in ids

    def test_filter_by_tag_empty(self) -> None:
        """filter_by_tag returns [] when no theorems have the tag."""
        catalog = _make_catalog_with_n(3)
        result = catalog.filter_by_tag("nonexistent-tag")
        assert result == []

    def test_filter_by_chapter_correct(self) -> None:
        """filter_by_chapter returns only theorems whose chapter_ref contains the chapter."""
        catalog = TheoremCatalog()
        t1 = _make_theorem(theorem_id="49-a", chapter_ref="Chapter 49")
        t2 = _make_theorem(theorem_id="50-a", chapter_ref="Chapter 50")
        catalog.add(t1)
        catalog.add(t2)
        result = catalog.filter_by_chapter("49")
        ids = [t.theorem_id for t in result]
        assert "49-a" in ids
        assert "50-a" not in ids

    def test_size_increments(self) -> None:
        """size() increments by 1 for each added theorem."""
        catalog = TheoremCatalog()
        for i in range(7):
            catalog.add(_make_theorem(theorem_id=f"sz.{i}"))
        assert catalog.size() == 7

    def test_to_dict_from_dict_round_trip(self) -> None:
        """from_dict(to_dict(catalog)) restores all theorems."""
        catalog = _make_catalog_with_n(4)
        d = catalog.to_dict()
        restored = TheoremCatalog.from_dict(d)
        assert restored.size() == 4
        original_ids = {t.theorem_id for t in catalog.list_all()}
        restored_ids = {t.theorem_id for t in restored.list_all()}
        assert original_ids == restored_ids

    def test_to_dict_round_trip_preserves_theorem_data(self) -> None:
        """Round-trip preserves statement_text and chapter_ref."""
        catalog = TheoremCatalog()
        t = _make_theorem(
            theorem_id="rt.1",
            statement_text="The future is reachable.",
            chapter_ref="Chapter 49",
        )
        catalog.add(t)
        restored_catalog = TheoremCatalog.from_dict(catalog.to_dict())
        restored_t = restored_catalog.get("rt.1")
        assert restored_t.statement_text == "The future is reachable."
        assert restored_t.chapter_ref == "Chapter 49"


# ---------------------------------------------------------------------------
# TestTheoremVerifier
# ---------------------------------------------------------------------------

class TestTheoremVerifier:
    """Tests for TheoremVerifier — checks whether a theorem's hypotheses are met
    by a given context dict.
    """

    def test_all_hypotheses_met_with_full_context(self) -> None:
        """all_hypotheses_met returns True when context contains all required keys."""
        t = _make_theorem(
            hypotheses=(
                _make_hypothesis(label="H1", required_context_keys=("budget",)),
                _make_hypothesis(label="H2", required_context_keys=("purpose",)),
            )
        )
        ctx = {"budget": 10.0, "purpose": "grow theorems"}
        v = TheoremVerifier()
        assert v.all_hypotheses_met(t, ctx) is True

    def test_all_hypotheses_met_with_missing_key(self) -> None:
        """all_hypotheses_met returns False when a required key is absent."""
        t = _make_theorem(
            hypotheses=(_make_hypothesis(required_context_keys=("budget", "purpose")),)
        )
        ctx = {"budget": 5.0}  # 'purpose' is missing
        v = TheoremVerifier()
        assert v.all_hypotheses_met(t, ctx) is False

    def test_check_hypotheses_returns_missing_keys(self) -> None:
        """check_hypotheses returns the list of missing context keys."""
        t = _make_theorem(
            hypotheses=(
                _make_hypothesis(required_context_keys=("alpha", "beta", "gamma")),
            )
        )
        ctx = {"alpha": 1}  # beta and gamma missing
        v = TheoremVerifier()
        missing = v.check_hypotheses(t, ctx)
        assert "beta" in missing
        assert "gamma" in missing
        assert "alpha" not in missing

    def test_check_hypotheses_empty_when_satisfied(self) -> None:
        """check_hypotheses returns [] when all required keys are present."""
        t = _make_theorem(
            hypotheses=(_make_hypothesis(required_context_keys=("x", "y")),)
        )
        ctx = {"x": 1, "y": 2, "extra": 3}
        v = TheoremVerifier()
        assert v.check_hypotheses(t, ctx) == []

    def test_applicable_theorems_empty_catalog(self) -> None:
        """applicable_theorems returns [] for an empty catalog."""
        catalog = TheoremCatalog()
        v = TheoremVerifier()
        result = v.applicable_theorems(catalog, {"budget": 10.0})
        assert result == []

    def test_applicable_theorems_filters_by_context(self) -> None:
        """applicable_theorems returns only theorems whose hypotheses are satisfied."""
        catalog = TheoremCatalog()
        t_satisfied = _make_theorem(
            theorem_id="sat",
            hypotheses=(_make_hypothesis(required_context_keys=("x",)),),
        )
        t_unsatisfied = _make_theorem(
            theorem_id="unsat",
            hypotheses=(_make_hypothesis(required_context_keys=("x", "y")),),
        )
        catalog.add(t_satisfied)
        catalog.add(t_unsatisfied)
        ctx = {"x": 1}  # 'y' missing
        v = TheoremVerifier()
        result = v.applicable_theorems(catalog, ctx)
        ids = [t.theorem_id for t in result]
        assert "sat" in ids
        assert "unsat" not in ids

    def test_applicable_theorems_all_when_context_full(self) -> None:
        """applicable_theorems returns all theorems if all hypotheses are met."""
        catalog = TheoremCatalog()
        for i in range(3):
            catalog.add(_make_theorem(
                theorem_id=f"full.{i}",
                hypotheses=(_make_hypothesis(required_context_keys=("budget",)),),
            ))
        v = TheoremVerifier()
        result = v.applicable_theorems(catalog, {"budget": 100.0})
        assert len(result) == 3

    def test_check_hypotheses_multiple_hypotheses(self) -> None:
        """check_hypotheses aggregates missing keys across all hypotheses."""
        t = _make_theorem(
            hypotheses=(
                _make_hypothesis(label="H1", required_context_keys=("a", "b")),
                _make_hypothesis(label="H2", required_context_keys=("c", "d")),
            )
        )
        ctx = {"a": 1, "c": 2}  # b and d missing
        v = TheoremVerifier()
        missing = v.check_hypotheses(t, ctx)
        assert "b" in missing
        assert "d" in missing


# ---------------------------------------------------------------------------
# TestTheoremConstants
# ---------------------------------------------------------------------------

class TestTheoremConstants:
    """Tests for the 15 pre-built theorem constants: THEOREM_49_1 – THEOREM_49_15.

    Verifies that each exported theorem has the required data quality:
    non-empty statement_text, at least one hypothesis, a non-empty conclusion,
    a non-empty proof_sketch, and a chapter_ref containing "49".
    """

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_statement_text_non_empty(self, theorem: TheoremStatement) -> None:
        """Each theorem has a non-empty statement_text."""
        assert isinstance(theorem.statement_text, str)
        assert len(theorem.statement_text.strip()) > 0

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_has_at_least_one_hypothesis(self, theorem: TheoremStatement) -> None:
        """Each theorem declares at least one hypothesis."""
        assert len(theorem.hypotheses) >= 1

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_conclusion_non_empty(self, theorem: TheoremStatement) -> None:
        """Each theorem has a non-empty conclusion."""
        assert isinstance(theorem.conclusion, str)
        assert len(theorem.conclusion.strip()) > 0

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_proof_sketch_non_empty(self, theorem: TheoremStatement) -> None:
        """Each theorem has a non-empty proof_sketch."""
        assert isinstance(theorem.proof_sketch, str)
        assert len(theorem.proof_sketch.strip()) > 0

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_chapter_ref_contains_49(self, theorem: TheoremStatement) -> None:
        """Each theorem's chapter_ref contains the string '49'."""
        assert "49" in theorem.chapter_ref

    @pytest.mark.parametrize("theorem, expected_id", list(zip(ALL_THEOREMS, ALL_THEOREM_IDS)))
    def test_theorem_ids_match_convention(
        self, theorem: TheoremStatement, expected_id: str
    ) -> None:
        """Each theorem's theorem_id matches the '49.N' naming convention."""
        assert theorem.theorem_id == expected_id

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_theorem_is_frozen(self, theorem: TheoremStatement) -> None:
        """Each pre-built theorem is immutable."""
        with pytest.raises((AttributeError, TypeError)):
            theorem.statement_text = "tampered"  # type: ignore[misc]

    @pytest.mark.parametrize("theorem", ALL_THEOREMS, ids=ALL_THEOREM_IDS)
    def test_theorem_round_trips(self, theorem: TheoremStatement) -> None:
        """Each theorem can be serialised to dict and restored."""
        restored = TheoremStatement.from_dict(theorem.to_dict())
        assert restored.theorem_id == theorem.theorem_id
        assert restored.statement_text == theorem.statement_text

    def test_all_fifteen_constants_exported(self) -> None:
        """Exactly 15 theorem constants are exported from the theorems module."""
        assert len(ALL_THEOREMS) == 15

    def test_all_theorem_ids_distinct(self) -> None:
        """All 15 theorem_ids are distinct."""
        ids = [t.theorem_id for t in ALL_THEOREMS]
        assert len(set(ids)) == 15


# ---------------------------------------------------------------------------
# TestTheoremCatalogModule
# ---------------------------------------------------------------------------

class TestTheoremCatalogModule:
    """Tests for the module-level THEOREM_CATALOG constant.

    THEOREM_CATALOG should be a TheoremCatalog pre-loaded with all 15 theorems.
    """

    def test_catalog_has_fifteen_theorems(self) -> None:
        """THEOREM_CATALOG contains exactly 15 theorems."""
        assert THEOREM_CATALOG.size() == 15

    def test_catalog_contains_all_theorems(self) -> None:
        """THEOREM_CATALOG contains each of the 15 constants by theorem_id."""
        for theorem_id in ALL_THEOREM_IDS:
            t = THEOREM_CATALOG.get(theorem_id)
            assert t is not None
            assert t.theorem_id == theorem_id

    def test_filter_by_chapter_49_returns_all(self) -> None:
        """filter_by_chapter('49') on THEOREM_CATALOG returns all 15 theorems."""
        result = THEOREM_CATALOG.filter_by_chapter("49")
        assert len(result) == 15

    def test_filter_by_tag_works(self) -> None:
        """filter_by_tag on THEOREM_CATALOG returns only matching theorems."""
        # Find a tag that exists in at least one theorem
        all_tags: list[str] = []
        for t in THEOREM_CATALOG.list_all():
            all_tags.extend(t.tags)
        if not all_tags:
            pytest.skip("No tags found in catalog theorems")
        sample_tag = all_tags[0]
        result = THEOREM_CATALOG.filter_by_tag(sample_tag)
        assert len(result) >= 1
        for t in result:
            assert sample_tag in t.tags

    def test_catalog_is_theorem_catalog_instance(self) -> None:
        """THEOREM_CATALOG is an instance of TheoremCatalog."""
        assert isinstance(THEOREM_CATALOG, TheoremCatalog)

    def test_catalog_list_all_non_empty(self) -> None:
        """list_all() on THEOREM_CATALOG returns a non-empty list."""
        result = THEOREM_CATALOG.list_all()
        assert len(result) > 0

    def test_catalog_get_first_and_last(self) -> None:
        """THEOREM_CATALOG.get works for both '49.1' and '49.15'."""
        first = THEOREM_CATALOG.get("49.1")
        last = THEOREM_CATALOG.get("49.15")
        assert first.theorem_id == "49.1"
        assert last.theorem_id == "49.15"


# ---------------------------------------------------------------------------
# TestIntegrationTheorems
# ---------------------------------------------------------------------------

class TestIntegrationTheorems:
    """Integration tests: theorems interact with verifier and ideation models.

    These tests check that the theorem infrastructure can be applied to validate
    a realistic semantic-futures session context.
    """

    def _realistic_context(self) -> dict:
        """Return a context dict representing a live ideation session."""
        return {
            "budget": 20.0,
            "purpose": "maximise theorem yield in algebraic topology",
            "frontier_size": 5,
            "current_regime": "cover-refinement",
            "spent_budget": 4.0,
            "semantic_distance_model": "cosine",
            "reachability_estimator": "bridge-probability",
        }

    def test_verifier_on_theorem_catalog(self) -> None:
        """TheoremVerifier can process all 15 catalog theorems against a context."""
        v = TheoremVerifier()
        ctx = self._realistic_context()
        applicable = v.applicable_theorems(THEOREM_CATALOG, ctx)
        # At least some theorems should be applicable with a rich context
        assert isinstance(applicable, list)
        assert all(isinstance(t, TheoremStatement) for t in applicable)

    def test_full_hypothesis_check_on_each_theorem(self) -> None:
        """check_hypotheses returns a list of strings for every catalog theorem."""
        v = TheoremVerifier()
        ctx = self._realistic_context()
        for theorem in THEOREM_CATALOG.list_all():
            missing = v.check_hypotheses(theorem, ctx)
            assert isinstance(missing, list)
            assert all(isinstance(k, str) for k in missing)

    def test_catalog_round_trip_preserves_applicability(self) -> None:
        """Serialising and restoring THEOREM_CATALOG preserves applicability results."""
        v = TheoremVerifier()
        ctx = self._realistic_context()
        original_applicable = {
            t.theorem_id for t in v.applicable_theorems(THEOREM_CATALOG, ctx)
        }
        restored_catalog = TheoremCatalog.from_dict(THEOREM_CATALOG.to_dict())
        restored_applicable = {
            t.theorem_id for t in v.applicable_theorems(restored_catalog, ctx)
        }
        assert original_applicable == restored_applicable

    def test_ideation_models_integration_if_available(self) -> None:
        """If models module is available, SemanticFuture data can satisfy hypotheses."""
        try:
            from jugeo.ideation.semantic_futures.models import (  # noqa: F401
                SemanticFuture,
                IdeationState,
            )
            future = SemanticFuture(
                future_id="integration-f",
                title="Prove the budget-optimality lemma",
                description="Key lemma for Ch. 49.",
                reachability=0.85,
                purpose_alignment=0.90,
                yield_estimate=6.0,
                cost_estimate=2.0,
            )
            ctx = {
                "budget": 20.0,
                "purpose": "budget-optimality",
                "frontier_size": 1,
                "future_id": future.future_id,
                "reachability": future.reachability,
            }
            v = TheoremVerifier()
            applicable = v.applicable_theorems(THEOREM_CATALOG, ctx)
            assert isinstance(applicable, list)
        except ImportError:
            pytest.skip("models module not available for integration test")

    def test_regimes_integration_if_available(self) -> None:
        """If regimes module is available, add its context keys and verify theorems."""
        try:
            from jugeo.ideation.regimes import IdeationRegime  # noqa: F401
            ctx = self._realistic_context()
            ctx["regime_kind"] = "cover-refinement"
            v = TheoremVerifier()
            applicable = v.applicable_theorems(THEOREM_CATALOG, ctx)
            assert isinstance(applicable, list)
        except ImportError:
            pytest.skip("regimes module not available for integration test")

    def test_scheduling_integration_if_available(self) -> None:
        """If scheduling module is available, add epoch/phase context and verify."""
        try:
            from jugeo.ideation.scheduling import IdeationSchedule  # noqa: F401
            ctx = self._realistic_context()
            ctx["epoch"] = 3
            ctx["phase"] = "growth"
            v = TheoremVerifier()
            applicable = v.applicable_theorems(THEOREM_CATALOG, ctx)
            assert isinstance(applicable, list)
        except ImportError:
            pytest.skip("scheduling module not available for integration test")


# ---------------------------------------------------------------------------
# TestTheoremDifficulty
# ---------------------------------------------------------------------------

class TestTheoremDifficulty:
    """Tests for the TheoremDifficulty enum."""

    def test_all_expected_levels_exist(self) -> None:
        """TheoremDifficulty has the expected difficulty levels."""
        expected = {"elementary", "moderate", "advanced", "deep"}
        actual = {d.value for d in TheoremDifficulty}
        assert expected.issubset(actual)

    def test_difficulty_is_string_comparable(self) -> None:
        """TheoremDifficulty values compare equal to their string equivalents."""
        assert TheoremDifficulty.MODERATE == "moderate"
        assert TheoremDifficulty.ADVANCED == "advanced"

    def test_theorem_difficulty_set_to_advanced(self) -> None:
        """A theorem can be created with ADVANCED difficulty."""
        t = _make_theorem(difficulty=TheoremDifficulty.ADVANCED)
        assert t.difficulty == TheoremDifficulty.ADVANCED

    def test_theorem_difficulty_round_trips(self) -> None:
        """Difficulty value survives to_dict/from_dict round-trip."""
        t = _make_theorem(difficulty=TheoremDifficulty.DEEP)
        restored = TheoremStatement.from_dict(t.to_dict())
        assert restored.difficulty == TheoremDifficulty.DEEP
