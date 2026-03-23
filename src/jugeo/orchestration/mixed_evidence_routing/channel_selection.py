"""Channel selection for the mixed-evidence routing layer.

This module implements the channel-selection machinery described in
*theory2.tex* Ch 45 ("Channel Selection in Mixed-Evidence Routing Systems").
That chapter establishes the theoretical foundations for routing logical
claims and natural-language tasks to the most appropriate evidence channel
(SMT solver, LLM assistant, runtime witness, or human expert) based on a
combination of claim kind, jurisdictional scope, current system load, and
trust algebra.

The key idea (Ch 45 §3) is that each evidence channel has a *jurisdiction*
— a set of claim kinds it can evaluate with bounded error.  The
:class:`ChannelSelector` consults a list of :class:`~jugeo.orchestration
.mixed_evidence_routing.models.JurisdictionMap` objects to score channels
and records every decision in a :class:`~jugeo.orchestration
.mixed_evidence_routing.models.RoutingHistory` for auditing and future
load-balancing.

Adapters (:class:`Z3ChannelAdapter`, :class:`CopilotChannelAdapter`,
:class:`RuntimeWitnessAdapter`, :class:`HumanEscalationAdapter`) wrap
concrete back-ends behind a uniform :class:`ChannelAdapterProtocol`
interface so the orchestration layer never depends on a specific solver or
model API.
"""

from __future__ import annotations

import abc
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Models import (required — must succeed)
# ---------------------------------------------------------------------------

from jugeo.orchestration.mixed_evidence_routing.models import (
    ChannelStats,
    CopilotQueryRecord,
    EscalationUrgency,
    EvidenceChannel,
    HumanEscalation,
    JurisdictionMap,
    RoutingDecision,
    RoutingHistory,
    EvidenceChannelSelector,  # noqa: F401  (re-exported for convenience)
)

# ---------------------------------------------------------------------------
# Optional upstream imports — guarded with try/except to allow the module to
# load even when sister packages have not been installed or are still stubs.
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustCeiling  # type: ignore[import]

    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustLevel."""

        VERIFIED = "VERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        WITNESSED = "WITNESSED"
        UNKNOWN = "UNKNOWN"

    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustAlgebra."""

    class TrustCeiling:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustCeiling."""


try:
    from jugeo.geometry.descent import DescentEngine, DescentResult  # type: ignore[import]

    _DESCENT_AVAILABLE = True
except Exception:
    _DESCENT_AVAILABLE = False

    class DescentEngine:  # type: ignore[no-redef]
        """Stub for jugeo.geometry.descent.DescentEngine."""

    class DescentResult:  # type: ignore[no-redef]
        """Stub for jugeo.geometry.descent.DescentResult."""


try:
    from jugeo.orchestration.controller import OrchestratorState, Orchestrator  # type: ignore[import]

    _CONTROLLER_AVAILABLE = True
except Exception:
    _CONTROLLER_AVAILABLE = False

    class OrchestratorState:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.controller.OrchestratorState."""

    class Orchestrator:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.controller.Orchestrator."""


try:
    from jugeo.orchestration.fleet import FleetMember, Fleet  # type: ignore[import]

    _FLEET_AVAILABLE = True
except Exception:
    _FLEET_AVAILABLE = False

    class FleetMember:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.fleet.FleetMember."""

    class Fleet:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.fleet.Fleet."""


try:
    from jugeo.orchestration.frontier import FrontierItem, Frontier  # type: ignore[import]

    _FRONTIER_AVAILABLE = True
except Exception:
    _FRONTIER_AVAILABLE = False

    class FrontierItem:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.frontier.FrontierItem."""

    class Frontier:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.frontier.Frontier."""


try:
    from jugeo.orchestration.negotiation import NegotiationSession, Negotiator  # type: ignore[import]

    _NEGOTIATION_AVAILABLE = True
except Exception:
    _NEGOTIATION_AVAILABLE = False

    class NegotiationSession:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.negotiation.NegotiationSession."""

    class Negotiator:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.negotiation.Negotiator."""


# ---------------------------------------------------------------------------
# ChannelAdapterProtocol (Abstract Base Class)
# ---------------------------------------------------------------------------


class ChannelAdapterProtocol(abc.ABC):
    """Abstract interface that every evidence-channel adapter must satisfy.

    Each concrete adapter wraps one back-end (a solver, an LLM API, a test
    runner, or a human-review queue) and exposes a uniform surface so the
    :class:`ChannelSelector` can dispatch tasks without knowing which
    back-end is involved.

    Implementors *must* provide all six abstract methods.  The routing layer
    calls :meth:`can_handle` and :meth:`health_check` before committing to a
    channel, then calls :meth:`execute` to actually run the task.
    """

    @abc.abstractmethod
    def channel_id(self) -> str:
        """Return the stable string identifier of this channel.

        Returns:
            A short, lower-case channel identifier (e.g. ``"z3"``).
        """

    @abc.abstractmethod
    def can_handle(self, task: dict) -> bool:
        """Return True if this adapter is capable of handling *task*.

        The routing layer uses this method to filter the candidate set
        before scoring channels against jurisdictional maps.

        Args:
            task: Arbitrary task dictionary.  Relevant keys include
                  ``"claim_kind"``, ``"formula"``, ``"code"``, etc.

        Returns:
            True when the adapter supports the task's claim kind.
        """

    @abc.abstractmethod
    def execute(self, task: dict) -> dict[str, Any]:
        """Execute *task* and return a result dictionary.

        The returned dictionary must contain at minimum:
        - ``"status"`` — ``"ok"`` / ``"sat"`` / ``"unsat"`` / ``"pending"``
          / ``"error"``
        - ``"result"`` — the substantive result, or ``None``
        - ``"evidence"`` — a nested dict of provenance information
        - ``"cost"`` — estimated monetary / compute cost (float)
        - ``"latency_ms"`` — observed latency in milliseconds (float)
        - ``"channel"`` — the channel_id string

        Args:
            task: Task dictionary to evaluate.

        Returns:
            A result dictionary conforming to the schema above.
        """

    @abc.abstractmethod
    def estimated_cost(self, task: dict) -> float:
        """Return the estimated cost of executing *task* on this channel.

        Cost is expressed in arbitrary but consistent units (e.g. USD or
        compute-credits).  Used by the routing layer for cost-optimal
        channel selection.

        Args:
            task: Task dictionary.

        Returns:
            Non-negative float cost estimate.
        """

    @abc.abstractmethod
    def estimated_latency(self, task: dict) -> float:
        """Return the estimated latency of executing *task* in milliseconds.

        Used by the routing layer for latency-optimal channel selection.

        Args:
            task: Task dictionary.

        Returns:
            Non-negative float latency estimate in milliseconds.
        """

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Return True if the back-end is currently healthy and accepting work.

        Returns:
            True when the adapter should be considered as a routing candidate.
        """


