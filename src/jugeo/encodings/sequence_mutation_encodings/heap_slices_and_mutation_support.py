"""Heap slices and mutation support: encoding of heap-allocated memory regions and their mutation
obligations as Z3 SMT fragments.

A heap slice is a bounded region of the heap identified by an allocation site and a support
set. Mutation obligations arise when code writes to a heap slice — they must be discharged
by proving frame conditions or by witnessed runtime invariants.

Invariants:
- Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans
- Trust is an ordered algebra — never a float
- Obstructions are Čech H¹ cohomology classes

# copilot: heap slices and mutation support — Z3 encoding of bounded heap regions and write obligations
"""

from __future__ import annotations

import abc
import collections
import contextlib
import copy
import dataclasses
import enum
import functools
import hashlib
import itertools
import json
import logging
import math
import os
import pathlib
import re
import sys
import time
import uuid
import weakref
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    Generator,
    Iterable,
    Iterator,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

log: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo error imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False

    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"
        ENCODING = "encoding"
        UNKNOWN = "unknown"

    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"
        DESCENT_OBSTRUCTION = "descent_obstruction"
        UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError):  # type: ignore[no-redef]
        pass

    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None:
            self.message = message

    def raise_with_scope(  # type: ignore[misc]
        code: str, *, message: str, provenance: Any = None, **kw: Any
    ) -> None:
        raise JuGeoError(f"[{code}] {message}")


try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"


# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """
    Lattice of trust levels ordered by evidence strength.

    PROPOSAL          — oracle-proposed, not yet reviewed
    REVIEWED          — human-reviewed but not formally verified
    VERIFIED          — statically verified by type checker or SMT solver
    RUNTIME_WITNESSED — witnessed at runtime by a concrete execution
    PROOF_BACKED      — backed by a machine-checked proof
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: TrustTier) -> TrustTier:
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> TrustTier:
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> TrustTier:
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))

    def is_at_least(self, other: TrustTier) -> bool:
        return int(self) >= int(other)

    def is_admissible(self, threshold: TrustTier) -> bool:
        """Return True iff self meets or exceeds threshold (ordered algebra admissibility)."""
        return self.value >= threshold.value


# ---------------------------------------------------------------------------
# Mandatory theory dataclasses (Judgment tuple and CechObstruction)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Encodes the full judgment tuple required by the theory:
      c  = context (typing environment, heap state, path condition)
      φ  = formula (the proposition being judged)
      A  = assumptions (tuple of assumed propositions)
      E  = evidence (tuple of evidence items supporting the judgment)
      O  = obligations (tuple of residual discharge obligations)
      B  = burden (who bears the proof burden: 'solver', 'oracle', 'human')
      T  = trust (TrustTier — the ordered algebra element, never a float)
      Π  = provenance (source of the judgment: function/line/tool)
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology class obstructing global assembly of local heap data.

    When local heap slices on overlapping patches cannot be consistently glued
    into a global section, the obstruction is a non-trivial class in H¹(Cov, F)
    where F is the validity sheaf over the mutation cover Cov.

    Fields:
        cover_id: Identifier of the open cover Cov = {U_i}.
        cocycle: The 1-cocycle — a frozenset of (i,j,delta_ij) transition data
                 on double overlaps U_i ∩ U_j.
        cohomology_class: Human-readable description of the cohomology class,
                          e.g. "[delta_ij] in H¹(Cov, F)".
        description: Plain-English description of what the obstruction means.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff this obstruction is trivial (cocycle is empty = coboundary)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_heap_judgment(
    coordinate: str,
    *,
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    obstructions: tuple[HeapSliceCechObstruction, ...] = (),
    evidence: tuple[str, ...] = (),
) -> HeapSliceJudgment:
    c = coordinate
    phi = f"heap_slice_wff({coordinate})"
    A = (f"agent:heap_slice_encoder@{coordinate}",)
    E = evidence if evidence else (f"evidence:heap_encoding@{c}",)
    O = (f"obligation:slice_consistency@{c}",)
    B = obstructions
    T = trust_tier
    Pi: dict[str, Any] = {"coordinate": coordinate, "encoding": "heap_slice"}
    return HeapSliceJudgment(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)


# ---------------------------------------------------------------------------
# Čech obstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSliceCechObstruction:
    coordinate: str
    cocycle_description: str
    trust_tier: TrustTier
    is_coboundary: bool
    repair_suggestion: str
    obstruction_kind: str
    location: str
    conflicting_values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "trust_tier": int(self.trust_tier),
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
            "obstruction_kind": self.obstruction_kind,
            "location": self.location,
            "conflicting_values": list(self.conflicting_values),
        }

    def canonical_form(self) -> str:
        return f"H1({self.coordinate}, {self.obstruction_kind}, loc={self.location})"

    def is_trivial(self) -> bool:
        return self.is_coboundary


# ---------------------------------------------------------------------------
# Judgment tuple
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSliceJudgment:
    c: str
    phi: str
    A: tuple[str, ...]
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[HeapSliceCechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        return len(self.B) == 0 and self.T.is_at_least(TrustTier.VERIFIED)

    @property
    def is_obstructed(self) -> bool:
        return len(self.B) > 0

    def with_obstruction(self, obs: HeapSliceCechObstruction) -> HeapSliceJudgment:
        return HeapSliceJudgment(
            c=self.c, phi=self.phi, A=self.A, E=self.E, O=self.O,
            B=(*self.B, obs), T=self.T.demote(), Pi=self.Pi,
        )

    def with_evidence(self, evidence: str) -> HeapSliceJudgment:
        return HeapSliceJudgment(
            c=self.c, phi=self.phi, A=self.A, E=(*self.E, evidence),
            O=self.O, B=self.B, T=self.T.promote(), Pi=self.Pi,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c,
            "phi": self.phi,
            "A": list(self.A),
            "E": list(self.E),
            "O": list(self.O),
            "B": [b.to_dict() for b in self.B],
            "T": int(self.T),
            "Pi": dict(self.Pi),
            "is_settled": self.is_settled,
            "is_obstructed": self.is_obstructed,
        }


# ---------------------------------------------------------------------------
# GlobalSection and DescentObstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSliceGlobalSection:
    section_id: str
    coordinate: str
    location_value_pairs: tuple[tuple[str, str], ...]
    total_locations: int
    judgment: HeapSliceJudgment
    constructed_at: str

    def get(self, location: str) -> str | None:
        for loc, val in self.location_value_pairs:
            if loc == location:
                return val
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "coordinate": self.coordinate,
            "location_value_pairs": [{"location": l, "value": v} for l, v in self.location_value_pairs],
            "total_locations": self.total_locations,
            "judgment": self.judgment.to_dict(),
            "constructed_at": self.constructed_at,
        }

    def as_dict(self) -> dict[str, str]:
        return {loc: val for loc, val in self.location_value_pairs}


