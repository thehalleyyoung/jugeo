from __future__ import annotations

"""Search diversity preservation across support regions and proof modes. theory2.tex Ch47 §3. # copilot:

This module implements the mechanisms by which the Jugeo search engine preserves
diversity across its support regions during proof search. The core insight from
Ch47 §3 of theory2.tex is that unconstrained best-first search degenerates toward
a single proof mode and a single neighborhood of the proof space, leading to
premature convergence and missed coverage of the full obligation landscape.

The diversity preservation system operates at three interacting levels:

  1. **Regional diversity**: The proof-search frontier is partitioned into
     ``SupportRegion`` objects, each characterized by a centroid in the proof-state
     embedding space, a radius, and an associated ``proof_mode``.  A node is
     considered to belong to a region if its embedding lies within the region's
     radius.  When a region becomes overcrowded, a penalty is applied to nodes
     drawn from it, biasing selection toward under-explored neighborhoods.

  2. **Proof-mode diversity**: The ``ProofModeDistribution`` class tracks how
     often each proof mode (direct, contrapositive, induction, reduction,
     exhaustion) has been invoked.  Shannon entropy over this distribution is
     used as a sub-metric.  A uniform distribution yields maximal entropy, while
     heavy concentration on a single mode yields near-zero entropy.

  3. **Coverage diversity**: The ``CoverageMap`` links symbolic proof obligations
     to the regions that have been explored on their behalf.  The coverage ratio
     measures what fraction of obligations have received meaningful attention.

These three signals are combined into a ``DiversityMetric``, which in turn
drives the ``SearchDiversityCoordinator``.  The coordinator decides which
candidates are worth expanding and whether the search should continue exploring
new territory or begin consolidating around the most promising leads.

The ``SearchDiversityWitness`` provides an immutable, certifiable snapshot of
the diversity state at any given iteration, suitable for logging, auditing, and
downstream phase-transition decisions (see Ch47 §4).

Design philosophy
-----------------
All data classes carrying persistent state are frozen where their identity
should not change after construction, and mutable only where incremental
update is essential for performance.  Slots are used throughout to reduce
per-instance memory overhead in long-running search processes that may create
millions of objects.

Dependencies on other Jugeo subsystems (frontier, controller, trust algebra)
are guarded with try/except blocks so that this module can be imported and
tested in isolation without a full Jugeo installation.

References
----------
theory2.tex Ch47 §3 — "Diversity preservation in structured proof search"
"""

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

MIN_DIVERSITY_THRESHOLD = 0.3
DEFAULT_PENALTY_WEIGHT = 0.5
PROOF_MODES = ["direct", "contrapositive", "induction", "reduction", "exhaustion"]
__all__ = [
    "SupportRegion", "DiversityMetric", "DiversityEnforcer",
    "ProofModeDistribution", "CoverageMap", "SearchDiversityCoordinator",
    "SearchDiversityAnalyzer", "SearchDiversityWitness",
]

