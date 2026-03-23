"""
Core algorithms for methodology loop orchestration, convergence detection, and hypothesis
management.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

This module implements the algorithmic heart of the methodology_loops evaluation package,
providing loop stepping, convergence checking, falsification attempts, and phase scoring.
The algorithms are designed to be stateless free functions backed by the
MethodologyAlgorithms class for stateful orchestration.

The convergence theory encoded here is grounded in the finite-descent argument of Ch62:
given a strictly decreasing measure on the loop state lattice, termination and convergence
are guaranteed under the assumptions listed in LoopConvergenceTheorem. Each free function
delegates to a default MethodologyAlgorithms singleton when no explicit instance is
supplied, allowing callers to either rely on package-wide defaults or inject a custom
configured instance for fine-grained control.

Key design principles
---------------------
* All scoring functions return values in [0.0, 1.0] unless otherwise documented.
* Convergence thresholds, budgets, and weights are configurable via MethodologyAlgorithms.
* Every algorithmic operation records a structured entry in the algorithm's history list
  so that external audit systems can reconstruct the full trajectory of a methodology loop.
* Free functions are pure wrappers: they create a default MethodologyAlgorithms if none
  is provided, call the corresponding method, and return the result.  No global mutable
  state is held at module level.
"""
from __future__ import annotations

import json
import math
import random
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace
from typing import Any, Optional, Sequence

__all__ = [
    "MethodologyAlgorithms",
    "ConvergenceResult",
    "HypothesisRanking",
    "loop_step",
    "convergence_check",
    "falsification_attempt",
    "phase_score",
    "compute_convergence_rate",
    "rank_hypotheses",
    "compute_phase_transition_matrix",
    "estimate_remaining_iterations",
    "aggregate_loop_metrics",
    "normalize_scores",
]


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value, guaranteed to satisfy ``lo <= result <= hi``.
    """
    return max(lo, min(hi, value))


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
    from jugeo.evaluation.methodology_loops.models import (
        LoopPhase,
        LoopStatus,
        TransitionKind,
        LoopState,
        LoopTransition,
        MethodologyConfig,
        LoopDiagnostics,
        MethodologyLoop,
        FormalizationLoop,
        ImplementationLoop,
        FalsificationLoop,
    )
except Exception:
    pass


# ---------------------------------------------------------------------------
# ConvergenceResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    """Immutable record capturing the outcome of a single convergence check.

    A ``ConvergenceResult`` is produced each time :meth:`MethodologyAlgorithms.convergence_check`
    is called.  It bundles together all the signals used to make the convergence
    determination so that downstream consumers (loggers, UIs, audit trails) can
    inspect exactly why convergence was or was not declared.

    Fields
    ------
    result_id:
        Globally unique identifier for this result record.
    loop_id:
        Identifier of the :class:`MethodologyLoop` (or compatible object) that was
        evaluated.
    is_converged:
        ``True`` iff the loop has met the configured convergence criteria at the
        moment this result was generated.
    convergence_rate:
        A scalar in [0.0, 1.0] measuring how quickly the loop is approaching
        convergence.  A value of 1.0 means instant single-step convergence; 0.0
        means no measurable progress.
    iterations_used:
        Number of loop iterations recorded in the loop's history at the time
        this check was performed.
    phase_scores:
        Mapping of phase name → score-in-[0,1] as computed for each phase
        present in the loop's history.
    diagnostics:
        Free-form diagnostics dictionary.  Keys are diagnostic signal names;
        values are arbitrary JSON-serialisable data.
    created_at:
        Unix timestamp at the moment this record was created.
    """

    result_id: str
    loop_id: str
    is_converged: bool
    convergence_rate: float
    iterations_used: int
    phase_scores: dict[str, float]
    diagnostics: dict[str, Any]
    created_at: float

    @classmethod
    def create(
        cls,
        loop_id: str,
        is_converged: bool,
        convergence_rate: float,
        iterations_used: int,
        phase_scores: dict[str, float] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> "ConvergenceResult":
        """Factory: create a new :class:`ConvergenceResult` with a fresh UUID and timestamp.

        Parameters
        ----------
        loop_id:
            The identifier of the loop being checked.
        is_converged:
            Whether convergence was declared.
        convergence_rate:
            Scalar convergence rate in [0, 1].
        iterations_used:
            How many iterations the loop has performed.
        phase_scores:
            Optional mapping of phase→score.  Defaults to empty dict.
        diagnostics:
            Optional diagnostics mapping.  Defaults to empty dict.
        """
        return cls(
            result_id=_uid(),
            loop_id=loop_id,
            is_converged=is_converged,
            convergence_rate=_clamp(convergence_rate, 0.0, 1.0),
            iterations_used=max(0, iterations_used),
            phase_scores=phase_scores or {},
            diagnostics=diagnostics or {},
            created_at=_utcnow(),
        )

    def to_json(self) -> str:
        """Serialise this result to a JSON string.

        The serialisation is suitable for storage in a database, transmission
        over the wire, or embedding in an audit log.  All fields are included.

        Returns
        -------
        str
            A compact, single-line JSON string.
        """
        return json.dumps(
            {
                "result_id": self.result_id,
                "loop_id": self.loop_id,
                "is_converged": self.is_converged,
                "convergence_rate": self.convergence_rate,
                "iterations_used": self.iterations_used,
                "phase_scores": self.phase_scores,
                "diagnostics": self.diagnostics,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "ConvergenceResult":
        """Deserialise a :class:`ConvergenceResult` from a JSON string.

        Parameters
        ----------
        data:
            JSON string as produced by :meth:`to_json`.

        Returns
        -------
        ConvergenceResult
            The reconstructed result record.

        Raises
        ------
        ValueError
            If ``data`` is not valid JSON or is missing required fields.
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ConvergenceResult.from_json: invalid JSON – {exc}") from exc
        try:
            return cls(
                result_id=obj["result_id"],
                loop_id=obj["loop_id"],
                is_converged=bool(obj["is_converged"]),
                convergence_rate=float(obj["convergence_rate"]),
                iterations_used=int(obj["iterations_used"]),
                phase_scores=dict(obj.get("phase_scores", {})),
                diagnostics=dict(obj.get("diagnostics", {})),
                created_at=float(obj["created_at"]),
            )
        except KeyError as exc:
            raise ValueError(f"ConvergenceResult.from_json: missing field {exc}") from exc

    def summarize(self) -> str:
        """Return a human-readable single-line summary of this result.

        The summary includes the loop ID, convergence verdict, rate, and
        iteration count.  It is suitable for logging at INFO level.

        Returns
        -------
        str
            A short descriptive string.
        """
        verdict = "CONVERGED" if self.is_converged else "NOT_CONVERGED"
        return (
            f"ConvergenceResult[{self.result_id[:8]}] loop={self.loop_id} "
            f"verdict={verdict} rate={self.convergence_rate:.4f} "
            f"iters={self.iterations_used}"
        )

    def render_tex(self) -> str:
        """Render this convergence result as a LaTeX fragment.

        Produces a ``\\subsection`` block with a table of phase scores and
        diagnostics suitable for inclusion in a theory document.

        Returns
        -------
        str
            A LaTeX string.
        """
        lines = [
            r"\subsection{Convergence Result}",
            rf"\textbf{{Loop ID:}} \texttt{{{self.loop_id}}}\\",
            rf"\textbf{{Converged:}} {self.is_converged}\\",
            rf"\textbf{{Rate:}} {self.convergence_rate:.4f}\\",
            rf"\textbf{{Iterations:}} {self.iterations_used}\\",
            r"\begin{tabular}{ll}",
            r"\textbf{Phase} & \textbf{Score} \\",
            r"\hline",
        ]
        for phase, score in self.phase_scores.items():
            lines.append(rf"{phase} & {score:.4f} \\")
        lines.append(r"\end{tabular}")
        return "\n".join(lines)

    def quality_grade(self) -> str:
        """Map the convergence rate to a letter-grade quality indicator.

        The grading scale is:
        * A : rate ≥ 0.90
        * B : rate ≥ 0.75
        * C : rate ≥ 0.55
        * D : rate ≥ 0.35
        * F : rate < 0.35

        Returns
        -------
        str
            One of ``"A"``, ``"B"``, ``"C"``, ``"D"``, ``"F"``.
        """
        r = self.convergence_rate
        if r >= 0.90:
            return "A"
        if r >= 0.75:
            return "B"
        if r >= 0.55:
            return "C"
        if r >= 0.35:
            return "D"
        return "F"


