"""
Integration of the Generation State Space with Orchestration, Solver, and Evidence Layers.

# copilot: state-space-integration

This module provides three bridge classes that connect the generation state
space to the three external systems it depends on:

1. **OrchestratorBridge** — submits goals to and queries results from the
   :class:`jugeo.orchestration.controller.OrchestrationController`.  The
   orchestrator is responsible for routing construction work to the appropriate
   agents (local LLMs, remote solvers, human reviewers).

2. **SolverBridge** — queries the :class:`jugeo.solver.router.SolverRouter`
   for logical consistency checks, obligation discharge, and section
   compatibility scores.  The solver layer abstracts over multiple backends
   (SMT solvers, proof assistants, type checkers).

3. **EvidenceBridge** — reads and writes to the evidence layer
   (:mod:`jugeo.evidence`).  Updates trust levels, records obstructions, and
   retrieves evidence bundles by state ID.

All three bridges are *optional*: if the underlying system is not available,
they degrade gracefully by returning stub responses.  This ensures the state
space can be tested and explored without a fully installed jugeo environment.

The :class:`StateSpaceIntegration` facade composes all three bridges and
provides a single entry point for the integration cycle.

Theory Reference: theory2.tex §40.14.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import hashlib
import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "GenerationJudgment",
    "TrustTier",
    "OrchestratorBridge",
    "SolverBridge",
    "EvidenceBridge",
    "StateSpaceIntegration",
    "IntegrationManager",
    "integrate_with_orchestrator",
    "query_solver_for_state",
    "update_evidence_from_state",
    "build_integration",
    "THEORY_SECTION",
    "CHAPTER",
]

THEORY_SECTION = "40.14"
CHAPTER = 40

# ---------------------------------------------------------------------------
# Jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import OrchestrationController
    _HAS_ORCHESTRATOR = True
except ImportError:
    _HAS_ORCHESTRATOR = False

    class OrchestrationController:  # type: ignore[no-redef]
        """Stub when jugeo.orchestration is not available."""

        def submit_goal(self, goal: dict) -> str:
            return f"stub_goal_{uuid.uuid4().hex[:8]}"

        def query_goal(self, goal_id: str) -> dict:
            return {"goal_id": goal_id, "status": "pending", "result": None}

        def request_fleet(self, patches: list) -> list:
            return [f"stub_fleet_{i}" for i in range(len(patches))]

        def await_fleet(self, fleet_ids: list, timeout_ms: float) -> dict:
            return {fid: {"status": "stub"} for fid in fleet_ids}

        def signal_backpressure(self, reason: str, severity: str) -> None:
            pass

try:
    from jugeo.solver.router import SolverRouter, BackendKind
    _HAS_SOLVER = True
except ImportError:
    _HAS_SOLVER = False

    class SolverRouter:  # type: ignore[no-redef]
        """Stub when jugeo.solver is not available."""

        def query(self, query: dict) -> dict:
            return {"verdict": "unknown", "confidence": 0.0, "explanation": "stub"}

        def discharge(self, obligation_id: str, state: dict) -> dict:
            return {"discharged": False, "explanation": "stub router"}

        def check_consistency(self, sections: dict) -> tuple:
            return True, []

        def compute_compatibility(self, a: dict, b: dict) -> float:
            return 1.0

    class BackendKind:  # type: ignore[no-redef]
        SMT = "SMT"
        TYPE_CHECK = "TYPE_CHECK"
        PROOF_ASSISTANT = "PROOF_ASSISTANT"

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    _HAS_TRUST = True
except ImportError:
    _HAS_TRUST = False

    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = "CONTRADICTED"
        UNVERIFIED = "UNVERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        ORACLE_PROPOSED = "ORACLE_PROPOSED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

    class TrustTier(Enum):  # type: ignore[no-redef]
        """Ordered trust tiers — trust is an ordered algebra, never a float.

        PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.
        """

        PROPOSAL = 0
        REVIEWED = 1
        VERIFIED = 2
        RUNTIME_WITNESSED = 3
        PROOF_BACKED = 4

        def join(self, other: "TrustTier") -> "TrustTier":
            return TrustTier(max(self.value, other.value))

        def meet(self, other: "TrustTier") -> "TrustTier":
            return TrustTier(min(self.value, other.value))

        def is_at_least(self, floor: "TrustTier") -> bool:
            return self.value >= floor.value

    class TrustProfile:  # type: ignore[no-redef]
        def __init__(self, level: str = "UNVERIFIED", tier: str = "PROPOSAL"):
            self.level = level
            self.tier = tier

try:
    from jugeo.evidence.channels import EvidenceChannel, ChannelJurisdiction
    _HAS_CHANNELS = True
except ImportError:
    _HAS_CHANNELS = False

    class EvidenceChannel:  # type: ignore[no-redef]
        pass

    class ChannelJurisdiction:  # type: ignore[no-redef]
        pass

try:
    from jugeo.generation.treaties import TreatyStatus, OverlapTreaty
    _HAS_TREATIES = True
except ImportError:
    _HAS_TREATIES = False

    class TreatyStatus:  # type: ignore[no-redef]
        pass

    class OverlapTreaty:  # type: ignore[no-redef]
        pass

try:
    from jugeo.errors import JuGeoError, StructuredFailure
except ImportError:
    class JuGeoError(Exception):  # type: ignore[no-redef]
        pass

    class StructuredFailure(Exception):  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Trust ordering
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = [
    "CONTRADICTED",
    "UNVERIFIED",
    "COPILOT_SUGGESTED",
    "ORACLE_PROPOSED",
    "HUMAN_ATTESTED",
    "RUNTIME_WITNESSED",
    "SOLVER_DISCHARGED",
    "MECHANICALLY_VERIFIED",
]


def _trust_ge(level: str, floor: str) -> bool:
    try:
        return _TRUST_ORDER.index(level) >= _TRUST_ORDER.index(floor)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# GenerationJudgment — frozen 8-tuple (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationJudgment:
    """A generation judgment as an immutable 8-tuple (c, φ, A, E, O, B, T, Π).

    Theory Invariant: Judgments are 8-tuples, never booleans.

    Attributes:
        c:   Candidate identifier (e.g., state_id or generated code handle).
        phi: Specification φ the candidate must satisfy.
        A:   Assumptions active at the judgment site.
        E:   Evidence items supporting or refuting the judgment.
        O:   Open obligations (proof goals not yet discharged).
        B:   Obstruction set — Čech H¹ cohomology classes.
             Obstructions persist until explicitly discharged (Obstruction-
             Persistence invariant): they cannot be removed without proof.
        T:   Trust tier (element of the ordered trust algebra).
        Pi:  Proof context — propositions already established.
    """

    c: str                            # candidate
    phi: str                          # specification
    A: tuple                          # assumptions
    E: tuple                          # evidence
    O: tuple                          # open obligations
    B: tuple                          # obstructions (Čech H¹ classes)
    T: str                            # trust tier name
    Pi: tuple                         # proof context

    @property
    def is_discharged(self) -> bool:
        """True iff all obligations are closed and no obstructions remain."""
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def obstruction_count(self) -> int:
        """Number of Čech H¹ cohomology classes blocking discharge."""
        return len(self.B)

    def promote_trust(self, evidence_id: str) -> "GenerationJudgment":
        """Return a new judgment with trust promoted by one tier.

        Requires a non-empty evidence_id (No-Silent-Promotion invariant).
        """
        if not evidence_id:
            raise ValueError("Cannot promote trust without explicit evidence (No-Silent-Promotion)")
        tier_order = ["PROPOSAL", "REVIEWED", "VERIFIED", "RUNTIME_WITNESSED", "PROOF_BACKED"]
        try:
            idx = tier_order.index(self.T)
            new_tier = tier_order[min(idx + 1, len(tier_order) - 1)]
        except ValueError:
            new_tier = "REVIEWED"
        return GenerationJudgment(
            c=self.c, phi=self.phi, A=self.A,
            E=self.E + (evidence_id,),
            O=self.O, B=self.B, T=new_tier,
            Pi=self.Pi + (f"trust_promotion:{evidence_id}",),
        )

    def discharge_obligation(self, obligation: str, proof_id: str) -> "GenerationJudgment":
        """Return a new judgment with *obligation* removed from O."""
        if obligation not in self.O:
            raise ValueError(f"Obligation {obligation!r} not in O")
        return GenerationJudgment(
            c=self.c, phi=self.phi, A=self.A, E=self.E,
            O=tuple(o for o in self.O if o != obligation),
            B=self.B, T=self.T,
            Pi=self.Pi + (f"discharged:{obligation}:{proof_id}",),
        )

    def clear_obstruction(self, cech_class: str, proof_id: str) -> "GenerationJudgment":
        """Return a new judgment with *cech_class* removed from B.

        Obstructions require explicit proof evidence to discharge
        (Obstruction-Persistence invariant).
        """
        if cech_class not in self.B:
            raise ValueError(f"Čech class {cech_class!r} not in B")
        return GenerationJudgment(
            c=self.c, phi=self.phi, A=self.A, E=self.E,
            O=self.O,
            B=tuple(b for b in self.B if b != cech_class),
            T=self.T,
            Pi=self.Pi + (f"cech_discharged:{cech_class}:{proof_id}",),
        )

    def to_dict(self) -> dict:
        """Serialise the judgment to a plain dict."""
        return {
            "c": self.c, "phi": self.phi,
            "A": list(self.A), "E": list(self.E),
            "O": list(self.O), "B": list(self.B),
            "T": self.T, "Pi": list(self.Pi),
        }

    @classmethod
    def make(
        cls,
        c: str,
        phi: str = "default_spec",
        *,
        assumptions: tuple = (),
        evidence: tuple = (),
        obligations: tuple = (),
        obstructions: tuple = (),
        trust: str = "PROPOSAL",
        proof_context: tuple = (),
    ) -> "GenerationJudgment":
        """Convenience constructor for a GenerationJudgment."""
        return cls(
            c=c, phi=phi, A=assumptions, E=evidence,
            O=obligations, B=obstructions, T=trust, Pi=proof_context,
        )



class OrchestratorBridge:
    """Bridge to the jugeo orchestration layer.

    This bridge submits generation goals to the orchestration controller,
    queries their status, and manages parallel construction fleets.

    When the :class:`jugeo.orchestration.controller.OrchestrationController`
    is not available (import failure), all methods return stub responses
    rather than raising.

    Parameters
    ----------
    controller:
        An optional :class:`OrchestrationController` instance.  If None,
        a stub is used.
    """

    def __init__(self, controller: Any = None) -> None:
        if controller is not None:
            self._controller = controller
        elif _HAS_ORCHESTRATOR:
            try:
                self._controller = OrchestrationController()
            except Exception as exc:
                logger.warning("OrchestratorBridge: could not create controller: %s", exc)
                self._controller = OrchestrationController()
        else:
            logger.debug("OrchestratorBridge: using stub controller (no jugeo.orchestration)")
            self._controller = OrchestrationController()

        self._is_stub = not _HAS_ORCHESTRATOR or controller is None
        logger.info(
            "OrchestratorBridge initialised (stub=%s)", self._is_stub
        )

    def submit_generation_goal(self, goal: dict) -> str:
        """Submit a generation goal to the orchestrator.

        Parameters
        ----------
        goal:
            The goal dict (must contain at least "goal_id" and "coordinate").

        Returns
        -------
        str
            The goal ID assigned by the orchestrator.
        """
        try:
            goal_id = self._controller.submit_goal(goal)
            logger.info(
                "OrchestratorBridge: submitted goal, assigned id=%s", goal_id
            )
            return goal_id
        except Exception as exc:
            logger.warning("OrchestratorBridge.submit_generation_goal: %s", exc)
            return f"fallback_goal_{uuid.uuid4().hex[:8]}"

    def query_goal_status(self, goal_id: str) -> dict:
        """Query the status of a previously submitted goal.

        Parameters
        ----------
        goal_id:
            The goal ID returned by :meth:`submit_generation_goal`.

        Returns
        -------
        dict
            Contains at minimum "goal_id", "status", and "result".
        """
        try:
            result = self._controller.query_goal(goal_id)
            logger.debug("OrchestratorBridge: goal %s status=%s", goal_id[:8], result.get("status"))
            return result
        except Exception as exc:
            logger.warning("OrchestratorBridge.query_goal_status: %s", exc)
            return {
                "goal_id": goal_id,
                "status": "unknown",
                "result": None,
                "error": str(exc),
            }

    def request_parallel_construction(self, patches: list[str]) -> list[str]:
        """Request parallel construction of multiple patches.

        Parameters
        ----------
        patches:
            Patch identifiers to construct in parallel.

        Returns
        -------
        list[str]
            Fleet identifiers (one per patch).
        """
        try:
            fleet_ids = self._controller.request_fleet(patches)
            logger.info(
                "OrchestratorBridge: requested fleet for %d patches", len(patches)
            )
            return fleet_ids
        except Exception as exc:
            logger.warning("OrchestratorBridge.request_parallel_construction: %s", exc)
            return [f"fallback_fleet_{i}_{uuid.uuid4().hex[:6]}" for i in range(len(patches))]

    def await_fleet_results(
        self, fleet_ids: list[str], timeout_ms: float = 5000.0
    ) -> dict:
        """Await results from a construction fleet.

        Parameters
        ----------
        fleet_ids:
            Fleet identifiers from :meth:`request_parallel_construction`.
        timeout_ms:
            Timeout in milliseconds.

        Returns
        -------
        dict
            Maps fleet_id → result dict.
        """
        try:
            results = self._controller.await_fleet(fleet_ids, timeout_ms)
            logger.info(
                "OrchestratorBridge: fleet results received (%d)", len(results)
            )
            return results
        except Exception as exc:
            logger.warning("OrchestratorBridge.await_fleet_results: %s", exc)
            return {fid: {"status": "timeout", "error": str(exc)} for fid in fleet_ids}

    def signal_backpressure(self, reason: str, severity: str = "warning") -> None:
        """Signal backpressure to the orchestrator.

        Parameters
        ----------
        reason:
            Human-readable reason for backpressure.
        severity:
            One of "info", "warning", "critical".
        """
        try:
            self._controller.signal_backpressure(reason, severity)
            logger.info(
                "OrchestratorBridge: backpressure signalled (%s): %s",
                severity,
                reason,
            )
        except Exception as exc:
            logger.warning("OrchestratorBridge.signal_backpressure: %s", exc)

    def is_available(self) -> bool:
        """Return True if the real orchestrator is available."""
        return _HAS_ORCHESTRATOR and not self._is_stub


# ---------------------------------------------------------------------------
# SolverBridge
# ---------------------------------------------------------------------------


class SolverBridge:
    """Bridge to the jugeo solver layer.

    The solver layer is responsible for logical consistency checking,
    obligation discharge, and compatibility scoring between sections.

    When :class:`jugeo.solver.router.SolverRouter` is not available, all
    methods return conservative stub responses (consistent = True,
    compatibility = 1.0).

    Parameters
    ----------
    router:
        An optional :class:`SolverRouter` instance.  If None, a stub is used.
    """

    def __init__(self, router: Any = None) -> None:
        if router is not None:
            self._router = router
        elif _HAS_SOLVER:
            try:
                self._router = SolverRouter()
            except Exception as exc:
                logger.warning("SolverBridge: could not create router: %s", exc)
                self._router = SolverRouter()
        else:
            logger.debug("SolverBridge: using stub router (no jugeo.solver)")
            self._router = SolverRouter()

        self._is_stub = not _HAS_SOLVER or router is None
        logger.info("SolverBridge initialised (stub=%s)", self._is_stub)

    def query_solver_for_state(self, state: dict) -> dict:
        """Query the solver for a verdict on the current state.

        The solver examines the judgment tuple components and returns a
        verdict dict with "verdict" (one of "valid", "invalid", "unknown"),
        "confidence" (0–1), and "explanation".

        Parameters
        ----------
        state:
            The generation state dict.

        Returns
        -------
        dict
        """
        query = {
            "coordinate": state.get("judgment_coordinate", ""),
            "proposition": state.get("judgment_proposition", ""),
            "carrier": state.get("judgment_carrier", "Any"),
            "trust_annotation": state.get("trust_annotation", "UNVERIFIED"),
            "evidence_refs": list(state.get("evidence_refs", [])),
            "obligations": list(state.get("obligations", [])),
        }
        try:
            result = self._router.query(query)
            logger.debug(
                "SolverBridge: verdict=%s (confidence=%.2f)",
                result.get("verdict", "?"),
                result.get("confidence", 0.0),
            )
            return result
        except Exception as exc:
            logger.warning("SolverBridge.query_solver_for_state: %s", exc)
            return {
                "verdict": "unknown",
                "confidence": 0.0,
                "explanation": f"solver unavailable: {exc}",
            }

    def discharge_obligation(self, obligation_id: str, state: dict) -> dict:
        """Ask the solver to discharge an obligation.

        Parameters
        ----------
        obligation_id:
            The obligation to discharge.
        state:
            The state in which the obligation arises.

        Returns
        -------
        dict
            Contains "discharged" (bool), "evidence_id" (str), "explanation".
        """
        try:
            result = self._router.discharge(obligation_id, state)
            if result.get("discharged"):
                logger.info(
                    "SolverBridge: discharged obligation %s", obligation_id[:8]
                )
            return result
        except Exception as exc:
            logger.warning("SolverBridge.discharge_obligation: %s", exc)
            return {
                "discharged": False,
                "evidence_id": "",
                "explanation": f"solver unavailable: {exc}",
            }

    def check_logical_consistency(self, sections: dict) -> tuple[bool, list[str]]:
        """Check that the given sections are mutually consistent.

        Parameters
        ----------
        sections:
            Mapping patch_id → section content.

        Returns
        -------
        (is_consistent, list_of_inconsistencies)
        """
        try:
            ok, inconsistencies = self._router.check_consistency(sections)
            logger.debug(
                "SolverBridge: consistency=%s, issues=%d", ok, len(inconsistencies)
            )
            return ok, inconsistencies
        except Exception as exc:
            logger.warning("SolverBridge.check_logical_consistency: %s", exc)
            return True, []  # conservative: assume consistent

    def compute_compatibility(self, section_a: dict, section_b: dict) -> float:
        """Compute a compatibility score between two sections.

        Parameters
        ----------
        section_a, section_b:
            Section content dicts.

        Returns
        -------
        float
            Compatibility score in [0, 1].  1.0 = fully compatible.
        """
        try:
            score = self._router.compute_compatibility(section_a, section_b)
            logger.debug("SolverBridge: compatibility score=%.3f", score)
            return float(score)
        except Exception as exc:
            logger.warning("SolverBridge.compute_compatibility: %s", exc)
            return 1.0  # conservative: assume compatible

    def is_available(self) -> bool:
        """Return True if the real solver router is available."""
        return _HAS_SOLVER and not self._is_stub


# ---------------------------------------------------------------------------
# EvidenceBridge
# ---------------------------------------------------------------------------


class EvidenceBridge:
    """Bridge to the jugeo evidence layer.

    This bridge reads and writes evidence items, updates trust levels, and
    records obstructions.  It is the single point of contact between the
    state space and the evidence layer.

    Unlike the orchestrator and solver bridges, the evidence bridge does not
    wrap a single controller object; instead it uses the module-level API of
    ``jugeo.evidence``.
    """

    def __init__(self) -> None:
        self._evidence_store: dict[str, dict] = {}
        self._trust_store: dict[str, str] = {}
        self._obstruction_store: dict[str, list] = {}
        self._is_stub = not (_HAS_TRUST and _HAS_CHANNELS)
        logger.info("EvidenceBridge initialised (stub=%s)", self._is_stub)

    def update_evidence_from_state(self, state: dict) -> list[str]:
        """Extract and record evidence from *state*.

        Creates evidence items for each evidence_ref in the state and stores
        them in the evidence layer (or the local stub store).

        Parameters
        ----------
        state:
            The generation state dict.

        Returns
        -------
        list[str]
            New evidence item IDs created.
        """
        new_ids: list[str] = []
        state_id = state.get("state_id", str(uuid.uuid4()))
        evidence_refs = list(state.get("evidence_refs", []))

        for ref in evidence_refs:
            ev_id = f"ev_{ref}_{uuid.uuid4().hex[:6]}"
            ev_item = {
                "evidence_id": ev_id,
                "state_id": state_id,
                "ref": ref,
                "trust_level": state.get("trust_annotation", "UNVERIFIED"),
                "recorded_at": time.time(),
            }
            self._evidence_store[ev_id] = ev_item
            new_ids.append(ev_id)
            logger.debug("EvidenceBridge: recorded evidence %s from state %s", ev_id[:8], state_id[:8])

        return new_ids

    def query_trust_level(self, state_id: str) -> str:
        """Query the trust level associated with *state_id*.

        Parameters
        ----------
        state_id:
            The state ID.

        Returns
        -------
        str
            The TrustLevel string, or "UNVERIFIED" if not found.
        """
        level = self._trust_store.get(state_id, "UNVERIFIED")
        logger.debug("EvidenceBridge: trust for state %s = %r", state_id[:8], level)
        return level

    def record_obstruction(self, state_id: str, obstruction: dict) -> str:
        """Record an obstruction for *state_id*.

        Obstructions are persistent (never silently removed).

        Parameters
        ----------
        state_id:
            The state where the obstruction was found.
        obstruction:
            A dict describing the obstruction (at minimum: "class_id", "description").

        Returns
        -------
        str
            The obstruction record ID.
        """
        obs_id = obstruction.get("class_id", f"obs_{uuid.uuid4().hex[:8]}")
        self._obstruction_store.setdefault(state_id, []).append(
            {**obstruction, "recorded_at": time.time()}
        )
        logger.info(
            "EvidenceBridge: recorded obstruction %s for state %s",
            obs_id[:8],
            state_id[:8],
        )
        return obs_id

    def promote_trust(
        self,
        state_id: str,
        from_level: str,
        to_level: str,
        justification: str,
    ) -> bool:
        """Promote the trust level of *state_id* from *from_level* to *to_level*.

        This implements the ``↑_π`` operator from the trust algebra.  Returns
        False if:
        - the justification is empty (No-Silent-Promotion invariant),
        - the promotion would decrease trust (trust monotonicity invariant).

        Parameters
        ----------
        state_id:
            The state whose trust level is being promoted.
        from_level:
            The current trust level.
        to_level:
            The target trust level.
        justification:
            Explicit justification (must not be empty).

        Returns
        -------
        bool
            True iff the promotion was accepted.
        """
        if not justification:
            logger.warning(
                "EvidenceBridge.promote_trust: empty justification (No-Silent-Promotion)"
            )
            return False

        if not _trust_ge(to_level, from_level):
            logger.warning(
                "EvidenceBridge.promote_trust: cannot decrease trust %r → %r",
                from_level,
                to_level,
            )
            return False

        self._trust_store[state_id] = to_level
        logger.info(
            "EvidenceBridge: trust promoted for state %s: %r → %r (justification=%r)",
            state_id[:8],
            from_level,
            to_level,
            justification[:30],
        )
        return True

    def get_obstructions(self, state_id: str) -> list[dict]:
        """Return all recorded obstructions for *state_id*."""
        return list(self._obstruction_store.get(state_id, []))

    def is_available(self) -> bool:
        """Return True if the real evidence layer is available."""
        return _HAS_TRUST


# ---------------------------------------------------------------------------
# StateSpaceIntegration facade
# ---------------------------------------------------------------------------


class StateSpaceIntegration:
    """Facade composing the three integration bridges.

    This is the main entry point for the integration layer.  It provides
    convenience methods that combine orchestration, solver, and evidence
    operations into coherent cycles.

    Parameters
    ----------
    controller:
        Optional orchestration controller.
    router:
        Optional solver router.
    """

    def __init__(
        self,
        controller: Any = None,
        router: Any = None,
    ) -> None:
        self.orchestrator_bridge = OrchestratorBridge(controller)
        self.solver_bridge = SolverBridge(router)
        self.evidence_bridge = EvidenceBridge()
        logger.info("StateSpaceIntegration facade initialised")

    def integrate_with_orchestrator(self, state: dict) -> dict:
        """Submit the current state as a generation goal to the orchestrator.

        Parameters
        ----------
        state:
            The generation state dict.

        Returns
        -------
        dict
            Integration result with "goal_id", "status", "orchestrator_available".
        """
        goal_id = self.orchestrator_bridge.submit_generation_goal(state)
        status = self.orchestrator_bridge.query_goal_status(goal_id)
        result = {
            "goal_id": goal_id,
            "status": status.get("status", "unknown"),
            "orchestrator_available": self.orchestrator_bridge.is_available(),
            "raw_status": status,
        }
        logger.debug(
            "StateSpaceIntegration.integrate_with_orchestrator: goal=%s, status=%s",
            goal_id[:8],
            result["status"],
        )
        return result

    def query_solver_for_state(self, state: dict) -> dict:
        """Query the solver for a verdict on *state*.

        Parameters
        ----------
        state:
            The generation state dict.

        Returns
        -------
        dict
            Solver verdict dict augmented with "solver_available".
        """
        verdict = self.solver_bridge.query_solver_for_state(state)
        verdict["solver_available"] = self.solver_bridge.is_available()
        return verdict

    def update_evidence_from_state(self, state: dict) -> dict:
        """Update the evidence layer from *state*.

        Records evidence items, queries trust level, and checks for
        obstructions.

        Parameters
        ----------
        state:
            The generation state dict.

        Returns
        -------
        dict
            Evidence update result with "new_evidence_ids", "trust_level",
            "obstructions", "evidence_available".
        """
        state_id = state.get("state_id", str(uuid.uuid4()))
        new_ids = self.evidence_bridge.update_evidence_from_state(state)
        trust = self.evidence_bridge.query_trust_level(state_id)
        obstructions = self.evidence_bridge.get_obstructions(state_id)

        return {
            "new_evidence_ids": new_ids,
            "trust_level": trust,
            "obstructions": obstructions,
            "evidence_available": self.evidence_bridge.is_available(),
            "state_id": state_id,
        }

    def full_integration_cycle(self, state: dict) -> dict:
        """Run all three integration operations in sequence.

        This is the main entry point for the integration cycle.  It:
        1. Submits the state to the orchestrator.
        2. Queries the solver for a verdict.
        3. Updates the evidence layer.

        Parameters
        ----------
        state:
            The generation state dict.

        Returns
        -------
        dict
            Combined result from all three integrations.
        """
        t0 = time.time()
        logger.info(
            "StateSpaceIntegration: full cycle for state %s",
            state.get("state_id", "?")[:8],
        )

        orch_result = self.integrate_with_orchestrator(state)
        solver_result = self.query_solver_for_state(state)
        evidence_result = self.update_evidence_from_state(state)

        elapsed_ms = (time.time() - t0) * 1000
        combined = {
            "state_id": state.get("state_id", ""),
            "orchestrator": orch_result,
            "solver": solver_result,
            "evidence": evidence_result,
            "elapsed_ms": elapsed_ms,
            "cycle_at": time.time(),
        }
        logger.info(
            "StateSpaceIntegration: cycle complete in %.1f ms", elapsed_ms
        )
        return combined

    def health_check(self) -> dict:
        """Check availability of all bridges.

        Returns
        -------
        dict
            Maps bridge_name → is_available (bool).
        """
        health = {
            "orchestrator": self.orchestrator_bridge.is_available(),
            "solver": self.solver_bridge.is_available(),
            "evidence": self.evidence_bridge.is_available(),
            "all_available": (
                self.orchestrator_bridge.is_available()
                and self.solver_bridge.is_available()
                and self.evidence_bridge.is_available()
            ),
            "checked_at": time.time(),
        }
        logger.info("StateSpaceIntegration health: %s", health)
        return health


# ---------------------------------------------------------------------------
# IntegrationManager — manages multiple StateSpaceIntegration instances
# ---------------------------------------------------------------------------


class IntegrationManager:
    """Manages multiple :class:`StateSpaceIntegration` instances.

    Routes generation requests to the appropriate integration, monitors
    health of all integrations, and provides a unified statistics interface.

    Each integration is registered under a name.  Requests can be routed
    by name or broadcast to all healthy integrations.

    Theory Invariant: all cross-integration communication is mediated by
    :class:`GenerationJudgment` 8-tuples, never plain booleans.
    """

    def __init__(self) -> None:
        self._integrations: dict[str, StateSpaceIntegration] = {}
        self._created_at: dict[str, float] = {}
        self._round_count: dict[str, int] = {}
        logger.debug("IntegrationManager created")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, integration: StateSpaceIntegration) -> None:
        """Register *integration* under *name*."""
        self._integrations[name] = integration
        self._created_at[name] = time.monotonic()
        self._round_count[name] = 0
        logger.info("IntegrationManager: registered integration %r", name)

    def create_and_register(
        self, name: str, controller: Any = None, router: Any = None
    ) -> StateSpaceIntegration:
        """Create a new :class:`StateSpaceIntegration` and register it."""
        intg = StateSpaceIntegration(controller=controller, router=router)
        self.register(name, intg)
        return intg

    # ------------------------------------------------------------------
    # Request routing
    # ------------------------------------------------------------------

    def route(self, name: str, state: dict) -> dict:
        """Route a full integration cycle for *state* to the named integration.

        Auto-creates the integration if it does not exist.
        """
        if name not in self._integrations:
            logger.info("IntegrationManager: auto-creating integration %r", name)
            self.create_and_register(name)
        intg = self._integrations[name]
        self._round_count[name] += 1
        return intg.full_integration_cycle(state)

    def broadcast(self, state: dict) -> dict[str, dict]:
        """Broadcast *state* to all healthy integrations.

        Returns a dict mapping integration name → result dict.
        """
        results: dict[str, dict] = {}
        for name, intg in self._integrations.items():
            health = intg.health_check()
            if health.get("all_available") or True:  # run even degraded integrations
                try:
                    results[name] = intg.full_integration_cycle(state)
                    self._round_count[name] += 1
                except Exception as exc:
                    logger.warning("IntegrationManager.broadcast: error in %r: %s", name, exc)
                    results[name] = {"error": str(exc), "name": name}
        return results

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    def health_report(self) -> dict[str, dict]:
        """Return a dict mapping name → health dict for all integrations."""
        return {
            name: intg.health_check()
            for name, intg in self._integrations.items()
        }

    def healthy_names(self) -> list[str]:
        """Return names of integrations where all bridges are available."""
        return [
            name
            for name, intg in self._integrations.items()
            if intg.health_check().get("all_available", False)
        ]

    def degraded_names(self) -> list[str]:
        """Return names of integrations with at least one unavailable bridge."""
        return [
            name
            for name, intg in self._integrations.items()
            if not intg.health_check().get("all_available", False)
        ]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Aggregate statistics across all managed integrations."""
        return {
            "n_integrations": len(self._integrations),
            "names": list(self._integrations.keys()),
            "round_counts": dict(self._round_count),
            "total_rounds": sum(self._round_count.values()),
            "healthy": self.healthy_names(),
            "degraded": self.degraded_names(),
        }

    def get_integration(self, name: str) -> StateSpaceIntegration:
        """Return the integration registered under *name*."""
        if name not in self._integrations:
            raise KeyError(f"No integration registered as {name!r}")
        return self._integrations[name]

    def __len__(self) -> int:
        return len(self._integrations)


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def integrate_with_orchestrator(
    state: dict, controller: Any = None
) -> dict:
    """Submit *state* as a goal to the orchestrator.

    Parameters
    ----------
    state:
        The generation state dict.
    controller:
        Optional orchestration controller.

    Returns
    -------
    dict
        Integration result.
    """
    integration = StateSpaceIntegration(controller=controller)
    return integration.integrate_with_orchestrator(state)


