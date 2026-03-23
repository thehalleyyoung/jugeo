"""Incremental Memory Encoding Package — theory2.tex Ch34.

This package implements the incremental memory encoding subsystem for the JuGeo
judgment geometry framework, developed with copilot assistance. It provides
all necessary machinery for the Glue construction M' = Glue(M|_{X\\S},
new_sections, overlap_data) as specified in theory2.tex Chapter 34.

The package integrates with jugeo.runtime.memory (SemanticMemory),
jugeo.runtime.invalidation (InvalidationGraph, InvalidationEngine),
jugeo.evidence.manifests (Manifest, EpochMap), and jugeo.judgments.judgment_terms
to provide a complete encoding layer for incremental semantic memory updates.

Components:
- manifest.py: Package manifest and descriptor infrastructure
- models.py: Core data models (EncodingSupportSet, IncrementalUpdate, ChangeEvent, etc.)
- update_law.py: Glue construction implementation
- change_events.py: Change event streaming and aggregation
- invalidation.py: Encoding-layer invalidation cascade infrastructure
- algorithms.py: Core computational algorithms
- integration.py: Runtime integration and pipeline
- theorems.py: Formal theorem registry and proof infrastructure

copilot: This package was scaffolded and implemented with GitHub Copilot assistance,
aligning with the formal theory in theory2.tex Chapter 34.
"""
from __future__ import annotations

__version__ = "0.1.0"
__chapter_ref__ = "theory2.tex:Ch34"
__author__ = "jugeo-team"

from typing import Any
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional imports — each block is independent so that partial availability
# of sibling modules does not prevent the package from loading.
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.incremental_memory.manifest import (
        EncodingStatus, SubsystemKind, EncodingDescriptor, IncrementalMemoryManifest,
        PackageRegistry, ManifestValidator, build_manifest, validate_manifest,
    )
except ImportError as e:
    logger.warning("Could not import manifest: %s", e)

try:
    from jugeo.encodings.incremental_memory.models import (
        ChangeEventKind, RegionType, EncodingSupportSet, IncrementalUpdate,
        ChangeEvent, InvalidationWaveInfo, MemoryInvalidationCascade, PersistentMemoryState,
    )
except ImportError as e:
    logger.warning("Could not import models: %s", e)

try:
    from jugeo.encodings.incremental_memory.update_law import (
        apply_incremental_update, GlueComputation, GlueOperation, RestrictionOperation,
        OverlapChecker, OverlapData, RestrictionResult,
    )
except ImportError as e:
    logger.warning("Could not import update_law: %s", e)

try:
    from jugeo.encodings.incremental_memory.change_events import (
        ChangeEventStream, ChangeEventBatch,
    )
except ImportError as e:
    logger.warning("Could not import change_events: %s", e)

try:
    from jugeo.encodings.incremental_memory.invalidation import (
        CascadeComputer, CascadePolicy, CascadeScheduler, DependencyTracer,
        compute_cascade, repair_after_cascade,
    )
except ImportError as e:
    logger.warning("Could not import invalidation: %s", e)

try:
    from jugeo.encodings.incremental_memory.algorithms import (
        GlueAlgorithm, SectionDiffAlgorithm, BatchUpdateOptimizer,
        QuotaEnforcementAlgorithm, MemoryCompactionAlgorithm,
    )
except ImportError as e:
    logger.warning("Could not import algorithms: %s", e)

try:
    from jugeo.encodings.incremental_memory.integration import (
        IntegrationHealth, RuntimeMemoryBridge, InvalidationEngineAdapter,
        MemoryStateExporter, IncrementalUpdatePipeline, IncrementalMemoryIntegration,
        run_integration_test,
    )
except ImportError as e:
    logger.warning("Could not import integration: %s", e)

try:
    from jugeo.encodings.incremental_memory.theorems import (
        IncrementalMemoryTheorem, TheoremStatus, ProofStrategy,
        TheoremStatement, ProofWitness,
        SerializationDeterminismProof, GlueCompatibilityProof,
        CascadeTerminationProof, EpochMonotonicityProof,
        IncrementalMemoryTheoremRegistry, verify_theorem, check_all_theorems,
    )
