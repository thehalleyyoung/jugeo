"""
Ecological Metrics for Theorem Reuse: Breadth, Depth, and Coverage Analysis
===========================================================================

# copilot: theorem-ecologies/s03-ecological-metrics — reuse breadth, citation
# depth, theoretical coverage, ecology scores, and the 8-tuple judgment schema.

This module provides comprehensive metrics for analyzing theorem reuse ecosystems,
focusing on breadth of application (how widely theorems are used), depth of citation
relationships (how deeply connected citation networks are), and theoretical coverage
(how much of a domain space is covered by available theorems).

Judgment schema (8-tuple):
    (c, φ, A, E, O, B, T, Π)

where
    c  = context
    φ  = formula / property being judged
    A  = authority
    E  = evidence tuple
    O  = obligations tuple
    B  = budget
    T  = TrustTier (PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED)
    Π  = proof_chain

Key Classes:
    - ReuseBreadth: Measures how widely a theorem is reused across different contexts
    - CitationDepth: Analyzes the depth and structure of theorem citation relationships
    - TheoreticalCoverage: Evaluates domain coverage and identifies gaps
    - EcologyScore: Composite ecological score
    - EcologicalMetric: A single ecological metric measurement with metadata

Author: Jugeo Project
Version: 0.4.0
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Dict, List, Set, Tuple, Any
import statistics
import math


# ============================================================================
# Enums and Type Definitions
# ============================================================================

class TrustTier(Enum):
    """Ordered trust tiers — PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED."""
    PROPOSAL = auto()
    REVIEWED = auto()
    VERIFIED = auto()
    RUNTIME_WITNESSED = auto()
    PROOF_BACKED = auto()

    def dominates(self, other: "TrustTier") -> bool:
        return self.value >= other.value

    def label(self) -> str:
        return self.name.replace("_", " ").title()


# ---------------------------------------------------------------------------
# jugeo optional imports
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.theorem_ecologies.models import EcologyModel  # type: ignore
except ImportError:
    EcologyModel = None  # type: ignore

try:
    from jugeo.ideation.theorem_ecologies.theorems import EcologyTheorem  # type: ignore
except ImportError:
    EcologyTheorem = None  # type: ignore

import uuid as _uuid_mod
import json as _json_mod


def _now_iso_eco() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat() + "Z"


def _uid_eco() -> str:
    return _uuid_mod.uuid4().hex[:16]


def _clamp_eco(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


class MetricJudgment:
    """
    A judgment about a metric value that combines the raw measurement
    with confidence assessment and interpretability information.
    """
    __slots__ = ('value', 'confidence', 'interpretation', 'timestamp')

    def __init__(
        self,
        value: float,
        confidence: float = 1.0,
        interpretation: str = "",
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize a MetricJudgment.
        
        Args:
            value: The measured metric value
            confidence: Confidence in the measurement (0.0 to 1.0)
            interpretation: Human-readable interpretation
            timestamp: When the judgment was made
        """
        self.value = float(value)
        self.confidence = max(0.0, min(1.0, float(confidence)))
        self.interpretation = str(interpretation)
        self.timestamp = timestamp or datetime.now()

    def __repr__(self) -> str:
        return (
            f"MetricJudgment(value={self.value:.4f}, "
            f"confidence={self.confidence:.4f}, "
            f"interpretation='{self.interpretation}')"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, MetricJudgment):
            return NotImplemented
        return (
            abs(self.value - other.value) < 1e-9 and
            abs(self.confidence - other.confidence) < 1e-9
        )


# ============================================================================
# Frozen Dataclasses for Immutable Metrics
# ============================================================================

@dataclass(frozen=True)
class UsageRecord:
    """
    Represents a single usage instance of a theorem in a specific context.
    
    Attributes:
        context_id: Unique identifier for the usage context
        context_type: Type of context (e.g., 'proof', 'definition', 'lemma')
        timestamp: When the usage occurred
        citation_chain_length: How many times this usage was cited downstream
        is_foundational: Whether this theorem was essential to the context
    """
    context_id: str
    context_type: str
    timestamp: datetime = field(default_factory=datetime.now)
    citation_chain_length: int = 0
    is_foundational: bool = False

    def __post_init__(self):
        if self.citation_chain_length < 0:
            raise ValueError("citation_chain_length must be non-negative")


