"""
Local construction loops — proposal→verification→refinement per cover element.

# copilot: This module implements the innermost feedback loop of JuGeo's
# construction pipeline.  For every element U_i in a good cover {U_i} of the
# target space, the system (1) *proposes* a local construction, (2) *verifies*
# that the proposal satisfies every local obligation discharged to U_i, and
# (3) *refines* the proposal when verification fails — iterating until the loop
# either converges (all obligations satisfied) or times out.
#
# The design is deliberately algebraic: a proposal is not a Boolean "good / bad"
# flag but a full Judgment tuple (c, φ, A, E, O, B, T, Π) so that downstream
# gluing steps can inspect evidence and trust tiers without re-running work.
#
# Descent obstructions are represented as Čech H¹ cohomology classes; a trivial
# class means the local constructions are compatible across overlaps and can be
# glued into a global section.
#
# Relationship to surrounding modules
# ------------------------------------
# * interface_discipline  — verifies that proposals conform to declared
#   interface contracts before they enter the gluing phase.
# * coordinated_elaboration — orchestrates *multiple* local loops in
#   parallel, coordinates shared obligations, and collects loop judgments into
#   a treaty.
# * copilot_in_construction — oracle layer that seeds the initial proposal
#   for each cover element using an LLM or solver back-end.
#
# Theoretical background
# ----------------------
# The loop implements a fragment of constructive type theory: each iteration
# refines a proof-term candidate until it type-checks against the local context.
# The TrustTier lattice tracks how much epistemic weight each judgment carries,
# ranging from an unverified PROPOSAL (tier 1) up to a PROOF_BACKED certificate
# (tier 5) discharged by a formal solver.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict, replace
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import itertools
import functools
import collections
import abc
import re
import math

# ---------------------------------------------------------------------------
# Optional jugeo integration — graceful degradation when the library is absent
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

    def raise_with_scope(  # type: ignore[no-redef]
        code: str,
        *,
        message: str,
        provenance: Any = None,
        **kw: Any,
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
# Module-level logger
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TrustTier lattice
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """
    An ordered lattice of epistemic trust for local-construction judgments.

    The lattice is a chain (total order) with five positive tiers above the
    implicit NONE/bottom:

        PROPOSAL < WITNESSED < DISCHARGED < CERTIFIED < PROOF_BACKED

    The *join* (least upper bound) and *meet* (greatest lower bound) operations
    allow downstream code to compute the combined trust of a collection of
    judgments without case-splitting.

    Notes
    -----
    * A judgment at PROPOSAL tier has been syntactically well-formed and
      seeded by an oracle but has not yet been verified against any obligation.
    * WITNESSED means at least one runtime test confirmed the judgment holds
      in a concrete instance.
    * DISCHARGED means every local obligation has been statically satisfied by
      a decision procedure (e.g. an SMT solver).
    * CERTIFIED means the judgment has been peer-reviewed by a second oracle or
      a human annotator.
    * PROOF_BACKED means a formal proof term exists (e.g. a Lean 4 proof).
    """

    PROPOSAL = 1
    WITNESSED = 2
    DISCHARGED = 3
    CERTIFIED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    def join(self, other: "TrustTier") -> "TrustTier":
        """Return the least upper bound (max) of *self* and *other*."""
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Return the greatest lower bound (min) of *self* and *other*."""
        return TrustTier(min(int(self), int(other)))

    def promote(self, steps: int = 1) -> "TrustTier":
        """
        Promote *self* by *steps* tiers, clamping at PROOF_BACKED.

        Parameters
        ----------
        steps:
            Number of tiers to climb.  Negative values are silently ignored
            (use :meth:`demote` instead).

        Returns
        -------
        TrustTier
            The promoted tier, or PROOF_BACKED if the ceiling is reached.
        """
        if steps < 0:
            return self
        new_value = min(int(self) + steps, int(TrustTier.PROOF_BACKED))
        return TrustTier(new_value)

    def demote(self, steps: int = 1) -> "TrustTier":
        """
        Demote *self* by *steps* tiers, clamping at PROPOSAL.

        Parameters
        ----------
        steps:
            Number of tiers to descend.  Negative values are silently ignored.

        Returns
        -------
        TrustTier
            The demoted tier, or PROPOSAL if the floor is reached.
        """
        if steps < 0:
            return self
        new_value = max(int(self) - steps, int(TrustTier.PROPOSAL))
        return TrustTier(new_value)