@dataclass(frozen=True, slots=True)
class HeapSliceDescentObstruction:
    obstruction_id: str
    coordinate: str
    obstructions: tuple[HeapSliceCechObstruction, ...]
    partial_locations_resolved: int
    unresolved_locations: tuple[str, ...]
    judgment: HeapSliceJudgment
    detected_at: str

    @property
    def obstruction_count(self) -> int:
        return len(self.obstructions)

    def primary_obstruction(self) -> HeapSliceCechObstruction | None:
        return self.obstructions[0] if self.obstructions else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "coordinate": self.coordinate,
            "obstructions": [o.to_dict() for o in self.obstructions],
            "partial_locations_resolved": self.partial_locations_resolved,
            "unresolved_locations": list(self.unresolved_locations),
            "judgment": self.judgment.to_dict(),
            "detected_at": self.detected_at,
            "obstruction_count": self.obstruction_count,
        }


# ---------------------------------------------------------------------------
# WriteBarrier
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WriteBarrier:
    barrier_id: str
    guarded_locations: tuple[str, ...]
    write_condition_smt: str
    judgment: HeapSliceJudgment

    def allows_write(self, location: str) -> bool:
        """A barrier allows a write only if the location is NOT guarded (or condition is trivially true)."""
        if location not in self.guarded_locations:
            return True
        # Trivially-true condition: "(= true true)"
        return self.write_condition_smt in ("(= true true)", "true", "True")

    def to_smt_assertion(self) -> str:
        locs = " ".join(f'"{loc}"' for loc in self.guarded_locations)
        return f"(assert (forall ((loc String)) (=> (member loc (set {locs})) {self.write_condition_smt})))"

    def to_dict(self) -> dict[str, Any]:
        return {
            "barrier_id": self.barrier_id,
            "guarded_locations": list(self.guarded_locations),
            "write_condition_smt": self.write_condition_smt,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# SliceConsistencyObligation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SliceConsistencyObligation:
    obligation_id: str
    slice_id: str
    pre_condition: str
    post_condition: str
    judgment: HeapSliceJudgment

    def is_discharged(self) -> bool:
        return self.judgment.is_settled

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "slice_id": self.slice_id,
            "pre_condition": self.pre_condition,
            "post_condition": self.post_condition,
            "judgment": self.judgment.to_dict(),
            "is_discharged": self.is_discharged(),
        }

    def to_smt_pair(self) -> tuple[str, str]:
        return self.pre_condition, self.post_condition


# ---------------------------------------------------------------------------
# MutationTransition
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MutationTransition:
    transition_id: str
    pre_state_id: str
    post_state_id: str
    mutations: tuple[tuple[str, str, str], ...]
    transition_coordinate: str
    judgment: HeapSliceJudgment

    def apply_to(self, value_map: Mapping[str, str]) -> Mapping[str, str]:
        result = dict(value_map)
        for loc, old_val, new_val in self.mutations:
            if loc in result:
                if result[loc] == old_val:
                    result[loc] = new_val
                else:
                    logger.warning(
                        "Mutation at %s: expected old=%r, found=%r",
                        loc,
                        old_val,
                        result[loc],
                    )
                    result[loc] = new_val
            else:
                result[loc] = new_val
        return result

    def is_sound(self) -> bool:
        seen_locations: set[str] = set()
        for loc, _old, _new in self.mutations:
            if loc in seen_locations:
                return False
            seen_locations.add(loc)
        return True

    def to_smt_transition(self) -> str:
        assertions = []
        for loc, old_val, new_val in self.mutations:
            assertions.append(
                f"(assert (=> (= (select heap_pre {loc!r}) {old_val}) "
                f"(= (select heap_post {loc!r}) {new_val})))"
            )
        return "\n".join(assertions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "pre_state_id": self.pre_state_id,
            "post_state_id": self.post_state_id,
            "mutations": [
                {"location": loc, "old": old, "new": new}
                for loc, old, new in self.mutations
            ],
            "transition_coordinate": self.transition_coordinate,
            "judgment": self.judgment.to_dict(),
            "is_sound": self.is_sound(),
        }


# ---------------------------------------------------------------------------
# HeapSlice
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSlice:
    slice_id: str
    base_coordinate: str
    owned_locations: tuple[str, ...]
    value_map: Mapping[str, str]
    write_barriers: tuple[WriteBarrier, ...]
    judgment: HeapSliceJudgment
    is_mutable: bool

    def read(self, location: str) -> str | None:
        return self.value_map.get(location)

    def write(self, location: str, value: str) -> HeapSlice:
        """Returns a new (frozen) HeapSlice with the location updated."""
        if not self.is_mutable:
            logger.warning("Attempting write on immutable slice at %s", location)
        if location not in self.owned_locations:
            logger.warning("Writing to unowned location %s", location)

        # Check barriers
        for barrier in self.write_barriers:
            if not barrier.allows_write(location):
                logger.warning("Write barrier blocks write to %s", location)

        new_map = dict(self.value_map)
        new_map[location] = value
        new_j = self.judgment.with_evidence(f"write:{location}={value}")
        new_id = _stable_id("slice", self.base_coordinate, location, value)
        return HeapSlice(
            slice_id=new_id,
            base_coordinate=self.base_coordinate,
            owned_locations=self.owned_locations,
            value_map=new_map,
            write_barriers=self.write_barriers,
            judgment=new_j,
            is_mutable=self.is_mutable,
        )

    def is_framed(self) -> bool:
        """A slice is framed if all owned locations have values."""
        return all(loc in self.value_map for loc in self.owned_locations)

    def check_write_permissions(self, location: str) -> bool:
        if location not in self.owned_locations:
            return False
        for barrier in self.write_barriers:
            if not barrier.allows_write(location):
                return False
        return self.is_mutable

    def attempt_descent(self) -> HeapSliceGlobalSection | HeapSliceDescentObstruction:
        obstruction_list: list[HeapSliceCechObstruction] = []

        # Check all owned locations have values
        for loc in self.owned_locations:
            if loc not in self.value_map:
                obs = HeapSliceCechObstruction(
                    coordinate=self.base_coordinate,
                    cocycle_description=f"Owned location {loc!r} has no value — Čech 1-cocycle",
                    trust_tier=TrustTier.PROPOSAL,
                    is_coboundary=False,
                    repair_suggestion=f"Assign a value to location {loc!r}",
                    obstruction_kind="missing_value",
                    location=loc,
                    conflicting_values=(),
                )
                obstruction_list.append(obs)

        # Check barrier soundness
        for barrier in self.write_barriers:
            for loc in barrier.guarded_locations:
                if loc not in self.owned_locations:
                    obs = HeapSliceCechObstruction(
                        coordinate=self.base_coordinate,
                        cocycle_description=f"Barrier {barrier.barrier_id} guards unowned location {loc!r}",
                        trust_tier=TrustTier.PROPOSAL,
                        is_coboundary=False,
                        repair_suggestion=f"Remove {loc!r} from barrier or add to owned_locations",
                        obstruction_kind="barrier_scope_error",
                        location=loc,
                        conflicting_values=(),
                    )
                    obstruction_list.append(obs)

        if obstruction_list:
            bad_j = _make_heap_judgment(
                self.base_coordinate,
                trust_tier=TrustTier.PROPOSAL,
                obstructions=tuple(obstruction_list),
            )
            return HeapSliceDescentObstruction(
                obstruction_id=_stable_id("obs", self.slice_id),
                coordinate=self.base_coordinate,
                obstructions=tuple(obstruction_list),
                partial_locations_resolved=len(self.owned_locations) - len(obstruction_list),
                unresolved_locations=tuple(o.location for o in obstruction_list),
                judgment=bad_j,
                detected_at=_now_iso(),
            )

        pairs = tuple(
            (loc, self.value_map[loc])
            for loc in self.owned_locations
            if loc in self.value_map
        )
        good_j = _make_heap_judgment(
            self.base_coordinate,
            trust_tier=TrustTier.VERIFIED,
            evidence=tuple(f"loc:{loc}" for loc in self.owned_locations),
        )
        return HeapSliceGlobalSection(
            section_id=_stable_id("gs", self.slice_id),
            coordinate=self.base_coordinate,
            location_value_pairs=pairs,
            total_locations=len(pairs),
            judgment=good_j,
            constructed_at=_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "base_coordinate": self.base_coordinate,
            "owned_locations": list(self.owned_locations),
            "value_map": dict(self.value_map),
            "write_barriers": [b.to_dict() for b in self.write_barriers],
            "judgment": self.judgment.to_dict(),
            "is_mutable": self.is_mutable,
            "is_framed": self.is_framed(),
        }


