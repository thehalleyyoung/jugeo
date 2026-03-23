"""
State space models for generation (theory2.tex Ch40).

This module implements the core data structures for the generation state space:
a judgment-geometry search framework where generation is modeled as navigation
through semantic states.

Overview
--------
Large-scale generation in jugeo is treated as a search problem over a structured
state space. Each node in this space (a SemanticState) represents one possible
configuration of the generation process: which sections are assigned to which
patches, which treaties are in force, and which obligations have been closed.

Edges in this graph (StateTransitions) represent semantic moves: proposing a
new section assignment, retracting one, refining an existing one, forming or
breaking a treaty. The goal is to reach a terminal/goal state where:
1. Descent succeeds on the fully-assembled cover.
2. All obligations are closed.
3. All treaties are ratified.

Key Classes
-----------
- SemanticState: a node in the state space (an assignment snapshot)
- StateTransition: a directed edge, with type, cost, and delta info
- GenerationStateSpace: the full graph of states and transitions
- ConvergenceMetric: tracks convergence quality over time

Mathematical Background
-----------------------
Let P = {p_1, ..., p_n} be the set of patches in a cover C.
Let S = {s_1, s_2, ...} be the universe of available sections.
A SemanticState sigma is a partial function sigma: P -> S assigning sections to patches.
The state space Sigma = {sigma | sigma: P ->_partial S} is exponentially large but
practically small because generation strategies severely restrict valid transitions.

Transitions
-----------
Six canonical transition types are defined:
  - propose:     add or change a patch assignment
  - retract:     remove a patch assignment
  - refine:      replace a section with a more specific one
  - generalize:  replace a section with a more general one
  - treaty_form: create a new compatibility treaty between adjacent patches
  - treaty_break: dissolve an existing treaty

Convergence
-----------
A search converges when a ConvergenceCriterion is satisfied. Multiple criteria
are available: metric threshold, fixed-point detection, max rounds, or goal state.

Usage Example
-------------
::

    from jugeo.generation.state_space.models import (
        SemanticState, StateTransition, GenerationStateSpace,
        ConvergenceMetric, make_initial_state, make_goal_state,
        make_propose_transition, make_retract_transition, make_linear_space
    )

    # Create an initial state
    state = make_initial_state(["p0", "p1", "p2"])

    # Create a transition
    t = make_propose_transition(state.state_id, "p0", "section_a")

    # Apply transition
    new_state = state.apply_transition(t)

    # Build a linear test space
    space = make_linear_space(5)

See Also
--------
- jugeo.generation.state_space.state_representation: encoding and comparison
- jugeo.generation.state_space.transition_system: transition rules
- jugeo.generation.state_space.convergence: convergence analysis
- theory2.tex Chapter 40: full mathematical treatment
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.treaties import TreatyStatus
    _TREATY_STATUS_AVAILABLE = True
except ImportError:
    _TREATY_STATUS_AVAILABLE = False
    TreatyStatus = None  # type: ignore[misc,assignment]

try:
    from jugeo.geometry.descent import DescentResult
    _DESCENT_RESULT_AVAILABLE = True
except ImportError:
    _DESCENT_RESULT_AVAILABLE = False
    DescentResult = None  # type: ignore[misc,assignment]

try:
    from jugeo.evidence.trust import TrustTier
    _TRUST_TIER_AVAILABLE = True
except ImportError:
    _TRUST_TIER_AVAILABLE = False
    TrustTier = None  # type: ignore[misc,assignment]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_CONVERGENCE_THRESHOLD: float = 0.01
"""Default threshold below which a metric is considered converged."""

DEFAULT_WINDOW_SIZE: int = 10
"""Default rolling-average window for smoothed metrics."""

MAX_STATE_SPACE_SIZE: int = 100_000
"""Hard upper bound on the number of states in a GenerationStateSpace."""

TRANSITION_TYPE_COSTS: Dict[str, float] = {
    "propose": 1.0,
    "retract": 0.5,
    "refine": 2.0,
    "generalize": 3.0,
    "treaty_form": 2.5,
    "treaty_break": 1.5,
}
"""Default cost for each canonical transition type."""

VALID_TRANSITION_TYPES: FrozenSet[str] = frozenset(TRANSITION_TYPE_COSTS.keys())
"""The set of valid transition type strings."""

STATE_SPACE_VERSION: str = "1.0.0"
"""Version string for state space serialisation format."""

MAX_SEARCH_DEPTH: int = 200
"""Default maximum depth for path-finding algorithms."""

SMOOTHING_EPSILON: float = 1e-9
"""Small value to avoid division-by-zero in metric computations."""

# Type aliases
PatchId = str
SectionLabel = str
TreatyId = str
StateId = str
ObligationId = str
MetadataDict = Dict[str, Any]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TransitionType(Enum):
    """Canonical types of transitions in the generation state space.

    Each type corresponds to a semantic move the generation engine can make.
    The cost and reversibility of each type differ; see TRANSITION_TYPE_COSTS
    and the TransitionCostModel in transition_system.py.

    Members
    -------
    PROPOSE     : Assign a new section to a patch (or reassign).
    RETRACT     : Remove a section assignment from a patch.
    REFINE      : Replace a section assignment with a strictly more specific one.
    GENERALIZE  : Replace a section assignment with a strictly more general one.
    TREATY_FORM : Establish a new compatibility treaty between two patches.
    TREATY_BREAK: Dissolve an existing treaty.
    """

    PROPOSE = "propose"
    RETRACT = "retract"
    REFINE = "refine"
    GENERALIZE = "generalize"
    TREATY_FORM = "treaty_form"
    TREATY_BREAK = "treaty_break"

    @classmethod
    def from_str(cls, s: str) -> "TransitionType":
        """Convert a string to a TransitionType, raising ValueError if unknown.

        Args:
            s: A string matching one of the TransitionType values.

        Returns:
            The corresponding TransitionType member.

        Raises:
            ValueError: If *s* is not a recognised transition type.
        """
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(
            f"Unknown transition type: {s!r}. "
            f"Valid values: {[m.value for m in cls]}"
        )

    def default_cost(self) -> float:
        """Return the default cost for this transition type.

        Returns:
            A float cost value from TRANSITION_TYPE_COSTS.
        """
        return TRANSITION_TYPE_COSTS.get(self.value, 1.0)

    def is_reversible(self) -> bool:
        """Return True if this transition type has a natural inverse.

        Returns:
            True for propose/retract/treaty_form/treaty_break pairs.
        """
        return self in {
            TransitionType.PROPOSE,
            TransitionType.RETRACT,
            TransitionType.TREATY_FORM,
            TransitionType.TREATY_BREAK,
        }


class StateStatus(Enum):
    """Lifecycle status of a SemanticState.

    Members
    -------
    INITIAL      : The seed state before any transitions are applied.
    INTERMEDIATE : A state reached by at least one transition, not yet terminal.
    TERMINAL     : A state from which no further transitions will be attempted.
    GOAL         : A terminal state that satisfies the generation goal.
    ERROR        : A state that represents an error or invalid configuration.
    """

    INITIAL = "initial"
    INTERMEDIATE = "intermediate"
    TERMINAL = "terminal"
    GOAL = "goal"
    ERROR = "error"

    def is_final(self) -> bool:
        """Return True if no further search should proceed from this state.

        Returns:
            True for TERMINAL, GOAL, and ERROR.
        """
        return self in {StateStatus.TERMINAL, StateStatus.GOAL, StateStatus.ERROR}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class StateSpaceError(Exception):
    """Base exception for all state space errors.

    Subclass this for specific error categories. All jugeo.generation.state_space
    code raises this or a subclass rather than bare RuntimeError.

    Attributes
    ----------
    message : str
        Human-readable description of the error.
    context : dict
        Optional contextual data (e.g. state_id, transition_id).
    """

    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context: Dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}({self.message!r}, "
            f"context={self.context!r})"
        )


class InvalidTransitionError(StateSpaceError):
    """Raised when a transition cannot be applied to a state."""


class StateNotFoundError(StateSpaceError):
    """Raised when a state_id is not found in a GenerationStateSpace."""


class ConvergenceError(StateSpaceError):
    """Raised when convergence machinery encounters an inconsistency."""


class ObligationConflictError(StateSpaceError):
    """Raised when open and closed obligations overlap."""


# ---------------------------------------------------------------------------
# SemanticState
# ---------------------------------------------------------------------------

@dataclass
class SemanticState:
    """A single point in the generation state space.

    A SemanticState captures the full configuration of the generation process
    at a given moment: which section has been assigned to each patch, the
    current status of all treaties, and the open/closed obligation sets.

    Geometrically this is a point in the product space::

        Sigma = (S ∪ {None})^P × TreatyStatus^T × 2^Ob

    where P is the patch set, T is the treaty set, and Ob is the obligation set.

    Attributes
    ----------
    state_id : str
        Unique identifier for this state (UUID4 by default).
    patch_assignments : dict[str, str]
        Maps each assigned patch to its section label.
        Patches not in this dict are considered unassigned.
    treaty_status : dict[str, str]
        Maps treaty_id to a status string (e.g. "PROPOSED", "RATIFIED").
    obligations_closed : set[str]
        Obligation IDs that have been satisfied.
    obligations_open : set[str]
        Obligation IDs that are still pending.
    descent_result_cache : dict[str, Any]
        Cache of DescentResult objects keyed by a hash of the state config.
    generation_round : int
        The search round in which this state was created (0 = initial).
    is_terminal : bool
        True if the search should not expand further from this state.
    is_goal_state : bool
        True if this state satisfies the generation goal.
    metadata : dict[str, Any]
        Arbitrary metadata (e.g. heuristic scores, provenance).
    """

    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patch_assignments: Dict[PatchId, SectionLabel] = field(default_factory=dict)
    treaty_status: Dict[TreatyId, str] = field(default_factory=dict)
    obligations_closed: Set[ObligationId] = field(default_factory=set)
    obligations_open: Set[ObligationId] = field(default_factory=set)
    descent_result_cache: Dict[str, Any] = field(default_factory=dict)
    generation_round: int = 0
    is_terminal: bool = False
    is_goal_state: bool = False
    metadata: MetadataDict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def apply_transition(self, transition: "StateTransition") -> "SemanticState":
        """Return a new SemanticState obtained by applying *transition*.

        The returned state is a deep copy of self with the transition's
        patch_delta, obligation_adds, and obligation_closes applied.

        Args:
            transition: The StateTransition to apply.

        Returns:
            A new SemanticState reflecting the transition.

        Raises:
            InvalidTransitionError: If the transition's source_state_id does not
                match this state's state_id (when both are non-empty).
        """
        if (transition.source_state_id
                and transition.source_state_id != self.state_id):
            raise InvalidTransitionError(
                f"Transition source {transition.source_state_id!r} does not "
                f"match state {self.state_id!r}",
                context={"transition_id": transition.transition_id},
            )

        new_state = self.copy()
        new_state.state_id = transition.target_state_id or str(uuid.uuid4())
        new_state.generation_round = self.generation_round + 1
        new_state.descent_result_cache = {}  # invalidate cache

        # Apply patch delta
        for patch, section in transition.patch_delta.items():
            if section is None:
                new_state.patch_assignments.pop(patch, None)
            else:
                new_state.patch_assignments[patch] = section

        # Apply obligation changes
        for ob_id in transition.obligation_closes:
            new_state.obligations_open.discard(ob_id)
            new_state.obligations_closed.add(ob_id)
        for ob_id in transition.obligation_adds:
            if ob_id not in new_state.obligations_closed:
                new_state.obligations_open.add(ob_id)

        return new_state

    def copy(self) -> "SemanticState":
        """Return a deep copy of this state.

        Returns:
            A new SemanticState with all mutable fields deep-copied.
        """
        return SemanticState(
            state_id=self.state_id,
            patch_assignments=dict(self.patch_assignments),
            treaty_status=dict(self.treaty_status),
            obligations_closed=set(self.obligations_closed),
            obligations_open=set(self.obligations_open),
            descent_result_cache=dict(self.descent_result_cache),
            generation_round=self.generation_round,
            is_terminal=self.is_terminal,
            is_goal_state=self.is_goal_state,
            metadata=copy.deepcopy(self.metadata),
        )

    def distance_to(self, other: "SemanticState") -> float:
        """Compute Jaccard distance between this state and *other*.

        Distance is measured on the set of (patch, section) pairs.
        Two identical states have distance 0.0; two disjoint states have 1.0.

        Args:
            other: Another SemanticState.

        Returns:
            Float in [0.0, 1.0] representing semantic distance.
        """
        self_items = set(self.patch_assignments.items())
        other_items = set(other.patch_assignments.items())
        union = self_items | other_items
        if not union:
            return 0.0
        intersection = self_items & other_items
        return 1.0 - len(intersection) / len(union)

    def is_valid(self) -> bool:
        """Return True if this state is internally consistent.

        A state is valid when:
        1. obligations_closed and obligations_open are disjoint.
        2. All treaty status values are non-empty strings.
        3. All patch_assignment values are non-empty strings.

        Returns:
            True if valid, False otherwise.
        """
        if self.obligations_closed & self.obligations_open:
            return False
        for v in self.treaty_status.values():
            if not isinstance(v, str) or not v:
                return False
        for v in self.patch_assignments.values():
            if not isinstance(v, str) or not v:
                return False
        return True

    def summarize(self) -> str:
        """Return a multi-line human-readable summary of this state.

        Returns:
            A formatted string with all key fields.
        """
        patch_sample = sorted(self.patch_assignments.keys())[:5]
        patch_str = ", ".join(patch_sample)
        if len(self.patch_assignments) > 5:
            patch_str += "..."
        section_sample = sorted(set(self.patch_assignments.values()))[:5]
        lines = [
            f"SemanticState(id={self.state_id[:8]}...)",
            f"  round={self.generation_round}  terminal={self.is_terminal}"
            f"  goal={self.is_goal_state}",
            f"  patches assigned: {len(self.patch_assignments)}  ({patch_str})",
            f"  sections: {', '.join(section_sample)}",
            f"  treaties: {len(self.treaty_status)}",
            f"  obligations open: {len(self.obligations_open)}"
            f"  closed: {len(self.obligations_closed)}",
            f"  coverage: {self.coverage():.2%}"
            f"  open_ratio: {self.open_ratio():.2%}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this state to a JSON-compatible dictionary.

        Returns:
            A dict with all fields; sets are converted to sorted lists.
        """
        return {
            "state_id": self.state_id,
            "patch_assignments": dict(self.patch_assignments),
            "treaty_status": dict(self.treaty_status),
            "obligations_closed": sorted(self.obligations_closed),
            "obligations_open": sorted(self.obligations_open),
            "descent_result_cache": {},
            "generation_round": self.generation_round,
            "is_terminal": self.is_terminal,
            "is_goal_state": self.is_goal_state,
            "metadata": self.metadata,
            "_version": STATE_SPACE_VERSION,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SemanticState":
        """Deserialise a SemanticState from a dictionary.

        Args:
            d: A dict as produced by to_dict().

        Returns:
            A SemanticState instance.
        """
        return cls(
            state_id=d.get("state_id", str(uuid.uuid4())),
            patch_assignments=dict(d.get("patch_assignments", {})),
            treaty_status=dict(d.get("treaty_status", {})),
            obligations_closed=set(d.get("obligations_closed", [])),
            obligations_open=set(d.get("obligations_open", [])),
            descent_result_cache={},
            generation_round=int(d.get("generation_round", 0)),
            is_terminal=bool(d.get("is_terminal", False)),
            is_goal_state=bool(d.get("is_goal_state", False)),
            metadata=dict(d.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Helper / derived property methods
    # ------------------------------------------------------------------

    def coverage(self) -> float:
        """Fraction of known patches that have an assignment.

        Returns:
            Float in [0.0, 1.0].
        """
        total = len(self.patch_assignments) + len(self.obligations_open)
        if total == 0:
            return 0.0
        return len(self.patch_assignments) / max(1, total)

    def open_ratio(self) -> float:
        """Fraction of all obligations that are still open.

        Returns:
            Float in [0.0, 1.0].
        """
        total = len(self.obligations_open) + len(self.obligations_closed)
        if total == 0:
            return 0.0
        return len(self.obligations_open) / total

    def closed_ratio(self) -> float:
        """Fraction of all obligations that are closed.

        Returns:
            Float in [0.0, 1.0].
        """
        return 1.0 - self.open_ratio()

    def get_treaty_summary(self) -> str:
        """Return a compact string summarising treaty statuses.

        Returns:
            Comma-separated 'id=status' pairs, sorted by id.
        """
        if not self.treaty_status:
            return "(no treaties)"
        return ", ".join(
            f"{k}={v}" for k, v in sorted(self.treaty_status.items())
        )

    def has_section(self, section: str) -> bool:
        """Return True if *section* is assigned to any patch in this state.

        Args:
            section: A section label to search for.

        Returns:
            True if found in any patch assignment.
        """
        return section in self.patch_assignments.values()

    def get_section(self, patch: str) -> Optional[str]:
        """Return the section assigned to *patch*, or None.

        Args:
            patch: A patch identifier.

        Returns:
            The section label, or None if the patch is unassigned.
        """
        return self.patch_assignments.get(patch)

    def all_obligations_closed(self) -> bool:
        """Return True when there are no open obligations.

        Returns:
            True iff obligations_open is empty.
        """
        return len(self.obligations_open) == 0

    def all_treaties_ratified(self) -> bool:
        """Return True when all treaties have status 'RATIFIED'.

        Returns:
            True iff every value in treaty_status equals 'RATIFIED'.
        """
        return all(v == "RATIFIED" for v in self.treaty_status.values())

    def get_unassigned_patches(self, all_patches: Optional[Set[str]] = None) -> Set[str]:
        """Return the set of patches that have no assignment.

        Args:
            all_patches: The complete set of patches. If None, uses
                         obligations_open as a proxy.

        Returns:
            Set of patch IDs without assignments.
        """
        known = all_patches or self.obligations_open
        return known - set(self.patch_assignments.keys())

    def status(self) -> StateStatus:
        """Return the lifecycle StateStatus of this state.

        Returns:
            StateStatus.GOAL if goal, TERMINAL if terminal (non-goal),
            INITIAL if round == 0, otherwise INTERMEDIATE.
        """
        if self.is_goal_state:
            return StateStatus.GOAL
        if self.is_terminal:
            return StateStatus.TERMINAL
        if self.generation_round == 0:
            return StateStatus.INITIAL
        return StateStatus.INTERMEDIATE

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"SemanticState(id={self.state_id[:8]}..., "
            f"patches={len(self.patch_assignments)}, "
            f"round={self.generation_round}, "
            f"goal={self.is_goal_state})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticState):
            return NotImplemented
        return self.state_id == other.state_id

    def __hash__(self) -> int:
        return hash(self.state_id)


# ---------------------------------------------------------------------------
# StateTransition
# ---------------------------------------------------------------------------

@dataclass
class StateTransition:
    """A directed edge in the generation state space.

    A StateTransition encodes the semantic move that takes one SemanticState
    to another. It carries the patch delta, obligation changes, cost metadata,
    and a validity certificate.

    Attributes
    ----------
    transition_id : str
        Unique identifier (UUID4 by default).
    source_state_id : str
        ID of the state this transition originates from.
    target_state_id : str
        ID of the state this transition leads to.
    transition_type : str
        One of the VALID_TRANSITION_TYPES strings.
    cost : float
        The cost of applying this transition.
    semantic_distance : float
        The semantic distance between source and target states.
    validity_certificate : str
        Opaque certificate from the validator (empty if not validated).
    applied_at : float
        Unix timestamp of when this transition was applied.
    patch_delta : dict[str, str | None]
        Maps patch IDs to their new section label, or None to remove.
    obligation_adds : set[str]
        Obligation IDs to be added to obligations_open.
    obligation_closes : set[str]
        Obligation IDs to be moved from open to closed.
    metadata : dict[str, Any]
        Arbitrary metadata.
    """

    transition_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_state_id: str = ""
    target_state_id: str = ""
    transition_type: str = "propose"
    cost: float = 1.0
    semantic_distance: float = 0.0
    validity_certificate: str = ""
    applied_at: float = field(default_factory=time.time)
    patch_delta: Dict[PatchId, Optional[SectionLabel]] = field(
        default_factory=dict
    )
    obligation_adds: Set[ObligationId] = field(default_factory=set)
    obligation_closes: Set[ObligationId] = field(default_factory=set)
    metadata: MetadataDict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return True if this transition is structurally valid.

        Checks:
        1. transition_type is recognised.
        2. cost >= 0.
        3. patch_delta is not None.
        4. obligation_adds and obligation_closes are disjoint.

        Returns:
            True if valid.
        """
        if self.transition_type not in VALID_TRANSITION_TYPES:
            return False
        if self.cost < 0:
            return False
        if self.patch_delta is None:
            return False
        if self.obligation_adds & self.obligation_closes:
            return False
        return True

    def inverse(self) -> "StateTransition":
        """Return the logical inverse of this transition.

        Returns:
            A new StateTransition representing the reverse move.
        """
        inverse_type_map = {
            "propose": "retract",
            "retract": "propose",
            "refine": "generalize",
            "generalize": "refine",
            "treaty_form": "treaty_break",
            "treaty_break": "treaty_form",
        }
        inv_type = inverse_type_map.get(self.transition_type, self.transition_type)

        inv_delta: Dict[PatchId, Optional[SectionLabel]] = {}
        for patch, section in self.patch_delta.items():
            if section is not None:
                inv_delta[patch] = None

        return StateTransition(
            source_state_id=self.target_state_id,
            target_state_id=self.source_state_id,
            transition_type=inv_type,
            cost=self.cost,
            semantic_distance=self.semantic_distance,
            patch_delta=inv_delta,
            obligation_adds=set(self.obligation_closes),
            obligation_closes=set(self.obligation_adds),
            metadata={"inverse_of": self.transition_id},
        )

    def compose_with(
        self, other: "StateTransition"
    ) -> Optional["StateTransition"]:
        """Compose this transition with *other* if they chain.

        Args:
            other: The transition to compose with.

        Returns:
            A new StateTransition representing the composed move,
            or None if the transitions do not chain.
        """
        if self.target_state_id != other.source_state_id:
            return None

        composed_delta = dict(self.patch_delta)
        composed_delta.update(other.patch_delta)

        composed_adds = (
            (self.obligation_adds | other.obligation_adds)
            - other.obligation_closes
        )
        composed_closes = self.obligation_closes | other.obligation_closes

        return StateTransition(
            source_state_id=self.source_state_id,
            target_state_id=other.target_state_id,
            transition_type=f"{self.transition_type}+{other.transition_type}",
            cost=self.cost + other.cost,
            semantic_distance=self.semantic_distance + other.semantic_distance,
            patch_delta=composed_delta,
            obligation_adds=composed_adds,
            obligation_closes=composed_closes,
            metadata={
                "composed_from": [self.transition_id, other.transition_id]
            },
        )

    def affects_patch(self, patch: str) -> bool:
        """Return True if this transition modifies *patch*.

        Args:
            patch: A patch identifier.

        Returns:
            True if patch is in patch_delta.
        """
        return patch in self.patch_delta

    def transition_type_enum(self) -> Optional[TransitionType]:
        """Return the TransitionType enum for this transition's type string.

        Returns:
            TransitionType or None if not recognised.
        """
        try:
            return TransitionType.from_str(self.transition_type)
        except ValueError:
            return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this transition to a JSON-compatible dictionary.

        Returns:
            A dict with all fields.
        """
        return {
            "transition_id": self.transition_id,
            "source_state_id": self.source_state_id,
            "target_state_id": self.target_state_id,
            "transition_type": self.transition_type,
            "cost": self.cost,
            "semantic_distance": self.semantic_distance,
            "validity_certificate": self.validity_certificate,
            "applied_at": self.applied_at,
            "patch_delta": {k: v for k, v in self.patch_delta.items()},
            "obligation_adds": sorted(self.obligation_adds),
            "obligation_closes": sorted(self.obligation_closes),
            "metadata": self.metadata,
            "_version": STATE_SPACE_VERSION,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StateTransition":
        """Deserialise a StateTransition from a dictionary.

        Args:
            d: A dict as produced by to_dict().

        Returns:
            A StateTransition instance.
        """
        return cls(
            transition_id=d.get("transition_id", str(uuid.uuid4())),
            source_state_id=d.get("source_state_id", ""),
            target_state_id=d.get("target_state_id", ""),
            transition_type=d.get("transition_type", "propose"),
            cost=float(d.get("cost", 1.0)),
            semantic_distance=float(d.get("semantic_distance", 0.0)),
            validity_certificate=d.get("validity_certificate", ""),
            applied_at=float(d.get("applied_at", time.time())),
            patch_delta={k: v for k, v in d.get("patch_delta", {}).items()},
            obligation_adds=set(d.get("obligation_adds", [])),
            obligation_closes=set(d.get("obligation_closes", [])),
            metadata=dict(d.get("metadata", {})),
        )

    def __repr__(self) -> str:
        return (
            f"StateTransition(id={self.transition_id[:8]}..., "
            f"type={self.transition_type!r}, "
            f"cost={self.cost:.2f}, "
            f"patches={list(self.patch_delta.keys())})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StateTransition):
            return NotImplemented
        return self.transition_id == other.transition_id

    def __hash__(self) -> int:
        return hash(self.transition_id)


# ---------------------------------------------------------------------------
# GenerationStateSpace
# ---------------------------------------------------------------------------

@dataclass
class GenerationStateSpace:
    """The full directed graph of semantic states and transitions.

    A GenerationStateSpace is an explicit graph representation:
    - Nodes are SemanticState objects, keyed by state_id.
    - Edges are StateTransition objects in a flat list.

    Attributes
    ----------
    space_id : str
        Unique identifier for this state space instance.
    states : dict[str, SemanticState]
        All states known to this space, keyed by state_id.
    transitions : list[StateTransition]
        All transitions (edges) in this space.
    initial_state_id : str | None
        The state from which search begins.
    goal_states : set[str]
        State IDs that have been identified as goal states.
    current_state_id : str | None
        The state currently under consideration in an ongoing search.
    metadata : dict[str, Any]
        Arbitrary metadata.
    """

    space_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    states: Dict[StateId, SemanticState] = field(default_factory=dict)
    transitions: List[StateTransition] = field(default_factory=list)
    initial_state_id: Optional[StateId] = None
    goal_states: Set[StateId] = field(default_factory=set)
    current_state_id: Optional[StateId] = None
    metadata: MetadataDict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_state(self, state: SemanticState) -> None:
        """Add a state to this space.

        Args:
            state: The SemanticState to add.
        """
        self.states[state.state_id] = state
        if self.initial_state_id is None:
            self.initial_state_id = state.state_id
        if state.is_goal_state:
            self.goal_states.add(state.state_id)

    def add_transition(self, transition: StateTransition) -> None:
        """Add a transition (edge) to this space.

        Args:
            transition: The StateTransition to add.
        """
        self.transitions.append(transition)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_state(self, state_id: StateId) -> Optional[SemanticState]:
        """Return the state with the given ID, or None.

        Args:
            state_id: The state identifier to look up.

        Returns:
            The SemanticState, or None if not found.
        """
        return self.states.get(state_id)

    def get_transitions_from(
        self, state_id: StateId
    ) -> List[StateTransition]:
        """Return all transitions that originate from *state_id*.

        Args:
            state_id: Source state identifier.

        Returns:
            List of StateTransition objects.
        """
        return [
            t for t in self.transitions if t.source_state_id == state_id
        ]

    def get_transitions_to(
        self, state_id: StateId
    ) -> List[StateTransition]:
        """Return all transitions that lead to *state_id*.

        Args:
            state_id: Target state identifier.

        Returns:
            List of StateTransition objects.
        """
        return [
            t for t in self.transitions if t.target_state_id == state_id
        ]

    def get_neighbors(self, state_id: StateId) -> List[StateId]:
        """Return IDs of states directly reachable from *state_id*.

        Args:
            state_id: Source state identifier.

        Returns:
            List of target state IDs.
        """
        return [
            t.target_state_id
            for t in self.get_transitions_from(state_id)
            if t.target_state_id in self.states
        ]

    def num_states(self) -> int:
        """Return the number of states in this space."""
        return len(self.states)

    def num_transitions(self) -> int:
        """Return the number of transitions in this space."""
        return len(self.transitions)

    # ------------------------------------------------------------------
    # Path finding
    # ------------------------------------------------------------------

    def find_path(
        self,
        start_id: StateId,
        goal_id: StateId,
    ) -> Optional[List[StateId]]:
        """Find a path from *start_id* to *goal_id* using BFS.

        Args:
            start_id: ID of the start state.
            goal_id: ID of the goal state.

        Returns:
            A list of state IDs forming a path (inclusive), or None.
        """
        if start_id not in self.states or goal_id not in self.states:
            return None
        if start_id == goal_id:
            return [start_id]

        visited: Set[StateId] = {start_id}
        queue: deque = deque([[start_id]])

        while queue:
            path = queue.popleft()
            current = path[-1]
            for neighbor in self.get_neighbors(current):
                if neighbor == goal_id:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def compute_reachable(self, state_id: StateId) -> Set[StateId]:
        """Return all state IDs reachable from *state_id* via BFS.

        Args:
            state_id: Starting state identifier.

        Returns:
            Set of reachable state IDs (including start itself).
        """
        if state_id not in self.states:
            return set()
        visited: Set[StateId] = set()
        queue: deque = deque([state_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for neighbor in self.get_neighbors(current):
                if neighbor not in visited:
                    queue.append(neighbor)
        return visited

    def is_connected(self) -> bool:
        """Return True if all states are reachable from the initial state.

        Returns:
            True if connected.
        """
        if not self.states:
            return True
        start = self.initial_state_id or next(iter(self.states))
        reachable = self.compute_reachable(start)
        return reachable == set(self.states.keys())

    # ------------------------------------------------------------------
    # Goal states
    # ------------------------------------------------------------------

    def get_goal_states(self) -> List[SemanticState]:
        """Return all SemanticState objects marked as goal states.

        Returns:
            List of SemanticState where is_goal_state == True.
        """
        return [s for s in self.states.values() if s.is_goal_state]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self) -> Dict[str, Any]:
        """Return a statistics dictionary about this space.

        Returns:
            Dict with keys: num_states, num_transitions, num_goal_states,
            is_connected, avg_out_degree, has_initial, has_current.
        """
        n = self.num_states()
        t = self.num_transitions()
        avg_degree = t / max(1, n)
        return {
            "space_id": self.space_id,
            "num_states": n,
            "num_transitions": t,
            "num_goal_states": len(self.goal_states),
            "is_connected": self.is_connected(),
            "avg_out_degree": avg_degree,
            "has_initial": self.initial_state_id is not None,
            "has_current": self.current_state_id is not None,
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this space to a JSON-compatible dict.

        Returns:
            A nested dict representation.
        """
        return {
            "space_id": self.space_id,
            "states": {sid: s.to_dict() for sid, s in self.states.items()},
            "transitions": [t.to_dict() for t in self.transitions],
            "initial_state_id": self.initial_state_id,
            "goal_states": sorted(self.goal_states),
            "current_state_id": self.current_state_id,
            "metadata": self.metadata,
            "_version": STATE_SPACE_VERSION,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GenerationStateSpace":
        """Deserialise a GenerationStateSpace from a dictionary.

        Args:
            d: A dict as produced by to_dict().

        Returns:
            A GenerationStateSpace instance.
        """
        space = cls(
            space_id=d.get("space_id", str(uuid.uuid4())),
            initial_state_id=d.get("initial_state_id"),
            goal_states=set(d.get("goal_states", [])),
            current_state_id=d.get("current_state_id"),
            metadata=dict(d.get("metadata", {})),
        )
        for sd in d.get("states", {}).values():
            space.states[sd["state_id"]] = SemanticState.from_dict(sd)
        for td in d.get("transitions", []):
            space.transitions.append(StateTransition.from_dict(td))
        return space

    def __repr__(self) -> str:
        return (
            f"GenerationStateSpace(id={self.space_id[:8]}..., "
            f"states={self.num_states()}, "
            f"transitions={self.num_transitions()})"
        )


# ---------------------------------------------------------------------------
# ConvergenceMetric
# ---------------------------------------------------------------------------

@dataclass
class ConvergenceMetric:
    """Tracks a scalar convergence metric over time.

    A ConvergenceMetric records one floating-point quality score per search
    step. It maintains a rolling smoothed average and computes trend/variance
    from the recent history window.

    The metric semantics: lower values indicate better convergence.

    Attributes
    ----------
    metric_id : str
        Unique identifier (UUID4 by default).
    current_value : float
        The most recently recorded raw value.
    history : list[float]
        All recorded values in order.
    smoothed_value : float
        Rolling average of the last window_size values.
    trend : str
        One of "improving", "worsening", "stable".
    convergence_threshold : float
        Value below which we consider the metric converged.
    window_size : int
        Number of recent values used for smoothing and trend.
    metadata : dict[str, Any]
        Arbitrary metadata.
    """

    metric_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    current_value: float = 1.0
    history: List[float] = field(default_factory=list)
    smoothed_value: float = 1.0
    trend: str = "stable"
    convergence_threshold: float = DEFAULT_CONVERGENCE_THRESHOLD
    window_size: int = DEFAULT_WINDOW_SIZE
    metadata: MetadataDict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, new_value: float) -> None:
        """Record a new metric value and update smoothed/trend.

        Args:
            new_value: The new raw metric value (lower = better).
        """
        self.current_value = new_value
        self.history.append(new_value)

        window = self.history[-self.window_size:]
        self.smoothed_value = sum(window) / len(window)
        self.trend = self.get_trend()

    def is_converged(self) -> bool:
        """Return True if the metric has converged.

        Returns:
            True if smoothed_value < threshold or recent variance < epsilon.
        """
        if self.smoothed_value < self.convergence_threshold:
            return True
        if len(self.history) >= self.window_size:
            if self.compute_variance() < SMOOTHING_EPSILON:
                return True
        return False

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def get_trend(self) -> str:
        """Compute the current trend from recent history.

        Returns:
            "improving", "worsening", or "stable".
        """
        if len(self.history) < 2:
            return "stable"
        window = self.history[-self.window_size:]
        if len(window) < 2:
            return "stable"
        mid = len(window) // 2
        first_half = sum(window[:mid]) / max(1, mid)
        second_half = sum(window[mid:]) / max(1, len(window) - mid)
        diff = first_half - second_half
        if diff > self.convergence_threshold:
            return "improving"
        if diff < -self.convergence_threshold:
            return "worsening"
        return "stable"

    def get_smoothed(self) -> float:
        """Return the current smoothed value.

        Returns:
            The rolling average of the last window_size values.
        """
        return self.smoothed_value

    def compute_variance(self) -> float:
        """Compute variance of recent history window.

        Returns:
            Float variance; 0.0 if fewer than 2 values available.
        """
        window = self.history[-self.window_size:]
        if len(window) < 2:
            return 0.0
        mean = sum(window) / len(window)
        return sum((x - mean) ** 2 for x in window) / len(window)

    def compute_rate(self) -> float:
        """Compute the rate of change (last value minus first in window).

        Returns:
            Float rate (negative means improving).
        """
        window = self.history[-self.window_size:]
        if len(window) < 2:
            return 0.0
        return window[-1] - window[0]

    def reset(self) -> None:
        """Reset this metric to its initial state."""
        self.current_value = 1.0
        self.history = []
        self.smoothed_value = 1.0
        self.trend = "stable"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this metric to a JSON-compatible dict.

        Returns:
            A dict with all fields.
        """
        return {
            "metric_id": self.metric_id,
            "current_value": self.current_value,
            "history": list(self.history),
            "smoothed_value": self.smoothed_value,
            "trend": self.trend,
            "convergence_threshold": self.convergence_threshold,
            "window_size": self.window_size,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        return (
            f"ConvergenceMetric(id={self.metric_id[:8]}..., "
            f"current={self.current_value:.4f}, "
            f"smoothed={self.smoothed_value:.4f}, "
            f"trend={self.trend!r}, "
            f"converged={self.is_converged()})"
        )


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def make_initial_state(patches: List[str]) -> SemanticState:
    """Create an initial SemanticState with given patches, all unassigned.

    Args:
        patches: List of patch IDs to include in the state's obligation set.

    Returns:
        A new SemanticState at generation_round 0.
    """
    return SemanticState(
        generation_round=0,
        obligations_open=set(patches),
    )


def make_goal_state(
    patches: List[str],
    sections: Optional[Dict[str, str]] = None,
) -> SemanticState:
    """Create a goal SemanticState with all patches assigned and obligations closed.

    Args:
        patches: List of patch IDs.
        sections: Optional mapping of patch→section.

    Returns:
        A new SemanticState with is_goal_state=True.
    """
    if sections is None:
        sections = {p: f"section_{p}" for p in patches}
    return SemanticState(
        patch_assignments=dict(sections),
        obligations_closed=set(patches),
        obligations_open=set(),
        is_goal_state=True,
        is_terminal=True,
    )


def make_propose_transition(
    source_id: str,
    patch: str,
    section: str,
    cost: Optional[float] = None,
) -> StateTransition:
    """Create a PROPOSE transition that assigns *section* to *patch*.

    Args:
        source_id: The source state ID.
        patch: The patch to assign.
        section: The section to assign to the patch.
        cost: Optional override cost.

    Returns:
        A new StateTransition of type "propose".
    """
    return StateTransition(
        source_state_id=source_id,
        target_state_id=str(uuid.uuid4()),
        transition_type="propose",
        cost=cost if cost is not None else TRANSITION_TYPE_COSTS["propose"],
        patch_delta={patch: section},
        obligation_closes={patch},
    )


def make_retract_transition(
    source_id: str,
    patch: str,
    cost: Optional[float] = None,
) -> StateTransition:
    """Create a RETRACT transition that removes *patch*'s assignment.

    Args:
        source_id: The source state ID.
        patch: The patch to unassign.
        cost: Optional override cost.

    Returns:
        A new StateTransition of type "retract".
    """
    return StateTransition(
        source_state_id=source_id,
        target_state_id=str(uuid.uuid4()),
        transition_type="retract",
        cost=cost if cost is not None else TRANSITION_TYPE_COSTS["retract"],
        patch_delta={patch: None},
        obligation_adds={patch},
    )


def make_linear_space(n: int) -> GenerationStateSpace:
    """Build a linear GenerationStateSpace: state_0 → state_1 → ... → state_(n-1).

    The last state is marked as a goal state.

    Args:
        n: Number of states (must be >= 1).

    Returns:
        A GenerationStateSpace with n states and n-1 transitions.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")

    space = GenerationStateSpace()
    states: List[SemanticState] = []

    for i in range(n):
        s = SemanticState(
            patch_assignments={f"p{i}": f"section_{i}"},
            generation_round=i,
            is_goal_state=(i == n - 1),
            is_terminal=(i == n - 1),
        )
        states.append(s)
        space.add_state(s)

    for i in range(n - 1):
        t = StateTransition(
            source_state_id=states[i].state_id,
            target_state_id=states[i + 1].state_id,
            transition_type="propose",
            cost=1.0,
            patch_delta={f"p{i + 1}": f"section_{i + 1}"},
        )
        space.add_transition(t)

    return space


def make_convergence_metric(
    threshold: float = DEFAULT_CONVERGENCE_THRESHOLD,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> ConvergenceMetric:
    """Create a fresh ConvergenceMetric with given parameters.

    Args:
        threshold: Convergence threshold.
        window_size: Rolling average window.

    Returns:
        A new ConvergenceMetric.
    """
    return ConvergenceMetric(
        convergence_threshold=threshold,
        window_size=window_size,
    )


def compute_state_fingerprint(state: SemanticState) -> str:
    """Compute a deterministic SHA-256 fingerprint for a SemanticState.

    Args:
        state: The state to fingerprint.

    Returns:
        A hex SHA-256 digest string.
    """
    payload = json.dumps(
        {
            "patch_assignments": dict(sorted(state.patch_assignments.items())),
            "treaty_status": dict(sorted(state.treaty_status.items())),
            "obligations_closed": sorted(state.obligations_closed),
            "obligations_open": sorted(state.obligations_open),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def states_are_semantically_equal(
    s1: SemanticState, s2: SemanticState
) -> bool:
    """Return True if two states have the same semantic content (ignoring IDs).

    Args:
        s1: First state.
        s2: Second state.

    Returns:
        True if patch_assignments, treaty_status, and obligations match.
    """
    return (
        s1.patch_assignments == s2.patch_assignments
        and s1.treaty_status == s2.treaty_status
        and s1.obligations_closed == s2.obligations_closed
        and s1.obligations_open == s2.obligations_open
    )


def path_cost(path: List[str], space: GenerationStateSpace) -> float:
    """Compute the total transition cost along a path of state IDs.

    Args:
        path: List of state IDs forming a path.
        space: The GenerationStateSpace containing the transitions.

    Returns:
        Total cost (sum of costs of transitions along the path).
    """
    total = 0.0
    for i in range(len(path) - 1):
        src, tgt = path[i], path[i + 1]
        for t in space.get_transitions_from(src):
            if t.target_state_id == tgt:
                total += t.cost
                break
    return total


def build_small_test_space() -> GenerationStateSpace:
    """Build a small 5-state test space for unit testing.

    Creates states s0..s4 with connections:
    s0 -> s1, s0 -> s2, s1 -> s3, s2 -> s3, s3 -> s4(goal)

    Returns:
        A GenerationStateSpace with 5 states and 5 transitions.
    """
    space = GenerationStateSpace()
    states = []
    for i in range(5):
        s = SemanticState(
            patch_assignments={f"p{i}": f"sec_{i}"},
            generation_round=i,
            is_goal_state=(i == 4),
            is_terminal=(i == 4),
        )
        states.append(s)
        space.add_state(s)

    edges = [(0, 1), (0, 2), (1, 3), (2, 3), (3, 4)]
    for src, tgt in edges:
        t = StateTransition(
            source_state_id=states[src].state_id,
            target_state_id=states[tgt].state_id,
            transition_type="propose",
            cost=1.0,
        )
        space.add_transition(t)

    return space


def validate_path(
    path: List[str], space: GenerationStateSpace
) -> Tuple[bool, List[str]]:
    """Validate that a path of state IDs is consistent in a space.

    Args:
        path: List of state IDs.
        space: The GenerationStateSpace.

    Returns:
        Tuple of (is_valid, list_of_error_messages).
    """
    errors: List[str] = []
    for sid in path:
        if sid not in space.states:
            errors.append(f"State {sid!r} not in space")

    for i in range(len(path) - 1):
        src, tgt = path[i], path[i + 1]
        neighbors = space.get_neighbors(src)
        if tgt not in neighbors:
            errors.append(f"No transition from {src[:8]}... to {tgt[:8]}...")

    return len(errors) == 0, errors
