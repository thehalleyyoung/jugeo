"""
Core data models for the methodology_loops evaluation package.

This module defines the canonical data structures used throughout the
methodology-loop evaluation pipeline in JuGeo.  A *methodology loop* is a
structured, multi-phase process that drives a formal specification from an
initial idea through formalization, implementation, falsification, and
revision cycles until convergence (or declared failure).

Each loop instance is self-contained: it carries its own configuration
(:class:`MethodologyConfig`), live state machine (:class:`LoopState`),
transition log (:class:`LoopTransition`), and diagnostic telemetry
(:class:`LoopDiagnostics`).  Specialised loop sub-types
(:class:`FormalizationLoop`, :class:`ImplementationLoop`,
:class:`FalsificationLoop`) extend the base :class:`MethodologyLoop` with
phase-specific bookkeeping.

copilot: shared-core marker
Theory reference: theory2.tex Ch62
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    # Enums
    "LoopPhase",
    "LoopStatus",
    "TransitionKind",
    # Core dataclasses
    "LoopDiagnostics",
    "MethodologyConfig",
    "LoopState",
    "LoopTransition",
    "MethodologyLoop",
    # Specialised loop types
    "FormalizationLoop",
    "ImplementationLoop",
    "FalsificationLoop",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp (float seconds).

    This thin wrapper exists so that tests can monkeypatch time without
    reaching into the standard library directly.

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC).
    """
    return time.time()


