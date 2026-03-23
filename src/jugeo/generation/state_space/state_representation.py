r"""Chapter 40, Section 1 — State space representation.

Theory (theory2.tex §40.1):
    A semantic state σ is a partial function σ: P →_partial S from patches to sections.
    The state space Σ = {σ | σ: P →_partial S} is the search graph.
    States are nodes; transitions are edges. To navigate Σ efficiently we need:
      (a) a canonical encoding (fingerprint) for deduplication,
      (b) comparison operators for the lattice Σ under refinement order,
      (c) a registry to store/recall states by ID or fingerprint.
    The refinement order ≤ is defined by: σ1 ≤ σ2 iff dom(σ1) ⊆ dom(σ2) and
    ∀p ∈ dom(σ1): σ1(p) = σ2(p). The lattice join σ1 ⊔ σ2 exists iff σ1 and σ2
    are compatible (agree on shared patches); the meet σ1 ⊓ σ2 is the restriction
    of either to the common domain where they agree.

# copilot: s01-state-representation
"""

from __future__ import annotations

import hashlib
import json
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
        StateTransition,
        GenerationStateSpace,
        ConvergenceMetric,
        make_initial_state,
        compute_state_fingerprint,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    StateTransition = Any
    GenerationStateSpace = Any
    ConvergenceMetric = Any

    def make_initial_state(patches):  # type: ignore[misc]
        return None

    def compute_state_fingerprint(state):  # type: ignore[misc]
        return ""

__all__ = [
    "StateComparisonResult",
    "StateDiff",
    "StateRepresentationCoordinator",
    "StateRepresentationAnalyzer",
    "StateRepresentationWitness",
    "encode_state",
    "decode_state",
    "fingerprint_state",
]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class StateComparisonResult:
    """The result of a structural comparison between two semantic states.

    This dataclass captures the full picture of how two states σ1 and σ2
    relate to each other in the lattice Σ under the refinement order.  It
    records which patches appear only in σ1, only in σ2, or in both — and
    whether the two states agree where they overlap.

    Theory (§40.1.3):
        Two states are *compatible* if they agree on all shared patches, i.e.
        ∀p ∈ dom(σ1) ∩ dom(σ2): σ1(p) = σ2(p).
        Conflict patches are those in the intersection where the section
        assignments differ.

    Attributes:
        are_equal: True iff dom(σ1) == dom(σ2) and all assignments agree.
        s1_id: The state_id of the first operand.
        s2_id: The state_id of the second operand.
        shared_patches: Patches that appear in both domains.
        s1_only_patches: Patches that appear only in dom(σ1).
        s2_only_patches: Patches that appear only in dom(σ2).
        conflict_patches: Patches in the shared domain where σ1 and σ2 assign
            different sections.
        is_refinement_s1_of_s2: True iff σ1 is a refinement of σ2, meaning
            dom(σ2) ⊆ dom(σ1) and they agree on dom(σ2).
        is_refinement_s2_of_s1: True iff σ2 is a refinement of σ1.
        distance: Jaccard distance ∈ [0, 1] measuring assignment-set divergence.
        summary: A human-readable one-liner summarising the comparison.
    """

    are_equal: bool
    s1_id: str
    s2_id: str
    shared_patches: List[str]
    s1_only_patches: List[str]
    s2_only_patches: List[str]
    conflict_patches: List[str]
    is_refinement_s1_of_s2: bool
    is_refinement_s2_of_s1: bool
    distance: float
    summary: str


@dataclass(frozen=True, slots=True)
class StateDiff:
    """A single atomic change between two successive semantic states.

    Each StateDiff records what happened to one patch when moving from state
    σ1 to state σ2.  The diff_type is one of three values:

    * ``"added"``   — the patch was not in dom(σ1) but is now in dom(σ2).
    * ``"removed"`` — the patch was in dom(σ1) but is no longer in dom(σ2).
    * ``"changed"`` — the patch is in both domains but the section changed.

    Attributes:
        patch_id: The identifier of the patch that changed.
        diff_type: One of ``"added"``, ``"removed"``, or ``"changed"``.
        old_section: The section assigned in σ1, or ``None`` if the patch was
            absent from σ1 (i.e. diff_type is ``"added"``).
        new_section: The section assigned in σ2, or ``None`` if the patch is
            absent from σ2 (i.e. diff_type is ``"removed"``).

    Examples:
        >>> d = StateDiff("p3", "added", None, "sec_7")
        >>> d.patch_id
        'p3'
        >>> d.diff_type
        'added'
    """

    patch_id: str
    diff_type: str          # "added" | "removed" | "changed"
    old_section: Optional[str]
    new_section: Optional[str]


# ---------------------------------------------------------------------------
# StateRepresentationCoordinator
# ---------------------------------------------------------------------------

