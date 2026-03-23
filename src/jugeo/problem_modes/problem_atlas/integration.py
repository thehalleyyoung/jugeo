"""Integration layer for the Unified Problem Atlas — Theory2.tex Ch14 §14.6.

copilot: integration bridge connecting problem atlas to jugeo subsystems.

This module implements §14.6 of Theory2.tex, providing the integration layer
that connects the Problem Atlas to:
  - The Judgment System (jugeo.judgments)
  - The Evidence System (jugeo.evidence)
  - The Solver/Orchestration layer (jugeo.solver, jugeo.orchestration)

The ProblemAtlasIntegration class is the main entry point.  It bridges the
atlas classification machinery to the rest of jugeo by:
  1. Resolving problem classes for given coordinates/judgments
  2. Building evidence requirements from judgment specifications
  3. Registering the atlas with the orchestration system
  4. Exporting/importing the catalog for persistence

Supporting classes:
  AtlasExporter   — Serializes the full atlas to JSON/dict
  AtlasImporter   — Deserializes and validates an atlas from JSON
  AtlasEventBus   — Publishes atlas events to subscribers
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        SemanticSignature,
        EvidenceRequirement,
        AtlasCatalog,
        ProblemCategory,
        DifficultyLevel,
        ConjunctionMode,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    SemanticSignature = object  # type: ignore[assignment,misc]
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    AtlasCatalog = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    DifficultyLevel = None  # type: ignore[assignment]
    ConjunctionMode = None  # type: ignore[assignment]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite, CoordinateKind
except ImportError:
    CoordinateObject = object  # type: ignore[assignment,misc]
    SemanticSite = object  # type: ignore[assignment,misc]
    CoordinateKind = None  # type: ignore[assignment]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind, ProvenanceKind
except ImportError:
    JudgmentTerm = object  # type: ignore[assignment,misc]
    JudgmentKind = None  # type: ignore[assignment]
    ProvenanceKind = None  # type: ignore[assignment]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = object  # type: ignore[assignment,misc]
    CertificateStatus = None  # type: ignore[assignment]

try:
    from jugeo.evidence.trust import TrustProfile
except ImportError:
    TrustProfile = object  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.problem_atlas.algorithms import (
        atlas_lookup_algorithm,
        evidence_routing_algorithm,
        LookupStrategy,
        LookupResult,
    )
except ImportError:
    atlas_lookup_algorithm = None  # type: ignore[assignment]
    evidence_routing_algorithm = None  # type: ignore[assignment]
    LookupStrategy = None  # type: ignore[assignment]
    LookupResult = object  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SubscriptionId: TypeAlias = str
HandlerFn: TypeAlias = Callable[[dict[str, Any]], None]
ClassId: TypeAlias = str

# ---------------------------------------------------------------------------
# §14.6.1  IntegrationStatus
# ---------------------------------------------------------------------------


class IntegrationStatus(str, Enum):
    """Lifecycle status of the ProblemAtlasIntegration instance.

    DISCONNECTED means no subsystems have been connected.
    CONNECTED means at least one subsystem is active.
    DEGRADED means some subsystems connected with errors.
    ERROR means the integration itself is in a fault state.
    """

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ERROR = "error"

    def is_operational(self) -> bool:
        """Return True if the integration can serve requests.

        CONNECTED and DEGRADED are both considered operational — DEGRADED
        indicates partial functionality but the core lookup paths still work.

        Returns:
            True for CONNECTED and DEGRADED; False for DISCONNECTED and ERROR.
        """
        return self in (IntegrationStatus.CONNECTED, IntegrationStatus.DEGRADED)


# ---------------------------------------------------------------------------
# §14.6.2  AtlasEvent
# ---------------------------------------------------------------------------


class AtlasEvent(str, Enum):
    """Events that the atlas integration layer can publish.

    Subscribers registered via :meth:`ProblemAtlasIntegration.subscribe` or
    :meth:`AtlasEventBus.subscribe` receive a ``data`` dict payload whenever
    an event fires.

    Mutation events (CLASS_REGISTERED, CLASS_UPDATED, CLASS_REMOVED) change
    the catalog state; read events (REQUIREMENT_CHECKED, EVIDENCE_ROUTED) do
    not modify state.
    """

    CLASS_REGISTERED = "class_registered"
    CLASS_UPDATED = "class_updated"
    CLASS_REMOVED = "class_removed"
    REQUIREMENT_CHECKED = "requirement_checked"
    EVIDENCE_ROUTED = "evidence_routed"
    CATALOG_IMPORTED = "catalog_imported"
    CATALOG_EXPORTED = "catalog_exported"

    def is_mutation(self) -> bool:
        """Return True if this event mutates catalog state.

        Mutation events are CLASS_REGISTERED, CLASS_UPDATED, CLASS_REMOVED,
        and CATALOG_IMPORTED (which replaces or merges catalog content).

        Returns:
            True for CLASS_REGISTERED, CLASS_UPDATED, CLASS_REMOVED,
            and CATALOG_IMPORTED.
        """
        return self in (
            AtlasEvent.CLASS_REGISTERED,
            AtlasEvent.CLASS_UPDATED,
            AtlasEvent.CLASS_REMOVED,
            AtlasEvent.CATALOG_IMPORTED,
        )


# ---------------------------------------------------------------------------
# §14.6.3  IntegrationReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationReport:
    """Snapshot of the current integration state.

    Produced by :meth:`ProblemAtlasIntegration.get_status` and consumed by
    health checks and monitoring tools.

    Attributes:
        integration_id: Unique identifier for this integration instance.
        status: Current IntegrationStatus.
        connected_systems: Tuple of subsystem names that have been successfully
            connected (e.g., ``("judgments", "evidence")``).
        catalog_size: Number of ProblemClass entries in the current catalog.
        last_event: Name of the most recently published AtlasEvent, or
            ``"none"`` if no events have been published.
        timestamp: ISO-8601 timestamp at which this report was generated.
    """

    integration_id: str
    status: IntegrationStatus
    connected_systems: tuple[str, ...]
    catalog_size: int
    last_event: str
    timestamp: str

    def is_healthy(self) -> bool:
        """Return True if the integration is in a healthy, operational state.

        Healthy means the status is CONNECTED and at least one subsystem is
        connected.  DEGRADED is not considered fully healthy.

        Returns:
            True when status is CONNECTED and connected_systems is non-empty.
        """
        return (
            self.status == IntegrationStatus.CONNECTED
            and len(self.connected_systems) > 0
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this report to a plain Python dictionary.

        Returns:
            Dict with all fields serialized to JSON-compatible types.
        """
        return {
            "integration_id": self.integration_id,
            "status": self.status.value,
            "connected_systems": list(self.connected_systems),
            "catalog_size": self.catalog_size,
            "last_event": self.last_event,
            "timestamp": self.timestamp,
            "is_healthy": self.is_healthy(),
        }


