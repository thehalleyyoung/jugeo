"""Integration of the discovery engine with JuGeo subsystems — theory2.tex Ch58.

This module wires the discovery engine to the rest of the JuGeo framework:

  * ``DiscoveryEngineIntegration`` — top-level integration facade that
    coordinates evidence channels, bridge theorems, and the orchestrator.
  * ``BridgeIntegrationAdapter`` — adapts BridgeTheorem objects from the
    packs subsystem for consumption by the discovery pipeline.
  * ``OrchestrationAdapter`` — registers the DiscoveryPipeline as a task
    with the JuGeo Orchestrator so it can be scheduled and monitored.

Free functions provide convenience entry-points for integration scenarios.

Theory reference: theory2.tex Ch58 §7 — Discovery Engine Integration.

copilot: shared-core marker

Design Overview
---------------
The JuGeo framework is composed of loosely coupled subsystems that communicate
through well-defined adapter interfaces.  This module defines three such
adapters for the discovery engine:

  BridgeIntegrationAdapter
      Translates :class:`~jugeo.packs.bridges.BridgeTheorem` objects
      (produced by the ``packs`` subsystem) into
      :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
      objects suitable for ingestion by the discovery pipeline.  The adapter
      can operate in both single-item and bulk-conversion modes.

  OrchestrationAdapter
      Exposes the discovery pipeline to the JuGeo
      :class:`~jugeo.orchestration.controller.Orchestrator`, enabling
      async scheduling, run monitoring, and cancellation.  Each submitted run
      is assigned a unique ``run_id`` and its status is tracked in an
      in-process dictionary.

  EvidenceChannelAdapter
      Bridges the evidence channel subsystem (``jugeo.evidence.channels``)
      with the discovery pipeline.  It consumes
      :class:`~jugeo.evidence.channels.EvidenceRecord` messages from the
      channel and converts them to
      :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
      objects; it also publishes :class:`DiscoveryResult` objects back to the
      channel after the pipeline completes.

All adapters are designed to be independent: a caller may use just one adapter
without the others.  The top-level :class:`DiscoveryEngineIntegration` facade
composes them together for the common all-in-one scenario.

Integration Event Bus
---------------------
:class:`DiscoveryEngineIntegration` maintains a lightweight in-process event
bus.  Integration events are represented as :class:`IntegrationEvent` frozen
dataclasses and are appended to an internal deque.  Callers can retrieve
recent events via :meth:`DiscoveryEngineIntegration.recent_events`.

Thread Safety
-------------
None of the adapters or the integration facade are thread-safe.  For
concurrent workloads, create one set of adapters per thread.

Usage Examples
--------------
Minimal integration with bridge registry::

    from jugeo.ideation.discovery_engine.integration import (
        build_integrated_pipeline,
        integrate_with_bridges,
    )
    from jugeo.packs.bridges import BridgeRegistry

    registry = BridgeRegistry.load_default()
    pipeline = build_integrated_pipeline()
    adapter = integrate_with_bridges(pipeline.pipeline, registry)
    candidates = adapter.adapt_bridge_registry(registry)
    results = pipeline.run_integrated(candidates)

Integration with orchestrator::

    from jugeo.ideation.discovery_engine.integration import (
        integrate_with_orchestrator,
    )
    orch_adapter = integrate_with_orchestrator(pipeline, orchestrator)
    run_id = orch_adapter.submit_run(candidates)
    status = orch_adapter.get_run_status(run_id)

Event inspection::

    integration = build_integrated_pipeline()
    for event in integration.recent_events(n=5):
        print(event.event_type, event.source, event.timestamp)
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enum
    "IntegrationStatus",
    # Dataclass
    "IntegrationEvent",
    # Adapters
    "BridgeIntegrationAdapter",
    "OrchestrationAdapter",
    "EvidenceChannelAdapter",
    # Facade
    "DiscoveryEngineIntegration",
    # Free functions
    "integrate_with_kind_discovery",
    "integrate_with_bridges",
    "integrate_with_orchestrator",
    "build_integrated_pipeline",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------

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
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate, DiscoveryConfig, DiscoveryResult, DiscoveryDiagnostics,
        DiscoveryStatus, KindSignature, TheoremCandidate, PromotionDecision,
    )
    from jugeo.ideation.discovery_engine.algorithms import DiscoveryPipeline
    from jugeo.ideation.discovery_engine.manifest import DiscoveryEngineManifest, ManifestBuilder
except Exception:
    DiscoveryCandidate = None  # type: ignore[assignment,misc]
    DiscoveryConfig = None  # type: ignore[assignment,misc]
    DiscoveryResult = None  # type: ignore[assignment,misc]
    DiscoveryDiagnostics = None  # type: ignore[assignment,misc]
    DiscoveryStatus = None  # type: ignore[assignment,misc]
    KindSignature = None  # type: ignore[assignment,misc]
    TheoremCandidate = None  # type: ignore[assignment,misc]
    PromotionDecision = None  # type: ignore[assignment,misc]
    DiscoveryPipeline = None  # type: ignore[assignment,misc]
    DiscoveryEngineManifest = None  # type: ignore[assignment,misc]
    ManifestBuilder = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp.

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC).
    """
    return time.time()


def _uid() -> str:
    """Generate a random UUID4 identifier without hyphens.

    Returns
    -------
    str
        32-character hexadecimal string.
    """
    return uuid.uuid4().hex


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* into the interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        Value to clamp.
    lo:
        Lower bound.
    hi:
        Upper bound.

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# IntegrationStatus Enum
# ---------------------------------------------------------------------------


