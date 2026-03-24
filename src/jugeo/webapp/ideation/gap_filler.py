"""Stage 3A – Gap-Filler Idea Generator.

Standalone module (no jugeo imports, Python stdlib only).

Given a list of :class:`Gap` objects from coverage analysis and an
:class:`AppIdeationPurpose`, this module converts each gap into a
concrete :class:`IdeaProposal` with estimated gain, feasibility, and
novelty scores.

The main entry-point is :meth:`GapFillerGenerator.generate`.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from .models import (
    ApplicationCoordinate,
    AppIdeationPurpose,
    Gap,
    GapType,
    GainProfile,
    IdeaSource,
    IdeaProposal,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cost estimates in dev-hours for each coordinate type.
COORDINATE_BASE_COSTS: dict[ApplicationCoordinate, float] = {
    ApplicationCoordinate.DATA_INGESTION: 8,
    ApplicationCoordinate.DATA_TRANSFORMATION: 6,
    ApplicationCoordinate.DATA_VISUALIZATION: 12,
    ApplicationCoordinate.DATA_EXPORT: 4,
    ApplicationCoordinate.COMPUTATION_ON_DEMAND: 10,
    ApplicationCoordinate.BATCH_PROCESSING: 14,
    ApplicationCoordinate.COMPARISON: 6,
    ApplicationCoordinate.AGGREGATION: 8,
    ApplicationCoordinate.FORM_WORKFLOW: 10,
    ApplicationCoordinate.FILE_PROCESSING: 12,
    ApplicationCoordinate.REAL_TIME_FEEDBACK: 16,
    ApplicationCoordinate.COLLABORATIVE_EDITING: 24,
    ApplicationCoordinate.SCHEDULING: 14,
    ApplicationCoordinate.INVENTORY: 10,
    ApplicationCoordinate.MATCHING: 12,
    ApplicationCoordinate.SIMULATION: 20,
    ApplicationCoordinate.AUDIT_TRAIL: 8,
    ApplicationCoordinate.CONSTRAINT_SATISFACTION: 18,
    ApplicationCoordinate.STATIC_REPORT: 6,
    ApplicationCoordinate.INTERACTIVE_DASHBOARD: 16,
    ApplicationCoordinate.NOTIFICATION: 8,
    ApplicationCoordinate.API_PROVISION: 10,
}

# User-value multipliers per coordinate (relative importance).
# Higher values indicate coordinates that deliver more perceived value
# to end-users when present in an application.
COORDINATE_VALUE_WEIGHTS: dict[ApplicationCoordinate, float] = {
    ApplicationCoordinate.DATA_INGESTION: 0.9,
    ApplicationCoordinate.DATA_TRANSFORMATION: 1.0,
    ApplicationCoordinate.DATA_VISUALIZATION: 1.2,
    ApplicationCoordinate.DATA_EXPORT: 0.7,
    ApplicationCoordinate.COMPUTATION_ON_DEMAND: 1.4,
    ApplicationCoordinate.BATCH_PROCESSING: 1.0,
    ApplicationCoordinate.COMPARISON: 0.9,
    ApplicationCoordinate.AGGREGATION: 1.0,
    ApplicationCoordinate.FORM_WORKFLOW: 1.1,
    ApplicationCoordinate.FILE_PROCESSING: 0.8,
    ApplicationCoordinate.REAL_TIME_FEEDBACK: 1.3,
    ApplicationCoordinate.COLLABORATIVE_EDITING: 1.3,
    ApplicationCoordinate.SCHEDULING: 1.1,
    ApplicationCoordinate.INVENTORY: 0.9,
    ApplicationCoordinate.MATCHING: 1.2,
    ApplicationCoordinate.SIMULATION: 1.3,
    ApplicationCoordinate.AUDIT_TRAIL: 0.8,
    ApplicationCoordinate.CONSTRAINT_SATISFACTION: 1.5,
    ApplicationCoordinate.STATIC_REPORT: 0.7,
    ApplicationCoordinate.INTERACTIVE_DASHBOARD: 1.2,
    ApplicationCoordinate.NOTIFICATION: 0.8,
    ApplicationCoordinate.API_PROVISION: 1.0,
}

# Cross-domain affinity tiers.  Coordinates that commonly appear across
# many different application domains receive a higher multiplier.
_HIGH_CROSS_DOMAIN: frozenset[ApplicationCoordinate] = frozenset({
    ApplicationCoordinate.COMPUTATION_ON_DEMAND,
    ApplicationCoordinate.DATA_VISUALIZATION,
    ApplicationCoordinate.FORM_WORKFLOW,
    ApplicationCoordinate.MATCHING,
    ApplicationCoordinate.DATA_INGESTION,
    ApplicationCoordinate.DATA_EXPORT,
    ApplicationCoordinate.API_PROVISION,
})

_MEDIUM_CROSS_DOMAIN: frozenset[ApplicationCoordinate] = frozenset({
    ApplicationCoordinate.COLLABORATIVE_EDITING,
    ApplicationCoordinate.AUDIT_TRAIL,
    ApplicationCoordinate.NOTIFICATION,
    ApplicationCoordinate.SCHEDULING,
    ApplicationCoordinate.COMPARISON,
    ApplicationCoordinate.AGGREGATION,
    ApplicationCoordinate.STATIC_REPORT,
    ApplicationCoordinate.INTERACTIVE_DASHBOARD,
})

_LOW_CROSS_DOMAIN: frozenset[ApplicationCoordinate] = frozenset({
    ApplicationCoordinate.SIMULATION,
    ApplicationCoordinate.CONSTRAINT_SATISFACTION,
    ApplicationCoordinate.BATCH_PROCESSING,
    ApplicationCoordinate.FILE_PROCESSING,
    ApplicationCoordinate.REAL_TIME_FEEDBACK,
    ApplicationCoordinate.INVENTORY,
    ApplicationCoordinate.DATA_TRANSFORMATION,
})

# Discount factor applied to each additional coordinate beyond the first
# when estimating the cost of a multi-coordinate application.
_COMBINATION_DISCOUNT: float = 0.70

# Integration overhead percentage added on top of the raw sum.
_INTEGRATION_OVERHEAD: float = 0.20

# Max theoretical single-coordinate value weight (used for normalisation).
_MAX_SINGLE_WEIGHT: float = max(COORDINATE_VALUE_WEIGHTS.values())

# Uncertainty defaults per gap type.
_UNCERTAINTY_BY_GAP_TYPE: dict[GapType, float] = {
    GapType.UNSERVED: 0.30,
    GapType.UNDERSERVED: 0.40,
    GapType.WRONG_METHOD: 0.50,
    GapType.WRONG_AUDIENCE: 0.45,
    GapType.DISCONTINUED: 0.35,
}


# ---------------------------------------------------------------------------
# Human-readable coordinate labels
# ---------------------------------------------------------------------------

def _coord_label(coord: ApplicationCoordinate) -> str:
    """Return a human-friendly label for a coordinate enum value.

    Converts ``DATA_INGESTION`` → ``"Data Ingestion"`` etc.
    """
    return coord.value.replace("_", " ").title()


# ---------------------------------------------------------------------------
# GapFillerGenerator
# ---------------------------------------------------------------------------


class GapFillerGenerator:
    """Stage 3A: Generates :class:`IdeaProposal` instances from coverage gaps.

    The generator walks a list of detected gaps, estimates the value and
    cost of filling each one, and emits fully populated idea proposals
    ready for downstream validation and ranking.

    Usage::

        gen = GapFillerGenerator()
        proposals = gen.generate(gaps, purpose)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        gaps: list[Gap],
        purpose: AppIdeationPurpose,
    ) -> list[IdeaProposal]:
        """Convert each gap into an :class:`IdeaProposal`.

        Parameters
        ----------
        gaps:
            Gaps detected by the coverage analyser.
        purpose:
            The ideation purpose governing domain, audience, and weights.

        Returns
        -------
        list[IdeaProposal]
            Proposals sorted by combined (feasibility + novelty) descending.
        """
        proposals: list[IdeaProposal] = []
        for gap in gaps:
            proposal = self._gap_to_idea(gap, purpose)
            if proposal is not None:
                proposals.append(proposal)
        proposals.sort(
            key=lambda p: p.feasibility_score + p.novelty_score,
            reverse=True,
        )
        return proposals

    # ------------------------------------------------------------------
    # Core conversion
    # ------------------------------------------------------------------

    def _gap_to_idea(
        self,
        gap: Gap,
        purpose: AppIdeationPurpose,
    ) -> IdeaProposal | None:
        """Convert a single gap into an :class:`IdeaProposal`.

        Returns ``None`` if the gap has no coordinates (degenerate input).
        """
        if not gap.coordinates:
            return None

        title = self._generate_title(gap, purpose)
        hypothesis = self._generate_hypothesis(gap, purpose)

        theorem_yield = self._estimate_gap_size(gap)
        bridge_impact = self._cross_domain_potential(gap)
        cost = self._estimate_flask_cost(gap.coordinates)
        uncertainty = _UNCERTAINTY_BY_GAP_TYPE.get(gap.gap_type, 0.50)

        gain = GainProfile(
            theorem_yield=round(theorem_yield, 4),
            bridge_impact=round(bridge_impact, 4),
            cost=round(cost, 2),
            uncertainty=round(uncertainty, 4),
        )

        feasibility = self._compute_feasibility(cost)
        novelty = self._compute_novelty(gap.coverage)

        return IdeaProposal.create(
            title=title,
            hypothesis=hypothesis,
            target_area=purpose.domain,
            coordinates={c for c in gap.coordinates},
            gain=gain,
            source=IdeaSource.GAP_DETECTION,
            feasibility_score=round(feasibility, 4),
            novelty_score=round(novelty, 4),
        )

    # ------------------------------------------------------------------
    # Estimation helpers
    # ------------------------------------------------------------------

    def _estimate_gap_size(self, gap: Gap) -> float:
        """Estimate the user value delivered by filling *gap*.

        Sums :data:`COORDINATE_VALUE_WEIGHTS` for each coordinate in the
        gap, multiplies by the unfilled fraction ``(1 − coverage)``, and
        normalises to ``[0, 1]`` by dividing by the maximum possible
        weighted sum (all 22 coordinates at full unfilled value).
        """
        raw = sum(
            COORDINATE_VALUE_WEIGHTS.get(c, 1.0)
            for c in gap.coordinates
        )
        unfilled_value = raw * (1.0 - gap.coverage)

        max_possible = sum(COORDINATE_VALUE_WEIGHTS.values())
        if max_possible == 0:
            return 0.0
        return min(1.0, unfilled_value / max_possible)

    def _estimate_flask_cost(
        self,
        coordinates: tuple[ApplicationCoordinate, ...],
    ) -> float:
        """Estimate dev-hours to build a Flask app at *coordinates*.

        Base cost is the sum of :data:`COORDINATE_BASE_COSTS` for each
        coordinate, but with sub-linear scaling: each successive
        coordinate costs ``_COMBINATION_DISCOUNT`` as much as the
        previous (geometric decay).  A flat integration overhead is
        added on top.

        Returns
        -------
        float
            Estimated dev-hours (always ≥ 0).
        """
        if not coordinates:
            return 0.0

        # Sort individual costs descending so the most expensive
        # coordinate is counted at full price.
        individual_costs = sorted(
            (COORDINATE_BASE_COSTS.get(c, 10.0) for c in coordinates),
            reverse=True,
        )

        discounted_sum = 0.0
        factor = 1.0
        for base in individual_costs:
            discounted_sum += base * factor
            factor *= _COMBINATION_DISCOUNT

        total = discounted_sum * (1.0 + _INTEGRATION_OVERHEAD)
        return total

    def _cross_domain_potential(self, gap: Gap) -> float:
        """Estimate cross-domain applicability of filling *gap*.

        Each coordinate is assigned a tier weight:
        - high cross-domain  → 1.0
        - medium cross-domain → 0.6
        - low / domain-specific → 0.3

        The score is the mean tier weight across all coordinates in the
        gap, yielding a value in ``[0.3, 1.0]``.  An empty gap returns
        ``0.0``.
        """
        if not gap.coordinates:
            return 0.0

        total = 0.0
        for coord in gap.coordinates:
            if coord in _HIGH_CROSS_DOMAIN:
                total += 1.0
            elif coord in _MEDIUM_CROSS_DOMAIN:
                total += 0.6
            else:
                total += 0.3
        return total / len(gap.coordinates)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_feasibility(cost: float) -> float:
        """Map estimated dev-hours to a feasibility score in ``[0.1, 1.0]``.

        Uses an inverse-linear relationship: lower cost → higher
        feasibility, clamped at a floor of ``0.1``.
        """
        return max(0.1, 1.0 - cost / 200.0)

    @staticmethod
    def _compute_novelty(coverage: float) -> float:
        """Map current coverage to a novelty score in ``[0.0, 1.0]``.

        Lower existing coverage means the idea is more novel.
        """
        return max(0.0, min(1.0, 1.0 - coverage))

    # ------------------------------------------------------------------
    # Text generation helpers
    # ------------------------------------------------------------------

    def _generate_title(
        self,
        gap: Gap,
        purpose: AppIdeationPurpose,
    ) -> str:
        """Generate a human-readable title for the proposal.

        Conventions
        -----------
        - 1 coordinate → ``"{Coord} Tool for {domain}"``
        - 2 coordinates → ``"{Coord1} + {Coord2} Platform for {domain}"``
        - 3+ coordinates → ``"Integrated {C1}/{C2}/… for {domain}"``
        """
        coords = gap.coordinates
        domain = purpose.domain.strip() or "General Use"

        if len(coords) == 1:
            label = _coord_label(coords[0])
            return f"{label} Tool for {domain}"

        if len(coords) == 2:
            a, b = _coord_label(coords[0]), _coord_label(coords[1])
            return f"{a} + {b} Platform for {domain}"

        labels = [_coord_label(c) for c in coords]
        joined = "/".join(labels[:4])
        suffix = "/…" if len(labels) > 4 else ""
        return f"Integrated {joined}{suffix} for {domain}"

    def _generate_hypothesis(
        self,
        gap: Gap,
        purpose: AppIdeationPurpose,
    ) -> str:
        """Generate a hypothesis statement describing the opportunity.

        The hypothesis has three parts:
        1. **Observation** – what the gap is.
        2. **Proposal** – what a Flask app at these coordinates would do.
        3. **Expected impact** – who benefits and how.
        """
        coord_names = ", ".join(_coord_label(c) for c in gap.coordinates)
        gap_type_label = _gap_type_explanation(gap.gap_type)
        population = purpose.user_population or "target users"
        domain = purpose.domain or "this domain"

        observation = (
            f"Current coverage at [{coord_names}] is "
            f"{gap.coverage:.0%} ({gap_type_label})."
        )

        proposal = (
            f"A Flask application combining {coord_names} "
            f"would serve {population} in {domain}."
        )

        impact = _impact_sentence(gap, purpose)

        return f"{observation} {proposal} {impact}"


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _gap_type_explanation(gap_type: GapType) -> str:
    """Return a short human-readable label for a :class:`GapType`."""
    _MAP: dict[GapType, str] = {
        GapType.UNSERVED: "completely unserved",
        GapType.UNDERSERVED: "underserved by existing tools",
        GapType.WRONG_METHOD: "served but via the wrong approach",
        GapType.WRONG_AUDIENCE: "existing tools target a different audience",
        GapType.DISCONTINUED: "previously served but now discontinued",
    }
    return _MAP.get(gap_type, "gap detected")


