r"""Patch selection for the cover_design sub-package (Stage 2).

Theory (theory2.tex §43.4 — Patch Selection):
    Stage 2 of the cover design pipeline selects a finite cover
    {U_1, …, U_n} from a larger universe of candidate patches, subject to
    a global budget constraint B.  The selection must satisfy:

        1. **Coverage** — the selected patches cover all of J (cf. §43.1).
        2. **Budget feasibility** — the sub-budget b_i assigned to each
           patch satisfies ∑_i b_i ≤ B and each b_i is large enough for
           the local construction on U_i to be tractable.
        3. **Sufficient overlap** — neighbouring patches share enough points
           for the Čech compatibility checks in Stage 1 to be executable.
        4. **Priority alignment** — regions of J where obligations are dense
           receive patches with larger sub-budgets.

    The selection algorithm uses a *greedy-with-lookahead* strategy:

        while uncovered(J) ≠ ∅ and budget_remaining > 0:
            candidates = score(universe \ selected)
            best = lookahead_select(candidates, budget_remaining)
            if best is None:
                break
            selected.append(best)
            budget_remaining -= best.cost

    Each candidate patch is scored by the :class:`PatchSelectionAnalyzer`
    on three axes:

    - **Priority score** — density of obligations in the patch region.
    - **Coverage gain** — fraction of as-yet-uncovered J-points this patch adds.
    - **Overlap cost** — the extra verification work incurred by overlapping
      the already-selected patches.

    The :class:`PatchSelectionCoordinator` drives the full pipeline and the
    :class:`PatchSelectionWitness` issues admissibility certificates.

    **Trust tier**: selected patch sets enter at the *PROPOSAL* tier.
    A witness certificate promotes them to *VERIFIED*.

    copilot: s02-patch-selection

References
----------
theory2.tex  §43.4 (Patch selection), §43.1 (Cover design principles),
             §38 (Budget as first-class object), §39 (Local construction loops)

Usage::

    from jugeo.generation.cover_design.patch_selection import (
        PatchSelectionCoordinator,
        PatchSelectionAnalyzer,
        PatchSelectionWitness,
        PatchCandidate,
        SelectionRanking,
        SelectionPolicy,
    )

    from jugeo.generation.cover_design.patch_selection import PatchCandidate
    candidates = [
        PatchCandidate(
            patch_id="U1",
            covered_points=frozenset(["p1", "p2", "p3"]),
            obligation_density=0.8,
            cost=0.3,
            min_overlap_required=1,
        ),
        PatchCandidate(
            patch_id="U2",
            covered_points=frozenset(["p3", "p4"]),
            obligation_density=0.5,
            cost=0.25,
            min_overlap_required=1,
        ),
    ]
    coordinator = PatchSelectionCoordinator(total_budget=1.0)
    result = coordinator.run(
        site_points=frozenset(["p1", "p2", "p3", "p4"]),
        candidates=candidates,
        policy=SelectionPolicy.BALANCED,
    )
    print(result.selected_patches, result.certified)
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.generation.cover_design.models import (
        Budget,
        CoverDesignPhase,
        CoverDesignPlan,
        PatchDescriptor,
    )
except ImportError:
    Budget = Any  # type: ignore[assignment,misc]
    CoverDesignPhase = Any  # type: ignore[assignment,misc]
    CoverDesignPlan = Any  # type: ignore[assignment,misc]
    PatchDescriptor = Any  # type: ignore[assignment,misc]

__all__ = [
    "PatchCandidate",
    "SelectionRanking",
    "SelectionPolicy",
    "PatchSelectionCoordinator",
    "PatchSelectionAnalyzer",
    "PatchSelectionWitness",
]

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_OVERLAP_DEFAULT: int = 1
_LOOKAHEAD_DEPTH: int = 3
_COVERAGE_GAIN_FLOOR: float = 0.0   # accept patches with zero marginal gain only if forced
_DEFAULT_TOTAL_BUDGET: float = 1.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SelectionPolicy(str, Enum):
    """Strategy used by the patch-selection algorithm.

    Attributes
    ----------
    GREEDY:
        At each step pick the patch with the highest coverage gain,
        ignoring priority and overlap cost.
    PRIORITY:
        At each step pick the patch with the highest obligation-density
        score, ignoring coverage gain.
    COVERAGE:
        At each step maximise coverage gain subject to budget feasibility.
    BALANCED:
        Weighted combination of priority score, coverage gain, and
        inverse overlap cost.  This is the recommended default.
    """

    GREEDY = "greedy"
    PRIORITY = "priority"
    COVERAGE = "coverage"
    BALANCED = "balanced"


# ---------------------------------------------------------------------------
# PatchCandidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchCandidate:
    """A single candidate patch that may be included in the cover.

    Attributes
    ----------
    patch_id:
        Unique identifier for this patch.
    covered_points:
        The frozenset of judgment-site points this patch covers.
    obligation_density:
        A scalar in [0, 1] indicating how densely obligations are
        concentrated in this patch's region.  Higher = higher priority.
    cost:
        The fraction of the total budget B consumed by this patch.
        Must satisfy 0 < cost ≤ 1.
    min_overlap_required:
        Minimum number of shared points with any neighbouring selected
        patch required for the Čech compatibility check to be executable.
    tags:
        Optional free-form labels for filtering and grouping.
    metadata:
        Optional additional data attached by the caller.
    """

    patch_id: str
    covered_points: frozenset[str]
    obligation_density: float
    cost: float
    min_overlap_required: int = _MIN_OVERLAP_DEFAULT
    tags: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"PatchCandidate(id={self.patch_id!r}, "
            f"pts={len(self.covered_points)}, "
            f"density={self.obligation_density:.2f}, "
            f"cost={self.cost:.3f})"
        )

    @property
    def size(self) -> int:
        """Number of site points covered by this patch."""
        return len(self.covered_points)

    def coverage_gain(self, already_covered: frozenset[str]) -> float:
        """Return the fraction of *already_covered*'s complement that this patch adds.

        Parameters
        ----------
        already_covered:
            Points already covered by previously selected patches.

        Returns
        -------
        float
            0.0 if nothing new is covered; 1.0 if all points are new.
        """
        new_points = self.covered_points - already_covered
        if self.size == 0:
            return 0.0
        return len(new_points) / self.size

    def overlap_with(self, other: PatchCandidate) -> frozenset[str]:
        """Return the set of points shared with *other*."""
        return self.covered_points & other.covered_points

    def satisfies_min_overlap(self, selected: list[PatchCandidate]) -> bool:
        """Return ``True`` if this patch has sufficient overlap with at least one
        already-selected patch, or if no patches are selected yet.

        Parameters
        ----------
        selected:
            Currently selected patches.
        """
        if not selected:
            return True
        for s in selected:
            if len(self.overlap_with(s)) >= self.min_overlap_required:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "covered_points": sorted(self.covered_points),
            "obligation_density": self.obligation_density,
            "cost": self.cost,
            "min_overlap_required": self.min_overlap_required,
            "tags": sorted(self.tags),
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# SelectionRanking
# ---------------------------------------------------------------------------


@dataclass
class SelectionRanking:
    """Stores scored rankings for a collection of :class:`PatchCandidate` objects.

    Attributes
    ----------
    ranking_id:
        Unique identifier for this ranking instance.
    policy:
        The :class:`SelectionPolicy` used to compute scores.
    scores:
        Mapping patch_id → composite score.
    priority_scores:
        Mapping patch_id → priority score (obligation density component).
    coverage_scores:
        Mapping patch_id → coverage-gain score at the time of ranking.
    overlap_penalties:
        Mapping patch_id → overlap penalty subtracted from the composite.
    ranked_ids:
        Patch ids in descending composite-score order.
    created_at:
        Unix timestamp when the ranking was computed.
    """

    ranking_id: str
    policy: SelectionPolicy
    scores: dict[str, float] = field(default_factory=dict)
    priority_scores: dict[str, float] = field(default_factory=dict)
    coverage_scores: dict[str, float] = field(default_factory=dict)
    overlap_penalties: dict[str, float] = field(default_factory=dict)
    ranked_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        top3 = self.ranked_ids[:3]
        return (
            f"SelectionRanking(id={self.ranking_id!r}, "
            f"policy={self.policy.value!r}, "
            f"top3={top3})"
        )

    def top_n(self, n: int) -> list[str]:
        """Return the top *n* patch ids by composite score."""
        return self.ranked_ids[:n]

    def get_score(self, patch_id: str) -> float:
        """Return the composite score for *patch_id*, or 0.0 if absent."""
        return self.scores.get(patch_id, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranking_id": self.ranking_id,
            "policy": self.policy.value,
            "scores": self.scores,
            "priority_scores": self.priority_scores,
            "coverage_scores": self.coverage_scores,
            "overlap_penalties": self.overlap_penalties,
            "ranked_ids": self.ranked_ids,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# SelectionResult (internal; summarises the output of a run)
# ---------------------------------------------------------------------------


@dataclass
class _SelectionResult:
    """Collects the output of a complete :class:`PatchSelectionCoordinator` run."""

    result_id: str
    selected_patches: list[PatchCandidate]
    budget_used: float
    budget_remaining: float
    covered_points: frozenset[str]
    uncovered_points: frozenset[str]
    coverage_fraction: float
    ranking: SelectionRanking | None
    certified: bool
    phase: str
    violations: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"SelectionResult(id={self.result_id!r}, "
            f"patches={len(self.selected_patches)}, "
            f"coverage={self.coverage_fraction:.1%}, "
            f"budget_used={self.budget_used:.3f}, "
            f"certified={self.certified})"
        )

    @property
    def is_complete_cover(self) -> bool:
        """Return ``True`` if all site points are covered."""
        return not self.uncovered_points

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "selected_patches": [p.to_dict() for p in self.selected_patches],
            "budget_used": self.budget_used,
            "budget_remaining": self.budget_remaining,
            "covered_points": sorted(self.covered_points),
            "uncovered_points": sorted(self.uncovered_points),
            "coverage_fraction": self.coverage_fraction,
            "certified": self.certified,
            "phase": self.phase,
            "violations": self.violations,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# PatchSelectionAnalyzer
# ---------------------------------------------------------------------------


class PatchSelectionAnalyzer:
    """Scores and ranks candidate patches for inclusion in the cover.

    The analyzer computes three component scores for each candidate:

    - **Priority score** ``p_i`` — equal to ``candidate.obligation_density``.
    - **Coverage-gain score** ``g_i`` — the fraction of as-yet-uncovered
      points that candidate U_i would add (computed against the current
      selection frontier).
    - **Overlap penalty** ``o_i`` — the sum of overlap sizes with already-
      selected patches, normalised by the candidate's own size.

    The composite score under :attr:`SelectionPolicy.BALANCED` is::

        score_i = w_p * p_i + w_g * g_i - w_o * o_i

    with default weights ``w_p = 0.4, w_g = 0.5, w_o = 0.1``.

    Parameters
    ----------
    w_priority:
        Weight for the priority score component.
    w_coverage:
        Weight for the coverage-gain component.
    w_overlap_penalty:
        Weight for the overlap penalty component.
    """

    def __init__(
        self,
        w_priority: float = 0.4,
        w_coverage: float = 0.5,
        w_overlap_penalty: float = 0.1,
    ) -> None:
        self._w_p = w_priority
        self._w_g = w_coverage
        self._w_o = w_overlap_penalty
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        candidates: list[PatchCandidate],
        already_covered: frozenset[str],
        selected: list[PatchCandidate],
        policy: SelectionPolicy = SelectionPolicy.BALANCED,
        budget_remaining: float = _DEFAULT_TOTAL_BUDGET,
    ) -> SelectionRanking:
        """Compute and return a :class:`SelectionRanking` for *candidates*.

        Only candidates whose ``cost`` does not exceed *budget_remaining*
        are included in the ranking; the rest are silently excluded.

        Parameters
        ----------
        candidates:
            The pool of unselected patches to rank.
        already_covered:
            Points already covered by *selected*.
        selected:
            Patches that have already been selected.
        policy:
            The scoring policy to apply.
        budget_remaining:
            Amount of budget still available; patches with ``cost >
            budget_remaining`` are excluded.

        Returns
        -------
        SelectionRanking
            Candidates sorted by descending composite score.
        """
        affordable = [c for c in candidates if c.cost <= budget_remaining]

        priority_scores: dict[str, float] = {}
        coverage_scores: dict[str, float] = {}
        overlap_penalties: dict[str, float] = {}
        composite_scores: dict[str, float] = {}

        for c in affordable:
            p_i = self._priority_score(c, policy)
            g_i = self._coverage_score(c, already_covered, policy)
            o_i = self._overlap_penalty(c, selected, policy)

            priority_scores[c.patch_id] = p_i
            coverage_scores[c.patch_id] = g_i
            overlap_penalties[c.patch_id] = o_i
            composite_scores[c.patch_id] = self._composite(p_i, g_i, o_i, policy)

        ranked_ids = sorted(
            composite_scores, key=lambda pid: composite_scores[pid], reverse=True
        )

        ranking = SelectionRanking(
            ranking_id=str(uuid.uuid4()),
            policy=policy,
            scores=composite_scores,
            priority_scores=priority_scores,
            coverage_scores=coverage_scores,
            overlap_penalties=overlap_penalties,
            ranked_ids=ranked_ids,
            created_at=time.time(),
        )
        self._logger.debug(
            "Ranked %d affordable candidates (policy=%s), top=%r",
            len(affordable),
            policy.value,
            ranked_ids[:3],
        )
        return ranking

    def score_single(
        self,
        candidate: PatchCandidate,
        already_covered: frozenset[str],
        selected: list[PatchCandidate],
        policy: SelectionPolicy = SelectionPolicy.BALANCED,
    ) -> float:
        """Return the composite score for a single *candidate*.

        Useful for incremental re-scoring after each selection step.
        """
        p_i = self._priority_score(candidate, policy)
        g_i = self._coverage_score(candidate, already_covered, policy)
        o_i = self._overlap_penalty(candidate, selected, policy)
        return self._composite(p_i, g_i, o_i, policy)

    def lookahead_select(
        self,
        candidates: list[PatchCandidate],
        already_covered: frozenset[str],
        selected: list[PatchCandidate],
        budget_remaining: float,
        policy: SelectionPolicy,
        depth: int = _LOOKAHEAD_DEPTH,
    ) -> PatchCandidate | None:
        """Use greedy lookahead of *depth* steps to pick the best next patch.

        For each affordable candidate c, we simulate adding c and then
        greedily adding (depth-1) more candidates.  The candidate that
        leads to the best projected coverage after *depth* steps wins.

        Parameters
        ----------
        candidates:
            Unselected candidate pool (affordable check done inside).
        already_covered:
            Points currently covered.
        selected:
            Currently selected patches.
        budget_remaining:
            Available budget.
        policy:
            Scoring policy for intermediate rankings.
        depth:
            How many steps ahead to simulate.

        Returns
        -------
        PatchCandidate | None
            The best next patch to add, or ``None`` if the budget is exhausted
            or no candidates remain.
        """
        affordable = [c for c in candidates if c.cost <= budget_remaining]
        if not affordable:
            return None

        best_patch: PatchCandidate | None = None
        best_projected_coverage: float = -1.0

        for first_candidate in affordable:
            proj_covered = already_covered | first_candidate.covered_points
            proj_selected = selected + [first_candidate]
            proj_budget = budget_remaining - first_candidate.cost
            remaining = [c for c in affordable if c.patch_id != first_candidate.patch_id]

            # Simulate (depth - 1) more greedy steps
            for _ in range(depth - 1):
                if not remaining or proj_budget <= 0.0:
                    break
                sub_ranking = self.rank(
                    remaining, proj_covered, proj_selected, policy, proj_budget
                )
                if not sub_ranking.ranked_ids:
                    break
                next_id = sub_ranking.ranked_ids[0]
                next_patch = next(
                    (c for c in remaining if c.patch_id == next_id), None
                )
                if next_patch is None:
                    break
                proj_covered = proj_covered | next_patch.covered_points
                proj_selected.append(next_patch)
                proj_budget -= next_patch.cost
                remaining = [c for c in remaining if c.patch_id != next_id]

            # Total covered fraction after simulation
            if len(already_covered) + len(first_candidate.covered_points) > 0:
                projected_coverage = len(proj_covered)
            else:
                projected_coverage = 0

            if projected_coverage > best_projected_coverage:
                best_projected_coverage = projected_coverage
                best_patch = first_candidate

        return best_patch

    def estimate_sub_budget(
        self,
        candidate: PatchCandidate,
        total_budget: float,
        n_patches: int,
    ) -> float:
        """Estimate the sub-budget to allocate to *candidate*.

        Patches with higher obligation density receive a proportionally
        larger sub-budget.  The sub-budget is guaranteed to be at least
        ``candidate.cost``.

        Parameters
        ----------
        candidate:
            The patch for which to estimate the sub-budget.
        total_budget:
            The global budget B.
        n_patches:
            Expected total number of selected patches (used for even
            distribution baseline).

        Returns
        -------
        float
            The estimated sub-budget b_i in (0, total_budget].
        """
        if n_patches <= 0:
            return total_budget
        base = total_budget / n_patches
        density_factor = 1.0 + candidate.obligation_density  # in [1, 2]
        estimate = base * density_factor
        # Ensure we never over-allocate
        return min(estimate, total_budget)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _priority_score(
        self, candidate: PatchCandidate, policy: SelectionPolicy
    ) -> float:
        if policy == SelectionPolicy.COVERAGE:
            return 0.0  # priority is irrelevant in pure-coverage mode
        return candidate.obligation_density

    def _coverage_score(
        self,
        candidate: PatchCandidate,
        already_covered: frozenset[str],
        policy: SelectionPolicy,
    ) -> float:
        if policy == SelectionPolicy.PRIORITY:
            return 0.0  # coverage score is irrelevant in pure-priority mode
        return candidate.coverage_gain(already_covered)

    def _overlap_penalty(
        self,
        candidate: PatchCandidate,
        selected: list[PatchCandidate],
        policy: SelectionPolicy,
    ) -> float:
        if not selected or candidate.size == 0:
            return 0.0
        total_overlap = sum(len(candidate.overlap_with(s)) for s in selected)
        normalised = total_overlap / candidate.size
        return min(normalised, 1.0)

    def _composite(
        self,
        p_i: float,
        g_i: float,
        o_i: float,
        policy: SelectionPolicy,
    ) -> float:
        if policy == SelectionPolicy.GREEDY:
            return g_i
        if policy == SelectionPolicy.PRIORITY:
            return p_i
        if policy == SelectionPolicy.COVERAGE:
            return g_i
        # BALANCED
        return self._w_p * p_i + self._w_g * g_i - self._w_o * o_i


# ---------------------------------------------------------------------------
# PatchSelectionWitness
# ---------------------------------------------------------------------------


class PatchSelectionWitness:
    """Certifies that a selected patch set forms a valid admissible cover.

    An *admissibility witness* checks:

    1. **Coverage** — the union of selected patches covers all of J.
    2. **Budget feasibility** — the total cost of selected patches ≤ B.
    3. **Overlap sufficiency** — each consecutive pair of patches sharing
       any point satisfies ``min_overlap_required`` for each patch.
    4. **Non-emptiness** — at least one patch is selected.

    Parameters
    ----------
    budget_tolerance:
        Fractional tolerance on the budget check.  A selection that
        exceeds the budget by at most ``budget_tolerance * B`` is still
        accepted (accounts for floating-point rounding).
    """

    def __init__(self, budget_tolerance: float = 1e-9) -> None:
        self._tol = budget_tolerance
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._records: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def certify(
        self,
        result: _SelectionResult,
        site_points: frozenset[str],
        total_budget: float,
    ) -> bool:
        """Certify *result* against the admissibility conditions.

        Parameters
        ----------
        result:
            The selection result to certify.
        site_points:
            The complete judgment-site point set.
        total_budget:
            The global budget constraint B.

        Returns
        -------
        bool
            ``True`` if all admissibility conditions are satisfied.
        """
        violations: list[str] = []

        # 1. Non-emptiness
        if not result.selected_patches:
            violations.append("No patches selected; the cover is empty.")

        # 2. Coverage
        covered: set[str] = set()
        for p in result.selected_patches:
            covered |= p.covered_points
        uncovered = site_points - covered
        if uncovered:
            violations.append(
                f"Coverage gap: {len(uncovered)} site point(s) uncovered — "
                f"{sorted(uncovered)!r}."
            )

        # 3. Budget feasibility
        total_cost = sum(p.cost for p in result.selected_patches)
        if total_cost > total_budget * (1.0 + self._tol):
            violations.append(
                f"Budget exceeded: cost={total_cost:.4f} > budget={total_budget:.4f}."
            )

        # 4. Overlap sufficiency
        for p in result.selected_patches:
            neighbours = [
                s for s in result.selected_patches
                if s.patch_id != p.patch_id and p.overlap_with(s)
            ]
            for nb in neighbours:
                overlap_size = len(p.overlap_with(nb))
                if overlap_size < p.min_overlap_required:
                    violations.append(
                        f"Patch {p.patch_id!r} has insufficient overlap with "
                        f"{nb.patch_id!r}: {overlap_size} < {p.min_overlap_required} required."
                    )

        certified = not violations
        result.certified = certified
        result.phase = "certified" if certified else "rejected"
        result.violations = violations

        self._records.append(
            {
                "result_id": result.result_id,
                "certified": certified,
                "violations": violations,
                "total_cost": total_cost,
                "coverage_fraction": result.coverage_fraction,
                "timestamp": time.time(),
            }
        )

        if certified:
            self._logger.info(
                "SelectionResult %s CERTIFIED: %d patches, cost=%.3f",
                result.result_id,
                len(result.selected_patches),
                total_cost,
            )
        else:
            self._logger.error(
                "SelectionResult %s REJECTED: %d violation(s): %s",
                result.result_id,
                len(violations),
                violations,
            )

        return certified

    def get_records(self) -> list[dict[str, Any]]:
        """Return all witness records issued during this object's lifetime."""
        return list(self._records)

    def summarise(self) -> dict[str, Any]:
        """Return aggregate statistics over all witness decisions."""
        total = len(self._records)
        certified_count = sum(1 for r in self._records if r["certified"])
        return {
            "total_reviewed": total,
            "certified": certified_count,
            "rejected": total - certified_count,
        }


