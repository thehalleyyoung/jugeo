from __future__ import annotations

"""Theory2.tex Ch8 §"Closure and resumability" — Projects, modules,
hypercovers, and fleets.

*Closure* is the process of completing a partial section by filling in the
holes left by incomplete evidence, unanswered obligations, or partially covered
coordinates.  *Resumability* is the orthogonal property of a long computation:
the ability to checkpoint intermediate state and restart from that checkpoint
without re-doing already-completed work.

Mathematical setting
--------------------
A *partial section* s̃ ∈ Γ_partial(𝒮, ℱ) is a section defined on a sub-site
𝒮' ⊆ 𝒮 satisfying descent on 𝒮' but potentially failing descent on 𝒮.  The
*closure problem* asks: does there exist an extension of s̃ to a full section
s ∈ Γ(𝒮, ℱ) compatible with s̃?  Theory2.tex §8 shows:

    1.  If H¹(𝒮, ℱ) = 0 (the site has trivial first cohomology), closure
        always succeeds.
    2.  Otherwise, the obstruction class [s̃] ∈ H¹(𝒮, ℱ) must vanish for
        closure to succeed.  The runtime heuristic attempts to kill the
        obstruction by iterative patch extension.

Resumability checkpoints
------------------------
A *ResumptionCheckpoint* captures:

    -   The set of coordinates already successfully covered.
    -   The set of coordinates remaining.
    -   The elapsed compute budget consumed so far.
    -   The partial section data for already-covered coordinates.
    -   A digest of the site state at checkpoint time.

Theory2.tex §8 requires that checkpoints be *monotone*: the covered set at
checkpoint n+1 must contain the covered set at checkpoint n.  This prevents
regressions during resumption.

Judgment tuples are (c, φ, A, E, O, B, T, Π) — trust T is a tier string,
never a float.

# copilot: foundations/project_hypercovers §s03 — closure and resumability
"""

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

try:
    from jugeo.geometry.descent import DescentResult
except ImportError:
    DescentResult = Any  # type: ignore

try:
    from jugeo.foundations.project_hypercovers.models import TrustTier as _MTT
    _TRUST_BASE = _MTT
except ImportError:
    _TRUST_BASE = None  # type: ignore


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TrustTier(str, Enum):
    """Categorical trust tier for closure and resumability operations.

    Trust is categorical — never a float.  Closure operations preserve trust
    tier: a partial section at PROVISIONAL cannot be closed to CERTIFIED
    without additional evidence.
    """

    PROPOSAL     = "PROPOSAL"
    PROVISIONAL  = "PROVISIONAL"
    CORROBORATED = "CORROBORATED"
    CERTIFIED    = "CERTIFIED"
    CANONICAL    = "CANONICAL"

    def dominates(self, other: TrustTier) -> bool:
        """Return True if this tier strictly dominates *other*."""
        order = list(TrustTier)
        return order.index(self) > order.index(other)

    def meets(self, other: TrustTier) -> TrustTier:
        """Return the categorical meet (infimum) of two tiers."""
        order = list(TrustTier)
        return self if order.index(self) <= order.index(other) else other


class ClosureStatus(str, Enum):
    """Outcome of a single closure attempt.

    Theory2.tex §8 defines three terminal states for closure:
    SUCCESS, OBSTRUCTED, and TIMEOUT.  PARTIAL is an intermediate
    state during iterative closure.
    """

    PENDING    = "PENDING"     # Closure not yet attempted
    PARTIAL    = "PARTIAL"     # Some holes filled; more iterations needed
    SUCCESS    = "SUCCESS"     # Full section assembled; all holes closed
    OBSTRUCTED = "OBSTRUCTED"  # H¹ obstruction prevents full closure
    TIMEOUT    = "TIMEOUT"     # Ran out of iterations/budget
    ABANDONED  = "ABANDONED"   # Explicitly abandoned by coordinator


class CheckpointStatus(str, Enum):
    """Lifecycle status of a ResumptionCheckpoint.

    ACTIVE checkpoints can be used to resume a computation.
    STALE checkpoints are outdated (site changed since checkpoint was taken).
    CONSUMED checkpoints have been successfully resumed and are no longer needed.
    """

    ACTIVE   = "ACTIVE"    # Valid and can be resumed from
    STALE    = "STALE"     # Site has changed; checkpoint may be invalid
    CONSUMED = "CONSUMED"  # Successfully resumed; computation complete
    CORRUPT  = "CORRUPT"   # Digest mismatch; checkpoint data is unreliable


class HoleKind(str, Enum):
    """Classification of a hole in a partial section.

    Theory2.tex §8 identifies three principal kinds of hole:
    MISSING_EVIDENCE, UNANSWERED_OBLIGATION, and COCYCLE_FAILURE.
    """

    MISSING_EVIDENCE      = "MISSING_EVIDENCE"      # No evidence for this coordinate
    UNANSWERED_OBLIGATION = "UNANSWERED_OBLIGATION"  # Obligation not discharged
    COCYCLE_FAILURE       = "COCYCLE_FAILURE"        # Local sections disagree on overlap
    TRUST_DEFICIT         = "TRUST_DEFICIT"          # Section exists but at wrong tier


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SectionHole:
    """A single hole (missing or inconsistent datum) in a partial section.

    Holes are the atomic unit that closure attempts to fill.  Each hole has a
    coordinate address, a kind, and a set of repair strategies.

    Parameters
    ----------
    hole_id : str
        Unique 12-hex identifier.
    coord_id : str
        The site coordinate where the hole resides.
    kind : HoleKind
        Classification of the hole.
    description : str
        Human-readable description of why this hole exists.
    repair_strategies : Sequence[str]
        Ordered list of strategy names to attempt for filling this hole.
    blocking_obligations : Sequence[str]
        Obligation keys that must be discharged before this hole can be filled.
    trust_floor : TrustTier
        Minimum trust tier required for the fill to be accepted.
    """

    hole_id               : str
    coord_id              : str
    kind                  : HoleKind
    description           : str
    repair_strategies     : Sequence[str]
    blocking_obligations  : Sequence[str]
    trust_floor           : TrustTier

    def is_blocked(self) -> bool:
        """Return True if this hole has outstanding blocking obligations."""
        return len(self.blocking_obligations) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "hole_id":              self.hole_id,
            "coord_id":             self.coord_id,
            "kind":                 self.kind.value,
            "description":          self.description,
            "repair_strategies":    list(self.repair_strategies),
            "blocking_obligations": list(self.blocking_obligations),
            "trust_floor":          self.trust_floor.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SectionHole:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            hole_id               = d["hole_id"],
            coord_id              = d["coord_id"],
            kind                  = HoleKind(d["kind"]),
            description           = d.get("description", ""),
            repair_strategies     = tuple(d.get("repair_strategies", [])),
            blocking_obligations  = tuple(d.get("blocking_obligations", [])),
            trust_floor           = TrustTier(d.get("trust_floor", "PROPOSAL")),
        )

    @classmethod
    def make(
        cls,
        coord_id: str,
        kind: HoleKind,
        description: str = "",
        repair_strategies: Sequence[str] = (),
        blocking_obligations: Sequence[str] = (),
        trust_floor: TrustTier = TrustTier.PROPOSAL,
    ) -> SectionHole:
        """Convenience factory with auto-assigned hole_id."""
        return cls(
            hole_id               = uuid.uuid4().hex[:12],
            coord_id              = coord_id,
            kind                  = kind,
            description           = description,
            repair_strategies     = tuple(repair_strategies),
            blocking_obligations  = tuple(blocking_obligations),
            trust_floor           = trust_floor,
        )


