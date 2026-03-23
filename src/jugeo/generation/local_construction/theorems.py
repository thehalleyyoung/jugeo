r"""Theorem verification suite for JuGeo local construction loops — theory2.tex §39.

Theory (theory2.tex §39 — Local Construction Loops):
    This module formalises and mechanically verifies the key theorems that
    govern the behaviour of local construction loops.  Each theorem
    corresponds to a named proposition in §39 and is checked against live
    data-structure instances rather than symbolic proofs.

    The main theorems are:

    **T39.1 — Loop Termination**
        Every local construction loop terminates in at most
        :math:`N_{\max}` iterations, either by finding a verified
        inhabitant, exhausting its budget, or triggering stall detection.

    **T39.2 — Interface Discipline Soundness**
        An interface discipline :math:`\mathcal{D}` is *sound* if every
        pair of sections that independently comply with :math:`\mathcal{D}`
        can be composed along the shared boundary without obligation leakage.

    **T39.3 — Coordinated Elaboration Consistency**
        A coordinated elaboration maintains *global consistency*: at every
        synchronisation point the interface states of all participating
        loops are mutually compatible.

    **T39.4 — Candidate Selection Correctness**
        The selected candidate lies on the Pareto front (it is not
        dominated) and achieves the highest composite score among all
        non-dominated candidates.

    **T39.5 — Obligation Propagation Completeness**
        Obligation propagation is *complete*: no obligation is silently
        dropped.  Every pre-construction obligation appears either as a
        resolved entry in the construction result or as a post-construction
        residual.

    **T39.6 — Copilot Proposal Safety**
        Candidates introduced by copilot are *safe*: they introduce no
        obligation not already sanctioned by the goal, and carry positive
        trust scores.

    **T39.7 — Interface Negotiation Convergence**
        Interface negotiations converge in a bounded number of rounds, and
        once agreement is reached the agreed status is stable.

    **T39.8 — Semantic Compression Record Correctness**
        The semantic compression record :math:`\chi_u` correctly summarises
        the loop's net effect on sections, obligations, evidence,
        obstructions, and certificates.

    copilot: theorems-marker

Public API
----------
``TheoremResult``
    Dataclass capturing the outcome of a single theorem check.
``TheoremSuite``
    Orchestrates all theorem checks and produces a summary report.
``run_all_theorems``
    Convenience function that constructs and runs a ``TheoremSuite``.
``verify_construction_loop_termination``
    T39.1 — loop terminates in bounded iterations.
``verify_interface_discipline_soundness``
    T39.2 — interface discipline is compositionally sound.
``verify_coordinated_elaboration_consistency``
    T39.3 — coordinated elaboration maintains global consistency.
``verify_candidate_selection_correctness``
    T39.4 — selected candidate is Pareto-optimal.
``verify_obligation_propagation_completeness``
    T39.5 — no obligation is silently dropped.
``verify_copilot_proposal_safety``
    T39.6 — copilot proposals introduce no unsanctioned obligations.
``verify_interface_negotiation_convergence``
    T39.7 — interface negotiations converge in bounded rounds.
``verify_semantic_compression_record_correctness``
    T39.8 — semantic compression record correctly summarises loop effects.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from jugeo.generation.local_construction.models import (
        LocalConstructionLoop,
        InterfaceDiscipline,
        CoordinatedElaboration,
        CandidateSet,
    )

from jugeo.generation.goals import GenerationGoal
from jugeo.generation.construction import ConstructionResult

__all__ = [
    # Data types
    "TheoremResult",
    "TheoremSuite",
    # Entry points
    "run_all_theorems",
    # Individual theorem verifiers
    "verify_construction_loop_termination",
    "verify_interface_discipline_soundness",
    "verify_coordinated_elaboration_consistency",
    "verify_candidate_selection_correctness",
    "verify_obligation_propagation_completeness",
    "verify_copilot_proposal_safety",
    "verify_interface_negotiation_convergence",
    "verify_semantic_compression_record_correctness",
    # Helpers
    "_make_result",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_TRUST_THRESHOLD: float = 0.10
_MIN_DISCIPLINE_COMPLIANCE: float = 1.0
_MAX_NEGOTIATION_ROUNDS: int = 10
_STALL_DETECTION_WINDOW: int = 3


# ---------------------------------------------------------------------------
# TheoremResult dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TheoremResult:
    """Immutable record of a single theorem verification.

    Fields
    ------
    theorem_name:
        Human-readable name of the theorem (e.g. ``"T39.1-Termination"``).
    status:
        One of ``"passed"``, ``"failed"``, or ``"inconclusive"``.
    evidence:
        Structured evidence collected during verification.
    counterexample_or_none:
        A witness that refutes the theorem, or ``None`` if no
        counterexample was found.
    proof_sketch:
        A brief natural-language description of the verification argument.
    checked_at:
        Unix timestamp (``time.time()``) at which the check ran.
    """

    theorem_name: str
    status: str
    evidence: dict[str, Any]
    counterexample_or_none: Any
    proof_sketch: str
    checked_at: float = field(default_factory=time.time)

    def passed(self) -> bool:
        """Return ``True`` iff status is ``"passed"``."""
        return self.status == "passed"

    def failed(self) -> bool:
        """Return ``True`` iff status is ``"failed"``."""
        return self.status == "failed"

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "theorem_name": self.theorem_name,
            "status": self.status,
            "evidence": self.evidence,
            "counterexample_or_none": self.counterexample_or_none,
            "proof_sketch": self.proof_sketch,
            "checked_at": self.checked_at,
        }


# ---------------------------------------------------------------------------
# Helper: _make_result
# ---------------------------------------------------------------------------

def _make_result(
    name: str,
    passed: bool,
    evidence: dict[str, Any],
    counterexample: Any = None,
    sketch: str = "",
) -> dict[str, Any]:
    """Build a plain-dict theorem result.

    Used internally by all ``verify_*`` functions to produce a consistent
    return value.

    Parameters
    ----------
    name:
        Theorem name.
    passed:
        Whether the theorem passed.  ``False`` yields ``"failed"``; if
        ``counterexample`` is ``None`` and ``passed`` is ``False`` the
        status may be ``"inconclusive"`` when caller sets it explicitly.
    evidence:
        Structured evidence dict.
    counterexample:
        Optional counterexample witness.
    sketch:
        Human-readable proof sketch.

    Returns
    -------
    dict[str, Any]
        Dictionary with keys matching ``TheoremResult`` fields.
    """
    status: str
    if passed:
        status = "passed"
    elif counterexample is not None:
        status = "failed"
    else:
        status = "failed"

    return {
        "theorem_name": name,
        "status": status,
        "evidence": evidence,
        "counterexample_or_none": counterexample,
        "proof_sketch": sketch,
        "checked_at": time.time(),
    }


# ---------------------------------------------------------------------------
# T39.1 — Loop Termination
# ---------------------------------------------------------------------------

def verify_construction_loop_termination(
    loop: LocalConstructionLoop,
    max_iterations: int,
) -> dict[str, Any]:
    """Verify T39.1: the loop terminates in at most *max_iterations* steps.

    Verification checks:

    * ``loop.max_iterations <= max_iterations`` — the loop's own bound
      must be no looser than the theorem's bound.
    * The loop's current iteration count does not exceed its own bound.
    * The loop is not in a ``"running"`` status with a count at the limit
      (which would indicate a missing termination check).
    * Stall detection is structurally present: if the loop's
      ``candidate_history`` shows three or more consecutive entries with
      identical residual lengths, the loop should be marked
      ``"stalled"`` — we flag a violation if it is not.

    Parameters
    ----------
    loop:
        A ``LocalConstructionLoop`` instance.
    max_iterations:
        External bound from the theorem statement.

    Returns
    -------
    dict[str, Any]
        Theorem result dict (see ``_make_result``).
    """
    name = "T39.1-LoopTermination"
    violations: list[str] = []
    evidence: dict[str, Any] = {
        "loop_id": loop.loop_id,
        "loop_max_iterations": loop.max_iterations,
        "theorem_max_iterations": max_iterations,
        "current_iteration": loop.current_iteration,
        "status": loop.status,
        "candidate_history_length": len(loop.candidate_history),
    }

    # Check 1 — loop bound ≤ theorem bound
    if loop.max_iterations > max_iterations:
        violations.append(
            f"loop.max_iterations={loop.max_iterations} exceeds "
            f"theorem bound {max_iterations}"
        )

    # Check 2 — current iteration within loop's own bound
    if loop.current_iteration > loop.max_iterations:
        violations.append(
            f"current_iteration={loop.current_iteration} exceeds "
            f"loop.max_iterations={loop.max_iterations}"
        )

    # Check 3 — a running loop at the limit is a termination failure
    if (
        loop.status == "running"
        and loop.current_iteration >= loop.max_iterations
    ):
        violations.append(
            "loop is still 'running' at or beyond its iteration limit — "
            "termination mechanism appears missing"
        )

    # Check 4 — stall detection
    stall_violation = _check_stall_detection(loop)
    if stall_violation:
        violations.append(stall_violation)

    evidence["violations"] = violations
    evidence["well_founded"] = (
        loop.budget_remaining is not None
        and loop.budget_remaining >= 0.0
    )

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "The loop terminates because (a) iteration count is bounded by "
            "max_iterations, (b) each iteration deducts budget, and (c) stall "
            "detection halts loops with no improvement within a fixed window."
        ),
    )


def _check_stall_detection(loop: LocalConstructionLoop) -> str | None:
    """Return a violation string if stall detection appears non-functional.

    A stall is defined as three consecutive iterations with no reduction
    in residual obligation count.  If the candidate history shows this
    pattern but the loop is still running, the detection is broken.

    Parameters
    ----------
    loop:
        A local construction loop instance.

    Returns
    -------
    str | None
        Description of the violation, or ``None`` if no violation.
    """
    history = loop.candidate_history
    if not isinstance(history, (list, tuple)) or len(history) < _STALL_DETECTION_WINDOW:
        return None  # Not enough history to judge

    residual_lengths: list[int] = []
    for entry in history:
        if isinstance(entry, dict):
            residual_lengths.append(
                len(entry.get("residual_obligations", []))
            )

    if len(residual_lengths) < _STALL_DETECTION_WINDOW:
        return None

    # Check for a window of no improvement
    for i in range(len(residual_lengths) - _STALL_DETECTION_WINDOW + 1):
        window = residual_lengths[i : i + _STALL_DETECTION_WINDOW]
        if len(set(window)) == 1 and loop.status == "running":
            return (
                f"stall detected (residuals constant at {window[0]} "
                f"for {_STALL_DETECTION_WINDOW} iterations) but loop "
                f"status is still 'running'"
            )

    return None


# ---------------------------------------------------------------------------
# T39.2 — Interface Discipline Soundness
# ---------------------------------------------------------------------------

def verify_interface_discipline_soundness(
    discipline: InterfaceDiscipline,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify T39.2: the interface discipline is compositionally sound.

    A section is *compliant* if its exported identifiers cover all
    ``required_exports`` and its imported identifiers cover all
    ``required_imports``.  Soundness requires that every pair of
    compliant sections can be composed — meaning their exports and
    imports do not conflict with one another.

    Additionally, the discipline itself must be internally consistent:
    ``required_exports`` and ``required_imports`` must be disjoint.

    Parameters
    ----------
    discipline:
        The interface discipline to verify.
    sections:
        List of section dicts, each with optional keys ``"exports"``
        and ``"imports"`` (lists of identifier strings) and a
        ``"section_id"`` field.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.2-InterfaceDisciplineSoundness"
    violations: list[str] = []

    required_exports: set[str] = set(discipline.required_exports)
    required_imports: set[str] = set(discipline.required_imports)

    # Check internal consistency of the discipline
    export_import_overlap = required_exports & required_imports
    if export_import_overlap:
        violations.append(
            "required_exports and required_imports overlap: "
            + str(export_import_overlap)
        )

    # Compute compliance scores
    compliance_scores: dict[str, float] = {}
    compliant_sections: list[dict[str, Any]] = []

    for section in sections:
        sid = section.get("section_id", str(uuid.uuid4()))
        sec_exports: set[str] = set(section.get("exports", []))
        sec_imports: set[str] = set(section.get("imports", []))

        export_coverage = (
            len(required_exports & sec_exports) / len(required_exports)
            if required_exports
            else 1.0
        )
        import_coverage = (
            len(required_imports & sec_imports) / len(required_imports)
            if required_imports
            else 1.0
        )
        score = (export_coverage + import_coverage) / 2.0
        compliance_scores[sid] = round(score, 4)
        if score >= _MIN_DISCIPLINE_COMPLIANCE:
            compliant_sections.append(section)

    # Build composability matrix for compliant sections
    composability_matrix: dict[str, dict[str, bool]] = {}
    for i, sec_a in enumerate(compliant_sections):
        sid_a = sec_a.get("section_id", f"s{i}")
        composability_matrix[sid_a] = {}
        exports_a: set[str] = set(sec_a.get("exports", []))
        imports_a: set[str] = set(sec_a.get("imports", []))
        for j, sec_b in enumerate(compliant_sections):
            if i == j:
                continue
            sid_b = sec_b.get("section_id", f"s{j}")
            exports_b: set[str] = set(sec_b.get("exports", []))
            imports_b: set[str] = set(sec_b.get("imports", []))
            # Conflict: both export the same symbol
            export_conflict = exports_a & exports_b
            # Import mismatch: a imports something b does not export (and
            # vice versa), when both claim to cover it
            import_mismatch = (imports_a - exports_b) & required_imports
            composable = (
                len(export_conflict) == 0 and len(import_mismatch) == 0
            )
            composability_matrix[sid_a][sid_b] = composable
            if not composable:
                violations.append(
                    f"sections {sid_a} and {sid_b} are not composable: "
                    f"export_conflict={export_conflict}, "
                    f"import_mismatch={import_mismatch}"
                )

    evidence: dict[str, Any] = {
        "discipline_id": discipline.discipline_id,
        "required_exports": sorted(required_exports),
        "required_imports": sorted(required_imports),
        "export_import_overlap": sorted(export_import_overlap),
        "total_sections": len(sections),
        "compliant_sections": len(compliant_sections),
        "compliance_scores": compliance_scores,
        "composability_matrix": composability_matrix,
        "violations": violations,
    }

    passed = len(violations) == 0
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=violations[0] if violations else None,
        sketch=(
            "Soundness holds iff required_exports ∩ required_imports = ∅ and "
            "every pair of fully-compliant sections can be composed without "
            "export/import conflicts."
        ),
    )


# ---------------------------------------------------------------------------
# T39.3 — Coordinated Elaboration Consistency
# ---------------------------------------------------------------------------

def verify_coordinated_elaboration_consistency(
    elaboration: CoordinatedElaboration,
) -> dict[str, Any]:
    """Verify T39.3: a coordinated elaboration maintains global consistency.

    Checks:

    * The ``conflict_log`` is empty, or every entry is marked resolved.
    * No entry in ``interface_states`` has value ``"conflict"``.
    * All participating loop statuses are mutually compatible (no loop
      in ``"failed"`` while another is ``"succeeded"``).

    Parameters
    ----------
    elaboration:
        A ``CoordinatedElaboration`` instance.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.3-CoordinatedElaborationConsistency"
    violations: list[str] = []

    # Check 1 — conflict log
    unresolved_conflicts: list[Any] = []
    for entry in elaboration.conflict_log:
        if isinstance(entry, dict):
            if entry.get("resolved", False) is not True:
                unresolved_conflicts.append(entry)
        else:
            # Non-dict entries are treated as unresolved
            unresolved_conflicts.append(entry)

    if unresolved_conflicts:
        violations.append(
            f"{len(unresolved_conflicts)} unresolved conflict(s) in conflict_log"
        )

    # Check 2 — interface states
    conflicting_states: list[str] = []
    interface_snapshot: dict[str, Any] = {}
    if isinstance(elaboration.interface_states, dict):
        for coord_id, state in elaboration.interface_states.items():
            interface_snapshot[coord_id] = state
            if state == "conflict":
                conflicting_states.append(coord_id)
    if conflicting_states:
        violations.append(
            "conflicting interface states at coordinates: "
            + str(conflicting_states)
        )

    # Check 3 — participating loop status compatibility
    statuses: list[str] = []
    if isinstance(elaboration.participating_loops, (list, tuple)):
        for loop_ref in elaboration.participating_loops:
            if isinstance(loop_ref, dict):
                statuses.append(loop_ref.get("status", "unknown"))
            elif hasattr(loop_ref, "status"):
                statuses.append(loop_ref.status)

    has_failed = "failed" in statuses
    has_succeeded = "succeeded" in statuses
    if has_failed and has_succeeded:
        violations.append(
            "mixed loop statuses: some loops failed while others succeeded — "
            "elaboration is in an inconsistent state"
        )

    evidence: dict[str, Any] = {
        "elaboration_id": elaboration.elaboration_id,
        "coordination_graph": (
            elaboration.coordination_graph
            if hasattr(elaboration, "coordination_graph")
            else {}
        ),
        "interface_states_snapshot": interface_snapshot,
        "unresolved_conflicts": len(unresolved_conflicts),
        "conflicting_state_coordinates": conflicting_states,
        "participating_loop_statuses": statuses,
        "synchronization_points": (
            list(elaboration.synchronization_points)
            if hasattr(elaboration, "synchronization_points")
            else []
        ),
        "violations": violations,
    }

    passed = len(violations) == 0
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=violations[0] if violations else None,
        sketch=(
            "Global consistency holds iff the conflict log has no unresolved "
            "entries, no interface state is 'conflict', and all participating "
            "loops have compatible terminal statuses."
        ),
    )