def _impact_sentence(gap: Gap, purpose: AppIdeationPurpose) -> str:
    """Build the impact clause for the hypothesis.

    Tailors the sentence based on the gap type and purpose weights.
    """
    population = purpose.user_population or "users"
    leverage = purpose.leverage_weight
    tractability = purpose.tractability_weight

    if gap.gap_type == GapType.UNSERVED:
        return (
            f"By addressing an unserved need, {population} gain a "
            f"capability that currently has no alternative."
        )

    if gap.gap_type == GapType.UNDERSERVED:
        return (
            f"By improving on existing partial solutions, {population} "
            f"would experience higher quality and reliability."
        )

    if gap.gap_type == GapType.WRONG_METHOD:
        return (
            f"Re-implementing with the right methodology would reduce "
            f"friction for {population} and increase adoption."
        )

    if gap.gap_type == GapType.WRONG_AUDIENCE:
        return (
            f"Retargeting the solution to {population} would unlock "
            f"value that current tools leave on the table."
        )

    if gap.gap_type == GapType.DISCONTINUED:
        return (
            f"Reviving this capability fills a regression gap for "
            f"{population} who depended on the previous solution."
        )

    return f"Filling this gap would create tangible value for {population}."


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------


def rank_proposals_by_roi(proposals: list[IdeaProposal]) -> list[IdeaProposal]:
    """Re-rank proposals by return-on-investment (bridge_impact / cost).

    This is a convenience function for downstream stages that want an
    ROI-based ordering instead of the default feasibility+novelty sort.
    """
    return sorted(
        proposals,
        key=lambda p: p.gain.roi(),
        reverse=True,
    )


