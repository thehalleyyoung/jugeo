"""Scaling infrastructure for JuGeo.

This package provides the persistent backend and distributed coordination
primitives that replace in-memory dicts with durable, indexed storage.
"""

from __future__ import annotations

from jugeo.scaling.storage import CachedStore, SQLiteBackend, Store

__all__ = [
    "Store",
    "SQLiteBackend",
    "CachedStore",
]