# ---------------------------------------------------------------------------
# Z3ChannelAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Z3ChannelAdapter(ChannelAdapterProtocol):
    """Adapter wrapping the Z3 SMT/SAT solver as an evidence channel.

    The adapter simulates (or, when a real Z3 installation is present, can
    call) the solver to decide formulae expressed as strings.  It tracks
    call and error counts to support health monitoring.

    Attributes:
        adapter_id: Unique identifier for this adapter instance.
        endpoint: Target endpoint — ``"local"`` for an in-process solver,
                  or a URI for a remote service.
        timeout_s: Maximum number of seconds to wait for the solver.
        max_formula_size: Maximum number of characters accepted in a formula.
        call_count: Running total of :meth:`execute` invocations.
        error_count: Running total of failed invocations.
    """

    adapter_id: str
    endpoint: str = "local"
    timeout_s: float = 30.0
    max_formula_size: int = 10_000
    call_count: int = 0
    error_count: int = 0

    # ------------------------------------------------------------------
    # ChannelAdapterProtocol implementation
    # ------------------------------------------------------------------

    def channel_id(self) -> str:
        """Return the fixed channel identifier ``"z3"``.

        Returns:
            The string ``"z3"``.
        """
        return "z3"

    def can_handle(self, task: dict) -> bool:
        """Return True when the task contains a formula or a Z3-compatible claim kind.

        Z3 can handle equality, arithmetic, bit-vector, Horn-clause, and
        quantifier-free linear rational arithmetic (QF_LRA) problems.

        Args:
            task: Task dictionary.  Checked for ``"formula"`` key and
                  ``"claim_kind"`` value.

        Returns:
            True if the task is solvable by Z3.
        """
        z3_claim_kinds = {
            "equality",
            "arithmetic",
            "bitvector",
            "horn_clause",
            "quantifier_free_lra",
        }
        return "formula" in task or task.get("claim_kind") in z3_claim_kinds

    def execute(self, task: dict) -> dict[str, Any]:
        """Run the Z3 solver on *task* and return a structured result.

        Increments :attr:`call_count` on every invocation.  The simulation
        produces a deterministic-ish result based on the formula string: if
        the formula contains ``"false"`` or ``"unsat"``, the status is
        ``"unsat"``; otherwise ``"sat"`` is returned.  Real deployments
        would call ``z3.solve()`` here.

        Args:
            task: Task dictionary.  The ``"formula"`` key is used as solver
                  input; ``"quantifiers"`` (list) adds complexity.

        Returns:
            Dictionary with keys ``status``, ``result``, ``evidence``,
            ``cost``, ``latency_ms``, ``channel``.
        """
        self.call_count += 1
        formula = str(task.get("formula", ""))

        # Simulate solver decision based on formula content
        if "false" in formula.lower() or "unsat" in formula.lower():
            status = "unsat"
            result: bool | None = False
        elif formula:
            status = "sat"
            result = True
        else:
            status = "unknown"
            result = None

        # Introduce realistic latency jitter: uniform noise in [0.8, 1.2]
        jitter = 0.8 + 0.4 * random.random()
        latency = self.estimated_latency(task) * jitter

        return {
            "status": status,
            "result": result,
            "evidence": {
                "formula": formula[:200],  # truncate for safety
                "solver": "z3",
                "timeout": self.timeout_s,
            },
            "cost": self.estimated_cost(task),
            "latency_ms": latency,
            "channel": self.channel_id(),
        }

    def estimated_cost(self, task: dict) -> float:
        """Estimate the cost of running Z3 on *task*.

        Cost scales linearly with formula length and grows with the number
        of quantifiers (which force expensive instantiation).

        Args:
            task: Task dictionary.

        Returns:
            Non-negative cost estimate (in compute-credits).
        """
        formula_len = len(str(task.get("formula", "")))
        quantifier_penalty = len(task.get("quantifiers", [])) * 10
        return max(0.01, (formula_len + quantifier_penalty) / 1000.0 * 0.05)

    def estimated_latency(self, task: dict) -> float:
        """Estimate the latency for Z3 on *task* in milliseconds.

        Latency grows with formula size but is capped at the configured
        timeout.

        Args:
            task: Task dictionary.

        Returns:
            Latency estimate in milliseconds, in the range [10, timeout_s*1000].
        """
        formula_size = len(str(task.get("formula", "")))
        return min(self.timeout_s * 1000, max(10.0, formula_size * 0.5))

    def health_check(self) -> bool:
        """Return True if the adapter's error rate is below 50 %.

        A high error rate likely indicates the solver process is unavailable
        or the endpoint is misconfigured.

        Returns:
            True when fewer than half of all calls have failed.
        """
        return self.error_rate() < 0.5

    # ------------------------------------------------------------------
    # Additional helpers
    # ------------------------------------------------------------------

    def error_rate(self) -> float:
        """Return the fraction of calls that resulted in errors.

        Returns:
            A float in [0, 1]; 0.0 when no calls have been made.
        """
        return self.error_count / max(self.call_count, 1)

    def reset_stats(self) -> None:
        """Reset call and error counters to zero.

        Useful after a solver restart or in test fixtures that need a clean
        slate.
        """
        self.call_count = 0
        self.error_count = 0