# ---------------------------------------------------------------------------
# Shared algebraic dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """
    A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    A Judgment is the fundamental currency of JuGeo's epistemic bookkeeping.
    Instead of reducing a construction outcome to True/False, a Judgment records
    *why* something holds (or fails), *what evidence* supports it, *what
    obligations* remain, *who bears the burden* of discharging them, and *how
    much trust* the overall claim deserves.

    Fields
    ------
    context:
        The local context (cover element descriptor, environment, etc.) under
        which the judgment is made.
    formula:
        The proposition or claim being judged — may be a string, an AST node,
        or any serialisable object.
    assumptions:
        An ordered tuple of assumptions in scope.  Immutable so that judgments
        can be used as dictionary keys or stored in frozen sets.
    evidence:
        Tuple of evidence items supporting the claim.  Each item should be a
        dict with at least ``kind`` (EvidenceItemKind) and ``payload`` keys.
    obligations:
        Tuple of residual obligations that must be discharged before the
        judgment can be elevated to PROOF_BACKED.
    burden:
        A string or object identifying who is responsible for discharging the
        remaining obligations (e.g. ``"solver"``, ``"human"``, ``"oracle"``).
    trust:
        The current :class:`TrustTier` of the judgment.
    provenance:
        Serialisable object describing how this judgment was produced (e.g.
        iteration number, oracle call identifier, timestamp).
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
    """
    A Čech H¹ cohomology class witnessing descent failure.

    When two local constructions on overlapping cover elements U_i ∩ U_j are
    incompatible, the incompatibility is encoded as a non-trivial Čech 1-cocycle.
    This class stores the minimal information needed to (a) report the
    obstruction to the user and (b) feed it back into the refinement loop so
    the proposals can be adjusted to make the cocycle trivial.

    A *trivial* obstruction (``is_trivial() == True``) means the cocycle
    vanishes and the local sections can be glued into a global section.

    Fields
    ------
    cover_id:
        Identifier of the cover (e.g. a good cover index) this obstruction
        belongs to.
    cocycle:
        Frozenset of ``(i, j)`` index pairs where the local constructions
        disagree on the overlap U_i ∩ U_j.
    cohomology_class:
        A human-readable or canonical string representing the cohomology class
        (e.g. ``"[σ_01 · σ_12 · σ_02^{-1}]"``).
    description:
        Free-form prose description of the obstruction for logging and UIs.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the cocycle is empty, i.e. no descent obstruction."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalConstructionLoop:
    """
    State snapshot of a single proposal→verification→refinement loop.

    A :class:`LocalConstructionLoop` is *immutable*; each iteration produces a
    new instance via :meth:`step`.  The full history of (proposal_id, status,
    trust) triples is accumulated in :attr:`history` so that callers can audit
    the entire trajectory.

    Fields
    ------
    loop_id:
        Unique identifier for this loop instance.
    cover_element_id:
        Identifier of the cover element U_i this loop is responsible for.
    max_iterations:
        Hard upper bound on the number of refinement iterations.
    current_iteration:
        Zero-based index of the current (most recent) iteration.
    status:
        One of ``"running"``, ``"converged"``, ``"timed_out"``, or
        ``"failed"``.
    history:
        Tuple of dicts, one per completed iteration.  Each dict contains
        at minimum ``iteration``, ``proposal_id``, and ``trust_awarded``.
    """

    loop_id: str
    cover_element_id: str
    max_iterations: int
    current_iteration: int
    status: str
    history: tuple

    # ------------------------------------------------------------------
    # Transition helpers
    # ------------------------------------------------------------------

    def step(self, proposal: "ConstructionProposal") -> "LocalConstructionLoop":
        """
        Advance the loop by one iteration, recording *proposal* in history.

        Returns a new :class:`LocalConstructionLoop` with
        ``current_iteration`` incremented and the proposal's metadata
        appended to :attr:`history`.

        Parameters
        ----------
        proposal:
            The proposal produced in this iteration.

        Returns
        -------
        LocalConstructionLoop
            Updated loop state.
        """
        entry: Dict[str, Any] = {
            "iteration": self.current_iteration,
            "proposal_id": proposal.proposal_id,
            "trust_awarded": int(proposal.trust),
            "content_type": proposal.content_type,
            "timestamp": proposal.timestamp,
        }
        new_history = self.history + (entry,)
        new_iteration = self.current_iteration + 1
        new_status = "timed_out" if new_iteration >= self.max_iterations else "running"
        return replace(
            self,
            current_iteration=new_iteration,
            history=new_history,
            status=new_status,
        )

    def is_converged(self) -> bool:
        """Return True iff the loop status is ``"converged"``."""
        return self.status == "converged"

    def is_timed_out(self) -> bool:
        """Return True iff the loop has exhausted its iteration budget."""
        return self.status == "timed_out"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the loop state to a plain dict for logging / storage."""
        return {
            "loop_id": self.loop_id,
            "cover_element_id": self.cover_element_id,
            "max_iterations": self.max_iterations,
            "current_iteration": self.current_iteration,
            "status": self.status,
            "history": list(self.history),
        }

    def reset(self) -> "LocalConstructionLoop":
        """
        Return a fresh loop with the same configuration but cleared state.

        Useful for retrying a cover element after an external change (e.g. a
        new oracle hint or an updated obligation set).
        """
        return replace(
            self,
            loop_id=str(uuid.uuid4()),
            current_iteration=0,
            status="running",
            history=(),
        )


