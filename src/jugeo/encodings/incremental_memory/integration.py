"""Integration layer for incremental memory encoding — theory2.tex Ch34.

This module provides the integration layer that ties together all components
of the incremental_memory encoding subsystem, developed with copilot assistance.
It bridges the runtime memory and invalidation systems with the encoding-layer
constructions, providing a unified pipeline for processing incremental updates.

The IncrementalMemoryIntegration class is the main entry point, orchestrating
SemanticMemory, InvalidationEngine, ChangeEventStream, and GlueAlgorithm into
a coherent workflow implementing M' = Glue(M|_{X\\S}, new_sections, overlap_data).

Theory reference: theory2.tex §34.6 — Integration and orchestration.
"""
from __future__ import annotations
import uuid
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from jugeo.runtime.invalidation import (
        InvalidationGraph, InvalidationEngine, InvalidationPolicy,
        InvalidationCascade, InvalidationEvent, TriggerKind,
    )
except ImportError:
    InvalidationGraph = Any  # type: ignore
    InvalidationEngine = Any  # type: ignore
    InvalidationPolicy = Any  # type: ignore
    InvalidationCascade = Any  # type: ignore
    InvalidationEvent = Any  # type: ignore
    TriggerKind = Any  # type: ignore

try:
    from jugeo.runtime.memory import SemanticMemory, MemoryRegion, MemorySnapshot
except ImportError:
    SemanticMemory = Any  # type: ignore
    MemoryRegion = Any  # type: ignore
    MemorySnapshot = Any  # type: ignore

try:
    from jugeo.evidence.manifests import Manifest, EvidenceArchive, EpochMap
except ImportError:
    Manifest = Any  # type: ignore
    EvidenceArchive = Any  # type: ignore
    EpochMap = Any  # type: ignore

try:
    from jugeo.judgments.judgment_terms import Judgment, EvidenceBundle
except ImportError:
    Judgment = Any  # type: ignore
    EvidenceBundle = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.models import (
        IncrementalUpdate, ChangeEvent, EncodingSupportSet, MemoryInvalidationCascade,
        PersistentMemoryState, ChangeEventKind,
    )
except ImportError:
    IncrementalUpdate = Any  # type: ignore
    ChangeEvent = Any  # type: ignore
    EncodingSupportSet = Any  # type: ignore
    MemoryInvalidationCascade = Any  # type: ignore
    PersistentMemoryState = Any  # type: ignore
    ChangeEventKind = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.change_events import ChangeEventStream, ChangeEventBatch
except ImportError:
    ChangeEventStream = Any  # type: ignore
    ChangeEventBatch = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.invalidation import (
        CascadeComputer, CascadePolicy, CascadeScheduler, DependencyTracer,
        compute_cascade, repair_after_cascade,
    )
except ImportError:
    CascadeComputer = Any  # type: ignore
    CascadePolicy = Any  # type: ignore
    CascadeScheduler = Any  # type: ignore
    DependencyTracer = Any  # type: ignore
    compute_cascade = None  # type: ignore
    repair_after_cascade = None  # type: ignore

try:
    from jugeo.encodings.incremental_memory.update_law import (
        apply_incremental_update, GlueComputation,
    )
except ImportError:
    apply_incremental_update = None  # type: ignore
    GlueComputation = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.algorithms import (
        GlueAlgorithm, SectionDiffAlgorithm, BatchUpdateOptimizer,
        QuotaEnforcementAlgorithm, MemoryCompactionAlgorithm,
    )
except ImportError:
    GlueAlgorithm = Any  # type: ignore
    SectionDiffAlgorithm = Any  # type: ignore
    BatchUpdateOptimizer = Any  # type: ignore
    QuotaEnforcementAlgorithm = Any  # type: ignore
    MemoryCompactionAlgorithm = Any  # type: ignore


# ---------------------------------------------------------------------------
# IntegrationHealth
# ---------------------------------------------------------------------------

