"""Section 14.4 — Trust Requirements for the Unified Problem Atlas.

copilot: trust requirement builder and gap analysis engine.

This module implements §14.4 of Theory2.tex, providing the complete machinery
for specifying, checking, and analyzing trust requirements for problem classes.

A *trust requirement* specifies what evidence channels must contribute, at what
trust levels, and in what combination, in order to count the problem as
sufficiently verified.

Key components:
  TrustRequirementBuilder — Fluent builder for EvidenceRequirement instances
  RequirementChecker      — Checks whether current evidence satisfies requirements
  GapAnalyzer             — Identifies gaps and computes gap magnitude
  RequirementComposer     — Composes requirements for composite problems
  TrustBudgetManager      — Manages trust budget allocation
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        EvidenceRequirement,
        ConjunctionMode,
        ProblemClass,
        DifficultyLevel,
    )
except ImportError:
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    ConjunctionMode = None  # type: ignore[assignment]
    ProblemClass = object  # type: ignore[assignment,misc]
    DifficultyLevel = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.problem_atlas.evidence_channels import (
        ChannelContribution,
        ChannelKind,
        TrustLevelComputer,
        ChannelRegistry,
    )
except ImportError:
    ChannelContribution = object  # type: ignore[assignment,misc]
    ChannelKind = None  # type: ignore[assignment]
    TrustLevelComputer = object  # type: ignore[assignment,misc]
    ChannelRegistry = object  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ChannelId: TypeAlias = str
TrustLevel: TypeAlias = float
EvidenceMap: TypeAlias = dict[str, float]  # channel_id → trust score


# ---------------------------------------------------------------------------
# RequirementStatus
# ---------------------------------------------------------------------------


class RequirementStatus(str, Enum):
    """Status of an evidence requirement after checking against current evidence.

    Attributes:
        SATISFIED: All required channels met the minimum trust threshold.
        PARTIALLY_SATISFIED: Some channels met the threshold but not all.
        UNSATISFIED: No channels (or too few) met the threshold.
        OVER_SATISFIED: All channels exceeded the threshold with margin.
        UNKNOWN: The check could not be completed due to missing information.
    """

    SATISFIED = "SATISFIED"
    PARTIALLY_SATISFIED = "PARTIALLY_SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    OVER_SATISFIED = "OVER_SATISFIED"
    UNKNOWN = "UNKNOWN"

    def is_acceptable(self) -> bool:
        """Return True when the status represents a passing outcome.

        Returns:
            bool: True for SATISFIED and OVER_SATISFIED; False otherwise.
        """
        return self in (RequirementStatus.SATISFIED, RequirementStatus.OVER_SATISFIED)


# ---------------------------------------------------------------------------
# GapSeverity
# ---------------------------------------------------------------------------


class GapSeverity(str, Enum):
    """Severity classification for a single trust gap.

    Attributes:
        NONE: No gap; the channel meets or exceeds the requirement.
        MINOR: Small shortfall; addressable with minor effort.
        MODERATE: Meaningful shortfall; requires planned effort.
        SEVERE: Large shortfall; blocks most verification workflows.
        CRITICAL: Trust is zero or the channel is completely absent.
    """

    NONE = "NONE"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"
    CRITICAL = "CRITICAL"

    def score(self) -> float:
        """Return a numeric severity score in ``[0.0, 1.0]``.

        Returns:
            float: 0.0 for NONE, 0.25 for MINOR, 0.5 for MODERATE,
                0.75 for SEVERE, 1.0 for CRITICAL.
        """
        scores: dict[GapSeverity, float] = {
            GapSeverity.NONE: 0.0,
            GapSeverity.MINOR: 0.25,
            GapSeverity.MODERATE: 0.5,
            GapSeverity.SEVERE: 0.75,
            GapSeverity.CRITICAL: 1.0,
        }
        return scores[self]

    @classmethod
    def from_gap(cls, gap: float) -> "GapSeverity":
        """Classify a gap magnitude as a :class:`GapSeverity`.

        Args:
            gap: The raw gap magnitude in ``[0.0, 1.0]``.  Values ≤ 0 produce
                NONE; values ≥ 1 produce CRITICAL.

        Returns:
            GapSeverity: The corresponding severity level.
        """
        if gap <= 0.0:
            return cls.NONE
        if gap < 0.1:
            return cls.MINOR
        if gap < 0.3:
            return cls.MODERATE
        if gap < 0.6:
            return cls.SEVERE
        return cls.CRITICAL


# ---------------------------------------------------------------------------
# TrustGap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustGap:
    """An immutable record describing the gap between required and actual trust.

    A gap exists when the actual trust score for a channel falls below the
    minimum trust threshold specified by the requirement.

    Attributes:
        channel_id: The channel for which the gap was measured.
        required_trust: The minimum trust level demanded by the requirement.
        actual_trust: The trust level actually provided by the channel.
        gap_magnitude: The absolute difference ``required_trust - actual_trust``.
            Always ≥ 0; a closed gap has magnitude 0.
        severity: :class:`GapSeverity` classification of this gap.
        is_blocking: True when this gap prevents verification from proceeding.
        suggested_action: A human-readable remediation suggestion.
    """

    channel_id: str
    required_trust: float
    actual_trust: float
    gap_magnitude: float
    severity: GapSeverity
    is_blocking: bool
    suggested_action: str

    def is_closed(self) -> bool:
        """Return True when the gap has been fully closed.

        Returns:
            bool: True if ``gap_magnitude`` ≤ 0 (i.e. actual ≥ required).
        """
        return self.gap_magnitude <= 0.0

    def relative_gap(self) -> float:
        """Return the gap expressed as a fraction of the required trust.

        Args: (none)

        Returns:
            float: ``gap_magnitude / required_trust`` when ``required_trust > 0``,
                else 0.0.
        """
        if self.required_trust <= 0.0:
            return 0.0
        return self.gap_magnitude / self.required_trust

    def to_dict(self) -> dict[str, Any]:
        """Serialize this gap record to a plain dictionary.

        Returns:
            dict[str, Any]: JSON-serialisable representation.
        """
        return {
            "channel_id": self.channel_id,
            "required_trust": self.required_trust,
            "actual_trust": self.actual_trust,
            "gap_magnitude": self.gap_magnitude,
            "severity": self.severity.value,
            "is_blocking": self.is_blocking,
            "suggested_action": self.suggested_action,
            "is_closed": self.is_closed(),
            "relative_gap": self.relative_gap(),
        }


# ---------------------------------------------------------------------------
# RequirementCheckResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequirementCheckResult:
    """Immutable result of checking an evidence requirement.

    Captures both the overall pass/fail verdict and fine-grained per-channel
    gap information.

    Attributes:
        req_id: Identifier of the requirement that was checked.
        status: Aggregate :class:`RequirementStatus` verdict.
        gaps: Tuple of :class:`TrustGap` records for channels that did not
            fully satisfy the requirement.
        satisfied_channels: Channel IDs that individually passed.
        missing_channels: Channel IDs required but absent from the evidence map.
        aggregate_trust: The aggregate trust score computed for this check.
        message: Human-readable summary of the check outcome.
    """

    req_id: str
    status: RequirementStatus
    gaps: tuple[TrustGap, ...]
    satisfied_channels: tuple[str, ...]
    missing_channels: tuple[str, ...]
    aggregate_trust: float
    message: str

    def has_blocking_gaps(self) -> bool:
        """Return True when at least one gap is marked blocking.

        Returns:
            bool: True if any gap in ``self.gaps`` has ``is_blocking=True``.
        """
        return any(g.is_blocking for g in self.gaps)

    def gap_count(self) -> int:
        """Return the total number of gaps recorded.

        Returns:
            int: Length of ``self.gaps``.
        """
        return len(self.gaps)

    def worst_gap(self) -> TrustGap | None:
        """Return the gap with the highest ``gap_magnitude``.

        Returns:
            TrustGap | None: The most severe gap, or ``None`` if there are none.
        """
        if not self.gaps:
            return None
        return max(self.gaps, key=lambda g: g.gap_magnitude)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain dictionary.

        Returns:
            dict[str, Any]: JSON-serialisable representation.
        """
        return {
            "req_id": self.req_id,
            "status": self.status.value,
            "gaps": [g.to_dict() for g in self.gaps],
            "satisfied_channels": list(self.satisfied_channels),
            "missing_channels": list(self.missing_channels),
            "aggregate_trust": self.aggregate_trust,
            "message": self.message,
            "has_blocking_gaps": self.has_blocking_gaps(),
            "gap_count": self.gap_count(),
        }


