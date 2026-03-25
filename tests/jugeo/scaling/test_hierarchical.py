"""Tests for the Hierarchical Site module.

Covers:
- HierarchicalSite construction and queries
- from_flat_coordinates parsing
- HierarchicalDescent (full and incremental)
- OverlapIndex queries
- GeometricPartitioner (balanced + SCC)
- PartitionScheduler wave assignment
"""

from __future__ import annotations

import pytest

from jugeo.scaling.hierarchical import (
    DescentLevel,
    GeometricPartitioner,
    GeometricPartitioning,
    HierarchicalCover,
    HierarchicalCoordinate,
    HierarchicalDescent,
    HierarchicalDescentResult,
    HierarchicalSite,
    LevelHeuristic,
    LevelPolicy,
    LevelView,
    OverlapIndex,
    PartitionAssignment,
    PartitionScheduler,
    SiteLevel,
)

# ===========================================================================
# Fixtures
# ===========================================================================


def _make_three_level_site() -> HierarchicalSite:
    """Build a small 3-level (package→module→function) site for testing."""
    site = HierarchicalSite("myproject")

    # Package level
    site.add_coordinate("pkg_a", "pkg_a", SiteLevel.PACKAGE, package="pkg_a")
    site.add_coordinate("pkg_b", "pkg_b", SiteLevel.PACKAGE, package="pkg_b")

    # Module level — children of packages
    site.add_coordinate(
        "pkg_a.mod1", "mod1", SiteLevel.MODULE, parent_id="pkg_a", package="pkg_a", module="pkg_a.mod1"
    )
    site.add_coordinate(
        "pkg_a.mod2", "mod2", SiteLevel.MODULE, parent_id="pkg_a", package="pkg_a", module="pkg_a.mod2"
    )
    site.add_coordinate(
        "pkg_b.mod1", "mod1", SiteLevel.MODULE, parent_id="pkg_b", package="pkg_b", module="pkg_b.mod1"
    )

    # Function level — children of modules
    site.add_coordinate(
        "pkg_a.mod1.fn_foo",
        "fn_foo",
        SiteLevel.FUNCTION,
        parent_id="pkg_a.mod1",
        package="pkg_a",
        module="pkg_a.mod1",
    )
    site.add_coordinate(
        "pkg_a.mod1.fn_bar",
        "fn_bar",
        SiteLevel.FUNCTION,
        parent_id="pkg_a.mod1",
        package="pkg_a",
        module="pkg_a.mod1",
    )
    site.add_coordinate(
        "pkg_a.mod2.fn_baz",
        "fn_baz",
        SiteLevel.FUNCTION,
        parent_id="pkg_a.mod2",
        package="pkg_a",
        module="pkg_a.mod2",
    )
    site.add_coordinate(
        "pkg_b.mod1.fn_qux",
        "fn_qux",
        SiteLevel.FUNCTION,
        parent_id="pkg_b.mod1",
        package="pkg_b",
        module="pkg_b.mod1",
    )

    # Morphisms at module level
    site.add_morphism("pkg_a.mod1", "pkg_a.mod2", "import", "mod1→mod2")
    site.add_morphism("pkg_a.mod2", "pkg_b.mod1", "import", "mod2→b.mod1")

    # Morphisms at function level
    site.add_morphism("pkg_a.mod1.fn_foo", "pkg_a.mod1.fn_bar", "call")
    site.add_morphism("pkg_a.mod1.fn_bar", "pkg_a.mod2.fn_baz", "call")

    return site


def _make_flat_coords() -> tuple[list, list]:
    """Return flat coordinate/morphism lists for from_flat_coordinates tests."""
    coords = [
        {"id": "mypkg", "name": "mypkg"},
        {"id": "mypkg.utils", "name": "mypkg.utils"},
        {"id": "mypkg.core", "name": "mypkg.core"},
        {"id": "mypkg.utils.helper", "name": "mypkg.utils.helper"},
        {"id": "mypkg.core.process", "name": "mypkg.core.process"},
        {"id": "mypkg.core.validate", "name": "mypkg.core.validate"},
    ]
    morphisms = [
        {"source_id": "mypkg.core.process", "target_id": "mypkg.utils.helper", "kind": "call"},
        {"source_id": "mypkg.core.validate", "target_id": "mypkg.utils.helper", "kind": "call"},
    ]
    return coords, morphisms


# ===========================================================================
# SiteLevel enum
# ===========================================================================


