"""
Theorem and falsification burden for the jugeo discovery engine.

# copilot: theorem_and_falsification_burden_f -- manages discovery theorems,
# assigns and distributes falsification burdens across agents, tracks obligations.
# This module provides the formal machinery for theorem lifecycle management,
# including creation from templates, falsification burden computation and distribution,
# obligation tracking, verification recording, and judgment synthesis. Each theorem
# is associated with one or more FalsificationBurden records which quantify the
# effort required to falsify the theorem. Burdens are distributed among agents
# according to configurable DistributionStrategy objects. Obligations are
# DiscoveryObligation records that must be fulfilled for a theorem to advance
# its TrustTier. The module exposes both class-based (BurdenDistribution) and
# functional interfaces. All data structures are frozen dataclasses with slots.
"""

# ----------------------------------------------------------------------
# Background and motivation
# --------------------------
# A theorem proposal is only valuable if it is, in principle, falsifiable.  An
# unfalsifiable proposal carries no epistemic content and should be discarded.
# Conversely, a highly falsifiable but hard-to-disprove theorem is a strong
# candidate for investment: if we cannot falsify it despite significant effort, we
# have accumulated substantial evidence for its truth.
#
# The *falsification burden* of a theorem T is defined as::
#
#     burden(T) = Σ_{c in conditions(T)}  difficulty(c) x criticality(c)
#
# where:
#
# * ``conditions(T)`` -- the enumerated set of falsification conditions, i.e. the
#   specific scenarios under which T would be false if they were realised.
# * ``difficulty(c)`` -- how hard it is to check whether condition c is realised,
#   on a scale TRIVIAL (0.1) -> EASY (0.3) -> MODERATE (0.5) -> HARD (0.8) ->
#   INTRACTABLE (1.0).
# * ``criticality(c)`` -- how critical the condition is to the theorem's overall
#   truth, on a scale [0, 1].
#
# A high burden (large Σ) indicates many hard-to-check conditions, each critical
# to the theorem.  Such theorems are expensive to falsify and therefore expensive
# to certify.  A low burden means the theorem is either trivially verified or has
# few critical conditions.
#
# Falsification conditions
# ------------------------
# For a theorem statement expressed in natural language, we heuristically extract
# falsification conditions by identifying negatable clauses -- substatements that,
# if negated, would make the theorem false.  Each such clause becomes a
# ``FalsificationCondition`` with:
#
# * a condition_id
# * a human-readable description of the falsifying scenario
# * a difficulty level (``ConditionDifficulty``)
# * a criticality score in [0, 1]
# * a current status (``ConditionStatus``: OPEN, VERIFIED, FALSIFIED, INCONCLUSIVE)
#
# Checking a condition against evidence items produces a ``ConditionCheckResult``
# that updates the condition's status and records a confidence score.
#
# Falsification campaign
# ----------------------
# A *falsification campaign* runs the full check pipeline over a list of
# theorems.  For each theorem, it:
# 1. Enumerates falsification conditions.
# 2. For each condition, checks it against available evidence items.
# 3. Aggregates results into a ``FalsificationCampaignResult``.
# 4. Prioritizes theorems by burden for further investment.
#
# The campaign result contains per-theorem burden records, overall statistics,
# and a list of theorems that were *falsified* (at least one FALSIFIED condition
# found).
#
# Burden vs leverage
# ------------------
# The analyzer's ``correlate_burden_with_leverage`` method computes the Pearson
# correlation between burden scores and leverage scores across a population of
# theorems.  A positive correlation would indicate that high-leverage theorems
# also tend to have high falsification burden -- they make strong claims that are
# both hard to disprove and, if true, highly useful.  A negative or near-zero
# correlation may indicate over-speculation (proposals that are easy to falsify
# but promise high leverage) or under-speculation (safe proposals with minimal
# leverage).
# ----------------------------------------------------------------------