# ---------------------------------------------------------------------------
# TrustRequirementBuilder
# ---------------------------------------------------------------------------


class TrustRequirementBuilder:
    """Fluent builder for constructing :class:`EvidenceRequirement` instances.

    Provides a readable, step-by-step API for assembling the configuration of
    a trust requirement.  Call :meth:`build` to produce the final immutable
    requirement object.

    Example::

        req = (
            TrustRequirementBuilder("SEARCH")
            .require_channel("TESTING")
            .require_channel("STATIC_ANALYSIS")
            .with_minimum_trust(0.6)
            .with_conjunction_mode("ALL")
            .build()
        )

    Args:
        problem_class_id: Identifier of the problem class this requirement
            belongs to.
    """

    def __init__(self, problem_class_id: str) -> None:
        """Initialise the builder.

        Args:
            problem_class_id: Identifier of the owning problem class.
        """
        self._problem_class_id = problem_class_id
        self._req_id: str = str(uuid.uuid4())
        self._required_channels: list[str] = []
        self._minimum_trust: float = 0.5
        self._conjunction_mode: Any = "ALL"
        self._allowed_residuals: list[str] = []
        self._forbidden_residuals: list[str] = []
        self._temporal_constraints: dict[str, Any] = {}
        self._override_conditions: list[str] = []

    def require_channel(self, channel: str) -> "TrustRequirementBuilder":
        """Add a required evidence channel to the requirement.

        Duplicate channel IDs are ignored.

        Args:
            channel: Channel identifier to add.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.
        """
        if channel not in self._required_channels:
            self._required_channels.append(channel)
        return self

    def with_minimum_trust(self, level: float) -> "TrustRequirementBuilder":
        """Set the minimum trust threshold.

        Args:
            level: Required minimum trust level in ``[0.0, 1.0]``.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.

        Raises:
            ValueError: If *level* is outside ``[0.0, 1.0]``.
        """
        if not (0.0 <= level <= 1.0):
            raise ValueError(
                f"Minimum trust level must be in [0.0, 1.0]; got {level!r}."
            )
        self._minimum_trust = level
        return self

    def with_conjunction_mode(
        self, mode: Any
    ) -> "TrustRequirementBuilder":
        """Set the conjunction mode for aggregating channel evidence.

        Args:
            mode: A :class:`ConjunctionMode` value or a string name
                such as ``"ALL"``, ``"ANY"``, or ``"WEIGHTED"``.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.
        """
        self._conjunction_mode = mode
        return self

    def allow_residual(self, residual: str) -> "TrustRequirementBuilder":
        """Permit a named residual uncertainty class.

        Residuals are strings naming classes of unresolved uncertainty that the
        requirement tolerates (e.g. ``"performance_uncertainty"``).

        Args:
            residual: Residual class name to allow.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.
        """
        if residual not in self._allowed_residuals:
            self._allowed_residuals.append(residual)
        return self

    def forbid_residual(self, residual: str) -> "TrustRequirementBuilder":
        """Explicitly forbid a named residual uncertainty class.

        A forbidden residual causes the requirement to be unsatisfied if that
        residual is present in the context.

        Args:
            residual: Residual class name to forbid.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.
        """
        if residual not in self._forbidden_residuals:
            self._forbidden_residuals.append(residual)
        return self

    def with_temporal_constraint(
        self, key: str, value: Any
    ) -> "TrustRequirementBuilder":
        """Attach a temporal constraint key-value pair to the requirement.

        Temporal constraints express time-based conditions such as evidence
        freshness windows.

        Args:
            key: Constraint name (e.g. ``"max_evidence_age_seconds"``).
            value: Constraint value.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.
        """
        self._temporal_constraints[key] = value
        return self

    def with_override_condition(
        self, condition: str
    ) -> "TrustRequirementBuilder":
        """Add a condition string under which the requirement can be overridden.

        Override conditions are free-text predicates evaluated by
        :meth:`RequirementChecker.check_overrides`.

        Args:
            condition: Condition predicate string.

        Returns:
            TrustRequirementBuilder: ``self`` for chaining.
        """
        if condition not in self._override_conditions:
            self._override_conditions.append(condition)
        return self

    def build(self) -> Any:
        """Construct and return the configured :class:`EvidenceRequirement`.

        When the models package is unavailable the method returns a plain
        :class:`_FallbackRequirement` dataclass so that the rest of the
        module remains functional.

        Returns:
            EvidenceRequirement: The built requirement.
        """
        try:
            from jugeo.problem_modes.problem_atlas.models import (
                EvidenceRequirement as _ER,
            )
            return _ER(  # type: ignore[call-arg]
                req_id=self._req_id,
                problem_class_id=self._problem_class_id,
                required_channels=tuple(self._required_channels),
                minimum_trust=self._minimum_trust,
                conjunction_mode=self._conjunction_mode,
                allowed_residuals=tuple(self._allowed_residuals),
                forbidden_residuals=tuple(self._forbidden_residuals),
                temporal_constraints=dict(self._temporal_constraints),
                override_conditions=tuple(self._override_conditions),
            )
        except Exception:
            return _FallbackRequirement(
                req_id=self._req_id,
                problem_class_id=self._problem_class_id,
                required_channels=tuple(self._required_channels),
                minimum_trust=self._minimum_trust,
                conjunction_mode=str(self._conjunction_mode),
                allowed_residuals=tuple(self._allowed_residuals),
                forbidden_residuals=tuple(self._forbidden_residuals),
                temporal_constraints=dict(self._temporal_constraints),
                override_conditions=tuple(self._override_conditions),
            )


