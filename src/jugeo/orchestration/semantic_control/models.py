"""Semantic control models for JuGeo orchestration (theory2.tex Ch44).

This module defines the core data model layer for the semantic control subsystem.
It follows Ch44 of theory2.tex, which develops a formal framework for *admissible
semantic trajectories*: sequences of orchestrator states linked by admissible moves
whose postconditions constitute a monotone covering chain converging to a
ConvergenceCertificate.

Key concepts
------------
SemanticControlState
    A snapshot of the orchestrator's knowledge graph at a single point in time.
    Captures cover ids, context ids, section ids, treaty ids, obligation ids,
    channel ids, and a resource budget.

AdmissibleMove
    A typed transition from one SemanticControlState to another.  Each move
    carries explicit preconditions, postconditions, cost, priority, expected gain,
    and a trust requirement.  Admissibility is checked against the current state
    before application.

ControlLaw
    A policy that selects the next AdmissibleMove given a state and a candidate
    set.  Concrete instantiations (GREEDY, LOOKAHEAD, BALANCED, ADAPTIVE) are
    parameterised dictionaries; the ``select_move`` method dispatches to the
    appropriate strategy.

SemanticTrajectory
    An ordered log of (state, move) pairs that records the full history of a
    control episode.  Convergence analysis is performed over the score_history
    produced by the trajectory.

ConvergenceCertificate
    An immutable witness that the trajectory has reached a fixpoint satisfying
    the chapter's convergence criterion: coverage_ratio >= CONVERGENCE_THRESHOLD
    and obligation_count == 0.

References
----------
- theory2.tex Ch44 §44.1 – §44.9 (Semantic Trajectory Calculus)
- jugeo.orchestration.controller – upstream orchestrator primitives
- jugeo.evidence.trust – trust tier and profile machinery
"""

from __future__ import annotations

import copy
import enum
import logging
import math
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import (
        MoveKind,
        OrchestratorState,
        SemanticMove,
        ResourceBudget,
        ConvergenceMonitor as _BaseConvergenceMonitor,
        MoveHistory,
    )
    _CONTROLLER_AVAILABLE = True
except Exception:  # pragma: no cover – fallback stubs when controller absent
    _CONTROLLER_AVAILABLE = False

    class MoveKind(enum.Enum):  # type: ignore[no-redef]
        """Fallback MoveKind stub used when controller is not importable."""

        VERIFY = "verify"
        CONSTRUCT = "construct"
        REPAIR = "repair"
        NEGOTIATE_TREATY = "negotiate_treaty"
        REFINE_COVER = "refine_cover"
        DISCHARGE_OBLIGATION = "discharge_obligation"
        CONSULT_ORACLE = "consult_oracle"
        # Extended variants used within this module
        EXTEND_COVER = "extend_cover"
        LIFT_SECTION = "lift_section"
        BIND_TREATY = "bind_treaty"
        OPEN_CHANNEL = "open_channel"
        CLOSE_CHANNEL = "close_channel"
        PROMOTE_CONTEXT = "promote_context"
        DEMOTE_CONTEXT = "demote_context"
        ASSERT_INVARIANT = "assert_invariant"
        RETRACT_INVARIANT = "retract_invariant"
        CHECKPOINT = "checkpoint"
        ROLLBACK = "rollback"

    class OrchestratorState:  # type: ignore[no-redef]
        pass

    class SemanticMove:  # type: ignore[no-redef]
        pass

    class ResourceBudget:  # type: ignore[no-redef]
        pass

    class _BaseConvergenceMonitor:  # type: ignore[no-redef]
        pass

    class MoveHistory:  # type: ignore[no-redef]
        pass


_EXTENDED_MOVE_KIND_VALUES: dict[str, str] = {
    "EXTEND_COVER": "extend_cover",
    "LIFT_SECTION": "lift_section",
    "BIND_TREATY": "bind_treaty",
    "OPEN_CHANNEL": "open_channel",
    "CLOSE_CHANNEL": "close_channel",
    "PROMOTE_CONTEXT": "promote_context",
    "DEMOTE_CONTEXT": "demote_context",
    "ASSERT_INVARIANT": "assert_invariant",
    "RETRACT_INVARIANT": "retract_invariant",
    "CHECKPOINT": "checkpoint",
    "ROLLBACK": "rollback",
}

if any(not hasattr(MoveKind, name) for name in _EXTENDED_MOVE_KIND_VALUES):
    _move_kind_members = {member.name: member.value for member in MoveKind}
    _move_kind_members.update(
        {
            name: value
            for name, value in _EXTENDED_MOVE_KIND_VALUES.items()
            if name not in _move_kind_members
        }
    )
    MoveKind = enum.Enum("MoveKind", _move_kind_members)  # type: ignore[assignment,misc]

if hasattr(MoveKind, "VERIFY") and not hasattr(MoveKind, "TYPE_CHECK"):
    MoveKind.TYPE_CHECK = MoveKind.VERIFY


try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    _TRUST_AVAILABLE = True
except Exception:  # pragma: no cover
    _TRUST_AVAILABLE = False

    class TrustLevel(enum.Enum):  # type: ignore[no-redef]
        UNTRUSTED = "untrusted"
        PROVISIONAL = "provisional"
        TRUSTED = "trusted"
        AUTHORITATIVE = "authoritative"

    class TrustTier(enum.Enum):  # type: ignore[no-redef]
        T0 = "T0"
        T1 = "T1"
        T2 = "T2"
        T3 = "T3"

    class TrustProfile:  # type: ignore[no-redef]
        pass