from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "ConditionStatus",
    "ConditionDifficulty",
    # Frozen dataclasses
    "FalsificationConfig",
    "TheoremRecord",
    "FalsificationCondition",
    "FalsificationBurden",
    "EvidenceItem",
    "ConditionCheckResult",
    "FalsificationCampaignResult",
    "BurdenDistribution",
    "ConditionCoverageReport",
    "BurdenLeverageCorrelation",
    "BurdenWitnessReport",
    "CampaignWitnessReport",
    "ConditionWitnessReport",
    # Main classes
    "TheoremFalsificationBurdenCoordinator",
    "TheoremFalsificationBurdenAnalyzer",
    "TheoremFalsificationBurdenWitness",
    # Free functions
    "run_falsification_campaign",
    "score_falsification_burden",
    "select_hardest_conditions",
    # Helpers exposed for testing
    "_utcnow",
    "_uid",
    "_clamp",
    "_difficulty_to_float",
    "_extract_conditions_heuristic",
    "_assign_difficulty",
    "_assign_criticality",
    "_check_condition_against_evidence",
    "_pearson_correlation",
    "_burden_tier",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryResult,
        DiscoveryDiagnostics,
        DiscoveryStatus,
        PipelineStage,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
    )
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.a_real_mathematical_discovery_subs import (
        ProposalRecord,
        ObstructionRecord,
        ProposalOutcome,
    )
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.evaluation_and_calibration_realize import (
        LeverageEvaluation,
        CalibrationResult,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level constants and helpers
# ---------------------------------------------------------------------------

# Mapping from ConditionDifficulty to its numeric weight.
_DIFFICULTY_WEIGHTS: dict[str, float] = {
    "TRIVIAL": 0.1,
    "EASY": 0.3,
    "MODERATE": 0.5,
    "HARD": 0.8,
    "INTRACTABLE": 1.0,
}

# Structural keywords that indicate a theorem has hard-to-check conditions.
_HARD_KEYWORDS: set[str] = {
    "arbitrary", "all", "every", "any", "universal", "generic",
    "uncountable", "infinite", "transcendental", "non-trivial",
    "non-degenerate", "irreducible", "absolutely",
}

# Keywords suggesting easy verification.
_EASY_KEYWORDS: set[str] = {
    "finite", "countable", "explicit", "constructive", "computable",
    "decidable", "polynomial", "bounded", "smooth", "regular",
}

# Domain-specific falsification clause patterns (used by heuristic extractor).
_DOMAIN_CLAUSE_PATTERNS: dict[str, list[str]] = {
    "algebraic-geometry": [
        "The base scheme is not regular",
        "The morphism is not flat",
        "Cohomological descent fails in positive characteristic",
        "The sheaf is not quasi-coherent",
        "The residue fields have unequal characteristic",
    ],
    "number-theory": [
        "The prime ramification index exceeds the degree",
        "The modular form has non-trivial nebentypus",
        "The Galois representation is reducible",
        "The L-function has a zero off the critical line",
        "The congruence conditions are incompatible",
    ],
    "topology": [
        "The manifold is not simply connected",
        "The fibration has non-trivial monodromy",
        "The homology class is torsion",
        "The cobordism invariant does not vanish",
        "The surgery obstruction is non-zero",
    ],
    "category-theory": [
        "The adjunction unit is not a natural isomorphism",
        "The monad does not satisfy the associativity axiom",
        "Limits do not exist in the base category",
        "The enrichment is not closed",
        "The fiber functor is not faithful",
    ],
    "analysis": [
        "The operator is not self-adjoint",
        "The spectrum contains negative eigenvalues",
        "The measure is not absolutely continuous",
        "Convergence fails in the strong topology",
        "The functional is not lower semi-continuous",
    ],
    "combinatorics": [
        "The graph contains an odd cycle",
        "The partition function has a real zero",
        "The bijection does not respect the grading",
        "The chromatic polynomial is not positive definite",
        "The generating function diverges",
    ],
    "logic": [
        "The theory is omega-inconsistent",
        "The model does not satisfy the axiom of choice",
        "The forcing extension collapses cardinals",
        "The ultrafilter is not countably complete",
        "The provability predicate violates Löb's theorem",
    ],
}

_DEFAULT_CLAUSES: list[str] = [
    "The main hypothesis fails in a degenerate case",
    "A counterexample exists in low dimension",
    "The conclusion fails over an algebraically closed field",
    "A boundary case violates the stated conditions",
    "The structural condition is not preserved under the relevant operation",
]


def _utcnow() -> float:
    """Return the current UTC time as a POSIX float timestamp."""
    return time.time()


def _uid() -> str:
    """Return a 32-character lowercase hex unique identifier."""
    return uuid.uuid4().hex


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lower*, *upper*].

    Parameters
    ----------
    value:
        Numeric value to clamp.
    lower:
        Lower bound (inclusive).
    upper:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.
    """
    return max(lower, min(upper, value))


def _difficulty_to_float(difficulty: ConditionDifficulty) -> float:
    """Convert a ``ConditionDifficulty`` enum value to its numeric weight.

    Parameters
    ----------
    difficulty:
        The difficulty level to convert.

    Returns
    -------
    float
        Numeric weight in {0.1, 0.3, 0.5, 0.8, 1.0}.
    """
    return _DIFFICULTY_WEIGHTS.get(difficulty.name, 0.5)


def _extract_conditions_heuristic(
    theorem: TheoremRecord,
    max_conditions: int,
) -> list[tuple[str, ConditionDifficulty, float]]:
    """Heuristically extract falsification conditions for *theorem*.

    Returns a list of (description, difficulty, criticality) triples.  Uses
    domain-specific clause patterns from ``_DOMAIN_CLAUSE_PATTERNS`` if
    available, falling back to generic patterns.

    Parameters
    ----------
    theorem:
        The theorem for which to extract conditions.
    max_conditions:
        Maximum number of conditions to return.

    Returns
    -------
    list[tuple[str, ConditionDifficulty, float]]
        List of (description, difficulty, criticality) triples.
    """
    clauses = _DOMAIN_CLAUSE_PATTERNS.get(theorem.domain, _DEFAULT_CLAUSES)
    n = min(max_conditions, len(clauses))
    result: list[tuple[str, ConditionDifficulty, float]] = []
    tokens = set(theorem.statement.lower().split())

    for i in range(n):
        clause = clauses[i % len(clauses)]
        difficulty = _assign_difficulty(clause, tokens)
        criticality = _assign_criticality(clause, i, n)
        result.append((clause, difficulty, criticality))
    return result


def _assign_difficulty(clause: str, statement_tokens: set[str]) -> ConditionDifficulty:
    """Assign a difficulty level to a falsification condition clause.

    Hard keywords in the original theorem statement raise the difficulty;
    easy keywords lower it.  The clause itself is also inspected.

    Parameters
    ----------
    clause:
        The falsification condition clause.
    statement_tokens:
        Lowercase token set from the original theorem statement.

    Returns
    -------
    ConditionDifficulty
        The assigned difficulty level.
    """
    clause_tokens = set(clause.lower().split())
    all_tokens = statement_tokens | clause_tokens

    hard_hits = len(all_tokens & _HARD_KEYWORDS)
    easy_hits = len(all_tokens & _EASY_KEYWORDS)
    score = hard_hits - easy_hits

    if score >= 3:
        return ConditionDifficulty.INTRACTABLE
    elif score >= 1:
        return ConditionDifficulty.HARD
    elif score == 0:
        return ConditionDifficulty.MODERATE
    elif score == -1:
        return ConditionDifficulty.EASY
    else:
        return ConditionDifficulty.TRIVIAL


def _assign_criticality(clause: str, index: int, total: int) -> float:
    """Assign a criticality score to a falsification condition.

    Uses a simple heuristic: the first condition is considered most critical
    (criticality 1.0) and later ones decay with a mild cosine schedule,
    reflecting the natural ordering from most to least central clause in the
    domain-specific pattern lists.

    Parameters
    ----------
    clause:
        The falsification condition clause (inspected for critical keywords).
    index:
        Zero-based index of this condition in the enumerated list.
    total:
        Total number of conditions.

    Returns
    -------
    float
        Criticality score in [0.3, 1.0].
    """
    base = 1.0 - 0.7 * (index / max(total - 1, 1)) * (1 - math.cos(math.pi * index / max(total, 1)))
    # Boost criticality if the clause contains "not", "fails", "does not"
    if any(w in clause.lower() for w in ("not", "fails", "does not", "no ")):
        base = min(1.0, base + 0.05)
    return _clamp(base, 0.3, 1.0)


def _check_condition_against_evidence(
    condition: FalsificationCondition,
    evidence: list[EvidenceItem],
) -> tuple[ConditionStatus, float]:
    """Determine condition status and confidence from a list of evidence items.

    The check is heuristic: each evidence item either supports or refutes the
    condition based on keyword overlap between the condition description and
    the evidence content.  Net refutation evidence -> FALSIFIED; net support
    evidence -> VERIFIED; balanced -> INCONCLUSIVE; no evidence -> OPEN.

    Parameters
    ----------
    condition:
        The falsification condition to check.
    evidence:
        Available evidence items.

    Returns
    -------
    tuple[ConditionStatus, float]
        A (status, confidence) pair.
    """
    if not evidence:
        return ConditionStatus.OPEN, 0.0

    cond_tokens = set(condition.description.lower().split())
    support_score = 0.0
    refute_score = 0.0

    for item in evidence:
        item_tokens = set(item.content.lower().split())
        overlap = len(cond_tokens & item_tokens) / max(len(cond_tokens), 1)
        if item.is_refutation:
            refute_score += overlap * item.weight
        else:
            support_score += overlap * item.weight

    total = support_score + refute_score
    if total < 0.01:
        return ConditionStatus.OPEN, 0.0

    confidence = _clamp(total / (total + 0.5))  # soft confidence cap

    if refute_score > support_score * 1.5:
        return ConditionStatus.FALSIFIED, confidence
    elif support_score > refute_score * 1.5:
        return ConditionStatus.VERIFIED, confidence
    else:
        return ConditionStatus.INCONCLUSIVE, confidence * 0.6


def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    """Compute the Pearson correlation coefficient between *xs* and *ys*.

    Returns 0.0 if fewer than 2 data points or if either series has zero
    variance.

    Parameters
    ----------
    xs:
        First variable.
    ys:
        Second variable.

    Returns
    -------
    float
        Pearson r in [-1, 1].
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return 0.0
    return _clamp(cov / denom, -1.0, 1.0)