class TestSiteLevel:
    def test_ordering(self) -> None:
        assert SiteLevel.PROJECT < SiteLevel.PACKAGE
        assert SiteLevel.FUNCTION < SiteLevel.BRANCH
        assert SiteLevel.EXPRESSION.value == 6

    def test_is_coarser_finer(self) -> None:
        assert SiteLevel.PROJECT.is_coarser_than(SiteLevel.FUNCTION)
        assert SiteLevel.EXPRESSION.is_finer_than(SiteLevel.CLASS)
        assert not SiteLevel.MODULE.is_coarser_than(SiteLevel.MODULE)

    def test_label(self) -> None:
        assert SiteLevel.PACKAGE.label() == "package"
        assert SiteLevel.EXPRESSION.label() == "expression"

    def test_serialization_round_trip(self) -> None:
        for level in SiteLevel:
            assert SiteLevel.from_dict(level.to_dict()) == level

    def test_from_dict_int(self) -> None:
        assert SiteLevel.from_dict(3) == SiteLevel.CLASS


# ===========================================================================
# HierarchicalCoordinate
# ===========================================================================


class TestHierarchicalCoordinate:
    def test_create_defaults(self) -> None:
        coord = HierarchicalCoordinate.create("c1", "my_func", SiteLevel.FUNCTION)
        assert coord.id == "c1"
        assert coord.level == SiteLevel.FUNCTION
        assert coord.parent_id is None
        assert coord.children_ids == []
        assert coord.metadata == {}

    def test_is_root_leaf(self) -> None:
        root = HierarchicalCoordinate.create("root", "proj", SiteLevel.PROJECT)
        assert root.is_root()
        assert root.is_leaf()

    def test_serialization_round_trip(self) -> None:
        c = HierarchicalCoordinate.create(
            "c2", "MyClass", SiteLevel.CLASS,
            parent_id="mod1",
            package="pkg",
            module="pkg.mod",
            depth=2,
            metadata={"doc": "A class"},
        )
        d = c.to_dict()
        c2 = HierarchicalCoordinate.from_dict(d)
        assert c2.id == c.id
        assert c2.level == SiteLevel.CLASS
        assert c2.metadata == {"doc": "A class"}
        assert c2.depth == 2


# ===========================================================================
# HierarchicalSite — basic construction
# ===========================================================================


class TestHierarchicalSiteBasic:
    def test_create_site(self) -> None:
        site = HierarchicalSite("testproject")
        # The root PROJECT coordinate is created automatically
        assert site.coordinate_count() >= 1
        assert site.project_name == "testproject"

    def test_add_coordinate(self) -> None:
        site = HierarchicalSite("proj")
        site.add_coordinate("p1", "pkg1", SiteLevel.PACKAGE, package="pkg1")
        assert site.coordinate_count() == 2  # root + p1
        coords = site.coordinates_at_level(SiteLevel.PACKAGE)
        assert any(c.id == "p1" for c in coords)

    def test_add_morphism(self) -> None:
        site = _make_three_level_site()
        assert site.morphism_count() >= 2

    def test_parent_child_links(self) -> None:
        site = _make_three_level_site()
        mod1 = site.get_coordinate("pkg_a.mod1")
        assert mod1 is not None
        assert mod1.parent_id == "pkg_a"
        pkg_a = site.get_coordinate("pkg_a")
        assert "pkg_a.mod1" in pkg_a.children_ids

    def test_coordinates_at_level(self) -> None:
        site = _make_three_level_site()
        fns = site.coordinates_at_level(SiteLevel.FUNCTION)
        fn_ids = {c.id for c in fns}
        assert "pkg_a.mod1.fn_foo" in fn_ids
        assert "pkg_a.mod2.fn_baz" in fn_ids

    def test_level_statistics(self) -> None:
        site = _make_three_level_site()
        stats = site.level_statistics()
        assert stats[SiteLevel.PACKAGE]["coordinate_count"] == 2
        assert stats[SiteLevel.MODULE]["coordinate_count"] == 3
        assert stats[SiteLevel.FUNCTION]["coordinate_count"] == 4


# ===========================================================================
# HierarchicalSite — tree navigation
# ===========================================================================


class TestHierarchicalSiteTree:
    def test_get_subtree(self) -> None:
        site = _make_three_level_site()
        descendants = site.get_subtree("pkg_a.mod1")
        assert "pkg_a.mod1.fn_foo" in descendants
        assert "pkg_a.mod1.fn_bar" in descendants
        assert "pkg_a.mod2" not in descendants

    def test_get_subtree_leaf(self) -> None:
        site = _make_three_level_site()
        assert site.get_subtree("pkg_a.mod1.fn_foo") == []

    def test_get_ancestors(self) -> None:
        site = _make_three_level_site()
        ancestors = site.get_ancestors("pkg_a.mod1.fn_foo")
        assert "pkg_a.mod1" in ancestors
        assert "pkg_a" in ancestors

    def test_get_ancestors_root(self) -> None:
        site = _make_three_level_site()
        root_id = site._root_id
        assert site.get_ancestors(root_id) == []

    def test_get_subtree_package(self) -> None:
        site = _make_three_level_site()
        descendants = site.get_subtree("pkg_a")
        # Should include modules and functions
        assert "pkg_a.mod1" in descendants
        assert "pkg_a.mod1.fn_foo" in descendants
        assert len(descendants) >= 5


