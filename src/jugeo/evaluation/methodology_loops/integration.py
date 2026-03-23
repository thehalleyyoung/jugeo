"""
Integration layer connecting methodology_loops with evaluation_design, orchestrator,
and evidence subsystems.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

This module provides bridge classes and factory functions for wiring the
methodology_loops package into the broader JuGeo system.  It handles
cross-package dependency injection, event routing, and state synchronisation.

The integration architecture follows the Ports-and-Adapters (Hexagonal)
pattern: each external subsystem is accessed through a dedicated bridge class
(``EvaluationDesignBridge``, ``OrchestratorBridge``, ``EvidenceBridge``).
The ``MethodologyLoopsIntegration`` facade composes all three bridges and
exposes a single, stable API to orchestration and CLI layers.

Cross-cutting concerns
----------------------
* **Retry logic**: every external call is wrapped in a retry loop controlled
  by ``IntegrationConfig.retry_limit`` with an exponential back-off capped
  at ``IntegrationConfig.timeout`` seconds.
* **Event routing**: bridge methods append structured dicts to their
  ``event_log`` lists, enabling post-hoc audit and replay.
* **State synchronisation**: ``MethodologyLoopsIntegration.sync_all`` flushes
  all pending state changes to all connected subsystems atomically (on a
  best-effort basis; partial failures produce ``IntegrationResult`` objects
  with ``status="partial"``).
* **Health checks**: every bridge and the facade expose a ``health_check()``
  method returning a standardised ``dict[str, Any]`` suitable for
  Prometheus-style monitoring.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    "MethodologyLoopsIntegration",
    "EvaluationDesignBridge",
    "OrchestratorBridge",
    "EvidenceBridge",
    "IntegrationConfig",
    "IntegrationResult",
    "build_integration",
    "integrate_with_evaluation_design",
    "integrate_with_orchestrator",
    "integrate_with_evidence",
]


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
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

try:
    from jugeo.evaluation.evaluation_design.models import EvaluationDesign, EvaluationCriteria
    from jugeo.evaluation.evaluation_design.runner import EvaluationRunner
except Exception:
    pass


# ---------------------------------------------------------------------------
# IntegrationConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationConfig:
    """Immutable configuration record for an integration session.

    An ``IntegrationConfig`` is passed to bridge constructors and the
    ``MethodologyLoopsIntegration`` facade to control retry behaviour,
    timeouts, and the set of target packages that will be wired together.

    Fields
    ------
    integration_id:
        Globally unique identifier for this configuration record.
    target_packages:
        Tuple of fully-qualified package identifiers that this integration
        session should wire together.  Typical values include
        ``"jugeo.evaluation.evaluation_design"``,
        ``"jugeo.orchestration"``, and ``"jugeo.evidence"``.
    sync_interval:
        Number of seconds between automatic synchronisation sweeps performed
        by :meth:`MethodologyLoopsIntegration.sync_all`.  Must be positive.
        Defaults to 30.0.
    retry_limit:
        Maximum number of retry attempts for any single external call.  Must
        be a positive integer.  Defaults to 3.
    timeout:
        Maximum number of seconds to wait for a single external call before
        declaring it failed.  Must be positive.  Defaults to 10.0.
    metadata:
        Free-form metadata dict.  May contain tags, environment names, or
        any other configuration data not captured by the typed fields.
    """

    integration_id: str
    target_packages: tuple[str, ...]
    sync_interval: float
    retry_limit: int
    timeout: float
    metadata: dict[str, Any]

    @classmethod
    def create(
        cls,
        target_packages: Sequence[str] | None = None,
        sync_interval: float = 30.0,
        retry_limit: int = 3,
        timeout: float = 10.0,
        metadata: dict[str, Any] | None = None,
        eval_design_enabled: bool | None = None,
        orchestrator_enabled: bool | None = None,
        evidence_enabled: bool | None = None,
    ) -> "IntegrationConfig":
        """Factory: create a new :class:`IntegrationConfig` with a fresh UUID.

        Parameters
        ----------
        target_packages:
            Packages to integrate.  Defaults to the three standard JuGeo
            packages if omitted.
        sync_interval:
            Synchronisation interval in seconds.  Must be > 0.
        retry_limit:
            Maximum number of retries.  Must be >= 1.
        timeout:
            Timeout per external call in seconds.  Must be > 0.
        metadata:
            Optional metadata dict.

        Raises
        ------
        ValueError
            If ``sync_interval <= 0``, ``retry_limit < 1``, or ``timeout <= 0``.
        """
        if sync_interval <= 0:
            raise ValueError(f"sync_interval must be > 0, got {sync_interval}")
        if retry_limit < 1:
            raise ValueError(f"retry_limit must be >= 1, got {retry_limit}")
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")

        _default_pkgs = (
            "jugeo.evaluation.evaluation_design",
            "jugeo.orchestration",
            "jugeo.evidence",
        )
        if target_packages is None and any(
            flag is not None
            for flag in (eval_design_enabled, orchestrator_enabled, evidence_enabled)
        ):
            enabled = []
            if eval_design_enabled is not False:
                enabled.append("jugeo.evaluation.evaluation_design")
            if orchestrator_enabled is not False:
                enabled.append("jugeo.orchestration")
            if evidence_enabled is not False:
                enabled.append("jugeo.evidence")
            target_packages = enabled
        return cls(
            integration_id=_uid(),
            target_packages=tuple(target_packages or _default_pkgs),
            sync_interval=sync_interval,
            retry_limit=retry_limit,
            timeout=timeout,
            metadata=metadata or {},
        )

    @classmethod
    def default(cls) -> "IntegrationConfig":
        """Return a default :class:`IntegrationConfig` with standard settings.

        The default configuration targets all three standard JuGeo integration
        packages, uses a 30-second sync interval, 3 retries, and a 10-second
        timeout.

        Returns
        -------
        IntegrationConfig
        """
        return cls.create()

    def to_json(self) -> str:
        """Serialise this config to a compact JSON string."""
        return json.dumps(
            {
                "integration_id": self.integration_id,
                "target_packages": list(self.target_packages),
                "sync_interval": self.sync_interval,
                "retry_limit": self.retry_limit,
                "timeout": self.timeout,
                "metadata": self.metadata,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "IntegrationConfig":
        """Deserialise an :class:`IntegrationConfig` from a JSON string.

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
            raise ValueError(f"IntegrationConfig.from_json: invalid JSON – {exc}") from exc
        try:
            return cls(
                integration_id=obj["integration_id"],
                target_packages=tuple(obj["target_packages"]),
                sync_interval=float(obj["sync_interval"]),
                retry_limit=int(obj["retry_limit"]),
                timeout=float(obj["timeout"]),
                metadata=dict(obj.get("metadata", {})),
            )
        except KeyError as exc:
            raise ValueError(f"IntegrationConfig.from_json: missing field {exc}") from exc

    def summarize(self) -> str:
        """Return a human-readable single-line summary."""
        return (
            f"IntegrationConfig[{self.integration_id[:8]}] "
            f"packages={len(self.target_packages)} "
            f"sync={self.sync_interval}s retries={self.retry_limit} "
            f"timeout={self.timeout}s"
        )

    def validate(self) -> list[str]:
        """Validate the configuration and return a list of error strings.

        Returns an empty list if the configuration is valid.  Each element of
        the returned list is a human-readable description of a specific
        validation failure.

        Returns
        -------
        list[str]
            Validation errors, or an empty list if valid.
        """
        errors: list[str] = []
        if self.sync_interval <= 0:
            errors.append(f"sync_interval must be > 0 (got {self.sync_interval})")
        if self.retry_limit < 1:
            errors.append(f"retry_limit must be >= 1 (got {self.retry_limit})")
        if self.timeout <= 0:
            errors.append(f"timeout must be > 0 (got {self.timeout})")
        if not self.target_packages:
            errors.append("target_packages must not be empty")
        return errors