def _burden_tier(total_burden: float) -> str:
    """Return a qualitative tier label for a total burden score.

    Parameters
    ----------
    total_burden:
        The total burden score.

    Returns
    -------
    str
        One of 'negligible', 'low', 'moderate', 'high', 'extreme'.
    """
    if total_burden < 0.5:
        return "negligible"
    elif total_burden < 1.5:
        return "low"
    elif total_burden < 3.0:
        return "moderate"
    elif total_burden < 5.0:
        return "high"
    else:
        return "extreme"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConditionStatus(Enum):
    """Status of a falsification condition after checking against evidence.

    Attributes
    ----------
    OPEN:
        The condition has not been checked yet (no evidence available).
    VERIFIED:
        Evidence supports the condition being true (not a counterexample).
    FALSIFIED:
        Evidence refutes the condition; this is a counterexample to the theorem.
    INCONCLUSIVE:
        Evidence is contradictory or insufficient to decide.
    """

    OPEN = "open"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    INCONCLUSIVE = "inconclusive"


class ConditionDifficulty(Enum):
    """Difficulty of checking a falsification condition.

    Attributes
    ----------
    TRIVIAL:
        Can be checked by inspection or a simple computation (weight 0.1).
    EASY:
        Requires a short calculation or well-known lemma (weight 0.3).
    MODERATE:
        Requires non-trivial but standard techniques (weight 0.5).
    HARD:
        Requires deep domain expertise or novel techniques (weight 0.8).
    INTRACTABLE:
        Believed to be computationally or logically intractable (weight 1.0).
    """

    TRIVIAL = "trivial"
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    INTRACTABLE = "intractable"


# ---------------------------------------------------------------------------
# Frozen dataclasses -- value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FalsificationConfig:
    """Configuration for the falsification burden subsystem.

    Attributes
    ----------
    max_conditions_per_theorem:
        Maximum number of falsification conditions to enumerate per theorem.
    evidence_weight_floor:
        Minimum weight for an evidence item to be counted.
    burden_high_threshold:
        Total burden score above which a theorem is flagged as high-burden.
    min_conditions_for_campaign:
        Minimum conditions required before a theorem participates in a campaign.
    prioritize_by_leverage:
        If True, the campaign processes higher-leverage theorems first.
    """

    max_conditions_per_theorem: int = 5
    evidence_weight_floor: float = 0.1
    burden_high_threshold: float = 3.0
    min_conditions_for_campaign: int = 1
    prioritize_by_leverage: bool = True


@dataclass(frozen=True, slots=True)
class TheoremRecord:
    """Record for a single proposed or proven theorem.

    Attributes
    ----------
    theorem_id:
        Unique identifier.
    statement:
        Formal or semi-formal statement of the theorem.
    domain:
        Mathematical domain of the theorem.
    predicted_leverage:
        Predicted obstruction-reduction leverage in [0, 1].
    source_proposal_id:
        ID of the proposal that generated this theorem (if any).
    created_at:
        POSIX timestamp of record creation.
    """

    theorem_id: str
    statement: str
    domain: str
    predicted_leverage: float = 0.5
    source_proposal_id: str = ""
    created_at: float = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class FalsificationCondition:
    """A single falsification condition for a theorem.

    Attributes
    ----------
    condition_id:
        Unique identifier.
    theorem_id:
        The theorem this condition belongs to.
    description:
        Natural-language description of the falsifying scenario.
    difficulty:
        How hard it is to check this condition.
    criticality:
        How critical this condition is to the theorem's truth in [0, 1].
    status:
        Current check status.
    """

    condition_id: str
    theorem_id: str
    description: str
    difficulty: ConditionDifficulty
    criticality: float
    status: ConditionStatus = ConditionStatus.OPEN

    def weighted_burden(self) -> float:
        """Return the contribution of this condition to the theorem's burden.

        Returns
        -------
        float
            difficulty_weight x criticality.
        """
        return _difficulty_to_float(self.difficulty) * self.criticality


@dataclass(frozen=True, slots=True)
class FalsificationBurden:
    """The complete falsification burden for a theorem.

    Attributes
    ----------
    theorem_id:
        The theorem whose burden this represents.
    conditions:
        Ordered tuple of falsification conditions.
    total_burden:
        Sum of weighted burdens across all conditions.
    burden_tier:
        Qualitative tier label from ``_burden_tier``.
    open_condition_count:
        Number of conditions with status OPEN (not yet checked).
    falsified_condition_count:
        Number of conditions found to be FALSIFIED.
    """

    theorem_id: str
    conditions: tuple[FalsificationCondition, ...]
    total_burden: float
    burden_tier: str
    open_condition_count: int
    falsified_condition_count: int

    def is_falsified(self) -> bool:
        """Return True if any condition has been falsified.

        Returns
        -------
        bool
            True iff ``falsified_condition_count > 0``.
        """
        return self.falsified_condition_count > 0

    def check_fraction(self) -> float:
        """Return the fraction of conditions that have been checked (non-OPEN).

        Returns
        -------
        float
            Fraction of conditions checked in [0, 1].
        """
        n = len(self.conditions)
        if n == 0:
            return 1.0
        return (n - self.open_condition_count) / n


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """A single piece of evidence against which a falsification condition is checked.

    Attributes
    ----------
    evidence_id:
        Unique identifier.
    content:
        Free-text description of the evidence.
    is_refutation:
        If True, this evidence attempts to refute the theorem (potential counterexample).
        If False, this evidence supports the theorem.
    weight:
        Evidence strength in [0, 1].
    source:
        Descriptive source label (e.g. citation, computation, or analogy).
    """

    evidence_id: str
    content: str
    is_refutation: bool = False
    weight: float = 0.5
    source: str = ""


