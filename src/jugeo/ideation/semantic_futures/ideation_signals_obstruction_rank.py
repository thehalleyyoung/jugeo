"""
Ideation signals from obstruction ranking.

Identifies which obstructions in a semantic planning context signal future
ideation directions, ranks them by ideation potential, and extracts structured
future-direction hints for downstream reasoning pipelines.

# copilot: s03 – obstruction → ideation-signal → future-direction pipeline
"""
from __future__ import annotations

import enum
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

try:
    from jugeo.core.context import JugeoContext  # type: ignore
except ImportError:
    JugeoContext = None  # type: ignore

try:
    from jugeo.ideation.obstruction import ObstructionRecord  # type: ignore
except ImportError:
    ObstructionRecord = None  # type: ignore

try:
    from jugeo.ideation.semantic_futures.semantic_futures import SemanticFuture  # type: ignore
except ImportError:
    SemanticFuture = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    """Return a short collision-resistant identifier."""
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _sigmoid(x: float) -> float:
    """Logistic sigmoid, maps any real number to (0, 1)."""
    return 1.0 / (1.0 + math.exp(-x))


def _normalise_scores(scores: Dict[str, float]) -> Dict[str, float]:
    """
    Normalise a score dict so all values lie in [0, 1].

    Uses min-max normalisation; if all values are equal the result is all 0.5.
    """
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    span = hi - lo
    if span == 0.0:
        return {k: 0.5 for k in scores}
    return {k: (v - lo) / span for k, v in scores.items()}


def _weighted_mean(values: Dict[str, float], weights: Dict[str, float]) -> float:
    """
    Compute a weighted mean of *values* using *weights*.

    Missing weights default to 1.0; missing values are skipped.
    The result is clamped to [0, 1].
    """
    total_weight = 0.0
    total_value = 0.0
    for k, v in values.items():
        w = weights.get(k, 1.0)
        total_value += v * w
        total_weight += w
    if total_weight == 0.0:
        return 0.0
    return _clamp(total_value / total_weight)


def _entropy(distribution: Dict[str, float]) -> float:
    """
    Compute the Shannon entropy of a probability distribution.

    *distribution* values should sum to 1; any zeros are skipped.
    Returns a value in [0, log2(n)] where n is the number of categories.
    """
    h = 0.0
    for p in distribution.values():
        if p > 0.0:
            h -= p * math.log2(p)
    return h


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    """
    Compute cosine similarity between two sparse feature vectors.

    Keys present in only one dict are treated as zero in the other.
    Returns a value in [-1, 1]; returns 0.0 if either vector is the zero vector.
    """
    all_keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in all_keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# Old helper - kept for compatibility
def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on any error.

    Args:
        value: The value to convert.
        default: The fallback value if conversion fails.

    Returns:
        A float representation of value, or default on failure.
    """
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: The value to clamp.
        lo: The lower bound (inclusive).
        hi: The upper bound (inclusive).

    Returns:
        The clamped value.
    """
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _ema(previous: float, current: float, alpha: float = 0.1) -> float:
    """Compute an exponential moving average update.

    Args:
        previous: The previous EMA value.
        current: The new observation.
        alpha: The smoothing factor in (0, 1].  Defaults to 0.1.

    Returns:
        The updated EMA value.
    """
    alpha = _clamp(_safe_float(alpha, 0.1), 1e-9, 1.0)
    return alpha * _safe_float(current, 0.0) + (1.0 - alpha) * _safe_float(previous, 0.0)


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionRankMap:
    """An immutable map from coordinate IDs to their obstruction ranks.

    The obstruction rank of a coordinate c is the number of other coordinates
    that list c as a dependency.  High-rank coordinates are critical-path nodes.

    Attributes:
        rank_map: A dict mapping coordinate ID to its integer obstruction rank.
        total_coords: Total number of coordinates in the search space.
        timestamp: Unix timestamp at which the rank map was computed.
    """

    rank_map: dict[str, int]
    total_coords: int
    timestamp: float

    def top_k(self, k: int) -> list[tuple[str, int]]:
        """Return the top-k coordinates by obstruction rank.

        Args:
            k: The number of top-ranked coordinates to return.

        Returns:
            A list of (coord_id, rank) tuples sorted in descending rank order.
        """
        k = max(0, int(k))
        pairs = list(self.rank_map.items())
        # Use heapq.nlargest for efficiency on large maps
        top = heapq.nlargest(k, pairs, key=lambda p: p[1])
        return top

    def critical_path_coords(self, threshold: int) -> list[str]:
        """Return all coordinates with rank >= threshold.

        Args:
            threshold: The minimum rank to be considered critical path.

        Returns:
            A list of coordinate IDs whose rank meets or exceeds the threshold.
        """
        return [c for c, r in self.rank_map.items() if r >= threshold]


@dataclass(frozen=True, slots=True)
class OverlapEntropyReport:
    """An immutable report on the overlap entropy of the current idea pool.

    Overlap entropy measures the diversity of support scope coverage across
    the idea pool.  High entropy implies good diversity; low entropy implies
    redundancy (many ideas covering the same coordinates).

    Attributes:
        entropy: The computed overlap entropy H (in nats).
        coord_coverage: A dict mapping coord_id -> fraction of ideas covering it.
        most_covered: The coordinate covered by the most ideas.
        least_covered: The coordinate covered by the fewest ideas (>0 coverage).
        timestamp: Unix timestamp of the entropy computation.
    """

    entropy: float
    coord_coverage: dict[str, float]
    most_covered: str
    least_covered: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class BottleneckCoordinate:
    """A single bottleneck coordinate with rank and coverage information.

    A bottleneck coordinate is one that is (a) obstructed, (b) high-rank
    (many others depend on it), and (c) covered by few ideas in the pool.

    Attributes:
        coord_id: The identifier of the bottleneck coordinate.
        rank: The obstruction rank of this coordinate.
        coverage_count: Number of ideas currently covering this coordinate.
        coverage_deficit: Fraction of ideas that do NOT cover this coordinate.
        is_obstructed: Whether this coordinate is currently obstructed.
    """

    coord_id: str
    rank: int
    coverage_count: int
    coverage_deficit: float
    is_obstructed: bool

    def severity(self) -> float:
        """Compute the severity score for this bottleneck.

        Severity is defined as:

            rank * (1 - coverage_count / max(1, rank)) * (1 if obstructed else 0.5)

        Higher severity indicates the coordinate is more urgently in need of
        new ideas targeting it.

        Returns:
            A non-negative float representing bottleneck severity.
        """
        rank = max(0, int(self.rank))
        cov = max(0, int(self.coverage_count))
        obstruction_factor = 1.0 if self.is_obstructed else 0.5
        coverage_ratio = cov / max(1, rank)
        return rank * (1.0 - coverage_ratio) * obstruction_factor