@dataclass(frozen=True)
class EcologicalMetric:
    """
    A single ecological metric measurement with metadata.
    
    Attributes:
        metric_name: Name of the metric (e.g., 'breadth', 'depth', 'coverage')
        value: Numeric value of the metric
        unit: Unit of measurement
        trust_tier: Trust tier for this metric
        source: Data source for this metric
        computed_at: When this metric was computed
    """
    metric_name: str
    value: float
    unit: str
    trust_tier: TrustTier
    source: str
    computed_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class BreadthSnapshot:
    """
    A snapshot of breadth metrics at a specific point in time.
    
    Attributes:
        unique_contexts: Number of unique application contexts
        unique_domains: Number of unique domains using the theorem
        average_chain_depth: Average citation chain depth from this theorem
        reuse_diversity_index: Index measuring diversity of reuse (0 to 1)
        temporal_spread_days: Days between earliest and latest usage
    """
    unique_contexts: int
    unique_domains: int
    average_chain_depth: float
    reuse_diversity_index: float
    temporal_spread_days: int

    def __post_init__(self):
        if self.unique_contexts < 0:
            raise ValueError("unique_contexts must be non-negative")
        if not (0.0 <= self.reuse_diversity_index <= 1.0):
            raise ValueError("reuse_diversity_index must be between 0 and 1")


@dataclass(frozen=True)
class CitationEdge:
    """
    Represents a directed citation relationship between theorems.
    
    Attributes:
        source_theorem_id: ID of the citing theorem
        target_theorem_id: ID of the cited theorem
        citation_type: Type of citation ('direct', 'indirect', 'foundational')
        confidence: Confidence in this citation relationship (0 to 1)
        distance: Graph distance in the citation network
    """
    source_theorem_id: str
    target_theorem_id: str
    citation_type: str = "direct"
    confidence: float = 1.0
    distance: int = 1

    def __post_init__(self):
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        if self.distance < 1:
            raise ValueError("distance must be at least 1")


@dataclass(frozen=True)
class DepthResult:
    """
    Result of a citation depth analysis.
    
    Attributes:
        max_depth: Maximum citation chain length
        average_depth: Average citation chain length
        network_diameter: Diameter of citation network
        strongly_connected_components: Number of SCC groups
        centrality_scores: Dict of theorem IDs to centrality scores
    """
    max_depth: int
    average_depth: float
    network_diameter: int
    strongly_connected_components: int
    centrality_scores: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if self.network_diameter < 0:
            raise ValueError("network_diameter must be non-negative")


@dataclass(frozen=True)
class DomainSpec:
    """
    Specification of a knowledge domain for coverage analysis.
    
    Attributes:
        domain_name: Name of the domain
        core_concepts: Set of core concepts that should be covered
        expected_theorem_count: Expected number of theorems to cover domain
        version: Version of this domain specification
    """
    domain_name: str
    core_concepts: Set[str] = field(default_factory=set)
    expected_theorem_count: int = 0
    version: str = "1.0"


@dataclass(frozen=True)
class CoverageResult:
    """
    Result of theoretical coverage analysis.
    
    Attributes:
        domain_name: Name of the analyzed domain
        covered_concepts: Set of concepts with available theorems
        coverage_percentage: Percentage of domain covered
        gap_count: Number of uncovered concepts
        theorem_distribution: Dict mapping concepts to theorem counts
    """
    domain_name: str
    covered_concepts: Set[str] = field(default_factory=set)
    coverage_percentage: float = 0.0
    gap_count: int = 0
    theorem_distribution: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if not (0.0 <= self.coverage_percentage <= 100.0):
            raise ValueError("coverage_percentage must be between 0 and 100")


@dataclass(frozen=True)
class EcologyScore:
    """
    Comprehensive ecological score combining multiple metrics.
    
    Attributes:
        breadth_score: Normalized breadth score (0 to 1)
        depth_score: Normalized depth score (0 to 1)
        coverage_score: Normalized coverage score (0 to 1)
        overall_health: Overall ecosystem health metric
        trend: Trend indicator ('improving', 'stable', 'declining')
    """
    breadth_score: float
    depth_score: float
    coverage_score: float
    overall_health: float
    trend: str = "stable"

    def __post_init__(self):
        for score_name, score_val in [
            ('breadth_score', self.breadth_score),
            ('depth_score', self.depth_score),
            ('coverage_score', self.coverage_score),
            ('overall_health', self.overall_health)
        ]:
            if not (0.0 <= score_val <= 1.0):
                raise ValueError(f"{score_name} must be between 0 and 1")