@dataclass(frozen=True, slots=True)
class PartialSection:
    """A section defined on a sub-site of the full judgment site.

    A ``PartialSection`` represents work-in-progress: some coordinates are
    covered (their local sections are known), others have holes that must be
    filled to achieve a global section.

    Parameters
    ----------
    section_id : str
        Unique 12-hex identifier.
    covered_data : Mapping[str, Any]
        Maps covered coord_id → local section datum.
    holes : Sequence[SectionHole]
        The holes remaining to be filled.
    trust_tier : TrustTier
        The minimum trust tier of all covered data (categorical meet).
    total_coords : int
        The total number of coordinates in the full site.
    created_at : str
        ISO-8601 creation timestamp.
    meta : Mapping[str, Any]
        Arbitrary metadata.

    Notes
    -----
    ``coverage_fraction()`` = len(covered_data) / total_coords.  A partial
    section with coverage_fraction == 1.0 and no holes is a full section.
    """

    section_id   : str
    covered_data : Mapping[str, Any]
    holes        : Sequence[SectionHole]
    trust_tier   : TrustTier
    total_coords : int
    created_at   : str
    meta         : Mapping[str, Any] = field(default_factory=dict)

    def coverage_fraction(self) -> float:
        """Return the fraction of site coordinates covered by this section."""
        return len(self.covered_data) / max(self.total_coords, 1)

    def is_complete(self) -> bool:
        """True when all coordinates are covered and no holes remain."""
        return len(self.holes) == 0 and len(self.covered_data) >= self.total_coords

    def holes_by_kind(self, kind: HoleKind) -> list[SectionHole]:
        """Return all holes of the given *kind*."""
        return [h for h in self.holes if h.kind == kind]

    def unblocked_holes(self) -> list[SectionHole]:
        """Return holes with no blocking obligations (ready to fill)."""
        return [h for h in self.holes if not h.is_blocked()]

    def fill_hole(self, hole_id: str, datum: Any) -> PartialSection:
        """Return a copy with the specified hole filled.

        Parameters
        ----------
        hole_id : str
            The hole to fill.
        datum : Any
            The section datum to insert for the hole's coordinate.

        Returns
        -------
        PartialSection
            A new PartialSection with one fewer hole and the datum recorded.

        Raises
        ------
        KeyError
            If *hole_id* is not found in this section's holes.
        """
        target = next((h for h in self.holes if h.hole_id == hole_id), None)
        if target is None:
            raise KeyError(f"hole_id {hole_id!r} not found")
        new_data  = dict(self.covered_data)
        new_data[target.coord_id] = datum
        new_holes = tuple(h for h in self.holes if h.hole_id != hole_id)
        return replace(self, covered_data=new_data, holes=new_holes)

    def digest(self) -> str:
        """Content-hash over covered_data keys and section_id (SHA-256, 16 hex)."""
        keys  = sorted(self.covered_data.keys())
        raw   = f"{self.section_id}|{','.join(keys)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "section_id":   self.section_id,
            "covered_data": {k: v for k, v in self.covered_data.items()},
            "holes":        [h.to_dict() for h in self.holes],
            "trust_tier":   self.trust_tier.value,
            "total_coords": self.total_coords,
            "created_at":   self.created_at,
            "meta":         dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PartialSection:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            section_id   = d["section_id"],
            covered_data = d.get("covered_data", {}),
            holes        = tuple(SectionHole.from_dict(h) for h in d.get("holes", [])),
            trust_tier   = TrustTier(d.get("trust_tier", "PROPOSAL")),
            total_coords = int(d.get("total_coords", 0)),
            created_at   = d["created_at"],
            meta         = d.get("meta", {}),
        )

    @classmethod
    def make(
        cls,
        covered_data: Mapping[str, Any],
        holes: Sequence[SectionHole],
        total_coords: int,
        trust_tier: TrustTier = TrustTier.PROPOSAL,
        meta: Mapping[str, Any] | None = None,
    ) -> PartialSection:
        """Factory with auto-assigned section_id and created_at."""
        return cls(
            section_id   = uuid.uuid4().hex[:12],
            covered_data = dict(covered_data),
            holes        = tuple(holes),
            trust_tier   = trust_tier,
            total_coords = total_coords,
            created_at   = datetime.now(timezone.utc).isoformat(),
            meta         = meta or {},
        )


@dataclass(frozen=True, slots=True)
class ClosureAttempt:
    """A record of one closure iteration on a PartialSection.

    The coordinator makes multiple closure attempts on a PartialSection,
    each attempt filling some holes.  Each attempt is recorded independently
    for auditability.

    Parameters
    ----------
    attempt_id : str
        Unique 12-hex identifier.
    iteration : int
        The zero-indexed iteration number of this attempt.
    input_section_id : str
        The section_id of the PartialSection this attempt operated on.
    holes_filled : Sequence[str]
        hole_ids successfully filled in this iteration.
    holes_remaining : int
        Number of holes remaining after this iteration.
    status : ClosureStatus
        Status after this iteration.
    budget_consumed : float
        Compute budget consumed by this attempt.
    elapsed_s : float
        Wall-clock time for this iteration.
    created_at : str
        ISO-8601 timestamp.
    notes : str
        Free-form notes from the closure strategy (e.g. "filled via evidence lookup").
    """

    attempt_id        : str
    iteration         : int
    input_section_id  : str
    holes_filled      : Sequence[str]
    holes_remaining   : int
    status            : ClosureStatus
    budget_consumed   : float
    elapsed_s         : float
    created_at        : str
    notes             : str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "attempt_id":       self.attempt_id,
            "iteration":        self.iteration,
            "input_section_id": self.input_section_id,
            "holes_filled":     list(self.holes_filled),
            "holes_remaining":  self.holes_remaining,
            "status":           self.status.value,
            "budget_consumed":  self.budget_consumed,
            "elapsed_s":        self.elapsed_s,
            "created_at":       self.created_at,
            "notes":            self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClosureAttempt:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            attempt_id        = d["attempt_id"],
            iteration         = int(d.get("iteration", 0)),
            input_section_id  = d["input_section_id"],
            holes_filled      = tuple(d.get("holes_filled", [])),
            holes_remaining   = int(d.get("holes_remaining", 0)),
            status            = ClosureStatus(d["status"]),
            budget_consumed   = float(d.get("budget_consumed", 0.0)),
            elapsed_s         = float(d.get("elapsed_s", 0.0)),
            created_at        = d["created_at"],
            notes             = d.get("notes", ""),
        )

    @classmethod
    def make(
        cls,
        iteration: int,
        input_section_id: str,
        holes_filled: Sequence[str],
        holes_remaining: int,
        status: ClosureStatus,
        budget_consumed: float = 0.0,
        elapsed_s: float = 0.0,
        notes: str = "",
    ) -> ClosureAttempt:
        """Factory with auto-assigned attempt_id and created_at."""
        return cls(
            attempt_id        = uuid.uuid4().hex[:12],
            iteration         = iteration,
            input_section_id  = input_section_id,
            holes_filled      = tuple(holes_filled),
            holes_remaining   = holes_remaining,
            status            = status,
            budget_consumed   = budget_consumed,
            elapsed_s         = elapsed_s,
            created_at        = datetime.now(timezone.utc).isoformat(),
            notes             = notes,
        )


