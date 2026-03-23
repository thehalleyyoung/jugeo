"""Stage S01 (Companion): The System Should Be Cyclic, Not Pipeline-Linear — JuGeo cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
This module articulates and implements the central architectural thesis of the
cyclic picture framework (Ch65, theory2.tex): a mature geometric-reasoning
system must be structured as a *cycle*, not a *pipeline*.

A pipeline is a directed acyclic graph that accepts input, transforms it
through a fixed sequence of stages, and terminates.  Once it terminates, it
produces no further value; the feedback arrows that would allow later stages to
influence earlier ones are categorically absent.  This architecture has the
virtue of simplicity but the fatal flaw of stasis: a pipeline cannot improve
itself, cannot detect that its own outputs have become stale, and cannot respond
to the emergence of new obstruction classes that invalidate its earlier stages.

A cycle, by contrast, is a directed graph in which every stage has at least one
outgoing arc back to an earlier stage.  The canonical JuGeo cycle is:

    IDEATION → GENERATION → VERIFICATION → OBSTRUCTION_ANALYSIS → SYNTHESIS
        ↑___________________________________________________|

After the SYNTHESIS phase completes, the enriched context—including newly
discovered obstructions, revised trust scores, and updated capability maps—is
fed back into IDEATION to seed the next iteration.  The loop is never cut
unless an explicit external stop signal is received; in the steady state, the
system runs forever and monotonically improves.

Cyclic soundness theorem (Ch65, §4.1)
--------------------------------------
Let *C* be a cyclic system with phase set *P* = {IDEATION, GENERATION,
VERIFICATION, OBSTRUCTION_ANALYSIS, SYNTHESIS} and transition function
*δ: P × E → P* (where *E* is the set of observable events).  Define the trust
score *τ: ℕ → [0,1]* as the mean over completed phases of the fraction of
successfully verified claims.  The theorem states:

    For every ε > 0, there exists a cycle index *n₀* such that for all *n ≥ n₀*,
    |τ(n) - 1| < ε,

provided the obstruction-analysis phase correctly classifies every obstruction
encountered.  In other words, a correctly implemented cyclic system converges
toward a trust score of 1 in the limit.

This module provides the following concrete witnesses for the theorem:

* ``CyclePhase`` — encodes the phase set *P* and the transition function *δ*.
* ``CycleRecord`` — records one full orbit of the cycle, providing the
  time-series data for *τ*.
* ``CycleTransition`` — witnesses a single application of *δ*.
* ``CycleMetrics`` — aggregates many ``CycleRecord`` instances into summary
  statistics, enabling empirical verification of the convergence claim.
* ``CycleObstruction`` — represents an element of the obstruction set *O* that
  the OBSTRUCTION_ANALYSIS phase must classify.
* ``CyclicSystemAnalyzer`` — analyses a running system to determine whether it
  exhibits true cyclic behaviour or has degraded into linear pipeline operation.
* ``CyclicSystemWitness`` — a cryptographic/logical proof object that certifies
  the system has completed at least one valid cycle, satisfying the precondition
  of the soundness theorem.
* ``CyclicSystemCoordinator`` — the main runtime object that manages cycle
  execution, phase transitions, obstruction handling, and metrics collection.

All public names are listed in ``__all__``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "CyclePhase",
    "CycleRecord",
    "CycleTransition",
    "CycleMetrics",
    "CycleObstruction",
    "CyclicSystemAnalyzer",
    "CyclicSystemWitness",
    "CyclicSystemCoordinator",
    "run_cycle",
    "analyze_system_cyclicity",
    "build_cycle_witness",
]

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.maturity.cyclic_picture.models import (
        ImprovementCycle,
        ImprovementKind,
        MaturityLevel,
        MatureSystem,
        SelfImprovingEngine,
        FederationState,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.algorithms import (
        score_transition,
        classify_obstruction,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.theorems import (
        CyclicSoundnessTheorem,
        ConvergenceWitness,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses ``time.gmtime`` rather than ``datetime`` to avoid the import overhead
    and to remain compatible with environments where the ``datetime`` module
    may be restricted.  The returned string is always in the format
    ``YYYY-MM-DDTHH:MM:SSZ``.

    Returns
    -------
    str
        A UTC timestamp string in ISO-8601 format, e.g.
        ``'2024-07-01T12:00:00Z'``.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _uid() -> str:
    """Generate a short, unique identifier string.

    Produces a 16-character hex string derived from a UUID4 value.  The
    truncation keeps identifiers human-readable while providing enough entropy
    (64 bits) for practical uniqueness within a single pipeline run.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string, e.g. ``'a3f1c9e20b7d4f81'``.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point number to clamp.
    lo:
        The lower bound (inclusive).
    hi:
        The upper bound (inclusive).

    Returns
    -------
    float
        The clamped value; equal to *lo* if *value < lo*, equal to *hi* if
        *value > hi*, and equal to *value* otherwise.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# CyclePhase
# ---------------------------------------------------------------------------

# Canonical phase name constants used throughout the module.
_PHASE_IDEATION = "IDEATION"
_PHASE_GENERATION = "GENERATION"
_PHASE_VERIFICATION = "VERIFICATION"
_PHASE_OBSTRUCTION_ANALYSIS = "OBSTRUCTION_ANALYSIS"
_PHASE_SYNTHESIS = "SYNTHESIS"

_PHASE_ORDER = [
    _PHASE_IDEATION,
    _PHASE_GENERATION,
    _PHASE_VERIFICATION,
    _PHASE_OBSTRUCTION_ANALYSIS,
    _PHASE_SYNTHESIS,
]

_PHASE_DESCRIPTIONS = {
    _PHASE_IDEATION: (
        "Seed the cycle with new goals, hypotheses, and capability requirements.  "
        "This phase consumes the enriched context produced by the previous SYNTHESIS "
        "phase (or a bootstrap context for the very first cycle) and outputs a "
        "structured ideation bundle that guides GENERATION."
    ),
    _PHASE_GENERATION: (
        "Produce candidate artefacts — proofs, code fragments, geometric "
        "constructions, or abstract terms — that attempt to realise the goals set "
        "during IDEATION.  The output is a generation bundle containing one or more "
        "candidates together with their provenance records."
    ),
    _PHASE_VERIFICATION: (
        "Check each candidate from GENERATION against the system's current "
        "soundness constraints and trust thresholds.  Candidates that pass are "
        "promoted to verified artefacts; those that fail are tagged with their "
        "failure mode and forwarded to OBSTRUCTION_ANALYSIS."
    ),
    _PHASE_OBSTRUCTION_ANALYSIS: (
        "Classify, explain, and — where possible — resolve each obstruction "
        "encountered during VERIFICATION.  Resolutions may feed back into IDEATION "
        "as negative examples, strengthen the generation heuristics used in "
        "GENERATION, or update the soundness constraints used in VERIFICATION "
        "itself.  This phase is the key feedback mechanism that distinguishes a "
        "genuine cycle from a pipeline."
    ),
    _PHASE_SYNTHESIS: (
        "Merge all verified artefacts with the accumulated obstruction resolutions "
        "into an enriched system state.  Update capability maps, priority weights, "
        "and the trust score.  Emit the enriched context as input to the next "
        "IDEATION phase, closing the cycle."
    ),
}