@dataclass(frozen=True)
class GapReport:
    """
    Report of gaps in theorem coverage.
    
    Attributes:
        gap_id: Unique identifier for this gap
        concept_name: Name of the uncovered concept
        domain: Domain in which the gap exists
        severity: Severity level (1-5, where 5 is most severe)
        suggested_theorem_count: How many theorems would ideally cover this gap
        affected_downstream_count: Count of theorems affected by this gap
    """
    gap_id: str
    concept_name: str
    domain: str
    severity: int
    suggested_theorem_count: int = 1
    affected_downstream_count: int = 0

    def __post_init__(self):
        if not (1 <= self.severity <= 5):
            raise ValueError("severity must be between 1 and 5")
        if self.suggested_theorem_count < 1:
            raise ValueError("suggested_theorem_count must be at least 1")


# ============================================================================
# Main Analysis Classes
# ============================================================================

class ReuseBreadth:
    """
    Analyzes the breadth of theorem reuse across contexts, domains, and applications.
    
    Breadth measures how widely a theorem is applied and how diverse those applications are.
    High breadth indicates a theorem with fundamental, broadly applicable insights.
    """

    def __init__(self, theorem_id: str):
        """Initialize ReuseBreadth analyzer for a specific theorem."""
        self.theorem_id = theorem_id
        self.usage_records: List[UsageRecord] = []
        self.domain_usage: Dict[str, int] = {}

    def add_usage(self, record: UsageRecord) -> None:
        """Record a usage instance of this theorem."""
        self.usage_records.append(record)
        # Update domain tracking
        if record.context_type not in self.domain_usage:
            self.domain_usage[record.context_type] = 0
        self.domain_usage[record.context_type] += 1

    def compute_unique_contexts(self) -> int:
        """Count the number of unique usage contexts."""
        return len(set(r.context_id for r in self.usage_records))

    def compute_unique_domains(self) -> int:
        """Count the number of unique domain types."""
        return len(self.domain_usage)

    def compute_average_chain_depth(self) -> float:
        """Compute average citation chain depth from usages."""
        if not self.usage_records:
            return 0.0
        depths = [r.citation_chain_length for r in self.usage_records]
        return statistics.mean(depths) if depths else 0.0

    def compute_reuse_diversity_index(self) -> float:
        """
        Compute Shannon diversity index for reuse distribution across domains.
        Returns value between 0 (uniform) and 1 (maximum diversity).
        """
        if not self.domain_usage or sum(self.domain_usage.values()) == 0:
            return 0.0

        total = sum(self.domain_usage.values())
        proportions = [count / total for count in self.domain_usage.values()]

        # Shannon entropy
        entropy = -sum(p * math.log2(p) for p in proportions if p > 0)

        # Normalize by maximum possible entropy
        max_entropy = math.log2(len(self.domain_usage))
        return entropy / max_entropy if max_entropy > 0 else 0.0

    def compute_temporal_spread(self) -> int:
        """Compute days between earliest and latest usage."""
        if len(self.usage_records) < 2:
            return 0

        timestamps = [r.timestamp for r in self.usage_records]
        earliest = min(timestamps)
        latest = max(timestamps)
        return (latest - earliest).days

    def get_breadth_snapshot(self) -> BreadthSnapshot:
        """Generate a breadth snapshot of current metrics."""
        return BreadthSnapshot(
            unique_contexts=self.compute_unique_contexts(),
            unique_domains=self.compute_unique_domains(),
            average_chain_depth=self.compute_average_chain_depth(),
            reuse_diversity_index=self.compute_reuse_diversity_index(),
            temporal_spread_days=self.compute_temporal_spread()
        )