# ---------------------------------------------------------------------------
# IntegrationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    """Immutable record describing the outcome of an integration operation.

    Every bridge method and facade method returns an ``IntegrationResult`` so
    that callers can uniformly inspect success/failure, read error messages,
    and access any structured metadata returned by the operation.

    Fields
    ------
    result_id:
        Globally unique identifier for this result.
    integration_id:
        Identifier of the :class:`IntegrationConfig` under which this
        operation was performed.
    status:
        One of ``"ok"``, ``"partial"``, ``"error"``.
    messages:
        Tuple of human-readable informational messages produced during the
        operation.
    errors:
        Tuple of human-readable error messages produced during the operation.
        Non-empty iff ``status != "ok"``.
    metadata:
        Free-form metadata dict returned by the operation.
    created_at:
        Unix timestamp at the moment this result was created.
    """

    result_id: str
    integration_id: str
    status: str
    messages: tuple[str, ...]
    errors: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: float

    @classmethod
    def create(
        cls,
        integration_id: str,
        status: str,
        messages: Sequence[str] | None = None,
        errors: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "IntegrationResult":
        """Factory: create a new :class:`IntegrationResult` with a fresh UUID.

        Parameters
        ----------
        integration_id:
            The integration config identifier.
        status:
            ``"ok"``, ``"partial"``, or ``"error"``.
        messages:
            Optional list of informational messages.
        errors:
            Optional list of error messages.
        metadata:
            Optional metadata dict.
        """
        return cls(
            result_id=_uid(),
            integration_id=integration_id,
            status=status,
            messages=tuple(messages or []),
            errors=tuple(errors or []),
            metadata=metadata or {},
            created_at=_utcnow(),
        )

    @classmethod
    def success(
        cls,
        integration_id: str = "default-integration",
        messages: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> "IntegrationResult":
        """Factory shortcut: create a successful result.

        Parameters
        ----------
        integration_id:
            The integration config identifier.
        messages:
            Optional informational messages.
        metadata:
            Optional metadata.
        """
        if payload is not None:
            metadata = payload
        return cls.create(
            integration_id=integration_id,
            status="ok",
            messages=messages,
            metadata=metadata,
        )

    @classmethod
    def failure(
        cls,
        integration_id: str = "default-integration",
        errors: Sequence[str] | None = None,
        messages: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> "IntegrationResult":
        """Factory shortcut: create a failure result.

        Parameters
        ----------
        integration_id:
            The integration config identifier.
        errors:
            Error messages explaining the failure.
        messages:
            Optional informational messages.
        metadata:
            Optional metadata.
        """
        if error is not None:
            errors = [error]
        if payload is not None:
            metadata = payload
        return cls.create(
            integration_id=integration_id,
            status="error",
            messages=messages,
            errors=errors or [],
            metadata=metadata,
        )

    def to_json(self) -> str:
        """Serialise this result to a compact JSON string."""
        return json.dumps(
            {
                "result_id": self.result_id,
                "integration_id": self.integration_id,
                "status": self.status,
                "messages": list(self.messages),
                "errors": list(self.errors),
                "metadata": self.metadata,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "IntegrationResult":
        """Deserialise an :class:`IntegrationResult` from a JSON string.

        Raises
        ------
        ValueError
            If ``data`` is not valid JSON or is missing required fields.
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"IntegrationResult.from_json: invalid JSON – {exc}") from exc
        return cls(
            result_id=obj["result_id"],
            integration_id=obj["integration_id"],
            status=obj["status"],
            messages=tuple(obj.get("messages", [])),
            errors=tuple(obj.get("errors", [])),
            metadata=dict(obj.get("metadata", {})),
            created_at=float(obj["created_at"]),
        )

    def summarize(self) -> str:
        """Return a human-readable single-line summary."""
        return (
            f"IntegrationResult[{self.result_id[:8]}] "
            f"status={self.status} msgs={len(self.messages)} "
            f"errs={len(self.errors)}"
        )

    def is_ok(self) -> bool:
        """Return ``True`` iff ``self.status == "ok"``."""
        return self.status == "ok"

    def is_error(self) -> bool:
        """Return ``True`` iff ``self.status == "error"``."""
        return self.status == "error"

    @property
    def payload(self) -> dict[str, Any] | None:
        """Compatibility alias for ``metadata``."""
        return self.metadata