@dataclass(frozen=True, slots=True)
class ConditionCheckResult:
    """Result of checking a single falsification condition against evidence.

    Attributes
    ----------
    condition_id:
        The condition that was checked.
    theorem_id:
        The theorem containing the condition.
    status:
        Updated status after checking.
    confidence:
        Confidence in the status determination in [0, 1].
    evidence_used:
        IDs of evidence items that contributed to the result.
    checked_at:
        POSIX timestamp.
    """

    condition_id: str
    theorem_id: str
    status: ConditionStatus
    confidence: float
    evidence_used: tuple[str, ...]
    checked_at: float = field(default_factory=_utcnow)

    def is_falsifying(self) -> bool:
        """Return True if this result indicates the condition is FALSIFIED.

        Returns
        -------
        bool
        """
        return self.status == ConditionStatus.FALSIFIED


@dataclass(frozen=True, slots=True)
class FalsificationCampaignResult:
    """Result of a full falsification campaign over a collection of theorems.

    Attributes
    ----------
    campaign_id:
        Unique ID for this campaign run.
    theorem_count:
        Number of theorems processed.
    burden_records:
        Mapping from theorem_id to its FalsificationBurden.
    condition_check_results:
        All ConditionCheckResult objects produced during the campaign.
    falsified_theorem_ids:
        IDs of theorems for which at least one condition was FALSIFIED.
    mean_burden:
        Mean total burden across all theorems.
    campaign_duration_s:
        Wall-clock duration of the campaign.
    """

    campaign_id: str
    theorem_count: int
    burden_records: dict[str, FalsificationBurden]
    condition_check_results: tuple[ConditionCheckResult, ...]
    falsified_theorem_ids: tuple[str, ...]
    mean_burden: float
    campaign_duration_s: float


@dataclass(frozen=True, slots=True)
class BurdenDistribution:
    """Distribution statistics for a collection of falsification burden scores.

    Attributes
    ----------
    n:
        Sample size.
    mean:
        Arithmetic mean of total burden scores.
    std:
        Sample standard deviation.
    min_burden:
        Minimum total burden.
    max_burden:
        Maximum total burden.
    high_burden_count:
        Number of theorems with burden above config threshold.
    tier_counts:
        Mapping from tier label to count.
    """

    n: int
    mean: float
    std: float
    min_burden: float
    max_burden: float
    high_burden_count: int
    tier_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class ConditionCoverageReport:
    """Coverage report for conditions checked in a falsification campaign.

    Attributes
    ----------
    total_conditions:
        Total number of falsification conditions across all theorems.
    checked_conditions:
        Number of conditions that have been checked (non-OPEN).
    falsified_conditions:
        Number of conditions found to be FALSIFIED.
    verified_conditions:
        Number of conditions found to be VERIFIED.
    inconclusive_conditions:
        Number of conditions with INCONCLUSIVE status.
    coverage_fraction:
        checked / total in [0, 1].
    """

    total_conditions: int
    checked_conditions: int
    falsified_conditions: int
    verified_conditions: int
    inconclusive_conditions: int
    coverage_fraction: float


@dataclass(frozen=True, slots=True)
class BurdenLeverageCorrelation:
    """Pearson correlation between falsification burden and predicted leverage.

    Attributes
    ----------
    n_pairs:
        Number of (burden, leverage) pairs used.
    pearson_r:
        Pearson correlation coefficient in [-1, 1].
    interpretation:
        Human-readable interpretation of the correlation sign and magnitude.
    """

    n_pairs: int
    pearson_r: float
    interpretation: str


@dataclass(frozen=True, slots=True)
class BurdenWitnessReport:
    """Witness report for a falsification burden computation.

    Attributes
    ----------
    theorem_id:
        The theorem whose burden was witnessed.
    is_valid:
        Whether the burden computation is internally consistent.
    issues:
        List of detected issues.
    confidence:
        Witness confidence in [0, 1].
    """

    theorem_id: str
    is_valid: bool
    issues: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class CampaignWitnessReport:
    """Witness report for a falsification campaign result.

    Attributes
    ----------
    campaign_id:
        ID of the campaign being witnessed.
    all_burdens_valid:
        Whether all per-theorem burden records passed witness checks.
    burden_witness_reports:
        Per-theorem burden witness reports.
    falsified_count_consistent:
        Whether the falsified_theorem_ids count is consistent with burden records.
    overall_valid:
        All checks passed.
    summary:
        Human-readable summary.
    """

    campaign_id: str
    all_burdens_valid: bool
    burden_witness_reports: tuple[BurdenWitnessReport, ...]
    falsified_count_consistent: bool
    overall_valid: bool
    summary: str


@dataclass(frozen=True, slots=True)
class ConditionWitnessReport:
    """Witness report for a single condition check result.

    Attributes
    ----------
    condition_id:
        The condition being witnessed.
    is_valid:
        Whether the check result is internally consistent.
    issues:
        Any detected issues.
    confidence:
        Witness confidence.
    """

    condition_id: str
    is_valid: bool
    issues: tuple[str, ...]
    confidence: float


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def score_falsification_burden(
    theorem: TheoremRecord,
    conditions: list[FalsificationCondition],
) -> float:
    """Compute the total falsification burden score for *theorem*.

    Parameters
    ----------
    theorem:
        The theorem whose burden is being scored.
    conditions:
        The enumerated falsification conditions for the theorem.

    Returns
    -------
    float
        Total burden score >= 0.
    """
    return sum(c.weighted_burden() for c in conditions)


def select_hardest_conditions(
    conditions: list[FalsificationCondition],
    top_k: int = 3,
) -> list[FalsificationCondition]:
    """Return the *top_k* hardest (highest weighted burden) conditions.

    Parameters
    ----------
    conditions:
        The full list of conditions to select from.
    top_k:
        Number of conditions to return.

    Returns
    -------
    list[FalsificationCondition]
        The top-k conditions by descending weighted burden.
    """
    return sorted(conditions, key=lambda c: c.weighted_burden(), reverse=True)[:top_k]


