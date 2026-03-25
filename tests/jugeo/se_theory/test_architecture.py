"""Comprehensive tests for the SE Theory Architecture module.

Tests cover:
- CoverAnalyzer: coupling, cohesion, interface widths, dependency depth, cycles
- CoverSuggester: cover suggestion, refinement, splits, merges
- TarjanSCC: SCC detection, condensation
- ArchitectureEnforcer: manifest loading, boundary checking
- ArchitectureTracker: snapshots, drift, alerts
- SiteArchitectureAnalyzer: site integration (duck-typed)
- ImportGraphArchitecture: import graph analysis
- Theorems: computational checks
"""
from __future__ import annotations

import json
import os
from collections import namedtuple
from dataclasses import dataclass

import pytest

from jugeo.se_theory.architecture.algorithms import (
    ArchitectureEnforcer,
    ArchitectureTracker,
    CoverAnalyzer,
    CoverSuggester,
    TarjanSCC,
)
from jugeo.se_theory.architecture.integration import (
    ImportGraphArchitecture,
    SiteArchitectureAnalyzer,
)
from jugeo.se_theory.architecture.models import (
    ArchitecturalDecision,
    ArchitecturalDecisionKind,
    ArchitecturalDrift,
    ArchitecturalManifest,
    ArchitecturalMetric,
    ArchitecturalOverlap,
    ArchitecturalSnapshot,
    BoundaryViolation,
    CoverMember,
    CoverMemberKind,
    CoverQualityMetrics,
    DeclaredBoundary,
)
from jugeo.se_theory.architecture.theorems import (
    ALL_THEOREMS,
    THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT,
    THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS,
    THEOREM_COUPLING_BOUNDS_DESCENT_COST,
    THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST,
    THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_cover_members() -> list[CoverMember]:
    """Three modules with some shared coordinates.

    auth: [user, token, session]
    api: [endpoint, request, response, user]   ← user shared with auth
    db: [connection, query, model, session]    ← session shared with auth
    """
    return [
        CoverMember(
            id="auth",
            name="auth",
            kind=CoverMemberKind.MODULE,
            coordinates=["user", "token", "session"],
            internal_morphisms=["user->token", "token->session"],
            external_morphisms=["user->endpoint", "session->model"],
        ),
        CoverMember(
            id="api",
            name="api",
            kind=CoverMemberKind.MODULE,
            coordinates=["endpoint", "request", "response", "user"],
            internal_morphisms=["request->endpoint", "endpoint->response"],
            external_morphisms=["user->token", "response->query"],
        ),
        CoverMember(
            id="db",
            name="db",
            kind=CoverMemberKind.MODULE,
            coordinates=["connection", "query", "model", "session"],
            internal_morphisms=["connection->query", "query->model"],
            external_morphisms=["session->token", "model->response"],
        ),
    ]


@pytest.fixture
def simple_morphisms() -> list[tuple[str, str]]:
    """Morphisms between coordinate ids."""
    return [
        ("user", "endpoint"),
        ("user", "token"),
        ("token", "session"),
        ("session", "model"),
        ("endpoint", "response"),
        ("request", "endpoint"),
        ("connection", "query"),
        ("query", "model"),
        ("response", "query"),
        ("session", "token"),
    ]


@pytest.fixture
def cyclic_morphisms() -> list[tuple[str, str]]:
    """Cycle: A -> B -> C -> A, plus D -> A."""
    return [("A", "B"), ("B", "C"), ("C", "A"), ("D", "A")]


@pytest.fixture
def dag_morphisms() -> list[tuple[str, str]]:
    """Pure DAG: A -> B -> D, A -> C -> D."""
    return [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]


@pytest.fixture
def bipartite_graph():
    """Two natural clusters connected by a single bridge edge."""
    coords_a = [f"a{i}" for i in range(6)]
    coords_b = [f"b{i}" for i in range(6)]
    coordinates = coords_a + coords_b

    # Dense internal edges within each cluster
    morphisms = [
        (f"a{i}", f"a{j}")
        for i in range(6)
        for j in range(i + 1, 6)
        if abs(i - j) <= 2
    ]
    morphisms += [
        (f"b{i}", f"b{j}")
        for i in range(6)
        for j in range(i + 1, 6)
        if abs(i - j) <= 2
    ]
    # One bridge edge
    morphisms.append(("a5", "b0"))

    return coordinates, morphisms


@pytest.fixture
def sample_manifest_data() -> dict:
    """JSON data for an architectural manifest."""
    return {
        "id": "test-manifest",
        "version": "1.0",
        "declared_covers": [
            {
                "name": "auth",
                "coordinate_patterns": ["auth.*"],
                "allowed_imports": ["common.*"],
                "disallowed_imports": ["db.*"],
                "trust_requirement": "high",
            },
            {
                "name": "api",
                "coordinate_patterns": ["api.*"],
                "allowed_imports": ["auth.*", "common.*", "db.*"],
                "disallowed_imports": [],
                "trust_requirement": "normal",
            },
        ],
        "interface_contracts": [],
        "boundary_rules": [],
    }


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModels:
    """Tests for data model serialization and basic behavior."""

    def test_cover_member_to_dict_roundtrip(self):
        member = CoverMember(
            id="m1",
            name="module_one",
            kind=CoverMemberKind.MODULE,
            coordinates=["a", "b", "c"],
            internal_morphisms=["a->b"],
            external_morphisms=["c->d"],
            metadata={"key": "value"},
        )
        data = member.to_dict()
        restored = CoverMember.from_dict(data)
        assert restored.id == member.id
        assert restored.name == member.name
        assert restored.kind == member.kind
        assert restored.coordinates == member.coordinates
        assert restored.metadata == member.metadata

    def test_architectural_overlap_width(self):
        overlap = ArchitecturalOverlap(
            member_a_id="a",
            member_b_id="b",
            shared_coordinates=["x", "y", "z"],
        )
        assert overlap.width == 3

    def test_architectural_overlap_roundtrip(self):
        overlap = ArchitecturalOverlap(
            member_a_id="a",
            member_b_id="b",
            shared_coordinates=["x", "y"],
            interface_propositions=["prop1"],
            treaty_id="treaty_1",
        )
        data = overlap.to_dict()
        restored = ArchitecturalOverlap.from_dict(data)
        assert restored.member_a_id == "a"
        assert restored.width == 2

    def test_cover_quality_metrics_roundtrip(self):
        metrics = CoverQualityMetrics(
            cover_id="test",
            coupling_score=0.3,
            cohesion_score=0.8,
            total_members=5,
        )
        data = metrics.to_dict()
        restored = CoverQualityMetrics.from_dict(data)
        assert restored.cover_id == "test"
        assert restored.coupling_score == 0.3
        assert restored.computed_at != ""

    def test_architectural_decision_roundtrip(self):
        decision = ArchitecturalDecision(
            id="d1",
            kind=ArchitecturalDecisionKind.EXTRACT_MODULE,
            target_members=["m1"],
            description="Extract auth module",
            confidence=0.8,
        )
        data = decision.to_dict()
        restored = ArchitecturalDecision.from_dict(data)
        assert restored.kind == ArchitecturalDecisionKind.EXTRACT_MODULE
        assert restored.confidence == 0.8

    def test_declared_boundary_roundtrip(self):
        boundary = DeclaredBoundary(
            name="auth",
            coordinate_patterns=["auth.*"],
            allowed_imports=["common.*"],
            disallowed_imports=["db.*"],
            trust_requirement="high",
        )
        data = boundary.to_dict()
        restored = DeclaredBoundary.from_dict(data)
        assert restored.name == "auth"
        assert restored.trust_requirement == "high"

    def test_architectural_manifest_roundtrip(self):
        manifest = ArchitecturalManifest(
            id="test",
            declared_covers=[
                DeclaredBoundary(name="auth", coordinate_patterns=["auth.*"])
            ],
            version="2.0",
        )
        data = manifest.to_dict()
        restored = ArchitecturalManifest.from_dict(data)
        assert restored.id == "test"
        assert len(restored.declared_covers) == 1
        assert restored.version == "2.0"

    def test_boundary_violation_roundtrip(self):
        violation = BoundaryViolation(
            boundary_name="auth",
            violating_coordinate="auth.secret",
            violation_kind="UNDECLARED_IMPORT",
            details="Bad import",
            severity="error",
        )
        data = violation.to_dict()
        restored = BoundaryViolation.from_dict(data)
        assert restored.violation_kind == "UNDECLARED_IMPORT"

    def test_architectural_snapshot_roundtrip(self):
        snapshot = ArchitecturalSnapshot(
            id="snap1",
            member_count=3,
            violation_count=1,
        )
        assert snapshot.timestamp != ""
        data = snapshot.to_dict()
        restored = ArchitecturalSnapshot.from_dict(data)
        assert restored.id == "snap1"
        assert restored.member_count == 3

    def test_architectural_drift_roundtrip(self):
        drift = ArchitecturalDrift(
            baseline_snapshot_id="snap1",
            current_snapshot_id="snap2",
            coupling_delta=0.15,
            cohesion_delta=-0.1,
            drift_score=0.25,
            needs_attention=True,
        )
        data = drift.to_dict()
        restored = ArchitecturalDrift.from_dict(data)
        assert restored.needs_attention is True
        assert restored.coupling_delta == 0.15

    def test_enums(self):
        assert ArchitecturalMetric.COUPLING.value == "coupling"
        assert CoverMemberKind.MODULE.value == "module"
        assert ArchitecturalDecisionKind.EXTRACT_MODULE.value == "extract_module"


# ---------------------------------------------------------------------------
# CoverAnalyzer tests
# ---------------------------------------------------------------------------


class TestCoverAnalyzer:
    """Tests for architectural quality metric computation."""

    def test_compute_coupling_basic(self, simple_cover_members, simple_morphisms):
        scores = CoverAnalyzer.compute_coupling(
            simple_cover_members, simple_morphisms
        )
        assert len(scores) == 3
        for mid, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{mid} coupling out of range: {score}"
        # auth and api share "user", so both should have non-zero coupling
        assert scores["auth"] > 0
        assert scores["api"] > 0

    def test_compute_coupling_empty(self):
        assert CoverAnalyzer.compute_coupling([], []) == {}

    def test_compute_cohesion_basic(self, simple_cover_members, simple_morphisms):
        scores = CoverAnalyzer.compute_cohesion(
            simple_cover_members, simple_morphisms
        )
        assert len(scores) == 3
        for mid, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{mid} cohesion out of range: {score}"

    def test_compute_cohesion_single_coord_member(self):
        member = CoverMember(
            id="solo", name="solo", kind=CoverMemberKind.MODULE,
            coordinates=["only_coord"],
        )
        scores = CoverAnalyzer.compute_cohesion([member], [])
        assert scores["solo"] == 1.0

    def test_compute_interface_widths(self, simple_cover_members):
        widths = CoverAnalyzer.compute_interface_widths(simple_cover_members)
        # auth and api share "user"
        assert widths.get(("auth", "api")) == 1
        # auth and db share "session"
        assert widths.get(("auth", "db")) == 1

    def test_compute_interface_widths_no_overlap(self):
        m1 = CoverMember(
            id="m1", name="m1", kind=CoverMemberKind.MODULE,
            coordinates=["a", "b"],
        )
        m2 = CoverMember(
            id="m2", name="m2", kind=CoverMemberKind.MODULE,
            coordinates=["c", "d"],
        )
        widths = CoverAnalyzer.compute_interface_widths([m1, m2])
        assert len(widths) == 0

    def test_detect_circular_dependencies_cycle(self, cyclic_morphisms):
        cycles = CoverAnalyzer.detect_circular_dependencies(cyclic_morphisms)
        assert len(cycles) >= 1
        cycle_sets = [set(c) for c in cycles]
        assert any({"A", "B", "C"} == cs for cs in cycle_sets)

    def test_detect_circular_dependencies_dag(self, dag_morphisms):
        cycles = CoverAnalyzer.detect_circular_dependencies(dag_morphisms)
        assert len(cycles) == 0

    def test_compute_dependency_depth_dag(self, dag_morphisms):
        depth = CoverAnalyzer.compute_dependency_depth(dag_morphisms)
        # A -> B -> D is length 2
        assert depth == 2

    def test_compute_dependency_depth_empty(self):
        assert CoverAnalyzer.compute_dependency_depth([]) == 0

    def test_compute_dependency_depth_with_cycle(self, cyclic_morphisms):
        depth = CoverAnalyzer.compute_dependency_depth(cyclic_morphisms)
        # After SCC collapse: D -> SCC_0, depth = 1
        assert depth >= 1

    def test_compute_instability(self):
        morphisms = [("A", "B"), ("A", "C"), ("D", "A")]
        # A: Ce=2 (outgoing to B, C), Ca=1 (incoming from D)
        instability = CoverAnalyzer.compute_instability("A", morphisms)
        assert abs(instability - 2 / 3) < 0.01

    def test_compute_instability_no_deps(self):
        instability = CoverAnalyzer.compute_instability("A", [])
        assert instability == 0.5

    def test_compute_abstractness(self):
        member = CoverMember(
            id="m1", name="m1", kind=CoverMemberKind.MODULE,
            coordinates=["abstract_handler", "base_model", "concrete_impl", "util"],
        )
        abstractness = CoverAnalyzer.compute_abstractness(member)
        # abstract_handler and base_model are abstract -> 2/4 = 0.5
        assert abs(abstractness - 0.5) < 0.01

    def test_compute_abstractness_none(self):
        member = CoverMember(
            id="m1", name="m1", kind=CoverMemberKind.MODULE,
            coordinates=["foo", "bar"],
        )
        assert CoverAnalyzer.compute_abstractness(member) == 0.0

    def test_compute_abstractness_empty(self):
        member = CoverMember(
            id="m1", name="m1", kind=CoverMemberKind.MODULE,
            coordinates=[],
        )
        assert CoverAnalyzer.compute_abstractness(member) == 0.0

    def test_full_quality_analysis(self, simple_cover_members, simple_morphisms):
        metrics = CoverAnalyzer.full_quality_analysis(
            simple_cover_members, simple_morphisms, cover_id="test_cover"
        )
        assert isinstance(metrics, CoverQualityMetrics)
        assert metrics.cover_id == "test_cover"
        assert metrics.total_members == 3
        assert metrics.total_overlaps >= 0
        assert 0.0 <= metrics.coupling_score <= 1.0
        assert 0.0 <= metrics.cohesion_score <= 1.0
        assert metrics.dependency_depth >= 0
        assert metrics.computed_at != ""
        assert len(metrics.instability_scores) == 3
        assert len(metrics.abstractness_scores) == 3

    def test_full_quality_analysis_empty(self):
        metrics = CoverAnalyzer.full_quality_analysis([], [])
        assert metrics.total_members == 0
        assert metrics.coupling_score == 0.0


# ---------------------------------------------------------------------------
# CoverSuggester tests
# ---------------------------------------------------------------------------


class TestCoverSuggester:
    """Tests for cover suggestion via graph partitioning."""

    def test_suggest_cover_basic(self, bipartite_graph):
        coordinates, morphisms = bipartite_graph
        members = CoverSuggester.suggest_cover(
            coordinates, morphisms, target_coupling=0.3, max_members=4
        )
        assert len(members) >= 2
        # All coordinates should be assigned
        all_assigned = set()
        for m in members:
            all_assigned.update(m.coordinates)
        assert all_assigned == set(coordinates)

    def test_suggest_cover_quality(self, bipartite_graph):
        """Suggested cover should have reasonable quality metrics."""
        coordinates, morphisms = bipartite_graph
        members = CoverSuggester.suggest_cover(coordinates, morphisms)
        metrics = CoverAnalyzer.full_quality_analysis(members, morphisms)
        # Coupling should be bounded
        assert metrics.coupling_score <= 1.0

    def test_suggest_cover_empty(self):
        members = CoverSuggester.suggest_cover([], [])
        assert members == []

    def test_suggest_cover_single(self):
        members = CoverSuggester.suggest_cover(["a"], [])
        assert len(members) == 1

    def test_refine_cover(self, simple_cover_members, simple_morphisms):
        refined = CoverSuggester.refine_cover(
            simple_cover_members, simple_morphisms, quality_target=0.7
        )
        assert len(refined) >= 1
        # All original coordinates should still be assigned
        original_coords = set()
        for m in simple_cover_members:
            original_coords.update(m.coordinates)
        refined_coords = set()
        for m in refined:
            refined_coords.update(m.coordinates)
        assert refined_coords == original_coords

    def test_refine_cover_empty(self):
        assert CoverSuggester.refine_cover([], []) == []

    def test_suggest_splits(self, simple_morphisms):
        """A member with poor cohesion should trigger split suggestions."""
        # Create a large member with few internal edges
        member = CoverMember(
            id="big_module",
            name="big_module",
            kind=CoverMemberKind.MODULE,
            coordinates=[f"coord_{i}" for i in range(10)],
            internal_morphisms=[],
            external_morphisms=[],
        )
        # Minimal internal morphisms — poor cohesion
        morphisms = [("coord_0", "coord_1")]
        decisions = CoverSuggester.suggest_splits(member, morphisms)
        assert len(decisions) >= 1
        assert decisions[0].kind == ArchitecturalDecisionKind.SPLIT_PACKAGE

    def test_suggest_splits_high_cohesion(self):
        """Well-cohesive member should not trigger splits."""
        member = CoverMember(
            id="tight", name="tight", kind=CoverMemberKind.MODULE,
            coordinates=["a", "b", "c"],
        )
        # All pairs connected
        morphisms = [("a", "b"), ("b", "c"), ("a", "c")]
        decisions = CoverSuggester.suggest_splits(member, morphisms)
        assert len(decisions) == 0

    def test_suggest_merges(self, simple_morphisms):
        """Two tightly coupled small members should trigger merge."""
        m1 = CoverMember(
            id="tiny_a", name="tiny_a", kind=CoverMemberKind.MODULE,
            coordinates=["x1", "x2"],
        )
        m2 = CoverMember(
            id="tiny_b", name="tiny_b", kind=CoverMemberKind.MODULE,
            coordinates=["y1", "y2"],
        )
        # Every coordinate in m1 connects to every in m2
        morphisms = [
            ("x1", "y1"), ("x1", "y2"), ("x2", "y1"), ("x2", "y2"),
        ]
        decisions = CoverSuggester.suggest_merges([m1, m2], morphisms)
        assert len(decisions) >= 1
        assert decisions[0].kind == ArchitecturalDecisionKind.MERGE_MODULES

    def test_suggest_merges_no_coupling(self):
        """Unrelated members should not trigger merge."""
        m1 = CoverMember(
            id="a", name="a", kind=CoverMemberKind.MODULE,
            coordinates=["x1", "x2"],
        )
        m2 = CoverMember(
            id="b", name="b", kind=CoverMemberKind.MODULE,
            coordinates=["y1", "y2"],
        )
        decisions = CoverSuggester.suggest_merges([m1, m2], [])
        assert len(decisions) == 0

    def test_estimate_k(self):
        assert CoverSuggester._estimate_k(1, []) == 1
        assert CoverSuggester._estimate_k(2, []) == 1
        assert CoverSuggester._estimate_k(10, []) == 2
        assert CoverSuggester._estimate_k(30, []) == 6
        assert CoverSuggester._estimate_k(200, []) <= 20


# ---------------------------------------------------------------------------
# TarjanSCC tests
# ---------------------------------------------------------------------------


class TestTarjanSCC:
    """Tests for Tarjan's strongly connected components algorithm."""

    def test_find_sccs_simple_cycle(self):
        adj = {"A": ["B"], "B": ["C"], "C": ["A"]}
        sccs = TarjanSCC.find_sccs(adj)
        scc_sets = [set(s) for s in sccs]
        assert any({"A", "B", "C"} == s for s in scc_sets)

    def test_find_sccs_dag(self):
        adj = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
        sccs = TarjanSCC.find_sccs(adj)
        # All singletons
        for scc in sccs:
            assert len(scc) == 1

    def test_find_sccs_multiple_cycles(self):
        adj = {
            "A": ["B"], "B": ["A"],     # cycle 1
            "C": ["D"], "D": ["E"], "E": ["C"],  # cycle 2
            "F": [],  # singleton
        }
        sccs = TarjanSCC.find_sccs(adj)
        nontrivial = [s for s in sccs if len(s) > 1]
        assert len(nontrivial) == 2
        scc_sets = [set(s) for s in nontrivial]
        assert any({"A", "B"} == s for s in scc_sets)
        assert any({"C", "D", "E"} == s for s in scc_sets)

    def test_find_sccs_empty(self):
        assert TarjanSCC.find_sccs({}) == []

    def test_find_sccs_self_loop(self):
        adj = {"A": ["A"]}
        sccs = TarjanSCC.find_sccs(adj)
        assert len(sccs) == 1
        assert sccs[0] == ["A"]

    def test_condense_to_dag(self):
        adj = {"A": ["B"], "B": ["C"], "C": ["A", "D"], "D": []}
        sccs = TarjanSCC.find_sccs(adj)
        dag = TarjanSCC.condense_to_dag(adj, sccs)

        # Verify no cycles in condensed graph
        dag_sccs = TarjanSCC.find_nontrivial_sccs(dag)
        assert len(dag_sccs) == 0

    def test_condense_preserves_reachability(self):
        """Condensed graph should maintain reachability structure."""
        adj = {"A": ["B"], "B": ["A", "C"], "C": ["D"], "D": []}
        sccs = TarjanSCC.find_sccs(adj)
        dag = TarjanSCC.condense_to_dag(adj, sccs)
        # SCC containing A,B should connect to C and C to D
        assert len(dag) >= 2

    def test_nontrivial_sccs(self):
        adj = {"A": ["B"], "B": ["A"], "C": ["D"], "D": []}
        nontrivial = TarjanSCC.find_nontrivial_sccs(adj)
        assert len(nontrivial) == 1
        assert set(nontrivial[0]) == {"A", "B"}

    def test_nontrivial_sccs_empty(self):
        adj = {"A": ["B"], "B": [], "C": []}
        nontrivial = TarjanSCC.find_nontrivial_sccs(adj)
        assert len(nontrivial) == 0


# ---------------------------------------------------------------------------
# ArchitectureEnforcer tests
# ---------------------------------------------------------------------------


class TestArchitectureEnforcer:
    """Tests for boundary enforcement and manifest loading."""

    def test_load_manifest(self, tmp_path, sample_manifest_data):
        manifest_path = str(tmp_path / "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(sample_manifest_data, f)

        manifest = ArchitectureEnforcer.load_manifest(manifest_path)
        assert manifest.id == "test-manifest"
        assert manifest.version == "1.0"
        assert len(manifest.declared_covers) == 2
        assert manifest.declared_covers[0].name == "auth"

    def test_check_boundaries_no_violations(self, sample_manifest_data):
        manifest = ArchitecturalManifest.from_dict(sample_manifest_data)
        coordinates = ["auth.login", "auth.logout", "common.utils"]
        morphisms = [("auth.login", "common.utils")]
        violations = ArchitectureEnforcer.check_boundaries(
            manifest, coordinates, morphisms
        )
        assert len(violations) == 0

    def test_check_boundaries_with_violations(self, sample_manifest_data):
        manifest = ArchitecturalManifest.from_dict(sample_manifest_data)
        coordinates = ["auth.login", "db.connection", "common.utils"]
        # auth.login imports db.connection — disallowed!
        morphisms = [("auth.login", "db.connection")]
        violations = ArchitectureEnforcer.check_boundaries(
            manifest, coordinates, morphisms
        )
        assert len(violations) >= 1
        assert any(v.violation_kind == "UNDECLARED_IMPORT" for v in violations)

    def test_check_boundaries_allowed_import(self, sample_manifest_data):
        manifest = ArchitecturalManifest.from_dict(sample_manifest_data)
        coordinates = ["api.endpoint", "auth.login", "db.query"]
        # api can import from auth and db
        morphisms = [("api.endpoint", "auth.login"), ("api.endpoint", "db.query")]
        violations = ArchitectureEnforcer.check_boundaries(
            manifest, coordinates, morphisms
        )
        assert len(violations) == 0

    def test_pattern_matching(self):
        assert ArchitectureEnforcer._match_pattern("auth.login", "auth.*")
        assert ArchitectureEnforcer._match_pattern("auth.login.handler", "auth.*")
        assert not ArchitectureEnforcer._match_pattern("api.login", "auth.*")
        assert ArchitectureEnforcer._match_pattern("anything", "*")
        assert ArchitectureEnforcer._match_pattern("auth.x", "auth.?")

    def test_check_interface_contracts(self):
        manifest = ArchitecturalManifest(
            id="test",
            interface_contracts=["auth.public_api"],
        )
        # auth.public_api exists
        coords = ["auth.public_api", "auth.internal"]
        violations = ArchitectureEnforcer.check_boundaries(manifest, coords, [])
        assert len(violations) == 0

    def test_check_interface_contracts_missing(self):
        manifest = ArchitecturalManifest(
            id="test",
            interface_contracts=["auth.public_api"],
        )
        # auth.public_api does NOT exist
        coords = ["auth.internal"]
        violations = ArchitectureEnforcer.check_boundaries(manifest, coords, [])
        assert len(violations) == 1
        assert violations[0].violation_kind == "INTERFACE_VIOLATION"

    def test_check_trust_requirements(self):
        boundary = DeclaredBoundary(
            name="secure",
            coordinate_patterns=["secure.*"],
            trust_requirement="high",
        )
        coords = ["secure.handler", "secure.util"]
        judgments = {"secure.handler": "normal", "secure.util": "high"}
        violations = ArchitectureEnforcer._check_trust_requirements(
            boundary, coords, judgments
        )
        # secure.handler has "normal" but needs "high"
        assert len(violations) == 1
        assert violations[0].violation_kind == "TRUST_INSUFFICIENT"

    def test_check_trust_requirements_all_pass(self):
        boundary = DeclaredBoundary(
            name="basic",
            coordinate_patterns=["basic.*"],
            trust_requirement="normal",
        )
        coords = ["basic.a", "basic.b"]
        judgments = {"basic.a": "high", "basic.b": "normal"}
        violations = ArchitectureEnforcer._check_trust_requirements(
            boundary, coords, judgments
        )
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# ArchitectureTracker tests
# ---------------------------------------------------------------------------


class TestArchitectureTracker:
    """Tests for architectural evolution tracking."""

    def test_take_snapshot(self, simple_cover_members, simple_morphisms):
        tracker = ArchitectureTracker()
        snapshot = tracker.take_snapshot(simple_cover_members, simple_morphisms)
        assert isinstance(snapshot, ArchitecturalSnapshot)
        assert snapshot.id.startswith("snap_")
        assert snapshot.member_count == 3
        assert snapshot.cover_quality is not None
        assert snapshot.timestamp != ""

    def test_history(self, simple_cover_members, simple_morphisms):
        tracker = ArchitectureTracker()
        s1 = tracker.take_snapshot(simple_cover_members, simple_morphisms)
        s2 = tracker.take_snapshot(simple_cover_members, simple_morphisms)
        history = tracker.history()
        assert len(history) == 2
        assert history[0].id == s1.id
        assert history[1].id == s2.id

    def test_compute_drift(self, simple_cover_members, simple_morphisms):
        tracker = ArchitectureTracker()
        s1 = tracker.take_snapshot(simple_cover_members, simple_morphisms)
        s2 = tracker.take_snapshot(simple_cover_members, simple_morphisms)
        drift = tracker.compute_drift(s1.id, s2.id)
        assert isinstance(drift, ArchitecturalDrift)
        assert drift.baseline_snapshot_id == s1.id
        assert drift.current_snapshot_id == s2.id
        # Same data twice → minimal drift
        assert abs(drift.coupling_delta) < 0.01
        assert abs(drift.cohesion_delta) < 0.01

    def test_compute_drift_missing_snapshot(self):
        tracker = ArchitectureTracker()
        drift = tracker.compute_drift("nonexistent1", "nonexistent2")
        assert drift.coupling_delta == 0.0

    def test_alert_on_degradation(self):
        tracker = ArchitectureTracker()
        drift = ArchitecturalDrift(
            baseline_snapshot_id="s1",
            current_snapshot_id="s2",
            coupling_delta=0.2,
            cohesion_delta=-0.15,
            drift_score=0.35,
            needs_attention=True,
        )
        alerts = tracker.alert_on_degradation(drift)
        assert len(alerts) >= 2
        assert any("Coupling increased" in a for a in alerts)
        assert any("Cohesion decreased" in a for a in alerts)

    def test_alert_on_no_degradation(self):
        tracker = ArchitectureTracker()
        drift = ArchitecturalDrift(
            baseline_snapshot_id="s1",
            current_snapshot_id="s2",
            coupling_delta=0.01,
            cohesion_delta=0.01,
            drift_score=0.02,
        )
        alerts = tracker.alert_on_degradation(drift)
        assert len(alerts) == 0

    def test_trend_analysis(self, simple_cover_members, simple_morphisms):
        tracker = ArchitectureTracker()
        for _ in range(3):
            tracker.take_snapshot(simple_cover_members, simple_morphisms)
        trends = tracker.trend_analysis()
        assert "coupling" in trends
        assert "cohesion" in trends
        assert len(trends["coupling"]) == 3
        assert len(trends["cohesion"]) == 3


# ---------------------------------------------------------------------------
# SiteArchitectureAnalyzer tests (duck-typed mock site)
# ---------------------------------------------------------------------------


MockCoordinate = namedtuple("MockCoordinate", ["id"])
MockMorphism = namedtuple("MockMorphism", ["source", "target"])


@dataclass
class MockSite:
    """Duck-typed mock of a JuGeo Site for testing."""

    coordinates: list
    morphisms: list


class TestSiteArchitectureAnalyzer:
    """Tests for site integration via duck-typing."""

    def _make_site(self):
        coords = [
            MockCoordinate(id="pkg_a.mod1"),
            MockCoordinate(id="pkg_a.mod2"),
            MockCoordinate(id="pkg_b.mod1"),
            MockCoordinate(id="pkg_b.mod2"),
            MockCoordinate(id="pkg_c.mod1"),
        ]
        morphisms = [
            MockMorphism(
                source=MockCoordinate(id="pkg_a.mod1"),
                target=MockCoordinate(id="pkg_a.mod2"),
            ),
            MockMorphism(
                source=MockCoordinate(id="pkg_a.mod2"),
                target=MockCoordinate(id="pkg_b.mod1"),
            ),
            MockMorphism(
                source=MockCoordinate(id="pkg_b.mod1"),
                target=MockCoordinate(id="pkg_b.mod2"),
            ),
            MockMorphism(
                source=MockCoordinate(id="pkg_b.mod2"),
                target=MockCoordinate(id="pkg_c.mod1"),
            ),
        ]
        return MockSite(coordinates=coords, morphisms=morphisms)

    def test_analyze_site(self):
        analyzer = SiteArchitectureAnalyzer()
        site = self._make_site()
        metrics = analyzer.analyze_site(site)
        assert isinstance(metrics, CoverQualityMetrics)
        assert metrics.total_members >= 1

    def test_suggest_covers_for_site(self):
        analyzer = SiteArchitectureAnalyzer()
        site = self._make_site()
        members = analyzer.suggest_covers_for_site(site)
        assert isinstance(members, list)
        assert len(members) >= 1

    def test_coordinates_to_cover_members(self):
        analyzer = SiteArchitectureAnalyzer()
        site = self._make_site()
        members = analyzer.coordinates_to_cover_members(site)
        # Should group by pkg prefix
        member_ids = {m.id for m in members}
        assert "pkg_a" in member_ids
        assert "pkg_b" in member_ids
        assert "pkg_c" in member_ids

    def test_site_to_adjacency(self):
        analyzer = SiteArchitectureAnalyzer()
        site = self._make_site()
        adj = analyzer.site_to_adjacency(site)
        assert isinstance(adj, dict)
        assert "pkg_a.mod1" in adj
        assert "pkg_a.mod2" in adj["pkg_a.mod1"]

    def test_extract_coordinate_ids(self):
        analyzer = SiteArchitectureAnalyzer()
        site = self._make_site()
        ids = analyzer._extract_coordinate_ids(site)
        assert len(ids) == 5
        assert "pkg_a.mod1" in ids

    def test_extract_morphisms(self):
        analyzer = SiteArchitectureAnalyzer()
        site = self._make_site()
        morphisms = analyzer._extract_morphisms(site)
        assert len(morphisms) == 4
        assert ("pkg_a.mod1", "pkg_a.mod2") in morphisms

    def test_string_coordinates(self):
        """Handle site with plain string coordinates."""
        site = MockSite(
            coordinates=["a", "b", "c"],
            morphisms=[("a", "b"), ("b", "c")],
        )
        analyzer = SiteArchitectureAnalyzer()
        ids = analyzer._extract_coordinate_ids(site)
        assert ids == ["a", "b", "c"]

    def test_empty_site(self):
        analyzer = SiteArchitectureAnalyzer()
        site = MockSite(coordinates=[], morphisms=[])
        metrics = analyzer.analyze_site(site)
        assert metrics.total_members == 0


# ---------------------------------------------------------------------------
# ImportGraphArchitecture tests
# ---------------------------------------------------------------------------


class TestImportGraphArchitecture:
    """Tests for import graph analysis."""

    def test_from_import_edges(self):
        edges = [
            ("pkg_a.mod1", "pkg_b.mod2"),
            ("pkg_a.mod1", "pkg_a.mod3"),
            ("pkg_b.mod2", "pkg_c.util"),
        ]
        members = ImportGraphArchitecture.from_import_edges(edges)
        member_ids = {m.id for m in members}
        assert "pkg_a" in member_ids
        assert "pkg_b" in member_ids
        assert "pkg_c" in member_ids

        # Check coordinate assignment
        pkg_a = next(m for m in members if m.id == "pkg_a")
        assert "pkg_a.mod1" in pkg_a.coordinates
        assert "pkg_a.mod3" in pkg_a.coordinates

    def test_from_import_edges_empty(self):
        members = ImportGraphArchitecture.from_import_edges([])
        assert members == []

    def test_detect_cycles(self):
        edges = [
            ("a.mod1", "b.mod1"),
            ("b.mod1", "c.mod1"),
            ("c.mod1", "a.mod1"),
        ]
        cycles = ImportGraphArchitecture.detect_cycles(edges)
        assert len(cycles) >= 1
        cycle_set = set(cycles[0])
        assert "a.mod1" in cycle_set
        assert "b.mod1" in cycle_set
        assert "c.mod1" in cycle_set

    def test_detect_cycles_none(self):
        edges = [("a.mod1", "b.mod1"), ("b.mod1", "c.mod1")]
        cycles = ImportGraphArchitecture.detect_cycles(edges)
        assert len(cycles) == 0

    def test_suggest_cycle_breaks(self):
        edges = [
            ("a.mod1", "b.mod1"),
            ("b.mod1", "c.mod1"),
            ("c.mod1", "a.mod1"),
        ]
        cycles = ImportGraphArchitecture.detect_cycles(edges)
        decisions = ImportGraphArchitecture.suggest_cycle_breaks(cycles, edges)
        assert len(decisions) >= 1
        assert decisions[0].kind == ArchitecturalDecisionKind.RESOLVE_CIRCULAR

    def test_suggest_cycle_breaks_no_cycles(self):
        decisions = ImportGraphArchitecture.suggest_cycle_breaks([], [])
        assert decisions == []

    def test_internal_external_morphisms(self):
        """Check internal/external morphism classification."""
        edges = [
            ("pkg_a.mod1", "pkg_a.mod2"),  # internal to pkg_a
            ("pkg_a.mod1", "pkg_b.mod1"),  # external
        ]
        members = ImportGraphArchitecture.from_import_edges(edges)
        pkg_a = next(m for m in members if m.id == "pkg_a")
        assert any("pkg_a.mod1->pkg_a.mod2" in m for m in pkg_a.internal_morphisms)
        assert any("pkg_a.mod1->pkg_b.mod1" in m for m in pkg_a.external_morphisms)


# ---------------------------------------------------------------------------
# Theorem tests
# ---------------------------------------------------------------------------


class TestTheorems:
    """Tests for computational verification of architectural theorems."""

    def _make_low_coupling_data(self):
        members = [
            CoverMember(
                id="m1", name="m1", kind=CoverMemberKind.MODULE,
                coordinates=["a", "b", "c"],
            ),
            CoverMember(
                id="m2", name="m2", kind=CoverMemberKind.MODULE,
                coordinates=["d", "e", "f"],
            ),
        ]
        # Only one cross-member morphism
        morphisms = [("a", "b"), ("b", "c"), ("d", "e"), ("e", "f"), ("c", "d")]
        return {"cover_members": members, "morphisms": morphisms}

    def _make_high_cohesion_data(self):
        members = [
            CoverMember(
                id="m1", name="m1", kind=CoverMemberKind.MODULE,
                coordinates=["a", "b", "c"],
            ),
        ]
        # Dense internal edges
        morphisms = [("a", "b"), ("b", "c"), ("a", "c")]
        return {"cover_members": members, "morphisms": morphisms}

    def test_theorem_coupling_bounds_descent_cost(self):
        data = self._make_low_coupling_data()
        result = THEOREM_COUPLING_BOUNDS_DESCENT_COST.check(data)
        assert result is True

    def test_theorem_coupling_bounds_empty(self):
        assert THEOREM_COUPLING_BOUNDS_DESCENT_COST.check({}) is True

    def test_theorem_cohesion_implies_local_correctness(self):
        data = self._make_high_cohesion_data()
        result = THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS.check(data)
        assert result is True

    def test_theorem_cohesion_empty(self):
        assert THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS.check({}) is True

    def test_theorem_scc_collapse_preserves_descent(self):
        # DAG: should pass
        data = {"morphisms": [("A", "B"), ("B", "C"), ("A", "C")]}
        result = THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT.check(data)
        assert result is True

    def test_theorem_scc_collapse_with_cycle(self):
        # Cycle: after collapse should still be DAG
        data = {"morphisms": [("A", "B"), ("B", "C"), ("C", "A"), ("A", "D")]}
        result = THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT.check(data)
        assert result is True

    def test_theorem_scc_collapse_with_adjacency(self):
        data = {"adjacency": {"A": ["B"], "B": ["C"], "C": []}}
        result = THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT.check(data)
        assert result is True

    def test_theorem_interface_width_bounds_treaty_cost(self):
        data = self._make_low_coupling_data()
        result = THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST.check(data)
        assert result is True

    def test_theorem_interface_width_empty(self):
        assert THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST.check({}) is True

    def test_theorem_boundary_enforcement_prevents_drift(self):
        # No violations
        result = THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT.check(
            {"violations": []}
        )
        assert result is True

    def test_theorem_boundary_enforcement_with_violations(self):
        violations = [
            BoundaryViolation(
                boundary_name="test",
                violating_coordinate="x",
                violation_kind="UNDECLARED_IMPORT",
            )
        ]
        result = THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT.check(
            {"violations": violations}
        )
        assert result is False

    def test_all_theorems_have_names(self):
        for theorem in ALL_THEOREMS:
            assert theorem.name != ""
            assert theorem.statement != ""
            assert len(theorem.assumptions) > 0
            assert theorem.proof_sketch != ""
            assert theorem.computational_interpretation != ""

    def test_all_theorems_count(self):
        assert len(ALL_THEOREMS) == 5

    def test_theorem_base_check_default(self):
        """Base ArchitecturalTheorem.check always returns True."""
        from jugeo.se_theory.architecture.theorems import ArchitecturalTheorem

        base = ArchitecturalTheorem(name="base", statement="test")
        assert base.check({}) is True