@dataclass(frozen=True, slots=True)
class BottleneckGeometry:
    """The geometric structure of bottlenecks in the current search state.

    A bottleneck geometry is the set of all bottleneck coordinates, together
    with their ranks and coverage deficits.  It provides a structured view of
    where the ideation process should focus its attention.

    Attributes:
        geometry_id: A unique identifier for this geometry snapshot.
        bottlenecks: A tuple of BottleneckCoordinate records.
        timestamp: Unix timestamp of the geometry computation.
    """

    geometry_id: str
    bottlenecks: tuple[BottleneckCoordinate, ...]
    timestamp: float

    def most_severe(self) -> BottleneckCoordinate | None:
        """Return the bottleneck with the highest severity score.

        Args:
            None

        Returns:
            The BottleneckCoordinate with maximum severity(), or None if there
            are no bottlenecks.
        """
        if not self.bottlenecks:
            return None
        return max(self.bottlenecks, key=lambda b: b.severity())

    def total_severity(self) -> float:
        """Return the sum of severity scores across all bottlenecks.

        Returns:
            Total severity as a float.  0.0 if there are no bottlenecks.
        """
        return sum(b.severity() for b in self.bottlenecks)


# ---------------------------------------------------------------------------
# Mutable dataclasses (computers and detectors)
# ---------------------------------------------------------------------------


@dataclass
class ObstructionRankComputer:
    """Computes obstruction ranks for a set of coordinates.

    The obstruction rank of coordinate c is the number of coordinates c' such
    that c ∈ dependency_graph[c'].  That is, how many coordinates list c as a
    prerequisite.

    Attributes:
        computer_id: A unique identifier for this computer instance.
    """

    computer_id: str

    def compute(
        self,
        obstructed_coords: set[str],
        dependency_graph: dict[str, set[str]],
    ) -> ObstructionRankMap:
        """Compute the obstruction rank map for all obstructed coordinates.

        For each c in obstructed_coords, rank(c) is the count of coordinates
        c' (anywhere in the graph) such that c ∈ dependency_graph[c'].

        Args:
            obstructed_coords: The set of obstructed coordinate IDs to rank.
            dependency_graph: A dict mapping coord_id -> set of its dependencies.

        Returns:
            An ObstructionRankMap with ranks for all obstructed coordinates.
        """
        rank_map: dict[str, int] = {c: 0 for c in obstructed_coords}

        for c_prime, deps in dependency_graph.items():
            for dep in deps:
                if dep in rank_map:
                    rank_map[dep] += 1

        total_coords = len(dependency_graph) + len(
            obstructed_coords - set(dependency_graph.keys())
        )

        return ObstructionRankMap(
            rank_map=rank_map,
            total_coords=total_coords,
            timestamp=time.time(),
        )


@dataclass
class OverlapEntropyComputer:
    """Computes the overlap entropy of the current idea pool.

    Overlap entropy measures the diversity of coordinate coverage across the
    idea pool.  A higher value indicates that ideas are covering a more
    diverse set of coordinates.

    Attributes:
        computer_id: A unique identifier for this computer instance.
    """

    computer_id: str

    def compute(
        self,
        ideas: list[Any],
        all_coords: set[str],
    ) -> OverlapEntropyReport:
        """Compute the overlap entropy report for the given idea pool.

        For each coordinate c in all_coords, p_c = (number of ideas covering c)
        / max(1, len(ideas)).  Entropy H = -sum(p * ln(p) for p > 0).

        Args:
            ideas: A list of idea objects.  Each must have a support_scope
                attribute (frozenset or set of coord IDs).
            all_coords: The complete set of coordinate IDs in the search space.

        Returns:
            An OverlapEntropyReport capturing entropy, coverage, extremes.
        """
        n_ideas = max(1, len(ideas))
        coord_coverage: dict[str, float] = {}

        for coord in all_coords:
            count = sum(1 for idea in ideas if coord in getattr(idea, "support_scope", set()))
            coord_coverage[coord] = count / n_ideas

        entropy = 0.0
        for p in coord_coverage.values():
            if p > 0.0:
                entropy -= p * math.log(p)

        # Most / least covered (among coords with >0 coverage)
        covered_items = [(c, p) for c, p in coord_coverage.items() if p > 0.0]
        if covered_items:
            most_covered = max(covered_items, key=lambda x: x[1])[0]
            least_covered = min(covered_items, key=lambda x: x[1])[0]
        else:
            # Fall back to arbitrary coords if nothing is covered
            all_coord_list = sorted(all_coords)
            most_covered = all_coord_list[0] if all_coord_list else ""
            least_covered = all_coord_list[-1] if all_coord_list else ""

        return OverlapEntropyReport(
            entropy=_safe_float(entropy, 0.0),
            coord_coverage=coord_coverage,
            most_covered=most_covered,
            least_covered=least_covered,
            timestamp=time.time(),
        )


@dataclass
class BottleneckGeometryDetector:
    """Detects bottleneck coordinates from rank and entropy information.

    A coordinate is classified as a bottleneck if it is obstructed and has
    an obstruction rank >= rank_threshold.

    Attributes:
        detector_id: A unique identifier for this detector instance.
        rank_threshold: Minimum obstruction rank to be flagged as bottleneck.
    """

    detector_id: str
    rank_threshold: int = 2

    def detect(
        self,
        rank_map: ObstructionRankMap,
        entropy_report: OverlapEntropyReport,
        idea_pool: list[Any],
        obstructed_coords: set[str],
    ) -> BottleneckGeometry:
        """Detect bottleneck coordinates from rank and coverage information.

        For each coordinate in obstructed_coords with rank >= rank_threshold,
        computes:
          - coverage_count: number of ideas in idea_pool covering it
          - coverage_deficit: fraction of ideas NOT covering it

        Args:
            rank_map: The precomputed ObstructionRankMap.
            entropy_report: The overlap entropy report (for coverage fractions).
            idea_pool: The current list of idea objects.
            obstructed_coords: The set of currently obstructed coordinates.

        Returns:
            A BottleneckGeometry containing all detected bottleneck coordinates.
        """
        n_ideas = max(1, len(idea_pool))
        bottlenecks: list[BottleneckCoordinate] = []

        for coord in obstructed_coords:
            rank = rank_map.rank_map.get(coord, 0)
            if rank < self.rank_threshold:
                continue

            cov_count = sum(
                1 for idea in idea_pool
                if coord in getattr(idea, "support_scope", set())
            )
            cov_deficit = _clamp(1.0 - cov_count / n_ideas, 0.0, 1.0)

            bottlenecks.append(
                BottleneckCoordinate(
                    coord_id=coord,
                    rank=rank,
                    coverage_count=cov_count,
                    coverage_deficit=cov_deficit,
                    is_obstructed=True,
                )
            )

        # Sort by severity descending for deterministic output
        bottlenecks.sort(key=lambda b: b.severity(), reverse=True)

        return BottleneckGeometry(
            geometry_id=str(uuid.uuid4()),
            bottlenecks=tuple(bottlenecks),
            timestamp=time.time(),
        )