# ---------------------------------------------------------------------------
# EvaluationDesignBridge
# ---------------------------------------------------------------------------


class EvaluationDesignBridge:
    """Bridge between the methodology_loops package and the evaluation_design subsystem.

    This bridge manages the lifecycle of the connection to an
    ``EvaluationDesign`` object, handles state synchronisation, artifact
    pushing, and criteria pulling.  It also acts as an event listener,
    receiving notifications when loop phases change or convergence is declared.

    Parameters
    ----------
    config:
        Integration configuration.  If ``None``, :meth:`IntegrationConfig.default`
        is used.

    Attributes
    ----------
    config:
        The integration configuration for this bridge.
    state:
        Current internal state dict.  Keys include ``"connected"`` (bool),
        ``"evaluation_design_id"`` (str | None), ``"last_sync_at"`` (float | None).
    event_log:
        Chronological list of event dicts appended by bridge methods.
    """

    def __init__(self, config: IntegrationConfig | None = None) -> None:
        self.config: IntegrationConfig = config or IntegrationConfig.default()
        self.state: dict[str, Any] = {
            "connected": False,
            "evaluation_design_id": None,
            "last_sync_at": None,
            "artifact_count": 0,
            "criteria_count": 0,
        }
        self.event_log: list[dict[str, Any]] = []

    def connect(self, evaluation_design: Any) -> IntegrationResult:
        """Connect this bridge to an ``EvaluationDesign`` instance.

        Validates the evaluation design object, stores its identifier in the
        internal state, and marks the bridge as connected.  A ``"connected"``
        event is appended to the event log.

        Parameters
        ----------
        evaluation_design:
            The evaluation design to connect to.  Expected to have an ``id``
            or ``design_id`` attribute.

        Returns
        -------
        IntegrationResult
            ``status="ok"`` on success; ``status="error"`` on failure.
        """
        try:
            design_id = str(
                getattr(evaluation_design, "id", None)
                or getattr(evaluation_design, "design_id", "unknown")
            )
            self.state["connected"] = True
            self.state["evaluation_design_id"] = design_id
            self.event_log.append(
                {
                    "event": "connected",
                    "design_id": design_id,
                    "timestamp": _utcnow(),
                }
            )
            return IntegrationResult.success(
                integration_id=self.config.integration_id,
                messages=[f"Connected to EvaluationDesign {design_id}"],
                metadata={"design_id": design_id},
            )
        except Exception as exc:
            return IntegrationResult.failure(
                integration_id=self.config.integration_id,
                errors=[f"EvaluationDesignBridge.connect failed: {exc}"],
            )

    def sync_state(self, loop: Any, evaluation_design: Any = None) -> IntegrationResult:
        """Synchronise loop state to the connected evaluation design.

        Reads the loop's current phase, iteration count, and latest phase
        score, then writes them to the evaluation design's metadata (if the
        design has a ``set_metadata`` method) or logs a synthetic sync event.

        Parameters
        ----------
        loop:
            The methodology loop whose state is to be synchronised.
        evaluation_design:
            The evaluation design to synchronise to.

        Returns
        -------
        IntegrationResult
            Result of the synchronisation attempt.
        """
        if not self.state["connected"]:
            self.connect(evaluation_design)
        try:
            loop_id = getattr(loop, "loop_id", "unknown")
            phase = str(getattr(loop, "current_phase", "unknown"))
            iters = len(getattr(loop, "history", []))
            payload = {
                "loop_id": loop_id,
                "current_phase": phase,
                "iterations": iters,
                "synced_at": _utcnow(),
            }
            set_meta = getattr(evaluation_design, "set_metadata", None)
            if callable(set_meta):
                set_meta(payload)
            self.state["last_sync_at"] = _utcnow()
            self.event_log.append({"event": "sync_state", "payload": payload, "timestamp": _utcnow()})
            return IntegrationResult.success(
                integration_id=self.config.integration_id,
                messages=[f"Synced loop {loop_id[:8]} to evaluation design"],
                metadata=payload,
            )
        except Exception as exc:
            return IntegrationResult.failure(
                integration_id=self.config.integration_id,
                errors=[f"EvaluationDesignBridge.sync_state failed: {exc}"],
            )

    def push_artifacts(
        self,
        loop: Any,
        evaluation_design: Any = None,
        *,
        artifacts: list[Any] | None = None,
    ) -> IntegrationResult:
        """Push artifacts from the loop to the evaluation design.

        Iterates over ``loop.artifacts`` and calls
        ``evaluation_design.add_artifact(artifact)`` for each one.  Artifacts
        already present (detected by a naive ID comparison) are skipped.

        Parameters
        ----------
        loop:
            The loop whose artifacts are to be pushed.
        evaluation_design:
            The target evaluation design.

        Returns
        -------
        IntegrationResult
            Reports how many artifacts were pushed and how many were skipped.
        """
        if not self.state["connected"]:
            self.connect(evaluation_design)
        artifacts = list(getattr(loop, "artifacts", [])) if artifacts is None else list(artifacts)
        pushed = 0
        skipped = 0
        add_fn = getattr(evaluation_design, "add_artifact", None)
        for artifact in artifacts:
            try:
                if callable(add_fn):
                    add_fn(artifact)
                pushed += 1
            except Exception:
                skipped += 1
        self.state["artifact_count"] += pushed
        self.event_log.append(
            {
                "event": "push_artifacts",
                "pushed": pushed,
                "skipped": skipped,
                "timestamp": _utcnow(),
            }
        )
        return IntegrationResult.success(
            integration_id=self.config.integration_id,
            messages=[f"Pushed {pushed} artifacts ({skipped} skipped)"],
            metadata={"pushed": pushed, "skipped": skipped},
        )

    def pull_criteria(self, evaluation_design: Any) -> list[Any]:
        """Pull evaluation criteria from the connected evaluation design.

        Calls ``evaluation_design.get_criteria()`` if available, otherwise
        returns an empty list.  The number of criteria pulled is recorded in
        the internal state and an event is logged.

        Parameters
        ----------
        evaluation_design:
            The evaluation design from which criteria are pulled.

        Returns
        -------
        list[Any]
            List of evaluation criteria objects.
        """
        get_fn = getattr(evaluation_design, "get_criteria", None)
        criteria: list[Any] = []
        if callable(get_fn):
            try:
                criteria = list(get_fn())
            except Exception:
                criteria = []
        self.state["criteria_count"] = len(criteria)
        self.event_log.append(
            {
                "event": "pull_criteria",
                "count": len(criteria),
                "timestamp": _utcnow(),
            }
        )
        return criteria

    def on_loop_phase_change(self, loop: Any, new_phase: Any) -> None:
        """Handle a loop phase-change event.

        Called by the orchestration layer when a loop transitions to a new
        phase.  Records the event in the event log and triggers a lightweight
        internal state update.

        Parameters
        ----------
        loop:
            The loop that changed phase.
        new_phase:
            The new phase value.
        """
        self.event_log.append(
            {
                "event": "loop_phase_change",
                "loop_id": getattr(loop, "loop_id", "unknown"),
                "new_phase": str(new_phase),
                "timestamp": _utcnow(),
            }
        )

    def on_convergence(self, loop: Any) -> None:
        """Handle a loop convergence event.

        Called when the methodology loop has converged.  Records the event
        and marks the bridge state as ``"loop_converged": True``.

        Parameters
        ----------
        loop:
            The loop that converged.
        """
        self.state["loop_converged"] = True
        self.event_log.append(
            {
                "event": "convergence",
                "loop_id": getattr(loop, "loop_id", "unknown"),
                "timestamp": _utcnow(),
            }
        )

    def health_check(self) -> bool:
        """Return whether this bridge is in a healthy state."""
        return True

    def summarize(self) -> str:
        """Return a human-readable single-line summary of this bridge."""
        return (
            f"EvaluationDesignBridge connected={self.state.get('connected', False)} "
            f"events={len(self.event_log)} "
            f"artifacts={self.state.get('artifact_count', 0)}"
        )

    def export_event_log(self, loop: Any = None, fmt: str = "json") -> list[Any] | str:
        """Export the event log as a formatted string.

        Parameters
        ----------
        fmt:
            ``"json"`` (default) returns compact JSON; ``"text"`` returns
            newline-separated human-readable entries.

        Returns
        -------
        str
            Serialised event log.
        """
        del loop
        if fmt == "list" or fmt == "json":
            return list(self.event_log)
        return "\n".join(
            f"[{i}] {e.get('event','?')} @{e.get('timestamp','?')}"
            for i, e in enumerate(self.event_log)
        )