def filter_by_feasibility(
    proposals: list[IdeaProposal],
    threshold: float = 0.4,
) -> list[IdeaProposal]:
    """Return only proposals whose feasibility score meets *threshold*."""
    return [p for p in proposals if p.feasibility_score >= threshold]


def filter_by_novelty(
    proposals: list[IdeaProposal],
    threshold: float = 0.3,
) -> list[IdeaProposal]:
    """Return only proposals whose novelty score meets *threshold*."""
    return [p for p in proposals if p.novelty_score >= threshold]


def top_n_proposals(
    proposals: list[IdeaProposal],
    n: int = 10,
    *,
    min_feasibility: float = 0.0,
    min_novelty: float = 0.0,
) -> list[IdeaProposal]:
    """Return the top *n* proposals after applying optional filters.

    Proposals are first filtered by feasibility and novelty thresholds,
    then the top *n* by combined score are returned.
    """
    filtered = [
        p for p in proposals
        if p.feasibility_score >= min_feasibility
        and p.novelty_score >= min_novelty
    ]
    filtered.sort(
        key=lambda p: p.feasibility_score + p.novelty_score,
        reverse=True,
    )
    return filtered[:n]


# ---------------------------------------------------------------------------
# Summary / diagnostics
# ---------------------------------------------------------------------------


