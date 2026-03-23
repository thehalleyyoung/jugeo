r"""Chapter 40, Section 6 — State merging.

Theory (theory2.tex §40.6):
    Two semantic states σ1, σ2 can be merged if and only if they are compatible
    on their shared domain: ∀p ∈ dom(σ1) ∩ dom(σ2): σ1(p) = σ2(p).
    When compatible, the merged state is σ = σ1 ⊔ σ2 with:
        dom(σ) = dom(σ1) ∪ dom(σ2)
        σ(p) = σ1(p) if p ∈ dom(σ1), else σ2(p)
    This is precisely the sheaf-theoretic gluing operation lifted to the state level.
    The merge is the categorical pushout in the category of partial functions:
        σ1, σ2 → σ  subject to the compatibility condition.
    Merge benefit = |dom(σ)| - max(|dom(σ1)|, |dom(σ2)|) = new patches gained.
    Incompatible states have MergeStatus.CONFLICT; the conflict patches are those
    p where σ1(p) ≠ σ2(p).

# copilot: s06-state-merging
"""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

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
    "MergeStatus",
    "MergeResult",
    "CompatibilityScore",
    "ConflictResolution",
    "MergeConflict",
    "StateMergingCoordinator",
    "StateMergingAnalyzer",
    "StateMergingWitness",
    "merge_states",
    "find_all_merge_candidates",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MergeStatus(Enum):
    """Outcome classification for a merge attempt between two semantic states.

    SUCCESS   — the states were compatible on their shared domain and the merge
                produced a new state whose domain is dom(σ1) ∪ dom(σ2).
    CONFLICT  — at least one patch is assigned different sections in σ1 vs σ2;
                the merge is rejected and the set of conflict patches is recorded.
    EMPTY     — one or both input states had an empty patch_assignments dict, so
                there was nothing meaningful to merge (degenerate case).
    IDENTICAL — dom(σ1) == dom(σ2) and the assignment functions agree everywhere;
                no new information would be gained, so no merged state is emitted.
    SUBSET    — dom(σ1) ⊆ dom(σ2) (or vice versa); the larger state already
                subsumes the smaller, making an explicit merge redundant.
    """

    SUCCESS = auto()
    CONFLICT = auto()
    EMPTY = auto()
    IDENTICAL = auto()
    SUBSET = auto()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MergeConflict:
    """A single patch where two states disagree on section assignment.

    Attributes
    ----------
    patch_id:
        The identifier of the conflicting patch (key in patch_assignments).
    section_in_s1:
        The section string assigned to *patch_id* by state σ1.
    section_in_s2:
        The section string assigned to *patch_id* by state σ2.
    resolution_hint:
        An optional human-readable hint produced by heuristic analysis,
        e.g. "prefer_s1 because generation_round(s1) > generation_round(s2)".
        Defaults to the empty string when no heuristic hint is available.
    """

    patch_id: str
    section_in_s1: str
    section_in_s2: str
    resolution_hint: str = ""


@dataclass
class MergeResult:
    """Comprehensive record of a merge attempt, successful or not.

    The merge attempt is fully described by this object so that callers can
    make decisions, log outcomes, and reconstruct the witness lattice without
    retaining references to the original states.

    Attributes
    ----------
    status:
        One of the MergeStatus values indicating the outcome.
    merged_state:
        The newly constructed merged SemanticState when status == SUCCESS,
        otherwise None.
    s1_id:
        state_id of the first input state.
    s2_id:
        state_id of the second input state.
    merged_id:
        state_id of the merged state (mirrors merged_state.state_id) when
        status == SUCCESS, otherwise None.
    conflicts:
        List of MergeConflict objects, non-empty only when status == CONFLICT.
    new_patches_gained:
        Number of patches in the merged domain that were not already in the
        larger of the two input domains.  Zero for non-SUCCESS results.
    merge_benefit:
        Normalised benefit in [0, 1]: new_patches_gained / total_merged_patches.
        Provides a relative measure of informational gain.
    message:
        Short human-readable description of the outcome.
    timestamp:
        Wall-clock time (seconds since epoch) when the MergeResult was created.
    """

    status: MergeStatus
    merged_state: Optional[Any]
    s1_id: str
    s2_id: str
    merged_id: Optional[str]
    conflicts: List[MergeConflict]
    new_patches_gained: int
    merge_benefit: float
    message: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class CompatibilityScore:
    """Quantitative compatibility summary for a pair of semantic states.

    This dataclass captures everything needed to decide whether and how
    profitably two states can be merged, without actually performing the merge.

    Attributes
    ----------
    s1_id:
        state_id of the first state.
    s2_id:
        state_id of the second state.
    score:
        Overall compatibility in [0.0, 1.0].  1.0 means all shared patches
        agree (perfectly compatible); 0.0 means every shared patch conflicts
        (completely incompatible) — or there are no shared patches at all and
        the score defaults to 1.0 (vacuous compatibility).
    shared_patch_count:
        |dom(σ1) ∩ dom(σ2)| — number of patches present in both states.
    conflict_count:
        Number of shared patches where σ1(p) ≠ σ2(p).
    compatible_shared:
        Number of shared patches where σ1(p) == σ2(p).
    jaccard_similarity:
        |dom(σ1) ∩ dom(σ2)| / |dom(σ1) ∪ dom(σ2)|, a domain-overlap metric
        independent of the actual section values.
    is_compatible:
        True iff conflict_count == 0; i.e. a merge would succeed.
    """

    s1_id: str
    s2_id: str
    score: float
    shared_patch_count: int
    conflict_count: int
    compatible_shared: int
    jaccard_similarity: float
    is_compatible: bool