# ---------------------------------------------------------------------------
# create_heap_slice
# ---------------------------------------------------------------------------

def create_heap_slice(
    owned_locations: list[str],
    value_map: Mapping[str, str],
    *,
    coordinate: str,
    is_mutable: bool = True,
) -> HeapSlice:
    """Construct a HeapSlice for the given owned locations and initial values."""
    j = _make_heap_judgment(coordinate, trust_tier=TrustTier.REVIEWED)
    slice_id = _stable_id("slice", coordinate, *owned_locations)
    return HeapSlice(
        slice_id=slice_id,
        base_coordinate=coordinate,
        owned_locations=tuple(owned_locations),
        value_map=dict(value_map),
        write_barriers=(),
        judgment=j,
        is_mutable=is_mutable,
    )


# ---------------------------------------------------------------------------
# apply_mutation
# ---------------------------------------------------------------------------

def apply_mutation(
    heap_slice: HeapSlice,
    location: str,
    new_value: str,
    *,
    coordinate: str,
) -> tuple[HeapSlice, MutationTransition, SliceConsistencyObligation]:
    """Apply a single mutation to a heap slice. Returns new slice, transition, and obligation."""
    old_value = heap_slice.read(location) or "undefined"

    # Build new slice
    new_slice = heap_slice.write(location, new_value)

    # Build transition
    j_trans = _make_heap_judgment(coordinate, trust_tier=TrustTier.REVIEWED)
    transition = MutationTransition(
        transition_id=_stable_id("trans", coordinate, location, new_value),
        pre_state_id=heap_slice.slice_id,
        post_state_id=new_slice.slice_id,
        mutations=((location, old_value, new_value),),
        transition_coordinate=coordinate,
        judgment=j_trans,
    )

    # Build consistency obligation
    pre_cond = f"(= (select heap {location!r}) {old_value!r})"
    post_cond = f"(= (select heap_post {location!r}) {new_value!r})"
    j_obl = _make_heap_judgment(coordinate, trust_tier=TrustTier.PROPOSAL)
    obligation = SliceConsistencyObligation(
        obligation_id=_stable_id("obl", coordinate, location),
        slice_id=new_slice.slice_id,
        pre_condition=pre_cond,
        post_condition=post_cond,
        judgment=j_obl,
    )

    return new_slice, transition, obligation


# ---------------------------------------------------------------------------
# check_slice_consistency
# ---------------------------------------------------------------------------

def check_slice_consistency(
    slice_: HeapSlice,
    mutation: MutationTransition,
    *,
    runtime_check: bool = False,
) -> HeapSliceGlobalSection | HeapSliceDescentObstruction:
    """Check that a mutation transition is consistent with a heap slice."""
    obstruction_list: list[HeapSliceCechObstruction] = []

    # Verify mutation is sound (no duplicate locations)
    if not mutation.is_sound():
        obs = HeapSliceCechObstruction(
            coordinate=mutation.transition_coordinate,
            cocycle_description="Mutation transition writes the same location twice",
            trust_tier=TrustTier.PROPOSAL,
            is_coboundary=False,
            repair_suggestion="Deduplicate mutation targets",
            obstruction_kind="duplicate_mutation",
            location="<multiple>",
            conflicting_values=(),
        )
        obstruction_list.append(obs)

    # Verify pre-state values match
    for loc, old_val, _new_val in mutation.mutations:
        actual = slice_.read(loc)
        if actual is not None and actual != old_val and not runtime_check:
            obs = HeapSliceCechObstruction(
                coordinate=mutation.transition_coordinate,
                cocycle_description=(
                    f"Pre-state mismatch at {loc!r}: expected {old_val!r}, found {actual!r}"
                ),
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion=f"Update pre-condition for location {loc!r}",
                obstruction_kind="pre_state_mismatch",
                location=loc,
                conflicting_values=(old_val, actual),
            )
            obstruction_list.append(obs)

    if obstruction_list:
        bad_j = _make_heap_judgment(
            mutation.transition_coordinate,
            obstructions=tuple(obstruction_list),
        )
        return HeapSliceDescentObstruction(
            obstruction_id=_stable_id("obs_cons", slice_.slice_id, mutation.transition_id),
            coordinate=mutation.transition_coordinate,
            obstructions=tuple(obstruction_list),
            partial_locations_resolved=len(mutation.mutations) - len(obstruction_list),
            unresolved_locations=tuple(o.location for o in obstruction_list),
            judgment=bad_j,
            detected_at=_now_iso(),
        )

    # Apply transition and build global section
    new_map = mutation.apply_to(slice_.value_map)
    all_owned = slice_.owned_locations
    pairs = tuple((loc, new_map.get(loc, "undefined")) for loc in all_owned)
    good_j = _make_heap_judgment(
        mutation.transition_coordinate,
        trust_tier=TrustTier.RUNTIME_WITNESSED if runtime_check else TrustTier.VERIFIED,
        evidence=tuple(f"mutation:{loc}" for loc, _, _ in mutation.mutations),
    )
    return HeapSliceGlobalSection(
        section_id=_stable_id("gs_cons", slice_.slice_id, mutation.transition_id),
        coordinate=mutation.transition_coordinate,
        location_value_pairs=pairs,
        total_locations=len(pairs),
        judgment=good_j,
        constructed_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# HeapSliceStats
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSliceStats:
    stats_id: str
    total_owned_locations: int
    total_valued_locations: int
    total_barriers: int
    is_framed: bool
    is_mutable: bool
    computed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats_id": self.stats_id,
            "total_owned_locations": self.total_owned_locations,
            "total_valued_locations": self.total_valued_locations,
            "total_barriers": self.total_barriers,
            "is_framed": self.is_framed,
            "is_mutable": self.is_mutable,
            "computed_at": self.computed_at,
        }