@dataclass(frozen=True, slots=True)
class _FallbackRequirement:
    """Internal fallback when the models package is unavailable.

    This mirrors the public EvidenceRequirement interface so all other
    classes in this module work correctly in degraded environments.
    """

    req_id: str
    problem_class_id: str
    required_channels: tuple[str, ...]
    minimum_trust: float
    conjunction_mode: str
    allowed_residuals: tuple[str, ...]
    forbidden_residuals: tuple[str, ...]
    temporal_constraints: dict[str, Any]
    override_conditions: tuple[str, ...]


# ---------------------------------------------------------------------------
# RequirementChecker
# ---------------------------------------------------------------------------


class RequirementChecker:
    """Checks whether a body of evidence satisfies an evidence requirement.

    The checker dispatches to mode-specific sub-methods based on the
    requirement's ``conjunction_mode``.  All sub-methods return a
    :class:`RequirementCheckResult`.
    """

    def __init__(self) -> None:
        """Initialise the checker (no configuration required)."""
        pass

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def check(
        self,
        requirement: Any,
        evidence_map: dict[str, float],
    ) -> RequirementCheckResult:
        """Check *evidence_map* against *requirement*.

        Dispatches to the appropriate mode-specific checker.

        Args:
            requirement: An :class:`EvidenceRequirement` (or fallback).
            evidence_map: Mapping from channel_id to trust score.

        Returns:
            RequirementCheckResult: The check result.
        """
        mode_str = _get_mode_str(requirement)

        if "ANY" in mode_str:
            return self.check_disjunction(requirement, evidence_map)
        if "WEIGHTED" in mode_str:
            return self.check_weighted(requirement, evidence_map)
        # Default: ALL (conjunction).
        return self.check_conjunction(requirement, evidence_map)

    # ------------------------------------------------------------------
    # Mode-specific checkers
    # ------------------------------------------------------------------

    def check_conjunction(
        self,
        requirement: Any,
        evidence_map: dict[str, float],
    ) -> RequirementCheckResult:
        """Check using ALL (conjunction) mode — every channel must pass.

        Args:
            requirement: The evidence requirement.
            evidence_map: Channel trust scores.

        Returns:
            RequirementCheckResult: Check result with per-channel gaps.
        """
        required = list(_get_required_channels(requirement))
        min_trust = _get_min_trust(requirement)
        req_id = _get_req_id(requirement)

        gaps = self._compute_gaps(requirement, evidence_map)
        satisfied = [
            cid for cid in required
            if cid in evidence_map and evidence_map[cid] >= min_trust
        ]
        missing = [cid for cid in required if cid not in evidence_map]

        if not required:
            status = RequirementStatus.SATISFIED
            agg = 1.0
            msg = "No channels required; requirement trivially satisfied."
        elif missing:
            status = RequirementStatus.UNSATISFIED
            agg = sum(evidence_map.get(c, 0.0) for c in required) / len(required)
            msg = f"Missing evidence from {len(missing)} required channel(s)."
        elif len(satisfied) == len(required):
            agg = min(evidence_map.get(c, 0.0) for c in required)
            if agg > min_trust + 0.1:
                status = RequirementStatus.OVER_SATISFIED
                msg = "All required channels exceeded the trust threshold."
            else:
                status = RequirementStatus.SATISFIED
                msg = "All required channels met the trust threshold."
        elif satisfied:
            status = RequirementStatus.PARTIALLY_SATISFIED
            agg = sum(evidence_map.get(c, 0.0) for c in required) / len(required)
            msg = (
                f"{len(satisfied)}/{len(required)} channels satisfied. "
                f"{len(required) - len(satisfied)} channel(s) below threshold."
            )
        else:
            status = RequirementStatus.UNSATISFIED
            agg = sum(evidence_map.get(c, 0.0) for c in required) / max(1, len(required))
            msg = "No required channels met the trust threshold."

        return RequirementCheckResult(
            req_id=req_id,
            status=status,
            gaps=tuple(gaps),
            satisfied_channels=tuple(satisfied),
            missing_channels=tuple(missing),
            aggregate_trust=max(0.0, min(1.0, agg)),
            message=msg,
        )

    def check_disjunction(
        self,
        requirement: Any,
        evidence_map: dict[str, float],
    ) -> RequirementCheckResult:
        """Check using ANY (disjunction) mode — at least one channel must pass.

        Args:
            requirement: The evidence requirement.
            evidence_map: Channel trust scores.

        Returns:
            RequirementCheckResult: Check result.
        """
        required = list(_get_required_channels(requirement))
        min_trust = _get_min_trust(requirement)
        req_id = _get_req_id(requirement)

        gaps = self._compute_gaps(requirement, evidence_map)
        satisfied = [
            cid for cid in required
            if cid in evidence_map and evidence_map[cid] >= min_trust
        ]
        missing = [cid for cid in required if cid not in evidence_map]

        if not required:
            return RequirementCheckResult(
                req_id=req_id,
                status=RequirementStatus.SATISFIED,
                gaps=(),
                satisfied_channels=(),
                missing_channels=(),
                aggregate_trust=1.0,
                message="No channels required; trivially satisfied.",
            )

        agg = max((evidence_map.get(c, 0.0) for c in required), default=0.0)

        if satisfied:
            status = (
                RequirementStatus.OVER_SATISFIED
                if len(satisfied) == len(required)
                else RequirementStatus.SATISFIED
            )
            msg = f"{len(satisfied)} channel(s) satisfied (ANY mode)."
        else:
            status = RequirementStatus.UNSATISFIED
            msg = "No channel reached the minimum trust threshold (ANY mode)."

        return RequirementCheckResult(
            req_id=req_id,
            status=status,
            gaps=tuple(gaps),
            satisfied_channels=tuple(satisfied),
            missing_channels=tuple(missing),
            aggregate_trust=max(0.0, min(1.0, agg)),
            message=msg,
        )

    def check_weighted(
        self,
        requirement: Any,
        evidence_map: dict[str, float],
        weights: dict[str, float] | None = None,
    ) -> RequirementCheckResult:
        """Check using WEIGHTED mode — weighted average must meet minimum.

        When *weights* is ``None``, equal weights are assumed for all channels.

        Args:
            requirement: The evidence requirement.
            evidence_map: Channel trust scores.
            weights: Optional per-channel weights (need not sum to 1; they are
                normalised internally).

        Returns:
            RequirementCheckResult: Check result.
        """
        required = list(_get_required_channels(requirement))
        min_trust = _get_min_trust(requirement)
        req_id = _get_req_id(requirement)

        gaps = self._compute_gaps(requirement, evidence_map)
        missing = [cid for cid in required if cid not in evidence_map]

        if not required:
            return RequirementCheckResult(
                req_id=req_id,
                status=RequirementStatus.SATISFIED,
                gaps=(),
                satisfied_channels=(),
                missing_channels=(),
                aggregate_trust=1.0,
                message="No channels required; trivially satisfied.",
            )

        effective_weights = weights or {cid: 1.0 for cid in required}
        total_weight = sum(effective_weights.get(c, 1.0) for c in required)

        if total_weight == 0.0:
            agg = 0.0
        else:
            agg = sum(
                evidence_map.get(c, 0.0) * effective_weights.get(c, 1.0)
                for c in required
            ) / total_weight

        agg = max(0.0, min(1.0, agg))
        satisfied = [
            cid for cid in required
            if cid in evidence_map and evidence_map[cid] >= min_trust
        ]

        if agg >= min_trust and not missing:
            status = (
                RequirementStatus.OVER_SATISFIED
                if agg > min_trust + 0.1
                else RequirementStatus.SATISFIED
            )
            msg = f"Weighted average trust {agg:.3f} meets threshold {min_trust:.3f}."
        elif agg > 0.0:
            status = RequirementStatus.PARTIALLY_SATISFIED
            msg = (
                f"Weighted average trust {agg:.3f} below threshold {min_trust:.3f}."
            )
        else:
            status = RequirementStatus.UNSATISFIED
            msg = "No evidence present; weighted trust is zero."

        return RequirementCheckResult(
            req_id=req_id,
            status=status,
            gaps=tuple(gaps),
            satisfied_channels=tuple(satisfied),
            missing_channels=tuple(missing),
            aggregate_trust=agg,
            message=msg,
        )

    def check_overrides(
        self,
        requirement: Any,
        context: dict[str, Any],
    ) -> bool:
        """Return True when any override condition in *requirement* is triggered.

        Each override condition is a plain string key.  If the key appears in
        *context* with a truthy value the override fires and the requirement
        may be bypassed.

        Args:
            requirement: The evidence requirement.
            context: Contextual key-value data to evaluate against.

        Returns:
            bool: True if at least one override condition is satisfied.
        """
        conditions: tuple[str, ...]
        if hasattr(requirement, "override_conditions"):
            conditions = requirement.override_conditions
        else:
            return False

        for condition in conditions:
            if context.get(condition):
                return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_gaps(
        self,
        requirement: Any,
        evidence_map: dict[str, float],
    ) -> list[TrustGap]:
        """Compute gap records for every required channel in *requirement*.

        A gap record is produced for each channel in the requirement's required
        list.  Channels that meet the threshold receive a gap of magnitude 0
        and severity NONE.

        Args:
            requirement: The evidence requirement.
            evidence_map: Current evidence scores.

        Returns:
            list[TrustGap]: One gap record per required channel.
        """
        required = list(_get_required_channels(requirement))
        min_trust = _get_min_trust(requirement)
        gaps: list[TrustGap] = []

        for cid in required:
            actual = evidence_map.get(cid, 0.0)
            raw_gap = max(0.0, min_trust - actual)
            severity = GapSeverity.from_gap(raw_gap)
            is_missing = cid not in evidence_map
            is_blocking = is_missing or raw_gap >= 0.3

            if is_missing:
                suggested = (
                    f"Add evidence from channel '{cid}'. "
                    f"This channel is completely absent from the evidence map."
                )
            elif raw_gap > 0:
                suggested = (
                    f"Improve trust score for '{cid}' from {actual:.2f} "
                    f"to at least {min_trust:.2f} (+{raw_gap:.2f} needed)."
                )
            else:
                suggested = f"Channel '{cid}' satisfies the requirement."

            gaps.append(
                TrustGap(
                    channel_id=cid,
                    required_trust=min_trust,
                    actual_trust=actual,
                    gap_magnitude=raw_gap,
                    severity=severity,
                    is_blocking=is_blocking,
                    suggested_action=suggested,
                )
            )

        return gaps