class CitationDepth:
    """
    Analyzes the depth and structure of citation relationships in theorem ecosystems.
    
    Depth measures how deeply theorems are interconnected through citation relationships
    and how central key theorems are to the overall network structure.
    """

    def __init__(self):
        """Initialize CitationDepth analyzer."""
        self.edges: List[CitationEdge] = []
        self.adjacency: Dict[str, List[str]] = {}

    def add_citation(self, edge: CitationEdge) -> None:
        """Add a citation relationship."""
        self.edges.append(edge)

        # Update adjacency list
        if edge.source_theorem_id not in self.adjacency:
            self.adjacency[edge.source_theorem_id] = []
        self.adjacency[edge.source_theorem_id].append(edge.target_theorem_id)

    def compute_max_depth(self) -> int:
        """Compute the maximum citation chain length in the network."""
        if not self.adjacency:
            return 0

        max_depth = 0
        visited: Set[str] = set()

        def dfs(node: str, depth: int) -> int:
            nonlocal max_depth
            if node in visited and depth > 0:
                return depth
            visited.add(node)
            max_depth = max(max_depth, depth)

            for neighbor in self.adjacency.get(node, []):
                dfs(neighbor, depth + 1)

            visited.discard(node)
            return depth

        for theorem_id in self.adjacency:
            dfs(theorem_id, 0)

        return max_depth

    def compute_average_depth(self) -> float:
        """Compute average depth across all citation chains."""
        if not self.edges:
            return 0.0
        depths = [e.distance for e in self.edges]
        return statistics.mean(depths)

    def compute_network_diameter(self) -> int:
        """Estimate network diameter (longest shortest path)."""
        if not self.adjacency:
            return 0

        all_nodes = set(self.adjacency.keys())
        for targets in self.adjacency.values():
            all_nodes.update(targets)

        if len(all_nodes) <= 1:
            return 0

        # Simplified diameter estimation
        return self.compute_max_depth()

    def compute_centrality(self) -> Dict[str, float]:
        """
        Compute simple betweenness-like centrality scores.
        Higher scores indicate more central theorems in citation network.
        """
        centrality: Dict[str, float] = {}
        all_nodes = set(self.adjacency.keys())
        for targets in self.adjacency.values():
            all_nodes.update(targets)

        for node in all_nodes:
            in_degree = sum(1 for e in self.edges if e.target_theorem_id == node)
            out_degree = len(self.adjacency.get(node, []))
            centrality[node] = (in_degree + out_degree) / max(1, len(all_nodes))

        return centrality

    def get_depth_result(self) -> DepthResult:
        """Generate a depth analysis result."""
        return DepthResult(
            max_depth=self.compute_max_depth(),
            average_depth=self.compute_average_depth(),
            network_diameter=self.compute_network_diameter(),
            strongly_connected_components=len(set(e.source_theorem_id for e in self.edges)),
            centrality_scores=self.compute_centrality()
        )


class TheoreticalCoverage:
    """
    Analyzes the coverage of domains by available theorems and identifies gaps.
    
    Coverage measures how well the available theorems address the core concepts
    and requirements of a knowledge domain, helping identify areas needing new theorems.
    """

    def __init__(self):
        """Initialize TheoreticalCoverage analyzer."""
        self.domains: Dict[str, DomainSpec] = {}
        self.coverage_data: Dict[str, Set[str]] = {}  # domain -> covered concepts
        self.gaps: List[GapReport] = []

    def register_domain(self, spec: DomainSpec) -> None:
        """Register a domain specification."""
        self.domains[spec.domain_name] = spec
        if spec.domain_name not in self.coverage_data:
            self.coverage_data[spec.domain_name] = set()

    def mark_concept_covered(self, domain_name: str, concept: str) -> None:
        """Mark a concept as covered by available theorems."""
        if domain_name not in self.coverage_data:
            self.coverage_data[domain_name] = set()
        self.coverage_data[domain_name].add(concept)

    def compute_coverage_percentage(self, domain_name: str) -> float:
        """Compute coverage percentage for a domain."""
        if domain_name not in self.domains:
            return 0.0

        spec = self.domains[domain_name]
        if not spec.core_concepts:
            return 100.0

        covered = self.coverage_data.get(domain_name, set())
        overlap = len(covered & spec.core_concepts)
        return (overlap / len(spec.core_concepts)) * 100.0

    def identify_gaps(self, domain_name: str) -> List[str]:
        """Identify uncovered concepts in a domain."""
        if domain_name not in self.domains:
            return []

        spec = self.domains[domain_name]
        covered = self.coverage_data.get(domain_name, set())
        return list(spec.core_concepts - covered)

    def report_gap(self, gap: GapReport) -> None:
        """Register a discovered gap in coverage."""
        self.gaps.append(gap)

    def get_coverage_result(self, domain_name: str) -> CoverageResult:
        """Generate a coverage result for a domain."""
        gaps = self.identify_gaps(domain_name)
        covered = self.coverage_data.get(domain_name, set())

        # Build theorem distribution
        theorem_dist = {concept: 1 for concept in covered}

        return CoverageResult(
            domain_name=domain_name,
            covered_concepts=covered,
            coverage_percentage=self.compute_coverage_percentage(domain_name),
            gap_count=len(gaps),
            theorem_distribution=theorem_dist
        )