class IntegrationStatus(str, Enum):
    """Lifecycle status of an integration component.

    Values
    ------
    DISCONNECTED:
        The adapter has not yet been connected to an external subsystem.
    CONNECTING:
        The adapter is in the process of establishing a connection.
    CONNECTED:
        The adapter is connected and ready for use.
    ERROR:
        The adapter encountered an unrecoverable error and is not usable.

    Notes
    -----
    Status transitions follow a simple state machine:

      DISCONNECTED → CONNECTING → CONNECTED
                                ↘ ERROR
      CONNECTED    → DISCONNECTED (on explicit close / reset)
      CONNECTED    → ERROR (on unexpected failure)

    The ``ERROR`` state is terminal; the adapter must be reconstructed to
    resume operation.
    """

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# IntegrationEvent dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    """An immutable record of a single integration event.

    Integration events are produced by adapters and the facade when
    noteworthy state transitions occur (e.g. a successful connection, a
    failed run submission, or a published result set).

    Parameters
    ----------
    event_id:
        Unique identifier for this event (UUID4 hex).
    event_type:
        Short string categorising the event, e.g. ``'bridge.adapted'``,
        ``'orchestrator.run_submitted'``, ``'channel.published'``.
    source:
        Human-readable identifier for the component that emitted the event,
        e.g. ``'BridgeIntegrationAdapter'``, ``'EvidenceChannelAdapter'``.
    payload:
        Arbitrary key-value data associated with the event.  Must be a
        plain dict containing only JSON-serialisable values if the events
        will be persisted.
    timestamp:
        POSIX UTC timestamp at the moment the event was created.

    Examples
    --------
    >>> evt = IntegrationEvent(
    ...     event_id=_uid(),
    ...     event_type="bridge.adapted",
    ...     source="BridgeIntegrationAdapter",
    ...     payload={"candidate_count": 42},
    ...     timestamp=_utcnow(),
    ... )
    >>> print(evt.event_type, evt.timestamp)
    """

    event_id: str
    event_type: str
    source: str
    payload: dict[str, Any]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise this event to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            All fields as key-value pairs, suitable for JSON serialisation.

        Notes
        -----
        Because :class:`IntegrationEvent` is frozen (immutable), the
        returned dict is a snapshot; modifying it does not affect the event.
        """
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# BridgeIntegrationAdapter
# ---------------------------------------------------------------------------