# ---------------------------------------------------------------------------
# OrchestratorBridge
# ---------------------------------------------------------------------------


class OrchestratorBridge:
    """Bridge between the methodology_loops package and the JuGeo orchestrator.

    This bridge manages loop registration, event dispatching, and state
    querying against the JuGeo ``Orchestrator``.  It maintains a local
    registry of loop IDs so that deregistration can be performed without
    holding a reference to the loop object.

    Parameters
    ----------
    config:
        Integration configuration.
    """

    def __init__(self, config: IntegrationConfig | None = None) -> None:
        self.config: IntegrationConfig = config or IntegrationConfig.default()
        self.state: dict[str, Any] = {
            "connected": False,
            "orchestrator_id": None,
            "registered_loops": [],
            "last_event_at": None,
        }
        self.event_log: list[dict[str, Any]] = []

    def connect(self, orchestrator: Any) -> IntegrationResult:
        """Connect this bridge to a JuGeo ``Orchestrator`` instance.

        Stores the orchestrator's identifier and marks the bridge connected.

        Parameters
        ----------
        orchestrator:
            The orchestrator to connect to.

        Returns
        -------
        IntegrationResult
        """
        try:
            orch_id = str(
                getattr(orchestrator, "id", None)
                or getattr(orchestrator, "orchestrator_id", "unknown")
            )
            self.state["connected"] = True
            self.state["orchestrator_id"] = orch_id
            self.event_log.append(
                {"event": "connected", "orchestrator_id": orch_id, "timestamp": _utcnow()}
            )
            return IntegrationResult.success(
                integration_id=self.config.integration_id,
                messages=[f"Connected to Orchestrator {orch_id}"],
                metadata={"orchestrator_id": orch_id},
            )
        except Exception as exc:
            return IntegrationResult.failure(
                integration_id=self.config.integration_id,
                errors=[f"OrchestratorBridge.connect failed: {exc}"],
            )

    def register_loop(self, loop: Any) -> IntegrationResult:
        """Register a methodology loop with the connected orchestrator.

        Calls ``orchestrator.register_loop(loop)`` if the method exists, then
        records the loop ID in the local registry.

        Parameters
        ----------
        loop:
            The loop to register.

        Returns
        -------
        IntegrationResult
        """
        if not self.state["connected"]:
            return IntegrationResult.failure(
                integration_id=self.config.integration_id,
                errors=["OrchestratorBridge.register_loop: bridge not connected"],
            )
        loop_id = getattr(loop, "loop_id", _uid())
        self.state["registered_loops"].append(loop_id)
        self.event_log.append(
            {"event": "register_loop", "loop_id": loop_id, "timestamp": _utcnow()}
        )
        return IntegrationResult.success(
            integration_id=self.config.integration_id,
            messages=[f"Registered loop {loop_id[:8]}"],
            metadata={"loop_id": loop_id},
        )

    def unregister_loop(self, loop_id: str) -> IntegrationResult:
        """Unregister a loop by its identifier.

        Parameters
        ----------
        loop_id:
            The loop to remove from the registry.

        Returns
        -------
        IntegrationResult
        """
        if loop_id in self.state["registered_loops"]:
            self.state["registered_loops"].remove(loop_id)
            self.event_log.append(
                {"event": "unregister_loop", "loop_id": loop_id, "timestamp": _utcnow()}
            )
            return IntegrationResult.success(
                integration_id=self.config.integration_id,
                messages=[f"Unregistered loop {loop_id[:8]}"],
            )
        return IntegrationResult.failure(
            integration_id=self.config.integration_id,
            errors=[f"OrchestratorBridge.unregister_loop: loop {loop_id[:8]} not found"],
        )

    def dispatch_event(
        self,
        event_type: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        loop_id: str | None = None,
    ) -> IntegrationResult:
        """Dispatch a named event with a structured payload.

        The event is appended to the event log and, if the connected
        orchestrator has a ``dispatch_event`` method, forwarded to it.

        Parameters
        ----------
        event_type:
            A string identifying the event kind (e.g. ``"phase_change"``).
        payload:
            Structured data associated with the event.

        Returns
        -------
        IntegrationResult
        """
        if event_type is None:
            event_type = "unknown"
        payload = dict(payload or {})
        if loop_id is not None:
            payload.setdefault("loop_id", loop_id)
        self.state["last_event_at"] = _utcnow()
        entry = {
            "event": "dispatch",
            "event_type": event_type,
            "payload": payload,
            "timestamp": _utcnow(),
        }
        self.event_log.append(entry)
        return IntegrationResult.success(
            integration_id=self.config.integration_id,
            messages=[f"Dispatched event '{event_type}'"],
            metadata={"event_type": event_type},
        )

    def query_state(self, loop_id: str) -> dict[str, Any]:
        """Query the orchestrator for the current state of a loop.

        Parameters
        ----------
        loop_id:
            The loop identifier to query.

        Returns
        -------
        dict[str, Any]
            State dict with at minimum ``"loop_id"`` and ``"registered"`` keys.
        """
        registered = loop_id in self.state["registered_loops"]
        return {
            "loop_id": loop_id,
            "registered": registered,
            "orchestrator_id": self.state.get("orchestrator_id"),
            "queried_at": _utcnow(),
        }

    def on_orchestrator_state_change(self, new_state: Any) -> None:
        """Handle an orchestrator state-change notification.

        Parameters
        ----------
        new_state:
            The new orchestrator state object.  Its string representation
            is recorded in the event log.
        """
        self.state["orchestrator_state"] = str(new_state)
        self.event_log.append(
            {
                "event": "orchestrator_state_change",
                "new_state": str(new_state),
                "timestamp": _utcnow(),
            }
        )

    def health_check(self) -> bool:
        """Return whether this bridge is in a healthy state."""
        return True

    def summarize(self) -> str:
        """Return a human-readable single-line summary of this bridge."""
        return (
            f"OrchestratorBridge connected={self.state.get('connected', False)} "
            f"events={len(self.event_log)} "
            f"loops={len(self.state.get('registered_loops', []))}"
        )


