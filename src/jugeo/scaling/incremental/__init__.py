"""Incremental computation module for JuGeo's scaling infrastructure.

Provides file-hash caching, lazy AST loading, delta computation, and
enhanced invalidation with contract boundaries and dampening strategies.
"""

from __future__ import annotations

from jugeo.scaling.incremental.delta_engine import DeltaEngine
from jugeo.scaling.incremental.file_hasher import FileHasher, ImportScanner
from jugeo.scaling.incremental.invalidation_graph import EnhancedInvalidationGraph
from jugeo.scaling.incremental.lazy_loader import CoordinateExtractor, LazyASTLoader
from jugeo.scaling.incremental.models import (
    CacheEntry,
    CacheStatistics,
    ChangeKind,
    ChangeSet,
    DeltaRecord,
    FileChange,
    FileState,
    InvalidationEvent,
    InvalidationPolicy,
    InvalidationStrategy,
    LazyLoadStatus,
)

__all__ = [
    # Models
    "FileState",
    "ChangeKind",
    "FileChange",
    "ChangeSet",
    "DeltaRecord",
    "InvalidationEvent",
    "InvalidationPolicy",
    "InvalidationStrategy",
    "CacheEntry",
    "CacheStatistics",
    "LazyLoadStatus",
    # File hashing
    "FileHasher",
    "ImportScanner",
    # Lazy loading
    "LazyASTLoader",
    "CoordinateExtractor",
    # Delta computation
    "DeltaEngine",
    # Invalidation
    "EnhancedInvalidationGraph",
]