except ImportError as e:
    logger.warning("Could not import theorems: %s", e)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # manifest
    "EncodingStatus",
    "SubsystemKind",
    "EncodingDescriptor",
    "IncrementalMemoryManifest",
    "PackageRegistry",
    "ManifestValidator",
    "build_manifest",
    "validate_manifest",
    # models
    "ChangeEventKind",
    "RegionType",
    "EncodingSupportSet",
    "IncrementalUpdate",
    "ChangeEvent",
    "InvalidationWaveInfo",
    "MemoryInvalidationCascade",
    "PersistentMemoryState",
    # update_law
    "apply_incremental_update",
    "GlueComputation",
    "GlueOperation",
    "RestrictionOperation",
    "OverlapChecker",
    "OverlapData",
    "RestrictionResult",
    # change_events
    "ChangeEventStream",
    "ChangeEventBatch",
    # invalidation
    "CascadeComputer",
    "CascadePolicy",
    "CascadeScheduler",
    "DependencyTracer",
    "compute_cascade",
    "repair_after_cascade",
    # algorithms
    "GlueAlgorithm",
    "SectionDiffAlgorithm",
    "BatchUpdateOptimizer",
    "QuotaEnforcementAlgorithm",
    "MemoryCompactionAlgorithm",
    # integration
    "IntegrationHealth",
    "RuntimeMemoryBridge",
    "InvalidationEngineAdapter",
    "MemoryStateExporter",
    "IncrementalUpdatePipeline",
    "IncrementalMemoryIntegration",
    "run_integration_test",
    # theorems
    "IncrementalMemoryTheorem",
    "TheoremStatus",
    "ProofStrategy",
    "TheoremStatement",
    "ProofWitness",
    "SerializationDeterminismProof",
    "GlueCompatibilityProof",
    "CascadeTerminationProof",
    "EpochMonotonicityProof",
    "IncrementalMemoryTheoremRegistry",
    "verify_theorem",
    "check_all_theorems",
    # package metadata
    "get_package_info",
    # cross-subsystem integration
    "cache_aware_encoding",
    "replay_encoding",
]


def get_package_info() -> dict[str, Any]:
    """Return metadata about the incremental_memory encoding package.

    This function provides a comprehensive summary of the package including
    version, chapter reference, component count, and available exports.
    Developed with copilot assistance.

    Returns:
        dict[str, Any]: Package metadata dictionary with keys: version,
            chapter_ref, author, export_count, components, description.
    """
    return {
        "version": __version__,
        "chapter_ref": __chapter_ref__,
        "author": __author__,
        "export_count": len(__all__),
        "components": [
            "manifest",
            "models",
            "update_law",
            "change_events",
            "invalidation",
            "algorithms",
            "integration",
            "theorems",
        ],
        "description": (
            "Incremental memory encoding package implementing M' = Glue(M|_{X\\S}, "
            "new_sections, overlap_data) from theory2.tex Ch34."
        ),
        "copilot_assisted": True,
        "theory_ref": "theory2.tex:Ch34",
    }


# ---------------------------------------------------------------------------
# Cross-subsystem integration — runtime cache and replay
# ---------------------------------------------------------------------------

try:
    from jugeo.runtime.cache import SemanticCache, CacheKey  # type: ignore[import]
except ImportError:
    SemanticCache = None  # type: ignore[assignment]
    CacheKey = None  # type: ignore[assignment]

try:
    from jugeo.runtime.replay import ReplayEngine, ReplayRecord  # type: ignore[import]
except ImportError:
    ReplayEngine = None  # type: ignore[assignment]
    ReplayRecord = None  # type: ignore[assignment]