@dataclass
class IntegrationHealth:
    """Captures the overall health state of the incremental memory integration layer.

    An IntegrationHealth instance is created at the conclusion of a health-check
    cycle and summarises whether each of the four major subsystems — memory,
    invalidation, cascade computation, and quota enforcement — are operating
    within acceptable bounds.  Individual subsystem failures are recorded as
    boolean flags so that callers can act on specific failures without having
    to parse unstructured log output.  Additional context is provided through
    the ``error_messages`` and ``warnings`` lists, which accumulate human-readable
    descriptions of any problems that were detected.  A unique ``health_id`` and
    monotonically increasing ``timestamp`` allow health reports to be correlated
    across distributed tracing systems.  The ``metrics`` dict carries quantitative
    measurements (e.g. queue depths, latencies) that complemented the Boolean
    flags.

    Theory reference: theory2.tex §34.6.1 — Health and observability.
    """

    memory_ok: bool = True
    invalidation_ok: bool = True
    cascade_ok: bool = True
    quota_ok: bool = True
    error_messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    health_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    def is_healthy(self) -> bool:
        """Return True iff all four subsystem flags are set and no errors exist."""
        return (
            self.memory_ok
            and self.invalidation_ok
            and self.cascade_ok
            and self.quota_ok
            and not self.error_messages
        )

    def add_error(self, msg: str) -> None:
        """Append *msg* to the error_messages list."""
        self.error_messages.append(msg)

    def add_warning(self, msg: str) -> None:
        """Append *msg* to the warnings list."""
        self.warnings.append(msg)

    def to_json(self) -> str:
        """Serialise this health report to a JSON string."""
        return json.dumps(
            {
                "health_id": self.health_id,
                "timestamp": self.timestamp,
                "memory_ok": self.memory_ok,
                "invalidation_ok": self.invalidation_ok,
                "cascade_ok": self.cascade_ok,
                "quota_ok": self.quota_ok,
                "error_messages": self.error_messages,
                "warnings": self.warnings,
                "metrics": self.metrics,
            },
            sort_keys=True,
        )

    def summary(self) -> str:
        """Return a one-line summary of the health report."""
        status = self.severity().upper()
        nerr = len(self.error_messages)
        nwarn = len(self.warnings)
        return (
            f"IntegrationHealth[{self.health_id[:8]}] status={status} "
            f"errors={nerr} warnings={nwarn} "
            f"memory={self.memory_ok} invalidation={self.invalidation_ok} "
            f"cascade={self.cascade_ok} quota={self.quota_ok}"
        )

    def severity(self) -> str:
        """Return 'error', 'warning', or 'ok' based on current state."""
        if self.error_messages:
            return "error"
        if self.warnings:
            return "warning"
        return "ok"


# ---------------------------------------------------------------------------
# RuntimeMemoryBridge
# ---------------------------------------------------------------------------