@dataclass
class ConflictResolution:
    """A proposed resolution strategy for a single conflicting patch.

    Produced by StateMergingAnalyzer.propose_conflict_resolution and used
    when callers want to attempt a *forced* merge despite conflicts (e.g. in
    a repair pipeline or interactive editing session).

    Attributes
    ----------
    patch_id:
        The patch for which a resolution is proposed.
    strategy:
        One of:
        - "prefer_s1"      — use the section from σ1
        - "prefer_s2"      — use the section from σ2
        - "prefer_longer"  — use whichever section string is longer
        - "prefer_shorter" — use whichever section string is shorter
        - "reject"         — no automatic resolution; human review required
    resolved_section:
        The concrete section string to use after applying *strategy*, or None
        when strategy == "reject".
    confidence:
        Heuristic confidence in [0.0, 1.0] that the chosen strategy is correct.
    rationale:
        Human-readable explanation of why this strategy was chosen.
    """

    patch_id: str
    strategy: str
    resolved_section: Optional[str]
    confidence: float
    rationale: str


# ---------------------------------------------------------------------------
# StateMergingCoordinator
# ---------------------------------------------------------------------------

class StateMergingCoordinator:
    """Orchestrates state merging operations across a collection of SemanticStates.

    The coordinator is the primary entry point for merge operations.  It
    maintains a cache of previously computed merge results (keyed by the
    canonical "id1:id2" pair, where id1 < id2 lexicographically) to avoid
    redundant computation when the same pair is queried multiple times.

    Typical usage::

        coordinator = StateMergingCoordinator()
        result = coordinator.merge(state_a, state_b)
        if result.status == MergeStatus.SUCCESS:
            # result.merged_state is the new combined state
            ...

    Thread-safety: this class is *not* thread-safe; callers must synchronise
    external access if sharing a coordinator across threads.
    """

    def __init__(self) -> None:
        # Cache maps canonical key "minId:maxId" → MergeResult so that
        # repeated queries for the same pair return instantly.
        self._merge_cache: Dict[str, MergeResult] = {}
        # Running totals for observability / reporting.
        self._merge_count: int = 0
        self._conflict_count: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(s1: Any, s2: Any) -> str:
        """Return the canonical cache key for the unordered pair {s1, s2}.

        We always put the lexicographically smaller id first so that
        (A, B) and (B, A) map to the same cache entry.
        """
        id1 = s1.state_id if hasattr(s1, "state_id") else str(id(s1))
        id2 = s2.state_id if hasattr(s2, "state_id") else str(id(s2))
        if id1 > id2:
            id1, id2 = id2, id1
        return f"{id1}:{id2}"

    @staticmethod
    def _patch_assignments(s: Any) -> Dict[str, str]:
        """Safely extract patch_assignments from a state, returning {} on failure."""
        if hasattr(s, "patch_assignments") and isinstance(s.patch_assignments, dict):
            return s.patch_assignments
        return {}

    @staticmethod
    def _state_id(s: Any) -> str:
        """Safely extract state_id, falling back to repr."""
        return s.state_id if hasattr(s, "state_id") else repr(s)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def merge(self, s1: Any, s2: Any) -> MergeResult:
        """Attempt to merge two semantic states into a single combined state.

        The merge follows the sheaf-theoretic gluing axiom from theory2.tex
        §40.6: the states may be combined iff they agree on every patch that
        both of them have assigned.  When compatible, the resulting state's
        patch_assignments is the union of both assignment maps, with σ1 taking
        priority on any patch that (vacuously) exists in both with the same
        value.

        Parameters
        ----------
        s1, s2:
            SemanticState instances (or duck-typed equivalents) to merge.

        Returns
        -------
        MergeResult
            Full record of the merge outcome; check .status before using
            .merged_state.
        """
        cache_key = self._cache_key(s1, s2)
        if cache_key in self._merge_cache:
            logger.debug("Returning cached merge result for key=%s", cache_key)
            return self._merge_cache[cache_key]

        s1_id = self._state_id(s1)
        s2_id = self._state_id(s2)
        pa1 = self._patch_assignments(s1)
        pa2 = self._patch_assignments(s2)

        # ---- degenerate / trivial cases --------------------------------

        if not pa1 and not pa2:
            result = MergeResult(
                status=MergeStatus.EMPTY,
                merged_state=None,
                s1_id=s1_id,
                s2_id=s2_id,
                merged_id=None,
                conflicts=[],
                new_patches_gained=0,
                merge_benefit=0.0,
                message="Both states have empty patch_assignments; nothing to merge.",
            )
            self._merge_cache[cache_key] = result
            return result

        if pa1 == pa2:
            result = MergeResult(
                status=MergeStatus.IDENTICAL,
                merged_state=None,
                s1_id=s1_id,
                s2_id=s2_id,
                merged_id=None,
                conflicts=[],
                new_patches_gained=0,
                merge_benefit=0.0,
                message="States are identical; merge would produce no new information.",
            )
            self._merge_cache[cache_key] = result
            return result

        keys1 = set(pa1.keys())
        keys2 = set(pa2.keys())

        # Subset detection: if one domain is entirely contained in the other
        # and values agree on the intersection, the larger already subsumes the smaller.
        if keys1 <= keys2 or keys2 <= keys1:
            # Still need to check that the shared values agree.
            shared = keys1 & keys2
            conflicts_on_shared = [p for p in shared if pa1[p] != pa2[p]]
            if not conflicts_on_shared:
                result = MergeResult(
                    status=MergeStatus.SUBSET,
                    merged_state=None,
                    s1_id=s1_id,
                    s2_id=s2_id,
                    merged_id=None,
                    conflicts=[],
                    new_patches_gained=0,
                    merge_benefit=0.0,
                    message=(
                        f"{'s1' if keys1 <= keys2 else 's2'} is a subset of the other "
                        "with consistent values; the larger state already subsumes the smaller."
                    ),
                )
                self._merge_cache[cache_key] = result
                return result
            # Fall through to conflict handling below if values disagree.

        # ---- compatibility check on shared domain ----------------------

        shared_patches = keys1 & keys2
        conflict_patches: List[MergeConflict] = []
        for p in sorted(shared_patches):
            if pa1[p] != pa2[p]:
                conflict_patches.append(
                    MergeConflict(
                        patch_id=p,
                        section_in_s1=pa1[p],
                        section_in_s2=pa2[p],
                        resolution_hint=(
                            "prefer_longer"
                            if len(pa1[p]) != len(pa2[p])
                            else "reject"
                        ),
                    )
                )

        if conflict_patches:
            self._conflict_count += 1
            result = MergeResult(
                status=MergeStatus.CONFLICT,
                merged_state=None,
                s1_id=s1_id,
                s2_id=s2_id,
                merged_id=None,
                conflicts=conflict_patches,
                new_patches_gained=0,
                merge_benefit=0.0,
                message=(
                    f"Merge rejected: {len(conflict_patches)} conflicting patch(es) "
                    f"on shared domain of size {len(shared_patches)}."
                ),
            )
            self._merge_cache[cache_key] = result
            return result

        # ---- compatible: build the merged state ------------------------

        # Union of assignments; σ1 wins on any key in the shared domain
        # (values are identical at this point, so it makes no difference).
        merged_assignments: Dict[str, str] = {**pa2, **pa1}

        # Compute new_patches_gained: patches added beyond the larger domain.
        max_domain_size = max(len(keys1), len(keys2))
        new_patches_gained = len(merged_assignments) - max_domain_size

        merged_id = f"merged_{uuid.uuid4().hex[:12]}"

        # Combine obligations from both states.
        open_1: Set[str] = getattr(s1, "obligations_open", set()) or set()
        open_2: Set[str] = getattr(s2, "obligations_open", set()) or set()
        closed_1: Set[str] = getattr(s1, "obligations_closed", set()) or set()
        closed_2: Set[str] = getattr(s2, "obligations_closed", set()) or set()

        # An obligation that is closed in *either* state is treated as closed
        # in the merged state (optimistic assumption; closed ≥ open in the
        # obligation lattice).
        merged_closed = closed_1 | closed_2
        # Open obligations: union minus anything already closed.
        merged_open = (open_1 | open_2) - merged_closed

        # The merged state's generation round is the maximum of the two inputs
        # so that it is treated as "at least as advanced" as either parent.
        gen_round_1: int = getattr(s1, "generation_round", 0) or 0
        gen_round_2: int = getattr(s2, "generation_round", 0) or 0
        merged_round = max(gen_round_1, gen_round_2)

        # Propagate terminal / goal flags: the merged state is goal iff both
        # parents are, and terminal iff either parent is terminal (conservative).
        is_goal = bool(getattr(s1, "is_goal_state", False)) and bool(
            getattr(s2, "is_goal_state", False)
        )
        is_terminal = bool(getattr(s1, "is_terminal", False)) or bool(
            getattr(s2, "is_terminal", False)
        )

        # Provenance metadata: record which states were merged.
        meta_1: Dict[str, Any] = dict(getattr(s1, "metadata", {}) or {})
        meta_2: Dict[str, Any] = dict(getattr(s2, "metadata", {}) or {})
        merged_meta: Dict[str, Any] = {**meta_2, **meta_1}
        merged_meta["_merge_provenance"] = {
            "parent_1": s1_id,
            "parent_2": s2_id,
            "merged_at": time.time(),
            "new_patches_gained": new_patches_gained,
        }

        # Attempt to construct a proper SemanticState if the models are
        # available; otherwise build a duck-typed namespace object.
        if _MODELS_AVAILABLE:
            try:
                merged_state = SemanticState(  # type: ignore[call-arg]
                    state_id=merged_id,
                    patch_assignments=merged_assignments,
                    obligations_open=merged_open,
                    obligations_closed=merged_closed,
                    generation_round=merged_round,
                    is_terminal=is_terminal,
                    is_goal_state=is_goal,
                    metadata=merged_meta,
                )
            except Exception as exc:  # pragma: no cover — defensive fallback
                logger.warning("SemanticState construction failed (%s); using namespace.", exc)
                merged_state = _FallbackState(
                    state_id=merged_id,
                    patch_assignments=merged_assignments,
                    obligations_open=merged_open,
                    obligations_closed=merged_closed,
                    generation_round=merged_round,
                    is_terminal=is_terminal,
                    is_goal_state=is_goal,
                    metadata=merged_meta,
                )
        else:
            merged_state = _FallbackState(
                state_id=merged_id,
                patch_assignments=merged_assignments,
                obligations_open=merged_open,
                obligations_closed=merged_closed,
                generation_round=merged_round,
                is_terminal=is_terminal,
                is_goal_state=is_goal,
                metadata=merged_meta,
            )

        total_merged = len(merged_assignments)
        merge_benefit = new_patches_gained / total_merged if total_merged > 0 else 0.0

        self._merge_count += 1
        result = MergeResult(
            status=MergeStatus.SUCCESS,
            merged_state=merged_state,
            s1_id=s1_id,
            s2_id=s2_id,
            merged_id=merged_id,
            conflicts=[],
            new_patches_gained=new_patches_gained,
            merge_benefit=merge_benefit,
            message=(
                f"Merge succeeded: {new_patches_gained} new patch(es) gained "
                f"(benefit={merge_benefit:.3f})."
            ),
        )
        self._merge_cache[cache_key] = result
        logger.info(
            "Merged states %s + %s → %s (%d new patches)",
            s1_id,
            s2_id,
            merged_id,
            new_patches_gained,
        )
        return result

    def can_merge(self, s1: Any, s2: Any) -> bool:
        """Return True iff s1 and s2 are compatible on their shared patch domain.

        This is a lightweight check that does *not* construct the merged state
        and does *not* update the merge cache.  Use it for pre-filtering large
        candidate lists before calling the heavier :meth:`merge`.

        Parameters
        ----------
        s1, s2:
            States to test for compatibility.

        Returns
        -------
        bool
            True when every patch present in both states has the same section
            assignment, False otherwise.
        """
        pa1 = self._patch_assignments(s1)
        pa2 = self._patch_assignments(s2)
        shared = set(pa1.keys()) & set(pa2.keys())
        return all(pa1[p] == pa2[p] for p in shared)

    def merge_batch(self, states: List[Any]) -> List[MergeResult]:
        """Try all unordered pairs within *states* and return successful merges.

        The method iterates over the O(n²) pair set, skips pairs that are
        already known to be incompatible via :meth:`can_merge`, and appends
        the MergeResult to the output list only when the status is SUCCESS.

        Parameters
        ----------
        states:
            Collection of SemanticState (or compatible) objects.

        Returns
        -------
        List[MergeResult]
            All successful merge results, in the order they were attempted.
        """
        results: List[MergeResult] = []
        for s1, s2 in itertools.combinations(states, 2):
            # Quick reject before paying the cost of a full merge.
            if not self.can_merge(s1, s2):
                logger.debug(
                    "Skipping incompatible pair (%s, %s)",
                    self._state_id(s1),
                    self._state_id(s2),
                )
                continue
            result = self.merge(s1, s2)
            if result.status == MergeStatus.SUCCESS:
                results.append(result)
        return results

    def find_mergeable_pairs(self, states: List[Any]) -> List[Tuple[str, str]]:
        """Return the list of (state_id_1, state_id_2) pairs that can be merged.

        Unlike :meth:`merge_batch`, this method does *not* perform the actual
        merge; it only identifies which pairs pass the compatibility test.  This
        is useful when the caller wants to present merge options to a user or
        planner before committing.

        Parameters
        ----------
        states:
            Collection of states to screen.

        Returns
        -------
        List[Tuple[str, str]]
            Pairs of state_ids, with id1 < id2 lexicographically (canonical
            ordering matching the cache key).
        """
        pairs: List[Tuple[str, str]] = []
        for s1, s2 in itertools.combinations(states, 2):
            if self.can_merge(s1, s2):
                id1 = self._state_id(s1)
                id2 = self._state_id(s2)
                # Canonical ordering.
                if id1 > id2:
                    id1, id2 = id2, id1
                pairs.append((id1, id2))
        return pairs