# ============================================================================
# Convenience Functions
# ============================================================================

def compute_ecological_score(
    breadth: BreadthSnapshot,
    depth: DepthResult,
    coverage: CoverageResult
) -> EcologyScore:
    """
    Compute comprehensive ecological score from component metrics.

    Combines breadth, depth, and coverage into normalized scores
    that represent overall ecosystem health.
    """
    # Normalize breadth score
    breadth_score = min(1.0, breadth.reuse_diversity_index * 0.5 +
                        (breadth.unique_domains / max(10, breadth.unique_domains)) * 0.5)

    # Normalize depth score
    depth_score = min(1.0, breadth.average_chain_depth / 10.0)

    # Normalize coverage score
    coverage_score = coverage.coverage_percentage / 100.0

    # Overall health
    overall_health = (breadth_score + depth_score + coverage_score) / 3.0

    # Determine trend (simplified)
    trend = "stable"
    if overall_health > 0.7:
        trend = "improving"
    elif overall_health < 0.4:
        trend = "declining"

    return EcologyScore(
        breadth_score=breadth_score,
        depth_score=depth_score,
        coverage_score=coverage_score,
        overall_health=overall_health,
        trend=trend
    )


def compute_diversity_index(items: List[str]) -> float:
    """
    Compute Shannon diversity index for a list of items.
    Returns 0 for no diversity, 1 for maximum diversity.
    """
    if not items:
        return 0.0

    from collections import Counter
    counts = Counter(items)
    total = len(items)
    proportions = [count / total for count in counts.values()]

    entropy = -sum(p * math.log2(p) for p in proportions if p > 0)
    max_entropy = math.log2(len(counts))

    return entropy / max_entropy if max_entropy > 0 else 0.0


def compute_network_statistics(
    edges: List[CitationEdge]
) -> Dict[str, float]:
    """
    Compute basic statistics on a citation network.

    Returns dict with keys: node_count, edge_count, avg_degree, density
    """
    if not edges:
        return {
            'node_count': 0,
            'edge_count': 0,
            'avg_degree': 0.0,
            'density': 0.0
        }

    nodes = set()
    for edge in edges:
        nodes.add(edge.source_theorem_id)
        nodes.add(edge.target_theorem_id)

    node_count = len(nodes)
    edge_count = len(edges)
    avg_degree = (2.0 * edge_count) / max(1, node_count)

    # Density = edges / possible_edges
    max_edges = node_count * (node_count - 1)
    density = edge_count / max_edges if max_edges > 0 else 0.0

    return {
        'node_count': node_count,
        'edge_count': edge_count,
        'avg_degree': avg_degree,
        'density': density
    }


# ============================================================================
# Module-Level Test Suite
# ============================================================================

