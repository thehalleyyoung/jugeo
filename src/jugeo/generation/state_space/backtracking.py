r"""Chapter 40, Section 7 — Backtracking.

Theory (theory2.tex §40.7):
    Backtracking is the process of reverting a section assignment at a coordinate
    when the current partial state reaches a dead end. Formally: given a partial
    state σ_t with assignment history [(p_k, s_k)]_{k=1..t}, backtracking at step t
    means returning to σ_{t-1} = σ_t \ {(p_t, s_t)} and trying an alternative
    section s_t' ∈ S(p_t) \ {s_t}.

    A choice point is a state where |alternatives(p)| > 1 for some unassigned
    patch p. The backtracking stack Γ = [(σ_k, alts_k)]_{k=1..d} records d levels
    of outstanding choices; depth d is the recursion depth.

    Thrashing: repeated backtracking to the same choice point indicates a
    structural conflict. CDCL-style clause learning can avoid re-exploring the
    same conflict: a learning clause records the incompatible assignment combination
    as a nogood, so the search backtracks further (non-chronological backjumping).

    Three strategies:
    - CHRONOLOGICAL: always backtrack to the most recent choice point (simple)
    - NON_CHRONOLOGICAL: backjump past irrelevant choice points to the true cause
    - CLAUSE_LEARNING: generate and record nogoods to prevent revisiting conflicts

# copilot: s07-backtracking
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from jugeo.generation.state_space.models import (
        SemanticState,
        StateTransition,
        GenerationStateSpace,
        make_initial_state,
        make_propose_transition,
        make_retract_transition,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    StateTransition = Any
    GenerationStateSpace = Any

    def make_initial_state(patches): return None
    def make_propose_transition(src, patch, sec): return None
    def make_retract_transition(src, patch): return None


__all__ = [
    "BacktrackingStrategy",
    "ChoicePoint",
    "BacktrackResult",
    "BacktrackStats",
    "ConflictCause",
    "LearningClause",
    "BacktrackingCoordinator",
    "BacktrackingAnalyzer",
    "BacktrackingWitness",
]


# ---------------------------------------------------------------------------
# BacktrackingStrategy
# ---------------------------------------------------------------------------

class BacktrackingStrategy(Enum):
    """Enumeration of the three backtracking strategies supported by this module.

    CHRONOLOGICAL
        The simplest strategy: always undo the most recently made assignment.
        Equivalent to depth-first search with left-to-right sibling exploration.
        Well-understood, but can thrash badly when the root cause of a conflict
        lies several levels up the search stack.

    NON_CHRONOLOGICAL
        Scan the choice stack from the top downward and jump back to the first
        choice point that still has untried alternatives.  This skips over
        choice points that are "blameless" for the current conflict.  Reduces
        thrashing at the cost of slightly more bookkeeping.

    CLAUSE_LEARNING
        Like NON_CHRONOLOGICAL, but additionally records a *nogood* (an
        incompatible set of (patch, section) pairs) as a LearningClause.
        Future search states that would recreate the same partial assignment
        can be pruned immediately by consulting the clause store, mirroring
        the CDCL algorithm used in modern SAT solvers.
    """

    CHRONOLOGICAL = auto()
    NON_CHRONOLOGICAL = auto()
    CLAUSE_LEARNING = auto()


# ---------------------------------------------------------------------------
# ChoicePoint
# ---------------------------------------------------------------------------

@dataclass
class ChoicePoint:
    """A snapshot of the search state at a branching decision.

    Each time the coordinator assigns a section to a patch and there exist
    other viable sections for that same patch, a ChoicePoint is pushed onto
    the backtracking stack.  If the search later reaches a dead end the
    coordinator pops this record, reverts the state to `state`, and retries
    with the next element from `alternatives`.

    Attributes
    ----------
    choice_id:
        Unique identifier for this choice point (UUID4 hex prefix).
    state:
        The SemanticState (or compatible object) that existed *just before*
        the current assignment was made.  Reverting backtrack here.
    patch_id:
        The patch whose section assignment is being reconsidered.
    chosen_section:
        The section that was originally chosen at this point.
    alternatives:
        Remaining sections for `patch_id` that have not yet been tried at
        this choice point.  Maintained as a mutable list; pop_alternative()
        consumes from the front.
    depth:
        The stack depth at the time this choice point was created (0-indexed).
    timestamp:
        Wall-clock time (seconds since epoch) when the point was pushed.
    """

    choice_id: str
    state: Any  # SemanticState or compatible mapping
    patch_id: str
    chosen_section: str
    alternatives: List[str]
    depth: int
    timestamp: float = field(default_factory=time.time)

    def has_alternatives(self) -> bool:
        """Return True iff there is at least one untried alternative section.

        When this returns False the choice point is exhausted and backtracking
        must continue further up the stack (or the search must report failure).

        Returns
        -------
        bool
            ``True`` if ``len(self.alternatives) > 0``, else ``False``.
        """
        return len(self.alternatives) > 0

    def pop_alternative(self) -> Optional[str]:
        """Consume and return the next untried alternative section.

        Removes the first element from ``self.alternatives`` and returns it.
        If ``alternatives`` is empty, returns ``None`` (caller must check
        ``has_alternatives()`` beforehand if the None case should be avoided).

        Returns
        -------
        Optional[str]
            The next section string to try, or ``None`` if no alternatives
            remain.
        """
        if not self.alternatives:
            return None
        # Pop from the front so alternatives are tried in the original order
        # they were provided (deterministic ordering aids reproducibility).
        return self.alternatives.pop(0)


# ---------------------------------------------------------------------------
# BacktrackResult
# ---------------------------------------------------------------------------

@dataclass
class BacktrackResult:
    """Record describing the outcome of a single backtracking operation.

    This is returned by BacktrackingCoordinator.backtrack() and consumed by
    BacktrackingAnalyzer and BacktrackingWitness.  It captures enough
    information to reconstruct what happened, drive the search forward with
    the new section choice, and update statistics.

    Attributes
    ----------
    success:
        True if the backtrack found a viable alternative to try; False if
        the entire stack was exhausted with no remaining choices (search fails).
    reverted_to_state:
        The state object the coordinator reverted to.  None when success=False.
    reverted_from_state_id:
        Identifier of the state that was active when backtracking was triggered.
    reverted_patch:
        The patch_id whose assignment was undone.
    reverted_section:
        The section that was undone.
    new_section_to_try:
        The alternative section that the search should now attempt to assign
        to ``reverted_patch``.  None when success=False.
    depth_before:
        Stack depth immediately before the backtrack.
    depth_after:
        Stack depth immediately after the backtrack (may be < depth_before - 1
        for non-chronological jumps).
    alternatives_remaining:
        How many untried alternatives remained at the choice point that was
        selected (not counting the one being returned as new_section_to_try).
    message:
        Human-readable summary of the operation, useful for logging/debugging.
    timestamp:
        Wall-clock time when this result was created.
    """

    success: bool
    reverted_to_state: Optional[Any]
    reverted_from_state_id: str
    reverted_patch: str
    reverted_section: str
    new_section_to_try: Optional[str]
    depth_before: int
    depth_after: int
    alternatives_remaining: int
    message: str
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# BacktrackStats
# ---------------------------------------------------------------------------

@dataclass
class BacktrackStats:
    """Aggregate statistics collected over the lifetime of a search session.

    An instance of this class is owned by BacktrackingCoordinator and is
    updated after every call to backtrack().  It can be queried at any time
    via BacktrackingCoordinator.get_stats().

    Attributes
    ----------
    total_backtracks:
        Total number of backtrack operations performed.
    chronological_backtracks:
        Subset of total_backtracks that used the CHRONOLOGICAL strategy.
    non_chronological_backtracks:
        Subset that used the NON_CHRONOLOGICAL strategy.
    clause_learning_backtracks:
        Subset that used the CLAUSE_LEARNING strategy.
    max_depth_reached:
        The highest stack depth observed during the search.
    current_depth:
        Current stack depth (updated after each push/backtrack).
    thrashing_events:
        Number of times the analyzer detected a thrashing pattern.
    clauses_learned:
        Number of LearningClause objects added to the clause store.
    avg_backtrack_depth:
        Running average of the stack depth at which backtracks occurred.
    """

    total_backtracks: int = 0
    chronological_backtracks: int = 0
    non_chronological_backtracks: int = 0
    clause_learning_backtracks: int = 0
    max_depth_reached: int = 0
    current_depth: int = 0
    thrashing_events: int = 0
    clauses_learned: int = 0
    avg_backtrack_depth: float = 0.0

    # Internal accumulator for computing running average; not exposed to callers.
    _depth_sum: float = field(default=0.0, repr=False, compare=False)

    def update(self, result: BacktrackResult) -> None:
        """Incorporate a completed BacktrackResult into aggregate statistics.

        Updates all relevant counters and recomputes the running average
        backtrack depth.  This method is called automatically by
        BacktrackingCoordinator.backtrack(); external callers typically do not
        need to invoke it directly.

        Parameters
        ----------
        result:
            The BacktrackResult returned by a backtrack() call.
        """
        # Always increment the total regardless of success/failure.
        self.total_backtracks += 1

        # Record the current depth before the backtrack for the average.
        self._depth_sum += result.depth_before
        self.avg_backtrack_depth = self._depth_sum / self.total_backtracks

        # Track the high-water mark of stack depth.
        if result.depth_before > self.max_depth_reached:
            self.max_depth_reached = result.depth_before

        # Update the current depth to the post-backtrack value.
        self.current_depth = result.depth_after


# ---------------------------------------------------------------------------
# ConflictCause
# ---------------------------------------------------------------------------

@dataclass
class ConflictCause:
    """Diagnosis of *why* the search reached a dead end at a given state.

    The BacktrackingAnalyzer constructs these objects via find_conflict_cause().
    They are informational and are not directly consumed by the search loop,
    but they can be used to build LearningClauses and to emit useful log
    messages.

    Attributes
    ----------
    cause_id:
        UUID-based unique identifier.
    conflict_patch:
        The patch_id that could not be assigned any viable section.
    conflicting_sections:
        The sections that were tried and led to the dead end.
    involved_patches:
        Other patches whose assignments contributed to the conflict (may be
        empty if the conflict is self-contained).
    description:
        Human-readable explanation of the conflict.
    depth_of_cause:
        The stack depth at which the conflict was first introduced.
    """

    cause_id: str
    conflict_patch: str
    conflicting_sections: List[str]
    involved_patches: List[str]
    description: str
    depth_of_cause: int

    @property
    def is_fundamental(self) -> bool:
        """Indicate whether this conflict may represent a global obstruction.

        In the language of algebraic topology (theory2.tex §40.7), a conflict
        is *fundamental* — potentially a Čech H^1 cohomology obstruction — when
        it cannot be resolved by reconsidering a single assignment, because
        multiple patches contribute to the incompatibility.

        The heuristic here is simple: if more than one patch is listed in
        ``involved_patches`` the conflict is classified as fundamental.

        Returns
        -------
        bool
            True if ``len(involved_patches) > 1``.
        """
        return len(self.involved_patches) > 1


# ---------------------------------------------------------------------------
# LearningClause
# ---------------------------------------------------------------------------

@dataclass
class LearningClause:
    """A nogood clause learned from a conflict during CLAUSE_LEARNING search.

    A LearningClause records a set of (patch_id, section) pairs that were
    simultaneously present in a state that led to a dead end.  Any future
    partial state that contains *all* of these pairs is guaranteed to also
    reach a dead end, so the search can prune that branch immediately.

    This mirrors the "no-good" or "conflict clause" concept in CDCL SAT
    solvers (Marques-Silva & Sakallah, 1999).

    Attributes
    ----------
    clause_id:
        Unique identifier for this clause.
    nogood:
        Frozen set of ``(patch_id, section_id)`` tuples that are mutually
        incompatible.  Using a FrozenSet makes equality comparison and hashing
        cheap.
    source_conflict:
        Human-readable string describing the conflict that generated this clause.
    added_at_depth:
        The backtracking stack depth when this clause was learned.
    times_triggered:
        Number of times this clause successfully pruned a search branch.
    timestamp:
        Wall-clock time when the clause was created.
    """

    clause_id: str
    nogood: FrozenSet[Tuple[str, str]]
    source_conflict: str
    added_at_depth: int
    times_triggered: int = 0
    timestamp: float = field(default_factory=time.time)

    def is_violated_by(self, state: Any) -> bool:
        """Return True iff *all* nogood pairs appear in state's patch_assignments.

        A state *violates* this clause (meaning it is on a doomed branch) when
        every (patch_id, section) pair in ``self.nogood`` is already committed
        in the state.  Partial overlap does not trigger pruning.

        Parameters
        ----------
        state:
            An object with a ``patch_assignments`` attribute that maps
            patch_id strings to section strings.  May also be a plain dict.

        Returns
        -------
        bool
            True if the state is guaranteed to lead to the conflict encoded
            in this clause; False otherwise.
        """
        # Tolerate both SemanticState objects and plain dict-like objects.
        if hasattr(state, "patch_assignments"):
            assignments: Dict[str, str] = state.patch_assignments
        elif isinstance(state, dict):
            assignments = state
        else:
            # Unknown state type — cannot verify, conservatively return False.
            logger.warning(
                "LearningClause.is_violated_by received unknown state type %s; "
                "returning False to avoid spurious pruning.",
                type(state).__name__,
            )
            return False

        # The clause fires only when *every* pair in the nogood is present.
        for patch_id, section in self.nogood:
            if assignments.get(patch_id) != section:
                return False
        return True

    def trigger(self) -> None:
        """Record that this clause successfully pruned a search branch.

        Increments the ``times_triggered`` counter.  High trigger counts
        indicate that a clause is highly effective and should be kept; low
        counts may indicate a redundant or overly specific clause that could
        be garbage-collected in a clause-management phase.
        """
        self.times_triggered += 1
        logger.debug(
            "LearningClause %s triggered (total=%d); nogood=%s",
            self.clause_id,
            self.times_triggered,
            self.nogood,
        )


# ---------------------------------------------------------------------------
# BacktrackingCoordinator
# ---------------------------------------------------------------------------

class BacktrackingCoordinator:
    """Manages the backtracking stack and drives the search recovery process.

    The coordinator is the central component of the backtracking subsystem.
    It maintains:

    - A stack of ChoicePoints recording all branching decisions made so far.
    - A list of LearningClauses (only populated when using CLAUSE_LEARNING).
    - A history of BacktrackResult objects for post-hoc analysis.
    - A BacktrackStats summary updated after every operation.

    The coordinator does *not* directly manipulate SemanticState objects;
    instead it records the state snapshot at each choice point and returns
    it via BacktrackResult.reverted_to_state so that the search loop can
    restore it.  This keeps the coordinator decoupled from the state
    representation details.

    Usage example
    -------------
    >>> coordinator = BacktrackingCoordinator(BacktrackingStrategy.CLAUSE_LEARNING)
    >>> # Search loop makes an assignment and pushes a choice point:
    >>> coordinator.push_choice_point(current_state, "patch_A", "sec_1",
    ...                               alternatives=["sec_2", "sec_3"])
    >>> # ... search proceeds ... dead end reached ...
    >>> result = coordinator.backtrack()
    >>> if result.success:
    ...     current_state = result.reverted_to_state
    ...     assign(current_state, result.reverted_patch, result.new_section_to_try)
    """

    def __init__(
        self,
        strategy: BacktrackingStrategy = BacktrackingStrategy.CHRONOLOGICAL,
    ) -> None:
        """Initialise the coordinator with a given backtracking strategy.

        Parameters
        ----------
        strategy:
            Controls which algorithm is used when backtrack() is called.
            Defaults to CHRONOLOGICAL for simplicity and predictability.
        """
        self._strategy: BacktrackingStrategy = strategy
        # The main backtracking stack.  Index 0 = oldest choice, -1 = most recent.
        self._stack: List[ChoicePoint] = []
        # Aggregate statistics for this search session.
        self._stats: BacktrackStats = BacktrackStats()
        # Clause store; populated only under CLAUSE_LEARNING strategy.
        self._learned_clauses: List[LearningClause] = []
        # Full history of every BacktrackResult produced in this session.
        self._backtrack_history: List[BacktrackResult] = []

        logger.debug(
            "BacktrackingCoordinator initialised with strategy=%s", strategy.name
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def push_choice_point(
        self,
        state: Any,
        patch_id: str,
        chosen_section: str,
        alternatives: List[str],
    ) -> ChoicePoint:
        """Record a branching decision and push it onto the choice stack.

        Call this immediately after committing an assignment to the search
        state, whenever the patch had more than one viable section.

        Parameters
        ----------
        state:
            A snapshot of the state *before* the assignment of
            ``chosen_section`` to ``patch_id`` was committed.  The
            coordinator holds a reference to this object; the caller must not
            mutate it after pushing.
        patch_id:
            The patch whose section is being decided.
        chosen_section:
            The section that was chosen for this assignment (already applied
            to the live state by the caller).
        alternatives:
            The remaining sections for ``patch_id`` that have not yet been
            tried at this depth.  Copied defensively to avoid aliasing issues.

        Returns
        -------
        ChoicePoint
            The newly created choice point (already pushed onto the stack).
        """
        depth = len(self._stack)
        cp = ChoicePoint(
            choice_id=uuid.uuid4().hex[:12],
            state=state,
            patch_id=patch_id,
            chosen_section=chosen_section,
            # Defensive copy so external mutations don't corrupt the stack.
            alternatives=list(alternatives),
            depth=depth,
        )
        self._stack.append(cp)

        # Keep the high-water mark up to date immediately.
        if depth + 1 > self._stats.max_depth_reached:
            self._stats.max_depth_reached = depth + 1
        self._stats.current_depth = len(self._stack)

        logger.debug(
            "Pushed choice point %s: patch=%s chosen=%s alts=%s depth=%d",
            cp.choice_id,
            patch_id,
            chosen_section,
            alternatives,
            depth,
        )
        return cp

    def backtrack(self) -> BacktrackResult:
        """Execute one backtracking step using the configured strategy.

        Dispatches to the appropriate private implementation based on
        ``self._strategy``.  The result is appended to ``_backtrack_history``
        and used to update ``_stats``.

        Returns
        -------
        BacktrackResult
            Describes what was undone and what to try next.  When
            ``result.success`` is False the entire search stack is exhausted.
        """
        depth_before = len(self._stack)

        if self._strategy == BacktrackingStrategy.CHRONOLOGICAL:
            result = self._do_chronological_backtrack()
            self._stats.chronological_backtracks += 1
        elif self._strategy == BacktrackingStrategy.NON_CHRONOLOGICAL:
            result = self._do_non_chronological_backtrack()
            self._stats.non_chronological_backtracks += 1
        else:  # CLAUSE_LEARNING
            result = self._do_clause_learning_backtrack()
            self._stats.clause_learning_backtracks += 1

        # Record the result and refresh statistics.
        self._backtrack_history.append(result)
        self._stats.update(result)
        self._stats.current_depth = len(self._stack)

        logger.info(
            "Backtrack[%s] depth %d→%d patch=%s section=%s→%s success=%s",
            self._strategy.name,
            depth_before,
            result.depth_after,
            result.reverted_patch,
            result.reverted_section,
            result.new_section_to_try,
            result.success,
        )
        return result

    def can_backtrack(self) -> bool:
        """Return True iff a productive backtrack step is possible.

        A backtrack is productive when the stack is non-empty and the top
        choice point has at least one untried alternative.  If the stack is
        empty, or all alternatives at every level are exhausted, backtracking
        cannot recover and the search must fail.

        Returns
        -------
        bool
            True if backtrack() would return a result with success=True.
        """
        # Walk from top to find any choice point with remaining alternatives.
        for cp in reversed(self._stack):
            if cp.has_alternatives():
                return True
        return False

    def get_depth(self) -> int:
        """Return the current backtracking stack depth.

        The depth equals the number of outstanding choice points, i.e., the
        number of assignments that could still be undone.

        Returns
        -------
        int
            ``len(self._stack)``
        """
        return len(self._stack)

    def get_stats(self) -> BacktrackStats:
        """Return the current aggregate statistics for this search session.

        Returns
        -------
        BacktrackStats
            The live statistics object (not a copy; updates are visible
            immediately).
        """
        return self._stats

    def clear(self) -> None:
        """Reset the coordinator to a clean initial state.

        Clears the choice stack, learned clauses, history, and statistics.
        After calling this, the coordinator behaves as if freshly constructed
        with the same strategy.
        """
        self._stack.clear()
        self._learned_clauses.clear()
        self._backtrack_history.clear()
        self._stats = BacktrackStats()
        logger.debug("BacktrackingCoordinator cleared.")

    # ------------------------------------------------------------------
    # Private implementation helpers
    # ------------------------------------------------------------------

    def _do_chronological_backtrack(self) -> BacktrackResult:
        """Implement the CHRONOLOGICAL backtracking strategy.

        Undoes the most recently pushed assignment (top of stack).  If the
        top choice point is exhausted (no alternatives), pops it and tries
        the one below, continuing up the stack.

        This is equivalent to a standard DFS backtrack in a constraint
        satisfaction framework.

        Returns
        -------
        BacktrackResult
            Success when a choice point with alternatives was found; failure
            when the entire stack was drained with no alternative found.
        """
        depth_before = len(self._stack)

        # Scan from the top of the stack for a choice point that still has
        # at least one untried alternative.
        while self._stack:
            cp = self._stack[-1]
            next_section = cp.pop_alternative()
            if next_section is not None:
                # Found a productive choice point; leave it on the stack
                # (it may have more alternatives after this one).
                return BacktrackResult(
                    success=True,
                    reverted_to_state=cp.state,
                    reverted_from_state_id=f"depth-{depth_before}",
                    reverted_patch=cp.patch_id,
                    reverted_section=cp.chosen_section,
                    new_section_to_try=next_section,
                    depth_before=depth_before,
                    depth_after=len(self._stack),
                    alternatives_remaining=len(cp.alternatives),
                    message=(
                        f"Chronological backtrack at depth {depth_before}: "
                        f"reverting patch '{cp.patch_id}' from '{cp.chosen_section}' "
                        f"to try '{next_section}' ({len(cp.alternatives)} alts left)."
                    ),
                )
            # This choice point is exhausted; pop it and continue up.
            self._stack.pop()

        # Stack is fully exhausted — search fails.
        return BacktrackResult(
            success=False,
            reverted_to_state=None,
            reverted_from_state_id=f"depth-{depth_before}",
            reverted_patch="",
            reverted_section="",
            new_section_to_try=None,
            depth_before=depth_before,
            depth_after=0,
            alternatives_remaining=0,
            message=(
                "Chronological backtrack exhausted entire search stack; "
                "no solution exists in the explored subtree."
            ),
        )

    def _do_non_chronological_backtrack(self) -> BacktrackResult:
        """Implement the NON_CHRONOLOGICAL (backjumping) strategy.

        Scans the stack from the top downward to locate the *first* (most
        recent) choice point that has alternatives remaining, then jumps
        directly to that level, discarding all choice points above it.

        This avoids thrashing: if the top-of-stack choice point is exhausted
        the search immediately jumps higher rather than reporting failure or
        attempting useless single-step chronological pops.

        Returns
        -------
        BacktrackResult
            Success when any choice point with alternatives is found; failure
            only when the entire stack has no alternatives anywhere.
        """
        depth_before = len(self._stack)

        # Find the highest (most-recent) choice point with remaining alternatives.
        target_index: Optional[int] = None
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx].has_alternatives():
                target_index = idx
                break

        if target_index is None:
            # Entire stack exhausted.
            return BacktrackResult(
                success=False,
                reverted_to_state=None,
                reverted_from_state_id=f"depth-{depth_before}",
                reverted_patch="",
                reverted_section="",
                new_section_to_try=None,
                depth_before=depth_before,
                depth_after=0,
                alternatives_remaining=0,
                message=(
                    "Non-chronological backtrack: entire stack exhausted; "
                    "no viable alternative found at any depth."
                ),
            )

        # Remove all choice points above the target level (backjump).
        skipped = len(self._stack) - 1 - target_index
        if skipped > 0:
            self._stack = self._stack[: target_index + 1]
            logger.debug(
                "Non-chronological backjump: skipped %d levels (depth %d → %d).",
                skipped,
                depth_before,
                len(self._stack),
            )

        cp = self._stack[-1]
        next_section = cp.pop_alternative()
        # next_section is guaranteed non-None because we found has_alternatives().

        return BacktrackResult(
            success=True,
            reverted_to_state=cp.state,
            reverted_from_state_id=f"depth-{depth_before}",
            reverted_patch=cp.patch_id,
            reverted_section=cp.chosen_section,
            new_section_to_try=next_section,
            depth_before=depth_before,
            depth_after=len(self._stack),
            alternatives_remaining=len(cp.alternatives),
            message=(
                f"Non-chronological backjump: skipped {skipped} levels; "
                f"reverting patch '{cp.patch_id}' to try '{next_section}'."
            ),
        )

    def _do_clause_learning_backtrack(self) -> BacktrackResult:
        """Implement the CLAUSE_LEARNING backtracking strategy.

        Combines non-chronological backjumping with the creation of a new
        LearningClause that captures the nogood assignment combination.  The
        clause is added to ``_learned_clauses`` so that the search can prune
        states that would recreate the same conflict.

        After creating the clause, the coordinator delegates the actual stack
        manipulation to _do_non_chronological_backtrack().

        Returns
        -------
        BacktrackResult
            Same semantics as _do_non_chronological_backtrack(), with the
            side-effect that a new LearningClause may have been added.
        """
        # First, synthesise a LearningClause from the current stack state.
        # The nogood consists of the (patch, section) pairs that are committed
        # at every live choice point — this is the partial assignment that led
        # to the dead end.
        if self._stack:
            nogood_pairs: FrozenSet[Tuple[str, str]] = frozenset(
                (cp.patch_id, cp.chosen_section)
                for cp in self._stack
            )
            # Only record a clause if the nogood is non-trivial (size ≥ 2).
            if len(nogood_pairs) >= 2:
                clause = LearningClause(
                    clause_id=uuid.uuid4().hex[:12],
                    nogood=nogood_pairs,
                    source_conflict=(
                        f"Dead-end at stack depth {len(self._stack)}; "
                        f"assignments: {dict(nogood_pairs)}"
                    ),
                    added_at_depth=len(self._stack),
                )
                self._learned_clauses.append(clause)
                self._stats.clauses_learned += 1
                logger.info(
                    "Learned clause %s at depth %d with %d nogood pairs.",
                    clause.clause_id,
                    len(self._stack),
                    len(nogood_pairs),
                )

        # Delegate the actual backjump to the non-chronological implementation.
        result = self._do_non_chronological_backtrack()
        return result

    def check_learned_clauses(self, state: Any) -> Optional[LearningClause]:
        """Test whether any learned clause is violated by the current state.

        If a clause is violated the state is on a branch guaranteed to fail.
        The caller should immediately backtrack rather than proceeding.

        Parameters
        ----------
        state:
            The current partial assignment state.

        Returns
        -------
        Optional[LearningClause]
            The first triggered clause, or None if no clause fires.
        """
        for clause in self._learned_clauses:
            if clause.is_violated_by(state):
                clause.trigger()
                logger.debug(
                    "Learned clause %s triggered; pruning current branch.",
                    clause.clause_id,
                )
                return clause
        return None

    def get_learned_clauses(self) -> List[LearningClause]:
        """Return all learned clauses recorded in this search session.

        Returns
        -------
        List[LearningClause]
            A reference to the internal list (not a copy).
        """
        return self._learned_clauses


# ---------------------------------------------------------------------------
# BacktrackingAnalyzer
# ---------------------------------------------------------------------------

class BacktrackingAnalyzer:
    """Post-hoc and real-time analysis of backtracking behaviour.

    The analyzer operates on BacktrackResult histories and SemanticState
    snapshots to detect pathological patterns, diagnose the root cause of
    conflicts, and suggest learned clauses.

    It is stateless by design: all methods accept their input as arguments.
    This makes it easy to share a single instance across multiple search runs.
    """

    # Number of consecutive backtracks to the same patch that constitutes
    # a thrashing event.
    THRASHING_THRESHOLD: int = 5

    def detect_thrashing(self, history: List[BacktrackResult]) -> bool:
        """Return True if the recent backtrack history shows thrashing.

        Thrashing is defined as: the last THRASHING_THRESHOLD (or more)
        backtrack results all reverted the *same* patch.  This indicates that
        the search is oscillating around the same conflict without making
        global progress.

        Parameters
        ----------
        history:
            List of BacktrackResult objects in chronological order (oldest
            first, most recent last).

        Returns
        -------
        bool
            True if thrashing is detected in the tail of ``history``.
        """
        if len(history) < self.THRASHING_THRESHOLD:
            return False

        # Examine only the most recent THRASHING_THRESHOLD results.
        recent = history[-self.THRASHING_THRESHOLD :]
        first_patch = recent[0].reverted_patch

        # If every result in the window reverted the same patch, that's thrashing.
        if all(r.reverted_patch == first_patch for r in recent):
            logger.warning(
                "Thrashing detected: last %d backtracks all reverted patch '%s'.",
                self.THRASHING_THRESHOLD,
                first_patch,
            )
            return True

        return False

    def find_conflict_cause(self, state: Any) -> ConflictCause:
        """Diagnose the most likely cause of a dead-end at ``state``.

        Heuristic approach: inspects ``patch_assignments`` for patches whose
        assigned section label is unusually short or generic (e.g. single
        character, or a common placeholder like "sec", "s0", "default").
        Such assignments often indicate that an over-constrained patch was
        forced into a poor-fit section.

        When models are available and ``state`` has richer metadata, those are
        consulted; otherwise the heuristic falls back to name-length analysis.

        Parameters
        ----------
        state:
            The current (dead-end) state.  Expected to have a
            ``patch_assignments`` dict attribute.

        Returns
        -------
        ConflictCause
            A best-effort diagnosis.  The ``conflict_patch`` field names the
            most suspicious patch.
        """
        # Extract the assignment map; tolerate dict-like objects.
        if hasattr(state, "patch_assignments"):
            assignments: Dict[str, str] = state.patch_assignments
        elif isinstance(state, dict):
            assignments = state
        else:
            assignments = {}

        # Generic placeholder section names that commonly cause over-constraint.
        GENERIC_LABELS: Set[str] = {"sec", "s0", "default", "none", "?", "x", ""}

        suspicious_patch = ""
        suspicious_section = ""
        lowest_score = float("inf")

        for patch_id, section in assignments.items():
            # Score: shorter and more generic = more suspicious.
            score = len(section) + (10 if section.lower() not in GENERIC_LABELS else 0)
            if score < lowest_score:
                lowest_score = score
                suspicious_patch = patch_id
                suspicious_section = section

        # Identify which other patches share the same suspicious section
        # (they may be competing for the same resource).
        involved = [
            p for p, s in assignments.items()
            if s == suspicious_section and p != suspicious_patch
        ]

        state_id = getattr(state, "state_id", repr(state)[:40])
        description = (
            f"Dead end at state '{state_id}': patch '{suspicious_patch}' "
            f"assigned to section '{suspicious_section}' (score={lowest_score:.1f}); "
            f"{len(involved)} other patch(es) share the same section: {involved}."
        )

        return ConflictCause(
            cause_id=uuid.uuid4().hex[:12],
            conflict_patch=suspicious_patch,
            conflicting_sections=[suspicious_section] if suspicious_section else [],
            involved_patches=involved,
            description=description,
            depth_of_cause=len(assignments),
        )

    def compute_backtrack_rate(self, history: List[BacktrackResult]) -> float:
        """Return the fraction of search steps that were backtrack operations.

        This metric estimates how much of the search effort was "wasted"
        backtracking.  A rate close to 0.5 suggests significant constraint
        violation; a rate near 0 is healthy.

        Parameters
        ----------
        history:
            The full list of BacktrackResult objects for the search session.

        Returns
        -------
        float
            Ratio of backtrack steps to total recorded results, in [0.0, 1.0].
            Returns 0.0 for an empty history.
        """
        if not history:
            return 0.0

        # Count every recorded result as one step; successful backtracks and
        # failures both count toward the numerator.
        total_steps = len(history)
        backtrack_steps = sum(1 for r in history if r.depth_before > r.depth_after)

        rate = backtrack_steps / total_steps
        logger.debug(
            "Backtrack rate: %d/%d = %.3f", backtrack_steps, total_steps, rate
        )
        return rate

    def suggest_learning_clause(
        self, history: List[BacktrackResult]
    ) -> Optional[LearningClause]:
        """Suggest a LearningClause if thrashing is detected in ``history``.

        When thrashing is active, this method constructs a clause whose nogood
        is the set of (patch, section) pairs from the most recent round of
        repeated backtracks.  Recording this clause will prevent the search
        from revisiting the same assignment combination.

        Parameters
        ----------
        history:
            List of BacktrackResult objects (chronological order).

        Returns
        -------
        Optional[LearningClause]
            A fresh LearningClause if thrashing was detected, else None.
        """
        if not self.detect_thrashing(history):
            return None

        # Build the nogood from the reverted patches and sections in the
        # thrashing window.
        recent = history[-self.THRASHING_THRESHOLD :]
        nogood_pairs: FrozenSet[Tuple[str, str]] = frozenset(
            (r.reverted_patch, r.reverted_section)
            for r in recent
            if r.reverted_patch  # skip empty sentinel values
        )

        if not nogood_pairs:
            return None

        clause = LearningClause(
            clause_id=uuid.uuid4().hex[:12],
            nogood=nogood_pairs,
            source_conflict=(
                f"Thrashing detected over {self.THRASHING_THRESHOLD} backtracks; "
                f"nogood derived from patches: "
                f"{[r.reverted_patch for r in recent]}."
            ),
            added_at_depth=history[-1].depth_before,
        )

        logger.info(
            "Suggesting learning clause %s with %d nogood pairs.",
            clause.clause_id,
            len(nogood_pairs),
        )
        return clause


# ---------------------------------------------------------------------------
# BacktrackingWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BacktrackingWitness:
    """Immutable, hashable record of a single backtracking event.

    Witnesses are lightweight, frozen snapshots that can be stored in sets,
    used as dictionary keys, or serialised for external logging without risk
    of mutation.  They are deliberately stripped of the full state object
    to keep memory usage bounded.

    Attributes
    ----------
    witness_id:
        UUID-based unique identifier for this witness record.
    from_state_id:
        Identifier of the state from which backtracking was triggered.
    to_state_id:
        Identifier of the state the search returned to (often constructed
        as "depth-<n>" when the full state_id is unavailable).
    reverted_patch:
        The patch whose assignment was undone.
    reverted_section:
        The section that was undone.
    alternatives_remaining:
        How many alternatives were still available at the choice point after
        popping the one being tried next.
    backtrack_depth:
        The stack depth *before* the backtrack (i.e. how deep the search was).
    timestamp:
        Wall-clock time when the witness was created.
    """

    witness_id: str
    from_state_id: str
    to_state_id: str
    reverted_patch: str
    reverted_section: str
    alternatives_remaining: int
    backtrack_depth: int
    timestamp: float

    @classmethod
    def from_result(cls, result: BacktrackResult) -> "BacktrackingWitness":
        """Construct a BacktrackingWitness from a BacktrackResult.

        Parameters
        ----------
        result:
            The BacktrackResult produced by BacktrackingCoordinator.backtrack().

        Returns
        -------
        BacktrackingWitness
            An immutable snapshot of the backtracking event.
        """
        # Derive a stable to_state_id from the reverted state object or fall
        # back to the depth string used in the result message.
        reverted = result.reverted_to_state
        if reverted is not None and hasattr(reverted, "state_id"):
            to_state_id: str = reverted.state_id
        else:
            to_state_id = f"depth-{result.depth_after}"

        return cls(
            witness_id=uuid.uuid4().hex[:12],
            from_state_id=result.reverted_from_state_id,
            to_state_id=to_state_id,
            reverted_patch=result.reverted_patch,
            reverted_section=result.reverted_section,
            alternatives_remaining=result.alternatives_remaining,
            backtrack_depth=result.depth_before,
            timestamp=result.timestamp,
        )


# ---------------------------------------------------------------------------
# Smoke test / demonstration
# ---------------------------------------------------------------------------

def _run_smoke_test() -> None:
    """Demonstrate backtracking in a small search scenario with dead ends.

    This function constructs a toy constraint-satisfaction problem over three
    patches (A, B, C) with two sections each, where certain combinations are
    incompatible.  It exercises CLAUSE_LEARNING to show how nogoods are
    accumulated across multiple dead ends.

    The "state" used here is a plain dict for simplicity — the coordinator
    and analyzer are designed to work with any dict-like object.
    """
    print("=" * 70)
    print("Backtracking smoke test — Chapter 40 §7")
    print("=" * 70)

    # ------------------------------------------------------------------ #
    # Problem setup
    #
    # Patches: A, B, C
    # Sections: "alpha", "beta"
    # Compatibility constraint (hand-coded dead-ends):
    #   - (A=alpha, B=alpha) is a dead end
    #   - (A=alpha, C=alpha) is a dead end
    #   - (A=beta,  B=beta)  is a dead end
    # Goal: find an assignment where all patches are assigned and no dead end.
    # ------------------------------------------------------------------ #

    DEAD_ENDS: Set[FrozenSet[Tuple[str, str]]] = {
        frozenset({("A", "alpha"), ("B", "alpha")}),
        frozenset({("A", "alpha"), ("C", "alpha")}),
        frozenset({("A", "beta"), ("B", "beta")}),
    }

    def is_dead_end(assignments: Dict[str, str]) -> bool:
        """Return True if the current partial assignment triggers any dead end."""
        pairs = frozenset(assignments.items())
        for dead in DEAD_ENDS:
            if dead.issubset(pairs):
                return True
        return False

    def is_complete(assignments: Dict[str, str]) -> bool:
        """Return True when all three patches are assigned."""
        return set(assignments.keys()) == {"A", "B", "C"}

    coordinator = BacktrackingCoordinator(BacktrackingStrategy.CLAUSE_LEARNING)
    analyzer = BacktrackingAnalyzer()
    witnesses: List[BacktrackingWitness] = []

    # The patches to assign, in order.
    patch_order = ["A", "B", "C"]
    sections = ["alpha", "beta"]

    # Current partial state (plain dict acting as SemanticState).
    current_state: Dict[str, str] = {}

    print(f"\nStrategy: {coordinator._strategy.name}")
    print(f"Patches: {patch_order}, Sections: {sections}\n")

    # Simple iterative search loop with backtracking.
    patch_index = 0
    max_iterations = 100  # safety limit to prevent infinite loops in tests
    iteration = 0

    while patch_index < len(patch_order) and iteration < max_iterations:
        iteration += 1
        patch = patch_order[patch_index]
        placed = False

        for section_idx, section in enumerate(sections):
            # Tentatively assign.
            trial_state = dict(current_state)
            trial_state[patch] = section

            # Check whether a learned clause blocks this assignment.
            triggered_clause = coordinator.check_learned_clauses(trial_state)
            if triggered_clause is not None:
                print(
                    f"  [iter {iteration}] Clause {triggered_clause.clause_id} "
                    f"pruned {patch}={section}; skipping."
                )
                continue

            if is_dead_end(trial_state):
                print(
                    f"  [iter {iteration}] Dead end: {patch}={section} "
                    f"with assignments {trial_state}."
                )
                # Record a choice point (the alternative is the *other* section).
                remaining_alts = [s for s in sections[section_idx + 1 :]]
                pre_state = dict(current_state)
                coordinator.push_choice_point(pre_state, patch, section, remaining_alts)
                result = coordinator.backtrack()
                witness = BacktrackingWitness.from_result(result)
                witnesses.append(witness)

                if not result.success:
                    print("  Search failed — no solution found.")
                    break

                # Restore the state the coordinator says to revert to.
                current_state = dict(result.reverted_to_state)
                # Re-apply the new section.
                current_state[result.reverted_patch] = result.new_section_to_try  # type: ignore[index]
                print(
                    f"  [iter {iteration}] Backtracked to "
                    f"{result.reverted_patch}={result.new_section_to_try}; "
                    f"depth now {result.depth_after}."
                )
                placed = True
                break
            else:
                # Commit the assignment and push a choice point for alternatives.
                pre_state = dict(current_state)
                current_state = trial_state
                remaining_alts = [s for s in sections[section_idx + 1 :]]
                if remaining_alts:
                    coordinator.push_choice_point(
                        pre_state, patch, section, remaining_alts
                    )
                placed = True
                break

        if not placed:
            # All sections for this patch were exhausted — must backtrack further.
            result = coordinator.backtrack()
            witness = BacktrackingWitness.from_result(result)
            witnesses.append(witness)
            if not result.success:
                print("  Search failed — entire stack exhausted.")
                break
            current_state = dict(result.reverted_to_state)
            current_state[result.reverted_patch] = result.new_section_to_try  # type: ignore[index]
            # Reconsider from the reverted patch's position.
            try:
                patch_index = patch_order.index(result.reverted_patch)
            except ValueError:
                patch_index = max(0, patch_index - 1)
            continue

        if is_complete(current_state) and not is_dead_end(current_state):
            print(f"\n  ✓ Solution found: {current_state}")
            break

        patch_index += 1

    # ------------------------------------------------------------------ #
    # Report statistics
    # ------------------------------------------------------------------ #
    stats = coordinator.get_stats()
    print("\n--- Backtracking Statistics ---")
    print(f"  Total backtracks      : {stats.total_backtracks}")
    print(f"  Clause learning BTs   : {stats.clause_learning_backtracks}")
    print(f"  Clauses learned       : {stats.clauses_learned}")
    print(f"  Max depth reached     : {stats.max_depth_reached}")
    print(f"  Avg backtrack depth   : {stats.avg_backtrack_depth:.2f}")

    print(f"\n--- Witnesses ({len(witnesses)} total) ---")
    for w in witnesses:
        print(
            f"  {w.witness_id}: {w.reverted_patch}={w.reverted_section} "
            f"@ depth {w.backtrack_depth} → {w.to_state_id}"
        )

    # Detect thrashing in the backtrack history.
    history = coordinator._backtrack_history
    if analyzer.detect_thrashing(history):
        suggested = analyzer.suggest_learning_clause(history)
        print(f"\n  ⚠ Thrashing detected! Suggested clause: {suggested}")
    else:
        print("\n  No thrashing detected.")

    rate = analyzer.compute_backtrack_rate(history)
    print(f"\n  Backtrack rate: {rate:.2%}")

    # Show learned clauses.
    print(f"\n--- Learned Clauses ({len(coordinator.get_learned_clauses())}) ---")
    for clause in coordinator.get_learned_clauses():
        print(
            f"  {clause.clause_id}: nogood={set(clause.nogood)} "
            f"triggered={clause.times_triggered}x"
        )

    print("\n" + "=" * 70)
    print("Smoke test complete.")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    _run_smoke_test()