# ---------------------------------------------------------------------------
# StateMergingAnalyzer
# ---------------------------------------------------------------------------

class StateMergingAnalyzer:
    """Provides analytical tools for understanding merge compatibility and benefit.

    Unlike the coordinator (which creates merged states), the analyzer only
    *examines* the relationship between two states and returns structured data
    to help callers decide how — or whether — to merge them.
    """

    @staticmethod
    def _patch_assignments(s: Any) -> Dict[str, str]:
        """Safely retrieve patch_assignments, returning {} on failure."""
        if hasattr(s, "patch_assignments") and isinstance(s.patch_assignments, dict):
            return s.patch_assignments
        return {}

    @staticmethod
    def _state_id(s: Any) -> str:
        """Safely retrieve state_id."""
        return s.state_id if hasattr(s, "state_id") else repr(s)

    def compute_compatibility(self, s1: Any, s2: Any) -> CompatibilityScore:
        """Compute a full compatibility score for the pair (s1, s2).

        The returned CompatibilityScore bundles every metric needed to decide
        whether and how profitably to merge the two states.

        Algorithm:
        1. Compute the shared domain and check value agreement on each patch.
        2. Derive *score* = compatible_shared / shared_patch_count (1.0 when
           shared_patch_count == 0 — vacuous compatibility).
        3. Compute Jaccard similarity of the two domains.

        Parameters
        ----------
        s1, s2:
            States to analyse.

        Returns
        -------
        CompatibilityScore
            Fully populated score object.
        """
        pa1 = self._patch_assignments(s1)
        pa2 = self._patch_assignments(s2)
        keys1 = set(pa1.keys())
        keys2 = set(pa2.keys())
        shared = keys1 & keys2
        union = keys1 | keys2

        shared_count = len(shared)
        conflict_count = sum(1 for p in shared if pa1[p] != pa2[p])
        compatible_shared = shared_count - conflict_count

        # Compatibility score: fraction of shared patches that agree.
        score = (compatible_shared / shared_count) if shared_count > 0 else 1.0

        # Jaccard similarity of domains (independent of values).
        jaccard = (len(shared) / len(union)) if union else 1.0

        return CompatibilityScore(
            s1_id=self._state_id(s1),
            s2_id=self._state_id(s2),
            score=score,
            shared_patch_count=shared_count,
            conflict_count=conflict_count,
            compatible_shared=compatible_shared,
            jaccard_similarity=jaccard,
            is_compatible=(conflict_count == 0),
        )

    def identify_conflict_patches(self, s1: Any, s2: Any) -> List[str]:
        """Return the list of patch_ids where σ1 and σ2 disagree.

        This is a pure diagnostic function that returns the raw list of
        conflicting patch identifiers without any resolution attempt.

        Parameters
        ----------
        s1, s2:
            States to compare.

        Returns
        -------
        List[str]
            Sorted list of patch_ids where pa1[p] != pa2[p].
        """
        pa1 = self._patch_assignments(s1)
        pa2 = self._patch_assignments(s2)
        shared = set(pa1.keys()) & set(pa2.keys())
        return sorted(p for p in shared if pa1[p] != pa2[p])

    def estimate_merge_benefit(self, s1: Any, s2: Any) -> float:
        """Estimate the normalised informational benefit of merging s1 and s2.

        Benefit is defined as the fraction of the merged domain that consists
        of *new* patches (patches not already present in the larger of the two
        input domains).  Returns 0.0 immediately if the pair has any conflicts,
        since a conflicted merge is infeasible.

        The formula is:
            benefit = new_patches_gained / |dom(σ1) ∪ dom(σ2)|

        where new_patches_gained = |dom(σ1) ∪ dom(σ2)| − max(|dom(σ1)|, |dom(σ2)|).

        Parameters
        ----------
        s1, s2:
            States to evaluate.

        Returns
        -------
        float
            A value in [0.0, 1.0]; 0.0 for conflicted or non-beneficial merges.
        """
        pa1 = self._patch_assignments(s1)
        pa2 = self._patch_assignments(s2)
        keys1 = set(pa1.keys())
        keys2 = set(pa2.keys())
        shared = keys1 & keys2

        # If any shared patch conflicts, benefit is 0 (merge not feasible).
        if any(pa1[p] != pa2[p] for p in shared):
            return 0.0

        union_size = len(keys1 | keys2)
        if union_size == 0:
            return 0.0

        max_size = max(len(keys1), len(keys2))
        new_patches = union_size - max_size
        return new_patches / union_size

    def propose_conflict_resolution(
        self, s1: Any, s2: Any, patch: str
    ) -> ConflictResolution:
        """Propose a heuristic resolution strategy for a specific conflict patch.

        Heuristics applied in order:
        1. If one state was generated at a higher generation_round, prefer that
           state's assignment (it is "more refined").
        2. If generation_rounds are equal, prefer the longer section string
           (it likely contains more detail).
        3. If section strings are the same length, fall back to "reject" and
           require human review.

        Parameters
        ----------
        s1, s2:
            States with a conflict on *patch*.
        patch:
            The patch_id for which to propose a resolution.

        Returns
        -------
        ConflictResolution
            Proposed resolution with strategy, resolved_section, confidence,
            and rationale.
        """
        pa1 = self._patch_assignments(s1)
        pa2 = self._patch_assignments(s2)

        sec1 = pa1.get(patch, "")
        sec2 = pa2.get(patch, "")

        gr1: int = getattr(s1, "generation_round", 0) or 0
        gr2: int = getattr(s2, "generation_round", 0) or 0

        if gr1 != gr2:
            # Prefer the state with the higher (more refined) generation round.
            if gr1 > gr2:
                strategy = "prefer_s1"
                resolved_section: Optional[str] = sec1
                confidence = 0.75
                rationale = (
                    f"s1 has higher generation_round ({gr1} > {gr2}); "
                    "it is considered more refined."
                )
            else:
                strategy = "prefer_s2"
                resolved_section = sec2
                confidence = 0.75
                rationale = (
                    f"s2 has higher generation_round ({gr2} > {gr1}); "
                    "it is considered more refined."
                )
        elif len(sec1) != len(sec2):
            # Prefer the longer section string as a proxy for more detail.
            if len(sec1) > len(sec2):
                strategy = "prefer_longer"
                resolved_section = sec1
                confidence = 0.55
                rationale = (
                    f"Section in s1 is longer ({len(sec1)} > {len(sec2)} chars); "
                    "longer text is heuristically preferred."
                )
            else:
                strategy = "prefer_longer"
                resolved_section = sec2
                confidence = 0.55
                rationale = (
                    f"Section in s2 is longer ({len(sec2)} > {len(sec1)} chars); "
                    "longer text is heuristically preferred."
                )
        else:
            # Cannot distinguish; require human review.
            strategy = "reject"
            resolved_section = None
            confidence = 0.0
            rationale = (
                "Both states have the same generation_round and equal-length "
                "section strings for this patch; automated resolution is not safe."
            )

        return ConflictResolution(
            patch_id=patch,
            strategy=strategy,
            resolved_section=resolved_section,
            confidence=confidence,
            rationale=rationale,
        )