# ---------------------------------------------------------------------------
# GapAnalyzer
# ---------------------------------------------------------------------------


class GapAnalyzer:
    """Analyses :class:`RequirementCheckResult` objects to quantify and explain gaps.

    Provides methods for ranking gaps, estimating remediation effort, and
    identifying the critical path — the ordered sequence of gap closures
    that would most efficiently bring the requirement to satisfaction.
    """

    def __init__(self) -> None:
        """Initialise the gap analyser (no configuration required)."""
        pass

    def analyze(self, check_result: RequirementCheckResult) -> dict[str, Any]:
        """Produce a comprehensive gap analysis for *check_result*.

        Args:
            check_result: The check result to analyse.

        Returns:
            dict[str, Any]: Analysis dictionary with keys:
                ``status``, ``total_gap``, ``gap_count``, ``blocking_gaps``,
                ``ranked_gaps``, ``remediation_effort``, ``worst_gap``,
                ``satisfied_fraction``.
        """
        gaps = check_result.gaps
        total = self.compute_total_gap(gaps)
        ranked = self.rank_gaps_by_severity(gaps)
        effort = self.estimate_remediation_effort(gaps)
        worst = check_result.worst_gap()
        n_channels = len(check_result.satisfied_channels) + len(check_result.missing_channels) + len(check_result.gaps)
        satisfied_fraction = (
            len(check_result.satisfied_channels) / n_channels
            if n_channels > 0
            else 1.0
        )

        return {
            "status": check_result.status.value,
            "total_gap": total,
            "gap_count": check_result.gap_count(),
            "blocking_gaps": sum(1 for g in gaps if g.is_blocking),
            "ranked_gaps": [g.to_dict() for g in ranked],
            "remediation_effort": effort,
            "worst_gap": worst.to_dict() if worst else None,
            "satisfied_fraction": satisfied_fraction,
            "aggregate_trust": check_result.aggregate_trust,
            "message": check_result.message,
        }

    def compute_total_gap(self, gaps: tuple[TrustGap, ...]) -> float:
        """Return the sum of all gap magnitudes.

        Args:
            gaps: Tuple of :class:`TrustGap` records.

        Returns:
            float: Sum of ``gap_magnitude`` values; 0.0 if empty.
        """
        return sum(g.gap_magnitude for g in gaps)

    def rank_gaps_by_severity(
        self, gaps: tuple[TrustGap, ...]
    ) -> list[TrustGap]:
        """Return gaps sorted by severity descending, then by gap_magnitude descending.

        Args:
            gaps: Gaps to rank.

        Returns:
            list[TrustGap]: Sorted gaps, most severe first.
        """
        return sorted(
            gaps,
            key=lambda g: (g.severity.score(), g.gap_magnitude),
            reverse=True,
        )

    def suggest_remediation(self, gap: TrustGap) -> str:
        """Return a human-readable remediation suggestion for *gap*.

        The suggestion is tailored to the gap's severity and whether the
        channel is missing entirely or just below threshold.

        Args:
            gap: The :class:`TrustGap` to remediate.

        Returns:
            str: Actionable remediation text.
        """
        if gap.is_closed():
            return f"Channel '{gap.channel_id}' is fully satisfied. No action needed."

        severity_advice: dict[GapSeverity, str] = {
            GapSeverity.MINOR: (
                f"Minor adjustment needed for '{gap.channel_id}': "
                f"increase trust by {gap.gap_magnitude:.2f}. "
                "Consider adding a few more targeted tests or tightening static analysis rules."
            ),
            GapSeverity.MODERATE: (
                f"Moderate gap in '{gap.channel_id}' ({gap.gap_magnitude:.2f} shortfall). "
                "Expand test coverage, add integration tests, or tighten linting configuration."
            ),
            GapSeverity.SEVERE: (
                f"Severe gap in '{gap.channel_id}' ({gap.gap_magnitude:.2f} shortfall). "
                "A significant investment in evidence collection is required. "
                "Consider adding a formal proof or comprehensive review cycle."
            ),
            GapSeverity.CRITICAL: (
                f"Critical gap: channel '{gap.channel_id}' has no or near-zero evidence. "
                "This channel must be onboarded before the requirement can be satisfied. "
                "Escalate immediately and plan a full evidence-gathering sprint."
            ),
        }
        return severity_advice.get(
            gap.severity,
            gap.suggested_action,
        )

    def estimate_remediation_effort(
        self, gaps: tuple[TrustGap, ...]
    ) -> float:
        """Estimate normalised remediation effort across all gaps.

        The estimate is based on gap severity scores and gap magnitudes,
        normalised to ``[0.0, 1.0]``.  A result of 1.0 means maximum effort;
        0.0 means no effort required.

        Args:
            gaps: Gap records to estimate.

        Returns:
            float: Normalised effort in ``[0.0, 1.0]``.
        """
        if not gaps:
            return 0.0
        raw = sum(g.severity.score() * (1.0 + g.gap_magnitude) for g in gaps)
        # Normalise: each gap contributes at most 2.0 (severity 1.0 + magnitude 1.0).
        max_possible = len(gaps) * 2.0
        return min(1.0, raw / max_possible) if max_possible > 0 else 0.0

    def identify_critical_path(
        self,
        gaps: tuple[TrustGap, ...],
        requirements: list[Any],
    ) -> list[str]:
        """Identify the channel IDs on the critical remediation path.

        The critical path is the minimal ordered sequence of gap closures
        such that closing them would bring the most requirements to
        SATISFIED status.  Channels are ranked by (blocking, severity,
        frequency across requirements).

        Args:
            gaps: Current gap records.
            requirements: All requirements to consider.

        Returns:
            list[str]: Channel IDs ordered by remediation priority.
        """
        # Count how many requirements each channel appears in.
        req_freq: dict[str, int] = defaultdict(int)
        for req in requirements:
            for cid in _get_required_channels(req):
                req_freq[cid] += 1

        blocking_gaps = [g for g in gaps if g.is_blocking]
        ranked = sorted(
            blocking_gaps,
            key=lambda g: (
                g.severity.score(),
                req_freq.get(g.channel_id, 0),
                g.gap_magnitude,
            ),
            reverse=True,
        )
        # Deduplicate while preserving order.
        seen: set[str] = set()
        path: list[str] = []
        for g in ranked:
            if g.channel_id not in seen:
                seen.add(g.channel_id)
                path.append(g.channel_id)
        return path

    def compute_gap_summary(
        self, check_results: list[RequirementCheckResult]
    ) -> dict[str, Any]:
        """Summarise gaps across a collection of check results.

        Args:
            check_results: Results to summarise.

        Returns:
            dict[str, Any]: Summary with keys ``total_requirements``,
                ``satisfied``, ``unsatisfied``, ``partially_satisfied``,
                ``total_gaps``, ``blocking_gaps``, ``average_trust``.
        """
        satisfied = sum(
            1 for r in check_results if r.status.is_acceptable()
        )
        partially = sum(
            1 for r in check_results
            if r.status == RequirementStatus.PARTIALLY_SATISFIED
        )
        unsatisfied = sum(
            1 for r in check_results
            if r.status == RequirementStatus.UNSATISFIED
        )
        all_gaps = [g for r in check_results for g in r.gaps]
        blocking = sum(1 for g in all_gaps if g.is_blocking)
        avg_trust = (
            sum(r.aggregate_trust for r in check_results) / len(check_results)
            if check_results else 0.0
        )

        return {
            "total_requirements": len(check_results),
            "satisfied": satisfied,
            "partially_satisfied": partially,
            "unsatisfied": unsatisfied,
            "total_gaps": len(all_gaps),
            "blocking_gaps": blocking,
            "average_trust": avg_trust,
        }