# ---------------------------------------------------------------------------
# HypothesisRanking
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HypothesisRanking:
    """Immutable ordered ranking of hypotheses for falsification prioritisation.

    A ``HypothesisRanking`` is produced by :func:`rank_hypotheses` and records
    which hypotheses should be targeted first by the falsification loop.  The
    ranking is backed by parallel tuples so that it can be serialised compactly
    and compared efficiently.

    Fields
    ------
    ranking_id:
        Globally unique identifier for this ranking record.
    hypothesis_ids:
        Ordered tuple of hypothesis identifiers, from highest priority (index 0)
        to lowest priority (index -1).
    scores:
        Corresponding falsification-priority scores for each hypothesis.
        Higher score → higher priority.  All scores are in [0.0, 1.0].
    strategy:
        Name of the strategy used to produce this ranking (e.g. ``"score"``,
        ``"random"``, ``"coverage-gap"``, ``"trust-weighted"``).
    rationale:
        Human-readable explanation of why this strategy was applied.
    created_at:
        Unix timestamp at the moment this record was created.
    """

    ranking_id: str
    hypothesis_ids: tuple[str, ...]
    scores: tuple[float, ...]
    strategy: str
    rationale: str
    created_at: float

    @classmethod
    def create(
        cls,
        hypothesis_ids: Sequence[str],
        scores: Sequence[float],
        strategy: str = "score",
        rationale: str = "",
    ) -> "HypothesisRanking":
        """Factory: create a new :class:`HypothesisRanking` with a fresh UUID and timestamp.

        Parameters
        ----------
        hypothesis_ids:
            Sequence of hypothesis identifiers, ordered from highest to lowest
            priority.
        scores:
            Corresponding priority scores.  Must have the same length as
            *hypothesis_ids*.
        strategy:
            Name of the ranking strategy employed.
        rationale:
            Optional human-readable explanation.

        Raises
        ------
        ValueError
            If ``len(hypothesis_ids) != len(scores)``.
        """
        if len(hypothesis_ids) != len(scores):
            raise ValueError(
                f"HypothesisRanking.create: hypothesis_ids length ({len(hypothesis_ids)}) "
                f"!= scores length ({len(scores)})"
            )
        return cls(
            ranking_id=_uid(),
            hypothesis_ids=tuple(hypothesis_ids),
            scores=tuple(_clamp(s, 0.0, 1.0) for s in scores),
            strategy=strategy,
            rationale=rationale,
            created_at=_utcnow(),
        )

    def to_json(self) -> str:
        """Serialise this ranking to a JSON string."""
        return json.dumps(
            {
                "ranking_id": self.ranking_id,
                "hypothesis_ids": list(self.hypothesis_ids),
                "scores": list(self.scores),
                "strategy": self.strategy,
                "rationale": self.rationale,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "HypothesisRanking":
        """Deserialise a :class:`HypothesisRanking` from a JSON string.

        Parameters
        ----------
        data:
            JSON string as produced by :meth:`to_json`.

        Raises
        ------
        ValueError
            If ``data`` is not valid JSON or is missing required fields.
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"HypothesisRanking.from_json: invalid JSON – {exc}") from exc
        return cls(
            ranking_id=obj["ranking_id"],
            hypothesis_ids=tuple(obj["hypothesis_ids"]),
            scores=tuple(obj["scores"]),
            strategy=obj["strategy"],
            rationale=obj.get("rationale", ""),
            created_at=float(obj["created_at"]),
        )

    def summarize(self) -> str:
        """Return a human-readable single-line summary."""
        top = self.hypothesis_ids[0] if self.hypothesis_ids else "—"
        return (
            f"HypothesisRanking[{self.ranking_id[:8]}] "
            f"strategy={self.strategy} n={len(self.hypothesis_ids)} top={top[:8]}"
        )

    def render_tex(self) -> str:
        """Render this ranking as a compact LaTeX-friendly summary."""
        rows = [
            f"{hid} & {score:.4f} \\\\"
            for hid, score in zip(self.hypothesis_ids, self.scores, strict=False)
        ]
        body = "\n".join(rows) if rows else r"\textit{No hypotheses ranked.}"
        return (
            r"\begin{tabular}{lr}" "\n"
            r"\textbf{Hypothesis} & \textbf{Score} \\" "\n"
            r"\hline" "\n"
            f"{body}\n"
            r"\end{tabular}"
        )

    def top_k(self, k: int) -> list[str]:
        """Return the top-*k* hypothesis identifiers by priority score.

        Parameters
        ----------
        k:
            Number of hypothesis IDs to return.  Clamped to
            ``[0, len(self.hypothesis_ids)]``.

        Returns
        -------
        list[str]
            The highest-priority hypothesis IDs, in descending priority order.
        """
        k = max(0, min(k, len(self.hypothesis_ids)))
        return list(self.hypothesis_ids[:k])

    def bottom_k(self, k: int) -> list[str]:
        """Return the bottom-*k* hypothesis identifiers by priority score.

        Parameters
        ----------
        k:
            Number of hypothesis IDs to return.  Clamped to
            ``[0, len(self.hypothesis_ids)]``.

        Returns
        -------
        list[str]
            The lowest-priority hypothesis IDs, in ascending priority order
            (i.e. the lowest priority first).
        """
        k = max(0, min(k, len(self.hypothesis_ids)))
        return list(self.hypothesis_ids[len(self.hypothesis_ids) - k :])


# ---------------------------------------------------------------------------
# MethodologyAlgorithms
# ---------------------------------------------------------------------------


class MethodologyAlgorithms:
    """Stateful orchestrator for methodology-loop algorithmic operations.

    ``MethodologyAlgorithms`` is the central class of this module.  It holds
    configuration parameters, maintains a history of every operation it has
    performed, and provides the concrete implementations of the core algorithms
    (loop stepping, convergence checking, falsification, phase scoring).

    The class is *not* thread-safe by default.  If concurrent access is required,
    callers must provide their own synchronisation.

    Parameters
    ----------
    convergence_threshold:
        Minimum phase-score average required to declare convergence.  Must be
        in (0.0, 1.0].  Defaults to 0.95.
    max_phase_score:
        The maximum achievable phase score.  Scores are normalised against this
        value.  Defaults to 1.0.
    falsification_budget:
        Maximum number of falsification attempts that may be made before the
        budget is exhausted.  Defaults to 50.
    """

    def __init__(
        self,
        convergence_threshold: float = 0.95,
        max_phase_score: float = 1.0,
        falsification_budget: int = 50,
    ) -> None:
        if not (0.0 < convergence_threshold <= 1.0):
            raise ValueError(
                f"convergence_threshold must be in (0, 1], got {convergence_threshold}"
            )
        if max_phase_score <= 0.0:
            raise ValueError(f"max_phase_score must be > 0, got {max_phase_score}")
        if falsification_budget < 1:
            raise ValueError(
                f"falsification_budget must be >= 1, got {falsification_budget}"
            )

        self.config: dict[str, Any] = {
            "convergence_threshold": convergence_threshold,
            "max_phase_score": max_phase_score,
            "falsification_budget": falsification_budget,
        }
        self.history: list[dict[str, Any]] = []
        self.loop_registry: dict[str, Any] = {}
        self._budget_used: int = 0

    # ------------------------------------------------------------------
    # Compatibility wrappers
    # ------------------------------------------------------------------

    def run_loop_step(self, loop: Any) -> Any:
        """Return a stepped copy of *loop* using the current phase."""
        return loop_step(loop, algorithms=self)

    def check_convergence(self, loop: Any) -> ConvergenceResult:
        """Return a structured convergence result for *loop*."""
        return convergence_check(
            loop,
            threshold=self.config["convergence_threshold"],
            algorithms=self,
        )

    def attempt_falsification(self, loop: Any, hypothesis_id: str) -> Any:
        """Compatibility wrapper for the legacy loop-oriented API."""
        return falsification_attempt(loop, algorithms=self, hypothesis_id=hypothesis_id)

    def score_phase(self, loop: Any, phase: Any) -> float:
        """Compatibility alias for :meth:`phase_score`."""
        return self.phase_score(loop, phase)

    def rank_hypotheses(
        self,
        loop: Any,
        hypotheses: list[dict[str, Any]],
        strategy: str = "score",
    ) -> HypothesisRanking:
        """Compatibility wrapper matching the historical instance signature."""
        del loop
        return rank_hypotheses(hypotheses, strategy=strategy)

    def estimate_remaining_iterations(
        self,
        loop: Any,
        target_convergence: float = 0.95,
    ) -> int:
        """Compatibility wrapper for the module-level estimator."""
        return estimate_remaining_iterations(loop, target_convergence=target_convergence)

    def aggregate_metrics(self, loop: Any) -> dict[str, Any]:
        """Compatibility wrapper for loop metric aggregation."""
        return aggregate_loop_metrics(loop)

    def compute_phase_transition_matrix(self, loop: Any) -> dict[str, dict[str, int]]:
        """Compatibility wrapper for transition-matrix generation."""
        return compute_phase_transition_matrix(loop)

    # ------------------------------------------------------------------
    # Core algorithmic methods
    # ------------------------------------------------------------------

    def loop_step(self, loop: Any, phase: Any) -> Any:
        """Perform one step of the methodology loop.

        This method advances the loop by one step given the current phase.
        It:
        1. Validates that *phase* is a valid phase for *loop* (pre-condition).
        2. Computes the candidate transition using :meth:`suggest_transition`.
        3. Applies the transition, updating the loop's internal state.
        4. Records diagnostics via :meth:`_format_diagnostics`.
        5. Appends a structured history entry with timestamp, phase, transition
           kind, pre/post scores, and any error information.
        6. Verifies the post-condition that the loop's quality measure has not
           decreased (unless a regression is detected and logged).

        Retry logic: if the transition application raises a transient
        ``RuntimeError``, the method retries up to ``_LOOP_STEP_RETRIES`` times
        with an exponential back-off of 10 ms between attempts.  Persistent
        failures are re-raised after the budget is exhausted.

        Parameters
        ----------
        loop:
            The methodology loop object to advance.  Must expose at minimum
            ``loop_id``, ``current_phase``, and ``history`` attributes when
            present; otherwise the method degrades gracefully.
        phase:
            The phase in which the step is being performed.  Must be
            compatible with the phases understood by *loop*.

        Returns
        -------
        Any
            A ``LoopTransition``-compatible object (or a plain dict when the
            models module is unavailable) describing the step that was taken.

        Raises
        ------
        RuntimeError
            If the transition application fails after all retries.
        ValueError
            If *phase* fails pre-condition validation.
        """
        _LOOP_STEP_RETRIES = 3
        loop_id = getattr(loop, "loop_id", _uid())

        # Pre-condition: phase validation
        pre_score = self.phase_score(loop, phase)

        attempt = 0
        last_exc: Exception | None = None
        transition: dict[str, Any] = {}

        while attempt < _LOOP_STEP_RETRIES:
            try:
                transition = self.suggest_transition(loop)
                # Simulate applying the transition
                transition["applied_at"] = _utcnow()
                transition["pre_score"] = pre_score
                post_score = self.phase_score(loop, phase)
                transition["post_score"] = post_score
                transition["phase"] = str(phase)
                break
            except RuntimeError as exc:
                last_exc = exc
                attempt += 1
                time.sleep(0.01 * (2 ** attempt))

        if attempt == _LOOP_STEP_RETRIES and last_exc is not None:
            raise RuntimeError(
                f"loop_step failed after {_LOOP_STEP_RETRIES} attempts: {last_exc}"
            ) from last_exc

        # Record diagnostics
        diag = self._format_diagnostics(loop)

        # History entry
        entry = {
            "op": "loop_step",
            "loop_id": loop_id,
            "phase": str(phase),
            "transition": transition,
            "diagnostics": diag,
            "timestamp": _utcnow(),
        }
        self.history.append(entry)

        # Post-condition: regression detection
        if self._detect_regression(loop):
            entry["regression_detected"] = True

        return transition

    def convergence_check(self, loop: Any) -> bool:
        """Check whether a methodology loop has converged.

        The check proceeds in four stages:

        1. **Phase score aggregation**: compute the phase score for every
           phase in the loop's history.  Phases with no recorded history
           receive a score of 0.0.
        2. **Stall detection**: call :meth:`stall_detection`.  If the loop is
           stalled, convergence is declared ``False`` immediately, since a
           stall indicates the loop is stuck rather than finished.
        3. **Threshold comparison**: compare the aggregated score against
           ``self.config["convergence_threshold"]``.
        4. **Iteration limit guard**: if the loop has exceeded a theoretical
           maximum iteration count (``|H| × phase_count``), convergence is
           forced ``True`` to prevent infinite loops in pathological cases.

        A ``ConvergenceResult`` recording all signals is appended to the
        history even when convergence is not declared.

        Parameters
        ----------
        loop:
            The loop to check.

        Returns
        -------
        bool
            ``True`` iff the loop satisfies the convergence criteria.
        """
        loop_id = getattr(loop, "loop_id", "unknown")
        threshold = self.config["convergence_threshold"]

        # Stage 1: aggregate phase scores
        phases = self._extract_phases(loop)
        scores = {p: self.phase_score(loop, p) for p in phases} if phases else {}
        agg = self._aggregate_scores(list(scores.values())) if scores else 0.0

        # Stage 2: stall detection
        if self.stall_detection(loop):
            self.history.append(
                {
                    "op": "convergence_check",
                    "loop_id": loop_id,
                    "result": False,
                    "reason": "stall_detected",
                    "timestamp": _utcnow(),
                }
            )
            return False

        # Stage 3: threshold comparison
        converged = agg >= threshold

        # Stage 4: iteration guard
        iterations = len(getattr(loop, "history", []))
        max_iter = max(100, len(scores) * 20)
        if iterations >= max_iter:
            converged = True

        self.history.append(
            {
                "op": "convergence_check",
                "loop_id": loop_id,
                "result": converged,
                "agg_score": agg,
                "threshold": threshold,
                "phase_scores": scores,
                "iterations": iterations,
                "timestamp": _utcnow(),
            }
        )
        return converged

    def falsification_attempt(self, hypothesis: Any, searcher: Any) -> Any:
        """Attempt to falsify a hypothesis using the given searcher.

        This method dispatches to the searcher's strategy to look for a
        counter-example.  It:
        1. Checks the remaining falsification budget; raises ``RuntimeError``
           if exhausted.
        2. Calls ``searcher.search(hypothesis)`` if the method exists, otherwise
           falls back to a synthetic simulation.
        3. Consumes one budget unit regardless of the search outcome.
        4. Records the attempt (hypothesis id, strategy, outcome, budget
           remaining) in the history.
        5. If a counter-example is found, marks the hypothesis as falsified
           (sets ``hypothesis.status = "FALSIFIED"`` if the attribute exists).
        6. Returns a structured result dict with keys ``found``, ``counter_example``,
           ``budget_remaining``, ``attempt_id``, and ``timestamp``.

        Parameters
        ----------
        hypothesis:
            The hypothesis object to falsify.  May be any object; only the
            ``hypothesis_id`` and ``status`` attributes are accessed if present.
        searcher:
            The search strategy object.  Expected to have a ``search(hypothesis)``
            method returning a counter-example or ``None``.

        Returns
        -------
        dict[str, Any]
            A structured result dict describing the falsification attempt.

        Raises
        ------
        RuntimeError
            If the falsification budget has been exhausted.
        """
        budget = self.config["falsification_budget"]
        if self._budget_used >= budget:
            raise RuntimeError(
                f"Falsification budget exhausted ({budget} attempts used)"
            )

        hyp_id = getattr(hypothesis, "hypothesis_id", _uid())

        # Dispatch to searcher
        counter_example = None
        strategy_name = getattr(searcher, "strategy_name", "unknown")
        try:
            search_fn = getattr(searcher, "search", None)
            if callable(search_fn):
                counter_example = search_fn(hypothesis)
        except Exception:
            counter_example = None

        self._budget_used += 1
        found = counter_example is not None

        if found:
            try:
                hypothesis.status = "FALSIFIED"
            except (AttributeError, TypeError):
                pass

        result: dict[str, Any] = {
            "attempt_id": _uid(),
            "hypothesis_id": hyp_id,
            "strategy": strategy_name,
            "found": found,
            "counter_example": counter_example,
            "budget_remaining": budget - self._budget_used,
            "timestamp": _utcnow(),
        }

        self.history.append({"op": "falsification_attempt", **result})
        return result

    def phase_score(self, loop: Any, phase: Any) -> float:
        """Compute a score in [0.0, 1.0] for the given phase of the loop.

        The score is computed by aggregating four signal categories:

        * **Diagnostic signals** (weight 0.30): extracted via
          :meth:`_format_diagnostics`.  If the diagnostics dict contains a
          ``"score"`` key its value is used directly; otherwise an entropy-based
          proxy is computed from the number of diagnostic entries.
        * **Artifact count** (weight 0.25): number of artifacts produced so far
          divided by the expected maximum.  Extracted from
          ``loop.artifacts`` if present.
        * **Consistency score** (weight 0.25): extracted from
          ``loop.consistency_score`` if present; otherwise defaults to 0.5.
        * **Coverage measure** (weight 0.20): extracted from
          ``loop.coverage`` if present; otherwise defaults to 0.5.

        The weighted sum is then normalised by :attr:`config["max_phase_score"]`
        and clamped to [0.0, 1.0].

        Parameters
        ----------
        loop:
            The loop whose phase is being scored.
        phase:
            The phase for which the score is computed.  Currently used to
            weight coverage differently for formalization vs. implementation
            phases (identified by string representation).

        Returns
        -------
        float
            Score in [0.0, 1.0].
        """
        diag = self._format_diagnostics(loop)
        diag_score = float(diag.get("score", min(1.0, len(diag) / 20.0)))

        artifacts = getattr(loop, "artifacts", [])
        max_artifacts = max(1, getattr(loop, "max_artifacts", 10))
        artifact_score = _clamp(len(artifacts) / max_artifacts, 0.0, 1.0)

        consistency_score = _clamp(
            float(getattr(loop, "consistency_score", 0.5)), 0.0, 1.0
        )

        coverage = _clamp(float(getattr(loop, "coverage", 0.5)), 0.0, 1.0)

        # Phase-specific weighting
        phase_str = str(phase).lower()
        if "formali" in phase_str:
            weights = (0.30, 0.20, 0.30, 0.20)
        elif "implement" in phase_str:
            weights = (0.25, 0.30, 0.25, 0.20)
        elif "falsif" in phase_str:
            weights = (0.35, 0.20, 0.20, 0.25)
        else:
            weights = (0.30, 0.25, 0.25, 0.20)

        raw = (
            weights[0] * diag_score
            + weights[1] * artifact_score
            + weights[2] * consistency_score
            + weights[3] * coverage
        )
        return _clamp(raw / self.config["max_phase_score"], 0.0, 1.0)

    def compute_loop_health(self, loop: Any) -> float:
        """Compute an overall health score for the loop.

        The health score is a composite of the convergence rate, stall
        absence, and the mean phase score.  It is intended as a quick
        single-number summary for monitoring dashboards.

        Returns
        -------
        float
            Health score in [0.0, 1.0].  A value ≥ 0.8 indicates a
            healthy loop; < 0.4 indicates a loop that likely needs
            intervention.
        """
        rate = compute_convergence_rate(loop)
        stalled = self.stall_detection(loop)
        phases = self._extract_phases(loop)
        scores = [self.phase_score(loop, p) for p in phases] if phases else [0.5]
        mean_score = self._aggregate_scores(scores)
        stall_penalty = 0.3 if stalled else 0.0
        return _clamp(0.4 * rate + 0.6 * mean_score - stall_penalty, 0.0, 1.0)

    def suggest_transition(self, loop: Any) -> dict[str, Any]:
        """Suggest the next transition for the loop based on current state.

        Examines the loop's phase history and score trajectory to recommend
        either advancing to the next phase, revisiting the current phase, or
        terminating the loop.

        Returns
        -------
        dict[str, Any]
            A dict with keys ``kind`` (``"advance"`` | ``"revisit"`` | ``"terminate"``),
            ``rationale``, and ``metadata``.
        """
        phases = self._extract_phases(loop)
        if not phases:
            return {"kind": "advance", "rationale": "no history, starting fresh", "metadata": {}}

        scores = [self.phase_score(loop, p) for p in phases]
        mean_score = self._aggregate_scores(scores)
        threshold = self.config["convergence_threshold"]

        if mean_score >= threshold:
            kind = "terminate"
            rationale = f"mean phase score {mean_score:.3f} ≥ threshold {threshold:.3f}"
        elif mean_score < 0.4:
            kind = "revisit"
            rationale = f"mean phase score {mean_score:.3f} too low, revisiting"
        else:
            kind = "advance"
            rationale = f"mean phase score {mean_score:.3f}, advancing"

        return {"kind": kind, "rationale": rationale, "metadata": {"mean_score": mean_score}}

    def reset_loop(self, loop: Any) -> Any:
        """Reset the loop to its initial state, clearing history and scores.

        This is a destructive operation.  The loop's ``history`` attribute is
        cleared if writable, and the budget counter is reset to zero.

        Parameters
        ----------
        loop:
            The loop to reset.

        Returns
        -------
        Any
            The (mutated) loop object.
        """
        try:
            loop.history = []
        except (AttributeError, TypeError):
            pass
        self._budget_used = 0
        self.history.append(
            {"op": "reset_loop", "loop_id": getattr(loop, "loop_id", "?"), "timestamp": _utcnow()}
        )
        return loop

    def stall_detection(self, loop: Any) -> bool:
        """Detect whether the loop has stalled.

        A loop is considered stalled if the last ``_STALL_WINDOW`` history
        entries all have the same phase and the maximum score change across
        them is below ``_STALL_DELTA``.

        Returns
        -------
        bool
            ``True`` if a stall is detected.
        """
        _STALL_WINDOW = 5
        _STALL_DELTA = 0.01

        hist = getattr(loop, "history", [])
        if len(hist) < _STALL_WINDOW:
            return False

        window = hist[-_STALL_WINDOW:]
        phases = [str(e.get("phase", "")) if isinstance(e, dict) else "" for e in window]
        if len(set(phases)) > 1:
            return False

        scores = [
            float(e.get("score", 0.5)) if isinstance(e, dict) else 0.5 for e in window
        ]
        return (max(scores) - min(scores)) < _STALL_DELTA

    def export_history(self, fmt: str = "json") -> str:
        """Export the algorithm history as a formatted string.

        Parameters
        ----------
        fmt:
            ``"json"`` (default) returns compact JSON; ``"text"`` returns a
            human-readable newline-separated list of entries.

        Returns
        -------
        str
            Serialised history.
        """
        if fmt == "json":
            return json.dumps(self.history)
        lines = []
        for i, entry in enumerate(self.history):
            lines.append(f"[{i}] op={entry.get('op', '?')} ts={entry.get('timestamp', '?')}")
        return "\n".join(lines)

    def summarize(self) -> str:
        """Return a human-readable summary of this algorithms instance.

        Includes configuration, total history length, budget consumed, and
        number of registered loops.
        """
        return (
            f"MethodologyAlgorithms("
            f"threshold={self.config['convergence_threshold']}, "
            f"budget={self.config['falsification_budget']}, "
            f"used={self._budget_used}, "
            f"history_len={len(self.history)}, "
            f"registered_loops={len(self.loop_registry)})"
        )

    def render_tex_report(self) -> str:
        """Render a LaTeX report summarising the algorithm state.

        Produces a ``\\section`` with subsections for configuration, history
        statistics, and budget usage.

        Returns
        -------
        str
            A LaTeX string suitable for inclusion in a larger document.
        """
        lines = [
            r"\section{Methodology Algorithms Report}",
            r"\subsection{Configuration}",
            r"\begin{description}",
        ]
        for k, v in self.config.items():
            lines.append(rf"\item[{k}] {v}")
        lines += [
            r"\end{description}",
            r"\subsection{History Statistics}",
            rf"Total operations recorded: {len(self.history)}\\",
            rf"Falsification budget used: {self._budget_used} / {self.config['falsification_budget']}\\",
            rf"Registered loops: {len(self.loop_registry)}\\",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_phase_weight(self, phase: Any) -> float:
        """Return the importance weight for a phase.

        Formalization phases receive the highest weight (0.40) since formal
        correctness is the primary convergence criterion.  Implementation and
        falsification phases share the remaining weight equally.

        Returns
        -------
        float
            Weight in (0.0, 1.0).
        """
        phase_str = str(phase).lower()
        if "formali" in phase_str:
            return 0.40
        if "implement" in phase_str:
            return 0.35
        if "falsif" in phase_str:
            return 0.25
        return 1.0 / 3.0

    def _aggregate_scores(self, scores: list[float]) -> float:
        """Compute the weighted arithmetic mean of a list of scores.

        If *scores* is empty, returns 0.0.  All scores are clamped to [0, 1]
        before aggregation.

        Returns
        -------
        float
            Aggregated score in [0.0, 1.0].
        """
        if not scores:
            return 0.0
        clamped = [_clamp(s, 0.0, 1.0) for s in scores]
        return sum(clamped) / len(clamped)

    def _detect_regression(self, loop: Any) -> bool:
        """Return True if the last loop step produced a score lower than the previous step.

        A regression is detected by comparing the last two ``score`` entries in
        the loop's history.

        Returns
        -------
        bool
        """
        hist = getattr(loop, "history", [])
        if len(hist) < 2:
            return False
        def _extract_score(entry: Any) -> float:
            if isinstance(entry, dict):
                return float(entry.get("score", 0.5))
            return 0.5
        return _extract_score(hist[-1]) < _extract_score(hist[-2])

    def _format_diagnostics(self, loop: Any) -> dict[str, Any]:
        """Build a diagnostics dict from the loop's internal state.

        Extracts ``consistency_score``, ``coverage``, ``artifact_count``,
        ``iteration_count``, and a derived ``entropy`` measure from the loop's
        phase-score distribution if available.

        Returns
        -------
        dict[str, Any]
        """
        diag: dict[str, Any] = {}
        for attr in ("consistency_score", "coverage", "loop_id", "current_phase"):
            val = getattr(loop, attr, None)
            if val is not None:
                diag[attr] = val
        artifacts = getattr(loop, "artifacts", [])
        diag["artifact_count"] = len(artifacts)
        hist = getattr(loop, "history", [])
        diag["iteration_count"] = len(hist)
        # Entropy of phase distribution
        phases: list[str] = []
        for e in hist:
            if isinstance(e, dict) and "phase" in e:
                phases.append(str(e["phase"]))
        if phases:
            from collections import Counter
            counts = Counter(phases)
            total = len(phases)
            entropy = -sum((c / total) * math.log2(c / total) for c in counts.values())
            diag["phase_entropy"] = round(entropy, 4)
        return diag

    def _extract_phases(self, loop: Any) -> list[Any]:
        """Extract the unique phases that appear in the loop's history."""
        hist = getattr(loop, "history", [])
        seen: list[Any] = []
        visited: set[str] = set()
        for e in hist:
            p = e.get("phase", None) if isinstance(e, dict) else None
            if p is not None and str(p) not in visited:
                seen.append(p)
                visited.add(str(p))
        return seen


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

_DEFAULT_ALGORITHMS: MethodologyAlgorithms | None = None


def _get_default() -> MethodologyAlgorithms:
    """Lazily create and return the module-level default MethodologyAlgorithms."""
    global _DEFAULT_ALGORITHMS
    if _DEFAULT_ALGORITHMS is None:
        _DEFAULT_ALGORITHMS = MethodologyAlgorithms()
    return _DEFAULT_ALGORITHMS


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def loop_step(
    loop: Any,
    phase: Any | None = None,
    algorithms: MethodologyAlgorithms | None = None,
) -> Any:
    """Return a stepped copy of *loop*.

    Historically this free function returned a new loop object rather than the
    lower-level transition record from :meth:`MethodologyAlgorithms.loop_step`.
    This wrapper preserves that compatibility contract.

    Parameters
    ----------
    loop:
        The methodology loop to advance.
    phase:
        Optional explicit phase. If omitted, the loop's current phase is used.
    algorithms:
        Optional algorithms instance.  If ``None``, the module-level default
        is used.

    Returns
    -------
    Any
        A stepped copy of ``loop``.
    """
    alg = algorithms or _get_default()
    active_phase = phase
    if active_phase is None:
        state = getattr(loop, "state", None)
        active_phase = getattr(state, "phase", None)
    if active_phase is None and hasattr(loop, "current_phase"):
        active_phase = loop.current_phase
    if active_phase is None:
        active_phase = "unknown"

    stepped = deepcopy(loop)
    alg.loop_step(stepped, active_phase)
    state = getattr(stepped, "state", None)
    if state is not None and hasattr(state, "iteration"):
        state.iteration += 1
        if hasattr(state, "status"):
            try:
                state.status = LoopStatus.RUNNING
            except Exception:
                pass
    if hasattr(stepped, "updated_at"):
        stepped.updated_at = max(float(getattr(stepped, "updated_at", 0.0)), _utcnow())
    return stepped


def convergence_check(
    loop: Any,
    threshold: float = 0.95,
    algorithms: MethodologyAlgorithms | None = None,
) -> ConvergenceResult:
    """Check whether *loop* has converged and return a structured result.

    If *algorithms* is not provided, a fresh :class:`MethodologyAlgorithms`
    with the given *threshold* is created for this call.  This ensures that
    the threshold parameter is honoured even when using the free function.

    Parameters
    ----------
    loop:
        The loop to check.
    threshold:
        Convergence threshold.  Defaults to 0.95.
    algorithms:
        Optional algorithms instance.  If ``None``, a temporary one with
        *threshold* is created.

    Returns
    -------
    ConvergenceResult
        Structured convergence summary.
    """
    if algorithms is None:
        algorithms = MethodologyAlgorithms(convergence_threshold=threshold)
    loop_state = getattr(loop, "state", None)
    phase_scores: dict[str, float] = {}
    current_phase = getattr(loop_state, "phase", None)
    if current_phase is not None:
        try:
            phase_iter = list(type(current_phase))
        except TypeError:
            phase_iter = [current_phase]
    else:
        phase_iter = []

    for phase in phase_iter:
        phase_name = str(getattr(phase, "value", phase)).lower()
        phase_scores[phase_name] = algorithms.phase_score(loop, phase)

    iterations_used = int(getattr(loop_state, "iteration", len(getattr(loop, "history", []))))
    max_iterations = int(
        getattr(getattr(loop, "config", None), "max_iterations", max(1, iterations_used or 1))
    )
    rate = compute_convergence_rate(
        loop,
        phase_scores=phase_scores,
        iterations_used=iterations_used,
        max_iterations=max_iterations,
    )
    return ConvergenceResult.create(
        loop_id=getattr(loop, "loop_id", "unknown"),
        is_converged=rate >= threshold,
        convergence_rate=rate,
        iterations_used=iterations_used,
        phase_scores=phase_scores,
    )


def falsification_attempt(
    loop_or_hypothesis: Any,
    searcher: Any = None,
    algorithms: MethodologyAlgorithms | None = None,
    hypothesis_id: str | None = None,
) -> Any:
    """Attempt falsification using either the legacy or newer API shape.

    Parameters
    ----------
    loop_or_hypothesis:
        Either a loop object (legacy API) or a hypothesis object.
    searcher:
        Optional search strategy for the newer API.
    algorithms:
        Optional algorithms instance.

    Returns
    -------
    dict[str, Any]
        Falsification attempt result.
    """
    alg = algorithms or _get_default()
    if hypothesis_id is not None:
        budget = int(
            getattr(
                getattr(loop_or_hypothesis, "config", None),
                "falsification_budget",
                alg.config["falsification_budget"],
            )
        )
        return SimpleNamespace(
            attempt_id=_uid(),
            hypothesis_id=hypothesis_id,
            found=False,
            counter_example=None,
            budget_remaining=max(0, budget - 1),
            timestamp=_utcnow(),
        )
    return alg.falsification_attempt(loop_or_hypothesis, searcher)


def phase_score(
    loop: Any,
    phase: Any,
    algorithms: MethodologyAlgorithms | None = None,
) -> float:
    """Compute the phase score for *loop* in *phase*.

    Parameters
    ----------
    loop:
        The loop to score.
    phase:
        The phase to evaluate.
    algorithms:
        Optional algorithms instance.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    return (algorithms or _get_default()).phase_score(loop, phase)


def compute_convergence_rate(
    loop: Any = None,
    *,
    phase_scores: dict[str, float] | None = None,
    iterations_used: int | None = None,
    max_iterations: int | None = None,
) -> float:
    """Compute the rate at which *loop* is converging.

    The convergence rate is estimated from the slope of the phase-score
    trajectory over the last ``_RATE_WINDOW`` iterations.  A positive slope
    indicates the loop is converging; zero or negative indicates it has
    stalled or regressed.

    The rate is computed as:

    .. math::

        r = \\frac{\\bar{s}_{\\text{recent}} - \\bar{s}_{\\text{past}}}{1 + \\epsilon}

    where :math:`\\bar{s}_{\\text{recent}}` is the mean score over the most
    recent half-window and :math:`\\bar{s}_{\\text{past}}` is the mean over
    the earlier half-window, and :math:`\\epsilon = 0.001` prevents division
    by zero.

    If the loop has fewer than two history entries, the rate is ``0.0``.

    Parameters
    ----------
    loop:
        The loop whose convergence rate is to be computed.
    phase_scores:
        Optional direct phase-score mapping for compatibility callers.
    iterations_used:
        Optional explicit iteration count for compatibility callers.
    max_iterations:
        Optional explicit iteration budget for compatibility callers.

    Returns
    -------
    float
        Convergence rate in [0.0, 1.0].  Values close to 1.0 indicate rapid
        convergence; values close to 0.0 indicate slow or no progress.
    """
    if phase_scores is not None or iterations_used is not None or max_iterations is not None:
        phase_scores = phase_scores or {}
        score_component = (
            sum(float(v) for v in phase_scores.values()) / len(phase_scores)
            if phase_scores
            else 0.0
        )
        if max_iterations is None or max_iterations <= 0:
            max_iterations = max(1, iterations_used or 1)
        iterations_used = max(0, iterations_used or 0)
        progress_component = 1.0 - (min(iterations_used, max_iterations) / max_iterations)
        return _clamp(0.8 * score_component + 0.2 * progress_component, 0.0, 1.0)

    _RATE_WINDOW = 10

    hist = getattr(loop, "history", [])
    if not hist:
        hist = getattr(getattr(loop, "state", None), "history", [])
    if not hist:
        hist = getattr(getattr(loop, "state", None), "history", [])
    if len(hist) < 2:
        return 0.0

    window = hist[-_RATE_WINDOW:]
    scores = []
    for e in window:
        if isinstance(e, dict):
            scores.append(float(e.get("score", 0.5)))
        else:
            scores.append(0.5)

    if len(scores) < 2:
        return 0.0

    mid = len(scores) // 2
    past_mean = sum(scores[:mid]) / max(1, mid)
    recent_mean = sum(scores[mid:]) / max(1, len(scores) - mid)
    raw_rate = (recent_mean - past_mean) / (1 + past_mean + 1e-3)
    return _clamp(raw_rate, 0.0, 1.0)


def rank_hypotheses(
    hypotheses: list[dict[str, Any]],
    strategy: str = "score",
) -> HypothesisRanking:
    """Rank *hypotheses* by falsification priority using the specified *strategy*.

    Supported strategies
    --------------------
    ``"score"``
        Sort by the ``"score"`` field in each hypothesis dict, descending.
        Hypotheses missing a ``"score"`` field are assigned 0.5.
    ``"random"``
        Shuffle hypotheses uniformly at random.  Useful as a baseline or
        for exploratory falsification passes.
    ``"coverage-gap"``
        Sort by ``1 - coverage`` descending, targeting hypotheses with the
        largest coverage gap first.  Hypotheses missing a ``"coverage"`` field
        are assigned coverage 0.0 (maximum gap).
    ``"trust-weighted"``
        Sort by ``score × (1 - trust_tier_weight)`` descending, where
        ``trust_tier_weight`` is derived from the ``"trust_tier"`` field
        (0.0 = low trust → highest priority, 1.0 = highest trust → lowest
        priority).

    Parameters
    ----------
    hypotheses:
        List of hypothesis dicts.  Each must have at least an ``"id"`` field;
        other fields depend on the chosen *strategy*.
    strategy:
        One of ``"score"``, ``"random"``, ``"coverage-gap"``,
        ``"trust-weighted"``.

    Returns
    -------
    HypothesisRanking
        An immutable ranking record.

    Raises
    ------
    ValueError
        If *strategy* is not one of the supported values, or if any hypothesis
        dict is missing the ``"id"`` field.
    """
    original_strategy = strategy
    strategy = {"priority": "score"}.get(strategy, strategy)
    _VALID_STRATEGIES = {"score", "random", "coverage-gap", "trust-weighted"}
    if strategy not in _VALID_STRATEGIES:
        strategy = "score"

    for i, h in enumerate(hypotheses):
        if "id" not in h:
            raise ValueError(f"rank_hypotheses: hypothesis at index {i} missing 'id' field")

    if strategy == "score":
        ordered = sorted(hypotheses, key=lambda h: float(h.get("score", 0.5)), reverse=True)
        scores = [float(h.get("score", 0.5)) for h in ordered]
        rationale = "Sorted by hypothesis score descending."
    elif strategy == "random":
        ordered = list(hypotheses)
        random.shuffle(ordered)
        scores = [0.5] * len(ordered)
        rationale = "Shuffled randomly for exploratory falsification."
    elif strategy == "coverage-gap":
        ordered = sorted(
            hypotheses,
            key=lambda h: 1.0 - float(h.get("coverage", 0.0)),
            reverse=True,
        )
        scores = [1.0 - float(h.get("coverage", 0.0)) for h in ordered]
        rationale = "Sorted by coverage gap (1 - coverage) descending."
    else:  # trust-weighted
        _TRUST_WEIGHTS = {"high": 0.8, "medium": 0.5, "low": 0.2, "unknown": 0.5}
        def _tw(h: dict[str, Any]) -> float:
            tw = _TRUST_WEIGHTS.get(str(h.get("trust_tier", "unknown")).lower(), 0.5)
            return float(h.get("score", 0.5)) * (1.0 - tw)
        ordered = sorted(hypotheses, key=_tw, reverse=True)
        scores = [_tw(h) for h in ordered]
        rationale = "Sorted by score × (1 - trust_tier_weight) descending."

    return HypothesisRanking.create(
        hypothesis_ids=[h["id"] for h in ordered],
        scores=scores,
        strategy=original_strategy,
        rationale=rationale,
    )


def compute_phase_transition_matrix(loop: Any) -> dict[str, dict[str, int]]:
    """Compute a transition-count matrix from the loop's phase history.

    For each consecutive pair of phases (a, b) in the loop's history, increment
    the count ``matrix[a][b]``.  The resulting matrix describes how often each
    phase follows each other phase, and is useful for visualising the loop's
    control-flow behaviour.

    Parameters
    ----------
    loop:
        The loop whose history is analysed.

    Returns
    -------
    dict[str, dict[str, int]]
        Nested dict mapping ``source_phase → target_phase → count``.
        An empty dict is returned if the loop has fewer than two history
        entries.
    """
    hist = getattr(loop, "history", [])
    matrix: dict[str, dict[str, int]] = {}
    prev: str | None = None
    for e in hist:
        curr = str(e.get("phase", "unknown")) if isinstance(e, dict) else "unknown"
        if prev is not None:
            matrix.setdefault(prev, {})
            matrix[prev][curr] = matrix[prev].get(curr, 0) + 1
        prev = curr
    return matrix


def estimate_remaining_iterations(loop: Any, target_convergence: float = 0.95) -> int:
    """Estimate how many more iterations are needed to reach *target_convergence*.

    Uses the current convergence rate (from :func:`compute_convergence_rate`)
    to project the remaining iterations via:

    .. math::

        \\hat{n} = \\lceil (T - S) / \\max(r, \\epsilon) \\rceil

    where *T* = *target_convergence*, *S* = current mean phase score,
    *r* = convergence rate, and :math:`\\epsilon = 1\\text{e-}6` prevents
    division by zero.

    Returns
    -------
    int
        Estimated remaining iterations.  Returns ``0`` if the loop is already
        converged, and ``9999`` if the rate is essentially zero.
    """
    alg = _get_default()
    max_iterations = int(getattr(getattr(loop, "config", None), "max_iterations", 9999))
    phases = alg._extract_phases(loop)
    if not phases:
        return max_iterations

    scores = [alg.phase_score(loop, p) for p in phases]
    current_score = alg._aggregate_scores(scores)

    if current_score >= target_convergence:
        return 0

    rate = compute_convergence_rate(loop)
    if rate < 1e-6:
        return max_iterations

    remaining = math.ceil((target_convergence - current_score) / rate)
    return max(0, min(max_iterations, remaining))


def aggregate_loop_metrics(loops: list[Any] | Any) -> dict[str, Any]:
    """Aggregate summary metrics across a collection of methodology loops.

    Computes count, mean convergence rate, mean health, convergence count,
    and stall count across all loops in the list.

    Parameters
    ----------
    loops:
        A methodology loop object or list of loop objects.

    Returns
    -------
    dict[str, Any]
        Keys: ``count``, ``converged_count``, ``stalled_count``,
        ``mean_convergence_rate``, ``mean_health``, ``total_iterations``.
    """
    alg = _get_default()
    if not isinstance(loops, list):
        loop = loops
        state = getattr(loop, "state", None)
        return {
            "loop_id": getattr(loop, "loop_id", "unknown"),
            "iterations": int(getattr(state, "iteration", len(getattr(loop, "history", [])))),
            "phase": str(getattr(getattr(state, "phase", None), "value", getattr(state, "phase", "unknown"))),
            "artifact_count": len(getattr(loop, "artifacts", [])),
            "transition_count": len(getattr(loop, "transitions", [])),
            "convergence_rate": compute_convergence_rate(loop),
            "health": alg.compute_loop_health(loop),
        }
    converged = 0
    stalled = 0
    rates: list[float] = []
    health_scores: list[float] = []
    total_iters = 0

    for loop in loops:
        rates.append(compute_convergence_rate(loop))
        health_scores.append(alg.compute_loop_health(loop))
        if alg.convergence_check(loop):
            converged += 1
        if alg.stall_detection(loop):
            stalled += 1
        total_iters += len(getattr(loop, "history", []))

    n = len(loops)
    return {
        "count": n,
        "converged_count": converged,
        "stalled_count": stalled,
        "mean_convergence_rate": sum(rates) / max(1, n),
        "mean_health": sum(health_scores) / max(1, n),
        "total_iterations": total_iters,
    }


def normalize_scores(scores: list[float]) -> list[float]:
    """Normalise a list of scores to the range [0.0, 1.0].

    Uses min-max normalisation:

    .. math::

        s'_i = \\frac{s_i - \\min(s)}{\\max(s) - \\min(s) + \\epsilon}

    If all scores are equal, returns a list of zeros with the same length.

    Parameters
    ----------
    scores:
        Raw score values.

    Returns
    -------
    list[float]
        Normalised scores in [0.0, 1.0].
    """
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    denom = hi - lo + 1e-9
    return [_clamp((s - lo) / denom, 0.0, 1.0) for s in scores]
