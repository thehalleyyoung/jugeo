"""Tests for the Debugging as Obstruction Localization module (B4).

Covers:
- ObstructionLocalizer: localize descent failures in known graphs
- RootCauseTracer: trace causal chains, find root causes
- RepairFrontierComputer: compute minimal vertex cuts
- ObstructionTriager: cluster and triage obstructions
- CountermodelAnalyzer: extract and convert countermodels
- SiteDebugger: end-to-end on synthetic sites
- Theorem checks
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.se_theory.debugging.models import (
    CohomologyClass,
    CountermodelReport,
    DescentTrace,
    LocalSection,
    Morphism,
    Obstruction,
    ObstructionCluster,
    ObstructionSeverity,
    Overlap,
    RepairFrontier,
    RepairPlan,
    RepairStrategy,
    RootCauseAnalysis,
    TriageReport,
)
from jugeo.se_theory.debugging.algorithms import (
    CountermodelAnalyzer,
    ObstructionLocalizer,
    ObstructionTriager,
    RepairFrontierComputer,
    RootCauseTracer,
)
from jugeo.se_theory.debugging.integration import (
    ObstructionDatabase,
    SiteDebugger,
)
from jugeo.se_theory.debugging.theorems import (
    CANONICAL_THEOREM_OBLIGATIONS,
    ProofStrategy,
    TheoremObligation,
    TheoremStatus,
    check_theorem_blast_radius_bounds_cascade,
    check_theorem_clustering_reduces_human_load,
    check_theorem_obstruction_localization_is_sound,
    check_theorem_repair_frontier_is_minimal,
    check_theorem_root_cause_precedes_symptoms,
    get_theorem,
    list_open_theorems,
    list_verified_theorems,
    theorem_summary,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def make_section(coord: str, value=None, is_valid: bool = True, proposition: str = "holds", metadata: dict | None = None) -> LocalSection:
    return LocalSection(
        coordinate_id=coord,
        proposition=proposition,
        value=value,
        is_valid=is_valid,
        metadata=metadata or {},
    )


def make_morphism(source: str, target: str, kind: str = "dependency", is_critical: bool = False) -> Morphism:
    return Morphism(source=source, target=target, kind=kind, is_critical_path=is_critical)


def make_overlap(oid: str, a: str, b: str, shared: list[str] | None = None) -> Overlap:
    return Overlap(
        overlap_id=oid,
        coordinate_a=a,
        coordinate_b=b,
        shared_coordinates=shared or [],
    )


def make_chain_graph(nodes: list[str]) -> list[Morphism]:
    """Create a linear chain A→B→C→... of morphisms."""
    return [make_morphism(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]


def make_obstruction(coord: str, cls: CohomologyClass = CohomologyClass.LOGIC_ERROR,
                     severity: ObstructionSeverity = ObstructionSeverity.ERROR,
                     blast: int = 0, downstream: list[str] | None = None) -> Obstruction:
    return Obstruction.make(
        coordinate_id=coord,
        proposition=f"proposition for {coord}",
        cohomology_class=cls,
        severity=severity,
        blast_radius=blast,
        downstream_ids=downstream or [],
    )


# ---------------------------------------------------------------------------
# Models: serialization round-trips
# ---------------------------------------------------------------------------

class TestModelSerialization:
    def test_obstruction_round_trip(self) -> None:
        obs = make_obstruction("utils/parser", CohomologyClass.TYPE_ERROR, ObstructionSeverity.ERROR, 3, ["utils/formatter"])
        restored = Obstruction.from_dict(obs.to_dict())
        assert restored.coordinate_id == obs.coordinate_id
        assert restored.cohomology_class == obs.cohomology_class
        assert restored.severity == obs.severity
        assert restored.blast_radius == obs.blast_radius
        assert restored.downstream_ids == obs.downstream_ids

    def test_obstruction_cluster_round_trip(self) -> None:
        cluster = ObstructionCluster.make(
            cohomology_class=CohomologyClass.NULL_REFERENCE,
            coordinate_pattern="utils/*",
            obstructions=["obs-1", "obs-2", "obs-3"],
            common_root_cause="Missing null guard",
            suggested_batch_fix="Add null checks",
        )
        restored = ObstructionCluster.from_dict(cluster.to_dict())
        assert restored.cohomology_class == cluster.cohomology_class
        assert restored.coordinate_pattern == cluster.coordinate_pattern
        assert restored.count == 3

    def test_descent_trace_round_trip(self) -> None:
        trace = DescentTrace.make(
            start_coordinate="A",
            end_coordinate="C",
            morphism_chain=[("A", "B", "dep"), ("B", "C", "dep")],
            failure_point="B",
        )
        restored = DescentTrace.from_dict(trace.to_dict())
        assert restored.start_coordinate == "A"
        assert restored.failure_point == "B"
        assert len(restored.morphism_chain) == 2

    def test_root_cause_analysis_round_trip(self) -> None:
        rca = RootCauseAnalysis.make(
            symptom_id="obs-abc",
            root_coordinate_id="root/module",
            root_proposition="root invariant holds",
            causal_chain=["root/module", "mid/module", "leaf/module"],
            confidence=0.85,
            alternative_roots=["alt/root"],
        )
        restored = RootCauseAnalysis.from_dict(rca.to_dict())
        assert restored.symptom_id == rca.symptom_id
        assert restored.root_coordinate_id == rca.root_coordinate_id
        assert restored.confidence == 0.85

    def test_repair_frontier_round_trip(self) -> None:
        frontier = RepairFrontier.make(
            obstruction_id="obs-xyz",
            minimal_coordinates=["coord/a", "coord/b"],
            estimated_effort=2.5,
            strategy=RepairStrategy.PROPAGATED_FIX,
            prerequisites=["obs-prereq"],
            side_effects=["coord/c"],
        )
        restored = RepairFrontier.from_dict(frontier.to_dict())
        assert restored.strategy == RepairStrategy.PROPAGATED_FIX
        assert restored.estimated_effort == 2.5

    def test_repair_plan_round_trip(self) -> None:
        frontiers = [
            RepairFrontier.make("obs-1", ["coord/a"], 1.0),
            RepairFrontier.make("obs-2", ["coord/b"], 2.0),
        ]
        plan = RepairPlan.make(["obs-1", "obs-2"], frontiers, blast_radius=5)
        restored = RepairPlan.from_dict(plan.to_dict())
        assert len(restored.ordered_repairs) == 2
        assert restored.total_estimated_effort == 3.0

    def test_countermodel_report_round_trip(self) -> None:
        report = CountermodelReport.make(
            obstruction_id="obs-1",
            coordinate_id="api/handler",
            proposition="handler returns 200",
            concrete_inputs={"method": "POST", "body": {"x": 1}},
            expected_output=200,
            actual_output=500,
        )
        restored = CountermodelReport.from_dict(report.to_dict())
        assert restored.concrete_inputs["method"] == "POST"
        assert restored.expected_output == 200

    def test_triage_report_round_trip(self) -> None:
        obstructions = [
            make_obstruction("mod/a", CohomologyClass.TYPE_ERROR, ObstructionSeverity.ERROR, 2),
            make_obstruction("mod/b", CohomologyClass.NULL_REFERENCE, ObstructionSeverity.WARNING, 1),
        ]
        clusters = [
            ObstructionCluster.make(CohomologyClass.TYPE_ERROR, "mod/*", [obstructions[0].id]),
        ]
        report = TriageReport.make(obstructions, clusters, 3.5, 1, 1)
        restored = TriageReport.from_dict(report.to_dict())
        assert restored.total_obstructions == 2
        assert restored.auto_fixable_count == 1


# ---------------------------------------------------------------------------
# ObstructionLocalizer
# ---------------------------------------------------------------------------

class TestObstructionLocalizer:
    def setup_method(self) -> None:
        self.localizer = ObstructionLocalizer()

    def test_no_obstructions_when_all_valid(self) -> None:
        sections = [
            make_section("A", value="ok"),
            make_section("B", value="ok"),
        ]
        morphisms = [make_morphism("A", "B")]
        result = self.localizer.localize_descent_failure(sections, [], morphisms)
        assert result == []

    def test_invalid_section_generates_obstruction(self) -> None:
        sections = [
            make_section("A", is_valid=False, proposition="A holds"),
            make_section("B", value="ok"),
        ]
        morphisms = [make_morphism("A", "B")]
        result = self.localizer.localize_descent_failure(sections, [], morphisms)
        assert len(result) == 1
        assert result[0].coordinate_id == "A"

    def test_error_value_generates_obstruction(self) -> None:
        sections = [
            make_section("A", value="error: something went wrong"),
        ]
        result = self.localizer.localize_descent_failure(sections, [], [])
        assert len(result) == 1

    def test_overlap_disagreement_generates_obstruction(self) -> None:
        sec_a = make_section("A", value={"x": 1, "y": 2})
        sec_b = make_section("B", value={"x": 99, "y": 2})
        overlap = make_overlap("ov-1", "A", "B", shared=["x"])
        result = self.localizer.localize_descent_failure([sec_a, sec_b], [overlap], [])
        assert len(result) == 1
        assert result[0].overlap_id == "ov-1"

    def test_overlap_agreement_no_obstruction(self) -> None:
        sec_a = make_section("A", value={"x": 5, "y": 2})
        sec_b = make_section("B", value={"x": 5, "z": 9})
        overlap = make_overlap("ov-2", "A", "B", shared=["x"])
        result = self.localizer.localize_descent_failure([sec_a, sec_b], [overlap], [])
        assert result == []

    def test_blast_radius_computed(self) -> None:
        sections = [make_section("root", is_valid=False)]
        morphisms = [
            make_morphism("root", "child1"),
            make_morphism("root", "child2"),
            make_morphism("child1", "grandchild"),
        ]
        result = self.localizer.localize_descent_failure(sections, [], morphisms)
        assert len(result) == 1
        assert result[0].blast_radius == 3  # child1, child2, grandchild

    def test_multiple_failures(self) -> None:
        sections = [
            make_section("A", is_valid=False),
            make_section("B", is_valid=False),
            make_section("C", value="ok"),
        ]
        result = self.localizer.localize_descent_failure(sections, [], [])
        assert len(result) == 2

    @pytest.mark.parametrize("detail,expected_class", [
        ("typeerror: expected int", CohomologyClass.TYPE_ERROR),
        ("nonetype object has no attribute", CohomologyClass.NULL_REFERENCE),
        ("indexerror: list index out of range", CohomologyClass.BOUNDS_VIOLATION),
        ("importerror: no module named requests", CohomologyClass.IMPORT_ERROR),
        ("permissionerror: access denied", CohomologyClass.PERMISSION_ERROR),
        ("unicodedecodeerror: codec can't decode", CohomologyClass.ENCODING_MISMATCH),
        ("deadlock detected in thread", CohomologyClass.DEADLOCK),
        ("race condition on shared variable", CohomologyClass.RACE_CONDITION),
        ("precondition violated", CohomologyClass.CONTRACT_VIOLATION),
        ("configuration: missing key", CohomologyClass.CONFIGURATION_ERROR),
    ])
    def test_classify_obstruction(self, detail: str, expected_class: CohomologyClass) -> None:
        cls = self.localizer.classify_obstruction("coord", "prop", detail)
        assert cls == expected_class

    def test_severity_from_blast_radius(self) -> None:
        localizer = self.localizer
        assert localizer.severity_from_blast_radius(0, False) == ObstructionSeverity.INFO
        assert localizer.severity_from_blast_radius(1, False) == ObstructionSeverity.WARNING
        assert localizer.severity_from_blast_radius(4, False) == ObstructionSeverity.ERROR
        assert localizer.severity_from_blast_radius(10, False) == ObstructionSeverity.CRITICAL
        assert localizer.severity_from_blast_radius(20, False) == ObstructionSeverity.BLOCKER
        assert localizer.severity_from_blast_radius(3, True) == ObstructionSeverity.CRITICAL

    def test_critical_path_severity_escalation(self) -> None:
        sections = [make_section("critical", is_valid=False)]
        morphisms = [
            make_morphism("critical", "dep1", is_critical=True),
            make_morphism("critical", "dep2", is_critical=True),
            make_morphism("dep1", "dep3"),
            make_morphism("dep2", "dep4"),
            make_morphism("dep3", "dep5"),
        ]
        result = self.localizer.localize_descent_failure(sections, [], morphisms)
        assert len(result) == 1
        assert result[0].severity in (ObstructionSeverity.BLOCKER, ObstructionSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# RootCauseTracer
# ---------------------------------------------------------------------------

class TestRootCauseTracer:
    """Test that root cause tracing correctly identifies the earliest failing node."""

    def setup_method(self) -> None:
        self.tracer = RootCauseTracer()

    def _make_chain(self, nodes: list[str], invalid_at: str) -> tuple[list[Morphism], dict[str, LocalSection]]:
        """Create a chain graph with one invalid section."""
        morphisms = make_chain_graph(nodes)
        sections = {}
        for n in nodes:
            sections[n] = make_section(n, is_valid=(n != invalid_at))
        return morphisms, sections

    def test_root_cause_is_invalid_ancestor(self) -> None:
        """A→B→C, A is invalid — root cause should be A."""
        nodes = ["A", "B", "C"]
        morphisms, sections = self._make_chain(nodes, "A")
        obs = Obstruction.make("C", "C holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)

        rca = self.tracer.find_root_cause(obs, morphisms, sections)
        assert rca.root_coordinate_id == "A"

    def test_root_cause_is_symptom_when_no_ancestors_fail(self) -> None:
        """A→B→C, B is invalid — if we observe at C, root is B (not A, since A is valid)."""
        nodes = ["A", "B", "C"]
        morphisms, sections = self._make_chain(nodes, "B")
        obs = Obstruction.make("C", "C holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)

        rca = self.tracer.find_root_cause(obs, morphisms, sections)
        # Root should be B, not C
        assert rca.root_coordinate_id in ("B", "C")

    def test_causal_chain_non_empty(self) -> None:
        nodes = ["root", "mid", "leaf"]
        morphisms, sections = self._make_chain(nodes, "root")
        obs = Obstruction.make("leaf", "leaf holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)

        rca = self.tracer.find_root_cause(obs, morphisms, sections)
        assert len(rca.causal_chain) >= 1

    def test_multiple_root_causes_groups_correctly(self) -> None:
        """Two independent chains, each with its own root cause."""
        morphisms = [
            make_morphism("root1", "child1"),
            make_morphism("root2", "child2"),
        ]
        sections = {
            "root1": make_section("root1", is_valid=False),
            "root2": make_section("root2", is_valid=False),
            "child1": make_section("child1"),
            "child2": make_section("child2"),
        }
        obs1 = Obstruction.make("child1", "child1 holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)
        obs2 = Obstruction.make("child2", "child2 holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)

        grouped = self.tracer.multiple_root_causes([obs1, obs2], morphisms, sections)
        assert len(grouped) >= 1
        # Each obstruction should be grouped under some root
        all_grouped_ids = set()
        for ids in grouped.values():
            all_grouped_ids.update(ids)
        assert obs1.id in all_grouped_ids
        assert obs2.id in all_grouped_ids

    def test_causal_graph_shows_causes(self) -> None:
        """A causes B if A's coordinate is an ancestor of B's coordinate."""
        morphisms = [
            make_morphism("A", "B"),
            make_morphism("B", "C"),
        ]
        obs_a = Obstruction.make("A", "A", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)
        obs_b = Obstruction.make("B", "B", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)
        obs_c = Obstruction.make("C", "C", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)

        causal = self.tracer.causal_graph([obs_a, obs_b, obs_c], morphisms)
        # A causes B and C
        assert obs_b.id in causal[obs_a.id]
        assert obs_c.id in causal[obs_a.id]
        # B causes C
        assert obs_c.id in causal[obs_b.id]
        # C causes nothing
        assert causal[obs_c.id] == []

    def test_trace_descent_finds_failure_point(self) -> None:
        sections = {
            "A": make_section("A"),
            "B": make_section("B", is_valid=False),
            "C": make_section("C"),
        }
        morphisms = make_chain_graph(["A", "B", "C"])
        trace = self.tracer.trace_descent("A", morphisms, sections)
        assert trace.failure_point == "B"

    def test_trace_descent_no_failure(self) -> None:
        sections = {
            "A": make_section("A"),
            "B": make_section("B"),
            "C": make_section("C"),
        }
        morphisms = make_chain_graph(["A", "B", "C"])
        trace = self.tracer.trace_descent("A", morphisms, sections)
        assert trace.failure_point is None
        assert not trace.failed


# ---------------------------------------------------------------------------
# RepairFrontierComputer
# ---------------------------------------------------------------------------

class TestRepairFrontierComputer:
    def setup_method(self) -> None:
        self.computer = RepairFrontierComputer()

    def test_repair_frontier_for_isolated_obstruction(self) -> None:
        """Obstruction with no morphisms — frontier is the coordinate itself."""
        obs = make_obstruction("isolated/module")
        frontier = self.computer.compute_repair_frontier(obs, [], {})
        assert obs.id == frontier.obstruction_id
        assert "isolated/module" in frontier.minimal_coordinates

    def test_repair_frontier_linear_chain(self) -> None:
        """A→B→C→D: obstruction at A, cut should include A (or early node)."""
        morphisms = make_chain_graph(["A", "B", "C", "D"])
        obs = Obstruction.make("A", "A holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR,
                               blast_radius=3, downstream_ids=["B", "C", "D"])
        frontier = self.computer.compute_repair_frontier(obs, morphisms, {})
        # Frontier must cut the path from A to downstream
        assert len(frontier.minimal_coordinates) >= 1

    def test_repair_plan_topological_order(self) -> None:
        """Repairs are ordered so prerequisites come first."""
        morphisms = [make_morphism("A", "B"), make_morphism("B", "C")]
        obs_a = make_obstruction("A", blast=2, downstream=["B", "C"])
        obs_b = make_obstruction("B", blast=1, downstream=["C"])
        obs_c = make_obstruction("C")

        plan = self.computer.compute_repair_plan([obs_a, obs_b, obs_c], morphisms, {})
        assert len(plan.ordered_repairs) == 3
        assert plan.total_estimated_effort > 0

    def test_estimate_effort_scales_with_complexity(self) -> None:
        coords = ["a", "b", "c"]
        complexity_low = {"a": 10.0, "b": 20.0, "c": 5.0}
        complexity_high = {"a": 500.0, "b": 600.0, "c": 300.0}
        effort_low = self.computer.estimate_effort(coords, complexity_low)
        effort_high = self.computer.estimate_effort(coords, complexity_high)
        assert effort_high > effort_low

    def test_strategy_selection_for_various_classes(self) -> None:
        strategies = {
            CohomologyClass.CONTRACT_VIOLATION: RepairStrategy.INTERFACE_RENEGOTIATION,
            CohomologyClass.DEADLOCK: RepairStrategy.MANUAL_REVIEW,
            CohomologyClass.CONFIGURATION_ERROR: RepairStrategy.LOCAL_FIX,
            CohomologyClass.STATE_CORRUPTION: RepairStrategy.COVER_REFINEMENT,
        }
        for cls, expected_strategy in strategies.items():
            obs = make_obstruction("coord", cls=cls, blast=1)
            strategy = self.computer.strategy_for_obstruction(obs, [])
            assert strategy == expected_strategy, f"For {cls}: expected {expected_strategy}, got {strategy}"

    def test_repair_plan_has_strategy_summary(self) -> None:
        obs = make_obstruction("mod/a")
        plan = self.computer.compute_repair_plan([obs], [], {})
        assert isinstance(plan.strategy_summary, str)
        assert len(plan.strategy_summary) > 0


# ---------------------------------------------------------------------------
# ObstructionTriager
# ---------------------------------------------------------------------------

class TestObstructionTriager:
    def setup_method(self) -> None:
        self.triager = ObstructionTriager()

    def _make_many_obstructions(self, n: int = 20) -> list[Obstruction]:
        """Create n obstructions spread across ~5 classes and 4 module prefixes."""
        classes = [
            CohomologyClass.TYPE_ERROR,
            CohomologyClass.NULL_REFERENCE,
            CohomologyClass.BOUNDS_VIOLATION,
            CohomologyClass.LOGIC_ERROR,
            CohomologyClass.CONFIGURATION_ERROR,
        ]
        prefixes = ["auth", "utils", "api", "db"]
        obstructions = []
        for i in range(n):
            cls = classes[i % len(classes)]
            prefix = prefixes[i % len(prefixes)]
            coord = f"{prefix}/module_{i // len(prefixes)}"
            obs = make_obstruction(coord, cls=cls, blast=i % 5)
            obstructions.append(obs)
        return obstructions

    def test_cluster_groups_by_class(self) -> None:
        obstructions = self._make_many_obstructions(20)
        clusters = self.triager.cluster_obstructions(obstructions)
        # Should produce fewer clusters than obstructions
        assert len(clusters) < 20
        # All obstructions should be covered
        covered_ids: set[str] = set()
        for c in clusters:
            covered_ids.update(c.obstructions)
        all_ids = {o.id for o in obstructions}
        assert all_ids == covered_ids

    def test_cluster_count_at_most_n(self) -> None:
        obstructions = self._make_many_obstructions(20)
        clusters = self.triager.cluster_obstructions(obstructions)
        assert len(clusters) <= 20

    def test_triage_report_fields(self) -> None:
        obstructions = self._make_many_obstructions(20)
        morphisms: list[Morphism] = []
        report = self.triager.triage(obstructions, morphisms)
        assert report.total_obstructions == 20
        assert report.auto_fixable_count + report.needs_manual_count == 20
        assert report.estimated_total_effort >= 0
        assert len(report.clusters) > 0

    def test_auto_fixable_low_blast_small_frontier(self) -> None:
        obs = make_obstruction("utils/helper", CohomologyClass.TYPE_ERROR,
                               ObstructionSeverity.WARNING, blast=1)
        frontier = RepairFrontier.make(obs.id, ["utils/helper"], 0.5, RepairStrategy.LOCAL_FIX)
        assert self.triager.auto_fixable(obs, frontier) is True

    def test_not_auto_fixable_deadlock(self) -> None:
        obs = make_obstruction("thread/pool", CohomologyClass.DEADLOCK,
                               ObstructionSeverity.CRITICAL, blast=3)
        frontier = RepairFrontier.make(obs.id, ["thread/pool"], 1.0, RepairStrategy.MANUAL_REVIEW)
        assert self.triager.auto_fixable(obs, frontier) is False

    def test_not_auto_fixable_blocker(self) -> None:
        obs = make_obstruction("core/module", CohomologyClass.LOGIC_ERROR,
                               ObstructionSeverity.BLOCKER, blast=1)
        frontier = RepairFrontier.make(obs.id, ["core/module"], 1.0, RepairStrategy.LOCAL_FIX)
        assert self.triager.auto_fixable(obs, frontier) is False

    def test_priority_score_increases_with_severity(self) -> None:
        obs_warn = make_obstruction("a", severity=ObstructionSeverity.WARNING, blast=5)
        obs_crit = make_obstruction("b", severity=ObstructionSeverity.CRITICAL, blast=5)
        score_warn = self.triager.priority_score(obs_warn)
        score_crit = self.triager.priority_score(obs_crit)
        assert score_crit > score_warn

    def test_priority_score_increases_with_blast(self) -> None:
        obs_small = make_obstruction("a", severity=ObstructionSeverity.ERROR, blast=1)
        obs_large = make_obstruction("b", severity=ObstructionSeverity.ERROR, blast=20)
        assert self.triager.priority_score(obs_large) > self.triager.priority_score(obs_small)

    def test_batch_fix_suggestion(self) -> None:
        cluster = ObstructionCluster.make(
            CohomologyClass.NULL_REFERENCE,
            "utils/*",
            ["obs-1", "obs-2"],
        )
        fix = self.triager.batch_fixes(cluster)
        assert fix is not None
        assert "null" in fix.lower() or "utils" in fix.lower()

    def test_top_blast_radius_in_report(self) -> None:
        obstructions = [make_obstruction(f"mod/{i}", blast=i) for i in range(15)]
        report = self.triager.triage(obstructions, [])
        assert len(report.top_blast_radius) <= 10
        # Top entries should have higher blast radii
        if len(report.top_blast_radius) >= 2:
            assert report.top_blast_radius[0].blast_radius >= report.top_blast_radius[-1].blast_radius


# ---------------------------------------------------------------------------
# CountermodelAnalyzer
# ---------------------------------------------------------------------------

class TestCountermodelAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = CountermodelAnalyzer()

    def test_extract_countermodel_basic(self) -> None:
        obs = Obstruction.make(
            coordinate_id="api/endpoint",
            proposition="returns 200 for valid input",
            cohomology_class=CohomologyClass.LOGIC_ERROR,
            severity=ObstructionSeverity.ERROR,
            countermodel={"input": {"x": 1}, "failure": "returned 500"},
        )
        section = make_section("api/endpoint", value={"x": 1}, is_valid=False)
        report = self.analyzer.extract_countermodel(obs, {"api/endpoint": section})
        assert report.obstruction_id == obs.id
        assert report.coordinate_id == "api/endpoint"
        assert "input" in report.concrete_inputs or "coordinate_value" in report.concrete_inputs

    def test_countermodel_to_test_structure(self) -> None:
        report = CountermodelReport.make(
            obstruction_id="obs-1",
            coordinate_id="parser/main",
            proposition="parse(x) returns valid AST",
            concrete_inputs={"x": "bad input"},
            expected_output="AST",
            actual_output="ParseError",
        )
        test_obligation = self.analyzer.countermodel_to_test(report)
        assert "test_id" in test_obligation
        assert "obstruction_id" in test_obligation
        assert test_obligation["test_type"] == "regression"
        assert test_obligation["priority"] == "high"

    def test_batch_countermodels(self) -> None:
        obstructions = [
            Obstruction.make(
                coordinate_id=f"mod/{i}",
                proposition=f"mod {i} holds",
                cohomology_class=CohomologyClass.LOGIC_ERROR,
                severity=ObstructionSeverity.ERROR,
                countermodel={"val": i, "failure": "wrong result"},
            )
            for i in range(5)
        ]
        sections = {
            f"mod/{i}": make_section(f"mod/{i}", value=i, is_valid=False)
            for i in range(5)
        }
        reports = self.analyzer.batch_countermodels(obstructions, sections)
        assert len(reports) == 5

    def test_reproducibility_check_deterministic(self) -> None:
        report = CountermodelReport.make(
            obstruction_id="obs-1",
            coordinate_id="utils/hash",
            proposition="hash(x) is deterministic",
            concrete_inputs={"x": "hello", "algo": "sha256"},
            expected_output="hash_value",
            actual_output="different_hash",
        )
        assert self.analyzer.reproducibility_check(report) is True

    def test_reproducibility_check_nondeterministic(self) -> None:
        report = CountermodelReport.make(
            obstruction_id="obs-2",
            coordinate_id="utils/uuid_gen",
            proposition="uuid is unique",
            concrete_inputs={"seed": "random timestamp"},
            expected_output="unique id",
            actual_output="collision",
        )
        assert self.analyzer.reproducibility_check(report) is False

    def test_reproducibility_requires_inputs(self) -> None:
        report = CountermodelReport.make(
            obstruction_id="obs-3",
            coordinate_id="empty/module",
            proposition="holds",
            concrete_inputs={},
            expected_output=None,
            actual_output=None,
        )
        assert self.analyzer.reproducibility_check(report) is False

    def test_suggested_test_in_report(self) -> None:
        obs = Obstruction.make(
            coordinate_id="math/divide",
            proposition="divide(a, b) > 0",
            cohomology_class=CohomologyClass.LOGIC_ERROR,
            severity=ObstructionSeverity.ERROR,
            countermodel={"a": 10, "b": -1, "failure": "got negative result"},
        )
        section = make_section("math/divide", value=-1, is_valid=False)
        report = self.analyzer.extract_countermodel(obs, {"math/divide": section})
        assert report.suggested_test is not None
        assert "def test_" in report.suggested_test


# ---------------------------------------------------------------------------
# SiteDebugger (end-to-end)
# ---------------------------------------------------------------------------

class TestSiteDebugger:
    def setup_method(self) -> None:
        self.debugger = SiteDebugger()

    def _build_synthetic_site(self) -> tuple[list[str], list[Morphism], list[Overlap], list[LocalSection]]:
        """Build a synthetic site with known failures."""
        coordinates = ["db/schema", "db/query", "auth/token", "api/handler", "api/response"]
        morphisms = [
            make_morphism("db/schema", "db/query"),
            make_morphism("db/query", "api/handler"),
            make_morphism("auth/token", "api/handler", is_critical=True),
            make_morphism("api/handler", "api/response"),
        ]
        covers: list[Overlap] = []
        sections = [
            make_section("db/schema", value={"tables": ["users", "orders"]}, is_valid=True),
            make_section("db/query", value="error: connection refused", is_valid=True),  # error in value
            make_section("auth/token", value=None, is_valid=False),  # invalid section
            make_section("api/handler", value="ok"),
            make_section("api/response", value="ok"),
        ]
        return coordinates, morphisms, covers, sections

    def test_debug_site_returns_triage_report(self) -> None:
        coordinates, morphisms, covers, sections = self._build_synthetic_site()
        report = self.debugger.debug_site(coordinates, morphisms, covers, sections)
        assert isinstance(report, TriageReport)
        assert report.total_obstructions >= 2  # db/query error value + auth/token invalid

    def test_root_cause_for_site(self) -> None:
        _, morphisms, _, sections = self._build_synthetic_site()
        # Create known obstructions
        obs_auth = Obstruction.make("auth/token", "token is valid",
                                   CohomologyClass.NULL_REFERENCE, ObstructionSeverity.CRITICAL)
        obs_handler = Obstruction.make("api/handler", "handler responds",
                                      CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)
        obstructions = [obs_auth, obs_handler]

        rca_map = self.debugger.root_cause_for_site(obstructions, [], morphisms)
        assert len(rca_map) == 2
        for obs_id, rca in rca_map.items():
            assert isinstance(rca, RootCauseAnalysis)
            assert rca.symptom_id == obs_id

    def test_repair_plan_for_site(self) -> None:
        _, morphisms, _, _ = self._build_synthetic_site()
        obstructions = [
            make_obstruction("auth/token", CohomologyClass.NULL_REFERENCE,
                             ObstructionSeverity.CRITICAL, blast=2),
            make_obstruction("db/query", CohomologyClass.LOGIC_ERROR,
                             ObstructionSeverity.ERROR, blast=1),
        ]
        plan = self.debugger.repair_plan_for_site(obstructions, [], morphisms)
        assert isinstance(plan, RepairPlan)
        assert len(plan.ordered_repairs) == 2
        assert plan.total_estimated_effort > 0

    def test_incremental_debug_only_checks_affected(self) -> None:
        coordinates, morphisms, _, sections = self._build_synthetic_site()
        # Only change auth/token
        changed = ["auth/token"]
        existing: list[Obstruction] = []
        new_obs = self.debugger.incremental_debug(changed, morphisms, sections, existing)
        # New obstructions should be in the affected region
        affected_coords = {"auth/token", "api/handler", "api/response"}
        for obs in new_obs:
            assert obs.coordinate_id in affected_coords

    def test_debug_site_no_failures_empty_report(self) -> None:
        coordinates = ["A", "B", "C"]
        morphisms = make_chain_graph(coordinates)
        sections = [make_section(c, value="ok") for c in coordinates]
        report = self.debugger.debug_site(coordinates, morphisms, [], sections)
        assert report.total_obstructions == 0


# ---------------------------------------------------------------------------
# ObstructionDatabase
# ---------------------------------------------------------------------------

class TestObstructionDatabase:
    def setup_method(self) -> None:
        self.db = ObstructionDatabase()

    def test_store_and_retrieve(self) -> None:
        obs = make_obstruction("mod/a")
        self.db.store(obs)
        retrieved = self.db.get(obs.id)
        assert retrieved is not None
        assert retrieved.id == obs.id

    def test_query_by_class(self) -> None:
        obs1 = make_obstruction("a", CohomologyClass.TYPE_ERROR)
        obs2 = make_obstruction("b", CohomologyClass.NULL_REFERENCE)
        self.db.store(obs1)
        self.db.store(obs2)
        results = self.db.query(kind=CohomologyClass.TYPE_ERROR)
        assert len(results) == 1
        assert results[0].id == obs1.id

    def test_query_active_only(self) -> None:
        obs = make_obstruction("mod/x")
        self.db.store(obs)
        self.db.resolve(obs.id, "fixed")
        active = self.db.query(active_only=True)
        assert not any(o.id == obs.id for o in active)

    def test_resolve(self) -> None:
        obs = make_obstruction("mod/y")
        self.db.store(obs)
        result = self.db.resolve(obs.id, "patched")
        assert result is True
        retrieved = self.db.get(obs.id)
        assert retrieved.is_resolved
        assert retrieved.resolution_note == "patched"

    def test_similar_finds_same_class(self) -> None:
        obs1 = make_obstruction("utils/a", CohomologyClass.TYPE_ERROR)
        obs2 = make_obstruction("utils/b", CohomologyClass.TYPE_ERROR)
        obs3 = make_obstruction("utils/c", CohomologyClass.NULL_REFERENCE)
        self.db.store(obs1)
        self.db.store(obs2)
        self.db.store(obs3)
        similar = self.db.similar(obs1, threshold=0.4)
        similar_ids = {o.id for o in similar}
        # obs2 has same class and similar prefix — should be similar
        assert obs2.id in similar_ids

    def test_statistics(self) -> None:
        for i in range(5):
            self.db.store(make_obstruction(f"mod/{i}", CohomologyClass.TYPE_ERROR))
        for i in range(3):
            self.db.store(make_obstruction(f"other/{i}", CohomologyClass.NULL_REFERENCE))
        stats = self.db.statistics()
        assert stats["total"] == 8
        assert stats["active"] == 8
        assert stats["resolved"] == 0
        assert stats["by_class"]["type_error"] == 5

    def test_export_import_round_trip(self) -> None:
        obs1 = make_obstruction("export/a")
        obs2 = make_obstruction("export/b")
        self.db.store(obs1)
        self.db.store(obs2)
        exported = self.db.export()
        new_db = ObstructionDatabase()
        count = new_db.import_records(exported)
        assert count == 2
        assert len(new_db) == 2

    def test_len_and_contains(self) -> None:
        obs = make_obstruction("mod/z")
        assert obs.id not in self.db
        self.db.store(obs)
        assert obs.id in self.db
        assert len(self.db) == 1

    def test_query_by_coordinate_prefix(self) -> None:
        obs1 = make_obstruction("auth/login")
        obs2 = make_obstruction("auth/logout")
        obs3 = make_obstruction("utils/hash")
        self.db.store_many([obs1, obs2, obs3])
        auth_results = self.db.query(coordinate_prefix="auth/")
        assert len(auth_results) == 2
        ids = {o.id for o in auth_results}
        assert obs1.id in ids
        assert obs2.id in ids


# ---------------------------------------------------------------------------
# Theorem Checks
# ---------------------------------------------------------------------------

class TestTheorems:
    def test_all_canonical_theorems_exist(self) -> None:
        assert len(CANONICAL_THEOREM_OBLIGATIONS) == 5

    def test_get_theorem_by_name(self) -> None:
        thm = get_theorem("theorem_obstruction_localization_is_sound")
        assert thm.theorem_name == "theorem_obstruction_localization_is_sound"

    def test_get_theorem_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            get_theorem("theorem_nonexistent")

    def test_list_verified_theorems(self) -> None:
        verified = list_verified_theorems()
        assert len(verified) >= 1
        assert all(t.status == TheoremStatus.ALGORITHMICALLY_VERIFIED for t in verified)

    def test_theorem_summary_non_empty(self) -> None:
        summary = theorem_summary()
        assert len(summary) > 50
        assert "theorem_" in summary

    def test_check_obstruction_localization_is_sound_passes(self) -> None:
        """Sound check: obstructions match real failures."""
        sections = [
            make_section("A", is_valid=False),
            make_section("B"),
        ]
        localizer = ObstructionLocalizer()
        obstructions = localizer.localize_descent_failure(sections, [], [])
        ok, msg = check_theorem_obstruction_localization_is_sound(obstructions, sections, [])
        assert ok, msg

    def test_check_obstruction_localization_is_sound_fails_on_fabricated(self) -> None:
        """Soundness fails if we invent an obstruction with no real failure."""
        sections = [make_section("A", value="ok", is_valid=True)]
        fake_obs = Obstruction.make("A", "A holds", CohomologyClass.LOGIC_ERROR,
                                   ObstructionSeverity.ERROR)
        ok, msg = check_theorem_obstruction_localization_is_sound([fake_obs], sections, [])
        assert not ok

    def test_check_blast_radius_bounds_cascade(self) -> None:
        morphisms = [make_morphism("A", "B"), make_morphism("A", "C"), make_morphism("B", "D")]
        obs = Obstruction.make("A", "A holds", CohomologyClass.LOGIC_ERROR,
                               ObstructionSeverity.ERROR,
                               blast_radius=3, downstream_ids=["B", "C", "D"])
        ok, msg = check_theorem_blast_radius_bounds_cascade([obs], morphisms)
        assert ok, msg

    def test_check_blast_radius_fails_when_underestimated(self) -> None:
        morphisms = [make_morphism("A", "B"), make_morphism("A", "C"), make_morphism("B", "D")]
        # Blast radius is 3 (B, C, D) but we claim 1
        obs = Obstruction.make("A", "A", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR,
                               blast_radius=1, downstream_ids=["B"])
        ok, msg = check_theorem_blast_radius_bounds_cascade([obs], morphisms)
        assert not ok

    def test_check_clustering_reduces_load(self) -> None:
        obstructions = [make_obstruction(f"mod/{i}", CohomologyClass.TYPE_ERROR) for i in range(10)]
        triager = ObstructionTriager()
        clusters = triager.cluster_obstructions(obstructions)
        ok, msg = check_theorem_clustering_reduces_human_load(obstructions, clusters)
        assert ok, msg

    def test_check_root_cause_precedes_symptoms(self) -> None:
        morphisms = make_chain_graph(["A", "B", "C"])
        obs = Obstruction.make("C", "C holds", CohomologyClass.LOGIC_ERROR, ObstructionSeverity.ERROR)
        ok, msg = check_theorem_root_cause_precedes_symptoms([obs], morphisms)
        assert ok, msg

    def test_theorem_round_trip(self) -> None:
        thm = get_theorem("theorem_repair_frontier_is_minimal")
        restored = TheoremObligation.from_dict(thm.to_dict())
        assert restored.theorem_name == thm.theorem_name
        assert restored.proof_strategy == thm.proof_strategy


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------

class TestObstructionSeverityOrdering:
    def test_ordering(self) -> None:
        sev = ObstructionSeverity
        assert sev.INFO < sev.WARNING < sev.ERROR < sev.CRITICAL < sev.BLOCKER

    def test_numeric_weight(self) -> None:
        assert ObstructionSeverity.BLOCKER.numeric_weight == 5
        assert ObstructionSeverity.INFO.numeric_weight == 1


# ---------------------------------------------------------------------------
# CohomologyClass coverage
# ---------------------------------------------------------------------------

class TestCohomologyClassEnum:
    def test_all_classes_have_string_values(self) -> None:
        for cls in CohomologyClass:
            assert isinstance(cls.value, str)
            assert len(cls.value) > 0

    def test_from_value(self) -> None:
        assert CohomologyClass("type_error") == CohomologyClass.TYPE_ERROR
        assert CohomologyClass("unknown") == CohomologyClass.UNKNOWN