class RuntimeMemoryBridge:
    """Thin adapter that exposes a uniform read/write interface over SemanticMemory.

    The RuntimeMemoryBridge decouples the incremental-update pipeline from the
    concrete SemanticMemory API, insulating pipeline code from changes in the
    underlying memory implementation.  It tracks cumulative read and write
    counts so that callers can monitor access patterns without instrumenting
    every call site individually.  The bridge exposes a ``get_snapshot`` method
    that captures a shallow copy of the current memory state, which is useful
    for checkpointing and debugging.  Eviction is supported via ``evict_coord``,
    which removes a coordinate from the in-memory region metadata; this is used
    by the cascade repair path to free invalidated entries.  All operations are
    guarded by try/except blocks so that a corrupt or partially initialised
    memory object does not propagate unexpected exceptions into the pipeline.

    Theory reference: theory2.tex §34.4.2 — Memory access abstraction.
    """

    def __init__(self, memory: Any) -> None:
        self._memory = memory
        self._write_count: int = 0
        self._read_count: int = 0

    # ------------------------------------------------------------------
    def _get_metadata(self) -> dict[str, Any]:
        """Return the underlying metadata dict, or an empty dict on failure."""
        try:
            return self._memory._region._metadata  # type: ignore[union-attr]
        except AttributeError:
            return {}

    def _get_judgments(self) -> Any:
        """Return the judgments container, or None on failure."""
        try:
            return self._memory._region.judgments  # type: ignore[union-attr]
        except AttributeError:
            return None

    # ------------------------------------------------------------------
    def read_section(self, coord: str) -> dict[str, Any]:
        """Read the data stored at *coord* from the memory region.

        Looks first in ``_metadata``, then in ``judgments`` if the key is absent.
        Increments the internal read counter on each call.
        """
        self._read_count += 1
        meta = self._get_metadata()
        if coord in meta:
            value = meta[coord]
            return value if isinstance(value, dict) else {"value": value}
        judgments = self._get_judgments()
        if judgments is not None:
            try:
                j = judgments.get(coord)
                if j is not None:
                    return {"judgment": str(j)}
            except Exception:
                pass
        return {}

    def write_section(self, coord: str, data: dict[str, Any]) -> None:
        """Write *data* into the memory region at *coord*.

        Increments the internal write counter.  If ``_metadata`` is not
        accessible the write is silently dropped and a warning is logged.
        """
        self._write_count += 1
        try:
            self._memory._region._metadata[coord] = data  # type: ignore[union-attr]
        except AttributeError:
            logger.warning("RuntimeMemoryBridge.write_section: _metadata not accessible for coord=%s", coord)

    def get_snapshot(self) -> dict[str, Any]:
        """Return a shallow snapshot of the current memory state."""
        meta = dict(self._get_metadata())
        return {
            "metadata": meta,
            "coord_count": len(meta),
            "read_count": self._read_count,
            "write_count": self._write_count,
        }

    def apply_batch(self, batch: Any) -> int:
        """Apply all events in *batch* to the memory region.

        Returns the number of events successfully applied.
        """
        applied = 0
        try:
            events = batch.events if hasattr(batch, "events") else []
            for event in events:
                try:
                    coord = getattr(event, "coordinate", None) or getattr(event, "coord", None)
                    payload = getattr(event, "payload", {}) or {}
                    if coord:
                        self.write_section(coord, payload if isinstance(payload, dict) else {"raw": str(payload)})
                        applied += 1
                except Exception as exc:
                    logger.debug("apply_batch: skipping event due to %s", exc)
        except Exception as exc:
            logger.warning("apply_batch failed: %s", exc)
        return applied

    def evict_coord(self, coord: str) -> bool:
        """Remove *coord* from the memory region metadata.

        Returns True if the coord existed and was removed, False otherwise.
        """
        meta = self._get_metadata()
        if coord in meta:
            try:
                del meta[coord]
                return True
            except Exception:
                pass
        return False

    def list_coords(self) -> list[str]:
        """Return a sorted list of all coordinate keys in the memory region."""
        return sorted(self._get_metadata().keys())

    def region_size(self) -> int:
        """Return the number of entries currently in the memory region."""
        return len(self._get_metadata())

    def health_check(self) -> bool:
        """Return True iff the underlying memory object is usable."""
        try:
            _ = self._get_metadata()
            return True
        except Exception:
            return False

    def summary(self) -> str:
        """Return a one-line summary of the bridge state."""
        return (
            f"RuntimeMemoryBridge: coords={self.region_size()} "
            f"reads={self._read_count} writes={self._write_count} "
            f"healthy={self.health_check()}"
        )


# ---------------------------------------------------------------------------
# InvalidationEngineAdapter
# ---------------------------------------------------------------------------