def _uid() -> str:
    """Generate a universally unique identifier (UUID4) as a plain string.

    Each call produces a cryptographically random 128-bit value formatted in
    the canonical xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx hex representation.

    Returns
    -------
    str
        A new UUID4 string, e.g. ``"3d7f2a1b-0c4e-4f9a-8b3d-1e2f5a6c7d8e"``.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The raw floating-point number to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``lo`` if ``value < lo``, ``hi`` if ``value > hi``, else ``value``.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.3, 0.0, 1.0)
    0.0
    >>> _clamp(0.7, 0.0, 1.0)
    0.7
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class LoopPhase(str, Enum):
    """The discrete phase that a :class:`MethodologyLoop` currently occupies.

    The five phases form the canonical JuGeo methodology cycle as described in
    theory2.tex Ch62.  Progress through them is not strictly linear; a loop
    may regress from EVALUATION back to FORMALIZATION when a fundamental
    inconsistency is detected, or branch to spawn a parallel sub-loop.

    Members
    -------
    FORMALIZATION
        The specification is being written in a formal language (e.g.
        first-order logic, type theory, or a domain-specific notation).
        Artefacts produced here include axiom sets, type signatures, and
        structured glossaries.  The loop exits FORMALIZATION when a
        consistency check passes and coverage reaches the configured
        ``min_coverage`` threshold.

    IMPLEMENTATION
        A mechanised or semi-mechanised artefact (code, proof assistant
        script, model) is constructed so that its observable behaviour
        satisfies the formal specification.  The loop tracks build status
        and test-coverage metrics during this phase.

    EVALUATION
        The implementation is exercised against the specification.  This
        phase accumulates evaluation evidence: benchmarks, property-based
        tests, expert reviews, and automatic checkers.  The outcome is a
        structured verdict — PASS, CONDITIONAL, or FAIL — together with
        a numerical coverage score.

    FALSIFICATION
        Adversarial attempts to disprove the specification's core
        hypotheses are made (counter-example search, fuzzing, model
        checking).  Each attempt either reinforces confidence or produces
        a counter-example that forces regression.

    REVISION
        Evidence gathered in EVALUATION and FALSIFICATION is distilled into
        targeted changes.  After REVISION the loop either re-enters
        FORMALIZATION (structural revision) or IMPLEMENTATION (patch-level
        revision) depending on the severity of the findings.
    """

    FORMALIZATION = "formalization"
    IMPLEMENTATION = "implementation"
    EVALUATION = "evaluation"
    FALSIFICATION = "falsification"
    REVISION = "revision"


class LoopStatus(str, Enum):
    """High-level lifecycle status of a :class:`MethodologyLoop`.

    This is distinct from :class:`LoopPhase`: *status* describes *whether* the
    loop is making progress, whereas *phase* describes *where* in the cycle it
    is.

    Members
    -------
    IDLE
        The loop has been created but not yet started.  No iterations have
        been executed and the state machine has not been advanced.
    RUNNING
        The loop is actively progressing through phases.  Worker threads or
        orchestrator tasks are permitted to invoke transition methods.
    CONVERGED
        The loop has reached a stable fixed-point: the specification,
        implementation, evaluation evidence, and falsification attempts are
        mutually consistent and the configured convergence threshold has been
        satisfied.  This is a *terminal* status.
    STALLED
        The maximum iteration count has been reached without convergence.
        A human reviewer must intervene; the loop may be reconfigured and
        restarted.  This is a *terminal* status.
    FAILED
        An unrecoverable error occurred (e.g. specification inconsistency with
        no valid revision, budget exhausted).  The loop is archived for
        post-mortem analysis.  This is a *terminal* status.
    """

    IDLE = "idle"
    RUNNING = "running"
    CONVERGED = "converged"
    STALLED = "stalled"
    FAILED = "failed"


class TransitionKind(str, Enum):
    """The nature of a phase transition recorded in a :class:`LoopTransition`.

    Members
    -------
    FORWARD
        Normal progression to the next phase in the canonical cycle order
        (FORMALIZATION → IMPLEMENTATION → EVALUATION → FALSIFICATION →
        REVISION → FORMALIZATION…).
    BACKWARD
        Regression to an earlier phase triggered by a negative evaluation
        finding or a counter-example from falsification.
    RESET
        Full restart of the loop — the phase returns to FORMALIZATION and
        the iteration counter is incremented but artefacts are *not* discarded
        (they are archived and tagged with the iteration number).
    BRANCH
        A new child loop is spawned to explore a hypothesis variant while
        the parent loop continues independently.  The transition records the
        child loop's identifier in its metadata.
    """

    FORWARD = "forward"
    BACKWARD = "backward"
    RESET = "reset"
    BRANCH = "branch"


# ---------------------------------------------------------------------------
# LoopDiagnostics
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoopDiagnostics:
    """Telemetry accumulator for a single :class:`MethodologyLoop` run.

    :class:`LoopDiagnostics` is a mutable container that collects timing
    samples, error messages, and per-phase counters during loop execution.
    It is embedded inside :class:`LoopState` and updated in-place as the loop
    progresses.

    Attributes
    ----------
    iteration_times : list[float]
        Wall-clock durations (seconds) of each completed iteration.  The
        *i*-th element is the duration of the *i*-th iteration.
    errors : list[str]
        Ordered list of error messages recorded during execution.  Each entry
        is a free-form string; callers should include enough context for
        post-mortem analysis (e.g. phase name, artefact identifier).
    warnings : list[str]
        Ordered list of non-fatal warning messages.  Warnings do not cause
        state transitions but are surfaced in summary reports.
    phase_counts : dict[str, int]
        Maps each :class:`LoopPhase` value string to the number of times the
        loop has entered that phase.  Counts start at zero and are incremented
        on entry.
    """

    iteration_times: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    phase_counts: dict[str, int] = field(
        default_factory=lambda: {p.value: 0 for p in LoopPhase}
    )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def record_iteration(self, duration: float) -> None:
        """Append a completed-iteration wall-clock duration sample.

        Parameters
        ----------
        duration:
            Elapsed wall-clock time in seconds for the most recently
            completed iteration.  Negative values are silently clamped to 0.
        """
        self.iteration_times.append(max(0.0, duration))

    def record_error(self, message: str) -> None:
        """Append an error message to the diagnostics log.

        Parameters
        ----------
        message:
            Human-readable description of the error.  Should include
            contextual information such as the current phase and iteration
            number so that post-mortem analysis is possible without
            additional lookups.
        """
        self.errors.append(message)

    def record_warning(self, message: str) -> None:
        """Append a non-fatal warning message to the diagnostics log.

        Warnings are surfaced in :meth:`summary` and
        :meth:`render_tex` but do not affect loop status.

        Parameters
        ----------
        message:
            Human-readable description of the warning condition.
        """
        self.warnings.append(message)

    def increment_phase(self, phase: LoopPhase) -> None:
        """Increment the entry counter for *phase*.

        Called automatically by :meth:`LoopState.advance_phase`; callers
        should not normally invoke this directly.

        Parameters
        ----------
        phase:
            The :class:`LoopPhase` that has just been entered.
        """
        key = phase.value if isinstance(phase, LoopPhase) else str(phase)
        self.phase_counts[key] = self.phase_counts.get(key, 0) + 1

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def get_avg_iteration_time(self) -> float:
        """Compute the arithmetic mean of all recorded iteration durations.

        Returns
        -------
        float
            Mean duration in seconds, or ``0.0`` if no iterations have been
            recorded yet.
        """
        if not self.iteration_times:
            return 0.0
        return sum(self.iteration_times) / len(self.iteration_times)

    def get_total_errors(self) -> int:
        """Return the total count of recorded error messages.

        Returns
        -------
        int
            Number of entries in :attr:`errors`.
        """
        return len(self.errors)

    def _summary_data(self) -> dict[str, Any]:
        """Build a concise structured diagnostic summary."""
        return {
            "iterations": len(self.iteration_times),
            "avg_iteration_time_s": round(self.get_avg_iteration_time(), 6),
            "total_errors": self.get_total_errors(),
            "total_warnings": len(self.warnings),
            "phase_counts": dict(self.phase_counts),
        }

    def summary(self) -> str:
        """Return a one-line human-readable diagnostics summary."""
        data = self._summary_data()
        return (
            "LoopDiagnostics("
            f"iterations={data['iterations']}, "
            f"avg_iteration_time_s={data['avg_iteration_time_s']}, "
            f"total_errors={data['total_errors']}, "
            f"total_warnings={data['total_warnings']})"
        )

    def reset(self) -> None:
        """Clear all accumulated diagnostics, restoring the object to its
        initial state.

        This is used when a loop is *reset* (via a :attr:`TransitionKind.RESET`
        transition) but the :class:`LoopDiagnostics` instance is reused to
        accumulate fresh telemetry for the new iteration sequence.

        .. warning::
            All previously recorded errors, warnings, and timing samples are
            permanently discarded.  Call :meth:`to_json` first if you need to
            preserve them.
        """
        self.iteration_times.clear()
        self.errors.clear()
        self.warnings.clear()
        self.phase_counts = {p.value: 0 for p in LoopPhase}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise diagnostics to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A fully JSON-serialisable mapping that round-trips through
            :meth:`from_json` without loss of information.
        """
        return {
            "iteration_times": list(self.iteration_times),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "phase_counts": dict(self.phase_counts),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LoopDiagnostics":
        """Deserialise a :class:`LoopDiagnostics` from a JSON dictionary.

        Parameters
        ----------
        data:
            A mapping as produced by :meth:`to_json`.

        Returns
        -------
        LoopDiagnostics
            A new instance populated with the provided data.
        """
        obj = cls()
        obj.iteration_times = list(data.get("iteration_times", []))
        obj.errors = list(data.get("errors", []))
        obj.warnings = list(data.get("warnings", []))
        obj.phase_counts = dict(data.get("phase_counts", {p.value: 0 for p in LoopPhase}))
        return obj

    def render_tex(self) -> str:
        """Render a LaTeX fragment summarising the diagnostics.

        Returns
        -------
        str
            A LaTeX ``tabular`` environment suitable for inclusion in a
            theory2.tex Ch62 appendix.
        """
        rows = "\n".join(
            rf"  {phase} & {count} \\"
            for phase, count in self.phase_counts.items()
        )
        return (
            r"\begin{tabular}{ll}" + "\n"
            r"  \textbf{Phase} & \textbf{Entries} \\" + "\n"
            r"  \hline" + "\n"
            f"{rows}\n"
            rf"  \hline" + "\n"
            rf"  Avg iter time & {self.get_avg_iteration_time():.4f}s \\" + "\n"
            rf"  Errors & {self.get_total_errors()} \\" + "\n"
            rf"  Warnings & {len(self.warnings)} \\" + "\n"
            r"\end{tabular}"
        )


# ---------------------------------------------------------------------------
# MethodologyConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MethodologyConfig:
    """Immutable configuration record for a :class:`MethodologyLoop`.

    Because :class:`MethodologyConfig` is *frozen*, all mutation methods
    return a *new* instance rather than modifying in-place.  This makes
    configuration changes explicit and audit-friendly.

    Attributes
    ----------
    max_iterations : int
        Hard upper bound on the total number of phase-transition iterations.
        When this count is reached without convergence the loop transitions
        to :attr:`LoopStatus.STALLED`.  Must be a positive integer.
    convergence_threshold : float
        Minimum combined score (in [0, 1]) of coverage, consistency, and
        falsification confidence required to declare
        :attr:`LoopStatus.CONVERGED`.  Higher values demand more evidence.
    falsification_budget : int
        Maximum number of adversarial falsification attempts allowed before
        the FALSIFICATION phase is considered exhausted.  Setting this to a
        very large value effectively means "attempt until timeout".
    min_coverage : float
        Minimum specification-coverage fraction (in [0, 1]) that the
        IMPLEMENTATION must achieve before the loop may advance from
        EVALUATION to FALSIFICATION.
    max_revisions : int
        Maximum number of times the loop may enter the REVISION phase in a
        single run.  Exceeding this limit triggers a :attr:`LoopStatus.STALLED`
        transition.
    """

    max_iterations: int = 100
    convergence_threshold: float = 0.95
    falsification_budget: int = 50
    min_coverage: float = 0.80
    max_revisions: int = 10

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validation_errors(self) -> list[str]:
        """Return any configuration issues without raising.

        This preserves access to the newer structured validation style while
        :meth:`validate` keeps the older boolean/exception contract used by
        the test-facing API.
        """
        issues: list[str] = []
        if self.max_iterations < 1:
            issues.append(f"max_iterations must be >= 1, got {self.max_iterations}")
        if not (0.0 < self.convergence_threshold <= 1.0):
            issues.append(
                f"convergence_threshold must be in (0, 1], got {self.convergence_threshold}"
            )
        if self.falsification_budget < 0:
            issues.append(
                f"falsification_budget must be >= 0, got {self.falsification_budget}"
            )
        if not (0.0 <= self.min_coverage <= 1.0):
            issues.append(f"min_coverage must be in [0, 1], got {self.min_coverage}")
        if self.max_revisions < 0:
            issues.append(f"max_revisions must be >= 0, got {self.max_revisions}")
        return issues

    def validate(self) -> bool:
        """Validate configuration field values.

        Returns ``True`` when the configuration is valid.  Invalid
        configurations raise :class:`ValueError`, matching the legacy API
        expected by the tests.
        """
        issues = self.validation_errors()
        if issues:
            raise ValueError("; ".join(issues))
        return True

    # ------------------------------------------------------------------
    # Named constructors
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "MethodologyConfig":
        """Return a balanced default configuration suitable for most loops.

        The defaults correspond to the recommended settings in theory2.tex
        Ch62 §4 and have been validated against the JuGeo benchmark suite.

        Returns
        -------
        MethodologyConfig
            A configuration with ``max_iterations=100``,
            ``convergence_threshold=0.95``, ``falsification_budget=50``,
            ``min_coverage=0.80``, ``max_revisions=10``.
        """
        return cls()

    @classmethod
    def strict(cls) -> "MethodologyConfig":
        """Return a high-rigour configuration for production-grade loops.

        *Strict* mode raises the convergence threshold to 0.99, doubles the
        falsification budget, and demands near-complete coverage before
        advancing.  Appropriate for safety-critical or formally verified
        specifications.

        Returns
        -------
        MethodologyConfig
            A configuration with ``convergence_threshold=0.99``,
            ``falsification_budget=200``, ``min_coverage=0.95``.
        """
        return cls(
            max_iterations=200,
            convergence_threshold=0.99,
            falsification_budget=200,
            min_coverage=0.95,
            max_revisions=5,
        )

    @classmethod
    def lenient(cls) -> "MethodologyConfig":
        """Return a relaxed configuration suitable for exploratory research.

        *Lenient* mode reduces the coverage and convergence requirements and
        allows many more revisions, making it appropriate for early-stage
        ideation where strict thresholds would cause premature stalling.

        Returns
        -------
        MethodologyConfig
            A configuration with ``convergence_threshold=0.70``,
            ``falsification_budget=10``, ``min_coverage=0.50``,
            ``max_revisions=30``.
        """
        return cls(
            max_iterations=500,
            convergence_threshold=0.70,
            falsification_budget=10,
            min_coverage=0.50,
            max_revisions=30,
        )

    # ------------------------------------------------------------------
    # Functional updaters (return new instances)
    # ------------------------------------------------------------------

    def with_max_iterations(self, n: int) -> "MethodologyConfig":
        """Return a copy with :attr:`max_iterations` replaced by *n*.

        Parameters
        ----------
        n:
            New upper bound on iteration count.  Must be a positive integer.

        Returns
        -------
        MethodologyConfig
            A new immutable instance with the updated field.
        """
        return MethodologyConfig(
            max_iterations=n,
            convergence_threshold=self.convergence_threshold,
            falsification_budget=self.falsification_budget,
            min_coverage=self.min_coverage,
            max_revisions=self.max_revisions,
        )

    def with_threshold(self, threshold: float) -> "MethodologyConfig":
        """Return a copy with :attr:`convergence_threshold` replaced.

        Parameters
        ----------
        threshold:
            New convergence threshold.  Must be in the range (0, 1].

        Returns
        -------
        MethodologyConfig
            A new immutable instance with the updated field.
        """
        return MethodologyConfig(
            max_iterations=self.max_iterations,
            convergence_threshold=_clamp(threshold, 1e-9, 1.0),
            falsification_budget=self.falsification_budget,
            min_coverage=self.min_coverage,
            max_revisions=self.max_revisions,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "max_iterations": self.max_iterations,
            "convergence_threshold": self.convergence_threshold,
            "falsification_budget": self.falsification_budget,
            "min_coverage": self.min_coverage,
            "max_revisions": self.max_revisions,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MethodologyConfig":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the constructor parameters.

        Returns
        -------
        MethodologyConfig
            New frozen instance.
        """
        return cls(
            max_iterations=int(data.get("max_iterations", 100)),
            convergence_threshold=float(data.get("convergence_threshold", 0.95)),
            falsification_budget=int(data.get("falsification_budget", 50)),
            min_coverage=float(data.get("min_coverage", 0.80)),
            max_revisions=int(data.get("max_revisions", 10)),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary of the configuration.

        Returns
        -------
        str
            A brief description listing the key thresholds.
        """
        return (
            f"MethodologyConfig("
            f"max_iter={self.max_iterations}, "
            f"thresh={self.convergence_threshold:.2f}, "
            f"falsif_budget={self.falsification_budget}, "
            f"min_cov={self.min_coverage:.2f}, "
            f"max_rev={self.max_revisions})"
        )


# ---------------------------------------------------------------------------
# LoopState
# ---------------------------------------------------------------------------


@dataclass(slots=True, init=False)
class LoopState:
    """Live state of a :class:`MethodologyLoop`.

    :class:`LoopState` is the mutable heart of every loop; it tracks the
    current phase, iteration counter, accumulated artefacts, embedded
    diagnostics, and a structured history of all events.

    Attributes
    ----------
    phase : LoopPhase
        The phase the loop is currently executing.
    iteration : int
        Zero-based count of fully completed iterations (i.e. complete cycles
        through the canonical phase sequence).
    artifacts : list[str]
        Identifiers (URIs or UUIDs) of artefacts produced so far by any phase.
    diagnostics : LoopDiagnostics
        Telemetry accumulator; updated in-place throughout loop execution.
    history : list[dict[str, Any]]
        Chronological log of events.  Each entry is a free-form mapping with
        at least ``"timestamp"`` and ``"event"`` keys.
    status : LoopStatus
        Current lifecycle status of the loop.
    """

    phase: LoopPhase = LoopPhase.FORMALIZATION
    iteration: int = 0
    artifacts: list[str] = field(default_factory=list)
    diagnostics: LoopDiagnostics = field(default_factory=LoopDiagnostics)
    history: list[dict[str, Any]] = field(default_factory=list)
    status: LoopStatus = LoopStatus.IDLE
    config: Optional[MethodologyConfig] = None

    def __init__(
        self,
        phase: LoopPhase = LoopPhase.FORMALIZATION,
        iteration: int = 0,
        artifacts: Optional[list[str]] = None,
        diagnostics: Optional[LoopDiagnostics] = None,
        history: Optional[list[dict[str, Any]]] = None,
        status: LoopStatus = LoopStatus.IDLE,
        config: Optional[MethodologyConfig] = None,
        current_phase: Optional[LoopPhase] = None,
    ) -> None:
        if current_phase is not None:
            phase = current_phase
        self.phase = phase
        self.iteration = iteration
        self.artifacts = list(artifacts) if artifacts is not None else []
        self.diagnostics = diagnostics if diagnostics is not None else LoopDiagnostics()
        self.history = list(history) if history is not None else []
        self.status = status
        self.config = config

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def advance_phase(self, next_phase: Optional[LoopPhase] = None, *, trigger: str = "") -> None:
        """Advance the state machine to *next_phase*.

        This method updates :attr:`phase`, increments the diagnostics counter
        for the new phase, and appends an event to :attr:`history`.  It does
        *not* create a :class:`LoopTransition` record — that is the
        responsibility of the enclosing :class:`MethodologyLoop`.

        Parameters
        ----------
        next_phase:
            The target :class:`LoopPhase` to enter.
        trigger:
            Optional human-readable description of what caused this transition
            (e.g. ``"coverage threshold reached"``).
        """
        if next_phase is None:
            phases = list(LoopPhase)
            next_index = (phases.index(self.phase) + 1) % len(phases)
            next_phase = phases[next_index]
        prev = self.phase
        self.phase = next_phase
        self.diagnostics.increment_phase(next_phase)
        self.append_history(
            {
                "event": "phase_advance",
                "from_phase": prev.value,
                "to_phase": next_phase.value,
                "trigger": trigger,
                "timestamp": _utcnow(),
            }
        )

    # ------------------------------------------------------------------
    # Artefact management
    # ------------------------------------------------------------------

    def record_artifact(self, artifact_id: str, artifact: Any = None) -> None:
        """Register an artefact identifier produced during the current phase.

        Parameters
        ----------
        artifact_id:
            A string identifier (URI, UUID, or path) for the artefact.  No
            deduplication is performed; duplicate IDs will appear multiple
            times in :attr:`artifacts`.
        """
        self.artifacts.append(artifact_id)
        self.append_history(
            {
                "event": "artifact_recorded",
                "artifact_id": artifact_id,
                "artifact": artifact,
                "phase": self.phase.value,
                "timestamp": _utcnow(),
            }
        )

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def append_history(self, entry: Any) -> None:
        """Append an event dictionary to the chronological history log.

        Parameters
        ----------
        entry:
            A mapping describing the event.  Should include at minimum a
            ``"timestamp"`` key with a Unix-epoch float value and an
            ``"event"`` key with a string event type.
        """
        if isinstance(entry, dict):
            payload = entry
        else:
            payload = {"event": str(entry), "timestamp": _utcnow()}
        self.history.append(payload)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Return ``True`` if the loop has reached a terminal status.

        A loop is terminal when its :attr:`status` is one of
        :attr:`LoopStatus.CONVERGED`, :attr:`LoopStatus.STALLED`, or
        :attr:`LoopStatus.FAILED`.  No further phase transitions should occur
        once a loop is terminal.

        Returns
        -------
        bool
        """
        return self.status in (
            LoopStatus.CONVERGED,
            LoopStatus.STALLED,
            LoopStatus.FAILED,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the full loop state to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "phase": self.phase.value,
            "iteration": self.iteration,
            "artifacts": list(self.artifacts),
            "diagnostics": self.diagnostics.to_json(),
            "history": list(self.history),
            "status": self.status.value,
            "config": self.config.to_json() if self.config is not None else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LoopState":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the class attributes.

        Returns
        -------
        LoopState
            A new mutable instance populated with the provided data.
        """
        obj = cls.__new__(cls)
        obj.phase = LoopPhase(data.get("phase", LoopPhase.FORMALIZATION.value))
        obj.iteration = int(data.get("iteration", 0))
        obj.artifacts = list(data.get("artifacts", []))
        obj.diagnostics = LoopDiagnostics.from_json(data.get("diagnostics", {}))
        obj.history = list(data.get("history", []))
        obj.status = LoopStatus(data.get("status", LoopStatus.IDLE.value))
        config_data = data.get("config")
        obj.config = MethodologyConfig.from_json(config_data) if isinstance(config_data, dict) else None
        return obj

    def _summary_data(self) -> dict[str, Any]:
        """Return a concise structured summary of the current loop state."""
        return {
            "phase": self.phase.value,
            "iteration": self.iteration,
            "artifact_count": len(self.artifacts),
            "status": self.status.value,
            "diagnostics": self.diagnostics._summary_data(),
        }

    def summarize(self) -> str:
        """Return a human-readable summary of the current loop state."""
        data = self._summary_data()
        return (
            "LoopState("
            f"phase={data['phase']}, "
            f"iteration={data['iteration']}, "
            f"artifact_count={data['artifact_count']}, "
            f"status={data['status']})"
        )

    def render_tex(self) -> str:
        """Render a LaTeX description of the current loop state.

        Returns
        -------
        str
            A short LaTeX paragraph suitable for inclusion in a theory2.tex
            Ch62 appendix.
        """
        return (
            r"\paragraph{Loop State}" + "\n"
            rf"Phase: \texttt{{{self.phase.value}}}, "
            rf"Iteration: {self.iteration}, "
            rf"Status: \texttt{{{self.status.value}}}, "
            rf"Artefacts: {len(self.artifacts)}."
            "\n" + self.diagnostics.render_tex()
        )


# ---------------------------------------------------------------------------
# LoopTransition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoopTransition:
    """An immutable record of a single phase transition in a methodology loop.

    Each time a :class:`MethodologyLoop` moves from one :class:`LoopPhase` to
    another, a :class:`LoopTransition` is created and appended to the loop's
    transition log.  The collection of transitions forms a complete audit
    trail of the loop's execution history.

    Attributes
    ----------
    transition_id : str
        Globally unique identifier for this transition event.
    from_phase : LoopPhase
        The phase the loop was in immediately before this transition.
    to_phase : LoopPhase
        The phase the loop entered as a result of this transition.
    kind : TransitionKind
        The nature of the transition (forward, backward, reset, or branch).
    trigger : str
        Human-readable description of the condition that triggered the
        transition (e.g. ``"coverage >= 0.80"``).
    timestamp : float
        Unix epoch time at which the transition was recorded.
    metadata : dict[str, Any]
        Arbitrary additional data associated with this transition (e.g. the
        child loop ID for a BRANCH transition).
    """

    from_phase: LoopPhase
    to_phase: LoopPhase
    kind: TransitionKind
    transition_id: str = field(default_factory=_uid)
    trigger: str = ""
    timestamp: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    loop_id: str = ""

    # ------------------------------------------------------------------
    # Named constructors
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        from_phase: LoopPhase,
        to_phase: LoopPhase,
        kind: TransitionKind,
        trigger: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> "LoopTransition":
        """Create a new :class:`LoopTransition` with auto-generated id and timestamp.

        Parameters
        ----------
        from_phase:
            The phase the loop is transitioning *from*.
        to_phase:
            The phase the loop is transitioning *to*.
        kind:
            The :class:`TransitionKind` classifying this transition.
        trigger:
            Optional human-readable trigger description.
        metadata:
            Optional extra data to embed in :attr:`metadata`.

        Returns
        -------
        LoopTransition
            A new frozen instance.
        """
        return cls(
            transition_id=_uid(),
            from_phase=from_phase,
            to_phase=to_phase,
            kind=kind,
            trigger=trigger,
            timestamp=_utcnow(),
            metadata=metadata or {},
        )

    @classmethod
    def forward(
        cls,
        from_phase: LoopPhase,
        to_phase: LoopPhase,
        trigger: str = "",
    ) -> "LoopTransition":
        """Convenience constructor for :attr:`TransitionKind.FORWARD` transitions.

        Parameters
        ----------
        from_phase, to_phase, trigger:
            Forwarded to :meth:`create`.

        Returns
        -------
        LoopTransition
        """
        return cls.create(from_phase, to_phase, TransitionKind.FORWARD, trigger)

    @classmethod
    def backward(
        cls,
        from_phase: LoopPhase,
        to_phase: LoopPhase,
        trigger: str = "",
    ) -> "LoopTransition":
        """Convenience constructor for :attr:`TransitionKind.BACKWARD` transitions.

        Parameters
        ----------
        from_phase, to_phase, trigger:
            Forwarded to :meth:`create`.

        Returns
        -------
        LoopTransition
        """
        return cls.create(from_phase, to_phase, TransitionKind.BACKWARD, trigger)

    @classmethod
    def reset(
        cls,
        from_phase: LoopPhase,
        trigger: str = "",
    ) -> "LoopTransition":
        """Convenience constructor for full-reset transitions.

        A reset always targets :attr:`LoopPhase.FORMALIZATION` regardless of
        the current phase.

        Parameters
        ----------
        from_phase:
            The phase the loop is resetting from.
        trigger:
            Optional trigger description.

        Returns
        -------
        LoopTransition
        """
        return cls.create(
            from_phase, LoopPhase.FORMALIZATION, TransitionKind.RESET, trigger
        )

    # ------------------------------------------------------------------
    # Predicate
    # ------------------------------------------------------------------

    def is_regression(self) -> bool:
        """Return ``True`` if this transition moves the loop to an earlier phase.

        A *regression* is any :attr:`TransitionKind.BACKWARD` transition or
        any :attr:`TransitionKind.RESET` transition.  Regressions are tracked
        separately in summary reports because they indicate that earlier work
        must be revisited.

        Returns
        -------
        bool
        """
        return self.kind in (TransitionKind.BACKWARD, TransitionKind.RESET)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "transition_id": self.transition_id,
            "from_phase": self.from_phase.value,
            "to_phase": self.to_phase.value,
            "kind": self.kind.value,
            "trigger": self.trigger,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
            "loop_id": self.loop_id,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LoopTransition":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the frozen fields.

        Returns
        -------
        LoopTransition
            A new frozen instance.
        """
        return cls(
            transition_id=str(data["transition_id"]),
            from_phase=LoopPhase(data["from_phase"]),
            to_phase=LoopPhase(data["to_phase"]),
            kind=TransitionKind(data["kind"]),
            trigger=str(data.get("trigger", "")),
            timestamp=float(data.get("timestamp", 0.0)),
            metadata=dict(data.get("metadata", {})),
            loop_id=str(data.get("loop_id", "")),
        )

    def summarize(self) -> str:
        """Return a one-line human-readable description of this transition.

        Returns
        -------
        str
            E.g. ``"[FORWARD] formalization → implementation (coverage >= 0.80)"``.
        """
        regression_marker = " ⚠ REGRESSION" if self.is_regression() else ""
        return (
            f"[{self.kind.value.upper()}] "
            f"{self.from_phase.value} → {self.to_phase.value} "
            f"({self.trigger}){regression_marker}"
        )


# ---------------------------------------------------------------------------
# MethodologyLoop
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MethodologyLoop:
    """Top-level container for a complete methodology loop execution.

    A :class:`MethodologyLoop` ties together configuration, live state, the
    complete transition log, and a flat list of artefact identifiers produced
    over the loop's lifetime.

    Attributes
    ----------
    loop_id : str
        Globally unique identifier for this loop instance.
    config : MethodologyConfig
        Frozen configuration governing iteration limits and thresholds.
    state : LoopState
        Mutable live state of the loop.
    transitions : list[LoopTransition]
        Ordered log of every phase transition that has occurred.
    artifacts : list[str]
        Aggregated list of all artefact identifiers produced across all phases.
        This is a denormalised copy; :attr:`LoopState.artifacts` is the
        authoritative source.
    created_at : float
        Unix epoch timestamp when this loop was created.
    updated_at : float
        Unix epoch timestamp of the most recent mutation.
    """

    loop_id: str = field(default_factory=_uid)
    config: MethodologyConfig = field(default_factory=MethodologyConfig.default)
    state: LoopState = field(default_factory=LoopState)
    transitions: list[LoopTransition] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=_utcnow)
    updated_at: float = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_transition(self, transition: LoopTransition) -> None:
        """Append a phase transition to the log and update :attr:`updated_at`.

        This method also applies the transition to the embedded
        :class:`LoopState` by calling :meth:`LoopState.advance_phase`.

        Parameters
        ----------
        transition:
            The :class:`LoopTransition` to record.  It should have been
            created via one of the :class:`LoopTransition` named constructors.
        """
        self.transitions.append(transition)
        self.state.advance_phase(transition.to_phase, trigger=transition.trigger)
        self.updated_at = _utcnow()

    def add_artifact(self, artifact_id: str, artifact: Any = None) -> None:
        """Register an artefact identifier and synchronise with the state.

        Parameters
        ----------
        artifact_id:
            Identifier of the artefact (URI, UUID, or file path).
        """
        self.artifacts.append(artifact_id)
        self.state.record_artifact(artifact_id, artifact)
        self.updated_at = _utcnow()

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_converged(self) -> bool:
        """Return ``True`` if the loop has converged.

        Returns
        -------
        bool
        """
        return self.state.status == LoopStatus.CONVERGED

    def is_failed(self) -> bool:
        """Return ``True`` if the loop has failed.

        Returns
        -------
        bool
        """
        return self.state.status == LoopStatus.FAILED

    @property
    def current_phase(self) -> LoopPhase:
        """Return the current :class:`LoopPhase` of the loop.

        Returns
        -------
        LoopPhase
        """
        return self.state.phase

    @property
    def iteration_count(self) -> int:
        """Return the number of completed iterations.

        Returns
        -------
        int
        """
        return self.state.iteration

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the full loop to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "loop_id": self.loop_id,
            "config": self.config.to_json(),
            "state": self.state.to_json(),
            "transitions": [t.to_json() for t in self.transitions],
            "artifacts": list(self.artifacts),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MethodologyLoop":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the class attributes.

        Returns
        -------
        MethodologyLoop
            A fully populated instance.
        """
        obj = cls.__new__(cls)
        obj.loop_id = str(data.get("loop_id", _uid()))
        obj.config = MethodologyConfig.from_json(data.get("config", {}))
        obj.state = LoopState.from_json(data.get("state", {}))
        obj.transitions = [
            LoopTransition.from_json(t) for t in data.get("transitions", [])
        ]
        obj.artifacts = list(data.get("artifacts", []))
        obj.created_at = float(data.get("created_at", _utcnow()))
        obj.updated_at = float(data.get("updated_at", _utcnow()))
        return obj

    def _summary_data(self) -> dict[str, Any]:
        """Build a structured summary of the loop's current state."""
        regressions = sum(1 for t in self.transitions if t.is_regression())
        return {
            "loop_id": self.loop_id,
            "config": self.config.summary(),
            "state": self.state._summary_data(),
            "transition_count": len(self.transitions),
            "artifact_count": len(self.artifacts),
            "regressions": regressions,
        }

    def summarize(self) -> str:
        """Return a human-readable summary of the loop's current state."""
        data = self._summary_data()
        return (
            "MethodologyLoop("
            f"loop_id={data['loop_id']}, "
            f"transition_count={data['transition_count']}, "
            f"artifact_count={data['artifact_count']}, "
            f"regressions={data['regressions']})"
        )

    def history_entries(self) -> list[str]:
        """Return ordered human-readable transition summaries."""
        return [t.summarize() for t in self.transitions]

    def history_report(self) -> str:
        """Return a printable transition history report."""
        return "\n".join(self.history_entries())


# ---------------------------------------------------------------------------
# FormalizationLoop
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FormalizationLoop(MethodologyLoop):
    """Specialised loop tracking the formal specification phase in detail.

    :class:`FormalizationLoop` supplements the base :class:`MethodologyLoop`
    with data specific to the FORMALIZATION phase: the formal language in use,
    the current specification version, individual clauses, and quality metrics
    (consistency and completeness scores).

    Attributes
    ----------
    loop_id : str
        Identifier of the parent :class:`MethodologyLoop`.
    formal_language : str
        Name of the formal specification language being used (e.g. ``"TLA+"``,
        ``"Lean4"``, ``"Z"``, ``"Alloy"``).
    spec_version : str
        Semantic version string of the current specification draft
        (e.g. ``"0.3.1"``).
    formalization_artifacts : list[str]
        Identifiers of artefacts produced specifically during formalization
        (axiom files, type-signature documents, glossaries).
    spec_clauses : list[str]
        Individual specification clauses or axioms, represented as strings.
        The ordering is significant and corresponds to the clause ordering in
        the formal document.
    consistency_score : float
        A score in [0, 1] representing the degree to which the current
        set of spec_clauses is internally consistent (no contradictions
        detected by automated checkers).
    completeness_score : float
        A score in [0, 1] representing the fraction of the intended
        specification domain that is covered by the current clause set.
    created_at : float
        Unix epoch timestamp when this record was created.
    """

    formal_language: str = "unspecified"
    spec_version: str = "0.1.0"
    formalization_artifacts: list[str] = field(default_factory=list)
    spec_clauses: list[str] = field(default_factory=list)
    consistency_score: float = 0.0
    completeness_score: float = 0.0

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @property
    def clauses(self) -> list[str]:
        return self.spec_clauses

    def add_clause(self, clause: str, score: Optional[float] = None) -> None:
        """Append a specification clause to the clause list.

        Parameters
        ----------
        clause:
            The formal clause text.  It should be a well-formed formula
            in :attr:`formal_language`.
        """
        self.spec_clauses.append(clause)
        if score is not None:
            bounded = _clamp(float(score), 0.0, 1.0)
            self.consistency_score = max(self.consistency_score, bounded)
            clause_count = len(self.spec_clauses)
            self.completeness_score = (
                bounded if clause_count <= 1 else ((self.completeness_score * (clause_count - 1)) + bounded) / clause_count
            )

    def add_artifact(self, artifact_id: str, artifact: Any = None) -> None:
        """Register a formalization artefact identifier.

        Parameters
        ----------
        artifact_id:
            URI, UUID, or path of the artefact.
        """
        self.formalization_artifacts.append(artifact_id)
        MethodologyLoop.add_artifact(self, artifact_id, artifact)

    # ------------------------------------------------------------------
    # Quality queries
    # ------------------------------------------------------------------

    def is_complete(self, threshold: float = 0.80) -> bool:
        """Return ``True`` if the formalization meets the completeness threshold.

        Parameters
        ----------
        threshold:
            Minimum :attr:`completeness_score` required to declare the
            formalization complete.  Defaults to ``0.80``.

        Returns
        -------
        bool
        """
        return self.completeness_score >= threshold

    def compute_quality_score(self) -> float:
        """Compute a composite quality score for the formalization.

        The quality score is the geometric mean of :attr:`consistency_score`
        and :attr:`completeness_score`, which penalises imbalanced profiles
        (e.g. perfectly consistent but entirely incomplete).

        Returns
        -------
        float
            A value in [0, 1]; ``0.0`` if either score is zero.
        """
        if self.consistency_score <= 0.0 or self.completeness_score <= 0.0:
            return 0.0
        return math.sqrt(self.consistency_score * self.completeness_score)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "loop_id": self.loop_id,
            "formal_language": self.formal_language,
            "spec_version": self.spec_version,
            "formalization_artifacts": list(self.formalization_artifacts),
            "spec_clauses": list(self.spec_clauses),
            "consistency_score": self.consistency_score,
            "completeness_score": self.completeness_score,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "FormalizationLoop":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the class attributes.

        Returns
        -------
        FormalizationLoop
        """
        obj = cls.__new__(cls)
        obj.loop_id = str(data.get("loop_id", _uid()))
        obj.formal_language = str(data.get("formal_language", "unspecified"))
        obj.spec_version = str(data.get("spec_version", "0.1.0"))
        obj.formalization_artifacts = list(data.get("formalization_artifacts", []))
        obj.spec_clauses = list(data.get("spec_clauses", []))
        obj.consistency_score = float(data.get("consistency_score", 0.0))
        obj.completeness_score = float(data.get("completeness_score", 0.0))
        obj.created_at = float(data.get("created_at", _utcnow()))
        return obj

    def _summary_data(self) -> dict[str, Any]:
        """Return a structured summary of the formalization loop state."""
        return {
            "loop_id": self.loop_id,
            "formal_language": self.formal_language,
            "spec_version": self.spec_version,
            "clause_count": len(self.spec_clauses),
            "artifact_count": len(self.formalization_artifacts),
            "consistency_score": round(self.consistency_score, 4),
            "completeness_score": round(self.completeness_score, 4),
            "quality_score": round(self.compute_quality_score(), 4),
        }

    def summarize(self) -> str:
        """Return a human-readable summary of the formalization loop state."""
        data = self._summary_data()
        return (
            "FormalizationLoop("
            f"loop_id={data['loop_id']}, "
            f"clause_count={data['clause_count']}, "
            f"artifact_count={data['artifact_count']}, "
            f"quality_score={data['quality_score']})"
        )

    def render_tex(self) -> str:
        """Render a LaTeX summary of the formalization loop.

        Returns
        -------
        str
            A LaTeX ``description`` environment listing key metrics.
        """
        return (
            r"\begin{description}" + "\n"
            rf"  \item[Language] \texttt{{{self.formal_language}}}" + "\n"
            rf"  \item[Version] \texttt{{{self.spec_version}}}" + "\n"
            rf"  \item[Clauses] {len(self.spec_clauses)}" + "\n"
            rf"  \item[Consistency] {self.consistency_score:.4f}" + "\n"
            rf"  \item[Completeness] {self.completeness_score:.4f}" + "\n"
            rf"  \item[Quality] {self.compute_quality_score():.4f}" + "\n"
            r"\end{description}"
        )


# ---------------------------------------------------------------------------
# ImplementationLoop
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ImplementationLoop(MethodologyLoop):
    """Specialised loop tracking the implementation phase in detail.

    :class:`ImplementationLoop` records build status, test-coverage evolution,
    individual test results, and build log entries accumulated during the
    IMPLEMENTATION phase of a methodology loop.

    Attributes
    ----------
    loop_id : str
        Identifier of the parent :class:`MethodologyLoop`.
    test_coverage : float
        Current test-coverage fraction in [0, 1].
    build_status : str
        Human-readable build status string: typically ``"passing"``,
        ``"failing"``, or ``"unknown"``.
    implementation_artifacts : list[str]
        Identifiers of artefacts produced during implementation (compiled
        binaries, proof scripts, model files).
    test_results : list[dict[str, Any]]
        Ordered list of test-result records.  Each record should contain at
        minimum ``"test_id"``, ``"outcome"`` (``"pass"`` or ``"fail"``), and
        ``"timestamp"``.
    build_log : list[str]
        Chronological build log lines.
    created_at : float
        Unix epoch timestamp when this record was created.
    """

    test_coverage: float = 0.0
    build_status: str = "unknown"
    implementation_artifacts: list[str] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    build_log: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.test_coverage

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def update_coverage(self, coverage: float) -> None:
        """Update the test-coverage metric.

        Parameters
        ----------
        coverage:
            New coverage value.  Automatically clamped to [0, 1].
        """
        self.test_coverage = _clamp(coverage, 0.0, 1.0)

    def add_test_result(self, result: Optional[dict[str, Any]] = None, **kwargs: Any) -> None:
        """Append a test-result record.

        Parameters
        ----------
        result:
            A mapping describing the outcome of a single test run.  Expected
            keys: ``"test_id"`` (str), ``"outcome"`` (``"pass"`` or
            ``"fail"``), ``"timestamp"`` (float).  Additional keys are
            permitted and will be preserved.
        """
        if result is None:
            name = kwargs.get("name", kwargs.get("test_id", ""))
            passed = kwargs.get("passed")
            outcome = kwargs.get("outcome")
            if outcome is None and passed is not None:
                outcome = "pass" if passed else "fail"
            result = {
                "test_id": name,
                "outcome": outcome or "pass",
                "duration": kwargs.get("duration", 0.0),
                "timestamp": kwargs.get("timestamp", _utcnow()),
            }
        self.test_results.append(result)

    def add_build_log(self, line: str) -> None:
        """Append a line to the build log.

        Parameters
        ----------
        line:
            A single log line string.  Newlines within the string are
            preserved as-is.
        """
        self.build_log.append(line)

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_passing(self) -> bool:
        """Return ``True`` if the build is currently passing.

        Returns
        -------
        bool
            ``True`` if :attr:`build_status` equals ``"passing"``.
        """
        return self.build_status == "passing"

    def compute_health_score(self) -> float:
        """Compute a composite health score for the implementation.

        The health score combines :attr:`test_coverage` with the pass-rate of
        recorded test results.  Both components are weighted equally.

        Returns
        -------
        float
            A value in [0, 1].  Returns ``0.0`` if no test results are recorded
            and the coverage is zero.
        """
        if not self.test_results:
            return self.test_coverage
        passes = sum(
            1 for r in self.test_results if r.get("outcome") == "pass"
        )
        pass_rate = passes / len(self.test_results)
        return (self.test_coverage + pass_rate) / 2.0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "loop_id": self.loop_id,
            "test_coverage": self.test_coverage,
            "build_status": self.build_status,
            "implementation_artifacts": list(self.implementation_artifacts),
            "test_results": list(self.test_results),
            "build_log": list(self.build_log),
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ImplementationLoop":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the class attributes.

        Returns
        -------
        ImplementationLoop
        """
        obj = cls.__new__(cls)
        obj.loop_id = str(data.get("loop_id", _uid()))
        obj.test_coverage = float(data.get("test_coverage", 0.0))
        obj.build_status = str(data.get("build_status", "unknown"))
        obj.implementation_artifacts = list(data.get("implementation_artifacts", []))
        obj.test_results = list(data.get("test_results", []))
        obj.build_log = list(data.get("build_log", []))
        obj.created_at = float(data.get("created_at", _utcnow()))
        return obj

    def _summary_data(self) -> dict[str, Any]:
        """Return a structured summary of the implementation loop state."""
        return {
            "loop_id": self.loop_id,
            "test_coverage": round(self.test_coverage, 4),
            "build_status": self.build_status,
            "test_result_count": len(self.test_results),
            "artifact_count": len(self.implementation_artifacts),
            "health_score": round(self.compute_health_score(), 4),
        }

    def summarize(self) -> str:
        """Return a human-readable summary of the implementation loop state."""
        data = self._summary_data()
        return (
            "ImplementationLoop("
            f"loop_id={data['loop_id']}, "
            f"test_coverage={data['test_coverage']}, "
            f"test_result_count={data['test_result_count']}, "
            f"health_score={data['health_score']})"
        )


# ---------------------------------------------------------------------------
# FalsificationLoop
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FalsificationLoop(MethodologyLoop):
    """Specialised loop tracking adversarial falsification attempts in detail.

    :class:`FalsificationLoop` records each falsification attempt, any
    counter-examples discovered, and the current hypothesis status map.  It
    enforces a configurable budget on the total number of attempts.

    Attributes
    ----------
    loop_id : str
        Identifier of the parent :class:`MethodologyLoop`.
    falsification_attempts : list[dict[str, Any]]
        Ordered list of attempt records.  Each record should contain at
        minimum ``"attempt_id"`` (str), ``"method"`` (str describing the
        falsification strategy), ``"result"`` (``"refuted"`` or
        ``"inconclusive"``), and ``"timestamp"`` (float).
    counterexamples : list[dict[str, Any]]
        Counter-example records discovered during falsification.  Each entry
        should contain ``"hypothesis_id"`` (str), ``"counterexample"``
        (structured data), and ``"timestamp"`` (float).
    hypothesis_status : dict[str, str]
        Maps hypothesis identifiers to their current status strings: one of
        ``"untested"``, ``"supported"``, ``"refuted"``, or
        ``"inconclusive"``.
    budget_used : int
        Number of falsification attempts consumed so far.
    budget_total : int
        Maximum allowed falsification attempts (copied from
        :attr:`MethodologyConfig.falsification_budget`).
    created_at : float
        Unix epoch timestamp when this record was created.
    """

    falsification_attempts: list[dict[str, Any]] = field(default_factory=list)
    counterexamples: list[dict[str, Any]] = field(default_factory=list)
    hypothesis_status: dict[str, str] = field(default_factory=dict)
    budget_used: int = 0
    budget_total: int = 50

    @property
    def attempt_count(self) -> int:
        return len(self.falsification_attempts)

    @property
    def hypothesis(self) -> Optional[str]:
        return next(iter(self.hypothesis_status), None)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_attempt(self, attempt: Optional[dict[str, Any]] = None, **kwargs: Any) -> None:
        """Record a falsification attempt and increment :attr:`budget_used`.

        Parameters
        ----------
        attempt:
            Attempt descriptor mapping.  Expected keys: ``"attempt_id"``,
            ``"method"``, ``"result"``, ``"timestamp"``.
        """
        if attempt is None:
            hypothesis = kwargs.get("hypothesis", "")
            outcome = kwargs.get("outcome", "inconclusive")
            attempt = {
                "attempt_id": kwargs.get("attempt_id", _uid()),
                "hypothesis_id": hypothesis,
                "method": kwargs.get("method", "unspecified"),
                "result": outcome,
                "timestamp": kwargs.get("timestamp", _utcnow()),
            }
            if hypothesis:
                self.hypothesis_status.setdefault(hypothesis, outcome)
        self.falsification_attempts.append(attempt)
        self.budget_used += 1

    def add_counterexample(self, counterexample: dict[str, Any]) -> None:
        """Record a discovered counter-example.

        Parameters
        ----------
        counterexample:
            Counter-example descriptor.  Should include
            ``"hypothesis_id"`` so that the hypothesis status can be
            updated automatically.
        """
        self.counterexamples.append(counterexample)
        hypothesis_id = counterexample.get("hypothesis_id", "")
        if hypothesis_id:
            self.hypothesis_status[hypothesis_id] = "refuted"

    def update_hypothesis(self, hypothesis_id: str, status: str = "supported") -> None:
        """Manually update the status of a hypothesis.

        Parameters
        ----------
        hypothesis_id:
            Unique identifier of the hypothesis to update.
        status:
            New status string: one of ``"untested"``, ``"supported"``,
            ``"refuted"``, or ``"inconclusive"``.
        """
        self.hypothesis_status[hypothesis_id] = status

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_exhausted(self) -> bool:
        """Return ``True`` if the falsification budget has been fully consumed.

        Returns
        -------
        bool
            ``True`` if :attr:`budget_used` >= :attr:`budget_total`.
        """
        return self.budget_used >= self.budget_total

    def compute_falsification_rate(self) -> float:
        """Compute the fraction of attempts that produced a counter-example.

        Returns
        -------
        float
            Number of counter-examples divided by number of attempts, or
            ``0.0`` if no attempts have been made.
        """
        if not self.falsification_attempts:
            return 0.0
        return len(self.counterexamples) / len(self.falsification_attempts)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "loop_id": self.loop_id,
            "falsification_attempts": list(self.falsification_attempts),
            "counterexamples": list(self.counterexamples),
            "hypothesis_status": dict(self.hypothesis_status),
            "budget_used": self.budget_used,
            "budget_total": self.budget_total,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "FalsificationLoop":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the class attributes.

        Returns
        -------
        FalsificationLoop
        """
        obj = cls.__new__(cls)
        obj.loop_id = str(data.get("loop_id", _uid()))
        obj.falsification_attempts = list(data.get("falsification_attempts", []))
        obj.counterexamples = list(data.get("counterexamples", []))
        obj.hypothesis_status = dict(data.get("hypothesis_status", {}))
        obj.budget_used = int(data.get("budget_used", 0))
        obj.budget_total = int(data.get("budget_total", 50))
        obj.created_at = float(data.get("created_at", _utcnow()))
        return obj

    def _summary_data(self) -> dict[str, Any]:
        """Return a structured summary of the falsification loop state."""
        refuted = sum(
            1 for s in self.hypothesis_status.values() if s == "refuted"
        )
        supported = sum(
            1 for s in self.hypothesis_status.values() if s == "supported"
        )
        return {
            "loop_id": self.loop_id,
            "attempt_count": len(self.falsification_attempts),
            "counterexample_count": len(self.counterexamples),
            "budget_used": self.budget_used,
            "budget_total": self.budget_total,
            "budget_remaining": max(0, self.budget_total - self.budget_used),
            "falsification_rate": round(self.compute_falsification_rate(), 4),
            "hypotheses_total": len(self.hypothesis_status),
            "hypotheses_refuted": refuted,
            "hypotheses_supported": supported,
            "is_exhausted": self.is_exhausted(),
        }

    def summarize(self) -> str:
        """Return a human-readable summary of the falsification loop state."""
        data = self._summary_data()
        return (
            "FalsificationLoop("
            f"loop_id={data['loop_id']}, "
            f"attempt_count={data['attempt_count']}, "
            f"counterexample_count={data['counterexample_count']}, "
            f"falsification_rate={data['falsification_rate']})"
        )
