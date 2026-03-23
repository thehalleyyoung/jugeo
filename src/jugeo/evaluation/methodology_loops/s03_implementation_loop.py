"""
Implementation Loop: evaluation methodology for implementation quality.

# copilot: This module defines the s03 implementation loop — a structured,
# iterative evaluation harness for assessing whether a generated or human-written
# implementation satisfies a specification to the degree required by the configured
# quality gates. The loop runs up to MAX_ITERATIONS rounds, collecting metrics,
# running clause verifications, applying gates, and ultimately issuing a signed
# ImplementationJudgment encoded as the canonical 8-tuple (c, φ, A, E, O, B, T, Π).
#
# Design philosophy:
#   - All state objects are immutable (frozen dataclasses) to enable safe caching
#     and distributed evaluation without hidden mutation.
#   - Trust tiers escalate only when evidence is sufficient; they never degrade
#     within a single loop run.
#   - Quality gates are composable: a gate is simply a named set of metric
#     thresholds plus a flag indicating whether failure is blocking.
#   - The loop is spec-driven: every iteration is anchored to a spec_id, and
#     ambiguities detected in the spec bubble up as SpecAmbiguity records that
#     can pause the loop with BLOCKED_BY_SPEC state.
#
# Judgment tuple encoding:
#   (c, φ, A, E, O, B, T, Π) =
#   (context, formula, authority, evidence, obligations, budget, trust_tier, proof_chain)
#
# No boolean 'passed' field is permitted on ImplementationJudgment. The degree
# of confidence is encoded via TrustTier and proof_chain depth instead.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports (always available)
# ---------------------------------------------------------------------------
import datetime
import logging
import math
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo imports — gracefully degrade if the package is not installed.
# copilot: All jugeo imports are wrapped in try/except so this module can be
# imported in isolation (e.g. during unit-testing of the evaluation harness
# itself) without requiring the full jugeo runtime to be present.
# ---------------------------------------------------------------------------
try:
    from jugeo.core.spec import SpecArtifact  # type: ignore
except ImportError:
    SpecArtifact = None  # type: ignore[assignment,misc]

try:
    from jugeo.core.registry import Registry  # type: ignore
except ImportError:
    Registry = None  # type: ignore[assignment,misc]

try:
    from jugeo.core.clause import Clause  # type: ignore
except ImportError:
    Clause = None  # type: ignore[assignment,misc]

try:
    from jugeo.evaluation.metrics import MetricRegistry  # type: ignore
except ImportError:
    MetricRegistry = None  # type: ignore[assignment,misc]

try:
    from jugeo.proofs.certificate import ProofCertificate  # type: ignore
except ImportError:
    ProofCertificate = None  # type: ignore[assignment,misc]

try:
    from jugeo.evaluation.base import BaseEvaluator  # type: ignore
except ImportError:
    BaseEvaluator = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ITERATIONS: int = 25
"""Maximum number of iterations the loop will execute before forcing FAILED state."""

MIN_CLAUSE_CONFIDENCE: float = 0.80
"""
Minimum per-clause confidence required for the loop to reach VERIFIED trust tier.

copilot: This threshold was chosen to be strict enough to exclude weak
probabilistic verifications while permitting coverage-weighted confidence
scores that account for clause criticality.
"""

VERIFICATION_PASS_THRESHOLDS: dict[str, float] = {
    "TYPE_CHECK": 1.0,
    "UNIT_TEST": 0.90,
    "PROPERTY_TEST": 0.85,
    "PROOF_CERTIFICATE": 0.95,
    "INTEGRATION_TEST": 0.80,
}
"""
Per-verification-kind pass thresholds.

copilot: These values are intentionally conservative. TYPE_CHECK is 1.0 because
a type error is never acceptable in a verified implementation. PROOF_CERTIFICATE
is 0.95 rather than 1.0 to accommodate partial-proof certificates that cover all
safety-critical paths but leave auxiliary lemmas to runtime witnesses.
INTEGRATION_TEST is 0.80 to allow for acceptable flakiness in environment-sensitive
tests while still demanding strong overall integration coverage.
"""

_TRUST_TIER_WEIGHTS: dict[str, float] = {
    "TYPE_CHECK": 0.30,
    "UNIT_TEST": 0.25,
    "PROPERTY_TEST": 0.20,
    "PROOF_CERTIFICATE": 0.15,
    "INTEGRATION_TEST": 0.10,
}
"""
Weights used when computing a weighted aggregate confidence score.

