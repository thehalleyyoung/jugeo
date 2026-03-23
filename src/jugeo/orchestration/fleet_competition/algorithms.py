"""Core algorithms for fleet competition.

This module provides the fundamental algorithmic building blocks for
fleet-based competitive search as described in theory2.tex Ch46.  The
functions here operate on duck-typed inputs so they remain usable even
when parts of the jugeo model hierarchy are unavailable.

Algorithm inventory
-------------------
``pareto_optimal_bids``
    Filter a list of bids down to the Pareto-optimal frontier on
    (semantic_score, uncertainty).

``tournament_select``
    Probabilistic tournament selection for downstream bid refinement.

``bid_delta_lattice``
    Compute pairwise ``BidDelta`` objects for all ordered pairs of bids.

``calibrate_member``
    Aggregate calibration traces for a single member into a scalar report.

``challenge_resolution_cost``
    Estimate the computational cost of resolving a challenge record.

``fleet_convergence_score``
    Compute a scalar convergence metric from fleet history.

``competitive_search_step``
    Simulate one discrete step of the competitive search loop.

``optimal_bid_assignment``
    Greedy assignment of fleet members to frontier nodes.

Design notes
------------
All functions use duck typing and ``_safe_attr`` access rather than isinstance
checks.  This makes them compatible with both the real jugeo model dataclasses
and simple ``dict``/namespace objects used in tests.

All external jugeo imports are wrapped in ``try/except`` blocks.  Missing
imports produce ``Any``-typed stubs or ``pass``; the functions must work
without them.
"""

from __future__ import annotations

import logging
import math
import random
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Guarded external imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        CompetitiveBid,
        BidDelta,
        FleetRound,
        CalibrationTrace,
        BidStatus,
        RoundPhase,
    )
except Exception:
    pass  # All stubs handled via duck-typing below.