# ---------------------------------------------------------------------------
# CopilotChannelAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotChannelAdapter(ChannelAdapterProtocol):
    """Adapter routing tasks to a large-language-model (Copilot/GPT) back-end.

    LLM outputs are inherently heuristic; their trust ceiling is therefore
    lower than mechanically verified results (see *theory2.tex* Ch 45 §4.2).
    This adapter records every query as a :class:`CopilotQueryRecord` so the
    trust-algebra layer can apply appropriate epistemic discounts.

    Attributes:
        adapter_id: Unique identifier for this adapter instance.
        model_id: Model identifier string (e.g. ``"gpt-4"``).
        max_tokens: Token budget for model responses.
        trust_ceiling: String representation of the maximum trust level
                       that can be assigned to LLM outputs.
        call_count: Running total of :meth:`execute` invocations.
        total_tokens: Cumulative token consumption.
        query_history: Ordered list of :class:`CopilotQueryRecord` objects.
    """

    adapter_id: str
    model_id: str = "gpt-4"
    max_tokens: int = 4096
    trust_ceiling: str = "COPILOT_SUGGESTED"
    call_count: int = 0
    total_tokens: int = 0
    query_history: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # ChannelAdapterProtocol implementation
    # ------------------------------------------------------------------

    def channel_id(self) -> str:
        """Return the fixed channel identifier ``"copilot_llm"``.

        Returns:
            The string ``"copilot_llm"``.
        """
        return "copilot_llm"

    def can_handle(self, task: dict) -> bool:
        """Return True for natural-language or heuristic tasks.

        LLM channels are permissive: unless ``allow_copilot`` is explicitly
        set to False, they accept any task.  Specific claim kinds that are
        LLM-native (natural language, code suggestion, etc.) are also
        recognised.

        Args:
            task: Task dictionary.  Checked for ``"claim_kind"`` and the
                  ``"allow_copilot"`` override flag.

        Returns:
            True if the task is suitable for LLM processing.
        """
        llm_claim_kinds = {
            "natural_language",
            "code_suggestion",
            "explanation",
            "heuristic",
            "sketch",
        }
        if task.get("claim_kind") in llm_claim_kinds:
            return True
        # Default: allow unless explicitly disabled
        return bool(task.get("allow_copilot", True))

    def execute(self, task: dict) -> dict[str, Any]:
        """Query the LLM back-end and return a structured result.

        Constructs a prompt from the task, estimates token usage, records a
        :class:`CopilotQueryRecord`, and returns a result dictionary.  In
        a production deployment this method would call the OpenAI / GitHub
        Copilot API; here it simulates the response.

        Args:
            task: Task dictionary.  The ``"prompt"`` key is used as the
                  query text; falls back to ``"formula"`` or a string
                  representation of the task.

        Returns:
            Dictionary with keys ``status``, ``result``, ``evidence``,
            ``cost``, ``latency_ms``, ``channel``.
        """
        self.call_count += 1

        # Build the prompt text from available task keys
        prompt_text: str = task.get("prompt", task.get("formula", str(task)))

        # Rough token estimate: words + overhead
        token_estimate = len(prompt_text.split()) + 50
        self.total_tokens += token_estimate

        # Record the query for auditing and trust-algebra consumption
        record = CopilotQueryRecord.new(
            query_text=prompt_text[:500],  # cap stored text length
            response_text=f"Copilot analysis of: {prompt_text[:100]}",
            trust_ceiling=self.trust_ceiling,
            latency_ms=self.estimated_latency(task),
            token_count=token_estimate,
            model_id=self.model_id,
        )
        self.query_history.append(record)

        return {
            "status": "ok",
            "result": {
                "response": f"Copilot analysis of: {prompt_text[:100]}",
                "trust_ceiling": self.trust_ceiling,
            },
            "evidence": {
                "query_id": record.query_id,
                "model": self.model_id,
                "tokens": token_estimate,
            },
            "cost": self.estimated_cost(task),
            "latency_ms": self.estimated_latency(task),
            "channel": self.channel_id(),
        }

    def estimated_cost(self, task: dict) -> float:
        """Estimate the token-based cost of querying the LLM.

        Uses a simple per-token pricing model.

        Args:
            task: Task dictionary.

        Returns:
            Non-negative cost estimate in USD (approximate).
        """
        prompt_tokens = len(str(task.get("prompt", task)).split())
        return max(0.001, prompt_tokens * 0.00002)

    def estimated_latency(self, task: dict) -> float:
        """Estimate the network + generation latency for the LLM in milliseconds.

        Latency scales with prompt length; minimum is 200 ms.

        Args:
            task: Task dictionary.

        Returns:
            Latency estimate in milliseconds.
        """
        prompt_len = len(str(task.get("prompt", task)))
        return max(200.0, prompt_len * 0.5)

    def health_check(self) -> bool:
        """Return True — the LLM adapter is assumed always available.

        In production this would ping the API endpoint.

        Returns:
            Always True for the simulated adapter.
        """
        return True

    # ------------------------------------------------------------------
    # Additional helpers
    # ------------------------------------------------------------------

    def get_query_history(self, n: int = 10) -> list[CopilotQueryRecord]:
        """Return the last *n* query records.

        Args:
            n: Number of records to return.  Defaults to 10.

        Returns:
            A list of up to *n* :class:`CopilotQueryRecord` instances,
            most recent last.
        """
        return self.query_history[-n:]

    def average_latency_ms(self) -> float:
        """Return the mean latency across all recorded queries in milliseconds.

        Returns:
            Mean latency, or 0.0 if no queries have been made.
        """
        if not self.query_history:
            return 0.0
        return sum(r.latency_ms for r in self.query_history) / len(self.query_history)