def compute_slice_stats(slice_: HeapSlice) -> HeapSliceStats:
    valued = sum(1 for loc in slice_.owned_locations if loc in slice_.value_map)
    return HeapSliceStats(
        stats_id=_stable_id("stats", slice_.slice_id),
        total_owned_locations=len(slice_.owned_locations),
        total_valued_locations=valued,
        total_barriers=len(slice_.write_barriers),
        is_framed=slice_.is_framed(),
        is_mutable=slice_.is_mutable,
        computed_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "TrustTier",
    "TrustLevel",
    "HeapSliceCechObstruction",
    "HeapSliceJudgment",
    "HeapSliceGlobalSection",
    "HeapSliceDescentObstruction",
    "WriteBarrier",
    "SliceConsistencyObligation",
    "MutationTransition",
    "HeapSlice",
    "HeapSliceStats",
    "create_heap_slice",
    "apply_mutation",
    "check_slice_consistency",
    "compute_slice_stats",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Create a heap slice
    locs = ["x", "y", "z"]
    vals = {"x": "0", "y": "1", "z": "2"}
    hs = create_heap_slice(locs, vals, coordinate="test:heap", is_mutable=True)
    assert hs.is_framed(), "Slice should be framed"
    assert hs.read("x") == "0"
    assert hs.read("y") == "1"

    # Write returns a new slice
    hs2 = hs.write("x", "42")
    assert hs2.read("x") == "42"
    assert hs.read("x") == "0"  # original unchanged (frozen)

    # attempt_descent on framed slice
    result = hs.attempt_descent()
    assert isinstance(result, HeapSliceGlobalSection), f"Got {type(result)}"
    assert result.get("x") == "0"

    # Create slice with missing value
    hs_partial = create_heap_slice(["a", "b"], {"a": "1"}, coordinate="test:partial")
    obs_result = hs_partial.attempt_descent()
    assert isinstance(obs_result, HeapSliceDescentObstruction), f"Got {type(obs_result)}"
    assert obs_result.obstruction_count >= 1

    # apply_mutation
    new_hs, trans, obl = apply_mutation(hs, "x", "99", coordinate="test:mut")
    assert new_hs.read("x") == "99"
    assert trans.is_sound()
    assert not obl.is_discharged()

    # check_slice_consistency — sound
    cons_result = check_slice_consistency(hs, trans, runtime_check=False)
    assert isinstance(cons_result, HeapSliceGlobalSection), f"Got {type(cons_result)}"

    # WriteBarrier
    j = _make_heap_judgment("test:barrier")
    barrier = WriteBarrier(
        barrier_id="b1",
        guarded_locations=("secret_loc",),
        write_condition_smt="false",
        judgment=j,
    )
    assert not barrier.allows_write("secret_loc")
    assert barrier.allows_write("other_loc")

    # Stats
    stats = compute_slice_stats(hs)
    assert stats.total_owned_locations == 3
    assert stats.is_framed
    assert stats.is_mutable

    # TrustTier
    assert TrustTier.PROPOSAL.join(TrustTier.VERIFIED) == TrustTier.VERIFIED
    assert TrustTier.PROOF_BACKED.promote() == TrustTier.PROOF_BACKED
    assert TrustTier.PROPOSAL.demote() == TrustTier.PROPOSAL

    print("heap_slices_and_mutation_support: OK")
    sys.exit(0)


# ---------------------------------------------------------------------------
# CechObstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CechObstruction:
    """
    A Čech H¹ cohomology class witnessing descent failure.

    In the heap context, a CechObstruction arises when two heap slices covering
    an overlapping address range have inconsistent cells at the overlap. The
    obstruction is a non-trivial 1-cocycle in the Čech complex of the cover.

    Fields
    ------
    cover_id         : identifier of the covering used to detect the obstruction
    cocycle          : frozenset of (addr, cell_a, cell_b) conflict triples
    cohomology_class : string label for the Čech cohomology class
    description      : human-readable description of the failure
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the cocycle is empty (no conflicts → trivial class)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _new_id(prefix: str = "") -> str:
    """Generate a fresh UUID-based identifier with optional prefix."""
    uid = str(uuid.uuid4()).replace("-", "")
    return f"{prefix}{uid}" if prefix else uid


def _hash_cells(cells: dict) -> str:
    """
    Compute a stable SHA-256 hash of a cells dict {int addr → (type_repr, value_repr)}.

    Cells are sorted by address before hashing to ensure stability.
    """
    sorted_cells = sorted((str(addr), list(cell)) for addr, cell in cells.items())
    raw = json.dumps(sorted_cells, ensure_ascii=True)
    return hashlib.new(_HASH_ALGORITHM, raw.encode()).hexdigest()


def _validate_addr_range(addr_start: int, addr_end: int) -> None:
    """Raise JuGeoError if the address range is invalid."""
    if addr_start < 0:
        raise JuGeoError(f"addr_start must be ≥ 0, got {addr_start}")
    if addr_end <= addr_start:
        raise JuGeoError(
            f"addr_end ({addr_end}) must be > addr_start ({addr_start})"
        )
    if addr_end - addr_start > _MAX_ADDR_RANGE:
        raise JuGeoError(
            f"Address range too large: {addr_end - addr_start} > {_MAX_ADDR_RANGE}"
        )


# ---------------------------------------------------------------------------
# HeapSlice
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeapSlice:
    """
    A section of the heap sheaf H restricted to [addr_start, addr_end).

    A HeapSlice records the type and value of each cell in a contiguous address
    range. It is immutable: mutations produce new HeapSlice objects.

    Sheaf interpretation
    --------------------
    * BaseSpace = {addr_start, addr_start+1, ..., addr_end-1}  (discrete)
    * Stalk at addr = (TypeRepr × ValueRepr)  (the cell type and value)
    * Section σ = cells dict
    * Addresses in [addr_start, addr_end) not in cells have stalk = (_DEFAULT_TYPE_REPR, _DEFAULT_VALUE_REPR)

    Fields
    ------
    slice_id    : unique identifier for this slice
    addr_start  : inclusive lower bound of the address range
    addr_end    : exclusive upper bound of the address range
    cells       : dict mapping int address → (type_repr: str, value_repr: str)
    trust       : TrustTier of this slice's content
    provenance  : origin/lineage of this slice
    """

    slice_id: str
    addr_start: int
    addr_end: int
    cells: dict   # int → (str, str)
    trust: TrustTier
    provenance: Any

    def size(self) -> int:
        """Return the number of addresses in the range (addr_end - addr_start)."""
        return self.addr_end - self.addr_start

    def cell_at(self, addr: int) -> Optional[tuple]:
        """
        Return the (type_repr, value_repr) cell at address addr, or None.

        Addresses outside [addr_start, addr_end) always return None.
        Addresses in range but not in cells return the default cell.
        """
        if not (self.addr_start <= addr < self.addr_end):
            return None
        return self.cells.get(addr, (_DEFAULT_TYPE_REPR, _DEFAULT_VALUE_REPR))

    def has_addr(self, addr: int) -> bool:
        """Return True iff addr is within the slice's address range."""
        return self.addr_start <= addr < self.addr_end

    def type_at(self, addr: int) -> Optional[str]:
        """Return the type_repr of the cell at addr, or None if out of range."""
        cell = self.cell_at(addr)
        return cell[0] if cell is not None else None

    def to_section(self) -> dict:
        """
        Return the full section as a JSON-serialisable dict.

        All addresses in [addr_start, addr_end) are included; addresses not in
        cells get the default cell.
        """
        result: Dict[str, Any] = {
            "slice_id": self.slice_id,
            "addr_start": self.addr_start,
            "addr_end": self.addr_end,
            "cells": {
                str(addr): list(self.cells.get(addr, (_DEFAULT_TYPE_REPR, _DEFAULT_VALUE_REPR)))
                for addr in range(self.addr_start, self.addr_end)
            },
        }
        return result

    def encoding_hash(self) -> str:
        """
        Compute a content-addressed hash of this slice.

        The hash covers (addr_start, addr_end, cells) so that slices with
        identical content but different slice_ids hash equally.
        """
        payload = {
            "addr_start": self.addr_start,
            "addr_end": self.addr_end,
        }
        base = json.dumps(payload, sort_keys=True).encode()
        cell_hash = _hash_cells(self.cells)
        combined = base + cell_hash.encode()
        return hashlib.new(_HASH_ALGORITHM, combined).hexdigest()


# ---------------------------------------------------------------------------
# MutationTransition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationTransition:
    """
    A sheaf morphism representing a single-cell mutation on a HeapSlice.

    A MutationTransition records the before-slice, after-slice, the address
    mutated, the old and new cells, and the timestamp of the mutation.

    Sheaf interpretation
    --------------------
    The transition is the endomorphism
        τ : H([s, e)) → H([s, e))
    that is the identity everywhere except at mutation_addr, where it sends
    old_cell to new_cell. The before/after HeapSlice objects witness this.

    Fields
    ------
    transition_id  : unique identifier
    slice_before   : the HeapSlice before the mutation
    slice_after    : the HeapSlice after the mutation
    mutation_addr  : the address that was mutated
    old_cell       : the (type_repr, value_repr) before (None if fresh alloc)
    new_cell       : the (type_repr, value_repr) after
    timestamp      : Unix timestamp of the mutation
    trust          : TrustTier of this transition record
    """

    transition_id: str
    slice_before: HeapSlice
    slice_after: HeapSlice
    mutation_addr: int
    old_cell: Optional[tuple]
    new_cell: tuple
    timestamp: float
    trust: TrustTier

    def is_type_preserving(self) -> bool:
        """
        Return True iff the mutation preserves the cell's type.

        A type-preserving mutation only changes the value, not the type. This is
        a weaker constraint than full barrier satisfaction.
        """
        if self.old_cell is None:
            return False
        return self.old_cell[0] == self.new_cell[0]

    def delta(self) -> dict:
        """
        Return a dict summarising the change made by this transition.

        Keys: addr, old_type, old_value, new_type, new_value, type_preserving.
        """
        old_type = self.old_cell[0] if self.old_cell else None
        old_value = self.old_cell[1] if self.old_cell else None
        return {
            "addr": self.mutation_addr,
            "old_type": old_type,
            "old_value": old_value,
            "new_type": self.new_cell[0],
            "new_value": self.new_cell[1],
            "type_preserving": self.is_type_preserving(),
        }

    def to_judgment(self) -> Judgment:
        """
        Lift this transition to a Judgment asserting it is a valid sheaf morphism.

        The formula asserts that τ(mutation_addr, new_cell) is a valid transition
        from slice_before to slice_after.
        """
        d = self.delta()
        formula = (
            f"ValidTransition(addr={d['addr']}, "
            f"old=({d['old_type']!r},{d['old_value']!r}), "
            f"new=({d['new_type']!r},{d['new_value']!r}), "
            f"slice={self.slice_before.slice_id!r})"
        )
        return Judgment(
            context={
                "transition_id": self.transition_id,
                "slice_id": self.slice_before.slice_id,
            },
            formula=formula,
            assumptions=(),
            evidence=({"transition_id": self.transition_id, "timestamp": self.timestamp},),
            obligations=(),
            burden=None,
            trust=self.trust,
            provenance={"transition_id": self.transition_id},
        )

    def to_sheaf_morphism_repr(self) -> str:
        """
        Return a compact string representation of this transition as a sheaf morphism.

        Format:
          τ(addr=0x{addr:08x}, ({old_type},{old_val}) → ({new_type},{new_val}))
        """
        ot = self.old_cell[0] if self.old_cell else "∅"
        ov = self.old_cell[1] if self.old_cell else "∅"
        return (
            f"τ(addr=0x{self.mutation_addr:08x},"
            f" ({ot},{ov}) → ({self.new_cell[0]},{self.new_cell[1]}))"
        )


# ---------------------------------------------------------------------------
# WriteBarrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteBarrier:
    """
    A semantic write barrier for a HeapSlice address range.

    A WriteBarrier specifies constraints that must hold for any mutation within
    its addr_range to be considered valid. It is the sheaf-level analogue of a
    precondition in Hoare logic.

    The barrier consists of three independent constraints:
    1. type_constraint: a string formula that the new_type must satisfy
       (e.g. "int", "ptr", "int|float", "NOT null")
    2. bounds_constraint: a string formula on the new_value
       (e.g. "0 ≤ v ≤ 255", "v ≠ 0", "len(v) < 64")
    3. alias_constraint: a constraint on aliasing
       (e.g. "UNIQUE", "NO_OVERLAP", "DISJOINT_FROM(region_id)")

    Fields
    ------
    barrier_id         : unique identifier
    addr_range         : (start, end) — the address range this barrier guards
    type_constraint    : string formula for the type
    bounds_constraint  : string formula for the value bounds
    alias_constraint   : string formula for aliasing
    is_active          : False → barrier is disabled (for testing/debugging)
    """

    barrier_id: str
    addr_range: tuple   # (int, int)
    type_constraint: str
    bounds_constraint: str
    alias_constraint: str
    is_active: bool

    def check(
        self, addr: int, new_type: str, new_value: str, slice_: HeapSlice
    ) -> bool:
        """
        Check if the proposed mutation satisfies this barrier.

        Parameters
        ----------
        addr      : the address being mutated
        new_type  : the proposed new type_repr
        new_value : the proposed new value_repr
        slice_    : the current HeapSlice state (used for alias checking)

        Returns
        -------
        True iff:
        * barrier is not active, OR
        * addr is outside this barrier's addr_range, OR
        * all three constraints are "satisfied" (simplified heuristic check)

        Notes
        -----
        The actual constraint evaluation is a simplified string-matching
        heuristic. In a production system, an SMT solver would be used.
        """
        if not self.is_active:
            return True
        s, e = self.addr_range
        if not (s <= addr < e):
            return True  # barrier does not apply to this address

        # Type constraint: check if new_type matches (heuristic)
        if self.type_constraint not in ("any", "ANY", "", "unknown"):
            allowed_types = {t.strip() for t in self.type_constraint.split("|")}
            if "NOT " in self.type_constraint:
                forbidden = self.type_constraint.replace("NOT ", "").strip()
                if new_type == forbidden:
                    return False
            elif new_type not in allowed_types:
                return False

        # Bounds constraint: check via simple numeric heuristic
        if self.bounds_constraint and self.bounds_constraint not in ("any", "ANY", ""):
            if "≥ 0" in self.bounds_constraint or ">= 0" in self.bounds_constraint:
                try:
                    if float(new_value) < 0:
                        return False
                except (ValueError, TypeError):
                    pass

        # Alias constraint: simplified check (no real aliasing analysis)
        if self.alias_constraint in ("UNIQUE",):
            # Check that no other address in the slice has the same value
            for other_addr, cell in slice_.cells.items():
                if other_addr != addr and cell[1] == new_value:
                    return False

        return True

    def violation_message(self, addr: int, new_type: str, new_value: str) -> str:
        """
        Produce a human-readable violation message for a failed check.

        This message is intended to be used by the countermodel extractor to
        classify the violation.
        """
        return (
            f"{_BARRIER_VIOLATION_PREFIX}: barrier={self.barrier_id!r} "
            f"addr=0x{addr:08x} "
            f"new_type={new_type!r} "
            f"new_value={new_value!r} "
            f"type_constraint={self.type_constraint!r} "
            f"bounds_constraint={self.bounds_constraint!r} "
            f"alias_constraint={self.alias_constraint!r}"
        )

    def as_formula(self) -> str:
        """
        Render this barrier as an SMT-style formula string.

        Format:
          WriteBarrier(range=[s,e), type=..., bounds=..., alias=...)
        """
        s, e = self.addr_range
        return (
            f"WriteBarrier(range=[{s},{e}), "
            f"type={self.type_constraint!r}, "
            f"bounds={self.bounds_constraint!r}, "
            f"alias={self.alias_constraint!r})"
        )


# ---------------------------------------------------------------------------
# SliceConsistencyObligation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SliceConsistencyObligation:
    """
    A proof obligation asserting that a HeapSlice satisfies all its WriteBarriers.

    This is the heap-slice analogue of a Čech descent condition: the section
    (cells dict) is globally consistent iff every local barrier is satisfied at
    every address in its range.

    Fields
    ------
    obligation_id : unique identifier
    slice_        : the HeapSlice to be checked
    barriers      : tuple of WriteBarriers that must be satisfied
    formula       : string formula summarising the obligation
    trust         : TrustTier
    """

    obligation_id: str
    slice_: HeapSlice
    barriers: tuple   # of WriteBarrier
    formula: str
    trust: TrustTier

    def is_satisfied(self, checker_result: list) -> bool:
        """
        Return True iff checker_result is an empty list (no violations).

        Parameters
        ----------
        checker_result : list of violation message strings returned by
                         check_slice_consistency()
        """
        return len(checker_result) == 0

    def to_judgment(self) -> Judgment:
        """
        Lift this obligation to a Judgment.

        The formula asserts that slice_ satisfies all barriers. The obligations
        tuple contains the formula string (pending discharge).
        """
        return Judgment(
            context={
                "obligation_id": self.obligation_id,
                "slice_id": self.slice_.slice_id,
                "barrier_count": len(self.barriers),
            },
            formula=self.formula,
            assumptions=(),
            evidence=(),
            obligations=(self.formula,),
            burden="verifier",
            trust=self.trust,
            provenance={
                "obligation_id": self.obligation_id,
                "slice_hash": self.slice_.encoding_hash(),
            },
        )

    def discharge_with(self, evidence: tuple) -> Judgment:
        """
        Discharge this obligation with provided evidence, upgrading trust.

        Parameters
        ----------
        evidence : tuple of evidence items (e.g. checker results, proofs)

        Returns
        -------
        A new Judgment with trust = RUNTIME_WITNESSED and no pending obligations.
        """
        return Judgment(
            context={
                "obligation_id": self.obligation_id,
                "slice_id": self.slice_.slice_id,
            },
            formula=self.formula,
            assumptions=(),
            evidence=evidence,
            obligations=(),
            burden=None,
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"discharged_at": time.time()},
        )


