"""Core Generation State Space

# copilot: This module defines the core state space for the jugeo generation process.
The state space models all states a generation process can occupy, from initial setup
through cover proposal, obligation generation, local verification, global gluing, to
completion or failure. Judgments are tuples (c, phi, A, E, O, B, T, Pi). Trust uses
TrustTier enum. Obstructions are Cech H1 cohomology classes.

This module defines the core state space for the jugeo generation process. The state
space models all states a generation process can occupy, from initial setup through cover
proposal, obligation generation, local verification, global gluing, to completion or failure.

The state space is a directed graph where each node is a GenerationState and each edge is a
StateTransition. States carry snapshots of the current context (what cover elements exist,
what obligations are open) and their TrustTier. Transitions are triggered by generation moves
and carry their own TrustTier.
"""

from __future__ import annotations

import uuid
import hashlib
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, List, Dict, FrozenSet, Tuple
import itertools
import functools
import datetime

try:
    from jugeo.core.trust import TrustTier
    from jugeo.core.judgment import Judgment
    from jugeo.core.obstruction import CechObstruction
except ImportError:
    from enum import Enum
    class TrustTier(Enum):
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5
    Judgment = tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

STATE_SPACE_VERSION = "ss-v2"

MAX_STATES_DEFAULT = 1024

MAX_TRANSITIONS_DEFAULT = 4096

TRANSITION_LABELS: Dict[str, str] = {
    "initialize": "Transition from nil to INITIAL state with empty context.",
    "propose_cover": "Transition from INITIAL to COVER_PROPOSED after a valid cover is proposed.",
    "generate_obligations": "Transition from COVER_PROPOSED to OBLIGATIONS_GENERATED.",
    "verify_locally": "Transition from OBLIGATIONS_GENERATED to LOCALLY_VERIFIED after local checks pass.",
    "glue_globally": "Transition from LOCALLY_VERIFIED to GLOBALLY_GLUED after the gluing axiom is checked.",
    "complete": "Transition from GLOBALLY_GLUED to COMPLETE when all obligations are discharged.",
    "fail_cover": "Transition from COVER_PROPOSED to FAILED when the cover is invalid.",
    "fail_obligations": "Transition from OBLIGATIONS_GENERATED to FAILED when obligations cannot be satisfied.",
    "fail_local": "Transition from LOCALLY_VERIFIED to FAILED when local checks reveal inconsistency.",
    "retry_propose": "Transition back to COVER_PROPOSED from OBLIGATIONS_GENERATED for re-proposal.",
}

VALID_TRIGGER_NAMES: List[str] = [
    "initialize",
    "propose_cover",
    "generate_obligations",
    "verify_locally",
    "glue_globally",
    "complete",
    "fail_cover",
    "fail_obligations",
    "fail_local",
    "retry_propose",
    "abort",
    "reset",
]