# ---------------------------------------------------------------------------
# StateMergingWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StateMergingWitness:
    """Immutable, hashable witness record for a single merge event.

    A witness is a lightweight, archival snapshot of a MergeResult that can be
    stored in a database, serialised to JSON, or accumulated in a witness lattice
    without retaining references to the potentially large merged_state object.

    Because this class uses ``frozen=True`` and ``slots=True``, instances are
    both immutable and memory-efficient.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness record (UUID hex string).
    state_id_1, state_id_2:
        Identifiers of the two parent states.
    merged_state_id:
        Identifier of the merged state, or None if the merge was not successful.
    compatible:
        True iff the merge attempt resulted in SUCCESS (i.e. no conflicts).
    conflict_patches:
        Tuple of patch_ids that were in conflict (empty for successful merges).
    merge_benefit:
        Normalised benefit from the MergeResult; 0.0 for non-successful merges.
    timestamp:
        Wall-clock time at which the MergeResult was created.
    """

    witness_id: str
    state_id_1: str
    state_id_2: str
    merged_state_id: Optional[str]
    compatible: bool
    conflict_patches: Tuple[str, ...]
    merge_benefit: float
    timestamp: float

    @classmethod
    def from_result(cls, result: MergeResult) -> "StateMergingWitness":
        """Construct a StateMergingWitness from a MergeResult.

        Parameters
        ----------
        result:
            A completed MergeResult (any status).

        Returns
        -------
        StateMergingWitness
            Immutable witness capturing the essential outcome of *result*.
        """
        return cls(
            witness_id=uuid.uuid4().hex,
            state_id_1=result.s1_id,
            state_id_2=result.s2_id,
            merged_state_id=result.merged_id,
            compatible=(result.status == MergeStatus.SUCCESS),
            conflict_patches=tuple(c.patch_id for c in result.conflicts),
            merge_benefit=result.merge_benefit,
            timestamp=result.timestamp,
        )