@dataclass
class IdeationSignalsObstructionRankAnalyzer:
    """Synthesizes obstruction rank, entropy, and bottleneck signals into a report.

    Provides both a structured analysis dict and a human-readable focus
    recommendation to guide the next round of ideation.

    Attributes:
        analyzer_id: A unique identifier for this analyzer instance.
    """

    analyzer_id: str

    def analyze(
        self,
        rank_map: ObstructionRankMap,
        entropy_report: OverlapEntropyReport,
        geometry: BottleneckGeometry,
    ) -> dict[str, Any]:
        """Produce a synthesis report from all three ideation signals.

        Args:
            rank_map: The obstruction rank map.
            entropy_report: The overlap entropy report.
            geometry: The bottleneck geometry.

        Returns:
            A dict containing:
              - top_ranks (list of (coord_id, rank) pairs)
              - entropy (float)
              - n_bottlenecks (int)
              - total_severity (float)
              - critical_coords (list of str)
              - entropy_assessment ("LOW", "MEDIUM", or "HIGH")
              - recommendation (str)
        """
        top_ranks = rank_map.top_k(5)
        entropy = _safe_float(entropy_report.entropy, 0.0)
        n_bottlenecks = len(geometry.bottlenecks)
        total_sev = geometry.total_severity()

        # Critical coords: rank >= 3 (strict threshold)
        critical_coords = rank_map.critical_path_coords(threshold=3)

        # Entropy assessment
        if entropy < 0.5:
            entropy_assessment = "LOW"
        elif entropy < 1.5:
            entropy_assessment = "MEDIUM"
        else:
            entropy_assessment = "HIGH"

        # Recommendation
        most_sev = geometry.most_severe()
        if most_sev is not None and most_sev.severity() > 5.0:
            recommendation = (
                f"CRITICAL: Coordinate '{most_sev.coord_id}' has severity {most_sev.severity():.1f}. "
                f"Generate new ideas targeting '{most_sev.coord_id}' immediately."
            )
        elif n_bottlenecks > 0 and entropy_assessment == "LOW":
            recommendation = (
                f"DIVERSIFY: Entropy is {entropy:.3f} (LOW) with {n_bottlenecks} bottleneck(s). "
                "Expand the idea pool to cover under-represented coordinates."
            )
        elif n_bottlenecks == 0 and entropy_assessment == "HIGH":
            recommendation = (
                f"FOCUS: No bottlenecks detected and entropy is HIGH ({entropy:.3f}). "
                "Consolidate ideas around the highest-leverage coordinates."
            )
        elif len(critical_coords) > 0:
            cc_list = ", ".join(sorted(critical_coords)[:3])
            recommendation = (
                f"TARGET: Critical-path coordinates [{cc_list}] need coverage. "
                "Generate ideas addressing these coordinates first."
            )
        else:
            recommendation = (
                f"MAINTAIN: Search state is balanced "
                f"(entropy={entropy:.3f}, bottlenecks={n_bottlenecks}). "
                "Continue current ideation strategy."
            )

        return {
            "top_ranks": top_ranks,
            "entropy": entropy,
            "n_bottlenecks": n_bottlenecks,
            "total_severity": total_sev,
            "critical_coords": critical_coords,
            "entropy_assessment": entropy_assessment,
            "recommendation": recommendation,
        }

    def recommended_focus(self, analysis: dict[str, Any]) -> str:
        """Extract a human-readable focus recommendation from an analysis report.

        Args:
            analysis: A dict as returned by analyze().

        Returns:
            A human-readable string describing the recommended focus area.
        """
        rec = analysis.get("recommendation", "")
        entropy_assess = analysis.get("entropy_assessment", "UNKNOWN")
        n_bn = analysis.get("n_bottlenecks", 0)
        top_ranks = analysis.get("top_ranks", [])
        top_coord = top_ranks[0][0] if top_ranks else "unknown"

        return (
            f"Focus on coordinate '{top_coord}' (highest rank). "
            f"Entropy is {entropy_assess}; {n_bn} bottleneck(s) detected. "
            f"Recommendation: {rec}"
        )


@dataclass(frozen=True, slots=True)
class IdeationSignalsObstructionRankWitness:
    """An immutable witness record for an ideation signal computation run.

    Attributes:
        witness_id: A unique identifier for this witness.
        timestamp: Unix timestamp of the run.
        success: Whether the run completed without error.
        top_obstruction_rank: The highest obstruction rank observed.
        overlap_entropy: The computed overlap entropy of the idea pool.
        n_bottlenecks: The number of bottleneck coordinates detected.
        most_severe_bottleneck: The coord_id of the most severe bottleneck, or None.
        analysis_report: The full dict from IdeationSignalsObstructionRankAnalyzer.
        summary: A human-readable one-line summary of the run.
    """

    witness_id: str
    timestamp: float
    success: bool
    top_obstruction_rank: int
    overlap_entropy: float
    n_bottlenecks: int
    most_severe_bottleneck: str | None
    analysis_report: dict[str, Any]
    summary: str


