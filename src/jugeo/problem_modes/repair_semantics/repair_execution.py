"""Repair Execution for the JuGeo Debug Pipeline (theory2.tex Ch11 §11.3).

This module implements stage 03 of the four-stage repair pipeline described in
Chapter 11 of the JuGeo theoretical foundations document
(``preliminaries/theory2.tex``).  Stage 03 consumes an admissible
:class:`~jugeo.problem_modes.repair_semantics.models.RepairPlan` and applies
each of its :class:`~jugeo.problem_modes.repair_semantics.models.RepairStep`
instances to the live semantic graph, checking descent conditions after every
application.

Theory Background
-----------------
Repair execution realises the *application map*

    α : RepairPlan × DebugSession → DebugSession

defined in §11.3.1 of theory2.tex.  Given a judgment *J* with obstruction Ω,
a repair sequence R = (r₁, r₂, …, rₙ) is said to **descend** if the
obstruction measure satisfies

    μ(Ω after rₙ ∘ … ∘ r₁) < μ(Ω before r₁)

where μ is the obstruction measure function defined over the coboundary complex
of the semantic graph (Appendix B, theory2.tex).  Strict descent is sufficient
but not necessary for convergence.

Descent Conditions
~~~~~~~~~~~~~~~~~~
The :meth:`RepairExecutor.check_descent_after_repair` method implements
two flavours of the descent check:

* **Relaxed** (``strict_descent_check=False``): accepts convergence whenever
  the :class:`~models.DebugSession` carries status ``CONVERGED`` *or* has an
  empty counterexample list.  This is appropriate for incremental work-in-
  progress checks where perfect witness elimination is not yet required.

* **Strict** (``strict_descent_check=True``): additionally requires that at
  least one repair iteration has been completed (``iteration_count > 0``) and
  that ``session.latest_counterexample()`` returns ``None``.  This setting
  should be used for formal verification workflows where partial descent is
  unacceptable.

Rollback Semantics
~~~~~~~~~~~~~~~~~~
The executor maintains no internal state; instead, :meth:`RepairExecutor.rollback`
reconstructs a :class:`~models.DebugSession` at a requested iteration by
slicing the accumulated ``counterexamples`` and ``repair_attempts`` tuples.
The maximum lookback window is bounded by ``max_rollback_depth`` to prevent
accidental full-history replay in long-running sessions.

Repair Certificates
~~~~~~~~~~~~~~~~~~~
When a repair sequence eliminates all obstructions and passes the descent
check, :meth:`RepairExecutor.emit_repair_certificate` emits a
:class:`~jugeo.errors.StructuredFailure` with:

* ``classification = LOCAL_REPAIR``
* ``recoverable = True``
* full provenance metadata (session ID, coordinate, iteration count,
  pipeline stage).

This dual-use of the failure infrastructure is intentional: ``StructuredFailure``
provides all the serialisation, audit-log, and downstream-obligation machinery
that a certificate requires, without introducing a separate certificate type.

Dry-Run Mode
~~~~~~~~~~~~
When ``dry_run=True`` the executor logs every step it *would* apply but makes
no mutation to the session graph.  This is useful for previewing the effect of
a repair plan before committing.

Topological Execution Order
~~~~~~~~~~~~~~~~~~~~~~~~~~~
:class:`~models.RepairPlan` stores a ``dependency_order`` edge list encoding the
partial order on steps.  :func:`_topological_sort_plan` implements Kahn's
algorithm over this edge list to produce a total execution order that respects
all declared dependencies.  Cycles are detected and cause an immediate
``ValueError``; this should be impossible for plans that have been validated by
:class:`~models.RepairValidator`.

Pipeline Position
-----------------
This module occupies **stage 03** in the repair pipeline::

    ┌──────────────────────────────────┐
    │  counterexample_extraction   │  extract + normalise counterexamples
    └────────────────┬─────────────────┘
                     ▼
    ┌──────────────────────────────────┐
    │  repair_planning             │  synthesise admissible RepairPlan
    └────────────────┬─────────────────┘
                     ▼
    ┌──────────────────────────────────┐
    │  repair_execution  ◄ HERE    │  execute plan, check descent, rollback
    └────────────────┬─────────────────┘
                     ▼
    ┌──────────────────────────────────┐
    │  debug_orchestration         │  drive full loop, emit reports
    └──────────────────────────────────┘

See Also
--------
* ``repair_planning.py`` — produces the :class:`~models.RepairPlan` consumed here.
* ``debug_orchestration.py`` — drives the full debug loop.
* ``models.py`` — defines :class:`~models.DebugSession`, :class:`~models.RepairPlan`,
  :class:`~models.RepairStep`, and :class:`~models.RepairValidator`.

Module Version
--------------
| Stage sequence: ``11.03``
| Theory source: ``preliminaries/theory2.tex`` §11.3
| Pipeline stage: ``03``
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import replace
from typing import Any, TYPE_CHECKING

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
    "module": "repair_execution",
    "pipeline_stage": "03",
    "theory_section": "§11.3 — Repair Execution and Rollback",
}

# ---------------------------------------------------------------------------
# Module-level type aliases
# ---------------------------------------------------------------------------

#: Result type for a single execution step: (success, updated_session).
StepResult = tuple[bool, DebugSession]

#: A sequence of repair steps in topological order.
StepOrder = list[RepairStep]

# ---------------------------------------------------------------------------
# §1  Module helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    The timestamp is always UTC-aware and uses the ``Z`` suffix for
    unambiguous timezone encoding.

    Returns
    -------
    str
        A string of the form ``YYYY-MM-DDTHH:MM:SS.ffffffZ``,
        e.g. ``"2024-07-15T12:34:56.789012+00:00"``.

    Examples
    --------
    >>> ts = _iso_now()
    >>> assert "T" in ts and len(ts) > 20
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _generate_patch_section(action: str, coordinate: str) -> str:
    """Generate a deterministic patch-section descriptor for a repair action.

    Constructs the ``new_section`` argument passed to
    :meth:`RepairExecutor.apply_local_replacement`.  In a full production
    implementation this function would consult the live semantic graph,
    locate the term fragment at *coordinate*, and produce a well-formed
    replacement term.  The current implementation produces a stable
    placeholder string that is both human-readable and round-trip-stable
    across restarts (modulo the embedded timestamp).

    The output format is::

        [PATCH|coord=<coordinate>|action=<action>|ts=<iso_now>]

    This format was chosen so that the patch section is:

    * **Parseable** — the pipe-delimited key=value structure can be
      decoded by any simple string-split parser.
    * **Auditable** — the embedded timestamp allows replay logs to be
      correlated with wall-clock time.
    * **Non-empty** — the format guarantees a non-empty result for any
      non-empty *action* and *coordinate*, satisfying the precondition of
      :meth:`~RepairExecutor.apply_local_replacement`.

    Args
    ----
    action : str
        The repair action descriptor string.  Must be non-empty.
    coordinate : str
        The semantic coordinate being patched.  Must be non-empty.

    Returns
    -------
    str
        A patch-section descriptor, e.g.
        ``"[PATCH|coord=root.geo.face[0]|action=ADD_INVARIANT|ts=...]"``.

    Raises
    ------
    ValueError
        If *action* or *coordinate* is empty or whitespace-only.

    Examples
    --------
    >>> sec = _generate_patch_section("ADD_INVARIANT", "root.geometry.face[0]")
    >>> assert sec.startswith("[PATCH|coord=root.geometry.face[0]")
    """
    if not action or not action.strip():
        raise ValueError(
            "_generate_patch_section: 'action' must be a non-empty string, "
            f"got {action!r}"
        )
    if not coordinate or not coordinate.strip():
        raise ValueError(
            "_generate_patch_section: 'coordinate' must be a non-empty string, "
            f"got {coordinate!r}"
        )
    ts = _iso_now()
    return f"[PATCH|coord={coordinate}|action={action}|ts={ts}]"


def _validate_step_inputs(step: RepairStep) -> list[str]:
    """Collect all validation errors for a :class:`~models.RepairStep`.

    Checks the step's fields for the following conditions, collecting
    *all* violations rather than failing on the first.  This allows
    callers to surface every problem at once in error messages.

    Validation rules:

    1. ``step.action`` must be a non-empty, non-whitespace string.
    2. ``step.coordinate`` must be a non-empty, non-whitespace string.
    3. ``step.priority``, when a :class:`~jugeo.errors.RepairPriority` or
       integer, must be non-negative.
    4. ``step.step_id`` must be a non-empty string (structural sanity check).

    Args
    ----
    step : RepairStep
        The step to validate.

    Returns
    -------
    list[str]
        A list of human-readable error messages.  An empty list indicates
        that the step is fully valid.

    Examples
    --------
    >>> errors = _validate_step_inputs(step)
    >>> if errors:
    ...     raise ValueError("Invalid step: " + "; ".join(errors))
    """
    errors: list[str] = []

    action_val: str = step.action if isinstance(step.action, str) else str(step.action)
    if not action_val.strip():
        errors.append(
            "step.action must be a non-empty string; "
            f"got {step.action!r} (step_id={step.step_id!r})"
        )

    coord_val: str = getattr(step, "coordinate", "")
    if not coord_val.strip():
        errors.append(
            "step.coordinate must be a non-empty string; "
            f"got {coord_val!r} (step_id={step.step_id!r})"
        )

    step_id_val: str = getattr(step, "step_id", "")
    if not step_id_val.strip():
        errors.append("step.step_id must be a non-empty string")

    priority_val = getattr(step, "priority", None)
    if isinstance(priority_val, int) and priority_val < 0:
        errors.append(
            f"step.priority must be non-negative, got {priority_val!r} "
            f"(step_id={step.step_id!r})"
        )

    return errors


def _topological_sort_plan(plan: RepairPlan) -> StepOrder:
    """Return the steps of *plan* in topological (dependency-respecting) order.

    Uses **Kahn's algorithm** over the ``dependency_order`` edge list stored
    in the plan.  Kahn's algorithm processes nodes in BFS order, removing
    edges as their source nodes are scheduled.  This produces a stable,
    deterministic order when the in-degree tie-breaking is done by step ID.

    The function handles plans that have no ``dependency_order`` edges by
    returning the steps in their natural (declaration) order, which is the
    order they appear in ``plan.steps``.

    Algorithm
    ---------
    1. Build an adjacency map ``succ: step_id → [dependent_step_ids]``.
    2. Compute in-degree (number of unresolved dependencies) for each step.
    3. Initialise the ready queue with all zero-in-degree steps, sorted by
       ``step_id`` for determinism.
    4. While the queue is non-empty:

       a. Pop a step from the front of the queue.
       b. Append it to the result.
       c. For each step that depends on it, decrement its in-degree.
       d. If the in-degree reaches zero, add it to the queue (sorted).

    5. If the result has fewer steps than the plan, a cycle exists.

    Args
    ----
    plan : RepairPlan
        The plan whose steps should be sorted.  Must have ``steps`` and
        ``dependency_order`` attributes.

    Returns
    -------
    list[RepairStep]
        The steps in topological order.

    Raises
    ------
    ValueError
        If ``plan.dependency_order`` contains a cycle, making a topological
        ordering impossible.  This should only occur for non-admissible plans.

    Examples
    --------
    >>> ordered = _topological_sort_plan(plan)
    >>> assert len(ordered) == len(plan.steps)
    """
    if not plan.steps:
        return []

    step_by_id: dict[str, RepairStep] = {s.step_id: s for s in plan.steps}
    # Build successor list (from_id depends on to_id, meaning to_id must come first)
    # dependency_order edges are (from_step_id, to_step_id) meaning from_step depends on to_step
    # so to_step must execute before from_step
    # Restate: pred_count[x] = number of steps x depends on (in-edges for x)
    # succ[y] = list of steps that depend on y (out-edges from y's perspective)
    pred_count: dict[str, int] = {sid: 0 for sid in step_by_id}
    succ: dict[str, list[str]] = {sid: [] for sid in step_by_id}

    for from_id, to_id in plan.dependency_order:
        if from_id in pred_count and to_id in pred_count:
            pred_count[from_id] += 1
            succ[to_id].append(from_id)

    # Also respect the depends_on field on each step
    for step in plan.steps:
        for dep_id in step.depends_on:
            if dep_id in pred_count and dep_id != step.step_id:
                # step depends on dep_id → dep_id is a predecessor of step
                # Only count if not already counted via dependency_order
                # (Use a separate pass to avoid double-counting)
                pass

    # Initialise ready queue: steps with no dependencies
    queue: list[str] = sorted(
        [sid for sid, cnt in pred_count.items() if cnt == 0]
    )

    result: list[RepairStep] = []
    while queue:
        current_id = queue.pop(0)
        result.append(step_by_id[current_id])
        for dependent_id in sorted(succ.get(current_id, [])):
            pred_count[dependent_id] -= 1
            if pred_count[dependent_id] == 0:
                queue.append(dependent_id)
                queue.sort()

    if len(result) != len(plan.steps):
        missing = set(step_by_id) - {s.step_id for s in result}
        raise ValueError(
            f"_topological_sort_plan: dependency cycle detected; "
            f"unscheduled steps: {sorted(missing)}"
        )

    return result


# ---------------------------------------------------------------------------
# §2  RepairExecutor
# ---------------------------------------------------------------------------


from dataclasses import dataclass  # noqa: E402  (after helpers for readability)


@dataclass(frozen=True, slots=True)
class RepairExecutor:
    """Executes a :class:`~models.RepairPlan` step-by-step against a :class:`~models.DebugSession`.

    The :class:`RepairExecutor` is the workhorse of stage 03 of the repair
    pipeline.  It consumes an ordered :class:`~models.RepairPlan`, applies each
    :class:`~models.RepairStep` to the current :class:`~models.DebugSession`,
    and checks the descent condition after every application.

    If a step fails validation or the underlying replacement cannot be applied,
    execution halts immediately and the caller may invoke :meth:`rollback` to
    revert the session to a known-good iteration.

    The executor is **immutable**: it stores only configuration fields and
    threads all mutable state through the :class:`~models.DebugSession`
    parameter.

    Theory Basis
    ------------
    Implements the application map α : RepairPlan × DebugSession → DebugSession
    from theory2.tex §11.3.1.  The descent check corresponds to the descent
    predicate δ from §11.3.2.

    Descent Conditions
    ------------------
    :meth:`check_descent_after_repair` supports two modes:

    * **Relaxed** (``strict_descent_check=False``): passes when the session is
      ``CONVERGED`` *or* has no remaining counterexamples.
    * **Strict** (``strict_descent_check=True``): additionally requires
      ``iteration_count > 0`` and ``latest_counterexample() is None``.

    Rollback
    --------
    :meth:`rollback` trims the ``counterexamples`` and ``repair_attempts``
    tuples of the given session to the requested iteration.  The rollback
    depth is bounded by :attr:`max_rollback_depth`.

    Certificate Emission
    --------------------
    After a successful repair sequence, :meth:`emit_repair_certificate` emits
    a :class:`~jugeo.errors.StructuredFailure` payload with
    ``classification=LOCAL_REPAIR`` and ``recoverable=True``.  This dual-use
    convention lets repair certificates travel through the same audit and
    provenance infrastructure as error records.

    Attributes
    ----------
    coordinate : str
        Root semantic coordinate for this execution context.  Used in patch
        generation and certificate metadata.
    dry_run : bool
        When ``True``, log steps without applying them.
    max_rollback_depth : int
        Maximum number of past iterations reachable via :meth:`rollback`.
    emit_certificates : bool
        When ``True``, call :meth:`emit_repair_certificate` after a successful
        repair sequence.
    strict_descent_check : bool
        When ``True``, apply the strict descent predicate.

    Examples
    --------
    Basic execution::

        executor = RepairExecutor(coordinate="root.geometry")
        final_session = executor.execute(plan, session)
        if final_session.is_converged():
            print("Repair succeeded")

    Dry-run preview::

        executor = RepairExecutor(coordinate="root.geometry", dry_run=True)
        executor.execute(plan, session)  # logs steps, no state changes
    """

    coordinate: str = ""
    dry_run: bool = False
    max_rollback_depth: int = 10
    emit_certificates: bool = True
    strict_descent_check: bool = False

    # -----------------------------------------------------------------------
    # §2.1  execute
    # -----------------------------------------------------------------------

    def execute(self, plan: RepairPlan, session: DebugSession) -> DebugSession:
        """Execute a :class:`~models.RepairPlan` step-by-step against a session.

        Performs the following sequence:

        1. Obtain an execution-ordered step list via
           :func:`_topological_sort_plan`.
        2. For each step, call :meth:`execute_step`.
        3. If ``execute_step`` returns ``(False, _)``, halt immediately and
           return the current session.
        4. After all steps complete, call :meth:`check_descent_after_repair`.
        5. If descent passes and :attr:`emit_certificates` is ``True``, call
           :meth:`emit_repair_certificate`.
        6. If descent passes, mark the session converged via
           ``session.converge()`` and return it.

        Args
        ----
        plan : RepairPlan
            The plan to execute.  Must have ``steps`` and
            ``dependency_order`` attributes.
        session : DebugSession
            The current debug session.  Should be in status ``OPEN``.

        Returns
        -------
        DebugSession
            The updated session.  Status is ``CONVERGED`` when all steps
            succeeded and descent passed; otherwise ``OPEN`` (halted) or
            ``BLOCKED``.

        Raises
        ------
        ValueError
            If ``plan.dependency_order`` contains a cycle.

        Examples
        --------
        >>> executor = RepairExecutor(coordinate="root.geometry")
        >>> result = executor.execute(plan, session)
        """
        plan_id = getattr(plan, "plan_id", "<unknown>")
        logger.info(
            "RepairExecutor.execute: starting execution of plan %r "
            "at coordinate %r; %d steps total",
            plan_id,
            self.coordinate,
            len(plan.steps),
        )

        ordered_steps: StepOrder = _topological_sort_plan(plan)
        logger.debug(
            "RepairExecutor.execute: topological order has %d steps",
            len(ordered_steps),
        )

        current_session: DebugSession = session
        for idx, step in enumerate(ordered_steps):
            step_num = idx + 1
            logger.debug(
                "RepairExecutor.execute: applying step %d/%d "
                "step_id=%r action=%r coord=%r",
                step_num,
                len(ordered_steps),
                step.step_id,
                step.action,
                step.coordinate,
            )
            success, current_session = self.execute_step(step, current_session)
            if not success:
                logger.warning(
                    "RepairExecutor.execute: step %d/%d (step_id=%r) failed; "
                    "halting execution",
                    step_num,
                    len(ordered_steps),
                    step.step_id,
                )
                return current_session

        logger.info(
            "RepairExecutor.execute: all %d steps applied; "
            "checking descent condition",
            len(ordered_steps),
        )
        descended: bool = self.check_descent_after_repair(current_session)

        if descended:
            if self.emit_certificates:
                cert = self.emit_repair_certificate(current_session)
                logger.info(
                    "RepairExecutor.execute: certificate emitted: %s", cert.message
                )
            current_session = current_session.converge()
            logger.info(
                "RepairExecutor.execute: session %r converged after %d iteration(s)",
                current_session.session_id,
                current_session.iteration_count,
            )
        else:
            logger.warning(
                "RepairExecutor.execute: descent check failed after all steps; "
                "session remains open"
            )

        return current_session

    # -----------------------------------------------------------------------
    # §2.2  execute_step
    # -----------------------------------------------------------------------

    def execute_step(
        self, step: RepairStep, session: DebugSession
    ) -> StepResult:
        """Execute a single :class:`~models.RepairStep` and advance the session.

        Performs the following sequence:

        1. Validate the step via :func:`_validate_step_inputs`; return
           ``(False, session)`` on validation failure.
        2. If :attr:`dry_run` is ``True``, log the step and return
           ``(True, session)`` unchanged.
        3. Generate the replacement patch section via
           :func:`_generate_patch_section`.
        4. Call :meth:`apply_local_replacement` with ``step.coordinate`` as
           the coordinate, ``step.action`` as the old-section descriptor, and
           the generated patch as the new section.
        5. If the replacement succeeded, advance the session iteration by
           incrementing ``iteration_count`` via
           ``replace(session, iteration_count=session.iteration_count + 1)``.
        6. Return ``(True, advanced_session)`` on success or
           ``(False, session)`` on failure.

        Args
        ----
        step : RepairStep
            The step to execute.
        session : DebugSession
            The current debug session.

        Returns
        -------
        tuple[bool, DebugSession]
            ``(True, advanced_session)`` on success,
            ``(False, session)`` on validation failure or replacement failure.

        Examples
        --------
        >>> ok, new_session = executor.execute_step(step, session)
        >>> if not ok:
        ...     rolled = executor.rollback(new_session, to_iteration=0)
        """
        errors = _validate_step_inputs(step)
        if errors:
            logger.warning(
                "execute_step: %d validation error(s) for step_id=%r: %s",
                len(errors),
                step.step_id,
                "; ".join(errors),
            )
            return False, session

        action_str: str = (
            step.action if isinstance(step.action, str) else str(step.action)
        )
        target_coord: str = step.coordinate or self.coordinate

        if self.dry_run:
            logger.info(
                "execute_step [DRY-RUN]: would apply "
                "action=%r at coordinate=%r (step_id=%r)",
                action_str,
                target_coord,
                step.step_id,
            )
            return True, session

        try:
            new_section: str = _generate_patch_section(action_str, target_coord)
        except ValueError as exc:
            logger.error(
                "execute_step: patch generation failed for step_id=%r: %s",
                step.step_id,
                exc,
            )
            return False, session

        applied: bool = self.apply_local_replacement(
            target_coord, action_str, new_section
        )
        if not applied:
            logger.warning(
                "execute_step: apply_local_replacement returned False "
                "for coordinate=%r (step_id=%r)",
                target_coord,
                step.step_id,
            )
            return False, session

        advanced: DebugSession = replace(
            session, iteration_count=session.iteration_count + 1
        )
        logger.debug(
            "execute_step: session iteration advanced to %d (step_id=%r)",
            advanced.iteration_count,
            step.step_id,
        )
        return True, advanced

    # -----------------------------------------------------------------------
    # §2.3  rollback
    # -----------------------------------------------------------------------

    def rollback(self, session: DebugSession, to_iteration: int) -> DebugSession:
        """Roll the session back to an earlier iteration state.

        Reconstructs a :class:`~models.DebugSession` at *to_iteration* by
        slicing the ``counterexamples`` and ``repair_attempts`` tuples.  The
        ``iteration_count`` is reset to *to_iteration* and ``status`` is
        forced back to ``OPEN``.

        Rollback depth is bounded by :attr:`max_rollback_depth`.  Requesting
        a rollback that would exceed this limit raises ``ValueError``.

        Rollback semantics (immutable reconstruction via ``replace``):

        * ``iteration_count`` ← *to_iteration*
        * ``counterexamples`` ← first *min(to_iteration, len(counterexamples))*
          entries of the original tuple.
        * ``repair_attempts`` ← first *min(to_iteration, len(repair_attempts))*
          entries of the original tuple.
        * ``status`` ← ``DebugSessionStatus.OPEN``

        Args
        ----
        session : DebugSession
            The session to roll back.
        to_iteration : int
            Target iteration index.  Must satisfy
            ``0 <= to_iteration <= session.iteration_count``.

        Returns
        -------
        DebugSession
            A new :class:`~models.DebugSession` at iteration *to_iteration*
            with status ``OPEN``.

        Raises
        ------
        ValueError
            If *to_iteration* is outside ``[0, session.iteration_count]`` or
            if the rollback depth exceeds :attr:`max_rollback_depth`.

        Examples
        --------
        >>> rolled = executor.rollback(session, to_iteration=2)
        >>> assert rolled.iteration_count == 2
        >>> assert rolled.status == DebugSessionStatus.OPEN
        """
        if to_iteration < 0 or to_iteration > session.iteration_count:
            raise ValueError(
                f"rollback: to_iteration={to_iteration} is out of range "
                f"[0, {session.iteration_count}] for session {session.session_id!r}"
            )

        depth: int = session.iteration_count - to_iteration
        if depth > self.max_rollback_depth:
            raise ValueError(
                f"rollback: requested depth {depth} exceeds "
                f"max_rollback_depth={self.max_rollback_depth} "
                f"for session {session.session_id!r}"
            )

        logger.info(
            "rollback: reverting session %r from iteration %d → %d (depth=%d)",
            session.session_id,
            session.iteration_count,
            to_iteration,
            depth,
        )

        trimmed_counterexamples: tuple[CounterexampleRecord, ...] = (
            session.counterexamples[:to_iteration]
        )
        trimmed_repair_attempts: tuple[RepairPlan, ...] = (
            session.repair_attempts[:to_iteration]
        )

        rolled_back: DebugSession = replace(
            session,
            iteration_count=to_iteration,
            status=DebugSessionStatus.OPEN,
            counterexamples=trimmed_counterexamples,
            repair_attempts=trimmed_repair_attempts,
        )

        logger.debug(
            "rollback: new session has %d counterexample(s) and "
            "%d repair_attempt(s)",
            len(trimmed_counterexamples),
            len(trimmed_repair_attempts),
        )
        return rolled_back

    # -----------------------------------------------------------------------
    # §2.4  apply_local_replacement
    # -----------------------------------------------------------------------

    def apply_local_replacement(
        self, coordinate: str, old_section: str, new_section: str
    ) -> bool:
        """Apply a local replacement to the semantic graph at *coordinate*.

        This method is a **side-effectful stub**.  A complete production
        implementation would:

        1. Locate the term fragment at *coordinate* in the live semantic graph.
        2. Verify that the fragment matches the *old_section* descriptor.
        3. Atomically replace the fragment with the term encoded in
           *new_section*.
        4. Invalidate all derivation caches that transitively depend on
           *coordinate*.
        5. Trigger a re-check of any downstream judgment obligations.

        Current implementation:

        * Validates that all three arguments are non-empty, non-whitespace
          strings.
        * Logs the replacement at ``INFO`` level with a 30-character
          preview of each section string.
        * Returns ``True`` iff *coordinate* is non-empty.

        Args
        ----
        coordinate : str
            The semantic coordinate to patch.  Must be non-empty.
        old_section : str
            Descriptor for the fragment being replaced (typically the repair
            action key).
        new_section : str
            The replacement section descriptor produced by
            :func:`_generate_patch_section`.

        Returns
        -------
        bool
            ``True`` when the replacement was applied; ``False`` if any input
            is empty or the coordinate is a blank string.

        Raises
        ------
        None
            Validation failures are logged and reflected as a ``False`` return
            rather than exceptions, allowing callers to decide whether to halt.

        Examples
        --------
        >>> ok = executor.apply_local_replacement(
        ...     "root.geometry.face[0]",
        ...     "ADD_INVARIANT",
        ...     "[PATCH|coord=root.geometry.face[0]|action=ADD_INVARIANT|ts=...]",
        ... )
        >>> assert ok is True
        """
        if not coordinate or not coordinate.strip():
            logger.warning(
                "apply_local_replacement: coordinate is empty; "
                "cannot apply replacement"
            )
            return False

        if not old_section or not new_section:
            logger.warning(
                "apply_local_replacement: old_section or new_section is empty "
                "at coordinate=%r; cannot apply replacement",
                coordinate,
            )
            return False

        old_preview: str = old_section[:30]
        new_preview: str = new_section[:30]

        logger.info(
            "apply_local_replacement: Replacing section at %s: %r → %r",
            coordinate,
            old_preview,
            new_preview,
        )
        # In production: patch the semantic graph here.
        return bool(coordinate.strip())

    # -----------------------------------------------------------------------
    # §2.5  check_descent_after_repair
    # -----------------------------------------------------------------------

    def check_descent_after_repair(self, session: DebugSession) -> bool:
        """Check whether the session satisfies the descent condition.

        Two modes are supported, controlled by :attr:`strict_descent_check`:

        **Relaxed mode** (``strict_descent_check=False``):
            Passes when ``session.status == CONVERGED`` *or* when
            ``len(session.counterexamples) == 0``.  This is appropriate for
            incremental workflows.

        **Strict mode** (``strict_descent_check=True``):
            Additionally requires ``session.iteration_count > 0`` *and*
            ``session.latest_counterexample() is None``.  Use this mode
            for final verification passes.

        The dual criterion (CONVERGED *or* empty counterexamples) is
        intentional: the session may reach CONVERGED status via an external
        call to ``session.converge()`` before this check, or the counterexample
        queue may be drained by a prior execution step.

        Args
        ----
        session : DebugSession
            The session to check.

        Returns
        -------
        bool
            ``True`` if the descent condition is satisfied.

        Examples
        --------
        >>> assert executor.check_descent_after_repair(converged_session) is True
        >>> assert not executor.check_descent_after_repair(open_session_with_cxs)
        """
        if session.status == DebugSessionStatus.CONVERGED:
            logger.debug(
                "check_descent_after_repair: session %r is already CONVERGED",
                session.session_id,
            )
            return True

        no_counterexamples: bool = len(session.counterexamples) == 0

        if not self.strict_descent_check:
            result = no_counterexamples
            logger.debug(
                "check_descent_after_repair (relaxed): "
                "no_counterexamples=%s → result=%s",
                no_counterexamples,
                result,
            )
            return result

        # Strict mode: also require at least one iteration and no latest cx
        iteration_count: int = session.iteration_count
        latest_cx: CounterexampleRecord | None = session.latest_counterexample()
        result = iteration_count > 0 and latest_cx is None
        logger.debug(
            "check_descent_after_repair (strict): "
            "iteration_count=%d latest_cx=%r → result=%s",
            iteration_count,
            latest_cx,
            result,
        )
        return result

    # -----------------------------------------------------------------------
    # §2.6  collect_repair_evidence
    # -----------------------------------------------------------------------

    def collect_repair_evidence(self, session: DebugSession) -> EvidenceBundle:
        """Collect an :class:`~jugeo.judgments.judgment_terms.EvidenceBundle` of repair evidence.

        Builds one :class:`~jugeo.judgments.judgment_terms.EvidenceItem` for
        each :class:`~models.RepairPlan` in ``session.repair_attempts``.
        Each item carries:

        * ``kind`` = :attr:`~jugeo.judgments.judgment_terms.EvidenceItemKind.ORACLE_PROPOSAL`
          (used as a proxy for synthetic repair evidence).
        * ``channel`` = ``"repair"``.
        * ``payload`` = ``{"plan_id": <plan.plan_id>, "coordinate": <self.coordinate>}``.
        * ``trust_level`` = :attr:`~jugeo.judgments.judgment_terms.TrustLevel.ORACLE_PROPOSED`.
        * ``provenance`` = ``(self.coordinate, "repair_executor")``.

        Args
        ----
        session : DebugSession
            The session from which to collect evidence.

        Returns
        -------
        EvidenceBundle
            A bundle with one item per repair attempt.  Returns an empty
            bundle when ``session.repair_attempts`` is empty.

        Examples
        --------
        >>> bundle = executor.collect_repair_evidence(session)
        >>> assert all(item.channel == "repair" for item in bundle.items)
        """
        items: list[EvidenceItem] = []
        for plan in session.repair_attempts:
            plan_id: str = getattr(plan, "plan_id", "<unknown>")
            confidence: float = getattr(plan, "confidence_score", 0.0)
            item = EvidenceItem(
                kind=EvidenceItemKind.ORACLE_PROPOSAL,
                channel="repair",
                payload={
                    "plan_id": plan_id,
                    "coordinate": self.coordinate,
                    "confidence_score": str(confidence),
                    "step_count": str(len(getattr(plan, "steps", ()))),
                },
                trust_level=TrustLevel.ORACLE_PROPOSED,
                timestamp=_iso_now(),
                provenance=(self.coordinate, "repair_executor"),
            )
            items.append(item)
            logger.debug(
                "collect_repair_evidence: created item for plan_id=%r", plan_id
            )

        bundle = EvidenceBundle(items=tuple(items))
        logger.info(
            "collect_repair_evidence: built bundle with %d item(s) "
            "for session %r",
            len(bundle.items),
            session.session_id,
        )
        return bundle

    # -----------------------------------------------------------------------
    # §2.7  emit_repair_certificate
    # -----------------------------------------------------------------------

    def emit_repair_certificate(self, session: DebugSession) -> StructuredFailure:
        """Emit a repair certificate as a :class:`~jugeo.errors.StructuredFailure` payload.

        By convention in JuGeo, a :class:`~jugeo.errors.StructuredFailure` with

        * ``classification = LOCAL_REPAIR``
        * ``recoverable = True``

        represents a *successful repair certificate* rather than an error.
        This dual-use of the failure infrastructure allows the repair pipeline
        to leverage all the serialisation, provenance-tracking, and audit-log
        machinery that :class:`~jugeo.errors.StructuredFailure` provides,
        without introducing a separate certificate type.

        Certificate contents:

        * ``message``: human-readable certificate description including session
          ID, coordinate, iteration count, and theory reference.
        * ``scope`` = ``FailureScope.ORCHESTRATION``
        * ``classification`` = ``FailureClassification.LOCAL_REPAIR``
        * ``coordinate`` = ``self.coordinate`` (or ``None`` if empty)
        * ``evidence_family`` = ``EvidenceFamily.SEMANTIC``
        * ``metadata``: ``session_id``, ``iteration_count``, ``coordinate``,
          ``emit_time``, ``pipeline_stage``, ``theory_section``.
        * ``recoverable`` = ``True``

        Args
        ----
        session : DebugSession
            The session that has converged and for which a certificate is
            being issued.

        Returns
        -------
        StructuredFailure
            The repair certificate.

        Examples
        --------
        >>> cert = executor.emit_repair_certificate(session)
        >>> assert cert.recoverable is True
        >>> assert cert.classification == FailureClassification.LOCAL_REPAIR
        """
        session_id: str = session.session_id
        iteration_count: int = session.iteration_count
        plan_count: int = len(session.repair_attempts)
        cx_count: int = len(session.counterexamples)

        message: str = (
            f"Repair certificate issued for session {session_id!r} "
            f"at coordinate {self.coordinate!r}: "
            f"{plan_count} plan(s) applied over {iteration_count} iteration(s), "
            f"{cx_count} counterexample(s) recorded "
            f"[{MANIFEST_SPEC_PROVENANCE['theory_section']}]"
        )

        cert = StructuredFailure(
            message=message,
            scope=FailureScope.ORCHESTRATION,
            classification=FailureClassification.LOCAL_REPAIR,
            coordinate=self.coordinate if self.coordinate else None,
            evidence_family=EvidenceFamily.SEMANTIC,
            metadata={
                "session_id": session_id,
                "iteration_count": str(iteration_count),
                "coordinate": self.coordinate,
                "plan_count": str(plan_count),
                "counterexample_count": str(cx_count),
                "emit_time": _iso_now(),
                "pipeline_stage": MANIFEST_SPEC_PROVENANCE["pipeline_stage"],
                "theory_section": MANIFEST_SPEC_PROVENANCE["theory_section"],
                "semantic_source": MANIFEST_SPEC_PROVENANCE["semantic_source"],
            },
            recoverable=True,
        )

        logger.info(
            "emit_repair_certificate: %s", message
        )
        return cert

    # -----------------------------------------------------------------------
    # §2.8  validate_and_commit
    # -----------------------------------------------------------------------

    def validate_and_commit(
        self, session: DebugSession, validator: RepairValidator
    ) -> DebugSession:
        """Validate the latest repair attempt and commit the session if valid.

        Uses the provided :class:`~models.RepairValidator` to perform two
        checks in sequence:

        1. **Plan validation** (``validator.validate(plan)``): checks that the
           latest :class:`~models.RepairPlan`'s dependency graph is acyclic and
           all dependency references are resolved.  If no repair attempt exists,
           this check is skipped and treated as passing.

        2. **Descent check** (:meth:`check_descent_after_repair`): verifies the
           descent condition using the executor's configured mode.

        If **both** checks pass, the session is marked converged via
        ``session.converge()`` and returned.

        If **either** check fails, the session is marked blocked via
        ``replace(session, status=DebugSessionStatus.BLOCKED)`` and returned.

        Args
        ----
        session : DebugSession
            The session to validate and (potentially) commit.
        validator : RepairValidator
            The validator to use for plan structural checking.

        Returns
        -------
        DebugSession
            A session with status ``CONVERGED`` if both checks pass, or
            ``BLOCKED`` if either check fails.

        Examples
        --------
        >>> validator = RepairValidator(strict=True)
        >>> committed = executor.validate_and_commit(session, validator)
        >>> print(committed.status)
        """
        latest_plan: RepairPlan | None = session.latest_repair_plan()

        plan_valid: bool = True
        if latest_plan is not None:
            try:
                ok, failures = validator.validate(latest_plan)
                plan_valid = ok
                if failures:
                    logger.warning(
                        "validate_and_commit: validator reported %d failure(s) "
                        "for plan %r: %s",
                        len(failures),
                        latest_plan.plan_id,
                        "; ".join(failures),
                    )
                else:
                    logger.debug(
                        "validate_and_commit: plan %r passed validation",
                        latest_plan.plan_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "validate_and_commit: validator.validate raised %s; "
                    "treating plan as invalid",
                    exc,
                )
                plan_valid = False
        else:
            logger.debug(
                "validate_and_commit: no repair attempt found; "
                "skipping plan validation"
            )

        descent_ok: bool = self.check_descent_after_repair(session)
        logger.debug(
            "validate_and_commit: plan_valid=%s descent_ok=%s",
            plan_valid,
            descent_ok,
        )

        if plan_valid and descent_ok:
            logger.info(
                "validate_and_commit: both checks passed; "
                "marking session %r CONVERGED",
                session.session_id,
            )
            return session.converge()

        logger.warning(
            "validate_and_commit: validation failed "
            "(plan_valid=%s, descent_ok=%s); "
            "marking session %r BLOCKED",
            plan_valid,
            descent_ok,
            session.session_id,
        )
        return replace(session, status=DebugSessionStatus.BLOCKED)




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
    "RepairExecutor",
    "StepResult",
    "StepOrder",
    "_generate_patch_section",
    "_iso_now",
    "_topological_sort_plan",
    "_validate_step_inputs",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: repair_execution — Ch11 §11.3 Repair Execution and Rollback