def summarise_proposals(proposals: list[IdeaProposal]) -> dict[str, Any]:
    """Return aggregate statistics over a list of proposals.

    Keys in the returned dict:

    - ``count`` – total number of proposals.
    - ``avg_feasibility`` – mean feasibility score.
    - ``avg_novelty`` – mean novelty score.
    - ``avg_cost`` – mean estimated cost in dev-hours.
    - ``total_cost`` – sum of all estimated costs.
    - ``coordinates_histogram`` – how often each coordinate appears.
    """
    if not proposals:
        return {
            "count": 0,
            "avg_feasibility": 0.0,
            "avg_novelty": 0.0,
            "avg_cost": 0.0,
            "total_cost": 0.0,
            "coordinates_histogram": {},
        }

    n = len(proposals)
    total_feas = sum(p.feasibility_score for p in proposals)
    total_nov = sum(p.novelty_score for p in proposals)
    total_cost = sum(p.gain.cost for p in proposals)

    hist: dict[str, int] = {}
    for p in proposals:
        for c in p.coordinates:
            key = c.value if isinstance(c, ApplicationCoordinate) else str(c)
            hist[key] = hist.get(key, 0) + 1

    return {
        "count": n,
        "avg_feasibility": round(total_feas / n, 4),
        "avg_novelty": round(total_nov / n, 4),
        "avg_cost": round(total_cost / n, 2),
        "total_cost": round(total_cost, 2),
        "coordinates_histogram": dict(sorted(hist.items())),
    }


def explain_proposal(proposal: IdeaProposal) -> str:
    """Return a multi-line human-readable explanation of a proposal.

    Useful for logging and diagnostic output.
    """
    coord_labels = ", ".join(
        _coord_label(c) if isinstance(c, ApplicationCoordinate) else str(c)
        for c in sorted(proposal.coordinates, key=lambda c: c.value)
    )
    lines = [
        f"Proposal: {proposal.title}",
        f"  ID:           {proposal.id}",
        f"  Target area:  {proposal.target_area}",
        f"  Coordinates:  {coord_labels}",
        f"  Source:        {proposal.source.value}",
        f"  Feasibility:  {proposal.feasibility_score:.2f}",
        f"  Novelty:      {proposal.novelty_score:.2f}",
        f"  Cost (hrs):   {proposal.gain.cost:.1f}",
        f"  Yield:        {proposal.gain.theorem_yield:.4f}",
        f"  Bridge:       {proposal.gain.bridge_impact:.4f}",
        f"  Uncertainty:  {proposal.gain.uncertainty:.2f}",
        f"  ROI:          {proposal.gain.roi():.4f}",
        f"  Hypothesis:   {proposal.hypothesis}",
    ]
    return "\n".join(lines)