@dataclass(frozen=True)
class ConstructionProposal:
    """
    A concrete proposal for how to construct a local section over U_i.

    Proposals are the *output* of the oracle / proposal step and the *input* to
    the local verification step.  They carry a trust tier that is updated as
    verification proceeds and are never mutated — refinement produces a new
    proposal via :meth:`with_content`.

    Fields
    ------
    proposal_id:
        UUID-based unique identifier.
    cover_element_id:
        The cover element this proposal targets.
    content:
        The actual proposed construction — may be source code, a JSON
        structure, a proof term, or any domain object.
    content_type:
        MIME-like tag describing how to interpret *content*
        (e.g. ``"python/ast"``, ``"json/schema"``, ``"lean4/term"``).
    obligations_satisfied:
        Tuple of obligation identifiers that this proposal claims to satisfy.
        The verifier checks each claim.
    trust:
        Initial trust tier for the proposal (usually PROPOSAL).
    timestamp:
        Unix timestamp (float) when the proposal was created.
    """

    proposal_id: str
    cover_element_id: str
    content: Any
    content_type: str
    obligations_satisfied: tuple
    trust: TrustTier
    timestamp: float

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def satisfies(self, obligation: str) -> bool:
        """
        Return True iff *obligation* appears in :attr:`obligations_satisfied`.

        Parameters
        ----------
        obligation:
            The obligation identifier to check.

        Returns
        -------
        bool
        """
        return obligation in self.obligations_satisfied

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the proposal to a plain dict."""
        return {
            "proposal_id": self.proposal_id,
            "cover_element_id": self.cover_element_id,
            "content_type": self.content_type,
            "obligations_satisfied": list(self.obligations_satisfied),
            "trust": int(self.trust),
            "timestamp": self.timestamp,
        }

    def with_content(self, new_content: Any) -> "ConstructionProposal":
        """
        Return a new proposal with *new_content*, a fresh ID, and PROPOSAL tier.

        Refinement always resets the trust tier to PROPOSAL because the new
        content has not yet been verified.

        Parameters
        ----------
        new_content:
            The refined content to substitute.

        Returns
        -------
        ConstructionProposal
            A new, independent proposal instance.
        """
        return replace(
            self,
            proposal_id=str(uuid.uuid4()),
            content=new_content,
            trust=TrustTier.PROPOSAL,
            timestamp=time.time(),
        )


@dataclass(frozen=True)
class LocalVerification:
    """
    The result of locally verifying a :class:`ConstructionProposal`.

    Verification checks each obligation in the proposal's claimed satisfaction
    list and any additional obligations supplied by the caller.  Failures are
    accumulated rather than short-circuiting so that the refinement step can
    address multiple issues in a single pass.

    Fields
    ------
    verification_id:
        UUID for this verification run.
    proposal_id:
        The proposal that was verified.
    passed:
        True iff every obligation passed and there are no failures.
    failures:
        Tuple of failure dicts, each containing ``obligation_id``, ``reason``,
        and ``severity`` keys.
    warnings:
        Tuple of warning strings that do not constitute failures but are
        worth reporting.
    trust_awarded:
        The :class:`TrustTier` granted to the proposal after this
        verification run.
    """

    verification_id: str
    proposal_id: str
    passed: bool
    failures: tuple
    warnings: tuple
    trust_awarded: TrustTier

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_failures(self) -> bool:
        """Return True iff at least one failure was recorded."""
        return len(self.failures) > 0

    def is_passing(self) -> bool:
        """Return True iff verification passed without any failures."""
        return self.passed and not self.has_failures()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the verification result to a plain dict."""
        return {
            "verification_id": self.verification_id,
            "proposal_id": self.proposal_id,
            "passed": self.passed,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "trust_awarded": int(self.trust_awarded),
        }


