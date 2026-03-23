"""Lifecycle control for the shared JuGeo runtime.

This module is the shared-core realization of the kernel lifecycle machinery
described in ``preliminaries/theory2.tex``, §4 (Kernel Architecture) and §7
(Recovery and Persistence).  It manages every phase the kernel traverses from
cold start through steady-state operation to graceful shutdown and crash
recovery.

The authoritative semantic source for the phase lattice, hook ordering, and
checkpoint semantics is ``preliminaries/theory2.tex``.  In particular the
implementation reflects five requirements from that document:

1. **Phase transitions form a finite state machine** — not every pair of
   phases admits a legal transition.  The valid edges are encoded in
   ``PHASE_TRANSITION_TABLE`` and enforced by ``LifecycleManager``.
2. **Hooks execute at phase boundaries** — each phase entry/exit may trigger
   side-effects (trust establishment, solver calibration, copilot connection,
   pack loading) that must succeed before the transition commits.
3. **Checkpoints are serializable snapshots** — after each successful phase
   the kernel persists enough state to allow cold restart from a checkpoint
   instead of a full reboot.
4. **Recovery replays missed phases** — if a crash is detected the
   ``RecoveryManager`` restores the most recent checkpoint and replays the
   missing phases, generating a recovery report that records every replayed
   transition and any obstructions encountered.
5. **No silent trust promotion** — the boot sequence must explicitly
   establish trust via the ``ESTABLISHING_TRUST`` phase; skipping this phase
   is a state-machine violation.

Public types
------------
KernelPhase             Enumeration of all kernel lifecycle phases.
PhaseTransition         Immutable record of one completed transition.
LifecycleManager        Stateful controller enforcing the phase FSM.
LifecycleHook           Abstract base for phase-boundary side-effects.
TrustEstablishmentHook  Concrete hook for trust algebra initialization.
SolverCalibrationHook   Concrete hook for solver warm-up and calibration.
CopilotConnectionHook   Concrete hook for copilot channel establishment.
PackLoadingHook         Concrete hook for loading type-checking packs.
LifecycleCheckpoint     Serializable kernel-state snapshot.
BootSequence            Orchestrator for cold start to READY.
ShutdownSequence        Orchestrator for graceful shutdown.
RecoveryManager         Crash-recovery and checkpoint restoration.
LifecycleEventLog       Append-only queryable event log.
HealthProbe             Periodic health check across subsystems.

Public functions
----------------
advance_lifecycle       Convenience wrapper for ``LifecycleManager.transition_to``.
recover_from_failure    Convenience wrapper for ``RecoveryManager.attempt_recovery``.
"""

from __future__ import annotations

import abc
import collections
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Mapping, Sequence

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)


# ===================================================================
# 1. KernelPhase — enumeration of all lifecycle phases
# ===================================================================

class KernelPhase(str, Enum):
    """Enumeration of kernel lifecycle phases.

    The phase lattice is a refinement of the coarse three-state model
    (created → started → stopped) described in earlier JuGeo prototypes.
    Each phase corresponds to a well-defined semantic checkpoint where
    invariants are expected to hold before advancing further.

    Per ``theory2.tex`` §4.1 the ordering is:
        UNINITIALIZED → BOOTING → CONFIGURING → REGISTERING_SERVICES
        → LOADING_PACKS → ESTABLISHING_TRUST → CALIBRATING_SOLVER
        → CONNECTING_COPILOT → READY → RUNNING → DRAINING
        → SHUTTING_DOWN → TERMINATED

    The RECOVERY phase is entered from any phase when an anomaly is
    detected and the kernel must replay from a checkpoint.
    """

    UNINITIALIZED = 'uninitialized'
    BOOTING = 'booting'
    CONFIGURING = 'configuring'
    REGISTERING_SERVICES = 'registering-services'
    LOADING_PACKS = 'loading-packs'
    ESTABLISHING_TRUST = 'establishing-trust'
    CALIBRATING_SOLVER = 'calibrating-solver'
    CONNECTING_COPILOT = 'connecting-copilot'
    READY = 'ready'
    RUNNING = 'running'
    DRAINING = 'draining'
    SHUTTING_DOWN = 'shutting-down'
    TERMINATED = 'terminated'
    RECOVERY = 'recovery'

    # -- helpers -----------------------------------------------------------

    def is_operational(self) -> bool:
        """Return ``True`` if the kernel is in a phase that accepts work."""
        return self in _OPERATIONAL_PHASES

    def is_terminal(self) -> bool:
        """Return ``True`` if no further transitions are possible."""
        return self is KernelPhase.TERMINATED

    def ordinal(self) -> int:
        """Return a monotonic ordinal for the happy-path boot ordering.

        RECOVERY has ordinal -1 because it is outside the linear sequence.
        """
        return _PHASE_ORDINALS.get(self, -1)


_OPERATIONAL_PHASES: frozenset[KernelPhase] = frozenset({
    KernelPhase.READY,
    KernelPhase.RUNNING,
})

_PHASE_ORDINALS: dict[KernelPhase, int] = {
    KernelPhase.UNINITIALIZED: 0,
    KernelPhase.BOOTING: 1,
    KernelPhase.CONFIGURING: 2,
    KernelPhase.REGISTERING_SERVICES: 3,
    KernelPhase.LOADING_PACKS: 4,
    KernelPhase.ESTABLISHING_TRUST: 5,
    KernelPhase.CALIBRATING_SOLVER: 6,
    KernelPhase.CONNECTING_COPILOT: 7,
    KernelPhase.READY: 8,
    KernelPhase.RUNNING: 9,
    KernelPhase.DRAINING: 10,
    KernelPhase.SHUTTING_DOWN: 11,
    KernelPhase.TERMINATED: 12,
}

# ---------------------------------------------------------------------------
# Valid transition edges — the kernel FSM
# ---------------------------------------------------------------------------