if hasattr(TrustTier, "PROPOSAL") and not hasattr(TrustTier, "PROVISIONAL"):
    TrustTier.PROVISIONAL = TrustTier.PROPOSAL
if hasattr(TrustTier, "REVIEWED") and not hasattr(TrustTier, "TRUSTED"):
    TrustTier.TRUSTED = TrustTier.REVIEWED


# ---------------------------------------------------------------------------
# Module-level constants  (theory2.tex Ch44 §44.2)
# ---------------------------------------------------------------------------

#: Minimum coverage ratio required to issue a ConvergenceCertificate.
CONVERGENCE_THRESHOLD: float = 0.90

#: Default validity window (seconds) for a ConvergenceCertificate.
CERTIFICATE_TTL: float = 3600.0

#: Weight applied to coverage_ratio in attainability_score computation.
ATTAINABILITY_COVERAGE_WEIGHT: float = 0.50

#: Weight applied to treaty health in attainability_score computation.
ATTAINABILITY_TREATY_WEIGHT: float = 0.30

#: Weight applied to obligation deficit in attainability_score computation.
ATTAINABILITY_OBLIGATION_WEIGHT: float = 0.20

#: Maximum obligation count before the state is declared STALLED.
OBLIGATION_STALL_THRESHOLD: int = 50

#: Minimum consecutive improving steps to declare STRONG convergence.
STRONG_CONVERGENCE_WINDOW: int = 5

#: Minimum trajectory length to attempt convergence analysis.
MIN_TRAJECTORY_LENGTH: int = 3

#: Version tag for this module's serialisation format.
MODELS_VERSION: str = "1.0.0"

__all__ = [
    # enums
    "ControlLawKind",
    "StateHealthStatus",
    "ConvergenceMode",
    # dataclasses
    "SemanticControlState",
    "StateDelta",
    "AdmissibleMove",
    "ControlLaw",
    "ConvergenceCertificate",
    "SemanticTrajectory",
    # re-exported
    "MoveKind",
    # constants
    "CONVERGENCE_THRESHOLD",
    "CERTIFICATE_TTL",
    "MODELS_VERSION",
]


# ===========================================================================
# Enums  (theory2.tex Ch44 §44.3)
# ===========================================================================


class ControlLawKind(enum.Enum):
    """Identifies the algorithmic family of a ControlLaw.

    Variants correspond to the four canonical policies in Ch44 §44.5 plus an
    escape hatch for user-supplied implementations.

    GREEDY
        Always select the move with the highest net_value().  Offers O(n)
        selection but may get stuck in local optima.
    LOOKAHEAD
        Evaluate k-step rollouts and pick the root move leading to the best
        terminal score.  Parameters: ``depth`` (int), ``beam_width`` (int).
    BALANCED
        Trade off immediate net_value against long-run coverage gain using a
        configurable mixing coefficient ``alpha``.
    ADAPTIVE
        Online policy that updates its mixing coefficient from a running reward
        signal, implementing a lightweight bandit update.
    CUSTOM
        Caller-supplied policy; ``select_move`` delegates to a registered
        callable stored in ``parameters["selector"]``.
    """

    GREEDY = "greedy"
    LOOKAHEAD = "lookahead"
    BALANCED = "balanced"
    ADAPTIVE = "adaptive"
    CUSTOM = "custom"