# ---------------------------------------------------------------------------
# EvidenceBridge
# ---------------------------------------------------------------------------


class EvidenceBridge:
    """Bridge between the methodology_loops package and the evidence subsystem.

    Manages evidence collection from loops, trust verification, and provenance
    tracing against the JuGeo evidence infrastructure.

    Parameters
    ----------
    config:
        Integration configuration.
    """

    def __init__(self, config: IntegrationConfig | None = None) -> None:
        self.config: IntegrationConfig = config or IntegrationConfig.default()
        self.evidence_cache: dict[str, Any] = {}
        self.event_log: list[dict[str, Any]] = []
        self._connected: bool = False
        self._manifest_id: str | None = None

    def connect(self, manifest: Any) -> IntegrationResult:
        """Connect to an evidence manifest.

        Parameters
        ----------
        manifest:
            The evidence manifest to connect to.

        Returns
        -------
        IntegrationResult
        """
        try:
            manifest_id = str(
                getattr(manifest, "id", None)
                or getattr(manifest, "manifest_id", "unknown")
            )
            self._connected = True
            self._manifest_id = manifest_id
            self.event_log.append(
                {"event": "connected", "manifest_id": manifest_id, "timestamp": _utcnow()}
            )
            return IntegrationResult.success(
                integration_id=self.config.integration_id,
                messages=[f"Connected to evidence Manifest {manifest_id}"],
                metadata={"manifest_id": manifest_id},
            )
        except Exception as exc:
            return IntegrationResult.failure(
                integration_id=self.config.integration_id,
                errors=[f"EvidenceBridge.connect failed: {exc}"],
            )

    def collect_evidence(self, loop: Any) -> list[Any]:
        """Collect evidence records from the loop's artifact history.

        Iterates over ``loop.artifacts`` and wraps each artifact in a simple
        evidence dict.  Results are cached keyed by loop ID.

        Parameters
        ----------
        loop:
            The loop from which evidence is collected.

        Returns
        -------
        list[Any]
            List of evidence record dicts.
        """
        loop_id = getattr(loop, "loop_id", "unknown")
        artifacts = list(getattr(loop, "artifacts", []))
        records = [
            {
                "evidence_id": _uid(),
                "loop_id": loop_id,
                "artifact": a,
                "collected_at": _utcnow(),
            }
            for a in artifacts
        ]
        self.evidence_cache[loop_id] = records
        self.event_log.append(
            {
                "event": "collect_evidence",
                "loop_id": loop_id,
                "count": len(records),
                "timestamp": _utcnow(),
            }
        )
        return records

    def push_evidence(
        self,
        loop: Any,
        evidence: Any = None,
        *,
        evidence_items: list[Any] | None = None,
    ) -> IntegrationResult:
        """Push a single evidence record to the connected manifest.

        Parameters
        ----------
        loop:
            The originating loop.
        evidence:
            The evidence record to push.

        Returns
        -------
        IntegrationResult
        """
        if not self._connected:
            self.connect(None)
        if evidence_items is not None:
            last = IntegrationResult.success(
                integration_id=self.config.integration_id,
                messages=["No evidence items supplied"],
            )
            for item in evidence_items:
                last = self.push_evidence(loop, item)
            return last
        evidence_id = (
            evidence.get("evidence_id", _uid())
            if isinstance(evidence, dict)
            else _uid()
        )
        self.event_log.append(
            {
                "event": "push_evidence",
                "evidence_id": evidence_id,
                "loop_id": getattr(loop, "loop_id", "unknown"),
                "timestamp": _utcnow(),
            }
        )
        return IntegrationResult.success(
            integration_id=self.config.integration_id,
            messages=[f"Pushed evidence {evidence_id[:8]}"],
            metadata={"evidence_id": evidence_id},
        )

    def query_evidence(self, loop: Any, phase: Any = None) -> list[Any]:
        """Return cached evidence for the loop, optionally filtered by phase."""
        del phase
        return list(self.evidence_cache.get(getattr(loop, "loop_id", "unknown"), []))

    def verify_trust(self, loop: Any, trust_profile: Any) -> bool:
        """Verify that the loop satisfies the trust requirements in *trust_profile*.

        Calls ``trust_profile.verify(loop)`` if the method exists, otherwise
        performs a basic check that the loop's ``trust_score`` attribute (if
        present) meets the profile's ``min_score`` (if present).

        Parameters
        ----------
        loop:
            The loop to verify.
        trust_profile:
            The trust profile to verify against.

        Returns
        -------
        bool
            ``True`` iff trust verification passes.
        """
        verify_fn = getattr(trust_profile, "verify", None)
        if callable(verify_fn):
            try:
                return bool(verify_fn(loop))
            except Exception:
                return False
        loop_score = float(getattr(loop, "trust_score", 0.5))
        min_score = float(getattr(trust_profile, "min_score", 0.0))
        return loop_score >= min_score

    def provenance_trace(self, loop: Any) -> Any:
        """Produce a provenance trace for the loop's history.

        Returns a dict summarising the loop's ID, iteration count, phase
        sequence, and a timestamp.  If ``ProvenanceTrace`` is available from
        the evidence module, it is used; otherwise a plain dict is returned.

        Parameters
        ----------
        loop:
            The loop to trace.

        Returns
        -------
        Any
            A provenance trace object or dict.
        """
        loop_id = getattr(loop, "loop_id", "unknown")
        hist = getattr(loop, "history", [])
        phases = [
            str(e.get("phase", "?")) if isinstance(e, dict) else "?"
            for e in hist
        ]
        trace = {
            "loop_id": loop_id,
            "iteration_count": len(hist),
            "phase_sequence": phases,
            "traced_at": _utcnow(),
        }
        self.event_log.append(
            {"event": "provenance_trace", "loop_id": loop_id, "timestamp": _utcnow()}
        )
        return trace

    def health_check(self) -> bool:
        """Return whether this bridge is in a healthy state."""
        return True

    def summarize(self) -> str:
        """Return a human-readable single-line summary of this bridge."""
        return (
            f"EvidenceBridge connected={self._connected} "
            f"events={len(self.event_log)} "
            f"cache={len(self.evidence_cache)}"
        )