# ---------------------------------------------------------------------------
# HeapSliceEncoder (stateful)
# ---------------------------------------------------------------------------


class HeapSliceEncoder:
    """
    Stateful encoder for heap slice mutations with write-barrier enforcement.

    HeapSliceEncoder maintains a mutable view of a HeapSlice and tracks all
    mutations as MutationTransitions. It applies WriteBarriers before each
    mutation and collects violations.

    This is the "operational" counterpart to the immutable HeapSlice: it
    provides a mutable interface while recording all changes as sheaf morphisms.

    Attributes
    ----------
    current_slice  : the current HeapSlice (updated after each apply_mutation)
    mutation_log   : list of MutationTransition objects, in order
    barriers       : list of WriteBarrier objects active on this encoder

    Parameters
    ----------
    slice_id    : identifier for the initial HeapSlice
    addr_start  : inclusive lower bound of the address range
    addr_end    : exclusive upper bound of the address range
    """

    def __init__(self, slice_id: str, addr_start: int, addr_end: int) -> None:
        _validate_addr_range(addr_start, addr_end)
        self.current_slice: HeapSlice = HeapSlice(
            slice_id=slice_id,
            addr_start=addr_start,
            addr_end=addr_end,
            cells={},
            trust=TrustTier.PROPOSAL,
            provenance={"created_at": time.time(), "encoder": "HeapSliceEncoder"},
        )
        self.mutation_log: List[MutationTransition] = []
        self.barriers: List[WriteBarrier] = []
        _LOGGER.debug(
            "HeapSliceEncoder: init slice_id=%s range=[%d,%d)",
            slice_id,
            addr_start,
            addr_end,
        )

    def add_write_barrier(self, barrier: WriteBarrier) -> None:
        """
        Register a WriteBarrier on this encoder.

        Parameters
        ----------
        barrier : the WriteBarrier to add

        The barrier will be checked on every subsequent apply_mutation call.
        """
        self.barriers.append(barrier)
        _LOGGER.debug("add_write_barrier: added %s", barrier.barrier_id)

    def apply_mutation(
        self, addr: int, new_type: str, new_value: str
    ) -> MutationTransition:
        """
        Apply a mutation at addr with new (type, value), checking all barriers.

        Parameters
        ----------
        addr      : the address to mutate
        new_type  : the new type_repr
        new_value : the new value_repr

        Returns
        -------
        MutationTransition recording the before/after state.

        Raises
        ------
        JuGeoError if addr is outside the slice's range.

        Notes
        -----
        Barrier violations are logged as warnings but do NOT raise exceptions
        (to allow the caller to collect violations and build a countermodel).
        The mutation is still applied so that subsequent mutations can be tested.
        """
        if not self.current_slice.has_addr(addr):
            raise JuGeoError(
                f"apply_mutation: addr 0x{addr:08x} outside range "
                f"[{self.current_slice.addr_start}, {self.current_slice.addr_end})"
            )

        old_cell = self.current_slice.cells.get(addr)
        violations = []
        for barrier in self.barriers:
            if not barrier.check(addr, new_type, new_value, self.current_slice):
                msg = barrier.violation_message(addr, new_type, new_value)
                violations.append(msg)
                _LOGGER.warning("apply_mutation: %s", msg)

        new_cells = dict(self.current_slice.cells)
        new_cells[addr] = (new_type, new_value)

        new_slice = HeapSlice(
            slice_id=self.current_slice.slice_id,
            addr_start=self.current_slice.addr_start,
            addr_end=self.current_slice.addr_end,
            cells=new_cells,
            trust=(
                TrustTier.RUNTIME_WITNESSED if not violations else TrustTier.PROPOSAL
            ),
            provenance={
                "mutated_at": time.time(),
                "addr": addr,
                "new_type": new_type,
                "new_value": new_value,
                "violations": violations,
            },
        )

        transition = MutationTransition(
            transition_id=_new_id("trans_"),
            slice_before=self.current_slice,
            slice_after=new_slice,
            mutation_addr=addr,
            old_cell=old_cell,
            new_cell=(new_type, new_value),
            timestamp=time.time(),
            trust=TrustTier.RUNTIME_WITNESSED if not violations else TrustTier.PROPOSAL,
        )

        self.current_slice = new_slice
        if len(self.mutation_log) < _MAX_MUTATION_LOG_SIZE:
            self.mutation_log.append(transition)
        else:
            _LOGGER.warning(
                "apply_mutation: mutation_log full (%d entries); dropping oldest",
                _MAX_MUTATION_LOG_SIZE,
            )
            self.mutation_log.pop(0)
            self.mutation_log.append(transition)

        return transition

    def check_consistency(self) -> list:
        """
        Check the current slice against all registered barriers.

        Returns
        -------
        list of violation message strings (empty → all barriers satisfied)
        """
        return check_slice_consistency(self.current_slice, self.barriers)

    def get_current_slice(self) -> HeapSlice:
        """Return the current HeapSlice."""
        return self.current_slice

    def mutation_history(self) -> list:
        """Return the full mutation log as a list of MutationTransitions."""
        return list(self.mutation_log)

    def encode_current(self) -> SliceConsistencyObligation:
        """
        Build a SliceConsistencyObligation for the current slice and all barriers.

        Returns
        -------
        SliceConsistencyObligation capturing the current state and barriers.
        """
        barrier_formulas = " ∧ ".join(b.as_formula() for b in self.barriers)
        formula = (
            f"Consistent(slice={self.current_slice.slice_id!r}, barriers=[{barrier_formulas}])"
        )
        return SliceConsistencyObligation(
            obligation_id=_new_id("oblig_"),
            slice_=self.current_slice,
            barriers=tuple(self.barriers),
            formula=formula,
            trust=TrustTier.PROPOSAL,
        )


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def create_heap_slice(
    addr_start: int,
    addr_end: int,
    initial_cells: Optional[dict] = None,
    slice_id: str = "",
) -> HeapSlice:
    """
    Create a HeapSlice over [addr_start, addr_end) with optional initial cells.

    Parameters
    ----------
    addr_start    : inclusive lower bound
    addr_end      : exclusive upper bound
    initial_cells : dict {int addr → (type_repr, value_repr)}; default = empty
    slice_id      : optional identifier; auto-generated if empty

    Returns
    -------
    HeapSlice with the given range and cells.

    Raises
    ------
    JuGeoError if addr_start or addr_end are invalid, or if any key in
    initial_cells is outside the range.
    """
    _validate_addr_range(addr_start, addr_end)
    sid = slice_id or _new_id("slice_")
    cells: Dict[int, tuple] = {}

    if initial_cells:
        for addr, cell in initial_cells.items():
            if not (addr_start <= addr < addr_end):
                raise JuGeoError(
                    f"create_heap_slice: initial_cells addr {addr} outside range "
                    f"[{addr_start}, {addr_end})"
                )
            if not (isinstance(cell, (tuple, list)) and len(cell) == 2):
                raise JuGeoError(
                    f"create_heap_slice: cell at addr {addr} must be (type_repr, value_repr)"
                )
            cells[int(addr)] = (str(cell[0]), str(cell[1]))

    return HeapSlice(
        slice_id=sid,
        addr_start=addr_start,
        addr_end=addr_end,
        cells=cells,
        trust=TrustTier.PROPOSAL,
        provenance={"created_at": time.time(), "initial_cell_count": len(cells)},
    )