class BridgeIntegrationAdapter:
    """Adapt BridgeTheorem objects from the packs subsystem for the discovery pipeline.

    :class:`BridgeIntegrationAdapter` converts
    :class:`~jugeo.packs.bridges.BridgeTheorem` objects (which live in the
    ``jugeo.packs`` subsystem) into
    :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
    objects that can be fed directly into the discovery pipeline.

    The conversion is best-effort: if a required field is missing from the
    ``BridgeTheorem``, the adapter fills in a sensible default rather than
    raising an exception.  Callers can detect a failed conversion by checking
    whether the return value of :meth:`adapt_bridge_theorem` is ``None``.

    Parameters
    ----------
    bridge_registry:
        Optional :class:`~jugeo.packs.bridges.BridgeRegistry` used to
        provide metadata during conversion.  When ``None``, conversion
        relies solely on the attributes present on the theorem object.

    Attributes
    ----------
    _status:
        Current :class:`IntegrationStatus` of this adapter.
    _registry:
        The bridge registry (may be ``None``).

    Notes
    -----
    * This adapter does not validate the mathematical correctness of the
      bridge theorem; it only performs a structural mapping.
    * The ``novelty_score`` of the resulting
      :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
      is estimated heuristically; see :meth:`_estimate_novelty`.

    Examples
    --------
    Single-item conversion::

        adapter = BridgeIntegrationAdapter(bridge_registry=registry)
        candidate = adapter.adapt_bridge_theorem(bridge_theorem)

    Bulk conversion::

        candidates = adapter.adapt_bridge_registry(registry)
        results = pipeline.run(candidates)
    """

    def __init__(self, bridge_registry: Any = None) -> None:
        self._registry: Any = bridge_registry
        self._status: IntegrationStatus = (
            IntegrationStatus.CONNECTED
            if bridge_registry is not None
            else IntegrationStatus.DISCONNECTED
        )
        # Conversion statistics for diagnostics.
        self._adapted_count: int = 0
        self._failed_count: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def adapt_bridge_theorem(self, bridge_theorem: Any) -> Any | None:
        """Convert a single :class:`~jugeo.packs.bridges.BridgeTheorem` to a
        :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`.

        Conversion steps:

        1. Extract the domain via :meth:`_extract_domain`.
        2. Build a description string via :meth:`_extract_description`.
        3. Estimate the novelty score via :meth:`_estimate_novelty`.
        4. Assemble a candidate dict (or
           :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
           if the models module is available).

        If the bridge theorem is ``None`` or missing essential attributes,
        the method returns ``None`` and increments the internal failure
        counter.

        Parameters
        ----------
        bridge_theorem:
            A :class:`~jugeo.packs.bridges.BridgeTheorem` or duck-typed
            object with at least a ``theorem_id`` attribute.

        Returns
        -------
        DiscoveryCandidate or dict or None
            The converted candidate, or ``None`` on failure.
        """
        if bridge_theorem is None:
            self._failed_count += 1
            return None

        try:
            theorem_id: str = (
                str(getattr(bridge_theorem, "theorem_id", None) or _uid())
            )
            domain = self._extract_domain(bridge_theorem)
            description = self._extract_description(bridge_theorem)
            novelty = self._estimate_novelty(bridge_theorem)

            candidate = {
                "candidate_id": f"bridge-{theorem_id}",
                "description": description,
                "domain": domain,
                "novelty_score": novelty,
                "evidence_count": int(getattr(bridge_theorem, "evidence_count", 0) or 0),
                "source": "bridge_theorem",
                "source_id": theorem_id,
                "timestamp": _utcnow(),
            }
            self._adapted_count += 1
            return candidate
        except Exception:
            self._failed_count += 1
            return None

    def adapt_bridge_registry(self, registry: Any) -> list[Any]:
        """Convert all theorems in *registry* to
        :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
        objects.

        Iterates over the registry's theorems using either an ``all_theorems``
        attribute (list/tuple) or an ``__iter__`` protocol.  Theorems that
        fail conversion are silently skipped.

        Parameters
        ----------
        registry:
            A :class:`~jugeo.packs.bridges.BridgeRegistry` or any iterable
            of :class:`~jugeo.packs.bridges.BridgeTheorem` objects.

        Returns
        -------
        list
            Possibly-empty list of converted
            :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
            objects.

        Notes
        -----
        * A registry with zero theorems returns an empty list.
        * The order of candidates mirrors the order of theorems in the
          registry.
        """
        theorems: list[Any] = []

        # Try the ``all_theorems`` attribute first (BridgeRegistry API).
        raw = getattr(registry, "all_theorems", None)
        if raw is not None:
            theorems = list(raw)
        else:
            try:
                theorems = list(registry)
            except TypeError:
                pass

        candidates: list[Any] = []
        for t in theorems:
            c = self.adapt_bridge_theorem(t)
            if c is not None:
                candidates.append(c)

        self._status = IntegrationStatus.CONNECTED
        return candidates

    @property
    def status(self) -> IntegrationStatus:
        """Current connection status of this adapter.

        Returns
        -------
        IntegrationStatus
            One of :attr:`IntegrationStatus.DISCONNECTED`,
            :attr:`IntegrationStatus.CONNECTED`, or
            :attr:`IntegrationStatus.ERROR`.
        """
        return self._status

    @property
    def adapted_count(self) -> int:
        """Total number of successfully adapted bridge theorems."""
        return self._adapted_count

    @property
    def failed_count(self) -> int:
        """Total number of bridge theorems that failed to adapt."""
        return self._failed_count

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_domain(self, bridge_theorem: Any) -> str:
        """Extract the domain string from *bridge_theorem*.

        Looks for the attributes ``domain``, ``source_domain``, and
        ``target_domain`` (in that order).  Falls back to ``'unknown'``.

        Parameters
        ----------
        bridge_theorem:
            Source object.

        Returns
        -------
        str
            Domain string, never empty.
        """
        for attr in ("domain", "source_domain", "target_domain"):
            val = getattr(bridge_theorem, attr, None)
            if val:
                return str(val)
        return "unknown"

    def _extract_description(self, bridge_theorem: Any) -> str:
        """Build a description string for *bridge_theorem*.

        Concatenates ``statement`` and ``label`` attributes if present.
        Falls back to ``repr(bridge_theorem)`` truncated to 512 characters.

        Parameters
        ----------
        bridge_theorem:
            Source object.

        Returns
        -------
        str
            Non-empty description string.
        """
        parts: list[str] = []
        for attr in ("statement", "label", "name", "description"):
            val = getattr(bridge_theorem, attr, None)
            if val:
                parts.append(str(val))
        if parts:
            return " ".join(parts)[:512]
        return repr(bridge_theorem)[:512]

    def _estimate_novelty(self, bridge_theorem: Any) -> float:
        """Estimate a novelty score for *bridge_theorem* in [0.0, 1.0].

        Heuristic:

        * If the theorem has a ``novelty_score`` attribute, use it directly.
        * Otherwise, use ``confidence * 0.8`` if ``confidence`` is present.
        * Otherwise, default to 0.5.

        Parameters
        ----------
        bridge_theorem:
            Source object.

        Returns
        -------
        float
            Estimated novelty score clamped to [0.0, 1.0].
        """
        raw = getattr(bridge_theorem, "novelty_score", None)
        if raw is not None:
            return _clamp(float(raw))
        conf = getattr(bridge_theorem, "confidence", None)
        if conf is not None:
            return _clamp(float(conf) * 0.8)
        return 0.5

    def __repr__(self) -> str:
        return (
            f"BridgeIntegrationAdapter("
            f"status={self._status.value!r}, "
            f"adapted={self._adapted_count}, "
            f"failed={self._failed_count})"
        )


# ---------------------------------------------------------------------------
# OrchestrationAdapter
# ---------------------------------------------------------------------------