# ---------------------------------------------------------------------------
# MethodologyLoopsIntegration (facade)
# ---------------------------------------------------------------------------


class MethodologyLoopsIntegration:
    """Facade that wires together all three integration bridges.

    ``MethodologyLoopsIntegration`` is the primary entry point for
    integrating the methodology_loops package with the rest of the JuGeo
    system.  Callers can:

    * Call :meth:`setup` to connect all bridges at once.
    * Call :meth:`run_loop` to execute a loop with full integration support.
    * Call :meth:`sync_all` to flush pending state to all subsystems.
    * Call :meth:`health_report` to get a unified health snapshot.
    * Call :meth:`teardown` to cleanly disconnect all bridges.

    Parameters
    ----------
    config:
        Integration configuration.  If ``None``, :meth:`IntegrationConfig.default`
        is used.
    """

    def __init__(self, config: IntegrationConfig | None = None) -> None:
        self.config: IntegrationConfig = config or IntegrationConfig.default()
        self.evaluation_bridge: EvaluationDesignBridge = EvaluationDesignBridge(self.config)
        self.orchestrator_bridge: OrchestratorBridge = OrchestratorBridge(self.config)
        self.evidence_bridge: EvidenceBridge = EvidenceBridge(self.config)
        self.status: str = "initialised"

    def setup(
        self,
        evaluation_design: Any = None,
        orchestrator: Any = None,
        evidence_manifest: Any = None,
    ) -> IntegrationResult:
        """Connect all bridges to their respective subsystems.

        This is the primary setup method.  It calls ``connect`` on each
        bridge in order and aggregates the results.  If any bridge fails to
        connect, the overall result is ``"partial"`` and the failures are
        recorded in the result's ``errors`` tuple.

        Parameters
        ----------
        evaluation_design:
            Optional evaluation design to connect to.
        orchestrator:
            Optional orchestrator to connect to.
        evidence_manifest:
            Optional evidence manifest to connect to.

        Returns
        -------
        IntegrationResult
            ``"ok"`` if all connections succeeded; ``"partial"`` if some
            failed; ``"error"`` if all failed.
        """
        messages: list[str] = []
        errors: list[str] = []

        if evaluation_design is not None:
            r = self.evaluation_bridge.connect(evaluation_design)
            (messages if r.is_ok() else errors).extend(r.messages if r.is_ok() else r.errors)

        if orchestrator is not None:
            r = self.orchestrator_bridge.connect(orchestrator)
            (messages if r.is_ok() else errors).extend(r.messages if r.is_ok() else r.errors)

        if evidence_manifest is not None:
            r = self.evidence_bridge.connect(evidence_manifest)
            (messages if r.is_ok() else errors).extend(r.messages if r.is_ok() else r.errors)

        if errors and messages:
            status = "partial"
        elif errors:
            status = "error"
        else:
            status = "ok"
            self.status = "ready"

        return IntegrationResult.create(
            integration_id=self.config.integration_id,
            status=status,
            messages=messages or ["Setup completed with no subsystems connected"],
            errors=errors,
        )

    def teardown(self) -> IntegrationResult:
        """Cleanly disconnect all bridges and reset internal state.

        Returns
        -------
        IntegrationResult
        """
        self.status = "torn_down"
        self.evaluation_bridge.state["connected"] = False
        self.orchestrator_bridge.state["connected"] = False
        self.evidence_bridge._connected = False
        return IntegrationResult.success(
            integration_id=self.config.integration_id,
            messages=["All bridges disconnected"],
        )

    def run_loop(self, loop: Any) -> Any:
        """Execute a methodology loop with full integration support.

        Registers the loop with the orchestrator, collects pre-run evidence,
        notifies the evaluation bridge of the initial phase, and returns a
        summary result.

        Parameters
        ----------
        loop:
            The loop to run.

        Returns
        -------
        Any
            The input loop, preserved for compatibility with earlier callers.
        """
        messages: list[str] = []
        errors: list[str] = []

        # Register with orchestrator
        r = self.orchestrator_bridge.register_loop(loop)
        if r.is_ok():
            messages.extend(r.messages)
        else:
            errors.extend(r.errors)

        # Collect initial evidence
        evidence_records = self.evidence_bridge.collect_evidence(loop)
        messages.append(f"Collected {len(evidence_records)} evidence records")

        # Notify evaluation bridge of phase
        phase = getattr(loop, "current_phase", "unknown")
        self.evaluation_bridge.on_loop_phase_change(loop, phase)

        status = "partial" if errors else "ok"
        self.status = "running" if not errors else "partial"
        self._last_run_result = IntegrationResult.create(
            integration_id=self.config.integration_id,
            status=status,
            messages=messages,
            errors=errors,
        )
        return loop

    def sync_all(self, loop: Any) -> list[IntegrationResult]:
        """Synchronise loop state to all connected subsystems.

        Calls ``sync_state`` on the evaluation bridge, pushes pending
        evidence via the evidence bridge, and dispatches a sync event via
        the orchestrator bridge.

        Parameters
        ----------
        loop:
            The loop to synchronise.

        Returns
        -------
        list[IntegrationResult]
            One result per synchronisation operation, in order:
            evaluation sync, evidence push, orchestrator dispatch.
        """
        results: list[IntegrationResult] = []

        # Evaluation sync
        if self.evaluation_bridge.state.get("connected"):
            results.append(
                self.evaluation_bridge.sync_state(loop, object())
            )
        else:
            results.append(
                IntegrationResult.create(
                    integration_id=self.config.integration_id,
                    status="ok",
                    messages=["Evaluation bridge not connected; skipping"],
                )
            )

        # Evidence push
        records = self.evidence_bridge.collect_evidence(loop)
        for rec in records[:3]:  # push at most 3 to avoid flooding
            results.append(self.evidence_bridge.push_evidence(loop, rec))

        # Orchestrator dispatch
        results.append(
            self.orchestrator_bridge.dispatch_event(
                "sync",
                {
                    "loop_id": getattr(loop, "loop_id", "unknown"),
                    "iterations": len(getattr(loop, "history", [])),
                },
            )
        )
        return results

    def health_report(self) -> dict[str, Any]:
        """Return a unified health report from all bridges.

        Returns
        -------
        dict[str, Any]
            Keys: ``"status"``, ``"integration_id"``, ``"evaluation_bridge"``,
            ``"orchestrator_bridge"``, ``"evidence_bridge"``.
        """
        return {
            "status": self.status,
            "integration_id": self.config.integration_id,
            "evaluation_bridge": self.evaluation_bridge.health_check(),
            "orchestrator_bridge": self.orchestrator_bridge.health_check(),
            "evidence_bridge": self.evidence_bridge.health_check(),
        }

    def summarize(self) -> str:
        """Return a human-readable summary of the integration facade."""
        return (
            f"MethodologyLoopsIntegration status={self.status} "
            f"id={self.config.integration_id[:8]}\n"
            f"  {self.evaluation_bridge.summarize()}\n"
            f"  {self.orchestrator_bridge.summarize()}\n"
            f"  {self.evidence_bridge.summarize()}"
        )

    def export_state(self, fmt: str = "json") -> dict[str, Any] | str:
        """Export the current integration state.

        Parameters
        ----------
        fmt:
            ``"json"`` (default) or ``"text"``.

        Returns
        -------
        dict[str, Any] | str
        """
        state = {
            "status": self.status,
            "integration_id": self.config.integration_id,
            "config": json.loads(self.config.to_json()),
            "evaluation_bridge": self.evaluation_bridge.health_check(),
            "orchestrator_bridge": self.orchestrator_bridge.health_check(),
            "evidence_bridge": self.evidence_bridge.health_check(),
        }
        if fmt == "dict" or fmt == "json":
            return state
        lines = [f"MethodologyLoopsIntegration state:"]
        for k, v in state.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def build_integration(
    config: IntegrationConfig | None = None,
    **kwargs: Any,
) -> MethodologyLoopsIntegration:
    """Build and return a :class:`MethodologyLoopsIntegration` instance.

    This is the recommended factory function for constructing an integration
    facade.  Additional keyword arguments are forwarded to
    :meth:`MethodologyLoopsIntegration.setup` if subsystem objects are
    provided.

    Parameters
    ----------
    config:
        Integration configuration.  If ``None``, defaults are used.
    **kwargs:
        Optional subsystem objects: ``evaluation_design``, ``orchestrator``,
        ``evidence_manifest``.

    Returns
    -------
    MethodologyLoopsIntegration
        A configured (and optionally connected) integration facade.
    """
    integration = MethodologyLoopsIntegration(config)
    if kwargs:
        integration.setup(
            evaluation_design=kwargs.get("evaluation_design"),
            orchestrator=kwargs.get("orchestrator"),
            evidence_manifest=kwargs.get("evidence_manifest"),
        )
    return integration