@dataclass(slots=True)
class CyclePhase:
    """An enum-like dataclass encoding one of the five canonical cycle phases.

    ``CyclePhase`` is the computational witness for the phase set *P* in the
    cyclic soundness theorem (Ch65, §4.1).  Rather than using a bare Python
    ``Enum``, we use a dataclass so that additional metadata (e.g., the phase
    index and a description) can be carried alongside the name without a
    separate lookup table.

    Attributes
    ----------
    name : str
        One of the five canonical phase names: ``'IDEATION'``,
        ``'GENERATION'``, ``'VERIFICATION'``, ``'OBSTRUCTION_ANALYSIS'``,
        ``'SYNTHESIS'``.
    index : int
        Zero-based position in the canonical phase order (0 = IDEATION,
        4 = SYNTHESIS).

    Class-level constants
    ---------------------
    IDEATION, GENERATION, VERIFICATION, OBSTRUCTION_ANALYSIS, SYNTHESIS are
    string constants exposed for convenient comparison without constructing
    instances.
    """

    name: str
    index: int

    IDEATION: str = field(default=_PHASE_IDEATION, init=False, repr=False, compare=False)
    GENERATION: str = field(default=_PHASE_GENERATION, init=False, repr=False, compare=False)
    VERIFICATION: str = field(default=_PHASE_VERIFICATION, init=False, repr=False, compare=False)
    OBSTRUCTION_ANALYSIS: str = field(
        default=_PHASE_OBSTRUCTION_ANALYSIS, init=False, repr=False, compare=False
    )
    SYNTHESIS: str = field(default=_PHASE_SYNTHESIS, init=False, repr=False, compare=False)

    # ------------------------------------------------------------------
    @classmethod
    def from_name(cls, name: str) -> "CyclePhase":
        """Construct a ``CyclePhase`` from a phase name string.

        Looks up the canonical index for *name* in the global ``_PHASE_ORDER``
        list and raises ``ValueError`` if the name is not recognised.

        Parameters
        ----------
        name:
            One of the five canonical phase name strings.  Case-sensitive.

        Returns
        -------
        CyclePhase
            An instance whose ``name`` and ``index`` correspond to the given
            phase name.

        Raises
        ------
        ValueError
            If *name* is not in the canonical phase list.
        """
        if name not in _PHASE_ORDER:
            raise ValueError(
                f"Unknown phase name {name!r}; expected one of {_PHASE_ORDER}"
            )
        return cls(name=name, index=_PHASE_ORDER.index(name))

    # ------------------------------------------------------------------
    @classmethod
    def initial(cls) -> "CyclePhase":
        """Return the initial (IDEATION) phase, used to start every cycle.

        This is a convenience factory method that creates the phase at index 0
        without requiring the caller to know the canonical name string.

        Returns
        -------
        CyclePhase
            The IDEATION phase instance.
        """
        return cls(name=_PHASE_IDEATION, index=0)

    # ------------------------------------------------------------------
    def next_phase(self) -> "CyclePhase":
        """Return the phase that follows the current one in the canonical order.

        After SYNTHESIS (index 4), wraps around to IDEATION (index 0), modelling
        the cyclic nature of the system.  This wrap-around is the key structural
        property that distinguishes the cycle from a pipeline: there is no
        terminal state — the system always has a well-defined next phase.

        Returns
        -------
        CyclePhase
            The successor phase.  Wraps from SYNTHESIS back to IDEATION.
        """
        next_index = (self.index + 1) % len(_PHASE_ORDER)
        return CyclePhase(name=_PHASE_ORDER[next_index], index=next_index)

    # ------------------------------------------------------------------
    def is_terminal(self) -> bool:
        """Return whether this phase is the SYNTHESIS (final) phase of a cycle.

        Although the cycle wraps back to IDEATION after SYNTHESIS, SYNTHESIS is
        conventionally regarded as the *end* of a single cycle orbit.  Callers
        use this predicate to decide when to close a ``CycleRecord`` and open
        a new one.

        Returns
        -------
        bool
            ``True`` if and only if ``self.name == 'SYNTHESIS'``.
        """
        return self.name == _PHASE_SYNTHESIS

    # ------------------------------------------------------------------
    def description(self) -> str:
        """Return a prose description of what this phase does.

        Retrieves the description from the module-level ``_PHASE_DESCRIPTIONS``
        mapping.  Returns a generic placeholder string if the phase name is not
        found (which should not occur for canonically constructed instances).

        Returns
        -------
        str
            Multi-sentence description of the phase's role in the cycle.
        """
        return _PHASE_DESCRIPTIONS.get(
            self.name,
            f"No description available for phase {self.name!r}.",
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this phase to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary with keys ``name`` and ``index``.
        """
        return {"name": self.name, "index": self.index}


# ---------------------------------------------------------------------------
# CycleRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CycleRecord:
    """Records one full orbit of the cyclic system, from IDEATION to SYNTHESIS.

    A ``CycleRecord`` is the primary evidence object for the cyclic soundness
    theorem: it captures the sequence of phases completed, the wall-clock
    duration of each phase, the number of obstructions encountered, and the
    trust score at the end of the orbit.  Accumulating many ``CycleRecord``
    instances into a ``CycleMetrics`` object enables empirical verification of
    the convergence claim.

    Attributes
    ----------
    record_id : str
        Unique identifier for this record, generated by ``_uid()``.
    cycle_index : int
        Zero-based ordinal of this orbit within the coordinator's history.
    phases_completed : list[str]
        Ordered list of phase names completed during this orbit.
    phase_durations : dict[str, float]
        Mapping from phase name to wall-clock duration in seconds.
    obstruction_count : int
        Total number of obstructions encountered across all phases.
    trust_score : float
        Mean trust score at the end of this orbit, clamped to [0, 1].
    started_at : str
        ISO-8601 UTC timestamp when the orbit began (IDEATION entered).
    completed_at : str
        ISO-8601 UTC timestamp when the orbit ended (SYNTHESIS exited), or an
        empty string if the orbit has not yet completed.
    metadata : dict
        Arbitrary key-value metadata attached by the caller or coordinator.
    """

    record_id: str
    cycle_index: int
    phases_completed: list
    phase_durations: dict
    obstruction_count: int
    trust_score: float
    started_at: str
    completed_at: str
    metadata: dict

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, cycle_index: int, metadata: dict | None = None) -> "CycleRecord":
        """Factory method that opens a new, empty ``CycleRecord``.

        The record starts with no completed phases, no phase durations, zero
        obstructions, and a trust score of 0.0.  Call ``advance`` on the
        coordinator to populate these fields as the orbit progresses.

        Parameters
        ----------
        cycle_index:
            The zero-based ordinal of this orbit in the coordinator's history.
        metadata:
            Optional mapping of arbitrary key-value pairs attached to the
            record for traceability (e.g., context tags, caller identifiers).
            Defaults to an empty dict when ``None``.

        Returns
        -------
        CycleRecord
            A freshly opened record ready to be populated by the coordinator.
        """
        return cls(
            record_id=_uid(),
            cycle_index=cycle_index,
            phases_completed=[],
            phase_durations={},
            obstruction_count=0,
            trust_score=0.0,
            started_at=_utcnow(),
            completed_at="",
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    def total_duration(self) -> float:
        """Compute the total wall-clock duration of this orbit in seconds.

        Sums the individual phase durations stored in ``phase_durations``.
        This is the sum-of-parts duration; if a phase was skipped (e.g., due
        to an unrecovered obstruction) its duration is 0.0 and does not inflate
        the total.

        Returns
        -------
        float
            Sum of all phase durations in seconds.  Returns 0.0 if no phases
            have been completed yet.
        """
        return sum(self.phase_durations.values())

    # ------------------------------------------------------------------
    def mean_phase_duration(self) -> float:
        """Compute the mean per-phase wall-clock duration.

        Divides ``total_duration()`` by the number of completed phases.
        Returns 0.0 for an orbit with no completed phases to avoid division
        by zero.

        Returns
        -------
        float
            Mean phase duration in seconds, or 0.0 if no phases completed.
        """
        n = len(self.phase_durations)
        if n == 0:
            return 0.0
        return self.total_duration() / n

    # ------------------------------------------------------------------
    def is_successful(self) -> bool:
        """Return whether this orbit completed successfully.

        An orbit is considered successful if and only if all five canonical
        phases appear in ``phases_completed``, the trust score exceeds the
        minimum threshold of 0.5, and the ``completed_at`` timestamp is
        non-empty.  This definition is conservative: partial orbits and low-
        trust orbits both count as unsuccessful, forcing the coordinator to
        investigate before proceeding.

        Returns
        -------
        bool
            ``True`` if the orbit is complete, high-trust, and all phases ran.
        """
        all_phases_run = all(p in self.phases_completed for p in _PHASE_ORDER)
        return (
            all_phases_run
            and self.trust_score >= 0.5
            and bool(self.completed_at)
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this record to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary containing all fields of the record.
            ``phases_completed`` is returned as a list copy; ``phase_durations``
            and ``metadata`` are returned as dict copies so that mutations to
            the returned value cannot corrupt the record.
        """
        return {
            "record_id": self.record_id,
            "cycle_index": self.cycle_index,
            "phases_completed": list(self.phases_completed),
            "phase_durations": dict(self.phase_durations),
            "obstruction_count": self.obstruction_count,
            "trust_score": self.trust_score,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration": self.total_duration(),
            "is_successful": self.is_successful(),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# CycleTransition
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CycleTransition:
    """Models a single application of the transition function *δ: P × E → P*.

    A ``CycleTransition`` witnesses the move from one cycle phase to the next.
    It records the source and destination phases, the event that triggered the
    transition, and any obstruction that was active at the time.  Collecting
    a sequence of ``CycleTransition`` objects for a single orbit provides a
    detailed audit trail of how the system navigated the cycle.

    Attributes
    ----------
    from_phase : str
        The name of the phase that was left.
    to_phase : str
        The name of the phase that was entered.
    transition_id : str
        Unique identifier for this transition event.
    trigger : str
        A short human-readable string describing the event that caused the
        transition (e.g., ``'phase_complete'``, ``'obstruction_resolved'``).
    obstruction : Optional[str]
        The ``obstruction_id`` of an active ``CycleObstruction`` that was
        present at the time of transition, or ``None`` if the transition was
        unobstructed.
    timestamp : str
        ISO-8601 UTC timestamp when the transition occurred.
    """

    from_phase: str
    to_phase: str
    transition_id: str
    trigger: str
    obstruction: Optional[str]
    timestamp: str

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        from_phase: str,
        to_phase: str,
        trigger: str = "phase_complete",
        obstruction: Optional[str] = None,
    ) -> "CycleTransition":
        """Factory method for ``CycleTransition``.

        Generates a fresh ``transition_id`` and records the current UTC
        timestamp.

        Parameters
        ----------
        from_phase:
            The phase being exited.
        to_phase:
            The phase being entered.
        trigger:
            The event label that caused the transition.  Defaults to
            ``'phase_complete'`` for normal unobstructed transitions.
        obstruction:
            Optional obstruction ID if an obstruction was involved.

        Returns
        -------
        CycleTransition
            A freshly constructed transition record.
        """
        return cls(
            from_phase=from_phase,
            to_phase=to_phase,
            transition_id=_uid(),
            trigger=trigger,
            obstruction=obstruction,
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    def is_obstructed(self) -> bool:
        """Return whether an obstruction was active during this transition.

        A transition is obstructed if and only if the ``obstruction`` field is
        a non-empty string.  Obstructed transitions indicate that the cycle had
        to route through the OBSTRUCTION_ANALYSIS phase rather than proceeding
        normally, and they are counted when computing obstruction rates in
        ``CycleMetrics``.

        Returns
        -------
        bool
            ``True`` if ``self.obstruction`` is a non-empty string, ``False``
            otherwise.
        """
        return bool(self.obstruction)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this transition to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary with all transition fields plus a
            boolean ``is_obstructed`` key for convenience.
        """
        return {
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "transition_id": self.transition_id,
            "trigger": self.trigger,
            "obstruction": self.obstruction,
            "timestamp": self.timestamp,
            "is_obstructed": self.is_obstructed(),
        }


# ---------------------------------------------------------------------------
# CycleMetrics
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CycleMetrics:
    """Aggregated metrics computed over a collection of ``CycleRecord`` objects.

    ``CycleMetrics`` is the summary statistics layer of the cyclic system.
    After the coordinator has completed *n* orbits, it computes a
    ``CycleMetrics`` snapshot that captures the success rate, mean duration,
    mean trust score, total obstruction count, and per-phase visit counts and
    mean durations.  These statistics are the empirical evidence used to verify
    the convergence claim of the cyclic soundness theorem.

    Attributes
    ----------
    total_cycles : int
        Total number of orbits completed (including unsuccessful ones).
    successful_cycles : int
        Number of orbits for which ``CycleRecord.is_successful()`` returned
        ``True``.
    mean_cycle_duration : float
        Mean wall-clock duration in seconds across all completed orbits.
    mean_trust_score : float
        Mean trust score across all completed orbits.
    total_obstructions : int
        Total number of obstructions encountered across all orbits.
    phase_visit_counts : dict[str, int]
        Mapping from phase name to the number of times that phase was visited
        across all orbits.
    phase_mean_durations : dict[str, float]
        Mapping from phase name to the mean wall-clock duration in seconds
        across all orbits in which that phase was visited.
    """

    total_cycles: int
    successful_cycles: int
    mean_cycle_duration: float
    mean_trust_score: float
    total_obstructions: int
    phase_visit_counts: dict
    phase_mean_durations: dict

    # ------------------------------------------------------------------
    @classmethod
    def from_records(cls, records: list) -> "CycleMetrics":
        """Compute a ``CycleMetrics`` instance from a list of ``CycleRecord`` objects.

        Iterates over *records* once to accumulate all counters, then divides
        to produce mean values.  Phases that were never visited receive a visit
        count of 0 and a mean duration of 0.0.

        Parameters
        ----------
        records:
            List of ``CycleRecord`` instances (or dicts returned by
            ``CycleRecord.to_dict()``).  May be empty.

        Returns
        -------
        CycleMetrics
            The aggregated metrics snapshot.
        """
        total = len(records)
        successful = 0
        total_duration = 0.0
        total_trust = 0.0
        total_obs = 0
        phase_visit_counts: dict = {p: 0 for p in _PHASE_ORDER}
        phase_duration_sums: dict = {p: 0.0 for p in _PHASE_ORDER}

        for rec in records:
            if isinstance(rec, dict):
                rec_dict = rec
            elif hasattr(rec, "to_dict"):
                rec_dict = rec.to_dict()
            else:
                rec_dict = {}

            if rec_dict.get("is_successful", False):
                successful += 1
            total_duration += rec_dict.get("total_duration", 0.0)
            total_trust += rec_dict.get("trust_score", 0.0)
            total_obs += rec_dict.get("obstruction_count", 0)
            phase_durations = rec_dict.get("phase_durations", {})
            phases_completed = rec_dict.get("phases_completed", [])
            for ph in phases_completed:
                phase_visit_counts[ph] = phase_visit_counts.get(ph, 0) + 1
                dur = phase_durations.get(ph, 0.0)
                phase_duration_sums[ph] = phase_duration_sums.get(ph, 0.0) + dur

        mean_dur = total_duration / total if total > 0 else 0.0
        mean_trust = total_trust / total if total > 0 else 0.0
        phase_mean_durations = {
            p: (phase_duration_sums[p] / phase_visit_counts[p])
            if phase_visit_counts.get(p, 0) > 0
            else 0.0
            for p in _PHASE_ORDER
        }
        return cls(
            total_cycles=total,
            successful_cycles=successful,
            mean_cycle_duration=mean_dur,
            mean_trust_score=mean_trust,
            total_obstructions=total_obs,
            phase_visit_counts=dict(phase_visit_counts),
            phase_mean_durations=phase_mean_durations,
        )

    # ------------------------------------------------------------------
    def success_rate(self) -> float:
        """Return the fraction of completed orbits that were successful.

        Divides ``successful_cycles`` by ``total_cycles``.  Returns 0.0 when
        no cycles have been completed, rather than raising ``ZeroDivisionError``.

        Returns
        -------
        float
            Success rate in [0, 1].
        """
        if self.total_cycles == 0:
            return 0.0
        return _clamp(self.successful_cycles / self.total_cycles, 0.0, 1.0)

    # ------------------------------------------------------------------
    def obstruction_rate(self) -> float:
        """Return the mean number of obstructions encountered per orbit.

        Divides ``total_obstructions`` by ``total_cycles``.  Returns 0.0 when
        no cycles have been completed.  Note that this is a *rate* (obstructions
        per cycle), not a fraction, and may therefore exceed 1.0.

        Returns
        -------
        float
            Mean obstructions per orbit.  Non-negative.
        """
        if self.total_cycles == 0:
            return 0.0
        return max(0.0, self.total_obstructions / self.total_cycles)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this metrics snapshot to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary containing all fields plus the
            computed ``success_rate`` and ``obstruction_rate`` values.
        """
        return {
            "total_cycles": self.total_cycles,
            "successful_cycles": self.successful_cycles,
            "mean_cycle_duration": self.mean_cycle_duration,
            "mean_trust_score": self.mean_trust_score,
            "total_obstructions": self.total_obstructions,
            "phase_visit_counts": dict(self.phase_visit_counts),
            "phase_mean_durations": dict(self.phase_mean_durations),
            "success_rate": self.success_rate(),
            "obstruction_rate": self.obstruction_rate(),
        }

    # ------------------------------------------------------------------
    def merge(self, other: "CycleMetrics") -> "CycleMetrics":
        """Produce a new ``CycleMetrics`` that is the union of *self* and *other*.

        Adds the counts and re-derives means arithmetically from the combined
        totals.  Phase visit counts and duration sums are merged element-wise
        across all canonical phases.  This operation is commutative and
        associative, making it suitable for use in distributed aggregation
        pipelines (e.g., aggregating per-node metrics into a global summary).

        Parameters
        ----------
        other:
            The other ``CycleMetrics`` instance to merge with.

        Returns
        -------
        CycleMetrics
            A new instance representing the combined metrics.
        """
        combined_total = self.total_cycles + other.total_cycles
        combined_successful = self.successful_cycles + other.successful_cycles
        combined_obs = self.total_obstructions + other.total_obstructions

        self_total_dur = self.mean_cycle_duration * self.total_cycles
        other_total_dur = other.mean_cycle_duration * other.total_cycles
        new_mean_dur = (
            (self_total_dur + other_total_dur) / combined_total
            if combined_total > 0
            else 0.0
        )

        self_total_trust = self.mean_trust_score * self.total_cycles
        other_total_trust = other.mean_trust_score * other.total_cycles
        new_mean_trust = (
            (self_total_trust + other_total_trust) / combined_total
            if combined_total > 0
            else 0.0
        )

        new_visit_counts: dict = {}
        new_mean_durations: dict = {}
        for ph in _PHASE_ORDER:
            sc = self.phase_visit_counts.get(ph, 0)
            oc = other.phase_visit_counts.get(ph, 0)
            combined_count = sc + oc
            new_visit_counts[ph] = combined_count
            self_sum = self.phase_mean_durations.get(ph, 0.0) * sc
            other_sum = other.phase_mean_durations.get(ph, 0.0) * oc
            new_mean_durations[ph] = (
                (self_sum + other_sum) / combined_count if combined_count > 0 else 0.0
            )

        return CycleMetrics(
            total_cycles=combined_total,
            successful_cycles=combined_successful,
            mean_cycle_duration=new_mean_dur,
            mean_trust_score=new_mean_trust,
            total_obstructions=combined_obs,
            phase_visit_counts=new_visit_counts,
            phase_mean_durations=new_mean_durations,
        )


# ---------------------------------------------------------------------------
# CycleObstruction
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CycleObstruction:
    """Represents an obstruction encountered during a cycle phase.

    An obstruction is any event that prevents a phase from completing normally.
    Obstructions are first-class objects in the cyclic system because the
    OBSTRUCTION_ANALYSIS phase exists specifically to classify and resolve them.
    By making obstructions explicit, the system can learn from its failures and
    refine its generation strategy in future cycles — a capability that is
    structurally impossible in a pure pipeline.

    Attributes
    ----------
    obstruction_id : str
        Unique identifier for this obstruction instance.
    phase : str
        The name of the phase in which the obstruction occurred.
    description : str
        Human-readable description of the obstruction.
    severity : float
        Severity score in [0, 1].  A severity of 1.0 is a critical blocking
        obstruction that should halt the current phase; 0.0 is a minor
        informational notice that does not require immediate resolution.
    blocking : bool
        Whether this obstruction prevents the phase from completing.  A
        non-blocking obstruction is logged but does not cause a phase abort.
    resolution : Optional[str]
        Prose description of how the obstruction was resolved, or ``None``
        if it has not yet been resolved.
    timestamp : str
        ISO-8601 UTC timestamp when the obstruction was first observed.
    """

    obstruction_id: str
    phase: str
    description: str
    severity: float
    blocking: bool
    resolution: Optional[str]
    timestamp: str

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        phase: str,
        description: str,
        severity: float = 0.5,
        blocking: bool = False,
    ) -> "CycleObstruction":
        """Factory method for ``CycleObstruction``.

        Generates a fresh ``obstruction_id`` and records the current UTC
        timestamp.  The severity is clamped to [0, 1] on construction.

        Parameters
        ----------
        phase:
            The phase in which the obstruction occurred.
        description:
            Human-readable description of the obstruction.
        severity:
            Initial severity score.  Clamped to [0, 1].  Defaults to 0.5.
        blocking:
            Whether this obstruction prevents normal phase completion.
            Defaults to ``False``.

        Returns
        -------
        CycleObstruction
            A freshly created obstruction with no resolution.
        """
        return cls(
            obstruction_id=_uid(),
            phase=phase,
            description=description,
            severity=_clamp(severity, 0.0, 1.0),
            blocking=blocking,
            resolution=None,
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    def is_critical(self) -> bool:
        """Return whether this obstruction is critical.

        An obstruction is critical if it is both blocking and has a severity
        of at least 0.8.  Critical obstructions must be resolved before the
        cycle can continue; non-critical blocking obstructions may be deferred
        if the OBSTRUCTION_ANALYSIS phase determines a workaround is available.

        Returns
        -------
        bool
            ``True`` if both ``blocking`` is ``True`` and
            ``severity >= 0.8``.
        """
        return self.blocking and self.severity >= 0.8

    # ------------------------------------------------------------------
    def resolve(self, resolution: str) -> None:
        """Record the resolution of this obstruction.

        Sets the ``resolution`` field to *resolution* and marks the obstruction
        as non-blocking.  Mutates the obstruction in place.  After resolution,
        ``is_critical()`` will return ``False`` because ``blocking`` is now
        ``False``.

        Parameters
        ----------
        resolution:
            A human-readable description of how the obstruction was resolved.
            Should be concise but informative enough that a future IDEATION
            phase can use it as a negative example.
        """
        self.resolution = resolution
        self.blocking = False

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this obstruction to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary with all obstruction fields plus a
            boolean ``is_critical`` key and a boolean ``is_resolved`` key for
            convenience.
        """
        return {
            "obstruction_id": self.obstruction_id,
            "phase": self.phase,
            "description": self.description,
            "severity": self.severity,
            "blocking": self.blocking,
            "resolution": self.resolution,
            "timestamp": self.timestamp,
            "is_critical": self.is_critical(),
            "is_resolved": self.resolution is not None,
        }


# ---------------------------------------------------------------------------
# CyclicSystemAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CyclicSystemAnalyzer:
    """Analyses whether a system exhibits cyclic (vs pipeline-linear) behaviour.

    The analyzer examines a system's state and execution history to produce a
    quantitative assessment of how cyclic the system is.  It scores the system
    on two complementary dimensions:

    * **cycle_score**: the degree to which the system's execution history shows
      evidence of genuine feedback loops — i.e., outputs of later phases
      influencing earlier phases in subsequent iterations.
    * **pipeline_score**: the degree to which the system's execution history
      shows signs of linear, non-recurrent execution — i.e., each logical
      "unit of work" passes through the phases exactly once and is never
      revisited.

    A mature system should have a high ``cycle_score`` and a low
    ``pipeline_score``.  The difference (cycle_score - pipeline_score) is used
    as the primary cyclicity metric.

    Attributes
    ----------
    system_id : str
        The identifier of the system being analysed.
    config : dict
        Configuration dictionary.  Recognised keys: ``'min_cycle_evidence'``
        (int, default 3 — minimum number of completed orbits required before a
        positive cyclicity verdict is issued) and ``'linearity_threshold'``
        (float, default 0.6 — pipeline_score above which the system is flagged
        as possibly linear).
    """

    system_id: str
    config: dict

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls, system_id: str, config: dict | None = None
    ) -> "CyclicSystemAnalyzer":
        """Factory method for ``CyclicSystemAnalyzer``.

        Parameters
        ----------
        system_id:
            Identifier of the system to analyse.
        config:
            Optional configuration overrides.  Missing keys receive defaults.

        Returns
        -------
        CyclicSystemAnalyzer
            A freshly created analyser instance.
        """
        default_config = {
            "min_cycle_evidence": 3,
            "linearity_threshold": 0.6,
        }
        if config:
            default_config.update(config)
        return cls(system_id=system_id, config=default_config)

    # ------------------------------------------------------------------
    def analyze(self, system_state: dict) -> dict:
        """Analyse the given system state and return a cyclicity verdict.

        Examines ``system_state`` for indicators of cyclic vs. pipeline
        behaviour.  The indicators checked are:

        * Whether ``system_state`` contains a ``'cycle_history'`` key with at
          least ``config['min_cycle_evidence']`` entries (positive signal for
          cyclicity).
        * Whether ``system_state`` contains a ``'pipeline_runs'`` key (positive
          signal for linearity).
        * Whether ``system_state`` contains feedback-related keys such as
          ``'obstruction_log'`` or ``'synthesis_context'`` (positive signal for
          cyclicity).

        Parameters
        ----------
        system_state:
            A dictionary describing the current observable state of the system.
            Expected keys (all optional): ``'cycle_history'``, ``'pipeline_runs'``,
            ``'obstruction_log'``, ``'synthesis_context'``, ``'trust_score'``.

        Returns
        -------
        dict
            Analysis result with keys:

            * ``is_cyclic`` (bool) — overall verdict.
            * ``pipeline_score`` (float in [0, 1]) — degree of pipeline linearity.
            * ``cycle_score`` (float in [0, 1]) — degree of genuine cyclicity.
            * ``evidence`` (list[str]) — human-readable evidence items.
            * ``recommendation`` (str) — actionable recommendation.
        """
        evidence = []
        cycle_signals = 0.0
        pipeline_signals = 0.0

        cycle_history = system_state.get("cycle_history", [])
        min_evidence = self.config.get("min_cycle_evidence", 3)
        if len(cycle_history) >= min_evidence:
            cycle_signals += 1.0
            evidence.append(
                f"cycle_history contains {len(cycle_history)} entries "
                f"(≥ min_evidence={min_evidence}): strong cyclic signal."
            )
        elif cycle_history:
            cycle_signals += 0.4
            evidence.append(
                f"cycle_history contains {len(cycle_history)} entries "
                f"(< min_evidence={min_evidence}): weak cyclic signal."
            )
        else:
            evidence.append("cycle_history is absent or empty: no cyclic signal.")

        if "pipeline_runs" in system_state:
            runs = system_state["pipeline_runs"]
            pipeline_signals += 1.0
            evidence.append(
                f"pipeline_runs key present with {len(runs)} entries: "
                "linear pipeline signal."
            )

        if "obstruction_log" in system_state:
            obs_log = system_state["obstruction_log"]
            if obs_log:
                cycle_signals += 0.5
                evidence.append(
                    f"obstruction_log has {len(obs_log)} entries: "
                    "suggests OBSTRUCTION_ANALYSIS phase is active."
                )

        if "synthesis_context" in system_state:
            cycle_signals += 0.5
            evidence.append(
                "synthesis_context key present: suggests SYNTHESIS → IDEATION "
                "feedback loop is active."
            )

        trust = system_state.get("trust_score", None)
        if trust is not None and float(trust) > 0.7:
            cycle_signals += 0.3
            evidence.append(
                f"trust_score={trust:.3f} > 0.7: consistent with repeated "
                "successful cycles."
            )

        total_signals = cycle_signals + pipeline_signals
        if total_signals == 0.0:
            cycle_score = 0.0
            pipeline_score = 0.0
        else:
            cycle_score = _clamp(cycle_signals / (total_signals + 1.0), 0.0, 1.0)
            pipeline_score = _clamp(pipeline_signals / (total_signals + 1.0), 0.0, 1.0)

        is_cyclic = cycle_score > pipeline_score and len(cycle_history) >= min_evidence
        threshold = self.config.get("linearity_threshold", 0.6)
        if pipeline_score >= threshold:
            recommendation = (
                "WARNING: pipeline_score exceeds linearity_threshold. "
                "Consider refactoring the system to include explicit feedback "
                "arcs from SYNTHESIS back to IDEATION."
            )
        elif is_cyclic:
            recommendation = (
                "System exhibits cyclic behaviour. "
                "Continue monitoring obstruction_rate and trust_score convergence."
            )
        else:
            recommendation = (
                "Insufficient evidence of cyclicity. "
                "Ensure the coordinator is completing full orbits and that "
                "synthesis_context is being fed back to the ideation phase."
            )

        return {
            "is_cyclic": is_cyclic,
            "pipeline_score": pipeline_score,
            "cycle_score": cycle_score,
            "evidence": evidence,
            "recommendation": recommendation,
        }

    # ------------------------------------------------------------------
    def detect_linearity_breaks(self, history: list) -> list:
        """Find places in an execution history where cycle feedback is broken.

        Iterates over *history* looking for consecutive pairs of records where
        the SYNTHESIS phase was completed but the following record does not
        start from IDEATION — which would indicate that the feedback arc was
        cut and the system restarted from a later phase (a pipeline symptom).

        Parameters
        ----------
        history:
            A list of ``CycleRecord.to_dict()`` dictionaries in execution order.

        Returns
        -------
        list[dict]
            A list of break reports, each with keys ``'index'``, ``'record_id'``,
            ``'expected_first_phase'``, ``'actual_first_phase'``, and
            ``'description'``.  An empty list means no linearity breaks were
            detected.
        """
        breaks = []
        for i, rec in enumerate(history):
            phases = rec.get("phases_completed", [])
            if not phases:
                continue
            first_phase = phases[0]
            if first_phase != _PHASE_IDEATION:
                breaks.append(
                    {
                        "index": i,
                        "record_id": rec.get("record_id", ""),
                        "expected_first_phase": _PHASE_IDEATION,
                        "actual_first_phase": first_phase,
                        "description": (
                            f"Cycle at index {i} started from {first_phase!r} "
                            f"instead of {_PHASE_IDEATION!r}. "
                            "This suggests the feedback arc from SYNTHESIS was cut."
                        ),
                    }
                )
        return breaks

    # ------------------------------------------------------------------
    def score_cyclicity(self, metrics: "CycleMetrics") -> float:
        """Compute a scalar cyclicity score from aggregated ``CycleMetrics``.

        Uses three weighted components:

        1. **success_rate** (weight 0.4): a high success rate implies the cycle
           is completing full orbits reliably.
        2. **mean_trust_score** (weight 0.4): a high mean trust score implies
           the VERIFICATION phase is consistently approving candidates, which
           is only sustainable if the OBSTRUCTION_ANALYSIS → IDEATION feedback
           loop is functioning.
        3. **coverage** (weight 0.2): the fraction of canonical phases that
           have been visited at least once, normalised by the total number of
           canonical phases.

        Parameters
        ----------
        metrics:
            An aggregated ``CycleMetrics`` instance.

        Returns
        -------
        float
            Cyclicity score in [0, 1].  Higher is better.
        """
        success_component = metrics.success_rate() * 0.4
        trust_component = _clamp(metrics.mean_trust_score, 0.0, 1.0) * 0.4
        visited = sum(
            1 for ph in _PHASE_ORDER if metrics.phase_visit_counts.get(ph, 0) > 0
        )
        coverage_component = (visited / len(_PHASE_ORDER)) * 0.2
        return _clamp(success_component + trust_component + coverage_component, 0.0, 1.0)

    # ------------------------------------------------------------------
    def generate_report(self, analysis: dict) -> str:
        """Render a human-readable report from the output of ``analyze()``.

        Parameters
        ----------
        analysis:
            The dictionary returned by ``analyze()``.

        Returns
        -------
        str
            A multi-line plain-text report suitable for printing to a terminal
            or logging at INFO level.
        """
        lines = [
            "=" * 60,
            f"Cyclic System Analysis Report — system_id={self.system_id!r}",
            "=" * 60,
            f"  is_cyclic      : {analysis.get('is_cyclic')}",
            f"  cycle_score    : {analysis.get('cycle_score', 0.0):.4f}",
            f"  pipeline_score : {analysis.get('pipeline_score', 0.0):.4f}",
            "",
            "Evidence:",
        ]
        for item in analysis.get("evidence", []):
            lines.append(f"  • {item}")
        lines.append("")
        lines.append(f"Recommendation: {analysis.get('recommendation', '')}")
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def compare_architectures(self, cyclic: dict, pipeline: dict) -> dict:
        """Produce a side-by-side comparison of a cyclic and a pipeline system.

        Runs ``analyze()`` on both *cyclic* and *pipeline* system state dicts
        and summarises their differences on the key dimensions of cyclicity.

        Parameters
        ----------
        cyclic:
            System state dict for the system claimed to be cyclic.
        pipeline:
            System state dict for the system claimed to be a pipeline.

        Returns
        -------
        dict
            Comparison result with keys ``'cyclic_analysis'``,
            ``'pipeline_analysis'``, ``'cycle_score_delta'`` (cyclic minus
            pipeline), ``'verdict'`` (str), and ``'summary'`` (str).
        """
        cyclic_analysis = self.analyze(cyclic)
        pipeline_analysis = self.analyze(pipeline)
        delta = cyclic_analysis["cycle_score"] - pipeline_analysis["cycle_score"]
        if delta > 0.1:
            verdict = "cyclic_system_is_better"
            summary = (
                f"The cyclic system scores {delta:.4f} higher on cycle_score. "
                "It exhibits genuine feedback and convergence behaviour."
            )
        elif delta < -0.1:
            verdict = "pipeline_system_is_surprisingly_more_cyclic"
            summary = (
                f"The pipeline system scores {abs(delta):.4f} higher on cycle_score. "
                "This may indicate the cyclic system is misconfigured."
            )
        else:
            verdict = "systems_are_comparable"
            summary = (
                f"The two systems have similar cycle scores (delta={delta:.4f}). "
                "More execution history is needed to discriminate them."
            )
        return {
            "cyclic_analysis": cyclic_analysis,
            "pipeline_analysis": pipeline_analysis,
            "cycle_score_delta": delta,
            "verdict": verdict,
            "summary": summary,
        }


# ---------------------------------------------------------------------------
# CyclicSystemWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CyclicSystemWitness:
    """Cryptographic/logical proof object certifying that the system is cyclic.

    The ``CyclicSystemWitness`` accumulates attestations — one per phase visit
    — and uses them to build a proof chain demonstrating that the system has
    completed at least one valid orbit.  In the language of Ch65, §4.3, this
    object is the constructive witness *W* that satisfies the hypothesis of the
    cyclic soundness theorem: the existence of *W* guarantees that the theorem's
    conclusion (convergence to trust score 1) applies.

    Attestations are stored as dictionaries with a stable schema and are
    addressable by their ``attestation_id`` string.  The proof chain orders
    them by phase index within each orbit.

    Attributes
    ----------
    witness_id : str
        Unique identifier for this witness object.
    system_id : str
        The identifier of the system being witnessed.
    attestations : list[dict]
        Append-only list of attestation records.  Each attestation has keys
        ``attestation_id``, ``phase``, ``phase_index``, ``evidence``,
        ``orbit_index``, and ``timestamp``.
    """

    witness_id: str
    system_id: str
    attestations: list

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, witness_id: str, system_id: str) -> "CyclicSystemWitness":
        """Factory method for ``CyclicSystemWitness``.

        Parameters
        ----------
        witness_id:
            Unique identifier to assign to this witness object.  If an empty
            string is passed, a fresh identifier is generated via ``_uid()``.
        system_id:
            Identifier of the system being witnessed.

        Returns
        -------
        CyclicSystemWitness
            A fresh witness with an empty attestation list.
        """
        return cls(
            witness_id=witness_id or _uid(),
            system_id=system_id,
            attestations=[],
        )

    # ------------------------------------------------------------------
    def attest_cycle_phase(self, phase: str, evidence: dict) -> str:
        """Record an attestation that the system visited a specific cycle phase.

        Each call to this method adds one attestation to ``self.attestations``.
        The orbit index is derived from the number of SYNTHESIS attestations
        already present (since each orbit ends with SYNTHESIS).  The phase
        index is looked up from the canonical order.

        Parameters
        ----------
        phase:
            The name of the phase that was visited.  Should be one of the five
            canonical phase names.
        evidence:
            A dict of supporting evidence (e.g., artefact IDs, trust scores,
            duration measurements) that corroborates the visit.

        Returns
        -------
        str
            The ``attestation_id`` of the newly created attestation.  Store
            this to later call ``verify_attestation``.
        """
        phase_index = _PHASE_ORDER.index(phase) if phase in _PHASE_ORDER else -1
        orbit_index = sum(
            1 for a in self.attestations if a.get("phase") == _PHASE_SYNTHESIS
        )
        attestation_id = _uid()
        record = {
            "attestation_id": attestation_id,
            "phase": phase,
            "phase_index": phase_index,
            "evidence": dict(evidence),
            "orbit_index": orbit_index,
            "timestamp": _utcnow(),
        }
        self.attestations.append(record)
        return attestation_id

    # ------------------------------------------------------------------
    def verify_attestation(self, attestation_id: str) -> bool:
        """Verify that a specific attestation exists in this witness.

        Performs a linear scan over ``self.attestations`` looking for a record
        whose ``attestation_id`` matches the argument.  Returns ``True`` if
        found, ``False`` otherwise.  In a production system this would also
        check a cryptographic hash; here we use identity lookup as a
        stand-in.

        Parameters
        ----------
        attestation_id:
            The identifier string returned by ``attest_cycle_phase``.

        Returns
        -------
        bool
            ``True`` if the attestation is present in this witness,
            ``False`` otherwise.
        """
        for att in self.attestations:
            if att.get("attestation_id") == attestation_id:
                return True
        return False

    # ------------------------------------------------------------------
    def build_proof_chain(self) -> list:
        """Return attestations sorted into a proof chain ordered by orbit then phase.

        The proof chain is the ordered sequence of attestations that together
        constitute constructive evidence of cyclic execution.  Attestations are
        sorted first by ``orbit_index`` (ascending) and then by ``phase_index``
        (ascending), so that each orbit's phases appear in canonical order.

        Returns
        -------
        list[dict]
            The sorted list of attestation records.  Each record has keys
            ``attestation_id``, ``phase``, ``phase_index``, ``evidence``,
            ``orbit_index``, and ``timestamp``.
        """
        return sorted(
            self.attestations,
            key=lambda a: (a.get("orbit_index", 0), a.get("phase_index", 0)),
        )

    # ------------------------------------------------------------------
    def is_valid(self) -> bool:
        """Return whether all attestations form at least one valid complete cycle.

        A valid complete cycle requires that all five canonical phases appear
        in at least one orbit.  This method checks whether orbit 0 (the first
        orbit) contains attestations for every phase in the canonical order.
        If so, the proof chain is valid and the witness satisfies the theorem's
        hypothesis.

        Returns
        -------
        bool
            ``True`` if orbit 0 contains all five canonical phase attestations.
        """
        orbit_zero_phases = {
            a["phase"]
            for a in self.attestations
            if a.get("orbit_index", -1) == 0
        }
        return all(ph in orbit_zero_phases for ph in _PHASE_ORDER)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this witness to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary with keys ``witness_id``,
            ``system_id``, ``attestation_count``, ``is_valid``, and
            ``proof_chain`` (the sorted attestation list).
        """
        return {
            "witness_id": self.witness_id,
            "system_id": self.system_id,
            "attestation_count": len(self.attestations),
            "is_valid": self.is_valid(),
            "proof_chain": self.build_proof_chain(),
        }


# ---------------------------------------------------------------------------
# CyclicSystemCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CyclicSystemCoordinator:
    """Main coordinator that manages cycle execution and phase transitions.

    The ``CyclicSystemCoordinator`` is the top-level runtime object for the
    cyclic architecture.  It maintains a list of completed ``CycleRecord``
    objects, a ``CyclicSystemWitness`` that accumulates phase attestations,
    and a current-phase pointer.  Callers drive the coordinator forward by
    calling ``start_cycle``, ``advance_phase``, and ``complete_cycle`` (or the
    convenience wrapper ``run_full_cycle`` that invokes all three).

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    config : dict
        Configuration dictionary.  Recognised keys: ``'simulate_duration'``
        (bool, default True — whether ``advance_phase`` should insert a small
        simulated phase duration) and ``'default_trust_delta'`` (float,
        default 0.1 — amount by which the trust score grows per successful
        phase).
    completed_records : list[CycleRecord]
        Append-only list of all completed orbits.
    _witness : CyclicSystemWitness
        The running proof witness object.
    _current_phase : CyclePhase
        The phase the coordinator is currently in.
    _cycle_counter : int
        Total number of orbits started (including in-progress ones).
    """

    coordinator_id: str
    config: dict
    completed_records: list
    _witness: "CyclicSystemWitness"
    _current_phase: "CyclePhase"
    _cycle_counter: int

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls, coordinator_id: str = "", config: dict | None = None
    ) -> "CyclicSystemCoordinator":
        """Factory method for ``CyclicSystemCoordinator``.

        Parameters
        ----------
        coordinator_id:
            Optional identifier.  A fresh ``_uid()`` is generated when the
            empty string is passed.
        config:
            Optional configuration overrides.

        Returns
        -------
        CyclicSystemCoordinator
            A fully initialised coordinator at the IDEATION phase with an
            empty history and a fresh witness.
        """
        cid = coordinator_id or _uid()
        default_config = {
            "simulate_duration": True,
            "default_trust_delta": 0.1,
        }
        if config:
            default_config.update(config)
        witness = CyclicSystemWitness.create(witness_id=_uid(), system_id=cid)
        return cls(
            coordinator_id=cid,
            config=default_config,
            completed_records=[],
            _witness=witness,
            _current_phase=CyclePhase.initial(),
            _cycle_counter=0,
        )

    # ------------------------------------------------------------------
    def start_cycle(self, context: dict | None = None) -> "CycleRecord":
        """Open a new cycle orbit and return the initialised ``CycleRecord``.

        Resets the coordinator's current phase to IDEATION, increments the
        cycle counter, and creates a new ``CycleRecord`` with the current
        counter as its ``cycle_index``.  The optional *context* dict is
        stored verbatim in the record's ``metadata`` field so that callers can
        attach traceability information.

        Parameters
        ----------
        context:
            Optional key-value context dict (e.g., goal descriptions, caller
            tags, trigger events).  Stored in ``CycleRecord.metadata``.

        Returns
        -------
        CycleRecord
            The newly opened orbit record.  At this point only ``started_at``
            and ``cycle_index`` are populated; all other fields are at their
            initial defaults.
        """
        self._current_phase = CyclePhase.initial()
        record = CycleRecord.create(
            cycle_index=self._cycle_counter,
            metadata=dict(context) if context else {},
        )
        self._cycle_counter += 1
        return record

    # ------------------------------------------------------------------
    def advance_phase(
        self,
        record: "CycleRecord",
        obstruction: "CycleObstruction | None" = None,
    ) -> "CycleTransition":
        """Advance the coordinator to the next phase and update the record.

        Marks the current phase as completed in *record*, records a simulated
        duration (if ``config['simulate_duration']`` is True), attests the
        phase visit in the witness, increments the trust score by
        ``config['default_trust_delta']`` (unless an obstruction is present),
        and creates and returns a ``CycleTransition``.

        If *obstruction* is non-``None`` and blocking, the trust score is *not*
        incremented and the transition is tagged with the obstruction's ID.
        Non-blocking obstructions increment the obstruction count but do not
        affect the trust score.

        Parameters
        ----------
        record:
            The in-progress ``CycleRecord`` to update.
        obstruction:
            An optional ``CycleObstruction`` that was encountered during the
            current phase.

        Returns
        -------
        CycleTransition
            The transition record for the move from the current phase to the
            next.
        """
        current_name = self._current_phase.name
        simulate = self.config.get("simulate_duration", True)
        duration = 0.01 if simulate else 0.0

        if current_name not in record.phases_completed:
            record.phases_completed.append(current_name)
        record.phase_durations[current_name] = (
            record.phase_durations.get(current_name, 0.0) + duration
        )

        evidence = {
            "duration": duration,
            "obstruction": obstruction.to_dict() if obstruction else None,
        }
        self._witness.attest_cycle_phase(current_name, evidence)

        obstruction_id: Optional[str] = None
        if obstruction is not None:
            record.obstruction_count += 1
            obstruction_id = obstruction.obstruction_id
            if not obstruction.blocking:
                trust_delta = self.config.get("default_trust_delta", 0.1)
                record.trust_score = _clamp(
                    record.trust_score + trust_delta, 0.0, 1.0
                )
            trigger = "obstruction_encountered"
        else:
            trust_delta = self.config.get("default_trust_delta", 0.1)
            record.trust_score = _clamp(record.trust_score + trust_delta, 0.0, 1.0)
            trigger = "phase_complete"

        next_phase = self._current_phase.next_phase()
        transition = CycleTransition.create(
            from_phase=current_name,
            to_phase=next_phase.name,
            trigger=trigger,
            obstruction=obstruction_id,
        )
        self._current_phase = next_phase
        return transition

    # ------------------------------------------------------------------
    def complete_cycle(self, record: "CycleRecord") -> "CycleRecord":
        """Finalise an in-progress ``CycleRecord`` and add it to history.

        Sets ``completed_at`` to the current UTC time, normalises
        ``trust_score`` to [0, 1], and appends the record to
        ``self.completed_records``.  The coordinator's current phase is reset
        to IDEATION in preparation for the next orbit.

        Parameters
        ----------
        record:
            The in-progress record to finalise.  Should have had all five
            phases advanced through ``advance_phase`` before this call.

        Returns
        -------
        CycleRecord
            The finalised record (same object as the input, mutated in place
            and returned for method-chaining convenience).
        """
        record.completed_at = _utcnow()
        record.trust_score = _clamp(record.trust_score, 0.0, 1.0)
        self.completed_records.append(record)
        self._current_phase = CyclePhase.initial()
        return record

    # ------------------------------------------------------------------
    def run_full_cycle(
        self, context: dict | None = None
    ) -> "tuple[CycleRecord, list[CycleTransition]]":
        """Run one complete orbit from IDEATION through SYNTHESIS.

        Convenience method that calls ``start_cycle``, then ``advance_phase``
        for each of the five canonical phases (with no obstructions), then
        ``complete_cycle``.  Returns the completed record and the ordered list
        of transitions produced during the orbit.

        Parameters
        ----------
        context:
            Optional context dict forwarded to ``start_cycle``.

        Returns
        -------
        tuple[CycleRecord, list[CycleTransition]]
            A 2-tuple of the completed ``CycleRecord`` and the five
            ``CycleTransition`` objects produced during the orbit.
        """
        record = self.start_cycle(context=context)
        transitions: list = []
        for _ in _PHASE_ORDER:
            transition = self.advance_phase(record, obstruction=None)
            transitions.append(transition)
            if self._current_phase.name == _PHASE_IDEATION:
                break
        completed = self.complete_cycle(record)
        return completed, transitions

    # ------------------------------------------------------------------
    def get_metrics(self) -> "CycleMetrics":
        """Compute and return aggregated metrics from all completed cycles.

        Delegates to ``CycleMetrics.from_records``, passing the list of
        completed ``CycleRecord`` objects (converted to dicts).

        Returns
        -------
        CycleMetrics
            A metrics snapshot aggregated over all completed orbits.  If no
            orbits have completed yet, all numeric fields will be 0.0 or 0.
        """
        record_dicts = [r.to_dict() for r in self.completed_records]
        return CycleMetrics.from_records(record_dicts)

    # ------------------------------------------------------------------
    def get_witness(self) -> "CyclicSystemWitness":
        """Return the current proof witness accumulated by the coordinator.

        The witness grows with every call to ``advance_phase`` and provides
        cryptographic/logical evidence that the system has visited cycle phases
        in the correct order.

        Returns
        -------
        CyclicSystemWitness
            The running witness object, including all attestations gathered
            since the coordinator was created (or last reset).
        """
        return self._witness

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Reset the coordinator to its initial state.

        Clears ``completed_records``, resets ``_cycle_counter`` to 0, resets
        ``_current_phase`` to IDEATION, and creates a fresh ``_witness``.
        This method is intended for use in test harnesses and benchmarks where
        a clean slate is needed between runs.
        """
        self.completed_records.clear()
        self._cycle_counter = 0
        self._current_phase = CyclePhase.initial()
        self._witness = CyclicSystemWitness.create(
            witness_id=_uid(), system_id=self.coordinator_id
        )


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def run_cycle(
    coordinator_id: str = "", context: dict | None = None
) -> "CycleRecord":
    """Create a temporary coordinator and run one full orbit, returning the record.

    This is a convenience function for scripting and integration tests.  It
    creates a ``CyclicSystemCoordinator`` with the given ID, runs a single
    full orbit via ``run_full_cycle``, and returns the completed ``CycleRecord``.
    The coordinator is discarded after the call.

    Parameters
    ----------
    coordinator_id:
        Optional identifier for the ephemeral coordinator.  A fresh ``_uid()``
        is used when the empty string is passed.
    context:
        Optional context dict forwarded to the coordinator's ``start_cycle``.

    Returns
    -------
    CycleRecord
        The completed record from the single orbit.
    """
    coordinator = CyclicSystemCoordinator.create(
        coordinator_id=coordinator_id or _uid()
    )
    record, _transitions = coordinator.run_full_cycle(context=context)
    return record


def analyze_system_cyclicity(system_state: dict) -> dict:
    """Analyse a system state dict and return a cyclicity verdict.

    Creates a temporary ``CyclicSystemAnalyzer`` and runs ``analyze()`` on
    *system_state*.  Suitable for one-shot usage in scripts and REPL sessions
    where constructing an analyser object explicitly would be verbose.

    Parameters
    ----------
    system_state:
        A dictionary describing the observable state of the system to analyse.
        See ``CyclicSystemAnalyzer.analyze`` for expected keys.

    Returns
    -------
    dict
        The analysis result dict with keys ``is_cyclic``, ``pipeline_score``,
        ``cycle_score``, ``evidence``, and ``recommendation``.
    """
    analyzer = CyclicSystemAnalyzer.create(system_id=_uid())
    return analyzer.analyze(system_state)


def build_cycle_witness(
    system_id: str, records: list
) -> "CyclicSystemWitness":
    """Build a ``CyclicSystemWitness`` from a list of completed ``CycleRecord`` objects.

    Iterates over *records* in order and adds one attestation per completed
    phase in each orbit.  The resulting witness can then be inspected via
    ``is_valid()`` and ``build_proof_chain()`` to verify that at least one
    complete orbit was executed.

    Parameters
    ----------
    system_id:
        The identifier of the system being witnessed.
    records:
        List of ``CycleRecord`` instances (or dicts) in execution order.

    Returns
    -------
    CyclicSystemWitness
        A populated witness object.  Its ``is_valid()`` method will return
        ``True`` if the first record in *records* contains all five canonical
        phases in ``phases_completed``.
    """
    witness = CyclicSystemWitness.create(witness_id=_uid(), system_id=system_id)
    for rec in records:
        if isinstance(rec, CycleRecord):
            rec_dict = rec.to_dict()
        elif isinstance(rec, dict):
            rec_dict = rec
        else:
            try:
                rec_dict = vars(rec)
            except TypeError:
                rec_dict = {}
        phases = rec_dict.get("phases_completed", [])
        phase_durations = rec_dict.get("phase_durations", {})
        trust = rec_dict.get("trust_score", 0.0)
        for phase in phases:
            evidence = {
                "duration": phase_durations.get(phase, 0.0),
                "trust_score": trust,
                "record_id": rec_dict.get("record_id", ""),
            }
            witness.attest_cycle_phase(phase, evidence)
    return witness


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Smoke test: the_system_should_be_cyclic_not_pi")
    print("=" * 60)

    # ---- CyclePhase round-trip ----------------------------------------
    phase = CyclePhase.initial()
    assert phase.name == _PHASE_IDEATION, "Initial phase should be IDEATION"
    for expected in _PHASE_ORDER[1:] + [_PHASE_IDEATION]:
        phase = phase.next_phase()
        assert phase.name == expected, f"Expected {expected!r}, got {phase.name!r}"
    print("[PASS] CyclePhase next_phase() wraps correctly through all 5 phases")

    # ---- CycleObstruction lifecycle ------------------------------------
    obs = CycleObstruction.create(
        phase=_PHASE_VERIFICATION,
        description="Candidate failed soundness check",
        severity=0.9,
        blocking=True,
    )
    assert obs.is_critical(), "Severity=0.9 blocking obstruction should be critical"
    obs.resolve("Relaxed soundness constraint to allow partial verification")
    assert not obs.is_critical(), "Resolved obstruction should not be critical"
    assert obs.resolution is not None, "Resolution should be set after resolve()"
    print("[PASS] CycleObstruction create / is_critical / resolve lifecycle")

    # ---- CyclicSystemCoordinator full orbit ----------------------------
    coordinator = CyclicSystemCoordinator.create(coordinator_id="smoke-test-coord")
    record, transitions = coordinator.run_full_cycle(context={"goal": "smoke_test"})
    assert record.is_successful(), "Full orbit with no obstructions should be successful"
    assert len(transitions) == 5, f"Expected 5 transitions, got {len(transitions)}"
    assert len(record.phases_completed) == 5, "All 5 phases should be completed"
    print(f"[PASS] Full orbit completed: trust_score={record.trust_score:.4f}, "
          f"duration={record.total_duration():.4f}s")

    # ---- CycleMetrics aggregation -------------------------------------
    for _ in range(4):
        coordinator.run_full_cycle()
    metrics = coordinator.get_metrics()
    assert metrics.total_cycles == 5, f"Expected 5 total cycles, got {metrics.total_cycles}"
    assert metrics.success_rate() == 1.0, f"Expected 100% success rate, got {metrics.success_rate()}"
    print(f"[PASS] CycleMetrics: total={metrics.total_cycles}, "
          f"success_rate={metrics.success_rate():.2f}, "
          f"mean_trust={metrics.mean_trust_score:.4f}")

    # ---- CyclicSystemWitness ------------------------------------------
    witness = coordinator.get_witness()
    assert witness.is_valid(), "Witness should be valid after 5 complete orbits"
    proof_chain = witness.build_proof_chain()
    assert len(proof_chain) == 25, (
        f"Expected 25 attestations (5 orbits × 5 phases), got {len(proof_chain)}"
    )
    print(f"[PASS] Witness is_valid=True, proof_chain length={len(proof_chain)}")

    # ---- CyclicSystemAnalyzer -----------------------------------------
    state = {
        "cycle_history": [r.to_dict() for r in coordinator.completed_records],
        "obstruction_log": [],
        "synthesis_context": {"enriched": True},
        "trust_score": metrics.mean_trust_score,
    }
    analyzer = CyclicSystemAnalyzer.create(system_id="smoke-test-system")
    analysis = analyzer.analyze(state)
    assert analysis["is_cyclic"], "Smoke test system should be detected as cyclic"
    print(f"[PASS] CyclicSystemAnalyzer: is_cyclic={analysis['is_cyclic']}, "
          f"cycle_score={analysis['cycle_score']:.4f}")
    report = analyzer.generate_report(analysis)
    assert "Recommendation" in report, "Report should contain Recommendation section"
    print("[PASS] generate_report produced non-empty report")

    # ---- build_cycle_witness free function ----------------------------
    built_witness = build_cycle_witness(
        system_id="smoke-test-system",
        records=coordinator.completed_records,
    )
    assert built_witness.is_valid(), "Built witness should be valid"
    print("[PASS] build_cycle_witness produced a valid witness")

    # ---- run_cycle free function --------------------------------------
    quick_record = run_cycle(coordinator_id="quick-smoke", context={"run": "quick"})
    assert quick_record.is_successful(), "run_cycle() should return a successful record"
    print(f"[PASS] run_cycle(): record_id={quick_record.record_id}, "
          f"is_successful={quick_record.is_successful()}")

    # ---- analyze_system_cyclicity free function -----------------------
    quick_analysis = analyze_system_cyclicity(state)
    assert "is_cyclic" in quick_analysis, "analyze_system_cyclicity should return is_cyclic key"
    print(f"[PASS] analyze_system_cyclicity(): is_cyclic={quick_analysis['is_cyclic']}")

    # ---- CycleMetrics merge -------------------------------------------
    metrics_a = coordinator.get_metrics()
    coordinator2 = CyclicSystemCoordinator.create(coordinator_id="coord-b")
    for _ in range(3):
        coordinator2.run_full_cycle()
    metrics_b = coordinator2.get_metrics()
    merged = metrics_a.merge(metrics_b)
    assert merged.total_cycles == metrics_a.total_cycles + metrics_b.total_cycles, (
        "Merged total_cycles should equal sum of both"
    )
    print(f"[PASS] CycleMetrics.merge(): merged total_cycles={merged.total_cycles}")

    print("=" * 60)
    print("All smoke tests passed.")
    print("=" * 60)
