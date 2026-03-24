"""Stage 2 – Application-space coverage estimation.

Standalone module (no jugeo imports, Python stdlib only).
Provides :class:`AppCoverageEstimator` for computing a full
:class:`CoverageReport` and :class:`GapDetector` for multi-granularity
gap detection across the 22-coordinate application space defined in §5.2.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations
from typing import Any

from .models import (
    ApplicationCoordinate,
    AppIdeationPurpose,
    CoverageReport,
    ExistingApp,
    Gap,
    GapType,
    IdeaPortfolio,
)

# ---------------------------------------------------------------------------
# Coordinate-to-need-category mapping
# ---------------------------------------------------------------------------

_NEED_CATEGORIES: dict[str, frozenset[ApplicationCoordinate]] = {
    "data_handling": frozenset({
        ApplicationCoordinate.DATA_INGESTION,
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.FILE_PROCESSING,
    }),
    "computation": frozenset({
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.SIMULATION,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
    }),
    "interaction": frozenset({
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.MATCHING,
    }),
    "domain_specific": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.INVENTORY,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.COMPARISON,
    }),
    "output": frozenset({
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.STATIC_REPORT,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.API_PROVISION,
    }),
}

_COORD_TO_CATEGORY: dict[ApplicationCoordinate, str] = {}
for _cat, _coords in _NEED_CATEGORIES.items():
    for _c in _coords:
        _COORD_TO_CATEGORY[_c] = _cat

_ALL_COORDINATES: list[ApplicationCoordinate] = list(ApplicationCoordinate)

_QUALITY_TIERS = ("high", "medium", "low")

# Domain-keyword hints used by GapDetector.rank_gaps to estimate relevance.
_DOMAIN_KEYWORD_MAP: dict[str, frozenset[ApplicationCoordinate]] = {
    "legal": frozenset({
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
        ApplicationCoordinate.STATIC_REPORT,
        ApplicationCoordinate.DATA_EXPORT,
    }),
    "finance": frozenset({
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.STATIC_REPORT,
    }),
    "health": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.INVENTORY,
    }),
    "education": frozenset({
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.MATCHING,
    }),
    "logistics": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.INVENTORY,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
        ApplicationCoordinate.DATA_INGESTION,
    }),
    "retail": frozenset({
        ApplicationCoordinate.INVENTORY,
        ApplicationCoordinate.MATCHING,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.COMPARISON,
        ApplicationCoordinate.API_PROVISION,
    }),
    "engineering": frozenset({
        ApplicationCoordinate.SIMULATION,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
    }),
    "media": frozenset({
        ApplicationCoordinate.FILE_PROCESSING,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.API_PROVISION,
    }),
}


# ===================================================================
# Helpers
# ===================================================================


def _safe_div(numerator: float, denominator: float) -> float:
    """Division that returns 0.0 when *denominator* is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _apps_covering(apps: list[ExistingApp], coord: ApplicationCoordinate) -> list[ExistingApp]:
    """Return apps whose coordinate set includes *coord*."""
    return [a for a in apps if coord in a.coordinates]


def _apps_covering_all(
    apps: list[ExistingApp],
    coords: tuple[ApplicationCoordinate, ...],
) -> list[ExistingApp]:
    """Return apps whose coordinate set includes **all** of *coords*."""
    coord_set = set(coords)
    return [a for a in apps if coord_set.issubset(a.coordinates)]


def _classify_gap(coverage: float) -> GapType:
    """Derive a :class:`GapType` from a raw coverage value."""
    if coverage == 0.0:
        return GapType.UNSERVED
    if coverage < 0.15:
        return GapType.UNDERSERVED
    return GapType.WRONG_METHOD


def _describe_gap(
    coords: tuple[ApplicationCoordinate, ...],
    coverage: float,
    gap_type: GapType,
) -> str:
    """Build a human-readable gap description."""
    names = ", ".join(c.value for c in coords)
    pct = f"{coverage * 100:.1f}%"
    if gap_type is GapType.UNSERVED:
        return f"No apps cover [{names}]. Completely unserved."
    if gap_type is GapType.UNDERSERVED:
        return f"Only {pct} coverage at [{names}]. Significantly underserved."
    return (
        f"Coverage at [{names}] is {pct} but existing apps may use the "
        f"wrong approach. Opportunity for a better method."
    )