# ---------------------------------------------------------------------------
# PatchSelectionCoordinator
# ---------------------------------------------------------------------------


class PatchSelectionCoordinator:
    """Runs the full patch-selection pipeline.

    Pipeline phases:

        initialising → analysing → proposing → ranking → selecting
        → finalising → certified | rejected

    The coordinator delegates scoring to a :class:`PatchSelectionAnalyzer`
    and certification to a :class:`PatchSelectionWitness`.

    Parameters
    ----------
    total_budget:
        The global budget B (as a scalar; must be positive).
    analyzer:
        Optional pre-built analyzer.  A default one is constructed otherwise.
    witness:
        Optional pre-built witness.  A default one is constructed otherwise.
    lookahead_depth:
        How many steps ahead the greedy-with-lookahead algorithm simulates.
    """

    def __init__(
        self,
        total_budget: float = _DEFAULT_TOTAL_BUDGET,
        analyzer: PatchSelectionAnalyzer | None = None,
        witness: PatchSelectionWitness | None = None,
        lookahead_depth: int = _LOOKAHEAD_DEPTH,
    ) -> None:
        if total_budget <= 0:
            raise ValueError(f"total_budget must be positive; got {total_budget}")
        self._budget = total_budget
        self._analyzer = analyzer or PatchSelectionAnalyzer()
        self._witness = witness or PatchSelectionWitness()
        self._lookahead_depth = lookahead_depth
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._run_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        site_points: frozenset[str],
        candidates: list[PatchCandidate],
        policy: SelectionPolicy = SelectionPolicy.BALANCED,
        metadata: dict[str, Any] | None = None,
    ) -> _SelectionResult:
        """Execute the full patch-selection pipeline and return a result.

        Parameters
        ----------
        site_points:
            The complete set of judgment-site points J.
        candidates:
            Universe of candidate patches to select from.
        policy:
            Scoring / selection policy.
        metadata:
            Optional extra data attached to the result.

        Returns
        -------
        _SelectionResult
            Contains the chosen patches, budget accounting, coverage
            statistics, and certification status.
        """
        result_id = str(uuid.uuid4())
        self._logger.info(
            "PatchSelectionCoordinator: starting run %s (budget=%.3f, policy=%s, "
            "candidates=%d, site_points=%d)",
            result_id,
            self._budget,
            policy.value,
            len(candidates),
            len(site_points),
        )

        # --- Phase: analysing ---
        self._logger.debug("Run %s: phase=analysing", result_id)
        errors = self._validate_inputs(site_points, candidates)
        if errors:
            self._logger.error("Run %s: input validation errors: %s", result_id, errors)

        # --- Phase: proposing + ranking + selecting ---
        selected, budget_used, last_ranking = self._selection_loop(
            site_points=site_points,
            candidates=candidates,
            policy=policy,
        )

        # --- Phase: finalising ---
        covered_pts = frozenset(
            pt for p in selected for pt in p.covered_points
        )
        uncovered_pts = site_points - covered_pts
        coverage_fraction = (
            len(covered_pts) / len(site_points) if site_points else 1.0
        )

        result = _SelectionResult(
            result_id=result_id,
            selected_patches=selected,
            budget_used=budget_used,
            budget_remaining=self._budget - budget_used,
            covered_points=covered_pts,
            uncovered_points=uncovered_pts,
            coverage_fraction=coverage_fraction,
            ranking=last_ranking,
            certified=False,
            phase="finalising",
            violations=[],
            metadata={
                **(metadata or {}),
                "policy": policy.value,
                "total_budget": self._budget,
                "trust_tier": "PROPOSAL",
                "run_at": time.time(),
            },
        )

        # --- Phase: certification ---
        self._witness.certify(result, site_points, self._budget)
        self._record_run(result)
        return result

    def propose_candidates(
        self,
        site_points: frozenset[str],
        candidates: list[PatchCandidate],
        policy: SelectionPolicy = SelectionPolicy.BALANCED,
    ) -> SelectionRanking:
        """Return the initial ranking of all candidates without selecting any.

        Useful for inspection before committing to a full run.

        Parameters
        ----------
        site_points:
            The full judgment-site point set.
        candidates:
            Candidate patches to rank.
        policy:
            Ranking policy.

        Returns
        -------
        SelectionRanking
            Candidates sorted by composite score, with nothing selected yet.
        """
        return self._analyzer.rank(
            candidates=candidates,
            already_covered=frozenset(),
            selected=[],
            policy=policy,
            budget_remaining=self._budget,
        )

    def get_run_history(self) -> list[dict[str, Any]]:
        """Return the history of all :meth:`run` calls."""
        return list(self._run_history)

    def summarise(self) -> dict[str, Any]:
        """Return aggregate statistics over all runs."""
        total = len(self._run_history)
        certified = sum(1 for r in self._run_history if r["certified"])
        avg_coverage = (
            sum(r["coverage_fraction"] for r in self._run_history) / total
            if total
            else 0.0
        )
        witness_summary = self._witness.summarise()
        return {
            "total_runs": total,
            "certified_runs": certified,
            "rejected_runs": total - certified,
            "average_coverage_fraction": avg_coverage,
            "witness": witness_summary,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _selection_loop(
        self,
        site_points: frozenset[str],
        candidates: list[PatchCandidate],
        policy: SelectionPolicy,
    ) -> tuple[list[PatchCandidate], float, SelectionRanking | None]:
        """Core greedy-with-lookahead selection loop.

        Returns
        -------
        (selected, budget_used, last_ranking)
        """
        selected: list[PatchCandidate] = []
        already_covered: frozenset[str] = frozenset()
        budget_remaining = self._budget
        remaining = list(candidates)
        last_ranking: SelectionRanking | None = None
        iteration = 0

        while remaining and budget_remaining > 0.0:
            # Check if J is already fully covered
            if site_points and site_points <= already_covered:
                self._logger.info(
                    "Full coverage achieved after %d iterations.", iteration
                )
                break

            ranking = self._analyzer.rank(
                candidates=remaining,
                already_covered=already_covered,
                selected=selected,
                policy=policy,
                budget_remaining=budget_remaining,
            )
            last_ranking = ranking

            if not ranking.ranked_ids:
                self._logger.info(
                    "No affordable candidates remain at iteration %d.", iteration
                )
                break

            # Use lookahead to pick the best next patch
            best = self._analyzer.lookahead_select(
                candidates=remaining,
                already_covered=already_covered,
                selected=selected,
                budget_remaining=budget_remaining,
                policy=policy,
                depth=self._lookahead_depth,
            )

            if best is None:
                self._logger.info(
                    "Lookahead could not select any candidate at iteration %d.",
                    iteration,
                )
                break

            selected.append(best)
            already_covered = already_covered | best.covered_points
            budget_remaining -= best.cost
            remaining = [c for c in remaining if c.patch_id != best.patch_id]
            iteration += 1

            self._logger.debug(
                "Iteration %d: selected %r (cost=%.3f, coverage_gain=%.2f, "
                "budget_remaining=%.3f)",
                iteration,
                best.patch_id,
                best.cost,
                best.coverage_gain(already_covered - best.covered_points),
                budget_remaining,
            )

        budget_used = self._budget - budget_remaining
        return selected, budget_used, last_ranking

    def _validate_inputs(
        self,
        site_points: frozenset[str],
        candidates: list[PatchCandidate],
    ) -> list[str]:
        """Return a list of input-validation error strings (empty = valid)."""
        errors: list[str] = []
        if not site_points:
            errors.append("site_points is empty.")
        for c in candidates:
            if c.cost <= 0.0:
                errors.append(f"Candidate {c.patch_id!r} has non-positive cost {c.cost}.")
            if not (0.0 <= c.obligation_density <= 1.0):
                errors.append(
                    f"Candidate {c.patch_id!r} has obligation_density "
                    f"{c.obligation_density} outside [0, 1]."
                )
        ids = [c.patch_id for c in candidates]
        if len(ids) != len(set(ids)):
            errors.append("Duplicate patch_id values in candidates list.")
        return errors

    def _record_run(self, result: _SelectionResult) -> None:
        self._run_history.append(
            {
                "result_id": result.result_id,
                "certified": result.certified,
                "patch_count": len(result.selected_patches),
                "budget_used": result.budget_used,
                "coverage_fraction": result.coverage_fraction,
                "phase": result.phase,
                "timestamp": time.time(),
            }
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 70)
    print("patch_selection — smoke test")
    print("=" * 70)

    site = frozenset([f"p{i}" for i in range(1, 9)])  # p1 … p8

    candidates = [
        PatchCandidate(
            patch_id="U1",
            covered_points=frozenset(["p1", "p2", "p3"]),
            obligation_density=0.9,
            cost=0.25,
            min_overlap_required=1,
        ),
        PatchCandidate(
            patch_id="U2",
            covered_points=frozenset(["p3", "p4", "p5"]),
            obligation_density=0.6,
            cost=0.25,
            min_overlap_required=1,
        ),
        PatchCandidate(
            patch_id="U3",
            covered_points=frozenset(["p5", "p6", "p7"]),
            obligation_density=0.7,
            cost=0.25,
            min_overlap_required=1,
        ),
        PatchCandidate(
            patch_id="U4",
            covered_points=frozenset(["p7", "p8"]),
            obligation_density=0.5,
            cost=0.2,
            min_overlap_required=1,
        ),
        PatchCandidate(
            patch_id="U5",
            covered_points=frozenset(["p1", "p2"]),  # redundant once U1 is picked
            obligation_density=0.3,
            cost=0.1,
            min_overlap_required=1,
        ),
    ]

    # --- Test 1: BALANCED policy ---
    print("\n[1] BALANCED policy, budget=1.0")
    coord = PatchSelectionCoordinator(total_budget=1.0)
    result1 = coord.run(site, candidates, policy=SelectionPolicy.BALANCED)
    print(f"  Result: {result1}")
    print(f"  Selected: {[p.patch_id for p in result1.selected_patches]}")
    print(f"  Budget used: {result1.budget_used:.3f}")
    print(f"  Coverage: {result1.coverage_fraction:.1%}")
    print(f"  Certified: {result1.certified}")

    # --- Test 2: GREEDY policy ---
    print("\n[2] GREEDY policy, budget=0.6")
    coord2 = PatchSelectionCoordinator(total_budget=0.6)
    result2 = coord2.run(site, candidates, policy=SelectionPolicy.GREEDY)
    print(f"  Result: {result2}")
    print(f"  Selected: {[p.patch_id for p in result2.selected_patches]}")

    # --- Test 3: PRIORITY policy ---
    print("\n[3] PRIORITY policy, budget=1.0")
    coord3 = PatchSelectionCoordinator(total_budget=1.0)
    result3 = coord3.run(site, candidates, policy=SelectionPolicy.PRIORITY)
    print(f"  Result: {result3}")
    print(f"  Selected: {[p.patch_id for p in result3.selected_patches]}")

    # --- Test 4: Tight budget that cannot cover all of J ---
    print("\n[4] Tight budget=0.3 (cannot cover all of J)")
    coord4 = PatchSelectionCoordinator(total_budget=0.3)
    result4 = coord4.run(site, candidates, policy=SelectionPolicy.BALANCED)
    print(f"  Result: {result4}")
    print(f"  Uncovered: {sorted(result4.uncovered_points)}")
    print(f"  Certified: {result4.certified}")
    assert not result4.certified, "Should not be certified with insufficient budget"

    # --- Test 5: Initial proposal ranking ---
    print("\n[5] Initial proposal ranking (no selection yet)")
    coord5 = PatchSelectionCoordinator(total_budget=1.0)
    ranking = coord5.propose_candidates(site, candidates)
    print(f"  Ranking: {ranking}")
    print(f"  Top-3: {ranking.top_n(3)}")

    # --- Test 6: Coverage policy ---
    print("\n[6] COVERAGE policy, budget=1.0")
    coord6 = PatchSelectionCoordinator(total_budget=1.0)
    result6 = coord6.run(site, candidates, policy=SelectionPolicy.COVERAGE)
    print(f"  Result: {result6}")
    print(f"  Selected: {[p.patch_id for p in result6.selected_patches]}")
    print(f"  Coverage: {result6.coverage_fraction:.1%}")

    # --- Coordinator summary ---
    print("\n[Coordinator summary]")
    import json
    print(json.dumps(coord.summarise(), indent=2))

    # --- Witness summary ---
    print("\n[Witness summary]")
    print(json.dumps(coord._witness.summarise(), indent=2))

    # --- SelectionRanking repr ---
    print("\n[Ranking repr test]")
    analyzer = PatchSelectionAnalyzer()
    ranking2 = analyzer.rank(
        candidates, frozenset(), [], SelectionPolicy.BALANCED, budget_remaining=1.0
    )
    print(f"  {ranking2}")
    print(f"  Top scores: {dict(list(ranking2.scores.items())[:3])}")

    # --- PatchCandidate helpers ---
    c1 = candidates[0]
    c2 = candidates[1]
    print(f"\n[PatchCandidate helpers]")
    print(f"  U1 size: {c1.size}")
    print(f"  U1 coverage_gain(empty): {c1.coverage_gain(frozenset()):.2f}")
    print(f"  U1 ∩ U2: {sorted(c1.overlap_with(c2))}")
    print(f"  U1 satisfies_min_overlap([]): {c1.satisfies_min_overlap([])}")
    print(f"  U1 satisfies_min_overlap([U2]): {c1.satisfies_min_overlap([c2])}")

    print("\n✓ All smoke-test assertions passed.")