# ---------------------------------------------------------------------------
# RuntimeWitnessAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RuntimeWitnessAdapter(ChannelAdapterProtocol):
    """Adapter that gathers evidence by actually running code or tests.

    Dynamic witnesses (execution traces, property-based tests, fuzzing
    campaigns, benchmarks) provide *empirical* evidence distinct from
    solver-theoretic proofs.  Results are cached by a hash of the task to
    avoid redundant test runs.

    Attributes:
        adapter_id: Unique identifier for this adapter instance.
        runner_id: Identifier of the test-runner back-end.
        timeout_s: Maximum wall-clock time for a witness run.
        max_retries: Maximum number of retries on transient failures.
        call_count: Running total of :meth:`execute` invocations.
        witness_cache: Dict mapping task-hash → cached result dict.
    """

    adapter_id: str
    runner_id: str = "default"
    timeout_s: float = 60.0
    max_retries: int = 3
    call_count: int = 0
    witness_cache: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # ChannelAdapterProtocol implementation
    # ------------------------------------------------------------------

    def channel_id(self) -> str:
        """Return the fixed channel identifier ``"runtime_witness"``.

        Returns:
            The string ``"runtime_witness"``.
        """
        return "runtime_witness"

    def can_handle(self, task: dict) -> bool:
        """Return True for execution-oriented tasks.

        Recognises claim kinds that require dynamic evaluation: execution
        traces, test cases, property tests, fuzzing runs, and benchmarks.
        Also matches any task that contains a ``"code"`` key.

        Args:
            task: Task dictionary.

        Returns:
            True if the task needs runtime execution.
        """
        witness_claim_kinds = {
            "execution_trace",
            "test_case",
            "property_test",
            "fuzzing",
            "benchmark",
        }
        return task.get("claim_kind") in witness_claim_kinds or "code" in task

    def execute(self, task: dict) -> dict[str, Any]:
        """Run the witness back-end for *task* and return a structured result.

        Results are cached by task hash.  Cache hits are returned immediately
        without incrementing :attr:`call_count`.  The simulation assumes all
        tests pass; a real implementation would execute the test suite and
        capture pass/fail counts.

        Args:
            task: Task dictionary.  The ``"test_count"`` key controls the
                  number of tests simulated.

        Returns:
            Dictionary with keys ``status``, ``result``, ``evidence``,
            ``cost``, ``latency_ms``, ``channel``.
        """
        cache_key = str(hash(str(task)))

        # Return cached witness if available — avoids redundant test runs
        cached = self.get_cached_witness(cache_key)
        if cached is not None:
            return cached

        self.call_count += 1

        tests_run: int = task.get("test_count", 1)
        passed: int = tests_run  # simulation: all tests pass

        witness_evidence: dict[str, Any] = {
            "tests_run": tests_run,
            "passed": passed,
            "runner": self.runner_id,
        }

        result: dict[str, Any] = {
            "status": "witnessed",
            "result": True,
            "evidence": witness_evidence,
            "cost": self.estimated_cost(task),
            "latency_ms": self.estimated_latency(task),
            "channel": self.channel_id(),
        }

        # Cache for future calls with the same task
        self.cache_witness(cache_key, result)
        return result

    def estimated_cost(self, task: dict) -> float:
        """Estimate the cost of running *test_count* tests.

        Args:
            task: Task dictionary.  ``"test_count"`` defaults to 1.

        Returns:
            Non-negative cost estimate.
        """
        return task.get("test_count", 1) * 0.001

    def estimated_latency(self, task: dict) -> float:
        """Estimate the latency for running *test_count* tests in milliseconds.

        Each test incurs 100 ms; total is capped at *timeout_s*.

        Args:
            task: Task dictionary.

        Returns:
            Latency estimate in milliseconds.
        """
        return min(self.timeout_s * 1000, task.get("test_count", 1) * 100.0)

    def health_check(self) -> bool:
        """Return True — the witness adapter is assumed always available.

        Returns:
            Always True for the simulated adapter.
        """
        return True

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def cache_witness(self, key: str, result: dict) -> None:
        """Store *result* in the witness cache under *key*.

        Args:
            key: Cache key (typically a hash of the task dict).
            result: Result dictionary to store.
        """
        self.witness_cache[key] = result

    def get_cached_witness(self, key: str) -> dict | None:
        """Retrieve a cached witness result, or None if not present.

        Args:
            key: Cache key to look up.

        Returns:
            Cached result dict, or None if no entry exists for *key*.
        """
        return self.witness_cache.get(key)


# ---------------------------------------------------------------------------
# HumanEscalationAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HumanEscalationAdapter(ChannelAdapterProtocol):
    """Adapter that routes tasks to a human-review queue.

    When a claim cannot be decided automatically (ethical judgment, ambiguous
    specification, policy decision, or genuinely novel claim), it is placed
    in the escalation queue as a :class:`HumanEscalation` record.  A human
    reviewer resolves the escalation asynchronously; the adapter returns a
    ``"pending"`` status immediately.

    Attributes:
        adapter_id: Unique identifier for this adapter instance.
        escalation_queue: Ordered list of outstanding escalations.
        default_urgency: Default urgency string when none is supplied by the task.
        sla_hours: Service-level agreement: expected resolution time in hours.
    """

    adapter_id: str
    escalation_queue: list[HumanEscalation] = field(default_factory=list)
    default_urgency: str = "medium"
    sla_hours: float = 24.0

    # ------------------------------------------------------------------
    # ChannelAdapterProtocol implementation
    # ------------------------------------------------------------------

    def channel_id(self) -> str:
        """Return the fixed channel identifier ``"human"``.

        Returns:
            The string ``"human"``.
        """
        return "human"

    def can_handle(self, task: dict) -> bool:
        """Return True for tasks that require human judgment.

        Recognises ethical judgments, ambiguous specifications, policy
        decisions, novel claims, and any task with ``requires_human=True``.

        Args:
            task: Task dictionary.

        Returns:
            True if the task warrants human review.
        """
        human_claim_kinds = {
            "ethical_judgment",
            "ambiguous_spec",
            "policy_decision",
            "novel_claim",
            "escalation",
        }
        return (
            task.get("claim_kind") in human_claim_kinds
            or bool(task.get("requires_human", False))
        )

    def execute(self, task: dict) -> dict[str, Any]:
        """Place *task* into the human-review queue and return a pending result.

        Creates a :class:`HumanEscalation` record, appends it to
        :attr:`escalation_queue`, and returns immediately with
        ``status="pending"``.  The calling layer is responsible for polling
        :meth:`resolved_escalations` or implementing a push-notification
        mechanism.

        Args:
            task: Task dictionary.  Keys ``"task_id"``, ``"reason"``, and
                  ``"urgency"`` are used if present.

        Returns:
            Dictionary with keys ``status``, ``result``, ``evidence``,
            ``cost``, ``latency_ms``, ``channel``.
        """
        urgency = EscalationUrgency(task.get("urgency", self.default_urgency))
        escalation = HumanEscalation.new(
            task_id=task.get("task_id", str(uuid.uuid4())),
            reason=task.get("reason", "Human review required"),
            urgency=urgency,
        )
        self.escalation_queue.append(escalation)

        return {
            "status": "pending",
            "result": None,
            "evidence": {
                "escalation_id": escalation.escalation_id,
                "urgency": urgency.value,
                "sla_hours": self.sla_hours,
            },
            "cost": self.estimated_cost(task),
            "latency_ms": self.estimated_latency(task),
            "channel": self.channel_id(),
        }

    def estimated_cost(self, task: dict) -> float:
        """Estimate the cost of human-review time.

        Assumes a flat rate of 50 credits/hour of SLA.

        Args:
            task: Task dictionary (unused).

        Returns:
            Cost estimate proportional to :attr:`sla_hours`.
        """
        return self.sla_hours * 50.0

    def estimated_latency(self, task: dict) -> float:
        """Estimate the worst-case latency for human review in milliseconds.

        Worst-case is the full SLA window.

        Args:
            task: Task dictionary (unused).

        Returns:
            Latency estimate equal to ``sla_hours * 3 600 000`` ms.
        """
        return self.sla_hours * 3600 * 1000

    def health_check(self) -> bool:
        """Return True — the human escalation queue is always accepting work.

        Returns:
            Always True.
        """
        return True

    # ------------------------------------------------------------------
    # Queue management helpers
    # ------------------------------------------------------------------

    def resolve_escalation(
        self,
        escalation_id: str,
        resolution: str,
        resolver: str | None = None,
    ) -> bool:
        """Mark an escalation as resolved.

        Searches :attr:`escalation_queue` for the escalation with the given
        ID and calls its :meth:`~HumanEscalation.resolve` method.

        Args:
            escalation_id: ID of the escalation to resolve.
            resolution: Human-readable resolution text.
            resolver: Optional name or ID of the resolver.

        Returns:
            True if the escalation was found and resolved, False otherwise.
        """
        for escalation in self.escalation_queue:
            if escalation.escalation_id == escalation_id:
                escalation.resolve(resolution=resolution, resolver=resolver)
                return True
        return False

    def pending_escalations(self) -> list[HumanEscalation]:
        """Return all unresolved escalations.

        Returns:
            List of :class:`HumanEscalation` instances not yet resolved.
        """
        return [e for e in self.escalation_queue if not e.is_resolved()]

    def resolved_escalations(self) -> list[HumanEscalation]:
        """Return all resolved escalations.

        Returns:
            List of :class:`HumanEscalation` instances that have been resolved.
        """
        return [e for e in self.escalation_queue if e.is_resolved()]

    def queue_depth(self) -> int:
        """Return the number of unresolved escalations.

        Returns:
            Integer count of pending escalations.
        """
        return len(self.pending_escalations())