def run_falsification_campaign(
    theorems: list[TheoremRecord],
    evidence_map: dict[str, list[EvidenceItem]] | None = None,
    config: FalsificationConfig | None = None,
) -> FalsificationCampaignResult:
    """Run a full falsification campaign as a free function (convenience API).

    Parameters
    ----------
    theorems:
        The theorems to include in the campaign.
    evidence_map:
        Optional mapping from theorem_id to list of evidence items.
    config:
        Optional configuration; defaults are used if not provided.

    Returns
    -------
    FalsificationCampaignResult
        The campaign result.
    """
    cfg = config or FalsificationConfig()
    coord = TheoremFalsificationBurdenCoordinator(cfg)
    return coord.run_falsification_campaign(theorems, evidence_map or {})


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TheoremFalsificationBurdenCoordinator:
    """Coordinates theorem and falsification burden analysis.

    Manages the theorem registry, condition enumeration, and campaign execution.

    Parameters
    ----------
    config:
        Configuration for the falsification subsystem.
    """

    def __init__(self, config: FalsificationConfig) -> None:
        self._config = config
        self._theorem_registry: dict[str, TheoremRecord] = {}
        self._burden_registry: dict[str, FalsificationBurden] = {}
        self._all_check_results: list[ConditionCheckResult] = []

    def compute_falsification_burden(
        self,
        theorem: TheoremRecord,
    ) -> FalsificationBurden:
        """Compute and cache the falsification burden for *theorem*.

        Parameters
        ----------
        theorem:
            The theorem to analyse.

        Returns
        -------
        FalsificationBurden
            The computed burden (also stored in the internal burden registry).
        """
        self._theorem_registry[theorem.theorem_id] = theorem
        conditions_raw = _extract_conditions_heuristic(
            theorem, self._config.max_conditions_per_theorem
        )
        conditions: list[FalsificationCondition] = []
        for desc, diff, crit in conditions_raw:
            conditions.append(
                FalsificationCondition(
                    condition_id=f"cond-{_uid()[:8]}",
                    theorem_id=theorem.theorem_id,
                    description=desc,
                    difficulty=diff,
                    criticality=crit,
                    status=ConditionStatus.OPEN,
                )
            )

        total_burden = score_falsification_burden(theorem, conditions)
        tier = _burden_tier(total_burden)
        open_count = sum(1 for c in conditions if c.status == ConditionStatus.OPEN)
        falsified_count = sum(1 for c in conditions if c.status == ConditionStatus.FALSIFIED)

        burden = FalsificationBurden(
            theorem_id=theorem.theorem_id,
            conditions=tuple(conditions),
            total_burden=total_burden,
            burden_tier=tier,
            open_condition_count=open_count,
            falsified_condition_count=falsified_count,
        )
        self._burden_registry[theorem.theorem_id] = burden
        return burden

    def enumerate_falsification_conditions(
        self,
        theorem: TheoremRecord,
    ) -> list[FalsificationCondition]:
        """Enumerate falsification conditions for *theorem* without computing burden.

        If the burden has already been computed for this theorem, the stored
        conditions are returned directly.  Otherwise, conditions are freshly
        enumerated.

        Parameters
        ----------
        theorem:
            The theorem to enumerate conditions for.

        Returns
        -------
        list[FalsificationCondition]
            The enumerated conditions.
        """
        if theorem.theorem_id in self._burden_registry:
            return list(self._burden_registry[theorem.theorem_id].conditions)
        burden = self.compute_falsification_burden(theorem)
        return list(burden.conditions)

    def check_falsification_condition(
        self,
        condition: FalsificationCondition,
        evidence: list[EvidenceItem],
    ) -> ConditionCheckResult:
        """Check *condition* against *evidence* and record the result.

        Parameters
        ----------
        condition:
            The condition to check.
        evidence:
            Available evidence items.

        Returns
        -------
        ConditionCheckResult
            The check result (also appended to the internal result list).
        """
        filtered_evidence = [
            e for e in evidence if e.weight >= self._config.evidence_weight_floor
        ]
        status, confidence = _check_condition_against_evidence(condition, filtered_evidence)
        evidence_ids = tuple(e.evidence_id for e in filtered_evidence)
        result = ConditionCheckResult(
            condition_id=condition.condition_id,
            theorem_id=condition.theorem_id,
            status=status,
            confidence=confidence,
            evidence_used=evidence_ids,
            checked_at=_utcnow(),
        )
        self._all_check_results.append(result)
        return result

    def run_falsification_campaign(
        self,
        theorems: list[TheoremRecord],
        evidence_map: dict[str, list[EvidenceItem]] | None = None,
    ) -> FalsificationCampaignResult:
        """Run a full falsification campaign over *theorems*.

        For each theorem, enumerates conditions, checks each against available
        evidence (from *evidence_map*), and computes the final burden.

        Parameters
        ----------
        theorems:
            The theorems to include.
        evidence_map:
            Optional mapping from theorem_id to evidence items.

        Returns
        -------
        FalsificationCampaignResult
            The complete campaign result.
        """
        t0 = _utcnow()
        ev_map = evidence_map or {}
        campaign_id = f"campaign-{_uid()[:8]}"
        burden_records: dict[str, FalsificationBurden] = {}
        all_check_results: list[ConditionCheckResult] = []
        falsified_ids: list[str] = []

        ordered = self.prioritize_by_burden(theorems)

        for theorem in ordered:
            conditions = self.enumerate_falsification_conditions(theorem)
            if len(conditions) < self._config.min_conditions_for_campaign:
                # Still compute burden but skip checking
                burden = self.compute_falsification_burden(theorem)
                burden_records[theorem.theorem_id] = burden
                continue

            evidence = ev_map.get(theorem.theorem_id, [])
            updated_conditions: list[FalsificationCondition] = []
            theorem_falsified = False

            for cond in conditions:
                check_result = self.check_falsification_condition(cond, evidence)
                all_check_results.append(check_result)
                updated_cond = FalsificationCondition(
                    condition_id=cond.condition_id,
                    theorem_id=cond.theorem_id,
                    description=cond.description,
                    difficulty=cond.difficulty,
                    criticality=cond.criticality,
                    status=check_result.status,
                )
                updated_conditions.append(updated_cond)
                if check_result.status == ConditionStatus.FALSIFIED:
                    theorem_falsified = True

            total_burden = score_falsification_burden(theorem, updated_conditions)
            tier = _burden_tier(total_burden)
            open_count = sum(
                1 for c in updated_conditions if c.status == ConditionStatus.OPEN
            )
            falsified_count = sum(
                1 for c in updated_conditions if c.status == ConditionStatus.FALSIFIED
            )
            burden = FalsificationBurden(
                theorem_id=theorem.theorem_id,
                conditions=tuple(updated_conditions),
                total_burden=total_burden,
                burden_tier=tier,
                open_condition_count=open_count,
                falsified_condition_count=falsified_count,
            )
            burden_records[theorem.theorem_id] = burden
            if theorem_falsified:
                falsified_ids.append(theorem.theorem_id)

        burdens = list(burden_records.values())
        mean_burden = sum(b.total_burden for b in burdens) / max(len(burdens), 1)
        duration = _utcnow() - t0

        return FalsificationCampaignResult(
            campaign_id=campaign_id,
            theorem_count=len(theorems),
            burden_records=burden_records,
            condition_check_results=tuple(all_check_results),
            falsified_theorem_ids=tuple(falsified_ids),
            mean_burden=mean_burden,
            campaign_duration_s=duration,
        )

    def prioritize_by_burden(
        self,
        theorems: list[TheoremRecord],
    ) -> list[TheoremRecord]:
        """Sort *theorems* by decreasing burden (or leverage if burden unknown).

        Theorems with a pre-computed burden are sorted by total burden
        (descending) so that the most burdensome theorems are processed first.
        Theorems without a pre-computed burden are sorted by predicted leverage.

        Parameters
        ----------
        theorems:
            The theorems to sort.

        Returns
        -------
        list[TheoremRecord]
            Sorted theorems (original list not mutated).
        """
        def _sort_key(t: TheoremRecord) -> float:
            if t.theorem_id in self._burden_registry:
                return self._burden_registry[t.theorem_id].total_burden
            return t.predicted_leverage

        return sorted(theorems, key=_sort_key, reverse=True)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class TheoremFalsificationBurdenAnalyzer:
    """Analyses falsification burden patterns across a population of theorems.

    Stateless: all information is passed as parameters.
    """

    def analyze_burden_distribution(
        self,
        burdens: list[FalsificationBurden],
        high_burden_threshold: float = 3.0,
    ) -> BurdenDistribution:
        """Compute distribution statistics for a collection of burden records.

        Parameters
        ----------
        burdens:
            The burden records to analyse.
        high_burden_threshold:
            Threshold above which a theorem is considered high-burden.

        Returns
        -------
        BurdenDistribution
            Distribution statistics.
        """
        if not burdens:
            return BurdenDistribution(0, 0.0, 0.0, 0.0, 0.0, 0, {})

        values = [b.total_burden for b in burdens]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
        std = math.sqrt(variance)
        min_b = min(values)
        max_b = max(values)
        high_count = sum(1 for v in values if v >= high_burden_threshold)

        tier_counts: dict[str, int] = {}
        for b in burdens:
            tier_counts[b.burden_tier] = tier_counts.get(b.burden_tier, 0) + 1

        return BurdenDistribution(
            n=n,
            mean=mean,
            std=std,
            min_burden=min_b,
            max_burden=max_b,
            high_burden_count=high_count,
            tier_counts=tier_counts,
        )

    def analyze_condition_coverage(
        self,
        campaign: FalsificationCampaignResult,
    ) -> ConditionCoverageReport:
        """Compute condition coverage statistics for a campaign.

        Parameters
        ----------
        campaign:
            The campaign result to analyse.

        Returns
        -------
        ConditionCoverageReport
            Coverage report.
        """
        total = 0
        checked = 0
        falsified = 0
        verified = 0
        inconclusive = 0

        for burden in campaign.burden_records.values():
            for cond in burden.conditions:
                total += 1
                if cond.status != ConditionStatus.OPEN:
                    checked += 1
                if cond.status == ConditionStatus.FALSIFIED:
                    falsified += 1
                elif cond.status == ConditionStatus.VERIFIED:
                    verified += 1
                elif cond.status == ConditionStatus.INCONCLUSIVE:
                    inconclusive += 1

        coverage = checked / total if total > 0 else 0.0
        return ConditionCoverageReport(
            total_conditions=total,
            checked_conditions=checked,
            falsified_conditions=falsified,
            verified_conditions=verified,
            inconclusive_conditions=inconclusive,
            coverage_fraction=coverage,
        )

    def identify_high_burden_theorems(
        self,
        burdens: list[FalsificationBurden],
        threshold: float,
    ) -> list[TheoremRecord]:
        """Return theorem records for burdens above *threshold*.

        Since ``FalsificationBurden`` only stores ``theorem_id``, this method
        returns synthetic ``TheoremRecord`` stubs with just the ID.  In
        production the caller should join with the theorem registry.

        Parameters
        ----------
        burdens:
            The burden records to filter.
        threshold:
            Minimum total burden to include.

        Returns
        -------
        list[TheoremRecord]
            Stub theorem records for all high-burden theorems.
        """
        high_burden = [b for b in burdens if b.total_burden >= threshold]
        return [
            TheoremRecord(
                theorem_id=b.theorem_id,
                statement=f"(high-burden theorem {b.theorem_id})",
                domain="unknown",
                predicted_leverage=0.0,
            )
            for b in sorted(high_burden, key=lambda b: b.total_burden, reverse=True)
        ]

    def correlate_burden_with_leverage(
        self,
        burden_leverage_pairs: list[tuple[FalsificationBurden, float]],
    ) -> BurdenLeverageCorrelation:
        """Compute the Pearson correlation between burden and leverage.

        Parameters
        ----------
        burden_leverage_pairs:
            List of (FalsificationBurden, leverage_score) pairs.

        Returns
        -------
        BurdenLeverageCorrelation
            Correlation result.
        """
        n = len(burden_leverage_pairs)
        if n < 2:
            return BurdenLeverageCorrelation(n, 0.0, "Insufficient data for correlation.")

        burdens_x = [b.total_burden for b, _ in burden_leverage_pairs]
        leverages_y = [lev for _, lev in burden_leverage_pairs]
        r = _pearson_correlation(burdens_x, leverages_y)

        if r > 0.6:
            interpretation = (
                f"Strong positive correlation (r={r:.3f}): high-leverage theorems also carry "
                f"high falsification burden -- they make bold, hard-to-disprove claims."
            )
        elif r > 0.2:
            interpretation = (
                f"Moderate positive correlation (r={r:.3f}): some tendency for higher-leverage "
                f"theorems to have greater falsification burden."
            )
        elif r > -0.2:
            interpretation = (
                f"Near-zero correlation (r={r:.3f}): burden and leverage are approximately "
                f"independent in this population."
            )
        elif r > -0.6:
            interpretation = (
                f"Moderate negative correlation (r={r:.3f}): high-leverage proposals tend to "
                f"be easy to falsify -- possible over-speculation."
            )
        else:
            interpretation = (
                f"Strong negative correlation (r={r:.3f}): high-leverage proposals are mostly "
                f"easy to falsify, suggesting systematic over-optimism."
            )

        return BurdenLeverageCorrelation(
            n_pairs=n,
            pearson_r=r,
            interpretation=interpretation,
        )


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class TheoremFalsificationBurdenWitness:
    """Witness for theorem and burden assessments.

    All methods are pure with respect to the witness itself.
    """

    def witness_burden_computation(
        self,
        theorem: TheoremRecord,
        burden: FalsificationBurden,
    ) -> BurdenWitnessReport:
        """Check that a burden computation is internally consistent.

        Checks:
        * ``burden.theorem_id`` matches ``theorem.theorem_id``.
        * ``total_burden`` equals the sum of weighted burdens over conditions.
        * ``open_condition_count`` matches the actual count of OPEN conditions.
        * ``falsified_condition_count`` matches the actual count.
        * No condition has criticality outside [0, 1].

        Parameters
        ----------
        theorem:
            The theorem whose burden is being witnessed.
        burden:
            The burden computation to validate.

        Returns
        -------
        BurdenWitnessReport
            Witness report.
        """
        issues: list[str] = []
        if burden.theorem_id != theorem.theorem_id:
            issues.append(
                f"burden.theorem_id '{burden.theorem_id}' != "
                f"theorem.theorem_id '{theorem.theorem_id}'."
            )

        recomputed_burden = sum(c.weighted_burden() for c in burden.conditions)
        if abs(recomputed_burden - burden.total_burden) > 1e-6:
            issues.append(
                f"total_burden {burden.total_burden:.6f} does not equal "
                f"recomputed {recomputed_burden:.6f}."
            )

        actual_open = sum(1 for c in burden.conditions if c.status == ConditionStatus.OPEN)
        if actual_open != burden.open_condition_count:
            issues.append(
                f"open_condition_count {burden.open_condition_count} != actual {actual_open}."
            )

        actual_falsified = sum(
            1 for c in burden.conditions if c.status == ConditionStatus.FALSIFIED
        )
        if actual_falsified != burden.falsified_condition_count:
            issues.append(
                f"falsified_condition_count {burden.falsified_condition_count} "
                f"!= actual {actual_falsified}."
            )

        for cond in burden.conditions:
            if not (0.0 <= cond.criticality <= 1.0):
                issues.append(
                    f"Condition '{cond.condition_id}' has criticality "
                    f"{cond.criticality:.4f} outside [0, 1]."
                )

        is_valid = len(issues) == 0
        confidence = _clamp(1.0 - 0.2 * len(issues))
        return BurdenWitnessReport(
            theorem_id=theorem.theorem_id,
            is_valid=is_valid,
            issues=tuple(issues),
            confidence=confidence,
        )

    def witness_falsification_campaign(
        self,
        campaign: FalsificationCampaignResult,
    ) -> CampaignWitnessReport:
        """Witness a full falsification campaign result.

        Parameters
        ----------
        campaign:
            The campaign result to witness.

        Returns
        -------
        CampaignWitnessReport
            Campaign-level witness report.
        """
        burden_witness_reports: list[BurdenWitnessReport] = []
        for tid, burden in campaign.burden_records.items():
            stub_theorem = TheoremRecord(
                theorem_id=tid,
                statement="(campaign stub)",
                domain="unknown",
            )
            report = self.witness_burden_computation(stub_theorem, burden)
            burden_witness_reports.append(report)

        all_valid = all(r.is_valid for r in burden_witness_reports)

        # Check that falsified_theorem_ids is consistent with burden records
        actual_falsified = {
            tid
            for tid, burden in campaign.burden_records.items()
            if burden.is_falsified()
        }
        claimed_falsified = set(campaign.falsified_theorem_ids)
        count_consistent = actual_falsified == claimed_falsified

        overall_valid = all_valid and count_consistent
        if overall_valid:
            summary = (
                f"Campaign '{campaign.campaign_id}' passed all witness checks. "
                f"{campaign.theorem_count} theorems processed; "
                f"{len(campaign.falsified_theorem_ids)} falsified."
            )
        else:
            issues: list[str] = []
            if not all_valid:
                bad = sum(1 for r in burden_witness_reports if not r.is_valid)
                issues.append(f"{bad} burden record(s) failed witness check.")
            if not count_consistent:
                issues.append(
                    f"Falsified set mismatch: claimed {len(claimed_falsified)}, "
                    f"actual {len(actual_falsified)}."
                )
            summary = "Campaign witness issues: " + "; ".join(issues)

        return CampaignWitnessReport(
            campaign_id=campaign.campaign_id,
            all_burdens_valid=all_valid,
            burden_witness_reports=tuple(burden_witness_reports),
            falsified_count_consistent=count_consistent,
            overall_valid=overall_valid,
            summary=summary,
        )

    def witness_condition_check(
        self,
        condition: FalsificationCondition,
        result: ConditionCheckResult,
    ) -> ConditionWitnessReport:
        """Witness a single condition check result.

        Checks:
        * ``result.condition_id`` matches ``condition.condition_id``.
        * ``result.confidence`` in [0, 1].
        * If status is OPEN, confidence should be 0.
        * If status is FALSIFIED, confidence should be > 0.

        Parameters
        ----------
        condition:
            The condition that was checked.
        result:
            The check result to validate.

        Returns
        -------
        ConditionWitnessReport
            Condition-level witness report.
        """
        issues: list[str] = []
        if result.condition_id != condition.condition_id:
            issues.append(
                f"result.condition_id '{result.condition_id}' != "
                f"condition.condition_id '{condition.condition_id}'."
            )
        if not (0.0 <= result.confidence <= 1.0):
            issues.append(f"confidence {result.confidence:.4f} outside [0, 1].")
        if result.status == ConditionStatus.OPEN and result.confidence != 0.0:
            issues.append(
                f"OPEN status should have confidence 0 but got {result.confidence:.4f}."
            )
        if result.status == ConditionStatus.FALSIFIED and result.confidence == 0.0:
            issues.append("FALSIFIED status should have confidence > 0.")

        is_valid = len(issues) == 0
        confidence = _clamp(1.0 - 0.3 * len(issues))
        return ConditionWitnessReport(
            condition_id=condition.condition_id,
            is_valid=is_valid,
            issues=tuple(issues),
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== TheoremFalsificationBurden smoke test ===\n")

    cfg = FalsificationConfig(
        max_conditions_per_theorem=4,
        evidence_weight_floor=0.05,
        burden_high_threshold=2.5,
        min_conditions_for_campaign=1,
        prioritize_by_leverage=True,
    )

    theorems = [
        TheoremRecord(
            theorem_id="thm-ag-001",
            statement=(
                "Every flat morphism of finite presentation between Noetherian schemes "
                "has cohomological descent in the étale topology."
            ),
            domain="algebraic-geometry",
            predicted_leverage=0.80,
            source_proposal_id="prop-001",
        ),
        TheoremRecord(
            theorem_id="thm-nt-001",
            statement=(
                "Every modular form of weight k >= 2 has an associated Galois representation "
                "whose arithmetic is controlled by the prime ramification data."
            ),
            domain="number-theory",
            predicted_leverage=0.65,
            source_proposal_id="prop-002",
        ),
        TheoremRecord(
            theorem_id="thm-cat-001",
            statement=(
                "The adjunction between a left Kan extension functor and its right adjoint "
                "preserves all small limits in the enriched setting."
            ),
            domain="category-theory",
            predicted_leverage=0.55,
            source_proposal_id="prop-003",
        ),
        TheoremRecord(
            theorem_id="thm-top-001",
            statement=(
                "Every smooth simply connected 4-manifold with even intersection form "
                "is homeomorphic to a connected sum of standard pieces."
            ),
            domain="topology",
            predicted_leverage=0.72,
            source_proposal_id="prop-004",
        ),
    ]

    evidence_map: dict[str, list[EvidenceItem]] = {
        "thm-ag-001": [
            EvidenceItem("ev-001", "flat base change in étale cohomology is well established",
                         is_refutation=False, weight=0.8, source="SGA 4"),
            EvidenceItem("ev-002", "morphism fails to be flat in mixed characteristic",
                         is_refutation=True, weight=0.4, source="counterexample search"),
        ],
        "thm-nt-001": [
            EvidenceItem("ev-003", "modular Galois representations for weight >= 2 are known",
                         is_refutation=False, weight=0.9, source="Deligne 1971"),
        ],
        "thm-top-001": [
            EvidenceItem("ev-004", "smooth 4-manifold surgery obstructions can be non-zero",
                         is_refutation=True, weight=0.6, source="Donaldson theory"),
            EvidenceItem("ev-005", "topological 4-manifolds do satisfy the classification",
                         is_refutation=False, weight=0.7, source="Freedman 1982"),
        ],
    }

    coord = TheoremFalsificationBurdenCoordinator(cfg)

    # --- Coordinator: compute burdens individually ---
    print("Per-theorem burdens:")
    for t in theorems:
        burden = coord.compute_falsification_burden(t)
        print(
            f"  {t.theorem_id:20s}  burden={burden.total_burden:.3f}  "
            f"tier={burden.burden_tier:10s}  conditions={len(burden.conditions)}"
        )

    # --- Campaign ---
    campaign = coord.run_falsification_campaign(theorems, evidence_map)
    print(f"\nCampaign '{campaign.campaign_id}':")
    print(f"  theorems={campaign.theorem_count}  "
          f"mean_burden={campaign.mean_burden:.3f}  "
          f"falsified={len(campaign.falsified_theorem_ids)}")
    print(f"  condition checks performed: {len(campaign.condition_check_results)}")
    for tid in campaign.falsified_theorem_ids:
        print(f"  ⚠  Falsified: {tid}")

    # --- Prioritize by burden ---
    prioritized = coord.prioritize_by_burden(theorems)
    print(f"\nPrioritized order:")
    for t in prioritized:
        burd = campaign.burden_records.get(t.theorem_id)
        burden_str = f"{burd.total_burden:.3f}" if burd else "?"
        print(f"  {t.theorem_id}  burden={burden_str}")

    # --- Analyzer ---
    analyzer = TheoremFalsificationBurdenAnalyzer()
    all_burdens = list(campaign.burden_records.values())
    dist = analyzer.analyze_burden_distribution(all_burdens, high_burden_threshold=2.5)
    print(f"\nBurden Distribution:")
    print(f"  n={dist.n}  mean={dist.mean:.3f}  std={dist.std:.3f}  "
          f"high_burden={dist.high_burden_count}")
    print(f"  tier_counts={dist.tier_counts}")

    cov = analyzer.analyze_condition_coverage(campaign)
    print(f"\nCondition Coverage:")
    print(f"  total={cov.total_conditions}  checked={cov.checked_conditions}  "
          f"coverage={cov.coverage_fraction:.3f}")
    print(f"  falsified={cov.falsified_conditions}  verified={cov.verified_conditions}  "
          f"inconclusive={cov.inconclusive_conditions}")

    pairs = [
        (campaign.burden_records[t.theorem_id], t.predicted_leverage)
        for t in theorems
        if t.theorem_id in campaign.burden_records
    ]
    corr = analyzer.correlate_burden_with_leverage(pairs)
    print(f"\nBurden-Leverage Correlation:")
    print(f"  n={corr.n_pairs}  r={corr.pearson_r:.4f}")
    print(f"  {corr.interpretation}")

    high_burden_theorems = analyzer.identify_high_burden_theorems(all_burdens, threshold=2.5)
    print(f"\nHigh-burden theorems (threshold=2.5): {[t.theorem_id for t in high_burden_theorems]}")

    # --- Hardest conditions ---
    first_burden = all_burdens[0]
    hardest = select_hardest_conditions(list(first_burden.conditions), top_k=2)
    print(f"\nHardest conditions for {first_burden.theorem_id}:")
    for c in hardest:
        print(f"  [{c.difficulty.value:12s}  crit={c.criticality:.3f}] {c.description[:60]}")

    # --- Witness ---
    witness = TheoremFalsificationBurdenWitness()
    bw = witness.witness_burden_computation(
        theorems[0], campaign.burden_records[theorems[0].theorem_id]
    )
    print(f"\nBurden Witness (thm-ag-001): valid={bw.is_valid}  confidence={bw.confidence:.3f}")
    if bw.issues:
        for issue in bw.issues:
            print(f"  issue: {issue}")

    cw = witness.witness_falsification_campaign(campaign)
    print(f"\nCampaign Witness: overall_valid={cw.overall_valid}")
    print(f"  {cw.summary}")

    if campaign.condition_check_results:
        first_result = campaign.condition_check_results[0]
        matching_cond: FalsificationCondition | None = None
        for b in campaign.burden_records.values():
            for c in b.conditions:
                if c.condition_id == first_result.condition_id:
                    matching_cond = c
                    break
        if matching_cond is not None:
            cond_wit = witness.witness_condition_check(matching_cond, first_result)
            print(f"\nCondition Witness ({matching_cond.condition_id}): "
                  f"valid={cond_wit.is_valid}  confidence={cond_wit.confidence:.3f}")

    # --- Free-function API ---
    campaign2 = run_falsification_campaign(theorems[:2], evidence_map, config=cfg)
    print(f"\nFree-function campaign: {campaign2.campaign_id}  "
          f"theorems={campaign2.theorem_count}  mean_burden={campaign2.mean_burden:.3f}")

    # --- score_falsification_burden free function ---
    conds = coord.enumerate_falsification_conditions(theorems[0])
    raw_score = score_falsification_burden(theorems[0], conds)
    print(f"\nFree-function burden score for {theorems[0].theorem_id}: {raw_score:.4f}")

    print("\n=== Smoke test passed ===")