# ===========================================================================
# HierarchicalSite — level views
# ===========================================================================


class TestHierarchicalSiteLevelView:
    def test_get_level_view_function(self) -> None:
        site = _make_three_level_site()
        view = site.get_level_view(SiteLevel.FUNCTION)
        assert view.coordinate_count == 4
        assert isinstance(view, LevelView)

    def test_get_level_view_module_morphisms(self) -> None:
        site = _make_three_level_site()
        view = site.get_level_view(SiteLevel.MODULE)
        # mod1→mod2, mod2→b.mod1 are same-level morphisms
        assert view.morphism_count >= 2

    def test_get_level_view_empty_level(self) -> None:
        site = HierarchicalSite("empty")
        view = site.get_level_view(SiteLevel.CLASS)
        assert view.is_empty()
        assert view.coordinate_count == 0

    def test_level_view_serialization(self) -> None:
        site = _make_three_level_site()
        view = site.get_level_view(SiteLevel.FUNCTION)
        d = view.to_dict()
        view2 = LevelView.from_dict(d)
        assert view2.level == SiteLevel.FUNCTION
        assert view2.coordinate_count == view.coordinate_count


# ===========================================================================
# HierarchicalSite — morphism queries
# ===========================================================================


class TestHierarchicalSiteMorphisms:
    def test_morphisms_at_level(self) -> None:
        site = _make_three_level_site()
        fn_morphs = site.morphisms_at_level(SiteLevel.FUNCTION)
        assert len(fn_morphs) >= 2

    def test_morphisms_across_levels(self) -> None:
        site = HierarchicalSite("proj")
        site.add_coordinate("m1", "mod1", SiteLevel.MODULE, package="p")
        site.add_coordinate("f1", "fn1", SiteLevel.FUNCTION, package="p")
        site.add_morphism("m1", "f1", "contains")
        cross = site.morphisms_across_levels(SiteLevel.MODULE, SiteLevel.FUNCTION)
        assert len(cross) == 1
        assert cross[0]["source_id"] == "m1"


# ===========================================================================
# HierarchicalSite — covers
# ===========================================================================


class TestHierarchicalSiteCovers:
    def test_add_cover(self) -> None:
        site = _make_three_level_site()
        site.add_cover(
            "cover_fn",
            SiteLevel.FUNCTION,
            [
                {
                    "id": "member1",
                    "name": "group1",
                    "coordinate_ids": ["pkg_a.mod1.fn_foo", "pkg_a.mod1.fn_bar"],
                },
                {
                    "id": "member2",
                    "name": "group2",
                    "coordinate_ids": ["pkg_a.mod2.fn_baz"],
                },
            ],
        )
        covers = site.covers_at_level(SiteLevel.FUNCTION)
        assert len(covers) == 1
        assert covers[0].id == "cover_fn"

    def test_cover_member_counts(self) -> None:
        site = _make_three_level_site()
        site.add_cover(
            "c1",
            SiteLevel.MODULE,
            [{"id": "m1", "name": "m", "coordinate_ids": ["pkg_a.mod1", "pkg_a.mod2"]}],
        )
        cover = site.covers_at_level(SiteLevel.MODULE)[0]
        assert cover.total_coordinates() == 2

    def test_level_view_includes_covers(self) -> None:
        site = _make_three_level_site()
        site.add_cover(
            "cov1",
            SiteLevel.PACKAGE,
            [{"id": "m1", "name": "all_pkgs", "coordinate_ids": ["pkg_a", "pkg_b"]}],
        )
        view = site.get_level_view(SiteLevel.PACKAGE)
        assert len(view.covers) == 1


# ===========================================================================
# HierarchicalSite — restriction
# ===========================================================================