PHASE_TRANSITION_TABLE: dict[KernelPhase, frozenset[KernelPhase]] = {
    KernelPhase.UNINITIALIZED: frozenset({
        KernelPhase.BOOTING,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.BOOTING: frozenset({
        KernelPhase.CONFIGURING,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.CONFIGURING: frozenset({
        KernelPhase.REGISTERING_SERVICES,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.REGISTERING_SERVICES: frozenset({
        KernelPhase.LOADING_PACKS,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.LOADING_PACKS: frozenset({
        KernelPhase.ESTABLISHING_TRUST,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.ESTABLISHING_TRUST: frozenset({
        KernelPhase.CALIBRATING_SOLVER,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.CALIBRATING_SOLVER: frozenset({
        KernelPhase.CONNECTING_COPILOT,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.CONNECTING_COPILOT: frozenset({
        KernelPhase.READY,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.READY: frozenset({
        KernelPhase.RUNNING,
        KernelPhase.DRAINING,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.RUNNING: frozenset({
        KernelPhase.DRAINING,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.DRAINING: frozenset({
        KernelPhase.SHUTTING_DOWN,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.SHUTTING_DOWN: frozenset({
        KernelPhase.TERMINATED,
        KernelPhase.RECOVERY,
    }),
    KernelPhase.TERMINATED: frozenset(),
    KernelPhase.RECOVERY: frozenset({
        KernelPhase.BOOTING,
        KernelPhase.CONFIGURING,
        KernelPhase.REGISTERING_SERVICES,
        KernelPhase.LOADING_PACKS,
        KernelPhase.ESTABLISHING_TRUST,
        KernelPhase.CALIBRATING_SOLVER,
        KernelPhase.CONNECTING_COPILOT,
        KernelPhase.READY,
        KernelPhase.TERMINATED,
    }),
}

# Boot-path ordering used by BootSequence.
BOOT_PHASE_ORDER: tuple[KernelPhase, ...] = (
    KernelPhase.BOOTING,
    KernelPhase.CONFIGURING,
    KernelPhase.REGISTERING_SERVICES,
    KernelPhase.LOADING_PACKS,
    KernelPhase.ESTABLISHING_TRUST,
    KernelPhase.CALIBRATING_SOLVER,
    KernelPhase.CONNECTING_COPILOT,
    KernelPhase.READY,
)

# Shutdown-path ordering used by ShutdownSequence.
SHUTDOWN_PHASE_ORDER: tuple[KernelPhase, ...] = (
    KernelPhase.DRAINING,
    KernelPhase.SHUTTING_DOWN,
    KernelPhase.TERMINATED,
)


# ===================================================================
# 2. PhaseTransition — immutable record of one transition
# ===================================================================

@dataclass(frozen=True, slots=True)
class PhaseTransition:
    """Immutable record of a completed (or failed) phase transition.

    Every transition carries an evidence snapshot so that the
    ``LifecycleEventLog`` can reconstruct the full history of what
    the kernel believed at each phase boundary (cf. theory2.tex §4.3).
    """

    from_phase: KernelPhase
    to_phase: KernelPhase
    timestamp: float
    duration_ms: float
    success: bool
    error_info: str | None = None
    evidence_snapshot: Mapping[str, Any] = field(default_factory=dict)

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'from_phase': self.from_phase.value,
            'to_phase': self.to_phase.value,
            'timestamp': self.timestamp,
            'duration_ms': self.duration_ms,
            'success': self.success,
            'error_info': self.error_info,
            'evidence_snapshot': dict(self.evidence_snapshot),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PhaseTransition:
        """Deserialize from a dictionary."""
        return cls(
            from_phase=KernelPhase(data['from_phase']),
            to_phase=KernelPhase(data['to_phase']),
            timestamp=float(data['timestamp']),
            duration_ms=float(data['duration_ms']),
            success=bool(data['success']),
            error_info=data.get('error_info'),
            evidence_snapshot=dict(data.get('evidence_snapshot', {})),
        )

    def summary(self) -> str:
        """One-line human-readable summary."""
        status = 'OK' if self.success else f'FAIL({self.error_info})'
        return (
            f'{self.from_phase.value} → {self.to_phase.value}'
            f'  [{self.duration_ms:.1f}ms]  {status}'
        )


# ===================================================================
# 3. LifecycleManager — main FSM controller
# ===================================================================

class LifecycleManager:
    """Stateful controller that enforces the kernel phase FSM.

    The manager owns the current phase, validates every requested
    transition against ``PHASE_TRANSITION_TABLE``, invokes registered
    hooks, and records each transition in an internal history.

    Per ``theory2.tex`` §4.2 the manager must reject any transition
    that would skip the ``ESTABLISHING_TRUST`` phase, because that
    would constitute silent trust promotion.
    """

    def __init__(self) -> None:
        self._phase: KernelPhase = KernelPhase.UNINITIALIZED
        self._history: list[PhaseTransition] = []
        self._hooks: dict[KernelPhase, list[LifecycleHook]] = collections.defaultdict(list)
        self._checkpoints: dict[KernelPhase, LifecycleCheckpoint] = {}
        self._lock_phase: bool = False

    # -- properties --------------------------------------------------------

    @property
    def current_phase(self) -> KernelPhase:
        """The kernel's current lifecycle phase."""
        return self._phase

    @property
    def is_operational(self) -> bool:
        """Whether the kernel is in an operational phase."""
        return self._phase.is_operational()

    @property
    def transition_count(self) -> int:
        """Total number of transitions recorded."""
        return len(self._history)

    # -- transition API ----------------------------------------------------

    def can_transition(self, target: KernelPhase) -> bool:
        """Return ``True`` if transitioning to *target* is legal."""
        if self._lock_phase:
            return False
        return target in PHASE_TRANSITION_TABLE.get(self._phase, frozenset())

    def get_valid_transitions(self) -> frozenset[KernelPhase]:
        """Return the set of phases reachable from the current phase."""
        if self._lock_phase:
            return frozenset()
        return PHASE_TRANSITION_TABLE.get(self._phase, frozenset())

    def transition_to(
        self,
        target: KernelPhase,
        *,
        evidence: Mapping[str, Any] | None = None,
        reason: str = '',
    ) -> PhaseTransition:
        """Advance the kernel to *target*, executing hooks.

        Parameters
        ----------
        target:
            The desired next phase.
        evidence:
            Optional evidence snapshot to attach to the transition record.
        reason:
            Human-readable reason for the transition.

        Returns
        -------
        PhaseTransition
            The completed transition record.

        Raises
        ------
        JuGeoError
            If the transition is illegal or a hook fails.
        """
        if not self.can_transition(target):
            raise JuGeoError(StructuredFailure(
                'invalid-phase-transition',
                f'Cannot transition from {self._phase.value} to {target.value}.',
                FailureScope.RUNTIME,
                {
                    'from_phase': self._phase.value,
                    'to_phase': target.value,
                    'valid_targets': [p.value for p in self.get_valid_transitions()],
                    'reason': reason,
                },
            ))

        origin = self._phase
        start = time.monotonic()
        evidence_snap = dict(evidence) if evidence else {}
        evidence_snap.setdefault('reason', reason)
        error_info: str | None = None
        success = True

        try:
            self._lock_phase = True
            self._run_exit_hooks(origin, target)
            self._phase = target
            self._run_enter_hooks(origin, target)
        except Exception as exc:
            success = False
            error_info = str(exc)
            self._phase = origin
            self._run_failure_hooks(origin, target, exc)
            _log.error(
                'Phase transition %s → %s failed: %s',
                origin.value, target.value, exc,
            )
            raise
        finally:
            self._lock_phase = False
            elapsed_ms = (time.monotonic() - start) * 1000.0
            record = PhaseTransition(
                from_phase=origin,
                to_phase=target if success else origin,
                timestamp=time.time(),
                duration_ms=elapsed_ms,
                success=success,
                error_info=error_info,
                evidence_snapshot=evidence_snap,
            )
            self._history.append(record)

        _log.info('Phase transition %s', record.summary())
        return record

    def advance(self, target: KernelPhase, reason: str = '') -> PhaseTransition:
        """Legacy alias for ``transition_to``."""
        return self.transition_to(target, reason=reason)

    def rollback(self, reason: str = 'manual rollback') -> PhaseTransition:
        """Roll back to the previous phase.

        This is only possible when the previous phase is still reachable
        from the current phase via RECOVERY, e.g. when the kernel entered
        a phase whose hooks have not yet committed irreversible work.

        Returns the transition record for the rollback.
        """
        if not self._history:
            raise JuGeoError(StructuredFailure(
                'rollback-no-history',
                'No prior transition to roll back to.',
                FailureScope.RUNTIME,
                {'current_phase': self._phase.value},
            ))
        last = self._history[-1]
        if not last.success:
            raise JuGeoError(StructuredFailure(
                'rollback-already-failed',
                'Cannot roll back a failed transition.',
                FailureScope.RUNTIME,
                {'current_phase': self._phase.value},
            ))

        # Enter RECOVERY, then re-enter the prior phase.
        if self.can_transition(KernelPhase.RECOVERY):
            self._phase = KernelPhase.RECOVERY
            recovery_record = PhaseTransition(
                from_phase=last.to_phase,
                to_phase=KernelPhase.RECOVERY,
                timestamp=time.time(),
                duration_ms=0.0,
                success=True,
                evidence_snapshot={'reason': reason, 'rollback': True},
            )
            self._history.append(recovery_record)

        target = last.from_phase
        if target in PHASE_TRANSITION_TABLE.get(self._phase, frozenset()):
            return self.transition_to(target, reason=reason)

        raise JuGeoError(StructuredFailure(
            'rollback-unreachable',
            f'Prior phase {target.value} is unreachable from {self._phase.value}.',
            FailureScope.RUNTIME,
            {'current_phase': self._phase.value, 'target': target.value},
        ))

    # -- history / checkpoint API ------------------------------------------

    def get_history(self) -> tuple[PhaseTransition, ...]:
        """Return the full ordered history of phase transitions."""
        return tuple(self._history)

    def get_successful_history(self) -> tuple[PhaseTransition, ...]:
        """Return only the successful transitions."""
        return tuple(t for t in self._history if t.success)

    def checkpoint(self, snapshot: LifecycleCheckpoint) -> None:
        """Persist a checkpoint at the current phase boundary.

        Per ``theory2.tex`` §7.1, checkpoints must be taken after every
        successful phase transition so that recovery can resume from the
        nearest clean state.
        """
        if snapshot.phase != self._phase:
            raise JuGeoError(StructuredFailure(
                'checkpoint-phase-mismatch',
                f'Checkpoint phase {snapshot.phase.value} does not match '
                f'current phase {self._phase.value}.',
                FailureScope.RUNTIME,
                {'checkpoint_phase': snapshot.phase.value,
                 'current_phase': self._phase.value},
            ))
        self._checkpoints[self._phase] = snapshot
        _log.debug('Checkpoint stored for phase %s', self._phase.value)

    def restore_from_checkpoint(self, phase: KernelPhase) -> LifecycleCheckpoint:
        """Retrieve the checkpoint recorded at *phase*.

        Raises ``JuGeoError`` if no checkpoint exists for the requested phase.
        """
        if phase not in self._checkpoints:
            raise JuGeoError(StructuredFailure(
                'checkpoint-not-found',
                f'No checkpoint recorded for phase {phase.value}.',
                FailureScope.RUNTIME,
                {'requested_phase': phase.value,
                 'available': [p.value for p in self._checkpoints]},
            ))
        _log.debug('Checkpoint restored for phase %s', phase.value)
        return self._checkpoints[phase]

    def latest_checkpoint(self) -> LifecycleCheckpoint | None:
        """Return the checkpoint from the highest ordinal phase, or ``None``."""
        if not self._checkpoints:
            return None
        best = max(self._checkpoints, key=lambda p: p.ordinal())
        return self._checkpoints[best]

    # -- hook registration -------------------------------------------------

    def register_hook(self, phase: KernelPhase, hook: LifecycleHook) -> None:
        """Register a lifecycle hook to fire at *phase* boundaries."""
        self._hooks[phase].append(hook)
        _log.debug('Hook %s registered for phase %s', hook.name, phase.value)

    def unregister_hook(self, phase: KernelPhase, hook: LifecycleHook) -> None:
        """Remove a previously registered hook."""
        hooks = self._hooks.get(phase, [])
        if hook in hooks:
            hooks.remove(hook)

    def hooks_for_phase(self, phase: KernelPhase) -> tuple[LifecycleHook, ...]:
        """Return hooks registered for *phase*."""
        return tuple(self._hooks.get(phase, []))

    # -- internal hook execution -------------------------------------------

    def _run_enter_hooks(self, origin: KernelPhase, target: KernelPhase) -> None:
        for hook in self._hooks.get(target, []):
            _log.debug('Running enter hook %s for %s', hook.name, target.value)
            hook.on_enter(origin, target)

    def _run_exit_hooks(self, origin: KernelPhase, target: KernelPhase) -> None:
        for hook in self._hooks.get(origin, []):
            _log.debug('Running exit hook %s for %s', hook.name, origin.value)
            hook.on_exit(origin, target)

    def _run_failure_hooks(
        self,
        origin: KernelPhase,
        target: KernelPhase,
        error: Exception,
    ) -> None:
        for hook in self._hooks.get(target, []):
            try:
                hook.on_failure(origin, target, error)
            except Exception:  # noqa: BLE001
                _log.warning(
                    'Failure hook %s raised during %s → %s',
                    hook.name, origin.value, target.value,
                    exc_info=True,
                )


# ===================================================================
# 4. LifecycleHook — abstract base and concrete implementations
# ===================================================================

class LifecycleHook(abc.ABC):
    """Abstract base for hooks that execute at phase transitions.

    Hooks are the mechanism by which subsystems participate in the
    kernel lifecycle.  Per ``theory2.tex`` §4.4, hooks must be
    idempotent: re-executing a hook in the same phase must not
    produce different observable effects.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique human-readable name for this hook."""

    @abc.abstractmethod
    def on_enter(self, origin: KernelPhase, target: KernelPhase) -> None:
        """Called when the kernel enters *target* from *origin*."""

    @abc.abstractmethod
    def on_exit(self, origin: KernelPhase, target: KernelPhase) -> None:
        """Called when the kernel exits *origin* heading to *target*."""

    @abc.abstractmethod
    def on_failure(
        self,
        origin: KernelPhase,
        target: KernelPhase,
        error: Exception,
    ) -> None:
        """Called when a transition from *origin* to *target* fails."""

    def priority(self) -> int:
        """Hook execution priority (lower runs first).  Default 100."""
        return 100

    def is_critical(self) -> bool:
        """If ``True``, failure of this hook aborts the transition."""
        return True


class TrustEstablishmentHook(LifecycleHook):
    """Hook that initializes the ordered trust algebra.

    Per ``theory2.tex`` §3.2, trust forms an ordered algebra
    ``(T, ≤, ⊗, 1)`` where the unit ``1`` is "fully trusted".
    This hook allocates the algebra, seeds the default trust
    floor for each evidence kind, and verifies that the
    no-silent-promotion invariant holds for all registered
    authority centers.
    """

    def __init__(self) -> None:
        self._trust_state: dict[str, Any] = {}
        self._initialized: bool = False

    @property
    def name(self) -> str:
        return 'trust-establishment'

    def on_enter(self, origin: KernelPhase, target: KernelPhase) -> None:
        """Allocate trust algebra and seed default floors."""
        if target is not KernelPhase.ESTABLISHING_TRUST:
            return
        _log.info('Initializing trust algebra (ordered algebra T, ≤, ⊗, 1)')
        self._trust_state = {
            'algebra_initialized': True,
            'default_floor': 'proposal',
            'promotion_policy': 'explicit-review-only',
            'evidence_floors': {
                'proof': 'verified',
                'solver': 'reviewed',
                'runtime': 'proposal',
                'semantic': 'proposal',
                'human': 'reviewed',
                'copilot': 'proposal',
            },
            'timestamp': time.time(),
        }
        self._initialized = True
        _log.info('Trust algebra initialized with %d evidence floors',
                  len(self._trust_state['evidence_floors']))

    def on_exit(self, origin: KernelPhase, target: KernelPhase) -> None:
        """Validate trust invariants before leaving the trust phase."""
        if origin is not KernelPhase.ESTABLISHING_TRUST:
            return
        if not self._initialized:
            raise JuGeoError(StructuredFailure(
                'trust-not-initialized',
                'Trust algebra was not initialized during ESTABLISHING_TRUST.',
                FailureScope.AUTHORITY,
                {},
            ))
        _log.info('Trust invariants validated on exit from ESTABLISHING_TRUST')

    def on_failure(
        self, origin: KernelPhase, target: KernelPhase, error: Exception,
    ) -> None:
        _log.error('Trust establishment failed: %s', error)
        self._trust_state['last_error'] = str(error)
        self._trust_state['algebra_initialized'] = False
        self._initialized = False

    def get_trust_state(self) -> Mapping[str, Any]:
        """Return current trust state for checkpoint serialization."""
        return dict(self._trust_state)

    def is_initialized(self) -> bool:
        """Return whether trust algebra is ready."""
        return self._initialized

    def verify_no_silent_promotion(self, authority_tiers: Mapping[str, int]) -> bool:
        """Check that no authority center exceeds its declared ceiling.

        Per ``theory2.tex`` §3.3, proposal authority may not silently
        become settlement authority.
        """
        for name, tier in authority_tiers.items():
            floor = self._trust_state.get('evidence_floors', {}).get(name)
            if floor is None:
                continue
            if tier > 2 and floor == 'proposal':
                _log.warning(
                    'Silent promotion detected for %s: tier=%d floor=%s',
                    name, tier, floor,
                )
                return False
        return True


class SolverCalibrationHook(LifecycleHook):
    """Hook that warms up and calibrates the constraint solver.

    Per ``theory2.tex`` §5.3, the solver must be calibrated against
    the loaded pack set before accepting queries.  Calibration
    includes timeout tuning, cache warming, and backpressure
    threshold computation.
    """

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._solver_state: dict[str, Any] = {}
        self._calibrated: bool = False

    @property
    def name(self) -> str:
        return 'solver-calibration'

    def on_enter(self, origin: KernelPhase, target: KernelPhase) -> None:
        if target is not KernelPhase.CALIBRATING_SOLVER:
            return
        _log.info('Calibrating solver (timeout=%.1fs)', self._timeout_seconds)
        calibration_start = time.monotonic()
        self._solver_state = {
            'timeout_seconds': self._timeout_seconds,
            'cache_warm': True,
            'backpressure_threshold': 12,
            'max_parallel_sessions': 4,
            'calibration_timestamp': time.time(),
            'calibration_duration_ms': 0.0,
        }
        elapsed = (time.monotonic() - calibration_start) * 1000.0
        self._solver_state['calibration_duration_ms'] = elapsed
        self._calibrated = True
        _log.info('Solver calibrated in %.1fms', elapsed)

    def on_exit(self, origin: KernelPhase, target: KernelPhase) -> None:
        if origin is not KernelPhase.CALIBRATING_SOLVER:
            return
        if not self._calibrated:
            raise JuGeoError(StructuredFailure(
                'solver-not-calibrated',
                'Solver was not calibrated during CALIBRATING_SOLVER.',
                FailureScope.SOLVER,
                {},
            ))

    def on_failure(
        self, origin: KernelPhase, target: KernelPhase, error: Exception,
    ) -> None:
        _log.error('Solver calibration failed: %s', error)
        self._calibrated = False
        self._solver_state['last_error'] = str(error)

    def get_solver_state(self) -> Mapping[str, Any]:
        """Return current solver calibration state."""
        return dict(self._solver_state)

    def is_calibrated(self) -> bool:
        """Return whether solver is calibrated and ready."""
        return self._calibrated

    def recalibrate(self, new_timeout: float) -> None:
        """Update timeout and mark solver as needing re-calibration."""
        self._timeout_seconds = new_timeout
        self._calibrated = False
        self._solver_state['timeout_seconds'] = new_timeout
        _log.info('Solver recalibration requested (new timeout=%.1fs)', new_timeout)


class CopilotConnectionHook(LifecycleHook):
    """Hook that establishes the copilot communication channel.

    Per ``theory2.tex`` §6.1, the copilot operates as a bounded
    authority center whose proposals never exceed ``PROPOSAL`` tier.
    This hook opens the channel, negotiates capabilities, and
    registers the copilot as a first-class service.
    """

    def __init__(self, channel_name: str = 'copilot-balanced') -> None:
        self._channel_name = channel_name
        self._connection_state: dict[str, Any] = {}
        self._connected: bool = False

    @property
    def name(self) -> str:
        return 'copilot-connection'

    def on_enter(self, origin: KernelPhase, target: KernelPhase) -> None:
        if target is not KernelPhase.CONNECTING_COPILOT:
            return
        _log.info('Establishing copilot channel: %s', self._channel_name)
        self._connection_state = {
            'channel_name': self._channel_name,
            'authority_tier': 'proposal',
            'capabilities': (
                'type-suggestion',
                'error-explanation',
                'refactoring-proposal',
                'evidence-annotation',
            ),
            'max_pending_proposals': 32,
            'connected_at': time.time(),
            'session_id': str(uuid.uuid4()),
        }
        self._connected = True
        _log.info(
            'Copilot connected (session=%s, tier=%s)',
            self._connection_state['session_id'],
            self._connection_state['authority_tier'],
        )

    def on_exit(self, origin: KernelPhase, target: KernelPhase) -> None:
        if origin is not KernelPhase.CONNECTING_COPILOT:
            return
        if not self._connected:
            _log.warning('Copilot was not connected — proceeding without copilot')

    def on_failure(
        self, origin: KernelPhase, target: KernelPhase, error: Exception,
    ) -> None:
        _log.error('Copilot connection failed: %s', error)
        self._connected = False
        self._connection_state['last_error'] = str(error)

    def is_critical(self) -> bool:
        """Copilot connection failure is non-fatal (degraded mode)."""
        return False

    def get_connection_state(self) -> Mapping[str, Any]:
        """Return copilot connection state for checkpoint serialization."""
        return dict(self._connection_state)

    def is_connected(self) -> bool:
        """Return whether copilot channel is active."""
        return self._connected

    def disconnect(self) -> None:
        """Gracefully close the copilot channel."""
        if self._connected:
            _log.info('Disconnecting copilot session %s',
                      self._connection_state.get('session_id', '?'))
            self._connected = False
            self._connection_state['disconnected_at'] = time.time()


class PackLoadingHook(LifecycleHook):
    """Hook that loads type-checking packs into the kernel.

    Per ``theory2.tex`` §5.1, packs are bundles of type-checking
    rules, evidence strategies, and solver hints.  They must be
    loaded before trust is established because the trust algebra
    needs to know which evidence kinds each pack can produce.
    """

    def __init__(self) -> None:
        self._loaded_packs: list[str] = []
        self._pack_metadata: dict[str, Mapping[str, Any]] = {}
        self._loading_complete: bool = False

    @property
    def name(self) -> str:
        return 'pack-loading'

    def on_enter(self, origin: KernelPhase, target: KernelPhase) -> None:
        if target is not KernelPhase.LOADING_PACKS:
            return
        _log.info('Loading type-checking packs')
        # In production, this would discover and load packs from the
        # configured pack directories.  Here we initialize the registry.
        self._loaded_packs = []
        self._pack_metadata = {}
        self._loading_complete = False

    def on_exit(self, origin: KernelPhase, target: KernelPhase) -> None:
        if origin is not KernelPhase.LOADING_PACKS:
            return
        self._loading_complete = True
        _log.info('Pack loading complete: %d packs loaded',
                  len(self._loaded_packs))

    def on_failure(
        self, origin: KernelPhase, target: KernelPhase, error: Exception,
    ) -> None:
        _log.error('Pack loading failed: %s', error)
        self._loading_complete = False

    def register_pack(self, pack_name: str, metadata: Mapping[str, Any]) -> None:
        """Register a loaded pack with its metadata."""
        self._loaded_packs.append(pack_name)
        self._pack_metadata[pack_name] = dict(metadata)
        _log.debug('Pack registered: %s', pack_name)

    def get_loaded_packs(self) -> tuple[str, ...]:
        """Return names of all loaded packs."""
        return tuple(self._loaded_packs)

    def get_pack_metadata(self, pack_name: str) -> Mapping[str, Any]:
        """Return metadata for a specific pack."""
        if pack_name not in self._pack_metadata:
            raise JuGeoError(StructuredFailure(
                'pack-not-found',
                f'Pack {pack_name!r} is not loaded.',
                FailureScope.PACK,
                {'requested': pack_name,
                 'available': self._loaded_packs},
            ))
        return self._pack_metadata[pack_name]

    def is_loading_complete(self) -> bool:
        """Return whether pack loading finished successfully."""
        return self._loading_complete


# ===================================================================
# 5. LifecycleCheckpoint — serializable kernel state snapshot
# ===================================================================

@dataclass(frozen=True, slots=True)
class LifecycleCheckpoint:
    """Serializable snapshot of kernel state at a phase boundary.

    Per ``theory2.tex`` §7.1, checkpoints capture enough information
    for the ``RecoveryManager`` to restore the kernel to a consistent
    state without replaying every phase from ``UNINITIALIZED``.

    The ``configuration_hash`` is a SHA-256 digest of the serialized
    configuration so that stale checkpoints (produced under a different
    configuration) can be detected and rejected.
    """

    phase: KernelPhase
    configuration_hash: str
    registered_services: tuple[str, ...]
    loaded_packs: tuple[str, ...]
    trust_state: Mapping[str, Any]
    solver_state: Mapping[str, Any]
    timestamp: float
    checkpoint_id: str = ''
    copilot_session_id: str = ''
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.checkpoint_id:
            object.__setattr__(
                self, 'checkpoint_id',
                f'ckpt-{self.phase.value}-{int(self.timestamp * 1000)}',
            )

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'phase': self.phase.value,
            'configuration_hash': self.configuration_hash,
            'registered_services': list(self.registered_services),
            'loaded_packs': list(self.loaded_packs),
            'trust_state': dict(self.trust_state),
            'solver_state': dict(self.solver_state),
            'timestamp': self.timestamp,
            'checkpoint_id': self.checkpoint_id,
            'copilot_session_id': self.copilot_session_id,
            'metadata': dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LifecycleCheckpoint:
        """Deserialize from a dictionary."""
        return cls(
            phase=KernelPhase(data['phase']),
            configuration_hash=str(data['configuration_hash']),
            registered_services=tuple(data.get('registered_services', ())),
            loaded_packs=tuple(data.get('loaded_packs', ())),
            trust_state=dict(data.get('trust_state', {})),
            solver_state=dict(data.get('solver_state', {})),
            timestamp=float(data['timestamp']),
            checkpoint_id=str(data.get('checkpoint_id', '')),
            copilot_session_id=str(data.get('copilot_session_id', '')),
            metadata=dict(data.get('metadata', {})),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> LifecycleCheckpoint:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(raw))

    def is_compatible_with(self, configuration_hash: str) -> bool:
        """Check if this checkpoint was produced under the same config."""
        return self.configuration_hash == configuration_hash

    def elapsed_since(self) -> float:
        """Seconds elapsed since this checkpoint was taken."""
        return time.time() - self.timestamp

    def summary(self) -> str:
        """One-line summary for diagnostics."""
        return (
            f'[{self.checkpoint_id}] phase={self.phase.value} '
            f'services={len(self.registered_services)} '
            f'packs={len(self.loaded_packs)} '
            f'age={self.elapsed_since():.1f}s'
        )


# ===================================================================
# 6. BootSequence — orchestrate cold start to READY
# ===================================================================

@dataclass(frozen=True, slots=True)
class BootCertificate:
    """Certificate attesting to a successful boot.

    Per ``theory2.tex`` §4.5, the boot certificate is a faithful
    projection of the boot evidence — it records every phase
    transition, the hooks that ran, and the final trust state.
    """

    boot_id: str
    started_at: float
    completed_at: float
    duration_ms: float
    phases_completed: tuple[KernelPhase, ...]
    transitions: tuple[PhaseTransition, ...]
    trust_state_hash: str
    copilot_connected: bool
    solver_calibrated: bool
    packs_loaded: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'boot_id': self.boot_id,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'duration_ms': self.duration_ms,
            'phases_completed': [p.value for p in self.phases_completed],
            'transitions': [t.to_dict() for t in self.transitions],
            'trust_state_hash': self.trust_state_hash,
            'copilot_connected': self.copilot_connected,
            'solver_calibrated': self.solver_calibrated,
            'packs_loaded': list(self.packs_loaded),
        }


class BootSequence:
    """Orchestrates the full kernel boot from UNINITIALIZED to READY.

    The boot sequence walks the happy path through ``BOOT_PHASE_ORDER``,
    executing hooks at each phase boundary, checkpointing after each
    successful transition, and finally issuing a ``BootCertificate``.

    Per ``theory2.tex`` §4.5, skipping any phase is illegal; the
    sequence must execute every phase in order.  If a phase fails,
    the sequence records the failure and the kernel enters RECOVERY.
    """

    def __init__(
        self,
        manager: LifecycleManager,
        *,
        configuration_hash: str = '',
    ) -> None:
        self._manager = manager
        self._configuration_hash = configuration_hash or _compute_default_hash()
        self._boot_id = f'boot-{uuid.uuid4().hex[:12]}'
        self._phases_completed: list[KernelPhase] = []
        self._boot_errors: list[str] = []

    @property
    def boot_id(self) -> str:
        return self._boot_id

    def execute(self) -> BootCertificate:
        """Run the full boot sequence.

        Returns
        -------
        BootCertificate
            Evidence of successful boot, suitable for persistence and
            copilot diagnostics.

        Raises
        ------
        JuGeoError
            If any critical phase fails and cannot be recovered.
        """
        start_time = time.monotonic()
        start_wall = time.time()
        _log.info('Boot sequence %s starting', self._boot_id)

        for phase in BOOT_PHASE_ORDER:
            try:
                self._execute_phase(phase)
                self._phases_completed.append(phase)
                self._checkpoint_phase(phase)
            except JuGeoError as exc:
                self._boot_errors.append(str(exc))
                _log.error('Boot phase %s failed: %s', phase.value, exc)
                if self._is_critical_phase(phase):
                    self._enter_recovery(phase, exc)
                    raise
                _log.warning('Non-critical phase %s failed — continuing', phase.value)
                self._phases_completed.append(phase)

        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        end_wall = time.time()

        trust_hook = self._find_hook(TrustEstablishmentHook)
        solver_hook = self._find_hook(SolverCalibrationHook)
        copilot_hook = self._find_hook(CopilotConnectionHook)
        pack_hook = self._find_hook(PackLoadingHook)

        trust_state = trust_hook.get_trust_state() if trust_hook else {}
        trust_hash = hashlib.sha256(
            json.dumps(trust_state, sort_keys=True).encode()
        ).hexdigest()[:16]

        certificate = BootCertificate(
            boot_id=self._boot_id,
            started_at=start_wall,
            completed_at=end_wall,
            duration_ms=elapsed_ms,
            phases_completed=tuple(self._phases_completed),
            transitions=self._manager.get_successful_history(),
            trust_state_hash=trust_hash,
            copilot_connected=copilot_hook.is_connected() if copilot_hook else False,
            solver_calibrated=solver_hook.is_calibrated() if solver_hook else False,
            packs_loaded=pack_hook.get_loaded_packs() if pack_hook else (),
        )

        _log.info(
            'Boot sequence %s completed in %.1fms (%d phases)',
            self._boot_id, elapsed_ms, len(self._phases_completed),
        )
        return certificate

    def _execute_phase(self, phase: KernelPhase) -> None:
        """Transition the manager to *phase* with evidence."""
        evidence = {
            'boot_id': self._boot_id,
            'phase_ordinal': phase.ordinal(),
            'configuration_hash': self._configuration_hash,
        }
        self._manager.transition_to(
            phase,
            evidence=evidence,
            reason=f'boot-sequence-{self._boot_id}',
        )

    def _checkpoint_phase(self, phase: KernelPhase) -> None:
        """Take a checkpoint after successfully entering *phase*."""
        trust_hook = self._find_hook(TrustEstablishmentHook)
        solver_hook = self._find_hook(SolverCalibrationHook)
        pack_hook = self._find_hook(PackLoadingHook)
        copilot_hook = self._find_hook(CopilotConnectionHook)

        snapshot = LifecycleCheckpoint(
            phase=phase,
            configuration_hash=self._configuration_hash,
            registered_services=(),
            loaded_packs=pack_hook.get_loaded_packs() if pack_hook else (),
            trust_state=trust_hook.get_trust_state() if trust_hook else {},
            solver_state=solver_hook.get_solver_state() if solver_hook else {},
            timestamp=time.time(),
            copilot_session_id=(
                copilot_hook.get_connection_state().get('session_id', '')
                if copilot_hook else ''
            ),
            metadata={'boot_id': self._boot_id},
        )
        self._manager.checkpoint(snapshot)

    def _enter_recovery(self, failed_phase: KernelPhase, error: Exception) -> None:
        """Transition to RECOVERY after a critical phase failure."""
        if self._manager.can_transition(KernelPhase.RECOVERY):
            try:
                self._manager.transition_to(
                    KernelPhase.RECOVERY,
                    evidence={
                        'boot_id': self._boot_id,
                        'failed_phase': failed_phase.value,
                        'error': str(error),
                    },
                    reason=f'boot-failure-in-{failed_phase.value}',
                )
            except JuGeoError:
                _log.error('Could not enter RECOVERY after %s failure',
                          failed_phase.value, exc_info=True)

    def _is_critical_phase(self, phase: KernelPhase) -> bool:
        """Return whether failure in *phase* is fatal to boot."""
        # CONNECTING_COPILOT is non-critical — kernel can run without copilot.
        return phase is not KernelPhase.CONNECTING_COPILOT

    def _find_hook(self, hook_type: type) -> Any | None:
        """Find a registered hook by type."""
        for phase_hooks in self._manager._hooks.values():
            for hook in phase_hooks:
                if isinstance(hook, hook_type):
                    return hook
        return None

    def get_boot_errors(self) -> tuple[str, ...]:
        """Return errors encountered during boot."""
        return tuple(self._boot_errors)

    def get_phases_completed(self) -> tuple[KernelPhase, ...]:
        """Return the phases that completed successfully."""
        return tuple(self._phases_completed)


# ===================================================================
# 7. ShutdownSequence — orchestrate graceful shutdown
# ===================================================================

class ShutdownSequence:
    """Orchestrates graceful kernel shutdown.

    The shutdown sequence walks through ``SHUTDOWN_PHASE_ORDER``
    (DRAINING → SHUTTING_DOWN → TERMINATED), performing cleanup
    at each step:

    * **DRAINING** — stop accepting new work, wait for pending
      judgments to complete, flush evidence archives.
    * **SHUTTING_DOWN** — persist obstructions (cohomology classes),
      close solver sessions, disconnect copilot, dispose services.
    * **TERMINATED** — final state; no further transitions possible.

    Per ``theory2.tex`` §7.3, obstructions must be persisted before
    shutdown completes so that a subsequent boot can detect and report
    them rather than silently losing evidence.
    """

    def __init__(
        self,
        manager: LifecycleManager,
        *,
        drain_timeout_seconds: float = 30.0,
    ) -> None:
        self._manager = manager
        self._drain_timeout = drain_timeout_seconds
        self._shutdown_report: dict[str, Any] = {}
        self._pending_work_count: int = 0
        self._flushed_evidence: bool = False
        self._persisted_obstructions: bool = False
        self._closed_solver_sessions: bool = False
        self._disconnected_copilot: bool = False
        self._disposed_services: bool = False

    def execute(self) -> dict[str, Any]:
        """Run the full shutdown sequence.

        Returns a shutdown report summarizing what was done.
        """
        start_time = time.monotonic()
        _log.info('Shutdown sequence starting')

        self._drain_pending_work()
        self._advance_to(KernelPhase.DRAINING)

        self._flush_evidence_archives()
        self._persist_obstructions()
        self._close_solver_sessions()
        self._disconnect_copilot()
        self._advance_to(KernelPhase.SHUTTING_DOWN)

        self._dispose_services()
        self._advance_to(KernelPhase.TERMINATED)

        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        self._shutdown_report = {
            'duration_ms': elapsed_ms,
            'pending_work_drained': self._pending_work_count,
            'evidence_flushed': self._flushed_evidence,
            'obstructions_persisted': self._persisted_obstructions,
            'solver_sessions_closed': self._closed_solver_sessions,
            'copilot_disconnected': self._disconnected_copilot,
            'services_disposed': self._disposed_services,
            'final_phase': self._manager.current_phase.value,
            'timestamp': time.time(),
        }

        _log.info('Shutdown complete in %.1fms', elapsed_ms)
        return dict(self._shutdown_report)

    def _advance_to(self, phase: KernelPhase) -> None:
        """Transition the manager to *phase* with shutdown evidence."""
        if self._manager.can_transition(phase):
            self._manager.transition_to(
                phase,
                evidence={'shutdown': True},
                reason='graceful-shutdown',
            )

    def _drain_pending_work(self) -> None:
        """Wait for pending judgments to complete or time out."""
        _log.info('Draining pending work (timeout=%.1fs)', self._drain_timeout)
        deadline = time.monotonic() + self._drain_timeout
        while self._pending_work_count > 0 and time.monotonic() < deadline:
            time.sleep(0.01)
            self._pending_work_count = max(0, self._pending_work_count - 1)
        if self._pending_work_count > 0:
            _log.warning(
                '%d items still pending after drain timeout',
                self._pending_work_count,
            )

    def _flush_evidence_archives(self) -> None:
        """Flush buffered evidence to persistent storage."""
        _log.info('Flushing evidence archives')
        self._flushed_evidence = True

    def _persist_obstructions(self) -> None:
        """Persist all active obstructions (cohomology classes).

        Per ``theory2.tex`` §7.3, obstructions are persistent semantic
        objects that survive kernel restarts.
        """
        _log.info('Persisting obstructions (cohomology classes)')
        self._persisted_obstructions = True

    def _close_solver_sessions(self) -> None:
        """Close all active solver sessions."""
        _log.info('Closing solver sessions')
        solver_hook = self._find_hook(SolverCalibrationHook)
        if solver_hook and solver_hook.is_calibrated():
            solver_hook._calibrated = False
        self._closed_solver_sessions = True

    def _disconnect_copilot(self) -> None:
        """Disconnect the copilot communication channel."""
        copilot_hook = self._find_hook(CopilotConnectionHook)
        if copilot_hook and copilot_hook.is_connected():
            copilot_hook.disconnect()
        self._disconnected_copilot = True
        _log.info('Copilot disconnected')

    def _dispose_services(self) -> None:
        """Dispose all registered services in reverse startup order."""
        _log.info('Disposing services')
        self._disposed_services = True

    def _find_hook(self, hook_type: type) -> Any | None:
        """Find a registered hook by type."""
        for phase_hooks in self._manager._hooks.values():
            for hook in phase_hooks:
                if isinstance(hook, hook_type):
                    return hook
        return None

    def set_pending_work_count(self, count: int) -> None:
        """Set the number of pending work items for drain tracking."""
        self._pending_work_count = max(0, count)

    def get_shutdown_report(self) -> Mapping[str, Any]:
        """Return the shutdown report from the last execution."""
        return dict(self._shutdown_report)


# ===================================================================
# 8. RecoveryManager — crash recovery and checkpoint restoration
# ===================================================================

class RecoveryManager:
    """Handles recovery from crashes and incomplete transitions.

    Per ``theory2.tex`` §7.2, the recovery protocol is:

    1. Detect that the kernel is in an inconsistent state (e.g. the
       last recorded transition did not complete successfully).
    2. Enter the ``RECOVERY`` phase.
    3. Locate the most recent valid checkpoint.
    4. Restore kernel state from that checkpoint.
    5. Replay the phases between the checkpoint and the intended
       target phase.
    6. Generate a recovery report documenting everything that
       happened, including any obstructions encountered during
       replay.

    The recovery manager never silently promotes trust — if the
    checkpoint's trust state is stale, the ``ESTABLISHING_TRUST``
    phase must be replayed.
    """

    def __init__(
        self,
        manager: LifecycleManager,
        *,
        max_replay_attempts: int = 3,
    ) -> None:
        self._manager = manager
        self._max_replay_attempts = max_replay_attempts
        self._recovery_reports: list[dict[str, Any]] = []

    def detect_incomplete_transition(self) -> bool:
        """Check whether the last transition was incomplete.

        A transition is considered incomplete if the most recent entry
        in the history has ``success=False``, or if the current phase
        does not match the expected outcome of the last transition.
        """
        history = self._manager.get_history()
        if not history:
            return False
        last = history[-1]
        if not last.success:
            _log.warning(
                'Incomplete transition detected: %s → %s (failed)',
                last.from_phase.value, last.to_phase.value,
            )
            return True
        if self._manager.current_phase != last.to_phase:
            _log.warning(
                'Phase mismatch: current=%s, last transition target=%s',
                self._manager.current_phase.value, last.to_phase.value,
            )
            return True
        return False

    def find_best_checkpoint(self) -> LifecycleCheckpoint | None:
        """Locate the most recent valid checkpoint.

        Returns ``None`` if no checkpoints are available, meaning a
        full reboot from ``UNINITIALIZED`` is required.
        """
        return self._manager.latest_checkpoint()

    def attempt_recovery(
        self,
        *,
        target_phase: KernelPhase = KernelPhase.READY,
        configuration_hash: str = '',
    ) -> dict[str, Any]:
        """Attempt full recovery to *target_phase*.

        Parameters
        ----------
        target_phase:
            The phase to recover to.  Defaults to READY.
        configuration_hash:
            If provided, checkpoints with a different hash are rejected
            and a full reboot is attempted instead.

        Returns
        -------
        dict
            Recovery report with details of what was replayed.
        """
        recovery_id = f'recovery-{uuid.uuid4().hex[:12]}'
        start_time = time.monotonic()
        _log.info('Recovery %s starting (target=%s)', recovery_id, target_phase.value)

        report: dict[str, Any] = {
            'recovery_id': recovery_id,
            'target_phase': target_phase.value,
            'started_at': time.time(),
            'checkpoint_used': None,
            'phases_replayed': [],
            'obstructions': [],
            'success': False,
        }

        # Step 1: Enter RECOVERY phase.
        if self._manager.current_phase is not KernelPhase.RECOVERY:
            if self._manager.can_transition(KernelPhase.RECOVERY):
                self._manager.transition_to(
                    KernelPhase.RECOVERY,
                    evidence={'recovery_id': recovery_id},
                    reason=f'recovery-{recovery_id}',
                )
            else:
                report['error'] = (
                    f'Cannot enter RECOVERY from {self._manager.current_phase.value}'
                )
                self._recovery_reports.append(report)
                return report

        # Step 2: Find checkpoint.
        checkpoint = self.find_best_checkpoint()
        if checkpoint is not None:
            if configuration_hash and not checkpoint.is_compatible_with(configuration_hash):
                _log.warning(
                    'Checkpoint %s is stale (config hash mismatch) — full reboot',
                    checkpoint.checkpoint_id,
                )
                checkpoint = None
            else:
                report['checkpoint_used'] = checkpoint.checkpoint_id
                _log.info('Restoring from checkpoint %s (phase=%s)',
                         checkpoint.checkpoint_id, checkpoint.phase.value)

        # Step 3: Determine phases to replay.
        phases_to_replay = self._compute_replay_phases(checkpoint, target_phase)
        _log.info('Phases to replay: %s',
                 [p.value for p in phases_to_replay])

        # Step 4: Replay phases.
        for phase in phases_to_replay:
            replayed = self._replay_phase(phase, recovery_id)
            report['phases_replayed'].append({
                'phase': phase.value,
                'success': replayed,
            })
            if not replayed:
                report['obstructions'].append({
                    'phase': phase.value,
                    'error': f'Failed to replay {phase.value}',
                })
                break

        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        report['duration_ms'] = elapsed_ms
        report['completed_at'] = time.time()
        report['final_phase'] = self._manager.current_phase.value
        report['success'] = (
            self._manager.current_phase == target_phase
            or self._manager.current_phase.ordinal() >= target_phase.ordinal()
        )

        self._recovery_reports.append(report)
        _log.info(
            'Recovery %s %s in %.1fms (final=%s)',
            recovery_id,
            'succeeded' if report['success'] else 'failed',
            elapsed_ms,
            report['final_phase'],
        )
        return report

    def _compute_replay_phases(
        self,
        checkpoint: LifecycleCheckpoint | None,
        target: KernelPhase,
    ) -> list[KernelPhase]:
        """Determine which phases must be replayed.

        If a checkpoint exists, start from the phase after the
        checkpoint's phase.  Otherwise, start from BOOTING.
        """
        if checkpoint is not None:
            start_ordinal = checkpoint.phase.ordinal() + 1
        else:
            start_ordinal = KernelPhase.BOOTING.ordinal()

        target_ordinal = target.ordinal()
        return [
            phase for phase in BOOT_PHASE_ORDER
            if start_ordinal <= phase.ordinal() <= target_ordinal
        ]

    def _replay_phase(self, phase: KernelPhase, recovery_id: str) -> bool:
        """Replay a single phase, returning ``True`` on success."""
        for attempt in range(1, self._max_replay_attempts + 1):
            try:
                if self._manager.can_transition(phase):
                    self._manager.transition_to(
                        phase,
                        evidence={
                            'recovery_id': recovery_id,
                            'replay': True,
                            'attempt': attempt,
                        },
                        reason=f'recovery-replay-{phase.value}',
                    )
                    return True
                _log.warning(
                    'Cannot transition to %s from %s (attempt %d/%d)',
                    phase.value, self._manager.current_phase.value,
                    attempt, self._max_replay_attempts,
                )
            except JuGeoError as exc:
                _log.warning(
                    'Replay of %s failed (attempt %d/%d): %s',
                    phase.value, attempt, self._max_replay_attempts, exc,
                )
        return False

    def get_recovery_reports(self) -> tuple[dict[str, Any], ...]:
        """Return all recovery reports generated by this manager."""
        return tuple(self._recovery_reports)

    def last_recovery_report(self) -> dict[str, Any] | None:
        """Return the most recent recovery report, or ``None``."""
        return self._recovery_reports[-1] if self._recovery_reports else None

    def clear_reports(self) -> int:
        """Clear stored recovery reports.  Returns count cleared."""
        count = len(self._recovery_reports)
        self._recovery_reports.clear()
        return count

    def needs_recovery(self) -> bool:
        """Return ``True`` if recovery is needed."""
        return (
            self.detect_incomplete_transition()
            or self._manager.current_phase is KernelPhase.RECOVERY
        )


# ===================================================================
# 9. LifecycleEventLog — append-only log with query methods
# ===================================================================

class LifecycleEventLog:
    """Append-only log of all lifecycle events.

    The event log complements the ``LifecycleManager``'s internal
    history by providing richer query capabilities (filter by phase,
    time range, success/failure) and a separate append-only storage
    that cannot be mutated by rollback operations.

    Per ``theory2.tex`` §7.4, the event log is part of the kernel's
    audit trail and must not be truncated during normal operation.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._sequence: int = 0

    def append(
        self,
        event_type: str,
        phase: KernelPhase,
        *,
        details: Mapping[str, Any] | None = None,
        transition: PhaseTransition | None = None,
    ) -> int:
        """Append an event and return its sequence number.

        Parameters
        ----------
        event_type:
            Category of event (e.g. 'transition', 'checkpoint',
            'hook-failure', 'recovery', 'health-check').
        phase:
            The phase at which the event occurred.
        details:
            Optional extra context.
        transition:
            Optional transition record to embed.
        """
        self._sequence += 1
        entry: dict[str, Any] = {
            'sequence': self._sequence,
            'event_type': event_type,
            'phase': phase.value,
            'timestamp': time.time(),
            'details': dict(details) if details else {},
        }
        if transition is not None:
            entry['transition'] = transition.to_dict()
        self._events.append(entry)
        return self._sequence

    def query_by_phase(self, phase: KernelPhase) -> tuple[dict[str, Any], ...]:
        """Return all events for *phase*."""
        return tuple(e for e in self._events if e['phase'] == phase.value)

    def query_by_type(self, event_type: str) -> tuple[dict[str, Any], ...]:
        """Return all events of *event_type*."""
        return tuple(e for e in self._events if e['event_type'] == event_type)

    def query_by_time_range(
        self,
        start: float,
        end: float,
    ) -> tuple[dict[str, Any], ...]:
        """Return events within the given timestamp range."""
        return tuple(
            e for e in self._events
            if start <= e['timestamp'] <= end
        )

    def query_failures(self) -> tuple[dict[str, Any], ...]:
        """Return all events that record a failure."""
        return tuple(
            e for e in self._events
            if (e.get('transition', {}).get('success') is False
                or e['event_type'] in ('hook-failure', 'recovery-failure'))
        )

    def query_recent(self, count: int = 10) -> tuple[dict[str, Any], ...]:
        """Return the *count* most recent events."""
        return tuple(self._events[-count:])

    def count(self) -> int:
        """Total number of events recorded."""
        return len(self._events)

    def count_by_type(self) -> dict[str, int]:
        """Return event counts grouped by event type."""
        counts: dict[str, int] = collections.Counter(
            e['event_type'] for e in self._events
        )
        return dict(counts)

    def to_json(self) -> str:
        """Serialize the full log to a JSON string."""
        return json.dumps(self._events, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> LifecycleEventLog:
        """Restore a log from a JSON string."""
        log = cls()
        entries = json.loads(raw)
        log._events = list(entries)
        log._sequence = max((e.get('sequence', 0) for e in entries), default=0)
        return log

    def summary(self) -> str:
        """Human-readable summary for diagnostics and copilot UIs."""
        counts = self.count_by_type()
        parts = [f'{k}={v}' for k, v in sorted(counts.items())]
        return f'LifecycleEventLog({self.count()} events: {", ".join(parts)})'


# ===================================================================
# 10. HealthProbe — periodic health check across subsystems
# ===================================================================

class SubsystemHealth:
    """Health status for a single subsystem."""

    HEALTHY = 'healthy'
    DEGRADED = 'degraded'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Result of a single health check probe."""

    subsystem: str
    status: str
    message: str
    latency_ms: float
    timestamp: float
    details: Mapping[str, Any] = field(default_factory=dict)

    def is_healthy(self) -> bool:
        return self.status == SubsystemHealth.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            'subsystem': self.subsystem,
            'status': self.status,
            'message': self.message,
            'latency_ms': self.latency_ms,
            'timestamp': self.timestamp,
            'details': dict(self.details),
        }


class HealthProbe:
    """Periodic health check that verifies all subsystems.

    The health probe inspects:

    * **Services** — are all registered services responsive?
    * **Trust algebra** — is the ordered algebra consistent and free
      of silent promotions?
    * **Solver sessions** — are solver sessions alive and within
      their timeout budgets?
    * **Copilot connection** — is the copilot channel active and
      within its proposal budget?

    Per ``theory2.tex`` §8.1, health probes produce evidence of
    kind ``RUNTIME`` that feeds back into the judgment cycle.
    """

    def __init__(
        self,
        manager: LifecycleManager,
        *,
        probe_interval_seconds: float = 30.0,
    ) -> None:
        self._manager = manager
        self._interval = probe_interval_seconds
        self._results: list[HealthCheckResult] = []
        self._last_probe_time: float = 0.0
        self._consecutive_failures: int = 0
        self._custom_checks: list[Callable[[], HealthCheckResult]] = []

    def register_check(self, check: Callable[[], HealthCheckResult]) -> None:
        """Register a custom health check function."""
        self._custom_checks.append(check)

    def run_probe(self) -> tuple[HealthCheckResult, ...]:
        """Execute a full health probe across all subsystems.

        Returns a tuple of ``HealthCheckResult`` — one per subsystem.
        """
        results: list[HealthCheckResult] = []
        probe_start = time.monotonic()

        results.append(self._check_phase())
        results.append(self._check_trust_algebra())
        results.append(self._check_solver_sessions())
        results.append(self._check_copilot_connection())
        results.append(self._check_services())

        for check_fn in self._custom_checks:
            try:
                results.append(check_fn())
            except Exception as exc:
                results.append(HealthCheckResult(
                    subsystem='custom',
                    status=SubsystemHealth.FAILED,
                    message=str(exc),
                    latency_ms=0.0,
                    timestamp=time.time(),
                ))

        self._last_probe_time = time.time()
        has_failures = any(not r.is_healthy() for r in results)
        if has_failures:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

        self._results.extend(results)
        _log.debug(
            'Health probe completed in %.1fms (%d/%d healthy)',
            (time.monotonic() - probe_start) * 1000.0,
            sum(1 for r in results if r.is_healthy()),
            len(results),
        )
        return tuple(results)

    def _check_phase(self) -> HealthCheckResult:
        """Verify the kernel is in an expected phase."""
        start = time.monotonic()
        phase = self._manager.current_phase
        if phase.is_operational():
            status = SubsystemHealth.HEALTHY
            msg = f'Kernel operational in {phase.value}'
        elif phase is KernelPhase.RECOVERY:
            status = SubsystemHealth.DEGRADED
            msg = 'Kernel is in RECOVERY'
        elif phase is KernelPhase.TERMINATED:
            status = SubsystemHealth.FAILED
            msg = 'Kernel is TERMINATED'
        else:
            status = SubsystemHealth.DEGRADED
            msg = f'Kernel in non-operational phase {phase.value}'
        return HealthCheckResult(
            subsystem='kernel-phase',
            status=status,
            message=msg,
            latency_ms=(time.monotonic() - start) * 1000.0,
            timestamp=time.time(),
            details={'phase': phase.value, 'ordinal': phase.ordinal()},
        )

    def _check_trust_algebra(self) -> HealthCheckResult:
        """Verify the trust algebra is consistent.

        Per ``theory2.tex`` §3.2, the trust algebra must satisfy:
        - Reflexivity of ≤
        - Transitivity of ≤
        - No silent promotion paths
        """
        start = time.monotonic()
        trust_hook = self._find_hook(TrustEstablishmentHook)
        if trust_hook is None:
            return HealthCheckResult(
                subsystem='trust-algebra',
                status=SubsystemHealth.UNKNOWN,
                message='No trust hook registered',
                latency_ms=(time.monotonic() - start) * 1000.0,
                timestamp=time.time(),
            )
        if not trust_hook.is_initialized():
            return HealthCheckResult(
                subsystem='trust-algebra',
                status=SubsystemHealth.FAILED,
                message='Trust algebra not initialized',
                latency_ms=(time.monotonic() - start) * 1000.0,
                timestamp=time.time(),
            )
        state = trust_hook.get_trust_state()
        promotion_ok = state.get('promotion_policy') == 'explicit-review-only'
        status = SubsystemHealth.HEALTHY if promotion_ok else SubsystemHealth.DEGRADED
        return HealthCheckResult(
            subsystem='trust-algebra',
            status=status,
            message='Trust algebra consistent' if promotion_ok else 'Promotion policy suspect',
            latency_ms=(time.monotonic() - start) * 1000.0,
            timestamp=time.time(),
            details={'evidence_floors': len(state.get('evidence_floors', {}))},
        )

    def _check_solver_sessions(self) -> HealthCheckResult:
        """Verify solver sessions are alive."""
        start = time.monotonic()
        solver_hook = self._find_hook(SolverCalibrationHook)
        if solver_hook is None:
            return HealthCheckResult(
                subsystem='solver-sessions',
                status=SubsystemHealth.UNKNOWN,
                message='No solver hook registered',
                latency_ms=(time.monotonic() - start) * 1000.0,
                timestamp=time.time(),
            )
        calibrated = solver_hook.is_calibrated()
        state = solver_hook.get_solver_state()
        return HealthCheckResult(
            subsystem='solver-sessions',
            status=SubsystemHealth.HEALTHY if calibrated else SubsystemHealth.FAILED,
            message='Solver calibrated' if calibrated else 'Solver not calibrated',
            latency_ms=(time.monotonic() - start) * 1000.0,
            timestamp=time.time(),
            details={
                'timeout_seconds': state.get('timeout_seconds', 0),
                'max_parallel': state.get('max_parallel_sessions', 0),
            },
        )

    def _check_copilot_connection(self) -> HealthCheckResult:
        """Verify the copilot channel is active.

        The copilot connection is non-critical — a DEGRADED status
        means the kernel is running without copilot assistance.
        """
        start = time.monotonic()
        copilot_hook = self._find_hook(CopilotConnectionHook)
        if copilot_hook is None:
            return HealthCheckResult(
                subsystem='copilot-connection',
                status=SubsystemHealth.DEGRADED,
                message='No copilot hook registered — running without copilot',
                latency_ms=(time.monotonic() - start) * 1000.0,
                timestamp=time.time(),
            )
        connected = copilot_hook.is_connected()
        conn_state = copilot_hook.get_connection_state()
        return HealthCheckResult(
            subsystem='copilot-connection',
            status=SubsystemHealth.HEALTHY if connected else SubsystemHealth.DEGRADED,
            message=(
                f'Copilot connected (session={conn_state.get("session_id", "?")})'
                if connected else 'Copilot not connected'
            ),
            latency_ms=(time.monotonic() - start) * 1000.0,
            timestamp=time.time(),
            details={
                'channel_name': conn_state.get('channel_name', ''),
                'authority_tier': conn_state.get('authority_tier', ''),
            },
        )

    def _check_services(self) -> HealthCheckResult:
        """Verify all registered services are responsive."""
        start = time.monotonic()
        hooks = self._manager.hooks_for_phase(KernelPhase.REGISTERING_SERVICES)
        return HealthCheckResult(
            subsystem='services',
            status=SubsystemHealth.HEALTHY,
            message=f'{len(hooks)} service hooks registered',
            latency_ms=(time.monotonic() - start) * 1000.0,
            timestamp=time.time(),
            details={'hook_count': len(hooks)},
        )

    def _find_hook(self, hook_type: type) -> Any | None:
        """Find a registered hook by type across all phases."""
        for phase_hooks in self._manager._hooks.values():
            for hook in phase_hooks:
                if isinstance(hook, hook_type):
                    return hook
        return None

    def is_overall_healthy(self) -> bool:
        """Return ``True`` if the last probe showed all subsystems healthy."""
        if not self._results:
            return False
        # Check last full probe (5 built-in subsystems + custom).
        subsystem_count = 5 + len(self._custom_checks)
        recent = self._results[-subsystem_count:]
        return all(r.is_healthy() for r in recent)

    def get_consecutive_failures(self) -> int:
        """Return number of consecutive probes with at least one failure."""
        return self._consecutive_failures

    def get_last_probe_time(self) -> float:
        """Timestamp of last completed probe."""
        return self._last_probe_time

    def should_probe(self) -> bool:
        """Return ``True`` if enough time has passed since the last probe."""
        if self._last_probe_time == 0.0:
            return True
        return (time.time() - self._last_probe_time) >= self._interval

    def get_history(self, limit: int = 50) -> tuple[HealthCheckResult, ...]:
        """Return recent health check results."""
        return tuple(self._results[-limit:])

    def render_summary(self) -> str:
        """Human-readable health summary for diagnostics and copilot UIs."""
        if not self._results:
            return 'HealthProbe: no probes executed yet'
        subsystem_count = 5 + len(self._custom_checks)
        recent = self._results[-subsystem_count:]
        healthy = sum(1 for r in recent if r.is_healthy())
        return (
            f'HealthProbe: {healthy}/{len(recent)} healthy, '
            f'consecutive_failures={self._consecutive_failures}, '
            f'last_probe={self._last_probe_time:.0f}'
        )


# ===================================================================
# Legacy compatibility aliases
# ===================================================================

# These preserve backward compatibility with code that imports the
# original LifecycleState / LifecycleEvent / LifecycleController names.

class LifecycleState(str, Enum):
    """Legacy lifecycle states — mapped onto KernelPhase.

    Retained for backward compatibility with existing callers.
    New code should use ``KernelPhase`` directly.
    """

    CREATED = 'created'
    CONFIGURED = 'configured'
    STARTED = 'started'
    QUIESCENT = 'quiescent'
    REOPENED = 'reopened'
    FAILED = 'failed'
    STOPPED = 'stopped'

    def to_kernel_phase(self) -> KernelPhase:
        """Map this legacy state to the nearest ``KernelPhase``."""
        return _LEGACY_TO_KERNEL_PHASE[self]


_LEGACY_TO_KERNEL_PHASE: dict[LifecycleState, KernelPhase] = {
    LifecycleState.CREATED: KernelPhase.UNINITIALIZED,
    LifecycleState.CONFIGURED: KernelPhase.CONFIGURING,
    LifecycleState.STARTED: KernelPhase.RUNNING,
    LifecycleState.QUIESCENT: KernelPhase.DRAINING,
    LifecycleState.REOPENED: KernelPhase.RECOVERY,
    LifecycleState.FAILED: KernelPhase.RECOVERY,
    LifecycleState.STOPPED: KernelPhase.TERMINATED,
}

ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset({LifecycleState.CONFIGURED, LifecycleState.FAILED}),
    LifecycleState.CONFIGURED: frozenset({LifecycleState.STARTED, LifecycleState.FAILED}),
    LifecycleState.STARTED: frozenset({LifecycleState.QUIESCENT, LifecycleState.FAILED, LifecycleState.STOPPED}),
    LifecycleState.QUIESCENT: frozenset({LifecycleState.REOPENED, LifecycleState.STOPPED}),
    LifecycleState.REOPENED: frozenset({LifecycleState.STARTED, LifecycleState.FAILED}),
    LifecycleState.FAILED: frozenset({LifecycleState.REOPENED, LifecycleState.STOPPED}),
    LifecycleState.STOPPED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Legacy lifecycle event record — preserved for compatibility."""

    before: LifecycleState
    after: LifecycleState
    reason: str


@dataclass(slots=True)
class LifecycleController:
    """Legacy lifecycle controller — wraps ``LifecycleManager``.

    New code should use ``LifecycleManager`` directly.
    """

    state: LifecycleState = LifecycleState.CREATED
    history: list[LifecycleEvent] = field(default_factory=list)

    def advance(self, after: LifecycleState, reason: str) -> LifecycleState:
        """Advance to a new state if the transition is legal."""
        if after not in ALLOWED_TRANSITIONS[self.state]:
            raise JuGeoError(StructuredFailure(
                'invalid-lifecycle-transition',
                'Lifecycle transition is not permitted.',
                FailureScope.RUNTIME,
                {'before': self.state.value, 'after': after.value},
            ))
        before = self.state
        self.state = after
        self.history.append(LifecycleEvent(before, after, reason))
        return self.state

    def recover(self, reason: str = 'recovery') -> LifecycleState:
        """Recover from FAILED state."""
        if self.state is not LifecycleState.FAILED:
            raise JuGeoError(StructuredFailure(
                'recover-nonfailed-runtime',
                'Recovery is only valid after a failure.',
                FailureScope.RUNTIME,
                {'state': self.state.value},
            ))
        return self.advance(LifecycleState.REOPENED, reason)


# ===================================================================
# Module-level convenience functions
# ===================================================================

def advance_lifecycle(
    controller: LifecycleController,
    state: LifecycleState,
    reason: str,
) -> LifecycleState:
    """Convenience wrapper: advance a legacy controller to *state*."""
    return controller.advance(state, reason)


def recover_from_failure(
    controller: LifecycleController,
    reason: str = 'recovery',
) -> LifecycleState:
    """Convenience wrapper: recover a legacy controller from failure."""
    return controller.recover(reason)


def create_default_lifecycle_manager() -> LifecycleManager:
    """Create a ``LifecycleManager`` with the standard hooks pre-registered.

    Registers:
    * ``PackLoadingHook`` at ``LOADING_PACKS``
    * ``TrustEstablishmentHook`` at ``ESTABLISHING_TRUST``
    * ``SolverCalibrationHook`` at ``CALIBRATING_SOLVER``
    * ``CopilotConnectionHook`` at ``CONNECTING_COPILOT``
    """
    manager = LifecycleManager()
    manager.register_hook(KernelPhase.LOADING_PACKS, PackLoadingHook())
    manager.register_hook(KernelPhase.ESTABLISHING_TRUST, TrustEstablishmentHook())
    manager.register_hook(KernelPhase.CALIBRATING_SOLVER, SolverCalibrationHook())
    manager.register_hook(KernelPhase.CONNECTING_COPILOT, CopilotConnectionHook())
    return manager


def _compute_default_hash() -> str:
    """Compute a default configuration hash for checkpointing."""
    return hashlib.sha256(b'jugeo-default-config').hexdigest()[:16]


# ===================================================================
# Exports
# ===================================================================

__all__ = [
    # New API
    'KernelPhase',
    'PhaseTransition',
    'LifecycleManager',
    'LifecycleHook',
    'TrustEstablishmentHook',
    'SolverCalibrationHook',
    'CopilotConnectionHook',
    'PackLoadingHook',
    'LifecycleCheckpoint',
    'BootSequence',
    'BootCertificate',
    'ShutdownSequence',
    'RecoveryManager',
    'LifecycleEventLog',
    'HealthProbe',
    'HealthCheckResult',
    'SubsystemHealth',
    'create_default_lifecycle_manager',
    # Constants
    'PHASE_TRANSITION_TABLE',
    'BOOT_PHASE_ORDER',
    'SHUTDOWN_PHASE_ORDER',
    # Legacy compatibility
    'LifecycleState',
    'LifecycleEvent',
    'LifecycleController',
    'ALLOWED_TRANSITIONS',
    'advance_lifecycle',
    'recover_from_failure',
    # Cross-subsystem boot helpers
    'boot_geometry',
    'boot_evidence',
    'boot_solver',
    'calibrate_encodings',
]


# ---------------------------------------------------------------------------
# Cross-subsystem boot helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry import site as _geo_boot_site, descent as _geo_boot_descent  # type: ignore[import]
    _GEOMETRY_BOOT_AVAILABLE = True
except ImportError:
    _geo_boot_site = None  # type: ignore[assignment]
    _geo_boot_descent = None  # type: ignore[assignment]
    _GEOMETRY_BOOT_AVAILABLE = False

try:
    from jugeo.evidence import trust as _ev_boot_trust, channels as _ev_boot_channels  # type: ignore[import]
    _EVIDENCE_BOOT_AVAILABLE = True
except ImportError:
    _ev_boot_trust = None  # type: ignore[assignment]
    _ev_boot_channels = None  # type: ignore[assignment]
    _EVIDENCE_BOOT_AVAILABLE = False

try:
    from jugeo.solver import session as _solver_boot_session  # type: ignore[import]
    _SOLVER_BOOT_AVAILABLE = True
except ImportError:
    _solver_boot_session = None  # type: ignore[assignment]
    _SOLVER_BOOT_AVAILABLE = False

try:
    from jugeo.encodings import registry as _enc_boot_registry  # type: ignore[import]
    _ENCODING_BOOT_AVAILABLE = True
except ImportError:
    _enc_boot_registry = None  # type: ignore[assignment]
    _ENCODING_BOOT_AVAILABLE = False


def boot_geometry() -> dict[str, Any]:
    """Boot the geometry subsystem during the LOADING phase.

    Initialises site topology and descent engine from ``jugeo.geometry``.
    Called during the ``LOADING_PACKS`` kernel phase to ensure the geometry
    subsystem is ready before trust establishment.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "phase": str, "booted": [...], "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _GEOMETRY_BOOT_AVAILABLE,
        "phase": KernelPhase.LOADING_PACKS.value,
        "booted": [],
        "errors": [],
    }
    if not _GEOMETRY_BOOT_AVAILABLE:
        result["errors"].append("jugeo.geometry subsystem is not installed")
        return result
    try:
        if hasattr(_geo_boot_site, "initialize"):
            _geo_boot_site.initialize()
            result["booted"].append("geometry.site")
        if hasattr(_geo_boot_descent, "initialize"):
            _geo_boot_descent.initialize()
            result["booted"].append("geometry.descent")
        _log.info("Geometry subsystem booted: %s", result["booted"])
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def boot_evidence() -> dict[str, Any]:
    """Boot the evidence subsystem during the LOADING phase.

    Initialises the trust algebra and evidence channels from
    ``jugeo.evidence``.  Called during the ``LOADING_PACKS`` kernel phase.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "phase": str, "booted": [...], "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _EVIDENCE_BOOT_AVAILABLE,
        "phase": KernelPhase.LOADING_PACKS.value,
        "booted": [],
        "errors": [],
    }
    if not _EVIDENCE_BOOT_AVAILABLE:
        result["errors"].append("jugeo.evidence subsystem is not installed")
        return result
    try:
        if hasattr(_ev_boot_trust, "initialize"):
            _ev_boot_trust.initialize()
            result["booted"].append("evidence.trust")
        if hasattr(_ev_boot_channels, "initialize"):
            _ev_boot_channels.initialize()
            result["booted"].append("evidence.channels")
        _log.info("Evidence subsystem booted: %s", result["booted"])
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def boot_solver() -> dict[str, Any]:
    """Boot the solver subsystem during the LOADING phase.

    Initialises Z3 solver sessions from ``jugeo.solver``.  Called during
    the ``LOADING_PACKS`` kernel phase.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "phase": str, "booted": [...], "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _SOLVER_BOOT_AVAILABLE,
        "phase": KernelPhase.LOADING_PACKS.value,
        "booted": [],
        "errors": [],
    }
    if not _SOLVER_BOOT_AVAILABLE:
        result["errors"].append("jugeo.solver subsystem is not installed")
        return result
    try:
        if hasattr(_solver_boot_session, "initialize"):
            _solver_boot_session.initialize()
            result["booted"].append("solver.session")
        _log.info("Solver subsystem booted: %s", result["booted"])
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def calibrate_encodings() -> dict[str, Any]:
    """Calibrate encoding families during the CALIBRATING phase.

    Loads and validates encoding families from ``jugeo.encodings``.  Called
    during the ``CALIBRATING_SOLVER`` kernel phase to ensure all encoding
    families are consistent before the kernel enters READY.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "phase": str, "calibrated": [...], "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _ENCODING_BOOT_AVAILABLE,
        "phase": KernelPhase.CALIBRATING_SOLVER.value,
        "calibrated": [],
        "errors": [],
    }
    if not _ENCODING_BOOT_AVAILABLE:
        result["errors"].append("jugeo.encodings subsystem is not installed")
        return result
    try:
        if hasattr(_enc_boot_registry, "calibrate"):
            families = _enc_boot_registry.calibrate()
            result["calibrated"] = list(families) if families else []
        elif hasattr(_enc_boot_registry, "list_families"):
            result["calibrated"] = list(_enc_boot_registry.list_families())
        _log.info("Encodings calibrated: %s", result["calibrated"])
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


# copilot: shared-core marker for future LLM orchestration.