# ---------------------------------------------------------------------------
# CompositeChannelOrchestrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CompositeChannelOrchestrator:
    """Orchestrator that manages multiple channel adapters as a unit.

    Supports three combination strategies:
    - ``"first_success"`` — try adapters in order, return the first
      non-error result.
    - ``"parallel"`` — run all capable adapters (simulated) and merge.
    - ``"all"`` — run all capable adapters and return the full result list.

    The :meth:`merge_results` method applies a trust-ordered ranking
    (z3 > runtime_witness > copilot_llm > human) to select the most
    authoritative result.

    Attributes:
        orchestrator_id: Unique identifier for this orchestrator instance.
        adapters: Ordered list of registered :class:`ChannelAdapterProtocol`
                  instances.
        combination_strategy: One of ``"first_success"``, ``"parallel"``,
                              ``"all"``.
        timeout_s: Global timeout in seconds for combined execution.
    """

    orchestrator_id: str
    adapters: list = field(default_factory=list)
    combination_strategy: str = "first_success"
    timeout_s: float = 120.0

    # Trust priority for merge: lower index = higher trust
    _TRUST_ORDER: tuple[str, ...] = field(
        default_factory=lambda: ("z3", "runtime_witness", "copilot_llm", "human"),
        init=False,
        repr=False,
    )

    def add_adapter(self, adapter: Any) -> None:
        """Register a new channel adapter.

        Args:
            adapter: Any object implementing :class:`ChannelAdapterProtocol`.
        """
        self.adapters.append(adapter)

    def remove_adapter(self, adapter_id: str) -> bool:
        """Remove an adapter by its ID.

        Args:
            adapter_id: The :meth:`ChannelAdapterProtocol.channel_id` value
                        or the ``adapter_id`` attribute of the target adapter.

        Returns:
            True if an adapter was found and removed, False otherwise.
        """
        before = len(self.adapters)
        self.adapters = [
            a for a in self.adapters if getattr(a, "adapter_id", None) != adapter_id
        ]
        return len(self.adapters) < before

    def capable_adapters(self, task: dict) -> list:
        """Return adapters that can handle *task* and are healthy.

        Args:
            task: Task dictionary to match against each adapter.

        Returns:
            Filtered list of adapter instances.
        """
        return [
            a
            for a in self.adapters
            if a.can_handle(task) and a.health_check()
        ]

    def execute_all(self, task: dict) -> list[dict[str, Any]]:
        """Run all capable adapters sequentially and collect results.

        Args:
            task: Task dictionary.

        Returns:
            List of result dicts, one per capable adapter.
        """
        results: list[dict[str, Any]] = []
        for adapter in self.capable_adapters(task):
            try:
                results.append(adapter.execute(task))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "status": "error",
                        "result": None,
                        "evidence": {"error": str(exc)},
                        "cost": 0.0,
                        "latency_ms": 0.0,
                        "channel": getattr(adapter, "channel_id", lambda: "unknown")(),
                    }
                )
        return results

    def execute_first_success(self, task: dict) -> dict[str, Any]:
        """Return the first non-error result from capable adapters.

        Adapters are tried in registration order.  If all fail, an error
        dict is returned.

        Args:
            task: Task dictionary.

        Returns:
            First successful result dict, or an error dict if all adapters fail.
        """
        for adapter in self.capable_adapters(task):
            try:
                result = adapter.execute(task)
                if result.get("status") != "error":
                    return result
            except Exception:  # noqa: BLE001
                continue  # try next adapter

        return {
            "status": "error",
            "result": None,
            "evidence": {"error": "All adapters failed or are unavailable"},
            "cost": 0.0,
            "latency_ms": 0.0,
            "channel": "none",
        }

    def execute_parallel(self, task: dict) -> list[dict[str, Any]]:
        """Simulate parallel execution across all capable adapters.

        In this implementation parallel execution is simulated by calling
        :meth:`execute_all`; a production implementation would use
        ``asyncio`` or a thread pool.

        Args:
            task: Task dictionary.

        Returns:
            List of result dicts from all capable adapters.
        """
        return self.execute_all(task)

    def merge_results(self, results: list[dict]) -> dict[str, Any]:
        """Combine multiple channel results into a single authoritative result.

        Trust ordering: z3 > runtime_witness > copilot_llm > human.
        The result from the highest-trust channel is returned; evidence dicts
        from all channels are merged under a ``"multi_channel_evidence"`` key.

        Args:
            results: List of result dicts (one per channel).

        Returns:
            A merged result dict.  Returns an error dict if *results* is empty.
        """
        if not results:
            return {
                "status": "error",
                "result": None,
                "evidence": {"error": "No results to merge"},
                "cost": 0.0,
                "latency_ms": 0.0,
                "channel": "none",
            }

        trust_order = ("z3", "runtime_witness", "copilot_llm", "human")

        def trust_rank(r: dict) -> int:
            channel = r.get("channel", "")
            try:
                return trust_order.index(channel)
            except ValueError:
                return len(trust_order)  # unknown channels rank last

        # Sort by trust rank; pick the highest-trust (lowest index) result
        sorted_results = sorted(results, key=trust_rank)
        best = sorted_results[0]

        # Merge evidence from all channels for auditability
        merged_evidence: dict[str, Any] = {"multi_channel_evidence": {}}
        for r in results:
            ch = r.get("channel", "unknown")
            merged_evidence["multi_channel_evidence"][ch] = r.get("evidence", {})

        return {
            "status": best.get("status"),
            "result": best.get("result"),
            "evidence": merged_evidence,
            "cost": sum(r.get("cost", 0.0) for r in results),
            "latency_ms": max(r.get("latency_ms", 0.0) for r in results),
            "channel": best.get("channel"),
        }

    def health_summary(self) -> dict[str, bool]:
        """Return a health map for all registered adapters.

        Returns:
            Dict mapping channel_id → health_check() result.
        """
        return {adapter.channel_id(): adapter.health_check() for adapter in self.adapters}