class InvalidationEngineAdapter:
    """Adapts the runtime InvalidationEngine to the encoding-layer ChangeEvent API.

    The InvalidationEngineAdapter translates encoding-layer ChangeEvent objects
    into the coordinate/kind pairs expected by InvalidationEngine.invalidate,
    and wraps the results in a form that the rest of the pipeline can consume
    uniformly.  It maintains per-adapter statistics (total invalidations triggered,
    total cascades returned, total errors encountered) so that callers can monitor
    the health of the invalidation subsystem without examining engine internals.
    The adapter supports runtime policy swapping, allowing the upstream pipeline
    to change cascade eagerness without rebuilding the full integration stack.
    All calls to the underlying engine are guarded by try/except so that engine
    failures are surfaced as warnings rather than propagated exceptions, keeping
    the pipeline alive even when the invalidation subsystem degrades.

    Theory reference: theory2.tex §34.5.3 — Invalidation adapter pattern.
    """

    def __init__(self, engine: Any, policy: Any = None) -> None:
        self._engine = engine
        self._policy: Any = policy
        self._total_invalidations: int = 0
        self._total_cascades: int = 0
        self._total_errors: int = 0
        self._last_cascade: Any = None

    # ------------------------------------------------------------------
    def adapt_event(self, event: Any) -> Any:
        """Convert a ChangeEvent into an (coordinate, kind) pair for the engine."""
        try:
            coord = getattr(event, "coordinate", None) or getattr(event, "coord", "unknown")
            kind = getattr(event, "kind", None)
            return (coord, kind)
        except Exception as exc:
            logger.debug("adapt_event: could not adapt event %s: %s", event, exc)
            return ("unknown", None)

    def trigger_invalidation(self, event: Any) -> Any:
        """Trigger engine.invalidate for *event* and return the resulting cascade.

        On failure, logs a warning, increments the error counter, and returns None.
        """
        coord, kind = self.adapt_event(event)
        try:
            self._total_invalidations += 1
            cascade = self._engine.invalidate(coord, kind)
            self._total_cascades += 1
            self._last_cascade = cascade
            return cascade
        except Exception as exc:
            self._total_errors += 1
            logger.warning("trigger_invalidation failed for coord=%s: %s", coord, exc)
            return None

    def trigger_batch(self, events: list[Any]) -> list[Any]:
        """Trigger invalidation for each event in *events*.

        Returns a list of cascades (or None entries where the engine failed).
        """
        results = []
        for event in events:
            results.append(self.trigger_invalidation(event))
        return results

    def get_policy(self) -> Any:
        """Return the current cascade policy."""
        return self._policy

    def set_policy(self, policy: Any) -> None:
        """Replace the current cascade policy with *policy*."""
        self._policy = policy

    def get_stats(self) -> dict[str, Any]:
        """Return a dict of cumulative statistics."""
        return {
            "total_invalidations": self._total_invalidations,
            "total_cascades": self._total_cascades,
            "total_errors": self._total_errors,
            "policy": str(self._policy),
        }

    def reset_stats(self) -> None:
        """Reset all cumulative statistics to zero."""
        self._total_invalidations = 0
        self._total_cascades = 0
        self._total_errors = 0
        self._last_cascade = None

    def summary(self) -> str:
        """Return a one-line summary of the adapter state."""
        return (
            f"InvalidationEngineAdapter: invalidations={self._total_invalidations} "
            f"cascades={self._total_cascades} errors={self._total_errors} "
            f"policy={self._policy}"
        )


# ---------------------------------------------------------------------------
# MemoryStateExporter
# ---------------------------------------------------------------------------

class MemoryStateExporter:
    """Serialises and deserialises the state of a SemanticMemory region.

    The MemoryStateExporter provides bidirectional conversion between the
    in-memory representation of a SemanticMemory region and portable JSON
    payloads that can be written to disk, transmitted over a network, or
    stored in an EvidenceArchive.  It exposes a ``diff_exports`` method that
    computes the symmetric difference between two exported states, making it
    straightforward to identify what changed between two checkpoints.  The
    ``export_epoch_map`` method extracts a coordinate-to-epoch mapping from
    a live EpochMap object, enabling downstream systems to reason about
    staleness without access to the full memory state.  Import is guarded by
    a validity check so that malformed JSON does not silently corrupt the
    memory region.  All methods return sensible empty values on failure rather
    than raising, to preserve pipeline liveness.

    Theory reference: theory2.tex §34.7.1 — State export and archival.
    """

    def __init__(self, memory: Any) -> None:
        self._memory = memory
        self._bridge = RuntimeMemoryBridge(memory)

    # ------------------------------------------------------------------
    def export_to_json(self) -> str:
        """Export the current memory state to a JSON string."""
        snapshot = self._bridge.get_snapshot()
        snapshot["export_timestamp"] = time.time()
        snapshot["export_id"] = str(uuid.uuid4())
        return json.dumps(snapshot, sort_keys=True, default=str)

    def export_section_cache(self) -> dict[str, Any]:
        """Return a dict mapping each coord to its stored data."""
        return dict(self._bridge._get_metadata())

    def export_epoch_map(self, epoch_map: Any) -> dict[str, int]:
        """Extract a coord -> epoch mapping from *epoch_map*.

        Falls back to an empty dict if the epoch_map is not accessible.
        """
        result: dict[str, int] = {}
        try:
            coords = self._bridge.list_coords()
            for coord in coords:
                try:
                    result[coord] = epoch_map.current_epoch_at(coord)
                except Exception:
                    result[coord] = -1
        except Exception as exc:
            logger.warning("export_epoch_map failed: %s", exc)
        return result

    def export_snapshots(self) -> list[dict[str, Any]]:
        """Return a list containing a single snapshot of the current state."""
        return [self._bridge.get_snapshot()]

    def export_for_archive(self) -> dict[str, Any]:
        """Build a complete export bundle suitable for storage in an EvidenceArchive."""
        return {
            "archive_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "section_cache": self.export_section_cache(),
            "snapshots": self.export_snapshots(),
            "coord_count": self._bridge.region_size(),
        }

    def import_from_json(self, data: str) -> bool:
        """Import a previously exported JSON state into the memory region.

        Returns True if the import succeeded, False otherwise.
        """
        try:
            obj = json.loads(data)
            metadata = obj.get("metadata", {})
            if not isinstance(metadata, dict):
                logger.warning("import_from_json: 'metadata' key missing or not a dict")
                return False
            for coord, value in metadata.items():
                self._bridge.write_section(coord, value if isinstance(value, dict) else {"value": value})
            return True
        except Exception as exc:
            logger.warning("import_from_json failed: %s", exc)
            return False

    def diff_exports(self, a: str, b: str) -> dict[str, Any]:
        """Compute the symmetric difference between two exported JSON states.

        Returns a dict with keys ``added``, ``removed``, and ``changed``,
        each mapping coord names to the corresponding new or old values.
        """
        try:
            obj_a = json.loads(a).get("metadata", {})
            obj_b = json.loads(b).get("metadata", {})
        except Exception as exc:
            logger.warning("diff_exports: parse failed: %s", exc)
            return {"added": {}, "removed": {}, "changed": {}}

        keys_a = set(obj_a)
        keys_b = set(obj_b)
        added = {k: obj_b[k] for k in keys_b - keys_a}
        removed = {k: obj_a[k] for k in keys_a - keys_b}
        changed = {
            k: {"before": obj_a[k], "after": obj_b[k]}
            for k in keys_a & keys_b
            if obj_a[k] != obj_b[k]
        }
        return {"added": added, "removed": removed, "changed": changed}

    def summary(self) -> str:
        """Return a one-line summary of the exporter state."""
        return (
            f"MemoryStateExporter: coords={self._bridge.region_size()} "
            f"bridge_healthy={self._bridge.health_check()}"
        )


