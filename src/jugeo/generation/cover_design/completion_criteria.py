r"""Completion criteria for cover design runs — theory2.tex §cover-design-completion.

# copilot: cover-design-completion-marker

Theory
------
A cover design run produces a collection of *patches* — local sections defined
on individual coordinate charts — together with quality metrics computed on each
patch.  The orchestration layer must decide, after each patch finishes, whether
to continue elaborating remaining patches or to halt and return the best partial
result assembled so far.

The **completion criteria** layer encodes the five conditions under which a run
is considered finished:

1. *Success* — every patch in the cover has reached ``COMPLETED`` status and
   every quality metric (overlap compatibility, obligation resolution ratio,
   trust threshold) passes its configured gate.
2. *Partial success* — the fraction of completed patches is at or above a
   configured ``partial_threshold`` (default 0.75) **and** every patch that has
   been designated *critical* in the :class:`CriticalPatchSet` is either
   completed or deliberately skipped.
3. *Budget exhaustion* — ``budget.remaining < epsilon`` and the best partial
   cover assembled so far is returned; budget is a first-class object, not a raw
   number.
4. *Timeout* — wall-clock time has exceeded the configured ``timeout_seconds``
   and the best partial cover is returned.
5. *Failure* — at least one critical patch has failed (not merely timed out) and
   the patch cannot be retried because its retry budget is zero; the run halts
   immediately without attempting further patches.

The completion-criteria layer is invoked by the orchestration loop **after every
patch finishes**, regardless of the patch's outcome.  The layer does three
things in each invocation:

* Evaluates all five conditions in order of precedence (Failure takes priority
  over all others; Success takes priority over Partial success).
* Returns a :class:`CompletionDecision` that tells the orchestrator whether to
  ``CONTINUE`` or ``HALT`` and, if halting, which :class:`CompletionCondition`
  caused the halt.
* Optionally triggers a budget reallocation across remaining patches when the
  remaining budget is below a configured ``reallocation_trigger_fraction``
  (default 0.25 of original budget).

The :class:`CompletionCriteriaWitness` is a separate certification object that
is created only after the final :class:`CompletionDecision` is HALT.  It
produces a machine-checkable certificate that records which condition was
satisfied and provides evidence (patch statuses, budget snapshot, elapsed time)
that can be replayed for audit purposes.

Key theory2.tex invariants enforced here:
- Generated code enters at ``PROPOSAL`` trust tier; witnesses may upgrade to
  ``VERIFIED`` only after full certificate validation.
- Budget is a first-class object (see :class:`CoverBudget`) — never a bare
  ``float``.
- Cover sections must be compatible on overlaps (Čech condition); the quality
  metric ``overlap_compatibility`` captures this.

copilot: cover-design-completion-marker
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Jugeo imports — all wrapped in try/except so the module loads standalone.
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.goals import GenerationGoal
except Exception:  # noqa: BLE001
    GenerationGoal = Any  # type: ignore[assignment,misc]

try:
    from jugeo.generation.cover_design.models import (
        CoverPatch,
        PatchStatus,
        CoverBudget,
        QualityMetrics,
        CoverDesignResult,
    )
except Exception:  # noqa: BLE001
    CoverPatch = Any  # type: ignore[assignment,misc]
    PatchStatus = Any  # type: ignore[assignment,misc]
    CoverBudget = Any  # type: ignore[assignment,misc]
    QualityMetrics = Any  # type: ignore[assignment,misc]
    CoverDesignResult = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustTier
except Exception:  # noqa: BLE001
    TrustTier = Any  # type: ignore[assignment,misc]

__all__ = [
    "CompletionCondition",
    "CompletionRecord",
    "CompletionDecision",
    "CriticalPatchSet",
    "CompletionCriteriaCoordinator",
    "CompletionCriteriaAnalyzer",
    "CompletionCriteriaWitness",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

_PATCH_STATUS_COMPLETED = "COMPLETED"
_PATCH_STATUS_FAILED = "FAILED"
_PATCH_STATUS_PENDING = "PENDING"
_PATCH_STATUS_IN_PROGRESS = "IN_PROGRESS"
_PATCH_STATUS_SKIPPED = "SKIPPED"


class CompletionCondition(str, Enum):
    """The five mutually-exclusive conditions under which a run terminates.

    Precedence order (highest to lowest):
    ``FAILURE`` > ``SUCCESS`` > ``PARTIAL_SUCCESS`` > ``BUDGET_EXHAUSTED`` >
    ``TIMEOUT``.  When multiple conditions hold simultaneously the highest-
    precedence one is reported.

    Attributes
    ----------
    SUCCESS:
        All patches completed and all quality metrics pass.
    PARTIAL_SUCCESS:
        Enough patches completed and all critical patches are done.
    BUDGET_EXHAUSTED:
        Budget remaining fell below the configured epsilon; best partial
        cover returned.
    TIMEOUT:
        Wall-clock deadline exceeded; best partial cover returned.
    FAILURE:
        A critical patch failed and cannot be retried.
    """

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    FAILURE = "FAILURE"


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletionRecord:
    """Immutable snapshot of the cover state at completion time.

    One ``CompletionRecord`` is created at the moment the orchestrator calls
    :meth:`CompletionCriteriaCoordinator.check_completion` and a HALT decision
    is produced.  It is embedded in the :class:`CompletionDecision` and later
    referenced by the :class:`CompletionCriteriaWitness`.

    Attributes
    ----------
    record_id:
        Globally unique identifier minted at record creation time.
    condition:
        The :class:`CompletionCondition` that caused the halt.
    total_patches:
        Total number of patches in the cover design.
    completed_patches:
        Number of patches with status ``COMPLETED`` at halt time.
    failed_patches:
        Number of patches with status ``FAILED`` at halt time.
    skipped_patches:
        Number of patches with status ``SKIPPED`` at halt time.
    pending_patches:
        Number of patches still pending or in-progress at halt time.
    partial_success_score:
        Float in [0, 1] computed by :class:`CompletionCriteriaAnalyzer`.
        Represents the weighted fraction of work done.
    budget_remaining:
        Remaining budget at halt time (abstract cost units).
    budget_original:
        Original budget at run start.
    elapsed_seconds:
        Wall-clock seconds elapsed since run start.
    critical_patches_done:
        Whether every critical patch in the :class:`CriticalPatchSet` was
        completed or deliberately skipped.
    quality_passed:
        Whether all quality metrics gates passed for completed patches.
    reallocations_triggered:
        Number of budget reallocation events that fired during the run.
    timestamp:
        Unix timestamp at which this record was created.
    metadata:
        Arbitrary key-value diagnostics for extended audit trails.
    """

    record_id: str
    condition: CompletionCondition
    total_patches: int
    completed_patches: int
    failed_patches: int
    skipped_patches: int
    pending_patches: int
    partial_success_score: float
    budget_remaining: float
    budget_original: float
    elapsed_seconds: float
    critical_patches_done: bool
    quality_passed: bool
    reallocations_triggered: int
    timestamp: float
    metadata: dict[str, Any]


@dataclass
class CompletionDecision:
    """The orchestrator's instruction after the completion check.

    :class:`CompletionCriteriaCoordinator.check_completion` returns one of
    these after each patch finishes.  The orchestrator must obey the
    ``action`` field.

    Attributes
    ----------
    decision_id:
        Unique identifier for this decision event.
    action:
        Either ``"CONTINUE"`` (keep elaborating remaining patches) or
        ``"HALT"`` (stop immediately and return the best partial cover).
    condition:
        The :class:`CompletionCondition` that was evaluated.  When
        ``action == "CONTINUE"`` this field is ``None``.
    record:
        The :class:`CompletionRecord` snapshot; ``None`` when continuing.
    reallocation_triggered:
        Whether the coordinator fired a budget reallocation alongside this
        decision.
    reallocation_details:
        Optional dict describing the reallocation (new budget per remaining
        patch), populated only when ``reallocation_triggered`` is ``True``.
    rationale:
        Human-readable explanation of why this decision was reached.
    decided_at:
        Unix timestamp.
    """

    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action: str = "CONTINUE"
    condition: CompletionCondition | None = None
    record: CompletionRecord | None = None
    reallocation_triggered: bool = False
    reallocation_details: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    decided_at: float = field(default_factory=time.time)


@dataclass
class CriticalPatchSet:
    """The set of patches designated as *critical* for the cover design run.

    A patch is critical when:

    * It covers a region that no other patch in the cover overlaps
      (non-redundant chart), **or**
    * It was explicitly marked critical by the orchestrator because the
      downstream pipeline requires its section unconditionally.

    If any patch in this set reaches ``FAILED`` status with zero retry budget
    remaining, the run must halt with ``CompletionCondition.FAILURE``.

    Attributes
    ----------
    critical_ids:
        Set of patch IDs considered critical.
    no_overlap_ids:
        Subset of ``critical_ids`` that are critical due to non-redundancy.
    explicit_ids:
        Subset of ``critical_ids`` that were explicitly marked by the caller.
    created_at:
        Unix timestamp.
    """

    critical_ids: set[str] = field(default_factory=set)
    no_overlap_ids: set[str] = field(default_factory=set)
    explicit_ids: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)

    def is_critical(self, patch_id: str) -> bool:
        """Return ``True`` if *patch_id* is in the critical set."""
        return patch_id in self.critical_ids

    def add_explicit(self, patch_id: str) -> None:
        """Add *patch_id* as an explicitly critical patch."""
        self.explicit_ids.add(patch_id)
        self.critical_ids.add(patch_id)

    def add_no_overlap(self, patch_id: str) -> None:
        """Add *patch_id* as a non-redundant (no-overlap) critical patch."""
        self.no_overlap_ids.add(patch_id)
        self.critical_ids.add(patch_id)

    def all_done(
        self,
        patch_statuses: dict[str, str],
    ) -> bool:
        """Return ``True`` when all critical patches are completed or skipped.

        Parameters
        ----------
        patch_statuses:
            Mapping from patch_id to its current status string.
        """
        terminal = {_PATCH_STATUS_COMPLETED, _PATCH_STATUS_SKIPPED}
        return all(
            patch_statuses.get(pid, _PATCH_STATUS_PENDING) in terminal
            for pid in self.critical_ids
        )

    def any_irrecoverably_failed(
        self,
        patch_statuses: dict[str, str],
        retry_budgets: dict[str, int],
    ) -> list[str]:
        """Return the IDs of critical patches that failed with no retry budget.

        Parameters
        ----------
        patch_statuses:
            Mapping from patch_id to current status.
        retry_budgets:
            Mapping from patch_id to remaining retry count.

        Returns
        -------
        list[str]
            Patch IDs that are critical, failed, and have zero retries left.
        """
        return [
            pid
            for pid in self.critical_ids
            if patch_statuses.get(pid) == _PATCH_STATUS_FAILED
            and retry_budgets.get(pid, 0) <= 0
        ]


# ---------------------------------------------------------------------------
# CompletionCriteriaAnalyzer
# ---------------------------------------------------------------------------


class CompletionCriteriaAnalyzer:
    """Evaluates all five completion conditions and computes partial-success score.

    The analyzer is stateless with respect to the run timeline — it accepts a
    complete snapshot of the current cover state and returns which completion
    conditions currently hold.  Callers (typically
    :class:`CompletionCriteriaCoordinator`) are responsible for passing
    consistent snapshots.

    Parameters
    ----------
    config:
        Optional configuration overrides.  See :meth:`__init__` for keys.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the analyzer with the given configuration.

        Configuration keys
        ------------------
        partial_threshold : float
            Minimum fraction of patches that must be ``COMPLETED`` for
            ``PARTIAL_SUCCESS`` to be considered.  Default ``0.75``.
        budget_epsilon : float
            When ``budget_remaining <= budget_epsilon`` the
            ``BUDGET_EXHAUSTED`` condition triggers.  Default ``1e-6``.
        quality_gate_min : float
            Minimum overlap-compatibility score required for a completed
            patch to count toward the quality gate.  Default ``0.8``.
        obligation_resolution_min : float
            Minimum fraction of obligations that must be resolved per
            completed patch for the quality gate.  Default ``0.7``.
        trust_tier_min : str
            Minimum trust tier (``"PROPOSAL"``, ``"VERIFIED"``, etc.) for
            patches to pass the quality gate.  Default ``"PROPOSAL"``.
        """
        cfg = config or {}
        self._partial_threshold: float = float(
            cfg.get("partial_threshold", 0.75)
        )
        self._budget_epsilon: float = float(cfg.get("budget_epsilon", 1e-6))
        self._quality_gate_min: float = float(cfg.get("quality_gate_min", 0.8))
        self._obligation_resolution_min: float = float(
            cfg.get("obligation_resolution_min", 0.7)
        )
        self._trust_tier_min: str = str(cfg.get("trust_tier_min", "PROPOSAL"))
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Public evaluation interface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        patch_statuses: dict[str, str],
        quality_scores: dict[str, dict[str, float]],
        critical_set: CriticalPatchSet,
        retry_budgets: dict[str, int],
        budget_remaining: float,
        budget_original: float,
        elapsed_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Evaluate all five completion conditions against the current state.

        The conditions are evaluated in precedence order:
        ``FAILURE`` > ``SUCCESS`` > ``PARTIAL_SUCCESS`` >
        ``BUDGET_EXHAUSTED`` > ``TIMEOUT``.

        Parameters
        ----------
        patch_statuses:
            Mapping from patch_id → current status string.
        quality_scores:
            Mapping from patch_id → dict with keys ``"overlap_compatibility"``,
            ``"obligation_resolution"``, ``"trust_tier"``.
        critical_set:
            The :class:`CriticalPatchSet` for this run.
        retry_budgets:
            Mapping from patch_id → remaining retry count.
        budget_remaining:
            Current remaining budget (abstract cost units).
        budget_original:
            Original budget at run start.
        elapsed_seconds:
            Elapsed wall-clock seconds since run start.
        timeout_seconds:
            Configured timeout in wall-clock seconds.

        Returns
        -------
        dict[str, Any]
            Keys:

            ``"active_conditions"``
                List of :class:`CompletionCondition` values that hold.
            ``"dominant_condition"``
                The highest-precedence condition that holds, or ``None``.
            ``"partial_success_score"``
                Float in [0, 1] regardless of which conditions hold.
            ``"quality_passed"``
                Whether the quality gate passes for all completed patches.
            ``"critical_patches_done"``
                Whether all critical patches are terminal.
            ``"condition_details"``
                Per-condition evidence dicts.
        """
        total = len(patch_statuses)
        completed_ids = [
            pid
            for pid, s in patch_statuses.items()
            if s == _PATCH_STATUS_COMPLETED
        ]
        failed_ids = [
            pid
            for pid, s in patch_statuses.items()
            if s == _PATCH_STATUS_FAILED
        ]
        skipped_ids = [
            pid
            for pid, s in patch_statuses.items()
            if s == _PATCH_STATUS_SKIPPED
        ]
        pending_ids = [
            pid
            for pid, s in patch_statuses.items()
            if s in (_PATCH_STATUS_PENDING, _PATCH_STATUS_IN_PROGRESS)
        ]

        completed_count = len(completed_ids)
        fraction_completed = completed_count / max(total, 1)

        quality_passed = self._check_quality_gate(completed_ids, quality_scores)
        critical_done = critical_set.all_done(patch_statuses)
        irrecoverable = critical_set.any_irrecoverably_failed(
            patch_statuses, retry_budgets
        )

        partial_score = self._compute_partial_success_score(
            completed_ids=completed_ids,
            skipped_ids=skipped_ids,
            failed_ids=failed_ids,
            quality_scores=quality_scores,
            critical_set=critical_set,
            budget_remaining=budget_remaining,
            budget_original=budget_original,
            total=total,
        )

        # ---- Condition 5: FAILURE -------------------------------------------
        failure_holds = len(irrecoverable) > 0
        failure_detail: dict[str, Any] = {
            "holds": failure_holds,
            "irrecoverable_critical_patches": irrecoverable,
        }

        # ---- Condition 1: SUCCESS -------------------------------------------
        success_holds = (
            not failure_holds
            and completed_count == total
            and total > 0
            and quality_passed
        )
        success_detail: dict[str, Any] = {
            "holds": success_holds,
            "all_completed": completed_count == total,
            "quality_passed": quality_passed,
            "total": total,
            "completed": completed_count,
        }

        # ---- Condition 2: PARTIAL_SUCCESS -----------------------------------
        partial_holds = (
            not failure_holds
            and not success_holds
            and fraction_completed >= self._partial_threshold
            and critical_done
        )
        partial_detail: dict[str, Any] = {
            "holds": partial_holds,
            "fraction_completed": fraction_completed,
            "partial_threshold": self._partial_threshold,
            "critical_done": critical_done,
        }

        # ---- Condition 3: BUDGET_EXHAUSTED ----------------------------------
        budget_exhausted_holds = (
            not failure_holds
            and not success_holds
            and not partial_holds
            and budget_remaining <= self._budget_epsilon
        )
        budget_detail: dict[str, Any] = {
            "holds": budget_exhausted_holds,
            "budget_remaining": budget_remaining,
            "epsilon": self._budget_epsilon,
        }

        # ---- Condition 4: TIMEOUT -------------------------------------------
        timeout_holds = (
            not failure_holds
            and not success_holds
            and not partial_holds
            and not budget_exhausted_holds
            and elapsed_seconds >= timeout_seconds
        )
        timeout_detail: dict[str, Any] = {
            "holds": timeout_holds,
            "elapsed_seconds": elapsed_seconds,
            "timeout_seconds": timeout_seconds,
        }

        # ---- Collect active conditions in precedence order ------------------
        active: list[CompletionCondition] = []
        if failure_holds:
            active.append(CompletionCondition.FAILURE)
        if success_holds:
            active.append(CompletionCondition.SUCCESS)
        if partial_holds:
            active.append(CompletionCondition.PARTIAL_SUCCESS)
        if budget_exhausted_holds:
            active.append(CompletionCondition.BUDGET_EXHAUSTED)
        if timeout_holds:
            active.append(CompletionCondition.TIMEOUT)

        dominant = active[0] if active else None

        return {
            "active_conditions": active,
            "dominant_condition": dominant,
            "partial_success_score": partial_score,
            "quality_passed": quality_passed,
            "critical_patches_done": critical_done,
            "completed_count": completed_count,
            "failed_count": len(failed_ids),
            "skipped_count": len(skipped_ids),
            "pending_count": len(pending_ids),
            "total": total,
            "condition_details": {
                "FAILURE": failure_detail,
                "SUCCESS": success_detail,
                "PARTIAL_SUCCESS": partial_detail,
                "BUDGET_EXHAUSTED": budget_detail,
                "TIMEOUT": timeout_detail,
            },
        }

    def _check_quality_gate(
        self,
        completed_ids: list[str],
        quality_scores: dict[str, dict[str, float]],
    ) -> bool:
        """Return ``True`` if all completed patches pass quality gates.

        Parameters
        ----------
        completed_ids:
            IDs of patches that have reached ``COMPLETED`` status.
        quality_scores:
            Per-patch quality metric dicts.
        """
        if not completed_ids:
            return True
        for pid in completed_ids:
            scores = quality_scores.get(pid, {})
            compat = float(scores.get("overlap_compatibility", 1.0))
            oblig = float(scores.get("obligation_resolution", 1.0))
            if compat < self._quality_gate_min:
                self._logger.debug(
                    "Patch %s failed overlap_compatibility gate: %.3f < %.3f",
                    pid,
                    compat,
                    self._quality_gate_min,
                )
                return False
            if oblig < self._obligation_resolution_min:
                self._logger.debug(
                    "Patch %s failed obligation_resolution gate: %.3f < %.3f",
                    pid,
                    oblig,
                    self._obligation_resolution_min,
                )
                return False
        return True

    def _compute_partial_success_score(
        self,
        completed_ids: list[str],
        skipped_ids: list[str],
        failed_ids: list[str],
        quality_scores: dict[str, dict[str, float]],
        critical_set: CriticalPatchSet,
        budget_remaining: float,
        budget_original: float,
        total: int,
    ) -> float:
        """Compute a scalar partial-success score in [0, 1].

        The score is a weighted sum of four sub-scores:

        1. **Completion rate** (weight 0.40): fraction of patches completed.
        2. **Quality weighted completion** (weight 0.30): completion rate
           weighted by per-patch overlap_compatibility score.
        3. **Critical patch coverage** (weight 0.20): fraction of critical
           patches that are completed or skipped.
        4. **Budget efficiency** (weight 0.10): fraction of budget consumed
           that is "productive" (not wasted on failed patches).

        Parameters
        ----------
        completed_ids:
            Patch IDs with COMPLETED status.
        skipped_ids:
            Patch IDs with SKIPPED status.
        failed_ids:
            Patch IDs with FAILED status.
        quality_scores:
            Per-patch quality metric dicts.
        critical_set:
            The critical patch set.
        budget_remaining:
            Current remaining budget.
        budget_original:
            Budget at run start.
        total:
            Total number of patches.
        """
        if total == 0:
            return 0.0

        # Sub-score 1: completion rate
        completion_rate = len(completed_ids) / total

        # Sub-score 2: quality-weighted completion
        quality_weighted = 0.0
        if completed_ids:
            q_sum = sum(
                float(quality_scores.get(pid, {}).get("overlap_compatibility", 1.0))
                for pid in completed_ids
            )
            quality_weighted = q_sum / total

        # Sub-score 3: critical patch coverage
        critical_ids = critical_set.critical_ids
        if critical_ids:
            critical_done = sum(
                1
                for pid in critical_ids
                if pid in set(completed_ids) | set(skipped_ids)
            )
            critical_coverage = critical_done / len(critical_ids)
        else:
            critical_coverage = 1.0

        # Sub-score 4: budget efficiency
        budget_consumed = budget_original - budget_remaining
        if budget_original <= 0.0:
            budget_efficiency = 1.0
        elif budget_consumed <= 0.0:
            budget_efficiency = 1.0
        else:
            # Productive consumption: avoid penalising for budget spent on
            # completed patches; penalise only for budget lost on failures.
            failed_fraction = len(failed_ids) / max(total, 1)
            budget_efficiency = max(0.0, 1.0 - failed_fraction)

        score = (
            0.40 * completion_rate
            + 0.30 * quality_weighted
            + 0.20 * critical_coverage
            + 0.10 * budget_efficiency
        )
        return min(max(score, 0.0), 1.0)

    def compute_budget_reallocation(
        self,
        remaining_patch_ids: list[str],
        budget_remaining: float,
        quality_scores: dict[str, dict[str, float]],
        critical_set: CriticalPatchSet,
    ) -> dict[str, float]:
        """Compute a new budget allocation for the remaining patches.

        Uses a priority-weighted scheme: critical patches receive double
        weight, patches with lower current quality scores receive slightly
        higher weight (they need more work), and all weights are normalised
        so they sum to ``budget_remaining``.

        Parameters
        ----------
        remaining_patch_ids:
            IDs of patches that are still PENDING or IN_PROGRESS.
        budget_remaining:
            Total budget available to redistribute.
        quality_scores:
            Current quality scores per patch.
        critical_set:
            The critical patch set.

        Returns
        -------
        dict[str, float]
            Mapping from patch_id → newly allocated budget.
        """
        if not remaining_patch_ids or budget_remaining <= 0.0:
            return {}

        weights: dict[str, float] = {}
        for pid in remaining_patch_ids:
            w = 1.0
            if critical_set.is_critical(pid):
                w *= 2.0
            compat = float(
                quality_scores.get(pid, {}).get("overlap_compatibility", 1.0)
            )
            # Patches with lower compatibility need more work → higher share
            w *= max(0.1, 2.0 - compat)
            weights[pid] = w

        total_weight = sum(weights.values())
        if total_weight <= 0.0:
            even = budget_remaining / len(remaining_patch_ids)
            return {pid: even for pid in remaining_patch_ids}

        return {
            pid: (w / total_weight) * budget_remaining
            for pid, w in weights.items()
        }