class StateHealthStatus(enum.Enum):
    """Coarse health signal for a SemanticControlState.

    HEALTHY
        Attainability score is above 0.7 and no stall condition.
    DEGRADED
        Score between 0.4 and 0.7; trajectory may still recover.
    STALLED
        Score below 0.4 or obligation count exceeds OBLIGATION_STALL_THRESHOLD.
    DIVERGED
        Score is decreasing monotonically over the recent window.
    CONVERGED
        Coverage ratio >= CONVERGENCE_THRESHOLD and obligations == 0.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALLED = "stalled"
    DIVERGED = "diverged"
    CONVERGED = "converged"


class ConvergenceMode(enum.Enum):
    """Distinguishes the quality of a convergence certificate.

    STRONG
        The trajectory shows STRONG_CONVERGENCE_WINDOW consecutive improving
        steps ending with coverage_ratio >= CONVERGENCE_THRESHOLD and zero
        obligations.
    WEAK
        The terminal state satisfies the threshold but the improvement window
        is insufficient for STRONG certification.
    APPROXIMATE
        Coverage ratio is within 5% of the threshold (i.e., >= 0.85) and
        obligation count is small (< 3); used for incremental checkpoints.
    """

    STRONG = "strong"
    WEAK = "weak"
    APPROXIMATE = "approximate"


# ===========================================================================
# SemanticControlState  (theory2.tex Ch44 §44.4)
# ===========================================================================


@dataclass(slots=True)
class SemanticControlState:
    """Snapshot of the orchestrator's semantic world-state at a single instant.

    This is the central carrier type in the semantic control subsystem.  Each
    field corresponds to a named category of semantic objects managed by the
    orchestrator.

    Parameters
    ----------
    state_id:
        Globally-unique identifier for this snapshot (UUID4 string).
    cover_ids:
        Identifiers of active cover elements (theory2.tex §44.4 Def 44.3).
    context_ids:
        Active context frame identifiers.
    section_ids:
        Identifiers of proof/document sections currently in scope.
    treaty_ids:
        Active treaty identifiers binding external commitments.
    obligation_ids:
        Unfulfilled obligation identifiers.  A converged state has an empty
        list.
    channel_ids:
        Open inter-agent communication channel identifiers.
    budget:
        Mutable resource budget (tokens, time, API calls, etc.).
    timestamp:
        POSIX timestamp at snapshot creation (float seconds since epoch).
    metadata:
        Arbitrary key-value annotations for debugging/provenance.
    """

    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cover_ids: list[str] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)
    treaty_ids: list[str] = field(default_factory=list)
    obligation_ids: list[str] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Admissibility
    # ------------------------------------------------------------------

    def is_admissible(self, move: AdmissibleMove) -> bool:
        """Return True if *move* is admissible from this state.

        A move is admissible when every precondition identifier names a semantic
        object that is present in one of the state's identity collections.

        Parameters
        ----------
        move:
            The candidate AdmissibleMove to test.

        Returns
        -------
        bool
            ``True`` iff all preconditions are satisfied.
        """
        all_ids: set[str] = (
            set(self.cover_ids)
            | set(self.context_ids)
            | set(self.section_ids)
            | set(self.treaty_ids)
            | set(self.obligation_ids)
            | set(self.channel_ids)
        )
        for precond in move.preconditions:
            if precond not in all_ids:
                logger.debug(
                    "Precondition %r not satisfied in state %s", precond, self.state_id
                )
                return False
        return True

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def coverage_ratio(self) -> float:
        """Compute the coverage ratio as defined in theory2.tex §44.4.

        The ratio is:
            len(section_ids) / max(1, len(cover_ids) + len(obligation_ids))

        A value of 1.0 means all potential obligations are covered by sections
        and no cover gaps remain.

        Returns
        -------
        float
            Value in [0.0, ∞); clipped to 1.0 in practice.
        """
        denominator = max(1, len(self.cover_ids) + len(self.obligation_ids))
        ratio = len(self.section_ids) / denominator
        return min(ratio, 1.0)

    def attainability_score(self) -> float:
        """Compute a composite attainability score in [0.0, 1.0].

        The score is a weighted combination of three sub-signals:

        1. ``coverage_ratio`` — fraction of sections covered (weight 0.50).
        2. treaty health — fraction of treaties with no pending obligations
           associated (weight 0.30).
        3. obligation deficit — inverted normalised obligation count (weight 0.20).

        Returns
        -------
        float
            Composite score in [0.0, 1.0]; higher is better.
        """
        cov = self.coverage_ratio()

        # Treaty health: treaties are healthy when the ratio of treaties to
        # obligations is favourable.
        if self.treaty_ids:
            obs_per_treaty = len(self.obligation_ids) / max(1, len(self.treaty_ids))
            treaty_health = math.exp(-obs_per_treaty)
        else:
            treaty_health = 1.0 if not self.obligation_ids else 0.5

        # Obligation deficit: sigmoid inverse of obligation count.
        obs_count = len(self.obligation_ids)
        ob_deficit = math.exp(-obs_count / max(1, OBLIGATION_STALL_THRESHOLD / 5))

        score = (
            ATTAINABILITY_COVERAGE_WEIGHT * cov
            + ATTAINABILITY_TREATY_WEIGHT * treaty_health
            + ATTAINABILITY_OBLIGATION_WEIGHT * ob_deficit
        )
        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # Comparison / delta
    # ------------------------------------------------------------------

    def delta_from(self, other: SemanticControlState) -> StateDelta:
        """Compute the structural difference from *other* to ``self``.

        Parameters
        ----------
        other:
            The reference (earlier) state.

        Returns
        -------
        StateDelta
            Immutable record of all additions and removals between the two
            states.
        """
        self_covers = set(self.cover_ids)
        other_covers = set(other.cover_ids)
        self_sections = set(self.section_ids)
        other_sections = set(other.section_ids)
        self_obs = set(self.obligation_ids)
        other_obs = set(other.obligation_ids)

        # Budget delta – numeric fields only.
        budget_delta: dict[str, Any] = {}
        all_budget_keys = set(self.budget) | set(other.budget)
        for k in all_budget_keys:
            sv = self.budget.get(k, 0)
            ov = other.budget.get(k, 0)
            if isinstance(sv, (int, float)) and isinstance(ov, (int, float)):
                diff = sv - ov
                if diff != 0:
                    budget_delta[k] = diff
            elif sv != ov:
                budget_delta[k] = sv

        return StateDelta(
            added_covers=tuple(self_covers - other_covers),
            removed_covers=tuple(other_covers - self_covers),
            added_sections=tuple(self_sections - other_sections),
            removed_sections=tuple(other_sections - self_sections),
            added_obligations=tuple(self_obs - other_obs),
            resolved_obligations=tuple(other_obs - self_obs),
            budget_delta=budget_delta,
            score_delta=self.attainability_score() - other.attainability_score(),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding.

        Returns
        -------
        dict
            Nested dict representation.
        """
        return {
            "state_id": self.state_id,
            "cover_ids": list(self.cover_ids),
            "context_ids": list(self.context_ids),
            "section_ids": list(self.section_ids),
            "treaty_ids": list(self.treaty_ids),
            "obligation_ids": list(self.obligation_ids),
            "channel_ids": list(self.channel_ids),
            "budget": dict(self.budget),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
            "_version": MODELS_VERSION,
        }

    def snapshot(self) -> SemanticControlState:
        """Return an independent deep copy of this state.

        Returns
        -------
        SemanticControlState
            A new state preserving identity while copying mutable fields.
        """
        new_state = SemanticControlState(
            state_id=self.state_id,
            cover_ids=list(self.cover_ids),
            context_ids=list(self.context_ids),
            section_ids=list(self.section_ids),
            treaty_ids=list(self.treaty_ids),
            obligation_ids=list(self.obligation_ids),
            channel_ids=list(self.channel_ids),
            budget=copy.deepcopy(self.budget),
            timestamp=self.timestamp,
            metadata=copy.deepcopy(self.metadata),
        )
        return new_state

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_status(self) -> StateHealthStatus:
        """Derive a coarse StateHealthStatus from the current attainability.

        Returns
        -------
        StateHealthStatus
            One of CONVERGED, HEALTHY, DEGRADED, STALLED, DIVERGED.
        """
        if self.coverage_ratio() >= CONVERGENCE_THRESHOLD and not self.obligation_ids:
            return StateHealthStatus.CONVERGED
        score = self.attainability_score()
        if len(self.obligation_ids) > OBLIGATION_STALL_THRESHOLD:
            return StateHealthStatus.STALLED
        if score >= 0.7:
            return StateHealthStatus.HEALTHY
        if score >= 0.4:
            return StateHealthStatus.DEGRADED
        return StateHealthStatus.STALLED

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"SemanticControlState("
            f"id={self.state_id[:8]}…, "
            f"covers={len(self.cover_ids)}, "
            f"sections={len(self.section_ids)}, "
            f"obligations={len(self.obligation_ids)}, "
            f"score={self.attainability_score():.3f})"
        )


