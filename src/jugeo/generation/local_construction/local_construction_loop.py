r"""Local construction loop engine for JuGeo generation.

Theory (theory2.tex §39 — Local construction loops):
    A *local construction loop* (LCL) is the primary operational unit for building
    a section s_u over a coordinate chart U.  Given a GenerationGoal G_u, the loop
    iterates through four phases until convergence or budget exhaustion:

        1.  Proposal phase   — enumerate candidate sections C = {c_1, …, c_k}
        2.  Selection phase  — choose the best candidate c* ∈ C
        3.  Verification phase — test c* against the current obligation set O_u
        4.  Propagation phase  — push residual obligations ∂O_u to neighbouring charts

    The convergence criterion is:

        converged(loop) ⟺ ∃ c* : verify(c*, O_u) = ✓  ∧  budget_remaining(loop) ≥ 0

    Budget is tracked as a continuous scalar B ∈ [0, 1] and is decremented by a
    cost model on each iteration.  If B drops to 0 the loop is aborted and a
    BudgetExhaustedError is raised into the outer ConstructionLoop.

    Semantic compression records χ_u = (ΔS_u, ΔO_u, ΔE_u, ΔX_u, ΔK_u, supp(Δ_u))
    are emitted at the end of every successful loop run.  These records feed the
    global compression accounting machinery described in §41.

    The LocalConstructionLoopEngine defined here drives a single LocalConstructionLoop
    object through all four phases.  It is deliberately kept stateless with respect
    to mathematical content: all domain logic lives in the model layer; the engine
    only orchestrates calls and records telemetry.

    References
    ----------
    theory2.tex  §39 (Local construction loops), §41 (Semantic compression)

copilot: s01-local-construction-loop
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from jugeo.generation.construction import (
    Candidate,
    ConstructionContext,
    ConstructionResult,
)
from jugeo.generation.goals import GenerationGoal
from jugeo.generation.local_construction.models import (  # type: ignore[import]
    BudgetExhaustedError,
    CandidateSet,
    ConvergenceFailureError,
    LocalConstructionError,
    LocalConstructionLoop,
)

__all__ = [
    "LocalConstructionLoopEngine",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "max_iterations": 20,
    "budget_per_loop": 1.0,
    "stall_threshold": 3,
    "proposal_count": 5,
    "verification_policy": "standard",
    "trace_enabled": True,
}

_BUDGET_DECREMENT_PER_ITER: float = 0.05  # 5 % of total budget per iteration
_PROPOSAL_EXPANSION_DELTA: int = 3       # extra proposals when unstalling


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _budget_cost(iteration: int, policy: str) -> float:
    """Return the budget cost for a single iteration under *policy*."""
    base = _BUDGET_DECREMENT_PER_ITER
    if policy == "thorough":
        base *= 2.0
    elif policy == "cheap":
        base *= 0.5
    # exponential bleed-off for long-running loops
    return base * (1.0 + 0.02 * iteration)


def _score_candidate(candidate: dict) -> float:
    """Heuristic score used when the CandidateSet scoring is unavailable."""
    trust = float(candidate.get("trust_score", 0.5))
    residuals = len(candidate.get("residual_obligations", ()))
    penalty = 0.05 * residuals
    return _clamp(trust - penalty, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class LocalConstructionLoopEngine:
    """Engine that drives a LocalConstructionLoop through all phases.

    The engine is responsible for:
    - Creating and registering LocalConstructionLoop instances
    - Sequencing the four phases (propose → select → verify → propagate)
    - Detecting and handling stalls and budget exhaustion
    - Emitting semantic compression records at loop completion

    It stores no mathematical state; all domain data lives in the
    LocalConstructionLoop model and the CandidateSet model.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the engine with optional configuration overrides.

        Parameters
        ----------
        config:
            Mapping of configuration keys to override.  Any key not present
            defaults to the value in ``_DEFAULT_CONFIG``.
        """
        merged: dict[str, Any] = dict(_DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self._config: dict[str, Any] = merged

        self._logger: logging.Logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._active_loops: dict[str, LocalConstructionLoop] = {}
        self._loop_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Phase 0 — Setup
    # ------------------------------------------------------------------

    def setup_loop(
        self,
        goal: GenerationGoal,
        context: ConstructionContext,
    ) -> LocalConstructionLoop:
        """Create, initialise, and register a new LocalConstructionLoop.

        Parameters
        ----------
        goal:
            The GenerationGoal that this loop must satisfy.
        context:
            The ConstructionContext providing bindings and budget information.

        Returns
        -------
        LocalConstructionLoop
            The freshly initialised loop, ready for the proposal phase.
        """
        loop_id = str(uuid.uuid4())

        loop = LocalConstructionLoop(
            loop_id=loop_id,
            goal_id=goal.goal_id,
            coordinate_id=goal.coordinate_id,
            max_iterations=self._config["max_iterations"],
            current_iteration=0,
            status="initialising",
            candidate_history=[],
            selected_candidate_id=None,
            verification_record=[],
            budget_remaining=min(
                goal.budget,
                context.budget_remaining,
                self._config["budget_per_loop"],
            ),
        )
        loop.initialize(goal)

        self._active_loops[loop_id] = loop
        self._logger.info(
            "LocalConstructionLoop %s set up for goal %s on coordinate %s "
            "(budget=%.3f, max_iter=%d)",
            loop_id,
            goal.goal_id,
            goal.coordinate_id,
            loop.budget_remaining,
            loop.max_iterations,
        )
        return loop

    # ------------------------------------------------------------------
    # Phase 1 — Proposal
    # ------------------------------------------------------------------

    def run_proposal_phase(
        self,
        loop: LocalConstructionLoop,
        context: ConstructionContext,
    ) -> CandidateSet:
        """Populate a CandidateSet for the current iteration.

        Calls ``loop.propose_candidates(context)`` to obtain raw candidate
        dicts, wraps them in a ``CandidateSet``, scores the set, and returns
        it.

        Parameters
        ----------
        loop:
            The loop whose proposal phase is being executed.
        context:
            The construction context (bindings, budget, evidence).

        Returns
        -------
        CandidateSet
            A scored set of candidates ready for the selection phase.
        """
        set_id = str(uuid.uuid4())

        candidate_set = CandidateSet(
            set_id=set_id,
            goal_id=loop.goal_id,
            candidates=[],
            generation_method="solver",
            generated_at=time.time(),
            scored=False,
            scores={},
            dominated_ids=set(),
        )

        raw_candidates: list[dict[str, Any]] = loop.propose_candidates(context)

        # Ensure we have at most proposal_count candidates
        count_limit = self._config["proposal_count"]
        for raw in raw_candidates[:count_limit]:
            candidate_set.candidates.append(raw)

        # Score the candidate set
        self._score_candidate_set(candidate_set)

        self._logger.debug(
            "Loop %s (iter %d): proposed %d candidates (set %s)",
            loop.loop_id,
            loop.current_iteration,
            len(candidate_set.candidates),
            set_id,
        )
        return candidate_set

    def _score_candidate_set(self, candidate_set: CandidateSet) -> None:
        """In-place scoring of every candidate in *candidate_set*."""
        for c in candidate_set.candidates:
            cid = c.get("candidate_id", str(uuid.uuid4()))
            c["candidate_id"] = cid
            candidate_set.scores[cid] = _score_candidate(c)
        candidate_set.scored = True

        # Mark dominated candidates (score strictly below max - threshold)
        if not candidate_set.scores:
            return
        best_score = max(candidate_set.scores.values())
        domination_threshold = 0.25
        for cid, score in candidate_set.scores.items():
            if best_score - score > domination_threshold:
                candidate_set.dominated_ids.add(cid)

    # ------------------------------------------------------------------
    # Phase 2 — Selection
    # ------------------------------------------------------------------

    def run_selection_phase(
        self,
        loop: LocalConstructionLoop,
        candidate_set: CandidateSet,
    ) -> dict[str, Any] | None:
        """Choose the best candidate from the scored set.

        Retrieves the top-3 non-dominated candidates, asks the loop model to
        select among them, and returns the winning candidate dict.

        Parameters
        ----------
        loop:
            The loop performing selection.
        candidate_set:
            The previously scored candidate set.

        Returns
        -------
        dict | None
            The selected candidate dict, or ``None`` if the loop could not
            select any candidate (empty set or all dominated).
        """
        # Build ranked list of non-dominated candidates
        viable = [
            c for c in candidate_set.candidates
            if c.get("candidate_id") not in candidate_set.dominated_ids
        ]
        if not viable:
            viable = list(candidate_set.candidates)  # fall back to all

        top3 = sorted(
            viable,
            key=lambda c: candidate_set.scores.get(c.get("candidate_id", ""), 0.0),
            reverse=True,
        )[:3]

        selected_id: str | None = loop.select_best(top3)

        if selected_id is None:
            self._logger.warning(
                "Loop %s (iter %d): selection phase returned no candidate",
                loop.loop_id,
                loop.current_iteration,
            )
            return None

        # Locate the selected candidate in the set
        selected: dict[str, Any] | None = next(
            (c for c in candidate_set.candidates if c.get("candidate_id") == selected_id),
            None,
        )
        if selected is None:
            # Fall back to the top-scored candidate
            selected = top3[0] if top3 else None

        if selected is not None:
            self._logger.debug(
                "Loop %s (iter %d): selected candidate %s (score=%.3f)",
                loop.loop_id,
                loop.current_iteration,
                selected.get("candidate_id"),
                candidate_set.scores.get(selected.get("candidate_id", ""), 0.0),
            )
        return selected

    # ------------------------------------------------------------------
    # Phase 3 — Verification
    # ------------------------------------------------------------------

    def run_verification_phase(
        self,
        loop: LocalConstructionLoop,
        candidate: dict[str, Any],
        context: ConstructionContext,
    ) -> tuple[bool, list[str], dict[str, Any]]:
        """Verify a candidate against the loop's obligation set.

        Parameters
        ----------
        loop:
            The loop performing verification.
        candidate:
            The candidate dict to verify.
        context:
            The current construction context.

        Returns
        -------
        (ok, residuals, evidence)
            *ok* is ``True`` iff verification passed.
            *residuals* is the list of obligations that could not be
            discharged.
            *evidence* is a mapping from obligation id to proof artefact.
        """
        ok: bool
        residuals: list[str]
        evidence: dict[str, Any]

        ok, residuals, evidence = loop.verify_candidate(candidate, context)

        # Append a record tuple to the verification log
        record_entry: tuple[str, bool, list[str], float] = (
            candidate.get("candidate_id", "unknown"),
            ok,
            list(residuals),
            time.time(),
        )
        loop.verification_record.append(record_entry)

        # Deduct budget for the verification effort
        cost = _budget_cost(loop.current_iteration, self._config["verification_policy"])
        loop.budget_remaining = max(0.0, loop.budget_remaining - cost)

        if not ok:
            self._logger.warning(
                "Loop %s (iter %d): verification FAILED for candidate %s "
                "— residuals: %s (budget_remaining=%.3f)",
                loop.loop_id,
                loop.current_iteration,
                candidate.get("candidate_id"),
                residuals,
                loop.budget_remaining,
            )
        else:
            self._logger.debug(
                "Loop %s (iter %d): verification PASSED for candidate %s",
                loop.loop_id,
                loop.current_iteration,
                candidate.get("candidate_id"),
            )

        return ok, residuals, evidence

    # ------------------------------------------------------------------
    # Phase 4 — Propagation
    # ------------------------------------------------------------------

    def run_propagation_phase(
        self,
        loop: LocalConstructionLoop,
        ok: bool,
        residuals: list[str],
        evidence: dict[str, Any],
    ) -> list[str]:
        """Propagate obligations and advance the loop state.

        If verification passed the loop status is set to ``"succeeded"``
        and obligations are propagated to neighbours.  Otherwise the
        iteration counter is advanced and convergence is checked.

        Parameters
        ----------
        loop:
            The loop in the propagation phase.
        ok:
            Whether the previous verification step passed.
        residuals:
            Residual obligations from the verification step.
        evidence:
            Evidence map from the verification step.

        Returns
        -------
        list[str]
            Obligations to be propagated upward to the outer
            ConstructionLoop.
        """
        obligations_to_propagate: list[str] = []

        if ok:
            loop.status = "succeeded"
            payload: dict[str, Any] = {"residuals": residuals, "evidence": evidence}
            propagated: list[str] = loop.propagate_obligations(payload)
            obligations_to_propagate.extend(propagated)
            self._logger.debug(
                "Loop %s succeeded; propagating %d obligations upward",
                loop.loop_id,
                len(obligations_to_propagate),
            )
        else:
            loop.advance_iteration()

            if loop.is_converged():
                loop.status = "converged_no_solution"
                self._logger.debug(
                    "Loop %s reached convergence limit at iteration %d",
                    loop.loop_id,
                    loop.current_iteration,
                )
            elif loop.budget_remaining <= 0.0:
                loop.status = "budget_exhausted"
                self._logger.warning(
                    "Loop %s budget exhausted at iteration %d",
                    loop.loop_id,
                    loop.current_iteration,
                )
            else:
                loop.status = "iterating"

            # Surface unresolved obligations upward regardless
            obligations_to_propagate.extend(residuals)

        return obligations_to_propagate

    # ------------------------------------------------------------------
    # Stall handling
    # ------------------------------------------------------------------

    def handle_stall(
        self,
        loop: LocalConstructionLoop,
        context: ConstructionContext,
    ) -> bool:
        """Detect and attempt to recover from a stall.

        A *stall* occurs when ``current_iteration > stall_threshold`` and
        the last ``stall_threshold`` verification attempts all failed.

        Recovery is attempted in three escalating steps:

        1. Expand the proposal count to generate more diverse candidates.
        2. Relax the verification policy to ``"cheap"``.
        3. Request a budget increase by borrowing from the context.

        Parameters
        ----------
        loop:
            The loop that may be stalled.
        context:
            The current construction context (used for budget requests).

        Returns
        -------
        bool
            ``True`` if at least one recovery measure was applied,
            ``False`` if the loop is irrecoverably stalled.
        """
        threshold = self._config["stall_threshold"]
        if loop.current_iteration <= threshold:
            return False  # Too early to call a stall

        # Inspect the last `threshold` verification records
        recent = loop.verification_record[-threshold:] if loop.verification_record else []
        all_failed = bool(recent) and all(not entry[1] for entry in recent)

        if not all_failed:
            return False  # Not stalled

        self._logger.warning(
            "Loop %s stalled after %d consecutive verification failures "
            "(iteration=%d, budget=%.3f)",
            loop.loop_id,
            threshold,
            loop.current_iteration,
            loop.budget_remaining,
        )

        recovered = False

        # Step 1: Expand proposal count
        old_count = self._config["proposal_count"]
        self._config["proposal_count"] = old_count + _PROPOSAL_EXPANSION_DELTA
        self._logger.warning(
            "Stall recovery [1/3]: expanding proposal_count %d → %d",
            old_count,
            self._config["proposal_count"],
        )
        recovered = True

        # Step 2: Relax verification policy
        if self._config["verification_policy"] != "cheap":
            self._logger.warning(
                "Stall recovery [2/3]: relaxing verification_policy '%s' → 'cheap'",
                self._config["verification_policy"],
            )
            self._config["verification_policy"] = "cheap"
            recovered = True

        # Step 3: Request budget increase from context
        available_from_context = context.budget_remaining * 0.1  # borrow 10 %
        if available_from_context > 0.01:
            loop.budget_remaining += available_from_context
            context.budget_remaining -= available_from_context
            self._logger.warning(
                "Stall recovery [3/3]: borrowed %.4f budget from context "
                "(loop budget now %.4f)",
                available_from_context,
                loop.budget_remaining,
            )
            recovered = True

        if not recovered:
            loop.status = "stalled"
            self._logger.warning(
                "Loop %s is irrecoverably stalled — marking STALLED",
                loop.loop_id,
            )

        return recovered

    # ------------------------------------------------------------------
    # Budget exhaustion
    # ------------------------------------------------------------------

    def handle_budget_exhaustion(
        self,
        loop: LocalConstructionLoop,
    ) -> dict[str, Any]:
        """Handle the case where a loop has exhausted its budget.

        Sets ``loop.status = "failed"``, collects all verification records
        as evidence, and returns a ConstructionResult-like dict.

        Parameters
        ----------
        loop:
            The loop whose budget has been exhausted.

        Returns
        -------
        dict
            A ConstructionResult-compatible dict with ``status="failed"``
            and ``reason="budget_exhausted"``.
        """
        loop.status = "failed"

        evidence: dict[str, Any] = {
            "verification_records": [
                {
                    "candidate_id": entry[0],
                    "passed": entry[1],
                    "residuals": entry[2],
                    "timestamp": entry[3],
                }
                for entry in loop.verification_record
            ],
            "final_iteration": loop.current_iteration,
            "budget_at_exhaustion": loop.budget_remaining,
        }

        result: dict[str, Any] = {
            "result_id": str(uuid.uuid4()),
            "goal_id": loop.goal_id,
            "candidate_id": loop.selected_candidate_id,
            "status": "failed",
            "reason": "budget_exhausted",
            "residual_obligations": list(loop.get_residual_obligations()),
            "evidence": evidence,
            "elapsed_ms": 0,  # caller should fill in
            "iteration_count": loop.current_iteration,
        }

        self._logger.error(
            "Loop %s failed due to budget exhaustion after %d iterations",
            loop.loop_id,
            loop.current_iteration,
        )
        return result

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def collect_diagnostics(
        self,
        loop: LocalConstructionLoop,
    ) -> dict[str, Any]:
        """Collect a comprehensive diagnostics snapshot for *loop*.

        Returns
        -------
        dict
            Diagnostics dict with the following keys:

            * ``loop_id``, ``goal_id``, ``coordinate_id``
            * ``current_iteration``, ``max_iterations``, ``budget_remaining``
            * ``status``, ``selected_candidate_id``
            * ``verification_summary`` — pass/fail counts
            * ``candidate_history_length``
            * ``residual_obligations``
            * ``health_score`` — scalar in [0, 1]
        """
        records = loop.verification_record
        passed_count = sum(1 for r in records if r[1])
        failed_count = len(records) - passed_count
        total_checks = max(1, len(records))

        convergence_rate = passed_count / total_checks

        # Health score: blend of budget and convergence rate, penalised by
        # the ratio of current iteration to max iterations.
        progress_factor = 1.0 - (loop.current_iteration / max(1, loop.max_iterations))
        health_score = _clamp(
            loop.budget_remaining * convergence_rate * (0.5 + 0.5 * progress_factor),
            0.0,
            1.0,
        )

        return {
            "loop_id": loop.loop_id,
            "goal_id": loop.goal_id,
            "coordinate_id": loop.coordinate_id,
            "current_iteration": loop.current_iteration,
            "max_iterations": loop.max_iterations,
            "budget_remaining": loop.budget_remaining,
            "status": loop.status,
            "selected_candidate_id": loop.selected_candidate_id,
            "verification_summary": {
                "total": len(records),
                "passed": passed_count,
                "failed": failed_count,
                "pass_rate": passed_count / total_checks,
            },
            "candidate_history_length": len(loop.candidate_history),
            "residual_obligations": list(loop.get_residual_obligations()),
            "health_score": health_score,
        }

    # ------------------------------------------------------------------
    # Semantic compression
    # ------------------------------------------------------------------

    def emit_semantic_compression_record(
        self,
        loop: LocalConstructionLoop,
        delta_S: float,
        delta_O: int,
        delta_E: int,
    ) -> dict[str, Any]:
        """Emit a semantic compression record for a completed loop.

        The record captures the *change* in the semantic state caused by
        executing this loop:

            χ_u = (ΔS_u, ΔO_u, ΔE_u, ΔX_u, ΔK_u, supp(Δ_u))

        where:

        * ΔS_u — change in section complexity (caller-provided)
        * ΔO_u — change in obligation count (caller-provided)
        * ΔE_u — change in evidence count (caller-provided)
        * ΔX_u — number of obstructions cleared in this loop
        * ΔK_u — number of certificates generated
        * supp(Δ_u) — the support region (here, the loop's coordinate)

        Parameters
        ----------
        loop:
            The loop for which to emit the record.
        delta_S:
            Change in section complexity (e.g., number of new symbols
            introduced).
        delta_O:
            Change in obligation count (negative means obligations were
            discharged).
        delta_E:
            Change in evidence count.

        Returns
        -------
        dict
            The χ_u record dict, also appended to ``self._loop_history``.
        """
        # ΔX_u: count verification records that transitioned from fail to pass
        records = loop.verification_record
        delta_X = 0
        for i in range(1, len(records)):
            if not records[i - 1][1] and records[i][1]:
                delta_X += 1

        # ΔK_u: count passed verifications (each generates a certificate)
        delta_K = sum(1 for r in records if r[1])

        support_region = [loop.coordinate_id]

        record: dict[str, Any] = {
            "record_id": str(uuid.uuid4()),
            "loop_id": loop.loop_id,
            "goal_id": loop.goal_id,
            "coordinate_id": loop.coordinate_id,
            "chi": {
                "delta_S": delta_S,
                "delta_O": delta_O,
                "delta_E": delta_E,
                "delta_X": delta_X,
                "delta_K": delta_K,
                "support": support_region,
            },
            "emitted_at": time.time(),
            "loop_status": loop.status,
            "iteration_count": loop.current_iteration,
        }

        self._loop_history.append(record)

        if self._config["trace_enabled"]:
            self._logger.debug(
                "χ_u emitted for loop %s: ΔS=%.2f ΔO=%d ΔE=%d ΔX=%d ΔK=%d supp=%s",
                loop.loop_id,
                delta_S,
                delta_O,
                delta_E,
                delta_X,
                delta_K,
                support_region,
            )

        return record

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    def finalize_loop(
        self,
        loop: LocalConstructionLoop,
    ) -> dict[str, Any]:
        """Finalise a loop and remove it from the active registry.

        Computes final diagnostics, emits a semantic compression record,
        and returns a comprehensive finalisation record.

        Parameters
        ----------
        loop:
            The loop to finalise.

        Returns
        -------
        dict
            Finalisation record containing:
            * ``loop_summary`` — from ``loop.summary()``
            * ``final_status``
            * ``residual_obligations``
            * ``diagnostics``
            * ``compression_record``
        """
        diagnostics = self.collect_diagnostics(loop)

        # Estimate compression deltas from diagnostics
        delta_S = float(len(loop.candidate_history))
        delta_O = -len(diagnostics["residual_obligations"])
        passed = diagnostics["verification_summary"]["passed"]
        delta_E = passed

        compression_record = self.emit_semantic_compression_record(
            loop, delta_S=delta_S, delta_O=delta_O, delta_E=delta_E
        )

        self._active_loops.pop(loop.loop_id, None)

        finalisation: dict[str, Any] = {
            "finalisation_id": str(uuid.uuid4()),
            "loop_id": loop.loop_id,
            "loop_summary": loop.summary(),
            "final_status": loop.status,
            "residual_obligations": diagnostics["residual_obligations"],
            "diagnostics": diagnostics,
            "compression_record": compression_record,
            "finalised_at": time.time(),
        }

        self._logger.info(
            "Loop %s finalised with status '%s' "
            "(health=%.3f, residuals=%d)",
            loop.loop_id,
            loop.status,
            diagnostics["health_score"],
            len(diagnostics["residual_obligations"]),
        )
        return finalisation

    # ------------------------------------------------------------------
    # Convenience orchestrator
    # ------------------------------------------------------------------

    def run_full_loop(
        self,
        goal: GenerationGoal,
        context: ConstructionContext,
    ) -> dict[str, Any]:
        """Orchestrate all phases of a LocalConstructionLoop end-to-end.

        This convenience method sequences setup → repeated
        (propose → select → verify → propagate) → finalise, handling stalls
        and budget exhaustion internally.

        Parameters
        ----------
        goal:
            The GenerationGoal to satisfy.
        context:
            The ConstructionContext supplying bindings and budget.

        Returns
        -------
        dict
            The finalisation record from :meth:`finalize_loop`.
        """
        start_time = time.time()

        loop = self.setup_loop(goal, context)

        while True:
            # --- Budget gate ---
            if loop.budget_remaining <= 0.0:
                exhaustion_result = self.handle_budget_exhaustion(loop)
                self._logger.error(
                    "run_full_loop: budget exhausted for goal %s", goal.goal_id
                )
                # Still finalise for clean accounting
                final = self.finalize_loop(loop)
                final["exhaustion_result"] = exhaustion_result
                final["elapsed_ms"] = int((time.time() - start_time) * 1000)
                return final

            # --- Convergence / max-iteration gate ---
            if loop.is_converged():
                loop.status = "converged_no_solution"
                self._logger.warning(
                    "run_full_loop: loop %s reached max iterations without success",
                    loop.loop_id,
                )
                break

            if loop.status in ("succeeded", "failed", "stalled"):
                break

            # --- Phase 1: Proposal ---
            candidate_set = self.run_proposal_phase(loop, context)

            if not candidate_set.candidates:
                # No candidates generated — treat as stall trigger
                self._logger.warning(
                    "run_full_loop: no candidates generated in iteration %d "
                    "for loop %s",
                    loop.current_iteration,
                    loop.loop_id,
                )
                recovered = self.handle_stall(loop, context)
                if not recovered:
                    loop.status = "stalled"
                    break
                continue

            # --- Phase 2: Selection ---
            selected = self.run_selection_phase(loop, candidate_set)
            if selected is None:
                loop.advance_iteration()
                continue

            # Track in candidate history
            loop.candidate_history.append(selected)

            # --- Phase 3: Verification ---
            ok, residuals, evidence = self.run_verification_phase(
                loop, selected, context
            )

            # --- Phase 4: Propagation ---
            _ = self.run_propagation_phase(loop, ok, residuals, evidence)

            if loop.status == "succeeded":
                break

            # Check for stall after propagation
            self.handle_stall(loop, context)

            if loop.status == "stalled":
                break

        final = self.finalize_loop(loop)
        final["elapsed_ms"] = int((time.time() - start_time) * 1000)
        return final

    # ------------------------------------------------------------------
    # Public read-only accessors
    # ------------------------------------------------------------------

    @property
    def active_loops(self) -> dict[str, LocalConstructionLoop]:
        """Read-only view of currently active loops."""
        return dict(self._active_loops)

    @property
    def loop_history(self) -> list[dict[str, Any]]:
        """Read-only view of completed loop records."""
        return list(self._loop_history)

    def __repr__(self) -> str:
        return (
            f"LocalConstructionLoopEngine("
            f"active={len(self._active_loops)}, "
            f"history={len(self._loop_history)}, "
            f"policy={self._config['verification_policy']!r})"
        )