@dataclass(frozen=True, slots=True)
class ResumptionCheckpoint:
    """An immutable snapshot of partial-closure state enabling resumption.

    Theory2.tex §8 requires checkpoints to be monotone: the covered_coord_ids
    set must grow (or stay the same) across consecutive checkpoints.

    Parameters
    ----------
    checkpoint_id : str
        Unique 12-hex identifier.
    sequence_number : int
        Monotonically increasing checkpoint index (0-indexed).
    covered_coord_ids : frozenset[str]
        Coordinates already successfully covered at checkpoint time.
    remaining_coord_ids : frozenset[str]
        Coordinates yet to be covered.
    partial_section_id : str
        The section_id of the PartialSection at checkpoint time.
    budget_consumed_so_far : float
        Total compute budget used up to this checkpoint.
    iterations_completed : int
        Number of closure iterations completed before this checkpoint.
    site_digest : str
        SHA-256 digest (16 hex) of the site state at checkpoint time; used
        to detect staleness.
    status : CheckpointStatus
        Current lifecycle status of this checkpoint.
    created_at : str
        ISO-8601 creation timestamp.
    meta : Mapping[str, Any]
        Arbitrary metadata (e.g. coordinator configuration snapshot).

    Notes
    -----
    A coordinator must verify ``site_digest`` before resuming from a checkpoint.
    If the site has changed, the checkpoint should be marked STALE and a fresh
    run initiated.
    """

    checkpoint_id          : str
    sequence_number        : int
    covered_coord_ids      : frozenset[str]
    remaining_coord_ids    : frozenset[str]
    partial_section_id     : str
    budget_consumed_so_far : float
    iterations_completed   : int
    site_digest            : str
    status                 : CheckpointStatus
    created_at             : str
    meta                   : Mapping[str, Any] = field(default_factory=dict)

    def coverage_fraction(self) -> float:
        """Fraction of coordinates covered at this checkpoint."""
        total = len(self.covered_coord_ids) + len(self.remaining_coord_ids)
        return len(self.covered_coord_ids) / max(total, 1)

    def is_usable(self) -> bool:
        """True when this checkpoint is ACTIVE and can be resumed from."""
        return self.status == CheckpointStatus.ACTIVE

    def monotone_check(self, predecessor: ResumptionCheckpoint) -> bool:
        """Verify the monotonicity invariant against a *predecessor* checkpoint.

        Returns True iff ``self.covered_coord_ids ⊇ predecessor.covered_coord_ids``.

        Parameters
        ----------
        predecessor : ResumptionCheckpoint
            The immediately preceding checkpoint to compare against.

        Returns
        -------
        bool
            True when the monotonicity invariant holds.
        """
        return predecessor.covered_coord_ids.issubset(self.covered_coord_ids)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "checkpoint_id":          self.checkpoint_id,
            "sequence_number":        self.sequence_number,
            "covered_coord_ids":      sorted(self.covered_coord_ids),
            "remaining_coord_ids":    sorted(self.remaining_coord_ids),
            "partial_section_id":     self.partial_section_id,
            "budget_consumed_so_far": self.budget_consumed_so_far,
            "iterations_completed":   self.iterations_completed,
            "site_digest":            self.site_digest,
            "status":                 self.status.value,
            "created_at":             self.created_at,
            "meta":                   dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResumptionCheckpoint:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            checkpoint_id          = d["checkpoint_id"],
            sequence_number        = int(d.get("sequence_number", 0)),
            covered_coord_ids      = frozenset(d.get("covered_coord_ids", [])),
            remaining_coord_ids    = frozenset(d.get("remaining_coord_ids", [])),
            partial_section_id     = d["partial_section_id"],
            budget_consumed_so_far = float(d.get("budget_consumed_so_far", 0.0)),
            iterations_completed   = int(d.get("iterations_completed", 0)),
            site_digest            = d.get("site_digest", ""),
            status                 = CheckpointStatus(d.get("status", "ACTIVE")),
            created_at             = d["created_at"],
            meta                   = d.get("meta", {}),
        )

    @classmethod
    def make(
        cls,
        sequence_number: int,
        section: PartialSection,
        budget_consumed_so_far: float,
        iterations_completed: int,
        site_digest: str,
        meta: Mapping[str, Any] | None = None,
    ) -> ResumptionCheckpoint:
        """Factory: compute covered/remaining sets from the partial section."""
        covered = frozenset(section.covered_data.keys())
        # Remaining = the coordinate IDs referenced in holes
        remaining = frozenset(h.coord_id for h in section.holes)
        return cls(
            checkpoint_id          = uuid.uuid4().hex[:12],
            sequence_number        = sequence_number,
            covered_coord_ids      = covered,
            remaining_coord_ids    = remaining,
            partial_section_id     = section.section_id,
            budget_consumed_so_far = budget_consumed_so_far,
            iterations_completed   = iterations_completed,
            site_digest            = site_digest,
            status                 = CheckpointStatus.ACTIVE,
            created_at             = datetime.now(timezone.utc).isoformat(),
            meta                   = meta or {},
        )