# ---------------------------------------------------------------------------
# Internal fallback state (used when models are not available)
# ---------------------------------------------------------------------------

class _FallbackState:
    """Minimal duck-type substitute for SemanticState used when models are absent.

    This class ensures that the merge logic can produce *something* sensible
    even in environments where the full jugeo.generation.state_space.models
    package is not installed or importable.
    """

    def __init__(
        self,
        *,
        state_id: str,
        patch_assignments: Dict[str, str],
        obligations_open: Set[str],
        obligations_closed: Set[str],
        generation_round: int,
        is_terminal: bool,
        is_goal_state: bool,
        metadata: Dict[str, Any],
    ) -> None:
        self.state_id = state_id
        self.patch_assignments = patch_assignments
        self.obligations_open = obligations_open
        self.obligations_closed = obligations_closed
        self.generation_round = generation_round
        self.is_terminal = is_terminal
        self.is_goal_state = is_goal_state
        self.metadata = metadata

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"_FallbackState(state_id={self.state_id!r}, "
            f"patches={list(self.patch_assignments.keys())})"
        )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def merge_states(s1: Any, s2: Any) -> MergeResult:
    """Convenience wrapper: merge two states using a fresh StateMergingCoordinator.

    This function is intended for one-off merge calls where the caller does not
    need to retain the coordinator (and its cache) across multiple operations.
    For repeated or batch merging, instantiate a StateMergingCoordinator directly
    so that the merge cache is shared.

    Parameters
    ----------
    s1, s2:
        States to merge.

    Returns
    -------
    MergeResult
        The merge outcome.
    """
    return StateMergingCoordinator().merge(s1, s2)