def cache_aware_encoding(
    update: object,
    cache: object | None = None,
    namespace: str = "incremental_memory",
) -> dict[str, Any]:
    """Encode an incremental update with cache awareness.

    Consults ``jugeo.runtime.cache.SemanticCache`` to check whether the
    encoding for the given update (identified by its coordinate key and
    support hash) already exists.  When a cache hit occurs the cached
    result is returned directly, avoiding redundant re-encoding.

    Parameters
    ----------
    update:
        An ``IncrementalUpdate`` or equivalent object with at least a
        ``coordinate_key`` attribute.
    cache:
        Optional ``jugeo.runtime.cache.SemanticCache`` instance.  When
        ``None`` a fresh cache is created if the cache subsystem is
        available, otherwise caching is skipped.
    namespace:
        Cache namespace for key scoping (default ``"incremental_memory"``).

    Returns
    -------
    dict[str, Any]
        Result dictionary with ``cache_hit`` (bool), ``encoding``, and
        ``cache_key`` keys.
    """
    result: dict[str, Any] = {"cache_hit": False, "encoding": None, "cache_key": None}

    coord_key = getattr(update, "coordinate_key", None) or str(update)
    support_hash = getattr(update, "support_hash", None) or ""

    if cache is None and SemanticCache is not None:
        try:
            cache = SemanticCache()
        except Exception:
            pass

    # Attempt cache lookup
    if cache is not None and CacheKey is not None:
        try:
            key = CacheKey(namespace=namespace, coordinate=coord_key, support_hash=support_hash)
            result["cache_key"] = str(key)
            cached = cache.get(key)
            if cached is not None:
                result["cache_hit"] = True
                result["encoding"] = cached
                return result
        except Exception:
            pass

    # Cache miss — perform the encoding
    try:
        pipeline = IncrementalUpdatePipeline()
        encoding = pipeline.encode(update)
        result["encoding"] = encoding

        # Store in cache for future lookups
        if cache is not None and CacheKey is not None and encoding is not None:
            try:
                key = CacheKey(namespace=namespace, coordinate=coord_key, support_hash=support_hash)
                cache.put(key, encoding)
            except Exception:
                pass
    except Exception as exc:
        result["error"] = str(exc)

    return result


def replay_encoding(
    update: object,
    engine: object | None = None,
) -> dict[str, Any]:
    """Replay a previous encoding result using the runtime replay subsystem.

    Consults ``jugeo.runtime.replay.ReplayEngine`` to find a previous
    encoding record for the given update.  When a matching ``ReplayRecord``
    is found its result is returned, potentially after re-validation.

    This is useful for incremental workflows where the same update may
    be processed multiple times (e.g. during iterative refinement or
    after a partial rollback).

    Parameters
    ----------
    update:
        An ``IncrementalUpdate`` or equivalent object.
    engine:
        Optional ``jugeo.runtime.replay.ReplayEngine``.  When ``None`` a
        fresh engine is created if the replay subsystem is available.

    Returns
    -------
    dict[str, Any]
        Result dictionary with ``replayed`` (bool), ``encoding``,
        ``replay_record_id``, and ``stale`` keys.
    """
    result: dict[str, Any] = {
        "replayed": False,
        "encoding": None,
        "replay_record_id": None,
        "stale": False,
    }

    if engine is None and ReplayEngine is not None:
        try:
            engine = ReplayEngine()
        except Exception:
            pass

    if engine is None:
        result["error"] = "ReplayEngine unavailable"
        return result

    coord_key = getattr(update, "coordinate_key", None) or str(update)

    try:
        record = engine.find(coordinate_key=coord_key)
        if record is not None:
            result["replayed"] = True
            result["encoding"] = getattr(record, "result", None) or getattr(record, "encoding", None)
            result["replay_record_id"] = getattr(record, "record_id", None)

            # Check staleness — if the update's epoch is newer the record is stale
            record_epoch = getattr(record, "epoch", None)
            update_epoch = getattr(update, "epoch", None)
            if record_epoch is not None and update_epoch is not None:
                result["stale"] = update_epoch > record_epoch
        else:
            result["note"] = f"No replay record found for coordinate '{coord_key}'"
    except Exception as exc:
        result["error"] = str(exc)

    return result