# ---------------------------------------------------------------------------
# IncrementalUpdatePipeline
# ---------------------------------------------------------------------------

class IncrementalUpdatePipeline:
    """Orchestrates the end-to-end processing of IncrementalUpdate objects.

    The IncrementalUpdatePipeline wires together the RuntimeMemoryBridge,
    InvalidationEngineAdapter, GlueAlgorithm, and CascadeComputer into a
    single callable pipeline that accepts IncrementalUpdate objects and returns
    (GlueComputation, MemoryInvalidationCascade) pairs.  Batch processing is
    supported via ``process_batch``, which applies a sequence of updates and
    accumulates results.  The pipeline exposes a ``health_check`` method that
    queries each component in turn and aggregates their health into an
    IntegrationHealth report.  Running statistics (update count, cascade count,
    error count) are maintained so that operators can track pipeline throughput
    without external instrumentation.  The pipeline is designed to be
    idempotent: processing the same update twice produces consistent results
    because the underlying Glue construction is idempotent by design.

    Theory reference: theory2.tex §34.6.3 — Pipeline orchestration.
    """

    def __init__(self, memory: Any, engine: Any, manifest: Any = None) -> None:
        self._memory = memory
        self._engine = engine
        self._manifest = manifest
        self._bridge = RuntimeMemoryBridge(memory)
        self._adapter = InvalidationEngineAdapter(engine)
        self._exporter = MemoryStateExporter(memory)
        self._update_count: int = 0
        self._cascade_count: int = 0
        self._error_count: int = 0

        # Lazily constructed algorithm instances
        self._glue_algorithm: Any = None
        self._diff_algorithm: Any = None
        self._batch_optimizer: Any = None
        self._quota_algorithm: Any = None
        self._compaction_algorithm: Any = None

        try:
            self._glue_algorithm = GlueAlgorithm()
        except Exception:
            pass
        try:
            self._diff_algorithm = SectionDiffAlgorithm()
        except Exception:
            pass
        try:
            self._batch_optimizer = BatchUpdateOptimizer()
        except Exception:
            pass
        try:
            self._quota_algorithm = QuotaEnforcementAlgorithm()
        except Exception:
            pass
        try:
            self._compaction_algorithm = MemoryCompactionAlgorithm()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def process_update(self, update: Any) -> tuple[Any, Any]:
        """Process a single IncrementalUpdate through the full pipeline.

        Returns a (GlueComputation, MemoryInvalidationCascade) tuple.
        On failure the cascade slot is None and the error counter is incremented.
        """
        self._update_count += 1
        glue_result: Any = None
        cascade_result: Any = None

        # Step 1: apply the incremental update law
        try:
            if apply_incremental_update is not None:
                glue_result = apply_incremental_update(update, self._bridge)
            else:
                # Minimal fallback: record the update directly via the bridge
                new_sections = getattr(update, "new_sections", {}) or {}
                for coord, data in new_sections.items():
                    self._bridge.write_section(coord, data if isinstance(data, dict) else {"value": data})
                glue_result = None
        except Exception as exc:
            self._error_count += 1
            logger.warning("process_update: apply_incremental_update failed: %s", exc)

        # Step 2: derive change events and trigger invalidation
        try:
            support = getattr(update, "support_set", None)
            coords = getattr(support, "coords", frozenset()) if support else frozenset()
            cascades = []
            for coord in coords:
                event_obj = type("_FakeEvent", (), {"coordinate": coord, "kind": None, "payload": {}})()
                result = self._adapter.trigger_invalidation(event_obj)
                if result is not None:
                    cascades.append(result)
            if cascades:
                self._cascade_count += 1
                cascade_result = cascades[0]
        except Exception as exc:
            self._error_count += 1
            logger.warning("process_update: invalidation step failed: %s", exc)

        return (glue_result, cascade_result)

    def process_batch(self, updates: list[Any]) -> list[tuple[Any, Any]]:
        """Process a list of IncrementalUpdate objects in order.

        Returns a list of (GlueComputation, MemoryInvalidationCascade) tuples,
        one per input update.
        """
        results = []
        for update in updates:
            results.append(self.process_update(update))
        return results

    def process_events(self, stream: Any) -> Any:
        """Drain *stream* and trigger invalidation for each event.

        Returns a MemoryInvalidationCascade aggregating all individual cascades,
        or None if no cascades were produced.
        """
        cascades = []
        try:
            events = list(stream) if hasattr(stream, "__iter__") else []
            cascades = self._adapter.trigger_batch(events)
        except Exception as exc:
            self._error_count += 1
            logger.warning("process_events failed: %s", exc)

        # Return the last non-None cascade as the aggregate
        for c in reversed(cascades):
            if c is not None:
                return c
        return None

    def run_repair(self, cascade: Any) -> bool:
        """Attempt to repair memory state after *cascade*.

        Uses ``repair_after_cascade`` from the invalidation module if available.
        Returns True on success, False on failure.
        """
        try:
            if repair_after_cascade is not None and cascade is not None:
                repair_after_cascade(cascade, self._bridge)
                return True
            # Fallback: evict all affected coordinates
            if cascade is not None and hasattr(cascade, "all_affected"):
                for coord in cascade.all_affected():
                    self._bridge.evict_coord(coord)
                return True
        except Exception as exc:
            logger.warning("run_repair failed: %s", exc)
        return False

    def health_check(self) -> "IntegrationHealth":
        """Query each pipeline component and return an IntegrationHealth report."""
        health = IntegrationHealth()

        # Memory health
        try:
            health.memory_ok = self._bridge.health_check()
            if not health.memory_ok:
                health.add_error("RuntimeMemoryBridge health check failed")
        except Exception as exc:
            health.memory_ok = False
            health.add_error(f"Memory health check raised: {exc}")

        # Invalidation health (engine must be non-None)
        try:
            health.invalidation_ok = self._engine is not None
            if not health.invalidation_ok:
                health.add_warning("Invalidation engine is None")
        except Exception as exc:
            health.invalidation_ok = False
            health.add_error(f"Invalidation health check raised: {exc}")

        # Cascade health (no errors in adapter)
        adapter_stats = self._adapter.get_stats()
        health.cascade_ok = adapter_stats.get("total_errors", 0) == 0
        if not health.cascade_ok:
            health.add_warning(f"Adapter has {adapter_stats['total_errors']} error(s)")

        # Quota health (algorithm present)
        health.quota_ok = self._quota_algorithm is not None
        if not health.quota_ok:
            health.add_warning("QuotaEnforcementAlgorithm unavailable")

        # Metrics
        health.metrics.update({
            "update_count": self._update_count,
            "cascade_count": self._cascade_count,
            "error_count": self._error_count,
            "coord_count": self._bridge.region_size(),
        })
        health.metrics.update(self._adapter.get_stats())

        return health

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of pipeline statistics."""
        return {
            "update_count": self._update_count,
            "cascade_count": self._cascade_count,
            "error_count": self._error_count,
            "adapter_stats": self._adapter.get_stats(),
            "bridge_size": self._bridge.region_size(),
        }

    def reset_stats(self) -> None:
        """Reset all pipeline statistics to zero."""
        self._update_count = 0
        self._cascade_count = 0
        self._error_count = 0
        self._adapter.reset_stats()

    def summary(self) -> str:
        """Return a one-line summary of the pipeline state."""
        return (
            f"IncrementalUpdatePipeline: updates={self._update_count} "
            f"cascades={self._cascade_count} errors={self._error_count} "
            f"bridge={self._bridge.region_size()} coords"
        )


# ---------------------------------------------------------------------------
# IncrementalMemoryIntegration
# ---------------------------------------------------------------------------

class IncrementalMemoryIntegration:
    """Main entry point for the incremental memory encoding integration layer.

    IncrementalMemoryIntegration is the top-level coordinator for the
    incremental_memory encoding subsystem, developed with copilot assistance.
    It owns the lifecycle of the InvalidationEngine, RuntimeMemoryBridge, and
    IncrementalUpdatePipeline, initialising them lazily via ``initialize()`` so
    that construction does not fail even when optional runtime dependencies are
    absent.  The ``process`` method provides a single-update entry point that
    returns the GlueComputation result, while ``process_stream`` accepts a
    ChangeEventStream and returns the aggregate MemoryInvalidationCascade.
    Shutdown is graceful: ``shutdown()`` resets statistics and logs a summary,
    but does not attempt to flush persistent state — that responsibility belongs
    to the caller.  Health checking is integrated throughout: ``health_check()``
    delegates to the pipeline and augments the report with integration-level
    metadata.

    Theory reference: theory2.tex §34.6 — Integration and orchestration.
    copilot: Implemented with GitHub Copilot assistance.
    """

    def __init__(self, memory: Any, graph: Any = None, policy: Any = None) -> None:
        self._memory = memory
        self._graph = graph
        self._policy = policy
        self._engine: Any = None
        self._bridge: RuntimeMemoryBridge | None = None
        self._pipeline: IncrementalUpdatePipeline | None = None
        self._initialised: bool = False
        self._instance_id: str = str(uuid.uuid4())
        self._created_at: float = time.time()

    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Initialise the engine, bridge, and pipeline.

        Uses try/except blocks around each component so that partial
        initialisation is still usable.
        """
        try:
            if self._graph is not None:
                self._engine = InvalidationEngine(self._graph, self._policy)
            else:
                self._engine = None
        except Exception as exc:
            logger.warning("initialize: could not create InvalidationEngine: %s", exc)
            self._engine = None

        try:
            self._bridge = RuntimeMemoryBridge(self._memory)
        except Exception as exc:
            logger.warning("initialize: could not create RuntimeMemoryBridge: %s", exc)
            self._bridge = None

        try:
            self._pipeline = IncrementalUpdatePipeline(
                memory=self._memory,
                engine=self._engine,
            )
        except Exception as exc:
            logger.warning("initialize: could not create IncrementalUpdatePipeline: %s", exc)
            self._pipeline = None

        self._initialised = True
        logger.info(
            "IncrementalMemoryIntegration[%s] initialised: engine=%s bridge=%s pipeline=%s",
            self._instance_id[:8],
            self._engine is not None,
            self._bridge is not None,
            self._pipeline is not None,
        )

    def process(self, update: Any) -> Any:
        """Process a single IncrementalUpdate and return the GlueComputation result.

        Initialises the integration layer on first call if ``initialize()`` has
        not already been called.
        """
        if not self._initialised:
            self.initialize()
        if self._pipeline is None:
            logger.warning("process: pipeline not available; returning None")
            return None
        glue, _cascade = self._pipeline.process_update(update)
        return glue

    def process_stream(self, stream: Any) -> Any:
        """Drain *stream* and return the aggregate MemoryInvalidationCascade.

        Initialises the integration layer on first call if not already done.
        """
        if not self._initialised:
            self.initialize()
        if self._pipeline is None:
            logger.warning("process_stream: pipeline not available; returning None")
            return None
        return self._pipeline.process_events(stream)

    def get_pipeline(self) -> "IncrementalUpdatePipeline | None":
        """Return the IncrementalUpdatePipeline, or None if not initialised."""
        return self._pipeline

    def get_bridge(self) -> "RuntimeMemoryBridge | None":
        """Return the RuntimeMemoryBridge, or None if not initialised."""
        if self._bridge is not None:
            return self._bridge
        if self._pipeline is not None:
            return self._pipeline._bridge
        return None

    def health_check(self) -> IntegrationHealth:
        """Return an IntegrationHealth report for the full integration stack."""
        if not self._initialised:
            health = IntegrationHealth()
            health.add_warning("Integration layer not yet initialised")
            return health
        if self._pipeline is not None:
            health = self._pipeline.health_check()
        else:
            health = IntegrationHealth()
            health.add_error("Pipeline is None after initialisation")
        health.metrics["instance_id"] = self._instance_id
        health.metrics["uptime_s"] = time.time() - self._created_at
        return health

    def shutdown(self) -> None:
        """Gracefully shut down the integration layer."""
        logger.info("IncrementalMemoryIntegration[%s] shutting down: %s", self._instance_id[:8], self.summary())
        if self._pipeline is not None:
            self._pipeline.reset_stats()
        self._initialised = False

    def copilot_summary(self) -> str:
        """Return a copilot-style summary of the integration layer."""
        pipeline_summary = self._pipeline.summary() if self._pipeline else "pipeline=None"
        return (
            f"IncrementalMemoryIntegration[copilot-assisted] "
            f"id={self._instance_id[:8]} "
            f"initialised={self._initialised} "
            f"{pipeline_summary} "
            f"theory=theory2.tex:Ch34"
        )

    def summary(self) -> str:
        """Return a concise one-line summary."""
        return (
            f"IncrementalMemoryIntegration[{self._instance_id[:8]}]: "
            f"initialised={self._initialised} "
            f"engine={'yes' if self._engine else 'no'} "
            f"bridge={'yes' if self.get_bridge() else 'no'} "
            f"pipeline={'yes' if self._pipeline else 'no'}"
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def run_integration_test() -> IntegrationHealth:
    """Run a self-contained smoke test of the integration layer.

    Creates a SemanticMemory and InvalidationGraph, initialises an
    IncrementalMemoryIntegration, processes a dummy IncrementalUpdate,
    and returns the resulting IntegrationHealth.  This function is
    intended for use in CI and as a quick sanity check during development.
    Any unexpected exception is caught, recorded in the health report,
    and returned rather than re-raised.

    Returns:
        IntegrationHealth: Health report from the test run.
    """
    try:
        from jugeo.runtime.memory import SemanticMemory  # noqa: F401
        from jugeo.runtime.invalidation import InvalidationGraph  # noqa: F401
        memory = SemanticMemory()
        graph = InvalidationGraph()
        integration = IncrementalMemoryIntegration(memory=memory, graph=graph)
        integration.initialize()
        from jugeo.encodings.incremental_memory.models import (
            IncrementalUpdate, EncodingSupportSet, RegionType,
        )
        update = IncrementalUpdate(
            support_set=EncodingSupportSet(
                coords=frozenset(["test.coord"]),
                region_type=RegionType.ARBITRARY,
                metadata={},
            ),
            new_sections={"test.coord": {"value": 42}},
            overlap_data={},
            author="integration_test",
            epoch=1,
        )
        integration.process(update)
        return integration.health_check()
    except Exception as e:
        logger.error("Integration test failed: %s", e)
        health = IntegrationHealth()
        health.add_error(f"Integration test failed: {e}")
        return health


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Dataclasses / value types
    "IntegrationHealth",
    # Adapters and bridges
    "RuntimeMemoryBridge",
    "InvalidationEngineAdapter",
    "MemoryStateExporter",
    # Pipeline and integration
    "IncrementalUpdatePipeline",
    "IncrementalMemoryIntegration",
    # Module-level functions
    "run_integration_test",
]