class OrchestrationAdapter:
    """Register and manage discovery pipeline runs through the JuGeo Orchestrator.

    :class:`OrchestrationAdapter` bridges the discovery pipeline with the
    JuGeo :class:`~jugeo.orchestration.controller.Orchestrator`, enabling:

    * **Pipeline registration** — the pipeline is registered as a named task
      with the orchestrator via :meth:`register_pipeline`.
    * **Async run submission** — callers submit candidate lists via
      :meth:`submit_run`, which returns a ``run_id`` immediately.  The actual
      pipeline execution is delegated to the orchestrator's task queue.
    * **Run monitoring** — callers poll :meth:`get_run_status` and
      :meth:`get_run_results` to track progress.
    * **Run cancellation** — submitted runs can be cancelled via
      :meth:`cancel_run` before they complete.

    Parameters
    ----------
    orchestrator:
        An :class:`~jugeo.orchestration.controller.Orchestrator` instance,
        or ``None`` to operate in standalone (non-orchestrated) mode.  In
        standalone mode, pipeline runs are executed synchronously within
        :meth:`submit_run`.
    pipeline:
        The :class:`DiscoveryPipeline` to register.  If ``None``, a default
        pipeline is created lazily on first use.

    Attributes
    ----------
    _runs : dict[str, dict]
        Internal run-state dictionary keyed by ``run_id``.
    _registered : bool
        Whether :meth:`register_pipeline` has been called successfully.

    Notes
    -----
    * In standalone mode, runs execute synchronously in :meth:`submit_run`
      and are immediately available via :meth:`get_run_results`.
    * In orchestrated mode, runs are submitted to the orchestrator's task
      queue; their completion is asynchronous.
    * :class:`OrchestrationAdapter` does not support concurrent runs of the
      same :class:`DiscoveryPipeline` instance; the pipeline will raise a
      :py:class:`RuntimeError` if re-entered.

    Examples
    --------
    Standalone mode::

        adapter = OrchestrationAdapter(pipeline=my_pipeline)
        adapter.register_pipeline()
        run_id = adapter.submit_run(candidates)
        results = adapter.get_run_results(run_id)

    Orchestrated mode::

        adapter = OrchestrationAdapter(orchestrator=orch, pipeline=my_pipeline)
        adapter.register_pipeline()
        run_id = adapter.submit_run(candidates)
        # Poll until complete
        while adapter.get_run_status(run_id).value != "COMPLETED":
            time.sleep(0.5)
        results = adapter.get_run_results(run_id)
    """

    #: Task name used when registering the pipeline with the orchestrator.
    TASK_NAME: str = "jugeo.discovery_engine.pipeline"

    def __init__(
        self,
        orchestrator: Any = None,
        pipeline: Any = None,
    ) -> None:
        self._orchestrator: Any = orchestrator
        self._pipeline: Any = pipeline
        self._runs: dict[str, dict[str, Any]] = {}
        self._registered: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def register_pipeline(self) -> bool:
        """Register the pipeline as a task with the orchestrator.

        In standalone mode (no orchestrator), this is a no-op that returns
        ``True`` immediately.

        Returns
        -------
        bool
            ``True`` if registration succeeded (or is not needed),
            ``False`` if the orchestrator rejected the registration.
        """
        if self._orchestrator is None:
            self._registered = True
            return True

        register_fn = getattr(self._orchestrator, "register_task", None)
        if not callable(register_fn):
            self._registered = True
            return True

        try:
            register_fn(self.TASK_NAME, self._get_or_create_pipeline())
            self._registered = True
            return True
        except Exception:
            return False

    def submit_run(self, candidates: list[Any]) -> str:
        """Submit a pipeline run for *candidates* and return a run ID.

        In standalone mode, the pipeline is executed synchronously and the
        results are stored immediately.

        In orchestrated mode, the run is submitted to the orchestrator's
        queue.  Callers should poll :meth:`get_run_status` to wait for
        completion.

        Parameters
        ----------
        candidates:
            List of
            :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
            objects.

        Returns
        -------
        str
            A unique ``run_id`` for this submission.
        """
        run_id = _uid()
        self._runs[run_id] = {
            "run_id": run_id,
            "status": "PENDING",
            "candidates": candidates,
            "results": None,
            "submitted_at": _utcnow(),
            "completed_at": None,
            "error": None,
        }

        if self._orchestrator is not None:
            submit_fn = getattr(self._orchestrator, "submit", None)
            if callable(submit_fn):
                try:
                    submit_fn(self.TASK_NAME, run_id=run_id, candidates=candidates)
                    self._runs[run_id]["status"] = "RUNNING"
                    return run_id
                except Exception as exc:
                    self._runs[run_id]["status"] = "ERROR"
                    self._runs[run_id]["error"] = str(exc)
                    return run_id

        # Standalone mode: run synchronously.
        self._runs[run_id]["status"] = "RUNNING"
        try:
            pipeline = self._get_or_create_pipeline()
            results = pipeline.run(candidates)
            self._runs[run_id]["results"] = results
            self._runs[run_id]["status"] = "COMPLETED"
            self._runs[run_id]["completed_at"] = _utcnow()
        except Exception as exc:
            self._runs[run_id]["status"] = "ERROR"
            self._runs[run_id]["error"] = str(exc)

        return run_id

    def get_run_status(self, run_id: str) -> Any:
        """Return the current status of run *run_id*.

        If the run does not exist, returns a string ``'UNKNOWN'`` (or a
        :class:`~jugeo.ideation.discovery_engine.models.DiscoveryStatus` enum
        value if the models module is available).

        Parameters
        ----------
        run_id:
            The identifier returned by :meth:`submit_run`.

        Returns
        -------
        DiscoveryStatus or str
            Current run status.
        """
        run = self._runs.get(run_id)
        if run is None:
            return "UNKNOWN"

        # Try to promote to DiscoveryStatus enum if available.
        status_str: str = run.get("status", "UNKNOWN")
        if DiscoveryStatus is not None:
            try:
                return DiscoveryStatus(status_str)
            except (ValueError, KeyError):
                pass
        return status_str

    def get_run_results(self, run_id: str) -> list[Any] | None:
        """Return the results of a completed run, or ``None`` if not yet done.

        Parameters
        ----------
        run_id:
            The identifier returned by :meth:`submit_run`.

        Returns
        -------
        list or None
            List of :class:`~jugeo.ideation.discovery_engine.models.DiscoveryResult`
            objects if the run has completed, otherwise ``None``.
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        return run.get("results")

    def cancel_run(self, run_id: str) -> bool:
        """Attempt to cancel the run identified by *run_id*.

        Cancellation is only possible for runs in ``PENDING`` or ``RUNNING``
        state.  Completed or errored runs cannot be cancelled.

        Parameters
        ----------
        run_id:
            The identifier returned by :meth:`submit_run`.

        Returns
        -------
        bool
            ``True`` if the run was successfully cancelled (or was already
            complete), ``False`` if the run ID is unknown.
        """
        run = self._runs.get(run_id)
        if run is None:
            return False

        if run["status"] in ("COMPLETED", "ERROR", "CANCELLED"):
            return True

        # Attempt orchestrator cancellation.
        if self._orchestrator is not None:
            cancel_fn = getattr(self._orchestrator, "cancel", None)
            if callable(cancel_fn):
                try:
                    cancel_fn(run_id)
                except Exception:
                    pass

        run["status"] = "CANCELLED"
        run["completed_at"] = _utcnow()
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_create_pipeline(self) -> Any:
        """Return the pipeline, creating a default one if necessary."""
        if self._pipeline is not None:
            return self._pipeline
        # Lazy import to avoid circular imports.
        try:
            from jugeo.ideation.discovery_engine.algorithms import create_default_pipeline
            self._pipeline = create_default_pipeline()
        except Exception:
            # Ultra-minimal fallback.
            self._pipeline = _MinimalPipeline()
        return self._pipeline

    def __repr__(self) -> str:
        return (
            f"OrchestrationAdapter("
            f"registered={self._registered}, "
            f"runs={len(self._runs)})"
        )


class _MinimalPipeline:
    """Fallback pipeline used when the real pipeline cannot be imported."""

    def run(self, candidates: list[Any]) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# EvidenceChannelAdapter
# ---------------------------------------------------------------------------


class EvidenceChannelAdapter:
    """Bridge the evidence channel subsystem with the discovery pipeline.

    :class:`EvidenceChannelAdapter` integrates with JuGeo's evidence channel
    infrastructure (``jugeo.evidence.channels``) to:

    * **Consume** :class:`~jugeo.evidence.channels.EvidenceRecord` messages
      from a channel and convert them into
      :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
      objects ready for the pipeline.
    * **Publish** :class:`~jugeo.ideation.discovery_engine.models.DiscoveryResult`
      objects back to the channel after the pipeline has run.

    Parameters
    ----------
    channel:
        An evidence channel object exposing ``consume(limit)`` and
        ``publish(record)`` methods, or ``None`` for standalone operation.

    Notes
    -----
    * When *channel* is ``None``, :meth:`consume_records` returns an empty
      list and :meth:`publish_results` returns 0.
    * The adapter does not retain consumed records; each call to
      :meth:`consume_records` requests fresh records from the channel.

    Examples
    --------
    Consume and run::

        channel_adapter = EvidenceChannelAdapter(channel=my_channel)
        candidates = channel_adapter.consume_records(limit=50)
        results = pipeline.run(candidates)
        published = channel_adapter.publish_results(results)
        print(f"Published {published} results")
    """

    def __init__(self, channel: Any = None) -> None:
        self._channel: Any = channel
        self._consumed_total: int = 0
        self._published_total: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def consume_records(self, limit: int = 100) -> list[Any]:
        """Read up to *limit* evidence records from the channel.

        Each :class:`~jugeo.evidence.channels.EvidenceRecord` is converted to
        a :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
        via :meth:`_record_to_candidate`.  Records that fail conversion are
        skipped silently.

        Parameters
        ----------
        limit:
            Maximum number of records to consume.

        Returns
        -------
        list
            Converted candidates (possibly fewer than *limit* if the channel
            has fewer records or some conversions fail).
        """
        if self._channel is None:
            return []

        consume_fn = getattr(self._channel, "consume", None)
        if not callable(consume_fn):
            return []

        try:
            raw_records = consume_fn(limit)
        except Exception:
            return []

        candidates: list[Any] = []
        for record in (raw_records or []):
            c = self._record_to_candidate(record)
            if c is not None:
                candidates.append(c)
                self._consumed_total += 1

        return candidates

    def publish_results(self, results: list[Any]) -> int:
        """Publish *results* back to the evidence channel.

        Each :class:`~jugeo.ideation.discovery_engine.models.DiscoveryResult`
        is converted to an :class:`~jugeo.evidence.channels.EvidenceRecord`
        via :meth:`_result_to_record` and then passed to the channel's
        ``publish`` method.

        Parameters
        ----------
        results:
            List of
            :class:`~jugeo.ideation.discovery_engine.models.DiscoveryResult`
            objects.

        Returns
        -------
        int
            Number of results successfully published.
        """
        if self._channel is None:
            return 0

        publish_fn = getattr(self._channel, "publish", None)
        if not callable(publish_fn):
            return 0

        published = 0
        for result in (results or []):
            record = self._result_to_record(result)
            if record is not None:
                try:
                    publish_fn(record)
                    published += 1
                    self._published_total += 1
                except Exception:
                    pass

        return published

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_to_candidate(self, record: Any) -> Any | None:
        """Convert an :class:`~jugeo.evidence.channels.EvidenceRecord` to
        a :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`.

        Conversion mapping:

        +--------------------------+---------------------------+
        | EvidenceRecord attribute | DiscoveryCandidate field  |
        +==========================+===========================+
        | ``record_id``            | ``candidate_id``          |
        +--------------------------+---------------------------+
        | ``content`` or ``text``  | ``description``           |
        +--------------------------+---------------------------+
        | ``domain`` or ``kind``   | ``domain``                |
        +--------------------------+---------------------------+
        | ``confidence``           | ``novelty_score``         |
        +--------------------------+---------------------------+

        Parameters
        ----------
        record:
            An evidence record object.

        Returns
        -------
        dict or None
            Converted candidate, or ``None`` if conversion fails.
        """
        if record is None:
            return None
        try:
            record_id = str(getattr(record, "record_id", None) or _uid())
            content = (
                getattr(record, "content", None)
                or getattr(record, "text", None)
                or ""
            )
            domain = (
                getattr(record, "domain", None)
                or getattr(record, "kind", None)
                or "unknown"
            )
            confidence = float(getattr(record, "confidence", 0.5) or 0.5)
            return {
                "candidate_id": f"evidence-{record_id}",
                "description": str(content)[:512],
                "domain": str(domain),
                "novelty_score": _clamp(confidence),
                "evidence_count": 1,
                "source": "evidence_channel",
                "source_id": record_id,
                "timestamp": _utcnow(),
            }
        except Exception:
            return None

    def _result_to_record(self, result: Any) -> Any | None:
        """Convert a
        :class:`~jugeo.ideation.discovery_engine.models.DiscoveryResult` to
        an evidence record dict suitable for publishing.

        Parameters
        ----------
        result:
            A discovery result object.

        Returns
        -------
        dict or None
            Evidence record dict, or ``None`` on failure.
        """
        if result is None:
            return None
        try:
            def _g(key: str) -> Any:
                return result.get(key) if isinstance(result, dict) else getattr(result, key, None)

            return {
                "record_id": _uid(),
                "kind": "DISCOVERY_RESULT",
                "domain": "discovery_engine",
                "content": str(_g("candidate_id") or ""),
                "confidence": 1.0,
                "source_result_id": str(_g("result_id") or ""),
                "status": str(_g("status") or "PROMOTED"),
                "timestamp": _utcnow(),
            }
        except Exception:
            return None

    def __repr__(self) -> str:
        status = "connected" if self._channel is not None else "standalone"
        return (
            f"EvidenceChannelAdapter("
            f"status={status!r}, "
            f"consumed={self._consumed_total}, "
            f"published={self._published_total})"
        )


# ---------------------------------------------------------------------------
# DiscoveryEngineIntegration  (facade)
# ---------------------------------------------------------------------------


class DiscoveryEngineIntegration:
    """Top-level integration facade for the JuGeo discovery engine.

    :class:`DiscoveryEngineIntegration` composes
    :class:`BridgeIntegrationAdapter`, :class:`OrchestrationAdapter`, and
    :class:`EvidenceChannelAdapter` into a single object that can be used as
    a one-stop integration point for the discovery engine.

    Parameters
    ----------
    config:
        Optional :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig`.
        If ``None``, pipeline defaults are used.

    Attributes
    ----------
    _bridge_adapter : BridgeIntegrationAdapter or None
        Attached bridge adapter, populated by :meth:`connect_bridges`.
    _orch_adapter : OrchestrationAdapter or None
        Attached orchestration adapter, populated by
        :meth:`connect_orchestrator`.
    _channel_adapter : EvidenceChannelAdapter or None
        Attached evidence channel adapter, populated by
        :meth:`connect_evidence_channel`.
    _pipeline : DiscoveryPipeline
        The underlying pipeline instance.
    _events : deque[IntegrationEvent]
        Ring buffer of recent integration events (max 256 entries).

    Notes
    -----
    * Adapters are created lazily; :meth:`connect_bridges` etc. must be
      called before the corresponding adapter is used.
    * :meth:`run_integrated` uses whatever adapters are currently connected.
      If no bridge or channel adapter is connected, it falls back to the
      *candidates* parameter directly.

    Examples
    --------
    All-in-one integration::

        integration = build_integrated_pipeline()
        integration.connect_bridges(bridge_registry)
        integration.connect_orchestrator(orchestrator)
        integration.connect_evidence_channel(evidence_channel)
        results = integration.run_integrated()

    Status inspection::

        statuses = integration.get_integration_status()
        for name, status in statuses.items():
            print(name, status.value)

    Event inspection::

        for evt in integration.recent_events(n=10):
            print(evt.event_type, evt.source, evt.payload)
    """

    #: Maximum number of events retained in the in-process event ring buffer.
    MAX_EVENTS: int = 256

    def __init__(self, config: Any | None = None) -> None:
        self._config: Any = config
        self._bridge_adapter: BridgeIntegrationAdapter | None = None
        self._orch_adapter: OrchestrationAdapter | None = None
        self._channel_adapter: EvidenceChannelAdapter | None = None
        self._pipeline: Any = self._make_pipeline()
        self._events: deque[IntegrationEvent] = deque(maxlen=self.MAX_EVENTS)

    # ------------------------------------------------------------------
    # Connection methods
    # ------------------------------------------------------------------

    def connect_bridges(self, registry: Any) -> BridgeIntegrationAdapter:
        """Create and attach a :class:`BridgeIntegrationAdapter` for *registry*.

        Parameters
        ----------
        registry:
            A :class:`~jugeo.packs.bridges.BridgeRegistry` instance.

        Returns
        -------
        BridgeIntegrationAdapter
            The newly created adapter.
        """
        self._bridge_adapter = BridgeIntegrationAdapter(bridge_registry=registry)
        self.emit_event(
            "bridge.connected",
            {"registry_type": type(registry).__name__},
        )
        return self._bridge_adapter

    def connect_orchestrator(self, orchestrator: Any) -> OrchestrationAdapter:
        """Create and attach an :class:`OrchestrationAdapter` for *orchestrator*.

        Parameters
        ----------
        orchestrator:
            A JuGeo :class:`~jugeo.orchestration.controller.Orchestrator`.

        Returns
        -------
        OrchestrationAdapter
            The newly created adapter.
        """
        self._orch_adapter = OrchestrationAdapter(
            orchestrator=orchestrator,
            pipeline=self._pipeline,
        )
        self._orch_adapter.register_pipeline()
        self.emit_event(
            "orchestrator.connected",
            {"orchestrator_type": type(orchestrator).__name__},
        )
        return self._orch_adapter

    def connect_evidence_channel(self, channel: Any) -> EvidenceChannelAdapter:
        """Create and attach an :class:`EvidenceChannelAdapter` for *channel*.

        Parameters
        ----------
        channel:
            An evidence channel object.

        Returns
        -------
        EvidenceChannelAdapter
            The newly created adapter.
        """
        self._channel_adapter = EvidenceChannelAdapter(channel=channel)
        self.emit_event(
            "channel.connected",
            {"channel_type": type(channel).__name__},
        )
        return self._channel_adapter

    # ------------------------------------------------------------------
    # Run integration
    # ------------------------------------------------------------------

    def run_integrated(
        self,
        candidates: list[Any] | None = None,
    ) -> list[Any]:
        """Run the discovery pipeline with all connected adapters.

        Candidate sourcing order:

        1. If a :class:`EvidenceChannelAdapter` is connected, consume records
           from the channel and merge them with the *candidates* parameter.
        2. If a :class:`BridgeIntegrationAdapter` is connected, adapt its
           registry and merge those candidates too.
        3. Run the pipeline on the merged candidate list.
        4. If an :class:`EvidenceChannelAdapter` is connected, publish the
           results back to the channel.

        Parameters
        ----------
        candidates:
            Optional base candidate list.  Merged with any adapter-sourced
            candidates.

        Returns
        -------
        list
            Discovery results from the pipeline.
        """
        merged: list[Any] = list(candidates or [])

        # Consume from channel.
        if self._channel_adapter is not None:
            channel_candidates = self._channel_adapter.consume_records()
            merged.extend(channel_candidates)
            self.emit_event(
                "channel.consumed",
                {"count": len(channel_candidates)},
            )

        # Adapt bridge registry.
        if self._bridge_adapter is not None and self._bridge_adapter._registry is not None:
            bridge_candidates = self._bridge_adapter.adapt_bridge_registry(
                self._bridge_adapter._registry
            )
            merged.extend(bridge_candidates)
            self.emit_event(
                "bridge.adapted",
                {"count": len(bridge_candidates)},
            )

        # Run pipeline.
        self.emit_event("pipeline.start", {"candidate_count": len(merged)})
        results: list[Any] = []
        try:
            results = self._pipeline.run(merged)
            self.emit_event(
                "pipeline.complete",
                {"result_count": len(results)},
            )
        except Exception as exc:
            self.emit_event("pipeline.error", {"error": str(exc)})

        # Publish results to channel.
        if self._channel_adapter is not None and results:
            published = self._channel_adapter.publish_results(results)
            self.emit_event(
                "channel.published",
                {"published_count": published},
            )

        return results

    # ------------------------------------------------------------------
    # Status and events
    # ------------------------------------------------------------------

    def get_integration_status(self) -> dict[str, IntegrationStatus]:
        """Return the current status of each connected adapter.

        Returns
        -------
        dict[str, IntegrationStatus]
            A mapping of component name to status:

            * ``'bridge'`` — :class:`BridgeIntegrationAdapter` status
              (or ``DISCONNECTED`` if not connected).
            * ``'orchestrator'`` — :class:`OrchestrationAdapter` status.
            * ``'channel'`` — :class:`EvidenceChannelAdapter` status.
            * ``'pipeline'`` — pipeline readiness.
        """
        return {
            "bridge": (
                self._bridge_adapter.status
                if self._bridge_adapter is not None
                else IntegrationStatus.DISCONNECTED
            ),
            "orchestrator": (
                IntegrationStatus.CONNECTED
                if self._orch_adapter is not None and self._orch_adapter._registered
                else IntegrationStatus.DISCONNECTED
            ),
            "channel": (
                IntegrationStatus.CONNECTED
                if self._channel_adapter is not None
                else IntegrationStatus.DISCONNECTED
            ),
            "pipeline": (
                IntegrationStatus.CONNECTED
                if self._pipeline is not None
                else IntegrationStatus.DISCONNECTED
            ),
        }

    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an integration event into the internal event bus.

        Parameters
        ----------
        event_type:
            Short string identifying the event kind, e.g. ``'pipeline.start'``.
        payload:
            Arbitrary data attached to the event.
        """
        evt = IntegrationEvent(
            event_id=_uid(),
            event_type=event_type,
            source="DiscoveryEngineIntegration",
            payload=payload,
            timestamp=_utcnow(),
        )
        self._events.append(evt)

    def recent_events(self, n: int = 10) -> list[IntegrationEvent]:
        """Return the *n* most recent integration events.

        Parameters
        ----------
        n:
            Maximum number of events to return.

        Returns
        -------
        list[IntegrationEvent]
            Most recent events in chronological order (oldest first).
        """
        events = list(self._events)
        return events[-n:] if n < len(events) else events

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_pipeline(self) -> Any:
        """Create the underlying discovery pipeline."""
        try:
            from jugeo.ideation.discovery_engine.algorithms import create_default_pipeline
            return create_default_pipeline(self._config)
        except Exception:
            return _MinimalPipeline()

    def __repr__(self) -> str:
        status = self.get_integration_status()
        parts = ", ".join(f"{k}={v.value}" for k, v in status.items())
        return f"DiscoveryEngineIntegration({parts})"


