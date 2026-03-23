r"""Chapter 40, Section 4 — Pruning.

Theory (theory2.tex §40.4):
    Pruning eliminates states from the frontier that cannot lead to a goal.
    Three fundamental pruning principles apply to the jugeo state space:

    (1) Dominance pruning: σ1 dominates σ2 (written σ2 ≼ σ1) if σ1 is a
        refinement of σ2: dom(σ2) ⊆ dom(σ1) and ∀p ∈ dom(σ2): σ1(p) = σ2(p).
        A dominated state σ2 can be pruned if σ1 is already in the frontier or
        the visited set, since every extension of σ2 is reachable from σ1.

    (2) Obstruction pruning: a state σ has a fundamental obstruction if the
        Čech cohomology class [c] ∈ H^1(N(P), S) is non-trivial for the
        partial assignment. Such states can never extend to a GlobalSection
        regardless of future choices and should be pruned immediately.

    (3) Bound pruning (branch-and-bound): given a cost function g: Σ → ℝ and
        a lower-bound heuristic h: Σ → ℝ, if g(σ) + h(σ) ≥ best_known, then
        σ cannot improve on the best solution found so far.

# copilot: s04-pruning
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from jugeo.generation.state_space.models import (
        SemanticState,
        GenerationStateSpace,
        make_initial_state,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    GenerationStateSpace = Any

    def make_initial_state(patches): return None


__all__ = [
    "PruningRule",
    "DominancePruningRule",
    "ObstructionPruningRule",
    "BoundPruningRule",
    "PruneDecision",
    "DominanceResult",
    "PruningStats",
    "PruningAnalysis",
    "PruningCoordinator",
    "PruningAnalyzer",
    "PruningWitness",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PruneDecision:
    """Record of whether a pruning rule decided to prune a given state.

    Each time a :class:`PruningRule` is applied, it produces exactly one
    ``PruneDecision``.  The decision captures not only the binary yes/no
    outcome but also the rule that made the decision, a human-readable reason
    string, and a confidence score in [0, 1].  This makes it straightforward
    to audit the frontier-management policy, replay a search run, or analyse
    which rules are most effective.

    Attributes:
        should_prune: ``True`` if the state should be removed from the
            frontier; ``False`` if the rule does not request pruning.
        rule_name: Identifier string of the rule that produced this decision.
            Corresponds to :pymeth:`PruningRule.name`.
        reason: Free-form, human-readable description of why the rule decided
            as it did.  Should be concise enough to fit in a log line.
        confidence: A float in ``[0.0, 1.0]`` indicating the rule's
            confidence in its decision.  A value of 1.0 means the decision is
            mathematically certain (e.g. a proved dominance relationship); 0.5
            means the rule is essentially guessing.
        state_id: The ``state_id`` of the state being evaluated.  Stored here
            so that decisions can be cross-referenced with states even after
            the state object is no longer in scope.
        timestamp: Unix epoch time at which the decision was created.
            Defaults to the current time via :func:`time.time`.
    """

    should_prune: bool
    rule_name: str
    reason: str
    confidence: float
    state_id: str
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        """Validate confidence is within the expected range."""
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1]; got {self.confidence!r}"
            )


@dataclass
class DominanceResult:
    """Pairwise dominance relationship between two states.

    The dominance partial order is defined as follows: state ``s1`` dominates
    state ``s2`` (written ``s2 ≼ s1``) when the patch-assignment of ``s1`` is
    a *refinement* of ``s2`` — that is, ``dom(s2) ⊆ dom(s1)`` and
    ``s1(p) = s2(p)`` for every patch ``p`` in ``dom(s2)``.  Intuitively,
    ``s1`` is "at least as advanced" as ``s2`` without contradicting any
    decision already made in ``s2``.

    Attributes:
        s1_id: ``state_id`` of the first state in the comparison.
        s2_id: ``state_id`` of the second state in the comparison.
        s1_dominates_s2: ``True`` iff every assignment of ``s2`` is also
            present in ``s1`` with the same value.
        s2_dominates_s1: ``True`` iff every assignment of ``s1`` is also
            present in ``s2`` with the same value.
        incomparable: ``True`` when neither state dominates the other (the
            two states make conflicting or non-overlapping assignments that
            prevent a dominance relationship).
        shared_patch_count: Number of patches that appear in both
            ``patch_assignments`` dictionaries.
        s1_extra_patches: Number of patches assigned in ``s1`` but not in
            ``s2``.
        s2_extra_patches: Number of patches assigned in ``s2`` but not in
            ``s1``.
        conflict_count: Number of patches that are assigned in *both* states
            but with *different* values.  If this is > 0 then neither state
            can dominate the other and ``incomparable`` will be ``True``.
    """

    s1_id: str
    s2_id: str
    s1_dominates_s2: bool
    s2_dominates_s1: bool
    incomparable: bool
    shared_patch_count: int
    s1_extra_patches: int
    s2_extra_patches: int
    conflict_count: int


@dataclass
class PruningStats:
    """Running aggregate statistics for a :class:`PruningCoordinator`.

    Counters are updated incrementally each time a :class:`PruneDecision` is
    processed by :meth:`update`.  ``pruning_rate`` and ``avg_confidence`` are
    recomputed on every update so callers can read them directly without
    triggering extra computation.

    Attributes:
        total_evaluated: Total number of states that have been evaluated by
            at least one pruning rule.
        total_pruned: Number of states for which *at least one* rule returned
            ``should_prune=True``.
        pruned_by_dominance: States pruned specifically by the dominance rule.
        pruned_by_obstruction: States pruned specifically by the obstruction
            rule.
        pruned_by_bound: States pruned specifically by the bound rule.
        pruning_rate: ``total_pruned / total_evaluated`` (0.0 if no states
            have been evaluated yet).
        avg_confidence: Running mean of ``confidence`` over all pruning
            decisions where ``should_prune`` is ``True``.
        timestamp: Unix epoch time at which this stats snapshot was last
            updated.
    """

    total_evaluated: int = 0
    total_pruned: int = 0
    pruned_by_dominance: int = 0
    pruned_by_obstruction: int = 0
    pruned_by_bound: int = 0
    pruning_rate: float = 0.0
    avg_confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

    # Internal accumulator for incremental mean — not exposed publicly.
    _confidence_sum: float = field(default=0.0, repr=False, compare=False)

    def update(self, decision: PruneDecision) -> None:
        """Incorporate a new pruning decision into the running statistics.

        Increments ``total_evaluated`` unconditionally, then updates the
        per-rule counters, ``total_pruned``, ``pruning_rate``, and
        ``avg_confidence`` based on the decision's content.

        Args:
            decision: The :class:`PruneDecision` to incorporate.  Must have
                a valid ``rule_name`` and ``confidence``.

        Returns:
            ``None``.  All state is updated in-place.
        """
        self.total_evaluated += 1
        self.timestamp = time.time()

        if decision.should_prune:
            self.total_pruned += 1
            self._confidence_sum += decision.confidence

            # Attribute the prune to the correct per-rule counter.
            if decision.rule_name == "dominance":
                self.pruned_by_dominance += 1
            elif decision.rule_name == "obstruction":
                self.pruned_by_obstruction += 1
            elif decision.rule_name == "bound":
                self.pruned_by_bound += 1

        # Recompute derived fields.
        if self.total_evaluated > 0:
            self.pruning_rate = self.total_pruned / self.total_evaluated
        if self.total_pruned > 0:
            self.avg_confidence = self._confidence_sum / self.total_pruned


@dataclass
class PruningAnalysis:
    """High-level summary of a pruning analysis run.

    Produced by :meth:`PruningAnalyzer.analyze_pruning_history` after
    processing a list of :class:`PruneDecision` objects.  Intended for
    offline inspection, tuning, and reporting.

    Attributes:
        analysis_id: A UUID string uniquely identifying this analysis run.
        total_states_analyzed: Total number of states whose decisions appear
            in the input history.
        pruning_rate_history: Ordered list of per-epoch pruning rates (each
            entry is a float in [0, 1]).  Useful for plotting convergence.
        most_effective_rule: Name of the rule that pruned the most states.
        avg_confidence: Mean confidence across all prune decisions.
        dominance_chains: Number of distinct dominance chains identified in
            the history (approximated as the number of ``dominance`` prune
            decisions, since each corresponds to one chain link).
        obstruction_count: Number of states pruned due to obstructions.
        bound_violations: Number of states pruned due to bound violations.
        recommendations: Human-readable list of actionable suggestions derived
            from the analysis (e.g. "increase heuristic tightness").
    """

    analysis_id: str
    total_states_analyzed: int
    pruning_rate_history: List[float]
    most_effective_rule: str
    avg_confidence: float
    dominance_chains: int
    obstruction_count: int
    bound_violations: int
    recommendations: List[str]


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class PruningRule(ABC):
    """Abstract base for all pruning rules.

    Concrete subclasses must implement :meth:`apply` and :meth:`name`.
    The optional :meth:`priority` method controls the order in which rules
    are applied by a :class:`PruningCoordinator`; rules with *higher*
    priority integers are applied first.

    The contract for :meth:`apply` is:
    - It MUST return a :class:`PruneDecision` for every call.
    - If the rule does not request pruning it should return
      ``PruneDecision(should_prune=False, ...)``.
    - It MUST NOT raise exceptions for well-formed inputs.
    - Side effects (e.g. updating a cache) are permitted but must not alter
      ``state`` or ``context``.
    """

    @abstractmethod
    def apply(self, state: Any, context: Dict[str, Any]) -> PruneDecision:
        """Evaluate whether ``state`` should be pruned.

        Args:
            state: A :class:`SemanticState` (or duck-typed equivalent) to
                evaluate.
            context: A dictionary of contextual information.  Common keys:
                - ``"visited_states"``: ``List[Any]`` — already-visited states.
                - ``"known_conflicts"``: ``Set[str]`` — patch IDs with known
                  conflicting assignments in the current problem instance.
                - ``"best_cost"``: ``float`` — best total cost seen so far
                  (used by the bound rule).
                - ``"cost_so_far"``: ``float`` — accumulated cost ``g(σ)``.
                - ``"max_rounds"``: ``int`` — maximum allowed generation round.

        Returns:
            A :class:`PruneDecision` indicating whether the state should be
            pruned, which rule made the decision, and why.
        """

    @abstractmethod
    def name(self) -> str:
        """Return the canonical identifier string for this rule.

        Returns:
            A short lowercase string, e.g. ``"dominance"``.  Used for
            logging, statistics attribution, and human-readable output.
        """

    def priority(self) -> int:
        """Return the application priority of this rule.

        Higher priority rules are applied before lower priority rules in a
        :class:`PruningCoordinator`.  The coordinator applies rules in
        descending priority order and short-circuits on the first prune.

        Returns:
            An integer priority.  Defaults to 0.
        """
        return 0


# ---------------------------------------------------------------------------
# Concrete rule: Dominance
# ---------------------------------------------------------------------------


class DominancePruningRule(PruningRule):
    """Prune states that are dominated by an already-visited state.

    Formal definition (theory2.tex §40.4.1):
        State ``s_v`` (visited) *dominates* candidate state ``s_c`` iff
        ``dom(s_c) ⊆ dom(s_v)`` and ``∀p ∈ dom(s_c): s_v(p) = s_c(p)``.

    When this condition holds, every future extension of ``s_c`` is also
    reachable by extending ``s_v``, which is already in the visited set.
    Therefore ``s_c`` offers no new reachability and may be safely discarded.

    Implementation notes:
    - The rule maintains its own ``_visited`` dictionary so that it can be
      used independently of the coordinator's visited-set management.
    - Results of pairwise dominance checks are cached in
      ``_dominance_cache`` keyed by ``(s_visited.state_id, s_c.state_id)``.
    - The rule also checks ``context["visited_states"]`` at apply-time so
      that it works correctly even if the caller manages visited states
      externally.
    """

    def __init__(self) -> None:
        """Initialise internal visited-state registry and dominance cache."""
        # Maps state_id -> state object for fast lookup of previously-seen states.
        self._visited: Dict[str, Any] = {}
        # Cache keyed by (visited_id, candidate_id) to avoid redundant comparisons.
        self._dominance_cache: Dict[Tuple[str, str], DominanceResult] = {}

    def name(self) -> str:
        """Return rule identifier.

        Returns:
            ``"dominance"``
        """
        return "dominance"

    def priority(self) -> int:
        """Return priority 10 — applied after obstruction but before bound.

        Returns:
            ``10``
        """
        return 10

    def apply(self, state: Any, context: Dict[str, Any]) -> PruneDecision:
        """Check whether any visited state dominates ``state``.

        The method first collects candidate visitors from:
          1. ``context["visited_states"]`` — an external list supplied by the
             caller (e.g. the search loop).
          2. ``self._visited`` — the rule's own internal registry, populated
             via :meth:`register_visited`.

        For each visited state it calls :meth:`_dominates` and, on the first
        positive match, returns a ``should_prune=True`` decision.  If no
        dominator is found it returns ``should_prune=False``.

        Args:
            state: The candidate :class:`SemanticState` to evaluate.
            context: Must support key ``"visited_states"`` (list); other keys
                are ignored by this rule.

        Returns:
            A :class:`PruneDecision` whose ``should_prune`` is ``True`` iff a
            dominating visited state was found.

        Raises:
            AttributeError: If ``state`` lacks ``state_id`` or
                ``patch_assignments``.  Callers should ensure well-formed
                states are passed.
        """
        candidate_id = getattr(state, "state_id", str(id(state)))

        # Merge external and internal visited collections without duplicates.
        external: List[Any] = context.get("visited_states", [])
        all_visited: Dict[str, Any] = {
            getattr(v, "state_id", str(id(v))): v for v in external
        }
        all_visited.update(self._visited)
        # Do not compare state against itself.
        all_visited.pop(candidate_id, None)

        for v_id, v_state in all_visited.items():
            if self._dominates(v_state, state):
                logger.debug(
                    "dominance prune: %s is dominated by visited state %s",
                    candidate_id,
                    v_id,
                )
                return PruneDecision(
                    should_prune=True,
                    rule_name=self.name(),
                    reason=(
                        f"state {candidate_id} is dominated by visited "
                        f"state {v_id}: all assignments of the candidate are "
                        "subsumed by the visitor"
                    ),
                    confidence=1.0,
                    state_id=candidate_id,
                )

        return PruneDecision(
            should_prune=False,
            rule_name=self.name(),
            reason="no visited state dominates the candidate",
            confidence=1.0,
            state_id=candidate_id,
        )

    def register_visited(self, state: Any) -> None:
        """Add a state to the internal visited registry.

        Call this after a state has been selected for expansion so that future
        candidates can be checked against it.

        Args:
            state: A :class:`SemanticState` (or duck-typed equivalent) that
                has been fully processed.

        Returns:
            ``None``.
        """
        state_id = getattr(state, "state_id", str(id(state)))
        self._visited[state_id] = state
        logger.debug("registered visited state %s (total=%d)", state_id, len(self._visited))

    def _dominates(self, s_visited: Any, s_candidate: Any) -> bool:
        """Return ``True`` iff ``s_visited`` dominates ``s_candidate``.

        Dominance holds when every patch assignment of the candidate is also
        present in the visitor *with the same value*.  Formally:

            dom(s_candidate) ⊆ dom(s_visited)
            ∧ ∀p ∈ dom(s_candidate): s_visited(p) = s_candidate(p)

        Results are memoised in ``self._dominance_cache``.

        Args:
            s_visited: The state already in the visited set.
            s_candidate: The state under consideration for pruning.

        Returns:
            ``True`` if ``s_visited`` dominates ``s_candidate``, else
            ``False``.
        """
        v_id = getattr(s_visited, "state_id", str(id(s_visited)))
        c_id = getattr(s_candidate, "state_id", str(id(s_candidate)))
        cache_key: Tuple[str, str] = (v_id, c_id)

        if cache_key in self._dominance_cache:
            return self._dominance_cache[cache_key].s1_dominates_s2

        v_assignments: Dict[str, str] = getattr(s_visited, "patch_assignments", {}) or {}
        c_assignments: Dict[str, str] = getattr(s_candidate, "patch_assignments", {}) or {}

        # Every patch in the candidate must appear in the visitor.
        if not set(c_assignments.keys()).issubset(set(v_assignments.keys())):
            self._dominance_cache[cache_key] = DominanceResult(
                s1_id=v_id,
                s2_id=c_id,
                s1_dominates_s2=False,
                s2_dominates_s1=False,
                incomparable=True,
                shared_patch_count=len(set(v_assignments) & set(c_assignments)),
                s1_extra_patches=len(set(v_assignments) - set(c_assignments)),
                s2_extra_patches=len(set(c_assignments) - set(v_assignments)),
                conflict_count=0,
            )
            return False

        # All shared patches must have identical assignments.
        for patch_id, c_value in c_assignments.items():
            if v_assignments.get(patch_id) != c_value:
                self._dominance_cache[cache_key] = DominanceResult(
                    s1_id=v_id,
                    s2_id=c_id,
                    s1_dominates_s2=False,
                    s2_dominates_s1=False,
                    incomparable=True,
                    shared_patch_count=len(set(v_assignments) & set(c_assignments)),
                    s1_extra_patches=len(set(v_assignments) - set(c_assignments)),
                    s2_extra_patches=len(set(c_assignments) - set(v_assignments)),
                    conflict_count=sum(
                        1 for p, v in c_assignments.items()
                        if p in v_assignments and v_assignments[p] != v
                    ),
                )
                return False

        # All checks passed — visitor dominates candidate.
        self._dominance_cache[cache_key] = DominanceResult(
            s1_id=v_id,
            s2_id=c_id,
            s1_dominates_s2=True,
            s2_dominates_s1=(set(v_assignments.keys()) == set(c_assignments.keys())),
            incomparable=False,
            shared_patch_count=len(c_assignments),
            s1_extra_patches=len(set(v_assignments) - set(c_assignments)),
            s2_extra_patches=0,
            conflict_count=0,
        )
        return True


# ---------------------------------------------------------------------------
# Concrete rule: Obstruction
# ---------------------------------------------------------------------------


class ObstructionPruningRule(PruningRule):
    """Prune states with fundamental cohomological obstructions.

    Background (theory2.tex §40.4.2):
        A partial assignment σ has a *fundamental obstruction* if the
        Čech 1-cocycle c ∈ Z^1(N(P), S) induced by σ is not a coboundary —
        equivalently, if the cohomology class [c] ∈ H^1(N(P), S) is
        non-trivial.  Such states can never be extended to a global section,
        so they are pruned with confidence 1.0.

    Practical heuristics used in this implementation (an exact cohomology
    computation is too expensive for the hot search path):

    1. **Metadata flag**: if ``state.metadata.get("obstruction_detected")``
       evaluates to ``True``, an obstruction has been detected by a prior
       phase and we prune immediately.

    2. **Known-conflict check**: the context key ``"known_conflicts"`` may
       carry a set of patch IDs that are known to produce conflicting
       assignments in the current problem instance.  If the state's
       ``patch_assignments`` contains a patch whose assigned value is in
       conflict with a pair listed in ``context["known_conflict_pairs"]`` (a
       dict mapping patch_id → set of conflicting values), we prune.

    3. **Stagnation check**: if ``coverage_fraction`` is 0.0 and
       ``generation_round`` exceeds ``obstruction_threshold * max_rounds``,
       the state has made no progress and is likely obstructed.

    Attributes:
        obstruction_threshold: Fraction of max_rounds beyond which a
            zero-coverage state is treated as obstructed.  Default 0.8.
    """

    def __init__(self, obstruction_threshold: float = 0.8) -> None:
        """Initialise the obstruction rule.

        Args:
            obstruction_threshold: Float in ``(0, 1]`` controlling stagnation
                detection.  A zero-coverage state is pruned when
                ``generation_round >= obstruction_threshold * max_rounds``.

        Raises:
            ValueError: If ``obstruction_threshold`` is not in ``(0, 1]``.
        """
        if not (0.0 < obstruction_threshold <= 1.0):
            raise ValueError(
                f"obstruction_threshold must be in (0, 1]; got {obstruction_threshold!r}"
            )
        self.obstruction_threshold = obstruction_threshold

    def name(self) -> str:
        """Return rule identifier.

        Returns:
            ``"obstruction"``
        """
        return "obstruction"

    def priority(self) -> int:
        """Return priority 20 — highest priority among default rules.

        Obstruction pruning is applied first because an obstruction is a
        *definitive* barrier: no amount of additional work can recover a state
        with a non-trivial cohomology class.  Applying this rule before the
        cheaper dominance and bound rules avoids unnecessarily expensive
        comparisons for states that are already dead ends.

        Returns:
            ``20``
        """
        return 20

    def apply(self, state: Any, context: Dict[str, Any]) -> PruneDecision:
        """Check ``state`` for fundamental obstructions.

        The three heuristics are tried in order; the first positive match
        triggers an immediate prune.

        Args:
            state: The :class:`SemanticState` to evaluate.
            context: May contain:
                - ``"known_conflicts"``: ``Set[str]`` — patch IDs known to
                  conflict anywhere in the problem instance.
                - ``"known_conflict_pairs"``: ``Dict[str, Set[str]]`` —
                  maps patch IDs to the set of values that would create a
                  conflict.
                - ``"max_rounds"``: ``int`` — maximum allowed generation round
                  (used for stagnation detection).

        Returns:
            :class:`PruneDecision` with ``should_prune=True`` and
            ``confidence=1.0`` if an obstruction is detected, or
            ``should_prune=False`` with ``confidence=0.9`` otherwise
            (high confidence that there is *no* obstruction, but not
            certainty since the check is a heuristic approximation).
        """
        state_id = getattr(state, "state_id", str(id(state)))
        metadata: Dict[str, Any] = getattr(state, "metadata", {}) or {}

        # Heuristic 1: explicit flag set by an upstream analysis phase.
        if metadata.get("obstruction_detected"):
            logger.debug("obstruction prune (metadata flag): %s", state_id)
            return PruneDecision(
                should_prune=True,
                rule_name=self.name(),
                reason=(
                    "state metadata carries obstruction_detected=True; "
                    "non-trivial cohomology class detected by prior analysis"
                ),
                confidence=1.0,
                state_id=state_id,
            )

        # Heuristic 2: assignment conflicts on known-conflict patches.
        patch_assignments: Dict[str, str] = (
            getattr(state, "patch_assignments", {}) or {}
        )
        known_conflict_pairs: Dict[str, Set[str]] = context.get(
            "known_conflict_pairs", {}
        )
        for patch_id, assigned_value in patch_assignments.items():
            conflicting_values = known_conflict_pairs.get(patch_id, set())
            if assigned_value in conflicting_values:
                logger.debug(
                    "obstruction prune (conflict pair) for %s: patch %s = %r is conflicting",
                    state_id,
                    patch_id,
                    assigned_value,
                )
                return PruneDecision(
                    should_prune=True,
                    rule_name=self.name(),
                    reason=(
                        f"assignment {patch_id}={assigned_value!r} is in the "
                        "known-conflict set; local consistency is violated"
                    ),
                    confidence=1.0,
                    state_id=state_id,
                )

        # Heuristic 3: stagnation — zero coverage after many rounds.
        generation_round: int = getattr(state, "generation_round", 0)
        max_rounds: int = context.get("max_rounds", 100)
        coverage: float = 0.0
        compute_cov = getattr(state, "compute_coverage_fraction", None)
        if callable(compute_cov):
            try:
                coverage = float(compute_cov())
            except Exception:
                coverage = 0.0
        elif patch_assignments:
            # Fallback: treat having *any* assignments as non-zero coverage.
            coverage = len(patch_assignments) / max(len(patch_assignments), 1)

        stagnation_threshold_round = int(self.obstruction_threshold * max_rounds)
        if coverage == 0.0 and generation_round > stagnation_threshold_round:
            logger.debug(
                "obstruction prune (stagnation) for %s: round=%d threshold=%d",
                state_id,
                generation_round,
                stagnation_threshold_round,
            )
            return PruneDecision(
                should_prune=True,
                rule_name=self.name(),
                reason=(
                    f"zero coverage after round {generation_round} "
                    f"(threshold={stagnation_threshold_round}); "
                    "probable structural obstruction"
                ),
                confidence=0.85,
                state_id=state_id,
            )

        return PruneDecision(
            should_prune=False,
            rule_name=self.name(),
            reason="no obstruction detected by any heuristic",
            confidence=0.9,
            state_id=state_id,
        )


# ---------------------------------------------------------------------------
# Concrete rule: Bound (branch-and-bound)
# ---------------------------------------------------------------------------


class BoundPruningRule(PruningRule):
    """Prune states whose lower-bound cost cannot beat the current best.

    Implements the classic branch-and-bound criterion:

        prune iff g(σ) + h(σ) ≥ best_known

    where ``g`` is the accumulated cost passed in via context and ``h`` is
    an admissible (never-overestimating) heuristic.

    The default heuristic is ``h(σ) = 1 − coverage_fraction(σ)``, which
    estimates the remaining fraction of patches still to be assigned.  This
    is admissible when the cost model charges exactly 1 unit per newly
    assigned patch (a reasonable approximation for jugeo generation rounds).

    Custom heuristics can be supplied at construction time; they must satisfy
    admissibility (``h(σ) ≤ true_remaining_cost``) for the pruning to remain
    sound.

    Attributes:
        _heuristic: The callable ``h: state → float`` used to estimate
            remaining cost.
    """

    def __init__(self, heuristic: Optional[Callable[..., float]] = None) -> None:
        """Initialise the bound rule.

        Args:
            heuristic: Optional callable ``h(state) -> float`` that returns a
                lower bound on the remaining cost to reach a goal from
                ``state``.  Must be admissible (never overestimate) for the
                rule to be sound.  If ``None``, the default heuristic
                ``1 - coverage_fraction`` is used.
        """
        if heuristic is not None:
            self._heuristic: Callable[..., float] = heuristic
        else:
            self._heuristic = self._default_heuristic

    @staticmethod
    def _default_heuristic(state: Any) -> float:
        """Compute the default lower-bound heuristic.

        Estimates the remaining cost as the fraction of patches not yet
        assigned.  Returns ``1.0 - coverage_fraction``.

        For states that expose ``compute_coverage_fraction()``, that method
        is called directly.  Otherwise, the method returns ``0.0`` (optimistic
        but safe — it avoids incorrect pruning of states we cannot evaluate).

        Args:
            state: A :class:`SemanticState` or duck-typed equivalent.

        Returns:
            A float in ``[0.0, 1.0]``.
        """
        compute_cov = getattr(state, "compute_coverage_fraction", None)
        if callable(compute_cov):
            try:
                return max(0.0, 1.0 - float(compute_cov()))
            except Exception:
                return 0.0
        # Fallback: if there are assignments, assume partial coverage.
        patch_assignments = getattr(state, "patch_assignments", {}) or {}
        if patch_assignments:
            # Unknown total patches — assume at least half done.
            return 0.5
        return 0.0

    def name(self) -> str:
        """Return rule identifier.

        Returns:
            ``"bound"``
        """
        return "bound"

    def priority(self) -> int:
        """Return priority 5 — applied after dominance and obstruction.

        Bound pruning requires more context (cost estimates) than the other
        rules.  By applying it last we avoid wasting effort computing costs
        for states that would have been pruned more cheaply by other rules.

        Returns:
            ``5``
        """
        return 5

    def apply(self, state: Any, context: Dict[str, Any]) -> PruneDecision:
        """Apply the branch-and-bound pruning criterion.

        Retrieves ``best_cost`` and ``cost_so_far`` from ``context``, computes
        the heuristic, and prunes if ``g + h ≥ best_known``.

        Args:
            state: The :class:`SemanticState` to evaluate.
            context: Must support:
                - ``"best_cost"``: ``float`` — the cost of the best solution
                  found so far.  Defaults to ``float("inf")`` (no bound).
                - ``"cost_so_far"``: ``float`` — the accumulated cost ``g(σ)``
                  along the path to ``state``.  Defaults to ``0.0``.

        Returns:
            :class:`PruneDecision` with ``should_prune=True`` iff the lower
            bound ``g + h ≥ best_cost``.  When ``best_cost`` is ``inf``, the
            criterion is never triggered and the decision is always
            ``should_prune=False``.
        """
        state_id = getattr(state, "state_id", str(id(state)))
        best_cost: float = context.get("best_cost", float("inf"))
        g: float = float(context.get("cost_so_far", 0.0))

        # Short-circuit: no bound available, cannot prune.
        if best_cost == float("inf"):
            return PruneDecision(
                should_prune=False,
                rule_name=self.name(),
                reason="best_cost is inf; no bound available",
                confidence=1.0,
                state_id=state_id,
            )

        h: float = self._heuristic(state)
        f: float = g + h

        if f >= best_cost:
            logger.debug(
                "bound prune for %s: g=%.4f h=%.4f f=%.4f best=%.4f",
                state_id,
                g,
                h,
                f,
                best_cost,
            )
            return PruneDecision(
                should_prune=True,
                rule_name=self.name(),
                reason=(
                    f"g({g:.4f}) + h({h:.4f}) = f({f:.4f}) ≥ best_cost({best_cost:.4f}); "
                    "state cannot improve on the current best solution"
                ),
                confidence=min(1.0, (f - best_cost + 1.0) / (abs(best_cost) + 1.0)),
                state_id=state_id,
            )

        return PruneDecision(
            should_prune=False,
            rule_name=self.name(),
            reason=f"f={f:.4f} < best_cost={best_cost:.4f}; state may still improve",
            confidence=1.0,
            state_id=state_id,
        )


# ---------------------------------------------------------------------------
# PruningCoordinator
# ---------------------------------------------------------------------------


class PruningCoordinator:
    """Orchestrates multiple :class:`PruningRule` instances.

    The coordinator applies registered rules in descending priority order and
    short-circuits as soon as one rule requests pruning.  It accumulates
    running statistics and a full decision history for offline analysis.

    Default rules registered at construction time (in application order):
    1. :class:`ObstructionPruningRule` (priority 20)
    2. :class:`DominancePruningRule` (priority 10)
    3. :class:`BoundPruningRule` (priority 5)

    Additional rules can be injected via :meth:`register_rule`.
    """

    def __init__(self) -> None:
        """Initialise the coordinator with the three default pruning rules."""
        self._rules: List[PruningRule] = []
        self._stats: PruningStats = PruningStats()
        self._decision_history: List[PruneDecision] = []

        # Register the three canonical rules in priority order.
        self.register_rule(ObstructionPruningRule())
        self.register_rule(DominancePruningRule())
        self.register_rule(BoundPruningRule())

    def register_rule(self, rule: PruningRule) -> None:
        """Add a pruning rule to the coordinator.

        The internal rule list is re-sorted by priority (descending) after
        insertion so that :meth:`should_prune` always applies rules in the
        correct order.

        Args:
            rule: A :class:`PruningRule` instance to register.

        Returns:
            ``None``.
        """
        self._rules.append(rule)
        # Keep sorted descending by priority so highest-priority rule is first.
        self._rules.sort(key=lambda r: r.priority(), reverse=True)
        logger.debug(
            "registered rule %r (priority=%d); total rules=%d",
            rule.name(),
            rule.priority(),
            len(self._rules),
        )

    def should_prune(self, state: Any, context: Dict[str, Any]) -> PruneDecision:
        """Determine whether ``state`` should be pruned.

        Rules are applied in descending priority order.  The method returns
        the decision of the *first* rule that requests pruning.  If no rule
        requests pruning, a composite no-prune decision is returned.

        After the method returns, the decision is appended to
        ``_decision_history`` and the running stats are updated.

        Args:
            state: The :class:`SemanticState` to evaluate.
            context: Contextual information forwarded to each rule.  See
                individual rule ``apply`` docstrings for supported keys.

        Returns:
            The first :class:`PruneDecision` with ``should_prune=True``, or a
            no-prune decision if all rules pass the state.
        """
        for rule in self._rules:
            decision = rule.apply(state, context)
            if decision.should_prune:
                self._record(decision)
                return decision

        # No rule requested pruning; return a composite no-prune decision.
        state_id = getattr(state, "state_id", str(id(state)))
        no_prune = PruneDecision(
            should_prune=False,
            rule_name="none",
            reason="no rule requested pruning",
            confidence=1.0,
            state_id=state_id,
        )
        self._record(no_prune)
        return no_prune

    def apply_all_rules(
        self, state: Any, context: Dict[str, Any]
    ) -> List[PruneDecision]:
        """Apply every rule and collect all decisions (no short-circuit).

        Unlike :meth:`should_prune`, this method does *not* stop at the first
        prune.  Useful for analysis and rule-effectiveness studies.

        Args:
            state: The :class:`SemanticState` to evaluate.
            context: Contextual information forwarded to each rule.

        Returns:
            A list of :class:`PruneDecision` objects, one per registered rule,
            in descending priority order.
        """
        decisions: List[PruneDecision] = []
        for rule in self._rules:
            decisions.append(rule.apply(state, context))
        return decisions

    def get_pruning_stats(self) -> PruningStats:
        """Return the current running pruning statistics.

        Returns:
            A reference to the internal :class:`PruningStats` object.  Note
            that this is a *live* reference; it will reflect any subsequent
            updates.  Callers that need a snapshot should copy the fields.
        """
        return self._stats

    def _record(self, decision: PruneDecision) -> None:
        """Append ``decision`` to history and update stats.

        Args:
            decision: The :class:`PruneDecision` to record.
        """
        self._decision_history.append(decision)
        self._stats.update(decision)


# ---------------------------------------------------------------------------
# PruningAnalyzer
# ---------------------------------------------------------------------------


class PruningAnalyzer:
    """Utility class for offline pruning analysis and diagnostics.

    ``PruningAnalyzer`` is stateless; all methods are pure functions of their
    inputs.  It is designed to be used *after* a search run to study pruning
    effectiveness, tune heuristics, and generate human-readable reports.
    """

    def compute_dominance(self, s1: Any, s2: Any) -> DominanceResult:
        """Compute the full pairwise dominance relationship between ``s1`` and ``s2``.

        Unlike the internal helper :meth:`DominancePruningRule._dominates`,
        this method computes *both* directions of the relationship and returns
        a richly annotated :class:`DominanceResult`.

        Args:
            s1: First :class:`SemanticState` (or duck-typed equivalent).
            s2: Second :class:`SemanticState` (or duck-typed equivalent).

        Returns:
            A :class:`DominanceResult` capturing whether ``s1`` dominates
            ``s2``, ``s2`` dominates ``s1``, or they are incomparable, along
            with patch-count diagnostics.
        """
        s1_id: str = getattr(s1, "state_id", str(id(s1)))
        s2_id: str = getattr(s2, "state_id", str(id(s2)))
        a1: Dict[str, str] = getattr(s1, "patch_assignments", {}) or {}
        a2: Dict[str, str] = getattr(s2, "patch_assignments", {}) or {}

        shared = set(a1.keys()) & set(a2.keys())
        conflicts = sum(1 for p in shared if a1[p] != a2[p])
        s1_extra = len(set(a1.keys()) - set(a2.keys()))
        s2_extra = len(set(a2.keys()) - set(a1.keys()))

        # s1 dominates s2 iff dom(s2) ⊆ dom(s1) and no conflicts.
        s1_dom_s2 = (s2_extra == 0) and (conflicts == 0)
        # s2 dominates s1 iff dom(s1) ⊆ dom(s2) and no conflicts.
        s2_dom_s1 = (s1_extra == 0) and (conflicts == 0)
        incomparable = (not s1_dom_s2) and (not s2_dom_s1)

        return DominanceResult(
            s1_id=s1_id,
            s2_id=s2_id,
            s1_dominates_s2=s1_dom_s2,
            s2_dominates_s1=s2_dom_s1,
            incomparable=incomparable,
            shared_patch_count=len(shared),
            s1_extra_patches=s1_extra,
            s2_extra_patches=s2_extra,
            conflict_count=conflicts,
        )

    def check_obstruction_prune(self, state: Any) -> bool:
        """Heuristically check whether ``state`` carries an obstruction.

        Applies a lightweight version of the obstruction check without
        requiring a full context dictionary.  Checks:
        - ``state.metadata.get("obstruction_detected")``
        - ``state.patch_assignments`` is empty and round > 0 (possible
          stagnation indicator)

        Args:
            state: A :class:`SemanticState` or duck-typed equivalent.

        Returns:
            ``True`` if an obstruction is likely, ``False`` otherwise.
        """
        metadata = getattr(state, "metadata", {}) or {}
        if metadata.get("obstruction_detected"):
            return True
        patch_assignments = getattr(state, "patch_assignments", {}) or {}
        generation_round = getattr(state, "generation_round", 0)
        # Stagnation: no assignments after multiple rounds is suspicious.
        if not patch_assignments and generation_round > 5:
            return True
        return False

    def estimate_bound(
        self, state: Any, heuristic: Optional[Callable[..., float]] = None
    ) -> float:
        """Estimate the lower-bound cost ``h(σ)`` for ``state``.

        If a custom ``heuristic`` is supplied it is called; otherwise the
        default heuristic ``1 - coverage_fraction`` is used.

        Args:
            state: A :class:`SemanticState` or duck-typed equivalent.
            heuristic: Optional callable ``h(state) -> float``.  If ``None``,
                ``BoundPruningRule._default_heuristic`` is used.

        Returns:
            A non-negative float representing the estimated remaining cost.
        """
        fn = heuristic if heuristic is not None else BoundPruningRule._default_heuristic
        try:
            result = float(fn(state))
        except Exception as exc:
            logger.warning("heuristic raised %r; returning 0.0", exc)
            result = 0.0
        return max(0.0, result)

    def analyze_pruning_history(
        self, history: List[PruneDecision]
    ) -> PruningAnalysis:
        """Aggregate a list of :class:`PruneDecision` records into a summary.

        Computes per-rule counts, average confidence, dominance chain count,
        and generates a list of actionable recommendations.

        Args:
            history: Ordered list of :class:`PruneDecision` objects from a
                search run (typically ``coordinator._decision_history``).

        Returns:
            A :class:`PruningAnalysis` summarising the pruning behaviour.
        """
        if not history:
            return PruningAnalysis(
                analysis_id=str(uuid.uuid4()),
                total_states_analyzed=0,
                pruning_rate_history=[],
                most_effective_rule="none",
                avg_confidence=0.0,
                dominance_chains=0,
                obstruction_count=0,
                bound_violations=0,
                recommendations=["No pruning history available; run the search first."],
            )

        total = len(history)
        pruned_decisions = [d for d in history if d.should_prune]
        total_pruned = len(pruned_decisions)

        # Per-rule counts.
        rule_counts: Dict[str, int] = {}
        for d in pruned_decisions:
            rule_counts[d.rule_name] = rule_counts.get(d.rule_name, 0) + 1

        most_effective = max(rule_counts, key=lambda r: rule_counts[r]) if rule_counts else "none"
        avg_conf = (
            sum(d.confidence for d in pruned_decisions) / total_pruned
            if total_pruned
            else 0.0
        )

        # Build a pruning-rate history in windows of 10.
        window = max(1, total // 10)
        rate_history: List[float] = []
        for i in range(0, total, window):
            chunk = history[i : i + window]
            rate_history.append(sum(1 for d in chunk if d.should_prune) / len(chunk))

        dominance_chains = rule_counts.get("dominance", 0)
        obstruction_count = rule_counts.get("obstruction", 0)
        bound_violations = rule_counts.get("bound", 0)

        # Generate recommendations.
        recommendations: List[str] = []
        overall_rate = total_pruned / total if total else 0.0
        if overall_rate < 0.1:
            recommendations.append(
                "Pruning rate is below 10%. Consider tightening the bound heuristic "
                "or adding problem-specific dominance criteria."
            )
        if obstruction_count == 0 and total > 50:
            recommendations.append(
                "No obstructions detected over a large run. If the problem has known "
                "conflict structure, consider populating 'known_conflict_pairs' in context."
            )
        if dominance_chains > 0 and (dominance_chains / total) > 0.5:
            recommendations.append(
                "Dominance pruning is very active (>50% of states). Consider a more "
                "aggressive expansion strategy to reduce redundant frontier entries."
            )
        if bound_violations == 0 and total > 100:
            recommendations.append(
                "Bound pruning never triggered. Supply a tighter admissible heuristic "
                "or a non-trivial 'best_cost' in context to enable branch-and-bound."
            )
        if avg_conf < 0.7:
            recommendations.append(
                "Average pruning confidence is low (< 0.70). Review obstruction and "
                "bound heuristics for admissibility and tightness."
            )
        if not recommendations:
            recommendations.append(
                "Pruning behaviour looks healthy. No immediate recommendations."
            )

        return PruningAnalysis(
            analysis_id=str(uuid.uuid4()),
            total_states_analyzed=total,
            pruning_rate_history=rate_history,
            most_effective_rule=most_effective,
            avg_confidence=avg_conf,
            dominance_chains=dominance_chains,
            obstruction_count=obstruction_count,
            bound_violations=bound_violations,
            recommendations=recommendations,
        )


# ---------------------------------------------------------------------------
# PruningWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PruningWitness:
    """Immutable record of a pruning event, suitable for archival and auditing.

    A :class:`PruningWitness` is a frozen snapshot of a :class:`PruneDecision`
    that can be stored, hashed, serialised, and compared without risk of
    inadvertent mutation.  It is intended for use in audit logs, proofs of
    soundness, and test assertions.

    Attributes:
        witness_id: A UUID string uniquely identifying this witness record.
        state_id: The ``state_id`` of the pruned state.
        rule_name: Identifier of the rule that issued the prune.
        prune_reason: Human-readable reason string copied from the decision.
        confidence: Confidence score copied from the decision.
        timestamp: Unix epoch time at which the original decision was made.
    """

    witness_id: str
    state_id: str
    rule_name: str
    prune_reason: str
    confidence: float
    timestamp: float

    @classmethod
    def from_decision(cls, decision: PruneDecision) -> "PruningWitness":
        """Construct a :class:`PruningWitness` from a :class:`PruneDecision`.

        Only creates a witness for decisions that *do* prune; calling this
        for a no-prune decision is permitted but semantically unusual.

        Args:
            decision: The :class:`PruneDecision` to convert.

        Returns:
            A new :class:`PruningWitness` with a freshly generated
            ``witness_id`` and all other fields copied from ``decision``.
        """
        return cls(
            witness_id=str(uuid.uuid4()),
            state_id=decision.state_id,
            rule_name=decision.rule_name,
            prune_reason=decision.reason,
            confidence=decision.confidence,
            timestamp=decision.timestamp,
        )


# ---------------------------------------------------------------------------
# Smoke test / demonstration
# ---------------------------------------------------------------------------

def _make_simple_state(
    state_id: str,
    patch_assignments: Dict[str, str],
    generation_round: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> "object":
    """Create a minimal duck-typed state object for testing.

    Produces a simple namespace object that mimics the essential interface of
    :class:`SemanticState` without requiring the models module.

    Args:
        state_id: Unique identifier for the state.
        patch_assignments: Dictionary mapping patch IDs to assigned values.
        generation_round: The current generation round counter.
        metadata: Optional metadata dictionary.

    Returns:
        An object with ``state_id``, ``patch_assignments``,
        ``generation_round``, ``metadata``, and
        ``compute_coverage_fraction()`` attributes.
    """

    class _SimpleState:
        def __init__(self) -> None:
            self.state_id = state_id
            self.patch_assignments = dict(patch_assignments)
            self.generation_round = generation_round
            self.metadata = metadata or {}

        def compute_coverage_fraction(self) -> float:
            # Treat 10 patches as the universe size for demo purposes.
            universe_size = 10
            return min(1.0, len(self.patch_assignments) / universe_size)

        def __repr__(self) -> str:
            return (
                f"<State {self.state_id} "
                f"assignments={list(self.patch_assignments.keys())} "
                f"round={self.generation_round}>"
            )

    return _SimpleState()


def _run_smoke_test() -> None:
    """Demonstrate dominance, obstruction, and bound pruning.

    This function is executed when the module is run as a script
    (``python -m jugeo.generation.state_space.pruning``).  It creates
    several synthetic states and shows each pruning rule in action.

    States used:
    - ``alpha``: partial assignment covering patches p0–p2 (small).
    - ``beta``: assignment covering p0–p4 with same values on p0–p2 as alpha
      (dominates alpha).
    - ``gamma``: obstructed state (metadata flag).
    - ``delta``: high-cost state that exceeds the bound.
    - ``epsilon``: healthy state with no prune trigger.

    Returns:
        ``None``.  Prints results to stdout.
    """
    print("=" * 70)
    print("Pruning Smoke Test — Chapter 40, Section 4")
    print("=" * 70)

    # ------------------------------------------------------------------
    # State construction
    # ------------------------------------------------------------------
    alpha = _make_simple_state(
        "alpha",
        {"p0": "v_A", "p1": "v_B", "p2": "v_C"},
        generation_round=1,
    )
    beta = _make_simple_state(
        "beta",
        {"p0": "v_A", "p1": "v_B", "p2": "v_C", "p3": "v_D", "p4": "v_E"},
        generation_round=2,
    )
    gamma = _make_simple_state(
        "gamma",
        {"p0": "v_A"},
        generation_round=1,
        metadata={"obstruction_detected": True},
    )
    delta = _make_simple_state(
        "delta",
        {"p5": "v_F"},
        generation_round=3,
    )
    epsilon = _make_simple_state(
        "epsilon",
        {"p0": "v_A", "p6": "v_G", "p7": "v_H"},
        generation_round=1,
    )

    # ------------------------------------------------------------------
    # Dominance pruning demo
    # ------------------------------------------------------------------
    print("\n--- Dominance Pruning ---")
    dom_rule = DominancePruningRule()
    dom_rule.register_visited(beta)  # beta is already in visited set

    ctx_dom: Dict[str, Any] = {"visited_states": [beta]}
    decision_alpha = dom_rule.apply(alpha, ctx_dom)
    print(
        f"Prune alpha? {decision_alpha.should_prune}  "
        f"(confidence={decision_alpha.confidence:.2f})"
    )
    print(f"  Reason: {decision_alpha.reason}")

    decision_epsilon = dom_rule.apply(epsilon, ctx_dom)
    print(
        f"Prune epsilon? {decision_epsilon.should_prune}  "
        f"(confidence={decision_epsilon.confidence:.2f})"
    )
    print(f"  Reason: {decision_epsilon.reason}")

    # ------------------------------------------------------------------
    # Obstruction pruning demo
    # ------------------------------------------------------------------
    print("\n--- Obstruction Pruning ---")
    obs_rule = ObstructionPruningRule(obstruction_threshold=0.8)
    ctx_obs: Dict[str, Any] = {
        "known_conflict_pairs": {"p5": {"v_F"}},  # v_F on p5 is conflicting
        "max_rounds": 10,
    }
    decision_gamma = obs_rule.apply(gamma, ctx_obs)
    print(
        f"Prune gamma (flagged)? {decision_gamma.should_prune}  "
        f"(confidence={decision_gamma.confidence:.2f})"
    )
    print(f"  Reason: {decision_gamma.reason}")

    decision_delta_obs = obs_rule.apply(delta, ctx_obs)
    print(
        f"Prune delta (conflict)? {decision_delta_obs.should_prune}  "
        f"(confidence={decision_delta_obs.confidence:.2f})"
    )
    print(f"  Reason: {decision_delta_obs.reason}")

    # ------------------------------------------------------------------
    # Bound pruning demo
    # ------------------------------------------------------------------
    print("\n--- Bound Pruning ---")
    bound_rule = BoundPruningRule()
    ctx_bound: Dict[str, Any] = {
        "best_cost": 0.8,  # best solution so far has cost 0.8
        "cost_so_far": 0.6,  # delta already cost 0.6 to reach
    }
    decision_delta_bound = bound_rule.apply(delta, ctx_bound)
    print(
        f"Prune delta (bound)? {decision_delta_bound.should_prune}  "
        f"(confidence={decision_delta_bound.confidence:.2f})"
    )
    print(f"  Reason: {decision_delta_bound.reason}")

    ctx_bound_ok: Dict[str, Any] = {
        "best_cost": 2.0,
        "cost_so_far": 0.1,
    }
    decision_epsilon_bound = bound_rule.apply(epsilon, ctx_bound_ok)
    print(
        f"Prune epsilon (bound)? {decision_epsilon_bound.should_prune}  "
        f"(confidence={decision_epsilon_bound.confidence:.2f})"
    )
    print(f"  Reason: {decision_epsilon_bound.reason}")

    # ------------------------------------------------------------------
    # PruningCoordinator demo
    # ------------------------------------------------------------------
    print("\n--- PruningCoordinator (full pipeline) ---")
    coordinator = PruningCoordinator()

    # Manually register beta as visited in the dominance rule.
    for rule in coordinator._rules:
        if isinstance(rule, DominancePruningRule):
            rule.register_visited(beta)

    full_ctx: Dict[str, Any] = {
        "visited_states": [beta],
        "known_conflict_pairs": {"p5": {"v_F"}},
        "max_rounds": 10,
        "best_cost": 0.8,
        "cost_so_far": 0.6,
    }

    for state_obj in [alpha, gamma, delta, epsilon]:
        verdict = coordinator.should_prune(state_obj, full_ctx)
        print(
            f"  {state_obj.state_id:10s}: prune={verdict.should_prune} "
            f"rule={verdict.rule_name:15s} conf={verdict.confidence:.2f}"
        )

    stats = coordinator.get_pruning_stats()
    print(
        f"\nStats: evaluated={stats.total_evaluated}, "
        f"pruned={stats.total_pruned}, "
        f"rate={stats.pruning_rate:.2%}, "
        f"avg_conf={stats.avg_confidence:.2f}"
    )

    # ------------------------------------------------------------------
    # PruningAnalyzer demo
    # ------------------------------------------------------------------
    print("\n--- PruningAnalyzer ---")
    analyzer = PruningAnalyzer()
    dom_result = analyzer.compute_dominance(beta, alpha)
    print(
        f"beta dom alpha? {dom_result.s1_dominates_s2}  "
        f"conflicts={dom_result.conflict_count}  "
        f"s1_extra={dom_result.s1_extra_patches}"
    )

    analysis = analyzer.analyze_pruning_history(coordinator._decision_history)
    print(f"Analysis id: {analysis.analysis_id[:8]}…")
    print(f"Most effective rule: {analysis.most_effective_rule}")
    print("Recommendations:")
    for rec in analysis.recommendations:
        print(f"  • {rec}")

    # ------------------------------------------------------------------
    # PruningWitness demo
    # ------------------------------------------------------------------
    print("\n--- PruningWitness ---")
    prune_decisions = [d for d in coordinator._decision_history if d.should_prune]
    witnesses = [PruningWitness.from_decision(d) for d in prune_decisions]
    for w in witnesses:
        print(
            f"  witness {w.witness_id[:8]}… state={w.state_id} "
            f"rule={w.rule_name} conf={w.confidence:.2f}"
        )

    print("\nSmoke test complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _run_smoke_test()