def _domain_coords(purpose: AppIdeationPurpose) -> frozenset[ApplicationCoordinate]:
    """Resolve the set of coordinates most relevant to *purpose.domain*."""
    domain_lower = purpose.domain.lower()
    relevant: set[ApplicationCoordinate] = set()
    for keyword, coords in _DOMAIN_KEYWORD_MAP.items():
        if keyword in domain_lower:
            relevant |= coords
    if not relevant:
        # Fallback: all coordinates are equally relevant.
        return frozenset(_ALL_COORDINATES)
    return frozenset(relevant)


# ===================================================================
# AppCoverageEstimator
# ===================================================================


class AppCoverageEstimator:
    """Stage 2: Estimates coverage of the application coordinate space.

    Call :meth:`estimate` with an :class:`IdeaPortfolio` to receive a
    :class:`CoverageReport` containing coordinate, need, and quality
    density maps together with a list of detected :class:`Gap` objects.
    """

    COVERAGE_THRESHOLD = 0.3  # below this is a gap

    # ------------------------------------------------------------------ public

    def estimate(self, portfolio: IdeaPortfolio) -> CoverageReport:
        """Compute full coverage report for a portfolio.

        Returns
        -------
        CoverageReport
            ``coordinate_coverage`` maps ``tuple[ApplicationCoordinate]``
            to a float in [0, 1].  ``need_coverage`` maps need-category
            labels to floats.  ``quality_coverage`` maps quality-tier
            labels to floats.  ``gaps`` is sorted ascending by coverage.
        """
        coord_density = self._compute_coordinate_density(portfolio)
        need_density = self._compute_need_density(portfolio)
        quality_density = self._compute_quality_density(portfolio)
        gaps = self._detect_gaps(coord_density)

        return CoverageReport(
            coordinate_coverage=coord_density,
            need_coverage=need_density,
            quality_coverage=quality_density,
            gaps=gaps,
        )

    # --------------------------------------------------------------- internal

    def _compute_coordinate_density(
        self,
        portfolio: IdeaPortfolio,
    ) -> dict[tuple[ApplicationCoordinate, ...], float]:
        """Compute coverage for single coordinates and pairs.

        For each :class:`ApplicationCoordinate` *c*::

            density[(c,)] = |{app : c in app.coordinates}| / max(|apps|, 1)

        For each ordered pair *(c1, c2)* with ``c1 < c2``::

            density[(c1, c2)] = |{app : {c1, c2} ⊆ app.coordinates}| / max(|apps|, 1)
        """
        apps = portfolio.ideas
        n = max(len(apps), 1)
        density: dict[tuple[ApplicationCoordinate, ...], float] = {}

        # Singles
        for coord in _ALL_COORDINATES:
            count = sum(1 for a in apps if coord in a.coordinates)
            density[(coord,)] = count / n

        # Pairs – iterate over the 231 unique pairs.
        for c1, c2 in combinations(_ALL_COORDINATES, 2):
            pair = frozenset({c1, c2})
            count = sum(1 for a in apps if pair.issubset(a.coordinates))
            density[(c1, c2)] = count / n

        return density

    def _compute_need_density(
        self,
        portfolio: IdeaPortfolio,
    ) -> dict[str, float]:
        """Coverage by user-need category.

        For each of the five categories, compute the fraction of the
        category's coordinates that appear in *at least one* app.
        """
        apps = portfolio.ideas
        covered_coords: set[ApplicationCoordinate] = set()
        for app in apps:
            covered_coords |= app.coordinates

        need_density: dict[str, float] = {}
        for category, cat_coords in _NEED_CATEGORIES.items():
            if not cat_coords:
                need_density[category] = 0.0
                continue
            covered_count = len(cat_coords & covered_coords)
            need_density[category] = covered_count / len(cat_coords)

        return need_density

    def _compute_quality_density(
        self,
        portfolio: IdeaPortfolio,
    ) -> dict[str, float]:
        """Coverage by quality tier (``high`` / ``medium`` / ``low``).

        For each tier, compute the fraction of all 22 coordinates that
        are covered by *at least one* app of that quality tier.
        """
        apps = portfolio.ideas
        total_coords = max(len(_ALL_COORDINATES), 1)
        tier_coords: dict[str, set[ApplicationCoordinate]] = {
            tier: set() for tier in _QUALITY_TIERS
        }
        for app in apps:
            tier = app.quality_tier if app.quality_tier in _QUALITY_TIERS else "medium"
            tier_coords[tier] |= app.coordinates

        return {
            tier: len(tier_coords[tier]) / total_coords
            for tier in _QUALITY_TIERS
        }

    def _detect_gaps(
        self,
        density: dict[tuple[ApplicationCoordinate, ...], float],
    ) -> list[Gap]:
        """Identify coordinate combinations below :attr:`COVERAGE_THRESHOLD`.

        Returns a list of :class:`Gap` sorted ascending by coverage
        (worst gaps first).
        """
        gaps: list[Gap] = []
        for coord_tuple, coverage in density.items():
            if coverage < self.COVERAGE_THRESHOLD:
                gap_type = _classify_gap(coverage)
                gaps.append(Gap(
                    coordinates=coord_tuple,
                    coverage=coverage,
                    gap_type=gap_type,
                    description=_describe_gap(coord_tuple, coverage, gap_type),
                ))
        gaps.sort(key=lambda g: (g.coverage, g.coordinates))
        return gaps