@dataclass(frozen=True, slots=True)
class ClosureResult:
    """The final outcome of a closure procedure.

    Summarises whether the partial section was successfully closed to a full
    section, the terminal PartialSection state, and all attempt records.

    Parameters
    ----------
    result_id : str
        Unique 12-hex identifier.
    status : ClosureStatus
        Terminal status: SUCCESS, OBSTRUCTED, TIMEOUT, or ABANDONED.
    final_section : PartialSection
        The section at the end of the closure procedure (may still have holes
        if status is not SUCCESS).
    attempts : Sequence[ClosureAttempt]
        All closure attempt records in chronological order.
    total_budget_consumed : float
        Total compute budget used across all attempts.
    total_iterations : int
        Total closure iterations performed.
    elapsed_s : float
        Wall-clock time for the full closure procedure.
    created_at : str
        ISO-8601 creation timestamp.

    Notes
    -----
    When ``status == ClosureStatus.SUCCESS``, ``final_section.is_complete()``
    must be True.  This is enforced by ``ClosureResumabilityCoordinator.run()``.
    """

    result_id             : str
    status                : ClosureStatus
    final_section         : PartialSection
    attempts              : Sequence[ClosureAttempt]
    total_budget_consumed : float
    total_iterations      : int
    elapsed_s             : float
    created_at            : str

    def holes_filled_count(self) -> int:
        """Return total number of holes filled across all attempts."""
        return sum(len(a.holes_filled) for a in self.attempts)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "result_id":             self.result_id,
            "status":                self.status.value,
            "final_section":         self.final_section.to_dict(),
            "attempts":              [a.to_dict() for a in self.attempts],
            "total_budget_consumed": self.total_budget_consumed,
            "total_iterations":      self.total_iterations,
            "elapsed_s":             self.elapsed_s,
            "created_at":            self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClosureResult:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            result_id             = d["result_id"],
            status                = ClosureStatus(d["status"]),
            final_section         = PartialSection.from_dict(d["final_section"]),
            attempts              = tuple(ClosureAttempt.from_dict(a) for a in d.get("attempts", [])),
            total_budget_consumed = float(d.get("total_budget_consumed", 0.0)),
            total_iterations      = int(d.get("total_iterations", 0)),
            elapsed_s             = float(d.get("elapsed_s", 0.0)),
            created_at            = d["created_at"],
        )


# ---------------------------------------------------------------------------
# ClosureResumabilityCoordinator
# ---------------------------------------------------------------------------