# ---------------------------------------------------------------------------
# RequirementComposer
# ---------------------------------------------------------------------------


class RequirementComposer:
    """Composes multiple evidence requirements into a single composite requirement.

    Useful when a problem class is built from sub-problems, each with its own
    evidence requirements.  The composer merges channels and trust levels
    according to the chosen composition strategy.
    """

    def __init__(self) -> None:
        """Initialise the composer."""
        pass

    def compose_conjunctive(
        self, reqs: list[Any]
    ) -> Any:
        """Compose *reqs* so that ALL must be simultaneously satisfied.

        The composite requirement demands the union of all required channels
        and the maximum minimum-trust level across all requirements.

        Args:
            reqs: Requirements to compose.

        Returns:
            Any: A new :class:`_FallbackRequirement` representing the conjunction.
        """
        if not reqs:
            return TrustRequirementBuilder("COMPOSITE_CONJUNCTION").build()

        all_channels = list(self.union_channels(reqs))
        max_trust = max(_get_min_trust(r) for r in reqs)

        return _FallbackRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id="COMPOSITE_CONJUNCTION",
            required_channels=tuple(all_channels),
            minimum_trust=max_trust,
            conjunction_mode="ALL",
            allowed_residuals=(),
            forbidden_residuals=(),
            temporal_constraints={},
            override_conditions=(),
        )

    def compose_disjunctive(
        self, reqs: list[Any]
    ) -> Any:
        """Compose *reqs* so that ANY single requirement suffices.

        The composite requirement demands the intersection of all required
        channels (channels required by every constituent) and the minimum
        trust level across constituents.

        Args:
            reqs: Requirements to compose.

        Returns:
            Any: A new requirement representing the disjunction.
        """
        if not reqs:
            return TrustRequirementBuilder("COMPOSITE_DISJUNCTION").build()

        common_channels = list(self.intersect_channels(reqs))
        min_trust = min(_get_min_trust(r) for r in reqs)

        return _FallbackRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id="COMPOSITE_DISJUNCTION",
            required_channels=tuple(common_channels),
            minimum_trust=min_trust,
            conjunction_mode="ANY",
            allowed_residuals=(),
            forbidden_residuals=(),
            temporal_constraints={},
            override_conditions=(),
        )

    def compose_weighted(
        self, reqs: list[Any], weights: list[float]
    ) -> Any:
        """Compose *reqs* with a weighted average strategy.

        The composite minimum trust is the weighted average of constituent
        minimum trusts.  Channels are the union of all required channels.

        Args:
            reqs: Requirements to compose.
            weights: Non-negative weights parallel to *reqs*.  Need not sum to 1.

        Returns:
            Any: A new requirement using WEIGHTED mode.

        Raises:
            ValueError: If ``len(weights) != len(reqs)`` or all weights are zero.
        """
        if len(weights) != len(reqs):
            raise ValueError(
                f"weights length {len(weights)} != reqs length {len(reqs)}."
            )
        total = sum(weights)
        if total == 0.0:
            raise ValueError("All weights are zero; cannot compute weighted composition.")

        all_channels = list(self.union_channels(reqs))
        weighted_trust = sum(
            _get_min_trust(r) * w for r, w in zip(reqs, weights)
        ) / total

        return _FallbackRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id="COMPOSITE_WEIGHTED",
            required_channels=tuple(all_channels),
            minimum_trust=max(0.0, min(1.0, weighted_trust)),
            conjunction_mode="WEIGHTED",
            allowed_residuals=(),
            forbidden_residuals=(),
            temporal_constraints={},
            override_conditions=(),
        )

    def intersect_channels(self, reqs: list[Any]) -> set[str]:
        """Return channels required by ALL requirements in *reqs*.

        Args:
            reqs: Requirements whose channel sets are intersected.

        Returns:
            set[str]: Channels present in every requirement's required list.
                Empty set if *reqs* is empty.
        """
        if not reqs:
            return set()
        sets = [set(_get_required_channels(r)) for r in reqs]
        result = sets[0]
        for s in sets[1:]:
            result = result & s
        return result

    def union_channels(self, reqs: list[Any]) -> set[str]:
        """Return channels required by ANY requirement in *reqs*.

        Args:
            reqs: Requirements whose channel sets are unioned.

        Returns:
            set[str]: Union of all required channel sets.
        """
        result: set[str] = set()
        for r in reqs:
            result.update(_get_required_channels(r))
        return result

    def strengthen_requirement(
        self, req: Any, extra_trust: float
    ) -> Any:
        """Return a new requirement with a raised minimum trust threshold.

        Args:
            req: The requirement to strengthen.
            extra_trust: Amount to add to the current ``minimum_trust``.
                Clamped so the result stays within ``[0.0, 1.0]``.

        Returns:
            Any: A new requirement with the higher threshold.
        """
        new_trust = min(1.0, _get_min_trust(req) + max(0.0, extra_trust))
        return _FallbackRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=_get_req_attr(req, "problem_class_id", "STRENGTHENED"),
            required_channels=tuple(_get_required_channels(req)),
            minimum_trust=new_trust,
            conjunction_mode=_get_mode_str(req),
            allowed_residuals=tuple(_get_req_attr(req, "allowed_residuals", ())),
            forbidden_residuals=tuple(_get_req_attr(req, "forbidden_residuals", ())),
            temporal_constraints=dict(_get_req_attr(req, "temporal_constraints", {})),
            override_conditions=tuple(_get_req_attr(req, "override_conditions", ())),
        )

    def weaken_requirement(
        self, req: Any, reduced_trust: float
    ) -> Any:
        """Return a new requirement with a lowered minimum trust threshold.

        Args:
            req: The requirement to weaken.
            reduced_trust: Amount to subtract from the current ``minimum_trust``.
                Clamped so the result stays within ``[0.0, 1.0]``.

        Returns:
            Any: A new requirement with the lower threshold.
        """
        new_trust = max(0.0, _get_min_trust(req) - max(0.0, reduced_trust))
        return _FallbackRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=_get_req_attr(req, "problem_class_id", "WEAKENED"),
            required_channels=tuple(_get_required_channels(req)),
            minimum_trust=new_trust,
            conjunction_mode=_get_mode_str(req),
            allowed_residuals=tuple(_get_req_attr(req, "allowed_residuals", ())),
            forbidden_residuals=tuple(_get_req_attr(req, "forbidden_residuals", ())),
            temporal_constraints=dict(_get_req_attr(req, "temporal_constraints", {})),
            override_conditions=tuple(_get_req_attr(req, "override_conditions", ())),
        )


