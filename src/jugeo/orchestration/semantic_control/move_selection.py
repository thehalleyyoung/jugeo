"""Move selection and precondition checking for JuGeo semantic control (theory2.tex Ch44).

This module implements the move-selection pipeline for the JuGeo semantic-control
layer.  Its architecture follows the admissible-move framework of theory2.tex
Ch44, which defines proof-search progression as *selecting and executing
admissible moves* from the frontier of the semantic site.

Key responsibilities
────────────────────
*   **PreconditionChecker** – evaluates whether a move's preconditions hold
    in the current state (theory2.tex §44.1 – applicability).
*   **PostconditionVerifier** – after a move executes, verifies that its
    declared postconditions are satisfied and computes realised gain.
*   **MoveEnumerator** – enumerates applicable moves from a registry,
    respecting the ``max_moves`` capacity bound.
*   **MovePrioritizer** – sorts moves by a configurable scoring strategy
    (expected gain, cost-adjusted, trust-adjusted).
*   **MoveConflictResolver** – detects and resolves conflicts among moves
    that would interfere if both were applied (theory2.tex §44.2 –
    non-interference).
*   **MoveApplicationEngine** – applies a move (or sequence), optionally in
    dry-run mode, and collects postcondition results.
*   **MoveSelector** – top-level façade that wires all of the above together
    and exposes a :meth:`~MoveSelector.select` / :meth:`~MoveSelector.explain`
    interface.

Design notes
────────────
*   Immutable value objects use ``@dataclass(frozen=True)``.
*   Mutable workers use ``@dataclass(slots=True)``.
*   All upstream imports are guarded so the module degrades gracefully.
*   Caching in :class:`PreconditionChecker` is keyed on
    ``(move_id, state_id)`` to avoid redundant evaluation.
*   The :class:`MoveSelector.explain` diagnostic dictionary is designed to
    feed the CLI dashboard without further processing.

References
──────────
*   theory2.tex Ch44    – Semantic Control
*   theory2.tex §44.1   – Applicability and Admissible Moves
*   theory2.tex §44.2   – Non-Interference and Conflict Resolution
*   theory2.tex §44.3   – Control Laws and Priority Strategies
*   theory2.tex §44.4   – Postcondition Verification and Gain Measurement
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# ── Internal JuGeo imports (guarded) ────────────────────────────────────────

try:
    from jugeo.orchestration.semantic_control.models import (
        AdmissibleMove,
        ControlLaw,
        ControlLawKind,
        SemanticControlState,
        StateDelta,
    )
except Exception:  # pragma: no cover

    class ControlLawKind(enum.Enum):  # type: ignore[no-redef]
        GREEDY = "greedy"
        LOOKAHEAD = "lookahead"
        BALANCED = "balanced"
        ADAPTIVE = "adaptive"

    @dataclass(slots=True)
    class SemanticControlState:  # type: ignore[no-redef]
        state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        cover_ids: list[str] = field(default_factory=list)
        context_ids: list[str] = field(default_factory=list)
        section_ids: list[str] = field(default_factory=list)
        treaty_ids: list[str] = field(default_factory=list)
        obligation_ids: list[str] = field(default_factory=list)
        channel_ids: list[str] = field(default_factory=list)
        budget: dict[str, float] = field(default_factory=dict)
        timestamp: float = field(default_factory=time.time)
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_admissible(self) -> bool:
            return all(v >= 0 for v in self.budget.values())

        def coverage_ratio(self) -> float:
            total = len(self.section_ids) or 1
            return len(self.cover_ids) / total

        def attainability_score(self) -> float:
            return self.coverage_ratio() * (1.0 if self.is_admissible() else 0.0)

        def to_dict(self) -> dict[str, Any]:
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
            }

    @dataclass(slots=True)
    class AdmissibleMove:  # type: ignore[no-redef]
        move_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        kind: str = "expand"
        preconditions: list[str] = field(default_factory=list)
        postconditions: list[str] = field(default_factory=list)
        cost: float = 1.0
        priority: int = 0
        expected_gain: float = 0.0
        trust_requirement: float = 0.0
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_applicable(self, state: "SemanticControlState") -> bool:
            return state.is_admissible() and state.budget.get("default", 1.0) >= self.cost

        def apply(self, state: "SemanticControlState") -> "SemanticControlState":
            new_budget = dict(state.budget)
            new_budget["default"] = new_budget.get("default", 0.0) - self.cost
            return SemanticControlState(
                state_id=str(uuid.uuid4()),
                cover_ids=list(state.cover_ids),
                context_ids=list(state.context_ids),
                section_ids=list(state.section_ids),
                treaty_ids=list(state.treaty_ids),
                obligation_ids=list(state.obligation_ids),
                channel_ids=list(state.channel_ids),
                budget=new_budget,
                timestamp=time.time(),
                metadata=dict(state.metadata),
            )

        def validate(self) -> list[str]:
            violations: list[str] = []
            if self.cost < 0:
                violations.append(f"cost must be non-negative, got {self.cost}")
            if not self.move_id:
                violations.append("move_id must be non-empty")
            return violations

        def to_dict(self) -> dict[str, Any]:
            return {
                "move_id": self.move_id,
                "kind": self.kind,
                "preconditions": list(self.preconditions),
                "postconditions": list(self.postconditions),
                "cost": self.cost,
                "priority": self.priority,
                "expected_gain": self.expected_gain,
                "trust_requirement": self.trust_requirement,
                "metadata": dict(self.metadata),
            }

        def net_value(self) -> float:
            return self.expected_gain - self.cost

    @dataclass(slots=True)
    class ControlLaw:  # type: ignore[no-redef]
        law_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        name: str = "greedy"
        kind: "ControlLawKind" = field(default_factory=lambda: ControlLawKind.GREEDY)
        parameters: dict[str, Any] = field(default_factory=dict)

        def select_move(
            self,
            moves: list["AdmissibleMove"],
            state: "SemanticControlState",
        ) -> "AdmissibleMove | None":
            if not moves:
                return None
            return max(moves, key=lambda m: m.net_value())

        def evaluate(self, state: "SemanticControlState") -> float:
            return state.attainability_score()

        def adapt(self, feedback: dict[str, Any]) -> None:
            pass

        def to_dict(self) -> dict[str, Any]:
            return {
                "law_id": self.law_id,
                "name": self.name,
                "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
                "parameters": dict(self.parameters),
            }

    @dataclass(frozen=True)
    class StateDelta:  # type: ignore[no-redef]
        added_covers: tuple[str, ...] = ()
        removed_covers: tuple[str, ...] = ()
        added_sections: tuple[str, ...] = ()
        removed_sections: tuple[str, ...] = ()
        added_obligations: tuple[str, ...] = ()
        resolved_obligations: tuple[str, ...] = ()
        budget_delta: dict[str, float] = field(default_factory=dict)
        score_delta: float = 0.0


try:
    from jugeo.orchestration.controller import MoveKind
except Exception:  # pragma: no cover

    class MoveKind(enum.Enum):  # type: ignore[no-redef]
        EXPAND = "expand"
        REFINE = "refine"
        MERGE = "merge"
        SPLIT = "split"
        CLOSE = "close"
        DELEGATE = "delegate"
        NEGOTIATE = "negotiate"
        ARCHIVE = "archive"


try:
    from jugeo.orchestration.semantic_control.state_management import StateValidator
except Exception:  # pragma: no cover

    @dataclass(slots=True)
    class StateValidator:  # type: ignore[no-redef]
        rules: list[Callable] = field(default_factory=list)
        strict: bool = True

        def validate(self, state: "SemanticControlState") -> list[str]:
            return []

        def is_valid(self, state: "SemanticControlState") -> bool:
            return True


# ── Module constants ─────────────────────────────────────────────────────────

VERSION: str = "0.1.0"
"""Module version, incremented on breaking schema changes."""

LOGGER: logging.Logger = logging.getLogger(__name__)
"""Module-level logger; configure via ``logging.getLogger('jugeo...')``."""

DEFAULT_MAX_MOVES: int = 100
"""Default maximum number of moves enumerated per :class:`MoveEnumerator` call."""

DEFAULT_PRIORITIZER_STRATEGY: str = "expected_gain"
"""Default scoring strategy used by :class:`MovePrioritizer`."""

DEFAULT_CONFLICT_RESOLUTION: str = "highest_priority"
"""Default conflict resolution strategy for :class:`MoveConflictResolver`."""

PRECONDITION_CACHE_TTL: float = 60.0
"""Seconds before a cached :class:`PreconditionResult` is considered stale."""


# ── PreconditionResult ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreconditionResult:
    """Immutable result of evaluating a move's preconditions against a state.

    Produced by :class:`PreconditionChecker` and consumed by
    :class:`MoveSelector` to filter inapplicable moves before prioritisation.

    Parameters
    ----------
    move_id:
        ID of the :class:`AdmissibleMove` that was checked.
    satisfied:
        ``True`` iff all preconditions hold in the checked state.
    violations:
        Tuple of violation messages; empty when ``satisfied=True``.
    checked_at:
        Unix epoch float when the check was performed.

    References
    ----------
    theory2.tex §44.1 – Applicability condition.
    """

    move_id: str
    satisfied: bool
    violations: tuple[str, ...]
    checked_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation.
        """
        return {
            "move_id": self.move_id,
            "satisfied": self.satisfied,
            "violations": list(self.violations),
            "checked_at": self.checked_at,
        }


