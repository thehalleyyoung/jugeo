"""Integration layer connecting semantic control to JuGeo orchestration subsystems (theory2.tex Ch44).

This module wires the semantic-control plane described in theory2.tex Chapter 44
("Semantic Control of Project-Scale Orchestration") to the five core JuGeo
subsystems:

    1.  **Trust** (jugeo.evidence.trust) – every move must satisfy a trust
        requirement before it is dispatched; trust levels are composed and
        audited via :class:`ControlTrustIntegrator`.

    2.  **Descent / Gluing** (jugeo.geometry.descent) – state transitions are
        validated as local sections that must be globally consistent; the
        :class:`ControlDescentConnector` runs the descent engine and surfaces
        obstructions.

    3.  **Fleet** (jugeo.orchestration.fleet) – admissible moves are offered to
        fleet members via competitive bidding; :class:`ControlFleetBridge`
        manages the bid lifecycle and assignment history.

    4.  **Frontier** (jugeo.orchestration.frontier) – obligations and cover
        items are reflected into the frontier's priority queue and produce
        backpressure signals consumed by the step loop;
        :class:`ControlFrontierAdapter` owns this conversion.

    5.  **Orchestrator** (jugeo.orchestration.controller) – the top-level
        :class:`SemanticControlOrchestrator` delegates to all four integrators
        in each control step and records the resulting
        :class:`SemanticTrajectory`.

Theory reference
────────────────
*   theory2.tex §44.1  – Semantic control state as a functor on the site
*   theory2.tex §44.2  – Admissibility, preconditions, and postconditions
*   theory2.tex §44.3  – Convergence: Lyapunov functions and coverage monotonicity
*   theory2.tex §44.4  – Trust integration and the evidence channel hierarchy
*   theory2.tex §44.5  – Descent validation of state transitions
*   theory2.tex §44.6  – Fleet-competitive search for move selection
*   theory2.tex §44.7  – Frontier backpressure and obligation scheduling

Design notes
────────────
*   All external imports are guarded with ``try/except ImportError`` so that
    this module degrades gracefully if upstream packages are not yet compiled.
*   Mutable classes use ``@dataclass(slots=True)``; frozen value objects use
    ``@dataclass(frozen=True, slots=True)``.
*   IDs are generated with ``uuid.uuid4()``; timestamps with ``time.time()``.
*   The module exposes a ``DEFAULT_ORCHESTRATOR_CONFIG`` constant and a
    factory function :func:`build_semantic_control_orchestrator` for quick
    construction in scripts and tests.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local model imports (from models.py in the same package)
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.semantic_control.models import (
        AdmissibleMove,
        ControlLaw,
        ConvergenceCertificate,
        SemanticControlState,
        SemanticTrajectory,
    )
except ImportError:  # pragma: no cover – models not yet compiled
    logger.warning("semantic_control.models not available; using stubs")

    @dataclass(slots=True)  # type: ignore[misc]
    class SemanticControlState:  # type: ignore[no-redef]
        state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        cover_ids: list = field(default_factory=list)
        context_ids: list = field(default_factory=list)
        section_ids: list = field(default_factory=list)
        treaty_ids: list = field(default_factory=list)
        obligation_ids: list = field(default_factory=list)
        channel_ids: list = field(default_factory=list)
        budget: float = 1.0
        timestamp: float = field(default_factory=time.time)
        metadata: dict = field(default_factory=dict)

        def is_admissible(self) -> bool:
            return True

        def coverage_ratio(self) -> float:
            return 0.0

        def attainability_score(self) -> float:
            return 0.0

        def delta_from(self, other: Any) -> dict:
            return {}

        def to_dict(self) -> dict:
            return {"state_id": self.state_id}

        def snapshot(self) -> "SemanticControlState":
            return self

        def health_status(self) -> str:
            return "unknown"

    @dataclass(slots=True)  # type: ignore[misc]
    class AdmissibleMove:  # type: ignore[no-redef]
        move_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        kind: str = "noop"
        preconditions: list = field(default_factory=list)
        postconditions: list = field(default_factory=list)
        cost: float = 0.0
        priority: float = 0.0
        expected_gain: float = 0.0
        trust_requirement: str = "LOW"
        metadata: dict = field(default_factory=dict)

        def is_applicable(self, state: Any) -> bool:
            return True

        def apply(self, state: Any) -> Any:
            return state

        def validate(self) -> bool:
            return True

        def to_dict(self) -> dict:
            return {"move_id": self.move_id, "kind": self.kind}

        def net_value(self) -> float:
            return self.expected_gain - self.cost

    @dataclass(slots=True)  # type: ignore[misc]
    class ControlLaw:  # type: ignore[no-redef]
        law_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        name: str = "stub_law"
        kind: str = "greedy"
        parameters: dict = field(default_factory=dict)

        def select_move(self, state: Any, candidates: list) -> Any:
            return candidates[0] if candidates else None

        def evaluate(self, state: Any) -> float:
            return 0.0

        def adapt(self, feedback: dict) -> None:
            pass

        def to_dict(self) -> dict:
            return {"law_id": self.law_id}

    @dataclass(frozen=True)  # type: ignore[misc]
    class ConvergenceCertificate:  # type: ignore[no-redef]
        cert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        state_id: str = ""
        coverage_ratio: float = 0.0
        obligation_count: int = 0
        issued_at: float = field(default_factory=time.time)
        valid_for: float = 3600.0
        evidence: dict = field(default_factory=dict)

        def is_valid(self) -> bool:
            return True

        def is_expired(self) -> bool:
            return False

        def summary(self) -> str:
            return f"cert:{self.cert_id[:8]}"

        def to_dict(self) -> dict:
            return {"cert_id": self.cert_id}

    @dataclass(slots=True)  # type: ignore[misc]
    class SemanticTrajectory:  # type: ignore[no-redef]
        trajectory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        states: list = field(default_factory=list)
        moves: list = field(default_factory=list)
        timestamps: list = field(default_factory=list)

        def append(self, state: Any, move: Any | None = None) -> None:
            self.states.append(state)
            self.moves.append(move)
            self.timestamps.append(time.time())

        def length(self) -> int:
            return len(self.states)

        def is_converging(self) -> bool:
            return False

        def export(self) -> dict:
            return {"trajectory_id": self.trajectory_id, "length": self.length()}

        def replay(self) -> list:
            return list(zip(self.states, self.moves))

        def latest_state(self) -> Any:
            return self.states[-1] if self.states else None

        def score_history(self) -> list:
            return []


# ---------------------------------------------------------------------------
# Orchestrator imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import (
        ControlLaw as BaseControlLaw,
        ConvergenceMonitor,
        GreedyControl,
        MoveHistory,
        MoveKind,
        Orchestrator,
        OrchestratorConfiguration,
        OrchestratorEventBus,
        OrchestratorState,
        SemanticMove,
    )
except ImportError:  # pragma: no cover
    logger.warning("jugeo.orchestration.controller not available; using stubs")
    BaseControlLaw = object  # type: ignore[assignment,misc]

    class MoveKind:  # type: ignore[no-redef]
        EXPAND = "EXPAND"
        CONTRACT = "CONTRACT"
        NEGOTIATE = "NEGOTIATE"
        REPAIR = "REPAIR"
        CERTIFY = "CERTIFY"
        PRIORITIZE = "PRIORITIZE"
        ARCHIVE = "ARCHIVE"
        ESCALATE = "ESCALATE"

    class OrchestratorState:  # type: ignore[no-redef]
        pass

    class SemanticMove:  # type: ignore[no-redef]
        pass

    class GreedyControl:  # type: ignore[no-redef]
        pass

    class Orchestrator:  # type: ignore[no-redef]
        def step(self, *a: Any, **kw: Any) -> Any:
            return None

        def run(self, *a: Any, **kw: Any) -> Any:
            return None

    class ConvergenceMonitor:  # type: ignore[no-redef]
        pass

    class MoveHistory:  # type: ignore[no-redef]
        pass

    class OrchestratorConfiguration:  # type: ignore[no-redef]
        pass

    class OrchestratorEventBus:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Fleet imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet import (
        BidOutcome,
        CompetitiveSearch,
        Fleet,
        FleetBid,
        FleetMember,
        FleetState,
    )
except ImportError:  # pragma: no cover
    logger.warning("jugeo.orchestration.fleet not available; using stubs")

    class FleetMember:  # type: ignore[no-redef]
        member_id: str = ""
        capabilities: list = []

    class FleetBid:  # type: ignore[no-redef]
        bid_id: str = ""
        member_id: str = ""
        score: float = 0.0

    class Fleet:  # type: ignore[no-redef]
        def solicit_bids(self, *a: Any, **kw: Any) -> list:
            return []

        def assign(self, *a: Any, **kw: Any) -> None:
            pass

        def member_count(self) -> int:
            return 0

    class CompetitiveSearch:  # type: ignore[no-redef]
        def run(self, *a: Any, **kw: Any) -> Any:
            return None

    class FleetState:  # type: ignore[no-redef]
        pass

    class BidOutcome:  # type: ignore[no-redef]
        SUCCESS = "SUCCESS"
        FAILURE = "FAILURE"
        REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Frontier imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.frontier import (
        Frontier,
        FrontierItem,
        FrontierNode,
        FrontierSearch,
        FrontierState,
    )
except ImportError:  # pragma: no cover
    logger.warning("jugeo.orchestration.frontier not available; using stubs")

    class FrontierItem:  # type: ignore[no-redef]
        pass

    class FrontierState:  # type: ignore[no-redef]
        pass

    class FrontierNode:  # type: ignore[no-redef]
        pass

    class Frontier:  # type: ignore[no-redef]
        def push(self, *a: Any, **kw: Any) -> None:
            pass

        def pop(self) -> Any:
            return None

        def size(self) -> int:
            return 0

    class FrontierSearch:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Negotiation imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.negotiation import (
        Negotiator,
        NegotiationSession,
        TreatyProposal,
    )
except ImportError:  # pragma: no cover
    logger.warning("jugeo.orchestration.negotiation not available; using stubs")

    class NegotiationSession:  # type: ignore[no-redef]
        pass

    class TreatyProposal:  # type: ignore[no-redef]
        pass

    class Negotiator:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Trust imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import (
        TrustAlgebra,
        TrustAuditLog,
        TrustAttenuation,
        TrustCeiling,
        TrustComposition,
        TrustLevel,
        TrustPolicy,
        TrustProfile,
        TrustPromotion,
        TrustTier,
        join_trust_profiles,
    )
except ImportError:  # pragma: no cover
    logger.warning("jugeo.evidence.trust not available; using stubs")

    class TrustLevel:  # type: ignore[no-redef]
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        VERIFIED = "VERIFIED"

    class TrustAlgebra:  # type: ignore[no-redef]
        def meet(self, a: str, b: str) -> str:
            return a

        def join(self, a: str, b: str) -> str:
            return b

        def compose(self, levels: list) -> str:
            return levels[0] if levels else "LOW"

    class TrustAuditLog:  # type: ignore[no-redef]
        def record(self, *a: Any, **kw: Any) -> None:
            pass

        def entries(self) -> list:
            return []

    class TrustAttenuation:  # type: ignore[no-redef]
        pass

    class TrustCeiling:  # type: ignore[no-redef]
        pass

    class TrustComposition:  # type: ignore[no-redef]
        pass

    class TrustPolicy:  # type: ignore[no-redef]
        def allows(self, *a: Any, **kw: Any) -> bool:
            return True

    class TrustProfile:  # type: ignore[no-redef]
        trust_level: str = "LOW"

    class TrustPromotion:  # type: ignore[no-redef]
        pass

    class TrustTier:  # type: ignore[no-redef]
        pass

    def join_trust_profiles(*profiles: Any) -> Any:  # type: ignore[no-redef]
        return profiles[0] if profiles else None


# ---------------------------------------------------------------------------
# Descent imports
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.descent import (
        CohomologyClass,
        DescentEngine,
        DescentLog,
        DescentObstruction,
        DescentResult,
        DescentStrategy,
        GlobalSection,
        GluingData,
        LocalSection,
        Obstruction,
        OverlapCondition,
        OverlapStatus,
        RepairFrontier,
    )
except ImportError:  # pragma: no cover
    logger.warning("jugeo.geometry.descent not available; using stubs")

    class LocalSection:  # type: ignore[no-redef]
        pass

    class OverlapCondition:  # type: ignore[no-redef]
        pass

    class GluingData:  # type: ignore[no-redef]
        pass

    class DescentEngine:  # type: ignore[no-redef]
        def run(self, *a: Any, **kw: Any) -> Any:
            return None

        def check_obstructions(self, *a: Any, **kw: Any) -> list:
            return []

    class DescentResult:  # type: ignore[no-redef]
        success: bool = True
        obstructions: list = []

    class GlobalSection:  # type: ignore[no-redef]
        pass

    class DescentObstruction:  # type: ignore[no-redef]
        pass

    class DescentLog:  # type: ignore[no-redef]
        pass

    class OverlapStatus:  # type: ignore[no-redef]
        COMPATIBLE = "COMPATIBLE"
        INCOMPATIBLE = "INCOMPATIBLE"
        UNKNOWN = "UNKNOWN"

    class DescentStrategy:  # type: ignore[no-redef]
        GREEDY = "GREEDY"
        EXHAUSTIVE = "EXHAUSTIVE"

    class CohomologyClass:  # type: ignore[no-redef]
        pass

    class RepairFrontier:  # type: ignore[no-redef]
        pass

    class Obstruction:  # type: ignore[no-redef]
        obstruction_id: str = ""
        severity: float = 0.0
        description: str = ""


# ---------------------------------------------------------------------------
# State management import (state_management)
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.semantic_control.state_management import StateManager  # type: ignore[import]
except ImportError:  # pragma: no cover
    logger.warning("state_management not available; using stub StateManager")

    @dataclass(slots=True)  # type: ignore[misc]
    class StateManager:  # type: ignore[no-redef]
        """Stub state manager used when state_management is unavailable."""

        _history: list = field(default_factory=list)

        def push(self, state: Any) -> None:
            self._history.append(state)

        def latest(self) -> Any:
            return self._history[-1] if self._history else None

        def history(self) -> list:
            return list(self._history)

        def reset(self) -> None:
            self._history.clear()

        def checkpoint(self) -> dict:
            return {"depth": len(self._history)}


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default backpressure thresholds (theory2.tex §44.7).
BACKPRESSURE_THROTTLE_RATIO: float = 0.75
BACKPRESSURE_BLOCK_RATIO: float = 0.95

#: Minimum trust level names for move dispatch (theory2.tex §44.4).
TRUST_LEVELS_ORDERED: list[str] = ["LOW", "MEDIUM", "HIGH", "VERIFIED"]

#: Maximum number of fleet bids to evaluate per step.
MAX_FLEET_BIDS: int = 16

#: Default obstruction tolerance for descent validation.
DEFAULT_OBSTRUCTION_TOLERANCE: float = 0.1

#: Version tag for integration compatibility.
INTEGRATION_VERSION: str = "1.0.0"


# ---------------------------------------------------------------------------
# ControlTrustIntegrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ControlTrustIntegrator:
    """Integrates semantic-control state with the trust algebra (theory2.tex §44.4).

    Every :class:`AdmissibleMove` carries a ``trust_requirement`` field naming
    the minimum :class:`TrustLevel` that a move's evidence channel must satisfy
    before the move may be dispatched.  This class:

    *   Translates ``trust_requirement`` strings to canonical ``TrustLevel``
        names via :meth:`trust_for_move`.
    *   Validates that the *current* trust profile embedded in
        :class:`SemanticControlState` metadata meets that requirement via
        :meth:`validate_trust`.
    *   Elevates state metadata when a higher trust profile is presented via
        :meth:`elevate_state_trust`.
    *   Composes multiple trust levels (e.g., from concurrent channels) via
        :meth:`compose_trust_levels`.
    *   Audits every move dispatch via :meth:`audit_move`.

    The ``_move_trust_cache`` avoids redundant trust lookups for repeated move
    kinds within a single trajectory step.

    Theory reference: theory2.tex §44.4 "Trust Integration and Evidence
    Channel Hierarchy."
    """

    trust_algebra: TrustAlgebra | Any
    trust_policy: TrustPolicy | Any
    audit_log: TrustAuditLog | Any
    _move_trust_cache: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trust_for_move(self, move: AdmissibleMove) -> str:
        """Return the canonical TrustLevel name for *move*'s trust requirement.

        Results are memoised in ``_move_trust_cache`` keyed by the move's
        ``trust_requirement`` string so that repeated lookups within a step
        are O(1).

        Args:
            move: The admissible move whose trust requirement is to be resolved.

        Returns:
            A string naming a TrustLevel (e.g. ``"LOW"``, ``"MEDIUM"``,
            ``"HIGH"``, ``"VERIFIED"``).  Defaults to ``"LOW"`` if the
            requirement is not recognised.
        """
        req: str = str(getattr(move, "trust_requirement", "LOW")).upper()
        if req in self._move_trust_cache:
            return self._move_trust_cache[req]
        canonical = req if req in TRUST_LEVELS_ORDERED else "LOW"
        self._move_trust_cache[req] = canonical
        return canonical

    def validate_trust(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> bool:
        """Return True iff the state's trust profile satisfies *move*'s requirement.

        The state's current trust level is read from
        ``state.metadata.get("trust_level", "LOW")``.  The comparison is
        ordinal: each level in ``TRUST_LEVELS_ORDERED`` is strictly higher than
        the previous one.

        If ``trust_policy`` exposes an ``allows`` method it is consulted as a
        secondary gate; otherwise the ordinal comparison alone is used.

        Args:
            move:  The move whose trust requirement must be met.
            state: The current semantic-control state.

        Returns:
            True if the state's trust is sufficient, False otherwise.
        """
        required = self.trust_for_move(move)
        current = str(
            getattr(state, "metadata", {}).get("trust_level", "LOW")
        ).upper()

        required_idx = TRUST_LEVELS_ORDERED.index(required) if required in TRUST_LEVELS_ORDERED else 0
        current_idx = TRUST_LEVELS_ORDERED.index(current) if current in TRUST_LEVELS_ORDERED else 0

        ordinal_ok = current_idx >= required_idx

        # Secondary policy gate
        policy_ok = True
        if hasattr(self.trust_policy, "allows"):
            try:
                policy_ok = bool(self.trust_policy.allows(current, required))
            except Exception:  # pragma: no cover
                logger.debug("trust_policy.allows raised; ignoring", exc_info=True)

        result = ordinal_ok and policy_ok
        logger.debug(
            "validate_trust move=%s required=%s current=%s ok=%s",
            getattr(move, "move_id", "?")[:8],
            required,
            current,
            result,
        )
        return result

    def elevate_state_trust(
        self,
        state: SemanticControlState,
        trust_profile: Any,
    ) -> SemanticControlState:
        """Return a copy of *state* whose metadata reflects an elevated trust level.

        The new trust level is taken from ``trust_profile.trust_level`` (or its
        ``name`` attribute if it is an enum value).  The returned state's
        metadata is a shallow copy of the original with ``trust_level`` and
        ``trust_elevated_at`` updated.

        Args:
            state:         The state to elevate.
            trust_profile: An object carrying a ``trust_level`` attribute
                           (e.g., a :class:`TrustProfile` instance).

        Returns:
            A new :class:`SemanticControlState` with updated metadata.
        """
        raw_level = getattr(trust_profile, "trust_level", "LOW")
        level_str = getattr(raw_level, "name", str(raw_level)).upper()

        old_meta: dict = dict(getattr(state, "metadata", {}) or {})
        old_meta["trust_level"] = level_str
        old_meta["trust_elevated_at"] = time.time()

        snapshot = state.snapshot() if hasattr(state, "snapshot") else state
        try:
            object.__setattr__(snapshot, "metadata", old_meta)
        except (AttributeError, TypeError):
            pass  # frozen or slot-guarded; best effort

        logger.debug("elevate_state_trust -> %s on state %s", level_str, getattr(state, "state_id", "?")[:8])
        return snapshot

    def compose_trust_levels(self, levels: list[str]) -> str:
        """Compose multiple trust-level strings into a single resultant level.

        Delegates to ``trust_algebra.compose`` when available; otherwise falls
        back to the ordinal minimum of *levels* (the most conservative choice,
        consistent with theory2.tex §44.4 which requires that composed
        evidence can be no stronger than its weakest constituent).

        Args:
            levels: List of trust-level name strings.

        Returns:
            A single trust-level name string.
        """
        if not levels:
            return "LOW"
        if hasattr(self.trust_algebra, "compose"):
            try:
                return str(self.trust_algebra.compose(levels))
            except Exception:  # pragma: no cover
                logger.debug("trust_algebra.compose raised; falling back", exc_info=True)
        # Ordinal minimum fallback
        idxs = [TRUST_LEVELS_ORDERED.index(l) if l in TRUST_LEVELS_ORDERED else 0 for l in levels]
        return TRUST_LEVELS_ORDERED[min(idxs)]

    def audit_move(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
        result: bool,
    ) -> None:
        """Record a move-dispatch event in the audit log.

        Constructs an audit entry dict and delegates to
        ``audit_log.record`` if available.

        Args:
            move:   The move that was (or was not) dispatched.
            state:  The state at dispatch time.
            result: True if the move was successfully dispatched.
        """
        entry = {
            "event": "move_dispatch",
            "move_id": getattr(move, "move_id", "?"),
            "move_kind": str(getattr(move, "kind", "?")),
            "state_id": getattr(state, "state_id", "?"),
            "trust_required": self.trust_for_move(move),
            "trust_current": str(
                getattr(state, "metadata", {}).get("trust_level", "LOW")
            ),
            "result": result,
            "timestamp": time.time(),
        }
        if hasattr(self.audit_log, "record"):
            try:
                self.audit_log.record(**entry)
            except Exception:  # pragma: no cover
                logger.debug("audit_log.record raised", exc_info=True)
        logger.debug("audit_move %s result=%s", entry["move_id"][:8], result)

    def status(self) -> dict:
        """Return a summary dict describing the integrator's current state.

        Returns:
            Dict with keys: ``version``, ``cache_size``,
            ``has_trust_algebra``, ``has_trust_policy``, ``has_audit_log``,
            ``audit_entry_count``.
        """
        audit_count = 0
        if hasattr(self.audit_log, "entries"):
            try:
                audit_count = len(self.audit_log.entries())
            except Exception:  # pragma: no cover
                pass
        return {
            "version": INTEGRATION_VERSION,
            "cache_size": len(self._move_trust_cache),
            "has_trust_algebra": not isinstance(self.trust_algebra, type),
            "has_trust_policy": not isinstance(self.trust_policy, type),
            "has_audit_log": not isinstance(self.audit_log, type),
            "audit_entry_count": audit_count,
        }


# ---------------------------------------------------------------------------
# ControlDescentConnector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ControlDescentConnector:
    """Uses the descent engine to validate semantic-control state transitions (theory2.tex §44.5).

    In JuGeo's geometric framework (theory2.tex §3), a state transition
    corresponds to a local section that must agree on overlaps with all
    previously assembled sections.  The :class:`DescentEngine` checks this
    compatibility and reports obstructions when sections disagree.

    This class:

    *   Validates that a proposed ``to_state`` is geometrically consistent with
        ``from_state`` via :meth:`validate_transition`.
    *   Computes a full :class:`DescentResult` for a state via
        :meth:`compute_descent_result`.
    *   Enumerates :class:`Obstruction` objects blocking progress via
        :meth:`check_obstructions`.
    *   Attempts to repair a state by resolving its obstructions via
        :meth:`repair_frontier`.
    *   Assembles a :class:`GlobalSection` from a complete trajectory via
        :meth:`assemble_global_section`.

    Theory reference: theory2.tex §44.5 "Descent Validation of State
    Transitions."
    """

    descent_engine: DescentEngine | Any
    gluing_data: GluingData | Any
    obstruction_tolerance: float = DEFAULT_OBSTRUCTION_TOLERANCE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_transition(
        self,
        from_state: SemanticControlState,
        to_state: SemanticControlState,
    ) -> bool:
        """Return True iff the transition from *from_state* to *to_state* is valid.

        A transition is valid when the two states' section representations agree
        on their overlapping cover elements (theory2.tex §44.5, Definition 44.5).
        The check is delegated to ``descent_engine``; if the engine raises or is
        unavailable, the connector falls back to a lightweight heuristic: the
        transition is valid iff the to-state's coverage ratio is at least as
        large as the from-state's.

        Args:
            from_state: The state being left.
            to_state:   The proposed successor state.

        Returns:
            True if the transition is geometrically consistent.
        """
        if hasattr(self.descent_engine, "validate_transition"):
            try:
                return bool(
                    self.descent_engine.validate_transition(from_state, to_state)
                )
            except Exception:  # pragma: no cover
                logger.debug("descent_engine.validate_transition raised; using heuristic", exc_info=True)

        # Heuristic fallback: non-decreasing coverage
        from_cov = getattr(from_state, "coverage_ratio", lambda: 0.0)
        to_cov = getattr(to_state, "coverage_ratio", lambda: 0.0)
        if callable(from_cov) and callable(to_cov):
            return float(to_cov()) >= float(from_cov()) - self.obstruction_tolerance
        return True

    def compute_descent_result(self, state: SemanticControlState) -> DescentResult | Any:
        """Run the descent engine on *state* and return the result.

        Converts the state's cover IDs into :class:`LocalSection` stubs and
        passes them to the descent engine together with the stored gluing data.

        Args:
            state: The state to analyse.

        Returns:
            A :class:`DescentResult` (or a stub dict on failure).
        """
        if not hasattr(self.descent_engine, "run"):
            return {"success": True, "obstructions": [], "state_id": getattr(state, "state_id", "?")}
        try:
            return self.descent_engine.run(
                cover_ids=getattr(state, "cover_ids", []),
                gluing_data=self.gluing_data,
                metadata=getattr(state, "metadata", {}),
            )
        except Exception:  # pragma: no cover
            logger.debug("descent_engine.run raised", exc_info=True)
            return {"success": False, "obstructions": [], "state_id": getattr(state, "state_id", "?")}

    def check_obstructions(self, state: SemanticControlState) -> list[Any]:
        """Return the list of :class:`Obstruction` objects blocking *state*.

        Delegates to ``descent_engine.check_obstructions`` when available;
        otherwise falls back to an empty list (optimistic: no obstructions
        detected).

        Args:
            state: The state to check.

        Returns:
            List of :class:`Obstruction` objects (may be empty).
        """
        if hasattr(self.descent_engine, "check_obstructions"):
            try:
                result = self.descent_engine.check_obstructions(
                    cover_ids=getattr(state, "cover_ids", []),
                    section_ids=getattr(state, "section_ids", []),
                )
                return list(result) if result is not None else []
            except Exception:  # pragma: no cover
                logger.debug("check_obstructions raised", exc_info=True)
        return []

    def repair_frontier(self, state: SemanticControlState) -> SemanticControlState:
        """Attempt to repair *state* by resolving its obstructions.

        Queries ``check_obstructions`` and, for each obstruction whose severity
        exceeds ``obstruction_tolerance``, attempts a repair via the descent
        engine's ``repair`` method.  Returns either the repaired state or the
        original state when repair is not possible.

        Args:
            state: The state to repair.

        Returns:
            A (possibly repaired) :class:`SemanticControlState`.
        """
        obstructions = self.check_obstructions(state)
        if not obstructions:
            return state

        severe = [
            o for o in obstructions
            if float(getattr(o, "severity", 0.0)) > self.obstruction_tolerance
        ]
        if not severe:
            return state

        if hasattr(self.descent_engine, "repair"):
            try:
                repaired = self.descent_engine.repair(state, severe)
                if repaired is not None:
                    logger.debug(
                        "repair_frontier: repaired %d obstructions on state %s",
                        len(severe),
                        getattr(state, "state_id", "?")[:8],
                    )
                    return repaired
            except Exception:  # pragma: no cover
                logger.debug("descent_engine.repair raised", exc_info=True)

        logger.debug(
            "repair_frontier: could not repair %d obstructions; returning original",
            len(severe),
        )
        return state

    def assemble_global_section(
        self, trajectory: SemanticTrajectory
    ) -> GlobalSection | Any | None:
        """Attempt to assemble a :class:`GlobalSection` from the full trajectory.

        A global section exists when all local sections (one per trajectory
        step) are mutually compatible on their pairwise overlaps.  This method
        delegates to ``descent_engine.assemble``; returns None on failure.

        Args:
            trajectory: The complete semantic trajectory.

        Returns:
            A :class:`GlobalSection` if assembly succeeds, otherwise None.
        """
        if not hasattr(self.descent_engine, "assemble"):
            return None
        states = getattr(trajectory, "states", [])
        if not states:
            return None
        try:
            return self.descent_engine.assemble(
                states=states,
                gluing_data=self.gluing_data,
            )
        except Exception:  # pragma: no cover
            logger.debug("descent_engine.assemble raised", exc_info=True)
            return None

    def status(self) -> dict:
        """Return a summary of the connector's current configuration.

        Returns:
            Dict with keys: ``has_descent_engine``, ``has_gluing_data``,
            ``obstruction_tolerance``.
        """
        return {
            "has_descent_engine": not isinstance(self.descent_engine, type),
            "has_gluing_data": not isinstance(self.gluing_data, type),
            "obstruction_tolerance": self.obstruction_tolerance,
        }


# ---------------------------------------------------------------------------
# ControlFleetBridge
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ControlFleetBridge:
    """Bridges semantic control to fleet management (theory2.tex §44.6).

    Fleet-competitive search (theory2.tex §44.6) treats admissible moves as
    tasks that fleet members bid on.  Each member's bid carries an expected
    semantic gain, a resource cost, and a trust ceiling.  This class manages:

    *   :meth:`assign_move_to_fleet` – dispatch a single move to the best
        available fleet member.
    *   :meth:`collect_bids` – solicit bids from the fleet for a list of
        candidate moves.
    *   :meth:`evaluate_bids` – select the best move based on collected bids.
    *   :meth:`register_member_capabilities` – declare which move kinds a
        member can handle.
    *   :meth:`update_fleet_from_trajectory` – feed trajectory history back
        to the fleet for calibration.

    Theory reference: theory2.tex §44.6 "Fleet-Competitive Search for Move
    Selection."
    """

    fleet: Fleet | Any
    competitive_search: CompetitiveSearch | Any
    _assignment_history: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assign_move_to_fleet(
        self,
        move: AdmissibleMove,
        state: SemanticControlState,
    ) -> str | None:
        """Assign *move* to the most suitable fleet member; return its member_id.

        Delegates to ``fleet.assign`` when available.  The assignment is
        recorded in ``_assignment_history`` for trajectory calibration.

        Args:
            move:  The admissible move to dispatch.
            state: The state at dispatch time (used for contextual routing).

        Returns:
            The assigned member's ID string, or None if no member is available.
        """
        member_id: str | None = None
        if hasattr(self.fleet, "assign"):
            try:
                result = self.fleet.assign(move=move, state=state)
                member_id = str(result) if result is not None else None
            except Exception:  # pragma: no cover
                logger.debug("fleet.assign raised", exc_info=True)

        record: dict = {
            "move_id": getattr(move, "move_id", "?"),
            "state_id": getattr(state, "state_id", "?"),
            "member_id": member_id,
            "assigned_at": time.time(),
        }
        self._assignment_history.append(record)
        return member_id

    def collect_bids(
        self,
        state: SemanticControlState,
        candidates: list[AdmissibleMove],
    ) -> list[Any]:
        """Solicit bids from the fleet for *candidates* given *state*.

        Delegates to ``fleet.solicit_bids``.  Returns at most
        ``MAX_FLEET_BIDS`` bids sorted by descending score.

        Args:
            state:      The current semantic-control state.
            candidates: List of candidate moves to bid on.

        Returns:
            List of :class:`FleetBid` objects (may be empty).
        """
        if not hasattr(self.fleet, "solicit_bids"):
            return []
        try:
            bids = self.fleet.solicit_bids(
                state=state,
                candidates=candidates,
                limit=MAX_FLEET_BIDS,
            )
            bids = list(bids) if bids is not None else []
            bids.sort(key=lambda b: float(getattr(b, "score", 0.0)), reverse=True)
            return bids[:MAX_FLEET_BIDS]
        except Exception:  # pragma: no cover
            logger.debug("fleet.solicit_bids raised", exc_info=True)
            return []

    def evaluate_bids(
        self,
        bids: list[Any],
        state: SemanticControlState,
    ) -> AdmissibleMove | None:
        """Select the best :class:`AdmissibleMove` from *bids*.

        Uses :class:`CompetitiveSearch` when available; otherwise picks the
        highest-scored bid's move directly.

        Args:
            bids:  List of :class:`FleetBid` objects.
            state: The current state for context.

        Returns:
            The selected :class:`AdmissibleMove`, or None if *bids* is empty.
        """
        if not bids:
            return None
        if hasattr(self.competitive_search, "select"):
            try:
                return self.competitive_search.select(bids=bids, state=state)
            except Exception:  # pragma: no cover
                logger.debug("competitive_search.select raised", exc_info=True)
        # Fallback: pick highest-score bid's move
        best = bids[0]
        return getattr(best, "move", None)

    def register_member_capabilities(
        self,
        member_id: str,
        capabilities: list[MoveKind | str],
    ) -> None:
        """Declare that *member_id* can handle moves of the given *capabilities*.

        Delegates to ``fleet.register_capabilities`` when available.  This
        allows the fleet to route moves to members that declare they can
        handle the corresponding MoveKind.

        Args:
            member_id:    The fleet member identifier.
            capabilities: List of :class:`MoveKind` values or string names.
        """
        if hasattr(self.fleet, "register_capabilities"):
            try:
                self.fleet.register_capabilities(
                    member_id=member_id,
                    capabilities=[
                        str(getattr(c, "value", c)) for c in capabilities
                    ],
                )
            except Exception:  # pragma: no cover
                logger.debug("fleet.register_capabilities raised", exc_info=True)
        else:
            logger.debug(
                "register_member_capabilities: fleet has no register_capabilities; skipping"
            )

    def update_fleet_from_trajectory(self, trajectory: SemanticTrajectory) -> None:
        """Feed completed-trajectory history back to the fleet for calibration.

        Extracts the assignment history that corresponds to the trajectory's
        moves and passes outcomes to ``fleet.calibrate`` when available.

        Args:
            trajectory: The completed (or in-progress) trajectory.
        """
        if not hasattr(self.fleet, "calibrate"):
            return
        moves = getattr(trajectory, "moves", [])
        try:
            self.fleet.calibrate(
                moves=moves,
                assignment_history=self._assignment_history,
            )
        except Exception:  # pragma: no cover
            logger.debug("fleet.calibrate raised", exc_info=True)

    def fleet_status(self) -> dict:
        """Return a summary of fleet bridge state.

        Returns:
            Dict with keys: ``member_count``, ``assignment_count``,
            ``has_fleet``, ``has_competitive_search``.
        """
        member_count = 0
        if hasattr(self.fleet, "member_count"):
            try:
                member_count = int(self.fleet.member_count())
            except Exception:  # pragma: no cover
                pass
        return {
            "member_count": member_count,
            "assignment_count": len(self._assignment_history),
            "has_fleet": not isinstance(self.fleet, type),
            "has_competitive_search": not isinstance(self.competitive_search, type),
        }


# ---------------------------------------------------------------------------
# ControlFrontierAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ControlFrontierAdapter:
    """Adapts the frontier for use by semantic control (theory2.tex §44.7).

    The frontier maintains a priority-ordered collection of :class:`FrontierNode`
    objects representing semantically admissible futures.  This adapter:

    *   Converts :class:`SemanticControlState` obligations and covers to
        :class:`FrontierItem` entries via :meth:`state_to_frontier_items`.
    *   Converts frontier items back to partial state-update dicts via
        :meth:`frontier_to_state_update`.
    *   Synchronises the frontier with the current state via
        :meth:`update_frontier_from_state`.
    *   Scores the state relative to the frontier's priority distribution via
        :meth:`score_state`.
    *   Emits a backpressure signal when the frontier is near capacity via
        :meth:`backpressure_signal`.

    Theory reference: theory2.tex §44.7 "Frontier Backpressure and Obligation
    Scheduling."
    """

    frontier: Frontier | Any
    scorer_weights: dict[str, float] = field(
        default_factory=lambda: {
            "coverage": 0.4,
            "obligation_pressure": 0.3,
            "budget": 0.2,
            "health": 0.1,
        }
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def state_to_frontier_items(self, state: SemanticControlState) -> list[Any]:
        """Convert *state*'s obligations and cover IDs to :class:`FrontierItem` entries.

        Each obligation generates one frontier item with urgency proportional
        to the state's attainability score (theory2.tex §44.7, Definition 44.7).

        Args:
            state: The semantic-control state to convert.

        Returns:
            List of :class:`FrontierItem` instances (or dicts on stub).
        """
        items: list[Any] = []
        obligation_ids = getattr(state, "obligation_ids", []) or []
        cover_ids = getattr(state, "cover_ids", []) or []

        attainability = float(
            state.attainability_score() if callable(getattr(state, "attainability_score", None)) else 0.5
        )
        urgency_base = max(1, int(attainability * 10))

        for i, ob_id in enumerate(obligation_ids):
            item = {
                "item_id": ob_id,
                "kind": "obligation",
                "urgency": urgency_base + i,
                "source_state_id": getattr(state, "state_id", "?"),
            }
            items.append(item)

        for cov_id in cover_ids:
            item = {
                "item_id": cov_id,
                "kind": "cover",
                "urgency": urgency_base,
                "source_state_id": getattr(state, "state_id", "?"),
            }
            items.append(item)

        return items

    def frontier_to_state_update(self, frontier_items: list[Any]) -> dict:
        """Convert *frontier_items* back to a partial state-update dict.

        Extracts obligation IDs and cover IDs from the items to produce the
        ``obligation_ids`` and ``cover_ids`` fields of a state update.

        Args:
            frontier_items: Items fetched from the frontier.

        Returns:
            Dict suitable for merging into a :class:`SemanticControlState`
            constructor call (keys: ``obligation_ids``, ``cover_ids``).
        """
        obligation_ids: list[str] = []
        cover_ids: list[str] = []
        for item in frontier_items:
            kind = str(getattr(item, "kind", item.get("kind", "unknown") if isinstance(item, dict) else "unknown"))
            item_id = str(getattr(item, "item_id", item.get("item_id", "") if isinstance(item, dict) else ""))
            if kind == "obligation":
                obligation_ids.append(item_id)
            elif kind == "cover":
                cover_ids.append(item_id)
        return {"obligation_ids": obligation_ids, "cover_ids": cover_ids}

    def update_frontier_from_state(self, state: SemanticControlState) -> None:
        """Push *state*'s frontier items into the managed :class:`Frontier`.

        Calls :meth:`state_to_frontier_items` and then ``frontier.push`` for
        each resulting item.

        Args:
            state: The state whose items should be pushed.
        """
        items = self.state_to_frontier_items(state)
        if not hasattr(self.frontier, "push"):
            return
        for item in items:
            try:
                self.frontier.push(item)
            except Exception:  # pragma: no cover
                logger.debug("frontier.push raised for item %s", item, exc_info=True)

    def score_state(self, state: SemanticControlState) -> float:
        """Compute a composite priority score for *state* relative to the frontier.

        The score is a weighted combination of:
        *   ``coverage``: the state's coverage ratio (theory2.tex §44.1).
        *   ``obligation_pressure``: inverse of obligation count (fewer is better).
        *   ``budget``: normalised remaining budget.
        *   ``health``: 1.0 if health_status is "healthy", else 0.5 or 0.0.

        Args:
            state: The state to score.

        Returns:
            A float in [0.0, 1.0].
        """
        w = self.scorer_weights
        cov = float(
            state.coverage_ratio() if callable(getattr(state, "coverage_ratio", None)) else 0.0
        )
        n_ob = len(getattr(state, "obligation_ids", []) or [])
        ob_pressure = 1.0 / (1.0 + n_ob)
        budget = min(1.0, max(0.0, float(getattr(state, "budget", 1.0))))
        health_str = (
            state.health_status() if callable(getattr(state, "health_status", None)) else "unknown"
        )
        health_score = {"healthy": 1.0, "degraded": 0.5, "critical": 0.0}.get(
            str(health_str).lower(), 0.5
        )
        score = (
            w.get("coverage", 0.4) * cov
            + w.get("obligation_pressure", 0.3) * ob_pressure
            + w.get("budget", 0.2) * budget
            + w.get("health", 0.1) * health_score
        )
        return float(min(1.0, max(0.0, score)))

    def backpressure_signal(self, state: SemanticControlState) -> str:
        """Return a backpressure signal for *state*: ``"normal"``, ``"throttle"``, or ``"block"``.

        The signal is derived from the obligation-to-cover ratio compared to
        the module-level thresholds ``BACKPRESSURE_THROTTLE_RATIO`` and
        ``BACKPRESSURE_BLOCK_RATIO`` (theory2.tex §44.7).

        Args:
            state: The state to evaluate.

        Returns:
            ``"normal"``, ``"throttle"``, or ``"block"``.
        """
        n_ob = len(getattr(state, "obligation_ids", []) or [])
        n_cov = max(1, len(getattr(state, "cover_ids", []) or []))
        ratio = n_ob / n_cov

        if ratio >= BACKPRESSURE_BLOCK_RATIO:
            return "block"
        if ratio >= BACKPRESSURE_THROTTLE_RATIO:
            return "throttle"
        return "normal"


# ---------------------------------------------------------------------------
# SemanticControlOrchestrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SemanticControlOrchestrator:
    """Top-level integration class: orchestrates semantic control across all subsystems (theory2.tex §44).

    This class is the main entry point for running a full semantic-control
    loop.  A single :meth:`step` performs:

    1.  Backpressure check via :attr:`frontier_adapter`.
    2.  Trust validation via :attr:`trust_integrator`.
    3.  Move selection via :attr:`law`.
    4.  Fleet assignment via :attr:`fleet_bridge`.
    5.  Descent validation via :attr:`descent_connector`.
    6.  Move execution and trajectory append.

    :meth:`run` iterates :meth:`step` up to ``max_steps`` times and returns
    the completed :class:`SemanticTrajectory`.

    Theory reference: theory2.tex §44 "Semantic Control of Project-Scale
    Orchestration."
    """

    orchestrator: Orchestrator | Any
    state_manager: StateManager | Any
    trust_integrator: ControlTrustIntegrator
    descent_connector: ControlDescentConnector
    fleet_bridge: ControlFleetBridge
    frontier_adapter: ControlFrontierAdapter
    current_trajectory: SemanticTrajectory | None
    law: ControlLaw
    max_steps: int = 100

    # Internal control flags (must appear after user-visible fields)
    _running: bool = field(default=False)
    _paused: bool = field(default=False)
    _step_count: int = field(default=0)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, initial_state: SemanticControlState) -> None:
        """Set up the orchestrator with *initial_state* as the starting point.

        Creates a fresh :class:`SemanticTrajectory`, pushes the initial state
        into the state manager, and seeds the frontier.

        Args:
            initial_state: The starting :class:`SemanticControlState`.
        """
        traj_id = str(uuid.uuid4())
        self.current_trajectory = SemanticTrajectory(trajectory_id=traj_id)
        if hasattr(self.current_trajectory, "append"):
            self.current_trajectory.append(initial_state, None)

        if hasattr(self.state_manager, "push"):
            self.state_manager.push(initial_state)

        self.frontier_adapter.update_frontier_from_state(initial_state)
        self._running = True
        self._paused = False
        self._step_count = 0
        logger.info(
            "SemanticControlOrchestrator initialized: state=%s trajectory=%s",
            getattr(initial_state, "state_id", "?")[:8],
            traj_id[:8],
        )

    def step(self) -> tuple[SemanticControlState, AdmissibleMove | None]:
        """Execute a single semantic-control step.

        Returns the new state and the move that was applied (or None if no
        move was selected).

        The step protocol (theory2.tex §44.2):
        1.  Retrieve the current state from the state manager.
        2.  Check backpressure; if ``"block"``, return state unchanged.
        3.  Generate candidate moves via the control law.
        4.  Validate trust for the top candidate.
        5.  Run fleet bidding to select a move.
        6.  Validate the resulting state transition via descent.
        7.  Apply the move, repair obstructions, and record.

        Returns:
            Tuple of (new_state, applied_move).
        """
        if not self._running or self._paused:
            current = self._current_state()
            return current, None

        current = self._current_state()
        if current is None:
            logger.warning("step: no current state; aborting")
            return SemanticControlState(), None  # type: ignore[call-arg]

        # --- 1. Backpressure ---
        bp = self.frontier_adapter.backpressure_signal(current)
        if bp == "block":
            logger.debug("step: backpressure=block; skipping")
            return current, None

        # --- 2. Candidate generation via ControlLaw ---
        candidates: list[AdmissibleMove] = []
        if hasattr(self.law, "select_move"):
            try:
                move_candidate = self.law.select_move(current, [])
                if move_candidate is not None:
                    candidates = [move_candidate]
            except Exception:  # pragma: no cover
                logger.debug("law.select_move raised", exc_info=True)

        if not candidates:
            logger.debug("step: no candidates generated")
            self._step_count += 1
            return current, None

        top_candidate = candidates[0]

        # --- 3. Trust validation ---
        trust_ok = self.trust_integrator.validate_trust(top_candidate, current)
        self.trust_integrator.audit_move(top_candidate, current, trust_ok)
        if not trust_ok:
            logger.debug("step: trust validation failed for move %s", getattr(top_candidate, "move_id", "?")[:8])
            self._step_count += 1
            return current, None

        # --- 4. Fleet bidding ---
        bids = self.fleet_bridge.collect_bids(current, candidates)
        selected_move: AdmissibleMove | None = self.fleet_bridge.evaluate_bids(bids, current)
        if selected_move is None:
            selected_move = top_candidate

        # --- 5. Apply move ---
        new_state: SemanticControlState = current
        if hasattr(selected_move, "apply") and callable(selected_move.apply):
            try:
                new_state = selected_move.apply(current)
            except Exception:  # pragma: no cover
                logger.debug("move.apply raised", exc_info=True)
                new_state = current

        # --- 6. Descent validation ---
        descent_ok = self.descent_connector.validate_transition(current, new_state)
        if not descent_ok:
            logger.debug("step: descent validation failed; attempting repair")
            new_state = self.descent_connector.repair_frontier(new_state)

        # --- 7. Fleet assignment ---
        self.fleet_bridge.assign_move_to_fleet(selected_move, new_state)

        # --- 8. Record ---
        if hasattr(self.state_manager, "push"):
            self.state_manager.push(new_state)
        self.frontier_adapter.update_frontier_from_state(new_state)
        if self.current_trajectory is not None and hasattr(self.current_trajectory, "append"):
            self.current_trajectory.append(new_state, selected_move)

        self._step_count += 1
        return new_state, selected_move

    def run(
        self,
        initial_state: SemanticControlState | None = None,
    ) -> SemanticTrajectory:
        """Run the full semantic-control loop for up to ``max_steps`` steps.

        If *initial_state* is provided, :meth:`initialize` is called first.
        Iteration halts when either ``max_steps`` is reached or
        :meth:`is_converging` on the trajectory returns True.

        Args:
            initial_state: Optional starting state; if None the existing
                           trajectory is continued.

        Returns:
            The completed (or partially completed) :class:`SemanticTrajectory`.
        """
        if initial_state is not None:
            self.initialize(initial_state)

        if self.current_trajectory is None:
            raise RuntimeError("SemanticControlOrchestrator.run: not initialized")

        logger.info("SemanticControlOrchestrator.run: max_steps=%d", self.max_steps)
        for i in range(self.max_steps):
            if not self._running or self._paused:
                break
            state, move = self.step()
            traj = self.current_trajectory
            if traj is not None and hasattr(traj, "is_converging") and traj.is_converging():
                logger.info("run: trajectory converged at step %d", i + 1)
                break
            if move is None and i > 0:
                logger.info("run: no move selected at step %d; halting", i + 1)
                break

        logger.info(
            "SemanticControlOrchestrator.run complete: %d steps, trajectory=%s",
            self._step_count,
            getattr(self.current_trajectory, "trajectory_id", "?")[:8],
        )
        return self.current_trajectory  # type: ignore[return-value]

    def pause(self) -> None:
        """Pause the step loop; :meth:`step` will be a no-op until :meth:`resume`."""
        self._paused = True
        logger.debug("SemanticControlOrchestrator paused")

    def resume(self) -> None:
        """Resume the step loop after a :meth:`pause`."""
        self._paused = False
        logger.debug("SemanticControlOrchestrator resumed")

    def status(self) -> dict:
        """Return a high-level status dict for the orchestrator.

        Returns:
            Dict with keys: ``running``, ``paused``, ``step_count``,
            ``trajectory_length``, ``trust_integrator``, ``descent_connector``,
            ``fleet_bridge``, ``backpressure``, ``law_name``.
        """
        traj_len = 0
        bp = "unknown"
        if self.current_trajectory is not None:
            traj_len = (
                self.current_trajectory.length()
                if hasattr(self.current_trajectory, "length")
                else len(getattr(self.current_trajectory, "states", []))
            )
            current = self._current_state()
            if current is not None:
                bp = self.frontier_adapter.backpressure_signal(current)
        return {
            "running": self._running,
            "paused": self._paused,
            "step_count": self._step_count,
            "trajectory_length": traj_len,
            "trust_integrator": self.trust_integrator.status(),
            "descent_connector": self.descent_connector.status(),
            "fleet_bridge": self.fleet_bridge.fleet_status(),
            "backpressure": bp,
            "law_name": str(getattr(self.law, "name", type(self.law).__name__)),
        }

    def diagnose(self) -> dict:
        """Return a detailed diagnostic dict for debugging and monitoring.

        Includes full status plus the latest state snapshot, trajectory
        convergence flag, and global section assembly result.

        Returns:
            Dict combining :meth:`status` with extended diagnostics.
        """
        base = self.status()
        current = self._current_state()
        state_dict: dict = {}
        obstructions: list = []
        global_section_ok: bool = False

        if current is not None:
            if hasattr(current, "to_dict"):
                state_dict = current.to_dict()
            obstructions = self.descent_connector.check_obstructions(current)
            if self.current_trajectory is not None:
                gs = self.descent_connector.assemble_global_section(
                    self.current_trajectory
                )
                global_section_ok = gs is not None

        is_converging = False
        if self.current_trajectory is not None and hasattr(
            self.current_trajectory, "is_converging"
        ):
            is_converging = bool(self.current_trajectory.is_converging())

        base.update(
            {
                "current_state": state_dict,
                "obstruction_count": len(obstructions),
                "global_section_assembled": global_section_ok,
                "is_converging": is_converging,
                "state_manager_depth": (
                    len(self.state_manager.history())
                    if hasattr(self.state_manager, "history")
                    else None
                ),
            }
        )
        return base

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _current_state(self) -> SemanticControlState | None:
        """Return the most recent state from the state manager or trajectory."""
        if hasattr(self.state_manager, "latest"):
            result = self.state_manager.latest()
            if result is not None:
                return result  # type: ignore[return-value]
        if self.current_trajectory is not None:
            if hasattr(self.current_trajectory, "latest_state"):
                return self.current_trajectory.latest_state()  # type: ignore[return-value]
        return None


# ---------------------------------------------------------------------------
# Default configuration constant
# ---------------------------------------------------------------------------

#: Suggested default configuration dict for :func:`build_semantic_control_orchestrator`.
DEFAULT_ORCHESTRATOR_CONFIG: dict = {
    "max_steps": 100,
    "obstruction_tolerance": DEFAULT_OBSTRUCTION_TOLERANCE,
    "backpressure_throttle_ratio": BACKPRESSURE_THROTTLE_RATIO,
    "backpressure_block_ratio": BACKPRESSURE_BLOCK_RATIO,
    "trust_level_default": "LOW",
    "scorer_weights": {
        "coverage": 0.4,
        "obligation_pressure": 0.3,
        "budget": 0.2,
        "health": 0.1,
    },
}


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def build_semantic_control_orchestrator(
    trust_algebra: Any | None = None,
    trust_policy: Any | None = None,
    audit_log: Any | None = None,
    descent_engine: Any | None = None,
    gluing_data: Any | None = None,
    fleet: Any | None = None,
    competitive_search: Any | None = None,
    frontier: Any | None = None,
    orchestrator: Any | None = None,
    law: ControlLaw | None = None,
    max_steps: int = 100,
    obstruction_tolerance: float = DEFAULT_OBSTRUCTION_TOLERANCE,
    scorer_weights: dict | None = None,
) -> "SemanticControlOrchestrator":
    """Construct a :class:`SemanticControlOrchestrator` with sensible defaults.

    All parameters are optional; stub instances are used for any that are
    omitted, enabling use in unit tests without the full JuGeo stack.

    Args:
        trust_algebra:          :class:`TrustAlgebra` instance or None.
        trust_policy:           :class:`TrustPolicy` instance or None.
        audit_log:              :class:`TrustAuditLog` instance or None.
        descent_engine:         :class:`DescentEngine` instance or None.
        gluing_data:            :class:`GluingData` instance or None.
        fleet:                  :class:`Fleet` instance or None.
        competitive_search:     :class:`CompetitiveSearch` instance or None.
        frontier:               :class:`Frontier` instance or None.
        orchestrator:           :class:`Orchestrator` instance or None.
        law:                    :class:`ControlLaw` instance or None.
        max_steps:              Maximum number of control steps (default 100).
        obstruction_tolerance:  Descent obstruction tolerance (default 0.1).
        scorer_weights:         Override for frontier scorer weights.

    Returns:
        A fully wired :class:`SemanticControlOrchestrator`.
    """
    _trust_algebra = trust_algebra if trust_algebra is not None else TrustAlgebra()
    _trust_policy = trust_policy if trust_policy is not None else TrustPolicy()
    _audit_log = audit_log if audit_log is not None else TrustAuditLog()
    _descent_engine = descent_engine if descent_engine is not None else DescentEngine()
    _gluing_data = gluing_data if gluing_data is not None else GluingData()
    _fleet = fleet if fleet is not None else Fleet()
    _comp_search = competitive_search if competitive_search is not None else CompetitiveSearch()
    _frontier = frontier if frontier is not None else Frontier()
    _orchestrator = orchestrator if orchestrator is not None else Orchestrator()
    _law = law if law is not None else ControlLaw()
    _weights = scorer_weights or dict(DEFAULT_ORCHESTRATOR_CONFIG["scorer_weights"])

    trust_integrator = ControlTrustIntegrator(
        trust_algebra=_trust_algebra,
        trust_policy=_trust_policy,
        audit_log=_audit_log,
    )
    descent_connector = ControlDescentConnector(
        descent_engine=_descent_engine,
        gluing_data=_gluing_data,
        obstruction_tolerance=obstruction_tolerance,
    )
    fleet_bridge = ControlFleetBridge(
        fleet=_fleet,
        competitive_search=_comp_search,
    )
    frontier_adapter = ControlFrontierAdapter(
        frontier=_frontier,
        scorer_weights=_weights,
    )
    state_manager = StateManager()

    return SemanticControlOrchestrator(
        orchestrator=_orchestrator,
        state_manager=state_manager,
        trust_integrator=trust_integrator,
        descent_connector=descent_connector,
        fleet_bridge=fleet_bridge,
        frontier_adapter=frontier_adapter,
        current_trajectory=None,
        law=_law,
        max_steps=max_steps,
    )


__all__ = [
    # Core integration classes
    "ControlTrustIntegrator",
    "ControlDescentConnector",
    "ControlFleetBridge",
    "ControlFrontierAdapter",
    "SemanticControlOrchestrator",
    # Constants
    "BACKPRESSURE_BLOCK_RATIO",
    "BACKPRESSURE_THROTTLE_RATIO",
    "DEFAULT_OBSTRUCTION_TOLERANCE",
    "DEFAULT_ORCHESTRATOR_CONFIG",
    "INTEGRATION_VERSION",
    "MAX_FLEET_BIDS",
    "TRUST_LEVELS_ORDERED",
    # Factory
    "build_semantic_control_orchestrator",
]