def run_smoke_test():
    """Run comprehensive smoke test of ecological metrics module."""
    print("\n" + "=" * 70)
    print("ECOLOGICAL METRICS SMOKE TEST")
    print("=" * 70 + "\n")

    # Test 1: MetricJudgment
    print("Test 1: MetricJudgment")
    judgment = MetricJudgment(0.85, confidence=0.95, interpretation="Good reuse breadth")
    print(f"  Created: {judgment}")
    print(f"  Value: {judgment.value}, Confidence: {judgment.confidence}")
    assert 0.8 < judgment.value < 0.9
    print("  ✓ PASSED\n")

    # Test 2: Frozen dataclasses
    print("Test 2: Frozen Dataclasses")
    usage = UsageRecord("ctx1", "proof", citation_chain_length=3)
    metric = EcologicalMetric("breadth", 0.75, "ratio", TrustTier.REVIEWED, "experiment1")
    breadth = BreadthSnapshot(5, 2, 2.5, 0.8, 100)
    print(f"  UsageRecord: {usage.context_id}")
    print(f"  EcologicalMetric: {metric.metric_name}")
    print(f"  BreadthSnapshot: {breadth.unique_contexts} contexts")
    assert usage.citation_chain_length == 3
    assert breadth.unique_domains == 2
    print("  ✓ PASSED\n")

    # Test 3: ReuseBreadth
    print("Test 3: ReuseBreadth Analysis")
    rb = ReuseBreadth("theorem_42")
    for i in range(5):
        rb.add_usage(UsageRecord(f"ctx{i}", "proof" if i % 2 == 0 else "lemma"))
    snapshot = rb.get_breadth_snapshot()
    print(f"  Unique contexts: {snapshot.unique_contexts}")
    print(f"  Unique domains: {snapshot.unique_domains}")
    print(f"  Diversity index: {snapshot.reuse_diversity_index:.3f}")
    assert snapshot.unique_contexts == 5
    assert snapshot.unique_domains == 2
    print("  ✓ PASSED\n")

    # Test 4: CitationDepth
    print("Test 4: CitationDepth Analysis")
    cd = CitationDepth()
    edges = [
        CitationEdge("t1", "t2", distance=1),
        CitationEdge("t2", "t3", distance=2),
        CitationEdge("t1", "t3", distance=1),
    ]
    for edge in edges:
        cd.add_citation(edge)
    depth_result = cd.get_depth_result()
    print(f"  Max depth: {depth_result.max_depth}")
    print(f"  Average depth: {depth_result.average_depth:.2f}")
    print(f"  Network diameter: {depth_result.network_diameter}")
    assert depth_result.max_depth >= 0
    print("  ✓ PASSED\n")

    # Test 5: TheoreticalCoverage
    print("Test 5: TheoreticalCoverage Analysis")
    tc = TheoreticalCoverage()
    domain_spec = DomainSpec(
        "algebra",
        core_concepts={"groups", "rings", "fields", "modules"},
        expected_theorem_count=50
    )
    tc.register_domain(domain_spec)
    tc.mark_concept_covered("algebra", "groups")
    tc.mark_concept_covered("algebra", "rings")
    coverage = tc.get_coverage_result("algebra")
    print(f"  Domain: {coverage.domain_name}")
    print(f"  Coverage: {coverage.coverage_percentage:.1f}%")
    print(f"  Gaps: {coverage.gap_count}")
    assert coverage.coverage_percentage == 50.0
    assert coverage.gap_count == 2
    print("  ✓ PASSED\n")

    # Test 6: Comprehensive ecological score
    print("Test 6: Ecological Score Computation")
    eco_score = compute_ecological_score(snapshot, depth_result, coverage)
    print(f"  Breadth score: {eco_score.breadth_score:.3f}")
    print(f"  Depth score: {eco_score.depth_score:.3f}")
    print(f"  Coverage score: {eco_score.coverage_score:.3f}")
    print(f"  Overall health: {eco_score.overall_health:.3f}")
    print(f"  Trend: {eco_score.trend}")
    assert 0.0 <= eco_score.overall_health <= 1.0
    print("  ✓ PASSED\n")

    # Test 7: Network statistics
    print("Test 7: Network Statistics")
    net_stats = compute_network_statistics(edges)
    print(f"  Nodes: {net_stats['node_count']}")
    print(f"  Edges: {net_stats['edge_count']}")
    print(f"  Avg degree: {net_stats['avg_degree']:.2f}")
    print(f"  Density: {net_stats['density']:.3f}")
    assert net_stats['node_count'] == 3
    assert net_stats['edge_count'] == 3
    print("  ✓ PASSED\n")

    # Test 8: Diversity computation
    print("Test 8: Diversity Index")
    items = ["type_a", "type_a", "type_b", "type_c", "type_c", "type_c"]
    diversity = compute_diversity_index(items)
    print(f"  Items: {items}")
    print(f"  Diversity: {diversity:.3f}")
    assert 0.0 <= diversity <= 1.0
    print("  ✓ PASSED\n")

    # Test 9: Gap reporting
    print("Test 9: Gap Reporting")
    gap = GapReport(
        gap_id="gap_001",
        concept_name="homomorphism_theorems",
        domain="algebra",
        severity=4,
        suggested_theorem_count=3,
        affected_downstream_count=7
    )
    tc.report_gap(gap)
    print(f"  Gap ID: {gap.gap_id}")
    print(f"  Concept: {gap.concept_name}")
    print(f"  Severity: {gap.severity}/5")
    print(f"  Affected downstream: {gap.affected_downstream_count}")
    assert gap.severity == 4
    assert len(tc.gaps) == 1
    print("  ✓ PASSED\n")

    # Test 10: Trust tier enum (copilot: PROPOSAL → PROOF_BACKED ordering)
    print("Test 10: Trust Tier Enum")
    tiers = [
        TrustTier.PROPOSAL,
        TrustTier.REVIEWED,
        TrustTier.VERIFIED,
        TrustTier.RUNTIME_WITNESSED,
        TrustTier.PROOF_BACKED,
    ]
    print(f"  Trust tiers defined: {len(tiers)}")
    print(f"  Example: {TrustTier.PROPOSAL}")
    assert len(tiers) == 5
    assert TrustTier.PROOF_BACKED.dominates(TrustTier.PROPOSAL)
    print("  ✓ PASSED\n")

    print("=" * 70)
    print("ALL SMOKE TESTS PASSED!")
    print("=" * 70 + "\n")