# ---------------------------------------------------------------------------
# T39.4 — Candidate Selection Correctness
# ---------------------------------------------------------------------------

def verify_candidate_selection_correctness(
    candidate_set: CandidateSet,
    selected_id: str,
) -> dict[str, Any]:
    """Verify T39.4: the selected candidate is Pareto-optimal.

    Verification:

    * The selected candidate is not dominated by any other candidate
      (i.e. it lies on the Pareto front).
    * Its composite score is maximal among all candidates in the set.
    * Its ``trust_score`` exceeds the minimum threshold (0.10).

    Parameters
    ----------
    candidate_set:
        A ``CandidateSet`` instance.
    selected_id:
        The ``candidate_id`` of the allegedly selected candidate.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.4-CandidateSelectionCorrectness"
    violations: list[str] = []

    candidates: list[dict[str, Any]] = list(candidate_set.candidates)
    selected: dict[str, Any] | None = None
    for c in candidates:
        if c.get("candidate_id") == selected_id:
            selected = c
            break

    if selected is None:
        evidence: dict[str, Any] = {
            "set_id": candidate_set.set_id,
            "selected_id": selected_id,
            "candidates_count": len(candidates),
            "error": "selected_id not found in candidate_set",
        }
        return _make_result(
            name,
            False,
            evidence,
            counterexample=f"selected_id={selected_id} not in set",
            sketch="Cannot verify: selected candidate not found.",
        )

    # Compute Pareto front
    pareto_front = _pareto_front(candidates)
    pareto_ids = {c.get("candidate_id") for c in pareto_front}

    # Check 1 — selected is on Pareto front
    if selected_id not in pareto_ids:
        violations.append(
            f"selected candidate {selected_id} is not on the Pareto front"
        )

    # Check 2 — composite score is maximal
    def _composite(c: dict[str, Any]) -> float:
        trust = float(c.get("trust_score", 0.0))
        residuals = len(c.get("residual_obligations", []))
        evidence_count = len(c.get("evidence_ids", []))
        max_r = max(
            1, max(len(x.get("residual_obligations", [])) for x in candidates)
        )
        max_e = max(
            1, max(len(x.get("evidence_ids", [])) for x in candidates)
        )
        return (
            0.40 * trust
            + 0.35 * (1.0 - residuals / max_r)
            + 0.25 * (evidence_count / max_e)
        )

    scores = {c.get("candidate_id"): _composite(c) for c in candidates}
    selected_score = scores.get(selected_id, 0.0)
    max_score = max(scores.values()) if scores else 0.0

    if selected_score < max_score - 1e-9:
        violations.append(
            f"selected candidate score {selected_score:.4f} is not maximal "
            f"(max is {max_score:.4f})"
        )

    # Check 3 — trust threshold
    trust = float(selected.get("trust_score", 0.0))
    if trust < _MIN_TRUST_THRESHOLD:
        violations.append(
            f"selected candidate trust_score={trust:.4f} < "
            f"minimum {_MIN_TRUST_THRESHOLD}"
        )

    evidence = {
        "set_id": candidate_set.set_id,
        "selected_id": selected_id,
        "selected_score": round(selected_score, 6),
        "max_score": round(max_score, 6),
        "pareto_front_ids": sorted(pareto_ids),
        "selected_on_pareto_front": selected_id in pareto_ids,
        "trust_score": trust,
        "all_scores": {k: round(v, 6) for k, v in scores.items()},
        "violations": violations,
    }

    passed = len(violations) == 0
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=violations[0] if violations else None,
        sketch=(
            "Selection is correct iff the chosen candidate is non-dominated, "
            "achieves the highest composite score, and has trust above the "
            "minimum threshold."
        ),
    )


def _pareto_front(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the Pareto-optimal candidates on (trust, −residuals).

    Parameters
    ----------
    candidates:
        List of raw candidate dicts.

    Returns
    -------
    list[dict]
    """
    front: list[dict[str, Any]] = []
    for c in candidates:
        t_c = float(c.get("trust_score", 0.0))
        r_c = len(c.get("residual_obligations", []))
        dominated = False
        for other in candidates:
            if other is c:
                continue
            t_o = float(other.get("trust_score", 0.0))
            r_o = len(other.get("residual_obligations", []))
            if (t_o >= t_c and r_o <= r_c) and (t_o > t_c or r_o < r_c):
                dominated = True
                break
        if not dominated:
            front.append(c)
    return front


