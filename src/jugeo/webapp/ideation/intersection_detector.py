"""Stage 3C – Intersection Detector for the ideation pipeline.

Standalone module (no jugeo imports, Python stdlib only).

Detects valuable application ideas at coordinate intersections.  The key
insight: most valuable opportunities lie at 3-way and 4-way intersections
where *individual* coordinates are densely covered but their *joint*
intersection is empty – the "genuinely novel" zone.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any

from .models import (
    ApplicationCoordinate,
    AppIdeationPurpose,
    CoverageReport,
    GainProfile,
    IdeaPortfolio,
    IdeaProposal,
    IdeaSource,
)

AC = ApplicationCoordinate

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Base development cost (person-hours) per coordinate.
COORD_BASE_COSTS: dict[ApplicationCoordinate, float] = {
    AC.DATA_INGESTION:          40.0,
    AC.DATA_TRANSFORMATION:     55.0,
    AC.DATA_VISUALIZATION:      65.0,
    AC.DATA_EXPORT:             25.0,
    AC.COMPUTATION_ON_DEMAND:   70.0,
    AC.BATCH_PROCESSING:        50.0,
    AC.COMPARISON:              35.0,
    AC.AGGREGATION:             30.0,
    AC.FORM_WORKFLOW:           45.0,
    AC.FILE_PROCESSING:         40.0,
    AC.REAL_TIME_FEEDBACK:      80.0,
    AC.COLLABORATIVE_EDITING:   95.0,
    AC.SCHEDULING:              55.0,
    AC.INVENTORY:               45.0,
    AC.MATCHING:                60.0,
    AC.SIMULATION:              90.0,
    AC.AUDIT_TRAIL:             35.0,
    AC.CONSTRAINT_SATISFACTION: 85.0,
    AC.STATIC_REPORT:           30.0,
    AC.INTERACTIVE_DASHBOARD:   75.0,
    AC.NOTIFICATION:            25.0,
    AC.API_PROVISION:           50.0,
}

# Short noun for each coordinate, used in title generation.
COORD_NOUNS: dict[ApplicationCoordinate, str] = {
    AC.DATA_INGESTION:          "Collector",
    AC.DATA_TRANSFORMATION:     "Transformer",
    AC.DATA_VISUALIZATION:      "Visualizer",
    AC.DATA_EXPORT:             "Exporter",
    AC.COMPUTATION_ON_DEMAND:   "Calculator",
    AC.BATCH_PROCESSING:        "Processor",
    AC.COMPARISON:              "Comparator",
    AC.AGGREGATION:             "Aggregator",
    AC.FORM_WORKFLOW:           "Workflow",
    AC.FILE_PROCESSING:         "FileHandler",
    AC.REAL_TIME_FEEDBACK:      "LivePreview",
    AC.COLLABORATIVE_EDITING:   "Collaborator",
    AC.SCHEDULING:              "Scheduler",
    AC.INVENTORY:               "Tracker",
    AC.MATCHING:                "Matcher",
    AC.SIMULATION:              "Simulator",
    AC.AUDIT_TRAIL:             "Auditor",
    AC.CONSTRAINT_SATISFACTION: "Optimizer",
    AC.STATIC_REPORT:           "Reporter",
    AC.INTERACTIVE_DASHBOARD:   "Dashboard",
    AC.NOTIFICATION:            "Notifier",
    AC.API_PROVISION:           "APIProvider",
}

# Hand-crafted title overrides for well-known coordinate pairs.
_PAIR_TITLE_OVERRIDES: dict[frozenset[ApplicationCoordinate], str] = {
    frozenset({AC.SCHEDULING, AC.SIMULATION}):              "Schedule Scenario Planner",
    frozenset({AC.DATA_VISUALIZATION, AC.CONSTRAINT_SATISFACTION}): "Constraint Explorer",
    frozenset({AC.REAL_TIME_FEEDBACK, AC.COLLABORATIVE_EDITING}):   "Live Co-Editor",
    frozenset({AC.MATCHING, AC.NOTIFICATION}):              "Smart Match Alerter",
    frozenset({AC.INVENTORY, AC.AUDIT_TRAIL}):              "Asset Audit Tracker",
    frozenset({AC.DATA_INGESTION, AC.DATA_TRANSFORMATION}): "ETL Pipeline Builder",
    frozenset({AC.BATCH_PROCESSING, AC.NOTIFICATION}):      "Batch Monitor",
    frozenset({AC.COMPUTATION_ON_DEMAND, AC.INTERACTIVE_DASHBOARD}): "On-Demand Analytics Hub",
    frozenset({AC.FORM_WORKFLOW, AC.CONSTRAINT_SATISFACTION}):       "Smart Form Validator",
    frozenset({AC.AGGREGATION, AC.STATIC_REPORT}):          "Summary Report Generator",
    frozenset({AC.DATA_EXPORT, AC.API_PROVISION}):          "Data API Gateway",
    frozenset({AC.FILE_PROCESSING, AC.DATA_EXPORT}):        "File Export Pipeline",
    frozenset({AC.SIMULATION, AC.CONSTRAINT_SATISFACTION}):  "Constraint Simulation Lab",
    frozenset({AC.SCHEDULING, AC.NOTIFICATION}):            "Schedule Notifier",
    frozenset({AC.COMPARISON, AC.DATA_VISUALIZATION}):      "Visual Diff Tool",
    frozenset({AC.MATCHING, AC.AGGREGATION}):               "Match Aggregator",
}

_TITLE_ADJECTIVES: list[str] = [
    "Smart", "Unified", "Adaptive", "Integrated", "Dynamic",
    "Auto", "Contextual", "Responsive", "Predictive", "Composable",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _individual_coverage(coverage: CoverageReport, coord: AC) -> float:
    """Return coverage for a single coordinate (1-tuple lookup)."""
    return coverage.coverage_at((coord,))


def _joint_coverage(coverage: CoverageReport, coords: tuple[AC, ...]) -> float:
    """Return joint coverage for a coordinate tuple, canonicalised."""
    canonical = tuple(sorted(coords, key=lambda c: c.value))
    return coverage.coverage_at(canonical)


def _pick_adjective(coords: tuple[AC, ...]) -> str:
    """Deterministically pick an adjective from the coordinate combo."""
    h = sum(hash(c) for c in coords)
    return _TITLE_ADJECTIVES[h % len(_TITLE_ADJECTIVES)]


# ---------------------------------------------------------------------------
# IntersectionDetector
# ---------------------------------------------------------------------------

class IntersectionDetector:
    """Stage 3C: detects ideas at coordinate intersections.

    Key insight: most valuable ideas live at 3-way and 4-way intersections
    where individual coordinates are densely covered but their intersection
    is empty.  This is the "genuinely novel" zone – each piece exists but
    nobody combined them.
    """

    BRIDGE_THRESHOLD: float = 0.15  # joint coverage below this = bridge
    MIN_INDIVIDUAL: float = 0.10    # require at least this individual coverage

    def __init__(
        self,
        bridge_threshold: float | None = None,
        min_individual: float | None = None,
        max_pair_results: int = 30,
        max_triple_results: int = 20,
        max_quad_results: int = 10,
    ) -> None:
        if bridge_threshold is not None:
            self.BRIDGE_THRESHOLD = bridge_threshold
        if min_individual is not None:
            self.MIN_INDIVIDUAL = min_individual
        self._max_pair = max_pair_results
        self._max_triple = max_triple_results
        self._max_quad = max_quad_results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        portfolio: IdeaPortfolio,
        coverage: CoverageReport,
        purpose: AppIdeationPurpose | None = None,
    ) -> list[IdeaProposal]:
        """Main entry point: detect bridge opportunities → IdeaProposals.

        1. Find pair bridges
        2. Find triple bridges
        3. Find quad bridges (extending top triples)
        4. Convert to IdeaProposals
        5. Sort by score descending
        """
        pair_bridges = self._find_bridge_opportunities(coverage)
        triple_bridges = self._find_triple_bridges(coverage)
        quad_bridges = self._find_quad_bridges(coverage, triple_bridges)

        proposals: list[IdeaProposal] = []
        seen: set[frozenset[AC]] = set()

        for coords, score in self._scored_bridges(
            pair_bridges, triple_bridges, quad_bridges, coverage,
        ):
            key = frozenset(coords)
            if key in seen:
                continue
            seen.add(key)
            proposals.append(self._bridge_to_idea(coords, score, purpose))

        proposals.sort(key=lambda p: p.novelty_score, reverse=True)
        return proposals

    # ------------------------------------------------------------------
    # Bridge detection
    # ------------------------------------------------------------------

    def _find_bridge_opportunities(
        self, coverage: CoverageReport,
    ) -> list[tuple[AC, AC]]:
        """Find coordinate pairs where:
        - Individual coverage of each coord > MIN_INDIVIDUAL
        - Joint coverage < BRIDGE_THRESHOLD

        Returns sorted by bridge_score descending.
        """
        eligible = [
            c for c in AC
            if _individual_coverage(coverage, c) >= self.MIN_INDIVIDUAL
        ]
        bridges: list[tuple[tuple[AC, AC], float]] = []
        for a, b in combinations(eligible, 2):
            jc = _joint_coverage(coverage, (a, b))
            if jc < self.BRIDGE_THRESHOLD:
                indiv = {a: _individual_coverage(coverage, a),
                         b: _individual_coverage(coverage, b)}
                bridges.append(((a, b), self._score_bridge((a, b), indiv, jc)))

        bridges.sort(key=lambda x: x[1], reverse=True)
        return [pair for pair, _ in bridges[:self._max_pair]]

    def _find_triple_bridges(
        self, coverage: CoverageReport,
    ) -> list[tuple[AC, AC, AC]]:
        """Find triple intersections where:
        - Each individual coord has coverage > MIN_INDIVIDUAL
        - Pairwise coverage of each pair > 0.1
        - Triple joint coverage < BRIDGE_THRESHOLD / 2

        Returns top triples sorted by opportunity.
        """
        triple_thresh = self.BRIDGE_THRESHOLD / 2.0
        pairwise_floor = 0.10
        eligible = [
            c for c in AC
            if _individual_coverage(coverage, c) >= self.MIN_INDIVIDUAL
        ]
        triples: list[tuple[tuple[AC, AC, AC], float]] = []

        for a, b, c in combinations(eligible, 3):
            if (_joint_coverage(coverage, (a, b)) < pairwise_floor
                    or _joint_coverage(coverage, (a, c)) < pairwise_floor
                    or _joint_coverage(coverage, (b, c)) < pairwise_floor):
                continue
            triple_jc = _joint_coverage(coverage, (a, b, c))
            if triple_jc >= triple_thresh:
                continue
            indiv = {x: _individual_coverage(coverage, x) for x in (a, b, c)}
            triples.append(((a, b, c), self._score_bridge((a, b, c), indiv, triple_jc)))

        triples.sort(key=lambda x: x[1], reverse=True)
        return [t for t, _ in triples[:self._max_triple]]

    def _find_quad_bridges(
        self,
        coverage: CoverageReport,
        top_triples: list[tuple[AC, ...]],
    ) -> list[tuple[AC, ...]]:
        """Extend top triple bridges by a fourth coordinate.

        The quad is kept only if quad-level joint coverage is near zero
        (< BRIDGE_THRESHOLD / 4).
        """
        quad_thresh = self.BRIDGE_THRESHOLD / 4.0
        eligible_set = {
            c for c in AC
            if _individual_coverage(coverage, c) >= self.MIN_INDIVIDUAL
        }
        quads: list[tuple[tuple[AC, ...], float]] = []
        seen: set[frozenset[AC]] = set()

        for triple in top_triples:
            for d in eligible_set - set(triple):
                quad = tuple(sorted((*triple, d), key=lambda c: c.value))
                key = frozenset(quad)
                if key in seen:
                    continue
                seen.add(key)
                quad_jc = _joint_coverage(coverage, quad)
                if quad_jc >= quad_thresh:
                    continue
                indiv = {c: _individual_coverage(coverage, c) for c in quad}
                quads.append((quad, self._score_bridge(quad, indiv, quad_jc)))

        quads.sort(key=lambda x: x[1], reverse=True)
        return [q for q, _ in quads[:self._max_quad]]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _scored_bridges(
        self,
        pairs: list[tuple[AC, ...]],
        triples: list[tuple[AC, ...]],
        quads: list[tuple[AC, ...]],
        coverage: CoverageReport,
    ) -> list[tuple[tuple[AC, ...], float]]:
        """Merge all bridge lists, score, and sort descending."""
        all_bridges: list[tuple[tuple[AC, ...], float]] = []
        for coords in (*pairs, *triples, *quads):
            indiv = {c: _individual_coverage(coverage, c) for c in coords}
            jc = _joint_coverage(coverage, coords)
            score = self._score_bridge(coords, indiv, jc)
            # Arity bonus: triples 1.3×, quads 1.6×
            score *= 1.0 + 0.3 * (len(coords) - 2)
            all_bridges.append((coords, score))
        all_bridges.sort(key=lambda x: x[1], reverse=True)
        return all_bridges

    def _score_bridge(
        self,
        coords: tuple[AC, ...],
        individual_coverages: dict[AC, float],
        joint_coverage: float,
    ) -> float:
        """Score a bridge opportunity.

        bridge_score = (sum of individual coverages) * (1 - joint_coverage)
                       / len(coords)

        Higher individual coverage = more user need exists.
        Lower joint coverage = bigger gap.
        A logarithmic rarity bonus rewards higher-arity intersections.
        """
        if not coords:
            return 0.0
        indiv_sum = sum(individual_coverages.get(c, 0.0) for c in coords)
        gap_factor = 1.0 - min(joint_coverage, 1.0)
        base = (indiv_sum * gap_factor) / len(coords)
        rarity = math.log2(len(coords)) if len(coords) > 1 else 0.0
        return base * (1.0 + 0.1 * rarity)

    # ------------------------------------------------------------------
    # Proposal generation
    # ------------------------------------------------------------------

    def _bridge_to_idea(
        self,
        coords: tuple[AC, ...],
        score: float,
        purpose: AppIdeationPurpose | None = None,
    ) -> IdeaProposal:
        """Convert bridge opportunity to IdeaProposal.

        Title: creative name combining coordinate concepts
        Hypothesis: explain why the combination is novel
        source = IdeaSource.INTERSECTION_DETECTION
        feasibility based on coordinate costs
        novelty = score (normalised)
        gain: theorem_yield = score, bridge_impact = score * 1.2,
              cost = sum of coord costs * 0.7
        """
        title = self._generate_bridge_title(coords)
        hypothesis = self._generate_hypothesis(coords, score, purpose)
        target_area = self._infer_target_area(coords, purpose)
        cost = self._estimate_intersection_cost(coords)
        novelty = min(score / 0.8, 1.0)
        feasibility = self._estimate_feasibility(coords)

        gain = GainProfile(
            theorem_yield=round(score, 4),
            bridge_impact=round(score * 1.2, 4),
            cost=round(cost, 2),
            uncertainty=round(max(0.1, 1.0 - score), 4),
        )
        return IdeaProposal.create(
            title=title,
            hypothesis=hypothesis,
            target_area=target_area,
            coordinates=set(coords),
            gain=gain,
            source=IdeaSource.INTERSECTION_DETECTION,
            feasibility_score=round(feasibility, 3),
            novelty_score=round(novelty, 3),
        )

    def _generate_bridge_title(self, coords: tuple[AC, ...]) -> str:
        """Generate creative title for a bridge intersection.

        Uses hand-crafted overrides for known pairs, otherwise combines
        coordinate nouns with a deterministic adjective.

        Examples:
          SCHEDULING + SIMULATION -> "Schedule Scenario Planner"
          DATA_VISUALIZATION + CONSTRAINT_SATISFACTION -> "Constraint Explorer"
          SCHEDULING + SIMULATION + NOTIFICATION -> "Smart Scheduler Simulator Notifier"
        """
        coord_key = frozenset(coords)
        if len(coords) == 2 and coord_key in _PAIR_TITLE_OVERRIDES:
            return _PAIR_TITLE_OVERRIDES[coord_key]

        nouns = [COORD_NOUNS.get(c, c.value.replace("_", " ").title()) for c in coords]
        adj = _pick_adjective(coords)
        if len(nouns) == 2:
            return f"{adj} {nouns[0]} {nouns[1]}"
        return f"{adj} {' '.join(nouns)}"

    def _generate_hypothesis(
        self,
        coords: tuple[AC, ...],
        score: float,
        purpose: AppIdeationPurpose | None = None,
    ) -> str:
        """Build human-readable hypothesis for why this bridge is valuable."""
        names = [c.value.replace("_", " ").lower() for c in coords]
        if len(names) == 2:
            combo = f"{names[0]} and {names[1]}"
        else:
            combo = ", ".join(names[:-1]) + f", and {names[-1]}"

        hypothesis = (
            f"Combining {combo} addresses an unmet need "
            f"(bridge score {score:.2f}). Individual capabilities each "
            f"have proven user demand, yet no existing product unifies them."
        )
        if purpose is not None:
            hypothesis += (
                f" In the {purpose.domain} domain targeting "
                f"{purpose.user_population}, this intersection is "
                f"especially underserved."
            )
        return hypothesis

    def _infer_target_area(
        self,
        coords: tuple[AC, ...],
        purpose: AppIdeationPurpose | None = None,
    ) -> str:
        """Derive target area label from coordinates and purpose."""
        if purpose is not None:
            return purpose.domain

        data_coords = {AC.DATA_INGESTION, AC.DATA_TRANSFORMATION,
                       AC.DATA_VISUALIZATION, AC.DATA_EXPORT}
        compute_coords = {AC.COMPUTATION_ON_DEMAND, AC.BATCH_PROCESSING,
                          AC.SIMULATION, AC.CONSTRAINT_SATISFACTION}
        collab_coords = {AC.COLLABORATIVE_EDITING, AC.REAL_TIME_FEEDBACK,
                         AC.NOTIFICATION}
        ops_coords = {AC.SCHEDULING, AC.INVENTORY, AC.AUDIT_TRAIL,
                      AC.FORM_WORKFLOW}

        coord_set = set(coords)
        families = [
            ("data-pipeline",     len(coord_set & data_coords)),
            ("compute-analytics", len(coord_set & compute_coords)),
            ("collaboration",     len(coord_set & collab_coords)),
            ("operations",        len(coord_set & ops_coords)),
        ]
        families.sort(key=lambda f: f[1], reverse=True)
        return families[0][0] if families[0][1] > 0 else "general"

    # ------------------------------------------------------------------
    # Cost & feasibility estimation
    # ------------------------------------------------------------------

    def _estimate_intersection_cost(self, coords: tuple[AC, ...]) -> float:
        """Estimate dev cost for building at this intersection.

        Uses base costs with diminishing returns (0.7^i) for each
        additional coordinate.  Adds 15h integration overhead per
        additional coordinate beyond the first.
        """
        if not coords:
            return 0.0
        sorted_by_cost = sorted(
            coords, key=lambda c: COORD_BASE_COSTS.get(c, 50.0), reverse=True,
        )
        total = sum(
            COORD_BASE_COSTS.get(coord, 50.0) * (0.7 ** i)
            for i, coord in enumerate(sorted_by_cost)
        )
        integration = 15.0 * max(0, len(coords) - 1)
        return total + integration

    def _estimate_feasibility(self, coords: tuple[AC, ...]) -> float:
        """Feasibility in [0, 1] via sigmoid on aggregate cost.

        Lower total cost -> higher feasibility.  Sigmoid centred at
        ~130 person-hours (median two-coordinate cost).
        """
        cost = self._estimate_intersection_cost(coords)
        centre, scale = 130.0, 60.0
        feasibility = 1.0 / (1.0 + math.exp((cost - centre) / scale))
        return max(0.05, min(feasibility, 0.99))

    # ------------------------------------------------------------------
    # Convenience / introspection
    # ------------------------------------------------------------------

    def eligible_coordinates(self, coverage: CoverageReport) -> list[AC]:
        """Return coordinates whose individual coverage meets the minimum."""
        return [
            c for c in AC
            if _individual_coverage(coverage, c) >= self.MIN_INDIVIDUAL
        ]

    def bridge_count_summary(self, coverage: CoverageReport) -> dict[str, int]:
        """Quick summary of bridge counts at each arity level."""
        pairs = self._find_bridge_opportunities(coverage)
        triples = self._find_triple_bridges(coverage)
        quads = self._find_quad_bridges(coverage, triples)
        return {"pairs": len(pairs), "triples": len(triples), "quads": len(quads)}