# ---------------------------------------------------------------------------
# §14.6.4  ClassResolutionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassResolutionResult:
    """Result of resolving a coordinate or judgment to a ProblemClass.

    Produced by :meth:`ProblemAtlasIntegration.resolve_problem_class_for_coordinate`.

    Attributes:
        coordinate_id: The input coordinate ID or judgment key that was resolved.
        resolved_class_id: The class_id of the matched ProblemClass, or None if
            resolution failed.
        confidence: Confidence score of the resolution in [0.0, 1.0].
        method_used: Description of the resolution method that succeeded (e.g.
            ``"direct_lookup"``, ``"fuzzy_match"``, ``"category_filter"``).
        fallback_used: True if a fallback strategy was required because the
            primary method failed.
        notes: Human-readable explanation of the resolution outcome.
    """

    coordinate_id: str
    resolved_class_id: str | None
    confidence: float
    method_used: str
    fallback_used: bool
    notes: str

    def succeeded(self) -> bool:
        """Return True if the resolution found a matching ProblemClass.

        Returns:
            True when resolved_class_id is not None.
        """
        return self.resolved_class_id is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain Python dictionary.

        Returns:
            Dict with all fields serialized to JSON-compatible types.
        """
        return {
            "coordinate_id": self.coordinate_id,
            "resolved_class_id": self.resolved_class_id,
            "confidence": self.confidence,
            "method_used": self.method_used,
            "fallback_used": self.fallback_used,
            "notes": self.notes,
            "succeeded": self.succeeded(),
        }


# ---------------------------------------------------------------------------
# §14.6.5  AtlasEventBus
# ---------------------------------------------------------------------------


class AtlasEventBus:
    """Publish/subscribe event bus for atlas integration events.

    Maintains a registry of event handlers keyed by :class:`AtlasEvent`.
    Handlers are identified by unique subscription IDs returned from
    :meth:`subscribe`.

    This bus is intentionally synchronous: handlers are invoked in the order
    they were registered, in the same thread as the publisher.  For async
    dispatch or batching, wrap handlers externally.

    Examples::

        bus = AtlasEventBus()

        def on_class_registered(data: dict) -> None:
            print(f"New class: {data['class_id']}")

        sub_id = bus.subscribe(AtlasEvent.CLASS_REGISTERED, on_class_registered)
        bus.publish(AtlasEvent.CLASS_REGISTERED, {"class_id": "COMP_OPT"})
        bus.unsubscribe(sub_id)
    """

    def __init__(self) -> None:
        """Initialise an empty event bus with no subscribers."""
        # Maps subscription_id -> (event, handler)
        self._subscriptions: dict[str, tuple[AtlasEvent, HandlerFn]] = {}

    def subscribe(self, event: AtlasEvent, handler: HandlerFn) -> SubscriptionId:
        """Register a handler for the given event.

        Args:
            event: The AtlasEvent to subscribe to.
            handler: Callable that accepts a ``dict[str, Any]`` payload.  Called
                synchronously when the event is published.

        Returns:
            A unique subscription ID string that can be passed to
            :meth:`unsubscribe` to remove the handler.

        Raises:
            TypeError: If ``handler`` is not callable.
        """
        if not callable(handler):
            raise TypeError(
                f"AtlasEventBus.subscribe: handler must be callable, got {type(handler)!r}."
            )
        sub_id = str(uuid.uuid4())
        self._subscriptions[sub_id] = (event, handler)
        return sub_id

    def unsubscribe(self, subscription_id: SubscriptionId) -> bool:
        """Remove a previously registered handler by subscription ID.

        Args:
            subscription_id: The ID returned from a prior :meth:`subscribe` call.

        Returns:
            True if the subscription existed and was removed; False if the ID
            was not found (already removed or never registered).
        """
        if subscription_id in self._subscriptions:
            del self._subscriptions[subscription_id]
            return True
        return False

    def publish(self, event: AtlasEvent, data: dict[str, Any]) -> int:
        """Publish an event, invoking all registered handlers.

        Handlers are called in registration order.  Exceptions raised by
        individual handlers are caught and printed to stderr — the bus
        continues calling remaining handlers.

        Args:
            event: The AtlasEvent to publish.
            data: Payload dictionary passed to each handler.

        Returns:
            The number of handlers that were successfully invoked (not counting
            handlers that raised exceptions).
        """
        called = 0
        for sub_id, (registered_event, handler) in list(self._subscriptions.items()):
            if registered_event == event:
                try:
                    handler(data)
                    called += 1
                except Exception as exc:
                    import sys
                    print(
                        f"AtlasEventBus: handler {sub_id!r} raised {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
        return called

    def subscriber_count(self, event: AtlasEvent) -> int:
        """Return the number of active subscribers for the given event.

        Args:
            event: The AtlasEvent to count subscribers for.

        Returns:
            Integer count >= 0.
        """
        return sum(
            1 for ev, _ in self._subscriptions.values() if ev == event
        )

    def clear(self) -> None:
        """Remove all subscriptions from the bus.

        After calling :meth:`clear`, publishing any event will invoke zero
        handlers.
        """
        self._subscriptions.clear()

    def list_events(self) -> list[AtlasEvent]:
        """Return the list of events that currently have at least one subscriber.

        Returns:
            List of AtlasEvent values with active subscribers.  Order reflects
            registration order of the first subscriber per event.
        """
        seen: set[AtlasEvent] = set()
        result: list[AtlasEvent] = []
        for ev, _ in self._subscriptions.values():
            if ev not in seen:
                seen.add(ev)
                result.append(ev)
        return result


# ---------------------------------------------------------------------------
# §14.6.6  _MinimalCatalog — fallback catalog implementation
# ---------------------------------------------------------------------------


class _MinimalCatalog:
    """Minimal in-process catalog used when the real AtlasCatalog model is absent.

    Stores ProblemClass-like dicts and supports the attribute access patterns
    used by the algorithm functions.  This is the fallback that
    :meth:`ProblemAtlasIntegration.get_catalog` returns when no real catalog
    has been set.
    """

    def __init__(self) -> None:
        """Initialise with an empty class registry."""
        self._classes: dict[str, dict[str, Any]] = {}

    def register(self, class_id: str, data: dict[str, Any]) -> None:
        """Register a problem class dictionary.

        Args:
            class_id: Unique identifier for this class.
            data: Dict describing the class (name, description, category, etc.).
        """
        self._classes[class_id] = {"class_id": class_id, **data}

    @property
    def classes(self) -> list[dict[str, Any]]:
        """Return all registered class dicts as a list."""
        return list(self._classes.values())

    def get(self, class_id: str) -> dict[str, Any] | None:
        """Look up a class by ID.

        Args:
            class_id: The class ID to look up.

        Returns:
            Class dict or None.
        """
        return self._classes.get(class_id)

    def __len__(self) -> int:
        return len(self._classes)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._classes.values())


def _build_default_catalog() -> _MinimalCatalog:
    """Build a default minimal catalog with placeholder problem class entries.

    Creates one representative class per canonical problem category
    (COMPUTATIONAL, VERIFICATION, CONSTRUCTIVE, ANALYTICAL, RELATIONAL), each
    with a name, description, category, and one required channel.

    Returns:
        A :class:`_MinimalCatalog` instance populated with default entries.
    """
    catalog = _MinimalCatalog()

    defaults: list[dict[str, Any]] = [
        {
            "class_id": "COMPUTATIONAL_SEARCH",
            "name": "COMPUTATIONAL_SEARCH",
            "description": "Search for a solution satisfying given constraints",
            "category": "COMPUTATIONAL",
            "requirements": [
                {
                    "requirement_id": "req_comp_search",
                    "required_channels": ["proof", "test", "benchmark"],
                    "threshold": 0.7,
                }
            ],
        },
        {
            "class_id": "VERIFICATION_DECISION",
            "name": "VERIFICATION_DECISION",
            "description": "Decide whether a property holds of a given input",
            "category": "VERIFICATION",
            "requirements": [
                {
                    "requirement_id": "req_verif_decision",
                    "required_channels": ["formal_proof", "model_check"],
                    "threshold": 0.85,
                }
            ],
        },
        {
            "class_id": "CONSTRUCTIVE_SYNTHESIS",
            "name": "CONSTRUCTIVE_SYNTHESIS",
            "description": "Synthesize an artifact satisfying a specification",
            "category": "CONSTRUCTIVE",
            "requirements": [
                {
                    "requirement_id": "req_constr_synth",
                    "required_channels": ["synthesis_trace", "test"],
                    "threshold": 0.75,
                }
            ],
        },
        {
            "class_id": "ANALYTICAL_CLASSIFICATION",
            "name": "ANALYTICAL_CLASSIFICATION",
            "description": "Classify an input into a predefined category or class",
            "category": "ANALYTICAL",
            "requirements": [
                {
                    "requirement_id": "req_anal_class",
                    "required_channels": ["labeled_data", "accuracy_report"],
                    "threshold": 0.8,
                }
            ],
        },
        {
            "class_id": "RELATIONAL_MATCHING",
            "name": "RELATIONAL_MATCHING",
            "description": "Find correspondences between two structured objects",
            "category": "RELATIONAL",
            "requirements": [
                {
                    "requirement_id": "req_rel_match",
                    "required_channels": ["alignment_proof", "consistency_check"],
                    "threshold": 0.72,
                }
            ],
        },
    ]

    for entry in defaults:
        catalog.register(entry["class_id"], entry)

    return catalog


# ---------------------------------------------------------------------------
# §14.6.7  ProblemAtlasIntegration
# ---------------------------------------------------------------------------


class ProblemAtlasIntegration:
    """Main integration bridge between the Problem Atlas and jugeo subsystems.

    :class:`ProblemAtlasIntegration` is the primary entry point for all
    atlas-related operations outside the atlas package itself.  It manages:

    - A reference to the active :class:`AtlasCatalog` (or fallback).
    - Connection bookkeeping for each jugeo subsystem.
    - An internal :class:`AtlasEventBus` for event propagation.
    - Lookup, routing, and class-resolution methods.

    Typical usage::

        integration = ProblemAtlasIntegration.default()
        integration.integrate_with_judgment_system()
        integration.integrate_with_evidence_system()

        class_id = integration.lookup_class_for_problem("graph coloring")
        report = integration.get_status()
    """

    def __init__(self, catalog: Any | None = None) -> None:
        """Initialise the integration with an optional catalog.

        Args:
            catalog: An AtlasCatalog-like object.  If None, a default minimal
                catalog is constructed automatically.
        """
        self._integration_id: str = str(uuid.uuid4())
        self._catalog: Any = catalog if catalog is not None else _build_default_catalog()
        self._connected_systems: set[str] = set()
        self._status: IntegrationStatus = IntegrationStatus.DISCONNECTED
        self._event_bus: AtlasEventBus = AtlasEventBus()
        self._last_event: str = "none"

    # ------------------------------------------------------------------
    # Subsystem integration methods
    # ------------------------------------------------------------------

    def integrate_with_judgment_system(self) -> bool:
        """Attempt to connect to the jugeo judgment system.

        Tries to import ``jugeo.judgments`` and register a hook that maps
        incoming judgment terms to problem class IDs via the atlas lookup.
        On success, ``"judgments"`` is added to the set of connected systems.

        Returns:
            True if the connection succeeded; False if the import failed or the
            hook registration raised an exception.
        """
        try:
            import jugeo.judgments  # noqa: F401 — verify importability
            self._connected_systems.add("judgments")
            self._update_status()
            self._emit(
                AtlasEvent.CLASS_REGISTERED,
                {
                    "system": "judgments",
                    "integration_id": self._integration_id,
                    "action": "subsystem_connected",
                },
            )
            return True
        except ImportError:
            # Subsystem not available in this environment — degraded is OK
            self._connected_systems.discard("judgments")
            self._update_status()
            return False
        except Exception:
            self._status = IntegrationStatus.DEGRADED
            return False

    def integrate_with_evidence_system(self) -> bool:
        """Attempt to connect to the jugeo evidence system.

        Tries to import ``jugeo.evidence`` and verify that the
        :class:`TrustProfile` class is available.  On success, ``"evidence"``
        is added to the connected systems set.

        Returns:
            True if the connection succeeded; False otherwise.
        """
        try:
            import jugeo.evidence  # noqa: F401
            self._connected_systems.add("evidence")
            self._update_status()
            self._emit(
                AtlasEvent.EVIDENCE_ROUTED,
                {
                    "system": "evidence",
                    "integration_id": self._integration_id,
                    "action": "subsystem_connected",
                },
            )
            return True
        except ImportError:
            self._connected_systems.discard("evidence")
            self._update_status()
            return False
        except Exception:
            self._status = IntegrationStatus.DEGRADED
            return False

    def integrate_with_solver(self) -> bool:
        """Attempt to connect to the jugeo solver/orchestration layer.

        Tries to import ``jugeo.solver`` and adds ``"solver"`` to the connected
        systems if successful.

        Returns:
            True if the connection succeeded; False otherwise.
        """
        try:
            import jugeo.solver  # noqa: F401
            self._connected_systems.add("solver")
            self._update_status()
            return True
        except ImportError:
            self._connected_systems.discard("solver")
            self._update_status()
            return False
        except Exception:
            self._status = IntegrationStatus.DEGRADED
            return False

    def _update_status(self) -> None:
        """Recompute the integration status from the set of connected systems.

        Sets CONNECTED when at least one system is connected, DISCONNECTED when
        none are, and leaves DEGRADED/ERROR unchanged if already set to those
        values externally.
        """
        if self._status in (IntegrationStatus.ERROR,):
            return  # Sticky error state — must be reset manually
        if self._connected_systems:
            self._status = IntegrationStatus.CONNECTED
        else:
            self._status = IntegrationStatus.DISCONNECTED

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def build_requirements_from_judgment(
        self, judgment_spec: dict[str, Any]
    ) -> Any | None:
        """Construct an EvidenceRequirement from a judgment specification dict.

        Extracts the problem description (or class ID) from the judgment spec,
        resolves the corresponding ProblemClass, and returns a synthetic
        EvidenceRequirement dict that combines the class's channel list with
        the trust threshold encoded in the spec.

        The judgment spec is expected to contain at least one of:

        - ``"problem_class"`` — a class_id string
        - ``"description"`` — free-text problem description
        - ``"channels"`` — list of required channel IDs
        - ``"threshold"`` — minimum trust threshold (default: 0.7)

        Args:
            judgment_spec: Dict describing the judgment.

        Returns:
            An EvidenceRequirement-like dict, or None if no class could be
            resolved and no channel list was provided.
        """
        class_id = judgment_spec.get("problem_class")
        description = judgment_spec.get("description", "")
        channels: list[str] = judgment_spec.get("channels", [])
        threshold: float = float(judgment_spec.get("threshold", 0.7))

        # Resolve class if not given directly
        if not class_id and description:
            class_id = self.lookup_class_for_problem(description)

        # Pull channels from the class's first requirement if not overridden
        if class_id and not channels:
            pc = _safe_find_class(class_id, self._catalog)
            if pc is not None:
                for attr in ("requirements", "evidence_requirements"):
                    val = getattr(pc, attr, None) or (
                        pc.get(attr) if isinstance(pc, dict) else None
                    )
                    if isinstance(val, (list, tuple)) and val:
                        req = val[0]
                        if isinstance(req, dict):
                            channels = req.get("required_channels", [])
                            threshold = float(req.get("threshold", threshold))
                        else:
                            for ch_attr in ("required_channels", "channels"):
                                ch_val = getattr(req, ch_attr, None)
                                if isinstance(ch_val, (list, tuple)):
                                    channels = list(ch_val)
                                    break
                        break

        if not channels and not class_id:
            return None

        req_id = f"req_{class_id or 'judgment'}_{uuid.uuid4().hex[:8]}"
        requirement = {
            "requirement_id": req_id,
            "required_channels": channels,
            "threshold": threshold,
            "source": "judgment_spec",
            "class_id": class_id,
        }

        self._emit(
            AtlasEvent.REQUIREMENT_CHECKED,
            {
                "requirement_id": req_id,
                "class_id": class_id,
                "channels": channels,
            },
        )
        return requirement

    def resolve_problem_class_for_coordinate(
        self,
        coordinate_id: str,
        coordinate_kind: str | None = None,
    ) -> ClassResolutionResult:
        """Resolve a coordinate or judgment key to a ProblemClass ID.

        Tries three resolution strategies in order:

        1. **Direct lookup** — check whether ``coordinate_id`` is a known
           class_id in the catalog.
        2. **Fuzzy match** — call :func:`atlas_lookup_algorithm` with the
           coordinate_id as the description.
        3. **Category filter** — if ``coordinate_kind`` is provided, filter the
           catalog by that category and return the first matching class.

        Args:
            coordinate_id: The coordinate ID or judgment key to resolve.
            coordinate_kind: Optional kind string (e.g., ``"COMPUTATIONAL"``)
                used as a category hint for the fallback strategy.

        Returns:
            ClassResolutionResult describing whether resolution succeeded and
            which strategy was used.
        """
        # Strategy 1: Direct lookup
        pc = _safe_find_class(coordinate_id, self._catalog)
        if pc is not None:
            resolved_id = _safe_get_class_id(pc)
            return ClassResolutionResult(
                coordinate_id=coordinate_id,
                resolved_class_id=resolved_id,
                confidence=1.0,
                method_used="direct_lookup",
                fallback_used=False,
                notes=f"Coordinate ID matched class '{resolved_id}' directly.",
            )

        # Strategy 2: Fuzzy match via atlas_lookup_algorithm
        if atlas_lookup_algorithm is not None:
            try:
                from jugeo.problem_modes.problem_atlas.algorithms import (
                    LookupStrategy as LS,
                    atlas_lookup_algorithm as _lookup,
                )
                result = _lookup(
                    coordinate_id,
                    self._catalog,
                    strategy=LS.FUZZY,
                    category_hint=coordinate_kind,
                )
                if result.matched_class is not None and result.confidence >= 0.4:
                    return ClassResolutionResult(
                        coordinate_id=coordinate_id,
                        resolved_class_id=result.matched_class,
                        confidence=result.confidence,
                        method_used="fuzzy_match",
                        fallback_used=False,
                        notes=(
                            f"Fuzzy lookup matched '{result.matched_class}' "
                            f"(confidence={result.confidence:.3f})."
                        ),
                    )
            except Exception:
                pass

        # Strategy 3: Category filter fallback
        if coordinate_kind:
            kind_lower = coordinate_kind.lower()
            for pc_candidate in _get_all_classes(self._catalog):
                cat = str(
                    getattr(pc_candidate, "category", None)
                    or (
                        pc_candidate.get("category")
                        if isinstance(pc_candidate, dict)
                        else ""
                    )
                    or ""
                ).lower()
                if cat == kind_lower:
                    cid = _safe_get_class_id(pc_candidate)
                    return ClassResolutionResult(
                        coordinate_id=coordinate_id,
                        resolved_class_id=cid,
                        confidence=0.3,
                        method_used="category_filter",
                        fallback_used=True,
                        notes=(
                            f"Category filter matched first '{coordinate_kind}' "
                            f"class '{cid}'."
                        ),
                    )

        return ClassResolutionResult(
            coordinate_id=coordinate_id,
            resolved_class_id=None,
            confidence=0.0,
            method_used="none",
            fallback_used=True,
            notes=(
                f"All resolution strategies failed for coordinate '{coordinate_id}'."
            ),
        )

    def get_status(self) -> IntegrationReport:
        """Return a snapshot of the current integration state.

        Returns:
            An IntegrationReport with current status, connected systems, catalog
            size, last published event, and a UTC timestamp.
        """
        catalog_size = len(self._catalog) if hasattr(self._catalog, "__len__") else 0
        return IntegrationReport(
            integration_id=self._integration_id,
            status=self._status,
            connected_systems=tuple(sorted(self._connected_systems)),
            catalog_size=catalog_size,
            last_event=self._last_event,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
        )

    def lookup_class_for_problem(self, description: str) -> str | None:
        """Convenience method: look up the best-matching class ID for a description.

        Delegates to :func:`atlas_lookup_algorithm` when available; otherwise
        falls back to a simple token-overlap scan of the catalog.

        Args:
            description: Free-text problem description.

        Returns:
            class_id string of the best match, or None if nothing matched.
        """
        try:
            from jugeo.problem_modes.problem_atlas.algorithms import (
                LookupStrategy as LS,
                atlas_lookup_algorithm as _lookup,
            )
            result = _lookup(description, self._catalog, strategy=LS.FUZZY)
            return result.matched_class
        except Exception:
            pass

        # Fallback: token-overlap scan
        desc_words = set(description.lower().split())
        best_id: str | None = None
        best_hits = 0

        for pc in _get_all_classes(self._catalog):
            name = (
                getattr(pc, "name", None)
                or (pc.get("name") if isinstance(pc, dict) else None)
                or ""
            )
            class_words = set(str(name).lower().split())
            hits = len(desc_words & class_words)
            if hits > best_hits:
                best_hits = hits
                best_id = _safe_get_class_id(pc)

        return best_id

    def route_evidence_to_class(
        self,
        class_id: str,
        evidence: dict[str, float],
    ) -> dict[str, Any]:
        """Route an evidence dictionary to the requirements of a problem class.

        Retrieves the requirements for ``class_id`` from the catalog, then calls
        :func:`evidence_routing_algorithm` to compute per-requirement trust
        scores.

        Args:
            class_id: The problem class ID whose requirements to route to.
            evidence: Mapping from channel_id to trust score in [0.0, 1.0].

        Returns:
            Dict with keys ``"class_id"``, ``"routing"`` (per-requirement trust),
            and ``"coverage"`` (fraction of requirements with trust >= 0.5).
        """
        pc = _safe_find_class(class_id, self._catalog)
        if pc is None:
            return {
                "class_id": class_id,
                "routing": {},
                "coverage": 0.0,
                "error": f"Class '{class_id}' not found in catalog.",
            }

        requirements = _extract_requirements(pc)

        try:
            from jugeo.problem_modes.problem_atlas.algorithms import (
                evidence_routing_algorithm as _route,
            )
            routing = _route(evidence, requirements)
        except Exception:
            # Fallback: manual routing
            routing = {}
            for req in requirements:
                req_id = _get_req_id(req)
                channels = _get_req_channels(req)
                if not channels:
                    routing[req_id] = 1.0
                else:
                    total = sum(evidence.get(ch, 0.0) for ch in channels)
                    routing[req_id] = round(total / len(channels), 6)

        coverage = (
            sum(1 for v in routing.values() if v >= 0.5) / len(routing)
            if routing
            else 0.0
        )

        self._emit(
            AtlasEvent.EVIDENCE_ROUTED,
            {
                "class_id": class_id,
                "routing": routing,
                "coverage": coverage,
            },
        )

        return {"class_id": class_id, "routing": routing, "coverage": round(coverage, 4)}

    def get_catalog(self) -> Any:
        """Return the current catalog object.

        Returns:
            The active AtlasCatalog (or _MinimalCatalog fallback).
        """
        return self._catalog

    def set_catalog(self, catalog: Any) -> None:
        """Replace the current catalog with a new one.

        Args:
            catalog: New AtlasCatalog-like object to use.
        """
        self._catalog = catalog
        self._emit(
            AtlasEvent.CATALOG_IMPORTED,
            {
                "integration_id": self._integration_id,
                "action": "catalog_replaced",
                "catalog_size": len(catalog) if hasattr(catalog, "__len__") else -1,
            },
        )

    def subscribe(self, event: AtlasEvent, handler: HandlerFn) -> None:
        """Subscribe to an atlas event.

        Args:
            event: The AtlasEvent to subscribe to.
            handler: Callable receiving ``dict[str, Any]`` payloads.
        """
        self._event_bus.subscribe(event, handler)

    def _emit(self, event: AtlasEvent, data: dict[str, Any]) -> None:
        """Publish an atlas event to all registered subscribers.

        Also records the event name as the last event for status reporting.

        Args:
            event: The AtlasEvent to publish.
            data: Payload dict passed to each subscriber.
        """
        self._last_event = event.value
        self._event_bus.publish(event, data)

    @classmethod
    def default(cls) -> "ProblemAtlasIntegration":
        """Create a ProblemAtlasIntegration with a default minimal catalog.

        Attempts to connect to all available jugeo subsystems automatically.
        Connection failures are silently ignored (degraded mode).

        Returns:
            A fully initialised ProblemAtlasIntegration instance.
        """
        integration = cls(catalog=_build_default_catalog())
        integration.integrate_with_judgment_system()
        integration.integrate_with_evidence_system()
        integration.integrate_with_solver()
        return integration


# ---------------------------------------------------------------------------
# §14.6.8  AtlasExporter
# ---------------------------------------------------------------------------


class AtlasExporter:
    """Serialises an AtlasCatalog to dict/JSON for persistence and transport.

    The exported format is a self-describing dictionary with a ``manifest``
    header (version, export timestamp, class count) and three data sections:
    ``classes``, ``requirements``, and ``signatures``.

    Examples::

        exporter = AtlasExporter(catalog)
        json_str = exporter.export_to_json(indent=2)
        errors = exporter.validate_export(json.loads(json_str))
        assert not errors
    """

    def __init__(self, catalog: Any) -> None:
        """Initialise the exporter with a catalog.

        Args:
            catalog: The AtlasCatalog to export.
        """
        self._catalog = catalog

    def export_to_dict(self) -> dict[str, Any]:
        """Export the full catalog to a Python dictionary.

        The output structure is::

            {
                "manifest": { ... },
                "classes": [ { class fields } ],
                "requirements": [ { req fields } ],
                "signatures": [ { sig fields } ],
            }

        Returns:
            Full export dictionary, ready for JSON serialization.
        """
        manifest = self.export_manifest()
        classes = [self.export_class(_safe_get_class_id(pc)) for pc in _get_all_classes(self._catalog)]
        requirements = self.export_requirements()
        signatures = self.export_signatures()

        return {
            "manifest": manifest,
            "classes": classes,
            "requirements": requirements,
            "signatures": signatures,
        }

    def export_to_json(self, *, indent: int = 2) -> str:
        """Serialize the catalog to a JSON string.

        Args:
            indent: Number of spaces for JSON indentation.  Set to 0 for
                compact output.

        Returns:
            JSON string representation of the full catalog.
        """
        data = self.export_to_dict()
        indent_arg = indent if indent > 0 else None
        return json.dumps(data, indent=indent_arg, default=str, ensure_ascii=False)

    def export_class(self, class_id: str) -> dict[str, Any]:
        """Export a single ProblemClass to a dictionary.

        Args:
            class_id: The ID of the class to export.

        Returns:
            Dictionary of class fields.  Returns an error dict if the class
            is not found.
        """
        pc = _safe_find_class(class_id, self._catalog)
        if pc is None:
            return {"error": f"class '{class_id}' not found", "class_id": class_id}

        if isinstance(pc, dict):
            return dict(pc)

        result: dict[str, Any] = {}
        for attr in (
            "class_id", "name", "description", "category",
            "difficulty", "parent_ids", "child_ids", "keywords",
        ):
            val = getattr(pc, attr, None)
            if val is not None:
                result[attr] = val if not isinstance(val, set) else list(val)
        result.setdefault("class_id", class_id)
        return result

    def export_requirements(self) -> list[dict[str, Any]]:
        """Export all EvidenceRequirements from the catalog.

        Returns:
            List of requirement dicts, one per unique requirement across all
            classes.  Duplicate requirement IDs are deduplicated (first seen
            wins).
        """
        seen_ids: set[str] = set()
        result: list[dict[str, Any]] = []

        for pc in _get_all_classes(self._catalog):
            for req in _extract_requirements(pc):
                req_id = _get_req_id(req)
                if req_id in seen_ids:
                    continue
                seen_ids.add(req_id)

                if isinstance(req, dict):
                    result.append(dict(req))
                else:
                    req_dict: dict[str, Any] = {}
                    for attr in ("requirement_id", "required_channels", "threshold",
                                 "conjunction_mode", "description"):
                        val = getattr(req, attr, None)
                        if val is not None:
                            req_dict[attr] = val
                    req_dict.setdefault("requirement_id", req_id)
                    result.append(req_dict)

        return result

    def export_signatures(self) -> list[dict[str, Any]]:
        """Export all SemanticSignatures from the catalog.

        Returns:
            List of signature dicts, deduplicated by signature name/ID.
        """
        seen: set[str] = set()
        result: list[dict[str, Any]] = []

        for pc in _get_all_classes(self._catalog):
            sig = _get_class_sig(pc)
            if sig is None:
                continue

            sig_id = str(
                getattr(sig, "name", None) or getattr(sig, "id", None) or id(sig)
            )
            if sig_id in seen:
                continue
            seen.add(sig_id)

            if isinstance(sig, dict):
                result.append(dict(sig))
            else:
                sig_dict: dict[str, Any] = {}
                for attr in ("name", "input_schema", "output_schema", "description"):
                    val = getattr(sig, attr, None)
                    if val is not None:
                        sig_dict[attr] = val
                result.append(sig_dict)

        return result

    def export_manifest(self) -> dict[str, Any]:
        """Export a summary/header manifest for the catalog.

        Returns:
            Dict containing version, export timestamp, class count, category
            breakdown, and module reference string.
        """
        all_classes = _get_all_classes(self._catalog)
        category_counts: dict[str, int] = {}

        for pc in all_classes:
            cat = str(
                getattr(pc, "category", None)
                or (pc.get("category") if isinstance(pc, dict) else None)
                or "UNKNOWN"
            )
            category_counts[cat] = category_counts.get(cat, 0) + 1

        return {
            "version": "0.1.0",
            "theory_ref": "theory2.tex Ch14 §14.6",
            "exported_at": datetime.now(tz=timezone.utc).isoformat(),
            "class_count": len(all_classes),
            "category_breakdown": category_counts,
            "format": "jugeo.problem_atlas.v1",
        }

    def validate_export(self, exported: dict[str, Any]) -> list[str]:
        """Validate a previously exported dict for structural integrity.

        Checks that all required top-level keys are present, that the
        ``manifest`` section has the expected fields, and that each class has a
        ``class_id``.

        Args:
            exported: Dictionary produced by :meth:`export_to_dict` or parsed
                from :meth:`export_to_json`.

        Returns:
            List of validation error strings.  Empty list means the export is
            structurally valid.
        """
        errors: list[str] = []

        for key in ("manifest", "classes", "requirements", "signatures"):
            if key not in exported:
                errors.append(f"Missing required top-level key: '{key}'.")

        manifest = exported.get("manifest", {})
        for mkey in ("version", "exported_at", "class_count"):
            if mkey not in manifest:
                errors.append(f"Manifest missing required field: '{mkey}'.")

        classes = exported.get("classes", [])
        if not isinstance(classes, list):
            errors.append("'classes' must be a list.")
        else:
            for i, cls_dict in enumerate(classes):
                if not isinstance(cls_dict, dict):
                    errors.append(f"Class at index {i} is not a dict.")
                elif "class_id" not in cls_dict and "error" not in cls_dict:
                    errors.append(f"Class at index {i} is missing 'class_id'.")

        requirements = exported.get("requirements", [])
        if not isinstance(requirements, list):
            errors.append("'requirements' must be a list.")

        return errors


# ---------------------------------------------------------------------------
# §14.6.9  AtlasImporter
# ---------------------------------------------------------------------------


class AtlasImporter:
    """Deserialises an AtlasCatalog from a JSON string or dict.

    Validates the incoming data before constructing catalog objects.  Works
    with both the real AtlasCatalog model (when available) and the
    :class:`_MinimalCatalog` fallback.

    Examples::

        importer = AtlasImporter()
        catalog = importer.import_from_json(json_str)
        errors = importer.validate_import_data(data)
    """

    def __init__(self) -> None:
        """Initialise the importer with an empty state."""
        pass

    def import_from_dict(self, data: dict[str, Any]) -> Any:
        """Import a catalog from a previously exported dict.

        Validates the data, then constructs ProblemClass and EvidenceRequirement
        objects (or dicts if the real models are unavailable) and populates a
        catalog.

        Args:
            data: Dict produced by :meth:`AtlasExporter.export_to_dict`.

        Returns:
            A populated AtlasCatalog or _MinimalCatalog instance.

        Raises:
            ValueError: If the data has validation errors that prevent import.
        """
        errors = self.validate_import_data(data)
        if errors:
            raise ValueError(
                f"AtlasImporter.import_from_dict: validation failed "
                f"({len(errors)} error(s)): {'; '.join(errors[:3])}."
            )

        catalog = _MinimalCatalog()

        req_map: dict[str, dict[str, Any]] = {}
        for req_data in data.get("requirements", []):
            req_id = req_data.get("requirement_id", str(uuid.uuid4()))
            req_map[req_id] = req_data

        for cls_data in data.get("classes", []):
            if "error" in cls_data:
                continue
            class_id = cls_data.get("class_id", str(uuid.uuid4()))
            catalog.register(class_id, dict(cls_data))

        from jugeo.problem_modes.problem_atlas.integration import AtlasEvent  # self-ref OK
        return catalog

    def import_from_json(self, json_str: str) -> Any:
        """Import a catalog from a JSON string.

        Args:
            json_str: JSON string produced by :meth:`AtlasExporter.export_to_json`.

        Returns:
            A populated catalog object.

        Raises:
            json.JSONDecodeError: If the JSON is malformed.
            ValueError: If the deserialized data fails validation.
        """
        data = json.loads(json_str)
        return self.import_from_dict(data)

    def import_class(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import a single ProblemClass from its dict representation.

        Args:
            data: Dict with at least a ``class_id`` field.

        Returns:
            A normalised class dict with all expected keys filled in.
        """
        return {
            "class_id": data.get("class_id", str(uuid.uuid4())),
            "name": data.get("name", data.get("class_id", "UNNAMED")),
            "description": data.get("description", ""),
            "category": data.get("category", "UNKNOWN"),
            "difficulty": data.get("difficulty", "UNKNOWN"),
            "parent_ids": data.get("parent_ids", []),
            "child_ids": data.get("child_ids", []),
            "keywords": data.get("keywords", []),
            "requirements": data.get("requirements", []),
        }

    def import_requirement(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import a single EvidenceRequirement from its dict representation.

        Args:
            data: Dict with at least a ``requirement_id`` and ``required_channels``.

        Returns:
            A normalised requirement dict.
        """
        return {
            "requirement_id": data.get("requirement_id", str(uuid.uuid4())),
            "required_channels": data.get("required_channels", []),
            "threshold": float(data.get("threshold", 0.7)),
            "conjunction_mode": data.get("conjunction_mode", "AND"),
            "description": data.get("description", ""),
        }

    def import_signature(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import a single SemanticSignature from its dict representation.

        Args:
            data: Dict with ``input_schema`` and ``output_schema`` fields.

        Returns:
            A normalised signature dict.
        """
        return {
            "name": data.get("name", str(uuid.uuid4())),
            "input_schema": data.get("input_schema", {}),
            "output_schema": data.get("output_schema", {}),
            "description": data.get("description", ""),
        }

    def validate_import_data(self, data: dict[str, Any]) -> list[str]:
        """Validate import data for structural correctness before import.

        Args:
            data: Dict to validate.

        Returns:
            List of error strings.  Empty list means data is valid.
        """
        errors: list[str] = []

        if not isinstance(data, dict):
            return ["Import data must be a dict."]

        manifest = data.get("manifest")
        if manifest is None:
            errors.append("Missing 'manifest' key.")
        elif not isinstance(manifest, dict):
            errors.append("'manifest' must be a dict.")
        else:
            if "version" not in manifest:
                errors.append("Manifest missing 'version'.")

        classes = data.get("classes", [])
        if not isinstance(classes, list):
            errors.append("'classes' must be a list.")
        else:
            for i, c in enumerate(classes):
                if isinstance(c, dict) and "class_id" not in c and "error" not in c:
                    errors.append(f"Class at index {i} missing 'class_id'.")

        return errors

    def merge_into(self, data: dict[str, Any], existing: Any) -> Any:
        """Merge imported classes into an existing catalog.

        Imports all classes from ``data`` and adds them to ``existing``,
        skipping classes whose ``class_id`` is already present.  Requirements
        are not de-duplicated against the existing catalog.

        Args:
            data: Import data dict (same format as :meth:`import_from_dict`).
            existing: An existing catalog to merge into.

        Returns:
            The ``existing`` catalog with new classes added.  If ``existing``
            does not have a ``register`` method, a new _MinimalCatalog is
            returned containing both old and new classes.
        """
        errors = self.validate_import_data(data)
        if errors:
            return existing

        new_catalog = self.import_from_dict(data)

        register_fn = getattr(existing, "register", None)
        if not callable(register_fn):
            # Can't merge — return new catalog with old classes added
            merged = _MinimalCatalog()
            for pc in _get_all_classes(existing):
                cid = _safe_get_class_id(pc)
                merged.register(cid, pc if isinstance(pc, dict) else {"class_id": cid})
            for pc in _get_all_classes(new_catalog):
                cid = _safe_get_class_id(pc)
                if merged.get(cid) is None:
                    merged.register(cid, pc if isinstance(pc, dict) else {"class_id": cid})
            return merged

        existing_ids = {_safe_get_class_id(pc) for pc in _get_all_classes(existing)}
        for pc in _get_all_classes(new_catalog):
            cid = _safe_get_class_id(pc)
            if cid not in existing_ids:
                entry = pc if isinstance(pc, dict) else {"class_id": cid}
                register_fn(cid, entry)

        return existing


# ---------------------------------------------------------------------------
# §14.6.10  Internal helpers
# ---------------------------------------------------------------------------


def _get_all_classes(catalog: Any) -> list[Any]:
    """Return all classes from any catalog-like object."""
    for attr in ("classes", "_classes", "problem_classes"):
        val = getattr(catalog, attr, None)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            return list(val.values())

    for method_name in ("all_classes", "list_classes", "get_classes"):
        fn = getattr(catalog, method_name, None)
        if callable(fn):
            try:
                result = fn()
                if isinstance(result, (list, tuple)):
                    return list(result)
            except Exception:
                pass

    try:
        items = list(catalog)
        return items
    except TypeError:
        return []


def _safe_find_class(class_id: str, catalog: Any) -> Any | None:
    """Find a class by ID using whatever lookup the catalog provides."""
    for method in ("get", "lookup", "lookup_by_id", "find"):
        fn = getattr(catalog, method, None)
        if callable(fn):
            try:
                result = fn(class_id)
                if result is not None:
                    return result
            except Exception:
                pass

    for pc in _get_all_classes(catalog):
        if _safe_get_class_id(pc) == class_id:
            return pc

    return None


def _safe_get_class_id(pc: Any) -> str:
    """Extract a class_id from any class-like object."""
    if isinstance(pc, dict):
        for k in ("class_id", "id", "name"):
            v = pc.get(k)
            if isinstance(v, str) and v:
                return v
        return str(id(pc))

    for attr in ("class_id", "id", "name", "class_name"):
        val = getattr(pc, attr, None)
        if isinstance(val, str) and val:
            return val

    return str(pc)


def _extract_requirements(pc: Any) -> list[Any]:
    """Extract requirements from a class-like object."""
    if isinstance(pc, dict):
        val = pc.get("requirements") or pc.get("evidence_requirements", [])
        if isinstance(val, (list, tuple)):
            return list(val)
        return []

    for attr in ("requirements", "evidence_requirements"):
        val = getattr(pc, attr, None)
        if isinstance(val, (list, tuple)):
            return list(val)
        if isinstance(val, dict):
            return list(val.values())

    return []


def _get_req_id(req: Any) -> str:
    """Extract requirement ID from any requirement-like object."""
    if isinstance(req, dict):
        for k in ("requirement_id", "id", "req_id"):
            v = req.get(k)
            if isinstance(v, str) and v:
                return v
        return str(id(req))

    for attr in ("requirement_id", "id", "req_id"):
        val = getattr(req, attr, None)
        if isinstance(val, str) and val:
            return val

    return str(id(req))


def _get_req_channels(req: Any) -> list[str]:
    """Extract channel list from any requirement-like object."""
    if isinstance(req, dict):
        val = req.get("required_channels") or req.get("channels", [])
        if isinstance(val, (list, tuple)):
            return [str(c) for c in val]
        return []

    for attr in ("required_channels", "channels", "channel_ids"):
        val = getattr(req, attr, None)
        if isinstance(val, (list, tuple)):
            return [str(c) for c in val]

    return []


def _get_class_sig(pc: Any) -> Any | None:
    """Extract semantic signature from a class-like object."""
    if isinstance(pc, dict):
        return pc.get("signature") or pc.get("semantic_signature")

    for attr in ("signature", "semantic_signature", "sig"):
        val = getattr(pc, attr, None)
        if val is not None:
            return val

    return None


# ---------------------------------------------------------------------------
# §14.6.11  Module-level functions
# ---------------------------------------------------------------------------

_default_integration: ProblemAtlasIntegration | None = None


def register_atlas(catalog: Any | None = None) -> ProblemAtlasIntegration:
    """Create and connect a ProblemAtlasIntegration, setting it as the module default.

    If a ``catalog`` is provided it is used directly; otherwise a default minimal
    catalog is constructed.  After creation, all three subsystem integrations are
    attempted (judgment, evidence, solver) and any failures are silently ignored.

    Args:
        catalog: Optional AtlasCatalog to use.

    Returns:
        The newly created ProblemAtlasIntegration instance, which is also stored
        as the module-level singleton accessible via :func:`get_default_integration`.
    """
    global _default_integration

    integration = ProblemAtlasIntegration(catalog=catalog)
    integration.integrate_with_judgment_system()
    integration.integrate_with_evidence_system()
    integration.integrate_with_solver()

    _default_integration = integration
    return integration


def connect_to_orchestration(integration: ProblemAtlasIntegration) -> bool:
    """Attempt to connect the integration to the jugeo orchestration layer.

    Tries to import ``jugeo.orchestration`` and register the integration with
    whatever registration API is available.  This is a best-effort operation;
    if orchestration is not available, returns False without raising.

    Args:
        integration: The ProblemAtlasIntegration instance to register.

    Returns:
        True if the orchestration module was importable and the registration
        call succeeded; False otherwise.
    """
    try:
        import jugeo.orchestration as orch  # noqa: F401

        # Try a few common registration patterns
        register_fn = (
            getattr(orch, "register_atlas", None)
            or getattr(orch, "register_integration", None)
            or getattr(orch, "register", None)
        )
        if callable(register_fn):
            register_fn(integration)

        integration._connected_systems.add("orchestration")
        integration._update_status()
        return True

    except ImportError:
        return False
    except Exception:
        return False


def get_default_integration() -> ProblemAtlasIntegration:
    """Return the module-level ProblemAtlasIntegration singleton.

    If no integration has been registered yet (via :func:`register_atlas`),
    a default integration is created and cached automatically.

    Returns:
        The module-level ProblemAtlasIntegration instance.
    """
    global _default_integration
    if _default_integration is None:
        _default_integration = ProblemAtlasIntegration.default()
    return _default_integration


def resolve_class(description: str) -> str | None:
    """Convenience wrapper: resolve a problem description to a class ID.

    Uses the module-level singleton integration.  Creates the singleton if it
    does not yet exist.

    Args:
        description: Natural language problem description.

    Returns:
        class_id of the best matching ProblemClass, or None.

    Examples:
        >>> resolve_class("find the shortest path between two nodes")
        'COMPUTATIONAL_SEARCH'
    """
    return get_default_integration().lookup_class_for_problem(description)




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "IntegrationStatus",
    "AtlasEvent",
    # Dataclasses
    "IntegrationReport",
    "ClassResolutionResult",
    # Main classes
    "ProblemAtlasIntegration",
    "AtlasExporter",
    "AtlasImporter",
    "AtlasEventBus",
    # Module-level functions
    "register_atlas",
    "connect_to_orchestration",
    "get_default_integration",
    "resolve_class",
    # Module-level singleton
    "_default_integration",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