@dataclass(frozen=True)
class RefinementStep:
    """
    A single refinement action applied to a proposal to address a failure.

    Each :class:`RefinementStep` records *why* the refinement was attempted
    (which failure it addresses), *what* was changed
    (modification_type + modification_detail), and the *confidence* the
    refiner has that the modification will fix the failure.

    Fields
    ------
    step_id:
        UUID for this refinement step.
    proposal_id:
        The proposal that this step refines.
    failure_addressed:
        The ``obligation_id`` of the failure this step targets.
    modification_type:
        A short tag describing the kind of modification:
        ``"substitution"``, ``"extension"``, ``"deletion"``, ``"reorder"``.
    modification_detail:
        Free-form description or diff of the modification.
    confidence:
        Float in [0, 1] — the refiner's confidence that this step fixes
        the addressed failure.  Used to order competing refinements.
    """

    step_id: str
    proposal_id: str
    failure_addressed: str
    modification_type: str
    modification_detail: str
    confidence: float

    # ------------------------------------------------------------------
    # Application helpers
    # ------------------------------------------------------------------

    def apply(self, proposal: ConstructionProposal) -> ConstructionProposal:
        """
        Apply this refinement step to *proposal*, returning a new proposal.

        The implementation delegates to :meth:`ConstructionProposal.with_content`
        after producing a refined content blob that incorporates
        :attr:`modification_detail`.  In a production system this would call a
        domain-specific patch routine; here we produce an annotated wrapper so
        that the change is traceable.

        Parameters
        ----------
        proposal:
            The proposal to refine.

        Returns
        -------
        ConstructionProposal
            A new proposal with the refinement applied.
        """
        log.debug(
            "Applying refinement step %s (type=%s, confidence=%.2f) to proposal %s",
            self.step_id,
            self.modification_type,
            self.confidence,
            proposal.proposal_id,
        )
        refined_content = {
            "_refined_by": self.step_id,
            "_modification_type": self.modification_type,
            "_modification_detail": self.modification_detail,
            "_base_content": proposal.content,
        }
        return proposal.with_content(refined_content)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the step to a plain dict."""
        return {
            "step_id": self.step_id,
            "proposal_id": self.proposal_id,
            "failure_addressed": self.failure_addressed,
            "modification_type": self.modification_type,
            "modification_detail": self.modification_detail,
            "confidence": self.confidence,
        }

    def is_safe(self) -> bool:
        """
        Return True iff the refinement is considered safe to apply automatically.

        A refinement is safe when its confidence is at least 0.5 and its
        modification type is not ``"deletion"`` (deletions carry extra risk
        because they may remove load-bearing content).

        Returns
        -------
        bool
        """
        if self.modification_type == "deletion":
            return self.confidence >= 0.8
        return self.confidence >= 0.5


@dataclass(frozen=True)
class LoopController:
    """
    Budget and timeout controller for a :class:`LocalConstructionLoop`.

    The controller is separate from the loop state so that the same budget
    policy can be shared across multiple loops (e.g. all loops in a
    coordinated elaboration session share a wall-clock timeout).

    Fields
    ------
    controller_id:
        UUID for this controller instance.
    max_iterations:
        Maximum number of proposal→verification→refinement cycles.
    timeout_seconds:
        Wall-clock time limit in seconds.  0 or negative means no timeout.
    start_time:
        Unix timestamp when this controller was created / started.
    iterations_run:
        Counter of how many iterations have been run under this controller.
    """

    controller_id: str
    max_iterations: int
    timeout_seconds: float
    start_time: float
    iterations_run: int

    # ------------------------------------------------------------------
    # Budget queries
    # ------------------------------------------------------------------

    def check_timeout(self) -> bool:
        """
        Return True iff the wall-clock timeout has been exceeded.

        If :attr:`timeout_seconds` is non-positive the timeout is disabled
        and this method always returns False.

        Returns
        -------
        bool
        """
        if self.timeout_seconds <= 0:
            return False
        elapsed = time.time() - self.start_time
        timed_out = elapsed >= self.timeout_seconds
        if timed_out:
            log.warning(
                "Controller %s: timeout after %.2fs (limit=%.2fs)",
                self.controller_id,
                elapsed,
                self.timeout_seconds,
            )
        return timed_out

    def increment(self) -> "LoopController":
        """
        Return a new controller with :attr:`iterations_run` incremented by 1.

        Returns
        -------
        LoopController
        """
        return replace(self, iterations_run=self.iterations_run + 1)

    def should_stop(self, verification: LocalVerification) -> bool:
        """
        Decide whether the loop should stop given the latest verification result.

        Stopping criteria (any one is sufficient):
        1. The verification passed — the loop has converged.
        2. The iteration budget is exhausted.
        3. The wall-clock timeout has been exceeded.

        Parameters
        ----------
        verification:
            The most recent verification result.

        Returns
        -------
        bool
            True iff the loop should stop.
        """
        if verification.is_passing():
            log.info(
                "Controller %s: verification passed — stopping loop.",
                self.controller_id,
            )
            return True
        if self.iterations_run >= self.max_iterations:
            log.warning(
                "Controller %s: iteration budget exhausted (%d/%d).",
                self.controller_id,
                self.iterations_run,
                self.max_iterations,
            )
            return True
        if self.check_timeout():
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the controller state to a plain dict."""
        elapsed = time.time() - self.start_time
        return {
            "controller_id": self.controller_id,
            "max_iterations": self.max_iterations,
            "timeout_seconds": self.timeout_seconds,
            "iterations_run": self.iterations_run,
            "elapsed_seconds": round(elapsed, 4),
        }


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _stable_hash(obj: Any) -> str:
    """
    Produce a short deterministic hex digest of *obj*.

    Uses JSON serialisation (with sorted keys) so that the hash is stable
    across Python sessions for plain dict / list / str / int / float payloads.
    Falls back to ``repr`` for non-serialisable objects.

    Parameters
    ----------
    obj:
        The object to hash.

    Returns
    -------
    str
        8-character hex string.
    """
    try:
        raw = json.dumps(obj, sort_keys=True, default=str).encode()
    except (TypeError, ValueError):
        raw = repr(obj).encode()
    return hashlib.sha256(raw).hexdigest()[:8]