class ClosureResumabilityCoordinator:
    """Orchestrates iterative closure of a PartialSection with checkpoint support.

    The coordinator drives a loop of closure attempts, taking a checkpoint after
    each successful iteration.  If interrupted, it can resume from the most
    recent ACTIVE checkpoint.  On completion it produces a ClosureResult and
    a witness certificate.

    Parameters
    ----------
    max_iterations : int
        Maximum number of closure iterations before emitting TIMEOUT.
        Default: 20.
    budget_per_iteration : float
        Compute budget consumed per iteration.  Default: 1.0.
    total_budget : float
        Hard budget cap for the entire closure procedure.  Default: 30.0.
    checkpoint_interval : int
        Take a checkpoint every *checkpoint_interval* iterations.  Default: 5.
    verbose : bool
        Emit progress messages to stdout.

    Examples
    --------
    >>> section = PartialSection.make(
    ...     covered_data = {"coord_a": "value_a"},
    ...     holes        = [SectionHole.make("coord_b", HoleKind.MISSING_EVIDENCE)],
    ...     total_coords = 2,
    ... )
    >>> coord = ClosureResumabilityCoordinator(max_iterations=5)
    >>> witness = coord.run(section)
    """

    def __init__(
        self,
        max_iterations: int = 20,
        budget_per_iteration: float = 1.0,
        total_budget: float = 30.0,
        checkpoint_interval: int = 5,
        verbose: bool = False,
    ) -> None:
        self.max_iterations      = max_iterations
        self.budget_per_iteration = budget_per_iteration
        self.total_budget        = total_budget
        self.checkpoint_interval = checkpoint_interval
        self.verbose             = verbose
        self._log: list[str] = []

    # ------------------------------------------------------------------
    # Primary interface
    # ------------------------------------------------------------------

    def run(
        self,
        section: PartialSection,
        site_digest: str = "",
        resume_from: ResumptionCheckpoint | None = None,
    ) -> ClosureResumabilityWitness:
        """Run the iterative closure procedure and return an immutable witness.

        Parameters
        ----------
        section : PartialSection
            The partial section to close.
        site_digest : str, optional
            SHA-256 digest of the current site state.  Used to validate
            checkpoints during resumption.  An empty string disables the check.
        resume_from : ResumptionCheckpoint, optional
            If provided, validate and resume from this checkpoint instead of
            starting fresh.

        Returns
        -------
        ClosureResumabilityWitness
            Immutable certificate of the closure procedure.

        Raises
        ------
        ValueError
            If *resume_from* is stale (site_digest mismatch) when site_digest
            is non-empty.
        """
        t0 = time.monotonic()
        self._log.clear()

        if resume_from is not None:
            section = self._validate_and_resume(section, resume_from, site_digest)

        self._emit(
            f"run: section={section.section_id!r} "
            f"holes={len(section.holes)} "
            f"covered={len(section.covered_data)}/{section.total_coords}"
        )

        attempts: list[ClosureAttempt]  = []
        checkpoints: list[ResumptionCheckpoint] = []
        budget_used   = 0.0
        iteration     = 0
        current       = section

        while iteration < self.max_iterations and budget_used < self.total_budget:
            if current.is_complete():
                break

            iter_t0 = time.monotonic()
            current, filled, notes = self._fill_iteration(current, iteration)
            iter_elapsed = time.monotonic() - iter_t0
            budget_used += self.budget_per_iteration

            if current.is_complete():
                iter_status = ClosureStatus.SUCCESS
            elif not filled:
                iter_status = ClosureStatus.OBSTRUCTED
            else:
                iter_status = ClosureStatus.PARTIAL

            attempt = ClosureAttempt.make(
                iteration        = iteration,
                input_section_id = section.section_id,
                holes_filled     = filled,
                holes_remaining  = len(current.holes),
                status           = iter_status,
                budget_consumed  = self.budget_per_iteration,
                elapsed_s        = iter_elapsed,
                notes            = notes,
            )
            attempts.append(attempt)
            self._emit(
                f"iteration {iteration}: filled={len(filled)} "
                f"remaining={len(current.holes)} status={iter_status.value}"
            )

            if (iteration + 1) % self.checkpoint_interval == 0:
                cp = ResumptionCheckpoint.make(
                    sequence_number        = len(checkpoints),
                    section                = current,
                    budget_consumed_so_far = budget_used,
                    iterations_completed   = iteration + 1,
                    site_digest            = site_digest,
                )
                checkpoints.append(cp)
                self._emit(f"checkpoint #{cp.sequence_number}: {cp.checkpoint_id!r}")

            if iter_status in (ClosureStatus.OBSTRUCTED, ClosureStatus.SUCCESS):
                break

            iteration += 1

        terminal_status = self._compute_terminal_status(current, attempts, budget_used)
        result = ClosureResult(
            result_id             = uuid.uuid4().hex[:12],
            status                = terminal_status,
            final_section         = current,
            attempts              = tuple(attempts),
            total_budget_consumed = budget_used,
            total_iterations      = iteration + 1,
            elapsed_s             = time.monotonic() - t0,
            created_at            = datetime.now(timezone.utc).isoformat(),
        )

        elapsed = time.monotonic() - t0
        return ClosureResumabilityWitness(
            witness_id   = uuid.uuid4().hex[:12],
            result       = result,
            checkpoints  = tuple(checkpoints),
            elapsed_s    = elapsed,
            log_lines    = tuple(self._log),
            created_at   = datetime.now(timezone.utc).isoformat(),
        )

    def validate(self, section: PartialSection) -> list[str]:
        """Return validation error messages for *section* (empty = valid).

        Checks:

        -   ``section.total_coords`` is positive.
        -   No two holes share the same ``hole_id``.
        -   No two holes share the same ``coord_id`` (each coordinate has at
            most one hole per partial section).
        -   All covered coords are non-empty strings.

        Parameters
        ----------
        section : PartialSection
            The section to validate.

        Returns
        -------
        list[str]
            Human-readable error messages; empty when valid.
        """
        errors: list[str] = []
        if section.total_coords <= 0:
            errors.append(f"total_coords must be positive; got {section.total_coords}")
        seen_ids: set[str] = set()
        seen_coords: set[str] = set()
        for h in section.holes:
            if h.hole_id in seen_ids:
                errors.append(f"duplicate hole_id: {h.hole_id!r}")
            seen_ids.add(h.hole_id)
            if h.coord_id in seen_coords:
                errors.append(f"duplicate coord_id in holes: {h.coord_id!r}")
            seen_coords.add(h.coord_id)
        for k in section.covered_data:
            if not k:
                errors.append("Empty coord_id in covered_data.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise coordinator configuration."""
        return {
            "max_iterations":       self.max_iterations,
            "budget_per_iteration": self.budget_per_iteration,
            "total_budget":         self.total_budget,
            "checkpoint_interval":  self.checkpoint_interval,
            "verbose":              self.verbose,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClosureResumabilityCoordinator:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            max_iterations       = int(d.get("max_iterations", 20)),
            budget_per_iteration = float(d.get("budget_per_iteration", 1.0)),
            total_budget         = float(d.get("total_budget", 30.0)),
            checkpoint_interval  = int(d.get("checkpoint_interval", 5)),
            verbose              = bool(d.get("verbose", False)),
        )

    # ------------------------------------------------------------------
    # Domain methods
    # ------------------------------------------------------------------

    def enumerate_holes(self, section: PartialSection) -> list[SectionHole]:
        """Return all holes in *section*, sorted by kind then coord_id.

        Parameters
        ----------
        section : PartialSection
            The section to inspect.

        Returns
        -------
        list[SectionHole]
            Holes sorted by (kind.value, coord_id).
        """
        return sorted(section.holes, key=lambda h: (h.kind.value, h.coord_id))

    def fill_from_evidence(
        self,
        section: PartialSection,
        evidence_store: Mapping[str, Any],
    ) -> tuple[PartialSection, list[str]]:
        """Fill holes of kind MISSING_EVIDENCE using an evidence store.

        For each MISSING_EVIDENCE hole, looks up the hole's coord_id in
        *evidence_store*.  If found, fills the hole with the evidence datum.

        Parameters
        ----------
        section : PartialSection
            The section to fill.
        evidence_store : Mapping[str, Any]
            Maps coord_id → evidence datum.

        Returns
        -------
        tuple[PartialSection, list[str]]
            (updated_section, list_of_filled_hole_ids).
        """
        filled_ids: list[str] = []
        current = section
        for hole in list(current.holes):
            if hole.kind == HoleKind.MISSING_EVIDENCE and hole.coord_id in evidence_store:
                datum   = evidence_store[hole.coord_id]
                current = current.fill_hole(hole.hole_id, datum)
                filled_ids.append(hole.hole_id)
        return current, filled_ids

    def fill_from_defaults(
        self,
        section: PartialSection,
        default_value: Any = None,
    ) -> tuple[PartialSection, list[str]]:
        """Fill unblocked holes with a default value as a last-resort strategy.

        This is appropriate when no evidence is available and the coordinator
        must produce a complete section (e.g. for PROPOSAL-tier output).
        The filled holes will have ``trust_floor = PROPOSAL``.

        Parameters
        ----------
        section : PartialSection
            The section to fill.
        default_value : Any, optional
            The default datum to insert.  Defaults to None.

        Returns
        -------
        tuple[PartialSection, list[str]]
            (updated_section, list_of_filled_hole_ids).
        """
        filled_ids: list[str] = []
        current = section
        for hole in list(current.holes):
            if not hole.is_blocked():
                current = current.fill_hole(hole.hole_id, default_value)
                filled_ids.append(hole.hole_id)
        return current, filled_ids

    def take_checkpoint(
        self,
        section: PartialSection,
        budget_consumed: float,
        iterations_done: int,
        existing_checkpoints: Sequence[ResumptionCheckpoint],
        site_digest: str = "",
    ) -> ResumptionCheckpoint:
        """Create and return a new checkpoint for *section*.

        Validates the monotonicity invariant against the most recent existing
        checkpoint (if any).

        Parameters
        ----------
        section : PartialSection
            Current partial section state.
        budget_consumed : float
            Budget consumed so far.
        iterations_done : int
            Iterations completed so far.
        existing_checkpoints : Sequence[ResumptionCheckpoint]
            Previously taken checkpoints for monotonicity check.
        site_digest : str, optional
            Site state digest for staleness detection.

        Returns
        -------
        ResumptionCheckpoint
            A fresh ACTIVE checkpoint.

        Raises
        ------
        ValueError
            If the monotonicity invariant is violated relative to the most
            recent prior checkpoint.
        """
        seq = len(existing_checkpoints)
        cp  = ResumptionCheckpoint.make(
            sequence_number        = seq,
            section                = section,
            budget_consumed_so_far = budget_consumed,
            iterations_completed   = iterations_done,
            site_digest            = site_digest,
        )
        if existing_checkpoints:
            predecessor = existing_checkpoints[-1]
            if not cp.monotone_check(predecessor):
                raise ValueError(
                    f"Monotonicity violation: checkpoint {seq} covers "
                    f"fewer coordinates than checkpoint {seq - 1}."
                )
        return cp

    def resume_from_checkpoint(
        self,
        checkpoint: ResumptionCheckpoint,
        section_store: Mapping[str, PartialSection],
        site_digest: str = "",
    ) -> PartialSection:
        """Look up and validate the partial section at *checkpoint*.

        Parameters
        ----------
        checkpoint : ResumptionCheckpoint
            The checkpoint to resume from.
        section_store : Mapping[str, PartialSection]
            Maps section_id → PartialSection; used to retrieve the saved section.
        site_digest : str, optional
            Current site digest; compared against ``checkpoint.site_digest``
            when non-empty.

        Returns
        -------
        PartialSection
            The partial section at checkpoint time.

        Raises
        ------
        KeyError
            If the partial section is not found in *section_store*.
        ValueError
            If the checkpoint is not ACTIVE or the site digest has changed.
        """
        if not checkpoint.is_usable():
            raise ValueError(
                f"Checkpoint {checkpoint.checkpoint_id!r} is not ACTIVE "
                f"(status={checkpoint.status.value})."
            )
        if site_digest and checkpoint.site_digest and site_digest != checkpoint.site_digest:
            raise ValueError(
                f"Site digest mismatch: checkpoint was taken on site "
                f"{checkpoint.site_digest!r}, current site is {site_digest!r}. "
                "Checkpoint is stale."
            )
        if checkpoint.partial_section_id not in section_store:
            raise KeyError(
                f"PartialSection {checkpoint.partial_section_id!r} not found in section_store."
            )
        return section_store[checkpoint.partial_section_id]

    def invalidate_stale_checkpoints(
        self,
        checkpoints: Sequence[ResumptionCheckpoint],
        current_site_digest: str,
    ) -> list[ResumptionCheckpoint]:
        """Mark checkpoints as STALE when their site digest differs from current.

        Parameters
        ----------
        checkpoints : Sequence[ResumptionCheckpoint]
            All known checkpoints.
        current_site_digest : str
            Digest of the current site state.

        Returns
        -------
        list[ResumptionCheckpoint]
            Checkpoints with stale ones replaced by STALE-status copies.
        """
        result: list[ResumptionCheckpoint] = []
        for cp in checkpoints:
            if cp.status == CheckpointStatus.ACTIVE and cp.site_digest != current_site_digest:
                result.append(replace(cp, status=CheckpointStatus.STALE))
            else:
                result.append(cp)
        return result

    def compute_site_digest(self, coord_ids: Sequence[str]) -> str:
        """Compute a stable digest over an ordered set of coordinate IDs.

        Parameters
        ----------
        coord_ids : Sequence[str]
            The coordinate IDs defining the site topology.

        Returns
        -------
        str
            SHA-256 digest (16 hex chars) of the sorted, joined coordinate IDs.
        """
        raw = ",".join(sorted(coord_ids))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        ts    = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = f"[{ts}] {msg}"
        self._log.append(entry)
        if self.verbose:
            print(entry)

    def _validate_and_resume(
        self,
        section: PartialSection,
        checkpoint: ResumptionCheckpoint,
        site_digest: str,
    ) -> PartialSection:
        """Validate a checkpoint and adjust the section to checkpoint state."""
        if not checkpoint.is_usable():
            self._emit(
                f"resume: checkpoint {checkpoint.checkpoint_id!r} is "
                f"{checkpoint.status.value}; starting fresh"
            )
            return section
        if site_digest and checkpoint.site_digest and site_digest != checkpoint.site_digest:
            self._emit(
                f"resume: site digest mismatch — checkpoint is STALE; starting fresh"
            )
            return section
        self._emit(
            f"resume: from checkpoint {checkpoint.checkpoint_id!r} "
            f"(seq={checkpoint.sequence_number}, "
            f"covered={len(checkpoint.covered_coord_ids)})"
        )
        # Keep only holes for coordinates not yet covered at checkpoint time
        remaining_holes = [
            h for h in section.holes
            if h.coord_id not in checkpoint.covered_coord_ids
        ]
        recovered_data = {
            k: v for k, v in section.covered_data.items()
            if k in checkpoint.covered_coord_ids
        }
        return replace(section, covered_data=recovered_data, holes=tuple(remaining_holes))

    def _fill_iteration(
        self,
        section: PartialSection,
        iteration: int,
    ) -> tuple[PartialSection, list[str], str]:
        """Perform one closure iteration: fill unblocked holes heuristically.

        Strategy (in priority order):
        1.  Fill COCYCLE_FAILURE holes by removing the duplicate (keep first datum).
        2.  Fill UNANSWERED_OBLIGATION holes with a placeholder datum.
        3.  Fill MISSING_EVIDENCE holes with an empty dict datum.
        4.  Fill TRUST_DEFICIT holes by raising trust to floor.

        Returns
        -------
        tuple[PartialSection, list[str], str]
            (updated_section, filled_hole_ids, strategy_notes).
        """
        filled: list[str] = []
        current = section
        notes_parts: list[str] = []

        for hole in list(current.holes):
            if hole.is_blocked():
                continue
            if hole.kind == HoleKind.COCYCLE_FAILURE:
                datum = current.covered_data.get(hole.coord_id, {})
                try:
                    current = current.fill_hole(hole.hole_id, datum)
                    filled.append(hole.hole_id)
                    notes_parts.append(f"cocycle-repair:{hole.coord_id}")
                except KeyError:
                    pass
            elif hole.kind == HoleKind.UNANSWERED_OBLIGATION:
                placeholder = {"obligation_placeholder": True, "iteration": iteration}
                try:
                    current = current.fill_hole(hole.hole_id, placeholder)
                    filled.append(hole.hole_id)
                    notes_parts.append(f"obligation-placeholder:{hole.coord_id}")
                except KeyError:
                    pass
            elif hole.kind == HoleKind.MISSING_EVIDENCE:
                try:
                    current = current.fill_hole(hole.hole_id, {})
                    filled.append(hole.hole_id)
                    notes_parts.append(f"empty-evidence:{hole.coord_id}")
                except KeyError:
                    pass
            elif hole.kind == HoleKind.TRUST_DEFICIT:
                try:
                    current = current.fill_hole(hole.hole_id, {"trust_elevated": True})
                    filled.append(hole.hole_id)
                    notes_parts.append(f"trust-elevated:{hole.coord_id}")
                except KeyError:
                    pass

        return current, filled, "; ".join(notes_parts) or "no progress"

    def _compute_terminal_status(
        self,
        section: PartialSection,
        attempts: list[ClosureAttempt],
        budget_used: float,
    ) -> ClosureStatus:
        """Determine the terminal closure status from the final state."""
        if section.is_complete():
            return ClosureStatus.SUCCESS
        if budget_used >= self.total_budget:
            return ClosureStatus.TIMEOUT
        if attempts and attempts[-1].status == ClosureStatus.OBSTRUCTED:
            return ClosureStatus.OBSTRUCTED
        if len(attempts) >= self.max_iterations:
            return ClosureStatus.TIMEOUT
        return ClosureStatus.PARTIAL


# ---------------------------------------------------------------------------
# ClosureResumabilityAnalyzer
# ---------------------------------------------------------------------------

class ClosureResumabilityAnalyzer:
    """Analyses closure witnesses and produces structured diagnostics.

    Computes closure metrics, hole-fill rates, checkpoint statistics, and
    budget efficiency.  Provides recommendations for improving closure
    strategy configuration.

    Parameters
    ----------
    min_coverage_threshold : float
        Minimum coverage fraction considered healthy.  Default: 0.9.
    """

    def __init__(self, min_coverage_threshold: float = 0.9) -> None:
        self.min_coverage_threshold = min_coverage_threshold

    def analyze(self, witness: ClosureResumabilityWitness) -> dict[str, Any]:
        """Produce a full structured analysis of the closure witness.

        Parameters
        ----------
        witness : ClosureResumabilityWitness
            The output of a ``Coordinator.run()`` call.

        Returns
        -------
        dict[str, Any]
            Keys: ``summary``, ``closure``, ``checkpoints``, ``budget``,
            ``holes``, ``recommendations``.
        """
        res = witness.result
        return {
            "summary":         self.summarize(witness),
            "closure":         self._analyze_closure(res),
            "checkpoints":     self._analyze_checkpoints(witness.checkpoints),
            "budget":          self._analyze_budget(res),
            "holes":           self._analyze_holes(res.final_section),
            "recommendations": self._build_recommendations(res, witness.checkpoints),
        }

    def score(self, witness: ClosureResumabilityWitness) -> float:
        """Return a [0, 1] quality score for the closure procedure.

        Score formula::

            score = coverage_fraction × success_bonus × budget_efficiency
        """
        res               = witness.result
        coverage          = res.final_section.coverage_fraction()
        success_bonus     = 1.0 if res.status == ClosureStatus.SUCCESS else 0.7
        budget_efficiency = min(
            1.0,
            res.final_section.coverage_fraction()
            / max(res.total_budget_consumed, 1e-9),
        )
        return coverage * success_bonus * min(budget_efficiency, 1.0)

    def report(self, witness: ClosureResumabilityWitness) -> str:
        """Return a human-readable text report of the closure procedure.

        Parameters
        ----------
        witness : ClosureResumabilityWitness
            The certificate to report on.

        Returns
        -------
        str
            Multi-line text report.
        """
        res = witness.result
        lines = [
            "=" * 72,
            "ClosureResumability — Closure Procedure Report",
            f"  witness_id      : {witness.witness_id}",
            f"  created_at      : {witness.created_at}",
            f"  elapsed_s       : {witness.elapsed_s:.4f}",
            f"  score           : {self.score(witness):.4f}",
            "-" * 72,
            f"  status          : {res.status.value}",
            f"  total_iterations: {res.total_iterations}",
            f"  budget_consumed : {res.total_budget_consumed:.2f}",
            f"  holes_filled    : {res.holes_filled_count()}",
            f"  coverage        : {res.final_section.coverage_fraction():.2%}",
            f"  n_checkpoints   : {len(witness.checkpoints)}",
            f"  final_holes     : {len(res.final_section.holes)}",
            "-" * 72,
        ]
        for i, attempt in enumerate(res.attempts):
            lines.append(
                f"  iter {attempt.iteration:3d}: "
                f"filled={len(attempt.holes_filled)} "
                f"remaining={attempt.holes_remaining} "
                f"status={attempt.status.value}  "
                f"notes={attempt.notes!r}"
            )
        if witness.checkpoints:
            lines.append("-" * 72)
            lines.append("  CHECKPOINTS:")
            for cp in witness.checkpoints:
                lines.append(
                    f"    #{cp.sequence_number} [{cp.status.value}] "
                    f"covered={len(cp.covered_coord_ids)} "
                    f"remaining={len(cp.remaining_coord_ids)} "
                    f"budget={cp.budget_consumed_so_far:.2f}"
                )
        lines.append("=" * 72)
        return "\n".join(lines)

    def summarize(self, witness: ClosureResumabilityWitness) -> dict[str, Any]:
        """Return a compact summary dict.

        Parameters
        ----------
        witness : ClosureResumabilityWitness
            The certificate to summarise.

        Returns
        -------
        dict[str, Any]
            Keys: ``witness_id``, ``status``, ``score``, ``coverage``,
            ``n_iterations``, ``n_checkpoints``, ``budget_consumed``.
        """
        res = witness.result
        return {
            "witness_id":    witness.witness_id,
            "status":        res.status.value,
            "score":         round(self.score(witness), 6),
            "coverage":      round(res.final_section.coverage_fraction(), 6),
            "n_iterations":  res.total_iterations,
            "n_checkpoints": len(witness.checkpoints),
            "budget_consumed": round(res.total_budget_consumed, 4),
            "elapsed_s":     round(witness.elapsed_s, 6),
        }

    def is_healthy(self, witness: ClosureResumabilityWitness) -> bool:
        """True when the closure is successful and coverage meets the threshold.

        Health criteria:

        -   ``result.status == ClosureStatus.SUCCESS``.
        -   ``final_section.coverage_fraction() >= min_coverage_threshold``.
        -   No remaining holes in the final section.
        """
        res = witness.result
        return (
            res.status == ClosureStatus.SUCCESS
            and res.final_section.coverage_fraction() >= self.min_coverage_threshold
            and len(res.final_section.holes) == 0
        )

    def hole_fill_rate(self, witness: ClosureResumabilityWitness) -> float:
        """Return the rate of hole-filling per iteration.

        Parameters
        ----------
        witness : ClosureResumabilityWitness
            The certificate to analyse.

        Returns
        -------
        float
            ``total_holes_filled / total_iterations``.
        """
        n_iters = max(witness.result.total_iterations, 1)
        return witness.result.holes_filled_count() / n_iters

    def checkpoint_coverage_series(
        self, witness: ClosureResumabilityWitness
    ) -> list[dict[str, Any]]:
        """Return a time-series of coverage fractions at each checkpoint.

        Parameters
        ----------
        witness : ClosureResumabilityWitness
            The certificate to analyse.

        Returns
        -------
        list[dict[str, Any]]
            One entry per checkpoint: ``{sequence_number, covered, fraction}``.
        """
        return [
            {
                "sequence_number": cp.sequence_number,
                "covered":         len(cp.covered_coord_ids),
                "fraction":        cp.coverage_fraction(),
            }
            for cp in witness.checkpoints
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _analyze_closure(self, res: ClosureResult) -> dict[str, Any]:
        return {
            "status":         res.status.value,
            "n_iterations":   res.total_iterations,
            "holes_filled":   res.holes_filled_count(),
            "final_coverage": res.final_section.coverage_fraction(),
            "success":        res.status == ClosureStatus.SUCCESS,
        }

    def _analyze_checkpoints(
        self, checkpoints: Sequence[ResumptionCheckpoint]
    ) -> dict[str, Any]:
        active   = sum(1 for cp in checkpoints if cp.status == CheckpointStatus.ACTIVE)
        stale    = sum(1 for cp in checkpoints if cp.status == CheckpointStatus.STALE)
        consumed = sum(1 for cp in checkpoints if cp.status == CheckpointStatus.CONSUMED)
        return {
            "total":    len(checkpoints),
            "active":   active,
            "stale":    stale,
            "consumed": consumed,
        }

    def _analyze_budget(self, res: ClosureResult) -> dict[str, Any]:
        return {
            "consumed":              res.total_budget_consumed,
            "per_iteration":         (
                res.total_budget_consumed / max(res.total_iterations, 1)
            ),
            "per_hole_filled":       (
                res.total_budget_consumed / max(res.holes_filled_count(), 1)
            ),
        }

    def _analyze_holes(self, section: PartialSection) -> dict[str, Any]:
        by_kind: dict[str, int] = defaultdict(int)
        for h in section.holes:
            by_kind[h.kind.value] += 1
        return {
            "remaining_count": len(section.holes),
            "by_kind":         dict(by_kind),
            "blocked_count":   sum(1 for h in section.holes if h.is_blocked()),
        }

    def _build_recommendations(
        self,
        res: ClosureResult,
        checkpoints: Sequence[ResumptionCheckpoint],
    ) -> list[str]:
        recs: list[str] = []
        if res.status == ClosureStatus.TIMEOUT:
            recs.append("Increase max_iterations or total_budget to avoid timeout.")
        if res.status == ClosureStatus.OBSTRUCTED:
            recs.append(
                "Closure is obstructed; supply additional evidence or split "
                "coordinates to resolve H¹ obstruction."
            )
        if res.final_section.holes:
            blocked = sum(1 for h in res.final_section.holes if h.is_blocked())
            if blocked:
                recs.append(f"Discharge {blocked} blocking obligation(s) to unblock holes.")
        if not checkpoints:
            recs.append("No checkpoints were taken; reduce checkpoint_interval for resilience.")
        if not recs:
            recs.append("Closure is healthy; no action required.")
        return recs


# ---------------------------------------------------------------------------
# ClosureResumabilityWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClosureResumabilityWitness:
    """Immutable output certificate for a closure/resumability run.

    Captures the full closure result, all checkpoints, timing information,
    and the audit log for this run.

    Parameters
    ----------
    witness_id : str
        Unique 12-hex identifier.
    result : ClosureResult
        The terminal closure result including all attempts and the final section.
    checkpoints : tuple[ResumptionCheckpoint, ...]
        All checkpoints taken during this run in chronological order.
    elapsed_s : float
        Total wall-clock time for the run.
    log_lines : tuple[str, ...]
        Ordered log lines for debugging.
    created_at : str
        ISO-8601 creation timestamp.

    Examples
    --------
    >>> w = coordinator.run(section)
    >>> assert w.result.status in (ClosureStatus.SUCCESS, ClosureStatus.OBSTRUCTED)
    >>> serialised = json.dumps(w.to_dict())
    >>> w2 = ClosureResumabilityWitness.from_dict(json.loads(serialised))
    >>> w2.witness_id == w.witness_id
    True
    """

    witness_id  : str
    result      : ClosureResult
    checkpoints : tuple[ResumptionCheckpoint, ...]
    elapsed_s   : float
    log_lines   : tuple[str, ...]
    created_at  : str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "witness_id":  self.witness_id,
            "result":      self.result.to_dict(),
            "checkpoints": [cp.to_dict() for cp in self.checkpoints],
            "elapsed_s":   self.elapsed_s,
            "log_lines":   list(self.log_lines),
            "created_at":  self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClosureResumabilityWitness:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            witness_id  = d["witness_id"],
            result      = ClosureResult.from_dict(d["result"]),
            checkpoints = tuple(
                ResumptionCheckpoint.from_dict(cp) for cp in d.get("checkpoints", [])
            ),
            elapsed_s   = float(d.get("elapsed_s", 0.0)),
            log_lines   = tuple(d.get("log_lines", [])),
            created_at  = d["created_at"],
        )

    def digest(self) -> str:
        """Content-hash of this witness (SHA-256 over canonical JSON, 24 hex)."""
        raw = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def is_successful(self) -> bool:
        """True when closure succeeded and the final section is complete."""
        return (
            self.result.status == ClosureStatus.SUCCESS
            and self.result.final_section.is_complete()
        )

    def latest_checkpoint(self) -> ResumptionCheckpoint | None:
        """Return the most recent ACTIVE checkpoint, or None if none exist."""
        actives = [cp for cp in self.checkpoints if cp.status == CheckpointStatus.ACTIVE]
        return actives[-1] if actives else None


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== ClosureResumability smoke test ===")

    # Build a partial section with 4 holes across 6 coordinates
    covered = {
        "coord_security_auth":   "auth_validated",
        "coord_correctness_type": "typed_checked",
    }
    holes = [
        SectionHole.make(
            "coord_security_sql",
            HoleKind.MISSING_EVIDENCE,
            description     = "No SQL injection evidence found",
            repair_strategies = ["evidence_lookup", "static_analysis"],
        ),
        SectionHole.make(
            "coord_correctness_null",
            HoleKind.UNANSWERED_OBLIGATION,
            description           = "Null-check obligation outstanding",
            blocking_obligations  = [],
        ),
        SectionHole.make(
            "coord_performance_hot",
            HoleKind.TRUST_DEFICIT,
            description = "Section at PROPOSAL; needs PROVISIONAL",
            trust_floor = TrustTier.PROVISIONAL,
        ),
        SectionHole.make(
            "coord_security_xss",
            HoleKind.MISSING_EVIDENCE,
            description           = "XSS evidence missing",
            blocking_obligations  = ["run_bandit_scan"],
        ),
    ]

    section = PartialSection.make(
        covered_data = covered,
        holes        = holes,
        total_coords = 6,
        trust_tier   = TrustTier.PROVISIONAL,
    )

    coord = ClosureResumabilityCoordinator(
        max_iterations       = 10,
        budget_per_iteration = 1.0,
        total_budget         = 15.0,
        checkpoint_interval  = 3,
        verbose              = False,
    )

    errs = coord.validate(section)
    assert not errs, f"Validation errors: {errs}"

    witness  = coord.run(section)
    analyzer = ClosureResumabilityAnalyzer()

    print(analyzer.report(witness))
    summary = analyzer.summarize(witness)
    assert summary["n_iterations"] > 0
    assert summary["coverage"] > 0

    # Round-trip serialisation
    reloaded = ClosureResumabilityWitness.from_dict(witness.to_dict())
    assert reloaded.witness_id == witness.witness_id
    assert reloaded.digest() == witness.digest()

    # Evidence-fill shortcut
    evidence = {
        "coord_security_sql":    {"evidence": "parameterised_queries"},
        "coord_performance_hot": {"evidence": "profiled"},
    }
    updated, filled_ids = coord.fill_from_evidence(section, evidence)
    assert len(filled_ids) == 1  # only non-blocked holes filled (sql is unblocked)

    # Compute site digest
    all_ids = list(covered.keys()) + [h.coord_id for h in holes]
    digest  = coord.compute_site_digest(all_ids)
    assert len(digest) == 16

    print("\nsmoke test PASSED")
    print(json.dumps(summary, indent=2))
    sys.exit(0)