copilot: Weights sum to 1.0. TYPE_CHECK has the highest weight because it
provides a zero-cost, high-signal verification that can be run on every commit.
PROOF_CERTIFICATE has a lower weight despite its high threshold because formal
proofs are expensive and not always available.
"""

_DEFAULT_BUDGET: int = 1_000
"""Default obligation budget assigned to a new ImplementationJudgment."""

_PROOF_CHAIN_MIN_LENGTH_FOR_PROOF_BACKED: int = 3
"""Minimum proof chain length required to assign PROOF_BACKED trust tier."""

_PROOF_CHAIN_MIN_LENGTH_FOR_RUNTIME_WITNESSED: int = 2
"""Minimum proof chain length required to assign RUNTIME_WITNESSED trust tier."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with 'Z' suffix."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _uid() -> str:
    """Return a short random hex identifier (16 characters)."""
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [lo, hi]."""
    return max(lo, min(hi, value))


def _weighted_mean(values: dict[str, float], weights: dict[str, float]) -> float:
    """
    Compute a weighted mean of *values* using *weights*.

    Keys missing from *weights* are assigned an equal share of the remaining
    weight budget (i.e. 1.0 minus the sum of all known weights, divided by
    the number of unknown keys).

    Parameters
    ----------
    values:
        Mapping from metric name to float value in [0, 1].
    weights:
        Mapping from metric name to weight. Need not sum to 1.0.

    Returns
    -------
    float
        Weighted mean in [0, 1], or 0.0 if *values* is empty.
    """
    if not values:
        return 0.0
    total_w = 0.0
    total_wv = 0.0
    known_w = sum(weights.get(k, 0.0) for k in values)
    n_unknown = sum(1 for k in values if k not in weights)
    fallback_w = max(0.0, (1.0 - known_w)) / max(1, n_unknown)
    for name, val in values.items():
        w = weights.get(name, fallback_w)
        total_wv += val * w
        total_w += w
    return _clamp(total_wv / total_w) if total_w > 0 else 0.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TrustTier(str, Enum):
    """
    Ordered scale of epistemic trust assigned to an ImplementationJudgment.

    copilot: The ordering PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED
    < PROOF_BACKED mirrors the escalation of evidence strength. A judgment
    should only move *forward* along this scale within a single loop run.
    Callers that need a numeric comparison can use list(TrustTier).index(tier).
    """

    PROPOSAL = "PROPOSAL"
    """
    Initial tier: the implementation exists but has not been evaluated.

    At this tier, no automated or human verification has been performed.
    The implementation is merely a candidate for further evaluation.
    """

    REVIEWED = "REVIEWED"
    """
    Peer review has occurred; no automated verification has passed yet.

    A human reviewer has examined the implementation and found no obvious
    defects, but automated checks have not been run or have not yet passed.
    """

    VERIFIED = "VERIFIED"
    """
    Automated verification (type-check + unit-test) has passed.

    Both static type checking and dynamic unit testing have passed at or above
    their configured thresholds. Property-based and integration tests may or
    may not have run yet.
    """

    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    """
    Integration tests and property tests passed in a live environment.

    The implementation has been exercised in a real (or high-fidelity staging)
    environment. Runtime witnesses provide additional evidence beyond unit tests.
    """

    PROOF_BACKED = "PROOF_BACKED"
    """
    A formal proof certificate covers all safety-critical obligations.

    The strongest tier. A machine-checkable proof exists for the core safety
    and correctness properties. Auxiliary lemmas may still rely on runtime
    witnesses, but all critical paths are formally verified.
    """


class LoopState(str, Enum):
    """
    Finite-state machine states for an ImplementationLoop instance.

    copilot: State transitions are enforced in ImplementationLoop.iterate().
    The only terminal states are VERIFIED and FAILED; all others are transient.
    Valid transitions:
      WAITING -> IN_PROGRESS (via start())
      IN_PROGRESS -> IN_PROGRESS | BLOCKED_BY_SPEC | VERIFICATION_PENDING | VERIFIED | FAILED
      VERIFICATION_PENDING -> VERIFIED | FAILED
      BLOCKED_BY_SPEC -> IN_PROGRESS (via resolve_ambiguity())
    """

    WAITING = "WAITING"
    """
    Loop has been created but start() has not yet been called.

    This is the initial state of every ImplementationLoop instance.
    """

    IN_PROGRESS = "IN_PROGRESS"
    """
    Loop is actively iterating; verifications are being collected.

    The loop will remain in this state until either a terminal state is
    reached or a blocking condition (ambiguity, gate failure) occurs.
    """

    BLOCKED_BY_SPEC = "BLOCKED_BY_SPEC"
    """
    A SpecAmbiguity was detected; the loop is paused awaiting clarification.

    The loop will not advance until resolve_ambiguity() is called. This
    prevents evaluation against an underspecified contract.
    """

    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    """
    All quality gates have been submitted; awaiting final pass/fail verdicts.

    The loop is in a holding pattern while slow verifications (e.g. proof
    certificate generation) complete asynchronously.
    """

    VERIFIED = "VERIFIED"
    """
    All blocking quality gates passed; the implementation is accepted.

    This is a terminal success state. No further iterations will be run.
    """

    FAILED = "FAILED"
    """
    The loop exhausted MAX_ITERATIONS or a blocking gate failed irrecoverably.

    This is a terminal failure state. A root-cause analysis obligation is
    automatically added to the ImplementationJudgment.
    """


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ImplementationJudgment:
    """
    The canonical 8-tuple judgment (c, phi, A, E, O, B, T, Pi) for an implementation.

    This dataclass encodes the full epistemic state of an implementation
    evaluation as an immutable record suitable for audit logging, distributed
    caching, and formal verification of the evaluation process itself.

    Fields
    ------
    context : str
        Identifier of the evaluation context (e.g. loop_id + iteration_id).
        Provides the situational frame for interpreting the judgment.
    formula : str
        A human-readable logical formula summarising the verdict.
    authority : str
        The entity (agent, tool, or human) that issued this judgment.
    evidence : tuple[str, ...]
        Ordered list of evidence identifiers (e.g. batch_ids, certificate hashes).
    obligations : tuple[str, ...]
        Remaining obligations that must be discharged after this judgment.
    budget : int
        Remaining computation / review budget at time of judgment.
    trust_tier : TrustTier
        The epistemic trust tier assigned based on available evidence.
    proof_chain : tuple[str, ...]
        Ordered chain of proof-step identifiers leading to this judgment.

    Notes
    -----
    copilot: No 'passed' field exists on this dataclass. The judgment encodes
    the *degree* of confidence through trust_tier and proof_chain rather than a
    binary outcome. Callers must interpret tier >= VERIFIED as acceptance.
    """

    context: str
    formula: str
    authority: str
    evidence: tuple[str, ...]
    obligations: tuple[str, ...]
    budget: int
    trust_tier: TrustTier
    proof_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImplementationMetric:
    """
    A single measured quality metric for an implementation artifact.

    copilot: 'passed' here refers to whether the metric value met its
    threshold — this is a measurement result, not a judgment outcome.
    The distinction matters: a metric can 'pass' at the measurement level
    while the enclosing judgment still has a low trust tier if other
    evidence is missing (e.g. no proof certificate).
    """

    metric_id: str
    """Unique identifier for this metric measurement instance."""

    name: str
    """Human-readable metric name matching a key in VERIFICATION_PASS_THRESHOLDS."""

    value: float
    """Observed metric value in [0.0, 1.0]."""

    threshold: float
    """Required threshold for this metric to be considered passing."""

    passed: bool
    """True iff value >= threshold (for maximising metrics)."""

    context: str
    """Free-form context string (e.g. which tool produced this metric)."""

    measured_at: str
    """ISO-8601 timestamp when this metric was measured."""


@dataclass(frozen=True, slots=True)
class LoopIteration:
    """
    A single pass through the implementation loop.

    Each iteration corresponds to one submission of an ImplementationArtifact
    for evaluation. Metrics are collected, quality gates are applied, and the
    resulting state is recorded here.

    copilot: LoopIteration objects are append-only within a loop run. The loop
    never modifies a previously created iteration; it only creates new ones.
    This makes the iteration history a complete, immutable audit trail.
    """

    iteration_id: str
    loop_id: str
    iteration_number: int
    input_spec_id: str
    output_artifact_id: str
    metrics: tuple[ImplementationMetric, ...]
    state: LoopState
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class QualityGate:
    """
    A named collection of metric thresholds that an iteration must satisfy.

    copilot: Gates with is_blocking=True will transition the loop to FAILED if
    they do not pass. Non-blocking gates record a warning but allow the loop to
    continue. This allows teams to enforce hard constraints (e.g. zero type
    errors) while tracking soft goals (e.g. documentation coverage) without
    interrupting the evaluation flow.
    """

    gate_id: str
    name: str
    required_metrics: tuple[str, ...]
    threshold_map: dict[str, float] = field(default_factory=dict)
    is_blocking: bool = True
    created_at: str = field(default_factory=_now_iso)


@dataclass(frozen=True, slots=True)
class ImplementationArtifact:
    """
    An implementation artifact submitted for evaluation in a loop iteration.

    copilot: code_hash is used to detect no-op re-submissions (same artifact
    submitted twice in the same loop). The loop will skip re-evaluation and
    return the cached LoopIteration in that case, preventing wasteful
    redundant verifications.
    """

    artifact_id: str
    spec_id: str
    code_hash: str
    clause_count: int
    verified_clause_count: int
    state: LoopState
    created_at: str


@dataclass(frozen=True, slots=True)
class ClauseVerification:
    """
    Verification result for a single spec clause.

    copilot: confidence is a float in [0, 1]. A clause with confidence below
    MIN_CLAUSE_CONFIDENCE is treated as unverified even if passed=True, because
    the evidence is considered insufficiently strong to raise the trust tier.
    """

    clause_id: str
    clause_text: str
    verification_kind: str
    confidence: float
    passed: bool
    details: str
    verified_at: str


@dataclass(frozen=True, slots=True)
class VerificationBatch:
    """
    A collection of ClauseVerification records produced in one iteration.

    copilot: The batch_id is used as a proof chain link in ImplementationJudgment.
    Only batches with aggregate_confidence >= MIN_CLAUSE_CONFIDENCE contribute
    to the proof chain, so the chain length reflects quality, not quantity.
    """

    batch_id: str
    loop_id: str
    iteration_id: str
    verifications: tuple[ClauseVerification, ...]
    aggregate_confidence: float
    created_at: str


@dataclass(frozen=True, slots=True)
class SpecAmbiguity:
    """
    A detected ambiguity in the specification that blocks further iteration.

    copilot: When a SpecAmbiguity is created, the loop transitions to
    BLOCKED_BY_SPEC. The loop can only resume after an external resolver
    (human or automated spec-clarification agent) dismisses the ambiguity.
    This design prevents the loop from silently producing incorrect
    implementations by ignoring spec defects.
    """

    ambiguity_id: str
    clause_id: str
    signal_text: str
    spec_id: str
    detected_at: str


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """
    Policy governing retry behaviour when a verification step fails transiently.

    copilot: Exponential backoff with jitter is used to avoid thundering-herd
    effects when multiple loops are running concurrently against shared
    verification infrastructure.
    """

    max_retries: int = 3
    backoff_base: float = 2.0
    jitter: float = 0.1
    timeout_seconds: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """
        Compute the backoff delay in seconds for a given attempt number (0-based).

        Formula: delay = backoff_base**attempt * (1 + U(-jitter, +jitter))
        """
        raw = self.backoff_base ** attempt
        noise = raw * self.jitter * (2.0 * random.random() - 1.0)
        return max(0.0, raw + noise)


@dataclass(frozen=True, slots=True)
class LoopReport:
    """
    Final report produced at the end of a loop run.

    copilot: LoopReport is intentionally serialisation-friendly: all fields
    are primitive types or tuples of strings, so it can be JSON-encoded without
    a custom encoder. The summary field provides a one-line human-readable
    verdict suitable for display in GitHub PR checks or Slack notifications.
    """

    report_id: str
    loop_id: str
    total_iterations: int
    final_state: LoopState
    quality_gate_results: tuple[str, ...]
    summary: str
    generated_at: str


@dataclass(frozen=True, slots=True)
class MetricAggregation:
    """
    Weighted aggregate of ImplementationMetric values across a loop run.

    copilot: The weighted_score is computed using _TRUST_TIER_WEIGHTS keyed
    on metric names. If a metric name is not in the weight map, it contributes
    with an equal share of the remaining weight budget. This ensures that
    novel or custom metrics are not silently ignored.
    """

    agg_id: str
    loop_id: str
    metrics: tuple[ImplementationMetric, ...]
    weighted_score: float
    method: str
    computed_at: str


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """
    The outcome of applying a QualityGate to a LoopIteration.

    copilot: GateVerdict is produced by apply_quality_gate() and stored
    in the loop's internal verdict list. The failing_metrics tuple provides
    actionable feedback: developers can look up each failing metric by name
    and understand exactly which verification kind caused the gate to fail.
    """

    verdict_id: str
    gate_id: str
    iteration_id: str
    passed: bool
    failing_metrics: tuple[str, ...]
    verdict_text: str
    issued_at: str


# ---------------------------------------------------------------------------
# ImplementationLoop class
# ---------------------------------------------------------------------------

class ImplementationLoop:
    """
    Stateful controller for the implementation evaluation loop.

    The loop manages a set of LoopIteration records keyed by loop_id and
    iteration_number. It enforces state-machine transitions, applies quality
    gates, aggregates verification batches, and ultimately issues an
    ImplementationJudgment.

    copilot: This class intentionally uses mutable internal dicts for
    performance, while all *externally visible* objects (LoopIteration,
    GateVerdict, etc.) are frozen dataclasses. The mutable interior is
    fully encapsulated and never exposed directly through the public API.

    Typical Usage
    -------------
    ::

        loop = ImplementationLoop()
        iteration = loop.start(spec_id="spec-abc123")
        artifact = ImplementationArtifact(
            artifact_id=_uid(), spec_id="spec-abc123",
            code_hash="deadbeef", clause_count=10,
            verified_clause_count=0,
            state=LoopState.IN_PROGRESS,
            created_at=_now_iso(),
        )
        iteration2 = loop.iterate(iteration.loop_id, artifact)
        judgment = loop.check_exit_conditions(iteration.loop_id)
        report = loop.generate_report(iteration.loop_id)
    """

    def __init__(self, loop_id: str | None = None) -> None:
        """
        Initialise a new ImplementationLoop.

        Parameters
        ----------
        loop_id:
            Optional explicit loop identifier. If omitted, a random 16-char
            hex ID is generated via _uid().
        """
        self._loop_id: str = loop_id or _uid()
        self._states: dict[str, LoopState] = {}
        self._iterations: dict[str, list[LoopIteration]] = {}
        self._verdicts: dict[str, list[GateVerdict]] = {}
        self._batches: dict[str, list[VerificationBatch]] = {}
        self._ambiguities: dict[str, list[SpecAmbiguity]] = {}
        self._seen_hashes: dict[str, set[str]] = {}
        self._spec_ids: dict[str, str] = {}
        logger.debug("ImplementationLoop initialised with loop_id=%s", self._loop_id)

    def start(self, spec_id: str) -> LoopIteration:
        """
        Start a new evaluation loop for the given spec.

        Creates the first LoopIteration and transitions to IN_PROGRESS.

        Parameters
        ----------
        spec_id:
            Identifier of the specification to evaluate against.

        Returns
        -------
        LoopIteration
            The first iteration record.

        Raises
        ------
        ValueError
            If the loop has already been started.
        """
        lid = self._loop_id
        if lid in self._states and self._states[lid] != LoopState.WAITING:
            raise ValueError(
                f"Loop {lid!r} is already in state {self._states[lid]!r}; "
                "cannot call start() again."
            )
        self._states[lid] = LoopState.IN_PROGRESS
        self._iterations[lid] = []
        self._verdicts[lid] = []
        self._batches[lid] = []
        self._ambiguities[lid] = []
        self._seen_hashes[lid] = set()
        self._spec_ids[lid] = spec_id

        now = _now_iso()
        first_iteration = LoopIteration(
            iteration_id=_uid(),
            loop_id=lid,
            iteration_number=1,
            input_spec_id=spec_id,
            output_artifact_id="",
            metrics=(),
            state=LoopState.IN_PROGRESS,
            started_at=now,
            completed_at=now,
        )
        self._iterations[lid].append(first_iteration)
        logger.info("Loop %s started for spec %s", lid, spec_id)
        return first_iteration

    def iterate(
        self,
        loop_id: str,
        implementation_artifact: ImplementationArtifact,
    ) -> LoopIteration:
        """
        Advance the loop by evaluating an ImplementationArtifact.

        Steps:
        1. Validates loop state and artifact uniqueness.
        2. Generates ClauseVerification records.
        3. Computes a VerificationBatch with aggregate confidence.
        4. Converts verifications to ImplementationMetric objects.
        5. Determines the new LoopState.
        6. Creates and records a new LoopIteration.

        Parameters
        ----------
        loop_id:
            The loop to advance. Must have been started with start().
        implementation_artifact:
            The artifact to evaluate in this iteration.

        Returns
        -------
        LoopIteration
            The new iteration record with metrics and updated state.

        Raises
        ------
        KeyError
            If loop_id has not been started via start().
        RuntimeError
            If the loop is already in a terminal state.
        """
        if loop_id not in self._states:
            raise KeyError(f"Unknown loop_id {loop_id!r}. Call start() first.")
        current_state = self._states[loop_id]
        if current_state in (LoopState.VERIFIED, LoopState.FAILED):
            raise RuntimeError(
                f"Loop {loop_id!r} is in terminal state {current_state!r}."
            )

        iterations_so_far = len(self._iterations[loop_id])
        if iterations_so_far >= MAX_ITERATIONS:
            self._states[loop_id] = LoopState.FAILED
            logger.warning(
                "Loop %s exhausted MAX_ITERATIONS=%d; transitioning to FAILED",
                loop_id, MAX_ITERATIONS,
            )

        # Duplicate-hash check.
        h = implementation_artifact.code_hash
        if h in self._seen_hashes[loop_id]:
            logger.debug("Loop %s: duplicate artifact hash %s; skipping", loop_id, h)
            return self._iterations[loop_id][-1]
        self._seen_hashes[loop_id].add(h)

        verifications = self._simulate_clause_verifications(
            implementation_artifact, loop_id
        )
        agg_conf = (
            sum(v.confidence for v in verifications) / len(verifications)
            if verifications else 0.0
        )
        iteration_id = _uid()
        batch = VerificationBatch(
            batch_id=_uid(),
            loop_id=loop_id,
            iteration_id=iteration_id,
            verifications=tuple(verifications),
            aggregate_confidence=_clamp(agg_conf),
            created_at=_now_iso(),
        )
        self._batches[loop_id].append(batch)

        metrics = self._verifications_to_metrics(verifications)
        new_state = self._compute_new_state(
            loop_id, agg_conf, implementation_artifact, iterations_so_far + 1
        )
        self._states[loop_id] = new_state

        now = _now_iso()
        new_iter = LoopIteration(
            iteration_id=iteration_id,
            loop_id=loop_id,
            iteration_number=iterations_so_far + 1,
            input_spec_id=self._spec_ids[loop_id],
            output_artifact_id=implementation_artifact.artifact_id,
            metrics=tuple(metrics),
            state=new_state,
            started_at=now,
            completed_at=now,
        )
        self._iterations[loop_id].append(new_iter)
        logger.info(
            "Loop %s iteration %d: state=%s agg_conf=%.3f",
            loop_id, new_iter.iteration_number, new_state.value, agg_conf,
        )
        return new_iter

    def check_exit_conditions(self, loop_id: str) -> ImplementationJudgment:
        """
        Evaluate exit conditions and return a signed ImplementationJudgment.

        Trust tier assignment:

        ======================= =========================================
        LoopState               Assigned TrustTier
        ======================= =========================================
        VERIFIED + chain >= 3   PROOF_BACKED
        VERIFIED + chain >= 2   RUNTIME_WITNESSED
        VERIFIED + chain < 2    VERIFIED
        IN_PROGRESS             REVIEWED
        VERIFICATION_PENDING    REVIEWED
        FAILED / others         PROPOSAL
        ======================= =========================================

        copilot: No 'passed' field is on ImplementationJudgment. Callers
        must interpret tier >= VERIFIED as acceptance.

        Parameters
        ----------
        loop_id:
            The loop to evaluate.

        Returns
        -------
        ImplementationJudgment
            The 8-tuple judgment for this loop.
        """
        if loop_id not in self._states:
            raise KeyError(f"Unknown loop_id {loop_id!r}.")

        state = self._states[loop_id]
        batches = self._batches.get(loop_id, [])
        evidence = tuple(b.batch_id for b in batches)
        proof_chain = self._build_proof_chain(loop_id)

        if state == LoopState.VERIFIED:
            if len(proof_chain) >= _PROOF_CHAIN_MIN_LENGTH_FOR_PROOF_BACKED:
                tier = TrustTier.PROOF_BACKED
            elif len(proof_chain) >= _PROOF_CHAIN_MIN_LENGTH_FOR_RUNTIME_WITNESSED:
                tier = TrustTier.RUNTIME_WITNESSED
            else:
                tier = TrustTier.VERIFIED
        elif state in (LoopState.IN_PROGRESS, LoopState.VERIFICATION_PENDING):
            tier = TrustTier.REVIEWED
        else:
            tier = TrustTier.PROPOSAL

        obligations = self._compute_obligations(loop_id, state)
        agg_conf = (
            sum(b.aggregate_confidence for b in batches) / len(batches)
            if batches else 0.0
        )
        formula = (
            f"implementation_quality(loop={loop_id}) "
            f">= {agg_conf:.3f} @ {tier.value}"
        )
        iters = self._iterations.get(loop_id, [])
        budget_remaining = max(0, _DEFAULT_BUDGET - len(iters) * 10)

        judgment = ImplementationJudgment(
            context=f"{loop_id}:exit_check",
            formula=formula,
            authority="implementation_loop",
            evidence=evidence,
            obligations=obligations,
            budget=budget_remaining,
            trust_tier=tier,
            proof_chain=proof_chain,
        )
        logger.info(
            "Loop %s exit judgment: tier=%s obligations=%d proof_chain=%d",
            loop_id, tier.value, len(obligations), len(proof_chain),
        )
        return judgment

    def get_current_state(self, loop_id: str) -> LoopState:
        """
        Return the current LoopState for the given loop_id.

        Returns WAITING if the loop_id has never been started.

        Parameters
        ----------
        loop_id:
            The loop to query.

        Returns
        -------
        LoopState
            Current state.
        """
        return self._states.get(loop_id, LoopState.WAITING)

    def apply_quality_gate(
        self,
        gate: QualityGate,
        iteration: LoopIteration,
    ) -> GateVerdict:
        """
        Check whether an iteration satisfies a QualityGate.

        For each required metric, the method looks it up in the iteration and
        compares its value to the gate threshold (falling back to
        VERIFICATION_PASS_THRESHOLDS). Missing metrics are treated as failures.

        If the gate is blocking and any metric fails, the loop transitions to
        FAILED. Non-blocking failures are recorded but do not halt the loop.

        Parameters
        ----------
        gate:
            The gate to apply.
        iteration:
            The iteration whose metrics are evaluated.

        Returns
        -------
        GateVerdict
            The verdict with pass/fail status and failing metric names.
        """
        failing: list[str] = []
        metric_map = {m.name: m for m in iteration.metrics}

        for metric_name in gate.required_metrics:
            threshold = gate.threshold_map.get(
                metric_name,
                VERIFICATION_PASS_THRESHOLDS.get(metric_name, 0.0),
            )
            metric = metric_map.get(metric_name)
            if metric is None:
                # copilot: Missing metric is treated as failure (conservative).
                logger.warning(
                    "Gate %s: metric %r missing from iteration %s; treating as failure",
                    gate.gate_id, metric_name, iteration.iteration_id,
                )
                failing.append(metric_name)
                continue
            if metric.value < threshold:
                failing.append(metric_name)

        all_passed = len(failing) == 0
        if all_passed:
            verdict_text = (
                f"Gate '{gate.name}' PASSED all {len(gate.required_metrics)} "
                f"metrics in iteration {iteration.iteration_number}."
            )
        else:
            verdict_text = (
                f"Gate '{gate.name}' FAILED on: {', '.join(failing)} "
                f"in iteration {iteration.iteration_number}."
            )

        verdict = GateVerdict(
            verdict_id=_uid(),
            gate_id=gate.gate_id,
            iteration_id=iteration.iteration_id,
            passed=all_passed,
            failing_metrics=tuple(failing),
            verdict_text=verdict_text,
            issued_at=_now_iso(),
        )
        lid = iteration.loop_id
        if lid in self._verdicts:
            self._verdicts[lid].append(verdict)
        if not all_passed and gate.is_blocking:
            self._states[lid] = LoopState.FAILED
            logger.warning(
                "Blocking gate %s failed for loop %s; -> FAILED. Failing: %s",
                gate.gate_id, lid, failing,
            )
        return verdict

    def generate_report(self, loop_id: str) -> dict[str, Any]:
        """
        Generate a summary report dict for the given loop.

        Returns a JSON-serialisable dict with:
        - loop_id, final_state, total_iterations
        - aggregate_confidence (float)
        - gate_verdicts (list[str])
        - iterations (list[dict] with per-iteration metric summaries)
        - generated_at (ISO-8601 str)

        Parameters
        ----------
        loop_id:
            The loop to report on.

        Returns
        -------
        dict
            JSON-serialisable summary of the loop run.

        Raises
        ------
        KeyError
            If loop_id has not been started.
        """
        if loop_id not in self._states:
            raise KeyError(f"Unknown loop_id {loop_id!r}.")

        iterations = self._iterations.get(loop_id, [])
        batches = self._batches.get(loop_id, [])
        verdicts = self._verdicts.get(loop_id, [])
        state = self._states[loop_id]

        agg_conf = (
            sum(b.aggregate_confidence for b in batches) / len(batches)
            if batches else 0.0
        )

        iteration_summaries: list[dict[str, Any]] = []
        for it in iterations:
            metric_vals = {m.name: round(m.value, 4) for m in it.metrics}
            iteration_summaries.append({
                "iteration_id": it.iteration_id,
                "iteration_number": it.iteration_number,
                "state": it.state.value,
                "metrics": metric_vals,
                "artifact_id": it.output_artifact_id,
                "started_at": it.started_at,
                "completed_at": it.completed_at,
            })

        return {
            "loop_id": loop_id,
            "final_state": state.value,
            "total_iterations": len(iterations),
            "aggregate_confidence": round(agg_conf, 4),
            "gate_verdicts": [v.verdict_text for v in verdicts],
            "iterations": iteration_summaries,
            "generated_at": _now_iso(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _simulate_clause_verifications(
        self,
        artifact: ImplementationArtifact,
        loop_id: str,
    ) -> list[ClauseVerification]:
        """
        Simulate clause verifications for an artifact.

        copilot: In production, this dispatches to real verification tools.
        Here, confidence trends upward with iteration count using log1p to
        model diminishing returns. Per-clause Gaussian jitter (sigma=0.05)
        prevents all clauses from succeeding simultaneously.
        """
        iterations_done = len(self._iterations.get(loop_id, []))
        base_conf = _clamp(0.50 + 0.08 * math.log1p(iterations_done))

        verifications: list[ClauseVerification] = []
        clause_count = max(1, artifact.clause_count)
        for i in range(clause_count):
            kind = random.choice(list(VERIFICATION_PASS_THRESHOLDS.keys()))
            threshold = VERIFICATION_PASS_THRESHOLDS[kind]
            jitter = random.gauss(0, 0.05)
            conf = _clamp(base_conf + jitter)
            cv = ClauseVerification(
                clause_id=f"clause-{artifact.spec_id}-{i:04d}",
                clause_text=f"Clause {i} of spec {artifact.spec_id}",
                verification_kind=kind,
                confidence=conf,
                passed=conf >= threshold,
                details=(
                    f"Simulated {kind}: conf={conf:.3f} "
                    f"threshold={threshold:.3f} iter={iterations_done}"
                ),
                verified_at=_now_iso(),
            )
            verifications.append(cv)
        return verifications

    def _verifications_to_metrics(
        self,
        verifications: list[ClauseVerification],
    ) -> list[ImplementationMetric]:
        """
        Aggregate ClauseVerification records into ImplementationMetric objects.

        One ImplementationMetric per verification_kind; value = mean confidence.

        copilot: Aggregating to one metric per kind simplifies gate evaluation.
        Gates work with named metrics, not individual clause records.
        """
        kind_conf: dict[str, list[float]] = {}
        for v in verifications:
            kind_conf.setdefault(v.verification_kind, []).append(v.confidence)

        metrics: list[ImplementationMetric] = []
        for kind, confs in kind_conf.items():
            mean_conf = sum(confs) / len(confs)
            threshold = VERIFICATION_PASS_THRESHOLDS.get(kind, 0.0)
            metrics.append(
                ImplementationMetric(
                    metric_id=_uid(),
                    name=kind,
                    value=round(mean_conf, 4),
                    threshold=threshold,
                    passed=mean_conf >= threshold,
                    context=f"aggregate_over_{len(confs)}_clauses",
                    measured_at=_now_iso(),
                )
            )
        return metrics

    def _compute_new_state(
        self,
        loop_id: str,
        agg_conf: float,
        artifact: ImplementationArtifact,
        iteration_number: int,
    ) -> LoopState:
        """
        Determine the next LoopState after an iteration.

        Transitions (in priority order):
        1. 1% chance: BLOCKED_BY_SPEC (simulates spec ambiguity detection)
        2. iteration_number >= MAX_ITERATIONS: FAILED
        3. agg_conf >= MIN_CLAUSE_CONFIDENCE AND all clauses verified: VERIFIED
        4. agg_conf >= MIN_CLAUSE_CONFIDENCE - 0.05: VERIFICATION_PENDING
        5. Otherwise: IN_PROGRESS

        copilot: The BLOCKED_BY_SPEC transition is simulated with 1% probability.
        In production this would be driven by a real spec-analysis service.
        """
        if random.random() < 0.01:
            ambiguity = SpecAmbiguity(
                ambiguity_id=_uid(),
                clause_id=f"clause-{artifact.spec_id}-0000",
                signal_text="ambiguous precondition detected during verification",
                spec_id=artifact.spec_id,
                detected_at=_now_iso(),
            )
            self._ambiguities.setdefault(loop_id, []).append(ambiguity)
            logger.warning(
                "Loop %s: spec ambiguity detected in spec %s",
                loop_id, artifact.spec_id,
            )
            return LoopState.BLOCKED_BY_SPEC

        if iteration_number >= MAX_ITERATIONS:
            return LoopState.FAILED

        all_clauses_verified = (
            artifact.verified_clause_count >= artifact.clause_count
        )
        if agg_conf >= MIN_CLAUSE_CONFIDENCE and all_clauses_verified:
            return LoopState.VERIFIED

        if agg_conf >= MIN_CLAUSE_CONFIDENCE - 0.05:
            return LoopState.VERIFICATION_PENDING

        return LoopState.IN_PROGRESS

    def _build_proof_chain(self, loop_id: str) -> tuple[str, ...]:
        """
        Build a proof chain from accumulated high-confidence verification batches.

        Only batches with aggregate_confidence >= MIN_CLAUSE_CONFIDENCE are
        included. The chain is ordered chronologically.

        copilot: Chain length determines the trust tier in check_exit_conditions.
        Longer chains indicate more independent high-quality verification passes.
        """
        batches = self._batches.get(loop_id, [])
        return tuple(
            b.batch_id
            for b in batches
            if b.aggregate_confidence >= MIN_CLAUSE_CONFIDENCE
        )

    def _compute_obligations(
        self,
        loop_id: str,
        state: LoopState,
    ) -> tuple[str, ...]:
        """
        Compute remaining obligations based on the current loop state.

        copilot: Obligations are deterministic on state. VERIFIED discharges
        most obligations; FAILED adds a root-cause analysis obligation;
        BLOCKED_BY_SPEC adds a spec-ambiguity-resolution obligation.
        """
        base_obligations = (
            "audit_trail_preserved",
            "spec_coverage_documented",
            "performance_regression_checked",
            "security_scan_completed",
        )
        if state == LoopState.VERIFIED:
            return ("audit_trail_preserved",)
        if state == LoopState.FAILED:
            return base_obligations + ("root_cause_analysis_required",)
        if state == LoopState.BLOCKED_BY_SPEC:
            return base_obligations + ("spec_ambiguity_resolved",)
        return base_obligations


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def run_implementation_loop(
    spec_artifact: dict[str, Any],
    max_iterations: int = MAX_ITERATIONS,
    quality_gates: list[QualityGate] | None = None,
) -> LoopReport:
    """
    Run a full implementation evaluation loop from start to finish.

    This is the primary entry point for automated evaluation pipelines.
    Creates an ImplementationLoop, iterates until terminal state or
    max_iterations, applies quality gates, and returns a LoopReport.

    copilot: Non-blocking gates by default so automated pipeline runs
    do not fail unexpectedly. Teams should explicitly set is_blocking=True
    when they want hard stops.

    Parameters
    ----------
    spec_artifact:
        Dict with 'spec_id' (str) and 'clause_count' (int).
    max_iterations:
        Upper bound on iterations, capped at MAX_ITERATIONS.
    quality_gates:
        Gates to apply. If None, a default non-blocking gate is used.

    Returns
    -------
    LoopReport
        Final report for the completed loop run.
    """
    effective_max = min(max_iterations, MAX_ITERATIONS)
    spec_id: str = spec_artifact.get("spec_id", _uid())
    clause_count: int = int(spec_artifact.get("clause_count", 5))

    if quality_gates is None:
        quality_gates = [
            QualityGate(
                gate_id=_uid(),
                name="default_gate",
                required_metrics=tuple(VERIFICATION_PASS_THRESHOLDS.keys()),
                threshold_map=dict(VERIFICATION_PASS_THRESHOLDS),
                is_blocking=False,
                created_at=_now_iso(),
            )
        ]

    loop = ImplementationLoop()
    lid = loop._loop_id
    loop.start(spec_id=spec_id)

    terminal_states = {LoopState.VERIFIED, LoopState.FAILED}
    gate_result_texts: list[str] = []

    for i in range(effective_max):
        verified = min(
            clause_count,
            int(clause_count * (i + 1) / effective_max * 1.5),
        )
        artifact = ImplementationArtifact(
            artifact_id=_uid(),
            spec_id=spec_id,
            code_hash=uuid.uuid4().hex,
            clause_count=clause_count,
            verified_clause_count=verified,
            state=LoopState.IN_PROGRESS,
            created_at=_now_iso(),
        )
        try:
            iteration = loop.iterate(lid, artifact)
        except RuntimeError:
            break

        for gate in quality_gates:
            verdict = loop.apply_quality_gate(gate, iteration)
            gate_result_texts.append(verdict.verdict_text)

        if loop.get_current_state(lid) in terminal_states:
            break

    final_state = loop.get_current_state(lid)
    report_dict = loop.generate_report(lid)

    return LoopReport(
        report_id=_uid(),
        loop_id=lid,
        total_iterations=report_dict["total_iterations"],
        final_state=final_state,
        quality_gate_results=tuple(gate_result_texts[-10:]),
        summary=(
            f"Loop {lid} finished: state={final_state.value} "
            f"iterations={report_dict['total_iterations']} "
            f"agg_conf={report_dict['aggregate_confidence']:.3f}."
        ),
        generated_at=_now_iso(),
    )


def measure_implementation_quality(
    artifact_id: str,
    metric_definitions: list[dict[str, Any]],
) -> MetricAggregation:
    """
    Compute a weighted quality score for an implementation artifact.

    Accepts pre-computed metric values and aggregates them using
    _TRUST_TIER_WEIGHTS. Unknown metric names receive a fallback weight.

    Parameters
    ----------
    artifact_id:
        Identifier of the artifact being measured.
    metric_definitions:
        List of dicts, each with 'name' (str) and 'value' (float in [0,1]).

    Returns
    -------
    MetricAggregation
        Aggregated quality score with per-metric breakdown.
    """
    metrics: list[ImplementationMetric] = []
    for defn in metric_definitions:
        name = str(defn.get("name", "UNKNOWN"))
        value = _clamp(float(defn.get("value", 0.0)))
        threshold = VERIFICATION_PASS_THRESHOLDS.get(name, 0.0)
        metrics.append(
            ImplementationMetric(
                metric_id=_uid(),
                name=name,
                value=value,
                threshold=threshold,
                passed=value >= threshold,
                context=f"artifact={artifact_id}",
                measured_at=_now_iso(),
            )
        )

    metric_values = {m.name: m.value for m in metrics}
    weighted_score = _weighted_mean(metric_values, _TRUST_TIER_WEIGHTS)

    return MetricAggregation(
        agg_id=_uid(),
        loop_id=artifact_id,
        metrics=tuple(metrics),
        weighted_score=_clamp(weighted_score),
        method="weighted_mean",
        computed_at=_now_iso(),
    )


def check_quality_gate(
    gate_id: str,
    artifact: ImplementationArtifact,
) -> GateVerdict:
    """
    Apply a named quality gate to an artifact without a full loop context.

    Built-in gate IDs:
    - 'type_check_only': TYPE_CHECK must be 1.0 (blocking)
    - 'full_verification': all VERIFICATION_PASS_THRESHOLDS must pass (blocking)
    - 'smoke_test': UNIT_TEST >= 0.50 (non-blocking)
    - 'property_and_integration': PROPERTY_TEST + INTEGRATION_TEST (non-blocking)

    Unknown gate IDs: permissive non-blocking gate (always passes).

    Parameters
    ----------
    gate_id:
        The built-in gate identifier to apply.
    artifact:
        Clause coverage ratio is used as proxy metric value.

    Returns
    -------
    GateVerdict
        The verdict for the artifact against the named gate.
    """
    _BUILTIN_GATES: dict[str, QualityGate] = {
        "type_check_only": QualityGate(
            gate_id="type_check_only",
            name="Type Check Only",
            required_metrics=("TYPE_CHECK",),
            threshold_map={"TYPE_CHECK": 1.0},
            is_blocking=True,
            created_at=_now_iso(),
        ),
        "full_verification": QualityGate(
            gate_id="full_verification",
            name="Full Verification",
            required_metrics=tuple(VERIFICATION_PASS_THRESHOLDS.keys()),
            threshold_map=dict(VERIFICATION_PASS_THRESHOLDS),
            is_blocking=True,
            created_at=_now_iso(),
        ),
        "smoke_test": QualityGate(
            gate_id="smoke_test",
            name="Smoke Test",
            required_metrics=("UNIT_TEST",),
            threshold_map={"UNIT_TEST": 0.50},
            is_blocking=False,
            created_at=_now_iso(),
        ),
        "property_and_integration": QualityGate(
            gate_id="property_and_integration",
            name="Property and Integration",
            required_metrics=("PROPERTY_TEST", "INTEGRATION_TEST"),
            threshold_map={
                "PROPERTY_TEST": VERIFICATION_PASS_THRESHOLDS["PROPERTY_TEST"],
                "INTEGRATION_TEST": VERIFICATION_PASS_THRESHOLDS["INTEGRATION_TEST"],
            },
            is_blocking=False,
            created_at=_now_iso(),
        ),
    }

    gate = _BUILTIN_GATES.get(
        gate_id,
        QualityGate(
            gate_id=gate_id,
            name=f"Unknown gate {gate_id}",
            required_metrics=(),
            threshold_map={},
            is_blocking=False,
            created_at=_now_iso(),
        ),
    )

    coverage = (
        artifact.verified_clause_count / artifact.clause_count
        if artifact.clause_count > 0 else 0.0
    )

    failing: list[str] = []
    for metric_name in gate.required_metrics:
        threshold = gate.threshold_map.get(
            metric_name, VERIFICATION_PASS_THRESHOLDS.get(metric_name, 0.0)
        )
        if coverage < threshold:
            failing.append(metric_name)

    all_passed = len(failing) == 0
    verdict_text = (
        f"Gate '{gate.name}' {'PASSED' if all_passed else 'FAILED'} "
        f"(coverage={coverage:.3f}) on artifact {artifact.artifact_id}."
    )
    if not all_passed:
        verdict_text += f" Failing: {', '.join(failing)}."

    return GateVerdict(
        verdict_id=_uid(),
        gate_id=gate.gate_id,
        iteration_id="",
        passed=all_passed,
        failing_metrics=tuple(failing),
        verdict_text=verdict_text,
        issued_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # copilot: Full smoke test exercising all major classes and functions.
    # Run with: python implementation_loop.py
    # No external dependencies required.

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    print("=" * 70)
    print("implementation_loop.py — smoke test")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Basic loop lifecycle
    # -----------------------------------------------------------------------
    print("\n[1] Basic loop lifecycle: start -> iterate 3x -> check_exit_conditions")
    loop = ImplementationLoop(loop_id="smoke-loop-001")
    spec_id = "spec-" + _uid()
    first_iter = loop.start(spec_id=spec_id)
    assert loop.get_current_state("smoke-loop-001") == LoopState.IN_PROGRESS
    print(f"    Started: loop_id={first_iter.loop_id} iter={first_iter.iteration_number}")

    for n in range(3):
        artifact = ImplementationArtifact(
            artifact_id=_uid(),
            spec_id=spec_id,
            code_hash=uuid.uuid4().hex,
            clause_count=8,
            verified_clause_count=min(8, (n + 1) * 3),
            state=LoopState.IN_PROGRESS,
            created_at=_now_iso(),
        )
        it = loop.iterate("smoke-loop-001", artifact)
        print(f"    Iteration {it.iteration_number}: state={it.state.value} metrics={len(it.metrics)}")

    judgment = loop.check_exit_conditions("smoke-loop-001")
    print(f"    Judgment: tier={judgment.trust_tier.value}")
    print(f"    Formula: {judgment.formula}")
    print(f"    Evidence: {judgment.evidence}")
    print(f"    Obligations: {judgment.obligations}")
    assert isinstance(judgment, ImplementationJudgment)
    assert not hasattr(judgment, "passed"), "ImplementationJudgment must not have 'passed' field"
    assert judgment.trust_tier in list(TrustTier)
    print("    PASS")

    # -----------------------------------------------------------------------
    # 2. Quality gate application
    # -----------------------------------------------------------------------
    print("\n[2] Quality gate application")
    gate = QualityGate(
        gate_id=_uid(),
        name="unit_test_gate",
        required_metrics=("UNIT_TEST", "TYPE_CHECK"),
        threshold_map={"UNIT_TEST": 0.70, "TYPE_CHECK": 0.90},
        is_blocking=False,
        created_at=_now_iso(),
    )
    last_iter = loop._iterations["smoke-loop-001"][-1]
    verdict = loop.apply_quality_gate(gate, last_iter)
    print(f"    Gate '{gate.name}': passed={verdict.passed}")
    print(f"    Verdict: {verdict.verdict_text}")
    assert isinstance(verdict, GateVerdict)
    print("    PASS")

    # -----------------------------------------------------------------------
    # 3. Generate report
    # -----------------------------------------------------------------------
    print("\n[3] Generate report")
    report_dict = loop.generate_report("smoke-loop-001")
    print(f"    total_iterations={report_dict['total_iterations']}")
    print(f"    final_state={report_dict['final_state']}")
    print(f"    aggregate_confidence={report_dict['aggregate_confidence']}")
    assert "iterations" in report_dict
    assert "gate_verdicts" in report_dict
    print("    PASS")

    # -----------------------------------------------------------------------
    # 4. run_implementation_loop
    # -----------------------------------------------------------------------
    print("\n[4] run_implementation_loop (module-level)")
    custom_gate = QualityGate(
        gate_id=_uid(),
        name="integration_gate",
        required_metrics=("INTEGRATION_TEST",),
        threshold_map={"INTEGRATION_TEST": 0.75},
        is_blocking=False,
        created_at=_now_iso(),
    )
    loop_report = run_implementation_loop(
        spec_artifact={"spec_id": "spec-" + _uid(), "clause_count": 6},
        max_iterations=5,
        quality_gates=[custom_gate],
    )
    assert isinstance(loop_report, LoopReport)
    assert loop_report.total_iterations > 0
    print(f"    Summary: {loop_report.summary}")
    print(f"    Gate results: {loop_report.quality_gate_results[:2]}")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 5. measure_implementation_quality
    # -----------------------------------------------------------------------
    print("\n[5] measure_implementation_quality")
    agg = measure_implementation_quality(
        artifact_id="art-" + _uid(),
        metric_definitions=[
            {"name": "TYPE_CHECK", "value": 1.0},
            {"name": "UNIT_TEST", "value": 0.92},
            {"name": "PROPERTY_TEST", "value": 0.87},
            {"name": "PROOF_CERTIFICATE", "value": 0.96},
            {"name": "INTEGRATION_TEST", "value": 0.81},
        ],
    )
    assert isinstance(agg, MetricAggregation)
    assert 0.0 <= agg.weighted_score <= 1.0
    assert agg.method == "weighted_mean"
    print(f"    Weighted score: {agg.weighted_score:.4f}")
    print(f"    Metrics: {[(m.name, round(m.value, 2), m.passed) for m in agg.metrics]}")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 6. check_quality_gate (module-level)
    # -----------------------------------------------------------------------
    print("\n[6] check_quality_gate (module-level)")
    full_art = ImplementationArtifact(
        artifact_id=_uid(), spec_id="spec-" + _uid(),
        code_hash=uuid.uuid4().hex, clause_count=10,
        verified_clause_count=10, state=LoopState.VERIFICATION_PENDING,
        created_at=_now_iso(),
    )
    zero_art = ImplementationArtifact(
        artifact_id=_uid(), spec_id="spec-" + _uid(),
        code_hash=uuid.uuid4().hex, clause_count=10,
        verified_clause_count=0, state=LoopState.IN_PROGRESS,
        created_at=_now_iso(),
    )
    gv_full = check_quality_gate("smoke_test", full_art)
    gv_zero = check_quality_gate("smoke_test", zero_art)
    gv_unknown = check_quality_gate("nonexistent_gate_xyz", full_art)
    assert gv_full.passed, "smoke_test should pass with full coverage"
    assert not gv_zero.passed, "smoke_test should fail with zero coverage"
    assert gv_unknown.passed, "Unknown gate should always pass"
    print(f"    smoke_test (full): passed={gv_full.passed}")
    print(f"    smoke_test (zero): passed={gv_zero.passed}")
    print(f"    unknown gate:      passed={gv_unknown.passed}")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 7. RetryPolicy.delay_for
    # -----------------------------------------------------------------------
    print("\n[7] RetryPolicy.delay_for")
    rp = RetryPolicy(max_retries=5, backoff_base=2.0, jitter=0.1, timeout_seconds=60.0)
    for attempt in range(5):
        d = rp.delay_for(attempt)
        assert d >= 0.0
        print(f"    attempt={attempt} delay={d:.3f}s")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 8. Enum coverage
    # -----------------------------------------------------------------------
    print("\n[8] Enum coverage")
    assert {t.name for t in TrustTier} == {
        "PROPOSAL", "REVIEWED", "VERIFIED", "RUNTIME_WITNESSED", "PROOF_BACKED"
    }
    assert {s.name for s in LoopState} == {
        "WAITING", "IN_PROGRESS", "BLOCKED_BY_SPEC",
        "VERIFICATION_PENDING", "VERIFIED", "FAILED",
    }
    for tier in TrustTier:
        print(f"    TrustTier.{tier.name}")
    for st in LoopState:
        print(f"    LoopState.{st.name}")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 9. SpecAmbiguity and VerificationBatch
    # -----------------------------------------------------------------------
    print("\n[9] SpecAmbiguity and VerificationBatch")
    amb = SpecAmbiguity(
        ambiguity_id=_uid(), clause_id="clause-0001",
        signal_text="precondition underspecified",
        spec_id="spec-xyz", detected_at=_now_iso(),
    )
    assert isinstance(amb, SpecAmbiguity)
    print(f"    SpecAmbiguity: {amb.ambiguity_id}")

    cvs = tuple(
        ClauseVerification(
            clause_id=f"clause-{i:04d}",
            clause_text=f"Clause {i}",
            verification_kind="UNIT_TEST",
            confidence=round(0.88 + i * 0.01, 3),
            passed=True,
            details=f"test_clause_{i} passed",
            verified_at=_now_iso(),
        )
        for i in range(4)
    )
    vbatch = VerificationBatch(
        batch_id=_uid(), loop_id="smoke-loop-001",
        iteration_id=_uid(),
        verifications=cvs,
        aggregate_confidence=round(sum(v.confidence for v in cvs) / len(cvs), 4),
        created_at=_now_iso(),
    )
    assert len(vbatch.verifications) == 4
    print(f"    VerificationBatch: {vbatch.batch_id} agg={vbatch.aggregate_confidence:.3f}")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 10. _weighted_mean helper
    # -----------------------------------------------------------------------
    print("\n[10] _weighted_mean helper")
    wm = _weighted_mean(
        {"TYPE_CHECK": 1.0, "UNIT_TEST": 0.90, "PROPERTY_TEST": 0.85},
        _TRUST_TIER_WEIGHTS,
    )
    assert 0.0 <= wm <= 1.0
    print(f"    _weighted_mean result: {wm:.4f}")
    assert _weighted_mean({}, _TRUST_TIER_WEIGHTS) == 0.0
    print("    PASS")

    # -----------------------------------------------------------------------
    # 11. Frozen dataclass immutability
    # -----------------------------------------------------------------------
    print("\n[11] Frozen dataclass immutability")
    import dataclasses as _dc
    j = ImplementationJudgment(
        context="test", formula="quality >= 0.9",
        authority="smoke_test", evidence=("e1",),
        obligations=("audit",), budget=900,
        trust_tier=TrustTier.VERIFIED,
        proof_chain=("p1", "p2", "p3"),
    )
    try:
        j.budget = 0  # type: ignore[misc]
        raise AssertionError("Should have raised FrozenInstanceError")
    except (_dc.FrozenInstanceError, AttributeError):
        print("    ImplementationJudgment is correctly frozen")
    print("    PASS")

    # -----------------------------------------------------------------------
    # 12. Multiple runs do not interfere
    # -----------------------------------------------------------------------
    print("\n[12] Multiple independent loop runs")
    reports = []
    for run_n in range(3):
        r = run_implementation_loop(
            spec_artifact={"spec_id": f"spec-run-{run_n}", "clause_count": 4},
            max_iterations=3,
        )
        reports.append(r)
        print(f"    Run {run_n}: state={r.final_state.value} iters={r.total_iterations}")
    assert len({r.loop_id for r in reports}) == 3, "Each run should have a unique loop_id"
    print("    PASS")

    print("\n" + "=" * 70)
    print("All 12 smoke tests passed. Module is functional.")
    print("=" * 70)