# ---------------------------------------------------------------------------
# Free Functions
# ---------------------------------------------------------------------------


def integrate_with_kind_discovery(
    pipeline: Any,
    kind_discovery_module: Any,
) -> DiscoveryEngineIntegration:
    """Wire *pipeline* to a kind-discovery module and return an integration facade.

    This convenience function creates a
    :class:`DiscoveryEngineIntegration` and configures it with any
    registries or channels exposed by *kind_discovery_module*.

    Parameters
    ----------
    pipeline:
        A :class:`DiscoveryPipeline` (or compatible object).
    kind_discovery_module:
        A module or object with optional ``bridge_registry`` and
        ``evidence_channel`` attributes.

    Returns
    -------
    DiscoveryEngineIntegration
        A configured integration facade.

    Examples
    --------
    >>> integration = integrate_with_kind_discovery(pipeline, kind_module)
    >>> results = integration.run_integrated(candidates)
    """
    integration = DiscoveryEngineIntegration()
    integration._pipeline = pipeline

    bridge_reg = getattr(kind_discovery_module, "bridge_registry", None)
    if bridge_reg is not None:
        integration.connect_bridges(bridge_reg)

    channel = getattr(kind_discovery_module, "evidence_channel", None)
    if channel is not None:
        integration.connect_evidence_channel(channel)

    return integration