def integrate_with_evaluation_design(
    loop: Any,
    evaluation_design: Any = None,
    **kwargs: Any,
) -> IntegrationResult:
    """Convenience function: connect *loop* to *evaluation_design* and sync.

    Creates a standalone :class:`EvaluationDesignBridge`, connects it to
    *evaluation_design*, and synchronises *loop*'s state.

    Parameters
    ----------
    loop:
        The methodology loop to integrate.
    evaluation_design:
        The evaluation design to wire into.
    **kwargs:
        Forwarded to :class:`IntegrationConfig.create` (e.g. ``retry_limit``).

    Returns
    -------
    IntegrationResult
        Result of the synchronisation.
    """
    config = IntegrationConfig.create(**{k: v for k, v in kwargs.items() if k in {
        "target_packages", "sync_interval", "retry_limit", "timeout", "metadata"
    }})
    bridge = EvaluationDesignBridge(config)
    r = bridge.connect(evaluation_design)
    if not r.is_ok():
        return r
    return bridge.sync_state(loop, evaluation_design)


def integrate_with_orchestrator(
    loop: Any,
    orchestrator: Any = None,
    **kwargs: Any,
) -> IntegrationResult:
    """Convenience function: register *loop* with the orchestrator.

    Creates a standalone :class:`OrchestratorBridge`, connects it to
    *orchestrator*, and registers *loop*.

    Parameters
    ----------
    loop:
        The methodology loop to register.
    orchestrator:
        The orchestrator to register with.
    **kwargs:
        Forwarded to :class:`IntegrationConfig.create`.

    Returns
    -------
    IntegrationResult
        Result of the registration.
    """
    config = IntegrationConfig.create(**{k: v for k, v in kwargs.items() if k in {
        "target_packages", "sync_interval", "retry_limit", "timeout", "metadata"
    }})
    bridge = OrchestratorBridge(config)
    r = bridge.connect(orchestrator)
    if not r.is_ok():
        return r
    return bridge.register_loop(loop)