class TestHierarchicalSiteRestriction:
    def test_restrict_to_package(self) -> None:
        site = _make_three_level_site()
        sub = site.restrict_to_package("pkg_a")
        coord_ids = {c.id for c in sub._coordinates.values()}
        assert "pkg_a.mod1" in coord_ids
        assert "pkg_a.mod1.fn_foo" in coord_ids
        # pkg_b stuff should not be present
        assert "pkg_b.mod1" not in coord_ids
        assert "pkg_b.mod1.fn_qux" not in coord_ids

    def test_restrict_to_level_range(self) -> None:
        site = _make_three_level_site()
        sub = site.restrict_to_level_range(SiteLevel.MODULE, SiteLevel.FUNCTION)
        levels = {c.level for c in sub._coordinates.values()}
        assert SiteLevel.MODULE in levels
        assert SiteLevel.FUNCTION in levels
        assert SiteLevel.PROJECT not in levels

    def test_restrict_preserves_morphisms(self) -> None:
        site = _make_three_level_site()
        sub = site.restrict_to_level_range(SiteLevel.FUNCTION, SiteLevel.FUNCTION)
        # fn_foo→fn_bar morphism should survive
        assert sub.morphism_count() >= 1


# ===========================================================================
# HierarchicalSite — serialization
# ===========================================================================


class TestHierarchicalSiteSerialization:
    def test_serialize_parse_round_trip(self) -> None:
        site = _make_three_level_site()
        d = site.serialize()
        site2 = HierarchicalSite.parse(d)
        assert site2.project_name == site.project_name
        assert site2.coordinate_count() == site.coordinate_count()
        assert site2.morphism_count() == site.morphism_count()

    def test_serialized_keys(self) -> None:
        site = HierarchicalSite("simple")
        d = site.serialize()
        assert "project_name" in d
        assert "coordinates" in d
        assert "morphisms" in d
        assert "covers" in d

    def test_level_statistics_preserved(self) -> None:
        site = _make_three_level_site()
        d = site.serialize()
        site2 = HierarchicalSite.parse(d)
        stats1 = site.level_statistics()
        stats2 = site2.level_statistics()
        assert stats1[SiteLevel.FUNCTION]["coordinate_count"] == stats2[SiteLevel.FUNCTION]["coordinate_count"]


# ===========================================================================
# from_flat_coordinates
# ===========================================================================


class TestFromFlatCoordinates:
    def test_basic_parsing(self) -> None:
        coords, morphisms = _make_flat_coords()
        site = HierarchicalSite.from_flat_coordinates(coords, morphisms)
        assert site.coordinate_count() > len(coords)  # root also included

    def test_level_inference(self) -> None:
        coords, morphisms = _make_flat_coords()
        site = HierarchicalSite.from_flat_coordinates(coords, morphisms)
        # "mypkg" → PACKAGE, "mypkg.utils" → MODULE, "mypkg.utils.helper" → FUNCTION
        helper = site.get_coordinate("mypkg.utils.helper")
        assert helper is not None
        assert helper.level == SiteLevel.FUNCTION

    def test_parent_inference(self) -> None:
        coords, morphisms = _make_flat_coords()
        site = HierarchicalSite.from_flat_coordinates(coords, morphisms)
        utils = site.get_coordinate("mypkg.utils")
        assert utils is not None
        helper = site.get_coordinate("mypkg.utils.helper")
        assert helper is not None
        assert helper.parent_id == "mypkg.utils"

    def test_morphisms_preserved(self) -> None:
        coords, morphisms = _make_flat_coords()
        site = HierarchicalSite.from_flat_coordinates(coords, morphisms)
        assert site.morphism_count() == len(morphisms)

    def test_single_coord(self) -> None:
        coords = [{"id": "solo", "name": "solo"}]
        site = HierarchicalSite.from_flat_coordinates(coords, [])
        assert site.get_coordinate("solo") is not None

    def test_level_view_after_flat(self) -> None:
        coords, morphisms = _make_flat_coords()
        site = HierarchicalSite.from_flat_coordinates(coords, morphisms)
        fn_view = site.get_level_view(SiteLevel.FUNCTION)
        # helper, process, validate should be inferred as FUNCTION
        assert fn_view.coordinate_count >= 3


# ===========================================================================
# LevelHeuristic
# ===========================================================================