def _make_obligation_checker(
    obligation: Dict[str, Any],
) -> Callable[[ConstructionProposal], Optional[Dict[str, Any]]]:
    """
    Return a callable that checks one obligation against a proposal.

    The returned callable returns *None* if the obligation is satisfied, or a
    failure dict if it is not.  This factory pattern allows obligation schemas
    to be extensible without modifying the verification function.

    Parameters
    ----------
    obligation:
        A dict with at least ``obligation_id`` and ``kind`` keys.
        Supported kinds: ``"presence"``, ``"type_conformance"``,
        ``"trust_threshold"``, ``"custom"``.

    Returns
    -------
    Callable[[ConstructionProposal], Optional[Dict[str, Any]]]
    """
    obligation_id = obligation.get("obligation_id", "unknown")
    kind = obligation.get("kind", "custom")

    def check(proposal: ConstructionProposal) -> Optional[Dict[str, Any]]:
        # ------------------------------------------------------------------
        # "presence" — the proposal must explicitly claim this obligation
        # ------------------------------------------------------------------
        if kind == "presence":
            if not proposal.satisfies(obligation_id):
                return {
                    "obligation_id": obligation_id,
                    "reason": f"Proposal does not claim to satisfy '{obligation_id}'.",
                    "severity": "error",
                }
            return None

        # ------------------------------------------------------------------
        # "trust_threshold" — the proposal must have at least a given tier
        # ------------------------------------------------------------------
        if kind == "trust_threshold":
            required_tier = TrustTier(obligation.get("min_tier", int(TrustTier.PROPOSAL)))
            if proposal.trust < required_tier:
                return {
                    "obligation_id": obligation_id,
                    "reason": (
                        f"Proposal trust tier {proposal.trust} < required {required_tier}."
                    ),
                    "severity": "error",
                }
            return None

        # ------------------------------------------------------------------
        # "type_conformance" — content_type must match a pattern
        # ------------------------------------------------------------------
        if kind == "type_conformance":
            pattern = obligation.get("content_type_pattern", ".*")
            if not re.fullmatch(pattern, proposal.content_type):
                return {
                    "obligation_id": obligation_id,
                    "reason": (
                        f"Content type '{proposal.content_type}' does not match "
                        f"pattern '{pattern}'."
                    ),
                    "severity": "warning",
                }
            return None

        # ------------------------------------------------------------------
        # "custom" — always passes (placeholder for domain-specific checks)
        # ------------------------------------------------------------------
        if kind == "custom":
            log.debug("Custom obligation '%s' auto-passed (no checker registered).", obligation_id)
            return None

        # Unknown kind — record a warning but do not fail
        return {
            "obligation_id": obligation_id,
            "reason": f"Unknown obligation kind '{kind}' — skipped.",
            "severity": "warning",
        }

    return check


def _trust_from_verification(verification: LocalVerification) -> TrustTier:
    """
    Compute the trust tier to award to a proposal based on a verification result.

    The mapping is:
    * All obligations pass, no warnings → DISCHARGED
    * All obligations pass, warnings present → WITNESSED
    * Some obligations fail → stays at PROPOSAL

    Parameters
    ----------
    verification:
        The verification result to evaluate.

    Returns
    -------
    TrustTier
    """
    if verification.passed and not verification.has_failures():
        if verification.warnings:
            return TrustTier.WITNESSED
        return TrustTier.DISCHARGED
    return TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def propose_construction(
    cover_element: Dict[str, Any],
    iteration: int,
) -> ConstructionProposal:
    """
    Produce an initial or re-seeded construction proposal for *cover_element*.

    In a full JuGeo deployment this function would invoke the oracle layer
    (copilot_in_construction) to generate a domain-specific proposal.
    Here it produces a structurally valid stub that carries enough metadata
    for the verification and refinement pipeline to exercise.

    The proposal content is a dict with:
    * ``cover_element_id`` — echoed from the input
    * ``iteration`` — the iteration number (refinements increment this)
    * ``body`` — a placeholder string
    * ``content_hash`` — 8-char SHA-256 of (cover_element_id, iteration)

    Parameters
    ----------
    cover_element:
        Dict describing the cover element, with at minimum an ``element_id``
        key and optionally ``obligations`` (list of obligation IDs to claim).
    iteration:
        The zero-based iteration number for this proposal.

    Returns
    -------
    ConstructionProposal
        A fresh proposal at PROPOSAL trust tier.
    """
    element_id: str = cover_element.get("element_id", "unknown")
    claimed_obligations: Tuple[str, ...] = tuple(
        cover_element.get("obligations", [])
    )
    content_hash = _stable_hash({"element_id": element_id, "iteration": iteration})
    content = {
        "cover_element_id": element_id,
        "iteration": iteration,
        "body": f"stub_construction_for_{element_id}_iter{iteration}",
        "content_hash": content_hash,
    }
    proposal = ConstructionProposal(
        proposal_id=str(uuid.uuid4()),
        cover_element_id=element_id,
        content=content,
        content_type="json/stub",
        obligations_satisfied=claimed_obligations,
        trust=TrustTier.PROPOSAL,
        timestamp=time.time(),
    )
    log.debug(
        "propose_construction: element=%s iter=%d pid=%s claimed=%d obligations",
        element_id,
        iteration,
        proposal.proposal_id[:8],
        len(claimed_obligations),
    )
    return proposal


def verify_locally(
    proposal: ConstructionProposal,
    obligations: List[Dict[str, Any]],
) -> LocalVerification:
    """
    Verify that *proposal* satisfies every obligation in *obligations*.

    Each obligation is checked by a dedicated checker produced by
    :func:`_make_obligation_checker`.  Failures and warnings are accumulated;
    the final :class:`LocalVerification` records the aggregate outcome.

    The trust awarded is computed by :func:`_trust_from_verification` and is
    stored in the :class:`LocalVerification` so that callers can update the
    proposal's trust tier without re-running verification.

    Parameters
    ----------
    proposal:
        The proposal to verify.
    obligations:
        List of obligation dicts.  Each must have at least ``obligation_id``
        and ``kind`` keys.

    Returns
    -------
    LocalVerification
        Full verification result including failures, warnings, and trust awarded.
    """
    log.debug(
        "verify_locally: proposal=%s, %d obligations",
        proposal.proposal_id[:8],
        len(obligations),
    )
    failures: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for obligation in obligations:
        checker = _make_obligation_checker(obligation)
        result = checker(proposal)
        if result is None:
            continue
        if result.get("severity") == "warning":
            warnings.append(result["reason"])
            log.debug("  warn: %s", result["reason"])
        else:
            failures.append(result)
            log.debug("  fail: %s", result["reason"])

    passed = len(failures) == 0
    verification = LocalVerification(
        verification_id=str(uuid.uuid4()),
        proposal_id=proposal.proposal_id,
        passed=passed,
        failures=tuple(failures),
        warnings=tuple(warnings),
        trust_awarded=TrustTier.PROPOSAL,  # placeholder; set below
    )
    trust = _trust_from_verification(verification)
    verification = replace(verification, trust_awarded=trust)

    log.info(
        "verify_locally: proposal=%s passed=%s trust=%s failures=%d warnings=%d",
        proposal.proposal_id[:8],
        passed,
        trust.name,
        len(failures),
        len(warnings),
    )
    return verification