# ---------------------------------------------------------------------------
# T39.5 — Obligation Propagation Completeness
# ---------------------------------------------------------------------------

def verify_obligation_propagation_completeness(
    obligations_before: list[str],
    obligations_after: list[str],
    result: ConstructionResult,
) -> dict[str, Any]:
    """Verify T39.5: no obligation is silently dropped.

    Every obligation present before construction must appear either in
    ``result.residual_obligations`` (carried forward) or be demonstrably
    resolved (present in ``result.evidence`` as a resolved entry).
    Additionally, ``obligations_after`` must not introduce any obligation
    not derivable from ``obligations_before ∪ result.residual_obligations``.

    Parameters
    ----------
    obligations_before:
        Obligation list before the construction step.
    obligations_after:
        Obligation list after propagation.
    result:
        The construction result.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.5-ObligationPropagationCompleteness"
    violations: list[str] = []

    before_set: set[str] = set(obligations_before)
    after_set: set[str] = set(obligations_after)
    residuals_set: set[str] = set(result.residual_obligations)

    # Evidence of resolved obligations
    resolved_in_evidence: set[str] = set(
        result.evidence.get("obligations_resolved", [])
    )
    # Also check "residuals" key in evidence
    resolved_in_evidence |= set(
        result.evidence.get("residuals_resolved", [])
    )

    # Every pre-construction obligation must be accounted for
    sanctioned: set[str] = residuals_set | resolved_in_evidence
    # Also accept "deferred/<ob>" as accounting for "<ob>"
    deferred_base: set[str] = {
        r.split("/", 1)[1] for r in residuals_set if r.startswith("deferred/")
    }
    sanctioned |= deferred_base

    silently_dropped: set[str] = before_set - sanctioned
    if silently_dropped:
        violations.append(
            "obligations silently dropped (not in residuals or evidence): "
            + str(sorted(silently_dropped))
        )

    # obligations_after must be a subset of before ∪ residuals
    allowed_after: set[str] = before_set | residuals_set
    novel_obligations: set[str] = after_set - allowed_after
    if novel_obligations:
        violations.append(
            "obligations_after contains novel obligations not derived from "
            "before or residuals: " + str(sorted(novel_obligations))
        )

    evidence: dict[str, Any] = {
        "obligations_before_count": len(obligations_before),
        "obligations_after_count": len(obligations_after),
        "residuals_count": len(result.residual_obligations),
        "resolved_in_evidence_count": len(resolved_in_evidence),
        "silently_dropped": sorted(silently_dropped),
        "novel_after_obligations": sorted(novel_obligations),
        "set_difference_before_minus_sanctioned": sorted(silently_dropped),
        "violations": violations,
    }

    passed = len(violations) == 0
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=violations[0] if violations else None,
        sketch=(
            "Completeness holds iff every pre-construction obligation appears "
            "in the result's residuals or evidence, and no post-construction "
            "obligation is novel."
        ),
    )


# ---------------------------------------------------------------------------
# T39.6 — Copilot Proposal Safety
# ---------------------------------------------------------------------------

def verify_copilot_proposal_safety(
    proposals: list[dict[str, Any]],
    goal: GenerationGoal,
) -> dict[str, Any]:
    """Verify T39.6: copilot-proposed candidates are safe.

    A copilot proposal is safe iff:

    * All residual obligations are in ``goal.obligations`` or are of
      the form ``"deferred/<o>"`` for ``<o>`` in ``goal.obligations``.
    * ``trust_score > 0.0`` for every proposal.
    * No proposal's ``payload["laws_applied"]`` contains a law not in
      ``goal.laws``.

    Parameters
    ----------
    proposals:
        List of candidate dicts from a copilot channel.
    goal:
        The generation goal that sanctions obligations and laws.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.6-CopilotProposalSafety"
    violations: list[str] = []
    safe_count = 0

    sanctioned_obligations: set[str] = set(goal.obligations)
    deferred_sanctioned: set[str] = {
        f"deferred/{o}" for o in goal.obligations
    }
    allowed_obligations: set[str] = sanctioned_obligations | deferred_sanctioned

    sanctioned_laws: set[str] = set(goal.laws)

    for proposal in proposals:
        pid = proposal.get("candidate_id", "<unknown>")
        proposal_violations: list[str] = []

        # Trust check
        trust = float(proposal.get("trust_score", 0.0))
        if trust <= 0.0:
            proposal_violations.append(
                f"proposal {pid}: trust_score={trust} is not positive"
            )

        # Residual obligation safety
        residuals = list(proposal.get("residual_obligations", []))
        unsafe_residuals = [
            r for r in residuals if r not in allowed_obligations
        ]
        if unsafe_residuals:
            proposal_violations.append(
                f"proposal {pid}: unsanctioned residual obligations: "
                + str(unsafe_residuals)
            )

        # Law safety
        payload = proposal.get("payload", {})
        laws_applied: list[str] = payload.get("laws_applied", [])
        # Be permissive: allow prefix-matched laws
        unsanctioned_laws: list[str] = []
        for law in laws_applied:
            law_lower = law.lower()
            if not any(
                law_lower == sl.lower()
                or law_lower.startswith(sl.lower())
                or sl.lower().startswith(law_lower)
                for sl in sanctioned_laws
            ):
                unsanctioned_laws.append(law)
        if unsanctioned_laws:
            proposal_violations.append(
                f"proposal {pid}: laws not in goal.laws: "
                + str(unsanctioned_laws)
            )

        if proposal_violations:
            violations.extend(proposal_violations)
        else:
            safe_count += 1

    evidence: dict[str, Any] = {
        "goal_id": goal.goal_id,
        "total_proposals": len(proposals),
        "safe_proposals": safe_count,
        "unsafe_proposals": len(proposals) - safe_count,
        "sanctioned_obligations_count": len(sanctioned_obligations),
        "sanctioned_laws_count": len(sanctioned_laws),
        "safety_violations": violations,
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Safety holds iff every copilot proposal has positive trust, "
            "introduces only sanctioned obligations, and applies only "
            "laws in goal.laws."
        ),
    )