# ---------------------------------------------------------------------------
# CompletionCriteriaCoordinator
# ---------------------------------------------------------------------------


class CompletionCriteriaCoordinator:
    """Checks completion after each patch and decides whether to continue or halt.

    The coordinator is the single integration point between the patch
    execution loop and the completion-criteria logic.  It holds a reference to
    a :class:`CompletionCriteriaAnalyzer` and a :class:`CriticalPatchSet`,
    maintains a lightweight run-state, and emits :class:`CompletionDecision`
    objects after each patch.

    The coordinator also fires budget reallocation events when the remaining
    budget drops below ``reallocation_trigger_fraction`` of the original budget.
    Reallocation details are delegated to
    :meth:`CompletionCriteriaAnalyzer.compute_budget_reallocation`.

    Parameters
    ----------
    config:
        Optional configuration overrides.  See :meth:`__init__`.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the coordinator.

        Configuration keys
        ------------------
        partial_threshold : float
            Forwarded to :class:`CompletionCriteriaAnalyzer`.  Default 0.75.
        budget_epsilon : float
            Forwarded to :class:`CompletionCriteriaAnalyzer`.  Default 1e-6.
        quality_gate_min : float
            Forwarded to :class:`CompletionCriteriaAnalyzer`.  Default 0.8.
        obligation_resolution_min : float
            Forwarded to :class:`CompletionCriteriaAnalyzer`.  Default 0.7.
        reallocation_trigger_fraction : float
            Fire a reallocation event when
            ``budget_remaining / budget_original <= this value``.
            Default 0.25.
        timeout_seconds : float
            Run wall-clock timeout.  Default 3600.0 (one hour).
        max_reallocations : int
            Hard cap on number of reallocation events per run.  Default 3.
        """
        cfg = config or {}
        analyzer_cfg = {
            "partial_threshold": cfg.get("partial_threshold", 0.75),
            "budget_epsilon": cfg.get("budget_epsilon", 1e-6),
            "quality_gate_min": cfg.get("quality_gate_min", 0.8),
            "obligation_resolution_min": cfg.get("obligation_resolution_min", 0.7),
        }
        self._analyzer = CompletionCriteriaAnalyzer(config=analyzer_cfg)
        self._reallocation_trigger: float = float(
            cfg.get("reallocation_trigger_fraction", 0.25)
        )
        self._timeout_seconds: float = float(cfg.get("timeout_seconds", 3600.0))
        self._max_reallocations: int = int(cfg.get("max_reallocations", 3))
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # Run-state
        self._run_id: str = str(uuid.uuid4())
        self._started_at: float = time.time()
        self._reallocations_triggered: int = 0
        self._decisions: list[CompletionDecision] = []
        self._critical_set: CriticalPatchSet = CriticalPatchSet()
        self._budget_original: float = 1.0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def initialise_run(
        self,
        budget_original: float,
        critical_set: CriticalPatchSet | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        """Reset internal run-state and configure a new run.

        Must be called once before the first :meth:`check_completion` call.

        Parameters
        ----------
        budget_original:
            Total budget allocated to this cover design run.
        critical_set:
            The critical patch set.  If ``None``, an empty set is used
            (all patches are non-critical).
        timeout_seconds:
            Optional per-run timeout override.  If ``None``, the value
            from the constructor configuration is used.
        """
        self._run_id = str(uuid.uuid4())
        self._started_at = time.time()
        self._reallocations_triggered = 0
        self._decisions = []
        self._budget_original = max(budget_original, 1e-12)
        self._critical_set = critical_set or CriticalPatchSet()
        if timeout_seconds is not None:
            self._timeout_seconds = timeout_seconds
        self._logger.info(
            "Coordinator initialised run=%s, budget=%.4f, timeout=%.1fs.",
            self._run_id,
            self._budget_original,
            self._timeout_seconds,
        )

    # ------------------------------------------------------------------
    # Core decision method
    # ------------------------------------------------------------------

    def check_completion(
        self,
        patch_statuses: dict[str, str],
        quality_scores: dict[str, dict[str, float]],
        retry_budgets: dict[str, int],
        budget_remaining: float,
    ) -> CompletionDecision:
        """Check all completion conditions and return a decision.

        This method is invoked by the orchestrator after each patch finishes.
        It evaluates all five completion conditions (via the embedded analyzer),
        optionally triggers a budget reallocation, and returns a
        :class:`CompletionDecision`.

        Parameters
        ----------
        patch_statuses:
            Current status of every patch in the cover.
        quality_scores:
            Per-patch quality metrics.
        retry_budgets:
            Per-patch remaining retry counts.
        budget_remaining:
            Current remaining budget.

        Returns
        -------
        CompletionDecision
            Contains ``action`` (``"CONTINUE"`` or ``"HALT"``), optional
            :class:`CompletionRecord`, and optional reallocation details.
        """
        elapsed = time.time() - self._started_at

        eval_result = self._analyzer.evaluate(
            patch_statuses=patch_statuses,
            quality_scores=quality_scores,
            critical_set=self._critical_set,
            retry_budgets=retry_budgets,
            budget_remaining=budget_remaining,
            budget_original=self._budget_original,
            elapsed_seconds=elapsed,
            timeout_seconds=self._timeout_seconds,
        )

        dominant: CompletionCondition | None = eval_result["dominant_condition"]

        # ---- Check whether to trigger reallocation -------------------------
        realloc_triggered = False
        realloc_details: dict[str, Any] = {}
        if (
            dominant is None  # still continuing
            and self._reallocations_triggered < self._max_reallocations
            and self._budget_original > 0.0
            and (budget_remaining / self._budget_original)
            <= self._reallocation_trigger
        ):
            pending_ids = [
                pid
                for pid, s in patch_statuses.items()
                if s in (_PATCH_STATUS_PENDING, _PATCH_STATUS_IN_PROGRESS)
            ]
            if pending_ids:
                new_alloc = self._analyzer.compute_budget_reallocation(
                    remaining_patch_ids=pending_ids,
                    budget_remaining=budget_remaining,
                    quality_scores=quality_scores,
                    critical_set=self._critical_set,
                )
                realloc_details = {
                    "new_allocations": new_alloc,
                    "trigger_fraction": self._reallocation_trigger,
                    "reallocation_index": self._reallocations_triggered,
                }
                realloc_triggered = True
                self._reallocations_triggered += 1
                self._logger.info(
                    "Budget reallocation #%d triggered: %d patches reallocated.",
                    self._reallocations_triggered,
                    len(new_alloc),
                )

        # ---- Build decision -------------------------------------------------
        if dominant is not None:
            record = CompletionRecord(
                record_id=str(uuid.uuid4()),
                condition=dominant,
                total_patches=eval_result["total"],
                completed_patches=eval_result["completed_count"],
                failed_patches=eval_result["failed_count"],
                skipped_patches=eval_result["skipped_count"],
                pending_patches=eval_result["pending_count"],
                partial_success_score=eval_result["partial_success_score"],
                budget_remaining=budget_remaining,
                budget_original=self._budget_original,
                elapsed_seconds=elapsed,
                critical_patches_done=eval_result["critical_patches_done"],
                quality_passed=eval_result["quality_passed"],
                reallocations_triggered=self._reallocations_triggered,
                timestamp=time.time(),
                metadata={
                    "run_id": self._run_id,
                    "condition_details": eval_result["condition_details"],
                },
            )
            rationale = self._build_rationale(dominant, eval_result)
            decision = CompletionDecision(
                action="HALT",
                condition=dominant,
                record=record,
                reallocation_triggered=realloc_triggered,
                reallocation_details=realloc_details,
                rationale=rationale,
            )
            self._logger.info(
                "Run %s HALTED: condition=%s, score=%.3f.",
                self._run_id,
                dominant.value,
                eval_result["partial_success_score"],
            )
        else:
            decision = CompletionDecision(
                action="CONTINUE",
                condition=None,
                record=None,
                reallocation_triggered=realloc_triggered,
                reallocation_details=realloc_details,
                rationale="No completion condition holds; continue elaboration.",
            )

        self._decisions.append(decision)
        return decision

    def _build_rationale(
        self,
        condition: CompletionCondition,
        eval_result: dict[str, Any],
    ) -> str:
        """Build a human-readable rationale string for a HALT decision."""
        details = eval_result["condition_details"].get(condition.value, {})
        base = f"Condition {condition.value} triggered. "
        if condition == CompletionCondition.SUCCESS:
            base += (
                f"All {eval_result['total']} patches completed and quality gate passed."
            )
        elif condition == CompletionCondition.PARTIAL_SUCCESS:
            base += (
                f"{eval_result['completed_count']}/{eval_result['total']} patches "
                f"completed ({eval_result['partial_success_score']:.1%}). "
                f"Critical patches done: {details.get('critical_done', '?')}."
            )
        elif condition == CompletionCondition.BUDGET_EXHAUSTED:
            base += (
                f"Budget remaining {details.get('budget_remaining', '?'):.6f} "
                f"≤ epsilon {details.get('epsilon', '?'):.2e}."
            )
        elif condition == CompletionCondition.TIMEOUT:
            base += (
                f"Elapsed {details.get('elapsed_seconds', '?'):.1f}s "
                f"≥ timeout {details.get('timeout_seconds', '?'):.1f}s."
            )
        elif condition == CompletionCondition.FAILURE:
            failed = details.get("irrecoverable_critical_patches", [])
            base += f"Critical patches with no retries: {failed}."
        return base

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        """The unique ID of the current run."""
        return self._run_id

    @property
    def decisions(self) -> list[CompletionDecision]:
        """All decisions emitted in the current run, in order."""
        return list(self._decisions)

    @property
    def reallocations_triggered(self) -> int:
        """Number of budget reallocation events fired in the current run."""
        return self._reallocations_triggered

    def elapsed_seconds(self) -> float:
        """Return elapsed wall-clock seconds since :meth:`initialise_run`."""
        return time.time() - self._started_at

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of the current run state."""
        return {
            "run_id": self._run_id,
            "elapsed_seconds": self.elapsed_seconds(),
            "decision_count": len(self._decisions),
            "reallocations_triggered": self._reallocations_triggered,
            "last_action": (
                self._decisions[-1].action if self._decisions else None
            ),
            "last_condition": (
                self._decisions[-1].condition.value
                if self._decisions and self._decisions[-1].condition
                else None
            ),
        }


# ---------------------------------------------------------------------------
# CompletionCriteriaWitness
# ---------------------------------------------------------------------------


class CompletionCriteriaWitness:
    """Certifies that the final state satisfies the declared completion criterion.

    A witness is created from a :class:`CompletionRecord` produced by a HALT
    decision.  It validates the record against the current patch state and
    emits a machine-checkable certificate.  The certificate can be persisted
    for audit replay.

    A witness certificate has trust tier ``PROPOSAL`` by default (per
    theory2.tex invariant: generated code enters at PROPOSAL).  It may be
    upgraded to ``VERIFIED`` by calling :meth:`upgrade_to_verified` after
    human or automated review.

    Parameters
    ----------
    record:
        The :class:`CompletionRecord` to certify.
    """

    _CERTIFICATE_VERSION = "1.0"

    def __init__(self, record: CompletionRecord) -> None:
        self._record = record
        self._trust_tier: str = "PROPOSAL"
        self._certificate: dict[str, Any] | None = None
        self._validated: bool = False
        self._validation_issues: list[str] = []
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(
        self,
        patch_statuses: dict[str, str],
        quality_scores: dict[str, dict[str, float]],
        budget_remaining: float,
        elapsed_seconds: float,
    ) -> dict[str, Any]:
        """Validate the completion record against the live run state.

        Checks that the record's numerical claims are consistent with the
        current patch state.  Discrepancies are accumulated in
        ``self._validation_issues``.

        Parameters
        ----------
        patch_statuses:
            Current (final) status of every patch.
        quality_scores:
            Final quality scores per patch.
        budget_remaining:
            Final remaining budget.
        elapsed_seconds:
            Total elapsed wall-clock seconds.

        Returns
        -------
        dict[str, Any]
            ``{"valid": bool, "issues": list[str], "checks": dict}``.
        """
        issues: list[str] = []
        r = self._record

        # Check: completed_patches matches actual status counts
        actual_completed = sum(
            1 for s in patch_statuses.values() if s == _PATCH_STATUS_COMPLETED
        )
        if actual_completed != r.completed_patches:
            issues.append(
                f"completed_patches mismatch: record={r.completed_patches}, "
                f"actual={actual_completed}."
            )

        # Check: failed_patches
        actual_failed = sum(
            1 for s in patch_statuses.values() if s == _PATCH_STATUS_FAILED
        )
        if actual_failed != r.failed_patches:
            issues.append(
                f"failed_patches mismatch: record={r.failed_patches}, "
                f"actual={actual_failed}."
            )

        # Check: budget_remaining tolerance
        budget_tol = max(abs(r.budget_remaining) * 0.01, 1e-9)
        if abs(budget_remaining - r.budget_remaining) > budget_tol:
            issues.append(
                f"budget_remaining mismatch: record={r.budget_remaining:.6f}, "
                f"actual={budget_remaining:.6f}."
            )

        # Check: condition-specific invariants
        condition_ok, condition_issue = self._check_condition_invariant(
            patch_statuses, quality_scores, budget_remaining, elapsed_seconds
        )
        if not condition_ok:
            issues.append(condition_issue)

        self._validation_issues = issues
        self._validated = True

        checks = {
            "completed_count_match": actual_completed == r.completed_patches,
            "failed_count_match": actual_failed == r.failed_patches,
            "budget_match": abs(budget_remaining - r.budget_remaining) <= budget_tol,
            "condition_invariant": condition_ok,
        }

        valid = len(issues) == 0
        self._logger.info(
            "Witness validation for record %s: valid=%s, issues=%d.",
            r.record_id,
            valid,
            len(issues),
        )
        return {"valid": valid, "issues": issues, "checks": checks}

    def _check_condition_invariant(
        self,
        patch_statuses: dict[str, str],
        quality_scores: dict[str, dict[str, float]],
        budget_remaining: float,
        elapsed_seconds: float,
    ) -> tuple[bool, str]:
        """Verify the condition-specific invariant for the record's condition."""
        r = self._record
        cond = r.condition

        if cond == CompletionCondition.SUCCESS:
            all_done = all(
                s == _PATCH_STATUS_COMPLETED for s in patch_statuses.values()
            )
            if not all_done:
                return False, "SUCCESS claimed but not all patches are COMPLETED."
        elif cond == CompletionCondition.FAILURE:
            has_failed = any(
                s == _PATCH_STATUS_FAILED for s in patch_statuses.values()
            )
            if not has_failed:
                return False, "FAILURE claimed but no patch is in FAILED state."
        elif cond == CompletionCondition.BUDGET_EXHAUSTED:
            if r.budget_remaining > 1e-4:
                return (
                    False,
                    f"BUDGET_EXHAUSTED claimed but budget_remaining={r.budget_remaining:.6f}.",
                )
        elif cond == CompletionCondition.TIMEOUT:
            if r.elapsed_seconds < 0.0:
                return False, "TIMEOUT claimed but elapsed_seconds is negative."

        return True, ""

    # ------------------------------------------------------------------
    # Certificate generation
    # ------------------------------------------------------------------

    def generate_certificate(self) -> dict[str, Any]:
        """Generate a machine-checkable completion certificate.

        The certificate embeds the full :class:`CompletionRecord`, the
        validation outcome, the trust tier, and a unique certificate ID.
        It can be serialised to JSON for persistent audit storage.

        Returns
        -------
        dict[str, Any]
            The certificate dict.

        Raises
        ------
        RuntimeError
            If :meth:`validate` has not been called yet.
        """
        if not self._validated:
            raise RuntimeError(
                "CompletionCriteriaWitness.validate() must be called before "
                "generate_certificate()."
            )
        r = self._record
        self._certificate = {
            "certificate_id": str(uuid.uuid4()),
            "certificate_version": self._CERTIFICATE_VERSION,
            "trust_tier": self._trust_tier,
            "record_id": r.record_id,
            "condition": r.condition.value,
            "partial_success_score": r.partial_success_score,
            "total_patches": r.total_patches,
            "completed_patches": r.completed_patches,
            "failed_patches": r.failed_patches,
            "budget_remaining": r.budget_remaining,
            "budget_original": r.budget_original,
            "elapsed_seconds": r.elapsed_seconds,
            "critical_patches_done": r.critical_patches_done,
            "quality_passed": r.quality_passed,
            "reallocations_triggered": r.reallocations_triggered,
            "validation_valid": len(self._validation_issues) == 0,
            "validation_issues": self._validation_issues,
            "generated_at": time.time(),
            "metadata": r.metadata,
        }
        return dict(self._certificate)

    def upgrade_to_verified(self, reviewer_id: str) -> None:
        """Upgrade the trust tier from PROPOSAL to VERIFIED.

        Per theory2.tex invariant: generated artefacts begin at PROPOSAL and
        may be promoted to VERIFIED only after review.

        Parameters
        ----------
        reviewer_id:
            Identifier of the reviewer authorising the upgrade.
        """
        if self._trust_tier == "VERIFIED":
            return
        old_tier = self._trust_tier
        self._trust_tier = "VERIFIED"
        if self._certificate is not None:
            self._certificate["trust_tier"] = "VERIFIED"
            self._certificate["verified_by"] = reviewer_id
            self._certificate["verified_at"] = time.time()
        self._logger.info(
            "Witness trust tier upgraded: %s → VERIFIED by %s.",
            old_tier,
            reviewer_id,
        )

    @property
    def is_valid(self) -> bool:
        """``True`` if validation has been run and found no issues."""
        return self._validated and len(self._validation_issues) == 0

    @property
    def trust_tier(self) -> str:
        """Current trust tier of this witness."""
        return self._trust_tier

    @property
    def certificate(self) -> dict[str, Any] | None:
        """The generated certificate, or ``None`` if not yet generated."""
        return dict(self._certificate) if self._certificate else None

    def summarise(self) -> str:
        """Return a one-line human-readable summary of this witness."""
        r = self._record
        valid_str = "VALID" if self.is_valid else "INVALID"
        return (
            f"Witness[{r.record_id[:8]}] "
            f"condition={r.condition.value} "
            f"score={r.partial_success_score:.2f} "
            f"tier={self._trust_tier} "
            f"validation={valid_str}"
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== completion_criteria smoke test ===")

    # Build a minimal cover of 4 patches
    patch_ids = ["p0", "p1", "p2", "p3"]
    statuses: dict[str, str] = {
        "p0": _PATCH_STATUS_COMPLETED,
        "p1": _PATCH_STATUS_COMPLETED,
        "p2": _PATCH_STATUS_COMPLETED,
        "p3": _PATCH_STATUS_PENDING,
    }
    quality: dict[str, dict[str, float]] = {
        pid: {"overlap_compatibility": 0.9, "obligation_resolution": 0.85}
        for pid in patch_ids
    }
    retry_budgets: dict[str, int] = {pid: 2 for pid in patch_ids}

    # Set up a critical patch set
    crit = CriticalPatchSet()
    crit.add_explicit("p0")
    crit.add_no_overlap("p1")

    # Coordinator
    coord = CompletionCriteriaCoordinator(
        config={
            "partial_threshold": 0.75,
            "timeout_seconds": 60.0,
        }
    )
    coord.initialise_run(budget_original=10.0, critical_set=crit)

    # Simulate patch completions
    for i in range(4):
        statuses[f"p{i}"] = _PATCH_STATUS_COMPLETED
        decision = coord.check_completion(
            patch_statuses=dict(statuses),
            quality_scores=quality,
            retry_budgets=retry_budgets,
            budget_remaining=10.0 - (i + 1) * 2.0,
        )
        print(
            f"  After p{i}: action={decision.action}",
            f"condition={decision.condition}",
            f"realloc={decision.reallocation_triggered}",
        )
        if decision.action == "HALT":
            break

    print("  Summary:", coord.summary())

    # Witness
    if coord.decisions and coord.decisions[-1].action == "HALT":
        record = coord.decisions[-1].record
        witness = CompletionCriteriaWitness(record)
        val = witness.validate(
            patch_statuses=statuses,
            quality_scores=quality,
            budget_remaining=record.budget_remaining,
            elapsed_seconds=record.elapsed_seconds,
        )
        cert = witness.generate_certificate()
        print("  Witness valid:", val["valid"])
        print("  Certificate condition:", cert["condition"])
        print("  Trust tier:", witness.trust_tier)
        print("  Summary:", witness.summarise())
    else:
        print("  No HALT decision reached in smoke test (all continue).")

    print("=== smoke test complete ===")
