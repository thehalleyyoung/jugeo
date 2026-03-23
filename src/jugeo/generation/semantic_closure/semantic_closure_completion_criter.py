r"""Completion criteria for semantic closure.

Theory (theory2.tex §38.3 — Completion criteria):
    *Semantic closure completion* is the formal predicate that determines
    when a construction is considered complete with respect to its obligation
    set and evidence pool.  Completion is a multi-criteria judgment, not a
    single Boolean:

        complete(construction) ⟺
            ∀ k ∈ K_required, C_k(construction) ∧
            ∃ witness W : valid(W, construction)

    Each criterion C_k is associated with a metric M_k and a threshold θ_k:

        C_k satisfied ⟺ M_k(construction) ≥ θ_k

    The default criteria set K comprises:

    * C_COV  — *Coverage completeness*: fraction of obligations that are closed.
    * C_OBL  — *Obligation discharge*: all mandatory obligations are discharged.
    * C_BUD  — *Budget compliance*: budget used ≤ budget allocated.
    * C_TRU  — *Trust floor*: trust tier of result ≥ required floor.

    A :class:`ClosureWitness` is a proof certificate that the completion
    predicate holds.  Witnesses encode the full judgment tuple
    ``(c, φ, A, E, O, B, T, Π)``:

    * c = context_id  (construction context)
    * φ = formula     (completion formula)
    * A = agent_id    (agent producing the witness)
    * E = evidence_ids (supporting evidence)
    * O = obligations (obligations discharged)
    * B = budget_at_witness (budget remaining)
    * T = trust_tier  (achieved trust tier)
    * Π = policy_id   (policy under which completion is claimed)

    Trust tier ordering: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED

    # copilot: s02-closure-completion-criteria

Usage::

    from jugeo.generation.semantic_closure.semantic_closure_completion_criter import (
        ClosureCompletionCriterion,
        ClosureMetric,
        CompletionCheck,
        ClosureWitness,
        CompletionEngine,
        check_closure_completion,
        measure_closure,
        generate_closure_witness,
        COVERAGE_CRITERION,
        OBLIGATION_CRITERION,
        BUDGET_CRITERION,
        TRUST_CRITERION,
        DEFAULT_CRITERIA,
    )
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "CompletionStatus",
    "WitnessType",
    "CriterionKind",
    # Dataclasses
    "ClosureCompletionCriterion",
    "ClosureMetric",
    "CompletionCheck",
    "ClosureWitness",
    "CriteriaEvaluation",
    "CompletionReport",
    # Classes
    "CompletionEngine",
    "CriteriaRegistry",
    "WitnessValidator",
    # Functions
    "check_closure_completion",
    "measure_closure",
    "generate_closure_witness",
    "evaluate_criteria",
    "weighted_completion_score",
    "build_default_criteria",
    # Constants
    "COVERAGE_CRITERION",
    "OBLIGATION_CRITERION",
    "BUDGET_CRITERION",
    "TRUST_CRITERION",
    "DEFAULT_CRITERIA",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.semantic_closure.models import (  # type: ignore[import]
        ClosureResult,
        ClosureCheck,
    )
    _MODELS_AVAILABLE = True
except Exception:  # pragma: no cover
    _MODELS_AVAILABLE = False

    class ClosureResult(str, Enum):  # type: ignore[no-redef]
        OPEN = "open"
        PARTIAL = "partial"
        CLOSED = "closed"

    ClosureCheck = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustTier  # type: ignore[import]
    _TRUST_AVAILABLE = True
except Exception:  # pragma: no cover
    _TRUST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Trust tier ranks
# ---------------------------------------------------------------------------

_TRUST_RANKS: dict[str, int] = {
    "PROPOSAL": 0,
    "REVIEWED": 1,
    "VERIFIED": 2,
    "RUNTIME_WITNESSED": 3,
    "PROOF_BACKED": 4,
}


def _trust_rank(tier: str) -> int:
    """Return numeric rank for a trust tier string."""
    return _TRUST_RANKS.get(tier.upper(), 0)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CompletionStatus(str, Enum):
    """Overall status of a completion check.

    * ``COMPLETE``    — all required criteria satisfied and witness exists.
    * ``INCOMPLETE``  — some required criteria not satisfied.
    * ``PARTIAL``     — all optional criteria met but some required are not.
    * ``FAILED``      — hard failure (e.g. budget overrun, mandatory obligation open).
    """

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class WitnessType(str, Enum):
    """Type of closure witness.

    * ``EXISTENCE``    — proves that a closed construction exists.
    * ``UNIQUENESS``   — proves the closed construction is unique.
    * ``CONSTRUCTIVE`` — provides an explicit construction witnessing closure.
    """

    EXISTENCE = "existence"
    UNIQUENESS = "uniqueness"
    CONSTRUCTIVE = "constructive"


class CriterionKind(str, Enum):
    """Kind of completion criterion.

    * ``COVERAGE``    — measures fraction of obligations that are closed.
    * ``OBLIGATION``  — checks that all mandatory obligations are discharged.
    * ``BUDGET``      — verifies budget compliance.
    * ``TRUST``       — checks trust tier floor.
    * ``CUSTOM``      — user-defined criterion.
    """

    COVERAGE = "coverage"
    OBLIGATION = "obligation"
    BUDGET = "budget"
    TRUST = "trust"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosureCompletionCriterion:
    """A formal criterion for semantic closure completion.

    A criterion is satisfied when::

        measure(construction) ≥ threshold

    where ``measure`` is the metric associated with this criterion.

    Attributes
    ----------
    criterion_id:
        Unique identifier.
    name:
        Human-readable name.
    description:
        Description of what this criterion measures.
    kind:
        :class:`CriterionKind` categorising the criterion.
    theory_section:
        Theory section that introduces this criterion.
    threshold:
        Minimum metric value for the criterion to be satisfied.
    weight:
        Weight in the weighted completion score (sum of weights need not be 1).
    is_required:
        When True, this criterion MUST be satisfied for completion.
    trust_tier_required:
        Minimum trust tier for the construction to satisfy this criterion.
    metric_higher_is_better:
        When True (default), higher metric → closer to satisfaction.
    tags:
        Additional classification tags.
    """

    criterion_id: str
    name: str
    description: str
    kind: str  # CriterionKind value
    theory_section: str
    threshold: float
    weight: float
    is_required: bool
    trust_tier_required: str
    metric_higher_is_better: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)

    def is_satisfied(self, score: float) -> bool:
        """Return True when *score* meets or exceeds the threshold.

        Parameters
        ----------
        score:
            The measured metric value.

        Returns
        -------
        bool
            ``score >= threshold`` (or ``score <= threshold`` if lower is better).
        """
        if self.metric_higher_is_better:
            return score >= self.threshold
        return score <= self.threshold

    def weighted_score(self, score: float) -> float:
        """Return the weighted contribution of *score* to the overall completion.

        Parameters
        ----------
        score:
            The raw metric value.

        Returns
        -------
        float
            ``weight * score / threshold`` (capped at ``weight`` when score > threshold).
        """
        if self.threshold <= 0:
            return self.weight if score >= 0 else 0.0
        normalized = score / self.threshold
        if self.metric_higher_is_better:
            return min(self.weight, self.weight * normalized)
        # lower-is-better: threshold is the maximum allowed value
        return min(self.weight, self.weight * (1.0 - max(0.0, (score - self.threshold) / self.threshold)))

    def describe(self) -> str:
        """Return a formatted description string."""
        req = "required" if self.is_required else "optional"
        return (
            f"[{self.kind}] {self.name} ({req}, weight={self.weight:.2f}, "
            f"threshold={self.threshold:.3f}): {self.description}"
        )


@dataclass(frozen=True)
class ClosureMetric:
    """A measured metric value for a single criterion.

    Attributes
    ----------
    metric_id:
        Unique identifier for this measurement.
    criterion_id:
        The criterion this metric measures.
    value:
        The measured value.
    measured_at:
        UNIX timestamp of the measurement.
    measurement_context:
        Serialisable key-value pairs providing context.
    confidence:
        Confidence in the measurement (0.0–1.0).
    source:
        Description of how the metric was computed.
    """

    metric_id: str
    criterion_id: str
    value: float
    measured_at: float
    measurement_context: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    confidence: float = 1.0
    source: str = "computed"

    def context_dict(self) -> dict[str, str]:
        """Return measurement_context as a plain dict."""
        return dict(self.measurement_context)

    def is_reliable(self, min_confidence: float = 0.5) -> bool:
        """Return True if confidence ≥ min_confidence."""
        return self.confidence >= min_confidence


@dataclass(frozen=True)
class CriteriaEvaluation:
    """Result of evaluating one criterion against a metric.

    Attributes
    ----------
    criterion_id:
        The criterion evaluated.
    criterion_name:
        Human-readable criterion name.
    metric_value:
        The measured value.
    threshold:
        The criterion threshold.
    satisfied:
        True when metric_value meets the threshold.
    weighted_contribution:
        Weighted contribution to the overall completion score.
    is_required:
        Whether this criterion is required for completion.
    """

    criterion_id: str
    criterion_name: str
    metric_value: float
    threshold: float
    satisfied: bool
    weighted_contribution: float
    is_required: bool
    notes: str = ""

    def gap(self) -> float:
        """Return the gap between threshold and metric (negative = over-satisfied)."""
        return self.threshold - self.metric_value


@dataclass(frozen=True)
class CompletionCheck:
    """Result of a full completion check for a construction.

    Encodes the judgment tuple (c, φ, A, E, O, B, T, Π) where:
    - c = context_id, φ = formula, A = agent_id, E = evidence_ids,
    - O = failed_criteria (unsatisfied obligations), B = budget_used,
    - T = trust_tier, Π = policy_id.

    Attributes
    ----------
    check_id:
        Unique identifier.
    construction_id:
        The construction being checked.
    criteria_evaluations:
        Tuple of per-criterion evaluations.
    all_required_met:
        True when all required criteria are satisfied.
    weighted_completion:
        Weighted sum of satisfied criterion contributions.
    max_weighted_completion:
        Maximum possible weighted completion (sum of all weights).
    completion_status:
        Overall :class:`CompletionStatus`.
    trust_tier:
        Trust tier of the construction.
    checked_at:
        UNIX timestamp of the check.
    witness_id:
        ID of the :class:`ClosureWitness` if one was generated, else None.
    context_id:
        Construction context (c in the judgment tuple).
    formula:
        Completion formula (φ).
    agent_id:
        Agent that performed the check (A).
    evidence_ids:
        Evidence supporting the check (E).
    budget_used:
        Budget consumed by the construction (B).
    policy_id:
        Policy under which completion is evaluated (Π).
    """

    check_id: str
    construction_id: str
    criteria_evaluations: tuple[CriteriaEvaluation, ...]
    all_required_met: bool
    weighted_completion: float
    max_weighted_completion: float
    completion_status: str
    trust_tier: str
    checked_at: float
    witness_id: str | None
    context_id: str
    formula: str
    agent_id: str
    evidence_ids: tuple[str, ...]
    budget_used: float
    policy_id: str

    def completion_fraction(self) -> float:
        """Return weighted_completion / max_weighted_completion."""
        if self.max_weighted_completion <= 0:
            return 0.0
        return self.weighted_completion / self.max_weighted_completion

    def failed_criteria(self) -> list[CriteriaEvaluation]:
        """Return evaluations that were not satisfied."""
        return [e for e in self.criteria_evaluations if not e.satisfied]

    def passed_criteria(self) -> list[CriteriaEvaluation]:
        """Return evaluations that were satisfied."""
        return [e for e in self.criteria_evaluations if e.satisfied]

    def summary(self) -> str:
        """Return a one-line summary."""
        pct = self.completion_fraction() * 100
        return (
            f"[{self.completion_status}] {self.construction_id}: "
            f"{pct:.1f}% complete, all_required={self.all_required_met}, "
            f"trust={self.trust_tier}"
        )


@dataclass(frozen=True)
class ClosureWitness:
    """A proof certificate that the completion predicate holds.

    Encodes the full judgment tuple (c, φ, A, E, O, B, T, Π):
    - c = context_id
    - φ = formula
    - A = agent_id
    - E = evidence_ids
    - O = obligations_discharged
    - B = budget_at_witness
    - T = trust_tier
    - Π = policy_id

    Attributes
    ----------
    witness_id:
        Unique identifier.
    construction_id:
        The construction this witness is for.
    check_id:
        The :class:`CompletionCheck` this witness was generated from.
    witness_type:
        One of ``"existence"``, ``"uniqueness"``, ``"constructive"``.
    evidence_ids:
        Tuple of evidence item IDs supporting the witness (E).
    formula:
        Completion formula expressed as a string (φ).
    agent_id:
        Agent producing the witness (A).
    budget_at_witness:
        Budget remaining when the witness was produced (B).
    policy_id:
        Policy under which completion is claimed (Π).
    created_at:
        UNIX timestamp.
    context_id:
        Construction context (c).
    trust_tier:
        Trust tier of the witness (T).
    obligations_discharged:
        Tuple of obligation IDs confirmed closed by this witness (O).
    notes:
        Human-readable notes about the witness.
    """

    witness_id: str
    construction_id: str
    check_id: str
    witness_type: str
    evidence_ids: tuple[str, ...]
    formula: str
    agent_id: str
    budget_at_witness: float
    policy_id: str
    created_at: float
    context_id: str
    trust_tier: str
    obligations_discharged: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def is_constructive(self) -> bool:
        """Return True when witness_type is 'constructive'."""
        return self.witness_type == WitnessType.CONSTRUCTIVE.value

    def judgment_tuple(self) -> tuple[str, str, str, tuple, tuple, float, str, str]:
        """Return the witness as a (c, φ, A, E, O, B, T, Π) tuple."""
        return (
            self.context_id,
            self.formula,
            self.agent_id,
            self.evidence_ids,
            self.obligations_discharged,
            self.budget_at_witness,
            self.trust_tier,
            self.policy_id,
        )


@dataclass(frozen=True)
class CompletionReport:
    """Aggregated completion report for a generation run.

    Attributes
    ----------
    report_id:
        Unique report identifier.
    construction_count:
        Total constructions checked.
    complete_count:
        Constructions that achieved COMPLETE status.
    incomplete_count:
        Constructions that achieved INCOMPLETE or PARTIAL status.
    failed_count:
        Constructions with FAILED status.
    mean_completion_fraction:
        Average completion fraction across all constructions.
    witness_count:
        Total witnesses generated.
    generated_at:
        UNIX timestamp.
    """

    report_id: str
    construction_count: int
    complete_count: int
    incomplete_count: int
    failed_count: int
    mean_completion_fraction: float
    witness_count: int
    generated_at: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def completion_rate(self) -> float:
        """Return fraction of constructions that completed successfully."""
        if self.construction_count == 0:
            return 0.0
        return self.complete_count / self.construction_count

    def summary(self) -> str:
        """Return a one-line report summary."""
        return (
            f"CompletionReport({self.construction_count} constructions, "
            f"{self.complete_count} complete, "
            f"{self.failed_count} failed, "
            f"mean={self.mean_completion_fraction:.1%})"
        )


# ---------------------------------------------------------------------------
# Default criteria
# ---------------------------------------------------------------------------

COVERAGE_CRITERION = ClosureCompletionCriterion(
    criterion_id="coverage",
    name="Coverage Completeness",
    description="Fraction of obligations that are semantically closed must exceed threshold.",
    kind=CriterionKind.COVERAGE.value,
    theory_section="§38.1",
    threshold=0.85,
    weight=3.0,
    is_required=True,
    trust_tier_required="PROPOSAL",
    metric_higher_is_better=True,
    tags=("core", "coverage"),
)

OBLIGATION_CRITERION = ClosureCompletionCriterion(
    criterion_id="obligation_discharge",
    name="Obligation Discharge",
    description="All mandatory obligations must be discharged.",
    kind=CriterionKind.OBLIGATION.value,
    theory_section="§38.2",
    threshold=1.0,
    weight=4.0,
    is_required=True,
    trust_tier_required="REVIEWED",
    metric_higher_is_better=True,
    tags=("core", "obligation"),
)

BUDGET_CRITERION = ClosureCompletionCriterion(
    criterion_id="budget_compliance",
    name="Budget Compliance",
    description="Budget used must not exceed budget allocated.",
    kind=CriterionKind.BUDGET.value,
    theory_section="§38.4",
    threshold=1.0,
    weight=2.0,
    is_required=False,
    trust_tier_required="PROPOSAL",
    metric_higher_is_better=False,  # lower budget use is better
    tags=("budget",),
)

TRUST_CRITERION = ClosureCompletionCriterion(
    criterion_id="trust_floor",
    name="Trust Tier Floor",
    description="Construction must achieve at least REVIEWED trust tier.",
    kind=CriterionKind.TRUST.value,
    theory_section="§38.4",
    threshold=1.0,  # REVIEWED rank
    weight=2.0,
    is_required=True,
    trust_tier_required="REVIEWED",
    metric_higher_is_better=True,
    tags=("trust",),
)

DEFAULT_CRITERIA: list[ClosureCompletionCriterion] = [
    COVERAGE_CRITERION,
    OBLIGATION_CRITERION,
    BUDGET_CRITERION,
    TRUST_CRITERION,
]


def build_default_criteria() -> list[ClosureCompletionCriterion]:
    """Return a fresh copy of the default criteria list."""
    return list(DEFAULT_CRITERIA)


# ---------------------------------------------------------------------------
# Criteria registry
# ---------------------------------------------------------------------------


class CriteriaRegistry:
    """Registry of named :class:`ClosureCompletionCriterion` instances.

    Provides lookup by criterion_id and supports registration of custom criteria.
    """

    def __init__(self) -> None:
        self._criteria: dict[str, ClosureCompletionCriterion] = {}
        for c in DEFAULT_CRITERIA:
            self.register(c)

    def register(self, criterion: ClosureCompletionCriterion) -> None:
        """Register a criterion, replacing any existing entry with the same id."""
        self._criteria[criterion.criterion_id] = criterion
        logger.debug("Registered criterion: %s", criterion.criterion_id)

    def get(self, criterion_id: str) -> ClosureCompletionCriterion | None:
        """Return the criterion with *criterion_id*, or None."""
        return self._criteria.get(criterion_id)

    def list_all(self) -> list[ClosureCompletionCriterion]:
        """Return all registered criteria."""
        return list(self._criteria.values())

    def required_criteria(self) -> list[ClosureCompletionCriterion]:
        """Return only required criteria."""
        return [c for c in self._criteria.values() if c.is_required]


# Module-level default registry
_DEFAULT_REGISTRY = CriteriaRegistry()


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _measure_coverage(construction: dict[str, Any]) -> float:
    """Compute coverage completeness from a construction dict.

    Parameters
    ----------
    construction:
        Dict with optional keys: obligations (list), closed_obligations (list).

    Returns
    -------
    float
        Fraction of obligations that are closed.
    """
    obligations = construction.get("obligations", [])
    closed = construction.get("closed_obligations", [])
    if not obligations:
        return 1.0  # vacuously complete
    return len(closed) / len(obligations)


def _measure_obligation_discharge(construction: dict[str, Any]) -> float:
    """Compute obligation discharge metric.

    Returns 1.0 if all mandatory obligations are discharged, else < 1.0.
    """
    mandatory = construction.get("mandatory_obligations", [])
    closed = set(construction.get("closed_obligations", []))
    if not mandatory:
        return 1.0
    discharged = sum(1 for o in mandatory if o in closed)
    return discharged / len(mandatory)


def _measure_budget_compliance(construction: dict[str, Any]) -> float:
    """Compute budget compliance metric.

    Returns budget_used / budget_allocated.  Values > 1.0 indicate overrun.
    """
    allocated = float(construction.get("budget_allocated", 1.0))
    used = float(construction.get("budget_used", 0.0))
    if allocated <= 0:
        return 0.0
    return used / allocated


def _measure_trust(construction: dict[str, Any]) -> float:
    """Compute trust tier metric as a normalised rank."""
    tier = construction.get("trust_tier", "PROPOSAL")
    rank = _trust_rank(tier)
    max_rank = 4  # PROOF_BACKED
    return rank / max_rank


_METRIC_FUNCTIONS: dict[str, Any] = {
    CriterionKind.COVERAGE.value: _measure_coverage,
    CriterionKind.OBLIGATION.value: _measure_obligation_discharge,
    CriterionKind.BUDGET.value: _measure_budget_compliance,
    CriterionKind.TRUST.value: _measure_trust,
}


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def measure_closure(construction: dict[str, Any], metric_id: str) -> ClosureMetric:
    """Measure a closure metric for a construction.

    Parameters
    ----------
    construction:
        Dict representing the construction state.
    metric_id:
        The criterion_id to measure (e.g. ``"coverage"``).

    Returns
    -------
    ClosureMetric
        The measured metric.
    """
    fn = _METRIC_FUNCTIONS.get(metric_id)
    if fn is not None:
        value = fn(construction)
    else:
        logger.warning("No measurement function for metric %s; defaulting to 0.0.", metric_id)
        value = 0.0

    return ClosureMetric(
        metric_id=str(uuid.uuid4()),
        criterion_id=metric_id,
        value=value,
        measured_at=time.time(),
        measurement_context=tuple(
            (k, str(v))
            for k, v in {
                "construction_id": construction.get("construction_id", ""),
                "trust_tier": construction.get("trust_tier", "PROPOSAL"),
            }.items()
        ),
        confidence=0.9,
        source=f"_measure_{metric_id}",
    )


def evaluate_criteria(
    construction: dict[str, Any],
    criteria: list[ClosureCompletionCriterion],
    metrics: list[ClosureMetric] | None = None,
) -> list[CriteriaEvaluation]:
    """Evaluate a list of criteria for a construction.

    Parameters
    ----------
    construction:
        Construction state dict.
    criteria:
        Criteria to evaluate.
    metrics:
        Pre-computed metrics; if provided, used instead of re-measuring.

    Returns
    -------
    list[CriteriaEvaluation]
        One evaluation per criterion.
    """
    # Build metric lookup
    metric_lookup: dict[str, ClosureMetric] = {}
    if metrics:
        for m in metrics:
            metric_lookup[m.criterion_id] = m

    evaluations: list[CriteriaEvaluation] = []
    for criterion in criteria:
        # Get or measure
        metric = metric_lookup.get(criterion.criterion_id) or measure_closure(
            construction, criterion.criterion_id
        )
        satisfied = criterion.is_satisfied(metric.value)
        wt_contrib = criterion.weighted_score(metric.value) if satisfied else 0.0

        evaluations.append(CriteriaEvaluation(
            criterion_id=criterion.criterion_id,
            criterion_name=criterion.name,
            metric_value=metric.value,
            threshold=criterion.threshold,
            satisfied=satisfied,
            weighted_contribution=wt_contrib,
            is_required=criterion.is_required,
            notes=(
                f"metric={metric.value:.4f}, threshold={criterion.threshold:.4f}, "
                f"{'✓' if satisfied else '✗'}"
            ),
        ))

    return evaluations


def weighted_completion_score(evaluations: list[CriteriaEvaluation]) -> tuple[float, float]:
    """Compute the total and maximum weighted completion scores.

    Parameters
    ----------
    evaluations:
        List of :class:`CriteriaEvaluation` results.

    Returns
    -------
    tuple[float, float]
        (total_weighted_score, max_possible_score)
    """
    total = sum(e.weighted_contribution for e in evaluations)
    # Max is the sum of weights of ALL criteria (if all satisfied)
    # We don't have direct access to criterion weights here, so use the
    # satisfied contribution / threshold approach: just sum contributions
    max_score = total + sum(
        e.threshold * (1.0 - e.metric_value / max(e.threshold, 1e-9))
        for e in evaluations
        if not e.satisfied
    )
    return total, max(max_score, total)


def check_closure_completion(
    construction: dict[str, Any],
    criteria: list[ClosureCompletionCriterion] | None = None,
    metrics: list[ClosureMetric] | None = None,
    agent_id: str = "completion-engine",
    policy_id: str = "default",
) -> CompletionCheck:
    """Check whether *construction* satisfies the given completion criteria.

    Parameters
    ----------
    construction:
        Dict representing the construction state.  Expected keys:
        construction_id, obligations, closed_obligations, mandatory_obligations,
        budget_allocated, budget_used, trust_tier, context_id, formula, agent_id,
        evidence_ids, policy_id.
    criteria:
        Criteria to check; defaults to :data:`DEFAULT_CRITERIA`.
    metrics:
        Pre-computed metrics; if None, metrics are computed on-the-fly.
    agent_id:
        Agent performing the check.
    policy_id:
        Policy under which completion is evaluated.

    Returns
    -------
    CompletionCheck
        The structured completion check result.
    """
    if criteria is None:
        criteria = DEFAULT_CRITERIA

    evaluations = evaluate_criteria(construction, criteria, metrics)

    # Determine all_required_met
    all_req = all(e.satisfied for e in evaluations if e.is_required)

    # Compute weighted scores
    total_score, max_score = weighted_completion_score(evaluations)

    # Determine status
    if all_req:
        status = CompletionStatus.COMPLETE.value
    elif any(not e.satisfied and e.is_required for e in evaluations):
        required_failures = [e for e in evaluations if e.is_required and not e.satisfied]
        # FAILED if mandatory obligations are open
        if any(
            e.criterion_id == CriterionKind.OBLIGATION.value
            for e in required_failures
        ):
            status = CompletionStatus.FAILED.value
        else:
            status = CompletionStatus.INCOMPLETE.value
    else:
        status = CompletionStatus.PARTIAL.value

    check_id = str(uuid.uuid4())
    context_id = construction.get("context_id", "")
    trust_tier = construction.get("trust_tier", "PROPOSAL")

    return CompletionCheck(
        check_id=check_id,
        construction_id=construction.get("construction_id", ""),
        criteria_evaluations=tuple(evaluations),
        all_required_met=all_req,
        weighted_completion=total_score,
        max_weighted_completion=max_score,
        completion_status=status,
        trust_tier=trust_tier,
        checked_at=time.time(),
        witness_id=None,
        context_id=context_id,
        formula=construction.get("formula", f"complete({construction.get('construction_id', '')})" ),
        agent_id=construction.get("agent_id", agent_id),
        evidence_ids=tuple(construction.get("evidence_ids", [])),
        budget_used=float(construction.get("budget_used", 0.0)),
        policy_id=construction.get("policy_id", policy_id),
    )


def generate_closure_witness(
    check: CompletionCheck,
    construction: dict[str, Any],
    witness_type: str = WitnessType.EXISTENCE.value,
) -> ClosureWitness:
    """Generate a :class:`ClosureWitness` for a passed completion check.

    Parameters
    ----------
    check:
        A :class:`CompletionCheck` — typically with status COMPLETE.
    construction:
        The construction dict.
    witness_type:
        One of ``"existence"``, ``"uniqueness"``, ``"constructive"``.

    Returns
    -------
    ClosureWitness
        The generated witness encoding the full judgment tuple.
    """
    closed_obligations = tuple(construction.get("closed_obligations", []))
    budget_remaining = float(construction.get("budget_allocated", 1.0)) - check.budget_used

    return ClosureWitness(
        witness_id=str(uuid.uuid4()),
        construction_id=check.construction_id,
        check_id=check.check_id,
        witness_type=witness_type,
        evidence_ids=check.evidence_ids,
        formula=check.formula,
        agent_id=check.agent_id,
        budget_at_witness=max(0.0, budget_remaining),
        policy_id=check.policy_id,
        created_at=time.time(),
        context_id=check.context_id,
        trust_tier=check.trust_tier,
        obligations_discharged=closed_obligations,
        notes=f"Generated from CompletionCheck {check.check_id} with status {check.completion_status}",
    )


# ---------------------------------------------------------------------------
# Witness validator
# ---------------------------------------------------------------------------


class WitnessValidator:
    """Validates :class:`ClosureWitness` instances for consistency.

    Checks that:
    * The referenced check_id exists (if a registry is provided).
    * The obligations_discharged are non-empty (for constructive witnesses).
    * The trust_tier is at or above the minimum required.
    * The formula is non-empty.
    """

    def __init__(self, min_trust_tier: str = "PROPOSAL") -> None:
        self.min_trust_tier = min_trust_tier

    def validate(self, witness: ClosureWitness) -> list[str]:
        """Return a list of validation errors; empty if valid."""
        errors: list[str] = []

        if not witness.formula.strip():
            errors.append("Witness formula is empty.")

        if witness.is_constructive() and not witness.obligations_discharged:
            errors.append("Constructive witness must discharge at least one obligation.")

        req_rank = _trust_rank(self.min_trust_tier)
        actual_rank = _trust_rank(witness.trust_tier)
        if actual_rank < req_rank:
            errors.append(
                f"Witness trust tier {witness.trust_tier!r} is below minimum {self.min_trust_tier!r}."
            )

        if witness.budget_at_witness < 0:
            errors.append(f"Witness has negative budget_at_witness: {witness.budget_at_witness}.")

        return errors


# ---------------------------------------------------------------------------
# Completion engine
# ---------------------------------------------------------------------------


class CompletionEngine:
    """Orchestrates completion checking across multiple constructions.

    Attributes
    ----------
    criteria:
        The criteria to apply.
    registry:
        Optional :class:`CriteriaRegistry` for custom criteria lookup.
    generate_witnesses:
        When True, automatically generate witnesses for COMPLETE checks.
    agent_id:
        Agent ID attributed to checks produced by this engine.
    """

    def __init__(
        self,
        criteria: list[ClosureCompletionCriterion] | None = None,
        registry: CriteriaRegistry | None = None,
        generate_witnesses: bool = True,
        agent_id: str = "completion-engine",
        policy_id: str = "default",
    ) -> None:
        self.criteria = criteria or DEFAULT_CRITERIA
        self.registry = registry or _DEFAULT_REGISTRY
        self.generate_witnesses = generate_witnesses
        self.agent_id = agent_id
        self.policy_id = policy_id
        self._checks: list[CompletionCheck] = []
        self._witnesses: list[ClosureWitness] = []

    def check(
        self,
        construction: dict[str, Any],
        metrics: list[ClosureMetric] | None = None,
    ) -> CompletionCheck:
        """Run a completion check for *construction*.

        Parameters
        ----------
        construction:
            Construction state dict.
        metrics:
            Optional pre-computed metrics.

        Returns
        -------
        CompletionCheck
            The check result, stored internally.
        """
        check = check_closure_completion(
            construction,
            criteria=self.criteria,
            metrics=metrics,
            agent_id=self.agent_id,
            policy_id=self.policy_id,
        )
        self._checks.append(check)

        if self.generate_witnesses and check.completion_status == CompletionStatus.COMPLETE.value:
            witness = generate_closure_witness(check, construction)
            self._witnesses.append(witness)
            logger.debug("Generated witness %s for construction %s.", witness.witness_id, check.construction_id)

        return check

    def check_many(
        self,
        constructions: list[dict[str, Any]],
    ) -> list[CompletionCheck]:
        """Check a list of constructions; return all check results."""
        return [self.check(c) for c in constructions]

    def get_report(self) -> CompletionReport:
        """Build a :class:`CompletionReport` from all checks so far."""
        total = len(self._checks)
        complete = sum(1 for c in self._checks if c.completion_status == CompletionStatus.COMPLETE.value)
        failed = sum(1 for c in self._checks if c.completion_status == CompletionStatus.FAILED.value)
        incomplete = total - complete - failed
        mean_frac = (
            sum(c.completion_fraction() for c in self._checks) / total
            if total > 0 else 0.0
        )
        return CompletionReport(
            report_id=str(uuid.uuid4()),
            construction_count=total,
            complete_count=complete,
            incomplete_count=incomplete,
            failed_count=failed,
            mean_completion_fraction=mean_frac,
            witness_count=len(self._witnesses),
            generated_at=time.time(),
        )

    def reset(self) -> None:
        """Clear all stored checks and witnesses."""
        self._checks.clear()
        self._witnesses.clear()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== semantic_closure_completion_criter smoke test ===\n")

    # Build test constructions
    constructions = []
    for i in range(10):
        obligations = [f"obl-{j}" for j in range(20)]
        closed = obligations[:int(18 - i)]  # progressively fewer closed
        mandatory = obligations[:5]
        constructions.append({
            "construction_id": f"construction-{i:03d}",
            "obligations": obligations,
            "closed_obligations": closed,
            "mandatory_obligations": mandatory,
            "budget_allocated": 1.0,
            "budget_used": 0.3 + i * 0.08,
            "trust_tier": "REVIEWED" if i < 7 else "PROPOSAL",
            "context_id": f"ctx-{i}",
            "formula": f"complete(construction-{i:03d})",
            "agent_id": "smoke-test",
            "evidence_ids": [f"ev-{j}" for j in range(5)],
            "policy_id": "default",
        })

    # Use CompletionEngine
    engine = CompletionEngine(generate_witnesses=True, agent_id="smoke-test")
    checks = engine.check_many(constructions)

    print(f"Checked {len(checks)} constructions:")
    for check in checks:
        print(f"  {check.summary()}")

    report = engine.get_report()
    print(f"\n{report.summary()}")

    # Check individual functions
    c = constructions[0]
    metric = measure_closure(c, "coverage")
    print(f"\nCoverage metric for construction-000: {metric.value:.4f}")

    evaluations = evaluate_criteria(c, DEFAULT_CRITERIA)
    print(f"\nCriteria evaluations for construction-000:")
    for ev in evaluations:
        print(f"  {ev.notes}")

    # Generate witness
    check0 = check_closure_completion(constructions[0])
    if check0.completion_status == CompletionStatus.COMPLETE.value:
        witness = generate_closure_witness(check0, constructions[0])
        print(f"\nWitness generated: {witness.witness_id}")
        print(f"  Judgment tuple: {witness.judgment_tuple()}")

        # Validate witness
        validator = WitnessValidator(min_trust_tier="PROPOSAL")
        errs = validator.validate(witness)
        print(f"  Validation errors: {errs}")
    else:
        print(f"\nConstruction-000 status: {check0.completion_status} — no witness needed")

    # Default criteria
    print("\nDefault criteria:")
    for c in DEFAULT_CRITERIA:
        print(f"  {c.describe()}")

    # CriteriaRegistry
    registry = CriteriaRegistry()
    print(f"\nRegistry has {len(registry.list_all())} criteria")
    print(f"Required criteria: {[c.criterion_id for c in registry.required_criteria()]}")

    print("\n=== smoke test PASSED ===")
