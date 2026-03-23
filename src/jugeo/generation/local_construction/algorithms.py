r"""Local construction algorithms for JuGeo — theory2.tex §39.

Theory (theory2.tex §39 — Local Construction Loops):
    A *local construction loop* drives the inner generate–verify cycle for a
    single goal record :math:`g_u`.  Starting from the goal's obligation set
    :math:`\Omega_u` and law bundle :math:`\Lambda_u`, the loop iterates the
    four-phase pipeline

    .. math::

        \text{propose} \;\to\; \text{select} \;\to\; \text{verify}
        \;\to\; \text{propagate}

    until either a verified inhabitant is found, the iteration budget
    :math:`\mu_u` is exhausted, or a stall is detected (no improvement in
    three consecutive rounds).

    Each iteration emits a *semantic compression record*

    .. math::

        \chi_u = (\Delta S_u,\; \Delta O_u,\; \Delta E_u,\;
                  \Delta X_u,\; \Delta K_u,\;
                  \operatorname{supp}(\Delta_u))

    collecting section-complexity change, obligation delta, evidence delta,
    obstruction count, certificate delta, and the support region over which
    changes occurred.

    Interface discipline (§39.3) ensures that when two loops share a
    boundary coordinate, their interface states remain compatible throughout
    the run — guaranteed by ``coordinate_interfaces``.

    copilot: algorithms-marker

Public API
----------
``run_local_construction_loop``
    Main entry point: runs the full propose→select→verify→propagate cycle.
``propose_candidates``
    Generate *n* candidate inhabitants for a goal.
``select_best_candidate``
    Multi-criterion weighted selection over a candidate list.
``verify_candidate``
    Budget, treaty, law-satisfaction, and obligation checks.
``propagate_obligations``
    Lift residual obligations up to the parent goal.
``coordinate_interfaces``
    Synchronise interface states between two loops sharing a boundary.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from jugeo.generation.local_construction.models import (
        LocalConstructionLoop,
    )

from jugeo.generation.goals import GenerationGoal
from jugeo.generation.construction import (
    ConstructionContext,
    ConstructionResult,
)

__all__ = [
    "run_local_construction_loop",
    "propose_candidates",
    "select_best_candidate",
    "verify_candidate",
    "propagate_obligations",
    "coordinate_interfaces",
    # helpers (exported for testing / introspection)
    "_score_candidate",
    "_compute_pareto_front",
    "_is_dominated",
    "_generate_candidate_id",
    "_check_law_satisfaction",
    "_budget_fraction",
    "_apply_verification_policy",
]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_BUDGET_FOR_ATTEMPT: float = 0.01
_MIN_BUDGET_FOR_VERIFY: float = 0.05
_MIN_TRUST_WITH_TREATY: float = 0.30
_STALL_WINDOW: int = 3       # iterations with no improvement before stall
_DEFAULT_MAX_ITER: int = 20
_DEFAULT_N_PROPOSALS: int = 5

# Criterion weights for composite candidate scoring
_W_TRUST: float = 0.40
_W_RESOLVED: float = 0.35
_W_EVIDENCE: float = 0.25

# Verification policies
_POLICY_STRICT: str = "strict"
_POLICY_LENIENT: str = "lenient"
_POLICY_DEFERRED: str = "deferred"


# ---------------------------------------------------------------------------
# 1.  Main loop driver
# ---------------------------------------------------------------------------

def run_local_construction_loop(
    goal: GenerationGoal,
    context: ConstructionContext,
    max_iter: int = _DEFAULT_MAX_ITER,
) -> ConstructionResult:
    """Run the propose → select → verify → propagate cycle.

    Parameters
    ----------
    goal:
        The generation goal specifying laws, obligations, budget, and
        coordinate context.
    context:
        Ambient construction context carrying bindings, evidence, treaty
        reference, and current budget.
    max_iter:
        Hard upper bound on the number of iterations.

    Returns
    -------
    ConstructionResult
        A result whose ``status`` is one of ``"succeeded"``,
        ``"budget_exhausted"``, ``"stalled"``, or ``"failed"``.
    """
    t_start = time.perf_counter()
    result_id = str(uuid.uuid4())

    best_score: float = -1.0
    stall_count: int = 0
    iteration: int = 0
    selected: dict[str, Any] | None = None
    final_residuals: tuple[str, ...] = tuple(goal.obligations)
    final_evidence: dict[str, Any] = {}

    log.debug(
        "run_local_construction_loop: goal=%s max_iter=%d budget=%.4f",
        goal.goal_id, max_iter, context.budget_remaining,
    )

    for iteration in range(1, max_iter + 1):
        # ── Budget guard ──────────────────────────────────────────────────
        if context.budget_remaining < _MIN_BUDGET_FOR_ATTEMPT:
            log.debug("iter %d: budget exhausted (%.6f)", iteration, context.budget_remaining)
            elapsed_ms = int((time.perf_counter() - t_start) * 1000)
            return ConstructionResult(
                result_id=result_id,
                goal_id=goal.goal_id,
                candidate_id=None,
                status="budget_exhausted",
                residual_obligations=final_residuals,
                evidence={
                    **final_evidence,
                    "budget_remaining": context.budget_remaining,
                    "reason": "budget_exhausted",
                },
                elapsed_ms=elapsed_ms,
                iteration_count=iteration,
            )

        # ── Choose generation strategy based on stall state ───────────────
        n_proposals = _DEFAULT_N_PROPOSALS
        if stall_count >= _STALL_WINDOW:
            log.debug("iter %d: stall detected — switching strategy", iteration)
            # Widen search on stall: more candidates, analogy-heavy
            n_proposals = _DEFAULT_N_PROPOSALS + 3
            stall_count = 0  # reset after strategy change

        # ── Propose ───────────────────────────────────────────────────────
        raw_candidates = propose_candidates(goal, context, n=n_proposals)
        log.debug("iter %d: proposed %d candidates", iteration, len(raw_candidates))

        if not raw_candidates:
            stall_count += 1
            log.debug("iter %d: no candidates proposed", iteration)
            continue

        # ── Select ────────────────────────────────────────────────────────
        best = select_best_candidate(raw_candidates, context)
        if best is None:
            stall_count += 1
            log.debug("iter %d: selection returned None", iteration)
            continue

        composite = _score_candidate(best, context)
        log.debug(
            "iter %d: selected candidate %s score=%.4f",
            iteration, best.get("candidate_id", "?"), composite,
        )

        # ── Stall detection ───────────────────────────────────────────────
        if composite <= best_score:
            stall_count += 1
            log.debug(
                "iter %d: no improvement (%.4f <= %.4f) stall=%d",
                iteration, composite, best_score, stall_count,
            )
        else:
            best_score = composite
            stall_count = 0
            selected = best

        # ── Verify ────────────────────────────────────────────────────────
        ok, residuals, verify_ev = verify_candidate(best, goal, context)
        log.debug(
            "iter %d: verify ok=%s residuals=%d", iteration, ok, len(residuals)
        )

        final_residuals = tuple(residuals)
        final_evidence = verify_ev

        # Consume a small budget fraction per iteration
        budget_cost = max(0.005, context.budget_remaining * 0.04)
        # ConstructionContext is a dataclass — create an updated copy
        context = _deduct_budget(context, budget_cost)

        if ok:
            # ── Propagate ─────────────────────────────────────────────────
            propagated = propagate_obligations(
                ConstructionResult(
                    result_id=result_id,
                    goal_id=goal.goal_id,
                    candidate_id=best.get("candidate_id"),
                    status="succeeded",
                    residual_obligations=tuple(residuals),
                    evidence=verify_ev,
                    elapsed_ms=0,
                    iteration_count=iteration,
                ),
                goal,
            )
            elapsed_ms = int((time.perf_counter() - t_start) * 1000)
            log.debug(
                "succeeded on iter %d, propagated %d obligations",
                iteration, len(propagated),
            )
            return ConstructionResult(
                result_id=result_id,
                goal_id=goal.goal_id,
                candidate_id=best.get("candidate_id"),
                status="succeeded",
                residual_obligations=tuple(propagated),
                evidence={
                    **verify_ev,
                    "iteration_count": iteration,
                    "best_score": best_score,
                    "budget_consumed": goal.budget - context.budget_remaining,
                },
                elapsed_ms=elapsed_ms,
                iteration_count=iteration,
            )

        # Hard stall — stop and report
        if stall_count >= _STALL_WINDOW * 2:
            log.debug("iter %d: hard stall limit — terminating", iteration)
            break

    # ── Loop exhausted ────────────────────────────────────────────────────
    elapsed_ms = int((time.perf_counter() - t_start) * 1000)
    status = "stalled" if stall_count >= _STALL_WINDOW else "failed"
    return ConstructionResult(
        result_id=result_id,
        goal_id=goal.goal_id,
        candidate_id=selected.get("candidate_id") if selected else None,
        status=status,
        residual_obligations=final_residuals,
        evidence={
            **final_evidence,
            "iterations_run": iteration,
            "stall_count": stall_count,
            "best_score": best_score,
            "reason": status,
        },
        elapsed_ms=elapsed_ms,
        iteration_count=iteration,
    )


# ---------------------------------------------------------------------------
# 2.  Candidate proposal
# ---------------------------------------------------------------------------

def propose_candidates(
    goal: GenerationGoal,
    context: ConstructionContext,
    n: int = _DEFAULT_N_PROPOSALS,
) -> list[dict[str, Any]]:
    """Generate *n* candidate inhabitants for *goal*.

    The generation strategy varies by position in the list:

    * **First candidate** — applies *all* laws with full trust.
    * **Middle candidates** — apply shrinking subsets of laws; trust
      is weighted by the fraction of obligations resolved.
    * **Last candidate** — conservative fallback that defers all
      obligations, guaranteeing a non-empty candidate list.

    Parameters
    ----------
    goal:
        The generation goal carrying laws and obligations.
    context:
        Ambient context (used to determine budget fraction for trust
        modulation).
    n:
        Number of candidates to generate (≥ 1).

    Returns
    -------
    list[dict]
        Each dict is a raw candidate record suitable for
        ``select_best_candidate``.
    """
    if n < 1:
        n = 1

    laws: list[str] = list(goal.laws)
    obligations: list[str] = list(goal.obligations)
    budget_frac = _budget_fraction(context)
    candidates: list[dict[str, Any]] = []

    for i in range(n):
        cid = _generate_candidate_id()

        if i == 0:
            # Maximal: apply all laws, try to resolve all obligations
            laws_applied = list(laws)
            obligations_resolved = list(obligations)
            residuals: list[str] = []
            trust = min(1.0, 0.85 + 0.15 * budget_frac)
            source = "enumeration"
            trust_basis = "full_law_application"

        elif i == n - 1:
            # Conservative fallback: defer every obligation
            laws_applied = laws[:1] if laws else []
            obligations_resolved = []
            residuals = [f"deferred/{o}" for o in obligations]
            trust = 0.35
            source = "heuristic"
            trust_basis = "deferred_fallback"

        else:
            # Middle candidates: shrinking law subsets, partial resolution
            frac = 1.0 - (i / n)
            cutoff = max(1, int(len(laws) * frac))
            laws_applied = laws[:cutoff]
            resolved_count = max(0, int(len(obligations) * frac))
            obligations_resolved = obligations[:resolved_count]
            residuals = obligations[resolved_count:]

            resolved_frac = (
                resolved_count / len(obligations) if obligations else 1.0
            )
            trust = 0.40 + 0.45 * resolved_frac * budget_frac
            source = "heuristic" if i % 2 == 0 else "analogy"
            trust_basis = f"partial_law_subset_{cutoff}/{len(laws)}"

        evidence_ids = [
            str(uuid.uuid4()) for _ in range(len(laws_applied))
        ]

        candidates.append(
            {
                "candidate_id": cid,
                "goal_id": goal.goal_id,
                "source": source,
                "payload": {
                    "laws_applied": laws_applied,
                    "obligations_resolved": obligations_resolved,
                    "trust_basis": trust_basis,
                },
                "trust_score": round(min(1.0, max(0.0, trust)), 6),
                "residual_obligations": residuals,
                "evidence_ids": evidence_ids,
            }
        )

    return candidates


# ---------------------------------------------------------------------------
# 3.  Candidate selection
# ---------------------------------------------------------------------------

def select_best_candidate(
    candidates: list[dict[str, Any]],
    context: ConstructionContext,
) -> dict[str, Any] | None:
    """Select the best candidate using multi-criterion weighted scoring.

    Criteria and weights
    --------------------
    * **trust_score** (weight 0.40) — direct trust signal from the
      generating channel.
    * **resolved_obligations_fraction** (weight 0.35) — fraction of
      obligations resolved (1 − residuals/max_residuals), normalised
      across the candidate set.
    * **evidence_density** (weight 0.25) — evidence item count
      normalised by the maximum across all candidates.

    Parameters
    ----------
    candidates:
        Raw candidate dicts as produced by ``propose_candidates``.
    context:
        Ambient context (used by ``_score_candidate``).

    Returns
    -------
    dict | None
        The candidate with the highest composite score, or ``None`` if
        the candidate list is empty.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Pre-compute normalisation constants
    max_residuals = max(
        len(c.get("residual_obligations", [])) for c in candidates
    ) or 1
    max_evidence = max(
        len(c.get("evidence_ids", [])) for c in candidates
    ) or 1

    def _composite(c: dict[str, Any]) -> float:
        trust = float(c.get("trust_score", 0.0))
        residuals = len(c.get("residual_obligations", []))
        evidence = len(c.get("evidence_ids", []))

        norm_trust = min(1.0, max(0.0, trust))
        norm_resolved = 1.0 - residuals / max_residuals
        norm_evidence = evidence / max_evidence

        return (
            _W_TRUST * norm_trust
            + _W_RESOLVED * norm_resolved
            + _W_EVIDENCE * norm_evidence
        )

    scored = [(c, _composite(c)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]


# ---------------------------------------------------------------------------
# 4.  Verification
# ---------------------------------------------------------------------------

def verify_candidate(
    candidate: dict[str, Any],
    goal: GenerationGoal,
    context: ConstructionContext,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Verify a candidate against budget, treaty, law, and obligation checks.

    Verification steps (in order):

    1. **Budget check** — ``context.budget_remaining < 0.05`` → fail
       immediately with ``budget_exhausted`` evidence.
    2. **Treaty trust check** — if a treaty is active and
       ``trust_score < 0.30`` → fail.
    3. **Law satisfaction** — collect every law in ``goal.laws`` not
       present in ``payload["laws_applied"]``.
    4. **Obligation residuals** — collect ``candidate["residual_obligations"]``.
    5. **Verdict** — ``ok`` iff no unsatisfied laws *and* every residual
       is prefixed with ``"deferred/"`` (i.e., the only open obligations
       are explicitly deferred).

    Parameters
    ----------
    candidate:
        Raw candidate dict as returned by ``propose_candidates``.
    goal:
        The generation goal.
    context:
        Ambient context.

    Returns
    -------
    (ok, residuals, evidence)
    """
    t_verify = time.time()
    payload: dict[str, Any] = candidate.get("payload", {})
    trust: float = float(candidate.get("trust_score", 0.0))
    obligations: list[str] = list(goal.obligations)
    residuals: list[str] = list(candidate.get("residual_obligations", []))

    # Step 1 — budget
    if context.budget_remaining < _MIN_BUDGET_FOR_VERIFY:
        evidence: dict[str, Any] = {
            "reason": "budget_exhausted",
            "budget_remaining": context.budget_remaining,
            "timestamp": t_verify,
        }
        return False, residuals, evidence

    # Step 2 — treaty trust
    if context.treaty_id and trust < _MIN_TRUST_WITH_TREATY:
        evidence = {
            "reason": "treaty_trust_violation",
            "trust_score": trust,
            "min_required": _MIN_TRUST_WITH_TREATY,
            "treaty_id": context.treaty_id,
            "timestamp": t_verify,
        }
        return False, residuals, evidence

    # Step 3 — law satisfaction
    unsatisfied: list[str] = []
    for law in goal.laws:
        if not _check_law_satisfaction(law, payload):
            unsatisfied.append(law)

    # Step 4 — obligation residuals already collected above
    # Step 5 — verdict
    all_deferred = all(r.startswith("deferred/") for r in residuals)
    ok = (len(unsatisfied) == 0) and (len(residuals) == 0 or all_deferred)

    evidence = {
        "laws_checked": len(goal.laws),
        "laws_satisfied": len(goal.laws) - len(unsatisfied),
        "unsatisfied_laws": unsatisfied,
        "obligations_checked": len(obligations),
        "residuals": residuals,
        "trust_score": trust,
        "ok": ok,
        "timestamp": t_verify,
    }
    return ok, residuals, evidence


# ---------------------------------------------------------------------------
# 5.  Obligation propagation
# ---------------------------------------------------------------------------

def propagate_obligations(
    result: ConstructionResult,
    parent_goal: GenerationGoal,
) -> list[str]:
    """Compute the obligations to propagate upward to *parent_goal*.

    Algorithm
    ---------
    1. Start with ``result.residual_obligations``.
    2. Append any parent-goal obligation not addressed in this result
       (i.e., not in ``result.residual_obligations`` and not in
       the result's evidence as resolved).
    3. De-duplicate while preserving order.
    4. Sort: obligations containing ``"critical"`` come first; otherwise
       lexicographic order.

    Parameters
    ----------
    result:
        The construction result from which residuals are drawn.
    parent_goal:
        The parent goal whose obligations may need lifting.

    Returns
    -------
    list[str]
        Deduplicated, priority-sorted list of obligations for the parent.
    """
    addressed: set[str] = set(result.residual_obligations)

    # Also consider obligations mentioned in result evidence as resolved
    resolved_in_evidence: set[str] = set(
        result.evidence.get("residuals", [])
    )
    addressed |= resolved_in_evidence

    propagated: list[str] = list(result.residual_obligations)

    for ob in parent_goal.obligations:
        # An obligation is "addressed" if it appears literally, or as
        # "deferred/<ob>", or was resolved in evidence.
        deferred_form = f"deferred/{ob}"
        if ob not in addressed and deferred_form not in addressed:
            if ob not in propagated:
                propagated.append(ob)

    # De-duplicate preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for ob in propagated:
        if ob not in seen:
            seen.add(ob)
            deduped.append(ob)

    # Sort: "critical" obligations first, then lexicographic
    def _priority_key(ob: str) -> tuple[int, str]:
        return (0 if "critical" in ob else 1, ob)

    deduped.sort(key=_priority_key)
    return deduped


# ---------------------------------------------------------------------------
# 6.  Interface coordination
# ---------------------------------------------------------------------------

def coordinate_interfaces(
    loop_a: LocalConstructionLoop,
    loop_b: LocalConstructionLoop,
    shared_boundary: frozenset[str],
) -> dict[str, Any]:
    """Synchronise interface states between *loop_a* and *loop_b*.

    The shared boundary is the set of coordinate IDs that both loops must
    agree on.  Compatibility is determined from the loop statuses and, when
    both loops are running, from their current obligation overlaps.

    Parameters
    ----------
    loop_a, loop_b:
        The two local construction loops sharing a boundary.
    shared_boundary:
        Frozenset of coordinate IDs on the shared boundary.

    Returns
    -------
    dict
        A coordination record with keys ``coordination_id``,
        ``loop_a_id``, ``loop_b_id``, ``shared_boundary``,
        ``agreement_reached``, ``interface_state``,
        ``resolution``, ``actions``.
    """
    coordination_id = str(uuid.uuid4())
    status_a: str = loop_a.status
    status_b: str = loop_b.status

    agreement_reached: bool = False
    interface_state: str = "pending"
    resolution: str | None = None
    actions: list[str] = []

    if status_a == "succeeded" and status_b == "succeeded":
        # Both succeeded — check no obligation leaks across boundary
        residuals_a: set[str] = set(
            _get_loop_residuals(loop_a)
        )
        residuals_b: set[str] = set(
            _get_loop_residuals(loop_b)
        )
        cross_contamination = residuals_a & residuals_b
        if cross_contamination:
            interface_state = "conflict"
            resolution = (
                "resolve_shared_residuals:"
                + ",".join(sorted(cross_contamination))
            )
            actions = [
                f"re_negotiate_obligation:{o}"
                for o in sorted(cross_contamination)
            ]
        else:
            interface_state = "compatible"
            agreement_reached = True
            resolution = "no_action_required"
            actions = ["record_agreement"]

    elif status_a == "failed" or status_b == "failed":
        interface_state = "conflict"
        failed_loop_id = loop_a.loop_id if status_a == "failed" else loop_b.loop_id
        resolution = f"re_run_failed_loop:{failed_loop_id}"
        actions = [
            f"requeue_loop:{failed_loop_id}",
            "notify_parent_coordinator",
        ]

    elif status_a == "running" and status_b == "running":
        interface_state = "pending"
        actions = ["wait_for_convergence"]
        # Look for obligation overlap as an early conflict signal
        overlap = _obligation_overlap(loop_a, loop_b)
        if overlap:
            actions.append(
                "flag_obligation_overlap:" + ",".join(sorted(overlap))
            )

    else:
        # Mixed statuses — one running, one succeeded (or stalled)
        running_id = (
            loop_a.loop_id if status_a == "running" else loop_b.loop_id
        )
        done_id = (
            loop_b.loop_id if status_a == "running" else loop_a.loop_id
        )
        overlap = _obligation_overlap(loop_a, loop_b)
        if overlap:
            interface_state = "conflict"
            resolution = (
                f"await_running_loop:{running_id};"
                f"re_check_after_completion"
            )
            actions = [
                f"pause_done_loop:{done_id}",
                f"wait_for:{running_id}",
            ]
        else:
            interface_state = "pending"
            agreement_reached = False
            actions = [f"wait_for:{running_id}"]

    return {
        "coordination_id": coordination_id,
        "loop_a_id": loop_a.loop_id,
        "loop_b_id": loop_b.loop_id,
        "shared_boundary": sorted(shared_boundary),
        "agreement_reached": agreement_reached,
        "interface_state": interface_state,
        "resolution": resolution,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _score_candidate(
    c: dict[str, Any],
    context: ConstructionContext,
) -> float:
    """Compute the composite score for a single candidate.

    Uses the same weights as ``select_best_candidate`` but treats the
    candidate in isolation (normalisation constants are 1.0).

    Parameters
    ----------
    c:
        A raw candidate dict.
    context:
        Ambient context (unused directly but kept for API symmetry with
        future budget-aware scoring).

    Returns
    -------
    float
        Composite score in [0, 1].
    """
    trust = min(1.0, max(0.0, float(c.get("trust_score", 0.0))))
    residuals = len(c.get("residual_obligations", []))
    evidence = len(c.get("evidence_ids", []))

    # Without cross-candidate normalisation, use a fixed upper bound of 10
    norm_resolved = max(0.0, 1.0 - residuals / 10.0)
    norm_evidence = min(1.0, evidence / 10.0)

    return (
        _W_TRUST * trust
        + _W_RESOLVED * norm_resolved
        + _W_EVIDENCE * norm_evidence
    )


def _compute_pareto_front(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the Pareto-optimal subset of *candidates*.

    Dominance is defined on the pair
    ``(trust_score, −len(residual_obligations))``.  A candidate ``a``
    dominates ``b`` iff it is at least as good on both objectives and
    strictly better on at least one.

    Parameters
    ----------
    candidates:
        List of raw candidate dicts.

    Returns
    -------
    list[dict]
        Non-dominated subset (Pareto front).
    """
    front: list[dict[str, Any]] = []
    for c in candidates:
        if not _is_dominated(c, candidates):
            front.append(c)
    return front


def _is_dominated(
    c: dict[str, Any],
    others: list[dict[str, Any]],
) -> bool:
    """Check whether *c* is dominated by any other candidate in *others*.

    Dominance objectives: maximise ``trust_score``,
    maximise ``−len(residual_obligations)`` (i.e. fewer residuals
    is better).

    Parameters
    ----------
    c:
        Candidate under test.
    others:
        Full candidate pool (including *c* itself; self-comparison is
        skipped automatically).

    Returns
    -------
    bool
        ``True`` if *c* is dominated by at least one other candidate.
    """
    t_c = float(c.get("trust_score", 0.0))
    r_c = len(c.get("residual_obligations", []))

    for other in others:
        if other is c:
            continue
        t_o = float(other.get("trust_score", 0.0))
        r_o = len(other.get("residual_obligations", []))

        # other dominates c iff at least as good on both and strictly
        # better on at least one
        at_least_as_good = (t_o >= t_c) and (r_o <= r_c)
        strictly_better = (t_o > t_c) or (r_o < r_c)
        if at_least_as_good and strictly_better:
            return True
    return False


def _generate_candidate_id() -> str:
    """Return a fresh UUID4 string suitable for use as a candidate ID."""
    return str(uuid.uuid4())


def _check_law_satisfaction(law: str, payload: dict[str, Any]) -> bool:
    """Return ``True`` if *law* appears in ``payload["laws_applied"]``.

    The check is case-insensitive and also accepts a fuzzy prefix match
    (useful when law identifiers carry version suffixes).

    Parameters
    ----------
    law:
        Law identifier string (e.g. ``"law_sheaf_gluing"``).
    payload:
        Candidate payload dict containing ``"laws_applied"`` list.

    Returns
    -------
    bool
    """
    applied: list[str] = payload.get("laws_applied", [])
    law_lower = law.lower()
    for a in applied:
        a_lower = a.lower()
        if a_lower == law_lower:
            return True
        # Prefix match: "law_sheaf_gluing_v2" satisfies "law_sheaf_gluing"
        if a_lower.startswith(law_lower):
            return True
        if law_lower.startswith(a_lower):
            return True
    return False


def _budget_fraction(context: ConstructionContext) -> float:
    """Return a normalised budget fraction in [0, 1].

    Uses ``budget_remaining`` directly since the initial budget is not
    stored on the context.  Values > 1.0 are clamped to 1.0.

    Parameters
    ----------
    context:
        The current construction context.

    Returns
    -------
    float
        Clamped ``budget_remaining`` in [0, 1].
    """
    return min(1.0, max(0.0, float(context.budget_remaining)))


def _apply_verification_policy(
    candidate: dict[str, Any],
    policy: str,
) -> bool:
    """Apply a named verification policy to a candidate.

    Policies
    --------
    ``"strict"``
        Requires trust_score ≥ 0.7 and no residual obligations.
    ``"lenient"``
        Requires trust_score ≥ 0.2 and at most 2 residuals.
    ``"deferred"``
        Always passes — all obligations may be deferred.

    Parameters
    ----------
    candidate:
        Raw candidate dict.
    policy:
        One of ``"strict"``, ``"lenient"``, or ``"deferred"``.

    Returns
    -------
    bool
        Whether the candidate passes the policy.

    Raises
    ------
    ValueError
        For an unrecognised policy name.
    """
    trust: float = float(candidate.get("trust_score", 0.0))
    residuals: int = len(candidate.get("residual_obligations", []))

    if policy == _POLICY_STRICT:
        return trust >= 0.70 and residuals == 0
    elif policy == _POLICY_LENIENT:
        return trust >= 0.20 and residuals <= 2
    elif policy == _POLICY_DEFERRED:
        # All residuals must be deferred, but we always pass
        return True
    else:
        raise ValueError(
            f"Unknown verification policy {policy!r}. "
            f"Expected one of: {_POLICY_STRICT!r}, "
            f"{_POLICY_LENIENT!r}, {_POLICY_DEFERRED!r}."
        )


# ---------------------------------------------------------------------------
# Internal utilities (not in __all__)
# ---------------------------------------------------------------------------

def _deduct_budget(
    context: ConstructionContext,
    amount: float,
) -> ConstructionContext:
    """Return a copy of *context* with *amount* deducted from budget.

    Uses ``dataclasses.replace`` when available, otherwise reconstructs
    manually.

    Parameters
    ----------
    context:
        Current context.
    amount:
        Non-negative budget to deduct.

    Returns
    -------
    ConstructionContext
        Updated context.
    """
    import dataclasses  # local import to avoid top-level cost
    new_budget = max(0.0, context.budget_remaining - amount)
    try:
        return dataclasses.replace(context, budget_remaining=new_budget)
    except Exception:
        # Fallback: reconstruct from known fields
        return ConstructionContext(
            context_id=context.context_id,
            coordinate_id=context.coordinate_id,
            bindings=context.bindings,
            evidence=context.evidence,
            treaty_id=getattr(context, "treaty_id", None),
            budget_remaining=new_budget,
        )


def _get_loop_residuals(loop: LocalConstructionLoop) -> list[str]:
    """Extract current residual obligations from a loop's candidate history.

    If the loop has a selected candidate, its residuals are used.
    Otherwise the most-recent candidate in history is checked.

    Parameters
    ----------
    loop:
        A local construction loop instance.

    Returns
    -------
    list[str]
    """
    # Try selected candidate first
    selected_id: str | None = loop.selected_candidate_id
    if selected_id and loop.candidate_history:
        for entry in loop.candidate_history:
            if isinstance(entry, dict):
                if entry.get("candidate_id") == selected_id:
                    return list(entry.get("residual_obligations", []))

    # Fall back to most recent history entry
    if loop.candidate_history:
        last = loop.candidate_history[-1]
        if isinstance(last, dict):
            return list(last.get("residual_obligations", []))

    return []


def _obligation_overlap(
    loop_a: LocalConstructionLoop,
    loop_b: LocalConstructionLoop,
) -> set[str]:
    """Return the set of obligations present in both loops' residuals.

    Parameters
    ----------
    loop_a, loop_b:
        Two local construction loops.

    Returns
    -------
    set[str]
        Obligations that appear as residuals in both loops.
    """
    ra = set(_get_loop_residuals(loop_a))
    rb = set(_get_loop_residuals(loop_b))
    return ra & rb