def apply_mutation(
    slice_: HeapSlice,
    addr: int,
    new_type: str,
    new_value: str,
    trust: TrustTier = TrustTier.PROPOSAL,
) -> tuple:
    """
    Apply a single mutation to a HeapSlice, returning the new slice and transition.

    This is the functional (pure) version of HeapSliceEncoder.apply_mutation.
    No barriers are checked; the caller is responsible for validation.

    Parameters
    ----------
    slice_    : the HeapSlice to mutate
    addr      : the address to mutate
    new_type  : the new type_repr
    new_value : the new value_repr
    trust     : TrustTier for the new slice and transition

    Returns
    -------
    (new_HeapSlice, MutationTransition)

    Raises
    ------
    JuGeoError if addr is outside slice_.addr_range.
    """
    if not slice_.has_addr(addr):
        raise JuGeoError(
            f"apply_mutation: addr {addr} outside range "
            f"[{slice_.addr_start}, {slice_.addr_end})"
        )
    old_cell = slice_.cells.get(addr)
    new_cells = dict(slice_.cells)
    new_cells[addr] = (new_type, new_value)

    new_slice = HeapSlice(
        slice_id=slice_.slice_id,
        addr_start=slice_.addr_start,
        addr_end=slice_.addr_end,
        cells=new_cells,
        trust=trust,
        provenance={
            "mutated_at": time.time(),
            "addr": addr,
            "new_type": new_type,
            "new_value": new_value,
            "parent_hash": slice_.encoding_hash(),
        },
    )

    transition = MutationTransition(
        transition_id=_new_id("trans_"),
        slice_before=slice_,
        slice_after=new_slice,
        mutation_addr=addr,
        old_cell=old_cell,
        new_cell=(new_type, new_value),
        timestamp=time.time(),
        trust=trust,
    )
    return new_slice, transition


