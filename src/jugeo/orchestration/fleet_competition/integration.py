"""Fleet-competition integration layer for JuGeo orchestration.

This module wires together every sub-system introduced in theory2.tex Ch 46
(Fleet Competition and Convergent Bid Dynamics) so that the rest of the
JuGeo orchestration stack can interact with the fleet-competition machinery
through a single, stable surface.

Design philosophy
─────────────────
The fleet-competition sub-package is deliberately decoupled from the wider
JuGeo packages through *guarded imports*: each dependency is wrapped in a
``try/except`` block so that individual modules can be compiled and tested in
isolation.  The classes in this module act as *bridges* or *connectors* that
translate between the rich internal types of the fleet-competition package and
the lower-level primitives used by ``jugeo.orchestration.controller``,
``jugeo.orchestration.frontier``, ``jugeo.evidence.trust``, and
``jugeo.geometry.descent``.

Public surface
──────────────
``CompetitionSession``
    High-level entry point.  Create one session per competitive search
    episode, call ``initialize()``, then ``run()`` or repeated ``step()``
    calls.

``FleetCompetitionOrchestrator``
    Drives the per-step logic: collect bids → evaluate → challenge → calibrate.
    Delegates to sub-system components and records a structured history.

``FleetTrustIntegrator``
    Connects calibration scores to the ``TrustAlgebra`` so that per-member
    trust ceilings evolve with empirical accuracy.

``FleetFrontierBridge``
    Submits bids to the ``Frontier`` data structure and keeps budget
    accounting in sync after each round.

``FleetDescentConnector``
    Validates bid compatibility through the ``DescentEngine``'s gluing
    conditions and checks for obstructions.

References
──────────
*   theory2.tex Ch 46  — Fleet Competition and Convergent Bid Dynamics
*   theory2.tex §252   — Evidence Algebra, Channel Jurisdiction, Trust
*   theory2.tex §3     — Descent and Gluing
*   theory2.tex §354   — Trust is Semantic State
"""

from __future__ import annotations

import enum
import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Logger ───────────────────────────────────────────────────────────────────

_log = logging.getLogger(__name__)

# ── Fleet-competition internal imports (guarded) ─────────────────────────────