# ============================================================================
# 8-tuple MetricJudgment (copilot: canonical judgment form)
# ============================================================================

@dataclass(frozen=True, slots=True)
class MetricJudgment8:
    """8-tuple judgment (c, φ, A, E, O, B, T, Π) for ecological metric decisions.

    Attributes
    ----------
    context : str           c  — context (ecology ID, session ID, etc.)
    formula : str           φ  — the metric property being judged
    authority : str         A  — issuing authority
    evidence : tuple        E  — supporting evidence items
    obligations : tuple     O  — remaining obligations
    budget : float          B  — computational budget remaining
    trust_tier : TrustTier  T  — trust level
    proof_chain : tuple     Π  — proof-step chain
    """

    context: str
    formula: str
    authority: str
    evidence: tuple
    obligations: tuple
    budget: float
    trust_tier: TrustTier
    proof_chain: tuple

    def is_fully_discharged(self) -> bool:
        return len(self.obligations) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context": self.context,
            "formula": self.formula,
            "authority": self.authority,
            "evidence": list(self.evidence),
            "obligations": list(self.obligations),
            "budget": self.budget,
            "trust_tier": self.trust_tier.name,
            "proof_chain": list(self.proof_chain),
            "fully_discharged": self.is_fully_discharged(),
        }


@dataclass(frozen=True, slots=True)
class EcologyScore:
    """Composite ecological score for a theorem ecology.

    Attributes
    ----------
    score_id : str
        Unique identifier for this score record.
    ecology_id : str
        ID of the ecology being scored.
    reuse_score : float
        Reuse breadth score in [0, 1].
    citation_score : float
        Citation depth score in [0, 1].
    coverage_score : float
        Theoretical coverage score in [0, 1].
    composite_score : float
        Weighted composite of the three scores.
    trust_tier : TrustTier
        Trust tier of this assessment.
    computed_at : str
        ISO-8601 timestamp.
    """

    score_id: str
    ecology_id: str
    reuse_score: float
    citation_score: float
    coverage_score: float
    composite_score: float
    trust_tier: TrustTier
    computed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score_id": self.score_id,
            "ecology_id": self.ecology_id,
            "reuse_score": self.reuse_score,
            "citation_score": self.citation_score,
            "coverage_score": self.coverage_score,
            "composite_score": self.composite_score,
            "trust_tier": self.trust_tier.name,
            "computed_at": self.computed_at,
        }

    def is_healthy(self) -> bool:
        return self.composite_score >= 0.6


# ============================================================================
# Module-level measurement functions
# ============================================================================