@dataclass
class IdeationSignalsObstructionRankCoordinator:
    """Coordinator for ideation signal computation in JuGeo.

    # copilot: The coordinator orchestrates ideation signal computation.
    # copilot: It accepts obstructed coords, dependency graph, and idea pool and:
    # copilot:
    # copilot: 1. Computes obstruction ranks for all obstructed coordinates.
    # copilot: 2. Computes overlap entropy of the current idea pool.
    # copilot: 3. Detects bottleneck geometry from ranks and coverage.
    # copilot: 4. Runs IdeationSignalsObstructionRankAnalyzer for synthesis.
    # copilot: 5. Generates a recommended focus area from the analysis.
    # copilot: 6. Seals and returns an IdeationSignalsObstructionRankWitness.

    Attributes:
        session_id: A unique identifier for this coordinator session.
        rank_threshold: Minimum rank for bottleneck detection.  Defaults to 2.
        entropy_target: Target overlap entropy for the idea pool.  Defaults to 2.0.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    rank_threshold: int = 2
    entropy_target: float = 2.0

    def run(
        self,
        obstructed_coords: set[str],
        dependency_graph: dict[str, set[str]],
        idea_pool: list[Any],
        all_coords: set[str],
    ) -> IdeationSignalsObstructionRankWitness:
        """Execute the full ideation signal computation pipeline.

        Args:
            obstructed_coords: The set of currently obstructed coordinate IDs.
            dependency_graph: A dict mapping coord_id -> set of dependency IDs.
            idea_pool: The current list of idea objects (duck-typed, with
                support_scope attribute).
            all_coords: The complete set of coordinate IDs in the search space.

        Returns:
            An IdeationSignalsObstructionRankWitness capturing all outputs.
        """
        success = True
        top_rank = 0
        entropy = 0.0
        n_bottlenecks = 0
        most_severe_id: str | None = None
        report: dict[str, Any] = {}
        focus: str = ""

        try:
            # Step 1: Compute obstruction ranks
            rank_computer = ObstructionRankComputer(
                computer_id=f"{self.session_id}:rank"
            )
            rank_map = rank_computer.compute(obstructed_coords, dependency_graph)
            if rank_map.rank_map:
                top_rank = max(rank_map.rank_map.values())

            # Step 2: Compute overlap entropy
            entropy_computer = OverlapEntropyComputer(
                computer_id=f"{self.session_id}:ent"
            )
            entropy_report = entropy_computer.compute(idea_pool, all_coords)
            entropy = _safe_float(entropy_report.entropy, 0.0)

            # Step 3: Detect bottleneck geometry
            detector = BottleneckGeometryDetector(
                detector_id=f"{self.session_id}:det",
                rank_threshold=self.rank_threshold,
            )
            geometry = detector.detect(rank_map, entropy_report, idea_pool, obstructed_coords)
            n_bottlenecks = len(geometry.bottlenecks)
            most_severe_bn = geometry.most_severe()
            most_severe_id = most_severe_bn.coord_id if most_severe_bn is not None else None

            # Step 4: Analyze
            analyzer = IdeationSignalsObstructionRankAnalyzer(
                analyzer_id=f"{self.session_id}:anal"
            )
            report = analyzer.analyze(rank_map, entropy_report, geometry)

            # Step 5: Generate focus
            focus = analyzer.recommended_focus(report)
            report["focus"] = focus
            report["entropy_vs_target"] = entropy - self.entropy_target

        except Exception as exc:  # pragma: no cover
            success = False
            report = {"error": str(exc)}
            focus = "ERROR: signal computation failed"

        summary = (
            f"[{self.session_id}] "
            f"top_rank={top_rank} entropy={entropy:.3f} "
            f"bottlenecks={n_bottlenecks} "
            f"most_severe={most_severe_id} "
            f"success={success}"
        )

        return IdeationSignalsObstructionRankWitness(
            witness_id=str(uuid.uuid4()),
            timestamp=time.time(),
            success=success,
            top_obstruction_rank=top_rank,
            overlap_entropy=entropy,
            n_bottlenecks=n_bottlenecks,
            most_severe_bottleneck=most_severe_id,
            analysis_report=report,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Module-level standalone functions
# ---------------------------------------------------------------------------


def compute_obstruction_rank(
    coord: str,
    dependency_graph: dict[str, set[str]],
) -> int:
    """Compute the obstruction rank of a single coordinate.

    The rank of coord c is the count of coordinates c' such that
    c ∈ dependency_graph.get(c', set()).  That is, the number of other
    coordinates that list c as a prerequisite.

    Args:
        coord: The coordinate ID whose rank to compute.
        dependency_graph: A dict mapping coord_id -> set of its dependency IDs.

    Returns:
        A non-negative integer obstruction rank.
    """
    count = 0
    for c_prime, deps in dependency_graph.items():
        if c_prime != coord and coord in deps:
            count += 1
    return count


def compute_overlap_entropy(
    ideas: list[Any],
    all_coords: set[str],
) -> float:
    """Compute the overlap entropy of the idea pool over all coordinates.

    The overlap entropy is:

        H = -sum_{c in all_coords} p_c * ln(p_c)

    where p_c = (number of ideas with c in support_scope) / max(1, len(ideas)).

    Args:
        ideas: A list of idea objects with a support_scope attribute.
        all_coords: The complete set of coordinate IDs.

    Returns:
        The overlap entropy H as a non-negative float (in nats).
    """
    n = max(1, len(ideas))
    entropy = 0.0
    for coord in all_coords:
        count = sum(1 for idea in ideas if coord in getattr(idea, "support_scope", set()))
        p = count / n
        if p > 0.0:
            entropy -= p * math.log(p)
    return _safe_float(entropy, 0.0)


def identify_bottlenecks(
    rank_map: ObstructionRankMap,
    coverage_map: dict[str, int],
    obstructed: set[str],
) -> list[BottleneckCoordinate]:
    """Build a list of BottleneckCoordinate for obstructed, high-rank coords.

    A coordinate is included if it is in *obstructed* and has a rank > 0 in
    the rank_map.  coverage_map provides coverage counts per coordinate.

    Args:
        rank_map: The precomputed ObstructionRankMap.
        coverage_map: A dict mapping coord_id -> number of ideas covering it.
        obstructed: The set of currently obstructed coordinate IDs.

    Returns:
        A list of BottleneckCoordinate objects sorted by severity descending.
    """
    total_ideas = max(1, sum(coverage_map.values())) if coverage_map else 1
    bottlenecks: list[BottleneckCoordinate] = []

    for coord in obstructed:
        rank = rank_map.rank_map.get(coord, 0)
        if rank <= 0:
            continue
        cov_count = coverage_map.get(coord, 0)
        cov_deficit = _clamp(1.0 - cov_count / total_ideas, 0.0, 1.0)
        bottlenecks.append(
            BottleneckCoordinate(
                coord_id=coord,
                rank=rank,
                coverage_count=cov_count,
                coverage_deficit=cov_deficit,
                is_obstructed=True,
            )
        )

    bottlenecks.sort(key=lambda b: b.severity(), reverse=True)
    return bottlenecks


def coverage_deficit(coord: str, idea_pool: list[Any]) -> float:
    """Compute the coverage deficit of a coordinate relative to the idea pool.

    The coverage deficit is the fraction of ideas that do NOT have *coord* in
    their support scope:

        deficit = 1 - (coverage_count / max(1, len(idea_pool)))

    Args:
        coord: The coordinate ID to check.
        idea_pool: The list of idea objects to check coverage against.

    Returns:
        A float in [0, 1]: 1.0 if no idea covers coord, 0.0 if all do.
    """
    n = max(1, len(idea_pool))
    covering = sum(1 for idea in idea_pool if coord in getattr(idea, "support_scope", set()))
    return _clamp(1.0 - covering / n, 0.0, 1.0)


def signal_summary(
    rank_map: ObstructionRankMap,
    entropy: float,
    geometry: BottleneckGeometry,
) -> str:
    """Generate a concise one-line summary of the current ideation signals.

    Args:
        rank_map: The obstruction rank map.
        entropy: The computed overlap entropy.
        geometry: The bottleneck geometry.

    Returns:
        A single-line string summarising the key signal values.
    """
    top_rank = max(rank_map.rank_map.values()) if rank_map.rank_map else 0
    n_critical = len(rank_map.critical_path_coords(threshold=3))
    most_sev = geometry.most_severe()
    sev_str = f"{most_sev.coord_id}(sev={most_sev.severity():.1f})" if most_sev else "none"
    return (
        f"top_rank={top_rank} | entropy={entropy:.3f} | "
        f"bottlenecks={len(geometry.bottlenecks)} | "
        f"critical={n_critical} | most_severe={sev_str}"
    )


def ideation_priority(
    coord: str,
    rank_map: ObstructionRankMap,
    geometry: BottleneckGeometry,
) -> float:
    """Compute a priority score for generating new ideas targeting a coordinate.

    The priority combines:
      - Obstruction rank (normalised by max rank in the map)
      - Bottleneck severity (0 if coord is not a bottleneck)

    Args:
        coord: The coordinate ID to compute priority for.
        rank_map: The obstruction rank map.
        geometry: The bottleneck geometry.

    Returns:
        A non-negative float priority score.
    """
    rank = rank_map.rank_map.get(coord, 0)
    max_rank = max(rank_map.rank_map.values()) if rank_map.rank_map else 1
    normalised_rank = rank / max(1, max_rank)

    severity = 0.0
    for bn in geometry.bottlenecks:
        if bn.coord_id == coord:
            severity = bn.severity()
            break

    max_sev = geometry.total_severity()
    normalised_sev = severity / max(1e-9, max_sev)

    priority = 0.6 * normalised_rank + 0.4 * normalised_sev
    return _clamp(priority, 0.0, 1.0)


# ---------------------------------------------------------------------------
# TrustTier enum (required by jugeo judgment schema)
# ---------------------------------------------------------------------------

class TrustTier(enum.Enum):
    """Ordinal trust levels for judgments in the jugeo system.

    Tiers progress from informal proposals to machine-verified proofs.
    Every judgment must carry exactly one TrustTier value so that consumers
    can decide whether to act on the judgment or request further validation.
    """
    PROPOSAL          = 1   # Human-authored, not yet reviewed
    REVIEWED          = 2   # Peer-reviewed but not formally verified
    VERIFIED          = 3   # Formally verified by static analysis
    RUNTIME_WITNESSED = 4   # Witnessed at runtime by a trusted observer
    PROOF_BACKED      = 5   # Backed by a machine-checked proof

    def at_least(self, other: TrustTier) -> bool:
        """Return True iff this tier is >= *other* in the ordinal ordering."""
        return self.value >= other.value


# ---------------------------------------------------------------------------
# SignalJudgment — 8-tuple judgment for ideation signals
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SignalJudgment:
    """An 8-tuple judgment about an ideation signal derived from obstruction ranking.

    Fields follow the jugeo judgment schema (c, phi, A, E, O, B, T, Pi):
      context      (c) — the obstruction context under evaluation
      formula      (phi) — the formal property being judged
      authority    (A) — the agent or rule set asserting the judgment
      evidence     (E) — evidence supporting the judgment
      obligations  (O) — follow-up actions required after the judgment
      budget       (B) — resource budget consumed for this judgment
      trust_tier   (T) — confidence level from TrustTier
      proof_chain  (Pi) — ordered sequence of reasoning steps
    """
    context:     str
    formula:     str
    authority:   str
    evidence:    Tuple[str, ...]
    obligations: Tuple[str, ...]
    budget:      float
    trust_tier:  TrustTier
    proof_chain: Tuple[str, ...]

    def upgrade(self, new_tier: TrustTier, extra_proof: str) -> SignalJudgment:
        """Return a copy with an upgraded trust tier."""
        return SignalJudgment(
            context=self.context, formula=self.formula, authority=self.authority,
            evidence=self.evidence, obligations=self.obligations, budget=self.budget,
            trust_tier=new_tier, proof_chain=self.proof_chain + (extra_proof,),
        )


# ---------------------------------------------------------------------------
# IdeationSignal — frozen dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IdeationSignal:
    """A structured signal extracted from an obstruction record indicating a future ideation direction.

    Attributes
    ----------
    signal_id             : unique identifier
    obstruction_id        : the obstruction that produced this signal
    signal_type           : type of signal ('structural', 'coverage', 'rank', 'entropy')
    strength              : signal strength in [0, 1]
    future_direction_hints: tuple of direction hint strings
    trust_tier            : confidence in this signal
    created_at            : ISO-8601 timestamp
    """
    signal_id:              str
    obstruction_id:         str
    signal_type:            str
    strength:               float
    future_direction_hints: Tuple[str, ...]
    trust_tier:             TrustTier
    created_at:             str

    def is_strong(self, threshold: float = 0.6) -> bool:
        """Return True iff strength >= threshold."""
        return self.strength >= threshold

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "signal_id":              self.signal_id,
            "obstruction_id":         self.obstruction_id,
            "signal_type":            self.signal_type,
            "strength":               self.strength,
            "future_direction_hints": list(self.future_direction_hints),
            "trust_tier":             self.trust_tier.name,
            "created_at":             self.created_at,
        }


# ---------------------------------------------------------------------------
# FutureDirection — frozen dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FutureDirection:
    """A future ideation direction synthesized from multiple IdeationSignals.

    Attributes
    ----------
    direction_id      : unique identifier
    name              : short name for this direction
    description       : human-readable description
    supporting_signals: tuple of signal_ids that support this direction
    confidence        : confidence score in [0, 1]
    domain_hints      : tuple of domain/concept hints for ideation
    trust_tier        : confidence level
    created_at        : ISO-8601 timestamp
    """
    direction_id:       str
    name:               str
    description:        str
    supporting_signals: Tuple[str, ...]
    confidence:         float
    domain_hints:       Tuple[str, ...]
    trust_tier:         TrustTier
    created_at:         str

    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """Return True iff confidence >= threshold."""
        return self.confidence >= threshold

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "direction_id":       self.direction_id,
            "name":               self.name,
            "description":        self.description,
            "supporting_signals": list(self.supporting_signals),
            "confidence":         self.confidence,
            "domain_hints":       list(self.domain_hints),
            "trust_tier":         self.trust_tier.name,
            "created_at":         self.created_at,
        }


# ---------------------------------------------------------------------------
# ObstructionRecord stub (for standalone use)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ObstructionRecordStub:
    """Minimal obstruction record for standalone use when jugeo.ideation is unavailable.

    Attributes
    ----------
    obstruction_id   : unique identifier
    coord_id         : the coordinate that is obstructed
    obstruction_type : type of obstruction
    severity_score   : numeric severity in [0, 1]
    metadata         : free-form metadata dict
    recorded_at      : ISO-8601 timestamp
    """
    obstruction_id:   str
    coord_id:         str
    obstruction_type: str
    severity_score:   float
    metadata:         Dict[str, Any]
    recorded_at:      str


# ---------------------------------------------------------------------------
# ExtractionConfig — frozen dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    """Configuration for signal extraction from obstruction records.

    Attributes
    ----------
    min_strength   : minimum signal strength to retain
    max_signals    : maximum number of signals to extract per record
    signal_types   : tuple of signal types to include
    trust_tier     : minimum trust tier for extracted signals
    """
    min_strength: float
    max_signals:  int
    signal_types: Tuple[str, ...]
    trust_tier:   TrustTier

    @classmethod
    def default(cls) -> ExtractionConfig:
        """Return default extraction configuration."""
        return cls(
            min_strength = 0.3,
            max_signals  = 10,
            signal_types = ("structural", "coverage", "rank", "entropy"),
            trust_tier   = TrustTier.REVIEWED,
        )


# ---------------------------------------------------------------------------
# ObstructionRanking class
# ---------------------------------------------------------------------------

class ObstructionRanking:
    """Ranks obstructions by their ideation potential.

    Ideation potential is the degree to which an obstruction points toward
    a productive future ideation direction.  High-rank obstructions are
    structural bottlenecks that, if resolved by new ideas, would unlock many
    downstream coordinates.

    Parameters
    ----------
    ranking_id : unique identifier for this ranking session
    """

    def __init__(self, ranking_id: str) -> None:
        self.ranking_id = ranking_id
        self._ranked: List[Tuple[Any, float]] = []  # (obstruction, potential)
        self._created_at: str = datetime.now(timezone.utc).isoformat()

    def rank(
        self,
        obstructions: Sequence[Any],
        ranking_criterion: str = "rank_weighted",
    ) -> List[Tuple[Any, float]]:
        """Rank *obstructions* by ideation potential.

        Parameters
        ----------
        obstructions      : sequence of obstruction records or stubs
        ranking_criterion : one of 'rank_weighted', 'severity', 'uniform'

        Returns
        -------
        List of (obstruction, potential) pairs sorted descending by potential
        """
        scored: List[Tuple[Any, float]] = []
        for obs in obstructions:
            potential = self.compute_ideation_potential(obs)
            scored.append((obs, potential))

        scored.sort(key=lambda x: x[1], reverse=True)
        self._ranked = scored
        return scored

    def get_top_k(self, k: int) -> List[Tuple[Any, float]]:
        """Return the top-k obstructions by ideation potential.

        Parameters
        ----------
        k : number of obstructions to return

        Returns
        -------
        Up to k (obstruction, potential) pairs
        """
        return self._ranked[:max(0, k)]

    def compute_ideation_potential(self, obstruction: Any) -> float:
        """Compute the ideation potential of a single obstruction.

        Potential is derived from the obstruction's severity, rank, and
        structural position.  Higher severity + higher rank = more potential.

        Parameters
        ----------
        obstruction : an obstruction record or stub

        Returns
        -------
        float in [0, 1]
        """
        # Extract severity from different obstruction types
        if hasattr(obstruction, "severity_score"):
            severity = float(obstruction.severity_score)
        elif hasattr(obstruction, "severity"):
            sv = obstruction.severity
            severity = sv() if callable(sv) else float(sv)
        else:
            severity = 0.5

        # Extract rank from different obstruction types
        if hasattr(obstruction, "rank"):
            rank = float(obstruction.rank)
        else:
            rank = 1.0

        # Combine into potential score
        raw = 0.6 * _clamp(severity, 0.0, 1.0) + 0.4 * _clamp(rank / 10.0, 0.0, 1.0)
        return _clamp(raw, 0.0, 1.0)

    def get_potential_judgment(
        self,
        obstruction: Any,
        authority: str = "ObstructionRanking",
    ) -> SignalJudgment:
        """Produce a SignalJudgment about the ideation potential of *obstruction*.

        Parameters
        ----------
        obstruction : the obstruction to judge
        authority   : who is issuing this judgment

        Returns
        -------
        SignalJudgment
        """
        potential = self.compute_ideation_potential(obstruction)
        obs_id = str(getattr(obstruction, "obstruction_id", id(obstruction)))

        formula = f"ideation_potential({obs_id}) = {potential:.4f}"
        tier = (
            TrustTier.VERIFIED if potential >= 0.7
            else (TrustTier.REVIEWED if potential >= 0.4 else TrustTier.PROPOSAL)
        )

        return SignalJudgment(
            context     = f"ranking:{self.ranking_id}",
            formula     = formula,
            authority   = authority,
            evidence    = (obs_id, f"potential:{potential:.4f}"),
            obligations = () if potential >= 0.5 else (f"investigate:{obs_id}",),
            budget      = 1.0,
            trust_tier  = tier,
            proof_chain = (f"severity+rank_weighted:{potential:.4f}",),
        )


# ---------------------------------------------------------------------------
# SignalExtractor class
# ---------------------------------------------------------------------------

class SignalExtractor:
    """Extracts IdeationSignals from obstruction records.

    The extractor applies configurable heuristics to identify which
    obstructions most strongly signal future ideation directions.

    Parameters
    ----------
    config : ExtractionConfig controlling the extraction behavior
    """

    def __init__(self, config: Optional[ExtractionConfig] = None) -> None:
        self.config = config or ExtractionConfig.default()
        self._extracted: List[IdeationSignal] = []

    def extract(self, obstruction_record: Any) -> List[IdeationSignal]:
        """Extract IdeationSignals from a single obstruction record.

        Parameters
        ----------
        obstruction_record : an obstruction record or stub

        Returns
        -------
        List[IdeationSignal]
        """
        obs_id = str(getattr(obstruction_record, "obstruction_id",
                              getattr(obstruction_record, "coord_id", str(id(obstruction_record)))))
        signals: List[IdeationSignal] = []
        ts = datetime.now(timezone.utc).isoformat()

        # Structural signal: based on rank
        if "structural" in self.config.signal_types:
            rank = float(getattr(obstruction_record, "rank", 1.0))
            strength = _clamp(rank / 10.0, 0.0, 1.0)
            if strength >= self.config.min_strength:
                signals.append(IdeationSignal(
                    signal_id              = uuid.uuid4().hex[:12],
                    obstruction_id         = obs_id,
                    signal_type            = "structural",
                    strength               = strength,
                    future_direction_hints = (f"resolve_structural_blockage:{obs_id}",),
                    trust_tier             = self.config.trust_tier,
                    created_at             = ts,
                ))

        # Coverage signal: based on coverage_deficit
        if "coverage" in self.config.signal_types:
            cov_def = float(getattr(obstruction_record, "coverage_deficit", 0.5))
            strength = _clamp(cov_def, 0.0, 1.0)
            if strength >= self.config.min_strength:
                signals.append(IdeationSignal(
                    signal_id              = uuid.uuid4().hex[:12],
                    obstruction_id         = obs_id,
                    signal_type            = "coverage",
                    strength               = strength,
                    future_direction_hints = (f"improve_coverage:{obs_id}",),
                    trust_tier             = self.config.trust_tier,
                    created_at             = ts,
                ))

        # Severity signal: based on severity_score
        if "rank" in self.config.signal_types:
            sev = float(getattr(obstruction_record, "severity_score",
                                 getattr(obstruction_record, "rank", 1.0) / 10.0))
            strength = _clamp(sev, 0.0, 1.0)
            if strength >= self.config.min_strength:
                signals.append(IdeationSignal(
                    signal_id              = uuid.uuid4().hex[:12],
                    obstruction_id         = obs_id,
                    signal_type            = "rank",
                    strength               = strength,
                    future_direction_hints = (f"high_severity_target:{obs_id}",),
                    trust_tier             = self.config.trust_tier,
                    created_at             = ts,
                ))

        # Limit to max_signals
        signals = signals[:self.config.max_signals]
        self._extracted.extend(signals)
        return signals

    def filter_signals(self, threshold: float) -> List[IdeationSignal]:
        """Return all extracted signals with strength >= threshold.

        Parameters
        ----------
        threshold : minimum strength to retain

        Returns
        -------
        List[IdeationSignal]
        """
        return [s for s in self._extracted if s.strength >= threshold]

    def aggregate_signals(self, signal_list: Sequence[IdeationSignal]) -> FutureDirection:
        """Aggregate a list of IdeationSignals into a FutureDirection.

        Parameters
        ----------
        signal_list : signals to aggregate

        Returns
        -------
        FutureDirection synthesized from the provided signals
        """
        if not signal_list:
            return FutureDirection(
                direction_id       = uuid.uuid4().hex[:12],
                name               = "empty_direction",
                description        = "No signals provided.",
                supporting_signals = (),
                confidence         = 0.0,
                domain_hints       = (),
                trust_tier         = TrustTier.PROPOSAL,
                created_at         = datetime.now(timezone.utc).isoformat(),
            )

        avg_strength = sum(s.strength for s in signal_list) / len(signal_list)
        signal_ids = tuple(s.signal_id for s in signal_list)
        all_hints = tuple({h for s in signal_list for h in s.future_direction_hints})

        # Determine trust tier from strongest signal
        max_tier = max(signal_list, key=lambda s: s.trust_tier.value).trust_tier

        types = sorted({s.signal_type for s in signal_list})
        name = f"direction_from_{'+'.join(types[:3])}"

        return FutureDirection(
            direction_id       = uuid.uuid4().hex[:12],
            name               = name,
            description        = (
                f"Aggregated from {len(signal_list)} signals of types {types}. "
                f"Average strength: {avg_strength:.3f}."
            ),
            supporting_signals = signal_ids,
            confidence         = _clamp(avg_strength, 0.0, 1.0),
            domain_hints       = all_hints,
            trust_tier         = max_tier,
            created_at         = datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def extract_ideation_signals(
    obstruction_database: Sequence[Any],
    extraction_config: Optional[ExtractionConfig] = None,
) -> List[IdeationSignal]:
    """Extract ideation signals from all obstruction records in *obstruction_database*.

    Parameters
    ----------
    obstruction_database : sequence of obstruction records or stubs
    extraction_config    : optional ExtractionConfig; uses default if None

    Returns
    -------
    List[IdeationSignal] — all extracted signals, sorted by strength descending
    """
    extractor = SignalExtractor(extraction_config)
    all_signals: List[IdeationSignal] = []
    for record in obstruction_database:
        signals = extractor.extract(record)
        all_signals.extend(signals)
    all_signals.sort(key=lambda s: s.strength, reverse=True)
    return all_signals


def rank_obstructions_for_ideation(
    obstructions: Sequence[Any],
    ranking_method: str = "rank_weighted",
) -> List[Tuple[Any, float]]:
    """Rank *obstructions* by their ideation potential using *ranking_method*.

    Parameters
    ----------
    obstructions   : sequence of obstruction records
    ranking_method : 'rank_weighted', 'severity', or 'uniform'

    Returns
    -------
    List of (obstruction, potential) pairs sorted descending by potential
    """
    ranking = ObstructionRanking(ranking_id=uuid.uuid4().hex[:12])
    return ranking.rank(obstructions, ranking_criterion=ranking_method)


def identify_future_directions(
    signal_set: Sequence[IdeationSignal],
    direction_threshold: float = 0.5,
) -> List[FutureDirection]:
    """Identify future directions from a set of IdeationSignals.

    Groups signals by type and aggregates each group into a FutureDirection.
    Only groups with mean strength >= direction_threshold are returned.

    Parameters
    ----------
    signal_set          : the signals to cluster into directions
    direction_threshold : minimum mean strength for a direction to be included

    Returns
    -------
    List[FutureDirection] sorted by confidence descending
    """
    if not signal_set:
        return []

    extractor = SignalExtractor()

    # Group signals by type
    by_type: Dict[str, List[IdeationSignal]] = {}
    for sig in signal_set:
        by_type.setdefault(sig.signal_type, []).append(sig)

    directions: List[FutureDirection] = []
    for sig_type, sigs in by_type.items():
        mean_strength = sum(s.strength for s in sigs) / len(sigs)
        if mean_strength >= direction_threshold:
            direction = extractor.aggregate_signals(sigs)
            directions.append(direction)

    # Also aggregate all signals together if multiple types
    if len(by_type) > 1:
        strong_sigs = [s for s in signal_set if s.strength >= direction_threshold]
        if strong_sigs:
            composite = extractor.aggregate_signals(strong_sigs)
            directions.append(composite)

    directions.sort(key=lambda d: d.confidence, reverse=True)
    return directions


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Ideation Signals Obstruction Rank Smoke Test ===\n")

    # Build a dependency graph
    dep_graph: dict[str, set[str]] = {
        "c1": {"c-base-1", "c-base-2"},
        "c2": {"c-base-1", "c3"},
        "c3": {"c-base-2", "c-base-3"},
        "c4": {"c3", "c-base-1"},
        "c5": {"c3", "c4"},
        "c6": set(),
        "c-base-1": set(),
        "c-base-2": set(),
        "c-base-3": set(),
    }

    obstructed = {"c-base-1", "c-base-2", "c3"}
    all_coords = set(dep_graph.keys()) | {"c7", "c8"}

    # Mock ideas
    @dataclass
    class _MockIdea:
        """Minimal mock idea for smoke testing."""
        idea_id: str
        support_scope: frozenset

    ideas = [
        _MockIdea("i1", frozenset({"c-base-1", "c3", "c1"})),
        _MockIdea("i2", frozenset({"c-base-2", "c3"})),
        _MockIdea("i3", frozenset({"c-base-1", "c-base-3"})),
        _MockIdea("i4", frozenset({"c4", "c5"})),
    ]

    # ObstructionRankComputer
    rank_computer = ObstructionRankComputer(computer_id="rank-01")
    rank_map = rank_computer.compute(obstructed, dep_graph)
    print(f"Rank map: {rank_map.rank_map}")
    print(f"Top-3: {rank_map.top_k(3)}")
    print(f"Critical (rank>=2): {rank_map.critical_path_coords(2)}")

    # OverlapEntropyComputer
    ent_computer = OverlapEntropyComputer(computer_id="ent-01")
    ent_report = ent_computer.compute(ideas, all_coords)
    print(f"\nOverlap entropy: {ent_report.entropy:.4f}")
    print(f"Most covered: {ent_report.most_covered}")
    print(f"Least covered: {ent_report.least_covered}")

    # BottleneckGeometryDetector
    detector = BottleneckGeometryDetector(detector_id="det-01", rank_threshold=1)
    geometry = detector.detect(rank_map, ent_report, ideas, obstructed)
    print(f"\nBottleneck count: {len(geometry.bottlenecks)}")
    print(f"Total severity: {geometry.total_severity():.4f}")
    ms = geometry.most_severe()
    if ms:
        print(f"Most severe: {ms.coord_id} (rank={ms.rank}, sev={ms.severity():.3f})")

    # Standalone functions
    rank_c_base_1 = compute_obstruction_rank("c-base-1", dep_graph)
    print(f"\nObstruction rank of c-base-1: {rank_c_base_1}")
    ent_val = compute_overlap_entropy(ideas, all_coords)
    print(f"Overlap entropy (standalone): {ent_val:.4f}")

    cov_map = {"c-base-1": 2, "c-base-2": 1, "c3": 2}
    bns = identify_bottlenecks(rank_map, cov_map, obstructed)
    print(f"Identified bottlenecks: {[b.coord_id for b in bns]}")

    def_val = coverage_deficit("c-base-1", ideas)
    print(f"Coverage deficit for c-base-1: {def_val:.4f}")

    summary_str = signal_summary(rank_map, ent_report.entropy, geometry)
    print(f"\nSignal summary: {summary_str}")

    prio = ideation_priority("c-base-1", rank_map, geometry)
    print(f"Ideation priority for c-base-1: {prio:.4f}")

    # Analyzer
    analyzer = IdeationSignalsObstructionRankAnalyzer(analyzer_id="anal-01")
    report = analyzer.analyze(rank_map, ent_report, geometry)
    print(f"\nAnalysis entropy_assessment: {report['entropy_assessment']}")
    print(f"Recommendation: {report['recommendation']}")
    focus = analyzer.recommended_focus(report)
    print(f"Focus: {focus}")

    # Coordinator
    print("\n--- Coordinator run ---")
    coord = IdeationSignalsObstructionRankCoordinator(rank_threshold=1)
    witness = coord.run(obstructed, dep_graph, ideas, all_coords)
    print(f"Witness summary: {witness.summary}")
    print(f"Top rank: {witness.top_obstruction_rank}")
    print(f"Entropy: {witness.overlap_entropy:.4f}")
    print(f"Most severe bottleneck: {witness.most_severe_bottleneck}")

    # BottleneckCoordinate.severity edge cases
    bn_zero = BottleneckCoordinate(
        coord_id="test-coord", rank=0, coverage_count=0,
        coverage_deficit=1.0, is_obstructed=True
    )
    print(f"\nSeverity of rank-0 bottleneck: {bn_zero.severity():.4f}")

    bn_high = BottleneckCoordinate(
        coord_id="high-coord", rank=10, coverage_count=2,
        coverage_deficit=0.8, is_obstructed=True
    )
    print(f"Severity of high bottleneck: {bn_high.severity():.4f}")

    # BottleneckGeometry with no bottlenecks
    empty_geom = BottleneckGeometry(
        geometry_id="empty",
        bottlenecks=(),
        timestamp=time.time(),
    )
    print(f"Most severe of empty geometry: {empty_geom.most_severe()}")
    print(f"Total severity of empty geometry: {empty_geom.total_severity():.4f}")

    print("\n=== All smoke tests passed ===")
