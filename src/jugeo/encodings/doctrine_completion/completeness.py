"""
Completeness analysis and planning for the doctrine_completion package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It provides strategies, metrics, analyzers, and planning utilities for assessing
and improving doctrine completeness.  The critical-path analysis, gap-bridging,
and completion planning components are designed to work with the statement and
evidence models from models.py and implementation_evidence.py.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models import (
    DoctrineStatement,
    ImplementationEvidence,
    CompletenessCheck,
    DoctrineGap,
    GapSeverity,
    EvidenceKind,
    StatementStatus,
    ClaimType,
)
from .implementation_evidence import EvidenceAggregator, ConfidenceEstimator

__all__ = [
    "CompletionStrategy",
    "CompletenessMetrics",
    "CompletenessAnalyzer",
    "CriticalPathAnalyzer",
    "DoctrineGraph",
    "CompletionPlan",
    "GapBridger",
    "compute_completion_metrics",
    "plan_completion",
]


# ---------------------------------------------------------------------------
# CompletionStrategy
# ---------------------------------------------------------------------------


class CompletionStrategy(str, Enum):
    """Strategy for doctrine completeness analysis.

    EXHAUSTIVE   — check every statement exhaustively against all evidence.
    SAMPLING     — sample a representative subset of statements.
    CRITICAL_PATH — focus on the critical path of dependent statements.
    RISK_BASED   — prioritise by risk/severity score.
    """

    EXHAUSTIVE = "exhaustive"
    SAMPLING = "sampling"
    CRITICAL_PATH = "critical_path"
    RISK_BASED = "risk_based"


# ---------------------------------------------------------------------------
# CompletenessMetrics
# ---------------------------------------------------------------------------


@dataclass
class CompletenessMetrics:
    """Quantitative metrics summarising doctrine completeness.

    CompletenessMetrics aggregates several dimensions of completeness into
    a single record.  The overall_score() combines coverage, depth, breadth,
    and confidence into a single weighted score.

    Attributes:
        metrics_id: Unique identifier (uuid4).
        coverage: Fraction of statements that are COMPLETE.
        depth: Average grounding depth across all evidence items.
        breadth: Fraction of distinct evidence kinds represented.
        confidence: Average evidence confidence.
        timestamp: When these metrics were computed.
        strategy_used: Which CompletionStrategy was used.
        statement_count: Total number of statements evaluated.
        evidence_count: Total number of evidence items evaluated.
    """

    metrics_id: str
    coverage: float
    depth: float
    breadth: float
    confidence: float
    timestamp: float
    strategy_used: CompletionStrategy
    statement_count: int
    evidence_count: int

    @classmethod
    def create(
        cls,
        coverage: float,
        depth: float,
        breadth: float,
        confidence: float,
        strategy_used: CompletionStrategy,
        statement_count: int,
        evidence_count: int,
    ) -> CompletenessMetrics:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            coverage: Fraction of statements that are COMPLETE.
            depth: Normalised grounding depth score.
            breadth: Fraction of required evidence kinds covered.
            confidence: Average evidence confidence.
            strategy_used: The analysis strategy used.
            statement_count: Number of statements evaluated.
            evidence_count: Number of evidence items evaluated.

        Returns:
            A new CompletenessMetrics instance.
        """
        return cls(
            metrics_id=str(uuid.uuid4()),
            coverage=max(0.0, min(1.0, coverage)),
            depth=max(0.0, min(1.0, depth)),
            breadth=max(0.0, min(1.0, breadth)),
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=time.time(),
            strategy_used=strategy_used,
            statement_count=statement_count,
            evidence_count=evidence_count,
        )

    def overall_score(self) -> float:
        """Compute a weighted overall completeness score.

        Weights: coverage=0.40, confidence=0.30, depth=0.20, breadth=0.10.
        This weighting reflects that coverage is the primary metric from
        Ch37, while confidence provides the secondary quality signal.

        Returns:
            Weighted overall score in [0.0, 1.0].
        """
        return (
            0.40 * self.coverage
            + 0.30 * self.confidence
            + 0.20 * self.depth
            + 0.10 * self.breadth
        )

    def is_adequate(self, threshold: float = 0.75) -> bool:
        """Return True if the overall score meets the adequacy threshold.

        Args:
            threshold: Minimum overall score to be considered adequate.

        Returns:
            True if overall_score() >= threshold.
        """
        return self.overall_score() >= threshold

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of metrics fields.
        """
        data = {
            "metrics_id": self.metrics_id,
            "coverage": self.coverage,
            "depth": self.depth,
            "breadth": self.breadth,
            "confidence": self.confidence,
            "overall_score": self.overall_score(),
            "timestamp": self.timestamp,
            "strategy_used": self.strategy_used.value,
            "statement_count": self.statement_count,
            "evidence_count": self.evidence_count,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> CompletenessMetrics:
        """Deserialise from a JSON string.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A reconstructed CompletenessMetrics instance.
        """
        obj = json.loads(data)
        return cls(
            metrics_id=obj["metrics_id"],
            coverage=obj["coverage"],
            depth=obj["depth"],
            breadth=obj["breadth"],
            confidence=obj["confidence"],
            timestamp=obj["timestamp"],
            strategy_used=CompletionStrategy(obj["strategy_used"]),
            statement_count=obj["statement_count"],
            evidence_count=obj["evidence_count"],
        )

    def diff_with(self, other: CompletenessMetrics) -> dict[str, float]:
        """Compute the delta between this metrics and another.

        Args:
            other: A later CompletenessMetrics to compare against.

        Returns:
            Dictionary of metric name -> delta value.
        """
        return {
            "coverage_delta": other.coverage - self.coverage,
            "depth_delta": other.depth - self.depth,
            "breadth_delta": other.breadth - self.breadth,
            "confidence_delta": other.confidence - self.confidence,
            "overall_score_delta": other.overall_score() - self.overall_score(),
            "statement_count_delta": other.statement_count - self.statement_count,
            "evidence_count_delta": other.evidence_count - self.evidence_count,
        }

    def summarize(self) -> str:
        """Return a human-readable one-line summary.

        Returns:
            Concise summary string.
        """
        return (
            f"[METRICS {self.metrics_id[:8]}] "
            f"overall={self.overall_score():.3f} "
            f"coverage={self.coverage:.3f} confidence={self.confidence:.3f} "
            f"depth={self.depth:.3f} breadth={self.breadth:.3f} "
            f"stmts={self.statement_count} evs={self.evidence_count}"
        )


# ---------------------------------------------------------------------------
# CompletenessAnalyzer
# ---------------------------------------------------------------------------


class CompletenessAnalyzer:
    """Analyzes doctrine completeness using a configurable strategy.

    CompletenessAnalyzer orchestrates the computation of CompletenessMetrics
    for a collection of DoctrineStatements and their associated evidence.
    It supports per-type breakdown and bottleneck identification.

    Attributes:
        strategy: The CompletionStrategy to apply (default CRITICAL_PATH).
    """

    def __init__(
        self, strategy: CompletionStrategy = CompletionStrategy.CRITICAL_PATH
    ) -> None:
        """Initialise the analyzer with a strategy.

        Args:
            strategy: The CompletionStrategy to use for analysis.
        """
        self.strategy = strategy
        self._aggregator = EvidenceAggregator()
        self._estimator = ConfidenceEstimator()
        self._analyzer_id: str = str(uuid.uuid4())

    def analyze(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> CompletenessMetrics:
        """Compute completeness metrics for all statements.

        For each statement, collects evidence from the map and computes
        per-statement coverage, then aggregates into CompletenessMetrics.

        Args:
            statements: List of DoctrineStatements to evaluate.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            CompletenessMetrics for the full set.
        """
        if not statements:
            return CompletenessMetrics.create(
                coverage=0.0,
                depth=0.0,
                breadth=0.0,
                confidence=0.0,
                strategy_used=self.strategy,
                statement_count=0,
                evidence_count=0,
            )

        complete_count = 0
        all_evidences: list[ImplementationEvidence] = []
        total_required_kinds: set[str] = set()
        present_kinds: set[str] = set()
        total_depth = 0.0
        depth_count = 0

        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            all_evidences.extend(evs)
            available_kinds = [ev.evidence_kind for ev in evs]
            status = stmt.check_completeness(available_kinds)
            if status == StatementStatus.COMPLETE:
                complete_count += 1
            for k in stmt.required_evidence_kinds:
                total_required_kinds.add(k.value)
            for ev in evs:
                present_kinds.add(ev.evidence_kind.value)
                total_depth += ev.grounding_depth
                depth_count += 1

        coverage = complete_count / len(statements)
        breadth = (
            len(total_required_kinds & present_kinds) / len(total_required_kinds)
            if total_required_kinds
            else 1.0
        )
        depth_score = min(1.0, (total_depth / depth_count / 4.0) if depth_count > 0 else 0.0)
        avg_confidence = (
            sum(ev.confidence for ev in all_evidences) / len(all_evidences)
            if all_evidences
            else 0.0
        )

        return CompletenessMetrics.create(
            coverage=coverage,
            depth=depth_score,
            breadth=breadth,
            confidence=avg_confidence,
            strategy_used=self.strategy,
            statement_count=len(statements),
            evidence_count=len(all_evidences),
        )

    def analyze_by_type(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> dict[str, CompletenessMetrics]:
        """Compute separate metrics for each ClaimType.

        Groups statements by claim_type and runs analyze() on each group.

        Args:
            statements: All statements to evaluate.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Dictionary mapping claim_type.value to CompletenessMetrics.
        """
        by_type: dict[str, list[DoctrineStatement]] = {}
        for stmt in statements:
            key = stmt.claim_type.value
            by_type.setdefault(key, []).append(stmt)
        return {
            ctype: self.analyze(stmts, evidence_map)
            for ctype, stmts in by_type.items()
        }

    def identify_bottlenecks(
        self,
        metrics: CompletenessMetrics,
        statements: list[DoctrineStatement],
    ) -> list[str]:
        """Identify statement IDs that are bottlenecks for completeness.

        A bottleneck is any statement with UNGROUNDED status whose
        required_evidence_kinds list is non-empty.

        Args:
            metrics: Already-computed metrics (used for context).
            statements: List of statements to inspect.

        Returns:
            List of statement IDs that are bottlenecks.
        """
        bottlenecks: list[str] = []
        for stmt in statements:
            if stmt.status == StatementStatus.UNGROUNDED and stmt.required_evidence_kinds:
                bottlenecks.append(stmt.statement_id)
        return bottlenecks

    def estimate_completion_effort(
        self, gaps: list[DoctrineGap]
    ) -> dict[str, float]:
        """Estimate effort required to close each gap.

        Effort is proportional to gap severity and the number of missing
        evidence kinds.  BLOCKING gaps cost 4× MINOR gaps.

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            Dictionary mapping gap_id to estimated effort units.
        """
        effort_map: dict[str, float] = {}
        base_cost = {
            GapSeverity.MINOR: 1.0,
            GapSeverity.MODERATE: 2.0,
            GapSeverity.CRITICAL: 3.0,
            GapSeverity.BLOCKING: 4.0,
        }
        for gap in gaps:
            severity_cost = base_cost.get(gap.gap_severity, 2.0)
            kinds_cost = len(gap.missing_evidence_kinds) * 1.5
            effort_map[gap.gap_id] = severity_cost + kinds_cost
        return effort_map

    def generate_completion_summary(self, metrics: CompletenessMetrics) -> str:
        """Generate a prose summary from metrics.

        Args:
            metrics: The CompletenessMetrics to summarise.

        Returns:
            Multi-sentence summary string.
        """
        status = "ADEQUATE" if metrics.is_adequate() else "INADEQUATE"
        return (
            f"Doctrine completeness analysis ({metrics.strategy_used.value} strategy): "
            f"Overall score {metrics.overall_score():.1%} — status {status}. "
            f"Coverage: {metrics.coverage:.1%} of {metrics.statement_count} statements complete. "
            f"Evidence confidence: {metrics.confidence:.1%}. "
            f"Grounding depth score: {metrics.depth:.1%}. "
            f"Evidence kind breadth: {metrics.breadth:.1%}."
        )


# ---------------------------------------------------------------------------
# CriticalPathAnalyzer
# ---------------------------------------------------------------------------


class CriticalPathAnalyzer:
    """Identifies the critical path through dependent doctrine statements.

    The critical path is the longest chain of dependencies; completing
    statements on this path has the greatest impact on overall coverage.
    """

    def __init__(self) -> None:
        """Initialise the critical path analyzer.

        Maintains no persistent state; each call is independent.
        """
        self._analyzer_id: str = str(uuid.uuid4())

    def find_critical_path(
        self,
        statements: list[DoctrineStatement],
        dependencies: dict[str, list[str]],
    ) -> list[str]:
        """Find the critical (longest dependency) path through statements.

        Uses a topological longest-path algorithm.  Statements with no
        dependents are considered terminal nodes.

        Args:
            statements: All doctrine statements.
            dependencies: Mapping from statement_id to list of dependency IDs.

        Returns:
            Ordered list of statement_ids forming the critical path.
        """
        stmt_ids = {s.statement_id for s in statements}
        # Build adjacency: node -> children (nodes that depend on it)
        in_degree: dict[str, int] = {sid: 0 for sid in stmt_ids}
        children: dict[str, list[str]] = {sid: [] for sid in stmt_ids}
        for sid, deps in dependencies.items():
            if sid not in stmt_ids:
                continue
            for dep in deps:
                if dep in stmt_ids:
                    children[dep].append(sid)
                    in_degree[sid] += 1

        # Topological sort via Kahn's algorithm
        queue: deque[str] = deque(sid for sid in stmt_ids if in_degree[sid] == 0)
        topo_order: list[str] = []
        while queue:
            node = queue.popleft()
            topo_order.append(node)
            for child in children.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # Longest path using DP on topo order
        dist: dict[str, int] = {sid: 0 for sid in stmt_ids}
        pred: dict[str, Optional[str]] = {sid: None for sid in stmt_ids}
        for node in topo_order:
            for child in children.get(node, []):
                if dist[node] + 1 > dist[child]:
                    dist[child] = dist[node] + 1
                    pred[child] = node

        # Find the end of the longest path
        if not dist:
            return []
        end_node = max(dist, key=lambda k: dist[k])
        # Reconstruct path backwards
        path: list[str] = []
        current: Optional[str] = end_node
        while current is not None:
            path.append(current)
            current = pred[current]
        path.reverse()
        return path

    def compute_path_length(
        self,
        path: list[str],
        statements: list[DoctrineStatement],
    ) -> float:
        """Compute the length of a given path in terms of total required evidence.

        Path length is defined as the sum of required_evidence_kinds counts
        for all statements on the path.

        Args:
            path: Ordered list of statement IDs.
            statements: All statements (for lookup).

        Returns:
            Total required evidence count along the path.
        """
        stmt_map = {s.statement_id: s for s in statements}
        total = 0.0
        for sid in path:
            stmt = stmt_map.get(sid)
            if stmt:
                total += len(stmt.required_evidence_kinds)
        return total

    def identify_critical_statements(
        self,
        statements: list[DoctrineStatement],
        dependencies: dict[str, list[str]],
    ) -> list[str]:
        """Return the statement IDs that lie on the critical path.

        A thin wrapper that calls find_critical_path and returns the result.

        Args:
            statements: All doctrine statements.
            dependencies: Dependency mapping.

        Returns:
            Ordered list of critical statement IDs.
        """
        return self.find_critical_path(statements, dependencies)

    def critical_path_coverage(
        self,
        path: list[str],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> float:
        """Compute coverage fraction for statements on the critical path.

        A statement on the path is "covered" if it has at least one
        evidence item with confidence >= 0.7.

        Args:
            path: Ordered list of statement IDs on the critical path.
            evidence_map: Statement ID to evidence list mapping.

        Returns:
            Coverage fraction in [0.0, 1.0].
        """
        if not path:
            return 0.0
        covered = 0
        for sid in path:
            evs = evidence_map.get(sid, [])
            if any(ev.confidence >= 0.7 for ev in evs):
                covered += 1
        return covered / len(path)


# ---------------------------------------------------------------------------
# DoctrineGraph
# ---------------------------------------------------------------------------


class DoctrineGraph:
    """Directed acyclic graph of doctrine statement dependencies.

    DoctrineGraph stores statements as nodes and dependencies as directed
    edges (from_id -> to_id means from_id depends on to_id).  It supports
    topological ordering, reachability, and cycle detection.
    """

    def __init__(self) -> None:
        """Initialise an empty doctrine graph.

        The graph stores statements in an ID-indexed dict and edges in two
        adjacency lists (forward and reverse) for efficient traversal.
        """
        self._statements: dict[str, DoctrineStatement] = {}
        # _edges[a] = list of b means a depends on b
        self._edges: dict[str, list[str]] = {}
        # _reverse_edges[b] = list of a means a depends on b
        self._reverse_edges: dict[str, list[str]] = {}
        self._graph_id: str = str(uuid.uuid4())

    def add_statement(self, statement: DoctrineStatement) -> None:
        """Add a statement as a node in the graph.

        If a statement with the same ID already exists, it is overwritten.

        Args:
            statement: The DoctrineStatement to add.
        """
        sid = statement.statement_id
        self._statements[sid] = statement
        if sid not in self._edges:
            self._edges[sid] = []
        if sid not in self._reverse_edges:
            self._reverse_edges[sid] = []

    def add_dependency(self, from_id: str, to_id: str) -> None:
        """Add a directed dependency edge from from_id to to_id.

        from_id depends on to_id (to_id must be satisfied first).

        Args:
            from_id: ID of the dependent statement.
            to_id: ID of the prerequisite statement.
        """
        if from_id not in self._edges:
            self._edges[from_id] = []
        if to_id not in self._reverse_edges:
            self._reverse_edges[to_id] = []
        if to_id not in self._edges[from_id]:
            self._edges[from_id].append(to_id)
        if from_id not in self._reverse_edges.get(to_id, []):
            self._reverse_edges.setdefault(to_id, []).append(from_id)

    def get_dependencies(self, statement_id: str) -> list[str]:
        """Return the IDs of statements that statement_id depends on.

        Args:
            statement_id: The statement to query.

        Returns:
            List of prerequisite statement IDs.
        """
        return list(self._edges.get(statement_id, []))

    def get_dependents(self, statement_id: str) -> list[str]:
        """Return the IDs of statements that depend on statement_id.

        Args:
            statement_id: The statement to query.

        Returns:
            List of dependent statement IDs.
        """
        return list(self._reverse_edges.get(statement_id, []))

    def topological_order(self) -> list[str]:
        """Return all statement IDs in topological order.

        Uses Kahn's algorithm.  If the graph contains a cycle, raises
        a ValueError.

        Returns:
            List of statement IDs in topological order.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        all_ids = set(self._statements.keys())
        in_degree: dict[str, int] = {sid: 0 for sid in all_ids}
        for sid in all_ids:
            for dep in self._edges.get(sid, []):
                if dep in all_ids:
                    in_degree[sid] += 1  # sid depends on dep, so sid has in-degree bump

        # Actually, let's use standard Kahn's with deps as "edges":
        # An edge (u->v) means u depends on v, so v has no in-degree from u.
        # Recompute: in_degree[v] = number of nodes u that have v in their deps
        in_degree = {sid: 0 for sid in all_ids}
        for sid, deps in self._edges.items():
            # sid depends on deps: edges go from deps to sid in execution order
            in_degree[sid] += 0  # already zero; we increment for nodes pointing to sid
        for sid, deps in self._edges.items():
            # re-frame: prerequisite nodes have no incoming edges
            pass
        # Standard formulation: edge from dependency to dependent
        # in_degree[dependent] += 1 for each dependency
        in_degree = {sid: len(self._edges.get(sid, [])) for sid in all_ids}
        queue: deque[str] = deque(sid for sid in all_ids if in_degree[sid] == 0)
        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for dependent in self._reverse_edges.get(node, []):
                if dependent in all_ids:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)
        if len(result) != len(all_ids):
            raise ValueError("DoctrineGraph contains a cycle; topological order undefined")
        return result

    def reachable_from(self, statement_id: str) -> set[str]:
        """Return all statement IDs reachable (downstream) from statement_id.

        Performs a BFS following dependent edges (reverse direction).

        Args:
            statement_id: Starting node.

        Returns:
            Set of statement IDs reachable from the starting node.
        """
        visited: set[str] = set()
        queue: deque[str] = deque([statement_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for dep in self._reverse_edges.get(current, []):
                queue.append(dep)
        visited.discard(statement_id)
        return visited

    def is_acyclic(self) -> bool:
        """Return True if the graph contains no cycles.

        Attempts a topological sort; catches ValueError to detect cycles.

        Returns:
            True if the graph is a DAG.
        """
        try:
            self.topological_order()
            return True
        except ValueError:
            return False

    def to_adjacency_dict(self) -> dict[str, list[str]]:
        """Return the dependency adjacency as a plain dictionary.

        Returns:
            Dictionary mapping statement_id to list of dependency IDs.
        """
        return {sid: list(deps) for sid, deps in self._edges.items()}


# ---------------------------------------------------------------------------
# CompletionPlan
# ---------------------------------------------------------------------------


@dataclass
class CompletionPlan:
    """A prioritised plan for achieving doctrine completeness.

    CompletionPlan describes an ordered sequence of steps to address
    doctrine gaps, together with effort estimates and expected coverage.

    Attributes:
        plan_id: Unique identifier (uuid4).
        created_at: When this plan was created.
        strategy: The CompletionStrategy used to generate this plan.
        ordered_steps: List of step dictionaries (gap_id, action, effort).
        estimated_effort: Total estimated effort for all steps.
        priority_gaps: Ordered list of gap IDs by priority.
        expected_coverage: Projected coverage after plan completion.
    """

    plan_id: str
    created_at: float
    strategy: CompletionStrategy
    ordered_steps: list[dict[str, Any]]
    estimated_effort: float
    priority_gaps: list[str]
    expected_coverage: float

    @classmethod
    def create(
        cls,
        strategy: CompletionStrategy,
        ordered_steps: list[dict[str, Any]],
        estimated_effort: float,
        priority_gaps: list[str],
        expected_coverage: float,
    ) -> CompletionPlan:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            strategy: The strategy used to generate this plan.
            ordered_steps: Ordered list of step dictionaries.
            estimated_effort: Total estimated effort.
            priority_gaps: Priority-ordered gap IDs.
            expected_coverage: Expected coverage after execution.

        Returns:
            A new CompletionPlan instance.
        """
        return cls(
            plan_id=str(uuid.uuid4()),
            created_at=time.time(),
            strategy=strategy,
            ordered_steps=list(ordered_steps),
            estimated_effort=estimated_effort,
            priority_gaps=list(priority_gaps),
            expected_coverage=max(0.0, min(1.0, expected_coverage)),
        )

    def step_count(self) -> int:
        """Return the number of steps in this plan.

        Returns:
            Integer step count.
        """
        return len(self.ordered_steps)

    def next_step(self) -> Optional[dict[str, Any]]:
        """Return the next incomplete step, or None if all done.

        Looks for the first step without 'done: True' in its metadata.

        Returns:
            The next pending step dict, or None.
        """
        for step in self.ordered_steps:
            if not step.get("done", False):
                return step
        return None

    def mark_step_done(self, step_idx: int) -> None:
        """Mark a step as completed by index.

        Args:
            step_idx: Zero-based index of the step to mark done.

        Raises:
            IndexError: If step_idx is out of range.
        """
        if step_idx < 0 or step_idx >= len(self.ordered_steps):
            raise IndexError(f"step_idx {step_idx} out of range [0, {len(self.ordered_steps)})")
        self.ordered_steps[step_idx]["done"] = True
        self.ordered_steps[step_idx]["completed_at"] = time.time()

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of plan fields.
        """
        data = {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "strategy": self.strategy.value,
            "ordered_steps": self.ordered_steps,
            "estimated_effort": self.estimated_effort,
            "priority_gaps": self.priority_gaps,
            "expected_coverage": self.expected_coverage,
        }
        return json.dumps(data, indent=2)

    def summarize(self) -> str:
        """Return a human-readable summary of this plan.

        Returns:
            Concise summary string.
        """
        done_count = sum(1 for s in self.ordered_steps if s.get("done", False))
        return (
            f"[PLAN {self.plan_id[:8]}] strategy={self.strategy.value} "
            f"steps={self.step_count()} done={done_count} "
            f"effort={self.estimated_effort:.1f} "
            f"expected_coverage={self.expected_coverage:.1%}"
        )


# ---------------------------------------------------------------------------
# GapBridger
# ---------------------------------------------------------------------------


class GapBridger:
    """Suggests concrete actions to bridge evidence gaps.

    GapBridger generates actionable suggestions for resolving doctrine gaps
    and assembles these into CompletionPlans.  Each suggestion is a dict
    describing what evidence to collect and how.
    """

    # Action templates per evidence kind
    _ACTIONS: dict[str, str] = {
        "code": "Write or identify source code artefact for '{stmt_id}'",
        "test": "Write and run automated tests covering '{stmt_id}'",
        "runtime": "Collect runtime traces or execution logs for '{stmt_id}'",
        "proof": "Develop or find a formal proof for '{stmt_id}'",
        "oracle": "Set up property-based or oracle-based tests for '{stmt_id}'",
        "benchmark": "Run performance benchmarks to evidence '{stmt_id}'",
        "human_review": "Schedule a human review session for '{stmt_id}'",
        "copilot_review": "Conduct a copilot-assisted review for '{stmt_id}'",
    }

    def __init__(self) -> None:
        """Initialise the GapBridger with a unique ID.

        The bridger is stateless beyond its identity.
        """
        self._bridger_id: str = str(uuid.uuid4())

    def bridge(self, gap: DoctrineGap) -> list[dict[str, Any]]:
        """Generate bridging actions for a single gap.

        For each missing evidence kind, produces an action dictionary with
        the action description, kind, and effort estimate.

        Args:
            gap: The DoctrineGap to generate actions for.

        Returns:
            List of action dictionaries.
        """
        actions: list[dict[str, Any]] = []
        for kind in gap.missing_evidence_kinds:
            template = self._ACTIONS.get(
                kind.value, "Collect '{kind}' evidence for '{stmt_id}'"
            )
            description = template.format(
                stmt_id=gap.statement_id[:8], kind=kind.value
            )
            actions.append({
                "gap_id": gap.gap_id,
                "statement_id": gap.statement_id,
                "kind": kind.value,
                "action": description,
                "severity": gap.gap_severity.value,
                "effort": self.estimate_bridge_effort(gap),
                "done": False,
            })
        return actions

    def bridge_all(
        self, gaps: list[DoctrineGap]
    ) -> dict[str, list[dict[str, Any]]]:
        """Generate bridging actions for a list of gaps.

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            Dictionary mapping gap_id to list of action dicts.
        """
        return {gap.gap_id: self.bridge(gap) for gap in gaps}

    def estimate_bridge_effort(self, gap: DoctrineGap) -> float:
        """Estimate effort to bridge a gap in normalised effort units.

        Effort = severity_score * 3.0 + len(missing_kinds) * 1.0.

        Args:
            gap: The DoctrineGap to estimate effort for.

        Returns:
            Float effort estimate.
        """
        return gap.compute_severity_score() * 3.0 + len(gap.missing_evidence_kinds) * 1.0

    def prioritized_bridge_plan(
        self, gaps: list[DoctrineGap]
    ) -> CompletionPlan:
        """Build a CompletionPlan prioritised by gap severity.

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            A CompletionPlan with steps ordered by severity.
        """
        sorted_gaps = sorted(
            gaps,
            key=lambda g: (-g.compute_severity_score(), g.created_at),
        )
        all_steps: list[dict[str, Any]] = []
        total_effort = 0.0
        for gap in sorted_gaps:
            actions = self.bridge(gap)
            all_steps.extend(actions)
            total_effort += self.estimate_bridge_effort(gap)

        priority_gap_ids = [g.gap_id for g in sorted_gaps]
        # Heuristic expected coverage: assume each closed gap improves coverage
        # by 1 / (total gaps + 1), capped at 0.99
        expected_coverage = min(0.99, 1.0 - 1.0 / (len(gaps) + 1)) if gaps else 1.0

        return CompletionPlan.create(
            strategy=CompletionStrategy.RISK_BASED,
            ordered_steps=all_steps,
            estimated_effort=total_effort,
            priority_gaps=priority_gap_ids,
            expected_coverage=expected_coverage,
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def compute_completion_metrics(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> CompletenessMetrics:
    """Compute completeness metrics using the default CRITICAL_PATH strategy.

    Convenience wrapper around CompletenessAnalyzer.analyze().

    Args:
        statements: List of DoctrineStatements to evaluate.
        evidence_map: Mapping from statement_id to evidence list.

    Returns:
        CompletenessMetrics for the full set.
    """
    analyzer = CompletenessAnalyzer(strategy=CompletionStrategy.CRITICAL_PATH)
    return analyzer.analyze(statements, evidence_map)


def plan_completion(
    gaps: list[DoctrineGap],
    resources: dict[str, float],
) -> CompletionPlan:
    """Generate a prioritised completion plan given gaps and available resources.

    Uses GapBridger to generate actions and then filters/limits steps based
    on the available resources dictionary (resource_name -> capacity).

    Args:
        gaps: List of DoctrineGap instances to bridge.
        resources: Dictionary mapping resource names to available capacity.

    Returns:
        A CompletionPlan for the given gaps.
    """
    bridger = GapBridger()
    plan = bridger.prioritized_bridge_plan(gaps)

    # Apply resource constraints: if 'max_effort' is provided, trim steps
    max_effort = resources.get("max_effort", float("inf"))
    cumulative_effort = 0.0
    trimmed_steps: list[dict[str, Any]] = []
    for step in plan.ordered_steps:
        step_effort = step.get("effort", 1.0)
        if cumulative_effort + step_effort <= max_effort:
            trimmed_steps.append(step)
            cumulative_effort += step_effort
        else:
            break

    return CompletionPlan.create(
        strategy=CompletionStrategy.RISK_BASED,
        ordered_steps=trimmed_steps,
        estimated_effort=cumulative_effort,
        priority_gaps=plan.priority_gaps,
        expected_coverage=plan.expected_coverage,
    )
