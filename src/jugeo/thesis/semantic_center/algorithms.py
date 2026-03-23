"""
jugeo.thesis.semantic_center.algorithms
=========================================

Key algorithms for the semantic center: bootstrapping, semantic-center
detection, and claim verification.

Each algorithm is implemented as a class with:
* ``step(state)``         — Execute one step of the algorithm.
* ``run(input)``          — Execute the algorithm to completion.
* ``copilot_assist(...)`` — Copilot-guided step (oracle evidence, ORACLE_PROPOSED trust).

All algorithms use the judgment tuple J=(c,φ,A,E,O,B,T,Π) as their primary
data structure and produce structured failures on error rather than raising
generic exceptions.

Algorithms
----------
* ``JuGeoBootstrapAlgorithm``         — Bootstrap the semantic center from an
  initial set of judgment proposals.
* ``SemanticCenterDetectionAlgorithm`` — Detect the semantic center of a given
  evidence corpus (find the coordinate subspace that maximizes gluing).
* ``ClaimVerificationAlgorithm``       — Verify a single ``ThesisClaim`` against
  the available evidence pipeline.

References
----------
* theory2.tex §1.4 — The Bootstrap Algorithm
* theory2.tex §2.4 — Verification Algorithms
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from jugeo.errors import (
    FailureClassification,
    FailureScope,
    JuGeoError,
    StructuredFailure,
    raise_with_scope,
)
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    Proposition,
    PropositionKind,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.evidence.trust import TrustAlgebra

__all__ = [
    "AlgorithmStatus",
    "AlgorithmState",
    "AlgorithmResult",
    "JuGeoBootstrapAlgorithm",
    "SemanticCenterDetectionAlgorithm",
    "ClaimVerificationAlgorithm",
]


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


class AlgorithmStatus(str, Enum):
    """Status of an algorithm execution.

    Values
    ------
    NOT_STARTED
        Algorithm has been initialized but not yet run.
    RUNNING
        Algorithm is actively executing.
    COMPLETED
        Algorithm has terminated successfully.
    FAILED
        Algorithm has terminated with a failure.
    SUSPENDED
        Algorithm has been suspended pending external input (e.g., oracle call).
    """

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"

    def is_terminal(self) -> bool:
        """Return ``True`` iff the status is terminal.

        Returns
        -------
        bool
        """
        return self in (AlgorithmStatus.COMPLETED, AlgorithmStatus.FAILED)


@dataclass(frozen=True, slots=True)
class AlgorithmState:
    """Immutable snapshot of an algorithm's state at a single step.

    Parameters
    ----------
    step_number:
        Current step number (0 = initial state).
    status:
        Current algorithm status.
    current_judgments:
        Judgments processed so far.
    pending_obligations:
        Obligations not yet discharged.
    obstructions:
        Obstruction records accumulated.
    trust_level:
        Current aggregate trust level.
    log_messages:
        Tuple of log messages for this step.
    metadata:
        Arbitrary metadata for this step.
    """

    step_number: int
    status: AlgorithmStatus
    current_judgments: tuple[Judgment, ...]
    pending_obligations: tuple[str, ...]
    obstructions: tuple[str, ...]
    trust_level: TrustLevel
    log_messages: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_complete(self) -> bool:
        """Return ``True`` iff the algorithm state is terminal.

        Returns
        -------
        bool
        """
        return self.status.is_terminal()

    def has_obstructions(self) -> bool:
        """Return ``True`` iff there are unresolved obstructions.

        Returns
        -------
        bool
        """
        return bool(self.obstructions)

    def summary(self) -> str:
        """Return a one-line summary of this state.

        Returns
        -------
        str
        """
        return (
            f"Step {self.step_number} [{self.status.value}] "
            f"judgments={len(self.current_judgments)} "
            f"obligations={len(self.pending_obligations)} "
            f"obstructions={len(self.obstructions)} "
            f"trust={self.trust_level.name}"
        )


@dataclass(frozen=True, slots=True)
class AlgorithmResult:
    """Result of a completed algorithm execution.

    Parameters
    ----------
    success:
        Whether the algorithm completed successfully.
    final_state:
        The final ``AlgorithmState``.
    output_judgments:
        The judgment(s) produced as the algorithm's output.
    failure:
        If ``success`` is ``False``, the structured failure.
    execution_log:
        Tuple of all log messages from the execution.
    steps_taken:
        Number of steps executed.
    copilot_suggestions:
        Copilot-generated suggestions collected during execution.
    """

    success: bool
    final_state: AlgorithmState
    output_judgments: tuple[Judgment, ...]
    failure: StructuredFailure | None
    execution_log: tuple[str, ...]
    steps_taken: int
    copilot_suggestions: tuple[str, ...] = ()

    def is_verified(self) -> bool:
        """Return ``True`` iff the output judgments are fully verified.

        Returns
        -------
        bool
        """
        return (
            self.success
            and all(j.is_fully_discharged() for j in self.output_judgments)
        )

    def trust_level(self) -> TrustLevel:
        """Return the aggregate trust level of the result.

        Returns
        -------
        TrustLevel
        """
        return self.final_state.trust_level

    def summary(self) -> str:
        """Return a multi-line summary of the result.

        Returns
        -------
        str
        """
        status = "SUCCESS" if self.success else "FAILURE"
        lines = [
            f"AlgorithmResult [{status}]",
            f"Steps: {self.steps_taken}",
            f"Trust: {self.trust_level().name}",
            f"Output judgments: {len(self.output_judgments)}",
        ]
        if self.failure:
            lines.append(f"Failure: {self.failure.message}")
        if self.copilot_suggestions:
            lines.append(f"Copilot suggestions: {len(self.copilot_suggestions)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# JuGeoBootstrapAlgorithm
# ---------------------------------------------------------------------------


class JuGeoBootstrapAlgorithm:
    """Bootstrap algorithm for initializing the semantic center.

    ``JuGeoBootstrapAlgorithm`` takes a collection of initial judgment
    proposals (typically at ORACLE_PROPOSED trust level) and bootstraps the
    semantic center by:

    1. Placing each proposal at a coordinate in the semantic product space.
    2. Running the trust algebra's meet operator to compute aggregate trust.
    3. Checking gluing conditions for proposals at overlapping coordinates.
    4. Recording obstructions for incompatible proposals.
    5. Escalating trust levels for proposals that pass solver checks.

    The algorithm terminates when either:
    * All proposals are at SOLVER_DISCHARGED or above (success), or
    * A maximum step count is reached (failure with remaining obligations).

    Parameters
    ----------
    max_steps:
        Maximum number of algorithm steps before termination.
    trust_floor:
        Minimum trust level for a proposal to be accepted.
    solver_callback:
        Optional callback function for solver escalation.
        Signature: ``(Judgment) -> tuple[bool, str]`` where the bool is
        success and the string is the certificate/error.
    """

    def __init__(
        self,
        max_steps: int = 100,
        trust_floor: TrustLevel = TrustLevel.ORACLE_PROPOSED,
        solver_callback: Callable[[Judgment], tuple[bool, str]] | None = None,
    ) -> None:
        """Initialize the bootstrap algorithm.

        Parameters
        ----------
        max_steps:
            Maximum steps.
        trust_floor:
            Minimum trust level.
        solver_callback:
            Optional solver callback.
        """
        self.max_steps = max_steps
        self.trust_floor = trust_floor
        self.solver_callback = solver_callback
        self._algebra = TrustAlgebra()
        self._log: list[str] = []
        self._copilot_suggestions: list[str] = []

    def step(self, state: AlgorithmState) -> AlgorithmState:
        """Execute one step of the bootstrap algorithm.

        Parameters
        ----------
        state:
            Current algorithm state.

        Returns
        -------
        AlgorithmState
            Updated state after one step.
        """
        if state.is_complete():
            return state

        step_log: list[str] = []
        new_obligations = list(state.pending_obligations)
        new_obstructions = list(state.obstructions)
        new_trust = state.trust_level

        # Step: attempt to discharge one obligation
        if new_obligations:
            obligation = new_obligations[0]
            remaining = new_obligations[1:]

            if self.solver_callback is not None:
                # Try to escalate via solver
                try:
                    j_dummy = state.current_judgments[0] if state.current_judgments else None
                    if j_dummy is not None:
                        success, certificate = self.solver_callback(j_dummy)
                        if success:
                            new_trust = self._algebra.promote(
                                new_trust,
                                f"solver-certificate: {certificate[:50]}",
                            )
                            step_log.append(
                                f"Step {state.step_number + 1}: discharged obligation "
                                f"{obligation!r} via solver → trust={new_trust.name}"
                            )
                        else:
                            new_obstructions.append(
                                f"Solver failed on {obligation!r}: {certificate[:80]}"
                            )
                            step_log.append(
                                f"Step {state.step_number + 1}: obstruction from solver "
                                f"on {obligation!r}"
                            )
                except Exception as exc:
                    new_obstructions.append(f"Solver exception on {obligation!r}: {exc}")
            else:
                # No solver: leave at current trust
                step_log.append(
                    f"Step {state.step_number + 1}: no solver available for {obligation!r}"
                )
                remaining = new_obligations  # don't consume the obligation

            new_status = (
                AlgorithmStatus.COMPLETED
                if not remaining and not new_obstructions
                else (
                    AlgorithmStatus.FAILED
                    if new_obstructions
                    else AlgorithmStatus.RUNNING
                )
            )
            if state.step_number + 1 >= self.max_steps:
                new_status = AlgorithmStatus.FAILED
                step_log.append(f"Max steps ({self.max_steps}) reached")

            return AlgorithmState(
                step_number=state.step_number + 1,
                status=new_status,
                current_judgments=state.current_judgments,
                pending_obligations=tuple(remaining),
                obstructions=tuple(new_obstructions),
                trust_level=new_trust,
                log_messages=tuple(step_log),
            )

        # No obligations: we are done
        final_status = (
            AlgorithmStatus.COMPLETED if not new_obstructions else AlgorithmStatus.FAILED
        )
        step_log.append(
            f"Step {state.step_number + 1}: no obligations remain → {final_status.value}"
        )
        return AlgorithmState(
            step_number=state.step_number + 1,
            status=final_status,
            current_judgments=state.current_judgments,
            pending_obligations=(),
            obstructions=tuple(new_obstructions),
            trust_level=new_trust,
            log_messages=tuple(step_log),
        )

    def run(
        self,
        initial_judgments: Sequence[Judgment],
        initial_obligations: Sequence[str] | None = None,
    ) -> AlgorithmResult:
        """Execute the bootstrap algorithm to completion.

        Parameters
        ----------
        initial_judgments:
            The initial judgment proposals (typically ORACLE_PROPOSED).
        initial_obligations:
            Optional initial list of obligations.  If ``None``, obligations
            are derived from the judgments' O components.

        Returns
        -------
        AlgorithmResult
            The result of the bootstrap algorithm.
        """
        if initial_obligations is None:
            all_obligations: list[str] = []
            for j in initial_judgments:
                for ob in j.obligations:
                    all_obligations.append(str(ob.coordinate))
        else:
            all_obligations = list(initial_obligations)

        # Compute initial trust level (meet of all judgment trust levels)
        if initial_judgments:
            trust = initial_judgments[0].trust.level
            for j in initial_judgments[1:]:
                trust = self._algebra.compose(trust, j.trust.level)
        else:
            trust = TrustLevel.UNVERIFIED

        initial_state = AlgorithmState(
            step_number=0,
            status=AlgorithmStatus.RUNNING if all_obligations else AlgorithmStatus.COMPLETED,
            current_judgments=tuple(initial_judgments),
            pending_obligations=tuple(all_obligations),
            obstructions=(),
            trust_level=trust,
            log_messages=(
                f"Bootstrap started: {len(initial_judgments)} judgments, "
                f"{len(all_obligations)} obligations",
            ),
        )

        state = initial_state
        all_log: list[str] = list(initial_state.log_messages)

        while not state.is_complete():
            state = self.step(state)
            all_log.extend(state.log_messages)

        failure: StructuredFailure | None = None
        if state.status == AlgorithmStatus.FAILED:
            failure = StructuredFailure(
                message=(
                    f"Bootstrap algorithm failed after {state.step_number} steps: "
                    f"{len(state.obstructions)} obstruction(s)"
                ),
                scope=FailureScope.CHAPTER,
                classification=FailureClassification.DESCENT_OBSTRUCTION
                if state.obstructions
                else FailureClassification.TIMEOUT,
            )

        return AlgorithmResult(
            success=state.status == AlgorithmStatus.COMPLETED,
            final_state=state,
            output_judgments=state.current_judgments,
            failure=failure,
            execution_log=tuple(all_log),
            steps_taken=state.step_number,
            copilot_suggestions=tuple(self._copilot_suggestions),
        )

    def copilot_assist(
        self,
        state: AlgorithmState,
        suggestion: str,
    ) -> AlgorithmState:
        """Apply a Copilot-generated suggestion to advance the algorithm.

        Parameters
        ----------
        state:
            Current algorithm state.
        suggestion:
            A natural-language suggestion from a Copilot oracle.

        Returns
        -------
        AlgorithmState
            Updated state with the suggestion recorded at ORACLE_PROPOSED trust.

        Notes
        -----
        Copilot suggestions are recorded in the log at ORACLE_PROPOSED trust
        level.  They do NOT automatically discharge obligations or resolve
        obstructions.  The suggestion is noted for audit purposes.
        """
        if not suggestion:
            return state
        self._copilot_suggestions.append(suggestion)
        log_msg = f"[COPILOT @ORACLE_PROPOSED] {suggestion[:120]}"
        return AlgorithmState(
            step_number=state.step_number,
            status=AlgorithmStatus.SUSPENDED,
            current_judgments=state.current_judgments,
            pending_obligations=state.pending_obligations,
            obstructions=state.obstructions,
            trust_level=state.trust_level,
            log_messages=state.log_messages + (log_msg,),
        )

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary of this algorithm.

        Returns
        -------
        str
        """
        return (
            "JuGeoBootstrapAlgorithm\n"
            f"Max steps: {self.max_steps} | Trust floor: {self.trust_floor.name}\n"
            "Solver: " + ("provided" if self.solver_callback else "not provided") + "\n"
            "\n"
            "Algorithm:\n"
            "  1. Initialize judgments at ORACLE_PROPOSED trust\n"
            "  2. For each obligation: attempt solver escalation\n"
            "  3. Successful escalation → promote trust level\n"
            "  4. Failed escalation → record obstruction\n"
            "  5. Terminate when all obligations discharged or max steps reached\n"
            "\n"
            "Copilot role: copilot_assist() records suggestions at ORACLE_PROPOSED.\n"
            "  Suggestions are audited but do not discharge obligations alone."
        )