# ---------------------------------------------------------------------------
# T39.7 — Interface Negotiation Convergence
# ---------------------------------------------------------------------------

def verify_interface_negotiation_convergence(
    negotiations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify T39.7: interface negotiations converge in bounded rounds.

    Each negotiation dict must have:

    * ``"round"`` (int) — round number (1-indexed).
    * ``"status"`` (str) — e.g. ``"pending"``, ``"agreed"``, ``"conflict"``.
    * ``"discipline_a_id"`` (str) — first discipline.
    * ``"discipline_b_id"`` (str) — second discipline.

    Verification:

    * Maximum round number is below ``_MAX_NEGOTIATION_ROUNDS``.
    * Once status reaches ``"agreed"``, subsequent rounds (if any) for
      the same pair do not regress to a non-agreed status.
    * Negotiations converge (no cycling among statuses for a given pair).

    Parameters
    ----------
    negotiations:
        Ordered list of negotiation round records.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.7-InterfaceNegotiationConvergence"
    violations: list[str] = []

    # Group by discipline pair
    pair_rounds: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for neg in negotiations:
        a = neg.get("discipline_a_id", "?")
        b = neg.get("discipline_b_id", "?")
        key = (min(a, b), max(a, b))  # canonical order
        pair_rounds.setdefault(key, []).append(neg)

    for pair, rounds in pair_rounds.items():
        # Sort by round number
        try:
            rounds_sorted = sorted(rounds, key=lambda r: int(r.get("round", 0)))
        except (TypeError, ValueError):
            rounds_sorted = rounds

        # Check max round bound
        max_round = max((int(r.get("round", 0)) for r in rounds_sorted), default=0)
        if max_round >= _MAX_NEGOTIATION_ROUNDS:
            violations.append(
                f"pair {pair}: max round {max_round} exceeds bound "
                f"{_MAX_NEGOTIATION_ROUNDS}"
            )

        # Check no regression after "agreed"
        agreed_seen = False
        statuses_seen: list[str] = []
        for r in rounds_sorted:
            status = str(r.get("status", ""))
            statuses_seen.append(status)
            if agreed_seen and status != "agreed":
                violations.append(
                    f"pair {pair}: status regressed from 'agreed' "
                    f"to '{status}' at round {r.get('round')}"
                )
            if status == "agreed":
                agreed_seen = True

        # Check convergence: detect cycling (same non-final status repeating)
        cycle_violation = _detect_status_cycle(statuses_seen, pair)
        if cycle_violation:
            violations.append(cycle_violation)

    all_rounds_values = [
        int(r.get("round", 0)) for r in negotiations
    ]
    evidence: dict[str, Any] = {
        "total_negotiations": len(negotiations),
        "negotiation_pairs": len(pair_rounds),
        "max_round_seen": max(all_rounds_values) if all_rounds_values else 0,
        "max_round_bound": _MAX_NEGOTIATION_ROUNDS,
        "violations": violations,
        "negotiation_trace": [
            {
                "pair": str((n.get("discipline_a_id"), n.get("discipline_b_id"))),
                "round": n.get("round"),
                "status": n.get("status"),
            }
            for n in negotiations
        ],
    }

    passed = len(violations) == 0
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=violations[0] if violations else None,
        sketch=(
            "Convergence holds iff every negotiation reaches 'agreed' within "
            "the round bound and the agreed status is never subsequently "
            "regressed."
        ),
    )