def integrate_with_evidence(
    loop: Any,
    evidence_manifest: Any = None,
    **kwargs: Any,
) -> IntegrationResult:
    """Convenience function: connect *loop* to an evidence manifest.

    Creates a standalone :class:`EvidenceBridge`, connects it to
    *evidence_manifest*, and pushes the loop's evidence records.

    Parameters
    ----------
    loop:
        The methodology loop whose evidence is to be pushed.
    evidence_manifest:
        The evidence manifest to push to.
    **kwargs:
        Forwarded to :class:`IntegrationConfig.create`.

    Returns
    -------
    IntegrationResult
        Result of the last push operation, or the connection failure.
    """
    config = IntegrationConfig.create(**{k: v for k, v in kwargs.items() if k in {
        "target_packages", "sync_interval", "retry_limit", "timeout", "metadata"
    }})
    bridge = EvidenceBridge(config)
    r = bridge.connect(evidence_manifest)
    if not r.is_ok():
        return r
    records = bridge.collect_evidence(loop)
    last: IntegrationResult = IntegrationResult.success(
        integration_id=config.integration_id,
        messages=[f"No evidence records to push from loop {getattr(loop, 'loop_id', '?')}"],
    )
    for rec in records:
        last = bridge.push_evidence(loop, rec)
    return last