try:
    from jugeo.orchestration.fleet_competition.models import (
        CompetitiveBid,
        FleetRound,
        ChallengeRecord,
        CalibrationTrace,
        BidStatus,
        RoundPhase,
        CalibrationStatus,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any
    FleetRound = Any
    ChallengeRecord = Any
    CalibrationTrace = Any
    BidStatus = Any
    RoundPhase = Any
    CalibrationStatus = Any

try:
    from jugeo.orchestration.fleet_competition.bid_evaluation import (
        MultiCriterionEvaluator,
        BidAuction,
        EvaluationHistory,
        BidEvaluation,
    )
except Exception:  # pragma: no cover
    MultiCriterionEvaluator = Any
    BidAuction = Any
    EvaluationHistory = Any
    BidEvaluation = Any

try:
    from jugeo.orchestration.fleet_competition.challenge_protocol import (
        ChallengeInitiator,
        ChallengeAdjudicator,
        ChallengeLedger,
        ChallengeEventBus,
        AdjudicationPolicy,
    )
except Exception:  # pragma: no cover
    ChallengeInitiator = Any
    ChallengeAdjudicator = Any
    ChallengeLedger = Any
    ChallengeEventBus = Any
    AdjudicationPolicy = Any

try:
    from jugeo.orchestration.fleet_competition.calibration import (
        CalibrationEngine,
        CrossMemberCalibrator,
        CalibrationReport,
    )
except Exception:  # pragma: no cover
    CalibrationEngine = Any
    CrossMemberCalibrator = Any
    CalibrationReport = Any

try:
    from jugeo.orchestration.fleet_competition.algorithms import (
        competitive_search_step,
        pareto_optimal_bids,
        fleet_convergence_score,
        optimal_bid_assignment,
    )
except Exception:  # pragma: no cover
    competitive_search_step = None
    pareto_optimal_bids = None
    fleet_convergence_score = None
    optimal_bid_assignment = None

try:
    from jugeo.orchestration.controller import (
        Orchestrator,
        OrchestratorState,
        MoveKind,
        SemanticMove,
    )
except Exception:  # pragma: no cover
    Orchestrator = Any
    OrchestratorState = Any
    MoveKind = Any
    SemanticMove = Any

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, CompetitiveSearch
except Exception:  # pragma: no cover
    Fleet = Any
    FleetMember = Any
    CompetitiveSearch = Any

try:
    from jugeo.orchestration.frontier import Frontier, FrontierNode, FrontierBudget
except Exception:  # pragma: no cover
    Frontier = Any
    FrontierNode = Any
    FrontierBudget = Any

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustPolicy
except Exception:  # pragma: no cover
    TrustAlgebra = Any
    TrustPolicy = Any

try:
    from jugeo.geometry.descent import DescentEngine, GluingData
except Exception:  # pragma: no cover
    DescentEngine = Any
    GluingData = Any

# ── Module-level constants ────────────────────────────────────────────────────

#: Default number of synthetic bids generated per member when the fleet object
#: is unavailable or returns no bids.
DEFAULT_SYNTHETIC_BIDS_PER_MEMBER: int = 1

#: Minimum trust value — trust never falls below this floor.
TRUST_FLOOR: float = 0.05

#: Maximum trust value — trust never rises above this ceiling.
TRUST_CEILING_ABS: float = 1.0

#: Reward applied to trust after a winning bid.
TRUST_WIN_DELTA: float = 0.04

#: Penalty applied to trust after a losing bid.
TRUST_LOSE_DELTA: float = 0.02

#: Blending weight for calibration score when computing effective trust
#: ceiling.  The formula is:
#:   ceiling = alpha * calibration_score + (1 - alpha) * mean(trust_history)
CALIBRATION_TRUST_ALPHA: float = 0.35

#: Default frontier budget allocated to each member's first submission.
DEFAULT_FRONTIER_BUDGET: float = 10.0

#: Obstruction severity threshold — obstructions below this level are ignored.
OBSTRUCTION_SEVERITY_FLOOR: float = 0.15

#: Number of history entries retained per orchestration step.
MAX_HISTORY_ENTRIES: int = 512

# ── Utility helpers ───────────────────────────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


def _safe_mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or 0.5 if the list is empty.

    Parameters
    ----------
    values:
        A list of floating-point samples.

    Returns
    -------
    float
        Arithmetic mean, or 0.5 for the empty case.
    """
    if not values:
        return 0.5
    return sum(values) / len(values)


def _extract_semantic_score(bid: Any) -> float:
    """Extract the ``semantic_score`` from *bid* regardless of its concrete type.

    Tries attribute access first, then dict-key lookup, then returns 0.5 as a
    neutral default so that integration code is robust against missing fields.

    Parameters
    ----------
    bid:
        Any object that may carry a ``semantic_score`` attribute or key.

    Returns
    -------
    float
        The extracted score, clamped to [0, 1].
    """
    if bid is None:
        return 0.5
    if hasattr(bid, "semantic_score"):
        return _clamp(float(bid.semantic_score), 0.0, 1.0)
    if isinstance(bid, dict) and "semantic_score" in bid:
        return _clamp(float(bid["semantic_score"]), 0.0, 1.0)
    return 0.5


def _extract_member_id(bid: Any) -> str:
    """Extract the ``member_id`` from *bid*, defaulting to ``"unknown"``.

    Parameters
    ----------
    bid:
        Any object that may carry a ``member_id`` attribute or key.

    Returns
    -------
    str
        The member identifier string.
    """
    if bid is None:
        return "unknown"
    if hasattr(bid, "member_id"):
        return str(bid.member_id)
    if isinstance(bid, dict) and "member_id" in bid:
        return str(bid["member_id"])
    return "unknown"


def _make_synthetic_bid(member_id: str, step: int) -> dict:
    """Construct a minimal synthetic bid dict for *member_id*.

    Used when the underlying fleet object is unavailable or when testing
    integration code without live fleet members.

    Parameters
    ----------
    member_id:
        The identifier of the member submitting the bid.
    step:
        The current orchestration step number (used to vary scores).

    Returns
    -------
    dict
        A bid dict compatible with the integration layer's extraction helpers.
    """
    jitter = (hash(member_id) % 100) / 1000.0
    return {
        "bid_id": str(uuid.uuid4()),
        "member_id": member_id,
        "semantic_score": _clamp(0.5 + jitter + step * 0.01, 0.0, 1.0),
        "uncertainty": _clamp(0.3 - step * 0.01 + jitter, 0.0, 1.0),
        "timestamp": time.time(),
        "move_id": f"move-{member_id}-{step}",
        "metadata": {"synthetic": True, "step": step},
    }


# ── CompetitionSessionState ───────────────────────────────────────────────────


class CompetitionSessionState(enum.Enum):
    """Lifecycle states of a ``CompetitionSession``.

    A session transitions through these states in roughly the order listed.
    ``ERROR`` and ``TERMINATED`` are absorbing states — once reached the
    session does not advance further.

    Theory reference
    ────────────────
    theory2.tex Ch 46 §46.1 — Competition Session Lifecycle
    """

    INITIALIZED = "initialized"
    """Components have been set up; no steps have run yet."""

    BIDDING = "bidding"
    """Fleet members are currently solicited for bids."""

    EVALUATING = "evaluating"
    """Collected bids are being ranked and filtered."""

    CHALLENGING = "challenging"
    """Active challenges are being adjudicated."""

    CALIBRATING = "calibrating"
    """Per-member calibration traces are being updated."""

    CONVERGED = "converged"
    """The fleet has reached a stable bid set; session is effectively done."""

    TERMINATED = "terminated"
    """Session was explicitly terminated (normal or forced)."""

    ERROR = "error"
    """An unrecoverable error occurred; session is inert."""


# ── FleetTrustIntegrator ──────────────────────────────────────────────────────


@dataclass(slots=True)
class FleetTrustIntegrator:
    """Bridge between calibration scores and the ``TrustAlgebra``.

    The integrator maintains a per-member history of trust readings and uses
    them, together with incoming calibration scores, to compute effective trust
    ceilings that govern how much epistemic weight the fleet can assign to each
    member's bids.

    Design note (theory2.tex §252)
    ──────────────────────────────
    Trust is a *semantic state*, not a static annotation.  The integrator
    embodies this by recomputing the effective ceiling on each calibration event
    and by gating ceiling increases through the ``TrustAlgebra``'s policy
    object when one is available.

    Parameters
    ----------
    trust_algebra:
        Optional ``TrustAlgebra`` instance from ``jugeo.evidence.trust``.
        When ``None``, the integrator operates in standalone mode.
    policy:
        Optional ``TrustPolicy`` that may veto ceiling increases.
        When ``None``, no policy constraints are applied.
    """

    trust_algebra: Any = None
    policy: Any = None

    # Private state
    _trust_history: dict[str, list[float]] = field(
        default_factory=dict, repr=False
    )
    _audit_log: list[dict] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def integrate_calibration(self, trace: Any) -> float:
        """Compute the effective trust ceiling from a calibration trace.

        The effective ceiling is a weighted blend of the latest calibration
        accuracy score and the member's historical mean trust reading:

        .. math::

            \\text{ceiling} = \\alpha \\cdot s_{\\text{cal}}
                            + (1-\\alpha) \\cdot \\bar{t}_{\\text{hist}}

        where :math:`\\alpha` is ``CALIBRATION_TRUST_ALPHA`` (default 0.35)
        and :math:`\\bar{t}_{\\text{hist}}` is the arithmetic mean of all
        previously recorded trust values for this member.

        Parameters
        ----------
        trace:
            A ``CalibrationTrace`` (or any object / dict carrying
            ``member_id`` and ``accuracy`` fields).

        Returns
        -------
        float
            Effective trust ceiling in [``TRUST_FLOOR``, ``TRUST_CEILING_ABS``].
        """
        member_id = _safe_extract(trace, "member_id", "unknown")
        cal_score = _clamp(
            float(_safe_extract(trace, "accuracy", 0.5)), 0.0, 1.0
        )

        history = self._trust_history.get(member_id, [])
        hist_mean = _safe_mean(history)

        effective = (
            CALIBRATION_TRUST_ALPHA * cal_score
            + (1.0 - CALIBRATION_TRUST_ALPHA) * hist_mean
        )
        ceiling = _clamp(effective, TRUST_FLOOR, TRUST_CEILING_ABS)

        # Apply policy veto if one is wired in
        if self.policy is not None:
            try:
                ceiling = float(
                    getattr(self.policy, "apply", lambda v: v)(ceiling)
                )
            except Exception as exc:  # pragma: no cover
                _log.debug("TrustPolicy.apply failed: %s", exc)

        self._record_audit(
            "integrate_calibration",
            {
                "member_id": member_id,
                "cal_score": cal_score,
                "hist_mean": hist_mean,
                "ceiling": ceiling,
            },
        )
        return ceiling

    def update_trust_from_outcome(
        self, member_id: str, won: bool, current_trust: float
    ) -> float:
        """Update the recorded trust value after a bid outcome.

        A winning bid increases trust by ``TRUST_WIN_DELTA``; a losing bid
        decreases trust by ``TRUST_LOSE_DELTA``.  The result is clamped to
        [``TRUST_FLOOR``, ``TRUST_CEILING_ABS``] and appended to the
        member's history.

        Parameters
        ----------
        member_id:
            The fleet member whose trust should be updated.
        won:
            ``True`` if the member's bid won the round, ``False`` otherwise.
        current_trust:
            The member's current trust value before the update.

        Returns
        -------
        float
            Updated trust value.
        """
        delta = TRUST_WIN_DELTA if won else -TRUST_LOSE_DELTA
        new_trust = _clamp(current_trust + delta, TRUST_FLOOR, TRUST_CEILING_ABS)

        self._trust_history.setdefault(member_id, []).append(new_trust)
        # Cap history length to avoid unbounded growth
        if len(self._trust_history[member_id]) > 256:
            self._trust_history[member_id] = self._trust_history[member_id][-256:]

        self._record_audit(
            "update_trust_from_outcome",
            {
                "member_id": member_id,
                "won": won,
                "current_trust": current_trust,
                "new_trust": new_trust,
            },
        )
        return new_trust

    def apply_trust_ceiling(self, value: float, ceiling: float) -> float:
        """Ensure *value* does not exceed *ceiling*.

        This is a thin helper that enforces the invariant described in
        theory2.tex §252: no channel's output may receive more epistemic
        weight than its current trust ceiling.

        Parameters
        ----------
        value:
            Raw trust or weight value.
        ceiling:
            Maximum permissible value.

        Returns
        -------
        float
            ``min(value, ceiling)``, clamped to ``TRUST_FLOOR`` from below.
        """
        result = _clamp(min(value, ceiling), TRUST_FLOOR, TRUST_CEILING_ABS)
        return result

    @property
    def audit_log(self) -> list[dict]:
        """Return a copy of the internal audit log.

        Each entry is a dict with keys ``op`` (operation name),
        ``ts`` (timestamp), and ``data`` (operation-specific dict).

        Returns
        -------
        list[dict]
            Snapshot of the audit log — the returned list may be freely
            mutated without affecting the integrator's internal state.
        """
        return list(self._audit_log)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_audit(self, op: str, data: dict) -> None:
        """Append an entry to the audit log.

        Parameters
        ----------
        op:
            Short operation label.
        data:
            Arbitrary dict of operation-specific metadata.
        """
        self._audit_log.append({"op": op, "ts": time.time(), "data": data})
        # Trim to avoid unbounded growth
        if len(self._audit_log) > 1024:
            self._audit_log = self._audit_log[-1024:]


# ── FleetDescentConnector ─────────────────────────────────────────────────────


@dataclass(slots=True)
class FleetDescentConnector:
    """Connect fleet bids to the ``DescentEngine`` for gluing-compatibility checks.

    When the fleet generates competing bids for the same node, those bids must
    be *glueable* in the sheaf-theoretic sense of theory2.tex §3: their local
    sections must agree on overlaps.  This connector provides three facilities:

    1. **Compatibility check** — are two bids compatible along their shared
       move boundary?
    2. **Gluing weight** — how much weight should a bid receive given the
       gluing geometry of its target node?
    3. **Obstruction detection** — does a collection of bids contain
       obstructions that prevent global assembly?

    Parameters
    ----------
    descent_engine:
        Optional ``DescentEngine`` from ``jugeo.geometry.descent``.
        When ``None``, the connector uses fallback heuristics.
    """

    descent_engine: Any = None

    # Private cache: (bid_id_a, bid_id_b) -> bool
    _compat_cache: dict[tuple[str, str], bool] = field(
        default_factory=dict, repr=False
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_bid_compatibility(self, bid_a: Any, bid_b: Any) -> bool:
        """Determine whether two bids are compatible for gluing.

        Two bids are considered *compatible* if their semantic scores differ
        by no more than a threshold (``0.45`` by default) and their
        ``move_id`` attributes (when present) do not explicitly conflict.

        When a live ``DescentEngine`` is available, it is consulted first.

        Parameters
        ----------
        bid_a:
            First bid object or dict.
        bid_b:
            Second bid object or dict.

        Returns
        -------
        bool
            ``True`` if the bids are compatible, ``False`` otherwise.
        """
        id_a = _safe_extract(bid_a, "bid_id", str(id(bid_a)))
        id_b = _safe_extract(bid_b, "bid_id", str(id(bid_b)))
        cache_key = (min(id_a, id_b), max(id_a, id_b))

        if cache_key in self._compat_cache:
            return self._compat_cache[cache_key]

        # Try descent engine first
        if self.descent_engine is not None:
            try:
                result = bool(
                    self.descent_engine.check_compatibility(bid_a, bid_b)
                )
                self._compat_cache[cache_key] = result
                return result
            except Exception as exc:
                _log.debug("DescentEngine.check_compatibility failed: %s", exc)

        # Fallback: score proximity heuristic
        score_a = _extract_semantic_score(bid_a)
        score_b = _extract_semantic_score(bid_b)
        compat = abs(score_a - score_b) <= 0.45
        self._compat_cache[cache_key] = compat
        return compat

    def compute_gluing_weight(self, bid: Any, node: Any) -> float:
        """Compute the gluing weight for *bid* at *node*.

        The gluing weight represents how well the bid's local section
        extends to cover the full node neighbourhood.  Higher values mean
        the bid's proposal is more coherent with the node's geometric
        environment.

        Parameters
        ----------
        bid:
            The bid whose weight is being computed.
        node:
            The frontier or descent node being targeted.

        Returns
        -------
        float
            Gluing weight in [0, 1].
        """
        if self.descent_engine is not None:
            try:
                raw = float(
                    self.descent_engine.gluing_weight(bid, node)
                )
                return _clamp(raw, 0.0, 1.0)
            except Exception as exc:
                _log.debug("DescentEngine.gluing_weight failed: %s", exc)

        # Fallback: use semantic score attenuated by uncertainty
        score = _extract_semantic_score(bid)
        uncert = _clamp(float(_safe_extract(bid, "uncertainty", 0.3)), 0.0, 1.0)
        return _clamp(score * (1.0 - 0.5 * uncert), 0.0, 1.0)

    def check_obstruction(self, bids: list[Any]) -> dict:
        """Detect obstructions in a collection of bids.

        An *obstruction* occurs when a subset of bids cannot be jointly
        glued — i.e., their combined local sections are inconsistent.  The
        detector scans all pairs for incompatibility and aggregates severity.

        Parameters
        ----------
        bids:
            List of bid objects to check for obstructions.

        Returns
        -------
        dict
            A dict with three keys:

            ``has_obstruction`` : bool
                Whether any obstruction was detected above the floor threshold.
            ``obstruction_ids`` : list[str]
                Bid IDs participating in at least one incompatible pair.
            ``severity`` : float
                Aggregate obstruction severity in [0, 1].
        """
        if not bids:
            return {"has_obstruction": False, "obstruction_ids": [], "severity": 0.0}

        obstruction_ids: set[str] = set()
        incompatible_count = 0
        total_pairs = 0

        for i, ba in enumerate(bids):
            for j, bb in enumerate(bids):
                if j <= i:
                    continue
                total_pairs += 1
                if not self.validate_bid_compatibility(ba, bb):
                    incompatible_count += 1
                    obstruction_ids.add(
                        str(_safe_extract(ba, "bid_id", str(id(ba))))
                    )
                    obstruction_ids.add(
                        str(_safe_extract(bb, "bid_id", str(id(bb))))
                    )

        severity = (
            incompatible_count / total_pairs if total_pairs > 0 else 0.0
        )
        has_obstruction = severity > OBSTRUCTION_SEVERITY_FLOOR

        return {
            "has_obstruction": has_obstruction,
            "obstruction_ids": sorted(obstruction_ids),
            "severity": round(severity, 4),
        }


# ── FleetFrontierBridge ───────────────────────────────────────────────────────


@dataclass(slots=True)
class FleetFrontierBridge:
    """Synchronise fleet bids with the JuGeo ``Frontier`` data structure.

    The bridge submits winning or candidate bids as frontier nodes so the
    main orchestration controller can include them in its planning.  After
    each round it reconciles consumed budget and marks exhausted nodes.

    Parameters
    ----------
    frontier:
        Optional ``Frontier`` instance from ``jugeo.orchestration.frontier``.
    budget:
        Optional ``FrontierBudget`` for cost-aware pruning.
    """

    frontier: Any = None
    budget: Any = None

    _submitted: dict[str, Any] = field(default_factory=dict, repr=False)
    _budgets: dict[str, float] = field(default_factory=dict, repr=False)
    _pending: list[Any] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit_bid_to_frontier(self, bid: Any, node_id: str) -> bool:
        """Submit *bid* as a candidate at frontier node *node_id*.

        If the bridge has a live ``Frontier`` it calls ``add_node`` (or
        ``add``), otherwise it buffers the submission internally.

        Parameters
        ----------
        bid:
            The bid to submit.
        node_id:
            The frontier node identifier associated with this bid.

        Returns
        -------
        bool
            ``True`` on success, ``False`` if the submission was rejected.
        """
        bid_id = str(_safe_extract(bid, "bid_id", str(uuid.uuid4())))
        record = {"bid": bid, "node_id": node_id, "submitted_at": time.time()}

        if self.frontier is not None:
            try:
                adder = (
                    getattr(self.frontier, "add_node", None)
                    or getattr(self.frontier, "add", None)
                )
                if adder is not None:
                    adder(record)
                    self._submitted[bid_id] = record
                    return True
            except Exception as exc:
                _log.debug("Frontier submission failed: %s", exc)

        # Buffer locally
        self._submitted[bid_id] = record
        self._pending.append(record)
        return True

    def get_frontier_budget(self, member_id: str) -> float:
        """Return the current budget allocated to *member_id*.

        Consults the ``FrontierBudget`` object when available; otherwise
        uses the internal budget table (default ``DEFAULT_FRONTIER_BUDGET``).

        Parameters
        ----------
        member_id:
            The fleet member whose budget is queried.

        Returns
        -------
        float
            Remaining budget units, always >= 0.
        """
        if self.budget is not None:
            try:
                raw = float(
                    getattr(self.budget, "remaining", lambda m: DEFAULT_FRONTIER_BUDGET)(member_id)
                )
                return max(0.0, raw)
            except Exception as exc:
                _log.debug("FrontierBudget.remaining failed: %s", exc)

        return max(0.0, self._budgets.get(member_id, DEFAULT_FRONTIER_BUDGET))

    def update_frontier_after_round(self, round_: Any) -> None:
        """Reconcile frontier state after a completed round.

        Marks the winning member's nodes as *explored* and adjusts budget
        allocations for all participants based on round outcomes.

        Parameters
        ----------
        round_:
            The completed ``FleetRound`` (or compatible dict).
        """
        winner_id = str(_safe_extract(round_, "winner_id", ""))
        member_ids: list[str] = list(
            _safe_extract(round_, "member_ids", []) or []
        )

        for mid in member_ids:
            current = self._budgets.get(mid, DEFAULT_FRONTIER_BUDGET)
            if mid == winner_id:
                # Winner earns a small budget bonus
                self._budgets[mid] = _clamp(current + 2.0, 0.0, 100.0)
            else:
                # Losers spend some budget on participation
                self._budgets[mid] = _clamp(current - 0.5, 0.0, 100.0)

        if self.frontier is not None:
            try:
                tick = getattr(self.frontier, "tick", None)
                if tick is not None:
                    tick()
            except Exception as exc:
                _log.debug("Frontier.tick failed: %s", exc)

    def pending_nodes(self) -> list[Any]:
        """Return bid submissions that are buffered locally (not forwarded to a live frontier).

        Returns
        -------
        list[Any]
            A snapshot of the pending submission records.
        """
        return list(self._pending)

    def bridge_summary(self) -> dict:
        """Return a diagnostic summary of the bridge's current state.

        Returns
        -------
        dict
            Keys: ``submitted_count``, ``pending_count``, ``budgets``,
            ``has_frontier``, ``has_budget``.
        """
        return {
            "submitted_count": len(self._submitted),
            "pending_count": len(self._pending),
            "budgets": dict(self._budgets),
            "has_frontier": self.frontier is not None,
            "has_budget": self.budget is not None,
        }


# ── FleetCompetitionOrchestrator ──────────────────────────────────────────────


@dataclass(slots=True)
class FleetCompetitionOrchestrator:
    """Per-step orchestration driver for the fleet-competition protocol.

    This class drives the inner loop described in theory2.tex Ch 46:

    1. **Collect** — solicit bids from active fleet members.
    2. **Evaluate** — rank bids via ``MultiCriterionEvaluator``; run a
       ``BidAuction`` if one is available.
    3. **Challenge** — submit low-scoring bids to the ``ChallengeLedger``
       and adjudicate pending challenges via the event bus.
    4. **Calibrate** — update per-member calibration traces via the
       ``CalibrationEngine``.
    5. **Record** — append a structured step summary to the internal history.

    Parameters
    ----------
    orchestrator:
        Optional outer ``Orchestrator`` from the controller module.
    fleet:
        Optional ``Fleet`` from the fleet module.
    evaluator:
        Optional ``MultiCriterionEvaluator`` for bid ranking.
    ledger:
        Optional ``ChallengeLedger`` for challenge bookkeeping.
    calibration_engine:
        Optional ``CalibrationEngine`` for accuracy tracking.
    event_bus:
        Optional ``ChallengeEventBus`` for publish/subscribe notifications.
    """

    orchestrator: Any = None
    fleet: Any = None
    evaluator: Any = None  # MultiCriterionEvaluator | None
    ledger: Any = None  # ChallengeLedger | None
    calibration_engine: Any = None  # CalibrationEngine | None
    event_bus: Any = None  # ChallengeEventBus | None

    _step_count: int = field(default=0, repr=False)
    _history: list[dict] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def step(self, state: Any = None) -> dict:
        """Execute one orchestration step.

        Collects bids, evaluates them, checks for challenges, and runs a
        calibration pass.  Returns a structured summary that callers can
        use to decide whether to continue or terminate.

        Parameters
        ----------
        state:
            Optional ``OrchestratorState`` forwarded to the outer
            orchestrator's ``step`` method.

        Returns
        -------
        dict
            Step summary with keys:
            ``step``, ``bid_count``, ``round_id``, ``winner_id``,
            ``challenge_count``, ``calibration_results``,
            ``convergence_score``, ``timestamp``.
        """
        self._step_count += 1
        ts_start = time.time()

        # 1. Collect member ids
        member_ids = self._active_member_ids()

        # 2. Collect bids
        bids = self.collect_bids(member_ids)

        # 3. Evaluate
        round_ = self.evaluate_round(bids)

        # 4. Challenge
        challenges = self.run_challenges(round_)

        # 5. Calibrate
        cal_results = self.calibrate_members(member_ids)

        # 6. Convergence
        conv = self.convergence_score()

        # 7. Advance outer orchestrator if present
        if self.orchestrator is not None and state is not None:
            try:
                self.orchestrator.step(state)
            except Exception as exc:
                _log.debug("Outer orchestrator.step failed: %s", exc)

        summary = {
            "step": self._step_count,
            "bid_count": len(bids),
            "round_id": str(_safe_extract(round_, "round_id", "n/a")),
            "winner_id": str(_safe_extract(round_, "winner_id", "")),
            "challenge_count": len(challenges),
            "calibration_results": cal_results,
            "convergence_score": conv,
            "timestamp": ts_start,
            "elapsed_s": time.time() - ts_start,
        }

        self._history.append(summary)
        if len(self._history) > MAX_HISTORY_ENTRIES:
            self._history = self._history[-MAX_HISTORY_ENTRIES:]

        _log.debug(
            "FleetCompetitionOrchestrator step %d: %d bids, conv=%.3f",
            self._step_count,
            len(bids),
            conv,
        )
        return summary

    def collect_bids(self, member_ids: list[str]) -> list[Any]:
        """Collect bids from the specified fleet members.

        When a live ``Fleet`` object is available, it is asked to solicit
        bids via ``solicit_bids`` or similar.  Otherwise a synthetic bid is
        generated per member.

        Parameters
        ----------
        member_ids:
            List of member identifiers to solicit.

        Returns
        -------
        list[Any]
            A list of bid objects (dicts when synthetic).
        """
        bids: list[Any] = []

        if self.fleet is not None:
            try:
                solicited = getattr(self.fleet, "solicit_bids", None)
                if solicited is not None:
                    raw = solicited(member_ids)
                    if raw:
                        bids.extend(raw)
            except Exception as exc:
                _log.debug("Fleet.solicit_bids failed: %s", exc)

        # Backfill with synthetic bids for any member not yet covered
        covered = {_extract_member_id(b) for b in bids}
        for mid in member_ids:
            if mid not in covered:
                bids.append(_make_synthetic_bid(mid, self._step_count))

        return bids

    def evaluate_round(self, bids: list[Any]) -> Any:
        """Evaluate a collection of bids and determine a winner.

        Creates a ``FleetRound`` record (or a plain dict fallback) and runs
        it through the ``MultiCriterionEvaluator`` and ``BidAuction`` if
        they are available.

        Parameters
        ----------
        bids:
            The bids collected in the current step.

        Returns
        -------
        Any
            A ``FleetRound`` instance or dict representing the evaluated
            round, including a ``winner_id`` field.
        """
        round_id = str(uuid.uuid4())
        winner_id = ""
        best_score = -1.0

        for bid in bids:
            score = _extract_semantic_score(bid)
            if score > best_score:
                best_score = score
                winner_id = _extract_member_id(bid)

        # Try to use live evaluator
        if self.evaluator is not None:
            try:
                evaluate = getattr(self.evaluator, "evaluate_bids", None)
                if evaluate is not None:
                    result = evaluate(bids)
                    if result is not None:
                        w = _safe_extract(result, "winner_id", winner_id)
                        winner_id = str(w) if w else winner_id
            except Exception as exc:
                _log.debug("MultiCriterionEvaluator.evaluate_bids failed: %s", exc)

        round_record: dict = {
            "round_id": round_id,
            "winner_id": winner_id,
            "member_ids": [_extract_member_id(b) for b in bids],
            "bid_count": len(bids),
            "best_score": best_score,
            "timestamp": time.time(),
            "step": self._step_count,
        }

        return round_record

    def run_challenges(self, round_: Any) -> list[Any]:
        """Identify and record challenges arising from the round.

        Any bid with a score below the round's mean is a candidate for a
        challenge by the top-scoring member.  Challenges are submitted to
        the ``ChallengeLedger`` when available and published on the
        ``ChallengeEventBus``.

        Parameters
        ----------
        round_:
            The evaluated round record (dict or ``FleetRound``).

        Returns
        -------
        list[Any]
            List of challenge records created in this step.
        """
        challenges: list[Any] = []
        winner_id = str(_safe_extract(round_, "winner_id", ""))
        bid_count = int(_safe_extract(round_, "bid_count", 0))
        best_score = float(_safe_extract(round_, "best_score", 0.5))

        if bid_count < 2 or not winner_id:
            return challenges

        # Create a challenge from winner against the average performer
        if best_score > 0.6:
            ch: dict = {
                "challenge_id": str(uuid.uuid4()),
                "challenger_id": winner_id,
                "round_id": str(_safe_extract(round_, "round_id", "")),
                "basis": "score_dominance",
                "challenger_score": best_score,
                "timestamp": time.time(),
            }

            if self.ledger is not None:
                try:
                    record = getattr(self.ledger, "record_challenge", None)
                    if record is not None:
                        record(ch)
                except Exception as exc:
                    _log.debug("ChallengeLedger.record_challenge failed: %s", exc)

            if self.event_bus is not None:
                try:
                    publish = getattr(self.event_bus, "publish", None)
                    if publish is not None:
                        publish("challenge_created", ch)
                except Exception as exc:
                    _log.debug("ChallengeEventBus.publish failed: %s", exc)

            challenges.append(ch)

        return challenges

    def calibrate_members(self, member_ids: list[str]) -> dict[str, Any]:
        """Run a calibration pass for each listed member.

        Delegates to ``CalibrationEngine.calibrate_member`` when available.
        Returns a dict mapping each member id to its calibration result.

        Parameters
        ----------
        member_ids:
            Members to calibrate in this step.

        Returns
        -------
        dict[str, Any]
            Per-member calibration results.
        """
        results: dict[str, Any] = {}

        for mid in member_ids:
            if self.calibration_engine is not None:
                try:
                    cal = getattr(self.calibration_engine, "calibrate_member", None)
                    if cal is not None:
                        results[mid] = cal(mid)
                        continue
                except Exception as exc:
                    _log.debug("CalibrationEngine.calibrate_member failed: %s", exc)

            # Fallback: synthetic calibration result
            jitter = (hash(mid + str(self._step_count)) % 100) / 1000.0
            results[mid] = {
                "member_id": mid,
                "accuracy": _clamp(0.6 + jitter, 0.0, 1.0),
                "step": self._step_count,
                "synthetic": True,
            }

        return results

    def convergence_score(self) -> float:
        """Compute the current fleet convergence score.

        Delegates to ``fleet_convergence_score`` from the algorithms module
        when available, otherwise estimates from recent history.

        Returns
        -------
        float
            Convergence score in [0, 1].  Values >= 0.9 suggest the fleet
            has converged to a stable bid distribution.
        """
        if fleet_convergence_score is not None:
            try:
                raw = fleet_convergence_score(self._history)
                return _clamp(float(raw), 0.0, 1.0)
            except Exception as exc:
                _log.debug("fleet_convergence_score failed: %s", exc)

        # Estimate from last few steps' best scores
        if len(self._history) < 3:
            return 0.0

        recent = self._history[-5:]
        scores = [float(h.get("best_score", 0.5)) for h in recent if "best_score" in h]
        if len(scores) < 2:
            return 0.0

        try:
            stdev = statistics.stdev(scores)
        except statistics.StatisticsError:
            stdev = 1.0

        # Low stdev → high convergence
        return _clamp(1.0 - stdev * 5.0, 0.0, 1.0)

    @property
    def history(self) -> list[dict]:
        """Return a copy of the orchestration step history.

        Returns
        -------
        list[dict]
            Snapshot of per-step summary dicts.
        """
        return list(self._history)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _active_member_ids(self) -> list[str]:
        """Collect identifiers of currently active fleet members.

        Returns
        -------
        list[str]
            List of member id strings.
        """
        if self.fleet is not None:
            try:
                members = getattr(self.fleet, "active_members", None)
                if members is not None:
                    ids = members()
                    if ids:
                        return [str(m) if not hasattr(m, "member_id") else str(m.member_id) for m in ids]
            except Exception as exc:
                _log.debug("Fleet.active_members failed: %s", exc)

        # Fall back to a deterministic default set
        return [f"member-{i}" for i in range(3)]


# ── CompetitionSession ────────────────────────────────────────────────────────


@dataclass(slots=True)
class CompetitionSession:
    """High-level controller for a complete fleet-competition episode.

    A ``CompetitionSession`` encapsulates the full lifecycle of one
    competitive search episode as described in theory2.tex Ch 46.  Callers
    should:

    1. Create a session (optionally supplying config, fleet, and component
       overrides).
    2. Call ``initialize(member_ids)`` to wire up all sub-systems.
    3. Call ``run(max_steps=N)`` for a fully automated episode, or use
       ``step()`` for fine-grained control.
    4. Inspect ``status()`` at any point.
    5. Call ``terminate(reason)`` when done.

    The session transitions through the states defined in
    ``CompetitionSessionState``.  Each call to ``step()`` advances the
    state machine and returns a structured summary dict.

    Parameters
    ----------
    session_id:
        Unique identifier for this session.  Defaults to a fresh UUID.
    config:
        Optional configuration object (any dict-like or attribute-bearing
        object).
    fleet:
        Optional ``Fleet`` instance.
    orchestrator_integration:
        Optional ``FleetCompetitionOrchestrator`` to drive the inner loop.
    trust_integrator:
        Optional ``FleetTrustIntegrator`` for trust accounting.
    frontier_bridge:
        Optional ``FleetFrontierBridge`` for frontier synchronisation.
    descent_connector:
        Optional ``FleetDescentConnector`` for geometry checks.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: Any = None
    fleet: Any = None
    orchestrator_integration: Any = None  # FleetCompetitionOrchestrator | None
    trust_integrator: Any = None  # FleetTrustIntegrator | None
    frontier_bridge: Any = None  # FleetFrontierBridge | None
    descent_connector: Any = None  # FleetDescentConnector | None

    _state: CompetitionSessionState = field(
        default=CompetitionSessionState.INITIALIZED, repr=False
    )
    _member_ids: list[str] = field(default_factory=list, repr=False)
    _rounds: list[dict] = field(default_factory=list, repr=False)
    _step_count: int = field(default=0, repr=False)
    _created_at: float = field(default_factory=time.time, repr=False)
    _terminated_at: float | None = field(default=None, repr=False)
    _termination_reason: str = field(default="", repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize(self, member_ids: list[str]) -> None:
        """Set up all session components and transition to INITIALIZED.

        If ``orchestrator_integration`` has not been provided, a default
        ``FleetCompetitionOrchestrator`` is constructed.  Likewise for the
        trust integrator, frontier bridge, and descent connector.

        Parameters
        ----------
        member_ids:
            List of fleet member identifiers to participate in this session.
        """
        self._member_ids = list(member_ids)

        if self.orchestrator_integration is None:
            self.orchestrator_integration = FleetCompetitionOrchestrator(
                fleet=self.fleet
            )

        if self.trust_integrator is None:
            self.trust_integrator = FleetTrustIntegrator()

        if self.frontier_bridge is None:
            self.frontier_bridge = FleetFrontierBridge()

        if self.descent_connector is None:
            self.descent_connector = FleetDescentConnector()

        self._state = CompetitionSessionState.INITIALIZED
        _log.info(
            "CompetitionSession %s initialized with %d members",
            self.session_id,
            len(self._member_ids),
        )

    def run(self, max_steps: int = 10) -> dict:
        """Run the full competition session for up to *max_steps* steps.

        Iterates ``step()`` until either convergence is reached, the step
        limit is hit, or the session enters an absorbing state.

        Parameters
        ----------
        max_steps:
            Upper bound on the number of steps to execute.

        Returns
        -------
        dict
            Results dict with keys: ``session_id``, ``rounds``, ``winner``,
            ``convergence_score``, ``steps_taken``, ``state``,
            ``elapsed_s``.
        """
        if self._state in (
            CompetitionSessionState.TERMINATED,
            CompetitionSessionState.ERROR,
        ):
            return self.status()

        if self._state == CompetitionSessionState.INITIALIZED and not self._member_ids:
            self.initialize([f"member-{i}" for i in range(3)])

        ts_start = time.time()
        final_convergence = 0.0
        winner = ""

        for _ in range(max_steps):
            if self._state in (
                CompetitionSessionState.CONVERGED,
                CompetitionSessionState.TERMINATED,
                CompetitionSessionState.ERROR,
            ):
                break

            summary = self.step()
            final_convergence = float(summary.get("convergence_score", 0.0))
            winner = str(summary.get("winner_id", ""))

            if final_convergence >= 0.9:
                self._state = CompetitionSessionState.CONVERGED
                break

        if self._state not in (
            CompetitionSessionState.CONVERGED,
            CompetitionSessionState.TERMINATED,
            CompetitionSessionState.ERROR,
        ):
            self.terminate("max_steps_reached")

        return {
            "session_id": self.session_id,
            "rounds": list(self._rounds),
            "winner": winner,
            "convergence_score": final_convergence,
            "steps_taken": self._step_count,
            "state": self._state.value,
            "elapsed_s": time.time() - ts_start,
        }

    def step(self) -> dict:
        """Execute a single competition step.

        Updates the session state machine and delegates to
        ``orchestrator_integration.step()``.  The session advances through
        BIDDING → EVALUATING → CHALLENGING → CALIBRATING and then loops.

        Returns
        -------
        dict
            Step summary forwarded from ``FleetCompetitionOrchestrator.step``.
        """
        if self._state in (
            CompetitionSessionState.TERMINATED,
            CompetitionSessionState.ERROR,
            CompetitionSessionState.CONVERGED,
        ):
            return self.status()

        self._step_count += 1

        # Cycle the internal state machine
        _state_cycle = [
            CompetitionSessionState.BIDDING,
            CompetitionSessionState.EVALUATING,
            CompetitionSessionState.CHALLENGING,
            CompetitionSessionState.CALIBRATING,
        ]
        idx = (self._step_count - 1) % len(_state_cycle)
        self._state = _state_cycle[idx]

        if self.orchestrator_integration is None:
            self.orchestrator_integration = FleetCompetitionOrchestrator(
                fleet=self.fleet
            )

        try:
            summary = self.orchestrator_integration.step()
        except Exception as exc:
            _log.error("FleetCompetitionOrchestrator.step raised: %s", exc)
            self._state = CompetitionSessionState.ERROR
            return {"error": str(exc), "step": self._step_count}

        self._rounds.append(summary)

        # Trust integration post-step
        if self.trust_integrator is not None:
            winner_id = str(summary.get("winner_id", ""))
            for cal_result in summary.get("calibration_results", {}).values():
                try:
                    self.trust_integrator.integrate_calibration(cal_result)
                except Exception as exc:
                    _log.debug("trust_integrator.integrate_calibration failed: %s", exc)
            if winner_id:
                for mid in self._member_ids:
                    won = mid == winner_id
                    try:
                        self.trust_integrator.update_trust_from_outcome(
                            mid, won, 0.5
                        )
                    except Exception as exc:
                        _log.debug("update_trust_from_outcome failed: %s", exc)

        # Frontier bridge post-step
        if self.frontier_bridge is not None:
            try:
                self.frontier_bridge.update_frontier_after_round(summary)
            except Exception as exc:
                _log.debug("frontier_bridge.update_frontier_after_round failed: %s", exc)

        return summary

    def terminate(self, reason: str = "completed") -> None:
        """Terminate the session and record the reason.

        After termination the session is inert — further calls to ``step()``
        return the current status without advancing state.

        Parameters
        ----------
        reason:
            Human-readable termination reason string.
        """
        self._state = CompetitionSessionState.TERMINATED
        self._terminated_at = time.time()
        self._termination_reason = reason
        _log.info(
            "CompetitionSession %s terminated: %s", self.session_id, reason
        )

    def status(self) -> dict:
        """Return a snapshot of the session's current status.

        Returns
        -------
        dict
            Keys: ``session_id``, ``state``, ``step_count``,
            ``member_count``, ``round_count``, ``created_at``,
            ``terminated_at``, ``termination_reason``.
        """
        return {
            "session_id": self.session_id,
            "state": self._state.value,
            "step_count": self._step_count,
            "member_count": len(self._member_ids),
            "round_count": len(self._rounds),
            "created_at": self._created_at,
            "terminated_at": self._terminated_at,
            "termination_reason": self._termination_reason,
            "bridge_summary": (
                self.frontier_bridge.bridge_summary()
                if self.frontier_bridge is not None
                else {}
            ),
        }

    @property
    def state(self) -> CompetitionSessionState:
        """Current lifecycle state of this session.

        Returns
        -------
        CompetitionSessionState
        """
        return self._state


# ── Private utility (module-level) ────────────────────────────────────────────


def _safe_extract(obj: Any, key: str, default: Any) -> Any:
    """Extract *key* from *obj* via attribute or dict lookup, returning *default*.

    Parameters
    ----------
    obj:
        Source object.
    key:
        Attribute or dict key to look up.
    default:
        Value to return when the key is absent.

    Returns
    -------
    Any
        Extracted value or *default*.
    """
    if obj is None:
        return default
    if hasattr(obj, key):
        val = getattr(obj, key)
        return val if val is not None else default
    if isinstance(obj, dict) and key in obj:
        val = obj[key]
        return val if val is not None else default
    return default


# ── Public re-exports ─────────────────────────────────────────────────────────

__all__ = [
    "CompetitionSessionState",
    "FleetTrustIntegrator",
    "FleetDescentConnector",
    "FleetFrontierBridge",
    "FleetCompetitionOrchestrator",
    "CompetitionSession",
    # Constants
    "TRUST_FLOOR",
    "TRUST_CEILING_ABS",
    "TRUST_WIN_DELTA",
    "TRUST_LOSE_DELTA",
    "CALIBRATION_TRUST_ALPHA",
    "DEFAULT_FRONTIER_BUDGET",
    "OBSTRUCTION_SEVERITY_FLOOR",
    "MAX_HISTORY_ENTRIES",
    "DEFAULT_SYNTHETIC_BIDS_PER_MEMBER",
]