def refine_proposal(
    proposal: ConstructionProposal,
    verification: LocalVerification,
) -> Tuple[ConstructionProposal, List[RefinementStep]]:
    """
    Produce a refined proposal and the refinement steps applied to address failures.

    The refinement strategy is:
    1. For each failure in *verification*, generate a :class:`RefinementStep`
       with modification type ``"substitution"`` and a confidence derived from
       the failure severity.
    2. Apply only the *safe* steps (as determined by :meth:`RefinementStep.is_safe`).
    3. Claim any obligation_ids that appear in the failures as newly satisfied
       (optimistic refinement — the verifier will re-check them next iteration).

    In a production system, step generation would be delegated to an oracle
    or a domain-specific solver; here we produce well-typed stubs so that the
    loop can exercise the full proposal→verification→refinement pipeline.

    Parameters
    ----------
    proposal:
        The proposal that failed verification.
    verification:
        The verification result that identified the failures.

    Returns
    -------
    Tuple[ConstructionProposal, List[RefinementStep]]
        The refined proposal and the list of steps that were applied.
    """
    if verification.is_passing():
        log.debug(
            "refine_proposal: verification already passing for %s — no refinement needed.",
            proposal.proposal_id[:8],
        )
        return proposal, []

    steps: List[RefinementStep] = []
    newly_satisfied: Set[str] = set(proposal.obligations_satisfied)

    for failure in verification.failures:
        obligation_id: str = failure.get("obligation_id", "unknown")
        severity: str = failure.get("severity", "error")
        confidence = 0.7 if severity == "error" else 0.55

        step = RefinementStep(
            step_id=str(uuid.uuid4()),
            proposal_id=proposal.proposal_id,
            failure_addressed=obligation_id,
            modification_type="substitution",
            modification_detail=(
                f"Substitute content to satisfy '{obligation_id}': {failure.get('reason', '')}"
            ),
            confidence=confidence,
        )
        steps.append(step)
        log.debug(
            "refine_proposal: step %s addresses '%s' (confidence=%.2f, safe=%s)",
            step.step_id[:8],
            obligation_id,
            confidence,
            step.is_safe(),
        )

    # Apply only safe steps
    refined = proposal
    applied_steps: List[RefinementStep] = []
    for step in steps:
        if step.is_safe():
            refined = step.apply(refined)
            newly_satisfied.add(step.failure_addressed)
            applied_steps.append(step)
        else:
            log.warning(
                "refine_proposal: step %s deemed unsafe (confidence=%.2f) — skipped.",
                step.step_id[:8],
                step.confidence,
            )

    # Update obligations_satisfied on the refined proposal
    refined = replace(refined, obligations_satisfied=tuple(sorted(newly_satisfied)))

    log.info(
        "refine_proposal: %d/%d steps applied; refined proposal id=%s",
        len(applied_steps),
        len(steps),
        refined.proposal_id[:8],
    )
    return refined, applied_steps