def integrate_with_bridges(
    pipeline: Any,
    bridge_registry: Any,
) -> BridgeIntegrationAdapter:
    """Create a :class:`BridgeIntegrationAdapter` for *bridge_registry* and
    attach it to *pipeline*.

    This is a thin convenience wrapper for callers that only need bridge
    integration, without the full :class:`DiscoveryEngineIntegration` facade.

    Parameters
    ----------
    pipeline:
        The discovery pipeline (used only for documentation purposes here;
        the adapter does not hold a reference to it).
    bridge_registry:
        A :class:`~jugeo.packs.bridges.BridgeRegistry`.

    Returns
    -------
    BridgeIntegrationAdapter
        A ready-to-use adapter.

    Examples
    --------
    >>> adapter = integrate_with_bridges(pipeline, registry)
    >>> candidates = adapter.adapt_bridge_registry(registry)
    >>> results = pipeline.run(candidates)
    """
    return BridgeIntegrationAdapter(bridge_registry=bridge_registry)


def integrate_with_orchestrator(
    pipeline: Any,
    orchestrator: Any,
) -> OrchestrationAdapter:
    """Create an :class:`OrchestrationAdapter` for *orchestrator* and register
    *pipeline* as its task.

    Parameters
    ----------
    pipeline:
        The :class:`DiscoveryPipeline` to manage.
    orchestrator:
        The JuGeo :class:`~jugeo.orchestration.controller.Orchestrator`.

    Returns
    -------
    OrchestrationAdapter
        A registered orchestration adapter.

    Examples
    --------
    >>> adapter = integrate_with_orchestrator(pipeline, orchestrator)
    >>> run_id = adapter.submit_run(candidates)
    """
    adapter = OrchestrationAdapter(orchestrator=orchestrator, pipeline=pipeline)
    adapter.register_pipeline()
    return adapter


def build_integrated_pipeline(
    config: Any | None = None,
) -> DiscoveryEngineIntegration:
    """Build and return a :class:`DiscoveryEngineIntegration` with a default pipeline.

    This is the recommended entry-point for most callers.  It creates a new
    :class:`DiscoveryEngineIntegration` with the given *config* (or module
    defaults if ``None``).  Adapters can then be connected as needed via
    :meth:`DiscoveryEngineIntegration.connect_bridges` etc.

    Parameters
    ----------
    config:
        Optional :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig`.

    Returns
    -------
    DiscoveryEngineIntegration
        A new, unconnnected integration facade.

    Examples
    --------
    >>> integration = build_integrated_pipeline()
    >>> integration.connect_bridges(my_registry)
    >>> results = integration.run_integrated()
    """
    return DiscoveryEngineIntegration(config=config)
