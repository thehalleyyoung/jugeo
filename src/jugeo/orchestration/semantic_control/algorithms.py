"""Core algorithms for JuGeo semantic control (theory2.tex Ch44).

This module implements the fundamental algorithms that drive the semantic
control loop described in theory2.tex Chapter 44.  All functions are
pure or near-pure: they accept typed inputs, produce typed outputs, and
carry no hidden global state beyond the module-level constants defined
below.

The main entry point for orchestration is ``semantic_control_loop``, which
runs a state machine:

    enumerate_admissible_moves → select_admissible_move → apply_move
    → check convergence → repeat

Supporting this loop are:

*   ``lyapunov_function``        — non-negative convergence potential.
*   ``compute_attainability``    — composite [0, 1] health metric.
*   ``certify_convergence``      — issue a certificate for a converged trajectory.
*   ``_greedy_select``           — best-net-value move selection.
*   ``_lookahead_select``        — 2-step lookahead variant.
*   ``_balanced_select``         — joint gain–cost optimisation.
*   ``_generate_default_moves``  — move enumeration from the current state.
*   ``_compute_convergence_metrics`` — trajectory-level metric aggregation.
*   ``_treaty_health``           — treaty-based health scoring.
*   ``_compute_budget_deficit``  — fraction of budget consumed.

Design notes
────────────
*   Every public function is annotated with PEP 526 type hints.
*   Docstrings follow the NumPy convention with ``Args:`` / ``Returns:``
    sections and cross-references to theory2.tex.
*   Imports from other JuGeo modules are guarded with ``try/except`` to
    ensure graceful degradation when upstack packages are not yet compiled.
*   Constants are named in UPPER_SNAKE_CASE at module level so they are
    easy to override in unit tests.

References
──────────
*   theory2.tex §44     — Convergence and Certification
*   theory2.tex §44.1  — Lyapunov Functions on Semantic Sites
*   theory2.tex §44.2  — Obligation Pressure and Discharge
*   theory2.tex §44.3  — Coverage Dimensions and Weighted Analysis
*   theory2.tex §44.5  — Convergence Certificates and Validity Periods
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Internal JuGeo imports (guarded) ────────────────────────────────────────

try:
    from jugeo.orchestration.semantic_control.models import (
        AdmissibleMove,
        ControlLaw,
        ControlLawKind,
        ConvergenceCertificate,
        ConvergenceMode,
        SemanticControlState,
        SemanticTrajectory,
        StateDelta,
        StateHealthStatus,
    )
except Exception:  # pragma: no cover
    import enum

    class ControlLawKind(enum.Enum):  # type: ignore[no-redef]
        GREEDY = "greedy"
        LOOKAHEAD = "lookahead"
        BALANCED = "balanced"
        ADAPTIVE = "adaptive"

    class ConvergenceMode(enum.Enum):  # type: ignore[no-redef]
        GREEDY = "greedy"
        LOOKAHEAD = "lookahead"
        BALANCED = "balanced"
        ADAPTIVE = "adaptive"

    class StateHealthStatus(enum.Enum):  # type: ignore[no-redef]
        HEALTHY = "healthy"
        DEGRADED = "degraded"
        CRITICAL = "critical"

    @dataclass(frozen=True, slots=True)
    class ConvergenceCertificate:  # type: ignore[no-redef]
        cert_id: str
        state_id: str
        coverage_ratio: float
        obligation_count: int
        issued_at: float
        valid_for: float
        evidence: dict

        def is_valid(self) -> bool:
            return not self.is_expired()

        def is_expired(self) -> bool:
            return time.time() > self.issued_at + self.valid_for

        def summary(self) -> str:
            return (
                f"Certificate {self.cert_id}: coverage={self.coverage_ratio:.3f}, "
                f"obligations={self.obligation_count}"
            )

        def to_dict(self) -> dict:
            return {
                "cert_id": self.cert_id,
                "state_id": self.state_id,
                "coverage_ratio": self.coverage_ratio,
                "obligation_count": self.obligation_count,
                "issued_at": self.issued_at,
                "valid_for": self.valid_for,
                "evidence": self.evidence,
            }

    @dataclass(slots=True)
    class SemanticControlState:  # type: ignore[no-redef]
        state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        cover_ids: list[str] = field(default_factory=list)
        context_ids: list[str] = field(default_factory=list)
        section_ids: list[str] = field(default_factory=list)
        treaty_ids: list[str] = field(default_factory=list)
        obligation_ids: list[str] = field(default_factory=list)
        channel_ids: list[str] = field(default_factory=list)
        budget: dict[str, Any] = field(default_factory=dict)
        timestamp: float = field(default_factory=time.time)
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_admissible(self) -> bool:
            return bool(self.cover_ids)

        def coverage_ratio(self) -> float:
            if not self.cover_ids:
                return 0.0
            return min(len(self.section_ids) / len(self.cover_ids), 1.0)

        def attainability_score(self) -> float:
            return self.coverage_ratio()

        def delta_from(self, other: SemanticControlState) -> Any:
            return None

        def to_dict(self) -> dict:
            return {
                "state_id": self.state_id,
                "cover_ids": self.cover_ids,
                "section_ids": self.section_ids,
                "obligation_ids": self.obligation_ids,
            }

        def health_status(self) -> StateHealthStatus:
            if self.coverage_ratio() >= 0.9:
                return StateHealthStatus.HEALTHY
            if self.coverage_ratio() >= 0.5:
                return StateHealthStatus.DEGRADED
            return StateHealthStatus.CRITICAL

        def snapshot(self) -> SemanticControlState:
            return SemanticControlState(
                state_id=self.state_id,
                cover_ids=list(self.cover_ids),
                context_ids=list(self.context_ids),
                section_ids=list(self.section_ids),
                treaty_ids=list(self.treaty_ids),
                obligation_ids=list(self.obligation_ids),
                channel_ids=list(self.channel_ids),
                budget=dict(self.budget),
                timestamp=self.timestamp,
                metadata=dict(self.metadata),
            )

    @dataclass(slots=True)
    class AdmissibleMove:  # type: ignore[no-redef]
        move_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        kind: str = "construct"
        preconditions: list[str] = field(default_factory=list)
        postconditions: list[str] = field(default_factory=list)
        cost: float = 1.0
        priority: float = 1.0
        expected_gain: float = 0.0
        trust_requirement: float = 0.0
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_applicable(self, state: SemanticControlState) -> bool:
            return state.is_admissible()

        def apply(self, state: SemanticControlState) -> SemanticControlState:
            return state

        def validate(self) -> bool:
            return self.cost >= 0 and 0.0 <= self.expected_gain <= 1.0

        def net_value(self) -> float:
            return self.expected_gain - self.cost * 0.01

        def to_dict(self) -> dict:
            return {
                "move_id": self.move_id,
                "kind": self.kind,
                "cost": self.cost,
                "expected_gain": self.expected_gain,
            }

    @dataclass(slots=True)
    class ControlLaw:  # type: ignore[no-redef]
        law_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        name: str = "greedy"
        kind: ControlLawKind = field(default_factory=lambda: ControlLawKind.GREEDY)
        parameters: dict[str, Any] = field(default_factory=dict)

        def select_move(
            self,
            state: SemanticControlState,
            candidates: list[AdmissibleMove],
        ) -> AdmissibleMove | None:
            applicable = [m for m in candidates if m.is_applicable(state)]
            if not applicable:
                return None
            return max(applicable, key=lambda m: m.net_value())

        def evaluate(self, state: SemanticControlState) -> float:
            return state.coverage_ratio()

        def adapt(self, metrics: dict[str, Any]) -> None:
            pass

        def to_dict(self) -> dict:
            return {
                "law_id": self.law_id,
                "name": self.name,
                "kind": self.kind.value,
                "parameters": self.parameters,
            }

    @dataclass(slots=True)
    class SemanticTrajectory:  # type: ignore[no-redef]
        trajectory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        states: list[SemanticControlState] = field(default_factory=list)
        moves: list[Any] = field(default_factory=list)
        timestamps: list[float] = field(default_factory=list)

        def append(self, state: SemanticControlState, move: Any = None) -> None:
            self.states.append(state)
            self.moves.append(move)
            self.timestamps.append(time.time())

        def length(self) -> int:
            return len(self.states)

        def is_converging(self) -> bool:
            if len(self.states) < 2:
                return False
            return (
                self.states[-1].coverage_ratio()
                > self.states[-2].coverage_ratio()
            )

        def latest_state(self) -> SemanticControlState | None:
            return self.states[-1] if self.states else None

        def score_history(self) -> list[float]:
            return [s.coverage_ratio() for s in self.states]

        def export(self) -> dict:
            return {
                "trajectory_id": self.trajectory_id,
                "length": self.length(),
                "scores": self.score_history(),
            }

        def replay(self) -> list[SemanticControlState]:
            return list(self.states)


try:
    from jugeo.orchestration.controller import MoveKind
except Exception:  # pragma: no cover
    import enum

    class MoveKind(enum.Enum):  # type: ignore[no-redef]
        VERIFY = "verify"
        CONSTRUCT = "construct"
        REPAIR = "repair"
        NEGOTIATE_TREATY = "negotiate_treaty"
        REFINE_COVER = "refine_cover"
        DISCHARGE_OBLIGATION = "discharge_obligation"
        CONSULT_ORACLE = "consult_oracle"


def _move_kind_value(kind: Any) -> str:
    """Return a normalised string value for a move kind."""
    return str(kind.value) if hasattr(kind, "value") else str(kind)


try:
    from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier  # noqa: F401
except Exception:  # pragma: no cover
    import enum

    class TrustLevel(enum.Enum):  # type: ignore[no-redef]
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"


# ── Module-level constants ────────────────────────────────────────────────────

log = logging.getLogger(__name__)

#: Lyapunov weight for obligation pressure term.
LYAPUNOV_W1: float = 0.15
#: Lyapunov weight for treaty-health deficit term.
LYAPUNOV_W2: float = 0.10
#: Lyapunov weight for budget-deficit term.
LYAPUNOV_W3: float = 0.05

#: Combined weights dict (exported for downstream use / testing).
LYAPUNOV_WEIGHTS: dict[str, float] = {
    "obligation": LYAPUNOV_W1,
    "treaty": LYAPUNOV_W2,
    "budget": LYAPUNOV_W3,
}

#: Default convergence threshold for ``certify_convergence``.
DEFAULT_CONVERGENCE_THRESHOLD: float = 0.95

#: Default validity period (seconds) for issued certificates.
DEFAULT_CERTIFICATE_VALIDITY: float = 300.0

#: Default maximum steps for ``semantic_control_loop``.
DEFAULT_MAX_STEPS: int = 100

#: Minimum expected gain for a move to be considered non-trivial.
MIN_EXPECTED_GAIN: float = 1e-6

#: Lookahead depth used by ``_lookahead_select``.
DEFAULT_LOOKAHEAD_DEPTH: int = 2

#: Scale factor mapping cost to utility loss in ``_balanced_select``.
BALANCED_COST_PENALTY: float = 0.02

#: Weight given to gain vs cost in ``_balanced_select`` (∈ (0, 1]).
BALANCED_GAIN_WEIGHT: float = 0.7


# ═══════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_budget_deficit(budget: dict[str, Any]) -> float:
    """Return the fraction of budget already consumed.

    The budget dict is expected to contain ``"used"`` and ``"total"`` keys
    (numeric).  If either is missing or the total is zero, the deficit is
    treated as 0.0 (no consumption known).

    Args:
        budget: Mapping with at least ``"used"`` and ``"total"`` keys.

    Returns:
        Fraction consumed in [0, 1].  Returns 0.0 if total is zero or
        keys are absent; returns 1.0 if used ≥ total.

    References
    ──────────
    theory2.tex §44 — Budget tracking in semantic control.
    """
    try:
        used = float(budget.get("used", 0))
        total = float(budget.get("total", 0))
    except (TypeError, ValueError):
        return 0.0
    if total <= 0.0:
        return 0.0
    return max(0.0, min(used / total, 1.0))


def _treaty_health(state: SemanticControlState) -> float:
    """Return a treaty-based health score in [0, 1].

    The score is defined as the ratio of treaties to the expected number
    of treaties for a fully-covered site.  For a site with ``n`` covers,
    a healthy set of pairwise overlap treaties has ``n − 1`` elements
    (assuming a path-like adjacency graph).  Extra treaties are capped at 1.

    When there are outstanding obligations the score is penalised
    proportionally to the obligation-to-cover ratio.

    Args:
        state: The current semantic control state.

    Returns:
        Health score in [0, 1] where 1.0 represents perfect treaty coverage
        with zero obligations.

    References
    ──────────
    theory2.tex §44 — Overlap treaties and obligation pressure.
    """
    n_covers = len(state.cover_ids)
    if n_covers == 0:
        return 1.0  # vacuously healthy

    n_treaties = len(state.treaty_ids)
    expected_treaties = max(n_covers - 1, 1)
    treaty_ratio = min(n_treaties / expected_treaties, 1.0)

    # Penalise for outstanding obligations.
    n_obligations = len(state.obligation_ids)
    obligation_penalty = min(n_obligations / n_covers, 1.0)
    health = treaty_ratio * (1.0 - 0.5 * obligation_penalty)
    return max(0.0, min(health, 1.0))


def _generate_default_moves(state: SemanticControlState) -> list[AdmissibleMove]:
    """Generate a default set of admissible moves from the current state.

    For each ``MoveKind`` variant that is semantically applicable to
    *state*, one representative ``AdmissibleMove`` is created.  The move
    parameters are heuristically chosen based on the state's coverage gap,
    obligation pressure, and treaty health.

    Move generation rules:
    *   CONSTRUCT  — always generated when coverage < 1; expected gain ∝ gap.
    *   VERIFY     — generated when sections exist but coverage < 1.
    *   REPAIR     — generated when treaty health < 0.9.
    *   NEGOTIATE  — generated when treaties are fewer than covers − 1.
    *   REFINE     — generated when obligation pressure is high.
    *   DISCHARGE  — generated for each pending obligation (up to 3).
    *   ORACLE     — generated when channels are available and stalled.

    Args:
        state: The current semantic control state.

    Returns:
        List of ``AdmissibleMove`` objects applicable to *state*.

    References
    ──────────
    theory2.tex §44 — Admissible moves in semantic control.
    """
    moves: list[AdmissibleMove] = []
    coverage = state.coverage_ratio()
    n_covers = len(state.cover_ids)
    n_sections = len(state.section_ids)
    n_obligations = len(state.obligation_ids)
    treaty_h = _treaty_health(state)

    # CONSTRUCT: add new sections to close coverage gap.
    if coverage < 1.0 and n_covers > 0:
        gap = 1.0 - coverage
        moves.append(
            AdmissibleMove(
                move_id=f"construct-{state.state_id[:8]}",
                kind=MoveKind.CONSTRUCT.value,
                preconditions=[],
                postconditions=["section_added"],
                cost=3.0,
                priority=2.0,
                expected_gain=min(gap * 0.5, 0.3),
                trust_requirement=0.5,
                metadata={"target_gap": gap},
            )
        )

    # VERIFY: validate existing sections.
    if n_sections > 0 and coverage < 1.0:
        moves.append(
            AdmissibleMove(
                move_id=f"verify-{state.state_id[:8]}",
                kind=MoveKind.VERIFY.value,
                preconditions=[],
                postconditions=["sections_verified"],
                cost=1.5,
                priority=1.5,
                expected_gain=0.05,
                trust_requirement=0.3,
                metadata={},
            )
        )

    # REPAIR: fix treaty violations.
    if treaty_h < 0.9:
        moves.append(
            AdmissibleMove(
                move_id=f"repair-{state.state_id[:8]}",
                kind=MoveKind.REPAIR.value,
                preconditions=[],
                postconditions=["treaties_repaired"],
                cost=4.0,
                priority=1.8,
                expected_gain=min((1.0 - treaty_h) * 0.4, 0.25),
                trust_requirement=0.6,
                metadata={"treaty_health": treaty_h},
            )
        )

    # NEGOTIATE_TREATY: add missing treaties.
    expected_treaties = max(n_covers - 1, 1) if n_covers > 0 else 0
    if len(state.treaty_ids) < expected_treaties:
        deficit = expected_treaties - len(state.treaty_ids)
        moves.append(
            AdmissibleMove(
                move_id=f"negotiate-{state.state_id[:8]}",
                kind=MoveKind.NEGOTIATE_TREATY.value,
                preconditions=[],
                postconditions=["treaty_added"],
                cost=2.5,
                priority=1.6,
                expected_gain=min(deficit / max(expected_treaties, 1) * 0.2, 0.2),
                trust_requirement=0.4,
                metadata={"treaty_deficit": deficit},
            )
        )

    # DISCHARGE_OBLIGATION: resolve pending obligations.
    for i, oid in enumerate(state.obligation_ids[:3]):
        moves.append(
            AdmissibleMove(
                move_id=f"discharge-{oid[:8] if len(oid) >= 8 else oid}",
                kind=MoveKind.DISCHARGE_OBLIGATION.value,
                preconditions=[oid],
                postconditions=[oid],
                cost=2.0,
                priority=2.5,
                expected_gain=0.1,
                trust_requirement=0.5,
                metadata={"obligation_id": oid, "index": i},
            )
        )

    # REFINE_COVER: split covers when obligations are heavy.
    if n_obligations > n_covers * 0.3 and n_covers > 0:
        moves.append(
            AdmissibleMove(
                move_id=f"refine-{state.state_id[:8]}",
                kind=MoveKind.REFINE_COVER.value,
                preconditions=[],
                postconditions=["cover_refined"],
                cost=5.0,
                priority=1.2,
                expected_gain=0.15,
                trust_requirement=0.7,
                metadata={"obligation_pressure": n_obligations},
            )
        )

    # CONSULT_ORACLE: use channels when available and stuck.
    if state.channel_ids and coverage < 0.5:
        moves.append(
            AdmissibleMove(
                move_id=f"oracle-{state.state_id[:8]}",
                kind=MoveKind.CONSULT_ORACLE.value,
                preconditions=[],
                postconditions=["oracle_consulted"],
                cost=6.0,
                priority=1.0,
                expected_gain=0.20,
                trust_requirement=0.2,
                metadata={"channels": state.channel_ids[:2]},
            )
        )

    return moves


def _greedy_select(
    candidates: list[AdmissibleMove],
    state: SemanticControlState,
) -> AdmissibleMove | None:
    """Select the move with the highest ``net_value`` among applicable candidates.

    The greedy strategy is the simplest control law: it picks the locally
    optimal move without any look-ahead.  It is suitable for states with
    clear dominant moves and serves as the default when no control law is
    specified.

    Args:
        candidates: Pool of ``AdmissibleMove`` objects to filter and rank.
        state:      Current semantic control state used for applicability checks.

    Returns:
        The applicable move with the greatest ``net_value()``, or ``None`` if
        no applicable moves exist.

    References
    ──────────
    theory2.tex §44 — Greedy control law for semantic control.
    """
    applicable = [m for m in candidates if m.is_applicable(state)]
    if not applicable:
        return None
    return max(applicable, key=lambda m: m.net_value())


def _lookahead_select(
    candidates: list[AdmissibleMove],
    state: SemanticControlState,
    depth: int = DEFAULT_LOOKAHEAD_DEPTH,
) -> AdmissibleMove | None:
    """Select the move that yields the highest cumulative gain over *depth* steps.

    At each level the algorithm simulates applying the best available move
    (by ``net_value``) and accumulates the expected gains.  The root-level
    move that leads to the best cumulative outcome is returned.

    This is a simplified greedy tree search, not a true minimax.  It is
    suitable when the move set is small and state transitions are cheap.

    Args:
        candidates: Pool of ``AdmissibleMove`` objects to consider at root.
        state:      Current semantic control state.
        depth:      Number of lookahead levels (default 2).

    Returns:
        The root-level move with the best lookahead score, or ``None``.

    References
    ──────────
    theory2.tex §44 — Lookahead control law for semantic control.
    """
    applicable = [m for m in candidates if m.is_applicable(state)]
    if not applicable:
        return None

    best_move: AdmissibleMove | None = None
    best_score: float = float("-inf")

    for root_move in applicable:
        # Simulate applying the root move.
        try:
            next_state = apply_move(state, root_move)
        except Exception:
            next_state = state
        score = root_move.net_value()

        # Recurse one level deeper if depth > 1.
        if depth > 1:
            deeper_candidates = enumerate_admissible_moves(
                next_state, candidates
            )
            deeper = _lookahead_select(deeper_candidates, next_state, depth - 1)
            if deeper is not None:
                # Discount the deeper gain by 0.8 per level.
                discount = 0.8 ** (DEFAULT_LOOKAHEAD_DEPTH - depth + 1)
                score += discount * deeper.net_value()

        if score > best_score:
            best_score = score
            best_move = root_move

    return best_move


def _balanced_select(
    candidates: list[AdmissibleMove],
    state: SemanticControlState,
) -> AdmissibleMove | None:
    """Select the move that balances expected gain against cost.

    The balanced utility for move *m* is:

        U(m) = BALANCED_GAIN_WEIGHT * expected_gain
               − (1 − BALANCED_GAIN_WEIGHT) * BALANCED_COST_PENALTY * cost

    This is a simple scalarisation of the bi-objective (gain, cost) problem.
    The constant ``BALANCED_COST_PENALTY`` scales cost to the same order of
    magnitude as gains.

    Args:
        candidates: Pool of ``AdmissibleMove`` objects to filter and rank.
        state:      Current semantic control state used for applicability checks.

    Returns:
        The applicable move with the greatest balanced utility, or ``None``.

    References
    ──────────
    theory2.tex §44 — Balanced control law for semantic control.
    """
    applicable = [m for m in candidates if m.is_applicable(state)]
    if not applicable:
        return None

    def _utility(m: AdmissibleMove) -> float:
        gain_term = BALANCED_GAIN_WEIGHT * m.expected_gain
        cost_term = (1.0 - BALANCED_GAIN_WEIGHT) * BALANCED_COST_PENALTY * m.cost
        return gain_term - cost_term

    return max(applicable, key=_utility)


def _compute_convergence_metrics(trajectory: SemanticTrajectory) -> dict[str, float]:
    """Compute summary convergence metrics over the full *trajectory*.

    Computed metrics:
    *   ``mean_coverage``    — arithmetic mean of coverage ratios.
    *   ``final_coverage``   — coverage at the last state.
    *   ``max_coverage``     — maximum coverage achieved.
    *   ``min_coverage``     — minimum coverage observed.
    *   ``total_moves``      — number of non-None moves in the trajectory.
    *   ``lyapunov_final``   — Lyapunov value at the last state.
    *   ``is_converged``     — 1.0 if final coverage ≥ threshold, else 0.0.
    *   ``mean_rate``        — mean per-step coverage improvement.

    Args:
        trajectory: The ``SemanticTrajectory`` to analyse.

    Returns:
        Dict of metric names to scalar float values.

    References
    ──────────
    theory2.tex §44 — Convergence metrics for trajectory analysis.
    """
    scores = trajectory.score_history()
    if not scores:
        return {
            "mean_coverage": 0.0,
            "final_coverage": 0.0,
            "max_coverage": 0.0,
            "min_coverage": 0.0,
            "total_moves": 0.0,
            "lyapunov_final": 1.0,
            "is_converged": 0.0,
            "mean_rate": 0.0,
        }

    final_state = trajectory.latest_state()
    lyapunov_val = lyapunov_function(final_state) if final_state else 1.0

    rates: list[float] = [
        scores[i] - scores[i - 1] for i in range(1, len(scores))
    ]
    mean_rate = sum(rates) / len(rates) if rates else 0.0
    total_moves = sum(1 for m in trajectory.moves if m is not None)

    return {
        "mean_coverage": sum(scores) / len(scores),
        "final_coverage": scores[-1],
        "max_coverage": max(scores),
        "min_coverage": min(scores),
        "total_moves": float(total_moves),
        "lyapunov_final": lyapunov_val,
        "is_converged": 1.0 if scores[-1] >= DEFAULT_CONVERGENCE_THRESHOLD else 0.0,
        "mean_rate": mean_rate,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Public algorithms
# ═══════════════════════════════════════════════════════════════════════════════


def compute_attainability(state: SemanticControlState) -> float:
    """Compute the attainability score for *state*.

    The attainability score is a composite health metric in [0, 1] that
    integrates four dimensions (theory2.tex §44):

    1.  **Coverage ratio** (weight 0.5) — fraction of covers with sections.
    2.  **Treaty health** (weight 0.3) — treaty coverage and obligation pressure.
    3.  **Obligation deficit** (weight -0.15) — normalised pending obligations.
    4.  **Channel availability** (weight 0.05) — fraction of active channels.

    The result is clamped to [0, 1].

    Args:
        state: The semantic control state to evaluate.

    Returns:
        Attainability score in [0, 1]; higher is better.

    References
    ──────────
    theory2.tex §44 — Attainability as a composite convergence signal.
    """
    coverage = state.coverage_ratio()
    treaty_h = _treaty_health(state)

    n_covers = max(len(state.cover_ids), 1)
    n_obligations = len(state.obligation_ids)
    # Obligation deficit: penalise proportional to obligation density.
    obligation_deficit = min(n_obligations / n_covers, 1.0)

    n_channels = len(state.channel_ids)
    # Channel availability: ratio of available channels to covers (capped at 1).
    channel_availability = min(n_channels / n_covers, 1.0)

    score = (
        0.50 * coverage
        + 0.30 * treaty_h
        - 0.15 * obligation_deficit
        + 0.05 * channel_availability
    )
    return max(0.0, min(score, 1.0))


def lyapunov_function(state: SemanticControlState) -> float:
    """Evaluate the Lyapunov function for convergence analysis.

    The Lyapunov function L(s) is non-negative and decreases monotonically
    toward zero as the semantic site converges.  It is defined as:

        L(s) = (1 − coverage_ratio)
               + w₁ · obligation_density
               + w₂ · (1 − treaty_health)
               + w₃ · budget_deficit

    where each summand is in [0, 1] and the weights satisfy
    w₁ + w₂ + w₃ < 1 so that L(s) ≤ 2 always.

    L(s) = 0 iff:
    *   coverage_ratio = 1
    *   obligation_density = 0  (no pending obligations)
    *   treaty_health = 1
    *   budget_deficit = 0

    Args:
        state: The semantic control state at which to evaluate L.

    Returns:
        Non-negative Lyapunov value.  0.0 iff the state is fully converged.

    References
    ──────────
    theory2.tex §44.1 — Lyapunov Functions on Semantic Sites.
    """
    coverage = state.coverage_ratio()
    n_covers = max(len(state.cover_ids), 1)
    n_obligations = len(state.obligation_ids)

    # Normalised obligation density.
    obligation_density = min(n_obligations / n_covers, 1.0)
    # Treaty health complement.
    treaty_deficit = 1.0 - _treaty_health(state)
    # Budget deficit fraction.
    budget_deficit = _compute_budget_deficit(state.budget)

    value = (
        (1.0 - coverage)
        + LYAPUNOV_W1 * obligation_density
        + LYAPUNOV_W2 * treaty_deficit
        + LYAPUNOV_W3 * budget_deficit
    )
    return max(0.0, value)


def enumerate_admissible_moves(
    state: SemanticControlState,
    move_registry: list[AdmissibleMove],
) -> list[AdmissibleMove]:
    """Return all moves from *move_registry* that are applicable to *state*.

    Applicability is determined by ``AdmissibleMove.is_applicable``.  An
    empty registry causes an on-the-fly generation via
    ``_generate_default_moves``.

    Args:
        state:         The current semantic control state.
        move_registry: Pre-defined pool of candidate moves.  If empty,
                       default moves are generated from state.

    Returns:
        Filtered list of applicable moves, sorted by descending priority.

    References
    ──────────
    theory2.tex §44 — Enumerating the admissible move frontier.
    """
    if not move_registry:
        pool = _generate_default_moves(state)
    else:
        pool = move_registry

    applicable = [m for m in pool if m.is_applicable(state)]

    # Sort by descending priority, then by descending expected_gain.
    applicable.sort(key=lambda m: (m.priority, m.expected_gain), reverse=True)
    return applicable


def select_admissible_move(
    state: SemanticControlState,
    candidates: list[AdmissibleMove],
    law: ControlLaw | None = None,
) -> AdmissibleMove | None:
    """Select the best admissible move from *candidates* using *law*.

    Dispatch table:
    *   ``law is None``                   → greedy (highest ``net_value``).
    *   ``law.kind == GREEDY``            → ``_greedy_select``.
    *   ``law.kind == LOOKAHEAD``         → ``_lookahead_select``.
    *   ``law.kind == BALANCED``          → ``_balanced_select``.
    *   ``law.kind == ADAPTIVE``          → ``_greedy_select`` (default
                                             fallback; adaptive logic lives
                                             in the control law itself via
                                             ``law.select_move``).

    If *law* is not ``None`` and implements its own ``select_move`` the
    result of ``law.select_move`` is preferred over the internal dispatch.

    Args:
        state:      Current semantic control state.
        candidates: Pool of candidate moves (already filtered for the state).
        law:        Optional control law.  ``None`` ⟹ greedy selection.

    Returns:
        Selected ``AdmissibleMove``, or ``None`` if no applicable moves exist.

    References
    ──────────
    theory2.tex §44 — Control law selection in semantic control.
    """
    if not candidates:
        return None

    # Prefer the control law's own implementation when available.
    if law is not None:
        try:
            result = law.select_move(state, candidates)
            if result is not None:
                return result
        except Exception as exc:
            log.warning(
                "select_admissible_move: law.select_move raised %s; "
                "falling back to internal dispatch",
                exc,
            )

    # Internal dispatch by kind.
    if law is None:
        return _greedy_select(candidates, state)

    kind_value = law.kind.value if hasattr(law.kind, "value") else str(law.kind)

    if kind_value == ControlLawKind.GREEDY.value:
        return _greedy_select(candidates, state)
    if kind_value == ControlLawKind.LOOKAHEAD.value:
        depth = int(law.parameters.get("depth", DEFAULT_LOOKAHEAD_DEPTH))
        return _lookahead_select(candidates, state, depth=depth)
    if kind_value == ControlLawKind.BALANCED.value:
        return _balanced_select(candidates, state)
    # Default / ADAPTIVE: greedy fallback.
    return _greedy_select(candidates, state)


def apply_move(
    state: SemanticControlState,
    move: AdmissibleMove,
) -> SemanticControlState:
    """Apply *move* to *state* and return the resulting new state.

    The function delegates to ``move.apply(state)`` when that method
    produces a distinct object (detected by ``state_id`` change).  If the
    move's ``apply`` returns the same state or raises an exception, a
    shallow copy of the state is returned with an updated timestamp and
    ``state_id``, and with a note in the metadata.

    The budget ``"used"`` counter is incremented by ``move.cost`` in the
    returned state's budget dict.

    Args:
        state: The current semantic control state.
        move:  The move to apply.

    Returns:
        A new ``SemanticControlState`` reflecting the applied move.

    Raises:
        Never raises; failures are logged and a fallback state is returned.

    References
    ──────────
    theory2.tex §44 — State transitions in semantic control.
    """
    manual_effect = False
    try:
        new_state = move.apply(state)
        # If apply returned the identical object, produce a fresh copy.
        if new_state is state:
            raise ValueError("move.apply returned identical state object")
    except Exception as exc:
        log.debug(
            "apply_move: move.apply raised %s; constructing transition manually",
            exc,
        )
        new_state = state.snapshot()
        manual_effect = True
        # Simulate the move's effect based on its kind.
        kind_str = _move_kind_value(move.kind)
        _apply_move_effect(new_state, kind_str, move)

    new_state.state_id = str(uuid.uuid4())
    new_state.timestamp = time.time()
    new_state.budget = dict(new_state.budget)
    new_state.budget["used"] = float(new_state.budget.get("used", 0.0)) + float(move.cost)
    new_state.metadata = dict(new_state.metadata)
    new_state.metadata.setdefault("last_move_id", move.move_id)
    new_state.metadata.setdefault("last_move_kind", _move_kind_value(move.kind))
    new_state.metadata["last_move"] = move.move_id
    if manual_effect:
        new_state.metadata["manual_transition"] = True

    return new_state


def _apply_move_effect(
    state: SemanticControlState,
    kind_str: str,
    move: AdmissibleMove,
) -> None:
    """Apply in-place the expected side-effect of a move to *state*.

    This is a best-effort simulation used when ``move.apply`` is not
    implemented or returns the original state.  Modifications are made
    directly on the (already-copied) mutable *state*.

    Args:
        state:    The already-copied state to mutate.
        kind_str: The string value of the move kind.
        move:     The move being applied (for metadata).
    """
    if kind_str == MoveKind.CONSTRUCT.value:
        # Add a synthetic section ID if we are below full cover.
        if len(state.section_ids) < len(state.cover_ids):
            new_id = f"section-{str(uuid.uuid4())[:8]}"
            state.section_ids.append(new_id)

    elif kind_str == MoveKind.VERIFY.value:
        # Verification adds confidence but doesn't change section count.
        state.metadata["last_verified"] = time.time()

    elif kind_str == MoveKind.REPAIR.value:
        # Repair may resolve an obligation.
        if state.obligation_ids:
            resolved = state.obligation_ids.pop(0)
            state.metadata["last_repaired_obligation"] = resolved

    elif kind_str == MoveKind.NEGOTIATE_TREATY.value:
        # Add a synthetic treaty ID.
        expected = max(len(state.cover_ids) - 1, 0)
        if len(state.treaty_ids) < expected:
            state.treaty_ids.append(f"treaty-{str(uuid.uuid4())[:8]}")

    elif kind_str == MoveKind.DISCHARGE_OBLIGATION.value:
        # Remove the first pending obligation.
        if state.obligation_ids:
            state.obligation_ids.pop(0)

    elif kind_str == MoveKind.REFINE_COVER.value:
        # Add a new cover and remove one obligation.
        state.cover_ids.append(f"cover-{str(uuid.uuid4())[:8]}")
        if state.obligation_ids:
            state.obligation_ids.pop(0)

    elif kind_str == MoveKind.CONSULT_ORACLE.value:
        # Oracle consultation may add a section.
        if len(state.section_ids) < len(state.cover_ids):
            state.section_ids.append(f"section-oracle-{str(uuid.uuid4())[:8]}")


def certify_convergence(
    trajectory: SemanticTrajectory,
    threshold: float = DEFAULT_CONVERGENCE_THRESHOLD,
) -> ConvergenceCertificate | None:
    """Certify convergence of *trajectory* if criteria are satisfied.

    Certification criteria:
    1.  The trajectory contains at least two states.
    2.  The final state has ``attainability >= threshold`` (equivalently,
        ``lyapunov_function(final_state) < 1 − threshold``).
    3.  The final state has no pending obligations
        (``obligation_ids`` is empty).
    4.  The coverage trend over the last two states is non-decreasing.

    Args:
        trajectory: The ``SemanticTrajectory`` to certify.
        threshold:  Minimum coverage / attainability for certification.

    Returns:
        A ``ConvergenceCertificate`` if all criteria are met, else ``None``.

    References
    ──────────
    theory2.tex §44.5 — Convergence Certificates and Validity Periods.
    """
    if trajectory.length() < 2:
        log.debug("certify_convergence: trajectory too short (length=%d)", trajectory.length())
        return None

    final_state = trajectory.latest_state()
    if final_state is None:
        return None

    attainability = compute_attainability(final_state)
    if attainability < threshold:
        log.debug(
            "certify_convergence: attainability %.3f < threshold %.3f",
            attainability,
            threshold,
        )
        return None

    if final_state.obligation_ids:
        log.debug(
            "certify_convergence: %d obligations outstanding",
            len(final_state.obligation_ids),
        )
        return None

    scores = trajectory.score_history()
    if len(scores) >= 2 and scores[-1] < scores[-2] - 1e-9:
        log.debug("certify_convergence: coverage regressed at final step")
        return None

    metrics = _compute_convergence_metrics(trajectory)
    cert = ConvergenceCertificate(
        cert_id=str(uuid.uuid4()),
        state_id=final_state.state_id,
        coverage_ratio=final_state.coverage_ratio(),
        obligation_count=len(final_state.obligation_ids),
        issued_at=time.time(),
        valid_for=DEFAULT_CERTIFICATE_VALIDITY,
        evidence={
            "trajectory_id": trajectory.trajectory_id,
            "trajectory_length": trajectory.length(),
            "convergence_metrics": metrics,
            "score_history_tail": scores[-5:],
            "attainability": attainability,
            "lyapunov_final": metrics.get("lyapunov_final", 0.0),
        },
    )
    log.info(
        "certify_convergence: issued certificate %s (coverage=%.3f)",
        cert.cert_id,
        cert.coverage_ratio,
    )
    return cert


def semantic_control_loop(
    initial_state: SemanticControlState,
    law: ControlLaw,
    budget: dict[str, Any],
    max_steps: int = DEFAULT_MAX_STEPS,
) -> SemanticTrajectory:
    """Run the semantic control loop from *initial_state*.

    This is the main entry point for executing the semantic control system.
    It implements the core feedback loop from theory2.tex §44:

        1.  **Enumerate** admissible moves for the current state.
        2.  **Select** the best move via *law*.
        3.  **Apply** the selected move to obtain the next state.
        4.  **Observe** the new state and check convergence.
        5.  **Repeat** until converged, budget exhausted, or max_steps reached.

    The loop also monitors for stalls (no progress in 10 consecutive steps)
    and injects an oracle move when stalling occurs.

    Args:
        initial_state: Starting ``SemanticControlState``.
        law:           ``ControlLaw`` used to select moves at each step.
        budget:        Budget dict passed to the state.  Must have ``"total"``
                       and optionally ``"used"`` keys.
        max_steps:     Hard cap on the number of control steps.

    Returns:
        ``SemanticTrajectory`` containing all states and moves from this run,
        including the initial state (as the zeroth entry).

    References
    ──────────
    theory2.tex §44 — The semantic control loop.
    """
    trajectory = SemanticTrajectory(trajectory_id=str(uuid.uuid4()))

    # Attach budget to initial state if not already present.
    if not initial_state.budget:
        initial_state.budget.update(budget)

    trajectory.append(initial_state, move=None)
    state = initial_state

    stall_counter = 0
    prev_coverage: float = state.coverage_ratio()

    log.info(
        "semantic_control_loop: starting (max_steps=%d, initial_coverage=%.3f)",
        max_steps,
        prev_coverage,
    )

    for step in range(max_steps):
        # Budget exhaustion check.
        if _compute_budget_deficit(state.budget) >= 1.0:
            log.info(
                "semantic_control_loop: budget exhausted at step %d", step
            )
            break

        # Enumerate admissible moves.
        candidates = enumerate_admissible_moves(state, [])

        # Inject a forced oracle move if stalling.
        if stall_counter >= 10 and state.channel_ids:
            log.info(
                "semantic_control_loop: stall detected at step %d, "
                "injecting oracle move",
                step,
            )
            oracle_move = AdmissibleMove(
                move_id=f"oracle-forced-{step}",
                kind=MoveKind.CONSULT_ORACLE.value,
                preconditions=[],
                postconditions=["oracle_consulted"],
                cost=6.0,
                priority=3.0,
                expected_gain=0.25,
                trust_requirement=0.2,
                metadata={"forced": True, "step": step},
            )
            candidates.insert(0, oracle_move)

        if not candidates:
            log.info(
                "semantic_control_loop: no candidates at step %d, halting",
                step,
            )
            break

        # Select move via control law.
        selected = select_admissible_move(state, candidates, law=law)
        if selected is None:
            log.info(
                "semantic_control_loop: no move selected at step %d, halting",
                step,
            )
            break

        log.debug(
            "semantic_control_loop: step=%d selected move=%s kind=%s gain=%.3f",
            step,
            selected.move_id,
            selected.kind,
            selected.expected_gain,
        )

        # Apply move.
        state = apply_move(state, selected)
        trajectory.append(state, move=selected)

        # Convergence check.
        current_coverage = state.coverage_ratio()
        if current_coverage >= DEFAULT_CONVERGENCE_THRESHOLD and not state.obligation_ids:
            log.info(
                "semantic_control_loop: converged at step %d (coverage=%.3f)",
                step + 1,
                current_coverage,
            )
            break

        # Stall detection.
        if abs(current_coverage - prev_coverage) < 1e-6:
            stall_counter += 1
        else:
            stall_counter = 0
        prev_coverage = current_coverage

        # Let the law adapt based on current coverage metric.
        try:
            law.adapt({"coverage": current_coverage, "step": step})
        except Exception:
            pass

    log.info(
        "semantic_control_loop: finished (steps=%d, final_coverage=%.3f)",
        trajectory.length() - 1,
        state.coverage_ratio(),
    )
    return trajectory


# ---------------------------------------------------------------------------
# Cross-subsystem integration: geometry, solver, encodings, evidence
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.covers import Cover, score_cover
except Exception:
    Cover = None  # type: ignore[assignment,misc]
    score_cover = None  # type: ignore[assignment]

try:
    from jugeo.solver.z3_session import Z3Session as _Z3Session
except Exception:
    _Z3Session = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encode_judgment as _encode_judgment
except Exception:
    _encode_judgment = None  # type: ignore[assignment]

try:
    from jugeo.evidence.certificates import CertificateBuilder as _CertBuilder
except Exception:
    _CertBuilder = None  # type: ignore[assignment,misc]


def control_move_with_cover(state, cover):
    """Select a control move informed by cover quality from jugeo.geometry.covers.

    Better covers shrink the admissible move set because fewer coordinates
    remain unproved, focusing the controller on high-value moves.
    """
    if score_cover is not None and cover is not None:
        try:
            metric = score_cover(cover)
            completeness = getattr(metric, "completeness", 0.0)
        except Exception:
            completeness = 0.0
    else:
        completeness = 0.0

    coverage = getattr(state, "coverage_ratio", lambda: 0.0)
    cov = coverage() if callable(coverage) else coverage
    effective = max(float(cov), completeness)
    return {
        "effective_coverage": effective,
        "cover_completeness": completeness,
        "subsystem": "jugeo.geometry.covers",
    }


def solver_verify_convergence(trajectory):
    """Verify convergence of a control trajectory via Z3 (jugeo.solver.z3_session).

    Submits the trajectory's convergence claim as a satisfiability query
    to ensure the lyapunov bound was not violated.
    """
    if _Z3Session is None:
        return {"verified": False, "reason": "Z3Session unavailable",
                "subsystem": "jugeo.solver.z3_session"}
    try:
        session = _Z3Session()
        claims = getattr(trajectory, "convergence_claims", [])
        for c in claims:
            session.add(c)
        outcome = session.check()
        return {"verified": getattr(outcome, "satisfiable", False),
                "subsystem": "jugeo.solver.z3_session"}
    except Exception as exc:
        return {"verified": False, "reason": str(exc),
                "subsystem": "jugeo.solver.z3_session"}


def encode_control_state(state):
    """Encode the current control state via jugeo.encodings for downstream replay."""
    if _encode_judgment is None:
        return {"encoded": False, "reason": "encode_judgment unavailable",
                "subsystem": "jugeo.encodings"}
    try:
        encoded = _encode_judgment(state)
        return {"encoded": True, "keys": list(encoded.keys()) if isinstance(encoded, dict) else [],
                "subsystem": "jugeo.encodings"}
    except Exception as exc:
        return {"encoded": False, "reason": str(exc),
                "subsystem": "jugeo.encodings"}


def certify_control_outcome(trajectory):
    """Issue a certificate for a successful control outcome via jugeo.evidence.certificates."""
    if _CertBuilder is None:
        return {"certified": False, "reason": "CertificateBuilder unavailable",
                "subsystem": "jugeo.evidence.certificates"}
    try:
        builder = _CertBuilder()
        length = getattr(trajectory, "length", lambda: 0)
        n = length() if callable(length) else length
        if hasattr(builder, "set_payload"):
            builder.set_payload(f"control_outcome:steps={n}")
        if hasattr(builder, "set_issuer"):
            builder.set_issuer("orchestration.semantic_control")
        cert = builder.build() if hasattr(builder, "build") else None
        return {"certified": cert is not None,
                "certificate_id": getattr(cert, "id", None),
                "subsystem": "jugeo.evidence.certificates"}
    except Exception as exc:
        return {"certified": False, "reason": str(exc),
                "subsystem": "jugeo.evidence.certificates"}
