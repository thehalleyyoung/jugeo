"""Theorem statements and verifiable invariants for fleet-competition (theory2.tex Ch 46).

This module formalises the key theorems and lemmas from theory2.tex Chapter 46
(*Fleet Competition and Convergent Bid Dynamics*) as executable Python objects.
Each theorem is a class with three public methods:

* ``check(state)``          — returns ``bool``; ``True`` iff the invariant holds.
* ``counterexample(state)`` — returns the first violating witness as a ``dict``,
                              or ``None`` if none is found.
* ``verify(state)``         — returns a ``TheoremResult`` wrapping both.

The :class:`InvariantChecker` bundles all theorems and lemmas for bulk
verification.  The :class:`TheoremRegistry` provides a global name → theorem
map and can build a ready-to-use ``InvariantChecker`` at any time.

Design notes
────────────
*   All theorem classes are *stateless* — they read from a ``CompetitionState``
    snapshot and never mutate it.
*   The :class:`CompetitionState` dataclass is the *only* mutable piece; callers
    populate it by calling ``add_bid``, ``add_round``, etc.
*   Guard imports follow the same pattern as the rest of the fleet-competition
    package so that this module can be imported in isolation.
*   Proof sketches in docstrings reference the theorem statements in
    theory2.tex Ch 46 and are intended to be machine-readable in future
    formal-verification passes.

Theorem catalogue
─────────────────
``Theorem46_1_MonotonicBidRefinement``
    Bid refinement is monotone: newer refinements have >= semantic score.

``Theorem46_2_ChallengeConservativity``
    Challenges are conservative: no trust ceiling inflation occurs.

``Theorem46_3_CalibrationConvergence``
    Calibration converges: accuracy improves over long windows.

``Theorem46_4_ParetoStability``
    The Pareto-optimal bid set is stable under dominated additions.

``Lemma46_A_BidDeltaAntiSymmetry``
    BidDelta is antisymmetric: delta(A, B).value_delta = -delta(B, A).value_delta.

References
──────────
*   theory2.tex Ch 46  — Fleet Competition and Convergent Bid Dynamics
*   theory2.tex §252   — Evidence Algebra, Channel Jurisdiction, Trust
*   theory2.tex §3     — Descent and Gluing
*   theory2.tex §46.1  — Monotone Refinement Order
*   theory2.tex §46.2  — Conservative Challenge Protocol
*   theory2.tex §46.3  — Calibration Convergence Criterion
*   theory2.tex §46.4  — Pareto Stability Theorem
*   theory2.tex §46.A  — BidDelta Antisymmetry Lemma
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Logger ───────────────────────────────────────────────────────────────────

_log = logging.getLogger(__name__)

# ── Internal imports (guarded) ────────────────────────────────────────────────

try:
    from jugeo.orchestration.fleet_competition.models import (
        CompetitiveBid,
        BidDelta,
        FleetRound,
        ChallengeRecord,
        CalibrationTrace,
        BidStatus,
        RoundPhase,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any
    BidDelta = Any
    FleetRound = Any
    ChallengeRecord = Any
    CalibrationTrace = Any
    BidStatus = Any
    RoundPhase = Any

try:
    from jugeo.orchestration.fleet_competition.algorithms import (
        pareto_optimal_bids,
        bid_delta_lattice,
        fleet_convergence_score,
    )
except Exception:  # pragma: no cover
    pareto_optimal_bids = None
    bid_delta_lattice = None
    fleet_convergence_score = None

try:
    from jugeo.orchestration.fleet_competition.bid_evaluation import (
        BidEvaluation,
        ParetoFilter,
    )
except Exception:  # pragma: no cover
    BidEvaluation = Any
    ParetoFilter = None

# ── Module-level constants ────────────────────────────────────────────────────

#: Tolerance for floating-point comparisons in theorem checks.
FLOAT_TOL: float = 1e-9

#: Maximum regression allowed in Theorem 46-3 (5 % = 0.05).
CALIBRATION_REGRESSION_TOLERANCE: float = 0.05

#: Window size used for calibration convergence check (Theorem 46-3).
CALIBRATION_WINDOW: int = 10

#: Minimum number of calibration samples required for Theorem 46-3.
CALIBRATION_MIN_SAMPLES: int = 20

#: Score used for the injected dominated bid in Theorem 46-4.
DOMINATED_BID_SCORE: float = 0.0

#: Uncertainty used for the injected dominated bid in Theorem 46-4.
DOMINATED_BID_UNCERTAINTY: float = 1.0

# ── Utility helpers ───────────────────────────────────────────────────────────


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Extract *key* from *obj* via attribute or dict access.

    Parameters
    ----------
    obj:
        Any object or dict.
    key:
        The attribute or key to retrieve.
    default:
        Fallback when the key is absent.

    Returns
    -------
    Any
        Retrieved value or *default*.
    """
    if obj is None:
        return default
    if hasattr(obj, key):
        v = getattr(obj, key)
        return v if v is not None else default
    if isinstance(obj, dict):
        v = obj.get(key)
        return v if v is not None else default
    return default