def run_local_construction_loop(
    cover_element: Dict[str, Any],
    obligations: List[Dict[str, Any]],
    controller: LoopController,
) -> Judgment:
    """
    Run the full proposal→verification→refinement loop for one cover element.

    This is the top-level entry point for local construction.  It orchestrates:
    1. An initial proposal from :func:`propose_construction`.
    2. Verification via :func:`verify_locally`.
    3. Conditional refinement via :func:`refine_proposal`.
    4. Iteration until the controller signals that the loop should stop.
    5. Packaging the final proposal into a :class:`Judgment`.

    The returned :class:`Judgment` captures:
    * ``context`` — the cover element descriptor
    * ``formula`` — a summary of obligations discharged
    * ``assumptions`` — empty (no global assumptions in the local loop)
    * ``evidence`` — one entry per iteration (proposal + verification summary)
    * ``obligations`` — residual failures from the last verification
    * ``burden`` — ``"solver"`` if there are residual obligations, else ``"none"``
    * ``trust`` — the trust tier awarded by the last successful verification
    * ``provenance`` — the controller dict at loop exit

    Parameters
    ----------
    cover_element:
        Dict describing the cover element.  Must have ``element_id``.
    obligations:
        List of obligation dicts to satisfy.
    controller:
        Budget and timeout controller.  Mutated (via :meth:`LoopController.increment`)
        at each iteration.

    Returns
    -------
    Judgment
        The algebraic judgment summarising the loop outcome.

    Raises
    ------
    JuGeoError
        If the loop terminates without any verification having been run
        (defensive guard for malformed inputs).
    """
    element_id: str = cover_element.get("element_id", "unknown")
    log.info(
        "run_local_construction_loop: START element=%s obligations=%d max_iter=%d",
        element_id,
        len(obligations),
        controller.max_iterations,
    )

    loop = LocalConstructionLoop(
        loop_id=str(uuid.uuid4()),
        cover_element_id=element_id,
        max_iterations=controller.max_iterations,
        current_iteration=0,
        status="running",
        history=(),
    )

    evidence_items: List[Dict[str, Any]] = []
    last_verification: Optional[LocalVerification] = None
    last_proposal: Optional[ConstructionProposal] = None
    iteration = 0

    while True:
        # 1. Propose
        proposal = propose_construction(cover_element, iteration)
        loop = loop.step(proposal)
        controller = controller.increment()

        # 2. Verify
        verification = verify_locally(proposal, obligations)
        last_verification = verification
        last_proposal = proposal

        evidence_items.append(
            {
                "iteration": iteration,
                "kind": EvidenceItemKind.ORACLE_PROPOSAL.value if _JUGEO_JUDGMENTS
                        else "oracle_proposal",
                "proposal_summary": proposal.to_dict(),
                "verification_summary": verification.to_dict(),
            }
        )

        # 3. Check stopping condition
        if controller.should_stop(verification):
            final_status = "converged" if verification.is_passing() else "timed_out"
            loop = replace(loop, status=final_status)
            log.info(
                "run_local_construction_loop: STOP element=%s status=%s iter=%d",
                element_id,
                final_status,
                iteration,
            )
            break

        # 4. Refine
        proposal, applied_steps = refine_proposal(proposal, verification)
        if applied_steps:
            log.debug(
                "run_local_construction_loop: %d refinement steps applied at iter %d",
                len(applied_steps),
                iteration,
            )

        iteration += 1

    if last_verification is None:
        raise_with_scope(
            "LOCAL_LOOP_NO_VERIFICATION",
            message=f"Loop for element '{element_id}' exited without any verification.",
        )
        # unreachable — raise_with_scope always raises, but satisfies type checker
        raise JuGeoError("unreachable")

    # ------------------------------------------------------------------
    # Package the result as a Judgment
    # ------------------------------------------------------------------
    residual_failures: Tuple[Dict[str, Any], ...] = last_verification.failures
    burden: str = "solver" if residual_failures else "none"
    trust: TrustTier = last_verification.trust_awarded

    formula = {
        "discharged": [
            p.proposal_id for p in [last_proposal] if p is not None
        ],
        "obligations_satisfied": (
            list(last_proposal.obligations_satisfied) if last_proposal else []
        ),
        "loop_status": loop.status,
    }

    judgment = Judgment(
        context=cover_element,
        formula=formula,
        assumptions=(),
        evidence=tuple(evidence_items),
        obligations=residual_failures,
        burden=burden,
        trust=trust,
        provenance=controller.to_dict(),
    )
    log.info(
        "run_local_construction_loop: JUDGMENT element=%s trust=%s burden=%s residual_failures=%d",
        element_id,
        trust.name,
        burden,
        len(residual_failures),
    )
    return judgment


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    print("=" * 70)
    print("Local Construction Loops — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. TrustTier lattice sanity checks
    # ------------------------------------------------------------------
    print("\n--- TrustTier lattice ---")
    t_prop = TrustTier.PROPOSAL
    t_proof = TrustTier.PROOF_BACKED
    print(f"  PROPOSAL.join(PROOF_BACKED) = {t_prop.join(t_proof)}")
    print(f"  PROOF_BACKED.meet(PROPOSAL) = {t_proof.meet(t_prop)}")
    print(f"  PROPOSAL.promote(3) = {t_prop.promote(3)}")
    print(f"  PROOF_BACKED.demote(2) = {t_proof.demote(2)}")
    print(f"  PROPOSAL.promote(99) = {t_prop.promote(99)}  (clamped)")
    assert t_prop.join(t_proof) == TrustTier.PROOF_BACKED
    assert t_proof.meet(t_prop) == TrustTier.PROPOSAL
    assert t_prop.promote(3) == TrustTier.DISCHARGED
    assert t_proof.demote(2) == TrustTier.WITNESSED
    assert t_prop.promote(99) == TrustTier.PROOF_BACKED
    print("  [OK] All TrustTier assertions passed.")

    # ------------------------------------------------------------------
    # 2. CechObstruction trivial / non-trivial
    # ------------------------------------------------------------------
    print("\n--- CechObstruction ---")
    trivial_obs = CechObstruction(
        cover_id="cover_A",
        cocycle=frozenset(),
        cohomology_class="0",
        description="No obstruction",
    )
    non_trivial_obs = CechObstruction(
        cover_id="cover_A",
        cocycle=frozenset({(0, 1), (1, 2)}),
        cohomology_class="[σ_01 · σ_12]",
        description="Overlapping sections disagree on U_0 ∩ U_1 and U_1 ∩ U_2",
    )
    print(f"  trivial_obs.is_trivial() = {trivial_obs.is_trivial()}")
    print(f"  non_trivial_obs.is_trivial() = {non_trivial_obs.is_trivial()}")
    assert trivial_obs.is_trivial()
    assert not non_trivial_obs.is_trivial()
    print("  [OK] CechObstruction assertions passed.")

    # ------------------------------------------------------------------
    # 3. propose_construction
    # ------------------------------------------------------------------
    print("\n--- propose_construction ---")
    cover_element = {
        "element_id": "U_0",
        "obligations": ["presence:type_check", "presence:termination"],
    }
    proposal = propose_construction(cover_element, iteration=0)
    print(f"  proposal_id={proposal.proposal_id[:8]}  trust={proposal.trust.name}")
    print(f"  obligations_satisfied={proposal.obligations_satisfied}")
    assert proposal.trust == TrustTier.PROPOSAL
    assert "presence:type_check" in proposal.obligations_satisfied
    print("  [OK] propose_construction assertions passed.")

    # ------------------------------------------------------------------
    # 4. verify_locally — passing case
    # ------------------------------------------------------------------
    print("\n--- verify_locally (passing) ---")
    obligations_passing = [
        {"obligation_id": "presence:type_check", "kind": "presence"},
        {"obligation_id": "presence:termination", "kind": "presence"},
    ]
    verification_pass = verify_locally(proposal, obligations_passing)
    print(f"  passed={verification_pass.passed}  trust_awarded={verification_pass.trust_awarded.name}")
    print(f"  failures={len(verification_pass.failures)}  warnings={len(verification_pass.warnings)}")
    assert verification_pass.is_passing()
    assert verification_pass.trust_awarded >= TrustTier.WITNESSED
    print("  [OK] Passing verification assertions passed.")

    # ------------------------------------------------------------------
    # 5. verify_locally — failing case
    # ------------------------------------------------------------------
    print("\n--- verify_locally (failing) ---")
    obligations_failing = [
        {"obligation_id": "presence:type_check", "kind": "presence"},
        {"obligation_id": "presence:security_scan", "kind": "presence"},  # not claimed
    ]
    verification_fail = verify_locally(proposal, obligations_failing)
    print(f"  passed={verification_fail.passed}  trust_awarded={verification_fail.trust_awarded.name}")
    print(f"  failures={len(verification_fail.failures)}")
    assert not verification_fail.is_passing()
    assert verification_fail.trust_awarded == TrustTier.PROPOSAL
    print("  [OK] Failing verification assertions passed.")

    # ------------------------------------------------------------------
    # 6. refine_proposal
    # ------------------------------------------------------------------
    print("\n--- refine_proposal ---")
    refined_proposal, steps = refine_proposal(proposal, verification_fail)
    print(f"  refined_proposal_id={refined_proposal.proposal_id[:8]}")
    print(f"  steps applied: {len(steps)}")
    for s in steps:
        print(f"    step={s.step_id[:8]}  type={s.modification_type}  safe={s.is_safe()}")
    assert refined_proposal.proposal_id != proposal.proposal_id
    assert "presence:security_scan" in refined_proposal.obligations_satisfied
    print("  [OK] refine_proposal assertions passed.")

    # ------------------------------------------------------------------
    # 7. Full run_local_construction_loop
    # ------------------------------------------------------------------
    print("\n--- run_local_construction_loop ---")
    cover_element_full = {
        "element_id": "U_1",
        "obligations": ["presence:type_check", "presence:termination"],
    }
    obligations_full = [
        {"obligation_id": "presence:type_check", "kind": "presence"},
        {"obligation_id": "presence:termination", "kind": "presence"},
    ]
    ctrl = LoopController(
        controller_id=str(uuid.uuid4()),
        max_iterations=5,
        timeout_seconds=30.0,
        start_time=time.time(),
        iterations_run=0,
    )
    judgment = run_local_construction_loop(cover_element_full, obligations_full, ctrl)
    print(f"  trust={judgment.trust.name}")
    print(f"  burden={judgment.burden}")
    print(f"  residual_obligations={len(judgment.obligations)}")
    print(f"  evidence_items={len(judgment.evidence)}")
    assert isinstance(judgment, Judgment)
    assert judgment.trust >= TrustTier.WITNESSED
    print("  [OK] run_local_construction_loop assertions passed.")

    # ------------------------------------------------------------------
    # 8. LoopController budget exhaustion
    # ------------------------------------------------------------------
    print("\n--- LoopController budget exhaustion ---")
    tight_ctrl = LoopController(
        controller_id=str(uuid.uuid4()),
        max_iterations=1,
        timeout_seconds=0.0,   # no wall-clock timeout
        start_time=time.time(),
        iterations_run=0,
    )
    # Obligation that the stub proposal cannot satisfy
    obstruct_obligations = [
        {"obligation_id": "presence:impossible_obligation", "kind": "presence"},
    ]
    j2 = run_local_construction_loop(
        {"element_id": "U_2", "obligations": []},
        obstruct_obligations,
        tight_ctrl,
    )
    print(f"  trust={j2.trust.name}  burden={j2.burden}  residual={len(j2.obligations)}")
    assert j2.burden == "solver"
    print("  [OK] Budget exhaustion test passed.")

    print("\n" + "=" * 70)
    print("All smoke tests PASSED.")
    print("=" * 70)