OBLIGATION_KINDS: Dict[str, str] = {
    "local_section": "Obligation to produce a valid local section over an open set.",
    "compatibility": "Obligation to demonstrate that overlapping local sections agree on intersections.",
    "coherence": "Obligation to verify the coherence condition for the sheaf restriction maps.",
    "existence": "Obligation to show that at least one element satisfying the cover condition exists.",
    "uniqueness": "Obligation to prove that the global section is unique when it exists.",
    "tier_promotion": "Obligation to satisfy all checks required to promote the judgment to a higher tier.",
    "evidence_provision": "Obligation to supply concrete evidence items that discharge semantic requirements.",
    "discharge_certificate": "Obligation to provide a certificate that a prior obligation has been discharged.",
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GenStateKind(Enum):
    """Enumeration of all state kinds in the generation state machine.

    The ordering of values corresponds to the canonical progression through the
    generation pipeline: INITIAL -> COVER_PROPOSED -> OBLIGATIONS_GENERATED ->
    LOCALLY_VERIFIED -> GLOBALLY_GLUED -> COMPLETE. The FAILED state is a terminal
    absorbing state reachable from any non-terminal state.
    """

    INITIAL = 1
    COVER_PROPOSED = 2
    OBLIGATIONS_GENERATED = 3
    LOCALLY_VERIFIED = 4
    GLOBALLY_GLUED = 5
    COMPLETE = 6
    FAILED = 7


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationState:
    """A single node in the generation state space.

    Each GenerationState captures the full context of the generation process at a
    particular moment: the kind of state, a snapshot of the context (as a hashable
    tuple), the set of currently open obligations, and the TrustTier at which the
    state was reached. States are frozen and hashable so they can be stored in
    frozensets and used as dictionary keys.

    The context_snapshot is an opaque tuple that callers populate with whatever
    key-value pairs describe the cover, schema, and evidence pool at the time the
    state was recorded. The obligations frozenset contains string obligation IDs
    that have been generated but not yet discharged.
    """

    state_id: str
    kind: GenStateKind
    context_snapshot: tuple
    obligations: FrozenSet
    state_tier: TrustTier

    def is_terminal(self) -> bool:
        """Determine whether this state is a terminal state in the generation FSM.

        A state is terminal if its kind is either COMPLETE or FAILED. Terminal states
        have no valid outgoing transitions in the canonical state machine — once a
        generation process reaches a terminal state, no further moves may be applied.
        Callers should check is_terminal() before attempting to find successor states
        or apply transitions, to avoid spurious errors. This method does not inspect
        the obligations set or context; it is a pure function of self.kind.

        Returns:
            bool: True if kind is COMPLETE or FAILED.
        """
        return self.kind in (GenStateKind.COMPLETE, GenStateKind.FAILED)

    def is_failure(self) -> bool:
        """Determine whether this state represents a failed generation attempt.

        A state is a failure state if and only if its kind is exactly GenStateKind.FAILED.
        This is distinct from is_terminal(), which also returns True for COMPLETE states.
        Callers use is_failure() to differentiate between successful completion and failure
        when both is_terminal() values are True. The method is a pure function of self.kind
        and does not inspect obligations or context_snapshot.

        Returns:
            bool: True if kind is FAILED.
        """
        return self.kind == GenStateKind.FAILED

    def successor_kinds(self) -> List:
        """Return the list of valid next GenStateKind values from this state.

        The successor mapping encodes the canonical state machine topology. From INITIAL
        the only valid successor is COVER_PROPOSED. From COVER_PROPOSED the successors
        are OBLIGATIONS_GENERATED or FAILED. From OBLIGATIONS_GENERATED the successors
        are LOCALLY_VERIFIED, COVER_PROPOSED (for retry), or FAILED. From LOCALLY_VERIFIED
        the successors are GLOBALLY_GLUED or FAILED. From GLOBALLY_GLUED the successor
        is COMPLETE or FAILED. Terminal states COMPLETE and FAILED have empty successor lists.
        This method returns a new list on each call; callers may safely modify the returned list.

        Returns:
            List[GenStateKind]: The valid successor state kinds.
        """
        mapping: Dict[GenStateKind, List] = {
            GenStateKind.INITIAL: [GenStateKind.COVER_PROPOSED],
            GenStateKind.COVER_PROPOSED: [
                GenStateKind.OBLIGATIONS_GENERATED,
                GenStateKind.FAILED,
            ],
            GenStateKind.OBLIGATIONS_GENERATED: [
                GenStateKind.LOCALLY_VERIFIED,
                GenStateKind.COVER_PROPOSED,
                GenStateKind.FAILED,
            ],
            GenStateKind.LOCALLY_VERIFIED: [
                GenStateKind.GLOBALLY_GLUED,
                GenStateKind.FAILED,
            ],
            GenStateKind.GLOBALLY_GLUED: [
                GenStateKind.COMPLETE,
                GenStateKind.FAILED,
            ],
            GenStateKind.COMPLETE: [],
            GenStateKind.FAILED: [],
        }
        return list(mapping.get(self.kind, []))

    def to_judgment_tuple(self) -> tuple:
        """Serialise this state to a compact tuple for embedding in Judgment objects.

        The tuple contains the state_id, kind name, count of open obligations, tier name,
        and the length of the context_snapshot. This compact form is sufficient for
        judgment-level tracking without embedding the full context snapshot or obligation
        set. The tuple is hashable and can be used as a dictionary key or stored in a
        frozenset alongside other judgment tuples.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.state_id,
            self.kind.name,
            len(self.obligations),
            self.state_tier.name,
            len(self.context_snapshot),
        )

    def state_key(self) -> str:
        """Compute a short deterministic key for this state using SHA-256.

        The key is derived from the SHA-256 hash of the concatenation of state_id and
        kind.name, returning the first 16 hexadecimal characters of the digest. This
        provides a compact, collision-resistant identifier that can be used for state
        indexing in the explorer and for comparison in test assertions. The key is
        deterministic for the same (state_id, kind) pair and stable across process
        restarts.

        Returns:
            str: First 16 hex characters of SHA-256(state_id + kind.name).
        """
        raw = (self.state_id + self.kind.name).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]


@dataclass(frozen=True)
class StateTransition:
    """A directed edge in the generation state space graph.

    A StateTransition connects two states identified by from_state_id and to_state_id
    and carries a trigger name that identifies the generation move that caused the
    transition. Each transition has its own TrustTier so that low-trust transitions
    (e.g., heuristic cover proposals) can be distinguished from high-trust transitions
    (e.g., formally verified gluing steps).

    Instances are frozen and hashable. The transition does not hold references to the
    actual state objects — it holds only their IDs, to avoid cycle issues with frozen
    dataclasses and to keep the representation compact.
    """

    transition_id: str
    from_state_id: str
    to_state_id: str
    trigger: str
    transition_tier: TrustTier

    def is_valid_in_space(self, space: Any) -> bool:
        """Check whether both endpoint state IDs are present in the given state space.

        The method iterates over the space.states collection (expected to be a frozenset
        of GenerationState objects) and checks that at least one state has state_id equal
        to from_state_id and at least one has state_id equal to to_state_id. Both checks
        must pass for the method to return True. This is a linear-time check and is
        intended for use during state space construction validation, not in hot paths.
        If space is None or space.states is empty, the method returns False.

        Args:
            space: An object with a .states attribute containing GenerationState objects.

        Returns:
            bool: True if both state IDs are found in space.states.
        """
        if space is None:
            return False
        if not hasattr(space, "states"):
            return False
        found_from = any(getattr(s, "state_id", None) == self.from_state_id for s in space.states)
        found_to = any(getattr(s, "state_id", None) == self.to_state_id for s in space.states)
        return found_from and found_to

    def to_judgment_tuple(self) -> tuple:
        """Serialise this transition to a compact tuple for Judgment embedding.

        The tuple contains transition_id, from_state_id, to_state_id, trigger, and
        tier name. This is the minimal set of fields required to uniquely identify
        and characterise a transition in a judgment context. The tuple is hashable.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.transition_id,
            self.from_state_id,
            self.to_state_id,
            self.trigger,
            self.transition_tier.name,
        )

    def transition_key(self) -> str:
        """Compute a deterministic key for this transition using SHA-256.

        The key is derived from hashing the concatenation of transition_id, from_state_id,
        and to_state_id. The first 16 hexadecimal characters are returned. This key is
        used for indexing transitions in explorer data structures and for deduplication.

        Returns:
            str: First 16 hex chars of SHA-256(transition_id + from_state_id + to_state_id).
        """
        raw = (self.transition_id + self.from_state_id + self.to_state_id).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def describe(self) -> str:
        """Produce a human-readable description of this transition.

        The description includes the transition_id, trigger name, from and to state IDs,
        and the tier. It is formatted as a single line suitable for inclusion in state
        space reports and log messages. The TRANSITION_LABELS dict is consulted to
        provide a human-readable label for the trigger if available.

        Returns:
            str: A one-line description string.
        """
        label = TRANSITION_LABELS.get(self.trigger, "unknown trigger")
        return (
            "[{}] {} --({})-> {} | tier={} | {}".format(
                self.transition_id,
                self.from_state_id,
                self.trigger,
                self.to_state_id,
                self.transition_tier.name,
                label,
            )
        )