def check_slice_consistency(
    slice_: HeapSlice, barriers: list
) -> list:
    """
    Check a HeapSlice against a list of WriteBarriers.

    For each address in the slice's cells, check all barriers that cover that
    address. Collect all violation messages.

    Parameters
    ----------
    slice_   : the HeapSlice to check
    barriers : list of WriteBarrier objects

    Returns
    -------
    list of violation message strings; empty list means the slice is consistent
    with all barriers.

    Notes
    -----
    Only addresses that appear in slice_.cells are checked. Default cells
    (those not in cells) are assumed to satisfy all barriers.
    """
    violations: List[str] = []
    for addr, cell in slice_.cells.items():
        new_type, new_value = cell
        for barrier in barriers:
            if not barrier.check(addr, new_type, new_value, slice_):
                violations.append(barrier.violation_message(addr, new_type, new_value))
    return violations


def encode_mutation_transition(transition: MutationTransition) -> dict:
    """
    Encode a MutationTransition as a JSON-serialisable sheaf morphism dict.

    The returned dict captures all information about the transition in a form
    suitable for logging, persistence, or transmission to an oracle.

    Parameters
    ----------
    transition : the MutationTransition to encode

    Returns
    -------
    dict with keys:
      sentinel, transition_id, mutation_addr, old_cell, new_cell,
      type_preserving, timestamp, trust,
      slice_before_hash, slice_after_hash,
      sheaf_morphism_repr, delta
    """
    return {
        "sentinel": _TRANSITION_SENTINEL,
        "transition_id": transition.transition_id,
        "mutation_addr": transition.mutation_addr,
        "mutation_addr_hex": f"0x{transition.mutation_addr:08x}",
        "old_cell": list(transition.old_cell) if transition.old_cell else None,
        "new_cell": list(transition.new_cell),
        "type_preserving": transition.is_type_preserving(),
        "timestamp": transition.timestamp,
        "trust": transition.trust.name,
        "slice_before_id": transition.slice_before.slice_id,
        "slice_after_id": transition.slice_after.slice_id,
        "slice_before_hash": transition.slice_before.encoding_hash(),
        "slice_after_hash": transition.slice_after.encoding_hash(),
        "sheaf_morphism_repr": transition.to_sheaf_morphism_repr(),
        "delta": transition.delta(),
    }


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "HeapSlice",
    "HeapSliceCechObstruction",
    "HeapSliceEncoder",
    "HeapSliceGlobalSection",
    "HeapSliceJudgment",
    "MutationTransition",
    "SliceConsistencyObligation",
    "TrustTier",
    "WriteBarrier",
    "apply_mutation",
    "check_slice_consistency",
    "create_heap_slice",
    "encode_mutation_transition",
]