# ---------------------------------------------------------------------------
# TrustBudgetManager
# ---------------------------------------------------------------------------


class TrustBudgetManager:
    """Manages the allocation of a trust budget across evidence channels.

    The trust budget is a scalar resource (defaulting to 1.0) that represents
    the total "investment" available for evidence collection.  Portions of the
    budget are allocated to specific channels; the manager ensures that
    allocations do not exceed the total.

    Args:
        total_budget: Total trust budget available.  Defaults to 1.0.
    """

    def __init__(self, total_budget: float = 1.0) -> None:
        """Initialise the manager with the given total budget.

        Args:
            total_budget: Total budget available.  Must be > 0.

        Raises:
            ValueError: If *total_budget* ≤ 0.
        """
        if total_budget <= 0.0:
            raise ValueError(f"total_budget must be positive; got {total_budget!r}.")
        self._total_budget = total_budget
        self._allocations: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Budget operations
    # ------------------------------------------------------------------

    def allocate(self, channel: str, amount: float) -> bool:
        """Allocate *amount* of budget to *channel*.

        If a prior allocation exists for the channel it is replaced, provided
        the new amount does not cause the total to exceed the budget.

        Args:
            channel: Channel identifier receiving the allocation.
            amount: Amount to allocate.  Must be ≥ 0.

        Returns:
            bool: True if the allocation was successful; False if it would
                exceed the remaining budget (in which case no change is made).
        """
        if amount < 0.0:
            amount = 0.0

        current_for_channel = self._allocations.get(channel, 0.0)
        other_allocations = sum(
            v for k, v in self._allocations.items() if k != channel
        )
        if other_allocations + amount > self._total_budget + 1e-9:
            return False
        self._allocations[channel] = amount
        return True

    def deallocate(self, channel: str) -> float:
        """Remove the allocation for *channel* and return the released amount.

        Args:
            channel: Channel identifier whose allocation is to be removed.

        Returns:
            float: The amount released (0.0 if the channel had no allocation).
        """
        return self._allocations.pop(channel, 0.0)

    def remaining_budget(self) -> float:
        """Return the unallocated portion of the budget.

        Returns:
            float: ``total_budget - sum(allocations)``.
        """
        return self._total_budget - sum(self._allocations.values())

    def get_allocation(self, channel: str) -> float:
        """Return the current allocation for *channel*.

        Args:
            channel: Channel identifier.

        Returns:
            float: Allocated amount; 0.0 if none.
        """
        return self._allocations.get(channel, 0.0)

    def list_allocations(self) -> dict[str, float]:
        """Return a copy of the current allocation mapping.

        Returns:
            dict[str, float]: Mapping from channel_id to allocated amount.
        """
        return dict(self._allocations)

    def compute_optimal_allocation(
        self,
        channels: list[str],
        requirement: Any,
    ) -> dict[str, float]:
        """Distribute the budget proportionally to the minimum-trust requirement.

        Each channel receives a share proportional to the required minimum
        trust.  If all channels have the same weight, the budget is evenly
        split.

        Args:
            channels: Channel identifiers to allocate to.
            requirement: The evidence requirement providing context.

        Returns:
            dict[str, float]: Proposed allocation mapping (not applied).
        """
        if not channels:
            return {}

        min_trust = _get_min_trust(requirement)
        # Weight each channel equally (all need to reach the same min_trust).
        equal_share = self._total_budget / len(channels)
        return {cid: min(equal_share, min_trust) for cid in channels}

    def validate_allocations(self, requirement: Any) -> list[str]:
        """Return a list of violation messages for the current allocations.

        Checks that every required channel has a non-zero allocation and that
        the total does not exceed the budget.

        Args:
            requirement: Requirement used to determine which channels are expected.

        Returns:
            list[str]: Violation messages; empty if all is well.
        """
        violations: list[str] = []
        required = list(_get_required_channels(requirement))

        for cid in required:
            if self._allocations.get(cid, 0.0) == 0.0:
                violations.append(
                    f"Channel '{cid}' is required but has no budget allocation."
                )

        total_allocated = sum(self._allocations.values())
        if total_allocated > self._total_budget + 1e-9:
            violations.append(
                f"Total allocation {total_allocated:.4f} exceeds budget "
                f"{self._total_budget:.4f} by {total_allocated - self._total_budget:.4f}."
            )

        return violations

    def reset(self) -> None:
        """Clear all current allocations.

        After calling this method, the entire budget is available again.
        """
        self._allocations.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manager state to a plain dictionary.

        Returns:
            dict[str, Any]: JSON-serialisable state representation.
        """
        return {
            "total_budget": self._total_budget,
            "allocations": dict(self._allocations),
            "remaining": self.remaining_budget(),
            "utilisation": (
                1.0 - self.remaining_budget() / self._total_budget
                if self._total_budget > 0.0
                else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _get_required_channels(req: Any) -> tuple[str, ...]:
    """Extract ``required_channels`` from a requirement, returning empty tuple on failure."""
    if hasattr(req, "required_channels"):
        return tuple(req.required_channels)
    return ()


def _get_min_trust(req: Any) -> float:
    """Extract ``minimum_trust`` from a requirement, defaulting to 0.5."""
    if hasattr(req, "minimum_trust"):
        return float(req.minimum_trust)
    return 0.5


def _get_req_id(req: Any) -> str:
    """Extract ``req_id`` from a requirement, generating one on failure."""
    if hasattr(req, "req_id"):
        return str(req.req_id)
    return str(uuid.uuid4())


def _get_mode_str(req: Any) -> str:
    """Extract and stringify the conjunction mode, defaulting to ``"ALL"``."""
    if hasattr(req, "conjunction_mode"):
        return str(req.conjunction_mode).upper()
    return "ALL"


def _get_req_attr(req: Any, attr: str, default: Any) -> Any:
    """Return *attr* from *req* or *default* if absent."""
    return getattr(req, attr, default)


# ---------------------------------------------------------------------------
# Standard requirements
# ---------------------------------------------------------------------------

def _build_standard_requirements() -> dict[str, Any]:
    """Construct the standard evidence requirements for all canonical problem classes.

    Returns:
        dict[str, Any]: Mapping from problem class name to requirement object.
    """
    specs: list[tuple[str, list[str], float, str]] = [
        ("SEARCH",         ["TESTING", "STATIC_ANALYSIS"],                          0.60, "ALL"),
        ("OPTIMIZATION",   ["TESTING", "FORMAL_PROOF"],                             0.75, "ALL"),
        ("DECISION",       ["TESTING", "TYPE_CHECKING"],                            0.65, "ALL"),
        ("VERIFICATION",   ["FORMAL_PROOF", "TYPE_CHECKING", "TESTING"],            0.85, "ALL"),
        ("INFERENCE",      ["TESTING", "STATIC_ANALYSIS"],                          0.70, "ALL"),
        ("SYNTHESIS",      ["TESTING", "TYPE_CHECKING", "COPILOT_SYNTHESIS"],       0.75, "WEIGHTED"),
        ("REPAIR",         ["TESTING", "STATIC_ANALYSIS", "RUNTIME_MONITORING"],    0.70, "ALL"),
        ("CLASSIFICATION", ["TESTING"],                                             0.60, "ALL"),
    ]

    reqs: dict[str, Any] = {}
    for name, channels, trust, mode in specs:
        reqs[name] = _FallbackRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=name,
            required_channels=tuple(channels),
            minimum_trust=trust,
            conjunction_mode=mode,
            allowed_residuals=(),
            forbidden_residuals=(),
            temporal_constraints={},
            override_conditions=(),
        )
    return reqs


STANDARD_REQUIREMENTS: dict[str, Any] = _build_standard_requirements()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def check_all_requirements(
    requirements: list[Any],
    evidence_map: dict[str, float],
) -> dict[str, RequirementCheckResult]:
    """Check all *requirements* against *evidence_map* and return results by req_id.

    Args:
        requirements: Evidence requirements to check.
        evidence_map: Current channel trust scores.

    Returns:
        dict[str, RequirementCheckResult]: Mapping from req_id to check result.
    """
    checker = RequirementChecker()
    return {
        _get_req_id(req): checker.check(req, evidence_map)
        for req in requirements
    }


def compute_trust_gaps(
    requirement: Any,
    evidence_map: dict[str, float],
) -> list[TrustGap]:
    """Return gap records for every required channel in *requirement*.

    Args:
        requirement: The evidence requirement.
        evidence_map: Current channel trust scores.

    Returns:
        list[TrustGap]: Gap records, one per required channel.
    """
    checker = RequirementChecker()
    # Reuse internal gap computation by running a full check and extracting gaps.
    result = checker.check(requirement, evidence_map)
    return list(result.gaps)


def find_critical_requirements(
    requirements: list[Any],
    evidence_map: dict[str, float],
) -> list[Any]:
    """Return the subset of *requirements* that are currently unsatisfied.

    A requirement is "critical" when its :class:`RequirementCheckResult` has
    status UNSATISFIED or has blocking gaps.

    Args:
        requirements: All requirements to screen.
        evidence_map: Current channel trust scores.

    Returns:
        list[Any]: Unsatisfied or gap-blocked requirements.
    """
    checker = RequirementChecker()
    critical: list[Any] = []
    for req in requirements:
        result = checker.check(req, evidence_map)
        if not result.status.is_acceptable() or result.has_blocking_gaps():
            critical.append(req)
    return critical


def build_requirement_for_class(
    problem_class_name: str,
) -> Any | None:
    """Return the standard :class:`EvidenceRequirement` for *problem_class_name*.

    Args:
        problem_class_name: Name of the problem class (e.g. ``"SEARCH"``).
            Case-insensitive.

    Returns:
        Any | None: The corresponding requirement, or ``None`` if the name is
            not found in :data:`STANDARD_REQUIREMENTS`.
    """
    return STANDARD_REQUIREMENTS.get(problem_class_name.strip().upper())




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "RequirementStatus",
    "GapSeverity",
    # Dataclasses
    "TrustGap",
    "RequirementCheckResult",
    # Core classes
    "TrustRequirementBuilder",
    "RequirementChecker",
    "GapAnalyzer",
    "RequirementComposer",
    "TrustBudgetManager",
    # Module-level data
    "STANDARD_REQUIREMENTS",
    # Module-level functions
    "check_all_requirements",
    "compute_trust_gaps",
    "find_critical_requirements",
    "build_requirement_for_class",
    # Type aliases
    "ChannelId",
    "TrustLevel",
    "EvidenceMap",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