# ===================================================================
# GapDetector
# ===================================================================


class GapDetector:
    """Detects gaps at single, pairwise, and triple granularities.

    Unlike :class:`AppCoverageEstimator` (which produces a full report),
    ``GapDetector`` is optimised for targeted gap queries and ranking
    against a stated :class:`AppIdeationPurpose`.
    """

    GAP_THRESHOLD_SINGLE = 0.25
    GAP_THRESHOLD_PAIR = 0.15
    GAP_THRESHOLD_TRIPLE = 0.05

    # ---------------------------------------------------------------- singles

    def detect_single_gaps(self, portfolio: IdeaPortfolio) -> list[Gap]:
        """Single coordinates with coverage below :attr:`GAP_THRESHOLD_SINGLE`.

        Returns a list of :class:`Gap` sorted ascending by coverage.
        """
        apps = portfolio.ideas
        n = max(len(apps), 1)
        gaps: list[Gap] = []

        for coord in _ALL_COORDINATES:
            count = sum(1 for a in apps if coord in a.coordinates)
            coverage = count / n
            if coverage < self.GAP_THRESHOLD_SINGLE:
                gap_type = _classify_gap(coverage)
                gaps.append(Gap(
                    coordinates=(coord,),
                    coverage=coverage,
                    gap_type=gap_type,
                    description=_describe_gap((coord,), coverage, gap_type),
                ))

        gaps.sort(key=lambda g: g.coverage)
        return gaps

    # ------------------------------------------------------------------ pairs

    def detect_pairwise_gaps(self, portfolio: IdeaPortfolio) -> list[Gap]:
        """Pairs ``(c1, c2)`` with joint coverage below :attr:`GAP_THRESHOLD_PAIR`.

        All C(22, 2) = 231 pairs are considered.  To focus on the most
        *interesting* gaps the result is limited to the top 50 ordered by
        *gap opportunity* – a score that is high when joint coverage is
        low **and** both individual coverages are high (meaning users
        clearly want each capability, but nobody combines them).

        Returns up to 50 :class:`Gap` objects sorted descending by
        opportunity score.
        """
        apps = portfolio.ideas
        n = max(len(apps), 1)

        # Pre-compute single-coord coverages.
        single: dict[ApplicationCoordinate, float] = {}
        for coord in _ALL_COORDINATES:
            single[coord] = sum(1 for a in apps if coord in a.coordinates) / n

        scored_gaps: list[tuple[float, Gap]] = []

        for c1, c2 in combinations(_ALL_COORDINATES, 2):
            pair = frozenset({c1, c2})
            joint = sum(1 for a in apps if pair.issubset(a.coordinates)) / n
            if joint >= self.GAP_THRESHOLD_PAIR:
                continue
            # Opportunity: high individual × low joint.
            individual_strength = min(single[c1], single[c2])
            opportunity = individual_strength * (1.0 - joint)
            gap_type = _classify_gap(joint)
            gap = Gap(
                coordinates=(c1, c2),
                coverage=joint,
                gap_type=gap_type,
                description=_describe_gap((c1, c2), joint, gap_type),
            )
            scored_gaps.append((opportunity, gap))

        # Sort descending by opportunity, take top 50.
        scored_gaps.sort(key=lambda t: t[0], reverse=True)
        return [g for _, g in scored_gaps[:50]]

    # ---------------------------------------------------------------- triples

    def detect_triple_gaps(self, portfolio: IdeaPortfolio) -> list[Gap]:
        """Triple intersections – the most valuable novelty opportunities.

        All C(22, 3) = 1540 triples are evaluated.  A triple qualifies
        only when:

        * Each individual coordinate has coverage > 0.1 (the space is
          meaningful – people actually build apps with each coord).
        * Joint coverage < :attr:`GAP_THRESHOLD_TRIPLE`.

        Returns up to 30 :class:`Gap` objects sorted descending by
        opportunity score.
        """
        apps = portfolio.ideas
        n = max(len(apps), 1)

        # Pre-compute singles.
        single: dict[ApplicationCoordinate, float] = {}
        for coord in _ALL_COORDINATES:
            single[coord] = sum(1 for a in apps if coord in a.coordinates) / n

        # Only consider coordinates above the individual threshold.
        viable = [c for c in _ALL_COORDINATES if single[c] > 0.1]

        scored_gaps: list[tuple[float, Gap]] = []

        for triple in combinations(viable, 3):
            triple_set = frozenset(triple)
            joint = sum(1 for a in apps if triple_set.issubset(a.coordinates)) / n
            if joint >= self.GAP_THRESHOLD_TRIPLE:
                continue
            # Opportunity: geometric mean of individual coverages × (1 - joint).
            indiv = [single[c] for c in triple]
            geo_mean = math.pow(indiv[0] * indiv[1] * indiv[2], 1.0 / 3.0)
            opportunity = geo_mean * (1.0 - joint)
            gap_type = _classify_gap(joint)
            gap = Gap(
                coordinates=triple,
                coverage=joint,
                gap_type=gap_type,
                description=_describe_gap(triple, joint, gap_type),
            )
            scored_gaps.append((opportunity, gap))

        scored_gaps.sort(key=lambda t: t[0], reverse=True)
        return [g for _, g in scored_gaps[:30]]

    # ---------------------------------------------------------------- ranking

    def rank_gaps(
        self,
        gaps: list[Gap],
        purpose: AppIdeationPurpose,
    ) -> list[Gap]:
        """Rank gaps by opportunity score relative to *purpose*.

        For each gap the opportunity score is::

            opportunity = (1 - coverage) * relevance * coord_count_bonus

        where:

        * **relevance** is the fraction of the gap's coordinates that
          overlap with the coordinates associated with *purpose.domain*.
          If none overlap, a floor of 0.2 is applied so that all gaps
          remain rankable.
        * **coord_count_bonus** rewards higher-dimensional gaps:
          triples → 1.5, pairs → 1.2, singles → 1.0.

        Returns a new list sorted *descending* by opportunity.
        """
        domain_relevant = _domain_coords(purpose)
        constraint_keywords = {t.lower() for t in purpose.constraint_tags}

        scored: list[tuple[float, Gap]] = []
        for gap in gaps:
            n_coords = len(gap.coordinates)

            # --- coord_count_bonus ---
            if n_coords >= 3:
                bonus = 1.5
            elif n_coords == 2:
                bonus = 1.2
            else:
                bonus = 1.0

            # --- relevance ---
            if domain_relevant:
                overlap = sum(
                    1 for c in gap.coordinates if c in domain_relevant
                )
                relevance = max(overlap / n_coords, 0.2)
            else:
                relevance = 1.0

            # Boost relevance if any constraint tag matches a coordinate name.
            if constraint_keywords:
                for c in gap.coordinates:
                    for kw in constraint_keywords:
                        if kw in c.value.lower():
                            relevance = min(relevance + 0.15, 1.0)

            opportunity = (1.0 - gap.coverage) * relevance * bonus
            scored.append((opportunity, gap))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [g for _, g in scored]

    # ------------------------------------------------------- convenience API

    def detect_all(
        self,
        portfolio: IdeaPortfolio,
        *,
        include_triples: bool = True,
    ) -> list[Gap]:
        """Run all granularity detectors and return a merged list.

        Gaps are deduplicated by coordinate tuple and returned sorted
        ascending by coverage.
        """
        seen: set[tuple[ApplicationCoordinate, ...]] = set()
        merged: list[Gap] = []

        for gap in self.detect_single_gaps(portfolio):
            key = gap.coordinates
            if key not in seen:
                seen.add(key)
                merged.append(gap)

        for gap in self.detect_pairwise_gaps(portfolio):
            key = gap.coordinates
            if key not in seen:
                seen.add(key)
                merged.append(gap)

        if include_triples:
            for gap in self.detect_triple_gaps(portfolio):
                key = gap.coordinates
                if key not in seen:
                    seen.add(key)
                    merged.append(gap)

        merged.sort(key=lambda g: (g.coverage, len(g.coordinates), g.coordinates))
        return merged

    def detect_and_rank(
        self,
        portfolio: IdeaPortfolio,
        purpose: AppIdeationPurpose,
        *,
        include_triples: bool = True,
    ) -> list[Gap]:
        """Convenience: detect all gaps then rank by *purpose*.

        Equivalent to ``rank_gaps(detect_all(portfolio), purpose)``.
        """
        all_gaps = self.detect_all(portfolio, include_triples=include_triples)
        return self.rank_gaps(all_gaps, purpose)


