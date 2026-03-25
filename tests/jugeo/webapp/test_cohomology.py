"""Tests for the webapp Čech cohomology module."""
from __future__ import annotations

import pytest

from jugeo.webapp.cohomology.models import (
    NerveCell,
    Cochain,
    CohomologyGroup,
    CechComplex,
)
from jugeo.webapp.cohomology.cech_computation import CechCohomologyComputer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def computer() -> CechCohomologyComputer:
    return CechCohomologyComputer()


@pytest.fixture
def sample_vertex() -> NerveCell:
    return NerveCell(
        dimension=0,
        vertices=["python"],
        data={"coordinates": [{"name": "route_index"}]},
        cell_id="python",
    )


@pytest.fixture
def sample_edge() -> NerveCell:
    return NerveCell(
        dimension=1,
        vertices=["python", "template"],
        data={"interface": "CONTEXT_PROVISION", "shared_coordinates": []},
        cell_id="python|template",
    )


@pytest.fixture
def simple_covering() -> dict:
    return {
        "python": [{"name": "user_var"}],
        "template": [{"name": "user_var"}],
    }


@pytest.fixture
def multi_layer_covering() -> dict:
    return {
        "python": [{"name": "user"}],
        "template": [{"name": "user"}],
        "javascript": [{"name": "fetchUser"}],
        "html": [{"name": "user_element"}],
        "css": [{"name": "user-card"}],
    }


# ===================================================================
# NerveCell tests
# ===================================================================

class TestNerveCell:

    def test_nerve_cell_creation(self, sample_vertex):
        assert sample_vertex.dimension == 0
        assert sample_vertex.vertices == ["python"]
        assert sample_vertex.cell_id == "python"

    def test_nerve_cell_to_dict(self, sample_vertex):
        d = sample_vertex.to_dict()
        assert d["dimension"] == 0
        assert d["vertices"] == ["python"]
        assert "data" in d
        assert "cell_id" in d

    def test_nerve_cell_from_dict_roundtrip(self, sample_vertex):
        d = sample_vertex.to_dict()
        restored = NerveCell.from_dict(d)
        assert restored.dimension == sample_vertex.dimension
        assert restored.vertices == sample_vertex.vertices
        assert restored.data == sample_vertex.data
        assert restored.cell_id == sample_vertex.cell_id

    def test_nerve_cell_dimension_0_is_vertex(self, sample_vertex):
        assert sample_vertex.dimension == 0
        assert len(sample_vertex.vertices) == 1

    def test_nerve_cell_dimension_1_is_edge(self, sample_edge):
        assert sample_edge.dimension == 1
        assert len(sample_edge.vertices) == 2

    def test_nerve_cell_defaults(self):
        nc = NerveCell(dimension=0)
        assert nc.vertices == []
        assert nc.data == {}
        assert nc.cell_id == ""


# ===================================================================
# Cochain tests
# ===================================================================

class TestCochain:

    def test_cochain_creation(self, sample_vertex):
        c = Cochain(
            dimension=0,
            cells=[sample_vertex],
            values={"python": {"coordinates": []}},
        )
        assert c.dimension == 0
        assert len(c.cells) == 1

    def test_cochain_to_dict(self, sample_vertex):
        c = Cochain(
            dimension=0,
            cells=[sample_vertex],
            values={"python": {"key": "val"}},
        )
        d = c.to_dict()
        assert d["dimension"] == 0
        assert "cells" in d
        assert "values" in d
        assert len(d["cells"]) == 1

    def test_cochain_from_dict_roundtrip(self, sample_vertex):
        c = Cochain(
            dimension=0,
            cells=[sample_vertex],
            values={"python": {"test": True}},
        )
        d = c.to_dict()
        restored = Cochain.from_dict(d)
        assert restored.dimension == c.dimension
        assert len(restored.cells) == len(c.cells)
        assert restored.values == c.values

    def test_cochain_values_dict(self, sample_vertex):
        c = Cochain(
            dimension=0,
            cells=[sample_vertex],
            values={"python": {"x": 1}, "template": {"y": 2}},
        )
        assert isinstance(c.values, dict)
        assert "python" in c.values
        assert "template" in c.values

    def test_cochain_empty(self):
        c = Cochain(dimension=1)
        assert c.cells == []
        assert c.values == {}