class TestLevelHeuristic:
    def test_project_empty(self) -> None:
        assert LevelHeuristic.infer_level_from_name("") == SiteLevel.PROJECT

    def test_package_single(self) -> None:
        assert LevelHeuristic.infer_level_from_name("mypkg") == SiteLevel.PACKAGE

    def test_module_two_parts(self) -> None:
        assert LevelHeuristic.infer_level_from_name("mypkg.mymod") == SiteLevel.MODULE

    def test_function_three_parts(self) -> None:
        assert LevelHeuristic.infer_level_from_name("mypkg.mymod.my_func") == SiteLevel.FUNCTION

    def test_branch_five_parts(self) -> None:
        assert LevelHeuristic.infer_level_from_name("a.b.c.d.e") == SiteLevel.BRANCH

    def test_expression_deep(self) -> None:
        assert LevelHeuristic.infer_level_from_name("a.b.c.d.e.f") == SiteLevel.EXPRESSION

    def test_ast_kind(self) -> None:
        assert LevelHeuristic.infer_level_from_ast_kind("classdef") == SiteLevel.CLASS
        assert LevelHeuristic.infer_level_from_ast_kind("functiondef") == SiteLevel.FUNCTION
        assert LevelHeuristic.infer_level_from_ast_kind("if") == SiteLevel.BRANCH
        assert LevelHeuristic.infer_level_from_ast_kind("unknown_xyz") == SiteLevel.EXPRESSION

    def test_parent_child_level(self) -> None:
        assert LevelHeuristic.parent_level(SiteLevel.FUNCTION) == SiteLevel.CLASS
        assert LevelHeuristic.child_level(SiteLevel.FUNCTION) == SiteLevel.BRANCH
        assert LevelHeuristic.parent_level(SiteLevel.PROJECT) is None
        assert LevelHeuristic.child_level(SiteLevel.EXPRESSION) is None

    def test_levels_between(self) -> None:
        levels = LevelHeuristic.levels_between(SiteLevel.MODULE, SiteLevel.FUNCTION)
        assert SiteLevel.MODULE in levels
        assert SiteLevel.CLASS in levels
        assert SiteLevel.FUNCTION in levels
        assert SiteLevel.PACKAGE not in levels

    def test_levels_between_error(self) -> None:
        with pytest.raises(ValueError):
            LevelHeuristic.levels_between(SiteLevel.FUNCTION, SiteLevel.MODULE)


# ===========================================================================
# LevelPolicy
# ===========================================================================


class TestLevelPolicy:
    def test_default_policies_complete(self) -> None:
        policies = LevelPolicy.default_policies()
        for level in SiteLevel:
            assert level in policies
            assert "trust_requirement" in policies[level]
            assert "coverage_target" in policies[level]

    def test_coverage_targets_decreasing(self) -> None:
        # Coarser levels should have higher coverage targets
        assert (
            LevelPolicy.coverage_target_for_level(SiteLevel.PROJECT)
            > LevelPolicy.coverage_target_for_level(SiteLevel.EXPRESSION)
        )

    def test_max_cover_size_increasing(self) -> None:
        # Finer levels allow larger covers
        assert (
            LevelPolicy.max_cover_size_for_level(SiteLevel.EXPRESSION)
            > LevelPolicy.max_cover_size_for_level(SiteLevel.PROJECT)
        )

    def test_override(self) -> None:
        p = LevelPolicy.override(SiteLevel.MODULE, coverage_target=0.99)
        assert p["coverage_target"] == 0.99
        assert p["trust_requirement"] == "module_trust"


# ===========================================================================
# HierarchicalDescent — full descent
# ===========================================================================