class StateRepresentationCoordinator:
    """Manages a registry of semantic states indexed by both ID and fingerprint.

    The coordinator is the authoritative in-memory store for all states that
    have been discovered during a generation run.  It fulfils three roles:

    1. **Deduplication** — before inserting a new state it checks the
       fingerprint index; if an equivalent state already exists it returns the
       existing ID rather than creating a duplicate node in Σ.
    2. **Retrieval** — states can be looked up by their UUID ``state_id`` in
       O(1) time.
    3. **Bookkeeping** — the coordinator tracks access counts so that rarely-
       visited states can be identified for eviction in memory-constrained
       settings.

    The fingerprint used for deduplication is a SHA-256 hash of the canonical
    JSON representation of (patch_assignments sorted by key, obligations_open
    sorted, is_goal_state).  This is sufficient because the semantics of a
    state are fully determined by these three components.

    Attributes:
        _registry: Maps state_id → SemanticState.
        _fingerprint_index: Maps SHA-256 fingerprint → state_id for
            fast duplicate detection.
        _access_count: Maps state_id → number of times lookup() was called.
    """

    def __init__(self) -> None:
        """Initialise an empty coordinator with empty registry and indexes."""
        # Primary store: state_id → state object
        self._registry: Dict[str, Any] = {}
        # Secondary index: fingerprint hex → state_id (for dedup)
        self._fingerprint_index: Dict[str, str] = {}
        # Usage counter so callers can inspect access patterns
        self._access_count: Dict[str, int] = {}
        logger.debug("StateRepresentationCoordinator initialised (empty registry)")

    # ------------------------------------------------------------------
    # Core registry operations
    # ------------------------------------------------------------------

    def register(self, state: Any) -> str:
        """Register a semantic state and return its canonical state_id.

        If an equivalent state (same fingerprint) is already present in the
        registry, this method returns the *existing* state_id without
        inserting a duplicate.  Otherwise the new state is stored and its
        own state_id is returned.

        The fingerprint is computed via :meth:`compute_fingerprint`.  The
        operation is O(1) amortised because both the registry and the
        fingerprint index are hash maps.

        Args:
            state: A SemanticState (or compatible duck-typed object) to store.

        Returns:
            The state_id string that should be used to retrieve this state.
            May be the incoming state's own ID or an existing equivalent's ID.

        Raises:
            AttributeError: If the state object lacks a ``state_id`` attribute.

        Examples:
            >>> coord = StateRepresentationCoordinator()
            >>> sid = coord.register(some_state)
            >>> coord.lookup(sid) is not None
            True
        """
        # Compute the canonical fingerprint for this state.
        fp = self.compute_fingerprint(state)

        # Check whether an equivalent state already lives in the registry.
        existing_id = self._fingerprint_index.get(fp)
        if existing_id is not None:
            # We already have this state — return the existing ID so the caller
            # can use the canonical reference and we avoid duplicate nodes.
            logger.debug(
                "register: fingerprint %s already known as state_id=%s — skipping insert",
                fp[:12],
                existing_id,
            )
            # Bump the access count for the canonical state to reflect the
            # additional encounter.
            self._access_count[existing_id] = self._access_count.get(existing_id, 0) + 1
            return existing_id

        # New state — store it.
        sid = getattr(state, "state_id", None) or str(uuid.uuid4())
        self._registry[sid] = state
        self._fingerprint_index[fp] = sid
        self._access_count[sid] = 0
        logger.debug(
            "register: stored new state state_id=%s fingerprint=%s", sid, fp[:12]
        )
        return sid

    def lookup(self, state_id: str) -> Optional[Any]:
        """Return the state with the given ID, or None if not registered.

        Each successful lookup increments the access counter for the state,
        which can be used for LRU-style eviction policies.

        Args:
            state_id: The UUID string identifying the desired state.

        Returns:
            The SemanticState object, or None if ``state_id`` is unknown.

        Examples:
            >>> coord = StateRepresentationCoordinator()
            >>> coord.lookup("nonexistent") is None
            True
        """
        state = self._registry.get(state_id)
        if state is not None:
            # Record that this state was accessed.
            self._access_count[state_id] = self._access_count.get(state_id, 0) + 1
        return state

    def find_equivalent(self, state: Any) -> Optional[Any]:
        """Return an already-registered state that is equivalent to ``state``.

        Equivalence is defined by matching SHA-256 fingerprints.  If no
        equivalent state is in the registry, returns None.

        This is used by higher-level search algorithms to avoid re-expanding
        states that have already been explored under a different ID.

        Args:
            state: A semantic state to look up by fingerprint.

        Returns:
            The existing equivalent SemanticState, or None.
        """
        fp = self.compute_fingerprint(state)
        existing_id = self._fingerprint_index.get(fp)
        if existing_id is None:
            return None
        # Delegate to lookup so the access counter is updated.
        return self.lookup(existing_id)

    def get_all_states(self) -> List[Any]:
        """Return all registered states as a list.

        The order is not guaranteed to be stable across Python versions or
        interpreter runs; callers that require a deterministic order should
        sort the returned list themselves.

        Returns:
            A list of all SemanticState objects currently in the registry.
        """
        return list(self._registry.values())

    def size(self) -> int:
        """Return the number of distinct states currently in the registry.

        Returns:
            Non-negative integer count of registered states.
        """
        return len(self._registry)

    def remove(self, state_id: str) -> bool:
        """Remove a state from the registry by ID.

        This also removes the corresponding fingerprint index entry so that
        a future equivalent state can be re-registered without a phantom hit.

        Args:
            state_id: The ID of the state to remove.

        Returns:
            True if the state was present and has been removed; False if it
            was not found.
        """
        if state_id not in self._registry:
            logger.debug("remove: state_id=%s not found — nothing to remove", state_id)
            return False

        # Remove from primary store.
        del self._registry[state_id]

        # Remove matching fingerprint entry (scan by value — infrequent op).
        fp_to_remove = None
        for fp, sid in self._fingerprint_index.items():
            if sid == state_id:
                fp_to_remove = fp
                break
        if fp_to_remove is not None:
            del self._fingerprint_index[fp_to_remove]

        # Remove access count record.
        self._access_count.pop(state_id, None)

        logger.debug("remove: evicted state_id=%s", state_id)
        return True

    def get_access_count(self, state_id: str) -> int:
        """Return the number of times a state has been accessed via lookup().

        Args:
            state_id: The ID of the state to query.

        Returns:
            Access count as a non-negative integer.  Returns 0 if the state
            is not registered (rather than raising).
        """
        return self._access_count.get(state_id, 0)

    # ------------------------------------------------------------------
    # Fingerprinting
    # ------------------------------------------------------------------

    def compute_fingerprint(self, state: Any) -> str:
        """Compute a SHA-256 fingerprint for a semantic state.

        The fingerprint encodes the three components that together determine
        the identity of a state in the lattice:

        1. ``patch_assignments`` — a dict mapping patch IDs to section IDs.
           We sort by key before serialising so that insertion order does not
           affect the fingerprint.
        2. ``obligations_open`` — the set of still-open obligations.  Sorted
           for determinism.
        3. ``is_goal_state`` — whether the state is a terminal goal state.

        The resulting string is the hexadecimal SHA-256 digest (64 chars).

        Args:
            state: A SemanticState or duck-typed object with the above
                attributes (or equivalent dict structure).

        Returns:
            A 64-character lowercase hexadecimal string.

        Examples:
            >>> coord = StateRepresentationCoordinator()
            >>> fp = coord.compute_fingerprint(some_state)
            >>> len(fp)
            64
        """
        # Extract the relevant attributes, handling both proper SemanticState
        # objects and plain dicts (e.g. during testing without models).
        if isinstance(state, dict):
            patch_assignments = state.get("patch_assignments", {})
            obligations_open = state.get("obligations_open", set())
            is_goal = state.get("is_goal_state", False)
        else:
            patch_assignments = getattr(state, "patch_assignments", {}) or {}
            obligations_open = getattr(state, "obligations_open", set()) or set()
            is_goal = getattr(state, "is_goal_state", False)

        # Build the canonical representation as a plain Python structure.
        canonical = {
            "pa": {k: patch_assignments[k] for k in sorted(patch_assignments)},
            "oo": sorted(obligations_open),
            "goal": bool(is_goal),
        }

        # Serialise to JSON with sorted keys for extra safety, then hash.
        canonical_bytes = json.dumps(canonical, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        return digest


# ---------------------------------------------------------------------------
# StateRepresentationAnalyzer
# ---------------------------------------------------------------------------

class StateRepresentationAnalyzer:
    """Provides structural analysis of semantic states in the lattice Σ.

    The analyzer implements the theoretical operations defined in §40.1.2:

    * **Comparison** — determines the relationship between two states,
      including shared/disjoint patches, conflicts, and refinement order.
    * **Distance** — computes Jaccard distance on assignment sets.
    * **Diff** — produces a human-readable list of atomic changes.
    * **Lattice join/meet** — computes σ1 ⊔ σ2 and σ1 ⊓ σ2 when they exist.

    Comparison results are cached using a two-key string ``"id1:id2"`` so
    that repeated comparisons of the same pair are O(1).

    Attributes:
        _comparison_cache: Maps ``"s1_id:s2_id"`` → StateComparisonResult.
    """

    def __init__(self) -> None:
        """Initialise the analyzer with an empty comparison cache."""
        self._comparison_cache: Dict[str, StateComparisonResult] = {}
        logger.debug("StateRepresentationAnalyzer initialised")

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def compare(self, s1: Any, s2: Any) -> StateComparisonResult:
        """Perform a detailed structural comparison of two semantic states.

        This is the central method of the analyzer.  It determines:

        * Which patches appear in both, only s1, or only s2.
        * Which shared patches have conflicting section assignments.
        * Whether either state is a refinement of the other.
        * The Jaccard distance between the two assignment sets.
        * A plain-text summary suitable for logging.

        Results are memoised: calling compare(s1, s2) a second time returns
        the cached StateComparisonResult without recomputation.

        Args:
            s1: First semantic state (SemanticState or duck-typed).
            s2: Second semantic state.

        Returns:
            A :class:`StateComparisonResult` instance describing the
            relationship between s1 and s2.

        Examples:
            >>> ana = StateRepresentationAnalyzer()
            >>> result = ana.compare(state_a, state_b)
            >>> result.are_equal
            False
        """
        s1_id = getattr(s1, "state_id", "unknown_s1")
        s2_id = getattr(s2, "state_id", "unknown_s2")

        # Check the memoisation cache first.
        cache_key = f"{s1_id}:{s2_id}"
        cached = self._comparison_cache.get(cache_key)
        if cached is not None:
            logger.debug("compare: cache hit for %s", cache_key)
            return cached

        # Retrieve patch assignment dicts from both states.
        pa1: Dict[str, str] = dict(getattr(s1, "patch_assignments", {}) or {})
        pa2: Dict[str, str] = dict(getattr(s2, "patch_assignments", {}) or {})

        keys1: Set[str] = set(pa1.keys())
        keys2: Set[str] = set(pa2.keys())

        # Classify patches into the three disjoint groups.
        shared_set: Set[str] = keys1 & keys2
        s1_only_set: Set[str] = keys1 - keys2
        s2_only_set: Set[str] = keys2 - keys1

        # Within the shared set, identify conflicts (different section values).
        conflict_set: Set[str] = {
            p for p in shared_set if pa1[p] != pa2[p]
        }

        # Stable sorted lists for deterministic output.
        shared_patches = sorted(shared_set)
        s1_only_patches = sorted(s1_only_set)
        s2_only_patches = sorted(s2_only_set)
        conflict_patches = sorted(conflict_set)

        # Equality: same domain AND no conflicts.
        are_equal = (not s1_only_set) and (not s2_only_set) and (not conflict_set)

        # Refinement: σ1 ≤ σ2 means dom(σ2) ⊆ dom(σ1) and they agree on dom(σ2).
        # i.e. every key in pa2 is also in pa1 and has the same value.
        is_ref_s1_of_s2 = (not s2_only_set) and (not conflict_set)
        # Symmetric: σ2 ≤ σ1
        is_ref_s2_of_s1 = (not s1_only_set) and (not conflict_set)

        # Compute the Jaccard distance between assignment sets.
        distance = self.compute_distance(s1, s2)

        # Build a succinct human-readable summary.
        if are_equal:
            summary = f"s1==s2 (identical, {len(shared_patches)} patches)"
        elif is_ref_s1_of_s2:
            summary = (
                f"s1 refines s2: s1 has {len(s1_only_patches)} extra patches, "
                f"no conflicts"
            )
        elif is_ref_s2_of_s1:
            summary = (
                f"s2 refines s1: s2 has {len(s2_only_patches)} extra patches, "
                f"no conflicts"
            )
        elif conflict_set:
            summary = (
                f"incompatible: {len(conflict_patches)} conflict(s), "
                f"distance={distance:.3f}"
            )
        else:
            summary = (
                f"disjoint+compatible: {len(s1_only_patches)} s1-only, "
                f"{len(s2_only_patches)} s2-only, distance={distance:.3f}"
            )

        result = StateComparisonResult(
            are_equal=are_equal,
            s1_id=s1_id,
            s2_id=s2_id,
            shared_patches=shared_patches,
            s1_only_patches=s1_only_patches,
            s2_only_patches=s2_only_patches,
            conflict_patches=conflict_patches,
            is_refinement_s1_of_s2=is_ref_s1_of_s2,
            is_refinement_s2_of_s1=is_ref_s2_of_s1,
            distance=distance,
            summary=summary,
        )

        # Store in cache (both orderings so compare(s2, s1) also gets a hit).
        self._comparison_cache[cache_key] = result
        reverse_key = f"{s2_id}:{s1_id}"
        # The reversed result swaps the s1/s2 perspective.
        reversed_result = StateComparisonResult(
            are_equal=are_equal,
            s1_id=s2_id,
            s2_id=s1_id,
            shared_patches=shared_patches,
            s1_only_patches=s2_only_patches,
            s2_only_patches=s1_only_patches,
            conflict_patches=conflict_patches,
            is_refinement_s1_of_s2=is_ref_s2_of_s1,
            is_refinement_s2_of_s1=is_ref_s1_of_s2,
            distance=distance,
            summary=summary,
        )
        self._comparison_cache[reverse_key] = reversed_result

        logger.debug("compare: %s | %s", cache_key, summary)
        return result

    def is_refinement(self, s1: Any, s2: Any) -> bool:
        """Return True if σ1 is a refinement of σ2 in the lattice order.

        A state σ1 is a refinement of σ2 when:
            dom(σ2) ⊆ dom(σ1)  and  ∀p ∈ dom(σ2): σ1(p) = σ2(p)

        In words: σ1 has *at least as many* assignments as σ2, and where σ2
        has made a choice, σ1 agrees with it.  σ1 is therefore "more
        determined" than σ2.

        Args:
            s1: The candidate refinement state.
            s2: The base state.

        Returns:
            True iff σ1 is a refinement of σ2.

        Examples:
            >>> ana = StateRepresentationAnalyzer()
            >>> ana.is_refinement(more_assigned, fewer_assigned)
            True
        """
        result = self.compare(s1, s2)
        return result.is_refinement_s1_of_s2

    def compute_distance(self, s1: Any, s2: Any) -> float:
        """Compute the Jaccard distance between two states' assignment sets.

        The assignment set of a state σ is the set of (patch, section) pairs
        in its patch_assignments dict.  The Jaccard distance is:

            d(σ1, σ2) = 1 − |A1 ∩ A2| / |A1 ∪ A2|

        where A_i = {(p, s) | p ↦ s ∈ σ_i}.

        If both states have empty assignment sets the distance is defined as
        0.0 (they are identical — the empty function).

        Args:
            s1: First semantic state.
            s2: Second semantic state.

        Returns:
            A float in [0, 1].  0.0 means identical assignment sets; 1.0
            means completely disjoint.

        Examples:
            >>> ana = StateRepresentationAnalyzer()
            >>> ana.compute_distance(s, s)
            0.0
        """
        pa1: Dict[str, str] = dict(getattr(s1, "patch_assignments", {}) or {})
        pa2: Dict[str, str] = dict(getattr(s2, "patch_assignments", {}) or {})

        # Represent each state as a frozenset of (patch, section) tuples.
        set1: Set[Tuple[str, str]] = set(pa1.items())
        set2: Set[Tuple[str, str]] = set(pa2.items())

        # Handle the degenerate case where both sets are empty.
        union_size = len(set1 | set2)
        if union_size == 0:
            return 0.0

        intersection_size = len(set1 & set2)
        jaccard_similarity = intersection_size / union_size
        return 1.0 - jaccard_similarity

    def find_differences(self, s1: Any, s2: Any) -> List[StateDiff]:
        """Produce a list of atomic diffs showing how s2 differs from s1.

        Each :class:`StateDiff` represents a single patch whose status changed
        when moving from σ1 to σ2.  Three types of changes are possible:

        * ``"added"``   — p ∉ dom(σ1), p ∈ dom(σ2)
        * ``"removed"`` — p ∈ dom(σ1), p ∉ dom(σ2)
        * ``"changed"`` — p ∈ dom(σ1) ∩ dom(σ2) and σ1(p) ≠ σ2(p)

        Args:
            s1: The "before" state.
            s2: The "after" state.

        Returns:
            A list of :class:`StateDiff` objects, sorted by patch_id for
            determinism.  May be empty if the states are identical.

        Examples:
            >>> ana = StateRepresentationAnalyzer()
            >>> diffs = ana.find_differences(old_state, new_state)
            >>> diffs[0].diff_type
            'added'
        """
        pa1: Dict[str, str] = dict(getattr(s1, "patch_assignments", {}) or {})
        pa2: Dict[str, str] = dict(getattr(s2, "patch_assignments", {}) or {})

        keys1 = set(pa1.keys())
        keys2 = set(pa2.keys())
        all_keys = sorted(keys1 | keys2)

        diffs: List[StateDiff] = []
        for patch_id in all_keys:
            in_s1 = patch_id in keys1
            in_s2 = patch_id in keys2

            if in_s1 and not in_s2:
                # Patch was removed in the transition to s2.
                diffs.append(
                    StateDiff(
                        patch_id=patch_id,
                        diff_type="removed",
                        old_section=pa1[patch_id],
                        new_section=None,
                    )
                )
            elif not in_s1 and in_s2:
                # Patch was newly assigned in s2.
                diffs.append(
                    StateDiff(
                        patch_id=patch_id,
                        diff_type="added",
                        old_section=None,
                        new_section=pa2[patch_id],
                    )
                )
            elif in_s1 and in_s2 and pa1[patch_id] != pa2[patch_id]:
                # Patch is in both but its section changed.
                diffs.append(
                    StateDiff(
                        patch_id=patch_id,
                        diff_type="changed",
                        old_section=pa1[patch_id],
                        new_section=pa2[patch_id],
                    )
                )
            # If in_s1 and in_s2 and values are equal, nothing to record.

        logger.debug(
            "find_differences: %d diff(s) between %s and %s",
            len(diffs),
            getattr(s1, "state_id", "?"),
            getattr(s2, "state_id", "?"),
        )
        return diffs

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    def lattice_join(self, s1: Any, s2: Any) -> Optional[Any]:
        """Compute the lattice join σ1 ⊔ σ2, if it exists.

        The join of σ1 and σ2 is the least upper bound in the refinement
        lattice, i.e. the most specific state that refines both σ1 and σ2.
        It exists if and only if σ1 and σ2 are *compatible*: they must agree
        on all patches that appear in both domains.

        If they are compatible, the join is:
            (σ1 ⊔ σ2)(p) = σ1(p) if p ∈ dom(σ1)
                           σ2(p) if p ∈ dom(σ2) \\ dom(σ1)

        which is the union of the two assignment functions.

        If there are any conflicts the join does not exist and None is
        returned.

        Args:
            s1: First state.
            s2: Second state.

        Returns:
            A new SemanticState representing σ1 ⊔ σ2, or None if the join
            does not exist (incompatible states).
        """  # noqa: W605
        comparison = self.compare(s1, s2)

        # Join only exists when there are no conflicts.
        if comparison.conflict_patches:
            logger.debug(
                "lattice_join: no join exists — %d conflict(s)",
                len(comparison.conflict_patches),
            )
            return None

        # Merge the two assignment dicts (s1 takes precedence for shared keys,
        # but since there are no conflicts they are already equal).
        pa1: Dict[str, str] = dict(getattr(s1, "patch_assignments", {}) or {})
        pa2: Dict[str, str] = dict(getattr(s2, "patch_assignments", {}) or {})
        merged_assignments: Dict[str, str] = {**pa2, **pa1}

        # Merge open obligations (union — the join must satisfy both).
        oo1: Set[str] = set(getattr(s1, "obligations_open", set()) or set())
        oo2: Set[str] = set(getattr(s2, "obligations_open", set()) or set())
        merged_open = oo1 | oo2

        # Merge closed obligations (intersection — only those closed in both).
        oc1: Set[str] = set(getattr(s1, "obligations_closed", set()) or set())
        oc2: Set[str] = set(getattr(s2, "obligations_closed", set()) or set())
        merged_closed = oc1 & oc2

        # The join state is a goal state only if both operands are.
        is_goal = bool(
            getattr(s1, "is_goal_state", False) and getattr(s2, "is_goal_state", False)
        )

        new_state = _build_synthetic_state(
            patch_assignments=merged_assignments,
            obligations_open=merged_open,
            obligations_closed=merged_closed,
            is_goal_state=is_goal,
            metadata={"join_of": [comparison.s1_id, comparison.s2_id]},
        )
        logger.debug("lattice_join: join state built with %d patches", len(merged_assignments))
        return new_state

    def lattice_meet(self, s1: Any, s2: Any) -> Optional[Any]:
        """Compute the lattice meet σ1 ⊓ σ2.

        The meet is the greatest lower bound — the most general state that is
        refined by both σ1 and σ2.  It always exists (the empty state is a
        lower bound for everything) and is given by:

            (σ1 ⊓ σ2)(p) = σ1(p)  if p ∈ dom(σ1) ∩ dom(σ2) and σ1(p) = σ2(p)

        i.e. the restriction to the shared domain where both states agree.
        Conflicting patches are excluded from the meet.

        Args:
            s1: First state.
            s2: Second state.

        Returns:
            A new SemanticState representing σ1 ⊓ σ2.  This is never None
            (the empty state is the bottom element of the lattice).
        """
        comparison = self.compare(s1, s2)

        pa1: Dict[str, str] = dict(getattr(s1, "patch_assignments", {}) or {})
        pa2: Dict[str, str] = dict(getattr(s2, "patch_assignments", {}) or {})

        # The meet keeps only patches in the shared domain where both agree.
        conflict_set = set(comparison.conflict_patches)
        meet_assignments: Dict[str, str] = {
            p: pa1[p]
            for p in comparison.shared_patches
            if p not in conflict_set
        }

        # Open obligations: intersection (meet only inherits shared obligations).
        oo1: Set[str] = set(getattr(s1, "obligations_open", set()) or set())
        oo2: Set[str] = set(getattr(s2, "obligations_open", set()) or set())
        meet_open = oo1 & oo2

        # Closed obligations: union (the meet can close what either closed).
        oc1: Set[str] = set(getattr(s1, "obligations_closed", set()) or set())
        oc2: Set[str] = set(getattr(s2, "obligations_closed", set()) or set())
        meet_closed = oc1 | oc2

        new_state = _build_synthetic_state(
            patch_assignments=meet_assignments,
            obligations_open=meet_open,
            obligations_closed=meet_closed,
            is_goal_state=False,
            metadata={"meet_of": [comparison.s1_id, comparison.s2_id]},
        )
        logger.debug(
            "lattice_meet: meet state built with %d patches (from shared %d, conflicts %d)",
            len(meet_assignments),
            len(comparison.shared_patches),
            len(comparison.conflict_patches),
        )
        return new_state


# ---------------------------------------------------------------------------
# Internal helper for constructing synthetic states without models dependency
# ---------------------------------------------------------------------------

def _build_synthetic_state(
    patch_assignments: Dict[str, str],
    obligations_open: Set[str],
    obligations_closed: Set[str],
    is_goal_state: bool,
    metadata: Dict[str, Any],
) -> Any:
    """Build a SemanticState-compatible object from raw components.

    When the models module is available a proper SemanticState is constructed.
    Otherwise a plain Python object with the expected attributes is returned
    so that the rest of the module still functions correctly.

    Args:
        patch_assignments: The patch → section mapping for the new state.
        obligations_open: Set of open obligation IDs.
        obligations_closed: Set of closed obligation IDs.
        is_goal_state: Whether this state satisfies the goal condition.
        metadata: Arbitrary key-value metadata to attach.

    Returns:
        A SemanticState (or compatible duck-typed object).
    """
    new_id = str(uuid.uuid4())

    if _MODELS_AVAILABLE:
        # Use the proper constructor from models.py.
        try:
            state = SemanticState(
                state_id=new_id,
                patch_assignments=patch_assignments,
                obligations_open=obligations_open,
                obligations_closed=obligations_closed,
                generation_round=0,
                is_terminal=is_goal_state,
                is_goal_state=is_goal_state,
                metadata=metadata,
            )
            return state
        except Exception as exc:
            logger.warning("_build_synthetic_state: SemanticState init failed: %s", exc)

    # Fallback: a simple namespace object that quacks like SemanticState.
    class _SimpleState:  # pylint: disable=too-few-public-methods
        pass

    obj = _SimpleState()
    obj.state_id = new_id  # type: ignore[attr-defined]
    obj.patch_assignments = patch_assignments  # type: ignore[attr-defined]
    obj.obligations_open = obligations_open  # type: ignore[attr-defined]
    obj.obligations_closed = obligations_closed  # type: ignore[attr-defined]
    obj.generation_round = 0  # type: ignore[attr-defined]
    obj.is_terminal = is_goal_state  # type: ignore[attr-defined]
    obj.is_goal_state = is_goal_state  # type: ignore[attr-defined]
    obj.metadata = metadata  # type: ignore[attr-defined]

    def _to_dict(self=obj) -> Dict[str, Any]:
        return {
            "state_id": self.state_id,
            "patch_assignments": self.patch_assignments,
            "obligations_open": sorted(self.obligations_open),
            "obligations_closed": sorted(self.obligations_closed),
            "generation_round": self.generation_round,
            "is_terminal": self.is_terminal,
            "is_goal_state": self.is_goal_state,
            "metadata": self.metadata,
        }

    obj.to_dict = _to_dict  # type: ignore[attr-defined]
    return obj


# ---------------------------------------------------------------------------
# StateRepresentationWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StateRepresentationWitness:
    """An immutable certificate attesting to the structural properties of a state.

    A witness is a lightweight, hashable snapshot of the key measurable
    attributes of a semantic state at the moment it was observed.  Witnesses
    are used to:

    * Archive structural facts about states without retaining the full state
      object (which may be large).
    * Provide a tamper-evident record: the fingerprint field must match the
      fingerprint of the original state.
    * Allow downstream consumers to reason about state complexity without
      holding a live reference.

    Attributes:
        witness_id: A fresh UUID identifying this particular witness record.
        state_id: The state_id of the state being witnessed.
        fingerprint: The SHA-256 fingerprint of the state at witness time.
        patch_count: Total number of patches in the problem (i.e. the size of
            the universe P, which is not necessarily the same as assigned_count).
        assigned_count: Number of patches actually assigned a section (i.e.
            |dom(σ)|).
        trust_tier: A categorical label classifying trustworthiness.  One of
            ``"verified"``, ``"provisional"``, ``"suspect"``.
        obstruction_count: Number of open obligations that remain unsatisfied.
        timestamp: UNIX epoch time (float) when the witness was created.

    Examples:
        >>> w = StateRepresentationWitness.from_state(some_state)
        >>> w.trust_tier
        'verified'
    """

    witness_id: str
    state_id: str
    fingerprint: str
    patch_count: int
    assigned_count: int
    trust_tier: str
    obstruction_count: int
    timestamp: float

    @classmethod
    def from_state(cls, state: Any) -> "StateRepresentationWitness":
        """Construct a witness from a live SemanticState object.

        This factory method extracts the relevant attributes from ``state``
        and applies the trust-tier heuristic described below.

        **Trust tier heuristic** (§40.1.5):
            * ``"verified"``   — no open obligations and no conflicts
              (obstruction_count == 0 and assigned_count > 0).
            * ``"provisional"`` — some open obligations remain.
            * ``"suspect"``    — zero assigned patches with non-zero
              obstruction count, suggesting the state may be malformed.

        Args:
            state: A SemanticState (or duck-typed equivalent) to witness.

        Returns:
            A new :class:`StateRepresentationWitness` instance.

        Raises:
            TypeError: If ``state`` is None or lacks the expected attributes.

        Examples:
            >>> state = make_initial_state(["p1", "p2", "p3"])
            >>> w = StateRepresentationWitness.from_state(state)
            >>> isinstance(w.witness_id, str)
            True
        """
        if state is None:
            raise TypeError("StateRepresentationWitness.from_state: state must not be None")

        # Extract patch_assignments.
        pa: Dict[str, str] = dict(getattr(state, "patch_assignments", {}) or {})
        assigned_count = len(pa)

        # The universe size may be tracked in metadata under "patch_universe".
        metadata = getattr(state, "metadata", {}) or {}
        patch_universe = metadata.get("patch_universe", None)
        if patch_universe is not None and isinstance(patch_universe, (list, set)):
            patch_count = len(patch_universe)
        else:
            # Fall back to the number of assigned patches.
            patch_count = assigned_count

        # Open obligations = unresolved constraints.
        obligations_open: Set[str] = set(getattr(state, "obligations_open", set()) or set())
        obstruction_count = len(obligations_open)

        # Determine trust tier.
        if obstruction_count == 0 and assigned_count > 0:
            trust_tier = "verified"
        elif obstruction_count > 0 and assigned_count == 0:
            trust_tier = "suspect"
        else:
            trust_tier = "provisional"

        # Compute fingerprint via the coordinator's algorithm (reuse logic).
        coord = StateRepresentationCoordinator()
        fingerprint = coord.compute_fingerprint(state)

        state_id = getattr(state, "state_id", "unknown")
        witness_id = str(uuid.uuid4())
        timestamp = time.time()

        return cls(
            witness_id=witness_id,
            state_id=state_id,
            fingerprint=fingerprint,
            patch_count=patch_count,
            assigned_count=assigned_count,
            trust_tier=trust_tier,
            obstruction_count=obstruction_count,
            timestamp=timestamp,
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def encode_state(state: Any) -> str:
    """Serialize a semantic state to a JSON string.

    The encoding is performed by calling ``state.to_dict()`` if that method
    is available (as it is on proper SemanticState objects from models.py).
    For duck-typed state objects that lack ``to_dict``, the function falls
    back to using ``vars(state)`` to extract the instance ``__dict__``.

    The resulting JSON string is UTF-8 safe and uses sorted keys so that the
    encoding is deterministic regardless of dict insertion order.

    Args:
        state: A SemanticState or compatible duck-typed object to encode.

    Returns:
        A JSON string representing the state.

    Raises:
        TypeError: If the state cannot be serialised to JSON.

    Examples:
        >>> encoded = encode_state(some_state)
        >>> isinstance(encoded, str)
        True
        >>> import json; data = json.loads(encoded)
        >>> "state_id" in data
        True
    """
    # Prefer the canonical to_dict() method defined by SemanticState.
    if hasattr(state, "to_dict") and callable(state.to_dict):
        raw_dict = state.to_dict()
    elif hasattr(state, "__dict__"):
        raw_dict = dict(vars(state))
    elif isinstance(state, dict):
        raw_dict = dict(state)
    else:
        raise TypeError(
            f"encode_state: cannot serialise state of type {type(state).__name__}"
        )

    # Convert sets to sorted lists so JSON serialisation succeeds.
    def _make_serialisable(obj: Any) -> Any:
        if isinstance(obj, set):
            return sorted(obj)
        if isinstance(obj, dict):
            return {k: _make_serialisable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_make_serialisable(item) for item in obj]
        return obj

    serialisable_dict = _make_serialisable(raw_dict)

    encoded = json.dumps(serialisable_dict, sort_keys=True, ensure_ascii=False)
    logger.debug(
        "encode_state: state_id=%s encoded to %d bytes",
        raw_dict.get("state_id", "?"),
        len(encoded),
    )
    return encoded


def decode_state(encoded: str) -> Any:
    """Deserialise a JSON string back into a semantic state object.

    If the models module is available, this function delegates to
    ``SemanticState.from_dict(data)`` to reconstruct a proper typed object.
    Otherwise it returns the raw parsed dictionary.

    The function handles set fields (``obligations_open``,
    ``obligations_closed``) by converting lists (which JSON produces) back to
    Python ``set`` objects.

    Args:
        encoded: A JSON string as produced by :func:`encode_state`.

    Returns:
        A SemanticState instance if models are available, otherwise a dict.

    Raises:
        json.JSONDecodeError: If ``encoded`` is not valid JSON.
        KeyError: If required fields are missing from the decoded dict.

    Examples:
        >>> encoded = encode_state(some_state)
        >>> state2 = decode_state(encoded)
        >>> state2.state_id == some_state.state_id
        True
    """
    data: Dict[str, Any] = json.loads(encoded)

    # Convert list fields that should be sets back to sets.
    for set_field in ("obligations_open", "obligations_closed"):
        if set_field in data and isinstance(data[set_field], list):
            data[set_field] = set(data[set_field])

    if _MODELS_AVAILABLE:
        try:
            # Use the classmethod from SemanticState for a fully typed object.
            return SemanticState.from_dict(data)
        except Exception as exc:
            logger.warning(
                "decode_state: SemanticState.from_dict failed (%s) — returning dict",
                exc,
            )

    # Fallback: return the raw dict.
    return data


def fingerprint_state(state: Any) -> str:
    """Compute the SHA-256 fingerprint for a semantic state.

    This module-level convenience function delegates to
    :meth:`StateRepresentationCoordinator.compute_fingerprint` so callers
    do not need to instantiate a coordinator just to fingerprint a state.

    The fingerprint is a 64-character lowercase hexadecimal string encoding
    the SHA-256 hash of the canonical representation of:

    * ``patch_assignments`` (sorted by key)
    * ``obligations_open`` (sorted)
    * ``is_goal_state``

    Two states with the same fingerprint are considered semantically
    equivalent for the purposes of state-space deduplication.

    Args:
        state: A SemanticState or compatible duck-typed object.

    Returns:
        A 64-character hexadecimal SHA-256 digest string.

    Examples:
        >>> fp = fingerprint_state(some_state)
        >>> len(fp)
        64
        >>> fp == fingerprint_state(some_state.clone())
        True
    """
    # Reuse the coordinator's implementation to guarantee consistency.
    coord = StateRepresentationCoordinator()
    return coord.compute_fingerprint(state)


# ---------------------------------------------------------------------------
# Smoke test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    print("=" * 70)
    print("state_representation — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Build a pair of minimal duck-typed states for testing without models.
    # ------------------------------------------------------------------

    class _DemoState:
        """Minimal state object used in the smoke test."""

        def __init__(self, state_id, patch_assignments, obligations_open=None, is_goal_state=False):
            self.state_id = state_id
            self.patch_assignments = dict(patch_assignments)
            self.obligations_open = set(obligations_open or [])
            self.obligations_closed: Set[str] = set()
            self.generation_round = 0
            self.is_terminal = is_goal_state
            self.is_goal_state = is_goal_state
            self.metadata: Dict[str, Any] = {}

        def to_dict(self) -> Dict[str, Any]:
            return {
                "state_id": self.state_id,
                "patch_assignments": self.patch_assignments,
                "obligations_open": sorted(self.obligations_open),
                "obligations_closed": [],
                "generation_round": self.generation_round,
                "is_terminal": self.is_terminal,
                "is_goal_state": self.is_goal_state,
                "metadata": self.metadata,
            }

    sigma1 = _DemoState(
        state_id="sigma1",
        patch_assignments={"p1": "sec_A", "p2": "sec_B", "p3": "sec_C"},
        obligations_open={"obl_1"},
    )
    sigma2 = _DemoState(
        state_id="sigma2",
        patch_assignments={"p1": "sec_A", "p2": "sec_B", "p4": "sec_D"},
        obligations_open={"obl_2"},
    )
    sigma3 = _DemoState(
        state_id="sigma3",
        patch_assignments={"p1": "sec_A", "p2": "sec_B"},
        obligations_open=set(),
        is_goal_state=False,
    )
    # sigma3 should be a sub-state (precursor) of both sigma1 and sigma2 — i.e. both refine sigma3.

    # ------------------------------------------------------------------
    # Demo 1: StateRepresentationCoordinator
    # ------------------------------------------------------------------
    print("\n--- StateRepresentationCoordinator ---")
    coord = StateRepresentationCoordinator()
    sid1 = coord.register(sigma1)
    sid2 = coord.register(sigma2)
    sid3 = coord.register(sigma3)
    print(f"  Registered {coord.size()} distinct states.")
    # Re-register sigma1 — should return existing ID, not inflate size.
    sid1_again = coord.register(sigma1)
    assert sid1 == sid1_again, "Duplicate sigma1 should return same ID"
    print(f"  Dedup check: re-register sigma1 → same ID={sid1_again} ✓")
    looked_up = coord.lookup(sid1)
    assert looked_up is sigma1
    print(f"  Lookup sid1 → {looked_up.state_id} ✓")
    print(f"  Access count for sid1: {coord.get_access_count(sid1)}")
    removed = coord.remove(sid3)
    print(f"  Remove sigma3: {removed} (size now {coord.size()})")

    # ------------------------------------------------------------------
    # Demo 2: StateRepresentationAnalyzer
    # ------------------------------------------------------------------
    print("\n--- StateRepresentationAnalyzer ---")
    ana = StateRepresentationAnalyzer()

    cmp12 = ana.compare(sigma1, sigma2)
    print(f"  compare(σ1, σ2): {cmp12.summary}")
    print(f"    shared={cmp12.shared_patches}, conflicts={cmp12.conflict_patches}")
    print(f"    distance={cmp12.distance:.4f}")

    print(f"  is_refinement(σ1, σ3): {ana.is_refinement(sigma1, sigma3)}")
    print(f"  is_refinement(σ3, σ1): {ana.is_refinement(sigma3, sigma1)}")

    diffs = ana.find_differences(sigma3, sigma1)
    print(f"  diffs σ3→σ1 ({len(diffs)} item(s)):")
    for d in diffs:
        print(f"    {d.diff_type:8s} {d.patch_id}: {d.old_section!r} → {d.new_section!r}")

    join = ana.lattice_join(sigma1, sigma2)
    if join is not None:
        print(f"  join(σ1, σ2): patch_assignments={join.patch_assignments}")
    else:
        print("  join(σ1, σ2): None (incompatible)")

    meet = ana.lattice_meet(sigma1, sigma2)
    print(f"  meet(σ1, σ2): patch_assignments={meet.patch_assignments}")

    # ------------------------------------------------------------------
    # Demo 3: StateRepresentationWitness
    # ------------------------------------------------------------------
    print("\n--- StateRepresentationWitness ---")
    w1 = StateRepresentationWitness.from_state(sigma1)
    print(f"  witness for sigma1:")
    print(f"    witness_id    = {w1.witness_id}")
    print(f"    state_id      = {w1.state_id}")
    print(f"    fingerprint   = {w1.fingerprint[:16]}…")
    print(f"    assigned      = {w1.assigned_count}")
    print(f"    obstructions  = {w1.obstruction_count}")
    print(f"    trust_tier    = {w1.trust_tier}")

    # ------------------------------------------------------------------
    # Demo 4: Module-level encode/decode/fingerprint
    # ------------------------------------------------------------------
    print("\n--- encode_state / decode_state / fingerprint_state ---")
    encoded = encode_state(sigma1)
    print(f"  encode_state(σ1): {encoded[:80]}…")
    fp = fingerprint_state(sigma1)
    print(f"  fingerprint_state(σ1): {fp[:32]}…")
    # Round-trip check (decode returns dict when models unavailable).
    decoded = decode_state(encoded)
    if isinstance(decoded, dict):
        assert decoded["state_id"] == sigma1.state_id
        print(f"  decode_state → dict, state_id={decoded['state_id']} ✓")
    else:
        assert decoded.state_id == sigma1.state_id
        print(f"  decode_state → SemanticState, state_id={decoded.state_id} ✓")

    print("\nAll smoke tests passed ✓")