# ===========================================================================
# StateDelta  (theory2.tex Ch44 §44.4.1)
# ===========================================================================


@dataclass(frozen=True, slots=True)
class StateDelta:
    """Immutable record of the structural difference between two states.

    Parameters
    ----------
    added_covers:
        Cover IDs present in *new* but absent in *old*.
    removed_covers:
        Cover IDs present in *old* but absent in *new*.
    added_sections:
        Section IDs added in the transition.
    removed_sections:
        Section IDs removed in the transition.
    added_obligations:
        Obligations introduced by the transition.
    resolved_obligations:
        Obligations discharged by the transition.
    budget_delta:
        Numeric budget changes (positive = increase).
    score_delta:
        Change in attainability_score (positive = improvement).
    """

    added_covers: tuple[str, ...]
    removed_covers: tuple[str, ...]
    added_sections: tuple[str, ...]
    removed_sections: tuple[str, ...]
    added_obligations: tuple[str, ...]
    resolved_obligations: tuple[str, ...]
    budget_delta: dict[str, Any]
    score_delta: float

    def is_improving(self) -> bool:
        """Return True when the delta represents a net improvement.

        A delta is improving when *score_delta* is positive, i.e., the
        attainability score increased.

        Returns
        -------
        bool
        """
        return self.score_delta > 0.0

    def magnitude(self) -> float:
        """Return an unsigned magnitude scalar for the delta.

        Computed as the sum of cardinalities of all change sets plus the
        absolute value of the score_delta, normalised to [0, 1] by a soft
        sigmoid.

        Returns
        -------
        float
            Unsigned magnitude in (0.0, 1.0].
        """
        raw = (
            len(self.added_covers)
            + len(self.removed_covers)
            + len(self.added_sections)
            + len(self.removed_sections)
            + len(self.added_obligations)
            + len(self.resolved_obligations)
            + abs(self.score_delta) * 10
        )
        return 1.0 - math.exp(-raw / 5.0)

    def summary(self) -> str:
        """Return a one-line human-readable summary of the delta.

        Returns
        -------
        str
        """
        direction = "↑" if self.is_improving() else "↓"
        return (
            f"StateDelta({direction} score={self.score_delta:+.3f}, "
            f"+covers={len(self.added_covers)}, "
            f"-covers={len(self.removed_covers)}, "
            f"+sections={len(self.added_sections)}, "
            f"+obs={len(self.added_obligations)}, "
            f"-obs={len(self.resolved_obligations)})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "added_covers": list(self.added_covers),
            "removed_covers": list(self.removed_covers),
            "added_sections": list(self.added_sections),
            "removed_sections": list(self.removed_sections),
            "added_obligations": list(self.added_obligations),
            "resolved_obligations": list(self.resolved_obligations),
            "budget_delta": dict(self.budget_delta),
            "score_delta": self.score_delta,
            "is_improving": self.is_improving(),
            "magnitude": self.magnitude(),
        }


# ===========================================================================
# AdmissibleMove  (theory2.tex Ch44 §44.5)
# ===========================================================================