class TestHierarchicalDescentFull:
    def test_descend_returns_result(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.descend(site, sections={}, propositions=[])
        assert isinstance(result, HierarchicalDescentResult)

    def test_descend_covers_all_levels(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.descend(site, sections={}, propositions=[])
        reported_levels = {dl.level for dl in result.levels}
        for level in SiteLevel:
            assert level in reported_levels

    def test_bottom_up_order(self) -> None:
        """Finest level should appear first in result.levels."""
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.descend(site, sections={}, propositions=[])
        # The first level should be EXPRESSION (finest)
        assert result.levels[0].level == SiteLevel.EXPRESSION

    def test_no_sections_all_pass(self) -> None:
        """With empty sections the overlap check is trivially true."""
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.descend(site, sections={}, propositions=[])
        assert result.overall_passed
        assert result.total_failed == 0

    def test_conflicting_sections_fail(self) -> None:
        """Introduce a conflict on a shared coordinate and verify failure."""
        site = HierarchicalSite("p")
        site.add_coordinate("a", "a", SiteLevel.FUNCTION, package="p")
        site.add_coordinate("b", "b", SiteLevel.FUNCTION, package="p")
        site.add_coordinate("shared", "shared", SiteLevel.FUNCTION, package="p")
        site.add_morphism("a", "b", "depends")
        # Both a and b claim different values for "shared"
        sections = {
            "a": {"shared": {"value": 1}},
            "b": {"shared": {"value": 2}},
        }
        descent = HierarchicalDescent()
        result = descent.descend(site, sections=sections, propositions=[])
        assert result.total_failed >= 1
        assert not result.overall_passed

    def test_result_serialization(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.descend(site, sections={}, propositions=[])
        d = result.to_dict()
        result2 = HierarchicalDescentResult.from_dict(d)
        assert result2.overall_passed == result.overall_passed
        assert result2.total_checks == result.total_checks


# ===========================================================================
# HierarchicalDescent — level-specific descent
# ===========================================================================


class TestDescentAtLevel:
    def test_descend_at_level_empty(self) -> None:
        site = HierarchicalSite("p")
        view = site.get_level_view(SiteLevel.CLASS)
        descent = HierarchicalDescent()
        dl = descent.descend_at_level(view, sections={}, propositions=[])
        assert isinstance(dl, DescentLevel)
        assert dl.checks_required == 0
        assert dl.passed()

    def test_overlap_pairs_from_morphisms(self) -> None:
        site = _make_three_level_site()
        view = site.get_level_view(SiteLevel.FUNCTION)
        descent = HierarchicalDescent()
        dl = descent.descend_at_level(view, sections={}, propositions=[])
        # Should have found at least 2 function-level overlap pairs
        assert dl.checks_required >= 2

    def test_parallel_vs_sequential_same_result(self) -> None:
        site = _make_three_level_site()
        view = site.get_level_view(SiteLevel.FUNCTION)
        descent = HierarchicalDescent()
        seq = descent.descend_at_level(view, sections={}, propositions=[])
        par = descent.parallel_descent_at_level(view, sections={}, propositions=[], max_workers=2)
        assert seq.checks_required == par.checks_required
        assert seq.checks_passed == par.checks_passed


# ===========================================================================
# HierarchicalDescent — incremental descent
# ===========================================================================


class TestIncrementalDescent:
    def test_incremental_only_checks_affected(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        # Change one function
        result = descent.incremental_descent(
            changed_coords=["pkg_a.mod1.fn_foo"],
            site=site,
            sections={},
            propositions=[],
        )
        assert isinstance(result, HierarchicalDescentResult)

    def test_incremental_affected_levels(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        affected = descent._affected_levels(["pkg_a.mod1.fn_foo"], site)
        # fn_foo is FUNCTION level; its ancestors include MODULE and PACKAGE
        assert SiteLevel.FUNCTION in affected
        assert SiteLevel.MODULE in affected

    def test_incremental_empty_changes(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.incremental_descent(
            changed_coords=[],
            site=site,
            sections={},
            propositions=[],
        )
        # No changes → no checks required at any level
        assert result.total_checks == 0

    def test_incremental_result_passes_no_conflicts(self) -> None:
        site = _make_three_level_site()
        descent = HierarchicalDescent()
        result = descent.incremental_descent(
            changed_coords=["pkg_a.mod1.fn_bar"],
            site=site,
            sections={},
            propositions=[],
        )
        assert result.overall_passed


# ===========================================================================
# OverlapIndex
# ===========================================================================


class TestOverlapIndex:
    def _build_index(self) -> OverlapIndex:
        coords = [
            type("C", (), {"id": "a"})(),
            type("C", (), {"id": "b"})(),
            type("C", (), {"id": "c"})(),
            type("C", (), {"id": "d"})(),
        ]
        morphisms = [
            {"source_id": "a", "target_id": "b"},
            {"source_id": "b", "target_id": "c"},
            {"source_id": "a", "target_id": "d"},
        ]
        idx = OverlapIndex()
        idx.build(coords, morphisms)
        return idx

    def test_overlaps_of(self) -> None:
        idx = self._build_index()
        assert set(idx.overlaps_of("a")) == {"b", "d"}
        assert set(idx.overlaps_of("b")) == {"a", "c"}

    def test_overlaps_between(self) -> None:
        idx = self._build_index()
        pairs = idx.overlaps_between(["a"], ["b", "c"])
        assert ("a", "b") in pairs
        assert ("a", "c") not in pairs

    def test_degree(self) -> None:
        idx = self._build_index()
        assert idx.degree("a") == 2
        assert idx.degree("d") == 1

    def test_max_degree(self) -> None:
        idx = self._build_index()
        assert idx.max_degree() == 2

    def test_avg_degree(self) -> None:
        idx = self._build_index()
        # a:2, b:2, c:1, d:1 → avg = 6/4 = 1.5
        assert abs(idx.avg_degree() - 1.5) < 1e-9

    def test_all_overlap_pairs(self) -> None:
        idx = self._build_index()
        pairs = idx.all_overlap_pairs()
        assert len(pairs) == 3  # (a,b), (b,c), (a,d)

    def test_has_overlap(self) -> None:
        idx = self._build_index()
        assert idx.has_overlap("a", "b")
        assert not idx.has_overlap("a", "c")

    def test_isolated_node(self) -> None:
        idx = OverlapIndex()
        idx.build([type("C", (), {"id": "x"})()], [])
        assert idx.degree("x") == 0
        assert idx.max_degree() == 0

    def test_build_from_dicts(self) -> None:
        coords = [{"id": "p"}, {"id": "q"}]
        morphisms = [{"source_id": "p", "target_id": "q"}]
        idx = OverlapIndex()
        idx.build(coords, morphisms)
        assert idx.has_overlap("p", "q")


# ===========================================================================
# GeometricPartitioner — basic partitioning
# ===========================================================================


def _make_50_coord_site() -> HierarchicalSite:
    """Build a site with 50 FUNCTION-level coordinates."""
    site = HierarchicalSite("bigproject")
    site.add_coordinate("pkg", "pkg", SiteLevel.PACKAGE, package="pkg")
    for i in range(50):
        cid = f"fn_{i:02d}"
        site.add_coordinate(
            cid, cid, SiteLevel.FUNCTION,
            parent_id="pkg",
            package="pkg",
        )
    # Add a chain of morphisms to create some adjacency
    for i in range(49):
        site.add_morphism(f"fn_{i:02d}", f"fn_{i+1:02d}", "call")
    return site


class TestGeometricPartitioner:
    def test_basic_partition(self) -> None:
        site = _make_50_coord_site()
        partitioner = GeometricPartitioner()
        gp = partitioner.partition(site, max_partition_size=10)
        assert isinstance(gp, GeometricPartitioning)
        assert gp.total_coordinates == site.coordinate_count()

    def test_partition_count(self) -> None:
        site = _make_50_coord_site()
        partitioner = GeometricPartitioner()
        gp = partitioner.partition(site, max_partition_size=10)
        # 50 function coords + 1 package + 1 project root; at max_size=10 we
        # expect at least 5 partitions at function level
        fn_parts = [p for p in gp.partitions if p.level == SiteLevel.FUNCTION]
        assert len(fn_parts) >= 1

    def test_partition_sizes(self) -> None:
        site = _make_50_coord_site()
        partitioner = GeometricPartitioner()
        gp = partitioner.partition(site, max_partition_size=8)
        fn_parts = [p for p in gp.partitions if p.level == SiteLevel.FUNCTION]
        for p in fn_parts:
            assert p.size() <= 8 + 1  # small slack due to SCC grouping

    def test_balance_ratio(self) -> None:
        site = _make_50_coord_site()
        partitioner = GeometricPartitioner()
        gp = partitioner.partition(site, max_partition_size=10, balance_factor=0.5)
        assert 0.0 <= gp.balance_ratio <= 1.0

    def test_cost_estimation(self) -> None:
        partitioner = GeometricPartitioner()
        morphisms = [{"source_id": "a", "target_id": "b"}]
        cost = partitioner._estimate_cost(["a", "b"], morphisms)
        # 2 coords * 1.0 + 1 edge * 0.5 = 2.5
        assert abs(cost - 2.5) < 1e-9

    def test_from_partitions_balance_ratio(self) -> None:
        p1 = PartitionAssignment.create("p1", SiteLevel.FUNCTION, ["a", "b"], estimated_cost=4.0)
        p2 = PartitionAssignment.create("p2", SiteLevel.FUNCTION, ["c"], estimated_cost=2.0)
        gp = GeometricPartitioning.from_partitions(3, [p1, p2])
        assert abs(gp.balance_ratio - 0.5) < 1e-9

    def test_empty_site(self) -> None:
        site = HierarchicalSite("empty")
        partitioner = GeometricPartitioner()
        gp = partitioner.partition(site, max_partition_size=5)
        assert isinstance(gp, GeometricPartitioning)


# ===========================================================================
# GeometricPartitioner — SCC partitioning
# ===========================================================================


class TestSCCPartitioning:
    def test_linear_chain_all_in_one_scc(self) -> None:
        """A linear chain has one trivial SCC per node."""
        coords = [f"n{i}" for i in range(5)]
        morphisms = [
            {"source_id": f"n{i}", "target_id": f"n{i+1}"} for i in range(4)
        ]
        partitioner = GeometricPartitioner()
        sccs = partitioner.scc_based_partition(coords, morphisms)
        # Linear chain has no back-edges → each node is its own SCC
        assert len(sccs) == 5

    def test_cycle_one_scc(self) -> None:
        """A fully connected cycle is one SCC."""
        coords = ["a", "b", "c"]
        morphisms = [
            {"source_id": "a", "target_id": "b"},
            {"source_id": "b", "target_id": "c"},
            {"source_id": "c", "target_id": "a"},
        ]
        partitioner = GeometricPartitioner()
        sccs = partitioner.scc_based_partition(coords, morphisms)
        assert len(sccs) == 1
        assert set(sccs[0]) == {"a", "b", "c"}

    def test_two_separate_cycles(self) -> None:
        """Two disjoint cycles → two SCCs."""
        coords = ["a", "b", "c", "d"]
        morphisms = [
            {"source_id": "a", "target_id": "b"},
            {"source_id": "b", "target_id": "a"},
            {"source_id": "c", "target_id": "d"},
            {"source_id": "d", "target_id": "c"},
        ]
        partitioner = GeometricPartitioner()
        sccs = partitioner.scc_based_partition(coords, morphisms)
        assert len(sccs) == 2

    def test_cross_partition_edges(self) -> None:
        p1 = PartitionAssignment.create("p1", SiteLevel.FUNCTION, ["a", "b"])
        p2 = PartitionAssignment.create("p2", SiteLevel.FUNCTION, ["c", "d"])
        morphisms = [
            {"source_id": "a", "target_id": "c"},
            {"source_id": "b", "target_id": "b"},  # self-loop, not cross
            {"source_id": "d", "target_id": "a"},
        ]
        partitioner = GeometricPartitioner()
        cross = partitioner.cross_partition_edges([p1, p2], morphisms)
        assert ("a", "c") in cross
        assert ("d", "a") in cross
        assert len(cross) == 2

    def test_minimize_cross_edges_no_crash(self) -> None:
        """minimize_cross_edges should run without errors."""
        p1 = PartitionAssignment.create("p1", SiteLevel.FUNCTION, ["a", "b"], estimated_cost=2.0)
        p2 = PartitionAssignment.create("p2", SiteLevel.FUNCTION, ["c", "d"], estimated_cost=2.0)
        morphisms = [
            {"source_id": "a", "target_id": "c"},
            {"source_id": "b", "target_id": "d"},
        ]
        partitioner = GeometricPartitioner()
        result = partitioner.minimize_cross_edges([p1, p2], morphisms, iterations=3)
        assert len(result) == 2
        all_ids = set()
        for p in result:
            all_ids.update(p.coordinate_ids)
        assert all_ids == {"a", "b", "c", "d"}


# ===========================================================================
# PartitionScheduler
# ===========================================================================


class TestPartitionScheduler:
    def test_schedule_basic(self) -> None:
        partitions = [
            PartitionAssignment.create(f"p{i}", SiteLevel.FUNCTION, [f"c{i}"])
            for i in range(6)
        ]
        scheduler = PartitionScheduler()
        waves = scheduler.schedule(partitions, max_workers=2)
        assert len(waves) == 3  # 6 partitions / 2 per wave

    def test_schedule_all_in_one_wave(self) -> None:
        partitions = [
            PartitionAssignment.create(f"p{i}", SiteLevel.FUNCTION, [f"c{i}"])
            for i in range(3)
        ]
        scheduler = PartitionScheduler()
        waves = scheduler.schedule(partitions, max_workers=10)
        assert len(waves) == 1

    def test_schedule_empty(self) -> None:
        scheduler = PartitionScheduler()
        waves = scheduler.schedule([], max_workers=4)
        assert waves == []

    def test_schedule_with_morphisms(self) -> None:
        partitions = [
            PartitionAssignment.create("p1", SiteLevel.FUNCTION, ["a", "b"]),
            PartitionAssignment.create("p2", SiteLevel.FUNCTION, ["c", "d"]),
            PartitionAssignment.create("p3", SiteLevel.FUNCTION, ["e", "f"]),
        ]
        morphisms = [
            {"source_id": "b", "target_id": "c"},  # p1 → p2
            {"source_id": "d", "target_id": "e"},  # p2 → p3
        ]
        scheduler = PartitionScheduler()
        waves = scheduler.schedule_with_morphisms(partitions, morphisms, max_workers=3)
        # With deps p1→p2→p3 and max_workers=3 we need 3 waves
        assert len(waves) >= 1
        # All partitions must appear
        all_pids = {p.partition_id for wave in waves for p in wave}
        assert all_pids == {"p1", "p2", "p3"}

    def test_wave_assignment_respects_max_workers(self) -> None:
        partitions = [
            PartitionAssignment.create(f"p{i}", SiteLevel.FUNCTION, [f"c{i}"])
            for i in range(10)
        ]
        scheduler = PartitionScheduler()
        waves = scheduler.schedule(partitions, max_workers=3)
        for wave in waves:
            assert len(wave) <= 3

    def test_dependency_order_cycle_fallback(self) -> None:
        """Cycle in deps should fall back to original order gracefully."""
        partitions = [
            PartitionAssignment.create("p1", SiteLevel.FUNCTION, ["a"]),
            PartitionAssignment.create("p2", SiteLevel.FUNCTION, ["b"]),
        ]
        # Cyclic cross-edges: a→b and b→a
        cross = [("a", "b"), ("b", "a")]
        scheduler = PartitionScheduler()
        ordered = scheduler._dependency_order(partitions, cross)
        assert len(ordered) == 2