# ── PostconditionResult ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PostconditionResult:
    """Immutable result of verifying a move's postconditions after execution.

    Produced by :class:`PostconditionVerifier` following move application.
    The ``gain`` field is the *realised* attainability improvement, which may
    differ from ``move.expected_gain`` (theory2.tex §44.4 – gain measurement).

    Parameters
    ----------
    move_id:
        ID of the executed :class:`AdmissibleMove`.
    satisfied:
        ``True`` iff all postconditions hold in the post-execution state.
    violations:
        Tuple of violation messages; empty when ``satisfied=True``.
    gain:
        Realised gain = ``after.attainability_score() - before.attainability_score()``.
    checked_at:
        Unix epoch float when verification ran.

    References
    ----------
    theory2.tex §44.4 – Postcondition Verification and Gain Measurement.
    """

    move_id: str
    satisfied: bool
    violations: tuple[str, ...]
    gain: float
    checked_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation.
        """
        return {
            "move_id": self.move_id,
            "satisfied": self.satisfied,
            "violations": list(self.violations),
            "gain": self.gain,
            "checked_at": self.checked_at,
        }


# ── PreconditionChecker ──────────────────────────────────────────────────────


@dataclass(slots=True)
class PreconditionChecker:
    """Evaluates move preconditions against a :class:`SemanticControlState`.

    Implements the applicability test of theory2.tex §44.1.  Results are
    cached by ``(move_id, state_id)`` key to avoid redundant evaluation
    within a single control step.

    Parameters
    ----------
    strict:
        If ``True``, moves with unknown precondition expressions are marked
        unsatisfied.  If ``False``, unknown expressions are treated as
        vacuously true (lenient mode for bootstrapping).
    cache:
        Result cache mapping ``"<move_id>:<state_id>"`` → :class:`PreconditionResult`.

    References
    ----------
    theory2.tex §44.1 – Applicability and Admissible Moves.
    """

    strict: bool = True
    cache: dict[str, PreconditionResult] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def check(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> PreconditionResult:
        """Evaluate all preconditions of *move* against *state*.

        Checks the result cache first.  On cache miss, evaluates each
        precondition string via :meth:`_evaluate_condition`.  The result
        is cached and returned.

        Parameters
        ----------
        move:
            The move whose preconditions are to be checked.
        state:
            The current semantic control state.

        Returns
        -------
        PreconditionResult
            Cached or freshly computed result.
        """
        cache_key = f"{move.move_id}:{state.state_id}"
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if time.time() - cached.checked_at < PRECONDITION_CACHE_TTL:
                return cached
        violations: list[str] = []
        if hasattr(move, "is_applicable") and callable(move.is_applicable):
            applicable = move.is_applicable(state)
            if not applicable:
                violations.append("move.is_applicable() returned False")
        for cond in getattr(move, "preconditions", []):
            if not self._evaluate_condition(cond, state):
                violations.append(f"Precondition not satisfied: '{cond}'")
                if self.strict:
                    break
        result = PreconditionResult(
            move_id=move.move_id,
            satisfied=len(violations) == 0,
            violations=tuple(violations),
            checked_at=time.time(),
        )
        self.cache[cache_key] = result
        return result

    def check_all(
        self,
        moves: list[AdmissibleMove],
        state: SemanticControlState,
    ) -> list[PreconditionResult]:
        """Evaluate preconditions for every move in *moves*.

        Parameters
        ----------
        moves:
            List of moves to check.
        state:
            Current semantic control state.

        Returns
        -------
        list[PreconditionResult]
            One result per move, in the same order as *moves*.
        """
        return [self.check(m, state) for m in moves]

    def clear_cache(self) -> None:
        """Discard all cached precondition results.

        Call this at the start of each control step to ensure stale cache
        entries do not suppress re-evaluation on a newly transitioned state.
        """
        self.cache.clear()
        LOGGER.debug("PreconditionChecker: cache cleared")

    def _evaluate_condition(
        self,
        condition: str,
        state: SemanticControlState,
    ) -> bool:
        """Evaluate a single precondition expression string against *state*.

        Supported expression patterns (case-insensitive):

        *   ``"budget_positive"``   – all budget values ≥ 0.
        *   ``"has_covers"``        – at least one cover present.
        *   ``"has_obligations"``   – at least one unresolved obligation.
        *   ``"no_obligations"``    – no unresolved obligations.
        *   ``"admissible"``        – ``state.is_admissible()`` is True.
        *   ``"has_sections"``      – at least one section present.
        *   ``"coverage_below_1"``  – coverage ratio < 1.0.
        *   Anything else: vacuously ``True`` in lenient mode, ``False`` in
            strict mode (so unknown conditions fail-safe).

        Parameters
        ----------
        condition:
            String expression representing the precondition.
        state:
            State to evaluate against.

        Returns
        -------
        bool
        """
        normalized = condition.strip().lower()
        dispatch: dict[str, Callable[[], bool]] = {
            "budget_positive": lambda: all(v >= 0 for v in state.budget.values()),
            "has_covers": lambda: len(state.cover_ids) > 0,
            "has_obligations": lambda: len(state.obligation_ids) > 0,
            "no_obligations": lambda: len(state.obligation_ids) == 0,
            "admissible": lambda: state.is_admissible(),
            "has_sections": lambda: len(state.section_ids) > 0,
            "coverage_below_1": lambda: state.coverage_ratio() < 1.0,
            "coverage_above_0": lambda: state.coverage_ratio() > 0.0,
            "has_channels": lambda: len(state.channel_ids) > 0,
            "has_contexts": lambda: len(state.context_ids) > 0,
            "has_treaties": lambda: len(state.treaty_ids) > 0,
        }
        fn = dispatch.get(normalized)
        if fn is None:
            if self.strict:
                LOGGER.debug(
                    "PreconditionChecker: unknown condition '%s' (strict=True → False)",
                    condition,
                )
                return False
            LOGGER.debug(
                "PreconditionChecker: unknown condition '%s' (strict=False → True)",
                condition,
            )
            return True
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "PreconditionChecker: error evaluating '%s': %s", condition, exc
            )
            return False


# ── PostconditionVerifier ────────────────────────────────────────────────────


@dataclass(slots=True)
class PostconditionVerifier:
    """Verifies that a move's postconditions hold after execution.

    Implements the gain-measurement step of theory2.tex §44.4.  The
    *realised gain* is the increase in ``attainability_score()`` from
    *before* to *after* state.

    Parameters
    ----------
    tolerance:
        Minimum gain required for a postcondition to be deemed satisfied
        when the postcondition string is ``"gain_positive"`` or similar
        gain-threshold expressions.  Defaults to 0.01.

    References
    ----------
    theory2.tex §44.4 – Postcondition Verification and Gain Measurement.
    """

    tolerance: float = 0.01

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def verify(
        self,
        move: AdmissibleMove,
        before: SemanticControlState,
        after: SemanticControlState,
    ) -> PostconditionResult:
        """Verify *move*'s postconditions given the before/after state pair.

        Evaluates each string in ``move.postconditions`` using the same
        expression-dispatch pattern as :class:`PreconditionChecker`, but
        relative to the *after* state (and the delta from *before* to *after*).
        The realised ``gain`` is always computed regardless of postcondition
        satisfaction.

        Parameters
        ----------
        move:
            The move that was executed.
        before:
            State immediately before applying *move*.
        after:
            State produced by applying *move*.

        Returns
        -------
        PostconditionResult
        """
        gain = self.verify_gain(move, before, after)
        violations: list[str] = []
        for cond in getattr(move, "postconditions", []):
            if not self._evaluate_postcondition(cond, before, after, gain):
                violations.append(f"Postcondition not satisfied: '{cond}'")
        satisfied = len(violations) == 0
        return PostconditionResult(
            move_id=move.move_id,
            satisfied=satisfied,
            violations=tuple(violations),
            gain=gain,
            checked_at=time.time(),
        )

    def verify_gain(
        self,
        move: AdmissibleMove,
        before: SemanticControlState,
        after: SemanticControlState,
    ) -> float:
        """Compute the realised attainability gain for *move*.

        Defined as ``after.attainability_score() - before.attainability_score()``.
        A positive value means the move improved the semantic state.

        Parameters
        ----------
        move:
            The move (used only for logging context).
        before:
            Pre-application state.
        after:
            Post-application state.

        Returns
        -------
        float
            Signed gain; positive means improvement.
        """
        before_score = before.attainability_score()
        after_score = after.attainability_score()
        gain = after_score - before_score
        LOGGER.debug(
            "PostconditionVerifier: move %s gain=%.4f (%.4f → %.4f)",
            move.move_id[:8],
            gain,
            before_score,
            after_score,
        )
        return gain

    def batch_verify(
        self,
        results: list[tuple[AdmissibleMove, SemanticControlState, SemanticControlState]],
    ) -> list[PostconditionResult]:
        """Verify postconditions for a batch of ``(move, before, after)`` triples.

        Parameters
        ----------
        results:
            List of ``(move, before_state, after_state)`` triples, in the
            order the moves were applied.

        Returns
        -------
        list[PostconditionResult]
            One result per triple, preserving input order.
        """
        return [self.verify(move, before, after) for move, before, after in results]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_postcondition(
        self,
        condition: str,
        before: SemanticControlState,
        after: SemanticControlState,
        gain: float,
    ) -> bool:
        """Evaluate a single postcondition string given before/after states.

        Supported postcondition patterns:

        *   ``"gain_positive"``        – ``gain > self.tolerance``.
        *   ``"gain_non_negative"``    – ``gain >= 0``.
        *   ``"admissible"``           – ``after.is_admissible()``.
        *   ``"more_covers"``          – after has strictly more covers.
        *   ``"fewer_obligations"``    – after has strictly fewer obligations.
        *   ``"budget_non_negative"``  – all budget values in after ≥ 0.
        *   ``"no_regression"``        – ``gain >= -self.tolerance``.
        *   Anything else: vacuously ``True``.

        Parameters
        ----------
        condition:
            Postcondition expression string.
        before:
            State before move application.
        after:
            State after move application.
        gain:
            Pre-computed attainability gain.

        Returns
        -------
        bool
        """
        normalized = condition.strip().lower()
        dispatch: dict[str, Callable[[], bool]] = {
            "gain_positive": lambda: gain > self.tolerance,
            "gain_non_negative": lambda: gain >= 0.0,
            "admissible": lambda: after.is_admissible(),
            "more_covers": lambda: len(after.cover_ids) > len(before.cover_ids),
            "fewer_obligations": lambda: (
                len(after.obligation_ids) < len(before.obligation_ids)
            ),
            "budget_non_negative": lambda: all(
                v >= 0 for v in after.budget.values()
            ),
            "no_regression": lambda: gain >= -self.tolerance,
            "coverage_improved": lambda: after.coverage_ratio() > before.coverage_ratio(),
        }
        fn = dispatch.get(normalized)
        if fn is None:
            return True
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "PostconditionVerifier: error evaluating '%s': %s", condition, exc
            )
            return False


# ── MoveEnumerator ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class MoveEnumerator:
    """Enumerates applicable moves from a registry for a given state.

    The enumerator is the first stage in the move-selection pipeline.  It
    filters a *registry* of :class:`AdmissibleMove` objects down to those
    that are applicable in the current state, respecting the ``max_moves``
    capacity bound from theory2.tex §44.1.

    Parameters
    ----------
    max_moves:
        Upper bound on the number of moves returned per call.
    filter_inapplicable:
        If ``True`` (default), moves whose ``is_applicable()`` returns
        ``False`` are excluded before returning.
    """

    max_moves: int = DEFAULT_MAX_MOVES
    filter_inapplicable: bool = True

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def enumerate(
        self,
        state: SemanticControlState,
        registry: list[AdmissibleMove],
    ) -> list[AdmissibleMove]:
        """Return the applicable moves from *registry* for *state*.

        Applies ``filter_inapplicable`` and the ``max_moves`` cap.  If the
        registry is empty, falls back to :meth:`_build_default_moves`.

        Parameters
        ----------
        state:
            Current semantic control state.
        registry:
            Full move registry to filter.

        Returns
        -------
        list[AdmissibleMove]
            Applicable moves, up to ``max_moves``.
        """
        source = registry if registry else self._build_default_moves(state)
        if self.filter_inapplicable:
            applicable = [
                m for m in source
                if hasattr(m, "is_applicable") and m.is_applicable(state)
            ]
        else:
            applicable = list(source)
        return applicable[: self.max_moves]

    def enumerate_by_kind(
        self,
        state: SemanticControlState,
        kind: MoveKind,
        registry: list[AdmissibleMove],
    ) -> list[AdmissibleMove]:
        """Return applicable moves of a specific *kind*.

        Parameters
        ----------
        state:
            Current semantic control state.
        kind:
            :class:`MoveKind` discriminant to filter by.
        registry:
            Full move registry.

        Returns
        -------
        list[AdmissibleMove]
            Applicable moves whose ``kind`` attribute matches *kind*.
        """
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        all_applicable = self.enumerate(state, registry)
        return [
            m for m in all_applicable
            if (
                getattr(m, "kind", None) == kind
                or getattr(m, "kind", None) == kind_value
            )
        ]

    def count_applicable(
        self,
        state: SemanticControlState,
        registry: list[AdmissibleMove],
    ) -> int:
        """Return the total number of applicable moves without the ``max_moves`` cap.

        Useful for dashboard metrics without truncation.

        Parameters
        ----------
        state:
            Current semantic control state.
        registry:
            Full move registry.

        Returns
        -------
        int
            Count of moves that pass the ``is_applicable`` test.
        """
        if not self.filter_inapplicable:
            return len(registry)
        return sum(
            1 for m in registry
            if hasattr(m, "is_applicable") and m.is_applicable(state)
        )

    def _build_default_moves(
        self,
        state: SemanticControlState,
    ) -> list[AdmissibleMove]:
        """Construct a minimal set of default moves when the registry is empty.

        Default moves are derived from the current state:

        *   One ``"expand"`` move per uncovered section (up to 10).
        *   One ``"close"`` move if there are no remaining obligations.
        *   One ``"negotiate"`` move if there are active treaties.

        Parameters
        ----------
        state:
            Current semantic control state.

        Returns
        -------
        list[AdmissibleMove]
            Generated default moves.
        """
        moves: list[AdmissibleMove] = []
        covered = set(state.cover_ids)
        uncovered_sections = [s for s in state.section_ids if s not in covered]
        for sec_id in uncovered_sections[:10]:
            moves.append(
                AdmissibleMove(
                    move_id=str(uuid.uuid4()),
                    kind="expand",
                    preconditions=["admissible", "budget_positive"],
                    postconditions=["more_covers"],
                    cost=1.0,
                    priority=1,
                    expected_gain=0.1,
                    trust_requirement=0.0,
                    metadata={"target_section": sec_id},
                )
            )
        if not state.obligation_ids:
            moves.append(
                AdmissibleMove(
                    move_id=str(uuid.uuid4()),
                    kind="close",
                    preconditions=["no_obligations", "admissible"],
                    postconditions=["gain_non_negative"],
                    cost=0.5,
                    priority=5,
                    expected_gain=0.5,
                    trust_requirement=0.0,
                    metadata={"reason": "default_close"},
                )
            )
        if state.treaty_ids:
            moves.append(
                AdmissibleMove(
                    move_id=str(uuid.uuid4()),
                    kind="negotiate",
                    preconditions=["admissible", "has_treaties"],
                    postconditions=["no_regression"],
                    cost=2.0,
                    priority=2,
                    expected_gain=0.2,
                    trust_requirement=0.3,
                    metadata={"reason": "default_negotiate"},
                )
            )
        return moves


# ── MovePrioritizer ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class MovePrioritizer:
    """Sorts a list of moves by a configurable scoring strategy.

    Implements the priority ordering of theory2.tex §44.3.  Three built-in
    strategies are provided:

    *   ``"expected_gain"``    – sort descending by ``move.expected_gain``.
    *   ``"cost_adjusted"``    – sort descending by ``gain / (cost + ε)``.
    *   ``"trust_adjusted"``   – sort descending by gain penalised for
                                  unmet trust requirements.

    Parameters
    ----------
    strategy:
        One of the built-in strategy names above, or any custom key that
        can be handled via subclassing.
    weights:
        Optional per-criterion weights for a linear combination when
        ``strategy="weighted"``.

    References
    ----------
    theory2.tex §44.3 – Control Laws and Priority Strategies.
    """

    strategy: str = DEFAULT_PRIORITIZER_STRATEGY
    weights: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def prioritize(
        self,
        moves: list[AdmissibleMove],
        state: SemanticControlState,
    ) -> list[AdmissibleMove]:
        """Return *moves* sorted from best to worst by the current strategy.

        Parameters
        ----------
        moves:
            Candidate moves to sort.
        state:
            Current semantic control state (used by some strategies).

        Returns
        -------
        list[AdmissibleMove]
            Sorted copy (does not modify *moves* in-place).
        """
        if not moves:
            return []
        return sorted(moves, key=lambda m: self.score(m, state), reverse=True)

    def score(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> float:
        """Compute the scalar priority score for *move* under the current strategy.

        Parameters
        ----------
        move:
            Move to score.
        state:
            Current semantic control state.

        Returns
        -------
        float
            Higher is better.
        """
        if self.strategy == "expected_gain":
            return self._gain_score(move, state)
        if self.strategy == "cost_adjusted":
            return self._cost_adjusted_score(move, state)
        if self.strategy == "trust_adjusted":
            return self._trust_adjusted_score(move, state)
        if self.strategy == "weighted":
            w_gain = self.weights.get("gain", 1.0)
            w_cost = self.weights.get("cost", 0.5)
            w_trust = self.weights.get("trust", 0.3)
            return (
                w_gain * self._gain_score(move, state)
                + w_cost * self._cost_adjusted_score(move, state)
                + w_trust * self._trust_adjusted_score(move, state)
            )
        LOGGER.debug("MovePrioritizer: unknown strategy '%s', using expected_gain", self.strategy)
        return self._gain_score(move, state)

    def top_k(
        self,
        moves: list[AdmissibleMove],
        state: SemanticControlState,
        k: int,
    ) -> list[AdmissibleMove]:
        """Return the top-*k* moves by score.

        Parameters
        ----------
        moves:
            Candidate moves.
        state:
            Current semantic control state.
        k:
            Number of top moves to return.

        Returns
        -------
        list[AdmissibleMove]
            Up to *k* moves in descending score order.
        """
        ranked = self.prioritize(moves, state)
        return ranked[:k]

    def _gain_score(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,  # noqa: ARG002
    ) -> float:
        """Return the raw expected gain as the priority score.

        Parameters
        ----------
        move:
            Move to score.
        state:
            Current state (unused but kept for interface consistency).

        Returns
        -------
        float
        """
        return getattr(move, "expected_gain", 0.0)

    def _cost_adjusted_score(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,  # noqa: ARG002
    ) -> float:
        """Return the gain-to-cost ratio as the priority score.

        Formula: ``expected_gain / (cost + 1e-9)``

        Parameters
        ----------
        move:
            Move to score.
        state:
            Current state (unused).

        Returns
        -------
        float
        """
        gain = getattr(move, "expected_gain", 0.0)
        cost = max(getattr(move, "cost", 1.0), 1e-9)
        return gain / cost

    def _trust_adjusted_score(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> float:
        """Return gain penalised for unmet trust requirements.

        If ``move.trust_requirement`` exceeds a proxy trust level derived
        from ``state.metadata.get("trust_level", 1.0)``, the score is
        reduced proportionally.

        Formula: ``expected_gain * min(1.0, available_trust / requirement)``
        where ``requirement`` defaults to 1e-9 if zero.

        Parameters
        ----------
        move:
            Move to score.
        state:
            Current state, consulted for ``metadata["trust_level"]``.

        Returns
        -------
        float
        """
        gain = getattr(move, "expected_gain", 0.0)
        requirement = getattr(move, "trust_requirement", 0.0)
        if requirement <= 0:
            return gain
        available = state.metadata.get("trust_level", 1.0)
        trust_ratio = min(1.0, float(available) / max(requirement, 1e-9))
        return gain * trust_ratio


# ── MoveConflictResolver ─────────────────────────────────────────────────────


@dataclass(slots=True)
class MoveConflictResolver:
    """Detects and resolves conflicts between pairs of :class:`AdmissibleMove`.

    Two moves *conflict* if they would interfere when applied to the same
    state (theory2.tex §44.2 – non-interference).  The heuristic used here
    is that two moves conflict if they share at least one postcondition token
    (e.g. both try to modify the same obligation or cover) or if one's
    postcondition appears in the other's precondition set.

    Parameters
    ----------
    resolution_strategy:
        How to break ties: ``"highest_priority"`` (default) or ``"lowest_cost"``.

    References
    ----------
    theory2.tex §44.2 – Non-Interference and Conflict Resolution.
    """

    resolution_strategy: str = DEFAULT_CONFLICT_RESOLUTION

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def detect_conflicts(
        self,
        moves: list[AdmissibleMove],
    ) -> list[tuple[AdmissibleMove, AdmissibleMove]]:
        """Find all conflicting pairs in *moves*.

        Performs an O(n²) pairwise check via :meth:`is_conflicting`.
        Returns each conflict pair only once (a, b) where a precedes b in the
        input list.

        Parameters
        ----------
        moves:
            List of candidate moves.

        Returns
        -------
        list[tuple[AdmissibleMove, AdmissibleMove]]
            All conflicting pairs.
        """
        conflicts: list[tuple[AdmissibleMove, AdmissibleMove]] = []
        for i in range(len(moves)):
            for j in range(i + 1, len(moves)):
                if self.is_conflicting(moves[i], moves[j]):
                    conflicts.append((moves[i], moves[j]))
        return conflicts

    def resolve(
        self,
        conflicts: list[tuple[AdmissibleMove, AdmissibleMove]],
    ) -> list[AdmissibleMove]:
        """Return the set of moves that *lose* in each conflict pair.

        The returned list is the collection of moves that should be **removed**
        from the candidate set.  The caller is expected to subtract this from
        the full move list.

        Parameters
        ----------
        conflicts:
            List of conflicting pairs from :meth:`detect_conflicts`.

        Returns
        -------
        list[AdmissibleMove]
            Moves that lost conflict resolution; should be excluded.
        """
        losers: set[str] = set()
        for a, b in conflicts:
            if a.move_id in losers:
                continue
            if b.move_id in losers:
                continue
            if self.resolution_strategy == "lowest_cost":
                loser = self._resolve_by_cost(a, b)
            else:
                loser = self._resolve_by_priority(a, b)
            losers.add(loser.move_id)
        loser_ids = {m.move_id for m in [b for _, b in conflicts] + [a for a, _ in conflicts]
                     if m.move_id in losers}
        seen_pairs: list[AdmissibleMove] = []
        for a, b in conflicts:
            for m in (a, b):
                if m.move_id in losers and m.move_id not in {x.move_id for x in seen_pairs}:
                    seen_pairs.append(m)
        return seen_pairs

    def is_conflicting(
        self,
        a: AdmissibleMove,
        b: AdmissibleMove,
    ) -> bool:
        """Determine whether moves *a* and *b* conflict.

        Two moves conflict if:

        *   They share at least one postcondition token (indicating they both
            modify the same target resource), OR
        *   One move's postcondition appears in the other's precondition
            (indicating execution order dependency that cannot be parallelised).

        Parameters
        ----------
        a:
            First move.
        b:
            Second move.

        Returns
        -------
        bool
        """
        posts_a = set(getattr(a, "postconditions", []))
        posts_b = set(getattr(b, "postconditions", []))
        pres_a = set(getattr(a, "preconditions", []))
        pres_b = set(getattr(b, "preconditions", []))
        if posts_a & posts_b:
            return True
        if posts_a & pres_b:
            return True
        if posts_b & pres_a:
            return True
        return False

    def _resolve_by_priority(
        self,
        a: AdmissibleMove,
        b: AdmissibleMove,
    ) -> AdmissibleMove:
        """Return the lower-priority move (the loser).

        Parameters
        ----------
        a:
            First conflicting move.
        b:
            Second conflicting move.

        Returns
        -------
        AdmissibleMove
            The move with the lower ``priority`` field; in case of a tie,
            returns *b* (first-registered wins).
        """
        prio_a = getattr(a, "priority", 0)
        prio_b = getattr(b, "priority", 0)
        return b if prio_a >= prio_b else a

    def _resolve_by_cost(
        self,
        a: AdmissibleMove,
        b: AdmissibleMove,
    ) -> AdmissibleMove:
        """Return the higher-cost move (the loser).

        Parameters
        ----------
        a:
            First conflicting move.
        b:
            Second conflicting move.

        Returns
        -------
        AdmissibleMove
            The move with the higher ``cost`` field; in case of a tie,
            returns *b*.
        """
        cost_a = getattr(a, "cost", 0.0)
        cost_b = getattr(b, "cost", 0.0)
        return b if cost_a <= cost_b else a


# ── MoveApplicationEngine ────────────────────────────────────────────────────


@dataclass(slots=True)
class MoveApplicationEngine:
    """Applies moves to states and collects postcondition verification results.

    The engine is the execution stage of the move-selection pipeline.  It
    wraps :meth:`AdmissibleMove.apply` with optional pre-application
    validation and mandatory post-application verification.

    Parameters
    ----------
    validator:
        Optional :class:`StateValidator`; if set, the resulting state is
        validated after application.  ``None`` disables post-application
        validation.
    verifier:
        :class:`PostconditionVerifier` used to check postconditions and
        compute realised gain.
    dry_run:
        If ``True``, the engine simulates moves (via :meth:`simulate`) but
        does not record any side-effects.  Useful for lookahead search
        (theory2.tex §44.3 – lookahead control law).

    References
    ----------
    theory2.tex §44.3 – Control Laws (lookahead).
    theory2.tex §44.4 – Postcondition Verification.
    """

    validator: StateValidator | None
    verifier: PostconditionVerifier
    dry_run: bool = False

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def apply(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> tuple[SemanticControlState, PostconditionResult]:
        """Apply *move* to *state* and return the new state with verification result.

        Steps:

        1.  Call ``move.apply(state)`` to produce the new state.
        2.  If ``self.validator`` is set, validate the new state; log any
            violations but do not block the transition (the caller decides
            what to do with violations).
        3.  Call ``self.verifier.verify(move, state, new_state)``.
        4.  Return ``(new_state, result)``.

        If ``self.dry_run`` is ``True``, the apply is delegated to
        :meth:`simulate` and no side-effects are recorded.

        Parameters
        ----------
        move:
            The move to apply.
        state:
            The current semantic control state.

        Returns
        -------
        tuple[SemanticControlState, PostconditionResult]
            ``(new_state, postcondition_result)``
        """
        if self.dry_run:
            new_state = self.simulate(move, state)
        else:
            try:
                new_state = move.apply(state)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "MoveApplicationEngine: move %s raised during apply: %s",
                    move.move_id[:8],
                    exc,
                )
                result = PostconditionResult(
                    move_id=move.move_id,
                    satisfied=False,
                    violations=(f"apply() raised: {exc}",),
                    gain=0.0,
                    checked_at=time.time(),
                )
                return state, result
        if self.validator is not None:
            violations = self.validator.validate(new_state)
            if violations:
                LOGGER.warning(
                    "MoveApplicationEngine: post-apply validation violations for %s: %s",
                    move.move_id[:8],
                    violations[:3],
                )
        result = self.verifier.verify(move, state, new_state)
        return new_state, result

    def apply_sequence(
        self,
        moves: list[AdmissibleMove],
        initial_state: SemanticControlState,
    ) -> tuple[SemanticControlState, list[PostconditionResult]]:
        """Apply a sequence of moves, threading state through each application.

        Parameters
        ----------
        moves:
            Ordered list of moves to apply.
        initial_state:
            Starting state.

        Returns
        -------
        tuple[SemanticControlState, list[PostconditionResult]]
            ``(final_state, [result_for_move_0, result_for_move_1, ...])``.
            If a move fails during application (raises), the sequence stops
            and the accumulated results are returned.
        """
        current = initial_state
        results: list[PostconditionResult] = []
        for move in moves:
            current, result = self.apply(move, current)
            results.append(result)
            if not result.satisfied:
                LOGGER.warning(
                    "MoveApplicationEngine: sequence halted at move %s (postcondition failed)",
                    move.move_id[:8],
                )
                break
        return current, results

    def simulate(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> SemanticControlState:
        """Simulate the effect of *move* on *state* without recording side-effects.

        Delegates to ``move.apply(state)`` but does not publish events or
        update any external state.  Used by lookahead search and dry-run mode.

        Parameters
        ----------
        move:
            The move to simulate.
        state:
            The current state.

        Returns
        -------
        SemanticControlState
            The hypothetical next state.
        """
        try:
            return move.apply(state)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "MoveApplicationEngine.simulate: move %s raised: %s",
                move.move_id[:8],
                exc,
            )
            return state

    def rollback(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
    ) -> SemanticControlState | None:
        """Attempt to invert the effect of *move* on *state*.

        Rollback is only possible if the move metadata contains a
        ``"pre_state_id"`` key referencing a reachable state.  Since this
        engine does not maintain a full state store, it returns ``None`` when
        rollback is not directly possible (the caller should use
        :meth:`~state_management.StateManager.rollback` instead for
        full rollback support).

        Parameters
        ----------
        state:
            The state after *move* was applied.
        move:
            The move to undo.

        Returns
        -------
        SemanticControlState | None
            The rolled-back state, or ``None`` if rollback is not available.
        """
        pre_id = getattr(move, "metadata", {}).get("pre_state_id")
        if pre_id is None:
            LOGGER.debug(
                "MoveApplicationEngine.rollback: no pre_state_id in move %s metadata",
                move.move_id[:8],
            )
            return None
        LOGGER.warning(
            "MoveApplicationEngine.rollback: pre_state_id found but no state store "
            "available; use StateManager.rollback() for full rollback support."
        )
        return None


# ── MoveSelector ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class MoveSelector:
    """Top-level façade wiring the full move-selection pipeline.

    Implements the control step described in theory2.tex §44.3:

    1.  :class:`MoveEnumerator` – enumerate applicable moves from registry.
    2.  :class:`PreconditionChecker` – filter to those with satisfied
        preconditions.
    3.  :class:`MoveConflictResolver` – remove conflicting losers.
    4.  :class:`MovePrioritizer` – sort by score.
    5.  :class:`ControlLaw` (optional) – apply the control law for final
        selection.

    Parameters
    ----------
    enumerator:
        :class:`MoveEnumerator` for step 1.
    checker:
        :class:`PreconditionChecker` for step 2.
    prioritizer:
        :class:`MovePrioritizer` for step 4.
    resolver:
        :class:`MoveConflictResolver` for step 3.
    law:
        Optional :class:`ControlLaw`; if set, used in step 5.  If ``None``,
        the top-scored move from step 4 is returned directly.

    References
    ----------
    theory2.tex §44.3 – Control Laws and Priority Strategies.
    """

    enumerator: MoveEnumerator
    checker: PreconditionChecker
    prioritizer: MovePrioritizer
    resolver: MoveConflictResolver
    law: ControlLaw | None = None

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def select(
        self,
        state: SemanticControlState,
        registry: list[AdmissibleMove],
    ) -> AdmissibleMove | None:
        """Run the full selection pipeline and return the best single move.

        Returns ``None`` if no applicable move is found after all filters.

        Parameters
        ----------
        state:
            Current semantic control state.
        registry:
            Full move registry (may be empty; :class:`MoveEnumerator` handles
            that case with default moves).

        Returns
        -------
        AdmissibleMove | None
            The selected move, or ``None`` if no move is applicable.
        """
        candidates = self._pipeline(state, registry)
        if not candidates:
            return None
        if self.law is not None:
            return self.law.select_move(candidates, state)
        return candidates[0]

    def select_k(
        self,
        state: SemanticControlState,
        registry: list[AdmissibleMove],
        k: int,
    ) -> list[AdmissibleMove]:
        """Run the pipeline and return the top-*k* moves.

        Parameters
        ----------
        state:
            Current semantic control state.
        registry:
            Full move registry.
        k:
            Maximum number of moves to return.

        Returns
        -------
        list[AdmissibleMove]
            Up to *k* moves in descending priority order.
        """
        candidates = self._pipeline(state, registry)
        return candidates[:k]

    def explain(
        self,
        state: SemanticControlState,
        registry: list[AdmissibleMove],
    ) -> dict[str, Any]:
        """Return a diagnostic dictionary describing the selection process.

        Runs the full pipeline with diagnostics captured at each stage and
        returns a structured dict suitable for CLI dashboard rendering or
        structured logging.

        Fields
        ------
        ``state_id``
            ID of the current state.
        ``registry_size``
            Total moves in the registry.
        ``enumerated``
            Moves passing the enumerator filter.
        ``precondition_satisfied``
            Moves with satisfied preconditions.
        ``precondition_violations``
            Dict of ``move_id → [violations]`` for failing preconditions.
        ``conflicts_detected``
            List of ``[move_id_a, move_id_b]`` pairs that conflict.
        ``conflict_losers``
            Move IDs eliminated by conflict resolution.
        ``candidates``
            Final candidate move IDs (post-resolution, prioritised).
        ``selected``
            The selected move ID, or ``null``.
        ``law``
            Control law name if set, else ``"none"``.
        ``attainability_score``
            Current state attainability score.

        Parameters
        ----------
        state:
            Current semantic control state.
        registry:
            Full move registry.

        Returns
        -------
        dict[str, Any]
            Full diagnostic dictionary.
        """
        self.checker.clear_cache()
        enumerated = self.enumerator.enumerate(state, registry)
        precondition_results = self.checker.check_all(enumerated, state)
        satisfied = [
            m for m, r in zip(enumerated, precondition_results) if r.satisfied
        ]
        violations_map = {
            r.move_id: list(r.violations)
            for r in precondition_results
            if not r.satisfied
        }
        conflicts = self.resolver.detect_conflicts(satisfied)
        losers = self.resolver.resolve(conflicts)
        loser_ids = {m.move_id for m in losers}
        post_conflict = [m for m in satisfied if m.move_id not in loser_ids]
        candidates = self.prioritizer.prioritize(post_conflict, state)
        selected = self.select(state, registry)
        return {
            "state_id": state.state_id,
            "registry_size": len(registry),
            "enumerated": [m.move_id for m in enumerated],
            "precondition_satisfied": [m.move_id for m in satisfied],
            "precondition_violations": violations_map,
            "conflicts_detected": [[a.move_id, b.move_id] for a, b in conflicts],
            "conflict_losers": list(loser_ids),
            "candidates": [m.move_id for m in candidates],
            "selected": selected.move_id if selected is not None else None,
            "law": self.law.name if self.law is not None else "none",
            "attainability_score": state.attainability_score(),
        }

    def set_law(self, law: ControlLaw) -> None:
        """Replace the current control law with *law*.

        Parameters
        ----------
        law:
            The new :class:`ControlLaw` to use for final move selection.
        """
        self.law = law
        LOGGER.info(
            "MoveSelector: control law set to '%s' (%s)",
            law.name,
            law.kind.value if hasattr(law.kind, "value") else str(law.kind),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pipeline(
        self,
        state: SemanticControlState,
        registry: list[AdmissibleMove],
    ) -> list[AdmissibleMove]:
        """Run all pipeline stages and return the sorted candidate list.

        Parameters
        ----------
        state:
            Current state.
        registry:
            Full move registry.

        Returns
        -------
        list[AdmissibleMove]
            Sorted candidates after enumeration, precondition checking,
            conflict resolution, and prioritisation.
        """
        self.checker.clear_cache()
        enumerated = self.enumerator.enumerate(state, registry)
        satisfied: list[AdmissibleMove] = []
        for move, result in zip(enumerated, self.checker.check_all(enumerated, state)):
            if result.satisfied:
                satisfied.append(move)
        conflicts = self.resolver.detect_conflicts(satisfied)
        loser_ids = {m.move_id for m in self.resolver.resolve(conflicts)}
        post_conflict = [m for m in satisfied if m.move_id not in loser_ids]
        return self.prioritizer.prioritize(post_conflict, state)


# ── Factory helpers ──────────────────────────────────────────────────────────


def make_default_move_selector(
    max_moves: int = DEFAULT_MAX_MOVES,
    strategy: str = DEFAULT_PRIORITIZER_STRATEGY,
    strict_preconditions: bool = True,
) -> MoveSelector:
    """Construct a :class:`MoveSelector` with all-default sub-components.

    Parameters
    ----------
    max_moves:
        Passed to :class:`MoveEnumerator`.
    strategy:
        Prioritisation strategy passed to :class:`MovePrioritizer`.
    strict_preconditions:
        Passed to :class:`PreconditionChecker`.

    Returns
    -------
    MoveSelector
        Ready-to-use selector with no control law set.
    """
    return MoveSelector(
        enumerator=MoveEnumerator(max_moves=max_moves, filter_inapplicable=True),
        checker=PreconditionChecker(strict=strict_preconditions),
        prioritizer=MovePrioritizer(strategy=strategy),
        resolver=MoveConflictResolver(),
        law=None,
    )


def make_application_engine(
    dry_run: bool = False,
    verifier_tolerance: float = 0.01,
) -> MoveApplicationEngine:
    """Construct a :class:`MoveApplicationEngine` with default components.

    Parameters
    ----------
    dry_run:
        If ``True``, engine simulates rather than applies moves.
    verifier_tolerance:
        Gain tolerance passed to :class:`PostconditionVerifier`.

    Returns
    -------
    MoveApplicationEngine
    """
    return MoveApplicationEngine(
        validator=None,
        verifier=PostconditionVerifier(tolerance=verifier_tolerance),
        dry_run=dry_run,
    )