# ===================================================================
# CohomologyGroup tests
# ===================================================================

class TestCohomologyGroup:

    def test_cohomology_group_trivial(self):
        cg = CohomologyGroup(dimension=0, generators=[], relations=[])
        assert cg.is_trivial is True
        assert cg.rank == 0

    def test_cohomology_group_nontrivial(self):
        cg = CohomologyGroup(
            dimension=1,
            generators=["gen1"],
            relations=[],
        )
        assert cg.is_trivial is False
        assert cg.rank == 1

    def test_cohomology_group_rank_with_relations(self):
        cg = CohomologyGroup(
            dimension=1,
            generators=["gen1", "gen2", "gen3"],
            relations=["rel1"],
        )
        assert cg.rank == 2
        assert cg.is_trivial is False

    def test_cohomology_group_to_dict(self):
        cg = CohomologyGroup(
            dimension=1,
            generators=["g1"],
            relations=[],
            interpretation="H¹ has rank 1",
        )
        d = cg.to_dict()
        assert d["dimension"] == 1
        assert d["generators"] == ["g1"]
        assert d["rank"] == 1
        assert d["is_trivial"] is False
        assert "interpretation" in d

    def test_cohomology_group_from_dict_roundtrip(self):
        cg = CohomologyGroup(
            dimension=2,
            generators=["g1", "g2"],
            relations=["r1"],
            interpretation="test",
        )
        d = cg.to_dict()
        restored = CohomologyGroup.from_dict(d)
        assert restored.dimension == cg.dimension
        assert restored.generators == cg.generators
        assert restored.relations == cg.relations
        # __post_init__ recomputes rank
        assert restored.rank == 1

    def test_cohomology_group_rank_field(self):
        cg = CohomologyGroup(dimension=0, generators=["a", "b", "c"], relations=["r"])
        assert cg.rank == 2

    def test_cohomology_group_all_relations(self):
        cg = CohomologyGroup(
            dimension=0,
            generators=["g1"],
            relations=["r1", "r2"],
        )
        assert cg.rank == 0
        assert cg.is_trivial is True


# ===================================================================
# CechComplex tests
# ===================================================================

class TestCechComplex:

    def test_cech_complex_creation(self, sample_vertex, sample_edge):
        cc = CechComplex(
            nerve=[sample_vertex, sample_edge],
            cochains_by_dim={0: [{"dimension": 0}], 1: [{"dimension": 1}]},
            coboundary_maps={0: "delta_0", 1: "delta_1"},
            max_dimension=2,
        )
        assert len(cc.nerve) == 2
        assert 0 in cc.cochains_by_dim
        assert 1 in cc.cochains_by_dim

    def test_cech_complex_to_dict(self, sample_vertex):
        cc = CechComplex(
            nerve=[sample_vertex],
            cochains_by_dim={0: [{"dim": 0}]},
            coboundary_maps={0: "delta_0"},
        )
        d = cc.to_dict()
        assert "nerve" in d
        assert "cochains_by_dim" in d
        assert "coboundary_maps" in d
        assert "max_dimension" in d

    def test_cech_complex_from_dict_roundtrip(self, sample_vertex, sample_edge):
        cc = CechComplex(
            nerve=[sample_vertex, sample_edge],
            cochains_by_dim={0: [{"x": 1}]},
            coboundary_maps={0: "delta_0"},
            max_dimension=1,
        )
        d = cc.to_dict()
        restored = CechComplex.from_dict(d)
        assert len(restored.nerve) == 2
        assert restored.max_dimension == 1

    def test_cech_complex_defaults(self):
        cc = CechComplex()
        assert cc.nerve == []
        assert cc.cochains_by_dim == {}
        assert cc.coboundary_maps == {}
        assert cc.max_dimension == 2


# ===================================================================
# CechCohomologyComputer tests
# ===================================================================