# ---------------------------------------------------------------------------
# __main__ smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger(__name__)

    print("=" * 70)
    print(f"heap_slices_and_mutation_support.py  v{_MODULE_VERSION}")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Create a heap slice
    # -----------------------------------------------------------------------
    initial = {
        0x1000: ("int", "42"),
        0x1001: ("ptr", "0xdeadbeef"),
        0x1002: ("float", "3.14"),
        0x1003: ("str", "hello"),
    }
    s = create_heap_slice(0x1000, 0x1010, initial_cells=initial, slice_id="slice_main")
    print(f"\n[1] HeapSlice slice_id={s.slice_id}")
    print(f"    size()            = {s.size()}")
    print(f"    cell_at(0x1000)   = {s.cell_at(0x1000)}")
    print(f"    type_at(0x1001)   = {s.type_at(0x1001)}")
    print(f"    has_addr(0x1005)  = {s.has_addr(0x1005)}")
    print(f"    encoding_hash     = {s.encoding_hash()[:32]}...")
    assert s.size() == 16
    assert s.cell_at(0x1000) == ("int", "42")
    assert s.type_at(0x1001) == "ptr"
    assert not s.has_addr(0x2000)

    # -----------------------------------------------------------------------
    # 2. Functional apply_mutation
    # -----------------------------------------------------------------------
    s2, t1 = apply_mutation(s, 0x1000, "int", "99", TrustTier.RUNTIME_WITNESSED)
    print(f"\n[2] After apply_mutation(0x1000, 'int', '99')")
    print(f"    new cell_at(0x1000)    = {s2.cell_at(0x1000)}")
    print(f"    is_type_preserving     = {t1.is_type_preserving()}")
    print(f"    transition delta       = {t1.delta()}")
    print(f"    sheaf_morphism_repr    = {t1.to_sheaf_morphism_repr()}")
    assert s2.cell_at(0x1000) == ("int", "99")
    assert t1.is_type_preserving()

    # -----------------------------------------------------------------------
    # 3. Non-type-preserving mutation
    # -----------------------------------------------------------------------
    s3, t2 = apply_mutation(s2, 0x1001, "str", "now_a_string", TrustTier.PROPOSAL)
    print(f"\n[3] Non-type-preserving mutation")
    print(f"    old type = ptr, new type = str")
    print(f"    is_type_preserving = {t2.is_type_preserving()}")
    assert not t2.is_type_preserving()

    # -----------------------------------------------------------------------
    # 4. WriteBarrier construction and checking
    # -----------------------------------------------------------------------
    barrier_type = WriteBarrier(
        barrier_id="type_barrier",
        addr_range=(0x1000, 0x1010),
        type_constraint="int|float",
        bounds_constraint="",
        alias_constraint="",
        is_active=True,
    )
    print(f"\n[4] WriteBarrier")
    print(f"    as_formula: {barrier_type.as_formula()}")
    result_ok = barrier_type.check(0x1000, "int", "42", s)
    result_bad = barrier_type.check(0x1001, "ptr", "0xdeadbeef", s)
    print(f"    check(int,  42) = {result_ok}   (expected True)")
    print(f"    check(ptr, ...) = {result_bad}  (expected False)")
    assert result_ok
    assert not result_bad
    print(f"    violation_msg: {barrier_type.violation_message(0x1001, 'ptr', '0xdeadbeef')}")

    # -----------------------------------------------------------------------
    # 5. check_slice_consistency
    # -----------------------------------------------------------------------
    violations = check_slice_consistency(s, [barrier_type])
    print(f"\n[5] check_slice_consistency on original slice (has ptr and str)")
    print(f"    violations: {len(violations)}")
    for v in violations:
        print(f"      - {v[:80]}...")
    assert len(violations) > 0

    # -----------------------------------------------------------------------
    # 6. HeapSliceEncoder
    # -----------------------------------------------------------------------
    enc = HeapSliceEncoder("enc_slice", 0x2000, 0x2010)
    bounds_barrier = WriteBarrier(
        barrier_id="bounds_barrier",
        addr_range=(0x2000, 0x2010),
        type_constraint="int",
        bounds_constraint=">= 0",
        alias_constraint="",
        is_active=True,
    )
    enc.add_write_barrier(bounds_barrier)

    tr1 = enc.apply_mutation(0x2000, "int", "10")
    tr2 = enc.apply_mutation(0x2001, "int", "20")
    tr3 = enc.apply_mutation(0x2002, "float", "3.14")  # type violation
    print(f"\n[6] HeapSliceEncoder")
    print(f"    current_slice cells: {dict(enc.get_current_slice().cells)}")
    print(f"    mutation_log length: {len(enc.mutation_history())}")
    hist = enc.mutation_history()
    assert len(hist) == 3

    # -----------------------------------------------------------------------
    # 7. SliceConsistencyObligation
    # -----------------------------------------------------------------------
    oblig = enc.encode_current()
    violations2 = enc.check_consistency()
    print(f"\n[7] SliceConsistencyObligation")
    print(f"    formula: {oblig.formula[:80]}...")
    print(f"    is_satisfied: {oblig.is_satisfied(violations2)}")
    j = oblig.to_judgment()
    print(f"    judgment.trust: {j.trust.name}")
    discharged = oblig.discharge_with(evidence=({"checker": "runtime", "pass": True},))
    print(f"    discharged.trust: {discharged.trust.name}")
    assert discharged.trust == TrustTier.RUNTIME_WITNESSED

    # -----------------------------------------------------------------------
    # 8. encode_mutation_transition
    # -----------------------------------------------------------------------
    encoded = encode_mutation_transition(tr1)
    print(f"\n[8] encode_mutation_transition")
    print(f"    sentinel: {encoded['sentinel']}")
    print(f"    transition_id: {encoded['transition_id'][:16]}...")
    print(f"    sheaf_morphism_repr: {encoded['sheaf_morphism_repr']}")
    assert encoded["sentinel"] == _TRANSITION_SENTINEL

    # -----------------------------------------------------------------------
    # 9. to_section
    # -----------------------------------------------------------------------
    section = s.to_section()
    print(f"\n[9] to_section: {len(section['cells'])} cells")
    assert len(section["cells"]) == s.size()

    # -----------------------------------------------------------------------
    # 10. TrustTier
    # -----------------------------------------------------------------------
    ta = TrustTier.VERIFIED
    tb = TrustTier.PROOF_BACKED
    print(f"\n[10] TrustTier ops: {ta.name}.join({tb.name}) = {ta.join(tb).name}")
    assert ta.join(tb) == TrustTier.PROOF_BACKED

    print("\n✓ All smoke tests passed.")