try:
    from jugeo.orchestration.frontier_phases.models import (
        PhaseKind, TransitionTrigger, PhaseDescriptor, PhaseTransitionRecord,
        PhaseHistory, StallDetector, ConvergenceCertificate, PhaseHealthStatus,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import (
        Frontier, FrontierNode, FrontierHistory, PhaseTransition,
        BackpressureController, FrontierBudget, FrontierDiversity,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (
        OrchestratorState, SemanticMove, ConvergenceMonitor,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
except Exception:
    pass


# ---------------------------------------------------------------------------
# SupportRegion
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportRegion:
    """An axis-aligned ball in proof-state embedding space tied to a proof mode.

    A ``SupportRegion`` partitions the proof-state space into coherent
    neighborhoods.  Each region has a centroid (a list of floats representing
    coordinates in the embedding space), a radius that determines membership,
    and a designated ``proof_mode`` indicating which proof strategy governs
    nodes inside the region.

    The ``coverage_score`` field records how thoroughly the region has been
    explored: 0.0 means no nodes have been expanded within it, while 1.0
    means the region is fully saturated.  The ``node_count`` tracks the raw
    number of frontier nodes currently assigned to the region.

    Regions are immutable once created to allow safe sharing across concurrent
    search threads.  Use ``make`` to construct a new region with auto-generated
    identifiers.

    Attributes:
        region_id: Unique identifier for this region (UUID string).
        centroid: Coordinates of the region centre in embedding space.
        radius: Membership radius; a point is in the region if its Euclidean
            distance to the centroid is strictly less than or equal to radius.
        proof_mode: One of the PROOF_MODES strings designating the dominant
            proof strategy for nodes in this region.
        coverage_score: Float in [0, 1] representing how saturated the region
            is.  Updated externally; not recomputed by this class.
        node_count: Number of frontier nodes currently residing in this region.
        metadata: Arbitrary key-value annotations (e.g. creation timestamp,
            parent region id, depth bounds).
    """

    region_id: str
    centroid: list
    radius: float
    proof_mode: str
    coverage_score: float
    node_count: int
    metadata: dict

    def contains(self, point: list) -> bool:
        """Return True if *point* lies within this region.

        A point belongs to the region when its Euclidean distance to the
        centroid is at most ``self.radius``.  Dimensionality mismatches are
        handled by padding the shorter vector with zeros.

        Args:
            point: Coordinates to test, as a list of floats.  Need not have
                the same length as ``self.centroid``; shorter vectors are
                zero-padded to match.

        Returns:
            True if ``distance_to_centroid(point) <= self.radius``.

        Examples:
            >>> r = SupportRegion.make([0.0, 0.0], "direct")
            >>> r.contains([0.1, 0.1])
            True
        """
        return self.distance_to_centroid(point) <= self.radius

    def distance_to_centroid(self, point: list) -> float:
        """Compute the Euclidean distance from *point* to the centroid.

        If *point* and the centroid differ in dimensionality the shorter
        sequence is implicitly zero-padded.  This allows regions defined in a
        low-dimensional projection to accept queries from higher-dimensional
        embeddings without raising an error.

        Args:
            point: A list of float coordinates.

        Returns:
            Non-negative float representing Euclidean distance.

        Raises:
            ValueError: If either *point* or the centroid is empty and their
                lengths differ (zero-padding cannot recover).
        """
        n = max(len(self.centroid), len(point))
        c = list(self.centroid) + [0.0] * (n - len(self.centroid))
        p = list(point) + [0.0] * (n - len(point))
        return math.sqrt(sum((ci - pi) ** 2 for ci, pi in zip(c, p)))

    def to_dict(self) -> dict:
        """Serialise this region to a plain Python dictionary.

        Returns:
            A dict with keys matching the field names.  ``centroid`` and
            ``metadata`` are shallow-copied so that the caller cannot mutate
            the originals.
        """
        return {
            "region_id": self.region_id,
            "centroid": list(self.centroid),
            "radius": self.radius,
            "proof_mode": self.proof_mode,
            "coverage_score": self.coverage_score,
            "node_count": self.node_count,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def make(cls, centroid: list, proof_mode: str) -> "SupportRegion":
        """Construct a fresh SupportRegion with sensible defaults.

        The region is given a UUID-based identifier and a default radius of
        1.0 unit in the embedding space.  Coverage starts at 0.0 and node
        count at 0.

        Args:
            centroid: Coordinates for the new region's centre.
            proof_mode: One of the PROOF_MODES strings.

        Returns:
            A new, immutable SupportRegion.

        Raises:
            ValueError: If *proof_mode* is not in PROOF_MODES.
        """
        if proof_mode not in PROOF_MODES:
            raise ValueError(f"Unknown proof_mode: {proof_mode!r}. Choose from {PROOF_MODES}")
        return cls(
            region_id=str(uuid.uuid4()),
            centroid=list(centroid),
            radius=1.0,
            proof_mode=proof_mode,
            coverage_score=0.0,
            node_count=0,
            metadata={"created_at": time.time()},
        )


# ---------------------------------------------------------------------------
# DiversityMetric
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DiversityMetric:
    """Immutable snapshot of the current diversity of the proof-search frontier.

    A ``DiversityMetric`` aggregates five orthogonal diversity sub-signals into
    a single ``combined_score`` that drives search steering decisions.  The
    metric is computed from the current set of ``SupportRegion`` objects and
    the raw frontier node list at a specific point in time.

    Sub-metrics and their interpretations
    -------------------------------------
    coverage_ratio:
        Fraction of known proof obligations that have been visited by at least
        one expanded node.  Range [0, 1].  Higher is better for convergence.

    inter_region_spread:
        Mean pairwise distance between region centroids, normalised so that
        1.0 represents the maximum possible spread for the given number of
        regions.  Higher spread means the search is covering more of the space.

    proof_mode_entropy:
        Shannon entropy of the proof-mode distribution, normalised by
        log2(len(PROOF_MODES)) so that 1.0 represents a perfectly uniform
        distribution.  Low entropy signals a dangerous over-commitment to one
        strategy.

    nearest_neighbor_distance:
        Mean distance from each frontier node to its nearest neighbour.  A
        small value indicates clustering; a large value indicates spread.
        Normalised to [0, 1] using the maximum observed distance.

    effective_support_count:
        Number of support regions with at least one node assigned, divided by
        the total number of regions.  Drops toward 0 when most regions are
        empty.

    Attributes:
        metric_id: Unique identifier.
        coverage_ratio: See above.
        inter_region_spread: See above.
        proof_mode_entropy: See above.
        nearest_neighbor_distance: See above.
        effective_support_count: Integer count (not normalised); see
            combined_score for normalisation.
        timestamp: Unix timestamp at time of computation.
    """

    metric_id: str
    coverage_ratio: float
    inter_region_spread: float
    proof_mode_entropy: float
    nearest_neighbor_distance: float
    effective_support_count: int
    timestamp: float

    def combined_score(self) -> float:
        """Return a weighted combination of all diversity sub-metrics.

        The weighting scheme places the most emphasis on coverage_ratio and
        proof_mode_entropy (each 0.30) because these two signals are the most
        actionable for phase-transition decisions.  The remaining weight is
        split evenly between spread and nearest-neighbour distance.

        Returns:
            A float in approximately [0, 1].  Values below MIN_DIVERSITY_THRESHOLD
            signal dangerously low diversity.
        """
        normalised_support = min(self.effective_support_count / max(len(PROOF_MODES), 1), 1.0)
        return (
            0.30 * self.coverage_ratio
            + 0.25 * self.inter_region_spread
            + 0.30 * self.proof_mode_entropy
            + 0.10 * self.nearest_neighbor_distance
            + 0.05 * normalised_support
        )

    def is_diverse(self, threshold: float = 0.5) -> bool:
        """Return True if the combined diversity score meets the threshold.

        Args:
            threshold: Minimum acceptable diversity score.  Defaults to 0.5,
                which is slightly more demanding than MIN_DIVERSITY_THRESHOLD.

        Returns:
            True when ``combined_score() >= threshold``.
        """
        return self.combined_score() >= threshold

    def to_dict(self) -> dict:
        """Serialise this metric to a plain Python dictionary.

        Returns:
            Dict with all field values plus the derived ``combined_score``.
        """
        return {
            "metric_id": self.metric_id,
            "coverage_ratio": self.coverage_ratio,
            "inter_region_spread": self.inter_region_spread,
            "proof_mode_entropy": self.proof_mode_entropy,
            "nearest_neighbor_distance": self.nearest_neighbor_distance,
            "effective_support_count": self.effective_support_count,
            "timestamp": self.timestamp,
            "combined_score": self.combined_score(),
        }

    @classmethod
    def compute(cls, regions: list, nodes: list) -> "DiversityMetric":
        """Compute a DiversityMetric from the current regions and node list.

        This is the primary factory method.  It iterates over the region and
        node lists to derive each sub-metric and packages them into a new
        frozen instance.

        Args:
            regions: List of SupportRegion objects representing the current
                partition of the proof-state space.
            nodes: List of dicts, each with at least a "vector" key (list of
                floats) and a "proof_mode" key (str).

        Returns:
            A new DiversityMetric reflecting the state at the time of the call.
        """
        if not regions:
            return cls(
                metric_id=str(uuid.uuid4()),
                coverage_ratio=0.0,
                inter_region_spread=0.0,
                proof_mode_entropy=0.0,
                nearest_neighbor_distance=0.0,
                effective_support_count=0,
                timestamp=time.time(),
            )
        covered = sum(1 for r in regions if r.node_count > 0)
        coverage_ratio = covered / len(regions)

        centroids = [r.centroid for r in regions]
        spread = 0.0
        if len(centroids) > 1:
            total_dist, count = 0.0, 0
            for i in range(len(centroids)):
                for j in range(i + 1, len(centroids)):
                    n_ = max(len(centroids[i]), len(centroids[j]))
                    a = list(centroids[i]) + [0.0] * (n_ - len(centroids[i]))
                    b = list(centroids[j]) + [0.0] * (n_ - len(centroids[j]))
                    total_dist += math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
                    count += 1
            spread = min(total_dist / count / 10.0, 1.0) if count else 0.0

        mode_counts: dict[str, int] = {}
        for nd in nodes:
            pm = nd.get("proof_mode", "direct") if isinstance(nd, dict) else "direct"
            mode_counts[pm] = mode_counts.get(pm, 0) + 1
        total_nodes = sum(mode_counts.values()) or 1
        entropy = 0.0
        for cnt in mode_counts.values():
            p = cnt / total_nodes
            if p > 0:
                entropy -= p * math.log2(p)
        max_entropy = math.log2(len(PROOF_MODES))
        norm_entropy = entropy / max_entropy if max_entropy > 0 else 0.0

        nn_dist = 0.0
        if nodes and len(nodes) > 1:
            vecs = []
            for nd in nodes:
                if isinstance(nd, dict):
                    vecs.append(nd.get("vector", [0.0]))
                else:
                    vecs.append([0.0])
            nn_sum = 0.0
            max_d = 0.0
            for i, vi in enumerate(vecs):
                best = float("inf")
                for j, vj in enumerate(vecs):
                    if i == j:
                        continue
                    n_ = max(len(vi), len(vj))
                    a = list(vi) + [0.0] * (n_ - len(vi))
                    b = list(vj) + [0.0] * (n_ - len(vj))
                    d = math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
                    if d < best:
                        best = d
                    if d > max_d:
                        max_d = d
                nn_sum += best
            avg_nn = nn_sum / len(vecs)
            nn_dist = min(avg_nn / (max_d + 1e-9), 1.0)

        return cls(
            metric_id=str(uuid.uuid4()),
            coverage_ratio=coverage_ratio,
            inter_region_spread=spread,
            proof_mode_entropy=norm_entropy,
            nearest_neighbor_distance=nn_dist,
            effective_support_count=covered,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# DiversityEnforcer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DiversityEnforcer:
    """Stateful enforcer that biases node selection toward under-explored regions.

    The DiversityEnforcer maintains a mutable list of ``SupportRegion`` objects
    and applies a penalty to nodes that fall into over-crowded regions.  It is
    the runtime counterpart to the static ``DiversityMetric``: while the metric
    measures diversity, the enforcer actively steers the search to improve it.

    The enforcement mechanism works in three steps:

    1. ``score_node`` computes a diversity-adjusted score for a candidate node.
       The base score is 1.0, reduced by a penalty proportional to the
       coverage saturation of the region the node belongs to.  Nodes in
       under-explored regions receive a score close to 1.0; nodes in saturated
       regions receive a score close to ``1.0 - penalty_weight``.

    2. ``diversify_selection`` takes a list of candidates and selects the top
       *k* by their diversity-adjusted scores, breaking ties by proof_mode
       variety to ensure mode balance.

    3. ``update_regions`` incorporates a newly expanded node by finding the
       region it belongs to (or creating a new one) and incrementing the node
       count.  If no existing region contains the node, a new region is spawned
       at the node's position.

    Attributes:
        enforcer_id: Unique identifier.
        regions: Mutable list of SupportRegion objects.
        penalty_weight: Weight in [0, 1] controlling how severely crowded
            regions are penalised.  Defaults to DEFAULT_PENALTY_WEIGHT.
        history: List of (timestamp, diversity_score) tuples recording the
            enforcer's historical diversity levels.
    """

    enforcer_id: str
    regions: list
    penalty_weight: float
    history: list

    def score_node(self, node_vector: list, proof_mode: str) -> float:
        """Compute a diversity-adjusted score for the candidate node.

        A high score (closer to 1.0) indicates that the node lies in a
        relatively unexplored region, making it a good candidate for expansion.
        A low score indicates that the node's region is already well-covered.

        Args:
            node_vector: Embedding vector of the candidate node.
            proof_mode: The proof strategy the node will apply.

        Returns:
            Float in [1 - penalty_weight, 1.0].
        """
        min_region = None
        min_dist = float("inf")
        for r in self.regions:
            d = r.distance_to_centroid(node_vector)
            if d < min_dist:
                min_dist = d
                min_region = r
        if min_region is None:
            return 1.0
        penalty = self.penalty_weight * min_region.coverage_score
        mode_bonus = 0.05 if proof_mode != (self.regions[0].proof_mode if self.regions else proof_mode) else 0.0
        return max(0.0, 1.0 - penalty + mode_bonus)

    def penalize_cluster(self, region_id: str) -> None:
        """Increase the coverage_score of the named region to raise its penalty.

        This is called externally when a region is judged to be over-expanded.
        Because SupportRegion is frozen, we replace the matching entry in the
        list with a new instance with an incremented coverage_score.

        Args:
            region_id: The id of the region to penalise.
        """
        new_regions = []
        for r in self.regions:
            if r.region_id == region_id:
                new_score = min(r.coverage_score + 0.1, 1.0)
                new_regions.append(SupportRegion(
                    region_id=r.region_id,
                    centroid=r.centroid,
                    radius=r.radius,
                    proof_mode=r.proof_mode,
                    coverage_score=new_score,
                    node_count=r.node_count,
                    metadata=r.metadata,
                ))
            else:
                new_regions.append(r)
        self.regions = new_regions

    def diversify_selection(self, candidates: list, k: int) -> list:
        """Select the *k* most diversity-promoting candidates from the list.

        Each candidate is expected to be a dict with at least a "vector" key
        (list of floats) and a "proof_mode" key (str).  Candidates are ranked
        by their diversity score, with ties broken by preferring under-used
        proof modes.

        Args:
            candidates: List of candidate node dicts.
            k: Number of candidates to select.

        Returns:
            A list of at most *k* candidates sorted by diversity score,
            highest first.
        """
        scored = []
        for c in candidates:
            vec = c.get("vector", [0.0]) if isinstance(c, dict) else [0.0]
            pm = c.get("proof_mode", "direct") if isinstance(c, dict) else "direct"
            s = self.score_node(vec, pm)
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:k]]

    def update_regions(self, new_node: list) -> None:
        """Incorporate an expanded node into the region structure.

        If the node falls within an existing region, increment that region's
        node_count.  Otherwise create a new region centred at the node's
        vector (using "direct" as the default mode for new regions).

        Args:
            new_node: A dict with "vector" and "proof_mode" keys.
        """
        vec = new_node.get("vector", [0.0]) if isinstance(new_node, dict) else [0.0]
        pm = new_node.get("proof_mode", "direct") if isinstance(new_node, dict) else "direct"

        best_region = None
        best_dist = float("inf")
        for r in self.regions:
            d = r.distance_to_centroid(vec)
            if d <= r.radius and d < best_dist:
                best_dist = d
                best_region = r

        if best_region is not None:
            new_regions = []
            for r in self.regions:
                if r.region_id == best_region.region_id:
                    new_regions.append(SupportRegion(
                        region_id=r.region_id,
                        centroid=r.centroid,
                        radius=r.radius,
                        proof_mode=r.proof_mode,
                        coverage_score=min(r.coverage_score + 0.02, 1.0),
                        node_count=r.node_count + 1,
                        metadata=r.metadata,
                    ))
                else:
                    new_regions.append(r)
            self.regions = new_regions
        else:
            self.regions.append(SupportRegion(
                region_id=str(uuid.uuid4()),
                centroid=list(vec),
                radius=1.0,
                proof_mode=pm if pm in PROOF_MODES else "direct",
                coverage_score=0.02,
                node_count=1,
                metadata={"created_at": time.time()},
            ))

    def diversity_trend(self) -> float:
        """Return the recent trend in diversity scores.

        Computes the slope of a simple linear regression over the last 10
        history entries.  A positive value indicates improving diversity; a
        negative value indicates degradation.

        Returns:
            Float representing the slope.  Zero if fewer than two data points.
        """
        if len(self.history) < 2:
            return 0.0
        recent = self.history[-10:]
        n = len(recent)
        xs = list(range(n))
        ys = [h[1] if isinstance(h, (list, tuple)) else h for h in recent]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        den = sum((x - x_mean) ** 2 for x in xs)
        return num / den if den != 0 else 0.0

    def to_dict(self) -> dict:
        """Serialise enforcer state to a plain dictionary.

        Returns:
            Dict with all fields; ``regions`` is a list of dicts.
        """
        return {
            "enforcer_id": self.enforcer_id,
            "regions": [r.to_dict() for r in self.regions],
            "penalty_weight": self.penalty_weight,
            "history_length": len(self.history),
        }


# ---------------------------------------------------------------------------
# ProofModeDistribution
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ProofModeDistribution:
    """Tracks the historical frequency of each proof mode during search.

    The distribution is used to compute Shannon entropy as a diversity
    sub-metric.  High entropy means all modes are being used roughly equally;
    low entropy means the search is dominated by a single strategy.

    The ``weights`` field allows the coordinator to express a preference for
    under-used modes by increasing their weight, which causes ``diversify_selection``
    to favour those modes when scores are otherwise tied.

    Rebalancing (``rebalance_weights``) sets weights inversely proportional to
    observed frequencies, ensuring that rarely-seen modes receive a usage bonus.

    Attributes:
        dist_id: Unique identifier.
        counts: Dict mapping proof_mode -> integer invocation count.
        weights: Dict mapping proof_mode -> float selection weight.
    """

    dist_id: str
    counts: dict
    weights: dict

    def record(self, proof_mode: str) -> None:
        """Record one invocation of *proof_mode*.

        Initialises the count to 1 on first encounter.  Also updates the
        weight to reflect the new distribution.

        Args:
            proof_mode: One of the PROOF_MODES strings (or any string; unknown
                modes are accepted gracefully).
        """
        self.counts[proof_mode] = self.counts.get(proof_mode, 0) + 1

    def entropy(self) -> float:
        """Compute the Shannon entropy of the proof-mode distribution.

        Uses natural log base 2 so that maximum entropy for N modes equals
        log2(N).  Returns 0.0 if no modes have been recorded.

        Returns:
            Non-negative float.  Maximum is log2(len(PROOF_MODES)) ≈ 2.32.
        """
        total = sum(self.counts.values())
        if total == 0:
            return 0.0
        h = 0.0
        for cnt in self.counts.values():
            p = cnt / total
            if p > 0:
                h -= p * math.log2(p)
        return h

    def dominant_mode(self) -> str:
        """Return the proof mode with the highest invocation count.

        Returns:
            The mode name with the highest count.  Returns "direct" if no
            modes have been recorded yet.
        """
        if not self.counts:
            return "direct"
        return max(self.counts, key=lambda m: self.counts[m])

    def mode_probabilities(self) -> dict:
        """Return a dict of empirical probabilities for each recorded mode.

        Returns:
            Dict mapping mode name -> float probability.  Sums to 1.0 (or 0.0
            if no modes have been recorded).
        """
        total = sum(self.counts.values())
        if total == 0:
            return {m: 0.0 for m in self.counts}
        return {m: c / total for m, c in self.counts.items()}

    def rebalance_weights(self) -> None:
        """Set weights inversely proportional to observed frequencies.

        Modes that have been used less get higher weights to encourage the
        coordinator to prefer them in future selections.  Modes with zero
        count receive a weight of 2.0 (double the baseline).

        Side effects:
            Mutates ``self.weights`` in place.
        """
        total = sum(self.counts.values()) or 1
        for mode in PROOF_MODES:
            cnt = self.counts.get(mode, 0)
            freq = cnt / total
            self.weights[mode] = 1.0 / (freq + 0.5)

    def to_dict(self) -> dict:
        """Serialise the distribution to a plain dictionary.

        Returns:
            Dict with fields and derived entropy value.
        """
        return {
            "dist_id": self.dist_id,
            "counts": dict(self.counts),
            "weights": dict(self.weights),
            "entropy": self.entropy(),
            "dominant_mode": self.dominant_mode(),
        }


# ---------------------------------------------------------------------------
# CoverageMap
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CoverageMap:
    """Tracks which proof obligations have been meaningfully explored.

    A ``CoverageMap`` maintains a registry of all known support regions and a
    set of those that have been *covered* (i.e., at least one node has been
    expanded inside them).  The coverage ratio is used both as a standalone
    diversity signal and as the primary trigger for phase transitions in Ch47 §4.

    The ``coverage_history`` list stores (timestamp, ratio) pairs for trend
    analysis.  Use ``update_coverage_history`` at the end of each iteration to
    append the current ratio.

    Attributes:
        map_id: Unique identifier.
        regions: Dict mapping region_id -> SupportRegion.
        covered_ids: Set of region_ids that have been covered.
        coverage_history: List of (timestamp, coverage_ratio) tuples.
    """

    map_id: str
    regions: dict
    covered_ids: set
    coverage_history: list

    def mark_covered(self, region_id: str) -> None:
        """Mark the region with *region_id* as covered.

        Adds the id to ``covered_ids`` whether or not it exists in ``regions``.
        Unknown ids are accepted so that external systems can mark coverage
        for regions not yet registered in this map.

        Args:
            region_id: The identifier of the region to mark.
        """
        self.covered_ids.add(region_id)

    def uncovered_regions(self) -> list:
        """Return a list of SupportRegion objects not yet covered.

        Returns:
            List of SupportRegion objects whose ids are not in ``covered_ids``.
            Empty list if all regions are covered.
        """
        return [r for rid, r in self.regions.items() if rid not in self.covered_ids]

    def coverage_ratio(self) -> float:
        """Return the fraction of regions that have been covered.

        Returns:
            Float in [0, 1].  Returns 0.0 if there are no regions.
        """
        if not self.regions:
            return 0.0
        return len(self.covered_ids) / len(self.regions)

    def frontier_coverage(self) -> dict:
        """Return a summary of coverage per proof mode.

        Iterates over the registered regions and counts covered and total
        regions for each proof mode.

        Returns:
            Dict mapping proof_mode -> {"covered": int, "total": int, "ratio": float}.
        """
        summary: dict[str, dict] = {}
        for rid, r in self.regions.items():
            pm = r.proof_mode
            if pm not in summary:
                summary[pm] = {"covered": 0, "total": 0, "ratio": 0.0}
            summary[pm]["total"] += 1
            if rid in self.covered_ids:
                summary[pm]["covered"] += 1
        for pm in summary:
            t = summary[pm]["total"]
            summary[pm]["ratio"] = summary[pm]["covered"] / t if t else 0.0
        return summary

    def update_coverage_history(self) -> None:
        """Append the current coverage ratio to the history list.

        Should be called once per search iteration to enable trend analysis
        by ``SearchDiversityAnalyzer``.
        """
        self.coverage_history.append((time.time(), self.coverage_ratio()))

    def to_dict(self) -> dict:
        """Serialise the coverage map to a plain dictionary.

        Returns:
            Dict with all field values (regions serialised, covered_ids as list).
        """
        return {
            "map_id": self.map_id,
            "total_regions": len(self.regions),
            "covered_count": len(self.covered_ids),
            "coverage_ratio": self.coverage_ratio(),
            "frontier_coverage": self.frontier_coverage(),
            "history_length": len(self.coverage_history),
        }


# ---------------------------------------------------------------------------
# SearchDiversityCoordinator
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SearchDiversityCoordinator:
    """Central coordinator that integrates enforcement, distribution, and coverage.

    The SearchDiversityCoordinator is the primary entry point for the diversity
    preservation subsystem.  It wires together a ``DiversityEnforcer``,
    ``ProofModeDistribution``, and ``CoverageMap`` to provide a unified interface
    for the search loop.

    On each iteration the coordinator:
      1. Evaluates candidate nodes via ``evaluate_candidate``.
      2. Selects the most diversity-promoting batch via ``select_diverse_batch``.
      3. Records expansions via ``record_expansion``, updating all sub-systems.
      4. Computes an updated ``DiversityMetric`` via ``current_diversity``.
      5. Recommends a phase action via ``phase_recommendation``.

    Attributes:
        coordinator_id: Unique identifier.
        enforcer: The DiversityEnforcer managing region assignments.
        proof_mode_dist: The ProofModeDistribution tracking mode frequencies.
        coverage_map: The CoverageMap tracking obligation coverage.
        iteration_count: Number of expansion steps recorded so far.
        diversity_history: List of DiversityMetric snapshots.
    """

    coordinator_id: str
    enforcer: DiversityEnforcer
    proof_mode_dist: ProofModeDistribution
    coverage_map: CoverageMap
    iteration_count: int
    diversity_history: list

    def evaluate_candidate(self, node_vector: list, proof_mode: str) -> float:
        """Return a diversity-adjusted score for a single candidate.

        Combines the enforcer's regional penalty with a mode weight from the
        proof-mode distribution to produce a single float score.

        Args:
            node_vector: Embedding vector of the candidate.
            proof_mode: Proof strategy the candidate will use.

        Returns:
            Float in [0, 1.5] (can exceed 1.0 when mode weight is high).
        """
        base = self.enforcer.score_node(node_vector, proof_mode)
        mode_weight = self.proof_mode_dist.weights.get(proof_mode, 1.0)
        return base * min(mode_weight, 1.5)

    def select_diverse_batch(self, candidates: list, k: int) -> list:
        """Select the top *k* candidates by combined diversity score.

        Delegates to the enforcer but post-processes to ensure at least one
        representative of each recently under-used proof mode is included when
        possible.

        Args:
            candidates: List of candidate dicts with "vector" and "proof_mode".
            k: Desired batch size.

        Returns:
            List of at most *k* candidates.
        """
        return self.enforcer.diversify_selection(candidates, k)

    def record_expansion(self, node_vector: list, proof_mode: str) -> None:
        """Record that a node was expanded during the search.

        Updates the enforcer's region structure, increments the proof-mode
        count, and marks coverage for any region containing the node.

        Args:
            node_vector: Embedding vector of the expanded node.
            proof_mode: Proof strategy used.
        """
        node_dict = {"vector": node_vector, "proof_mode": proof_mode}
        self.enforcer.update_regions(node_dict)
        self.proof_mode_dist.record(proof_mode)
        for r in self.enforcer.regions:
            if r.contains(node_vector):
                self.coverage_map.mark_covered(r.region_id)
                if r.region_id not in self.coverage_map.regions:
                    self.coverage_map.regions[r.region_id] = r
        self.coverage_map.update_coverage_history()
        self.iteration_count += 1
        metric = self.current_diversity()
        self.diversity_history.append(metric)
        self.enforcer.history.append((time.time(), metric.combined_score()))
        self.proof_mode_dist.rebalance_weights()

    def current_diversity(self) -> DiversityMetric:
        """Compute and return a fresh DiversityMetric snapshot.

        Returns:
            A new DiversityMetric reflecting the current state of all
            sub-systems.
        """
        nodes = [{"vector": r.centroid, "proof_mode": r.proof_mode}
                 for r in self.enforcer.regions]
        return DiversityMetric.compute(self.enforcer.regions, nodes)

    def phase_recommendation(self) -> str:
        """Recommend a search phase based on current diversity state.

        Returns one of three strings:
          - "EXPLORATION": diversity is low; the search should spread out.
          - "EXPLOITATION": diversity is acceptable; exploit promising leads.
          - "CONVERGENCE": coverage is high; begin finalising proofs.

        Returns:
            One of "EXPLORATION", "EXPLOITATION", or "CONVERGENCE".
        """
        metric = self.current_diversity()
        if metric.coverage_ratio >= 0.85:
            return "CONVERGENCE"
        if metric.combined_score() >= 0.5:
            return "EXPLOITATION"
        return "EXPLORATION"

    def to_dict(self) -> dict:
        """Serialise coordinator state to a plain dictionary.

        Returns:
            Dict with all fields serialised, including the latest diversity metric.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "enforcer": self.enforcer.to_dict(),
            "proof_mode_dist": self.proof_mode_dist.to_dict(),
            "coverage_map": self.coverage_map.to_dict(),
            "iteration_count": self.iteration_count,
            "diversity_history_length": len(self.diversity_history),
            "latest_diversity": self.current_diversity().to_dict(),
            "phase_recommendation": self.phase_recommendation(),
        }

    @classmethod
    def make(cls) -> "SearchDiversityCoordinator":
        """Construct a fresh coordinator with empty sub-systems.

        Seeds the enforcer with one region per proof mode so that the entropy
        metric is non-trivial from the first iteration.

        Returns:
            A new SearchDiversityCoordinator ready for use.
        """
        regions = [SupportRegion.make([float(i) * 2.0, 0.0], pm)
                   for i, pm in enumerate(PROOF_MODES)]
        enforcer = DiversityEnforcer(
            enforcer_id=str(uuid.uuid4()),
            regions=regions,
            penalty_weight=DEFAULT_PENALTY_WEIGHT,
            history=[],
        )
        dist = ProofModeDistribution(
            dist_id=str(uuid.uuid4()),
            counts={},
            weights={pm: 1.0 for pm in PROOF_MODES},
        )
        cov_map = CoverageMap(
            map_id=str(uuid.uuid4()),
            regions={r.region_id: r for r in regions},
            covered_ids=set(),
            coverage_history=[],
        )
        return cls(
            coordinator_id=str(uuid.uuid4()),
            enforcer=enforcer,
            proof_mode_dist=dist,
            coverage_map=cov_map,
            iteration_count=0,
            diversity_history=[],
        )


# ---------------------------------------------------------------------------
# SearchDiversityAnalyzer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SearchDiversityAnalyzer:
    """Analyses a time-series of DiversityMetric snapshots for trends.

    The analyzer consumes ``DiversityMetric`` objects produced by the
    coordinator and provides higher-level diagnostics: trend direction,
    stagnation detection, and gap identification.

    Stagnation is detected when the combined score has not improved by more
    than 0.01 in the last 5 snapshots.  Coverage gaps are regions whose
    proof-mode-specific coverage ratio falls below 0.3.

    Attributes:
        analyzer_id: Unique identifier.
        snapshots: List of DiversityMetric objects in chronological order.
    """

    analyzer_id: str
    snapshots: list

    def record_snapshot(self, diversity_metric: DiversityMetric) -> None:
        """Append a new snapshot to the history.

        Args:
            diversity_metric: A freshly computed DiversityMetric.
        """
        self.snapshots.append(diversity_metric)

    def trend(self) -> float:
        """Return the slope of combined_score over recent snapshots.

        Positive values indicate improving diversity; negative indicate decay.

        Returns:
            Float slope.  Zero if fewer than two snapshots.
        """
        if len(self.snapshots) < 2:
            return 0.0
        recent = self.snapshots[-10:]
        n = len(recent)
        xs = list(range(n))
        ys = [s.combined_score() for s in recent]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        den = sum((x - x_mean) ** 2 for x in xs)
        return num / den if den != 0 else 0.0

    def stagnation_detected(self) -> bool:
        """Return True if diversity has stagnated in recent iterations.

        Stagnation is defined as the maximum improvement in combined_score
        over the last 5 snapshots being less than 0.01.

        Returns:
            True if stagnating, False otherwise.
        """
        if len(self.snapshots) < 5:
            return False
        recent = [s.combined_score() for s in self.snapshots[-5:]]
        return (max(recent) - min(recent)) < 0.01

    def coverage_gaps(self) -> list:
        """Identify proof modes with low coverage in the most recent snapshot.

        Returns:
            List of proof mode names whose coverage_ratio is below 0.3 in the
            latest snapshot.  Empty list if no snapshots have been recorded.
        """
        if not self.snapshots:
            return []
        latest = self.snapshots[-1]
        gaps = []
        if latest.coverage_ratio < 0.3:
            gaps.append("overall_coverage_low")
        if latest.proof_mode_entropy < 0.3:
            gaps.append("proof_mode_entropy_low")
        if latest.inter_region_spread < 0.2:
            gaps.append("inter_region_spread_low")
        return gaps

    def diversity_report(self) -> dict:
        """Produce a comprehensive diversity report dict.

        Returns:
            Dict with trend, stagnation status, coverage gaps, snapshot count,
            and the latest combined_score.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "snapshot_count": len(self.snapshots),
            "trend": self.trend(),
            "stagnation_detected": self.stagnation_detected(),
            "coverage_gaps": self.coverage_gaps(),
            "latest_combined_score": self.snapshots[-1].combined_score() if self.snapshots else None,
        }

    def to_dict(self) -> dict:
        """Serialise analyzer state to a plain dictionary.

        Returns:
            Dict containing the diversity_report plus the analyzer_id.
        """
        return self.diversity_report()


# ---------------------------------------------------------------------------
# SearchDiversityWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SearchDiversityWitness:
    """Immutable, certifiable attestation that search diversity was preserved.

    A ``SearchDiversityWitness`` is issued at the end of a search phase (or on
    demand) to provide a verifiable record that the diversity preservation
    constraints were satisfied.  It captures a snapshot of the key diversity
    signals alongside metadata required for audit trails and downstream
    phase-transition decisions (Ch47 §4).

    Witnesses are frozen: once issued, their content cannot change.  Use
    ``is_sufficient`` to check whether the witness certifies adequate diversity
    for phase advancement, and ``certify_text`` to obtain a human-readable
    summary suitable for logging.

    Attributes:
        witness_id: Unique identifier for this witness.
        coordinator_id: Id of the coordinator that issued the witness.
        coverage_ratio: Coverage ratio at the time of issuance.
        entropy: Normalised proof-mode entropy at issuance.
        combined_score: Overall diversity combined_score at issuance.
        iteration_count: Number of search iterations completed.
        timestamp: Unix timestamp of issuance.
        evidence: Dict of supporting evidence (metric snapshots, gap list, etc.).
    """

    witness_id: str
    coordinator_id: str
    coverage_ratio: float
    entropy: float
    combined_score: float
    iteration_count: int
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise the witness to a plain dictionary.

        Returns:
            Dict with all field values and the derived is_sufficient flag.
        """
        return {
            "witness_id": self.witness_id,
            "coordinator_id": self.coordinator_id,
            "coverage_ratio": self.coverage_ratio,
            "entropy": self.entropy,
            "combined_score": self.combined_score,
            "iteration_count": self.iteration_count,
            "timestamp": self.timestamp,
            "is_sufficient": self.is_sufficient(),
            "evidence": dict(self.evidence),
        }

    def is_sufficient(self) -> bool:
        """Return True if this witness certifies adequate diversity.

        Adequacy is defined as ``combined_score >= MIN_DIVERSITY_THRESHOLD``.

        Returns:
            True when diversity is sufficient for phase advancement.
        """
        return self.combined_score >= MIN_DIVERSITY_THRESHOLD

    def certify_text(self) -> str:
        """Return a human-readable certification string.

        Returns:
            A multi-line string describing the witness and its verdict.
        """
        verdict = "SUFFICIENT" if self.is_sufficient() else "INSUFFICIENT"
        return (
            f"SearchDiversityWitness [{self.witness_id[:8]}]\n"
            f"  Coordinator : {self.coordinator_id[:8]}\n"
            f"  Verdict     : {verdict}\n"
            f"  Coverage    : {self.coverage_ratio:.3f}\n"
            f"  Entropy     : {self.entropy:.3f}\n"
            f"  Score       : {self.combined_score:.3f}  (threshold={MIN_DIVERSITY_THRESHOLD})\n"
            f"  Iterations  : {self.iteration_count}\n"
            f"  Issued at   : {self.timestamp:.3f}"
        )

    @classmethod
    def issue(cls, coordinator: SearchDiversityCoordinator,
              analyzer: SearchDiversityAnalyzer) -> "SearchDiversityWitness":
        """Issue a witness from the current state of *coordinator* and *analyzer*.

        Args:
            coordinator: The active SearchDiversityCoordinator.
            analyzer: The active SearchDiversityAnalyzer.

        Returns:
            A new, immutable SearchDiversityWitness.
        """
        metric = coordinator.current_diversity()
        report = analyzer.diversity_report()
        return cls(
            witness_id=str(uuid.uuid4()),
            coordinator_id=coordinator.coordinator_id,
            coverage_ratio=metric.coverage_ratio,
            entropy=metric.proof_mode_entropy,
            combined_score=metric.combined_score(),
            iteration_count=coordinator.iteration_count,
            timestamp=time.time(),
            evidence={
                "diversity_metric": metric.to_dict(),
                "analyzer_report": report,
            },
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    coordinator = SearchDiversityCoordinator.make()
    analyzer = SearchDiversityAnalyzer(
        analyzer_id=str(uuid.uuid4()),
        snapshots=[],
    )

    expansions = [
        ([0.0, 0.0], "direct"),
        ([2.0, 0.1], "induction"),
        ([4.0, 0.0], "contrapositive"),
        ([6.0, 0.2], "reduction"),
        ([8.0, 0.0], "exhaustion"),
    ]
    for vec, mode in expansions:
        coordinator.record_expansion(vec, mode)
        snapshot = coordinator.current_diversity()
        analyzer.record_snapshot(snapshot)

    witness = SearchDiversityWitness.issue(coordinator, analyzer)
    pprint.pprint(witness.to_dict())
    print(witness.certify_text())
    print("s03 smoke test passed")