# ===================================================================
# Utility functions (public)
# ===================================================================


def coverage_summary(report: CoverageReport) -> dict[str, Any]:
    """Produce a compact JSON-safe summary of a :class:`CoverageReport`.

    Returns a dict with:

    * ``total_single_coords`` – how many of the 22 coordinates appear at
      least once.
    * ``mean_single_coverage`` – arithmetic mean of single-coord densities.
    * ``gap_count`` – total gaps detected.
    * ``unserved_count`` – gaps with zero coverage.
    * ``underserved_count`` – gaps with coverage in (0, 0.15).
    * ``need_coverage`` – direct copy.
    * ``quality_coverage`` – direct copy.
    """
    single_coverages: list[float] = []
    for key, val in report.coordinate_coverage.items():
        if len(key) == 1:
            single_coverages.append(val)

    present = sum(1 for v in single_coverages if v > 0)
    mean_cov = _safe_div(sum(single_coverages), len(single_coverages))

    unserved = sum(1 for g in report.gaps if g.gap_type is GapType.UNSERVED)
    underserved = sum(1 for g in report.gaps if g.gap_type is GapType.UNDERSERVED)

    return {
        "total_single_coords": present,
        "total_possible": len(_ALL_COORDINATES),
        "mean_single_coverage": round(mean_cov, 4),
        "gap_count": len(report.gaps),
        "unserved_count": unserved,
        "underserved_count": underserved,
        "need_coverage": {k: round(v, 4) for k, v in report.need_coverage.items()},
        "quality_coverage": {k: round(v, 4) for k, v in report.quality_coverage.items()},
    }