@dataclass(slots=True)
class AdmissibleMove:
    """A typed, parameterised transition between SemanticControlStates.

    An AdmissibleMove is admissible from a state ``s`` if every identifier in
    ``preconditions`` is present in ``s``.  When applied it returns a new state
    whose id collections are updated according to ``postconditions`` and the
    move's kind.

    Parameters
    ----------
    move_id:
        Globally-unique identifier for this move instance.
    kind:
        The MoveKind enum variant classifying this transition.
    preconditions:
        List of object IDs that must exist in the state for the move to fire.
    postconditions:
        List of object IDs that will be present in the state after the move.
    cost:
        Estimated resource cost (in abstract units) of executing this move.
    priority:
        Scheduling priority; higher values cause the move to be preferred by
        greedy and balanced control laws.
    expected_gain:
        Expected attainability improvement on application.
    trust_requirement:
        Minimum trust level name required to authorise this move.
    metadata:
        Arbitrary annotations.
    """

    move_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: MoveKind = field(default_factory=lambda: next(iter(MoveKind)))
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    cost: float = 1.0
    priority: float = 0.5
    expected_gain: float = 0.0
    trust_requirement: str = "PROVISIONAL"
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Applicability & application
    # ------------------------------------------------------------------

    def is_applicable(self, state: SemanticControlState) -> bool:
        """Check if this move is applicable from *state*.

        Parameters
        ----------
        state:
            The candidate state from which the move might fire.

        Returns
        -------
        bool
            ``True`` iff all preconditions are satisfied by ``state``.
        """
        return state.is_admissible(self)

    def apply(self, state: SemanticControlState) -> SemanticControlState:
        """Apply this move to *state* and return the successor state.

        The successor state is a snapshot of *state* with its id collections
        updated according to the move's postconditions and kind.

        Parameters
        ----------
        state:
            Source state.  Must satisfy ``is_applicable(state)``.

        Returns
        -------
        SemanticControlState
            New state; *state* is not mutated.

        Raises
        ------
        ValueError
            If the move is not applicable from *state*.
        """
        if not self.is_applicable(state):
            raise ValueError(
                f"Move {self.move_id} is not applicable from state {state.state_id}: "
                f"unsatisfied preconditions"
            )

        successor = state.snapshot()

        # Dispatch on kind value (string) so both real and stub MoveKind work.
        kind = self.kind
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        post = self.postconditions

        if kind_val in ("extend_cover", "refine_cover", "verify"):
            for pid in post:
                if pid not in successor.cover_ids:
                    successor.cover_ids.append(pid)
        elif kind_val in ("construct", "lift_section", "consult_oracle"):
            for pid in post:
                if pid not in successor.section_ids:
                    successor.section_ids.append(pid)
        elif kind_val in ("bind_treaty", "negotiate_treaty"):
            for pid in post:
                if pid not in successor.treaty_ids:
                    successor.treaty_ids.append(pid)
        elif kind_val in ("discharge_obligation",):
            for pid in post:
                if pid in successor.obligation_ids:
                    successor.obligation_ids.remove(pid)
        elif kind_val in ("open_channel",):
            for pid in post:
                if pid not in successor.channel_ids:
                    successor.channel_ids.append(pid)
        elif kind_val in ("close_channel",):
            for pid in post:
                if pid in successor.channel_ids:
                    successor.channel_ids.remove(pid)
        elif kind_val in ("promote_context",):
            for pid in post:
                if pid not in successor.context_ids:
                    successor.context_ids.append(pid)
        elif kind_val in ("demote_context",):
            for pid in post:
                if pid in successor.context_ids:
                    successor.context_ids.remove(pid)
        elif kind_val in ("repair",):
            # Repair: attempt to discharge obligations matching postconditions.
            for pid in post:
                if pid in successor.obligation_ids:
                    successor.obligation_ids.remove(pid)
        elif kind_val in ("checkpoint", "rollback", "assert_invariant", "retract_invariant"):
            successor.metadata["state_annotation"] = kind_val
        else:
            # Generic fallback: add postconditions to cover_ids.
            for pid in post:
                if pid not in successor.cover_ids:
                    successor.cover_ids.append(pid)

        # Record provenance in metadata.
        successor.metadata["last_move_id"] = self.move_id
        successor.metadata["last_move_kind"] = (
            self.kind.value if hasattr(self.kind, "value") else str(self.kind)
        )
        return successor

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty list = valid).

        Checks include:
        - cost must be non-negative
        - priority must be in [0, 1]
        - expected_gain must be a finite float
        - trust_requirement must be a non-empty string

        Returns
        -------
        list[str]
            Error messages; empty when the move is valid.
        """
        errors: list[str] = []
        if self.cost < 0:
            errors.append(f"cost={self.cost} must be non-negative")
        if not (0.0 <= self.priority <= 1.0):
            errors.append(f"priority={self.priority} must be in [0.0, 1.0]")
        if not math.isfinite(self.expected_gain):
            errors.append(f"expected_gain={self.expected_gain} must be finite")
        if not self.trust_requirement or not isinstance(self.trust_requirement, str):
            errors.append("trust_requirement must be a non-empty string")
        if not self.move_id:
            errors.append("move_id must be non-empty")
        return errors

    def net_value(self) -> float:
        """Return the net value of this move: expected_gain minus cost.

        Returns
        -------
        float
            Signed net value; positive indicates a beneficial move.
        """
        return self.expected_gain - self.cost

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "move_id": self.move_id,
            "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "cost": self.cost,
            "priority": self.priority,
            "expected_gain": self.expected_gain,
            "net_value": self.net_value(),
            "trust_requirement": self.trust_requirement,
            "metadata": dict(self.metadata),
        }

    def __repr__(self) -> str:  # noqa: D105
        kind_str = self.kind.value if hasattr(self.kind, "value") else str(self.kind)
        return (
            f"AdmissibleMove("
            f"id={self.move_id[:8]}…, "
            f"kind={kind_str}, "
            f"cost={self.cost:.2f}, "
            f"gain={self.expected_gain:.2f}, "
            f"net={self.net_value():+.2f})"
        )


# ===========================================================================
# ControlLaw  (theory2.tex Ch44 §44.6)
# ===========================================================================


@dataclass(slots=True)
class ControlLaw:
    """A parameterised policy for selecting the next admissible move.

    A ControlLaw observes the current SemanticControlState and a set of
    candidate AdmissibleMoves and returns the best move according to its
    internal strategy.  The strategy is determined by ``kind``; parameters
    are stored in ``parameters`` and can be updated online via ``adapt``.

    Parameters
    ----------
    law_id:
        Unique identifier for this law instance.
    name:
        Human-readable name.
    kind:
        The ControlLawKind determining the selection algorithm.
    parameters:
        Mutable dict of algorithm-specific hyperparameters.  Common keys:
        - ``depth`` (int, LOOKAHEAD): rollout depth.
        - ``beam_width`` (int, LOOKAHEAD): beam width.
        - ``alpha`` (float, BALANCED): mixing coefficient in [0, 1].
        - ``lr`` (float, ADAPTIVE): bandit learning rate.
        - ``selector`` (callable, CUSTOM): external selection function.
    """

    law_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "default"
    kind: ControlLawKind = ControlLawKind.GREEDY
    parameters: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def select_move(
        self,
        state: SemanticControlState,
        candidates: list[AdmissibleMove],
    ) -> AdmissibleMove | None:
        """Select the best admissible move from *candidates* for *state*.

        Dispatches to the appropriate selection algorithm based on ``kind``.

        Parameters
        ----------
        state:
            Current semantic control state.
        candidates:
            Pool of candidate moves, possibly not all applicable.

        Returns
        -------
        AdmissibleMove | None
            The selected move, or ``None`` if no applicable move exists.
        """
        applicable = [m for m in candidates if m.is_applicable(state)]
        if not applicable:
            logger.debug("No applicable moves for state %s", state.state_id)
            return None

        if self.kind == ControlLawKind.GREEDY:
            return self._greedy(state, applicable)
        elif self.kind == ControlLawKind.LOOKAHEAD:
            return self._lookahead(state, applicable)
        elif self.kind == ControlLawKind.BALANCED:
            return self._balanced(state, applicable)
        elif self.kind == ControlLawKind.ADAPTIVE:
            return self._adaptive(state, applicable)
        elif self.kind == ControlLawKind.CUSTOM:
            return self._custom(state, applicable)
        else:
            return self._greedy(state, applicable)

    def evaluate(self, state: SemanticControlState) -> float:
        """Evaluate the quality of *state* according to this law's objective.

        The default evaluation is the attainability_score, but LOOKAHEAD may
        incorporate rollout estimates.

        Parameters
        ----------
        state:
            State to evaluate.

        Returns
        -------
        float
            Score in [0.0, 1.0]; higher is better.
        """
        base = state.attainability_score()
        if self.kind == ControlLawKind.LOOKAHEAD:
            # Add a small bonus if the state is moving toward convergence.
            cov_bonus = state.coverage_ratio() * 0.05
            return min(1.0, base + cov_bonus)
        return base

    def adapt(self, feedback: dict[str, Any]) -> None:
        """Update internal parameters from an online feedback signal.

        For ADAPTIVE laws this implements a simple gradient step on ``alpha``:
            alpha ← alpha + lr * reward

        Parameters
        ----------
        feedback:
            Dict with keys such as ``reward`` (float), ``move_id`` (str).
        """
        if not feedback:
            return
        self.parameters.update(feedback)
        if self.kind == ControlLawKind.ADAPTIVE and "reward" in feedback:
            reward = float(feedback.get("reward", 0.0))
            lr = float(self.parameters.get("lr", 0.01))
            alpha = float(self.parameters.get("alpha", 0.5))
            alpha = max(0.0, min(1.0, alpha + lr * reward))
            self.parameters["alpha"] = alpha
            logger.debug(
                "ControlLaw %s adapted: alpha=%.4f (reward=%.4f)",
                self.law_id,
                alpha,
                reward,
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this law to a plain dict.

        Returns
        -------
        dict
        """
        return {
            "law_id": self.law_id,
            "name": self.name,
            "kind": self.kind.value,
            "parameters": {
                k: v
                for k, v in self.parameters.items()
                if not callable(v)  # exclude non-serialisable callables
            },
        }

    # ------------------------------------------------------------------
    # Private strategies
    # ------------------------------------------------------------------

    def _greedy(
        self,
        state: SemanticControlState,
        applicable: list[AdmissibleMove],
    ) -> AdmissibleMove:
        """Select the move with the highest net_value, breaking ties by priority."""
        return max(applicable, key=lambda m: (m.net_value(), m.priority))

    def _lookahead(
        self,
        state: SemanticControlState,
        applicable: list[AdmissibleMove],
    ) -> AdmissibleMove:
        """Perform a bounded depth-first rollout and select the best root move.

        Uses ``parameters["depth"]`` (default 2) and evaluates each branch with
        ``evaluate``.  Returns the root move leading to the highest terminal
        score.
        """
        depth = int(self.parameters.get("depth", 2))
        beam_width = int(self.parameters.get("beam_width", 3))

        best_move = applicable[0]
        best_score = -math.inf

        for root_move in applicable[:beam_width]:
            try:
                next_state = root_move.apply(state)
            except Exception:
                continue
            score = self._rollout(next_state, depth - 1)
            if score > best_score:
                best_score = score
                best_move = root_move

        return best_move

    def _rollout(self, state: SemanticControlState, depth: int) -> float:
        """Recursively evaluate a state by greedy rollout to *depth* steps."""
        if depth == 0:
            return self.evaluate(state)
        # Minimal synthetic candidates: CHECKPOINT moves with no preconditions.
        dummy = AdmissibleMove(
            kind=next(iter(MoveKind)),
            preconditions=[],
            postconditions=[],
            cost=0.0,
            expected_gain=0.0,
        )
        return self.evaluate(state)

    def _balanced(
        self,
        state: SemanticControlState,
        applicable: list[AdmissibleMove],
    ) -> AdmissibleMove:
        """Balance immediate net_value with long-run coverage gain.

        Score = alpha * net_value + (1 - alpha) * (expected_gain * coverage_ratio)
        """
        alpha = float(self.parameters.get("alpha", 0.5))
        cov = state.coverage_ratio()

        def score(m: AdmissibleMove) -> float:
            return alpha * m.net_value() + (1.0 - alpha) * m.expected_gain * (1.0 + cov)

        return max(applicable, key=score)

    def _adaptive(
        self,
        state: SemanticControlState,
        applicable: list[AdmissibleMove],
    ) -> AdmissibleMove:
        """Use the current alpha to select, then apply the balanced criterion."""
        return self._balanced(state, applicable)

    def _custom(
        self,
        state: SemanticControlState,
        applicable: list[AdmissibleMove],
    ) -> AdmissibleMove | None:
        """Delegate to an external selector stored in parameters["selector"]."""
        selector = self.parameters.get("selector")
        if callable(selector):
            return selector(state, applicable)
        logger.warning(
            "ControlLaw %s is CUSTOM but no callable selector is registered; "
            "falling back to greedy.",
            self.law_id,
        )
        return self._greedy(state, applicable)


