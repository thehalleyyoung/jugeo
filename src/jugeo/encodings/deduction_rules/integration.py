"""Integration layer for the deduction_rules package -- theory2.tex Chapter 33.

This module ties together the deduction-rule sub-system with the rest of
JuGeo: the Z3 solver, evidence channels, judgment algebra, and the Copilot
assistant.

Architecture
------------
- DeductionSession        -- manages a single deduction proof attempt
- TransitionSystemRunner  -- executes transition systems with logging
- RuleApplicationTracker  -- audit trail for rule applications
- JudgmentDischarger      -- discharges judgment obligations by rule application
- CopilotDeductionAssist  -- Copilot bridge for deduction assistance
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Result
except Exception:  # pragma: no cover – optional dependency
    class Z3Session:  # type: ignore[no-redef]
        """Stub when jugeo.solver is unavailable."""

    class Z3Formula:  # type: ignore[no-redef]
        """Stub."""

    class Z3Encoder:  # type: ignore[no-redef]
        """Stub."""

    class Z3Result:  # type: ignore[no-redef]
        """Stub."""

try:
    from jugeo.solver.reconstruction import ModelReconstruction
except Exception:  # pragma: no cover
    class ModelReconstruction:  # type: ignore[no-redef]
        """Stub."""

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm
except Exception:  # pragma: no cover
    class JudgmentTerm:  # type: ignore[no-redef]
        """Stub."""

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
except Exception:  # pragma: no cover
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub."""

    class TrustLevel(str, Enum):  # type: ignore[no-redef]
        """Minimal trust-level stub used when jugeo.evidence is unavailable."""
        UNVERIFIED = "UNVERIFIED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

from jugeo.encodings.deduction_rules.models import (
    DeductionRule,
    JudgmentTransition,
    InferenceStep,
    RuleApplication,
    TransitionSystem,
    RuleKind,
    ApplicationResult,
    TransitionKind,
    InferenceStatus,
    make_rule,
    make_axiom_rule,
    _new_id,
    _stable_hash,
    _now_iso,
)

try:
    from jugeo.encodings.deduction_rules import s01  # type: ignore[import]
except Exception:
    s01 = None  # type: ignore[assignment]

try:
    from jugeo.encodings.deduction_rules import s02  # type: ignore[import]
except Exception:
    s02 = None  # type: ignore[assignment]

try:
    from jugeo.encodings.deduction_rules import s03  # type: ignore[import]
except Exception:
    s03 = None  # type: ignore[assignment]

try:
    from jugeo.encodings.deduction_rules import s04  # type: ignore[import]
except Exception:
    s04 = None  # type: ignore[assignment]

from jugeo.encodings.deduction_rules.algorithms import (
    apply_deduction_rule,
    compute_transition_sequence,
    check_rule_applicability,
    run_transition_system,
    verify_proof_trace,
    synthesize_rules_for_obligations,
    copilot_suggest_next_rule,
    _token_similarity,
    _find_applicable_rules,
)