# ---------------------------------------------------------------------------
# ChannelLoadBalancer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChannelLoadBalancer:
    """Load balancer that tracks concurrent request counts per channel.

    Prevents any single evidence channel from being overwhelmed by enforcing
    per-channel slot limits.  Provides least-loaded-channel selection to
    spread work evenly across a pool of equivalent channels.

    Attributes:
        balancer_id: Unique identifier for this load balancer instance.
        channel_stats: Dict mapping channel value → :class:`ChannelStats`.
        load_limits: Dict mapping channel value → maximum concurrent slots.
        current_load: Dict mapping channel value → current active slot count.
    """

    balancer_id: str
    channel_stats: dict[str, ChannelStats] = field(default_factory=dict)
    load_limits: dict[str, int] = field(default_factory=dict)
    current_load: dict[str, int] = field(default_factory=dict)

    def register_channel(self, channel: EvidenceChannel, max_load: int = 100) -> None:
        """Register *channel* with the balancer and set its slot limit.

        Safe to call multiple times; repeated calls update the limit but
        do not reset the current-load counter.

        Args:
            channel: The :class:`EvidenceChannel` to register.
            max_load: Maximum number of concurrent tasks allowed for this
                      channel.  Defaults to 100.
        """
        self.load_limits[channel.value] = max_load
        self.current_load.setdefault(channel.value, 0)
        self.channel_stats.setdefault(channel.value, ChannelStats(channel=channel))

    def acquire_slot(self, channel: EvidenceChannel) -> bool:
        """Attempt to acquire a processing slot for *channel*.

        Args:
            channel: The :class:`EvidenceChannel` requesting a slot.

        Returns:
            True if the slot was acquired (load increased by 1),
            False if the channel is at capacity.
        """
        limit = self.load_limits.get(channel.value, 100)
        load = self.current_load.get(channel.value, 0)
        if load >= limit:
            return False
        self.current_load[channel.value] = load + 1
        return True

    def release_slot(self, channel: EvidenceChannel) -> None:
        """Release a previously acquired slot for *channel*.

        The counter is floored at zero to guard against unbalanced calls.

        Args:
            channel: The :class:`EvidenceChannel` releasing a slot.
        """
        self.current_load[channel.value] = max(
            0, self.current_load.get(channel.value, 1) - 1
        )

    def least_loaded_channel(
        self, candidates: list[EvidenceChannel]
    ) -> EvidenceChannel | None:
        """Return the candidate channel with the lowest load factor.

        Args:
            candidates: List of :class:`EvidenceChannel` instances to compare.

        Returns:
            The least-loaded :class:`EvidenceChannel`, or None if the list
            is empty.
        """
        if not candidates:
            return None
        return min(candidates, key=self.load_factor)

    def load_factor(self, channel: EvidenceChannel) -> float:
        """Return the fractional load for *channel* (0.0 = idle, 1.0 = full).

        Args:
            channel: The :class:`EvidenceChannel` to query.

        Returns:
            Load fraction in [0, 1].
        """
        limit = max(self.load_limits.get(channel.value, 1), 1)
        return self.current_load.get(channel.value, 0) / limit

    def rebalance_suggestion(self) -> dict[str, Any]:
        """Suggest which channels are over- or under-loaded.

        Channels with load factor > 0.8 are considered overloaded;
        those with load factor < 0.2 are underloaded.

        Returns:
            Dict with ``"overloaded"``, ``"underloaded"``, and
            ``"suggestion"`` keys.
        """
        overloaded: list[str] = []
        underloaded: list[str] = []

        for ch_val in self.load_limits:
            try:
                channel = EvidenceChannel(ch_val)
            except ValueError:
                continue
            factor = self.load_factor(channel)
            if factor > 0.8:
                overloaded.append(ch_val)
            elif factor < 0.2:
                underloaded.append(ch_val)

        if overloaded and underloaded:
            suggestion = (
                f"Consider redirecting load from {overloaded} to {underloaded}."
            )
        elif overloaded:
            suggestion = f"Channels {overloaded} are near capacity; scale up."
        elif underloaded:
            suggestion = f"Channels {underloaded} have spare capacity."
        else:
            suggestion = "Load is balanced across all channels."

        return {
            "overloaded": overloaded,
            "underloaded": underloaded,
            "suggestion": suggestion,
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a point-in-time snapshot of load state.

        Returns:
            Dict with ``"current_load"``, ``"load_limits"``, and
            ``"load_factors"`` sub-dicts.
        """
        load_factors = {}
        for ch_val in self.load_limits:
            try:
                load_factors[ch_val] = self.load_factor(EvidenceChannel(ch_val))
            except ValueError:
                load_factors[ch_val] = 0.0

        return {
            "current_load": dict(self.current_load),
            "load_limits": dict(self.load_limits),
            "load_factors": load_factors,
        }


# ---------------------------------------------------------------------------
# ChannelSelector
# ---------------------------------------------------------------------------

# Default jurisdiction definitions — maps claim kinds to primary channels
_DEFAULT_JURISDICTION_DATA: list[dict[str, Any]] = [
    {
        "channel": EvidenceChannel.Z3,
        "claim_kinds": [
            "equality",
            "arithmetic",
            "bitvector",
            "horn_clause",
            "quantifier_free_lra",
        ],
        "cost_weight": 0.3,
        "latency_weight": 0.3,
        "max_complexity": 0.9,
        "exclusive": False,
    },
    {
        "channel": EvidenceChannel.COPILOT_LLM,
        "claim_kinds": [
            "natural_language",
            "code_suggestion",
            "explanation",
            "heuristic",
            "sketch",
        ],
        "cost_weight": 0.4,
        "latency_weight": 0.2,
        "max_complexity": 0.6,
        "exclusive": False,
    },
    {
        "channel": EvidenceChannel.RUNTIME_WITNESS,
        "claim_kinds": [
            "execution_trace",
            "test_case",
            "property_test",
            "fuzzing",
            "benchmark",
        ],
        "cost_weight": 0.2,
        "latency_weight": 0.4,
        "max_complexity": 0.8,
        "exclusive": False,
    },
    {
        "channel": EvidenceChannel.HUMAN,
        "claim_kinds": [
            "ethical_judgment",
            "ambiguous_spec",
            "policy_decision",
            "novel_claim",
            "escalation",
        ],
        "cost_weight": 0.9,
        "latency_weight": 0.9,
        "max_complexity": 1.0,
        "exclusive": True,
    },
]


@dataclass(slots=True)
class ChannelSelector:
    """High-level channel selector integrating adapters, jurisdiction maps,
    and load balancing.

    The selector is the primary entry point for the mixed-evidence routing
    layer.  It:

    1. Consults :class:`JurisdictionMap` objects to identify capable channels.
    2. Checks :class:`ChannelLoadBalancer` slot availability.
    3. Picks the best channel for the given task.
    4. Delegates execution to the chosen :class:`ChannelAdapterProtocol`.
    5. Records every decision in a :class:`RoutingHistory`.

    Attributes:
        selector_id: Unique identifier for this selector instance.
        adapters: Dict mapping :attr:`EvidenceChannel.value` → adapter.
        jurisdiction_maps: List of :class:`JurisdictionMap` instances.
        load_balancer: :class:`ChannelLoadBalancer` for slot management.
        routing_history: :class:`RoutingHistory` log of all decisions made.
    """

    selector_id: str
    adapters: dict[str, Any] = field(default_factory=dict)
    jurisdiction_maps: list[JurisdictionMap] = field(default_factory=list)
    load_balancer: ChannelLoadBalancer = field(
        default_factory=lambda: ChannelLoadBalancer(balancer_id=str(uuid.uuid4()))
    )
    routing_history: RoutingHistory = field(default_factory=RoutingHistory)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> ChannelSelector:
        """Create a fully configured selector with all built-in adapters.

        Registers Z3, Copilot LLM, RuntimeWitness, and Human adapters, sets
        up default jurisdiction maps, and configures the load balancer.

        Returns:
            A ready-to-use :class:`ChannelSelector` instance.
        """
        selector_id = str(uuid.uuid4())
        lb = ChannelLoadBalancer(balancer_id=str(uuid.uuid4()))

        # Build adapters keyed by channel value
        adapters: dict[str, Any] = {
            EvidenceChannel.Z3.value: Z3ChannelAdapter(adapter_id=str(uuid.uuid4())),
            EvidenceChannel.COPILOT_LLM.value: CopilotChannelAdapter(
                adapter_id=str(uuid.uuid4())
            ),
            EvidenceChannel.RUNTIME_WITNESS.value: RuntimeWitnessAdapter(
                adapter_id=str(uuid.uuid4())
            ),
            EvidenceChannel.HUMAN.value: HumanEscalationAdapter(
                adapter_id=str(uuid.uuid4())
            ),
        }

        # Register each channel with the load balancer
        lb.register_channel(EvidenceChannel.Z3, max_load=50)
        lb.register_channel(EvidenceChannel.COPILOT_LLM, max_load=100)
        lb.register_channel(EvidenceChannel.RUNTIME_WITNESS, max_load=30)
        lb.register_channel(EvidenceChannel.HUMAN, max_load=10)

        # Build default jurisdiction maps from the module-level table
        jmaps: list[JurisdictionMap] = []
        for jd in _DEFAULT_JURISDICTION_DATA:
            jmaps.append(
                JurisdictionMap.new(
                    channel=jd["channel"],
                    claim_kinds=jd["claim_kinds"],
                    cost_weight=jd["cost_weight"],
                    latency_weight=jd["latency_weight"],
                    max_complexity=jd["max_complexity"],
                    exclusive=jd["exclusive"],
                )
            )

        return cls(
            selector_id=selector_id,
            adapters=adapters,
            jurisdiction_maps=jmaps,
            load_balancer=lb,
        )

    # ------------------------------------------------------------------
    # Core routing logic
    # ------------------------------------------------------------------

    def select_channel(self, task: dict) -> RoutingDecision:
        """Determine the best evidence channel for *task*.

        Algorithm (Ch 45 §6.1):
        1. Score each channel using the jurisdiction maps.
        2. Filter out channels over capacity.
        3. Pick the highest-scoring channel that has a healthy adapter.
        4. Create and record a :class:`RoutingDecision`.

        Args:
            task: Task dictionary.  ``"claim_kind"``, ``"formula"``,
                  ``"prompt"``, ``"requires_human"``, ``"urgency"``, and
                  ``"task_id"`` are all recognised.

        Returns:
            A :class:`RoutingDecision` for the selected channel.
        """
        claim_kind: str = task.get("claim_kind", "unknown")

        # Score channels against jurisdiction maps
        scores: dict[EvidenceChannel, float] = {}
        for jmap in self.jurisdiction_maps:
            if jmap.can_handle(task):
                existing = scores.get(jmap.channel, 0.0)
                scores[jmap.channel] = max(existing, jmap.complexity_score(task))

        # Fall back: if no jurisdiction map matched, allow LLM as default
        if not scores:
            scores[EvidenceChannel.COPILOT_LLM] = 0.5

        # Filter channels without a healthy adapter or with no capacity
        available = self.available_channels()
        candidates: list[tuple[EvidenceChannel, float]] = [
            (ch, score)
            for ch, score in scores.items()
            if ch in available and self.load_balancer.acquire_slot(ch)
        ]

        # Release speculatively acquired slots for non-winning channels
        if candidates:
            # Sort by score descending; keep the best
            candidates.sort(key=lambda t: t[1], reverse=True)
            chosen_channel, confidence = candidates[0]
            # Release slots acquired for all other candidates
            for ch, _ in candidates[1:]:
                self.load_balancer.release_slot(ch)
        else:
            # Last-resort: escalate to human
            chosen_channel = EvidenceChannel.HUMAN
            confidence = 0.1

        adapter = self.get_adapter(chosen_channel)
        estimated_cost = adapter.estimated_cost(task) if adapter else 0.0
        estimated_latency = adapter.estimated_latency(task) if adapter else 0.0

        decision = RoutingDecision.new(
            channel=chosen_channel,
            task_id=task.get("task_id", str(uuid.uuid4())),
            claim_kind=claim_kind,
            confidence=min(1.0, max(0.0, confidence)),
            estimated_cost=estimated_cost,
            estimated_latency=estimated_latency,
        )

        # Record in history for auditing
        self.routing_history.record(decision)
        return decision

    def execute_task(
        self, task: dict, decision: RoutingDecision
    ) -> dict[str, Any]:
        """Execute *task* on the channel indicated by *decision*.

        Retrieves the adapter for the chosen channel, runs the task, records
        the outcome in the load balancer, and releases the slot.

        Args:
            task: Task dictionary to evaluate.
            decision: A :class:`RoutingDecision` previously returned by
                      :meth:`select_channel`.

        Returns:
            Result dictionary from the adapter's :meth:`execute` method, or
            an error dict if no adapter is registered for the channel.
        """
        adapter = self.get_adapter(decision.channel)
        if adapter is None:
            return {
                "status": "error",
                "result": None,
                "evidence": {
                    "error": f"No adapter for channel {decision.channel.value}"
                },
                "cost": 0.0,
                "latency_ms": 0.0,
                "channel": decision.channel.value,
            }

        try:
            result = adapter.execute(task)
            success = result.get("status") not in ("error", "pending")
        except Exception as exc:  # noqa: BLE001
            result = {
                "status": "error",
                "result": None,
                "evidence": {"error": str(exc)},
                "cost": decision.estimated_cost,
                "latency_ms": 0.0,
                "channel": decision.channel.value,
            }
            success = False

        self.record_outcome(decision, success)
        # Release the slot acquired during select_channel
        self.load_balancer.release_slot(decision.channel)
        return result

    def record_outcome(self, decision: RoutingDecision, success: bool) -> None:
        """Update channel statistics with the result of a routing decision.

        Args:
            decision: The :class:`RoutingDecision` that was executed.
            success: True if the channel produced a usable result.
        """
        stats = self.load_balancer.channel_stats.get(decision.channel.value)
        if stats is not None:
            stats.update(decision, success)

    # ------------------------------------------------------------------
    # Adapter registry helpers
    # ------------------------------------------------------------------

    def get_adapter(self, channel: EvidenceChannel) -> Any | None:
        """Return the adapter registered for *channel*, or None.

        Args:
            channel: The :class:`EvidenceChannel` to look up.

        Returns:
            The registered adapter, or None if not present.
        """
        return self.adapters.get(channel.value)

    def available_channels(self) -> list[EvidenceChannel]:
        """Return channels that have both a registered adapter and pass health check.

        Returns:
            Ordered list of :class:`EvidenceChannel` instances that are
            ready to accept work.
        """
        available: list[EvidenceChannel] = []
        for channel in EvidenceChannel:
            adapter = self.get_adapter(channel)
            if adapter is not None and adapter.health_check():
                available.append(channel)
        return available


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ChannelAdapterProtocol",
    "Z3ChannelAdapter",
    "CopilotChannelAdapter",
    "RuntimeWitnessAdapter",
    "HumanEscalationAdapter",
    "CompositeChannelOrchestrator",
    "ChannelLoadBalancer",
    "ChannelSelector",
]

# ---------------------------------------------------------------------------
# Module-level example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== channel_selection.py — basic usage example ===\n")

    # 1. Build a fully configured selector with all default adapters.
    selector = ChannelSelector.default()
    print(f"Selector ID : {selector.selector_id}")
    print(f"Available channels: {[ch.value for ch in selector.available_channels()]}\n")

    # 2. Route an SMT formula task to Z3.
    z3_task = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "arithmetic",
        "formula": "(assert (= (+ x y) 10))",
    }
    z3_decision = selector.select_channel(z3_task)
    print(f"Z3 task → channel={z3_decision.channel.value}, "
          f"confidence={z3_decision.confidence:.2f}")
    z3_result = selector.execute_task(z3_task, z3_decision)
    print(f"  status={z3_result['status']}, result={z3_result['result']}\n")

    # 3. Route a natural-language explanation task to the LLM.
    llm_task = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "natural_language",
        "prompt": "Explain the Liskov substitution principle in simple terms.",
    }
    llm_decision = selector.select_channel(llm_task)
    print(f"LLM task  → channel={llm_decision.channel.value}, "
          f"confidence={llm_decision.confidence:.2f}")
    llm_result = selector.execute_task(llm_task, llm_decision)
    print(f"  status={llm_result['status']}\n")

    # 4. Route a test-case task to the runtime witness adapter.
    witness_task = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "test_case",
        "code": "def add(a, b): return a + b",
        "test_count": 5,
    }
    w_decision = selector.select_channel(witness_task)
    print(f"Witness task → channel={w_decision.channel.value}, "
          f"confidence={w_decision.confidence:.2f}")
    w_result = selector.execute_task(witness_task, w_decision)
    print(f"  status={w_result['status']}, "
          f"tests_run={w_result['evidence'].get('tests_run')}\n")

    # 5. Show load balancer snapshot.
    print("Load balancer snapshot:")
    snapshot = selector.load_balancer.snapshot()
    for ch, factor in snapshot["load_factors"].items():
        print(f"  {ch}: {factor:.2f}")

    # 6. Show routing history summary.
    history = selector.routing_history
    print(f"\nRouting history: {len(history.decisions)} decisions recorded")
    print(f"Average confidence: {history.average_confidence():.2f}")

    # 7. Demonstrate the composite orchestrator.
    print("\n--- CompositeChannelOrchestrator demo ---")
    orchestrator = CompositeChannelOrchestrator(
        orchestrator_id=str(uuid.uuid4()),
        combination_strategy="first_success",
    )
    orchestrator.add_adapter(Z3ChannelAdapter(adapter_id="z3-demo"))
    orchestrator.add_adapter(CopilotChannelAdapter(adapter_id="copilot-demo"))

    composite_task = {"claim_kind": "arithmetic", "formula": "(assert (> x 0))"}
    merged = orchestrator.merge_results(orchestrator.execute_all(composite_task))
    print(f"Merged result channel: {merged.get('channel')}, "
          f"status: {merged.get('status')}")
    print(f"Health: {orchestrator.health_summary()}")