@dataclass(frozen=True)
class StateSpace:
    """The complete directed graph of generation states and transitions.

    A StateSpace is an immutable record of all states and transitions in a generation
    state machine instance. The states field is a frozenset of GenerationState objects
    and the transitions field is a tuple of StateTransition objects. The initial_state_id
    identifies the state from which exploration should begin.

    Because the object is frozen, it can be used as a cache key or embedded in a Judgment
    tuple. Structural queries such as reachability and path existence are computed on
    demand using BFS/DFS over the transitions tuple.
    """

    space_id: str
    states: FrozenSet
    transitions: tuple
    initial_state_id: str
    space_tier: TrustTier

    def reachable_from(self, state_id: str) -> FrozenSet:
        """Compute the set of state IDs reachable from the given state via BFS.

        Starting from state_id, the method performs a breadth-first search over the
        transitions tuple. For each transition whose from_state_id matches a visited
        state, its to_state_id is added to the frontier and to the visited set. The
        search terminates when the frontier is empty. The returned frozenset includes
        state_id itself as the trivially reachable state. An empty frozenset is returned
        if state_id is not found in the state space. This method is O(|states| * |transitions|)
        in the worst case.

        Args:
            state_id: The ID of the starting state.

        Returns:
            FrozenSet[str]: All state IDs reachable from state_id, including itself.
        """
        visited = set()
        frontier = [state_id]
        visited.add(state_id)
        while frontier:
            current = frontier.pop(0)
            for t in self.transitions:
                if t.from_state_id == current and t.to_state_id not in visited:
                    visited.add(t.to_state_id)
                    frontier.append(t.to_state_id)
        return frozenset(visited)

    def terminal_states(self) -> FrozenSet:
        """Identify the set of state IDs from which no transition departs.

        A state is terminal if no transition in self.transitions has its from_state_id
        equal to that state's ID. This definition is structural (graph-based) rather than
        semantic (kind-based), so it works even for state spaces that have been built
        programmatically without using the GenStateKind enum. The frozenset of state IDs
        (strings) is returned, not the full GenerationState objects.

        Returns:
            FrozenSet[str]: State IDs with no outgoing transitions.
        """
        all_state_ids = frozenset(s.state_id for s in self.states)
        has_outgoing = frozenset(t.from_state_id for t in self.transitions)
        return all_state_ids - has_outgoing

    def path_exists(self, src: str, dst: str) -> bool:
        """Determine whether a directed path exists from src to dst in this state space.

        This is a wrapper around reachable_from(src) that checks whether dst is in the
        returned frozenset. Because reachable_from() performs BFS, path_exists() is also
        BFS-based and has the same O(|states| * |transitions|) complexity. For large state
        spaces, callers should cache the reachability result and perform multiple membership
        tests against the cached frozenset.

        Args:
            src: The source state ID.
            dst: The destination state ID.

        Returns:
            bool: True if dst is reachable from src.
        """
        return dst in self.reachable_from(src)

    def to_judgment_tuple(self) -> tuple:
        """Serialise this state space to a compact tuple for Judgment embedding.

        The tuple contains space_id, count of states, count of transitions, initial_state_id,
        and space tier name. This is sufficient to identify and characterise a state space
        in a judgment context without embedding the full state and transition data.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.space_id,
            len(self.states),
            len(self.transitions),
            self.initial_state_id,
            self.space_tier.name,
        )

    def space_summary(self) -> str:
        """Produce a multi-line human-readable summary of this state space.

        The summary lists the space ID, tier, counts of states and transitions, the
        initial state ID, and the set of terminal state IDs. Each state is listed with
        its kind name and obligation count. The summary is suitable for inclusion in
        debug logs and reports. It is generated on demand and not cached.

        Returns:
            str: Multi-line summary string.
        """
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        terminal_ids = self.terminal_states()
        state_lines = "\n".join(
            "  [{kind}] {sid} obls={o}".format(
                kind=s.kind.name, sid=s.state_id, o=len(s.obligations)
            )
            for s in sorted(self.states, key=lambda s: s.state_id)
        )
        trans_lines = "\n".join(
            "  {}".format(t.describe()) for t in self.transitions
        )
        return (
            "=== StateSpace {sid} ===\n"
            "Version  : {ver}\n"
            "Tier     : {tier}\n"
            "States   : {ns}\n"
            "Transitions: {nt}\n"
            "Initial  : {init}\n"
            "Terminal : {term}\n"
            "Generated: {ts}\n"
            "States:\n{sl}\n"
            "Transitions:\n{tl}\n"
        ).format(
            sid=self.space_id,
            ver=STATE_SPACE_VERSION,
            tier=self.space_tier.name,
            ns=len(self.states),
            nt=len(self.transitions),
            init=self.initial_state_id,
            term=", ".join(sorted(terminal_ids)) if terminal_ids else "none",
            ts=ts,
            sl=state_lines,
            tl=trans_lines,
        )


@dataclass(frozen=True)
class GenerationContext:
    """The ambient context attached to a generation step.

    A GenerationContext records the cover_id of the cover currently being worked on,
    the set of open obligation IDs, the set of evidence IDs that have been accumulated,
    and the TrustTier at which this context was established. Contexts are immutable;
    updated contexts are created by constructing new instances with modified fields.

    The frozen design allows contexts to be stored alongside states as part of
    context_snapshot tuples without fear of aliased mutation.
    """

    context_id: str
    cover_id: str
    obligation_ids: FrozenSet
    evidence_ids: FrozenSet
    context_tier: TrustTier

    def is_ready_for(self, kind: Any) -> bool:
        """Determine whether this context is ready for the transition into the given state kind.

        Readiness is assessed heuristically based on the combination of obligation_ids and
        evidence_ids in the context. For OBLIGATIONS_GENERATED, readiness requires at least
        one open obligation. For LOCALLY_VERIFIED, readiness requires that evidence_ids is
        non-empty. For GLOBALLY_GLUED, readiness requires that evidence_ids is at least as
        large as obligation_ids (all obligations potentially discharged). For COMPLETE,
        readiness requires that obligation_ids is empty. All other kinds are considered
        unconditionally ready. This method is intentionally permissive — final authority
        rests with the transition guards.

        Args:
            kind: A GenStateKind value or comparable object with a .name attribute.

        Returns:
            bool: True if the context appears ready for the given kind.
        """
        kind_name = getattr(kind, "name", str(kind))
        if kind_name == "OBLIGATIONS_GENERATED":
            return len(self.obligation_ids) > 0
        if kind_name == "LOCALLY_VERIFIED":
            return len(self.evidence_ids) > 0
        if kind_name == "GLOBALLY_GLUED":
            return len(self.evidence_ids) >= len(self.obligation_ids)
        if kind_name == "COMPLETE":
            return len(self.obligation_ids) == 0
        return True

    def to_judgment_tuple(self) -> tuple:
        """Serialise this context to a compact tuple for Judgment embedding.

        The tuple contains context_id, cover_id, count of obligation IDs, count of
        evidence IDs, and tier name. This minimal representation is sufficient for
        judgment bookkeeping without embedding the full ID sets.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.context_id,
            self.cover_id,
            len(self.obligation_ids),
            len(self.evidence_ids),
            self.context_tier.name,
        )

    def context_key(self) -> str:
        """Compute a deterministic key for this context using SHA-256.

        The key hashes the concatenation of context_id and cover_id and returns the
        first 16 hexadecimal characters. This key is stable for a given (context_id,
        cover_id) pair and is used for indexing contexts in explorer structures.

        Returns:
            str: First 16 hex chars of SHA-256(context_id + cover_id).
        """
        raw = (self.context_id + self.cover_id).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def summary(self) -> str:
        """Produce a concise summary string for this generation context.

        The summary includes the context_id, cover_id, counts of open obligations and
        accumulated evidence, and the trust tier. It is formatted as a single line
        suitable for inclusion in state reports and log output.

        Returns:
            str: A one-line summary string.
        """
        return (
            "Context[{cid}] cover={cover} obls={no} evidence={ne} tier={tier}".format(
                cid=self.context_id,
                cover=self.cover_id,
                no=len(self.obligation_ids),
                ne=len(self.evidence_ids),
                tier=self.context_tier.name,
            )
        )


