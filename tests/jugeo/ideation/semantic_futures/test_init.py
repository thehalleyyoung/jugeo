"""Tests for the jugeo.ideation.semantic_futures package __init__.

Validates that the package:
  * Exposes the correct dunder attributes (__version__, __theory_chapter__).
  * Re-exports all expected public symbols from its submodules.
  * Defines a well-formed __all__ list whose contents are all importable.
  * Provides a meaningful module docstring referencing Ch. 49 / semantic futures.
  * Supports a complete ideation workflow using only top-level imports.

Theory chapter: 49 — Semantic Futures for Open-Ended Ideation.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

import jugeo.ideation.semantic_futures as sf


# ---------------------------------------------------------------------------
# 1.  Package-level dunder attributes
# ---------------------------------------------------------------------------


class TestPackageDunders:
    """Package must declare __version__ and __theory_chapter__."""

    def test_version_attribute_exists(self) -> None:
        """__version__ must be defined on the package."""
        assert hasattr(sf, "__version__"), "__version__ is not defined"

    def test_version_is_string(self) -> None:
        """__version__ must be a non-empty string."""
        assert isinstance(sf.__version__, str)
        assert len(sf.__version__) > 0

    def test_version_is_semver_like(self) -> None:
        """__version__ should look like a semantic version (X.Y or X.Y.Z)."""
        parts = sf.__version__.split(".")
        assert len(parts) >= 2, f"version '{sf.__version__}' does not look like semver"
        for part in parts:
            assert part.isdigit() or part != "", f"non-numeric version part: '{part}'"

    def test_theory_chapter_attribute_exists(self) -> None:
        """__theory_chapter__ must be defined on the package."""
        assert hasattr(sf, "__theory_chapter__"), "__theory_chapter__ is not defined"

    def test_theory_chapter_equals_49(self) -> None:
        """The theory chapter for semantic futures is Ch. 49."""
        assert str(sf.__theory_chapter__) == "49", (
            f"expected __theory_chapter__ == '49', got {sf.__theory_chapter__!r}"
        )

    def test_docstring_non_empty(self) -> None:
        """Package docstring must not be empty."""
        assert sf.__doc__, "Package __doc__ is empty or None"
        assert len(sf.__doc__.strip()) > 0

    def test_docstring_mentions_semantic_futures_or_ch49(self) -> None:
        """Docstring must reference 'semantic futures' or 'Ch. 49' (case-insensitive)."""
        doc = (sf.__doc__ or "").lower()
        assert "semantic futures" in doc or "ch. 49" in doc or "chapter 49" in doc, (
            "Package docstring does not mention 'semantic futures' or 'Ch. 49'"
        )


# ---------------------------------------------------------------------------
# 2.  __all__ structure
# ---------------------------------------------------------------------------


class TestAllDefinition:
    """__all__ must be present, be a list of strings, and be consistent."""

    def test_all_is_defined(self) -> None:
        """__all__ attribute must exist on the package."""
        assert hasattr(sf, "__all__"), "__all__ is not defined"

    def test_all_is_list(self) -> None:
        """__all__ must be a list (not a tuple or set)."""
        assert isinstance(sf.__all__, list), f"__all__ is {type(sf.__all__)}, expected list"

    def test_all_non_empty(self) -> None:
        """__all__ must contain at least one entry."""
        assert len(sf.__all__) > 0

    def test_all_contains_only_strings(self) -> None:
        """Every element of __all__ must be a string."""
        for name in sf.__all__:
            assert isinstance(name, str), f"non-string in __all__: {name!r}"

    def test_all_entries_are_importable(self) -> None:
        """Every name in __all__ must resolve to an attribute on the package."""
        missing = [name for name in sf.__all__ if not hasattr(sf, name)]
        assert not missing, f"Names in __all__ not found on package: {missing}"

    def test_all_has_no_duplicates(self) -> None:
        """__all__ must not contain duplicate entries."""
        seen: set[str] = set()
        dupes: list[str] = []
        for name in sf.__all__:
            if name in seen:
                dupes.append(name)
            seen.add(name)
        assert not dupes, f"Duplicate entries in __all__: {dupes}"


# ---------------------------------------------------------------------------
# 3.  Model symbols (from models submodule)
# ---------------------------------------------------------------------------


class TestModelExports:
    """Core model classes must be importable from the package top level."""

    @pytest.mark.parametrize(
        "symbol",
        [
            "SemanticFuture",
            "FutureState",
            "PurposeFunction",
            "IdeationState",
            "FutureTag",
        ],
    )
    def test_model_symbol_importable(self, symbol: str) -> None:
        """Each model symbol must be accessible as sf.<symbol>."""
        assert hasattr(sf, symbol), f"sf.{symbol} is not accessible"
        obj = getattr(sf, symbol)
        assert obj is not None

    def test_semantic_future_is_class(self) -> None:
        """sf.SemanticFuture must be a class."""
        assert isinstance(sf.SemanticFuture, type)

    def test_future_state_is_class(self) -> None:
        """sf.FutureState must be a class."""
        assert isinstance(sf.FutureState, type)

    def test_purpose_function_is_class(self) -> None:
        """sf.PurposeFunction must be a class."""
        assert isinstance(sf.PurposeFunction, type)

    def test_ideation_state_is_class(self) -> None:
        """sf.IdeationState must be a class."""
        assert isinstance(sf.IdeationState, type)

    def test_future_tag_is_enum(self) -> None:
        """sf.FutureTag must be an Enum subclass."""
        import enum

        assert issubclass(sf.FutureTag, enum.Enum)


# ---------------------------------------------------------------------------
# 4.  Submodule s01 — FutureGenerator, FutureExpander, FuturePruner
# ---------------------------------------------------------------------------


class TestS01Exports:
    """s01 symbols must be importable from the package top level."""

    @pytest.mark.parametrize(
        "symbol",
        ["FutureGenerator", "FutureExpander", "FuturePruner"],
    )
    def test_s01_symbol_importable(self, symbol: str) -> None:
        """sf.<symbol> (from s01) must exist."""
        assert hasattr(sf, symbol), f"sf.{symbol} (s01) is not accessible"

    def test_future_generator_is_class(self) -> None:
        """sf.FutureGenerator must be a class."""
        assert isinstance(sf.FutureGenerator, type)

    def test_future_expander_is_class(self) -> None:
        """sf.FutureExpander must be a class."""
        assert isinstance(sf.FutureExpander, type)

    def test_future_pruner_is_class(self) -> None:
        """sf.FuturePruner must be a class."""
        assert isinstance(sf.FuturePruner, type)


# ---------------------------------------------------------------------------
# 5.  Submodule s02 — ReachabilityEstimator, ReachabilityModel
# ---------------------------------------------------------------------------


class TestS02Exports:
    """s02 (reachability) symbols must be importable."""

    @pytest.mark.parametrize("symbol", ["ReachabilityEstimator", "ReachabilityModel"])
    def test_s02_symbol_importable(self, symbol: str) -> None:
        """sf.<symbol> (from s02) must exist."""
        assert hasattr(sf, symbol), f"sf.{symbol} (s02) is not accessible"

    def test_reachability_estimator_is_class(self) -> None:
        """sf.ReachabilityEstimator must be a class."""
        assert isinstance(sf.ReachabilityEstimator, type)

    def test_reachability_model_is_class(self) -> None:
        """sf.ReachabilityModel must be a class."""
        assert isinstance(sf.ReachabilityModel, type)


# ---------------------------------------------------------------------------
# 6.  Submodule s03 — PurposeAligner, AlignmentScore
# ---------------------------------------------------------------------------


class TestS03Exports:
    """s03 (purpose alignment) symbols must be importable."""

    @pytest.mark.parametrize("symbol", ["PurposeAligner", "AlignmentScore"])
    def test_s03_symbol_importable(self, symbol: str) -> None:
        """sf.<symbol> (from s03) must exist."""
        assert hasattr(sf, symbol), f"sf.{symbol} (s03) is not accessible"

    def test_purpose_aligner_is_class(self) -> None:
        """sf.PurposeAligner must be a class."""
        assert isinstance(sf.PurposeAligner, type)

    def test_alignment_score_is_class(self) -> None:
        """sf.AlignmentScore must be a class."""
        assert isinstance(sf.AlignmentScore, type)


# ---------------------------------------------------------------------------
# 7.  Submodule s04 — BudgetAllocator, BudgetConstraint
# ---------------------------------------------------------------------------


class TestS04Exports:
    """s04 (budget) symbols must be importable."""

    @pytest.mark.parametrize("symbol", ["BudgetAllocator", "BudgetConstraint"])
    def test_s04_symbol_importable(self, symbol: str) -> None:
        """sf.<symbol> (from s04) must exist."""
        assert hasattr(sf, symbol), f"sf.{symbol} (s04) is not accessible"

    def test_budget_allocator_is_class(self) -> None:
        """sf.BudgetAllocator must be a class."""
        assert isinstance(sf.BudgetAllocator, type)

    def test_budget_constraint_is_class(self) -> None:
        """sf.BudgetConstraint must be a class."""
        assert isinstance(sf.BudgetConstraint, type)


# ---------------------------------------------------------------------------
# 8.  Algorithm symbols
# ---------------------------------------------------------------------------


class TestAlgorithmExports:
    """All five algorithm classes must be importable from the top level."""

    @pytest.mark.parametrize(
        "symbol",
        [
            "FutureSearchAlgorithm",
            "BeamSearchFutures",
            "GreedyFutureSearch",
            "DiversifiedSearch",
            "ArchiveBasedSearch",
            "PurposeDirectedSearch",
            "SearchConfig",
            "SearchResult",
            "SearchAlgorithmFactory",
            "SearchComparator",
        ],
    )
    def test_algorithm_symbol_importable(self, symbol: str) -> None:
        """sf.<symbol> (from algorithms) must be accessible."""
        assert hasattr(sf, symbol), f"sf.{symbol} (algorithms) is not accessible"

    def test_beam_search_is_class(self) -> None:
        """sf.BeamSearchFutures must be a class."""
        assert isinstance(sf.BeamSearchFutures, type)

    def test_greedy_is_class(self) -> None:
        """sf.GreedyFutureSearch must be a class."""
        assert isinstance(sf.GreedyFutureSearch, type)

    def test_diversified_is_class(self) -> None:
        """sf.DiversifiedSearch must be a class."""
        assert isinstance(sf.DiversifiedSearch, type)

    def test_archive_based_is_class(self) -> None:
        """sf.ArchiveBasedSearch must be a class."""
        assert isinstance(sf.ArchiveBasedSearch, type)

    def test_purpose_directed_is_class(self) -> None:
        """sf.PurposeDirectedSearch must be a class."""
        assert isinstance(sf.PurposeDirectedSearch, type)

    def test_search_config_is_class(self) -> None:
        """sf.SearchConfig must be a class."""
        assert isinstance(sf.SearchConfig, type)

    def test_search_result_is_class(self) -> None:
        """sf.SearchResult must be a class."""
        assert isinstance(sf.SearchResult, type)


# ---------------------------------------------------------------------------
# 9.  Integration symbols
# ---------------------------------------------------------------------------


class TestIntegrationExports:
    """Integration and event-bus symbols must be importable from the top level."""

    @pytest.mark.parametrize(
        "symbol",
        [
            "SemanticFuturesIntegration",
            "FuturesEventBus",
            "CopilotFuturesAdvisor",
            "IntegrationHealthCheck",
            "EventKind",
            "FutureEvent",
            "IntegrationStatus",
        ],
    )
    def test_integration_symbol_importable(self, symbol: str) -> None:
        """sf.<symbol> (from integration) must be accessible."""
        assert hasattr(sf, symbol), f"sf.{symbol} (integration) is not accessible"

    def test_semantic_futures_integration_is_class(self) -> None:
        """sf.SemanticFuturesIntegration must be a class."""
        assert isinstance(sf.SemanticFuturesIntegration, type)

    def test_futures_event_bus_is_class(self) -> None:
        """sf.FuturesEventBus must be a class."""
        assert isinstance(sf.FuturesEventBus, type)

    def test_event_kind_is_enum(self) -> None:
        """sf.EventKind must be an Enum subclass."""
        import enum

        assert issubclass(sf.EventKind, enum.Enum)

    def test_integration_status_is_enum(self) -> None:
        """sf.IntegrationStatus must be an Enum subclass."""
        import enum

        assert issubclass(sf.IntegrationStatus, enum.Enum)


# ---------------------------------------------------------------------------
# 10.  Theorems symbols
# ---------------------------------------------------------------------------


class TestTheoremExports:
    """Theorem-related symbols must be importable from the top level."""

    def test_theorem_statement_importable(self) -> None:
        """sf.TheoremStatement must exist."""
        assert hasattr(sf, "TheoremStatement"), "sf.TheoremStatement not accessible"

    def test_theorem_catalog_importable(self) -> None:
        """sf.THEOREM_CATALOG must exist."""
        assert hasattr(sf, "THEOREM_CATALOG"), "sf.THEOREM_CATALOG not accessible"

    def test_theorem_statement_is_class(self) -> None:
        """sf.TheoremStatement must be a class."""
        assert isinstance(sf.TheoremStatement, type)

    def test_theorem_catalog_is_dict_or_mapping(self) -> None:
        """sf.THEOREM_CATALOG must be a dict or Mapping."""
        import collections.abc

        assert isinstance(sf.THEOREM_CATALOG, (dict, collections.abc.Mapping))


# ---------------------------------------------------------------------------
# 11.  TestIntegrationPackage — workflow using only top-level imports
# ---------------------------------------------------------------------------


class TestIntegrationPackage:
    """Smoke-test a complete ideation workflow via the package top level only."""

    def test_construct_future_state(self) -> None:
        """sf.FutureState can be instantiated through the top-level import."""
        from datetime import datetime

        fs = sf.FutureState(
            state_id="wf-s1",
            theorem_portfolio=("T1", "T2"),
            known_kinds=("K1",),
            semantic_embedding=(0.5, 0.5),
            timestamp=datetime.now(),
        )
        assert fs.state_id == "wf-s1"

    def test_construct_purpose_function(self) -> None:
        """sf.PurposeFunction can be instantiated through the top-level import."""
        pf = sf.PurposeFunction(
            purpose_id="wf-p1",
            domain="algebra",
            utility_weights={"yield": 0.7, "novelty": 0.3},
            alignment_threshold=0.5,
            description="Workflow purpose",
        )
        assert pf.domain == "algebra"

    def test_construct_semantic_future(self) -> None:
        """sf.SemanticFuture can be instantiated through the top-level import."""
        f = sf.SemanticFuture(
            future_id="wf-f1",
            delta="Introduce Galois bridge",
            reachability=0.85,
            purpose_alignment=0.9,
            expected_yield=6.0,
            cost_estimate=2.0,
            tags=(sf.FutureTag.EXTENSION,),
            metadata={},
        )
        assert f.future_id == "wf-f1"

    def test_construct_ideation_state(self) -> None:
        """sf.IdeationState can be instantiated through the top-level import."""
        from datetime import datetime

        fs = sf.FutureState(
            state_id="wf-s2",
            theorem_portfolio=("T1",),
            known_kinds=("K1",),
            semantic_embedding=(0.3,),
            timestamp=datetime.now(),
        )
        pf = sf.PurposeFunction(
            purpose_id="wf-p2",
            domain="topology",
            utility_weights={"yield": 1.0},
            alignment_threshold=0.3,
            description="Topology purpose",
        )
        future = sf.SemanticFuture(
            future_id="wf-f2",
            delta="Topological gluing",
            reachability=0.7,
            purpose_alignment=0.8,
            expected_yield=3.0,
            cost_estimate=1.0,
            tags=(sf.FutureTag.BRIDGE,),
            metadata={},
        )
        state = sf.IdeationState(
            state_id="wf-is1",
            current_state=fs,
            purpose=pf,
            reachable_futures=[future],
            budget_remaining=5.0,
            archive=[],
        )
        assert state.state_id == "wf-is1"
        assert len(state.reachable_futures) == 1

    def test_run_beam_search_via_top_level(self) -> None:
        """BeamSearchFutures can be run using only top-level sf.* imports."""
        from datetime import datetime

        fs = sf.FutureState(
            state_id="wf-s3",
            theorem_portfolio=("T1", "T2", "T3"),
            known_kinds=("K1", "K2"),
            semantic_embedding=(0.4, 0.3, 0.3),
            timestamp=datetime.now(),
        )
        pf = sf.PurposeFunction(
            purpose_id="wf-p3",
            domain="algebra",
            utility_weights={"yield": 0.6, "novelty": 0.4},
            alignment_threshold=0.5,
            description="Algebraic search",
        )
        futures = [
            sf.SemanticFuture(
                future_id=f"wf-f{i}",
                delta=f"Algebraic extension {i}",
                reachability=0.9 - i * 0.1,
                purpose_alignment=0.8 - i * 0.05,
                expected_yield=5.0 - i * 0.5,
                cost_estimate=1.0 + i * 0.2,
                tags=(sf.FutureTag.EXTENSION,),
                metadata={},
            )
            for i in range(4)
        ]
        state = sf.IdeationState(
            state_id="wf-is2",
            current_state=fs,
            purpose=pf,
            reachable_futures=futures,
            budget_remaining=10.0,
            archive=[],
        )
        cfg = sf.SearchConfig(beam_width=2)
        algo = sf.BeamSearchFutures(cfg)
        result = algo.search(state)
        assert isinstance(result, sf.SearchResult)
        if result.best_future is not None:
            ids = {f.future_id for f in futures}
            assert result.best_future.future_id in ids

    def test_event_bus_via_top_level(self) -> None:
        """FuturesEventBus can be used end-to-end via top-level sf.* imports."""
        bus = sf.FuturesEventBus()
        received: list = []
        bus.subscribe(sf.EventKind.SEARCH_COMPLETED, received.append)
        ev = sf.FutureEvent(
            event_id="wf-ev1",
            kind=sf.EventKind.SEARCH_COMPLETED,
            payload={"count": 2},
            source="workflow-test",
            timestamp=__import__("datetime").datetime.now(),
        )
        bus.publish(ev)
        assert len(received) == 1

    def test_integration_connect_via_top_level(self) -> None:
        """SemanticFuturesIntegration can be wired up via top-level imports."""
        bus = sf.FuturesEventBus()
        integ = sf.SemanticFuturesIntegration(event_bus=bus)

        class _Sched:
            pass

        integ.connect_to_scheduler(_Sched())
        status = integ.status()
        assert isinstance(status, dict)

    def test_factory_create_via_top_level(self) -> None:
        """SearchAlgorithmFactory.create() is accessible through sf.*."""
        cfg = sf.SearchConfig(beam_width=2)
        algo = sf.SearchAlgorithmFactory.create("GreedyFutureSearch", cfg)
        assert isinstance(algo, sf.GreedyFutureSearch)

    def test_copilot_advisor_via_top_level(self) -> None:
        """CopilotFuturesAdvisor can be constructed through sf.*."""
        from datetime import datetime

        fs = sf.FutureState(
            state_id="adv-s1",
            theorem_portfolio=("T1",),
            known_kinds=("K1",),
            semantic_embedding=(0.5,),
            timestamp=datetime.now(),
        )
        pf = sf.PurposeFunction(
            purpose_id="adv-p1",
            domain="algebra",
            utility_weights={"yield": 1.0},
            alignment_threshold=0.4,
            description="Advisor test",
        )
        future = sf.SemanticFuture(
            future_id="adv-f1",
            delta="Advisor bridge",
            reachability=0.7,
            purpose_alignment=0.8,
            expected_yield=3.5,
            cost_estimate=1.0,
            tags=(sf.FutureTag.EXTENSION,),
            metadata={},
        )
        state = sf.IdeationState(
            state_id="adv-is1",
            current_state=fs,
            purpose=pf,
            reachable_futures=[future],
            budget_remaining=5.0,
            archive=[],
        )
        adv = sf.CopilotFuturesAdvisor(state=state)
        advice = adv.full_advisory()
        assert isinstance(advice, str) and len(advice) > 0


# ---------------------------------------------------------------------------
# 12.  Package is a proper Python module
# ---------------------------------------------------------------------------


class TestPackageIsProperModule:
    """Basic sanity checks that the package is a well-formed Python module."""

    def test_is_module_type(self) -> None:
        """sf must be an instance of types.ModuleType."""
        assert isinstance(sf, types.ModuleType)

    def test_has_file_attribute(self) -> None:
        """Package must have a __file__ attribute pointing to __init__.py."""
        assert hasattr(sf, "__file__")
        assert sf.__file__ is not None
        assert sf.__file__.endswith("__init__.py")

    def test_has_package_attribute(self) -> None:
        """__package__ must be set and non-empty."""
        assert sf.__package__
        assert "semantic_futures" in sf.__package__

    def test_has_path_attribute(self) -> None:
        """Being a package, sf must have __path__."""
        assert hasattr(sf, "__path__")

    def test_name_is_correct(self) -> None:
        """__name__ must match the fully-qualified module path."""
        assert sf.__name__ == "jugeo.ideation.semantic_futures"

    def test_submodules_importable_directly(self) -> None:
        """Key submodules (algorithms, integration, models) must be importable."""
        import importlib

        for submod in ("models", "algorithms", "integration", "theorems"):
            mod = importlib.import_module(f"jugeo.ideation.semantic_futures.{submod}")
            assert isinstance(mod, types.ModuleType), f"submodule {submod!r} is not a module"

    def test_package_does_not_export_private_helpers(self) -> None:
        """Names starting with '_' must not appear in __all__."""
        private_in_all = [n for n in sf.__all__ if n.startswith("_")]
        assert not private_in_all, f"Private names in __all__: {private_in_all}"