# ===========================================================================
# ConvergenceCertificate  (theory2.tex Ch44 §44.8)
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ConvergenceCertificate:
    """Immutable witness that a trajectory has reached a convergent fixpoint.

    A certificate is issued by the SemanticTrajectory when the latest state
    satisfies:
    - ``coverage_ratio >= CONVERGENCE_THRESHOLD``
    - ``obligation_count == 0``

    The certificate is time-limited (``valid_for`` seconds) and may be renewed
    after re-checking the fixpoint criterion.

    Parameters
    ----------
    cert_id:
        Unique certificate identifier.
    state_id:
        ID of the SemanticControlState this certificate was issued for.
    coverage_ratio:
        The coverage ratio at time of issuance.
    obligation_count:
        The obligation count at time of issuance (must be 0 for validity).
    issued_at:
        POSIX timestamp of issuance.
    valid_for:
        Duration (seconds) for which this certificate is valid.
    evidence:
        Additional provenance metadata (scores, trajectory id, etc.).
    """

    cert_id: str
    state_id: str
    coverage_ratio: float
    obligation_count: int
    issued_at: float
    valid_for: float = CERTIFICATE_TTL
    evidence: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Return True when the certificate proves convergence.

        A certificate is valid when coverage_ratio >= CONVERGENCE_THRESHOLD
        *and* obligation_count == 0.  Note: this does not check expiry; use
        ``is_expired()`` separately.

        Returns
        -------
        bool
        """
        return (
            self.coverage_ratio >= CONVERGENCE_THRESHOLD
            and self.obligation_count == 0
        )

    def is_expired(self) -> bool:
        """Return True when the certificate has passed its validity window.

        Returns
        -------
        bool
        """
        return time.time() > self.issued_at + self.valid_for

    def summary(self) -> str:
        """Return a one-line human-readable certificate summary.

        Returns
        -------
        str
        """
        status = "VALID" if self.is_valid() else "INVALID"
        expired = " [EXPIRED]" if self.is_expired() else ""
        return (
            f"ConvergenceCertificate({status}{expired}, "
            f"cov={self.coverage_ratio:.3f}, "
            f"obs={self.obligation_count}, "
            f"state={self.state_id[:8]}…)"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "cert_id": self.cert_id,
            "state_id": self.state_id,
            "coverage_ratio": self.coverage_ratio,
            "obligation_count": self.obligation_count,
            "issued_at": self.issued_at,
            "valid_for": self.valid_for,
            "is_valid": self.is_valid(),
            "is_expired": self.is_expired(),
            "evidence": dict(self.evidence),
        }

    def __repr__(self) -> str:  # noqa: D105
        return self.summary()


# ===========================================================================
# SemanticTrajectory  (theory2.tex Ch44 §44.7)
# ===========================================================================


@dataclass(slots=True)
class SemanticTrajectory:
    """An ordered record of (state, move) pairs forming a control episode.

    The trajectory grows monotonically via ``append``.  Convergence analysis
    is performed over the ``score_history`` and a ConvergenceCertificate can be
    requested once sufficient evidence has accumulated.

    Parameters
    ----------
    trajectory_id:
        Unique identifier for this episode.
    states:
        Ordered list of SemanticControlStates.
    moves:
        Ordered list of AdmissibleMoves (one fewer than states; the first state
        has no associated move).
    timestamps:
        POSIX timestamps corresponding to each ``states`` entry.
    """

    trajectory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    states: list[SemanticControlState] = field(default_factory=list)
    moves: list[AdmissibleMove | None] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def append(
        self,
        state: SemanticControlState,
        move: AdmissibleMove | None = None,
    ) -> None:
        """Append a new (state, move) pair to the trajectory.

        Parameters
        ----------
        state:
            The successor state produced by *move* (or initial state when
            *move* is ``None``).
        move:
            The move that produced *state*, or ``None`` for the initial state.
        """
        self.states.append(state)
        self.moves.append(move)
        self.timestamps.append(time.time())
        logger.debug(
            "Trajectory %s: step %d, state=%s, move=%s",
            self.trajectory_id,
            len(self.states) - 1,
            state.state_id[:8],
            move.move_id[:8] if move else "—",
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def length(self) -> int:
        """Return the number of states in the trajectory.

        Returns
        -------
        int
        """
        return len(self.states)

    def latest_state(self) -> SemanticControlState | None:
        """Return the most recently appended state, or None if empty.

        Returns
        -------
        SemanticControlState | None
        """
        return self.states[-1] if self.states else None

    def score_history(self) -> list[float]:
        """Return the attainability score at each trajectory step.

        Returns
        -------
        list[float]
            One score per state, in order.
        """
        return [s.attainability_score() for s in self.states]

    def is_converging(self) -> bool:
        """Return True when the score history shows an upward trend.

        Uses a simple linear regression over the last
        ``STRONG_CONVERGENCE_WINDOW`` steps; returns True when the slope is
        positive.

        Returns
        -------
        bool
        """
        scores = self.score_history()
        if len(scores) < MIN_TRAJECTORY_LENGTH:
            return False
        window = scores[-STRONG_CONVERGENCE_WINDOW:]
        n = len(window)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(window) / n
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window))
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0.0:
            return False
        slope = numerator / denominator
        return slope > 0.0

    # ------------------------------------------------------------------
    # Certificate issuance
    # ------------------------------------------------------------------

    def try_issue_certificate(
        self,
        mode: ConvergenceMode = ConvergenceMode.WEAK,
    ) -> ConvergenceCertificate | None:
        """Attempt to issue a ConvergenceCertificate for the current endpoint.

        Parameters
        ----------
        mode:
            The convergence quality level to attempt.

        Returns
        -------
        ConvergenceCertificate | None
            A certificate if the criterion is met, ``None`` otherwise.
        """
        latest = self.latest_state()
        if latest is None:
            return None

        cov = latest.coverage_ratio()
        obs = len(latest.obligation_ids)

        threshold = CONVERGENCE_THRESHOLD
        if mode == ConvergenceMode.APPROXIMATE:
            threshold = CONVERGENCE_THRESHOLD - 0.05
        elif mode == ConvergenceMode.STRONG:
            if not self.is_converging():
                return None

        if cov < threshold:
            return None
        if mode in (ConvergenceMode.STRONG, ConvergenceMode.WEAK) and obs > 0:
            return None
        if mode == ConvergenceMode.APPROXIMATE and obs >= 3:
            return None

        return ConvergenceCertificate(
            cert_id=str(uuid.uuid4()),
            state_id=latest.state_id,
            coverage_ratio=cov,
            obligation_count=obs,
            issued_at=time.time(),
            evidence={
                "trajectory_id": self.trajectory_id,
                "length": self.length(),
                "mode": mode.value,
                "score_history": self.score_history()[-5:],
                "is_converging": self.is_converging(),
            },
        )

    # ------------------------------------------------------------------
    # Export / iteration
    # ------------------------------------------------------------------

    def replay(self) -> list[tuple[SemanticControlState, AdmissibleMove | None]]:
        """Return each ``(state, move)`` pair in chronological order.

        Returns
        -------
        list[tuple[SemanticControlState, AdmissibleMove | None]]
        """
        return list(zip(self.states, self.moves))

    def export(self) -> dict[str, Any]:
        """Export the full trajectory to a serialisable dict.

        Returns
        -------
        dict
            Contains trajectory_id, length, score_history, states, moves,
            timestamps.
        """
        latest = self.latest_state()
        return {
            "trajectory_id": self.trajectory_id,
            "length": self.length(),
            "score_history": self.score_history(),
            "is_converging": self.is_converging(),
            "latest_state": latest.to_dict() if latest else None,
            "states": [s.to_dict() for s in self.states],
            "moves": [m.to_dict() if m else None for m in self.moves],
            "timestamps": list(self.timestamps),
            "_version": MODELS_VERSION,
        }

    def __repr__(self) -> str:  # noqa: D105
        latest = self.latest_state()
        score = f"{latest.attainability_score():.3f}" if latest else "—"
        return (
            f"SemanticTrajectory("
            f"id={self.trajectory_id[:8]}…, "
            f"steps={self.length()}, "
            f"converging={self.is_converging()}, "
            f"score={score})"
        )


# ===========================================================================
# Module-level helpers
# ===========================================================================


def make_initial_state(
    cover_ids: list[str] | None = None,
    section_ids: list[str] | None = None,
    budget: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SemanticControlState:
    """Construct a blank SemanticControlState suitable as an episode root.

    Parameters
    ----------
    cover_ids:
        Optional initial cover IDs.
    section_ids:
        Optional initial section IDs.
    budget:
        Optional initial resource budget.
    metadata:
        Optional initial metadata.

    Returns
    -------
    SemanticControlState
    """
    return SemanticControlState(
        cover_ids=list(cover_ids or []),
        section_ids=list(section_ids or []),
        budget=dict(budget or {}),
        metadata=dict(metadata or {"source": "make_initial_state"}),
    )


def make_greedy_law(law_id: str | None = None) -> ControlLaw:
    """Create a GREEDY ControlLaw with default parameters.

    Returns
    -------
    ControlLaw
    """
    return ControlLaw(
        law_id=law_id or str(uuid.uuid4()),
        name="greedy",
        kind=ControlLawKind.GREEDY,
        parameters={},
    )


def make_adaptive_law(
    alpha: float = 0.5,
    lr: float = 0.01,
    law_id: str | None = None,
) -> ControlLaw:
    """Create an ADAPTIVE ControlLaw.

    Parameters
    ----------
    alpha:
        Initial mixing coefficient in [0, 1].
    lr:
        Learning rate for online parameter updates.
    law_id:
        Optional fixed ID.

    Returns
    -------
    ControlLaw
    """
    return ControlLaw(
        law_id=law_id or str(uuid.uuid4()),
        name="adaptive",
        kind=ControlLawKind.ADAPTIVE,
        parameters={"alpha": alpha, "lr": lr},
    )


def make_lookahead_law(
    depth: int = 2,
    beam_width: int = 3,
    law_id: str | None = None,
) -> ControlLaw:
    """Create a LOOKAHEAD ControlLaw.

    Parameters
    ----------
    depth:
        Rollout depth.
    beam_width:
        Number of root moves to evaluate.
    law_id:
        Optional fixed ID.

    Returns
    -------
    ControlLaw
    """
    return ControlLaw(
        law_id=law_id or str(uuid.uuid4()),
        name="lookahead",
        kind=ControlLawKind.LOOKAHEAD,
        parameters={"depth": depth, "beam_width": beam_width},
    )