def gap_opportunity_matrix(
    portfolio: IdeaPortfolio,
) -> dict[tuple[ApplicationCoordinate, ApplicationCoordinate], float]:
    """Build a 22×22 opportunity matrix.

    Each cell ``(ci, cj)`` contains::

        opportunity = individual_mean(ci, cj) × (1 - joint(ci, cj))

    High values mean users want both capabilities but no app delivers
    them together.  Diagonal entries are ``0.0``.
    """
    apps = portfolio.ideas
    n = max(len(apps), 1)

    single: dict[ApplicationCoordinate, float] = {}
    for c in _ALL_COORDINATES:
        single[c] = sum(1 for a in apps if c in a.coordinates) / n

    matrix: dict[tuple[ApplicationCoordinate, ApplicationCoordinate], float] = {}
    for ci in _ALL_COORDINATES:
        for cj in _ALL_COORDINATES:
            if ci is cj:
                matrix[(ci, cj)] = 0.0
                continue
            pair = frozenset({ci, cj})
            joint = sum(1 for a in apps if pair.issubset(a.coordinates)) / n
            indiv_mean = (single[ci] + single[cj]) / 2.0
            matrix[(ci, cj)] = indiv_mean * (1.0 - joint)

    return matrix


def top_opportunities(
    portfolio: IdeaPortfolio,
    k: int = 10,
) -> list[tuple[tuple[ApplicationCoordinate, ApplicationCoordinate], float]]:
    """Return the *k* highest-opportunity coordinate pairs.

    Uses :func:`gap_opportunity_matrix` internally.  Each result is a
    ``((ci, cj), score)`` tuple, sorted descending by score.
    Duplicate symmetric pairs are removed (only *ci < cj* is kept).
    """
    matrix = gap_opportunity_matrix(portfolio)
    seen: set[frozenset[ApplicationCoordinate]] = set()
    unique: list[tuple[tuple[ApplicationCoordinate, ApplicationCoordinate], float]] = []

    for (ci, cj), score in matrix.items():
        key = frozenset({ci, cj})
        if ci is cj or key in seen:
            continue
        seen.add(key)
        unique.append(((ci, cj), score))

    unique.sort(key=lambda t: t[1], reverse=True)
    return unique[:k]


def need_gap_summary(report: CoverageReport) -> dict[str, list[str]]:
    """Map each need category to the coordinate-names of its uncovered coords.

    A coordinate is "uncovered" when its single-coord coverage in
    *report.coordinate_coverage* is zero.
    """
    result: dict[str, list[str]] = {}
    for category, cat_coords in _NEED_CATEGORIES.items():
        missing: list[str] = []
        for coord in sorted(cat_coords, key=lambda c: c.value):
            cov = report.coordinate_coverage.get((coord,), 0.0)
            if cov == 0.0:
                missing.append(coord.value)
        result[category] = missing
    return result