try:
    from jugeo.orchestration.fleet_competition.bid_evaluation import (
        BidEvaluation,
        MultiCriterionEvaluator,
        ParetoFilter,
        BidRanker,
        BidAuction,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, CompetitiveSearch, FleetHistory
except Exception:
    pass

try:
    from jugeo.orchestration.controller import ControlLaw, OrchestratorState
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import FrontierNode, FrontierBudget
except Exception:
    pass

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
except Exception:
    pass

try:
    from jugeo.orchestration.fleet_competition.challenge_protocol import (
        ChallengeRecord,
        AdjudicationPolicy,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.fleet_competition.calibration import (
        CalibrationTrace as CalTrace,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default semantic score when a bid has no ``semantic_score`` attribute.
DEFAULT_SEMANTIC_SCORE: float = 0.0

#: Default uncertainty when a bid has no ``uncertainty`` attribute.
DEFAULT_UNCERTAINTY: float = 1.0

#: Default trust ceiling when a member has no ``trust_ceiling`` attribute.
DEFAULT_TRUST_CEILING: float = 0.5

#: Minimum budget fraction that must remain for a member to be assignable.
MIN_BUDGET_FRACTION: float = 0.0

#: Number of recent history entries inspected by ``fleet_convergence_score``.
CONVERGENCE_HISTORY_WINDOW: int = 20

#: Upper bound on challenge resolution cost (from theory2.tex Ch46 §46.7).
MAX_RESOLUTION_COST: float = 100.0

#: Lower bound on challenge resolution cost.
MIN_RESOLUTION_COST: float = 0.1

#: Base cost for any challenge resolution.
BASE_RESOLUTION_COST: float = 1.0

#: Evidence gathering surcharge when ``require_evidence == True``.
EVIDENCE_COST_SURCHARGE: float = 0.5

#: Value returned by ``fleet_convergence_score`` when data is insufficient.
DEFAULT_CONVERGENCE_SCORE: float = 0.5

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Return ``getattr(obj, attr, default)`` without raising.

    Handles both attribute-style objects and plain dicts transparently.

    Parameters
    ----------
    obj:
        The object to read from.
    attr:
        The attribute name.
    default:
        Value returned when the attribute is absent.
    """
    if isinstance(obj, dict):
        return obj.get(attr, default)
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound.
    hi:
        Upper bound.
    """
    return max(lo, min(hi, value))


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return ``numerator / denominator`` or *default* when denominator is zero.

    Parameters
    ----------
    numerator:
        Dividend.
    denominator:
        Divisor.
    default:
        Fallback value when *denominator* is zero.
    """
    if denominator == 0.0:
        return default
    return numerator / denominator


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or 0.0 for an empty list."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _bid_semantic_score(bid: Any) -> float:
    """Extract and coerce the semantic score from a bid object.

    Falls back to ``DEFAULT_SEMANTIC_SCORE`` when the attribute is absent or
    non-numeric.
    """
    raw = _safe_attr(bid, "semantic_score", DEFAULT_SEMANTIC_SCORE)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SEMANTIC_SCORE


def _bid_uncertainty(bid: Any) -> float:
    """Extract and coerce the uncertainty from a bid object.

    Falls back to ``DEFAULT_UNCERTAINTY`` when absent.
    """
    raw = _safe_attr(bid, "uncertainty", DEFAULT_UNCERTAINTY)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_UNCERTAINTY


def _bid_id(bid: Any) -> str:
    """Return the bid's identifier as a string.

    Checks both ``bid_id`` and ``id`` attributes.
    """
    bid_id_val = _safe_attr(bid, "bid_id", None)
    if bid_id_val is not None:
        return str(bid_id_val)
    fallback = _safe_attr(bid, "id", None)
    if fallback is not None:
        return str(fallback)
    return str(uuid.uuid4())


def _make_bid_delta(
    bid_id_a: str,
    bid_id_b: str,
    value_delta: float,
    score_delta: float,
    uncertainty_delta: float,
    dominant: bool,
) -> Any:
    """Construct a ``BidDelta`` object, falling back to a plain dict.

    The real ``BidDelta`` dataclass is attempted first; if unavailable,
    returns a dict with the same keys.

    Parameters
    ----------
    bid_id_a:
        ID of the first bid in the comparison.
    bid_id_b:
        ID of the second bid.
    value_delta:
        Difference in ``bid_value`` (A - B).
    score_delta:
        Difference in ``semantic_score`` (A - B).
    uncertainty_delta:
        Difference in ``uncertainty`` (A - B).
    dominant:
        ``True`` when bid A Pareto-dominates bid B.
    """
    try:
        return BidDelta(  # type: ignore[name-defined]
            bid_id_a=bid_id_a,
            bid_id_b=bid_id_b,
            value_delta=value_delta,
            score_delta=score_delta,
            uncertainty_delta=uncertainty_delta,
            dominant=dominant,
        )
    except Exception:
        return {
            "bid_id_a": bid_id_a,
            "bid_id_b": bid_id_b,
            "value_delta": value_delta,
            "score_delta": score_delta,
            "uncertainty_delta": uncertainty_delta,
            "dominant": dominant,
        }


# ---------------------------------------------------------------------------
# Core algorithms
# ---------------------------------------------------------------------------


def pareto_optimal_bids(bids: list[Any]) -> list[Any]:
    """Return the Pareto-optimal subset of *bids*.

    A bid is Pareto-optimal when no other bid in the list dominates it.
    Dominance is defined on two objectives:

    * ``semantic_score`` – higher is better.
    * ``uncertainty`` – lower is better.

    Bid A **dominates** bid B when:
    * ``A.semantic_score >= B.semantic_score`` AND
    * ``A.uncertainty <= B.uncertainty``
    * with at least one strict inequality.

    The algorithm is O(n²) in the number of bids.  For very large bid sets
    (> 1 000), consider pre-sorting on one objective to reduce constant factors.

    Parameters
    ----------
    bids:
        List of bid objects (or dicts).  Empty lists return ``[]``.

    Returns
    -------
    list[Any]
        The subset of bids that are not dominated by any other bid in the
        input list.  The relative order of bids is preserved.

    Examples
    --------
    >>> pareto_optimal_bids([])
    []
    >>> # A bid with score=1.0, unc=0.0 dominates everything.
    """
    if not bids:
        return []

    n = len(bids)
    # Pre-extract scores for efficiency (avoid repeated attribute lookups).
    scores = [_bid_semantic_score(b) for b in bids]
    uncertainties = [_bid_uncertainty(b) for b in bids]

    # is_dominated[i] = True means bids[i] is dominated by at least one other bid.
    is_dominated = [False] * n

    for i in range(n):
        if is_dominated[i]:
            continue
        for j in range(n):
            if i == j:
                continue
            # Check whether j dominates i.
            # j dominates i if j is at least as good on both and strictly
            # better on at least one.
            j_better_score = scores[j] >= scores[i]
            j_better_unc = uncertainties[j] <= uncertainties[i]
            j_strictly_better = (scores[j] > scores[i]) or (uncertainties[j] < uncertainties[i])

            if j_better_score and j_better_unc and j_strictly_better:
                is_dominated[i] = True
                break  # No need to check further for bid i.

    pareto = [bids[i] for i in range(n) if not is_dominated[i]]
    logger.debug("pareto_optimal_bids: %d bids → %d pareto-optimal", n, len(pareto))
    return pareto


def tournament_select(
    bids: list[Any],
    k: int,
    tournament_size: int = 3,
) -> list[Any]:
    """Select *k* bids via tournament selection.

    In each tournament, ``tournament_size`` bids are drawn uniformly at
    random (with replacement) from *bids*, and the bid with the highest
    ``semantic_score`` is declared the winner.  The process is repeated *k*
    times, so the returned list may contain duplicates.

    Tournament selection is a classic method from evolutionary computation
    (theory2.tex Ch46 §46.3 cites Holland 1975) that balances exploration
    (small tournaments) and exploitation (large tournaments).

    Parameters
    ----------
    bids:
        Pool of bids to select from.
    k:
        Number of winners to return.
    tournament_size:
        Number of competitors per tournament.  Clamped to the length of
        *bids* when *bids* is smaller.

    Returns
    -------
    list[Any]
        List of *k* winning bids (with possible repetition).

    Raises
    ------
    ValueError
        When *bids* is empty or *k* <= 0.
    """
    if not bids:
        raise ValueError("Cannot run tournament selection on an empty bid list")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    actual_size = max(1, min(tournament_size, len(bids)))
    winners: list[Any] = []

    for _ in range(k):
        # Draw competitors (with replacement to handle small pools).
        competitors = random.choices(bids, k=actual_size)
        # The winner is the competitor with the highest semantic score.
        winner = max(competitors, key=_bid_semantic_score)
        winners.append(winner)

    logger.debug(
        "tournament_select: pool=%d, k=%d, tournament_size=%d → %d winners",
        len(bids),
        k,
        actual_size,
        len(winners),
    )
    return winners


def bid_delta_lattice(bids: list[Any]) -> dict[tuple[str, str], Any]:
    """Compute the pairwise ``BidDelta`` lattice for all ordered pairs.

    For every ordered pair (A, B) where A ≠ B, the lattice stores a
    ``BidDelta`` describing:

    * ``value_delta`` = A.bid_value - B.bid_value
    * ``score_delta`` = A.semantic_score - B.semantic_score
    * ``uncertainty_delta`` = A.uncertainty - B.uncertainty
    * ``dominant`` = True when A Pareto-dominates B.

    The lattice is asymmetric: ``lattice[(a, b)]`` and ``lattice[(b, a)]``
    describe the directed comparisons A→B and B→A respectively.

    Computational cost: O(n²).

    Parameters
    ----------
    bids:
        List of bids to compare.  Empty or singleton lists return ``{}``.

    Returns
    -------
    dict[tuple[str, str], Any]
        Mapping from ``(bid_id_a, bid_id_b)`` to a ``BidDelta`` object
        (or equivalent dict when the model class is unavailable).
    """
    if len(bids) < 2:
        return {}

    lattice: dict[tuple[str, str], Any] = {}

    for i, bid_a in enumerate(bids):
        id_a = _bid_id(bid_a)
        score_a = _bid_semantic_score(bid_a)
        unc_a = _bid_uncertainty(bid_a)
        value_a = float(_safe_attr(bid_a, "bid_value", 0.0))

        for j, bid_b in enumerate(bids):
            if i == j:
                continue
            id_b = _bid_id(bid_b)
            score_b = _bid_semantic_score(bid_b)
            unc_b = _bid_uncertainty(bid_b)
            value_b = float(_safe_attr(bid_b, "bid_value", 0.0))

            # Compute deltas (A minus B).
            value_delta = value_a - value_b
            score_delta = score_a - score_b
            uncertainty_delta = unc_a - unc_b

            # A dominates B when A is no worse on both objectives and
            # strictly better on at least one.
            a_dom_b = (
                score_a >= score_b
                and unc_a <= unc_b
                and (score_a > score_b or unc_a < unc_b)
            )

            lattice[(id_a, id_b)] = _make_bid_delta(
                bid_id_a=id_a,
                bid_id_b=id_b,
                value_delta=value_delta,
                score_delta=score_delta,
                uncertainty_delta=uncertainty_delta,
                dominant=a_dom_b,
            )

    logger.debug(
        "bid_delta_lattice: %d bids → %d delta entries",
        len(bids),
        len(lattice),
    )
    return lattice


def calibrate_member(
    member_id: str,
    traces: list[Any],
    trust_algebra: Any = None,
) -> dict:
    """Aggregate calibration traces for a single member into a summary dict.

    Computes simple statistics from the combined accuracy, latency, and trust
    histories across all traces supplied for *member_id*.

    Parameters
    ----------
    member_id:
        The fleet member to summarise.
    traces:
        List of ``CalibrationTrace`` objects (or duck-typed equivalents).
        Only traces where ``trace.member_id == member_id`` are used.
    trust_algebra:
        Optional trust algebra object.  When not ``None`` and it has a
        ``compose`` method, that method is called with the trust values to
        produce the final trust score; otherwise the mean is used.

    Returns
    -------
    dict
        Dictionary with keys:
        * ``member_id`` – echoes the input.
        * ``calibration_score`` – scalar in [0, 1] combining accuracy and trust.
        * ``accuracy`` – mean accuracy across all matching traces.
        * ``latency`` – mean latency across all matching traces.
        * ``trust`` – trust score (composed or mean).
        * ``samples`` – total number of accuracy samples found.
        * ``recommendation`` – short human-readable string.
    """
    # Filter to traces for this member only.
    member_traces = [
        t for t in traces
        if str(_safe_attr(t, "member_id", "")) == member_id
    ]

    # Collect raw history lists.
    acc_values: list[float] = []
    lat_values: list[float] = []
    trust_values: list[float] = []

    for trace in member_traces:
        # Accuracy history.
        acc_hist = _safe_list_attr(trace, "accuracy_history")
        acc_values.extend(float(v) for v in acc_hist)

        # Latency history.
        lat_hist = _safe_list_attr(trace, "latency_history")
        lat_values.extend(float(v) for v in lat_hist)

        # Trust history.
        trust_hist = _safe_list_attr(trace, "trust_history")
        trust_values.extend(float(v) for v in trust_hist)

    # Compute statistics.
    accuracy = _mean(acc_values) if acc_values else 0.5
    latency = _mean(lat_values) if lat_values else 1.0
    samples = len(acc_values)

    # Trust: use algebra if available.
    if trust_values:
        if trust_algebra is not None and hasattr(trust_algebra, "compose"):
            try:
                trust = float(trust_algebra.compose(trust_values))
            except Exception:
                trust = _mean(trust_values)
        else:
            trust = _mean(trust_values)
    else:
        trust = 0.5

    # Calibration score: weighted combination of accuracy and trust.
    lat_penalty = _clamp(latency / 10.0, 0.0, 0.3)
    calibration_score = _clamp(accuracy * 0.5 + trust * 0.3 + 0.2 - lat_penalty, 0.0, 1.0)

    # Recommendation.
    if samples == 0:
        recommendation = "No calibration data available; cannot assess member"
    elif accuracy < 0.4:
        recommendation = f"Low accuracy ({accuracy:.2%}); review member configuration"
    elif latency > 5.0:
        recommendation = f"High latency ({latency:.2f}s); investigate infrastructure"
    elif calibration_score < 0.5:
        recommendation = "Below-average calibration score; monitor closely"
    else:
        recommendation = "Calibration is healthy"

    logger.debug(
        "calibrate_member(%s): acc=%.3f lat=%.3f trust=%.3f score=%.3f samples=%d",
        member_id,
        accuracy,
        latency,
        trust,
        calibration_score,
        samples,
    )

    return {
        "member_id": member_id,
        "calibration_score": calibration_score,
        "accuracy": accuracy,
        "latency": latency,
        "trust": trust,
        "samples": samples,
        "recommendation": recommendation,
    }


def challenge_resolution_cost(
    record: Any,
    adjudication_policy: Any = None,
) -> float:
    """Estimate the computational cost of resolving *record*.

    The cost model is:

        cost = (base_cost * age_factor) + evidence_cost

    where:
    * ``base_cost = 1.0``
    * ``age_factor = min(2.0, age_seconds / 60.0)``  (saturates at 2× after 2 min)
    * ``evidence_cost = 0.5`` when ``adjudication_policy.require_evidence == True``
      (else 0).

    The result is clamped to [``MIN_RESOLUTION_COST``, ``MAX_RESOLUTION_COST``].

    Parameters
    ----------
    record:
        A challenge record with an optional ``created_at`` timestamp.
    adjudication_policy:
        Optional policy object with a ``require_evidence`` boolean attribute.

    Returns
    -------
    float
        Estimated cost in arbitrary computational units.
    """
    base_cost = BASE_RESOLUTION_COST

    # Age factor.
    created_at = _safe_attr(record, "created_at", None)
    if created_at is not None:
        age_seconds = time.time() - float(created_at)
        age_factor = min(2.0, max(0.0, age_seconds / 60.0))
    else:
        # No timestamp → assume minimal age.
        age_factor = 0.0

    # Evidence surcharge.
    evidence_cost = 0.0
    require_evidence = _safe_attr(adjudication_policy, "require_evidence", None)
    if require_evidence is True:
        evidence_cost = EVIDENCE_COST_SURCHARGE

    raw_cost = base_cost * age_factor + evidence_cost
    clamped = _clamp(raw_cost, MIN_RESOLUTION_COST, MAX_RESOLUTION_COST)

    logger.debug(
        "challenge_resolution_cost: age_factor=%.3f evidence_cost=%.2f raw=%.3f clamped=%.3f",
        age_factor,
        evidence_cost,
        raw_cost,
        clamped,
    )
    return clamped


def fleet_convergence_score(fleet_history: Any) -> float:
    """Compute a scalar convergence metric from fleet history.

    Convergence is measured by examining the ``BidDelta`` entries in
    *fleet_history*.  As the fleet converges, bid values stop changing
    and the mean absolute ``value_delta`` approaches zero:

        convergence = 1 / (1 + mean(|value_delta|))

    The function inspects up to ``CONVERGENCE_HISTORY_WINDOW`` recent
    entries.  If no usable data is found, returns
    ``DEFAULT_CONVERGENCE_SCORE`` (0.5).

    Parameters
    ----------
    fleet_history:
        An object with one of the following shapes:
        * Has a ``get_history()`` method → call it to get a list.
        * Has a ``history`` attribute → use it directly.
        * Has a ``rounds`` attribute → extract bids from rounds.

    Returns
    -------
    float
        Convergence score in [0, 1].  Values near 1 indicate the fleet
        has converged; values near 0 indicate high disagreement.
    """
    deltas: list[float] = []

    # Attempt 1: get_history() method.
    get_history_fn = _safe_attr(fleet_history, "get_history", None)
    if callable(get_history_fn):
        try:
            history_items = list(get_history_fn())
            deltas = _extract_deltas_from_list(history_items)
        except Exception as exc:
            logger.debug("fleet_convergence_score: get_history() failed: %s", exc)

    # Attempt 2: history attribute.
    if not deltas:
        history_attr = _safe_attr(fleet_history, "history", None)
        if isinstance(history_attr, (list, tuple)):
            deltas = _extract_deltas_from_list(list(history_attr))

    # Attempt 3: rounds attribute.
    if not deltas:
        rounds_attr = _safe_attr(fleet_history, "rounds", None)
        if isinstance(rounds_attr, (list, tuple)):
            deltas = _extract_deltas_from_rounds(list(rounds_attr))

    if not deltas:
        logger.debug(
            "fleet_convergence_score: no usable history found, returning default %.2f",
            DEFAULT_CONVERGENCE_SCORE,
        )
        return DEFAULT_CONVERGENCE_SCORE

    # Use only the most recent window.
    window = deltas[-CONVERGENCE_HISTORY_WINDOW:]
    mean_abs_delta = _mean([abs(d) for d in window])

    # Convergence formula: 1 / (1 + mean_abs_delta).
    score = 1.0 / (1.0 + mean_abs_delta)
    return _clamp(score, 0.0, 1.0)


def competitive_search_step(
    fleet: Any,
    round_: Any,
    control_law: Any,
    budget: float,
) -> dict:
    """Simulate one step of the competitive search loop.

    The step proceeds as follows:

    1. Extract the bid list from *round_* (``round_.bids`` or empty list).
    2. Compute the Pareto-optimal subset of those bids.
    3. Run tournament selection to pick up to 3 candidates from the Pareto
       frontier.
    4. Determine the winner as the candidate with the highest
       ``semantic_score``.
    5. Deduct the winner's ``bid_value`` from *budget*.

    Parameters
    ----------
    fleet:
        The fleet object (used for context; not directly queried in this step).
    round_:
        The current competition round.  Must have a ``bids`` attribute (list).
    control_law:
        The orchestrator control law (not queried directly here; included for
        API completeness and future use).
    budget:
        Remaining budget before this step.

    Returns
    -------
    dict
        Dictionary with keys:
        * ``winner`` – ``bid_id`` of the winning bid, or ``None``.
        * ``pareto_count`` – number of Pareto-optimal bids found.
        * ``candidates`` – list of bid IDs selected by tournament.
        * ``budget_remaining`` – budget after deducting step cost.
        * ``step_cost`` – the cost deducted this step.
    """
    # 1. Extract bids from the round.
    bids_raw = _safe_attr(round_, "bids", None)
    if isinstance(bids_raw, (list, tuple)):
        bids = list(bids_raw)
    else:
        bids = []

    logger.debug("competitive_search_step: %d bids in round", len(bids))

    # 2. Pareto-optimal subset.
    if bids:
        pareto = pareto_optimal_bids(bids)
    else:
        pareto = []
    pareto_count = len(pareto)

    # 3. Tournament selection from Pareto set.
    winner_bid = None
    candidate_ids: list[str] = []
    step_cost = 0.0

    if pareto:
        n_candidates = min(3, len(pareto))
        try:
            candidates = tournament_select(pareto, k=n_candidates)
        except ValueError:
            candidates = pareto[:n_candidates]

        candidate_ids = [_bid_id(c) for c in candidates]

        # 4. Winner = highest semantic score among candidates.
        winner_bid = max(candidates, key=_bid_semantic_score)

        # 5. Deduct cost.
        step_cost = float(_safe_attr(winner_bid, "bid_value", 0.0))

    budget_remaining = budget - step_cost

    result = {
        "winner": _bid_id(winner_bid) if winner_bid is not None else None,
        "pareto_count": pareto_count,
        "candidates": candidate_ids,
        "budget_remaining": budget_remaining,
        "step_cost": step_cost,
    }

    logger.debug(
        "competitive_search_step: winner=%s pareto=%d budget_remaining=%.4f",
        result["winner"],
        pareto_count,
        budget_remaining,
    )
    return result


def optimal_bid_assignment(
    fleet: Any,
    frontier: Any,
    budget_map: dict[str, float],
) -> dict[str, str]:
    """Assign fleet members to frontier nodes using a greedy trust-based heuristic.

    Algorithm
    ---------
    For each frontier node (in order of appearance), find the fleet member
    that:
    1. Has ``budget_map.get(member_id, 0) > MIN_BUDGET_FRACTION``, and
    2. Has the highest ``trust_ceiling`` attribute.

    A member may only be assigned to one node per call (once used they are
    marked as unavailable for subsequent nodes).

    Parameters
    ----------
    fleet:
        Fleet object with a ``members`` attribute (list or dict of members).
    frontier:
        Frontier object with a ``nodes`` attribute (list or dict of nodes).
    budget_map:
        Mapping from member ID to available budget.  Members with budget ≤ 0
        are not eligible.

    Returns
    -------
    dict[str, str]
        Mapping from ``node_id`` to ``member_id``.  Nodes that could not be
        assigned (no eligible members remain) are omitted.
    """
    # Extract frontier nodes.
    nodes_raw = _safe_attr(frontier, "nodes", None)
    if isinstance(nodes_raw, dict):
        nodes = list(nodes_raw.values())
    elif isinstance(nodes_raw, (list, tuple)):
        nodes = list(nodes_raw)
    else:
        nodes = []

    # Extract fleet members.
    members_raw = _safe_attr(fleet, "members", None)
    if isinstance(members_raw, dict):
        members = list(members_raw.values())
    elif isinstance(members_raw, (list, tuple)):
        members = list(members_raw)
    else:
        members = []

    logger.debug(
        "optimal_bid_assignment: %d nodes, %d members",
        len(nodes),
        len(members),
    )

    if not nodes or not members:
        return {}

    # Track which members are still available.
    available: set[str] = set()
    for m in members:
        mid = str(_safe_attr(m, "member_id", _safe_attr(m, "id", "")))
        if mid and budget_map.get(mid, 0.0) > MIN_BUDGET_FRACTION:
            available.add(mid)

    # Build a lookup: member_id → member object.
    member_by_id: dict[str, Any] = {}
    for m in members:
        mid = str(_safe_attr(m, "member_id", _safe_attr(m, "id", "")))
        if mid:
            member_by_id[mid] = m

    assignment: dict[str, str] = {}

    for node in nodes:
        node_id = str(_safe_attr(node, "node_id", _safe_attr(node, "id", "")))
        if not node_id:
            continue

        if not available:
            logger.debug("No more available members; stopping assignment at node %s", node_id)
            break

        # Choose the available member with the highest trust_ceiling.
        best_mid: str | None = None
        best_trust: float = -math.inf

        for mid in available:
            m_obj = member_by_id.get(mid)
            if m_obj is None:
                continue
            trust = float(_safe_attr(m_obj, "trust_ceiling", DEFAULT_TRUST_CEILING))
            if trust > best_trust:
                best_trust = trust
                best_mid = mid

        if best_mid is not None:
            assignment[node_id] = best_mid
            available.discard(best_mid)
            logger.debug("Assigned member %s → node %s (trust=%.3f)", best_mid, node_id, best_trust)

    return assignment


# ---------------------------------------------------------------------------
# Internal helpers (continued)
# ---------------------------------------------------------------------------


def _safe_list_attr(obj: Any, attr: str) -> list:
    """Return the list attribute *attr* of *obj*, or empty list.

    Unlike ``_safe_attr``, always returns a fresh list copy.
    """
    val = _safe_attr(obj, attr, None)
    if isinstance(val, (list, tuple)):
        return list(val)
    return []


def _extract_deltas_from_list(items: list[Any]) -> list[float]:
    """Extract absolute value_delta values from a list of BidDelta-like objects.

    Accepts both dict-style and attribute-style objects.  Items without a
    ``value_delta`` field are silently skipped.
    """
    deltas: list[float] = []
    for item in items:
        vd = _safe_attr(item, "value_delta", None)
        if vd is not None:
            try:
                deltas.append(float(vd))
            except (TypeError, ValueError):
                pass
    return deltas


def _extract_deltas_from_rounds(rounds: list[Any]) -> list[float]:
    """Extract bid value deltas from a list of round objects.

    For each round, looks for ``round_.bids`` and computes the range
    (max - min) of ``bid_value`` as a proxy for convergence spread.
    """
    deltas: list[float] = []
    for round_ in rounds:
        bids_raw = _safe_attr(round_, "bids", None)
        if not isinstance(bids_raw, (list, tuple)):
            continue
        bid_values = []
        for b in bids_raw:
            bv = _safe_attr(b, "bid_value", None)
            if bv is not None:
                try:
                    bid_values.append(float(bv))
                except (TypeError, ValueError):
                    pass
        if len(bid_values) >= 2:
            deltas.append(max(bid_values) - min(bid_values))
    return deltas


# ---------------------------------------------------------------------------
# Extended utility algorithms
# ---------------------------------------------------------------------------


def top_k_bids(bids: list[Any], k: int) -> list[Any]:
    """Return the top *k* bids ranked by ``semantic_score`` (descending).

    When ``k >= len(bids)`` the entire list is returned (sorted).

    Parameters
    ----------
    bids:
        List of bid objects.
    k:
        Number of top bids to return.

    Returns
    -------
    list[Any]
        At most *k* bids, sorted by ``semantic_score`` descending.
    """
    if not bids:
        return []
    k = max(1, k)
    sorted_bids = sorted(bids, key=_bid_semantic_score, reverse=True)
    return sorted_bids[:k]


def bid_diversity_score(bids: list[Any]) -> float:
    """Measure the diversity of a bid set as normalised standard deviation of scores.

    A score near 1 indicates high diversity (many distinct semantic score
    levels); a score near 0 indicates all bids are clustered at similar
    scores.

    Parameters
    ----------
    bids:
        List of bid objects.

    Returns
    -------
    float
        Diversity score in [0, 1].  Returns 0.0 for fewer than 2 bids.
    """
    if len(bids) < 2:
        return 0.0
    scores = [_bid_semantic_score(b) for b in bids]
    mean_s = _mean(scores)
    variance = sum((s - mean_s) ** 2 for s in scores) / len(scores)
    std_s = math.sqrt(variance)
    # Normalise to [0, 1] by dividing by the max possible std (0.5 for binary
    # values in [0, 1]).
    return _clamp(std_s / 0.5, 0.0, 1.0)


def filter_bids_by_status(bids: list[Any], desired_status: str) -> list[Any]:
    """Return only bids whose ``status`` matches *desired_status*.

    Parameters
    ----------
    bids:
        List of bid objects.
    desired_status:
        Status string to filter on (e.g. ``"pending"``, ``"accepted"``).
    """
    return [b for b in bids if str(_safe_attr(b, "status", "")) == desired_status]


def summarise_round(round_: Any) -> dict:
    """Produce a summary dictionary for a competition round.

    Extracts high-level statistics: bid count, best score, worst score, mean
    score, Pareto frontier size, and round phase.

    Parameters
    ----------
    round_:
        A ``FleetRound`` (or duck-typed equivalent) with a ``bids`` attribute.

    Returns
    -------
    dict
        Summary with keys: ``round_id``, ``phase``, ``bid_count``,
        ``best_score``, ``worst_score``, ``mean_score``, ``pareto_count``.
    """
    bids_raw = _safe_attr(round_, "bids", [])
    bids = list(bids_raw) if isinstance(bids_raw, (list, tuple)) else []
    scores = [_bid_semantic_score(b) for b in bids]

    pareto = pareto_optimal_bids(bids) if bids else []

    round_id = str(_safe_attr(round_, "round_id", _safe_attr(round_, "id", "?")))
    phase_raw = _safe_attr(round_, "phase", None)
    phase = str(phase_raw.name if hasattr(phase_raw, "name") else phase_raw or "unknown")

    return {
        "round_id": round_id,
        "phase": phase,
        "bid_count": len(bids),
        "best_score": max(scores) if scores else 0.0,
        "worst_score": min(scores) if scores else 0.0,
        "mean_score": _mean(scores),
        "pareto_count": len(pareto),
    }


def estimate_round_budget(
    bids: list[Any],
    multiplier: float = 1.2,
    floor: float = 0.1,
) -> float:
    """Estimate the budget required to process a round of *bids*.

    Uses the sum of ``bid_value`` attributes, scaled by *multiplier* to
    account for overhead, with a minimum of *floor*.

    Parameters
    ----------
    bids:
        List of bids in the round.
    multiplier:
        Scaling factor for overhead.
    floor:
        Minimum budget estimate.

    Returns
    -------
    float
        Estimated budget required.
    """
    total = sum(float(_safe_attr(b, "bid_value", 0.0)) for b in bids)
    return max(floor, total * multiplier)


def normalise_bid_scores(bids: list[Any]) -> list[float]:
    """Return the ``semantic_score`` values of *bids* normalised to [0, 1].

    Min-max normalisation is applied.  When all scores are equal, returns
    a list of 0.5 values.

    Parameters
    ----------
    bids:
        List of bid objects.

    Returns
    -------
    list[float]
        Normalised scores, parallel to *bids*.
    """
    if not bids:
        return []
    raw = [_bid_semantic_score(b) for b in bids]
    lo = min(raw)
    hi = max(raw)
    if hi == lo:
        return [0.5] * len(raw)
    return [(s - lo) / (hi - lo) for s in raw]


def rank_bids(bids: list[Any]) -> list[tuple[int, Any]]:
    """Return bids sorted by ``semantic_score`` with 1-based rank annotations.

    Parameters
    ----------
    bids:
        List of bid objects.

    Returns
    -------
    list[tuple[int, Any]]
        List of ``(rank, bid)`` tuples sorted from rank 1 (best) to N (worst).
    """
    sorted_bids = sorted(bids, key=_bid_semantic_score, reverse=True)
    return [(rank + 1, bid) for rank, bid in enumerate(sorted_bids)]


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    logging.basicConfig(level=logging.DEBUG)

    # Build a synthetic bid pool.
    class _FakeBid:
        def __init__(self, bid_id: str, score: float, unc: float, val: float) -> None:
            self.bid_id = bid_id
            self.semantic_score = score
            self.uncertainty = unc
            self.bid_value = val
            self.status = "pending"

    bids = [
        _FakeBid("b1", 0.9, 0.1, 10.0),
        _FakeBid("b2", 0.8, 0.2, 8.0),
        _FakeBid("b3", 0.7, 0.05, 7.0),  # low uncertainty → might dominate b2
        _FakeBid("b4", 0.6, 0.3, 5.0),
        _FakeBid("b5", 0.5, 0.4, 3.0),
    ]

    print("=== pareto_optimal_bids ===")
    pareto = pareto_optimal_bids(bids)
    pprint.pprint([b.bid_id for b in pareto])

    print("\n=== tournament_select ===")
    selected = tournament_select(bids, k=3, tournament_size=2)
    pprint.pprint([b.bid_id for b in selected])

    print("\n=== bid_delta_lattice (first 4 entries) ===")
    lattice = bid_delta_lattice(bids[:3])
    for pair, delta in list(lattice.items())[:4]:
        pprint.pprint({pair: delta})

    print("\n=== calibrate_member ===")

    class _FakeTrace:
        def __init__(self, mid: str) -> None:
            self.member_id = mid
            self.accuracy_history = [0.8, 0.85, 0.9]
            self.latency_history = [0.5, 0.6, 0.4]
            self.trust_history = [0.7, 0.75, 0.8]
            self.last_calibrated_at = time.time()

    traces = [_FakeTrace("alice"), _FakeTrace("alice"), _FakeTrace("bob")]
    result = calibrate_member("alice", traces)
    pprint.pprint(result)

    print("\n=== challenge_resolution_cost ===")

    class _FakeRecord:
        created_at = time.time() - 90.0  # 90 seconds old

    class _FakePolicy:
        require_evidence = True

    cost = challenge_resolution_cost(_FakeRecord(), _FakePolicy())
    print(f"cost = {cost:.4f}")

    print("\n=== competitive_search_step ===")

    class _FakeRound:
        bids = bids

    step = competitive_search_step(None, _FakeRound(), None, budget=100.0)
    pprint.pprint(step)

    print("\n=== fleet_convergence_score ===")

    class _FakeHistory:
        history = [
            {"value_delta": 0.5},
            {"value_delta": 0.4},
            {"value_delta": 0.3},
            {"value_delta": 0.2},
        ]

    score = fleet_convergence_score(_FakeHistory())
    print(f"convergence = {score:.4f}")


# ---------------------------------------------------------------------------
# Cross-subsystem integration: geometry, evidence, solver, encodings, judgments
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.descent import DescentPhase, RepairFrontier
except Exception:
    DescentPhase = None  # type: ignore[assignment,misc]
    RepairFrontier = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import CertificateBuilder, CertificateVerifier
except Exception:
    CertificateBuilder = None  # type: ignore[assignment,misc]
    CertificateVerifier = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encode_judgment
except Exception:
    encode_judgment = None  # type: ignore[assignment]


def fleet_bid_with_descent_context(bid, descent_result):
    """Augment a fleet bid with descent-phase context from jugeo.geometry.descent.

    The descent phase (exploration / refinement / convergence) shifts how
    bids are scored: early phases favour breadth, late phases favour precision.
    """
    phase = getattr(descent_result, "phase", None)
    if DescentPhase is not None and phase is not None:
        phase_label = phase.value if hasattr(phase, "value") else str(phase)
    else:
        phase_label = "unknown"

    base_score = getattr(bid, "semantic_score", 0.5)
    if phase_label in ("refinement", "REFINING"):
        adjusted = base_score * 1.2
    elif phase_label in ("convergence", "CONVERGING"):
        adjusted = base_score * 0.9
    else:
        adjusted = base_score

    return {
        "bid": bid,
        "original_score": base_score,
        "adjusted_score": min(adjusted, 1.0),
        "descent_phase": phase_label,
        "subsystem": "jugeo.geometry.descent",
    }


def certify_fleet_winner(winner_bid):
    """Issue a certificate for the fleet competition winner via jugeo.evidence.certificates."""
    if CertificateBuilder is None:
        return {"certified": False, "reason": "CertificateBuilder unavailable",
                "subsystem": "jugeo.evidence.certificates"}
    try:
        builder = CertificateBuilder()
        payload = f"winner:{getattr(winner_bid, 'member_id', 'anon')}"
        if hasattr(builder, "set_payload"):
            builder.set_payload(payload)
        if hasattr(builder, "set_issuer"):
            builder.set_issuer("orchestration.fleet_competition")
        cert = builder.build() if hasattr(builder, "build") else None
        return {"certified": cert is not None,
                "certificate_id": getattr(cert, "id", None),
                "subsystem": "jugeo.evidence.certificates"}
    except Exception as exc:
        return {"certified": False, "reason": str(exc),
                "subsystem": "jugeo.evidence.certificates"}


def encode_fleet_result(result):
    """Encode a fleet competition result via jugeo.encodings for downstream consumption."""
    if encode_judgment is None:
        return {"encoded": False, "reason": "encode_judgment unavailable",
                "subsystem": "jugeo.encodings"}
    try:
        encoded = encode_judgment(result)
        return {"encoded": True, "keys": list(encoded.keys()) if isinstance(encoded, dict) else [],
                "subsystem": "jugeo.encodings"}
    except Exception as exc:
        return {"encoded": False, "reason": str(exc), "subsystem": "jugeo.encodings"}