class TestCechCohomologyComputer:

    def test_compute_empty_site(self, computer):
        result = computer.compute({})
        assert isinstance(result, dict)
        # Should have entries for dimensions 0, 1, 2 by default
        assert 0 in result
        assert 1 in result
        assert 2 in result

    def test_compute_returns_cohomology_groups(self, computer, simple_covering):
        result = computer.compute({"covering": simple_covering})
        for dim, group in result.items():
            assert isinstance(group, CohomologyGroup)

    def test_compute_trivial_site(self, computer):
        result = computer.compute({})
        for group in result.values():
            assert group.is_trivial is True

    def test_build_nerve_empty(self, computer):
        nerve = computer.build_nerve({})
        assert isinstance(nerve, list)
        assert len(nerve) == 0

    def test_build_nerve_single_fiber(self, computer):
        nerve = computer.build_nerve({"python": [{"name": "coord1"}]})
        assert isinstance(nerve, list)
        # At least one 0-cell for the python layer
        zero_cells = [c for c in nerve if c.dimension == 0]
        assert len(zero_cells) >= 1

    def test_build_nerve_two_fibers(self, computer, simple_covering):
        nerve = computer.build_nerve(simple_covering)
        zero_cells = [c for c in nerve if c.dimension == 0]
        one_cells = [c for c in nerve if c.dimension == 1]
        assert len(zero_cells) == 2
        # python-template is a known intersection
        assert len(one_cells) >= 1

    def test_build_nerve_vertex_count(self, computer, multi_layer_covering):
        nerve = computer.build_nerve(multi_layer_covering)
        zero_cells = [c for c in nerve if c.dimension == 0]
        assert len(zero_cells) == 5

    def test_build_nerve_has_edges(self, computer, multi_layer_covering):
        nerve = computer.build_nerve(multi_layer_covering)
        one_cells = [c for c in nerve if c.dimension == 1]
        # Multiple known intersections exist among these layers
        assert len(one_cells) >= 1

    def test_build_nerve_has_triangles(self, computer, multi_layer_covering):
        nerve = computer.build_nerve(multi_layer_covering)
        two_cells = [c for c in nerve if c.dimension == 2]
        # javascript, html, css is a known triple
        assert len(two_cells) >= 1

    def test_compute_cochains_dim0(self, computer, simple_covering):
        nerve = computer.build_nerve(simple_covering)
        cochains = computer.compute_cochains(nerve, 0)
        assert isinstance(cochains, list)
        assert len(cochains) >= 1
        assert all(isinstance(c, Cochain) for c in cochains)

    def test_compute_cochains_dim1(self, computer, simple_covering):
        nerve = computer.build_nerve(simple_covering)
        cochains = computer.compute_cochains(nerve, 1)
        assert isinstance(cochains, list)

    def test_coboundary_map_returns_callable(self, computer, simple_covering):
        nerve = computer.build_nerve(simple_covering)
        cochains = computer.compute_cochains(nerve, 0)
        delta = computer.coboundary_map(cochains, 0)
        assert callable(delta)

    def test_coboundary_map_produces_output(self, computer, simple_covering):
        nerve = computer.build_nerve(simple_covering)
        cochains = computer.compute_cochains(nerve, 0)
        delta = computer.coboundary_map(cochains, 0)
        result = delta(cochains)
        assert isinstance(result, list)

    def test_compute_cocycles_returns_list(self, computer):
        cocycles = computer.compute_cocycles([], lambda _: [])
        assert isinstance(cocycles, list)

    def test_compute_coboundaries_returns_list(self, computer):
        coboundaries = computer.compute_coboundaries([], lambda _: [])
        assert isinstance(coboundaries, list)

    def test_quotient_trivial(self, computer):
        group = computer.quotient([], [])
        assert group.is_trivial is True
        assert group.rank == 0

    def test_quotient_nontrivial(self, computer):
        cell = NerveCell(dimension=1, vertices=["a", "b"], cell_id="a|b")
        cocycle = Cochain(dimension=1, cells=[cell], values={"a|b": {"test": True}})
        group = computer.quotient([cocycle], [])
        assert group.rank > 0
        assert group.is_trivial is False

    def test_quotient_with_coboundaries(self, computer):
        cell = NerveCell(dimension=1, vertices=["a", "b"], cell_id="a|b")
        cocycle = Cochain(dimension=1, cells=[cell], values={"a|b": {"test": True}})
        # Same cocycle is also a coboundary -> trivial
        group = computer.quotient([cocycle], [cocycle])
        assert group.is_trivial is True

    def test_interpret_generators_empty_group(self, computer):
        group = CohomologyGroup(dimension=0, generators=[], relations=[])
        interps = computer.interpret_generators(group, {})
        assert isinstance(interps, list)
        assert len(interps) == 0

    def test_interpret_generators_h1(self, computer):
        group = CohomologyGroup(
            dimension=1,
            generators=["non-trivial: python|template"],
            relations=[],
        )
        interps = computer.interpret_generators(group, {})
        assert len(interps) >= 1
        assert any("H¹" in desc for desc in interps)

    def test_interpret_generators_h2(self, computer):
        group = CohomologyGroup(
            dimension=2,
            generators=["non-trivial: css|javascript|python"],
            relations=[],
        )
        interps = computer.interpret_generators(group, {})
        assert len(interps) >= 1
        assert any("H²" in desc for desc in interps)

    def test_interpret_generators_h0(self, computer):
        group = CohomologyGroup(
            dimension=0,
            generators=["non-trivial: python"],
            relations=[],
        )
        interps = computer.interpret_generators(group, {})
        assert len(interps) >= 1
        assert any("H⁰" in desc for desc in interps)

    def test_full_computation_web_site(self, computer):
        site_data = {
            "covering": {
                "python": [{"name": "user"}, {"name": "route_index"}],
                "template": [{"name": "user"}, {"name": "base_template"}],
                "javascript": [{"name": "fetchUser"}],
                "html": [{"name": "user_element"}],
                "css": [{"name": "user-card"}],
            },
        }
        result = computer.compute(site_data, max_dimension=2)
        assert 0 in result
        assert 1 in result
        assert 2 in result
        for dim, group in result.items():
            assert isinstance(group, CohomologyGroup)
            assert isinstance(group.interpretation, str)
            assert len(group.interpretation) > 0

    def test_h1_generators_describe_bugs(self, computer):
        """H¹ generators in a site with cross-language inconsistency should
        describe actual bugs."""
        site_data = {
            "covering": {
                "python": [{"name": "user"}],
                "template": [{"name": "user"}],
                "javascript": [],
                "css": [],
            },
        }
        result = computer.compute(site_data, max_dimension=1)
        # The H¹ group's interpretation should be a non-empty string
        h1 = result[1]
        assert isinstance(h1.interpretation, str)
        assert len(h1.interpretation) > 0

    def test_compute_with_max_dimension_1(self, computer, simple_covering):
        result = computer.compute({"covering": simple_covering}, max_dimension=1)
        assert 0 in result
        assert 1 in result
        assert 2 not in result

    def test_compute_with_max_dimension_2(self, computer, multi_layer_covering):
        result = computer.compute({"covering": multi_layer_covering}, max_dimension=2)
        assert 0 in result
        assert 1 in result
        assert 2 in result

    def test_build_complex_returns_cech_complex(self, computer, simple_covering):
        cc = computer.build_complex({"covering": simple_covering})
        assert isinstance(cc, CechComplex)
        assert len(cc.nerve) >= 2
        assert 0 in cc.cochains_by_dim

    def test_build_complex_empty_site(self, computer):
        cc = computer.build_complex({})
        assert isinstance(cc, CechComplex)
        assert len(cc.nerve) == 0

    def test_nerve_cell_ids_are_sorted(self, computer, multi_layer_covering):
        nerve = computer.build_nerve(multi_layer_covering)
        for cell in nerve:
            if cell.dimension >= 1:
                # Vertices should be sorted
                assert cell.vertices == sorted(cell.vertices)

    def test_compute_preserves_dimension_labels(self, computer, simple_covering):
        result = computer.compute({"covering": simple_covering}, max_dimension=2)
        for dim, group in result.items():
            assert group.dimension == dim