def _semantic_score(bid: Any) -> float:
    """Return the semantic score of *bid*, defaulting to 0.5.

    Parameters
    ----------
    bid:
        Bid object or dict.

    Returns
    -------
    float
        Semantic score clamped to [0, 1].
    """
    raw = _get(bid, "semantic_score", 0.5)
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5


def _timestamp(bid: Any) -> float:
    """Return the timestamp of *bid*, defaulting to 0.0.

    Parameters
    ----------
    bid:
        Bid object or dict.

    Returns
    -------
    float
        Timestamp as a POSIX float.
    """
    raw = _get(bid, "timestamp", 0.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _move_id(bid: Any) -> str:
    """Return the move_id of *bid*, defaulting to empty string.

    Parameters
    ----------
    bid:
        Bid object or dict.

    Returns
    -------
    str
        Move identifier string.
    """
    return str(_get(bid, "move_id", ""))


def _bid_id(bid: Any) -> str:
    """Return the bid_id of *bid*, defaulting to a stable hash-based id.

    Parameters
    ----------
    bid:
        Bid object or dict.

    Returns
    -------
    str
        Bid identifier string.
    """
    raw = _get(bid, "bid_id", None)
    if raw is not None:
        return str(raw)
    return f"bid-{id(bid)}"


def _pareto_ids(bids: list[Any]) -> set[str]:
    """Compute the set of bid IDs on the Pareto front using the algorithms module.

    Falls back to a simple O(n²) dominance scan when the algorithms module is
    unavailable.

    Parameters
    ----------
    bids:
        List of bids to analyse.

    Returns
    -------
    set[str]
        Set of bid IDs that are Pareto-optimal.
    """
    if pareto_optimal_bids is not None:
        try:
            front = pareto_optimal_bids(bids)
            if front is not None:
                return {_bid_id(b) for b in front}
        except Exception as exc:
            _log.debug("pareto_optimal_bids failed: %s", exc)

    # Fallback: maximise semantic_score, minimise uncertainty
    def dominates(a: Any, b: Any) -> bool:
        """Return True if *a* weakly dominates *b* on all objectives."""
        sa, ua = _semantic_score(a), float(_get(a, "uncertainty", 0.5))
        sb, ub = _semantic_score(b), float(_get(b, "uncertainty", 0.5))
        # a dominates b iff a >= b on score AND a <= b on uncertainty,
        # with strict inequality on at least one.
        return (sa >= sb - FLOAT_TOL and ua <= ub + FLOAT_TOL) and (
            sa > sb + FLOAT_TOL or ua < ub - FLOAT_TOL
        )

    n = len(bids)
    dominated: set[int] = set()
    for i in range(n):
        for j in range(n):
            if i != j and dominates(bids[j], bids[i]):
                dominated.add(i)

    return {_bid_id(bids[i]) for i in range(n) if i not in dominated}


def _compute_bid_delta(bid_a: Any, bid_b: Any) -> dict:
    """Compute the delta between two bids.

    If the ``bid_delta_lattice`` function is available it is used; otherwise
    a simple arithmetic delta is computed.

    Parameters
    ----------
    bid_a:
        The "from" bid (reference).
    bid_b:
        The "to" bid.

    Returns
    -------
    dict
        A dict with at least ``value_delta`` (float).
    """
    if bid_delta_lattice is not None:
        try:
            result = bid_delta_lattice(bid_a, bid_b)
            if result is not None:
                if isinstance(result, dict):
                    return result
                vd = _get(result, "value_delta", None)
                if vd is not None:
                    return {"value_delta": float(vd)}
        except Exception as exc:
            _log.debug("bid_delta_lattice failed: %s", exc)

    # Fallback: semantic score difference
    value_delta = _semantic_score(bid_b) - _semantic_score(bid_a)
    return {"value_delta": value_delta}


# ── TheoremResult ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TheoremResult:
    """Immutable result record for a single theorem verification.

    Instances are produced by ``Theorem*.verify(state)`` and collected by
    :class:`InvariantChecker`.  They are deliberately *frozen* so that
    result lists cannot be silently mutated after collection.

    Parameters
    ----------
    theorem_name:
        Short canonical name of the theorem (e.g. ``"Theorem46_1"``).
    holds:
        ``True`` iff the invariant was verified to hold on the given state.
    counterexample:
        First violating witness, or ``None`` when the theorem holds.
    proof_sketch:
        Brief human-readable justification for the result.
    checked_at:
        POSIX timestamp at which the check was performed.
    details:
        Arbitrary extra metadata produced during the check.
    """

    theorem_name: str
    holds: bool
    counterexample: dict | None = None
    proof_sketch: str = ""
    checked_at: float = field(default_factory=time.time)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise this result to a plain dict.

        Returns
        -------
        dict
            All fields as a JSON-serialisable dict.
        """
        return {
            "theorem_name": self.theorem_name,
            "holds": self.holds,
            "counterexample": self.counterexample,
            "proof_sketch": self.proof_sketch,
            "checked_at": self.checked_at,
            "details": self.details,
        }

    def summary(self) -> str:
        """Return a short human-readable summary of this result.

        Returns
        -------
        str
            One-line summary, e.g. ``"[PASS] Theorem46_1 — Monotone bid refinement"``.
        """
        status = "PASS" if self.holds else "FAIL"
        sketch = self.proof_sketch[:60] + "…" if len(self.proof_sketch) > 60 else self.proof_sketch
        cx = ""
        if not self.holds and self.counterexample:
            cx = f" | cex={list(self.counterexample.keys())}"
        return f"[{status}] {self.theorem_name}{cx} — {sketch}"


# ── CompetitionState ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class CompetitionState:
    """Mutable state container passed to theorem checkers.

    This is a lightweight accumulator that holds the data the theorem
    checkers need to run.  Callers build up the state by calling
    ``add_bid``, ``add_round``, ``add_challenge``, and ``add_trace``.

    Parameters
    ----------
    bids:
        All bids seen during the competition episode.
    rounds:
        All evaluated rounds.
    challenges:
        All challenge records.
    calibration_traces:
        Per-member calibration traces.
    fleet_history:
        Optional opaque fleet history object.

    Design note
    ───────────
    ``fleet_history`` is intentionally typed as ``Any`` so that callers can
    pass a ``FleetHistory`` instance from ``jugeo.orchestration.fleet``
    without forcing a concrete import at this level.
    """

    bids: list[Any] = field(default_factory=list)
    rounds: list[Any] = field(default_factory=list)
    challenges: list[Any] = field(default_factory=list)
    calibration_traces: list[Any] = field(default_factory=list)
    fleet_history: Any = None

    def add_bid(self, bid: Any) -> None:
        """Append *bid* to the bid collection.

        Parameters
        ----------
        bid:
            Bid object or dict to record.
        """
        self.bids.append(bid)

    def add_round(self, round_: Any) -> None:
        """Append *round_* to the round collection.

        Parameters
        ----------
        round_:
            Round object or dict to record.
        """
        self.rounds.append(round_)

    def add_challenge(self, ch: Any) -> None:
        """Append *ch* to the challenge collection.

        Parameters
        ----------
        ch:
            Challenge record object or dict to record.
        """
        self.challenges.append(ch)

    def add_trace(self, trace: Any) -> None:
        """Append *trace* to the calibration traces.

        Parameters
        ----------
        trace:
            Calibration trace object or dict to record.
        """
        self.calibration_traces.append(trace)

    def snapshot(self) -> dict:
        """Return a lightweight summary snapshot of the current state.

        Returns
        -------
        dict
            Keys: ``bid_count``, ``round_count``, ``challenge_count``,
            ``trace_count``, ``has_fleet_history``.
        """
        return {
            "bid_count": len(self.bids),
            "round_count": len(self.rounds),
            "challenge_count": len(self.challenges),
            "trace_count": len(self.calibration_traces),
            "has_fleet_history": self.fleet_history is not None,
        }


# ── Theorem46_1_MonotonicBidRefinement ────────────────────────────────────────


class Theorem46_1_MonotonicBidRefinement:
    """Verify Theorem 46.1: Bid refinement is monotone.

    **Statement (theory2.tex §46.1)**
    Bid refinement is monotone: if bid B refines bid A (i.e. B is newer than
    A for the same move), then B's semantic score is >= A's semantic score.

    Proof sketch
    ────────────
    The refinement order is induced by timestamp: B refines A iff
    ``B.move_id == A.move_id`` and ``B.timestamp > A.timestamp``.  The
    fleet-competition protocol enforces that a refinement replaces a bid only
    when the new semantic score is at least as large; otherwise the challenger
    must rebid.  Hence by induction the sequence of scores for any fixed
    ``move_id`` is non-decreasing.

    The implementation checks all pairs sharing the same ``move_id`` and
    verifies that the later bid's score is >= the earlier bid's score.
    Pairs with equal timestamps are considered unordered and skipped.
    """

    statement: str = (
        "Bid refinement is monotone: if bid B refines bid A, then B's "
        "semantic score is >= A's semantic score."
    )

    def check(self, state: CompetitionState) -> bool:
        """Return ``True`` iff monotone refinement holds for all bid pairs in *state*.

        Parameters
        ----------
        state:
            Competition state containing the bids to inspect.

        Returns
        -------
        bool
            ``True`` if no violation is found.
        """
        return self.counterexample(state) is None

    def counterexample(self, state: CompetitionState) -> dict | None:
        """Return the first pair violating monotone refinement, or ``None``.

        A violation occurs when two bids share a ``move_id`` and the later
        one (by timestamp) has a strictly *lower* semantic score.

        Parameters
        ----------
        state:
            Competition state whose bids are checked.

        Returns
        -------
        dict | None
            Violation dict with keys ``bid_a_id``, ``bid_b_id``,
            ``move_id``, ``score_a``, ``score_b``, ``ts_a``, ``ts_b``;
            or ``None`` if the invariant holds.
        """
        # Group bids by move_id
        by_move: dict[str, list[Any]] = {}
        for bid in state.bids:
            mid = _move_id(bid)
            if not mid:
                continue
            by_move.setdefault(mid, []).append(bid)

        for move_id, group in by_move.items():
            # Sort by timestamp
            try:
                ordered = sorted(group, key=_timestamp)
            except Exception:
                continue

            for i in range(len(ordered) - 1):
                ba, bb = ordered[i], ordered[i + 1]
                ts_a, ts_b = _timestamp(ba), _timestamp(bb)
                if abs(ts_a - ts_b) < FLOAT_TOL:
                    # Concurrent bids — order is undefined; skip
                    continue
                sa, sb = _semantic_score(ba), _semantic_score(bb)
                if sb < sa - FLOAT_TOL:
                    return {
                        "bid_a_id": _bid_id(ba),
                        "bid_b_id": _bid_id(bb),
                        "move_id": move_id,
                        "score_a": sa,
                        "score_b": sb,
                        "ts_a": ts_a,
                        "ts_b": ts_b,
                        "violation": f"score dropped {sa:.4f} → {sb:.4f}",
                    }
        return None

    def verify(self, state: CompetitionState) -> TheoremResult:
        """Run a full verification pass and return a ``TheoremResult``.

        Parameters
        ----------
        state:
            Competition state to check.

        Returns
        -------
        TheoremResult
            Result with ``holds``, ``counterexample``, and proof sketch.
        """
        ts = time.time()
        cx = self.counterexample(state)
        holds = cx is None

        # Count the bid pairs checked
        total_pairs = sum(
            max(0, len(g) - 1)
            for g in _group_by_move_id(state.bids).values()
        )

        return TheoremResult(
            theorem_name="Theorem46_1_MonotonicBidRefinement",
            holds=holds,
            counterexample=cx,
            proof_sketch=(
                "Refinements are ordered by timestamp; the protocol requires "
                "the newer bid's score to be >= the older bid's score for the "
                "same move.  Verified by checking all same-move-id pairs."
            ),
            checked_at=ts,
            details={
                "bid_count": len(state.bids),
                "pairs_checked": total_pairs,
                "grouped_moves": len(_group_by_move_id(state.bids)),
            },
        )


# ── Theorem46_2_ChallengeConservativity ──────────────────────────────────────


class Theorem46_2_ChallengeConservativity:
    """Verify Theorem 46.2: Challenges are conservative.

    **Statement (theory2.tex §46.2)**
    Challenges are conservative: resolving a challenge does not increase the
    challenger's trust ceiling beyond the pre-challenge value.

    Proof sketch
    ────────────
    The challenge-resolution protocol (theory2.tex §46.2) requires that any
    trust adjustment made during adjudication is bounded above by the
    challenger's trust ceiling at the point the challenge was lodged.  The
    adjudicator must record ``trust_before`` and ``trust_after`` in the
    challenge evidence dict when trust is modified.  This theorem verifies
    the bound for all resolved challenges carrying these keys.

    Challenges that do not carry ``trust_before`` / ``trust_after`` evidence
    are skipped (they are assumed to have made no trust modifications).
    """

    statement: str = (
        "Challenges are conservative: resolving a challenge does not increase "
        "the challenger's trust ceiling beyond the pre-challenge value."
    )

    def check(self, state: CompetitionState) -> bool:
        """Return ``True`` iff trust conservativity holds for all challenges.

        Parameters
        ----------
        state:
            Competition state containing challenge records.

        Returns
        -------
        bool
        """
        return self.counterexample(state) is None

    def counterexample(self, state: CompetitionState) -> dict | None:
        """Return the first challenge violating trust conservativity, or ``None``.

        Parameters
        ----------
        state:
            Competition state whose challenges are inspected.

        Returns
        -------
        dict | None
            Violation dict with keys ``challenge_id``, ``trust_before``,
            ``trust_after``, ``excess``; or ``None``.
        """
        for ch in state.challenges:
            evidence = _get(ch, "evidence", {}) or {}
            if not isinstance(evidence, dict):
                continue
            tb = evidence.get("trust_before")
            ta = evidence.get("trust_after")
            if tb is None or ta is None:
                continue
            try:
                tb_f, ta_f = float(tb), float(ta)
            except (TypeError, ValueError):
                continue

            if ta_f > tb_f + FLOAT_TOL:
                return {
                    "challenge_id": str(_get(ch, "challenge_id", "unknown")),
                    "trust_before": tb_f,
                    "trust_after": ta_f,
                    "excess": ta_f - tb_f,
                    "violation": (
                        f"trust ceiling increased {tb_f:.4f} → {ta_f:.4f} "
                        f"(+{ta_f - tb_f:.4f})"
                    ),
                }
        return None

    def verify(self, state: CompetitionState) -> TheoremResult:
        """Run a full verification and return a ``TheoremResult``.

        Parameters
        ----------
        state:
            Competition state to check.

        Returns
        -------
        TheoremResult
        """
        ts = time.time()
        cx = self.counterexample(state)
        holds = cx is None

        evidenced = sum(
            1
            for ch in state.challenges
            if isinstance(_get(ch, "evidence", {}), dict)
            and "trust_before" in (_get(ch, "evidence", {}) or {})
        )

        return TheoremResult(
            theorem_name="Theorem46_2_ChallengeConservativity",
            holds=holds,
            counterexample=cx,
            proof_sketch=(
                "The adjudicator records trust_before and trust_after in "
                "challenge evidence.  For all such records, trust_after must "
                "not exceed trust_before.  Challenges without trust evidence "
                "are vacuously conservative."
            ),
            checked_at=ts,
            details={
                "challenge_count": len(state.challenges),
                "evidenced_count": evidenced,
            },
        )


# ── Theorem46_3_CalibrationConvergence ───────────────────────────────────────


class Theorem46_3_CalibrationConvergence:
    """Verify Theorem 46.3: Calibration converges.

    **Statement (theory2.tex §46.3)**
    Calibration converges: the moving average of accuracy over any 10-sample
    window is non-decreasing in expectation for a well-behaved member.

    Proof sketch
    ────────────
    For a well-behaved calibration schedule the accuracy samples are drawn
    from a distribution whose mean is non-decreasing over time.  By the
    law of large numbers the empirical window means converge to this
    non-decreasing sequence.  The theorem verifies the weaker empirical
    claim: the mean of the last ``CALIBRATION_WINDOW`` samples is >= the
    mean of the first ``CALIBRATION_WINDOW`` samples minus a 5 % tolerance,
    for any trace with at least ``CALIBRATION_MIN_SAMPLES`` entries.
    """

    statement: str = (
        "Calibration converges: the moving average of accuracy over any "
        "10-sample window is non-decreasing in expectation for a well-behaved "
        "member."
    )

    def check(self, state: CompetitionState) -> bool:
        """Return ``True`` iff calibration convergence holds for all long traces.

        Parameters
        ----------
        state:
            Competition state containing calibration traces.

        Returns
        -------
        bool
        """
        return self.counterexample(state) is None

    def counterexample(self, state: CompetitionState) -> dict | None:
        """Return the first trace violating calibration convergence, or ``None``.

        Only traces with >= ``CALIBRATION_MIN_SAMPLES`` entries are checked.
        Regression up to ``CALIBRATION_REGRESSION_TOLERANCE`` is tolerated.

        Parameters
        ----------
        state:
            Competition state whose calibration traces are inspected.

        Returns
        -------
        dict | None
            Violation dict with keys ``member_id``, ``first_window_mean``,
            ``last_window_mean``, ``regression``; or ``None``.
        """
        for trace in state.calibration_traces:
            member_id = str(_get(trace, "member_id", "unknown"))
            samples = _get(trace, "accuracy_samples", None) or _get(trace, "samples", None)
            if samples is None:
                # Try to treat the trace itself as a list
                if isinstance(trace, (list, tuple)):
                    samples = list(trace)
                else:
                    continue

            try:
                floats = [float(s) for s in samples]
            except (TypeError, ValueError):
                continue

            if len(floats) < CALIBRATION_MIN_SAMPLES:
                continue

            first_mean = sum(floats[:CALIBRATION_WINDOW]) / CALIBRATION_WINDOW
            last_mean = sum(floats[-CALIBRATION_WINDOW:]) / CALIBRATION_WINDOW

            if last_mean < first_mean - CALIBRATION_REGRESSION_TOLERANCE:
                return {
                    "member_id": member_id,
                    "first_window_mean": first_mean,
                    "last_window_mean": last_mean,
                    "regression": first_mean - last_mean,
                    "sample_count": len(floats),
                    "violation": (
                        f"accuracy regressed {first_mean:.4f} → {last_mean:.4f} "
                        f"(regression={first_mean - last_mean:.4f} > "
                        f"tolerance={CALIBRATION_REGRESSION_TOLERANCE})"
                    ),
                }
        return None

    def verify(self, state: CompetitionState) -> TheoremResult:
        """Run a full verification and return a ``TheoremResult``.

        Parameters
        ----------
        state:
            Competition state to check.

        Returns
        -------
        TheoremResult
        """
        ts = time.time()
        cx = self.counterexample(state)
        holds = cx is None

        long_traces = sum(
            1
            for tr in state.calibration_traces
            if len(_get(tr, "accuracy_samples", []) or []) >= CALIBRATION_MIN_SAMPLES
        )

        return TheoremResult(
            theorem_name="Theorem46_3_CalibrationConvergence",
            holds=holds,
            counterexample=cx,
            proof_sketch=(
                f"For each calibration trace with >= {CALIBRATION_MIN_SAMPLES} "
                f"samples, the last-{CALIBRATION_WINDOW} mean must be >= the "
                f"first-{CALIBRATION_WINDOW} mean minus "
                f"{CALIBRATION_REGRESSION_TOLERANCE:.0%} tolerance.  Follows "
                "from LLN applied to the accuracy distribution."
            ),
            checked_at=ts,
            details={
                "trace_count": len(state.calibration_traces),
                "long_trace_count": long_traces,
                "window_size": CALIBRATION_WINDOW,
                "tolerance": CALIBRATION_REGRESSION_TOLERANCE,
            },
        )


# ── Theorem46_4_ParetoStability ───────────────────────────────────────────────


class Theorem46_4_ParetoStability:
    """Verify Theorem 46.4: The Pareto-optimal bid set is stable.

    **Statement (theory2.tex §46.4)**
    The Pareto-optimal bid set is stable: adding a dominated bid does not
    change the Pareto front.

    Proof sketch
    ────────────
    Let P = pareto(B) for a set of bids B.  A bid b* is *dominated* iff
    there exists b in B such that b dominates b* on all objectives (higher
    semantic_score, lower uncertainty).  By definition of Pareto optimality,
    adding b* to B yields pareto(B ∪ {b*}) = pareto(B) = P.  The theorem
    verifies this constructively by injecting a worst-case dominated bid
    (semantic_score=0, uncertainty=1) and checking P is unchanged.
    """

    statement: str = (
        "The Pareto-optimal bid set is stable: adding a dominated bid does "
        "not change the Pareto front."
    )

    def check(self, state: CompetitionState) -> bool:
        """Return ``True`` iff Pareto stability holds for *state*'s bids.

        Parameters
        ----------
        state:
            Competition state containing the bids to analyse.

        Returns
        -------
        bool
        """
        return self.counterexample(state) is None

    def counterexample(self, state: CompetitionState) -> dict | None:
        """Return a counterexample if Pareto stability is violated, else ``None``.

        Injects a clearly dominated bid and verifies the Pareto front is
        unchanged.

        Parameters
        ----------
        state:
            Competition state whose bids are analysed.

        Returns
        -------
        dict | None
            Violation dict or ``None``.
        """
        bids = list(state.bids)
        if not bids:
            return None

        original_front = _pareto_ids(bids)

        # Inject a dominated bid
        dominated_id = f"dominated-{uuid.uuid4()}"
        dominated_bid = {
            "bid_id": dominated_id,
            "semantic_score": DOMINATED_BID_SCORE,
            "uncertainty": DOMINATED_BID_UNCERTAINTY,
            "member_id": "__dominated__",
            "timestamp": time.time(),
            "move_id": "__dominated__",
        }
        extended = bids + [dominated_bid]
        extended_front = _pareto_ids(extended)

        # The dominated bid must not appear in the extended front
        if dominated_id in extended_front:
            return {
                "dominated_bid_id": dominated_id,
                "original_front_size": len(original_front),
                "extended_front_size": len(extended_front),
                "violation": (
                    "Dominated bid appeared in extended Pareto front — "
                    "Pareto stability is violated."
                ),
            }

        # The front itself must not have changed
        if original_front != extended_front - {dominated_id}:
            return {
                "original_front": sorted(original_front),
                "extended_front": sorted(extended_front - {dominated_id}),
                "violation": (
                    "Pareto front changed after adding a dominated bid — "
                    "stability is violated."
                ),
            }

        return None

    def verify(self, state: CompetitionState) -> TheoremResult:
        """Run a full verification and return a ``TheoremResult``.

        Parameters
        ----------
        state:
            Competition state to check.

        Returns
        -------
        TheoremResult
        """
        ts = time.time()
        front_before = _pareto_ids(state.bids)
        cx = self.counterexample(state)
        holds = cx is None

        return TheoremResult(
            theorem_name="Theorem46_4_ParetoStability",
            holds=holds,
            counterexample=cx,
            proof_sketch=(
                "A dominated bid (score=0, uncertainty=1) is injected.  "
                "By Pareto-dominance, any bid in the original set dominates "
                "it.  Thus pareto(B ∪ {b*}) = pareto(B).  Verified "
                "constructively."
            ),
            checked_at=ts,
            details={
                "bid_count": len(state.bids),
                "pareto_front_size": len(front_before),
            },
        )


# ── Lemma46_A_BidDeltaAntiSymmetry ────────────────────────────────────────────


class Lemma46_A_BidDeltaAntiSymmetry:
    """Verify Lemma 46-A: BidDelta is antisymmetric.

    **Statement (theory2.tex §46.A)**
    BidDelta is antisymmetric:
    ``delta(A, B).value_delta = -delta(B, A).value_delta``

    Proof sketch
    ────────────
    The BidDelta ``value_delta`` field is defined as
    ``semantic_score(B) - semantic_score(A)``.  Therefore
    ``delta(A, B).value_delta = score(B) - score(A) = -(score(A) - score(B))
    = -delta(B, A).value_delta``.  This is a straightforward algebraic
    consequence of the antisymmetry of subtraction.  The lemma verifies this
    for all ordered pairs of bids in the state.
    """

    statement: str = (
        "BidDelta is antisymmetric: delta(A, B).value_delta = "
        "-delta(B, A).value_delta"
    )

    def check(self, state: CompetitionState) -> bool:
        """Return ``True`` iff antisymmetry holds for all bid pairs.

        Parameters
        ----------
        state:
            Competition state containing the bids to check.

        Returns
        -------
        bool
        """
        return self.counterexample(state) is None

    def counterexample(self, state: CompetitionState) -> dict | None:
        """Return the first bid pair violating delta antisymmetry, or ``None``.

        Parameters
        ----------
        state:
            Competition state whose bids are checked.

        Returns
        -------
        dict | None
            Violation dict with keys ``bid_a_id``, ``bid_b_id``,
            ``delta_ab``, ``delta_ba``, ``sum_`` (should be ~0);
            or ``None`` if antisymmetry holds.
        """
        bids = state.bids
        for i, ba in enumerate(bids):
            for j, bb in enumerate(bids):
                if j <= i:
                    continue
                delta_ab = _compute_bid_delta(ba, bb)
                delta_ba = _compute_bid_delta(bb, ba)

                vd_ab = float(delta_ab.get("value_delta", 0.0))
                vd_ba = float(delta_ba.get("value_delta", 0.0))

                # Antisymmetry: vd_ab + vd_ba ≈ 0
                if abs(vd_ab + vd_ba) > FLOAT_TOL * 10:
                    return {
                        "bid_a_id": _bid_id(ba),
                        "bid_b_id": _bid_id(bb),
                        "delta_ab": vd_ab,
                        "delta_ba": vd_ba,
                        "sum_": vd_ab + vd_ba,
                        "violation": (
                            f"delta(A,B)={vd_ab:.6f}, delta(B,A)={vd_ba:.6f}, "
                            f"sum={vd_ab + vd_ba:.6f} ≠ 0"
                        ),
                    }
        return None

    def verify(self, state: CompetitionState) -> TheoremResult:
        """Run a full verification and return a ``TheoremResult``.

        Parameters
        ----------
        state:
            Competition state to check.

        Returns
        -------
        TheoremResult
        """
        ts = time.time()
        cx = self.counterexample(state)
        holds = cx is None

        n = len(state.bids)
        pair_count = n * (n - 1) // 2

        return TheoremResult(
            theorem_name="Lemma46_A_BidDeltaAntiSymmetry",
            holds=holds,
            counterexample=cx,
            proof_sketch=(
                "value_delta(A,B) = score(B) - score(A) = "
                "-(score(A) - score(B)) = -value_delta(B,A).  "
                "Verified for all ordered pairs by checking |delta(A,B) + delta(B,A)| < ε."
            ),
            checked_at=ts,
            details={
                "bid_count": n,
                "pairs_checked": pair_count,
                "tolerance": FLOAT_TOL * 10,
            },
        )


# ── InvariantChecker ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class InvariantChecker:
    """Bundle of theorem instances for bulk invariant verification.

    Holds a list of theorem / lemma objects and can check them all at once
    against a ``CompetitionState`` snapshot.  The checker is intentionally
    *open*: callers can inject any object that implements ``verify(state)``.

    Parameters
    ----------
    theorems:
        List of theorem instances.  Each must have a ``verify`` method that
        accepts a :class:`CompetitionState` and returns a :class:`TheoremResult`.
    """

    theorems: list[Any]

    def check_all(self, state: CompetitionState) -> list[TheoremResult]:
        """Verify all theorems against *state*.

        Parameters
        ----------
        state:
            Competition state to check.

        Returns
        -------
        list[TheoremResult]
            One result per theorem, in the same order as ``self.theorems``.
        """
        results: list[TheoremResult] = []
        for thm in self.theorems:
            try:
                result = thm.verify(state)
                results.append(result)
            except Exception as exc:
                _log.warning(
                    "Theorem %s raised during verify: %s",
                    getattr(thm, "__class__", {__name__: "?"}).__name__,
                    exc,
                )
                results.append(
                    TheoremResult(
                        theorem_name=type(thm).__name__,
                        holds=False,
                        counterexample={"error": str(exc)},
                        proof_sketch="Verification raised an exception.",
                        details={"exception": str(exc)},
                    )
                )
        return results

    def check_one(self, theorem_name: str, state: CompetitionState) -> TheoremResult | None:
        """Verify a single theorem identified by *theorem_name*.

        Parameters
        ----------
        theorem_name:
            Class name of the theorem to check.
        state:
            Competition state to check.

        Returns
        -------
        TheoremResult | None
            The result, or ``None`` if no theorem with that name is registered.
        """
        for thm in self.theorems:
            if type(thm).__name__ == theorem_name:
                try:
                    return thm.verify(state)
                except Exception as exc:
                    _log.warning("Theorem %s raised: %s", theorem_name, exc)
                    return TheoremResult(
                        theorem_name=theorem_name,
                        holds=False,
                        counterexample={"error": str(exc)},
                        proof_sketch="Verification raised an exception.",
                        details={"exception": str(exc)},
                    )
        return None

    def summary(self, results: list[TheoremResult]) -> dict:
        """Produce a human-readable summary of verification results.

        Parameters
        ----------
        results:
            List of ``TheoremResult`` objects, typically from ``check_all``.

        Returns
        -------
        dict
            Keys: ``total``, ``passing``, ``failing``, ``pass_rate``,
            ``failures`` (list of dicts with ``name`` and ``counterexample``).
        """
        passing = [r for r in results if r.holds]
        failing = [r for r in results if not r.holds]
        total = len(results)
        return {
            "total": total,
            "passing": len(passing),
            "failing": len(failing),
            "pass_rate": len(passing) / total if total > 0 else 1.0,
            "failures": [
                {
                    "name": r.theorem_name,
                    "counterexample": r.counterexample,
                    "summary": r.summary(),
                }
                for r in failing
            ],
        }

    @classmethod
    def build_default(cls) -> InvariantChecker:
        """Build an ``InvariantChecker`` containing all Ch 46 theorems and lemmas.

        Returns
        -------
        InvariantChecker
            Pre-loaded with instances of all five theorems and the lemma.
        """
        return cls(
            theorems=[
                Theorem46_1_MonotonicBidRefinement(),
                Theorem46_2_ChallengeConservativity(),
                Theorem46_3_CalibrationConvergence(),
                Theorem46_4_ParetoStability(),
                Lemma46_A_BidDeltaAntiSymmetry(),
            ]
        )


# ── TheoremRegistry ───────────────────────────────────────────────────────────


class TheoremRegistry:
    """Global registry mapping theorem names to verifier instances.

    The registry is a *class-level* dict so that all code in a Python
    process shares the same namespace.  At the bottom of this module all
    five theorems and the lemma are registered automatically.

    Usage example::

        checker = TheoremRegistry.build_checker()
        results = checker.check_all(state)

    Design note
    ───────────
    The registry is intentionally simple — it does not attempt dynamic
    discovery or plugin loading.  Theorems for other chapters should use
    their own registries or extend this one via ``register``.
    """

    _registry: dict[str, Any] = {}

    @classmethod
    def register(cls, name: str, theorem: Any) -> None:
        """Register *theorem* under *name*.

        Silently overwrites any existing entry with the same name.

        Parameters
        ----------
        name:
            Canonical theorem name (should match the class name).
        theorem:
            Theorem instance (must implement ``verify(state)``).
        """
        cls._registry[name] = theorem
        _log.debug("TheoremRegistry: registered %s", name)

    @classmethod
    def get(cls, name: str) -> Any | None:
        """Retrieve the theorem registered under *name*.

        Parameters
        ----------
        name:
            Canonical theorem name.

        Returns
        -------
        Any | None
            The registered theorem instance, or ``None`` if not found.
        """
        return cls._registry.get(name)

    @classmethod
    def all_theorems(cls) -> list[Any]:
        """Return all registered theorem instances.

        Returns
        -------
        list[Any]
            List of theorem instances in registration order.
        """
        return list(cls._registry.values())

    @classmethod
    def build_checker(cls) -> InvariantChecker:
        """Build an ``InvariantChecker`` from all registered theorems.

        Returns
        -------
        InvariantChecker
            Checker loaded with every theorem in the registry.
        """
        return InvariantChecker(theorems=cls.all_theorems())


# ── Private helpers (module-level) ────────────────────────────────────────────


def _group_by_move_id(bids: list[Any]) -> dict[str, list[Any]]:
    """Group *bids* by their ``move_id``.

    Parameters
    ----------
    bids:
        List of bid objects or dicts.

    Returns
    -------
    dict[str, list[Any]]
        Mapping from move_id string to the list of bids sharing that id.
        Bids with an empty ``move_id`` are excluded.
    """
    result: dict[str, list[Any]] = {}
    for bid in bids:
        mid = _move_id(bid)
        if mid:
            result.setdefault(mid, []).append(bid)
    return result


# ── Module-load registration ──────────────────────────────────────────────────
# Register all Ch 46 theorems and lemmas in TheoremRegistry so callers can
# use TheoremRegistry.build_checker() without knowing individual class names.

TheoremRegistry.register(
    "Theorem46_1_MonotonicBidRefinement",
    Theorem46_1_MonotonicBidRefinement(),
)
TheoremRegistry.register(
    "Theorem46_2_ChallengeConservativity",
    Theorem46_2_ChallengeConservativity(),
)
TheoremRegistry.register(
    "Theorem46_3_CalibrationConvergence",
    Theorem46_3_CalibrationConvergence(),
)
TheoremRegistry.register(
    "Theorem46_4_ParetoStability",
    Theorem46_4_ParetoStability(),
)
TheoremRegistry.register(
    "Lemma46_A_BidDeltaAntiSymmetry",
    Lemma46_A_BidDeltaAntiSymmetry(),
)

# ── Public re-exports ─────────────────────────────────────────────────────────

__all__ = [
    # Value objects
    "TheoremResult",
    # State container
    "CompetitionState",
    # Theorems
    "Theorem46_1_MonotonicBidRefinement",
    "Theorem46_2_ChallengeConservativity",
    "Theorem46_3_CalibrationConvergence",
    "Theorem46_4_ParetoStability",
    # Lemmas
    "Lemma46_A_BidDeltaAntiSymmetry",
    # Orchestrators
    "InvariantChecker",
    "TheoremRegistry",
    # Constants
    "FLOAT_TOL",
    "CALIBRATION_REGRESSION_TOLERANCE",
    "CALIBRATION_WINDOW",
    "CALIBRATION_MIN_SAMPLES",
    "DOMINATED_BID_SCORE",
    "DOMINATED_BID_UNCERTAINTY",
]
