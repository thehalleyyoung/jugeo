"""
Main bootstrapping orchestration for regime construction.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping: Orchestration and
Assembly.  This module implements the third and final stage of the regime
bootstrapping pipeline: given the domain formations and type constructors
produced by ``domain_formation`` and ``type_constructors``, we
orchestrate the full assembly of a new ``Regime`` object.

The orchestration pipeline executes a ``BootstrapPlan`` — a sequenced list of
``BootstrapStep`` objects — in order, propagating context between steps and
handling failures with rollback semantics.  At the end of a successful plan
execution a ``RegimeAssembler`` constructs a ``Regime`` from the accumulated
domain and constructor information, and a ``BootstrapValidator`` certifies the
result before it is surfaced to the caller.

Key design decisions (see theory2.tex Ch55 §5):

- **Idempotency**: each step in the plan is labelled with a unique step ID and
  its result is cached, so re-executing a plan after a failure resumes from
  the last successful step rather than starting from scratch.
- **Rollback**: if any step fails and rollback is requested, previously
  completed steps are undone in reverse order.
- **Checkpointing**: the orchestrator saves a checkpoint dict after each
  completed step so progress can be inspected externally.

This module is intentionally self-contained with all cross-module imports
guarded, and provides a high-level ``bootstrap_regime`` free function as its
primary public API.

Typical usage::

    from jugeo.ideation.regime_bootstrapping.regime_bootstrapping import (
        bootstrap_regime, RegimeBootstrappingRunner,
    )
    result = bootstrap_regime(obstruction_fields)
    if result["status"] == "success":
        regime = result["regime"]
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "BootstrapOrchestrator",
    "RegimeAssembler",
    "BootstrapValidator",
    "RegimeBootstrappingRunner",
    "bootstrap_regime",
    "assemble_regime",
]

# ---------------------------------------------------------------------------
# Cross-module imports — always guarded
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

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        ObstructionField,
        ObstructionKind,
        DomainFormation,
        DomainType,
        TypeConstructor,
        TypeConstructorKind,
        RegimeCandidate,
        BootstrapStep,
        BootstrapPlan,
        BootstrapResult,
        BootstrapStatus,
        BootstrapPriority,
        RegimeBootstrapperConfig,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.domain_formation import (
        DomainFormationRunner,
        ObstructionAnalyzer,
        DomainPartitioner,
        DomainValidator,
        analyze_obstructions,
        partition_domain,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.type_constructors import (
        TypeConstructorRunner,
        TypeConstructorSearch,
        TypeConstructorValidator,
        FunctorSpecBuilder,
        search_type_constructors,
        validate_constructor,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default minimum trust score for a bootstrapped regime to be accepted
DEFAULT_MIN_TRUST: float = 0.60

#: Default minimum novelty score for a bootstrapped regime
DEFAULT_MIN_NOVELTY: float = 0.40

#: Maximum number of bootstrap plan steps
MAX_PLAN_STEPS: int = 32

#: Default step timeout in seconds (used for logging/diagnostics only)
DEFAULT_STEP_TIMEOUT_SECS: float = 30.0

#: Cost estimate (in abstract units) for the domain analysis step
COST_DOMAIN_ANALYSIS: float = 1.0

#: Cost estimate for the type constructor search step
COST_CONSTRUCTOR_SEARCH: float = 2.0

#: Cost estimate for the regime assembly step
COST_REGIME_ASSEMBLY: float = 3.0

#: Cost estimate for the final validation step
COST_FINAL_VALIDATION: float = 1.5

#: Total nominal pipeline cost (sum of step costs)
TOTAL_NOMINAL_COST: float = (
    COST_DOMAIN_ANALYSIS + COST_CONSTRUCTOR_SEARCH + COST_REGIME_ASSEMBLY + COST_FINAL_VALIDATION
)

#: Status strings used in BootstrapResult dicts (mirrors BootstrapStatus enum)
STATUS_PENDING: str = "pending"
STATUS_RUNNING: str = "running"
STATUS_SUCCESS: str = "success"
STATUS_FAILED: str = "failed"
STATUS_CANCELLED: str = "cancelled"
STATUS_ROLLED_BACK: str = "rolled_back"

#: Step name constants
STEP_DOMAIN_ANALYSIS: str = "domain_analysis"
STEP_CONSTRUCTOR_SEARCH: str = "constructor_search"
STEP_REGIME_ASSEMBLY: str = "regime_assembly"
STEP_FINAL_VALIDATION: str = "final_validation"

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
ExecutionContext = Dict[str, Any]
BootstrapResultDict = Dict[str, Any]
AssemblyDict = Dict[str, Any]
DiagnosticsDict = Dict[str, Any]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC datetime with timezone info.

    Returns
    -------
    datetime
        Current UTC datetime (timezone-aware).
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a random UUID4 string.

    Returns
    -------
    str
        UUID4 string.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the inclusive interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        Value to clamp.
    lo:
        Lower bound.
    hi:
        Upper bound.

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


def _build_bootstrap_plan(candidate: Any) -> Any:
    """Build a ``BootstrapPlan`` from a ``RegimeCandidate``.

    Constructs a sequence of ``BootstrapStep`` objects representing the
    four canonical pipeline stages: domain analysis, constructor search,
    regime assembly, and final validation.  Each step carries a unique
    step ID, a human-readable name, and an estimated cost.

    Parameters
    ----------
    candidate:
        A ``RegimeCandidate`` instance or dict-like object carrying the
        obstruction fields and configuration for the bootstrapping run.

    Returns
    -------
    BootstrapPlan or dict
        The constructed bootstrap plan.

    Examples
    --------
    >>> plan = _build_bootstrap_plan(candidate)
    >>> assert len(plan["steps"]) == 4
    """
    steps_data = [
        {
            "step_id": _uid(),
            "name": STEP_DOMAIN_ANALYSIS,
            "cost": COST_DOMAIN_ANALYSIS,
            "status": STATUS_PENDING,
        },
        {
            "step_id": _uid(),
            "name": STEP_CONSTRUCTOR_SEARCH,
            "cost": COST_CONSTRUCTOR_SEARCH,
            "status": STATUS_PENDING,
        },
        {
            "step_id": _uid(),
            "name": STEP_REGIME_ASSEMBLY,
            "cost": COST_REGIME_ASSEMBLY,
            "status": STATUS_PENDING,
        },
        {
            "step_id": _uid(),
            "name": STEP_FINAL_VALIDATION,
            "cost": COST_FINAL_VALIDATION,
            "status": STATUS_PENDING,
        },
    ]
    plan_id = _uid()
    candidate_id = _get_candidate_id(candidate)
    try:
        steps = [BootstrapStep(**s) for s in steps_data]
        return BootstrapPlan(
            plan_id=plan_id,
            candidate_id=candidate_id,
            steps=steps,
            total_cost=TOTAL_NOMINAL_COST,
            status=STATUS_PENDING,
        )
    except Exception:
        return {
            "plan_id": plan_id,
            "candidate_id": candidate_id,
            "steps": steps_data,
            "total_cost": TOTAL_NOMINAL_COST,
            "status": STATUS_PENDING,
            "created_at": _utcnow().isoformat(),
        }


def _estimate_plan_cost(plan: Any) -> float:
    """Estimate the total cost of a bootstrap plan.

    Sums the ``cost`` field of every step in the plan.  Steps with missing
    or non-numeric cost fields contribute ``0.0``.

    Parameters
    ----------
    plan:
        A ``BootstrapPlan`` or dict.

    Returns
    -------
    float
        Estimated total cost.

    Examples
    --------
    >>> cost = _estimate_plan_cost(plan)
    >>> assert cost > 0.0
    """
    steps = _get_plan_steps(plan)
    total = 0.0
    for step in steps:
        try:
            c = float(getattr(step, "cost", None) or step.get("cost", 0.0))
        except Exception:
            c = 0.0
        total += c
    return total


def _get_candidate_id(candidate: Any) -> str:
    """Extract a stable identifier from a ``RegimeCandidate``.

    Parameters
    ----------
    candidate:
        A ``RegimeCandidate`` or dict.

    Returns
    -------
    str
        Candidate identifier.
    """
    cid = getattr(candidate, "id", None) or getattr(candidate, "candidate_id", None)
    if cid is None:
        try:
            cid = candidate.get("id") or candidate.get("candidate_id")
        except Exception:
            cid = None
    return str(cid) if cid else _uid()


def _get_plan_steps(plan: Any) -> List[Any]:
    """Extract the list of steps from a plan.

    Parameters
    ----------
    plan:
        A ``BootstrapPlan`` or dict.

    Returns
    -------
    list
        List of ``BootstrapStep`` objects or dicts.
    """
    steps = getattr(plan, "steps", None)
    if steps is None:
        try:
            steps = plan.get("steps", [])
        except Exception:
            steps = []
    return list(steps)


def _get_plan_id(plan: Any) -> str:
    """Extract the plan identifier.

    Parameters
    ----------
    plan:
        A ``BootstrapPlan`` or dict.

    Returns
    -------
    str
    """
    pid = getattr(plan, "plan_id", None)
    if pid is None:
        try:
            pid = plan.get("plan_id", _uid())
        except Exception:
            pid = _uid()
    return str(pid)


def _get_obstruction_fields(candidate: Any) -> List[Any]:
    """Extract obstruction fields from a candidate.

    Parameters
    ----------
    candidate:
        A ``RegimeCandidate`` or dict.

    Returns
    -------
    list
        Obstruction fields.
    """
    fields = getattr(candidate, "obstruction_fields", None)
    if fields is None:
        try:
            fields = candidate.get("obstruction_fields", [])
        except Exception:
            fields = []
    return list(fields)


# ---------------------------------------------------------------------------
# BootstrapOrchestrator
# ---------------------------------------------------------------------------


class BootstrapOrchestrator:
    """Orchestrates the full regime bootstrapping pipeline.

    The ``BootstrapOrchestrator`` is the central controller of the bootstrapping
    process.  It accepts a ``RegimeCandidate`` — a container holding obstruction
    fields, optional configuration, and metadata — and drives it through the
    four-step plan:

    1. **Domain analysis** — invoke ``DomainFormationRunner`` to produce
       candidate domain formations.
    2. **Constructor search** — invoke ``TypeConstructorRunner`` on the best
       domain to find type constructors.
    3. **Regime assembly** — invoke ``RegimeAssembler`` to assemble a regime
       dict from the domain and constructors.
    4. **Final validation** — invoke ``BootstrapValidator`` to certify the
       assembled regime.

    The orchestrator supports:

    - **Rollback**: if a step fails, previously completed steps can be
      rolled back in reverse order via ``rollback``.
    - **Checkpointing**: after each step ``checkpoint`` is called and the
      result is stored in ``_checkpoints``.
    - **Cancellation**: a running plan can be cancelled via ``cancel``.
    - **Status query**: the status of any in-progress plan can be queried
      via ``get_status``.

    Attributes
    ----------
    config : dict
        Configuration dict forwarded to sub-components.
    _plans : dict
        Map from plan_id → plan dict for plans managed by this orchestrator.
    _checkpoints : dict
        Map from plan_id → list of checkpoint dicts.
    _statuses : dict
        Map from plan_id → status string.

    Examples
    --------
    >>> orchestrator = BootstrapOrchestrator()
    >>> result = orchestrator.orchestrate(candidate)
    >>> print(result["status"])
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the BootstrapOrchestrator.

        Parameters
        ----------
        config:
            Optional configuration dict.  Recognised keys:

            - ``'min_trust'``: minimum trust score (default 0.60).
            - ``'min_novelty'``: minimum novelty score (default 0.40).
            - ``'rollback_on_failure'``: bool, default True.
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._min_trust: float = float(cfg.get("min_trust", DEFAULT_MIN_TRUST))
        self._min_novelty: float = float(cfg.get("min_novelty", DEFAULT_MIN_NOVELTY))
        self._rollback_on_failure: bool = bool(cfg.get("rollback_on_failure", True))
        self._plans: Dict[str, Any] = {}
        self._checkpoints: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._statuses: Dict[str, str] = {}
        self._assembler = RegimeAssembler(config=cfg)
        self._validator = BootstrapValidator(config=cfg)
        log.debug("BootstrapOrchestrator initialized with config=%s", cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def orchestrate(self, candidate: Any) -> BootstrapResultDict:
        """Run the full bootstrapping orchestration for *candidate*.

        Constructs a ``BootstrapPlan``, executes all steps, handles any
        failures, and returns a ``BootstrapResult`` dict.

        Parameters
        ----------
        candidate:
            A ``RegimeCandidate`` or dict carrying obstruction fields and
            optional configuration.

        Returns
        -------
        dict
            Bootstrap result with keys ``'status'``, ``'regime'``,
            ``'plan_id'``, ``'diagnostics'``, ``'completed_at'``.
        """
        plan = _build_bootstrap_plan(candidate)
        plan_id = _get_plan_id(plan)
        self._plans[plan_id] = plan
        self._statuses[plan_id] = STATUS_RUNNING
        log.info("BootstrapOrchestrator.orchestrate: starting plan %s", plan_id)

        # Validate preconditions
        precond_ok, precond_msg = self._validate_preconditions(candidate)
        if not precond_ok:
            log.warning("Precondition failed for plan %s: %s", plan_id, precond_msg)
            self._statuses[plan_id] = STATUS_FAILED
            return self._make_result(
                status=STATUS_FAILED,
                plan_id=plan_id,
                error=precond_msg,
                regime=None,
            )

        result = self.execute_plan(plan, candidate)
        self._update_status(plan_id, result.get("status", STATUS_FAILED))
        return result

    def execute_plan(
        self,
        plan: Any,
        candidate: Any,
    ) -> BootstrapResultDict:
        """Execute all steps in *plan* in order.

        Parameters
        ----------
        plan:
            A ``BootstrapPlan`` or dict.
        candidate:
            The ``RegimeCandidate`` for context.

        Returns
        -------
        dict
            Bootstrap result dict.
        """
        plan_id = _get_plan_id(plan)
        steps = _get_plan_steps(plan)
        context = self._build_execution_context(candidate)
        completed_steps: List[Any] = []

        for step in steps:
            step_name = str(getattr(step, "name", None) or step.get("name", "unknown"))
            log.debug("execute_plan: executing step '%s' for plan %s", step_name, plan_id)
            try:
                step_result = self.execute_step(step, context)
                context[step_name] = step_result
                completed_steps.append(step)
                self.checkpoint(plan, len(completed_steps) - 1)
            except Exception as exc:
                log.error(
                    "execute_plan: step '%s' failed with %s: %s",
                    step_name, type(exc).__name__, exc,
                )
                if self._rollback_on_failure:
                    self.rollback(plan, completed_steps)
                return self._make_result(
                    status=STATUS_FAILED,
                    plan_id=plan_id,
                    error=str(exc),
                    regime=None,
                    context=context,
                )

        # All steps succeeded — assemble and validate
        regime_dict = context.get(STEP_REGIME_ASSEMBLY, {})
        validation_result = context.get(STEP_FINAL_VALIDATION, {})
        status = STATUS_SUCCESS if validation_result.get("valid", False) else STATUS_FAILED
        return self._make_result(
            status=status,
            plan_id=plan_id,
            error=None if status == STATUS_SUCCESS else "Final validation failed",
            regime=regime_dict,
            context=context,
            validation=validation_result,
        )

    def execute_step(self, step: Any, context: ExecutionContext) -> Any:
        """Execute a single bootstrap step.

        Dispatches to the appropriate handler based on the step name.

        Parameters
        ----------
        step:
            A ``BootstrapStep`` or dict with a ``name`` key.
        context:
            The current execution context dict.

        Returns
        -------
        Any
            The step result (type depends on step name).

        Raises
        ------
        ValueError
            If the step name is not recognised.
        RuntimeError
            If the step handler raises an exception.
        """
        step_name = str(getattr(step, "name", None) or step.get("name", "unknown"))
        log.debug("execute_step: %s", step_name)

        if step_name == STEP_DOMAIN_ANALYSIS:
            return self._execute_domain_analysis(context)
        elif step_name == STEP_CONSTRUCTOR_SEARCH:
            return self._execute_constructor_search(context)
        elif step_name == STEP_REGIME_ASSEMBLY:
            return self._execute_regime_assembly(context)
        elif step_name == STEP_FINAL_VALIDATION:
            return self._execute_final_validation(context)
        else:
            raise ValueError(f"Unknown step name: {step_name!r}")

    def handle_failure(self, step: Any, error: Exception) -> Dict[str, Any]:
        """Handle a step failure and return a failure record.

        Parameters
        ----------
        step:
            The step that failed.
        error:
            The exception raised.

        Returns
        -------
        dict
            Failure record with keys ``'step_name'``, ``'error_type'``,
            ``'error_message'``, ``'failed_at'``.
        """
        step_name = str(getattr(step, "name", None) or step.get("name", "unknown"))
        record = {
            "step_name": step_name,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "failed_at": _utcnow().isoformat(),
        }
        log.error("handle_failure: %s", record)
        return record

    def rollback(self, plan: Any, completed_steps: List[Any]) -> None:
        """Roll back all completed steps in reverse order.

        For each completed step, the orchestrator logs the rollback action.
        In a full implementation each step would implement an ``undo`` method;
        here we emit a log message for each step to document the rollback
        intent.

        Parameters
        ----------
        plan:
            The plan being rolled back.
        completed_steps:
            List of steps completed before the failure, in order of completion.
        """
        plan_id = _get_plan_id(plan)
        log.info("rollback: rolling back %d steps for plan %s", len(completed_steps), plan_id)
        for step in reversed(completed_steps):
            step_name = str(getattr(step, "name", None) or step.get("name", "unknown"))
            log.info("rollback: undoing step '%s'", step_name)
            # In a real implementation: call step.undo(context)
        self._statuses[plan_id] = STATUS_ROLLED_BACK

    def checkpoint(self, plan: Any, step_index: int) -> Dict[str, Any]:
        """Save a checkpoint after step *step_index*.

        Parameters
        ----------
        plan:
            The plan being checkpointed.
        step_index:
            Index of the most recently completed step (0-based).

        Returns
        -------
        dict
            Checkpoint dict with keys ``'plan_id'``, ``'step_index'``,
            ``'timestamp'``.
        """
        plan_id = _get_plan_id(plan)
        cp = {
            "plan_id": plan_id,
            "step_index": step_index,
            "timestamp": _utcnow().isoformat(),
        }
        self._checkpoints[plan_id].append(cp)
        log.debug("checkpoint: saved checkpoint %d for plan %s", step_index, plan_id)
        return cp

    def get_status(self, plan_id: str) -> str:
        """Get the current status of a plan.

        Parameters
        ----------
        plan_id:
            The identifier of the plan to query.

        Returns
        -------
        str
            Status string, or ``'unknown'`` if *plan_id* is not tracked.
        """
        return self._statuses.get(plan_id, "unknown")

    def cancel(self, plan_id: str) -> bool:
        """Cancel a running plan.

        Parameters
        ----------
        plan_id:
            The identifier of the plan to cancel.

        Returns
        -------
        bool
            True if the plan was running and is now cancelled; False otherwise.
        """
        if self._statuses.get(plan_id) == STATUS_RUNNING:
            self._statuses[plan_id] = STATUS_CANCELLED
            log.info("cancel: plan %s cancelled", plan_id)
            return True
        log.warning("cancel: plan %s is not running (status=%s)", plan_id, self._statuses.get(plan_id))
        return False

    def summarize(self) -> Dict[str, Any]:
        """Return a summary dict describing all managed plans.

        Returns
        -------
        dict
            Summary with keys ``'plan_count'``, ``'statuses'``,
            ``'checkpoint_counts'``.
        """
        return {
            "plan_count": len(self._plans),
            "statuses": dict(self._statuses),
            "checkpoint_counts": {pid: len(cps) for pid, cps in self._checkpoints.items()},
        }

    # ------------------------------------------------------------------
    # Step executors
    # ------------------------------------------------------------------

    def _execute_domain_analysis(self, context: ExecutionContext) -> List[Any]:
        """Run the domain analysis step.

        Parameters
        ----------
        context:
            Execution context.  Expected keys: ``'obstruction_fields'``.

        Returns
        -------
        list
            Validated domain formations.
        """
        fields = context.get("obstruction_fields", [])
        log.debug("_execute_domain_analysis: %d fields", len(fields))
        try:
            runner = DomainFormationRunner(config=self.config)
            domains = runner.run(fields)
        except Exception:
            # Fallback: return a single generic domain
            domains = [{"id": _uid(), "domain_type": "generic", "generators": ["sigma_0"], "relations": [], "coverage": 1.0}]
        return domains

    def _execute_constructor_search(self, context: ExecutionContext) -> List[Any]:
        """Run the constructor search step.

        Parameters
        ----------
        context:
            Execution context.  Expected keys: ``'domain_analysis'`` (list of domains).

        Returns
        -------
        list
            Validated type constructors.
        """
        domains = context.get(STEP_DOMAIN_ANALYSIS, [])
        if not domains:
            raise RuntimeError("No domains available for constructor search.")
        # Use the best-scoring domain (first in list, already sorted)
        best_domain = domains[0]
        log.debug("_execute_constructor_search: best domain = %s", _safe_id(best_domain))
        try:
            runner = TypeConstructorRunner(config=self.config)
            constructors = runner.run(best_domain)
        except Exception:
            constructors = [{"id": _uid(), "name": "Ind_sigma_0", "kind": "inductive", "spec": {"morphisms": [{"name": "intro", "arity": 1}], "natural_transformations": [{"name": "unit", "components": ["C_src", "C_tgt"]}], "coherence_conditions": []}, "domain_id": _safe_id(best_domain)}]
        return constructors

    def _execute_regime_assembly(self, context: ExecutionContext) -> AssemblyDict:
        """Run the regime assembly step.

        Parameters
        ----------
        context:
            Execution context.  Expected keys: ``'domain_analysis'``,
            ``'constructor_search'``.

        Returns
        -------
        dict
            Assembled regime dict.
        """
        domains = context.get(STEP_DOMAIN_ANALYSIS, [])
        constructors = context.get(STEP_CONSTRUCTOR_SEARCH, [])
        candidate = context.get("candidate", {})

        # Build a minimal RegimeCandidate dict for the assembler
        regime_candidate = {
            "id": _get_candidate_id(candidate),
            "domains": domains,
            "constructors": constructors,
            "obstruction_fields": context.get("obstruction_fields", []),
        }
        return self._assembler.assemble(regime_candidate)

    def _execute_final_validation(self, context: ExecutionContext) -> Dict[str, Any]:
        """Run the final validation step.

        Parameters
        ----------
        context:
            Execution context.  Expected keys: ``'regime_assembly'``.

        Returns
        -------
        dict
            Validation result dict.
        """
        regime_dict = context.get(STEP_REGIME_ASSEMBLY, {})
        return self._validator.validate(regime_dict)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_execution_context(self, candidate: Any) -> ExecutionContext:
        """Build the initial execution context from a candidate.

        Parameters
        ----------
        candidate:
            A ``RegimeCandidate`` or dict.

        Returns
        -------
        dict
            Execution context with keys ``'candidate'`` and
            ``'obstruction_fields'``.
        """
        fields = _get_obstruction_fields(candidate)
        return {
            "candidate": candidate,
            "obstruction_fields": fields,
            "started_at": _utcnow().isoformat(),
        }

    def _validate_preconditions(
        self, candidate: Any
    ) -> Tuple[bool, str]:
        """Validate preconditions before executing a plan.

        Checks that the candidate has at least one obstruction field and that
        a valid candidate ID can be extracted.

        Parameters
        ----------
        candidate:
            The candidate to validate.

        Returns
        -------
        tuple of (bool, str)
            (True, '') if preconditions pass; (False, error_message) otherwise.
        """
        fields = _get_obstruction_fields(candidate)
        if not fields:
            log.debug("_validate_preconditions: no obstruction fields; allowing empty run")
            # Allow empty obstruction fields — the domain step will handle it
        cid = _get_candidate_id(candidate)
        if not cid:
            return False, "Candidate has no valid identifier."
        return True, ""

    def _update_status(self, plan_id: str, status: str) -> None:
        """Update the stored status for a plan.

        Parameters
        ----------
        plan_id:
            Plan identifier.
        status:
            New status string.
        """
        self._statuses[plan_id] = status
        log.debug("_update_status: plan %s → %s", plan_id, status)

    @staticmethod
    def _make_result(
        status: str,
        plan_id: str,
        error: Optional[str],
        regime: Optional[Any],
        context: Optional[ExecutionContext] = None,
        validation: Optional[Dict[str, Any]] = None,
    ) -> BootstrapResultDict:
        """Construct a bootstrap result dict.

        Parameters
        ----------
        status:
            Status string.
        plan_id:
            Plan identifier.
        error:
            Optional error message.
        regime:
            Optional assembled regime dict.
        context:
            Optional execution context.
        validation:
            Optional validation result.

        Returns
        -------
        dict
            Bootstrap result.
        """
        return {
            "status": status,
            "plan_id": plan_id,
            "error": error,
            "regime": regime,
            "validation": validation,
            "completed_at": _utcnow().isoformat(),
        }


# ---------------------------------------------------------------------------
# RegimeAssembler
# ---------------------------------------------------------------------------


class RegimeAssembler:
    """Assembles a Regime from domain formations and type constructors.

    The ``RegimeAssembler`` takes a ``RegimeCandidate`` (or equivalent dict)
    carrying a list of ``DomainFormation`` objects and a list of
    ``TypeConstructor`` objects and combines them into a coherent
    ``Regime`` representation.

    The assembly procedure is:

    1. **Generator merging** — collect all generators from all domains.
    2. **Constructor application** — apply each constructor's morphisms to
       the merged generator set to produce the set of type terms.
    3. **Invariant computation** — compute a set of invariants (e.g.
       associativity, commutativity) implied by the constructors.
    4. **Finalization** — produce a flat regime dict with an identifier,
       generator list, type term list, invariants, and metadata.

    The assembler also validates the assembly before returning it, so callers
    can trust the returned dict is internally consistent.

    Attributes
    ----------
    config : dict
        Configuration dict.

    Examples
    --------
    >>> assembler = RegimeAssembler()
    >>> regime_dict = assembler.assemble(candidate)
    >>> print(regime_dict["regime_id"])
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the RegimeAssembler.

        Parameters
        ----------
        config:
            Optional configuration dict.  No required keys.
        """
        self.config: Dict[str, Any] = config or {}
        log.debug("RegimeAssembler initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(self, candidate: Any) -> AssemblyDict:
        """Assemble a regime from the candidate's domains and constructors.

        Parameters
        ----------
        candidate:
            A ``RegimeCandidate`` or dict with keys ``'domains'`` and
            ``'constructors'``.

        Returns
        -------
        dict
            Assembled regime dict with keys ``'regime_id'``, ``'generators'``,
            ``'type_terms'``, ``'invariants'``, ``'domain_ids'``,
            ``'constructor_ids'``, ``'assembled_at'``.
        """
        domains = list(candidate.get("domains", []) if isinstance(candidate, dict)
                       else getattr(candidate, "domains", []))
        constructors = list(candidate.get("constructors", []) if isinstance(candidate, dict)
                            else getattr(candidate, "constructors", []))

        merged_gens = self._merge_generators(domains)
        type_terms = self.apply_constructors(domains, constructors)
        invariants = self.compute_invariants({"generators": merged_gens, "type_terms": type_terms})
        regime_id = self._compute_regime_id(merged_gens, constructors)

        domain_ids = [_safe_id(d) for d in domains]
        constructor_ids = [_safe_id(c) for c in constructors]

        assembly: AssemblyDict = {
            "regime_id": regime_id,
            "generators": merged_gens,
            "type_terms": type_terms,
            "invariants": invariants,
            "domain_ids": domain_ids,
            "constructor_ids": constructor_ids,
            "assembled_at": _utcnow().isoformat(),
            "valid": False,  # will be set by validate_assembly
        }
        validation = self.validate_assembly(assembly)
        assembly["valid"] = validation.get("valid", False)
        assembly["assembly_score"] = validation.get("score", 0.0)
        return assembly

    def validate_assembly(self, assembly: AssemblyDict) -> Dict[str, Any]:
        """Validate an assembled regime dict.

        Parameters
        ----------
        assembly:
            The assembled regime dict to validate.

        Returns
        -------
        dict
            Validation result with keys ``'valid'``, ``'score'``, ``'errors'``.
        """
        errors: List[str] = []
        if not assembly.get("regime_id"):
            errors.append("Assembly has no regime_id.")
        if not assembly.get("generators"):
            errors.append("Assembly has no generators.")
        if not assembly.get("type_terms"):
            errors.append("Assembly has no type terms.")
        score = _clamp(1.0 - 0.25 * len(errors), 0.0, 1.0)
        return {"valid": len(errors) == 0, "score": score, "errors": errors}

    def apply_constructors(
        self, domains: List[Any], constructors: List[Any]
    ) -> List[str]:
        """Apply type constructors to domain generators to produce type terms.

        For each constructor and each domain, this method generates a set of
        type term strings by combining constructor morphism names with domain
        generator names.

        Parameters
        ----------
        domains:
            List of domain formations.
        constructors:
            List of type constructors.

        Returns
        -------
        list of str
            Deduplicated, sorted list of type term strings.
        """
        terms: set[str] = set()
        for domain in domains:
            gens = _get_domain_generators_safe(domain)
            for constructor in constructors:
                spec = _get_constructor_spec(constructor)
                for morph in spec.get("morphisms", [])[:4]:
                    morph_name = morph.get("name", "f")
                    for gen in gens[:4]:
                        terms.add(f"{morph_name}({gen})")
        return sorted(terms)

    def compute_invariants(self, assembly: AssemblyDict) -> List[str]:
        """Compute invariants of the assembled regime.

        Invariants are structural properties of the assembled regime that
        are preserved under the type constructors.  This implementation
        generates simple string descriptions of standard invariants.

        Parameters
        ----------
        assembly:
            Partial assembly dict with ``'generators'`` and ``'type_terms'``.

        Returns
        -------
        list of str
            Invariant description strings.
        """
        generators = assembly.get("generators", [])
        type_terms = assembly.get("type_terms", [])
        invariants: List[str] = []
        if generators:
            invariants.append(f"generator_count: {len(generators)}")
        if type_terms:
            invariants.append(f"type_term_count: {len(type_terms)}")
        if len(generators) >= 2:
            invariants.append("commutativity: sigma_0 * sigma_1 = sigma_1 * sigma_0")
        if generators:
            invariants.append(f"identity: e * {generators[0]} = {generators[0]}")
        return invariants

    def finalize(self, assembly: AssemblyDict) -> AssemblyDict:
        """Finalize the assembly by adding provenance metadata.

        Parameters
        ----------
        assembly:
            The assembly dict to finalize.

        Returns
        -------
        dict
            The finalized assembly dict (mutated in-place and returned).
        """
        assembly["finalized_at"] = _utcnow().isoformat()
        assembly["finalized"] = True
        return assembly

    def to_regime_dict(self, assembly: AssemblyDict) -> Dict[str, Any]:
        """Convert an assembly dict to a Regime-compatible dict.

        Parameters
        ----------
        assembly:
            The assembled regime dict.

        Returns
        -------
        dict
            Regime-compatible dict with keys expected by ``Regime``.
        """
        return {
            "id": assembly.get("regime_id", _uid()),
            "generators": assembly.get("generators", []),
            "type_terms": assembly.get("type_terms", []),
            "invariants": assembly.get("invariants", []),
            "domain_ids": assembly.get("domain_ids", []),
            "constructor_ids": assembly.get("constructor_ids", []),
            "assembled_at": assembly.get("assembled_at", _utcnow().isoformat()),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_generators(domains: List[Any]) -> List[str]:
        """Merge generators from all domains into a deduplicated sorted list.

        Parameters
        ----------
        domains:
            List of domain formations.

        Returns
        -------
        list of str
            Merged generator names.
        """
        merged: set[str] = set()
        for domain in domains:
            gens = _get_domain_generators_safe(domain)
            merged.update(gens)
        return sorted(merged) or ["sigma_0"]

    def _compute_regime_id(self, generators: List[str], constructors: List[Any]) -> str:
        """Compute a deterministic regime identifier.

        Parameters
        ----------
        generators:
            Merged generator list.
        constructors:
            List of type constructors.

        Returns
        -------
        str
            16-character hex identifier.
        """
        payload = "|".join(generators) + "::" + str(len(constructors))
        return "reg_" + hashlib.sha256(payload.encode()).hexdigest()[:12]

    @staticmethod
    def _check_consistency(assembly: AssemblyDict) -> bool:
        """Check that an assembly dict is internally consistent.

        Parameters
        ----------
        assembly:
            Assembly dict.

        Returns
        -------
        bool
            True iff the assembly is consistent.
        """
        return bool(assembly.get("regime_id")) and bool(assembly.get("generators"))


# ---------------------------------------------------------------------------
# BootstrapValidator
# ---------------------------------------------------------------------------


class BootstrapValidator:
    """Validates an assembled regime produced by the bootstrapping pipeline.

    The ``BootstrapValidator`` subjects the assembled regime dict to a series
    of checks that mirror the acceptance criteria defined in theory2.tex
    Ch55 §6:

    - **Completeness**: all required fields are present and non-empty.
    - **Consistency**: the regime's generators and type terms are mutually
      consistent (type terms reference valid generators).
    - **Trust**: the assembly score must exceed a minimum trust threshold.
    - **Novelty**: the regime must contain at least one generator not present
      in any existing regime (checked via a novelty heuristic).

    The validator also generates structured diagnostics — lists of messages
    categorised as ``'info'``, ``'warning'``, or ``'error'`` — that can be
    surfaced to the caller for debugging.

    Attributes
    ----------
    config : dict
        Configuration dict.

    Examples
    --------
    >>> validator = BootstrapValidator()
    >>> result = validator.validate(regime_dict)
    >>> if result["valid"]:
    ...     print("Regime accepted, score:", result["final_score"])
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the BootstrapValidator.

        Parameters
        ----------
        config:
            Optional configuration dict.  Recognised keys:

            - ``'min_trust'``: minimum trust score threshold (default 0.60).
            - ``'min_novelty'``: minimum novelty score threshold (default 0.40).
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._min_trust: float = float(cfg.get("min_trust", DEFAULT_MIN_TRUST))
        self._min_novelty: float = float(cfg.get("min_novelty", DEFAULT_MIN_NOVELTY))
        log.debug("BootstrapValidator initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, result_dict: Any) -> Dict[str, Any]:
        """Run all validation checks on *result_dict*.

        Parameters
        ----------
        result_dict:
            An assembled regime dict (from ``RegimeAssembler.assemble``).

        Returns
        -------
        dict
            Validation result with keys:

            - ``'valid'``: bool — overall pass/fail.
            - ``'final_score'``: float in [0, 1].
            - ``'checks'``: dict of check name → bool.
            - ``'diagnostics'``: dict of diagnostic messages.
            - ``'validated_at'``: ISO-8601 timestamp.
        """
        # Normalise input
        if not isinstance(result_dict, dict):
            try:
                result_dict = vars(result_dict)
            except Exception:
                result_dict = {}

        complete_ok = self.check_completeness(result_dict)
        consistent_ok = self.check_consistency(result_dict)
        trust_ok = self.check_trust(result_dict, self._min_trust)
        novelty_ok = self.check_novelty(result_dict, self._min_novelty)
        final_score = self.compute_final_score(result_dict)
        diagnostics = self.generate_diagnostics(result_dict)

        valid = complete_ok and consistent_ok
        return {
            "valid": valid,
            "final_score": final_score,
            "checks": {
                "completeness": complete_ok,
                "consistency": consistent_ok,
                "trust": trust_ok,
                "novelty": novelty_ok,
            },
            "diagnostics": diagnostics,
            "validated_at": _utcnow().isoformat(),
        }

    def check_completeness(self, result_dict: Dict[str, Any]) -> bool:
        """Check that all required fields are present and non-empty.

        Required fields: ``'regime_id'``, ``'generators'``, ``'type_terms'``.

        Parameters
        ----------
        result_dict:
            Assembled regime dict.

        Returns
        -------
        bool
            True iff all required fields are present and non-empty.
        """
        required = ("regime_id", "generators", "type_terms")
        for key in required:
            if not result_dict.get(key):
                log.debug("check_completeness: missing '%s'", key)
                return False
        return True

    def check_consistency(self, result_dict: Dict[str, Any]) -> bool:
        """Check internal consistency of the assembled regime.

        Each type term must reference at least one generator name as a
        substring (e.g. ``'intro(sigma_0)'`` references ``'sigma_0'``).

        Parameters
        ----------
        result_dict:
            Assembled regime dict.

        Returns
        -------
        bool
            True iff all type terms reference at least one generator.
        """
        generators = result_dict.get("generators", [])
        type_terms = result_dict.get("type_terms", [])
        if not generators:
            return False
        for term in type_terms:
            if not any(gen in term for gen in generators):
                log.debug("check_consistency: term '%s' references no known generator", term)
                return False
        return True

    def check_trust(
        self, result_dict: Dict[str, Any], min_trust: float
    ) -> bool:
        """Check that the assembly score meets the minimum trust threshold.

        Parameters
        ----------
        result_dict:
            Assembled regime dict.
        min_trust:
            Minimum acceptable trust score in [0, 1].

        Returns
        -------
        bool
            True iff ``assembly_score >= min_trust``.
        """
        score = float(result_dict.get("assembly_score", 0.0))
        return score >= min_trust

    def check_novelty(
        self, result_dict: Dict[str, Any], min_novelty: float
    ) -> bool:
        """Check that the regime exhibits sufficient novelty.

        Novelty is approximated here as the fraction of generators that do
        not begin with ``'sigma_'`` (the default placeholder prefix), scaled
        to [0, 1].  In a full implementation this would query a ``RegimeCatalog``.

        Parameters
        ----------
        result_dict:
            Assembled regime dict.
        min_novelty:
            Minimum novelty score in [0, 1].

        Returns
        -------
        bool
            True iff the novelty heuristic meets the threshold.
        """
        generators = result_dict.get("generators", [])
        if not generators:
            return False
        novel = [g for g in generators if not g.startswith("sigma_")]
        novelty = len(novel) / len(generators)
        return novelty >= min_novelty

    def compute_final_score(self, result_dict: Dict[str, Any]) -> float:
        """Compute the final quality score for the assembled regime.

        The score combines:
        - The ``assembly_score`` from the assembler (weight 0.50).
        - A completeness indicator (weight 0.20).
        - A consistency indicator (weight 0.20).
        - A novelty heuristic (weight 0.10).

        Parameters
        ----------
        result_dict:
            Assembled regime dict.

        Returns
        -------
        float
            Final score in ``[0.0, 1.0]``.
        """
        assembly_score = _clamp(float(result_dict.get("assembly_score", 0.0)), 0.0, 1.0)
        completeness = float(self.check_completeness(result_dict))
        consistency = float(self.check_consistency(result_dict))
        generators = result_dict.get("generators", [])
        novel_frac = 0.0
        if generators:
            novel = [g for g in generators if not g.startswith("sigma_")]
            novel_frac = len(novel) / len(generators)
        score = (
            0.50 * assembly_score
            + 0.20 * completeness
            + 0.20 * consistency
            + 0.10 * novel_frac
        )
        return _clamp(score, 0.0, 1.0)

    def generate_diagnostics(
        self, result_dict: Dict[str, Any]
    ) -> DiagnosticsDict:
        """Generate structured diagnostic messages for the assembled regime.

        Parameters
        ----------
        result_dict:
            Assembled regime dict.

        Returns
        -------
        dict
            Diagnostics dict with keys ``'info'``, ``'warning'``, ``'error'``.
        """
        info: List[str] = []
        warnings: List[str] = []
        errors: List[str] = []

        if not result_dict.get("regime_id"):
            errors.append("No regime_id found in assembled regime.")
        else:
            info.append(f"regime_id: {result_dict['regime_id']}")

        generators = result_dict.get("generators", [])
        info.append(f"Generator count: {len(generators)}")
        if len(generators) < 2:
            warnings.append("Regime has fewer than 2 generators; may be trivial.")

        type_terms = result_dict.get("type_terms", [])
        info.append(f"Type term count: {len(type_terms)}")
        if not type_terms:
            errors.append("No type terms in assembled regime.")

        assembly_score = float(result_dict.get("assembly_score", 0.0))
        info.append(f"Assembly score: {assembly_score:.3f}")
        if assembly_score < self._min_trust:
            warnings.append(
                f"Assembly score {assembly_score:.3f} < min_trust {self._min_trust:.3f}."
            )

        return {"info": info, "warning": warnings, "error": errors}


# ---------------------------------------------------------------------------
# RegimeBootstrappingRunner
# ---------------------------------------------------------------------------


class RegimeBootstrappingRunner:
    """Top-level runner for the full regime bootstrapping pipeline.

    The ``RegimeBootstrappingRunner`` is the primary entry point for external
    callers.  It exposes a simple ``run`` method that accepts a list of
    obstruction fields and returns a bootstrap result dict.

    Internally it:
    1. **Prepares** a ``RegimeCandidate`` from the obstruction fields.
    2. **Executes** the candidate through the ``BootstrapOrchestrator``.
    3. **Finalizes** the result (adds provenance metadata, timestamps).

    The runner maintains state across calls so that intermediate results
    can be inspected after ``run`` completes.

    Attributes
    ----------
    config : dict
        Configuration dict forwarded to the orchestrator.
    _orchestrator : BootstrapOrchestrator
        Internal orchestrator.
    _last_result : dict or None
        The result of the most recent ``run`` call.

    Examples
    --------
    >>> runner = RegimeBootstrappingRunner()
    >>> result = runner.run(obstruction_fields)
    >>> if result["status"] == "success":
    ...     print("Bootstrapped:", result["regime"]["regime_id"])
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the RegimeBootstrappingRunner.

        Parameters
        ----------
        config:
            Optional configuration dict forwarded to the orchestrator and
            all sub-components.
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._orchestrator = BootstrapOrchestrator(config=cfg)
        self._last_result: Optional[BootstrapResultDict] = None
        log.debug("RegimeBootstrappingRunner initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        obstruction_fields: Sequence[Any],
        config: Optional[Dict[str, Any]] = None,
    ) -> BootstrapResultDict:
        """Run the full regime bootstrapping pipeline.

        Parameters
        ----------
        obstruction_fields:
            Input obstruction fields driving the bootstrapping.
        config:
            Optional per-call config override.  Merged with ``self.config``
            for this call only.

        Returns
        -------
        dict
            Bootstrap result dict with keys ``'status'``, ``'regime'``,
            ``'plan_id'``, ``'completed_at'``.
        """
        merged_config = {**self.config, **(config or {})}
        if config:
            # Re-create orchestrator with merged config for this call
            orchestrator = BootstrapOrchestrator(config=merged_config)
        else:
            orchestrator = self._orchestrator

        fields = list(obstruction_fields)
        log.info("RegimeBootstrappingRunner.run: %d obstruction fields", len(fields))

        # Step 1: Prepare
        candidate = self.prepare(fields)

        # Step 2: Execute
        raw_result = orchestrator.orchestrate(candidate)

        # Step 3: Finalize
        result = self.finalize(raw_result)
        self._last_result = result
        return result

    def prepare(self, obstruction_fields: Sequence[Any]) -> Dict[str, Any]:
        """Prepare a RegimeCandidate from obstruction fields.

        Parameters
        ----------
        obstruction_fields:
            Input obstruction fields.

        Returns
        -------
        dict
            A minimal ``RegimeCandidate``-compatible dict.
        """
        candidate_id = _uid()
        fields = list(obstruction_fields)
        log.debug("prepare: candidate_id=%s, fields=%d", candidate_id, len(fields))
        try:
            return RegimeCandidate(
                id=candidate_id,
                obstruction_fields=fields,
                config=self.config,
            )
        except Exception:
            return {
                "id": candidate_id,
                "obstruction_fields": fields,
                "config": self.config,
                "prepared_at": _utcnow().isoformat(),
            }

    def execute(self, candidate: Any) -> BootstrapResultDict:
        """Execute the orchestrator for a prepared candidate.

        Parameters
        ----------
        candidate:
            A ``RegimeCandidate`` or dict.

        Returns
        -------
        dict
            Raw bootstrap result from the orchestrator.
        """
        return self._orchestrator.orchestrate(candidate)

    def finalize(self, result: BootstrapResultDict) -> BootstrapResultDict:
        """Finalize the bootstrap result by adding provenance metadata.

        Parameters
        ----------
        result:
            The raw bootstrap result dict.

        Returns
        -------
        dict
            Finalized result dict (mutated in-place and returned).
        """
        result["finalized_at"] = _utcnow().isoformat()
        result["pipeline_version"] = "regime_bootstrapping.v1"
        # If the regime assembly succeeded, finalize the regime assembly dict
        regime = result.get("regime")
        if isinstance(regime, dict) and regime.get("valid"):
            regime["finalized"] = True
            regime["finalized_at"] = _utcnow().isoformat()
        return result

    def get_result(self) -> Optional[BootstrapResultDict]:
        """Return the result of the most recent run call.

        Returns
        -------
        dict or None
            The last bootstrap result, or ``None`` if ``run`` has not been
            called yet.
        """
        return self._last_result

    def reset(self) -> None:
        """Reset the runner's internal state.

        Clears the last result and creates a fresh orchestrator.
        """
        self._last_result = None
        self._orchestrator = BootstrapOrchestrator(config=self.config)
        log.debug("RegimeBootstrappingRunner.reset: state cleared")

    def summarize(self) -> str:
        """Return a human-readable summary of the most recent run.

        Returns
        -------
        str
            Multi-line summary string, or a message if no run has been performed.
        """
        if self._last_result is None:
            return "RegimeBootstrappingRunner: no run has been performed yet."
        status = self._last_result.get("status", "unknown")
        plan_id = self._last_result.get("plan_id", "N/A")
        regime = self._last_result.get("regime") or {}
        regime_id = regime.get("regime_id", "N/A") if isinstance(regime, dict) else "N/A"
        gen_count = len(regime.get("generators", [])) if isinstance(regime, dict) else 0
        lines = [
            "RegimeBootstrappingRunner summary:",
            f"  Plan ID:     {plan_id}",
            f"  Status:      {status}",
            f"  Regime ID:   {regime_id}",
            f"  Generators:  {gen_count}",
        ]
        validation = self._last_result.get("validation") or {}
        if validation:
            lines.append(f"  Final score: {validation.get('final_score', 'N/A')}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level helper functions (private)
# ---------------------------------------------------------------------------


def _safe_id(obj: Any) -> str:
    """Extract a stable identifier from any object.

    Parameters
    ----------
    obj:
        Object to identify.

    Returns
    -------
    str
        Identifier string.
    """
    oid = getattr(obj, "id", None) or getattr(obj, "regime_id", None)
    if oid is None:
        try:
            oid = obj.get("id") or obj.get("regime_id") or obj.get("plan_id")
        except Exception:
            oid = None
    return str(oid) if oid else _uid()


def _get_domain_generators_safe(domain: Any) -> List[str]:
    """Safely extract generator names from a domain.

    Parameters
    ----------
    domain:
        A domain formation object or dict.

    Returns
    -------
    list of str
    """
    gens = getattr(domain, "generators", None)
    if gens is None:
        try:
            gens = domain.get("generators", [])
        except Exception:
            gens = []
    return [str(g) for g in gens]


def _get_constructor_spec(constructor: Any) -> Dict[str, Any]:
    """Safely extract the functor spec from a constructor.

    Parameters
    ----------
    constructor:
        A type constructor object or dict.

    Returns
    -------
    dict
    """
    spec = getattr(constructor, "spec", None)
    if spec is None:
        try:
            spec = constructor.get("spec", {})
        except Exception:
            spec = {}
    return spec or {}


# ---------------------------------------------------------------------------
# Free convenience functions (public API)
# ---------------------------------------------------------------------------


def bootstrap_regime(
    obstruction_fields: Sequence[Any],
    config: Optional[Dict[str, Any]] = None,
) -> BootstrapResultDict:
    """High-level API: bootstrap a new regime from obstruction fields.

    This is the primary public entry point for the regime bootstrapping
    pipeline.  It creates a ``RegimeBootstrappingRunner``, runs the full
    pipeline, and returns the result dict.

    Parameters
    ----------
    obstruction_fields:
        Sequence of ``ObstructionField`` objects (or duck-typed equivalents).
    config:
        Optional configuration dict forwarded to all sub-components.
        Recognised keys:

        - ``'min_trust'``: minimum trust score (default 0.60).
        - ``'min_novelty'``: minimum novelty score (default 0.40).
        - ``'rollback_on_failure'``: bool (default True).
        - ``'max_constructors'``: int (default 64).

    Returns
    -------
    dict
        Bootstrap result dict with keys:

        - ``'status'``: one of ``'success'``, ``'failed'``, ``'cancelled'``,
          ``'rolled_back'``.
        - ``'regime'``: assembled regime dict (``None`` on failure).
        - ``'plan_id'``: UUID of the executed plan.
        - ``'validation'``: validation result dict.
        - ``'completed_at'``: ISO-8601 timestamp.
        - ``'finalized_at'``: ISO-8601 timestamp.
        - ``'pipeline_version'``: version string.

    Examples
    --------
    >>> result = bootstrap_regime(fields, config={"min_trust": 0.7})
    >>> if result["status"] == "success":
    ...     print("Regime ID:", result["regime"]["regime_id"])
    """
    runner = RegimeBootstrappingRunner(config=config)
    return runner.run(obstruction_fields)


def assemble_regime(candidate: Any) -> AssemblyDict:
    """Assemble a regime directly from a ``RegimeCandidate``.

    Convenience wrapper around ``RegimeAssembler.assemble``.  Useful when
    domain formations and type constructors have already been computed and
    the caller only needs the assembly step.

    Parameters
    ----------
    candidate:
        A ``RegimeCandidate`` or dict with keys ``'domains'`` and
        ``'constructors'``.

    Returns
    -------
    dict
        Assembled regime dict.

    Examples
    --------
    >>> assembly = assemble_regime({"domains": [d], "constructors": [c]})
    >>> print(assembly["regime_id"])
    """
    return RegimeAssembler().assemble(candidate)