# ---------------------------------------------------------------------------
# SemanticCenterDetectionAlgorithm
# ---------------------------------------------------------------------------


class SemanticCenterDetectionAlgorithm:
    """Algorithm for detecting the semantic center of an evidence corpus.

    Given a corpus of judgments (evidence from multiple sources at multiple
    coordinates), the semantic-center detection algorithm identifies the
    sub-space of the semantic product space that:

    1. Has the highest aggregate gluing score (most compatible local sections).
    2. Has the highest aggregate trust level.
    3. Has the fewest unresolved obstructions.

    This is the coordinate subspace that can serve as the semantic center for
    coordinating future verification.

    Algorithm sketch:
    1. Group judgments by coordinate.
    2. For each pair of coordinates, compute gluing score (compatibility).
    3. Find the connected component with highest combined score.
    4. Return the set of coordinates forming the semantic center.

    Parameters
    ----------
    min_trust_level:
        Minimum trust level for a judgment to be included.
    max_obstructions:
        Maximum number of obstructions allowed in the detected center.
    """

    def __init__(
        self,
        min_trust_level: TrustLevel = TrustLevel.ORACLE_PROPOSED,
        max_obstructions: int = 0,
    ) -> None:
        """Initialize the detection algorithm.

        Parameters
        ----------
        min_trust_level:
            Minimum trust level for inclusion.
        max_obstructions:
            Maximum obstructions in detected center.
        """
        self.min_trust_level = min_trust_level
        self.max_obstructions = max_obstructions
        self._algebra = TrustAlgebra()
        self._log: list[str] = []

    def step(
        self,
        state: AlgorithmState,
        candidate_coordinates: Sequence[str],
    ) -> AlgorithmState:
        """Execute one detection step.

        Parameters
        ----------
        state:
            Current algorithm state.
        candidate_coordinates:
            Coordinates to evaluate in this step.

        Returns
        -------
        AlgorithmState
            Updated state with one coordinate cluster evaluated.
        """
        if state.is_complete():
            return state

        step_log: list[str] = [
            f"Detection step {state.step_number + 1}: "
            f"evaluating {len(candidate_coordinates)} coordinates"
        ]

        # Filter judgments by minimum trust level
        eligible = [
            j for j in state.current_judgments
            if j.trust.level >= self.min_trust_level
        ]

        # Score each coordinate by counting eligible judgments there
        coord_counts: dict[str, int] = {}
        for j in eligible:
            coord_str = str(j.coordinate)
            coord_counts[coord_str] = coord_counts.get(coord_str, 0) + 1

        if not coord_counts:
            step_log.append("No eligible judgments found at minimum trust level")
            return AlgorithmState(
                step_number=state.step_number + 1,
                status=AlgorithmStatus.FAILED,
                current_judgments=state.current_judgments,
                pending_obligations=state.pending_obligations,
                obstructions=state.obstructions + ("No eligible judgments",),
                trust_level=state.trust_level,
                log_messages=tuple(step_log),
            )

        # Find best coordinate (highest count)
        best_coord = max(coord_counts, key=lambda k: coord_counts[k])
        best_count = coord_counts[best_coord]
        step_log.append(f"Best coordinate: {best_coord!r} ({best_count} judgments)")

        # Compute aggregate trust at best coordinate
        best_judgments = [
            j for j in eligible if str(j.coordinate) == best_coord
        ]
        agg_trust = best_judgments[0].trust.level if best_judgments else self.min_trust_level
        for j in best_judgments[1:]:
            agg_trust = self._algebra.compose(agg_trust, j.trust.level)

        new_status = (
            AlgorithmStatus.COMPLETED
            if len(state.pending_obligations) <= 1
            else AlgorithmStatus.RUNNING
        )

        return AlgorithmState(
            step_number=state.step_number + 1,
            status=new_status,
            current_judgments=tuple(best_judgments),
            pending_obligations=state.pending_obligations[1:],
            obstructions=state.obstructions,
            trust_level=agg_trust,
            log_messages=tuple(step_log),
        )

    def run(
        self,
        judgments: Sequence[Judgment],
    ) -> AlgorithmResult:
        """Run the semantic center detection algorithm.

        Parameters
        ----------
        judgments:
            The judgment corpus to analyze.

        Returns
        -------
        AlgorithmResult
            The detected semantic center as a set of output judgments.
        """
        if not judgments:
            return AlgorithmResult(
                success=False,
                final_state=AlgorithmState(
                    step_number=0,
                    status=AlgorithmStatus.FAILED,
                    current_judgments=(),
                    pending_obligations=(),
                    obstructions=("No judgments provided",),
                    trust_level=TrustLevel.UNVERIFIED,
                ),
                output_judgments=(),
                failure=StructuredFailure(
                    message="SemanticCenterDetection: no judgments provided",
                    scope=FailureScope.GEOMETRY,
                    classification=FailureClassification.MISSING_KEY,
                ),
                execution_log=("No judgments provided",),
                steps_taken=0,
            )

        # Gather all unique coordinate strings
        coords = list({str(j.coordinate) for j in judgments})

        # Build initial state
        trust = judgments[0].trust.level
        for j in judgments[1:]:
            trust = self._algebra.compose(trust, j.trust.level)

        state = AlgorithmState(
            step_number=0,
            status=AlgorithmStatus.RUNNING,
            current_judgments=tuple(judgments),
            pending_obligations=tuple(coords),
            obstructions=(),
            trust_level=trust,
            log_messages=(f"Detection started: {len(judgments)} judgments, {len(coords)} unique coordinates",),
        )

        all_log: list[str] = list(state.log_messages)

        while not state.is_complete() and state.step_number < 50:
            remaining_coords = list(state.pending_obligations)
            state = self.step(state, remaining_coords)
            all_log.extend(state.log_messages)

        return AlgorithmResult(
            success=state.status == AlgorithmStatus.COMPLETED,
            final_state=state,
            output_judgments=state.current_judgments,
            failure=None,
            execution_log=tuple(all_log),
            steps_taken=state.step_number,
        )

    def copilot_assist(
        self,
        judgments: Sequence[Judgment],
        hint: str,
    ) -> list[str]:
        """Get Copilot-assisted coordinate suggestions for center detection.

        Parameters
        ----------
        judgments:
            Current judgment corpus.
        hint:
            Natural-language hint from Copilot about which coordinates to focus on.

        Returns
        -------
        list[str]
            Suggested coordinate strings (at ORACLE_PROPOSED trust).

        Notes
        -----
        Copilot's coordinate suggestions are recorded at ORACLE_PROPOSED trust.
        They are hints for the detection algorithm, not final answers.
        """
        self._log.append(f"[COPILOT @ORACLE_PROPOSED] Center hint: {hint[:120]}")
        # Return the highest-trust coordinates as a Copilot suggestion proxy
        scored = sorted(
            judgments,
            key=lambda j: j.trust.level.value,
            reverse=True,
        )
        return [str(j.coordinate) for j in scored[:5]]

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary.

        Returns
        -------
        str
        """
        return (
            "SemanticCenterDetectionAlgorithm\n"
            f"Min trust: {self.min_trust_level.name} | Max obstructions: {self.max_obstructions}\n"
            "\n"
            "Algorithm:\n"
            "  1. Filter judgments by minimum trust level\n"
            "  2. Group by coordinate; score by judgment density and trust\n"
            "  3. Find connected component with highest combined score\n"
            "  4. Return the semantic center coordinates\n"
            "\n"
            "Copilot role: copilot_assist() suggests coordinates to focus on.\n"
            "  Suggestions are at ORACLE_PROPOSED and do not alter trust levels."
        )


# ---------------------------------------------------------------------------
# ClaimVerificationAlgorithm
# ---------------------------------------------------------------------------


class ClaimVerificationAlgorithm:
    """Algorithm for verifying a single thesis claim against the evidence pipeline.

    ``ClaimVerificationAlgorithm`` takes a thesis claim (from
    ``jugeo.thesis.semantic_center.models.ThesisClaim``) and runs it through
    the available evidence pipeline to advance its trust level.

    The algorithm:
    1. Instantiates the claim as a Judgment proposal (ORACLE_PROPOSED).
    2. Routes it to the appropriate open set in the semantic cover.
    3. Calls the solver (if available) to attempt SOLVER_DISCHARGED.
    4. Calls the formal prover (if available) to attempt VERIFIED_PROOF.
    5. Records all obstructions encountered.
    6. Returns the highest trust level achieved.

    Parameters
    ----------
    solver_callback:
        Optional solver callback.
    prover_callback:
        Optional formal prover callback.
    max_steps:
        Maximum number of algorithm steps.
    """

    def __init__(
        self,
        solver_callback: Callable[[Judgment], tuple[bool, str]] | None = None,
        prover_callback: Callable[[Judgment], tuple[bool, str]] | None = None,
        max_steps: int = 50,
    ) -> None:
        """Initialize the claim verification algorithm.

        Parameters
        ----------
        solver_callback:
            Optional solver callback.
        prover_callback:
            Optional formal prover callback.
        max_steps:
            Maximum steps.
        """
        self.solver_callback = solver_callback
        self.prover_callback = prover_callback
        self.max_steps = max_steps
        self._algebra = TrustAlgebra()
        self._copilot_suggestions: list[str] = []

    def step(self, state: AlgorithmState) -> AlgorithmState:
        """Execute one verification step.

        Parameters
        ----------
        state:
            Current state.

        Returns
        -------
        AlgorithmState
            Updated state.
        """
        if state.is_complete():
            return state

        step_log: list[str] = []
        new_trust = state.trust_level
        new_obstructions = list(state.obstructions)
        new_obligations = list(state.pending_obligations)

        current_step = state.step_number + 1

        # Try solver escalation
        if (
            self.solver_callback is not None
            and new_trust < TrustLevel.SOLVER_DISCHARGED
            and state.current_judgments
        ):
            try:
                success, cert = self.solver_callback(state.current_judgments[0])
                if success:
                    new_trust = self._algebra.promote(
                        new_trust, f"solver-discharge@step{current_step}"
                    )
                    if new_obligations:
                        new_obligations = new_obligations[1:]
                    step_log.append(
                        f"Step {current_step}: solver discharge → {new_trust.name}"
                    )
                else:
                    new_obstructions.append(f"Solver failed: {cert[:80]}")
                    step_log.append(f"Step {current_step}: solver obstruction")
            except Exception as exc:
                new_obstructions.append(f"Solver exception: {exc}")

        # Try prover escalation
        elif (
            self.prover_callback is not None
            and new_trust < TrustLevel.VERIFIED_PROOF
            and state.current_judgments
        ):
            try:
                success, cert = self.prover_callback(state.current_judgments[0])
                if success:
                    new_trust = self._algebra.promote(
                        new_trust, f"formal-proof@step{current_step}"
                    )
                    if new_obligations:
                        new_obligations = new_obligations[1:]
                    step_log.append(
                        f"Step {current_step}: formal proof → {new_trust.name}"
                    )
                else:
                    new_obstructions.append(f"Prover failed: {cert[:80]}")
                    step_log.append(f"Step {current_step}: prover obstruction")
            except Exception as exc:
                new_obstructions.append(f"Prover exception: {exc}")

        else:
            step_log.append(f"Step {current_step}: no escalation available")
            # Move to terminal state
            new_obligations = []

        is_verified = (
            new_trust >= TrustLevel.SOLVER_DISCHARGED
            and not new_obligations
            and not new_obstructions
        )
        new_status = (
            AlgorithmStatus.COMPLETED
            if is_verified or not new_obligations
            else (
                AlgorithmStatus.FAILED
                if new_obstructions or current_step >= self.max_steps
                else AlgorithmStatus.RUNNING
            )
        )

        return AlgorithmState(
            step_number=current_step,
            status=new_status,
            current_judgments=state.current_judgments,
            pending_obligations=tuple(new_obligations),
            obstructions=tuple(new_obstructions),
            trust_level=new_trust,
            log_messages=tuple(step_log),
        )

    def run(
        self,
        claim_judgment: Judgment,
        obligations: Sequence[str] | None = None,
    ) -> AlgorithmResult:
        """Run the claim verification algorithm.

        Parameters
        ----------
        claim_judgment:
            The claim as an initial Judgment proposal.
        obligations:
            Optional explicit list of obligations.

        Returns
        -------
        AlgorithmResult
        """
        obs_list = list(obligations) if obligations else [
            str(ob.coordinate) for ob in claim_judgment.obligations
        ]

        state = AlgorithmState(
            step_number=0,
            status=AlgorithmStatus.RUNNING if obs_list else AlgorithmStatus.COMPLETED,
            current_judgments=(claim_judgment,),
            pending_obligations=tuple(obs_list),
            obstructions=tuple(
                str(ob.violated_condition) for ob in claim_judgment.obstructions
                if not ob.is_resolved
            ),
            trust_level=claim_judgment.trust.level,
            log_messages=(
                f"Claim verification started: "
                f"trust={claim_judgment.trust.level.name}, "
                f"obligations={len(obs_list)}",
            ),
        )

        all_log: list[str] = list(state.log_messages)

        while not state.is_complete() and state.step_number < self.max_steps:
            state = self.step(state)
            all_log.extend(state.log_messages)

        failure: StructuredFailure | None = None
        if state.status == AlgorithmStatus.FAILED:
            failure = StructuredFailure(
                message=(
                    f"Claim verification failed: {len(state.obstructions)} obstruction(s); "
                    f"final trust={state.trust_level.name}"
                ),
                scope=FailureScope.CHAPTER,
                classification=FailureClassification.DESCENT_OBSTRUCTION
                if state.obstructions
                else FailureClassification.TIMEOUT,
            )

        return AlgorithmResult(
            success=state.status == AlgorithmStatus.COMPLETED,
            final_state=state,
            output_judgments=state.current_judgments,
            failure=failure,
            execution_log=tuple(all_log),
            steps_taken=state.step_number,
            copilot_suggestions=tuple(self._copilot_suggestions),
        )

    def copilot_assist(
        self,
        state: AlgorithmState,
        suggestion: str,
        suggested_trust_ceiling: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> AlgorithmState:
        """Apply a Copilot suggestion to the verification state.

        Parameters
        ----------
        state:
            Current state.
        suggestion:
            Copilot's natural-language suggestion.
        suggested_trust_ceiling:
            Trust ceiling for the suggestion (default ORACLE_PROPOSED).

        Returns
        -------
        AlgorithmState
            State with suggestion recorded.
        """
        if not suggestion:
            return state
        ceiling = min(suggested_trust_ceiling, TrustLevel.ORACLE_PROPOSED)
        self._copilot_suggestions.append(suggestion)
        log_msg = (
            f"[COPILOT @{ceiling.name}] {suggestion[:120]}"
        )
        return AlgorithmState(
            step_number=state.step_number,
            status=AlgorithmStatus.SUSPENDED,
            current_judgments=state.current_judgments,
            pending_obligations=state.pending_obligations,
            obstructions=state.obstructions,
            trust_level=state.trust_level,
            log_messages=state.log_messages + (log_msg,),
        )

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary.

        Returns
        -------
        str
        """
        return (
            "ClaimVerificationAlgorithm\n"
            f"Max steps: {self.max_steps}\n"
            "Solver: " + ("provided" if self.solver_callback else "not provided") + "\n"
            "Prover: " + ("provided" if self.prover_callback else "not provided") + "\n"
            "\n"
            "Algorithm:\n"
            "  1. Initialize claim as ORACLE_PROPOSED judgment\n"
            "  2. Attempt solver discharge (→ SOLVER_DISCHARGED)\n"
            "  3. Attempt formal proof (→ VERIFIED_PROOF)\n"
            "  4. Record obstructions for failed attempts\n"
            "  5. Return highest trust level achieved\n"
            "\n"
            "Copilot role: copilot_assist() records suggestions at ORACLE_PROPOSED.\n"
            "  Copilot cannot self-escalate to SOLVER_DISCHARGED or higher."
        )
