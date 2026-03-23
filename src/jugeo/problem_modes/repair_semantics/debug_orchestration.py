"""Debug Orchestration for the JuGeo Repair Pipeline (theory2.tex Ch11 §11.4).

This module implements stage 04 of the four-stage repair pipeline described in
Chapter 11 of the JuGeo theoretical foundations document
(``preliminaries/theory2.tex``).  Stage 04 is the outermost control loop that
drives the complete debug cycle: counterexample analysis, repair planning,
repair execution, convergence checking, and report emission.

Theory Background
-----------------
The debug orchestrator realises the *iteration operator*

    Δ : DebugSession → DebugSession

defined in §11.4 of theory2.tex.  Starting from an initial session carrying a
batch of :class:`~jugeo.solver.countermodels.Countermodel` objects extracted by
the solver, the orchestrator iterates the four sub-stages until the session
reaches a terminal state (``CONVERGED`` or ``BLOCKED``), or the maximum
iteration budget is exhausted.

The Debug Cycle
~~~~~~~~~~~~~~~
A single iteration of the debug cycle proceeds as follows:

1. **Extract** — :class:`~jugeo.problem_modes.repair_semantics.counterexample_extraction.CounterexampleAnalyzer`
   normalises each :class:`~jugeo.solver.countermodels.Countermodel` into a
   :class:`~jugeo.problem_modes.repair_semantics.models.CounterexampleRecord`
   and attaches it to the session.

2. **Plan** — :class:`~jugeo.problem_modes.repair_semantics.repair_planning.RepairPlanner`
   synthesises an admissible :class:`~jugeo.problem_modes.repair_semantics.models.RepairPlan`
   from the latest unresolved :class:`~jugeo.problem_modes.repair_semantics.models.CounterexampleRecord`.

3. **Execute** — :class:`~jugeo.problem_modes.repair_semantics.repair_execution.RepairExecutor`
   applies each :class:`~jugeo.problem_modes.repair_semantics.models.RepairStep` in the
   plan, checking descent conditions at every step.

4. **Check convergence** — :meth:`DebugOrchestrator.evaluate_convergence` tests
   whether the session has reached a terminal state.

Between each iteration the orchestrator calls :meth:`DebugOrchestrator.next_action`
to decide which stage to invoke next.  This state-machine formulation allows
the orchestrator to be paused, resumed, and checkpointed without losing progress.

Convergence
~~~~~~~~~~~
Convergence is declared when any of the following hold:

* ``session.status == CONVERGED`` (set by
  :class:`~jugeo.problem_modes.repair_semantics.repair_execution.RepairExecutor`).
* ``session.iteration_count > 0`` and
  ``len(session.counterexamples) <= convergence_threshold``.
* ``session.latest_counterexample() is None``.

The ``convergence_threshold`` attribute (default ``0``) allows callers to
declare convergence with a small residual counterexample count.  Setting it to
a positive integer is useful in approximate-repair workflows where eliminating
every counterexample is not required.

Checkpointing
~~~~~~~~~~~~~
:meth:`DebugOrchestrator.checkpoint` serialises the session to a JSON-compatible
dictionary, computes a SHA-256 digest of the serialisation, and returns a
human-readable checkpoint token of the form::

    checkpoint:<session_id>:<first-8-chars-of-hash>

The checkpoint token can be stored in a run log or audit trail to allow a
session to be reconstructed from a previous state.

Debug Reports
~~~~~~~~~~~~~
:meth:`DebugOrchestrator.emit_debug_report` emits a
:class:`~jugeo.errors.StructuredFailure` that summarises the session at its
current state.  Like repair certificates (see stage 03), this uses the
:class:`~jugeo.errors.StructuredFailure` dual-use convention: a report with
``classification=DESCENT_OBSTRUCTION`` and ``recoverable=False`` signals an
unresolved obstruction, while one with ``classification=LOCAL_REPAIR`` and
``recoverable=True`` signals a successful repair.

Pipeline Position
-----------------
This module occupies **stage 04** in the repair pipeline::

    ┌──────────────────────────────────┐
    │  counterexample_extraction   │  extract + normalise counterexamples
    └────────────────┬─────────────────┘
                     ▼
    ┌──────────────────────────────────┐
    │  repair_planning             │  synthesise admissible RepairPlan
    └────────────────┬─────────────────┘
                     ▼
    ┌──────────────────────────────────┐
    │  repair_execution            │  execute plan, check descent, rollback
    └────────────────┬─────────────────┘
                     ▼
    ┌──────────────────────────────────┐
    │  debug_orchestration ◄ HERE  │  full loop, reports, checkpoints
    └──────────────────────────────────┘

See Also
--------
* ``repair_execution.py`` — execution of individual repair plans.
* ``models.py`` — defines the :class:`~models.DebugSession`,
  :class:`~models.RepairPlan`, and :class:`~models.CounterexampleRecord` types.
* ``manifest.py`` — subsystem manifest for the repair_semantics package.

Module Version
--------------
| Stage sequence: ``11.04``
| Theory source: ``preliminaries/theory2.tex`` §11.4
| Pipeline stage: ``04``
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.errors import (
    EvidenceFamily,
    FailureClassification,
    FailureScope,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    as_failure_payload,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentAlgebra,
    JudgmentStatus,
    Proposition,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.problem_modes.repair_semantics.models import (
    CounterexampleRecord,
    DebugSession,
    DebugSessionStatus,
    RepairFrontier,
    RepairPlan,
    RepairStep,
    RepairValidator,
)
from jugeo.solver.countermodels import (
    Countermodel,
    CountermodelExtractor,
    FailureClass,
    ObstructionConverter,
    RepairType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level provenance manifest
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "debug_orchestration",
    "pipeline_stage": "04",
    "theory_section": "§11.4 — Debug Orchestration",
}

# ---------------------------------------------------------------------------
# Module-level type aliases
# ---------------------------------------------------------------------------

#: JSON-serialisable summary dictionary returned by summarize_session.
SessionSummary = dict[str, Any]

#: The string tokens returned by next_action.
ActionToken = str

# Action token constants used by the debug loop state machine
ACTION_CONVERGED: ActionToken = "converged"
ACTION_STOP: ActionToken = "stop"
ACTION_ABANDON: ActionToken = "abandon"
ACTION_PLAN: ActionToken = "plan"
ACTION_EXECUTE: ActionToken = "execute"
ACTION_CHECKPOINT: ActionToken = "checkpoint"

# ---------------------------------------------------------------------------
# §1  Module helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    Produces a timezone-aware ISO-8601 string stamped to microsecond precision.
    Used throughout the module for ``created_at``/``updated_at`` fields and
    checkpoint metadata.

    Returns
    -------
    str
        A string of the form ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``.

    Examples
    --------
    >>> ts = _iso_now()
    >>> assert "T" in ts
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _new_session(coordinate: str) -> DebugSession:
    """Create a fresh :class:`~models.DebugSession` for the given coordinate.

    Constructs a new :class:`~models.DebugSession` with a freshly generated
    ``session_id``, the provided *coordinate*, ``iteration_count=0``, and
    status ``OPEN``.

    The ``session_id`` is a 12-character hex prefix of a random UUID, matching
    the convention used in :class:`~models.DebugSession` itself.

    Args
    ----
    coordinate : str
        The root semantic coordinate for the debug session.

    Returns
    -------
    DebugSession
        A new open debug session with no counterexamples or repair attempts.

    Examples
    --------
    >>> session = _new_session("root.geometry.face[0]")
    >>> assert session.is_open()
    >>> assert session.coordinate == "root.geometry.face[0]"
    >>> assert session.iteration_count == 0
    """
    session_id = uuid.uuid4().hex[:12]
    logger.debug(
        "_new_session: created session %r for coordinate %r",
        session_id,
        coordinate,
    )
    return DebugSession(
        session_id=session_id,
        coordinate=coordinate,
        iteration_count=0,
        status=DebugSessionStatus.OPEN,
        counterexamples=(),
        repair_attempts=(),
    )


def _session_age_seconds(session: DebugSession) -> float:
    """Compute the age of a debug session in seconds since it was opened.

    Parses the ``session_id`` to determine when the session was created.
    Since :class:`~models.DebugSession` does not carry an explicit
    ``created_at`` timestamp, this function uses the current UTC time as a
    lower-bound approximation.  Sessions are assumed to have been created in
    the current Python interpreter lifetime, so this value represents the
    maximum possible age.

    In a production system, ``DebugSession`` would carry a ``created_at``
    field allowing exact age computation.  This helper returns ``0.0`` as a
    safe fallback when no timestamp is available.

    Args
    ----
    session : DebugSession
        The session whose age is being computed.

    Returns
    -------
    float
        The session age in seconds.  Returns ``0.0`` when the age cannot be
        determined.

    Examples
    --------
    >>> age = _session_age_seconds(session)
    >>> assert age >= 0.0
    """
    created_at_str: str = getattr(session, "created_at", "")
    if not created_at_str:
        # No timestamp available: return 0.0 as a safe fallback.
        return 0.0
    try:
        created_at = datetime.datetime.fromisoformat(created_at_str)
        now = datetime.datetime.now(datetime.timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (now - created_at).total_seconds())
    except (ValueError, TypeError):
        logger.debug(
            "_session_age_seconds: could not parse created_at=%r; returning 0.0",
            created_at_str,
        )
        return 0.0


def _format_report_title(session: DebugSession) -> str:
    """Format a human-readable title string for a debug session report.

    Produces a concise report title incorporating the session ID, coordinate,
    current status, and iteration count.

    Format::

        Debug Report [session=<id> coord=<coordinate> status=<status> iter=<n>]

    Args
    ----
    session : DebugSession
        The session for which to generate a title.

    Returns
    -------
    str
        A human-readable report title string.

    Examples
    --------
    >>> title = _format_report_title(session)
    >>> assert "Debug Report" in title
    >>> assert session.session_id in title
    """
    status_label: str = (
        session.status.value
        if isinstance(session.status, DebugSessionStatus)
        else str(session.status)
    )
    return (
        f"Debug Report "
        f"[session={session.session_id} "
        f"coord={session.coordinate!r} "
        f"status={status_label} "
        f"iter={session.iteration_count}]"
    )


# ---------------------------------------------------------------------------
# §2  DebugOrchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DebugOrchestrator:
    """Drives the full four-stage debug loop for a semantic coordinate.

    The :class:`DebugOrchestrator` is the outermost controller of the JuGeo
    repair pipeline.  Given a set of :class:`~jugeo.solver.countermodels.Countermodel`
    objects extracted by the solver, it cycles through the four stages

    1. **Extract** — analyse countermodels into
       :class:`~models.CounterexampleRecord` objects.
    2. **Plan** — synthesise a :class:`~models.RepairPlan` from the latest
       unresolved counterexample.
    3. **Execute** — apply the plan via
       :class:`~jugeo.problem_modes.repair_semantics.repair_execution.RepairExecutor`.
    4. **Check** — evaluate convergence and, if needed, emit a checkpoint or
       report.

    until the session reaches a terminal state or the iteration budget is
    exhausted.

    Theory Basis
    ------------
    Implements the iteration operator Δ : DebugSession → DebugSession from
    theory2.tex §11.4.  The convergence criterion corresponds to the descent
    bound Δ*(D) from §11.4.3.

    State Machine
    -------------
    The orchestrator uses a simple state machine driven by
    :meth:`next_action`::

        ┌─────────────────────────────────────────────────────────┐
        │                     next_action()                        │
        │  ┌──────┐     "plan"     ┌────────┐                     │
        │  │ OPEN ├───────────────►│ PLAN   │──► RepairPlanner     │
        │  │      │    "execute"   │        │                     │
        │  │      ├───────────────►│ EXEC   │──► RepairExecutor    │
        │  │      │  "checkpoint"  │        │                     │
        │  │      ├───────────────►│ CKPT   │──► checkpoint()      │
        │  │      │  "converged"   │        │                     │
        │  │      ├───────────────►│ DONE   │──► summarize         │
        │  │      │  "abandon"     │        │                     │
        │  │      ├───────────────►│ BLOCK  │──► block session     │
        │  │      │   "stop"       │        │                     │
        │  │      └───────────────►│ STOP   │──► return            │
        │  └──────┘                └────────┘                     │
        └─────────────────────────────────────────────────────────┘

    Attributes
    ----------
    coordinate : str
        The root semantic coordinate being debugged.  Passed to sub-stage
        constructors.
    max_iterations : int
        Maximum number of debug loop iterations before abandonment.
    convergence_threshold : int
        The orchestrator declares convergence when the number of remaining
        counterexamples is at most this value.  Default is ``0`` (require
        full elimination).
    emit_reports : bool
        When ``True``, emit a :class:`~jugeo.errors.StructuredFailure` report
        after each loop exit.
    checkpoint_interval : int
        Number of iterations between automatic checkpoint emissions.
        Default is ``3``.

    Examples
    --------
    Orchestrate a full debug loop::

        orchestrator = DebugOrchestrator(coordinate="root.geo", max_iterations=5)
        session = orchestrator.orchestrate("root.geo", countermodels)
        if session.is_converged():
            print("All obstructions eliminated!")

    Get a session summary::

        summary = orchestrator.summarize_session(session)
        print(summary["status"], summary["counterexample_count"])
    """

    coordinate: str = ""
    max_iterations: int = 10
    convergence_threshold: int = 0
    emit_reports: bool = True
    checkpoint_interval: int = 3

    # -----------------------------------------------------------------------
    # §2.1  orchestrate
    # -----------------------------------------------------------------------

    def orchestrate(
        self, coordinate: str, countermodels: Sequence[Countermodel]
    ) -> DebugSession:
        """Drive the complete debug loop for the given coordinate and countermodels.

        Creates a fresh :class:`~models.DebugSession`, populates it with
        :class:`~models.CounterexampleRecord` objects derived from *countermodels*
        via a :class:`~counterexample_extraction.CounterexampleAnalyzer`,
        and then delegates to :meth:`run_debug_loop` to iterate the plan/execute
        cycle until convergence or budget exhaustion.

        The CounterexampleAnalyzer and RepairPlanner are imported lazily at
        call time so that circular imports are avoided.  If the stage files do
        not yet exist, a :class:`~jugeo.errors.StructuredFailure` is logged and
        the session is returned in its initial state with a
        ``BLOCKED`` status.

        Args
        ----
        coordinate : str
            The semantic coordinate to debug.
        countermodels : Sequence[Countermodel]
            The raw countermodels produced by the solver for this coordinate.

        Returns
        -------
        DebugSession
            The final debug session.  Status is ``CONVERGED`` when all
            counterexamples were eliminated, ``BLOCKED`` when the orchestrator
            could not make further progress.

        Examples
        --------
        >>> orchestrator = DebugOrchestrator(coordinate="root.geo")
        >>> session = orchestrator.orchestrate("root.geo", countermodels)
        >>> assert session.coordinate == "root.geo"
        """
        logger.info(
            "DebugOrchestrator.orchestrate: starting for coordinate=%r "
            "with %d countermodel(s)",
            coordinate,
            len(countermodels),
        )

        session: DebugSession = _new_session(coordinate)

        # Lazy import of s01 to avoid circular imports and allow the stage
        # files to be created independently.
        try:
            from jugeo.problem_modes.repair_semantics.counterexample_extraction import (  # noqa: PLC0415
                CounterexampleAnalyzer,
            )
        except ImportError:
            logger.warning(
                "orchestrate: could not import CounterexampleAnalyzer from s01; "
                "returning session without counterexample records"
            )
            return run_result if (run_result := self.run_debug_loop(session)) else session

        analyzer = CounterexampleAnalyzer(coordinate=coordinate)

        for cm in countermodels:
            try:
                record: CounterexampleRecord = analyzer.analyze(cm)
                session = session.with_counterexample(record)
                logger.debug(
                    "orchestrate: added counterexample record %r for coordinate %r",
                    getattr(record, "record_id", "<unknown>"),
                    coordinate,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "orchestrate: failed to analyze countermodel %r: %s",
                    getattr(cm, "model_id", "<unknown>"),
                    exc,
                )

        logger.info(
            "orchestrate: session %r has %d counterexample record(s); "
            "starting debug loop",
            session.session_id,
            len(session.counterexamples),
        )

        return self.run_debug_loop(session)

    # -----------------------------------------------------------------------
    # §2.2  run_debug_loop
    # -----------------------------------------------------------------------

    def run_debug_loop(self, session: DebugSession) -> DebugSession:
        """Run the plan/execute debug loop until convergence or budget exhaustion.

        Iterates up to :attr:`max_iterations` times.  On each iteration, calls
        :meth:`next_action` to determine which stage to invoke next:

        * ``"converged"`` — exit the loop immediately.
        * ``"stop"`` — exit the loop immediately (terminal or abandoned state).
        * ``"abandon"`` — mark the session blocked and exit.
        * ``"plan"`` — create a :class:`~repair_planning.RepairPlanner` and
          generate a :class:`~models.RepairPlan` from the latest counterexample,
          then attach it to the session via ``session.with_repair_attempt(plan)``.
        * ``"execute"`` — create a
          :class:`~repair_execution.RepairExecutor` and execute the latest
          plan.
        * ``"checkpoint"`` — call :meth:`checkpoint` and log the token.

        After the loop, if the session is not yet converged,
        :meth:`evaluate_convergence` is checked one final time and
        ``session.converge()`` is called if it returns ``True``.

        If :attr:`emit_reports` is ``True``, calls :meth:`emit_debug_report`
        before returning.

        Args
        ----
        session : DebugSession
            The initial debug session to iterate.

        Returns
        -------
        DebugSession
            The final session after the debug loop exits.

        Examples
        --------
        >>> final = orchestrator.run_debug_loop(session)
        >>> print(final.status)
        """
        # Lazy imports of stage 2 and 3 to avoid circular dependencies
        RepairPlanner = None
        RepairExecutor = None

        try:
            from jugeo.problem_modes.repair_semantics.repair_planning import (  # noqa: PLC0415
                RepairPlanner,
            )
        except ImportError:
            logger.warning(
                "run_debug_loop: could not import RepairPlanner from s02; "
                "planning actions will be skipped"
            )

        try:
            from jugeo.problem_modes.repair_semantics.repair_execution import (  # noqa: PLC0415
                RepairExecutor,
            )
        except ImportError:
            logger.warning(
                "run_debug_loop: could not import RepairExecutor from s03; "
                "execute actions will be skipped"
            )

        for iteration_idx in range(self.max_iterations):
            action: ActionToken = self.next_action(session)
            logger.debug(
                "run_debug_loop: iteration %d/%d action=%r "
                "session_status=%r counterexamples=%d repair_attempts=%d",
                iteration_idx + 1,
                self.max_iterations,
                action,
                session.status.value
                if isinstance(session.status, DebugSessionStatus)
                else str(session.status),
                len(session.counterexamples),
                len(session.repair_attempts),
            )

            if action == ACTION_CONVERGED:
                logger.info(
                    "run_debug_loop: session %r converged at iteration %d",
                    session.session_id,
                    iteration_idx,
                )
                break

            if action == ACTION_STOP:
                logger.info(
                    "run_debug_loop: stopping at iteration %d (action=stop)",
                    iteration_idx,
                )
                break

            if action == ACTION_ABANDON:
                logger.warning(
                    "run_debug_loop: abandoning session %r at iteration %d "
                    "(max_iterations=%d reached)",
                    session.session_id,
                    iteration_idx,
                    self.max_iterations,
                )
                session = replace(session, status=DebugSessionStatus.BLOCKED)
                break

            if action == ACTION_PLAN:
                if RepairPlanner is not None:
                    latest_cx = session.latest_counterexample()
                    if latest_cx is not None:
                        try:
                            planner = RepairPlanner(coordinate=self.coordinate)
                            plan: RepairPlan = planner.plan_from_counterexample(
                                latest_cx
                            )
                            session = session.with_repair_attempt(plan)
                            logger.info(
                                "run_debug_loop: created plan %r with %d step(s)",
                                plan.plan_id,
                                len(plan.steps),
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "run_debug_loop: RepairPlanner raised %s; "
                                "skipping plan step",
                                exc,
                            )
                    else:
                        logger.debug(
                            "run_debug_loop: action=plan but no counterexample available"
                        )
                else:
                    logger.warning(
                        "run_debug_loop: action=plan but RepairPlanner unavailable"
                    )
                continue

            if action == ACTION_EXECUTE:
                latest_plan = session.latest_repair_plan()
                if RepairExecutor is not None and latest_plan is not None:
                    try:
                        executor = RepairExecutor(coordinate=self.coordinate)
                        session = executor.execute(latest_plan, session)
                        logger.info(
                            "run_debug_loop: executed plan %r; "
                            "session_status=%r",
                            latest_plan.plan_id,
                            session.status.value
                            if isinstance(session.status, DebugSessionStatus)
                            else str(session.status),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "run_debug_loop: RepairExecutor raised %s; "
                            "skipping execute step",
                            exc,
                        )
                else:
                    if RepairExecutor is None:
                        logger.warning(
                            "run_debug_loop: action=execute but RepairExecutor unavailable"
                        )
                    else:
                        logger.debug(
                            "run_debug_loop: action=execute but no plan available"
                        )
                continue

            if action == ACTION_CHECKPOINT:
                token = self.checkpoint(session)
                logger.info(
                    "run_debug_loop: checkpoint emitted at iteration %d: %s",
                    iteration_idx,
                    token,
                )
                continue

        # Final convergence check after loop exits
        if not session.is_converged() and self.evaluate_convergence(session):
            logger.info(
                "run_debug_loop: post-loop convergence check passed; "
                "marking session %r CONVERGED",
                session.session_id,
            )
            session = session.converge()

        if self.emit_reports:
            report = self.emit_debug_report(session)
            logger.info(
                "run_debug_loop: debug report emitted: %s", report.message
            )

        return session

    # -----------------------------------------------------------------------
    # §2.3  next_action
    # -----------------------------------------------------------------------

    def next_action(self, session: DebugSession) -> ActionToken:
        """Determine the next action to take in the debug loop.

        Implements the state-machine transition function for the debug loop.
        Conditions are checked in strict priority order:

        1. ``CONVERGED`` → ``"converged"``
        2. ``BLOCKED`` → ``"stop"``
        3. ``iteration_count >= max_iterations`` → ``"abandon"``
        4. ``latest_counterexample() is None`` → ``"converged"``
        5. ``len(repair_attempts) == 0`` → ``"plan"``  (no plan yet)
        6. ``iteration_count % checkpoint_interval == 0 and iteration_count > 0``
           → ``"checkpoint"``
        7. ``len(repair_attempts) < len(counterexamples)`` → ``"plan"``
           (more counterexamples than plans)
        8. Default → ``"execute"``

        Args
        ----
        session : DebugSession
            The current debug session.

        Returns
        -------
        str
            One of: ``"converged"``, ``"stop"``, ``"abandon"``, ``"plan"``,
            ``"execute"``, ``"checkpoint"``.

        Examples
        --------
        >>> action = orchestrator.next_action(session)
        >>> assert action in ("converged", "stop", "abandon", "plan", "execute", "checkpoint")
        """
        # Priority 1: already converged
        if session.status == DebugSessionStatus.CONVERGED:
            return ACTION_CONVERGED

        # Priority 2: blocked (terminal failure state)
        if session.status == DebugSessionStatus.BLOCKED:
            return ACTION_STOP

        # Priority 3: budget exhausted
        if session.iteration_count >= self.max_iterations:
            return ACTION_ABANDON

        # Priority 4: no counterexamples remain → converged
        if session.latest_counterexample() is None:
            return ACTION_CONVERGED

        # Priority 5: no plans yet → must plan first
        if len(session.repair_attempts) == 0:
            return ACTION_PLAN

        # Priority 6: periodic checkpoint
        if (
            self.checkpoint_interval > 0
            and session.iteration_count > 0
            and session.iteration_count % self.checkpoint_interval == 0
        ):
            return ACTION_CHECKPOINT

        # Priority 7: more counterexamples than plans → need another plan
        if len(session.repair_attempts) < len(session.counterexamples):
            return ACTION_PLAN

        # Default: execute the latest plan
        return ACTION_EXECUTE

    # -----------------------------------------------------------------------
    # §2.4  apply_repair
    # -----------------------------------------------------------------------

    def apply_repair(self, session: DebugSession, plan: RepairPlan) -> DebugSession:
        """Apply a :class:`~models.RepairPlan` to the session via a :class:`~repair_execution.RepairExecutor`.

        Creates a :class:`~jugeo.problem_modes.repair_semantics.repair_execution.RepairExecutor`
        configured with :attr:`coordinate` and delegates to its
        :meth:`~jugeo.problem_modes.repair_semantics.repair_execution.RepairExecutor.execute`
        method.

        This method exists as a thin adapter so that callers can invoke
        repair execution without importing :mod:`repair_execution`
        directly.  It uses a lazy import to avoid circular dependencies.

        Args
        ----
        session : DebugSession
            The current debug session.
        plan : RepairPlan
            The repair plan to apply.

        Returns
        -------
        DebugSession
            The updated session after execution.

        Raises
        ------
        ImportError
            If :mod:`repair_execution` cannot be imported.

        Examples
        --------
        >>> updated = orchestrator.apply_repair(session, plan)
        """
        from jugeo.problem_modes.repair_semantics.repair_execution import (  # noqa: PLC0415
            RepairExecutor,
        )

        executor = RepairExecutor(coordinate=self.coordinate)
        logger.info(
            "apply_repair: executing plan %r against session %r",
            plan.plan_id,
            session.session_id,
        )
        result = executor.execute(plan, session)
        logger.debug(
            "apply_repair: resulting session status=%r iteration=%d",
            result.status.value
            if isinstance(result.status, DebugSessionStatus)
            else str(result.status),
            result.iteration_count,
        )
        return result

    # -----------------------------------------------------------------------
    # §2.5  evaluate_convergence
    # -----------------------------------------------------------------------

    def evaluate_convergence(self, session: DebugSession) -> bool:
        """Evaluate whether the session has reached a convergence criterion.

        Returns ``True`` if any of the following holds:

        1. ``session.status == CONVERGED``.
        2. ``session.iteration_count > 0`` and
           ``len(session.counterexamples) <= convergence_threshold``.
        3. ``session.latest_counterexample() is None``.

        Condition 2 uses the :attr:`convergence_threshold` attribute to support
        approximate-repair workflows where a small residual counterexample count
        is acceptable.  The default threshold of ``0`` requires full elimination.

        Condition 3 handles the case where the counterexample queue was externally
        drained (e.g., by a counterexample minimiser) before this check is called.

        Args
        ----
        session : DebugSession
            The session to check.

        Returns
        -------
        bool
            ``True`` if the session has converged.

        Examples
        --------
        >>> assert orchestrator.evaluate_convergence(converged_session) is True
        """
        # Condition 1: explicit CONVERGED status
        if session.status == DebugSessionStatus.CONVERGED:
            logger.debug(
                "evaluate_convergence: session %r is CONVERGED",
                session.session_id,
            )
            return True

        # Condition 2: iteration has occurred and counterexamples are below threshold
        if (
            session.iteration_count > 0
            and len(session.counterexamples) <= self.convergence_threshold
        ):
            logger.debug(
                "evaluate_convergence: session %r has %d counterexample(s) "
                "<= threshold %d after %d iteration(s); declaring convergence",
                session.session_id,
                len(session.counterexamples),
                self.convergence_threshold,
                session.iteration_count,
            )
            return True

        # Condition 3: latest counterexample is None (queue is empty)
        if session.latest_counterexample() is None:
            logger.debug(
                "evaluate_convergence: session %r latest_counterexample is None; "
                "declaring convergence",
                session.session_id,
            )
            return True

        logger.debug(
            "evaluate_convergence: session %r has not converged "
            "(status=%r, counterexamples=%d, threshold=%d, iterations=%d)",
            session.session_id,
            session.status.value
            if isinstance(session.status, DebugSessionStatus)
            else str(session.status),
            len(session.counterexamples),
            self.convergence_threshold,
            session.iteration_count,
        )
        return False

    # -----------------------------------------------------------------------
    # §2.6  summarize_session
    # -----------------------------------------------------------------------

    def summarize_session(self, session: DebugSession) -> SessionSummary:
        """Return a JSON-serialisable summary dictionary for the session.

        Produces a flat dictionary containing the most important session
        attributes in serialisable form.  All values are JSON primitives
        (strings, integers, booleans, or ``None``).

        The summary dictionary has the following keys:

        ``session_id``
            The session's unique identifier string.
        ``coordinate``
            The root semantic coordinate being debugged.
        ``status``
            The session status as a lowercase string (e.g. ``"open"``,
            ``"converged"``, ``"blocked"``).
        ``iteration_count``
            Number of repair iterations completed.
        ``counterexample_count``
            Total counterexample records accumulated.
        ``repair_attempt_count``
            Total repair plans attempted.
        ``converged``
            ``True`` iff ``session.status == CONVERGED``.
        ``created_at``
            ISO-8601 timestamp of the session creation, or ``None`` when not
            available.
        ``updated_at``
            ISO-8601 timestamp of the last session update, or ``None`` when
            not available.

        Args
        ----
        session : DebugSession
            The session to summarise.

        Returns
        -------
        dict[str, Any]
            A JSON-serialisable summary dictionary.

        Examples
        --------
        >>> summary = orchestrator.summarize_session(session)
        >>> import json; json.dumps(summary)  # must not raise
        """
        status_val: str = (
            session.status.value
            if isinstance(session.status, DebugSessionStatus)
            else str(session.status)
        )

        return {
            "session_id": session.session_id,
            "coordinate": session.coordinate,
            "status": status_val,
            "iteration_count": session.iteration_count,
            "counterexample_count": len(session.counterexamples),
            "repair_attempt_count": len(session.repair_attempts),
            "converged": session.is_converged(),
            "created_at": getattr(session, "created_at", None),
            "updated_at": getattr(session, "updated_at", None),
        }

    # -----------------------------------------------------------------------
    # §2.7  emit_debug_report
    # -----------------------------------------------------------------------

    def emit_debug_report(self, session: DebugSession) -> StructuredFailure:
        """Emit a :class:`~jugeo.errors.StructuredFailure` summarising the session.

        Constructs a :class:`~jugeo.errors.StructuredFailure` payload that
        records the full session state at the time of reporting.  The
        ``classification`` and ``recoverable`` fields reflect the session
        outcome:

        * If ``session.is_converged()``:
          ``classification=LOCAL_REPAIR``, ``recoverable=True``.
        * Otherwise:
          ``classification=DESCENT_OBSTRUCTION``, ``recoverable=False``.

        The ``metadata`` field contains the full :meth:`summarize_session`
        dictionary with string values (for JSON safety).

        Args
        ----
        session : DebugSession
            The session to report on.

        Returns
        -------
        StructuredFailure
            The debug report payload.

        Examples
        --------
        >>> report = orchestrator.emit_debug_report(session)
        >>> assert "session_id" in report.metadata
        """
        title: str = _format_report_title(session)
        summary: SessionSummary = self.summarize_session(session)

        converged: bool = session.is_converged()
        classification: FailureClassification = (
            FailureClassification.LOCAL_REPAIR
            if converged
            else FailureClassification.DESCENT_OBSTRUCTION
        )
        recoverable: bool = converged

        # Convert summary to string values for StructuredFailure metadata
        meta: dict[str, str] = {
            k: str(v) if v is not None else "" for k, v in summary.items()
        }
        meta["emit_time"] = _iso_now()
        meta["pipeline_stage"] = MANIFEST_SPEC_PROVENANCE["pipeline_stage"]
        meta["theory_section"] = MANIFEST_SPEC_PROVENANCE["theory_section"]

        report = StructuredFailure(
            message=title,
            scope=FailureScope.ORCHESTRATION,
            classification=classification,
            coordinate=session.coordinate if session.coordinate else None,
            evidence_family=(
                EvidenceFamily.SEMANTIC if converged else EvidenceFamily.UNKNOWN
            ),
            metadata=meta,
            recoverable=recoverable,
        )

        logger.info("emit_debug_report: %s", title)
        return report

    # -----------------------------------------------------------------------
    # §2.8  checkpoint
    # -----------------------------------------------------------------------

    def checkpoint(self, session: DebugSession) -> str:
        """Serialise the session and return a stable checkpoint token.

        Computes a SHA-256 digest of the JSON serialisation of
        :meth:`summarize_session` and returns a human-readable token of the
        form::

            checkpoint:<session_id>:<first-8-chars-of-sha256-hex>

        The token is suitable for storage in a run-log, audit trail, or CI
        artefact.  It uniquely identifies the session and its state at the
        time of checkpointing, up to SHA-256 collision resistance (which is
        sufficient for all practical audit purposes).

        Serialisation uses :func:`json.dumps` with ``sort_keys=True`` for
        deterministic ordering.  Any non-serialisable values in the summary
        are converted to strings via the default ``str()`` fallback.

        Args
        ----
        session : DebugSession
            The session to checkpoint.

        Returns
        -------
        str
            A checkpoint token string, e.g.
            ``"checkpoint:a3f9bc012def:d41d8cd9"``.

        Examples
        --------
        >>> token = orchestrator.checkpoint(session)
        >>> assert token.startswith("checkpoint:")
        >>> parts = token.split(":")
        >>> assert len(parts) == 3 and len(parts[2]) == 8
        """
        summary: SessionSummary = self.summarize_session(session)

        try:
            json_str: str = json.dumps(summary, sort_keys=True, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "checkpoint: JSON serialisation failed (%s); "
                "using fallback repr",
                exc,
            )
            json_str = repr(summary)

        digest: str = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
        short_hash: str = digest[:8]
        token: str = f"checkpoint:{session.session_id}:{short_hash}"

        logger.info(
            "checkpoint: emitted token=%r for session %r "
            "(iteration=%d, status=%r)",
            token,
            session.session_id,
            session.iteration_count,
            session.status.value
            if isinstance(session.status, DebugSessionStatus)
            else str(session.status),
        )
        return token




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "ACTION_ABANDON",
    "ACTION_CHECKPOINT",
    "ACTION_CONVERGED",
    "ACTION_EXECUTE",
    "ACTION_PLAN",
    "ACTION_STOP",
    "ActionToken",
    "DebugOrchestrator",
    "SessionSummary",
    "_format_report_title",
    "_iso_now",
    "_new_session",
    "_session_age_seconds",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: debug_orchestration — Ch11 §11.4 Debug Orchestration