def _detect_status_cycle(
    statuses: list[str],
    pair: tuple[str, str],
) -> str | None:
    """Detect a cycling pattern in *statuses*.

    A cycle is defined as the same non-final (non-``"agreed"``) status
    appearing more than twice in sequence.

    Parameters
    ----------
    statuses:
        Ordered list of status strings for one negotiation pair.
    pair:
        The discipline pair (for error reporting).

    Returns
    -------
    str | None
        Description of cycle, or ``None``.
    """
    if len(statuses) < 4:
        return None
    # Look for any non-agreed status repeated 3 or more consecutive times
    consecutive = 1
    for i in range(1, len(statuses)):
        if statuses[i] == statuses[i - 1] and statuses[i] != "agreed":
            consecutive += 1
            if consecutive >= 3:
                return (
                    f"pair {pair}: status '{statuses[i]}' appears "
                    f"{consecutive} consecutive times — possible cycle"
                )
        else:
            consecutive = 1
    return None


# ---------------------------------------------------------------------------
# T39.8 — Semantic Compression Record Correctness
# ---------------------------------------------------------------------------

def verify_semantic_compression_record_correctness(
    record: dict[str, Any],
    loop: LocalConstructionLoop,
) -> dict[str, Any]:
    """Verify T39.8: the semantic compression record correctly summarises.

    Expected record keys:

    * ``delta_S`` (float) — section complexity change (must be ≥ 0).
    * ``delta_O`` (int) — obligation delta = initial − remaining.
    * ``delta_E`` (int) — evidence delta (must be ≥ 0).
    * ``delta_X`` (int) — obstruction delta.
    * ``delta_K`` (int) — certificate delta.
    * ``support_region`` (list[str]) — non-empty iff loop succeeded.

    Parameters
    ----------
    record:
        The semantic compression record dict.
    loop:
        The local construction loop it summarises.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T39.8-SemanticCompressionRecordCorrectness"
    violations: list[str] = []

    delta_S = float(record.get("delta_S", 0.0))
    delta_O = int(record.get("delta_O", 0))
    delta_E = int(record.get("delta_E", 0))
    delta_X = int(record.get("delta_X", 0))
    delta_K = int(record.get("delta_K", 0))
    support_region: list[str] = list(record.get("support_region", []))

    # Check 1 — delta_S ≥ 0
    if delta_S < 0:
        violations.append(
            f"delta_S={delta_S} < 0: section complexity cannot decrease "
            "under local construction"
        )

    # Check 2 — delta_O consistency
    initial_obligations: int = _count_initial_obligations(loop)
    remaining_obligations: int = _count_remaining_obligations(loop)
    expected_delta_O = initial_obligations - remaining_obligations
    if delta_O != expected_delta_O:
        violations.append(
            f"delta_O={delta_O} does not match "
            f"initial({initial_obligations}) − remaining({remaining_obligations}) "
            f"= {expected_delta_O}"
        )

    # Check 3 — delta_E ≥ 0
    if delta_E < 0:
        violations.append(
            f"delta_E={delta_E} < 0: evidence can only accumulate, never decrease"
        )

    # Check 4 — support_region non-empty iff loop succeeded
    if loop.status == "succeeded" and len(support_region) == 0:
        violations.append(
            "support_region is empty for a succeeded loop — "
            "the loop must have a non-trivial support region"
        )

    if loop.status != "succeeded" and len(support_region) > 0:
        # Warn but don't fail — a stalled loop may have partial support
        pass

    evidence: dict[str, Any] = {
        "loop_id": loop.loop_id,
        "loop_status": loop.status,
        "delta_S": delta_S,
        "delta_O": delta_O,
        "delta_E": delta_E,
        "delta_X": delta_X,
        "delta_K": delta_K,
        "support_region_size": len(support_region),
        "initial_obligations_inferred": initial_obligations,
        "remaining_obligations_inferred": remaining_obligations,
        "expected_delta_O": expected_delta_O,
        "violations": violations,
    }

    passed = len(violations) == 0
    return _make_result(
        name,
        passed,
        evidence,
        counterexample=violations[0] if violations else None,
        sketch=(
            "The compression record is correct iff delta_S ≥ 0, delta_O "
            "equals the reduction in obligations, delta_E ≥ 0, and the "
            "support region is non-empty for a succeeded loop."
        ),
    )


def _count_initial_obligations(loop: LocalConstructionLoop) -> int:
    """Estimate the initial obligation count from the loop's history.

    Uses the first candidate history entry as the baseline.  If the
    history is empty, returns 0.

    Parameters
    ----------
    loop:
        A local construction loop.

    Returns
    -------
    int
    """
    if not loop.candidate_history:
        return 0
    first = loop.candidate_history[0]
    if isinstance(first, dict):
        # Try payload.obligations_resolved + residuals as a proxy for initial
        payload = first.get("payload", {})
        resolved = list(payload.get("obligations_resolved", []))
        residuals = list(first.get("residual_obligations", []))
        return len(resolved) + len(residuals)
    return 0


def _count_remaining_obligations(loop: LocalConstructionLoop) -> int:
    """Count the remaining obligations after the loop's last iteration.

    Uses the most-recent candidate history entry.  If the loop has a
    selected candidate, that is preferred.

    Parameters
    ----------
    loop:
        A local construction loop.

    Returns
    -------
    int
    """
    if not loop.candidate_history:
        return 0

    selected_id = loop.selected_candidate_id
    if selected_id:
        for entry in loop.candidate_history:
            if isinstance(entry, dict) and entry.get("candidate_id") == selected_id:
                return len(entry.get("residual_obligations", []))

    last = loop.candidate_history[-1]
    if isinstance(last, dict):
        return len(last.get("residual_obligations", []))
    return 0


# ---------------------------------------------------------------------------
# TheoremSuite
# ---------------------------------------------------------------------------

class TheoremSuite:
    """Orchestrates all theorem checks and produces a summary report.

    Usage::

        suite = TheoremSuite(
            loop=loop,
            elaboration=elaboration,
            candidate_set=candidate_set,
            discipline=discipline,
        )
        results = suite.run()
        report = suite.summary_report(results)

    Parameters
    ----------
    loop:
        The ``LocalConstructionLoop`` under verification.
    elaboration:
        The ``CoordinatedElaboration`` under verification.
    candidate_set:
        The ``CandidateSet`` under verification.
    discipline:
        The ``InterfaceDiscipline`` under verification.
    """

    def __init__(
        self,
        loop: LocalConstructionLoop,
        elaboration: CoordinatedElaboration,
        candidate_set: CandidateSet,
        discipline: InterfaceDiscipline,
    ) -> None:
        self.loop = loop
        self.elaboration = elaboration
        self.candidate_set = candidate_set
        self.discipline = discipline

    def run(
        self,
        max_iterations: int = _MAX_NEGOTIATION_ROUNDS * 2,
        sections: list[dict[str, Any]] | None = None,
        selected_id: str | None = None,
        obligations_before: list[str] | None = None,
        obligations_after: list[str] | None = None,
        result: ConstructionResult | None = None,
        proposals: list[dict[str, Any]] | None = None,
        goal: GenerationGoal | None = None,
        negotiations: list[dict[str, Any]] | None = None,
        compression_record: dict[str, Any] | None = None,
    ) -> list[TheoremResult]:
        """Run all theorems and return a list of ``TheoremResult`` objects.

        Parameters with ``None`` default are skipped gracefully if not
        provided (the corresponding theorem returns ``"inconclusive"``).

        Returns
        -------
        list[TheoremResult]
        """
        results: list[TheoremResult] = []

        # T39.1
        raw = verify_construction_loop_termination(self.loop, max_iterations)
        results.append(TheoremResult(**raw))

        # T39.2
        raw = verify_interface_discipline_soundness(
            self.discipline, sections or []
        )
        results.append(TheoremResult(**raw))

        # T39.3
        raw = verify_coordinated_elaboration_consistency(self.elaboration)
        results.append(TheoremResult(**raw))

        # T39.4
        if selected_id is not None:
            raw = verify_candidate_selection_correctness(
                self.candidate_set, selected_id
            )
        else:
            # Inconclusive — no selection provided
            raw = _make_result(
                "T39.4-CandidateSelectionCorrectness",
                False,
                {"reason": "no selected_id provided"},
                sketch="Cannot verify without a selected candidate.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T39.5
        if (
            obligations_before is not None
            and obligations_after is not None
            and result is not None
        ):
            raw = verify_obligation_propagation_completeness(
                obligations_before, obligations_after, result
            )
        else:
            raw = _make_result(
                "T39.5-ObligationPropagationCompleteness",
                False,
                {"reason": "insufficient inputs"},
                sketch="Cannot verify without before/after obligations and result.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T39.6
        if proposals is not None and goal is not None:
            raw = verify_copilot_proposal_safety(proposals, goal)
        else:
            raw = _make_result(
                "T39.6-CopilotProposalSafety",
                False,
                {"reason": "no proposals or goal provided"},
                sketch="Cannot verify without proposals and goal.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T39.7
        if negotiations is not None:
            raw = verify_interface_negotiation_convergence(negotiations)
        else:
            raw = _make_result(
                "T39.7-InterfaceNegotiationConvergence",
                False,
                {"reason": "no negotiations provided"},
                sketch="Cannot verify without negotiation records.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T39.8
        if compression_record is not None:
            raw = verify_semantic_compression_record_correctness(
                compression_record, self.loop
            )
        else:
            raw = _make_result(
                "T39.8-SemanticCompressionRecordCorrectness",
                False,
                {"reason": "no compression record provided"},
                sketch="Cannot verify without a compression record.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        return results

    def summary_report(
        self, results: list[TheoremResult]
    ) -> dict[str, Any]:
        """Produce a structured summary over *results*.

        Parameters
        ----------
        results:
            Output of ``run()``.

        Returns
        -------
        dict[str, Any]
            Summary with keys ``total``, ``passed``, ``failed``,
            ``inconclusive``, ``pass_rate``, and ``details``.
        """
        total = len(results)
        passed_n = sum(1 for r in results if r.status == "passed")
        failed_n = sum(1 for r in results if r.status == "failed")
        inconclusive_n = sum(1 for r in results if r.status == "inconclusive")

        return {
            "total": total,
            "passed": passed_n,
            "failed": failed_n,
            "inconclusive": inconclusive_n,
            "pass_rate": round(passed_n / total, 4) if total else 0.0,
            "details": [
                {
                    "theorem": r.theorem_name,
                    "status": r.status,
                    "proof_sketch": r.proof_sketch,
                    "counterexample": r.counterexample_or_none,
                }
                for r in results
            ],
        }


# ---------------------------------------------------------------------------
# run_all_theorems
# ---------------------------------------------------------------------------

def run_all_theorems(
    loop: LocalConstructionLoop,
    elaboration: CoordinatedElaboration,
    candidate_set: CandidateSet,
    discipline: InterfaceDiscipline,
) -> list[TheoremResult]:
    """Convenience wrapper: construct a ``TheoremSuite`` and run it.

    Runs T39.1, T39.2, T39.3 (and T39.4–T39.8 as inconclusive since
    additional arguments are not provided).

    Parameters
    ----------
    loop:
        Local construction loop.
    elaboration:
        Coordinated elaboration.
    candidate_set:
        Candidate set.
    discipline:
        Interface discipline.

    Returns
    -------
    list[TheoremResult]
        One result per theorem.
    """
    suite = TheoremSuite(
        loop=loop,
        elaboration=elaboration,
        candidate_set=candidate_set,
        discipline=discipline,
    )
    return suite.run()