class StateSpaceExplorer:
    """Mutable builder and explorer for generation state spaces.

    StateSpaceExplorer accumulates GenerationState and StateTransition objects via
    add_state() and add_transition(), then supports BFS exploration and DFS path
    finding. Once exploration is complete, report() creates and returns an immutable
    StateSpace snapshot of all accumulated data.

    Unlike the frozen dataclasses, StateSpaceExplorer is a regular mutable class
    because it is used during construction phases where states and transitions are
    added incrementally.
    """

    def __init__(self) -> None:
        """Initialise an empty explorer with no states or transitions."""
        self.states: Dict[str, Any] = {}
        self.transitions: List[Any] = []

    def add_state(self, s: Any) -> None:
        """Add a GenerationState to the explorer, keyed by its state_id.

        If a state with the same state_id already exists in the explorer, it is silently
        replaced by the new state. This allows callers to update state snapshots as the
        generation process progresses. The explorer does not validate that the state's
        kind is a member of GenStateKind — it accepts any object with a state_id attribute.

        Args:
            s: A GenerationState (or compatible) object to register.
        """
        if s is None:
            return
        sid = getattr(s, "state_id", None)
        if sid is not None:
            self.states[sid] = s

    def add_transition(self, t: Any) -> None:
        """Add a StateTransition to the explorer.

        Transitions are stored in an ordered list, so the order in which they are added
        is preserved. Duplicate transitions (same transition_id) are allowed — the
        deduplication responsibility rests with the caller if uniqueness is required.
        The explorer does not validate that the transition's endpoint state IDs are
        already registered.

        Args:
            t: A StateTransition (or compatible) object to record.
        """
        if t is not None:
            self.transitions.append(t)

    def explore_from(self, state_id: str) -> FrozenSet:
        """BFS over the accumulated transitions starting from state_id.

        The exploration proceeds breadth-first using self.transitions. It returns the
        frozenset of all state IDs reachable from state_id via any sequence of transitions.
        The starting state_id is included in the result. If state_id is not in self.states,
        the method still performs BFS using only the transitions and returns whatever
        is reachable.

        Args:
            state_id: The starting state ID.

        Returns:
            FrozenSet[str]: All reachable state IDs including state_id itself.
        """
        visited: set = set()
        frontier: List[str] = [state_id]
        visited.add(state_id)
        while frontier:
            current = frontier.pop(0)
            for t in self.transitions:
                nxt = getattr(t, "to_state_id", None)
                frm = getattr(t, "from_state_id", None)
                if frm == current and nxt not in visited:
                    visited.add(nxt)
                    frontier.append(nxt)
        return frozenset(visited)

    def find_complete_paths(self) -> List[List[str]]:
        """DFS to find all simple paths from any initial state to a terminal state.

        A path is considered complete if it ends at a state whose kind is COMPLETE or
        FAILED (i.e., is_terminal() returns True). The search begins from states that
        have no incoming transitions (i.e., starting nodes). All simple paths (no
        repeated state IDs) are returned as lists of state ID strings. If there are
        no terminal states or no starting states, an empty list is returned. The search
        is depth-limited to MAX_STATES_DEFAULT iterations to prevent infinite loops on
        cyclic state spaces.

        Returns:
            List[List[str]]: All simple paths ending at terminal states.
        """
        all_ids = set(self.states.keys())
        has_incoming: set = set()
        for t in self.transitions:
            has_incoming.add(getattr(t, "to_state_id", None))
        start_ids = [sid for sid in all_ids if sid not in has_incoming]
        if not start_ids:
            start_ids = list(all_ids)[:1]

        terminal_ids: set = set()
        for sid, s in self.states.items():
            if hasattr(s, "is_terminal") and s.is_terminal():
                terminal_ids.add(sid)

        completed_paths: List[List[str]] = []
        stack: List[Tuple] = [(sid, [sid]) for sid in start_ids]
        iterations = 0
        while stack and iterations < MAX_STATES_DEFAULT:
            current_id, path = stack.pop()
            iterations += 1
            if current_id in terminal_ids:
                completed_paths.append(path)
                continue
            for t in self.transitions:
                if getattr(t, "from_state_id", None) == current_id:
                    nxt = getattr(t, "to_state_id", None)
                    if nxt not in path:
                        stack.append((nxt, path + [nxt]))
        return completed_paths

    def report(self) -> StateSpace:
        """Create and return an immutable StateSpace from the accumulated data.

        All states registered via add_state() are collected into a frozenset. All
        transitions added via add_transition() are collected into a tuple. The initial
        state is determined by finding the state with no incoming transitions; if none
        is found, the lexicographically first state_id is used. The space_tier is
        determined by the minimum tier across all registered states. The returned
        StateSpace is fully immutable.

        Returns:
            StateSpace: An immutable snapshot of the current explorer contents.
        """
        states_frozenset: FrozenSet = frozenset(self.states.values())
        transitions_tuple = tuple(self.transitions)
        has_incoming: set = set()
        for t in self.transitions:
            has_incoming.add(getattr(t, "to_state_id", None))
        candidates = [sid for sid in self.states if sid not in has_incoming]
        if candidates:
            initial_id = sorted(candidates)[0]
        elif self.states:
            initial_id = sorted(self.states.keys())[0]
        else:
            initial_id = ""
        tiers = [getattr(s, "state_tier", TrustTier.PROPOSAL) for s in self.states.values()]
        min_tier = min(tiers, key=lambda t: t.value, default=TrustTier.PROPOSAL)
        space_id = "space-" + str(uuid.uuid4())[:8]
        return StateSpace(
            space_id=space_id,
            states=states_frozenset,
            transitions=transitions_tuple,
            initial_state_id=initial_id,
            space_tier=min_tier,
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def build_state_space(
    space_id: str,
    states: List[Any],
    transitions: List[Any],
    initial_id: str,
    tier: TrustTier,
) -> StateSpace:
    """Build an immutable StateSpace from lists of states and transitions.

    This factory function converts the lists into the required frozenset and tuple
    and constructs a StateSpace. It is the preferred way to build state spaces from
    externally constructed state and transition objects.

    Args:
        space_id: Unique identifier for the space.
        states: List of GenerationState objects.
        transitions: List of StateTransition objects.
        initial_id: The ID of the initial state.
        tier: The TrustTier for the space.

    Returns:
        StateSpace: An immutable state space.
    """
    return StateSpace(
        space_id=space_id,
        states=frozenset(states),
        transitions=tuple(transitions),
        initial_state_id=initial_id,
        space_tier=tier,
    )


def explore_state_space(explorer: Any, initial: Any) -> StateSpace:
    """Add the initial state to the explorer and return the resulting StateSpace.

    This convenience function adds the initial GenerationState to the explorer,
    calls explore_from() to perform BFS, and then calls report() to build the
    immutable StateSpace. It is intended for simple single-entry-point explorations.

    Args:
        explorer: A StateSpaceExplorer instance.
        initial: The initial GenerationState to register and explore from.

    Returns:
        StateSpace: The immutable result of the exploration.
    """
    if initial is not None:
        explorer.add_state(initial)
    initial_id = getattr(initial, "state_id", "")
    explorer.explore_from(initial_id)
    return explorer.report()


def find_path_to_completion(space: Any, initial_id: str) -> List[str]:
    """Find a directed path from initial_id to the first COMPLETE state in the space.

    This function performs BFS over space.transitions, starting from initial_id, and
    returns the path (as a list of state IDs) to the first state whose kind is COMPLETE.
    If no complete state is reachable, an empty list is returned. The path includes
    both the initial state and the COMPLETE state.

    Args:
        space: A StateSpace or compatible object.
        initial_id: The starting state ID.

    Returns:
        List[str]: Ordered list of state IDs forming the path, or [] if unreachable.
    """
    state_map: Dict[str, Any] = {getattr(s, "state_id", None): s for s in space.states}
    queue: List[List[str]] = [[initial_id]]
    visited: set = set()
    visited.add(initial_id)
    while queue:
        path = queue.pop(0)
        current_id = path[-1]
        current_state = state_map.get(current_id)
        if current_state is not None:
            kind = getattr(current_state, "kind", None)
            if kind == GenStateKind.COMPLETE:
                return path
        for t in space.transitions:
            if getattr(t, "from_state_id", None) == current_id:
                nxt = getattr(t, "to_state_id", None)
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(path + [nxt])
    return []


def enumerate_states(space: Any) -> List[Any]:
    """Return a sorted list of all states in the given state space.

    States are sorted by their state_id strings to provide a deterministic ordering
    for iteration and reporting. This function is a convenience wrapper around
    space.states that hides the frozenset type and provides a list interface.

    Args:
        space: A StateSpace with a .states frozenset.

    Returns:
        List[GenerationState]: States sorted by state_id.
    """
    if space is None or not hasattr(space, "states"):
        return []
    return sorted(space.states, key=lambda s: getattr(s, "state_id", ""))


# ---------------------------------------------------------------------------
# __main__ demonstration block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("jugeo.generation.state_space.s02 — Core Generation State Space demo")
    print("=" * 70)

    # 1. Build states across all kinds
    print("\n[1] Building GenerationState instances for all kinds:")
    states_list: List[GenerationState] = []
    for kind in GenStateKind:
        sid = "state-{}-{}".format(kind.name.lower(), str(uuid.uuid4())[:6])
        s = GenerationState(
            state_id=sid,
            kind=kind,
            context_snapshot=("cover_id", "example-cover"),
            obligations=frozenset(["obl-1", "obl-2"]) if kind not in (GenStateKind.COMPLETE, GenStateKind.FAILED) else frozenset(),
            state_tier=TrustTier.REVIEWED,
        )
        states_list.append(s)
        print("  [{kind}] {sid} terminal={t} failure={f} key={k}".format(
            kind=kind.name, sid=sid, t=s.is_terminal(), f=s.is_failure(), k=s.state_key()
        ))

    # 2. Successor kinds
    print("\n[2] Successor kinds:")
    for s in states_list:
        succ = s.successor_kinds()
        print("  {} -> {}".format(s.kind.name, [k.name for k in succ]))

    # 3. to_judgment_tuple
    print("\n[3] Judgment tuples:")
    for s in states_list[:3]:
        print("  ", s.to_judgment_tuple())

    # 4. Build transitions
    print("\n[4] Building StateTransition instances:")
    transitions_list: List[StateTransition] = []
    for i in range(len(states_list) - 1):
        from_s = states_list[i]
        to_s = states_list[i + 1]
        trigger = VALID_TRIGGER_NAMES[i % len(VALID_TRIGGER_NAMES)]
        t = StateTransition(
            transition_id="trans-{:03d}".format(i),
            from_state_id=from_s.state_id,
            to_state_id=to_s.state_id,
            trigger=trigger,
            transition_tier=TrustTier.REVIEWED,
        )
        transitions_list.append(t)
        print("  ", t.describe())

    # 5. Build StateSpace
    print("\n[5] Building StateSpace:")
    space = build_state_space(
        space_id="demo-space-001",
        states=states_list,
        transitions=transitions_list,
        initial_id=states_list[0].state_id,
        tier=TrustTier.REVIEWED,
    )
    print("  space_id:", space.space_id)
    print("  states:", len(space.states))
    print("  transitions:", len(space.transitions))
    print("  to_judgment_tuple:", space.to_judgment_tuple())

    # 6. Reachability
    print("\n[6] Reachability from initial state:")
    reachable = space.reachable_from(states_list[0].state_id)
    print("  Reachable count:", len(reachable))

    # 7. Terminal states
    print("\n[7] Terminal states (structural):")
    terminals = space.terminal_states()
    print("  Terminal IDs:", sorted(terminals))

    # 8. path_exists
    print("\n[8] path_exists:")
    src = states_list[0].state_id
    dst = states_list[-1].state_id
    print("  path_exists({}, {}) = {}".format(src[:16], dst[:16], space.path_exists(src, dst)))
    print("  path_exists(dst, src) = {}".format(space.path_exists(dst, src)))

    # 9. space_summary
    print("\n[9] Space summary:")
    print(space.space_summary())

    # 10. GenerationContext
    print("\n[10] GenerationContext:")
    ctx = GenerationContext(
        context_id="ctx-001",
        cover_id="cover-abc",
        obligation_ids=frozenset(["obl-1", "obl-2"]),
        evidence_ids=frozenset(["ev-a", "ev-b", "ev-c"]),
        context_tier=TrustTier.VERIFIED,
    )
    print("  summary:", ctx.summary())
    print("  context_key:", ctx.context_key())
    print("  to_judgment_tuple:", ctx.to_judgment_tuple())
    for kind in GenStateKind:
        print("  is_ready_for({}) = {}".format(kind.name, ctx.is_ready_for(kind)))

    # 11. StateSpaceExplorer
    print("\n[11] StateSpaceExplorer:")
    explorer = StateSpaceExplorer()
    for s in states_list:
        explorer.add_state(s)
    for t in transitions_list:
        explorer.add_transition(t)
    reported = explorer.report()
    print("  Reported space: states={}, transitions={}".format(
        len(reported.states), len(reported.transitions)
    ))

    # 12. find_complete_paths
    print("\n[12] Complete paths from explorer:")
    paths = explorer.find_complete_paths()
    print("  Found {} paths to terminal states".format(len(paths)))
    for p in paths[:3]:
        print("    Path length:", len(p))

    # 13. find_path_to_completion
    print("\n[13] find_path_to_completion:")
    path = find_path_to_completion(space, states_list[0].state_id)
    print("  Path:", path)

    # 14. enumerate_states
    print("\n[14] enumerate_states:")
    for s in enumerate_states(space)[:4]:
        print("  [{kind}] {sid}".format(kind=s.kind.name, sid=s.state_id))

    print("\n[done]")