def find_all_merge_candidates(states: List[Any]) -> List[Tuple[str, str]]:
    """Convenience wrapper: find all mergeable pairs in a list of states.

    Uses a fresh StateMergingCoordinator for the compatibility checks.  For
    large state collections, consider reusing a coordinator instance to benefit
    from the internal merge cache.

    Parameters
    ----------
    states:
        Collection of states to screen for mergeable pairs.

    Returns
    -------
    List[Tuple[str, str]]
        List of (state_id_1, state_id_2) pairs that can be merged, in
        canonical (lexicographic) order.
    """
    return StateMergingCoordinator().find_mergeable_pairs(states)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke_test() -> None:
    """Exercise the main merging machinery with several hand-crafted states.

    This function is invoked when the module is run directly (``python -m
    jugeo.generation.state_space.state_merging``).  It demonstrates:

    1. Merging two disjoint states (SUCCESS, maximum new_patches_gained).
    2. Merging two identical states (IDENTICAL short-circuit).
    3. Merging two conflicting states (CONFLICT with MergeConflict records).
    4. Merging partially overlapping states with agreement (SUCCESS).
    5. Subset detection (SUBSET short-circuit).
    6. Batch merging of a mixed collection.
    7. Compatibility scoring and conflict-resolution proposals via the Analyzer.
    8. Witness construction from a MergeResult.
    """
    print("=" * 70)
    print("state_merging — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Build some test states using _FallbackState so the test works even
    # without the models package.
    # ------------------------------------------------------------------

    def make_state(
        state_id: str,
        patches: Dict[str, str],
        gen_round: int = 1,
        open_obs: Optional[Set[str]] = None,
        closed_obs: Optional[Set[str]] = None,
    ) -> _FallbackState:
        return _FallbackState(
            state_id=state_id,
            patch_assignments=patches,
            obligations_open=open_obs or set(),
            obligations_closed=closed_obs or set(),
            generation_round=gen_round,
            is_terminal=False,
            is_goal_state=False,
            metadata={},
        )

    # Disjoint states: no shared patches → compatible by vacuity.
    s_alpha = make_state("alpha", {"p1": "sec_A", "p2": "sec_B"}, gen_round=1)
    s_beta = make_state("beta", {"p3": "sec_C", "p4": "sec_D"}, gen_round=2)

    # Overlapping states that agree on the shared patch p2.
    s_gamma = make_state("gamma", {"p2": "sec_B", "p5": "sec_E"}, gen_round=1)

    # Conflicting state: p2 has a different value than in s_alpha.
    s_delta = make_state("delta", {"p2": "DIFFERENT", "p6": "sec_F"}, gen_round=3)

    # Identical copy of s_alpha.
    s_alpha_copy = make_state("alpha_copy", {"p1": "sec_A", "p2": "sec_B"}, gen_round=1)

    # Subset state: keys of s_sub are a strict subset of s_alpha.
    s_sub = make_state("sub", {"p1": "sec_A"}, gen_round=1)

    coordinator = StateMergingCoordinator()
    analyzer = StateMergingAnalyzer()

    # -- 1. Disjoint merge (expected: SUCCESS) ----------------------------
    r1 = coordinator.merge(s_alpha, s_beta)
    print(f"\n[1] Disjoint merge: {r1.status.name} — {r1.message}")
    assert r1.status == MergeStatus.SUCCESS
    assert r1.new_patches_gained == 2  # p3 and p4 are new
    assert r1.merged_state is not None
    merged_keys = set(r1.merged_state.patch_assignments.keys())
    assert merged_keys == {"p1", "p2", "p3", "p4"}, merged_keys
    print(f"    Merged patches: {sorted(merged_keys)}")

    # -- 2. Identical states (expected: IDENTICAL) -----------------------
    r2 = coordinator.merge(s_alpha, s_alpha_copy)
    print(f"\n[2] Identical states: {r2.status.name} — {r2.message}")
    assert r2.status == MergeStatus.IDENTICAL

    # -- 3. Conflicting states (expected: CONFLICT) ----------------------
    r3 = coordinator.merge(s_alpha, s_delta)
    print(f"\n[3] Conflicting merge: {r3.status.name} — {r3.message}")
    assert r3.status == MergeStatus.CONFLICT
    assert len(r3.conflicts) == 1
    conflict = r3.conflicts[0]
    print(
        f"    Conflict on '{conflict.patch_id}': "
        f"'{conflict.section_in_s1}' vs '{conflict.section_in_s2}' "
        f"(hint: {conflict.resolution_hint})"
    )

    # -- 4. Partially overlapping, compatible (expected: SUCCESS) --------
    r4 = coordinator.merge(s_alpha, s_gamma)
    print(f"\n[4] Overlapping compatible merge: {r4.status.name} — {r4.message}")
    assert r4.status == MergeStatus.SUCCESS
    assert r4.new_patches_gained == 1  # p5 is new
    merged_keys_4 = set(r4.merged_state.patch_assignments.keys())
    assert merged_keys_4 == {"p1", "p2", "p5"}, merged_keys_4
    print(f"    Merged patches: {sorted(merged_keys_4)}")

    # -- 5. Subset detection (expected: SUBSET) --------------------------
    r5 = coordinator.merge(s_alpha, s_sub)
    print(f"\n[5] Subset detection: {r5.status.name} — {r5.message}")
    assert r5.status == MergeStatus.SUBSET

    # -- 6. Batch merging ------------------------------------------------
    all_states = [s_alpha, s_beta, s_gamma, s_delta, s_sub]
    batch_results = coordinator.merge_batch(all_states)
    print(f"\n[6] Batch merge: {len(batch_results)} successful merge(s) from {len(all_states)} states")
    for br in batch_results:
        print(f"    {br.s1_id} ⊔ {br.s2_id} → {br.merged_id} (+{br.new_patches_gained} patches)")

    # -- 7. Compatibility analysis and conflict resolution ---------------
    cs = analyzer.compute_compatibility(s_alpha, s_delta)
    print(
        f"\n[7] Compatibility(alpha, delta): score={cs.score:.2f}, "
        f"conflicts={cs.conflict_count}, jaccard={cs.jaccard_similarity:.2f}, "
        f"is_compatible={cs.is_compatible}"
    )
    assert not cs.is_compatible

    conflict_patches = analyzer.identify_conflict_patches(s_alpha, s_delta)
    print(f"    Conflict patches: {conflict_patches}")

    benefit = analyzer.estimate_merge_benefit(s_alpha, s_beta)
    print(f"    Merge benefit (alpha, beta): {benefit:.3f}")
    assert benefit > 0.0

    resolution = analyzer.propose_conflict_resolution(s_alpha, s_delta, "p2")
    print(
        f"    Resolution for 'p2': strategy={resolution.strategy}, "
        f"confidence={resolution.confidence:.2f}, rationale={resolution.rationale}"
    )

    # -- 8. Witness construction ----------------------------------------
    witness = StateMergingWitness.from_result(r1)
    print(
        f"\n[8] Witness: id={witness.witness_id[:8]}…, "
        f"compatible={witness.compatible}, "
        f"benefit={witness.merge_benefit:.3f}, "
        f"conflict_patches={witness.conflict_patches}"
    )
    assert witness.compatible
    assert witness.merged_state_id == r1.merged_id

    # -- 9. find_all_merge_candidates convenience function ---------------
    pairs = find_all_merge_candidates([s_alpha, s_beta, s_gamma])
    print(f"\n[9] find_all_merge_candidates: {len(pairs)} pair(s) found")
    for p in pairs:
        print(f"    {p[0]} ↔ {p[1]}")

    # -- 10. Cache hit verification --------------------------------------
    r1_cached = coordinator.merge(s_alpha, s_beta)
    assert r1_cached is r1, "Expected the same MergeResult object from cache"
    print("\n[10] Cache hit confirmed (same MergeResult object returned).")

    print("\n" + "=" * 70)
    print("All smoke-test assertions passed.")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _smoke_test()