# ---------------------------------------------------------------------------
# DeductionSession
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DeductionSession:
    """Manages a single end-to-end deduction proof attempt.

    A session encapsulates all state required to drive a proof from a set of
    *initial_judgments* towards a *goal*: the rule library, proof steps taken
    so far, all rule-application records, and the current status.

    The session exposes a high-level API for interactive proof construction
    as well as a *Copilot bridge* for AI-assisted guidance.

    Attributes
    ----------
    session_id:
        Unique identifier for this session.
    goal:
        The judgment string this session is trying to prove.
    rules:
        The deduction rules available in this session.
    initial_judgments:
        The starting judgment set (hypotheses / axiom instances).
    context:
        Ambient context propagated to every rule application.
    status:
        Current :class:`InferenceStatus` of the session.
    steps:
        Ordered list of :class:`InferenceStep` objects produced so far.
    applications:
        Ordered list of every :class:`RuleApplication` attempted so far.
    created_at:
        ISO-8601 UTC timestamp when the session was created.
    metadata:
        Free-form session annotations.
    """

    session_id: str
    goal: str
    rules: list[DeductionRule] = field(default_factory=list)
    initial_judgments: list[Any] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    status: InferenceStatus = InferenceStatus.PENDING
    steps: list[InferenceStep] = field(default_factory=list)
    applications: list[RuleApplication] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Transition the session from PENDING to IN_PROGRESS.

        Records the start timestamp in ``metadata["started_at"]`` and sets
        ``status`` to :attr:`InferenceStatus.IN_PROGRESS`.  Calling this
        method more than once is a no-op if the session is already in progress
        or has completed.

        Raises
        ------
        RuntimeError
            If the session has already been abandoned (FAILED status).
        """
        if self.status == InferenceStatus.FAILED:
            raise RuntimeError(
                f"Session '{self.session_id}' has been abandoned and cannot be restarted. "
                "Call reset() first."
            )
        if self.status == InferenceStatus.PENDING:
            self.status = InferenceStatus.IN_PROGRESS
            self.metadata["started_at"] = _now_iso()

    def apply_rule(
        self,
        rule: DeductionRule,
        judgment: Any,
    ) -> RuleApplication:
        """Apply *rule* to *judgment* and record the result in this session.

        Delegates to :func:`~jugeo.encodings.deduction_rules.algorithms.apply_deduction_rule`
        with the session's current context enriched by the list of outputs
        produced by prior steps (so that premise discharge can use earlier
        derivations).

        On success the method:

        1. Constructs a new :class:`InferenceStep` and appends it to ``steps``.
        2. Appends the :class:`RuleApplication` to ``applications``.
        3. Checks whether the new step's output satisfies ``goal`` and, if so,
           transitions the session to SUCCEEDED.

        Parameters
        ----------
        rule:
            The rule to apply.
        judgment:
            The judgment to apply the rule to.

        Returns
        -------
        RuleApplication
            The application record (may be a failure record if the rule does
            not apply).
        """
        # Enrich context with previously-produced judgments.
        ctx = dict(self.context)
        ctx["available_judgments"] = [s.output for s in self.steps] + [
            str(j) for j in self.initial_judgments
        ]
        ctx["target_judgment"] = str(judgment)

        app = apply_deduction_rule(rule, judgment, ctx)
        self.applications.append(app)

        if app.succeeded():
            fire_result = app.evidence_produced[0] if app.evidence_produced else {}
            output = (
                fire_result.get("conclusion", str(judgment))
                if isinstance(fire_result, dict)
                else str(judgment)
            )
            step = InferenceStep(
                step_id=_new_id("step"),
                rule=rule,
                inputs=(str(judgment),),
                output=output,
                justification=f"Applied {rule.rule_name} in session {self.session_id}",
                step_index=len(self.steps),
            )
            self.steps.append(step)

            # Auto-detect success.
            if output.strip() == self.goal.strip():
                self.status = InferenceStatus.SUCCEEDED
                self.metadata["completed_at"] = _now_iso()

        return app

    def apply_best_rule(self, judgment: Any) -> RuleApplication | None:
        """Find and apply the highest-scoring applicable rule to *judgment*.

        Uses :func:`~jugeo.encodings.deduction_rules.algorithms.copilot_suggest_next_rule`
        to rank all rules in the session, then calls :meth:`apply_rule` with
        the top-ranked applicable rule.

        Parameters
        ----------
        judgment:
            The judgment to apply the best rule to.

        Returns
        -------
        RuleApplication | None
            The application record of the best rule, or ``None`` if no rule
            is applicable to *judgment*.
        """
        suggestions = copilot_suggest_next_rule(
            current_judgment=str(judgment),
            goal=self.goal,
            available_rules=self.rules,
            proof_history=self.steps,
        )
        if not suggestions:
            return None

        best = suggestions[0]
        rule: DeductionRule = best["rule"]
        return self.apply_rule(rule, judgment)

    def step_toward_goal(self) -> JudgmentTransition | None:
        """Attempt one transition step towards *goal* from the latest judgment.

        Identifies the current frontier judgment (the output of the last step,
        or the first initial judgment if no steps have been taken), then
        delegates to :func:`~jugeo.encodings.deduction_rules.algorithms.compute_transition_sequence`
        with ``max_steps=1``.

        Returns
        -------
        JudgmentTransition | None
            The single transition taken, or ``None`` if no rule applies.
        """
        if self.steps:
            current = self.steps[-1].output
        elif self.initial_judgments:
            current = str(self.initial_judgments[0])
        else:
            return None

        transitions = compute_transition_sequence(
            rules=self.rules,
            initial_judgment=current,
            goal=self.goal,
            max_steps=1,
            context=self.context,
        )
        if transitions:
            t = transitions[0]
            # Record as an inference step.
            step = InferenceStep(
                step_id=_new_id("step"),
                rule=t.rule_applied,
                inputs=(str(t.source_judgment),),
                output=str(t.target_judgment),
                justification=(
                    f"One-step toward goal via {t.rule_applied.rule_name}"
                ),
                step_index=len(self.steps),
            )
            self.steps.append(step)
            if str(t.target_judgment).strip() == self.goal.strip():
                self.status = InferenceStatus.SUCCEEDED
                self.metadata["completed_at"] = _now_iso()
            return t
        return None

    def is_complete(self) -> bool:
        """Return ``True`` if the session has successfully reached its goal.

        Checks both the session ``status`` field and whether any step's
        output matches ``goal`` (normalised for whitespace), so that sessions
        that were driven by external code (bypassing :meth:`apply_rule`) are
        correctly recognised as complete.

        Returns
        -------
        bool
        """
        if self.status == InferenceStatus.SUCCEEDED:
            return True
        goal_stripped = self.goal.strip()
        for step in self.steps:
            if step.output.strip() == goal_stripped:
                self.status = InferenceStatus.SUCCEEDED
                return True
        return False

    def abandon(self, reason: str) -> None:
        """Mark the session as failed with a human-readable *reason*.

        Sets ``status`` to :attr:`InferenceStatus.FAILED` and records the
        reason and abandonment timestamp in ``metadata``.

        Parameters
        ----------
        reason:
            Human-readable explanation of why the session is being abandoned.
        """
        self.status = InferenceStatus.FAILED
        self.metadata["abandoned_reason"] = reason
        self.metadata["abandoned_at"] = _now_iso()

    def proof_certificate(self) -> dict[str, Any]:
        """Serialise the completed proof as a certificate dictionary.

        Returns a fully-serialisable dict suitable for JSON export, containing
        the session identifier, goal, status, all inference steps, all rule
        applications, and session metadata.

        Raises
        ------
        ValueError
            If the session has not yet succeeded (``status != SUCCEEDED``).
        """
        if not self.is_complete():
            raise ValueError(
                f"Session '{self.session_id}' has not succeeded "
                f"(status={self.status.value}). No certificate can be issued."
            )
        step_dicts = [s.to_derivation_tree() for s in self.steps]
        app_dicts = [a.to_audit_record() for a in self.applications if a.succeeded()]
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.metadata.get("completed_at", _now_iso()),
            "steps": step_dicts,
            "applications": app_dicts,
            "rule_count": len(self.rules),
            "step_count": len(self.steps),
            "metadata": self.metadata,
        }

    def copilot_hint(self) -> str:
        """Ask Copilot for a hint about the best next step.

        # copilot ask copilot for a hint about next step

        Determines the current frontier judgment and uses
        :func:`~jugeo.encodings.deduction_rules.algorithms.copilot_suggest_next_rule`
        to produce a ranked list of suggestions, then formats the top-3 as a
        human-readable hint string.

        Returns
        -------
        str
            A formatted hint string listing the top-3 suggested rules with
            their scores and rationales.
        """
        if self.steps:
            current = self.steps[-1].output
        elif self.initial_judgments:
            current = str(self.initial_judgments[0])
        else:
            return "No current judgment to hint from."

        suggestions = copilot_suggest_next_rule(
            current_judgment=current,
            goal=self.goal,
            available_rules=self.rules,
            proof_history=self.steps,
        )
        if not suggestions:
            return (
                f"No applicable rules found for current judgment '{current}'. "
                "Consider adding rules or modifying the goal."
            )

        lines: list[str] = [
            f"Copilot hint for session '{self.session_id}':",
            f"  Current judgment: {current}",
            f"  Goal: {self.goal}",
            "  Top suggestions:",
        ]
        for s in suggestions[:3]:
            r: DeductionRule = s["rule"]
            lines.append(
                f"    #{s['rank']} {r.rule_name!r} "
                f"(score={s['score']:.4f}): {s['rationale']}"
            )
        return "\n".join(lines)

    def summarize(self) -> str:
        """Return a one-paragraph summary of the current session state.

        Includes the session identifier, goal, status, number of steps taken,
        number of rule applications (with success/failure counts), and, if
        the proof is not yet complete, the frontier judgment.

        Returns
        -------
        str
            A prose summary suitable for display in a terminal or log.
        """
        success_apps = sum(1 for a in self.applications if a.succeeded())
        fail_apps = len(self.applications) - success_apps
        if self.steps:
            frontier = self.steps[-1].output
        elif self.initial_judgments:
            frontier = str(self.initial_judgments[0])
        else:
            frontier = "(none)"

        parts: list[str] = [
            f"Session '{self.session_id}' — goal: '{self.goal}'",
            f"  Status:       {self.status.value}",
            f"  Steps taken:  {len(self.steps)}",
            f"  Applications: {len(self.applications)} "
            f"({success_apps} succeeded, {fail_apps} failed)",
            f"  Frontier:     {frontier}",
            f"  Created at:   {self.created_at}",
        ]
        if self.metadata.get("abandoned_reason"):
            parts.append(f"  Abandon reason: {self.metadata['abandoned_reason']}")
        return "\n".join(parts)

    def reset(self) -> None:
        """Reset the session to its initial state.

        Clears all recorded steps, applications, and metadata (except for
        ``created_at`` and ``session_id``), and sets the status back to
        PENDING.  The rule library and goal are preserved.
        """
        self.steps.clear()
        self.applications.clear()
        preserved_created_at = self.created_at
        self.metadata.clear()
        self.metadata["reset_at"] = _now_iso()
        self.created_at = preserved_created_at
        self.status = InferenceStatus.PENDING


# ---------------------------------------------------------------------------
# TransitionSystemRunner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TransitionSystemRunner:
    """Executes a :class:`TransitionSystem` with step-level logging.

    Wraps :func:`~jugeo.encodings.deduction_rules.algorithms.run_transition_system`
    and adds:

    - A **log buffer** that records textual descriptions of every transition.
    - An optional **verbose mode** that prints log entries as they are produced.
    - A **timeout** mechanism that aborts long-running systems.
    - An **export** method that produces a fully-serialisable trace.

    Attributes
    ----------
    system:
        The :class:`TransitionSystem` to execute.
    logger:
        Accumulated log lines.
    max_iterations:
        Maximum rule-application steps.
    verbose:
        If ``True``, log lines are also printed to stdout.
    result:
        The result dict from the most recent run (empty dict initially).
    """

    system: TransitionSystem
    logger: list[str] = field(default_factory=list)
    max_iterations: int = 1000
    verbose: bool = False
    result: dict[str, Any] = field(default_factory=dict)

    def _log(self, message: str) -> None:
        """Append *message* to the log buffer, optionally printing it."""
        entry = f"[{_now_iso()}] {message}"
        self.logger.append(entry)
        if self.verbose:
            print(entry)

    def run(self) -> dict[str, Any]:
        """Execute the system to fixpoint and store the result.

        Calls :func:`~jugeo.encodings.deduction_rules.algorithms.run_transition_system`
        with the runner's ``max_iterations`` limit.  Logs a summary of every
        transition taken.

        Returns
        -------
        dict
            The result dictionary (also stored in ``self.result``).
        """
        self._log(
            f"Starting run of system '{self.system.system_id}' "
            f"(max_iterations={self.max_iterations})"
        )
        self.result = run_transition_system(
            system=self.system,
            max_iterations=self.max_iterations,
            context=None,
        )
        n_trans = len(self.result.get("transitions", []))
        fixpoint = self.result.get("fixpoint_reached", False)
        self._log(
            f"Run complete: {n_trans} transition(s), "
            f"fixpoint={'yes' if fixpoint else 'no'}, "
            f"elapsed={self.result.get('elapsed_ms', '?')}ms"
        )
        for t in self.result.get("transitions", []):
            self._log(
                f"  transition {t.transition_id}: "
                f"'{t.source_judgment}' →[{t.rule_applied.rule_name}]→ "
                f"'{t.target_judgment}' (Δtrust={t.trust_delta})"
            )
        if self.result.get("soundness_issues"):
            for issue in self.result["soundness_issues"]:
                self._log(f"  SOUNDNESS WARNING: {issue}")
        return self.result

    def run_with_timeout(self, timeout_seconds: float) -> dict[str, Any]:
        """Execute the system with a wall-clock timeout.

        If the run takes longer than *timeout_seconds*, the result is returned
        with ``terminated_early=True`` and ``timeout_hit=True``.

        Parameters
        ----------
        timeout_seconds:
            Maximum wall-clock time (in seconds) to allow.

        Returns
        -------
        dict
            The result dictionary (also stored in ``self.result``).
        """
        import threading

        result_holder: dict[str, Any] = {}
        exception_holder: list[Exception] = []

        def _target() -> None:
            try:
                result_holder.update(run_transition_system(
                    system=self.system,
                    max_iterations=self.max_iterations,
                    context=None,
                ))
            except Exception as exc:
                exception_holder.append(exc)

        thread = threading.Thread(target=_target, daemon=True)
        t_start = time.monotonic()
        thread.start()
        thread.join(timeout=timeout_seconds)
        elapsed = time.monotonic() - t_start

        if thread.is_alive():
            self._log(
                f"Timeout of {timeout_seconds}s exceeded after {elapsed:.3f}s; "
                "returning partial result."
            )
            partial: dict[str, Any] = {
                "transitions": [],
                "final_judgments": [str(j) for j in self.system.initial_judgments],
                "fixpoint_reached": False,
                "iterations": 0,
                "terminated_early": True,
                "timeout_hit": True,
                "elapsed_ms": round(elapsed * 1000, 3),
                "system_id": self.system.system_id,
                "soundness_issues": [],
            }
            self.result = partial
            return partial

        if exception_holder:
            self._log(f"Run raised exception: {exception_holder[0]!r}")

        result_holder.setdefault("timeout_hit", False)
        result_holder["elapsed_ms"] = round(elapsed * 1000, 3)
        self.result = result_holder
        return result_holder

    def step_once(self, judgment: Any) -> JudgmentTransition | None:
        """Apply the first applicable rule to *judgment* (single step).

        Delegates to :meth:`TransitionSystem.step`.  Logs the transition
        if one occurs.

        Parameters
        ----------
        judgment:
            The judgment to step from.

        Returns
        -------
        JudgmentTransition | None
            The transition produced, or ``None`` if no rule applies.
        """
        transition = self.system.step(judgment)
        if transition is not None:
            self._log(
                f"step_once: '{judgment}' →[{transition.rule_applied.rule_name}]→ "
                f"'{transition.target_judgment}'"
            )
        else:
            self._log(f"step_once: no rule applicable to '{judgment}'")
        return transition

    def get_log(self) -> list[str]:
        """Return a copy of the current log buffer.

        Returns
        -------
        list[str]
            All accumulated log entries (immutable snapshot).
        """
        return list(self.logger)

    def clear_log(self) -> None:
        """Clear the log buffer, discarding all accumulated entries."""
        self.logger.clear()

    def export_trace(self) -> list[dict[str, Any]]:
        """Export every transition from the most recent run as a list of dicts.

        Each dict is produced by :meth:`JudgmentTransition.serialize` and is
        fully JSON-serialisable.

        Returns
        -------
        list[dict]
            One dict per transition, in order.  Returns an empty list if no
            run has been performed yet.
        """
        transitions: list[JudgmentTransition] = self.result.get("transitions", [])
        return [t.serialize() for t in transitions]

    def check_termination(self) -> bool:
        """Heuristically predict whether the system will terminate.

        The check is conservative: it returns ``True`` (will terminate) only
        when *both* of the following hold:

        - The system has no rules with their own conclusion as a premise
          (no obvious self-loops).
        - There are no pairs of semantic rules whose conclusions overlap
          (no obvious divergence).

        Returns
        -------
        bool
            ``True`` if termination appears likely; ``False`` if a potential
            loop or divergence is detected.
        """
        # Check 1: self-loop detection.
        for rule in self.system.rules:
            if rule.conclusion in rule.premises:
                self._log(
                    f"check_termination: rule '{rule.rule_name}' has its "
                    "conclusion as a premise (potential loop)."
                )
                return False

        # Check 2: convergence via confluence check.
        confluent = self.system.check_confluence()
        if not confluent:
            self._log(
                "check_termination: confluence check failed "
                "(overlapping semantic rules detected)."
            )
            return False

        self._log(
            "check_termination: no obvious loops or divergences detected."
        )
        return True

    def copilot_diagnose(self) -> str:
        """Ask Copilot whether the system appears stuck or diverging.

        # copilot diagnose if system is stuck

        Analyses the result of the most recent run (if any) and the system's
        rule structure to produce a diagnostic report.

        Returns
        -------
        str
            A multi-line diagnostic message summarising potential issues and
            suggesting remedies.
        """
        lines: list[str] = [
            f"Copilot diagnosis for system '{self.system.system_id}':",
        ]

        if not self.result:
            lines.append("  No run has been performed yet. Call run() first.")
            return "\n".join(lines)

        transitions = self.result.get("transitions", [])
        if self.result.get("terminated_early"):
            lines.append(
                f"  WARNING: System terminated early after {len(transitions)} transitions "
                f"(max_iterations={self.max_iterations}). "
                "This suggests the system may not terminate."
            )
        elif self.result.get("fixpoint_reached") and len(transitions) == 0:
            lines.append(
                "  WARNING: No transitions were taken (immediate fixpoint). "
                "Check that the initial judgments match at least one rule conclusion."
            )
        else:
            lines.append(
                f"  System ran {len(transitions)} transition(s) and "
                f"reached {'fixpoint' if self.result.get('fixpoint_reached') else 'cap'}."
            )

        soundness = self.result.get("soundness_issues", [])
        if soundness:
            lines.append(f"  {len(soundness)} soundness issue(s) detected:")
            for issue in soundness:
                lines.append(f"    - {issue}")

        # Check rule coverage.
        rules_fired: dict[str, int] = self.result.get("rules_fired", {})
        unfired = [r.rule_name for r in self.system.rules if r.rule_name not in rules_fired]
        if unfired:
            lines.append(
                f"  {len(unfired)} rule(s) were never fired: {unfired[:5]}"
                + (" …" if len(unfired) > 5 else "")
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RuleApplicationTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RuleApplicationTracker:
    """An indexed audit trail for :class:`RuleApplication` events.

    Maintains three parallel indexes for fast lookup:

    - ``applications`` – flat chronological list of all applications
    - ``by_rule``       – mapping from rule name to list of applications
    - ``by_result``     – mapping from result value to list of applications

    Attributes
    ----------
    applications:
        Chronological list of all recorded applications.
    by_rule:
        Index mapping rule name → list of applications for that rule.
    by_result:
        Index mapping ApplicationResult value → list of matching applications.
    session_id:
        Identifier of the session this tracker belongs to.
    """

    applications: list[RuleApplication] = field(default_factory=list)
    by_rule: dict[str, list[RuleApplication]] = field(default_factory=dict)
    by_result: dict[str, list[RuleApplication]] = field(default_factory=dict)
    session_id: str = field(default_factory=lambda: _new_id("tracker"))

    def record(self, application: RuleApplication) -> None:
        """Add *application* to all internal indexes.

        This is the single method that must be called for every application
        that should be tracked.  It updates the chronological list and both
        secondary indexes atomically.

        Parameters
        ----------
        application:
            The :class:`RuleApplication` to record.
        """
        self.applications.append(application)

        rule_name = application.rule.rule_name
        if rule_name not in self.by_rule:
            self.by_rule[rule_name] = []
        self.by_rule[rule_name].append(application)

        result_key = application.result.value
        if result_key not in self.by_result:
            self.by_result[result_key] = []
        self.by_result[result_key].append(application)

    def applications_for_rule(self, rule_name: str) -> list[RuleApplication]:
        """Return all applications for the rule named *rule_name*.

        Parameters
        ----------
        rule_name:
            The human-readable name of the rule.

        Returns
        -------
        list[RuleApplication]
            All recorded applications for that rule, in chronological order.
            Returns an empty list if the rule has never been applied.
        """
        return list(self.by_rule.get(rule_name, []))

    def success_rate(self, rule_name: str) -> float:
        """Compute the fraction of applications of *rule_name* that succeeded.

        Parameters
        ----------
        rule_name:
            The rule to query.

        Returns
        -------
        float
            A value in [0, 1].  Returns 0.0 if the rule has never been
            applied (to avoid division-by-zero).
        """
        apps = self.by_rule.get(rule_name, [])
        if not apps:
            return 0.0
        successes = sum(1 for a in apps if a.succeeded())
        return successes / len(apps)

    def failures(self) -> list[RuleApplication]:
        """Return all applications that did *not* succeed.

        Returns
        -------
        list[RuleApplication]
            Every application whose result is not ``APPLIED``, in
            chronological order.
        """
        return [a for a in self.applications if not a.succeeded()]

    def most_used_rules(self, n: int = 10) -> list[tuple[str, int]]:
        """Return the top *n* rules by application count.

        Parameters
        ----------
        n:
            Maximum number of rules to return (default 10).

        Returns
        -------
        list[tuple[str, int]]
            Pairs of ``(rule_name, count)``, sorted descending by count.
        """
        counts = [(name, len(apps)) for name, apps in self.by_rule.items()]
        counts.sort(key=lambda x: -x[1])
        return counts[:n]

    def audit_report(self) -> list[dict[str, Any]]:
        """Produce a serialisable audit report for all recorded applications.

        Each entry is produced by :meth:`RuleApplication.to_audit_record`,
        enriched with an additional ``session_id`` field.

        Returns
        -------
        list[dict]
            One dict per application, in chronological order.
        """
        records: list[dict[str, Any]] = []
        for app in self.applications:
            record = app.to_audit_record()
            record["session_id"] = self.session_id
            records.append(record)
        return records

    def to_summary(self) -> str:
        """Return a multi-line textual summary of the tracker's state.

        Includes total application count, success/failure breakdown, and the
        top-5 most-used rules.

        Returns
        -------
        str
            A formatted summary string.
        """
        total = len(self.applications)
        successes = len(self.by_result.get(ApplicationResult.APPLIED.value, []))
        failures_count = total - successes
        top_rules = self.most_used_rules(5)

        lines: list[str] = [
            f"RuleApplicationTracker (session={self.session_id})",
            f"  Total applications: {total}",
            f"  Succeeded:          {successes}",
            f"  Failed:             {failures_count}",
            "  Top rules by use:",
        ]
        for name, count in top_rules:
            rate = self.success_rate(name)
            lines.append(f"    {name!r}: {count} applications, {rate:.0%} success")
        return "\n".join(lines)

    def clear(self) -> None:
        """Remove all recorded applications and clear all indexes."""
        self.applications.clear()
        self.by_rule.clear()
        self.by_result.clear()


# ---------------------------------------------------------------------------
# JudgmentDischarger
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class JudgmentDischarger:
    """Discharges a set of judgment obligations by systematic rule application.

    The discharger maintains two queues:

    - ``pending``    – obligations not yet resolved
    - ``discharged`` – obligations that have been successfully resolved

    Obligations are attempted one at a time via :meth:`discharge_one`.  The
    :meth:`discharge_all` method iterates until all pending obligations are
    discharged or the attempt limit is reached.

    All rule applications are recorded via the embedded
    :class:`RuleApplicationTracker`.

    Attributes
    ----------
    rules:
        The rule library to draw from.
    discharged:
        Successfully discharged obligations.
    pending:
        Obligations still awaiting discharge.
    context:
        Ambient context forwarded to applicability checks.
    tracker:
        Audit-trail tracker for all application events.
    """

    rules: list[DeductionRule] = field(default_factory=list)
    discharged: list[Any] = field(default_factory=list)
    pending: list[Any] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    tracker: RuleApplicationTracker = field(
        default_factory=lambda: RuleApplicationTracker()
    )

    def add_obligation(self, judgment: Any) -> None:
        """Enqueue *judgment* as a new pending obligation.

        If the same judgment (by string equality) is already pending or has
        already been discharged, the call is a no-op.

        Parameters
        ----------
        judgment:
            The obligation to add.
        """
        j_str = str(judgment)
        already_pending = any(str(p) == j_str for p in self.pending)
        already_discharged = any(str(d) == j_str for d in self.discharged)
        if not already_pending and not already_discharged:
            self.pending.append(judgment)

    def discharge_one(self, judgment: Any) -> RuleApplication | None:
        """Attempt to discharge a single *judgment* using the best applicable rule.

        Uses :func:`~jugeo.encodings.deduction_rules.algorithms.copilot_suggest_next_rule`
        to rank available rules, then attempts them in descending score order
        until one succeeds.  On success the judgment is moved from ``pending``
        to ``discharged``.  The application is always recorded via
        ``self.tracker``.

        Parameters
        ----------
        judgment:
            The obligation to attempt to discharge.

        Returns
        -------
        RuleApplication | None
            The successful application record, or ``None`` if no rule could
            discharge the obligation.
        """
        j_str = str(judgment)
        ctx = dict(self.context)
        ctx["available_judgments"] = [str(d) for d in self.discharged]
        ctx["target_judgment"] = j_str

        suggestions = copilot_suggest_next_rule(
            current_judgment=j_str,
            goal=j_str,  # Goal is the obligation itself.
            available_rules=self.rules,
            proof_history=None,
        )

        for suggestion in suggestions:
            rule: DeductionRule = suggestion["rule"]
            app = apply_deduction_rule(rule, judgment, ctx)
            self.tracker.record(app)
            if app.succeeded():
                # Move from pending to discharged.
                self.pending = [p for p in self.pending if str(p) != j_str]
                self.discharged.append(judgment)
                return app

        # No rule succeeded – record the last failure if any attempt was made.
        return None

    def discharge_all(self, max_attempts: int = 100) -> dict[str, Any]:
        """Attempt to discharge all pending obligations.

        Iterates through ``pending`` obligations, calling :meth:`discharge_one`
        for each.  Repeats until either all are discharged or *max_attempts*
        total application attempts have been made.

        Parameters
        ----------
        max_attempts:
            Safety cap on total rule-application attempts.

        Returns
        -------
        dict
            A summary dict with keys:

            - ``discharged_count``  – number of newly discharged obligations
            - ``remaining_count``   – number still pending
            - ``attempts``          – total application attempts made
            - ``success_rate``      – fraction of attempts that succeeded
        """
        attempts = 0
        discharged_this_run = 0

        # Work on a copy of the list so we can mutate self.pending safely.
        to_process: list[Any] = list(self.pending)

        for obligation in to_process:
            if attempts >= max_attempts:
                break
            result = self.discharge_one(obligation)
            attempts += self.tracker.applications[-1:] and 1 or 0
            if result is not None:
                discharged_this_run += 1
            attempts += 1  # Count the discharge attempt regardless.

        total_apps = len(self.tracker.applications)
        successful_apps = sum(
            1 for a in self.tracker.applications if a.succeeded()
        )
        success_rate = successful_apps / total_apps if total_apps else 0.0

        return {
            "discharged_count": discharged_this_run,
            "remaining_count": len(self.pending),
            "attempts": attempts,
            "success_rate": success_rate,
        }

    def is_fully_discharged(self) -> bool:
        """Return ``True`` iff all obligations have been discharged.

        Returns
        -------
        bool
            ``True`` when ``pending`` is empty.
        """
        return len(self.pending) == 0

    def remaining_obligations(self) -> list[Any]:
        """Return a snapshot of the current pending obligation list.

        Returns
        -------
        list
            A copy of ``self.pending`` (safe to mutate).
        """
        return list(self.pending)

    def evidence_produced(self) -> list[Any]:
        """Collect all evidence items from successful rule applications.

        Iterates over all applications recorded by ``self.tracker``, collects
        ``evidence_produced`` tuples from successful ones, and flattens them
        into a single list.

        Returns
        -------
        list
            All evidence items produced during this discharger's lifetime.
        """
        evidence: list[Any] = []
        for app in self.tracker.applications:
            if app.succeeded():
                evidence.extend(app.evidence_items())
        return evidence

    def explain_failures(self) -> list[str]:
        """Return human-readable explanations for all failed applications.

        Calls :meth:`RuleApplication.failed_reason` for every non-successful
        application in the tracker and returns them as a list, prefixed by
        a description of the application.

        Returns
        -------
        list[str]
            Failure explanations, one per failed application.  Empty list if
            no failures have been recorded.
        """
        explanations: list[str] = []
        for app in self.tracker.failures():
            reason = app.failed_reason() or "Unknown failure"
            explanations.append(
                f"Rule '{app.rule.rule_name}' "
                f"(id={app.application_id}) at {app.timestamp}: {reason}"
            )
        return explanations

    def copilot_suggest_for_undischarged(self) -> list[str]:
        """Ask Copilot to suggest rules for remaining obligations.

        # copilot suggest rules for remaining obligations

        For each pending obligation in ``self.pending``, calls
        :func:`~jugeo.encodings.deduction_rules.algorithms.copilot_suggest_next_rule`
        and collects the top suggestion per obligation.

        Returns
        -------
        list[str]
            Human-readable suggestion strings, one per pending obligation.
            Empty if there are no pending obligations.
        """
        suggestions: list[str] = []
        for obligation in self.pending:
            j_str = str(obligation)
            ranked = copilot_suggest_next_rule(
                current_judgment=j_str,
                goal=j_str,
                available_rules=self.rules,
                proof_history=None,
            )
            if ranked:
                top = ranked[0]
                r: DeductionRule = top["rule"]
                suggestions.append(
                    f"For obligation '{j_str}': try rule '{r.rule_name}' "
                    f"(score={top['score']:.4f}; {top['rationale']})"
                )
            else:
                suggestions.append(
                    f"For obligation '{j_str}': no applicable rule found. "
                    "Consider synthesizing a new rule or relaxing side conditions."
                )
        return suggestions


# ---------------------------------------------------------------------------
# CopilotDeductionAssist
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotDeductionAssist:
    """Copilot bridge for deduction-rule sub-system assistance.

    This class provides AI-assisted guidance for proof construction:

    - **Rule suggestion**: ranks available rules for a given judgment.
    - **Transition explanation**: produces natural-language explanations of
      individual transitions.
    - **Proof completion**: suggests next steps for incomplete proofs.
    - **Failure diagnosis**: explains why a rule application failed.
    - **Rule synthesis**: generates rules for unsatisfied obligations.

    All interaction results are cached (keyed by input hash) and logged
    so that the full interaction history is available for audit.

    Attributes
    ----------
    session:
        Optional :class:`DeductionSession` this assistant is attached to.
    rule_library:
        The pool of rules to suggest from.
    suggestion_cache:
        LRU-style cache mapping input hash → ranked suggestions.
    interaction_log:
        Chronological log of all assistant interactions.
    """

    session: DeductionSession | None = None
    rule_library: list[DeductionRule] = field(default_factory=list)
    suggestion_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    interaction_log: list[dict[str, Any]] = field(default_factory=list)

    def _cache_key(self, *parts: str) -> str:
        """Compute a stable cache key from string *parts*."""
        payload = "\0".join(parts)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def _record_interaction(
        self,
        kind: str,
        input_data: Any,
        output_data: Any,
    ) -> None:
        """Append an interaction record to the log."""
        self.interaction_log.append(
            {
                "kind": kind,
                "input": str(input_data)[:500],
                "output": str(output_data)[:500],
                "timestamp": _now_iso(),
            }
        )

    def suggest_rule(
        self,
        judgment: str,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Suggest applicable rules for *judgment*, ranked by relevance.

        # copilot suggest applicable rules ranked by score

        Uses :func:`~jugeo.encodings.deduction_rules.algorithms.copilot_suggest_next_rule`
        with the session's goal (if a session is attached) or the judgment
        itself as the goal.

        Results are cached by (judgment, context-hash) to avoid redundant
        computation.

        Parameters
        ----------
        judgment:
            The judgment to suggest rules for.
        context:
            Optional ambient context.

        Returns
        -------
        list[dict]
            Ranked list of suggestion dicts (same schema as
            :func:`copilot_suggest_next_rule`).
        """
        goal = self.session.goal if self.session else judgment
        ctx_key = _stable_hash(str(sorted((context or {}).items())))
        cache_key = self._cache_key(judgment, goal, ctx_key)

        if cache_key in self.suggestion_cache:
            cached = self.suggestion_cache[cache_key]
            self._record_interaction("suggest_rule[cached]", judgment, len(cached))
            return cached

        history = self.session.steps if self.session else None
        suggestions = copilot_suggest_next_rule(
            current_judgment=judgment,
            goal=goal,
            available_rules=self.rule_library,
            proof_history=history,
        )
        self.suggestion_cache[cache_key] = suggestions
        self._record_interaction("suggest_rule", judgment, len(suggestions))
        return suggestions

    def explain_transition(self, transition: JudgmentTransition) -> str:
        """Produce a natural-language explanation of *transition*.

        # copilot natural-language explanation

        Describes what the transition does in plain English by:

        1. Identifying the rule kind and name.
        2. Showing the source and target judgments.
        3. Describing the substitution applied.
        4. Noting the trust delta.

        Parameters
        ----------
        transition:
            The transition to explain.

        Returns
        -------
        str
            A multi-sentence natural-language explanation.
        """
        rule = transition.rule_applied
        source = str(transition.source_judgment)
        target = str(transition.target_judgment)
        subst = transition.substitution
        delta = transition.trust_delta

        kind_label: dict[str, str] = {
            RuleKind.STRUCTURAL.value: "structural (context-manipulation)",
            RuleKind.SEMANTIC.value: "semantic (connective-driven)",
            RuleKind.AXIOM.value: "axiom (base-case)",
            RuleKind.DERIVED.value: "derived (admissible)",
        }
        kind_desc = kind_label.get(rule.rule_kind.value, rule.rule_kind.value)

        parts: list[str] = [
            f"The transition applies the {kind_desc} rule '{rule.rule_name}'.",
            f"Starting from judgment '{source}', the rule fires and produces '{target}'.",
        ]

        if subst:
            bindings_str = ", ".join(f"{k} ↦ {v}" for k, v in sorted(subst.items()))
            parts.append(f"The unification produced the substitution: {bindings_str}.")

        if delta > 0:
            parts.append(
                f"The trust level increases by {delta} step(s) as a result of this transition."
            )
        elif delta < 0:
            parts.append(
                f"The trust level decreases by {abs(delta)} step(s) as a result."
            )
        else:
            parts.append("The trust level is unchanged by this transition.")

        if not transition.is_valid():
            parts.append(
                "WARNING: This transition does not pass internal validity checks; "
                "the rule conclusion may not match the target judgment."
            )

        explanation = " ".join(parts)
        self._record_interaction("explain_transition", transition.transition_id, explanation)
        return explanation

    def complete_proof(
        self,
        partial_steps: list[InferenceStep],
        goal: str,
    ) -> dict[str, Any]:
        """Suggest the next step for an incomplete proof.

        # copilot suggest next step for incomplete proof

        Analyses the last output in *partial_steps* and the *goal*, then uses
        :func:`~jugeo.encodings.deduction_rules.algorithms.copilot_suggest_next_rule`
        to produce a ranked list of candidate next steps.  Returns a dict with
        the suggestions and a completion-fraction estimate.

        Parameters
        ----------
        partial_steps:
            Steps taken so far (may be empty).
        goal:
            The ultimate proof goal.

        Returns
        -------
        dict
            Keys:

            - ``suggestions``         – ranked list from copilot_suggest_next_rule
            - ``current_judgment``    – the frontier judgment
            - ``goal``                – the requested goal
            - ``completion_fraction`` – heuristic [0, 1] progress estimate
            - ``is_complete``         – ``True`` if frontier == goal
        """
        if partial_steps:
            current = partial_steps[-1].output
        elif self.session and self.session.initial_judgments:
            current = str(self.session.initial_judgments[0])
        else:
            current = goal  # No starting point; treat as trivially complete.

        is_complete = current.strip() == goal.strip()
        completion_fraction = _token_similarity(current, goal)

        if is_complete:
            self._record_interaction("complete_proof", goal, "already_complete")
            return {
                "suggestions": [],
                "current_judgment": current,
                "goal": goal,
                "completion_fraction": 1.0,
                "is_complete": True,
            }

        suggestions = copilot_suggest_next_rule(
            current_judgment=current,
            goal=goal,
            available_rules=self.rule_library,
            proof_history=partial_steps,
        )
        self._record_interaction("complete_proof", current, len(suggestions))
        return {
            "suggestions": suggestions,
            "current_judgment": current,
            "goal": goal,
            "completion_fraction": completion_fraction,
            "is_complete": False,
        }

    def diagnose_failure(self, application: RuleApplication) -> str:
        """Explain in natural language why a rule application failed.

        # copilot explain why rule application failed

        Converts the :class:`ApplicationResult` into a detailed diagnostic
        message, including what can be done to fix the problem.

        Parameters
        ----------
        application:
            A failed (non-APPLIED) :class:`RuleApplication`.

        Returns
        -------
        str
            A multi-sentence diagnosis and recommended remediation.
        """
        if application.succeeded():
            diag = (
                f"Rule '{application.rule.rule_name}' (id={application.application_id}) "
                "actually succeeded — no failure to diagnose."
            )
            self._record_interaction("diagnose_failure", application.application_id, diag)
            return diag

        reason = application.failed_reason() or "unknown failure"
        rule = application.rule
        bindings = application.bindings

        remediation_map: dict[ApplicationResult, str] = {
            ApplicationResult.UNIFICATION_FAILURE: (
                f"The conclusion schema '{rule.conclusion}' could not be unified with "
                f"the target judgment. Check that the judgment structure matches "
                "the conclusion pattern, or consider rewriting the conclusion schema."
            ),
            ApplicationResult.TRUST_INSUFFICIENT: (
                f"The current trust level is below '{rule.trust_required}'. "
                "Discharge additional trust-raising obligations, or use a rule "
                "with a lower trust threshold."
            ),
            ApplicationResult.SIDE_CONDITION_FAILURE: (
                f"One or more side conditions failed under bindings {bindings!r}. "
                "Review the side conditions on the rule definition and ensure "
                "the context provides the required variable values."
            ),
            ApplicationResult.ERROR: (
                "An unexpected exception was raised during rule firing. "
                f"Error details: {application.context.get('_fire_error', 'no details')}. "
                "Check the rule's fire() logic for defensive error handling."
            ),
            ApplicationResult.INAPPLICABLE: (
                "The rule is inapplicable in the current context. "
                "Verify that the judgment matches the conclusion schema "
                "and that context requirements are satisfied."
            ),
        }

        remediation = remediation_map.get(
            application.result,
            f"Unrecognised failure mode '{application.result.value}'.",
        )
        diag = (
            f"Diagnosis for rule '{rule.rule_name}' "
            f"(application id={application.application_id}, "
            f"result={application.result.value}):\n"
            f"  Primary reason: {reason}\n"
            f"  Remediation:    {remediation}"
        )
        self._record_interaction("diagnose_failure", application.application_id, diag)
        return diag

    def generate_rule_for_obligation(self, obligation: str) -> DeductionRule:
        """Synthesise a new rule whose conclusion discharges *obligation*.

        # copilot synthesize a rule for an obligation

        Calls :func:`~jugeo.encodings.deduction_rules.algorithms.synthesize_rules_for_obligations`
        to generate a rule.  If the synthesiser returns a novel rule, it is
        added to ``self.rule_library``.  If synthesis produces no new rule
        (the obligation is already covered), the covering rule is returned
        instead.

        Parameters
        ----------
        obligation:
            The obligation string to synthesise a rule for.

        Returns
        -------
        DeductionRule
            The newly synthesised rule (or existing covering rule).
        """
        # Check if an existing rule already covers the obligation.
        for rule in self.rule_library:
            subst = rule._try_unify(rule.conclusion, obligation)
            if subst is not None:
                self._record_interaction(
                    "generate_rule_for_obligation",
                    obligation,
                    f"covered_by_existing:{rule.rule_name}",
                )
                return rule

        # Synthesise a new rule.
        new_rules = synthesize_rules_for_obligations(
            obligations=[obligation],
            existing_rules=self.rule_library,
            context=None,
        )

        if new_rules:
            synthesised = new_rules[0]
            # Add to library if not already present.
            existing_ids = {r.rule_id for r in self.rule_library}
            if synthesised.rule_id not in existing_ids:
                self.rule_library.append(synthesised)
            self._record_interaction(
                "generate_rule_for_obligation",
                obligation,
                synthesised.rule_name,
            )
            return synthesised

        # Fallback: create a minimal axiom rule.
        fallback = make_axiom_rule(
            name=f"assume-{_stable_hash(obligation)[:8]}",
            conclusion=obligation,
            synthesis_source="fallback_axiom",
        )
        self.rule_library.append(fallback)
        self._record_interaction(
            "generate_rule_for_obligation",
            obligation,
            f"fallback_axiom:{fallback.rule_name}",
        )
        return fallback

    def interaction_history(self) -> list[dict[str, Any]]:
        """Return a copy of the full interaction log.

        Returns
        -------
        list[dict]
            All recorded interactions, in chronological order.  Each entry
            has keys ``kind``, ``input``, ``output``, and ``timestamp``.
        """
        return list(self.interaction_log)

    def clear_cache(self) -> None:
        """Clear the suggestion cache, forcing re-computation on next queries."""
        self.suggestion_cache.clear()

    def summarize_capabilities(self) -> str:
        """Return a formatted description of this assistant's capabilities.

        Summarises the rule library size, the attached session (if any), the
        cache state, and a list of available interaction methods.

        Returns
        -------
        str
            A multi-line capability summary suitable for display in a help
            dialogue.
        """
        session_info = (
            f"attached session '{self.session.session_id}' (goal: '{self.session.goal}')"
            if self.session
            else "no session attached"
        )
        lines: list[str] = [
            "CopilotDeductionAssist — capabilities:",
            f"  Session:          {session_info}",
            f"  Rule library:     {len(self.rule_library)} rule(s)",
            f"  Cache entries:    {len(self.suggestion_cache)}",
            f"  Interactions:     {len(self.interaction_log)} logged",
            "",
            "  Methods:",
            "    suggest_rule(judgment, context) — rank applicable rules",
            "    explain_transition(t)           — natural-language transition explanation",
            "    complete_proof(steps, goal)     — suggest next step",
            "    diagnose_failure(application)   — explain why a rule failed",
            "    generate_rule_for_obligation(o) — synthesise a new rule",
            "    interaction_history()           — view full interaction log",
            "    clear_cache()                   — reset suggestion cache",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DeductionSession",
    "TransitionSystemRunner",
    "RuleApplicationTracker",
    "JudgmentDischarger",
    "CopilotDeductionAssist",
]