def measure_reuse_breadth(
    theorem_id: str,
    usage_database: Optional[List[Any]] = None,
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> MetricJudgment8:
    """Measure the reuse breadth of a theorem.

    Parameters
    ----------
    theorem_id : str
        The theorem whose reuse breadth should be measured.
    usage_database : list, optional
        List of usage records; if None, an empty breadth is assumed.
    trust_tier : TrustTier, optional
        Trust tier for the returned judgment.

    Returns
    -------
    MetricJudgment8
        An 8-tuple judgment encoding the breadth measurement outcome.
    """
    records = usage_database or []
    unique_contexts = len({getattr(r, "context_id", str(r)) for r in records})
    breadth_score = _clamp_eco(unique_contexts / max(1, len(records) + 1))
    evidence = (f"theorem_id={theorem_id}", f"usage_count={len(records)}",
                f"unique_contexts={unique_contexts}")
    obligations: tuple = () if unique_contexts > 0 else ("measure-with-real-usage-data",)
    tier = trust_tier if unique_contexts >= 3 else TrustTier.PROPOSAL
    return MetricJudgment8(
        context=f"reuse_breadth/{theorem_id[:8]}",
        formula=f"reuse_breadth_score({theorem_id}) = {breadth_score:.3f}",
        authority="measure_reuse_breadth",
        evidence=evidence,
        obligations=obligations,
        budget=max(0.0, 1.0 - 0.1 * len(obligations)),
        trust_tier=tier,
        proof_chain=(f"breadth={breadth_score:.3f}", f"contexts={unique_contexts}"),
    )


def compute_citation_depth(
    theorem_id: str,
    graph: Optional[Dict[str, List[str]]] = None,
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> MetricJudgment8:
    """Compute citation depth for a theorem in a citation graph.

    Parameters
    ----------
    theorem_id : str
        Root theorem whose citation depth is measured.
    graph : dict, optional
        Adjacency dict mapping theorem IDs to lists of cited theorem IDs.
    trust_tier : TrustTier, optional
        Trust tier for the returned judgment.

    Returns
    -------
    MetricJudgment8
        8-tuple judgment with depth measurement.
    """
    g = graph or {}
    # BFS depth measurement
    depth = 0
    visited: set = {theorem_id}
    frontier = [theorem_id]
    while frontier:
        next_frontier = []
        for node in frontier:
            for neighbour in g.get(node, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    next_frontier.append(neighbour)
        if next_frontier:
            depth += 1
        frontier = next_frontier
    depth_score = _clamp_eco(math.log1p(depth) / math.log1p(max(depth, 10)))
    evidence = (f"theorem_id={theorem_id}", f"max_depth={depth}",
                f"nodes_visited={len(visited)}")
    obligations: tuple = () if depth > 0 else ("build-citation-graph",)
    return MetricJudgment8(
        context=f"citation_depth/{theorem_id[:8]}",
        formula=f"citation_depth({theorem_id}) = {depth}",
        authority="compute_citation_depth",
        evidence=evidence,
        obligations=obligations,
        budget=max(0.0, 1.0 - 0.1 * len(obligations)),
        trust_tier=trust_tier,
        proof_chain=(f"depth={depth}", f"depth_score={depth_score:.3f}"),
    )


def assess_theoretical_coverage(
    domain_id: str,
    available_theorems: Optional[List[str]] = None,
    domain_size_estimate: int = 100,
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> MetricJudgment8:
    """Assess the theoretical coverage of a domain.

    Parameters
    ----------
    domain_id : str
        The domain whose coverage is assessed.
    available_theorems : list of str, optional
        Theorem IDs available for this domain.
    domain_size_estimate : int, optional
        Estimated total theorems in the domain.
    trust_tier : TrustTier, optional
        Trust tier for the returned judgment.

    Returns
    -------
    MetricJudgment8
        8-tuple judgment with coverage assessment.
    """
    theorems = available_theorems or []
    n_available = len(theorems)
    coverage_fraction = _clamp_eco(n_available / max(1, domain_size_estimate))
    gaps = max(0, domain_size_estimate - n_available)
    evidence = (f"domain_id={domain_id}", f"n_available={n_available}",
                f"domain_size_estimate={domain_size_estimate}")
    obligations: tuple = () if coverage_fraction >= 0.50 else (
        f"cover-at-least-50%-of-{domain_id}",
    )
    tier = TrustTier.VERIFIED if coverage_fraction >= 0.80 else trust_tier
    return MetricJudgment8(
        context=f"theoretical_coverage/{domain_id[:16]}",
        formula=f"coverage({domain_id}) = {coverage_fraction:.3f}",
        authority="assess_theoretical_coverage",
        evidence=evidence,
        obligations=obligations,
        budget=max(0.0, 1.0 - gaps / max(1, domain_size_estimate)),
        trust_tier=tier,
        proof_chain=(
            f"coverage={coverage_fraction:.3f}",
            f"gaps={gaps}",
        ),
    )


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    run_smoke_test()