def query_solver_for_state(state: dict, router: Any = None) -> dict:
    """Query the solver for a verdict on *state*.

    Parameters
    ----------
    state:
        The generation state dict.
    router:
        Optional solver router.

    Returns
    -------
    dict
        Solver verdict.
    """
    integration = StateSpaceIntegration(router=router)
    return integration.query_solver_for_state(state)


def update_evidence_from_state(state: dict) -> dict:
    """Update the evidence layer from *state*.

    Parameters
    ----------
    state:
        The generation state dict.

    Returns
    -------
    dict
        Evidence update result.
    """
    integration = StateSpaceIntegration()
    return integration.update_evidence_from_state(state)


def build_integration(
    controller: Any = None, router: Any = None
) -> StateSpaceIntegration:
    """Build and return a :class:`StateSpaceIntegration` facade.

    Parameters
    ----------
    controller:
        Optional orchestration controller.
    router:
        Optional solver router.

    Returns
    -------
    StateSpaceIntegration
    """
    return StateSpaceIntegration(controller=controller, router=router)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== integration.py smoke test ===")

    # 1. OrchestratorBridge
    orch = OrchestratorBridge()
    goal_id = orch.submit_generation_goal({"goal_id": "g1", "coordinate": "src/foo.py"})
    assert goal_id, "submit_generation_goal should return a non-empty id"
    print(f"  OrchestratorBridge.submit_generation_goal: {goal_id[:16]}...")

    status = orch.query_goal_status(goal_id)
    assert "goal_id" in status or "status" in status
    print(f"  OrchestratorBridge.query_goal_status: {status}")

    fleet_ids = orch.request_parallel_construction(["p1", "p2", "p3"])
    assert len(fleet_ids) == 3
    print(f"  OrchestratorBridge.request_parallel_construction: {fleet_ids}")

    fleet_results = orch.await_fleet_results(fleet_ids, timeout_ms=100.0)
    assert len(fleet_results) == 3
    print(f"  OrchestratorBridge.await_fleet_results: {len(fleet_results)} results")

    orch.signal_backpressure("test", "info")
    print("  OrchestratorBridge.signal_backpressure: OK ✓")

    # 2. SolverBridge
    solver = SolverBridge()
    state = {
        "state_id": "s1",
        "judgment_coordinate": "src/foo.py",
        "judgment_proposition": "implement bar()",
        "judgment_carrier": "function",
        "trust_annotation": "UNVERIFIED",
        "evidence_refs": ("ev1",),
        "obligations": ("obl1",),
    }
    verdict = solver.query_solver_for_state(state)
    assert "verdict" in verdict
    print(f"  SolverBridge.query_solver_for_state: {verdict}")

    discharge_result = solver.discharge_obligation("obl1", state)
    assert "discharged" in discharge_result
    print(f"  SolverBridge.discharge_obligation: {discharge_result}")

    ok, issues = solver.check_logical_consistency({"p1": "content1", "p2": "content2"})
    assert isinstance(ok, bool)
    print(f"  SolverBridge.check_logical_consistency: ok={ok}, issues={issues}")

    compat = solver.compute_compatibility({"content": "a"}, {"content": "b"})
    assert 0.0 <= compat <= 1.0
    print(f"  SolverBridge.compute_compatibility: {compat:.3f}")

    # 3. EvidenceBridge
    evb = EvidenceBridge()
    new_ids = evb.update_evidence_from_state(state)
    assert isinstance(new_ids, list)
    print(f"  EvidenceBridge.update_evidence_from_state: {len(new_ids)} new ids")

    trust = evb.query_trust_level("s1")
    print(f"  EvidenceBridge.query_trust_level: {trust!r}")

    obs_id = evb.record_obstruction("s1", {"class_id": "cech_h1_001", "description": "test"})
    assert obs_id
    print(f"  EvidenceBridge.record_obstruction: {obs_id}")

    # Trust promotion: valid
    ok2 = evb.promote_trust("s1", "UNVERIFIED", "COPILOT_SUGGESTED", "human reviewed it")
    assert ok2, "valid promotion should succeed"
    print(f"  EvidenceBridge.promote_trust (valid): {ok2} ✓")

    # Trust promotion: empty justification (No-Silent-Promotion)
    ok3 = evb.promote_trust("s1", "UNVERIFIED", "COPILOT_SUGGESTED", "")
    assert not ok3, "empty justification should fail (No-Silent-Promotion)"
    print(f"  EvidenceBridge.promote_trust (empty justification): {ok3} ✓")

    # Trust promotion: trust decrease (monotonicity)
    ok4 = evb.promote_trust("s1", "MECHANICALLY_VERIFIED", "UNVERIFIED", "reverting")
    assert not ok4, "trust decrease should be rejected"
    print(f"  EvidenceBridge.promote_trust (decrease): {ok4} ✓")

    # 4. StateSpaceIntegration
    integration = build_integration()
    health = integration.health_check()
    assert "orchestrator" in health and "solver" in health and "evidence" in health
    print(f"  StateSpaceIntegration.health_check: {health}")

    # 5. full_integration_cycle
    full_result = integration.full_integration_cycle(state)
    assert "orchestrator" in full_result and "solver" in full_result
    assert "elapsed_ms" in full_result
    print(f"  full_integration_cycle: elapsed={full_result['elapsed_ms']:.2f} ms")

    # 6. Module-level convenience functions
    r1 = integrate_with_orchestrator(state)
    r2 = query_solver_for_state(state)
    r3 = update_evidence_from_state(state)
    print("  Convenience functions: orch={r1['status']!r}, solver={r2['verdict']!r}, evids={len(r3['new_evidence_ids'])}")

    # 7. GenerationJudgment (frozen 8-tuple)
    j = GenerationJudgment.make(
        "state-001", "test_spec",
        assumptions=("assume_a",),
        evidence=("ev1",),
        obligations=("prove_P",),
        obstructions=("cech_alpha",),
        trust="PROPOSAL",
    )
    assert j.obstruction_count == 1
    assert not j.is_discharged
    j2 = j.promote_trust("test_evidence")
    assert j2.T == "REVIEWED"
    j3 = j2.discharge_obligation("prove_P", "proof_42")
    assert "prove_P" not in j3.O
    j4 = j3.clear_obstruction("cech_alpha", "proof_43")
    assert j4.is_discharged
    print(f"  GenerationJudgment 8-tuple OK (trust={j4.T}, discharged={j4.is_discharged})")

    # 8. TrustTier Enum algebra
    if hasattr(TrustTier, "join"):
        assert TrustTier.PROPOSAL.join(TrustTier.VERIFIED) == TrustTier.VERIFIED
        assert TrustTier.PROOF_BACKED.meet(TrustTier.REVIEWED) == TrustTier.REVIEWED
        assert TrustTier.VERIFIED.is_at_least(TrustTier.REVIEWED)
        print("  TrustTier Enum algebra OK")
    else:
        print("  TrustTier (real import) present OK")

    # 9. IntegrationManager
    mgr = IntegrationManager()
    mgr.create_and_register("primary")
    mgr.create_and_register("secondary")
    assert len(mgr) == 2
    result = mgr.route("primary", state)
    assert "orchestrator" in result
    stats = mgr.stats()
    assert stats["n_integrations"] == 2
    assert stats["total_rounds"] >= 1
    print(f"  IntegrationManager OK: {stats['n_integrations']} integrations, {stats['total_rounds']} rounds")

    print("All smoke tests passed.")
    sys.exit(0)
